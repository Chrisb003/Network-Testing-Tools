<#
.SYNOPSIS
    Network Diagnostics Installer & Manager
#>

# Capture the original user's paths before any elevation happens
param (
    [string]$OriginalProfile = $env:USERPROFILE,
    [string]$OriginalDesktop = [Environment]::GetFolderPath('Desktop'),
    [string]$OriginalAppData = $env:APPDATA
)

# ---------------------------------------------------------
# 1. CHECK FOR ADMIN PRIVILEGES
# ---------------------------------------------------------
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[!] Requesting Administrative Privileges..." -ForegroundColor Yellow
    
    # Check if running from memory (no file path) or from a local file
    if ([string]::IsNullOrEmpty($PSCommandPath)) {
        # Re-run the in-memory download command for the elevated session
        $MemCommand = "& ([scriptblock]::Create((irm 'https://test.chris94.com/install-scripts/Windows-Installer.ps1'))) -OriginalProfile `'$OriginalProfile`' -OriginalDesktop `'$OriginalDesktop`' -OriginalAppData `'$OriginalAppData`'"
        $Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $MemCommand)
    } else {
        # Pass the original user's directories into the elevated Admin session using the local file
        $Arguments = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$PSCommandPath`"",
            "-OriginalProfile", "`"$OriginalProfile`"",
            "-OriginalDesktop", "`"$OriginalDesktop`"",
            "-OriginalAppData", "`"$OriginalAppData`""
        )
    }
    
    Start-Process powershell.exe -ArgumentList $Arguments -Verb RunAs
    exit
}

# ---------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------
# Map to the original user's folders, NOT the Admin's folders
$script:TargetDir = "$OriginalProfile\Network-Testing-Tools\"
$script:RepoOwner = "Chrisb003"
$script:RepoName  = "Network-Testing-Tools"
$script:Branch    = "main"
$script:Token     = "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"

# Set to TEMP since in-memory scripts do not have a $PSScriptRoot
Set-Location $env:TEMP

