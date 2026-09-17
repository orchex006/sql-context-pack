from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_required_documentation_exists_and_local_links_resolve() -> None:
    required = [
        "README.md",
        "docs/README.md",
        "docs/getting-started.md",
        "docs/lifecycle.md",
        "docs/command-reference.md",
        "docs/usage-examples.md",
        "docs/api-and-mcp.md",
        "docs/output-format.md",
        "docs/architecture.md",
        "docs/security.md",
        "docs/troubleshooting.md",
        "docs/development.md",
        "docs/requirements.md",
        "docs/implementation-state.md",
        "docs/versioning.md",
        "docs/providers/codex.md",
        "docs/providers/claude-code.md",
        "docs/providers/gemini-cli.md",
        "docs/providers/antigravity.md",
        "docs/generated/openapi.json",
        "docs/generated/mcp-tools.json",
        "docs/generated/mcp-bridge-tools.json",
        "CHANGELOG.md",
    ]
    assert all((ROOT / path).is_file() for path in required)
    for markdown in [ROOT / path for path in required if path.endswith(".md")]:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", markdown.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (markdown.parent / path).resolve().exists(), (
                f"broken link in {markdown}: {target}"
            )


def test_getting_started_covers_complete_capture_and_safe_write_boundaries() -> None:
    guide = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")
    for required in (
        "selection.mode=all",
        "TABLE",
        "PROCEDURE",
        "FUNCTION",
        "unknowns/",
        "CREATE OR ALTER PROCEDURE",
        "metadata_context_write",
        "routine_write",
    ):
        assert required in guide


def test_lifecycle_and_provider_guides_cover_install_update_uninstall() -> None:
    guide = (ROOT / "docs/lifecycle.md").read_text(encoding="utf-8")
    for command in (
        r".\install.ps1",
        "./install.sh",
        "sqlctx doctor update --host",
        r".\install.ps1 -Repair",
        "./install.sh --repair",
        r".\scripts\lifecycle.ps1 -Operation uninstall",
        "sqlctx service remove",
        "$sql-context-pack setup",
        "/sql-context-pack:sql-context-pack setup",
        "/skills list",
        "Use the sql-context-pack skill to run setup.",
    ):
        assert command in guide, command
    providers = {
        "codex.md": (
            "codex plugin marketplace add orchex006/sql-context-pack",
            "$sql-context-pack setup",
            "$sql-context-pack connect <profile-name>",
        ),
        "claude-code.md": (
            "claude plugin marketplace add orchex006/sql-context-pack",
            "/sql-context-pack:sql-context-pack setup",
            "/sql-context-pack:sql-context-pack connect <profile-name>",
        ),
        "gemini-cli.md": (
            "gemini extensions install https://github.com/orchex006/sql-context-pack",
            "/skills list",
            "Use the sql-context-pack skill to run setup.",
            "Use the sql-context-pack skill to connect profile <profile-name>.",
        ),
        "antigravity.md": (
            "sqlctx doctor update --host agy",
            "~/.gemini/config/skills/sql-context-pack/",
            "~/.gemini/config/mcp_config.json",
            "sqlctx-mcp-bridge",
        ),
    }
    for name, required_commands in providers.items():
        content = (ROOT / "docs/providers" / name).read_text(encoding="utf-8")
        assert all(command in content for command in required_commands), name
        assert "## Uninstall" in content, name
    assert "$sql-context-pack setup" not in (ROOT / "docs/providers/claude-code.md").read_text(
        encoding="utf-8"
    )
    for name in ("gemini-cli.md", "antigravity.md"):
        assert "$sql-context-pack setup" not in (ROOT / "docs/providers" / name).read_text(
            encoding="utf-8"
        ), name


def test_getting_started_uses_provider_specific_skill_invocation() -> None:
    guide = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")
    for invocation in (
        "$sql-context-pack profiles",
        "/sql-context-pack:sql-context-pack profiles",
        "/skills list",
        "Use the sql-context-pack skill to list profiles.",
    ):
        assert invocation in guide


