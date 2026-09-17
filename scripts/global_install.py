"""Install the canonical SQL Context Pack Skill at owner user scope."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLUGIN_NAME = "sql-context-pack"
MARKETPLACE_NAME = "personal"
MARKETPLACE_ENTRY = {
    "name": PLUGIN_NAME,
    "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Developer Tools",
}


@dataclass(frozen=True)
class HarnessSpec:
    """Per-provider global discovery layout and native registration verbs."""

    name: str
    manifest: str
    payload_dirs: tuple[str, ...]
    payload_files: tuple[str, ...]
    skill_parent: tuple[str, ...]
    plugin_parent: tuple[str, ...]
    uses_marketplace: bool
    cli: str
    install_verb: tuple[str, ...]
    remove_verb: tuple[str, ...]
    list_verb: tuple[str, ...]
    # Antigravity has no plugin/extension CLI: discovery is purely file placement, so
    # there is no native registration step to run or verify for it.
    native_cli: bool = True
    # Relative to home. Set for hosts that read one shared MCP config file rather than
    # taking the server declaration from the plugin manifest.
    mcp_config: tuple[str, ...] = ()

    @property
    def manifest_dir(self) -> str | None:
        head, _, tail = self.manifest.partition("/")
        return head if tail else None

    def target(self) -> str:
        """The identifier the native CLI uses for this plugin or extension."""
        return f"{PLUGIN_NAME}@{MARKETPLACE_NAME}" if self.uses_marketplace else PLUGIN_NAME


HARNESSES: dict[str, HarnessSpec] = {
    "codex": HarnessSpec(
        name="codex",
        manifest=".codex-plugin/plugin.json",
        payload_dirs=(".codex-plugin", "skills", "hooks"),
        payload_files=(".mcp.json",),
        skill_parent=(".codex", "skills"),
        plugin_parent=("plugins",),
        uses_marketplace=True,
        cli="codex",
        install_verb=("plugin", "add"),
        remove_verb=("plugin", "remove"),
        list_verb=("plugin", "list"),
    ),
    "claude": HarnessSpec(
        name="claude",
        manifest=".claude-plugin/plugin.json",
        payload_dirs=(".claude-plugin", "skills", "hooks"),
        payload_files=(".mcp.json",),
        skill_parent=(".claude", "skills"),
        plugin_parent=("plugins",),
        uses_marketplace=True,
        cli="claude",
        install_verb=("plugin", "install"),
        remove_verb=("plugin", "uninstall"),
        list_verb=("plugin", "list"),
    ),
    "gemini": HarnessSpec(
        name="gemini",
        # Gemini has no marketplace concept; the extension directory itself is the install unit.
        manifest="gemini-extension.json",
        payload_dirs=("skills", "hooks"),
        payload_files=("gemini-extension.json", ".mcp.json"),
        skill_parent=(".gemini", "extensions"),
        plugin_parent=(".gemini", "extensions"),
        uses_marketplace=False,
        cli="gemini",
        install_verb=("extensions", "install"),
        remove_verb=("extensions", "uninstall"),
        list_verb=("extensions", "list"),
    ),
    "agy": HarnessSpec(
        name="agy",
        # Antigravity CLI and IDE share ~/.gemini/config. Skills are discovered by
        # directory placement and MCP servers by a single shared config file, so the
        # install unit is the skill directory plus one JSON merge -- there is no
        # marketplace and no install verb.
        manifest=".agents/plugin.json",
        payload_dirs=("skills", "hooks"),
        payload_files=(".agents/plugin.json", ".agents/mcp_config.json"),
        skill_parent=(".gemini", "config", "skills"),
        plugin_parent=(".gemini", "config", "skills"),
        # Only skill mode is loadable; see _assert_mode.
        uses_marketplace=False,
        cli="agy",
        install_verb=(),
        remove_verb=(),
        list_verb=(),
        native_cli=False,
        mcp_config=(".gemini", "config", "mcp_config.json"),
    ),
}


def _harness(name: str) -> HarnessSpec:
    spec = HARNESSES.get(name)
    if spec is None:
        raise InstallError("UNSUPPORTED_HARNESS", f"Unsupported harness: {name}")
    return spec


def _assert_mode(spec: HarnessSpec, mode: str) -> None:
    """Reject a discovery mode a provider cannot actually load."""
    if spec.name == "agy" and mode == "plugin":
        raise InstallError(
            "PLUGIN_MODE_UNSUPPORTED",
            "Antigravity discovers <skills-root>/<name>/SKILL.md directly; a plugin layout "
            "would bury the Skill one directory deeper and never be found. Use --mode skill.",
        )
    if spec.name == "gemini" and mode == "skill":
        raise InstallError(
            "SKILL_MODE_UNSUPPORTED",
            "Gemini loads extensions, not bare skills; a skill-only directory has no "
            "gemini-extension.json and would never be discovered. Use --mode plugin.",
        )


class InstallError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError("INVALID_JSON", f"Invalid JSON document: {path.name}") from exc
    if not isinstance(value, dict):
        raise InstallError("INVALID_JSON", f"JSON root must be an object: {path.name}")
    return value


def _skill_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise InstallError("INVALID_SKILL", "Canonical SKILL.md frontmatter is missing.")
    frontmatter = text.split("---", 2)[1]
    for line in frontmatter.splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    raise InstallError("INVALID_SKILL", "Canonical Skill metadata.version is missing.")


def _inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if (
            "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or path.name == ".sqlctx-install.json"
        ):
            continue
        content = path.read_bytes()
        items.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": len(content),
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
        )
    return items


def _inventory_hash(root: Path) -> str | None:
    inventory = _inventory(root)
    if not inventory:
        return None
    canonical = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _assert_under(home: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(home.resolve())
    except ValueError as exc:
        raise InstallError(
            "UNSAFE_DESTINATION", "Global destination escaped the selected home."
        ) from exc


def _paths(home: Path, spec: HarnessSpec) -> dict[str, Path]:
    home = home.expanduser().resolve()
    result = {
        "home": home,
        "marketplace": home / ".agents" / "plugins" / "marketplace.json",
        "plugin": home.joinpath(*spec.plugin_parent) / PLUGIN_NAME,
        "skill": home.joinpath(*spec.skill_parent) / PLUGIN_NAME,
    }
    for key, value in result.items():
        if key != "home":
            _assert_under(home, value)
    return result


def _validate_source(source_root: Path, spec: HarnessSpec) -> dict[str, str]:
    source_root = source_root.resolve()
    manifest_path = source_root / spec.manifest
    skill_path = source_root / "skills" / PLUGIN_NAME / "SKILL.md"
    for path in (manifest_path, skill_path):
        if not path.is_file():
            raise InstallError("SOURCE_INCOMPLETE", f"Required source file is missing: {path.name}")
    manifest = _read_json(manifest_path)
    if manifest.get("name") != PLUGIN_NAME:
        raise InstallError("INVALID_PLUGIN", "Plugin name does not match its canonical folder.")
    if spec.name == "agy":
        if manifest.get("skills") != "./skills/":
            raise InstallError(
                "INVALID_PLUGIN", "Antigravity manifest must expose the canonical Skill directory."
            )
    elif spec.name == "gemini":
        if manifest.get("contextFileName") != f"skills/{PLUGIN_NAME}/SKILL.md":
            raise InstallError(
                "INVALID_PLUGIN", "Extension must point at the canonical Skill context file."
            )
        if not isinstance(manifest.get("mcpServers"), dict):
            raise InstallError("INVALID_PLUGIN", "Extension must declare the MCP bridge inline.")
    else:
        if manifest.get("skills") != "./skills/":
            raise InstallError(
                "INVALID_PLUGIN", "Plugin must expose the canonical Skill directory."
            )
        if "hooks" in manifest:
            raise InstallError(
                "INVALID_PLUGIN",
                "Plugin hooks use hooks/hooks.json convention, not a manifest field.",
            )
    if spec.name == "codex" and manifest.get("mcpServers") != "./.mcp.json":
        raise InstallError("INVALID_PLUGIN", "Plugin must expose the session-scoped MCP bridge.")
    required = [*spec.payload_files, "hooks/hooks.json"]
    for relative in required:
        if not (source_root / relative).is_file():
            raise InstallError("SOURCE_INCOMPLETE", f"Required plugin file is missing: {relative}")
    plugin_version = str(manifest.get("version", ""))
    skill_version = _skill_version(skill_path)
    if not plugin_version or plugin_version != skill_version:
        raise InstallError("VERSION_MISMATCH", "Plugin and canonical Skill versions differ.")
    return {"version": plugin_version, "source": str(source_root)}


def _stage_plugin(source_root: Path, destination_parent: Path, spec: HarnessSpec) -> Path:
    destination_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}.stage-", dir=destination_parent))
    for relative in spec.payload_dirs:
        shutil.copytree(source_root / relative, stage / relative)
    for relative in spec.payload_files:
        target = stage / relative
        # Antigravity's manifest lives under .agents/, so payload files can be nested.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, target)
    _validate_source(stage, spec)
    return stage


def _stage_skill(source_root: Path, destination_parent: Path, spec: HarnessSpec) -> Path:
    destination_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}.stage-", dir=destination_parent))
    source = source_root / "skills" / PLUGIN_NAME
    for item in source.iterdir():
        target = stage / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    if _skill_version(stage / "SKILL.md") != _validate_source(source_root, spec)["version"]:
        raise InstallError("VERSION_MISMATCH", "Staged Skill version differs from the plugin.")
    return stage


def _replace_tree(stage: Path, destination: Path) -> None:
    backup = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
    replaced = False
    try:
        if destination.exists():
            destination.rename(backup)
        stage.rename(destination)
        replaced = True
    except OSError as exc:
        if not destination.exists() and backup.exists():
            backup.rename(destination)
        raise InstallError(
            "INSTALL_REPLACE_FAILED", "Could not atomically replace global files."
        ) from exc
    finally:
        if replaced and backup.exists():
            shutil.rmtree(backup)
        if stage.exists():
            shutil.rmtree(stage)


def _marketplace_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "Personal"},
            "plugins": [],
        }
    value = _read_json(path)
    if value.get("name") != MARKETPLACE_NAME:
        raise InstallError(
            "MARKETPLACE_NAME_CONFLICT",
            "Default personal marketplace exists with a different top-level name.",
        )
    if not isinstance(value.get("plugins", []), list):
        raise InstallError("INVALID_MARKETPLACE", "Marketplace plugins must be an array.")
    return value


def _write_marketplace(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.atomic-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _upsert_marketplace(path: Path) -> bool:
    value = _marketplace_document(path)
    plugins = value.setdefault("plugins", [])
    assert isinstance(plugins, list)
    existing = next(
        (index for index, item in enumerate(plugins) if item.get("name") == PLUGIN_NAME), None
    )
    changed = existing is None or plugins[existing] != MARKETPLACE_ENTRY
    if existing is None:
        plugins.append(MARKETPLACE_ENTRY)
    else:
        plugins[existing] = MARKETPLACE_ENTRY
    if changed or not path.exists():
        _write_marketplace(path, value)
    return changed


def _remove_marketplace_entry(path: Path) -> bool:
    if not path.exists():
        return False
    value = _marketplace_document(path)
    plugins = value.setdefault("plugins", [])
    assert isinstance(plugins, list)
    retained = [item for item in plugins if item.get("name") != PLUGIN_NAME]
    if len(retained) == len(plugins):
        return False
    value["plugins"] = retained
    _write_marketplace(path, value)
    return True


def _native(spec: HarnessSpec, *arguments: str, required: bool = True) -> Any:
    executable = shutil.which(spec.cli)
    if not executable:
        if required:
            raise InstallError(
                "HARNESS_CLI_UNAVAILABLE",
                f"{spec.cli} CLI is required for native registration.",
            )
        return subprocess.CompletedProcess([spec.cli, *arguments], 127, "", "not installed")
    result = subprocess.run(  # noqa: S603 - executable and arguments are closed internally
        [executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if required and result.returncode != 0:
        raise InstallError(
            "HARNESS_REGISTER_COMMAND_FAILED", f"{spec.cli} registration command failed."
        )
    return result


def _native_registered(spec: HarnessSpec) -> bool:
    if not spec.native_cli:
        # File placement is the registration; there is nothing to ask a CLI about.
        return True
    result = _native(spec, *spec.list_verb, required=False)
    return result.returncode == 0 and spec.target() in result.stdout


def _upsert_mcp_config(path: Path) -> bool:
    """Merge the bridge entry into a shared MCP config without disturbing other servers.

    Antigravity keeps every MCP server for the CLI and IDE in one file, so this must be a
    merge. Rewriting the document would silently delete the owner's other servers.
    """
    document: dict[str, Any] = {}
    if path.is_file():
        document = _read_json(path)
    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    desired = {"command": "sqlctx-mcp-bridge", "args": [], "disabled": False}
    if servers.get(PLUGIN_NAME) == desired:
        return False
    servers[PLUGIN_NAME] = desired
    document["mcpServers"] = servers
    _write_marketplace(path, document)
    return True


def _remove_mcp_config_entry(path: Path) -> bool:
    """Drop only our own server, leaving every other entry and the file itself in place."""
    if not path.is_file():
        return False
    document = _read_json(path)
    servers = document.get("mcpServers")
    if not isinstance(servers, dict) or PLUGIN_NAME not in servers:
        return False
    del servers[PLUGIN_NAME]
    document["mcpServers"] = servers
    _write_marketplace(path, document)
    return True


def _register_native(spec: HarnessSpec, destination: Path, *, refresh: bool) -> None:
    if not spec.native_cli:
        return
    registered = _native_registered(spec)
    if registered and refresh:
        _native(spec, *spec.remove_verb, spec.target())
        registered = False
    if not registered:
        # Gemini installs a local extension directory by path; the others resolve a marketplace id.
        argument = str(destination) if spec.name == "gemini" else spec.target()
        extra = () if spec.name == "gemini" else ("--json",)
        _native(spec, *spec.install_verb, argument, *extra)
    if not _native_registered(spec):
        raise InstallError(
            "HARNESS_PLUGIN_NOT_DISCOVERED",
            f"{spec.cli} did not report the plugin or extension installed.",
        )


def _installed_version(path: Path, mode: str, spec: HarnessSpec) -> str | None:
    try:
        if mode == "plugin":
            return str(_read_json(path / spec.manifest).get("version"))
        return _skill_version(path / "SKILL.md")
    except (InstallError, OSError):
        return None


def install(
    source_root: Path,
    home: Path,
    *,
    mode: str,
    update: bool,
    register_native: bool,
    harness: str = "codex",
) -> dict[str, Any]:
    spec = _harness(harness)
    _assert_mode(spec, mode)
    source = _validate_source(source_root, spec)
    paths = _paths(home, spec)
    if register_native and paths["home"] != Path.home().resolve():
        raise InstallError(
            "HOME_REGISTRATION_MISMATCH", "Native registration requires the real home."
        )
    destination = paths[mode]
    conflict = paths["skill" if mode == "plugin" else "plugin"]
    # Gemini uses one extensions directory for both modes, so they cannot collide.
    distinct_modes = paths["skill"] != paths["plugin"]
    if distinct_modes and (
        conflict.exists() or (mode == "skill" and register_native and _native_registered(spec))
    ):
        raise InstallError(
            "DUPLICATE_DISCOVERY_MODE",
            "Remove the other global discovery mode before installing this one.",
        )
    if update and not destination.exists():
        raise InstallError("NOT_INSTALLED", "The selected global mode is not installed yet.")
    stage = (
        _stage_plugin(source_root, destination.parent, spec)
        if mode == "plugin"
        else _stage_skill(source_root, destination.parent, spec)
    )
    before_hash = _inventory_hash(destination)
    after_hash = _inventory_hash(stage)
    changed = before_hash != after_hash
    installed_version = _installed_version(destination, mode, spec)
    if changed and destination.exists() and not update:
        shutil.rmtree(stage)
        code = (
            "SAME_VERSION_CONTENT_DRIFT"
            if installed_version == source["version"]
            else "UPDATE_REQUIRED"
        )
        raise InstallError(code, "Installed content differs; run the explicit update operation.")
    if changed:
        _replace_tree(stage, destination)
    else:
        shutil.rmtree(stage)
    marketplace_changed = False
    if spec.mcp_config:
        marketplace_changed = _upsert_mcp_config(paths["home"].joinpath(*spec.mcp_config))
    if not spec.native_cli:
        # File-based hosts still need provenance for doctor and update.
        _write_marketplace(
            destination / ".sqlctx-install.json",
            {
                "schema_version": 1,
                "source_root": str(source_root.resolve()),
                "mode": mode,
                "harness": spec.name,
                "version": source["version"],
            },
        )
    if mode == "plugin":
        if spec.uses_marketplace:
            marketplace_changed = _upsert_marketplace(paths["marketplace"])
        _write_marketplace(
            destination / ".sqlctx-install.json",
            {
                "schema_version": 1,
                "source_root": str(source_root.resolve()),
                "mode": mode,
                "harness": spec.name,
                "version": source["version"],
            },
        )
        if register_native:
            _register_native(spec, destination, refresh=changed or marketplace_changed)
    registered = _native_registered(spec) if register_native else None
    return {
        "ok": True,
        "operation": "update" if update else "install",
        "harness": spec.name,
        "mode": mode,
        "version": source["version"],
        "changed": changed,
        "marketplace_changed": marketplace_changed,
        "inventory_sha256": _inventory_hash(destination),
        "codex_registered": registered if spec.name == "codex" else None,
        "native_registered": registered,
        "service_restart_performed": False,
        "current_shell_ready": True,
        "new_codex_room_required": changed or marketplace_changed,
        "new_room_required": changed or marketplace_changed,
    }


def status(
    source_root: Path, home: Path, *, check_native: bool, harness: str = "codex"
) -> dict[str, Any]:
    spec = _harness(harness)
    source = _validate_source(source_root, spec)
    paths = _paths(home, spec)
    marketplace = (
        _marketplace_document(paths["marketplace"]) if paths["marketplace"].exists() else {}
    )
    entries = marketplace.get("plugins", []) if isinstance(marketplace, dict) else []
    registered_entry = any(
        isinstance(item, dict) and item.get("name") == PLUGIN_NAME for item in entries
    )
    plugin_hash = _inventory_hash(paths["plugin"])
    skill_hash = _inventory_hash(paths["skill"])
    source_stage = _stage_plugin(source_root, paths["plugin"].parent, spec)
    source_plugin_hash = _inventory_hash(source_stage)
    shutil.rmtree(source_stage)
    registered = _native_registered(spec) if check_native else None
    return {
        "ok": True,
        "harness": spec.name,
        "source_version": source["version"],
        "plugin": {
            "installed": plugin_hash is not None,
            "version": _installed_version(paths["plugin"], "plugin", spec),
            "hash_matches_source": plugin_hash == source_plugin_hash if plugin_hash else None,
            "marketplace_registered": registered_entry if spec.uses_marketplace else None,
            "codex_registered": registered if spec.name == "codex" else None,
            "native_registered": registered,
        },
        "skill_fallback": {
            "installed": skill_hash is not None,
            "version": _installed_version(paths["skill"], "skill", spec),
        },
        "duplicate_mode": (
            plugin_hash is not None and skill_hash is not None and paths["skill"] != paths["plugin"]
        ),
    }


def remove(
    home: Path,
    *,
    mode: str,
    register_native: bool,
    confirmed: bool,
    harness: str = "codex",
) -> dict[str, Any]:
    if not confirmed:
        raise InstallError("CONFIRMATION_REQUIRED", "Removal requires --yes.")
    spec = _harness(harness)
    _assert_mode(spec, mode)
    paths = _paths(home, spec)
    destination = paths[mode]
    if register_native and paths["home"] != Path.home().resolve():
        raise InstallError("HOME_REGISTRATION_MISMATCH", "Native removal requires the real home.")
    if mode == "plugin" and register_native and spec.native_cli and _native_registered(spec):
        extra = () if spec.name == "gemini" else ("--json",)
        _native(spec, *spec.remove_verb, spec.target(), *extra)
    removed_files = False
    if destination.exists():
        shutil.rmtree(destination)
        removed_files = True
    marketplace_changed = (
        _remove_marketplace_entry(paths["marketplace"])
        if mode == "plugin" and spec.uses_marketplace
        else False
    )
    if spec.mcp_config:
        marketplace_changed = (
            _remove_mcp_config_entry(paths["home"].joinpath(*spec.mcp_config))
            or marketplace_changed
        )
    return {
        "ok": True,
        "operation": "remove",
        "harness": spec.name,
        "mode": mode,
        "removed_files": removed_files,
        "marketplace_changed": marketplace_changed,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["install", "update", "status", "remove"])
    parser.add_argument("--mode", choices=["plugin", "skill"], default="plugin")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--harness", choices=sorted(HARNESSES), default="codex")
    parser.add_argument(
        "--skip-register",
        "--skip-codex-register",
        dest="skip_register",
        action="store_true",
        help="Stage files only; do not call the native plugin/extension CLI.",
    )
    parser.add_argument("--yes", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.operation == "status":
            result = status(
                args.source_root,
                args.home,
                check_native=not args.skip_register,
                harness=args.harness,
            )
        elif args.operation == "remove":
            result = remove(
                args.home,
                mode=args.mode,
                register_native=not args.skip_register,
                confirmed=args.yes,
                harness=args.harness,
            )
        else:
            result = install(
                args.source_root,
                args.home,
                mode=args.mode,
                update=args.operation == "update",
                register_native=not args.skip_register,
                harness=args.harness,
            )
    except InstallError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
