from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/global_install.py"


def run_installer(
    home: Path, operation: str, mode: str = "plugin", harness: str = "codex"
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        str(INSTALLER),
        operation,
        "--mode",
        mode,
        "--harness",
        harness,
        "--source-root",
        str(ROOT),
        "--home",
        str(home),
        "--skip-register",
    ]
    if operation == "remove":
        arguments.append("--yes")
    return subprocess.run(  # noqa: S603 - fixed test script and controlled temporary arguments
        arguments, capture_output=True, text=True, check=False, timeout=30
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout.strip().splitlines()[-1])


def seed_marketplace(home: Path) -> Path:
    path = home / ".agents/plugins/marketplace.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "name": "personal",
                "interface": {"displayName": "Owner Marketplace"},
                "plugins": [
                    {
                        "name": "existing-plugin",
                        "source": {"source": "local", "path": "./plugins/existing-plugin"},
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_USE",
                        },
                        "category": "Productivity",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_plugin_install_is_idempotent_and_preserves_marketplace(tmp_path: Path) -> None:
    marketplace_path = seed_marketplace(tmp_path)
    first = run_installer(tmp_path, "install")
    assert first.returncode == 0, first.stdout + first.stderr
    assert payload(first)["changed"] is True
    installed = tmp_path / "plugins/sql-context-pack"
    assert (installed / ".codex-plugin/plugin.json").is_file()
    assert (installed / ".mcp.json").is_file()
    assert (installed / "hooks/hooks.json").is_file()
    manifest = json.loads((installed / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "hooks" not in manifest
    assert not (tmp_path / ".codex/skills/sql-context-pack").exists()

    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    assert marketplace["interface"]["displayName"] == "Owner Marketplace"
    assert [item["name"] for item in marketplace["plugins"]] == [
        "existing-plugin",
        "sql-context-pack",
    ]
    entry = marketplace["plugins"][1]
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}

    second = run_installer(tmp_path, "install")
    assert second.returncode == 0
    assert payload(second)["changed"] is False


def test_content_drift_requires_explicit_update(tmp_path: Path) -> None:
    assert run_installer(tmp_path, "install").returncode == 0
    skill = tmp_path / "plugins/sql-context-pack/skills/sql-context-pack/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nlocal drift\n", encoding="utf-8")

    rejected = run_installer(tmp_path, "install")
    assert rejected.returncode == 1
    assert payload(rejected)["code"] == "SAME_VERSION_CONTENT_DRIFT"

    updated = run_installer(tmp_path, "update")
    assert updated.returncode == 0
    assert payload(updated)["changed"] is True
    assert "local drift" not in skill.read_text(encoding="utf-8")


def test_plugin_and_direct_skill_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    marketplace_path = seed_marketplace(tmp_path)
    assert run_installer(tmp_path, "install", "plugin").returncode == 0
    conflict = run_installer(tmp_path, "install", "skill")
    assert conflict.returncode == 1
    assert payload(conflict)["code"] == "DUPLICATE_DISCOVERY_MODE"

    removed = run_installer(tmp_path, "remove", "plugin")
    assert removed.returncode == 0
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    assert [item["name"] for item in marketplace["plugins"]] == ["existing-plugin"]

    fallback = run_installer(tmp_path, "install", "skill")
    assert fallback.returncode == 0
    assert (tmp_path / ".codex/skills/sql-context-pack/SKILL.md").is_file()
    reverse_conflict = run_installer(tmp_path, "install", "plugin")
    assert reverse_conflict.returncode == 1
    assert payload(reverse_conflict)["code"] == "DUPLICATE_DISCOVERY_MODE"


def test_the_deprecated_codex_register_flag_still_works(tmp_path: Path) -> None:
    """Owners with the old flag in a script must not break on upgrade."""
    result = subprocess.run(  # noqa: S603 - fixed test script and controlled temporary arguments
        [
            sys.executable,
            str(INSTALLER),
            "install",
            "--source-root",
            str(ROOT),
            "--home",
            str(tmp_path),
            "--skip-codex-register",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert payload(result)["harness"] == "codex"


@pytest.mark.parametrize(
    ("harness", "manifest", "relative"),
    [
        ("codex", ".codex-plugin/plugin.json", "plugins/sql-context-pack"),
        ("claude", ".claude-plugin/plugin.json", "plugins/sql-context-pack"),
        ("gemini", "gemini-extension.json", ".gemini/extensions/sql-context-pack"),
    ],
)
def test_each_harness_installs_its_own_manifest_and_the_same_canonical_skill(
    tmp_path: Path, harness: str, manifest: str, relative: str
) -> None:
    result = run_installer(tmp_path, "install", harness=harness)

    assert result.returncode == 0, result.stdout + result.stderr
    body = payload(result)
    assert body["harness"] == harness
    installed = tmp_path / relative
    assert (installed / manifest).is_file()
    assert (installed / ".mcp.json").is_file()
    canonical = (ROOT / "skills/sql-context-pack/SKILL.md").read_bytes()
    assert (installed / "skills/sql-context-pack/SKILL.md").read_bytes() == canonical


def test_only_the_requested_harness_manifest_is_installed(tmp_path: Path) -> None:
    assert run_installer(tmp_path, "install", harness="claude").returncode == 0

    installed = tmp_path / "plugins/sql-context-pack"
    assert (installed / ".claude-plugin/plugin.json").is_file()
    assert not (installed / ".codex-plugin").exists()
    assert not (installed / "gemini-extension.json").exists()


def test_claude_skill_mode_uses_the_claude_home(tmp_path: Path) -> None:
    assert run_installer(tmp_path, "install", "skill", harness="claude").returncode == 0

    assert (tmp_path / ".claude/skills/sql-context-pack/SKILL.md").is_file()
    assert not (tmp_path / ".codex/skills/sql-context-pack").exists()


def test_gemini_refuses_skill_mode_instead_of_writing_an_unloadable_extension(
    tmp_path: Path,
) -> None:
    result = run_installer(tmp_path, "install", "skill", harness="gemini")

    assert result.returncode == 1
    assert payload(result)["code"] == "SKILL_MODE_UNSUPPORTED"
    assert not (tmp_path / ".gemini/extensions/sql-context-pack").exists()


def test_gemini_install_is_idempotent_without_a_marketplace(tmp_path: Path) -> None:
    first = run_installer(tmp_path, "install", harness="gemini")
    assert first.returncode == 0
    assert payload(first)["changed"] is True
    assert payload(first)["marketplace_changed"] is False

    second = run_installer(tmp_path, "install", harness="gemini")
    assert second.returncode == 0
    assert payload(second)["changed"] is False
    assert not (tmp_path / ".agents/plugins/marketplace.json").exists()


def test_removal_is_scoped_to_the_requested_harness(tmp_path: Path) -> None:
    assert run_installer(tmp_path, "install", harness="gemini").returncode == 0
    assert run_installer(tmp_path, "install", harness="claude").returncode == 0

    removed = run_installer(tmp_path, "remove", harness="gemini")

    assert removed.returncode == 0
    assert not (tmp_path / ".gemini/extensions/sql-context-pack").exists()
    assert (tmp_path / "plugins/sql-context-pack/.claude-plugin/plugin.json").is_file()
