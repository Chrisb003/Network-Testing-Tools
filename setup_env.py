import os
import sys
import subprocess
import venv
import platform
import shutil
import urllib.request
import zipfile
import tarfile
import io
import ctypes
import stat
import socket
import time
import webbrowser
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta

# --- Configuration ---
SETUP_VERSION = "0.15.0"
VENV_DIR_NAME = "venv"

BASE_REQUIREMENTS = ["flask", "psutil", "scapy", "waitress", "pystray", "Pillow"]

# macOS-specific requirement for CoreWLAN Wi-Fi scanning
if platform.system() == "Darwin":
    BASE_REQUIREMENTS.extend([
        "pyobjc-framework-CoreWLAN",
        "pyobjc-framework-CoreLocation",
        "pyobjc-framework-Quartz"
    ])

REQUIREMENTS = BASE_REQUIREMENTS
APP_FILENAME = "app.py"

# --- MASTER FILE LIST ---
# Protects these files from the Isolation/Self-Containment security checks.
KNOWN_APP_ITEMS = {
    "setup_env.py", "app.py", "version.json", "github_settings.json", 
    "README.md", "Changelog", "templates", "static", "logs", "backups", 
    "venv", "network_data.db", "network_data.db-wal", "network_data.db-shm", 
    "autostart", "webport", "dev", "cleardatabase", "passwordreset", 
    "reinstall", "rollback.zip", "boot_attempts.txt", "workers", 
    "local_python", "setup_env_new.py", ".gitignore", "NetworkDiagnostics",
    "standalone", "shutdown_signal", "system_alerts.json", "disablecleanup", 
    "install.md", "Windows-Installer.bat", "Windows-Installer.ps1", 
    "Linux-Installer.sh", "MacOS-Installer.sh", "Install scripts"
}

# GITHUB DEFAULT FALLBACK CONFIGURATION
DEFAULT_GITHUB_CONFIG = {
    "stable": {
        "display_name": "Production (Stable)",
        "owner": "Chrisb003",
        "repo": "Network-Testing-Tools",
        "token": "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",
        "branch": "main"
    },
    "dev": {
        "display_name": "Development (Dev)",
        "owner": "Chrisb003",
        "repo": "Network-Testing-Tools",
        "token": "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",
        "branch": "dev"
    }
}

def get_foreign_items(base_dir):
    """
    Scans the directory and returns a list of items that do NOT belong to this application.
    Smart enough to ignore dynamically generated backups, caches, and databases.
    """
    current_items = set(os.listdir(base_dir))
    foreign = []
    
    for item in current_items:
        # 1. Ignore hidden files and python caches
        if item.startswith('.') or item == "__pycache__":
            continue
        # 2. Ignore explicitly known app files/folders
        if item in KNOWN_APP_ITEMS:
            continue
        # 3. Ignore dynamically generated backups/old files
        if item.endswith('.old') or item.endswith('.bak') or item.endswith('.back'):
            continue
            
        # If it reaches here, it's genuinely a foreign file
        foreign.append(item)
        
    return foreign

def is_dev_build(base_dir):
    """Determines if the system is on the DEV channel to route database and GitHub checks."""
    if (base_dir / "dev").exists():
        return True
    version_file = base_dir / "version.json"
    if version_file.exists():
        try:
            import json
            with open(version_file, "r") as f:
                data = json.load(f)
                if "DEV" in data.get("version", "").upper(): return True
        except: pass
    return False

def ensure_github_settings(base_dir):
    """Creates the github_settings.json file if it is missing."""
    settings_file = base_dir / "github_settings.json"
    if not settings_file.exists():
        try:
            import json
            with open(settings_file, "w") as f:
                json.dump(DEFAULT_GITHUB_CONFIG, f, indent=4)
        except Exception as e:
            print(f"[*] Failed to create github_settings.json: {e}")

def get_active_github_settings(base_dir):
    """Reads the active settings from github_settings.json or the setup defaults."""
    ensure_github_settings(base_dir)
    settings_file = base_dir / "github_settings.json"
    channel = "dev" if is_dev_build(base_dir) else "stable"
    
    try:
        import json
        with open(settings_file, "r") as f:
            data = json.load(f)
            if channel in data:
                return data[channel]
    except: pass
    
    return DEFAULT_GITHUB_CONFIG.get(channel, DEFAULT_GITHUB_CONFIG["stable"])


