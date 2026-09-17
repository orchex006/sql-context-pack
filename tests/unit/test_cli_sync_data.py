from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from sqlctx.cli import main
from sqlctx.server import facade


class FakeSyncResult:
    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {
            "considered_context_count": 2,
            "synced_context_count": 2,
            "skipped_context_count": 0,
            "failed_context_count": 0,
            "added_object_count": 1,
            "changed_object_count": 1,
            "deleted_object_count": 0,
            "reused_object_count": 10,
            "refreshed_object_count": 4,
            "definition_change_detection_complete": True,
            "skipped_reasons": {},
            "contexts": [],
        }


def test_sync_data_accepts_repeatable_profile_filters_and_prints_json(
    monkeypatch: Any,
) -> None:
    captured: dict[str, object] = {}

    class FakeService:
        def sync_data(self, *, profile_names: list[str]) -> FakeSyncResult:
            captured["profile_names"] = profile_names
            return FakeSyncResult()

    monkeypatch.setattr(facade, "ServiceFacade", FakeService)

    result = CliRunner().invoke(
        main.app,
        ["sync-data", "--profile", "one", "--profile", "two"],
    )

    assert result.exit_code == 0
    assert captured["profile_names"] == ["one", "two"]
    assert json.loads(result.stdout)["synced_context_count"] == 2
    assert "secret" not in result.stdout.lower()
