#!/usr/bin/env bash
# POSIX installer for SQL Context Pack (Linux, macOS, and other Unix hosts).
#
# Windows keeps install.ps1 because service registration goes through the SCM and needs a
# dedicated service account and ACLs. Everywhere else the same managed service runs under
# the owner's own account, supervised by systemd --user, launchd, or a pidfile fallback --
# all of which scripts/service-manager.py already selects. This script is the thin owner
# entry point that wires the package, the host plugin, and that supervisor together.
#
# Exactly one service instance exists per machine. Every harness session connects to it
# through its own stdio bridge; this never starts a second server.

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="codex"
OPERATION="install"
MODE=""
SKIP_SERVICE=0
PORT=8765

usage() {
    cat <<'USAGE'
Usage: install.sh [options]

  --harness <codex|claude|gemini|agy>  Host to register the Skill with (default: codex)
  --update                             Update an existing installation in place
  --repair                             Reinstall local components without a Git refresh
  --mode <plugin|skill>                Discovery layout (default: plugin; agy: skill)
  --skip-service                       Install files only; do not touch the service
  --port <n>                           Loopback service port (default: 8765)
  -h, --help                           Show this help

This script never creates a virtualenv and never writes into the repository.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --harness) HARNESS="${2:?--harness requires a value}"; shift 2 ;;
        --update) OPERATION="update"; shift ;;
        # global_install.py has no separate repair verb; a reinstall over the same
        # tree is the repair, and --update is what makes it tolerate existing files.
        --repair) OPERATION="update"; shift ;;
        --mode) MODE="${2:?--mode requires a value}"; shift 2 ;;
        --skip-service) SKIP_SERVICE=1; shift ;;
        --port) PORT="${2:?--port requires a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$HARNESS" in
    codex|claude|gemini|agy) ;;
    *) echo "Unsupported harness: $HARNESS (expected codex, claude, gemini, or agy)" >&2; exit 2 ;;
esac

step() { printf '[%s] %s\n' "$1" "$2"; }

# --- 1. Interpreter -----------------------------------------------------------------
step preflight "Selecting a host Python interpreter (>=3.11)."
PYTHON="$("$SOURCE_ROOT/scripts/python-preflight.sh" 2>/dev/null | head -n1 || true)"
if [ -z "${PYTHON:-}" ] || [ ! -x "$PYTHON" ]; then
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    done
fi
if [ -z "${PYTHON:-}" ]; then
    echo "No Python interpreter found. Install Python 3.11 or newer and retry." >&2
    exit 1
fi
"$PYTHON" - <<'PY' || { echo "Python 3.11 or newer is required." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
step preflight "Using $PYTHON"

# --- 2. Package ---------------------------------------------------------------------
# Build staging stays under the OS temp directory; nothing is written into the checkout.
BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sqlctx-install-XXXXXXXX")"
cleanup() { rm -rf "$BUILD_ROOT"; }
trap cleanup EXIT INT TERM

step package "Installing the owner package for this interpreter."
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pip install --user --upgrade \
    --no-warn-script-location "$SOURCE_ROOT" >"$BUILD_ROOT/pip.log" 2>&1 || {
        echo "Package installation failed:" >&2
        tail -n 40 "$BUILD_ROOT/pip.log" >&2
        exit 1
    }
INSTALLED_VERSION="$("$PYTHON" -c 'from sqlctx._version import __version__; print(__version__)')"
step package "Installed sql-context-pack $INSTALLED_VERSION"

# --- 3. Host plugin -----------------------------------------------------------------
# Antigravity loads <skills-root>/<name>/SKILL.md, so it only supports the skill layout.
if [ -z "$MODE" ]; then
    if [ "$HARNESS" = "agy" ]; then MODE="skill"; else MODE="plugin"; fi
fi

step plugin "Registering the Skill with $HARNESS (mode: $MODE)."
GLOBAL_INSTALL_ARGS=("$SOURCE_ROOT/scripts/global_install.py" "$OPERATION"
    --source-root "$SOURCE_ROOT" --harness "$HARNESS" --mode "$MODE")
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "${GLOBAL_INSTALL_ARGS[@]}"

# --- 4. Service ---------------------------------------------------------------------
if [ "$SKIP_SERVICE" -eq 1 ]; then
    step service "Skipped by --skip-service."
else
    step service "Installing the single shared loopback service."
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$SOURCE_ROOT/scripts/service-manager.py" \
        install --python "$PYTHON" --port "$PORT"
fi

# --- 5. Verify ----------------------------------------------------------------------
step verify "Checking package, plugin, and service versions agree."
if PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -B -m sqlctx.cli doctor check --host "$HARNESS"; then
    step verify "Installation verified at $INSTALLED_VERSION."
else
    echo >&2
    echo "Files installed, but verification reported drift or an unhealthy service." >&2
    echo "Run: sqlctx doctor --host $HARNESS   and see docs/knowledge/mcp-service.md" >&2
    exit 1
fi

cat <<EOF

SQL Context Pack $INSTALLED_VERSION is installed for $HARNESS.

Next:
  sqlctx profile add          configure a database profile
  sqlctx doctor --host $HARNESS   re-check readiness at any time

Open a new $HARNESS session so the updated Skill instructions load.
EOF
