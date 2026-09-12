#!/bin/sh

# Ensure we run as a standard user for paths
if [ "$(id -u)" -eq 0 ]; then
    echo "[!] Please do NOT run this script with 'sudo'. Run it as your normal user."
    echo "    The script will prompt for sudo credentials only when necessary."
    exit 1
fi

# --- 1. CONFIGURATION ---
cd "$(dirname "$0")" || exit

TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"
APP_DIR="$HOME/Applications"
APP_PATH="$APP_DIR/Network Diagnostics.app"
DAEMON_PLIST="/Library/LaunchDaemons/com.network.diagnostics.plist"
SCRIPT_VERSION="1.0.2"

# --- 2. EXISTING INSTALLATION CHECK & UNINSTALL OPTION ---
if [ -d "$TARGET_DIR" ]; then
    echo "========================================================"
    echo "   NETWORK DIAGNOSTICS - MACOS INSTALLER & MANAGER"
    echo "   Installer Version: $SCRIPT_VERSION"
    echo "========================================================"
    echo ""
    echo "[*] Existing installation detected at $TARGET_DIR."
    printf "[?] Do you want to REMOVE the existing installation? (y/N): "
    read remove_app < /dev/tty
    
    case "$remove_app" in
        [Yy]* )
            printf "[?] Do you want to KEEP your database files? (y/N): "
            read keep_db < /dev/tty
            
            printf "[?] Are you ABSOLUTELY sure you want to uninstall? Type 'yes' to confirm: "
            read confirm_wipe < /dev/tty
            
            if [ "$confirm_wipe" = "yes" ]; then
                
                # Cleanup background LaunchDaemon if it existed
                if [ -f "$DAEMON_PLIST" ]; then
                    echo "[*] Stopping system service..."
                    sudo launchctl unload "$DAEMON_PLIST" >/dev/null 2>&1
                    sudo rm -f "$DAEMON_PLIST"
                fi
                
                # Cleanup Login Item if it existed
                osascript -e 'tell application "System Events" to delete login item "Network Diagnostics"' >/dev/null 2>&1
                
                # --- BACKUP LOGIC (DATABASE ONLY) ---
                case "$keep_db" in
                    [Yy]* )
                        BACKUP_DIR="$HOME/Desktop/Network-Diagnostics-Backup"
                        echo "[*] Backing up database files to $BACKUP_DIR..."
                        mkdir -p "$BACKUP_DIR"
                        # Use sudo to copy in case the files are currently owned by root (LaunchDaemon)
                        sudo find "$TARGET_DIR" -type f \( -name "*.db" -o -name "*.sqlite" \) -exec cp {} "$BACKUP_DIR/" \;
                        # Ensure the user has full permissions to edit or delete the backed-up files
                        sudo chown -R "$USER" "$BACKUP_DIR"
                        sudo chmod -R 777 "$BACKUP_DIR"
                        echo "[+] Data backed up safely."
                        ;;
                esac
                
                echo "[*] Deleting application directory..."
                sudo rm -rf "$TARGET_DIR"
                
                echo "[*] Removing application bundle..."
                rm -rf "$APP_PATH"
                
                echo "[✓] Application completely removed."
                exit 0
            else
                echo "[*] Deletion cancelled."
            fi
            ;;
    esac
fi

# --- 3. WELCOME BANNER & INSTALL PROMPT ---
echo "========================================================"
echo "   NETWORK DIAGNOSTICS - MACOS INSTALLER & MANAGER"
echo "   Installer Version: $SCRIPT_VERSION"
echo "========================================================"
echo "This script installs, updates, or manages the Network"
echo "Diagnostics Dashboard, Python dependencies, and tools."
echo ""
printf "[?] Do you want to proceed with the installation of system prerequisites? (y/N): "
read proceed < /dev/tty

SKIP_PREREQS=false
case "$proceed" in
    [Yy]* ) ;;
    * ) 
        echo "[*] Skipping system prerequisites. Moving to application updates and configuration..."
        SKIP_PREREQS=true 
        ;;
esac

