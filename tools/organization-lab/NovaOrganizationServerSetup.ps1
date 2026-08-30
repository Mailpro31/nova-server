[CmdletBinding()]
param(
    [ValidateSet('install','analyze','resume','status','repair','update','reconfigure','selftest')]
    [string]$Action = 'install',
    [switch]$Headless,
    [string]$OrganizationName = 'Nova Lab',
    [ValidateSet('campus','business')][string]$OrganizationType = 'campus',
    [string]$ManifestUrl = 'https://releases.novaspeak.app/server/quick-lab/manifest.json',
    [string]$ReleasePublicKeyXml = '__NOVA_RELEASE_PUBLIC_KEY_XML__',
    [switch]$LibraryMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Version = '0.4.0'
$script:Root = Join-Path $env:ProgramData 'Nova\OrganizationServer'
$script:StatePath = Join-Path $script:Root 'setup-state.json'
$script:LogPath = Join-Path $script:Root 'logs\setup.log'
$script:Generated = Join-Path $script:Root 'generated'
$script:Secrets = Join-Path $script:Root 'secrets'
$script:ManifestCache = Join-Path $script:Root 'state\last-known-good-manifest.json'
$script:BundledProfiles = Join-Path $PSScriptRoot 'profiles.manifest.json'
$script:AllowedManifestOrigin = 'https://releases.novaspeak.app'

function Initialize-Directories {
    foreach ($path in @($script:Root, (Split-Path $script:LogPath), $script:Generated,
            $script:Secrets, (Split-Path $script:ManifestCache))) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}

function Write-SafeLog([string]$Message) {
    # Logging must never abort the operation it is describing. This is called
    # from catch blocks, so a failure to write would otherwise replace the real
    # error with an unrelated one.
    try {
        Initialize-Directories
        $clean = $Message -replace '(?i)(password|token|secret|credential)\s*[=:]\s*\S+', '$1=[REDACTED]'
        Add-Content -LiteralPath $script:LogPath -Value ("{0:o} {1}" -f (Get-Date), $clean)
    } catch {
        # deliberately swallowed: diagnostics are best effort
    }
}

function Run-Captured([string]$File, [string[]]$Arguments) {
    try {
        $text = & $File @Arguments 2>&1 | Out-String
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $text.Trim() }
    } catch {
        return [pscustomobject]@{ ExitCode = 127; Output = $_.Exception.Message }
    }
}

function Get-Hardware {
    $os = Get-CimInstance Win32_OperatingSystem
    $computer = Get-CimInstance Win32_ComputerSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    $nvidia = Run-Captured 'nvidia-smi.exe' @('--query-gpu=name,memory.total,driver_version,compute_cap','--format=csv,noheader,nounits')
    $gpuName = $null; $vram = 0; $driver = $null; $cuda = $null
    if ($nvidia.ExitCode -eq 0 -and $nvidia.Output) {
        $parts = @($nvidia.Output.Split(',') | ForEach-Object { $_.Trim() })
        if ($parts.Count -ge 4) {
            $gpuName = $parts[0]; [int]::TryParse($parts[1], [ref]$vram) | Out-Null
            $driver = $parts[2]; $cuda = $parts[3]
        }
    }
    $wsl = Run-Captured 'wsl.exe' @('--status')
    $wslVersion = Run-Captured 'wsl.exe' @('--version')
    $docker = Run-Captured 'docker.exe' @('version','--format','{{.Server.Version}}')
    $compose = Run-Captured 'docker.exe' @('compose','version','--short')
    $virtualization = [bool]$computer.HypervisorPresent
    if (-not $virtualization -and $cpu.PSObject.Properties.Name -contains 'VirtualizationFirmwareEnabled') {
        $virtualization = [bool]$cpu.VirtualizationFirmwareEnabled
    }
    [pscustomobject]@{
        Windows = $os.Caption; WindowsBuild = $os.BuildNumber; Architecture = $os.OSArchitecture
        Cpu = $cpu.Name.Trim(); Cores = [int]$cpu.NumberOfCores
        RamGiB = [math]::Round([double]$computer.TotalPhysicalMemory / 1GB, 1)
        DiskFreeGiB = [math]::Round([double]$disk.FreeSpace / 1GB, 1)
        Virtualization = $virtualization; NvidiaGpu = $gpuName; VramMiB = $vram
        NvidiaDriver = $driver; CudaCapability = $cuda
        WslAvailable = ($wsl.ExitCode -eq 0); WslVersion = $wslVersion.Output
        DockerAvailable = ($docker.ExitCode -eq 0); DockerVersion = $docker.Output
        DockerInstalled = (Test-DockerInstalled); DockerDesktopPath = (Get-DockerDesktopPath)
        ComposeAvailable = ($compose.ExitCode -eq 0); ComposeVersion = $compose.Output
    }
}