def test_usage_guide_has_exactly_three_progressive_examples() -> None:
    guide = (ROOT / "docs/usage-examples.md").read_text(encoding="utf-8")
    assert re.findall(r"^## Example [1-3] —", guide, flags=re.MULTILINE) == [
        "## Example 1 —",
        "## Example 2 —",
        "## Example 3 —",
    ]
    for label in ("Simple", "Intermediate", "Advanced"):
        assert label in guide, label


def test_default_category_policy_copy_matches_packaged_data() -> None:
    owner_example = yaml.safe_load((ROOT / "config/categories.yaml").read_text(encoding="utf-8"))
    packaged = yaml.safe_load(
        (ROOT / "src/sqlctx/data/categories.yaml").read_text(encoding="utf-8")
    )
    assert owner_example == packaged


def test_no_python_environment_or_project_temp_payload_exists() -> None:
    forbidden_names = {".venv", "venv", "virtualenv", ".conda", "pipx", "python-runtime"}
    offenders = [
        path for path in ROOT.rglob("*") if path.is_dir() and path.name.lower() in forbidden_names
    ]
    assert offenders == []
    assert not list(ROOT.rglob(".tmp-*"))
    ignored_residue = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
    assert not [path for path in ROOT.rglob("*") if path.is_dir() and path.name in ignored_residue]
    assert not [
        path for path in ROOT.rglob("*") if path.is_dir() and path.name.endswith(".egg-info")
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "src/sqlctx").rglob("*.py")
    )
    assert "python -m venv" not in source.lower()
    assert "virtualenv.create" not in source.lower()
    assert "conda create" not in source.lower()
    assert "pipx install" not in source.lower()


def test_ci_uses_residue_free_verification() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in workflow
    assert workflow.count("scripts/dev-check.ps1 -Task clean") == 3
    assert workflow.count("-p no:cacheprovider") == 4
    assert workflow.count("${{ runner.temp }}") == 4
    assert "scripts/dev-check.ps1 -Task build" in workflow
    assert "python -m build --no-isolation" not in workflow


def test_generated_public_schemas_cover_complete_surfaces() -> None:
    import json

    openapi = json.loads((ROOT / "docs/generated/openapi.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    operation_count = sum(
        method in {"get", "post", "delete"} for path in openapi["paths"].values() for method in path
    )
    assert operation_count == 38
    operations = [
        operation
        for path in openapi["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "delete"}
    ]
    assert all("x-sqlctx-examples" in operation for operation in operations)
    bundle = openapi["paths"]["/api/v1/exports/{export_id}/bundle"]["get"]
    assert "application/zip" in bundle["responses"]["200"]["content"]
    assert len(mcp["tools"]) == 34
    export = next(tool for tool in mcp["tools"] if tool["name"] == "sqlctx_export_batch")
    properties = export["inputSchema"]["properties"]
    assert "object_ids" not in export["inputSchema"]["required"]
    assert properties["object_ids"]["default"] is None
    assert properties["output_profile"]["default"] == "ai"
    assert properties["sample_format"]["default"] == "markdown"
    query = next(tool for tool in mcp["tools"] if tool["name"] == "sqlctx_query_data")
    query_properties = query["inputSchema"]["properties"]
    assert query_properties["max_rows"]["default"] == 100
    assert query_properties["value_mode"]["default"] == "short"
    assert "all_rows" not in query_properties
    bridge = json.loads((ROOT / "docs/generated/mcp-bridge-tools.json").read_text(encoding="utf-8"))
    assert len(bridge["tools"]) == 4
    assert all(item["inputSchema"].get("additionalProperties") is False for item in mcp["tools"])
    assert all(item["outputSchema"].get("additionalProperties") is False for item in mcp["tools"])
    assert all("inputExample" in item and "outputExample" in item for item in mcp["tools"])
    assert len(mcp["resource_templates"]) == 2
