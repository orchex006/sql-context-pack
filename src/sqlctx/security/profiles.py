"""Owner-managed connection profiles and environment secret resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import ConnectionProfileDescriptor, ResolvedConnectionProfile
from sqlctx.security.runtime import EncryptedProfileCredentialStore, _atomic_write


def default_config_dir() -> Path:
    configured = os.environ.get("SQLCTX_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            managed_root = Path(program_data) / "SQLContextPack"
            if (managed_root / "service-config.json").is_file():
                return managed_root / "config"
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sql-context-pack"


class ProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: DatabaseEngine
    credential_ref: str | None = None
    host_env: str | None = None
    port: int = Field(ge=1, le=65535)
    database_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    allowed_schemas: list[str] = Field(min_length=1)
    allowed_object_types: list[ObjectType] = Field(min_length=1)
    excluded_object_patterns: list[str] = Field(default_factory=list)
    sample_rows_per_table: int = Field(default=10, ge=10)
    max_sample_rows_per_table: int = Field(default=20, ge=10)
    masking_policy: str = "strict"
    trust_server_certificate: bool = False
    metadata_context_write: bool = False
    routine_write: bool = False

    @model_validator(mode="after")
    def validate_limits(self) -> ProfileDefinition:
        environment_fields = (
            self.host_env,
            self.database_env,
            self.username_env,
            self.password_env,
        )
        if self.credential_ref:
            if any(environment_fields):
                raise ValueError("credential_ref cannot be combined with environment references")
        elif not all(environment_fields):
            raise ValueError("all four environment references or credential_ref are required")
        if self.sample_rows_per_table > self.max_sample_rows_per_table:
            raise ValueError("sample_rows_per_table exceeds max_sample_rows_per_table")
        if self.trust_server_certificate and self.engine != DatabaseEngine.SQLSERVER:
            raise ValueError("trust_server_certificate is supported only for sqlserver profiles")
        if (
            self.metadata_context_write or self.routine_write
        ) and self.engine != DatabaseEngine.SQLSERVER:
            raise ValueError(
                "database write scopes are currently supported only for sqlserver profiles"
            )
        for schema in self.allowed_schemas:
            if not schema or any(char in schema for char in "\x00\r\n"):
                raise ValueError("allowed schema contains invalid characters")
        for pattern in self.excluded_object_patterns:
            if not pattern or any(char in pattern for char in "\x00\r\n"):
                raise ValueError("excluded object pattern contains invalid characters")
        return self


class ProfilesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profiles: dict[str, ProfileDefinition]


class YamlConnectionProfileRepository:
    def __init__(
        self,
        path: Path | None = None,
        environ: dict[str, str] | None = None,
        credentials: EncryptedProfileCredentialStore | None = None,
    ) -> None:
        self.path = path or default_config_dir() / "profiles.yaml"
        self.environ = os.environ if environ is None else environ
        self.credentials = credentials or EncryptedProfileCredentialStore()

    def _load(self) -> ProfilesDocument:
        if not self.path.is_file():
            return ProfilesDocument(profiles={})
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            if self._contains_raw_secret(raw):
                raise SqlCtxError(
                    "RAW_CREDENTIAL_IN_PROFILE",
                    "Profile files must reference environment-variable names, not raw credentials.",
                    status_code=403,
                )
            return ProfilesDocument.model_validate(raw)
        except SqlCtxError:
            raise
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise SqlCtxError(
                "INVALID_PROFILE_CONFIG", "Profile configuration is invalid."
            ) from exc

    @staticmethod
    def _contains_raw_secret(value: Any) -> bool:
        if isinstance(value, dict):
            forbidden = {
                "password",
                "passwd",
                "pwd",
                "username",
                "user",
                "host",
                "database",
                "connection_string",
                "dsn",
            }
            if any(str(key).lower() in forbidden for key in value):
                return True
            return any(
                YamlConnectionProfileRepository._contains_raw_secret(item)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(YamlConnectionProfileRepository._contains_raw_secret(item) for item in value)
        return False

    def list_descriptors(self) -> list[ConnectionProfileDescriptor]:
        result: list[ConnectionProfileDescriptor] = []
        for name, profile in sorted(self._load().profiles.items()):
            missing: list[str]
            if profile.credential_ref:
                missing = (
                    [] if self.credentials.exists(profile.credential_ref) else ["credential_ref"]
                )
            else:
                missing = []
                for env_name in (
                    profile.host_env,
                    profile.database_env,
                    profile.username_env,
                    profile.password_env,
                ):
                    if env_name is None or not self.environ.get(env_name):
                        missing.append(env_name or "<unset>")
            result.append(
                ConnectionProfileDescriptor(
                    name=name,
                    engine=profile.engine,
                    allowed_schemas=profile.allowed_schemas,
                    allowed_object_types=profile.allowed_object_types,
                    excluded_object_patterns=profile.excluded_object_patterns,
                    sample_rows_per_table=profile.sample_rows_per_table,
                    trust_server_certificate=profile.trust_server_certificate,
                    metadata_context_write=profile.metadata_context_write,
                    routine_write=profile.routine_write,
                    ready=not missing,
                    readiness_reason=("missing required environment values" if missing else None),
                )
            )
        return result

    def resolve(self, profile_name: str) -> ResolvedConnectionProfile:
        profile = self._load().profiles.get(profile_name)
        if profile is None:
            raise SqlCtxError(
                "PROFILE_NOT_FOUND", f"Unknown connection profile: {profile_name}", status_code=404
            )
        if profile.credential_ref:
            values = self.credentials.get(profile.credential_ref)
        else:
            env_names = {
                "host": profile.host_env,
                "database": profile.database_env,
                "username": profile.username_env,
                "password": profile.password_env,
            }
            missing = [
                field
                for field, env_name in env_names.items()
                if env_name is None or not self.environ.get(env_name)
            ]
            if missing:
                raise SqlCtxError(
                    "PROFILE_NOT_READY",
                    f"Profile {profile_name!r} is missing required owner environment values.",
                    status_code=503,
                )
            values = {
                field: self.environ[env_name]
                for field, env_name in env_names.items()
                if env_name is not None
            }
        return ResolvedConnectionProfile(
            name=profile_name,
            engine=profile.engine,
            host=values["host"],
            port=profile.port,
            database=values["database"],
            username=values["username"],
            password=values["password"],
            allowed_schemas=tuple(profile.allowed_schemas),
            allowed_object_types=tuple(profile.allowed_object_types),
            excluded_object_patterns=tuple(profile.excluded_object_patterns),
            sample_rows_per_table=profile.sample_rows_per_table,
            trust_server_certificate=profile.trust_server_certificate,
            metadata_context_write=profile.metadata_context_write,
            routine_write=profile.routine_write,
        )

    def set_trust_server_certificate(self, profile_name: str, enabled: bool) -> None:
        """Persist an explicit SQL Server certificate trust policy without resolving credentials."""
        document = self._load()
        profile = document.profiles.get(profile_name)
        if profile is None:
            raise SqlCtxError(
                "PROFILE_NOT_FOUND", f"Unknown connection profile: {profile_name}", status_code=404
            )
        if profile.engine != DatabaseEngine.SQLSERVER:
            raise SqlCtxError(
                "PROFILE_TRUST_POLICY_UNSUPPORTED",
                "Server-certificate trust policy is supported only for SQL Server profiles.",
            )
        updated = profile.model_copy(update={"trust_server_certificate": enabled})
        merged = dict(document.profiles)
        merged[profile_name] = updated
        payload = ProfilesDocument(profiles=merged).model_dump(mode="json", exclude_none=True)
        _atomic_write(
            self.path,
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode(),
        )

    def set_write_scopes(
        self,
        profile_name: str,
        *,
        metadata_context_write: bool,
        routine_write: bool,
    ) -> None:
        """Persist explicit SQL Server write scopes without touching credentials."""
        document = self._load()
        profile = document.profiles.get(profile_name)
        if profile is None:
            raise SqlCtxError(
                "PROFILE_NOT_FOUND", f"Unknown connection profile: {profile_name}", status_code=404
            )
        updated = ProfileDefinition.model_validate(
            profile.model_copy(
                update={
                    "metadata_context_write": metadata_context_write,
                    "routine_write": routine_write,
                }
            ).model_dump()
        )
        merged = dict(document.profiles)
        merged[profile_name] = updated
        payload = ProfilesDocument(profiles=merged).model_dump(mode="json", exclude_none=True)
        _atomic_write(
            self.path,
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode(),
        )

    def set_schema_policy(
        self,
        profile_name: str,
        *,
        allowed_schemas: list[str],
        excluded_object_patterns: list[str],
        allowed_object_types: list[ObjectType] | None = None,
    ) -> None:
        """Persist an explicit metadata scope without resolving or rewriting credentials."""
        document = self._load()
        profile = document.profiles.get(profile_name)
        if profile is None:
            raise SqlCtxError(
                "PROFILE_NOT_FOUND", f"Unknown connection profile: {profile_name}", status_code=404
            )
        update: dict[str, Any] = {
            "allowed_schemas": allowed_schemas,
            "excluded_object_patterns": excluded_object_patterns,
        }
        if allowed_object_types is not None:
            update["allowed_object_types"] = allowed_object_types
        updated = profile.model_copy(update=update)
        updated = ProfileDefinition.model_validate(updated.model_dump())
        merged = dict(document.profiles)
        merged[profile_name] = updated
        payload = ProfilesDocument(profiles=merged).model_dump(mode="json", exclude_none=True)
        _atomic_write(
            self.path,
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode(),
        )

    def remove(self, profile_name: str, *, remove_credentials: bool = True) -> dict[str, Any]:
        """Remove one profile definition and its unshared protected credential reference."""
        document = self._load()
        profile = document.profiles.get(profile_name)
        if profile is None:
            raise SqlCtxError(
                "PROFILE_NOT_FOUND", f"Unknown connection profile: {profile_name}", status_code=404
            )
        remaining = dict(document.profiles)
        removed = remaining.pop(profile_name)
        payload = ProfilesDocument(profiles=remaining).model_dump(mode="json", exclude_none=True)
        _atomic_write(
            self.path,
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode(),
        )
        credential_removed = False
        credential_preserved_reason = None
        if remove_credentials and removed.credential_ref:
            still_used = any(
                item.credential_ref == removed.credential_ref for item in remaining.values()
            )
            if still_used:
                credential_preserved_reason = "credential_ref_shared_by_another_profile"
            else:
                credential_removed = self.credentials.delete(removed.credential_ref)
        return {
            "profile": profile_name,
            "removed": True,
            "credential_ref": removed.credential_ref,
            "credential_removed": credential_removed,
            "credential_preserved_reason": credential_preserved_reason,
        }
