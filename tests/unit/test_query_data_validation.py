from __future__ import annotations

import pytest

from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef, ResolvedConnectionProfile
from sqlctx.query_data.validation import QueryValidator


class FakeValidationAdapter:
    dialect = "tsql"

    def __init__(self, objects: list[ObjectRef]) -> None:
        self.objects = objects

    def discover_objects(self, _: ResolvedConnectionProfile) -> list[ObjectRef]:
        return self.objects

    @staticmethod
    def quote_identifier(value: str) -> str:
        return f"[{value}]"

    @staticmethod
    def parameter_placeholder(_: int) -> str:
        return "?"


def profile(*schemas: str) -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine="sqlserver",
        host="host",
        port=1433,
        database="db",
        username="reader",
        password="secret",
        allowed_schemas=schemas,
        allowed_object_types=("table",),
    )


def table(schema: str, name: str) -> ObjectRef:
    return ObjectRef(
        object_id=f"table:{schema}.{name}",
        engine="sqlserver",
        schema_name=schema,
        object_name=name,
        object_type="table",
    )


def test_join_cte_and_literals_are_resolved_and_bound() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT_SHARE"), table("dbo", "CONTENT")])
    validated = QueryValidator().validate(
        """
        WITH selected AS (
          SELECT CONTENT_ID FROM dbo.CONTENT WHERE STATUS = 'ACTIVE'
        )
        SELECT s.CONTENT_ID, c.CONFIG_QUERY_PAYLOAD
        FROM dbo.CONTENT_SHARE AS s
        LEFT JOIN selected AS x ON x.CONTENT_ID = s.CONTENT_ID
        JOIN dbo.CONTENT AS c ON c.CONTENT_ID = s.CONTENT_ID
        WHERE s.CONTENT_ID = '2264a5365201432fa67b9bd4cedc936b' AND c.VERSION_NO = 2
        """,
        profile=profile("dbo"),
        adapter=adapter,  # type: ignore[arg-type]
    )

    assert "[dbo].[CONTENT_SHARE]" in validated.sql
    assert validated.sql.count("[dbo].[CONTENT]") == 2
    assert "'ACTIVE'" not in validated.sql
    assert "'2264a5365201432fa67b9bd4cedc936b'" not in validated.sql
    assert validated.parameters == ("ACTIVE", "2264a5365201432fa67b9bd4cedc936b", 2)
    assert {(item.schema_name, item.object_name) for item in validated.tables} == {
        ("dbo", "CONTENT_SHARE"),
        ("dbo", "CONTENT"),
    }


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("UPDATE dbo.CONTENT SET STATUS = 'X'", "QUERY_READ_ONLY_REQUIRED"),
        ("SELECT * INTO dbo.COPY FROM dbo.CONTENT", "QUERY_PROHIBITED_CONSTRUCT"),
        ("SELECT * FROM dbo.CONTENT; DELETE FROM dbo.CONTENT", "QUERY_SINGLE_STATEMENT_REQUIRED"),
        ("SELECT dbo.custom_fn(CONTENT_ID) FROM dbo.CONTENT", "QUERY_FUNCTION_NOT_ALLOWED"),
        ("SELECT * FROM otherdb.dbo.CONTENT", "QUERY_CROSS_DATABASE_NOT_ALLOWED"),
        ("SELECT * FROM dbo.CONTENT -- hidden", "QUERY_COMMENTS_NOT_ALLOWED"),
    ],
)
def test_unsafe_or_unknown_sql_fails_before_execution(sql: str, code: str) -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    with pytest.raises(SqlCtxError) as caught:
        QueryValidator().validate(sql, profile=profile("dbo"), adapter=adapter)  # type: ignore[arg-type]
    assert caught.value.code == code


def test_unqualified_table_must_resolve_uniquely() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT"), table("app", "CONTENT")])
    with pytest.raises(SqlCtxError) as caught:
        QueryValidator().validate(
            "SELECT COUNT(*) FROM CONTENT",
            profile=profile("dbo", "app"),
            adapter=adapter,  # type: ignore[arg-type]
        )
    assert caught.value.code == "QUERY_TABLE_AMBIGUOUS"
    assert caught.value.details == {"candidates": ["app.CONTENT", "dbo.CONTENT"]}


def test_structural_numeric_literals_remain_structural() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    validated = QueryValidator().validate(
        "SELECT TOP 10 CONTENT_ID FROM dbo.CONTENT WHERE VERSION_NO = 4 ORDER BY 1",
        profile=profile("dbo"),
        adapter=adapter,  # type: ignore[arg-type]
    )
    assert "TOP 10" in validated.sql.upper()
    assert "ORDER BY 1" in validated.sql.upper()
    assert validated.parameters == (4,)