# --- 4. MACOS PREREQUISITES (Python 3 Check) ---
if [ "$SKIP_PREREQS" = false ]; then
    echo ""
    echo "[*] Step 1: Checking macOS system prerequisites..."

    PYTHON_VALID=false
    
    # Check if python3 exists, but safely avoid Apple's fake stub trap
    if command -v python3 >/dev/null 2>&1; then
        PY_PATH=$(command -v python3)
        
        # If it points to the Apple stub AND developer tools are missing, executing it will trigger a popup
        if [ "$PY_PATH" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
            echo "[*] Apple Python stub detected. Bypassing to avoid Xcode popup..."
        else
            # It is safe to execute and verify
            if python3 --version >/dev/null 2>&1; then
                PYTHON_VALID=true
            fi
        fi
    fi

    if [ "$PYTHON_VALID" = false ]; then
        echo "[!] Valid Python 3 not found. Downloading official Python.org package..."
        PY_VERSION="3.14.7"
        PKG_NAME="python-${PY_VERSION}-macos11.pkg"
        PKG_URL="https://www.python.org/ftp/python/${PY_VERSION}/${PKG_NAME}"
        
        echo "[*] Downloading Python ${PY_VERSION}..."
        curl -O "$PKG_URL"
        
        echo "[*] Installing Python package (administrator password required)..."
        sudo installer -pkg "$PKG_NAME" -target /
        rm -f "$PKG_NAME"
        echo "[✓] Python installation completed."
    else
        echo "[✓] Python 3 is verified and working."
    fi
fi

# --- 5. DOWNLOAD OR UPDATE CODE ---
echo ""
echo "[*] Step 2: Managing application files..."
if [ ! -d "$TARGET_DIR" ]; then
    echo "[*] Downloading latest project files from GitHub..."
    curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github.v3+json" -L "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/zipball/$BRANCH" -o /tmp/network_dashboard.zip
    
    mkdir -p /tmp/network_dashboard_extract
    unzip -q /tmp/network_dashboard.zip -d /tmp/network_dashboard_extract
    EXTRACTED_FOLDER=$(ls -d /tmp/network_dashboard_extract/*/)
    mv "$EXTRACTED_FOLDER" "$TARGET_DIR"
    rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract
    echo "[✓] Files downloaded into $TARGET_DIR."
else
    echo "[✓] Code directory already exists. Skipping full re-download to preserve existing configs/database."
    printf "[?] Do you want to pull/update latest code changes from GitHub repository? (y/N): "
    read update_code < /dev/tty
    case "$update_code" in
        [Yy]* )
            curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github.v3+json" -L "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/zipball/$BRANCH" -o /tmp/network_dashboard.zip
            mkdir -p /tmp/network_dashboard_extract
            unzip -q /tmp/network_dashboard.zip -d /tmp/network_dashboard_extract
            EXTRACTED_FOLDER=$(ls -d /tmp/network_dashboard_extract/*/)
            
            # Move code files over without overwriting database/logs/configs
            if command -v rsync >/dev/null 2>&1; then
                rsync -av --ignore-existing --exclude="webport" --exclude="standalone" --exclude="disablecleanup" "$EXTRACTED_FOLDER/" "$TARGET_DIR/" >/dev/null 2>&1
            else
                cp -rn "$EXTRACTED_FOLDER/"* "$TARGET_DIR/" >/dev/null 2>&1
            fi
            
            rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract
            echo "[✓] Code updated."
            ;;
    esac
fi

# --- 6. APP CONFIGURATION & DEDICATED DEVICE ---
echo ""
echo "--------------------------------------------------------"

# 6a. Web Port Prompt
CURRENT_PORT="81"
if [ -f "$TARGET_DIR/webport" ]; then
    CURRENT_PORT=$(cat "$TARGET_DIR/webport" 2>/dev/null)
fi

printf "[?] Enter the port for the Web Dashboard [Default: %s]: " "$CURRENT_PORT"
read user_port < /dev/tty
user_port=${user_port:-$CURRENT_PORT}

# Validate that the user entered numbers only
if ! echo "$user_port" | grep -Eq '^[0-9]+$'; then
    echo "    [!] Invalid port format. Reverting to $CURRENT_PORT."
    user_port=$CURRENT_PORT
fi

mkdir -p "$TARGET_DIR"
echo "$user_port" > "$TARGET_DIR/webport"
echo "    [✓] Web port configured to $user_port."
echo ""

# 6b. Dedicated Device Prompt
printf "[?] Are you using this device as a dedicated test device? (y/N): "
read is_dedicated < /dev/tty
case "$is_dedicated" in
    [Yy]* )
        echo "    [*] Configuring for dedicated test device mode..."

        if [ ! -f "$TARGET_DIR/standalone" ]; then
            touch "$TARGET_DIR/standalone"
            echo "        [+] Created 'standalone' file."
        fi

        if [ ! -f "$TARGET_DIR/disablecleanup" ]; then
            touch "$TARGET_DIR/disablecleanup"
            echo "        [+] Created 'disablecleanup' file."
        fi
        ;;
    * ) echo "    [*] Skipping dedicated test device configurations." ;;
