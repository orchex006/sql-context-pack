"""One shared typed application facade for HTTP, MCP, and CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel

from sqlctx.adapters.base import BaseDatabaseAdapter
from sqlctx.adapters.registry import create_adapter, dialect_map
from sqlctx.application.catalog import CatalogRequest, CatalogService
from sqlctx.application.idempotency import IdempotencyService
from sqlctx.classification.classifier import ClassificationService
from sqlctx.classification.rules import CategoryRuleRepository
from sqlctx.context_index.contracts import (
    ContextGenerationPlan,
    ContextGenerationRequest,
    ContextIndexListRequest,
    ContextIndexPage,
    ContextIndexSyncRequest,
    ContextIndexSyncResult,
)
from sqlctx.context_index.generation import build_generation_plan
from sqlctx.context_index.service import (
    CompleteContextIndexScope,
    ContextIndexService,
    MetadataContextAdapter,
)
from sqlctx.core.enums import (
    ClassificationPass,
    ClassificationStatus,
    DatabaseEngine,
    MaterializationMode,
    ObjectType,
)
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogStatus,
    ClassificationProposal,
    ClassificationProposalBatch,
    DeleteResult,
    ExportBatchRequest,
    ExportStatus,
    HostPythonToolingDescriptor,
    ProposalBatchResult,
    ResolvedConnectionProfile,
    SyncDataContextResult,
    SyncDataResult,
    ValidationRequest,
    ValidationResult,
)
from sqlctx.exporting.service import ExportService
from sqlctx.exporting.writer import OutputPackageWriter
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.managed_files.contracts import (
    FolderApplyResult,
    FolderClassificationPlan,
    FolderClassificationPlanRequest,
    ManagedFileResolutionPlanRequest,
    RegisteredFolderDescriptor,
    RegisteredFolderList,
)
from sqlctx.managed_files.service import ManagedFolderService
from sqlctx.routine_deploy.contracts import (
    RoutineApplyResult,
    RoutineDeploymentPlan,
    RoutinePlanRequest,
)
from sqlctx.routine_deploy.service import RoutineApplyAdapter, RoutineDeploymentService
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.masking import DeterministicMaskingEngine
from sqlctx.security.profiles import YamlConnectionProfileRepository, default_config_dir
from sqlctx.security.runtime import (
    EncryptedSnapshotSecretStore,
    JsonRuntimeStateStore,
    exclusive_runtime_lock,
)
from sqlctx.server.contracts import (
    CapabilitiesResponse,
    ClassificationResolutionBatch,
    CreateCatalogRequest,
    EngineCapability,
    ExportCreateRequest,
    ProposalRequest,
    QueryDataRequest,
    QueryDataResult,
    ResolutionBatchResult,
)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


class QueryService(Protocol):
    def execute(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: Any,
    ) -> QueryDataResult: ...

    def stream_markdown(
        self,
        request: QueryDataRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: Any,
    ) -> Any: ...


class ServiceFacade:
    def __init__(
        self,
        *,
        state: JsonRuntimeStateStore | None = None,
        profiles: YamlConnectionProfileRepository | None = None,
        categories_path: Path | None = None,
        overrides_path: Path | None = None,
        manager: SqlFluffManager | None = None,
    ) -> None:
        self.state = state or JsonRuntimeStateStore()
        self.profiles = profiles or YamlConnectionProfileRepository()
        self.approvals = ApprovalService(state=self.state)
        self.manager = manager or SqlFluffManager(self.state)
        masker = DeterministicMaskingEngine(EncryptedSnapshotSecretStore(self.state))
        self.catalogs = CatalogService(self.state, masker)
        self._query_masker = masker
        self._queries: QueryService | None = None
        configured_categories = default_config_dir() / "categories.yaml"
        configured_overrides = default_config_dir() / "category-overrides.yaml"
        packaged_categories = Path(__file__).resolve().parents[1] / "data/categories.yaml"
        rules_path = categories_path or (
            configured_categories if configured_categories.exists() else packaged_categories
        )
        owner_overrides = overrides_path or configured_overrides
        if not owner_overrides.exists():
            owner_overrides.parent.mkdir(parents=True, exist_ok=True)
            owner_overrides.write_text("version: 1\noverrides: {}\n", encoding="utf-8")
        category_repository = CategoryRuleRepository(rules_path, owner_overrides)
        category_config, _ = category_repository.load()
        self.classifications = ClassificationService(
            self.state,
            self.catalogs,
            category_repository,
            self.approvals,
        )
        self.exports = ExportService(
            self.state,
            self.catalogs,
            self.classifications,
            self.manager,
            OutputPackageWriter(SqlFluffFormatter(self.manager)),
            self.approvals,
        )
        self.idempotency = IdempotencyService(self.state)
        self.managed_folders = ManagedFolderService(
            self.state, self.approvals, category_rules=category_config
        )
        self.context_index = ContextIndexService(self.approvals)
        self.routines = RoutineDeploymentService(self.state, self.managed_folders, self.approvals)

    def capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            engines=[
                EngineCapability(engine=DatabaseEngine(engine), sqlfluff_dialect=dialect)
                for engine, dialect in sorted(dialect_map().items())
            ]
        )

    def list_profiles(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "profile": item.name,
                    "engine": item.engine,
                    "allowed_schemas": item.allowed_schemas,
                    "allowed_object_types": item.allowed_object_types,
                    "excluded_object_patterns": item.excluded_object_patterns,
                    "sample_rows_per_table": item.sample_rows_per_table,
                    "trust_server_certificate": item.trust_server_certificate,
                    "ready": item.ready,
                    "readiness_reason": item.readiness_reason,
                }
                for item in self.profiles.list_descriptors()
            ]
        }

    def list_managed_folders(self) -> RegisteredFolderList:
        return RegisteredFolderList(
            items=[
                RegisteredFolderDescriptor(
                    folder_id=item.folder_id,
                    engine=item.engine,
                    separate_output=item.input_root != item.output_root,
                )
                for item in self.managed_folders.list_registered()
            ]
        )

    def plan_folder_classification(
        self, request: FolderClassificationPlanRequest
    ) -> FolderClassificationPlan:
        return self.managed_folders.plan(request)

    def apply_folder_classification(self, plan_id: str, *, caller: str) -> FolderApplyResult:
        return self.managed_folders.apply(plan_id, caller=caller)

    def sync_context_index(
        self, request: ContextIndexSyncRequest, *, caller: str
    ) -> ContextIndexSyncResult:
        profile = self.profiles.resolve(request.profile)
        adapter = create_adapter(profile.engine)
        complete_scope: CompleteContextIndexScope | None = None
        if request.complete_catalog_id is not None:
            proof = self.catalogs.complete_context_index_scope(request.complete_catalog_id, profile)
            submitted = {
                f"{entry.object_type}:{entry.schema_name}.{entry.object_name}".casefold()
                for entry in request.entries
            }
            expected = {object_id.casefold() for object_id in proof.object_ids}
            if submitted != expected:
                raise SqlCtxError(
                    "METADATA_CONTEXT_COMPLETE_SCOPE_INVENTORY_MISMATCH",
                    "Submitted metadata identities do not match the complete catalog inventory.",
                    status_code=409,
                    details={
                        "catalog_object_count": len(expected),
                        "submitted_object_count": len(submitted),
                    },
                )
            complete_scope = CompleteContextIndexScope(
                schemas=proof.schemas,
                object_types=proof.object_types,
            )
        return self.idempotency.execute(
            caller=caller,
            operation="metadata_context.sync",
            key=request.idempotency_key,
            normalized_request=request.model_dump(mode="json"),
            create=lambda: self.context_index.sync(
                request,
                entries=request.entries,
                complete_scope=complete_scope,
                profile=profile,
                adapter=cast(MetadataContextAdapter, adapter),
                caller=caller,
            ),
            serialize=lambda result: result.model_dump(mode="json"),
            validate=ContextIndexSyncResult.model_validate,
        )[0]

    def resolve_context_index(
        self, request: ManagedFileResolutionPlanRequest
    ) -> FolderClassificationPlan:
        return self.managed_folders.plan_resolution(request)

    def list_context_index(self, request: ContextIndexListRequest) -> ContextIndexPage:
        profile = self.profiles.resolve(request.profile)
        adapter = create_adapter(profile.engine)
        return self.context_index.list(
            request, profile=profile, adapter=cast(MetadataContextAdapter, adapter)
        )

    def plan_context_generation(self, request: ContextGenerationRequest) -> ContextGenerationPlan:
        page = self.list_context_index(
            ContextIndexListRequest(
                profile=request.profile,
                context=request.context,
                object_type=request.object_type,
                tag=request.tag,
                status=None if request.include_unresolved else "confirmed",
                limit=request.limit,
            )
        )
        return build_generation_plan(
            request,
            page,
            managed_root=self.managed_folders.managed_output_root(request.folder_id),
        )

    def plan_routine_deployment(
        self, request: RoutinePlanRequest, *, caller: str
    ) -> RoutineDeploymentPlan:
        profile = self.profiles.resolve(request.profile)
        adapter = create_adapter(profile.engine)
        return self.idempotency.execute(
            caller=caller,
            operation="routine.plan",
            key=request.idempotency_key,
            normalized_request=request.model_dump(mode="json"),
            create=lambda: self.routines.plan(
                request,
                profile=profile,
                caller=caller,
                adapter=cast(RoutineApplyAdapter, adapter),
            ),
            serialize=lambda result: result.model_dump(mode="json"),
            validate=RoutineDeploymentPlan.model_validate,
        )[0]

    def apply_routine_deployment(self, plan_id: str, *, caller: str) -> RoutineApplyResult:
        value = self.state.read_json(f"routine-plans/{plan_id}.json")
        if not isinstance(value, dict) or not isinstance(value.get("plan"), dict):
            raise SqlCtxError("ROUTINE_PLAN_NOT_FOUND", "Routine deployment plan was not found.")
        profile_name = str(value["plan"].get("profile", ""))
        profile = self.profiles.resolve(profile_name)
        adapter = create_adapter(profile.engine)
        return self.routines.apply(
            plan_id,
            profile=profile,
            adapter=cast(RoutineApplyAdapter, adapter),
            caller=caller,
        )

    def resolve_query_profile(self, requested: str | None) -> str:
        descriptors = self.profiles.list_descriptors()
        ready = sorted(item.name for item in descriptors if item.ready)
        if requested is not None:
            if requested not in ready:
                raise SqlCtxError(
                    "QUERY_PROFILE_NOT_READY",
                    "The selected query profile is unknown or not ready.",
                    status_code=503,
                    details={"ready_profiles": ready},
                )
            return requested
        if len(ready) != 1:
            raise SqlCtxError(
                "QUERY_PROFILE_REQUIRED",
                "Select an exact ready profile for Query Data.",
                details={"ready_profiles": ready},
            )
        return ready[0]

    def query(self, command: QueryDataRequest) -> QueryDataResult:
        profile = self.profiles.resolve(command.profile)
        adapter = create_adapter(profile.engine)
        return self.queries.execute(command, profile=profile, adapter=adapter)

    def stream_query(self, command: QueryDataRequest) -> Any:
        profile = self.profiles.resolve(command.profile)
        adapter = create_adapter(profile.engine)
        return self.queries.stream_markdown(command, profile=profile, adapter=adapter)

    @property
    def queries(self) -> QueryService:
        """Construct the additive query subsystem only when the new feature is invoked."""
        if self._queries is None:
            from sqlctx.query_data.service import QueryDataService

            self._queries = QueryDataService(self._query_masker)
        return self._queries

    @queries.setter
    def queries(self, value: QueryService) -> None:
        self._queries = value

    def test_profile(self, profile_name: str) -> dict[str, Any]:
        profile = self.profiles.resolve(profile_name)
        adapter = create_adapter(profile.engine)
        adapter.test_connection(profile)
        capabilities = adapter.capabilities()
        discovered_allowed_schemas = adapter.list_schemas(profile)
        return {
            "profile": profile_name,
            "reachable": True,
            "engine": profile.engine,
            "capabilities": {
                "tables": capabilities.supports_tables,
                "procedures": capabilities.supports_procedures,
            },
            "allowed_schemas": list(profile.allowed_schemas),
            "discovered_allowed_schemas": discovered_allowed_schemas,
            "excluded_object_patterns": list(profile.excluded_object_patterns),
            "session_catalog_cache_ttl_hours": 24,
        }

    @staticmethod
    def _source_fingerprints(
        adapter: BaseDatabaseAdapter,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> tuple[dict[str, str], str]:
        object_fingerprints = adapter.object_fingerprints(profile, schemas, object_types)
        schema_fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    object_fingerprints,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if object_fingerprints
            else adapter.schema_fingerprint(profile, schemas, object_types)
        )
        return object_fingerprints, schema_fingerprint

    def create_catalog(
        self, command: CreateCatalogRequest, *, caller: str, idempotency_key: str | None = None
    ) -> tuple[CatalogStatus, bool]:
        key = idempotency_key or command.idempotency_key
        if key is None:
            raise SqlCtxError(
                "IDEMPOTENCY_KEY_REQUIRED", "Catalog creation requires an idempotency key."
            )
        if command.selection.mode == MaterializationMode.ALL and command.include_patterns:
            raise SqlCtxError(
                "ALL_MODE_INCLUDE_FILTER_CONFLICT",
                "All-mode catalog requests cannot use include patterns.",
                details={"include_pattern_count": len(command.include_patterns)},
            )
        profile = self.profiles.resolve(command.profile)
        disallowed_schemas = sorted(set(command.schemas) - set(profile.allowed_schemas))
        if disallowed_schemas:
            raise SqlCtxError(
                "SCHEMA_NOT_ALLOWED",
                "One or more requested schemas are outside the profile allowlist.",
                details={"schemas": disallowed_schemas},
            )
        disallowed_types = sorted(set(command.object_types) - set(profile.allowed_object_types))
        if disallowed_types:
            raise SqlCtxError(
                "OBJECT_TYPE_NOT_ALLOWED",
                "One or more requested object types are outside the profile allowlist.",
                details={"object_types": disallowed_types},
            )
        adapter = create_adapter(profile.engine)
        sample_rows_per_table = command.sample.rows_per_table or profile.sample_rows_per_table
        source_object_fingerprints, source_schema_fingerprint = self._source_fingerprints(
            adapter, profile, command.schemas, command.object_types
        )
        normalized = command.model_dump(
            mode="json", exclude={"idempotency_key", "session_cache_key"}
        )
        normalized["source_schema_fingerprint"] = source_schema_fingerprint
        normalized["sample"]["rows_per_table"] = sample_rows_per_table

        def create() -> CatalogStatus:
            status = self.catalogs.create(
                CatalogRequest(
                    profile=command.profile,
                    schemas=command.schemas,
                    object_types=command.object_types,
                    include_patterns=command.include_patterns,
                    exclude_patterns=command.exclude_patterns,
                    sample_rows_per_table=sample_rows_per_table,
                    masking_policy=command.masking_policy,
                    selection=command.selection,
                ),
                profile,
                adapter,
                session_cache_key=command.session_cache_key,
                source_schema_fingerprint=source_schema_fingerprint,
                source_object_fingerprints=source_object_fingerprints,
            )
            if status.cache_hit:
                return status
            self.classifications.classify(status.catalog_id, command.selection)
            if command.selection.mode != "ask":
                status = self.catalogs.select(status.catalog_id, command.selection)
                self.classifications.classify(status.catalog_id, command.selection)
            return status

        return self.idempotency.execute(
            caller=caller,
            operation="catalog.create",
            key=key,
            normalized_request=normalized,
            create=create,
            serialize=_dump,
            validate=CatalogStatus.model_validate,
        )

    def sync_data(self, *, profile_names: list[str]) -> SyncDataResult:
        requested_profiles = sorted(set(profile_names))
        resolved_profiles = {name: self.profiles.resolve(name) for name in requested_profiles}
        with exclusive_runtime_lock(
            self.state,
            "sync-data",
            conflict_code="SYNC_ALREADY_RUNNING",
            conflict_message="Another data synchronization is already running.",
        ):
            candidates, skipped_reasons = self.catalogs.sync_candidates(
                profile_names=set(requested_profiles) or None
            )
            if not candidates:
                raise SqlCtxError(
                    "NO_SYNCABLE_CATALOGS",
                    "No eligible retained catalog context is available for synchronization.",
                    status_code=404,
                    details={"skipped_reasons": skipped_reasons},
                )

            contexts: list[SyncDataContextResult] = []
            for previous in candidates:
                try:
                    profile = resolved_profiles.get(previous.request.profile)
                    if profile is None:
                        profile = self.profiles.resolve(previous.request.profile)
                        resolved_profiles[previous.request.profile] = profile
                    adapter = create_adapter(profile.engine)
                    object_fingerprints, schema_fingerprint = self._source_fingerprints(
                        adapter,
                        profile,
                        previous.request.schemas,
                        previous.request.object_types,
                    )
                    previous_snapshot = self.catalogs.get_snapshot(previous.catalog_id)
                    refreshed = self.catalogs.create(
                        previous.request,
                        profile,
                        adapter,
                        session_cache_key=previous.session_cache_key,
                        source_schema_fingerprint=schema_fingerprint,
                        source_object_fingerprints=object_fingerprints,
                        force_refresh=True,
                    )
                    selection = previous.selection
                    if selection is None:
                        raise SqlCtxError(
                            "SYNC_SELECTION_MISSING",
                            "Retained catalog selection is unavailable for synchronization.",
                        )
                    self.classifications.classify(refreshed.catalog_id, selection)
                    refreshed_status = self.catalogs.select(refreshed.catalog_id, selection)
                    self.classifications.classify(refreshed.catalog_id, selection)
                    refreshed_snapshot = self.catalogs.get_snapshot(refreshed.catalog_id)

                    previous_ids = {item.ref.object_id for item in previous_snapshot.objects}
                    refreshed_ids = {item.ref.object_id for item in refreshed_snapshot.objects}
                    common_ids = previous_ids & refreshed_ids
                    change_detection_complete = bool(
                        previous.source_object_fingerprints and object_fingerprints
                    )
                    changed = (
                        sum(
                            previous.source_object_fingerprints.get(object_id)
                            != object_fingerprints.get(object_id)
                            for object_id in common_ids
                        )
                        if change_detection_complete
                        else 0
                    )
                    contexts.append(
                        SyncDataContextResult(
                            profile=previous.request.profile,
                            previous_catalog_id=previous.catalog_id,
                            catalog_id=refreshed.catalog_id,
                            status="synced",
                            added_object_count=len(refreshed_ids - previous_ids),
                            changed_object_count=changed,
                            deleted_object_count=len(previous_ids - refreshed_ids),
                            reused_object_count=refreshed_status.reused_object_count,
                            refreshed_object_count=len(refreshed_snapshot.samples),
                            definition_change_detection_complete=change_detection_complete,
                        )
                    )
                except SqlCtxError as exc:
                    contexts.append(
                        SyncDataContextResult(
                            profile=previous.request.profile,
                            previous_catalog_id=previous.catalog_id,
                            status="failed",
                            error_code=exc.code,
                        )
                    )
                except Exception:
                    contexts.append(
                        SyncDataContextResult(
                            profile=previous.request.profile,
                            previous_catalog_id=previous.catalog_id,
                            status="failed",
                            error_code="INTERNAL_ERROR",
                        )
                    )

        synced = [item for item in contexts if item.status == "synced"]
        failed = [item for item in contexts if item.status == "failed"]
        skipped_count = sum(skipped_reasons.values())
        return SyncDataResult(
            considered_context_count=len(contexts) + skipped_count,
            synced_context_count=len(synced),
            skipped_context_count=skipped_count,
            failed_context_count=len(failed),
            added_object_count=sum(item.added_object_count for item in synced),
            changed_object_count=sum(item.changed_object_count for item in synced),
            deleted_object_count=sum(item.deleted_object_count for item in synced),
            reused_object_count=sum(item.reused_object_count for item in synced),
            refreshed_object_count=sum(item.refreshed_object_count for item in synced),
            definition_change_detection_complete=bool(synced)
            and not failed
            and all(item.definition_change_detection_complete for item in synced),
            skipped_reasons=skipped_reasons,
            contexts=contexts,
        )

    def create_export(
        self, command: ExportCreateRequest, *, caller: str, idempotency_key: str | None = None
    ) -> tuple[ExportStatus, bool]:
        if command.sqlfluff is False or command.append_samples is False:
            raise SqlCtxError(
                "MANDATORY_EXPORT_STAGE_DISABLED",
                "SQLFluff and sample appending are mandatory export stages.",
            )
        key = idempotency_key or command.idempotency_key
        if key is None:
            raise SqlCtxError(
                "IDEMPOTENCY_KEY_REQUIRED", "Export creation requires an idempotency key."
            )
        plan = self.classifications.materialization_plan(command.catalog_id)
        object_ids = command.object_ids
        if object_ids is None:
            object_ids = [item.object_id for item in plan.items if item.included]
            if not object_ids:
                raise SqlCtxError(
                    "EMPTY_MATERIALIZATION_PLAN",
                    "The final materialization plan contains no exportable objects.",
                )
        normalized = command.model_dump(
            mode="json", exclude={"idempotency_key", "sqlfluff", "append_samples"}
        )
        normalized["resolved_object_ids"] = object_ids
        result, replayed = self.idempotency.execute(
            caller=caller,
            operation="export.create",
            key=key,
            normalized_request=normalized,
            create=lambda: self.exports.create(
                ExportBatchRequest(
                    catalog_id=command.catalog_id,
                    object_ids=object_ids,
                    output_profile=command.output_profile,
                    sample_format=command.sample_format,
                )
            ),
            serialize=_dump,
            validate=ExportStatus.model_validate,
        )
        if replayed:
            result = self.exports.status(result.export_id)
        return result, replayed

    def submit_proposals(self, request: ProposalRequest) -> ProposalBatchResult:
        batch = ClassificationProposalBatch(
            proposals=[
                ClassificationProposal(
                    object_id=item.object_id,
                    category=item.category,
                    confidence=item.confidence,
                    evidence_ids=item.evidence_ids,
                )
                for item in request.proposals
            ],
            harness=request.proposer.harness,
            skill_version=request.proposer.skill_version,
        )
        return self.classifications.intake_proposals(request.catalog_id, batch)

    def resolve(
        self, request: ClassificationResolutionBatch, *, caller: str
    ) -> ResolutionBatchResult:
        payload = request.model_dump(mode="json")
        self.approvals.require(
            caller=caller,
            operation="classification.resolve",
            target=request.catalog_id,
            payload=payload,
        )
        resolutions = {item.object_id: item.category for item in request.resolutions}
        run = self.classifications.resolve_authorized(request.catalog_id, resolutions)
        remaining = sum(
            item.pass_name == ClassificationPass.PASS_2
            and item.status != ClassificationStatus.FINAL_CONFIRMED
            for item in run.results
        )
        return ResolutionBatchResult(resolved=len(resolutions), remaining=remaining)

    def delete_catalog(self, catalog_id: str, *, caller: str) -> DeleteResult:
        payload = {"catalog_id": catalog_id}
        self.approvals.require(
            caller=caller, operation="catalog.delete", target=catalog_id, payload=payload
        )
        return self.catalogs.delete(catalog_id)

    def delete_export(self, export_id: str, *, caller: str) -> DeleteResult:
        payload = {"export_id": export_id}
        self.approvals.require(
            caller=caller, operation="export.delete", target=export_id, payload=payload
        )
        return self.exports.delete_authorized(export_id)

    def tooling_ensure(self, *, caller: str) -> HostPythonToolingDescriptor:
        status = self.manager.status()
        if status.ready:
            return status
        payload = {
            "python_executable_fingerprint": status.python_executable_fingerprint,
            "sqlfluff_version": self.manager.pinned_version,
        }
        self.approvals.require(
            caller=caller,
            operation="sqlfluff.ensure",
            target=status.python_executable_fingerprint,
            payload=payload,
        )
        return self.manager.ensure(approved=True)

    def tooling_update(self, version: str, *, caller: str) -> HostPythonToolingDescriptor:
        status = self.manager.status()
        payload = {
            "python_executable_fingerprint": status.python_executable_fingerprint,
            "version": version,
        }
        self.approvals.require(
            caller=caller,
            operation="sqlfluff.update",
            target=status.python_executable_fingerprint,
            payload=payload,
        )
        return self.manager.update(version, approved=True)

    def validate(self, request: ValidationRequest) -> ValidationResult:
        return self.exports.validate(request)
