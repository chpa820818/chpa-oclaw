@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Review the error above.
  pause
  exit /b 1
)
echo.
echo Wenquxing v02 installation completed.
pause
