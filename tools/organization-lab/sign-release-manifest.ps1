[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$PrivateKeyXmlPath
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $ManifestPath)) { throw 'Manifest not found.' }
if (-not (Test-Path $PrivateKeyXmlPath)) { throw 'Private signing key not found.' }
if ((Resolve-Path $PrivateKeyXmlPath).Path.StartsWith((Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)) {
    throw 'The release private key must never live inside the repository.'
}
[byte[]]$bytes = [IO.File]::ReadAllBytes((Resolve-Path $ManifestPath))
$rsa = New-Object Security.Cryptography.RSACryptoServiceProvider
try {
    $rsa.PersistKeyInCsp = $false
    $rsa.FromXmlString([IO.File]::ReadAllText((Resolve-Path $PrivateKeyXmlPath)))
    $signature = $rsa.SignData($bytes, [Security.Cryptography.CryptoConfig]::MapNameToOID('SHA256'))
    [IO.File]::WriteAllText("$ManifestPath.sig", [Convert]::ToBase64String($signature))
} finally { $rsa.Dispose() }
