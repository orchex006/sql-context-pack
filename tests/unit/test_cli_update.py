from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.errors import SqlCtxError


@pytest.mark.parametrize(
    ("changed_argument", "bad_value", "error_code"),
    [
        ("get-url", "https://untrusted.invalid/project.git", "UPDATE_REMOTE_NOT_TRUSTED"),
        ("--show-current", "unreviewed", "UPDATE_BRANCH_NOT_TRUSTED"),
        ("rev-parse", "other/main", "UPDATE_BRANCH_NOT_TRUSTED"),
        ("status", " M install.ps1", "UPDATE_SOURCE_DIRTY"),
    ],
)
def test_update_rejects_untrusted_or_dirty_source_before_execution(
    tmp_path: Path, monkeypatch: Any, changed_argument: str, bad_value: str, error_code: str
) -> None:
    (tmp_path / ".git").mkdir()
    calls = []
    monkeypatch.setattr(main.shutil, "which", lambda _: "git.exe")

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if changed_argument in arguments:
            return subprocess.CompletedProcess(arguments, 0, bad_value, "")
        return command_result(arguments)

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    with pytest.raises(SqlCtxError) as caught:
        main._refresh_trusted_checkout(tmp_path)
    assert caught.value.code == error_code
    assert not any("pull" in call or "install.ps1" in call for call in calls)


def fixture_source(source: Path) -> None:
    (source / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (source / ".codex-plugin/plugin.json").write_text(
        json.dumps({"version": "2.1.0"}), encoding="utf-8"
    )


def command_result(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    stdout = ""
    if "get-url" in arguments:
        stdout = "https://github.com/orchex006/sql-context-pack"
    elif "--show-current" in arguments:
        stdout = "main"
    elif "rev-parse" in arguments:
        stdout = "origin/main"
    elif "doctor" in arguments:
        stdout = json.dumps({"versions": dict.fromkeys(["package", "plugin", "service"], "2.1.0")})
    return subprocess.CompletedProcess(arguments, 0, stdout, "")


def test_installed_source_root_reads_safe_plugin_provenance(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "release"
    source.mkdir()
    provenance = tmp_path / "plugins/sql-context-pack/.sqlctx-install.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(json.dumps({"source_root": str(source)}), encoding="utf-8")
    monkeypatch.setattr(main.Path, "home", lambda: tmp_path)

    assert main._installed_source_root() == source


def test_product_update_runs_validated_windows_installer(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "release"
    (source / ".git").mkdir(parents=True)
    (source / "install.ps1").write_text("# fixture", encoding="utf-8")
    fixture_source(source)
    captured: list[str] = []
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.shutil, "which", lambda _: "powershell.exe")

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(arguments)
        return command_result(arguments)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["update", "--source", str(source)])

    assert result.exit_code == 0
    assert ["powershell.exe", "-C", str(source), "pull", "--ff-only"] in [
        captured[index : index + 5] for index in range(len(captured) - 4)
    ]
    assert str(source / "install.ps1") in captured
    assert "-Update" in captured
    assert "[1/2] Refreshing trusted Git source" in result.output
    assert "[2/2] Installing refreshed" in result.output


def test_default_product_update_fast_forwards_recorded_checkout(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "release"
    (source / ".git").mkdir(parents=True)
    (source / "install.ps1").write_text("# fixture", encoding="utf-8")
    fixture_source(source)
    calls: list[list[str]] = []
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main, "_installed_source_root", lambda: source)
    monkeypatch.setattr(
        main.shutil,
        "which",
        lambda command: "git.exe" if command == "git" else "powershell.exe",
    )

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return command_result(arguments)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["update"])

    assert result.exit_code == 0
    assert ["git.exe", "-C", str(source), "pull", "--ff-only"] in calls
    assert any("install.ps1" in " ".join(call) for call in calls)


def test_product_repair_reinstalls_without_git_refresh(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "dev-checkout"
    source.mkdir()
    (source / "install.ps1").write_text("# fixture", encoding="utf-8")
    fixture_source(source)
    calls: list[list[str]] = []
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.shutil, "which", lambda _: "powershell.exe")

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return command_result(arguments)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["repair", "--source", str(source)])

    assert result.exit_code == 0
    assert "-Repair" in calls[0]
    assert "-SkipConfigure" in calls[0]
    assert "pull" not in calls[0]


def test_product_repair_can_target_mcp_runtime(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "dev-checkout"
    source.mkdir()
    (source / "install.ps1").write_text("# fixture", encoding="utf-8")
    fixture_source(source)
    calls: list[list[str]] = []
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda arguments, **_: calls.append(arguments) or subprocess.CompletedProcess(arguments, 0),
    )

    result = CliRunner().invoke(
        main.app,
        ["repair", "--source", str(source), "--component", "mcp"],
    )

    assert result.exit_code == 0
    component_index = calls[0].index("-RepairComponent")
    assert calls[0][component_index + 1] == "mcp"
