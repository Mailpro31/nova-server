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
    $content = [IO.File]::ReadAllText($scriptSource).Replace('__NOVA_RELEASE_PUBLIC_KEY_XML__', $ReleasePublicKeyXml.Replace('"','`"'))
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
    Write-Output $output
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
