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
import socket
import time
import webbrowser
from pathlib import Path

# --- Configuration ---
SETUP_VERSION = "0.8.0"
VENV_DIR_NAME = "venv"

BASE_REQUIREMENTS = ["flask", "psutil", "scapy", "waitress"]

# macOS-specific requirement for CoreWLAN Wi-Fi scanning
if platform.system() == "Darwin":
    BASE_REQUIREMENTS.extend([
        "pyobjc-framework-CoreWLAN",
        "pyobjc-framework-CoreLocation"
    ])

REQUIREMENTS = BASE_REQUIREMENTS

APP_FILENAME = "app.py"

# GITHUB PRIVATE REPO CONFIGURATION
GITHUB_SETTINGS = {
    "owner": "Chrisb003",
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
            # Call our new Chocolatey installer
            if install_chocolatey():
                subprocess.run(["choco", "install", "git", "-y"], check=True)
            else:
                print("[!] Chocolatey installation failed. Please install Git manually.")
                return False
        elif system == "Darwin": # macOS
            if install_homebrew():
                subprocess.run(["brew", "install", "git"], check=True)
        elif system == "Linux":
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
    """Downloads and extracts the private project and sets full permissions."""
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
                        fix_permissions(target_path) # Set folder permissions
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zip_ref.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                        fix_permissions(target_path) # Set file permissions
                        
        print("[✓] Project files synchronized and permissions set.")
    except Exception as e:
        print(f"[X] Failed to download from GitHub: {e}")
        sys.exit(1)

def create_venv(base_dir):
    """Creates the virtual environment and links standalone macOS libraries."""
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists():
        print(f"[*] Creating virtual environment (Setup v{SETUP_VERSION})...")
        try:
            venv.create(venv_path, with_pip=True, clear=True)
            
            # FIX FOR MACOS STANDALONE PYTHON VENV LINKING
            if platform.system() == "Darwin":
                local_lib = base_dir / "local_python" / "lib"
                venv_lib = venv_path / "lib"
                if local_lib.exists():
                    venv_lib.mkdir(parents=True, exist_ok=True)
                    for file in local_lib.glob("libpython*.dylib"):
                        dest = venv_lib / file.name
                        if not dest.exists():
                            shutil.copy2(file, dest)
                            print(f"[✓] Linked shared library for macOS standalone python: {file.name}")
                            
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
        
        # Log what we are installing
        if platform.system() == "Darwin":
            print("[*] macOS detected: Including CoreWLAN framework bindings.")
            
        subprocess.check_call([str(python_path), "-m", "pip", "install"] + REQUIREMENTS)
        print("[✓] Dependencies installed.")
    except subprocess.CalledProcessError as e:
        print(f"[X] Error installing dependencies: {e}")
        if platform.system() == "Linux":
            print("[!] Suggestion: Run 'sudo apt-get install python3-dev build-essential' and try again.")
        sys.exit(1)

def install_speedtest_cli(bin_dir):
    """
    Installs Speedtest CLI and sets full Read/Write/Execute permissions.
    Includes dynamic architecture detection for Linux (x86 vs ARM).
    """
    system = platform.system()
    machine = platform.machine().lower()
    target_path = bin_dir / ("speedtest.exe" if system == "Windows" else "speedtest")
    
    if target_path.exists():
        print("[✓] Speedtest CLI already installed.")
        return

    print(f"[*] Attempting to install Speedtest CLI for {system} ({machine})...")

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

        # 2. LINUX INSTALLATION (With Multi-Arch Support)
        elif system == "Linux":
            # Dynamic URL Selection based on Architecture
            if "arm" in machine or "aarch64" in machine:
                print(f"[*] Architecture detected: ARM/Raspberry Pi")
                url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-aarch64.tgz"
            else:
                print(f"[*] Architecture detected: x86_64")
                url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-x86_64.tgz"
            
            tgz_path = bin_dir / "st.tgz"
            urllib.request.urlretrieve(url, tgz_path)
            
            with tarfile.open(tgz_path, "r:gz") as tar:
                tar.extract("speedtest", path=bin_dir)
            os.remove(tgz_path)

        # 3. MACOS INSTALLATION
        elif system == "Darwin":
            if not shutil.which("speedtest"):
                if install_homebrew():
                    subprocess.run(["brew", "tap", "teamookla/speedtest"], check=True)
                    subprocess.run(["brew", "install", "speedtest"], check=True)
                else:
                    raise Exception("Homebrew not found.")

        # --- THE MISSING PERMISSION FIX ---
        if target_path.exists():
            fix_permissions(target_path)  # Sets R/W/X for all users
            print("[✓] Speedtest CLI installed successfully with full permissions.")
        elif shutil.which("speedtest"):
             print("[✓] Speedtest CLI found in system path.")
        else:
            raise Exception("Binary not found after installation attempt.")

    except Exception as e:
        print(f"\n[!] SPEEDTEST CLI INSTALLATION FAILED: {e}")
        print("="*60)
        print("    Automatic installation failed. Please install manually:")
        print("    1. Download the CLI for your OS: https://www.speedtest.net/apps/cli")
        print(f"    2. Extract the 'speedtest' binary into this folder:")
        print(f"       {bin_dir}")
        print("    3. Ensure you set execute permissions (chmod +x speedtest)")
        print("="*60 + "\n")

def install_npcap_windows():
    """Checks/Installs Npcap on Windows."""
    if platform.system() != "Windows": return
    
    sys_root = os.environ.get('SystemRoot', 'C:\\Windows')
    if not os.path.exists(os.path.join(sys_root, "System32", "Npcap")):
        print("[*] Npcap missing. Attempting installation via Chocolatey...")
        
        # Call our new Chocolatey installer
        if install_chocolatey():
            try:
                subprocess.run(["choco", "install", "npcap", "-y"], check=True)
                print("[✓] Npcap successfully installed.")
            except Exception as e: 
                print(f"[!] Npcap install failed: {e}")
        else:
            print("[!] Please manually install Npcap from https://npcap.com/")

def run_application(base_dir, venv_python):
    """Launches the main app and waits for the server to be ready before opening the browser."""
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
        # Start the app as a subprocess so we can monitor it
        process = subprocess.Popen(cmd)
        
        print("[*] Waiting for the server to spin up...")
        
        # Check if port 81 is open, trying once per second for up to 60 seconds
        server_ready = False
        for _ in range(60):
            try:
                # Attempt to connect to the local port
                with socket.create_connection(("127.0.0.1", 81), timeout=1):
                    server_ready = True
                    break
            except (ConnectionRefusedError, TimeoutError, OSError):
                time.sleep(1)
        
        if server_ready:
            print("[✓] Server is ready! Opening browser...")
            webbrowser.open("http://127.0.0.1:81")
        else:
            print("[!] Could not verify server status. You can try opening http://127.0.0.1:81 manually.")

        # Keep this setup script open as long as the dashboard is running
        process.wait()
        
    except KeyboardInterrupt:
        print("\n[!] Dashboard stopped by user.")

def fix_permissions(path):
    """
    Sets path to full Read/Write/Execute for all users.
    Returns True on success, False on failure.
    """
    try:
        if platform.system() == "Windows":
            # Grant 'Everyone' group Full Control (F)
            res = subprocess.run(['icacls', str(path), '/grant', 'Everyone:(F)'], capture_output=True)
            return res.returncode == 0
        else:
            # Linux/Mac: 0o777 is rwxrwxrwx
            os.chmod(path, 0o777)
            return True
    except:
        return False

def install_chocolatey():
    """Installs Chocolatey on Windows if not present and updates the current PATH."""
    if platform.system() != "Windows":
        return True
        
    if shutil.which("choco"):
        return True
        
    print("[*] Chocolatey not found. Installing Chocolatey...")
    try:
        # Standard Chocolatey PowerShell installation command
        ps_command = (
            "Set-ExecutionPolicy Bypass -Scope Process -Force; "
            "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; "
            "iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
        )
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command], check=True)
        
        # Add Chocolatey bin to the current Python process PATH so it can be used immediately
        choco_path = os.path.join(os.environ.get('ALLUSERSPROFILE', 'C:\\ProgramData'), 'chocolatey', 'bin')
        if choco_path not in os.environ["PATH"]:
            os.environ["PATH"] = choco_path + os.pathsep + os.environ["PATH"]
            
        print("[✓] Chocolatey successfully installed.")
        return True
    except Exception as e:
        print(f"[X] Failed to install Chocolatey: {e}")
        return False

