from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlctx.classification.classifier import ClassificationRun
from sqlctx.core.enums import (
    DatabaseEngine,
    JobStatus,
    MaterializationMode,
    ObjectType,
    OutputProfile,
    SampleOutputFormat,
)
from sqlctx.core.models import (
    CatalogObjectFailure,
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
    SamplePage,
    SqlFormatResult,
)
from sqlctx.exporting.header import parse_managed_sql
from sqlctx.exporting.validation import inventory_output, validate_bundle
from sqlctx.exporting.writer import OutputPackageWriter, sha256_bytes


class FakeFormatter:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def format_one(
        self, *, object_id: str, sql: str, dialect: str, tooling: object
    ) -> SqlFormatResult:
        self.seen.append(sql)
        assert "sqlctx_sample_row" not in sql
        header, separator, body = sql.partition("\n")
        return SqlFormatResult(
            object_id=object_id,
            status="formatted",
            content=header + separator + body.upper().rstrip() + "   \n\n",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
        )


class FailingIndexes:
    def build(self, *_: object) -> object:
        raise AssertionError("lean output must not build machine indexes")


class PassthroughFormatter:
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


def test_all_mode_materializes_unresolved_function_under_unknowns() -> None:
    object_id = "function:app.CALCULATE_STATE"
    body = "CREATE FUNCTION app.CALCULATE_STATE() RETURNS int AS BEGIN RETURN 1; END;\n"
    snapshot = CatalogSnapshot(
        catalog_id="cat_unknown",
        profile_name="demo",
        request_fingerprint="sha256:req",
        status=JobStatus.READY,
        capabilities=DatabaseCapabilities(engine=DatabaseEngine.SQLSERVER, sqlfluff_dialect="tsql"),
        objects=[
            DatabaseObject(
                ref=ObjectRef(
                    object_id=object_id,
                    engine=DatabaseEngine.SQLSERVER,
                    schema_name="app",
                    object_name="CALCULATE_STATE",
                    object_type=ObjectType.FUNCTION,
                ),
                sanitized_definition=body,
                source_fingerprint="sha256:" + "a" * 64,
            )
        ],
    )
    classification = ClassificationRun(
        catalog_id="cat_unknown",
        categories=["app_state"],
        evidence=[],
        changes=[],
        results=[
            ClassificationPassResult(
                object_id=object_id,
                pass_name="pass_2",
                status="final_unresolved",
                category=None,
            )
        ],
    )
    plan = MaterializationPlan(
        catalog_id="cat_unknown",
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
        items=[
            MaterializationPlanItem(
                object_id=object_id,
                final_category=None,
                included=True,
                reason="all_mode",
            )
        ],
    )

    package = OutputPackageWriter(PassthroughFormatter()).build(  # type: ignore[arg-type]
        export_id="exp_unknown",
        snapshot=snapshot,
        catalog_status=CatalogStatus(
            catalog_id="cat_unknown",
            status=JobStatus.READY,
            request_fingerprint="sha256:req",
            discovered_object_count=1,
            fully_analyzed_object_count=1,
            materialized_object_count=1,
        ),
        classifications=classification,
        plan=plan,
        object_ids=[object_id],
        tooling=HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.11.10",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
            ready=True,
        ),
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    content = package.files["unknowns/functions/CALCULATE_STATE.sql"].decode()
    header, parsed_body = parse_managed_sql(content)
    assert header.context is None
    assert header.description is None
    assert header.tags == []
    assert header.classification_status == "unresolved"
    assert parsed_body == body


