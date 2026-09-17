"""Streamable HTTP MCP tools and resources over the shared application facade."""

from __future__ import annotations

import inspect
import json
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from sqlctx.context_index.contracts import (
    ContextGenerationPlan,
    ContextGenerationRequest,
    ContextIndexEntry,
    ContextIndexListRequest,
    ContextIndexPage,
    ContextIndexSyncRequest,
    ContextIndexSyncResult,
)
from sqlctx.core.enums import JobStatus, ObjectType, OutputProfile, SampleOutputFormat
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    AssembledInventory,
    CatalogJobPage,
    CatalogStatus,
    CategoryPreview,
    ClassificationRequestPage,
    DeleteResult,
    ExportJobPage,
    ExportStatus,
    HostPythonToolingDescriptor,
    MaterializationPlan,
    MaterializationSelection,
    ProposalBatchResult,
    SitemapPage,
    ValidationRequest,
    ValidationResult,
)
from sqlctx.managed_files.contracts import (
    FolderApplyResult,
    FolderClassificationPlan,
    FolderClassificationPlanRequest,
    ManagedFileResolutionPlanRequest,
    OwnerFileResolution,
    RegisteredFolderList,
)
from sqlctx.routine_deploy.contracts import (
    RoutineApplyResult,
    RoutineDeploymentPlan,
    RoutinePlanRequest,
)
from sqlctx.security.audit import OperationAuditLogger
from sqlctx.server.contracts import (
    CapabilitiesResponse,
    ClassificationResolutionBatch,
    ConnectionTestResult,
    CreateCatalogRequest,
    ExportCreateRequest,
    ProfileDescriptorList,
    ProposalItem,
    ProposalRequest,
    Proposer,
    QueryDataRequest,
    QueryDataResult,
    ResolutionBatchResult,
    ResolutionItem,
    SamplePolicy,
    ValueMode,
)
from sqlctx.server.facade import ServiceFacade


class McpPublicError(Exception):
    """Structured, sanitized error text retained by FastMCP's isError response."""

    def __init__(self, error: SqlCtxError) -> None:
        super().__init__(
            json.dumps({"error": error.public_payload()}, sort_keys=True, ensure_ascii=False)
        )


def _json(value: Any) -> Any:
    return value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value


