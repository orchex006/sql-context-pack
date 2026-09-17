"""Owner-local control and deterministic bundle transfer CLI."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, cast

import httpx
import typer

from sqlctx.adapters.registry import create_adapter, dialect_map
from sqlctx.context_index.contracts import (
    ContextGenerationRequest,
    ContextIndexEntry,
    ContextIndexListRequest,
    ContextIndexSyncRequest,
)
from sqlctx.core.enums import DatabaseEngine, FormatStatus, ObjectType
from sqlctx.core.errors import SqlCtxError, ToolingUnavailable
from sqlctx.core.models import ExportArtifact, ExportStatus
from sqlctx.doctor import inspect_installation, source_version
from sqlctx.exporting.assembly import assemble_bundles
from sqlctx.exporting.validation import inventory_output, validate_bundle
from sqlctx.exporting.writer import sha256_bytes
from sqlctx.formatting.formatter import SqlFluffFormatter
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.managed_files.contracts import (
    FolderClassificationPlanRequest,
    ManagedFileResolutionPlanRequest,
    OwnerFileResolution,
)
from sqlctx.routine_deploy.contracts import RoutinePlanRequest
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.profiles import YamlConnectionProfileRepository
from sqlctx.security.runtime import CredentialMetadataStore, JsonRuntimeStateStore
from sqlctx.security.transport import agent_connection
from sqlctx.server.facade import ServiceFacade

FORMAT_FILE_MAX_BYTES = 1_048_576

app = typer.Typer(help="Build and validate sanitized SQL context packages.", no_args_is_help=True)
approvals_app = typer.Typer(help="Grant request-bound privileged operation approvals.")
export_app = typer.Typer(help="Fetch integrity-protected export bundles.")
validate_app = typer.Typer(help="Validate locally assembled managed output.")
sqlfluff_app = typer.Typer(help="Verify and owner-manage SQLFluff on this exact host Python.")
harness_app = typer.Typer(
    help="Launch a supported harness with protected agent connection metadata."
)
profile_app = typer.Typer(help="Configure owner-local encrypted database profiles.")
audit_app = typer.Typer(help="Inspect sanitized owner-local operation audit events.")
runtime_app = typer.Typer(help="Inspect protected retained jobs and clean expired artifacts.")
folder_app = typer.Typer(help="Register, classify, and materialize owner-controlled SQL folders.")
context_index_app = typer.Typer(help="Manage the DB_METADATA_CONTEXT database index.")
routine_app = typer.Typer(help="Plan and apply safe Stored Procedure/Function updates.")
service_app = typer.Typer(help="Manage the single shared loopback service on any host OS.")
app.add_typer(approvals_app, name="approvals")
app.add_typer(export_app, name="export")
app.add_typer(validate_app, name="validate")
app.add_typer(sqlfluff_app, name="sqlfluff")
app.add_typer(harness_app, name="harness")
app.add_typer(profile_app, name="profile")
app.add_typer(audit_app, name="audit")
app.add_typer(runtime_app, name="runtime")
app.add_typer(folder_app, name="folder")
app.add_typer(context_index_app, name="context-index")
app.add_typer(routine_app, name="routine")
app.add_typer(service_app, name="service")


@app.command("doctor")
def doctor(
    action: Annotated[Literal["status", "version", "check", "update"], typer.Argument()] = "status",
    host: Annotated[Literal["codex", "claude", "gemini", "agy"], typer.Option("--host")] = "codex",
    source: Annotated[Path | None, typer.Option("--source")] = None,
    online: Annotated[
        bool,
        typer.Option(
            "--online", help="Read the latest GitHub release without changing local files."
        ),
    ] = False,
    mcp: Annotated[bool, typer.Option("--mcp", help="Probe authenticated MCP readiness.")] = False,
    version: Annotated[
        str | None,
        typer.Option("--version", help="Exact released version to install, e.g. 2.1.0."),
    ] = None,
) -> None:
    """Inspect versions/readiness, or explicitly update the selected host and shared runtime."""
    if action == "update":
        product_update(source=source, host=host, version=version)
        return
    if version is not None:
        raise typer.BadParameter("--version applies to `doctor update` only.")
    if action == "version":
        from sqlctx._version import __version__

        typer.echo(json.dumps({"package_version": __version__, "host": host}))
        return
    result = inspect_installation(source=source, host=host, online=online)
    legacy, ready = _doctor_tooling(mcp=mcp)
    result.update(legacy)
    typer.echo(json.dumps(result, sort_keys=True))
    if not ready:
        raise typer.Exit(code=1)


@service_app.command("status")
def service_status(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Report whether the one shared service is running, and which supervisor owns it."""
    _service_operation("status", port=port)


