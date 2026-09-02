# Regeneration du Compose : ce que le generateur doit garantir.
#
# Entierement hors ligne. Aucun Docker, aucun reseau, aucun acces au
# ProgramData reel : le script est charge en -LibraryMode et ecrit dans un
# repertoire jetable. Rien n'est demarre, aucune migration n'est jouee.
#
# Ce qui est verifie :
#   - une seconde generation conserve l'organisation et le deploiement ;
#   - l'image vient du manifeste, jamais d'une constante ;
#   - la configuration precedente est gardee pour le retour arriere ;
#   - une installation neuve, elle, tire bien une identite nouvelle.
#
# La premiere garantie est la plus importante : la base PostgreSQL est indexee
# par l'identifiant d'organisation. Le regenerer ne detruit rien, mais rend
# invisible tout ce qui a ete dicte avant.

$ErrorActionPreference = 'Stop'

$setup = Join-Path $PSScriptRoot 'NovaOrganizationServerSetup.ps1'
if (-not (Test-Path -LiteralPath $setup)) { throw 'SETUP_SCRIPT_NOT_FOUND' }

# icacls, appele par Get-OrCreateSecret, a besoin d'un nom d'utilisateur.
if (-not $env:USERNAME) { $env:USERNAME = 'nova-regen-test' }

$originalProgramData = $env:ProgramData
$scratch = @()

function New-Scratch {
    $path = Join-Path ([IO.Path]::GetTempPath()) ('nova-regen-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $script:scratch += $path
    return $path
}

function Use-Scratch([string]$Path) {
    # Les chemins sont figes au chargement du script : les repointer ici est ce
    # qui permet de tester une installation neuve dans le meme processus.
    $env:ProgramData = $Path
    $script:Root = Join-Path $Path 'Nova\OrganizationServer'
    $script:Generated = Join-Path $script:Root 'generated'
    $script:Secrets = Join-Path $script:Root 'secrets'
    $script:LogPath = Join-Path $script:Root 'logs\setup.log'
    $script:ManifestCache = Join-Path $script:Root 'state\last-known-good-manifest.json'
    $script:LabRoot = Join-Path $script:Root 'lab'
}

function Read-Identity([string]$Path) {
    $text = Get-Content -Raw -LiteralPath $Path
    [pscustomobject]@{
        Organization = [regex]::Match($text, 'NOVA_ORGANIZATION_ID:\s*"([^"]+)"').Groups[1].Value
        Deployment   = [regex]::Match($text, 'deployment_id:\s*"([^"]+)"').Groups[1].Value
        ServerImage  = [regex]::Match($text, 'image:\s*(nova-local/server:\S+)').Groups[1].Value
    }
}

try {
    $first = New-Scratch
    Use-Scratch $first
    . $setup -LibraryMode
    Use-Scratch $first

    $manifest = [pscustomobject]@{
        release_id = 'quick-lab-9.9.9'
        images = [pscustomobject]@{
            server = 'nova-local/server:9.9.9'
            postgres = 'nova-local/postgres:16'
            redis = 'nova-local/redis:7'
        }
    }
    $profileSpec = [pscustomobject]@{
        id = 'LAB_GPU_6GB'
        max_concurrent_text = 1
        max_concurrent_speech = 1
        queue_size = 2
        speech = [pscustomobject]@{
            image = 'nova-local/speech:9.9.9'
            model_id = '/models/whisper-small'
            model_path = '/models/whisper-small'
            compute_type = 'int8_float16'
        }
        language = [pscustomobject]@{
            image = 'nova-local/language:9.9.9'
            model_id = 'Qwen/Qwen2.5-1.5B-Instruct-AWQ'
            model_path = '/models/qwen2.5-1.5b-instruct-awq'
            max_model_len = 2048
            gpu_memory_utilization = 0.55
        }
    }
    $runtime = @{ Mode = 'no_uva'; Environment = @{ 'VLLM_USE_V2_MODEL_RUNNER' = '0' } }

    $failures = @()

    $one = Read-Identity (Write-StackCompose $manifest $profileSpec $runtime)
    $two = Read-Identity (Write-StackCompose $manifest $profileSpec $runtime)

    if ($one.Organization -notmatch '^org-[0-9a-f]{32}$') { $failures += 'ORGANIZATION_SHAPE' }
    if ($one.Organization -ne $two.Organization) { $failures += 'ORGANIZATION_CHANGED' }
    if ($one.Deployment -ne $two.Deployment) { $failures += 'DEPLOYMENT_CHANGED' }
    if ($two.ServerImage -ne 'nova-local/server:9.9.9') { $failures += 'IMAGE_NOT_FROM_MANIFEST' }
    if (-not (Test-Path -LiteralPath (Join-Path $script:Generated 'compose.previous.yml'))) {
        $failures += 'PREVIOUS_NOT_KEPT'
    }

    Use-Scratch (New-Scratch)
    $fresh = Read-Identity (Write-StackCompose $manifest $profileSpec $runtime)
    if ($fresh.Organization -eq $one.Organization) { $failures += 'FRESH_INSTALL_REUSED_IDENTITY' }

    if ($failures.Count -gt 0) {
        Write-Output ('REGENERATION_TEST_FAILED ' + ($failures -join ','))
        exit 1
    }
    Write-Output 'REGENERATION_TEST_OK identity_preserved=1 image_from_manifest=1 previous_kept=1 fresh_install_mints=1'
    exit 0
} finally {
    $env:ProgramData = $originalProgramData
    foreach ($path in $scratch) {
        Remove-Item -Recurse -Force -LiteralPath $path -ErrorAction SilentlyContinue
    }
}