class McpToolRouter:
    """SDK-independent dispatcher used by MCP registration and parity tests."""

    def __init__(self, service: ServiceFacade, caller: str) -> None:
        self.service = service
        self.caller = caller
        self.audit = OperationAuditLogger(service.state) if hasattr(service, "state") else None

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        started = time.perf_counter()
        error_code: str | None = None
        result: Any = None
        try:
            result = self._invoke(name, arguments)
            return result
        except SqlCtxError as exc:
            error_code = exc.code
            raise McpPublicError(exc) from exc
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            if self.audit is not None:
                query_result = (
                    result if name == "sqlctx_query_data" and isinstance(result, dict) else {}
                )
                requested_mode = arguments.get("value_mode", "short")
                self.audit.record(
                    transport="mcp",
                    caller=self.caller,
                    operation=name,
                    outcome="failed" if error_code else "succeeded",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_code=error_code,
                    value_mode=(
                        requested_mode
                        if name == "sqlctx_query_data" and requested_mode in {"short", "full"}
                        else None
                    ),
                    returned_row_count=(
                        int(query_result.get("returned_row_count", 0))
                        if name == "sqlctx_query_data"
                        else None
                    ),
                    truncated=(
                        bool(query_result.get("truncated", False))
                        if name == "sqlctx_query_data"
                        else None
                    ),
                )

    def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        s = self.service
        if name == "sqlctx_get_capabilities":
            return _json(s.capabilities())
        if name == "sqlctx_list_profiles":
            return s.list_profiles()
        if name == "sqlctx_test_profile":
            return s.test_profile(str(arguments["profile"]))
        if name == "sqlctx_query_data":
            return _json(s.query(QueryDataRequest.model_validate(arguments)))
        if name == "sqlctx_list_managed_folders":
            return _json(s.list_managed_folders())
        if name == "sqlctx_plan_folder_classification":
            return _json(
                s.plan_folder_classification(
                    FolderClassificationPlanRequest.model_validate(arguments)
                )
            )
        if name == "sqlctx_apply_folder_classification":
            return _json(
                s.apply_folder_classification(str(arguments["plan_id"]), caller=self.caller)
            )
        if name == "sqlctx_list_context_index":
            return _json(s.list_context_index(ContextIndexListRequest.model_validate(arguments)))
        if name == "sqlctx_sync_context_index":
            return _json(
                s.sync_context_index(
                    ContextIndexSyncRequest.model_validate(arguments), caller=self.caller
                )
            )
        if name == "sqlctx_resolve_context_index":
            return _json(
                s.resolve_context_index(ManagedFileResolutionPlanRequest.model_validate(arguments))
            )
        if name == "sqlctx_plan_context_generation":
            return _json(
                s.plan_context_generation(ContextGenerationRequest.model_validate(arguments))
            )
        if name == "sqlctx_plan_routine_deployment":
            return _json(
                s.plan_routine_deployment(
                    RoutinePlanRequest.model_validate(arguments), caller=self.caller
                )
            )
        if name == "sqlctx_apply_routine_deployment":
            return _json(s.apply_routine_deployment(str(arguments["plan_id"]), caller=self.caller))
        if name == "sqlctx_list_catalogs":
            return _json(
                s.catalogs.list_jobs(
                    status=arguments.get("status"),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit", 100),
                )
            )
        if name == "sqlctx_create_catalog":
            command = CreateCatalogRequest.model_validate(arguments)
            return _json(s.create_catalog(command, caller=self.caller)[0])
        if name == "sqlctx_get_catalog_status":
            return _json(s.catalogs.status(str(arguments["catalog_id"])))
        if name == "sqlctx_cancel_catalog":
            return _json(s.catalogs.cancel(str(arguments["catalog_id"])))
        if name == "sqlctx_delete_catalog":
            return _json(s.delete_catalog(str(arguments["catalog_id"]), caller=self.caller))
        if name == "sqlctx_get_category_preview":
            return _json(
                s.catalogs.category_preview(
                    str(arguments["catalog_id"]),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit", 100),
                )
            )
        if name == "sqlctx_set_materialization_selection":
            catalog_id = str(arguments["catalog_id"])
            selection = MaterializationSelection.model_validate(arguments["selection"])
            result = s.catalogs.select(catalog_id, selection)
            s.classifications.classify(catalog_id, selection)
            return _json(result)
        if name == "sqlctx_list_sitemap":
            return _json(
                s.catalogs.sitemap(
                    str(arguments["catalog_id"]),
                    view=str(arguments["view"]),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit", 100),
                )
            )
        if name == "sqlctx_get_materialization_plan":
            return _json(s.classifications.materialization_plan(str(arguments["catalog_id"])))
        if name == "sqlctx_get_classification_requests":
            return _json(
                s.classifications.requests(
                    str(arguments["catalog_id"]),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit", 100),
                )
            )
        if name == "sqlctx_submit_classification_proposals":
            return _json(s.submit_proposals(ProposalRequest.model_validate(arguments)))
        if name == "sqlctx_resolve_classifications":
            return _json(
                s.resolve(
                    ClassificationResolutionBatch.model_validate(arguments), caller=self.caller
                )
            )
        if name == "sqlctx_list_exports":
            return _json(
                s.exports.list_jobs(
                    catalog_id=arguments.get("catalog_id"),
                    status=arguments.get("status"),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit", 100),
                )
            )
        if name == "sqlctx_export_batch":
            return _json(
                s.create_export(ExportCreateRequest.model_validate(arguments), caller=self.caller)[
                    0
                ]
            )
        if name == "sqlctx_get_export_status":
            return _json(s.exports.status(str(arguments["export_id"])))
        if name == "sqlctx_cancel_export":
            return _json(s.exports.cancel(str(arguments["export_id"])))
        if name == "sqlctx_delete_export":
            return _json(s.delete_export(str(arguments["export_id"]), caller=self.caller))
        if name == "sqlctx_validate_exports":
            return _json(s.validate(ValidationRequest.model_validate(arguments)))
        if name == "sqlctx_sqlfluff_status":
            return _json(s.manager.status())
        if name == "sqlctx_sqlfluff_ensure":
            return _json(s.tooling_ensure(caller=self.caller))
        if name == "sqlctx_sqlfluff_update":
            return _json(s.tooling_update(str(arguments["version"]), caller=self.caller))
        raise ValueError("unknown MCP tool")


