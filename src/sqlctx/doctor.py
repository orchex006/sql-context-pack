"""Read-only version/readiness inspection; installation belongs to explicit CLI update."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from sqlctx._version import __version__
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.security.transport import agent_connection

REPOSITORY = "orchex006/sql-context-pack"


# Antigravity discovers skills and MCP servers by file placement; it exposes no plugin or
# extension listing, so its installed version comes from the staged manifest instead.
_FILE_BASED_HOSTS = {"agy"}
_HOSTS = {"codex", "claude", "gemini", "agy"}


def _native_plugin_version(host: str) -> str | None:
    if host in _FILE_BASED_HOSTS:
        return None
    executable = shutil.which(host)
    if executable is None:
        return None
    arguments = (
        [executable, "plugin", "list", "--json"]
        if host != "gemini"
        else [executable, "extensions", "list"]
    )
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, check=False, timeout=15)  # noqa: S603 - closed host CLI and read-only list arguments.
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        items = payload.get("installed", []) if isinstance(payload, dict) else payload
        matches = [
            item
            for item in items
            if isinstance(item, dict)
            and (
                item.get("name") == "sql-context-pack"
                or str(item.get("pluginId", "")).startswith("sql-context-pack@")
            )
            and item.get("enabled", True)
        ]
        if len(matches) == 1:
            version = matches[0].get("version")
            return version if isinstance(version, str) else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    return None


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _skill_md_version(path: Path) -> str | None:
    """Read metadata.version from SKILL.md frontmatter without a YAML dependency."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    frontmatter = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    match = re.search(r"^\s*version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*$", frontmatter, re.MULTILINE)
    return match.group(1) if match else None


def source_version(source: Path) -> str | None:
    value = _json(source / ".codex-plugin/plugin.json").get("version")
    return value if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value) else None


def inspect_installation(
    *, source: Path | None = None, host: str = "codex", online: bool = False
) -> dict[str, Any]:
    """Inspect only safe metadata. No facade construction, writes or database connections."""
    if host not in _HOSTS:
        raise ValueError("Unsupported host")
    root = Path.home()
    installed = root / {
        "gemini": ".gemini/extensions/sql-context-pack",
        "agy": ".gemini/config/skills/sql-context-pack",
    }.get(host, "plugins/sql-context-pack")
    manifest = {
        "codex": ".codex-plugin/plugin.json",
        "claude": ".claude-plugin/plugin.json",
        "gemini": "gemini-extension.json",
        "agy": ".agents/plugin.json",
    }[host]
    staged_plugin_version: object = _json(installed / manifest).get("version")
    if host in _FILE_BASED_HOSTS and not isinstance(staged_plugin_version, str):
        # Antigravity installs the skill layout, which has no manifest; the installed
        # SKILL.md frontmatter is the authoritative version there.
        staged_plugin_version = _skill_md_version(installed / "SKILL.md")
    plugin_version = _native_plugin_version(host)
    if host in _FILE_BASED_HOSTS:
        # There is no native listing to cross-check, so the staged version is the
        # installed version rather than an unverified hint.
        plugin_version = staged_plugin_version if isinstance(staged_plugin_version, str) else None
    provenance = _json(installed / ".sqlctx-install.json")
    if source is None and isinstance(provenance.get("source_root"), str):
        source = Path(provenance["source_root"])
    available = source_version(source) if source is not None else None
    findings: list[dict[str, str]] = []
    release_version = None
    if online:
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                response = client.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
                response.raise_for_status()
                tag = response.json().get("tag_name", "")
                if re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
                    release_version = tag.removeprefix("v")
        except (httpx.HTTPError, ValueError, AttributeError):
            findings.append(
                {
                    "code": "RELEASE_CHECK_UNAVAILABLE",
                    "action": "Retry doctor check --online; no installation was changed.",
                }
            )
    state = JsonRuntimeStateStore()
    service_version = None
    try:
        metadata = state.read_json("connection-metadata.json")
        if metadata is None:
            findings.append(
                {
                    "code": "SERVICE_NOT_CONFIGURED",
                    "action": "Install or start the managed service.",
                }
            )
        else:
            base, token = agent_connection(metadata)
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as client:
                response = client.get(
                    base + "/api/v1/health", headers={"Authorization": f"Bearer {token}"}
                )
                response.raise_for_status()
                health = response.json()
                if health.get("status") == "ok" and isinstance(health.get("version"), str):
                    service_version = health["version"]
                else:
                    findings.append(
                        {
                            "code": "SERVICE_UNHEALTHY",
                            "action": "Run sqlctx repair --component service.",
                        }
                    )
    except Exception:  # noqa: BLE001 - never include credentials, URLs or HTTP exception details.
        findings.append(
            {
                "code": "SERVICE_UNAVAILABLE",
                "action": "Check local metadata and run sqlctx repair --component service.",
            }
        )
    versions = {
        "package": __version__,
        "plugin": plugin_version,
        "staged_plugin": staged_plugin_version,
        "service": service_version,
        "source": available,
        "release": release_version,
    }
    for surface in ("plugin", "service"):
        value = versions[surface]
        if value is not None and value != __version__:
            findings.append(
                {"code": "VERSION_DRIFT", "action": f"Update or repair {surface} to {__version__}."}
            )
    if plugin_version is None and host not in _FILE_BASED_HOSTS:
        findings.append(
            {
                "code": "PLUGIN_VERSION_UNVERIFIED",
                "action": "Check the selected host's native plugin listing; cache-only installs may not have local staging metadata.",
            }
        )
    candidates = [v for v in (available, release_version) if isinstance(v, str)]
    update_available = any(
        tuple(map(int, v.split("."))) > tuple(map(int, __version__.split("."))) for v in candidates
    )
    return {
        "status": "attention" if findings or update_available else "ready",
        "host": host,
        "platform": platform.system(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "versions": versions,
        "update_available": update_available,
        "findings": findings,
        "update_command": f"sqlctx doctor update --host {host}",
    }
