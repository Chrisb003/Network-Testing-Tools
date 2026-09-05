import time
import psutil
import socket
import platform
import subprocess
import json
import sqlite3
import urllib.request
import urllib.error
import csv
import io
import base64
import re
import os
import ipaddress
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, jsonify, Response, request, send_file
from scapy.all import ARP, Ether, srp, conf
import sys
import zipfile
from pathlib import Path
import shutil
import threading
from flask import jsonify
import logging
import tempfile
import re
import base64
import filecmp
from flask import stream_with_context
from concurrent.futures import as_completed
from werkzeug.security import generate_password_hash, check_password_hash
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
conf.verb = 0

# ---------------------------------------------------------
# --- LOGGING SETUP (Redirects ALL terminal output to file) ---
# ---------------------------------------------------------
def setup_file_logging():
    import glob
    from datetime import timedelta
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
        os.chmod(log_dir, 0o777)
    except: pass

    # 1. Delete logs older than 7 days
    cutoff_date = datetime.now() - timedelta(days=7)
    for log_file in glob.glob(os.path.join(log_dir, '*.log')):
        try:
            if datetime.fromtimestamp(os.path.getmtime(log_file)) < cutoff_date:
                os.remove(log_file)
        except: pass

    # 2. Check Database for Logging preferences
    if "APP_FULL_LOGGING" not in os.environ or "APP_DISABLE_ALL_LOGS" not in os.environ:
        full_log = False
        disable_logs = False
        try:
            db_name_log = "network_data.db"
            if os.path.exists(db_name_log):
                with sqlite3.connect(db_name_log, timeout=2.0) as conn:
                    row_f = conn.execute("SELECT value FROM system_settings WHERE key='full_logging'").fetchone()
                    if row_f and row_f[0] == '1': full_log = True
                    
                    row_d = conn.execute("SELECT value FROM system_settings WHERE key='disable_all_logs'").fetchone()
                    if row_d and row_d[0] == '1': disable_logs = True
        except: pass
        os.environ["APP_FULL_LOGGING"] = "1" if full_log else "0"
        os.environ["APP_DISABLE_ALL_LOGS"] = "1" if disable_logs else "0"

    # 3. Create a new log file for this session
    timestamp = os.environ.get("APP_LOG_TIME", datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    log_path = os.path.join(log_dir, f"system_run_{timestamp}.log")

    # 4. TeeLogger to print to terminal AND conditionally write to file
    class TeeLogger:
        def __init__(self, filename, terminal, is_stderr=False):
            self.terminal = terminal
            self.is_stderr = is_stderr
            self.file = None
            try:
                self.file = open(filename, 'a', encoding='utf-8')
                os.chmod(filename, 0o666) # Ensure everyone can read/write to the log
            except Exception: pass
            
        def write(self, text):
            # Print to the live terminal so you can see the startup banners
            try:
                self.terminal.write(text)
                self.terminal.flush()
            except: pass
            
            if self.file:
                # NEW: Completely skip writing to the log file if disabled
                if os.environ.get("APP_DISABLE_ALL_LOGS", "0") == "1":
                    return

                # Dynamically check if we should write this line to the log file
                is_full = os.environ.get("APP_FULL_LOGGING", "0") == "1"
                is_error = self.is_stderr or any(kw in text.lower() for kw in ['[x]', '[!]', 'error', 'failed', 'exception', 'critical', 'traceback', 'warning', 'audit'])

                if is_full or is_error:
                    try:
                        self.file.write(text)
                        self.file.flush() # Ensure live writing
                    except: pass
                
        def flush(self):
            try: self.terminal.flush()
            except: pass
            if self.file:
                try: self.file.flush()
                except: pass

    # Save original terminal outputs before overwriting
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    custom_logger_out = TeeLogger(log_path, original_stdout, is_stderr=False)
    custom_logger_err = TeeLogger(log_path, original_stderr, is_stderr=True)
    sys.stdout = custom_logger_out
    sys.stderr = custom_logger_err

    # 5. Catch internal library logs (Waitress, Flask) and pipe them to the file too
    logging.basicConfig(
        stream=custom_logger_out,
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
setup_file_logging()

# --- Configuration ---
APP_VERSION = "0.12.0"

# Chrome, Firefox, and Edge restricted ports
RESTRICTED_PORTS = {87, 512, 513, 514, 515, 6000, 6665, 6666, 6667, 6668, 6669}


def get_global_version():
    """Reads the current global version from local version.json."""
    try:
        if os.path.exists("version.json"):
            with open("version.json", "r") as f:
                return json.load(f).get("version", "0.0.0")
        return "0.0.0"
    except:
        return "Error"

# Unified Global Database
DB_NAME = "network_data.db"

app = Flask(__name__)

# --- Database & Migrations ---
ALERTS_FILE = "system_alerts.json"

def add_system_alert(message):
    """Saves a system alert to be displayed on the web dashboard and prints to terminal."""
    print(f"\n[*] SYSTEM ALERT: {message}\n")
    alerts = []
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                alerts = json.load(f)
        if message not in alerts:
            alerts.append(message)
        with open(ALERTS_FILE, "w") as f:
            json.dump(alerts, f)
    except Exception as e:
        print(f"[!] Failed to save system alert: {e}")

def check_db_integrity():
    """Checks if the SQLite database is malformed or corrupted."""
    if not os.path.exists(DB_NAME): return True
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            c = conn.cursor()
            c.execute("PRAGMA integrity_check;")
            res = c.fetchone()
            if res and res[0].lower() != "ok":
                return False
        return True
    except sqlite3.DatabaseError:
        return False

def check_db_size():
    """Checks if the DB is over 100MB and warns the user."""
    try:
        if os.path.exists(DB_NAME):
            size_mb = os.path.getsize(DB_NAME) / (1024 * 1024)
            if size_mb > 100.0:  # 100 MB Threshold
                add_system_alert(f"Database size is getting large ({size_mb:.1f} MB). Consider using the Database Maintenance tool in the System tab to clear old logs and improve performance.")
    except Exception as e:
        print(f"[*] Could not check DB size: {e}")

def get_safe_channel():
    """Reads the update channel safely and sanitizes it for safe file naming."""
    try:
        if os.path.exists(DB_NAME):
            with sqlite3.connect(DB_NAME, timeout=2.0) as conn:
                row = conn.execute("SELECT value FROM system_settings WHERE key='update_channel'").fetchone()
                if row and row[0]:
                    # STRIP ODD CHARACTERS: Only allow letters, numbers, underscores, and dashes
                    clean_channel = re.sub(r'[^a-zA-Z0-9_\-]', '', str(row[0]))
                    return clean_channel if clean_channel else 'stable'
    except: pass
    return 'stable'

def is_version_compatible(backup_ver, current_ver):
    """Compares semantic versions. Returns True if backup_ver <= current_ver."""
    def parse_ver(v):
        return [int(x) for x in re.sub(r'[^\d.]', '', str(v)).split('.') if x]
    
    b_parts = parse_ver(backup_ver)
    c_parts = parse_ver(current_ver)
    
    for i in range(max(len(b_parts), len(c_parts))):
        b = b_parts[i] if i < len(b_parts) else 0
        c = c_parts[i] if i < len(c_parts) else 0
        if b > c: return False
        if b < c: return True
    return True

def get_safe_filename(name):
    """Strips Windows/Mac/Linux invalid file path characters from a string."""
    if not name: return "Unknown"
    # Removes \ / * ? : " < > | and replaces spaces with underscores
    safe = re.sub(r'[\\/*?:"<>|]', '', str(name)).replace(" ", "_")
    return safe if safe else "Export"

def manage_backup_rotation(category, max_count):
    """Sorts backups by category prefix and enforces strict quotas."""
    backup_dir = os.path.join(app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if category in f and f.endswith('.back')]
    files.sort(key=os.path.getmtime, reverse=True) 
    
    for f in files[max_count:]:
        try: os.remove(f)
        except: pass

def get_newest_backup():
    """Returns the path to the absolute newest backup file across all categories."""
    backup_dir = os.path.join(app.root_path, 'backups')
    if not os.path.exists(backup_dir): return None
    
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.back') and "temp_snapshot" not in f]
    if not files: return None
    return max(files, key=os.path.getmtime)

def execute_backup(prefix, max_count, force=False):
    """Takes a snapshot of the database. Skips if identical to newest backup."""
    if not os.path.exists(DB_NAME) or not check_db_integrity(): return None
    
    backup_dir = os.path.join(app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    temp_backup = os.path.join(backup_dir, "temp_snapshot.back")
    
    try:
        with sqlite3.connect(DB_NAME, timeout=10) as source:
            with sqlite3.connect(temp_backup) as dest:
                source.backup(dest) 
    except Exception as e:
        print(f"[!] Database snapshot failed: {e}")
        if os.path.exists(temp_backup): 
            try: os.remove(temp_backup)
            except: pass
        return None

    if not force:
        newest_existing = get_newest_backup()
        if newest_existing and os.path.exists(newest_existing):
            try:
                if filecmp.cmp(temp_backup, newest_existing, shallow=False):
                    os.remove(temp_backup)
                    print(f"[*] No new data since last backup. Skipping {prefix} backup.")
                    return None
            except Exception as e:
                pass

    channel = get_safe_channel()
    ts = int(time.time())
    
    # OS-Agnostic Safe Naming
    final_name = os.path.join(backup_dir, f"network_data_{prefix}_{channel}_v{APP_VERSION}_{ts}.back")
    
    try:
        # CHANGED: os.replace is safer than os.rename on Windows (prevents FileExistsError)
        os.replace(temp_backup, final_name)
        print(f"[*] Database backup created: {os.path.basename(final_name)}")
        manage_backup_rotation(prefix, max_count)
        return final_name
    except Exception as e:
        if os.path.exists(temp_backup): 
            try: os.remove(temp_backup)
            except: pass
        return None

def perform_startup_backup():
    execute_backup("startup_good", 10, force=False)

def schedule_routine_backups():
    """Runs a silent background thread that creates a backup every 7 days if the app is left open."""
    def backup_loop():
        while True:
            time.sleep(86400) # Sleep 24 hours
            newest = get_newest_backup()
            should_backup = False
            
            if not newest:
                should_backup = True
            else:
                if time.time() - os.path.getmtime(newest) >= 7 * 86400:
                    should_backup = True
                    
            if should_backup:
                print("[*] 7 days have passed since the last backup. Running routine background backup...")
                execute_backup("routine_good", 10, force=False)

    t = threading.Thread(target=backup_loop, daemon=True)
    t.start()

def init_db():
    """Initializes the database, handles automatic backup recovery, and runs migrations."""
    # --- 1. CORRUPTION & AUTO-RECOVERY SYSTEM ---
    if not check_db_integrity():
        print("\n[!] DATABASE CORRUPTION DETECTED! Initiating emergency recovery...")
        backup_dir = os.path.join(app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        # A. Save the corrupted DB to the error rotation
        channel = get_safe_channel()
        ts = int(time.time())
        corrupt_name = os.path.join(backup_dir, f"network_data_error_{channel}_v{APP_VERSION}_{ts}.back")
        try:
            shutil.copy2(DB_NAME, corrupt_name)
            manage_backup_rotation("error_", 2)
        except: pass
        
        # B. Safely wipe the broken database files
        for ext in ["", "-wal", "-shm"]:
            temp_file = f"{DB_NAME}{ext}"
            if os.path.exists(temp_file): 
                try: os.remove(temp_file)
                except: pass
                
        # C. Find valid restore candidates (Must be "good" and Compatible)
        candidates = []
        if os.path.exists(backup_dir):
            for f in os.listdir(backup_dir):
                if "_good_" in f and f.endswith(".back"):
                    file_path = os.path.join(backup_dir, f)
                    match = re.search(r'_v([\d\.]+)_', f)
                    if match and is_version_compatible(match.group(1), APP_VERSION):
                        candidates.append(file_path)
        
        # D. Restore the newest valid backup or start fresh
        if candidates:
            best_backup = max(candidates, key=os.path.getmtime)
            best_name = os.path.basename(best_backup)
            try:
                shutil.copy2(best_backup, DB_NAME)
                msg = f"Database corruption detected. Successfully recovered using backup: '{best_name}'."
                print(f"[✓] {msg}")
                add_system_alert(msg)
            except Exception as e:
                msg = f"Database corruption detected. Backup restoration failed. A fresh database was created. Error: {e}"
                print(f"[X] {msg}")
                add_system_alert(msg)
        else:
            msg = "Database corruption detected. No compatible backups found. A fresh database was created."
            print(f"[!] {msg}")
            add_system_alert(msg)

    # --- 2. SIZE CHECK ---
    check_db_size()

    # --- 3. TABLE CREATION ---
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL;") 
        c.execute("PRAGMA busy_timeout = 5000;")
        
        c.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT, network_name TEXT, connection_type TEXT,
                        download TEXT, upload TEXT, ping TEXT, 
                        wan_ip TEXT, device_ip TEXT, isp TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS adapter_settings (
                        mac_address TEXT PRIMARY KEY, custom_name TEXT, 
                        is_visible INTEGER DEFAULT 1, is_primary INTEGER DEFAULT 0)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS networks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_mac TEXT,
                        name TEXT, last_scan TEXT, gateway_ip TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS devices (
                        mac_address TEXT, network_id INTEGER, hostname TEXT,
                        custom_name TEXT, ip_address TEXT, last_seen TEXT,
                        services TEXT, is_online INTEGER DEFAULT 0,
                        previous_ip TEXT, discovery_status TEXT DEFAULT 'New Device',
                        vendor TEXT, PRIMARY KEY (mac_address, network_id),
                        FOREIGN KEY(network_id) REFERENCES networks(id) ON DELETE CASCADE)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS global_device_names (
                        mac_address TEXT PRIMARY KEY, custom_name TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS dns_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, 
                        domain TEXT, result_ip TEXT, record_type TEXT, status TEXT,
                        router_ip TEXT, network_name TEXT, lan_ip TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS ping_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, 
                        target TEXT, status TEXT, latency TEXT, packet_loss TEXT, 
                        network_context TEXT, router_ip TEXT, network_name TEXT, lan_ip TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS wifi_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        scan_name TEXT, comments TEXT, results_json TEXT)''')
                        
        c.execute('''CREATE TABLE IF NOT EXISTS connection_types (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
        
        c.execute("SELECT COUNT(*) FROM connection_types")
        if c.fetchone()[0] == 0:
            for t in ["Ethernet", "Wi-Fi", "Mobile data"]:
                c.execute("INSERT INTO connection_types (name) VALUES (?)", (t,))

        c.execute('''CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('update_channel', 'stable')")
        c.execute('''CREATE TABLE IF NOT EXISTS protected_wifi_ssids (ssid TEXT PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS device_scans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mac_address TEXT, network_id INTEGER, 
                        ip_address TEXT, hostname TEXT, services TEXT, 
                        timestamp TEXT)''')
        
        # --- 4. DATA MIGRATIONS ---
        try:
            c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='networks'")
            row = c.fetchone()
            if row and "gateway_mac TEXT UNIQUE" in row[0]:
                print("[*] Migrating database for VLAN support...")
                c.execute("ALTER TABLE networks RENAME TO networks_old")
                c.execute('''CREATE TABLE networks (id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_mac TEXT, name TEXT, last_scan TEXT, gateway_ip TEXT)''')
                c.execute("INSERT INTO networks (id, gateway_mac, name, last_scan, gateway_ip) SELECT id, gateway_mac, name, last_scan, gateway_ip FROM networks_old")
                c.execute("DROP TABLE networks_old")
        except Exception as e: pass

        c.execute('''CREATE TABLE IF NOT EXISTS global_device_vendors (mac_address TEXT PRIMARY KEY, custom_vendor TEXT)''')

        for col in ["isp TEXT", "connection_type TEXT", "device_ip TEXT"]:
            try: c.execute(f"ALTER TABLE history ADD COLUMN {col}")
            except sqlite3.OperationalError: pass
            
        for col in ["is_visible INTEGER DEFAULT 1", "is_primary INTEGER DEFAULT 0"]:
            try: c.execute(f"ALTER TABLE adapter_settings ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for table in ['dns_logs', 'ping_logs']:
            for col in ['router_ip TEXT', 'network_name TEXT', 'lan_ip TEXT']:
                try: c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError: pass 

        for table in ['history', 'wifi_history', 'dns_logs', 'ping_logs', 'devices', 'networks']:
            try: c.execute(f"ALTER TABLE {table} ADD COLUMN is_protected INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass

        # --- UPDATED: New snapshot columns added below ---
        for col in ["previous_ip TEXT", "discovery_status TEXT DEFAULT 'New Device'", "vendor TEXT", "custom_vendor TEXT", "last_network_name TEXT"]:
            try: c.execute(f"ALTER TABLE devices ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["network_name TEXT"]:
            try: c.execute(f"ALTER TABLE device_scans ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        # --- UPDATED: New snapshot columns added below ---
        for col in ["previous_ip TEXT", "discovery_status TEXT DEFAULT 'New Device'", "vendor TEXT", "custom_vendor TEXT", "last_network_name TEXT"]:
            try: c.execute(f"ALTER TABLE devices ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["network_name TEXT"]:
            try: c.execute(f"ALTER TABLE device_scans ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["is_deleted INTEGER DEFAULT 0"]:
            try: c.execute(f"ALTER TABLE wifi_history ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        conn.commit()

def check_clear_database():
    """Checks for a 'cleardatabase' file to completely wipe the database on startup."""
    clear_file = os.path.join(app.root_path, "cleardatabase")
    
    if os.path.exists(clear_file):
        print("[*] 'cleardatabase' file detected. Wiping the database completely...")
        try:
            # Delete the main DB file and its WAL/SHM temporary files
            for ext in ["", "-wal", "-shm"]:
                db_file = os.path.join(app.root_path, f"{DB_NAME}{ext}")
                if os.path.exists(db_file):
                    os.remove(db_file)
            
            # Delete the trigger file so it doesn't wipe on the next boot
            os.remove(clear_file)
            print("[✓] Database completely wiped. 'cleardatabase' file removed.")
        except Exception as e:
            print(f"[X] Failed to clear database: {e}")

def check_password_reset():
    """Checks for a 'passwordreset' file to reset authentication credentials."""
    reset_file = os.path.join(app.root_path, "passwordreset")
    
    if os.path.exists(reset_file):
        print("[*] 'passwordreset' file detected. Disabling authentication and removing credentials...")
        try:
            # We use IF EXISTS logic inherently by just executing the query safely
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                conn.execute("UPDATE system_settings SET value='0' WHERE key='auth_enabled'")
                conn.execute("DELETE FROM system_settings WHERE key='auth_username'")
                conn.execute("DELETE FROM system_settings WHERE key='auth_password'")
                conn.commit()
            os.remove(reset_file)
            print("[✓] Authentication reset successfully. 'passwordreset' file removed.")
        except Exception as e:
            print(f"[X] Failed to reset authentication: {e}")

# --- Startup Sequence ---
# 1. Check if we need to wipe the DB first
check_clear_database()
# 2. Build or rebuild the tables
init_db()
# 3. Check if we need to wipe passwords from the existing DB
check_password_reset()

# --- Versioning Helpers ---
def get_setup_version():
    """Reads SETUP_VERSION from setup_env.py."""
    try:
        if os.path.exists("setup_env.py"):
            with open("setup_env.py", "r") as f:
                match = re.search(r'SETUP_VERSION\s*=\s*["\']([^"\']+)["\']', f.read())
                return match.group(1) if match else "Unknown"
        return "Not Found"
    except:
        return "Error"

WORKERS_FILE = "workers"

def detect_hardware():
    """Identifies system hardware (Raspberry Pi models vs PC/Mac)."""
    if platform.system() == "Linux":
        for path in ["/proc/device-tree/model", "/sys/firmware/devicetree/base/model"]:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read().strip().replace('\x00', '')
                except Exception:
                    pass
        return "Linux PC / Server"
    elif platform.system() == "Darwin":
        return "Apple macOS"
    elif platform.system() == "Windows":
        return "Microsoft Windows"
    return "Generic Host"

def get_default_workers_for_hardware():
    """Returns sensible concurrency defaults based on detected hardware profile."""
    hw = detect_hardware().lower()
    if "pi 3" in hw or "pi 2" in hw or "pi zero" in hw:
        # Constrained: 1GB RAM, 4 slower Cortex-A53 cores
        return {"server_threads": 6, "scan_workers": 10, "ping_workers": 15}
    elif "pi 4" in hw or "pi 400" in hw:
        # Moderate: 2GB-8GB RAM, Cortex-A72 cores
        return {"server_threads": 12, "scan_workers": 20, "ping_workers": 30}
    else:
        # High performance: Pi 5, Desktop PCs, Servers
        return {"server_threads": 24, "scan_workers": 30, "ping_workers": 50}

def get_worker_config():
    """Reads the 'workers' file, automatically generating it from defaults if missing."""
    file_path = os.path.join(app.root_path, WORKERS_FILE)
    defaults = get_default_workers_for_hardware()
    
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w") as f:
                json.dump(defaults, f, indent=4)
            print(f"[*] Generated default 'workers' configuration for: {detect_hardware()}")
            return defaults
        except Exception as e:
            print(f"[!] Failed to write workers file: {e}")
            return defaults
            
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
        config = {}
        for k in ["server_threads", "scan_workers", "ping_workers"]:
            val = data.get(k)
            config[k] = int(val) if str(val).isdigit() and int(val) > 0 else defaults[k]
        return config
    except Exception as e:
        print(f"[!] Error parsing 'workers' file, using defaults: {e}")
        return defaults

# --- System & Network Helpers ---
def get_isp_info():
    """Fetches Public WAN IP and ISP name."""
    try:
        with urllib.request.urlopen('http://ip-api.com/json/?fields=query,isp', timeout=3) as url:
            data = json.loads(url.read().decode())
            return {"ip": data.get("query", "Unknown"), "isp": data.get("isp", "Unknown ISP")}
    except:
        return {"ip": "Unknown", "isp": "Unknown ISP"}

def get_local_ip():
    """Identifies the primary local LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except:
        return '127.0.0.1'
    finally:
        s.close()

def cleanup_old_files():
    """
    Scans the application directory for .old files (created during Windows updates)
    and removes them to keep the folder clean.
    """
    base_dir = app.root_path
    print("[*] Performing startup cleanup...")
    
    # Walk through all directories in the project
    for root, dirs, files in os.walk(base_dir):
        # Skip the venv folder to save time and avoid permission issues
        if "venv" in dirs:
            dirs.remove("venv")
        if "__pycache__" in dirs:
            dirs.remove("__pycache__")

        for filename in files:
            if filename.endswith(".old"):
                file_path = os.path.join(root, filename)
                try:
                    os.remove(file_path)
                    print(f"[✓] Deleted backup file: {filename}")
                except Exception as e:
                    print(f"[!] Could not delete {filename}: {e}")

def get_linux_dns(interface_name):
    """
    Fetches the actual upstream DNS servers for a specific interface on Linux.
    Prioritizes nmcli, then resolvectl, then falls back to /etc/resolv.conf.
    """
    try:
        # Method 1: NMCLI (Best for Ubuntu Desktop/Server with NetworkManager)
        # -g returns just the value, cleaner than parsing grep
        cmd = ["nmcli", "-g", "IP4.DNS", "dev", "show", interface_name]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        if output:
            # nmcli separates multiple servers with lines or pipes
            return output.replace('\n', ', ').replace(' | ', ', ')

        # Method 2: resolvectl (Standard on modern systemd Linux)
        cmd = f"resolvectl status {interface_name}"
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode()
        # Parse output like "DNS Servers: 8.8.8.8 1.1.1.1"
        for line in output.split('\n'):
            if "DNS Servers:" in line:
                return line.split(":", 1)[1].strip().replace(' ', ', ')

    except Exception:
        pass

    # Method 3: Global Fallback (/etc/resolv.conf)
    # This usually returns 127.0.0.53 on Ubuntu, but it's better than nothing
    dns_list = []
    try:
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                if line.startswith('nameserver'):
                    ip = line.strip().split()[1]
                    if ip not in dns_list:
                        dns_list.append(ip)
        return ', '.join(dns_list)
    except:
        return "Unknown"

def restart_server():
    """Signals the supervisor to restart the application."""
    print("[*] Triggering application restart in 2 seconds...")
    time.sleep(2)  # Allow the HTTP response to finish sending
    
    # Simply exit the process. The setup_env.py supervisor will catch this
    # and automatically spin up a fresh instance after clearing the port.
    os._exit(0)

def get_extended_iface_info():
    """
    Fetches Gateway, DNS, and MAC information.
    - Windows: Parses ipconfig (primary) -> PowerShell (fallback for missing DNS/GW).
    - macOS: Parses networksetup/ipconfig (Fixes missing MACs & secondary Gateways).
    - Linux: Parses ip route/resolv.conf.
    """
    info = {}
    system = platform.system()
    
    # Helper to clean and deduplicate gateway strings (e.g., "192.168.1.1, 192.168.1.1" -> "192.168.1.1")
    def _clean_gw(g_str):
        if not g_str or g_str == "-": 
            return "-"
        parts = [x.strip() for x in re.split(r'[, ]+', g_str) if x.strip()]
        uniq = []
        for p in parts:
            if p not in uniq: 
                uniq.append(p)
        return ", ".join(uniq) if uniq else "-"

    try:
        if system == "Windows":
            # --- PRIMARY: ipconfig /all (Most stable for static info) ---
            try:
                # Using latin-1 encoding to handle special characters in adapter names
                raw_ip = subprocess.check_output("ipconfig /all", shell=True, text=True, encoding='latin-1')
                current_iface = None
                
                for line in raw_ip.split('\n'):
                    line = line.strip()
                    # Identify the start of an adapter section
                    if "adapter" in line and ":" in line:
                        parts = line.split("adapter")
                        if len(parts) > 1:
                            current_iface = parts[-1].split(":")[0].strip()
                            if current_iface not in info:
                                info[current_iface] = {"gateway": "-", "dns": "-"}
                    
                    if current_iface:
                        if "Default Gateway" in line and ":" in line:
                            gw = line.split(":")[-1].strip()
                            if gw and "." in gw and ":" not in gw:
                                info[current_iface]["gateway"] = _clean_gw(gw)
                        if "DNS Servers" in line and ":" in line:
                            dns = line.split(":")[-1].strip()
                            if dns and "." in dns and ":" not in dns:
                                info[current_iface]["dns"] = dns
            except Exception as e:
                print(f"ipconfig failed: {e}")

            # --- IMPROVED FALLBACK: Targeted PowerShell ---
            # This fills in the gaps if ipconfig missed the DNS or Gateway
            try:
                ps_cmd = "Get-NetIPConfiguration | Select-Object InterfaceAlias, @{Name='G';Expression={$_.IPv4DefaultGateway.NextHop}}, @{Name='D';Expression={$_.DNSServer.ServerAddresses}} | ConvertTo-Json"
                out = subprocess.check_output(["powershell", "-Command", ps_cmd], text=True, timeout=5)
                data = json.loads(out)
                adapters = [data] if isinstance(data, dict) else data
                
                for item in adapters:
                    name = item.get('InterfaceAlias')
                    if not name: continue
                    
                    # If ipconfig missed it entirely, or it's currently "-", use PowerShell's data
                    if name not in info: info[name] = {"gateway": "-", "dns": "-"}
                    
                    # Resolve Gateway
                    gw_raw = item.get('G')
                    if gw_raw and info[name]["gateway"] == "-":
                        info[name]["gateway"] = _clean_gw(str(gw_raw))
                    
                    # Resolve DNS
                    dns_raw = item.get('D', [])
                    if dns_raw and info[name]["dns"] == "-":
                        dns_list = [str(d) for d in (dns_raw if isinstance(dns_raw, list) else [dns_raw]) if "." in str(d)]
                        if dns_list:
                            info[name]["dns"] = ", ".join(dns_list)
            except: pass

        elif system == "Darwin": # macOS
            try:
                # 1. Identify Global Default Gateway via netstat (as a fallback/confirmation)
                gw_out = subprocess.check_output("netstat -rn -f inet | grep 'default'", shell=True, text=True, stderr=subprocess.DEVNULL)
                default_gw = "-"
                primary_iface = None
                
                for line in gw_out.split('\n'):
                    parts = line.split()
                    if "default" in parts[0] and len(parts) >= 4:
                        default_gw = _clean_gw(parts[1])
                        primary_iface = parts[-1]
                        break
                
                # 2. Map Hardware Ports, DNS, MACs, and Specific Gateways
                port_out = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
                sections = port_out.split("Hardware Port: ")
                
                for section in sections:
                    if not section.strip(): continue 
                    
                    lines = section.split('\n')
                    port_name = lines[0].strip() 
                    
                    dev_name = None
                    mac_addr = "-"
                    
                    for line in lines:
                        if "Device:" in line:
                            dev_name = line.split(":")[1].strip()
                        if "Ethernet Address:" in line:
                            mac_addr = line.split(":")[1].strip()
                    
                    if dev_name:
                        # Default to global gateway if this is the primary interface
                        gw_val = default_gw if dev_name == primary_iface else "-"
                        dns_val = "-"
                        
                        # Method 1: ipconfig (Get DHCP info including Router & DNS)
                        try:
                            # Silence errors for inactive interfaces
                            ipconfig = subprocess.check_output(
                                ["ipconfig", "getpacket", dev_name], 
                                text=True, 
                                stderr=subprocess.DEVNULL 
                            )
                            
                            # Extract DNS
                            match_dns = re.search(r'domain_name_server\s*\(.*?\)\s*:\s*\{(.*?)\}', ipconfig, re.DOTALL)
                            if match_dns:
                                raw_dns = match_dns.group(1).replace('\n', '').strip()
                                dns_val = raw_dns.replace(',', ', ')
                            
                            # Extract Router (Gateway) - FIX FOR SECONDARY INTERFACES & DUPLICATES
                            # Looks for: router (ip_mult): {192.168.1.1}
                            match_gw = re.search(r'router\s*\(.*?\)\s*:\s*\{(.*?)\}', ipconfig, re.DOTALL)
                            if match_gw:
                                gw_found = match_gw.group(1).replace('\n', '').strip()
                                if gw_found and gw_found != "0.0.0.0":
                                    gw_val = _clean_gw(gw_found)

                        except: pass

                        # Method 2: networksetup fallback (Static DNS)
                        if dns_val == "-" or not dns_val:
                            try:
                                ns_out = subprocess.check_output(["networksetup", "-getdnsservers", port_name], text=True)
                                if "There aren't any" not in ns_out:
                                    dns_list = [d.strip() for d in ns_out.split('\n') if d.strip() and ":" not in d]
                                    if dns_list:
                                        dns_val = ", ".join(dns_list)
                            except: pass
                        
                        info[dev_name] = {"gateway": gw_val, "dns": dns_val, "mac": mac_addr}

            except Exception as e:
                print(f"macOS Iface Error: {e}")

        elif system == "Linux": # Linux
            try:
                # Gateway via ip route
                gw_out = subprocess.check_output("ip route show default | awk '/default/ {print $3}'", shell=True, text=True)
                default_gw = _clean_gw(gw_out.strip()) if ":" not in gw_out else "-"
            except: default_gw = "-"

            try:
                # DNS via resolv.conf
                with open("/etc/resolv.conf", "r") as f:
                    dns_list = [l.split()[1] for l in f if l.startswith("nameserver") and ":" not in l]
                dns_val = ", ".join(dns_list) if dns_list else "-"
            except: dns_val = "-"

            # Map results to all active interfaces known to psutil
            import psutil
            for iface in psutil.net_if_addrs().keys():
                info[iface] = {"gateway": default_gw, "dns": dns_val}

    except Exception as e:
        print(f"Error in get_extended_iface_info: {e}")
        
    return info

# --- Bandwidth Tracking ---
last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

def get_html_version():
    """
    Finds 'Version number ' on the first line of dashboard.html
    and extracts the numeric version following it.
    """
    try:
        # Locate the template folder relative to this script
        template_path = os.path.join(app.root_path, "templates", "dashboard.html")
        
        if os.path.exists(template_path):
            with open(template_path, "r", encoding='utf-8') as f:
                # Read only the first line of the document
                first_line = f.readline()
                
                # Search specifically for 'Version number ' followed by digits and dots
                match = re.search(r'Version number\s+([\d.]+)', first_line)
                
                if match:
                    return match.group(1)
                else:
                    print(f"[!] 'Version number' not found on first line: {first_line.strip()}")
                    return "Unknown"
        
        return "Not Found"
    except Exception as e:
        print(f"[X] HTML Version Error: {e}")
        return "Error"
    
def get_bandwidth():
    """
    Calculates network throughput. 
    Prioritizes the Pinned Adapter's traffic if one is set[cite: 1].
    Includes protection against negative values caused by interface counter resets.
    """
    global last_received, last_sent, last_time
    
    target_iface = None
    pinned_mac = None

    # 1. Identify if an adapter is pinned[cite: 1]
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
    except: 
        pass

    # 2. Map Pinned MAC to system interface name[cite: 1]
    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == psutil.AF_LINK and a.address == pinned_mac for a in addrs):
                target_iface = name
                break

    # 3. Get IO Counters[cite: 1]
    if target_iface:
        # Get stats ONLY for the pinned adapter[cite: 1]
        try:
            io = psutil.net_io_counters(pernic=True)[target_iface]
        except KeyError:
            # Fallback to global if adapter was unplugged[cite: 1]
            io = psutil.net_io_counters()
    else:
        # Use global sum if no pin is set[cite: 1]
        io = psutil.net_io_counters()

    curr_recv = io.bytes_recv
    curr_sent = io.bytes_sent
    curr_time = time.time()
    
    delta = curr_time - last_time
    if delta <= 0: delta = 1
    
    down = (curr_recv - last_received) / delta
    up = (curr_sent - last_sent) / delta
    
    # GUARD: If network counters reset (e.g., reconnect/VPN), delta is negative. Clamp to 0.
    if down < 0: down = 0.0
    if up < 0: up = 0.0
    
    # Update global tracking variables for the next poll[cite: 1]
    last_received, last_sent, last_time = curr_recv, curr_sent, curr_time
    
    return {
        "download": f"{down / 1024 / 1024:.2f} MB/s", 
        "upload": f"{up / 1024 / 1024:.2f} MB/s"
    }

def get_active_interface_name():
    """Finds the interface matching the local IP, but returns empty if it is hidden."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        target_ip = s.getsockname()[0]
    except:
        target_ip = '127.0.0.1'
    finally:
        s.close()

    if target_ip == '127.0.0.1':
        return "" 

    interfaces = psutil.net_if_addrs()
    for iface_name, addrs in interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == target_ip:
                # --- NEW: Check if this interface is hidden in the DB ---
                mac = None
                for a in addrs:
                    if a.family == psutil.AF_LINK:
                        mac = a.address
                
                if mac:
                    try:
                        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                            row = conn.execute("SELECT is_visible FROM adapter_settings WHERE mac_address=?", (mac,)).fetchone()
                            if row and row[0] == 0:
                                return "" # It is hidden, treat it as unusable
                    except:
                        pass
                
                return iface_name
    return ""

MAC_VENDOR_CACHE = {}

def get_mac_vendor(mac, fetch_online=False):
    """Fetches the manufacturer name based on the MAC address."""
    if not mac or mac == "-" or mac.startswith("NO_MAC"): return ""
    
    mac_prefix = mac[:8].upper() 
    if mac_prefix in MAC_VENDOR_CACHE:
        return MAC_VENDOR_CACHE[mac_prefix]

    # Check database before making an HTTP request
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT vendor FROM devices WHERE mac_address=? AND vendor IS NOT NULL AND vendor != '' LIMIT 1", (mac,)).fetchone()
            if row:
                MAC_VENDOR_CACHE[mac_prefix] = row[0]
                return row[0]
    except Exception:
        pass

    # If we are just scanning locally, skip the slow internet lookup
    if not fetch_online:
        return ""

    try:
        req = urllib.request.Request(
            f"https://api.maclookup.app/v2/macs/{mac_prefix}",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=2) as url:
            data = json.loads(url.read().decode())
            
            if data.get('success') and data.get('company'):
                company = data['company'].replace(' Inc.', '').replace(' Ltd.', '').split(',')[0]
                MAC_VENDOR_CACHE[mac_prefix] = company
                return company
    except Exception: 
        pass
    
    return ""

def resolve_hostname(ip, mac=None):
    """Resolves hostname using DNS, ARP cache, NetBIOS, and appends MAC Vendor in brackets."""
    hostname = "Unknown Device"
    
    try:
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1) 
        name = socket.gethostbyaddr(ip)[0]
        socket.setdefaulttimeout(default_timeout)
        if name and not name.startswith(ip) and name != "?": 
            hostname = name
    except:
        socket.setdefaulttimeout(default_timeout if 'default_timeout' in locals() else None)
    
    if hostname == "Unknown Device":
        try:
            arp_out = subprocess.check_output(["arp", "-a"], text=True)
            for line in arp_out.split('\n'):
                if ip in line:
                    match = re.search(r'^(\S+)\s+\(', line)
                    if match:
                        name = match.group(1)
                        if name != "?" and name != ip: 
                            hostname = name
                            break
        except: pass

    if hostname == "Unknown Device" and platform.system() == "Windows":
        try:
            out = subprocess.check_output(["nbtstat", "-A", ip], text=True, timeout=2)
            for line in out.split('\n'):
                if "<20>" in line and "UNIQUE" in line:
                    hostname = line.split("<20>")[0].strip()
                    break
        except: pass

    if mac:
        vendor = get_mac_vendor(mac, fetch_online=False)
        if vendor:
            hostname = f"{hostname} ({vendor})"

    return hostname

def process_device_info(received):
    """Worker function for threaded scanning."""
    ip = getattr(received, 'psrc', getattr(received, 'ip', None))
    mac = getattr(received, 'hwsrc', getattr(received, 'mac', None))
    
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip, mac),
        "vendor": get_mac_vendor(mac, fetch_online=False),
        "services": check_open_ports(ip)['services']
    }

def check_open_ports(ip):
    """
    Scans for common web and management ports.
    Returns a formatted string of services found.
    """
    services = []
    # Dictionary of ports to scan: {port: "Name"}
    common_ports = {
        22: "SSH", 
        80: "HTTP", 
        443: "HTTPS", 
        8080: "HTTP (8080)", 
        8443: "HTTPS (8443)",
        5000: "Flask/UPnP",
        9000: "Portainer/Admin"
    }
    
    for port, name in common_ports.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)  # Fast check
        if s.connect_ex((ip, port)) == 0:
            services.append(name)
        s.close()
        
    return {"services": ", ".join(services) if services else "None"}

def get_gateway_mac(gateway_ip):
    """
    Resolves Gateway MAC while respecting the Pinned Adapter.
    Prevents 'bind' errors by locking Scapy to a single interface.
    """
    if not gateway_ip or gateway_ip == "-" or gateway_ip == "Unknown": 
        return None
    
    target_iface = None
    pinned_mac = None

    # 1. Check for a user-pinned adapter first
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
    except: 
        pass

    # 2. Match the Pinned MAC to a system interface name
    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == psutil.AF_LINK and a.address == pinned_mac for a in addrs):
                target_iface = name
                break

    # 3. Fallback to the active interface if no pin is found
    if not target_iface:
        target_iface = get_active_interface_name()

    # --- NEW: Abort if the active interface was hidden (returned "") ---
    if not target_iface:
        return None

    try:
        # Use 'iface' to force Scapy to only bind to the chosen adapter
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), 
                     timeout=2, verbose=0, iface=target_iface, promisc=False)
        for _, received in ans: 
            return received.hwsrc
    except Exception as e:
        # If Windows rejects the interface string, fallback to Scapy's default routing
        print(f"[*] Targeted Gateway MAC resolution failed on {target_iface}: {e}. Retrying globally...")
        try:
            ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), 
                         timeout=2, verbose=0, promisc=False)
            for _, received in ans: 
                return received.hwsrc
        except: pass
    
    return None

def get_current_network_context():
    """Returns (lan_ip, gateway_ip, network_name) strictly using IPv4."""
    lan_ip = get_local_ip()
    
    # 1. Get Gateway
    ext_info = get_extended_iface_info()
    gateway_ip = "-"
    
    for iface_details in ext_info.values():
        gw = iface_details.get("gateway", "-")
        # STRICT FILTER: Only accept if not "-" and NO colons present
        if gw != "-" and ":" not in gw:
            gateway_ip = gw
            break
            
    # 2. Get Network Name
    network_name = "Unknown Network"
    if gateway_ip != "-":
        gateway_mac = get_gateway_mac(gateway_ip)
        if gateway_mac:
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                row = conn.execute("SELECT name FROM networks WHERE gateway_mac=?", (gateway_mac,)).fetchone()
                if row: network_name = row[0]
                
    return lan_ip, gateway_ip, network_name

def request_macos_permissions():
    """Probes for macOS Location and Local Network permissions."""
    if platform.system() == "Darwin":
        print("[*] Probing macOS Network & Location permissions...")
        
        # 1. Trigger Local Network Access Prompt (by sending a single UDP packet)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            s.send(b"probing-local-network")
            s.close()
        except: pass

        # 2. Trigger Location Services Prompt (by attempting a Wi-Fi scan)
        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if os.path.exists(airport_path):
            try:
                # Running a scan forces the OS to check for Location permissions
                subprocess.Popen([airport_path, "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("[!] If prompted, please allow 'Location Access' for Wi-Fi scanning to work.")
            except: pass

# --- Authentication Middleware ---
def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Network Dashboard Login Required"'})

@app.before_request
def require_auth():
    """Checks every single request to see if authentication is enabled and valid."""
    # Allow preflight requests to pass without auth
    if request.method == 'OPTIONS':
        return
        
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        try:
            enabled_row = conn.execute("SELECT value FROM system_settings WHERE key='auth_enabled'").fetchone()
            if enabled_row and enabled_row[0] == '1':
                user_row = conn.execute("SELECT value FROM system_settings WHERE key='auth_username'").fetchone()
                pass_row = conn.execute("SELECT value FROM system_settings WHERE key='auth_password'").fetchone()
                
                # ANTI-LOCKOUT: If auth is enabled but no user/pass exists in the DB, safely bypass auth
                if not user_row or not pass_row:
                    return 
                    
                auth = request.authorization
                if not auth or not auth.username or not auth.password:
                    return authenticate()
                    
                if auth.username != user_row[0] or not check_password_hash(pass_row[0], auth.password):
                    return authenticate()
        except sqlite3.OperationalError:
            pass # Failsafe if the database hasn't fully initialized yet

# --- Authentication API Routes ---
@app.route('/api/settings/auth', methods=['GET'])
def get_auth_settings():
    """Fetches the current auth state for the UI toggle."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        try:
            enabled = conn.execute("SELECT value FROM system_settings WHERE key='auth_enabled'").fetchone()
            user = conn.execute("SELECT value FROM system_settings WHERE key='auth_username'").fetchone()
            return jsonify({
                "enabled": enabled[0] == '1' if enabled else False,
                "username": user[0] if user else ""
            })
        except sqlite3.OperationalError:
            return jsonify({"enabled": False, "username": ""})

@app.route('/api/settings/auth', methods=['POST'])
def set_auth_settings():
    """Saves the auth state and securely hashes the password."""
    d = request.json
    enabled = '1' if d.get('enabled') else '0'
    username = str(d.get('username', '')).strip()[:50] # Limit applied
    password = str(d.get('password', ''))[:255] # Limit applied

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        # Check if a password already exists
        try:
            pass_row = conn.execute("SELECT value FROM system_settings WHERE key='auth_password'").fetchone()
        except sqlite3.OperationalError:
            pass_row = None
            
        # Server-side validation to prevent bad states
        if enabled == '1':
            if not username:
                return jsonify({"status": "error", "message": "A username is required."}), 400
            if not pass_row and not password:
                return jsonify({"status": "error", "message": "A password is required for the first setup."}), 400

        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_enabled', ?)", (enabled,))
        if username:
            conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_username', ?)", (username,))
        if password: 
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_password', ?)", (hashed_pw,))
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/settings/port', methods=['GET'])
def get_port():
    """Fetches the current web port."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        try:
            row = conn.execute("SELECT value FROM system_settings WHERE key='web_port'").fetchone()
            port = int(row[0]) if row else 81
        except:
            port = 81
    return jsonify({"port": port})

@app.route('/api/settings/port', methods=['POST'])
def set_port():
    """Saves a new port and restarts the server."""
    new_port = request.json.get('port')
    
    # ADDED STRICT VALIDATION
    if not new_port or not str(new_port).isdigit() or not (1 <= int(new_port) <= 65535):
        return jsonify({"status": "error", "message": "Invalid port number. Must be between 1 and 65535."}), 400
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('web_port', ?)", (str(new_port),))
        conn.commit()
        
    # Trigger a restart in the background to apply the new port
    threading.Thread(target=restart_server).start()
    return jsonify({"status": "success", "port": new_port})

def check_webport_file():
    """Checks for a 'webport' file to override the default web port on startup."""
    port_file = os.path.join(app.root_path, "webport")
    if os.path.exists(port_file):
        print("[*] 'webport' file detected. Updating web server port...")
        try:
            with open(port_file, "r") as f:
                new_port = f.read(10).strip()
                
            # ADDED STRICT VALIDATION: Must be numbers AND a valid port range
            if new_port.isdigit() and 1 <= int(new_port) <= 65535:
                with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('web_port', ?)", (new_port,))
                    conn.commit()
                print(f"[✓] Web port successfully updated to {new_port}.")
            else:
                print(f"[!] Invalid port '{new_port}' in webport file. Ignoring and deleting.")
                
            os.remove(port_file)
        except Exception as e:
            print(f"[X] Failed to process webport file: {e}")

def check_dev_file():
    """Checks for a 'dev' trigger file, updates the DB channel, and removes it."""
    dev_file = os.path.join(app.root_path, "dev")
    if os.path.exists(dev_file):
        print("[*] 'dev' file detected. Updating database channel to 'dev'...")
        try:
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('update_channel', 'dev')")
                conn.commit()
            os.remove(dev_file)
            print("[✓] Channel set to 'dev' and trigger file removed.")
        except Exception as e:
            print(f"[X] Failed to process dev file: {e}")

def get_current_port():
    """Reads the current port for Waitress to bind to."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT value FROM system_settings WHERE key='web_port'").fetchone()
            return int(row[0]) if row else 81
    except:
        return 81

# --- Routes ---
@app.route('/')
def index():
    info = get_isp_info()
    ext_info = get_extended_iface_info()
    active_iface = get_active_interface_name() # e.g., "Wi-Fi" or "Ethernet"
    
    # Default to "Unknown"
    active_gateway = "Unknown"
    active_dns = "Unknown"

    # Try to find the specific Gateway/DNS for the active interface
    if active_iface:
        # 1. Try Exact Match
        if active_iface in ext_info:
            active_gateway = ext_info[active_iface].get("gateway", "Unknown")
            active_dns = ext_info[active_iface].get("dns", "Unknown")
        
        # 2. Try Fuzzy Match (Windows naming is often messy)
        elif platform.system() == "Windows":
            for k, v in ext_info.items():
                # If "Wi-Fi" is in "Wireless LAN adapter Wi-Fi"
                if active_iface in k or k in active_iface:
                    active_gateway = v.get("gateway", active_gateway)
                    active_dns = v.get("dns", active_dns)
                    break
    
    # 3. Fallback: If still unknown, just grab the first one that has a Gateway
    if active_gateway == "Unknown" or active_gateway == "-":
        for v in ext_info.values():
            if v.get("gateway") and v.get("gateway") != "-":
                active_gateway = v.get("gateway")
                active_dns = v.get("dns")
                break

# Grab the current channel to pass to the frontend
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key='update_channel'").fetchone()
        current_channel = row[0] if row else 'stable'

    # Get the display name for the footer
    all_settings = get_all_github_settings()
    channel_display_name = all_settings.get(current_channel, {}).get("display_name", current_channel.capitalize())

    return render_template('dashboard.html', 
                           local_ip=get_local_ip(), 
                           wan_ip=info['ip'], 
                           isp_name=info['isp'],
                           router_ip=active_gateway,
                           dns_servers=active_dns,
                           global_version=get_global_version(), 
                           setup_version=get_setup_version(), 
                           app_version=APP_VERSION,
                           html_version=get_html_version(),
                           update_channel=current_channel,
                           update_channel_name=channel_display_name) # NEW VARIABLE

@app.route('/api/system/cleanup', methods=['POST'])
def cleanup_database():
    """
    Cleans up the database based on the selected interval across ALL tables.
    Respects the is_protected flag for every table.
    """
    days = request.json.get('days')
    
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            cursor = conn.cursor()
            
            if days == 'all':
                for table in ['history', 'dns_logs', 'ping_logs', 'wifi_history', 'networks', 'devices', 'device_scans', 'global_device_names', 'global_device_vendors']:
                    # Note: We do NOT respect is_protected on a total factory wipe ("Clear Database Completely")
                    cursor.execute(f"DELETE FROM {table}")
                message = "Database cleared completely."
            else:
                if not str(days).isdigit():
                    return jsonify({"status": "error", "message": "Invalid time interval."}), 400
                
                date_filter = f"datetime('now', '-{int(days)} days')"
                
                # Standard tables using 'timestamp'
                for table in ['history', 'dns_logs', 'ping_logs', 'wifi_history', 'device_scans']:
                    # device_scans doesn't have an is_protected column, it relies on the device itself
                    if table == 'device_scans':
                        cursor.execute(f"DELETE FROM {table} WHERE timestamp < {date_filter} AND mac_address NOT IN (SELECT mac_address FROM devices WHERE is_protected=1)")
                    else:
                        cursor.execute(f"DELETE FROM {table} WHERE timestamp < {date_filter} AND is_protected = 0")
                
                # Tables using 'last_seen' or 'last_scan'
                cursor.execute(f"DELETE FROM devices WHERE last_seen < {date_filter} AND is_protected = 0")
                cursor.execute(f"DELETE FROM networks WHERE last_scan < {date_filter} AND is_protected = 0")
                
                message = f"All data older than {days} days has been removed. (Protected items were kept)."
            
            conn.commit()
            conn.execute("VACUUM")
            
            return jsonify({"status": "success", "message": message})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/system/db_info', methods=['GET'])
def get_db_info():
    """Returns the current size of the main SQLite database in MB."""
    try:
        size_mb = 0
        if os.path.exists(DB_NAME):
            size_mb = os.path.getsize(DB_NAME) / (1024 * 1024)
        return jsonify({"size_mb": f"{size_mb:.2f}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/system/cleanup_orphaned_devices', methods=['POST'])
def cleanup_orphaned_devices():
    """Removes devices that are ONLY associated with deleted networks."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            cursor = conn.cursor()
            
            # Smart isolation query: 
            # Grab MACs that exist in deleted networks, EXCEPT any MACs that also exist in active networks
            query = """
                SELECT mac_address FROM devices WHERE network_id NOT IN (SELECT id FROM networks)
                UNION
                SELECT mac_address FROM device_scans WHERE network_id NOT IN (SELECT id FROM networks)
                EXCEPT
                SELECT mac_address FROM devices WHERE network_id IN (SELECT id FROM networks)
                EXCEPT
                SELECT mac_address FROM device_scans WHERE network_id IN (SELECT id FROM networks)
            """
            cursor.execute(query)
            orphaned_macs = [row[0] for row in cursor.fetchall()]

            if not orphaned_macs:
                return jsonify({"status": "success", "message": "No old orphaned devices found."})

            placeholders = ','.join(['?'] * len(orphaned_macs))
            
            # Completely purge the orphaned devices from all tables
            cursor.execute(f"DELETE FROM devices WHERE mac_address IN ({placeholders})", orphaned_macs)
            cursor.execute(f"DELETE FROM device_scans WHERE mac_address IN ({placeholders})", orphaned_macs)
            cursor.execute(f"DELETE FROM global_device_names WHERE mac_address IN ({placeholders})", orphaned_macs)
            cursor.execute(f"DELETE FROM global_device_vendors WHERE mac_address IN ({placeholders})", orphaned_macs)
            
            conn.commit()
            conn.execute("VACUUM") # Reclaim disk space
            
            return jsonify({"status": "success", "message": f"Successfully removed {len(orphaned_macs)} old device(s)." })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/live_bandwidth')
def api_live_bandwidth():
    return jsonify(get_bandwidth())

@app.route('/api/adapters')
def get_adapters():
    """
    Fetches all network adapters with:
    1. Stable IPv4 data from ipconfig.
    2. Pinned Adapter logic (Header locks to user choice).
    3. Hardware link speeds with a fallback to 'Not Available' if idle or invalid.
    4. Real-time Wi-Fi rates only when active traffic exists.
    5. OS-Specific DNS handling (Fixes Ubuntu 127.0.0.53 issue).
    6. macOS MAC Address Fallback (Fixes missing adapters).
    7. Real-time global bandwidth for dashboard metric cards.
    """
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    
    ext_info = get_extended_iface_info()
    wifi_rates = get_wifi_rates()
    
    active_iface_name = get_active_interface_name()
    primary_gw = "Unknown"
    primary_dns = "Unknown"
    pinned_mac = None

    settings = {}
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            for row in conn.execute("SELECT mac_address, custom_name, is_visible, is_primary FROM adapter_settings"):
                settings[row[0]] = {
                    "name": row[1], 
                    "visible": row[2], 
                    "is_primary": bool(row[3])
                }
                if row[3] == 1:
                    pinned_mac = row[0]
    except: 
        pass

    for name, addrs in interfaces.items():
        st = stats.get(name)
        
        if "Loopback" in name or "vEthernet" in name or name == "lo": 
            continue
        
        ip4, mac = "-", "-"
        for a in addrs:
            if a.family == socket.AF_INET: 
                ip4 = a.address
            elif a.family == psutil.AF_LINK: 
                mac = a.address

        spec_info = ext_info.get(name, {})
        
        if not spec_info and platform.system() == "Windows":
             for k, v in ext_info.items():
                 if k in name or name in k:
                     spec_info = v
                     break

        if mac == "-" and spec_info.get("mac"):
            mac = spec_info.get("mac")

        gw = spec_info.get("gateway", "-")
        if ":" in gw: gw = "-"
        
        dns = spec_info.get("dns", "-")
        if ":" in dns: dns = "-"

        if platform.system() == "Linux":
            real_dns = get_linux_dns(name)
            if real_dns:
                dns = real_dns

        is_pinned = (mac == pinned_mac) if pinned_mac else False
        is_active_default = False
        if not pinned_mac and active_iface_name:
            is_active_default = (name == active_iface_name or name in active_iface_name)

        if is_pinned or (not pinned_mac and is_active_default):
             if gw != "-": primary_gw = gw
             if dns != "-": primary_dns = dns

        raw_speed = st.speed if st else 0
        display_speed = "Not Available"
        
        if raw_speed and raw_speed > 0:
            if raw_speed >= 1000:
                display_speed = f"{raw_speed/1000:g} Gbps"
            else:
                display_speed = f"{raw_speed} Mbps"
        
        for wifi_name, rate_str in wifi_rates.items():
            if wifi_name.lower() in name.lower() or name.lower() in wifi_name.lower():
                display_speed = rate_str

        # FIX: Use exact matches so we don't accidentally wipe out valid Wi-Fi speeds
        if display_speed == "0 Mbps" or display_speed == "-1 Mbps" or display_speed == "-":
            display_speed = "Not Available"

        user_name = name
        is_vis = True
        key = mac if (mac and mac != "-") else name
        
        if key in settings:
            if settings[key]["name"]: user_name = settings[key]["name"]
            is_vis = bool(settings[key]["visible"])
            
        adapters_data.append({
            "id": name, 
            "name": user_name, 
            "mac": mac, 
            "status": "Active" if (st and st.isup) else "Inactive",
            "ip4": ip4, 
            "gateway": gw, 
            "dns": dns,
            "speed": display_speed, 
            "visible": is_vis,
            "is_primary": is_pinned
        })

    if primary_gw == "Unknown" or ":" in primary_gw:
        primary_gw = "-"
        for v in ext_info.values():
            curr_gw = v.get("gateway")
            if curr_gw and curr_gw != "-" and ":" not in curr_gw:
                primary_gw = curr_gw
                if primary_dns == "Unknown":
                    primary_dns = v.get("dns", "-")
                break
    
    live_traffic = get_bandwidth()
        
    return jsonify({
        "adapters": adapters_data, 
        "primary_router": primary_gw, 
        "primary_dns": primary_dns,
        "global_speed": live_traffic
    })

@app.route('/api/adapter_settings', methods=['POST'])
def save_adapter_settings():
    """Updates custom name, visibility, and primary (pinned) status."""
    data = request.json
    mac = data.get('mac')
    # --- FIXED: Apply string limit ---
    name = str(data.get('name', '')).strip()[:50]
    visible = data.get('visible', 1)
    primary = data.get('is_primary', 0)
    
    if not mac or mac == '-':
        return jsonify({"status": "error", "message": "Cannot configure adapter without MAC address"}), 400

    # --- Strict validation: Prevent hiding a pinned adapter ---
    if primary == 1 and visible == 0:
        return jsonify({"status": "error", "message": "You cannot hide a pinned adapter."}), 400

    # --- NEW: Prevent hiding the very last visible adapter ---
    if visible == 0:
        interfaces = psutil.net_if_addrs()
        valid_keys = []
        
# 1. Gather all actual usable adapters on the system
        for iface_name, addrs in interfaces.items():
            if "Loopback" in iface_name or "vEthernet" in iface_name or iface_name == "lo": 
                continue
            temp_mac = "-"
            for a in addrs:
                if a.family == psutil.AF_LINK: 
                    temp_mac = a.address
                    
            # --- FIXED: Only count physical adapters with a real MAC address ---
            if temp_mac and temp_mac != "-":
                valid_keys.append(temp_mac)
            
        # 2. Get the adapters currently hidden in the database
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            hidden_rows = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_visible = 0").fetchall()
            hidden_keys = set(r[0] for r in hidden_rows)
        
        # 3. Simulate hiding this adapter
        hidden_keys.add(mac)
        
        # 4. Count how many system adapters would survive
        visible_count = sum(1 for k in valid_keys if k not in hidden_keys)
        
        if visible_count == 0:
            return jsonify({"status": "error", "message": "You need to have at least one usable adapter usable."}), 400

    # --- Save Settings ---
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        # If this new adapter is being set as primary, un-pin all others first
        if primary == 1:
            conn.execute("UPDATE adapter_settings SET is_primary = 0")
            
        # Update or Insert the new settings
        conn.execute("""
            INSERT INTO adapter_settings (mac_address, custom_name, is_visible, is_primary)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mac_address) DO UPDATE SET
                custom_name=excluded.custom_name,
                is_visible=excluded.is_visible,
                is_primary=excluded.is_primary
        """, (mac, name, visible, primary))
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/adapters/update', methods=['POST'])
def update_adapter_settings():
    """Updates custom name and visibility for a specific adapter."""
    d = request.json
    mac = d.get('mac')
    # --- FIXED: Apply string limit ---
    name = str(d.get('name', '')).strip()[:50]
    visible = 1 if d.get('visible') else 0
    
    # If the adapter has no MAC (virtual interface), we can't reliably save settings
    if not mac or mac == '-':
        return jsonify({"status": "error", "message": "Cannot configure adapter without MAC address"})

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        # Upsert: Update if exists, Insert if new
        conn.execute("""
            INSERT INTO adapter_settings (mac_address, custom_name, is_visible)
            VALUES (?, ?, ?)
            ON CONFLICT(mac_address) DO UPDATE SET
            custom_name=excluded.custom_name,
            is_visible=excluded.is_visible
        """, (mac, name, visible))
        conn.commit()
        
    return jsonify({"status": "success"})
    
def get_wifi_rates():
    """
    Safety-first Wi-Fi rate fetching. Handles Windows JSON/NoneType,
    macOS ipconfig, and Linux nmcli/sysfs/iw/iwconfig speed attributes.
    """
    rates = {}
    system = platform.system()
    try:
        if system == "Windows":
            cmd = "Get-NetAdapterStatistics | Select-Object Name, TransmitBitRate, ReceiveBitRate | ConvertTo-Json"
            try:
                out = subprocess.check_output(["powershell", "-Command", cmd], text=True, timeout=5)
            except: return rates

            if not out.strip(): return rates
            try:
                data = json.loads(out)
            except: return rates

            adapter_stats = [data] if isinstance(data, dict) else data
            if isinstance(adapter_stats, list):
                for item in adapter_stats:
                    if not isinstance(item, dict): continue
                    name = item.get('Name')
                    if not name: continue

                    try:
                        raw_tx = item.get('TransmitBitRate')
                        raw_rx = item.get('ReceiveBitRate')
                        
                        tx_val = int(raw_tx) if raw_tx is not None else 0
                        rx_val = int(raw_rx) if raw_rx is not None else 0
                        
                        if tx_val > 0 or rx_val > 0:
                            tx_mbps = round(tx_val / 1_000_000, 1)
                            rx_mbps = round(rx_val / 1_000_000, 1)
                            rates[name] = f"Tx: {tx_mbps} / Rx: {rx_mbps} Mbps"
                    except: continue
                    
        elif system == "Darwin": # macOS
            try:
                airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
                if os.path.exists(airport_path):
                    out = subprocess.check_output([airport_path, "-I"], text=True)
                    rate_match = re.search(r'lastTxRate:\s+(\d+)', out)
                    if rate_match:
                        rates["en0"] = f"{rate_match.group(1)} Mbps"
                
                if "en0" not in rates:
                    out = subprocess.check_output(["ipconfig", "getsummary", "en0"], text=True)
                    tx_match = re.search(r'transmitRate\s+:\s+(\d+)', out)
                    if tx_match: 
                        rates["en0"] = f"{tx_match.group(1)} Mbps"
            except Exception as e:
                print(f"macOS Wi-Fi rate fetch failed: {e}")    

        elif system == "Linux": # Linux
            # Method 1: NetworkManager CLI (Most reliable, no root required, ignores $PATH issues)
            try:
                out = subprocess.check_output(["nmcli", "-t", "-f", "IN-USE,DEVICE,RATE", "dev", "wifi"], text=True, stderr=subprocess.DEVNULL)
                for line in out.strip().split('\n'):
                    parts = line.split(':')
                    # The active network is marked with a '*' in the IN-USE column
                    if len(parts) >= 3 and parts[0].replace('\\', '') == '*':
                        dev = parts[1]
                        rate_raw = parts[2]
                        # Extract ONLY the digits to ensure a clean value
                        match = re.search(r'([0-9.]+)', rate_raw)
                        if dev and match and "unknown" not in rate_raw.lower():
                            rates[dev] = f"{match.group(1)} Mbps"
            except: pass

            # Method 2-4: Fallbacks for systems without NetworkManager (e.g., Raspberry Pi OS)
            try:
                for iface in os.listdir('/sys/class/net/'):
                    if iface.startswith(('wlan', 'wlp', 'wlo')):
                        if iface in rates:
                            continue # Skip if nmcli already got it
                            
                        # Method 2: Safely check sysfs speed file (ignoring negative error codes)
                        try:
                            speed_path = f'/sys/class/net/{iface}/speed'
                            if os.path.exists(speed_path):
                                with open(speed_path, 'r') as f:
                                    speed_val = int(f.read().strip())
                                    if speed_val > 0:
                                        rates[iface] = f"{speed_val} Mbps"
                                        continue
                        except: pass

                        # Method 3: 'iw' with explicit absolute paths mapped for background services
                        try:
                            cmd = f"/sbin/iw dev {iface} link 2>/dev/null || /usr/sbin/iw dev {iface} link 2>/dev/null || iw dev {iface} link 2>/dev/null"
                            out = subprocess.check_output(cmd, shell=True, text=True)
                            if "Not connected" not in out:
                                # Extract ONLY the digits to ensure a clean value
                                match = re.search(r'tx bitrate:\s+([0-9.]+)', out)
                                if match:
                                    rates[iface] = f"{match.group(1)} Mbps"
                                    continue
                        except: pass
                        
                        # Method 4: Legacy 'iwconfig' with absolute paths
                        try:
                            cmd = f"/sbin/iwconfig {iface} 2>/dev/null || /usr/sbin/iwconfig {iface} 2>/dev/null || iwconfig {iface} 2>/dev/null"
                            out = subprocess.check_output(cmd, shell=True, text=True)
                            # Extract ONLY the digits to ensure a clean value
                            match = re.search(r'Bit Rate[=:]\s*([0-9.]+)', out)
                            if match:
                                rates[iface] = f"{match.group(1)} Mbps"
                        except: pass
            except: pass

    except Exception as e:
        print(f"Error in get_wifi_rates: {e}")
    return rates

# --- Audit Logging Middleware ---
@app.after_request
def audit_logger(response):
    """Automatically logs configuration changes, deletions, and exports."""
    # Only track successful state-changing or export requests
    if response.status_code in [200, 201] and (request.method in ['POST', 'DELETE'] or 'export' in request.path or 'download' in request.path):
        
        # Ignore background polling and raw tool execution (we already log Ping, DNS, etc. manually)
        ignore_paths = ['/api/speedtest', '/api/scan_network', '/api/dns/lookup', '/api/ping/run', '/api/vendor/lookup', '/api/wifi/save', '/api/live_bandwidth']
        if any(p in request.path for p in ignore_paths) and 'export' not in request.path:
            return response
        
        action = "System Action"
        path = request.path
        
        if 'export' in path or 'download' in path: action = "Data Export"
        elif 'delete' in path or 'clear' in path or 'cleanup' in path: action = "Data Deletion"
        elif 'update' in path or 'rename' in path or 'settings' in path or 'bulk_hide' in path: action = "Configuration Change"
        elif 'toggle_protection' in path: action = "Record Protection Toggled"
        elif 'import' in path: action = "Database Merged"
        
        # Capture the payload context if it's a small JSON request
        context = ""
        if request.is_json:
            try:
                data = request.get_json()
                if data:
                    # Mask sensitive or huge data in the log
                    if 'password' in data: data['password'] = '******'
                    if 'results' in data: data.pop('results') 
                    if 'rows' in data: data['rows'] = f"[{len(data['rows'])} items]"
                    context = f" | Context: {json.dumps(data)}"
            except: pass
        
        print(f"[*] Audit: {action} ({path}){context}")
        
    return response

# --- Network & Device Management Routes ---

@app.route('/api/networks')
def list_networks():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        networks = conn.execute("SELECT * FROM networks ORDER BY last_scan DESC").fetchall()
        result = []
        for net in networks:
            count = conn.execute("SELECT COUNT(*) FROM devices WHERE network_id=?", (net['id'],)).fetchone()[0]
            # Convert to dict to safely grab is_protected
            net_dict = dict(net)
            result.append({
                "id": net_dict['id'], "name": net_dict['name'], "gateway_mac": net_dict['gateway_mac'],
                "gateway_ip": net_dict['gateway_ip'], "last_scan": net_dict['last_scan'], "device_count": count,
                "is_protected": net_dict.get('is_protected', 0)
            })
        return jsonify(result)

@app.route('/api/networks/delete', methods=['POST'])
def delete_network():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM networks WHERE id=?", (request.json.get('id'),))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/rename', methods=['POST'])
def rename_network():
    d = request.json
    name = str(d.get('name', '')).strip()[:50] # Safely sliced
    net_id = d.get('id')
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE networks SET name=? WHERE id=?", (name, net_id))
        
        # Propagate the custom name to the history tables
        try: 
            conn.execute("UPDATE devices SET last_network_name=? WHERE network_id=?", (name, net_id))
        except sqlite3.OperationalError: pass
        
        try: 
            conn.execute("UPDATE device_scans SET network_name=? WHERE network_id=?", (name, net_id))
        except sqlite3.OperationalError: pass
        
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/devices/update_name', methods=['POST'])
def update_device_name():
    d = request.json
    name = str(d.get('name', '')).strip()[:50] # Safely sliced
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE devices SET custom_name=? WHERE mac_address=? AND network_id=?", 
                     (name, d.get('mac'), d.get('network_id'))) # Use the variable!
        conn.execute("INSERT OR REPLACE INTO global_device_names (mac_address, custom_name) VALUES (?, ?)", 
                     (d.get('mac'), name)) # Use the variable!
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/<int:net_id>/devices')
def get_network_devices(net_id):
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        # ADDED is_protected to the SELECT query here:
        query = "SELECT mac_address, hostname, custom_name, ip_address, previous_ip, discovery_status, last_seen, services, is_online, vendor, custom_vendor, is_protected FROM devices WHERE network_id=?"
        devices = conn.execute(query, (net_id,)).fetchall()
        
        dev_list = [dict(d) for d in devices]
        
        for dev in dev_list:
            # Sync Global Names
            g_name = conn.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (dev['mac_address'],)).fetchone()
            if g_name and g_name[0]: dev['custom_name'] = g_name[0]
            
            # Sync Global Vendors
            g_vendor = conn.execute("SELECT custom_vendor FROM global_device_vendors WHERE mac_address=?", (dev['mac_address'],)).fetchone()
            if g_vendor and g_vendor[0]: dev['custom_vendor'] = g_vendor[0]

        try:
            dev_list.sort(key=lambda x: ipaddress.IPv4Address(x['ip_address']))
        except: pass
        return jsonify(dev_list)

@app.route('/api/scan_network')
def scan_network():
    """
    Robust Pinned-First Network Scan for Windows/macOS/Linux.
    """
    worker_cfg = get_worker_config()
    pinned_mac = None
    target_iface = None
    target_ip_val = None
    target_mac_val = None

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
        if row: pinned_mac = row[0]

    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            current_mac, current_ip = None, None
            for a in addrs:
                if a.family == psutil.AF_LINK: current_mac = a.address
                elif a.family == socket.AF_INET: current_ip = a.address
            
            if current_mac == pinned_mac:
                target_iface, target_ip_val, target_mac_val = name, current_ip, current_mac
                break

    if not target_iface or not target_ip_val:
        target_iface = get_active_interface_name()
        target_ip_val = get_local_ip()
        if target_iface:
            for a in psutil.net_if_addrs().get(target_iface, []):
                if a.family == psutil.AF_LINK: 
                    target_mac_val = a.address

    if not target_ip_val or target_ip_val == "127.0.0.1":
        return jsonify({"error": "Could not determine local IP subnet."})

    target_subnet = f"{target_ip_val.rsplit('.', 1)[0]}.0/24"
    if target_iface: conf.iface = target_iface
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- 1. PERFORM SCAN FIRST ---
    ans = []
    try:
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                     timeout=3, retry=2, verbose=0, inter=0.02, 
                     iface=target_iface, promisc=False)
    except Exception as e:
        print(f"[*] Scapy targeted bind failed on {target_iface}: {e}. Retrying globally...")
        try:
            ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                         timeout=3, retry=2, verbose=0, inter=0.02, promisc=False)
        except: pass

    scanned_results = []
    with ThreadPoolExecutor(max_workers=worker_cfg['scan_workers']) as executor:
        futures = [executor.submit(process_device_info, received) for _, received in ans]
        for future in futures: scanned_results.append(future.result())

    # --- 2. OS-AGNOSTIC PING SWEEP FALLBACK ---
    if len(scanned_results) <= 2:
        print("[*] Scapy scan found few devices. Initiating OS Ping Sweep...")
        def fast_ping(ip_str):
            sys_plat = platform.system().lower()
            if sys_plat == 'windows':
                cmd = ['ping', '-n', '1', '-w', '500', ip_str]
            elif sys_plat == 'darwin':
                cmd = ['ping', '-c', '1', '-W', '500', ip_str]
            else:
                cmd = ['ping', '-c', '1', '-W', '1', ip_str]
            try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass

        network = ipaddress.IPv4Network(target_subnet, strict=False)
        with ThreadPoolExecutor(max_workers=worker_cfg['ping_workers']) as executor:
            for ip in network.hosts(): executor.submit(fast_ping, str(ip))
        
        try:
            arp_out = subprocess.check_output(["arp", "-a"], text=True)
            found_ips = [d['ip'] for d in scanned_results]
            arp_devices = []
            
            for match in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})', arp_out):
                ip_found, raw_mac = match.groups()
                mac_found = ':'.join([p.zfill(2) for p in raw_mac.replace('-', ':').split(':')]).lower()
                
                if ip_found not in found_ips and not mac_found.startswith('ff:ff') and not mac_found.startswith('01:00:5e') and ipaddress.IPv4Address(ip_found) in network:
                    class MockReceived:
                        ip = ip_found
                        psrc = ip_found
                        hwsrc = mac_found
                        mac = mac_found
                    arp_devices.append(MockReceived())
                    found_ips.append(ip_found)

            if arp_devices:
                with ThreadPoolExecutor(max_workers=worker_cfg['scan_workers']) as executor:
                    futures = [executor.submit(process_device_info, dev) for dev in arp_devices]
                    for future in futures: scanned_results.append(future.result())
        except: pass

    # --- 3. EXTRACT GATEWAY MAC FROM RESULTS ---
    ext_info = get_extended_iface_info()
    gateway_ip = ext_info.get(target_iface, {}).get("gateway", "-")
    gateway_mac = None

    found_ips = [d["ip"] for d in scanned_results]
    
    # Try to find router in the scan results first
    for device in scanned_results:
        if device["ip"] == gateway_ip:
            gateway_mac = device["mac"]
            if "(Router)" not in device["hostname"]:
                device["hostname"] = f"{device['hostname']} (Router)"
            break

    # If missing, try direct targeted ARP, otherwise NO_MAC fallback
    if not gateway_mac and gateway_ip != "-" and gateway_ip != "Unknown":
        gateway_mac = get_gateway_mac(gateway_ip)
        if not gateway_mac:
            gateway_mac = f"NO_MAC_{int(time.time()*1000)}"
        scanned_results.append({
            "ip": gateway_ip, "mac": gateway_mac,
            "hostname": f"{resolve_hostname(gateway_ip, gateway_mac)} (Router)",
            "vendor": get_mac_vendor(gateway_mac, fetch_online=False),
            "services": check_open_ports(gateway_ip)['services']
        })
    elif not gateway_mac:
        gateway_mac = f"NO_MAC_{int(time.time()*1000)}"

    # Inject localhost if missing
    if target_ip_val not in found_ips and target_mac_val and target_ip_val != "127.0.0.1":
        scanned_results.append({
            "ip": target_ip_val, "mac": target_mac_val,
            "hostname": f"{socket.gethostname()} (This device)",
            "vendor": get_mac_vendor(target_mac_val, fetch_online=False),
            "services": check_open_ports(target_ip_val)['services']
        })

    # --- 4. DB NETWORK CREATION & DEVICE SAVING ---
    try:
        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            cursor = conn.cursor()
            
            # VLAN Safe Check (MAC + IP)
            cursor.execute("SELECT id, name FROM networks WHERE gateway_mac=? AND gateway_ip=?", (gateway_mac, gateway_ip))
            row = cursor.fetchone()
            
            if row:
                network_id = row[0]
                final_network_name = row[1]
                cursor.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, network_id))
            else:
                final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                cursor.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip) VALUES (?, ?, ?, ?)", 
                               (gateway_mac, final_network_name, current_time, gateway_ip))
                network_id = cursor.lastrowid

            cursor.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))

            for device in scanned_results:
                cursor.execute("SELECT custom_name FROM devices WHERE mac_address=? AND network_id=?", (device["mac"], network_id))
                existing = cursor.fetchone()
                final_name = existing[0] if existing and existing[0] else ""
                
                if not final_name:
                    cursor.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (device["mac"],))
                    glob = cursor.fetchone()
                    if glob: final_name = glob[0]

                cursor.execute("""
                    INSERT INTO devices (mac_address, network_id, hostname, custom_name, ip_address, previous_ip, discovery_status, last_seen, services, is_online, vendor, last_network_name)
                    VALUES (?, ?, ?, ?, ?, NULL, 'New Device', ?, ?, 1, ?, ?)
                    ON CONFLICT(mac_address, network_id) DO UPDATE SET
                    hostname=excluded.hostname, 
                    custom_name=COALESCE(?, devices.custom_name),
                    previous_ip = CASE 
                        WHEN devices.ip_address != excluded.ip_address AND devices.ip_address != '0.0.0.0' THEN devices.ip_address 
                        ELSE devices.previous_ip 
                    END,
                    discovery_status = 'Seen Before',
                    ip_address=excluded.ip_address, 
                    last_seen=excluded.last_seen,
                    services=excluded.services, 
                    is_online=1,
                    vendor=excluded.vendor,
                    last_network_name=excluded.last_network_name
                """, (device["mac"], network_id, device["hostname"], final_name, device["ip"], current_time, device["services"], device["vendor"], final_network_name, final_name))

                cursor.execute("""
                    INSERT INTO device_scans (mac_address, network_id, ip_address, hostname, services, timestamp, network_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (device["mac"], network_id, device["ip"], device["hostname"], device["services"], current_time, final_network_name))

            conn.commit()
            return jsonify({"network_id": network_id, "network_name": final_network_name, "devices": scanned_results})
            
    except Exception as e: 
        return jsonify({"error": f"DB Error: {str(e)}"})

@app.route('/api/scan_network_stream')
def scan_network_stream():
    def generate():
        try:
            pinned_mac, target_iface, target_ip_val, target_mac_val = None, None, None, None

            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
                if row: pinned_mac = row[0]

            if pinned_mac:
                for name, addrs in psutil.net_if_addrs().items():
                    for a in addrs:
                        if a.family == psutil.AF_LINK and a.address == pinned_mac:
                            target_iface = name
                            for a2 in addrs:
                                if a2.family == socket.AF_INET: target_ip_val = a2.address
                            target_mac_val = pinned_mac
                            break

            if not target_iface or not target_ip_val:
                target_iface = get_active_interface_name()
                if target_iface:
                    target_ip_val = get_local_ip()
                    for a in psutil.net_if_addrs().get(target_iface, []):
                        if a.family == psutil.AF_LINK: target_mac_val = a.address

            if not target_iface or not target_ip_val or target_ip_val == "127.0.0.1":
                yield f"data: {json.dumps({'type': 'error', 'message': 'No usable or visible network adapter found.'})}\n\n"
                return

            target_subnet = f"{target_ip_val.rsplit('.', 1)[0]}.0/24"
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            worker_cfg = get_worker_config() if 'get_worker_config' in globals() else {"scan_workers": 20, "ping_workers": 30}

            # --- 1. PERFORM SCAN FIRST ---
            ans = []
            try:
                ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                             timeout=2, retry=1, verbose=0, inter=0.01, 
                             iface=target_iface, promisc=False)
            except Exception as e:
                print(f"[*] Scapy targeted bind failed on {target_iface}: {e}. Retrying globally...")
                try:
                    ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                                 timeout=2, retry=1, verbose=0, inter=0.01, promisc=False)
                except Exception as e2:
                    print(f"[*] Scapy global bind failed: {e2}")

            raw_candidates = [received for _, received in ans]

            class MockDev:
                def __init__(self, ip, mac): 
                    self.ip, self.mac = ip, mac
                    self.psrc, self.hwsrc = ip, mac

            # --- 2. OS-AGNOSTIC PING SWEEP FALLBACK ---
            if len(raw_candidates) <= 1:
                print("[*] Scapy scan found few devices. Initiating OS Ping Sweep...")
                def fast_ping(ip_str):
                    sys_plat = platform.system().lower()
                    if sys_plat == 'windows':
                        cmd = ['ping', '-n', '1', '-w', '500', ip_str]
                    elif sys_plat == 'darwin':
                        cmd = ['ping', '-c', '1', '-W', '500', ip_str]
                    else:
                        cmd = ['ping', '-c', '1', '-W', '1', ip_str]
                    try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except: pass

                network = ipaddress.IPv4Network(target_subnet, strict=False)
                with ThreadPoolExecutor(max_workers=worker_cfg['ping_workers']) as executor:
                    for ip_obj in network.hosts(): executor.submit(fast_ping, str(ip_obj))
                
                try:
                    arp_out = subprocess.check_output(["arp", "-a"], text=True)
                    found_ips = [getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates]
                    for match in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})', arp_out):
                        ip_found, raw_mac = match.groups()
                        mac_found = ':'.join([p.zfill(2) for p in raw_mac.replace('-', ':').split(':')]).lower()
                        if ip_found not in found_ips and not mac_found.startswith('ff:ff') and not mac_found.startswith('01:00:5e') and ipaddress.IPv4Address(ip_found) in network:
                            raw_candidates.append(MockDev(ip_found, mac_found))
                            found_ips.append(ip_found)
                except Exception as e:
                    print(f"[*] OS Ping Sweep Parsing Failed: {e}")

            # --- 3. EXTRACT GATEWAY MAC FROM RESULTS ---
            ext_info = get_extended_iface_info()
            gateway_ip = ext_info.get(target_iface, {}).get("gateway", "-")
            gateway_mac = None

            found_ips = []
            for d in raw_candidates:
                d_ip = getattr(d, 'psrc', getattr(d, 'ip', ''))
                found_ips.append(d_ip)
                if d_ip == gateway_ip:
                    gateway_mac = getattr(d, 'hwsrc', getattr(d, 'mac', None))

            # Try direct targeted ARP if not found in ping sweep, otherwise NO_MAC
            if not gateway_mac and gateway_ip != "-" and gateway_ip != "Unknown":
                gateway_mac = get_gateway_mac(gateway_ip)
                if not gateway_mac:
                    gateway_mac = f"NO_MAC_{int(time.time()*1000)}"
                raw_candidates.append(MockDev(gateway_ip, gateway_mac))
                found_ips.append(gateway_ip)
            elif not gateway_mac:
                gateway_mac = f"NO_MAC_{int(time.time()*1000)}"

            # Inject localhost if missing
            if target_ip_val not in found_ips and target_mac_val:
                raw_candidates.append(MockDev(target_ip_val, target_mac_val))

            # --- 4. DB NETWORK CREATION / LOOKUP ---
            with sqlite3.connect(DB_NAME, timeout=10) as conn:
                c = conn.cursor()
                
                # VLAN Safe Check (MAC + IP)
                c.execute("SELECT id, name FROM networks WHERE gateway_mac=? AND gateway_ip=?", (gateway_mac, gateway_ip))
                row = c.fetchone()
                if row:
                    network_id, final_network_name = row[0], row[1]
                    c.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, network_id))
                else:
                    final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                    c.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip) VALUES (?, ?, ?, ?)", 
                              (gateway_mac, final_network_name, current_time, gateway_ip))
                    network_id = c.lastrowid
                c.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))
                conn.commit()

            # --- 5. YIELD INIT TO FRONTEND ---
            yield f"data: {json.dumps({'type': 'init', 'network_id': network_id, 'network_name': final_network_name})}\n\n"

            # --- 6. PROCESS DEVICES STREAM ---
            with ThreadPoolExecutor(max_workers=worker_cfg['scan_workers']) as executor:
                future_to_dev = {executor.submit(process_device_quick, d): d for d in raw_candidates}
                
                for future in as_completed(future_to_dev):
                    try:
                        dev = future.result()
                        if dev["ip"] == gateway_ip and "(Router)" not in dev["hostname"]:
                            dev["hostname"] += " (Router)"
                        elif dev["ip"] == target_ip_val and "(This device)" not in dev["hostname"]:
                            dev["hostname"] += " (This device)"

                        with sqlite3.connect(DB_NAME, timeout=10) as conn:
                            c = conn.cursor()
                            c.execute("SELECT custom_name, custom_vendor, vendor, ip_address, previous_ip FROM devices WHERE mac_address=? AND network_id=?", (dev["mac"], network_id))
                            row = c.fetchone()
                            existing_name = row[0] if row and row[0] else ""
                            existing_custom_v = row[1] if row and row[1] else ""
                            existing_vendor = row[2] if row and row[2] else ""
                            db_ip = row[3] if row else None
                            db_prev = row[4] if row else None

                            if not existing_name:
                                g_name = c.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (dev["mac"],)).fetchone()
                                if g_name: existing_name = g_name[0]

                            if not existing_custom_v:
                                g_vend = c.execute("SELECT custom_vendor FROM global_device_vendors WHERE mac_address=?", (dev["mac"],)).fetchone()
                                if g_vend: existing_custom_v = g_vend[0]

                            dev["custom_name"] = existing_name
                            dev["custom_vendor"] = existing_custom_v
                            dev["vendor"] = existing_vendor
                            dev["is_online"] = 1
                            dev["ip_address"] = dev["ip"]
                            dev["mac_address"] = dev["mac"]
                            
                            if db_ip and db_ip != dev["ip"] and db_ip != "0.0.0.0":
                                dev["previous_ip"] = db_ip
                                dev["discovery_status"] = "Seen Before"
                            else:
                                dev["previous_ip"] = db_prev
                                dev["discovery_status"] = "Seen Before" if row else "New Device"

                            c.execute("""
                                INSERT INTO devices (mac_address, network_id, hostname, custom_name, custom_vendor, ip_address, previous_ip, discovery_status, last_seen, services, is_online, vendor, last_network_name)
                                VALUES (?, ?, ?, ?, ?, ?, NULL, 'New Device', ?, ?, 1, ?, ?)
                                ON CONFLICT(mac_address, network_id) DO UPDATE SET
                                    hostname=excluded.hostname,
                                    custom_name=COALESCE(?, devices.custom_name),
                                    custom_vendor=COALESCE(?, devices.custom_vendor),
                                    previous_ip=CASE WHEN devices.ip_address != excluded.ip_address AND devices.ip_address != '0.0.0.0' THEN devices.ip_address ELSE devices.previous_ip END,
                                    discovery_status='Seen Before',
                                    ip_address=excluded.ip_address,
                                    last_seen=excluded.last_seen,
                                    services=excluded.services,
                                    is_online=1,
                                    vendor=excluded.vendor,
                                    last_network_name=excluded.last_network_name
                            """, (dev["mac"], network_id, dev["hostname"], existing_name, existing_custom_v, dev["ip"], current_time, dev["services"], existing_vendor, final_network_name, existing_name, existing_custom_v))
                            
                            c.execute("""
                                INSERT INTO device_scans (mac_address, network_id, ip_address, hostname, services, timestamp, network_name)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (dev["mac"], network_id, dev["ip"], dev["hostname"], dev["services"], current_time, final_network_name))
                            conn.commit()

                        print(f"[*] Active Device Found: {dev['mac']} ({dev['ip']}) - {dev['hostname']}")
                        yield f"data: {json.dumps({'type': 'device', 'device': dev})}\n\n"
                    except Exception: pass

            # --- 7. APPEND OFFLINE DEVICES ---
            offline_devices = []
            with sqlite3.connect(DB_NAME, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("""
                    SELECT mac_address, hostname, custom_name, custom_vendor, ip_address, 
                           previous_ip, discovery_status, last_seen, services, is_online, vendor 
                    FROM devices 
                    WHERE network_id=? AND is_online=0
                """, (network_id,))
                for r in c.fetchall():
                    dev = dict(r)
                    if not dev.get("custom_name"):
                        g_name = conn.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (dev['mac_address'],)).fetchone()
                        if g_name: dev['custom_name'] = g_name[0]
                    if not dev.get("custom_vendor"):
                        g_vend = conn.execute("SELECT custom_vendor FROM global_device_vendors WHERE mac_address=?", (dev['mac_address'],)).fetchone()
                        if g_vend: dev['custom_vendor'] = g_vend[0]
                    offline_devices.append(dev)

            yield f"data: {json.dumps({'type': 'complete', 'network_id': network_id, 'network_name': final_network_name, 'offline_devices': offline_devices})}\n\n"     

        except Exception as critical_err:
            print(f"[!!!] CRITICAL SCAN ERROR: {critical_err}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Server Error: {str(critical_err)}'})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

@app.route('/api/dns/lookup', methods=['POST'])
def dns_lookup():
    """Performs DNS lookup and logs with network context."""
    domain = request.json.get('domain', '').strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lan_ip, router_ip, net_name = get_current_network_context()

    # --- NEW: Strict Validation ---
    if not domain or domain.startswith('-') or not re.match(r'^[\w\.-]+$', domain):
        return jsonify({"timestamp": ts, "domain": domain or "Invalid", "ip": "-", "status": "Failed"})

    try:
        ip = socket.gethostbyname(domain)
        status = "Resolved"
    except:
        ip = "-"
        status = "Failed"
    
    with sqlite3.connect(DB_NAME, timeout=5) as conn:
        conn.execute("""
            INSERT INTO dns_logs (timestamp, domain, result_ip, record_type, status, router_ip, network_name, lan_ip) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, domain, ip, "A", status, router_ip, net_name, lan_ip))
        conn.commit()
    
    print(f"[*] DNS Lookup: {domain} -> {ip} ({status})")
    return jsonify({"timestamp": ts, "domain": domain, "ip": ip, "status": status})

@app.route('/api/dns/logs')
def get_dns_logs():
    """Fetches the last 100 DNS lookup records."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/dns/clear', methods=['POST'])
def clear_dns_logs():
    """Clears the DNS history log."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM dns_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/tool_logs/update', methods=['POST'])
def update_tool_log():
    """Updates the network name of a DNS or Ping log entry."""
    d = request.json or {}
    log_type = d.get('type')
    item_id = d.get('id')
    new_name = str(d.get('name', '')).strip()[:50] # Safely limit to 50 characters
    
    if log_type not in ['dns', 'ping']:
        return jsonify({"error": "Invalid log type"}), 400
        
    table = 'dns_logs' if log_type == 'dns' else 'ping_logs'
    
    try:
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            conn.execute(f"UPDATE {table} SET network_name = ? WHERE id = ?", (new_name, item_id))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ping/run', methods=['POST'])
def run_ping():
    """
    Executes a system ping (4 packets) and logs latency/loss.
    Now includes Network Context (Router IP, Name, LAN IP).
    """
    target = request.json.get('target', '').strip()
    
    # --- NEW: Strict Validation ---
    if not target or target.startswith('-') or not re.match(r'^[\w\.-]+$', target):
        return jsonify({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target": target or "Invalid",
            "status": "Failed",
            "latency": "N/A",
            "loss": "100%",
            "error": "Invalid target format"
        })
    
    # Determine OS-specific ping command
    # Windows uses '-n', Unix/Mac uses '-c'
    param = '-n' if platform.system().lower()=='windows' else '-c'
    cmd = ['ping', param, '4', target]
    
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latency = "N/A"
    loss = "100%"
    status = "Failed"
    
    # --- GET CONTEXT (Router, Network Name, LAN IP) ---
    # This calls the helper function we added earlier
    lan_ip, router_ip, net_name = get_current_network_context()

    try:
        # Run the ping command and capture output
        # stderr=subprocess.STDOUT ensures we capture errors like "Host unreachable"
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        
        # Parse output for Latency and Packet Loss
        if platform.system().lower() == 'windows':
            # Windows Output: "Average = 24ms", "Lost = 0 (0% loss)"
            if "Average =" in output:
                latency = output.split("Average =")[1].strip().replace("ms", "").strip() + " ms"
            if "Lost =" in output:
                loss_part = output.split("Lost =")[1].split("(")[1]
                loss = loss_part.split(")")[0] # e.g., "0% loss"
        else:
            # Linux / macOS Output: "min/avg/max = ...", "0% packet loss"
            if "avg" in output: 
                # Output format: min/avg/max/mdev = ...
                latency = output.split(" = ")[1].split("/")[1] + " ms"
            if "packet loss" in output:
                loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', output)
                if loss_match: 
                    loss = loss_match.group(1) + "%"
        
        # Determine simple Status
        if "0%" in loss or "0.0%" in loss: 
            status = "Success"
        elif "100%" in loss: 
            status = "Failed"
        else:
            status = "Partial"

    except subprocess.CalledProcessError:
        pass # Ping command returned non-zero exit code (Host Unreachable)
    
    # Log to Database with the new columns
    with sqlite3.connect(DB_NAME, timeout=5) as conn:
        conn.execute("""
            INSERT INTO ping_logs (
                timestamp, target, status, latency, packet_loss, network_context, 
                router_ip, network_name, lan_ip
            ) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, target, status, latency, loss, net_name, router_ip, net_name, lan_ip))
        conn.commit()

    print(f"[*] Ping {target}: {status} (Latency: {latency}, Loss: {loss})")
    return jsonify({
        "timestamp": ts, 
        "target": target, 
        "status": status, 
        "latency": latency, 
        "loss": loss
    })

@app.route('/api/ping/logs')
def get_ping_logs():
    """Fetches the last 100 Ping records."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/ping/clear', methods=['POST'])
def clear_ping_logs():
    """Clears the Ping history log."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM ping_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/export/tool_logs', methods=['POST'])
def export_tool_logs():
    """Exports DNS or Ping logs to CSV including new columns."""
    log_type = request.json.get('type')
    out = io.StringIO()
    writer = csv.writer(out)
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        if log_type == 'dns':
            rows = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Domain', 'Result IP', 'Status', 'Router IP', 'Network', 'LAN IP'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['domain'], r['result_ip'], r['status'], r['router_ip'], r['network_name'], r['lan_ip']])
        else:
            rows = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Target', 'Status', 'Latency', 'Loss', 'Router IP', 'Network', 'LAN IP'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['target'], r['status'], r['latency'], r['packet_loss'], r['router_ip'], r['network_name'], r['lan_ip']])
            
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": f"attachment; filename={log_type}_logs.csv"})

@app.route('/api/system/alerts', methods=['GET'])
def get_system_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                return jsonify(json.load(f))
    except: pass
    return jsonify([])

@app.route('/api/system/alerts/dismiss', methods=['POST'])
def dismiss_system_alert():
    msg = request.json.get('message')
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                alerts = json.load(f)
            if msg in alerts:
                alerts.remove(msg)
            with open(ALERTS_FILE, "w") as f:
                json.dump(alerts, f)
    except: pass
    return jsonify({"status": "success"})

# --- Misc (WiFi, Speedtest, History, Update) ---

@app.route('/api/wifi')
def get_wifi_networks():
    """
    Returns detailed Wi-Fi data grouped by SSID.
    Locks Band and MAC on the same line to fix HTML desync.
    Clusters and averages redundant 'Unknown MAC' signals within a 5dBm variance.
    Enforces strict 2.4GHz -> 5GHz -> 6GHz ordering.
    Standardizes dBm and Percentage separately for all operating systems.
    """
    global re
    networks_dict = {}
    sys_plat = platform.system()
    
    try:
        # ==========================================
        # 1. macOS Implementation (CoreWLAN)
        # ==========================================
        if sys_plat == "Darwin":
            try:
                import CoreWLAN
                import re
                
                wifi_interface = CoreWLAN.CWInterface.interface()
                if not wifi_interface:
                    return jsonify({"error": "Interface Error", "message": "Could not find a Wi-Fi interface."})
                
                active_networks, error = wifi_interface.scanForNetworksWithName_error_(None, None)
                cached_networks = wifi_interface.cachedScanResults()
                
                all_networks = []
                if active_networks:
                    for n in active_networks: all_networks.append(n)
                if cached_networks:
                    for n in cached_networks: all_networks.append(n)
                    
                if all_networks:
                    for i in all_networks:
                        ssid = str(i.ssid()) if i.ssid() else "Hidden Network"
                        
                        mac_val = i.bssid()
                        mac = str(mac_val) if mac_val else f"Unknown_MAC_{id(i)}"
                        
                        # macOS returns dBm natively. Calculate percentage from dBm.
                        dbm_val = int(i.rssiValue()) if i.rssiValue() else None
                        pct_val = max(0, min(100, int((dbm_val + 100) * 2))) if dbm_val is not None else None
                        
                        ch_obj = i.wlanChannel()
                        ch = str(ch_obj.channelNumber()) if ch_obj else "0"
                        
                        band_val = ch_obj.channelBand() if ch_obj else 0
                        if band_val == 1: b = "2.4GHz"
                        elif band_val == 2: b = "5GHz"
                        elif band_val == 3: b = "6GHz"
                        else:
                            try:
                                c = int(ch)
                                if c <= 14: b = "2.4GHz"
                                elif 36 <= c <= 177: b = "5GHz"
                                elif c >= 190: b = "6GHz"
                                else: b = "Unknown"
                            except: b = "Unknown"
                        
                        sec_match = re.search(r'security=(.*?),', str(i))
                        auth = sec_match.group(1) if sec_match else "Unknown"
                        
                        if ssid not in networks_dict:
                            networks_dict[ssid] = {"ssid": ssid, "auth": auth, "bssids": {}}
                        
                        if auth != "Unknown" and networks_dict[ssid]["auth"] == "Unknown":
                            networks_dict[ssid]["auth"] = auth
                            
                        if mac not in networks_dict[ssid]["bssids"]:
                            networks_dict[ssid]["bssids"][mac] = {"dbm": dbm_val, "percent": pct_val, "channel": ch, "band": b}
                        else:
                            if dbm_val is not None:
                                networks_dict[ssid]["bssids"][mac]["dbm"] = dbm_val
                                networks_dict[ssid]["bssids"][mac]["percent"] = pct_val
                            if ch != "0": networks_dict[ssid]["bssids"][mac]["channel"] = ch
                            if b != "Unknown": networks_dict[ssid]["bssids"][mac]["band"] = b

            except ImportError:
                return jsonify({"error": "Missing Library", "message": "Run: pip install pyobjc-framework-CoreWLAN"})
            except Exception as e:
                return jsonify({"error": "macOS Scan Failed", "message": str(e)})

        # ==========================================
        # 2. Windows Implementation (netsh)
        # ==========================================
        elif sys_plat == "Windows":
            subprocess.run(["powershell", "-Command", "Get-NetAdapter | Where-Object {$_.MediaType -eq 'Native 802.11'} | Restart-NetAdapter"], capture_output=True)
            time.sleep(3) 

            process = subprocess.Popen(
                "netsh wlan show networks mode=bssid", 
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                text=True, encoding='cp437', errors='ignore'
            )
            stdout, _ = process.communicate(timeout=15)

            current_ssid = None
            current_mac = None
            
            for line in stdout.split('\n'):
                line = line.strip()
                if not line: continue

                if line.lower().startswith("ssid"):
                    parts = line.split(":", 1)
                    current_ssid = parts[1].strip() if len(parts) > 1 else "Hidden Network"
                    current_mac = None 
                    if current_ssid not in networks_dict:
                        networks_dict[current_ssid] = {"ssid": current_ssid, "auth": "Unknown", "bssids": {}}

                elif current_ssid:
                    if "authentication" in line.lower():
                        networks_dict[current_ssid]["auth"] = line.split(":", 1)[1].strip()
                    
                    elif line.lower().startswith("bssid"):
                        raw_mac = line.split(":", 1)[1].strip()
                        current_mac = raw_mac if raw_mac else f"Unknown_MAC_{time.time()}"
                        if current_mac not in networks_dict[current_ssid]["bssids"]:
                            networks_dict[current_ssid]["bssids"][current_mac] = {"dbm": None, "percent": None, "channel": "0", "band": "Unknown"}
                            
                    elif current_mac and "signal" in line.lower():
                        raw_sig = line.split(":", 1)[1].strip()
                        try:
                            # Windows returns percentage natively. Calculate dBm from percentage.
                            pct_val = int(''.join(filter(str.isdigit, raw_sig)))
                            dbm_val = int((pct_val / 2) - 100)
                        except ValueError:
                            pct_val = None
                            dbm_val = None
                            
                        networks_dict[current_ssid]["bssids"][current_mac]["percent"] = pct_val
                        networks_dict[current_ssid]["bssids"][current_mac]["dbm"] = dbm_val
                    
                    elif current_mac and "channel" in line.lower():
                        ch = line.split(":", 1)[1].strip()
                        if re.match(r"^\d{1,3}$", ch):
                            networks_dict[current_ssid]["bssids"][current_mac]["channel"] = ch
                            
                    elif current_mac and "band" in line.lower():
                        raw_band = line.split(":", 1)[1].strip().replace(" ", "")
                        if "2.4" in raw_band: b = "2.4GHz"
                        elif "5" in raw_band: b = "5GHz"
                        elif "6" in raw_band: b = "6GHz"
                        else: b = raw_band
                        networks_dict[current_ssid]["bssids"][current_mac]["band"] = b

        # ==========================================
        # 3. Linux Implementation (nmcli)
        # ==========================================
        elif sys_plat == "Linux":
            output = subprocess.check_output(["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,FREQ,SECURITY", "dev", "wifi"], text=True)
            for line in output.strip().split('\n'):
                parts = re.split(r'(?<!\\):', line)
                parts = [p.replace('\\:', ':') for p in parts]
                
                if len(parts) >= 6:
                    ssid = parts[0] or "Hidden Network"
                    mac = parts[1].strip() if parts[1] else f"Unknown_MAC_{time.time()}"
                    
                    # Linux returns percentage natively. Calculate dBm from percentage.
                    sig_raw = parts[2]
                    if sig_raw and sig_raw.isdigit():
                        pct_val = int(sig_raw)
                        dbm_val = int((pct_val / 2) - 100)
                    else:
                        pct_val = None
                        dbm_val = None
                        
                    ch = parts[3] if parts[3].isdigit() else "0"
                    
                    freq_digits = ''.join(filter(str.isdigit, parts[4]))
                    freq = int(freq_digits) if freq_digits else 0
                    
                    if 2400 <= freq <= 2500: b = "2.4GHz"
                    elif 5150 <= freq <= 5895: b = "5GHz"
                    elif freq >= 5925: b = "6GHz"
                    else: b = "Unknown"

                    if ssid not in networks_dict:
                        networks_dict[ssid] = {"ssid": ssid, "auth": parts[5], "bssids": {}}
                    
                    if parts[5] != "Unknown" and networks_dict[ssid]["auth"] == "Unknown":
                        networks_dict[ssid]["auth"] = parts[5]
                        
                    networks_dict[ssid]["bssids"][mac] = {"dbm": dbm_val, "percent": pct_val, "channel": ch, "band": b}

    except Exception as e: 
        return jsonify({"error": "Critical Error", "message": str(e)})

    # ==========================================
    # 4. Flattening, Clustering Unknowns, & Formatting
    # ==========================================
    final_networks = []
    
    def get_band_weight(band_str):
        if "2.4" in band_str: return 1
        if "5" in band_str: return 2
        if "6" in band_str: return 3
        return 4

    for net in networks_dict.values():
        bands_dict = {}
        raw_bssids = [] # Hidden list for the CSV exporter
        
        # 1. Bucket all MACs and signals by Band
        for mac, data in net["bssids"].items():
            b = data.get("band", "Unknown")
            dbm_val = data.get("dbm")
            pct_val = data.get("percent")
            ch = data.get("channel", "0")
            
            # Build the raw list for the CSV export containing separated fields
            raw_bssids.append({
                "mac": "Unknown" if mac.startswith("Unknown_MAC_") else mac,
                "dbm": dbm_val if dbm_val is not None else "",
                "percent": pct_val if pct_val is not None else "",
                "channel": ch,
                "band": b
            })
            
            # Format string for UI display
            if dbm_val is not None and pct_val is not None:
                sig_display = f"{dbm_val} dBm ({pct_val}%)"
            elif dbm_val is not None:
                sig_display = f"{dbm_val} dBm"
            elif pct_val is not None:
                sig_display = f"{pct_val}%"
            else:
                sig_display = ""
            
            if b not in bands_dict:
                bands_dict[b] = {"known_macs": [], "unknown_signals": [], "channels": set()}
            
            if ch and ch != "0":
                bands_dict[b]["channels"].add(ch)
            
            if mac.startswith("Unknown_MAC_"):
                if dbm_val is not None:
                    bands_dict[b]["unknown_signals"].append(dbm_val)
            else:
                bands_dict[b]["known_macs"].append({"mac": mac, "signal": sig_display, "dbm": dbm_val if dbm_val is not None else -100})
        
        display_bands = []
        display_signals = []
        display_channels = set()
        ui_sort_dbm = -100 # Track highest dbm in this SSID for sorting the UI cards
        
        sorted_bands = sorted(bands_dict.keys(), key=get_band_weight)
        
        # 2. Build the output formatting
        for b in sorted_bands:
            data = bands_dict[b]
            
            # --- Unknown MACs: Apply 5dBm Clustering using raw dBm values ---
            if data["unknown_signals"]:
                display_bands.append(f"{b} (Unknown MAC)")
                
                clusters = []
                for val in data["unknown_signals"]:
                    placed = False
                    for cluster in clusters:
                        avg_sig = sum(cluster) / len(cluster)
                        if abs(val - avg_sig) <= 5:
                            cluster.append(val)
                            placed = True
                            break
                    if not placed:
                        clusters.append([val])
                
                # Format averaged signals
                averaged_sigs = []
                for c in clusters:
                    avg_dbm = int(round(sum(c) / len(c)))
                    avg_pct = max(0, min(100, int((avg_dbm + 100) * 2)))
                    averaged_sigs.append({"dbm": avg_dbm, "pct": avg_pct})
                    if avg_dbm > ui_sort_dbm: ui_sort_dbm = avg_dbm
                
                # Sort from strongest to weakest
                sorted_sigs = sorted(averaged_sigs, key=lambda x: x["dbm"], reverse=True)
                for sig in sorted_sigs:
                    display_signals.append(f"{sig['dbm']} dBm ({sig['pct']}%) ({b})" if b != "Unknown" else f"{sig['dbm']} dBm ({sig['pct']}%)")
                    
            # --- Known MACs: Kept individual ---
            for kmac in data["known_macs"]:
                display_bands.append(f"{b} ({kmac['mac']})")
                if kmac["signal"]:
                    display_signals.append(f"{kmac['signal']} ({b})" if b != "Unknown" else kmac["signal"])
                if kmac["dbm"] > ui_sort_dbm: ui_sort_dbm = kmac["dbm"]
                    
            display_channels.update(data["channels"])
            
        sorted_channels = sorted(list(display_channels), key=lambda x: int(x) if str(x).isdigit() else 0)
        
        final_networks.append({
            "ssid": net["ssid"],
            "mac": "", # UI layout spacer
            "signal": "<br>".join(display_signals),
            "channel": ", ".join(sorted_channels),
            "auth": net["auth"],
            "band": "<br>".join(display_bands),
            "raw_bssids": raw_bssids,
            "_sort_dbm": ui_sort_dbm # Temporary key for sorting the UI cards
        })

    # Sort the final UI cards by the absolute strongest signal overall
    final_networks.sort(key=lambda x: x['_sort_dbm'], reverse=True)
    # Clean up the temporary sort key before saving to DB
    for n in final_networks:
        n.pop('_sort_dbm', None)

    # Auto-log scan to database
    if final_networks:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            auto_name = f"Auto-Scan {timestamp}"
            
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                c = conn.cursor()
                c.execute(
                    "INSERT INTO wifi_history (timestamp, scan_name, comments, results_json) VALUES (?, ?, ?, ?)",
                    (timestamp, auto_name, "Automatically logged", json.dumps(final_networks))
                )
                conn.commit()
            print(f"[✓] Wi-Fi scan auto-logged: {auto_name}")
        except Exception as db_err:
            print(f"[!] Database Auto-log Error: {db_err}")

    print(f"[*] Wi-Fi Scan Complete. Full Results:\n{json.dumps(final_networks, indent=2)}")
    return jsonify(final_networks)

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    """
    Executes an Ookla Speedtest.
    - Windows: Uses --ip with the local IP.
    - macOS/Linux: Uses --interface with the hardware name (e.g., en0).
    Provides specific UI error messages if a pinned adapter fails.
    """
    d = request.json
    target_iface_name = None
    device_ip = "-" 
    pinned_mac = None

    # 1. Identify the Adapter and its IP
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
            
            # --- NEW: Fetch hidden interfaces ---
            hidden_rows = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_visible = 0").fetchall()
            hidden_macs = [r[0] for r in hidden_rows]
            
            interfaces = psutil.net_if_addrs()
            
            for name, addrs in interfaces.items():
                temp_mac, temp_ip = None, None
                for a in addrs:
                    if a.family == psutil.AF_LINK: temp_mac = a.address
                    if a.family == socket.AF_INET: temp_ip = a.address
                
                # --- NEW: Skip this loop iteration completely if hidden ---
                if temp_mac in hidden_macs:
                    continue
                
                # If pinned, match by MAC; otherwise, find the active one
                if pinned_mac and temp_mac == pinned_mac:
                    target_iface_name = name
                    device_ip = temp_ip
                    break
                elif not pinned_mac and temp_ip == get_local_ip():
                    target_iface_name = name
                    device_ip = temp_ip
    except Exception as e:
        print(f"[*] Speedtest adapter lookup failed: {e}")

    # --- NEW: Block the speedtest if no visible interface is found ---
    if not target_iface_name and not pinned_mac:
        return jsonify({"error": "No usable or visible network adapter found."})

    # --- ENHANCED HELPER TO PARSE OOKLA OUTPUT AND PRINT ERRORS ---
    def parse_ookla(raw_text):
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as parse_err:
            # Print the FULL raw output to the terminal immediately upon error
            print(f"\n[!] OOKLA JSON PARSE WARNING: {parse_err}")
            print(f"[!] FULL RAW OUTPUT RECEIVED:\n{'-'*50}\n{raw_text}\n{'-'*50}\n")
            
            # Ookla CLI sometimes outputs multiple JSON objects (e.g., logs then results)
            # or mixes warnings with the JSON. We extract the LAST valid JSON line.
            for line in reversed(raw_text.strip().split('\n')):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        recovered_json = json.loads(line)
                        print("[*] Successfully recovered valid JSON from the last line.")
                        return recovered_json
                    except:
                        pass
            
            raise ValueError("Could not extract valid JSON. See terminal for raw output.")

    try:
        # 2. Path to the CLI Binary
        base_dir = app.root_path 
        st_path = os.path.join(base_dir, "venv", "Scripts", "speedtest.exe") if platform.system() == "Windows" else os.path.join(base_dir, "venv", "bin", "speedtest")
        cmd_path = st_path if os.path.exists(st_path) else "speedtest"
        
        # 3. Build Command
        base_cmd = [cmd_path, "--format=json", "--accept-license", "--accept-gdpr"]
        cmd = list(base_cmd)
        
        # 4. Apply OS-Specific Binding
        if platform.system() == "Windows":
            if device_ip and device_ip != "-":
                cmd.extend(["--ip", device_ip])
        else:
            # macOS/Linux require the Interface Name (e.g., 'en0'), not the IP
            if target_iface_name:
                cmd.extend(["--interface", target_iface_name])
        
        # 5. Execute with Fallback Logic
        try:
            # Capture stderr so we can read the actual Ookla error
            raw_out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            res = parse_ookla(raw_out)
        except subprocess.CalledProcessError as e:
            # If an adapter is specifically pinned, do NOT fallback to a global route
            if pinned_mac:
                raise e
                
            print(f"[*] Speedtest strict bind failed (Exit {e.returncode}). Retrying globally...")
            # Fallback: Try without --ip or --interface if no pin is set
            raw_out = subprocess.check_output(base_cmd, stderr=subprocess.STDOUT, text=True)
            res = parse_ookla(raw_out)
            # Reset device_ip since we used the global default route
            device_ip = get_local_ip()
            
        # 6. Check for internal Ookla JSON errors
        if "error" in res:
            print(f"\n[!] SPEEDTEST INTERNAL ERROR: {res.get('error')}\n")
            if pinned_mac:
                return jsonify({"error": "Speed Test not able to complete via pinned adapter. Either unpin adapter or try again later."})
            return jsonify({"error": "Speedtest Failed try again later"})
        
        # 7. Format and Save Results
        down = f"{(res['download']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        up = f"{(res['upload']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', '-')
        
        name = d.get('network_name') or "Unnamed Network"
        conn_type = d.get('connection_type') or "Ethernet"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            conn.execute("""
                INSERT INTO history (timestamp, network_name, connection_type, download, upload, ping, wan_ip, device_ip, isp) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, name, conn_type, down, up, ping, wan, device_ip, isp))
            conn.commit()
            
        print(f"[*] Speedtest Results: Down {down} | Up {up} | Ping {ping}")
        return jsonify({"download": down, "upload": up, "ping": ping})

    except subprocess.CalledProcessError as e:
        # Print the actual error output directly to the terminal for debugging
        print(f"\n[!] SPEEDTEST CLI FAILED (Exit Code: {e.returncode})")
        print(f"[!] Raw Error Output:\n{e.output}\n")
        
        # If pinned, strictly show the pinned error message
        if pinned_mac:
            return jsonify({"error": "Speed Test not able to complete via pinned adapter. Either unpin adapter or try again later."})
        
        # Determine if the error is related to no internet / configuration for unpinned tests
        err_text = str(e.output).lower()
        if e.returncode == 2 or "configuration" in err_text or "network unreachable" in err_text or "cannot retrieve" in err_text:
            return jsonify({"error": "Can not Connect to speed test server, check internet connection or retry later."})
        else:
            return jsonify({"error": "Speedtest Failed try again later"})
            
    except Exception as e:
        # Catch-all for other Python errors (e.g. JSON parsing failure, missing binary)
        print(f"\n[!] SPEEDTEST EXCEPTION: {str(e)}\n")
        if pinned_mac:
            return jsonify({"error": "Speed Test not able to complete via pinned adapter. Either unpin adapter or try again later."})
        return jsonify({"error": "Speedtest Failed try again later"})

@app.route('/api/get_last_name')
def get_last_name():
    """Gets the most recent network label for the current location."""
    info = get_isp_info()
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        row = conn.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (info['ip'],)).fetchone()
        return jsonify({"last_name": row[0] if row else "", "wan_ip": info['ip'], "isp": info['isp']})

@app.route('/api/history')
def get_history():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row  # THIS IS KEY
        cursor = conn.execute("SELECT * FROM history ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        # Convert sqlite objects to a list of dictionaries for JSON
        return jsonify([dict(ix) for ix in rows])

@app.route('/api/history/update', methods=['POST'])
def update_history():
    """Renames a history entry and updates its connection type."""
    d = request.json
    name = str(d.get('name', '')).strip()[:50]
    c_type = str(d.get('type', '')).strip()[:50]
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE history SET network_name = ?, connection_type = ? WHERE id = ?", 
                     (name, c_type, d.get('id'))) # Use variables
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/bulk_delete', methods=['POST'])
def bulk_delete():
    d = request.json
    table_map = {'networks': 'networks', 'wifi': 'wifi_history', 'history': 'history', 'dns': 'dns_logs', 'ping': 'ping_logs', 'devices': 'devices'}
    table = table_map.get(d.get('type'))
    ids = d.get('ids', [])
    
    if not table or not ids: return jsonify({"error": "Invalid parameters"}), 400
    placeholders = ','.join(['?'] * len(ids))
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        if table == 'devices':
            conn.execute(f"DELETE FROM devices WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM global_device_names WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM global_device_vendors WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM device_scans WHERE mac_address IN ({placeholders})", ids)
        elif table == 'wifi_history':
            # Soft-delete for Wi-Fi scans
            conn.execute(f"UPDATE wifi_history SET is_deleted=1 WHERE id IN ({placeholders})", ids)
        else:
            conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/bulk_export', methods=['POST'])
def bulk_export_networks():
    """Generates a ZIP file containing multiple CSVs for selected Networks."""
    ids = request.json.get('ids', [])
    if not ids: return jsonify({"error": "No IDs provided"}), 400
    
    # Reverse map to convert service names back to port numbers
    PORT_MAP = {"SSH": "22", "HTTP": "80", "HTTPS": "443", "HTTP (8080)": "8080", "HTTPS (8443)": "8443", "Flask/UPnP": "5000", "Portainer/Admin": "9000"}
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            for net_id in ids:
                net = conn.execute("SELECT name FROM networks WHERE id=?", (net_id,)).fetchone()
                if not net: continue
                
                net_name = get_safe_filename(net['name'])
                devices = conn.execute("SELECT hostname, custom_name, ip_address, previous_ip, discovery_status, mac_address, is_online, services FROM devices WHERE network_id=?", (net_id,)).fetchall()
                
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                writer.writerow(['Hostname', 'Custom Name', 'IP Address', 'MAC Address', 'Status', 'Services (Ports)', 'History'])
                for d in devices:
                    history_text = d['discovery_status']
                    if d['previous_ip']: history_text = f"IP Changed ({d['previous_ip']})"
                    
                    # Convert services string to ports
                    raw_services = d['services'] or "None"
                    port_str = "None" if raw_services == "None" else ", ".join([PORT_MAP.get(s.strip(), s.strip()) for s in raw_services.split(",")])
                    
                    writer.writerow([d['hostname'], d['custom_name'], d['ip_address'], d['mac_address'], 'Online' if d['is_online'] else 'Offline', port_str, history_text])

                zf.writestr(f"network_{net_id}_{net_name}.csv", csv_out.getvalue())
    
    memory_file.seek(0)
    return send_file(memory_file, download_name="networks_bulk_export.zip", as_attachment=True)

@app.route('/api/wifi/bulk_export', methods=['POST'])
def bulk_export_wifi():
    """Generates a ZIP file containing multiple CSVs for selected Wi-Fi scans."""
    ids = request.json.get('ids', [])
    if not ids: return jsonify({"error": "No IDs provided"}), 400
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            for scan_id in ids:
                scan = conn.execute("SELECT scan_name, results_json FROM wifi_history WHERE id=?", (scan_id,)).fetchone()
                if not scan: continue
                
                scan_name = get_safe_filename(scan['scan_name'])
                results = json.loads(scan['results_json'])
                
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                
                writer.writerow(["SSID", "MAC", "Signal (dBm)", "Signal (%)", "Channel", "Band", "Authentication"])
                for net in results:
                    if "raw_bssids" in net and net["raw_bssids"]:
                        for b in net["raw_bssids"]:
                            if "dbm" in b or "percent" in b:
                                writer.writerow([net.get('ssid',''), b.get('mac', ''), b.get('dbm',''), b.get('percent',''), b.get('channel',''), b.get('band',''), net.get('auth','')])
                            else:
                                writer.writerow([net.get('ssid',''), b.get('mac', ''), b.get('signal',''), "-", b.get('channel',''), b.get('band',''), net.get('auth','')])
                    else:
                        writer.writerow([net.get('ssid',''), net.get('mac', ''), net.get('signal','').replace('<br>', ' | '), "-", net.get('channel',''), net.get('band','').replace('<br>', ' | '), net.get('auth','')])

                zf.writestr(f"wifi_scan_{scan_id}_{scan_name}.csv", csv_out.getvalue())
    
    memory_file.seek(0)
    return send_file(memory_file, download_name="wifi_scans_bulk_export.zip", as_attachment=True)

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clears all speed test history."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM history")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/history/export', methods=['POST'])
def export_history():
    """
    Exports speed test history to CSV.
    Includes the new Device IP column and renames WAN IP.
    """
    d = request.json
    rows = d.get('rows', [])
    
    # If no specific rows were sent from the frontend, fetch all from the DB
    if not rows:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()]
    
    out = io.StringIO()
    writer = csv.writer(out)
    
    # Updated Header Row with Device IP and WAN IP
    writer.writerow(['Timestamp', 'Network Name', 'Type', 'Download', 'Upload', 'Ping', 'Device IP', 'WAN IP', 'ISP'])
    
    # Write Data Rows
    for r in rows: 
        writer.writerow([
            r.get('timestamp'), 
            r.get('network_name'), 
            r.get('connection_type'), 
            r.get('download'), 
            r.get('upload'), 
            r.get('ping'), 
            r.get('device_ip', '-'), # New Device IP field
            r.get('wan_ip', '-'),    # Maps to WAN IP header
            r.get('isp', '-')
        ])
    
    return Response(
        out.getvalue(), 
        mimetype="text/csv", 
        headers={"Content-disposition": "attachment; filename=history.csv"}
    )

@app.route('/api/devices/export', methods=['POST'])
def export_devices():
    """Exports current device list to CSV dynamically."""
    d = request.json
    rows = d.get('rows', [])
    out = io.StringIO()
    writer = csv.writer(out)
    
    # Check if we have data
    if rows:
        # 1. Write Header Row (using keys from the first item, e.g., "Hostname", "IP Address")
        headers = list(rows[0].keys())
        writer.writerow(headers)
        
        # 2. Write Data Rows (mapping values to the headers)
        for r in rows: 
            writer.writerow([r.get(h) for h in headers])
            
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=devices.csv"})

@app.route('/api/system/export_db')
def export_database():
    """Downloads the entire database file."""
    return send_file(DB_NAME, as_attachment=True)

@app.route('/api/system/import_db', methods=['POST'])
def import_database():
    """
    Imports data from another database file and merges it.
    Validates file integrity before performing a backup and merge.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    uploaded_file = request.files['file']
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        uploaded_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        # --- 1. VALIDATE THE UPLOADED FILE FIRST ---
        conn_remote = sqlite3.connect(tmp_path)
        cursor_r = conn_remote.cursor()
        
        try:
            # A. Check if it's a valid, uncorrupted SQLite database
            cursor_r.execute("PRAGMA integrity_check;")
            res = cursor_r.fetchone()
            if not res or res[0].lower() != "ok":
                conn_remote.close()
                os.remove(tmp_path)
                return jsonify({"error": "Uploaded file is corrupted or is not a valid SQLite database."}), 400
                
            # B. Check if it's OUR database by looking for a core table
            cursor_r.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='networks';")
            if not cursor_r.fetchone():
                conn_remote.close()
                os.remove(tmp_path)
                return jsonify({"error": "Invalid database format. Missing required application tables."}), 400
                
        except sqlite3.DatabaseError:
            # Catches cases where a completely random file (like an image) was uploaded
            conn_remote.close()
            os.remove(tmp_path)
            return jsonify({"error": "Uploaded file is not a valid database."}), 400

        # --- 2. VALIDATION PASSED: CREATE SAFETY BACKUP ---
        print("[*] Uploaded database verified. Creating pre-import backup...")
        execute_backup("pre_import_good", 5, force=True)
        
        # Re-initialize the remote cursor with Row factory for the merge process
        conn_remote.row_factory = sqlite3.Row
        cursor_r = conn_remote.cursor()
        
        conn_local = sqlite3.connect(DB_NAME, timeout=10.0)
        cursor_l = conn_local.cursor()

        # 3. Merge Networks (ID Mapping)
        network_map = {} 
        remote_networks = cursor_r.execute("SELECT * FROM networks").fetchall()
        for net in remote_networks:
            cursor_l.execute("SELECT id FROM networks WHERE gateway_mac IS ? AND gateway_ip IS ?", 
                             (net['gateway_mac'], net['gateway_ip']))
            exists = cursor_l.fetchone()
            if exists:
                network_map[net['id']] = exists[0]
            else:
                cursor_l.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip) VALUES (?, ?, ?, ?)",
                                 (net['gateway_mac'], net['name'], net['last_scan'], net['gateway_ip']))
                network_map[net['id']] = cursor_l.lastrowid

        # 4. Merge Devices (Updating metadata)
        remote_devices = cursor_r.execute("SELECT * FROM devices").fetchall()
        for dev in remote_devices:
            new_net_id = network_map.get(dev['network_id'])
            if new_net_id:
                cursor_l.execute("""
                    INSERT INTO devices (mac_address, network_id, hostname, custom_name, ip_address, last_seen, services, is_online)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mac_address, network_id) DO UPDATE SET
                    hostname = COALESCE(excluded.hostname, devices.hostname),
                    custom_name = CASE 
                        WHEN excluded.custom_name IS NOT NULL AND excluded.custom_name != '' THEN excluded.custom_name 
                        ELSE devices.custom_name 
                    END,
                    last_seen = MAX(last_seen, excluded.last_seen),
                    is_online = MAX(is_online, excluded.is_online)
                """, (dev['mac_address'], new_net_id, dev['hostname'], dev['custom_name'], 
                      dev['ip_address'], dev['last_seen'], dev['services'], dev['is_online']))

        # 5. Merge Logs (History, Ping, DNS)
        tables_to_append = {
            'history': ['timestamp', 'network_name', 'connection_type', 'download', 'upload', 'ping', 'wan_ip', 'device_ip', 'isp'],
            'dns_logs': ['timestamp', 'domain', 'result_ip', 'record_type', 'status', 'router_ip', 'network_name', 'lan_ip'],
            'ping_logs': ['timestamp', 'target', 'status', 'latency', 'packet_loss', 'network_context', 'router_ip', 'network_name', 'lan_ip'],
            'wifi_history': ['timestamp', 'scan_name', 'comments', 'results_json']
        }

        for table, cols in tables_to_append.items():
            remote_data = cursor_r.execute(f"SELECT * FROM {table}").fetchall()
            for row in remote_data:
                placeholders = " AND ".join([f"{c} IS ?" for c in cols])
                cursor_l.execute(f"SELECT 1 FROM {table} WHERE {placeholders}", [row[c] for c in cols])
                if not cursor_l.fetchone():
                    col_str = ", ".join(cols)
                    val_placeholders = ", ".join(["?" for _ in cols])
                    cursor_l.execute(f"INSERT INTO {table} ({col_str}) VALUES ({val_placeholders})", [row[c] for c in cols])

        # 6. Global Settings
        remote_global = cursor_r.execute("SELECT * FROM global_device_names").fetchall()
        for g in remote_global:
            cursor_l.execute("INSERT OR REPLACE INTO global_device_names (mac_address, custom_name) VALUES (?, ?)", 
                             (g['mac_address'], g['custom_name']))

        conn_local.commit()
        conn_local.close()
        conn_remote.close()
        os.remove(tmp_path)
        return jsonify({"status": "success", "message": "Database merged successfully! A backup was created prior to the merge."})

    except Exception as e:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        return jsonify({"error": str(e)}), 500

# --- Updater (GitHub Integration) ---

def get_update_channel():
    """Helper to fetch the current update channel from DB."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT value FROM system_settings WHERE key='update_channel'").fetchone()
            return row[0] if row else 'stable'
    except:
        return 'stable'

GITHUB_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "github_settings.json")

def get_all_github_settings():
    """Reads all available update channels from the external settings file."""
    try:
        if os.path.exists(GITHUB_SETTINGS_FILE):
            with open(GITHUB_SETTINGS_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"[*] Error reading github_settings.json: {e}")
        
    # Fallback: Safely import the defaults from setup_env.py if the file is missing/corrupt
    try:
        import setup_env
        if hasattr(setup_env, 'DEFAULT_GITHUB_CONFIG'):
            return setup_env.DEFAULT_GITHUB_CONFIG
    except: pass
        
    return {}

def get_github_settings():
    """Routes updates to the active channel's settings."""
    channel = get_update_channel()
    all_settings = get_all_github_settings()
    
    # If the user's saved channel exists in the JSON, use it
    if channel in all_settings:
        return all_settings[channel]
        
    # Fallback to the first available repo (usually 'stable') if their channel got deleted from the JSON
    if all_settings:
        return all_settings.get("stable", list(all_settings.values())[0])
    return None

@app.route('/api/settings/channels', methods=['GET'])
def get_channels():
    """Returns the dynamic list of available update channels for the UI Dropdown."""
    all_settings = get_all_github_settings()
    channels = []
    for key, val in all_settings.items():
        channels.append({
            "id": key,
            "name": val.get("display_name", key.capitalize())
        })
    return jsonify({"channels": channels})

@app.route('/api/settings/channel', methods=['POST'])
def handle_update_channel():
    """API endpoint to switch the update channel."""
    channel = (request.json or {}).get('channel', 'stable')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('update_channel', ?)", (channel,))
        conn.commit()
    return jsonify({"status": "success", "channel": channel})

def fetch_github_file(filename):
    """Fetches raw file content from private GitHub repo."""
    gh_set = get_github_settings()
    url = f"https://api.github.com/repos/{gh_set['owner']}/{gh_set['repo']}/contents/{filename}?ref={gh_set['branch']}"
    req = urllib.request.Request(url)
    if gh_set["token"]: 
        req.add_header("Authorization", f"token {gh_set['token']}")
    try:
        with urllib.request.urlopen(req) as response:
            return base64.b64decode(json.loads(response.read().decode())['content']).decode('utf-8')
    except: 
        return None

@app.route('/api/update/check')
def check_update():
    """
    Checks the version of SPECIFIC core files against GitHub.
    Returns a list of mismatches so the frontend knows exactly what is outdated.
    """
    # Map friendly names to their Repo Paths and Regex Patterns
    targets = {
        "app.py": {
            "path": "app.py",
            "local": APP_VERSION,
            "regex": r'APP_VERSION\s*=\s*["\']([^"\']+)["\']'
        },
        "setup_env.py": {
            "path": "setup_env.py",
            "local": get_setup_version(),
            "regex": r'SETUP_VERSION\s*=\s*["\']([^"\']+)["\']'
        },
        "dashboard.html": {
            "path": "templates/dashboard.html",
            "local": get_html_version(),
            "regex": r'Version number\s+([\d.]+)'
        }
    }
    
    mismatches = []
    
    def check_file(name, config):
        try:
            # Fetch raw content from GitHub
            content = fetch_github_file(config["path"])
            if not content: return None
            
            # Parse Remote Version
            match = re.search(config["regex"], content)
            if match:
                remote_ver = match.group(1)
                
                # Semantic Versioning Check (Remote > Local)
                def is_newer(r, l):
                    try:
                        return [int(x) for x in r.split('.')] > [int(x) for x in l.split('.')]
                    except: return r != l
                
                if is_newer(remote_ver, config["local"]):
                    return {
                        "file": name,
                        "local": config["local"],
                        "remote": remote_ver
                    }
        except: pass
        return None

    # Run checks in parallel to keep dashboard load time fast
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(check_file, name, cfg) for name, cfg in targets.items()]
        for f in futures:
            res = f.result()
            if res: mismatches.append(res)
            
    # Fetch the global tag for the footer
    global_remote = "0.0.0"
    try:
        c = fetch_github_file("version.json")
        if c: 
            global_remote = json.loads(c).get("version", "0.0.0")
    except: 
        pass

    return jsonify({
        "status": "success",
        "update_available": len(mismatches) > 0,
        "mismatches": mismatches,
        "remote_version": global_remote,      # Key used by dashboard.html footer
        "global_local": get_global_version()   # Current local version.json
    })

@app.route('/api/update/changelog')
def get_changelog():
    """Fetches Release Notes from the GitHub repository."""
    content = fetch_github_file("Changelog") 
    return jsonify({"status": "success", "changelog": content}) if content else jsonify({"status": "error"})

def backup_for_update():
    """Takes a safe DB snapshot right before updating (Max 5). Forces a backup."""
    execute_backup("update_good", 5, force=True)

@app.route('/api/update/apply', methods=['POST'])
def update_software():
    """
    Cross-Platform Update Mechanism with Rollback Support.
    Sets full Read/Write/Execute (777) permissions for all users.
    """
    try:
        gh_set = get_github_settings()
        if not gh_set: return jsonify({"error": "No GitHub settings."}), 500
        print(f"[*] Starting Update on {platform.system()} from {gh_set['repo']}...")
        
        base_dir = app.root_path
        
        # --- 1. Run the Database Backup System ---
        backup_for_update()
        
        # --- 2. Create a System Rollback Backup ---
        print("[*] Creating rollback backup before updating...")
        rollback_zip = os.path.join(base_dir, "rollback.zip")
        try:
            with zipfile.ZipFile(rollback_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(base_dir):
                    # Ignore heavy/unnecessary directories
                    if "venv" in dirs: dirs.remove("venv")
                    if "logs" in dirs: dirs.remove("logs")
                    if "backups" in dirs: dirs.remove("backups") # Skip the new backup folder
                    if "__pycache__" in dirs: dirs.remove("__pycache__")
                    if ".git" in dirs: dirs.remove(".git")
                    
                    for file in files:
                        if file.endswith(".db") or file.endswith(".db-wal") or file.endswith(".db-shm"): continue
                        if file.endswith(".bak") or file == "rollback.zip": continue
                        
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, os.path.relpath(file_path, base_dir))
        except Exception as e:
            print(f"[!] Rollback backup warning: {e}")
        
        # 3. Download from GitHub
        req = urllib.request.Request(f"https://api.github.com/repos/{gh_set['owner']}/{gh_set['repo']}/zipball/{gh_set['branch']}")
        if gh_set.get('token'): req.add_header("Authorization", f"token {gh_set['token']}")
        
        try:
            with urllib.request.urlopen(req) as response: zip_data = io.BytesIO(response.read())
        except Exception as e: return jsonify({"error": f"Download failed: {e}"}), 500

        # 4. Extract & Install
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(zip_data) as zip_ref:
                root_name = zip_ref.namelist()[0].split('/')[0]
                zip_ref.extractall(temp_dir)
                source_root = os.path.join(temp_dir, root_name)
                
                for root, dirs, files in os.walk(source_root):
                    rel_path = os.path.relpath(root, source_root)
                    dest_dir = os.path.join(base_dir, rel_path)
                    
                    if not os.path.exists(dest_dir):
                        os.makedirs(dest_dir)
                    fix_permissions(dest_dir)
                    
                    for file in files:
                        src_file = os.path.join(root, file)
                        dest_file = os.path.join(dest_dir, file)
                        
                        if file == DB_NAME or file.endswith(".db") or "venv" in dest_file: continue

                        try:
                            if os.path.exists(dest_file):
                                try: os.replace(src_file, dest_file)
                                except OSError:
                                    if platform.system() == "Windows":
                                        backup = dest_file + f".old_{int(time.time())}"
                                        if os.path.exists(backup): os.remove(backup)
                                        os.rename(dest_file, backup)
                                        shutil.move(src_file, dest_file)
                            else: shutil.move(src_file, dest_file)
                            
                            fix_permissions(dest_file)
                        except Exception as e: print(f"[!] Update copy failed for {file}: {e}")

        print("[✓] Update applied. Permissions set to Read/Write/Execute for all.")
        threading.Thread(target=restart_server).start()
        return jsonify({"status": "success", "message": "Update successful. All files set to R/W/X."})

    except Exception as e:
        print(f"[X] Update Error: {e}")
        return jsonify({"error": str(e)}), 500

def fix_permissions(path):
    """
    Sets path to full Read/Write/Execute for all users.
    Linux/Mac: chmod 777
    Windows: icacls grant Everyone:FullControl
    """
    try:
        if platform.system() == "Windows":
            # Grant 'Everyone' group Full Control (F)
            # /t and /c are avoided here as we are walking the tree manually in the loop above
            subprocess.run(['icacls', str(path), '/grant', 'Everyone:(F)'], capture_output=True)
        else:
            # Linux/Mac: 0o777 is rwxrwxrwx
            os.chmod(path, 0o777)
    except Exception as e:
        print(f"[!] Permission fix failed for {path}: {e}")

@app.route('/api/wifi/save', methods=['POST'])
def save_wifi_scan():
    data = request.json
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10.0)
        c = conn.cursor()
        
        raw_name = str(data.get('name', '')).strip()[:50]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scan_name = raw_name if raw_name else f"Scan {timestamp}"
        comments = str(data.get('comments', '')).strip()[:200]

        # ADDED 'timestamp' column and value here:
        c.execute(
            "INSERT INTO wifi_history (timestamp, scan_name, comments, results_json) VALUES (?, ?, ?, ?)",
            (timestamp, scan_name, comments, json.dumps(data.get('results', [])))
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "saved_as": scan_name})
    except Exception as e:
        print(f"[X] Database Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wifi/history', methods=['GET'])
def get_wifi_history():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        c = conn.cursor()
        # Only fetch scans that haven't been deleted
        c.execute("SELECT id, timestamp, scan_name, comments, is_protected FROM wifi_history WHERE is_deleted=0 ORDER BY timestamp DESC")
        rows = c.fetchall()
        history = [{"id": r[0], "timestamp": r[1], "name": r[2], "comments": r[3], "is_protected": r[4] or 0} for r in rows]
        return jsonify(history)

# --- NEW: Endpoint to lock/unlock records ---
@app.route('/api/system/toggle_protection', methods=['POST'])
def toggle_protection():
    d = request.json
    
    table_map = {
        'history': 'history', 
        'wifi': 'wifi_history', 
        'devices': 'devices', 
        'networks': 'networks',
        'dns': 'dns_logs',
        'ping': 'ping_logs',
        'wifi_ssid': 'protected_wifi_ssids' # NEW
    }
    
    table = table_map.get(d.get('type'))
    item_id = d.get('id')
    state = 1 if d.get('state') else 0
    
    if table and item_id is not None:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            if table == 'devices':
                conn.execute("UPDATE devices SET is_protected = ? WHERE mac_address = ?", (state, item_id))
            elif table == 'protected_wifi_ssids':
                if state:
                    conn.execute("INSERT OR IGNORE INTO protected_wifi_ssids (ssid) VALUES (?)", (item_id,))
                else:
                    conn.execute("DELETE FROM protected_wifi_ssids WHERE ssid = ?", (item_id,))
            else:
                conn.execute(f"UPDATE {table} SET is_protected = ? WHERE id = ?", (state, item_id))
            conn.commit()
        return jsonify({"status": "success"})
    return jsonify({"error": "Invalid request"}), 400

@app.route('/api/wifi/history/<int:scan_id>', methods=['GET'])
def load_wifi_scan(scan_id):
    conn = sqlite3.connect(DB_NAME, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT results_json, scan_name FROM wifi_history WHERE id = ?", (scan_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({"results": json.loads(row[0]), "name": row[1]})
    return jsonify({"error": "Not found"}), 404

@app.route('/api/wifi/delete', methods=['POST'])
def delete_wifi_scan():
    scan_id = request.json.get('id')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE wifi_history SET is_deleted=1 WHERE id = ?", (scan_id,))
        conn.commit()
    return jsonify({"status": "deleted"})

@app.route('/api/wifi/export/<int:scan_id>')
def export_wifi_csv(scan_id):
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT scan_name, results_json FROM wifi_history WHERE id = ?", (scan_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return "Scan not found", 404

        scan_name = get_safe_filename(row[0])
        results = json.loads(row[1])

        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["SSID", "MAC", "Signal (dBm)", "Signal (%)", "Channel", "Band", "Authentication"])
        for net in results:
            if "raw_bssids" in net and net["raw_bssids"]:
                for b in net["raw_bssids"]:
                    if "dbm" in b or "percent" in b:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('dbm', '-'), b.get('percent', '-'), b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-')])
                    else:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('signal', '-'), "-", b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-')])
            else:
                writer.writerow([net.get('ssid', 'Unknown'), net.get('mac', '-'), net.get('signal', '-').replace('<br>', ' | '), "-", net.get('channel', '-'), net.get('band', '-').replace('<br>', ' | '), net.get('auth', '-')])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=wifi_scan_{scan_name}.csv"}
        )
    except Exception as e:
        return str(e), 500



@app.route('/api/wifi/history/clear_all', methods=['POST'])
def clear_all_wifi_history():
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE wifi_history SET is_deleted=1")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/wifi/export_active', methods=['POST'])
def export_active_wifi_csv():
    """Exports the current active scan results to CSV."""
    try:
        data = request.json
        results = data.get('results', [])
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["SSID", "MAC", "Signal (dBm)", "Signal (%)", "Channel", "Band", "Authentication"])
        for net in results:
            if "raw_bssids" in net and net["raw_bssids"]:
                for b in net["raw_bssids"]:
                    if "dbm" in b or "percent" in b:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('dbm', '-'), b.get('percent', '-'), b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-')])
                    else:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('signal', '-'), "-", b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-')])
            else:
                writer.writerow([net.get('ssid', 'Unknown'), net.get('mac', '-'), net.get('signal', '-').replace('<br>', ' | '), "-", net.get('channel', '-'), net.get('band', '-').replace('<br>', ' | '), net.get('auth', '-')])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=active_wifi_scan.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/wifi/history/update', methods=['POST'])
def update_wifi_history():
    """Updates the scan name and comment of a specific Wi-Fi history entry."""
    data = request.json
    scan_id = data.get('id')
    new_name = str(data.get('name', '')).strip()[:50]
    new_comment = str(data.get('comment', '')).strip()[:200]
    
    # Fallback for name if left empty
    if not new_name:
        new_name = f"Scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.execute(
                "UPDATE wifi_history SET scan_name = ?, comments = ? WHERE id = ?",
                (new_name, new_comment, scan_id)
            )
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/settings/connection_types', methods=['GET'])
def get_connection_types():
    """Fetches all connection types for dropdowns and settings."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM connection_types ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/settings/connection_types/add', methods=['POST'])
def add_connection_type():
    """Adds a new custom connection type."""
    # --- FIXED: Apply strip and limit ---
    name = str(request.json.get('name', '')).strip()[:50]
    if not name: return jsonify({"error": "Name required"}), 400
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.execute("INSERT INTO connection_types (name) VALUES (?)", (name,))
            conn.commit()
        return jsonify({"status": "success"})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Type already exists"}), 400

@app.route('/api/settings/connection_types/delete', methods=['POST'])
def delete_connection_type():
    """Removes a connection type from the list."""
    type_id = request.json.get('id')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM connection_types WHERE id=?", (type_id,))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/device_history')
def api_device_history():
    """Returns the latest state of all unique devices across all networks."""
    try:
        #print("\n[-->] API /device_history called. Connecting to DB...")
        
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            #print("[*] DB connected. Executing optimized query...")
            
            # OPTIMIZED QUERY: Removed the nested SELECT MAX() subquery.
            # In SQLite, using MAX(last_seen) in a GROUP BY automatically returns the corresponding row's data.
            # This turns an O(N^2) query (which freezes the app) into a lightning-fast O(N) query.
            query = """
                SELECT d.mac_address, d.hostname, COALESCE(g.custom_name, d.custom_name) as custom_name,
                       d.ip_address, MAX(d.last_seen) as last_seen, COALESCE(n.name, d.last_network_name, 'Deleted Network') as network_name, d.vendor, d.is_protected
                FROM devices d
                LEFT JOIN networks n ON d.network_id = n.id
                LEFT JOIN global_device_names g ON d.mac_address = g.mac_address
                GROUP BY d.mac_address
                ORDER BY last_seen DESC
            """
            
            # Start timer to log database performance
            start_time = time.time()
            rows = conn.execute(query).fetchall()
            elapsed = time.time() - start_time
            
        #    print(f"[*] Query executed in {elapsed:.4f} seconds. Returned {len(rows)} unique devices.")
            
            result = []
            for r in rows:
                dev = dict(r)
                vendor = dev.get('vendor') or ""
                clean_host = str(dev['hostname']) if dev['hostname'] else "Unknown"
                
                # Clean up legacy hostnames if the vendor is attached in brackets
                if vendor and f"({vendor})" in clean_host:
                    clean_host = clean_host.replace(f"({vendor})", "").strip()
                else:
                    # Failsafe for legacy devices scanned before the vendor column existed
                    match = re.search(r'\(([^)]+)\)$', clean_host)
                    if match and match.group(1) not in ["This device", "Router"]:
                        if not vendor: vendor = match.group(1)
                        clean_host = clean_host.replace(f"({match.group(1)})", "").strip()
                        
                dev['vendor'] = vendor
                dev['clean_hostname'] = clean_host
                result.append(dev)
                
           # print(f"[<--] Processing complete. Sending JSON back to frontend.")
            return jsonify(result)
            
    except Exception as e:
        print(f"[!!!] CRITICAL ERROR in /api/device_history: {e}")
        return jsonify({"error": str(e)})
    
@app.route('/api/wifi_networks_history/delete_ssid', methods=['POST'])
def delete_wifi_ssid():
    """Removes a specific SSID from all historical JSON scans."""
    ssid = request.json.get('ssid')
    if not ssid: return jsonify({"error": "SSID required"}), 400
    
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute("SELECT id, results_json FROM wifi_history")
            all_scans = c.fetchall()
            
            for row in all_scans:
                try:
                    nets = json.loads(row['results_json'])
                    filtered_nets = [n for n in nets if n.get('ssid') != ssid]
                    
                    if len(filtered_nets) != len(nets):
                        c.execute("UPDATE wifi_history SET results_json=? WHERE id=?", (json.dumps(filtered_nets), row['id']))
                except: pass
                
            conn.commit()
            return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/device_history/<mac>')
def api_device_history_detail(mac):
    """Returns all historical scan records for a specific MAC address across all scans."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT ds.ip_address, ds.timestamp as last_seen, ds.services, COALESCE(n.name, ds.network_name, 'Deleted Network') as network_name
            FROM device_scans ds
            LEFT JOIN networks n ON ds.network_id = n.id
            WHERE ds.mac_address = ?
            ORDER BY ds.timestamp DESC
        """
        rows = conn.execute(query, (mac,)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/vendor/lookup', methods=['POST'])
def api_vendor_lookup():
    """Background task to fetch vendor info and save it to the DB permanently."""
    mac = request.json.get('mac')
    if not mac: 
        return jsonify({"vendor": "Unknown"})
        
    # Force the internet lookup
    vendor = get_mac_vendor(mac, fetch_online=True)
    
    # Save the result to the DB so we never look it up again
    if vendor:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.execute("UPDATE devices SET vendor=? WHERE mac_address=?", (vendor, mac))
            conn.commit()
            
    return jsonify({"vendor": vendor or "Unknown"})

@app.route('/api/wifi_networks_history')
def api_wifi_networks_history():
    """Aggregates all unique Wi-Fi SSIDs seen across all historical scans."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            
            # Fetch protected SSIDs
            try:
                protected_rows = conn.execute("SELECT ssid FROM protected_wifi_ssids").fetchall()
                protected_ssids = {r['ssid'] for r in protected_rows}
            except:
                protected_ssids = set()
            
            # Fetch all scans sorted oldest to newest to track first/last seen dates
            rows = conn.execute("SELECT timestamp, results_json FROM wifi_history ORDER BY timestamp ASC").fetchall()
            
            networks = {}
            for r in rows:
                scan_ts = r['timestamp']
                try:
                    results = json.loads(r['results_json'])
                    for net in results:
                        ssid = net.get('ssid', 'Unknown')
                        if not ssid: continue
                        
                        if ssid not in networks:
                            networks[ssid] = {
                                "ssid": ssid,
                                "first_seen": scan_ts,
                                "last_seen": scan_ts,
                                "auth": net.get('auth', 'Unknown'),
                                "macs": set(),
                                "scan_count": 0
                            }
                        
                        networks[ssid]["last_seen"] = scan_ts
                        networks[ssid]["scan_count"] += 1
                        
                        # Extract unique MACs
                        if "raw_bssids" in net:
                            for b in net["raw_bssids"]:
                                mac = b.get("mac")
                                if mac and mac != "Unknown MAC":
                                    networks[ssid]["macs"].add(mac)
                except:
                    continue
            
            # Format the output
            result = []
            for v in networks.values():
                v["mac_count"] = len(v["macs"])
                v.pop("macs") # Remove the set so it converts to JSON cleanly
                v["is_protected"] = 1 if v["ssid"] in protected_ssids else 0 # Add protection status
                result.append(v)
                
            # Sort by most recently seen
            result.sort(key=lambda x: x["last_seen"], reverse=True)
            return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/wifi_networks_history/details', methods=['POST'])
def api_wifi_network_details():
    ssid = request.json.get('ssid')
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, timestamp, scan_name, results_json, is_deleted FROM wifi_history ORDER BY timestamp DESC").fetchall()
            
            details = []
            for r in rows:
                scan_ts = r['timestamp']
                scan_name = r['scan_name']
                if r['is_deleted']:
                    scan_name += " (Deleted Scan)"
                    
                try:
                    results = json.loads(r['results_json'])
                    for net in results:
                        if net.get('ssid') == ssid:
                            if "raw_bssids" in net and net["raw_bssids"]:
                                for b in net["raw_bssids"]:
                                    details.append({"scan_name": scan_name, "timestamp": scan_ts, "mac": b.get("mac", "Unknown"), "dbm": b.get("dbm", ""), "percent": b.get("percent", ""), "channel": b.get("channel", ""), "band": b.get("band", ""), "auth": net.get("auth", "")})
                            else:
                                details.append({"scan_name": scan_name, "timestamp": scan_ts, "mac": net.get("mac", "Unknown"), "dbm": "", "percent": "", "channel": net.get("channel", ""), "band": net.get("band", ""), "auth": net.get("auth", "")})
                except: continue
            return jsonify(details)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/system/cleanup_orphaned_wifi', methods=['POST'])
def cleanup_orphaned_wifi():
    """Removes Wi-Fi networks that ONLY exist in deleted scans, respecting SSID locks."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Step 1: Get all SSIDs that exist in active (non-deleted) scans
            c.execute("SELECT results_json FROM wifi_history WHERE is_deleted=0")
            active_ssids = set()
            for row in c.fetchall():
                try:
                    for net in json.loads(row['results_json']):
                        if net.get('ssid'): active_ssids.add(net['ssid'])
                except: pass
                
            # NEW: Add all protected SSIDs to the "active" list so they are never deleted
            try:
                prot_rows = c.execute("SELECT ssid FROM protected_wifi_ssids").fetchall()
                for pr in prot_rows: active_ssids.add(pr['ssid'])
            except: pass
            
            # Step 2: Extract JSON from deleted scans and prune them
            c.execute("SELECT id, results_json FROM wifi_history WHERE is_deleted=1")
            deleted_scans = c.fetchall()
            
            removed_count = 0
            for row in deleted_scans:
                try:
                    nets = json.loads(row['results_json'])
                    filtered_nets = [n for n in nets if n.get('ssid') in active_ssids]
                    
                    if len(filtered_nets) == 0:
                        c.execute("DELETE FROM wifi_history WHERE id=?", (row['id'],))
                        removed_count += 1
                    elif len(filtered_nets) < len(nets):
                        c.execute("UPDATE wifi_history SET results_json=? WHERE id=?", (json.dumps(filtered_nets), row['id']))
                        removed_count += 1
                except:
                    c.execute("DELETE FROM wifi_history WHERE id=?", (row['id'],))
                    
            conn.commit()
            conn.execute("VACUUM")
            return jsonify({"status": "success", "message": f"Cleaned up orphaned networks across {removed_count} deleted scan(s)."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

AUTOSTART_FILE = "autostart"

@app.route('/api/settings/autostart', methods=['GET'])
def get_autostart():
    if not os.path.exists(AUTOSTART_FILE):
        with open(AUTOSTART_FILE, "w") as f:
            f.write("1")
        return jsonify({"autostart": True})
    try:
        with open(AUTOSTART_FILE, "r") as f:
            return jsonify({"autostart": f.read(10).strip() == "1"}) # Limit read
    except:
        return jsonify({"autostart": True})

@app.route('/api/settings/autostart', methods=['POST'])
def set_autostart():
    enable = request.json.get('enable', True)
    try:
        with open(AUTOSTART_FILE, "w") as f:
            f.write("1" if enable else "0")
        return jsonify({"status": "success", "autostart": enable})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/settings/workers', methods=['GET'])
def get_workers_endpoint():
    return jsonify({
        "config": get_worker_config(),
        "hardware": detect_hardware(),
        "defaults": get_default_workers_for_hardware()
    })

@app.route('/api/settings/workers', methods=['POST'])
def save_workers_endpoint():
    data = request.json or {}
    defaults = get_default_workers_for_hardware()
    new_config = {}
    
    for key in ["server_threads", "scan_workers", "ping_workers"]:
        val = data.get(key)
        if val is not None and str(val).isdigit() and 1 <= int(val) <= 100:
            new_config[key] = int(val)
        else:
            new_config[key] = defaults[key]

    file_path = os.path.join(app.root_path, WORKERS_FILE)
    backup_path = os.path.join(app.root_path, f"{WORKERS_FILE}.bak")
    try:
        # --- NEW: Create a backup of the previous working config before saving ---
        if os.path.exists(file_path):
            shutil.copy2(file_path, backup_path)
            
        with open(file_path, "w") as f:
            json.dump(new_config, f, indent=4)
            
        # Trigger server restart in the background
        threading.Thread(target=restart_server).start()
        
        return jsonify({"status": "success", "config": new_config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/devices/update_vendor', methods=['POST'])
def update_device_vendor():
    d = request.json or {}
    mac = d.get('mac')
    vendor = str(d.get('vendor', '')).strip()[:50]
    network_id = d.get('network_id')

    if not mac:
        return jsonify({"status": "error", "message": "MAC required"}), 400

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        if network_id:
            conn.execute("UPDATE devices SET custom_vendor=? WHERE mac_address=? AND network_id=?", (vendor, mac, network_id))
        else:
            conn.execute("UPDATE devices SET custom_vendor=? WHERE mac_address=?", (vendor, mac))
        
        # Save globally so it persists across different network scans
        conn.execute("INSERT OR REPLACE INTO global_device_vendors (mac_address, custom_vendor) VALUES (?, ?)", (mac, vendor))
        conn.commit()

    return jsonify({"status": "success", "vendor": vendor})

from flask import stream_with_context
from concurrent.futures import as_completed

def process_device_quick(received):
    """Processes device network info without making blocking online vendor calls."""
    ip = getattr(received, 'psrc', getattr(received, 'ip', None))
    mac = getattr(received, 'hwsrc', getattr(received, 'mac', None))
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip, mac=None),  # Skip vendor appending in hostname
        "services": check_open_ports(ip)['services']
    }


@app.route('/api/adapters/bulk_hide', methods=['POST'])
def bulk_hide_adapters():
    """Hides multiple adapters at once, ensuring safety constraints are met."""
    macs = request.json.get('macs', [])
    if not macs:
        return jsonify({"status": "error", "message": "No adapters provided."}), 400

    # Remove any invalid/empty MACs (like virtual adapters that can't be saved)
    macs = [m for m in macs if m and m != '-']
    if not macs:
        return jsonify({"status": "error", "message": "Cannot configure adapters without MAC addresses."}), 400

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        # 1. Prevent hiding a pinned adapter
        placeholders = ','.join(['?'] * len(macs))
        pinned = conn.execute(f"SELECT mac_address FROM adapter_settings WHERE is_primary=1 AND mac_address IN ({placeholders})", macs).fetchone()
        if pinned:
            return jsonify({"status": "error", "message": "One or more selected adapters are pinned. Unpin them before hiding."}), 400

# 2. Prevent hiding the last usable adapter
        interfaces = psutil.net_if_addrs()
        valid_keys = []
        for iface_name, addrs in interfaces.items():
            if "Loopback" in iface_name or "vEthernet" in iface_name or iface_name == "lo": 
                continue
            temp_mac = "-"
            for a in addrs:
                if a.family == psutil.AF_LINK: 
                    temp_mac = a.address
            
            # --- FIXED: Only count physical adapters with a real MAC address ---
            if temp_mac and temp_mac != "-":
                valid_keys.append(temp_mac)
            
        hidden_rows = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_visible = 0").fetchall()
        hidden_keys = set(r[0] for r in hidden_rows)
        
        # Add the new ones the user is attempting to hide
        hidden_keys.update(macs)
        
        visible_count = sum(1 for k in valid_keys if k not in hidden_keys)
        
        if visible_count == 0:
            return jsonify({"status": "error", "message": "You cannot hide all adapters. At least one must remain visible."}), 400

        # 3. Apply the bulk hide
        for mac in macs:
            conn.execute("""
                INSERT INTO adapter_settings (mac_address, is_visible)
                VALUES (?, 0)
                ON CONFLICT(mac_address) DO UPDATE SET is_visible=0
            """, (mac,))
        conn.commit()

    return jsonify({"status": "success"})

@app.route('/api/adapters/bulk_unhide', methods=['POST'])
def bulk_unhide_adapters():
    """Unhides multiple adapters at once."""
    macs = request.json.get('macs', [])
    if not macs:
        return jsonify({"status": "error", "message": "No adapters provided."}), 400

    # Remove any invalid/empty MACs (like virtual adapters that can't be saved)
    macs = [m for m in macs if m and m != '-']
    if not macs:
        return jsonify({"status": "error", "message": "Cannot configure adapters without MAC addresses."}), 400

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        for mac in macs:
            conn.execute("""
                INSERT INTO adapter_settings (mac_address, is_visible)
                VALUES (?, 1)
                ON CONFLICT(mac_address) DO UPDATE SET is_visible=1
            """, (mac,))
        conn.commit()

    return jsonify({"status": "success"})

@app.route('/api/networks/devices/delete', methods=['POST'])
def delete_network_devices():
    """Deletes selected or offline devices from a specific network."""
    d = request.json
    net_id = d.get('network_id')
    macs = d.get('macs', [])
    mode = d.get('mode', 'selected')
    
    if not net_id:
        return jsonify({"error": "Network ID required"}), 400
        
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        if mode == 'offline':
            # Delete all devices marked offline for this network
            conn.execute("DELETE FROM devices WHERE network_id=? AND is_online=0", (net_id,))
        elif macs:
            # Delete specifically selected MACs from this network
            placeholders = ','.join(['?'] * len(macs))
            # Safely pass net_id as the first parameter, followed by the MACs
            conn.execute(f"DELETE FROM devices WHERE network_id=? AND mac_address IN ({placeholders})", [net_id] + macs)
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/settings/logging', methods=['GET'])
def get_logging_settings():
    """Fetches the current logging preference and calculates total log size."""
    full_log = False
    disable_logs = False
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        try:
            row_f = conn.execute("SELECT value FROM system_settings WHERE key='full_logging'").fetchone()
            full_log = row_f[0] == '1' if row_f else False
            
            row_d = conn.execute("SELECT value FROM system_settings WHERE key='disable_all_logs'").fetchone()
            disable_logs = row_d[0] == '1' if row_d else False
        except sqlite3.OperationalError:
            pass
            
    # Calculate log folder size
    log_dir = os.path.join(app.root_path, 'logs')
    total_size = 0
    if os.path.exists(log_dir):
        for f in os.listdir(log_dir):
            fp = os.path.join(log_dir, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
    size_mb = total_size / (1024 * 1024)

    return jsonify({
        "full_logging": full_log, 
        "disable_all_logs": disable_logs, 
        "size_mb": f"{size_mb:.2f}"
    })

@app.route('/api/settings/logging', methods=['POST'])
def set_logging_settings():
    """Saves the logging preferences and immediately updates the live environment variables."""
    d = request.json
    enable_full = '1' if d.get('full_logging') else '0'
    disable_all = '1' if d.get('disable_all_logs') else '0'
    
    # Enforce mutual exclusivity on the backend
    if enable_full == '1' and disable_all == '1':
        return jsonify({"status": "error", "message": "Cannot enable both 'Full Logging' and 'Disable All Logs' simultaneously."}), 400

    os.environ["APP_FULL_LOGGING"] = enable_full
    os.environ["APP_DISABLE_ALL_LOGS"] = disable_all
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('full_logging', ?)", (enable_full,))
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('disable_all_logs', ?)", (disable_all,))
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/system/logs/download')
def download_logs():
    """Packages all available diagnostic logs into a zip file and downloads them."""
    log_dir = os.path.join(app.root_path, 'logs')
    if not os.path.exists(log_dir):
        return "No logs found.", 404
        
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(log_dir):
            for file in files:
                if file.endswith('.log'):
                    file_path = os.path.join(root, file)
                    zf.write(file_path, os.path.relpath(file_path, log_dir))
                    
    memory_file.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(memory_file, download_name=f"system_logs_{timestamp}.zip", as_attachment=True)

@app.route('/api/system/logs/delete', methods=['POST'])
def delete_system_logs():
    """Deletes all system diagnostic log files. Safely handles locked files on Windows."""
    log_dir = os.path.join(app.root_path, 'logs')
    if not os.path.exists(log_dir):
        return jsonify({"status": "success", "message": "No logs to delete."})
        
    try:
        for file_path in Path(log_dir).glob('*.log'):
            try:
                file_path.unlink()
            except PermissionError:
                with open(file_path, 'w') as f:
                    f.truncate(0)
            except Exception as e:
                pass
        
        # IMPORTANT: Reset the active file pointer to zero so we don't create a massive file filled with blank space!
        if hasattr(sys.stdout, 'file') and sys.stdout.file:
            try: sys.stdout.file.seek(0)
            except: pass
        if hasattr(sys.stderr, 'file') and sys.stderr.file:
            try: sys.stderr.file.seek(0)
            except: pass
            
        # The Audit Middleware will automatically trigger right after this return statement!
        return jsonify({"status": "success", "message": "System logs deleted successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/system/backups/info', methods=['GET'])
def get_backups_info():
    """Returns the total size and file count of the local backups folder."""
    backup_dir = os.path.join(app.root_path, 'backups')
    total_size = 0
    count = 0
    if os.path.exists(backup_dir):
        for f in os.listdir(backup_dir):
            fp = os.path.join(backup_dir, f)
            if os.path.isfile(fp) and f.endswith('.back'):
                total_size += os.path.getsize(fp)
                count += 1
    size_mb = total_size / (1024 * 1024)
    return jsonify({"size_mb": f"{size_mb:.2f}", "count": count})

@app.route('/api/system/backups/create', methods=['POST'])
def create_manual_backup():
    """Generates a manual snapshot of the database (Max 5). Skips if no new data."""
    try:
        res = execute_backup("manual_good", 5, force=False)
        if res:
            return jsonify({"status": "success", "message": "Manual backup created successfully."})
        else:
            return jsonify({"status": "success", "message": "Database is already backed up (no new data detected)."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/system/backups/delete', methods=['POST'])
def delete_backups():
    """Deletes backups based on the selected mode (all vs keep_latest)."""
    mode = (request.json or {}).get('mode', 'all')
    backup_dir = os.path.join(app.root_path, 'backups')
    
    if not os.path.exists(backup_dir):
        return jsonify({"status": "success", "message": "No backups to delete."})
    
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.back')]
        
        if mode == 'keep_latest':
            safe_files = [f for f in files if "_good_" in f]
            if safe_files:
                latest_safe = max(safe_files, key=os.path.getmtime)
                files.remove(latest_safe) 
        
        deleted_count = 0
        for f in files:
            try: 
                os.remove(f)
                deleted_count += 1
            except: pass
            
        return jsonify({"status": "success", "message": f"{deleted_count} backup(s) deleted successfully."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def manage_boot_counter():
    """
    Crash loop protection: Increments a counter on boot. 
    If it hits 5, restores the previous version (if updated), worker settings, and safe port.
    """
    base_dir = app.root_path
    counter_file = os.path.join(base_dir, "boot_attempts.txt")
    
    attempts = 0
    if os.path.exists(counter_file):
        try:
            with open(counter_file, "r") as f:
                attempts = int(f.read().strip())
        except: pass
        
    if attempts >= 5:
        print("\n[!] CRASH LOOP DETECTED! Restoring safe settings...")
        
        # --- NEW: 1. Rollback Failed Update ---
        rollback_zip = os.path.join(base_dir, "rollback.zip")
        if os.path.exists(rollback_zip):
            print("[*] Rollback archive found. Reverting to previous application version...")
            try:
                with zipfile.ZipFile(rollback_zip, 'r') as zip_ref:
                    zip_ref.extractall(base_dir)
                os.remove(rollback_zip)
                add_system_alert("Update caused a system crash. The application has been automatically rolled back to the previous version.")
            except Exception as e:
                print(f"[!] Failed to extract rollback archive: {e}")
                
        # --- 2. Restore Worker Settings ---
        file_path = os.path.join(base_dir, WORKERS_FILE)
        backup_path = os.path.join(base_dir, f"{WORKERS_FILE}.bak")
        
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, file_path)
                print("[*] Restored previous worker configuration.")
                add_system_alert("Crash loop detected: Worker settings restored to previous working configuration.")
            except: pass
        else:
            defaults = get_default_workers_for_hardware()
            try:
                with open(file_path, "w") as f:
                    json.dump(defaults, f, indent=4)
                print("[*] Restored hardware default worker configuration.")
                add_system_alert("Crash loop detected: Worker settings restored to hardware defaults.")
            except: pass
            
        # --- 3. Restore Last Known Good Web Port ---
        try:
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                row = conn.execute("SELECT value FROM system_settings WHERE key='last_good_port'").fetchone()
                safe_port = row[0] if row else '81'
                conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('web_port', ?)", (safe_port,))
                conn.commit()
            print(f"[*] Restored last known good web port ({safe_port}).")
            add_system_alert(f"Crash loop detected: Web port automatically restored to last known good port ({safe_port}).")
            
            port_override = os.path.join(base_dir, "webport")
            if os.path.exists(port_override):
                os.remove(port_override)
        except Exception as e:
            print(f"[!] Could not restore default port: {e}")

        # Clear the counter so it can boot normally
        if os.path.exists(counter_file):
            try: os.remove(counter_file)
            except: pass
    else:
        # Increment the counter
        try:
            with open(counter_file, "w") as f:
                f.write(str(attempts + 1))
        except: pass

def clear_boot_counter():
    """Clears the boot counter if the server survives startup, saves port, and triggers startup backup."""
    base_dir = app.root_path
    counter_file = os.path.join(base_dir, "boot_attempts.txt")
    
    if os.path.exists(counter_file):
        try:
            os.remove(counter_file)
            print("[*] Server stable. Boot counter cleared.")
        except: pass
        
    rollback_zip = os.path.join(base_dir, "rollback.zip")
    if os.path.exists(rollback_zip):
        try: os.remove(rollback_zip)
        except: pass
        
    try:
        current_port = get_current_port()
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_good_port', ?)", (str(current_port),))
            conn.commit()
    except: pass
    
    # 4. Trigger the safe startup backup
    perform_startup_backup()

def get_available_port(start_port):
    """
    Checks for an available port starting from start_port up to 90.
    Explicitly skips known browser-restricted ports.
    """
    max_port = max(start_port + 9, 90)
    for port in range(start_port, max_port + 1):
        if port in RESTRICTED_PORTS:
            print(f"[*] Port {port} skipped (browser-restricted unsafe port).")
            continue
            
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Tell the OS we are allowed to test ports that are in a TIME_WAIT state
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                print(f"[*] Port {port} is in use, checking next...")
                continue
    return None

def check_disk_space():
    """Checks if available disk space is below 250MB on startup."""
    try:
        root_path = os.path.splitdrive(os.getcwd())[0] or '/'
        usage = psutil.disk_usage(root_path if platform.system() == "Windows" else '/')
        free_mb = usage.free / (1024 * 1024)
        
        if free_mb < 250.0:
            add_system_alert(f"Critical low disk space warning: Only {free_mb:.1f} MB remaining. Free up space to ensure normal operation.")
            return False
    except Exception as e:
        print(f"[*] Could not check disk space: {e}")
    return True

if __name__ == '__main__':
    # --- 1. Catch boot loops before doing anything else ---
    manage_boot_counter()

    if platform.system() == "Darwin":
            try:
                import CoreLocation
                loc_manager = CoreLocation.CLLocationManager.alloc().init()
                loc_manager.requestAlwaysAuthorization()
                loc_manager.startUpdatingLocation()
                print("[*] CoreLocation authorization requested.")
            except Exception as e:
                print(f"[!] CoreLocation initialization failed: {e}")

    # Disable Wi-Fi Power Management on Linux/Raspberry Pi
    if platform.system() == "Linux":
        try:
            subprocess.run(["sudo", "iw", "dev", "wlan0", "set", "power_save", "off"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[*] Wi-Fi power management disabled for stable scanning.")
        except Exception as e:
            print(f"[*] Note: Could not disable Wi-Fi power management: {e}")

    cleanup_old_files()
    
    # --- Startup Sequence ---
    check_clear_database()
    init_db()
    check_password_reset()
    check_webport_file()
    check_dev_file()
    check_disk_space()
    schedule_routine_backups()
    
    current_port = get_current_port()

    # --- NEW: Port Conflict Fallback ---
    available_port = get_available_port(current_port)
    
    if not available_port:
        print("\n" + "!"*60)
        print(f"[!] CRITICAL ERROR: Port conflict detected.")
        print(f"[!] Could not find an open port between {current_port} and 90.")
        print(f"[!] Please define a port in the 'webport' file.")
        print("!"*60 + "\n")
        
        # Auto-create the webport file for the user with an alternative default
        port_file = os.path.join(app.root_path, "webport")
        try:
            with open(port_file, "w") as f:
                f.write("8080")
            print(f"[*] Auto-created 'webport' file in {app.root_path} with suggested port 8080.")
        except: pass
        
        sys.exit(1)
        
        if available_port != current_port:
            # Check if the port was changed because it was restricted or just in use
            if current_port in RESTRICTED_PORTS:
                conflict_msg = f"Port {current_port} is restricted by browsers. Automatically migrated to safe port {available_port}."
            else:
                conflict_msg = f"Port conflict on {current_port}. Automatically fell back to open port {available_port}."
                
            print(f"[*] {conflict_msg}")
            add_system_alert(conflict_msg)
            
            try:
                with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('web_port', ?)", (str(available_port),))
                    conn.commit()
            except: pass
            current_port = available_port
                
    # --- 2. Start a timer to clear the boot counter if the app stays alive for 5 seconds ---
    threading.Timer(5.0, clear_boot_counter).start()

    # Try to use the production-ready Waitress server
    try:
        from waitress import serve
        
        # --- NEW: Get the real LAN IP for the console output ---
        lan_ip = get_local_ip()
        
        print("\n" + "="*60)
        print(f"   DASHBOARD ACTIVE: http://{lan_ip}:{current_port}")
        print(f"   (Local Access: http://127.0.0.1:{current_port})")
        print("   (Production WSGI Server - No Warnings)")
        print("="*60 + "\n")
        
        worker_cfg = get_worker_config()
        # We still bind to 0.0.0.0 so other devices on the network can access it
        serve(app, host='0.0.0.0', port=current_port, threads=worker_cfg['server_threads'])
    except ImportError:
        lan_ip = get_local_ip()
        print("\n" + "="*60)
        print(f"   DASHBOARD ACTIVE: http://{lan_ip}:{current_port}")
        print("   (Development Server)")
        print("="*60 + "\n")
        app.run(debug=True, host='0.0.0.0', port=current_port)