function Read-BundledProfiles {
    if (-not (Test-Path -LiteralPath $script:BundledProfiles)) { throw 'BUNDLED_PROFILE_MANIFEST_MISSING' }
    $manifest = Get-Content -Raw -LiteralPath $script:BundledProfiles | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or -not $manifest.profiles) { throw 'PROFILE_MANIFEST_INVALID' }
    return $manifest
}

function Select-HardwareProfile($Hardware, $Manifest) {
    if (-not $Hardware.NvidiaGpu) {
        return @($Manifest.profiles | Where-Object { $_.id -eq 'LAB_CPU' })[0]
    }
    $matches = @($Manifest.profiles | Where-Object {
        $_.gpu -eq $true -and $Hardware.VramMiB -ge $_.minimum_vram_mib -and
        $Hardware.VramMiB -le $_.maximum_vram_mib
    })
    if ($matches.Count -ne 1) { throw 'NO_UNAMBIGUOUS_HARDWARE_PROFILE' }
    return $matches[0]
}

function Assert-ReleasePublicKeyConfigured {
    # Deliberately a positive test on the shape of the key rather than a
    # comparison against the build placeholder: build-installer.ps1 substitutes
    # every occurrence of that placeholder, so a comparison here would be
    # rewritten into a comparison of the key against itself and would throw
    # precisely when the key is correct.
    if ($ReleasePublicKeyXml -notmatch '<RSAKeyValue>\s*<Modulus>[A-Za-z0-9+/=]{256,}</Modulus>') {
        throw 'RELEASE_PUBLIC_KEY_NOT_CONFIGURED'
    }
}

function Test-ReleaseManifestSignature([byte[]]$Bytes, [string]$SignatureBase64) {
    Assert-ReleasePublicKeyConfigured
    $rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    try {
        $rsa.PersistKeyInCsp = $false
        $rsa.FromXmlString($ReleasePublicKeyXml)
        $signature = [Convert]::FromBase64String($SignatureBase64.Trim())
        return $rsa.VerifyData($Bytes, [System.Security.Cryptography.CryptoConfig]::MapNameToOID('SHA256'), $signature)
    } finally { $rsa.Dispose() }
}

function Get-VerifiedReleaseManifest {
    $uri = [Uri]$ManifestUrl
    if ($uri.Scheme -ne 'https' -or $uri.GetLeftPart([UriPartial]::Authority) -ne $script:AllowedManifestOrigin) {
        throw 'MANIFEST_ORIGIN_NOT_PINNED'
    }
    Initialize-Directories
    $tempManifest = Join-Path $env:TEMP ("nova-manifest-{0}.json" -f [Guid]::NewGuid())
    $tempSignature = "$tempManifest.sig"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $uri.AbsoluteUri -OutFile $tempManifest
        Invoke-WebRequest -UseBasicParsing -Uri ($uri.AbsoluteUri + '.sig') -OutFile $tempSignature
        [byte[]]$bytes = [IO.File]::ReadAllBytes($tempManifest)
        $signature = [IO.File]::ReadAllText($tempSignature)
        if (-not (Test-ReleaseManifestSignature $bytes $signature)) { throw 'MANIFEST_SIGNATURE_INVALID' }
        $manifest = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
        if ($manifest.schema_version -ne 1 -or -not $manifest.release_id -or -not $manifest.artifacts -or -not $manifest.profiles) {
            throw 'RELEASE_MANIFEST_INVALID'
        }
        [IO.File]::WriteAllBytes($script:ManifestCache, $bytes)
        return $manifest
    } finally {
        Remove-Item -LiteralPath $tempManifest,$tempSignature -Force -ErrorAction SilentlyContinue
    }
}

function Get-OrCreateSecret([string]$Name) {
    Initialize-Directories
    $path = Join-Path $script:Secrets $Name
    if (-not (Test-Path -LiteralPath $path)) {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        [IO.File]::WriteAllText($path, [Convert]::ToBase64String($bytes))
        & icacls.exe $path '/inheritance:r' "/grant:r:$env:USERNAME:(R,W)" | Out-Null
    }
    return $path
}