@service_app.command("start")
def service_start(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Start the shared service if it is not already running."""
    _service_operation("start", port=port)


@service_app.command("stop")
def service_stop(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Stop the shared service. Harness sessions reconnect once it is started again."""
    _service_operation("stop", port=port)


@service_app.command("install")
def service_install(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Register the shared service with this host's supervisor and start it."""
    _service_operation("install", port=port)


@service_app.command("remove")
def service_remove(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Stop the shared service and deregister it from this host's supervisor."""
    _service_operation("remove", port=port)


def _service_operation(operation: str, *, port: int) -> None:
    """Run one supervisor operation and surface the mode that actually handled it.

    There is exactly one service per machine; every harness session reaches it through
    its own stdio bridge. These commands never start a second instance.
    """
    from sqlctx.service.manager import Operation, detect_host_os, manage

    result = manage(
        cast(Operation, operation),
        python=Path(sys.executable).resolve(),
        port=port,
        host_os=detect_host_os(),
    )
    typer.echo(json.dumps(result, sort_keys=True))
    if operation in {"install", "start"} and result.get("status") not in {"running", "starting"}:
        raise typer.Exit(code=1)


@app.command("session-hook", hidden=True)
def session_hook() -> None:
    """Inject safe session-profile guidance for a Codex SessionStart hook."""
    typer.echo(
        json.dumps(
            {
                "continue": True,
                "systemMessage": (
                    "SQL Context Pack starts disconnected for this session. "
                    "Use $sql-context-pack profiles and connect before database work; "
                    "never assume an active profile from another room."
                ),
            },
            sort_keys=True,
        )
    )


@app.command("update")
def product_update(
    source: Annotated[
        Path | None,
        typer.Option(
            "--source", help="Trusted local release checkout; defaults to install provenance."
        ),
    ] = None,
    host: Annotated[Literal["codex", "claude", "gemini", "agy"], typer.Option("--host")] = "codex",
    version: Annotated[
        str | None,
        typer.Option("--version", help="Exact released version to install, e.g. 2.1.0."),
    ] = None,
) -> None:
    """Update the package, plugin, MCP bridge, hook, and managed service as one owner operation."""
    if version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SqlCtxError(
            "UPDATE_VERSION_INVALID",
            "--version must be an exact released product version such as 2.1.0.",
        )
    selected = (source or _installed_source_root()).resolve()
    typer.echo(f"[1/2] Refreshing trusted Git source: {selected}")
    _refresh_trusted_checkout(selected, version=version)
    expected_version = source_version(selected)
    if expected_version is None:
        raise SqlCtxError(
            "UPDATE_VERSION_INVALID", "The update source has no valid product version."
        )
    if version is not None and expected_version != version:
        # The tag exists but does not carry the version it claims: stop rather than
        # install something other than what the owner asked for.
        raise SqlCtxError(
            "UPDATE_VERSION_MISMATCH",
            f"Tag v{version} declares product version {expected_version}.",
        )
    installer, shell_arguments = _installer_command(selected)
    if not installer.is_file():
        raise SqlCtxError(
            "UPDATE_SOURCE_INVALID",
            f"The trusted update source does not contain {installer.name}.",
        )
    typer.echo(
        "[2/2] Installing refreshed package/plugin files, restarting the managed service, "
        "verifying health, and rolling back on failure."
    )
    if sys.platform == "win32":
        typer.echo(
            "Windows may request Administrator access only for service registration and the ProgramData install root."
        )
    result = subprocess.run(  # noqa: S603 - owner-selected shell and validated local script.
        [*shell_arguments, str(installer), *_installer_flags(selected, host)],
        check=False,
    )
    if result.returncode == 0:
        # Probe in the updated interpreter: this process still has the old modules loaded.
        verified = subprocess.run(  # noqa: S603 - fixed interpreter and closed module/host selection.
            [sys.executable, "-B", "-m", "sqlctx.cli", "doctor", "check", "--host", host],
            check=False,
            capture_output=True,
            text=True,
        )  # noqa: S603 - fixed interpreter/module and closed host selection.
        try:
            versions = json.loads(verified.stdout)["versions"]
            if verified.returncode != 0 or any(
                versions.get(key) != expected_version for key in ("package", "service", "plugin")
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise SqlCtxError(
                "UPDATE_VERIFICATION_FAILED",
                "Installation returned success but package/plugin/service versions do not match. Run sqlctx doctor check.",
            ) from exc
        typer.echo(
            f"Verified package/plugin/service {expected_version}. Open a new host session to load the updated Skill."
        )
    raise typer.Exit(code=result.returncode)


@app.command("repair")
def product_repair(
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Trusted local checkout; defaults to installed provenance."),
    ] = None,
    component: Annotated[
        str,
        typer.Option(
            "--component",
            help="Repair scope: auto, mcp, package, or service.",
        ),
    ] = "auto",
) -> None:
    """Reinstall and health-check package/plugin/service content after an interrupted install."""
    if sys.platform != "win32":
        raise SqlCtxError(
            "WINDOWS_REPAIR_REQUIRED",
            "Managed service repair is currently available on Windows only.",
        )
    if component not in {"auto", "mcp", "package", "service"}:
        raise SqlCtxError(
            "INVALID_REPAIR_COMPONENT",
            "Repair component must be auto, mcp, package, or service.",
        )
    selected = source or _installed_source_root()
    installer = selected.resolve() / "install.ps1"
    if not installer.is_file():
        raise SqlCtxError(
            "REPAIR_SOURCE_INVALID", "The repair source does not contain install.ps1."
        )
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise SqlCtxError("POWERSHELL_UNAVAILABLE", "PowerShell is required for product repair.")
    typer.echo(
        "Repair will restage package/plugin/service files, preserve profiles, restart the service, and verify authenticated health."
    )
    typer.echo(
        "Windows may request Administrator access only for service registration and protected ProgramData files."
    )
    result = subprocess.run(  # noqa: S603 - validated owner-selected local installer.
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-Repair",
            "-SkipConfigure",
            "-RepairComponent",
            component,
        ],
        check=False,
    )
    raise typer.Exit(code=result.returncode)


def _installer_command(source: Path) -> tuple[Path, list[str]]:
    """Pick the installer and interpreter for this host OS.

    Windows keeps the PowerShell installer because service registration goes through the
    SCM. Every other platform uses the POSIX installer, which drives the same
    scripts/service-manager.py supervisors.
    """
    if sys.platform == "win32":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise SqlCtxError(
                "POWERSHELL_UNAVAILABLE", "PowerShell is required for product update on Windows."
            )
        return source / "install.ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        raise SqlCtxError("SHELL_UNAVAILABLE", "A POSIX shell is required for product update.")
    return source / "install.sh", [shell]


def _installer_flags(source: Path, host: str) -> list[str]:
    if sys.platform == "win32":
        return ["-Update", "-Harness", host]
    return ["--update", "--harness", host]


def _refresh_trusted_checkout(source: Path, *, version: str | None = None) -> None:
    """Fast-forward the recorded checkout, or pin it to one released tag, without merges."""
    if not (source / ".git").exists():
        raise SqlCtxError(
            "UPDATE_SOURCE_REQUIRED",
            "Recorded source is not a Git checkout; pass `--source <release-checkout>`.",
        )
    git = shutil.which("git")
    if git is None:
        raise SqlCtxError("GIT_UNAVAILABLE", "Git is required to refresh the recorded checkout.")

    def inspect(*arguments: str) -> str:
        result = subprocess.run(  # noqa: S603 - fixed git inspection arguments.
            [git, "-C", str(source), *arguments], check=False, capture_output=True, text=True
        )  # noqa: S603 - fixed git inspection arguments.
        if result.returncode != 0:
            raise SqlCtxError("UPDATE_SOURCE_INVALID", "Could not verify the update checkout.")
        return result.stdout.strip()

    allowed = {
        "https://github.com/orchex006/sql-context-pack",
        "https://github.com/orchex006/sql-context-pack.git",
        "git@github.com:orchex006/sql-context-pack.git",
    }
    if inspect("remote", "get-url", "origin") not in allowed:
        raise SqlCtxError(
            "UPDATE_REMOTE_NOT_TRUSTED",
            "Update origin must be the canonical orchex006/sql-context-pack repository.",
        )
    if version is None and (
        inspect("branch", "--show-current") != "main"
        or inspect("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") != "origin/main"
    ):
        raise SqlCtxError("UPDATE_BRANCH_NOT_TRUSTED", "Update requires main tracking origin/main.")
    if inspect("status", "--porcelain", "--untracked-files=all"):
        raise SqlCtxError(
            "UPDATE_SOURCE_DIRTY",
            "Commit or move local changes before update; use repair for an explicitly reviewed local build.",
        )
    if version is not None:
        _checkout_release_tag(git, source, version, inspect)
        return
    typer.echo("Running Git pull --ff-only to download and fast-forward the tracked source branch.")
    result = subprocess.run(  # noqa: S603 - resolved Git executable and fixed arguments.
        [git, "-C", str(source), "pull", "--ff-only"],
        check=False,
    )
    if result.returncode != 0:
        raise SqlCtxError(
            "UPDATE_SOURCE_REFRESH_FAILED",
            "The recorded checkout could not fast-forward; no installation was started.",
        )
    typer.echo("Git source refresh completed; installation will use the refreshed checkout.")


def _checkout_release_tag(
    git: str, source: Path, version: str, inspect: Callable[..., str]
) -> None:
    """Check out exactly the requested release tag, or stop.

    An unknown version is a stop, never a quiet fall back to the latest release: the owner
    asked for a specific version and installing a different one is worse than failing.
    """
    tag = f"v{version}"
    typer.echo(f"Fetching release tags to pin the source at {tag}.")
    fetched = subprocess.run(  # noqa: S603 - resolved Git executable and fixed arguments.
        [git, "-C", str(source), "fetch", "--tags", "--force", "origin"],
        check=False,
    )
    if fetched.returncode != 0:
        raise SqlCtxError(
            "UPDATE_SOURCE_REFRESH_FAILED",
            "Release tags could not be fetched; no installation was started.",
        )
    known = subprocess.run(  # noqa: S603 - resolved Git executable and fixed arguments.
        [git, "-C", str(source), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if known.returncode != 0:
        available = [
            line.strip()
            for line in inspect("tag", "--list", "v*", "--sort=-v:refname").splitlines()
            if line.strip()
        ][:10]
        raise SqlCtxError(
            "UPDATE_VERSION_NOT_FOUND",
            f"Release {tag} does not exist in the trusted repository.",
            details={"requested": version, "available": available},
        )
    checked_out = subprocess.run(  # noqa: S603 - resolved Git executable and fixed arguments.
        [git, "-C", str(source), "checkout", "--detach", f"refs/tags/{tag}"],
        check=False,
    )
    if checked_out.returncode != 0:
        raise SqlCtxError(
            "UPDATE_SOURCE_REFRESH_FAILED",
            f"The recorded checkout could not move to {tag}; no installation was started.",
        )
    typer.echo(f"Source pinned at {tag}; installation will use that exact release.")


def _installed_source_root() -> Path:
    candidates = [
        Path.home() / "plugins/sql-context-pack/.sqlctx-install.json",
        Path.home() / ".codex/skills/sql-context-pack/.sqlctx-install.json",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            source = Path(str(payload["source_root"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SqlCtxError(
                "UPDATE_PROVENANCE_INVALID", "Installed update provenance is unreadable."
            ) from exc
        return source
    raise SqlCtxError(
        "UPDATE_SOURCE_REQUIRED",
        "No trusted install provenance exists; rerun with `sqlctx update --source <release-checkout>`.",
    )


@app.command("launch")
def launch(
    harness: Annotated[
        str, typer.Option("--harness", help="codex, claude, gemini, or agy")
    ] = "codex",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Exact safe profile name")
    ] = None,
) -> None:
    """Configure when needed, start the owner service, and launch one protected harness."""
    repository = YamlConnectionProfileRepository()
    profiles = repository.list_descriptors()
    if not profiles:
        if not sys.stdin.isatty():
            raise SqlCtxError(
                "PROFILE_SETUP_REQUIRED",
                "Run this command in an interactive owner terminal to configure the first profile.",
            )
        typer.echo("No database profile exists; starting secure profile setup.")
        from sqlctx.cli.configure import main as configure_main

        configure_main()
        profiles = repository.list_descriptors()
    ready_profiles = [item.name for item in profiles if item.ready]
    if not ready_profiles:
        raise SqlCtxError(
            "PROFILE_NOT_READY",
            "No configured profile is ready; run `sqlctx profile list` for safe details.",
            status_code=503,
        )
    if profile is None:
        if len(ready_profiles) != 1:
            typer.echo(json.dumps({"ready_profiles": ready_profiles}, sort_keys=True))
            raise SqlCtxError(
                "PROFILE_SELECTION_REQUIRED",
                "Multiple profiles are ready; rerun launch with `--profile <exact-name>`.",
            )
        profile = ready_profiles[0]
    if profile not in ready_profiles:
        raise SqlCtxError(
            "PROFILE_NOT_READY",
            "The selected profile is unknown or not ready; run `sqlctx profile list`.",
            status_code=503,
        )
    try:
        resolved = repository.resolve(profile)
        create_adapter(resolved.engine).test_connection(resolved)
    except SqlCtxError as exc:
        typer.echo(
            json.dumps(
                {
                    "profile": profile,
                    "reachable": False,
                    "code": exc.code,
                    "message": exc.message,
                },
                sort_keys=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps({"profile": profile, "reachable": True}, sort_keys=True))

    owned_server: subprocess.Popen[bytes] | None = None
    if not _port_is_listening(port):
        typer.echo(f"Starting owner-scoped SQL Context Pack service on 127.0.0.1:{port}.")
        owned_server = subprocess.Popen(  # noqa: S603 - exact selected host interpreter/module.
            [
                sys.executable,
                "-m",
                "sqlctx.server.http.app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
        )
        for _ in range(40):
            if owned_server.poll() is not None:
                raise SqlCtxError("SERVER_START_FAILED", "The local service exited during startup.")
            if _port_is_listening(port):
                break
            time.sleep(0.25)
        else:
            owned_server.terminate()
            raise SqlCtxError("SERVER_START_FAILED", "The local service did not become ready.")
    try:
        arguments, child_environment = _harness_invocation(harness)
        typer.echo(f"Launching {harness}; MCP credentials remain in the child environment only.")
        result = subprocess.run(  # noqa: S603 - executable comes from a closed harness allowlist.
            arguments, env=child_environment, check=False
        )
        raise typer.Exit(code=result.returncode)
    finally:
        if owned_server is not None and owned_server.poll() is None:
            owned_server.terminate()
            try:
                owned_server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                owned_server.kill()
                owned_server.wait(timeout=5)


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


@audit_app.command("tail")
def audit_tail(
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 50,
) -> None:
    """Show recent sanitized MCP operations without arguments or credential values."""
    state = JsonRuntimeStateStore()
    root = state._safe("audit/events")
    paths = sorted(root.rglob("*.json"))[-limit:] if root.is_dir() else []
    events = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    typer.echo(json.dumps({"count": len(events), "events": events}, sort_keys=True))


@runtime_app.command("status")
def runtime_status() -> None:
    """Show protected runtime location, retained counts, size, and approval state."""
    state = JsonRuntimeStateStore()
    files = (
        [path for path in state.root.rglob("*") if path.is_file()] if state.root.exists() else []
    )
    approvals = ApprovalService(state=state).list_challenges()
    typer.echo(
        json.dumps(
            {
                "runtime_root": str(state.root),
                "protected": True,
                "total_files": len(files),
                "total_bytes": sum(path.stat().st_size for path in files),
                "catalog_count": len(list(state._safe("catalogs").glob("*/record.json"))),
                "export_count": len(list(state._safe("exports").glob("*/job.json"))),
                "approval_counts": {
                    status: sum(item["status"] == status for item in approvals)
                    for status in ("pending", "granted", "consumed", "expired")
                },
                "retention_hours": 24,
                "cleanup_policy": (
                    "Temporary build/format/assembly directories use finally-scoped OS temp paths. "
                    "Retained catalogs, masking snapshots, exports, and approvals live only under "
                    "runtime_root; expired inactive artifacts are cleanup-eligible."
                ),
                "cleanup_command": "sqlctx runtime cleanup-expired",
            },
            sort_keys=True,
        )
    )


@runtime_app.command("cleanup-expired")
def runtime_cleanup_expired() -> None:
    """Remove expired inactive jobs, snapshots, and terminal approval records."""
    from sqlctx.server.facade import ServiceFacade

    service = ServiceFacade()
    exports = service.exports.cleanup_expired()
    catalogs = service.catalogs.cleanup_expired()
    approvals = service.approvals.cleanup_expired()
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "removed_exports": exports,
                "removed_catalogs": catalogs,
                "removed_approvals": approvals,
                "runtime_root": str(service.state.root),
                "active_or_unexpired_data_preserved": True,
            },
            sort_keys=True,
        )
    )


@app.command("sync-data")
def sync_data(
    profile: Annotated[
        list[str] | None,
        typer.Option(
            "--profile",
            help="Restrict synchronization to a configured retained profile; repeat as needed.",
        ),
    ] = None,
) -> None:
    """Refresh eligible retained catalog data and cache without changing exported files."""
    from sqlctx.server.facade import ServiceFacade

    result = ServiceFacade().sync_data(profile_names=profile or [])
    typer.echo(json.dumps(result.model_dump(mode="json", by_alias=True), sort_keys=True))


@app.command("query")
def query_data(
    sql: Annotated[str, typer.Argument(help="One validated read-only SELECT query")],
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Exact ready profile; optional only when one is ready"),
    ] = None,
    max_rows: Annotated[
        int | None,
        typer.Option("--max-rows", min=1, max=500, help="Bounded Markdown row limit"),
    ] = None,
    all_rows: Annotated[
        bool,
        typer.Option(
            "--all-rows",
            help="Stream all rows to stdout without a sqlctx row-count cap (CLI only)",
        ),
    ] = False,
    value_mode: Annotated[
        str,
        typer.Option("--value-mode", help="short (default) or full masked text"),
    ] = "short",
) -> None:
    """Run one validated relational SELECT and print masked Markdown only."""
    if all_rows and max_rows is not None:
        raise SqlCtxError(
            "QUERY_ROW_OPTIONS_CONFLICT", "Use either --max-rows or --all-rows, not both."
        )
    if value_mode not in {"short", "full"}:
        raise SqlCtxError("QUERY_VALUE_MODE_INVALID", "Value mode must be short or full.")
    from sqlctx.query_data.contracts import QueryDataRequest, ValueMode
    from sqlctx.server.facade import ServiceFacade

    service = ServiceFacade()
    selected = service.resolve_query_profile(profile)
    command = QueryDataRequest(
        profile=selected,
        sql=sql,
        max_rows=max_rows or 100,
        value_mode=cast(ValueMode, value_mode),
    )
    if all_rows:
        stream = service.stream_query(command)
        try:
            for line in stream:
                typer.echo(line)
        except SqlCtxError as exc:
            typer.echo(json.dumps({"error": exc.public_payload()}, sort_keys=True), err=True)
            raise typer.Exit(code=2) from None
        except Exception:
            correlation_id = "corr_" + secrets.token_urlsafe(12)
            state = JsonRuntimeStateStore()
            state.write_json(
                f"errors/{correlation_id}.json",
                {"correlation_id": correlation_id, "traceback": traceback.format_exc()},
            )
            typer.echo(
                json.dumps(
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "The command could not complete. No traceback was printed.",
                            "correlation_id": correlation_id,
                            "owner_action": "Run `sqlctx runtime status`; protected diagnostics are under runtime_root/errors.",
                        }
                    },
                    sort_keys=True,
                ),
                err=True,
            )
            raise typer.Exit(code=2) from None
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return
    result = service.query(command)
    typer.echo(result.markdown, nl=False)
    if result.truncated:
        typer.echo(
            f"\n> **sqlctx:** Result truncated ({result.truncation_reason}); "
            f"returned {result.returned_row_count} rows."
        )


@app.command("format")
def format_sql(
    file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local .sql file to read; the file itself is never rewritten",
        ),
    ],
    dialect: Annotated[
        str,
        typer.Option("--dialect", help="SQLFluff dialect; defaults to ansi"),
    ] = "ansi",
) -> None:
    """Format one local SQL file and print the verified result without rewriting the source."""
    allowed_dialects = {"ansi", *dialect_map().values()}
    if dialect not in allowed_dialects:
        raise SqlCtxError(
            "FORMAT_DIALECT_UNKNOWN",
            f"Dialect must be one of {', '.join(sorted(allowed_dialects))}.",
        )
    if file.suffix.lower() != ".sql":
        raise SqlCtxError("FORMAT_FILE_UNSUPPORTED", "Only a local .sql file can be formatted.")
    if file.stat().st_size > FORMAT_FILE_MAX_BYTES:
        raise SqlCtxError(
            "FORMAT_FILE_TOO_LARGE",
            "The file exceeds the 1 MiB formatting limit; split it and format each part.",
        )
    try:
        sql = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SqlCtxError("FORMAT_FILE_UNREADABLE", "The file is not UTF-8 SQL text.") from None
    manager = SqlFluffManager(JsonRuntimeStateStore())
    descriptor = manager.status()
    if not descriptor.ready:
        raise ToolingUnavailable(
            "Pinned SQLFluff is unavailable; run `sqlctx sqlfluff ensure` in an owner terminal."
        )
    result = SqlFluffFormatter(manager).format_one(
        object_id=f"file:{file.name}", sql=sql, dialect=dialect, tooling=descriptor
    )
    typer.echo(result.content, nl=False)
    if result.status != FormatStatus.FORMATTED:
        typer.echo(
            json.dumps(
                {
                    "status": str(result.status),
                    "file": file.name,
                    "dialect": dialect,
                    "diagnostics": result.diagnostics,
                    "owner_action": "The original SQL was preserved and printed unchanged.",
                },
                sort_keys=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2)


@profile_app.command("configure")
def profile_configure() -> None:
    """Prompt for a profile and store its connection values encrypted at user scope."""
    from sqlctx.cli.configure import main

    main()


@profile_app.command("list")
def profile_list() -> None:
    """List safe profile names and readiness without resolving or printing credentials."""
    profiles = YamlConnectionProfileRepository().list_descriptors()
    typer.echo(
        json.dumps(
            {
                "configured": len(profiles),
                "ready": sum(item.ready for item in profiles),
                "profiles": [item.model_dump(mode="json") for item in profiles],
            },
            sort_keys=True,
        )
    )


@profile_app.command("test")
def profile_test(
    profile: Annotated[str, typer.Argument(help="Exact name from profile list")],
) -> None:
    """Test one profile with safe driver/network/login diagnostics."""
    try:
        resolved = YamlConnectionProfileRepository().resolve(profile)
        adapter = create_adapter(resolved.engine)
        adapter.test_connection(resolved)
    except SqlCtxError as exc:
        typer.echo(
            json.dumps(
                {
                    "profile": profile,
                    "reachable": False,
                    "code": exc.code,
                    "message": exc.message,
                },
                sort_keys=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2) from exc
    typer.echo(
        json.dumps(
            {"profile": profile, "reachable": True, "engine": resolved.engine.value},
            sort_keys=True,
        )
    )


@profile_app.command("trust-certificate")
def profile_trust_certificate(
    profile: Annotated[str, typer.Argument(help="Exact SQL Server profile name")],
    enabled: Annotated[
        bool,
        typer.Option(
            "--enable/--disable",
            help="Explicitly trust or restore verification for this profile's server certificate.",
        ),
    ] = False,
) -> None:
    """Set an explicit per-profile SQL Server TLS certificate policy."""
    repository = YamlConnectionProfileRepository()
    repository.set_trust_server_certificate(profile, enabled)
    typer.echo(
        json.dumps(
            {
                "profile": profile,
                "trust_server_certificate": enabled,
                "scope": "profile",
                "warning": (
                    "Certificate chain verification is bypassed for this development profile."
                    if enabled
                    else None
                ),
            },
            sort_keys=True,
        )
    )


@profile_app.command("schemas")
def profile_schemas(
    profile: Annotated[str, typer.Argument(help="Exact configured profile name")],
) -> None:
    """List database-visible schemas and identify the explicit profile allowlist."""
    resolved = YamlConnectionProfileRepository().resolve(profile)
    visible = create_adapter(resolved.engine).list_visible_schemas(resolved)
    typer.echo(
        json.dumps(
            {
                "profile": profile,
                "visible_schemas": visible,
                "allowed_schemas": list(resolved.allowed_schemas),
                "visible_not_allowed": [
                    schema for schema in visible if schema not in resolved.allowed_schemas
                ],
            },
            sort_keys=True,
        )
    )


@profile_app.command("scope")
def profile_scope(
    profile: Annotated[str, typer.Argument(help="Exact configured profile name")],
    schemas: Annotated[
        list[str], typer.Option("--schema", help="Allowed schema; repeat for multiple schemas.")
    ],
    excludes: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Excluded object-name glob; repeat as needed."),
    ] = None,
    object_types: Annotated[
        list[str] | None,
        typer.Option(
            "--object-type",
            help="Allowed object type: table, procedure, or function; repeat as needed.",
        ),
    ] = None,
) -> None:
    """Set an explicit schema allowlist and metadata object exclusion policy."""
    repository = YamlConnectionProfileRepository()
    excluded_patterns = excludes or []
    allowed_types: list[ObjectType] | None = None
    if object_types:
        unknown = sorted(set(object_types) - {item.value for item in ObjectType})
        if unknown:
            raise SqlCtxError(
                "OBJECT_TYPE_UNKNOWN",
                f"Object types must be one of {', '.join(item.value for item in ObjectType)}.",
                details={"unknown_object_types": unknown},
            )
        allowed_types = [ObjectType(item) for item in dict.fromkeys(object_types)]
    repository.set_schema_policy(
        profile,
        allowed_schemas=schemas,
        excluded_object_patterns=excluded_patterns,
        allowed_object_types=allowed_types,
    )
    typer.echo(
        json.dumps(
            {
                "profile": profile,
                "allowed_schemas": schemas,
                "excluded_object_patterns": excluded_patterns,
                "allowed_object_types": [item.value for item in allowed_types]
                if allowed_types
                else "unchanged",
                "owner_action": "Run sqlctx profile test and profile schemas to verify the new scope.",
            },
            sort_keys=True,
        )
    )


@profile_app.command("remove")
def profile_remove(
    profile: Annotated[str, typer.Argument(help="Exact configured profile name")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm removal of this profile definition."),
    ] = False,
    keep_credentials: Annotated[
        bool,
        typer.Option(
            "--keep-credentials",
            help="Preserve the protected credential record even when no profile references it.",
        ),
    ] = False,
) -> None:
    """Remove one profile and its unshared protected credential record."""
    if not yes:
        raise SqlCtxError(
            "CONFIRMATION_REQUIRED",
            "Profile removal requires `--yes` and an exact profile name.",
            status_code=400,
        )
    result = YamlConnectionProfileRepository().remove(
        profile, remove_credentials=not keep_credentials
    )
    typer.echo(json.dumps(result, sort_keys=True))


def _doctor_tooling(
    mcp: Annotated[
        bool,
        typer.Option("--mcp", help="Probe the authenticated MCP bridge upstream end to end."),
    ] = False,
) -> tuple[dict[str, object], bool]:
    """Verify host Python, pinned SQLFluff, protected server metadata, and safe profiles."""
    state = JsonRuntimeStateStore()
    tooling = SqlFluffManager(state).status()
    metadata_ready = state._safe("connection-metadata.json").is_file()
    profiles = YamlConnectionProfileRepository().list_descriptors()
    result = {
        "python": {
            "version": tooling.python_version,
            "executable_fingerprint": tooling.python_executable_fingerprint,
            "supported": True,
        },
        "sqlfluff": {
            "version": tooling.sqlfluff_version,
            "ready": tooling.ready,
            "tooling_fingerprint": tooling.tooling_fingerprint,
        },
        "server_metadata_ready": metadata_ready,
        "profiles": {"configured": len(profiles), "ready": sum(item.ready for item in profiles)},
        "creates_python_environment": False,
    }
    mcp_ready = True
    if mcp:
        bridge_path = shutil.which("sqlctx-mcp-bridge")
        probe: dict[str, object] = {
            "bridge_launcher_ready": bridge_path is not None,
            "transport": "stdio_to_authenticated_loopback_http",
            "codex_auth_display": (
                "Auth Unsupported is expected for a STDIO bridge; use end_to_end_ready."
            ),
        }
        try:
            probe.update(_probe_mcp_upstream())
        except Exception as exc:
            probe.update(
                {
                    "end_to_end_ready": False,
                    "error_type": type(exc).__name__,
                    "owner_action": "Run sqlctx repair --component mcp, then open a new Codex room.",
                }
            )
        result["mcp"] = probe
        mcp_ready = bool(probe.get("bridge_launcher_ready")) and bool(probe.get("end_to_end_ready"))
    return result, tooling.ready and mcp_ready


def _probe_mcp_upstream() -> dict[str, object]:
    """List MCP tools through the protected HTTP upstream without exposing credentials."""
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def probe() -> int:
        base_url, token = _connection()
        headers = {"Authorization": f"Bearer {token}"}
        timeout = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=15.0)
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, trust_env=False, follow_redirects=False
        ) as client:
            async with streamable_http_client(base_url + "/mcp", http_client=client) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return len((await session.list_tools()).tools)

    tool_count = anyio.run(probe)
    return {"end_to_end_ready": tool_count > 0, "upstream_tool_count": tool_count}


