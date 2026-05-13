<#
.SYNOPSIS
    一键安装 Notepad + Copilot 桌面工具。

.DESCRIPTION
    自动检测并安装：
      - Python 3.10+ (优先走已安装的真实 python，必要时用 winget 安装)
      - Node.js LTS (用于 GitHub Copilot CLI)
      - GitHub Copilot CLI (`@github/copilot`, npm 全局)
      - Azure CLI (可选，--SkipAzureCli 跳过)
    然后：
      - 在工具目录下创建 .venv 虚拟环境
      - pip 安装 requirements.txt
      - 生成 launch.bat (无控制台窗口启动)
      - 在桌面 / 开始菜单创建快捷方式

.PARAMETER SkipAzureCli
    跳过 Azure CLI 检测/安装。

.PARAMETER NoShortcut
    不创建桌面 / 开始菜单快捷方式。

.PARAMETER Force
    即使 .venv 已存在也强制重建。

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

# ---------- 工具函数 ----------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}
function Write-Ok([string]$msg)   { Write-Host "  [OK] $msg"    -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  [WARN] $msg"  -ForegroundColor Yellow }
function Write-Err2([string]$msg) { Write-Host "  [FAIL] $msg"  -ForegroundColor Red }

function Test-CommandExists([string]$name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
}

function Invoke-WingetInstall([string]$id, [string]$friendlyName) {
    if (-not (Test-CommandExists 'winget')) {
        throw "winget 未安装，无法自动安装 $friendlyName。请手动安装后重试。"
    }
    Write-Host "  -> winget install --id $id -e --accept-package-agreements --accept-source-agreements"
    & winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget 安装 $friendlyName 失败 (exit $LASTEXITCODE)。"
    }
    # PATH 在当前 shell 不会自动刷新；提示用户重开
    Write-Warn2 "$friendlyName 已安装。新增的 PATH 在当前 PowerShell 不可见，脚本会尝试搜索常见路径。"
}

# 找一个不是 WindowsApps 重定向占位的真实 python.exe
function Find-RealPython() {
    $candidates = @()
    # 1. PATH 上的 python（排除 WindowsApps 占位）
    $cmds = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($c in $cmds) {
        if ($c.Source -and ($c.Source -notmatch 'WindowsApps')) {
            $candidates += $c.Source
        }
    }
    # 2. py launcher
    if (Test-CommandExists 'py') {
        try {
            $exe = (& py -3 -c "import sys; print(sys.executable)") 2>$null
            if ($exe) { $candidates += $exe.Trim() }
        } catch {}
    }
    # 3. 常见安装位置
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

# ---------- 路径准备 ----------------------------------------------------

$ToolDir = $PSScriptRoot
if (-not $ToolDir) { $ToolDir = (Get-Location).Path }
$VenvDir = Join-Path $ToolDir '.venv'
$ReqFile = Join-Path $ToolDir 'requirements.txt'

if (-not (Test-Path $ReqFile)) {
    throw "未找到 requirements.txt：$ReqFile`n请确认在 notepad-copilot 目录下运行此脚本。"
}

Write-Host "Notepad + Copilot 一键安装" -ForegroundColor Magenta
Write-Host "工具目录: $ToolDir`n"

# ---------- 1. Python ---------------------------------------------------

Write-Step "检测 Python (>= 3.10) ..."
$py = Find-RealPython
if (-not $py) {
    Write-Warn2 "未找到符合要求的 Python，开始通过 winget 安装 Python 3.12 ..."
    Invoke-WingetInstall -id 'Python.Python.3.12' -friendlyName 'Python 3.12'
    # winget 装完后 PATH 在当前 shell 看不到，直接搜索
    Start-Sleep -Seconds 2
    $py = Find-RealPython
    if (-not $py) {
        throw "Python 已通过 winget 安装但脚本无法定位 python.exe。请重开 PowerShell 后重试。"
    }
}
Write-Ok "Python $($py.Version) -> $($py.Exe)"

# ---------- 2. Node.js + Copilot CLI -----------------------------------

Write-Step "检测 Node.js (用于 Copilot CLI) ..."
if (-not (Test-CommandExists 'node')) {
    Write-Warn2 "未找到 node，通过 winget 安装 Node.js LTS ..."
    Invoke-WingetInstall -id 'OpenJS.NodeJS.LTS' -friendlyName 'Node.js LTS'
    # 刷新 PATH 中的 npm/node
    $machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
    $userPath    = [Environment]::GetEnvironmentVariable('Path','User')
    $env:Path = "$machinePath;$userPath"
}
if (Test-CommandExists 'node') {
    Write-Ok "Node $((node --version).Trim())"
} else {
    Write-Warn2 "node 仍不可见；如 Copilot CLI 后续步骤失败请重开 PowerShell。"
}

Write-Step "检测 GitHub Copilot CLI ..."
if (-not (Test-CommandExists 'copilot')) {
    if (Test-CommandExists 'npm') {
        Write-Host "  -> npm install -g @github/copilot"
        & npm install -g '@github/copilot'
        if ($LASTEXITCODE -ne 0) {
            Write-Err2 "Copilot CLI 安装失败。可稍后手动执行: npm install -g @github/copilot"
        }
    } else {
        Write-Warn2 "npm 不可用，跳过 Copilot CLI 安装。请重开 shell 后执行: npm install -g @github/copilot"
    }
}
if (Test-CommandExists 'copilot') {
    try {
        $cv = (& copilot --version 2>$null).Trim()
        Write-Ok "Copilot CLI $cv"
    } catch { Write-Ok "Copilot CLI 已安装" }
    Write-Warn2 "首次启动工具前请在终端执行一次 ``copilot`` 完成浏览器登录。"
} else {
    Write-Warn2 "Copilot CLI 暂不可用 — 工具仍能启动，但 AI 对话区会报错。"
}

# ---------- 3. Azure CLI（可选） ---------------------------------------

if (-not $SkipAzureCli) {
    Write-Step "检测 Azure CLI (云端归档/账户切换需要，--SkipAzureCli 可跳过) ..."
    if (-not (Test-CommandExists 'az')) {
        Write-Warn2 "未找到 az，通过 winget 安装 Azure CLI ..."
        try {
            Invoke-WingetInstall -id 'Microsoft.AzureCLI' -friendlyName 'Azure CLI'
        } catch {
            Write-Err2 "Azure CLI 安装失败：$_"
        }
    } else {
        Write-Ok "Azure CLI $(((az version --output tsv --query '\"azure-cli\"') 2>$null).Trim())"
    }
} else {
    Write-Warn2 "已跳过 Azure CLI 检测 (--SkipAzureCli)。"
}

# ---------- 4. 创建虚拟环境 + 安装依赖 ---------------------------------

Write-Step "创建 / 复用虚拟环境 .venv ..."
if ($Force -and (Test-Path $VenvDir)) {
    Write-Warn2 "--Force 指定，删除旧的 .venv ..."
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvDir)) {
    & $py.Exe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "创建虚拟环境失败 (exit $LASTEXITCODE)。"
    }
    Write-Ok "已创建 $VenvDir"
} else {
    Write-Ok "复用已有 .venv"
}

