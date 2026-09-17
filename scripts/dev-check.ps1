[CmdletBinding()]
param(
    [ValidateSet('all', 'clean', 'format', 'lint', 'typecheck', 'test', 'build')]
    [string]$Task = 'all',
    [string[]]$TestPath = @('tests')
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('sqlctx-dev-' + [Guid]::NewGuid().ToString('N'))
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MYPYPATH = Join-Path $repositoryRoot 'src'

function Invoke-Checked([string[]]$Arguments) {
    & python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Development command failed: python $($Arguments -join ' ')" }
}

function Remove-RepositoryResidue {
    $directoryNames = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'build', 'dist')
    Get-ChildItem -LiteralPath $repositoryRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in $directoryNames -or $_.Name.EndsWith('.egg-info') } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
}

try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    Remove-RepositoryResidue
    Push-Location $repositoryRoot
    if ($Task -in @('all', 'format')) { Invoke-Checked @('-m', 'ruff', 'format', '--check', '--cache-dir', (Join-Path $tempRoot 'ruff'), '.') }
    if ($Task -in @('all', 'lint')) { Invoke-Checked @('-m', 'ruff', 'check', '--cache-dir', (Join-Path $tempRoot 'ruff'), '.') }
    if ($Task -in @('all', 'typecheck')) {
        # CI runs on Linux while most development happens on Windows. Platform-specific
        # modules (msvcrt, fcntl) and stat attributes only type-check on the platform mypy
        # targets, so checking the host alone lets Linux-only errors reach CI. Check all
        # three targets here instead.
        foreach ($platform in @('linux', 'darwin', 'win32')) {
            Invoke-Checked @('-m', 'mypy', '--platform', $platform, '--cache-dir', (Join-Path $tempRoot "mypy-$platform"))
        }
    }
    if ($Task -in @('all', 'test')) {
        Invoke-Checked (@('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', (Join-Path $tempRoot 'pytest')) + $TestPath)
    }
    if ($Task -in @('all', 'build')) {
        $buildSource = Join-Path $tempRoot 'source'
        New-Item -ItemType Directory -Path $buildSource | Out-Null
        foreach ($name in @('pyproject.toml', 'README.md', 'LICENSE', 'src')) {
            Copy-Item -LiteralPath (Join-Path $repositoryRoot $name) -Destination $buildSource -Recurse
        }
        Invoke-Checked @('-m', 'build', '--no-isolation', '--outdir', (Join-Path $tempRoot 'dist'), $buildSource)
    }
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-RepositoryResidue
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
}

$remaining = @(Get-ChildItem -LiteralPath $repositoryRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'build', 'dist') -or $_.Name.EndsWith('.egg-info') })
if ($remaining.Count -ne 0) { throw 'Development residue remained after cleanup.' }
Write-Output 'Development verification completed with no repository-local cache/build residue.'
