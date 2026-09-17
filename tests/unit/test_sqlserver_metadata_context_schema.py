from __future__ import annotations

from typing import Any

import pytest

from sqlctx.adapters.sqlserver.adapter import (
    _METADATA_CONTEXT_CHECKS,
    _METADATA_CONTEXT_COLUMNS,
    _METADATA_CONTEXT_DEFAULTS,
    _METADATA_CONTEXT_INDEXES,
    SqlServerAdapter,
)
from sqlctx.context_index.contracts import ContextIndexEntry
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ResolvedConnectionProfile


def _profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.SQLSERVER,
        host="host",
        port=1433,
        database="db",
        username="user",
        password="secret",
        allowed_schemas=("dbo",),
        allowed_object_types=(ObjectType.TABLE, ObjectType.FUNCTION),
        metadata_context_write=True,
    )


def _rows() -> tuple[list[dict[str, Any]], ...]:
    columns = [
        {
            "column_name": name,
            "data_type": signature[0],
            "max_length": signature[1],
            "precision": signature[2],
            "scale": signature[3],
            "is_nullable": signature[4],
            "is_identity": signature[5],
            "is_computed": False,
            "is_rowguidcol": False,
            "is_sparse": False,
            "seed_value": 1 if name == "ID" else None,
            "increment_value": 1 if name == "ID" else None,
        }
        for name, signature in _METADATA_CONTEXT_COLUMNS.items()
    ]
    defaults = [
        {"constraint_name": name, "column_name": column, "definition": definition}
        for name, (column, definition) in _METADATA_CONTEXT_DEFAULTS.items()
    ]
    checks = [
        {
            "constraint_name": name,
            "definition": definition,
            "is_disabled": False,
            "is_not_trusted": False,
            "is_not_for_replication": False,
        }
        for name, definition in _METADATA_CONTEXT_CHECKS.items()
    ]
    indexes = [
        {
            "index_name": name,
            "is_unique": signature[0],
            "is_primary_key": signature[1],
            "is_unique_constraint": signature[2],
            "type_desc": signature[3],
            "is_disabled": False,
            "filter_definition": signature[4],
            "column_name": column,
            "key_ordinal": ordinal,
            "is_included_column": included,
            "is_descending_key": descending,
        }
        for name, signature in _METADATA_CONTEXT_INDEXES.items()
        for column, ordinal, included, descending in signature[5]
    ]
    return columns, defaults, checks, indexes, []


def test_metadata_context_schema_verification_accepts_full_signature() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    batches = iter(_rows())
    adapter._execute = lambda *_args, **_kwargs: next(batches)  # type: ignore[method-assign]

    adapter.verify_metadata_context_schema(_profile())


def test_metadata_context_schema_verification_rejects_wrong_type_and_version_check() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    columns, defaults, checks, indexes, auxiliary = _rows()
    next(item for item in columns if item["column_name"] == "ID")["data_type"] = "int"
    next(
        item
        for item in checks
        if item["constraint_name"] == "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION"
    )["definition"] = "([OUTPUT_FORMAT_VERSION]=(99))"
    next(item for item in indexes if item["index_name"] == "IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE")[
        "is_disabled"
    ] = True
    next(item for item in indexes if item["index_name"] == "PK_DB_METADATA_CONTEXT")[
        "is_descending_key"
    ] = True
    defaults.append(
        {
            "constraint_name": "DF_DB_METADATA_CONTEXT_UNEXPECTED",
            "column_name": "DESCRIPTION",
            "definition": "N'blocked'",
        }
    )
    checks.append(
        {
            "constraint_name": "CK_DB_METADATA_CONTEXT_UNEXPECTED",
            "definition": "OBJECT_TYPE = 'TABLE'",
            "is_disabled": False,
            "is_not_trusted": False,
            "is_not_for_replication": False,
        }
    )
    indexes.append(
        {
            "index_name": "UX_DB_METADATA_CONTEXT_UNEXPECTED",
            "is_unique": True,
            "is_primary_key": False,
            "is_unique_constraint": False,
            "type_desc": "NONCLUSTERED",
            "is_disabled": False,
            "filter_definition": None,
            "column_name": "SCHEMA_NAME",
            "key_ordinal": 1,
            "is_included_column": False,
            "is_descending_key": True,
        }
    )
    auxiliary.append({"object_kind": "TRIGGER", "object_name": "TR_METADATA_CONTEXT"})
    batches = iter((columns, defaults, checks, indexes, auxiliary))
    adapter._execute = lambda *_args, **_kwargs: next(batches)  # type: ignore[method-assign]

    with pytest.raises(SqlCtxError) as caught:
        adapter.verify_metadata_context_schema(_profile())

    assert caught.value.code == "METADATA_CONTEXT_SCHEMA_DRIFT"
    assert "ID" in caught.value.details["column_mismatches"]
    assert "CK_DB_METADATA_CONTEXT_OUTPUT_VERSION" in caught.value.details["check_mismatches"]
    assert "IX_DB_METADATA_CONTEXT_LOOKUP_ACTIVE" in caught.value.details["index_mismatches"]
    assert "PK_DB_METADATA_CONTEXT" in caught.value.details["index_mismatches"]
    assert "DF_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["default_mismatches"]
    assert "CK_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["check_mismatches"]
    assert "UX_DB_METADATA_CONTEXT_UNEXPECTED" in caught.value.details["index_mismatches"]
    assert "TRIGGER:TR_METADATA_CONTEXT" in caught.value.details["unexpected_objects"]


def test_metadata_upsert_reports_semantic_unchanged() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    adapter._execute_write_returning = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        {"action": "UPDATE", "owner_values_preserved": 1, "semantic_changed": 0}
    ]
    entry = ContextIndexEntry(
        schema_name="dbo",
        object_name="APP_STATE",
        object_type=ObjectType.TABLE,
        context="app_state",
        tags=["app_state"],
        classification_status="confirmed",
        classification_source="owner",
    )

    action, preserved = adapter.upsert_metadata_context(_profile(), entry, actor_id=42)

    assert action == "unchanged"
    assert preserved is True


def test_complete_scope_deactivation_returns_only_safe_identities() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    captured: dict[str, Any] = {}

    def execute(_profile: object, query: str, parameters: object) -> list[dict[str, str]]:
        captured["query"] = query
        captured["parameters"] = parameters
        return [
            {
                "schema_name": "dbo",
                "object_type": "FUNCTION",
                "object_name": "REMOVED_FUNCTION",
            }
        ]

    adapter._execute_write_returning = execute  # type: ignore[method-assign]

    deactivated = adapter.deactivate_missing_metadata_context(
        _profile(),
        schemas=("dbo",),
        object_types=(ObjectType.TABLE, ObjectType.FUNCTION),
        present_identities=[("dbo", ObjectType.TABLE, "APP_STATE")],
        actor_id=42,
    )

    assert deactivated == [("dbo", "FUNCTION", "REMOVED_FUNCTION")]
    assert "OPENJSON(?)" in captured["query"]
    assert "APP_STATE" in str(captured["parameters"])
