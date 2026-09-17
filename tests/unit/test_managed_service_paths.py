from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sqlctx.security.profiles import default_config_dir
from sqlctx.security.runtime import (
    EncryptedProfileCredentialStore,
    JsonRuntimeStateStore,
    default_runtime_dir,
)


@pytest.mark.skipif(os.name != "nt", reason="managed Windows path discovery")
def test_managed_install_marker_routes_cli_and_bridge_to_program_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root = tmp_path / "SQLContextPack"
    managed_root.mkdir()
    (managed_root / "service-config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.delenv("SQLCTX_CONFIG_DIR", raising=False)
    monkeypatch.delenv("SQLCTX_RUNTIME_DIR", raising=False)

    assert default_config_dir() == managed_root / "config"
    assert default_runtime_dir() == managed_root / "runtime"


def test_encrypted_profile_tree_remains_readable_after_managed_migration(tmp_path: Path) -> None:
    source = tmp_path / "owner-runtime"
    destination = tmp_path / "managed-runtime"
    values = {
        "host": "db.internal",
        "database": "demo",
        "username": "readonly",
        "password": "never-serialize-this",
    }
    EncryptedProfileCredentialStore(JsonRuntimeStateStore(source)).put("demo", values)

    shutil.copytree(source, destination)

    migrated = EncryptedProfileCredentialStore(JsonRuntimeStateStore(destination))
    assert migrated.get("demo") == values
