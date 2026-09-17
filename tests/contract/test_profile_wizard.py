from pathlib import Path

import pytest
import typer
import yaml

from sqlctx.cli.configure import configure_profile
from sqlctx.core.enums import DatabaseEngine
from sqlctx.security.profiles import YamlConnectionProfileRepository
from sqlctx.security.runtime import EncryptedProfileCredentialStore, JsonRuntimeStateStore


def test_wizard_places_safe_config_and_encrypted_credentials(tmp_path: Path) -> None:
    config = tmp_path / "config"
    runtime = tmp_path / "runtime"
    result = configure_profile(
        profile_name="agrimap-readonly",
        engine=DatabaseEngine.SQLSERVER,
        host="db.internal",
        port=1433,
        database="agrimap",
        username="reader",
        password="do-not-write-to-yaml",
        allowed_schemas=["agrimap_app"],
        config_dir=config,
        runtime_dir=runtime,
    )

    assert result["ok"] is True
    assert (config / "categories.yaml").is_file()
    assert (config / "category-overrides.yaml").is_file()
    profile_text = (config / "profiles.yaml").read_text(encoding="utf-8")
    assert "credential_ref: agrimap-readonly" in profile_text
    assert "reader" not in profile_text
    assert "do-not-write-to-yaml" not in profile_text
    assert "db.internal" not in profile_text
    assert yaml.safe_load(profile_text)["profiles"]["agrimap-readonly"]["port"] == 1433
    assert (
        yaml.safe_load(profile_text)["profiles"]["agrimap-readonly"]["trust_server_certificate"]
        is False
    )

    store = EncryptedProfileCredentialStore(JsonRuntimeStateStore(runtime))
    repository = YamlConnectionProfileRepository(config / "profiles.yaml", {}, store)
    resolved = repository.resolve("agrimap-readonly")
    _, _, database, _, password = resolved.connection_values()
    assert database == "agrimap"
    assert password == "do-not-write-to-yaml"


def test_wizard_rejects_shell_quotes_in_host(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter, match="without quote characters"):
        configure_profile(
            profile_name="quoted-host",
            engine=DatabaseEngine.SQLSERVER,
            host="'10.20.30.40\\DB2019'",
            port=1433,
            database="example",
            username="reader",
            password="secret",
            allowed_schemas=["dbo"],
            config_dir=tmp_path / "config",
            runtime_dir=tmp_path / "runtime",
        )
