from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sqlctx.application.catalog import CatalogService, CompleteCatalogContextScope
from sqlctx.context_index.contracts import (
    ContextGenerationRequest,
    ContextIndexEntry,
    ContextIndexPage,
    ContextIndexSyncRequest,
    ContextIndexSyncResult,
)
from sqlctx.context_index.generation import build_generation_plan
from sqlctx.context_index.service import CompleteContextIndexScope, ContextIndexService
from sqlctx.core.enums import (
    DatabaseEngine,
    JobStatus,
    MaterializationMode,
    ObjectType,
)
from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.core.models import PageInfo, ResolvedConnectionProfile
from sqlctx.exporting.header import ManagedSqlHeader, render_managed_sql
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.server.facade import ServiceFacade


class FakeAdapter:
    def __init__(self) -> None:
        self.verified = False
        self.entries: list[ContextIndexEntry] = []
        self.actions: list[tuple[str, bool]] = []
        self.deactivated: list[tuple[str, str, str]] = []

    def verify_metadata_context_schema(self, profile: ResolvedConnectionProfile) -> None:
        self.verified = True

    def upsert_metadata_context(
        self, profile: ResolvedConnectionProfile, entry: ContextIndexEntry, *, actor_id: int
    ) -> tuple[str, bool]:
        assert actor_id == 42
        self.entries.append(entry)
        return self.actions.pop(0) if self.actions else ("insert", False)

    def deactivate_missing_metadata_context(
        self,
        profile: ResolvedConnectionProfile,
        *,
        schemas: tuple[str, ...],
        object_types: tuple[ObjectType, ...],
        present_identities: list[tuple[str, ObjectType, str]],
        actor_id: int,
    ) -> list[tuple[str, str, str]]:
        assert schemas == ("dbo",)
        assert object_types == (
            ObjectType.TABLE,
            ObjectType.PROCEDURE,
            ObjectType.FUNCTION,
        )
        assert actor_id == 42
        assert len(present_identities) == len(self.entries)
        return list(self.deactivated)


def _profile(
    *, write: bool, excluded_object_patterns: tuple[str, ...] = ()
) -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.SQLSERVER,
        host="host",
        port=1433,
        database="db",
        username="user",
        password="secret",
        allowed_schemas=("dbo",),
        allowed_object_types=(ObjectType.TABLE, ObjectType.PROCEDURE, ObjectType.FUNCTION),
        excluded_object_patterns=excluded_object_patterns,
        metadata_context_write=write,
    )


