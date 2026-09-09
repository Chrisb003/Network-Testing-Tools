#!/bin/sh

# Ensure we run as a standard user for paths, but keep sudo available for apt commands
if [ "$EUID" -eq 0 ]; then
    echo "[!] Please do NOT run this script directly with 'sudo'. Run it as your normal user."
    echo "    The script will prompt for sudo credentials only when necessary."
    exit 1
fi

# --- 1. CONFIGURATION ---
TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"

# --- 2. EXISTING INSTALLATION CHECK & UNINSTALL OPTION ---
if [ -d "$TARGET_DIR" ]; then
    echo "========================================================"
    echo "   NETWORK DIAGNOSTICS - LINUX INSTALLER & MANAGER"
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
                
                # Cleanup old systemd service if it existed
                if [ -f "/etc/systemd/system/network-dashboard.service" ]; then
                    echo "[*] Stopping system service..."
                    sudo systemctl stop network-dashboard.service >/dev/null 2>&1
                    sudo systemctl disable network-dashboard.service >/dev/null 2>&1
                    sudo rm -f /etc/systemd/system/network-dashboard.service
                    sudo systemctl daemon-reload
                fi
                
                echo "[*] Deleting application directory..."
                rm -rf "$TARGET_DIR"
                
                echo "[*] Removing shortcuts..."
                rm -f "$HOME/Desktop/Network-Diagnostics.desktop"
                rm -f "$HOME/.config/autostart/Network-Diagnostics.desktop"
                
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
echo "   NETWORK DIAGNOSTICS - LINUX INSTALLER & MANAGER"
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

# --- 4. PREREQUISITES (Multi-Distro Support) ---
echo ""
echo "[*] Step 1: Installing system prerequisites (sudo password may be required)..."

if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools libpcap-dev unzip curl network-manager
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip python3-devel gcc git net-tools libpcap-devel unzip curl NetworkManager
elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Syu --noconfirm python python-pip base-devel git net-tools libpcap unzip curl networkmanager
elif command -v zypper >/dev/null 2>&1; then
    sudo zypper refresh
    sudo zypper install -y python3 python3-pip python3-devel gcc git net-tools libpcap-devel unzip curl NetworkManager
else
    echo "[!] Warning: Unknown package manager. Please ensure Python 3, venv, pip, git, libpcap, and curl are installed manually."
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

# --- 6. DEDICATED TEST DEVICE PROMPT & CONFIG TRIGGERS ---
echo ""
echo "--------------------------------------------------------"
printf "[?] Are you using this device as a dedicated test device? (y/N): "
read is_dedicated < /dev/tty
HOTSPOT_ACTIVE=false

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

        if [ ! -f "$TARGET_DIR/webport" ]; then
            echo "80" > "$TARGET_DIR/webport"
            echo "        [+] Created 'webport' file set to 80."
        fi

        # --- OPTIONAL WI-FI HOTSPOT SETUP ---
        echo ""
        echo "    [?] Wi-Fi Hotspot Configuration:"
        
        WIFI_IFACE=""
        if command -v iw >/dev/null 2>&1; then
            WIFI_IFACE=$(iw dev | awk '$1=="Interface"{print $2}' | head -n 1)
        elif command -v ip >/dev/null 2>&1; then
            WIFI_IFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep '^wl' | head -n 1)
        fi

        if [ -z "$WIFI_IFACE" ]; then
            WIFI_IFACE="wlan0"
        fi

        if nmcli connection show "Hotspot" >/dev/null 2>&1; then
            printf "    [?] A Wi-Fi Hotspot is currently ENABLED. Do you want to DISABLE it? (y/N): "
            read toggle_hotspot < /dev/tty
            case "$toggle_hotspot" in
                [Yy]* )
                    sudo nmcli connection delete Hotspot >/dev/null 2>&1
                    echo "        [✓] Hotspot successfully disabled and removed."
                    ;;
                * ) HOTSPOT_ACTIVE=true ;;
            esac
        else
            printf "    [?] Do you want to ENABLE a Wi-Fi Hotspot to access the dashboard? (y/N): "
            read toggle_hotspot < /dev/tty
            case "$toggle_hotspot" in
                [Yy]* )
                    MAC_ADDR=$(cat /sys/class/net/$WIFI_IFACE/address 2>/dev/null | tr -d ':')
                    if [ -n "$MAC_ADDR" ]; then
                        MAC_SUFFIX=$(echo "$MAC_ADDR" | awk '{print substr($0,length($0)-5,6)}' | tr 'a-z' 'A-Z')
                    else
                        MAC_SUFFIX=$RANDOM
                    fi
                    DEFAULT_SSID="Network-Dashboard-$MAC_SUFFIX"

                    printf "        Enter Hotspot SSID [Default: %s]: " "$DEFAULT_SSID"
                    read HOTSPOT_SSID < /dev/tty
                    HOTSPOT_SSID=${HOTSPOT_SSID:-$DEFAULT_SSID}
                    
                    while true; do
                        printf "        Enter Hotspot Password (min 8 chars) [Default: dashboard123]: "
                        read HOTSPOT_PASS < /dev/tty
                        HOTSPOT_PASS=${HOTSPOT_PASS:-dashboard123}
                        
                        if [ ${#HOTSPOT_PASS} -ge 8 ]; then
                            break
                        else
                            echo "        [!] Invalid password. WPA2 requires a minimum of 8 characters."
                        fi
                    done
                    
                    echo "        [*] Configuring Wi-Fi Hotspot on $WIFI_IFACE..."
                    sudo nmcli connection add type wifi ifname "$WIFI_IFACE" con-name Hotspot autoconnect yes ssid "$HOTSPOT_SSID" >/dev/null 2>&1
                    sudo nmcli connection modify Hotspot 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared
                    sudo nmcli connection modify Hotspot wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
                    sudo nmcli connection up Hotspot >/dev/null 2>&1
                    
                    if [ $? -eq 0 ]; then
                        echo "        [✓] Hotspot successfully activated!"
                        HOTSPOT_ACTIVE=true
                    else
                        echo "        [X] Failed to bring up Hotspot. Check your Wi-Fi adapter capabilities."
                    fi
                    ;;
            esac
        fi
        ;;
    * ) echo "    [*] Skipping dedicated test device configurations and hotspot setup." ;;
esac
echo "--------------------------------------------------------"

sudo chown -R "$USER:$USER" "$TARGET_DIR"
sudo chmod -R 777 "$TARGET_DIR"

# --- 7. USER LOGIN AUTOSTART (VISIBLE TERMINAL) ---
echo ""
echo "--------------------------------------------------------"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/Network-Diagnostics.desktop"

# Clean up legacy systemd service from older script versions
if [ -f "/etc/systemd/system/network-dashboard.service" ]; then
    echo "[*] Cleaning up legacy invisible systemd service..."
    sudo systemctl stop network-dashboard.service >/dev/null 2>&1
    sudo systemctl disable network-dashboard.service >/dev/null 2>&1
    sudo rm -f "/etc/systemd/system/network-dashboard.service"
    sudo systemctl daemon-reload
fi

if [ -f "$AUTOSTART_FILE" ]; then
    echo "[?] User-Login Startup is currently ENABLED."
    printf "[?] Do you want to DISABLE/REMOVE the startup shortcut? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            rm -f "$AUTOSTART_FILE"
            echo "    [✓] Startup shortcut removed."
            ;;
    esac
else
    echo "[?] User-Login Startup is currently DISABLED."
    printf "[?] Do you want to ENABLE automatic start on user login? (y/N): "
    read toggle_service < /dev/tty
    case "$toggle_service" in
        [Yy]* )
            echo "    [*] Setting up user login autostart..."
            mkdir -p "$AUTOSTART_DIR"
            
            ICON_PATH="$TARGET_DIR/static/favicon.ico"
            if [ ! -f "$ICON_PATH" ]; then
                ICON_PATH="$TARGET_DIR/static/Logo.png"
            fi
            
            cat <<EOL > "$AUTOSTART_FILE"