def test_writer_defaults_to_lean_markdown_samples_and_validates(tmp_path: Path) -> None:
    object_id = "table:app.UM_USER"
    snapshot = CatalogSnapshot(
        catalog_id="cat_1",
        profile_name="demo",
        request_fingerprint="sha256:req",
        status=JobStatus.READY,
        capabilities=DatabaseCapabilities(
            engine=DatabaseEngine.POSTGRES, sqlfluff_dialect="postgres"
        ),
        objects=[
            DatabaseObject(
                ref=ObjectRef(
                    object_id=object_id,
                    engine=DatabaseEngine.POSTGRES,
                    schema_name="app",
                    object_name="UM_USER",
                    object_type=ObjectType.TABLE,
                ),
                sanitized_definition="create table um_user(id int);\n",
            )
        ],
        samples={
            object_id: SamplePage(
                object_id=object_id,
                columns=["id"],
                rows=[[1]],
                requested_count=10,
                actual_count=1,
                shortage_reason="table_has_fewer_rows",
                deterministic=True,
            )
        },
    )
    classification = ClassificationRun(
        catalog_id="cat_1",
        categories=["um"],
        evidence=[],
        changes=[
            ClassificationChange(object_id=object_id, pass_1_category="um", pass_2_category="um")
        ],
        results=[
            ClassificationPassResult(
                object_id=object_id, pass_name="pass_2", status="final_confirmed", category="um"
            )
        ],
    )
    plan = MaterializationPlan(
        catalog_id="cat_1",
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
        items=[
            MaterializationPlanItem(
                object_id=object_id, final_category="um", included=True, reason="all_mode"
            )
        ],
    )
    formatter = FakeFormatter()
    package = OutputPackageWriter(formatter, indexes=FailingIndexes()).build(  # type: ignore[arg-type]
        export_id="exp_1",
        snapshot=snapshot,
        catalog_status=CatalogStatus(
            catalog_id="cat_1",
            status=JobStatus.READY,
            request_fingerprint="sha256:req",
            discovered_object_count=1,
            fully_analyzed_object_count=1,
            materialized_object_count=1,
        ),
        classifications=classification,
        plan=plan,
        object_ids=[object_id],
        tooling=HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.11.10",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
            ready=True,
        ),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    archive_path = tmp_path / "export.zip"
    archive_path.write_bytes(package.bundle)
    validate_bundle(
        archive_path,
        expected_size=len(package.bundle),
        expected_sha256=sha256_bytes(package.bundle),
    )
    output = tmp_path / "output"
    output.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(output)
    inventory = inventory_output(output)
    assert inventory.managed_manifest_sha256 == sha256_bytes(package.files["manifest.yaml"])
    table_sql = (output / "um" / "tables" / "UM_USER.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE UM_USER" in table_sql
    assert "sqlctx_sample_row" not in table_sql
    table_header, table_body = parse_managed_sql(table_sql)
    assert table_body.endswith("\n")
    assert not table_body.endswith(" \n")
    assert table_header.content_hash == sha256_bytes(table_body.encode())
    sample = (output / "um" / "samples" / "app__UM_USER.md").read_text(encoding="utf-8")
    assert "| id |" in sample
    assert "| 1 |" in sample
    metadata = (output / "um" / "table_metadata" / "app__UM_USER.yaml").read_text(encoding="utf-8")
    assert "description:" in metadata
    assert "constraints:" in metadata
    assert "indexes:" in metadata
    assert package.manifest["export"]["output_profile"] == "ai"
    assert package.manifest["export"]["machine_artifacts_skipped"] is True
    assert package.manifest["export"]["analysis_failures"] == []
    assert not any(path.endswith((".json", ".jsonl")) for path in package.files)

    full = OutputPackageWriter(formatter).build(
        export_id="exp_full",
        snapshot=snapshot,
        catalog_status=CatalogStatus(
            catalog_id="cat_1",
            status=JobStatus.READY,
            request_fingerprint="sha256:req",
            discovered_object_count=1,
            fully_analyzed_object_count=1,
            materialized_object_count=1,
        ),
        classifications=classification,
        plan=plan,
        object_ids=[object_id],
        tooling=HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.11.10",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
            ready=True,
        ),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
        output_profile=OutputProfile.FULL,
        sample_format=SampleOutputFormat.JSON,
    )
    assert "catalog.json" in full.files
    assert "um/samples/app__UM_USER.json" in full.files
    csv_sample = OutputPackageWriter._sample_content(snapshot, object_id, SampleOutputFormat.CSV)
    assert csv_sample == ("csv", b"id\n1\n")