@sqlfluff_app.command("status")
def sqlfluff_status() -> None:
    """Verify the selected host interpreter and pinned SQLFluff without mutation."""
    descriptor = SqlFluffManager(JsonRuntimeStateStore()).status()
    typer.echo(json.dumps(descriptor.model_dump(mode="json"), sort_keys=True))


@sqlfluff_app.command("ensure")
def sqlfluff_ensure() -> None:
    """Install pinned SQLFluff to the selected base host Python user site after confirmation."""
    manager = SqlFluffManager(JsonRuntimeStateStore())
    current = manager.status()
    if current.ready:
        typer.echo(json.dumps(current.model_dump(mode="json"), sort_keys=True))
        return
    if not sys.stdin.isatty() or not typer.confirm(
        f"Install sqlfluff=={manager.pinned_version} with this exact host Python using --user?",
        default=False,
    ):
        raise typer.Abort()
    descriptor = manager.ensure(approved=True)
    typer.echo(json.dumps(descriptor.model_dump(mode="json"), sort_keys=True))


@sqlfluff_app.command("update")
def sqlfluff_update(version: Annotated[str, typer.Option("--version")]) -> None:
    """Update/rollback SQLFluff through this same idle host interpreter after confirmation."""
    if not sys.stdin.isatty() or not typer.confirm(
        f"Update SQLFluff to {version!r} through this exact host Python?", default=False
    ):
        raise typer.Abort()
    descriptor = SqlFluffManager(JsonRuntimeStateStore()).update(version, approved=True)
    typer.echo(json.dumps(descriptor.model_dump(mode="json"), sort_keys=True))


