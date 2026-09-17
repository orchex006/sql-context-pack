[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'remove')]
    [string]$Operation = 'install',

    [ValidateSet('plugin', 'skill')]
    [string]$Mode = 'plugin',

    [ValidateSet('codex', 'claude', 'gemini')]
    [string]$Harness = 'codex',

    [switch]$SkipPackageInstall,

    [switch]$SkipPathUpdate,

    [Alias('SkipCodexRegister')]
    [switch]$SkipRegister,

    [switch]$SkipPluginInstall,

    [switch]$ForcePackageInstall,

    [string]$PackageArtifact,

    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$preflightOutput = & (Join-Path $PSScriptRoot 'python-preflight.ps1')
if ($LASTEXITCODE -ne 0) {
    $preflightOutput | Write-Output
    exit $LASTEXITCODE
}
$preflight = $preflightOutput | ConvertFrom-Json
$ownerStateRoot = Join-Path $env:LOCALAPPDATA 'sql-context-pack'
$ownerInstallState = Join-Path $ownerStateRoot 'install-state.json'
$fingerprint = (& $preflight.executable (Join-Path $PSScriptRoot 'install_fingerprint.py') --source-root $repositoryRoot) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not compute owner package fingerprints.' }
$previousState = if (Test-Path -LiteralPath $ownerInstallState) { Get-Content -Raw -LiteralPath $ownerInstallState | ConvertFrom-Json } else { $null }
$packageChanged = -not $previousState -or $previousState.app_fingerprint -ne $fingerprint.app_fingerprint
$dependenciesChanged = -not $previousState -or $previousState.dependency_fingerprint -ne $fingerprint.dependency_fingerprint
if ($ForcePackageInstall) {
    $packageChanged = $true
    $dependenciesChanged = $true
}
if ($Operation -in @('install', 'update') -and -not $SkipPackageInstall) {
    if (-not $packageChanged -and -not $dependenciesChanged) {
        Write-Output '[package] Cache hit: application and dependency fingerprints are unchanged; pip installation skipped.'
    }
    else {
      $activeBridge = Get-Process -Name 'sqlctx-mcp-bridge' -ErrorAction SilentlyContinue
      if ($activeBridge) {
        Write-Output '[package] An active Codex room is using sqlctx-mcp-bridge.exe.'
        Write-Output '[package] Updating only changed dependency/application layers while preserving locked console launchers.'
        Write-Output '[package] The current room continues on its loaded bridge; a new Codex room loads the updated bridge.'
        $activeArgs = @((Join-Path $PSScriptRoot 'install-owner-package-active.py'), '--source-root', $repositoryRoot)
        if ($PackageArtifact) { $activeArgs += @('--package-artifact', $PackageArtifact) }
        if (-not $dependenciesChanged) { $activeArgs += '--skip-dependencies' }
        & $preflight.executable @activeArgs
      }
      else {
        $target = if ($PackageArtifact) { $PackageArtifact } else { $repositoryRoot }
        $pipArgs = @('-m', 'pip', 'install', '--user', '--upgrade')
        if (-not $dependenciesChanged) { $pipArgs += '--no-deps' }
        $pipArgs += $target
        & $preflight.executable @pipArgs
      }
      if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      New-Item -ItemType Directory -Path $ownerStateRoot -Force | Out-Null
      @{
        schema_version = 1
        app_fingerprint = $fingerprint.app_fingerprint
        package_fingerprint = $fingerprint.package_fingerprint
        dependency_fingerprint = $fingerprint.dependency_fingerprint
        python = $fingerprint.python
      } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ownerInstallState -Encoding UTF8
    }
}

$userScripts = & $preflight.executable -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
if ($LASTEXITCODE -ne 0 -or -not $userScripts) {
    throw 'Unable to resolve the selected host Python user Scripts directory.'
}
$userScripts = [System.IO.Path]::GetFullPath(($userScripts | Select-Object -First 1).Trim())
if ($Operation -in @('install', 'update') -and -not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $pathParts = @($userPath -split ';' | Where-Object { $_ })
    if (-not ($pathParts | Where-Object { [System.IO.Path]::GetFullPath($_.TrimEnd('\')) -eq $userScripts.TrimEnd('\') })) {
        $updatedUserPath = (($pathParts + $userScripts) -join ';')
        [Environment]::SetEnvironmentVariable('Path', $updatedUserPath, 'User')
    }
    if (-not (($env:PATH -split ';') -contains $userScripts)) {
        $env:PATH = "$userScripts;$env:PATH"
    }
}

$installerExit = 0
if (-not $SkipPluginInstall) {
    $arguments = @(
        (Join-Path $PSScriptRoot 'global_install.py'),
        $Operation,
        '--mode',
        $Mode,
        '--harness',
        $Harness,
        '--source-root',
        $repositoryRoot
    )
    if ($SkipRegister) {
        $arguments += '--skip-register'
    }
    if ($Yes) {
        $arguments += '--yes'
    }
    & $preflight.executable @arguments
    $installerExit = $LASTEXITCODE
} else {
    Write-Output '[plugin] Native marketplace owns plugin files; personal marketplace staging skipped.'
}
if ($installerExit -eq 0 -and $Operation -in @('install', 'update') -and -not $SkipPackageInstall) {
    $serverLauncher = Join-Path $userScripts 'sqlctx-server.exe'
    $cliLauncher = Join-Path $userScripts 'sqlctx.exe'
    $bridgeLauncher = Join-Path $userScripts 'sqlctx-mcp-bridge.exe'
    if (
        -not (Test-Path -LiteralPath $serverLauncher) -or
        -not (Test-Path -LiteralPath $cliLauncher) -or
        -not (Test-Path -LiteralPath $bridgeLauncher)
    ) {
        throw 'Global package entry points were not created in the selected host Python user Scripts directory.'
    }
    & $serverLauncher --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'sqlctx-server entry point failed its post-install smoke test.'
    }
    Write-Output "PATH-independent diagnostic server command: & '$serverLauncher' --host 127.0.0.1 --port 8765"
    Write-Output 'The current PowerShell process PATH is ready. A new Codex room is required only when plugin content changed.'
}
exit $installerExit
