[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$required = [Version]'3.11'
$candidates = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidates += @{ Command = 'py'; Prefix = @('-3') }
}
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $candidates += @{ Command = 'python3'; Prefix = @() }
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    $candidates += @{ Command = 'python'; Prefix = @() }
}

foreach ($candidate in $candidates) {
    try {
        $args = @($candidate.Prefix) + @('-c', 'import json,sys; print(json.dumps({"executable":sys.executable,"version":".".join(map(str,sys.version_info[:3]))}))')
        $result = & $candidate.Command @args 2>$null | Select-Object -First 1 | ConvertFrom-Json
        if ([Version]$result.version -ge $required) {
            [pscustomobject]@{
                status = 'ready'
                executable = $result.executable
                version = $result.version
                required_version = '>=3.11'
            } | ConvertTo-Json -Compress
            exit 0
        }
    } catch {
        continue
    }
}

[pscustomobject]@{
    status = 'error'
    code = 'PYTHON_UNAVAILABLE'
    required_version = '>=3.11'
    guidance = 'Install supported CPython from https://www.python.org/downloads/windows/ or the official Python Install Manager, then run: py -3 --version'
} | ConvertTo-Json -Compress
exit 3