@harness_app.command(
    "run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def harness_run(
    context: typer.Context,
    harness: Annotated[str, typer.Option("--harness", help="codex, claude, gemini, or agy")],
) -> None:
    """Launch an installed harness with agent metadata injected only into its child process."""
    if harness not in {"codex", "claude", "gemini", "agy"}:
        raise typer.BadParameter("Supported harnesses are codex, claude, gemini, and agy.")
    arguments, child_environment = _harness_invocation(harness)
    arguments.extend(context.args)
    result = subprocess.run(  # noqa: S603 - executable comes from a closed harness allowlist.
        arguments, env=child_environment, check=False
    )
    raise typer.Exit(code=result.returncode)


@harness_app.command("mcp-list")
def harness_mcp_list(
    harness: Annotated[str, typer.Option("--harness", help="Currently codex only")] = "codex",
) -> None:
    """Show the effective protected MCP registration used by a harness child process."""
    if harness != "codex":
        raise typer.BadParameter("Effective MCP listing is currently supported for codex only.")
    arguments, child_environment = _harness_invocation(harness)
    arguments.extend(["mcp", "list"])
    result = subprocess.run(  # noqa: S603 - executable comes from a closed harness allowlist.
        arguments, env=child_environment, check=False
    )
    raise typer.Exit(code=result.returncode)


