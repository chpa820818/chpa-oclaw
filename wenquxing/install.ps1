[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\WenquxingV02'),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'Wenquxing\v02'),
    [string]$ConfigRoot = (Join-Path $env:LOCALAPPDATA 'Wenquxing\secrets'),
    [switch]$SkipStart,
    [switch]$SkipStartup,
    [switch]$ForceConfigure
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-CommandPath([string[]]$Names) {
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    return $null
}

function Read-Required([string]$Label, [switch]$Secret) {
    if ($Secret) {
        $secure = Read-Host $Label -AsSecureString
        $value = [System.Net.NetworkCredential]::new('', $secure).Password
    } else {
        $value = (Read-Host $Label).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Label cannot be empty."
    }
    return $value
}

function Read-EnvFile([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2]
        }
    }
    return $values
}

function Stop-InstalledInstance([string]$Root) {
    $escaped = [Regex]::Escape($Root)
    $processes = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like 'python*' -and
        $_.CommandLine -match 'wenquxing_v2\.cli\s+run' -and
        $_.ExecutablePath -match $escaped
    }
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force
    }
}

Write-Step 'Checking prerequisites'
$python = Get-CommandPath @('python.exe', 'python')
if (-not $python) {
    throw 'Python 3.12 or newer is required: https://www.python.org/downloads/windows/'
}
$versionText = & $python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or [version]$versionText -lt [version]'3.12') {
    throw "Python 3.12 or newer is required; detected $versionText."
}
$copilot = Get-CommandPath @('copilot.exe', 'copilot')
if (-not $copilot) {
    throw 'GitHub Copilot CLI is required: https://docs.github.com/copilot/how-tos/copilot-cli'
}
& $copilot --version
if ($LASTEXITCODE -ne 0) { throw 'GitHub Copilot CLI is not available.' }

$sourceRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'pyproject.toml'))) {
    throw 'The installer must be run from the extracted Wenquxing package.'
}

Write-Step 'Installing application files'
Stop-InstalledInstance $InstallRoot
$appRoot = Join-Path $InstallRoot 'app'
$venvRoot = Join-Path $InstallRoot '.venv'
if (Test-Path -LiteralPath $appRoot) {
    Remove-Item -LiteralPath $appRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $appRoot, $DataRoot -Force | Out-Null
foreach ($item in @('src', 'pyproject.toml', 'README.md', 'docs')) {
    $source = Join-Path $sourceRoot $item
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $appRoot -Recurse -Force
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $venvRoot 'Scripts\python.exe'))) {
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python environment.' }
}
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade $appRoot
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Python dependencies.' }

Write-Step 'Preparing private configuration'
$configPath = Join-Path $ConfigRoot 'v02.env'
$legacyPath = Join-Path $ConfigRoot 'feishu-credentials.env'
New-Item -ItemType Directory -Path $ConfigRoot -Force | Out-Null
$settings = Read-EnvFile $configPath
if (($settings.Count -eq 0) -and (Test-Path -LiteralPath $legacyPath)) {
    $settings = Read-EnvFile $legacyPath
    Write-Host 'Found retained Wenquxing Feishu credentials; reusing them.' -ForegroundColor Green
}
if ($ForceConfigure -or -not $settings['WX_APP_ID']) {
    $settings['WX_APP_ID'] = Read-Required 'Feishu App ID'
}
if ($ForceConfigure -or -not $settings['WX_APP_SECRET']) {
    $settings['WX_APP_SECRET'] = Read-Required 'Feishu App Secret' -Secret
}
if ($ForceConfigure -or -not $settings['WX_TENANT_KEY']) {
    $settings['WX_TENANT_KEY'] = Read-Required 'Feishu tenant key'
}
if ($ForceConfigure -or -not $settings['WX_OWNER_OPEN_ID']) {
    $settings['WX_OWNER_OPEN_ID'] = Read-Required 'Your Feishu open_id'
}
$configLines = @(
    '# Wenquxing v02 private configuration. Never commit this file.',
    "WX_APP_ID=$($settings['WX_APP_ID'])",
    "WX_APP_SECRET=$($settings['WX_APP_SECRET'])",
    "WX_TENANT_KEY=$($settings['WX_TENANT_KEY'])",
    "WX_OWNER_OPEN_ID=$($settings['WX_OWNER_OPEN_ID'])",
    "WX_V2_DATA_DIR=$DataRoot",
    "WX_COPILOT_CLI=$copilot",
    'WX_COPILOT_TIMEOUT_SECONDS=180',
    'WX_V2_WORKER_COUNT=4',
    'WX_ATTACHMENT_MAX_MB=50',
    'WX_ATTACHMENT_EXTRACT_MAX_MB=100',
    'WX_ATTACHMENT_ARCHIVE_MAX_FILES=12',
    'WX_V2_LOG_LEVEL=INFO'
)
[IO.File]::WriteAllLines($configPath, $configLines, [Text.UTF8Encoding]::new($false))
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $ConfigRoot /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
& icacls.exe $configPath /inheritance:r /grant:r "${identity}:F" 'SYSTEM:F' | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null

Write-Step 'Creating launcher and login startup'
$launcher = Join-Path $InstallRoot 'Start-WenquxingV02.ps1'
$launcherContent = @"
`$ErrorActionPreference = 'Stop'
`$env:WX_V2_CONFIG = '$($configPath.Replace("'", "''"))'
& '$($venvPython.Replace("'", "''"))' -m wenquxing_v2.cli run
"@
[IO.File]::WriteAllText($launcher, $launcherContent, [Text.UTF8Encoding]::new($false))
$runValue = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
if (-not $SkipStartup) {
    New-ItemProperty `
        -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'WenquxingV02' -Value $runValue -PropertyType String -Force | Out-Null
}

Write-Step 'Running deployment diagnostics'
$env:WX_V2_CONFIG = $configPath
& $venvPython -m wenquxing_v2.cli doctor
if ($LASTEXITCODE -ne 0) { throw 'Deployment diagnostics failed.' }

if (-not $SkipStart) {
    Write-Step 'Starting Wenquxing v02'
    Start-Process powershell.exe `
        -ArgumentList @(
            '-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$launcher`""
        ) -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 5
    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like 'python*' -and
        $_.CommandLine -match 'wenquxing_v2\.cli\s+run' -and
        $_.ExecutablePath -match [Regex]::Escape($InstallRoot)
    }
    if (-not $running) {
        throw "Wenquxing did not stay running. Check $DataRoot\wenquxing-v02.log"
    }
}

Write-Host "`nWenquxing v02 is installed." -ForegroundColor Green
Write-Host "Install root: $InstallRoot"
Write-Host "Data root:    $DataRoot"
Write-Host 'Next: publish the Feishu app configuration described in docs\CONFIGURATION.md.'
