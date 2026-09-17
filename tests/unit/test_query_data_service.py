from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from sqlctx.adapters.base import QueryColumnMetadata
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.query_data.contracts import QueryDataRequest
from sqlctx.query_data.service import QueryDataService
from sqlctx.security.masking import DeterministicMaskingEngine


class ClassifierOnlyMasker(DeterministicMaskingEngine):
    def __init__(self) -> None:
        pass


class FakeStream:
    def __init__(self, columns: list[QueryColumnMetadata], batches: list[list[tuple[Any, ...]]]):
        self.columns = columns
        self.batches = list(batches)
        self.fetch_calls = 0

    def fetchmany(self, _: int) -> list[tuple[Any, ...]]:
        self.fetch_calls += 1
        return self.batches.pop(0) if self.batches else []


class FakeQueryAdapter:
    dialect = "tsql"

    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.read_only_checked = False
        self.opened_sql = ""
        self.opened_parameters: tuple[Any, ...] = ()

    def discover_objects(self, _: ResolvedConnectionProfile) -> list[ObjectRef]:
        return [
            ObjectRef(
                object_id="table:dbo.CONTENT_SHARE",
                engine="sqlserver",
                schema_name="dbo",
                object_name="CONTENT_SHARE",
                object_type="table",
            )
        ]

    @staticmethod
    def quote_identifier(value: str) -> str:
        return f"[{value}]"

    @staticmethod
    def parameter_placeholder(_: int) -> str:
        return "?"

    def assert_query_read_only(
        self, _: ResolvedConnectionProfile, __: tuple[ObjectRef, ...]
    ) -> None:
        self.read_only_checked = True

    @contextmanager
    def open_query(
        self,
        _: ResolvedConnectionProfile,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> Iterator[FakeStream]:
        self.opened_sql = sql
        self.opened_parameters = parameters
        yield self.stream


def profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine="sqlserver",
        host="host",
        port=1433,
        database="db",
        username="reader",
        password="secret",
        allowed_schemas=("dbo",),
        allowed_object_types=("table",),
    )


def service() -> QueryDataService:
    return QueryDataService(ClassifierOnlyMasker())


def test_short_mode_masks_and_uses_existing_payload_markers() -> None:
    payload = '{"long":"' + ("x" * 220) + '"}'
    stream = FakeStream(
        [
            QueryColumnMetadata("EMAIL", "nvarchar"),
            QueryColumnMetadata("CONFIG_QUERY_PAYLOAD", "nvarchar(max)"),
        ],
        [[("person@example.com", payload)], []],
    )
    adapter = FakeQueryAdapter(stream)
    result = service().execute(
        QueryDataRequest(profile="demo", sql="SELECT * FROM dbo.CONTENT_SHARE", value_mode="short"),
        profile=profile(),
        adapter=adapter,  # type: ignore[arg-type]
    )

    assert "person@example.com" not in result.markdown
    assert "user_" in result.markdown
    assert f"...json string payload...({len(payload.encode())} bytes)..." in result.markdown
    assert result.masked is True
    assert result.returned_row_count == 1
    assert adapter.read_only_checked is True


def test_full_mode_returns_complete_masked_escaped_text() -> None:
    payload = "line 1|line 2\n" + ("x" * 220)
    stream = FakeStream(
        [QueryColumnMetadata("config_payload", "text")],
        [[(payload,)], []],
    )
    result = service().execute(
        QueryDataRequest(
            profile="demo", sql="SELECT config_payload FROM dbo.CONTENT_SHARE", value_mode="full"
        ),
        profile=profile(),
        adapter=FakeQueryAdapter(stream),  # type: ignore[arg-type]
    )
    assert "long text payload" not in result.markdown
    assert "line 1\\|line 2<br>" in result.markdown
    assert "x" * 220 in result.markdown


def test_full_json_payload_masks_nested_secrets_without_persisting_raw_values() -> None:
    payload = '{"password":"open-sesame","email":"person@example.com","enabled":true}'
    stream = FakeStream(
        [QueryColumnMetadata("config_payload", "json")],
        [[(payload,)], []],
    )
    result = service().execute(
        QueryDataRequest(
            profile="demo",
            sql="SELECT config_payload FROM dbo.CONTENT_SHARE",
            value_mode="full",
        ),
        profile=profile(),
        adapter=FakeQueryAdapter(stream),  # type: ignore[arg-type]
    )
    assert "open-sesame" not in result.markdown
    assert "person@example.com" not in result.markdown
    assert "[REDACTED]" in result.markdown
    assert "@example.invalid" in result.markdown


def test_bounded_result_reports_extra_row_without_emitting_it() -> None:
    stream = FakeStream(
        [QueryColumnMetadata("ID", "int")],
        [[(1,), (2,), (3,)], []],
    )
    result = service().execute(
        QueryDataRequest(profile="demo", sql="SELECT ID FROM dbo.CONTENT_SHARE", max_rows=2),
        profile=profile(),
        adapter=FakeQueryAdapter(stream),  # type: ignore[arg-type]
    )
    assert result.returned_row_count == 2
    assert result.truncated is True
    assert result.truncation_reason == "row_limit"
    assert "| 3 |" not in result.markdown


def test_full_mode_never_silently_cuts_an_oversize_cell() -> None:
    stream = FakeStream(
        [QueryColumnMetadata("config_payload", "text")],
        [[("x" * (300 * 1024),)], []],
    )
    with pytest.raises(SqlCtxError) as caught:
        service().execute(
            QueryDataRequest(
                profile="demo",
                sql="SELECT config_payload FROM dbo.CONTENT_SHARE",
                value_mode="full",
            ),
            profile=profile(),
            adapter=FakeQueryAdapter(stream),  # type: ignore[arg-type]
        )
    assert caught.value.code == "QUERY_RESULT_TOO_LARGE"


def test_all_row_markdown_streams_multiple_batches_with_one_header() -> None:
    stream = FakeStream(
        [QueryColumnMetadata("ID", "int")],
        [[(1,), (2,)], [(3,)], []],
    )
    lines = list(
        service().stream_markdown(
            QueryDataRequest(profile="demo", sql="SELECT ID FROM dbo.CONTENT_SHARE"),
            profile=profile(),
            adapter=FakeQueryAdapter(stream),  # type: ignore[arg-type]
        )
    )
    assert lines[:2] == ["| ID |", "| --- |"]
    assert lines[2:] == ["| 1 |", "| 2 |", "| 3 |"]
    assert stream.fetch_calls == 3
