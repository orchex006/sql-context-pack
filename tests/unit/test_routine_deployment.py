from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.core.models import ResolvedConnectionProfile
from sqlctx.exporting.header import ManagedSqlHeader, render_managed_sql
from sqlctx.managed_files.contracts import (
    FolderClassificationPlanRequest,
    OwnerFileResolution,
)
from sqlctx.managed_files.service import ManagedFolderService
from sqlctx.routine_deploy.contracts import RoutinePlanRequest
from sqlctx.routine_deploy.service import RoutineDeploymentService
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore


def _hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="writer",
        engine=DatabaseEngine.SQLSERVER,
        host="host",
        port=1433,
        database="db",
        username="user",
        password="secret",
        allowed_schemas=("dbo",),
        allowed_object_types=(ObjectType.PROCEDURE, ObjectType.FUNCTION),
        routine_write=True,
    )


class FakeAdapter:
    engine = DatabaseEngine.SQLSERVER

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.fingerprints: dict[str, str] = {}

    def object_fingerprints(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> dict[str, str]:
        return dict(self.fingerprints)

    def apply_routine_statement(
        self,
        profile: ResolvedConnectionProfile,
        statement: str,
        object_type: ObjectType,
    ) -> None:
        assert object_type == ObjectType.PROCEDURE
        self.statements.append(statement)


def test_single_routine_plan_and_approved_apply(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    body = "CREATE OR ALTER PROCEDURE [dbo].[P] AS SELECT 1;\n"
    header = ManagedSqlHeader(
        object_id="procedure:dbo.P",
        engine=DatabaseEngine.SQLSERVER,
        schema_name="dbo",
        object_name="P",
        object_type=ObjectType.PROCEDURE,
        context="app_state",
        tags=["app_state"],
        classification_status="confirmed",
        classification_source="owner",
        content_hash=_hash(body),
        output_format_version="2",
    )
    (root / "P.sql").write_text(render_managed_sql(header, body), encoding="utf-8")
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    folders = ManagedFolderService(state, approvals)
    folder = folders.register(input_root=root, output_root=root, engine=DatabaseEngine.SQLSERVER)
    service = RoutineDeploymentService(state, folders, approvals)
    plan = service.plan(
        RoutinePlanRequest(
            profile="writer",
            folder_id=folder.folder_id,
            relative_path="P.sql",
            idempotency_key="routine-0001",
        ),
        profile=_profile(),
        caller="agent:x",
    )
    adapter = FakeAdapter()
    with pytest.raises(ApprovalRequired):
        service.apply(plan.plan_id, profile=_profile(), adapter=adapter, caller="agent:x")
    challenge = approvals.list_challenges()[0]
    approvals.grant(str(challenge["challenge_id"]), interactive=True)
    result = service.apply(plan.plan_id, profile=_profile(), adapter=adapter, caller="agent:x")
    assert result.applied == 1
    assert adapter.statements[0].startswith("CREATE OR ALTER PROCEDURE")
    events = list((state.root / "audit/events").rglob("*.json"))
    assert len(events) == 1
    event = json.loads(events[0].read_text(encoding="utf-8"))
    assert event["operation"] == "routine.apply_item"
    assert event["outcome"] == "succeeded"
    assert event["object_identity_hash"].startswith("sha256:")
    assert "procedure:dbo.P" not in json.dumps(event)


def test_routine_apply_rejects_database_drift_before_approval(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    body = "CREATE OR ALTER PROCEDURE [dbo].[P] AS SELECT 1;\n"
    source_fingerprint = "sha256:" + "a" * 64
    header = ManagedSqlHeader(
        object_id="procedure:dbo.P",
        engine=DatabaseEngine.SQLSERVER,
        schema_name="dbo",
        object_name="P",
        object_type=ObjectType.PROCEDURE,
        context="app_state",
        tags=["app_state"],
        classification_status="confirmed",
        classification_source="owner",
        source_fingerprint=source_fingerprint,
        content_hash=_hash(body),
        output_format_version="2",
    )
    (root / "P.sql").write_text(render_managed_sql(header, body), encoding="utf-8")
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    folders = ManagedFolderService(state, approvals)
    folder = folders.register(input_root=root, output_root=root, engine=DatabaseEngine.SQLSERVER)
    service = RoutineDeploymentService(state, folders, approvals)
    adapter = FakeAdapter()
    adapter.fingerprints["procedure:dbo.P"] = source_fingerprint
    plan = service.plan(
        RoutinePlanRequest(
            profile="writer",
            folder_id=folder.folder_id,
            relative_path="P.sql",
            idempotency_key="routine-0002",
        ),
        profile=_profile(),
        caller="agent:x",
        adapter=adapter,
    )

    adapter.fingerprints["procedure:dbo.P"] = "sha256:" + "b" * 64
    with pytest.raises(SqlCtxError, match="changed after planning"):
        service.apply(plan.plan_id, profile=_profile(), adapter=adapter, caller="agent:x")

    assert approvals.list_challenges() == []


def test_routine_plan_reads_the_applied_separate_output_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "managed"
    source.mkdir()
    (source / "P.sql").write_text("CREATE PROCEDURE [dbo].[P] AS SELECT 1;\n", encoding="utf-8")
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    folders = ManagedFolderService(state, approvals)
    folder = folders.register(
        input_root=source, output_root=output, engine=DatabaseEngine.SQLSERVER
    )
    classification = folders.plan(
        FolderClassificationPlanRequest(
            folder_id=folder.folder_id,
            resolutions=[
                OwnerFileResolution(
                    relative_path="P.sql",
                    context="app_state",
                    tags=["app_state"],
                )
            ],
        )
    )
    folders.apply(classification.plan_id)

    service = RoutineDeploymentService(state, folders, approvals)
    plan = service.plan(
        RoutinePlanRequest(
            profile="writer",
            folder_id=folder.folder_id,
            relative_path="app_state/store_procedures/P.sql",
            idempotency_key="routine-0003",
        ),
        profile=_profile(),
        caller="agent:x",
    )

    assert plan.items[0].object_id == "procedure:dbo.P"
    assert plan.items[0].relative_path == "app_state/store_procedures/P.sql"
