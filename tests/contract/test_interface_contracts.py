from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sqlctx.application.idempotency import IdempotencyService
from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.core.models import (
    ExportBatchRequest,
    MaterializationPlan,
    MaterializationPlanItem,
    MaterializationSelection,
)
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.server.contracts import (
    CapabilitiesResponse,
    CreateCatalogRequest,
    EngineCapability,
    ExportCreateRequest,
)
from sqlctx.server.facade import ServiceFacade
from sqlctx.server.mcp.server import McpPublicError, McpToolRouter

HTTP_OPERATIONS = {
    ("get", "/api/v1/health"),
    ("get", "/api/v1/capabilities"),
    ("get", "/api/v1/profiles"),
    ("post", "/api/v1/profiles/{profile}/test"),
    ("post", "/api/v1/query"),
    ("get", "/api/v1/managed-folders"),
    ("post", "/api/v1/managed-folder-plans"),
    ("post", "/api/v1/managed-folder-plans/{plan_id}/apply"),
    ("post", "/api/v1/context-index/search"),
    ("post", "/api/v1/context-index/sync"),
    ("post", "/api/v1/context-index/resolve"),
    ("post", "/api/v1/context-index/generation-plans"),
    ("post", "/api/v1/routine-plans"),
    ("post", "/api/v1/routine-plans/{plan_id}/apply"),
    ("get", "/api/v1/catalogs"),
    ("post", "/api/v1/catalogs"),
    ("get", "/api/v1/catalogs/{catalog_id}"),
    ("post", "/api/v1/catalogs/{catalog_id}/cancel"),
    ("delete", "/api/v1/catalogs/{catalog_id}"),
    ("get", "/api/v1/catalogs/{catalog_id}/category-preview"),
    ("post", "/api/v1/catalogs/{catalog_id}/selection"),
    ("get", "/api/v1/catalogs/{catalog_id}/sitemap"),
    ("get", "/api/v1/catalogs/{catalog_id}/materialization-plan"),
    ("get", "/api/v1/catalogs/{catalog_id}/classification-requests"),
    ("post", "/api/v1/catalogs/{catalog_id}/classification-proposals"),
    ("post", "/api/v1/catalogs/{catalog_id}/classification-resolutions"),
    ("get", "/api/v1/exports"),
    ("post", "/api/v1/exports"),
    ("get", "/api/v1/exports/{export_id}"),
    ("post", "/api/v1/exports/{export_id}/cancel"),
    ("delete", "/api/v1/exports/{export_id}"),
    ("get", "/api/v1/exports/{export_id}/bundle"),
    ("get", "/api/v1/exports/{export_id}/manifest"),
    ("get", "/api/v1/exports/{export_id}/report"),
    ("post", "/api/v1/validations"),
    ("get", "/api/v1/tooling/sqlfluff"),
    ("post", "/api/v1/tooling/sqlfluff/ensure"),
    ("post", "/api/v1/tooling/sqlfluff/update"),
}

MCP_TOOLS = {
    "sqlctx_get_capabilities",
    "sqlctx_list_profiles",
    "sqlctx_test_profile",
    "sqlctx_query_data",
    "sqlctx_list_managed_folders",
    "sqlctx_plan_folder_classification",
    "sqlctx_apply_folder_classification",
    "sqlctx_list_context_index",
    "sqlctx_sync_context_index",
    "sqlctx_resolve_context_index",
    "sqlctx_plan_context_generation",
    "sqlctx_plan_routine_deployment",
    "sqlctx_apply_routine_deployment",
    "sqlctx_list_catalogs",
    "sqlctx_create_catalog",
    "sqlctx_get_catalog_status",
    "sqlctx_cancel_catalog",
    "sqlctx_delete_catalog",
    "sqlctx_get_category_preview",
    "sqlctx_set_materialization_selection",
    "sqlctx_list_sitemap",
    "sqlctx_get_materialization_plan",
    "sqlctx_get_classification_requests",
    "sqlctx_submit_classification_proposals",
    "sqlctx_resolve_classifications",
    "sqlctx_list_exports",
    "sqlctx_export_batch",
    "sqlctx_get_export_status",
    "sqlctx_cancel_export",
    "sqlctx_delete_export",
    "sqlctx_validate_exports",
    "sqlctx_sqlfluff_status",
    "sqlctx_sqlfluff_ensure",
    "sqlctx_sqlfluff_update",
}