function Save-State([string]$Step, [string]$ProfileId, [string]$ReleaseId = '') {
    Initialize-Directories
    [pscustomobject]@{
        schema_version = 1; installer_version = $script:Version; step = $Step
        profile_id = $ProfileId; release_id = $ReleaseId; updated_at = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $script:StatePath -Encoding UTF8
}

function Register-Resume {
    $exe = Join-Path $script:Root 'NovaOrganizationServerSetup.exe'
    if (-not (Test-Path -LiteralPath $exe)) { $exe = $PSCommandPath }
    $command = '"{0}" resume' -f $exe
    New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'NovaOrganizationServerSetup' -Value $command -PropertyType String -Force | Out-Null
}

function Get-DockerDesktopPath {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\Docker Desktop.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Test-DockerInstalled {
    # Installed is not the same thing as running. Any one of these is proof that
    # Docker Desktop is already on the machine, so nothing should be installed.
    if (Get-DockerDesktopPath) { return $true }
    if (Get-Service -Name 'com.docker.service' -ErrorAction SilentlyContinue) { return $true }
    if (Get-Command 'docker.exe' -ErrorAction SilentlyContinue) { return $true }
    return $false
}

function Get-DockerServerVersion {
    $probe = Run-Captured 'docker.exe' @('version','--format','{{.Server.Version}}')
    if ($probe.ExitCode -eq 0 -and $probe.Output.Trim()) { return $probe.Output.Trim() }
    return $null
}

function Start-DockerDesktop {
    $service = Get-Service -Name 'com.docker.service' -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Running') {
        try { Start-Service -Name 'com.docker.service' -ErrorAction Stop }
        catch { Write-SafeLog ("docker_service_start_failed {0}" -f $_.Exception.Message) }
    }
    $desktop = Get-DockerDesktopPath
    if (-not $desktop) { return $false }
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        try { Start-Process -FilePath $desktop | Out-Null }
        catch { Write-SafeLog ("docker_desktop_start_failed {0}" -f $_.Exception.Message); return $false }
    }
    return $true
}

function Wait-DockerDaemon([int]$TimeoutMinutes) {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $version = Get-DockerServerVersion
        if ($version) { return $version }
        if ((Get-Date) -gt $deadline) { return $null }
        Start-Sleep -Seconds 5
    }
}

function Ensure-DockerReady([string]$ProfileId) {
    # 1. The daemon already answers. Docker is ready by definition; install nothing.
    $version = Get-DockerServerVersion
    if ($version) { Write-SafeLog "docker_ready_existing version=$version"; return "ready:$version" }

    # 2. Installed but not answering: start it and wait, never reinstall.
    if (Test-DockerInstalled) {
        Write-SafeLog 'docker_installed_daemon_down starting'
        Start-DockerDesktop | Out-Null
        $version = Wait-DockerDaemon 5
        if ($version) { Write-SafeLog "docker_started version=$version"; return "started:$version" }
        Register-Resume
        throw 'DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING'
    }

    # 3. Genuinely absent: install it.
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw 'WINGET_REQUIRED' }
    Save-State 'docker-install' $ProfileId
    $result = Run-Captured 'winget.exe' @('install','--id','Docker.DockerDesktop','--exact','--silent','--accept-package-agreements','--accept-source-agreements')
    if ($result.ExitCode -ne 0) {
        # winget reports "already installed" and "no applicable upgrade" as
        # failures. Those mean Docker is present, so re-detect before deciding
        # rather than aborting on the exit code alone.
        Write-SafeLog ("winget_docker_exit={0}" -f $result.ExitCode)
        if (-not (Test-DockerInstalled)) {
            throw "DOCKER_INSTALL_FAILED: winget exit $($result.ExitCode). $($result.Output)"
        }
        Write-SafeLog 'winget_reported_failure_but_docker_present continuing'
    }
    Start-DockerDesktop | Out-Null
    $version = Wait-DockerDaemon 5
    if ($version) { Write-SafeLog "docker_installed version=$version"; return "installed:$version" }
    Register-Resume
    throw 'DOCKER_INSTALLED_STARTUP_PENDING'
}

