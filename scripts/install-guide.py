"""Detect the host OS and print the supported SQL Context Pack install path."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Sequence
from typing import Literal

HostOS = Literal["windows", "macos", "linux", "unix"]


def detect_host_os(system_name: str | None = None) -> HostOS:
    name = (system_name or platform.system()).strip().lower()
    if name == "windows":
        return "windows"
    if name == "darwin":
        return "macos"
    if name == "linux":
        return "linux"
    return "unix"


def install_guide(host_os: HostOS) -> str:
    native_harness = """1. Install the native harness plugin or extension:

   Codex:
     codex plugin marketplace add orchex006/sql-context-pack
     codex plugin add sql-context-pack@sql-context-pack

   Claude Code:
     claude plugin marketplace add orchex006/sql-context-pack
     claude plugin install sql-context-pack@sql-context-pack

   Gemini CLI:
     gemini extensions install https://github.com/orchex006/sql-context-pack"""
    skill_setup = """2. Open a new harness room/session and use only its invocation form:

   Codex:
     $sql-context-pack setup

   Claude Code:
     /sql-context-pack:sql-context-pack setup

   Gemini CLI:
     /skills list
     Use the sql-context-pack skill to run setup.

   Gemini has no repository-provided custom slash command."""
    skill_connect = """4. Open one final new room/session, then connect with the same harness form:

   Codex:
     $sql-context-pack profiles
     $sql-context-pack connect <profile-name>

   Claude Code:
     /sql-context-pack:sql-context-pack profiles
     /sql-context-pack:sql-context-pack connect <profile-name>

   Gemini CLI:
     Use the sql-context-pack skill to list profiles.
     Use the sql-context-pack skill to connect profile <profile-name>."""
    if host_os == "windows":
        return f"""Detected OS: Windows
Managed Agent runtime: supported

{native_harness}

{skill_setup}

3. Approve UAC when requested. Setup installs the owner package, verifies sqlctx/sqlctx-server/sqlctx-mcp-bridge launchers, registers the loopback SQLContextPack Windows Service, and verifies authenticated health.

{skill_connect}

Do not run sqlctx launch as an Agent fallback. It is only an explicit owner/development compatibility command."""
    label = {"macos": "macOS", "linux": "Linux", "unix": "Unix"}[host_os]
    manager = {
        "macos": "launchd user LaunchAgent",
        "linux": "systemd --user service when available, otherwise an owner background process",
        "unix": "owner background process with a pid/state file",
    }[host_os]
    return f"""Detected OS: {label}
Managed Agent runtime: supported through {manager}

{native_harness}

{skill_setup}

3. Setup installs the owner package with the selected host Python, verifies sqlctx/sqlctx-server/sqlctx-mcp-bridge launchers, registers the user service/background process, and verifies authenticated loopback health.

{skill_connect}

Managed setup is cross-platform. Native harness availability still depends on the selected harness CLI supporting this OS."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--os",
        choices=["windows", "macos", "linux", "unix"],
        help="Override OS detection for documentation/tests.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a compact machine-readable summary instead of the full guide.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    host_os = args.os or detect_host_os()
    if args.json:
        print(
            "{"
            f'"detected_os":"{host_os}",'
            '"managed_agent_runtime_supported":true,'
            '"native_harness_commands_platform_dependent":true'
            "}"
        )
    else:
        print(install_guide(host_os))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
