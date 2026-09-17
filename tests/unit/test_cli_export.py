from __future__ import annotations

import zipfile
from pathlib import Path

from sqlctx.cli import main
from sqlctx.core.models import ExportArtifact
from sqlctx.exporting.writer import sha256_bytes
from sqlctx.security.runtime import JsonRuntimeStateStore


def test_local_export_fallback_validates_retained_artifact(
    tmp_path: Path, monkeypatch: object
) -> None:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    export_id = "exp_recover"
    root = state._safe(f"exports/{export_id}")
    root.mkdir(parents=True)
    bundle = root / f"{export_id}.sqlctx.zip"
    manifest = b"managed_files: []\noutput_format_version: '1'\n"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.yaml", manifest)
    artifact = ExportArtifact(
        export_id=export_id,
        size_bytes=bundle.stat().st_size,
        sha256=sha256_bytes(bundle.read_bytes()),
        manifest_sha256=sha256_bytes(manifest),
        bundle_url="/bundle",
        manifest_url="/manifest",
        report_url="/report",
    )
    state.write_json(f"exports/{export_id}/artifact.json", artifact.model_dump(mode="json"))
    monkeypatch.setattr(main, "JsonRuntimeStateStore", lambda: state)  # type: ignore[attr-defined]

    recovered = main._fetch_local_export(export_id, tmp_path / "destination")

    assert recovered.read_bytes() == bundle.read_bytes()
