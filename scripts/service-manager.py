"""Owner entry point for the cross-platform service supervisor.

The implementation lives in ``sqlctx.service.manager`` so that it is importable from an
installed package; this file stays as the documented script path.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlctx.service.manager import detect_host_os, manage  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["install", "update", "status", "remove", "start", "stop"]
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--os", choices=["windows", "macos", "linux", "unix"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    host_os = args.os or detect_host_os()
    result = manage(args.operation, python=args.python.resolve(), port=args.port, host_os=host_os)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
