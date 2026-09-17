#!/usr/bin/env sh
set -eu

for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      "$candidate" -c 'import json,sys; print(json.dumps({"status":"ready","executable":sys.executable,"version":".".join(map(str,sys.version_info[:3])),"required_version":">=3.11"},separators=(",",":")))'
      exit 0
    fi
  fi
done

printf '%s\n' '{"status":"error","code":"PYTHON_UNAVAILABLE","required_version":">=3.11","guidance":"Install Python >=3.11 from https://www.python.org/downloads/ or an owner-approved OS package manager, then run: python3 --version"}'
exit 3
