param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) {
    $OutputPath = Join-Path $Root "Navi.exe"
}

$VersionFile = Join-Path $Root "version.json"
if (-not (Test-Path -LiteralPath $VersionFile)) {
    throw "version.json was not found."
}
$Version = (Get-Content -Raw -LiteralPath $VersionFile | ConvertFrom-Json).version
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "version.json must contain a semantic version such as 0.1.0."
}
$ProjectVersion = [regex]::Match(
    (Get-Content -Raw -LiteralPath (Join-Path $Root "pyproject.toml")),
    '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$PackageVersion = (Get-Content -Raw -LiteralPath (Join-Path $Root "frontend\package.json") | ConvertFrom-Json).version
if ($ProjectVersion -ne $Version -or $PackageVersion -ne $Version) {
    throw "Version mismatch: version.json=$Version, pyproject.toml=$ProjectVersion, frontend/package.json=$PackageVersion."
}
$VersionParts = $Version.Split(".")
while ($VersionParts.Count -lt 4) {
    $VersionParts += "0"
}
$AssemblyVersion = $VersionParts -join "."

$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $Csc)) {
    $Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $Csc)) {
    throw "The .NET Framework C# compiler was not found."
}

$Source = Join-Path $PSScriptRoot "NaviLauncher.cs"
$Icon = Join-Path $PSScriptRoot "navi.ico"
$Manifest = Join-Path $PSScriptRoot "Navi.exe.manifest"
$Temporary = Join-Path ([IO.Path]::GetTempPath()) ("navi-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Temporary | Out-Null
try {
    $VersionSource = Join-Path $Temporary "NaviVersion.cs"
    $VersionCode = @"
using System.Reflection;
[assembly: AssemblyVersion("$AssemblyVersion")]
[assembly: AssemblyFileVersion("$AssemblyVersion")]
[assembly: AssemblyInformationalVersion("$Version")]
"@
    [IO.File]::WriteAllText($VersionSource, $VersionCode, (New-Object Text.UTF8Encoding $false))

    $ManifestText = Get-Content -Raw -LiteralPath $Manifest
    $ManifestText = [regex]::Replace(
        $ManifestText,
        '(<assemblyIdentity\s+version=")[^"]+"',
        ('${1}' + $AssemblyVersion + '"'))
    $GeneratedManifest = Join-Path $Temporary "Navi.exe.manifest"
    [IO.File]::WriteAllText($GeneratedManifest, $ManifestText, (New-Object Text.UTF8Encoding $false))

    & $Csc `
        /nologo `
        /target:winexe `
        /platform:anycpu `
        /optimize+ `
        /codepage:65001 `
        /win32icon:$Icon `
        /win32manifest:$GeneratedManifest `
        /reference:System.dll `
        /reference:System.Core.dll `
        /reference:System.Drawing.dll `
        /reference:System.Windows.Forms.dll `
        /out:$OutputPath `
        $Source `
        $VersionSource
    if ($LASTEXITCODE -ne 0) {
        throw "Navi.exe build failed with exit code $LASTEXITCODE."
    }
}
finally {
    if (Test-Path -LiteralPath $Temporary) {
        [IO.Directory]::Delete($Temporary, $true)
    }
}
Write-Host "Built $OutputPath"