esac
echo "--------------------------------------------------------"

chown -R "$USER" "$TARGET_DIR" >/dev/null 2>&1 || sudo chown -R "$USER" "$TARGET_DIR"
sudo chmod -R 777 "$TARGET_DIR"

FORCE_APP_CREATION=false
SERVICE_ACTIVE=false
WANTS_LOGIN_ITEM=false

# --- 7. MACOS AUTOSTART CONFIGURATION ---
echo ""
echo "--------------------------------------------------------"

LOGIN_ITEM_CHECK=$(osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null)

if echo "$LOGIN_ITEM_CHECK" | grep -q "Network Diagnostics" || [ -f "$DAEMON_PLIST" ]; then
    echo "[?] Autostart (Desktop or Background Service) is currently ENABLED."
    printf "[?] Do you want to DISABLE/REMOVE the startup behavior? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            osascript -e 'tell application "System Events" to delete login item "Network Diagnostics"' >/dev/null 2>&1
            if [ -f "$DAEMON_PLIST" ]; then
                sudo launchctl unload "$DAEMON_PLIST" >/dev/null 2>&1
                sudo rm -f "$DAEMON_PLIST"
            fi
            echo "    [✓] All startup configurations removed."
            ;;
    esac
else
    echo "[?] Autostart is currently DISABLED."
    printf "[?] Do you want to ENABLE automatic start on boot/login? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            echo "    How should the dashboard start?"
            echo "      1) Visible Terminal Window (Standard User - Prompts for sudo)"
            echo "      2) Invisible Background Service (Runs silently as ROOT via LaunchDaemon)"
            printf "    Select option (1 or 2): "
            read start_mode < /dev/tty
            
            case "$start_mode" in
                1)
                    if [ -f "$DAEMON_PLIST" ]; then
                        sudo launchctl unload "$DAEMON_PLIST" >/dev/null 2>&1
                        sudo rm -f "$DAEMON_PLIST"
                    fi
                    echo "    [*] Flagging system for macOS Login Item setup..."
                    WANTS_LOGIN_ITEM=true
                    FORCE_APP_CREATION=true
                    ;;
                2)
                    osascript -e 'tell application "System Events" to delete login item "Network Diagnostics"' >/dev/null 2>&1
                    echo "    [*] Setting up LaunchDaemon background service (runs as root)..."
                    
                    sudo bash -c "cat > \"$DAEMON_PLIST\"" <<EOL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.network.diagnostics</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$TARGET_DIR/setup_env.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$TARGET_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
</dict>
</plist>
EOL
                    sudo chown root:wheel "$DAEMON_PLIST"
                    sudo chmod 644 "$DAEMON_PLIST"
                    sudo launchctl load "$DAEMON_PLIST" >/dev/null 2>&1
                    
                    # --- NEW: Disable browser autostart for headless mode ---
                    echo "0" > "$TARGET_DIR/autostart"
                    
                    echo "    [✓] Background boot service enabled and started as Root."
                    SERVICE_ACTIVE=true
                    ;;
                *)
                    echo "    [!] Invalid option. Skipping autostart configuration."
                    ;;
            esac
            ;;
    esac
fi
echo "--------------------------------------------------------"

# --- 8. APPLICATIONS FOLDER SHORTCUT (.APP BUNDLE) CREATION ---
echo ""
if [ -d "$APP_PATH" ]; then
    echo "[✓] Application shortcut already exists in your Applications folder."
