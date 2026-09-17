"""Pure value-shaping rules shared without catalog/export workflow coupling."""

from __future__ import annotations

import json
from typing import Any


def bounded_text_value(value: Any, *, column_name: str = "", data_type: str = "") -> Any:
    """Preserve the established v1 context placeholder behavior exactly."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[BINARY {len(value)} BYTES]"
    if isinstance(value, str):
        encoded_size = len(value.encode("utf-8"))
        normalized_name = column_name.lower()
        normalized_type = data_type.lower().replace(" ", "")
        payload_like = "payload" in normalized_name
        large_type = normalized_type in {
            "json",
            "jsonb",
            "text",
            "ntext",
            "clob",
            "nvarchar(max)",
            "varchar(max)",
        }
        if payload_like or large_type or len(value) > 200:
            try:
                json.loads(value)
                return f"...json string payload...({encoded_size} bytes)..."
            except (json.JSONDecodeError, TypeError):
                if len(value) > 200 or payload_like:
                    return f"...long text payload...({encoded_size} bytes)..."
    return value
