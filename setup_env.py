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
SETUP_VERSION = "0.5.1"
VENV_DIR_NAME = "venv"
REQUIREMENTS = ["flask", "psutil", "scapy"]
APP_FILENAME = "app.py"

# GITHUB PRIVATE REPO CONFIGURATION
GITHUB_SETTINGS = {
    "owner": "Pancool",
    "repo": "Network-Testing-Tools",
    "token": "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",
    "branch": "main"
}

def is_admin():
    """Checks if the script is running with administrative privileges."""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.getuid() == 0
    except:
        return False

def ensure_linux_prerequisites():
    """
    Ensures Ubuntu/Debian systems have the necessary Python build tools 
    and venv modules installed BEFORE trying to create the virtual environment.
    """
    if platform.system() == "Linux":
        print("[*] Checking Linux system prerequisites...")
        
        # Check if we are on a Debian/Ubuntu based system by looking for apt-get
        if shutil.which("apt-get"):
            try:
                # 1. Update Package List
                print("[*] Updating package lists...")
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                
                # 2. Install Critical Dependencies
                # python3-venv: Required to create the venv
                # python3-pip: Required to install packages
                # build-essential & python3-dev: Required to compile psutil/scapy extensions
                print("[*] Installing Python build tools and venv...")
                subprocess.run([
                    "sudo", "apt-get", "install", "-y", 
                    "python3-venv", "python3-pip", "python3-dev", "build-essential", "git"
                ], check=True)
                
                print("[✓] Linux prerequisites installed.")
            except subprocess.CalledProcessError as e:
                print(f"[!] Warning: Failed to install Linux prerequisites: {e}")
                print("    You may need to run: sudo apt-get install python3-venv python3-dev build-essential")

def install_git():
    """Checks for Git and installs it if missing."""
    if shutil.which("git"):
        return True

    system = platform.system()
    print(f"[*] Git not detected. Attempting automated installation for {system}...")

    try:
        if system == "Windows":
            if shutil.which("choco"):
                subprocess.run(["choco", "install", "git", "-y"], check=True)
            else:
                print("[!] Chocolatey not found. Please install Git manually.")
                return False
        elif system == "Darwin": # macOS
            if install_homebrew():
                subprocess.run(["brew", "install", "git"], check=True)
        elif system == "Linux":
            # Already handled in ensure_linux_prerequisites, but safe to double check
            subprocess.run(["sudo", "apt-get", "install", "-y", "git"], check=True)
        
        print("[✓] Git successfully installed.")
        return True
    except Exception as e:
        print(f"[X] Failed to install Git: {e}")
        return False

def install_homebrew():
    """Installs Homebrew on macOS if not already present."""
    if shutil.which("brew"): return True
    
    print("[*] Homebrew not found. Installing...")
    try:
        install_cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        subprocess.run(install_cmd, shell=True, check=True)
        
        if platform.machine() == "arm64": 
            os.environ["PATH"] += ":/opt/homebrew/bin"
        else: 
            os.environ["PATH"] += ":/usr/local/bin"
        return True
    except Exception as e:
        print(f"[X] Homebrew installation failed: {e}")
        return False

def fetch_latest_from_github(base_dir):
    """Downloads and extracts the private project if app.py is missing."""
    print(f"[*] '{APP_FILENAME}' not found. Initializing private download from GitHub...")
    
    zip_url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/zipball/{GITHUB_SETTINGS['branch']}"
    req = urllib.request.Request(zip_url)
    req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    
    try:
        print(f"[*] Authorizing and fetching: {GITHUB_SETTINGS['repo']}...")
        with urllib.request.urlopen(req) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as zip_ref:
                top_folder = zip_ref.namelist()[0]
                for member in zip_ref.infolist():
                    if member.filename == top_folder: continue
                    filename = Path(member.filename).relative_to(top_folder)
                    target_path = base_dir / filename
                    if member.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zip_ref.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
        print("[✓] Project files synchronized.")
    except Exception as e:
        print(f"[X] Failed to download from GitHub: {e}")
        sys.exit(1)

def create_venv(base_dir):
    """Creates the virtual environment."""
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists():
        print(f"[*] Creating virtual environment (Setup v{SETUP_VERSION})...")
        # clear=True ensures we start fresh if a broken venv exists
        try:
            venv.create(venv_path, with_pip=True, clear=True)
        except Exception as e:
            print(f"[X] Failed to create venv: {e}")
            print("    On Linux, ensure 'python3-venv' is installed.")
            sys.exit(1)
    else:
        print(f"[✓] Virtual environment detected.")
    return venv_path

