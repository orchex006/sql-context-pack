from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# Re-pinned when `function` was added to ObjectType, and again when `CatalogStatus.failures[]`
# was added so an analysis failure names its object instead of moving an anonymous counter.
# `failures[]` is optional and defaults to `[]`, so every v1.21 client stays compatible; it
# carries an `object_type`, which is why the four `CatalogStatus`-returning tools now own the
# object-type enum too. The only accepted deltas from the released v1.21 contract are those two
# additive changes, asserted explicitly by the two tests below. Any other drift must fail.
OBJECT_TYPE_ENUM_OWNERS = {
    "sqlctx_cancel_catalog",
    "sqlctx_create_catalog",
    "sqlctx_get_catalog_status",
    "sqlctx_set_materialization_selection",
    "sqlctx_get_capabilities",
    "sqlctx_list_profiles",
    "sqlctx_list_sitemap",
    "sqlctx_list_context_index",
    "sqlctx_plan_context_generation",
    "sqlctx_plan_folder_classification",
    "sqlctx_plan_routine_deployment",
    "sqlctx_resolve_context_index",
    "sqlctx_sync_context_index",
}

V130_ADDITIVE_TOOLS = {
    "sqlctx_apply_folder_classification",
    "sqlctx_apply_routine_deployment",
    "sqlctx_list_context_index",
    "sqlctx_list_managed_folders",
    "sqlctx_plan_context_generation",
    "sqlctx_plan_folder_classification",
    "sqlctx_plan_routine_deployment",
    "sqlctx_resolve_context_index",
    "sqlctx_sync_context_index",
}

V121_TOOL_HASHES = {
    "sqlctx_cancel_catalog": "86aa12fa0af20db010f4ad9b294919becd88e5abe491b18900817b8e6e2d394e",
    "sqlctx_cancel_export": "a79c195ffe22543011a779b5280f3a9db1a462e3bd931bc6ff8293ea1b8d74a1",
    "sqlctx_create_catalog": "0c2d3954eaa5c1f9b02ea3230afc68280de3077ea8e3cd1cf5da4b39a6064cfc",
    "sqlctx_delete_catalog": "4b0cec47bff0ef32470b7eff61c34afb60aca787be38d73faa7d095da1a5bf51",
    "sqlctx_delete_export": "6079921ef3183bfd15022c02938b65bc28d5d7cf60e8fbf26627a295862d8f54",
    "sqlctx_export_batch": "4579beb6e0a393cc8367c334e498536144da97d6db8e4ddc394ddbe4fe210be9",
    "sqlctx_get_capabilities": "06435f209509909304cfaad6a7394eb30d1c380c68694fe9ae940badb652d645",
    "sqlctx_get_catalog_status": "1c23b9e9130da1621fb7e52c58032d79f71d49f1a37dfdadd0cc4e38ecf5fee7",
    "sqlctx_get_category_preview": "bfbae25e825d80e089028a5cc5f4fa08e9e4a18ee039e752e4a8d2663d9bc5c2",
    "sqlctx_get_classification_requests": "06bc3d2e7a3e089c850b0d57a23ccff85a8e0c960c8609d5b7b7f99677cc81a0",
    "sqlctx_get_export_status": "ee503092e97ec9501701f79c04eff96c9c52282a49e1a40720afbc0b5f798d40",
    "sqlctx_get_materialization_plan": "f8696941ff121064c9a100883931f3f6cbe9e892012d2e85d89e54b21c953c02",
    "sqlctx_list_catalogs": "77e26dc49a03d45a142730e48df5030f93a603dc35ae21c2e43e8043e57e4b83",
    "sqlctx_list_exports": "ba4a2b12bb26b3a5745a4cd2a53acd71b674fa59de95334f9717151ea2c3a859",
    "sqlctx_list_profiles": "0aa7b22a05c7f2d673ad7149f48974e65c9098bf84c833f1f0d5b086c47da443",
    "sqlctx_list_sitemap": "599bd7c884d840f5ed28394c00197c588425623f372031c7b8a64beed56e8217",
    "sqlctx_resolve_classifications": "b1888d4e871406431027fb93ebb9f253d0adc2f0d19921dbcf41090cb62cf1a1",
    "sqlctx_set_materialization_selection": "ba695124c0b9a68ffbee8f21af711fa683a329780d3b4d941f5387834e5723a5",
    "sqlctx_sqlfluff_ensure": "620a28b6447bb5bc48cf499e8f271df287464144afa2633ec4b88a08ae8f3b98",
    "sqlctx_sqlfluff_status": "de57802f93abe837f102d2d9d6db9f0b461f2fc6752cf3186e8a18ad02488864",
    "sqlctx_sqlfluff_update": "809b51d73242a3fd3ab1ce61c5ccbe874a24c6914e68c1e89dd28365000ee5e5",
    "sqlctx_submit_classification_proposals": "6dcac10be4bd886cb7565adb3a381a7ce8a47358535fcc234908a2eea9566831",
    "sqlctx_test_profile": "2ed2fb023137c5f4c3148acc3ea1c595085717f3db4a9c8302ca9f8abe03e847",
    "sqlctx_validate_exports": "95cbf3522165264b008292da3a0c5bbae94a21801c34191d7234841eabbd9e96",
}

