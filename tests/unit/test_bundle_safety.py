from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sqlctx.core.errors import SqlCtxError
from sqlctx.exporting.validation import inventory_output, validate_bundle
from sqlctx.exporting.writer import sha256_bytes


def test_bundle_path_traversal_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../outside.sql", "SELECT 1;")
    with pytest.raises(SqlCtxError) as caught:
        validate_bundle(
            path, expected_size=path.stat().st_size, expected_sha256=sha256_bytes(path.read_bytes())
        )
    assert caught.value.code == "UNSAFE_BUNDLE"


def test_destination_corruption_fails_local_reread(tmp_path: Path) -> None:
    source = Path("fixtures/realistic-output")
    output = tmp_path / "output"
    import shutil

    shutil.copytree(source, output)
    (output / "um/tables/UM_USER.sql").write_text("corrupt", encoding="utf-8")
    with pytest.raises(SqlCtxError) as caught:
        inventory_output(output)
    assert caught.value.code == "ASSEMBLED_FILE_MISMATCH"
