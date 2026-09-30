$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$version = '0.3.7'
$dist = Join-Path $root 'Dist'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

& (Join-Path $root 'build_revit.ps1') -Year All
if ($LASTEXITCODE -ne 0) {
    throw 'Revit build failed.'
}

if (Test-Path -LiteralPath $dist) {
    $rootResolved = (Resolve-Path -LiteralPath $root).Path
    $distResolved = (Resolve-Path -LiteralPath $dist).Path
    if (-not $distResolved.StartsWith($rootResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove Dist outside package root: $distResolved"
    }
    Remove-Item -LiteralPath $dist -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $dist | Out-Null

function New-AddinManifest([string]$year, [string]$path) {
    $dllName = "SLBH.RevitBridge.$year.dll"
    $installPath = Join-Path (Join-Path $env:LOCALAPPDATA "SLBH\RevitBridge$year") $dllName
    $escapedDllPath = [System.Security.SecurityElement]::Escape($installPath)
    $addinId = if ($year -eq '2019') { '8D5A706F-352B-4B86-91E0-573A47772019' } else { '8D5A706F-352B-4B86-91E0-573A47772020' }
    $xml = @"
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>SLBH Revit Bridge $year</Name>
    <Assembly>$escapedDllPath</Assembly>
    <AddInId>$addinId</AddInId>
    <FullClassName>SLBH.RevitBridge.App</FullClassName>
    <VendorId>SLBH</VendorId>
    <VendorDescription>SLBH BIM Tools</VendorDescription>
  </AddIn>
</RevitAddIns>
"@
    [System.IO.File]::WriteAllText($path, $xml, $utf8NoBom)
}

function New-RevitPackage([string]$year) {
    $packageName = "SLBH_RevitBridge_Revit$year`_v$version"
    $packageDir = Join-Path $dist $packageName
    New-Item -ItemType Directory -Force -Path $packageDir | Out-Null

    Copy-Item -LiteralPath (Join-Path $root "SLBH.RevitBridge.$year.dll") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "INSTALL_REVIT$year.cmd") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "UNINSTALL_REVIT$year.cmd") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "CHECK_INSTALL_REVIT$year.cmd") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "install_revit$year.ps1") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "uninstall_revit$year.ps1") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "check_install_revit$year.ps1") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "UPDATE_v$version.txt") -Destination $packageDir -Force
    Copy-Item -LiteralPath (Join-Path $root "MANUAL_TEST_CHECKLIST_v$version.md") -Destination $packageDir -Force

    New-AddinManifest $year (Join-Path $packageDir "SLBH.RevitBridge.$year.addin")
    New-AddinManifest $year (Join-Path $root "SLBH.RevitBridge.$year.addin")

    $zipPath = Join-Path $dist ($packageName + '.zip')
    Compress-Archive -Path (Join-Path $packageDir '*') -DestinationPath $zipPath -Force
    Write-Host "Packaged $zipPath"
}

New-RevitPackage '2019'
New-RevitPackage '2020'
Copy-Item -LiteralPath (Join-Path $root "UPDATE_v$version.txt") -Destination $dist -Force
Copy-Item -LiteralPath (Join-Path $root "MANUAL_TEST_CHECKLIST_v$version.md") -Destination $dist -Force

# The standalone Blender add-on (Source\Blender, frozen at 0.3.6) is superseded by the
# revit_bridge module inside SLBH Toolbox and is no longer packaged with Revit releases.