def test_exact_http_and_mcp_operation_surfaces() -> None:
    http_source = Path("src/sqlctx/server/http/app.py").read_text(encoding="utf-8")
    routes = set(re.findall(r'@app\.(get|post|delete)\(\s*"([^\"]+)"', http_source))
    assert routes == HTTP_OPERATIONS
    mcp_source = Path("src/sqlctx/server/mcp/server.py").read_text(encoding="utf-8")
    tools = set(re.findall(r'@tool\("(sqlctx_[^\"]+)"\)', mcp_source))
    assert tools == MCP_TOOLS
    resources = set(re.findall(r'@mcp\.resource\("([^\"]+)"', mcp_source))
    assert resources == {
        "sqlctx://export/{export_id}/manifest",
        "sqlctx://export/{export_id}/report",
    }
    assert "sqlctx://catalog" not in mcp_source


def test_strict_schema_and_mandatory_export_stage_rejection() -> None:
    with pytest.raises(ValidationError):
        ExportCreateRequest(catalog_id="cat", object_ids=["table:app.T"], invented=True)  # type: ignore[call-arg]
    command = ExportCreateRequest(catalog_id="cat", object_ids=["table:app.T"], sqlfluff=False)
    facade = object.__new__(ServiceFacade)
    with pytest.raises(SqlCtxError, match="mandatory") as caught:
        facade.create_export(command, caller="agent", idempotency_key="safe-key-123")
    assert caught.value.code == "MANDATORY_EXPORT_STAGE_DISABLED"
    default_export = ExportCreateRequest(catalog_id="cat")
    assert default_export.object_ids is None
    assert default_export.output_profile == "ai"
    assert default_export.sample_format == "markdown"
    with pytest.raises(ValidationError):
        ExportCreateRequest(catalog_id="cat", output_profile="automatic")  # type: ignore[arg-type]


def test_all_mode_export_queues_every_included_object_when_classification_is_unresolved() -> None:
    class Classifications:
        def materialization_plan(self, catalog_id: str) -> MaterializationPlan:
            return MaterializationPlan(
                catalog_id=catalog_id,
                selection=MaterializationSelection(mode="all"),
                items=[
                    MaterializationPlanItem(
                        object_id="table:agrimap_etl.ETL_UNKNOWN",
                        final_category=None,
                        included=True,
                        reason="all_mode",
                    )
                ],
            )

    class Exports:
        request: ExportBatchRequest | None = None

        def create(self, request: ExportBatchRequest) -> object:
            self.request = request
            return object()

    class Idempotency:
        def execute(self, **arguments: object) -> tuple[object, bool]:
            create = arguments["create"]
            assert callable(create)
            return create(), False

    facade = object.__new__(ServiceFacade)
    facade.classifications = Classifications()  # type: ignore[assignment]
    exports = Exports()
    facade.exports = exports  # type: ignore[assignment]
    facade.idempotency = Idempotency()  # type: ignore[assignment]
    command = ExportCreateRequest(catalog_id="cat_etl")

    facade.create_export(command, caller="agent", idempotency_key="etl-export-key")

    assert exports.request is not None
    assert exports.request.object_ids == ["table:agrimap_etl.ETL_UNKNOWN"]


