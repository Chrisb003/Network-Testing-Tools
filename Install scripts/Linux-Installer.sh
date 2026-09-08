#!/bin/bash

echo "========================================================"
echo "   NETWORK DIAGNOSTICS - LINUX INSTALLER & MANAGER"
echo "========================================================"

# --- 1. CONFIGURATION ---
TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"
SERVICE_NAME="network-dashboard.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

# Ensure we run as a standard user for paths, but keep sudo available for apt/systemd
if [ "$EUID" -eq 0 ]; then
    echo "[!] Please do NOT run this script directly with 'sudo'. Run it as your normal user."
    echo "    The script will prompt for sudo credentials only when necessary."
    exit 1
fi

# --- 2. EXISTING INSTALLATION CHECK & UNINSTALL OPTION ---
if [ -d "$TARGET_DIR" ]; then
    echo ""
    echo "[*] Existing installation detected at $TARGET_DIR."
    read -p "[?] Do you want to REMOVE the existing installation completely (including database and logs)? (y/N): " remove_app
    if [[ "$remove_app" =~ ^[Yy]$ ]]; then
        read -p "[?] Are you ABSOLUTELY sure? Type 'yes' to confirm total deletion: " confirm_wipe
        if [ "$confirm_wipe" == "yes" ]; then
            echo "[*] Stopping system service if active..."
            sudo systemctl stop network-dashboard.service &>/dev/null
            sudo systemctl disable network-dashboard.service &>/dev/null
            sudo rm -f /etc/systemd/system/network-dashboard.service
            sudo systemctl daemon-reload
            
            echo "[*] Deleting application directory..."
            rm -rf "$TARGET_DIR"
            
            # Remove desktop shortcut if present
            rm -f "$HOME/Desktop/Network-Diagnostics.desktop"
            
            echo "[✓] Application completely removed."
            exit 0
        else
            echo "[*] Deletion cancelled."
        fi
    fi
fi

# --- 3. PREREQUISITES (Multi-Distro Support) ---
echo ""
echo "[*] Step 1: Installing system prerequisites (sudo password may be required)..."

if command -v apt-get &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools libpcap-dev unzip curl network-manager
elif command -v dnf &> /dev/null; then
    sudo dnf install -y python3 python3-pip python3-devel gcc git net-tools libpcap-devel unzip curl NetworkManager
elif command -v pacman &> /dev/null; then
    sudo pacman -Syu --noconfirm python python-pip base-devel git net-tools libpcap unzip curl networkmanager
elif command -v zypper &> /dev/null; then
    sudo zypper refresh
    sudo zypper install -y python3 python3-pip python3-devel gcc git net-tools libpcap-devel unzip curl NetworkManager
else
    echo "[!] Warning: Unknown package manager. Please ensure Python 3, venv, pip, git, libpcap, and curl are installed manually."
fi

# --- 4. DOWNLOAD OR UPDATE CODE ---
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
            rsync -av --ignore-existing --exclude="webport" --exclude="standalone" --exclude="disablecleanup" "$EXTRACTED_FOLDER/" "$TARGET_DIR/" 2>/dev/null
        else
            cp -rn "$EXTRACTED_FOLDER/"* "$TARGET_DIR/" 2>/dev/null
        fi
        
        rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract
        echo "[✓] Code updated."
    fi
fi

