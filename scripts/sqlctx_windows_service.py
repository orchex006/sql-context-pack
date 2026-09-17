"""Native Windows Service host for a staged SQL Context Pack application tree."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

_DLL_HANDLES: list[Any] = []


def _config() -> dict[str, Any]:
    path = Path(__file__).with_name("service-config.json")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("service-config.json must contain an object")
    return value


def _prepare_staged_imports(config: dict[str, Any]) -> None:
    """Make pip --target packages and pywin32 DLLs visible to the service interpreter."""
    app_root = Path(str(config["app_root"]))
    sys.path.insert(0, str(app_root))
    for relative in ("win32", "win32/lib", "Pythonwin"):
        candidate = app_root / relative
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
    dll_root = app_root / "pywin32_system32"
    if dll_root.is_dir() and hasattr(os, "add_dll_directory"):
        _DLL_HANDLES.append(os.add_dll_directory(str(dll_root)))


if sys.platform == "win32":
    config_path = Path(__file__).with_name("service-config.json")
    if config_path.is_file():
        _prepare_staged_imports(_config())
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    class SqlContextPackService(win32serviceutil.ServiceFramework):
        _svc_name_ = "SQLContextPack"
        _svc_display_name_ = "SQL Context Pack"
        _svc_description_ = "Owner-approved loopback HTTP/MCP database context service."

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.child: subprocess.Popen[bytes] | None = None

        def SvcStop(self) -> None:  # noqa: N802 - Windows Service callback contract.
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self) -> None:  # noqa: N802 - Windows Service callback contract.
            config = _config()
            app_root = Path(str(config["app_root"]))
            runtime_root = Path(str(config["runtime_root"]))
            runtime_root.mkdir(parents=True, exist_ok=True)
            log_path = runtime_root / "service-child.log"
            environment = os.environ.copy()
            import_paths = [
                app_root,
                app_root / "win32",
                app_root / "win32/lib",
                app_root / "Pythonwin",
            ]
            environment["PYTHONPATH"] = os.pathsep.join(
                str(path) for path in import_paths if path.is_dir()
            )
            dll_root = app_root / "pywin32_system32"
            if dll_root.is_dir():
                environment["PATH"] = str(dll_root) + os.pathsep + environment.get("PATH", "")
            environment["SQLCTX_CONFIG_DIR"] = str(config["config_root"])
            environment["SQLCTX_RUNTIME_DIR"] = str(config["runtime_root"])
            environment["SQLCTX_OWNER_ACCOUNT"] = str(config["owner_account"])
            environment["SQLCTX_SERVICE_ACCOUNT"] = "NT SERVICE\\SQLContextPack"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONNOUSERSITE"] = "1"
            try:
                with log_path.open("wb") as child_log:
                    self.child = subprocess.Popen(  # noqa: S603 - sealed absolute executable.
                        [
                            str(config["python_executable"]),
                            "-B",
                            "-s",
                            "-m",
                            "sqlctx.server.http.app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(config["port"]),
                        ],
                        cwd=str(app_root),
                        env=environment,
                        stdout=child_log,
                        stderr=subprocess.STDOUT,
                    )
                    self._wait_for_child()
            except Exception:  # noqa: BLE001 - preserve diagnostics for SCM-only failures.
                log_path.write_text(traceback.format_exc(), encoding="utf-8")
                servicemanager.LogErrorMsg(
                    "SQL Context Pack service host failed; see protected service-child.log."
                )
                raise

        def _wait_for_child(self) -> None:
            assert self.child is not None
            servicemanager.LogInfoMsg("SQL Context Pack loopback service started.")
            while self.child.poll() is None:
                if (
                    win32event.WaitForSingleObject(self.stop_event, 1000)
                    == win32event.WAIT_OBJECT_0
                ):
                    break
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.child.kill()
            if self.child.returncode not in {None, 0, -15}:
                servicemanager.LogErrorMsg(
                    f"SQL Context Pack child exited with code {self.child.returncode}."
                )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("SQLContextPack Windows Service host runs only on Windows.")
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(SqlContextPackService)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    main()