def test_all_mode_catalog_conflict_stops_before_profile_or_adapter_resolution() -> None:
    facade = object.__new__(ServiceFacade)
    command = CreateCatalogRequest(
        profile="demo",
        schemas=["agrimap_etl"],
        include_patterns=["ETL_*"],
        selection=MaterializationSelection(mode="all"),
    )

    with pytest.raises(SqlCtxError) as caught:
        facade.create_catalog(command, caller="agent", idempotency_key="etl-catalog-key")

    assert caught.value.code == "ALL_MODE_INCLUDE_FILTER_CONFLICT"
    assert caught.value.details == {"include_pattern_count": 1}


def test_idempotency_same_request_and_conflict(tmp_path: Path) -> None:
    service = IdempotencyService(JsonRuntimeStateStore(tmp_path / "runtime"))
    created = 0

    def create() -> dict[str, int]:
        nonlocal created
        created += 1
        return {"job": created}

    first, replay = service.execute(
        caller="agent",
        operation="catalog.create",
        key="same-key-123",
        normalized_request={"profile": "one"},
        create=create,
        serialize=lambda item: item,
        validate=lambda item: item,
    )
    second, replay2 = service.execute(
        caller="agent",
        operation="catalog.create",
        key="same-key-123",
        normalized_request={"profile": "one"},
        create=create,
        serialize=lambda item: item,
        validate=lambda item: item,
    )
    assert first == second == {"job": 1}
    assert replay is False and replay2 is True
    with pytest.raises(SqlCtxError) as caught:
        service.execute(
            caller="agent",
            operation="catalog.create",
            key="same-key-123",
            normalized_request={"profile": "two"},
            create=create,
            serialize=lambda item: item,
            validate=lambda item: item,
        )
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


def test_persistent_owner_approval_binding_and_single_use(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    first_process = ApprovalService(state=state)
    payload = {"export_id": "exp_1"}
    with pytest.raises(ApprovalRequired) as challenge_error:
        first_process.require(
            caller="agent-a", operation="export.delete", target="exp_1", payload=payload
        )
    challenge_id = challenge_error.value.details["approval"]["challenge_id"]
    ApprovalService(state=state).grant(challenge_id, interactive=True)
    retry_process = ApprovalService(state=state)
    retry_process.require(
        caller="agent-a", operation="export.delete", target="exp_1", payload=payload
    )
    with pytest.raises(ApprovalRequired):
        retry_process.require(
            caller="agent-a", operation="export.delete", target="exp_1", payload=payload
        )
    with pytest.raises(ApprovalRequired):
        retry_process.require(
            caller="agent-b", operation="export.delete", target="exp_1", payload=payload
        )


class CapabilityOnlyFacade:
    def capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            engines=[EngineCapability(engine="postgres", sqlfluff_dialect="postgres")]
        )


def test_http_mcp_normalization_uses_the_same_typed_result() -> None:
    facade = CapabilityOnlyFacade()
    http_result = facade.capabilities().model_dump(mode="json")
    mcp_result = McpToolRouter(facade, "agent:test").invoke("sqlctx_get_capabilities", {})  # type: ignore[arg-type]
    assert mcp_result == http_result


class ApprovalFacade:
    def delete_catalog(self, catalog_id: str, *, caller: str) -> None:
        raise ApprovalRequired(
            {
                "approval": {
                    "challenge_id": "apr_visible",
                    "expires_at": "2026-07-19T06:00:00+00:00",
                    "expires_in_seconds": 300,
                    "owner_command": "sqlctx approvals grant --challenge apr_visible",
                }
            }
        )


def test_mcp_error_preserves_safe_approval_contract() -> None:
    with pytest.raises(McpPublicError) as caught:
        McpToolRouter(ApprovalFacade(), "agent:test").invoke(  # type: ignore[arg-type]
            "sqlctx_delete_catalog", {"catalog_id": "cat_1"}
        )

    message = str(caught.value)
    assert "APPROVAL_REQUIRED" in message
    assert "apr_visible" in message
    assert "expires_in_seconds" in message
    assert "sqlctx approvals grant --challenge apr_visible" in message
