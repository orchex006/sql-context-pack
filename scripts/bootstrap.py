"""Cross-platform first-use bootstrap for SQL Context Pack."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def _host_os() -> str:
    name = platform.system().lower()
    if name == "windows":
        return "windows"
    if name == "darwin":
        return "macos"
    if name == "linux":
        return "linux"
    return "unix"


def _run(command: list[str]) -> None:
    result = subprocess.run(command, check=False)  # noqa: S603 - closed bootstrap commands.
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _user_scripts() -> Path:
    import sysconfig

    scheme = "nt_user" if _host_os() == "windows" else "posix_user"
    return Path(sysconfig.get_path("scripts", scheme=scheme)).resolve()


def _verify_launchers(scripts: Path) -> None:
    suffix = ".exe" if _host_os() == "windows" else ""
    missing = [
        name
        for name in ("sqlctx", "sqlctx-server", "sqlctx-mcp-bridge")
        if not (scripts / f"{name}{suffix}").exists()
    ]
    if missing:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "code": "LAUNCHER_MISSING",
                    "missing": missing,
                    "scripts": str(scripts),
                },
                sort_keys=True,
            )
        )


def _windows_bootstrap(root: Path, repair: bool) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise SystemExit("PowerShell is required for Windows managed setup.")
    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(root / "scripts/bootstrap.ps1"),
    ]
    if repair:
        command.append("-Repair")
    _run(command)


def _posix_bootstrap(root: Path, operation: str) -> None:
    target = str(root)
    _run([sys.executable, "-m", "pip", "install", "--user", "--upgrade", target])
    scripts = _user_scripts()
    _verify_launchers(scripts)
    service_manager = root / "scripts/service-manager.py"
    _run([sys.executable, str(service_manager), operation])
    print(
        json.dumps(
            {
                "ok": True,
                "operation": operation,
                "managed_runtime": True,
                "host_os": _host_os(),
                "new_room_required": True,
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        choices=["install", "update", "remove"],
        default="install",
    )
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.source_root.resolve()
    if _host_os() == "windows":
        if args.operation == "remove":
            raise SystemExit(
                "Windows removal is not available through bootstrap.py; "
                "use $sql-context-pack uninstall so the current harness is selected explicitly."
            )
        _windows_bootstrap(root, repair=args.repair or args.operation == "update")
    else:
        operation = "update" if args.repair else args.operation
        _posix_bootstrap(root, operation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
