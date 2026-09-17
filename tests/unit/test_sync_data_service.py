from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sqlctx.application.catalog import CatalogRecord, CatalogRequest
from sqlctx.core.enums import DatabaseEngine, JobStatus, MaterializationMode, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogSnapshot,
    CatalogStatus,
    DatabaseObject,
    MaterializationSelection,
    ObjectRef,
    ResolvedConnectionProfile,
    SamplePage,
)
from sqlctx.security.runtime import JsonRuntimeStateStore, exclusive_runtime_lock
from sqlctx.server import facade
from sqlctx.server.facade import ServiceFacade


def _profile(name: str = "demo") -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name=name,
        engine=DatabaseEngine.SQLSERVER,
        host="localhost",
        port=1433,
        database="demo",
        username="reader",
        password="secret",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.TABLE,),
    )


def _record(catalog_id: str, profile: str = "demo") -> CatalogRecord:
    request = CatalogRequest(
        profile=profile,
        schemas=["app"],
        object_types=[ObjectType.TABLE],
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
    )
    now = datetime.now(UTC)
    return CatalogRecord(
        catalog_id=catalog_id,
        request=request,
        request_fingerprint=request.fingerprint(),
        session_cache_key=f"sess_{profile}_context",
        source_schema_fingerprint="sha256:old-schema",
        source_object_fingerprints={
            "table:app.T": "sha256:old-t",
            "table:app.DELETED": "sha256:deleted",
        },
        status=JobStatus.READY,
        selection=request.selection,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


def _object(name: str) -> DatabaseObject:
    return DatabaseObject(
        ref=ObjectRef(
            object_id=f"table:app.{name}",
            engine=DatabaseEngine.SQLSERVER,
            schema_name="app",
            object_name=name,
            object_type=ObjectType.TABLE,
        )
    )


class Profiles:
    def resolve(self, name: str) -> ResolvedConnectionProfile:
        if name == "broken":
            raise SqlCtxError("PROFILE_NOT_READY", "Profile is not ready.")
        return _profile(name)


class Adapter:
    def object_fingerprints(self, *_: Any) -> dict[str, str]:
        return {
            "table:app.T": "sha256:new-t",
            "table:app.ADDED": "sha256:added",
        }

    def schema_fingerprint(self, *_: Any) -> str:
        raise AssertionError("per-object SQL Server validators should be used")


class Classifications:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def classify(self, catalog_id: str, _: MaterializationSelection) -> None:
        self.calls.append(catalog_id)


class Catalogs:
    def __init__(self, records: list[CatalogRecord]) -> None:
        self.records = records
        self.captured_profiles: set[str] | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.old_snapshot = CatalogSnapshot(
            catalog_id="cat_old",
            profile_name="demo",
            request_fingerprint="sha256:req",
            status=JobStatus.READY,
            objects=[_object("T"), _object("DELETED")],
        )
        self.new_snapshot = CatalogSnapshot(
            catalog_id="cat_new",
            profile_name="demo",
            request_fingerprint="sha256:req",
            status=JobStatus.READY,
            objects=[_object("T"), _object("ADDED")],
            samples={
                "table:app.T": SamplePage(
                    object_id="table:app.T",
                    columns=["id"],
                    rows=[["masked"]],
                    requested_count=10,
                    actual_count=1,
                    deterministic=True,
                    sampling_order=["id"],
                )
            },
        )

    def sync_candidates(
        self, *, profile_names: set[str] | None
    ) -> tuple[list[CatalogRecord], dict[str, int]]:
        self.captured_profiles = profile_names
        return self.records, {"superseded": 1} if self.records else {"expired": 1}

    def get_snapshot(self, catalog_id: str) -> CatalogSnapshot:
        if catalog_id.startswith("cat_old"):
            return self.old_snapshot.model_copy(update={"catalog_id": catalog_id})
        return self.new_snapshot

    def create(self, *_: Any, **kwargs: Any) -> CatalogStatus:
        self.create_calls.append(kwargs)
        return CatalogStatus(
            catalog_id="cat_new",
            status=JobStatus.AWAITING_SELECTION,
            request_fingerprint="sha256:req",
        )

    def select(self, catalog_id: str, _: MaterializationSelection) -> CatalogStatus:
        return CatalogStatus(
            catalog_id=catalog_id,
            status=JobStatus.READY,
            request_fingerprint="sha256:req",
            reused_object_count=1,
        )


def _service(tmp_path: Path, records: list[CatalogRecord]) -> ServiceFacade:
    service = ServiceFacade.__new__(ServiceFacade)
    service.state = JsonRuntimeStateStore(tmp_path / "runtime")
    service.profiles = Profiles()  # type: ignore[assignment]
    service.catalogs = Catalogs(records)  # type: ignore[assignment]
    service.classifications = Classifications()  # type: ignore[assignment]
    return service


def test_sync_data_aggregates_safe_diff_and_forces_refresh(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = _service(tmp_path, [_record("cat_old")])
    monkeypatch.setattr(facade, "create_adapter", lambda _: Adapter())

    result = service.sync_data(profile_names=["demo", "demo"])

    assert service.catalogs.captured_profiles == {"demo"}  # type: ignore[attr-defined]
    assert service.catalogs.create_calls[0]["force_refresh"] is True  # type: ignore[attr-defined]
    assert result.synced_context_count == 1
    assert result.skipped_context_count == 1
    assert result.added_object_count == 1
    assert result.changed_object_count == 1
    assert result.deleted_object_count == 1
    assert result.reused_object_count == 1
    assert result.refreshed_object_count == 1
    assert result.definition_change_detection_complete is True
    assert "secret" not in result.model_dump_json().lower()


def test_sync_data_isolates_one_context_failure(tmp_path: Path, monkeypatch: Any) -> None:
    service = _service(tmp_path, [_record("cat_old"), _record("cat_old_broken", "broken")])
    monkeypatch.setattr(facade, "create_adapter", lambda _: Adapter())

    result = service.sync_data(profile_names=[])

    assert result.synced_context_count == 1
    assert result.failed_context_count == 1
    assert {item.error_code for item in result.contexts if item.status == "failed"} == {
        "PROFILE_NOT_READY"
    }
    assert result.definition_change_detection_complete is False


def test_sync_data_rejects_when_no_eligible_catalog_exists(tmp_path: Path) -> None:
    service = _service(tmp_path, [])

    with pytest.raises(SqlCtxError) as caught:
        service.sync_data(profile_names=[])

    assert caught.value.code == "NO_SYNCABLE_CATALOGS"


def test_exclusive_runtime_lock_rejects_concurrent_holder(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")

    with exclusive_runtime_lock(
        state,
        "sync-data",
        conflict_code="SYNC_ALREADY_RUNNING",
        conflict_message="Already running.",
    ):
        with pytest.raises(SqlCtxError) as caught:
            with exclusive_runtime_lock(
                state,
                "sync-data",
                conflict_code="SYNC_ALREADY_RUNNING",
                conflict_message="Already running.",
            ):
                pass

    assert caught.value.code == "SYNC_ALREADY_RUNNING"
