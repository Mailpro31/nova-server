[CmdletBinding()]
param(
    [ValidateSet('install','analyze','resume','status','repair','update','reconfigure','selftest',
                 'lab-enable','lab-invite','lab-disable','lab-status')]
    [string]$Action = 'install',
    [switch]$Headless,
    [string]$OrganizationName = 'Nova Lab',
    [ValidateSet('campus','business')][string]$OrganizationType = 'campus',
    # L'adresse du premier administrateur. Une installation qui n'en nomme
    # aucun livre un serveur que personne ne peut piloter : la pile repond, et
    # Nova Admin reste inaccessible. Le motif accepte la chaine vide parce que
    # le mode interactif la demande plus tard ; il refuse en revanche tout ce
    # qui ne peut etre l'adresse de personne, et il le fait a la liaison des
    # parametres -- donc avant la moindre ligne de code, et loin du conteneur.
    [ValidatePattern('^$|^[^@\s,;<>"]+@[^@\s,;<>"]+\.[^@\s,;<>".]{2,}$')]
    [string]$FirstAdminEmail = '',
    # Le second est facultatif, jamais impose. Exiger deux adresses pousserait
    # a en inventer une pour passer l'ecran, et un administrateur invente est
    # pire qu'un administrateur manquant : il occupe la place sans jamais
    # correspondre a personne.
    [ValidatePattern('^$|^[^@\s,;<>"]+@[^@\s,;<>"]+\.[^@\s,;<>".]{2,}$')]
    [string]$SecondAdminEmail = '',
    # Les domaines de courriel de l'organisation, separes par des virgules.
    # Sans eux, le serveur retombe sur le defaut de `ALLOWED_DOMAINS`, herite
    # d'un deploiement mono-tenant : les administrateurs nommes par l'amorcage
    # se connectent quand meme -- leur compte existe deja -- mais tout autre
    # membre echoue a sa premiere connexion. Une installation peut donc
    # paraitre reussie en ne testant que l'administrateur, et etre inutilisable
    # pour tout le monde d'autre. Il n'y a donc pas de defaut ici.
    [ValidatePattern('^$|^\s*(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\s*(?:,\s*(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\s*)*$')]
    [string]$OrganizationDomains = '',
    [string]$ManifestUrl = 'https://releases.novaspeak.app/server/quick-lab/manifest.json',
    [string]$ReleasePublicKeyXml = '__NOVA_RELEASE_PUBLIC_KEY_XML__',
    [switch]$LibraryMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Ce que l'operateur a reellement passe, saisi ici et nulle part ailleurs :
# `$PSBoundParameters` est propre a chaque portee, et celui d'une fonction ne
# dit rien des arguments du script. Sans cette capture, un `-OrganizationName`
# explicite serait indistinguable de son defaut, et la valeur memorisee par une
# execution precedente l'emporterait sur ce que l'operateur vient de taper.
$script:BoundParameters = $PSBoundParameters
$script:Version = '0.5.0'
$script:Root = Join-Path $env:ProgramData 'Nova\OrganizationServer'
$script:StatePath = Join-Path $script:Root 'setup-state.json'
$script:LogPath = Join-Path $script:Root 'logs\setup.log'
$script:Generated = Join-Path $script:Root 'generated'
$script:Secrets = Join-Path $script:Root 'secrets'
$script:ManifestCache = Join-Path $script:Root 'state\last-known-good-manifest.json'
$script:BundledProfiles = Join-Path $PSScriptRoot 'profiles.manifest.json'
$script:AllowedManifestOrigin = 'https://releases.novaspeak.app'
# Lab lives apart from everything the production install writes. Nothing
# below this path is read by a normal install, and enabling the Lab never
# rewrites production configuration.
$script:LabRoot = Join-Path $script:Root 'lab'
$script:LabPort = 8443
# Les domaines retenus pour cette execution. Vide tant que Resolve-InstallAnswers
# n'a pas tranche ; Write-StackCompose refuse d'ecrire un compose sans eux.
$script:ResolvedDomains = ''

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
    # Native tools write progress and warnings to stderr. $ErrorActionPreference
    # is 'Stop' for this script, and under 'Stop' a `2>&1` redirection turns the
    # FIRST stderr line into a terminating NativeCommandError -- so the exit code
    # was never read. `docker compose up -d` writes its whole progress log to
    # stderr, which is why "Container ... Running" surfaced as COMPOSE_FAILED.
    #
    # Output on stderr is not failure. Only the exit code is.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = 0
        $lines = & $File @Arguments 2>&1
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        $text = (@($lines) | ForEach-Object {
            if ($_ -is [Management.Automation.ErrorRecord]) { $_.ToString() } else { [string]$_ }
        }) -join "`n"
        return [pscustomobject]@{ ExitCode = $code; Output = $text.Trim() }
    } catch {
        # Reached when the executable itself cannot be started.
        return [pscustomobject]@{ ExitCode = 127; Output = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $previous
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
        # URL-safe alphabet: this value is interpolated into a connection
        # URL, and '/' or '+' from standard base64 corrupt it.
        $secret = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
        [IO.File]::WriteAllText($path, $secret)
        # Deux erreurs se cumulaient ici, et l'echec partait dans Out-Null.
        # `"$env:USERNAME:(R,W)"` se lit comme une variable qualifiee par un
        # lecteur : PowerShell rendait une chaine vide. Et `/grant:r:` n'est
        # pas la syntaxe d'icacls, qui attend `/grant:r` puis l'entree en
        # argument distinct. Le mot de passe PostgreSQL heritait donc des ACL
        # du dossier parent, sur toutes les installations. Le SID est prefere
        # au nom : il survit a un compte renomme, localise ou joint a un
        # domaine. SYSTEM est conserve, faute de quoi le service qui monte le
        # secret dans le conteneur ne pourrait plus le lire.
        $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
        $acl = Run-Captured 'icacls.exe' @(
            $path, '/inheritance:r', '/grant:r', "*${sid}:(R,W)", '/grant:r', '*S-1-5-18:(R,W)')
        if ($acl.ExitCode -ne 0) {
            # Fail closed : mieux vaut pas de secret qu'un secret lisible par
            # tout le monde. Le prochain appel en regenerera un.
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            throw 'SECRET_ACL_NOT_APPLIED'
        }
    }
    return $path
}

