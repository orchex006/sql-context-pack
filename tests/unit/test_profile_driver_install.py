from __future__ import annotations

import subprocess
from typing import Any

from sqlctx.cli import configure
from sqlctx.core.enums import DatabaseEngine


def test_profile_setup_installs_only_the_selected_pinned_driver(monkeypatch: Any) -> None:
    discoveries = iter([None, object()])
    captured: list[str] = []
    monkeypatch.setattr(configure, "find_spec", lambda _: next(discoveries))
    monkeypatch.setattr(configure.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(configure.typer, "confirm", lambda *_, **__: True)

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(configure.subprocess, "run", fake_run)

    configure._ensure_database_driver(DatabaseEngine.SQLSERVER)

    assert captured[-2:] == ["--user", "pyodbc==5.3.0"]


def test_profile_setup_does_nothing_when_driver_is_already_available(monkeypatch: Any) -> None:
    monkeypatch.setattr(configure, "find_spec", lambda _: object())
    monkeypatch.setattr(
        configure.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pip must not run")),
    )

    configure._ensure_database_driver(DatabaseEngine.POSTGRES)
