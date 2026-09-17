from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sqlctx.classification.classifier import ClassificationRun
from sqlctx.core.enums import DatabaseEngine, JobStatus, MaterializationMode, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogSnapshot,
    CatalogStatus,
    ClassificationChange,
    ClassificationPassResult,
    DatabaseCapabilities,
    DatabaseObject,
    HostPythonToolingDescriptor,
    MaterializationPlan,
    MaterializationPlanItem,
    MaterializationSelection,
    ObjectRef,
    SqlFormatResult,
)
from sqlctx.exporting.assembly import assemble_bundles
from sqlctx.exporting.writer import OutputPackageWriter


class Formatter:
    def format_one(
        self, *, object_id: str, sql: str, dialect: str, tooling: object
    ) -> SqlFormatResult:
        return SqlFormatResult(
            object_id=object_id,
            status="formatted",
            content=sql,
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
        )


def _packages(tmp_path: Path) -> list[Path]:
    objects = [
        DatabaseObject(
            ref=ObjectRef(
                object_id="table:app.UM_USER",
                engine=DatabaseEngine.POSTGRES,
                schema_name="app",
                object_name="UM_USER",
                object_type=ObjectType.TABLE,
            ),
            sanitized_definition="CREATE TABLE app.UM_USER (ID INTEGER);\n",
        ),
        DatabaseObject(
            ref=ObjectRef(
                object_id="table:app.CONTENT",
                engine=DatabaseEngine.POSTGRES,
                schema_name="app",
                object_name="CONTENT",
                object_type=ObjectType.TABLE,
            ),
            sanitized_definition="CREATE TABLE app.CONTENT (ID INTEGER);\n",
        ),
    ]
    snapshot = CatalogSnapshot(
        catalog_id="cat_1",
        profile_name="demo",
        request_fingerprint="sha256:req",
        status=JobStatus.READY,
        capabilities=DatabaseCapabilities(
            engine=DatabaseEngine.POSTGRES, sqlfluff_dialect="postgres"
        ),
        objects=objects,
    )
    results = [
        ClassificationPassResult(
            object_id=objects[0].ref.object_id,
            pass_name="pass_2",
            status="final_confirmed",
            category="um",
        ),
        ClassificationPassResult(
            object_id=objects[1].ref.object_id,
            pass_name="pass_2",
            status="final_confirmed",
            category="content",
        ),
    ]
    run = ClassificationRun(
        catalog_id="cat_1",
        results=results,
        evidence=[],
        changes=[
            ClassificationChange(
                object_id=item.ref.object_id,
                pass_1_category=result.category,
                pass_2_category=result.category,
            )
            for item, result in zip(objects, results, strict=True)
        ],
        categories=["content", "um"],
    )
    plan = MaterializationPlan(
        catalog_id="cat_1",
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
        items=[
            MaterializationPlanItem(
                object_id=item.ref.object_id,
                final_category=result.category,
                included=True,
                reason="all_mode",
            )
            for item, result in zip(objects, results, strict=True)
        ],
    )
    status = CatalogStatus(
        catalog_id="cat_1",
        status=JobStatus.READY,
        request_fingerprint="sha256:req",
        discovered_object_count=2,
        fully_analyzed_object_count=2,
        materialized_object_count=2,
    )
    tooling = HostPythonToolingDescriptor(
        python_executable_fingerprint="sha256:python",
        python_version="3.11.10",
        environment_owner="host",
        sqlfluff_version="4.2.2",
        tooling_fingerprint="sha256:tool",
        ready=True,
    )
    writer = OutputPackageWriter(Formatter())  # type: ignore[arg-type]
    paths = []
    for index, obj in enumerate(objects, start=1):
        package = writer.build(
            export_id=f"exp_{index}",
            snapshot=snapshot,
            catalog_status=status,
            classifications=run,
            plan=plan,
            object_ids=[obj.ref.object_id],
            tooling=tooling,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
        )
        path = tmp_path / f"exp_{index}.zip"
        path.write_bytes(package.bundle)
        paths.append(path)
    return paths


def test_multiple_batches_assemble_and_repeat_safely(tmp_path: Path) -> None:
    bundles = _packages(tmp_path)
    output = tmp_path / "project" / "nested" / "sql-context"
    first = assemble_bundles(bundles, output)
    second = assemble_bundles(bundles, output)
    assert first.inventory_sha256 == second.inventory_sha256
    assert (output / "um/tables/UM_USER.sql").is_file()
    assert (output / "content/tables/CONTENT.sql").is_file()
    assert not list(output.rglob(".tmp-*"))


def test_assembly_refuses_unmanaged_collision(tmp_path: Path) -> None:
    bundles = _packages(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "categories.yaml").write_text("owner file", encoding="utf-8")
    with pytest.raises(SqlCtxError) as caught:
        assemble_bundles(bundles, output)
    assert caught.value.code == "UNMANAGED_FILE_CONFLICT"
