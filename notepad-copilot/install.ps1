<#
.SYNOPSIS
    One-click installer for Notepad + Copilot desktop tool.

.DESCRIPTION
    Detects and installs (via winget):
      - Python 3.10+
      - Node.js LTS (for GitHub Copilot CLI)
      - GitHub Copilot CLI (@github/copilot, npm global)
      - Azure CLI (optional, -SkipAzureCli to skip)
    Then:
      - Creates a .venv in the tool directory
      - pip install -r requirements.txt
      - Generates launch.bat (no console window)
      - Creates Desktop / Start Menu shortcuts

    All UI strings are intentionally English/ASCII so they render in any
    Windows console regardless of font or active code page.

.PARAMETER SkipAzureCli
    Skip Azure CLI detection / install.

.PARAMETER NoShortcut
    Do not create Desktop / Start Menu shortcuts.

.PARAMETER Force
    Recreate .venv even if it already exists.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkipAzureCli -NoShortcut
#>
[CmdletBinding()]
param(
    [switch]$SkipAzureCli,
    [switch]$NoShortcut,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference   = 'SilentlyContinue'

# ---------- Helpers -----------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}
function Write-Ok  ([string]$msg) { Write-Host "  [OK]   $msg" -ForegroundColor Green  }
function Write-Warn2([string]$msg){ Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err2([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red    }

function Test-CommandExists([string]$name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
}

function Invoke-WingetInstall {
    param(
        [Parameter(Mandatory)] [string[]]$Ids,
        [Parameter(Mandatory)] [string]   $FriendlyName
    )
    if (-not (Test-CommandExists 'winget')) {
        throw "winget not found - cannot auto-install $FriendlyName. Please install it manually."
    }
    foreach ($id in $Ids) {
        Write-Host "  -> winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent"
        & winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$FriendlyName installed (id=$id)."
            return
        }
        Write-Warn2 "winget id '$id' failed (exit $LASTEXITCODE). Trying next candidate..."
    }
    throw "winget failed to install $FriendlyName. Tried IDs: $($Ids -join ', '). Last exit: $LASTEXITCODE"
}

# Find a real python.exe (skip the WindowsApps redirector stub).
function Find-RealPython {
    $candidates = @()
    $cmds = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($c in $cmds) {
        if ($c.Source -and ($c.Source -notmatch 'WindowsApps')) {
            $candidates += $c.Source
        }
    }
    if (Test-CommandExists 'py') {
        try {
            $exe = (& py -3 -c "import sys; print(sys.executable)") 2>$null
            if ($exe) { $candidates += $exe.Trim() }
        } catch {}
    }
    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe"
    )
    foreach ($p in $patterns) {
        Get-ChildItem -Path $p -ErrorAction SilentlyContinue | ForEach-Object {
            $candidates += $_.FullName
        }
    }
    $candidates = $candidates | Select-Object -Unique
    foreach ($exe in $candidates) {
        try {
            $ver = (& $exe -c "import sys;print('%d.%d' % sys.version_info[:2])") 2>$null
            if ($ver) {
                $maj,$min = $ver.Trim().Split('.')
                if ([int]$maj -gt 3 -or ([int]$maj -eq 3 -and [int]$min -ge 10)) {
                    return [pscustomobject]@{ Exe = $exe; Version = $ver.Trim() }
                }
            }
        } catch {}
    }
    return $null
}

function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
    $userPath    = [Environment]::GetEnvironmentVariable('Path','User')
    $env:Path = "$machinePath;$userPath"
}

# ---------- Paths -------------------------------------------------------

$ToolDir = $PSScriptRoot
if (-not $ToolDir) { $ToolDir = (Get-Location).Path }
$VenvDir = Join-Path $ToolDir '.venv'
$ReqFile = Join-Path $ToolDir 'requirements.txt'

if (-not (Test-Path $ReqFile)) {
    throw "requirements.txt not found at: $ReqFile`nPlease run this script from the notepad-copilot directory."
}

Write-Host "Notepad + Copilot - one-click installer" -ForegroundColor Magenta
Write-Host "Tool directory: $ToolDir`n"

# ---------- 1. Python ---------------------------------------------------

Write-Step "Detecting Python (>= 3.10) ..."
$py = Find-RealPython
if (-not $py) {
    Write-Warn2 "No suitable Python found. Installing Python 3.12 via winget..."
    Invoke-WingetInstall -Ids @('Python.Python.3.12') -FriendlyName 'Python 3.12'
    Start-Sleep -Seconds 2
    Refresh-Path
    $py = Find-RealPython
    if (-not $py) {
        throw "Python was installed via winget but the script could not locate python.exe. Please reopen PowerShell and rerun."
    }
}
Write-Ok "Python $($py.Version)  ->  $($py.Exe)"

# ---------- 2. Node.js + Copilot CLI -----------------------------------

Write-Step "Detecting Node.js (required for Copilot CLI) ..."
if (-not (Test-CommandExists 'node')) {
    Write-Warn2 "node not found. Installing Node.js LTS via winget..."
    try {
        # Try multiple known IDs - winget catalog naming varies by version.
        Invoke-WingetInstall `
            -Ids @('OpenJS.NodeJS.LTS', 'OpenJS.NodeJS', 'CoreyButler.NVMforWindows') `
            -FriendlyName 'Node.js LTS'
        Refresh-Path
    } catch {
        Write-Err2 "Node.js auto-install failed: $_"
        Write-Warn2 "Continuing anyway. You can install Node manually from https://nodejs.org and then run:"
        Write-Warn2 "    npm install -g @github/copilot"
    }
}
if (Test-CommandExists 'node') {
    try { Write-Ok "Node $((node --version).Trim())" } catch { Write-Ok "Node detected" }
} else {
    Write-Warn2 "node still not visible in PATH. Reopen PowerShell after install if needed."
}

