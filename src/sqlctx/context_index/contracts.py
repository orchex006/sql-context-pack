"""Strict transport-safe contracts for ``DB_METADATA_CONTEXT``."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from sqlctx.core.enums import ObjectType
from sqlctx.core.models import PageInfo, PublicModel

_SAFE_TERM = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContextIndexEntry(PublicModel):
    """One canonical TABLE, PROCEDURE, or FUNCTION context record."""

    schema_name: str = Field(min_length=1, max_length=128)
    object_name: str = Field(min_length=1, max_length=256)
    object_type: ObjectType
    context: str | None = None
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=64)
    classification_status: Literal["confirmed", "unresolved"]
    classification_source: Literal["owner", "rule", "unknown"]
    source_fingerprint: str | None = None
    content_hash: str | None = None
    managed_relative_path: str | None = Field(default=None, max_length=1_024)
    header_version: Literal[1] = 1
    output_format_version: Literal["2"] = "2"
    evidence: list[str] = Field(default_factory=list, max_length=64)
    active: bool = True
    last_classified_at: datetime | None = None
    last_generated_at: datetime | None = None

    @field_validator("schema_name", "object_name")
    @classmethod
    def safe_identity(cls, value: str) -> str:
        if any(char in value for char in "\x00\r\n"):
            raise ValueError("object identity contains control characters")
        return value

    @field_validator("context")
    @classmethod
    def safe_context(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_TERM.fullmatch(value):
            raise ValueError("context must be one safe lowercase value")
        return value

    @field_validator("tags")
    @classmethod
    def safe_tags(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if normalized != value or any(not _SAFE_TERM.fullmatch(tag) for tag in value):
            raise ValueError("tags must be sorted, unique, safe lowercase values")
        return value

    @field_validator("source_fingerprint", "content_hash")
    @classmethod
    def safe_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("hashes must use canonical lowercase sha256 values")
        return value

    @field_validator("evidence")
    @classmethod
    def safe_evidence(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)) or any(
            not item or any(char in item for char in "\x00\r\n") for item in value
        ):
            raise ValueError("evidence must be sorted, unique, and single-line")
        return value

    @model_validator(mode="after")
    def coherent_classification(self) -> ContextIndexEntry:
        if self.classification_status == "unresolved":
            if self.context is not None or self.tags or self.classification_source != "unknown":
                raise ValueError("unresolved records cannot contain guessed context or tags")
        elif self.context is None or self.classification_source == "unknown":
            raise ValueError("confirmed records require context and a known source")
        return self


class ContextIndexListRequest(PublicModel):
    profile: str
    context: str | None = None
    object_type: ObjectType | None = None
    tag: str | None = None
    status: Literal["confirmed", "unresolved"] | None = None
    active: bool = True
    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=250)

    @field_validator("context", "tag")
    @classmethod
    def safe_filter(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_TERM.fullmatch(value):
            raise ValueError("context and tag filters must be safe lowercase values")
        return value


class ContextIndexPage(PublicModel):
    items: list[ContextIndexEntry]
    page: PageInfo


class ContextIndexSyncRequest(PublicModel):
    profile: str
    entries: list[ContextIndexEntry] = Field(max_length=5_000)
    actor_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    complete_catalog_id: str | None = None
    # ``inventory`` records machine-derived identity and technical fields for every
    # discovered object and never touches an owner-confirmed classification, so it runs
    # unattended. ``owner`` carries supervised classifications or deactivation and stays
    # approval-gated. Defaulting to ``inventory`` is what lets an export populate the
    # index on its own instead of leaving it at a handful of hand-resolved rows.
    mode: Literal["inventory", "owner"] = "inventory"

    @model_validator(mode="after")
    def unique_object_identities(self) -> ContextIndexSyncRequest:
        if not self.entries and self.complete_catalog_id is None:
            raise ValueError("empty entries require a verified complete catalog")
        if self.mode == "inventory":
            if self.complete_catalog_id is not None:
                raise ValueError("deactivation requires owner mode")
            if any(entry.classification_source == "owner" for entry in self.entries):
                raise ValueError("owner-sourced classifications require owner mode")
            if any(not entry.active for entry in self.entries):
                raise ValueError("deactivating an entry requires owner mode")
        identities = [
            (entry.schema_name.casefold(), entry.object_type, entry.object_name.casefold())
            for entry in self.entries
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("entries must contain unique object identities")
        return self


class ContextIndexSyncResult(PublicModel):
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    owner_values_preserved: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    deactivated: int = Field(ge=0)


class ContextGenerationRequest(PublicModel):
    profile: str
    folder_id: str
    context: str | None = None
    object_type: ObjectType | None = None
    tag: str | None = None
    include_unresolved: bool = False
    limit: int = Field(default=100, ge=1, le=250)

    @field_validator("context", "tag")
    @classmethod
    def safe_generation_filter(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_TERM.fullmatch(value):
            raise ValueError("generation filters must be safe lowercase values")
        return value


class ContextGenerationItem(PublicModel):
    object_id: str
    object_type: ObjectType
    context: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    classification_status: Literal["confirmed", "unresolved"]
    classification_source: Literal["owner", "rule", "unknown"]
    source_fingerprint: str | None = None
    evidence: list[str] = Field(default_factory=list)
    header_version: Literal[1] = 1
    output_format_version: Literal["2"] = "2"
    managed_relative_path: str
    content_hash: str


class ContextGenerationPlan(PublicModel):
    plan_id: str
    profile: str
    folder_id: str
    plan_hash: str
    items: list[ContextGenerationItem]