def test_window_aggregate_subquery_and_set_operations_are_supported() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    validated = QueryValidator().validate(
        """
        SELECT CONTENT_ID, COUNT(*) AS TOTAL,
               ROW_NUMBER() OVER (ORDER BY CONTENT_ID) AS RN
        FROM dbo.CONTENT
        WHERE EXISTS (
          SELECT 1 FROM dbo.CONTENT AS nested WHERE nested.CONTENT_ID = CONTENT.CONTENT_ID
        )
        GROUP BY CONTENT_ID HAVING COUNT(*) > 1
        UNION ALL
        SELECT CONTENT_ID, 0, 0 FROM dbo.CONTENT WHERE STATUS IN ('DRAFT', 'ACTIVE')
        """,
        profile=profile("dbo"),
        adapter=adapter,  # type: ignore[arg-type]
    )
    assert "UNION ALL" in validated.sql.upper()
    assert validated.parameters == (1, 1, 0, 0, "DRAFT", "ACTIVE")


def test_already_canonical_table_without_literals_is_valid() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    validated = QueryValidator().validate(
        "SELECT CONTENT_ID FROM [dbo].[CONTENT]",
        profile=profile("dbo"),
        adapter=adapter,  # type: ignore[arg-type]
    )
    assert validated.sql == "SELECT CONTENT_ID FROM [dbo].[CONTENT]"
    assert validated.parameters == ()


@pytest.mark.parametrize(
    "sql",
    [
        # Row-collapsing result clauses: one cell can carry an unbounded number of rows,
        # which escapes max_rows and leaves masking to an incidental column-count match.
        "SELECT CONTENT_ID FROM dbo.CONTENT FOR XML PATH('row')",
        "SELECT CONTENT_ID FROM dbo.CONTENT FOR XML AUTO",
        "SELECT CONTENT_ID FROM dbo.CONTENT FOR XML RAW",
        "SELECT CONTENT_ID FROM dbo.CONTENT FOR JSON AUTO",
        "SELECT CONTENT_ID FROM dbo.CONTENT FOR JSON PATH",
        # Hints need only SELECT permission but change locking, isolation or recursion
        # bounds, so they sit outside what assert_query_read_only can prove.
        "SELECT CONTENT_ID FROM dbo.CONTENT WITH (TABLOCKX)",
        "SELECT CONTENT_ID FROM dbo.CONTENT WITH (UPDLOCK, HOLDLOCK)",
        "SELECT CONTENT_ID FROM dbo.CONTENT WITH (NOLOCK)",
        "SELECT CONTENT_ID FROM dbo.CONTENT WITH (INDEX(0))",
        "SELECT CONTENT_ID FROM dbo.CONTENT OPTION (MAXRECURSION 0)",
    ],
)
def test_result_shaping_and_hints_are_rejected(sql: str) -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    with pytest.raises(SqlCtxError) as caught:
        QueryValidator().validate(sql, profile=profile("dbo"), adapter=adapter)  # type: ignore[arg-type]
    assert caught.value.code == "QUERY_PROHIBITED_CONSTRUCT"


@pytest.mark.parametrize(
    "sql",
    [
        # Parenthesis-free calls parse as bare_function and used to bypass the allowlist.
        "SELECT CURRENT_USER FROM dbo.CONTENT",
        "SELECT SYSTEM_USER FROM dbo.CONTENT",
        "SELECT SESSION_USER FROM dbo.CONTENT",
        "SELECT USER FROM dbo.CONTENT",
    ],
)
def test_bare_functions_are_allowlisted_too(sql: str) -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    with pytest.raises(SqlCtxError) as caught:
        QueryValidator().validate(sql, profile=profile("dbo"), adapter=adapter)  # type: ignore[arg-type]
    assert caught.value.code == "QUERY_FUNCTION_NOT_ALLOWED"


def test_allowlisted_bare_function_still_parses() -> None:
    adapter = FakeValidationAdapter([table("dbo", "CONTENT")])
    validated = QueryValidator().validate(
        "SELECT CURRENT_TIMESTAMP FROM dbo.CONTENT",
        profile=profile("dbo"),
        adapter=adapter,  # type: ignore[arg-type]
    )
    assert "CURRENT_TIMESTAMP" in validated.sql.upper()
