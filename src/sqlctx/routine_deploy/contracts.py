"""Strict contracts for immutable routine deployment plans."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.models import PublicModel


class RoutinePlanRequest(PublicModel):
    profile: str
    folder_id: str
    relative_path: str | None = None
    stop_on_error: bool = True
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("relative_path")
    @classmethod
    def safe_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("relative_path must stay below the registered root")
        return normalized


class RoutinePlanItem(PublicModel):
    relative_path: str
    object_id: str
    object_type: ObjectType
    schema_name: str
    object_name: str
    content_hash: str
    statement_hash: str
    database_fingerprint: str | None = None
    database_fingerprint_checked: bool = False


class RoutineDeploymentPlan(PublicModel):
    plan_id: str
    profile: str
    folder_id: str
    engine: DatabaseEngine
    requester: str
    stop_on_error: bool
    created_at: datetime
    expires_at: datetime
    plan_hash: str
    items: list[RoutinePlanItem]


class RoutineApplyItemResult(PublicModel):
    object_id: str
    relative_path: str
    status: Literal["applied", "failed", "skipped"]
    error_code: str | None = None


class RoutineApplyResult(PublicModel):
    plan_id: str
    applied: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    items: list[RoutineApplyItemResult]
