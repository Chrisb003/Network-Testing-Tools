#!/bin/bash

echo "========================================================"
echo "   NETWORK DIAGNOSTICS - MACOS INSTALLER & MANAGER"
echo "========================================================"

# --- 1. CONFIGURATION ---
TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"
PLIST_NAME="com.network.diagnostics.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"

# Ensure we run as a standard user for paths
if [ "$EUID" -eq 0 ]; then
    echo "[!] Please do NOT run this script with 'sudo'. Run it as your normal user."
    exit 1
fi

# --- 2. AUTO-DIRECTORY DETECTION ---
cd "$(dirname "$0")" || exit
echo "[*] Working Directory: $(pwd)"

# --- 3. EXISTING INSTALLATION CHECK & UNINSTALL OPTION ---
if [ -d "$TARGET_DIR" ]; then
    echo ""
    echo "[*] Existing installation detected at $TARGET_DIR."
    read -p "[?] Do you want to REMOVE the existing installation completely (including database and logs)? (y/N): " remove_app
    if [[ "$remove_app" =~ ^[Yy]$ ]]; then
        read -p "[?] Are you ABSOLUTELY sure? Type 'yes' to confirm total deletion: " confirm_wipe
        if [ "$confirm_wipe" == "yes" ]; then
            echo "[*] Stopping macOS background agent if active..."
            launchctl unload "$PLIST_PATH" &>/dev/null
            rm -f "$PLIST_PATH"
            
            echo "[*] Deleting application directory..."
            rm -rf "$TARGET_DIR"
            
            # Remove applications folder bundle if present
            rm -rf "$HOME/Applications/Network Diagnostics.app"
            
            echo "[✓] Application completely removed."
            exit 0
        else
            echo "[*] Deletion cancelled."
        fi
    fi
fi

# --- 4. MACOS PREREQUISITES (Python 3 & Git Check) ---
echo ""
echo "[*] Step 1: Checking macOS system prerequisites..."

if ! command -v python3 &> /dev/null; then
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

