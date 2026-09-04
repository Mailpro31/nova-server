# Le premier administrateur : ce que l'installateur doit garantir.
#
# Entierement hors ligne. Le script est charge en -LibraryMode, ecrit dans un
# repertoire jetable, et Run-Captured est remplace par un double qui note ce
# qu'on lui demande. Rien n'est demarre, aucun conteneur n'est touche, aucune
# adresse reelle n'apparait. Seul icacls est laisse passer jusqu'au vrai outil :
# une restriction de droits demandee mais non appliquee est exactement le defaut
# que ce test doit voir.
#
# Ce qui est verifie :
#   - une adresse mal formee est refusee **avant** d'atteindre le conteneur ;
#   - une promotion reussie nomme le bon role sur la bonne adresse ;
#   - rejouee deux fois, elle refait le meme geste et n'en ajoute aucun ;
#   - un echec du conteneur est signale, et son message ne porte pas l'adresse ;
#   - l'adresse retenue pour la reprise est protegee comme un secret ;
#   - le journal ne contient aucune adresse en clair.

$ErrorActionPreference = 'Stop'

$setup = Join-Path $PSScriptRoot 'NovaOrganizationServerSetup.ps1'
if (-not (Test-Path -LiteralPath $setup)) { throw 'SETUP_SCRIPT_NOT_FOUND' }

$originalProgramData = $env:ProgramData
$scratch = @()