function Ensure-Prerequisites($Hardware, [string]$ProfileId) {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'WINDOWS_X64_REQUIRED' }
    if (-not $Hardware.Virtualization) { throw 'VIRTUALIZATION_DISABLED_IN_FIRMWARE' }
    if (-not $Hardware.WslAvailable) {
        Save-State 'wsl-reboot-required' $ProfileId
        $result = Run-Captured 'wsl.exe' @('--install','--no-distribution')
        if ($result.ExitCode -ne 0) { throw "WSL_INSTALL_FAILED: $($result.Output)" }
        Register-Resume
        return 'reboot-required'
    }
    Ensure-DockerReady $ProfileId | Out-Null

    # Re-probe compose now that the daemon is up: the value captured during
    # hardware detection predates starting or installing Docker.
    $compose = Run-Captured 'docker.exe' @('compose','version','--short')
    if ($compose.ExitCode -ne 0) { throw 'DOCKER_COMPOSE_UNAVAILABLE' }
    return 'ready'
}

function Receive-Artifact($Artifact) {
    $uri = [Uri]$Artifact.url
    if ($uri.Scheme -ne 'https' -or $uri.GetLeftPart([UriPartial]::Authority) -ne $script:AllowedManifestOrigin) {
        throw "ARTIFACT_ORIGIN_NOT_PINNED: $($Artifact.id)"
    }
    if ($Artifact.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw "ARTIFACT_HASH_INVALID: $($Artifact.id)" }
    $target = Join-Path $script:Root ("artifacts\{0}" -f $Artifact.file_name)
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    if (-not (Test-Path $target) -or (Get-FileHash -Algorithm SHA256 $target).Hash -ne $Artifact.sha256) {
        $partial = "$target.partial"
        Invoke-WebRequest -UseBasicParsing -Uri $uri.AbsoluteUri -OutFile $partial
        if ((Get-FileHash -Algorithm SHA256 $partial).Hash -ne $Artifact.sha256) {
            Remove-Item $partial -Force; throw "ARTIFACT_HASH_MISMATCH: $($Artifact.id)"
        }
        Move-Item -Force $partial $target
    }
    return $target
}

function Import-OciArtifact($Artifact) {
    if ($Artifact.image -notmatch '^([a-z0-9.-]+)(/[a-z0-9._/-]+)+@sha256:[a-f0-9]{64}$') {
        throw "OCI_REFERENCE_NOT_IMMUTABLE: $($Artifact.id)"
    }
    if ($Artifact.image.StartsWith('ghcr.io/') -eq $false -and
        $Artifact.image.StartsWith('docker.io/') -eq $false) {
        throw "OCI_REGISTRY_NOT_APPROVED: $($Artifact.id)"
    }
    $result = Run-Captured 'docker.exe' @('pull',$Artifact.image)
    if ($result.ExitCode -ne 0) { throw "OCI_PULL_FAILED: $($Artifact.id)" }

    # {{json .RepoDigests}} rather than {{join ...}}: a backtick inside a Go
    # template opens a raw quoted string, so the previous format failed to parse
    # and docker exited non-zero, which this function then misreported as a
    # digest mismatch.
    $digests = Run-Captured 'docker.exe' @('image','inspect',$Artifact.image,'--format','{{json .RepoDigests}}')
    if ($digests.ExitCode -ne 0) { throw "OCI_INSPECT_FAILED: $($Artifact.id)" }
    $identity = Run-Captured 'docker.exe' @('image','inspect',$Artifact.image,'--format','{{.Id}}')
    if ($identity.ExitCode -ne 0) { throw "OCI_INSPECT_FAILED: $($Artifact.id)" }

    # The manifest pins the linux/amd64 manifest digest, which is what was
    # pulled, so the reference and what docker reports are the same value on
    # both the classic and the containerd image store.
    $wantedDigest = $Artifact.image.Substring($Artifact.image.IndexOf('@sha256:') + 1)
    $reported = "{0}`n{1}" -f $digests.Output, $identity.Output
    if ($reported -notmatch [regex]::Escape($wantedDigest)) {
        throw "OCI_DIGEST_VERIFICATION_FAILED: $($Artifact.id)"
    }

    # The pinned digest must address one platform image, never an index: an
    # index would leave the platform choice to the client and put bytes on disk
    # that the signed manifest never named.
    $platform = Run-Captured 'docker.exe' @('image','inspect',$Artifact.image,'--format','{{.Os}}/{{.Architecture}}')
    if ($platform.ExitCode -ne 0 -or $platform.Output.Trim() -ne 'linux/amd64') {
        throw "OCI_PLATFORM_UNEXPECTED: $($Artifact.id)"
    }
    if ($Artifact.local_image -notmatch '^nova-local/[a-z0-9._-]+:[a-z0-9._-]+$') {
        throw "OCI_LOCAL_NAME_INVALID: $($Artifact.id)"
    }
    $tag = Run-Captured 'docker.exe' @('tag',$Artifact.image,$Artifact.local_image)
    if ($tag.ExitCode -ne 0) { throw "OCI_TAG_FAILED: $($Artifact.id)" }
}

