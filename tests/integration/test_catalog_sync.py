from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlctx.application.catalog import CatalogRequest, CatalogService
from sqlctx.classification.classifier import ClassificationService
from sqlctx.classification.rules import CategoryRuleRepository
from sqlctx.core.enums import DatabaseEngine, MaterializationMode, ObjectType
from sqlctx.core.models import (
    ColumnMetadata,
    DatabaseCapabilities,
    DatabaseObject,
    MaterializationSelection,
    ObjectRef,
    ResolvedConnectionProfile,
    SamplePage,
)
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.masking import DeterministicMaskingEngine
from sqlctx.security.runtime import EncryptedSnapshotSecretStore, JsonRuntimeStateStore
from sqlctx.server import facade
from sqlctx.server.facade import ServiceFacade


class RefreshAdapter:
    def __init__(self) -> None:
        self.ref = ObjectRef(
            object_id="table:app.UM_USER",
            engine=DatabaseEngine.SQLSERVER,
            schema_name="app",
            object_name="UM_USER",
            object_type=ObjectType.TABLE,
        )
        self.extract_calls = 0
        self.sample_calls = 0

    def capabilities(self) -> DatabaseCapabilities:
        return DatabaseCapabilities(engine=DatabaseEngine.SQLSERVER, sqlfluff_dialect="tsql")

    def discover_objects(self, _: ResolvedConnectionProfile) -> list[ObjectRef]:
        return [self.ref]

    def extract_object(self, _: ResolvedConnectionProfile, ref: ObjectRef) -> DatabaseObject:
        self.extract_calls += 1
        return DatabaseObject(
            ref=ref,
            columns=[
                ColumnMetadata(name="username", data_type="varchar", nullable=True, ordinal=1)
            ],
            sanitized_definition="CREATE TABLE app.UM_USER (username varchar(100));",
        )

    def get_sample_rows(
        self,
        _: ResolvedConnectionProfile,
        ref: ObjectRef,
        requested: int,
        **__: Any,
    ) -> SamplePage:
        self.sample_calls += 1
        return SamplePage(
            object_id=ref.object_id,
            columns=["username"],
            rows=[[f"user-{self.sample_calls}@example.com"]],
            requested_count=requested,
            actual_count=1,
            deterministic=True,
            sampling_order=["username"],
        )

    def get_routine_dependencies(self, *_: Any) -> list[Any]:
        return []

    def cancel(self) -> bool:
        return False


class FullLutAdapter:
    def __init__(self, row_count: int) -> None:
        self.ref = ObjectRef(
            object_id="table:app.LUT_STATUS",
            engine=DatabaseEngine.SQLSERVER,
            schema_name="app",
            object_name="LUT_STATUS",
            object_type=ObjectType.TABLE,
        )
        self.row_count = row_count
        self.all_row_calls = 0

    def capabilities(self) -> DatabaseCapabilities:
        return DatabaseCapabilities(engine=DatabaseEngine.SQLSERVER, sqlfluff_dialect="tsql")

    def discover_objects(self, _: ResolvedConnectionProfile) -> list[ObjectRef]:
        return [self.ref]

    def object_fingerprints(self, *_: Any) -> dict[str, str]:
        return {self.ref.object_id: "sha256:unchanged-definition"}

    def schema_fingerprint(self, *_: Any) -> str:
        raise AssertionError("object fingerprints should produce the schema fingerprint")

    def extract_object(self, _: ResolvedConnectionProfile, ref: ObjectRef) -> DatabaseObject:
        return DatabaseObject(
            ref=ref,
            columns=[
                ColumnMetadata(name="status_id", data_type="int", nullable=False, ordinal=1),
                ColumnMetadata(name="label", data_type="varchar", nullable=False, ordinal=2),
            ],
            sanitized_definition=(
                "CREATE TABLE app.LUT_STATUS (status_id int, label varchar(100));"
            ),
        )

    def get_all_rows(
        self,
        _: ResolvedConnectionProfile,
        ref: ObjectRef,
        **__: Any,
    ) -> SamplePage:
        self.all_row_calls += 1
        rows = [[index, f"status-{index}"] for index in range(1, self.row_count + 1)]
        return SamplePage(
            object_id=ref.object_id,
            columns=["status_id", "label"],
            rows=rows,
            requested_count=self.row_count,
            actual_count=self.row_count,
            deterministic=True,
            sampling_order=["status_id"],
            all_rows=True,
            complete=True,
            page_count=1,
        )

    def get_sample_rows(self, *_: Any, **__: Any) -> SamplePage:
        raise AssertionError("LUT extraction must request all rows")

    def get_routine_dependencies(self, *_: Any) -> list[Any]:
        return []

    def cancel(self) -> bool:
        return False


