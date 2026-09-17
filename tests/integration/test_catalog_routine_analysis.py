"""SQL Server routine analysis: banner comments, OUTPUT/@@PROCID, and named failures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlctx.adapters.sqlserver import SqlServerAdapter
from sqlctx.application.catalog import CatalogRequest, CatalogService
from sqlctx.core.enums import DatabaseEngine, MaterializationMode, ObjectType
from sqlctx.core.models import MaterializationSelection, ResolvedConnectionProfile
from sqlctx.security.masking import DeterministicMaskingEngine
from sqlctx.security.runtime import EncryptedSnapshotSecretStore, JsonRuntimeStateStore

BANNER = (
    "-- =============================================\r\n"
    "-- Author       : owner\r\n"
    "-- Description  : legacy banner above the declaration\r\n"
    "-- =============================================\r\n"
)

# Repro A: OUTPUT parameter. Repro B: @@PROCID. Both behind the banner these routines really use.
OUTPUT_PROCEDURE = BANNER + (
    "CREATE PROCEDURE [app].[ZZ_PROBE_OUTPUT]\r\n"
    "    @PI_X INT = NULL,\r\n"
    "    @PO_STATUS_MSG NVARCHAR(400) OUTPUT\r\n"
    "AS\r\nBEGIN\r\n"
    "    SET NOCOUNT ON;\r\n"
    "    BEGIN TRY\r\n        SET @PO_STATUS_MSG = N'ok';\r\n    END TRY\r\n"
    "    BEGIN CATCH\r\n        SET @PO_STATUS_MSG = OBJECT_NAME(@@PROCID);\r\n    END CATCH\r\n"
    "END"
)
# Repro C: a scalar function, also behind a banner.
SCALAR_FUNCTION = BANNER + (
    "CREATE FUNCTION [app].[FN_GET_ERROR_MESSAGE] (@PI_N INT)\r\n"
    "RETURNS NVARCHAR(200)\r\n"
    "AS\r\nBEGIN\r\n    RETURN N'probe-' + CAST(@PI_N AS NVARCHAR(20));\r\nEND"
)

DEFINITIONS: dict[str, str] = {
    "ZZ_PROBE_OUTPUT": OUTPUT_PROCEDURE,
    "FN_GET_ERROR_MESSAGE": SCALAR_FUNCTION,
}


class RoutineCursor:
    """Answer exactly the SQL Server adapter queries this catalog run issues."""

    def __init__(self, definitions: dict[str, str]) -> None:
        self.definitions = definitions
        self.description: list[tuple[str]] = []
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, parameters: Any = ()) -> None:
        normalized = " ".join(query.lower().split())
        if normalized.startswith("set "):
            self.description, self.rows = [], []
        elif " as object_type" in normalized:
            self.description = [("object_name",), ("object_type",)]
            self.rows = [("ZZ_PROBE_OUTPUT", "procedure"), ("FN_GET_ERROR_MESSAGE", "function")]
        elif "from sys.sql_modules" in normalized:
            object_name = str(parameters[1])
            definition = self.definitions.get(object_name)
            self.description = [("definition",)]
            self.rows = [(definition,)] if definition is not None else []
        elif "target_object_id" in normalized:
            self.description, self.rows = [("target_object_id",), ("edge_type",)], []
        else:
            self.description, self.rows = [("version",)], [("test",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class RoutineConnection:
    def __init__(self, definitions: dict[str, str]) -> None:
        self.definitions = definitions

    def cursor(self) -> RoutineCursor:
        return RoutineCursor(self.definitions)

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def resolved_profile() -> ResolvedConnectionProfile:
    return ResolvedConnectionProfile(
        name="demo",
        engine=DatabaseEngine.SQLSERVER,
        host="localhost",
        port=1433,
        database="demo",
        username="user",
        password="password",
        allowed_schemas=("app",),
        allowed_object_types=(ObjectType.PROCEDURE, ObjectType.FUNCTION),
    )


def run_catalog(tmp_path: Path, definitions: dict[str, str]) -> Any:
    state = JsonRuntimeStateStore(tmp_path / "runtime")
    service = CatalogService(state, DeterministicMaskingEngine(EncryptedSnapshotSecretStore(state)))
    adapter = SqlServerAdapter(lambda _: RoutineConnection(definitions))
    request = CatalogRequest(
        profile="demo",
        schemas=["app"],
        object_types=["procedure", "function"],
    )
    accepted = service.create(request, resolved_profile(), adapter)
    return (
        service.select(accepted.catalog_id, MaterializationSelection(mode=MaterializationMode.ALL)),
        service,
        accepted.catalog_id,
    )


def test_banner_commented_routines_analyze_instead_of_failing(tmp_path: Path) -> None:
    """Repros A, B and C: an SSMS banner comment no longer costs the object its analysis."""
    status, service, catalog_id = run_catalog(tmp_path, DEFINITIONS)

    assert status.discovered_object_count == 2
    assert status.fully_analyzed_object_count == 2
    assert status.analysis_failed_object_count == 0
    assert status.failures == []

    definitions = {
        item.ref.object_name: item.sanitized_definition
        for item in service._snapshot(catalog_id).objects
    }
    procedure = definitions["ZZ_PROBE_OUTPUT"] or ""
    assert procedure.startswith(BANNER + "CREATE OR ALTER PROCEDURE")
    assert "@PO_STATUS_MSG NVARCHAR(400) OUTPUT" in procedure
    assert "OBJECT_NAME(@@PROCID)" in procedure
    assert (definitions["FN_GET_ERROR_MESSAGE"] or "").startswith(
        BANNER + "CREATE OR ALTER FUNCTION"
    )


def test_analysis_failures_name_the_object_stage_and_reason(tmp_path: Path) -> None:
    """A failure is still a failure, but it is now reportable instead of an anonymous count."""
    status, _, _ = run_catalog(tmp_path, {"ZZ_PROBE_OUTPUT": OUTPUT_PROCEDURE})

    assert status.analysis_failed_object_count == 1
    assert status.fully_analyzed_object_count == 1
    assert status.discovered_object_count == 2

    failure = next(item for item in status.failures)
    assert failure.object_id == "function:app.FN_GET_ERROR_MESSAGE"
    assert failure.object_type == ObjectType.FUNCTION
    assert failure.stage == "analysis"
    assert failure.error_code == "DEFINITION_UNAVAILABLE"
    assert failure.message == "Stored function definition is unavailable."


def test_failure_messages_carry_no_credentials_paths_or_sql_bodies(tmp_path: Path) -> None:
    """The reported reason must stay safe to hand to an agent or paste into a ticket."""
    from sqlctx.application.catalog import _safe_failure_message

    unsafe = (
        "Failed on C:\\Users\\owner\\secrets\\profiles.yaml and /etc/sqlctx/master.key while "
        "running password='hunter2'\nCREATE OR ALTER PROCEDURE [app].[P] AS SELECT 1;"
    )

    safe = _safe_failure_message(unsafe)

    assert "hunter2" not in safe
    assert "C:\\Users" not in safe
    assert "/etc/sqlctx" not in safe
    assert "SELECT 1" not in safe
    assert len(safe) <= 200
