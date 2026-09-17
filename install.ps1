[CmdletBinding()]
param(
    [ValidateSet('codex', 'claude', 'gemini', 'agy')]
    [string]$Harness = 'codex',
    [switch]$Update,
    [switch]$Repair,
    [switch]$SkipConfigure,
    [switch]$NativePlugin,
    [ValidateSet('auto', 'mcp', 'package', 'service')]
    [string]$RepairComponent = 'auto'
)

$ErrorActionPreference = 'Stop'
$installClock = [Diagnostics.Stopwatch]::StartNew()
$stageTimings = [ordered]@{}
function Complete-InstallStage([string]$Name, [long]$StartedAt) {
    $stageTimings[$Name] = [Math]::Round(($installClock.ElapsedMilliseconds - $StartedAt) / 1000.0, 3)
}
if ($Update -and $Repair) { throw 'Choose either -Update or -Repair, not both.' }
$installedPlugin = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'plugins\sql-context-pack'
$operation = if ($Update) { 'update' } elseif ($Repair -and (Test-Path -LiteralPath $installedPlugin)) { 'update' } else { 'install' }
$installer = Join-Path $PSScriptRoot 'scripts\install-global.ps1'
$stageStart = $installClock.ElapsedMilliseconds
$preflightOutput = & (Join-Path $PSScriptRoot 'scripts\python-preflight.ps1')
if ($LASTEXITCODE -ne 0) {
    $preflightOutput | Write-Output
    exit $LASTEXITCODE
}
Complete-InstallStage 'preflight' $stageStart
$python = ($preflightOutput | ConvertFrom-Json).executable
$fingerprint = (& $python (Join-Path $PSScriptRoot 'scripts\install_fingerprint.py') --source-root $PSScriptRoot) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not compute installation fingerprints.' }
$ownerStatePath = Join-Path $env:LOCALAPPDATA 'sql-context-pack\install-state.json'
$serviceStatePath = Join-Path $env:ProgramData 'SQLContextPack\install-state.json'
$ownerState = if (Test-Path -LiteralPath $ownerStatePath) { Get-Content -Raw -LiteralPath $ownerStatePath | ConvertFrom-Json } else { $null }
$serviceState = if (Test-Path -LiteralPath $serviceStatePath) { Get-Content -Raw -LiteralPath $serviceStatePath | ConvertFrom-Json } else { $null }
$wheelRequired = -not $ownerState -or -not $serviceState -or $ownerState.app_fingerprint -ne $fingerprint.app_fingerprint -or $serviceState.app_fingerprint -ne $fingerprint.app_fingerprint
$artifactRoot = $null
$packageArtifact = $null

try {
$stageStart = $installClock.ElapsedMilliseconds
if ($wheelRequired) {
    $artifactRoot = Join-Path ([IO.Path]::GetTempPath()) ('sqlctx-install-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $artifactRoot | Out-Null
    $wheelSource = Join-Path $artifactRoot 'source'
    New-Item -ItemType Directory -Path $wheelSource | Out-Null
    foreach ($sourceFile in @('pyproject.toml', 'README.md', 'LICENSE')) {
        $candidate = Join-Path $PSScriptRoot $sourceFile
        if (Test-Path -LiteralPath $candidate) { Copy-Item -LiteralPath $candidate -Destination $wheelSource }
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'src') -Destination $wheelSource -Recurse
    Get-ChildItem -LiteralPath (Join-Path $wheelSource 'src') -Directory -Recurse -Force |
        Where-Object { $_.Name -match '(\.egg-info|__pycache__)$' } |
        Remove-Item -Recurse -Force
    Write-Output '[build] Application fingerprint changed; building one wheel for owner package and Windows Service.'
    & $python -m pip wheel --no-deps --wheel-dir $artifactRoot $wheelSource
    if ($LASTEXITCODE -ne 0) { throw 'Application wheel build failed.' }
    $wheels = @(Get-ChildItem -LiteralPath $artifactRoot -Filter 'sql_context_pack-*.whl')
    if ($wheels.Count -ne 1) { throw 'Expected exactly one SQL Context Pack wheel.' }
    $packageArtifact = $wheels[0].FullName
} else {
    Write-Output '[build] Cache hit: application fingerprint unchanged; wheel build skipped.'
}
Complete-InstallStage 'wheel' $stageStart

$runOwnerInstall = -not ($Repair -and $RepairComponent -eq 'service')
if ($runOwnerInstall) {
    $stageStart = $installClock.ElapsedMilliseconds
    $globalArgs = @{ Operation = $operation; Mode = 'plugin'; Harness = $Harness; SkipPluginInstall = $NativePlugin }
    if ($packageArtifact) { $globalArgs.PackageArtifact = $packageArtifact }
    if ($Repair -and $RepairComponent -in @('mcp', 'package')) {
        $globalArgs.ForcePackageInstall = $true
    }
    & $installer @globalArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    Complete-InstallStage 'owner_package_plugin_mcp' $stageStart
}

if (-not $SkipConfigure -and $RepairComponent -notin @('mcp', 'service')) {
    $stageStart = $installClock.ElapsedMilliseconds
    $profileState = & $python -m sqlctx.cli profile list | ConvertFrom-Json
    if ($profileState.configured -eq 0) {
        Write-Output 'No database profile exists; starting secure interactive setup.'
        & $python -m sqlctx.cli.configure
        if ($LASTEXITCODE -ne 0) {
            Write-Output 'The package is installed, but profile connection validation did not pass.'
            Write-Output 'Correct the reported owner-side connection setting, then run:'
            Write-Output "& '$python' -m sqlctx.cli profile test <profile-name>"
            exit $LASTEXITCODE
        }
    }
    Complete-InstallStage 'profile_configuration' $stageStart
}

$runServiceInstall = -not ($Repair -and $RepairComponent -in @('mcp', 'package'))
if ($runServiceInstall) {
    $stageStart = $installClock.ElapsedMilliseconds
    & $python -B (Join-Path $PSScriptRoot 'scripts\grant_service_folders.py')
    if ($LASTEXITCODE -ne 0) { throw 'Registered folder permission migration failed; service elevation was not started.' }
    Write-Output 'Preparing the managed Windows Service. Administrator access is requested only for ProgramData ACLs and service registration.'
    Write-Output 'The service binds to 127.0.0.1; no firewall rule or remote network access is requested.'
    $serviceInstaller = Join-Path $PSScriptRoot 'scripts\windows-service.ps1'
    $serviceArgs = @{ Operation = $operation; SourceRoot = $PSScriptRoot; PythonExecutable = $python }
    if ($packageArtifact) { $serviceArgs.PackageArtifact = $packageArtifact }
    & $serviceInstaller @serviceArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'Package/plugin installation completed, but Windows Service installation or health verification failed.'
        exit $LASTEXITCODE
    }
    Complete-InstallStage 'windows_service' $stageStart
}

Write-Output 'Installation is ready in this terminal. Start normal Codex; the plugin provides MCP automatically.'
Write-Output 'Inside Codex, run: $sql-context-pack profiles, then $sql-context-pack connect <profile-name>'
if ($Update -or $Repair) {
    Write-Output 'The service and plugin files are updated. Open a new Codex room to load changed Skill content.'
}
} finally {
    if ($artifactRoot -and (Test-Path -LiteralPath $artifactRoot)) {
        Remove-Item -LiteralPath $artifactRoot -Recurse -Force
    }
    $stageTimings['total'] = [Math]::Round($installClock.Elapsed.TotalSeconds, 3)
    Write-Output (('[timing] ' + ($stageTimings | ConvertTo-Json -Compress)))
}
