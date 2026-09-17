"""Validated, masked, bounded or streamed Query Data orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Literal, Protocol

from sqlctx.adapters.base import QueryColumnMetadata
from sqlctx.core.enums import SensitivityClass
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.query_data.contracts import QueryDataRequest, QueryDataResult, QueryResultColumn
from sqlctx.query_data.lineage import MaskingLineage
from sqlctx.query_data.markdown import display_names, header_lines, row_line
from sqlctx.query_data.masking import EphemeralQueryMasker
from sqlctx.query_data.validation import QueryValidator, ValidatedQuery
from sqlctx.query_data.values import bounded_text_value
from sqlctx.security.masking import HIGH_RISK, DeterministicMaskingEngine

MAX_COLUMNS = 50
MAX_MARKDOWN_BYTES = 256 * 1024
FETCH_BATCH_SIZE = 100


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
        validated = self._prepare(request, profile=profile, adapter=adapter)
        with adapter.open_query(profile, validated.sql, validated.parameters) as stream:
            columns, names = self._columns(stream)
            masker = EphemeralQueryMasker(self.classifier)
            lines = list(header_lines(names))
            current_bytes = self._markdown_bytes(lines)
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
                            raw_row, columns, masker, request.value_mode, validated.lineage
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
                    lines.append(line)
                    current_bytes = candidate_bytes
                    emitted += 1
            markdown = "\n".join(lines) + "\n"
            return QueryDataResult(
                profile=profile.name,
                columns=[
                    QueryResultColumn(
                        name=column.name,
                        display_name=name,
                        data_type=column.data_type,
                    )
                    for column, name in zip(columns, names, strict=True)
                ],
                returned_row_count=emitted,
                truncated=truncated,
                truncation_reason=reason,
                value_mode=request.value_mode,
                markdown=markdown,
            )

    def stream_markdown(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> Iterator[str]:
        validated = self._prepare(request, profile=profile, adapter=adapter)
        with adapter.open_query(profile, validated.sql, validated.parameters) as stream:
            columns, names = self._columns(stream)
            masker = EphemeralQueryMasker(self.classifier)
            yield from header_lines(names)
            while True:
                batch = stream.fetchmany(FETCH_BATCH_SIZE)
                if not batch:
                    break
                for raw_row in batch:
                    yield row_line(
                        self._shape_row(
                            raw_row, columns, masker, request.value_mode, validated.lineage
                        )
                    )

    def _prepare(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: QueryAdapter,
    ) -> ValidatedQuery:
        validated = self.validator.validate(request.sql, profile=profile, adapter=adapter)
        adapter.assert_query_read_only(profile, validated.tables)
        return validated

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
    def _shape_row(
        raw_row: tuple[Any, ...],
        columns: list[QueryColumnMetadata],
        masker: EphemeralQueryMasker,
        value_mode: str,
        lineage: MaskingLineage,
    ) -> list[Any]:
        values: list[Any] = []
        for index, column in enumerate(columns):
            raw = raw_row[index] if index < len(raw_row) else None
            sources = lineage.sources(index, len(columns))
            source_classes = [
                (name, masker.classifier.classify(name, None)) for name in (sources or ())
            ]
            sensitive = [
                (name, sensitivity)
                for name, sensitivity in source_classes
                if sensitivity != SensitivityClass.PUBLIC
            ]
            if (
                sources is None
                or any(sensitivity in HIGH_RISK for _, sensitivity in sensitive)
                or (lineage.complex_scope and sensitive)
            ):
                masked = None if raw is None else "[REDACTED]"
            elif sensitive:
                # Different source sensitivities cannot safely share a single alias strategy.
                masked = (
                    masker.mask(sensitive[0][0], raw)
                    if len({s for _, s in sensitive}) == 1
                    else "[REDACTED]"
                )
            else:
                masked = masker.mask(column.name, raw)
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
