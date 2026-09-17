"""Fail-closed scanning and atomic materialization for registered SQL folders."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlctx._version import OUTPUT_FORMAT_VERSION
from sqlctx.adapters.sqlserver.adapter import SqlServerAdapter
from sqlctx.classification.rules import CategoryConfig
from sqlctx.core.enums import DatabaseEngine, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.exporting.header import ManagedSqlHeader, parse_managed_sql, render_managed_sql
from sqlctx.managed_files.contracts import (
    FolderApplyResult,
    FolderClassificationPlan,
    FolderClassificationPlanRequest,
    FolderPlanItem,
    ManagedFileResolutionPlanRequest,
    RegisteredFolder,
)
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore

_IDENTIFIER = r"(?:\[(?:[^\]\r\n]|\]\])+\]|[A-Za-z_][A-Za-z0-9_$#@]*)"
_DECLARATION = re.compile(
    rf"\b(?:CREATE\s+OR\s+ALTER|CREATE|ALTER)\s+"
    rf"(?P<kind>TABLE|PROCEDURE|PROC|FUNCTION)\s+"
    rf"(?P<first>{_IDENTIFIER})(?:\s*\.\s*(?P<second>{_IDENTIFIER}))?",
    re.IGNORECASE,
)
_TYPE_FOLDER = {
    ObjectType.TABLE: "tables",
    ObjectType.PROCEDURE: "store_procedures",
    ObjectType.FUNCTION: "functions",
}


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _identifier(value: str) -> str:
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1].replace("]]", "]")
    return value


def detect_sql_identity(content: str) -> tuple[ObjectType, str, str]:
    """Detect exactly one supported SQL object declaration."""
    matches = list(_DECLARATION.finditer(_declaration_surface(content)))
    if len(matches) != 1:
        raise SqlCtxError(
            "FOLDER_SQL_OBJECT_COUNT_INVALID", "Each SQL file must declare exactly one object."
        )
    match = matches[0]
    kind = match.group("kind").upper()
    object_type = (
        ObjectType.TABLE
        if kind == "TABLE"
        else ObjectType.PROCEDURE
        if kind in {"PROCEDURE", "PROC"}
        else ObjectType.FUNCTION
    )
    first = _identifier(match.group("first"))
    second = match.group("second")
    schema_name, object_name = (first, _identifier(second)) if second else ("dbo", first)
    return object_type, schema_name, object_name


def _declaration_surface(content: str) -> str:
    """Hide quoted/comment text so declaration words inside bodies are not parsed as objects."""
    output: list[str] = []
    index = 0
    state = "normal"
    block_depth = 0
    while index < len(content):
        char = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if state == "normal":
            if char == "'":
                state = "single_quote"
                output.append(" ")
            elif char == '"':
                state = "double_quote"
                output.append(" ")
            elif char == "[":
                state = "bracket"
                output.append(char)
            elif char == "-" and following == "-":
                state = "line_comment"
                output.extend((" ", " "))
                index += 1
            elif char == "/" and following == "*":
                state = "block_comment"
                block_depth = 1
                output.extend((" ", " "))
                index += 1
            else:
                output.append(char)
        elif state == "single_quote":
            output.append("\n" if char == "\n" else " ")
            if char == "'" and following == "'":
                output.append(" ")
                index += 1
            elif char == "'":
                state = "normal"
        elif state == "double_quote":
            output.append("\n" if char == "\n" else " ")
            if char == '"' and following == '"':
                output.append(" ")
                index += 1
            elif char == '"':
                state = "normal"
        elif state == "bracket":
            output.append(char)
            if char == "]" and following == "]":
                output.append(following)
                index += 1
            elif char == "]":
                state = "normal"
        elif state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "normal"
        else:
            output.append("\n" if char == "\n" else " ")
            if char == "/" and following == "*":
                output.append(" ")
                block_depth += 1
                index += 1
            elif char == "*" and following == "/":
                output.append(" ")
                block_depth -= 1
                index += 1
                if block_depth == 0:
                    state = "normal"
        index += 1
    return "".join(output)


class ManagedFolderService:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        approvals: ApprovalService | None = None,
        *,
        plan_ttl_seconds: int = 900,
        category_rules: CategoryConfig | None = None,
    ) -> None:
        self.state = state
        self.approvals = approvals
        self.plan_ttl_seconds = plan_ttl_seconds
        self.category_rules = category_rules

    def register(
        self, *, input_root: Path, output_root: Path, engine: DatabaseEngine
    ) -> RegisteredFolder:
        source = input_root.expanduser().resolve()
        target = output_root.expanduser().resolve()
        if not source.is_dir():
            raise SqlCtxError("FOLDER_INPUT_NOT_FOUND", "Registered input root is not a directory.")
        if source != target:
            try:
                target.relative_to(source)
            except ValueError:
                pass
            else:
                raise SqlCtxError(
                    "FOLDER_ROOT_OVERLAP",
                    "Separate output root cannot be nested inside the input root.",
                )
        folder = RegisteredFolder(
            folder_id="fld_" + secrets.token_urlsafe(12),
            engine=engine,
            input_root=str(source),
            output_root=str(target),
            created_at=datetime.now(UTC),
        )
        records = self._folders()
        records[folder.folder_id] = folder.model_dump(mode="json")
        self.state.write_json("managed-folders/registry.json", records)
        return folder

    def list_registered(self) -> list[RegisteredFolder]:
        return [
            RegisteredFolder.model_validate(value) for _, value in sorted(self._folders().items())
        ]

    def plan(self, request: FolderClassificationPlanRequest) -> FolderClassificationPlan:
        folder = self._folder(request.folder_id)
        source_root = Path(folder.input_root)
        output_root = source_root if request.in_place else Path(folder.output_root)
        if request.in_place and Path(folder.input_root) != Path(folder.output_root):
            output_root = source_root
        resolutions = {item.relative_path.casefold(): item for item in request.resolutions}
        if len(resolutions) != len(request.resolutions):
            raise SqlCtxError("FOLDER_RESOLUTION_DUPLICATE", "A file was resolved more than once.")
        files = sorted(source_root.rglob("*.sql"), key=lambda item: item.as_posix().casefold())
        items: list[FolderPlanItem] = []
        identities: set[str] = set()
        destinations: set[str] = set()
        private: list[dict[str, Any]] = []
        for path in files:
            relative = self._safe_relative(source_root, path)
            content = path.read_text(encoding="utf-8-sig")
            header, body = self._read_identity(content, folder.engine)
            header = self._classify_with_rules(header)
            resolution = resolutions.pop(relative.casefold(), None)
            if resolution is not None:
                header = header.model_copy(
                    update={
                        "context": resolution.context,
                        "description": resolution.description,
                        "tags": sorted(set(resolution.tags)),
                        "classification_status": "confirmed",
                        "classification_source": "owner",
                    }
                )
                header = ManagedSqlHeader.model_validate(header.model_dump())
            category = header.context if header.classification_status == "confirmed" else "unknowns"
            destination = f"{category}/{_TYPE_FOLDER[ObjectType(header.object_type)]}/{path.name}"
            identity_key = header.object_id.casefold()
            destination_key = destination.casefold()
            if identity_key in identities:
                raise SqlCtxError(
                    "FOLDER_DUPLICATE_OBJECT_IDENTITY", "Multiple files declare the same object."
                )
            if destination_key in destinations:
                raise SqlCtxError("FOLDER_DESTINATION_COLLISION", "Managed output path collision.")
            identities.add(identity_key)
            destinations.add(destination_key)
            rendered = render_managed_sql(header, body)
            content_hash = _sha256(content)
            items.append(
                FolderPlanItem(
                    source_relative_path=relative,
                    destination_relative_path=destination,
                    object_id=header.object_id,
                    object_type=header.object_type,
                    content_hash=content_hash,
                    classification_status=header.classification_status,
                    context=header.context,
                    description=header.description,
                    tags=header.tags,
                    warnings=([] if header.context else ["classification_unresolved"]),
                )
            )
            private.append(
                {
                    "source": relative,
                    "destination": destination,
                    "input_hash": content_hash,
                    "output_hash": _sha256(rendered),
                    "header": header.model_dump(mode="json"),
                }
            )
        if resolutions:
            raise SqlCtxError(
                "FOLDER_RESOLUTION_NOT_FOUND", "A resolved relative file was not found."
            )
        if not items:
            raise SqlCtxError("FOLDER_NO_SQL_FILES", "Registered input contains no SQL files.")
        if request.in_place:
            source_paths = {item.source_relative_path.casefold(): item.object_id for item in items}
            collisions = [
                item.destination_relative_path
                for item in items
                if item.destination_relative_path.casefold() in source_paths
                and item.destination_relative_path.casefold()
                != item.source_relative_path.casefold()
            ]
            if collisions:
                raise SqlCtxError(
                    "FOLDER_IN_PLACE_SOURCE_COLLISION",
                    "An in-place destination would overwrite another source file.",
                    details={"paths": sorted(collisions)},
                )
        created = datetime.now(UTC)
        public_payload = {
            "folder_id": folder.folder_id,
            "engine": folder.engine,
            "in_place": request.in_place,
            "items": [item.model_dump(mode="json") for item in items],
        }
        plan_hash = _sha256(json.dumps(public_payload, sort_keys=True, separators=(",", ":")))
        plan = FolderClassificationPlan(
            plan_id="fpl_" + secrets.token_urlsafe(12),
            folder_id=folder.folder_id,
            engine=folder.engine,
            in_place=request.in_place,
            created_at=created,
            expires_at=created + timedelta(seconds=self.plan_ttl_seconds),
            plan_hash=plan_hash,
            items=items,
        )
        self.state.write_json(
            f"managed-folders/plans/{plan.plan_id}.json",
            {
                "plan": plan.model_dump(mode="json"),
                "private": private,
                "source_root": str(source_root),
                "output_root": str(output_root),
                "plan_kind": "classification",
                "applied": False,
            },
        )
        return plan

    def plan_resolution(
        self, request: ManagedFileResolutionPlanRequest
    ) -> FolderClassificationPlan:
        """Plan one owner-confirmed header/path rewrite from the active managed root."""
        folder = self._folder(request.folder_id)
        root = self.managed_output_root(folder.folder_id).resolve()
        source = self._safe_join(root, request.managed_relative_path)
        if not source.is_file():
            raise SqlCtxError(
                "FOLDER_MANAGED_FILE_NOT_FOUND", "Managed SQL file was not found.", status_code=404
            )
        content = source.read_text(encoding="utf-8-sig")
        try:
            parse_managed_sql(content)
        except ValueError as exc:
            raise SqlCtxError(
                "FOLDER_MANAGED_HEADER_REQUIRED",
                "Owner resolution accepts only an already managed SQL file.",
            ) from exc
        header, body = self._read_identity(content, folder.engine)
        resolved_header = ManagedSqlHeader.model_validate(
            header.model_copy(
                update={
                    "context": request.context,
                    "description": request.description,
                    "tags": request.tags,
                    "evidence": sorted(set([*header.evidence, "owner:managed_file_resolution"])),
                    "classification_status": "confirmed",
                    "classification_source": "owner",
                }
            ).model_dump()
        )
        destination = (
            f"{request.context}/{_TYPE_FOLDER[ObjectType(header.object_type)]}/{source.name}"
        )
        target = self._safe_join(root, destination)
        if target.resolve() != source.resolve() and target.exists():
            raise SqlCtxError(
                "FOLDER_RESOLUTION_DESTINATION_EXISTS",
                "Owner resolution would overwrite another managed file.",
                status_code=409,
            )
        rendered = render_managed_sql(resolved_header, body)
        relative = self._safe_relative(root, source)
        item = FolderPlanItem(
            source_relative_path=relative,
            destination_relative_path=destination,
            object_id=resolved_header.object_id,
            object_type=resolved_header.object_type,
            content_hash=_sha256(content),
            classification_status="confirmed",
            context=request.context,
            description=request.description,
            tags=request.tags,
        )
        created = datetime.now(UTC)
        public_payload = {
            "folder_id": folder.folder_id,
            "engine": folder.engine,
            "in_place": True,
            "items": [item.model_dump(mode="json")],
        }
        plan_hash = _sha256(json.dumps(public_payload, sort_keys=True, separators=(",", ":")))
        plan = FolderClassificationPlan(
            plan_id="fpl_" + secrets.token_urlsafe(12),
            folder_id=folder.folder_id,
            engine=folder.engine,
            in_place=True,
            created_at=created,
            expires_at=created + timedelta(seconds=self.plan_ttl_seconds),
            plan_hash=plan_hash,
            items=[item],
        )
        manifest_path = root / "sqlctx-managed-manifest.json"
        if not manifest_path.is_file():
            raise SqlCtxError(
                "FOLDER_MANAGED_MANIFEST_REQUIRED",
                "Owner resolution requires the applied managed-file manifest.",
                status_code=409,
            )
        manifest_content = manifest_path.read_text(encoding="utf-8")
        try:
            manifest = json.loads(manifest_content)
        except json.JSONDecodeError as exc:
            raise SqlCtxError(
                "FOLDER_MANAGED_MANIFEST_DRIFT",
                "Managed-file manifest is invalid.",
                status_code=409,
            ) from exc
        manifest_files = manifest.get("files")
        source_inventory = (
            [
                item
                for item in manifest_files
                if isinstance(item, dict)
                and str(item.get("path", "")).casefold() == relative.casefold()
            ]
            if isinstance(manifest_files, list)
            else []
        )
        if len(source_inventory) != 1 or str(source_inventory[0].get("sha256", "")) != _sha256(
            content
        ):
            raise SqlCtxError(
                "FOLDER_MANAGED_MANIFEST_DRIFT",
                "Managed file does not match the applied inventory.",
                status_code=409,
            )
        self.state.write_json(
            f"managed-folders/plans/{plan.plan_id}.json",
            {
                "plan": plan.model_dump(mode="json"),
                "private": [
                    {
                        "source": relative,
                        "destination": destination,
                        "input_hash": _sha256(content),
                        "output_hash": _sha256(rendered),
                        "header": resolved_header.model_dump(mode="json"),
                    }
                ],
                "source_root": str(root),
                "output_root": str(root),
                "manifest_hash": _sha256(manifest_content),
                "plan_kind": "managed_resolution",
                "applied": False,
            },
        )
        return plan

    def apply(self, plan_id: str, *, caller: str = "owner-cli") -> FolderApplyResult:
        value = self.state.read_json(f"managed-folders/plans/{plan_id}.json")
        if not isinstance(value, dict):
            raise SqlCtxError("FOLDER_PLAN_NOT_FOUND", "Folder classification plan was not found.")
        plan = FolderClassificationPlan.model_validate(value["plan"])
        if plan.expires_at <= datetime.now(UTC):
            raise SqlCtxError("FOLDER_PLAN_EXPIRED", "Folder classification plan has expired.")
        folder = self._folder(plan.folder_id)
        if plan.in_place:
            if self.approvals is None:
                raise SqlCtxError("APPROVAL_SERVICE_REQUIRED", "In-place apply requires approval.")
            self.approvals.require(
                caller=caller,
                operation="managed_folder.apply_in_place",
                target=plan.folder_id,
                payload={"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
            )
        plan_kind = str(value.get("plan_kind", "classification"))
        source_root = Path(str(value.get("source_root", folder.input_root))).resolve()
        output_root = Path(str(value["output_root"])).resolve()
        if plan_kind == "managed_resolution":
            expected_source_root = self.managed_output_root(folder.folder_id).resolve()
            expected_output_root = expected_source_root
        else:
            expected_source_root = Path(folder.input_root).resolve()
            expected_output_root = Path(
                folder.input_root if plan.in_place else folder.output_root
            ).resolve()
        if source_root != expected_source_root or output_root != expected_output_root:
            raise SqlCtxError("FOLDER_PLAN_CORRUPT", "Folder plan registered roots changed.")
        output_root.mkdir(parents=True, exist_ok=True)
        private = value.get("private")
        if not isinstance(private, list):
            raise SqlCtxError("FOLDER_PLAN_CORRUPT", "Folder plan private state is invalid.")
        with tempfile.TemporaryDirectory(prefix="sqlctx-folder-") as temporary:
            stage = Path(temporary)
            manifest_files: list[dict[str, str]] = []
            for record in private:
                source = self._safe_join(source_root, str(record["source"]))
                content = source.read_text(encoding="utf-8-sig")
                if _sha256(content) != record["input_hash"]:
                    raise SqlCtxError(
                        "FOLDER_PLAN_CONTENT_DRIFT", "A source file changed after planning."
                    )
                header = ManagedSqlHeader.model_validate(record["header"])
                try:
                    _, body = parse_managed_sql(content)
                except ValueError:
                    _, body = self._read_identity(content, plan.engine)
                rendered = render_managed_sql(header, body)
                if _sha256(rendered) != record["output_hash"]:
                    raise SqlCtxError("FOLDER_PLAN_HASH_DRIFT", "Planned output hash changed.")
                staged = self._safe_join(stage, str(record["destination"]))
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_text(rendered, encoding="utf-8", newline="\n")
                manifest_files.append(
                    {"path": str(record["destination"]), "sha256": str(record["output_hash"])}
                )
            if plan_kind == "managed_resolution":
                current_manifest_path = output_root / "sqlctx-managed-manifest.json"
                try:
                    current_manifest_content = current_manifest_path.read_text(encoding="utf-8")
                    current_manifest = json.loads(current_manifest_content)
                except (OSError, json.JSONDecodeError) as exc:
                    raise SqlCtxError(
                        "FOLDER_MANAGED_MANIFEST_DRIFT",
                        "Managed-file manifest is missing or invalid.",
                        status_code=409,
                    ) from exc
                if _sha256(current_manifest_content) != value.get("manifest_hash"):
                    raise SqlCtxError(
                        "FOLDER_MANAGED_MANIFEST_DRIFT",
                        "Managed-file manifest changed after resolution planning.",
                        status_code=409,
                    )
                existing_files = current_manifest.get("files")
                if not isinstance(existing_files, list) or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("path"), str)
                    or not isinstance(item.get("sha256"), str)
                    for item in existing_files
                ):
                    raise SqlCtxError(
                        "FOLDER_MANAGED_MANIFEST_DRIFT", "Managed-file inventory is invalid."
                    )
                replaced_paths = {str(record["source"]).casefold() for record in private} | {
                    str(record["destination"]).casefold() for record in private
                }
                preserved_files = [
                    {"path": str(item["path"]), "sha256": str(item["sha256"])}
                    for item in existing_files
                    if str(item["path"]).casefold() not in replaced_paths
                ]
                manifest_files = sorted(
                    [*preserved_files, *manifest_files],
                    key=lambda item: item["path"].casefold(),
                )
            inventory_hash = _sha256(
                json.dumps(manifest_files, sort_keys=True, separators=(",", ":"))
            )
            manifest = {
                "version": 1,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "files": manifest_files,
                "inventory_hash": inventory_hash,
            }
            manifest_path = stage / "sqlctx-managed-manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            for staged in sorted(stage.rglob("*")):
                if not staged.is_file():
                    continue
                relative = staged.relative_to(stage)
                target = self._safe_join(output_root, relative.as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
            moved = 0
            if plan.in_place:
                for record in private:
                    if record["source"].casefold() == record["destination"].casefold():
                        continue
                    old = self._safe_join(source_root, str(record["source"]))
                    if old.is_file():
                        old.unlink()
                        moved += 1
        records = self._folders()
        records[folder.folder_id] = folder.model_copy(
            update={"managed_root": str(output_root)}
        ).model_dump(mode="json")
        self.state.write_json("managed-folders/registry.json", records)
        value["applied"] = True
        value["applied_at"] = datetime.now(UTC).isoformat()
        self.state.write_json(f"managed-folders/plans/{plan.plan_id}.json", value)
        return FolderApplyResult(
            plan_id=plan.plan_id,
            written=len(private),
            moved=moved,
            manifest_relative_path="sqlctx-managed-manifest.json",
            inventory_hash=inventory_hash,
        )

    def entries_from_plan(self, plan_id: str) -> list[dict[str, Any]]:
        """Return applied headers only while files and manifest still match the plan."""
        value = self.state.read_json(f"managed-folders/plans/{plan_id}.json")
        if not isinstance(value, dict) or not isinstance(value.get("private"), list):
            raise SqlCtxError("FOLDER_PLAN_NOT_FOUND", "Folder classification plan was not found.")
        if value.get("applied") is not True:
            raise SqlCtxError(
                "FOLDER_PLAN_NOT_APPLIED",
                "Context-index synchronization requires a successfully applied folder plan.",
                status_code=409,
            )
        plan = FolderClassificationPlan.model_validate(value["plan"])
        root = self.managed_output_root(plan.folder_id).resolve()
        manifest_path = root / "sqlctx-managed-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SqlCtxError(
                "FOLDER_MANAGED_MANIFEST_DRIFT",
                "Managed-file manifest is missing or invalid.",
                status_code=409,
            ) from exc
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            raise SqlCtxError(
                "FOLDER_MANAGED_MANIFEST_DRIFT",
                "Managed-file inventory is invalid.",
                status_code=409,
            )
        inventory: dict[str, str] = {}
        for item in manifest_files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str)
                or str(item["path"]).casefold() in inventory
            ):
                raise SqlCtxError(
                    "FOLDER_MANAGED_MANIFEST_DRIFT",
                    "Managed-file inventory is invalid.",
                    status_code=409,
                )
            inventory[str(item["path"]).casefold()] = str(item["sha256"])
        entries: list[dict[str, Any]] = []
        for item in value["private"]:
            destination = str(item["destination"])
            expected_hash = str(item["output_hash"])
            path = self._safe_join(root, destination)
            try:
                content = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise SqlCtxError(
                    "FOLDER_PLAN_CONTENT_DRIFT",
                    "An applied managed file is missing.",
                    status_code=409,
                ) from exc
            if (
                _sha256(content) != expected_hash
                or inventory.get(destination.casefold()) != expected_hash
            ):
                raise SqlCtxError(
                    "FOLDER_PLAN_CONTENT_DRIFT",
                    "An applied managed file or its inventory changed before index sync.",
                    status_code=409,
                )
            header, _ = self._read_identity(content, plan.engine)
            expected_header = ManagedSqlHeader.model_validate(item["header"])
            if header != expected_header:
                raise SqlCtxError(
                    "FOLDER_PLAN_CONTENT_DRIFT",
                    "An applied managed header changed before index sync.",
                    status_code=409,
                )
            entries.append(dict(header.model_dump(mode="json"), managed_relative_path=destination))
        return entries

    def managed_output_root(self, folder_id: str) -> Path:
        """Return the protected output root for internal file-integrity services only."""
        folder = self._folder(folder_id)
        return Path(folder.managed_root or folder.output_root)

    def _read_identity(self, content: str, engine: DatabaseEngine) -> tuple[ManagedSqlHeader, str]:
        try:
            header, body = parse_managed_sql(content)
            detected_type, detected_schema, detected_name = detect_sql_identity(body)
            if (
                ObjectType(header.object_type) != detected_type
                or header.schema_name.casefold() != detected_schema.casefold()
                or header.object_name.casefold() != detected_name.casefold()
            ):
                raise SqlCtxError(
                    "MANAGED_SQL_IDENTITY_MISMATCH",
                    "Managed header and SQL declaration identify different objects.",
                )
            if header.engine != engine:
                raise SqlCtxError(
                    "MANAGED_SQL_ENGINE_MISMATCH",
                    "Managed header engine differs from folder engine.",
                )
            if header.content_hash != _sha256(body):
                raise SqlCtxError(
                    "MANAGED_SQL_CONTENT_DRIFT", "Managed header and SQL body hashes differ."
                )
            return header, body
        except ValueError as exc:
            if str(exc) != "MANAGED_SQL_HEADER_MISSING":
                raise SqlCtxError(
                    "MANAGED_SQL_HEADER_INVALID", "Managed SQL header is invalid."
                ) from exc
        object_type, schema_name, object_name = detect_sql_identity(content)
        body = content
        if engine == DatabaseEngine.SQLSERVER and object_type == ObjectType.PROCEDURE:
            body = SqlServerAdapter.normalize_procedure_definition(body)
        elif engine == DatabaseEngine.SQLSERVER and object_type == ObjectType.FUNCTION:
            body = SqlServerAdapter.normalize_function_definition(body)
        content_hash = _sha256(body)
        return (
            ManagedSqlHeader(
                object_id=f"{object_type.value}:{schema_name}.{object_name}",
                engine=engine,
                schema_name=schema_name,
                object_name=object_name,
                object_type=object_type,
                classification_status="unresolved",
                classification_source="unknown",
                content_hash=content_hash,
                output_format_version=OUTPUT_FORMAT_VERSION,
            ),
            body,
        )

    def _folders(self) -> dict[str, Any]:
        value = self.state.read_json("managed-folders/registry.json", {})
        if not isinstance(value, dict):
            raise SqlCtxError("FOLDER_REGISTRY_CORRUPT", "Folder registry is invalid.")
        return value

    def _folder(self, folder_id: str) -> RegisteredFolder:
        value = self._folders().get(folder_id)
        if value is None:
            raise SqlCtxError(
                "FOLDER_NOT_FOUND", "Registered folder was not found.", status_code=404
            )
        return RegisteredFolder.model_validate(value)

    @staticmethod
    def _safe_relative(root: Path, path: Path) -> str:
        # st_file_attributes is Windows-only, so read it defensively rather than through
        # hasattr: mypy cannot narrow an attribute that its target platform's stubs omit.
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise SqlCtxError("FOLDER_LINK_REJECTED", "Links and reparse points are not scanned.")
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise SqlCtxError(
                "FOLDER_PATH_ESCAPE", "SQL file escaped its registered root."
            ) from exc
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise SqlCtxError("FOLDER_PATH_UNSAFE", "SQL relative path is unsafe.")
        return relative.as_posix()

    @staticmethod
    def _safe_join(root: Path, relative: str) -> Path:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise SqlCtxError("FOLDER_PATH_ESCAPE", "Managed path escaped its root.") from exc
        return candidate

    def _classify_with_rules(self, header: ManagedSqlHeader) -> ManagedSqlHeader:
        if header.classification_status == "confirmed" or self.category_rules is None:
            return header
        name = header.object_name.upper()
        schema = header.schema_name.lower()
        matches: dict[str, tuple[str, str]] = {}
        for rule in self.category_rules.categories:
            kind: str | None = None
            if name in {item.upper() for item in rule.exact_names}:
                kind = "configured_exact"
            elif any(name.startswith(prefix.upper()) for prefix in rule.prefixes):
                kind = "configured_prefix"
            elif schema in {item.lower() for item in rule.schemas}:
                kind = "configured_schema"
            if kind is not None:
                matches[rule.name] = (kind, rule.description)
        evidence = sorted(f"rule:{kind}:{category}" for category, (kind, _) in matches.items())
        if len(matches) != 1:
            return header.model_copy(update={"evidence": evidence})
        context, (_, description) = next(iter(matches.items()))
        return ManagedSqlHeader.model_validate(
            header.model_copy(
                update={
                    "context": context,
                    "description": description or None,
                    "tags": [],
                    "evidence": evidence,
                    "classification_status": "confirmed",
                    "classification_source": "rule",
                }
            ).model_dump()
        )