[Desktop Entry]
Name=Network Diagnostics
Comment=Open Network Diagnostics Dashboard
Exec=python3 "$TARGET_DIR/setup_env.py"
Path=$TARGET_DIR
Icon=$ICON_PATH
Terminal=true
Type=Application
Categories=Network;System;
EOL
            chmod +x "$AUTOSTART_FILE"
            echo "    [✓] Startup on login enabled (visible terminal window)."
            ;;
    esac
fi
echo "--------------------------------------------------------"

# --- 8. DESKTOP SHORTCUT CREATION ---
echo ""
printf "[?] Do you want to create a Desktop shortcut to launch the app? (y/N): "
read create_shortcut < /dev/tty
case "$create_shortcut" in
    [Yy]* )
        DESKTOP_DIR="$HOME/Desktop"
        if [ -d "$DESKTOP_DIR" ]; then
            SHORTCUT_FILE="$DESKTOP_DIR/Network-Diagnostics.desktop"
            
            ICON_PATH="$TARGET_DIR/static/favicon.ico"
            if [ ! -f "$ICON_PATH" ]; then
                ICON_PATH="$TARGET_DIR/static/Logo.png"
            fi
            if [ ! -f "$ICON_PATH" ]; then
                ICON_PATH="applications-internet"
            fi

            cat <<EOL > "$SHORTCUT_FILE"
[Desktop Entry]
Name=Network Diagnostics
Comment=Open Network Diagnostics Dashboard
Exec=python3 "$TARGET_DIR/setup_env.py"
Path=$TARGET_DIR
Icon=$ICON_PATH
Terminal=true
Type=Application
Categories=Network;System;
EOL
            chmod +x "$SHORTCUT_FILE"
            echo "[✓] Desktop shortcut created at $SHORTCUT_FILE."
        else
            echo "[!] Desktop folder not found, skipping shortcut."
        fi
        ;;
esac

# --- 9. FINAL SUMMARY & IP INFO ---
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk 'NR==1 {print $7}')
fi
PORT=$(cat "$TARGET_DIR/webport" 2>/dev/null || echo "81")

echo ""
echo "========================================================"
echo "   SETUP COMPLETE!"
echo "========================================================"
echo "   Starting dashboard via setup script..."
echo ""

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

# --- 10. HANDOFF TO SETUP PYTHON SCRIPT ---
python3 "$TARGET_DIR/setup_env.py"