def _harness_invocation(harness: str) -> tuple[list[str], dict[str, str]]:
    if harness not in {"codex", "claude", "gemini", "agy"}:
        raise typer.BadParameter("Supported harnesses are codex, claude, gemini, and agy.")
    executable = shutil.which(harness)
    if executable is None:
        raise SqlCtxError(
            "HARNESS_NOT_INSTALLED",
            f"{harness} is not installed; sqlctx never installs harness CLIs.",
        )
    base_url, token = _connection()
    child_environment = os.environ.copy()
    child_environment["SQLCTX_MCP_URL"] = base_url + "/mcp"
    child_environment["SQLCTX_API_TOKEN"] = token
    arguments = [executable]
    if harness == "codex":
        arguments.extend(
            [
                "-c",
                f'mcp_servers.sql-context-pack.url="{base_url}/mcp"',
                "-c",
                'mcp_servers.sql-context-pack.bearer_token_env_var="SQLCTX_API_TOKEN"',
            ]
        )
    return arguments, child_environment


def _connection() -> tuple[str, str]:
    state = JsonRuntimeStateStore()
    value = state.read_json("connection-metadata.json")
    if not value:
        raise SqlCtxError(
            "SERVER_METADATA_MISSING", "Start sqlctx-server before using transfer commands."
        )
    return agent_connection(value)


