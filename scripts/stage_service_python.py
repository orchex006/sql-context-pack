"""Copy the selected CPython runtime (not user packages) into protected service staging."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def stage(destination: Path) -> None:
    source = Path(sys.base_prefix).resolve()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Interpreter staging destination must not exist")
    destination.mkdir(parents=True)
    for name in ("Lib", "DLLs"):
        shutil.copytree(
            source / name,
            destination / name,
            ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "test", "tests"),
        )
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll", "python*.zip"):
        for item in source.glob(pattern):
            shutil.copy2(item, destination / item.name)
    if not (destination / "python.exe").is_file():
        raise ValueError("A standard Windows CPython installation is required")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    stage(parser.parse_args().destination)
