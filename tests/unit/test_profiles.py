from pathlib import Path

import pytest
import yaml

from sqlctx.core.errors import SqlCtxError
from sqlctx.security.profiles import YamlConnectionProfileRepository
from sqlctx.security.runtime import EncryptedProfileCredentialStore, JsonRuntimeStateStore

VALID_PROFILE = """
profiles:
  demo:
    engine: postgres
    host_env: TEST_DB_HOST
    port: 5432
    database_env: TEST_DB_NAME
    username_env: TEST_DB_USER
    password_env: TEST_DB_PASSWORD
    allowed_schemas: [public]
    allowed_object_types: [table, procedure]
    sample_rows_per_table: 10
    max_sample_rows_per_table: 20
    masking_policy: strict
"""


def test_profile_lists_safely_and_resolves_internally(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(VALID_PROFILE, encoding="utf-8")
    environment = {
        "TEST_DB_HOST": "localhost",
        "TEST_DB_NAME": "db",
        "TEST_DB_USER": "owner_user",
        "TEST_DB_PASSWORD": "owner_password",
    }
    repository = YamlConnectionProfileRepository(path, environment)

    descriptor = repository.list_descriptors()[0]
    assert descriptor.ready
    assert "password" not in descriptor.model_dump()
    resolved = repository.resolve("demo")
    assert "owner_password" not in repr(resolved)


def test_raw_password_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(
        VALID_PROFILE.replace("password_env: TEST_DB_PASSWORD", "password: do-not-store-this"),
        encoding="utf-8",
    )
    with pytest.raises(SqlCtxError, match="environment-variable") as error:
        YamlConnectionProfileRepository(path, {}).list_descriptors()
    assert error.value.code == "RAW_CREDENTIAL_IN_PROFILE"


def test_protected_credential_reference_resolves_without_yaml_secrets(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    credentials = EncryptedProfileCredentialStore(state)
    credentials.put(
        "demo",
        {
            "host": "localhost",
            "database": "private_db",
            "username": "private_user",
            "password": "private_password",
        },
    )
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """profiles:
  demo:
    engine: postgres
    credential_ref: demo
    port: 5432
    allowed_schemas: [public]
    allowed_object_types: [table, procedure]
""",
        encoding="utf-8",
    )
    repository = YamlConnectionProfileRepository(path, {}, credentials)

    assert repository.list_descriptors()[0].ready
    resolved = repository.resolve("demo")
    host, _, _, username, password = resolved.connection_values()
    assert host == "localhost"
    assert username == "private_user"
    assert password == "private_password"
    yaml_text = path.read_text(encoding="utf-8")
    assert "private_user" not in yaml_text
    assert "private_password" not in yaml_text


def test_sqlserver_trust_policy_is_explicit_and_persisted(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    credentials = EncryptedProfileCredentialStore(state)
    credentials.put(
        "dev",
        {"host": "localhost", "database": "db", "username": "reader", "password": "secret"},
    )
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """profiles:
  dev:
    engine: sqlserver
    credential_ref: dev
    port: 1433
    allowed_schemas: [dbo]
    allowed_object_types: [table, procedure]
""",
        encoding="utf-8",
    )
    repository = YamlConnectionProfileRepository(path, {}, credentials)

    repository.set_trust_server_certificate("dev", True)

    assert repository.list_descriptors()[0].trust_server_certificate is True
    assert repository.resolve("dev").trust_server_certificate is True
    assert (
        yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"]["dev"][
            "trust_server_certificate"
        ]
        is True
    )


def test_schema_scope_and_object_exclusions_are_persisted(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(VALID_PROFILE, encoding="utf-8")
    repository = YamlConnectionProfileRepository(
        path,
        {
            "TEST_DB_HOST": "localhost",
            "TEST_DB_NAME": "db",
            "TEST_DB_USER": "reader",
            "TEST_DB_PASSWORD": "secret",
        },
    )

    repository.set_schema_policy(
        "demo",
        allowed_schemas=["agrimap_app", "agrimap_etl", "agrimapadm"],
        excluded_object_patterns=["i[0-9]*"],
    )

    resolved = repository.resolve("demo")
    assert resolved.allowed_schemas == ("agrimap_app", "agrimap_etl", "agrimapadm")
    assert resolved.excluded_object_patterns == ("i[0-9]*",)
    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"]["demo"]
    assert persisted["excluded_object_patterns"] == ["i[0-9]*"]


def test_remove_profile_deletes_unshared_protected_credentials(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    credentials = EncryptedProfileCredentialStore(state)
    credentials.put(
        "demo",
        {"host": "localhost", "database": "db", "username": "user", "password": "secret"},
    )
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """profiles:
  demo:
    engine: postgres
    credential_ref: demo
    port: 5432
    allowed_schemas: [app]
    allowed_object_types: [table]
""",
        encoding="utf-8",
    )
    repository = YamlConnectionProfileRepository(path, {}, credentials)

    result = repository.remove("demo")

    assert result["removed"] is True
    assert result["credential_removed"] is True
    assert not credentials.exists("demo")
    assert repository.list_descriptors() == []


def test_remove_profile_preserves_shared_credentials(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    credentials = EncryptedProfileCredentialStore(state)
    credentials.put(
        "shared",
        {"host": "localhost", "database": "db", "username": "user", "password": "secret"},
    )
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """profiles:
  one:
    engine: postgres
    credential_ref: shared
    port: 5432
    allowed_schemas: [app]
    allowed_object_types: [table]
  two:
    engine: postgres
    credential_ref: shared
    port: 5432
    allowed_schemas: [app]
    allowed_object_types: [table]
""",
        encoding="utf-8",
    )
    repository = YamlConnectionProfileRepository(path, {}, credentials)

    result = repository.remove("one")

    assert result["credential_removed"] is False
    assert result["credential_preserved_reason"] == "credential_ref_shared_by_another_profile"
    assert credentials.exists("shared")
    assert [item.name for item in repository.list_descriptors()] == ["two"]
