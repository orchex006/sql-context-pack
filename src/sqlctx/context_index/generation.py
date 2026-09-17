"""Hash-checked generation selection driven by DB_METADATA_CONTEXT rows."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from sqlctx.context_index.contracts import (
    ContextGenerationItem,
    ContextGenerationPlan,
    ContextGenerationRequest,
    ContextIndexPage,
)
from sqlctx.core.enums import ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.exporting.header import parse_managed_sql


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_generation_plan(
    request: ContextGenerationRequest,
    page: ContextIndexPage,
    *,
    managed_root: Path,
) -> ContextGenerationPlan:
    """Verify index/header/body agreement and return metadata-only generation inputs."""
    root = managed_root.resolve()
    items: list[ContextGenerationItem] = []
    for entry in page.items:
        if entry.classification_status == "unresolved" and not request.include_unresolved:
            continue
        if entry.managed_relative_path is None or entry.content_hash is None:
            raise SqlCtxError(
                "METADATA_CONTEXT_GENERATION_DRIFT",
                "An index row lacks its managed path or content hash.",
                status_code=409,
            )
        path = (root / entry.managed_relative_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SqlCtxError(
                "METADATA_CONTEXT_GENERATION_DRIFT", "An index path escaped its registered root."
            ) from exc
        if not path.is_file():
            raise SqlCtxError(
                "METADATA_CONTEXT_GENERATION_DRIFT", "An indexed managed SQL file is missing."
            )
        try:
            header, body = parse_managed_sql(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise SqlCtxError(
                "METADATA_CONTEXT_GENERATION_DRIFT", "An indexed managed SQL header is invalid."
            ) from exc
        expected_id = f"{entry.object_type}:{entry.schema_name}.{entry.object_name}"
        if (
            header.object_id != expected_id
            or ObjectType(header.object_type) != ObjectType(entry.object_type)
            or header.context != entry.context
            or header.description != entry.description
            or header.tags != entry.tags
            or header.classification_status != entry.classification_status
            or header.classification_source != entry.classification_source
            or header.source_fingerprint != entry.source_fingerprint
            or header.evidence != entry.evidence
            or header.header_version != entry.header_version
            or header.output_format_version != entry.output_format_version
            or header.content_hash != entry.content_hash
            or _sha256(body) != entry.content_hash
        ):
            raise SqlCtxError(
                "METADATA_CONTEXT_GENERATION_DRIFT",
                "Database index, managed header, and SQL body do not agree.",
                status_code=409,
            )
        items.append(
            ContextGenerationItem(
                object_id=expected_id,
                object_type=ObjectType(entry.object_type),
                context=entry.context,
                description=entry.description,
                tags=entry.tags,
                classification_status=entry.classification_status,
                classification_source=entry.classification_source,
                source_fingerprint=entry.source_fingerprint,
                evidence=entry.evidence,
                header_version=entry.header_version,
                output_format_version=entry.output_format_version,
                managed_relative_path=entry.managed_relative_path,
                content_hash=entry.content_hash,
            )
        )
    payload = [item.model_dump(mode="json") for item in items]
    return ContextGenerationPlan(
        plan_id="gpl_" + secrets.token_urlsafe(12),
        profile=request.profile,
        folder_id=request.folder_id,
        plan_hash=_sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        items=items,
    )
