$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$scriptDir = $PSScriptRoot
$logPath = Join-Path $scriptDir 'install_log_revit2019.txt'

function Log([string]$message) {
    $line = ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

try {
    Set-Content -LiteralPath $logPath -Value 'SLBH Revit Bridge 2019 installer log' -Encoding UTF8
    Log 'Install started.'

    if (Get-Process -Name Revit -ErrorAction SilentlyContinue) {
        throw 'Revit is running. Close every Revit window and run the installer again.'
    }

    $dllName = 'SLBH.RevitBridge.2019.dll'
    $sourceDll = Join-Path $scriptDir $dllName
    if (-not (Test-Path -LiteralPath $sourceDll)) {
        $sourceDll = Join-Path $scriptDir ('Build\Revit2019\' + $dllName)
    }
    if (-not (Test-Path -LiteralPath $sourceDll)) {
        throw ($dllName + ' is missing. Run build_revit.ps1 first or extract the full package.')
    }

    $installDir = Join-Path $env:LOCALAPPDATA 'SLBH\RevitBridge2019'
    $addinDir = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2019'
    $dllPath = Join-Path $installDir $dllName
    $addinPath = Join-Path $addinDir 'SLBH.RevitBridge.2019.addin'

    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    New-Item -ItemType Directory -Force -Path $addinDir | Out-Null

    Unblock-File -LiteralPath $sourceDll -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $sourceDll -Destination $dllPath -Force
    Unblock-File -LiteralPath $dllPath -ErrorAction SilentlyContinue

    $escapedDllPath = [System.Security.SecurityElement]::Escape($dllPath)
    $addinXml = @"
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>SLBH Revit Bridge 2019</Name>
    <Assembly>$escapedDllPath</Assembly>
    <AddInId>8D5A706F-352B-4B86-91E0-573A47772019</AddInId>
    <FullClassName>SLBH.RevitBridge.App</FullClassName>
    <VendorId>SLBH</VendorId>
    <VendorDescription>SLBH BIM Tools</VendorDescription>
  </AddIn>
</RevitAddIns>
"@

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($addinPath, $addinXml, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $scriptDir 'SLBH.RevitBridge.2019.addin'), $addinXml, $utf8NoBom)

    if (-not (Test-Path -LiteralPath $dllPath)) { throw 'DLL copy validation failed.' }
    if (-not (Test-Path -LiteralPath $addinPath)) { throw 'ADDIN manifest validation failed.' }

    Log ('DLL installed to: ' + $dllPath)
    Log ('ADDIN installed to: ' + $addinPath)
    Log 'Install completed successfully.'
    exit 0
}
catch {
    $message = $_.Exception.Message
    try { Log ('ERROR: ' + $message) } catch { Write-Host ('ERROR: ' + $message) -ForegroundColor Red }
    exit 1
}
