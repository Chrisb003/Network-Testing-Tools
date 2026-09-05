#!/bin/bash

echo "========================================================"
echo "   NETWORK DIAGNOSTICS - RASPBERRY PI AUTO-INSTALLER"
echo "========================================================"

# --- 1. CONFIGURATION ---
TARGET_DIR="$HOME/Network-Testing-Tools"
REPO_OWNER="Chrisb003"
REPO_NAME="Network-Testing-Tools"
BRANCH="main"
TOKEN="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"

echo "[*] Step 1: Installing system prerequisites (sudo password may be required)..."
sudo apt-get update
# Added 'network-manager' to ensure hotspot creation tools are available
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools libpcap-dev unzip curl network-manager

echo ""
echo "[*] Step 2: Downloading latest project files from GitHub..."
if [ -d "$TARGET_DIR" ]; then
    echo "[!] Directory $TARGET_DIR already exists. Backing up old version..."
    mv "$TARGET_DIR" "${TARGET_DIR}_old_$(date +%s)"
fi

# Download the repository zip archive using the embedded token
curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github.v3+json" -L "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/zipball/$BRANCH" -o /tmp/network_dashboard.zip

echo "[*] Step 3: Extracting files into $TARGET_DIR..."
mkdir -p /tmp/network_dashboard_extract
unzip -q /tmp/network_dashboard.zip -d /tmp/network_dashboard_extract

# Find the dynamically named extracted folder and move it to our target directory
EXTRACTED_FOLDER=$(ls -d /tmp/network_dashboard_extract/*/)
mv "$EXTRACTED_FOLDER" "$TARGET_DIR"

# Clean up temp files
rm -rf /tmp/network_dashboard.zip /tmp/network_dashboard_extract

echo ""
echo "[*] Step 4: Configuring headless mode..."
# Create the autostart file with 0 to prevent it from trying to open a browser window on the Pi
echo "0" > "$TARGET_DIR/autostart"
echo "[✓] Headless mode enabled."

# --- NEW: TOGGLEABLE WI-FI HOTSPOT SETUP ---
echo ""
echo "--------------------------------------------------------"

# Automatically detect the wireless interface name (usually wlan0)
WIFI_IFACE=$(iw dev | awk '$1=="Interface"{print $2}' | head -n 1)
if [ -z "$WIFI_IFACE" ]; then
    WIFI_IFACE="wlan0"
fi

HOTSPOT_ACTIVE=false

# Check if a hotspot is currently configured
if nmcli connection show "Hotspot" &>/dev/null; then
    read -p "[?] A Wi-Fi Hotspot is currently ENABLED. Do you want to DISABLE it? (y/N): " toggle_hotspot
    if [[ "$toggle_hotspot" =~ ^[Yy]$ ]]; then
        sudo nmcli connection delete Hotspot &>/dev/null
        echo "    [✓] Hotspot successfully disabled and removed."
    else
        HOTSPOT_ACTIVE=true
    fi
else
    read -p "[?] Do you want to ENABLE a Wi-Fi Hotspot to access the dashboard? (y/N): " toggle_hotspot
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
        read -p "    Enter Hotspot SSID [Default: $DEFAULT_SSID]: " HOTSPOT_SSID
        HOTSPOT_SSID=${HOTSPOT_SSID:-$DEFAULT_SSID}
        
        # 2. Ask for Custom Password (Enforce 8 character minimum for WPA2)
        while true; do
            read -p "    Enter Hotspot Password (min 8 chars) [Default: dashboard123]: " HOTSPOT_PASS
            HOTSPOT_PASS=${HOTSPOT_PASS:-dashboard123}
            
            if [ ${#HOTSPOT_PASS} -ge 8 ]; then
                break
            else
                echo "    [!] Invalid password. WPA2 requires a minimum of 8 characters."
            fi
        done
        
        echo "    [*] Configuring Wi-Fi Hotspot on $WIFI_IFACE..."
        
        # Create the AP using NetworkManager's 'shared' IPv4 method
        sudo nmcli connection add type wifi ifname "$WIFI_IFACE" con-name Hotspot autoconnect yes ssid "$HOTSPOT_SSID" &>/dev/null
        sudo nmcli connection modify Hotspot 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared
        sudo nmcli connection modify Hotspot wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
        sudo nmcli connection up Hotspot &>/dev/null
        
        if [ $? -eq 0 ]; then
            echo "    [✓] Hotspot successfully activated!"
            HOTSPOT_ACTIVE=true
        else
            echo "    [X] Failed to bring up Hotspot. Check your Wi-Fi adapter capabilities."
        fi
    fi
fi
echo "--------------------------------------------------------"

echo ""
echo "[*] Step 5: Setting up automatic start on boot (systemd)..."
SERVICE_FILE="/etc/systemd/system/network-dashboard.service"

# Create a robust background service to keep the app alive and launch it on boot
sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Network Diagnostics Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$TARGET_DIR
# The setup script handles building the virtual environment automatically
ExecStart=/usr/bin/python3 $TARGET_DIR/setup_env.py
Restart=always
RestartSec=10
# Run as root to ensure Scapy can access raw network sockets and ARP tables
User=root

[Install]
WantedBy=multi-user.target
EOL

# Reload the system daemon to recognize the new service and enable it on boot
sudo systemctl daemon-reload
sudo systemctl enable network-dashboard.service &>/dev/null

echo ""
echo "[*] Step 6: Launching setup and dashboard..."
echo "[!] The service is now starting in the background. It will automatically build"
echo "    the Python environment and download the Speedtest CLI on its first run."
sudo systemctl start network-dashboard.service

# Get the Pi's standard local IP (ignoring the 10.42.0.1 hotspot IP)
PI_IP=$(hostname -I | awk '{for(i=1;i<=NF;i++) if($i !~ /^10\.42\.0\./) {print $i; exit}}')

echo ""
echo "========================================================"
echo "   INSTALLATION COMPLETE!"
echo "========================================================"
echo "   The dashboard is running securely in the background."
echo ""
if [ "$HOTSPOT_ACTIVE" = true ]; then
    # Fetch the live SSID and Password directly from NetworkManager to ensure accuracy
    DISPLAY_SSID=$(sudo nmcli -g 802-11-wireless.ssid connection show Hotspot 2>/dev/null)
    DISPLAY_PASS=$(sudo nmcli -s -g wifi-sec.psk connection show Hotspot 2>/dev/null)
    
    echo "   📱 CONNECT VIA HOTSPOT:"
    echo "      1. Connect to Wi-Fi: $DISPLAY_SSID"
    echo "      2. Password:         $DISPLAY_PASS"
    echo "      3. Open browser to:  http://10.42.0.1:81"
    echo ""
fi

if [ -n "$PI_IP" ]; then
    echo "   💻 CONNECT VIA LOCAL NETWORK (LAN/WLAN):"
    echo "      Open a browser to:   http://$PI_IP:81"
    echo ""
fi

echo "   To view the live console logs or troubleshoot, run:"
echo "   sudo journalctl -u network-dashboard.service -f"
echo "========================================================"