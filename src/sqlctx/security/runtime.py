"""Protected runtime state, credential metadata, and encrypted snapshot secrets."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from sqlctx.core.errors import SqlCtxError
from sqlctx.security.transport import local_mcp_url


def default_runtime_dir() -> Path:
    configured = os.environ.get("SQLCTX_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            managed_root = Path(program_data) / "SQLContextPack"
            if (managed_root / "service-config.json").is_file():
                return managed_root / "runtime"
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".local/state"))
    return base / "sql-context-pack"


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        os.chmod(path, mode)
        _harden_windows_acl(path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _harden_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    owner_account = os.environ.get("SQLCTX_OWNER_ACCOUNT") or os.environ.get("USERNAME")
    if not owner_account:
        raise SqlCtxError("RUNTIME_PERMISSION_FAILED", "Cannot determine the current Windows user.")
    icacls = shutil.which("icacls")
    if icacls is None:
        raise SqlCtxError("RUNTIME_PERMISSION_FAILED", "Windows ACL tooling is unavailable.")
    grants = [] if owner_account.upper() == "SYSTEM" else [f"{owner_account}:(R,W)"]
    grants.append("SYSTEM:(F)")
    grants.append("*S-1-5-32-544:(F)")
    program_data = os.environ.get("PROGRAMDATA")
    service_account = os.environ.get("SQLCTX_SERVICE_ACCOUNT")
    if not service_account and program_data:
        config_path = Path(program_data) / "SQLContextPack/service-config.json"
        if config_path.is_file():
            try:
                service_account = json.loads(config_path.read_text(encoding="utf-8-sig")).get(
                    "service_account"
                )
            except (OSError, ValueError, AttributeError):
                service_account = None
    if service_account == "NT SERVICE\\SQLContextPack":
        grants.append(f"{service_account}:(F)")
    result = subprocess.run(  # noqa: S603 - closed ACL command without a shell.
        [icacls, str(path), "/inheritance:r", "/grant:r", *grants],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SqlCtxError("RUNTIME_PERMISSION_FAILED", "Could not enforce owner-only runtime ACL.")


class JsonRuntimeStateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_runtime_dir()).resolve()

    def write_json(self, relative: str, value: Any) -> Path:
        path = self._safe(relative)
        _atomic_write(path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        return path

    def read_json(self, relative: str, default: Any = None) -> Any:
        path = self._safe(relative)
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SqlCtxError(
                "RUNTIME_STATE_CORRUPT", "Protected runtime state is unreadable."
            ) from exc

    def _safe(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise SqlCtxError(
                "UNSAFE_RUNTIME_PATH", "Runtime path escaped its protected root."
            ) from exc
        return candidate


@contextmanager
def exclusive_runtime_lock(
    state: JsonRuntimeStateStore,
    name: str,
    *,
    conflict_code: str,
    conflict_message: str,
) -> Iterator[None]:
    """Hold one non-blocking cross-process byte-range lock under protected runtime state."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise SqlCtxError("INVALID_RUNTIME_LOCK", "Runtime lock name is invalid.")
    path = state._safe(f"locks/{name}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                msvcrt: Any = import_module("msvcrt")

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl: Any = import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise SqlCtxError(conflict_code, conflict_message, status_code=409) from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt = import_module("msvcrt")

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class CredentialMetadataStore:
    def __init__(self, state: JsonRuntimeStateStore) -> None:
        self.state = state

    def ensure(self, mcp_url: str) -> tuple[Path, Path]:
        mcp_url = local_mcp_url(mcp_url)
        agent_path = self.state._safe("connection-metadata.json")
        owner_path = self.state._safe("owner-control.json")
        if not agent_path.exists():
            self.state.write_json(
                "connection-metadata.json",
                {
                    "mcp_url": mcp_url,
                    "agent_token": secrets.token_urlsafe(48),
                    "scope": "agent",
                },
            )
        else:
            agent_metadata = self.state.read_json("connection-metadata.json")
            if not isinstance(agent_metadata, dict) or not agent_metadata.get("agent_token"):
                raise SqlCtxError(
                    "RUNTIME_STATE_CORRUPT", "Protected connection metadata is invalid."
                )
            if agent_metadata.get("mcp_url") != mcp_url:
                self.state.write_json(
                    "connection-metadata.json", {**agent_metadata, "mcp_url": mcp_url}
                )
        if not owner_path.exists():
            self.state.write_json(
                "owner-control.json",
                {"owner_credential": secrets.token_urlsafe(64), "scope": "owner"},
            )
        return agent_path, owner_path


class EncryptedSnapshotSecretStore:
    def __init__(self, state: JsonRuntimeStateStore) -> None:
        self.state = state
        self.master_key_path = state._safe("master.key")

    def _cipher(self) -> Fernet:
        if not self.master_key_path.exists():
            _atomic_write(self.master_key_path, Fernet.generate_key())
        try:
            return Fernet(self.master_key_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise SqlCtxError(
                "SECURE_RESUME_UNAVAILABLE", "Snapshot key protection is unavailable."
            ) from exc

    def get_or_create_key(self, snapshot_id: str) -> bytes:
        relative = f"snapshots/{snapshot_id}/masking-key.enc"
        path = self.state._safe(relative)
        cipher = self._cipher()
        if path.exists():
            try:
                return cipher.decrypt(path.read_bytes())
            except (OSError, InvalidToken) as exc:
                raise SqlCtxError(
                    "SECURE_RESUME_UNAVAILABLE", "The protected snapshot masking key is unreadable."
                ) from exc
        key = secrets.token_bytes(32)
        _atomic_write(path, cipher.encrypt(key))
        return key

    def load_registry(self, snapshot_id: str) -> dict[str, str]:
        value = self.state.read_json(f"snapshots/{snapshot_id}/alias-registry.json", {})
        if not isinstance(value, dict):
            raise SqlCtxError("RUNTIME_STATE_CORRUPT", "The alias registry is invalid.")
        return {str(key): str(alias) for key, alias in value.items()}

    def save_registry(self, snapshot_id: str, registry: dict[str, str]) -> None:
        self.state.write_json(f"snapshots/{snapshot_id}/alias-registry.json", registry)


class EncryptedProfileCredentialStore:
    """Owner-only encrypted connection values referenced by a safe profile name."""

    _REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
    _FIELDS = ("host", "database", "username", "password")

    def __init__(self, state: JsonRuntimeStateStore | None = None) -> None:
        self.state = state or JsonRuntimeStateStore()
        self.master_key_path = self.state._safe("profile-credentials/master.key")

    def _relative(self, reference: str) -> str:
        if not self._REFERENCE.fullmatch(reference):
            raise SqlCtxError(
                "INVALID_PROFILE_REFERENCE",
                "Profile credential reference contains unsupported characters.",
            )
        return f"profile-credentials/{reference}.enc"

    def _cipher(self) -> Fernet:
        if not self.master_key_path.exists():
            _atomic_write(self.master_key_path, Fernet.generate_key())
        try:
            return Fernet(self.master_key_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise SqlCtxError(
                "PROFILE_CREDENTIAL_STORE_UNAVAILABLE",
                "Protected profile credential storage is unavailable.",
            ) from exc

    def put(self, reference: str, values: dict[str, str]) -> Path:
        if set(values) != set(self._FIELDS) or any(not values[field] for field in self._FIELDS):
            raise SqlCtxError(
                "INVALID_PROFILE_CREDENTIAL",
                "Every protected connection value is required.",
            )
        payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        target = self.state._safe(self._relative(reference))
        _atomic_write(target, self._cipher().encrypt(payload))
        return target

    def exists(self, reference: str) -> bool:
        return self.state._safe(self._relative(reference)).is_file()

    def delete(self, reference: str) -> bool:
        path = self.state._safe(self._relative(reference))
        if not path.is_file():
            return False
        path.unlink()
        return True

    def get(self, reference: str) -> dict[str, str]:
        path = self.state._safe(self._relative(reference))
        if not path.is_file():
            raise SqlCtxError(
                "PROFILE_NOT_READY",
                "The profile has no protected owner credential record.",
                status_code=503,
            )
        try:
            raw = self._cipher().decrypt(path.read_bytes())
            values = json.loads(raw)
            if not isinstance(values, dict):
                raise ValueError
            normalized = {field: str(values[field]) for field in self._FIELDS}
            if any(not value for value in normalized.values()):
                raise ValueError
            return normalized
        except (OSError, InvalidToken, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise SqlCtxError(
                "PROFILE_CREDENTIAL_STORE_UNAVAILABLE",
                "The protected profile credential record is unreadable.",
            ) from exc
