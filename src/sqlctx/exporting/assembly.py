"""Deterministic local assembly of one or more verified export batches."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import AssembledInventory
from sqlctx.exporting.validation import inventory_output, validate_bundle
from sqlctx.exporting.writer import canonical_json, sha256_bytes

FULL_AGGREGATED_PATHS = {
    "manifest.yaml",
    "reports/export-summary.md",
    "reports/integrity-report.json",
    "reports/sqlfluff-report.json",
}
AI_AGGREGATED_PATHS = {
    "manifest.yaml",
    "context-index.md",
    "reports/export-summary.md",
    "reports/integrity-report.md",
    "reports/sqlfluff-report.md",
}
AGGREGATED_PATHS = FULL_AGGREGATED_PATHS | AI_AGGREGATED_PATHS


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise SqlCtxError("UNSAFE_BUNDLE", "Bundle contains an unsafe managed path.")
    return path.as_posix()


def _managed_from_manifest(manifest: dict[str, Any]) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for item in manifest.get("managed_files", []):
        path = _safe_path(str(item["path"]))
        if path in result:
            raise SqlCtxError("DUPLICATE_OUTPUT_PATH", "Export manifest repeats a managed path.")
        result[path] = (int(item["size_bytes"]), str(item["sha256"]))
    return result


def assemble_bundles(
    bundles: list[Path],
    output_root: Path,
    *,
    allow_delete_stale: bool = False,
) -> AssembledInventory:
    if not bundles:
        raise SqlCtxError("BUNDLE_REQUIRED", "At least one fetched bundle is required.")
    unique_bundles: list[Path] = []
    seen_hashes: set[str] = set()
    for bundle in bundles:
        resolved = bundle.resolve(strict=True)
        digest = sha256_bytes(resolved.read_bytes())
        if digest not in seen_hashes:
            seen_hashes.add(digest)
            unique_bundles.append(resolved)

    with tempfile.TemporaryDirectory(prefix="sqlctx-assemble-") as temporary:
        stage = Path(temporary) / "stage"
        stage.mkdir()
        merged: dict[str, bytes] = {}
        manifests: list[dict[str, Any]] = []
        sqlfluff_items: dict[str, dict[str, Any]] = {}
        output_profiles: set[str] = set()
        for bundle in unique_bundles:
            validate_bundle(
                bundle,
                expected_size=bundle.stat().st_size,
                expected_sha256=sha256_bytes(bundle.read_bytes()),
            )
            with zipfile.ZipFile(bundle) as archive:
                names = {item.filename for item in archive.infolist()}
                if "manifest.yaml" not in names:
                    raise SqlCtxError("MANIFEST_MISSING", "An export bundle has no manifest.yaml.")
                manifest = yaml.safe_load(archive.read("manifest.yaml"))
                if not isinstance(manifest, dict):
                    raise SqlCtxError("MANIFEST_INVALID", "An export manifest is invalid.")
                declarations = _managed_from_manifest(manifest)
                manifests.append(manifest)
                output_profiles.add(str(manifest.get("export", {}).get("output_profile", "full")))
                manifest_items = manifest.get("sqlfluff", {}).get("items", [])
                for item in manifest_items if isinstance(manifest_items, list) else []:
                    object_id = str(item["object_id"])
                    if object_id in sqlfluff_items and sqlfluff_items[object_id] != item:
                        raise SqlCtxError(
                            "EXPORT_BATCH_CONFLICT",
                            "Batches disagree on a SQLFluff result.",
                        )
                    sqlfluff_items[object_id] = item
                for relative, (size, digest) in declarations.items():
                    if relative not in names:
                        raise SqlCtxError(
                            "ASSEMBLED_FILE_MISSING", "A declared bundle file is missing."
                        )
                    content = archive.read(relative)
                    if len(content) != size or sha256_bytes(content) != digest:
                        raise SqlCtxError(
                            "BUNDLE_INTEGRITY_FAILED",
                            "A declared bundle member failed hash validation.",
                        )
                    if relative == "reports/sqlfluff-report.json":
                        if not manifest_items:
                            report = json.loads(content)
                            for item in report.get("items", []):
                                object_id = str(item["object_id"])
                                if (
                                    object_id in sqlfluff_items
                                    and sqlfluff_items[object_id] != item
                                ):
                                    raise SqlCtxError(
                                        "EXPORT_BATCH_CONFLICT",
                                        "Batches disagree on a SQLFluff result.",
                                    )
                                sqlfluff_items[object_id] = item
                        continue
                    if relative in AGGREGATED_PATHS:
                        continue
                    previous = merged.get(relative)
                    if previous is not None and previous != content:
                        raise SqlCtxError(
                            "EXPORT_BATCH_CONFLICT", "Batches disagree on a shared managed file."
                        )
                    merged[relative] = content

        if len(output_profiles) != 1:
            raise SqlCtxError(
                "EXPORT_PROFILE_CONFLICT",
                "Lean and full export bundles cannot be assembled together.",
            )
        output_profile = next(iter(output_profiles))

        format_counts = {
            "format_requested": len(sqlfluff_items),
            "formatted": sum(item["status"] == "formatted" for item in sqlfluff_items.values()),
            "parse_failed_preserved": sum(
                item["status"] == "parse_failed" for item in sqlfluff_items.values()
            ),
            "format_failed_preserved": sum(
                item["status"] in {"format_failed", "rolled_back"}
                for item in sqlfluff_items.values()
            ),
        }
        if format_counts["format_requested"] != sum(
            value for key, value in format_counts.items() if key != "format_requested"
        ):
            raise SqlCtxError(
                "SQLFLUFF_ACCOUNTING_INVALID", "Aggregated SQLFluff coverage is incomplete."
            )
        export_ids = [str(item["export"]["export_id"]) for item in manifests]
        merged["reports/export-summary.md"] = (
            "# SQL Context Pack export\n\nAssembled export batches: "
            + ", ".join(export_ids)
            + f".\n\nMaterialized {format_counts['format_requested']} objects.\n"
        ).encode()
        if output_profile == "full":
            merged["reports/sqlfluff-report.json"] = canonical_json(
                {**format_counts, "items": [sqlfluff_items[key] for key in sorted(sqlfluff_items)]}
            )
            integrity = {
                "bundle_count": len(manifests),
                "duplicate_paths": False,
                "path_traversal": False,
                "raw_secrets_detected": False,
            }
            merged["reports/integrity-report.json"] = canonical_json(integrity)
        else:
            merged["context-index.md"] = (
                "# SQL Context\n\n"
                f"Materialized objects: {format_counts['format_requested']}\n\n"
                "## Managed context files\n\n"
                + "\n".join(
                    f"- `{path}`"
                    for path in sorted(merged)
                    if path.endswith((".sql", ".md", ".csv", ".yaml"))
                    and not path.startswith("reports/")
                )
                + "\n"
            ).encode()
            merged["reports/sqlfluff-report.md"] = (
                "# SQLFluff report\n\n"
                + "\n".join(f"- {key}: {value}" for key, value in format_counts.items())
                + "\n"
            ).encode()
            merged["reports/integrity-report.md"] = (
                "# Integrity report\n\n"
                f"- bundle count: {len(manifests)}\n"
                "- duplicate paths: false\n"
                "- path traversal: false\n"
                "- raw secrets detected: false\n"
            ).encode()

        base = dict(manifests[0])
        base["export"] = dict(base["export"])
        base["export"]["export_ids"] = export_ids
        base["export"].pop("export_id", None)
        base["export"]["materialized_object_count"] = format_counts["format_requested"]
        base["sqlfluff"] = {**base["sqlfluff"], **format_counts}
        base["sqlfluff"].pop("items", None)
        inventory = [
            {"path": path, "size_bytes": len(content), "sha256": sha256_bytes(content)}
            for path, content in sorted(merged.items())
        ]
        base["managed_files"] = inventory
        merged["manifest.yaml"] = yaml.safe_dump(base, sort_keys=True).encode()

        for relative, content in merged.items():
            staged = stage / PurePosixPath(relative)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)

        output_root = output_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        previous_managed: set[str] = set()
        previous_manifest = output_root / "manifest.yaml"
        if previous_manifest.is_file():
            try:
                previous = yaml.safe_load(previous_manifest.read_bytes())
                previous_managed = set(_managed_from_manifest(previous)) | {"manifest.yaml"}
            except (OSError, yaml.YAMLError, TypeError, KeyError) as exc:
                raise SqlCtxError(
                    "MANIFEST_INVALID",
                    "Existing output manifest is invalid; unmanaged files are protected.",
                ) from exc
        incoming = set(merged)
        stale = previous_managed - incoming
        if stale and not allow_delete_stale:
            raise SqlCtxError(
                "STALE_MANAGED_FILES_REQUIRE_APPROVAL",
                "Removing stale managed files requires explicit owner confirmation.",
                status_code=403,
            )
        for relative, content in sorted(merged.items()):
            target = output_root / PurePosixPath(relative)
            if (
                target.exists()
                and relative not in previous_managed
                and target.read_bytes() != content
            ):
                raise SqlCtxError(
                    "UNMANAGED_FILE_CONFLICT",
                    "Assembly would overwrite an unmanaged file.",
                    status_code=409,
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            source = stage / PurePosixPath(relative)
            atomic = target.with_name(f".sqlctx-atomic-{target.name}")
            try:
                shutil.copyfile(source, atomic)
                os.replace(atomic, target)
            finally:
                atomic.unlink(missing_ok=True)
        for relative in sorted(stale, reverse=True):
            target = output_root / PurePosixPath(relative)
            if target.is_file():
                target.unlink()
        for directory in sorted(
            (path for path in output_root.rglob("*") if path.is_dir()), reverse=True
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
        return inventory_output(output_root)
