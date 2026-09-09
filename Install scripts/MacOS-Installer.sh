#!/bin/sh

# Ensure we run as a standard user for paths
if [ "$EUID" -eq 0 ]; then
    echo "[!] Please do NOT run this script with 'sudo'. Run it as your normal user."
    exit 1
fi

# --- 1. AUTO-DIRECTORY DETECTION & CONFIGURATION ---
cd "$(dirname "$0")" || exit

TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"
APP_DIR="$HOME/Applications"
APP_PATH="$APP_DIR/Network Diagnostics.app"
PLIST_PATH="$HOME/Library/LaunchAgents/com.network.diagnostics.plist"

# --- 2. EXISTING INSTALLATION CHECK & UNINSTALL OPTION ---
if [ -d "$TARGET_DIR" ]; then
    echo "========================================================"
    echo "   NETWORK DIAGNOSTICS - MACOS INSTALLER & MANAGER"
    echo "========================================================"
    echo ""
    echo "[*] Existing installation detected at $TARGET_DIR."
    printf "[?] Do you want to REMOVE the existing installation completely (including database and logs)? (y/N): "
    read remove_app < /dev/tty
    
    case "$remove_app" in
        [Yy]* )
            printf "[?] Are you ABSOLUTELY sure? Type 'yes' to confirm total deletion: "
            read confirm_wipe < /dev/tty
            if [ "$confirm_wipe" = "yes" ]; then
                
                # Cleanup old LaunchAgent if present
                if [ -f "$PLIST_PATH" ]; then
                    echo "[*] Stopping macOS background agent..."
                    launchctl unload "$PLIST_PATH" >/dev/null 2>&1
                    rm -f "$PLIST_PATH"
                fi
                
                # Cleanup new Login Item if present
                osascript -e 'tell application "System Events" to delete login item "Network Diagnostics"' >/dev/null 2>&1
                
                echo "[*] Deleting application directory..."
                sudo rm -rf "$TARGET_DIR"
                
                echo "[*] Removing applications folder bundle if present..."
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
echo "========================================================"
echo "This script installs, updates, or manages the Network"
echo "Diagnostics Dashboard, Python dependencies, and tools."
echo ""
printf "[?] Do you want to proceed with the installation process? (y/N): "
read proceed < /dev/tty
case "$proceed" in
    [Yy]* ) ;;
    * ) echo "[*] Installation cancelled by user."; exit 0 ;;
esac

# --- 4. MACOS PREREQUISITES (Python 3 & Git Check) ---
echo ""
echo "[*] Step 1: Checking macOS system prerequisites..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] Python 3 not found. Downloading and installing official Python.org package for macOS..."
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
    echo "[✓] Python 3 is already installed."
fi

if ! command -v git >/dev/null 2>&1; then
    echo "[*] Git not found. Installing via Xcode Command Line Tools..."
    xcode-select --install
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
                rsync -av --ignore-existing --exclude="standalone" --exclude="disablecleanup" "$EXTRACTED_FOLDER/" "$TARGET_DIR/" >/dev/null 2>&1
            else
                cp -rn "$EXTRACTED_FOLDER/"* "$TARGET_DIR/" >/dev/null 2>&1
            fi
            
            rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract
            echo "[✓] Code updated."
            ;;
    esac
fi

# --- 6. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS ---
echo ""
echo "--------------------------------------------------------"
printf "[?] Are you using this device as a dedicated test device? (y/N): "
read is_dedicated < /dev/tty
case "$is_dedicated" in
    [Yy]* )
        echo "    [*] Configuring for dedicated test device mode..."
        mkdir -p "$TARGET_DIR"

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

chown -R "$USER" "$TARGET_DIR" 2>/dev/null || sudo chown -R "$USER" "$TARGET_DIR"
sudo chmod -R 777 "$TARGET_DIR"

# Ensure APP bundle is created, required for macOS Login Items to work properly
FORCE_APP_CREATION=false

# --- 7. MACOS USER LOGIN AUTOSTART ---
echo ""
echo "--------------------------------------------------------"

# Cleanup old invisible LaunchAgent if present from older script versions
if [ -f "$PLIST_PATH" ]; then
    echo "[*] Cleaning up legacy invisible background agent..."
    launchctl unload "$PLIST_PATH" >/dev/null 2>&1
    rm -f "$PLIST_PATH"
fi

LOGIN_ITEM_CHECK=$(osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null)

if echo "$LOGIN_ITEM_CHECK" | grep -q "Network Diagnostics"; then
    echo "[?] User-Login Startup is currently ENABLED."
    printf "[?] Do you want to DISABLE/REMOVE the startup shortcut? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            osascript -e 'tell application "System Events" to delete login item "Network Diagnostics"' >/dev/null 2>&1
            echo "    [✓] Startup shortcut removed."
            ;;
    esac
else
    echo "[?] User-Login Startup is currently DISABLED."
    printf "[?] Do you want to ENABLE automatic start on user login? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            echo "    [*] Flagging system for macOS Login Item setup..."
            FORCE_APP_CREATION=true
            ;;
    esac
fi
echo "--------------------------------------------------------"

# --- 8. APPLICATIONS FOLDER SHORTCUT (.APP BUNDLE) CREATION ---
echo ""
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
        
        # Finally, assign the Login Item if requested earlier
        if [ "$FORCE_APP_CREATION" = true ]; then
            osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP_PATH\", hidden:false}" >/dev/null 2>&1
            echo "    [✓] Startup on login securely assigned to Application Bundle."
        fi
        ;;
esac

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
echo "   Starting dashboard via setup script..."
echo ""

if [ -n "$LOCAL_IP" ]; then
    echo "   💻 ACCESS THE DASHBOARD:"
    echo "      Open your browser to: http://$LOCAL_IP:$PORT"
    echo "      (Or locally at:       http://127.0.0.1:$PORT)"
    echo ""
fi
echo "========================================================"

# --- 10. HANDOFF TO SETUP PYTHON SCRIPT ---
python3 "$TARGET_DIR/setup_env.py"