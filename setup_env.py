import os
import sys
import subprocess
import venv
import platform
import shutil
import urllib.request
import zipfile
import io
import ctypes
from pathlib import Path

# --- Configuration ---
SETUP_VERSION = "0.5.0"
VENV_DIR_NAME = "venv"
REQUIREMENTS = ["flask", "psutil", "scapy"]
APP_FILENAME = "app.py"

# GITHUB PRIVATE REPO CONFIGURATION
# Token is derived from provided application source
GITHUB_SETTINGS = {
    "owner": "Pancool",
    "repo": "Network-Testing-Tools",
    "token": "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",
    "branch": "main"
}

def install_git():
    """Checks for Git and installs it if missing based on the OS."""
    if shutil.which("git"):
        return True

    system = platform.system()
    print(f"[*] Git not detected. Attempting automated installation for {system}...")

    try:
        if system == "Windows":
            if shutil.which("choco"):
                subprocess.run(["choco", "install", "git", "-y"], check=True)
            else:
                print("[!] Chocolatey not found. Please install Git manually from https://git-scm.com/")
                return False

        elif system == "Darwin": # macOS
            if install_homebrew():
                subprocess.run(["brew", "install", "git"], check=True)
            else:
                return False

        elif system == "Linux":
            # Triggers sudo prompt for apt
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "git", "-y"], check=True)

        print("[✓] Git successfully installed.")
        return True
    except Exception as e:
        print(f"[X] Failed to install Git: {e}")
        return False

