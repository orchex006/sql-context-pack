from pathlib import Path

from sqlctx.security.masking import DeterministicMaskingEngine, scan_and_redact_sql_literals
from sqlctx.security.runtime import EncryptedSnapshotSecretStore, JsonRuntimeStateStore


def test_alias_is_stable_across_engine_instances(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    first = DeterministicMaskingEngine(EncryptedSnapshotSecretStore(state))
    alias_1 = first.mask(column_name="username", value="Owner007", snapshot_id="cat_1").masked_value

    second = DeterministicMaskingEngine(EncryptedSnapshotSecretStore(state))
    alias_2 = second.mask(
        column_name="username", value="Owner007", snapshot_id="cat_1"
    ).masked_value

    assert alias_1 == alias_2
    assert str(alias_1).startswith("user_")
    runtime_text = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (tmp_path / "runtime").rglob("*")
        if path.is_file()
    )
    assert "Owner007" not in runtime_text


def test_high_risk_value_is_fully_redacted(tmp_path: Path) -> None:
    engine = DeterministicMaskingEngine(
        EncryptedSnapshotSecretStore(JsonRuntimeStateStore(tmp_path / "runtime"))
    )
    decision = engine.mask(column_name="access_token", value="top-secret", snapshot_id="cat_1")
    assert decision.masked_value == "[REDACTED]"


def test_sql_literal_scanner_redacts_secrets() -> None:
    cleaned, count = scan_and_redact_sql_literals(
        "CREATE PROC p AS SELECT password='secret-value', 'Bearer abc.def.ghi';"
    )
    assert count == 2
    assert "secret-value" not in cleaned
    assert "abc.def.ghi" not in cleaned
