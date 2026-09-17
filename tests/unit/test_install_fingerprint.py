from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "install_fingerprint", ROOT / "scripts/install_fingerprint.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_install_fingerprints_are_deterministic_and_layered(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "src/sqlctx").mkdir(parents=True)
    (source / "skills/sql-context-pack").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / ".codex-plugin").mkdir()
    (source / "hooks").mkdir()
    (source / "pyproject.toml").write_text(
        """[project]
dependencies = ["base==1"]
[project.optional-dependencies]
sqlserver = ["driver==1"]
""",
        encoding="utf-8",
    )
    (source / "src/sqlctx/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "skills/sql-context-pack/SKILL.md").write_text("skill\n", encoding="utf-8")
    (source / ".codex-plugin/plugin.json").write_text("{}\n", encoding="utf-8")
    (source / "scripts/sqlctx_windows_service.py").write_text("host\n", encoding="utf-8")
    (source / "scripts/windows-service.ps1").write_text("service\n", encoding="utf-8")

    first = MODULE.compute(source, ["sqlserver"])
    assert first == MODULE.compute(source, ["sqlserver"])
    no_extra = MODULE.compute(source, [])
    assert first["app_fingerprint"] == no_extra["app_fingerprint"]
    assert first["dependency_fingerprint"] != no_extra["dependency_fingerprint"]
    assert first["package_fingerprint"]

    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    matching = MODULE.compute(source, ["sqlserver"], installed)
    assert matching["installed_package_fingerprint"] == matching["package_fingerprint"]
    (installed / "app.py").write_text("CORRUPT = True\n", encoding="utf-8")
    corrupt = MODULE.compute(source, ["sqlserver"], installed)
    assert corrupt["installed_package_fingerprint"] != corrupt["package_fingerprint"]

    (source / "skills/sql-context-pack/SKILL.md").write_text("changed\n", encoding="utf-8")
    plugin_changed = MODULE.compute(source, ["sqlserver"])
    assert plugin_changed["plugin_fingerprint"] != first["plugin_fingerprint"]
    assert plugin_changed["app_fingerprint"] == first["app_fingerprint"]

    json.dumps(plugin_changed)


def test_native_marketplace_and_extension_manifests_use_canonical_name() -> None:
    codex = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    claude = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    gemini = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))

    assert codex["name"] == claude["name"] == "sql-context-pack"
    assert codex["plugins"][0]["name"] == claude["plugins"][0]["name"]
    assert gemini["name"] == "sql-context-pack"
    assert gemini["mcpServers"]["sql-context-pack"]["command"] == "sqlctx-mcp-bridge"