def fetch_latest_from_github(base_dir):
    """Downloads and extracts the private project if app.py is missing."""
    print(f"[*] '{APP_FILENAME}' not found. Initializing private download from GitHub...")
    
    # API endpoint for private repository ZIP archives
    zip_url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/zipball/{GITHUB_SETTINGS['branch']}"
    
    req = urllib.request.Request(zip_url)
    # Add Authorization Header for Private Access
    req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    
    try:
        print(f"[*] Authorizing and fetching: {GITHUB_SETTINGS['repo']}...")
        with urllib.request.urlopen(req) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as zip_ref:
                # GitHub zips include a dynamic top-level folder
                top_folder = zip_ref.namelist()[0]
                
                for member in zip_ref.infolist():
                    if member.filename == top_folder:
                        continue
                    
                    # Strip the top-level folder name from the path
                    filename = Path(member.filename).relative_to(top_folder)
                    target_path = base_dir / filename
                    
                    if member.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zip_ref.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                            
        print("[✓] Private repository successfully synchronized.")
    except Exception as e:
        print(f"[X] Failed to download from Private GitHub: {e}")
        sys.exit(1)

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
    print("[*] Checking Python dependencies...")
    try:
        # Upgrade pip first to avoid installation issues
        subprocess.check_call([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
        # Install the defined REQUIREMENTS list
        subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)
        print("[✓] Dependencies are up to date.")
    except Exception as e:
        print(f"[X] Error installing dependencies: {e}")

def install_homebrew():
    """Installs Homebrew on macOS if not already present."""
    if shutil.which("brew"):
        return True
    
    print("[*] Homebrew not found. Installing Homebrew (this may take a while)...")
    try:
        # Official Homebrew installation command
        install_cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        # This will trigger a macOS password prompt for sudo access
        subprocess.run(install_cmd, shell=True, check=True)
        
        # Add brew to path for the current session based on processor architecture
        if platform.machine() == "arm64": # Apple Silicon
            os.environ["PATH"] += ":/opt/homebrew/bin"
        else: # Intel
            os.environ["PATH"] += ":/usr/local/bin"
            
        return True
    except Exception as e:
        print(f"[X] Homebrew installation failed: {e}")
        return False
    
def install_speedtest_cli(bin_dir):
    """Automated installation of Speedtest CLI for all platforms without manual steps."""
    system = platform.system()
    
    if system == "Windows":
        url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"
        target = bin_dir / "speedtest.exe"
        if not target.exists():
            print("[*] Installing Speedtest CLI for Windows...")
            try:
                urllib.request.urlretrieve(url, bin_dir / "st.zip")
                with zipfile.ZipFile(bin_dir / "st.zip", 'r') as z: 
                    z.extract("speedtest.exe", bin_dir)
                os.remove(bin_dir / "st.zip")
            except: pass

    elif system == "Darwin":
        if not shutil.which("speedtest"):
            print("[*] Installing Speedtest CLI via Homebrew...")
            # First ensure Brew is installed, then tap and install Ookla
            if install_homebrew():
                try:
                    subprocess.run(["brew", "tap", "teamookla/speedtest"], check=True)
                    subprocess.run(["brew", "install", "speedtest"], check=True)
                except Exception as e:
                    print(f"[X] macOS Speedtest install failed: {e}")

    elif system == "Linux":
        if not shutil.which("speedtest"):
            print("[*] Installing Speedtest CLI via official Apt repository...")
            try:
                # Automates repo addition and installation; triggers sudo prompt
                subprocess.run("curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash", shell=True, check=True)
                subprocess.run(["sudo", "apt-get", "install", "speedtest"], check=True)
            except Exception as e:
                print(f"[X] Linux Speedtest install failed: {e}")

def run_application(base_dir, venv_python):
    """Launches the main app with required privileges."""
    app_path = base_dir / APP_FILENAME
    print("\n" + "="*60)
    print(f"   LAUNCHING DASHBOARD (Setup v{SETUP_VERSION})")
    print("="*60)
    
    # Use 'sudo' on Mac/Linux for network scanning (ARP) permissions
    cmd = [str(venv_python), str(app_path)]
    if platform.system() != "Windows":
        print("[*] Elevated privileges required for network scanning.")
        cmd = ["sudo"] + cmd
        
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")

def install_npcap_windows():
    """Windows: Checks for Npcap and attempts installation via Chocolatey if missing."""
    if platform.system() != "Windows":
        return

    # Check if Npcap driver exists in System32
    npcap_exists = os.path.exists(os.environ.get('SystemRoot', 'C:\\Windows') + "\\System32\\Npcap")
    
    if not npcap_exists:
        print("[*] Npcap not detected. This is required for Network Scanning on Windows.")
        if shutil.which("choco"):
            print("[*] Installing Npcap via Chocolatey...")
            try:
                subprocess.run(["choco", "install", "npcap", "-y"], check=True)
                print("[✓] Npcap installed. You may need to restart your terminal.")
            except Exception as e:
                print(f"[!] Automated Npcap install failed: {e}")
        else:
            print("[!] Please manually install Npcap from https://npcap.com/ to enable scanning.")

def is_admin():
    """Checks if the script is running with administrative privileges."""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.getuid() == 0
    except AttributeError:
        return False

def run_application(base_dir, venv_python):
    """Launches the main app."""
    app_path = base_dir / APP_FILENAME
    print("\n" + "="*60)
    print(f"   LAUNCHING DASHBOARD (Setup v{SETUP_VERSION})")
    print("="*60)
    
    # Prerequisite check: is_admin should have been handled by main()
    # but we keep a check here for robustness.
    cmd = [str(venv_python), str(app_path)]
    
    try:
        # On Linux/Mac, if we aren't root, prepend sudo
        if platform.system() != "Windows" and not is_admin():
            print("[*] Re-launching with sudo for network scanning permissions...")
            subprocess.run(["sudo"] + cmd)
        else:
            subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")

def main():
    # Detect the script's directory
    base_dir = Path(__file__).parent.resolve()
    print(f"--- Network Diagnostics Setup Utility v{SETUP_VERSION} ---")

    # --- 1. ADMIN PRIVILEGE ELEVATION ---
    if platform.system() == "Windows":
        if not is_admin():
            print("[*] Requesting Administrative privileges...")
            # Relaunch the script with admin rights
            script = os.path.abspath(__file__)
            params = ' '.join([script] + sys.argv[1:])
            try:
                # 'runas' triggers the UAC prompt
                ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
                sys.exit(0)
            except Exception as e:
                print(f"[X] Failed to elevate: {e}")
                sys.exit(1)
    else:
        # For Linux/Mac, we handle elevation during the run_application phase via sudo
        pass

    install_git()

    # --- 2. BOOTSTRAP: Fetch project files if app.py is missing ---
    if not (base_dir / APP_FILENAME).exists():
        fetch_latest_from_github(base_dir)
    
    # --- 3. SETUP VENV ---
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # --- 4. INSTALL DEPENDENCIES ---
    install_requirements(paths["python"])
    
    if platform.system() == "Windows":
        install_npcap_windows()

    # --- 5. INSTALL SPEEDTEST BINARY ---
    install_speedtest_cli(paths["bin_dir"])
    
    # --- 6. LAUNCH APPLICATION ---
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()