else
    if [ "$FORCE_APP_CREATION" = true ]; then
        echo "[*] Application bundle required for Login Startup mechanism."
        create_app_shortcut="y"
    else
        printf "[?] Do you want to create an application shortcut in your Applications folder? (y/N): "
        read create_app_shortcut < /dev/tty
    fi

    case "$create_app_shortcut" in
        [Yy]* )
            mkdir -p "$APP_DIR"
            echo "    [*] Building macOS Application Bundle with visible terminal window..."
            rm -rf "$APP_PATH"
            mkdir -p "$APP_PATH/Contents/MacOS"
            mkdir -p "$APP_PATH/Contents/Resources"
            
            # Create executable launcher wrapper inside the bundle that natively commands Terminal to open
            cat << EOF > "$APP_PATH/Contents/MacOS/launcher"
#!/bin/bash
osascript -e 'tell application "Terminal" to do script "cd \\"$TARGET_DIR\\" && /usr/bin/env python3 \\"setup_env.py\\""'
EOF
            chmod +x "$APP_PATH/Contents/MacOS/launcher"
            
            # Create Info.plist metadata file
            cat << 'EOF' > "$APP_PATH/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.network.diagnostics</string>
    <key>CFBundleName</key>
    <string>Network Diagnostics</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
EOF

            # Convert favicon.ico or Logo.png into Apple's .icns format
            if [ -f "$TARGET_DIR/static/favicon.ico" ]; then
                sips -s format icns "$TARGET_DIR/static/favicon.ico" --out "$APP_PATH/Contents/Resources/AppIcon.icns" >/dev/null 2>&1
            fi
            
            if [ ! -f "$APP_PATH/Contents/Resources/AppIcon.icns" ] && [ -f "$TARGET_DIR/static/Logo.png" ]; then
                ICONSET_DIR="/tmp/icon.iconset"
                mkdir -p "$ICONSET_DIR"
                sips -z 16 16     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null 2>&1
                sips -z 32 32     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null 2>&1
                sips -z 32 32     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null 2>&1
                sips -z 64 64     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null 2>&1
                sips -z 128 128   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null 2>&1
                sips -z 256 256   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null 2>&1
                sips -z 256 256   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null 2>&1
                sips -z 512 512   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null 2>&1
                sips -z 512 512   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null 2>&1
                sips -z 1024 1024 "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null 2>&1
                iconutil -c icns "$ICONSET_DIR" -o "$APP_PATH/Contents/Resources/AppIcon.icns" >/dev/null 2>&1
                rm -rf "$ICONSET_DIR"
            fi
            
            echo "[✓] Applications bundle created successfully at $APP_PATH."
            ;;
    esac
fi

# Finally, assign the Login Item if requested earlier, ensuring it applies even if the bundle already existed
if [ "$WANTS_LOGIN_ITEM" = true ] && [ -d "$APP_PATH" ]; then
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP_PATH\", hidden:false}" >/dev/null 2>&1
    echo "    [✓] Startup on login securely assigned to Application Bundle."
fi

# --- 9. FINAL SUMMARY & IP INFO ---
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
PORT=$(cat "$TARGET_DIR/webport" 2>/dev/null || echo "81")

echo ""
echo "========================================================"
echo "   SETUP COMPLETE!"
echo "========================================================"

if [ "$SERVICE_ACTIVE" = false ]; then
    echo "   Starting dashboard via setup script..."
    echo ""
fi

if [ -n "$LOCAL_IP" ]; then
    echo "   💻 ACCESS THE DASHBOARD:"
    echo "      Open your browser to: http://$LOCAL_IP:$PORT"
    echo "      (Or locally at:       http://127.0.0.1:$PORT)"
    echo ""
fi
echo "========================================================"

# --- 10. HANDOFF TO SETUP PYTHON SCRIPT ---
if [ "$SERVICE_ACTIVE" = false ]; then
    echo "   [*] Checking for running instances..."
    
    # pgrep -f works natively on macOS to check the command line arguments
    if pgrep -f "setup_env.py" > /dev/null; then
        echo "   [✓] Network Diagnostics is already running. Skipping launch."
    else
        cd "$TARGET_DIR" || exit
        python3 setup_env.py
    fi
fi