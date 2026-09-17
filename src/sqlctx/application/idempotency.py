"""Caller- and operation-scoped idempotency records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlctx.core.errors import SqlCtxError
from sqlctx.security.runtime import JsonRuntimeStateStore

T = TypeVar("T")


class IdempotencyService:
    def __init__(self, state: JsonRuntimeStateStore, ttl_hours: int = 24) -> None:
        self.state = state
        self.ttl = timedelta(hours=ttl_hours)

    @staticmethod
    def _validate_key(key: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._~-]{8,128}", key):
            raise SqlCtxError(
                "IDEMPOTENCY_KEY_INVALID",
                "Idempotency key must be 8-128 safe non-secret characters.",
            )

    @staticmethod
    def _digest(request: Any) -> str:
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def execute(
        self,
        *,
        caller: str,
        operation: str,
        key: str,
        normalized_request: Any,
        create: Callable[[], T],
        serialize: Callable[[T], Any],
        validate: Callable[[Any], T],
    ) -> tuple[T, bool]:
        self._validate_key(key)
        scope = hashlib.sha256(f"{caller}\0{operation}\0{key}".encode()).hexdigest()
        path = f"idempotency/{scope}.json"
        digest = self._digest(normalized_request)
        existing = self.state.read_json(path)
        if existing and datetime.fromisoformat(existing["expires_at"]) > datetime.now(UTC):
            if existing["request_digest"] != digest:
                raise SqlCtxError(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency key was reused with a different request.",
                    status_code=409,
                )
            return validate(existing["response"]), True
        result = create()
        self.state.write_json(
            path,
            {
                "caller": caller,
                "operation": operation,
                "request_digest": digest,
                "response": serialize(result),
                "expires_at": (datetime.now(UTC) + self.ttl).isoformat(),
            },
        )
        return result, False
