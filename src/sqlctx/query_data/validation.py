"""Dialect-aware fail-closed validation and canonicalization for Query Data."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlfluff.core import Linter

from sqlctx.core.enums import ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.query_data.lineage import MaskingLineage, build_lineage

_ALLOWED_FUNCTIONS = {
    "abs",
    "avg",
    "cast",
    "ceiling",
    "coalesce",
    "concat",
    "convert",
    "count",
    "current_date",
    "current_timestamp",
    "current_time",
    "dateadd",
    "datediff",
    "datename",
    "datepart",
    "dense_rank",
    "date_trunc",
    "decode",
    "floor",
    "format",
    "first_value",
    "getdate",
    "greatest",
    "group_concat",
    "hour",
    "iif",
    "isnull",
    "ifnull",
    "instr",
    "json_extract",
    "json_query",
    "json_value",
    "jsonb_extract_path_text",
    "lag",
    "last_value",
    "lead",
    "least",
    "left",
    "len",
    "length",
    "listagg",
    "lower",
    "ltrim",
    "max",
    "min",
    "minute",
    "month",
    "now",
    "ntile",
    "nullif",
    "nvl",
    "percent_rank",
    "position",
    "rank",
    "regexp_replace",
    "replace",
    "right",
    "round",
    "row_number",
    "rtrim",
    "substring",
    "sum",
    "string_agg",
    "strpos",
    "to_char",
    "to_date",
    "trim",
    "try_cast",
    "try_convert",
    "upper",
    "year",
}

_PROHIBITED_TYPES = {
    "alter_table_statement",
    "create_table_statement",
    "delete_statement",
    "drop_table_statement",
    "execute_script_statement",
    "execute_statement",
    "insert_statement",
    "merge_statement",
    "transaction_statement",
    "truncate_statement",
    "update_statement",
}

# Result-shaping clauses collapse an unbounded number of rows into one opaque value.
# That escapes `max_rows` bounding entirely, and it leaves masking to depend on an
# incidental source-column-count match rather than on lineage, so it fails closed here.
_PROHIBITED_RESULT_SHAPING_TYPES = {
    "for_clause",
}

# Table and query hints need only SELECT permission yet can take restrictive locks
# (TABLOCKX, UPDLOCK, HOLDLOCK), read uncommitted rows (NOLOCK), or lift recursion
# bounds (OPTION (MAXRECURSION 0)). None of that is reachable through the permission
# proof in `assert_query_read_only`, so the read path refuses hints outright.
_PROHIBITED_HINT_TYPES = {
    "option_clause",
    "post_table_expression",
    "query_hint_segment",
}

_PROHIBITED_KEYWORDS = {
    "BULK",
    "CALL",
    "COPY",
    "DUMPFILE",
    "EXEC",
    "EXECUTE",
    "INTO",
    "LOCK",
    "OUTFILE",
}

_STRUCTURAL_LITERAL_ANCESTORS = {
    "fetch_clause",
    "frame_clause",
    "limit_clause",
    "offset_clause",
    "orderby_clause",
    "select_clause_modifier",
}


class ValidationAdapter(Protocol):
    dialect: str

    def discover_objects(self, profile: ResolvedConnectionProfile) -> Iterable[ObjectRef]: ...
    def quote_identifier(self, identifier: str) -> str: ...
    def parameter_placeholder(self, position: int) -> str: ...


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    parameters: tuple[Any, ...]
    tables: tuple[ObjectRef, ...]
    lineage: MaskingLineage = MaskingLineage(fallback=None)


class QueryValidator:
    """Validate one relational SELECT and replace tables/literals from parsed source slices."""

    def validate(
        self,
        sql: str,
        *,
        profile: ResolvedConnectionProfile,
        adapter: ValidationAdapter,
    ) -> ValidatedQuery:
        if not sql.strip():
            raise SqlCtxError("QUERY_SQL_REQUIRED", "A read-only SELECT query is required.")
        try:
            parsed = Linter(dialect=adapter.dialect).parse_string(sql)
        except Exception as exc:
            raise SqlCtxError(
                "QUERY_PARSE_FAILED", "The query could not be parsed safely."
            ) from exc
        tree = parsed.tree
        if tree is None or parsed.violations:
            raise SqlCtxError("QUERY_PARSE_FAILED", "The query could not be parsed safely.")
        if any(True for _ in tree.recursive_crawl("unparsable")):
            raise SqlCtxError("QUERY_PARSE_FAILED", "The query could not be parsed safely.")
        if list(tree.recursive_crawl("comment")):
            raise SqlCtxError("QUERY_COMMENTS_NOT_ALLOWED", "Query comments are not allowed.")

        statements = list(tree.recursive_crawl("statement"))
        if len(statements) != 1:
            raise SqlCtxError(
                "QUERY_SINGLE_STATEMENT_REQUIRED", "Exactly one read-only statement is required."
            )
        statement = statements[0]
        statement_types = {segment.type for segment in statement.segments}
        if not statement_types.intersection(
            {"select_statement", "with_compound_statement", "set_expression"}
        ):
            raise SqlCtxError("QUERY_READ_ONLY_REQUIRED", "Only read-only SELECT is allowed.")
        for prohibited in _PROHIBITED_TYPES:
            if list(tree.recursive_crawl(prohibited)):
                raise SqlCtxError(
                    "QUERY_PROHIBITED_CONSTRUCT",
                    "The query contains a prohibited executable construct.",
                )
        for prohibited in _PROHIBITED_RESULT_SHAPING_TYPES:
            if list(tree.recursive_crawl(prohibited)):
                raise SqlCtxError(
                    "QUERY_PROHIBITED_CONSTRUCT",
                    "Row-collapsing result clauses such as FOR XML and FOR JSON are not "
                    "allowed; select the columns directly so row limits and masking apply.",
                )
        for prohibited in _PROHIBITED_HINT_TYPES:
            if list(tree.recursive_crawl(prohibited)):
                raise SqlCtxError(
                    "QUERY_PROHIBITED_CONSTRUCT",
                    "Table and query hints are not allowed on a read-only query.",
                )
        keywords = {
            segment.raw.upper() for segment in tree.raw_segments if segment.is_type("keyword")
        }
        if keywords.intersection(_PROHIBITED_KEYWORDS):
            raise SqlCtxError(
                "QUERY_PROHIBITED_CONSTRUCT",
                "The query contains a prohibited executable construct.",
            )
        upper_sql = sql.upper()
        if re.search(r"\bFOR\s+UPDATE\b|\bOPEN(?:ROWSET|QUERY|DATASOURCE)\b", upper_sql):
            raise SqlCtxError(
                "QUERY_PROHIBITED_CONSTRUCT",
                "The query contains a prohibited external or locking construct.",
            )

        self._validate_functions(tree)
        replacements: list[tuple[int, int, str]] = []
        tables = self._resolve_tables(
            tree, profile=profile, adapter=adapter, replacements=replacements
        )
        parameters = self._parameterize_literals(tree, adapter=adapter, replacements=replacements)
        canonical = sql
        for start, stop, replacement in sorted(replacements, reverse=True):
            canonical = canonical[:start] + replacement + canonical[stop:]
        return ValidatedQuery(
            sql=canonical.strip(), parameters=parameters, tables=tables, lineage=build_lineage(tree)
        )

    @staticmethod
    def _validate_functions(tree: Any) -> None:
        # Parenthesis-free calls parse as `bare_function`, not `function`, so they used to
        # skip the allowlist entirely and expose principal identity via CURRENT_USER,
        # SESSION_USER and SYSTEM_USER. The allowlist only closes if both are checked.
        for bare in tree.recursive_crawl("bare_function"):
            if QueryValidator._dequote(bare.raw.strip()).casefold() not in _ALLOWED_FUNCTIONS:
                raise SqlCtxError(
                    "QUERY_FUNCTION_NOT_ALLOWED",
                    "The query function is not on the read-only allowlist.",
                )
        for function in tree.recursive_crawl("function"):
            names = list(function.recursive_crawl("function_name"))
            if not names:
                raise SqlCtxError(
                    "QUERY_FUNCTION_NOT_ALLOWED", "An unknown query function is not allowed."
                )
            raw = names[0].raw.strip()
            if "." in raw or QueryValidator._dequote(raw).casefold() not in _ALLOWED_FUNCTIONS:
                raise SqlCtxError(
                    "QUERY_FUNCTION_NOT_ALLOWED",
                    "The query function is not on the read-only allowlist.",
                )

    def _resolve_tables(
        self,
        tree: Any,
        *,
        profile: ResolvedConnectionProfile,
        adapter: ValidationAdapter,
        replacements: list[tuple[int, int, str]],
    ) -> tuple[ObjectRef, ...]:
        cte_names = {
            self._dequote(
                next(segment.raw for segment in cte.raw_segments if segment.is_type("identifier"))
            ).casefold()
            for cte in tree.recursive_crawl("common_table_expression")
            if any(segment.is_type("identifier") for segment in cte.raw_segments)
        }
        discovered = [
            item
            for item in adapter.discover_objects(profile)
            if ObjectType(item.object_type) == ObjectType.TABLE
        ]
        resolved: dict[str, ObjectRef] = {}
        for table_segment in tree.recursive_crawl("table_reference"):
            identifiers = [
                self._dequote(segment.raw)
                for segment in table_segment.raw_segments
                if segment.is_type("identifier")
            ]
            if not identifiers:
                raise SqlCtxError("QUERY_TABLE_INVALID", "A table reference is invalid.")
            if len(identifiers) > 2:
                raise SqlCtxError(
                    "QUERY_CROSS_DATABASE_NOT_ALLOWED",
                    "Cross-database table references are not allowed.",
                )
            if len(identifiers) == 1 and identifiers[0].casefold() in cte_names:
                continue
            candidates = [
                item
                for item in discovered
                if item.object_name.casefold() == identifiers[-1].casefold()
                and (
                    len(identifiers) == 1
                    or item.schema_name.casefold() == identifiers[0].casefold()
                )
            ]
            if not candidates:
                raise SqlCtxError(
                    "QUERY_TABLE_NOT_ALLOWED",
                    "A referenced table is not available under the profile policy.",
                    status_code=403,
                )
            if len(candidates) > 1:
                safe_candidates = sorted(
                    f"{item.schema_name}.{item.object_name}" for item in candidates
                )
                raise SqlCtxError(
                    "QUERY_TABLE_AMBIGUOUS",
                    "An unqualified table name is ambiguous.",
                    details={"candidates": safe_candidates},
                )
            match = candidates[0]
            marker = table_segment.pos_marker
            if marker is None:
                raise SqlCtxError(
                    "QUERY_CANONICALIZATION_FAILED", "A table reference has no source position."
                )
            replacements.append(
                (
                    marker.source_slice.start,
                    marker.source_slice.stop,
                    f"{adapter.quote_identifier(match.schema_name)}."
                    f"{adapter.quote_identifier(match.object_name)}",
                )
            )
            resolved[match.object_id] = match
        return tuple(resolved.values())

    def _parameterize_literals(
        self,
        tree: Any,
        *,
        adapter: ValidationAdapter,
        replacements: list[tuple[int, int, str]],
    ) -> tuple[Any, ...]:
        parameters: list[Any] = []
        literals = sorted(
            tree.recursive_crawl("literal"),
            key=lambda item: item.pos_marker.source_slice.start if item.pos_marker else -1,
        )
        for literal in literals:
            marker = literal.pos_marker
            if marker is None:
                raise SqlCtxError(
                    "QUERY_CANONICALIZATION_FAILED", "A query literal has no source position."
                )
            ancestors = {step.segment.type for step in tree.path_to(literal)}
            if ancestors.intersection(_STRUCTURAL_LITERAL_ANCESTORS):
                if not re.fullmatch(r"[0-9]+", literal.raw.strip()):
                    raise SqlCtxError(
                        "QUERY_STRUCTURAL_LITERAL_INVALID",
                        "A structural query literal is invalid.",
                    )
                continue
            value = self._literal_value(literal.raw)
            parameters.append(value)
            replacements.append(
                (
                    marker.source_slice.start,
                    marker.source_slice.stop,
                    adapter.parameter_placeholder(len(parameters)),
                )
            )
        return tuple(parameters)

    @staticmethod
    def _literal_value(raw: str) -> Any:
        value = raw.strip()
        if len(value) >= 3 and value[0] in {"N", "n"} and value[1] == "'":
            value = value[1:]
        if len(value) >= 2 and value[0] == value[-1] == "'":
            return value[1:-1].replace("''", "'")
        if re.fullmatch(r"[+-]?[0-9]+", value):
            return int(value)
        if re.fullmatch(r"[+-]?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[Ee][+-]?[0-9]+)?", value):
            try:
                return Decimal(value)
            except InvalidOperation as exc:
                raise SqlCtxError("QUERY_LITERAL_INVALID", "A query literal is invalid.") from exc
        raise SqlCtxError("QUERY_LITERAL_INVALID", "A query literal is not supported safely.")

    @staticmethod
    def _dequote(identifier: str) -> str:
        value = identifier.strip()
        if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
            return value[1:-1].replace("]]", "]")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "`"}:
            return value[1:-1].replace(value[0] * 2, value[0])
        return value
