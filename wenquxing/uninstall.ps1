[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\WenquxingV02'),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'Wenquxing\v02'),
    [string]$ConfigRoot = (Join-Path $env:LOCALAPPDATA 'Wenquxing\secrets'),
    [switch]$RemoveData,
    [switch]$RemoveCredentials
)

$ErrorActionPreference = 'Stop'
$escaped = [Regex]::Escape($InstallRoot)
$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'python*' -and
    $_.CommandLine -match 'wenquxing_v2\.cli\s+run' -and
    $_.ExecutablePath -match $escaped
}
foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force
}
Remove-ItemProperty `
    -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
    -Name 'WenquxingV02' -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
if ($RemoveData) {
    if (Test-Path -LiteralPath $DataRoot) {
        Remove-Item -LiteralPath $DataRoot -Recurse -Force
    }
}
if ($RemoveCredentials) {
    $config = Join-Path $ConfigRoot 'v02.env'
    if (Test-Path -LiteralPath $config) {
        Remove-Item -LiteralPath $config -Force
    }
}
Write-Host 'Wenquxing v02 has been uninstalled.' -ForegroundColor Green
