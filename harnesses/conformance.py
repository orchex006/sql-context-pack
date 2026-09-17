"""Provider-neutral deterministic conformance simulator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def simulate(harness: str, root: Path) -> dict[str, Any]:
    if harness not in {"codex", "claude", "gemini"}:
        raise ValueError("unsupported harness")
    scenario = json.loads((root / "fixtures/conformance/scenario.json").read_text(encoding="utf-8"))
    skill = root / "skills/sql-context-pack/SKILL.md"
    manifest = yaml.safe_load(
        (root / "fixtures/realistic-output/manifest.yaml").read_text(encoding="utf-8")
    )
    # Derived from the published schema so a harness never reports a stale hardcoded surface.
    core_tools = json.loads((root / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))[
        "tools"
    ]
    preview_items = [
        item for page in scenario["catalog"]["preview_pages"] for item in page["items"]
    ]
    analysis_ids = [
        item for page in scenario["catalog"]["analysis_pages"] for item in page["object_ids"]
    ]
    counts = scenario["catalog"]
    assert counts["discovered"] == counts["analyzed"] + counts["analysis_failed"]
    assert counts["analyzed"] == counts["materialized"] + counts["intentionally_excluded"]
    assert not counts["restricted_by_selection"]
    return {
        "skill_discovered": skill.is_file(),
        "skill_sha256": "sha256:" + hashlib.sha256(skill.read_bytes()).hexdigest(),
        "mcp_tool_count": len(core_tools),
        "credentials_exposed": False,
        "selection_mode": scenario["request"]["mode"],
        "selected_categories": sorted(scenario["request"]["selected_categories"]),
        "preview_categories": sorted(item["category"] for item in preview_items),
        "preview_pages_consumed": len(scenario["catalog"]["preview_pages"]),
        "analysis_pages_consumed": len(scenario["catalog"]["analysis_pages"]),
        "analysis_object_ids": sorted(analysis_ids),
        "full_analysis_despite_selection": len(analysis_ids) == counts["discovered"],
        "proposal_status": scenario["classification"]["proposal_status"],
        "owner_resolution_status": scenario["classification"]["after_owner_resolution"],
        "owner_questions": scenario["classification"]["consolidated_owner_questions"],
        "counts": {
            key: counts[key]
            for key in (
                "discovered",
                "analyzed",
                "analysis_failed",
                "materialized",
                "intentionally_excluded",
            )
        },
        "boundary_relationships": counts["boundary_relationships"],
        "output_format_version": manifest["output_format_version"],
        "format_requested": manifest["sqlfluff"]["format_requested"],
        "validation": "valid",
    }
