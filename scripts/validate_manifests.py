"""Portable release checks for shared Skill and harness manifest references.

The version is derived from ``src/sqlctx/_version.py`` rather than restated here. A
hardcoded constant in this file silently went stale at 1.3.0 while every shipped surface
moved to 1.6.0, which is exactly the drift this script exists to catch.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "sql-context-pack"


def _product_version() -> str:
    text = (ROOT / "src/sqlctx/_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', text, re.MULTILINE)
    if match is None:
        raise AssertionError("src/sqlctx/_version.py has no parsable __version__")
    return match.group(1)


def _json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _skill_frontmatter() -> dict[str, Any]:
    text = (ROOT / f"skills/{PLUGIN_NAME}/SKILL.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def main() -> int:
    version = _product_version()
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, found {actual!r}")

    # Every surface an owner can install from must agree on one product version.
    versioned = {
        "codex plugin": _json(".codex-plugin/plugin.json"),
        "claude plugin": _json(".claude-plugin/plugin.json"),
        "gemini extension": _json("gemini-extension.json"),
        "agy plugin": _json(".agents/plugin.json"),
    }
    for label, manifest in versioned.items():
        check(f"{label} name", manifest.get("name"), PLUGIN_NAME)
        check(f"{label} version", manifest.get("version"), version)

    claude_marketplace = _json(".claude-plugin/marketplace.json")["plugins"][0]
    check("claude marketplace version", claude_marketplace.get("version"), version)

    agy_marketplace = _json(".agents/plugins/marketplace.json")["plugins"][0]
    check("agy marketplace version", agy_marketplace.get("version"), version)
    check(
        "agy marketplace source",
        agy_marketplace["source"]["url"],
        "https://github.com/orchex006/sql-context-pack.git",
    )

    # The build version is dynamic from sqlctx._version, so only the restated metadata
    # block can drift from it.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check(
        "pyproject dynamic version source",
        'version = { attr = "sqlctx._version.__version__" }' in pyproject,
        True,
    )
    product = re.search(r'^product-version\s*=\s*"(\d+\.\d+\.\d+)"', pyproject, re.MULTILINE)
    check("pyproject product-version", product and product.group(1), version)

    skill = _skill_frontmatter()
    check("skill name", skill.get("name"), PLUGIN_NAME)
    check("skill version", skill.get("metadata", {}).get("version"), version)

    # Discovery wiring, not just version strings.
    check(
        "gemini contextFileName",
        versioned["gemini extension"].get("contextFileName"),
        f"skills/{PLUGIN_NAME}/SKILL.md",
    )
    check("agy skills pointer", versioned["agy plugin"].get("skills"), "./skills/")
    check("codex skills pointer", versioned["codex plugin"].get("skills"), "./skills/")
    check("claude skills pointer", versioned["claude plugin"].get("skills"), "./skills/")

    # Antigravity reads one shared MCP config; the bridge entry must be declared for it.
    agy_mcp = _json(".agents/mcp_config.json").get("mcpServers", {})
    check("agy mcp command", agy_mcp.get(PLUGIN_NAME, {}).get("command"), "sqlctx-mcp-bridge")

    skills = list(ROOT.rglob("SKILL.md"))
    check("canonical SKILL.md count", len(skills), 1)

    if failures:
        print(f"Manifest drift against product version {version}:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"Harness manifests and canonical Skill are consistent at {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
