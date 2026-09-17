#!/usr/bin/env sh
set -eu

echo "Read the owner-only connection metadata, set SQLCTX_INSPECTOR_TOKEN locally, then run:"
echo 'npx --yes @modelcontextprotocol/inspector --transport streamable-http --server-url http://127.0.0.1:8765/mcp --header "Authorization: Bearer $SQLCTX_INSPECTOR_TOKEN"'
