[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'remove')]
    [string]$Operation = 'install',

    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,

    [string]$PackageArtifact,

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$serviceName = 'SQLContextPack'
$programDataRoot = Join-Path $env:ProgramData 'SQLContextPack'
$appRoot = Join-Path $programDataRoot 'app'
$interpreterRoot = Join-Path $programDataRoot 'python'
$serviceAccount = 'NT SERVICE\SQLContextPack'
$servicePython = Join-Path $interpreterRoot 'python.exe'
$configRoot = Join-Path $programDataRoot 'config'
$runtimeRoot = Join-Path $programDataRoot 'runtime'
$hostScript = Join-Path $programDataRoot 'sqlctx_windows_service.py'
$serviceConfig = Join-Path $programDataRoot 'service-config.json'
$installState = Join-Path $programDataRoot 'install-state.json'
$metadataPath = Join-Path $runtimeRoot 'connection-metadata.json'
$ownerControlPath = Join-Path $runtimeRoot 'owner-control.json'

function Assert-ManagedTarget([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $allowedRoots = @([IO.Path]::GetFullPath($programDataRoot))
    if ($script:stageRoot) { $allowedRoots += [IO.Path]::GetFullPath($script:stageRoot) }
    $inside = @($allowedRoots | Where-Object { $absolute.StartsWith($_.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) })
    if ($inside.Count -eq 0 -and $absolute -ne $script:stageRoot) { throw 'Refusing an operation outside service staging/installation.' }
    $walk = $absolute
    while ($walk) {
        if (Test-Path -LiteralPath $walk) {
            if ((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Service installation does not follow reparse points.' }
        }
        $walk = [IO.Path]::GetDirectoryName($walk)
    }
}

function Set-ProtectedAcl([string]$Path, [string]$OwnerRights, [string]$ServiceRights, [switch]$Recurse) {
    if ($Path -ne $programDataRoot) { Assert-ManagedTarget $Path }
    if ($Recurse -and @(Get-ChildItem -LiteralPath $Path -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0) { throw 'Service ACL migration does not follow reparse points.' }
    $acl = if (Test-Path -LiteralPath $Path -PathType Container) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
    $acl.SetAccessRuleProtection($true, $false)
    $isDirectory = Test-Path -LiteralPath $Path -PathType Container
    $inherit = if ($isDirectory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($entry in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'), @($owner, $OwnerRights), @($serviceAccount, $ServiceRights))) {
        $identity = if ($entry[0] -like 'S-1-*') { [Security.Principal.SecurityIdentifier]::new($entry[0]) } else { [Security.Principal.NTAccount]::new($entry[0]) }
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, [Security.AccessControl.FileSystemRights]$entry[1], $inherit, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    Set-Acl -LiteralPath $Path -AclObject $acl
    if ($Recurse) {
        & icacls.exe (Join-Path $Path '*') /reset /T /Q | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-ChildItem -LiteralPath $Path -Force)) { throw 'Could not reset inherited service ACLs.' }
        if ($OwnerRights -eq 'ReadAndExecute') {
            & icacls.exe (Join-Path $Path '*') /setowner '*S-1-5-32-544' /T /Q | Out-Null
            if ($LASTEXITCODE -ne 0 -and (Get-ChildItem -LiteralPath $Path -Force)) { throw 'Could not protect executable ownership.' }
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
}

function Write-Stage([string]$Message) {
    Write-Output "[SQL Context Pack] $Message"
}

function Remove-StaleTransactions {
    $managedRoot = (Resolve-Path -LiteralPath $programDataRoot).Path
    $candidates = @(Get-ChildItem -LiteralPath $managedRoot -Force -Directory | Where-Object {
        $_.Name -match '^\.(stage|backup)-[0-9a-f]{32}$'
    })
    foreach ($candidate in $candidates) {
        $resolved = (Resolve-Path -LiteralPath $candidate.FullName).Path
        if ([IO.Path]::GetDirectoryName($resolved) -ne $managedRoot) {
            throw "Refusing stale transaction cleanup outside the managed root: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    if ($candidates.Count -gt 0) {
        Write-Stage "Removed $($candidates.Count) stale transaction directories from interrupted installs."
    }
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LocalHealth([object]$Metadata) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    try {
        $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', [string]$Metadata.agent_token)
        $response = $client.GetAsync("http://127.0.0.1:$Port/api/v1/health").GetAwaiter().GetResult()
        try {
            if (-not $response.IsSuccessStatusCode) { throw 'Authenticated local service health failed.' }
            return ($response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json)
        } finally { $response.Dispose() }
    } finally { $client.Dispose(); $handler.Dispose() }
}

function Test-InstalledHealth([string]$ExpectedVersion) {
    $currentService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if (-not $currentService -or $currentService.Status -ne 'Running' -or -not (Test-Path -LiteralPath $metadataPath)) { return $false }
    try {
        $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
        $health = Get-LocalHealth $metadata
        return $health.status -eq 'ok' -and $health.version -eq $ExpectedVersion -and (Get-CimInstance Win32_Service -Filter "Name='$serviceName'").StartName -eq $serviceAccount -and (Test-Path -LiteralPath $servicePython)
    } catch { return $false }
}

function Test-OwnerNoop {
    try {
        if (-not (Test-Path -LiteralPath $installState) -or -not (Test-Path -LiteralPath $appRoot)) { return $false }
        $profileState = (& $PythonExecutable -m sqlctx.cli profile list) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { return $false }
        $extraMap = @{ sqlserver = 'sqlserver'; postgres = 'postgres'; mysql = 'mysql'; mariadb = 'mariadb'; oracle = 'oracle' }
        $extras = @($profileState.profiles | ForEach-Object { $extraMap[[string]$_.engine] } | Where-Object { $_ } | Sort-Object -Unique)
        $fingerprintArgs = @((Join-Path $SourceRoot 'scripts\install_fingerprint.py'), '--source-root', $SourceRoot, '--installed-package', (Join-Path $appRoot 'sqlctx'))
        foreach ($extra in $extras) { $fingerprintArgs += @('--extra', $extra) }
        $current = (& $PythonExecutable @fingerprintArgs) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { return $false }
        $previous = Get-Content -Raw -LiteralPath $installState | ConvertFrom-Json
        $expectedVersion = (Get-Content -Raw -LiteralPath (Join-Path $SourceRoot '.codex-plugin\plugin.json') | ConvertFrom-Json).version
        return (
            $previous.app_fingerprint -eq $current.app_fingerprint -and
            $previous.dependency_fingerprint -eq $current.dependency_fingerprint -and
            $previous.service_host_fingerprint -eq $current.service_host_fingerprint -and
            $current.installed_package_fingerprint -eq $current.package_fingerprint -and
            (Test-InstalledHealth $expectedVersion)
        )
    } catch { return $false }
}

if ($Operation -in @('install', 'update') -and -not (Test-Administrator) -and (Test-OwnerNoop)) {
    Write-Stage 'Cache hit: application, dependencies, service host, installed inventory, and authenticated health are unchanged; UAC and service restart skipped.'
    exit 0
}

if ($Operation -in @('install', 'update', 'remove') -and -not (Test-Administrator)) {
    Write-Stage 'Administrator access is required to register the Windows Service and protect the ProgramData application tree.'
    Write-Stage 'No firewall rule is created; the service binds only to 127.0.0.1.'
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-Operation', $Operation, '-SourceRoot', $SourceRoot,
        '-PythonExecutable', $PythonExecutable, '-Port', $Port, '-Elevated'
    )
    if ($PackageArtifact) { $arguments += @('-PackageArtifact', $PackageArtifact) }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}

if ($Operation -eq 'status') {
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    [PSCustomObject]@{
        installed = $null -ne $service
        status = if ($service) { [string]$service.Status } else { 'NotInstalled' }
        app_root = $appRoot
        loopback_url = "http://127.0.0.1:$Port"
    } | ConvertTo-Json -Compress
    exit 0
}

if ($Operation -eq 'remove') {
    Assert-ManagedTarget $appRoot
    Assert-ManagedTarget $interpreterRoot
    Write-Stage 'Stopping and unregistering SQLContextPack. Profiles, credentials, and retained runtime data are preserved.'
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        Stop-Service -Name $serviceName -Force
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    if ($service) {
        & sc.exe delete $serviceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Windows Service removal failed.' }
    }
    Remove-Item -LiteralPath $appRoot, $interpreterRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $hostScript, $serviceConfig, $installState -Force -ErrorAction SilentlyContinue
    Remove-StaleTransactions
    Write-Stage 'Windows Service registration and replaceable application/service files were removed; config and runtime data were preserved.'
    exit 0
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$resolvedPython = (Resolve-Path -LiteralPath $PythonExecutable).Path
$stageRoot = Join-Path ([IO.Path]::GetTempPath()) ('sqlctx-service-stage-' + [Guid]::NewGuid().ToString('N'))
$stageApp = Join-Path $stageRoot 'app'
$stagePython = Join-Path $stageRoot 'python'
$backupPython = Join-Path $programDataRoot ('.backup-' + [Guid]::NewGuid().ToString('N'))
$pythonSwapped = $false
$previousService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
foreach ($target in @($appRoot, $interpreterRoot, $configRoot, $runtimeRoot, $stageRoot, $stageApp, $stagePython, $backupPython)) { Assert-ManagedTarget $target }
$backupApp = Join-Path $programDataRoot ('.backup-' + [Guid]::NewGuid().ToString('N'))
Assert-ManagedTarget $backupApp
$hadExistingApp = Test-Path -LiteralPath $appRoot
$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
$serviceWasRunning = $service -and $service.Status -ne 'Stopped'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$createdService = $false
$hostBackup = "$hostScript.backup"
$configBackup = "$serviceConfig.backup"
$metadataBackup = Join-Path $stageRoot 'connection-metadata.backup.json'
$ownerControlBackup = Join-Path $stageRoot 'owner-control.backup.json'
$appSwapped = $false

try {
    Write-Stage 'Reading safe configured profile engines so every required database driver is staged.'
    $profileJson = & $resolvedPython -m sqlctx.cli profile list
    if ($LASTEXITCODE -ne 0) { throw 'Could not read configured profile descriptors.' }
    $profileState = $profileJson | ConvertFrom-Json
    $extraMap = @{
        sqlserver = 'sqlserver'
        postgres = 'postgres'
        mysql = 'mysql'
        mariadb = 'mariadb'
        oracle = 'oracle'
    }
    $extras = @($profileState.profiles | ForEach-Object { $extraMap[[string]$_.engine] } | Where-Object { $_ } | Sort-Object -Unique)
    $fingerprintArgs = @((Join-Path $resolvedSource 'scripts\install_fingerprint.py'), '--source-root', $resolvedSource)
    foreach ($extra in $extras) { $fingerprintArgs += @('--extra', $extra) }
    $installedPackage = Join-Path $appRoot 'sqlctx'
    if (Test-Path -LiteralPath $installedPackage) { $fingerprintArgs += @('--installed-package', $installedPackage) }
    $fingerprint = (& $resolvedPython @fingerprintArgs) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Could not compute service installation fingerprints.' }
    $previousState = if (Test-Path -LiteralPath $installState) { Get-Content -Raw -LiteralPath $installState | ConvertFrom-Json } else { $null }
    $expectedVersion = (Get-Content -Raw -LiteralPath (Join-Path $resolvedSource '.codex-plugin\plugin.json') | ConvertFrom-Json).version
    $healthyBefore = Test-InstalledHealth $expectedVersion
    $appChanged = -not $previousState -or $previousState.app_fingerprint -ne $fingerprint.app_fingerprint
    if ($fingerprint.installed_package_fingerprint -ne $fingerprint.package_fingerprint) { $appChanged = $true }
    $dependenciesChanged = -not $previousState -or $previousState.dependency_fingerprint -ne $fingerprint.dependency_fingerprint
    $hostChanged = -not $previousState -or $previousState.service_host_fingerprint -ne $fingerprint.service_host_fingerprint
    if (-not $healthyBefore) { $appChanged = $true }
    $replaceApp = $appChanged -or $dependenciesChanged -or -not (Test-Path -LiteralPath $appRoot)
    if (-not $replaceApp -and -not $hostChanged -and $healthyBefore) {
        Write-Stage 'Cache hit: application, dependencies, service host, and authenticated health are unchanged; service restart skipped.'
        Remove-StaleTransactions
        exit 0
    }
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    & $resolvedPython -B (Join-Path $resolvedSource 'scripts\stage_service_python.py') $stagePython
    if ($LASTEXITCODE -ne 0) { throw 'Protected interpreter staging failed.' }
    if ($replaceApp) {
        New-Item -ItemType Directory -Path $stageApp -Force | Out-Null
        $baseTarget = if ($PackageArtifact) { $PackageArtifact } else { $resolvedSource }
        if ($dependenciesChanged -or -not (Test-Path -LiteralPath $appRoot)) {
            $installTarget = $baseTarget
            if ($extras.Count -gt 0) { $installTarget = "$baseTarget[$($extras -join ',')]" }
            Write-Stage "Dependency fingerprint changed; staging the complete pinned runtime under $stageApp."
            & $resolvedPython -m pip install --target $stageApp $installTarget
        } else {
            Write-Stage 'Dependency cache hit; copying the installed runtime and replacing only application files.'
            Copy-Item -Path (Join-Path $appRoot '*') -Destination $stageApp -Recurse -Force
            Remove-Item -LiteralPath (Join-Path $stageApp 'sqlctx') -Recurse -Force -ErrorAction SilentlyContinue
            Get-ChildItem -LiteralPath $stageApp -Directory -Filter 'sql_context_pack-*.dist-info' | Remove-Item -Recurse -Force
            & $resolvedPython -m pip install --target $stageApp --no-deps $baseTarget
        }
        if ($LASTEXITCODE -ne 0) { throw 'Staged package installation failed.' }
    }

    Write-Stage 'Preparing protected service configuration and preserving owner data outside the application tree.'
    New-Item -ItemType Directory -Path $configRoot, $runtimeRoot -Force | Out-Null
    $ownerConfig = Join-Path $env:APPDATA 'sql-context-pack'
    $ownerRuntime = Join-Path $env:LOCALAPPDATA 'sql-context-pack'
    if (-not (Get-ChildItem -LiteralPath $configRoot -Force -ErrorAction SilentlyContinue)) {
        if (Test-Path -LiteralPath $ownerConfig) {
            Copy-Item -Path (Join-Path $ownerConfig '*') -Destination $configRoot -Recurse -Force
        }
    }
    if (-not (Get-ChildItem -LiteralPath $runtimeRoot -Force -ErrorAction SilentlyContinue)) {
        if (Test-Path -LiteralPath $ownerRuntime) {
            Copy-Item -Path (Join-Path $ownerRuntime '*') -Destination $runtimeRoot -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $metadataPath) { Copy-Item -LiteralPath $metadataPath -Destination $metadataBackup -Force }
    if (Test-Path -LiteralPath $ownerControlPath) { Copy-Item -LiteralPath $ownerControlPath -Destination $ownerControlBackup -Force }
    if (Test-Path -LiteralPath $hostScript) { Copy-Item -LiteralPath $hostScript -Destination $hostBackup -Force }
    if (Test-Path -LiteralPath $serviceConfig) { Copy-Item -LiteralPath $serviceConfig -Destination $configBackup -Force }
    if ($hostChanged -or -not (Test-Path -LiteralPath $hostScript)) {
        Copy-Item -LiteralPath (Join-Path $resolvedSource 'scripts\sqlctx_windows_service.py') -Destination $hostScript -Force
    }
    @{
        schema_version = 1
        python_executable = $servicePython
        service_account = $serviceAccount
        app_root = $appRoot
        config_root = $configRoot
        runtime_root = $runtimeRoot
        owner_account = $owner
        port = $Port
    } | ConvertTo-Json | Set-Content -LiteralPath $serviceConfig -Encoding UTF8

    # Exact DACLs are installed after the virtual service identity is registered.

    if ($serviceWasRunning) {
        Write-Stage 'Stopping the existing service only after staging succeeded.'
        Stop-Service -Name $serviceName -Force
        (Get-Service -Name $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    Write-Stage 'Rotating volatile service authentication metadata so stale foreground servers cannot satisfy health checks.'
    Remove-Item -LiteralPath $metadataPath, $ownerControlPath -Force -ErrorAction SilentlyContinue
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $legacy = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($legacy -and [string]$legacy.CommandLine -match 'sqlctx\.server\.http\.app') {
            Write-Stage "Stopping legacy SQL Context Pack foreground process $($listener.OwningProcess) before service startup."
            Stop-Process -Id $listener.OwningProcess -Force
        } else {
            throw "PORT_IN_USE: Port $Port is owned by a non-SQLContextPack process."
        }
    }
    if ($replaceApp) {
        if ($hadExistingApp) { Move-Item -LiteralPath $appRoot -Destination $backupApp }
        Move-Item -LiteralPath $stageApp -Destination $appRoot
        $appSwapped = $true
    }

    if (Test-Path -LiteralPath $interpreterRoot) { Move-Item -LiteralPath $interpreterRoot -Destination $backupPython }
    Move-Item -LiteralPath $stagePython -Destination $interpreterRoot
    $pythonSwapped = $true
    $binPath = '"' + $servicePython + '" -I -B "' + $hostScript + '"'
    if (-not $service) {
        Write-Stage 'Registering automatic least-privilege virtual-account Windows Service; loopback-only network access is used.'
        & sc.exe create $serviceName binPath= $binPath start= auto obj= $serviceAccount DisplayName= 'SQL Context Pack' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Windows Service registration failed.' }
        $createdService = $true
        & sc.exe description $serviceName 'Owner-approved loopback HTTP/MCP database context service.' | Out-Null
        & sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/15000/none/0 | Out-Null
    } else {
        & sc.exe config $serviceName binPath= $binPath start= auto obj= $serviceAccount | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Windows Service reconfiguration failed.' }
    }

    & sc.exe sidtype $serviceName unrestricted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure the service SID.' }
    Set-ProtectedAcl $programDataRoot 'ReadAndExecute' 'ReadAndExecute'
    Set-ProtectedAcl $appRoot 'ReadAndExecute' 'ReadAndExecute' -Recurse
    Set-ProtectedAcl $interpreterRoot 'ReadAndExecute' 'ReadAndExecute' -Recurse
    Set-ProtectedAcl $hostScript 'ReadAndExecute' 'ReadAndExecute'
    Set-ProtectedAcl $serviceConfig 'Read' 'Read'
    Set-ProtectedAcl $configRoot 'Modify' 'Modify' -Recurse
    Set-ProtectedAcl $runtimeRoot 'Modify' 'Modify' -Recurse
    Write-Stage 'Starting service and waiting for authenticated health metadata.'
    Start-Service -Name $serviceName
    (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-Path -LiteralPath $metadataPath) {
            $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
            try {
                        $health = Get-LocalHealth $metadata
                $currentService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
                if ($currentService.Status -eq 'Running' -and $health.status -eq 'ok' -and $health.version -eq $expectedVersion) {
                    $ready = $true
                    break
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Write-Stage "Protected child diagnostics are available at $runtimeRoot\service-child.log."
        throw 'Service health verification failed.'
    }

    @{
        schema_version = 1
        app_fingerprint = $fingerprint.app_fingerprint
        package_fingerprint = $fingerprint.package_fingerprint
        dependency_fingerprint = $fingerprint.dependency_fingerprint
        service_host_fingerprint = $fingerprint.service_host_fingerprint
        python = $fingerprint.python
        extras = $fingerprint.extras
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $installState -Encoding UTF8

    if (Test-Path -LiteralPath $backupApp) { Remove-Item -LiteralPath $backupApp -Recurse -Force }
    if (Test-Path -LiteralPath $backupPython) { Remove-Item -LiteralPath $backupPython -Recurse -Force }
    Set-ProtectedAcl $installState 'Read' 'Read'
    Remove-StaleTransactions
    Remove-Item -LiteralPath $hostBackup, $configBackup -Force -ErrorAction SilentlyContinue
    Write-Stage 'Windows Service is installed, running, and health-verified. No firewall access was requested.'
} catch {
    Write-Stage "Operation failed: $($_.Exception.Message)"
    $current = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($current -and $current.Status -ne 'Stopped') { Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue }
    if ($appSwapped -and (Test-Path -LiteralPath $appRoot)) { Remove-Item -LiteralPath $appRoot -Recurse -Force }
    if (Test-Path -LiteralPath $backupApp) { Move-Item -LiteralPath $backupApp -Destination $appRoot }
    if (Test-Path -LiteralPath $hostBackup) { Move-Item -LiteralPath $hostBackup -Destination $hostScript -Force }
    if (Test-Path -LiteralPath $configBackup) { Move-Item -LiteralPath $configBackup -Destination $serviceConfig -Force }
    if (Test-Path -LiteralPath $metadataBackup) { Copy-Item -LiteralPath $metadataBackup -Destination $metadataPath -Force }
    if (Test-Path -LiteralPath $ownerControlBackup) { Copy-Item -LiteralPath $ownerControlBackup -Destination $ownerControlPath -Force }
    if ($pythonSwapped -and (Test-Path -LiteralPath $interpreterRoot)) { Remove-Item -LiteralPath $interpreterRoot -Recurse -Force }
    if (Test-Path -LiteralPath $backupPython) { Move-Item -LiteralPath $backupPython -Destination $interpreterRoot }
    if ($previousService) { & sc.exe config $serviceName binPath= $previousService.PathName obj= $previousService.StartName | Out-Null }
    if ($createdService) { & sc.exe delete $serviceName | Out-Null }
    if ($serviceWasRunning -and (Test-Path -LiteralPath $appRoot)) { Start-Service -Name $serviceName -ErrorAction SilentlyContinue }
    throw
} finally {
    if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
}