# --- 5. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS ---
echo ""
echo "--------------------------------------------------------"
read -p "[?] Are you using this device as a dedicated test device? (y/N): " is_dedicated
HOTSPOT_ACTIVE=false

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

    # webport file (default 80)
    if [ ! -f "$TARGET_DIR/webport" ]; then
        echo "80" > "$TARGET_DIR/webport"
        echo "        [+] Created 'webport' file set to 80."
    fi

    # --- OPTIONAL WI-FI HOTSPOT SETUP (Only for dedicated devices) ---
    echo ""
    echo "    [?] Wi-Fi Hotspot Configuration:"
    
    # Automatically detect the wireless interface name across different Linux utilities
    WIFI_IFACE=""
    if command -v iw &> /dev/null; then
        WIFI_IFACE=$(iw dev | awk '$1=="Interface"{print $2}' | head -n 1)
    elif command -v ip &> /dev/null; then
        WIFI_IFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep '^wl' | head -n 1)
    fi

    if [ -z "$WIFI_IFACE" ]; then
        WIFI_IFACE="wlan0"
    fi

    # Check if a hotspot is currently configured
    if nmcli connection show "Hotspot" &>/dev/null; then
        read -p "    [?] A Wi-Fi Hotspot is currently ENABLED. Do you want to DISABLE it? (y/N): " toggle_hotspot
        if [[ "$toggle_hotspot" =~ ^[Yy]$ ]]; then
            sudo nmcli connection delete Hotspot &>/dev/null
            echo "        [✓] Hotspot successfully disabled and removed."
        else
            HOTSPOT_ACTIVE=true
        fi
    else
        read -p "    [?] Do you want to ENABLE a Wi-Fi Hotspot to access the dashboard? (y/N): " toggle_hotspot
        if [[ "$toggle_hotspot" =~ ^[Yy]$ ]]; then
            
            # Generate default SSID using the last 6 characters of the MAC address
            MAC_ADDR=$(cat /sys/class/net/$WIFI_IFACE/address 2>/dev/null | tr -d ':')
            if [ -n "$MAC_ADDR" ]; then
                MAC_SUFFIX=$(echo "${MAC_ADDR: -6}" | tr 'a-z' 'A-Z')
            else
                MAC_SUFFIX=$RANDOM
            fi
            DEFAULT_SSID="Network-Dashboard-$MAC_SUFFIX"

            # 1. Ask for Custom SSID
            read -p "        Enter Hotspot SSID [Default: $DEFAULT_SSID]: " HOTSPOT_SSID
            HOTSPOT_SSID=${HOTSPOT_SSID:-$DEFAULT_SSID}
            
            # 2. Ask for Custom Password (Enforce 8 character minimum for WPA2)
            while true; do
                read -p "        Enter Hotspot Password (min 8 chars) [Default: dashboard123]: " HOTSPOT_PASS
                HOTSPOT_PASS=${HOTSPOT_PASS:-dashboard123}
                
                if [ ${#HOTSPOT_PASS} -ge 8 ]; then
                    break
                else
                    echo "        [!] Invalid password. WPA2 requires a minimum of 8 characters."
                fi
            done
            
            echo "        [*] Configuring Wi-Fi Hotspot on $WIFI_IFACE..."
            
            # Create the AP using NetworkManager's 'shared' IPv4 method
            sudo nmcli connection add type wifi ifname "$WIFI_IFACE" con-name Hotspot autoconnect yes ssid "$HOTSPOT_SSID" &>/dev/null
            sudo nmcli connection modify Hotspot 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared
            sudo nmcli connection modify Hotspot wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
            sudo nmcli connection up Hotspot &>/dev/null
            
            if [ $? -eq 0 ]; then
                echo "        [✓] Hotspot successfully activated!"
                HOTSPOT_ACTIVE=true
            else
                echo "        [X] Failed to bring up Hotspot. Check your Wi-Fi adapter capabilities."
            fi
        fi
    fi
else
    echo "    [*] Skipping dedicated test device configurations and hotspot setup."
fi
echo "--------------------------------------------------------"

# Fix ownership and permissions so anyone can modify/delete the folder
sudo chown -R "$USER:$USER" "$TARGET_DIR"
sudo chmod -R 777 "$TARGET_DIR"

# --- 6. OPTIONAL SYSTEMD SERVICE CONFIGURATION ---
echo ""
echo "--------------------------------------------------------"
SERVICE_ACTIVE=false

if systemctl is-active --quiet network-dashboard.service; then
    SERVICE_ACTIVE=true
    echo "[?] Background Boot Service is currently ENABLED."
    read -p "[?] Do you want to DISABLE/REMOVE the background service? (y/N): " toggle_service
    if [[ "$toggle_service" =~ ^[Yy]$ ]]; then
        sudo systemctl stop network-dashboard.service &>/dev/null
        sudo systemctl disable network-dashboard.service &>/dev/null
        sudo rm -f "$SERVICE_FILE"
        sudo systemctl daemon-reload
        echo "    [✓] Background boot service disabled and removed."
        SERVICE_ACTIVE=false
    fi
else
    echo "[?] Background Boot Service is currently DISABLED."
    read -p "[?] Do you want to ENABLE automatic start on boot and run in the background? (y/N): " toggle_service
    if [[ "$toggle_service" =~ ^[Yy]$ ]]; then
        echo "    [*] Setting up systemd service..."
        sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Network Diagnostics Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$TARGET_DIR
ExecStart=/usr/bin/python3 $TARGET_DIR/setup_env.py
Restart=always
RestartSec=10
User=$USER

[Install]
WantedBy=multi-user.target
EOL
        sudo systemctl daemon-reload
        sudo systemctl enable network-dashboard.service &>/dev/null
        sudo systemctl start network-dashboard.service &>/dev/null
        echo "    [✓] Background boot service enabled and started."
        SERVICE_ACTIVE=true
    fi
fi
echo "--------------------------------------------------------"

# --- 7. DESKTOP SHORTCUT CREATION ---
echo ""
read -p "[?] Do you want to create a Desktop shortcut to launch the app? (y/N): " create_shortcut
if [[ "$create_shortcut" =~ ^[Yy]$ ]]; then
    DESKTOP_DIR="$HOME/Desktop"
    if [ -d "$DESKTOP_DIR" ]; then
        SHORTCUT_FILE="$DESKTOP_DIR/Network-Diagnostics.desktop"
        
        # Point to the local logo image if it exists, otherwise use a generic fallback icon
        ICON_PATH="$TARGET_DIR/static/Logo.png"
        if [ ! -f "$ICON_PATH" ]; then
            ICON_PATH="applications-internet"
        fi

        cat <<EOL > "$SHORTCUT_FILE"
[Desktop Entry]
Name=Network Diagnostics
Comment=Open Network Diagnostics Dashboard
Exec=python3 $TARGET_DIR/setup_env.py
Icon=$ICON_PATH
Terminal=true
Type=Application
Categories=Network;System;
EOL
        chmod +x "$SHORTCUT_FILE"
        echo "[✓] Desktop shortcut created at $SHORTCUT_FILE using app logo."
    else
        echo "[!] Desktop folder not found, skipping shortcut."
    fi
fi

# --- 8. FINAL SUMMARY & IP INFO ---
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk 'NR==1 {print $7}')
fi
PORT=$(cat "$TARGET_DIR/webport" 2>/dev/null || echo "80")

echo ""
echo "========================================================"
echo "   SETUP COMPLETE!"
echo "========================================================"
if [ "$SERVICE_ACTIVE" = false ]; then
    echo "   Starting dashboard via setup script..."
    echo ""
fi

if [ "$HOTSPOT_ACTIVE" = true ]; then
    DISPLAY_SSID=$(sudo nmcli -g 802-11-wireless.ssid connection show Hotspot 2>/dev/null)
    DISPLAY_PASS=$(sudo nmcli -s -g wifi-sec.psk connection show Hotspot 2>/dev/null)
    
    echo "   📱 CONNECT VIA HOTSPOT:"
    echo "      1. Connect to Wi-Fi: $DISPLAY_SSID"
    echo "      2. Password:         $DISPLAY_PASS"
    echo "      3. Open browser to:  http://10.42.0.1:$PORT"
    echo ""
fi

if [ -n "$LOCAL_IP" ]; then
    echo "   💻 ACCESS THE DASHBOARD:"
    echo "      Open your browser to: http://$LOCAL_IP:$PORT"
    echo "      (Or locally at:       http://127.0.0.1:$PORT)"
    echo ""
fi
echo "========================================================"

# --- 9. HANDOFF TO SETUP PYTHON SCRIPT ---
if [ "$SERVICE_ACTIVE" = false ]; then
    python3 "$TARGET_DIR/setup_env.py"
fi