function Import-ReleaseImages($Manifest, $Profile) {
    $ids = @('nova-server','postgres','redis',$Profile.speech.artifact_id,$Profile.language.artifact_id)
    foreach ($id in $ids) {
        $artifact = @($Manifest.artifacts | Where-Object { $_.id -eq $id })[0]
        if (-not $artifact) { throw "REQUIRED_ARTIFACT_MISSING: $id" }
        if ($artifact.kind -eq 'oci_image') {
            Import-OciArtifact $artifact
        } elseif ($artifact.kind -eq 'file') {
            $path = Receive-Artifact $artifact
            $result = Run-Captured 'docker.exe' @('load','--input',$path)
            if ($result.ExitCode -ne 0) { throw "ARTIFACT_LOAD_FAILED: $id" }
        } else { throw "ARTIFACT_KIND_UNSUPPORTED: $id" }
    }
}

function Resolve-LlmRuntimeMode($Profile) {
    # Ask the GPU, do not assume. The probe ships inside the pinned language
    # image and runs on the client's real card before anything is configured.
    $image = $Profile.language.image
    $probe = Run-Captured 'docker.exe' @(
        'run','--rm','--gpus','all','--network','none',
        '--entrypoint','python3',$image,'/opt/nova/nova-llm-compat.py')

    $json = $null
    foreach ($line in ($probe.Output -split "`n")) {
        $candidate = $line.Trim()
        if ($candidate.StartsWith('{') -and $candidate.EndsWith('}')) {
            try { $json = $candidate | ConvertFrom-Json } catch { }
        }
    }
    if (-not $json) { throw 'LLM_COMPAT_PROBE_FAILED' }

    Write-SafeLog ("llm_compat gpu={0} cc={1} vram={2} uva_default={3} mode={4}" -f `
        $json.gpu, $json.compute_capability, $json.vram_mib, $json.uva_available_by_default, $json.mode)

    if ($probe.ExitCode -ne 0 -or -not $json.mode) {
        # Fail closed. No remote or cloud runtime is ever substituted.
        if ($json.error -eq 'NO_CUDA_GPU') { throw 'LLM_GPU_NOT_VISIBLE_TO_DOCKER' }
        if ($json.error -eq 'COMPUTE_CAPABILITY_TOO_LOW') { throw "LLM_GPU_TOO_OLD: $($json.compute_capability)" }
        throw 'LLM_RUNTIME_INCOMPATIBLE'
    }

    switch ($json.mode) {
        # Preferred: the runner that needs no UVA at all, so the WSL2 pinned
        # memory restriction cannot apply.
        'no_uva'     { return @{ Mode = 'no_uva';     Environment = @{ 'VLLM_USE_V2_MODEL_RUNNER' = '0' } } }
        # Only reachable when the probe actually exercised pinned memory and the
        # accelerator view UvaBuffer relies on.
        'uva_pinned' { return @{ Mode = 'uva_pinned'; Environment = @{ 'VLLM_WSL2_ENABLE_PIN_MEMORY' = '1' } } }
        default      { throw "LLM_RUNTIME_MODE_UNKNOWN: $($json.mode)" }
    }
}

function Write-LabCompose($Manifest, $Profile, $Runtime) {
    Initialize-Directories
    $dbSecret = Get-OrCreateSecret 'postgres-password.txt'
    $orgId = 'org-' + ([Guid]::NewGuid().ToString('N'))
    if (-not $Runtime -or -not $Runtime.Mode) { throw 'LLM_RUNTIME_NOT_RESOLVED' }
    $llmEnvironment = (($Runtime.Environment.GetEnumerator() | Sort-Object Name | ForEach-Object {
        '      {0}: "{1}"' -f $_.Key, $_.Value
    }) -join "`n")
    $deploymentId = 'dpl-' + ([Guid]::NewGuid().ToString('N'))
    $compose = @"
name: nova-organization-lab
services:
  postgres:
    image: $($Manifest.images.postgres)
    environment:
      POSTGRES_DB: nova
      POSTGRES_USER: nova
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    secrets: [postgres_password]
    volumes: [nova-postgres:/var/lib/postgresql/data]
    healthcheck: { test: [CMD-SHELL, "pg_isready -U nova -d nova"], interval: 5s, timeout: 5s, retries: 30 }
    restart: unless-stopped
  redis:
    image: $($Manifest.images.redis)
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    healthcheck: { test: [CMD, redis-cli, ping], interval: 5s, timeout: 3s, retries: 30 }
    restart: unless-stopped
  migrate:
    image: $($Manifest.images.server)
    entrypoint: ["/bin/sh", "-ec"]
    command: ["export NOVA_DATABASE_URL=postgresql://nova:`$(cat /run/secrets/postgres_password)@postgres:5432/nova; python -m migrations migrate"]
    secrets: [postgres_password]
    depends_on: { postgres: { condition: service_healthy } }
  whisper:
    image: $($Profile.speech.image)
    environment:
      WHISPER__MODEL: $($Profile.speech.model_path)
      WHISPER__COMPUTE_TYPE: $($Profile.speech.compute_type)
    gpus: all
    restart: unless-stopped
  llm:
    image: $($Profile.language.image)
    # --served-model-name matters: without it vLLM advertises the model under
    # its on-disk path, while the API asks for NOVA_AI_TEXT_MODEL, and every
    # completion comes back 404.
    command: ["--model", "$($Profile.language.model_path)", "--served-model-name", "$($Profile.language.model_id)", "--max-model-len", "$($Profile.language.max_model_len)", "--gpu-memory-utilization", "$($Profile.language.gpu_memory_utilization)"]
    environment:
$llmEnvironment
    gpus: all
    restart: unless-stopped
  api:
    image: $($Manifest.images.server)
    entrypoint: ["/bin/sh", "-ec"]
    command: ["export NOVA_DATABASE_URL=postgresql://nova:`$(cat /run/secrets/postgres_password)@postgres:5432/nova; exec python serve.py"]
    environment:
      NOVA_ENV: development
      NOVA_REDIS_URL: redis://redis:6379/0
      NOVA_REQUIRE_AUTH: "true"
      NOVA_ORGANIZATION_ID: "$orgId"
      NOVA_ORGANIZATION_NAME: "$OrganizationName"
      NOVA_ORGANIZATION_TYPE: "$OrganizationType"
      NOVA_AI_TEXT_BASE_URL: http://llm:8000/v1
      NOVA_AI_TEXT_MODEL: "$($Profile.language.model_id)"
      NOVA_AI_SPEECH_BASE_URL: http://whisper:8000
      NOVA_AI_SPEECH_MODEL: "$($Profile.speech.model_id)"
      NOVA_AI_MAX_CONCURRENT_TEXT: "$($Profile.max_concurrent_text)"
      NOVA_AI_MAX_CONCURRENT_SPEECH: "$($Profile.max_concurrent_speech)"
      NOVA_AI_MAX_QUEUE: "$($Profile.queue_size)"
    ports: ["8080:8000"]
    secrets: [postgres_password]
    depends_on:
      migrate: { condition: service_completed_successfully }
      redis: { condition: service_healthy }
      whisper: { condition: service_started }
      llm: { condition: service_started }
    restart: unless-stopped
secrets:
  postgres_password: { file: "$($dbSecret.Replace('\','/'))" }
volumes: { nova-postgres: {} }
x-nova-lab:
  release_id: "$($Manifest.release_id)"
  profile_id: "$($Profile.id)"
  deployment_id: "$deploymentId"
"@
    $path = Join-Path $script:Generated 'compose.quick-lab.yml'
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $script:Generated 'compose.previous.yml') -Force
    }
    [IO.File]::WriteAllText($path, $compose, (New-Object Text.UTF8Encoding($false)))
    return $path
}

