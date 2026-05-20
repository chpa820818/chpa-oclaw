@echo off
setlocal

set "APP_NAME=Notepad + Copilot"
set "TOOL_DIR=%~dp0"
set "APP_ENTRY=%TOOL_DIR%main.py"
set "VENV_PYTHONW=%TOOL_DIR%.venv\Scripts\pythonw.exe"

if not exist "%APP_ENTRY%" (
    echo [%APP_NAME%] main.py not found:
    echo   %APP_ENTRY%
    exit /b 1
)

if exist "%VENV_PYTHONW%" (
    start "%APP_NAME%" /D "%TOOL_DIR%" "%VENV_PYTHONW%" "%APP_ENTRY%" %*
    exit /b
)

where pyw.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "%APP_NAME%" /D "%TOOL_DIR%" pyw.exe -3 "%APP_ENTRY%" %*
    exit /b
)

where pythonw.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "%APP_NAME%" /D "%TOOL_DIR%" pythonw.exe "%APP_ENTRY%" %*
    exit /b
)

echo [%APP_NAME%] pythonw.exe was not found.
echo Falling back to python.exe so startup errors remain visible.
echo.
cd /d "%TOOL_DIR%"
python "%APP_ENTRY%" %*
