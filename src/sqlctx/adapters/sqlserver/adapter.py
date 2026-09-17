"""Microsoft SQL Server read-only catalog adapter."""

import hashlib
import json
import re
from fnmatch import fnmatchcase
from typing import Any

from sqlctx.adapters.base import AdapterQueries, BaseDatabaseAdapter
from sqlctx.context_index.contracts import ContextIndexEntry, ContextIndexListRequest
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile

_PROCEDURE_DECLARATION = re.compile(
    r"(?:CREATE\s+OR\s+ALTER|CREATE|ALTER)\s+(?:PROCEDURE|PROC)\b",
    flags=re.IGNORECASE,
)
_FUNCTION_DECLARATION = re.compile(
    r"(?:CREATE\s+OR\s+ALTER|CREATE|ALTER)\s+FUNCTION\b",
    flags=re.IGNORECASE,
)


def _declaration_offset(definition: str) -> int:
    """Return the offset of the first token that is not a BOM, whitespace, or T-SQL comment.

    A stored routine may legally begin with banner comments, so the declaration boundary is
    located by consuming exactly those leading tokens. Scanning instead of widening the regex
    keeps declaration words that appear inside a comment from being mistaken for the header.
    """
    length = len(definition)
    index = 1 if definition.startswith("\ufeff") else 0
    while index < length:
        char = definition[index]
        if char.isspace():
            index += 1
            continue
        following = definition[index + 1 : index + 2]
        if char == "-" and following == "-":
            line_end = definition.find("\n", index + 2)
            index = length if line_end == -1 else line_end + 1
            continue
        if char == "/" and following == "*":
            depth = 1
            index += 2
            while index < length and depth:
                pair = definition[index : index + 2]
                if pair == "/*":
                    depth += 1
                    index += 2
                elif pair == "*/":
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue
        break
    return index


def _normalize_declaration(
    definition: str, pattern: re.Pattern[str], canonical: str, error_code: str, noun: str
) -> str:
    """Rewrite one leading declaration to `canonical` and keep every other character."""
    start = _declaration_offset(definition)
    match = pattern.match(definition, start)
    if match is None:
        raise SqlCtxError(
            error_code,
            f"SQL Server {noun} definition does not begin with a supported declaration.",
        )
    return definition[:start] + canonical + definition[match.end() :]


_METADATA_CONTEXT_COLUMNS: dict[str, tuple[str, int, int, int, bool, bool]] = {
    "ID": ("numeric", 17, 38, 0, False, True),
    "DB_METADATA_CONTEXT_ID": ("uniqueidentifier", 16, 0, 0, False, False),
    "SCHEMA_NAME": ("nvarchar", 256, 0, 0, False, False),
    "OBJECT_NAME": ("nvarchar", 512, 0, 0, False, False),
    "OBJECT_TYPE": ("varchar", 20, 0, 0, False, False),
    "CONTEXT_CODE": ("varchar", 64, 0, 0, True, False),
    "DESCRIPTION": ("nvarchar", 4000, 0, 0, True, False),
    "TAGS_JSON": ("nvarchar", -1, 0, 0, False, False),
    "CLASSIFICATION_STATUS": ("varchar", 20, 0, 0, False, False),
    "CLASSIFICATION_SOURCE": ("varchar", 20, 0, 0, False, False),
    "SOURCE_FINGERPRINT": ("varchar", 71, 0, 0, True, False),
    "CONTENT_HASH": ("varchar", 71, 0, 0, True, False),
    "MANAGED_RELATIVE_PATH": ("nvarchar", 2000, 0, 0, True, False),
    "HEADER_VERSION": ("int", 4, 10, 0, False, False),
    "OUTPUT_FORMAT_VERSION": ("int", 4, 10, 0, False, False),
    "EVIDENCE_JSON": ("nvarchar", -1, 0, 0, False, False),
    "LAST_CLASSIFIED_AT": ("datetime2", 8, 27, 7, True, False),
    "LAST_GENERATED_AT": ("datetime2", 8, 27, 7, True, False),
    "DATE_CREATED": ("datetime2", 8, 27, 7, False, False),
    "DATE_MODIFIED": ("datetime2", 8, 27, 7, True, False),
    "USER_CREATED": ("numeric", 17, 38, 0, False, False),
    "USER_MODIFIED": ("numeric", 17, 38, 0, True, False),
    "DEL_FLAG": ("bit", 1, 1, 0, False, False),
}

_METADATA_CONTEXT_COLUMN_FLAGS: dict[str, tuple[bool, bool, bool, int | None, int | None]] = {
    name: (False, False, False, 1, 1) if name == "ID" else (False, False, False, None, None)
    for name in _METADATA_CONTEXT_COLUMNS
}