def has_internet():
    """Checks for internet connectivity by attempting to reach Google DNS."""
    try:
        # Timeout set to 2 seconds to avoid long hangs
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False

def main():
    base_dir = Path(__file__).parent.resolve()
    print(f"--- Network Diagnostics Setup Utility v{SETUP_VERSION} ---")

    # Flag to track if we need to run the final permission loop
    needs_permission_fix = False

    # 1. INTERNET CONNECTIVITY CHECK
    online = has_internet()
    if not online:
        print("\n" + "!" * 60)
        print("[!] No Internet: Requirements and dependency checks skipped.")
        print("    If this is the first time launching this, connect to the")
        print("    internet and run again to ensure all components are installed.")
        print("!" * 60 + "\n")
    
    # 2. WINDOWS ADMIN CHECK
    if platform.system() == "Windows" and not is_admin():
        print("[*] Requesting Administrative privileges...")
        params = ' '.join([os.path.abspath(__file__)] + sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)

    # 3. RUN ONLINE-ONLY TASKS
    if online:
        ensure_linux_prerequisites()
        install_git()
        # If the main app file doesn't exist, we download it and trigger the permission flag
        if not (base_dir / APP_FILENAME).exists():
            fetch_latest_from_github(base_dir)
            needs_permission_fix = True 
    
    # 4. ENVIRONMENT SETUP
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists() and not online:
        print("[X] ERROR: No virtual environment found and no internet to create one.")
        sys.exit(1)
        
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # 5. INSTALLATION
    if online:
        install_requirements(paths["python"])
        if platform.system() == "Windows":
            install_npcap_windows()
        install_speedtest_cli(paths["bin_dir"])
    
    # 6. CONDITIONAL PERMISSION FIX
    # Only runs if files were just downloaded from GitHub
    if needs_permission_fix:
        print("[*] New files detected. Verifying file system permissions...")
        success_count = 0
        fail_count = 0
        for root, dirs, files in os.walk(base_dir):
            if ".git" in dirs: dirs.remove(".git")
            for d in dirs:
                if fix_permissions(os.path.join(root, d)): success_count += 1
                else: fail_count += 1
            for f in files:
                if any(x in f for x in [".db-shm", ".db-wal", "python3", "python.exe"]): continue
                if fix_permissions(os.path.join(root, f)): success_count += 1
                else:
                    if not f.startswith("."): print(f"[!] Warning: Could not set permissions for {f}")
                    fail_count += 1

        if fail_count == 0:
            print(f"[✓] Permission check complete. All {success_count} items verified.")
        else:
            print(f"[!] Permission check finished: {success_count} succeeded, {fail_count} skipped/failed.")
    else:
        print("[✓] Skipping permission check (No new files downloaded).")

    # 7. LAUNCH
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()