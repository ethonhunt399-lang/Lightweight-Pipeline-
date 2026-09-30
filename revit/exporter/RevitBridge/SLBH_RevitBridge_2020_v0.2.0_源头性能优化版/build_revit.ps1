param(
    [ValidateSet('2019', '2020', 'All')]
    [string]$Year = 'All'
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$repoRoot = Split-Path -Parent $root
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

if (-not (Test-Path -LiteralPath $csc)) {
    throw "C# compiler not found: $csc"
}

function Build-RevitVersion([string]$targetYear) {
    $apiDir = Join-Path $repoRoot ("lib\Revit" + $targetYear)
    $outDir = Join-Path $root ("Build\Revit" + $targetYear)
    $dllName = "SLBH.RevitBridge.$targetYear.dll"
    $dllPath = Join-Path $outDir $dllName
    $define = "REVIT$targetYear"

    if (-not (Test-Path -LiteralPath (Join-Path $apiDir 'RevitAPI.dll'))) {
        throw "Missing RevitAPI.dll for $targetYear in $apiDir"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $apiDir 'RevitAPIUI.dll'))) {
        throw "Missing RevitAPIUI.dll for $targetYear in $apiDir"
    }

    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $sources = @(
        'Source\Revit\App.cs',
        'Source\Revit\AssemblyInfo.cs',
        'Source\Revit\BridgeModels.cs',
        'Source\Revit\ExportCommand.cs',
        'Source\Revit\ObjExportContext.cs',
        'Source\Revit\RevitApiCompat.cs'
    ) | ForEach-Object { Join-Path $root $_ }

    $refs = @(
        (Join-Path $apiDir 'RevitAPI.dll'),
        (Join-Path $apiDir 'RevitAPIUI.dll'),
        'System.dll',
        'System.Core.dll',
        'System.Windows.Forms.dll',
        'System.Runtime.Serialization.dll'
    )

    $args = @('/nologo', '/target:library', "/define:$define", "/out:$dllPath")
    foreach ($ref in $refs) { $args += "/reference:$ref" }
    $args += $sources

    & $csc @args
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed for Revit $targetYear"
    }

    Copy-Item -LiteralPath $dllPath -Destination (Join-Path $root $dllName) -Force
    Write-Host "Built $dllName"
}

if ($Year -eq 'All' -or $Year -eq '2019') {
    Build-RevitVersion '2019'
}
if ($Year -eq 'All' -or $Year -eq '2020') {
    Build-RevitVersion '2020'
}
