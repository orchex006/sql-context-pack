"""Compute deterministic, credential-free installation layer fingerprints."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import sysconfig
import tomllib
from pathlib import Path


def _hash_files(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _tree_files(root: Path, relative: str) -> list[Path]:
    directory = root / relative
    return (
        [
            path
            for path in directory.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ]
        if directory.exists()
        else []
    )


def _tree_hash(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    return _hash_files(directory, _tree_files(directory, "."))


def compute(
    source_root: Path, extras: list[str], installed_package: Path | None = None
) -> dict[str, object]:
    root = source_root.resolve()
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    dependencies = sorted(str(item) for item in project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    selected_optional = {
        extra: sorted(str(item) for item in optional.get(extra, []))
        for extra in sorted(set(extras))
    }
    python_identity = {
        "executable": str(Path(sys.executable).resolve()),
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "abi": sysconfig.get_config_var("SOABI"),
        "platform": sysconfig.get_platform(),
    }
    dependency_payload = json.dumps(
        {
            "dependencies": dependencies,
            "optional": selected_optional,
            "python": python_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    app_files = [root / "pyproject.toml", *_tree_files(root, "src/sqlctx")]
    plugin_files = [
        root / ".codex-plugin/plugin.json",
        root / ".mcp.json",
        root / "gemini-extension.json",
        *_tree_files(root, "skills/sql-context-pack"),
        *_tree_files(root, "hooks"),
    ]
    host_files = [
        root / "scripts/sqlctx_windows_service.py",
        root / "scripts/windows-service.ps1",
        root / "scripts/stage_service_python.py",
        root / "scripts/grant_service_folders.py",
    ]
    result: dict[str, object] = {
        "schema_version": 1,
        "app_fingerprint": _hash_files(root, [path for path in app_files if path.is_file()]),
        "package_fingerprint": _tree_hash(root / "src/sqlctx"),
        "dependency_fingerprint": "sha256:" + hashlib.sha256(dependency_payload).hexdigest(),
        "plugin_fingerprint": _hash_files(root, [path for path in plugin_files if path.is_file()]),
        "service_host_fingerprint": _hash_files(
            root, [path for path in host_files if path.is_file()]
        ),
        "python": python_identity,
        "extras": sorted(set(extras)),
    }
    if installed_package is not None:
        result["installed_package_fingerprint"] = _tree_hash(installed_package.resolve())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--installed-package", type=Path)
    arguments = parser.parse_args()
    print(
        json.dumps(
            compute(arguments.source_root, arguments.extra, arguments.installed_package),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
