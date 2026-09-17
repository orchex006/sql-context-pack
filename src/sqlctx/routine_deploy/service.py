"""Immutable, approval-gated routine plan and apply service."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from sqlctx.adapters.sqlserver.adapter import SqlServerAdapter
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ResolvedConnectionProfile
from sqlctx.exporting.header import parse_managed_sql
from sqlctx.managed_files.contracts import RegisteredFolder
from sqlctx.managed_files.service import ManagedFolderService, detect_sql_identity
from sqlctx.routine_deploy.contracts import (
    RoutineApplyItemResult,
    RoutineApplyResult,
    RoutineDeploymentPlan,
    RoutinePlanItem,
    RoutinePlanRequest,
)
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.audit import OperationAuditLogger
from sqlctx.security.runtime import JsonRuntimeStateStore


class RoutineApplyAdapter(Protocol):
    engine: DatabaseEngine

    def object_fingerprints(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> dict[str, str]: ...

    def apply_routine_statement(
        self,
        profile: ResolvedConnectionProfile,
        statement: str,
        object_type: ObjectType,
    ) -> None: ...


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class RoutineDeploymentService:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        folders: ManagedFolderService,
        approvals: ApprovalService,
        *,
        plan_ttl_seconds: int = 900,
    ) -> None:
        self.state = state
        self.folders = folders
        self.approvals = approvals
        self.plan_ttl_seconds = plan_ttl_seconds
        self.audit = OperationAuditLogger(state)

    def plan(
        self,
        request: RoutinePlanRequest,
        *,
        profile: ResolvedConnectionProfile,
        caller: str,
        adapter: RoutineApplyAdapter | None = None,
    ) -> RoutineDeploymentPlan:
        folder = self._folder(request.folder_id)
        if DatabaseEngine(folder.engine) != profile.engine:
            raise SqlCtxError("ROUTINE_ENGINE_MISMATCH", "Folder and profile engines do not match.")
        if profile.engine != DatabaseEngine.SQLSERVER:
            raise SqlCtxError(
                "ROUTINE_APPLY_ENGINE_UNSUPPORTED",
                "Safe routine apply is currently implemented only for SQL Server.",
            )
        root = self.folders.managed_output_root(folder.folder_id)
        paths = (
            [self._safe_join(root, request.relative_path)]
            if request.relative_path
            else sorted(root.rglob("*.sql"))
        )
        items: list[RoutinePlanItem] = []
        identities: set[str] = set()
        private: list[dict[str, str]] = []
        for path in paths:
            if not path.is_file() or path.suffix.lower() != ".sql":
                if request.relative_path:
                    raise SqlCtxError("ROUTINE_FILE_NOT_FOUND", "Routine SQL file was not found.")
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            content = path.read_text(encoding="utf-8-sig")
            try:
                header, body = parse_managed_sql(content)
            except ValueError as exc:
                raise SqlCtxError(
                    "ROUTINE_MANAGED_HEADER_REQUIRED",
                    "Routine deployment accepts only managed SQL files.",
                ) from exc
            object_type = ObjectType(header.object_type)
            if object_type not in {ObjectType.PROCEDURE, ObjectType.FUNCTION}:
                if request.relative_path:
                    raise SqlCtxError(
                        "ROUTINE_OBJECT_TYPE_REQUIRED", "Selected file is not a routine."
                    )
                continue
            if header.schema_name not in profile.allowed_schemas:
                raise SqlCtxError(
                    "SCHEMA_NOT_ALLOWED",
                    "Routine is outside the profile schema allowlist.",
                    status_code=403,
                )
            if object_type not in profile.allowed_object_types:
                raise SqlCtxError(
                    "OBJECT_TYPE_NOT_ALLOWED",
                    "Routine is outside the profile object-type allowlist.",
                    status_code=403,
                )
            if header.content_hash != _sha256(body):
                raise SqlCtxError(
                    "ROUTINE_FILE_DRIFT", "Managed header and SQL body hashes differ."
                )
            statement = (
                SqlServerAdapter.normalize_procedure_definition(body)
                if object_type == ObjectType.PROCEDURE
                else SqlServerAdapter.normalize_function_definition(body)
            )
            detected_type, detected_schema, detected_name = detect_sql_identity(statement)
            if (
                detected_type != object_type
                or detected_schema.casefold() != header.schema_name.casefold()
                or detected_name.casefold() != header.object_name.casefold()
            ):
                raise SqlCtxError(
                    "ROUTINE_IDENTITY_MISMATCH",
                    "Managed header and routine declaration identify different objects.",
                )
            if header.object_id.casefold() in identities:
                raise SqlCtxError(
                    "ROUTINE_DUPLICATE_OBJECT_IDENTITY", "A deployment plan contains duplicates."
                )
            identities.add(header.object_id.casefold())
            item = RoutinePlanItem(
                relative_path=relative,
                object_id=header.object_id,
                object_type=object_type,
                schema_name=header.schema_name,
                object_name=header.object_name,
                content_hash=_sha256(content),
                statement_hash=_sha256(statement),
                database_fingerprint=header.source_fingerprint,
            )
            items.append(item)
            private.append({"relative_path": relative, "statement_hash": item.statement_hash})
        if not items:
            raise SqlCtxError(
                "ROUTINE_PLAN_EMPTY", "No managed procedure/function files were found."
            )
        if adapter is not None:
            current = adapter.object_fingerprints(
                profile,
                sorted({item.schema_name for item in items}),
                sorted(
                    {ObjectType(item.object_type) for item in items},
                    key=lambda value: value.value,
                ),
            )
            items = [
                item.model_copy(
                    update={
                        "database_fingerprint": current.get(item.object_id),
                        "database_fingerprint_checked": True,
                    }
                )
                for item in items
            ]
        created = datetime.now(UTC)
        payload = {
            "profile": request.profile,
            "folder_id": request.folder_id,
            "engine": profile.engine,
            "requester": caller,
            "stop_on_error": request.stop_on_error,
            "items": [item.model_dump(mode="json") for item in items],
        }
        plan = RoutineDeploymentPlan(
            plan_id="rpl_" + secrets.token_urlsafe(12),
            profile=request.profile,
            folder_id=request.folder_id,
            engine=profile.engine,
            requester=caller,
            stop_on_error=request.stop_on_error,
            created_at=created,
            expires_at=created + timedelta(seconds=self.plan_ttl_seconds),
            plan_hash=_sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            items=items,
        )
        self.state.write_json(
            f"routine-plans/{plan.plan_id}.json",
            {"plan": plan.model_dump(mode="json"), "private": private},
        )
        return plan

    def apply(
        self,
        plan_id: str,
        *,
        profile: ResolvedConnectionProfile,
        adapter: RoutineApplyAdapter,
        caller: str,
    ) -> RoutineApplyResult:
        value = self.state.read_json(f"routine-plans/{plan_id}.json")
        if not isinstance(value, dict):
            raise SqlCtxError("ROUTINE_PLAN_NOT_FOUND", "Routine deployment plan was not found.")
        plan = RoutineDeploymentPlan.model_validate(value["plan"])
        if plan.expires_at <= datetime.now(UTC):
            raise SqlCtxError("ROUTINE_PLAN_EXPIRED", "Routine deployment plan has expired.")
        if plan.requester != caller or plan.profile != profile.name:
            raise SqlCtxError("ROUTINE_PLAN_BINDING_MISMATCH", "Routine plan binding changed.")
        if profile.engine != DatabaseEngine.SQLSERVER or adapter.engine != DatabaseEngine.SQLSERVER:
            raise SqlCtxError(
                "ROUTINE_APPLY_ENGINE_UNSUPPORTED",
                "Safe routine apply is currently implemented only for SQL Server.",
            )
        if not profile.routine_write:
            raise SqlCtxError(
                "ROUTINE_WRITE_SCOPE_REQUIRED",
                "The selected profile does not enable routine_write.",
                status_code=403,
            )
        checked_items = [item for item in plan.items if item.database_fingerprint_checked]
        if checked_items:
            current = adapter.object_fingerprints(
                profile,
                sorted({item.schema_name for item in checked_items}),
                sorted(
                    {ObjectType(item.object_type) for item in checked_items},
                    key=lambda value: value.value,
                ),
            )
            drifted = [
                item.object_id
                for item in checked_items
                if current.get(item.object_id) != item.database_fingerprint
            ]
            if drifted:
                raise SqlCtxError(
                    "ROUTINE_DATABASE_DRIFT",
                    "One or more database routines changed after planning.",
                    status_code=409,
                    details={"object_ids": drifted},
                )
        folder = self._folder(plan.folder_id)
        root = self.folders.managed_output_root(folder.folder_id)
        prepared: list[tuple[RoutinePlanItem, ObjectType, str]] = []
        for item in plan.items:
            path = self._safe_join(root, item.relative_path)
            try:
                content = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise SqlCtxError(
                    "ROUTINE_PLAN_CONTENT_DRIFT",
                    "A planned routine file is missing or unreadable.",
                    status_code=409,
                ) from exc
            if _sha256(content) != item.content_hash:
                raise SqlCtxError(
                    "ROUTINE_PLAN_CONTENT_DRIFT",
                    "A routine changed after planning.",
                    status_code=409,
                )
            try:
                header, body = parse_managed_sql(content)
            except ValueError as exc:
                raise SqlCtxError(
                    "ROUTINE_PLAN_CONTENT_DRIFT",
                    "A managed routine header changed after planning.",
                    status_code=409,
                ) from exc
            object_type = ObjectType(header.object_type)
            if object_type != ObjectType(item.object_type):
                raise SqlCtxError(
                    "ROUTINE_PLAN_IDENTITY_DRIFT", "Routine type changed after planning."
                )
            statement = (
                SqlServerAdapter.normalize_procedure_definition(body)
                if object_type == ObjectType.PROCEDURE
                else SqlServerAdapter.normalize_function_definition(body)
            )
            detected_type, detected_schema, detected_name = detect_sql_identity(statement)
            if (
                detected_type != object_type
                or detected_schema.casefold() != header.schema_name.casefold()
                or detected_name.casefold() != header.object_name.casefold()
            ):
                raise SqlCtxError("ROUTINE_IDENTITY_MISMATCH", "Routine identity changed.")
            if (
                header.object_id != item.object_id
                or header.schema_name != item.schema_name
                or header.object_name != item.object_name
                or _sha256(statement) != item.statement_hash
            ):
                raise SqlCtxError("ROUTINE_PLAN_IDENTITY_DRIFT", "Routine identity/hash changed.")
            prepared.append((item, object_type, statement))
        self.approvals.require(
            caller=caller,
            operation="routine.apply",
            target=profile.name,
            payload={"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
        )
        results: list[RoutineApplyItemResult] = []
        failed = False
        for item, object_type, statement in prepared:
            if failed and plan.stop_on_error:
                results.append(
                    RoutineApplyItemResult(
                        object_id=item.object_id,
                        relative_path=item.relative_path,
                        status="skipped",
                    )
                )
                self._audit_item(item, caller=caller, outcome="skipped")
                continue
            try:
                adapter.apply_routine_statement(profile, statement, object_type)
                results.append(
                    RoutineApplyItemResult(
                        object_id=item.object_id,
                        relative_path=item.relative_path,
                        status="applied",
                    )
                )
                self._audit_item(item, caller=caller, outcome="succeeded")
            except SqlCtxError as exc:
                failed = True
                results.append(
                    RoutineApplyItemResult(
                        object_id=item.object_id,
                        relative_path=item.relative_path,
                        status="failed",
                        error_code=exc.code,
                    )
                )
                self._audit_item(item, caller=caller, outcome="failed", error_code=exc.code)
        return RoutineApplyResult(
            plan_id=plan.plan_id,
            applied=sum(item.status == "applied" for item in results),
            failed=sum(item.status == "failed" for item in results),
            skipped=sum(item.status == "skipped" for item in results),
            items=results,
        )

    def _audit_item(
        self,
        item: RoutinePlanItem,
        *,
        caller: str,
        outcome: Literal["succeeded", "failed", "skipped"],
        error_code: str | None = None,
    ) -> None:
        self.audit.record(
            transport="service",
            caller=caller,
            operation="routine.apply_item",
            outcome=outcome,
            duration_ms=0,
            error_code=error_code,
            object_identity_hash=_sha256(item.object_id),
        )

    def _folder(self, folder_id: str) -> RegisteredFolder:
        matches = [item for item in self.folders.list_registered() if item.folder_id == folder_id]
        if not matches:
            raise SqlCtxError(
                "FOLDER_NOT_FOUND", "Registered folder was not found.", status_code=404
            )
        return matches[0]

    @staticmethod
    def _safe_join(root: Path, relative: str | None) -> Path:
        if relative is None:
            raise SqlCtxError("ROUTINE_FILE_REQUIRED", "A relative file path is required.")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise SqlCtxError("FOLDER_PATH_ESCAPE", "Routine path escaped its root.") from exc
        return candidate