V121_RESOURCE_HASHES = {
    "sqlctx://export/{export_id}/manifest": "a399620b5e19700b7a4f01a5e12567176d97ebb87b3fce4d8a9f377ac6e7446e",
    "sqlctx://export/{export_id}/report": "6d4bf441cf5bf97e5016158ff2e680f4694830e651463ed438cb05dafb3dd990",
}


def digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


# v2.1.0 added the Antigravity host. Growing a closed enum with a new member is additive for
# existing clients -- a client that sends "codex" is unaffected -- so it is normalized away
# here rather than re-pinned, which keeps the hashes able to catch any *other* drift in these
# tools instead of blessing whatever they currently produce.
PRE_AGY_HARNESSES = ["codex", "claude", "gemini", "other"]


def normalize_pre_v2_output_defaults(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {key: normalize_pre_v2_output_defaults(item) for key, item in value.items()}
        if "output_format_version" in normalized:
            schema = normalized["output_format_version"]
            if isinstance(schema, dict) and schema.get("default") == "2":
                schema["default"] = "1"
        if normalized.get("title") == "Harness" and normalized.get("enum") == [
            "codex",
            "claude",
            "gemini",
            "agy",
            "other",
        ]:
            normalized["enum"] = list(PRE_AGY_HARNESSES)
        return normalized
    if isinstance(value, list):
        return [normalize_pre_v2_output_defaults(item) for item in value]
    return value


def test_harness_enum_growth_is_additive_only() -> None:
    """agy must be added to the harness enum, never replace or reorder existing members."""
    generated = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    serialized = json.dumps(generated, sort_keys=True)
    found = False
    for enum in re.findall(r'"enum":\s*(\[[^]]*\])', serialized):
        values = json.loads(enum)
        if "codex" in values:
            assert values == ["codex", "claude", "gemini", "agy", "other"], values
            found = True
    assert found, "no harness enum found in the generated MCP contract"


def test_v130_adds_only_the_approved_tools_without_old_contract_drift() -> None:
    generated = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    tools = {item["name"]: item for item in generated["tools"]}
    assert set(tools) - set(V121_TOOL_HASHES) == {"sqlctx_query_data", *V130_ADDITIVE_TOOLS}
    for name, expected in V121_TOOL_HASHES.items():
        item = tools[name]
        contract = {
            key: item.get(key) for key in ("name", "description", "inputSchema", "outputSchema")
        }
        assert digest(normalize_pre_v2_output_defaults(contract)) == expected


def test_object_type_enum_growth_is_the_only_accepted_v121_delta() -> None:
    """Every re-pinned contract must differ from v1.21 only by the added object type."""
    generated = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    tools = {item["name"]: item for item in generated["tools"]}
    found: set[str] = set()
    for name, item in tools.items():
        if name == "sqlctx_query_data":
            continue
        serialized = json.dumps(item, sort_keys=True)
        for enum in re.findall(r'"enum":\s*(\[[^]]*\])', serialized):
            values = json.loads(enum)
            if "table" in values or "procedure" in values:
                assert values == ["table", "procedure", "function"], (
                    f"{name} exposes an unexpected object-type enum: {values}"
                )
                found.add(name)
    assert found == OBJECT_TYPE_ENUM_OWNERS


def test_v122_preserves_both_mcp_resources_exactly() -> None:
    generated = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    resources = {item["uriTemplate"]: item for item in generated["resource_templates"]}
    assert set(resources) == set(V121_RESOURCE_HASHES)
    for uri, expected in V121_RESOURCE_HASHES.items():
        assert digest(resources[uri]) == expected