def _http_error(response: httpx.Response) -> SqlCtxError:
    try:
        error = response.json()["error"]
        details = {
            key: value
            for key, value in error.items()
            if key not in {"code", "message", "retryable", "correlation_id"}
        }
        return SqlCtxError(
            str(error["code"]),
            str(error["message"]),
            retryable=bool(error.get("retryable", False)),
            status_code=response.status_code,
            details=details,
        )
    except (ValueError, KeyError, TypeError):
        return SqlCtxError(
            "HTTP_TRANSFER_FAILED",
            "The local service returned an invalid transfer response.",
            status_code=response.status_code,
        )


@folder_app.command("register")
def folder_register(
    input_root: Annotated[
        Path, typer.Option("--input-root", exists=True, file_okay=False, resolve_path=True)
    ],
    output_root: Annotated[Path, typer.Option("--output-root", resolve_path=True)],
    engine: Annotated[DatabaseEngine, typer.Option("--engine")],
) -> None:
    """Register exact owner-selected roots; Agent surfaces receive only the opaque folder ID."""
    folder = ServiceFacade().managed_folders.register(
        input_root=input_root, output_root=output_root, engine=engine
    )
    if sys.platform == "win32":
        from sqlctx.security.service_access import grant_folder_access

        grant_folder_access(input_root, output_root)
    typer.echo(json.dumps(folder.model_dump(mode="json"), sort_keys=True))


@folder_app.command("list")
def folder_list() -> None:
    """List registered roots in the owner terminal."""
    items = ServiceFacade().managed_folders.list_registered()
    typer.echo(
        json.dumps({"items": [item.model_dump(mode="json") for item in items]}, sort_keys=True)
    )


