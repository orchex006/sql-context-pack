import subprocess
import sys
from pathlib import Path

from sqlctx.core.enums import FormatStatus
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore


class FormatterRunner:
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
            path = Path(command[-1])
            path.write_text("SELECT\n    1;\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")


def make_formatter(tmp_path: Path, runner: FormatterRunner) -> SqlFluffFormatter:
    manager = SqlFluffManager(
        JsonRuntimeStateStore(tmp_path / "runtime"),
        python_executable=Path(sys.executable),
        runner=runner,
    )
    return SqlFluffFormatter(manager, runner=runner)


def test_parse_failure_preserves_cleaned_original(tmp_path: Path) -> None:
    runner = FormatterRunner(parse_fails=True)
    result = make_formatter(tmp_path, runner).format_one(
        object_id="procedure:public.p", sql="BROKEN SQL", dialect="postgres"
    )
    assert result.status == FormatStatus.PARSE_FAILED
    assert result.content == "BROKEN SQL"
    assert all(command[0] == str(Path(sys.executable).resolve()) for command in runner.commands)


def test_good_file_is_formatted_without_project_temp(tmp_path: Path) -> None:
    runner = FormatterRunner()
    result = make_formatter(tmp_path, runner).format_one(
        object_id="table:public.t", sql="select 1;", dialect="postgres"
    )
    assert result.status == FormatStatus.FORMATTED
    assert result.content == "SELECT\n    1;\n"
    assert not list(tmp_path.glob(".tmp-*"))


def test_unchanged_content_reuses_format_cache_without_subprocesses(tmp_path: Path) -> None:
    runner = FormatterRunner()
    formatter = make_formatter(tmp_path, runner)
    first = formatter.format_one(object_id="table:public.one", sql="select 1;", dialect="postgres")
    sqlfluff_work_count = sum(
        command[1:4] in (["-m", "sqlfluff", "parse"], ["-m", "sqlfluff", "format"])
        for command in runner.commands
    )

    second = formatter.format_one(object_id="table:public.two", sql="select 1;", dialect="postgres")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.object_id == "table:public.two"
    assert (
        sum(
            command[1:4] in (["-m", "sqlfluff", "parse"], ["-m", "sqlfluff", "format"])
            for command in runner.commands
        )
        == sqlfluff_work_count
    )