def setup_supervisor_logging(base_dir):
    """Initializes a dual-logger for the setup script and sets the environment sync variable."""
    log_dir = base_dir / 'logs'
    try:
        log_dir.mkdir(exist_ok=True)
        os.chmod(log_dir, 0o777) # Ensure normal users can access the folder
        fix_permissions(log_dir) # Force permissions on the log folder via OS
    except: pass

    # 1. Clean up old logs (older than 7 days)
    cutoff_date = datetime.now() - timedelta(days=7)
    for log_file in log_dir.glob('*.log'):
        try:
            if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff_date:
                log_file.unlink()
        except: pass

    # 2. Check Database for Full Logging preference
    full_log = False
    db_path = base_dir / "network_data.db"
    try:
        if db_path.exists():
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                row = conn.execute("SELECT value FROM system_settings WHERE key='full_logging'").fetchone()
                if row and row[0] == '1': full_log = True
    except: pass
    os.environ["APP_FULL_LOGGING"] = "1" if full_log else "0"

    # 3. Generate synchronized timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    os.environ["APP_LOG_TIME"] = timestamp
    log_path = log_dir / f"system_run_{timestamp}.log"

    # 4. TeeLogger to print to terminal AND unconditionally write to file
    class TeeLogger:
        def __init__(self, filename, terminal, is_stderr=False):
            self.terminal = terminal
            self.is_stderr = is_stderr
            self.file = None
            try:
                self.file = open(filename, 'a', encoding='utf-8')
                os.chmod(filename, 0o666) # Ensure everyone can read/write to the log
                fix_permissions(filename) # Force R/W/X for all users on the new log
            except PermissionError:
                pass # If owned by root, silently skip file logging for setup script
            except Exception:
                pass
            
        def write(self, text):
            # Always print to the terminal so the user can see what's happening
            try:
                self.terminal.write(text)
                self.terminal.flush()
            except: pass
            
            # Unconditionally log all setup script actions
            if self.file:
                try:
                    self.file.write(text)
                    self.file.flush()
                except: pass
                
        def flush(self):
            try: self.terminal.flush()
            except: pass
            if self.file:
                try: self.file.flush()
                except: pass

    sys.stdout = TeeLogger(log_path, sys.stdout, is_stderr=False)
    sys.stderr = TeeLogger(log_path, sys.stderr, is_stderr=True)

def is_admin():
    """Checks if the script is running with administrative privileges."""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.getuid() == 0
    except:
        return False

def get_autostart_setting(base_dir):
    """Reads the autostart file, creates it with '1' if missing."""
    autostart_file = base_dir / "autostart"
    if not autostart_file.exists():
        with open(autostart_file, "w") as f:
            f.write("1")
        return True
    try:
        with open(autostart_file, "r") as f:
            return f.read(10).strip() == "1" # Limit read
    except:
        return True

def ensure_linux_prerequisites():
    """
    Ensures Linux systems have the necessary Python build tools 
    and venv modules installed BEFORE trying to create the virtual environment.
    Supports APT (Debian/Ubuntu), DNF (Fedora/RHEL), Pacman (Arch), and Zypper (openSUSE).
    """
    if platform.system() == "Linux":
        print("[*] Checking Linux system prerequisites...")
        
        try:
            if shutil.which("apt-get"):
                print("[*] Updating package lists (APT)...")
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                print("[*] Installing Python build tools and venv...")
                subprocess.run(["sudo", "apt-get", "install", "-y", "python3-venv", "python3-pip", "python3-dev", "build-essential", "git", "net-tools"], check=True)
            elif shutil.which("dnf"):
                print("[*] Installing Python build tools and venv (DNF)...")
                subprocess.run(["sudo", "dnf", "install", "-y", "python3", "python3-pip", "python3-devel", "gcc", "git", "net-tools"], check=True)
            elif shutil.which("pacman"):
                print("[*] Installing Python build tools and venv (Pacman)...")
                subprocess.run(["sudo", "pacman", "-Syu", "--noconfirm", "python", "python-pip", "base-devel", "git", "net-tools"], check=True)
            elif shutil.which("zypper"):
                print("[*] Installing Python build tools and venv (Zypper)...")
                subprocess.run(["sudo", "zypper", "install", "-y", "python3", "python3-pip", "python3-devel", "gcc", "git", "net-tools"], check=True)
            else:
                print("[!] Warning: Unknown package manager. Please ensure Python 3, venv, and build tools are installed.")
                return
                
            print("[✓] Linux prerequisites installed.")
        except subprocess.CalledProcessError as e:
            print(f"[!] Warning: Failed to install Linux prerequisites automatically: {e}")

