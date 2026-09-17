from __future__ import annotations

import pytest
from pydantic import ValidationError

from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.exporting.header import (
    ManagedSqlHeader,
    parse_managed_sql,
    render_managed_sql,
)


def test_managed_sql_header_round_trips_without_changing_body() -> None:
    body = "\ufeff  CREATE OR ALTER PROCEDURE [app].[UM_USER_Q]\nAS\nSELECT 1;\n"
    header = ManagedSqlHeader(
        object_id="procedure:app.UM_USER_Q",
        engine=DatabaseEngine.SQLSERVER,
        schema_name="app",
        object_name="UM_USER_Q",
        object_type=ObjectType.PROCEDURE,
        context="um",
        description="อ่านข้อมูลผู้ใช้งาน",
        tags=["share", "um"],
        classification_status="confirmed",
        classification_source="owner",
        source_fingerprint="sha256:" + "a" * 64,
        content_hash="sha256:" + "b" * 64,
        output_format_version="2",
    )

    rendered = render_managed_sql(header, body)
    parsed, parsed_body = parse_managed_sql(rendered)

    assert parsed == header
    assert parsed.tags == ["share", "um"]
    assert parsed_body == body


def test_unresolved_header_cannot_guess_context_description_or_tags() -> None:
    with pytest.raises(ValidationError):
        ManagedSqlHeader(
            object_id="table:app.UNKNOWN_TABLE",
            engine=DatabaseEngine.SQLSERVER,
            schema_name="app",
            object_name="UNKNOWN_TABLE",
            object_type=ObjectType.TABLE,
            context="um",
            description=None,
            tags=[],
            classification_status="unresolved",
            classification_source="unknown",
            content_hash="sha256:" + "c" * 64,
            output_format_version="2",
        )


@pytest.mark.parametrize("context", ["../um", "UM CONTENT", "um/content", ""])
def test_header_rejects_unsafe_context_segments(context: str) -> None:
    with pytest.raises(ValidationError):
        ManagedSqlHeader(
            object_id="table:app.T",
            engine=DatabaseEngine.SQLSERVER,
            schema_name="app",
            object_name="T",
            object_type=ObjectType.TABLE,
            context=context,
            description=None,
            tags=[],
            classification_status="confirmed",
            classification_source="rule",
            content_hash="sha256:" + "d" * 64,
            output_format_version="2",
        )


def test_parser_rejects_missing_or_malformed_managed_header() -> None:
    with pytest.raises(ValueError, match="MANAGED_SQL_HEADER_MISSING"):
        parse_managed_sql("CREATE TABLE app.T (id int);\n")
    with pytest.raises(ValueError, match="MANAGED_SQL_HEADER_INVALID"):
        parse_managed_sql("-- sqlctx-context: {not-json}\nCREATE TABLE app.T (id int);\n")
