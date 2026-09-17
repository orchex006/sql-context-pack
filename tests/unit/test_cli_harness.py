from __future__ import annotations

import subprocess
from typing import Any

from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.models import ConnectionProfileDescriptor


def test_codex_harness_uses_ephemeral_bearer_env_config(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(main, "_connection", lambda: ("http://127.0.0.1:8765", "private-token"))
    monkeypatch.setattr(main.shutil, "which", lambda _: "codex.exe")

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["harness", "run", "--harness", "codex"])

    assert result.exit_code == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert 'mcp_servers.sql-context-pack.url="http://127.0.0.1:8765/mcp"' in arguments
    assert 'mcp_servers.sql-context-pack.bearer_token_env_var="SQLCTX_API_TOKEN"' in arguments
    assert all("private-token" not in item for item in arguments)
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["SQLCTX_API_TOKEN"] == "private-token"


def test_codex_effective_mcp_list_uses_same_protected_invocation(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(main, "_connection", lambda: ("http://127.0.0.1:8765", "private-token"))
    monkeypatch.setattr(main.shutil, "which", lambda _: "codex.exe")

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["harness", "mcp-list", "--harness", "codex"])

    assert result.exit_code == 0
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-2:] == ["mcp", "list"]
    assert all("private-token" not in item for item in arguments)
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["SQLCTX_API_TOKEN"] == "private-token"


def test_launch_reuses_running_server_and_opens_protected_harness(monkeypatch: Any) -> None:
    descriptor = ConnectionProfileDescriptor(
        name="agrimap-dev",
        engine=DatabaseEngine.SQLSERVER,
        allowed_schemas=["agrimap_app"],
        allowed_object_types=[ObjectType.TABLE],
        sample_rows_per_table=10,
        ready=True,
    )

    class FakeProfiles:
        def list_descriptors(self) -> list[ConnectionProfileDescriptor]:
            return [descriptor]

        def resolve(self, _: str) -> object:
            return type("Resolved", (), {"engine": DatabaseEngine.SQLSERVER})()

    captured: dict[str, object] = {}
    monkeypatch.setattr(main, "YamlConnectionProfileRepository", FakeProfiles)
    monkeypatch.setattr(main, "_port_is_listening", lambda _: True)
    monkeypatch.setattr(
        main,
        "create_adapter",
        lambda _: type("Adapter", (), {"test_connection": lambda self, profile: None})(),
    )
    monkeypatch.setattr(
        main,
        "_harness_invocation",
        lambda _: (["codex.exe", "-c", "safe-config"], {"SQLCTX_API_TOKEN": "secret"}),
    )

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["arguments"] = arguments
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = CliRunner().invoke(main.app, ["launch", "--harness", "codex"])

    assert result.exit_code == 0
    assert captured["arguments"] == ["codex.exe", "-c", "safe-config"]
    assert "agrimap-dev" in result.stdout
