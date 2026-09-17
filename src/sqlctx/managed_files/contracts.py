"""Strict contracts for registered-folder SQL classification."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.models import PublicModel

_SAFE_TERM = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class RegisteredFolder(PublicModel):
    folder_id: str
    engine: DatabaseEngine
    input_root: str
    output_root: str
    managed_root: str | None = None
    created_at: datetime


class RegisteredFolderDescriptor(PublicModel):
    folder_id: str
    engine: DatabaseEngine
    separate_output: bool


class RegisteredFolderList(PublicModel):
    items: list[RegisteredFolderDescriptor]


class OwnerFileResolution(PublicModel):
    relative_path: str
    context: str
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("relative_path")
    @classmethod
    def relative_only(cls, value: str) -> str:
        if (
            not value
            or value.startswith(("/", "\\"))
            or ".." in value.replace("\\", "/").split("/")
        ):
            raise ValueError("relative_path must stay below the registered root")
        return value.replace("\\", "/")


class FolderClassificationPlanRequest(PublicModel):
    folder_id: str
    resolutions: list[OwnerFileResolution] = Field(default_factory=list, max_length=500)
    in_place: bool = False


class ManagedFileResolutionPlanRequest(PublicModel):
    """Owner-confirm one existing managed file through an immutable move/header plan."""

    folder_id: str
    managed_relative_path: str
    context: str
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("managed_relative_path")
    @classmethod
    def managed_relative_only(cls, value: str) -> str:
        if (
            not value
            or value.startswith(("/", "\\"))
            or ".." in value.replace("\\", "/").split("/")
            or not value.lower().endswith(".sql")
        ):
            raise ValueError("managed_relative_path must name one SQL file below the managed root")
        return value.replace("\\", "/")

    @field_validator("context")
    @classmethod
    def safe_context(cls, value: str) -> str:
        if not _SAFE_TERM.fullmatch(value):
            raise ValueError("context must be one safe lowercase value")
        return value

    @field_validator("tags")
    @classmethod
    def safe_tags(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)) or any(not _SAFE_TERM.fullmatch(tag) for tag in value):
            raise ValueError("tags must be sorted, unique, safe lowercase values")
        return value


class FolderPlanItem(PublicModel):
    source_relative_path: str
    destination_relative_path: str
    object_id: str
    object_type: ObjectType
    content_hash: str
    classification_status: Literal["confirmed", "unresolved"]
    context: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FolderClassificationPlan(PublicModel):
    plan_id: str
    folder_id: str
    engine: DatabaseEngine
    in_place: bool
    created_at: datetime
    expires_at: datetime
    plan_hash: str
    items: list[FolderPlanItem]


class FolderApplyResult(PublicModel):
    plan_id: str
    written: int = Field(ge=0)
    moved: int = Field(ge=0)
    manifest_relative_path: str
    inventory_hash: str
