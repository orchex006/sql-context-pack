from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "sql/DB_METADATA_CONTEXT/table/DB_METADATA_CONTEXT.sql"


def test_metadata_context_is_exactly_one_rerunnable_table_contract() -> None:
    sql = DDL.read_text(encoding="utf-8")
    assert "SET ANSI_NULLS ON;" in sql
    assert "SET QUOTED_IDENTIFIER ON;" in sql
    assert "SET XACT_ABORT ON;" in sql
    assert "[agrimap_app].[DB_METADATA_CONTEXT]" in sql
    assert sql.count("CREATE TABLE") == 1
    assert "[OBJECT_TYPE] IN ('TABLE', 'PROCEDURE', 'FUNCTION')" in sql
    assert "[CONTEXT_CODE]" in sql
    assert "[DESCRIPTION]" in sql
    assert "[TAGS_JSON]" in sql
    assert "ISJSON([TAGS_JSON]) = 1" in sql
    assert "CHECK ([HEADER_VERSION] = 1)" in sql
    assert "CHECK ([OUTPUT_FORMAT_VERSION] = 2)" in sql
    assert "[USER_CREATED] NUMERIC(38, 0) NOT NULL" in sql
    assert "CREATE PROCEDURE" not in sql
    assert "CREATE FUNCTION" not in sql
