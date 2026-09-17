from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.application.catalog import CatalogRequest, CatalogService
from sqlctx.core.enums import (
    ClassificationPass,
    ClassificationStatus,
    DatabaseEngine,
    MaterializationMode,
    ObjectType,
)
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    ClassificationPassResult,
    MaterializationSelection,
    ResolvedConnectionProfile,
)
from sqlctx.security.masking import DeterministicMaskingEngine
from sqlctx.security.runtime import EncryptedSnapshotSecretStore, JsonRuntimeStateStore
from tests.contract.test_adapters import FakeConnection


class MultiObjectCursor(FakeConnection):
    pass


class CatalogCursor:
    def __init__(self) -> None:
        self.description: list[tuple[str]] = []
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, parameters: Any = ()) -> None:
        normalized = " ".join(query.lower().split())
        if normalized.startswith("set "):
            self.description, self.rows = [], []
        elif " as object_type" in normalized:
            self.description = [("object_name",), ("object_type",)]
            self.rows = [("UM_USER", "table"), ("CONTENT_ITEM", "table")]
        elif "constraint_type" in normalized:
            self.description = [("constraint_name",), ("constraint_type",), ("column_name",)]
            self.rows = [("pk", "primary key", "id")]
        elif "ordinal_position" in normalized:
            self.description = [
                ("column_name",),
                ("data_type",),
                ("is_nullable",),
                ("ordinal_position",),
            ]
            self.rows = [("id", "integer", False, 1), ("username", "varchar", True, 2)]
        elif "target_schema" in normalized or "target_object_id" in normalized:
            self.description, self.rows = [("target_object_id",), ("edge_type",)], []
        elif normalized.startswith("select") and "order by" in normalized:
            self.description = [("id",), ("username",)]
            self.rows = [(index, f"real-user-{index}") for index in range(10)]
        else:
            self.description, self.rows = [("version",)], [("test",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class CatalogConnection:
    def cursor(self) -> CatalogCursor:
        return CatalogCursor()

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class UnresolvedCatalogCursor(CatalogCursor):
    def execute(self, query: str, parameters: Any = ()) -> None:
        super().execute(query, parameters)
        if " as object_type" in " ".join(query.lower().split()):
            self.rows.append(("ORPHAN", "table"))


class UnresolvedCatalogConnection(CatalogConnection):
    def cursor(self) -> UnresolvedCatalogCursor:
        return UnresolvedCatalogCursor()


def resolved_profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.MYSQL,
        host="localhost",
        port=3306,
        database="demo",
        username="user",
        password="password",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.TABLE,),
    )


def make_service(tmp_path: Path) -> tuple[CatalogService, JsonRuntimeStateStore]:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    masker = DeterministicMaskingEngine(EncryptedSnapshotSecretStore(state))
    return CatalogService(state, masker), state


def test_selection_never_restricts_analysis_and_pages(tmp_path: Path) -> None:
    service, state = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    request = CatalogRequest(profile="demo", schemas=["app"], object_types=["table"])
    accepted = service.create(request, resolved_profile(), adapter)
    assert accepted.status == "awaiting_selection"
    first = service.category_preview(accepted.catalog_id, limit=1)
    assert first.page.next_cursor is not None
    second = service.category_preview(accepted.catalog_id, cursor=first.page.next_cursor, limit=1)
    assert second.page.next_cursor is None

    status = service.select(
        accepted.catalog_id,
        MaterializationSelection(mode=MaterializationMode.SELECTED, selected_categories=["um"]),
    )
    assert status.discovered_object_count == 2
    assert status.fully_analyzed_object_count == 2
    assert status.materialized_object_count == 1
    assert status.intentionally_excluded_object_count == 1
    snapshot_text = state._safe(f"catalogs/{accepted.catalog_id}/snapshot.json").read_text()
    assert "real-user-" not in snapshot_text


def test_all_mode_rejects_include_patterns_before_discovery_or_state_write(
    tmp_path: Path,
) -> None:
    service, state = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())

    def unexpected_discovery(*_: Any) -> list[Any]:
        raise AssertionError("adapter discovery must not run")

    adapter.discover_objects = unexpected_discovery  # type: ignore[method-assign]
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=["table"],
        include_patterns=["*ETL*"],
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
    )

    with pytest.raises(SqlCtxError) as caught:
        service.create(request, resolved_profile(), adapter)

    assert caught.value.code == "ALL_MODE_INCLUDE_FILTER_CONFLICT"
    assert caught.value.details == {"include_pattern_count": 1}
    assert not state._safe("catalogs").exists()


def test_named_objects_are_selected_without_depending_on_database_name_casing(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=["table"],
        include_patterns=["um_user", "CONTENT_ITEM"],
    )

    accepted = service.create(request, resolved_profile(), adapter)

    assert accepted.total_object_count == 2


