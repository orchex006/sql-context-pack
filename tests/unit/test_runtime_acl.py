from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from sqlctx.security import runtime


def test_windows_acl_does_not_duplicate_system_grant(tmp_path: Path, monkeypatch: Any) -> None:
    captured: list[str] = []
    monkeypatch.setattr(runtime.os, "name", "nt")
    monkeypatch.setenv("USERNAME", "SYSTEM")
    monkeypatch.setenv("SQLCTX_OWNER_ACCOUNT", r"EXAMPLE\owner")
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "icacls.exe")

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    runtime._harden_windows_acl(tmp_path / "metadata.json")

    assert captured.count("SYSTEM:(F)") == 1
    assert "SYSTEM:(R,W)" not in captured
    assert r"EXAMPLE\owner:(R,W)" in captured
