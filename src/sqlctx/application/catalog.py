"""Two-phase full-analysis catalog orchestration and retention."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from threading import Event, Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sqlctx.adapters.base import BaseDatabaseAdapter
from sqlctx.application.pagination import page_slice
from sqlctx.core.enums import (
    ClassificationPass,
    InclusionReason,
    JobStatus,
    MaterializationMode,
    ObjectType,
)
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogFailureStage,
    CatalogJobDescriptor,
    CatalogJobPage,
    CatalogObjectFailure,
    CatalogSnapshot,
    CatalogStatus,
    CategoryPreview,
    CategoryPreviewGroup,
    DatabaseObject,
    DeleteResult,
    DependencyEdge,
    MaterializationPlan,
    MaterializationPlanItem,
    MaterializationSelection,
    ObjectRef,
    ResolvedConnectionProfile,
    SamplePage,
    SitemapItem,
    SitemapPage,
)
from sqlctx.security.masking import DeterministicMaskingEngine, scan_and_redact_sql_literals
from sqlctx.security.runtime import JsonRuntimeStateStore

_FAILURE_MESSAGE_LIMIT = 200
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![\w.])/)[^\s'\"]*")
_SQL_BODY_MARKERS = re.compile(
    r"(?is)\b(?:CREATE|ALTER)\s+(?:OR\s+ALTER\s+)?(?:PROCEDURE|PROC|FUNCTION|TABLE|VIEW)\b.*"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_failure_message(message: str) -> str:
    """Reduce one adapter error to a short operator-readable reason with no protected content."""
    text = " ".join(message.split())
    text = _SQL_BODY_MARKERS.sub("[REDACTED]", text)
    text, _ = scan_and_redact_sql_literals(text)
    text = _ABSOLUTE_PATH.sub("[REDACTED]", text)
    if len(text) > _FAILURE_MESSAGE_LIMIT:
        text = text[: _FAILURE_MESSAGE_LIMIT - 1].rstrip() + "…"
    return text or "No sanitized reason is available."


def _matches_included(object_name: str, pattern: str) -> bool:
    """Match an owner-requested include pattern without depending on database name casing."""
    return fnmatchcase(object_name.casefold(), pattern.casefold())


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    schemas: list[str] = Field(min_length=1)
    object_types: list[ObjectType] = Field(
        default_factory=lambda: [ObjectType.TABLE, ObjectType.PROCEDURE, ObjectType.FUNCTION]
    )
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    sample_rows_per_table: int = Field(default=10, ge=10, le=20)
    masking_policy: str = "strict"
    classification_policy_version: str = "two-pass-v1"
    selection: MaterializationSelection = Field(
        default_factory=lambda: MaterializationSelection(mode=MaterializationMode.ASK)
    )

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


class CatalogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: str
    request: CatalogRequest
    request_fingerprint: str
    session_cache_key: str | None = None
    source_schema_fingerprint: str | None = None
    source_object_fingerprints: dict[str, str] = Field(default_factory=dict)
    status: JobStatus
    selection: MaterializationSelection | None = None
    created_at: datetime
    expires_at: datetime
    dependent_exports: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cancelled: bool = False
    phase: str = "discovery"
    total_object_count: int = Field(default=0, ge=0)
    processed_object_count: int = Field(default=0, ge=0)
    reused_object_count: int = Field(default=0, ge=0)
    current_object_id: str | None = None
    started_at: datetime | None = None
    last_progress_at: datetime | None = None


@dataclass(frozen=True)
class CompleteCatalogContextScope:
    """Application-verified identity inventory for one complete profile scope."""

    schemas: tuple[str, ...]
    object_types: tuple[ObjectType, ...]
    object_ids: frozenset[str]


class CatalogService:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        masker: DeterministicMaskingEngine,
        *,
        completed_ttl_hours: int = 24,
        max_total_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self.state = state
        self.masker = masker
        self.completed_ttl = timedelta(hours=completed_ttl_hours)
        self.max_total_bytes = max_total_bytes
        self._contexts: dict[str, tuple[ResolvedConnectionProfile, BaseDatabaseAdapter]] = {}
        self._cancel: dict[str, Event] = {}
        self._lock = Lock()
        self._recover_interrupted_jobs()

    def _recover_interrupted_jobs(self) -> None:
        """Reconcile stale work without discarding completed per-object checkpoints."""
        for record in self._all_records():
            if record.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                continue
            record.status = JobStatus.AWAITING_SELECTION
            record.phase = "interrupted_resume_available"
            record.current_object_id = None
            self._write_record(record)

    def create(
        self,
        request: CatalogRequest,
        profile: ResolvedConnectionProfile,
        adapter: BaseDatabaseAdapter,
        *,
        session_cache_key: str | None = None,
        source_schema_fingerprint: str | None = None,
        source_object_fingerprints: dict[str, str] | None = None,
        force_refresh: bool = False,
    ) -> CatalogStatus:
        if request.selection.mode == MaterializationMode.ALL and request.include_patterns:
            raise SqlCtxError(
                "ALL_MODE_INCLUDE_FILTER_CONFLICT",
                "All-mode catalog requests cannot use include patterns.",
                details={"include_pattern_count": len(request.include_patterns)},
            )
        if set(request.schemas) - set(profile.allowed_schemas):
            raise SqlCtxError(
                "SCHEMA_NOT_ALLOWED",
                "Catalog request exceeds the profile schema allowlist.",
                status_code=403,
            )
        self.cleanup_expired()
        request_fingerprint = request.fingerprint()
        if not force_refresh and session_cache_key and source_schema_fingerprint:
            cached = self._cached_record(
                session_cache_key=session_cache_key,
                request_fingerprint=request_fingerprint,
                source_schema_fingerprint=source_schema_fingerprint,
            )
            if cached is not None:
                with self._lock:
                    self._contexts[cached.catalog_id] = (profile, adapter)
                    self._cancel.setdefault(cached.catalog_id, Event())
                return self.status(cached.catalog_id).model_copy(update={"cache_hit": True})
        self._ensure_quota()
        catalog_id = "cat_" + secrets.token_urlsafe(16)
        in_scope = [
            ref
            for ref in adapter.discover_objects(profile)
            if ref.schema_name in set(request.schemas)
            and ref.object_type in set(request.object_types)
        ]
        candidates = [
            ref
            for ref in in_scope
            if not any(
                fnmatchcase(ref.object_name, pattern) for pattern in request.exclude_patterns
            )
        ]
        if request.include_patterns:
            unmatched = sorted(
                pattern
                for pattern in request.include_patterns
                if not any(_matches_included(ref.object_name, pattern) for ref in candidates)
            )
            if unmatched:
                raise SqlCtxError(
                    "CATALOG_INCLUDE_PATTERNS_UNMATCHED",
                    "Some requested object names matched no profile-allowed object.",
                    status_code=404,
                    details={
                        "unmatched_patterns": unmatched,
                        "excluded_by_policy_patterns": sorted(
                            pattern
                            for pattern in unmatched
                            if any(_matches_included(ref.object_name, pattern) for ref in in_scope)
                        ),
                    },
                )
        refs = [
            ref
            for ref in candidates
            if not request.include_patterns
            or any(
                _matches_included(ref.object_name, pattern) for pattern in request.include_patterns
            )
        ]
        objects = [DatabaseObject(ref=ref) for ref in refs]
        now = _now()
        record = CatalogRecord(
            catalog_id=catalog_id,
            request=request,
            request_fingerprint=request_fingerprint,
            session_cache_key=session_cache_key,
            source_schema_fingerprint=source_schema_fingerprint,
            source_object_fingerprints=source_object_fingerprints or {},
            status=JobStatus.AWAITING_SELECTION,
            phase="awaiting_selection",
            total_object_count=len(objects),
            created_at=now,
            expires_at=now + self.completed_ttl,
        )
        snapshot = CatalogSnapshot(
            catalog_id=catalog_id,
            profile_name=profile.name,
            request_fingerprint=record.request_fingerprint,
            status=JobStatus.AWAITING_SELECTION,
            objects=objects,
            capabilities=adapter.capabilities(),
            created_at=now,
            expires_at=record.expires_at,
        )
        with self._lock:
            self._contexts[catalog_id] = (profile, adapter)
            self._cancel[catalog_id] = Event()
            self._write_record(record)
            self._write_snapshot(snapshot)
            previous = self._incremental_record(
                session_cache_key=session_cache_key,
                request_fingerprint=request_fingerprint,
                source_object_fingerprints=source_object_fingerprints or {},
            )
            if previous is not None:
                for object_id, validator in (source_object_fingerprints or {}).items():
                    if previous.source_object_fingerprints.get(object_id) != validator:
                        continue
                    key = hashlib.sha256(object_id.encode()).hexdigest()
                    prior = self.state.read_json(
                        f"catalogs/{previous.catalog_id}/checkpoints/{key}.json"
                    )
                    if not isinstance(prior, dict) or prior.get("object") is None:
                        continue
                    copied = dict(prior)
                    copied_object = DatabaseObject.model_validate(copied["object"])
                    if copied_object.ref.object_type == ObjectType.TABLE:
                        copied["status"] = "definition_reused"
                        copied["sample"] = None
                        copied["dependencies"] = []
                    self.state.write_json(f"catalogs/{catalog_id}/checkpoints/{key}.json", copied)
        return self.status(catalog_id)

    def resume_context(
        self,
        catalog_id: str,
        profile: ResolvedConnectionProfile,
        adapter: BaseDatabaseAdapter,
    ) -> None:
        record = self._record(catalog_id)
        if record.request.profile != profile.name:
            raise SqlCtxError(
                "CATALOG_RESUME_MISMATCH",
                "Resolved profile does not match the retained catalog.",
                status_code=409,
            )
        with self._lock:
            self._contexts[catalog_id] = (profile, adapter)
            self._cancel.setdefault(catalog_id, Event())

    def category_preview(
        self, catalog_id: str, *, cursor: str | None = None, limit: int = 100
    ) -> CategoryPreview:
        snapshot = self._snapshot(catalog_id)
        groups: dict[str, list[str]] = {}
        unresolved = 0
        for item in snapshot.objects:
            category = self._preliminary_category(item.ref.object_name)
            if category is None:
                unresolved += 1
                continue
            groups.setdefault(category, []).append(item.ref.object_name)
        all_items = [
            CategoryPreviewGroup(
                category=category,
                object_count=len(names),
                representative_names=sorted(names)[:10],
            )
            for category, names in sorted(groups.items())
        ]
        items, page = page_slice(all_items, cursor=cursor, limit=limit)
        return CategoryPreview(items=items, unresolved_count=unresolved, page=page)

    def select(self, catalog_id: str, selection: MaterializationSelection) -> CatalogStatus:
        record = self._record(catalog_id)
        if record.status not in {JobStatus.AWAITING_SELECTION, JobStatus.READY}:
            raise SqlCtxError(
                "INVALID_JOB_STATE",
                "Catalog cannot accept selection in its current state.",
                status_code=409,
            )
        record.selection = selection
        record.status = JobStatus.RUNNING
        record.phase = "extracting"
        record.started_at = record.started_at or _now()
        record.last_progress_at = _now()
        self._write_record(record)
        self._run_phase_two(catalog_id)
        return self.status(catalog_id)

    def _run_phase_two(self, catalog_id: str) -> None:
        record = self._record(catalog_id)
        snapshot = self._snapshot(catalog_id)
        try:
            profile, adapter = self._contexts[catalog_id]
        except KeyError as exc:
            raise SqlCtxError(
                "CATALOG_RESUME_CONTEXT_REQUIRED",
                "Retained catalog requires owner-side profile resolution before resume.",
                status_code=409,
            ) from exc
        cancel = self._cancel[catalog_id]
        analyzed: list[DatabaseObject] = []
        samples: dict[str, SamplePage] = {}
        dependencies: list[DependencyEdge] = []
        failures = 0
        sample_failures = 0
        dependency_failures = 0
        failure_records: list[CatalogObjectFailure] = []

        def record_failure(
            ref: ObjectRef, stage: CatalogFailureStage, exc: SqlCtxError
        ) -> CatalogObjectFailure:
            failure = CatalogObjectFailure(
                object_id=ref.object_id,
                object_type=ref.object_type,
                stage=stage,
                error_code=exc.code,
                message=_safe_failure_message(exc.message),
            )
            failure_records.append(failure)
            return failure

        for position, placeholder in enumerate(snapshot.objects, start=1):
            if cancel.is_set():
                adapter.cancel()
                record.status = JobStatus.CANCELLED
                record.cancelled = True
                break
            ref = placeholder.ref
            checkpoint_key = hashlib.sha256(ref.object_id.encode()).hexdigest()
            raw_checkpoint = self.state.read_json(
                f"catalogs/{catalog_id}/checkpoints/{checkpoint_key}.json"
            )
            if isinstance(raw_checkpoint, dict) and raw_checkpoint.get("status") == "completed":
                try:
                    extracted = DatabaseObject.model_validate(raw_checkpoint["object"])
                    analyzed.append(extracted)
                    if raw_checkpoint.get("sample") is not None:
                        samples[ref.object_id] = SamplePage.model_validate(raw_checkpoint["sample"])
                    dependencies.extend(
                        DependencyEdge.model_validate(item)
                        for item in raw_checkpoint.get("dependencies", [])
                    )
                    record.reused_object_count += 1
                    record.processed_object_count = position
                    record.current_object_id = ref.object_id
                    record.last_progress_at = _now()
                    self._write_record(record)
                    continue
                except (KeyError, ValueError):
                    pass
            definition_reused = (
                isinstance(raw_checkpoint, dict)
                and raw_checkpoint.get("status") == "definition_reused"
            )
            try:
                extracted = (
                    DatabaseObject.model_validate(raw_checkpoint["object"])
                    if definition_reused
                    else adapter.extract_object(profile, ref)
                )
                if extracted.sanitized_definition:
                    cleaned, _ = scan_and_redact_sql_literals(extracted.sanitized_definition)
                    extracted.sanitized_definition = cleaned
                    extracted.source_fingerprint = (
                        "sha256:" + hashlib.sha256(cleaned.encode()).hexdigest()
                    )
            except SqlCtxError as exc:
                failures += 1
                failure = record_failure(ref, "analysis", exc)
                record.processed_object_count = position
                record.current_object_id = ref.object_id
                record.last_progress_at = _now()
                self._write_record(record)
                self.state.write_json(
                    f"catalogs/{catalog_id}/checkpoints/{checkpoint_key}.json",
                    {
                        "object_id": ref.object_id,
                        "status": "failed",
                        "error_code": "OBJECT_EXTRACTION_FAILED",
                        "failure": failure.model_dump(mode="json"),
                        "updated_at": record.last_progress_at.isoformat(),
                    },
                )
                continue
            analyzed.append(extracted)
            if definition_reused:
                record.reused_object_count += 1
            object_dependencies: list[DependencyEdge] = []
            object_sample: SamplePage | None = None
            if ref.object_type == ObjectType.TABLE:
                object_dependencies.extend(self._foreign_key_edges(extracted))
                dependencies.extend(object_dependencies)
                try:
                    if self._preliminary_category(ref.object_name) == "lut":
                        raw_page = adapter.get_all_rows(
                            profile,
                            ref,
                            columns=extracted.columns,
                            constraints=extracted.constraints,
                        )
                    else:
                        raw_page = adapter.get_sample_rows(
                            profile,
                            ref,
                            record.request.sample_rows_per_table,
                            columns=extracted.columns,
                            constraints=extracted.constraints,
                        )
                    sanitized_rows = []
                    for row in raw_page.rows:
                        sanitized_rows.append(
                            [
                                self.masker.mask(
                                    column_name=column,
                                    value=value,
                                    snapshot_id=catalog_id,
                                ).masked_value
                                for column, value in zip(raw_page.columns, row, strict=True)
                            ]
                        )
                    object_sample = raw_page.model_copy(update={"rows": sanitized_rows})
                    samples[ref.object_id] = object_sample
                except SqlCtxError as exc:
                    sample_failures += 1
                    record_failure(ref, "sample", exc)
            else:
                try:
                    object_dependencies.extend(adapter.get_routine_dependencies(profile, ref))
                    dependencies.extend(object_dependencies)
                except SqlCtxError as exc:
                    dependency_failures += 1
                    record_failure(ref, "dependencies", exc)
            self.state.write_json(
                f"catalogs/{catalog_id}/checkpoints/{checkpoint_key}.json",
                {
                    "object_id": ref.object_id,
                    "status": "completed",
                    "object": extracted.model_dump(mode="json"),
                    "sample": object_sample.model_dump(mode="json") if object_sample else None,
                    "dependencies": [item.model_dump(mode="json") for item in object_dependencies],
                    "updated_at": _now().isoformat(),
                },
            )
            record.processed_object_count = position
            record.current_object_id = ref.object_id
            record.last_progress_at = _now()
            self._write_record(record)
        if record.status != JobStatus.CANCELLED:
            record.status = (
                JobStatus.COMPLETED_WITH_WARNINGS
                if failures or sample_failures or dependency_failures
                else JobStatus.READY
            )
        record.expires_at = _now() + self.completed_ttl
        if record.status != JobStatus.CANCELLED:
            record.processed_object_count = record.total_object_count
        record.phase = "completed" if record.status != JobStatus.CANCELLED else "cancelled"
        record.current_object_id = None
        snapshot = snapshot.model_copy(
            update={
                "status": record.status,
                "objects": analyzed,
                "samples": samples,
                "dependencies": dependencies,
                "expires_at": record.expires_at,
            }
        )
        self._write_record(record)
        self._write_snapshot(snapshot)
        self.state.write_json(
            f"catalogs/{catalog_id}/analysis.json",
            {
                "failed_analysis": failures,
                "failed_samples": sample_failures,
                "failed_dependencies": dependency_failures,
                "failures": [item.model_dump(mode="json") for item in failure_records],
                "completed_object_ids": [item.ref.object_id for item in analyzed],
            },
        )

    def refresh_lut_samples(self, catalog_id: str, object_ids: list[str]) -> list[str]:
        """Fetch every row for final LUT tables that were not recognized during phase one."""
        if not object_ids:
            return []
        try:
            profile, adapter = self._contexts[catalog_id]
        except KeyError:
            return list(object_ids)
        snapshot = self._snapshot(catalog_id)
        object_map = {item.ref.object_id: item for item in snapshot.objects}
        samples = dict(snapshot.samples)
        failed: list[str] = []
        for object_id in object_ids:
            obj = object_map.get(object_id)
            if obj is None or obj.ref.object_type != ObjectType.TABLE:
                continue
            existing = samples.get(object_id)
            if existing is not None and existing.all_rows and existing.complete:
                continue
            try:
                raw_page = adapter.get_all_rows(
                    profile,
                    obj.ref,
                    columns=obj.columns,
                    constraints=obj.constraints,
                )
                sanitized_rows = [
                    [
                        self.masker.mask(
                            column_name=column,
                            value=value,
                            snapshot_id=catalog_id,
                        ).masked_value
                        for column, value in zip(raw_page.columns, row, strict=True)
                    ]
                    for row in raw_page.rows
                ]
                samples[object_id] = raw_page.model_copy(update={"rows": sanitized_rows})
            except SqlCtxError:
                failed.append(object_id)
        self._write_snapshot(snapshot.model_copy(update={"samples": samples}))
        return failed

    @staticmethod
    def _foreign_key_edges(extracted: DatabaseObject) -> list[Any]:
        from sqlctx.core.enums import EdgeType
        from sqlctx.core.models import DependencyEdge

        return [
            DependencyEdge(
                source_object_id=fk.source_object_id,
                target_object_id=fk.target_object_id,
                edge_type=EdgeType.FOREIGN_KEY,
                evidence=[fk.name],
            )
            for fk in extracted.foreign_keys
        ]

    def materialization_plan(self, catalog_id: str) -> MaterializationPlan:
        record = self._record(catalog_id)
        snapshot = self._snapshot(catalog_id)
        selection = record.selection or MaterializationSelection(mode=MaterializationMode.ASK)
        final_categories = {
            result.object_id: result.category
            for result in snapshot.classifications
            if result.pass_name == ClassificationPass.PASS_2
        }
        items: list[MaterializationPlanItem] = []
        for item in snapshot.objects:
            category = final_categories.get(
                item.ref.object_id, self._preliminary_category(item.ref.object_name)
            )
            included = selection.mode == MaterializationMode.ALL or (
                category is not None
                and (
                    (selection.mode != MaterializationMode.ASK and category == "lut")
                    or (
                        selection.mode == MaterializationMode.SELECTED
                        and category in selection.selected_categories
                    )
                )
            )
            items.append(
                MaterializationPlanItem(
                    object_id=item.ref.object_id,
                    final_category=category,
                    included=included,
                    reason=(
                        InclusionReason.ALL_MODE
                        if selection.mode == MaterializationMode.ALL
                        else InclusionReason.POLICY_ALWAYS_INCLUDE
                        if included and category == "lut"
                        else InclusionReason.SELECTED_CATEGORY
                        if included
                        else InclusionReason.INTENTIONALLY_EXCLUDED
                    ),
                )
            )
        return MaterializationPlan(catalog_id=catalog_id, selection=selection, items=items)

    def sitemap(
        self,
        catalog_id: str,
        *,
        view: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> SitemapPage:
        snapshot = self._snapshot(catalog_id)
        if view not in {"analysis", "materialization"}:
            raise SqlCtxError(
                "INVALID_SITEMAP_VIEW", "Sitemap view must be analysis or materialization."
            )
        if view == "materialization":
            plan = {item.object_id: item for item in self.materialization_plan(catalog_id).items}
        else:
            plan = {}
        final_categories = {
            result.object_id: result.category
            for result in snapshot.classifications
            if result.pass_name == ClassificationPass.PASS_2
        }
        all_items = [
            SitemapItem(
                object_id=item.ref.object_id,
                category=final_categories.get(
                    item.ref.object_id, self._preliminary_category(item.ref.object_name)
                ),
                object_type=item.ref.object_type,
                inclusion_reason=(
                    plan[item.ref.object_id].reason
                    if item.ref.object_id in plan
                    else InclusionReason.ALL_MODE
                ),
                content_hash=item.source_fingerprint,
            )
            for item in snapshot.objects
        ]
        selected, page = page_slice(all_items, cursor=cursor, limit=limit)
        return SitemapPage(items=selected, page=page)

    def status(self, catalog_id: str) -> CatalogStatus:
        record = self._record(catalog_id)
        snapshot = self._snapshot(catalog_id)
        analysis = self.state.read_json(
            f"catalogs/{catalog_id}/analysis.json", {"failed_analysis": 0}
        )
        plan = self.materialization_plan(catalog_id) if record.selection else None
        materialized = sum(item.included for item in plan.items) if plan else 0
        excluded = sum(not item.included for item in plan.items) if plan else 0
        finished = record.status not in {JobStatus.RUNNING, JobStatus.QUEUED}
        elapsed_end = record.last_progress_at if finished and record.last_progress_at else _now()
        elapsed = (
            max(0.0, (elapsed_end - record.started_at).total_seconds())
            if record.started_at
            else 0.0
        )
        remaining = max(0, record.total_object_count - record.processed_object_count)
        eta = (
            elapsed / record.processed_object_count * remaining
            if not finished and record.processed_object_count
            else None
        )
        return CatalogStatus(
            catalog_id=catalog_id,
            status=record.status,
            request_fingerprint=record.request_fingerprint,
            discovered_object_count=len(snapshot.objects) + int(analysis["failed_analysis"]),
            fully_analyzed_object_count=len(snapshot.objects)
            if record.status not in {JobStatus.AWAITING_SELECTION, JobStatus.RUNNING}
            else 0,
            analysis_failed_object_count=int(analysis["failed_analysis"]),
            materialized_object_count=materialized,
            intentionally_excluded_object_count=excluded,
            cache_expires_at=record.expires_at,
            phase=record.phase,
            total_object_count=record.total_object_count,
            processed_object_count=record.processed_object_count,
            reused_object_count=record.reused_object_count,
            current_object_id=record.current_object_id,
            started_at=record.started_at,
            last_progress_at=record.last_progress_at,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            warnings=[
                warning
                for count, warning in (
                    (int(analysis.get("failed_samples", 0)), "sample_extraction_incomplete"),
                    (
                        int(analysis.get("failed_dependencies", 0)),
                        "dependency_extraction_incomplete",
                    ),
                )
                if count
            ],
            failures=[
                CatalogObjectFailure.model_validate(item)
                for item in analysis.get("failures", [])
                if isinstance(item, dict)
            ],
        )

    def complete_context_index_scope(
        self, catalog_id: str, profile: ResolvedConnectionProfile
    ) -> CompleteCatalogContextScope:
        """Prove that a retained all-mode catalog exactly covered its profile boundary."""
        record = self._record(catalog_id)
        snapshot = self._snapshot(catalog_id)
        status = self.status(catalog_id)
        reusable = {
            JobStatus.READY,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        }
        complete = (
            record.request.profile == profile.name
            and record.selection is not None
            and record.selection.mode == MaterializationMode.ALL
            and not record.request.include_patterns
            and not record.request.exclude_patterns
            and not profile.excluded_object_patterns
            and set(record.request.schemas) == set(profile.allowed_schemas)
            and set(record.request.object_types) == set(profile.allowed_object_types)
            and status.status in reusable
            and status.analysis_failed_object_count == 0
            and status.discovered_object_count == len(snapshot.objects)
            and status.fully_analyzed_object_count == len(snapshot.objects)
        )
        if not complete:
            raise SqlCtxError(
                "METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED",
                "Catalog does not prove one complete matching profile scope.",
                status_code=409,
                details={"catalog_id": catalog_id},
            )
        return CompleteCatalogContextScope(
            schemas=tuple(profile.allowed_schemas),
            object_types=tuple(profile.allowed_object_types),
            object_ids=frozenset(item.ref.object_id for item in snapshot.objects),
        )

    def _cached_record(
        self,
        *,
        session_cache_key: str,
        request_fingerprint: str,
        source_schema_fingerprint: str,
    ) -> CatalogRecord | None:
        reusable = {
            JobStatus.AWAITING_SELECTION,
            JobStatus.READY,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        }
        now = _now()
        return next(
            (
                record
                for record in sorted(
                    self._all_records(), key=lambda item: item.created_at, reverse=True
                )
                if record.session_cache_key == session_cache_key
                and record.request_fingerprint == request_fingerprint
                and record.source_schema_fingerprint == source_schema_fingerprint
                and record.expires_at > now
                and record.status in reusable
            ),
            None,
        )

    def _incremental_record(
        self,
        *,
        session_cache_key: str | None,
        request_fingerprint: str,
        source_object_fingerprints: dict[str, str],
    ) -> CatalogRecord | None:
        if not session_cache_key or not source_object_fingerprints:
            return None
        reusable = {
            JobStatus.READY,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        }
        now = _now()
        return next(
            (
                record
                for record in sorted(
                    self._all_records(), key=lambda item: item.created_at, reverse=True
                )
                if record.session_cache_key == session_cache_key
                and record.request_fingerprint == request_fingerprint
                and record.source_object_fingerprints
                and record.expires_at > now
                and record.status in reusable
            ),
            None,
        )

    def list_jobs(
        self, *, status: JobStatus | None = None, cursor: str | None = None, limit: int = 100
    ) -> CatalogJobPage:
        records = sorted(self._all_records(), key=lambda item: item.created_at, reverse=True)
        if status is not None:
            records = [record for record in records if record.status == status]
        descriptors = [
            CatalogJobDescriptor(
                catalog_id=record.catalog_id,
                profile_name=record.request.profile,
                status=record.status,
                request_fingerprint=record.request_fingerprint,
                safe_request_summary={
                    "schemas": record.request.schemas,
                    "object_types": record.request.object_types,
                    "sample_rows_per_table": record.request.sample_rows_per_table,
                    "masking_policy": record.request.masking_policy,
                    "selection": record.selection.model_dump(mode="json")
                    if record.selection
                    else None,
                },
                expires_at=record.expires_at,
            )
            for record in records
        ]
        items, page = page_slice(descriptors, cursor=cursor, limit=limit)
        return CatalogJobPage(items=items, page=page)

    def sync_candidates(
        self, *, profile_names: set[str] | None = None
    ) -> tuple[list[CatalogRecord], dict[str, int]]:
        """Return newest eligible retained contexts without failing on unrelated corrupt records."""
        root = self.state._safe("catalogs")
        skipped: dict[str, int] = {}

        def skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        records: list[CatalogRecord] = []
        for path in root.glob("*/record.json") if root.exists() else []:
            try:
                record = CatalogRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                skip("runtime_state_corrupt")
                continue
            if profile_names and record.request.profile not in profile_names:
                continue
            records.append(record)

        now = _now()
        reusable = {
            JobStatus.READY,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        }
        selected: dict[tuple[str, str], CatalogRecord] = {}
        for record in sorted(records, key=lambda item: item.created_at, reverse=True):
            if not record.session_cache_key:
                skip("session_cache_key_missing")
                continue
            if record.expires_at <= now:
                skip("expired")
                continue
            if record.status not in reusable:
                skip(f"status_{record.status.value}")
                continue
            if record.selection is None or record.selection.mode == MaterializationMode.ASK:
                skip("selection_incomplete")
                continue
            key = (record.session_cache_key, record.request_fingerprint)
            if key in selected:
                skip("superseded")
                continue
            selected[key] = record
        candidates = sorted(
            selected.values(),
            key=lambda item: (item.request.profile, item.request_fingerprint, item.catalog_id),
        )
        return candidates, dict(sorted(skipped.items()))

    def cancel(self, catalog_id: str) -> CatalogStatus:
        record = self._record(catalog_id)
        if record.status in {
            JobStatus.CANCELLED,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
            JobStatus.READY,
            JobStatus.FAILED,
        }:
            return self.status(catalog_id)
        self._cancel.setdefault(catalog_id, Event()).set()
        context = self._contexts.get(catalog_id)
        if context:
            context[1].cancel()
        record.status = JobStatus.CANCELLED
        record.cancelled = True
        self._write_record(record)
        return self.status(catalog_id)

    def delete(self, catalog_id: str) -> DeleteResult:
        record = self._record(catalog_id)
        if record.status in {JobStatus.RUNNING, JobStatus.QUEUED} or any(
            item.get("active") for item in record.dependent_exports.values()
        ):
            raise SqlCtxError(
                "JOB_ACTIVE", "Active catalog work cannot be deleted.", status_code=409
            )
        self._remove_tree(f"catalogs/{catalog_id}")
        self._remove_tree(f"snapshots/{catalog_id}")
        return DeleteResult(deleted=True, target_id=catalog_id)

    def pin_export(
        self, catalog_id: str, export_id: str, expires_at: datetime, *, active: bool
    ) -> None:
        record = self._record(catalog_id)
        record.dependent_exports[export_id] = {
            "expires_at": expires_at.isoformat(),
            "active": active,
        }
        self._write_record(record)

    def release_export(self, catalog_id: str, export_id: str) -> None:
        record = self._record(catalog_id)
        record.dependent_exports.pop(export_id, None)
        self._write_record(record)

    def cleanup_expired(self) -> list[str]:
        removed: list[str] = []
        now = _now()
        for record in self._all_records():
            pinned = any(
                dependency.get("active")
                or datetime.fromisoformat(str(dependency["expires_at"])) > now
                for dependency in record.dependent_exports.values()
            )
            if (
                record.expires_at <= now
                and not pinned
                and record.status not in {JobStatus.RUNNING, JobStatus.QUEUED}
            ):
                self._remove_tree(f"catalogs/{record.catalog_id}")
                self._remove_tree(f"snapshots/{record.catalog_id}")
                removed.append(record.catalog_id)
        return removed

    def _remove_tree(self, relative: str) -> None:
        directory = self.state._safe(relative)
        for path in sorted(directory.rglob("*"), reverse=True) if directory.exists() else []:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if directory.exists():
            directory.rmdir()

    def _ensure_quota(self) -> None:
        used = (
            sum(path.stat().st_size for path in self.state.root.rglob("*") if path.is_file())
            if self.state.root.exists()
            else 0
        )
        if used >= self.max_total_bytes:
            raise SqlCtxError(
                "RUNTIME_STORAGE_FULL",
                "Runtime storage quota is exhausted after expired-artifact cleanup.",
                status_code=507,
            )

    def _record(self, catalog_id: str) -> CatalogRecord:
        value = self.state.read_json(f"catalogs/{catalog_id}/record.json")
        if value is None:
            raise SqlCtxError("CATALOG_NOT_FOUND", "Catalog job was not found.", status_code=404)
        return CatalogRecord.model_validate(value)

    def _snapshot(self, catalog_id: str) -> CatalogSnapshot:
        value = self.state.read_json(f"catalogs/{catalog_id}/snapshot.json")
        if value is None:
            raise SqlCtxError(
                "CATALOG_NOT_FOUND", "Catalog snapshot was not found.", status_code=404
            )
        return CatalogSnapshot.model_validate(value)

    def get_snapshot(self, catalog_id: str) -> CatalogSnapshot:
        """Return the retained sanitized snapshot to trusted application services."""
        return self._snapshot(catalog_id)

    def save_classifications(self, catalog_id: str, classifications: list[Any]) -> None:
        snapshot = self._snapshot(catalog_id)
        self._write_snapshot(snapshot.model_copy(update={"classifications": classifications}))

    def _write_record(self, record: CatalogRecord) -> None:
        self.state.write_json(
            f"catalogs/{record.catalog_id}/record.json", record.model_dump(mode="json")
        )

    def _write_snapshot(self, snapshot: CatalogSnapshot) -> None:
        self.state.write_json(
            f"catalogs/{snapshot.catalog_id}/snapshot.json", snapshot.model_dump(mode="json")
        )

    def _all_records(self) -> list[CatalogRecord]:
        catalog_root = self.state._safe("catalogs")
        if not catalog_root.exists():
            return []
        result = []
        for path in catalog_root.glob("*/record.json"):
            try:
                result.append(CatalogRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise SqlCtxError(
                    "RUNTIME_STATE_CORRUPT", "A retained catalog record is unreadable."
                ) from exc
        return result

    @staticmethod
    def _preliminary_category(name: str) -> str | None:
        parts = [part.lower() for part in name.split("_") if part]
        if len(parts) < 2 or not parts[0].isidentifier():
            return None
        return parts[0]
