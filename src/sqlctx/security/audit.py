"""Sanitized owner-local operation audit events for HTTP and MCP activity."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Literal

from sqlctx.security.runtime import JsonRuntimeStateStore

_LOGGER = logging.getLogger("sqlctx.audit")
_LOGGER.setLevel(logging.INFO)


class OperationAuditLogger:
    """Write one protected event per operation without arguments or credential values."""

    def __init__(self, state: JsonRuntimeStateStore) -> None:
        self.state = state

    def record(
        self,
        *,
        transport: Literal["http", "mcp", "service"],
        caller: str,
        operation: str,
        outcome: Literal["succeeded", "failed", "skipped"],
        duration_ms: int,
        error_code: str | None = None,
        value_mode: Literal["short", "full"] | None = None,
        returned_row_count: int | None = None,
        truncated: bool | None = None,
        object_identity_hash: str | None = None,
    ) -> None:
        occurred_at = datetime.now(UTC)
        event_id = "evt_" + secrets.token_urlsafe(12)
        event = {
            "event_id": event_id,
            "occurred_at": occurred_at.isoformat(),
            "transport": transport,
            "caller": caller,
            "operation": operation,
            "outcome": outcome,
            "duration_ms": max(duration_ms, 0),
            "error_code": error_code,
        }
        if object_identity_hash is not None:
            event["object_identity_hash"] = object_identity_hash
        if operation in {"query.data", "sqlctx_query_data"}:
            event.update(
                {
                    "value_mode": value_mode,
                    "returned_row_count": max(returned_row_count or 0, 0),
                    "truncated": bool(truncated),
                }
            )
        relative = f"audit/events/{occurred_at:%Y/%m/%d}/{event_id}.json"
        self.state.write_json(relative, event)
        _LOGGER.info("sqlctx_operation %s", json.dumps(event, sort_keys=True))
