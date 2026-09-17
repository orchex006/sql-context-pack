"""Deterministic, path-safe SQL context bundle writer."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from sqlctx._version import OUTPUT_FORMAT_VERSION, __version__
from sqlctx.classification.classifier import ClassificationRun
from sqlctx.core.enums import (
    ClassificationPass,
    ClassificationStatus,
    FormatStatus,
    ObjectType,
    OutputProfile,
    SampleOutputFormat,
)
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogSnapshot,
    CatalogStatus,
    HostPythonToolingDescriptor,
    MaterializationPlan,
    SqlFormatResult,
)
from sqlctx.exporting.header import ManagedSqlHeader, parse_managed_sql, render_managed_sql
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.indexing.builder import IndexBuilder, IndexBundle
from sqlctx.security.masking import scan_and_redact_sql_literals


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip(".")
    if not cleaned or cleaned in {".", ".."}:
        raise SqlCtxError(
            "UNSAFE_OUTPUT_PATH", "An object name cannot be mapped to a safe output path."
        )
    return cleaned


def _validate_relative(path: str) -> str:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise SqlCtxError("UNSAFE_OUTPUT_PATH", "A generated output path escaped the bundle root.")
    return candidate.as_posix()


@dataclass(frozen=True)
class ExportPackage:
    bundle: bytes
    manifest: dict[str, Any]
    report: dict[str, Any]
    files: dict[str, bytes]
    format_results: list[SqlFormatResult]
    skipped_objects: list[dict[str, Any]]


class OutputPackageWriter:
    def __init__(self, formatter: SqlFluffFormatter, indexes: IndexBuilder | None = None) -> None:
        self.formatter = formatter
        self.indexes = indexes or IndexBuilder()

    @staticmethod
    def _write(files: dict[str, bytes], path: str, content: bytes) -> None:
        path = _validate_relative(path)
        if path in files:
            raise SqlCtxError(
                "DUPLICATE_OUTPUT_PATH", "Two managed files mapped to one output path."
            )
        files[path] = content

    @staticmethod
    def _json_lines(items: list[dict[str, Any]]) -> bytes:
        return b"".join(canonical_json(item) for item in items)

    @staticmethod
    def _sample_content(
        snapshot: CatalogSnapshot, object_id: str, sample_format: SampleOutputFormat
    ) -> tuple[str, bytes] | None:
        page = snapshot.samples.get(object_id)
        if page is None:
            return None
        rows = [dict(zip(page.columns, row, strict=True)) for row in page.rows]
        if sample_format == SampleOutputFormat.JSON:
            return "json", canonical_json(
                {
                    "requested": page.requested_count,
                    "actual": page.actual_count,
                    "shortage_reason": page.shortage_reason,
                    "rows": rows,
                }
            )
        if sample_format == SampleOutputFormat.CSV:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=page.columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            return "csv", stream.getvalue().encode()
        lines = [
            f"# Sample: {object_id}",
            "",
            f"Requested: {page.requested_count}  ",
            f"Actual: {page.actual_count}  ",
            f"Shortage: {page.shortage_reason or 'none'}",
            "",
        ]
        if page.columns:
            lines.append("| " + " | ".join(page.columns) + " |")
            lines.append("| " + " | ".join("---" for _ in page.columns) + " |")
            for row in page.rows:
                values = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
                lines.append("| " + " | ".join(values) + " |")
        return "md", ("\n".join(lines) + "\n").encode()

    def build(
        self,
        *,
        export_id: str,
        snapshot: CatalogSnapshot,
        catalog_status: CatalogStatus,
        classifications: ClassificationRun,
        plan: MaterializationPlan,
        object_ids: list[str],
        tooling: HostPythonToolingDescriptor,
        created_at: datetime,
        output_profile: OutputProfile = OutputProfile.AI,
        sample_format: SampleOutputFormat = SampleOutputFormat.MARKDOWN,
        progress: Callable[[int, str, str, str | None], None] | None = None,
    ) -> ExportPackage:
        # Public request models serialize enum values, so normalize again at this boundary.
        output_profile = OutputProfile(output_profile)
        sample_format = SampleOutputFormat(sample_format)
        if not tooling.ready or not tooling.sqlfluff_version or not tooling.tooling_fingerprint:
            raise SqlCtxError(
                "TOOLING_UNAVAILABLE", "Pinned SQLFluff is required for export.", status_code=503
            )
        selected_ids = {item.object_id for item in plan.items if item.included}
        if not set(object_ids) <= selected_ids:
            raise SqlCtxError(
                "OBJECT_NOT_MATERIALIZABLE", "Export batch contains excluded or unresolved objects."
            )
        object_map = {item.ref.object_id: item for item in snapshot.objects}
        if not set(object_ids) <= set(object_map):
            raise SqlCtxError(
                "UNKNOWN_OBJECT", "Export batch references an unknown catalog object."
            )
        final = {
            item.object_id: item
            for item in classifications.results
            if item.pass_name == ClassificationPass.PASS_2
        }
        evidence_kinds = {item.evidence_id: item.kind for item in classifications.evidence}
        files: dict[str, bytes] = {}
        format_results: list[SqlFormatResult] = []
        skipped_objects: list[dict[str, Any]] = []
        redacted_secret_count = 0
        used_paths: dict[str, str] = {}

        for position, object_id in enumerate(object_ids, start=1):
            obj = object_map[object_id]
            classification = final.get(object_id)
            if classification is None:
                raise SqlCtxError(
                    "CLASSIFICATION_UNRESOLVED", "An unresolved object cannot be materialized."
                )
            confirmed = classification.status == ClassificationStatus.FINAL_CONFIRMED
            category = classification.category if confirmed else None
            output_category = category or "unknowns"
            folder = {
                ObjectType.TABLE: "tables",
                ObjectType.FUNCTION: "functions",
            }.get(obj.ref.object_type, "store_procedures")
            base = _safe_segment(obj.ref.object_name) + ".sql"
            relative = f"{_safe_segment(output_category)}/{folder}/{base}"
            if relative in used_paths and used_paths[relative] != object_id:
                suffix = hashlib.sha256(object_id.encode()).hexdigest()[:10]
                relative = f"{_safe_segment(output_category)}/{folder}/{_safe_segment(obj.ref.schema_name)}__{_safe_segment(obj.ref.object_name)}__{suffix}.sql"
            used_paths[relative] = object_id
            cleaned, secret_count = scan_and_redact_sql_literals(
                obj.sanitized_definition or "-- definition unavailable\n"
            )
            _, residual_secret_count = scan_and_redact_sql_literals(cleaned)
            if residual_secret_count:
                skipped_objects.append(
                    {
                        "object_id": object_id,
                        "status": "skipped_security",
                        "error_code": "RAW_SECRET_DETECTED",
                    }
                )
                if progress is not None:
                    progress(position, object_id, "skipped_security", "RAW_SECRET_DETECTED")
                continue
            redacted_secret_count += secret_count
            candidate_evidence = {
                evidence_id
                for candidate in classification.candidates
                for evidence_id in candidate.evidence_ids
            }
            source: Literal["owner", "rule", "unknown"] = (
                "owner"
                if confirmed
                and any(
                    evidence_kinds.get(evidence_id) == "owner_override"
                    for evidence_id in candidate_evidence
                )
                else "rule"
                if confirmed
                else "unknown"
            )
            tags = sorted({category, str(obj.ref.object_type)}) if category else []
            managed_header = ManagedSqlHeader(
                object_id=object_id,
                engine=obj.ref.engine,
                schema_name=obj.ref.schema_name,
                object_name=obj.ref.object_name,
                object_type=obj.ref.object_type,
                context=category,
                description=obj.native_comment,
                tags=tags,
                evidence=sorted(candidate_evidence),
                classification_status="confirmed" if confirmed else "unresolved",
                classification_source=source,
                source_fingerprint=obj.source_fingerprint,
                content_hash=sha256_bytes(cleaned.encode()),
                output_format_version=OUTPUT_FORMAT_VERSION,
            )
            result = self.formatter.format_one(
                object_id=object_id,
                sql=render_managed_sql(managed_header, cleaned),
                dialect=snapshot.capabilities.sqlfluff_dialect if snapshot.capabilities else "ansi",
                tooling=tooling,
            )
            if secret_count:
                result = result.model_copy(
                    update={"diagnostics": [*result.diagnostics, "secret_literals_redacted"]}
                )
            format_results.append(result)
            try:
                _, formatted_body = parse_managed_sql(result.content)
            except ValueError as exc:
                raise SqlCtxError(
                    "MANAGED_SQL_HEADER_FORMATTING_FAILED",
                    "SQL formatting did not preserve the managed metadata header.",
                ) from exc
            formatted_body = formatted_body.rstrip() + "\n"
            managed_header = managed_header.model_copy(
                update={"content_hash": sha256_bytes(formatted_body.encode())}
            )
            content = render_managed_sql(managed_header, formatted_body)
            self._write(files, relative, content.encode())
            if obj.ref.object_type == ObjectType.TABLE:
                metadata_path = (
                    f"{_safe_segment(output_category)}/table_metadata/"
                    f"{_safe_segment(obj.ref.schema_name)}__{_safe_segment(obj.ref.object_name)}.yaml"
                )
                self._write(
                    files,
                    metadata_path,
                    yaml.safe_dump(
                        {
                            "object_id": object_id,
                            "schema": obj.ref.schema_name,
                            "table": obj.ref.object_name,
                            "description": obj.native_comment,
                            "columns": [item.model_dump(mode="json") for item in obj.columns],
                            "constraints": [
                                item.model_dump(mode="json") for item in obj.constraints
                            ],
                            "foreign_keys": [
                                item.model_dump(mode="json") for item in obj.foreign_keys
                            ],
                            "indexes": [item.model_dump(mode="json") for item in obj.indexes],
                        },
                        sort_keys=False,
                        allow_unicode=True,
                    ).encode(),
                )
                sample = self._sample_content(snapshot, object_id, sample_format)
                if sample is not None:
                    extension, sample_content = sample
                    sample_path = (
                        f"{_safe_segment(output_category)}/samples/"
                        f"{_safe_segment(obj.ref.schema_name)}__{_safe_segment(obj.ref.object_name)}.{extension}"
                    )
                    self._write(files, sample_path, sample_content)
            if progress is not None:
                progress(position, object_id, str(result.status), None)

        category_names = sorted(
            {item.final_category for item in plan.items if item.included and item.final_category}
        )
        self._write(
            files,
            "categories.yaml",
            yaml.safe_dump({"categories": category_names}, sort_keys=True).encode(),
        )
        for category in category_names:
            self._write(
                files,
                f"{_safe_segment(category)}/category.yaml",
                yaml.safe_dump({"name": category}, sort_keys=True).encode(),
            )
        unresolved = [
            item.model_dump(mode="json")
            for item in classifications.results
            if item.pass_name == ClassificationPass.PASS_2
            and item.status != ClassificationStatus.FINAL_CONFIRMED
        ]
        if output_profile == OutputProfile.FULL:
            index_bundle: IndexBundle = self.indexes.build(snapshot, classifications, plan)
            self._write(files, "catalog.json", canonical_json(snapshot.model_dump(mode="json")))
            self._write(
                files,
                "unresolved/classification-requests.yaml",
                yaml.safe_dump({"items": unresolved}, sort_keys=True).encode(),
            )
            self._write(files, "indexes/objects.jsonl", self._json_lines(index_bundle.objects))
            self._write(files, "indexes/nodes.jsonl", self._json_lines(index_bundle.nodes))
            self._write(files, "indexes/edges.jsonl", self._json_lines(index_bundle.edges))
            self._write(
                files,
                "indexes/relationships.json",
                canonical_json(index_bundle.relationships),
            )
            self._write(
                files,
                "indexes/routine-dependencies.json",
                canonical_json(index_bundle.routine_dependencies),
            )
            self._write(files, "indexes/tags.json", canonical_json(index_bundle.tags))
            self._write(files, "indexes/graph.json", canonical_json(index_bundle.graph))

        format_counts = {
            "format_requested": len(format_results),
            "formatted": sum(item.status == FormatStatus.FORMATTED for item in format_results),
            "parse_failed_preserved": sum(
                item.status == FormatStatus.PARSE_FAILED for item in format_results
            ),
            "format_failed_preserved": sum(
                item.status in {FormatStatus.FORMAT_FAILED, FormatStatus.ROLLED_BACK}
                for item in format_results
            ),
        }
        if (
            format_counts["format_requested"]
            != format_counts["formatted"]
            + format_counts["parse_failed_preserved"]
            + format_counts["format_failed_preserved"]
        ):
            raise SqlCtxError(
                "SQLFLUFF_ACCOUNTING_INVALID", "SQLFluff result accounting is incomplete."
            )
        if output_profile == OutputProfile.FULL:
            reports = {
                "category-preview.json": {"categories": classifications.categories},
                "classification-report.json": classifications.model_dump(mode="json"),
                "materialization-plan.json": plan.model_dump(mode="json"),
                "masking-report.json": {
                    "raw_credentials_exported": False,
                    "raw_secrets_detected_after_export": False,
                    "secret_literals_redacted": redacted_secret_count,
                    "objects_skipped_security": len(skipped_objects),
                },
                "sqlfluff-report.json": {
                    **format_counts,
                    "items": [item.model_dump(mode="json") for item in format_results],
                },
            }
            for name, value in reports.items():
                self._write(files, f"reports/{name}", canonical_json(value))
        report = {
            "export_id": export_id,
            "catalog_id": snapshot.catalog_id,
            "objects": [
                *[item.model_dump(mode="json") for item in format_results],
                *skipped_objects,
            ],
            "warnings": [
                *[diagnostic for item in format_results for diagnostic in item.diagnostics],
                *["RAW_SECRET_DETECTED" for _ in skipped_objects],
            ],
        }
        self._write(
            files,
            "reports/export-summary.md",
            (
                f"# Export {export_id}\n\nMaterialized {len(format_results)} of {len(object_ids)} requested objects from catalog `{snapshot.catalog_id}`. Skipped for security: {len(skipped_objects)}.\n"
            ).encode(),
        )
        if output_profile == OutputProfile.AI:
            self._write(
                files,
                "context-index.md",
                (
                    "# SQL Context\n\n"
                    f"Categories: {', '.join(category_names)}\n\n"
                    f"Objects materialized in this batch: {len(format_results)}\n"
                ).encode(),
            )
            self._write(
                files,
                "reports/sqlfluff-report.md",
                (
                    "# SQLFluff report\n\n"
                    + "\n".join(f"- {key}: {value}" for key, value in format_counts.items())
                    + "\n"
                ).encode(),
            )

        inventory = [
            {"path": path, "size_bytes": len(content), "sha256": sha256_bytes(content)}
            for path, content in sorted(files.items())
        ]
        integrity = {
            "duplicate_paths": False,
            "path_traversal": False,
            "raw_secrets_detected": False,
            "managed_files": inventory,
        }
        if output_profile == OutputProfile.FULL:
            self._write(files, "reports/integrity-report.json", canonical_json(integrity))
        else:
            self._write(
                files,
                "reports/integrity-report.md",
                b"# Integrity report\n\n"
                b"- duplicate paths: false\n"
                b"- path traversal: false\n"
                b"- raw secrets detected: false\n",
            )
        inventory = [
            {"path": path, "size_bytes": len(content), "sha256": sha256_bytes(content)}
            for path, content in sorted(files.items())
        ]
        materialized_ids = {item.object_id for item in format_results}
        materialized_samples = [
            snapshot.samples[object_id]
            for object_id in object_ids
            if object_id in materialized_ids and object_id in snapshot.samples
        ]
        generated_payload_bytes = sum(len(content) for content in files.values())
        manifest = {
            "output_format_version": OUTPUT_FORMAT_VERSION,
            "generator": {"name": "sql-context-pack", "version": __version__},
            "source": {
                "profile": snapshot.profile_name,
                "engine": snapshot.capabilities.engine if snapshot.capabilities else None,
                "schemas": sorted({item.ref.schema_name for item in snapshot.objects}),
            },
            "export": {
                "export_id": export_id,
                "created_at": created_at.isoformat(),
                "discovered_object_count": catalog_status.discovered_object_count,
                "fully_analyzed_object_count": catalog_status.fully_analyzed_object_count,
                "analysis_failed_object_count": catalog_status.analysis_failed_object_count,
                "analysis_failures": [
                    item.model_dump(mode="json") for item in catalog_status.failures
                ],
                "requested_object_count": len(object_ids),
                "materialized_object_count": len(format_results),
                "skipped_security_object_count": len(skipped_objects),
                "intentionally_excluded_object_count": catalog_status.intentionally_excluded_object_count,
                "output_profile": output_profile.value,
                "sample_format": sample_format.value,
                "machine_artifacts_skipped": output_profile == OutputProfile.AI,
                "generated_file_count": len(inventory) + 1,
                "generated_payload_bytes": generated_payload_bytes,
                "sampled_table_count": len(materialized_samples),
                "sample_rows_requested": sum(item.requested_count for item in materialized_samples),
                "sample_rows_actual": sum(item.actual_count for item in materialized_samples),
            },
            "selection": {
                **plan.selection.model_dump(mode="json"),
                "excluded_dependencies": "index_only_boundary_metadata",
            },
            "classification": {
                "strategy": "two_pass",
                "moved_into_selected_categories": sum(
                    item.moved_into_selected for item in classifications.changes
                ),
                "moved_out_of_selected_categories": sum(
                    item.moved_out_of_selected for item in classifications.changes
                ),
                "unresolved_affecting_selection": len(unresolved),
            },
            "security": {
                "masking_policy": "strict",
                "raw_credentials_exported": False,
                "raw_secrets_detected_after_export": False,
                "secret_literals_redacted": redacted_secret_count,
                "objects_skipped_security": len(skipped_objects),
            },
            "sqlfluff": {
                "format_scope": "final_materialization",
                "python_executable_fingerprint": tooling.python_executable_fingerprint,
                "python_version": tooling.python_version,
                "version": tooling.sqlfluff_version,
                "tooling_fingerprint": tooling.tooling_fingerprint,
                "dialect": snapshot.capabilities.sqlfluff_dialect
                if snapshot.capabilities
                else "ansi",
                "exclude_rules": ["CP02", "LT01", "RF06"],
                **format_counts,
                "items": [
                    {
                        "object_id": item.object_id,
                        "status": str(item.status),
                        "diagnostics": item.diagnostics,
                    }
                    for item in format_results
                ],
            },
            "managed_files": inventory,
        }
        self._write(files, "manifest.yaml", yaml.safe_dump(manifest, sort_keys=True).encode())

        with tempfile.TemporaryDirectory(prefix="sqlctx-bundle-") as temporary:
            archive_path = Path(temporary) / f"{export_id}.sqlctx.zip"
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for path, file_content in sorted(files.items()):
                    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    archive.writestr(info, file_content)
            bundle = archive_path.read_bytes()
        return ExportPackage(
            bundle=bundle,
            manifest=manifest,
            report=report,
            files=files,
            format_results=format_results,
            skipped_objects=skipped_objects,
        )
