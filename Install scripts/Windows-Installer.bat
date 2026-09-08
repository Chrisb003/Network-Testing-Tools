@echo off
SETLOCAL EnableDelayedExpansion
TITLE Network Diagnostics Installer & Manager

:: ---------------------------------------------------------
:: 1. CHECK FOR ADMIN PRIVILEGES
:: ---------------------------------------------------------
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting Administrative Privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: ---------------------------------------------------------
:: 2. CONFIGURATION
:: ---------------------------------------------------------
set "TARGET_DIR=%USERPROFILE%\Network-Testing-Tools\"
set "REPO_OWNER=Chrisb003"
set "REPO_NAME=Network-Testing-Tools"
set "BRANCH=main"
set "TOKEN=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"

cd /d "%~dp0"
echo ========================================================
echo   NETWORK DIAGNOSTICS - WINDOWS INSTALLER & MANAGER
echo ========================================================

:: ---------------------------------------------------------
:: 3. EXISTING INSTALLATION CHECK & UNINSTALL OPTION
:: ---------------------------------------------------------
if exist "%TARGET_DIR%app.py" (
    echo.
    echo [*] Existing installation detected at %TARGET_DIR%.
    set /p remove_app="[?] Do you want to REMOVE the existing installation completely (including database and logs)? (y/N): "
    if /i "!remove_app!"=="y" (
        set /p confirm_wipe="[?] Are you ABSOLUTELY sure? Type 'yes' to confirm total deletion: "
        if "!confirm_wipe!"=="yes" (
            echo [*] Removing Startup shortcut if present...
            del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Network Diagnostics.lnk" >nul 2>&1
            echo [*] Removing Desktop shortcut if present...
            del "%USERPROFILE%\Desktop\Network Diagnostics.lnk" >nul 2>&1
            echo [*] Removing Start Menu shortcut if present...
            del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Network Diagnostics.lnk" >nul 2>&1
            
            echo [*] Deleting application directory...
            rd /s /q "%TARGET_DIR%" >nul 2>&1
            
            echo [✓] Application completely removed.
            exit /b
        } else (
            echo [*] Deletion cancelled.
        )
    )
)

:: ---------------------------------------------------------
:: 4. CHECK FOR PYTHON (LOOP UNTIL FOUND)
:: ---------------------------------------------------------
:CHECK_PYTHON
python --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [✓] Python is detected.
    GOTO :CHECK_INTERNET
)

echo.
echo [!] Python was not found on this system.
echo [*] Opening Microsoft Store to Python 3.12 Page...
start ms-windows-store://pdp/?ProductId=9NCVDN91XZQP

echo ========================================================
echo   PLEASE INSTALL PYTHON FROM THE WINDOWS STORE WINDOW
echo ========================================================
echo   1. Click "Get" or "Install" in the Microsoft Store.
echo   2. Wait for the download and installation to finish.
echo   3. Once finished, press any key in this window to continue.
echo ========================================================
pause

goto :CHECK_PYTHON

:: ---------------------------------------------------------
:: 5. CHECK FOR INTERNET CONNECTIVITY
:: ---------------------------------------------------------
:CHECK_INTERNET
echo.
echo [*] Checking for internet connectivity...
ping -n 1 8.8.8.8 >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [!] No Internet: Requirements skipped.
    echo.
    GOTO :DEDICATED_DEVICE_PROMPT
)

echo [✓] Internet detected. Installing system certificates...
pip install pip-system-certs >nul 2>&1

:: ---------------------------------------------------------
:: 6. DOWNLOAD OR UPDATE CODE FROM GITHUB
:: ---------------------------------------------------------
echo.
echo [*] Managing application files...
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

if not exist "%TARGET_DIR%app.py" (
    echo [*] Downloading latest project files from GitHub...
    powershell -Command "$headers = @{ 'Authorization' = 'token %TOKEN%'; 'Accept' = 'application/vnd.github.v3+json' }; Invoke-WebRequest -Uri 'https://api.github.com/repos/%REPO_OWNER%/%REPO_NAME%/zipball/%BRANCH%' -Headers $headers -OutFile '%TEMP%\network_dashboard.zip'"
    
    echo [*] Extracting files into %TARGET_DIR%...
    powershell -Command "Expand-Archive -Path '%TEMP%\network_dashboard.zip' -DestinationPath '%TEMP%\network_dashboard_extract' -Force"
    
    for /d %%D in ("%TEMP%\network_dashboard_extract\*") do (
        xcopy "%%D\*" "%TARGET_DIR%" /E /H /C /I /Y >nul
    )
    
    del /f /q "%TEMP%\network_dashboard.zip" >nul 2>&1
    rd /s /q "%TEMP%\network_dashboard_extract" >nul 2>&1
    echo [✓] Files downloaded into %TARGET_DIR%.
) else (
    echo [✓] Code directory already exists. Skipping full re-download to preserve configs/database.
    set /p update_code="[?] Do you want to pull/update latest code changes from GitHub repository? (y/N): "
    if /i "!update_code!"=="y" (
        echo [*] Updating code from GitHub...
        powershell -Command "$headers = @{ 'Authorization' = 'token %TOKEN%'; 'Accept' = 'application/vnd.github.v3+json' }; Invoke-WebRequest -Uri 'https://api.github.com/repos/%REPO_OWNER%/%REPO_NAME%/zipball/%BRANCH%' -Headers $headers -OutFile '%TEMP%\network_dashboard.zip'"
        powershell -Command "Expand-Archive -Path '%TEMP%\network_dashboard.zip' -DestinationPath '%TEMP%\network_dashboard_extract' -Force"
        
        for /d %%D in ("%TEMP%\network_dashboard_extract\*") do (
            xcopy "%%D\*" "%TARGET_DIR%" /E /H /C /I /Y /EXCLUDE:%TARGET_DIR%webport+%TARGET_DIR%standalone+%TARGET_DIR%disablecleanup >nul 2>&1
        )
        
        del /f /q "%TEMP%\network_dashboard.zip" >nul 2>&1
        rd /s /q "%TEMP%\network_dashboard_extract" >nul 2>&1
        echo [✓] Code updated.
    )
)

