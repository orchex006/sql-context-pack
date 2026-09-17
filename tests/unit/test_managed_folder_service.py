from __future__ import annotations

import json
from pathlib import Path

import pytest

from sqlctx.classification.rules import CategoryConfig, CategoryRule
from sqlctx.core.enums import DatabaseEngine
from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.exporting.header import parse_managed_sql
from sqlctx.managed_files.contracts import (
    FolderClassificationPlanRequest,
    ManagedFileResolutionPlanRequest,
    OwnerFileResolution,
)
from sqlctx.managed_files.service import ManagedFolderService, detect_sql_identity
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore


def _service(tmp_path: Path) -> ManagedFolderService:
    state = JsonRuntimeStateStore(tmp_path / "state")
    return ManagedFolderService(state, ApprovalService(state=state))


def test_folder_plan_materializes_unknown_and_owner_context(tmp_path: Path) -> None:
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    (source / "p.sql").write_text("CREATE PROCEDURE [dbo].[P] AS SELECT 1;\n", encoding="utf-8")
    service = _service(tmp_path)
    folder = service.register(
        input_root=source, output_root=output, engine=DatabaseEngine.SQLSERVER
    )

    unknown = service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id))
    assert unknown.items[0].destination_relative_path == "unknowns/store_procedures/p.sql"
    assert unknown.items[0].classification_status == "unresolved"

    confirmed = service.plan(
        FolderClassificationPlanRequest(
            folder_id=folder.folder_id,
            resolutions=[
                OwnerFileResolution(
                    relative_path="p.sql",
                    context="app_state",
                    description="สถานะแอป",
                    tags=["app_state", "share"],
                )
            ],
        )
    )
    with pytest.raises(SqlCtxError, match="successfully applied"):
        service.entries_from_plan(confirmed.plan_id)
    result = service.apply(confirmed.plan_id)
    target = output / "app_state/store_procedures/p.sql"
    header, body = parse_managed_sql(target.read_text(encoding="utf-8"))
    assert result.written == 1
    assert header.context == "app_state"
    assert header.tags == ["app_state", "share"]
    assert body.startswith("CREATE OR ALTER PROCEDURE")
    manifest = json.loads((output / result.manifest_relative_path).read_text(encoding="utf-8"))
    assert manifest["inventory_hash"] == result.inventory_hash
    assert service.managed_output_root(folder.folder_id) == output.resolve()
    assert service.entries_from_plan(confirmed.plan_id)[0]["managed_relative_path"] == (
        "app_state/store_procedures/p.sql"
    )


def test_folder_plan_rejects_changed_source_and_in_place_requires_approval(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    path = source / "f.sql"
    path.write_text("CREATE FUNCTION dbo.F() RETURNS int AS BEGIN RETURN 1; END;\n")
    service = _service(tmp_path)
    folder = service.register(
        input_root=source, output_root=source, engine=DatabaseEngine.SQLSERVER
    )
    plan = service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id, in_place=True))
    with pytest.raises(ApprovalRequired):
        service.apply(plan.plan_id)
    challenge = service.approvals.list_challenges()[0] if service.approvals else {}
    assert service.approvals is not None
    service.approvals.grant(str(challenge["challenge_id"]), interactive=True)
    path.write_text("CREATE FUNCTION dbo.F() RETURNS int AS BEGIN RETURN 2; END;\n")
    with pytest.raises(SqlCtxError, match="source file changed"):
        service.apply(plan.plan_id)


def test_folder_scan_rejects_multiple_objects(tmp_path: Path) -> None:
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    (source / "bad.sql").write_text(
        "CREATE PROCEDURE dbo.A AS SELECT 1;\nCREATE PROCEDURE dbo.B AS SELECT 2;\n"
    )
    service = _service(tmp_path)
    folder = service.register(
        input_root=source, output_root=output, engine=DatabaseEngine.SQLSERVER
    )
    with pytest.raises(SqlCtxError, match="exactly one object"):
        service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id))


