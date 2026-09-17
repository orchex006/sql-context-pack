import subprocess
import sys
from pathlib import Path

import pytest

from sqlctx.core.errors import ApprovalRequired, SqlCtxError, ToolingUnavailable
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore


class FakeRunner:
    def __init__(self, installed: str | None = None) -> None:
        self.installed = installed
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command[1:3] == ["-c", "import json,sys; print(json.dumps(list(sys.version_info[:3])))"]:
            return subprocess.CompletedProcess(command, 0, "[3, 11, 10]\n", "")
        if command[1:4] == ["-m", "sqlfluff", "version"]:
            if self.installed is None:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return subprocess.CompletedProcess(
                command, 0, f"sqlfluff, version {self.installed}\n", ""
            )
        if command[1:4] == ["-m", "sqlfluff", "dialects"]:
            return subprocess.CompletedProcess(command, 0, "postgres\ntsql\n", "")
        if command[1:4] == ["-m", "pip", "install"]:
            requested = next(
                item.split("==", 1)[1] for item in command if item.startswith("sqlfluff==")
            )
            self.installed = requested
            return subprocess.CompletedProcess(command, 0, "installed", "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected")


def make_manager(tmp_path: Path, runner: FakeRunner, owner: str = "host") -> SqlFluffManager:
    return SqlFluffManager(
        JsonRuntimeStateStore(tmp_path / "runtime"),
        python_executable=Path(sys.executable),
        environment_owner=owner,
        runner=runner,
    )


def test_ensure_requires_approval_and_uses_exact_host_python(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = make_manager(tmp_path, runner)
    with pytest.raises(ApprovalRequired):
        manager.ensure()
    status = manager.ensure(approved=True)
    assert status.ready
    install = next(command for command in runner.commands if "pip" in command)
    assert install[0] == str(Path(sys.executable).resolve())
    assert install[1:] == ["-m", "pip", "install", "--user", "sqlfluff==4.2.2"]
    assert manager.ensure(approved=True).ready
    assert sum("pip" in command for command in runner.commands) == 1


def test_owner_managed_environment_is_never_mutated(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = make_manager(tmp_path, runner, owner="owner")
    with pytest.raises(ToolingUnavailable) as error:
        manager.ensure(approved=True)
    assert error.value.code == "OWNER_MANAGED_PYTHON_ENVIRONMENT"
    assert not any("pip" in command for command in runner.commands)


def test_update_is_blocked_while_job_is_active(tmp_path: Path) -> None:
    runner = FakeRunner(installed="4.2.2")
    manager = make_manager(tmp_path, runner)
    with manager.pin_for_job():
        with pytest.raises(SqlCtxError) as error:
            manager.update("4.2.2", approved=True)
        assert error.value.code == "TOOLING_BUSY"
