[CmdletBinding()]
param([string]$Version = '0.4.0')

$ErrorActionPreference = 'Stop'
$stage = Join-Path $env:TEMP "Wenquxing-v$Version-windows"
$archive = Join-Path $PSScriptRoot "dist\Wenquxing-v$Version-windows.zip"
if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
foreach ($item in @(
    'src', 'docs', 'media', 'tests', 'pyproject.toml', 'README.md', 'LICENSE',
    'install.bat', 'install.ps1', 'uninstall.ps1'
)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $item) `
        -Destination $stage -Recurse -Force
}
Get-ChildItem -LiteralPath $stage -Directory -Recurse -Force |
    Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
    Remove-Item -Recurse -Force
$dist = Split-Path -Parent $archive
New-Item -ItemType Directory -Path $dist -Force | Out-Null
if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $archive `
    -CompressionLevel Optimal
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Host "Created $archive" -ForegroundColor Green