@folder_app.command("plan")
def folder_plan(
    folder_id: Annotated[str, typer.Option("--folder-id")],
    resolve_file: Annotated[str | None, typer.Option("--resolve-file")] = None,
    context: Annotated[str | None, typer.Option("--context")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    in_place: Annotated[bool, typer.Option("--in-place")] = False,
) -> None:
    """Scan all SQL files and create a non-mutating, hash-bound classification plan."""
    if (resolve_file is None) != (context is None):
        raise typer.BadParameter("--resolve-file and --context must be supplied together.")
    resolutions = (
        [
            OwnerFileResolution(
                relative_path=resolve_file,
                context=context,
                description=description,
                tags=sorted(set(tag or [])),
            )
        ]
        if resolve_file is not None and context is not None
        else []
    )
    plan = ServiceFacade().managed_folders.plan(
        FolderClassificationPlanRequest(
            folder_id=folder_id, resolutions=resolutions, in_place=in_place
        )
    )
    typer.echo(json.dumps(plan.model_dump(mode="json"), sort_keys=True))


@folder_app.command("apply")
def folder_apply(plan_id: Annotated[str, typer.Option("--plan-id")]) -> None:
    """Apply an unchanged plan; separate output is default and in-place requires approval."""
    result = ServiceFacade().managed_folders.apply(plan_id)
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@context_index_app.command("list")
def context_index_list(
    profile: Annotated[str, typer.Option("--profile")],
    context: Annotated[str | None, typer.Option("--context")] = None,
    object_type: Annotated[ObjectType | None, typer.Option("--object-type")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=250)] = 100,
) -> None:
    """List safe context-index metadata without returning SQL bodies."""
    request = ContextIndexListRequest(
        profile=profile,
        context=context,
        object_type=object_type,
        tag=tag,
        status=cast(Literal["confirmed", "unresolved"] | None, status),
        cursor=cursor,
        limit=limit,
    )
    result = ServiceFacade().list_context_index(request)
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@context_index_app.command("sync-plan")
def context_index_sync_plan(
    profile: Annotated[str, typer.Option("--profile")],
    actor_id: Annotated[int, typer.Option("--actor-id", min=1)],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    complete_catalog_id: Annotated[str | None, typer.Option("--complete-catalog-id")] = None,
) -> None:
    """Synchronize a folder plan, or a proven empty complete catalog, into the index."""
    facade = ServiceFacade()
    if plan_id is None and complete_catalog_id is None:
        raise typer.BadParameter("--plan-id is required unless --complete-catalog-id is supplied")
    headers = facade.managed_folders.entries_from_plan(plan_id) if plan_id is not None else []
    entries = [
        ContextIndexEntry(
            schema_name=str(item["schema_name"]),
            object_name=str(item["object_name"]),
            object_type=ObjectType(str(item["object_type"])),
            context=item.get("context"),
            description=item.get("description"),
            tags=list(item.get("tags", [])),
            classification_status=cast(
                Literal["confirmed", "unresolved"], str(item["classification_status"])
            ),
            classification_source=cast(
                Literal["owner", "rule", "unknown"], str(item["classification_source"])
            ),
            source_fingerprint=item.get("source_fingerprint"),
            content_hash=item.get("content_hash"),
            managed_relative_path=str(item["managed_relative_path"]),
            header_version=cast(Literal[1], int(item["header_version"])),
            output_format_version=cast(Literal["2"], str(item["output_format_version"])),
            evidence=list(item.get("evidence", [])),
        )
        for item in headers
    ]
    # Owner mode is what the payload requires, not a preference: deactivation and
    # owner-sourced classifications are the only writes that still need approval.
    mode: Literal["inventory", "owner"] = (
        "owner"
        if complete_catalog_id is not None
        or any(entry.classification_source == "owner" or not entry.active for entry in entries)
        else "inventory"
    )
    result = facade.sync_context_index(
        ContextIndexSyncRequest(
            profile=profile,
            entries=entries,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            complete_catalog_id=complete_catalog_id,
            mode=mode,
        ),
        caller="owner-cli",
    )
    typer.echo(json.dumps({**result.model_dump(mode="json"), "mode": mode}, sort_keys=True))


