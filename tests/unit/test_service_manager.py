"""The shared service must be manageable from an installed package on every host OS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sqlctx.service import manager


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Windows", "windows"),
        ("Darwin", "macos"),
        ("Linux", "linux"),
        ("FreeBSD", "unix"),
    ],
)
def test_host_os_detection(system: str, expected: str) -> None:
    assert manager.detect_host_os(system) == expected


def test_windows_defers_to_the_scm_service_when_one_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never start a pidfile copy beside a registered service; they would fight for the port."""
    monkeypatch.setattr(manager, "_windows_service_present", lambda: True)
    monkeypatch.setattr(manager, "_health", lambda port, host_os: True)
    started: list[Any] = []
    monkeypatch.setattr(manager, "_generic_start", lambda *a, **k: started.append(a))

    result = manager.manage("start", python=Path("python"), port=8765, host_os="windows")

    assert result["mode"] == "windows-service"
    assert result["status"] == "running"
    assert started == []


def test_windows_without_a_registered_service_uses_the_portable_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An owner without Administrator rights still gets one shared service."""
    monkeypatch.setattr(manager, "_windows_service_present", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(
        manager,
        "_generic_start",
        lambda python, port, host_os: calls.append(host_os) or {"mode": "generic"},
    )

    result = manager.manage("start", python=Path("python"), port=8765, host_os="windows")

    assert calls == ["windows"]
    assert result["mode"] == "generic"


def test_every_supported_host_os_resolves_a_state_directory() -> None:
    for host_os in ("windows", "macos", "linux", "unix"):
        root = manager._state_home(host_os)  # type: ignore[arg-type]
        assert root.name == "sql-context-pack"
        assert root.is_absolute()


def test_server_command_always_binds_loopback_only() -> None:
    command = manager._server_command(Path("python"), 8765)
    assert "--host" in command
    assert command[command.index("--host") + 1] == "127.0.0.1"
