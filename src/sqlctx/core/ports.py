"""Application ports; adapters and transports depend on these contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from sqlctx.core.models import (
    CatalogSnapshot,
    ClassificationPassResult,
    ConnectionProfileDescriptor,
    DatabaseCapabilities,
    DatabaseObject,
    DependencyEdge,
    ExportArtifact,
    ExportJob,
    MaskingDecision,
    ObjectRef,
    ResolvedConnectionProfile,
    SamplePage,
    SqlFormatResult,
)


class ConnectionProfileRepository(Protocol):
    def list_descriptors(self) -> list[ConnectionProfileDescriptor]: ...
    def resolve(self, profile_name: str) -> ResolvedConnectionProfile: ...


class DatabaseAdapter(Protocol):
    def capabilities(self) -> DatabaseCapabilities: ...
    def test_connection(self, profile: ResolvedConnectionProfile) -> None: ...
    def discover_objects(self, profile: ResolvedConnectionProfile) -> Iterable[ObjectRef]: ...
    def extract_object(
        self, profile: ResolvedConnectionProfile, object_ref: ObjectRef
    ) -> DatabaseObject: ...
    def sample_rows(
        self, profile: ResolvedConnectionProfile, object_ref: ObjectRef, requested: int
    ) -> SamplePage: ...
    def cancel(self) -> bool: ...


class MaskingEngine(Protocol):
    def mask(self, *, column_name: str, value: Any, snapshot_id: str) -> MaskingDecision: ...


class SqlFormatter(Protocol):
    def format_one(self, *, object_id: str, sql: str, dialect: str) -> SqlFormatResult: ...


class CategoryClassifier(Protocol):
    def classify(
        self, objects: Sequence[DatabaseObject], dependencies: Sequence[DependencyEdge]
    ) -> list[ClassificationPassResult]: ...


class DependencyAnalyzer(Protocol):
    def analyze(self, objects: Sequence[DatabaseObject]) -> list[DependencyEdge]: ...


class ExportStore(Protocol):
    def save(self, job: ExportJob, artifact: ExportArtifact) -> None: ...
    def get(self, export_id: str) -> tuple[ExportJob, ExportArtifact | None]: ...


class RuntimeStateStore(Protocol):
    def save_catalog(self, snapshot: CatalogSnapshot) -> None: ...
    def get_catalog(self, catalog_id: str) -> CatalogSnapshot: ...
    def put_secret(self, namespace: str, key: str, value: bytes) -> None: ...
    def get_secret(self, namespace: str, key: str) -> bytes | None: ...


class AuditSink(Protocol):
    def record(self, event: str, fields: Mapping[str, Any]) -> None: ...