@context_index_app.command("resolve")
def context_index_resolve(
    folder_id: Annotated[str, typer.Option("--folder-id")],
    file: Annotated[str, typer.Option("--file")],
    context: Annotated[str, typer.Option("--context")],
    description: Annotated[str | None, typer.Option("--description")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
) -> None:
    """Plan one owner-confirmed managed file move/header rewrite before index sync."""
    result = ServiceFacade().resolve_context_index(
        ManagedFileResolutionPlanRequest(
            folder_id=folder_id,
            managed_relative_path=file,
            context=context,
            description=description,
            tags=sorted(set(tag or [])),
        )
    )
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@context_index_app.command("generate-plan")
def context_index_generate_plan(
    profile: Annotated[str, typer.Option("--profile")],
    folder_id: Annotated[str, typer.Option("--folder-id")],
    context: Annotated[str | None, typer.Option("--context")] = None,
    object_type: Annotated[ObjectType | None, typer.Option("--object-type")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    include_unresolved: Annotated[bool, typer.Option("--include-unresolved")] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=250)] = 100,
) -> None:
    """Build a metadata-only generation plan after index/header/body drift checks."""
    result = ServiceFacade().plan_context_generation(
        ContextGenerationRequest(
            profile=profile,
            folder_id=folder_id,
            context=context,
            object_type=object_type,
            tag=tag,
            include_unresolved=include_unresolved,
            limit=limit,
        )
    )
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@routine_app.command("plan")
def routine_plan(
    profile: Annotated[str, typer.Option("--profile")],
    folder_id: Annotated[str, typer.Option("--folder-id")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    file: Annotated[str | None, typer.Option("--file")] = None,
    continue_on_error: Annotated[bool, typer.Option("--continue-on-error")] = False,
) -> None:
    """Plan one file or every managed routine file under a registered folder."""
    result = ServiceFacade().plan_routine_deployment(
        RoutinePlanRequest(
            profile=profile,
            folder_id=folder_id,
            relative_path=file,
            stop_on_error=not continue_on_error,
            idempotency_key=idempotency_key,
        ),
        caller="owner-cli",
    )
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@routine_app.command("apply")
def routine_apply(plan_id: Annotated[str, typer.Option("--plan-id")]) -> None:
    """Apply an unchanged SQL Server routine plan after request-bound owner approval."""
    result = ServiceFacade().apply_routine_deployment(plan_id, caller="owner-cli")
    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@profile_app.command("write-scope")
def profile_write_scope(
    profile: Annotated[str, typer.Option("--profile")],
    metadata_context_write: Annotated[
        bool, typer.Option("--metadata-context-write/--no-metadata-context-write")
    ] = False,
    routine_write: Annotated[bool, typer.Option("--routine-write/--no-routine-write")] = False,
) -> None:
    """Set explicit SQL Server write scopes; both remain disabled by default."""
    repository = YamlConnectionProfileRepository()
    repository.set_write_scopes(
        profile,
        metadata_context_write=metadata_context_write,
        routine_write=routine_write,
    )
    typer.echo(
        json.dumps(
            {
                "profile": profile,
                "metadata_context_write": metadata_context_write,
                "routine_write": routine_write,
            },
            sort_keys=True,
        )
    )


@approvals_app.command("grant")
def grant(
    challenge: Annotated[
        str | None,
        typer.Option(
            "--challenge", help="Challenge ID printed in the sanitized approval response."
        ),
    ] = None,
) -> None:
    """Grant one exact request from an interactive owner terminal."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise typer.BadParameter("Owner approval requires an interactive local terminal.")
    state = JsonRuntimeStateStore()
    CredentialMetadataStore(state).ensure("http://127.0.0.1:8765/mcp")
    approvals = ApprovalService(state=state)
    if challenge is None:
        pending = [item for item in approvals.list_challenges() if item["status"] == "pending"]
        if not pending:
            raise typer.BadParameter(
                "No unexpired approval is pending. Retry the original Agent operation first."
            )
        if len(pending) == 1:
            challenge = str(pending[0]["challenge_id"])
        else:
            typer.echo("Pending approvals:")
            for index, item in enumerate(pending, start=1):
                typer.echo(
                    f"{index}. {item['operation']} / {item['target']} "
                    f"(expires in {item['expires_in_seconds']}s)"
                )
            selected = typer.prompt("Select approval", type=int)
            if selected < 1 or selected > len(pending):
                raise typer.BadParameter("Approval selection is out of range.")
            challenge = str(pending[selected - 1]["challenge_id"])
    if not typer.confirm(f"Grant the exact operation bound to {challenge}?", default=False):
        raise typer.Abort()
    approvals.grant(challenge, interactive=True)
    typer.echo(
        "Approval granted. Return to the Agent; it must retry the identical request before expiry."
    )


@approvals_app.command("list")
def approvals_list() -> None:
    """Show pending, granted, consumed, and expired challenges without request payloads."""
    state = JsonRuntimeStateStore()
    challenges = ApprovalService(state=state).list_challenges()
    typer.echo(
        json.dumps(
            {
                "runtime_root": str(state.root),
                "count": len(challenges),
                "challenges": challenges,
                "owner_action": (
                    "Run `sqlctx approvals grant` to select a pending challenge. "
                    "Retry the original Agent operation if every challenge is expired."
                ),
            },
            sort_keys=True,
        )
    )


@export_app.command("fetch")
def fetch(
    export_id: Annotated[str, typer.Option("--export-id")],
    destination: Annotated[Path, typer.Option("--destination", resolve_path=True)],
) -> None:
    """Fetch through HTTP internally and validate size, bundle hash, manifest hash, and paths."""
    try:
        target = _fetch_remote_export(export_id, destination)
    except httpx.HTTPError:
        target = _fetch_local_export(export_id, destination)
    except SqlCtxError as exc:
        if exc.status_code < 500:
            raise
        target = _fetch_local_export(export_id, destination)
    typer.echo(str(target))


def _fetch_remote_export(export_id: str, destination: Path) -> Path:
    base_url, token = _connection()
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(
        base_url=base_url, headers=headers, timeout=120.0, trust_env=False, follow_redirects=False
    ) as client:
        response = client.get(f"/api/v1/exports/{export_id}")
        if response.status_code != 200:
            raise _http_error(response)
        status = ExportStatus.model_validate(response.json())
        if status.size_bytes is None or status.sha256 is None or status.manifest_sha256 is None:
            raise SqlCtxError(
                "EXPORT_NOT_COMPLETE",
                "Completed integrity metadata is required before bundle transfer.",
            )
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{export_id}.sqlctx.zip"
        with tempfile.NamedTemporaryFile(
            prefix="sqlctx-fetch-", suffix=".zip", dir=destination, delete=False
        ) as handle:
            temporary = Path(handle.name)
            received = 0
            with client.stream("GET", f"/api/v1/exports/{export_id}/bundle") as stream:
                if stream.status_code != 200:
                    raise _http_error(stream)
                for chunk in stream.iter_bytes():
                    received += len(chunk)
                    if received > status.size_bytes:
                        raise SqlCtxError(
                            "BUNDLE_INTEGRITY_FAILED",
                            "Bundle exceeded its declared size while streaming.",
                        )
                    handle.write(chunk)
        try:
            validate_bundle(
                temporary, expected_size=status.size_bytes, expected_sha256=status.sha256
            )
            with zipfile.ZipFile(temporary) as archive:
                manifest = archive.read("manifest.yaml")
            if sha256_bytes(manifest) != status.manifest_sha256:
                raise SqlCtxError(
                    "BUNDLE_INTEGRITY_FAILED", "Bundle manifest hash did not match export status."
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def _fetch_local_export(export_id: str, destination: Path) -> Path:
    state = JsonRuntimeStateStore()
    raw_artifact = state.read_json(f"exports/{export_id}/artifact.json")
    if raw_artifact is None:
        raise SqlCtxError(
            "EXPORT_ARTIFACT_NOT_FOUND",
            "No retained local export artifact is available for recovery.",
            status_code=404,
        )
    artifact = ExportArtifact.model_validate(raw_artifact)
    source = state._safe(f"exports/{export_id}/{export_id}.sqlctx.zip")
    if not source.is_file():
        raise SqlCtxError(
            "EXPORT_ARTIFACT_NOT_FOUND",
            "The retained local export bundle is missing.",
            status_code=404,
        )
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{export_id}.sqlctx.zip"
    with tempfile.NamedTemporaryFile(
        prefix="sqlctx-recover-", suffix=".zip", dir=destination, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(source, temporary)
        validate_bundle(
            temporary, expected_size=artifact.size_bytes, expected_sha256=artifact.sha256
        )
        with zipfile.ZipFile(temporary) as archive:
            manifest = archive.read("manifest.yaml")
        if sha256_bytes(manifest) != artifact.manifest_sha256:
            raise SqlCtxError(
                "BUNDLE_INTEGRITY_FAILED",
                "Recovered bundle manifest hash did not match retained metadata.",
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@export_app.command("assemble")
def assemble(
    bundle: Annotated[
        list[Path], typer.Option("--bundle", exists=True, dir_okay=False, resolve_path=True)
    ],
    output_root: Annotated[Path, typer.Option("--output-root", resolve_path=True)],
    allow_delete_stale: Annotated[
        bool,
        typer.Option(
            "--allow-delete-stale",
            help="Owner-confirmed removal of files managed by the previous manifest.",
        ),
    ] = False,
) -> None:
    """Merge fetched batches and atomically update managed files only."""
    inventory = assemble_bundles(bundle, output_root, allow_delete_stale=allow_delete_stale)
    typer.echo(json.dumps(inventory.model_dump(mode="json", by_alias=True), sort_keys=True))


@validate_app.command("output")
def validate_output(
    root: Annotated[Path, typer.Option("--root", exists=True, file_okay=False, resolve_path=True)],
) -> None:
    """Reopen and hash every managed file without sending the local root path to the server."""
    inventory = inventory_output(root)
    typer.echo(json.dumps(inventory.model_dump(mode="json", by_alias=True), sort_keys=True))


def run() -> None:
    """Run the owner CLI with sanitized production errors and opt-in developer traces."""
    try:
        app()
    except SqlCtxError as exc:
        if os.environ.get("SQLCTX_DEBUG_ERRORS") == "1":
            raise
        typer.echo(json.dumps({"error": exc.public_payload()}, sort_keys=True), err=True)
        raise SystemExit(2) from None
    except Exception as exc:
        if os.environ.get("SQLCTX_DEBUG_ERRORS") == "1":
            raise
        correlation_id = "corr_" + secrets.token_urlsafe(12)
        state = JsonRuntimeStateStore()
        state.write_json(
            f"errors/{correlation_id}.json",
            {"correlation_id": correlation_id, "traceback": traceback.format_exc()},
        )
        typer.echo(
            json.dumps(
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "The command could not complete. No traceback was printed.",
                        "correlation_id": correlation_id,
                        "owner_action": "Run `sqlctx runtime status`; protected diagnostics are under runtime_root/errors.",
                    }
                },
                sort_keys=True,
            ),
            err=True,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run()