def get_venv_paths(venv_path):
    """Returns executable paths based on OS."""
    if platform.system() == "Windows":
        return {
            "python": venv_path / "Scripts" / "python.exe",
            "bin_dir": venv_path / "Scripts"
        }
    else:
        # On Linux/Mac, the binary is in 'bin' and usually named 'python' or 'python3'
        py_path = venv_path / "bin" / "python"
        if not py_path.exists():
             py_path = venv_path / "bin" / "python3"
             
        return {
            "python": py_path,
            "bin_dir": venv_path / "bin"
        }

def install_requirements(python_path):
    """Installs required Python libraries into the venv."""
    print("[*] Installing Python dependencies...")
    try:
        # 1. Upgrade pip inside the venv
        subprocess.check_call([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
        
        # 2. Install requirements
        # We pass stdout=sys.stdout so you can see the progress on Linux
        subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)
        print("[✓] Dependencies installed.")
    except subprocess.CalledProcessError as e:
        print(f"[X] Error installing dependencies: {e}")
        if platform.system() == "Linux":
            print("[!] Suggestion: Run 'sudo apt-get install python3-dev build-essential' and try again.")
        sys.exit(1)

def install_speedtest_cli(bin_dir):
    """Installs Speedtest CLI."""
    system = platform.system()
    
    if system == "Windows":
        target = bin_dir / "speedtest.exe"
        if not target.exists():
            print("[*] Downloading Speedtest CLI for Windows...")
            url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"
            try:
                urllib.request.urlretrieve(url, bin_dir / "st.zip")
                with zipfile.ZipFile(bin_dir / "st.zip", 'r') as z: 
                    z.extract("speedtest.exe", bin_dir)
                os.remove(bin_dir / "st.zip")
            except: pass

    elif system == "Darwin":
        if not shutil.which("speedtest") and install_homebrew():
            try:
                subprocess.run(["brew", "tap", "teamookla/speedtest"], check=True)
                subprocess.run(["brew", "install", "speedtest"], check=True)
            except: pass

    elif system == "Linux":
        if not shutil.which("speedtest"):
            print("[*] Installing Speedtest CLI via Apt...")
            try:
                subprocess.run("curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash", shell=True, check=True)
                subprocess.run(["sudo", "apt-get", "install", "-y", "speedtest"], check=True)
            except Exception as e:
                print(f"[X] Linux Speedtest install failed: {e}")

def install_npcap_windows():
    """Checks/Installs Npcap on Windows."""
    if platform.system() != "Windows": return
    
    sys_root = os.environ.get('SystemRoot', 'C:\\Windows')
    if not os.path.exists(os.path.join(sys_root, "System32", "Npcap")):
        print("[*] Npcap missing. Installing via Chocolatey...")
        if shutil.which("choco"):
            try:
                subprocess.run(["choco", "install", "npcap", "-y"], check=True)
            except: print("[!] Npcap install failed.")
        else:
            print("[!] Please manually install Npcap from https://npcap.com/")

def run_application(base_dir, venv_python):
    """Launches the main app."""
    app_path = base_dir / APP_FILENAME
    print("\n" + "="*60)
    print(f"   LAUNCHING DASHBOARD (Setup v{SETUP_VERSION})")
    print("="*60)
    
    cmd = [str(venv_python), str(app_path)]
    
    # On Linux/Mac, we need sudo for Scapy to read ARP tables
    if platform.system() != "Windows" and not is_admin():
        print("[*] Elevating privileges for network scanning...")
        cmd = ["sudo"] + cmd
        
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")

def main():
    base_dir = Path(__file__).parent.resolve()
    print(f"--- Network Diagnostics Setup Utility v{SETUP_VERSION} ---")

    # 1. LINUX PRE-REQUISITES (Critical for Ubuntu)
    ensure_linux_prerequisites()

    # 2. WINDOWS ADMIN CHECK
    if platform.system() == "Windows" and not is_admin():
        print("[*] Requesting Administrative privileges...")
        params = ' '.join([os.path.abspath(__file__)] + sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)

    # 3. GIT & FILES
    install_git()
    if not (base_dir / APP_FILENAME).exists():
        fetch_latest_from_github(base_dir)
    
    # 4. ENVIRONMENT SETUP
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # 5. INSTALLATION
    install_requirements(paths["python"])
    
    if platform.system() == "Windows":
        install_npcap_windows()
    
    install_speedtest_cli(paths["bin_dir"])
    
    # 6. LAUNCH
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()