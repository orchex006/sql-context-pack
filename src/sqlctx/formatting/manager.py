"""Pinned SQLFluff lifecycle on one owner-selected host interpreter."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from sqlctx._version import SQLFLUFF_VERSION
from sqlctx.core.errors import ApprovalRequired, SqlCtxError, ToolingUnavailable
from sqlctx.core.models import HostPythonToolingDescriptor
from sqlctx.security.runtime import JsonRuntimeStateStore

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    # The selected absolute Python and closed internal arguments assemble every command.
    return subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=False, timeout=120
    )


class CrossProcessLock:
    def __init__(self, path: Path, timeout_seconds: float = 30.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.fd: int | None = None

    def __enter__(self) -> CrossProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise SqlCtxError(
                        "TOOLING_LOCK_TIMEOUT",
                        "Timed out waiting for the SQLFluff installation lock.",
                        retryable=True,
                        status_code=409,
                    ) from None
                time.sleep(0.05)

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


class SqlFluffManager:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        *,
        python_executable: Path | None = None,
        environment_owner: str = "host",
        runner: Runner = _default_runner,
        pinned_version: str = SQLFLUFF_VERSION,
    ) -> None:
        self.state = state
        configured = python_executable or Path(sys.executable)
        self.python_executable = configured.expanduser().resolve(strict=True)
        self.environment_owner = (
            "owner"
            if os.environ.get("SQLCTX_SERVICE_ACCOUNT") == "NT SERVICE\\SQLContextPack"
            else environment_owner
        )
        self.runner = runner
        self.pinned_version = pinned_version
        self._jobs_lock = Lock()
        self._active_jobs = 0
        self._validate_python()

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.runner([str(self.python_executable), *arguments])

    def _validate_python(self) -> None:
        result = self._run(
            "-c",
            "import json,sys; print(json.dumps(list(sys.version_info[:3])))",
        )
        if result.returncode != 0:
            raise ToolingUnavailable(
                "The configured host Python executable could not run.", code="PYTHON_UNAVAILABLE"
            )
        try:
            version = tuple(json.loads(result.stdout.strip().splitlines()[-1]))
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise ToolingUnavailable(
                "The configured host Python version is unreadable.", code="PYTHON_UNAVAILABLE"
            ) from exc
        if version < (3, 11, 0):
            raise ToolingUnavailable("Python >=3.11 is required.", code="PYTHON_UNAVAILABLE")
        self.python_version = ".".join(str(part) for part in version)

    @property
    def executable_fingerprint(self) -> str:
        payload = f"{self.python_executable}|{self.python_version}".encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def installed_version(self) -> str | None:
        result = self._run("-m", "sqlfluff", "version")
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        match = next(
            (token for token in text.replace(",", " ").split() if token[0:1].isdigit()), None
        )
        return match

    def tooling_fingerprint(self, version: str | None) -> str | None:
        if version is None:
            return None
        payload = f"{self.executable_fingerprint}|{version}|CP02,LT01,RF06|raw".encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def status(self) -> HostPythonToolingDescriptor:
        installed = self.installed_version()
        return HostPythonToolingDescriptor(
            python_executable_fingerprint=self.executable_fingerprint,
            python_version=self.python_version,
            environment_owner="owner" if self.environment_owner == "owner" else "host",
            sqlfluff_version=installed,
            tooling_fingerprint=self.tooling_fingerprint(installed),
            ready=installed == self.pinned_version,
            update_blocked_by_active_jobs=self.active_jobs > 0,
        )

    def ensure(self, *, approved: bool = False) -> HostPythonToolingDescriptor:
        current = self.status()
        if current.ready:
            return current
        if self.environment_owner == "owner":
            raise ToolingUnavailable(
                "The owner-selected environment is verify/execute-only; install the pinned SQLFluff manually.",
                code="OWNER_MANAGED_PYTHON_ENVIRONMENT",
            )
        if not approved:
            raise ApprovalRequired()
        with CrossProcessLock(self.state._safe("locks/sqlfluff-install.lock")):
            current = self.status()
            if current.ready:
                return current
            result = self._run(
                "-m",
                "pip",
                "install",
                "--user",
                f"sqlfluff=={self.pinned_version}",
            )
            if result.returncode != 0:
                raise ToolingUnavailable("Pinned SQLFluff installation failed under host policy.")
            verified = self.status()
            if not verified.ready:
                raise ToolingUnavailable("SQLFluff verification failed after installation.")
            self._write_state(verified)
            return verified

    def update(self, version: str, *, approved: bool = False) -> HostPythonToolingDescriptor:
        if not approved:
            raise ApprovalRequired()
        if self.active_jobs:
            raise SqlCtxError(
                "TOOLING_BUSY",
                "SQLFluff cannot be updated while export or formatting work is active.",
                retryable=True,
                status_code=409,
            )
        if self.environment_owner == "owner":
            raise ToolingUnavailable(
                "The owner-selected environment is verify/execute-only; update SQLFluff manually.",
                code="OWNER_MANAGED_PYTHON_ENVIRONMENT",
            )
        requested = self.pinned_version if version == "latest-stable" else version
        if not requested or any(char not in "0123456789." for char in requested):
            raise SqlCtxError(
                "INVALID_SQLFLUFF_VERSION", "SQLFluff version must be an exact stable version."
            )
        with CrossProcessLock(self.state._safe("locks/sqlfluff-install.lock")):
            previous = self.installed_version()
            result = self._run(
                "-m",
                "pip",
                "install",
                "--user",
                "--upgrade",
                f"sqlfluff=={requested}",
            )
            if result.returncode == 0 and self._self_test(requested):
                status = self.status()
                self._write_state(status)
                return status
            if previous:
                rollback = self._run(
                    "-m", "pip", "install", "--user", "--upgrade", f"sqlfluff=={previous}"
                )
                if rollback.returncode == 0 and self._self_test(previous):
                    raise ToolingUnavailable(
                        "SQLFluff update failed; the previous version was restored."
                    )
            raise ToolingUnavailable(
                "SQLFluff update and rollback both failed; manual repair is required.",
                code="TOOLING_BROKEN",
            )

    def _self_test(self, version: str) -> bool:
        return (
            self.installed_version() == version
            and self._run("-m", "sqlfluff", "dialects").returncode == 0
        )

    def _write_state(self, descriptor: HostPythonToolingDescriptor) -> None:
        self.state.write_json("tooling/sqlfluff.json", descriptor.model_dump(mode="json"))

    @property
    def active_jobs(self) -> int:
        with self._jobs_lock:
            return self._active_jobs

    @contextmanager
    def pin_for_job(self) -> Iterator[HostPythonToolingDescriptor]:
        descriptor = self.status()
        if not descriptor.ready:
            raise ToolingUnavailable("Pinned SQLFluff is not available.")
        with self._jobs_lock:
            self._active_jobs += 1
        try:
            yield descriptor
            if self.status().tooling_fingerprint != descriptor.tooling_fingerprint:
                raise SqlCtxError(
                    "TOOLING_CHANGED",
                    "SQLFluff changed while an export was running.",
                    status_code=409,
                )
        finally:
            with self._jobs_lock:
                self._active_jobs -= 1
