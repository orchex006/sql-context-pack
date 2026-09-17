from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlctx.classification.classifier import ClassificationRun
from sqlctx.core.models import (
    CatalogSnapshot,
    CatalogStatus,
    ExportBatchRequest,
    HostPythonToolingDescriptor,
    MaterializationPlan,
    MaterializationPlanItem,
    MaterializationSelection,
)
from sqlctx.exporting.service import ExportService
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore


class DeferredExecutor:
    def __init__(self) -> None:
        self.submission: tuple[object, tuple[object, ...]] | None = None

    def submit(self, function: object, *args: object) -> None:
        self.submission = (function, args)


class Catalogs:
    def __init__(self) -> None:
        self.snapshot = CatalogSnapshot(
            catalog_id="cat_1",
            profile_name="demo",
            request_fingerprint="sha256:req",
            status="ready",
        )

    def get_snapshot(self, _: str) -> CatalogSnapshot:
        return self.snapshot

    def status(self, _: str) -> CatalogStatus:
        return CatalogStatus(catalog_id="cat_1", status="ready", request_fingerprint="sha256:req")

    def pin_export(self, *_: object, **__: object) -> None:
        pass


class Classifications:
    def get_run(self, _: str) -> ClassificationRun:
        return ClassificationRun(
            catalog_id="cat_1", results=[], evidence=[], changes=[], categories=[]
        )

    def materialization_plan(self, _: str) -> MaterializationPlan:
        return MaterializationPlan(
            catalog_id="cat_1",
            selection=MaterializationSelection(mode="all"),
            items=[
                MaterializationPlanItem(
                    object_id="table:app.T", final_category="um", included=True, reason="all_mode"
                )
            ],
        )


class Manager:
    def status(self) -> HostPythonToolingDescriptor:
        return HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.13.14",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tooling",
            ready=True,
        )


def test_export_creation_returns_queued_status_before_worker_runs(tmp_path: Path) -> None:
    executor = DeferredExecutor()
    service = ExportService(
        JsonRuntimeStateStore(tmp_path / "runtime"),
        Catalogs(),  # type: ignore[arg-type]
        Classifications(),  # type: ignore[arg-type]
        Manager(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ApprovalService(),
        executor=executor,  # type: ignore[arg-type]
    )

    before = datetime.now(UTC)
    status = service.create(ExportBatchRequest(catalog_id="cat_1", object_ids=["table:app.T"]))

    assert status.status == "queued"
    assert status.created_at >= before
    assert status.requested_object_count == 1
    assert status.processed_object_count == 0
    assert status.output_profile == "ai"
    assert executor.submission is not None
