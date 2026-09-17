"""Stored functions are discovered, extracted, and materialized beside procedures."""

from __future__ import annotations

from typing import Any

import pytest

from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.adapters.registry import ADAPTER_TYPES
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ResolvedConnectionProfile
from tests.contract.test_adapters import FakeConnection, FakeCursor


def profile(engine: DatabaseEngine) -> ResolvedConnectionProfile:
    """A profile whose owner has opted functions into the allowlist."""
    return ResolvedConnectionProfile(
        name="demo",
        engine=engine,
        host="localhost",
        port=1,
        database="demo",
        username="user",
        password="password",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.TABLE, ObjectType.PROCEDURE, ObjectType.FUNCTION),
    )


class FunctionCursor(FakeCursor):
    def execute(self, query: str, parameters: Any = ()) -> None:
        normalized = " ".join(query.lower().split())
        if " as object_type" in normalized:
            self.description = [("object_name",), ("object_type",)]
            self.rows = [("UM_USER", "table"), ("UM_AUDIT_I", "procedure"), ("FN_AGE", "function")]
            return
        if "routine_type = 'function'" in normalized:
            self.description = [("definition",)]
            self.rows = [("RETURN YEAR(CURDATE()) - YEAR(P_BORN);",)]
            return
        super().execute(query, parameters)


class FunctionConnection(FakeConnection):
    def cursor(self) -> FunctionCursor:
        return FunctionCursor(self.sample_count)


def test_every_engine_adapter_can_read_a_stored_function_definition() -> None:
    missing = [
        engine.value
        for engine, adapter in ADAPTER_TYPES.items()
        if not adapter.queries.function_definition
    ]
    assert missing == []


def test_discovery_returns_functions_as_their_own_object_type() -> None:
    adapter = MySqlAdapter(lambda _: FunctionConnection())

    discovered = adapter.discover_objects(profile(DatabaseEngine.MYSQL))

    by_type = {ref.object_name: ref.object_type for ref in discovered}
    assert by_type["FN_AGE"] == ObjectType.FUNCTION
    assert by_type["UM_AUDIT_I"] == ObjectType.PROCEDURE
    assert by_type["UM_USER"] == ObjectType.TABLE


def test_a_function_is_extracted_through_the_function_definition_query() -> None:
    adapter = MySqlAdapter(lambda _: FunctionConnection())
    resolved = profile(DatabaseEngine.MYSQL)
    ref = next(
        item
        for item in adapter.discover_objects(resolved)
        if item.object_type == ObjectType.FUNCTION
    )

    extracted = adapter.extract_object(resolved, ref)

    assert extracted.sanitized_definition == "RETURN YEAR(CURDATE()) - YEAR(P_BORN);"
    assert extracted.columns == []


def test_an_engine_without_function_support_reports_it_instead_of_guessing() -> None:
    adapter = MySqlAdapter(lambda _: FunctionConnection())
    resolved = profile(DatabaseEngine.MYSQL)
    ref = next(
        item
        for item in adapter.discover_objects(resolved)
        if item.object_type == ObjectType.FUNCTION
    )
    object.__setattr__(adapter.queries, "function_definition", None)
    try:
        with pytest.raises(SqlCtxError) as caught:
            adapter.get_function_definition(resolved, ref)
        assert caught.value.code == "DEFINITION_UNAVAILABLE"
    finally:
        object.__setattr__(
            adapter.queries,
            "function_definition",
            MySqlAdapter.queries.function_definition,
        )


def test_functions_materialize_into_their_own_folder() -> None:
    import inspect

    from sqlctx.exporting import writer

    source = inspect.getsource(writer.ExportWriter) if hasattr(writer, "ExportWriter") else ""
    body = source or writer.__file__ and open(writer.__file__, encoding="utf-8").read()
    assert 'ObjectType.FUNCTION: "functions"' in body
    assert 'ObjectType.TABLE: "tables"' in body
