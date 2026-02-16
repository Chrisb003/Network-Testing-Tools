::This is for making sure python is installed then running the setup script which should do the rest.

@echo off
SETLOCAL EnableDelayedExpansion
TITLE Network Diagnostics Installer

:: ---------------------------------------------------------
:: 1. CHECK FOR ADMIN PRIVILEGES
:: ---------------------------------------------------------
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting Administrative Privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
echo ========================================================
echo   NETWORK DIAGNOSTICS - ONE-CLICK INSTALLER
echo ========================================================

:: ---------------------------------------------------------
:: 2. CHECK FOR PYTHON
:: ---------------------------------------------------------
python --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [✓] Python is already installed.
    GOTO :RUN_SETUP
)

echo [*] Python not found. Downloading Python 3.12...

:: ---------------------------------------------------------
:: 3. DOWNLOAD AND INSTALL PYTHON SILENTLY
:: ---------------------------------------------------------
:: Define URL and Target
SET "PYTHON_URL=https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe"
SET "INSTALLER=python_installer.exe"

:: Download using PowerShell
powershell -Command "Invoke-WebRequest -Uri '!PYTHON_URL!' -OutFile '!INSTALLER!'"

echo [*] Installing Python (this may take a minute)...
:: Install silently, add to PATH, install pip, and associate .py files
start /wait "" "!INSTALLER!" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0

:: Cleanup Installer
del "!INSTALLER!"

:: Refresh Environment Variables without restarting CMD
call :REFRESH_ENV

:: Verify Installation
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [X] Python installation failed or PATH not updated.
    echo     Please restart your computer and run this script again.
    pause
    exit /b
)

echo [✓] Python installed successfully.

:: ---------------------------------------------------------
:: 4. RUN THE SETUP SCRIPT
:: ---------------------------------------------------------
:RUN_SETUP
echo.
echo [*] Launching Setup Script...
python setup_env.py

pause
exit /b

:: ---------------------------------------------------------
:: SUBROUTINE: REFRESH ENVIRONMENT VARIABLES
:: ---------------------------------------------------------
:REFRESH_ENV
:: This hack retrieves the new PATH from the registry so we can use python immediately
for /f "tokens=2,*" %%A in ('reg query "HKLM\System\CurrentControlSet\Control\Session Manager\Environment" /v Path') do set "PATH=%%B"
exit /b