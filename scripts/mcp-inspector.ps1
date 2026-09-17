param(
  [ValidatePattern('^http://127\.0\.0\.1:[0-9]{1,5}/mcp$')]
  [string]$Url = "http://127.0.0.1:8765/mcp"
)

$ErrorActionPreference = "Stop"
$endpoint = [Uri]$Url
if ($endpoint.Port -lt 1 -or $endpoint.Port -gt 65535) { throw "Invalid loopback port." }
$metadata = Join-Path $env:LOCALAPPDATA "sql-context-pack\connection-metadata.json"
if (-not (Test-Path -LiteralPath $metadata)) {
  throw "Start sqlctx-server before MCP Inspector."
}
$connection = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
$env:SQLCTX_INSPECTOR_TOKEN = $connection.agent_token
npx --yes @modelcontextprotocol/inspector --transport streamable-http --server-url $Url --header "Authorization: Bearer `$env:SQLCTX_INSPECTOR_TOKEN"
