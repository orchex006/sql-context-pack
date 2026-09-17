import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sqlctx.core.enums import DatabaseEngine, JobStatus, MaterializationMode, ObjectType
from sqlctx.core.models import (
    AssembledFile,
    ConnectionProfileDescriptor,
    ExportJob,
    ExportObjectCounts,
    ExportStatus,
    MaterializationSelection,
    ResolvedConnectionProfile,
)


def test_resolved_profile_never_serializes_or_leaks_repr() -> None:
    profile = ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.POSTGRES,
        host="db.internal",
        port=5432,
        database="secret_db",
        username="secret_user",
        password="secret_password",
        allowed_schemas=("public",),
        allowed_object_types=(ObjectType.TABLE,),
    )

    rendered = repr(profile)
    assert "secret_password" not in rendered
    assert "secret_user" not in rendered
    assert "db.internal" not in rendered
    assert "[REDACTED]" in rendered
    with pytest.raises(TypeError):
        json.dumps(profile)


def test_public_profile_has_no_credential_fields() -> None:
    descriptor = ConnectionProfileDescriptor(
        name="demo",
        engine="postgres",
        allowed_schemas=["public"],
        allowed_object_types=["table"],
        ready=True,
    )
    payload = descriptor.model_dump()
    forbidden = {"host", "username", "password", "connection_string", "database"}
    assert forbidden.isdisjoint(payload)


def test_selected_mode_requires_categories() -> None:
    with pytest.raises(ValidationError):
        MaterializationSelection(mode=MaterializationMode.SELECTED)


def test_assembled_file_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        AssembledFile(relative_path="../secret", size_bytes=1, sha256="sha256:x")


def test_export_status_accepts_every_retained_job_field() -> None:
    job = ExportJob(
        export_id="exp_1",
        catalog_id="cat_1",
        status=JobStatus.RUNNING,
        request_fingerprint="sha256:req",
        object_batch_fingerprint="sha256:batch",
        python_executable_fingerprint="sha256:python",
        python_version="3.13.14",
        sqlfluff_version="4.2.2",
        tooling_fingerprint="sha256:tooling",
        requested_object_count=10,
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    status = ExportStatus(
        **job.model_dump(mode="json"),
        objects=ExportObjectCounts(requested=10, succeeded=0, parse_failed=0, failed=0),
    )
    assert status.created_at == job.created_at
    assert status.output_profile == "ai"