:: ---------------------------------------------------------
:: 7. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS
:: ---------------------------------------------------------
:DEDICATED_DEVICE_PROMPT
echo.
echo --------------------------------------------------------
set /p is_dedicated="[?] Are you using this device as a dedicated test device? (y/N): "
if /i "!is_dedicated!"=="y" (
    echo     [*] Configuring for dedicated test device mode...
    
    if not exist "%TARGET_DIR%standalone" (
        type nul > "%TARGET_DIR%standalone"
        echo         [+] Created 'standalone' file.
    )
    if not exist "%TARGET_DIR%disablecleanup" (
        type nul > "%TARGET_DIR%disablecleanup"
        echo         [+] Created 'disablecleanup' file.
    )
    if not exist "%TARGET_DIR%webport" (
        echo 80 > "%TARGET_DIR%webport"
        echo         [+] Created 'webport' file set to 80.
    )
    
    :: Optional Wi-Fi Hotspot Setup for Dedicated Test Devices
    echo.
    echo     [?] Windows Mobile Hotspot Configuration:
    set /p toggle_hotspot="    [?] Do you want to configure or toggle the Windows Wi-Fi Mobile Hotspot? (y/N): "
    if /i "!toggle_hotspot!"=="y" (
        echo         [*] Configuring Windows Mobile Hotspot via PowerShell...
        powershell -ExecutionPolicy Bypass -Command ^
            "try {" ^
            "   Add-Type -AssemblyName System.Runtime.WindowsRuntime;" ^
            "   $asTask = ([System.Runtime.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 })[0];" ^
            "   $connectionProfile = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]::GetInternetConnectionProfile();" ^
            "   $tetheringManager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]::CreateForConnectionProfile($connectionProfile);" ^
            "   if ($tetheringManager.TetheringOperationalState -eq 1) {" ^
            "       Write-Host '        [✓] Mobile Hotspot is currently ENABLED.';" ^
            "       $ans = Read-Host '        Do you want to disable it? (y/N)';" ^
            "       if ($ans -eq 'y' -or $ans -eq 'Y') {" ^
            "           $task = $tetheringManager.StopTetheringAsync();" ^
            "           $task.GetAwaiter().GetResult();" ^
            "           Write-Host '        [✓] Mobile Hotspot disabled.';" ^
            "       }" ^
            "   } else {" ^
            "       Write-Host '        [?] Mobile Hotspot is currently DISABLED.';" ^
            "       $ans = Read-Host '        Do you want to enable/configure it? (y/N)';" ^
            "       if ($ans -eq 'y' -or $ans -eq 'Y') {" ^
            "           $ssid = Read-Host '        Enter Hotspot SSID [Default: Network-Dashboard]';" ^
            "           $pass = Read-Host '        Enter Hotspot Password (min 8 chars) [Default: dashboard123]';" ^
            "           if (-not $ssid) { $ssid = 'Network-Dashboard'; }" ^
            "           if (-not $pass) { $pass = 'dashboard123'; }" ^
            "           $config = $tetheringManager.GetCurrentConfiguration();" ^
            "           $config.Ssid = $ssid;" ^
            "           $config.Passphrase = $pass;" ^
            "           $configTask = $tetheringManager.ConfigureAsync($config);" ^
            "           $configTask.GetAwaiter().GetResult();" ^
            "           $startTask = $tetheringManager.StartTetheringAsync();" ^
            "           $startTask.GetAwaiter().GetResult();" ^
            "           Write-Host '        [✓] Mobile Hotspot successfully enabled!';" ^
            "       }" ^
            "   }" ^
            "} catch {" ^
            "   Write-Host '        [!] Mobile Hotspot is not supported by this device\\'s Wi-Fi adapter or requires modern Windows 10/11.';" ^
            "   Write-Host '        Tip: You can also manage Mobile Hotspot directly in Windows Settings (Network & internet -> Mobile hotspot).';" ^
            "}"
    )
) else (
    echo     [*] Skipping dedicated test device configurations.
)
echo --------------------------------------------------------