function Invoke-Compose([string[]]$Arguments) {
    $compose = Join-Path $script:Generated 'compose.quick-lab.yml'
    if (-not (Test-Path $compose)) { throw 'LAB_CONFIGURATION_MISSING' }
    $result = Run-Captured 'docker.exe' (@('compose','--file',$compose) + $Arguments)
    if ($result.ExitCode -ne 0) { throw "COMPOSE_FAILED: $($result.Output)" }
    return $result.Output
}

function Get-Health {
    $live = $false; $ready = $false
    try { $live = (Invoke-RestMethod -TimeoutSec 5 'http://127.0.0.1:8080/api/livez').status -eq 'ok' } catch {}
    try { $ready = [bool](Invoke-RestMethod -TimeoutSec 5 'http://127.0.0.1:8080/api/readyz').ready } catch {}
    [pscustomobject]@{ Livez = $live; Readyz = $ready; Containers = (Run-Captured 'docker.exe' @('ps','--filter','label=com.docker.compose.project=nova-organization-lab','--format','{{.Names}}: {{.Status}}')).Output }
}

function Restore-PreviousConfiguration {
    $previous = Join-Path $script:Generated 'compose.previous.yml'
    if (-not (Test-Path -LiteralPath $previous)) { return $false }
    $result = Run-Captured 'docker.exe' @('compose','--file',$previous,'up','-d')
    if ($result.ExitCode -ne 0) { return $false }
    Copy-Item -LiteralPath $previous -Destination (Join-Path $script:Generated 'compose.quick-lab.yml') -Force
    return $true
}