# ---------------------------------------------------------
# 3. EXISTING INSTALLATION CHECK & UNINSTALL OPTION
# ---------------------------------------------------------
if (Test-Path "$script:TargetDir\app.py") {
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host "   NETWORK DIAGNOSTICS - WINDOWS INSTALLER & MANAGER" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[*] Existing installation detected at $script:TargetDir." -ForegroundColor Cyan
    $removeApp = Read-Host "[?] Do you want to REMOVE the existing installation completely (including database and logs)? (y/N)"
    if ($removeApp -eq 'y' -or $removeApp -eq 'Y') {
        $confirmWipe = Read-Host "[?] Are you ABSOLUTELY sure? Type 'yes' to confirm total deletion"
        if ($confirmWipe -eq 'yes') {
            Write-Host "[*] Removing Startup shortcut if present..." -ForegroundColor Gray
            Remove-Item "$OriginalAppData\Microsoft\Windows\Start Menu\Programs\Startup\Network Diagnostics.lnk" -ErrorAction SilentlyContinue
            Write-Host "[*] Removing Desktop shortcut if present..." -ForegroundColor Gray
            Remove-Item "$OriginalDesktop\Network Diagnostics.lnk" -ErrorAction SilentlyContinue
            Write-Host "[*] Removing Start Menu shortcut if present..." -ForegroundColor Gray
            Remove-Item "$OriginalAppData\Microsoft\Windows\Start Menu\Programs\Network Diagnostics.lnk" -ErrorAction SilentlyContinue
            
            Write-Host "[*] Deleting application directory..." -ForegroundColor Gray
            Remove-Item "$script:TargetDir" -Recurse -Force -ErrorAction SilentlyContinue
            
            Write-Host "[✓] Application completely removed." -ForegroundColor Green
            exit
        } else {
            Write-Host "[*] Deletion cancelled." -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------
# 4. WELCOME BANNER & INSTALL PROMPT
# ---------------------------------------------------------
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "   NETWORK DIAGNOSTICS - WINDOWS INSTALLER & MANAGER" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "This script installs, updates, or manages the Network" -ForegroundColor White
Write-Host "Diagnostics Dashboard, Python dependencies, and tools." -ForegroundColor White
Write-Host ""
$proceed = Read-Host "[?] Do you want to proceed with the installation process? (y/N)"
if ($proceed -ne 'y' -and $proceed -ne 'Y') {
    Write-Host "[*] Installation cancelled by user." -ForegroundColor Yellow
    exit
}

# ---------------------------------------------------------
# 5. CHECK FOR PYTHON (AUTO-DOWNLOAD FROM PYTHON.ORG)
# ---------------------------------------------------------
$pythonTest = Get-Command python -ErrorAction SilentlyContinue

# Verify it isn't the fake Windows Store shortcut
if ($pythonTest) {
    $testOutput = python --version 2>&1 | Out-String
    if ($testOutput -match "Python was not found") {
        $pythonTest = $null # Force the script to treat Python as missing
    }
}

if (-not $pythonTest) {
    Write-Host ""
    Write-Host "[!] Python was not found on this system." -ForegroundColor Red
    Write-Host "[*] Downloading official Python 3.14.7 installer from python.org..." -ForegroundColor Cyan
    
    $pyVersion = "3.14.7"
    $pyUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $installerPath = "$env:TEMP\python_installer.exe"
    
    Invoke-WebRequest -Uri $pyUrl -OutFile $installerPath
    
    Write-Host "[*] Installing Python silently (this may take a minute). Please wait..." -ForegroundColor Yellow
    $installArgs = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"
    Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait
    
    Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
    
    Write-Host "[✓] Python installation complete. Refreshing environment..." -ForegroundColor Green
    
    # Safely merge Machine and User PATH to prevent breaking built-in Windows commands like icacls
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    [Environment]::SetEnvironmentVariable("Path", "$machinePath;$userPath", "Process")
} else {
    Write-Host "[✓] Python is detected." -ForegroundColor Green
}

# Explicitly find the REAL python.exe to bypass PowerShell's cache of the Windows Store alias
$script:PythonCmd = (Get-Command python -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -ExpandProperty Source | Select-Object -First 1)

if (-not $script:PythonCmd) {
    Write-Host "[X] Valid Python executable not found. Please restart your computer and run this script again." -ForegroundColor Red
    pause
    exit
}

# Locate pythonw.exe for silent background execution
$script:PythonWCmd = (Get-Command pythonw -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -ExpandProperty Source | Select-Object -First 1)
if (-not $script:PythonWCmd) {
    $derivedW = $script:PythonCmd -replace "(?i)python\.exe$", "pythonw.exe"
    if (Test-Path $derivedW) { 
        $script:PythonWCmd = $derivedW 
    } else { 
        $script:PythonWCmd = $script:PythonCmd 
    }
}

# ---------------------------------------------------------
# 6. CHECK FOR INTERNET CONNECTIVITY
# ---------------------------------------------------------
Write-Host ""
Write-Host "[*] Checking for internet connectivity..." -ForegroundColor Cyan
$ping = Test-Connection -ComputerName 8.8.8.8 -Count 1 -Quiet -ErrorAction SilentlyContinue

if (-not $ping) {
    Write-Host ""
    Write-Host "[!] No Internet: Requirements skipped." -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "[✓] Internet detected. Installing system certificates..." -ForegroundColor Green
    & $script:PythonCmd -m pip install pip-system-certs | Out-Null
}

# ---------------------------------------------------------
# 7. DOWNLOAD OR UPDATE CODE FROM GITHUB
# ---------------------------------------------------------
Write-Host ""
Write-Host "[*] Managing application files..." -ForegroundColor Cyan
if (-not (Test-Path $script:TargetDir)) {
    New-Item -ItemType Directory -Path $script:TargetDir | Out-Null
}

if (-not (Test-Path "$script:TargetDir\app.py")) {
    Write-Host "[*] Downloading latest project files from GitHub..." -ForegroundColor Cyan
    $headers = @{
        'Authorization' = "token $script:Token"
        'Accept'        = 'application/vnd.github.v3+json'
    }
    $zipPath = "$env:TEMP\network_dashboard.zip"
    $extractPath = "$env:TEMP\network_dashboard_extract"

    Invoke-WebRequest -Uri "https://api.github.com/repos/$script:RepoOwner/$script:RepoName/zipball/$script:Branch" -Headers $headers -OutFile $zipPath
    
    Write-Host "[*] Extracting files into $script:TargetDir..." -ForegroundColor Cyan
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
    
    $extractedFolder = Get-ChildItem $extractPath | Select-Object -First 1
    Copy-Item "$($extractedFolder.FullName)\*" $script:TargetDir -Recurse -Force
    
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item $extractPath -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[✓] Files downloaded into $script:TargetDir." -ForegroundColor Green
} else {
    Write-Host "[✓] Code directory already exists. Skipping full re-download to preserve configs/database." -ForegroundColor Green
    $updateCode = Read-Host "[?] Do you want to pull/update latest code changes from GitHub repository? (y/N)"
    if ($updateCode -eq 'y' -or $updateCode -eq 'Y') {
        Write-Host "[*] Updating code from GitHub..." -ForegroundColor Cyan
        $headers = @{
            'Authorization' = "token $script:Token"
            'Accept'        = 'application/vnd.github.v3+json'
        }
        $zipPath = "$env:TEMP\network_dashboard.zip"
        $extractPath = "$env:TEMP\network_dashboard_extract"

        Invoke-WebRequest -Uri "https://api.github.com/repos/$script:RepoOwner/$script:RepoName/zipball/$script:Branch" -Headers $headers -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
        
        $extractedFolder = Get-ChildItem $extractPath | Select-Object -First 1
        
        Get-ChildItem "$($extractedFolder.FullName)" -Recurse | ForEach-Object {
            $relPath = $_.FullName.Substring($extractedFolder.FullName.Length + 1)
            if ($relPath -notin @('webport', 'standalone', 'disablecleanup')) {
                $destPath = Join-Path $script:TargetDir $relPath
                if ($_.PSIsContainer) {
                    if (-not (Test-Path $destPath)) { New-Item -ItemType Directory -Path $destPath | Out-Null }
                } else {
                    Copy-Item $_.FullName $destPath -Force
                }
            }
        }
        
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item $extractPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[✓] Code updated." -ForegroundColor Green
    }
}

# ---------------------------------------------------------
# 8. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS
# ---------------------------------------------------------
Write-Host ""
Write-Host "--------------------------------------------------------" -ForegroundColor Gray
$isDedicated = Read-Host "[?] Are you using this device as a dedicated test device? (y/N)"
if ($isDedicated -eq 'y' -or $isDedicated -eq 'Y') {
    Write-Host "    [*] Configuring for dedicated test device mode..." -ForegroundColor Cyan
    
    if (-not (Test-Path "$script:TargetDir\standalone")) {
        New-Item -ItemType File -Path "$script:TargetDir\standalone" | Out-Null
        Write-Host "        [+] Created 'standalone' file." -ForegroundColor Green
    }
    if (-not (Test-Path "$script:TargetDir\disablecleanup")) {
        New-Item -ItemType File -Path "$script:TargetDir\disablecleanup" | Out-Null
        Write-Host "        [+] Created 'disablecleanup' file." -ForegroundColor Green
    }
    if (-not (Test-Path "$script:TargetDir\webport")) {
        Set-Content -Path "$script:TargetDir\webport" -Value "80"
        Write-Host "        [+] Created 'webport' file set to 80." -ForegroundColor Green
    }
    
    Write-Host ""
    Write-Host "    [?] Windows Mobile Hotspot Configuration:" -ForegroundColor Cyan
    $toggleHotspot = Read-Host "    [?] Do you want to configure or toggle the Windows Wi-Fi Mobile Hotspot? (y/N)"
    if ($toggleHotspot -eq 'y' -or $toggleHotspot -eq 'Y') {
        Write-Host "        [*] Configuring Windows Mobile Hotspot via PowerShell..." -ForegroundColor Cyan
        try {
            Add-Type -AssemblyName System.Runtime.WindowsRuntime
            $asTask = ([System.Runtime.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 })[0]
            $connectionProfile = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]::GetInternetConnectionProfile()
            $tetheringManager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]::CreateForConnectionProfile($connectionProfile)
            
            if ($tetheringManager.TetheringOperationalState -eq 1) {
                Write-Host "        [✓] Mobile Hotspot is currently ENABLED." -ForegroundColor Green
                $ans = Read-Host "        Do you want to disable it? (y/N)"
                if ($ans -eq 'y' -or $ans -eq 'Y') {
                    $task = $tetheringManager.StopTetheringAsync()
                    $asTask.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]).Invoke($null, @($task)).GetAwaiter().GetResult() | Out-Null
                    Write-Host "        [✓] Mobile Hotspot disabled." -ForegroundColor Green
                }
            } else {
                Write-Host "        [?] Mobile Hotspot is currently DISABLED." -ForegroundColor Yellow
                $ans = Read-Host "        Do you want to enable/configure it? (y/N)"
                if ($ans -eq 'y' -or $ans -eq 'Y') {
                    $ssid = Read-Host "        Enter Hotspot SSID [Default: Network-Dashboard]"
                    $pass = Read-Host "        Enter Hotspot Password (min 8 chars) [Default: dashboard123]"
                    if (-not $ssid) { $ssid = "Network-Dashboard" }
                    if (-not $pass) { $pass = "dashboard123" }
                    
                    $config = $tetheringManager.GetCurrentConfiguration()
                    $config.Ssid = $ssid
                    $config.Passphrase = $pass
                    
                    $configTask = $tetheringManager.ConfigureAsync($config)
                    $asTask.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]).Invoke($null, @($configTask)).GetAwaiter().GetResult() | Out-Null
                    
                    $startTask = $tetheringManager.StartTetheringAsync()
                    $asTask.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]).Invoke($null, @($startTask)).GetAwaiter().GetResult() | Out-Null
                    Write-Host "        [✓] Mobile Hotspot successfully enabled!" -ForegroundColor Green
                }
            }
        } catch {
            Write-Host "        [!] Mobile Hotspot is not supported by this device's Wi-Fi adapter or requires modern Windows 10/11." -ForegroundColor Red
            Write-Host "        Tip: You can also manage Mobile Hotspot directly in Windows Settings (Network & internet -> Mobile hotspot)." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "    [*] Skipping dedicated test device configurations." -ForegroundColor Gray
}

