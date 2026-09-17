[CmdletBinding()]
param(
    [switch]$Repair
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$installer = Join-Path $repositoryRoot 'install.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw 'SQL Context Pack bootstrap cannot find its bundled installer.'
}

Write-Output '[SQL Context Pack bootstrap] Installing the owner Python package and loopback Windows Service from this installed plugin/extension.'
Write-Output '[SQL Context Pack bootstrap] Python is never installed automatically. Existing compatible dependencies and application layers are reused by fingerprint.'
Write-Output '[SQL Context Pack bootstrap] Windows requests Administrator access only for ProgramData ACLs and Windows Service registration; no firewall rule is created.'

if ($Repair) { & (Join-Path $PSScriptRoot 'lifecycle.ps1') -Operation update }
else { & (Join-Path $PSScriptRoot 'lifecycle.ps1') -Operation install }
exit $LASTEXITCODE