def test_folder_plan_confirms_only_one_deterministic_rule_match(tmp_path: Path) -> None:
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    (source / "CONTENT_ITEM.sql").write_text("CREATE PROCEDURE dbo.CONTENT_ITEM AS SELECT 1;\n")
    state = JsonRuntimeStateStore(tmp_path / "state")
    service = ManagedFolderService(
        state,
        ApprovalService(state=state),
        category_rules=CategoryConfig(
            version=1,
            categories=[
                CategoryRule(
                    name="content",
                    description="Confirmed content workflow.",
                    prefixes=["CONTENT_"],
                )
            ],
        ),
    )
    folder = service.register(
        input_root=source, output_root=output, engine=DatabaseEngine.SQLSERVER
    )

    plan = service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id))

    assert plan.items[0].destination_relative_path == ("content/store_procedures/CONTENT_ITEM.sql")
    assert plan.items[0].classification_status == "confirmed"
    result = service.apply(plan.plan_id)
    header, _ = parse_managed_sql(
        (output / plan.items[0].destination_relative_path).read_text(encoding="utf-8")
    )
    assert result.written == 1
    assert header.classification_source == "rule"
    assert header.description == "Confirmed content workflow."
    assert header.tags == []
    assert header.evidence == ["rule:configured_prefix:content"]


def test_in_place_plan_rejects_destination_that_is_another_source(tmp_path: Path) -> None:
    source = tmp_path / "input"
    occupied = source / "unknowns" / "store_procedures"
    occupied.mkdir(parents=True)
    (source / "P.sql").write_text("CREATE PROCEDURE dbo.P AS SELECT 1;\n")
    (occupied / "P.sql").write_text("CREATE FUNCTION dbo.Q() RETURNS int AS BEGIN RETURN 2; END;\n")
    service = _service(tmp_path)
    folder = service.register(
        input_root=source, output_root=source, engine=DatabaseEngine.SQLSERVER
    )

    with pytest.raises(SqlCtxError, match="overwrite another source"):
        service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id, in_place=True))


def test_identity_detection_ignores_declarations_in_comments_and_literals() -> None:
    sql = """
    /* CREATE FUNCTION dbo.FalsePositive() RETURNS int AS BEGIN RETURN 0; END; */
    CREATE PROCEDURE dbo.RealProcedure
    AS
    BEGIN
        SELECT 'ALTER PROCEDURE dbo.AlsoFalse AS SELECT 1';
        -- CREATE PROCEDURE dbo.CommentedOut AS SELECT 2;
    END;
    """

    object_type, schema_name, object_name = detect_sql_identity(sql)

    assert object_type.value == "procedure"
    assert schema_name == "dbo"
    assert object_name == "RealProcedure"


def test_owner_resolution_rewrites_one_applied_unknown_managed_file(tmp_path: Path) -> None:
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    original = "CREATE PROCEDURE [dbo].[P] AS SELECT 1;\n"
    (source / "P.sql").write_text(original, encoding="utf-8")
    service = _service(tmp_path)
    folder = service.register(
        input_root=source, output_root=output, engine=DatabaseEngine.SQLSERVER
    )
    initial = service.plan(FolderClassificationPlanRequest(folder_id=folder.folder_id))
    service.apply(initial.plan_id)
    unknown_path = output / "unknowns/store_procedures/P.sql"
    _, normalized_body = parse_managed_sql(unknown_path.read_text(encoding="utf-8"))

    resolution = service.plan_resolution(
        ManagedFileResolutionPlanRequest(
            folder_id=folder.folder_id,
            managed_relative_path="unknowns/store_procedures/P.sql",
            context="app_state",
            description="สถานะแอป",
            tags=["app_state", "share"],
        )
    )

    assert resolution.in_place is True
    assert resolution.items[0].destination_relative_path == ("app_state/store_procedures/P.sql")
    with pytest.raises(ApprovalRequired):
        service.apply(resolution.plan_id)
    assert service.approvals is not None
    challenge = service.approvals.list_challenges()[0]
    service.approvals.grant(str(challenge["challenge_id"]), interactive=True)
    applied = service.apply(resolution.plan_id)

    confirmed_path = output / "app_state/store_procedures/P.sql"
    header, body = parse_managed_sql(confirmed_path.read_text(encoding="utf-8"))
    assert applied.moved == 1
    assert not unknown_path.exists()
    assert header.context == "app_state"
    assert header.description == "สถานะแอป"
    assert header.tags == ["app_state", "share"]
    assert header.classification_status == "confirmed"
    assert header.classification_source == "owner"
    assert body == normalized_body
    assert service.entries_from_plan(resolution.plan_id)[0]["managed_relative_path"] == (
        "app_state/store_procedures/P.sql"
    )

    confirmed_path.write_text(
        confirmed_path.read_text(encoding="utf-8") + "-- drift\n", encoding="utf-8"
    )
    with pytest.raises(SqlCtxError, match="changed before index sync"):
        service.entries_from_plan(resolution.plan_id)