class SyncProfiles:
    def resolve(self, name: str) -> ResolvedConnectionProfile:
        assert name == "demo"
        return _profile()


def _profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.SQLSERVER,
        host="localhost",
        port=1433,
        database="demo",
        username="reader",
        password="secret",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.TABLE,),
    )


def _service(tmp_path: Path) -> tuple[CatalogService, JsonRuntimeStateStore]:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    masker = DeterministicMaskingEngine(EncryptedSnapshotSecretStore(state))
    return CatalogService(state, masker), state


def test_force_refresh_reuses_definition_but_fetches_new_table_data(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    adapter = RefreshAdapter()
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=[ObjectType.TABLE],
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
    )
    validators = {adapter.ref.object_id: "sha256:unchanged"}

    first = service.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_same_context",
        source_schema_fingerprint="sha256:schema",
        source_object_fingerprints=validators,
    )
    service.select(first.catalog_id, request.selection)

    refreshed = service.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_same_context",
        source_schema_fingerprint="sha256:schema",
        source_object_fingerprints=validators,
        force_refresh=True,
    )
    refreshed_status = service.select(refreshed.catalog_id, request.selection)
    cached = service.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_same_context",
        source_schema_fingerprint="sha256:schema",
        source_object_fingerprints=validators,
    )

    assert refreshed.catalog_id != first.catalog_id
    assert adapter.extract_calls == 1
    assert adapter.sample_calls == 2
    assert refreshed_status.reused_object_count == 1
    assert cached.catalog_id == refreshed.catalog_id
    assert cached.cache_hit is True


def test_sync_candidates_choose_newest_eligible_and_isolate_corrupt_records(
    tmp_path: Path,
) -> None:
    service, state = _service(tmp_path)
    adapter = RefreshAdapter()
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=[ObjectType.TABLE],
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
    )
    validators = {adapter.ref.object_id: "sha256:unchanged"}
    first = service.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_same_context",
        source_schema_fingerprint="sha256:schema-one",
        source_object_fingerprints=validators,
    )
    service.select(first.catalog_id, request.selection)
    second = service.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_same_context",
        source_schema_fingerprint="sha256:schema-two",
        source_object_fingerprints=validators,
        force_refresh=True,
    )
    service.select(second.catalog_id, request.selection)
    corrupt = state._safe("catalogs/cat_corrupt/record.json")
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not-json", encoding="utf-8")

    candidates, skipped = service.sync_candidates(profile_names={"demo"})

    assert [item.catalog_id for item in candidates] == [second.catalog_id]
    assert skipped == {"runtime_state_corrupt": 1, "superseded": 1}


def test_sync_data_replaces_complete_lut_cache_when_rows_grow_from_10_to_15(
    tmp_path: Path, monkeypatch: Any
) -> None:
    catalogs, state = _service(tmp_path)
    adapter = FullLutAdapter(row_count=10)
    selection = MaterializationSelection(mode=MaterializationMode.ALL)
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=[ObjectType.TABLE],
        selection=selection,
    )
    rules = Path("src/sqlctx/data/categories.yaml")
    overrides = tmp_path / "category-overrides.yaml"
    overrides.write_text("version: 1\noverrides: {}\n", encoding="utf-8")
    classifications = ClassificationService(
        state,
        catalogs,
        CategoryRuleRepository(rules, overrides),
        ApprovalService(state=state),
    )
    first = catalogs.create(
        request,
        _profile(),
        adapter,  # type: ignore[arg-type]
        session_cache_key="sess_lut_context",
        source_schema_fingerprint="sha256:schema",
        source_object_fingerprints=adapter.object_fingerprints(),
    )
    catalogs.select(first.catalog_id, selection)
    classifications.classify(first.catalog_id, selection)
    first_page = catalogs.get_snapshot(first.catalog_id).samples[adapter.ref.object_id]
    assert first_page.actual_count == 10
    assert len(first_page.rows) == 10

    service = ServiceFacade.__new__(ServiceFacade)
    service.state = state
    service.profiles = SyncProfiles()  # type: ignore[assignment]
    service.catalogs = catalogs
    service.classifications = classifications
    adapter.row_count = 15
    monkeypatch.setattr(facade, "create_adapter", lambda _: adapter)

    result = service.sync_data(profile_names=["demo"])

    assert result.synced_context_count == 1
    refreshed_id = result.contexts[0].catalog_id
    assert refreshed_id is not None
    refreshed_page = catalogs.get_snapshot(refreshed_id).samples[adapter.ref.object_id]
    assert refreshed_page.requested_count == 15
    assert refreshed_page.actual_count == 15
    assert len(refreshed_page.rows) == 15
    assert refreshed_page.all_rows is True
    assert refreshed_page.complete is True
    assert refreshed_page.rows != first_page.rows
