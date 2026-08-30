[CmdletBinding()]
param(
    [string]$ReleasePublicKeyXml = (Get-Content -Raw (Join-Path $PSScriptRoot 'release-public-key.xml')),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)

$ErrorActionPreference = 'Stop'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) { throw 'Windows .NET Framework C# compiler not found.' }
if ($ReleasePublicKeyXml -notmatch '<RSAKeyValue>') { throw 'A production RSA release public key is required.' }

$temporary = Join-Path $env:TEMP ('nova-setup-build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $scriptSource = Join-Path $PSScriptRoot 'NovaOrganizationServerSetup.ps1'
    $embeddedScript = Join-Path $temporary 'NovaOrganizationServerSetup.ps1'
    $rawScript = [IO.File]::ReadAllText($scriptSource)
    $placeholders = ([regex]::Matches($rawScript, '__NOVA_RELEASE_PUBLIC_KEY_XML__')).Count
    if ($placeholders -ne 1) {
        throw "Expected exactly one release key placeholder in the setup script, found $placeholders. Substituting more than one rewrites logic that must not contain the key."
    }
    # The placeholder sits inside a single-quoted PowerShell literal, so escape
    # single quotes, not double quotes, and drop the file's trailing newline.
    $keyLiteral = $ReleasePublicKeyXml.Trim().Replace("'", "''")
    $content = $rawScript.Replace('__NOVA_RELEASE_PUBLIC_KEY_XML__', $keyLiteral)
    if ($content -match '__NOVA_RELEASE_PUBLIC_KEY_XML__') { throw 'Release key placeholder survived substitution.' }
    if (([regex]::Matches($content, [regex]::Escape($keyLiteral))).Count -ne 1) {
        throw 'The embedded script must contain the release key exactly once.'
    }
    [IO.File]::WriteAllText($embeddedScript, $content, (New-Object Text.UTF8Encoding($false)))
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $output = Join-Path $OutputDirectory 'NovaOrganizationServerSetup.exe'
    $appManifest = Join-Path $PSScriptRoot 'installer\app.manifest'
    $profiles = Join-Path $PSScriptRoot 'profiles.manifest.json'
    $launcher = Join-Path $PSScriptRoot 'installer\NovaOrganizationServerSetup.cs'
    $compilerArguments = @(
        '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
        "/win32manifest:$appManifest",
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        "/resource:$embeddedScript,Nova.Setup.ps1",
        "/resource:$profiles,Nova.Profiles.json",
        "/out:$output", $launcher
    )
    & $compiler @compilerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }

    # Read the key back out of the artifact that was just produced. A build that
    # cannot prove its own key is embedded must not ship.
    $assembly = [Reflection.Assembly]::LoadFile($output)
    $reader = New-Object IO.StreamReader($assembly.GetManifestResourceStream('Nova.Setup.ps1'))
    try { $embedded = $reader.ReadToEnd() } finally { $reader.Dispose() }
    if ($embedded -match '__NOVA_RELEASE_PUBLIC_KEY_XML__') { throw 'Compiled installer still contains the key placeholder.' }
    if (([regex]::Matches($embedded, [regex]::Escape($keyLiteral))).Count -ne 1) { throw 'Compiled installer does not embed the release key exactly once.' }
    if ($embedded -match "-eq '<RSAKeyValue>") { throw 'Compiled installer compares the release key against itself.' }

    Write-Output $output
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