# ---------------------------------------------------------
# 9. FIX DIRECTORY PERMISSIONS FOR ALL USERS
# ---------------------------------------------------------
Write-Host "    [*] Unlocking folder permissions for all users..." -ForegroundColor Cyan
icacls "$script:TargetDir" /grant "Everyone:(F)" /T /C /Q | Out-Null
Write-Host "--------------------------------------------------------" -ForegroundColor Gray

# ---------------------------------------------------------
# 10. OPTIONAL USER-LOGIN STARTUP (STARTUP FOLDER)
# ---------------------------------------------------------
Write-Host ""
Write-Host "--------------------------------------------------------" -ForegroundColor Gray

$icoPath = Join-Path $script:TargetDir 'static\favicon.ico'
$startupLnk = "$OriginalAppData\Microsoft\Windows\Start Menu\Programs\Startup\Network Diagnostics.lnk"

if (Test-Path $startupLnk) {
    Write-Host "[?] Background User-Login Startup is currently ENABLED." -ForegroundColor Yellow
    $toggleStartup = Read-Host "[?] Do you want to DISABLE/REMOVE the startup shortcut? (y/N)"
    if ($toggleStartup -eq 'y' -or $toggleStartup -eq 'Y') {
        Remove-Item $startupLnk -Force -ErrorAction SilentlyContinue
        Write-Host "    [✓] Startup shortcut removed." -ForegroundColor Green
    }
} else {
    Write-Host "[?] Background User-Login Startup is currently DISABLED." -ForegroundColor Yellow
    $toggleStartup = Read-Host "[?] Do you want to ENABLE automatic start on user login? (y/N)"
    if ($toggleStartup -eq 'y' -or $toggleStartup -eq 'Y') {
        
        Write-Host "    How should the dashboard start on login?"
        Write-Host "      1) Visible Terminal Window"
        Write-Host "      2) Invisible Background Process (Silent)"
        $startMode = Read-Host "    Select option (1 or 2)"

        Write-Host "    [*] Creating startup shortcut..." -ForegroundColor Cyan
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($startupLnk)
        
        if ($startMode -eq '2') {
            $sc.TargetPath = $script:PythonWCmd
            $modeMsg = "Invisible Background Process"
        } else {
            $sc.TargetPath = $script:PythonCmd
            $modeMsg = "Visible Terminal Window"
        }
        
        $sc.Arguments = "`"$script:TargetDir\setup_env.py`""
        $sc.WorkingDirectory = $script:TargetDir
        if (Test-Path $icoPath) { 
            $sc.IconLocation = "$icoPath,0" 
        }
        $sc.Save()
        Write-Host "    [✓] Startup on login enabled ($modeMsg)." -ForegroundColor Green
    }
}
Write-Host "--------------------------------------------------------" -ForegroundColor Gray

# ---------------------------------------------------------
# 11. DESKTOP SHORTCUT CREATION
# ---------------------------------------------------------
Write-Host ""
$createDesktop = Read-Host "[?] Do you want to create a Desktop shortcut? (y/N)"
if ($createDesktop -eq 'y' -or $createDesktop -eq 'Y') {
    Write-Host "    [*] Generating Desktop shortcut..." -ForegroundColor Cyan
    $lnkPath = Join-Path $OriginalDesktop 'Network Diagnostics.lnk'
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = $script:PythonCmd
    $sc.Arguments = "`"$script:TargetDir\setup_env.py`""
    $sc.WorkingDirectory = $script:TargetDir
    if (Test-Path $icoPath) { 
        $sc.IconLocation = "$icoPath,0" 
    }
    $sc.Save()
    Write-Host "[✓] Desktop shortcut created successfully." -ForegroundColor Green
}

# ---------------------------------------------------------
# 12. START MENU SHORTCUT CREATION
# ---------------------------------------------------------
Write-Host ""
$createStartMenu = Read-Host "[?] Do you want to create a Start Menu shortcut? (y/N)"
if ($createStartMenu -eq 'y' -or $createStartMenu -eq 'Y') {
    Write-Host "    [*] Generating Start Menu shortcut..." -ForegroundColor Cyan
    $startMenuPath = Join-Path $OriginalAppData 'Microsoft\Windows\Start Menu\Programs'
    $lnkPath = Join-Path $startMenuPath 'Network Diagnostics.lnk'
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = $script:PythonCmd
    $sc.Arguments = "`"$script:TargetDir\setup_env.py`""
    $sc.WorkingDirectory = $script:TargetDir
    if (Test-Path $icoPath) { 
        $sc.IconLocation = "$icoPath,0" 
    }
    $sc.Save()
    Write-Host "[✓] Start Menu shortcut created successfully." -ForegroundColor Green
}

# ---------------------------------------------------------
# 13. CHECK AND INSTALL NPCAP (IF MISSING)
# ---------------------------------------------------------
Write-Host ""
Write-Host "--------------------------------------------------------" -ForegroundColor Gray

$npcapInstalled = Test-Path "$env:SystemRoot\System32\Npcap"

if ($npcapInstalled) {
    Write-Host "[✓] Npcap is already detected on this system. Skipping installation." -ForegroundColor Green
} else {
    Write-Host "[!] Npcap is required for network packet capture features." -ForegroundColor Yellow
    Write-Host "    (The free version requires you to click through the installer manually)." -ForegroundColor Gray
    $installNpcap = Read-Host "[?] Do you want to download and install Npcap now? (y/N)"

    if ($installNpcap -eq 'y' -or $installNpcap -eq 'Y') {
        Write-Host "    [*] Downloading the latest Npcap installer..." -ForegroundColor Cyan
        
        $npcapUrl = "https://npcap.com/dist/npcap-1.88.exe"
        $npcapInstaller = "$env:TEMP\npcap_installer.exe"
        
        Invoke-WebRequest -Uri $npcapUrl -OutFile $npcapInstaller -UseBasicParsing
        
        Write-Host "    [*] Launching Npcap installer. Please complete the installation window that pops up." -ForegroundColor Yellow
        
        Start-Process -FilePath $npcapInstaller -Wait
        
        Remove-Item $npcapInstaller -Force -ErrorAction SilentlyContinue
        Write-Host "    [✓] Npcap installation step completed." -ForegroundColor Green
    } else {
        Write-Host "    [*] Skipping Npcap installation." -ForegroundColor Gray
    }
}
Write-Host "--------------------------------------------------------" -ForegroundColor Gray

# ---------------------------------------------------------
# 14. RUN THE SETUP SCRIPT FROM TARGET DIRECTORY
# ---------------------------------------------------------
Write-Host ""
Write-Host "[*] Launching Setup Script from $script:TargetDir..." -ForegroundColor Cyan
Set-Location $script:TargetDir
& $script:PythonCmd setup_env.py