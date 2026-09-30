$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$logPath = Join-Path $scriptDir 'install_log_revit2019.txt'

function Log([string]$message) {
    $line = ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

try {
    Log 'Uninstall started.'
    if (Get-Process -Name Revit -ErrorAction SilentlyContinue) {
        throw 'Revit is running. Close every Revit window and try again.'
    }
    $installDir = Join-Path $env:LOCALAPPDATA 'SLBH\RevitBridge2019'
    $addinPath = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2019\SLBH.RevitBridge.2019.addin'
    if (Test-Path -LiteralPath $addinPath) { Remove-Item -LiteralPath $addinPath -Force }
    if (Test-Path -LiteralPath $installDir) { Remove-Item -LiteralPath $installDir -Recurse -Force }
    Log 'Uninstall completed successfully.'
    exit 0
}
catch {
    try { Log ('ERROR: ' + $_.Exception.Message) } catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red }
    exit 1
}
