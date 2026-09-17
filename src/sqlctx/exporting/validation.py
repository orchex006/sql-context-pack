"""Bundle and assembled-output integrity validation."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path, PurePosixPath

import yaml

from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import AssembledFile, AssembledInventory
from sqlctx.exporting.writer import canonical_json, sha256_bytes


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def validate_bundle(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    if path.stat().st_size != expected_size or sha256_bytes(path.read_bytes()) != expected_sha256:
        raise SqlCtxError(
            "BUNDLE_INTEGRITY_FAILED",
            "Downloaded bundle size or SHA-256 did not match export status.",
        )
    with zipfile.ZipFile(path) as archive:
        names = [item.filename for item in archive.infolist()]
        if len(names) != len(set(names)) or any(not _safe_archive_name(name) for name in names):
            raise SqlCtxError("UNSAFE_BUNDLE", "Bundle contains a duplicate or unsafe path.")
        for item in archive.infolist():
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SqlCtxError("UNSAFE_BUNDLE", "Bundle symbolic links are forbidden.")
            if item.file_size > 128 * 1024 * 1024:
                raise SqlCtxError("UNSAFE_BUNDLE", "A bundle member exceeds the v1 safety limit.")


def inventory_output(root: Path) -> AssembledInventory:
    root = root.resolve(strict=True)
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        raise SqlCtxError("MANIFEST_MISSING", "The assembled output has no manifest.yaml.")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = yaml.safe_load(manifest_bytes)
        entries = manifest["managed_files"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise SqlCtxError("MANIFEST_INVALID", "The managed-file manifest is invalid.") from exc
    expected: dict[str, dict[str, object]] = {}
    for entry in entries:
        relative = str(entry["path"])
        if not _safe_archive_name(relative) or relative in expected:
            raise SqlCtxError(
                "MANIFEST_INVALID",
                "The managed-file manifest contains an unsafe or duplicate path.",
            )
        expected[relative] = entry
    files: list[AssembledFile] = []
    for relative, declaration in sorted(expected.items()):
        path = (root / PurePosixPath(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SqlCtxError(
                "UNSAFE_OUTPUT_PATH", "A managed file escaped the output root."
            ) from exc
        if not path.is_file():
            raise SqlCtxError("ASSEMBLED_FILE_MISSING", "A managed output file is missing.")
        content = path.read_bytes()
        item = AssembledFile(
            relative_path=relative, size_bytes=len(content), sha256=sha256_bytes(content)
        )
        if item.size_bytes != declaration["size_bytes"] or item.sha256 != declaration["sha256"]:
            raise SqlCtxError(
                "ASSEMBLED_FILE_MISMATCH", "A managed output file failed local re-read validation."
            )
        files.append(item)
    files.append(
        AssembledFile(
            relative_path="manifest.yaml",
            size_bytes=len(manifest_bytes),
            sha256=sha256_bytes(manifest_bytes),
        )
    )
    canonical = [
        item.model_dump(mode="json", by_alias=True)
        for item in sorted(files, key=lambda item: item.relative_path)
    ]
    return AssembledInventory(
        files=files,
        managed_manifest_sha256=sha256_bytes(manifest_bytes),
        inventory_sha256=sha256_bytes(canonical_json(canonical)),
    )


def scan_managed_output_for_secrets(root: Path, inventory: AssembledInventory) -> list[str]:
    from sqlctx.security.masking import scan_and_redact_sql_literals

    findings: list[str] = []
    for item in inventory.files:
        if not item.relative_path.endswith((".sql", ".json", ".jsonl", ".yaml", ".md", ".csv")):
            continue
        text = (root / PurePosixPath(item.relative_path)).read_text(
            encoding="utf-8", errors="replace"
        )
        _, count = scan_and_redact_sql_literals(text)
        if count:
            findings.append(item.relative_path)
    return findings
