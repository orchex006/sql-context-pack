import json
import tomllib
from pathlib import Path

import yaml

from sqlctx import __version__
from sqlctx.server.contracts import HealthResponse

ROOT = Path(__file__).resolve().parents[2]


def test_product_version_is_consistent() -> None:
    """Every installable surface must agree with sqlctx.__version__.

    The expected value is derived, not restated: a hardcoded constant here would go stale
    the first time someone bumped the version without touching this file, which is exactly
    what happened to scripts/validate_manifests.py.
    """
    codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads(
        (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    gemini = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))
    agy = json.loads((ROOT / ".agents/plugin.json").read_text(encoding="utf-8"))
    agy_marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    skill_frontmatter = (
        (ROOT / "skills/sql-context-pack/SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    )
    skill = yaml.safe_load(skill_frontmatter)
    assert {
        __version__,
        HealthResponse().version,
        project["tool"]["sqlctx"]["product-version"],
        skill["metadata"]["version"],
        codex["version"],
        claude["version"],
        claude_marketplace["plugins"][0]["version"],
        gemini["version"],
        agy["version"],
        agy_marketplace["plugins"][0]["version"],
    } == {__version__}


def test_dependency_pins_and_host_python_policy() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"
    assert "sqlfluff==4.2.2" in project["project"]["dependencies"]
    assert (
        project["project"]["urls"]["Repository"] == "https://github.com/orchex006/sql-context-pack"
    )
    assert not any("virtualenv" in item for item in project["project"]["dependencies"])


def test_generated_descriptions_are_interpreter_independent() -> None:
    """Generated contracts must not depend on which Python produced them.

    Python 3.13 dedents docstrings at compile time and 3.11 does not. FastMCP and FastAPI
    take descriptions straight from __doc__, so an artifact generated on one interpreter
    would not match one generated on the other -- which broke CI, since quality runs 3.11
    while development ran 3.13. The MCP tool decorator now applies inspect.cleandoc, and
    this asserts the committed artifacts are in that normalized form.
    """
    import inspect

    for relative in (
        "docs/generated/mcp-tools.json",
        "docs/generated/mcp-bridge-tools.json",
    ):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        for entry in payload["tools"]:
            description = entry.get("description")
            if not description:
                continue
            assert description == inspect.cleandoc(description), f"{relative}: {entry['name']}"

    openapi = json.loads((ROOT / "docs/generated/openapi.json").read_text(encoding="utf-8"))
    for path, operations in openapi["paths"].items():
        for method, operation in operations.items():
            for field in ("summary", "description"):
                text = operation.get(field)
                if not text:
                    continue
                assert text == inspect.cleandoc(text), f"{method.upper()} {path}: {field}"
