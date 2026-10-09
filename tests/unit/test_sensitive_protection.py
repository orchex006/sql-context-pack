"""3.0.0 sensitive-data protection: classification, fakes, marking, guard and reveal handoff."""

from __future__ import annotations

import datetime as dt
import runpy
from pathlib import Path

import pytest

from sqlctx.adapters.base import QueryColumnMetadata
from sqlctx.core.enums import SensitivityClass
from sqlctx.core.errors import SqlCtxError
from sqlctx.query_data.contracts import QueryDataRequest
from sqlctx.security.masking import (
    DeterministicMaskingEngine,
    redact_pii_text,
    scan_and_redact_pii_literals,
)
from sqlctx.security.protector import THAI_FAMILY, THAI_GIVEN, ValueProtector
from sqlctx.security.runtime import EncryptedSnapshotSecretStore, JsonRuntimeStateStore
from sqlctx.security.sensitivity import (
    classify_column_name,
    classify_database_label,
    thai_id_check_digit,
)

FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_query_data_service.py")))
KEY = b"k" * 32


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        # Every column that 2.1.0 classified as public or classified only by accident.
        ("FIRST_NAME", SensitivityClass.PERSONAL_NAME),
        ("LNAME", SensitivityClass.PERSONAL_NAME),
        ("FARMER_NAME", SensitivityClass.PERSONAL_NAME),
        ("FarmerName", SensitivityClass.PERSONAL_NAME),
        ("SURNAME", SensitivityClass.PERSONAL_NAME),
        ("PWD", SensitivityClass.PASSWORD),
        ("TOKEN", SensitivityClass.ACCESS_TOKEN),
        ("ADDR", SensitivityClass.ADDRESS),
        ("LATITUDE", SensitivityClass.PRECISE_LOCATION),
        ("GEOM", SensitivityClass.PRECISE_LOCATION),
        ("REMARK", SensitivityClass.FREE_TEXT),
        ("ID_CARD", SensitivityClass.NATIONAL_ID),
        ("CITIZEN_NO", SensitivityClass.NATIONAL_ID),
        ("PID", SensitivityClass.NATIONAL_ID),
        ("DOB", SensitivityClass.DATE_OF_BIRTH),
        ("BANK_ACC_NO", SensitivityClass.FINANCIAL_ACCOUNT),
        ("TEL", SensitivityClass.PHONE),
        ("MOBILE_NO", SensitivityClass.PHONE),
        ("EMAIL", SensitivityClass.EMAIL),
        ("CREATED_BY", SensitivityClass.USERNAME),
        ("USER_NAME", SensitivityClass.USERNAME),
    ],
)
def test_thai_and_abbreviated_columns_are_classified(
    column: str, expected: SensitivityClass
) -> None:
    assert classify_column_name(column) == expected


@pytest.mark.parametrize(
    "column",
    ["ID", "CROP_NAME", "PROVINCE_NAME", "AREA_RAI", "STATUS", "IS_PASS", "CREATED_DATE"],
)
def test_ordinary_columns_stay_public(column: str) -> None:
    assert classify_column_name(column) == SensitivityClass.PUBLIC


def test_database_classification_marks_any_classified_column_sensitive() -> None:
    assert classify_database_label("Contact Info", "Confidential") == (
        SensitivityClass.UNKNOWN_SENSITIVE
    )
    assert classify_database_label("National ID", None) == SensitivityClass.NATIONAL_ID
    assert classify_database_label(None, "Public") == SensitivityClass.PUBLIC
    protector = ValueProtector(KEY, column_classes={"CODE": SensitivityClass.UNKNOWN_SENSITIVE})
    assert protector.protect("CODE", "anything") == "[REDACTED]"


def test_fake_thai_id_keeps_format_has_valid_checksum_and_is_never_real() -> None:
    protector = ValueProtector(KEY)
    fake = protector.protect("ID_CARD", "1-1037-00012-34-5")
    digits = fake.replace("-", "")
    assert len(digits) == 13
    assert fake.count("-") == 4
    assert digits[0] == "0"  # no issued Thai ID starts with 0
    assert int(digits[12]) == thai_id_check_digit(digits[:12])
    assert "1037000123" not in digits
    assert protector.protect("ID_CARD", "1-1037-00012-34-5") == fake


def test_fake_names_are_thai_when_input_is_thai_and_follow_the_column() -> None:
    protector = ValueProtector(KEY)
    assert protector.protect("FIRST_NAME", "สมชาย") in THAI_GIVEN
    assert protector.protect("LAST_NAME", "ใจดี") in THAI_FAMILY
    full = protector.protect("FARMER_NAME", "นายสมชาย ใจดี")
    assert full.startswith("นาย")
    assert "สมชาย" not in full and "ใจดี" not in full


def test_generalization_and_redaction() -> None:
    protector = ValueProtector(KEY)
    assert protector.protect("DOB", dt.date(1990, 5, 17)) == "1990"
    assert protector.protect("LATITUDE", 13.756331) == "13.8"
    assert protector.protect("ADDR", "99/1 ม.4 ต.บางพลี") == "[ADDRESS]"
    assert protector.protect("PWD", "hunter2") == "[REDACTED]"
    assert protector.counts["generalize"] == 3
    assert protector.counts["redact"] == 1


def test_free_text_scan_replaces_embedded_pii_and_keeps_the_rest() -> None:
    protector = ValueProtector(KEY)
    text = "โทร 081-234-5678 คุณสมชาย บัตร 1103700012345 อีเมล a.b@farm.co.th"
    masked = protector.protect("REMARK", text)
    for secret in ("081-234-5678", "สมชาย", "1103700012345", "a.b@farm.co.th"):
        assert secret not in masked
    assert masked.startswith("โทร ")
    assert "@example.invalid" in masked


