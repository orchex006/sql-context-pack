"""Validated, protected, bounded or streamed Query Data orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlctx.adapters.base import QueryColumnMetadata
from sqlctx.core.enums import SensitivityClass
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.query_data.contracts import (
    QueryDataRequest,
    QueryDataResult,
    QueryResultColumn,
    RevealHandoff,
)
from sqlctx.query_data.guard import restricted_sensitive_columns
from sqlctx.query_data.lineage import MaskingLineage
from sqlctx.query_data.markdown import display_names, header_lines, row_line
from sqlctx.query_data.masking import EphemeralQueryMasker
from sqlctx.query_data.validation import QueryValidator, ValidatedQuery
from sqlctx.query_data.values import bounded_text_value
from sqlctx.security.masking import HIGH_RISK, DeterministicMaskingEngine, column_marker
from sqlctx.security.sensitivity import TREATMENT

MAX_COLUMNS = 50
MAX_MARKDOWN_BYTES = 256 * 1024
FETCH_BATCH_SIZE = 100
SCANNED_MARKER = "⟨SCANNED⟩"


class RowStream(Protocol):
    columns: tuple[QueryColumnMetadata, ...] | list[QueryColumnMetadata]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]: ...


class QueryAdapter(Protocol):
    dialect: str

    def discover_objects(self, profile: ResolvedConnectionProfile) -> Iterable[ObjectRef]: ...
    def quote_identifier(self, identifier: str) -> str: ...
    def parameter_placeholder(self, position: int) -> str: ...
    def assert_query_read_only(
        self, profile: ResolvedConnectionProfile, tables: tuple[ObjectRef, ...]
    ) -> None: ...
    def open_query(
        self, profile: ResolvedConnectionProfile, query: str, parameters: tuple[Any, ...]
    ) -> Any: ...


@dataclass(frozen=True)
class _ColumnPolicy:
    """How one result column is protected, decided from lineage before any row is read."""

    sensitivity: SensitivityClass
    source_name: str
    # True when the class came from lineage or the column name, so every value gets it.
    forced: bool


def reveal_handoff(
    *,
    reason: Literal["protected_columns", "restricted_usage"],
    columns: list[str],
    profile: str,
    sql: str,
) -> RevealHandoff:
    return RevealHandoff(
        reason=reason,
        columns=columns,
        profile=profile,
        sql=sql,
        user_steps=[
            "Run this yourself, outside the AI session. Save the SQL to a file, then in your "
            "own interactive terminal run: "
            f"sqlctx query --reveal --profile {profile} --sql-file <file.sql>. It asks you to "
            "confirm, then prints real values to that terminal only.",
            "Or run the SQL in SSMS or Azure Data Studio with your own database account.",
            "Do not paste the real values back into the AI chat.",
        ],
    )


class QueryDataService:
    def __init__(
        self,
        classifier: DeterministicMaskingEngine,
        validator: QueryValidator | None = None,
    ) -> None:
        self.classifier = classifier
        self.validator = validator or QueryValidator()

    def execute(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> QueryDataResult:
        validated, masker = self._prepare(request, profile=profile, adapter=adapter)
        with adapter.open_query(profile, validated.sql, validated.parameters) as stream:
            columns, names = self._columns(stream)
            policies = self._policies(columns, masker, validated.lineage)
            scanned = [False] * len(columns)
            rows: list[str] = []
            # Reserve room for a late ⟨SCANNED⟩ marker so the final header still fits the bound.
            current_bytes = self._markdown_bytes(
                list(header_lines(self._headers(names, policies)))
            ) + len(SCANNED_MARKER.encode("utf-8")) * len(columns)
            emitted = 0
            truncated = False
            reason: Literal["row_limit", "output_limit"] | None = None
            stop = False
            while not stop:
                batch = stream.fetchmany(min(FETCH_BATCH_SIZE, request.max_rows + 1 - emitted))
                if not batch:
                    break
                for raw_row in batch:
                    if emitted >= request.max_rows:
                        truncated = True
                        reason = "row_limit"
                        stop = True
                        break
                    line = row_line(
                        self._shape_row(
                            raw_row, columns, policies, masker, request.value_mode, scanned
                        )
                    )
                    candidate_bytes = current_bytes + len((line + "\n").encode("utf-8"))
                    if candidate_bytes > MAX_MARKDOWN_BYTES:
                        if request.value_mode == "full":
                            raise SqlCtxError(
                                "QUERY_RESULT_TOO_LARGE",
                                "The complete masked result exceeds the bounded response size; use short mode, a narrower query, or owner CLI all-row output.",
                                status_code=413,
                            )
                        truncated = True
                        reason = "output_limit"
                        stop = True
                        break
                    rows.append(line)
                    current_bytes = candidate_bytes
                    emitted += 1
            markers = [
                column_marker(policy.sensitivity) or (SCANNED_MARKER if was_scanned else "")
                for policy, was_scanned in zip(policies, scanned, strict=True)
            ]
            lines = [*header_lines(self._marked(names, markers)), *rows]
            protected = [name for name, marker in zip(names, markers, strict=True) if marker]
            counts = {key: value for key, value in sorted(masker.protector.counts.items()) if value}
            return QueryDataResult(
                profile=profile.name,
                columns=[
                    QueryResultColumn(
                        name=column.name,
                        display_name=name,
                        data_type=column.data_type,
                        sensitivity=policy.sensitivity.value,
                        treatment=(
                            TREATMENT.get(policy.sensitivity, "redact")
                            if policy.sensitivity != SensitivityClass.PUBLIC
                            else ("scan" if was_scanned else "public")
                        ),
                        marker=marker,
                    )
                    for column, name, policy, marker, was_scanned in zip(
                        columns, names, policies, markers, scanned, strict=True
                    )
                ],
                returned_row_count=emitted,
                truncated=truncated,
                truncation_reason=reason,
                value_mode=request.value_mode,
                markdown="\n".join(lines) + "\n",
                protected_value_counts=counts,
                reveal_handoff=(
                    reveal_handoff(
                        reason="protected_columns",
                        columns=protected,
                        profile=profile.name,
                        sql=request.sql,
                    )
                    if protected
                    else None
                ),
            )

    def stream_markdown(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> Iterator[str]:
        validated, masker = self._prepare(request, profile=profile, adapter=adapter)
        with adapter.open_query(profile, validated.sql, validated.parameters) as stream:
            columns, names = self._columns(stream)
            policies = self._policies(columns, masker, validated.lineage)
            scanned = [False] * len(columns)
            yield from header_lines(self._headers(names, policies))
            while True:
                batch = stream.fetchmany(FETCH_BATCH_SIZE)
                if not batch:
                    break
                for raw_row in batch:
                    yield row_line(
                        self._shape_row(
                            raw_row, columns, policies, masker, request.value_mode, scanned
                        )
                    )
            late = [name for name, flag in zip(names, scanned, strict=True) if flag]
            if late:
                yield ""
                yield (
                    f"> **sqlctx:** {SCANNED_MARKER} personal data embedded in "
                    f"{', '.join(late)} was replaced with fakes."
                )

    def stream_revealed(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> Iterator[str]:
        """Unprotected rows for the owner's interactive terminal only.

        Never reachable from MCP or HTTP: only the CLI `--reveal` path calls it, after it has
        proven an interactive terminal and an explicit confirmation.
        """
        validated = self.validator.validate(request.sql, profile=profile, adapter=adapter)
        adapter.assert_query_read_only(profile, validated.tables)
        with adapter.open_query(profile, validated.sql, validated.parameters) as stream:
            columns, names = self._columns(stream)
            yield from header_lines(names)
            while True:
                batch = stream.fetchmany(FETCH_BATCH_SIZE)
                if not batch:
                    break
                for raw_row in batch:
                    yield row_line(
                        [
                            f"[BINARY {len(value)} BYTES]"
                            if isinstance(value, (bytes, bytearray, memoryview))
                            else value
                            for value in raw_row
                        ]
                    )

    def _prepare(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> tuple[ValidatedQuery, EphemeralQueryMasker]:
        validated = self.validator.validate(request.sql, profile=profile, adapter=adapter)
        adapter.assert_query_read_only(profile, validated.tables)
        lookup = getattr(adapter, "sensitivity_classifications", None)
        column_classes = lookup(profile, validated.tables) if callable(lookup) else {}
        masker = EphemeralQueryMasker(self.classifier, column_classes)
        if validated.tree is not None:
            restricted = restricted_sensitive_columns(
                validated.tree,
                lambda name: masker.column_class(name) != SensitivityClass.PUBLIC,
            )
            if restricted:
                raise SqlCtxError(
                    "QUERY_SENSITIVE_USAGE_RESTRICTED",
                    "Protected columns may only be selected directly, not used in filters, "
                    "joins, grouping, ordering, CASE or functions, because that reveals their "
                    "values. Follow reveal_handoff to let the user see them.",
                    status_code=403,
                    details={
                        "columns": restricted,
                        "reveal_handoff": reveal_handoff(
                            reason="restricted_usage",
                            columns=restricted,
                            profile=profile.name,
                            sql=request.sql,
                        ).model_dump(mode="json"),
                    },
                )
        return validated, masker

    @staticmethod
    def _columns(stream: RowStream) -> tuple[list[QueryColumnMetadata], list[str]]:
        columns = list(stream.columns)
        if len(columns) > MAX_COLUMNS:
            raise SqlCtxError(
                "QUERY_COLUMN_LIMIT_EXCEEDED",
                f"Query results may contain at most {MAX_COLUMNS} columns.",
            )
        names = display_names([column.name for column in columns])
        return columns, names

    @staticmethod
    def _policies(
        columns: list[QueryColumnMetadata],
        masker: EphemeralQueryMasker,
        lineage: MaskingLineage,
    ) -> list[_ColumnPolicy]:
        policies: list[_ColumnPolicy] = []
        for index, column in enumerate(columns):
            sources = lineage.sources(index, len(columns))
            if sources is None:
                # Unknown lineage is never treated as public.
                policies.append(
                    _ColumnPolicy(SensitivityClass.UNKNOWN_SENSITIVE, column.name, True)
                )
                continue
            sensitive = [
                (name, sensitivity)
                for name in sources
                if (sensitivity := masker.column_class(name)) != SensitivityClass.PUBLIC
            ]
            high_risk = [item for item in sensitive if item[1] in HIGH_RISK]
            if high_risk:
                policies.append(_ColumnPolicy(high_risk[0][1], high_risk[0][0], True))
            elif sensitive and (lineage.complex_scope or len({s for _, s in sensitive}) > 1):
                # Mixed or untraceable sources cannot safely share one fake strategy.
                policies.append(
                    _ColumnPolicy(SensitivityClass.UNKNOWN_SENSITIVE, sensitive[0][0], True)
                )
            elif sensitive:
                policies.append(_ColumnPolicy(sensitive[0][1], sensitive[0][0], True))
            else:
                by_output = masker.column_class(column.name)
                policies.append(
                    _ColumnPolicy(by_output, column.name, by_output != SensitivityClass.PUBLIC)
                )
        return policies

    @staticmethod
    def _headers(names: list[str], policies: list[_ColumnPolicy]) -> list[str]:
        return QueryDataService._marked(
            names, [column_marker(policy.sensitivity) for policy in policies]
        )

    @staticmethod
    def _marked(names: list[str], markers: list[str]) -> list[str]:
        return [
            f"{name} {marker}" if marker else name
            for name, marker in zip(names, markers, strict=True)
        ]

    @staticmethod
    def _shape_row(
        raw_row: tuple[Any, ...],
        columns: list[QueryColumnMetadata],
        policies: list[_ColumnPolicy],
        masker: EphemeralQueryMasker,
        value_mode: str,
        scanned: list[bool],
    ) -> list[Any]:
        values: list[Any] = []
        for index, column in enumerate(columns):
            raw = raw_row[index] if index < len(raw_row) else None
            policy = policies[index]
            if policy.forced:
                masked = masker.mask(policy.source_name, raw, policy.sensitivity)
            else:
                masked = masker.mask(column.name, raw)
                if masked != raw and not isinstance(raw, (bytes, bytearray, memoryview)):
                    scanned[index] = True
            values.append(
                bounded_text_value(
                    masked,
                    column_name=column.name,
                    data_type=column.data_type,
                )
                if value_mode == "short"
                else (
                    f"[BINARY {len(masked)} BYTES]"
                    if isinstance(masked, (bytes, bytearray, memoryview))
                    else masked
                )
            )
        return values

    @staticmethod
    def _markdown_bytes(lines: list[str]) -> int:
        return len(("\n".join(lines) + "\n").encode("utf-8"))