def build_mcp(service: ServiceFacade, caller: str) -> Any:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "sql-context-pack", stateless_http=True, json_response=True, streamable_http_path="/mcp"
    )
    router = McpToolRouter(service, caller)

    def tool(name: str):  # type: ignore[no-untyped-def]
        register = mcp.tool(name=name, structured_output=True)

        def decorate(fn):  # type: ignore[no-untyped-def]
            # Python 3.13 dedents docstrings at compile time; 3.11 does not. FastMCP takes
            # the tool description straight from __doc__, so without normalizing here the
            # description shipped to clients -- and the generated contract built from it --
            # would differ by interpreter version.
            if fn.__doc__:
                fn.__doc__ = inspect.cleandoc(fn.__doc__)
            return register(fn)

        return decorate

    @tool("sqlctx_get_capabilities")
    def get_capabilities() -> CapabilitiesResponse:
        """Return safe engine and service capabilities without probing a database."""
        return router.invoke("sqlctx_get_capabilities", {})

    @tool("sqlctx_list_profiles")
    def list_profiles() -> ProfileDescriptorList:
        """List safe owner-configured profile descriptors; never return credentials."""
        return router.invoke("sqlctx_list_profiles", {})

    @tool("sqlctx_test_profile")
    def test_profile(profile: str) -> ConnectionTestResult:
        """Perform a bounded read-only connection test for a named profile."""
        return router.invoke("sqlctx_test_profile", {"profile": profile})

    @tool("sqlctx_query_data")
    def query_data(
        sql: Annotated[str, Field(min_length=1, max_length=20_000)],
        profile: str,
        max_rows: Annotated[int, Field(ge=1, le=500)] = 100,
        value_mode: ValueMode = "short",
    ) -> QueryDataResult:
        """Run one validated read-only relational SELECT and return masked Markdown."""
        return router.invoke(
            "sqlctx_query_data",
            {
                "sql": sql,
                "profile": profile,
                "max_rows": max_rows,
                "value_mode": value_mode,
            },
        )

    @tool("sqlctx_list_managed_folders")
    def list_managed_folders() -> RegisteredFolderList:
        """List opaque registered folder IDs without exposing owner filesystem paths."""
        return router.invoke("sqlctx_list_managed_folders", {})

    @tool("sqlctx_plan_folder_classification")
    def plan_folder_classification(
        folder_id: str,
        resolutions: list[OwnerFileResolution] | None = None,
        in_place: bool = False,
    ) -> FolderClassificationPlan:
        """Scan a registered folder and return a complete non-mutating classification plan."""
        return router.invoke(
            "sqlctx_plan_folder_classification",
            {
                "folder_id": folder_id,
                "resolutions": [_json(item) for item in resolutions or []],
                "in_place": in_place,
            },
        )

    @tool("sqlctx_apply_folder_classification")
    def apply_folder_classification(plan_id: str) -> FolderApplyResult:
        """Apply one unchanged folder plan; in-place plans require owner approval."""
        return router.invoke("sqlctx_apply_folder_classification", {"plan_id": plan_id})

    @tool("sqlctx_list_context_index")
    def list_context_index(
        profile: str,
        context: str | None = None,
        object_type: ObjectType | None = None,
        tag: str | None = None,
        status: Literal["confirmed", "unresolved"] | None = None,
        active: bool = True,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> ContextIndexPage:
        """List bounded DB_METADATA_CONTEXT metadata without SQL bodies or credentials."""
        return router.invoke(
            "sqlctx_list_context_index",
            {
                "profile": profile,
                "context": context,
                "object_type": object_type,
                "tag": tag,
                "status": status,
                "active": active,
                "cursor": cursor,
                "limit": limit,
            },
        )

    @tool("sqlctx_sync_context_index")
    def sync_context_index(
        profile: str,
        entries: Annotated[list[ContextIndexEntry], Field(max_length=5_000)],
        actor_id: Annotated[int, Field(gt=0)],
        idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
        complete_catalog_id: str | None = None,
        mode: Literal["inventory", "owner"] = "inventory",
    ) -> ContextIndexSyncResult:
        """Typed upsert into DB_METADATA_CONTEXT.

        `inventory` (default) records identity and technical fields for every discovered
        object, never overwrites an owner classification, and needs no approval. `owner`
        carries supervised classifications or deactivation and stays approval-gated.
        """
        return router.invoke(
            "sqlctx_sync_context_index",
            {
                "profile": profile,
                "entries": [_json(item) for item in entries],
                "actor_id": actor_id,
                "idempotency_key": idempotency_key,
                "complete_catalog_id": complete_catalog_id,
                "mode": mode,
            },
        )

    @tool("sqlctx_resolve_context_index")
    def resolve_context_index(
        folder_id: str,
        managed_relative_path: str,
        context: str,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> FolderClassificationPlan:
        """Plan one owner-confirmed managed header/path rewrite before index synchronization."""
        return router.invoke(
            "sqlctx_resolve_context_index",
            {
                "folder_id": folder_id,
                "managed_relative_path": managed_relative_path,
                "context": context,
                "description": description,
                "tags": tags or [],
            },
        )

    @tool("sqlctx_plan_context_generation")
    def plan_context_generation(
        profile: str,
        folder_id: str,
        context: str | None = None,
        object_type: ObjectType | None = None,
        tag: str | None = None,
        include_unresolved: bool = False,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> ContextGenerationPlan:
        """Select generation inputs from the index and stop on header/body drift."""
        return router.invoke(
            "sqlctx_plan_context_generation",
            {
                "profile": profile,
                "folder_id": folder_id,
                "context": context,
                "object_type": object_type,
                "tag": tag,
                "include_unresolved": include_unresolved,
                "limit": limit,
            },
        )

    @tool("sqlctx_plan_routine_deployment")
    def plan_routine_deployment(
        profile: str,
        folder_id: str,
        idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
        relative_path: str | None = None,
        stop_on_error: bool = True,
    ) -> RoutineDeploymentPlan:
        """Plan one managed routine file or every eligible file in a registered folder."""
        return router.invoke(
            "sqlctx_plan_routine_deployment",
            {
                "profile": profile,
                "folder_id": folder_id,
                "relative_path": relative_path,
                "stop_on_error": stop_on_error,
                "idempotency_key": idempotency_key,
            },
        )

    @tool("sqlctx_apply_routine_deployment")
    def apply_routine_deployment(plan_id: str) -> RoutineApplyResult:
        """Apply an unchanged SQL Server routine plan after owner approval."""
        return router.invoke("sqlctx_apply_routine_deployment", {"plan_id": plan_id})

    @tool("sqlctx_list_catalogs")
    def list_catalogs(
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> CatalogJobPage:
        """Rediscover retained catalog jobs using exact request fingerprints and cursor paging."""
        return router.invoke(
            "sqlctx_list_catalogs", {"status": status, "cursor": cursor, "limit": limit}
        )

    @tool("sqlctx_create_catalog")
    def create_catalog(
        profile: str,
        schemas: list[str],
        idempotency_key: str,
        session_cache_key: str | None = None,
        object_types: list[ObjectType] | None = None,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        selection: MaterializationSelection | None = None,
        sample: SamplePolicy | None = None,
        masking_policy: Literal["strict"] = "strict",
        category_policy: Literal["two_pass"] = "two_pass",
    ) -> CatalogStatus:
        """Create a resumable two-phase catalog using a caller-scoped idempotency key."""
        return router.invoke(
            "sqlctx_create_catalog",
            {
                "profile": profile,
                "schemas": schemas,
                "idempotency_key": idempotency_key,
                "session_cache_key": session_cache_key,
                "object_types": object_types or ["table", "procedure", "function"],
                "include_patterns": include_patterns or [],
                "exclude_patterns": exclude_patterns or [],
                "selection": _json(selection)
                if selection
                else {"mode": "ask", "selected_categories": []},
                "sample": sample.model_dump(mode="json")
                if sample
                else {"strategy": "deterministic"},
                "masking_policy": masking_policy,
                "category_policy": category_policy,
            },
        )

    @tool("sqlctx_get_catalog_status")
    def get_catalog_status(catalog_id: str) -> CatalogStatus:
        """Get pollable catalog status and complete accounting."""
        return router.invoke("sqlctx_get_catalog_status", {"catalog_id": catalog_id})

    @tool("sqlctx_cancel_catalog")
    def cancel_catalog(catalog_id: str) -> CatalogStatus:
        """Cooperatively and idempotently cancel a catalog job."""
        return router.invoke("sqlctx_cancel_catalog", {"catalog_id": catalog_id})

    @tool("sqlctx_delete_catalog")
    def delete_catalog(catalog_id: str) -> DeleteResult:
        """Delete inactive catalog state after request-bound owner approval."""
        return router.invoke("sqlctx_delete_catalog", {"catalog_id": catalog_id})

    @tool("sqlctx_get_category_preview")
    def category_preview(
        catalog_id: str, cursor: str | None = None, limit: Annotated[int, Field(ge=1, le=250)] = 100
    ) -> CategoryPreview:
        """Page through preliminary categories; stop only when next_cursor is null."""
        return router.invoke(
            "sqlctx_get_category_preview",
            {"catalog_id": catalog_id, "cursor": cursor, "limit": limit},
        )

    @tool("sqlctx_set_materialization_selection")
    def set_selection(catalog_id: str, selection: MaterializationSelection) -> CatalogStatus:
        """Record materialization intent without narrowing full database analysis."""
        return router.invoke(
            "sqlctx_set_materialization_selection",
            {"catalog_id": catalog_id, "selection": _json(selection)},
        )

    @tool("sqlctx_list_sitemap")
    def list_sitemap(
        catalog_id: str,
        view: Literal["analysis", "materialization"],
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> SitemapPage:
        """Page through analysis or final-materialization sitemap records."""
        return router.invoke(
            "sqlctx_list_sitemap",
            {"catalog_id": catalog_id, "view": view, "cursor": cursor, "limit": limit},
        )

    @tool("sqlctx_get_materialization_plan")
    def get_plan(catalog_id: str) -> MaterializationPlan:
        """Return final-category materialization decisions and exclusions."""
        return router.invoke("sqlctx_get_materialization_plan", {"catalog_id": catalog_id})

    @tool("sqlctx_get_classification_requests")
    def classification_requests(
        catalog_id: str, cursor: str | None = None, limit: Annotated[int, Field(ge=1, le=250)] = 100
    ) -> ClassificationRequestPage:
        """Page through unresolved objects using sanitized evidence only."""
        return router.invoke(
            "sqlctx_get_classification_requests",
            {"catalog_id": catalog_id, "cursor": cursor, "limit": limit},
        )

    @tool("sqlctx_submit_classification_proposals")
    def submit_proposals(
        catalog_id: str, proposer: Proposer, proposals: list[ProposalItem]
    ) -> ProposalBatchResult:
        """Submit validated non-authoritative model suggestions."""
        return router.invoke(
            "sqlctx_submit_classification_proposals",
            {
                "catalog_id": catalog_id,
                "proposer": _json(proposer),
                "proposals": [_json(item) for item in proposals],
            },
        )

    @tool("sqlctx_resolve_classifications")
    def resolve_classifications(
        catalog_id: str,
        resolutions: list[ResolutionItem],
        persist_as_owner_override: Literal[True] = True,
    ) -> ResolutionBatchResult:
        """Persist authoritative resolutions only after server-enforced owner approval."""
        return router.invoke(
            "sqlctx_resolve_classifications",
            {
                "catalog_id": catalog_id,
                "resolutions": [_json(item) for item in resolutions],
                "persist_as_owner_override": persist_as_owner_override,
            },
        )

    @tool("sqlctx_list_exports")
    def list_exports(
        catalog_id: str | None = None,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> ExportJobPage:
        """Rediscover retained exports using request and batch fingerprints."""
        return router.invoke(
            "sqlctx_list_exports",
            {"catalog_id": catalog_id, "status": status, "cursor": cursor, "limit": limit},
        )

    @tool("sqlctx_export_batch")
    def export_batch(
        catalog_id: str,
        idempotency_key: str,
        object_ids: list[str] | None = None,
        sqlfluff: bool | None = None,
        append_samples: bool | None = None,
        output_profile: OutputProfile = OutputProfile.AI,
        sample_format: SampleOutputFormat = SampleOutputFormat.MARKDOWN,
    ) -> ExportStatus:
        """Queue lean final materialization; omit object IDs to resolve the complete plan server-side."""
        return router.invoke(
            "sqlctx_export_batch",
            {
                "catalog_id": catalog_id,
                "object_ids": object_ids,
                "idempotency_key": idempotency_key,
                "sqlfluff": sqlfluff,
                "append_samples": append_samples,
                "output_profile": output_profile,
                "sample_format": sample_format,
            },
        )

    @tool("sqlctx_get_export_status")
    def get_export_status(export_id: str) -> ExportStatus:
        """Return progress, terminal error code, bundle integrity, and safe tooling metadata."""
        return router.invoke("sqlctx_get_export_status", {"export_id": export_id})

    @tool("sqlctx_cancel_export")
    def cancel_export(export_id: str) -> ExportStatus:
        """Cooperatively and idempotently cancel an export."""
        return router.invoke("sqlctx_cancel_export", {"export_id": export_id})

    @tool("sqlctx_delete_export")
    def delete_export(export_id: str) -> DeleteResult:
        """Delete an inactive export only after request-bound owner approval."""
        return router.invoke("sqlctx_delete_export", {"export_id": export_id})

    @tool("sqlctx_validate_exports")
    def validate_exports(
        catalog_id: str,
        export_ids: list[str],
        expected_discovered_count: int,
        expected_analyzed_count: int,
        expected_materialized_count: int,
        expected_output_format_version: str,
        assembled_inventory: AssembledInventory,
    ) -> ValidationResult:
        """Compare a complete locally re-read inventory with immutable export manifests."""
        return router.invoke(
            "sqlctx_validate_exports",
            {
                "catalog_id": catalog_id,
                "export_ids": export_ids,
                "expected_discovered_count": expected_discovered_count,
                "expected_analyzed_count": expected_analyzed_count,
                "expected_materialized_count": expected_materialized_count,
                "expected_output_format_version": expected_output_format_version,
                "assembled_inventory": _json(assembled_inventory),
            },
        )

    @tool("sqlctx_sqlfluff_status")
    def sqlfluff_status() -> HostPythonToolingDescriptor:
        """Verify safe host-Python and SQLFluff identity without mutation."""
        return router.invoke("sqlctx_sqlfluff_status", {})

    @tool("sqlctx_sqlfluff_ensure")
    def sqlfluff_ensure() -> HostPythonToolingDescriptor:
        """Ensure pinned SQLFluff on the same host interpreter after owner approval if needed."""
        return router.invoke("sqlctx_sqlfluff_ensure", {})

    @tool("sqlctx_sqlfluff_update")
    def sqlfluff_update(version: str) -> HostPythonToolingDescriptor:
        """Update and self-test SQLFluff on the idle host interpreter after owner approval."""
        return router.invoke("sqlctx_sqlfluff_update", {"version": version})

    @mcp.resource("sqlctx://export/{export_id}/manifest", mime_type="application/json")
    def manifest_resource(export_id: str) -> str:
        """Read the same structured export manifest exposed by HTTP."""
        import json

        return json.dumps(service.exports.manifest(export_id), sort_keys=True)

    @mcp.resource("sqlctx://export/{export_id}/report", mime_type="application/json")
    def report_resource(export_id: str) -> str:
        """Read the same structured export report exposed by HTTP."""
        import json

        return json.dumps(_json(service.exports.report(export_id)), sort_keys=True)

    # FastMCP's generated argument models default to ignoring unknown top-level keys.
    # The v1 contract is stricter: advertise and enforce additionalProperties=false.
    for registered in mcp._tool_manager.list_tools():
        registered.fn_metadata.arg_model.model_config["extra"] = "forbid"
        registered.fn_metadata.arg_model.model_rebuild(force=True)
        registered.parameters = registered.fn_metadata.arg_model.model_json_schema(by_alias=True)

    return mcp
