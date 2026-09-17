"""Loopback FastAPI interface over the shared application facade."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from sqlctx._version import __version__
from sqlctx.context_index.contracts import (
    ContextGenerationPlan,
    ContextGenerationRequest,
    ContextIndexListRequest,
    ContextIndexPage,
    ContextIndexSyncRequest,
    ContextIndexSyncResult,
)
from sqlctx.core.enums import JobStatus
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogJobPage,
    CatalogStatus,
    CategoryPreview,
    ClassificationRequestPage,
    DeleteResult,
    ExportJobPage,
    ExportReport,
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
    RegisteredFolderList,
)
from sqlctx.routine_deploy.contracts import (
    RoutineApplyResult,
    RoutineDeploymentPlan,
    RoutinePlanRequest,
)
from sqlctx.security.audit import OperationAuditLogger
from sqlctx.security.runtime import CredentialMetadataStore
from sqlctx.server.contracts import (
    CapabilitiesResponse,
    ClassificationResolutionBatch,
    ClassificationResolutionBody,
    ConnectionTestResult,
    CreateCatalogRequest,
    ExportCreateRequest,
    ExportManifest,
    HealthResponse,
    ProfileDescriptorList,
    ProposalBody,
    ProposalRequest,
    QueryDataRequest,
    QueryDataResult,
    ResolutionBatchResult,
    SqlFluffEnsureRequest,
    SqlFluffUpdateRequest,
)
from sqlctx.server.facade import ServiceFacade

_ERROR_LOGGER = logging.getLogger("sqlctx.errors")


def _correlation_id() -> str:
    return "corr_" + secrets.token_urlsafe(12)


def _error(error: SqlCtxError, *, correlation_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                **error.public_payload(),
                "correlation_id": correlation_id or _correlation_id(),
            }
        },
    )


def create_app(
    facade: ServiceFacade | None = None,
    *,
    mcp_url: str = "http://127.0.0.1:8765/mcp",
) -> FastAPI:
    service = facade or ServiceFacade()
    metadata_path, _ = CredentialMetadataStore(service.state).ensure(mcp_url)
    metadata = service.state.read_json("connection-metadata.json")
    agent_token = str(metadata["agent_token"])
    caller = "agent:" + hashlib.sha256(agent_token.encode()).hexdigest()[:20]
    app = FastAPI(
        title="SQL Context Pack",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.sqlctx = service
    app.state.connection_metadata_path = metadata_path

    @app.middleware("http")
    async def protect_mcp(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/mcp"):
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not supplied or not secrets.compare_digest(supplied, agent_token):
                return _error(
                    SqlCtxError(
                        "UNAUTHENTICATED", "A valid agent bearer is required.", status_code=401
                    )
                )
        return await call_next(request)

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not supplied or not secrets.compare_digest(supplied, agent_token):
            raise SqlCtxError(
                "UNAUTHENTICATED", "A valid agent bearer is required.", status_code=401
            )
        return caller

    @app.exception_handler(SqlCtxError)
    async def sqlctx_error(_: Request, exc: SqlCtxError) -> JSONResponse:
        return _error(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(
            SqlCtxError(
                "INVALID_REQUEST",
                "Request validation failed.",
                details={
                    "issues": [
                        {"location": item["loc"], "type": item["type"]} for item in exc.errors()
                    ]
                },
            )
        )

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        correlation_id = _correlation_id()
        _ERROR_LOGGER.exception(
            "sqlctx_internal_error correlation_id=%s", correlation_id, exc_info=exc
        )
        return _error(
            SqlCtxError(
                "INTERNAL_ERROR",
                "The service could not complete this operation. No database credentials or traceback were exposed.",
                status_code=500,
                details={
                    "owner_action": (
                        "Run `sqlctx runtime status` and inspect the protected service log using "
                        "this correlation ID. Development tests retain the full traceback in logs."
                    )
                },
            ),
            correlation_id=correlation_id,
        )

    auth = Depends(authenticate)

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health(_: str = auth) -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/capabilities", response_model=CapabilitiesResponse)
    def capabilities(_: str = auth) -> Any:
        return service.capabilities()

    @app.get("/api/v1/profiles", response_model=ProfileDescriptorList)
    def profiles(_: str = auth) -> Any:
        return service.list_profiles()

    @app.post("/api/v1/profiles/{profile}/test", response_model=ConnectionTestResult)
    def test_profile(profile: str, _: str = auth) -> Any:
        return service.test_profile(profile)

    @app.post("/api/v1/query", response_model=QueryDataResult)
    def query_data(body: QueryDataRequest, identity: str = auth) -> Any:
        started = time.perf_counter()
        error_code: str | None = None
        result: QueryDataResult | None = None
        audit = OperationAuditLogger(service.state)
        try:
            result = service.query(body)
            return result
        except SqlCtxError as exc:
            error_code = exc.code
            raise
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            audit.record(
                transport="http",
                caller=identity,
                operation="query.data",
                outcome="failed" if error_code else "succeeded",
                duration_ms=round((time.perf_counter() - started) * 1000),
                error_code=error_code,
                value_mode=body.value_mode,
                returned_row_count=result.returned_row_count if result else 0,
                truncated=result.truncated if result else False,
            )

    @app.get("/api/v1/managed-folders", response_model=RegisteredFolderList)
    def managed_folders(_: str = auth) -> Any:
        return service.list_managed_folders()

    @app.post(
        "/api/v1/managed-folder-plans", status_code=202, response_model=FolderClassificationPlan
    )
    def folder_plan(body: FolderClassificationPlanRequest, _: str = auth) -> Any:
        return service.plan_folder_classification(body)

    @app.post("/api/v1/managed-folder-plans/{plan_id}/apply", response_model=FolderApplyResult)
    def folder_apply(plan_id: str, identity: str = auth) -> Any:
        return service.apply_folder_classification(plan_id, caller=identity)

    @app.post("/api/v1/context-index/search", response_model=ContextIndexPage)
    def context_index_search(body: ContextIndexListRequest, _: str = auth) -> Any:
        return service.list_context_index(body)

    @app.post("/api/v1/context-index/sync", response_model=ContextIndexSyncResult)
    def context_index_sync(body: ContextIndexSyncRequest, identity: str = auth) -> Any:
        return service.sync_context_index(body, caller=identity)

    @app.post(
        "/api/v1/context-index/resolve",
        status_code=202,
        response_model=FolderClassificationPlan,
    )
    def context_index_resolve(body: ManagedFileResolutionPlanRequest, _: str = auth) -> Any:
        return service.resolve_context_index(body)

    @app.post("/api/v1/context-index/generation-plans", response_model=ContextGenerationPlan)
    def context_generation_plan(body: ContextGenerationRequest, _: str = auth) -> Any:
        return service.plan_context_generation(body)

    @app.post("/api/v1/routine-plans", status_code=202, response_model=RoutineDeploymentPlan)
    def routine_plan(body: RoutinePlanRequest, identity: str = auth) -> Any:
        return service.plan_routine_deployment(body, caller=identity)

    @app.post("/api/v1/routine-plans/{plan_id}/apply", response_model=RoutineApplyResult)
    def routine_apply(plan_id: str, identity: str = auth) -> Any:
        return service.apply_routine_deployment(plan_id, caller=identity)

    @app.get("/api/v1/catalogs", response_model=CatalogJobPage)
    def list_catalogs(
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=250),
        _: str = auth,
    ) -> Any:
        return service.catalogs.list_jobs(status=status, cursor=cursor, limit=limit)

    @app.post("/api/v1/catalogs", status_code=202, response_model=CatalogStatus)
    def create_catalog(
        body: CreateCatalogRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        identity: str = auth,
    ) -> Any:
        result, _ = service.create_catalog(body, caller=identity, idempotency_key=idempotency_key)
        return result

    @app.get("/api/v1/catalogs/{catalog_id}", response_model=CatalogStatus)
    def catalog_status(catalog_id: str, _: str = auth) -> Any:
        return service.catalogs.status(catalog_id)

    @app.post("/api/v1/catalogs/{catalog_id}/cancel", response_model=CatalogStatus)
    def cancel_catalog(catalog_id: str, _: str = auth) -> Any:
        return service.catalogs.cancel(catalog_id)

    @app.delete("/api/v1/catalogs/{catalog_id}", response_model=DeleteResult)
    def delete_catalog(catalog_id: str, identity: str = auth) -> Any:
        return service.delete_catalog(catalog_id, caller=identity)

    @app.get("/api/v1/catalogs/{catalog_id}/category-preview", response_model=CategoryPreview)
    def category_preview(
        catalog_id: str,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=250),
        _: str = auth,
    ) -> Any:
        return service.catalogs.category_preview(catalog_id, cursor=cursor, limit=limit)

    @app.post(
        "/api/v1/catalogs/{catalog_id}/selection",
        status_code=202,
        response_model=CatalogStatus,
    )
    def select_catalog(catalog_id: str, body: MaterializationSelection, _: str = auth) -> Any:
        status = service.catalogs.select(catalog_id, body)
        service.classifications.classify(catalog_id, body)
        return status

    @app.get("/api/v1/catalogs/{catalog_id}/sitemap", response_model=SitemapPage)
    def sitemap(
        catalog_id: str,
        view: str,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=250),
        _: str = auth,
    ) -> Any:
        return service.catalogs.sitemap(catalog_id, view=view, cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/catalogs/{catalog_id}/materialization-plan",
        response_model=MaterializationPlan,
    )
    def materialization_plan(catalog_id: str, _: str = auth) -> Any:
        return service.classifications.materialization_plan(catalog_id)

    @app.get(
        "/api/v1/catalogs/{catalog_id}/classification-requests",
        response_model=ClassificationRequestPage,
    )
    def classification_requests(
        catalog_id: str,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=250),
        _: str = auth,
    ) -> Any:
        return service.classifications.requests(catalog_id, cursor=cursor, limit=limit)

    @app.post(
        "/api/v1/catalogs/{catalog_id}/classification-proposals",
        response_model=ProposalBatchResult,
    )
    def proposals(catalog_id: str, body: ProposalBody, _: str = auth) -> Any:
        return service.submit_proposals(ProposalRequest(catalog_id=catalog_id, **body.model_dump()))

    @app.post(
        "/api/v1/catalogs/{catalog_id}/classification-resolutions",
        response_model=ResolutionBatchResult,
    )
    def resolutions(
        catalog_id: str, body: ClassificationResolutionBody, identity: str = auth
    ) -> Any:
        return service.resolve(
            ClassificationResolutionBatch(catalog_id=catalog_id, **body.model_dump()),
            caller=identity,
        )

    @app.get("/api/v1/exports", response_model=ExportJobPage)
    def list_exports(
        catalog_id: str | None = None,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=250),
        _: str = auth,
    ) -> Any:
        return service.exports.list_jobs(
            catalog_id=catalog_id, status=status, cursor=cursor, limit=limit
        )

    @app.post("/api/v1/exports", status_code=202, response_model=ExportStatus)
    def create_export(
        body: ExportCreateRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        identity: str = auth,
    ) -> Any:
        result, _ = service.create_export(body, caller=identity, idempotency_key=idempotency_key)
        return result

    @app.get("/api/v1/exports/{export_id}", response_model=ExportStatus)
    def export_status(export_id: str, _: str = auth) -> Any:
        return service.exports.status(export_id)

    @app.post("/api/v1/exports/{export_id}/cancel", response_model=ExportStatus)
    def cancel_export(export_id: str, _: str = auth) -> Any:
        return service.exports.cancel(export_id)

    @app.delete("/api/v1/exports/{export_id}", response_model=DeleteResult)
    def delete_export(export_id: str, identity: str = auth) -> Any:
        return service.delete_export(export_id, caller=identity)

    @app.get(
        "/api/v1/exports/{export_id}/bundle",
        response_class=FileResponse,
        responses={
            200: {
                "description": "Integrity-protected SQL Context Pack ZIP bundle.",
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    def bundle(export_id: str, _: str = auth) -> FileResponse:
        return FileResponse(
            service.exports.artifact_path(export_id),
            media_type="application/zip",
            filename=f"{export_id}.sqlctx.zip",
        )

    @app.get("/api/v1/exports/{export_id}/manifest", response_model=ExportManifest)
    def manifest(export_id: str, _: str = auth) -> Any:
        return service.exports.manifest(export_id)

    @app.get("/api/v1/exports/{export_id}/report", response_model=ExportReport)
    def report(export_id: str, _: str = auth) -> Any:
        return service.exports.report(export_id)

    @app.post("/api/v1/validations", response_model=ValidationResult)
    def validate(body: ValidationRequest, _: str = auth) -> Any:
        return service.validate(body)

    @app.get("/api/v1/tooling/sqlfluff", response_model=HostPythonToolingDescriptor)
    def sqlfluff_status(_: str = auth) -> Any:
        return service.manager.status()

    @app.post("/api/v1/tooling/sqlfluff/ensure", response_model=HostPythonToolingDescriptor)
    def sqlfluff_ensure(_: SqlFluffEnsureRequest, identity: str = auth) -> Any:
        return service.tooling_ensure(caller=identity)

    @app.post("/api/v1/tooling/sqlfluff/update", response_model=HostPythonToolingDescriptor)
    def sqlfluff_update(body: SqlFluffUpdateRequest, identity: str = auth) -> Any:
        return service.tooling_update(body.version, caller=identity)

    from sqlctx.server.mcp.server import build_mcp

    mcp = build_mcp(service, caller)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def combined_lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app.router.lifespan_context = combined_lifespan
    app.mount("/", mcp_app)
    return app


def run() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        description="Run the owner-started SQL Context Pack loopback service."
    )
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    mcp_url = f"http://{args.host}:{args.port}/mcp"
    app = create_app(mcp_url=mcp_url)
    print(mcp_url)
    print(str(Path(app.state.connection_metadata_path)))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