_METADATA_CONTEXT_DEFAULTS: dict[str, tuple[str, str]] = {
    "DF_DB_METADATA_CONTEXT_PUBLIC_ID": ("DB_METADATA_CONTEXT_ID", "NEWSEQUENTIALID"),
    "DF_DB_METADATA_CONTEXT_TAGS": ("TAGS_JSON", "N'[]'"),
    "DF_DB_METADATA_CONTEXT_STATUS": ("CLASSIFICATION_STATUS", "'UNRESOLVED'"),
    "DF_DB_METADATA_CONTEXT_SOURCE": ("CLASSIFICATION_SOURCE", "'UNKNOWN'"),
    "DF_DB_METADATA_CONTEXT_HEADER_VERSION": ("HEADER_VERSION", "1"),
    "DF_DB_METADATA_CONTEXT_OUTPUT_VERSION": ("OUTPUT_FORMAT_VERSION", "2"),
    "DF_DB_METADATA_CONTEXT_EVIDENCE": ("EVIDENCE_JSON", "N'[]'"),
    "DF_DB_METADATA_CONTEXT_DATE_CREATED": ("DATE_CREATED", "SYSUTCDATETIME"),
    "DF_DB_METADATA_CONTEXT_DEL_FLAG": ("DEL_FLAG", "0"),
}

_METADATA_CONTEXT_CHECKS: dict[str, str] = {
    "CK_DB_METADATA_CONTEXT_OBJECT_TYPE": ("OBJECT_TYPE IN ('TABLE', 'PROCEDURE', 'FUNCTION')"),
    "CK_DB_METADATA_CONTEXT_STATUS": ("CLASSIFICATION_STATUS IN ('CONFIRMED', 'UNRESOLVED')"),
    "CK_DB_METADATA_CONTEXT_SOURCE": ("CLASSIFICATION_SOURCE IN ('OWNER', 'RULE', 'UNKNOWN')"),
    "CK_DB_METADATA_CONTEXT_CONTEXT_CODE": (
        "CONTEXT_CODE IS NULL OR "
        "(LEN(CONTEXT_CODE) BETWEEN 1 AND 64 "
        "AND CONTEXT_CODE COLLATE Latin1_General_100_BIN2 "
        "NOT LIKE '%[^a-z0-9_-]%')"
    ),
    "CK_DB_METADATA_CONTEXT_CLASSIFICATION": (
        "(CLASSIFICATION_STATUS = 'CONFIRMED' AND CONTEXT_CODE IS NOT NULL "
        "AND CLASSIFICATION_SOURCE IN ('OWNER', 'RULE')) OR "
        "(CLASSIFICATION_STATUS = 'UNRESOLVED' AND CONTEXT_CODE IS NULL "
        "AND CLASSIFICATION_SOURCE = 'UNKNOWN' AND TAGS_JSON = N'[]')"
    ),
    "CK_DB_METADATA_CONTEXT_TAGS_JSON": (
        "ISJSON(TAGS_JSON) = 1 AND LEFT(LTRIM(TAGS_JSON), 1) = N'['"
    ),
    "CK_DB_METADATA_CONTEXT_EVIDENCE_JSON": (
        "ISJSON(EVIDENCE_JSON) = 1 AND LEFT(LTRIM(EVIDENCE_JSON), 1) = N'['"
    ),
    "CK_DB_METADATA_CONTEXT_HEADER_VERSION": "HEADER_VERSION = 1",
    "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION": "OUTPUT_FORMAT_VERSION = 2",
}

_METADATA_CONTEXT_INDEXES: dict[
    str,
    tuple[
        bool,
        bool,
        bool,
        str,
        str | None,
        tuple[tuple[str, int, bool, bool], ...],
    ],
] = {
    "PK_DB_METADATA_CONTEXT": (
        True,
        True,
        False,
        "CLUSTERED",
        None,
        (("ID", 1, False, False),),
    ),
    "UX_DB_METADATA_CONTEXT_OBJECT_ACTIVE": (
        True,
        False,
        False,
        "NONCLUSTERED",
        "DEL_FLAG=0",
        (
            ("SCHEMA_NAME", 1, False, False),
            ("OBJECT_TYPE", 2, False, False),
            ("OBJECT_NAME", 3, False, False),
        ),
    ),
    "IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE": (
        False,
        False,
        False,
        "NONCLUSTERED",
        "DEL_FLAG=0",
        (
            ("CONTEXT_CODE", 1, False, False),
            ("CLASSIFICATION_STATUS", 2, False, False),
            ("OBJECT_TYPE", 3, False, False),
            ("SCHEMA_NAME", 0, True, False),
            ("OBJECT_NAME", 0, True, False),
            ("DESCRIPTION", 0, True, False),
            ("TAGS_JSON", 0, True, False),
            ("CONTENT_HASH", 0, True, False),
            ("MANAGED_RELATIVE_PATH", 0, True, False),
        ),
    ),
}


def _metadata_sql_signature(value: object) -> str:
    return re.sub(r"[\[\]()\s]", "", str(value or "")).upper()