def test_unmatched_requested_object_names_stop_before_an_empty_catalog(tmp_path: Path) -> None:
    service, state = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=["table"],
        include_patterns=["UM_USER", "UM_USRE", "TYPO_TABLE"],
    )

    with pytest.raises(SqlCtxError) as caught:
        service.create(request, resolved_profile(), adapter)

    assert caught.value.code == "CATALOG_INCLUDE_PATTERNS_UNMATCHED"
    assert caught.value.status_code == 404
    assert caught.value.details == {
        "unmatched_patterns": ["TYPO_TABLE", "UM_USRE"],
        "excluded_by_policy_patterns": [],
    }
    assert not state._safe("catalogs").exists()


def test_a_name_blocked_only_by_owner_exclusion_is_reported_separately(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=["table"],
        include_patterns=["CONTENT_ITEM"],
        exclude_patterns=["CONTENT_*"],
    )

    with pytest.raises(SqlCtxError) as caught:
        service.create(request, resolved_profile(), adapter)

    assert caught.value.code == "CATALOG_INCLUDE_PATTERNS_UNMATCHED"
    assert caught.value.details["excluded_by_policy_patterns"] == ["CONTENT_ITEM"]


def test_catalog_all_mode_plan_keeps_unresolved_objects_for_export_preflight(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: UnresolvedCatalogConnection())
    selection = MaterializationSelection(mode=MaterializationMode.ALL)
    accepted = service.create(
        CatalogRequest(
            profile="demo",
            schemas=["app"],
            object_types=["table"],
            selection=selection,
        ),
        resolved_profile(),
        adapter,
    )
    service.select(accepted.catalog_id, selection)

    unresolved = next(
        item
        for item in service.materialization_plan(accepted.catalog_id).items
        if item.object_id == "table:app.ORPHAN"
    )

    assert unresolved.final_category is None
    assert unresolved.included is True
    assert unresolved.reason == "all_mode"


def test_catalog_retention_is_pinned_by_dependent_export(tmp_path: Path) -> None:
    service, state = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    accepted = service.create(
        CatalogRequest(profile="demo", schemas=["app"], object_types=["table"]),
        resolved_profile(),
        adapter,
    )
    record = service._record(accepted.catalog_id)
    record.expires_at = datetime.now(UTC) - timedelta(hours=1)
    service._write_record(record)
    service.pin_export(
        accepted.catalog_id,
        "exp_1",
        datetime.now(UTC) + timedelta(hours=1),
        active=False,
    )
    assert service.cleanup_expired() == []
    service.release_export(accepted.catalog_id, "exp_1")
    assert service.cleanup_expired() == [accepted.catalog_id]
    assert not state._safe(f"catalogs/{accepted.catalog_id}").exists()
    assert not state._safe(f"snapshots/{accepted.catalog_id}").exists()


def test_catalog_cache_is_session_scoped_and_invalidated_by_schema_fingerprint(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    request = CatalogRequest(profile="demo", schemas=["app"], object_types=["table"])

    first = service.create(
        request,
        resolved_profile(),
        adapter,
        session_cache_key="sess_one",
        source_schema_fingerprint="sha256:one",
    )
    cached = service.create(
        request,
        resolved_profile(),
        adapter,
        session_cache_key="sess_one",
        source_schema_fingerprint="sha256:one",
    )
    other_session = service.create(
        request,
        resolved_profile(),
        adapter,
        session_cache_key="sess_two",
        source_schema_fingerprint="sha256:one",
    )
    changed_source = service.create(
        request,
        resolved_profile(),
        adapter,
        session_cache_key="sess_one",
        source_schema_fingerprint="sha256:two",
    )

    assert cached.catalog_id == first.catalog_id
    assert cached.cache_hit is True
    assert other_session.catalog_id != first.catalog_id
    assert changed_source.catalog_id != first.catalog_id


def test_status_and_sitemap_use_final_pass_two_categories(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    adapter = MySqlAdapter(lambda _: CatalogConnection())
    accepted = service.create(
        CatalogRequest(profile="demo", schemas=["app"], object_types=["table"]),
        resolved_profile(),
        adapter,
    )
    service.select(
        accepted.catalog_id,
        MaterializationSelection(mode=MaterializationMode.SELECTED, selected_categories=["um"]),
    )
    snapshot = service.get_snapshot(accepted.catalog_id)
    service.save_classifications(
        accepted.catalog_id,
        [
            ClassificationPassResult(
                object_id=item.ref.object_id,
                pass_name=ClassificationPass.PASS_2,
                status=ClassificationStatus.FINAL_CONFIRMED,
                category="content",
            )
            for item in snapshot.objects
        ],
    )

    status = service.status(accepted.catalog_id)
    sitemap = service.sitemap(accepted.catalog_id, view="materialization")
    assert status.materialized_object_count == 0
    assert status.intentionally_excluded_object_count == 2
    assert {item.category for item in sitemap.items} == {"content"}
