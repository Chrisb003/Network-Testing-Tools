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
:: 2. CHECK FOR PYTHON (LOOP UNTIL FOUND)
:: ---------------------------------------------------------
:CHECK_PYTHON
python --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [✓] Python is detected.
    GOTO :PRE_SETUP_TASKS
)

echo.
echo [!] Python was not found on this system.
echo [*] Opening Microsoft Store to Python 3.12 Page...
echo.

:: Opens the Microsoft Store directly to the Python 3.12 product page
start ms-windows-store://pdp/?ProductId=9NCVDN91XZQP

echo ========================================================
echo   PLEASE INSTALL PYTHON FROM THE WINDOWS STORE WINDOW
echo ========================================================
echo   1. Click "Get" or "Install" in the Microsoft Store.
echo   2. Wait for the download and installation to finish.
echo   3. Once finished, press any key in this window to continue.
echo ========================================================
pause

:: Re-check after user says they are done
goto :CHECK_PYTHON

:: ---------------------------------------------------------
:: 3. PRE-SETUP TASKS (CERTS)
:: ---------------------------------------------------------
:PRE_SETUP_TASKS
echo.
echo [*] Installing system certificates (pip-system-certs)...
:: This fixes common SSL errors in corporate environments
pip install pip-system-certs

:: ---------------------------------------------------------
:: 4. RUN THE SETUP SCRIPT
:: ---------------------------------------------------------
:RUN_SETUP
echo.
echo [*] Launching Setup Script...
python setup_env.py

:: ---------------------------------------------------------
:: SUBROUTINE: REFRESH ENVIRONMENT VARIABLES
:: ---------------------------------------------------------
:REFRESH_ENV
:: This hack retrieves the new PATH from the registry so we can use python immediately
for /f "tokens=2,*" %%A in ('reg query "HKLM\System\CurrentControlSet\Control\Session Manager\Environment" /v Path') do set "PATH=%%B"
exit /b