def test_writer_redacts_secret_and_continues_object_export() -> None:
    object_id = "table:app.SECRET_CONFIG"
    snapshot = CatalogSnapshot(
        catalog_id="cat_secret",
        profile_name="demo",
        request_fingerprint="sha256:req",
        status=JobStatus.READY,
        capabilities=DatabaseCapabilities(
            engine=DatabaseEngine.POSTGRES, sqlfluff_dialect="postgres"
        ),
        objects=[
            DatabaseObject(
                ref=ObjectRef(
                    object_id=object_id,
                    engine=DatabaseEngine.POSTGRES,
                    schema_name="app",
                    object_name="SECRET_CONFIG",
                    object_type=ObjectType.TABLE,
                ),
                sanitized_definition="CREATE VIEW x AS SELECT password='top-secret';",
            )
        ],
    )
    classification = ClassificationRun(
        catalog_id="cat_secret",
        categories=["config"],
        evidence=[],
        changes=[],
        results=[
            ClassificationPassResult(
                object_id=object_id,
                pass_name="pass_2",
                status="final_confirmed",
                category="config",
            )
        ],
    )
    plan = MaterializationPlan(
        catalog_id="cat_secret",
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
        items=[
            MaterializationPlanItem(
                object_id=object_id,
                final_category="config",
                included=True,
                reason="all_mode",
            )
        ],
    )
    formatter = FakeFormatter()

    package = OutputPackageWriter(formatter).build(
        export_id="exp_secret",
        snapshot=snapshot,
        catalog_status=CatalogStatus(
            catalog_id="cat_secret",
            status=JobStatus.READY,
            request_fingerprint="sha256:req",
            discovered_object_count=1,
            fully_analyzed_object_count=1,
            materialized_object_count=1,
        ),
        classifications=classification,
        plan=plan,
        object_ids=[object_id],
        tooling=HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.11.10",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
            ready=True,
        ),
        created_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert package.skipped_objects == []
    assert "top-secret" not in formatter.seen[0]
    assert "secret_literals_redacted" in package.report["warnings"]
    assert package.manifest["security"]["secret_literals_redacted"] == 1


def test_manifest_names_every_analysis_failure_behind_the_count() -> None:
    """An export must carry the same named failures the catalog status reported."""
    object_id = "table:app.UM_USER"
    snapshot = CatalogSnapshot(
        catalog_id="cat_failures",
        profile_name="demo",
        request_fingerprint="sha256:req",
        status=JobStatus.COMPLETED_WITH_WARNINGS,
        capabilities=DatabaseCapabilities(engine=DatabaseEngine.SQLSERVER, sqlfluff_dialect="tsql"),
        objects=[
            DatabaseObject(
                ref=ObjectRef(
                    object_id=object_id,
                    engine=DatabaseEngine.SQLSERVER,
                    schema_name="app",
                    object_name="UM_USER",
                    object_type=ObjectType.TABLE,
                ),
                sanitized_definition="create table um_user(id int);\n",
            )
        ],
    )
    classification = ClassificationRun(
        catalog_id="cat_failures",
        categories=["um"],
        evidence=[],
        changes=[
            ClassificationChange(object_id=object_id, pass_1_category="um", pass_2_category="um")
        ],
        results=[
            ClassificationPassResult(
                object_id=object_id, pass_name="pass_2", status="final_confirmed", category="um"
            )
        ],
    )
    plan = MaterializationPlan(
        catalog_id="cat_failures",
        selection=MaterializationSelection(mode=MaterializationMode.ALL),
        items=[
            MaterializationPlanItem(
                object_id=object_id, final_category="um", included=True, reason="all_mode"
            )
        ],
    )
    failure = CatalogObjectFailure(
        object_id="function:app.FN_GET_ERROR_MESSAGE",
        object_type=ObjectType.FUNCTION,
        stage="analysis",
        error_code="DEFINITION_UNAVAILABLE",
        message="Stored function definition is unavailable.",
    )

    package = OutputPackageWriter(PassthroughFormatter()).build(  # type: ignore[arg-type]
        export_id="exp_failures",
        snapshot=snapshot,
        catalog_status=CatalogStatus(
            catalog_id="cat_failures",
            status=JobStatus.COMPLETED_WITH_WARNINGS,
            request_fingerprint="sha256:req",
            discovered_object_count=2,
            fully_analyzed_object_count=1,
            analysis_failed_object_count=1,
            materialized_object_count=1,
            failures=[failure],
        ),
        classifications=classification,
        plan=plan,
        object_ids=[object_id],
        tooling=HostPythonToolingDescriptor(
            python_executable_fingerprint="sha256:python",
            python_version="3.11.10",
            environment_owner="host",
            sqlfluff_version="4.2.2",
            tooling_fingerprint="sha256:tool",
            ready=True,
        ),
        created_at=datetime(2026, 9, 16, tzinfo=UTC),
    )

    export = package.manifest["export"]
    assert export["analysis_failed_object_count"] == 1
    assert export["analysis_failures"] == [
        {
            "object_id": "function:app.FN_GET_ERROR_MESSAGE",
            "object_type": "function",
            "stage": "analysis",
            "error_code": "DEFINITION_UNAVAILABLE",
            "message": "Stored function definition is unavailable.",
        }
    ]
