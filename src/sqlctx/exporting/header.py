"""Versioned, dialect-safe metadata headers for managed SQL files."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sqlctx.core.enums import DatabaseEngine, ObjectType

HEADER_PREFIX = "-- sqlctx-context: "
_SAFE_VALUE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ManagedSqlHeader(BaseModel):
    """Strict metadata that can safely round-trip in a single SQL comment line."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    header_version: Literal[1] = 1
    object_id: str = Field(min_length=1, max_length=512)
    engine: DatabaseEngine
    schema_name: str = Field(min_length=1, max_length=128)
    object_name: str = Field(min_length=1, max_length=256)
    object_type: ObjectType
    context: str | None = None
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=64)
    evidence: list[str] = Field(default_factory=list, max_length=64)
    classification_status: Literal["confirmed", "unresolved"]
    classification_source: Literal["owner", "rule", "unknown"]
    source_fingerprint: str | None = None
    content_hash: str
    output_format_version: Literal["2"]

    @model_validator(mode="after")
    def validate_metadata(self) -> ManagedSqlHeader:
        expected_id = f"{self.object_type}:{self.schema_name}.{self.object_name}"
        if self.object_id != expected_id:
            raise ValueError("object_id does not match the canonical object identity")
        if self.context is not None and not _SAFE_VALUE.fullmatch(self.context):
            raise ValueError("context must be one safe lowercase path segment")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        if any(not _SAFE_VALUE.fullmatch(tag) for tag in self.tags):
            raise ValueError("tags must be safe lowercase values")
        if self.tags != sorted(self.tags):
            raise ValueError("tags must be sorted")
        if len(self.evidence) != len(set(self.evidence)) or self.evidence != sorted(self.evidence):
            raise ValueError("evidence identifiers must be sorted and unique")
        if any(not item or any(char in item for char in "\x00\r\n") for item in self.evidence):
            raise ValueError("evidence identifiers contain unsupported characters")
        for fingerprint in (self.source_fingerprint, self.content_hash):
            if fingerprint is not None and not _SHA256.fullmatch(fingerprint):
                raise ValueError("fingerprints must use canonical lowercase sha256 values")
        if self.classification_status == "unresolved":
            if self.context is not None or self.tags or self.classification_source != "unknown":
                raise ValueError("unresolved metadata cannot contain guessed context or tags")
        elif self.context is None or self.classification_source == "unknown":
            raise ValueError("confirmed metadata requires a context and evidence source")
        return self


def render_managed_sql(header: ManagedSqlHeader, body: str) -> str:
    payload = json.dumps(
        header.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{HEADER_PREFIX}{payload}\n{body}"


def parse_managed_sql(content: str) -> tuple[ManagedSqlHeader, str]:
    first_line, separator, body = content.partition("\n")
    if not separator or not first_line.startswith(HEADER_PREFIX):
        raise ValueError("MANAGED_SQL_HEADER_MISSING")
    try:
        payload = json.loads(first_line[len(HEADER_PREFIX) :])
        header = ManagedSqlHeader.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("MANAGED_SQL_HEADER_INVALID") from exc
    return header, body