def test_public_column_value_shape_is_still_protected() -> None:
    protector = ValueProtector(KEY)
    assert protector.protect("CODE", "0812345678") != "0812345678"
    assert protector.protect("CODE", "2024-01-31") == "2024-01-31"


def test_query_result_marks_protected_columns_and_returns_reveal_handoff() -> None:
    stream = FIXTURE["FakeStream"](
        [
            QueryColumnMetadata("ID", "int"),
            QueryColumnMetadata("FIRST_NAME", "nvarchar"),
            QueryColumnMetadata("ID_CARD", "varchar"),
        ],
        [[(1, "สมชาย", "1103700012345")], []],
    )
    result = FIXTURE["service"]().execute(
        QueryDataRequest(
            profile="demo", sql="SELECT ID, FIRST_NAME, ID_CARD FROM dbo.CONTENT_SHARE"
        ),
        profile=FIXTURE["profile"](),
        adapter=FIXTURE["FakeQueryAdapter"](stream),
    )
    assert "สมชาย" not in result.markdown
    assert "1103700012345" not in result.markdown
    assert result.markdown.splitlines()[0] == (
        "| ID | FIRST_NAME ⟨FAKE:PERSONAL_NAME⟩ | ID_CARD ⟨FAKE:NATIONAL_ID⟩ |"
    )
    treatments = {column.name: column.treatment for column in result.columns}
    assert treatments == {"ID": "public", "FIRST_NAME": "fake", "ID_CARD": "fake"}
    assert result.protected_value_counts == {"fake": 2}
    assert result.reveal_handoff is not None
    assert result.reveal_handoff.model_may_view is False
    assert result.reveal_handoff.columns == ["FIRST_NAME", "ID_CARD"]
    assert "--reveal" in result.reveal_handoff.user_steps[0]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ID FROM dbo.CONTENT_SHARE WHERE FIRST_NAME = 'x'",
        "SELECT ID FROM dbo.CONTENT_SHARE WHERE ID_CARD LIKE '11%'",
        "SELECT ID FROM dbo.CONTENT_SHARE ORDER BY TEL",
        "SELECT LEN(TEL) AS n FROM dbo.CONTENT_SHARE",
        "SELECT CASE WHEN FIRST_NAME = 'x' THEN 1 ELSE 0 END AS hit FROM dbo.CONTENT_SHARE",
        "SELECT x FROM (SELECT FIRST_NAME AS x FROM dbo.CONTENT_SHARE) AS s WHERE x = 'y'",
    ],
)
def test_protected_columns_cannot_be_inferred_through_predicates(sql: str) -> None:
    stream = FIXTURE["FakeStream"]([QueryColumnMetadata("ID", "int")], [[(1,)]])
    with pytest.raises(SqlCtxError) as caught:
        FIXTURE["service"]().execute(
            QueryDataRequest(profile="demo", sql=sql),
            profile=FIXTURE["profile"](),
            adapter=FIXTURE["FakeQueryAdapter"](stream),
        )
    assert caught.value.code == "QUERY_SENSITIVE_USAGE_RESTRICTED"
    handoff = caught.value.details["reveal_handoff"]
    assert handoff["model_may_view"] is False
    assert handoff["reason"] == "restricted_usage"


def test_public_predicates_remain_allowed() -> None:
    stream = FIXTURE["FakeStream"]([QueryColumnMetadata("FIRST_NAME", "nvarchar")], [[("A",)]])
    result = FIXTURE["service"]().execute(
        QueryDataRequest(
            profile="demo", sql="SELECT FIRST_NAME FROM dbo.CONTENT_SHARE WHERE ID = 1"
        ),
        profile=FIXTURE["profile"](),
        adapter=FIXTURE["FakeQueryAdapter"](stream),
    )
    assert "| A |" not in result.markdown


def test_sample_rows_are_protected_and_marked(tmp_path: Path) -> None:
    engine = DeterministicMaskingEngine(
        EncryptedSnapshotSecretStore(JsonRuntimeStateStore(tmp_path / "runtime"))
    )
    rows, markers = engine.protect_rows(
        snapshot_id="cat_1",
        columns=["ID", "LNAME", "NOTE"],
        rows=[[1, "ใจดี", "ติดต่อ 0812345678"]],
    )
    assert rows[0][0] == 1
    assert rows[0][1] in THAI_FAMILY
    assert "0812345678" not in rows[0][2]
    assert markers == {"LNAME": "⟨FAKE:PERSONAL_NAME⟩", "NOTE": "⟨SCANNED:FREE_TEXT⟩"}


def test_routine_literals_and_descriptions_get_visible_marks() -> None:
    cleaned, count = scan_and_redact_pii_literals(
        "SELECT * FROM t WHERE email = N'owner@farm.co.th' AND pid = '1103700012345'"
    )
    assert count == 2
    assert "N'[REDACTED:EMAIL]'" in cleaned
    assert "'[REDACTED:NATIONAL_ID]'" in cleaned
    assert "WHERE email" in cleaned
    text, found = redact_pii_text("ผู้ดูแล นายสมชาย ใจดี โทร 02-123-4567")
    assert found == 2
    assert "สมชาย" not in text and "02-123-4567" not in text


def test_cli_reveal_is_refused_without_an_interactive_terminal() -> None:
    from typer.testing import CliRunner

    from sqlctx.cli.main import app

    result = CliRunner().invoke(app, ["query", "--reveal", "--profile", "demo", "SELECT 1 AS x"])
    assert result.exit_code != 0
    assert "REVEAL_REQUIRES_INTERACTIVE_TERMINAL" in (result.output + str(result.exception))
