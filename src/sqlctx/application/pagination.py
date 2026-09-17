"""Opaque bounded cursor pagination."""

from __future__ import annotations

import base64
from typing import TypeVar

from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import PageInfo

T = TypeVar("T")


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"v1:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        prefix, offset = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        value = int(offset)
        if prefix != "v1" or value < 0:
            raise ValueError
        return value
    except (ValueError, UnicodeDecodeError) as exc:
        raise SqlCtxError("INVALID_CURSOR", "Pagination cursor is invalid.") from exc


def page_slice(items: list[T], *, cursor: str | None, limit: int) -> tuple[list[T], PageInfo]:
    if limit < 1 or limit > 250:
        raise SqlCtxError("INVALID_PAGE_LIMIT", "Page limit must be between 1 and 250.")
    offset = decode_cursor(cursor)
    selected = items[offset : offset + limit]
    next_offset = offset + len(selected)
    next_cursor = encode_cursor(next_offset) if next_offset < len(items) else None
    return selected, PageInfo(limit=limit, returned=len(selected), next_cursor=next_cursor)
