"""Cross-platform owner service manager for SQL Context Pack."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Literal

HostOS = Literal["windows", "macos", "linux", "unix"]
Operation = Literal["install", "update", "status", "remove", "start", "stop"]


def detect_host_os(system_name: str | None = None) -> HostOS:
    name = (system_name or platform.system()).strip().lower()
    if name == "windows":
        return "windows"
    if name == "darwin":
        return "macos"
    if name == "linux":
        return "linux"
    return "unix"


def _state_home(host_os: HostOS) -> Path:
    configured = os.environ.get("SQLCTX_SERVICE_STATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if host_os == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif host_os == "macos":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return (base / "sql-context-pack").resolve()


def _runtime_home(host_os: HostOS) -> Path:
    configured = os.environ.get("SQLCTX_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if host_os == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif host_os == "macos":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".local/state"))
    return (base / "sql-context-pack").resolve()


def _server_command(python: Path, port: int) -> list[str]:
    return [
        str(python),
        "-m",
        "sqlctx.server.http.app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _health(port: int, host_os: HostOS) -> bool:
    metadata = _runtime_home(host_os) / "connection-metadata.json"
    if not metadata.is_file():
        return False
    try:
        token = str(json.loads(metadata.read_text(encoding="utf-8"))["agent_token"])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=2) as response:
            payload = json.loads(response.read().decode())
        return bool(payload.get("status") == "ok")
    except Exception:
        return False


def _wait_health(port: int, host_os: HostOS, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _health(port, host_os):
            return True
        time.sleep(0.5)
    return False


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    """Ask the process to stop, using whatever the platform actually supports."""
    if os.name == "nt":
        taskkill = _which("taskkill")
        if taskkill is None:
            return
        subprocess.run(  # noqa: S603 - resolved taskkill with fixed arguments for one owned pid.
            [taskkill, "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        return
    os.kill(pid, signal.SIGTERM)


def _windows_service_present() -> bool:
    """True when the SCM-registered service exists, so we must not start a second copy."""
    sc = _which("sc")
    if sc is None:
        return False
    result = subprocess.run(  # noqa: S603 - fixed sc query for one known service name.
        [sc, "query", "SQLContextPack"], check=False, capture_output=True, text=True
    )
    return result.returncode == 0


def _generic_paths(host_os: HostOS) -> tuple[Path, Path]:
    root = _state_home(host_os)
    return root / "service.pid", root / "service.log"


def _generic_start(python: Path, port: int, host_os: HostOS) -> dict[str, object]:
    pid_path, log_path = _generic_paths(host_os)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_running(pid) and _health(port, host_os):
                return {"installed": True, "status": "running", "pid": pid, "mode": "generic"}
        except ValueError:
            pass
    log = log_path.open("ab")
    command = _server_command(python, port)
    if os.name == "nt":
        # start_new_session is POSIX-only; Windows detaches via creation flags instead.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        process = subprocess.Popen(  # noqa: S603 - closed command from selected Python/module.
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    else:
        process = subprocess.Popen(  # noqa: S603 - closed command from selected Python/module.
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    ready = _wait_health(port, host_os)
    return {
        "installed": True,
        "status": "running" if ready else "starting",
        "pid": process.pid,
        "mode": "generic",
        "health_verified": ready,
        "log": str(log_path),
    }


def _generic_stop(host_os: HostOS) -> dict[str, object]:
    pid_path, _ = _generic_paths(host_os)
    if not pid_path.is_file():
        return {"installed": False, "status": "not_installed", "mode": "generic"}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        return {"installed": False, "status": "stale", "mode": "generic"}
    if _is_running(pid):
        _terminate(pid)
        for _ in range(20):
            if not _is_running(pid):
                break
            time.sleep(0.25)
    pid_path.unlink(missing_ok=True)
    return {"installed": False, "status": "stopped", "pid": pid, "mode": "generic"}


def _systemd_unit(python: Path, port: int) -> str:
    command = " ".join(_server_command(python, port))
    return f"""[Unit]
Description=SQL Context Pack loopback service