# ──────────────────────────── Premier administrateur ──────────────────────
#
# Une installation qui va jusqu'au bout sans nommer personne livre un serveur
# que personne ne peut piloter : la pile repond, /api/readyz dit oui, et Nova
# Admin refuse tout le monde. Le geste manquant tenait en une commande, mais
# elle exigeait d'ouvrir un shell dans le conteneur -- c'est-a-dire de savoir
# qu'elle existe.
#
# Deux precautions gouvernent tout ce qui suit :
#
#   - l'adresse est verifiee **avant** d'atteindre le conteneur. Une adresse
#     fautive ne produit pas une erreur, elle produit pire : un compte
#     administrateur qui ne correspondra jamais a l'identite reelle, pendant
#     que la personne se connecte et obtient un compte ordinaire ;
#   - l'adresse n'entre dans aucun journal. Write-SafeLog ne masque que les
#     affectations de mot de passe, de jeton, de secret ou d'identifiant : une
#     adresse y passerait en clair. Le journal ne recoit donc qu'un decompte.

function Test-EmailShape([string]$Value) {
    # La forme, et rien de plus. Savoir si la boite existe n'appartient qu'au
    # fournisseur d'identite ; ce controle-ci arrete seulement ce qui ne peut
    # etre l'adresse de personne. Trop strict serait pire que trop large : une
    # adresse valide refusee ici empecherait de nommer un administrateur.
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return ($Value.Trim() -match '^[^@\s,;<>"]+@[^@\s,;<>"]+\.[^@\s,;<>".]{2,}$')
}

function Protect-OperatorFile([string]$Path) {
    # Memes droits que le mot de passe de la base : le proprietaire et SYSTEM,
    # sans heritage. Volontairement distinct de Get-OrCreateSecret, qui protege
    # un secret qu'il vient de tirer au sort ; ici on protege une valeur saisie,
    # et les deux chemins doivent pouvoir echouer -- et etre verifies --
    # separement. Le SID plutot que le nom : il survit a un compte renomme,
    # localise ou joint a un domaine.
    $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    $acl = Run-Captured 'icacls.exe' @(
        $Path, '/inheritance:r', '/grant:r', "*${sid}:(R,W)", '/grant:r', '*S-1-5-18:(R,W)')
    if ($acl.ExitCode -ne 0) {
        # Fail closed, comme pour le secret : mieux vaut redemander l'adresse
        # que la laisser lisible par tout le monde.
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        throw 'ADMIN_EMAIL_ACL_NOT_APPLIED'
    }
}

function Test-DomainListShape([string]$Value) {
    # Une liste de domaines, et rien de plus. Un label commence et finit par un
    # caractere alphanumerique, deux labels au minimum, et une extension
    # alphabetique : `exemple.fr` oui, `localhost` non, `-mauvais.fr` non.
    # Les espaces autour des virgules sont tolerés parce que le serveur les
    # retire de toute facon -- les refuser serait un refus gratuit.
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return ($Value -match '^\s*(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\s*(?:,\s*(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\s*)*$')
}

function ConvertTo-DomainList([string]$Value) {
    # Forme canonique : minuscules, sans espace, sans doublon, jointe par des
    # virgules. C'est exactement ce que le serveur lira.
    $seen = @()
    foreach ($item in ($Value -split ',')) {
        $domain = $item.Trim().ToLowerInvariant()
        if ($domain -and ($seen -notcontains $domain)) { $seen += $domain }
    }
    return ($seen -join ',')
}

function Get-InstallAnswersPath {
    return (Join-Path $script:Secrets 'install-answers.json')
}

function Read-InstallAnswers {
    <#
        Ce que l'operateur a repondu, relu tel quel.

        Register-Resume ecrit dans RunOnce la commande `"<exe>" resume`, **sans
        aucun argument** : apres le redemarrage exige par WSL2, tout ce qui a
        ete saisi avant ce redemarrage a disparu. Sans cette relecture, la
        reprise reinstallerait la pile en nommant personne et en retombant sur
        les valeurs par defaut -- y compris le defaut de domaines herite, qui
        est precisement ce que cette saisie existe pour eviter.
    #>
    $path = Get-InstallAnswersPath
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $answers = Get-Content -Raw -LiteralPath $path -ErrorAction Stop | ConvertFrom-Json
    } catch {
        # Illisible n'est pas fatal : le mode interactif redemandera, et le
        # mode silencieux echouera bruyamment faute de reponse.
        return $null
    }
    if ($answers.schema_version -ne 1) { return $null }
    return $answers
}

function Save-InstallAnswers($Answers) {
    Initialize-Directories
    $path = Get-InstallAnswersPath
    $json = $Answers | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding($false)))
    Protect-OperatorFile $path
}

function Read-TextFromDialog([string]$Prompt, [string]$Title) {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $answer = [Microsoft.VisualBasic.Interaction]::InputBox($Prompt, $Title, '')
    if ($null -eq $answer) { return '' }
    return $answer.Trim()
}

