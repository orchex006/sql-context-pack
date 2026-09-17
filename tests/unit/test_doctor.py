from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sqlctx import __version__
from sqlctx.cli import main
from sqlctx.doctor import inspect_installation
from sqlctx.security.runtime import JsonRuntimeStateStore


def test_doctor_default_does_not_write_or_start_database(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SQLCTX_RUNTIME_DIR", str(tmp_path / "absent-runtime"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        JsonRuntimeStateStore,
        "write_json",
        lambda *_: (_ for _ in ()).throw(AssertionError("doctor wrote state")),
    )
    result = CliRunner().invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["versions"]["package"] == "2.1.0"
    assert payload["status"] == "attention"
    assert not (tmp_path / "absent-runtime").exists()


def test_doctor_version_has_no_probe(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        main,
        "inspect_installation",
        lambda **_: (_ for _ in ()).throw(AssertionError("version probed service")),
    )
    result = CliRunner().invoke(main.app, ["doctor", "version", "--host", "claude"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"host": "claude", "package_version": "2.1.0"}


def test_doctor_update_preserves_selected_host(tmp_path: Path, monkeypatch: Any) -> None:
    calls = []
    monkeypatch.setattr(main, "product_update", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(
        main.app, ["doctor", "update", "--host", "gemini", "--source", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert calls == [{"source": tmp_path, "host": "gemini", "version": None}]


def test_doctor_source_version_drift_is_reported(tmp_path: Path, monkeypatch: Any) -> None:
    # Derived from the package version so a release bump cannot silently make source == installed,
    # which would leave this test asserting drift detection against no drift at all.
    major, minor, _ = __version__.split(".")
    newer = f"{major}.{int(minor) + 1}.0"
    (tmp_path / ".codex-plugin").mkdir()
    (tmp_path / ".codex-plugin/plugin.json").write_text(
        json.dumps({"version": newer}), encoding="utf-8"
    )
    monkeypatch.setenv("SQLCTX_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = inspect_installation(source=tmp_path)
    assert newer != __version__
    assert result["update_available"] is True
    assert result["versions"]["source"] == newer


def test_doctor_update_forwards_the_requested_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(main, "product_update", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(
        main.app,
        ["doctor", "update", "--host", "agy", "--source", str(tmp_path), "--version", "2.1.0"],
    )
    assert result.exit_code == 0
    assert calls == [{"source": tmp_path, "host": "agy", "version": "2.1.0"}]


def test_doctor_rejects_version_on_read_only_actions() -> None:
    result = CliRunner().invoke(main.app, ["doctor", "check", "--version", "2.1.0"])
    assert result.exit_code != 0


def test_product_update_rejects_a_malformed_version() -> None:
    result = CliRunner().invoke(main.app, ["doctor", "update", "--version", "latest"])
    assert result.exit_code != 0