function New-Scratch {
    $path = Join-Path ([IO.Path]::GetTempPath()) ('nova-admin-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $script:scratch += $path
    return $path
}

function Use-Scratch([string]$Path) {
    # Les chemins sont figes au chargement du script : les repointer ici est ce
    # qui permet de tout exercer sans approcher le ProgramData reel.
    $env:ProgramData = $Path
    $script:Root = Join-Path $Path 'Nova\OrganizationServer'
    $script:Generated = Join-Path $script:Root 'generated'
    $script:Secrets = Join-Path $script:Root 'secrets'
    $script:LogPath = Join-Path $script:Root 'logs\setup.log'
    $script:ManifestCache = Join-Path $script:Root 'state\last-known-good-manifest.json'
    $script:LabRoot = Join-Path $script:Root 'lab'
}

try {
    $home_ = New-Scratch
    Use-Scratch $home_
    . $setup -LibraryMode
    Use-Scratch $home_

    # Le double. icacls continue vers le vrai outil : c'est la seule facon de
    # constater que les droits sont reellement restreints.
    $realRunCaptured = Get-Command Run-Captured
    $script:Calls = @()
    $script:NextExitCode = 0
    function Run-Captured([string]$File, [string[]]$Arguments) {
        if ($File -eq 'icacls.exe') { return & $realRunCaptured $File $Arguments }
        $script:Calls += , (@($File) + $Arguments)
        return [pscustomobject]@{ ExitCode = $script:NextExitCode; Output = 'sortie simulee' }
    }

    Initialize-Directories
    # Une configuration presente : sans elle, Invoke-AdminCli refuse avant tout.
    Set-Content -LiteralPath (Join-Path $script:Generated 'compose.quick-lab.yml') -Value 'name: nova-organization-lab'

    $failures = @()
    $first = 'premiere.admin@exemple.test'
    $second = 'seconde.admin@exemple.test'

    # --- la forme de l'adresse -------------------------------------------
    foreach ($good in @('alex@exemple.fr', 'alex.martin@sous.exemple.fr', 'a+b@exemple-lab.co.uk')) {
        if (-not (Test-EmailShape $good)) { $failures += 'VALID_EMAIL_REFUSED' }
    }
    foreach ($bad in @('', '   ', 'alex', 'alex@exemple', 'alex@@exemple.fr',
                       'alex@exemple.f', 'alex martin@exemple.fr',
                       'a@exemple.fr,b@exemple.fr')) {
        if (Test-EmailShape $bad) { $failures += 'INVALID_EMAIL_ACCEPTED' }
    }

    # --- une adresse mal formee n'atteint jamais le conteneur -------------
    $script:Calls = @()
    try {
        Set-FirstAdministrators @('pas-une-adresse') | Out-Null
        $failures += 'MALFORMED_EMAIL_NOT_REFUSED'
    } catch {
        if ($_.Exception.Message -notmatch '^FIRST_ADMIN_EMAIL_INVALID') { $failures += 'MALFORMED_EMAIL_WRONG_CODE' }
    }
    if ($script:Calls.Count -ne 0) { $failures += 'MALFORMED_EMAIL_REACHED_CONTAINER' }

    # --- promotion reussie ------------------------------------------------
    $script:Calls = @()
    $promoted = Set-FirstAdministrators @($first, $second)
    if ($promoted -ne 2) { $failures += 'PROMOTED_COUNT_WRONG' }
    if ($script:Calls.Count -ne 2) { $failures += 'PROMOTION_CALL_COUNT_WRONG' }
    $flat = ($script:Calls | ForEach-Object { $_ -join ' ' })
    foreach ($needle in @('grant', 'organization_admin', '--create-if-missing', 'exec', '-T', 'api', 'admin_cli.py')) {
        if ($flat[0] -notmatch [regex]::Escape($needle)) { $failures += ('PROMOTION_ARGUMENT_MISSING:' + $needle) }
    }
    if ($flat[0] -notmatch [regex]::Escape($first)) { $failures += 'PROMOTION_TARGETS_WRONG_ADDRESS' }
    if ($flat[1] -notmatch [regex]::Escape($second)) { $failures += 'SECOND_ADMIN_NOT_PROMOTED' }

    # --- rejouee deux fois : le meme geste, jamais un de plus --------------
    $script:Calls = @()
    Set-FirstAdministrators @($first, $second) | Out-Null
    Set-FirstAdministrators @($first, $second) | Out-Null
    if ($script:Calls.Count -ne 4) { $failures += 'REPLAY_CHANGED_THE_CALLS' }
    $replayed = ($script:Calls | ForEach-Object { $_ -join ' ' })
    if ($replayed[0] -ne $flat[0] -or $replayed[1] -ne $flat[1]) { $failures += 'REPLAY_IS_NOT_IDENTICAL' }

    # --- un echec est signale, sans divulguer l'adresse -------------------
    $script:NextExitCode = 7
    try {
        Set-FirstAdministrators @($first) | Out-Null
        $failures += 'PROMOTION_FAILURE_SWALLOWED'
    } catch {
        $message = $_.Exception.Message
        if ($message -notmatch '^FIRST_ADMIN_PROMOTION_FAILED') { $failures += 'PROMOTION_FAILURE_WRONG_CODE' }
        if ($message -notmatch 'exit 7') { $failures += 'PROMOTION_FAILURE_HIDES_THE_EXIT_CODE' }
        # Ce message est journalise tel quel par le bloc d'erreur final.
        if ($message -match [regex]::Escape($first)) { $failures += 'PROMOTION_FAILURE_LEAKS_THE_ADDRESS' }
        if (-not (Get-FailureAdvice $message)) { $failures += 'PROMOTION_FAILURE_HAS_NO_ADVICE' }
    }
    $script:NextExitCode = 0

    # --- l'adresse survit au redemarrage, et reste protegee ---------------
    Save-Administrators @($first, $second)
    $stored = @(Read-StoredAdministrators)
    if ($stored.Count -ne 2 -or $stored[0] -ne $first -or $stored[1] -ne $second) {
        $failures += 'STORED_ADDRESSES_NOT_READ_BACK'
    }
    $statePath = Get-AdministratorStatePath
    $acl = Get-Acl -LiteralPath $statePath
    $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    if (-not $acl.AreAccessRulesProtected) { $failures += 'ADMIN_EMAIL_STILL_INHERITS' }
    $granted = @($acl.Access | ForEach-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value })
    if ($granted -notcontains $sid) { $failures += 'ADMIN_EMAIL_OWNER_NOT_GRANTED' }
    foreach ($open in @('S-1-1-0', 'S-1-5-32-545')) {
        if ($granted -contains $open) { $failures += 'ADMIN_EMAIL_ACL_TOO_OPEN' }
    }

    # --- le journal ne porte aucune adresse -------------------------------
    if (Test-Path -LiteralPath $script:LogPath) {
        $log = Get-Content -Raw -LiteralPath $script:LogPath
        foreach ($address in @($first, $second)) {
            if ($log -match [regex]::Escape($address)) { $failures += 'ADDRESS_IN_THE_LOG' }
        }
    }

    if ($failures.Count -gt 0) {
        Write-Output ('FIRST_ADMIN_TEST_FAILED ' + (($failures | Select-Object -Unique) -join ','))
        exit 1
    }
    Write-Output 'FIRST_ADMIN_TEST_OK shape=1 promoted=1 replay_is_idempotent=1 failure_is_reported=1 address_protected=1 log_is_clean=1'
    exit 0
} finally {
    $env:ProgramData = $originalProgramData
    foreach ($path in $scratch) {
        Remove-Item -Recurse -Force -LiteralPath $path -ErrorAction SilentlyContinue
    }
}
