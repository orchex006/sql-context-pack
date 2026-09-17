"""Transport-safe domain contracts.

The only credential-bearing type is ``ResolvedConnectionProfile``. It is deliberately not a
Pydantic model and cannot be serialized by the public model helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from sqlctx._version import OUTPUT_FORMAT_VERSION
from sqlctx.core.enums import (
    ClassificationPass,
    ClassificationStatus,
    ConstraintType,
    DatabaseEngine,
    EdgeType,
    FormatStatus,
    InclusionReason,
    JobStatus,
    MaterializationMode,
    ObjectType,
    OutputProfile,
    SampleOutputFormat,
    SensitivityClass,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=True)


class PageInfo(PublicModel):
    limit: int = Field(ge=1, le=250)
    returned: int = Field(ge=0)
    next_cursor: str | None = None


class ConnectionProfileDescriptor(PublicModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    engine: DatabaseEngine
    allowed_schemas: list[str]
    allowed_object_types: list[ObjectType]
    excluded_object_patterns: list[str] = Field(default_factory=list)
    sample_rows_per_table: int = Field(default=10, ge=10)
    trust_server_certificate: bool = False
    metadata_context_write: bool = False
    routine_write: bool = False
    ready: bool
    readiness_reason: str | None = None


class ResolvedConnectionProfile:
    """Internal-only resolved credentials with a permanently redacted representation."""

    __slots__ = (
        "name",
        "engine",
        "allowed_schemas",
        "allowed_object_types",
        "excluded_object_patterns",
        "sample_rows_per_table",
        "trust_server_certificate",
        "metadata_context_write",
        "routine_write",
        "_host",
        "_port",
        "_database",
        "_username",
        "_password",
    )

    def __init__(
        self,
        *,
        name: str,
        engine: DatabaseEngine,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        allowed_schemas: tuple[str, ...],
        allowed_object_types: tuple[ObjectType, ...],
        excluded_object_patterns: tuple[str, ...] = (),
        sample_rows_per_table: int = 10,
        trust_server_certificate: bool = False,
        metadata_context_write: bool = False,
        routine_write: bool = False,
    ) -> None:
        self.name = name
        self.engine = engine
        self.allowed_schemas = allowed_schemas
        self.allowed_object_types = allowed_object_types
        self.excluded_object_patterns = excluded_object_patterns
        self.sample_rows_per_table = sample_rows_per_table
        self.trust_server_certificate = trust_server_certificate
        self.metadata_context_write = metadata_context_write
        self.routine_write = routine_write
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password

    def __repr__(self) -> str:
        return (
            "ResolvedConnectionProfile("
            f"name={self.name!r}, engine={self.engine!r}, credentials='[REDACTED]')"
        )

    __str__ = __repr__

    def connection_values(self) -> tuple[str, int, str, str, str]:
        """Return credentials only to an adapter inside the trusted application boundary."""
        return self._host, self._port, self._database, self._username, self._password


class DatabaseCapabilities(PublicModel):
    engine: DatabaseEngine
    sqlfluff_dialect: str
    supports_tables: bool = True
    supports_procedures: bool = True
    supports_query_cancel: bool = False
    supports_native_dependencies: bool = True
    supports_consistent_snapshot: bool = False
    warnings: list[str] = Field(default_factory=list)


class ObjectRef(PublicModel):
    object_id: str
    engine: DatabaseEngine
    schema_name: str
    object_name: str
    object_type: ObjectType


class ColumnMetadata(PublicModel):
    name: str
    data_type: str
    nullable: bool
    ordinal: int = Field(ge=1)
    sensitivity: SensitivityClass = SensitivityClass.PUBLIC


class ConstraintMetadata(PublicModel):
    name: str
    constraint_type: ConstraintType
    columns: list[str]
    expression: str | None = None


class ForeignKeyMetadata(PublicModel):
    name: str
    source_object_id: str
    source_columns: list[str]
    target_object_id: str
    target_columns: list[str]


class IndexMetadata(PublicModel):
    name: str
    unique: bool = False
    primary: bool = False
    key_columns: list[str] = Field(default_factory=list)
    included_columns: list[str] = Field(default_factory=list)


class DependencyEdge(PublicModel):
    source_object_id: str
    target_object_id: str
    edge_type: EdgeType
    evidence: list[str] = Field(default_factory=list)
    boundary_only: bool = False


class DatabaseObject(PublicModel):
    ref: ObjectRef
    columns: list[ColumnMetadata] = Field(default_factory=list)
    constraints: list[ConstraintMetadata] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyMetadata] = Field(default_factory=list)
    sanitized_definition: str | None = None
    native_comment: str | None = None
    indexes: list[IndexMetadata] = Field(default_factory=list)
    source_fingerprint: str | None = None


class CatalogSnapshot(PublicModel):
    catalog_id: str
    profile_name: str
    request_fingerprint: str
    status: JobStatus
    objects: list[DatabaseObject] = Field(default_factory=list)
    dependencies: list[DependencyEdge] = Field(default_factory=list)
    samples: dict[str, SamplePage] = Field(default_factory=dict)
    capabilities: DatabaseCapabilities | None = None
    classifications: list[ClassificationPassResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None


CatalogFailureStage = Literal["analysis", "sample", "dependencies"]


class CatalogObjectFailure(PublicModel):
    """One named object that did not complete a catalog stage, with a sanitized reason."""

    object_id: str
    object_type: ObjectType
    stage: CatalogFailureStage
    error_code: str
    message: str = Field(max_length=200)


class CatalogStatus(PublicModel):
    catalog_id: str
    status: JobStatus
    request_fingerprint: str
    discovered_object_count: int = Field(default=0, ge=0)
    fully_analyzed_object_count: int = Field(default=0, ge=0)
    analysis_failed_object_count: int = Field(default=0, ge=0)
    materialized_object_count: int = Field(default=0, ge=0)
    intentionally_excluded_object_count: int = Field(default=0, ge=0)
    cache_hit: bool = False
    cache_expires_at: datetime | None = None
    phase: str = "discovery"
    total_object_count: int = Field(default=0, ge=0)
    processed_object_count: int = Field(default=0, ge=0)
    reused_object_count: int = Field(default=0, ge=0)
    current_object_id: str | None = None
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    failures: list[CatalogObjectFailure] = Field(default_factory=list)


class SyncDataContextResult(PublicModel):
    profile: str
    previous_catalog_id: str
    catalog_id: str | None = None
    status: Literal["synced", "failed"]
    added_object_count: int = Field(default=0, ge=0)
    changed_object_count: int = Field(default=0, ge=0)
    deleted_object_count: int = Field(default=0, ge=0)
    reused_object_count: int = Field(default=0, ge=0)
    refreshed_object_count: int = Field(default=0, ge=0)
    definition_change_detection_complete: bool = False
    error_code: str | None = None


class SyncDataResult(PublicModel):
    considered_context_count: int = Field(ge=0)
    synced_context_count: int = Field(ge=0)
    skipped_context_count: int = Field(ge=0)
    failed_context_count: int = Field(ge=0)
    added_object_count: int = Field(ge=0)
    changed_object_count: int = Field(ge=0)
    deleted_object_count: int = Field(ge=0)
    reused_object_count: int = Field(ge=0)
    refreshed_object_count: int = Field(ge=0)
    definition_change_detection_complete: bool
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    contexts: list[SyncDataContextResult] = Field(default_factory=list)


class SitemapItem(PublicModel):
    object_id: str
    category: str | None
    object_type: ObjectType
    inclusion_reason: InclusionReason
    content_hash: str | None = None


class SitemapPage(PublicModel):
    items: list[SitemapItem]
    page: PageInfo
    recommended_batch_size: int = Field(default=10, ge=1, le=25)
    maximum_batch_size: int = Field(default=25, ge=1, le=25)


class CategoryPreviewGroup(PublicModel):
    category: str
    object_count: int = Field(ge=0)
    representative_names: list[str] = Field(default_factory=list, max_length=10)


class CategoryPreview(PublicModel):
    items: list[CategoryPreviewGroup]
    unresolved_count: int = Field(default=0, ge=0)
    page: PageInfo


class MaterializationSelection(PublicModel):
    mode: MaterializationMode
    selected_categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self) -> MaterializationSelection:
        if self.mode == MaterializationMode.SELECTED and not self.selected_categories:
            raise ValueError("selected mode requires at least one category")
        if self.mode != MaterializationMode.SELECTED and self.selected_categories:
            raise ValueError("selected_categories is valid only in selected mode")
        return self


class MaterializationPlanItem(PublicModel):
    object_id: str
    final_category: str | None
    included: bool
    reason: InclusionReason


class MaterializationPlan(PublicModel):
    catalog_id: str
    selection: MaterializationSelection
    always_included_categories: list[str] = Field(default_factory=lambda: ["lut"])
    analysis_scope_restricted_by_selection: Literal[False] = False
    items: list[MaterializationPlanItem]


class ClassificationCandidate(PublicModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]


class ClassificationPassResult(PublicModel):
    object_id: str
    pass_name: ClassificationPass
    status: ClassificationStatus
    category: str | None = None
    candidates: list[ClassificationCandidate] = Field(default_factory=list)


class ClassificationProposal(PublicModel):
    object_id: str
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)


class ClassificationProposalBatch(PublicModel):
    proposals: list[ClassificationProposal] = Field(min_length=1, max_length=100)
    harness: Literal["codex", "claude", "gemini", "agy", "other"]
    skill_version: str


class ProposalBatchResult(PublicModel):
    accepted_as_suggestion: int = Field(ge=0)
    rejected: int = Field(ge=0)
    requires_owner_resolution: int = Field(ge=0)


class ClassificationRequest(PublicModel):
    request_id: str
    object_id: str
    current_categories: list[str]
    suggested_new_categories: list[str] = Field(default_factory=list)
    sanitized_evidence: list[str]
    candidates: list[ClassificationCandidate]
    unresolved_option: Literal[True] = True


class ClassificationRequestPage(PublicModel):
    items: list[ClassificationRequest]
    page: PageInfo


class EvidenceRecord(PublicModel):
    evidence_id: str
    object_id: str
    kind: str
    summary: str


class ClassificationChange(PublicModel):
    object_id: str
    pass_1_category: str | None
    pass_2_category: str | None
    moved_into_selected: bool = False
    moved_out_of_selected: bool = False


class ClassificationResolution(PublicModel):
    object_id: str
    category: str
    persist_as_owner_override: bool = True


class SampleRequest(PublicModel):
    object_id: str
    requested_rows: int = Field(default=10, ge=10, le=20)


class SamplePage(PublicModel):
    object_id: str
    columns: list[str]
    rows: list[list[Any]]
    requested_count: int = Field(ge=0)
    actual_count: int = Field(ge=0)
    shortage_reason: str | None = None
    deterministic: bool
    sampling_order: list[str] = Field(default_factory=list)
    all_rows: bool = False
    complete: bool = True
    page_count: int = Field(default=1, ge=0)


class MaskingDecision(PublicModel):
    sensitivity: SensitivityClass
    action: Literal["keep", "redact", "alias", "generalize"]
    masked_value: Any
    rule: str


class SqlFormatResult(PublicModel):
    object_id: str
    status: FormatStatus
    content: str
    diagnostics: list[str] = Field(default_factory=list)
    sqlfluff_version: str
    tooling_fingerprint: str
    cache_hit: bool = False


class ExportBatchRequest(PublicModel):
    catalog_id: str
    object_ids: list[str] = Field(min_length=1)
    output_profile: OutputProfile = OutputProfile.AI
    sample_format: SampleOutputFormat = SampleOutputFormat.MARKDOWN


class ExportJob(PublicModel):
    export_id: str
    catalog_id: str
    status: JobStatus
    request_fingerprint: str
    object_batch_fingerprint: str
    python_executable_fingerprint: str
    python_version: str
    sqlfluff_version: str
    tooling_fingerprint: str
    output_format_version: str = OUTPUT_FORMAT_VERSION
    output_profile: OutputProfile = OutputProfile.AI
    sample_format: SampleOutputFormat = SampleOutputFormat.MARKDOWN
    requested_object_count: int = Field(default=0, ge=0)
    processed_object_count: int = Field(default=0, ge=0)
    reused_object_count: int = Field(default=0, ge=0)
    skipped_object_count: int = Field(default=0, ge=0)
    failed_object_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    phase: str = "queued"
    current_object_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    expires_at: datetime | None = None


class ExportArtifact(PublicModel):
    export_id: str
    size_bytes: int = Field(ge=0)
    sha256: str
    manifest_sha256: str
    bundle_url: str
    manifest_url: str
    report_url: str


class ExportObjectCounts(PublicModel):
    requested: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    parse_failed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped_security: int = Field(default=0, ge=0)
    reused: int = Field(default=0, ge=0)


class ExportStatus(ExportJob):
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    manifest_sha256: str | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    objects: ExportObjectCounts
    artifacts: ExportArtifact | None = None


class ExportReport(PublicModel):
    export_id: str
    catalog_id: str
    status: JobStatus
    object_results: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class CatalogJobDescriptor(PublicModel):
    catalog_id: str
    profile_name: str
    status: JobStatus
    request_fingerprint: str
    safe_request_summary: dict[str, Any]
    expires_at: datetime | None = None


class CatalogJobPage(PublicModel):
    items: list[CatalogJobDescriptor]
    page: PageInfo


class ExportJobDescriptor(PublicModel):
    export: ExportJob
    artifact: ExportArtifact | None = None


class ExportJobPage(PublicModel):
    items: list[ExportJobDescriptor]
    page: PageInfo


class DeleteResult(PublicModel):
    deleted: bool
    target_id: str


class AssembledFile(PublicModel):
    relative_path: str = Field(
        validation_alias=AliasChoices("relative_path", "path"),
        serialization_alias="path",
    )
    size_bytes: int = Field(ge=0)
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value:
            raise ValueError("relative_path must remain inside the output root")
        return path.as_posix()


class AssembledInventory(PublicModel):
    files: list[AssembledFile]
    managed_manifest_sha256: str
    inventory_sha256: str


class ValidationRequest(PublicModel):
    catalog_id: str
    export_ids: list[str] = Field(min_length=1)
    expected_discovered_count: int = Field(ge=0)
    expected_analyzed_count: int = Field(ge=0)
    expected_materialized_count: int = Field(ge=0)
    expected_output_format_version: str
    assembled_inventory: AssembledInventory


class ApprovalChallenge(PublicModel):
    challenge_id: str
    request_digest: str
    operation: str
    target: str
    expires_at: datetime


class HostPythonToolingDescriptor(PublicModel):
    python_executable_fingerprint: str
    python_version: str
    environment_owner: Literal["host", "owner"]
    sqlfluff_version: str | None = None
    tooling_fingerprint: str | None = None
    ready: bool
    update_blocked_by_active_jobs: bool = False


class ValidationResult(PublicModel):
    valid: bool
    discovered_count_valid: bool
    analyzed_count_valid: bool
    materialized_count_valid: bool
    exclusion_count_valid: bool
    output_format_version_valid: bool
    manifest_hashes_valid: bool
    assembled_inventory_complete: bool
    assembled_files_match_export_manifests: bool
    errors: list[str] = Field(default_factory=list)
