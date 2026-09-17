"""One-file-at-a-time, fail-isolated SQLFluff formatting."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from sqlctx.core.enums import FormatStatus
from sqlctx.core.models import HostPythonToolingDescriptor, SqlFormatResult
from sqlctx.formatting.manager import Runner, SqlFluffManager, _default_runner


def _sanitized_diagnostics(result: subprocess.CompletedProcess[str]) -> list[str]:
    text = (result.stderr or result.stdout or "").strip()
    text = re.sub(r"(?i)(password|secret|token|key)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    text = text.replace("\r", " ").replace("\n", " ")
    return [text[:512]] if text else []


class SqlFluffFormatter:
    def __init__(self, manager: SqlFluffManager, runner: Runner = _default_runner) -> None:
        self.manager = manager
        self.runner = runner

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner([str(self.manager.python_executable), *arguments])

    def format_one(
        self,
        *,
        object_id: str,
        sql: str,
        dialect: str,
        tooling: HostPythonToolingDescriptor | None = None,
    ) -> SqlFormatResult:
        descriptor = tooling or self.manager.status()
        fingerprint = descriptor.tooling_fingerprint or "unavailable"
        version = descriptor.sqlfluff_version or self.manager.pinned_version
        cache_payload = {
            "sql": sql,
            "dialect": dialect,
            "tooling_fingerprint": fingerprint,
            "policy": "parse-format-verify-v1",
        }
        cache_key = hashlib.sha256(
            json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self.manager.state.read_json(f"format-cache/{cache_key}.json")
        if isinstance(cached, dict):
            result = SqlFormatResult.model_validate(cached)
            return result.model_copy(update={"object_id": object_id, "cache_hit": True})

        def finish(result: SqlFormatResult) -> SqlFormatResult:
            self.manager.state.write_json(
                f"format-cache/{cache_key}.json", result.model_dump(mode="json")
            )
            return result

        with tempfile.TemporaryDirectory(prefix="sqlctx-format-") as temporary:
            sql_path = Path(temporary) / "object.sql"
            sql_path.write_text(sql, encoding="utf-8", newline="\n")
            parse = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "parse",
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            if parse.returncode != 0:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.PARSE_FAILED,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(parse),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            formatted = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "format",
                    "--exclude-rules",
                    "CP02,LT01,RF06",
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            if formatted.returncode != 0:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.FORMAT_FAILED,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(formatted),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            candidate = sql_path.read_text(encoding="utf-8")
            verify = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "parse",
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            if verify.returncode != 0:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.ROLLED_BACK,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(verify),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            return finish(
                SqlFormatResult(
                    object_id=object_id,
                    status=FormatStatus.FORMATTED,
                    content=candidate,
                    sqlfluff_version=version,
                    tooling_fingerprint=fingerprint,
                )
            )
