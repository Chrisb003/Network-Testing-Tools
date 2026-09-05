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
# Install all underlying OS dependencies required for Python, Scapy, and packet sniffing
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools libpcap-dev unzip curl

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
sudo systemctl enable network-dashboard.service

echo ""
echo "[*] Step 6: Launching setup and dashboard..."
echo "[!] The service is now starting in the background. It will automatically build"
echo "    the Python environment and download the Speedtest CLI on its first run."
sudo systemctl start network-dashboard.service

# Get the Pi's local IP to show the user where to connect
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "========================================================"
echo "   INSTALLATION COMPLETE!"
echo "========================================================"
echo "   The dashboard is running securely in the background."
echo ""
echo "   You can access the dashboard from any device on your"
echo "   network by opening a web browser and going to:"
echo "   http://$PI_IP:81"
echo ""
echo "   To view the live console logs or troubleshoot, run:"
echo "   sudo journalctl -u network-dashboard.service -f"
echo "========================================================"