$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPyW = Join-Path $VenvDir 'Scripts\pythonw.exe'
if (-not (Test-Path $VenvPy)) {
    throw ".venv 不完整：$VenvPy 不存在"
}

Write-Step "安装 / 升级依赖 (PySide6 等) ..."
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    throw "pip 安装依赖失败 (exit $LASTEXITCODE)。"
}
Write-Ok "依赖安装完成。"

# ---------- 5. 生成 launch.bat -----------------------------------------

Write-Step "生成 launch.bat ..."
$LaunchBat = Join-Path $ToolDir 'launch.bat'
$launchContent = @"
@echo off
REM Notepad + Copilot —— 由 install.ps1 生成
REM 使用 .venv 内的 pythonw.exe，无控制台窗口
cd /d "%~dp0"
start "" "$VenvPyW" main.py
"@
Set-Content -Path $LaunchBat -Value $launchContent -Encoding ASCII
Write-Ok "$LaunchBat"

# ---------- 6. 快捷方式 ------------------------------------------------

if (-not $NoShortcut) {
    Write-Step "创建桌面 / 开始菜单快捷方式 ..."
    $WshShell = New-Object -ComObject WScript.Shell
    $iconPath = Join-Path $ToolDir 'main.py'  # 没有专门 .ico，先用 launch.bat 的默认图标

    $targets = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Notepad + Copilot.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'Notepad + Copilot.lnk')
    )
    foreach ($lnk in $targets) {
        try {
            $sc = $WshShell.CreateShortcut($lnk)
            $sc.TargetPath       = $LaunchBat
            $sc.WorkingDirectory = $ToolDir
            $sc.WindowStyle      = 7   # 最小化（实际窗口由 pythonw 弹出）
            $sc.Description      = 'Notepad + Copilot CLI 桌面工具'
            $sc.Save()
            Write-Ok $lnk
        } catch {
            Write-Warn2 "无法创建快捷方式 $lnk : $_"
        }
    }
}

# ---------- 7. 完成 -----------------------------------------------------

Write-Host "`n==============================================" -ForegroundColor Magenta
Write-Host " 安装完成 ✅" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "启动方式："
Write-Host "  1. 双击桌面快捷方式 「Notepad + Copilot」"
Write-Host "  2. 或运行 $LaunchBat"
Write-Host "  3. 或在此目录执行: .\.venv\Scripts\python.exe main.py"
Write-Host ""
if (Test-CommandExists 'copilot') {
    Write-Host "提示：首次使用 AI 对话前，请在终端执行一次 ``copilot`` 完成浏览器登录。" -ForegroundColor Yellow
}