def install_git():
    """Checks for Git and installs it if missing."""
    if shutil.which("git"):
        return True

    system = platform.system()
    print(f"[*] Git not detected. Attempting automated installation for {system}...")

    try:
        if system == "Windows":
            if install_chocolatey():
                subprocess.run(["choco", "install", "git", "-y"], check=True)
            else:
                print("[!] Chocolatey installation failed. Please install Git manually.")
                return False
        elif system == "Darwin": # macOS
            if install_homebrew():
                subprocess.run(["brew", "install", "git"], check=True)
        elif system == "Linux":
            if shutil.which("apt-get"):
                subprocess.run(["sudo", "apt-get", "install", "-y", "git"], check=True)
            elif shutil.which("dnf"):
                subprocess.run(["sudo", "dnf", "install", "-y", "git"], check=True)
            elif shutil.which("pacman"):
                subprocess.run(["sudo", "pacman", "-S", "--noconfirm", "git"], check=True)
            elif shutil.which("zypper"):
                subprocess.run(["sudo", "zypper", "install", "-y", "git"], check=True)
        
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
    
    # Use the dynamic settings instead of hardcoding
    gh_set = get_active_github_settings(base_dir)
    
    zip_url = f"https://api.github.com/repos/{gh_set['owner']}/{gh_set['repo']}/zipball/{gh_set['branch']}"
    req = urllib.request.Request(zip_url)
    req.add_header("Authorization", f"token {gh_set['token']}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    
    try:
        print(f"[*] Authorizing and fetching: {gh_set['repo']}...")
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
            if "aarch64" in machine or "arm64" in machine:
                print("[*] Architecture detected: ARM 64-bit")
                url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-aarch64.tgz"
            elif "arm" in machine:
                print("[*] Architecture detected: ARM 32-bit")
                url = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-armhf.tgz"
            else:
                print("[*] Architecture detected: x86_64")
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
    """Launches the main app, force-stops any hung instances, and auto-restarts on crash."""
    app_path = base_dir / APP_FILENAME
    print("\n" + "="*60)
    print(f"   LAUNCHING DASHBOARD SUPERVISOR (Setup v{SETUP_VERSION})")
    print("="*60)
    
    cmd = [str(venv_python), str(app_path)]
    
    # On Linux/Mac, we need sudo for Scapy to read ARP tables
    if platform.system() != "Windows" and not is_admin():
        print("[*] Elevating privileges for network scanning...")
        cmd = ["sudo"] + cmd
        
    first_launch = True
    
    try:
        while True:
            current_port = get_configured_port(base_dir)
            
            # --- FORCE STOP / CLEANUP: Kill any stale process blocking the port natively ---
            try:
                if platform.system() == "Windows":
                    out = subprocess.check_output(f"netstat -ano | findstr :{current_port}", shell=True, text=True)
                    for line in out.strip().split('\n'):
                        if "LISTENING" in line and f":{current_port}" in line.split()[1]:
                            pid = line.strip().split()[-1]
                            print(f"[*] Force-stopping stale process on port {current_port} (PID: {pid})...")
                            subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    out = subprocess.check_output(f"lsof -t -i:{current_port}", shell=True, text=True)
                    for pid in out.strip().split('\n'):
                        if pid:
                            print(f"[*] Force-stopping stale process on port {current_port} (PID: {pid})...")
                            subprocess.run(f"kill -9 {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            print(f"[*] Starting main application instance...")
            process = subprocess.Popen(cmd)
            
            print(f"[*] Waiting for server to spin up on port {current_port}...")
            server_ready = False
            for _ in range(30):
                current_port = get_configured_port(base_dir)
                try:
                    with socket.create_connection(("127.0.0.1", current_port), timeout=1):
                        server_ready = True
                        break
                except (ConnectionRefusedError, TimeoutError, OSError):
                    time.sleep(1)
            
            if server_ready and first_launch:
                if get_autostart_setting(base_dir):
                    print(f"[*] Server is ready on port {current_port}! Opening browser...")
                    webbrowser.open(f"http://127.0.0.1:{current_port}")
                else:
                    print(f"\n[*] Server is ready! Access it manually at: http://127.0.0.1:{current_port}\n")
                first_launch = False
            elif server_ready:
                print(f"\n[*] Application successfully restarted on port {current_port}.\n")
            
            # Wait for the application process to terminate or crash
            process.wait()
            
            # --- Check for intentional shutdown signal ---
            shutdown_file = base_dir / "shutdown_signal"
            if shutdown_file.exists():
                print("\n[*] Intentional shutdown signal received. Stopping supervisor safely.")
                try:
                    shutdown_file.unlink() # Clean up the file
                except OSError:
                    # Fallback for Linux/macOS if the file is locked by Root
                    if platform.system() != "Windows":
                        subprocess.run(["sudo", "rm", "-f", str(shutdown_file)], stderr=subprocess.DEVNULL)
                sys.exit(0) # Exit the supervisor loop completely
            
            # If we reach here and no signal exists, it was a legitimate crash
            print(f"\n[!] WARNING: Main application stopped unexpectedly (Exit code: {process.returncode}).")
            print("[*] Restarting application loop in 3 seconds...\n")
            
            # Ensure the dead process is fully cleaned up before looping
            try:
                process.kill()
            except:
                pass
                
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n[*] Dashboard supervisor stopped by user.")

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

def fix_permissions_bulk(base_path):
    """Recursively sets Read/Write/Execute permissions for the entire directory rapidly."""
    try:
        if platform.system() == "Windows":
            subprocess.run(['icacls', str(base_path), '/grant', 'Everyone:(F)', '/T', '/C', '/Q'], capture_output=True)
        else:
            subprocess.run(['sudo', 'chmod', '-R', '777', str(base_path)], stderr=subprocess.DEVNULL)
    except:
        pass

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

def get_configured_port(base_dir):
    """Reads the configured port from the DB or the 'webport' override file, defaulting to 81."""
    # Check if the override file is waiting to be processed
    port_file = base_dir / "webport"
    if port_file.exists():
        try:
            with open(port_file, "r") as f:
                val = f.read(10).strip() # Limit read
                if val.isdigit() and 1 <= int(val) <= 65535: 
                    return int(val)
        except: pass
        
    # Check the database
    db_path = base_dir / "network_data.db"
    if db_path.exists():
        try:
            import sqlite3
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                row = conn.execute("SELECT value FROM system_settings WHERE key='web_port'").fetchone()
                if row: return int(row[0])
        except: pass
        
    return 81

def main():
    base_dir = Path(__file__).parent.resolve()

    # 1. WINDOWS ADMIN CHECK (MUST HAPPEN FIRST to prevent permission errors on cleanup)
    if platform.system() == "Windows" and not is_admin():
        print("[*] Requesting Administrative privileges...")
        params = ' '.join([os.path.abspath(__file__)] + sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)

    # 2. CLEANUP STALE SHUTDOWN SIGNAL
    shutdown_file = base_dir / "shutdown_signal"
    if shutdown_file.exists():
        try:
            shutdown_file.unlink()
            print("[*] Cleared stale shutdown signal from a previous session.")
        except OSError:
            # Fallback for Linux/macOS if the file is locked by Root
            if platform.system() != "Windows":
                subprocess.run(["sudo", "rm", "-f", str(shutdown_file)], stderr=subprocess.DEVNULL)
                print("[*] Cleared stale shutdown signal (via sudo).")

    # 3. VENV EXECUTION SAFEGUARD & REINSTALL TRIGGERS
    reinstall_file = base_dir / "reinstall"
    needs_factory_reset = reinstall_file.exists()
    disable_cleanup = (base_dir / "disablecleanup").exists()
    
    needs_isolation = False
    if not (base_dir / APP_FILENAME).exists() and not disable_cleanup:
        foreign_items = get_foreign_items(base_dir)
        if foreign_items:
            needs_isolation = True

    venv_dir_full = (base_dir / VENV_DIR_NAME).resolve()
    is_in_venv = str(venv_dir_full) in str(Path(sys.executable).resolve())

    if is_in_venv and (needs_factory_reset or needs_isolation):
        # Extremely robust debugger detection via active modules
        is_debugging = sys.gettrace() is not None or 'debugpy' in sys.modules or 'pydevd' in sys.modules
        if is_debugging:
            print("\n[!] DEBUGGER DETECTED DURING A DESTRUCTIVE BOOT!")
            print("[!] The script needs to delete/move the 'venv' folder for a Factory Reset or File Isolation.")
            print("[!] This will crash your active debugger due to file locks on Windows.")
            print("[!] Please run the script normally (without debugging) to complete this action.\n")
            sys.exit(1)

        system_py = shutil.which("python3") or shutil.which("python")
        if system_py and Path(system_py).resolve() != Path(sys.executable).resolve():
            print("[*] Destructive boot detected. Re-launching with system Python to release venv file locks...")
            os.execv(system_py, [system_py] + sys.argv)

    # --- Reinstall / Factory Reset Handler ---
    if reinstall_file.exists():
        print("[*] 'reinstall' trigger detected! Initiating complete factory reset...")
        
        # Force Production (Stable/Main) channel configuration
        prod_config = DEFAULT_GITHUB_CONFIG["stable"]
        
        print(f"[*] Downloading latest production release from {prod_config['repo']} ({prod_config['branch']})...")
        zip_url = f"https://api.github.com/repos/{prod_config['owner']}/{prod_config['repo']}/zipball/{prod_config['branch']}"
        req = urllib.request.Request(zip_url)
        if prod_config.get('token'):
            req.add_header("Authorization", f"token {prod_config['token']}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        
        try:
            with urllib.request.urlopen(req) as response:
                zip_data = io.BytesIO(response.read())
            
            with zipfile.ZipFile(zip_data) as zip_ref:
                top_folder = zip_ref.namelist()[0]
                
                # Stage files: Extract everything except setup_env.py first
                for member in zip_ref.infolist():
                    if member.filename == top_folder: continue
                    filename = Path(member.filename).relative_to(top_folder)
                    
                    if filename.name == "setup_env.py":
                        setup_target_staging = base_dir / "setup_env_new.py"
                        with zip_ref.open(member) as source, open(setup_target_staging, "wb") as target:
                            shutil.copyfileobj(source, target)
                        continue
                        
                    target_path = base_dir / filename
                    if member.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zip_ref.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
            
            # Wipe old runtime environment, database, logs, and backups (Cross-Platform Safe)
            print("[*] Wiping old database, virtual environment, and runtime logs...")
            
            # --- SAFE DELETION LIST ---
            # Explicitly deletes specific heavy components. Ignores standalone/webport configs.
            for target_name in ["network_data.db", "network_data.db-wal", "network_data.db-shm", "venv", "logs", "backups", "rollback.zip", "boot_attempts.txt", "workers", "autostart"]:
                p = base_dir / target_name
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    # Sudo fallback for Linux/Mac if root owns the folders
                    if p.exists() and platform.system() != "Windows":
                        subprocess.run(["sudo", "rm", "-rf", str(p)], stderr=subprocess.DEVNULL)
                elif p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        # Sudo fallback for Linux/Mac if root owns the files
                        if platform.system() != "Windows":
                            subprocess.run(["sudo", "rm", "-f", str(p)], stderr=subprocess.DEVNULL)
            
            # Safely replace setup_env.py last (Handles Windows Execution Locks)
            new_setup_staging = base_dir / "setup_env_new.py"
            target_setup = base_dir / "setup_env.py"
            
            if new_setup_staging.exists():
                try:
                    if target_setup.exists():
                        try:
                            target_setup.unlink()
                        except OSError:
                            # Windows fallback: Rename the currently running script so we can overwrite it
                            target_setup.rename(base_dir / f"setup_env_{int(time.time())}.old")
                    new_setup_staging.rename(target_setup)
                except Exception as e:
                    print(f"[!] Warning: Could not cleanly replace setup_env.py: {e}")
            
            # Clean up trigger file safely
            try:
                reinstall_file.unlink()
            except OSError:
                pass
                
            print("[✓] Factory reset and clean reinstallation completed successfully.")
            
            # --- CRITICAL FIX: RESTART THE SETUP SCRIPT FRESH ---
            print("[*] Restarting setup script with fresh environment...")
            system_py = shutil.which("python3") or shutil.which("python") or sys.executable
            os.execv(system_py, [system_py] + sys.argv)
            
        except Exception as e:
            print(f"[X] Reinstall failed: {e}")
            sys.exit(1)
    
    # --- Start logging immediately ---
    setup_supervisor_logging(base_dir)
    
    print(f"--- Network Diagnostics Setup Utility v{SETUP_VERSION} ---")

    # 4. ENFORCE GLOBAL PERMISSIONS (Runs rapidly on every boot)
    print("[*] Enforcing file system permissions for all users (R/W/X)...")
    fix_permissions_bulk(base_dir)

    # 5. INTERNET CONNECTIVITY CHECK
    online = has_internet()
    if not online:
        print("\n" + "!" * 60)
        print("[!] No Internet: Requirements and dependency checks skipped.")
        print("    If this is the first time launching this, connect to the")
        print("    internet and run again to ensure all components are installed.")
        print("!" * 60 + "\n")

    # 6. RUN ONLINE-ONLY TASKS
    if online:
        ensure_linux_prerequisites()
        install_git()
        
        # Check if the app files are missing and we need to fetch from GitHub
        if not (base_dir / APP_FILENAME).exists():
            
            if not disable_cleanup:
                foreign_items = get_foreign_items(base_dir)
                
                if foreign_items:
                    app_folder = base_dir / "NetworkDiagnostics"
                    app_folder.mkdir(exist_ok=True)
                    print(f"[*] Foreign files detected. Isolating application into: {app_folder}")
                    
                    # ONLY move files that are identified as belonging to our application
                    # This explicitly prevents the isolation script from dragging the user's random files along
                    for item in os.listdir(base_dir):
                        if item not in foreign_items and item != "NetworkDiagnostics" and not item.startswith('.'):
                            src = base_dir / item
                            dst = app_folder / item
                            if src.exists() and not dst.exists():
                                try:
                                    shutil.move(str(src), str(dst))
                                except Exception as e:
                                    print(f"[!] Warning: Could not move {item}: {e}")
                    
                    new_setup = app_folder / "setup_env.py"
                    if not new_setup.exists():
                        shutil.copy2(base_dir / "setup_env.py", new_setup)
                    
                    print(f"[*] Restarting setup script from isolated folder...")
                    os.chdir(app_folder)
                    system_py = shutil.which("python3") or shutil.which("python") or sys.executable
                    subprocess.run([system_py, str(new_setup)] + sys.argv[1:])
                    sys.exit(0)
            else:
                print("[*] 'disablecleanup' detected. Skipping folder isolation safety checks.")
            
            fetch_latest_from_github(base_dir)
            fix_permissions_bulk(base_dir) # Enforce permissions on newly downloaded files
    
    # 7. ENVIRONMENT SETUP
    venv_path = base_dir / VENV_DIR_NAME
    if not venv_path.exists() and not online:
        print("[X] ERROR: No virtual environment found and no internet to create one.")
        sys.exit(1)
        
    venv_path = create_venv(base_dir)
    paths = get_venv_paths(venv_path)
    
    # 8. INSTALLATION
    if online:
        install_requirements(paths["python"])
        if platform.system() == "Windows":
            install_npcap_windows()
        install_speedtest_cli(paths["bin_dir"])
    
    # 9. LAUNCH
    run_application(base_dir, paths["python"])

if __name__ == "__main__":
    main()