from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sqlctx_harness_conformance", ROOT / "harnesses/conformance.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("harness", ["codex", "claude", "gemini"])
def test_all_harnesses_produce_identical_safe_normalized_results(harness: str) -> None:
    baseline = MODULE.simulate("codex", ROOT)
    result = MODULE.simulate(harness, ROOT)
    assert result == baseline
    assert result["skill_discovered"]
    assert result["mcp_tool_count"] == 34
    assert result["credentials_exposed"] is False
    assert result["preview_pages_consumed"] == 2
    assert result["analysis_pages_consumed"] == 2
    assert result["full_analysis_despite_selection"] is True
    assert result["proposal_status"] == "final_suggested"
    assert result["owner_resolution_status"] == "final_confirmed"
    assert result["owner_questions"] == 1
    assert result["validation"] == "valid"


def test_only_one_canonical_skill_workflow_exists() -> None:
    skills = list(ROOT.rglob("SKILL.md"))
    assert skills == [ROOT / "skills/sql-context-pack/SKILL.md"]
    assert not (ROOT / "harnesses/codex/SKILL.md").exists()
    assert not (ROOT / "harnesses/claude/SKILL.md").exists()
    assert not (ROOT / "harnesses/gemini/SKILL.md").exists()


def test_canonical_skill_preserves_complete_all_mode_and_clarifies_etl_scope() -> None:
    skill = (ROOT / "skills/sql-context-pack/SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / "skills/sql-context-pack/references/workflow.md").read_text(encoding="utf-8")
    contracts = (ROOT / "skills/sql-context-pack/references/contracts.md").read_text(
        encoding="utf-8"
    )
    contracts_flat = " ".join(contracts.split())
    workflow_flat = " ".join(workflow.split())

    assert "empty `include_patterns` in all mode" in skill
    assert "allowed schema" in workflow_flat
    assert "`ETL_` name prefix" in workflow_flat
    assert "final category `etl`" in workflow_flat
    assert "one consolidated owner question" in workflow_flat
    assert "ALL_MODE_INCLUDE_FILTER_CONFLICT" in contracts
    assert "do not raise `ALL_MODE_UNRESOLVED_OBJECTS`" in contracts_flat
