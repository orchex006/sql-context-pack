from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.errors import SqlCtxError
from sqlctx.query_data.contracts import QueryDataResult, QueryResultColumn
from sqlctx.server import facade


class FakeService:
    last_command: Any = None

    def resolve_query_profile(self, requested: str | None) -> str:
        return requested or "only-ready"

    def query(self, command: Any) -> QueryDataResult:
        FakeService.last_command = command
        return QueryDataResult(
            profile=command.profile,
            columns=[QueryResultColumn(name="ID", display_name="ID")],
            returned_row_count=1,
            markdown="| ID |\n| --- |\n| 1 |\n",
            value_mode=command.value_mode,
        )

    def stream_query(self, command: Any) -> list[str]:
        FakeService.last_command = command
        return ["| ID |", "| --- |", "| 1 |", "| 2 |"]


def test_query_defaults_to_short_bounded_markdown(monkeypatch: Any) -> None:
    monkeypatch.setattr(facade, "ServiceFacade", FakeService)
    result = CliRunner().invoke(main.app, ["query", "SELECT ID FROM dbo.CONTENT"])

    assert result.exit_code == 0
    assert result.stdout == "| ID |\n| --- |\n| 1 |\n"
    assert FakeService.last_command.profile == "only-ready"
    assert FakeService.last_command.max_rows == 100
    assert FakeService.last_command.value_mode == "short"


def test_query_all_rows_and_full_mode_stream_markdown(monkeypatch: Any) -> None:
    monkeypatch.setattr(facade, "ServiceFacade", FakeService)
    result = CliRunner().invoke(
        main.app,
        [
            "query",
            "SELECT CONFIG_PAYLOAD FROM dbo.CONTENT",
            "--profile",
            "demo",
            "--all-rows",
            "--value-mode",
            "full",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "| ID |\n| --- |\n| 1 |\n| 2 |\n"
    assert FakeService.last_command.profile == "demo"
    assert FakeService.last_command.value_mode == "full"


def test_query_rejects_all_rows_with_max_rows(monkeypatch: Any) -> None:
    monkeypatch.setattr(facade, "ServiceFacade", FakeService)
    result = CliRunner().invoke(
        main.app,
        ["query", "SELECT 1", "--all-rows", "--max-rows", "10"],
    )
    assert result.exit_code != 0
    assert "QUERY_ROW_OPTIONS_CONFLICT" in str(result.exception)


def test_bounded_cli_reports_truncation_as_markdown(monkeypatch: Any) -> None:
    class TruncatedService(FakeService):
        def query(self, command: Any) -> QueryDataResult:
            return QueryDataResult(
                profile=command.profile,
                columns=[QueryResultColumn(name="ID", display_name="ID")],
                returned_row_count=100,
                truncated=True,
                truncation_reason="row_limit",
                markdown="| ID |\n| --- |\n| 1 |\n",
            )

    monkeypatch.setattr(facade, "ServiceFacade", TruncatedService)
    result = CliRunner().invoke(main.app, ["query", "SELECT ID FROM dbo.CONTENT"])
    assert result.exit_code == 0
    assert "Result truncated (row_limit); returned 100 rows" in result.stdout


def test_all_rows_partial_failure_is_sanitized_nonzero_and_closes_stream(
    monkeypatch: Any,
) -> None:
    cleanup = {"closed": False}

    class PartialFailureService(FakeService):
        def stream_query(self, command: Any) -> Any:
            def rows() -> Any:
                try:
                    yield "| ID |"
                    yield "| --- |"
                    raise SqlCtxError(
                        "QUERY_TIMEOUT",
                        "The read-only query exceeded its configured timeout.",
                    )
                finally:
                    cleanup["closed"] = True

            return rows()

    monkeypatch.setattr(facade, "ServiceFacade", PartialFailureService)
    result = CliRunner().invoke(
        main.app,
        ["query", "SELECT 'sql-secret' FROM dbo.CONTENT", "--all-rows"],
    )

    assert result.exit_code == 2
    assert result.stdout == "| ID |\n| --- |\n"
    assert "QUERY_TIMEOUT" in result.stderr
    assert "sql-secret" not in result.stderr
    assert "Traceback" not in result.stderr
    assert cleanup["closed"] is True
