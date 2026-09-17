from __future__ import annotations

import pytest

from sqlctx.adapters.base import BaseDatabaseAdapter
from sqlctx.adapters.postgres.adapter import PostgreSqlAdapter
from sqlctx.adapters.sqlserver.adapter import SqlServerAdapter
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ObjectRef


@pytest.mark.parametrize(
    "declaration",
    [
        "CREATE PROCEDURE",
        "CREATE PROC",
        "ALTER PROCEDURE",
        "ALTER PROC",
        "create procedure",
        "AlTeR pRoC",
        "CREATE OR ALTER PROCEDURE",
        "create or alter proc",
    ],
)
def test_sqlserver_procedure_definition_uses_canonical_header(
    declaration: str,
) -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    adapter._assert_allowed = lambda *_: None  # type: ignore[method-assign]
    body = " [agrimap_app].[UM_USER_Q]\nAS\nBEGIN\n    SELECT 'ALTER PROCEDURE';\nEND;"
    adapter._execute = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        {"definition": f"\ufeff  {declaration}{body}"}
    ]

    result = adapter.get_procedure_definition(
        object(),  # type: ignore[arg-type]
        ObjectRef(
            object_id="procedure:agrimap_app.UM_USER_Q",
            engine="sqlserver",
            schema_name="agrimap_app",
            object_name="UM_USER_Q",
            object_type="procedure",
        ),
    )

    assert result == f"\ufeff  CREATE OR ALTER PROCEDURE{body}"
    assert "SELECT 'ALTER PROCEDURE';" in result


def test_sqlserver_procedure_definition_rejects_unsupported_leading_header() -> None:
    adapter = SqlServerAdapter(lambda _: None)  # type: ignore[arg-type,return-value]
    adapter._assert_allowed = lambda *_: None  # type: ignore[method-assign]
    adapter._execute = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        {"definition": "EXEC app.generated_comment; CREATE PROCEDURE app.p AS SELECT 1;"}
    ]

    with pytest.raises(SqlCtxError) as caught:
        adapter.get_procedure_definition(
            object(),  # type: ignore[arg-type]
            ObjectRef(
                object_id="procedure:app.p",
                engine="sqlserver",
                schema_name="app",
                object_name="p",
                object_type="procedure",
            ),
        )

    assert caught.value.code == "PROCEDURE_DEFINITION_HEADER_UNSUPPORTED"
    assert "generated_comment" not in caught.value.message


def test_non_sqlserver_adapter_keeps_native_procedure_definition_path() -> None:
    assert (
        PostgreSqlAdapter.get_procedure_definition is BaseDatabaseAdapter.get_procedure_definition
    )


def test_sqlserver_normalization_does_not_change_procedure_body() -> None:
    source = "ALTER PROCEDURE [app].[p] @value INT AS\nBEGIN\n    SELECT @value;\nEND;"
    expected_body = source[source.index(" [app]") :]

    normalized = SqlServerAdapter.normalize_procedure_definition(source)

    assert normalized == "CREATE OR ALTER PROCEDURE" + expected_body
    assert normalized.count("CREATE OR ALTER PROCEDURE") == 1


BANNER = (
    "-- =============================================\r\n"
    "-- Author       : Nattawit.kr\r\n"
    "-- Create date  : 2026-05-18\r\n"
    "-- Description  : Create ALLOCATE Q\r\n"
    "-- =============================================\r\n"
)

OUTPUT_PROCEDURE = (
    "CREATE PROCEDURE [agrimap_app].[ZZ_PROBE_OUTPUT]\r\n"
    "    @PI_X INT = NULL,\r\n"
    "    @PO_MSG NVARCHAR(400) OUTPUT\r\n"
    "AS\r\nBEGIN\r\n    SET NOCOUNT ON;\r\n    SET @PO_MSG = N'ok';\r\nEND"
)
PROCID_PROCEDURE = (
    "CREATE PROCEDURE [agrimap_app].[ZZ_PROBE_PROCID] @PI_X INT = NULL\r\n"
    "AS\r\nBEGIN\r\n    SET NOCOUNT ON;\r\n"
    "    SELECT OBJECT_NAME(@@PROCID) AS PROC_NAME;\r\nEND"
)
SCALAR_FUNCTION = (
    "CREATE FUNCTION [agrimap_app].[ZZ_PROBE_FN] (@PI_N INT)\r\n"
    "RETURNS NVARCHAR(200)\r\n"
    "AS\r\nBEGIN\r\n    RETURN N'probe-' + CAST(@PI_N AS NVARCHAR(20));\r\nEND"
)


@pytest.mark.parametrize(
    ("label", "definition"),
    [
        ("A_output_parameter", OUTPUT_PROCEDURE),
        ("B_procid", PROCID_PROCEDURE),
    ],
)
def test_procedure_features_do_not_block_canonicalization(label: str, definition: str) -> None:
    """Repro A/B: OUTPUT parameters and @@PROCID are not a declaration-boundary concern."""
    normalized = SqlServerAdapter.normalize_procedure_definition(definition)

    assert normalized.startswith("CREATE OR ALTER PROCEDURE")
    assert normalized == "CREATE OR ALTER " + definition[len("CREATE ") :]


def test_scalar_function_is_canonicalized() -> None:
    """Repro C: a scalar function is a supported declaration."""
    normalized = SqlServerAdapter.normalize_function_definition(SCALAR_FUNCTION)

    assert normalized.startswith("CREATE OR ALTER FUNCTION")
    assert "RETURNS NVARCHAR(200)" in normalized


@pytest.mark.parametrize(
    ("prefix", "label"),
    [
        (BANNER, "ssms_line_comment_banner"),
        ("/* banner */\r\n", "block_comment"),
        ("/* outer /* nested */ still comment */\n", "nested_block_comment"),
        ("\ufeff-- lead\n\n  /* mixed */\t", "bom_mixed_comments_and_whitespace"),
    ],
)
def test_leading_comments_are_preserved_and_do_not_fail_analysis(prefix: str, label: str) -> None:
    """The real defect: a legal banner comment before the declaration must not fail extraction."""
    normalized = SqlServerAdapter.normalize_procedure_definition(prefix + OUTPUT_PROCEDURE)

    assert normalized == prefix + "CREATE OR ALTER " + OUTPUT_PROCEDURE[len("CREATE ") :]
    assert normalized.startswith(prefix)
    assert "@PO_MSG NVARCHAR(400) OUTPUT" in normalized


def test_leading_comments_are_preserved_for_functions() -> None:
    normalized = SqlServerAdapter.normalize_function_definition(BANNER + SCALAR_FUNCTION)

    assert normalized == BANNER + "CREATE OR ALTER " + SCALAR_FUNCTION[len("CREATE ") :]


@pytest.mark.parametrize(
    "definition",
    [
        "-- CREATE PROCEDURE app.decoy AS SELECT 1;\nSELECT 1;",
        "/* CREATE PROCEDURE app.decoy */ SELECT 1;",
        "/* unterminated CREATE PROCEDURE app.decoy AS SELECT 1;",
    ],
)
def test_declaration_words_inside_comments_are_never_treated_as_the_header(
    definition: str,
) -> None:
    """Skipping comments must not let commented-out text stand in for a real declaration."""
    with pytest.raises(SqlCtxError) as caught:
        SqlServerAdapter.normalize_procedure_definition(definition)

    assert caught.value.code == "PROCEDURE_DEFINITION_HEADER_UNSUPPORTED"
    assert "decoy" not in caught.value.message
