from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.models import ConnectionProfileDescriptor


def test_profile_list_prints_safe_names_and_readiness(monkeypatch: Any) -> None:
    descriptor = ConnectionProfileDescriptor(
        name="agrimap-readonly",
        engine=DatabaseEngine.SQLSERVER,
        allowed_schemas=["agrimap_app"],
        allowed_object_types=[ObjectType.TABLE, ObjectType.PROCEDURE],
        sample_rows_per_table=10,
        ready=True,
    )
    monkeypatch.setattr(
        main.YamlConnectionProfileRepository,
        "list_descriptors",
        lambda _: [descriptor],
    )

    result = CliRunner().invoke(main.app, ["profile", "list"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["configured"] == 1
    assert payload["ready"] == 1
    assert payload["profiles"][0]["name"] == "agrimap-readonly"
    assert "password" not in result.stdout.lower()


def test_profile_trust_certificate_requires_explicit_enable(monkeypatch: Any) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        main.YamlConnectionProfileRepository,
        "set_trust_server_certificate",
        lambda _, profile, enabled: calls.append((profile, enabled)),
    )

    result = CliRunner().invoke(
        main.app, ["profile", "trust-certificate", "agrimap-dev", "--enable"]
    )

    assert result.exit_code == 0
    assert calls == [("agrimap-dev", True)]
    assert json.loads(result.stdout)["trust_server_certificate"] is True
