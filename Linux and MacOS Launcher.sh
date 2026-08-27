#!/bin/bash

# Network Diagnostics - One-Click Installer (Linux & macOS)
# This script ensures Python 3 is installed before launching the main setup.

echo "========================================================"
echo "   NETWORK DIAGNOSTICS - INSTALLER WRAPPER"
echo "========================================================"

# --- NEW: AUTO-DIRECTORY DETECTION ---
# Move to the folder where this script is actually located
# $0 is the script path, dirname gets the folder, and cd -P handles symlinks safely
cd "$(dirname "$0")" || exit
echo "[*] Working Directory: $(pwd)"

# 1. DETECT OPERATING SYSTEM
OS="$(uname -s)"
case "${OS}" in
    Linux*)     machine=Linux;;
    Darwin*)    machine=Mac;;
    *)          machine="UNKNOWN:${OS}"
esac

echo "[*] Detected System: $machine"

# ---------------------------------------------------------
# 2. LINUX INSTALLATION LOGIC (Debian/Ubuntu)
# ---------------------------------------------------------
if [ "$machine" == "Linux" ]; then
    # Check if Python 3 is installed
    if ! command -v python3 &> /dev/null; then
        echo "[!] Python 3 not found. Installing via apt-get..."
        
        # Check for sudo rights/presence
        if command -v sudo &> /dev/null; then
            sudo apt-get update
            # Install Python 3 and venv/pip (critical for Ubuntu)
            sudo apt-get install -y python3 python3-venv python3-pip git
        else
            echo "[X] Error: 'sudo' is missing. Please install Python 3 manually."
            exit 1
        fi
    else
        echo "[✓] Python 3 is already installed."
        
        # Check specifically for venv module (often missing on Ubuntu)
        python3 -c "import venv" 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "[!] Python venv module missing. Installing prerequisites..."
            sudo apt-get update
            sudo apt-get install -y python3-venv python3-pip git
        fi
    fi
fi

# ---------------------------------------------------------
# 3. MACOS INSTALLATION LOGIC
# ---------------------------------------------------------
if [ "$machine" == "Mac" ]; then
    if ! command -v python3 &> /dev/null; then
        echo "[!] Python 3 not found. Downloading and installing official Python.org package for macOS..."
        
        # Define the target stable Python version
        PY_VERSION="3.14.7"
        PKG_NAME="python-${PY_VERSION}-macos11.pkg"
        PKG_URL="https://www.python.org/ftp/python/${PY_VERSION}/${PKG_NAME}"
        
        echo "[*] Downloading Python ${PY_VERSION} from python.org..."
        curl -O "$PKG_URL"
        
        echo "[*] Installing Python package (administrator password required)..."
        sudo installer -pkg "$PKG_NAME" -target /
        
        # Clean up installer file
        rm -f "$PKG_NAME"
        echo "[✓] Python.org installation completed successfully."
    else
        echo "[✓] Python 3 is already installed."
    fi
fi

# ---------------------------------------------------------
# 4. HANDOFF TO SETUP SCRIPT
# ---------------------------------------------------------
# Now that we have cd'd into the correct folder, this check will always pass
if [ -f "setup_env.py" ]; then
    echo ""
    echo "[*] Launching Python Setup Script..."
    echo "--------------------------------------------------------"
    python3 setup_env.py
else
    echo ""
    echo "[X] Error: 'setup_env.py' not found in $(pwd)."
    echo "    Please ensure install.sh and setup_env.py are in the same folder."
    exit 1
fi