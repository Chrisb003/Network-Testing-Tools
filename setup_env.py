import os
import sys
import subprocess
import venv
import platform
import shutil
import urllib.request
import zipfile
import tarfile  # Added for Linux .tgz support
import io
import ctypes
import stat
from pathlib import Path

# --- Configuration ---
SETUP_VERSION = "0.6.2"
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
        
        if shutil.which("apt-get"):
            try:
                # 1. Update Package List
                print("[*] Updating package lists...")
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                
                # 2. Install Critical Dependencies
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
            # Already handled in ensure_linux_prerequisites
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
        # Linux/Mac standard path
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
        subprocess.check_call([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
        subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)
        print("[✓] Dependencies installed.")
    except subprocess.CalledProcessError as e:
        print(f"[X] Error installing dependencies: {e}")
        if platform.system() == "Linux":
            print("[!] Suggestion: Run 'sudo apt-get install python3-dev build-essential' and try again.")
        sys.exit(1)

def install_speedtest_cli(bin_dir):
    """
    Installs Speedtest CLI directly into the virtual environment.
    Includes fallback prompting if automatic installation fails.
    """
    system = platform.system()
    machine = platform.machine().lower()
    
    # Path where we expect the binary to end up in the venv
    target_path = bin_dir / ("speedtest.exe" if system == "Windows" else "speedtest")
    
    # If it already exists, skip
    if target_path.exists():
        print("[✓] Speedtest CLI already installed.")
        return

    print(f"[*] Attempting to install Speedtest CLI for {system}...")

    try:
        # 1. WINDOWS INSTALLATION
        if system == "Windows":
            print("[*] Downloading Speedtest CLI for Windows...")
            url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"
            zip_path = bin_dir / "st.zip"
            
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as z: 
                z.extract("speedtest.exe", bin_dir)
            os.remove(zip_path)

        # 2. LINUX INSTALLATION
        elif system == "Linux":
            print(f"[*] Downloading Speedtest CLI for Linux (x86_64)...")
            # Specific URL as requested
            url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-x86_64.tgz"
            tgz_path = bin_dir / "st.tgz"
            
            urllib.request.urlretrieve(url, tgz_path)
            
            # Extract 'speedtest' binary from tgz to bin_dir
            with tarfile.open(tgz_path, "r:gz") as tar:
                tar.extract("speedtest", path=bin_dir)
            
            os.remove(tgz_path)
            
            # Make Executable (chmod +x)
            st_stat = os.stat(target_path)
            os.chmod(target_path, st_stat.st_mode | stat.S_IEXEC)

        # 3. MACOS INSTALLATION
        elif system == "Darwin":
            # Check for global install since brew installs to /usr/local or /opt/homebrew
            if not shutil.which("speedtest"):
                print("[*] Installing via Homebrew...")
                if install_homebrew(): # Assumes install_homebrew() is defined elsewhere in your script
                    subprocess.run(["brew", "tap", "teamookla/speedtest"], check=True)
                    subprocess.run(["brew", "install", "speedtest"], check=True)
                else:
                    raise Exception("Homebrew not found and could not be installed.")

        # FINAL VERIFICATION
        # Check if the binary exists (either in venv or globally for Mac)
        if target_path.exists() or shutil.which("speedtest"):
            print("[✓] Speedtest CLI installed successfully.")
        else:
            raise Exception("Binary not found after installation attempt.")

    except Exception as e:
        print(f"\n[!] SPEEDTEST CLI INSTALLATION FAILED: {e}")
        print("="*60)
        print("    Automatic installation failed. Please install manually:")
        print("    1. Download the CLI for your OS: https://www.speedtest.net/apps/cli")
        print(f"    2. Extract the 'speedtest' binary into this folder:")
        print(f"       {bin_dir}")
        print("="*60 + "\n")

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