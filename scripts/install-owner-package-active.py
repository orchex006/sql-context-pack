"""Update owner Python package files without replacing locked console launchers."""

from __future__ import annotations

import argparse
import json
import shutil
import site
import subprocess
import sys
import tempfile
import tomllib
import uuid
from pathlib import Path


class ActivePackageInstallError(RuntimeError):
    pass


def _dependencies(source_root: Path) -> list[str]:
    with (source_root / "pyproject.toml").open("rb") as handle:
        value = tomllib.load(handle)
    dependencies = value.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ActivePackageInstallError("pyproject.toml has no valid project dependencies.")
    return dependencies


def _replace_tree(source: Path, destination: Path) -> None:
    token = uuid.uuid4().hex
    backup = destination.parent / f".{destination.name}.backup-{token}"
    if destination.exists():
        shutil.copytree(destination, backup)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        source_files = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
        for relative in sorted(source_files):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(f".{target.name}.stage-{token}")
            shutil.copy2(source / relative, stage)
            stage.replace(target)
        for old in sorted(
            (path for path in destination.rglob("*") if path.is_file()), reverse=True
        ):
            relative = old.relative_to(destination)
            if relative not in source_files and "__pycache__" not in relative.parts:
                old.unlink()
    except BaseException:
        if backup.exists():
            shutil.copytree(backup, destination, dirs_exist_ok=True)
        raise
    finally:
        for stage in destination.rglob(f"*.stage-{token}"):
            stage.unlink(missing_ok=True)
        if backup.exists():
            shutil.rmtree(backup)


def install(
    source_root: Path,
    user_site: Path,
    *,
    package_artifact: Path | None = None,
    install_dependencies: bool = True,
) -> dict[str, object]:
    source_root = source_root.resolve()
    user_site = user_site.resolve()
    for name in ("pyproject.toml", "src/sqlctx"):
        if not (source_root / name).exists():
            raise ActivePackageInstallError(f"Active-room package source is incomplete: {name}")

    if install_dependencies:
        subprocess.run(  # noqa: S603 - fixed interpreter and validated pinned dependencies.
            [sys.executable, "-m", "pip", "install", "--user", *_dependencies(source_root)],
            check=True,
        )
    install_target = (package_artifact or source_root).resolve()
    with tempfile.TemporaryDirectory(prefix="sqlctx-owner-package-") as temporary:
        staged = Path(temporary)
        subprocess.run(  # noqa: S603 - fixed current interpreter and owner-trusted source path.
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--target",
                str(staged),
                "--no-deps",
                str(install_target),
            ],
            check=True,
        )
        package = staged / "sqlctx"
        dist_infos = list(staged.glob("sql_context_pack-*.dist-info"))
        if not package.is_dir() or len(dist_infos) != 1:
            raise ActivePackageInstallError("Staged owner package is incomplete.")
        user_site.mkdir(parents=True, exist_ok=True)
        _replace_tree(package, user_site / "sqlctx")
        for old in user_site.glob("sql_context_pack-*.dist-info"):
            if old.name != dist_infos[0].name:
                shutil.rmtree(old)
        _replace_tree(dist_infos[0], user_site / dist_infos[0].name)

    return {
        "ok": True,
        "mode": "active-room-safe",
        "user_site": str(user_site),
        "console_launchers_preserved": True,
        "dependencies_installed": install_dependencies,
        "new_codex_room_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--user-site", type=Path, default=Path(site.getusersitepackages()))
    parser.add_argument("--package-artifact", type=Path)
    parser.add_argument("--skip-dependencies", action="store_true")
    arguments = parser.parse_args()
    try:
        result = install(
            arguments.source_root,
            arguments.user_site,
            package_artifact=arguments.package_artifact,
            install_dependencies=not arguments.skip_dependencies,
        )
    except (ActivePackageInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