function Get-FailureAdvice([string]$Message) {
    switch -Regex ($Message) {
        '^DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING' {
            return "Docker Desktop est installe mais son moteur ne repond pas.`nOuvrez Docker Desktop, attendez l etat 'Engine running', puis relancez l installation." }
        '^DOCKER_INSTALLED_STARTUP_PENDING' {
            return "Docker Desktop a ete installe mais n a pas fini de demarrer.`nUn redemarrage peut etre necessaire; l installation reprendra automatiquement." }
        '^DOCKER_INSTALL_FAILED' {
            return "L installation de Docker Desktop a echoue.`nInstallez Docker Desktop manuellement, demarrez-le, puis relancez cet installateur." }
        '^LLM_RUNTIME_INCOMPATIBLE' {
            return "Aucun runtime IA local compatible avec ce GPU n a ete trouve.`nL installation s arrete: aucun service cloud n est utilise en remplacement." }
        '^LLM_GPU_NOT_VISIBLE_TO_DOCKER' {
            return "Docker ne voit pas la carte NVIDIA.`nMettez a jour le pilote NVIDIA, activez le support GPU dans Docker Desktop, puis relancez." }
        '^LLM_GPU_TOO_OLD' {
            return "Ce GPU est trop ancien pour le runtime IA local (calcul 7.5 minimum).`nAucun service cloud n est utilise en remplacement." }
        '^LLM_COMPAT_PROBE_FAILED' {
            return "Le test de compatibilite GPU n a pas pu s executer.`nVerifiez que Docker Desktop expose le GPU, puis relancez." }
        '^DOCKER_COMPOSE_UNAVAILABLE' {
            return "Docker fonctionne mais 'docker compose' est indisponible.`nMettez Docker Desktop a jour." }
        '^WINGET_REQUIRED' {
            return "winget est requis pour installer Docker Desktop.`nInstallez 'App Installer' depuis le Microsoft Store, ou installez Docker Desktop manuellement." }
        '^WSL_INSTALL_FAILED' {
            return "L installation de WSL2 a echoue.`nVerifiez Windows Update, puis relancez." }
        '^VIRTUALIZATION_DISABLED_IN_FIRMWARE' {
            return "La virtualisation est desactivee dans le BIOS/UEFI.`nActivez SVM (AMD) ou VT-x (Intel), puis relancez." }
        '^OCI_' {
            return "La verification cryptographique d une image a echoue.`nAucune image non verifiee n a ete installee. Relancez; si l erreur persiste, signalez ce code." }
        '^MANIFEST_' {
            return "Le manifeste de release n a pas pu etre verifie.`nAucune installation n a eu lieu. Verifiez la connexion, puis relancez." }
        default { return '' }
    }
}

function Show-Result([string]$Title, [string]$Text, [bool]$Error = $false) {
    if ($Headless) { Write-Output $Text; return }
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($Text, $Title, 'OK', $(if ($Error) {'Error'} else {'Information'})) | Out-Null
}