Write-Step "Detecting GitHub Copilot CLI ..."
if (-not (Test-CommandExists 'copilot')) {
    if (Test-CommandExists 'npm') {
        Write-Host "  -> npm install -g @github/copilot"
        try {
            & npm install -g '@github/copilot'
            if ($LASTEXITCODE -ne 0) {
                Write-Err2 "Copilot CLI install failed (npm exit $LASTEXITCODE). Run manually later: npm install -g @github/copilot"
            }
        } catch {
            Write-Err2 "npm install failed: $_"
        }
    } else {
        Write-Warn2 "npm not available - skipping Copilot CLI install. After installing Node, run: npm install -g @github/copilot"
    }
}
if (Test-CommandExists 'copilot') {
    try {
        $cv = (& copilot --version 2>$null)
        if ($cv) { Write-Ok "Copilot CLI $($cv.Trim())" } else { Write-Ok "Copilot CLI installed" }
    } catch { Write-Ok "Copilot CLI installed" }
    Write-Warn2 "Before first launch, run 'copilot' once in a terminal to complete browser sign-in."
} else {
    Write-Warn2 "Copilot CLI is not available yet. The desktop tool will still launch, but the AI pane will error out until Copilot CLI is installed and signed in."
}

# ---------- 3. Azure CLI (optional) ------------------------------------

if (-not $SkipAzureCli) {
    Write-Step "Detecting Azure CLI (needed for cloud archive / account switching, use -SkipAzureCli to skip) ..."
    if (-not (Test-CommandExists 'az')) {
        Write-Warn2 "az not found. Installing Azure CLI via winget..."
        try {
            Invoke-WingetInstall -Ids @('Microsoft.AzureCLI') -FriendlyName 'Azure CLI'
            Refresh-Path
        } catch {
            Write-Err2 "Azure CLI install failed: $_"
            Write-Warn2 "Continuing anyway - you can install it later from https://aka.ms/installazurecliwindows"
        }
    } else {
        try {
            $azv = (& az version --output tsv --query '\"azure-cli\"') 2>$null
            Write-Ok "Azure CLI $(($azv | Out-String).Trim())"
        } catch { Write-Ok "Azure CLI detected" }
    }
} else {
    Write-Warn2 "Azure CLI detection skipped (-SkipAzureCli)."
}

# ---------- 4. Virtual env + dependencies ------------------------------

Write-Step "Creating / reusing virtual environment .venv ..."
if ($Force -and (Test-Path $VenvDir)) {
    Write-Warn2 "-Force given - removing existing .venv ..."
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvDir)) {
    & $py.Exe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed (exit $LASTEXITCODE)."
    }
    Write-Ok "Created $VenvDir"
} else {
    Write-Ok "Reusing existing .venv"
}

$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPyW = Join-Path $VenvDir 'Scripts\pythonw.exe'
if (-not (Test-Path $VenvPy)) {
    throw ".venv looks incomplete: $VenvPy missing"
}

Write-Step "Installing / upgrading dependencies (PySide6, etc.) ..."
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed (exit $LASTEXITCODE)."
}
Write-Ok "Dependencies installed."

# ---------- 5. Generate launch.bat -------------------------------------

Write-Step "Generating launch.bat ..."
$LaunchBat = Join-Path $ToolDir 'launch.bat'
$launchContent = @"
@echo off
REM Notepad + Copilot - generated by install.ps1
REM Uses .venv\Scripts\pythonw.exe so no console window appears.
cd /d "%~dp0"
start "" "$VenvPyW" main.py
"@
Set-Content -Path $LaunchBat -Value $launchContent -Encoding ASCII
Write-Ok "$LaunchBat"

# ---------- 6. Shortcuts -----------------------------------------------

if (-not $NoShortcut) {
    Write-Step "Creating Desktop / Start Menu shortcuts ..."
    $WshShell = New-Object -ComObject WScript.Shell

    $targets = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop'))  'Notepad + Copilot.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'Notepad + Copilot.lnk')
    )
    foreach ($lnk in $targets) {
        try {
            $sc = $WshShell.CreateShortcut($lnk)
            $sc.TargetPath       = $LaunchBat
            $sc.WorkingDirectory = $ToolDir
            $sc.WindowStyle      = 7   # minimized; the actual UI is opened by pythonw
            $sc.Description      = 'Notepad + Copilot CLI desktop tool'
            $sc.Save()
            Write-Ok $lnk
        } catch {
            Write-Warn2 "Could not create shortcut $lnk : $_"
        }
    }
}

# ---------- 7. Done ----------------------------------------------------

Write-Host "`n==============================================" -ForegroundColor Magenta
Write-Host "  Installation complete." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "How to launch:"
Write-Host "  1. Double-click the Desktop shortcut 'Notepad + Copilot'"
Write-Host "  2. Or run: $LaunchBat"
Write-Host "  3. Or in this directory: .\.venv\Scripts\python.exe main.py"
Write-Host ""
if (Test-CommandExists 'copilot') {
    Write-Host "Tip: before first AI use, run 'copilot' once in a terminal to complete the browser sign-in." -ForegroundColor Yellow
}
