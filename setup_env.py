import os
import sys
import subprocess
import venv
import platform
import shutil
import urllib.request
import zipfile
from pathlib import Path

# --- Configuration ---
SETUP_VERSION = "0.1.0"
VENV_DIR_NAME = "venv"
REQUIREMENTS = ["flask", "psutil", "scapy"]
APP_FILENAME = "app.py"

def create_venv(base_dir):
    """Creates the virtual environment if it doesn't exist."""
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists():
        print(f"[*] Creating virtual environment in {venv_path}...")
        venv.create(venv_path, with_pip=True)
    return venv_path

def get_venv_paths(venv_path):
    """Returns paths for python executable and the bin/scripts folder."""
    if platform.system() == "Windows":
        return {
            "python": venv_path / "Scripts" / "python.exe",
            "bin_dir": venv_path / "Scripts",
            "ext": ".exe"
        }
    else:
        return {
            "python": venv_path / "bin" / "python",
            "bin_dir": venv_path / "bin",
            "ext": ""
        }

def install_requirements(python_path):
    """Installs Python libraries."""
    print("[*] Installing Python dependencies (Flask, Scapy, Psutil)...")
    subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)

# --- OS Specific Installers for Speedtest ---
def install_speedtest_windows(bin_dir):
    """Windows: Downloads and extracts the official Ookla CLI."""
    WINDOWS_FALLBACK_URL = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"
    target_path = bin_dir / "speedtest.exe"
    
    if target_path.exists():
        print("[✓] Speedtest CLI already installed.")
        return
    
    print("[*] Installing Speedtest CLI for Windows...")
    temp_file = bin_dir / "speedtest.zip"
    try:
        urllib.request.urlretrieve(WINDOWS_FALLBACK_URL, temp_file)
        with zipfile.ZipFile(temp_file, 'r') as zip_ref:
            zip_ref.extract("speedtest.exe", bin_dir)
        if temp_file.exists(): os.remove(temp_file)
        print(f"[✓] Speedtest CLI installed.")
    except Exception as e:
        print(f"[X] Installation failed: {e}")

def install_homebrew_if_missing():
    """Mac: Checks/Installs Homebrew."""
    if shutil.which("brew"): return True
    print("\n[!] Homebrew missing. Installing...")
    try:
        cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        subprocess.check_call(cmd, shell=True)
        return True
    except:
        print("[X] Homebrew install failed.")
        return False

def install_speedtest_mac():
    """Mac: Installs Speedtest via Homebrew."""
    print("[*] Checking Speedtest CLI via Homebrew...")
    if not install_homebrew_if_missing(): return
    
    # We run these commands to ensure it's installed and updated
    commands = [
        ["brew", "tap", "teamookla/speedtest"],
        ["brew", "update"],
        ["brew", "install", "speedtest", "--force"]
    ]
    try:
        for cmd in commands: 
            # Suppress output for cleaner logs, unless error
            subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        print("[✓] Speedtest CLI ready.")
    except: 
        print("[X] Install failed.")

def install_speedtest_linux():
    """Linux: Installs Speedtest via apt-get (Debian/Ubuntu)."""
    print("[*] Checking Speedtest CLI via apt-get...")
    if shutil.which("apt-get") is None:
        print("[X] Error: 'apt-get' not found. Ensure you are on a Debian-based system."); return

    try:
        # We assume if the command exists, we are good, to save time on startup
        if shutil.which("speedtest"):
            print("[✓] Speedtest CLI ready.")
            return

        print("    Configuring Ookla repository...")
        subprocess.run(["sudo", "apt-get", "remove", "speedtest-cli", "-y"], stderr=subprocess.DEVNULL)
        subprocess.check_call(["sudo", "apt-get", "install", "curl", "-y"], stdout=subprocess.DEVNULL)
        subprocess.check_call("curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash", shell=True, stdout=subprocess.DEVNULL)
        subprocess.check_call(["sudo", "apt-get", "install", "speedtest", "-y"], stdout=subprocess.DEVNULL)
        print("[✓] Speedtest CLI installed.")
    except: 
        print("[X] Install failed.")

def install_speedtest_cli(bin_dir):
    """Router to pick the correct OS installer."""
    system = platform.system()
    if system == "Windows": install_speedtest_windows(bin_dir)
    elif system == "Darwin": install_speedtest_mac()
    elif system == "Linux": install_speedtest_linux()

def run_application(base_dir, venv_python):
    """Runs the main application."""
    app_path = base_dir / APP_FILENAME
    
    if not app_path.exists():
        print(f"\n[!] Critical Error: '{APP_FILENAME}' not found in {base_dir}")
        return

    print("\n" + "="*50)
    print(f"   SETUP v{SETUP_VERSION} COMPLETE - LAUNCHING APP")
    print("="*50)
    
    # Run with sudo/admin privileges
    cmd = ["sudo", str(venv_python), str(app_path)]
    
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[!] Stopped.")

def main():
    base_dir = Path(__file__).parent.resolve()
    print(f"--- Network Dashboard Setup (v{SETUP_VERSION}) ---")
    
    # 1. Create/Check Virtual Environment
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # 2. Install Python Dependencies
    install_requirements(paths["python"])
    
    # 3. Install Speedtest Binary
    install_speedtest_cli(paths["bin_dir"])
    
    # 4. Run the Application
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()