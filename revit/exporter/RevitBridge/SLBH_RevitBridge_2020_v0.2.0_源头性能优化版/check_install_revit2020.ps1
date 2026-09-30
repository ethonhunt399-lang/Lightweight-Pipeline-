$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$logPath = Join-Path $scriptDir 'install_log_revit2020.txt'
$installDir = Join-Path $env:LOCALAPPDATA 'SLBH\RevitBridge2020'
$addinDir = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2020'
$dllPath = Join-Path $installDir 'SLBH.RevitBridge.2020.dll'
$addinPath = Join-Path $addinDir 'SLBH.RevitBridge.2020.addin'
$revitExe = 'C:\Program Files\Autodesk\Revit 2020\Revit.exe'

function Log([string]$message) {
    $line = ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

try {
    Log 'Install check started.'
    Log 'SLBH Revit Bridge 2020 - installation check'
    Log ('Revit 2020: ' + $(if (Test-Path -LiteralPath $revitExe) {'FOUND'} else {'NOT FOUND in default path'}))
    Log ('Plugin DLL: ' + $(if (Test-Path -LiteralPath $dllPath) {'FOUND'} else {'MISSING'}))
    Log ('ADDIN file: ' + $(if (Test-Path -LiteralPath $addinPath) {'FOUND'} else {'MISSING'}))
    Log ('DLL path: ' + $dllPath)
    Log ('ADDIN path: ' + $addinPath)
    exit 0
}
catch {
    try { Log ('ERROR: ' + $_.Exception.Message) } catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red }
    exit 1
}