function Invoke-Install {
    $hardware = Get-Hardware
    $profile = Select-HardwareProfile $hardware (Read-BundledProfiles)
    if (-not $profile.supported) { throw $profile.diagnostic }
    if ($hardware.RamGiB -lt $profile.minimum_ram_gib) { throw 'INSUFFICIENT_RAM' }
    if ($hardware.DiskFreeGiB -lt 35) { throw 'INSUFFICIENT_DISK' }
    $prereq = Ensure-Prerequisites $hardware $profile.id
    if ($prereq -eq 'reboot-required') { Show-Result 'Nova Organization Server Setup' 'WSL2 a ete installe. Un redemarrage est requis; l installation reprendra automatiquement.'; return }
    $manifest = Get-VerifiedReleaseManifest
    $releaseProfile = @($manifest.profiles | Where-Object { $_.id -eq $profile.id })[0]
    if (-not $releaseProfile) { throw 'PROFILE_NOT_IN_SIGNED_RELEASE_MANIFEST' }
    Import-ReleaseImages $manifest $releaseProfile
    # Decided on the real GPU, after the verified image is on disk and before
    # any configuration is written.
    $runtime = Resolve-LlmRuntimeMode $releaseProfile
    $compose = Write-LabCompose $manifest $releaseProfile $runtime
    Save-State 'starting' $profile.id $manifest.release_id
    try {
        Invoke-Compose @('up','-d') | Out-Null
        $deadline = (Get-Date).AddMinutes(10)
        do { Start-Sleep -Seconds 5; $health = Get-Health } until ($health.Readyz -or (Get-Date) -gt $deadline)
        if (-not $health.Livez) { throw 'NOVA_LIVEZ_FAILED' }
        if (-not $health.Readyz) { throw 'NOVA_READYZ_FAILED' }
    } catch {
        $rolledBack = Restore-PreviousConfiguration
        Write-SafeLog ("update_failed rollback={0}" -f $rolledBack)
        throw
    }
    Save-State 'ready' $profile.id $manifest.release_id
    Show-Result 'Nova Organization Server — READY' "Organization: $OrganizationName`nProfile: $($profile.id)`nNova API: http://127.0.0.1:8080`nLanguage runtime: $($runtime.Mode)`nPostgreSQL, Redis, Speech, Language and Nova Server are running."
}

if ($LibraryMode) { return }

if ($Action -eq 'selftest') {
    try {
        Assert-ReleasePublicKeyConfigured
        $rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        try {
            $rsa.PersistKeyInCsp = $false
            $rsa.FromXmlString($ReleasePublicKeyXml)
            $probe = [Text.Encoding]::UTF8.GetBytes('nova-release-key-selftest')
            $empty = New-Object byte[] ($rsa.KeySize / 8)
            $oid = [Security.Cryptography.CryptoConfig]::MapNameToOID('SHA256')
            if ($rsa.VerifyData($probe, $oid, $empty)) { throw 'RELEASE_PUBLIC_KEY_ACCEPTS_INVALID_SIGNATURE' }
            Write-Output ('RELEASE_PUBLIC_KEY_OK bits={0} origin={1} installer={2}' -f $rsa.KeySize, $script:AllowedManifestOrigin, $script:Version)
            exit 0
        } finally { $rsa.Dispose() }
    } catch {
        Write-Output ('SELFTEST_FAILED {0}' -f $_.Exception.Message)
        exit 1
    }
}

try {
    Initialize-Directories
    Write-SafeLog "action=$Action installer=$script:Version"
    switch ($Action) {
        'analyze' { $h = Get-Hardware; $p = Select-HardwareProfile $h (Read-BundledProfiles); Show-Result 'Analyse du PC' (($h | Format-List | Out-String) + "`nProfil recommande: $($p.id)") }
        'install' { Invoke-Install }
        'resume' { Invoke-Install }
        'status' { Show-Result 'Nova Server Status' (Get-Health | Format-List | Out-String) }
        'repair' { Invoke-Compose @('up','-d','--force-recreate') | Out-Null; Show-Result 'Nova Server Repair' (Get-Health | Format-List | Out-String) }
        'update' { Invoke-Install }
        'reconfigure' { Invoke-Install }
    }
} catch {
    Write-SafeLog ("failure code={0}" -f $_.Exception.Message)
    $advice = Get-FailureAdvice $_.Exception.Message
    $detail = if ($advice) { "{0}`n`n{1}" -f $_.Exception.Message, $advice } else { $_.Exception.Message }
    Show-Result 'Nova Organization Server — Installation interrompue' ("{0}`n`nAucun fallback cloud n a ete utilise. Ouvrez Diagnostics ou reessayez." -f $detail) $true
    exit 1
}
