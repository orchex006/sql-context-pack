from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.models import HostPythonToolingDescriptor
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore


class FormatRunner:
    """Stand-in for the pinned SQLFluff subprocesses used by the owner CLI."""

    def __init__(self, parse_fails: bool = False) -> None:
        self.parse_fails = parse_fails
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command[1] == "-c":
            return subprocess.CompletedProcess(command, 0, "[3, 11, 10]\n", "")
        if command[1:4] == ["-m", "sqlfluff", "version"]:
            return subprocess.CompletedProcess(command, 0, "sqlfluff, version 4.2.2\n", "")
        if command[1:4] == ["-m", "sqlfluff", "parse"]:
            code = 1 if self.parse_fails else 0
            return subprocess.CompletedProcess(command, code, "", "parse failed" if code else "")
        if command[1:4] == ["-m", "sqlfluff", "format"]:
            Path(command[-1]).write_text("SELECT\n    1;\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")


def install_runner(monkeypatch: Any, tmp_path: Path, runner: FormatRunner) -> None:
    """Pin the CLI's manager/formatter to an isolated runtime and the fake runner."""
    state = JsonRuntimeStateStore(tmp_path / "runtime")

    def build_manager(*_: Any, **__: Any) -> SqlFluffManager:
        return SqlFluffManager(state, python_executable=Path(sys.executable), runner=runner)

    def build_formatter(manager: SqlFluffManager) -> SqlFluffFormatter:
        return SqlFluffFormatter(manager, runner=runner)

    monkeypatch.setattr(main, "SqlFluffManager", build_manager)
    monkeypatch.setattr(main, "SqlFluffFormatter", build_formatter)


def write_sql(tmp_path: Path, text: str = "select 1;", name: str = "object.sql") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_format_prints_result_and_never_rewrites_the_source(
    monkeypatch: Any, tmp_path: Path
) -> None:
    install_runner(monkeypatch, tmp_path, FormatRunner())
    source = write_sql(tmp_path)

    result = CliRunner().invoke(main.app, ["format", str(source)])

    assert result.exit_code == 0
    assert result.stdout == "SELECT\n    1;\n"
    assert source.read_text(encoding="utf-8") == "select 1;"


def test_format_defaults_to_ansi_and_accepts_an_explicit_dialect(
    monkeypatch: Any, tmp_path: Path
) -> None:
    runner = FormatRunner()
    install_runner(monkeypatch, tmp_path, runner)
    source = write_sql(tmp_path)

    assert CliRunner().invoke(main.app, ["format", str(source)]).exit_code == 0
    default_dialects = [
        command[command.index("--dialect") + 1]
        for command in runner.commands
        if "--dialect" in command
    ]
    assert set(default_dialects) == {"ansi"}

    runner.commands.clear()
    explicit = CliRunner().invoke(main.app, ["format", str(source), "--dialect", "tsql"])

    assert explicit.exit_code == 0
    assert "tsql" in {
        command[command.index("--dialect") + 1]
        for command in runner.commands
        if "--dialect" in command
    }


def test_unparseable_sql_is_preserved_and_reported_without_claiming_success(
    monkeypatch: Any, tmp_path: Path
) -> None:
    install_runner(monkeypatch, tmp_path, FormatRunner(parse_fails=True))
    source = write_sql(tmp_path, "BROKEN SQL")

    result = CliRunner().invoke(main.app, ["format", str(source)])

    assert result.exit_code == 2
    assert result.stdout == "BROKEN SQL"
    assert source.read_text(encoding="utf-8") == "BROKEN SQL"
    reported = json.loads(result.stderr)
    assert reported["status"] == "parse_failed"
    assert reported["file"] == "object.sql"
    assert str(tmp_path) not in result.stderr


def test_format_rejects_an_unknown_dialect(monkeypatch: Any, tmp_path: Path) -> None:
    install_runner(monkeypatch, tmp_path, FormatRunner())
    source = write_sql(tmp_path)

    result = CliRunner().invoke(main.app, ["format", str(source), "--dialect", "not-a-dialect"])

    assert result.exit_code != 0
    assert "FORMAT_DIALECT_UNKNOWN" in str(result.exception)


def test_format_rejects_a_non_sql_file(monkeypatch: Any, tmp_path: Path) -> None:
    install_runner(monkeypatch, tmp_path, FormatRunner())
    source = write_sql(tmp_path, "select 1;", name="notes.txt")

    result = CliRunner().invoke(main.app, ["format", str(source)])

    assert result.exit_code != 0
    assert "FORMAT_FILE_UNSUPPORTED" in str(result.exception)


def test_format_rejects_a_file_over_the_owner_limit(monkeypatch: Any, tmp_path: Path) -> None:
    install_runner(monkeypatch, tmp_path, FormatRunner())
    source = write_sql(tmp_path, "-- " + "x" * main.FORMAT_FILE_MAX_BYTES)

    result = CliRunner().invoke(main.app, ["format", str(source)])

    assert result.exit_code != 0
    assert "FORMAT_FILE_TOO_LARGE" in str(result.exception)


def test_format_requires_pinned_sqlfluff(monkeypatch: Any, tmp_path: Path) -> None:
    class MissingSqlFluffRunner(FormatRunner):
        def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1:4] == ["-m", "sqlfluff", "version"]:
                return subprocess.CompletedProcess(command, 1, "", "No module named sqlfluff")
            return super().__call__(command)

    install_runner(monkeypatch, tmp_path, MissingSqlFluffRunner())
    source = write_sql(tmp_path)

    result = CliRunner().invoke(main.app, ["format", str(source)])

    assert result.exit_code != 0
    assert "sqlctx sqlfluff ensure" in str(result.exception)


def test_format_does_not_reach_the_database_or_mcp_surface() -> None:
    from sqlctx.server.mcp.server import McpToolRouter

    source = Path(main.__file__).read_text(encoding="utf-8")
    command = source[
        source.index('@app.command("format")') : source.index('@profile_app.command("configure")')
    ]
    assert "ServiceFacade" not in command
    assert "resolve_query_profile" not in command
    assert not hasattr(McpToolRouter, "sqlctx_format")


def test_tooling_descriptor_is_resolved_once_per_run(monkeypatch: Any, tmp_path: Path) -> None:
    runner = FormatRunner()
    install_runner(monkeypatch, tmp_path, runner)
    source = write_sql(tmp_path)

    assert CliRunner().invoke(main.app, ["format", str(source)]).exit_code == 0

    version_probes = sum(
        command[1:4] == ["-m", "sqlfluff", "version"] for command in runner.commands
    )
    assert version_probes == 1
    assert isinstance(
        SqlFluffManager(
            JsonRuntimeStateStore(tmp_path / "runtime"),
            python_executable=Path(sys.executable),
            runner=runner,
        ).status(),
        HostPythonToolingDescriptor,
    )
