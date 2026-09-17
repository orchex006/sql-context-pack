"""Request-bound, single-use owner approval challenges."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from sqlctx.core.errors import ApprovalRequired, SqlCtxError
from sqlctx.core.models import ApprovalChallenge
from sqlctx.security.runtime import JsonRuntimeStateStore


class ApprovalService:
    def __init__(self, ttl_seconds: int = 300, state: JsonRuntimeStateStore | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self.state = state
        self._lock = Lock()
        self._records: dict[str, dict[str, Any]] = self._load_records()

    def _load_records(self) -> dict[str, dict[str, Any]]:
        if self.state is None:
            return {}
        value = self.state.read_json("approvals/records.json", {})
        return value if isinstance(value, dict) else {}

    def _refresh(self) -> None:
        if self.state is not None:
            self._records = self._load_records()

    def _save(self) -> None:
        if self.state is not None:
            self.state.write_json("approvals/records.json", self._records)

    @staticmethod
    def request_digest(payload: Any) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def challenge(
        self, *, caller: str, operation: str, target: str, payload: Any
    ) -> ApprovalChallenge:
        now = datetime.now(UTC)
        challenge = ApprovalChallenge(
            challenge_id="apr_" + secrets.token_urlsafe(18),
            request_digest=self.request_digest(payload),
            operation=operation,
            target=target,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        with self._lock:
            self._refresh()
            self._records[challenge.challenge_id] = {
                **challenge.model_dump(mode="json"),
                "caller": caller,
                "granted": False,
                "consumed": False,
            }
            self._save()
        return challenge

    def grant(self, challenge_id: str, *, interactive: bool) -> None:
        if not interactive:
            raise SqlCtxError(
                "OWNER_PRESENCE_REQUIRED",
                "Owner approval must be granted from an interactive local control session.",
                status_code=403,
            )
        with self._lock:
            self._refresh()
            record = self._records.get(challenge_id)
            if record is None:
                raise SqlCtxError(
                    "APPROVAL_NOT_FOUND",
                    "Approval challenge was not found. Run `sqlctx approvals list`.",
                    status_code=404,
                )
            if self._expired(record):
                raise SqlCtxError(
                    "APPROVAL_EXPIRED",
                    "Approval challenge expired. Retry the original operation to create a fresh challenge.",
                    status_code=403,
                    details={"expires_at": record["expires_at"]},
                )
            record["granted"] = True
            self._save()

    def consume(
        self,
        challenge_id: str,
        *,
        caller: str,
        operation: str,
        target: str,
        payload: Any,
    ) -> None:
        with self._lock:
            self._refresh()
            record = self._records.get(challenge_id)
            matches = record and all(
                (
                    record["caller"] == caller,
                    record["operation"] == operation,
                    record["target"] == target,
                    record["request_digest"] == self.request_digest(payload),
                    record["granted"],
                    not record["consumed"],
                    not self._expired(record),
                )
            )
            if not matches:
                raise ApprovalRequired()
            assert record is not None
            record["consumed"] = True
            self._save()

    def require(self, *, caller: str, operation: str, target: str, payload: Any) -> None:
        """Consume a matching grant or emit/reuse a request-bound challenge."""
        digest = self.request_digest(payload)
        with self._lock:
            self._refresh()
            matching = [
                record
                for record in self._records.values()
                if record["caller"] == caller
                and record["operation"] == operation
                and record["target"] == target
                and record["request_digest"] == digest
                and not record["consumed"]
                and not self._expired(record)
            ]
            granted = next((record for record in matching if record["granted"]), None)
            if granted is not None:
                granted["consumed"] = True
                self._save()
                return
            pending = matching[0] if matching else None
        if pending is None:
            challenge = self.challenge(
                caller=caller, operation=operation, target=target, payload=payload
            )
            details = challenge.model_dump(mode="json")
        else:
            details = {
                key: pending[key]
                for key in ("challenge_id", "request_digest", "operation", "target", "expires_at")
            }
        expires_at = datetime.fromisoformat(str(details["expires_at"]))
        details["expires_in_seconds"] = max(
            0, int((expires_at - datetime.now(UTC)).total_seconds())
        )
        details["owner_command"] = f"sqlctx approvals grant --challenge {details['challenge_id']}"
        details["owner_list_command"] = "sqlctx approvals list"
        raise ApprovalRequired({"approval": details})

    def list_challenges(self) -> list[dict[str, Any]]:
        """Return safe owner-facing approval state including expired records."""
        with self._lock:
            self._refresh()
            now = datetime.now(UTC)
            result = []
            for challenge_id, record in self._records.items():
                expires_at = datetime.fromisoformat(str(record["expires_at"]))
                status = (
                    "consumed"
                    if record["consumed"]
                    else "expired"
                    if expires_at <= now
                    else "granted"
                    if record["granted"]
                    else "pending"
                )
                result.append(
                    {
                        "challenge_id": challenge_id,
                        "operation": record["operation"],
                        "target": record["target"],
                        "expires_at": record["expires_at"],
                        "expires_in_seconds": max(0, int((expires_at - now).total_seconds())),
                        "status": status,
                        "owner_command": f"sqlctx approvals grant --challenge {challenge_id}",
                    }
                )
            return sorted(result, key=lambda item: str(item["expires_at"]), reverse=True)

    def cleanup_expired(self, *, retain_seconds: int = 86_400) -> list[str]:
        """Remove terminal challenge records after a bounded owner-visible retention window."""
        cutoff = datetime.now(UTC) - timedelta(seconds=retain_seconds)
        with self._lock:
            self._refresh()
            removed = [
                challenge_id
                for challenge_id, record in self._records.items()
                if datetime.fromisoformat(str(record["expires_at"])) <= cutoff
            ]
            for challenge_id in removed:
                del self._records[challenge_id]
            if removed:
                self._save()
            return sorted(removed)

    @staticmethod
    def _expired(record: dict[str, Any]) -> bool:
        expires = datetime.fromisoformat(str(record["expires_at"]))
        return expires <= datetime.now(UTC)
