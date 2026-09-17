[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'uninstall')]
    [string]$Operation,

    [ValidateSet('codex', 'claude', 'gemini')]
    [string]$Harness = 'codex',

    [switch]$KeepNativePlugin
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$installer = Join-Path $repositoryRoot 'install.ps1'

if ($Operation -in @('install', 'update')) {
    Write-Output "[lifecycle] Deploying the exact $Harness plugin/extension cache into the owner package and managed service."
    if ($Operation -eq 'update') { & $installer -Harness $Harness -Repair -SkipConfigure -NativePlugin } else { & $installer -Harness $Harness -NativePlugin }
    exit $LASTEXITCODE
}

Write-Output '[lifecycle] Uninstall removes SQLContextPack Windows Service, owner Python package, and this native plugin/extension.'
Write-Output '[lifecycle] Encrypted profiles and retained runtime data are preserved unless the owner removes them separately.'
$preflightOutput = & (Join-Path $PSScriptRoot 'python-preflight.ps1')
if ($LASTEXITCODE -ne 0) { $preflightOutput | Write-Output; exit $LASTEXITCODE }
$python = ($preflightOutput | ConvertFrom-Json).executable

& (Join-Path $PSScriptRoot 'windows-service.ps1') -Operation remove -SourceRoot $repositoryRoot -PythonExecutable $python
if ($LASTEXITCODE -ne 0) { throw 'Windows Service removal failed; native plugin removal was not attempted.' }

$bridges = @(Get-Process -Name 'sqlctx-mcp-bridge' -ErrorAction SilentlyContinue)
if ($bridges.Count -gt 0) {
    Write-Output "[lifecycle] Stopping $($bridges.Count) active SQL Context Pack bridge process(es) before package removal."
    $bridges | Stop-Process -Force
}
& $python -m pip uninstall --yes sql-context-pack
if ($LASTEXITCODE -ne 0) { throw 'Owner Python package removal failed.' }
$ownerState = Join-Path $env:LOCALAPPDATA 'sql-context-pack\install-state.json'
Remove-Item -LiteralPath $ownerState -Force -ErrorAction SilentlyContinue

if (-not $KeepNativePlugin) {
    if ($Harness -eq 'codex') {
        & codex plugin remove 'sql-context-pack@sql-context-pack'
        if ($LASTEXITCODE -eq 0) { & codex plugin marketplace remove 'sql-context-pack' }
    } elseif ($Harness -eq 'claude') {
        & claude plugin uninstall 'sql-context-pack@sql-context-pack'
        if ($LASTEXITCODE -eq 0) { & claude plugin marketplace remove 'sql-context-pack' }
    } else {
        & gemini extensions uninstall 'sql-context-pack'
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Managed runtime was removed, but the native plugin/extension manager reported a removal error.'
    }
}

Write-Output '[lifecycle] SQL Context Pack runtime and native plugin/extension removal completed.'
