"""Database-specific read-only adapters."""

from sqlctx.adapters.registry import create_adapter, dialect_map

__all__ = ["create_adapter", "dialect_map"]