[Service]
ExecStart={command}
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def _linux_install(python: Path, port: int) -> dict[str, object]:
    systemctl = _which("systemctl")
    if systemctl is None:
        return _generic_start(python, port, "linux")
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "sql-context-pack.service"
    unit.write_text(_systemd_unit(python, port), encoding="utf-8")
    for args in (
        [systemctl, "--user", "daemon-reload"],
        [systemctl, "--user", "enable", "--now", "sql-context-pack.service"],
    ):
        subprocess.run(args, check=True)  # noqa: S603 - resolved systemctl with fixed arguments.
    return {
        "installed": True,
        "status": "running" if _wait_health(port, "linux") else "starting",
        "mode": "systemd-user",
        "unit": str(unit),
        "health_verified": _health(port, "linux"),
    }


def _linux_remove() -> dict[str, object]:
    systemctl = _which("systemctl")
    unit = Path.home() / ".config/systemd/user/sql-context-pack.service"
    if systemctl and unit.exists():
        subprocess.run(  # noqa: S603 - resolved systemctl with fixed arguments.
            [systemctl, "--user", "disable", "--now", "sql-context-pack.service"], check=False
        )
        unit.unlink(missing_ok=True)
        subprocess.run(  # noqa: S603 - resolved systemctl with fixed arguments.
            [systemctl, "--user", "daemon-reload"], check=False
        )
        return {"installed": False, "status": "removed", "mode": "systemd-user"}
    return _generic_stop("linux")


def _launchd_plist(python: Path, port: int, log: Path) -> str:
    args = "\n".join(f"    <string>{item}</string>" for item in _server_command(python, port))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sql-context-pack.service</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _macos_install(python: Path, port: int) -> dict[str, object]:
    launchctl = _which("launchctl")
    if launchctl is None:
        return _generic_start(python, port, "macos")
    root = _state_home("macos")
    root.mkdir(parents=True, exist_ok=True)
    plist_dir = Path.home() / "Library/LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / "com.sql-context-pack.service.plist"
    log = root / "service.log"
    plist.write_text(_launchd_plist(python, port, log), encoding="utf-8")
    subprocess.run(  # noqa: S603 - resolved launchctl with fixed arguments.
        [launchctl, "unload", str(plist)], check=False
    )
    subprocess.run(  # noqa: S603 - resolved launchctl with fixed arguments.
        [launchctl, "load", str(plist)], check=True
    )
    return {
        "installed": True,
        "status": "running" if _wait_health(port, "macos") else "starting",
        "mode": "launchd-user",
        "plist": str(plist),
        "health_verified": _health(port, "macos"),
    }


def _macos_remove() -> dict[str, object]:
    launchctl = _which("launchctl")
    plist = Path.home() / "Library/LaunchAgents/com.sql-context-pack.service.plist"
    if launchctl and plist.exists():
        subprocess.run(  # noqa: S603 - resolved launchctl with fixed arguments.
            [launchctl, "unload", str(plist)], check=False
        )
        plist.unlink(missing_ok=True)
        return {"installed": False, "status": "removed", "mode": "launchd-user"}
    return _generic_stop("macos")


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def manage(operation: Operation, *, python: Path, port: int, host_os: HostOS) -> dict[str, object]:
    if host_os == "windows":
        # A registered SCM service owns the lifecycle; starting a pidfile copy beside it
        # would mean two servers fighting over the same loopback port.
        if _windows_service_present():
            return {
                "supported": True,
                "mode": "windows-service",
                "status": "running" if _health(port, "windows") else "not_running",
                "installed": True,
                "health_verified": _health(port, "windows"),
                "owner_action": (
                    "The SCM service owns this host; use install.ps1 or "
                    "scripts/windows-service.ps1 to install, update or remove it."
                ),
            }
        # No SCM service: the portable supervisor works on Windows too, so an owner
        # without Administrator rights still gets one shared service.
        if operation in {"install", "update", "start"}:
            return _generic_start(python, port, "windows")
        if operation in {"remove", "stop"}:
            return _generic_stop("windows")
        running = _health(port, "windows")
        return {
            "installed": running,
            "status": "running" if running else "not_running",
            "mode": "generic",
        }
    if operation in {"install", "update", "start"}:
        if host_os == "linux":
            return _linux_install(python, port)
        if host_os == "macos":
            return _macos_install(python, port)
        return _generic_start(python, port, "unix")
    if operation in {"remove", "stop"}:
        if host_os == "linux":
            return _linux_remove()
        if host_os == "macos":
            return _macos_remove()
        return _generic_stop("unix")
    if operation == "status":
        running = _health(port, host_os)
        return {"installed": running, "status": "running" if running else "not_running"}
    raise AssertionError(operation)
