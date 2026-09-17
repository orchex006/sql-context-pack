[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$preflightOutput = & (Join-Path $PSScriptRoot 'python-preflight.ps1')
if ($LASTEXITCODE -ne 0) {
    $preflightOutput | Write-Output
    exit $LASTEXITCODE
}
$preflight = $preflightOutput | ConvertFrom-Json
& $preflight.executable -m sqlctx.server.http.app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
