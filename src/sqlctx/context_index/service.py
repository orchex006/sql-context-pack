"""Approval-gated service for the one-table database context index."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from sqlctx.context_index.contracts import (
    ContextIndexEntry,
    ContextIndexListRequest,
    ContextIndexPage,
    ContextIndexSyncRequest,
    ContextIndexSyncResult,
)
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import PageInfo, ResolvedConnectionProfile
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.audit import OperationAuditLogger


@dataclass(frozen=True)
class CompleteContextIndexScope:
    """Internal proof that one catalog covered the complete bounded profile scope."""

    schemas: tuple[str, ...]
    object_types: tuple[ObjectType, ...]


class MetadataContextAdapter(Protocol):
    def verify_metadata_context_schema(self, profile: ResolvedConnectionProfile) -> None: ...

    def upsert_metadata_context(
        self, profile: ResolvedConnectionProfile, entry: ContextIndexEntry, *, actor_id: int
    ) -> tuple[str, bool]: ...

    def deactivate_missing_metadata_context(
        self,
        profile: ResolvedConnectionProfile,
        *,
        schemas: tuple[str, ...],
        object_types: tuple[ObjectType, ...],
        present_identities: list[tuple[str, ObjectType, str]],
        actor_id: int,
    ) -> list[tuple[str, str, str]]: ...

    def list_metadata_context(
        self, profile: ResolvedConnectionProfile, request: ContextIndexListRequest
    ) -> tuple[list[dict[str, Any]], str | None]: ...


_METADATA_CONTEXT_ENGINES = {DatabaseEngine.SQLSERVER}


def _assert_engine_supports_metadata_context(profile: ResolvedConnectionProfile) -> None:
    """Fail closed and readably on engines with no DB_METADATA_CONTEXT implementation.

    Only the SQL Server adapter implements the metadata-context protocol. Without this
    guard the service reached straight for adapter.verify_metadata_context_schema and
    raised a bare AttributeError, which surfaced as an internal error rather than a
    stated boundary -- and since inventory sync now runs on every export, that would hit
    every export on a PostgreSQL, MySQL, MariaDB or Oracle profile.
    """
    # ResolvedConnectionProfile stores whatever the caller passed, so normalize rather than
    # assuming an enum instance and tripping over .value on a plain string.
    engine = DatabaseEngine(profile.engine)
    if engine not in _METADATA_CONTEXT_ENGINES:
        raise SqlCtxError(
            "METADATA_CONTEXT_ENGINE_UNSUPPORTED",
            "DB_METADATA_CONTEXT is currently implemented only for SQL Server; "
            f"a {engine.value} profile exports context files without a database index.",
            status_code=409,
            details={"engine": engine.value, "supported": ["sqlserver"]},
        )


class ContextIndexService:
    def __init__(self, approvals: ApprovalService) -> None:
        self.approvals = approvals
        self.audit = OperationAuditLogger(approvals.state) if approvals.state is not None else None

    def sync(
        self,
        request: ContextIndexSyncRequest,
        *,
        entries: list[ContextIndexEntry] | None = None,
        complete_scope: CompleteContextIndexScope | None = None,
        profile: ResolvedConnectionProfile,
        adapter: MetadataContextAdapter,
        caller: str,
    ) -> ContextIndexSyncResult:
        sync_entries = request.entries if entries is None else entries
        _assert_engine_supports_metadata_context(profile)
        if not profile.metadata_context_write:
            raise SqlCtxError(
                "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED",
                "The selected profile does not enable metadata_context_write. This is a "
                "one-time owner opt-in, not a per-run approval; ask the owner to run "
                f"`sqlctx profile write-scope --profile {request.profile} "
                "--metadata-context-write` and then retry the same sync.",
                status_code=403,
                details={
                    "owner_command": (
                        f"sqlctx profile write-scope --profile {request.profile} "
                        "--metadata-context-write"
                    )
                },
            )
        if (request.complete_catalog_id is None) != (complete_scope is None):
            raise SqlCtxError(
                "METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED",
                "Complete synchronization requires a verified matching catalog scope.",
                status_code=409,
            )
        for entry in sync_entries:
            if entry.schema_name not in profile.allowed_schemas:
                raise SqlCtxError(
                    "SCHEMA_NOT_ALLOWED",
                    "Context-index entry is outside the profile schema allowlist.",
                    status_code=403,
                )
            if ObjectType(entry.object_type) not in profile.allowed_object_types:
                raise SqlCtxError(
                    "OBJECT_TYPE_NOT_ALLOWED",
                    "Context-index entry is outside the profile object-type allowlist.",
                    status_code=403,
                )
        # Inventory sync only writes machine-derived identity and technical fields, and the
        # adapter MERGE already refuses to overwrite an OWNER classification. Gating it behind
        # a single-use approval is what made the agent skip the step entirely, leaving the
        # index at whatever few rows were hand-resolved. Owner-supervised writes and
        # deactivation stay gated.
        if request.mode == "owner":
            payload = request.model_dump(mode="json")
            self.approvals.require(
                caller=caller,
                operation="metadata_context.sync",
                target=request.profile,
                payload=payload,
            )
        adapter.verify_metadata_context_schema(profile)
        inserted = 0
        updated = 0
        owner_values_preserved = 0
        unchanged = 0
        for entry in sync_entries:
            identity_hash = (
                "sha256:"
                + hashlib.sha256(
                    f"{entry.object_type}:{entry.schema_name}.{entry.object_name}".encode()
                ).hexdigest()
            )
            try:
                action, preserved = adapter.upsert_metadata_context(
                    profile, entry, actor_id=request.actor_id
                )
            except Exception as exc:
                if self.audit is not None:
                    self.audit.record(
                        transport="service",
                        caller=caller,
                        operation="metadata_context.sync_item",
                        outcome="failed",
                        duration_ms=0,
                        error_code=(exc.code if isinstance(exc, SqlCtxError) else "INTERNAL_ERROR"),
                        object_identity_hash=identity_hash,
                    )
                raise
            if action not in {"insert", "update", "unchanged"}:
                raise SqlCtxError(
                    "METADATA_CONTEXT_WRITE_FAILED", "Metadata upsert returned an invalid action."
                )
            if self.audit is not None:
                self.audit.record(
                    transport="service",
                    caller=caller,
                    operation="metadata_context.sync_item",
                    outcome="succeeded",
                    duration_ms=0,
                    object_identity_hash=identity_hash,
                )
            inserted += int(action == "insert")
            updated += int(action == "update")
            unchanged += int(action == "unchanged")
            owner_values_preserved += int(preserved)
        deactivated_identities: list[tuple[str, str, str]] = []
        if complete_scope is not None:
            deactivated_identities = adapter.deactivate_missing_metadata_context(
                profile,
                schemas=complete_scope.schemas,
                object_types=complete_scope.object_types,
                present_identities=[
                    (entry.schema_name, ObjectType(entry.object_type), entry.object_name)
                    for entry in sync_entries
                ],
                actor_id=request.actor_id,
            )
            if self.audit is not None:
                for schema_name, object_type, object_name in deactivated_identities:
                    identity_hash = (
                        "sha256:"
                        + hashlib.sha256(
                            f"{object_type.lower()}:{schema_name}.{object_name}".encode()
                        ).hexdigest()
                    )
                    self.audit.record(
                        transport="service",
                        caller=caller,
                        operation="metadata_context.deactivate_item",
                        outcome="succeeded",
                        duration_ms=0,
                        object_identity_hash=identity_hash,
                    )
        return ContextIndexSyncResult(
            inserted=inserted,
            updated=updated,
            owner_values_preserved=owner_values_preserved,
            unchanged=unchanged,
            deactivated=len(deactivated_identities),
        )

    def list(
        self,
        request: ContextIndexListRequest,
        *,
        profile: ResolvedConnectionProfile,
        adapter: MetadataContextAdapter,
    ) -> ContextIndexPage:
        _assert_engine_supports_metadata_context(profile)
        adapter.verify_metadata_context_schema(profile)
        rows, next_cursor = adapter.list_metadata_context(profile, request)
        items = [self._entry(row) for row in rows]
        return ContextIndexPage(
            items=items,
            page=PageInfo(limit=request.limit, returned=len(items), next_cursor=next_cursor),
        )

    @staticmethod
    def _entry(row: dict[str, Any]) -> ContextIndexEntry:
        try:
            tags = json.loads(str(row.get("tags_json") or "[]"))
            evidence = json.loads(str(row.get("evidence_json") or "[]"))
        except json.JSONDecodeError as exc:
            raise SqlCtxError(
                "METADATA_CONTEXT_SCHEMA_DRIFT", "Stored metadata JSON is invalid.", status_code=409
            ) from exc
        return ContextIndexEntry(
            schema_name=str(row["schema_name"]),
            object_name=str(row["object_name"]),
            object_type=ObjectType(str(row["object_type"]).lower()),
            context=(str(row["context_code"]) if row.get("context_code") is not None else None),
            description=(str(row["description"]) if row.get("description") is not None else None),
            tags=[str(item) for item in tags],
            classification_status=cast(
                Literal["confirmed", "unresolved"],
                str(row["classification_status"]).lower(),
            ),
            classification_source=cast(
                Literal["owner", "rule", "unknown"],
                str(row["classification_source"]).lower(),
            ),
            source_fingerprint=(
                str(row["source_fingerprint"])
                if row.get("source_fingerprint") is not None
                else None
            ),
            content_hash=str(row["content_hash"]) if row.get("content_hash") is not None else None,
            managed_relative_path=(
                str(row["managed_relative_path"])
                if row.get("managed_relative_path") is not None
                else None
            ),
            header_version=cast(Literal[1], int(row["header_version"])),
            output_format_version=cast(Literal["2"], str(row["output_format_version"])),
            evidence=[str(item) for item in evidence],
            active=not bool(row.get("del_flag", False)),
            last_classified_at=row.get("last_classified_at"),
            last_generated_at=row.get("last_generated_at"),
        )