def test_context_sync_requires_bound_approval_and_numeric_actor(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    service = ContextIndexService(approvals)
    adapter = FakeAdapter()
    request = ContextIndexSyncRequest(
        profile="demo",
        actor_id=42,
        idempotency_key="sync-0001",
        mode="owner",
        entries=[
            ContextIndexEntry(
                schema_name="dbo",
                object_name="APP_STATE",
                object_type=ObjectType.TABLE,
                context="app_state",
                description="Application state",
                tags=["app_state", "share"],
                classification_status="confirmed",
                classification_source="owner",
            )
        ],
    )
    with pytest.raises(ApprovalRequired):
        service.sync(request, profile=_profile(write=True), adapter=adapter, caller="agent:x")
    challenge = approvals.list_challenges()[0]
    approvals.grant(str(challenge["challenge_id"]), interactive=True)
    result = service.sync(request, profile=_profile(write=True), adapter=adapter, caller="agent:x")
    assert result.inserted == 1
    assert adapter.verified
    assert adapter.entries[0].tags == ["app_state", "share"]
    events = list((state.root / "audit/events").rglob("*.json"))
    assert len(events) == 1
    event = json.loads(events[0].read_text(encoding="utf-8"))
    assert event["operation"] == "metadata_context.sync_item"
    assert event["outcome"] == "succeeded"
    assert event["object_identity_hash"].startswith("sha256:")
    assert "table:dbo.APP_STATE" not in json.dumps(event)


def test_complete_sync_reports_exact_actions_and_deactivates_missing(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    service = ContextIndexService(approvals)
    adapter = FakeAdapter()
    adapter.actions = [("insert", False), ("update", True), ("unchanged", False)]
    adapter.deactivated = [("dbo", "FUNCTION", "REMOVED_FUNCTION")]
    entries = [
        ContextIndexEntry(
            schema_name="dbo",
            object_name=name,
            object_type=object_type,
            context="app_state",
            tags=["app_state"],
            classification_status="confirmed",
            classification_source="owner",
        )
        for name, object_type in (
            ("A", ObjectType.TABLE),
            ("B", ObjectType.PROCEDURE),
            ("C", ObjectType.FUNCTION),
        )
    ]
    request = ContextIndexSyncRequest(
        profile="demo",
        actor_id=42,
        idempotency_key="sync-complete-0001",
        entries=entries,
        complete_catalog_id="cat_complete",
        mode="owner",
    )
    scope = CompleteContextIndexScope(
        schemas=("dbo",),
        object_types=(ObjectType.TABLE, ObjectType.PROCEDURE, ObjectType.FUNCTION),
    )
    with pytest.raises(ApprovalRequired):
        service.sync(
            request,
            entries=entries,
            complete_scope=scope,
            profile=_profile(write=True),
            adapter=adapter,
            caller="agent:x",
        )
    challenge = approvals.list_challenges()[0]
    approvals.grant(str(challenge["challenge_id"]), interactive=True)

    result = service.sync(
        request,
        entries=entries,
        complete_scope=scope,
        profile=_profile(write=True),
        adapter=adapter,
        caller="agent:x",
    )

    assert result.inserted == 1
    assert result.updated == 1
    assert result.unchanged == 1
    assert result.deactivated == 1
    assert result.owner_values_preserved == 1


def test_unresolved_context_cannot_contain_guessed_tags() -> None:
    with pytest.raises(ValueError, match="unresolved records"):
        ContextIndexEntry(
            schema_name="dbo",
            object_name="UNKNOWN_OBJECT",
            object_type=ObjectType.FUNCTION,
            context=None,
            tags=["guess"],
            classification_status="unresolved",
            classification_source="unknown",
        )


def test_empty_sync_requires_complete_catalog_proof() -> None:
    with pytest.raises(ValueError, match="complete catalog"):
        ContextIndexSyncRequest(
            profile="demo",
            entries=[],
            actor_id=42,
            idempotency_key="sync-empty-0001",
        )

    request = ContextIndexSyncRequest(
        profile="demo",
        entries=[],
        actor_id=42,
        idempotency_key="sync-empty-0002",
        complete_catalog_id="cat_empty",
        mode="owner",
    )

    assert request.entries == []


def test_generation_plan_requires_index_header_and_body_to_agree(tmp_path: Path) -> None:
    body = "CREATE TABLE [dbo].[APP_STATE] ([ID] int NOT NULL);\n"
    content_hash = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    relative_path = "app_state/tables/APP_STATE.sql"
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(
        render_managed_sql(
            ManagedSqlHeader(
                object_id="table:dbo.APP_STATE",
                engine=DatabaseEngine.SQLSERVER,
                schema_name="dbo",
                object_name="APP_STATE",
                object_type=ObjectType.TABLE,
                context="app_state",
                tags=["app_state", "share"],
                classification_status="confirmed",
                classification_source="owner",
                content_hash=content_hash,
                output_format_version="2",
            ),
            body,
        ),
        encoding="utf-8",
    )
    entry = ContextIndexEntry(
        schema_name="dbo",
        object_name="APP_STATE",
        object_type=ObjectType.TABLE,
        context="app_state",
        tags=["app_state", "share"],
        classification_status="confirmed",
        classification_source="owner",
        content_hash=content_hash,
        managed_relative_path=relative_path,
    )
    request = ContextGenerationRequest(profile="demo", folder_id="fld_demo")

    plan = build_generation_plan(
        request,
        ContextIndexPage(items=[entry], page=PageInfo(limit=100, returned=1)),
        managed_root=tmp_path,
    )

    assert [item.object_id for item in plan.items] == ["table:dbo.APP_STATE"]
    drifted = entry.model_copy(update={"tags": ["app_state"]})
    with pytest.raises(SqlCtxError, match="do not agree"):
        build_generation_plan(
            request,
            ContextIndexPage(items=[drifted], page=PageInfo(limit=100, returned=1)),
            managed_root=tmp_path,
        )
    drifted_description = entry.model_copy(update={"description": "Changed elsewhere"})
    with pytest.raises(SqlCtxError, match="do not agree"):
        build_generation_plan(
            request,
            ContextIndexPage(
                items=[drifted_description],
                page=PageInfo(limit=100, returned=1),
            ),
            managed_root=tmp_path,
        )


def test_facade_accepts_complete_sync_only_for_exact_catalog_inventory() -> None:
    profile = _profile(write=True)

    class Profiles:
        def resolve(self, name: str) -> ResolvedConnectionProfile:
            assert name == "demo"
            return profile

    class Catalogs:
        def complete_context_index_scope(
            self, catalog_id: str, resolved: ResolvedConnectionProfile
        ) -> CompleteCatalogContextScope:
            assert catalog_id == "cat_complete"
            assert resolved is profile
            return CompleteCatalogContextScope(
                schemas=("dbo",),
                object_types=(ObjectType.TABLE,),
                object_ids=frozenset({"table:dbo.APP_STATE"}),
            )

    class Index:
        scope: CompleteContextIndexScope | None = None

        def sync(self, request: ContextIndexSyncRequest, **kwargs: Any) -> ContextIndexSyncResult:
            self.scope = kwargs["complete_scope"]
            return ContextIndexSyncResult(
                inserted=0,
                updated=0,
                unchanged=1,
                deactivated=0,
                owner_values_preserved=0,
            )

    class Idempotency:
        def execute(self, **kwargs: Any) -> tuple[Any, bool]:
            create = kwargs["create"]
            return create(), False

    entry = ContextIndexEntry(
        schema_name="dbo",
        object_name="APP_STATE",
        object_type=ObjectType.TABLE,
        context="app_state",
        tags=["app_state"],
        classification_status="confirmed",
        classification_source="owner",
    )
    request = ContextIndexSyncRequest(
        profile="demo",
        entries=[entry],
        actor_id=42,
        idempotency_key="sync-facade-0001",
        complete_catalog_id="cat_complete",
        mode="owner",
    )
    facade = object.__new__(ServiceFacade)
    facade.profiles = Profiles()  # type: ignore[assignment]
    facade.catalogs = Catalogs()  # type: ignore[assignment]
    facade.context_index = Index()  # type: ignore[assignment]
    facade.idempotency = Idempotency()  # type: ignore[assignment]

    result = facade.sync_context_index(request, caller="agent:x")

    assert result.unchanged == 1
    assert facade.context_index.scope == CompleteContextIndexScope(  # type: ignore[attr-defined]
        schemas=("dbo",), object_types=(ObjectType.TABLE,)
    )

    mismatch = request.model_copy(
        update={"entries": [entry.model_copy(update={"object_name": "OTHER"})]}
    )
    with pytest.raises(SqlCtxError) as caught:
        facade.sync_context_index(mismatch, caller="agent:x")
    assert caught.value.code == "METADATA_CONTEXT_COMPLETE_SCOPE_INVENTORY_MISMATCH"


def test_catalog_complete_scope_requires_exact_unexcluded_all_mode() -> None:
    service = object.__new__(CatalogService)
    object_types = [ObjectType.TABLE, ObjectType.PROCEDURE, ObjectType.FUNCTION]
    record = SimpleNamespace(
        request=SimpleNamespace(
            profile="demo",
            schemas=["dbo"],
            object_types=object_types,
            include_patterns=[],
            exclude_patterns=[],
        ),
        selection=SimpleNamespace(mode=MaterializationMode.ALL),
    )
    snapshot = SimpleNamespace(objects=[])
    status = SimpleNamespace(
        status=JobStatus.READY,
        analysis_failed_object_count=0,
        discovered_object_count=0,
        fully_analyzed_object_count=0,
    )
    service._record = lambda _catalog_id: record  # type: ignore[method-assign]
    service._snapshot = lambda _catalog_id: snapshot  # type: ignore[method-assign]
    service.status = lambda _catalog_id: status  # type: ignore[method-assign]

    proof = service.complete_context_index_scope("cat_empty", _profile(write=True))

    assert proof.object_ids == frozenset()
    assert proof.schemas == ("dbo",)

    excluded_profile = _profile(write=True, excluded_object_patterns=("TEMP_*",))
    with pytest.raises(SqlCtxError) as caught:
        service.complete_context_index_scope("cat_empty", excluded_profile)
    assert caught.value.code == "METADATA_CONTEXT_COMPLETE_SCOPE_REQUIRED"


def test_inventory_sync_runs_unattended_and_records_every_discovered_object(
    tmp_path: Path,
) -> None:
    """The whole point of inventory mode: a full discovered inventory lands with no approval.

    Gating this behind a single-use approval is why the index stayed at a handful of
    hand-resolved rows instead of covering the catalog.
    """
    state = JsonRuntimeStateStore(tmp_path / "state")
    approvals = ApprovalService(state=state)
    service = ContextIndexService(approvals)
    adapter = FakeAdapter()
    adapter.actions = [("insert", False)] * 3
    entries = [
        ContextIndexEntry(
            schema_name="dbo",
            object_name=name,
            object_type=object_type,
            classification_status="unresolved",
            classification_source="unknown",
        )
        for name, object_type in (
            ("SYSTEM_CONFIG", ObjectType.TABLE),
            ("SYSTEM_CONFIG_K8S", ObjectType.TABLE),
            ("SOME_PROC", ObjectType.PROCEDURE),
        )
    ]
    request = ContextIndexSyncRequest(
        profile="demo",
        entries=entries,
        actor_id=42,
        idempotency_key="sync-inventory-0001",
    )
    assert request.mode == "inventory"

    result = service.sync(request, profile=_profile(write=True), adapter=adapter, caller="agent:x")

    assert result.inserted == 3
    assert approvals.list_challenges() == []
    assert {item.object_name for item in adapter.entries} == {
        "SYSTEM_CONFIG",
        "SYSTEM_CONFIG_K8S",
        "SOME_PROC",
    }


def test_inventory_mode_refuses_owner_writes_and_deactivation() -> None:
    owner_entry = ContextIndexEntry(
        schema_name="dbo",
        object_name="APP_STATE",
        object_type=ObjectType.TABLE,
        context="app_state",
        classification_status="confirmed",
        classification_source="owner",
    )
    with pytest.raises(ValueError, match="owner mode"):
        ContextIndexSyncRequest(
            profile="demo",
            entries=[owner_entry],
            actor_id=42,
            idempotency_key="sync-inventory-0002",
        )
    with pytest.raises(ValueError, match="owner mode"):
        ContextIndexSyncRequest(
            profile="demo",
            entries=[
                ContextIndexEntry(
                    schema_name="dbo",
                    object_name="APP_STATE",
                    object_type=ObjectType.TABLE,
                    classification_status="unresolved",
                    classification_source="unknown",
                )
            ],
            actor_id=42,
            idempotency_key="sync-inventory-0003",
            complete_catalog_id="cat_complete",
        )


def test_missing_write_scope_names_the_exact_owner_command() -> None:
    service = ContextIndexService(ApprovalService(state=None))
    request = ContextIndexSyncRequest(
        profile="demo",
        entries=[
            ContextIndexEntry(
                schema_name="dbo",
                object_name="APP_STATE",
                object_type=ObjectType.TABLE,
                classification_status="unresolved",
                classification_source="unknown",
            )
        ],
        actor_id=42,
        idempotency_key="sync-inventory-0004",
    )
    with pytest.raises(SqlCtxError) as caught:
        service.sync(
            request, profile=_profile(write=False), adapter=FakeAdapter(), caller="agent:x"
        )
    assert caught.value.code == "METADATA_CONTEXT_WRITE_SCOPE_REQUIRED"
    assert caught.value.details is not None
    assert "profile write-scope" in str(caught.value.details["owner_command"])


@pytest.mark.parametrize("engine", ["postgres", "mysql", "mariadb", "oracle"])
def test_non_sqlserver_engines_report_the_boundary_instead_of_crashing(engine: str) -> None:
    """Only SQL Server implements the metadata-context protocol.

    Before the guard the service called adapter.verify_metadata_context_schema directly and
    raised a bare AttributeError. Inventory sync now runs on every export, so that would
    have surfaced as an internal error on every export against these engines.
    """
    profile = ResolvedConnectionProfile(
        name=engine,
        engine=engine,
        host="host",
        port=1234,
        database="db",
        username="u",
        password="p",
        allowed_schemas=("public",),
        allowed_object_types=(ObjectType.TABLE,),
        metadata_context_write=True,
    )
    request = ContextIndexSyncRequest(
        profile=engine,
        actor_id=1,
        idempotency_key=f"{engine}-sync-0001",
        entries=[
            ContextIndexEntry(
                schema_name="public",
                object_name="T",
                object_type=ObjectType.TABLE,
                classification_status="unresolved",
                classification_source="unknown",
            )
        ],
    )
    with pytest.raises(SqlCtxError) as caught:
        ContextIndexService(ApprovalService(state=None)).sync(
            request, profile=profile, adapter=FakeAdapter(), caller="agent:x"
        )
    assert caught.value.code == "METADATA_CONTEXT_ENGINE_UNSUPPORTED"
    assert caught.value.details == {"engine": engine, "supported": ["sqlserver"]}
