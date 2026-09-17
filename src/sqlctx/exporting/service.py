"""Export job orchestration, artifact retention, and final validation."""

from __future__ import annotations

import hashlib
import json
import secrets
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlctx._version import OUTPUT_FORMAT_VERSION
from sqlctx.application.catalog import CatalogService
from sqlctx.application.pagination import page_slice
from sqlctx.classification.classifier import ClassificationRun, ClassificationService
from sqlctx.core.enums import FormatStatus, JobStatus
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogSnapshot,
    DeleteResult,
    ExportArtifact,
    ExportBatchRequest,
    ExportJob,
    ExportJobDescriptor,
    ExportJobPage,
    ExportObjectCounts,
    ExportReport,
    ExportStatus,
    MaterializationPlan,
    ValidationRequest,
    ValidationResult,
)
from sqlctx.exporting.assembly import (
    AGGREGATED_PATHS,
    AI_AGGREGATED_PATHS,
    FULL_AGGREGATED_PATHS,
)
from sqlctx.exporting.writer import OutputPackageWriter, canonical_json, sha256_bytes
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore, _atomic_write


def _now() -> datetime:
    return datetime.now(UTC)


class ExportService:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        catalogs: CatalogService,
        classifications: ClassificationService,
        manager: SqlFluffManager,
        writer: OutputPackageWriter,
        approvals: ApprovalService,
        *,
        completed_ttl_hours: int = 24,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.state = state
        self.catalogs = catalogs
        self.classifications = classifications
        self.manager = manager
        self.writer = writer
        self.approvals = approvals
        self.completed_ttl = timedelta(hours=completed_ttl_hours)
        self.executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="sqlctx-export"
        )
        self._recover_interrupted_jobs()

    def _recover_interrupted_jobs(self) -> None:
        for job in self._all_jobs():
            if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                continue
            artifact = self.state.read_json(f"exports/{job.export_id}/artifact.json")
            report = self.state.read_json(f"exports/{job.export_id}/report.json")
            manifest = self.state.read_json(f"exports/{job.export_id}/manifest.json")
            if artifact and report and manifest:
                warnings = report.get("warnings", []) if isinstance(report, dict) else []
                results = report.get("object_results", []) if isinstance(report, dict) else []
                job.status = (
                    JobStatus.PARTIAL
                    if any(item.get("status") == "skipped_security" for item in results)
                    else JobStatus.COMPLETED_WITH_WARNINGS
                    if warnings
                    else JobStatus.COMPLETED
                )
                job.processed_object_count = job.requested_object_count
                job.error_code = None
            else:
                job.status = JobStatus.FAILED
                job.error_code = "EXPORT_WORKER_INTERRUPTED"
            job.completed_at = _now()
            job.phase = "completed" if artifact and report and manifest else "failed"
            job.current_object_id = None
            self._write_job(job)
            with suppress(SqlCtxError):
                self.catalogs.pin_export(
                    job.catalog_id, job.export_id, job.expires_at or _now(), active=False
                )

    @staticmethod
    def _batch_fingerprint(object_ids: list[str]) -> str:
        return (
            "sha256:"
            + hashlib.sha256(json.dumps(object_ids, separators=(",", ":")).encode()).hexdigest()
        )

    def create(self, request: ExportBatchRequest) -> ExportStatus:
        if len(request.object_ids) != len(set(request.object_ids)):
            raise SqlCtxError(
                "DUPLICATE_OBJECT_ID", "Export batches cannot contain duplicate object IDs."
            )
        snapshot = self.catalogs.get_snapshot(request.catalog_id)
        classification = self.classifications.get_run(request.catalog_id)
        plan = self.classifications.materialization_plan(request.catalog_id)
        tooling = self.manager.status()
        if not tooling.ready or not tooling.sqlfluff_version or not tooling.tooling_fingerprint:
            raise SqlCtxError(
                "TOOLING_UNAVAILABLE", "Pinned SQLFluff is not ready.", status_code=503
            )
        batch_fingerprint = self._batch_fingerprint(request.object_ids)
        fingerprint_payload = {
            "catalog_id": request.catalog_id,
            "object_batch_fingerprint": batch_fingerprint,
            "format_policy": "mandatory-per-file-v1",
            "sample_policy": "sanitized-files-v2",
            "output_profile": request.output_profile,
            "sample_format": request.sample_format,
            "python_executable_fingerprint": tooling.python_executable_fingerprint,
            "tooling_fingerprint": tooling.tooling_fingerprint,
            "output_format_version": OUTPUT_FORMAT_VERSION,
        }
        request_fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        export_id = "exp_" + secrets.token_urlsafe(16)
        created = _now()
        expires = created + self.completed_ttl
        job = ExportJob(
            export_id=export_id,
            catalog_id=request.catalog_id,
            status=JobStatus.QUEUED,
            request_fingerprint=request_fingerprint,
            object_batch_fingerprint=batch_fingerprint,
            python_executable_fingerprint=tooling.python_executable_fingerprint,
            python_version=tooling.python_version,
            sqlfluff_version=tooling.sqlfluff_version,
            tooling_fingerprint=tooling.tooling_fingerprint,
            output_profile=request.output_profile,
            sample_format=request.sample_format,
            requested_object_count=len(request.object_ids),
            created_at=created,
            expires_at=expires,
        )
        self._write_job(job)
        self.catalogs.pin_export(request.catalog_id, export_id, expires, active=True)
        try:
            self.executor.submit(
                self._execute_export,
                job,
                snapshot,
                classification,
                plan,
                request,
            )
        except Exception:
            job.status = JobStatus.FAILED
            job.error_code = "EXPORT_DISPATCH_FAILED"
            job.completed_at = _now()
            self._write_job(job)
            self.catalogs.pin_export(request.catalog_id, export_id, expires, active=False)
            raise
        return self.status(export_id)

    def _execute_export(
        self,
        job: ExportJob,
        snapshot: CatalogSnapshot,
        classification: ClassificationRun,
        plan: MaterializationPlan,
        request: ExportBatchRequest,
    ) -> None:
        try:
            current = self._job(job.export_id)
            if current.status == JobStatus.CANCELLED:
                return
            current.status = JobStatus.RUNNING
            current.phase = "formatting"
            current.started_at = _now()
            current.last_progress_at = _now()
            self._write_job(current)

            def progress(
                processed: int, object_id: str, status: str, error_code: str | None
            ) -> None:
                progress_job = self._job(job.export_id)
                if progress_job.status == JobStatus.CANCELLED:
                    raise SqlCtxError("EXPORT_CANCELLED", "Export was cancelled.")
                progress_job.processed_object_count = processed
                progress_job.current_object_id = object_id
                if status == "skipped_security":
                    progress_job.skipped_object_count += 1
                if status == FormatStatus.FORMATTED:
                    pass
                progress_job.last_progress_at = _now()
                self._write_job(progress_job)
                checkpoint_key = hashlib.sha256(object_id.encode()).hexdigest()
                self.state.write_json(
                    f"exports/{job.export_id}/checkpoints/{checkpoint_key}.json",
                    {
                        "object_id": object_id,
                        "position": processed,
                        "status": status,
                        "error_code": error_code,
                        "updated_at": progress_job.last_progress_at.isoformat(),
                    },
                )

            with self.manager.pin_for_job() as pinned:
                package = self.writer.build(
                    export_id=job.export_id,
                    snapshot=snapshot,
                    catalog_status=self.catalogs.status(request.catalog_id),
                    classifications=classification,
                    plan=plan,
                    object_ids=request.object_ids,
                    tooling=pinned,
                    created_at=job.created_at,
                    output_profile=request.output_profile,
                    sample_format=request.sample_format,
                    progress=progress,
                )
            current = self._job(job.export_id)
            if current.status == JobStatus.CANCELLED:
                return
            artifact_path = self.state._safe(f"exports/{job.export_id}/{job.export_id}.sqlctx.zip")
            _atomic_write(artifact_path, package.bundle)
            manifest_bytes = package.files["manifest.yaml"]
            artifact = ExportArtifact(
                export_id=job.export_id,
                size_bytes=len(package.bundle),
                sha256=sha256_bytes(package.bundle),
                manifest_sha256=sha256_bytes(manifest_bytes),
                bundle_url=f"/api/v1/exports/{job.export_id}/bundle",
                manifest_url=f"/api/v1/exports/{job.export_id}/manifest",
                report_url=f"/api/v1/exports/{job.export_id}/report",
            )
            if package.skipped_objects:
                current.status = JobStatus.PARTIAL
            elif any(
                item.status != FormatStatus.FORMATTED or item.diagnostics
                for item in package.format_results
            ):
                current.status = JobStatus.COMPLETED_WITH_WARNINGS
            else:
                current.status = JobStatus.COMPLETED
            current.phase = "completed"
            current.current_object_id = None
            current.processed_object_count = current.requested_object_count
            current.reused_object_count = sum(item.cache_hit for item in package.format_results)
            current.skipped_object_count = len(package.skipped_objects)
            current.failed_object_count = sum(
                item.status in {FormatStatus.FORMAT_FAILED, FormatStatus.ROLLED_BACK}
                for item in package.format_results
            )
            current.warning_count = len(package.report["warnings"])
            current.last_progress_at = _now()
            current.completed_at = current.last_progress_at
            self.state.write_json(
                f"exports/{job.export_id}/artifact.json", artifact.model_dump(mode="json")
            )
            self.state.write_json(f"exports/{job.export_id}/manifest.json", package.manifest)
            report = ExportReport(
                export_id=job.export_id,
                catalog_id=request.catalog_id,
                status=current.status,
                object_results=package.report["objects"],
                warnings=package.report["warnings"],
            )
            self.state.write_json(
                f"exports/{job.export_id}/report.json", report.model_dump(mode="json")
            )
            self._write_job(current)
        except SqlCtxError as exc:
            failed = self._job(job.export_id)
            if failed.status != JobStatus.CANCELLED:
                failed.status = JobStatus.FAILED
                failed.phase = "failed"
                failed.current_object_id = None
                failed.error_code = exc.code
                failed.completed_at = _now()
                self._write_job(failed)
        except Exception:
            failed = self._job(job.export_id)
            failed.status = JobStatus.FAILED
            failed.phase = "failed"
            failed.current_object_id = None
            failed.error_code = "INTERNAL_ERROR"
            failed.completed_at = _now()
            self._write_job(failed)
        finally:
            self.catalogs.pin_export(
                request.catalog_id, job.export_id, job.expires_at or _now(), active=False
            )

    def status(self, export_id: str) -> ExportStatus:
        job = self._job(export_id)
        raw_artifact = self.state.read_json(f"exports/{export_id}/artifact.json")
        artifact = ExportArtifact.model_validate(raw_artifact) if raw_artifact else None
        report = self.state.read_json(f"exports/{export_id}/report.json", {"object_results": []})
        results = report["object_results"]
        job_payload = job.model_dump(mode="json")
        job_payload["requested_object_count"] = job.requested_object_count or len(results)
        job_payload["processed_object_count"] = max(job.processed_object_count, len(results))
        completed_or_now = job.completed_at or _now()
        started = job.started_at or job.created_at
        elapsed = max(0.0, (completed_or_now - started).total_seconds())
        processed = int(job_payload["processed_object_count"])
        remaining = max(0, int(job_payload["requested_object_count"]) - processed)
        eta = (elapsed / processed * remaining) if processed and job.completed_at is None else None
        return ExportStatus(
            **job_payload,
            size_bytes=artifact.size_bytes if artifact else None,
            sha256=artifact.sha256 if artifact else None,
            manifest_sha256=artifact.manifest_sha256 if artifact else None,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            objects=ExportObjectCounts(
                requested=int(job_payload["requested_object_count"]),
                succeeded=sum(item.get("status") != "skipped_security" for item in results),
                parse_failed=sum(
                    item.get("status") == FormatStatus.PARSE_FAILED for item in results
                ),
                failed=sum(
                    item.get("status") in {FormatStatus.FORMAT_FAILED, FormatStatus.ROLLED_BACK}
                    for item in results
                ),
                skipped_security=sum(item.get("status") == "skipped_security" for item in results),
                reused=job.reused_object_count,
            ),
            artifacts=artifact,
        )

    def list_jobs(
        self,
        *,
        catalog_id: str | None = None,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ExportJobPage:
        jobs = self._all_jobs()
        if catalog_id:
            jobs = [item for item in jobs if item.catalog_id == catalog_id]
        if status:
            jobs = [item for item in jobs if item.status == status]
        descriptors = []
        for job in sorted(jobs, key=lambda item: item.created_at, reverse=True):
            raw = self.state.read_json(f"exports/{job.export_id}/artifact.json")
            descriptors.append(
                ExportJobDescriptor(
                    export=job, artifact=ExportArtifact.model_validate(raw) if raw else None
                )
            )
        items, page = page_slice(descriptors, cursor=cursor, limit=limit)
        return ExportJobPage(items=items, page=page)

    def cancel(self, export_id: str) -> ExportStatus:
        job = self._job(export_id)
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            job.status = JobStatus.CANCELLED
            job.phase = "cancelled"
            job.current_object_id = None
            job.last_progress_at = _now()
            job.completed_at = job.last_progress_at
            self._write_job(job)
        return self.status(export_id)

    def artifact_path(self, export_id: str) -> Path:
        self._job(export_id)
        path = self.state._safe(f"exports/{export_id}/{export_id}.sqlctx.zip")
        if not path.is_file():
            raise SqlCtxError(
                "EXPORT_ARTIFACT_NOT_FOUND", "Export bundle is not available.", status_code=404
            )
        return path

    def manifest(self, export_id: str) -> dict[str, Any]:
        value = self.state.read_json(f"exports/{export_id}/manifest.json")
        if value is None:
            raise SqlCtxError(
                "EXPORT_ARTIFACT_NOT_FOUND", "Export manifest is not available.", status_code=404
            )
        if not isinstance(value, dict):
            raise SqlCtxError("RUNTIME_STATE_CORRUPT", "Export manifest state is invalid.")
        return cast(dict[str, Any], value)

    def report(self, export_id: str) -> ExportReport:
        value = self.state.read_json(f"exports/{export_id}/report.json")
        if value is None:
            raise SqlCtxError(
                "EXPORT_ARTIFACT_NOT_FOUND", "Export report is not available.", status_code=404
            )
        return ExportReport.model_validate(value)

    def validate(self, request: ValidationRequest) -> ValidationResult:
        errors: list[str] = []
        catalog = self.catalogs.status(request.catalog_id)
        versions = []
        output_profiles: set[str] = set()
        declared: dict[str, tuple[int, str]] = {}
        manifest_hashes_valid = True
        for export_id in request.export_ids:
            job = self._job(export_id)
            if job.catalog_id != request.catalog_id:
                errors.append("export_catalog_mismatch")
                continue
            manifest = self.manifest(export_id)
            artifact = self.status(export_id).artifacts
            if artifact is None:
                manifest_hashes_valid = False
            else:
                bundle_path = self.artifact_path(export_id)
                try:
                    with zipfile.ZipFile(bundle_path) as archive:
                        manifest_bytes = archive.read("manifest.yaml")
                    manifest_hashes_valid = manifest_hashes_valid and (
                        bundle_path.stat().st_size == artifact.size_bytes
                        and sha256_bytes(bundle_path.read_bytes()) == artifact.sha256
                        and sha256_bytes(manifest_bytes) == artifact.manifest_sha256
                    )
                except (OSError, KeyError, zipfile.BadZipFile):
                    manifest_hashes_valid = False
            versions.append(str(manifest.get("output_format_version")))
            export_metadata = manifest.get("export", {})
            if isinstance(export_metadata, dict):
                output_profiles.add(str(export_metadata.get("output_profile", "full")))
            managed_files = manifest.get("managed_files", [])
            if not isinstance(managed_files, list):
                errors.append("managed_manifest_invalid")
                continue
            for raw_item in managed_files:
                item = cast(dict[str, Any], raw_item)
                path = str(item["path"])
                if path in AGGREGATED_PATHS:
                    continue
                value = (int(item["size_bytes"]), str(item["sha256"]))
                if path in declared and declared[path] != value:
                    errors.append(f"conflicting_managed_path:{path}")
                declared[path] = value
        submitted = {
            item.relative_path: (item.size_bytes, item.sha256)
            for item in request.assembled_inventory.files
            if item.relative_path != "manifest.yaml"
        }
        canonical_inventory = [
            item.model_dump(mode="json", by_alias=True)
            for item in sorted(
                request.assembled_inventory.files, key=lambda item: item.relative_path
            )
        ]
        manifest_item = next(
            (
                item
                for item in request.assembled_inventory.files
                if item.relative_path == "manifest.yaml"
            ),
            None,
        )
        submitted_hashes_valid = (
            manifest_item is not None
            and manifest_item.sha256 == request.assembled_inventory.managed_manifest_sha256
            and sha256_bytes(canonical_json(canonical_inventory))
            == request.assembled_inventory.inventory_sha256
        )
        if output_profiles == {"ai"}:
            aggregate_reports = AI_AGGREGATED_PATHS - {"manifest.yaml"}
        elif output_profiles == {"full"}:
            aggregate_reports = FULL_AGGREGATED_PATHS - {"manifest.yaml"}
        else:
            aggregate_reports = set()
            errors.append("output_profile_mismatch")
        inventory_complete = (
            all(submitted.get(path) == value for path, value in declared.items())
            and aggregate_reports <= set(submitted)
            and set(submitted) <= set(declared) | aggregate_reports
            and any(
                item.relative_path == "manifest.yaml" for item in request.assembled_inventory.files
            )
        )
        inventory_complete = inventory_complete and submitted_hashes_valid
        if not inventory_complete:
            errors.append("assembled_inventory_mismatch")
        output_version_valid = (
            request.expected_output_format_version == OUTPUT_FORMAT_VERSION
            and all(item == OUTPUT_FORMAT_VERSION for item in versions)
        )
        if not output_version_valid:
            errors.append("output_format_version_mismatch")
        discovered_count_valid = (
            catalog.discovered_object_count == request.expected_discovered_count
        )
        analyzed_count_valid = (
            catalog.fully_analyzed_object_count == request.expected_analyzed_count
        )
        materialized_count_valid = (
            catalog.materialized_object_count == request.expected_materialized_count
        )
        exclusion_count_valid = (
            catalog.fully_analyzed_object_count
            == catalog.materialized_object_count + catalog.intentionally_excluded_object_count
        )
        for valid, code in (
            (discovered_count_valid, "discovered_count_mismatch"),
            (analyzed_count_valid, "analyzed_count_mismatch"),
            (materialized_count_valid, "materialized_count_mismatch"),
            (exclusion_count_valid, "exclusion_count_mismatch"),
            (manifest_hashes_valid, "export_manifest_hash_mismatch"),
        ):
            if not valid:
                errors.append(code)
        return ValidationResult(
            valid=not errors,
            discovered_count_valid=discovered_count_valid,
            analyzed_count_valid=analyzed_count_valid,
            materialized_count_valid=materialized_count_valid,
            exclusion_count_valid=exclusion_count_valid,
            output_format_version_valid=output_version_valid,
            manifest_hashes_valid=manifest_hashes_valid,
            assembled_inventory_complete=inventory_complete,
            assembled_files_match_export_manifests=inventory_complete,
            errors=errors,
        )

    def delete(self, export_id: str, *, caller: str, approval_id: str) -> DeleteResult:
        job = self._job(export_id)
        if job.status in {JobStatus.RUNNING, JobStatus.QUEUED}:
            raise SqlCtxError(
                "JOB_ACTIVE", "Active export jobs cannot be deleted.", status_code=409
            )
        payload = {"export_id": export_id}
        self.approvals.consume(
            approval_id, caller=caller, operation="export.delete", target=export_id, payload=payload
        )
        directory = self.state._safe(f"exports/{export_id}")
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
        self.catalogs.release_export(job.catalog_id, export_id)
        return DeleteResult(deleted=True, target_id=export_id)

    def delete_authorized(self, export_id: str) -> DeleteResult:
        job = self._job(export_id)
        if job.status in {JobStatus.RUNNING, JobStatus.QUEUED}:
            raise SqlCtxError(
                "JOB_ACTIVE", "Active export jobs cannot be deleted.", status_code=409
            )
        directory = self.state._safe(f"exports/{export_id}")
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
        self.catalogs.release_export(job.catalog_id, export_id)
        return DeleteResult(deleted=True, target_id=export_id)

    def cleanup_expired(self) -> list[str]:
        """Remove only expired inactive export artifacts and release catalog pins."""
        removed: list[str] = []
        now = _now()
        for job in self._all_jobs():
            if (
                job.expires_at is None
                or job.expires_at > now
                or job.status
                in {
                    JobStatus.RUNNING,
                    JobStatus.QUEUED,
                }
            ):
                continue
            directory = self.state._safe(f"exports/{job.export_id}")
            for path in sorted(directory.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            directory.rmdir()
            self.catalogs.release_export(job.catalog_id, job.export_id)
            removed.append(job.export_id)
        return removed

    def _job(self, export_id: str) -> ExportJob:
        value = self.state.read_json(f"exports/{export_id}/job.json")
        if value is None:
            raise SqlCtxError("EXPORT_NOT_FOUND", "Export job was not found.", status_code=404)
        return ExportJob.model_validate(value)

    def _write_job(self, job: ExportJob) -> None:
        self.state.write_json(f"exports/{job.export_id}/job.json", job.model_dump(mode="json"))

    def _all_jobs(self) -> list[ExportJob]:
        root = self.state._safe("exports")
        if not root.exists():
            return []
        result = []
        for path in root.glob("*/job.json"):
            try:
                result.append(ExportJob.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise SqlCtxError(
                    "RUNTIME_STATE_CORRUPT", "A retained export record is unreadable."
                ) from exc
        return result
