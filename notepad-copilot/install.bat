@echo off
REM 双击此文件即可一键安装 Notepad + Copilot
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
