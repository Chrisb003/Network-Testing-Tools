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
# This version number is read by app.py for the footer display
SETUP_VERSION = "0.1.0"
VENV_DIR_NAME = "venv"
REQUIREMENTS = ["flask", "psutil", "scapy"]
APP_FILENAME = "app.py"

def create_venv(base_dir):
    """Creates the virtual environment if it doesn't exist."""
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists():
        print(f"[*] Creating virtual environment (Setup v{SETUP_VERSION})...")
        venv.create(venv_path, with_pip=True)
    else:
        print(f"[✓] Virtual environment detected.")
    return venv_path

def get_venv_paths(venv_path):
    """Returns executable paths based on Operating System."""
    if platform.system() == "Windows":
        return {
            "python": venv_path / "Scripts" / "python.exe",
            "bin_dir": venv_path / "Scripts"
        }
    else:
        return {
            "python": venv_path / "bin" / "python",
            "bin_dir": venv_path / "bin"
        }

def install_requirements(python_path):
    """Installs required Python libraries into the venv."""
    print("[*] Checking Python dependencies (Flask, Scapy, Psutil)...")
    try:
        subprocess.check_call([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
        subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)
        print("[✓] Python dependencies are up to date.")
    except Exception as e:
        print(f"[X] Error installing dependencies: {e}")

# --- OS Specific Installers for Official Speedtest CLI ---

def install_speedtest_windows(bin_dir):
    """Windows: Downloads and extracts the official Ookla CLI."""
    url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"
    target_path = bin_dir / "speedtest.exe"
    if target_path.exists():
        return
    
    print("[*] Downloading Official Speedtest CLI for Windows...")
    temp_zip = bin_dir / "speedtest.zip"
    try:
        urllib.request.urlretrieve(url, temp_zip)
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extract("speedtest.exe", bin_dir)
        os.remove(temp_zip)
        print(f"[✓] Speedtest CLI installed to venv.")
    except Exception as e:
        print(f"[X] Windows Speedtest install failed: {e}")

def install_speedtest_mac():
    """Mac: Installs Speedtest via Homebrew."""
    print("[*] Checking Speedtest CLI via Homebrew...")
    if not shutil.which("brew"):
        print("[!] Homebrew not found. Speedtest CLI installation skipped.")
        return
    
    try:
        # Tap and Install
        subprocess.run(["brew", "tap", "teamookla/speedtest"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["brew", "install", "speedtest", "--force"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[✓] Speedtest CLI is ready via Homebrew.")
    except Exception as e:
        print(f"[X] Mac Speedtest install failed: {e}")

def install_speedtest_linux():
    """Linux: Installs Speedtest via official Apt repository."""
    if not shutil.which("apt-get"):
        return

    print("[*] Checking Speedtest CLI via Apt...")
    if shutil.which("speedtest"):
        print("[✓] Speedtest CLI detected.")
        return

    try:
        print("    Adding Ookla repository...")
        subprocess.run(["sudo", "apt-get", "remove", "speedtest-cli", "-y"], stderr=subprocess.DEVNULL)
        subprocess.check_call(["sudo", "apt-get", "install", "curl", "-y"], stdout=subprocess.DEVNULL)
        subprocess.check_call("curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash", shell=True)
        subprocess.check_call(["sudo", "apt-get", "install", "speedtest", "-y"], stdout=subprocess.DEVNULL)
        print("[✓] Speedtest CLI installed.")
    except Exception as e:
        print(f"[X] Linux Speedtest install failed: {e}")

def install_speedtest_cli(bin_dir):
    """Routes to the correct Speedtest installer based on OS."""
    system = platform.system()
    if system == "Windows":
        install_speedtest_windows(bin_dir)
    elif system == "Darwin":
        install_speedtest_mac()
    elif system == "Linux":
        install_speedtest_linux()

def run_application(base_dir, venv_python):
    """Launches the main app with sudo/admin privileges."""
    app_path = base_dir / APP_FILENAME
    
    if not app_path.exists():
        print(f"\n[!] ERROR: '{APP_FILENAME}' not found in {base_dir}")
        print("Please ensure your main python file is named 'app.py' and is in this folder.")
        return

    print("\n" + "="*60)
    print(f"   SYSTEM READY - LAUNCHING NETWORK DASHBOARD (Setup v{SETUP_VERSION})")
    print("="*60)
    print("[*] Elevated privileges required for network scanning.")
    
    # Run with sudo
    cmd = ["sudo", str(venv_python), str(app_path)]
    
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")
    except Exception as e:
        print(f"[X] Failed to launch application: {e}")

def main():
    # Detect the script's directory
    base_dir = Path(__file__).parent.resolve()
    
    print(f"--- Network Diagnostics Setup Utility v{SETUP_VERSION} ---")
    
    # 1. Setup Venv
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # 2. Install Python dependencies
    install_requirements(paths["python"])
    
    # 3. Install/Update Speedtest CLI binary
    install_speedtest_cli(paths["bin_dir"])
    
    # 4. Launch the App
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()