[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  $results = @()
  foreach ($name in @('codex', 'claude', 'gemini')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $command) {
      throw "Required release smoke harness is not installed: $name"
    }
    $version = & $name --version 2>$null | Select-Object -First 1
    $results += [pscustomobject]@{ harness = $name; version = $version; installed = $true }
  }
  claude plugin validate .
  gemini extensions validate .
  python scripts\validate_manifests.py
  $results | ConvertTo-Json -Depth 3
} finally {
  Pop-Location
}
