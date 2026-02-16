#!/bin/bash

# Network Diagnostics - One-Click Installer (Linux & macOS)
# This script ensures Python 3 is installed before launching the main setup.

echo "========================================================"
echo "   NETWORK DIAGNOSTICS - INSTALLER WRAPPER"
echo "========================================================"

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
    # Check if Python 3 is installed
    if ! command -v python3 &> /dev/null; then
        echo "[!] Python 3 not found."
        
        # Check if Homebrew is installed
        if ! command -v brew &> /dev/null; then
            echo "[*] Homebrew not found. Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            
            # Add Brew to path for this session
            if [[ $(uname -m) == 'arm64' ]]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            else
                eval "$(/usr/local/bin/brew shellenv)"
            fi
        fi
        
        echo "[*] Installing Python 3 via Homebrew..."
        brew install python
    else
        echo "[✓] Python 3 is already installed."
    fi
fi

# ---------------------------------------------------------
# 4. HANDOFF TO SETUP SCRIPT
# ---------------------------------------------------------
if [ -f "setup_env.py" ]; then
    echo ""
    echo "[*] Launching Python Setup Script..."
    echo "--------------------------------------------------------"
    python3 setup_env.py
else
    echo ""
    echo "[X] Error: 'setup_env.py' not found in this directory."
    echo "    Please ensure install.sh and setup_env.py are in the same folder."
    exit 1
fi