class SqlServerAdapter(BaseDatabaseAdapter):
    engine = DatabaseEngine.SQLSERVER
    dialect = "tsql"
    quote_left = "["
    quote_right = "]"
    queries = AdapterQueries(
        server_info="SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version",
        schemas="SELECT name AS schema_name FROM sys.schemas ORDER BY name",
        objects="""
            SELECT o.name AS object_name,
                   CASE WHEN o.type = 'U' THEN 'table'
                        WHEN o.type = 'P' THEN 'procedure'
                        ELSE 'function' END AS object_type
              FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.type IN ('U', 'P', 'FN', 'IF', 'TF')
               AND o.is_ms_shipped = 0
             ORDER BY object_type, object_name
        """,
        columns="""
            SELECT c.name AS column_name, t.name AS data_type,
                   c.is_nullable, c.column_id AS ordinal_position
              FROM sys.columns c
              JOIN sys.types t ON t.user_type_id = c.user_type_id
              JOIN sys.objects o ON o.object_id = c.object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.name = ? ORDER BY c.column_id
        """,
        constraints="""
            WITH target AS (SELECT ? AS schema_name, ? AS object_name)
            SELECT i.name AS constraint_name,
                   CASE WHEN i.is_primary_key = 1 THEN 'primary key' ELSE 'unique' END AS constraint_type,
                   c.name AS column_name, CAST(NULL AS nvarchar(max)) AS expression
              FROM sys.indexes i
              JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
              JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
              JOIN sys.objects o ON o.object_id = i.object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
              JOIN target t ON t.schema_name = s.name AND t.object_name = o.name
             WHERE i.is_primary_key = 1 OR i.is_unique = 1
            UNION ALL
            SELECT cc.name, 'check', CAST(NULL AS sysname), cc.definition
              FROM sys.check_constraints cc
              JOIN sys.objects o ON o.object_id = cc.parent_object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
              JOIN target t ON t.schema_name = s.name AND t.object_name = o.name
             ORDER BY constraint_name, column_name
        """,
        foreign_keys="""
            SELECT fk.name AS constraint_name, pc.name AS source_column,
                   rs.name AS target_schema, ro.name AS target_table, rc.name AS target_column
              FROM sys.foreign_keys fk
              JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
              JOIN sys.objects po ON po.object_id = fkc.parent_object_id
              JOIN sys.schemas ps ON ps.schema_id = po.schema_id
              JOIN sys.columns pc ON pc.object_id = po.object_id AND pc.column_id = fkc.parent_column_id
              JOIN sys.objects ro ON ro.object_id = fkc.referenced_object_id
              JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
              JOIN sys.columns rc ON rc.object_id = ro.object_id AND rc.column_id = fkc.referenced_column_id
             WHERE ps.name = ? AND po.name = ?
        """,
        table_definition=None,
        procedure_definition="""
            SELECT m.definition
              FROM sys.sql_modules m JOIN sys.objects o ON o.object_id = m.object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.name = ? AND o.type = 'P'
        """,
        function_definition="""
            SELECT m.definition
              FROM sys.sql_modules m JOIN sys.objects o ON o.object_id = m.object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.name = ? AND o.type IN ('FN', 'IF', 'TF')
        """,
        routine_dependencies="""
            SELECT CONCAT('table:', rs.name, '.', ro.name) AS target_object_id,
                   'routine_read' AS edge_type
              FROM sys.sql_expression_dependencies d
              JOIN sys.objects so ON so.object_id = d.referencing_id
              JOIN sys.schemas ss ON ss.schema_id = so.schema_id
              JOIN sys.objects ro ON ro.object_id = d.referenced_id
              JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
             WHERE ss.name = ? AND so.name = ?
        """,
        table_comment="""
            SELECT CAST(ep.value AS nvarchar(max)) AS description
              FROM sys.tables o
              JOIN sys.schemas s ON s.schema_id = o.schema_id
              LEFT JOIN sys.extended_properties ep
                ON ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
             WHERE s.name = ? AND o.name = ?
        """,
        indexes="""
            SELECT i.name AS index_name, i.is_unique, i.is_primary_key AS is_primary,
                   c.name AS column_name, ic.key_ordinal AS column_order,
                   ic.is_included_column AS is_included
              FROM sys.indexes i
              JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
              JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
              JOIN sys.objects o ON o.object_id = i.object_id
              JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.name = ? AND i.name IS NOT NULL AND i.is_hypothetical = 0
             ORDER BY i.name, ic.is_included_column, ic.key_ordinal, ic.index_column_id
        """,
        read_only_setup="SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    )

    @staticmethod
    def normalize_procedure_definition(definition: str) -> str:
        """Return one rerunnable T-SQL procedure declaration without changing its body."""
        return _normalize_declaration(
            definition,
            _PROCEDURE_DECLARATION,
            "CREATE OR ALTER PROCEDURE",
            "PROCEDURE_DEFINITION_HEADER_UNSUPPORTED",
            "procedure",
        )

    @staticmethod
    def normalize_function_definition(definition: str) -> str:
        """Return one rerunnable T-SQL function declaration without changing its body."""
        return _normalize_declaration(
            definition,
            _FUNCTION_DECLARATION,
            "CREATE OR ALTER FUNCTION",
            "FUNCTION_DEFINITION_HEADER_UNSUPPORTED",
            "function",
        )

    def get_procedure_definition(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str:
        definition = super().get_procedure_definition(profile, ref)
        return self.normalize_procedure_definition(definition)

    def get_function_definition(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str:
        definition = super().get_function_definition(profile, ref)
        return self.normalize_function_definition(definition)

    def verify_metadata_context_schema(self, profile: ResolvedConnectionProfile) -> None:
        """Refuse writes when the one-table metadata contract is missing or incompatible."""
        column_rows = self._execute(
            profile,
            """
            SELECT c.name AS column_name, LOWER(y.name) AS data_type, c.max_length,
                   c.precision, c.scale, c.is_nullable,
                   COLUMNPROPERTY(c.object_id, c.name, 'IsIdentity') AS is_identity,
                   c.is_computed, c.is_rowguidcol, c.is_sparse,
                   ic.seed_value, ic.increment_value
              FROM sys.columns c
              JOIN sys.tables t ON t.object_id = c.object_id
              JOIN sys.types y ON y.user_type_id = c.user_type_id
              LEFT JOIN sys.identity_columns ic
                ON ic.object_id = c.object_id AND ic.column_id = c.column_id
              JOIN sys.schemas s ON s.schema_id = t.schema_id
             WHERE s.name = ? AND t.name = ?
             ORDER BY c.column_id
            """,
            self._parameters("agrimap_app", "DB_METADATA_CONTEXT"),
        )
        default_rows = self._execute(
            profile,
            """
            SELECT dc.name AS constraint_name, c.name AS column_name, dc.definition
              FROM sys.default_constraints dc
              JOIN sys.columns c
                ON c.object_id = dc.parent_object_id
               AND c.column_id = dc.parent_column_id
              JOIN sys.tables t ON t.object_id = dc.parent_object_id
              JOIN sys.schemas s ON s.schema_id = t.schema_id
             WHERE s.name = ? AND t.name = ?
            """,
            self._parameters("agrimap_app", "DB_METADATA_CONTEXT"),
        )
        check_rows = self._execute(
            profile,
            """
            SELECT cc.name AS constraint_name, cc.definition,
                   cc.is_disabled, cc.is_not_trusted, cc.is_not_for_replication
              FROM sys.check_constraints cc
              JOIN sys.tables t ON t.object_id = cc.parent_object_id
              JOIN sys.schemas s ON s.schema_id = t.schema_id
             WHERE s.name = ? AND t.name = ?
            """,
            self._parameters("agrimap_app", "DB_METADATA_CONTEXT"),
        )
        index_rows = self._execute(
            profile,
            """
            SELECT i.name AS index_name, i.is_unique, i.is_primary_key,
                   i.is_unique_constraint, i.type_desc, i.is_disabled,
                   i.filter_definition,
                   c.name AS column_name, ic.key_ordinal,
                   ic.is_included_column, ic.is_descending_key
              FROM sys.indexes i
              JOIN sys.tables t ON t.object_id = i.object_id
              JOIN sys.schemas s ON s.schema_id = t.schema_id
              JOIN sys.index_columns ic
                ON ic.object_id = i.object_id AND ic.index_id = i.index_id
              JOIN sys.columns c
                ON c.object_id = ic.object_id AND c.column_id = ic.column_id
             WHERE s.name = ? AND t.name = ? AND i.is_hypothetical = 0
            """,
            self._parameters("agrimap_app", "DB_METADATA_CONTEXT"),
        )
        auxiliary_rows = self._execute(
            profile,
            """
            SELECT 'FOREIGN_KEY' AS object_kind, fk.name AS object_name
              FROM sys.foreign_keys fk
             WHERE fk.parent_object_id = OBJECT_ID(?)
                OR fk.referenced_object_id = OBJECT_ID(?)
            UNION ALL
            SELECT 'TRIGGER' AS object_kind, tr.name AS object_name
              FROM sys.triggers tr
             WHERE tr.parent_id = OBJECT_ID(?)
            """,
            self._parameters(
                "[agrimap_app].[DB_METADATA_CONTEXT]",
                "[agrimap_app].[DB_METADATA_CONTEXT]",
                "[agrimap_app].[DB_METADATA_CONTEXT]",
            ),
        )

        actual_columns = {
            str(row["column_name"]).upper(): (
                str(row["data_type"]).lower(),
                int(row["max_length"]),
                int(row["precision"]),
                int(row["scale"]),
                bool(row["is_nullable"]),
                bool(row["is_identity"]),
            )
            for row in column_rows
        }
        missing_columns = sorted(set(_METADATA_CONTEXT_COLUMNS) - set(actual_columns))
        extra_columns = sorted(set(actual_columns) - set(_METADATA_CONTEXT_COLUMNS))
        column_mismatches = sorted(
            name
            for name in set(actual_columns) & set(_METADATA_CONTEXT_COLUMNS)
            if actual_columns[name] != _METADATA_CONTEXT_COLUMNS[name]
        )
        actual_column_flags = {
            str(row["column_name"]).upper(): (
                bool(row["is_computed"]),
                bool(row["is_rowguidcol"]),
                bool(row["is_sparse"]),
                int(row["seed_value"]) if row.get("seed_value") is not None else None,
                int(row["increment_value"]) if row.get("increment_value") is not None else None,
            )
            for row in column_rows
        }
        column_mismatches = sorted(
            set(column_mismatches)
            | {
                name
                for name in set(actual_column_flags) & set(_METADATA_CONTEXT_COLUMN_FLAGS)
                if actual_column_flags[name] != _METADATA_CONTEXT_COLUMN_FLAGS[name]
            }
        )

        actual_defaults = {
            str(row["constraint_name"]).upper(): (
                str(row["column_name"]).upper(),
                _metadata_sql_signature(row["definition"]),
            )
            for row in default_rows
        }
        default_mismatches = sorted(
            {
                name
                for name, (column_name, definition) in _METADATA_CONTEXT_DEFAULTS.items()
                if actual_defaults.get(name) != (column_name, _metadata_sql_signature(definition))
            }
            | (set(actual_defaults) - set(_METADATA_CONTEXT_DEFAULTS))
        )

        actual_checks = {
            str(row["constraint_name"]).upper(): (
                _metadata_sql_signature(row["definition"]),
                bool(row["is_disabled"]),
                bool(row["is_not_trusted"]),
                bool(row["is_not_for_replication"]),
            )
            for row in check_rows
        }
        check_mismatches = sorted(
            {
                name
                for name, definition in _METADATA_CONTEXT_CHECKS.items()
                if actual_checks.get(name)
                != (_metadata_sql_signature(definition), False, False, False)
            }
            | (set(actual_checks) - set(_METADATA_CONTEXT_CHECKS))
        )

        grouped_indexes: dict[str, list[dict[str, Any]]] = {}
        for row in index_rows:
            grouped_indexes.setdefault(str(row["index_name"]).upper(), []).append(row)
        index_mismatches: list[str] = sorted(set(grouped_indexes) - set(_METADATA_CONTEXT_INDEXES))
        for name, expected in _METADATA_CONTEXT_INDEXES.items():
            rows = grouped_indexes.get(name, [])
            if not rows:
                index_mismatches.append(name)
                continue
            (
                expected_unique,
                expected_primary,
                expected_unique_constraint,
                expected_type,
                expected_filter,
                expected_columns,
            ) = expected
            actual_filter = _metadata_sql_signature(rows[0].get("filter_definition")) or None
            actual_index_columns = tuple(
                sorted(
                    (
                        str(row["column_name"]).upper(),
                        int(row["key_ordinal"]),
                        bool(row["is_included_column"]),
                        bool(row["is_descending_key"]),
                    )
                    for row in rows
                )
            )
            if (
                bool(rows[0]["is_unique"]) != expected_unique
                or bool(rows[0]["is_primary_key"]) != expected_primary
                or bool(rows[0]["is_unique_constraint"]) != expected_unique_constraint
                or str(rows[0]["type_desc"]).upper() != expected_type
                or bool(rows[0]["is_disabled"])
                or actual_filter
                != (_metadata_sql_signature(expected_filter) if expected_filter else None)
                or actual_index_columns != tuple(sorted(expected_columns))
            ):
                index_mismatches.append(name)

        unexpected_objects = sorted(
            f"{str(row['object_kind']).upper()}:{str(row['object_name']).upper()}"
            for row in auxiliary_rows
        )

        if any(
            (
                missing_columns,
                extra_columns,
                column_mismatches,
                default_mismatches,
                check_mismatches,
                index_mismatches,
                unexpected_objects,
            )
        ):
            raise SqlCtxError(
                "METADATA_CONTEXT_SCHEMA_DRIFT",
                "DB_METADATA_CONTEXT is missing or incompatible; apply the reviewed DDL or migration.",
                status_code=409,
                details={
                    "missing_columns": missing_columns,
                    "extra_columns": extra_columns,
                    "column_mismatches": column_mismatches,
                    "default_mismatches": default_mismatches,
                    "check_mismatches": check_mismatches,
                    "index_mismatches": sorted(index_mismatches),
                    "unexpected_objects": unexpected_objects,
                },
            )

    def upsert_metadata_context(
        self,
        profile: ResolvedConnectionProfile,
        entry: ContextIndexEntry,
        *,
        actor_id: int,
    ) -> tuple[str, bool]:
        """Upsert one typed index row while preserving established owner classifications."""
        if not profile.metadata_context_write:
            raise SqlCtxError(
                "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED",
                "The selected profile does not enable metadata_context_write.",
                status_code=403,
            )
        rows = self._execute_write_returning(
            profile,
            """
            MERGE [agrimap_app].[DB_METADATA_CONTEXT] WITH (HOLDLOCK) AS target
            USING (SELECT ? AS [SCHEMA_NAME], ? AS [OBJECT_NAME], ? AS [OBJECT_TYPE]) AS source
               ON target.[SCHEMA_NAME] = source.[SCHEMA_NAME]
              AND target.[OBJECT_NAME] = source.[OBJECT_NAME]
              AND target.[OBJECT_TYPE] = source.[OBJECT_TYPE]
              AND target.[DEL_FLAG] = 0
            WHEN MATCHED THEN UPDATE SET
                [CONTEXT_CODE] = CASE
                    WHEN target.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                    THEN target.[CONTEXT_CODE] ELSE ? END,
                [DESCRIPTION] = CASE
                    WHEN target.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                    THEN target.[DESCRIPTION] ELSE ? END,
                [TAGS_JSON] = CASE
                    WHEN target.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                    THEN target.[TAGS_JSON] ELSE ? END,
                [CLASSIFICATION_STATUS] = CASE
                    WHEN target.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                    THEN target.[CLASSIFICATION_STATUS] ELSE ? END,
                [CLASSIFICATION_SOURCE] = CASE
                    WHEN target.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                    THEN target.[CLASSIFICATION_SOURCE] ELSE ? END,
                [SOURCE_FINGERPRINT] = ?, [CONTENT_HASH] = ?, [MANAGED_RELATIVE_PATH] = ?,
                [HEADER_VERSION] = ?, [OUTPUT_FORMAT_VERSION] = ?, [EVIDENCE_JSON] = ?,
                [LAST_CLASSIFIED_AT] = SYSUTCDATETIME(), [DATE_MODIFIED] = SYSUTCDATETIME(),
                [USER_MODIFIED] = ?
            WHEN NOT MATCHED THEN INSERT
                ([DB_METADATA_CONTEXT_ID], [SCHEMA_NAME], [OBJECT_NAME], [OBJECT_TYPE],
                 [CONTEXT_CODE], [DESCRIPTION], [TAGS_JSON], [CLASSIFICATION_STATUS],
                 [CLASSIFICATION_SOURCE], [SOURCE_FINGERPRINT], [CONTENT_HASH],
                 [MANAGED_RELATIVE_PATH], [HEADER_VERSION], [OUTPUT_FORMAT_VERSION],
                 [EVIDENCE_JSON], [LAST_CLASSIFIED_AT], [DATE_CREATED], [USER_CREATED], [DEL_FLAG])
            VALUES
                (NEWID(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                 SYSUTCDATETIME(), SYSUTCDATETIME(), ?, 0)
            OUTPUT $action AS [action],
                   CASE WHEN deleted.[CLASSIFICATION_SOURCE] = 'OWNER' AND ? <> 'OWNER'
                        THEN 1 ELSE 0 END AS [owner_values_preserved],
                   CASE
                       WHEN $action = 'INSERT' THEN 1
                       WHEN
                           (inserted.[CONTEXT_CODE] = deleted.[CONTEXT_CODE]
                            OR (inserted.[CONTEXT_CODE] IS NULL
                                AND deleted.[CONTEXT_CODE] IS NULL))
                           AND (inserted.[DESCRIPTION] = deleted.[DESCRIPTION]
                                OR (inserted.[DESCRIPTION] IS NULL
                                    AND deleted.[DESCRIPTION] IS NULL))
                           AND inserted.[TAGS_JSON] = deleted.[TAGS_JSON]
                           AND inserted.[CLASSIFICATION_STATUS]
                               = deleted.[CLASSIFICATION_STATUS]
                           AND inserted.[CLASSIFICATION_SOURCE]
                               = deleted.[CLASSIFICATION_SOURCE]
                           AND (inserted.[SOURCE_FINGERPRINT]
                                = deleted.[SOURCE_FINGERPRINT]
                                OR (inserted.[SOURCE_FINGERPRINT] IS NULL
                                    AND deleted.[SOURCE_FINGERPRINT] IS NULL))
                           AND (inserted.[CONTENT_HASH] = deleted.[CONTENT_HASH]
                                OR (inserted.[CONTENT_HASH] IS NULL
                                    AND deleted.[CONTENT_HASH] IS NULL))
                           AND (inserted.[MANAGED_RELATIVE_PATH]
                                = deleted.[MANAGED_RELATIVE_PATH]
                                OR (inserted.[MANAGED_RELATIVE_PATH] IS NULL
                                    AND deleted.[MANAGED_RELATIVE_PATH] IS NULL))
                           AND inserted.[HEADER_VERSION] = deleted.[HEADER_VERSION]
                           AND inserted.[OUTPUT_FORMAT_VERSION]
                               = deleted.[OUTPUT_FORMAT_VERSION]
                           AND inserted.[EVIDENCE_JSON] = deleted.[EVIDENCE_JSON]
                       THEN 0 ELSE 1
                   END AS [semantic_changed];
            """,
            self._parameters(
                entry.schema_name,
                entry.object_name,
                str(entry.object_type).upper(),
                entry.classification_source.upper(),
                entry.context,
                entry.classification_source.upper(),
                entry.description,
                entry.classification_source.upper(),
                json.dumps(entry.tags, ensure_ascii=False, separators=(",", ":")),
                entry.classification_source.upper(),
                entry.classification_status.upper(),
                entry.classification_source.upper(),
                entry.classification_source.upper(),
                entry.source_fingerprint,
                entry.content_hash,
                entry.managed_relative_path,
                entry.header_version,
                int(entry.output_format_version),
                json.dumps(entry.evidence, ensure_ascii=False, separators=(",", ":")),
                actor_id,
                entry.schema_name,
                entry.object_name,
                str(entry.object_type).upper(),
                entry.context,
                entry.description,
                json.dumps(entry.tags, ensure_ascii=False, separators=(",", ":")),
                entry.classification_status.upper(),
                entry.classification_source.upper(),
                entry.source_fingerprint,
                entry.content_hash,
                entry.managed_relative_path,
                entry.header_version,
                int(entry.output_format_version),
                json.dumps(entry.evidence, ensure_ascii=False, separators=(",", ":")),
                actor_id,
                entry.classification_source.upper(),
            ),
        )
        if len(rows) != 1 or str(rows[0].get("action", "")).upper() not in {"INSERT", "UPDATE"}:
            raise SqlCtxError(
                "METADATA_CONTEXT_WRITE_FAILED", "Metadata upsert returned no result."
            )
        action = str(rows[0]["action"]).lower()
        if action == "update" and not bool(rows[0].get("semantic_changed", 0)):
            action = "unchanged"
        return action, bool(rows[0].get("owner_values_preserved", 0))

    def deactivate_missing_metadata_context(
        self,
        profile: ResolvedConnectionProfile,
        *,
        schemas: tuple[str, ...],
        object_types: tuple[ObjectType, ...],
        present_identities: list[tuple[str, ObjectType, str]],
        actor_id: int,
    ) -> list[tuple[str, str, str]]:
        """Soft-delete only rows absent from one application-verified complete scope."""
        if not profile.metadata_context_write:
            raise SqlCtxError(
                "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED",
                "The selected profile does not enable metadata_context_write.",
                status_code=403,
            )
        if not schemas or not object_types:
            raise SqlCtxError(
                "METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED",
                "Complete synchronization requires non-empty schema and object-type scope.",
                status_code=409,
            )
        if (
            set(schemas) != set(profile.allowed_schemas)
            or set(object_types) != set(profile.allowed_object_types)
            or profile.excluded_object_patterns
        ):
            raise SqlCtxError(
                "METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED",
                "Deactivation scope must exactly match an unexcluded profile boundary.",
                status_code=409,
            )
        schema_placeholders = ", ".join("?" for _ in schemas)
        type_placeholders = ", ".join("?" for _ in object_types)
        inventory = json.dumps(
            [
                {
                    "schema_name": schema_name,
                    "object_type": str(object_type).upper(),
                    "object_name": object_name,
                }
                for schema_name, object_type, object_name in present_identities
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        rows = self._execute_write_returning(
            profile,
            f"""
            UPDATE target
               SET [DEL_FLAG] = 1,
                   [DATE_MODIFIED] = SYSUTCDATETIME(),
                   [USER_MODIFIED] = ?
            OUTPUT inserted.[SCHEMA_NAME] AS [schema_name],
                   inserted.[OBJECT_TYPE] AS [object_type],
                   inserted.[OBJECT_NAME] AS [object_name]
              FROM [agrimap_app].[DB_METADATA_CONTEXT] AS target
             WHERE target.[DEL_FLAG] = 0
               AND target.[SCHEMA_NAME] IN ({schema_placeholders})
               AND target.[OBJECT_TYPE] IN ({type_placeholders})
               AND NOT EXISTS
               (
                   SELECT 1
                     FROM OPENJSON(?)
                     WITH
                     (
                         [schema_name] NVARCHAR(128) '$.schema_name',
                         [object_type] VARCHAR(20) '$.object_type',
                         [object_name] NVARCHAR(256) '$.object_name'
                     ) AS present
                    WHERE present.[schema_name] = target.[SCHEMA_NAME]
                      AND present.[object_type] = target.[OBJECT_TYPE]
                      AND present.[object_name] = target.[OBJECT_NAME]
               );
            """,
            self._parameters(
                actor_id,
                *schemas,
                *(str(object_type).upper() for object_type in object_types),
                inventory,
            ),
        )
        return [
            (
                str(row["schema_name"]),
                str(row["object_type"]),
                str(row["object_name"]),
            )
            for row in rows
        ]

    def list_metadata_context(
        self, profile: ResolvedConnectionProfile, request: ContextIndexListRequest
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List bounded index metadata using parameterized filters and an ID cursor."""
        schema_placeholders = ", ".join("?" for _ in profile.allowed_schemas)
        clauses = ["[DEL_FLAG] = ?", f"[SCHEMA_NAME] IN ({schema_placeholders})"]
        values: list[Any] = [0 if request.active else 1, *profile.allowed_schemas]
        if request.context is not None:
            clauses.append("[CONTEXT_CODE] = ?")
            values.append(request.context)
        if request.object_type is not None:
            clauses.append("[OBJECT_TYPE] = ?")
            values.append(str(request.object_type).upper())
        if request.status is not None:
            clauses.append("[CLASSIFICATION_STATUS] = ?")
            values.append(request.status.upper())
        if request.tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM OPENJSON([TAGS_JSON]) WHERE [value] = ?)")
            values.append(request.tag)
        if request.cursor is not None:
            try:
                cursor_id = int(request.cursor)
            except ValueError as exc:
                raise SqlCtxError("INVALID_CURSOR", "Context index cursor is invalid.") from exc
            clauses.append("[ID] > ?")
            values.append(cursor_id)
        values.append(request.limit + 1)
        rows = self._execute(
            profile,
            """
            SELECT TOP (?) [ID], [SCHEMA_NAME], [OBJECT_NAME], [OBJECT_TYPE], [CONTEXT_CODE],
                   [DESCRIPTION], [TAGS_JSON], [CLASSIFICATION_STATUS],
                   [CLASSIFICATION_SOURCE], [SOURCE_FINGERPRINT], [CONTENT_HASH],
                   [MANAGED_RELATIVE_PATH], [HEADER_VERSION], [OUTPUT_FORMAT_VERSION],
                   [EVIDENCE_JSON], [LAST_CLASSIFIED_AT], [LAST_GENERATED_AT], [DEL_FLAG]
              FROM [agrimap_app].[DB_METADATA_CONTEXT]
             WHERE """
            + " AND ".join(clauses)
            + " ORDER BY [ID] ASC",
            self._parameters(values[-1], *values[:-1]),
        )
        has_more = len(rows) > request.limit
        selected = rows[: request.limit]
        next_cursor = str(selected[-1]["id"]) if has_more and selected else None
        return selected, next_cursor

    def apply_routine_statement(
        self,
        profile: ResolvedConnectionProfile,
        statement: str,
        object_type: ObjectType,
    ) -> None:
        """Apply one validated non-destructive SQL Server routine statement."""
        if not profile.routine_write:
            raise SqlCtxError(
                "ROUTINE_WRITE_SCOPE_REQUIRED",
                "The selected profile does not enable routine_write.",
                status_code=403,
            )
        if object_type == ObjectType.PROCEDURE:
            normalized = self.normalize_procedure_definition(statement)
        elif object_type == ObjectType.FUNCTION:
            normalized = self.normalize_function_definition(statement)
        else:
            raise SqlCtxError(
                "ROUTINE_OBJECT_TYPE_REQUIRED", "Only procedures/functions can apply."
            )
        self._execute_write(profile, normalized)

    def assert_query_read_only(
        self, profile: ResolvedConnectionProfile, tables: tuple[ObjectRef, ...]
    ) -> None:
        """Fail closed when the SQL Server principal has effective write/admin capability."""
        database_rows = self._execute(
            profile,
            """
            SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS can_create_table,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE PROCEDURE') AS can_create_procedure,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE VIEW') AS can_create_view,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE FUNCTION') AS can_create_function,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') AS can_insert,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') AS can_update,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') AS can_delete,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER') AS can_alter,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL') AS can_control,
                   HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE') AS can_execute
            """,
        )
        if self._permission_present_or_unknown(database_rows):
            self._raise_query_permission_error()
        for table in tables:
            qualified = f"{table.schema_name}.{table.object_name}"
            rows = self._execute(
                profile,
                """
                SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', 'INSERT') AS can_insert,
                       HAS_PERMS_BY_NAME(?, 'OBJECT', 'UPDATE') AS can_update,
                       HAS_PERMS_BY_NAME(?, 'OBJECT', 'DELETE') AS can_delete,
                       HAS_PERMS_BY_NAME(?, 'OBJECT', 'ALTER') AS can_alter,
                       HAS_PERMS_BY_NAME(?, 'OBJECT', 'CONTROL') AS can_control
                """,
                self._parameters(qualified, qualified, qualified, qualified, qualified),
            )
            if self._permission_present_or_unknown(rows):
                self._raise_query_permission_error()

    @staticmethod
    def _permission_present_or_unknown(rows: list[dict[str, object]]) -> bool:
        if len(rows) != 1 or not rows[0]:
            return True
        return any(value is None or bool(value) for value in rows[0].values())

    @staticmethod
    def _raise_query_permission_error() -> None:
        from sqlctx.core.errors import SqlCtxError

        raise SqlCtxError(
            "QUERY_READ_ONLY_CONTEXT_REQUIRED",
            "The selected profile does not prove an effective read-only query context.",
            status_code=403,
        )

    def sample_query(self, ref: ObjectRef, order: list[str], requested: int) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "(SELECT 1)"
        return f"SELECT TOP ({requested}) * FROM {qualified} ORDER BY {order_sql}"

    def sample_page_query(
        self, ref: ObjectRef, order: list[str], page_size: int, offset: int
    ) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "(SELECT 1)"
        return (
            f"SELECT * FROM {qualified} ORDER BY {order_sql} "
            f"OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
        )

    def schema_fingerprint(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> str:
        """Hash visible object identity and SQL Server modify dates without reading data."""
        fingerprints = self.object_fingerprints(profile, schemas, object_types)
        encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def object_fingerprints(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> dict[str, str]:
        """Use SQL Server modify dates as definition-level incremental validators."""
        allowed_types = set(object_types)
        payload: dict[str, str] = {}
        query = """
            SELECT o.name AS object_name,
                   CASE WHEN o.type = 'U' THEN 'table'
                        WHEN o.type = 'P' THEN 'procedure'
                        ELSE 'function' END AS object_type,
                   CONVERT(nvarchar(33), o.modify_date, 126) AS modified_at
              FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
             WHERE s.name = ? AND o.type IN ('U', 'P', 'FN', 'IF', 'TF')
               AND o.is_ms_shipped = 0
             ORDER BY object_type, object_name
        """
        for schema in schemas:
            for row in self._execute(profile, query, self._parameters(schema)):
                object_type = self._object_type(str(row["object_type"]))
                name = str(row["object_name"])
                if object_type not in allowed_types or any(
                    fnmatchcase(name.lower(), pattern.lower())
                    for pattern in profile.excluded_object_patterns
                ):
                    continue
                object_id = f"{object_type.value}:{schema}.{name}"
                validator = f"{schema}\0{object_type.value}\0{name}\0{row.get('modified_at') or ''}"
                payload[object_id] = "sha256:" + hashlib.sha256(validator.encode()).hexdigest()
        return payload