function Resolve-InstallAnswers {
    <#
        Tout ce que l'installation doit savoir avant de commencer, en un seul
        endroit.

        L'ordre est celui de l'autorite : ce que l'operateur vient de passer en
        ligne de commande, sinon ce qu'une execution precedente a retenu, sinon
        ce qu'on lui demande. Le mode silencieux n'a pas de troisieme recours :
        il echoue tout de suite, avant le materiel, avant le reseau, avant la
        moindre image telechargee.

        Le nom et le type d'organisation passent par le meme chemin. Ils
        n'avaient aucun filet : apres une reprise, `Write-StackCompose` les
        regenerait a leur valeur par defaut, et le nom saisi devenait
        silencieusement « Nova Lab ».
    #>
    $stored = Read-InstallAnswers

    $domains = ConvertTo-DomainList $OrganizationDomains
    if (-not $domains -and $stored) { $domains = ConvertTo-DomainList ([string]$stored.domains) }

    $first = $FirstAdminEmail.Trim()
    $second = $SecondAdminEmail.Trim()
    if (-not $first -and $stored) {
        $kept = @(@($stored.administrators) | Where-Object { Test-EmailShape $_ })
        if ($kept.Count -ge 1) { $first = $kept[0] }
        if (-not $second -and $kept.Count -ge 2) { $second = $kept[1] }
    }

    # Le nom et le type : l'argument explicite gagne, sinon la memoire, sinon le
    # defaut du parametre. `$PSBoundParameters` distingue « passe » de
    # « laisse a sa valeur par defaut », ce qu'une comparaison ne saurait faire.
    $name = $OrganizationName
    $type = $OrganizationType
    if ($stored) {
        if (-not $script:BoundParameters.ContainsKey('OrganizationName') -and $stored.organization_name) {
            $name = [string]$stored.organization_name
        }
        if (-not $script:BoundParameters.ContainsKey('OrganizationType') -and $stored.organization_type) {
            $type = [string]$stored.organization_type
        }
    }

    if (-not $domains) {
        if ($Headless) { throw 'ORGANIZATION_DOMAINS_REQUIRED' }
        $domains = ConvertTo-DomainList (Read-TextFromDialog (
            "Domaines de courriel de $name, separes par des virgules.`n`n" +
            "Exemple : exemple.fr, cadres.exemple.fr`n`n" +
            "Seules les adresses de ces domaines pourront se connecter. " +
            "Il n'y a pas de valeur par defaut : celle du serveur appartient a " +
            "une autre organisation.") `
            'Nova Organization Server — Domaines de l organisation')
        if (-not $domains) { throw 'ORGANIZATION_DOMAINS_REQUIRED' }
    }
    if (-not (Test-DomainListShape $domains)) { throw 'ORGANIZATION_DOMAINS_INVALID' }

    if (-not $first) {
        if ($Headless) { throw 'FIRST_ADMIN_EMAIL_REQUIRED' }
        $first = Read-TextFromDialog (
            "Adresse professionnelle du premier administrateur de $name.`n`n" +
            "Ce doit etre exactement l'adresse renvoyee par votre fournisseur d'identite : " +
            "c'est elle qui rattachera le compte lors de la premiere connexion.") `
            'Nova Organization Server — Premier administrateur'
        if (-not (Test-EmailShape $first)) { throw 'FIRST_ADMIN_EMAIL_REQUIRED' }
        if (-not $second) {
            $second = Read-TextFromDialog (
                "Adresse d'un second administrateur (facultatif).`n`n" +
                "Un administrateur unique bloque l'organisation des qu'il est absent. " +
                "Laissez vide pour continuer sans.") `
                'Nova Organization Server — Second administrateur'
        }
    }
    if (-not (Test-EmailShape $first)) { throw 'FIRST_ADMIN_EMAIL_INVALID' }
    if ($second -and -not (Test-EmailShape $second)) { throw 'FIRST_ADMIN_EMAIL_INVALID' }
    $emails = @($first.ToLowerInvariant())
    if ($second -and $second.ToLowerInvariant() -ne $emails[0]) {
        $emails += $second.ToLowerInvariant()
    }

    # Une adresse d'administrateur hors des domaines declares ne pourra jamais
    # se connecter : son compte serait promu, et le fournisseur d'identite le
    # refuserait. Mieux vaut le dire ici que le decouvrir devant l'ecran de
    # connexion.
    $declared = @($domains -split ',')
    foreach ($email in $emails) {
        if ($declared -notcontains $email.Split('@')[-1]) { throw 'FIRST_ADMIN_EMAIL_OUTSIDE_DOMAINS' }
    }

    return [pscustomobject]@{
        schema_version    = 1
        organization_name = $name
        organization_type = $type
        domains           = $domains
        administrators    = $emails
    }
}

function Invoke-AdminCli([string[]]$Arguments) {
    $compose = Join-Path $script:Generated 'compose.quick-lab.yml'
    if (-not (Test-Path -LiteralPath $compose)) { throw 'LAB_CONFIGURATION_MISSING' }
    # `exec -T` : sans lui, un TTY est reclame. L'installateur tourne sans
    # console en mode silencieux et a la reprise RunOnce, et la commande y
    # resterait bloquee.
    return Run-Captured 'docker.exe' (@('compose','--file',$compose,'exec','-T','api',
        'python','admin_cli.py') + $Arguments)
}

function Set-FirstAdministrators([string[]]$Emails) {
    <#
        Idempotent par construction : `grant` **ecrit** un role, il ne l'ajoute
        pas. Rejoue -- par `update`, par `reconfigure`, ou par une simple
        reinstallation -- il reecrit la meme valeur sur le meme compte. Et
        `--create-if-missing` ne peut pas dupliquer : l'unicite
        (organisation, adresse) est tenue par un index unique en base, et la
        seconde execution retrouve le compte au lieu d'en creer un.
    #>
    if ($Emails.Count -eq 0) { throw 'FIRST_ADMIN_EMAIL_REQUIRED' }
    foreach ($email in $Emails) {
        if (-not (Test-EmailShape $email)) { throw 'FIRST_ADMIN_EMAIL_INVALID' }
    }
    foreach ($email in $Emails) {
        $result = Invoke-AdminCli @('grant', $email, 'organization_admin', '--create-if-missing')
        if ($result.ExitCode -ne 0) {
            # Ni l'adresse ni la sortie de la commande n'entrent dans ce
            # message : il est journalise tel quel par le bloc d'erreur final.
            throw ('FIRST_ADMIN_PROMOTION_FAILED: exit {0}' -f $result.ExitCode)
        }
    }
    Write-SafeLog ('first_admin promoted count={0}' -f $Emails.Count)
    return $Emails.Count
}

function Get-AdministratorReport([int]$Count) {
    # Ce que l'ecran final annonce vient de la base, pas de ce que le script
    # croit avoir fait. Un echec de lecture n'invalide pas la promotion, qui a
    # deja rendu son code de sortie ; il rend seulement le rapport muet.
    $listed = Invoke-AdminCli @('list')
    $roster = if ($listed.ExitCode -eq 0) { $listed.Output } else { '' }
    $text = if ($roster) { "Administrateurs de l organisation :`n$roster" }
            else { "Administrateurs promus : $Count" }
    if ($Count -lt 2) {
        $text += "`n`nUn seul administrateur. S il est absent, plus personne ne peut " +
                 "piloter l organisation.`nAjoutez-en un second maintenant :`n" +
                 "  docker compose --file `"$(Join-Path $script:Generated 'compose.quick-lab.yml')`" " +
                 "exec -T api python admin_cli.py grant <adresse> organization_admin --create-if-missing"
    }
    return $text
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

function Get-GeneratedIdentity {
    # Une reparation ou une mise a jour regenere ce fichier. Si elle tirait une
    # nouvelle organisation au sort, la base PostgreSQL existante ne serait pas
    # detruite : elle deviendrait invisible, indexee sous un identifiant que
    # plus personne ne demande. L'identite se relit, elle ne se reinvente pas.
    $path = Join-Path $script:Generated 'compose.quick-lab.yml'
    $identity = @{ OrganizationId = $null; DeploymentId = $null }
    if (-not (Test-Path -LiteralPath $path)) { return $identity }
    $text = Get-Content -Raw -LiteralPath $path
    $organization = [regex]::Match($text, 'NOVA_ORGANIZATION_ID:\s*"(?<value>org-[0-9a-fA-F]{32})"')
    if ($organization.Success) { $identity.OrganizationId = $organization.Groups['value'].Value }
    $deployment = [regex]::Match($text, 'deployment_id:\s*"(?<value>dpl-[0-9a-fA-F]{32})"')
    if ($deployment.Success) { $identity.DeploymentId = $deployment.Groups['value'].Value }
    return $identity
}

function Write-StackCompose($Manifest, $Profile, $Runtime) {
    # Aucun compose n'est ecrit sans domaines. C'est ici que la garantie tient :
    # `ALLOWED_DOMAINS` a un defaut cote serveur, herite d'un deploiement
    # mono-tenant, et un compose qui omettrait `NOVA_DOMAINS` le laisserait
    # servir. Le refus est donc structurel, et non un controle en amont qu'un
    # futur chemin d'appel pourrait contourner.
    #
    # Avant `Get-OrCreateSecret`, et non apres : un refus ne doit rien laisser
    # derriere lui, pas meme un mot de passe de base tire au sort pour une
    # configuration qui ne sera jamais ecrite.
    if (-not $script:ResolvedDomains) { throw 'ORGANIZATION_DOMAINS_REQUIRED' }
    if (-not (Test-DomainListShape $script:ResolvedDomains)) { throw 'ORGANIZATION_DOMAINS_INVALID' }
    Initialize-Directories
    $dbSecret = Get-OrCreateSecret 'postgres-password.txt'
    $identity = Get-GeneratedIdentity
    $orgId = if ($identity.OrganizationId) { $identity.OrganizationId } else { 'org-' + ([Guid]::NewGuid().ToString('N')) }
    if (-not $Runtime -or -not $Runtime.Mode) { throw 'LLM_RUNTIME_NOT_RESOLVED' }
    $llmEnvironment = (($Runtime.Environment.GetEnumerator() | Sort-Object Name | ForEach-Object {
        '      {0}: "{1}"' -f $_.Key, $_.Value
    }) -join "`n")
    $deploymentId = if ($identity.DeploymentId) { $identity.DeploymentId } else { 'dpl-' + ([Guid]::NewGuid().ToString('N')) }
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
    command: ["export NOVA_DATABASE_URL=postgresql://nova:`$(python -c 'import urllib.parse;print(urllib.parse.quote(open(\"/run/secrets/postgres_password\").read().strip(), safe=\"\"))')@postgres:5432/nova; python -m migrations migrate"]
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
    command: ["export NOVA_DATABASE_URL=postgresql://nova:`$(python -c 'import urllib.parse;print(urllib.parse.quote(open(\"/run/secrets/postgres_password\").read().strip(), safe=\"\"))')@postgres:5432/nova; exec python serve.py"]
    environment:
      NOVA_ENV: development
      NOVA_REDIS_URL: redis://redis:6379/0
      NOVA_REQUIRE_AUTH: "true"
      NOVA_ORGANIZATION_ID: "$orgId"
      NOVA_ORGANIZATION_NAME: "$OrganizationName"
      NOVA_ORGANIZATION_TYPE: "$OrganizationType"
      NOVA_DOMAINS: "$script:ResolvedDomains"
      NOVA_AI_TEXT_BASE_URL: http://llm:8000/v1
      NOVA_AI_TEXT_MODEL: "$($Profile.language.model_id)"
      NOVA_AI_SPEECH_BASE_URL: http://whisper:8000
      NOVA_AI_SPEECH_MODEL: "$($Profile.speech.model_id)"
      NOVA_AI_MAX_CONCURRENT_TEXT: "$($Profile.max_concurrent_text)"
      NOVA_AI_MAX_CONCURRENT_SPEECH: "$($Profile.max_concurrent_speech)"
      NOVA_AI_MAX_QUEUE: "$($Profile.queue_size)"
    # Loopback only. The API speaks plain HTTP, so publishing it on every
    # interface put dictation and transcripts on the network in clear text. The
    # only way in from another machine is the Lab gateway, which terminates TLS
    # and admits enrolled devices.
    ports: ["127.0.0.1:8080:8000"]
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

function ConvertTo-ServiceState([string]$Inspect) {
    # One line of `docker inspect`: status|health|exitcode. Kept separate from the
    # docker call so the classification itself is directly testable.
    $parts = @($Inspect.Trim() -split '\|')
    if ($parts.Count -lt 3) {
        return [pscustomobject]@{ State = 'unknown'; Status = $Inspect.Trim(); Health = ''; ExitCode = $null }
    }
    $status = $parts[0].Trim(); $health = $parts[1].Trim()
    $exit = 0; [int]::TryParse($parts[2].Trim(), [ref]$exit) | Out-Null
    $state = switch ($status) {
        'created'    { 'starting' }
        'restarting' { 'starting' }
        'removing'   { 'failed' }
        'paused'     { 'failed' }
        'dead'       { 'failed' }
        # A container that exited 0 did its job: `migrate` is meant to end.
        'exited'     { if ($exit -eq 0) { 'completed' } else { 'failed' } }
        'running'    {
            switch ($health) {
                'healthy'   { 'healthy' }
                'starting'  { 'starting' }
                'unhealthy' { 'unhealthy' }
                # No healthcheck declared: running is as good as it gets.
                default     { 'running' }
            }
        }
        default { 'unknown' }
    }
    return [pscustomobject]@{ State = $state; Status = $status; Health = $health; ExitCode = $exit }
}

function Get-ServiceState([string]$Container) {
    $probe = Run-Captured 'docker.exe' @('inspect','--format',
        '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}',
        $Container)
    if ($probe.ExitCode -ne 0) {
        return [pscustomobject]@{ State = 'absent'; Status = ''; Health = ''; ExitCode = $null }
    }
    return ConvertTo-ServiceState $probe.Output
}

function Get-LabContainers {
    $probe = Run-Captured 'docker.exe' @('ps','--all','--filter',
        'label=com.docker.compose.project=nova-organization-lab','--format','{{.Names}}')
    if ($probe.ExitCode -ne 0) { return @() }
    return @($probe.Output -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Wait-ComposeServices([int]$TimeoutMinutes = 10) {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $names = Get-LabContainers
        if ($names.Count -eq 0) { throw 'COMPOSE_NO_CONTAINERS' }
        $pending = @(); $broken = @()
        foreach ($name in $names) {
            $state = Get-ServiceState $name
            switch ($state.State) {
                'healthy'   { }
                'running'   { }
                'completed' { }
                'starting'  { $pending += $name }
                'absent'    { $pending += $name }
                'unknown'   { $pending += $name }
                'unhealthy' { $broken += ('{0}: unhealthy' -f $name) }
                'failed'    { $broken += ('{0}: {1} (exit {2})' -f $name, $state.Status, $state.ExitCode) }
            }
        }
        if ($broken.Count -gt 0) {
            # Logs are read here and nowhere else: a container that exited with a
            # non-zero code, or went unhealthy. Running is never a failure.
            $detail = foreach ($line in $broken) {
                $container = ($line -split ':')[0]
                $log = Run-Captured 'docker.exe' @('logs','--tail','40',$container)
                "{0}`n{1}" -f $line, $log.Output
            }
            Write-SafeLog ('service_failed {0}' -f ($broken -join '; '))
            throw ('SERVICE_FAILED: ' + ($detail -join "`n`n"))
        }
        if ($pending.Count -eq 0) {
            Write-SafeLog ('services_ready count={0}' -f $names.Count)
            return 'services-ready'
        }
        if ((Get-Date) -gt $deadline) { throw ('SERVICE_START_TIMEOUT: ' + ($pending -join ', ')) }
        Start-Sleep -Seconds 5
    }
}

function Invoke-Compose([string[]]$Arguments) {
    $compose = Join-Path $script:Generated 'compose.quick-lab.yml'
    if (-not (Test-Path $compose)) { throw 'LAB_CONFIGURATION_MISSING' }
    $result = Run-Captured 'docker.exe' (@('compose','--file',$compose) + $Arguments)
    # Only the exit code decides. compose reports normal progress on stderr, so
    # its output says nothing at all about success.
    if ($result.ExitCode -ne 0) { throw "COMPOSE_FAILED: exit $($result.ExitCode). $($result.Output)" }
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
        '^ORGANIZATION_DOMAINS_REQUIRED' {
            return "Aucun domaine de courriel n a ete declare pour l organisation.`nRelancez avec -OrganizationDomains exemple.fr (plusieurs domaines separes par des virgules). Sans eux, le serveur retomberait sur des domaines appartenant a une autre organisation, et aucun de vos membres ne pourrait se connecter." }
        '^ORGANIZATION_DOMAINS_INVALID' {
            return "La liste de domaines fournie n est pas valide.`nAttendu : des domaines separes par des virgules, par exemple exemple.fr,cadres.exemple.fr. Aucune configuration n a ete ecrite." }
        '^FIRST_ADMIN_EMAIL_OUTSIDE_DOMAINS' {
            return "L adresse d administrateur n appartient a aucun des domaines declares.`nElle serait promue mais ne pourrait jamais se connecter. Corrigez -FirstAdminEmail, ou ajoutez son domaine a -OrganizationDomains." }
        '^FIRST_ADMIN_EMAIL_REQUIRED' {
            return "Aucune adresse d administrateur n a ete fournie.`nRelancez avec -FirstAdminEmail <adresse professionnelle>. Sans administrateur, la pile demarre mais personne ne peut ouvrir Nova Admin." }
        '^FIRST_ADMIN_EMAIL_INVALID' {
            return "L adresse d administrateur fournie n est pas une adresse valide.`nAucun compte n a ete cree. Relancez avec -FirstAdminEmail <adresse professionnelle>." }
        '^ADMIN_EMAIL_ACL_NOT_APPLIED' {
            return "Les droits d acces au fichier retenant l adresse de l administrateur n ont pas pu etre restreints.`nL adresse n a pas ete conservee. Relancez l installateur depuis une session Windows disposant des droits d administration." }
        '^FIRST_ADMIN_PROMOTION_FAILED' {
            return "Le serveur fonctionne, mais le premier administrateur n a pas pu etre nomme.`nPersonne ne peut encore ouvrir Nova Admin. Relancez l action 'reconfigure' avec -FirstAdminEmail <adresse>, ou nommez-le a la main :`n  docker compose --file %ProgramData%\Nova\OrganizationServer\generated\compose.quick-lab.yml exec -T api python admin_cli.py grant <adresse> organization_admin --create-if-missing" }
        '^SECRET_ACL_NOT_APPLIED' {
            return "Les droits d acces au mot de passe de la base n ont pas pu etre restreints.`nLe secret n a pas ete conserve. Relancez l installateur depuis une session Windows disposant des droits d administration." }
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
        '^LAB_NOT_ENABLED' {
            return "Le mode Lab n est pas actif sur ce serveur.`nLancez l action 'lab-enable' avant de generer une invitation." }
        '^LAB_NO_LAN_ADDRESS' {
            return "Aucune adresse reseau utilisable n a ete trouvee.`nConnectez la machine au reseau local, puis relancez." }
        '^LAB_GATEWAY_FAILED' {
            return "La passerelle Lab redemarre en boucle.`nSon journal figure ci-dessus; verifiez que le serveur Nova fonctionne." }
        '^LAB_CERTIFICATE_NOT_READY' {
            return "La passerelle Lab n a pas encore produit son certificat.`nAttendez quelques secondes puis relancez 'lab-status'." }
        '^LAB_COMPOSE_FAILED' {
            return "La passerelle Lab n a pas pu demarrer.`nVerifiez que le serveur Nova fonctionne, puis relancez." }
        '^SERVICE_FAILED' {
            return "Un conteneur s est arrete en erreur ou est devenu unhealthy.`nLes journaux du conteneur concerne figurent ci-dessus." }
        '^SERVICE_START_TIMEOUT' {
            return "Des conteneurs n ont pas fini de demarrer dans le delai imparti.`nLe premier demarrage charge les modeles et peut etre long; relancez l installation." }
        '^COMPOSE_NO_CONTAINERS' {
            return "Aucun conteneur n a ete cree.`nVerifiez que Docker Desktop fonctionne, puis relancez." }
        '^COMPOSE_FAILED' {
            return "Docker Compose a retourne un code d erreur.`nLe detail figure ci-dessus; relancez apres correction." }
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

# ─────────────────────────────────────────────────────────────── Lab mode
#
# The Lab exists so a second machine can be a real client without anyone typing
# an address into a production Desktop. It is off unless explicitly enabled, it
# has its own directory, and it is the only thing this installer ever puts on a
# network interface.

function Get-LabAddress {
    # The address another machine on the same network can actually reach.
    # Virtual switches (WSL, Hyper-V, Docker) answer locally and would produce a
    # code that works on this machine and nowhere else, so they are excluded.
    $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike '127.*' -and
            $_.IPAddress -notlike '169.254.*' -and
            $_.InterfaceAlias -notmatch 'vEthernet|Loopback|WSL|Hyper-V|Docker|VirtualBox|VMware'
        }
    $chosen = @($candidates | Sort-Object -Property SkipAsSource, InterfaceMetric | Select-Object -First 1)
    if ($chosen.Count -eq 0) { throw 'LAB_NO_LAN_ADDRESS' }
    return $chosen[0].IPAddress
}

function Get-LabSettings {
    $path = Join-Path $script:LabRoot 'lab.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { throw 'LAB_SETTINGS_UNREADABLE' }
}

function Write-LabGatewayCompose($Manifest, $Settings) {
    # A separate compose file, in the Lab directory, for a separate project.
    # The name matters: while this function was also called Write-LabCompose,
    # PowerShell kept whichever definition was parsed last, and the installer's
    # call for the production stack landed here instead — three arguments into a
    # two-parameter function, and no stack compose written at all.
    # The production stack is not redefined and not restarted by enabling Lab.
    $labData = ($Settings.data_dir).Replace('\','/')
    $network = 'nova-organization-lab_default'
    $compose = @"
name: nova-organization-lab-gateway
services:
  lab-gateway:
    image: $($Manifest.images.server)
    entrypoint: ["python", "/opt/nova-lab/nova_lab_gateway.py"]
    environment:
      NOVA_LAB_DIR: /lab
      NOVA_LAB_HOST: "$($Settings.host)"
      NOVA_LAB_PORT: "$($Settings.port)"
      NOVA_LAB_UPSTREAM: http://api:8000
      NOVA_LAB_ORGANIZATION_ID: "$($Settings.organization_id)"
      NOVA_LAB_ORGANIZATION_NAME: "$($Settings.organization_name)"
    volumes: ["$($labData):/lab"]
    ports: ["$($Settings.host):$($Settings.port):$($Settings.port)"]
    networks: [lab]
    restart: unless-stopped
networks:
  lab:
    name: $network
    external: true
"@
    $path = Join-Path $script:LabRoot 'compose.lab.yml'
    [IO.File]::WriteAllText($path, $compose, (New-Object Text.UTF8Encoding($false)))
    return $path
}

function Invoke-LabCompose([string[]]$Arguments) {
    $compose = Join-Path $script:LabRoot 'compose.lab.yml'
    if (-not (Test-Path $compose)) { throw 'LAB_NOT_ENABLED' }
    $result = Run-Captured 'docker.exe' (@('compose','--file',$compose) + $Arguments)
    if ($result.ExitCode -ne 0) { throw "LAB_COMPOSE_FAILED: exit $($result.ExitCode). $($result.Output)" }
    return $result.Output
}

function Set-LabFirewallRule([int]$Port, [string]$Address) {
    # The operator explicitly enabled this one LAN endpoint. Bind the firewall
    # exception to the exact address and interface chosen for the gateway, not
    # to a Windows profile: a legitimate lab LAN is sometimes classified
    # Public, while a profile-wide rule would follow the machine elsewhere.
    $interface = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $interface) { throw 'LAB_NO_LAN_ADDRESS' }
    $name = 'Nova Organization Lab (TLS)'
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -LocalAddress $Address `
        -InterfaceAlias $interface.InterfaceAlias -Profile Any -ErrorAction Stop | Out-Null
}

function Enable-Lab {
    Initialize-Directories
    New-Item -ItemType Directory -Force -Path $script:LabRoot | Out-Null
    $dataDir = Join-Path $script:LabRoot 'data'
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

    $manifest = Get-VerifiedReleaseManifest
    $address = Get-LabAddress
    $settings = [pscustomobject]@{
        schema_version    = 1
        enabled           = $true
        host              = $address
        port              = $script:LabPort
        organization_id   = 'lab-' + ([Guid]::NewGuid().ToString('N').Substring(0, 12))
        organization_name = $OrganizationName
        data_dir          = $dataDir
        enabled_at        = (Get-Date).ToUniversalTime().ToString('o')
    }
    $settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:LabRoot 'lab.json') -Encoding UTF8

    Write-LabGatewayCompose $manifest $settings | Out-Null
    Set-LabFirewallRule $script:LabPort $address
    Invoke-LabCompose @('up','-d') | Out-Null

    $fingerprint = Wait-LabCertificate 60
    Write-SafeLog ("lab_enabled host={0} port={1}" -f $address, $script:LabPort)
    return [pscustomobject]@{ Host = $address; Port = $script:LabPort; Fingerprint = $fingerprint }
}

function Wait-LabCertificate([int]$TimeoutSeconds) {
    # The gateway prints the fingerprint once the certificate exists. Reading it
    # from the log avoids a second implementation of the same hash.
    $container = 'nova-organization-lab-gateway-lab-gateway-1'
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $logs = Run-Captured 'docker.exe' @('logs',$container)
        $match = [regex]::Match($logs.Output, 'LAB_CERT_SHA256 ([0-9a-f]{64})')
        if ($match.Success) { return $match.Groups[1].Value }
        # A gateway that keeps dying will never print a fingerprint. Say so now,
        # with its own output, instead of waiting out the whole deadline.
        $state = Get-ServiceState $container
        if ($state.State -eq 'failed' -or $state.Status -eq 'restarting') {
            throw ("LAB_GATEWAY_FAILED: {0}`n{1}" -f $state.Status, $logs.Output)
        }
        Start-Sleep -Seconds 2
    }
    throw 'LAB_CERTIFICATE_NOT_READY'
}

function New-LabInvitation([double]$Hours, [int]$Uses) {
    $settings = Get-LabSettings
    if (-not $settings -or -not $settings.enabled) { throw 'LAB_NOT_ENABLED' }
    $result = Run-Captured 'docker.exe' @(
        'exec','-e',"NOVA_LAB_DIR=/lab",'-e',"NOVA_LAB_HOST=$($settings.host)",
        'nova-organization-lab-gateway-lab-gateway-1',
        'python','/opt/nova-lab/nova_lab_invite.py',
        '--hours',"$Hours",'--uses',"$Uses",'--port',"$($settings.port)")
    if ($result.ExitCode -ne 0) { throw "LAB_INVITATION_FAILED: $($result.Output)" }
    # The code is the last line: everything else the tool prints goes to stderr.
    $code = @($result.Output -split "`n" | ForEach-Object { $_.Trim() } |
              Where-Object { $_ -match '^NOVA-LAB1-' })
    if ($code.Count -ne 1) { throw 'LAB_INVITATION_FAILED' }
    # Deliberately not written to disk. An invitation that cannot be recovered
    # from the machine is the point; issue another one instead.
    return $code[0]
}

function Disable-Lab {
    $settings = Get-LabSettings
    if (-not $settings) { return 'lab-not-enabled' }
    try { Invoke-LabCompose @('down') | Out-Null } catch { }
    Get-NetFirewallRule -DisplayName 'Nova Organization Lab (TLS)' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    $settings.enabled = $false
    $settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:LabRoot 'lab.json') -Encoding UTF8
    Write-SafeLog 'lab_disabled'
    # Certificates and enrolled devices are kept: disabling is not forgetting,
    # and re-enabling should not silently invalidate every enrolled Desktop.
    return 'lab-disabled'
}

function Get-LabStatus {
    $settings = Get-LabSettings
    if (-not $settings) { return [pscustomobject]@{ Enabled = $false } }
    $state = Get-ServiceState 'nova-organization-lab-gateway-lab-gateway-1'
    $devices = 0
    $statePath = Join-Path $settings.data_dir 'lab-state.json'
    if (Test-Path -LiteralPath $statePath) {
        try { $devices = @((Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).devices).Count } catch { }
    }
    return [pscustomobject]@{
        Enabled = [bool]$settings.enabled
        Endpoint = "https://$($settings.host):$($settings.port)"
        Gateway = $state.State
        EnrolledDevices = $devices
    }
}

function Invoke-Install {
    # Avant tout le reste. En mode silencieux, une installation sans adresse
    # d'administrateur echoue ici, en une seconde, plutot qu'apres une demi-heure
    # de telechargement -- et plutot qu'en livrant un serveur que personne ne
    # pilote. En interactif, la question est posee tant que l'operateur est
    # encore devant l'ecran.
    $answers = Resolve-InstallAnswers
    Save-InstallAnswers $answers
    # Reinjecte dans la portee du script : le generateur de configuration lit
    # ces valeurs la, comme il lisait deja `$OrganizationName`. Les reaffecter
    # ici est ce qui fait qu'une reprise apres redemarrage regenere la meme
    # configuration que la premiere execution, au lieu des valeurs par defaut
    # du bloc `param`.
    $script:OrganizationName = $answers.organization_name
    $script:OrganizationType = $answers.organization_type
    $script:ResolvedDomains = $answers.domains
    $administrators = @($answers.administrators)
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
    $compose = Write-StackCompose $manifest $releaseProfile $runtime
    Save-State 'starting' $profile.id $manifest.release_id
    try {
        Invoke-Compose @('up','-d') | Out-Null
        # Containers first: tell starting from running, healthy, exited and
        # failed before asking the API anything.
        Wait-ComposeServices 10 | Out-Null
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
    # Hors du try/catch ci-dessus, et c'est delibere : Restore-PreviousConfiguration
    # ramenerait la pile precedente. Une promotion ratee n'est pas une raison
    # d'annuler une mise a jour qui, elle, a reussi -- la pile repond, readyz dit
    # oui, et seule la nomination reste a faire. L'echec reste visible : il
    # remonte au bloc d'erreur final, qui affiche le code et la marche a suivre.
    $promoted = Set-FirstAdministrators $administrators
    Show-Result 'Nova Organization Server — READY' ("Organization: $OrganizationName`nProfile: $($profile.id)`nNova API: http://127.0.0.1:8080`nLanguage runtime: $($runtime.Mode)`nPostgreSQL, Redis, Speech, Language and Nova Server are running.`n`n" + (Get-AdministratorReport $promoted))
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
        } finally { $rsa.Dispose() }

        # A tool that exits 0 while writing to stderr must not be read as having
        # failed. This is what reported "Container ... Running" as COMPOSE_FAILED:
        # docker compose writes all of its progress to stderr.
        $noisy = Run-Captured 'cmd.exe' @('/c','echo Container nova-organization-lab-postgres-1 Running 1>&2 & exit /b 0')
        if ($noisy.ExitCode -ne 0) { throw ('STDERR_TREATED_AS_FAILURE exit={0}' -f $noisy.ExitCode) }
        $failing = Run-Captured 'cmd.exe' @('/c','exit /b 3')
        if ($failing.ExitCode -ne 3) { throw ('EXIT_CODE_NOT_REPORTED exit={0}' -f $failing.ExitCode) }
        Write-Output 'COMPOSE_OUTPUT_OK stderr_is_not_failure=1 exit_code_is_read=1'

        # Running and healthy are not failures; only a non-zero exit or an
        # unhealthy healthcheck is. postgres and redis declare a healthcheck,
        # whisper, llm and api do not, and migrate is meant to exit 0.
        $expected = @{
            'running|healthy|0'   = 'healthy'
            'running|none|0'      = 'running'
            'running|starting|0'  = 'starting'
            'running|unhealthy|0' = 'unhealthy'
            'exited|none|0'       = 'completed'
            'exited|none|1'       = 'failed'
        }
        foreach ($case in $expected.Keys) {
            $got = (ConvertTo-ServiceState $case).State
            if ($got -ne $expected[$case]) { throw ('SERVICE_STATE_WRONG {0} -> {1}' -f $case, $got) }
        }
        Write-Output 'SERVICE_STATE_OK running=ok healthy=ok starting=ok unhealthy=ok exited0=ok exited1=ok'

        # Le premier administrateur. Une adresse fautive doit etre refusee ici,
        # sur le poste, et non decouverte le jour ou personne n arrive a ouvrir
        # Nova Admin. Ce controle est celui que la ligne de commande et la boite
        # de dialogue partagent.
        $emailCases = @{
            'alex@exemple.fr'             = $true
            'alex.martin@sous.exemple.fr' = $true
            ''                            = $false
            'alex'                        = $false
            'alex@exemple'                = $false
            'alex@@exemple.fr'            = $false
            'alex@exemple.f'              = $false
            'alex martin@exemple.fr'      = $false
            'alex@exemple.fr,bea@exemple.fr' = $false
        }
        foreach ($case in $emailCases.Keys) {
            if ((Test-EmailShape $case) -ne $emailCases[$case]) {
                throw ('ADMIN_EMAIL_SHAPE_WRONG case={0}' -f $(if ($case) { 'non-vide' } else { 'vide' }))
            }
        }
        Write-Output 'ADMIN_EMAIL_SHAPE_OK accepted=2 refused=7'

        # Chaque echec de nomination doit porter sa marche a suivre : c est ce
        # qui distingue un installateur d un code d erreur.
        foreach ($code in @('FIRST_ADMIN_EMAIL_REQUIRED', 'FIRST_ADMIN_EMAIL_INVALID',
                            'FIRST_ADMIN_PROMOTION_FAILED: exit 1',
                            'ADMIN_EMAIL_ACL_NOT_APPLIED')) {
            if (-not (Get-FailureAdvice $code)) { throw ('ADMIN_ADVICE_MISSING {0}' -f $code) }
        }
        Write-Output 'ADMIN_ADVICE_OK codes=4'

        # Les domaines de l organisation. Le serveur a un defaut herite d un
        # deploiement mono-tenant ; une installation faite par ce script ne doit
        # jamais le laisser servir. Ce controle est celui que la ligne de
        # commande et la boite de dialogue partagent.
        $domainCases = @{
            'exemple.fr'                      = $true
            'exemple.fr,cadres.exemple.fr'    = $true
            'exemple.fr, cadres.exemple.fr'   = $true
            'sous.domaine.exemple-lab.co.uk'  = $true
            ''                                = $false
            'localhost'                       = $false
            'exemple.f'                       = $false
            '-mauvais.fr'                     = $false
            'mauvais-.fr'                     = $false
            'exemple.fr,'                     = $false
            'exemple.fr;autre.fr'             = $false
            'http://exemple.fr'               = $false
        }
        foreach ($case in $domainCases.Keys) {
            if ((Test-DomainListShape $case) -ne $domainCases[$case]) {
                throw ('ORGANIZATION_DOMAIN_SHAPE_WRONG case={0}' -f $(if ($case) { 'non-vide' } else { 'vide' }))
            }
        }
        if ((ConvertTo-DomainList ' Exemple.FR , exemple.fr, Cadres.Exemple.fr ') -ne 'exemple.fr,cadres.exemple.fr') {
            throw 'ORGANIZATION_DOMAIN_NORMALISATION_WRONG'
        }
        Write-Output 'ORGANIZATION_DOMAIN_SHAPE_OK accepted=4 refused=8 normalised=1'

        # La garantie structurelle : aucun compose ne s ecrit sans domaines, donc
        # le defaut du serveur ne peut plus servir sur une installation faite ici.
        $previousDomains = $script:ResolvedDomains
        try {
            $script:ResolvedDomains = ''
            $refused = $false
            try { Write-StackCompose $null $null $null | Out-Null } catch {
                if ($_.Exception.Message -match '^ORGANIZATION_DOMAINS_REQUIRED') { $refused = $true }
            }
            if (-not $refused) { throw 'COMPOSE_WRITTEN_WITHOUT_DOMAINS' }
        } finally { $script:ResolvedDomains = $previousDomains }
        Write-Output 'ORGANIZATION_DOMAIN_GUARD_OK compose_refuses_empty=1'

        foreach ($code in @('ORGANIZATION_DOMAINS_REQUIRED', 'ORGANIZATION_DOMAINS_INVALID',
                            'FIRST_ADMIN_EMAIL_OUTSIDE_DOMAINS')) {
            if (-not (Get-FailureAdvice $code)) { throw ('ADMIN_ADVICE_MISSING {0}' -f $code) }
        }
        Write-Output 'ORGANIZATION_DOMAIN_ADVICE_OK codes=3'

        # If Docker is up, report what the real lab containers look like. This
        # reads state only: nothing is started, stopped or removed.
        if (Get-DockerServerVersion) {
            foreach ($name in Get-LabContainers) {
                $state = Get-ServiceState $name
                Write-Output ('SERVICE {0} status={1} health={2} exit={3} -> {4}' -f `
                    $name, $state.Status, $state.Health, $state.ExitCode, $state.State)
            }
        }
        exit 0
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
        'lab-enable' {
            $lab = Enable-Lab
            Show-Result 'Nova Organization Server — Lab' (
                "Le mode Lab est actif.`n`nAdresse: https://{0}:{1}`nEmpreinte du certificat:`n{2}`n`nGenerez une invitation avec l action 'lab-invite'." -f $lab.Host, $lab.Port, $lab.Fingerprint)
        }
        'lab-invite' {
            $code = New-LabInvitation 2 1
            Show-Result 'Nova Organization Server — Invitation Lab' (
                "Code d enrolement, valable 2 heures, utilisable une seule fois:`n`n{0}`n`nSaisissez-le dans Nova Desktop (build Lab) sur le poste client.`nCe code n est pas conserve: s il est perdu, generez-en un autre." -f $code)
        }
        'lab-disable' { Show-Result 'Nova Organization Server — Lab' (Disable-Lab) }
        'lab-status' { Show-Result 'Nova Organization Server — Lab' (Get-LabStatus | Format-List | Out-String) }
    }
} catch {
    Write-SafeLog ("failure code={0}" -f $_.Exception.Message)
    $advice = Get-FailureAdvice $_.Exception.Message
    $detail = if ($advice) { "{0}`n`n{1}" -f $_.Exception.Message, $advice } else { $_.Exception.Message }
    Show-Result 'Nova Organization Server — Installation interrompue' ("{0}`n`nAucun fallback cloud n a ete utilise. Ouvrez Diagnostics ou reessayez." -f $detail) $true
    exit 1
}