if ! command -v git &> /dev/null; then
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
    read -p "[?] Do you want to pull/update latest code changes from GitHub repository? (y/N): " update_code
    if [[ "$update_code" =~ ^[Yy]$ ]]; then
        curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github.v3+json" -L "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/zipball/$BRANCH" -o /tmp/network_dashboard.zip
        mkdir -p /tmp/network_dashboard_extract
        unzip -q /tmp/network_dashboard.zip -d /tmp/network_dashboard_extract
        EXTRACTED_FOLDER=$(ls -d /tmp/network_dashboard_extract/*/)
        
        # Move code files over without overwriting database/logs/configs
        if command -v rsync &> /dev/null; then
            rsync -av --ignore-existing --exclude="standalone" --exclude="disablecleanup" "$EXTRACTED_FOLDER/" "$TARGET_DIR/" 2>/dev/null
        else
            cp -rn "$EXTRACTED_FOLDER/"* "$TARGET_DIR/" 2>/dev/null
        fi
        
        rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract
        echo "[✓] Code updated."
    fi
fi

# --- 6. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS ---
echo ""
echo "--------------------------------------------------------"
read -p "[?] Are you using this device as a dedicated test device? (y/N): " is_dedicated
if [[ "$is_dedicated" =~ ^[Yy]$ ]]; then
    echo "    [*] Configuring for dedicated test device mode..."
    mkdir -p "$TARGET_DIR"

    # standalone file
    if [ ! -f "$TARGET_DIR/standalone" ]; then
        touch "$TARGET_DIR/standalone"
        echo "        [+] Created 'standalone' file."
    fi

    # disablecleanup file
    if [ ! -f "$TARGET_DIR/disablecleanup" ]; then
        touch "$TARGET_DIR/disablecleanup"
        echo "        [+] Created 'disablecleanup' file."
    fi
else
    echo "    [*] Skipping dedicated test device configurations."
fi
echo "--------------------------------------------------------"

# Fix ownership so current user owns everything in TARGET_DIR
chown -R "$USER" "$TARGET_DIR"

# --- 7. OPTIONAL MACOS LAUNCHAGENT (BACKGROUND SERVICE) ---
echo ""
echo "--------------------------------------------------------"
SERVICE_ACTIVE=false

if [ -f "$PLIST_PATH" ]; then
    SERVICE_ACTIVE=true
    echo "[?] macOS Background Boot Agent is currently ENABLED."
    read -p "[?] Do you want to DISABLE/REMOVE the background service? (y/N): " toggle_service
    if [[ "$toggle_service" =~ ^[Yy]$ ]]; then
        launchctl unload "$PLIST_PATH" &>/dev/null
        rm -f "$PLIST_PATH"
        echo "    [✓] Background boot service disabled and removed."
        SERVICE_ACTIVE=false
    fi
else
    echo "[?] macOS Background Boot Agent is currently DISABLED."
    read -p "[?] Do you want to ENABLE automatic start on login and run in the background? (y/N): " toggle_service
    if [[ "$toggle_service" =~ ^[Yy]$ ]]; then
        echo "    [*] Setting up launchd agent..."
        mkdir -p "$HOME/Library/LaunchAgents"
        cat <<EOL > "$PLIST_PATH"
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
    <true/>
</dict>
</plist>
EOL
        launchctl load "$PLIST_PATH" &>/dev/null
        echo "    [✓] Background boot agent enabled and started."
        SERVICE_ACTIVE=true
    fi
fi
echo "--------------------------------------------------------"

# --- 8. APPLICATIONS FOLDER SHORTCUT (.APP BUNDLE) CREATION ---
echo ""
read -p "[?] Do you want to create an application shortcut in your Applications folder? (y/N): " create_app_shortcut
if [[ "$create_app_shortcut" =~ ^[Yy]$ ]]; then
    APP_DIR="$HOME/Applications"
    mkdir -p "$APP_DIR"
    APP_PATH="$APP_DIR/Network Diagnostics.app"
    
    echo "    [*] Building macOS Application Bundle with custom icon..."
    rm -rf "$APP_PATH"
    mkdir -p "$APP_PATH/Contents/MacOS"
    mkdir -p "$APP_PATH/Contents/Resources"
    
    # Create executable launcher wrapper inside the bundle
    cat << 'EOF' > "$APP_PATH/Contents/MacOS/launcher"
#!/bin/bash
cd "$HOME/Network-Testing-Tools"
python3 setup_env.py
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

    # Convert static/Logo.png into Apple's .icns format if available
    if [ -f "$TARGET_DIR/static/Logo.png" ]; then
        ICONSET_DIR="/tmp/icon.iconset"
        mkdir -p "$ICONSET_DIR"
        sips -z 16 16     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_16x16.png" &>/dev/null
        sips -z 32 32     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_16x16@2x.png" &>/dev/null
        sips -z 32 32     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_32x32.png" &>/dev/null
        sips -z 64 64     "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_32x32@2x.png" &>/dev/null
        sips -z 128 128   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_128x128.png" &>/dev/null
        sips -z 256 256   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_128x128@2x.png" &>/dev/null
        sips -z 256 256   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_256x256.png" &>/dev/null
        sips -z 512 512   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_256x256@2x.png" &>/dev/null
        sips -z 512 512   "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_512x512.png" &>/dev/null
        sips -z 1024 1024 "$TARGET_DIR/static/Logo.png" --out "$ICONSET_DIR/icon_512x512@2x.png" &>/dev/null
        iconutil -c icns "$ICONSET_DIR" -o "$APP_PATH/Contents/Resources/AppIcon.icns" &>/dev/null
        rm -rf "$ICONSET_DIR"
    fi
    
    echo "[✓] Applications bundle created successfully at $APP_PATH."
fi

# --- 9. FINAL SUMMARY & IP INFO ---
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
PORT="81" # Default fallback port if config is absent

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
    python3 "$TARGET_DIR/setup_env.py"
fi