:: ---------------------------------------------------------
:: 8. OPTIONAL USER-LOGIN STARTUP (STARTUP FOLDER)
:: ---------------------------------------------------------
echo.
echo --------------------------------------------------------
set "STARTUP_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Network Diagnostics.lnk"
if exist "%STARTUP_LNK%" (
    echo [?] Background User-Login Startup is currently ENABLED.
    set /p toggle_startup="[?] Do you want to DISABLE/REMOVE the startup shortcut? (y/N): "
    if /i "!toggle_startup!"=="y" (
        del "%STARTUP_LNK%" >nul 2>&1
        echo     [✓] Startup shortcut removed.
    )
) else (
    echo [?] Background User-Login Startup is currently DISABLED.
    set /p toggle_startup="[?] Do you want to ENABLE automatic start on user login? (y/N): "
    if /i "!toggle_startup!"=="y" (
        echo     [*] Creating startup shortcut...
        powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%STARTUP_LNK%'); $sc.TargetPath = 'python'; $sc.Arguments = '\"%TARGET_DIR%setup_env.py\"'; $sc.WorkingDirectory = '%TARGET_DIR%'; $sc.Save()"
        echo     [✓] Startup shortcut created.
    )
)
echo --------------------------------------------------------

:: ---------------------------------------------------------
:: 9. ICON PREPARATION HELPER
:: ---------------------------------------------------------
powershell -ExecutionPolicy Bypass -Command ^
    "$targetDir = '%TARGET_DIR%';" ^
    "$logoPng = Join-Path $targetDir 'static\Logo.png';" ^
    "$icoPath = Join-Path $targetDir 'static\Logo.ico';" ^
    "if ((Test-Path $logoPng) -and (-not (Test-Path $icoPath))) {" ^
    "    try {" ^
    "        Add-Type -AssemblyName System.Drawing;" ^
    "        $bmp = [System.Drawing.Bitmap]::FromFile($logoPng);" ^
    "        $ico = [System.Drawing.Icon]::FromHandle($bmp.GetHicon());" ^
    "        $fs = New-Object System.IO.FileStream($icoPath, [System.IO.FileMode]::Create);" ^
    "        $ico.Save($fs);" ^
    "        $fs.Close();" ^
    "        $bmp.Dispose();" ^
    "    } catch {}" ^
    "}" >nul 2>&1

:: ---------------------------------------------------------
:: 10. DESKTOP SHORTCUT CREATION
:: ---------------------------------------------------------
echo.
set /p create_desktop="[?] Do you want to create a Desktop shortcut? (y/N): "
if /i "!create_desktop!"=="y" (
    echo     [*] Generating Desktop shortcut...
    powershell -ExecutionPolicy Bypass -Command ^
        "$targetDir = '%TARGET_DIR%';" ^
        "$icoPath = Join-Path $targetDir 'static\Logo.ico';" ^
        "$desktopPath = [Environment]::GetFolderPath('Desktop');" ^
        "$lnkPath = Join-Path $desktopPath 'Network Diagnostics.lnk';" ^
        "$ws = New-Object -ComObject WScript.Shell;" ^
        "$sc = $ws.CreateShortcut($lnkPath);" ^
        "$sc.TargetPath = 'python';" ^
        "$sc.Arguments = '\"' + $targetDir + 'setup_env.py\"';" ^
        "$sc.WorkingDirectory = $targetDir;" ^
        "if (Test-Path $icoPath) { $sc.IconLocation = \"$icoPath,0\" } " ^
        "$sc.Save();"
    echo [✓] Desktop shortcut created successfully.
)

:: ---------------------------------------------------------
:: 11. START MENU SHORTCUT CREATION
:: ---------------------------------------------------------
echo.
set /p create_startmenu="[?] Do you want to create a Start Menu shortcut? (y/N): "
if /i "!create_startmenu!"=="y" (
    echo     [*] Generating Start Menu shortcut...
    powershell -ExecutionPolicy Bypass -Command ^
        "$targetDir = '%TARGET_DIR%';" ^
        "$icoPath = Join-Path $targetDir 'static\Logo.ico';" ^
        "$startMenuPath = [Environment]::GetFolderPath('Programs');" ^
        "$lnkPath = Join-Path $startMenuPath 'Network Diagnostics.lnk';" ^
        "$ws = New-Object -ComObject WScript.Shell;" ^
        "$sc = $ws.CreateShortcut($lnkPath);" ^
        "$sc.TargetPath = 'python';" ^
        "$sc.Arguments = '\"' + $targetDir + 'setup_env.py\"';" ^
        "$sc.WorkingDirectory = $targetDir;" ^
        "if (Test-Path $icoPath) { $sc.IconLocation = \"$icoPath,0\" } " ^
        "$sc.Save();"
    echo [✓] Start Menu shortcut created successfully.
)

:: ---------------------------------------------------------
:: 12. RUN THE SETUP SCRIPT FROM TARGET DIRECTORY
:: ---------------------------------------------------------
echo.
echo [*] Launching Setup Script from %TARGET_DIR%...
cd /d "%TARGET_DIR%"
python setup_env.py
exit /b