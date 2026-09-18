"""
Network Diagnostics Dashboard - Main Application
Handles the Flask web server, background network scanning (Scapy), device discovery,
database management (SQLite), and OS-level network interactions across Windows, macOS, and Linux.
"""

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
import base64
import filecmp
from flask import stream_with_context
from concurrent.futures import as_completed
from werkzeug.security import generate_password_hash, check_password_hash

# Silence Scapy's default startup warnings about missing routes or IPv6
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
conf.verb = 0

# --- NEW: Fix for SSL Certificate Verify Errors ---
# Prevents urllib from crashing on systems with outdated root certificate stores
import ssl
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass
# ------------------------------------------------

# ==========================================
# PERMISSION ENGINE
# ==========================================
def fix_permissions(path):
    """
    Sets the specified path to full Read/Write/Execute permissions for all users (777).
    Crucial for allowing standard UI interaction (like deleting logs/backups) with files 
    created by the root/admin background service.
    """
    try:
        if platform.system() == "Windows":
            subprocess.run(['icacls', str(path), '/grant', 'Everyone:(F)'], capture_output=True)
        else:
            # 1. Try native Python chmod first (Lightning fast, perfect if app is already root)
            try:
                os.chmod(path, 0o777)
            except PermissionError:
                # 2. Fallback to sudo if a standard user process needs to override an old root file
                subprocess.run(['sudo', 'chmod', '777', str(path)], stderr=subprocess.DEVNULL)
        return True
    except:
        return False
    
# ---------------------------------------------------------
# --- LOGGING SETUP (Redirects ALL terminal output to file) ---
# ---------------------------------------------------------
def setup_file_logging():
    """
    Initializes a custom logging engine that mirrors terminal output (stdout/stderr) directly into daily log files.
    - Manages log rotation (automatically deletes logs older than 7 days).
    - Dynamically reads the SQLite database to apply the user's logging preferences (Full vs Errors Only vs Disabled).
    - Hooks into Flask and Waitress internal loggers to capture web server HTTP events.
    """
    import glob
    from datetime import timedelta
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, 'logs')
    
    # Ensure the logs directory exists and is accessible
    try:
        os.makedirs(log_dir, exist_ok=True)
        fix_permissions(log_dir) 
    except: pass

    # 1. Automated Log Rotation: Clean up logs older than 7 days to prevent disk bloat
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
                    # 'full_logging' logs every single action. If disabled, only errors/warnings are logged.
                    row_f = conn.execute("SELECT value FROM system_settings WHERE key='full_logging'").fetchone()
                    if row_f and row_f[0] == '1': full_log = True
                    
                    # 'disable_all_logs' completely halts disk I/O for logging (useful for saving Raspberry Pi SD cards).
                    row_d = conn.execute("SELECT value FROM system_settings WHERE key='disable_all_logs'").fetchone()
                    if row_d and row_d[0] == '1': disable_logs = True
        except: pass
        os.environ["APP_FULL_LOGGING"] = "1" if full_log else "0"
        os.environ["APP_DISABLE_ALL_LOGS"] = "1" if disable_logs else "0"

    # 3. Create a uniquely timestamped log file for this specific session execution
    timestamp = os.environ.get("APP_LOG_TIME", datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    log_path = os.path.join(log_dir, f"system_run_{timestamp}.log")

    # 4. TeeLogger: A custom class that intercepts 'print()' statements to send them to both the terminal and the log file
    class TeeLogger:
        def __init__(self, filename, terminal, is_stderr=False):
            self.terminal = terminal
            self.is_stderr = is_stderr # Tracks if this stream is for standard output or error output
            self.file = None
            try:
                self.file = open(filename, 'a', encoding='utf-8')
                fix_permissions(filename)
            except Exception: pass
            
        def write(self, text):
            # Ignore harmless macOS kernel threading warnings that spam the console
            if "Task policy set failed" in text:
                return
            
            # Always print to the live terminal so the user can see startup banners
            try:
                self.terminal.write(text)
                self.terminal.flush()
            except: pass
            
            if self.file:
                # If logging is disabled entirely, skip disk writing
                if os.environ.get("APP_DISABLE_ALL_LOGS", "0") == "1":
                    return

                # Check if this line is an error or if full logging is enabled
                is_full = os.environ.get("APP_FULL_LOGGING", "0") == "1"
                is_error = self.is_stderr or any(kw in text.lower() for kw in ['[x]', '[!]', 'error', 'failed', 'exception', 'critical', 'traceback', 'warning', 'audit'])

                # Write to the file only if it passes the filters
                if is_full or is_error:
                    try:
                        self.file.write(text)
                        self.file.flush() # Flush immediately so logs survive unexpected power losses/crashes
                    except: pass
                
        def flush(self):
            try: self.terminal.flush()
            except: pass
            if self.file:
                try: self.file.flush()
                except: pass

    # Override standard Python outputs to route through our custom TeeLogger
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    custom_logger_out = TeeLogger(log_path, original_stdout, is_stderr=False)
    custom_logger_err = TeeLogger(log_path, original_stderr, is_stderr=True)
    sys.stdout = custom_logger_out
    sys.stderr = custom_logger_err

    # 5. Catch internal Python library logs (like Flask/Waitress HTTP requests) and pipe them into the file
    logging.basicConfig(
        stream=custom_logger_out,
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
setup_file_logging()

# --- Configuration ---
APP_VERSION = "1.0.10"

# Chrome, Firefox, and Edge restrict web traffic on these specific ports for security reasons
RESTRICTED_PORTS = {87, 512, 513, 514, 515, 6000, 6665, 6666, 6667, 6668, 6669}

def get_global_version():
    """
    Reads the current global version from the local 'version.json' file.
    Fails safely by returning '0.0.0' or 'Error' if the file is missing or corrupted.
    """
    try:
        if os.path.exists("version.json"):
            with open("version.json", "r") as f:
                return json.load(f).get("version", "0.0.0")
        return "0.0.0"
    except:
        return "Error"

# Unified Global Database filename
DB_NAME = "network_data.db"

app = Flask(__name__)

# --- Database & Migrations ---
ALERTS_FILE = "system_alerts.json"

def add_system_alert(message):
    """
    Saves a system alert string to a JSON file to be displayed persistently on the web dashboard.
    Also prints the alert to the terminal logs.
    """
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
    """
    Executes SQLite's internal integrity check PRAGMA to detect malformed or corrupted database files.
    Returns True if the DB is healthy or doesn't exist yet, False if it is corrupted.
    """
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
    """
    Calculates the physical size of the SQLite database.
    If it exceeds 100MB, it triggers a UI alert suggesting the user perform database maintenance.
    """
    try:
        if os.path.exists(DB_NAME):
            size_mb = os.path.getsize(DB_NAME) / (1024 * 1024)
            if size_mb > 100.0:  # 100 MB Threshold
                add_system_alert(f"Database size is getting large ({size_mb:.1f} MB). Consider using the Database Maintenance tool in the System tab to clear old logs and improve performance.")
    except Exception as e:
        print(f"[*] Could not check DB size: {e}")

def get_safe_channel():
    """
    Reads the user's selected software update channel (e.g., 'stable' or 'dev') from the DB.
    Strips out any invalid characters to ensure the channel name can be safely used in backup filenames.
    Returns 'stable' as a default fallback.
    """
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
    """
    Compares semantic version strings (e.g., '1.0.4' vs '1.1.0').
    Returns True if the backup version is older than or equal to the current version,
    ensuring we do not accidentally restore a database schema built by a future app version.
    """
    def parse_ver(v):
        return [int(x) for x in re.sub(r'[^\d.]', '', str(v)).split('.') if x]
    
    b_parts = parse_ver(backup_ver)
    c_parts = parse_ver(current_ver)
    
    # Pad arrays with 0s to compare varying lengths (e.g., 1.0 vs 1.0.1)
    for i in range(max(len(b_parts), len(c_parts))):
        b = b_parts[i] if i < len(b_parts) else 0
        c = c_parts[i] if i < len(c_parts) else 0
        if b > c: return False
        if b < c: return True
    return True

def get_safe_filename(name):
    """
    Sanitizes user input (like custom network names) to create valid OS file paths.
    Strips Windows/Mac/Linux invalid path characters (\ / * ? : " < > |) and replaces spaces.
    """
    if not name: return "Unknown"
    safe = re.sub(r'[\\/*?:"<>|]', '', str(name)).replace(" ", "_")
    return safe if safe else "Export"

def manage_backup_rotation(category, max_count):
    """
    Scans the backups directory, groups backups by their prefix category (e.g., 'startup' or 'routine'),
    sorts them by modification date, and deletes the oldest files to enforce the max_count quota.
    """
    backup_dir = os.path.join(app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if category in f and f.endswith('.back')]
    
    # Sort files from newest to oldest
    files.sort(key=os.path.getmtime, reverse=True) 
    
    # Delete any files extending past the maximum allowed count
    for f in files[max_count:]:
        try: os.remove(f)
        except: pass

def get_newest_backup():
    """
    Scans the entire backups directory and returns the absolute file path of the most recently modified backup.
    Ignores temporary snapshot files.
    """
    backup_dir = os.path.join(app.root_path, 'backups')
    if not os.path.exists(backup_dir): return None
    
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.back') and "temp_snapshot" not in f]
    if not files: return None
    return max(files, key=os.path.getmtime)

def execute_backup(prefix, max_count, force=False):
    """
    Executes a live snapshot of the SQLite database.
    - Creates a temporary clone of the active database using SQLite's native backup API.
    - Compares the clone to the most recent backup. If identical and force=False, discards the clone to save space.
    - Renames the clone with a detailed timestamp and version signature, and enforces rotation quotas.
    """
    if not os.path.exists(DB_NAME) or not check_db_integrity(): return None
    
    backup_dir = os.path.join(app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    temp_backup = os.path.join(backup_dir, "temp_snapshot.back")
    
    # Safely clone the live database while avoiding file lock collisions
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

    # Check if the data has actually changed since the last backup
    if not force:
        newest_existing = get_newest_backup()
        if newest_existing and os.path.exists(newest_existing):
            try:
                # filecmp checks physical file bytes; if identical, we don't need a new backup
                if filecmp.cmp(temp_backup, newest_existing, shallow=False):
                    os.remove(temp_backup)
                    print(f"[*] No new data since last backup. Skipping {prefix} backup.")
                    return None
            except Exception as e:
                pass

    channel = get_safe_channel()
    ts = int(time.time())
    
    # OS-Agnostic Safe Naming Scheme
    final_name = os.path.join(backup_dir, f"network_data_{prefix}_{channel}_v{APP_VERSION}_{ts}.back")
    
    try:
        # os.replace is safer than os.rename on Windows (prevents FileExistsError)
        os.replace(temp_backup, final_name)
        fix_permissions(final_name) # Force permissions on the newly created backup file
        print(f"[*] Database backup created: {os.path.basename(final_name)}")
        manage_backup_rotation(prefix, max_count) # Apply quota pruning
        return final_name
    except Exception as e:
        if os.path.exists(temp_backup): 
            try: os.remove(temp_backup)
            except: pass
        return None

def perform_startup_backup():
    """
    Wrapper function triggered during application boot.
    Creates a routine snapshot labeled 'startup_good' and keeps the last 10 boots.
    """
    execute_backup("startup_good", 10, force=False)

def schedule_routine_backups():
    """
    Spawns a silent background thread that creates a backup every 7 days.
    Ensures backups continue even if the application is left running indefinitely on a dedicated host.
    """
    def backup_loop():
        while True:
            time.sleep(86400) # Sleep exactly 24 hours
            newest = get_newest_backup()
            should_backup = False
            
            if not newest:
                should_backup = True
            else:
                # Check if the newest backup is older than 7 days
                if time.time() - os.path.getmtime(newest) >= 7 * 86400:
                    should_backup = True
                    
            if should_backup:
                print("[*] 7 days have passed since the last backup. Running routine background backup...")
                execute_backup("routine_good", 10, force=False)

    t = threading.Thread(target=backup_loop, daemon=True)
    t.start()

def init_db():
    """
    The core database bootloader. Handles three primary tasks:
    1. Automatic Database Recovery: If corruption is detected, wipes the broken DB and restores the newest compatible backup.
    2. Initialization: Executes all CREATE TABLE IF NOT EXISTS statements.
    3. Live Migration: Scans tables for missing columns (added in updates) and dynamically alters schemas.
    """
    # --- 1. CORRUPTION & AUTO-RECOVERY SYSTEM ---
    if not check_db_integrity():
        print("\n[!] DATABASE CORRUPTION DETECTED! Initiating emergency recovery...")
        backup_dir = os.path.join(app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        # A. Save the corrupted DB to the error rotation for forensic inspection
        channel = get_safe_channel()
        ts = int(time.time())
        corrupt_name = os.path.join(backup_dir, f"network_data_error_{channel}_v{APP_VERSION}_{ts}.back")
        try:
            shutil.copy2(DB_NAME, corrupt_name)
            manage_backup_rotation("error_", 2)
        except: pass
        
        # B. Safely wipe the broken database files (including WAL and SHM temp files)
        for ext in ["", "-wal", "-shm"]:
            temp_file = f"{DB_NAME}{ext}"
            if os.path.exists(temp_file): 
                try: os.remove(temp_file)
                except: pass
                
        # C. Find valid restore candidates (Must be marked "good" and be version-compatible)
        candidates = []
        if os.path.exists(backup_dir):
            for f in os.listdir(backup_dir):
                if "_good_" in f and f.endswith(".back"):
                    file_path = os.path.join(backup_dir, f)
                    match = re.search(r'_v([\d\.]+)_', f)
                    if match and is_version_compatible(match.group(1), APP_VERSION):
                        candidates.append(file_path)
        
        # D. Restore the newest valid backup, or start fresh if none exist
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
        
        # Enable Write-Ahead Logging (WAL) for significantly faster concurrent read/writes
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
        
        # Seed default connection types if the table is empty
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
        # Detect legacy schema and migrate 'networks' table to support identical Gateways with different IPs (VLANs)
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

        # Safely attempt to add new columns from recent updates. Ignores OperationalError if they already exist.
        for col in ["isp TEXT", "connection_type TEXT", "device_ip TEXT"]:
            try: c.execute(f"ALTER TABLE history ADD COLUMN {col}")
            except sqlite3.OperationalError: pass
            
        for col in ["is_visible INTEGER DEFAULT 1", "is_primary INTEGER DEFAULT 0"]:
            try: c.execute(f"ALTER TABLE adapter_settings ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for table in ['dns_logs', 'ping_logs']:
            for col in ['router_ip TEXT', 'network_name TEXT', 'lan_ip TEXT', 'comments TEXT']:
                try: c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError: pass

        for table in ['history', 'wifi_history', 'dns_logs', 'ping_logs', 'devices', 'networks']:
            try: c.execute(f"ALTER TABLE {table} ADD COLUMN is_protected INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass

        for col in ["previous_ip TEXT", "discovery_status TEXT DEFAULT 'New Device'", "vendor TEXT", "custom_vendor TEXT", "last_network_name TEXT"]:
            try: c.execute(f"ALTER TABLE devices ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["network_name TEXT"]:
            try: c.execute(f"ALTER TABLE device_scans ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["is_deleted INTEGER DEFAULT 0"]:
            try: c.execute(f"ALTER TABLE wifi_history ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        for col in ["allow_matching INTEGER DEFAULT 1", "comments TEXT"]:
            try: c.execute(f"ALTER TABLE networks ADD COLUMN {col}")
            except sqlite3.OperationalError: pass
            
        c.execute('''CREATE TABLE IF NOT EXISTS global_wifi_comments (ssid TEXT PRIMARY KEY, comments TEXT)''')
        for col in ["comments TEXT"]:
            try: c.execute(f"ALTER TABLE global_device_names ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

        conn.commit()

def check_clear_database():
    """
    Emergency Startup Trigger: Checks for the existence of a 'cleardatabase' file.
    If found, it completely wipes the SQLite database and its temporary WAL/SHM files,
    allowing the user to hard-reset the application without needing SQLite tools.
    """
    clear_file = os.path.join(app.root_path, "cleardatabase")
    
    if os.path.exists(clear_file):
        print("[*] 'cleardatabase' file detected. Wiping the database completely...")
        try:
            # Delete the main DB file and its Write-Ahead Log (WAL) / Shared-Memory (SHM) files
            for ext in ["", "-wal", "-shm"]:
                db_file = os.path.join(app.root_path, f"{DB_NAME}{ext}")
                if os.path.exists(db_file):
                    os.remove(db_file)
            
            # Delete the trigger file so it doesn't wipe again on the next boot
            os.remove(clear_file)
            print("[✓] Database completely wiped. 'cleardatabase' file removed.")
        except Exception as e:
            print(f"[X] Failed to clear database: {e}")

def check_password_reset():
    """
    Emergency Startup Trigger: Checks for a 'passwordreset' file.
    If a user gets locked out of the web UI, creating this file and restarting the app 
    will securely strip their credentials and disable the authentication requirement.
    """
    reset_file = os.path.join(app.root_path, "passwordreset")
    
    if os.path.exists(reset_file):
        print("[*] 'passwordreset' file detected. Disabling authentication and removing credentials...")
        try:
            # Execute safely; if the table doesn't exist yet, it just skips gracefully
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
# Execute the emergency file checks before building the database schema
check_clear_database()
init_db()
check_password_reset()

# --- Versioning Helpers ---
def get_setup_version():
    """
    Reads the SETUP_VERSION string directly from setup_env.py using a regex.
    This prevents us from needing to import the setup file into the web server.
    """
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
    """
    Identifies the underlying hardware architecture.
    On Linux, it specifically checks the device-tree to differentiate between 
    a standard Linux Server/PC and various Raspberry Pi models (which have constrained CPU/RAM).
    """
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
    """
    Provides sensible concurrency limits for the ThreadPoolExecutors based on the detected hardware.
    Prevents the application from causing out-of-memory (OOM) crashes or CPU lockups on older Raspberry Pis
    when ping-sweeping large subnets.
    """
    hw = detect_hardware().lower()
    if "pi 3" in hw or "pi 2" in hw or "pi zero" in hw:
        # Constrained: 1GB RAM, 4 slower Cortex-A53 cores
        return {"server_threads": 6, "scan_workers": 10, "ping_workers": 15}
    elif "pi 4" in hw or "pi 400" in hw:
        # Moderate: 2GB-8GB RAM, Cortex-A72 cores
        return {"server_threads": 12, "scan_workers": 20, "ping_workers": 30}
    else:
        # High performance: Pi 5, Desktop PCs, Servers, Macs
        return {"server_threads": 24, "scan_workers": 30, "ping_workers": 50}

def get_worker_config():
    """
    Reads the custom 'workers' JSON file to apply user-defined thread limits.
    If the file is missing or corrupted, it automatically generates a new one based on hardware defaults.
    """
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
            # Ensure the provided values are safe integers
            config[k] = int(val) if str(val).isdigit() and int(val) > 0 else defaults[k]
        return config
    except Exception as e:
        print(f"[!] Error parsing 'workers' file, using defaults: {e}")
        return defaults

# --- System & Network Helpers ---

# GLOBAL CACHE: Terminal calls for hardware stats are slow and expensive.
# We cache them for 8 seconds so rapid UI refreshes don't crash the server.
OS_CACHE = {
    "ext_info": {"data": {}, "time": 0},
    "wifi_rates": {"data": {}, "time": 0},
    "wifi_ifaces": {"data": [], "time": 0}
}
CACHE_TTL = 8.0  

def get_isp_info():
    """
    Fetches the Public WAN IP and ISP name using the ip-api service.
    Short timeout applied so the dashboard doesn't hang if the internet is down.
    """
    try:
        with urllib.request.urlopen('http://ip-api.com/json/?fields=query,isp', timeout=3) as url:
            data = json.loads(url.read().decode())
            return {"ip": data.get("query", "Unknown"), "isp": data.get("isp", "Unknown ISP")}
    except:
        return {"ip": "Unknown", "isp": "Unknown ISP"}

def get_local_ip():
    """
    Identifies the primary local LAN IP address used for internet routing.
    Creates a dummy UDP socket connecting to Google DNS. Because UDP is connectionless, 
    no packets are actually sent, but the OS calculates which local interface IP would be used.
    """
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
    Windows rigidly locks files (like setup_env.py) while they are executing.
    When the auto-updater runs on Windows, it renames the locked files to .old to bypass the lock.
    This function deletes those orphaned .old files upon a fresh reboot to keep the folder clean.
    """
    base_dir = app.root_path
    print("[*] Performing startup cleanup...")
    
    # Walk through all directories in the project
    for root, dirs, files in os.walk(base_dir):
        # Skip the virtual environment to save time and avoid massive iteration loops
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
    Requires tiered fallbacks because Linux network management varies heavily across distros.
    """
    try:
        # Method 1: NMCLI (Best for Ubuntu Desktop/Server with NetworkManager)
        # -g returns just the value, cleaner than parsing grep output
        cmd = ["nmcli", "-g", "IP4.DNS", "dev", "show", interface_name]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        if output:
            return output.replace('\n', ', ').replace(' | ', ', ')

        # Method 2: resolvectl (Standard on modern systemd Linux like Ubuntu 22.04+)
        cmd = f"resolvectl status {interface_name}"
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if "DNS Servers:" in line:
                return line.split(":", 1)[1].strip().replace(' ', ', ')

    except Exception:
        pass

    # Method 3: Global Fallback (/etc/resolv.conf)
    # This usually just returns 127.0.0.53 on Ubuntu, but it's a necessary safety net
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
    """
    Gracefully handles application restarts triggered by the UI.
    Provides a 2-second sleep so the HTTP success response reaches the browser before the app dies.
    """
    print("[*] Triggering application restart in 2 seconds...")
    time.sleep(2) 
    
    # Windows and macOS do not use strict sudo PID locks in the supervisor loop.
    # Dropping back to the supervisor loop (os._exit) is the cleanest way to restart 
    # and guarantees the macOS system tray UI context gets redrawn properly!
    if platform.system() in ["Windows", "Darwin"]:
        os._exit(0)
    else:
        # On Linux, replacing the process image natively with os.execv 
        # is necessary to preserve the active sudo token/PID forever without prompting again!
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"[!] In-place restart failed: {e}. Falling back to supervisor loop.")
            os._exit(0)

def get_extended_iface_info():
    """
    Deep hardware query to fetch Gateway IPs, DNS Servers, and MAC addresses for all active adapters.
    Utilizes heavily varied, OS-specific terminal commands because standard Python libraries (like socket)
    cannot retrieve adapter-specific DNS or Gateways easily.
    Utilizes OS_CACHE to prevent spamming system processes on quick page refreshes.
    """
    global OS_CACHE
    if time.time() - OS_CACHE["ext_info"]["time"] < CACHE_TTL:
        return OS_CACHE["ext_info"]["data"]

    info = {}
    system = platform.system()
    
    def _clean_gw(g_str):
        """Helper to deduplicate and clean up gateway strings (e.g., stripping out IPv6 junk)."""
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
            # WINDOWS: Uses 'ipconfig /all' (Fastest native method available)
            try:
                raw_ip = subprocess.check_output("ipconfig /all", shell=True, text=True, encoding='latin-1', errors='ignore')
                current_iface = None
                for line in raw_ip.split('\n'):
                    line = line.strip()
                    if "adapter" in line and ":" in line:
                        parts = line.split("adapter")
                        if len(parts) > 1:
                            current_iface = parts[-1].split(":")[0].strip()
                            if current_iface not in info:
                                info[current_iface] = {"gateway": "-", "dns": "-"}
                    
                    # Associate the Gateway/DNS to the last identified adapter
                    if current_iface:
                        if "Default Gateway" in line and ":" in line:
                            gw = line.split(":")[-1].strip()
                            if gw and "." in gw and ":" not in gw: # Ignore IPv6
                                info[current_iface]["gateway"] = _clean_gw(gw)
                        if "DNS Servers" in line and ":" in line:
                            dns = line.split(":")[-1].strip()
                            if dns and "." in dns and ":" not in dns:
                                info[current_iface]["dns"] = dns
            except Exception as e: pass

        elif system == "Darwin": 
            # MACOS: Uses 'netstat' for the default route, and 'networksetup' / 'ipconfig' for specific hardware ports
            try:
                gw_out = subprocess.check_output("netstat -rn -f inet | grep 'default'", shell=True, text=True, stderr=subprocess.DEVNULL)
                default_gw = "-"
                primary_iface = None
                for line in gw_out.split('\n'):
                    parts = line.split()
                    if "default" in parts[0] and len(parts) >= 4:
                        default_gw = _clean_gw(parts[1])
                        primary_iface = parts[-1]
                        break
                
                port_out = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
                sections = port_out.split("Hardware Port: ")
                for section in sections:
                    if not section.strip(): continue 
                    lines = section.split('\n')
                    port_name = lines[0].strip() 
                    dev_name, mac_addr = None, "-"
                    
                    for line in lines:
                        if "Device:" in line: dev_name = line.split(":")[1].strip()
                        if "Ethernet Address:" in line: mac_addr = line.split(":")[1].strip()
                    
                    if dev_name:
                        gw_val = default_gw if dev_name == primary_iface else "-"
                        dns_val = "-"
                        try:
                            # Use internal ipconfig dict extraction for accurate active DNS
                            ipconfig = subprocess.check_output(["ipconfig", "getpacket", dev_name], text=True, stderr=subprocess.DEVNULL)
                            match_dns = re.search(r'domain_name_server\s*\(.*?\)\s*:\s*\{(.*?)\}', ipconfig, re.DOTALL)
                            if match_dns: dns_val = match_dns.group(1).replace('\n', '').strip().replace(',', ', ')
                            
                            match_gw = re.search(r'router\s*\(.*?\)\s*:\s*\{(.*?)\}', ipconfig, re.DOTALL)
                            if match_gw:
                                gw_found = match_gw.group(1).replace('\n', '').strip()
                                if gw_found and gw_found != "0.0.0.0": gw_val = _clean_gw(gw_found)
                        except: pass

                        if dns_val == "-" or not dns_val:
                            try:
                                ns_out = subprocess.check_output(["networksetup", "-getdnsservers", port_name], text=True)
                                if "There aren't any" not in ns_out:
                                    dns_list = [d.strip() for d in ns_out.split('\n') if d.strip() and ":" not in d]
                                    if dns_list: dns_val = ", ".join(dns_list)
                            except: pass
                        info[dev_name] = {"gateway": gw_val, "dns": dns_val, "mac": mac_addr}
            except Exception as e: pass

        elif system == "Linux": 
            # LINUX: Uses 'ip route' and the standard '/etc/resolv.conf'
            try:
                gw_out = subprocess.check_output("ip route show default | awk '/default/ {print $3}'", shell=True, text=True)
                default_gw = _clean_gw(gw_out.strip()) if ":" not in gw_out else "-"
            except: default_gw = "-"
            try:
                with open("/etc/resolv.conf", "r") as f:
                    dns_list = [l.split()[1] for l in f if l.startswith("nameserver") and ":" not in l]
                dns_val = ", ".join(dns_list) if dns_list else "-"
            except: dns_val = "-"
            for iface in psutil.net_if_addrs().keys():
                info[iface] = {"gateway": default_gw, "dns": dns_val}

    except Exception as e:
        print(f"Error in get_extended_iface_info: {e}")
        
    OS_CACHE["ext_info"]["data"] = info
    OS_CACHE["ext_info"]["time"] = time.time()
    return info

def get_wifi_rates():
    """
    Safety-first Wi-Fi capability fetching. Identifies if an adapter is a Wi-Fi card 
    and checks its current theoretical link speed to the router.
    - Windows: Parses `netsh wlan show interfaces`.
    - macOS: Uses the hidden `airport` utility or `ipconfig getsummary`.
    - Linux: Uses `nmcli`, `iw`, or reads from sysfs (`/sys/class/net/`).
    """
    global OS_CACHE
    if time.time() - OS_CACHE["wifi_rates"]["time"] < CACHE_TTL:
        return OS_CACHE["wifi_rates"]["data"]

    rates = {}
    system = platform.system()
    try:
        if system == "Windows":
            try:
                out = subprocess.check_output("netsh wlan show interfaces", shell=True, text=True, encoding='cp437', errors='ignore')
                current_iface = None
                rx_rate, tx_rate = 0, 0
                for line in out.split('\n'):
                    line = line.strip()
                    if line.startswith("Name"):
                        current_iface = line.split(":", 1)[1].strip()
                        rx_rate, tx_rate = 0, 0
                    elif current_iface:
                        if line.startswith("Receive rate"):
                            try: rx_rate = float(re.search(r'([0-9.]+)', line).group(1))
                            except: pass
                        elif line.startswith("Transmit rate"):
                            try: tx_rate = float(re.search(r'([0-9.]+)', line).group(1))
                            except: pass
                            if rx_rate > 0 or tx_rate > 0:
                                rates[current_iface] = f"Tx: {tx_rate} / Rx: {rx_rate} Mbps"
            except: pass
                    
        elif system == "Darwin": # macOS
            try:
                airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
                if os.path.exists(airport_path):
                    out = subprocess.check_output([airport_path, "-I"], text=True)
                    rate_match = re.search(r'lastTxRate:\s+(\d+)', out)
                    if rate_match: rates["en0"] = f"{rate_match.group(1)} Mbps"
                
                # Fallback if airport utility is removed/broken
                if "en0" not in rates:
                    out = subprocess.check_output(["ipconfig", "getsummary", "en0"], text=True)
                    tx_match = re.search(r'transmitRate\s+:\s+(\d+)', out)
                    if tx_match: rates["en0"] = f"{tx_match.group(1)} Mbps"
            except: pass    

        elif system == "Linux": 
            # Prioritize nmcli for stability as it handles parsing driver outputs nicely
            try:
                out = subprocess.check_output(["nmcli", "-t", "-f", "IN-USE,DEVICE,RATE", "dev", "wifi"], text=True, stderr=subprocess.DEVNULL)
                for line in out.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 3 and parts[0].replace('\\', '') == '*': # Asterisk denotes the active connection
                        dev = parts[1]
                        rate_raw = parts[2]
                        match = re.search(r'([0-9.]+)', rate_raw)
                        if dev and match and "unknown" not in rate_raw.lower():
                            rates[dev] = f"{match.group(1)} Mbps"
            except: pass
            
            # Tiered Fallbacks for headless/embedded linux devices (e.g. Raspberry Pi without NetworkManager)
            try:
                for iface in os.listdir('/sys/class/net/'):
                    if iface.startswith(('wlan', 'wlp', 'wlo')):
                        if iface in rates: continue
                        try:
                            # 1st Fallback: direct sysfs reading (Fastest)
                            speed_path = f'/sys/class/net/{iface}/speed'
                            if os.path.exists(speed_path):
                                with open(speed_path, 'r') as f:
                                    speed_val = int(f.read().strip())
                                    if speed_val > 0:
                                        rates[iface] = f"{speed_val} Mbps"
                                        continue
                        except: pass
                        try:
                            # 2nd Fallback: iw link
                            cmd = f"/sbin/iw dev {iface} link 2>/dev/null || /usr/sbin/iw dev {iface} link 2>/dev/null || iw dev {iface} link 2>/dev/null"
                            out = subprocess.check_output(cmd, shell=True, text=True)
                            if "Not connected" not in out:
                                match = re.search(r'tx bitrate:\s+([0-9.]+)', out)
                                if match:
                                    rates[iface] = f"{match.group(1)} Mbps"
                                    continue
                        except: pass
                        try:
                            # 3rd Fallback: legacy iwconfig parsing
                            cmd = f"/sbin/iwconfig {iface} 2>/dev/null || /usr/sbin/iwconfig {iface} 2>/dev/null || iwconfig {iface} 2>/dev/null"
                            out = subprocess.check_output(cmd, shell=True, text=True)
                            match = re.search(r'Bit Rate[=:]\s*([0-9.]+)', out)
                            if match: rates[iface] = f"{match.group(1)} Mbps"
                        except: pass
            except: pass
    except Exception as e: pass
    
    OS_CACHE["wifi_rates"]["data"] = rates
    OS_CACHE["wifi_rates"]["time"] = time.time()
    return rates

# --- Global Bandwidth Tracking Variables ---
# Stored globally so we can calculate the delta between API calls
last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

def get_html_version():
    """
    Parses the frontend 'dashboard.html' file to extract its embedded version number.
    Reads only the very first line of the file to prevent loading the entire HTML 
    document into memory, keeping the boot process and update checker extremely fast.
    """
    try:
        # Locate the template folder relative to this script's execution path
        template_path = os.path.join(app.root_path, "templates", "dashboard.html")
        
        if os.path.exists(template_path):
            with open(template_path, "r", encoding='utf-8') as f:
                # Read only the first line of the document for maximum performance
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
    Calculates live network throughput (MB/s).
    Crucially, it prioritizes tracking the traffic of the user's "Pinned Adapter" if one is set in the UI.
    Includes bounds-checking to prevent displaying negative speeds if the OS network counters reset 
    (which happens frequently when connecting/disconnecting from VPNs).
    """
    global last_received, last_sent, last_time
    
    target_iface = None
    pinned_mac = None

    # 1. Identify if an adapter is pinned in the database
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
    except: 
        pass

    # 2. Map Pinned MAC Address back to the OS system interface name
    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == psutil.AF_LINK and a.address == pinned_mac for a in addrs):
                target_iface = name
                break

    # 3. Retrieve IO Counters
    if target_iface:
        # Get stats ONLY for the specifically pinned adapter
        try:
            io = psutil.net_io_counters(pernic=True)[target_iface]
        except KeyError:
            # Fallback to global sum if the pinned adapter was unplugged/disabled
            io = psutil.net_io_counters()
    else:
        # Use global sum if no pin is set
        io = psutil.net_io_counters()

    curr_recv = io.bytes_recv
    curr_sent = io.bytes_sent
    curr_time = time.time()
    
    delta = curr_time - last_time
    if delta <= 0: delta = 1
    
    down = (curr_recv - last_received) / delta
    up = (curr_sent - last_sent) / delta
    
    # GUARD: If network counters reset, delta is negative. Clamp to 0.0 MB/s.
    if down < 0: down = 0.0
    if up < 0: up = 0.0
    
    # Update global tracking variables for the next UI poll
    last_received, last_sent, last_time = curr_recv, curr_sent, curr_time
    
    return {
        "download": f"{down / 1024 / 1024:.2f} MB/s", 
        "upload": f"{up / 1024 / 1024:.2f} MB/s"
    }

def get_active_interface_name():
    """
    Connects a dummy socket to find the active internet routing IP, then matches it to an interface name.
    Important: It checks the database to see if the interface is marked "hidden" by the user.
    If it is hidden, it returns an empty string to prevent the app from auto-selecting it.
    """
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
                # Check if this interface is hidden in the DB
                mac = None
                for a in addrs:
                    if a.family == psutil.AF_LINK:
                        mac = a.address
                
                if mac:
                    try:
                        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                            row = conn.execute("SELECT is_visible FROM adapter_settings WHERE mac_address=?", (mac,)).fetchone()
                            if row and row[0] == 0:
                                return "" # It is explicitly hidden, treat it as unusable
                    except:
                        pass
                
                return iface_name
    return ""

MAC_VENDOR_CACHE = {}

def get_mac_vendor(mac, fetch_online=False):
    """
    Identifies the hardware manufacturer of a device based on its MAC address prefix (OUI block).
    Tiered caching system to prevent API rate limits:
    1. Checks in-memory dictionary.
    2. Checks the SQLite device history to see if we've looked it up before.
    3. Only reaches out to the maclookup.app API if fetch_online is True.
    """
    if not mac or mac == "-" or mac.startswith("NO_MAC"): return ""
    
    mac_prefix = mac[:8].upper() 
    if mac_prefix in MAC_VENDOR_CACHE:
        return MAC_VENDOR_CACHE[mac_prefix]

    # Check database before making an HTTP request to save time/bandwidth
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT vendor FROM devices WHERE mac_address=? AND vendor IS NOT NULL AND vendor != '' LIMIT 1", (mac,)).fetchone()
            if row:
                MAC_VENDOR_CACHE[mac_prefix] = row[0]
                return row[0]
    except Exception:
        pass

    # Standard offline network sweeps skip the slow internet lookup
    if not fetch_online:
        return ""

    try:
        req = urllib.request.Request(
            f"https://api.maclookup.app/v2/macs/{mac_prefix}",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=2) as url:
            data = json.loads(url.read().decode())
            
            # Clean up long legal names (e.g. "Apple Inc." -> "Apple")
            if data.get('success') and data.get('company'):
                company = data['company'].replace(' Inc.', '').replace(' Ltd.', '').split(',')[0]
                MAC_VENDOR_CACHE[mac_prefix] = company
                return company
    except Exception: 
        pass
    
    return ""

# ==========================================
# ADVANCED HOSTNAME RESOLUTION ENGINE
# ==========================================

def _build_dns_ptr_query(ip):
    """
    Constructs a raw DNS PTR (Pointer) query packet payload for an IPv4 address.
    Building raw bytes instead of using heavy libraries like 'dnspython' keeps the 
    application footprint small and allows us to send queries to specific target IPs natively.
    """
    octets = ip.split('.')
    # Reverse the IP address to match the 'in-addr.arpa' standard for reverse DNS
    reversed_ip = '.'.join(reversed(octets)) + '.in-addr.arpa'
    
    txid = b'\x13\x37' # Arbitrary Transaction ID
    flags = b'\x01\x00'  # Standard query with recursion desired
    counts = b'\x00\x01\x00\x00\x00\x00\x00\x00' # 1 Question, 0 Answers, 0 Authority, 0 Additional
    
    # Format the query name (e.g. 1.0.168.192.in-addr.arpa) into DNS label format
    qname = b''.join(bytes([len(part)]) + part.encode('ascii') for part in reversed_ip.split('.')) + b'\x00'
    qtype_qclass = b'\x00\x0c\x00\x01'  # QTYPE: PTR (12), QCLASS: IN (1)
    
    return txid + flags + counts + qname + qtype_qclass

def _parse_dns_ptr_response(data):
    """
    Decodes a raw DNS/mDNS PTR response packet to extract the domain/hostname string.
    Navigates through the complex DNS header, skips the Question section, and 
    resolves pointers (0xC0) in the Answer section to extract the actual text.
    """
    try:
        if len(data) < 12: return None
        ancount = int.from_bytes(data[6:8], 'big') # Number of answers
        if ancount == 0: return None
        
        idx = 12
        # Skip Question section by jumping over the label lengths until we hit the null byte
        while idx < len(data) and data[idx] != 0:
            if (data[idx] & 0xC0) == 0xC0: # Hit a pointer, stop skipping
                idx += 2
                break
            idx += 1 + data[idx]
        if idx < len(data) and data[idx] == 0:
            idx += 1
        idx += 4  # Skip QTYPE and QCLASS

        # Parse Answer section
        for _ in range(ancount):
            if idx >= len(data): break
            
            # Skip the Name field of the answer
            if (data[idx] & 0xC0) == 0xC0:
                idx += 2
            else:
                while idx < len(data) and data[idx] != 0:
                    idx += 1 + data[idx]
                if idx < len(data) and data[idx] == 0:
                    idx += 1
            
            if idx + 10 > len(data): break
            
            atype = int.from_bytes(data[idx:idx+2], 'big') # Record Type
            rdlength = int.from_bytes(data[idx+8:idx+10], 'big') # Data Length
            idx += 10 # Move to the actual data payload
            rdata_end = idx + rdlength
            
            if atype == 12:  # PTR Record found!
                name_parts = []
                curr = idx
                visited = set() # Prevent infinite loops from recursive DNS pointers
                while curr < len(data) and data[curr] != 0:
                    if curr in visited: break
                    visited.add(curr)
                    
                    # Handle DNS compression pointers
                    if (data[curr] & 0xC0) == 0xC0:
                        pointer = int.from_bytes(data[curr:curr+2], 'big') & 0x3FFF
                        curr = pointer
                        continue
                        
                    # Extract literal string segment
                    length = data[curr]
                    curr += 1
                    name_parts.append(data[curr:curr+length].decode('utf-8', errors='ignore'))
                    curr += length
                    
                if name_parts:
                    return '.'.join(name_parts).strip('.')
                    
            idx = rdata_end
    except Exception:
        pass
    return None

def _query_router_dns(ip, gateway_ip, timeout=0.3):
    """
    Directly queries the local router/DHCP server for the device's assigned name.
    Routers often cache the hostname a device provides when requesting a DHCP IP address.
    """
    if not gateway_ip or gateway_ip in ("-", "Unknown") or ip == gateway_ip:
        return None
    try:
        query = _build_dns_ptr_query(ip)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(query, (gateway_ip, 53))
            data, _ = s.recvfrom(1024)
            name = _parse_dns_ptr_response(data)
            if name:
                # Strip generic router-assigned domain suffixes for a cleaner UI
                for suffix in [".lan", ".home", ".localdomain", ".domain"]:
                    if name.lower().endswith(suffix):
                        name = name[:-len(suffix)]
                return name
    except Exception:
        pass
    return None

def _query_mdns(ip, timeout=0.35):
    """
    Queries the target device directly via Unicast mDNS (UDP port 5353).
    Highly effective for discovering Apple devices, Android phones, smart TVs, and modern IoT hubs.
    """
    try:
        query = _build_dns_ptr_query(ip)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(query, (ip, 5353))
            data, _ = s.recvfrom(1024)
            name = _parse_dns_ptr_response(data)
            if name:
                if name.lower().endswith(".local"):
                    name = name[:-6]
                return name
    except Exception:
        pass
    return None

def _query_netbios_socket(ip, timeout=0.3):
    """
    Cross-platform NetBIOS Node Status query over UDP port 137.
    Sends a raw Windows SMB/NetBIOS payload directly to the device.
    Crucial for identifying Windows PCs, legacy servers, and Samba file shares.
    """
    # Standard NetBIOS Node Status Request Payload
    nb_query = b"\x82\x28\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00\x00\x21\x00\x01"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(nb_query, (ip, 137))
            data, _ = s.recvfrom(1024)
            if len(data) > 56:
                num_names = data[56]
                offset = 57
                for _ in range(num_names):
                    if offset + 18 <= len(data):
                        raw_name = data[offset:offset+15].decode('ascii', errors='ignore').strip()
                        rtype = data[offset+15]
                        flags = int.from_bytes(data[offset+16:offset+18], 'big')
                        is_group = bool(flags & 0x8000)
                        # Return the first unique active workstation name (Type 0x00 or 0x20)
                        if not is_group and rtype in (0x00, 0x20) and raw_name and not raw_name.startswith("IS~"):
                            return raw_name
                        offset += 18
    except Exception:
        pass
    return None

def _query_http_title(ip, timeout=0.35):
    """
    Scrapes the HTML <title> tag on common web server ports (80/8080).
    Many headless devices (printers, smart switches, IP cameras) don't broadcast a hostname
    but host a configuration webpage. This extracts the name from that page.
    """
    for port in (80, 8080):
        try:
            url = f"http://{ip}:{port}/"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                chunk = resp.read(2048).decode('utf-8', errors='ignore')
                match = re.search(r'<title>(.*?)</title>', chunk, re.IGNORECASE | re.DOTALL)
                if match:
                    title = " ".join(match.group(1).split()).strip()
                    # Filter generic, unhelpful webpage titles so they don't pollute the UI
                    if title and title.lower() not in ("login", "welcome", "home", "index", "404 not found", "error"):
                        return title[:40]
        except Exception:
            continue
    return None

def resolve_hostname(ip, mac=None, gateway_ip=None):
    """
    Multi-tier hostname resolution engine designed to quickly identify devices on a local subnet.
    Runs sequentially through 6 different resolution strategies until it finds a valid name.
    
    1. Direct Gateway/Router DHCP DNS query (UDP 53) - Fast, relies on router cache.
    2. Unicast mDNS query (UDP 5353) - Great for Apple, Android, and modern IoT devices.
    3. NetBIOS Node Status (UDP 137, pure Python) - Great for Windows/Samba environments.
    4. Native Windows nbtstat - Bypasses OS socket restrictions on Windows machines.
    5. Operating System DNS resolver - Standard fallback (gethostbyaddr).
    6. Web UI Title scraper - Connects to port 80/8080 and scrapes the HTML <title> tag.
    """
    hostname = None

    # Step 1: Query the router directly (It usually knows the DHCP lease names)
    if gateway_ip:
        hostname = _query_router_dns(ip, gateway_ip)

    # Step 2: Apple Bonjour / Multicast DNS (Most mobile devices respond to this)
    if not hostname:
        hostname = _query_mdns(ip)

    # Step 3: Pure-Python NetBIOS query (Works across all operating systems natively)
    if not hostname:
        hostname = _query_netbios_socket(ip)

    # Step 4: Native Windows NetBIOS fallback (Handles strict Windows firewalls/permissions better)
    if not hostname and platform.system() == "Windows":
        try:
            out = subprocess.check_output(["nbtstat", "-A", ip], text=True, timeout=1.0)
            for line in out.split('\n'):
                if "<20>" in line and "UNIQUE" in line:
                    hostname = line.split("<20>")[0].strip()
                    break
        except Exception:
            pass

    # Step 5: System Resolver (Relies on whatever local DNS the host machine is configured for)
    if not hostname:
        try:
            name, _, _ = socket.gethostbyaddr(ip)
            if name and name != ip and name != "?":
                hostname = name
        except Exception:
            pass

    # Step 6: Web Title Scraping (Useful for headless printers, routers, webcams, and switches)
    if not hostname:
        hostname = _query_http_title(ip)

    # Clean up and apply Vendor fallback if completely unresolved
    if not hostname or hostname in ("Unknown", "Unknown Device", "?"):
        hostname = "Unknown Device"
        if mac:
            # Look up the manufacturer from the MAC OUI block locally if we failed to get a real name
            vendor = get_mac_vendor(mac, fetch_online=False)
            if vendor:
                hostname = f"Unknown ({vendor})"
    else:
        # Strip trailing local domains to make the UI look cleaner
        if hostname.endswith(".local"):
            hostname = hostname[:-6]

    return hostname

def process_device_info(received, gateway_ip=None):
    """
    Threaded worker function utilized by the main Network Scanner engine.
    Extracts the target's IP and MAC from a raw Scapy network packet, 
    then independently triggers hostname resolution and open port scanning.
    """
    ip = getattr(received, 'psrc', getattr(received, 'ip', None))
    mac = getattr(received, 'hwsrc', getattr(received, 'mac', None))
    
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip, mac, gateway_ip),
        "vendor": get_mac_vendor(mac, fetch_online=False),
        "services": check_open_ports(ip)['services']
    }

def check_open_ports(ip):
    """
    Performs a rapid TCP connection test on a specific set of critical network management ports.
    Returns a formatted string of identified services (e.g., 'HTTP, SSH') to be saved in the database.
    """
    services = []
    # Dictionary of specific administration ports to check: {port: "Name"}
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
        s.settimeout(0.15)  # Exceptionally fast timeout since we only scan the local subnet
        
        # connect_ex returns 0 if the port is open and accepting connections
        if s.connect_ex((ip, port)) == 0:
            services.append(name)
        s.close()
        
    return {"services": ", ".join(services) if services else "None"}

def get_gateway_mac(gateway_ip):
    """
    Resolves the actual physical MAC address of the network's gateway router.
    This is crucial because Corporate VLANs or VPNs often reuse common IPs (like 192.168.1.1).
    By locking the Network ID to the Gateway MAC address, we prevent device collision in the database.
    """
    if not gateway_ip or gateway_ip == "-" or gateway_ip == "Unknown": 
        return None
    
    target_iface = None
    pinned_mac = None

    # 1. Check if the user has explicitly pinned an adapter for scanning
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
    except: 
        pass

    # 2. Match the Pinned MAC back to its system interface name
    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == psutil.AF_LINK and a.address == pinned_mac for a in addrs):
                target_iface = name
                break

    # 3. Fallback to the active interface if no pin is configured
    if not target_iface:
        target_iface = get_active_interface_name()

    # Abort if the active interface was intentionally hidden by the user
    if not target_iface:
        return None

    try:
        # Use 'iface' to force Scapy to only send the ARP request out of the target adapter
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), 
                     timeout=2, verbose=0, iface=target_iface, promisc=False)
        for _, received in ans: 
            return received.hwsrc
    except Exception as e:
        # If Windows/Npcap rejects the direct interface string, fall back to Scapy's default routing table
        print(f"[*] Targeted Gateway MAC resolution failed on {target_iface}: {e}. Retrying globally...")
        try:
            ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), 
                         timeout=2, verbose=0, promisc=False)
            for _, received in ans: 
                return received.hwsrc
        except: pass
    
    return None

def get_current_network_context():
    """
    Helper function designed specifically to enrich DNS and Ping tool logs.
    Calculates the active LAN IP, Gateway Router IP, and resolves the current Network Name
    so that tools have context of *where* the user was when they ran a test.
    """
    lan_ip = get_local_ip()
    
    # 1. Get Gateway
    ext_info = get_extended_iface_info()
    gateway_ip = "-"
    
    for iface_details in ext_info.values():
        gw = iface_details.get("gateway", "-")
        # STRICT FILTER: Only accept if not "-" and NO colons present (blocks IPv6 noise)
        if gw != "-" and ":" not in gw:
            gateway_ip = gw
            break
            
    # 2. Lookup the human-readable Network Name using BOTH the Gateway MAC and IP
    network_name = "Unknown Network"
    if gateway_ip != "-":
        gateway_mac = get_gateway_mac(gateway_ip)
        if gateway_mac:
            with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
                # FIXED: Must use both MAC and IP to prevent VLAN collisions
                row = conn.execute("SELECT name FROM networks WHERE gateway_mac=? AND gateway_ip=?", (gateway_mac, gateway_ip)).fetchone()
                if row: network_name = row[0]
                
    return lan_ip, gateway_ip, network_name

def request_macos_permissions():
    """
    Legacy Native Application Permission Probe for macOS TCC.
    (Note: Replaced primarily by the launcher script in updated installers to prevent sudo blocking).
    Attempts to force macOS to display the 'Location Services' and 'Local Network' permission
    dialogs by simulating native API activity, which are strictly required for Wi-Fi scanning.
    """
    if platform.system() == "Darwin":
        print("[*] Probing macOS Network & Location permissions...")
        
        # 1. Trigger Local Network Access Prompt by sending a raw UDP packet outward
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            s.send(b"probing-local-network")
            s.close()
        except: pass

        # 2. Trigger Location Services Prompt by executing a Wi-Fi scan via the native 'airport' utility
        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if os.path.exists(airport_path):
            try:
                # Running a scan forces the OS to evaluate Location permissions
                subprocess.Popen([airport_path, "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("[!] If prompted, please allow 'Location Access' for Wi-Fi scanning to work.")
            except: pass

# --- Authentication Middleware ---
def authenticate():
    """
    Sends a 401 Unauthorized HTTP response.
    This explicitly tells the browser to trigger its native Basic Authentication popup window.
    """
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Network Dashboard Login Required"'})

@app.before_request
def require_auth():
    """
    Security Middleware: Intercepts and evaluates EVERY single incoming HTTP request.
    - Allows 'OPTIONS' requests to pass freely (required for standard browser CORS preflight checks).
    - Verifies the requested Basic Auth credentials against the securely hashed SQLite database.
    - Implements an Anti-Lockout Failsafe: If the database says auth is enabled, but the 
      actual username/password rows are missing or corrupted, it safely bypasses the lock.
    """
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
                    
                # Verify the provided password against the PBKDF2:SHA256 hash stored in the database
                if auth.username != user_row[0] or not check_password_hash(pass_row[0], auth.password):
                    return authenticate()
        except sqlite3.OperationalError:
            pass # Failsafe if the database hasn't fully initialized yet on the very first application boot

# --- Authentication API Routes ---
@app.route('/api/settings/auth', methods=['GET'])
def get_auth_settings():
    """Fetches the current authentication state to populate the UI Settings toggle."""
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
    """
    Saves the authentication state and securely hashes the user's password.
    Enforces string limits to prevent buffer bloat and requires a password on initial setup.
    """
    d = request.json
    enabled = '1' if d.get('enabled') else '0'
    username = str(d.get('username', '')).strip()[:50] # Hard limit to 50 characters
    password = str(d.get('password', ''))[:255] # Hard limit to 255 characters

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        # Check if a password already exists so we don't accidentally overwrite a valid hash with an empty string
        try:
            pass_row = conn.execute("SELECT value FROM system_settings WHERE key='auth_password'").fetchone()
        except sqlite3.OperationalError:
            pass_row = None
            
        # Server-side validation to prevent broken lockout states
        if enabled == '1':
            if not username:
                return jsonify({"status": "error", "message": "A username is required."}), 400
            if not pass_row and not password:
                return jsonify({"status": "error", "message": "A password is required for the first setup."}), 400

        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_enabled', ?)", (enabled,))
        if username:
            conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_username', ?)", (username,))
        if password: 
            # Use Werkzeug to generate a salted PBKDF2:SHA256 hash (never store plain-text passwords)
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auth_password', ?)", (hashed_pw,))
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/settings/port', methods=['GET'])
def get_port():
    """Fetches the current internal Waitress web server port from the database."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        try:
            row = conn.execute("SELECT value FROM system_settings WHERE key='web_port'").fetchone()
            port = int(row[0]) if row else 81
        except:
            port = 81
    return jsonify({"port": port})

@app.route('/api/settings/port', methods=['POST'])
def set_port():
    """
    Saves a new web port and immediately triggers a background application restart to apply it.
    Applies strict mathematical validation to ensure the port is within the valid OS TCP bounds.
    """
    new_port = request.json.get('port')
    
    # ADDED STRICT VALIDATION: Ensures the port isn't empty, is numeric, and fits the 1-65535 standard
    if not new_port or not str(new_port).isdigit() or not (1 <= int(new_port) <= 65535):
        return jsonify({"status": "error", "message": "Invalid port number. Must be between 1 and 65535."}), 400
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('web_port', ?)", (str(new_port),))
        conn.commit()
        
    # Trigger a restart in the background to apply the new port binding
    threading.Thread(target=restart_server).start()
    return jsonify({"status": "success", "port": new_port})

def check_webport_file():
    """
    Headless CLI Fallback: Checks for a physical 'webport' file in the root directory.
    If a user locks themselves out of the dashboard by setting a bad port, they can just create
    a file named 'webport' containing '8080' to force a recovery on the next boot.
    """
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
    """
    Headless CLI Fallback: Checks for a 'dev' trigger file.
    Updates the software update channel to Development if found, then cleans up the trigger.
    """
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
    """Reads the current port from the DB for Waitress to bind to on initial startup."""
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT value FROM system_settings WHERE key='web_port'").fetchone()
            return int(row[0]) if row else 81
    except:
        return 81

# --- Routes ---
@app.route('/')
def index():
    """
    The primary Dashboard render route.
    Calculates the active interface and its specific DNS/Gateway. Because OS network
    naming conventions vary wildly, it uses fuzzy-matching to associate physical settings.
    """
    info = get_isp_info()
    ext_info = get_extended_iface_info()
    active_iface = get_active_interface_name() # e.g., "Wi-Fi" or "eth0"
    
    # Default to "Unknown" if we can't find a definitive match
    active_gateway = "Unknown"
    active_dns = "Unknown"

    # Try to find the specific Gateway/DNS for the active interface
    if active_iface:
        # 1. Try Exact Match (Works beautifully on Linux and macOS)
        if active_iface in ext_info:
            active_gateway = ext_info[active_iface].get("gateway", "Unknown")
            active_dns = ext_info[active_iface].get("dns", "Unknown")
        
        # 2. Try Fuzzy Match (Windows naming is often messy, e.g., "Wi-Fi" inside "Wireless LAN adapter Wi-Fi")
        elif platform.system() == "Windows":
            for k, v in ext_info.items():
                if active_iface in k or k in active_iface:
                    active_gateway = v.get("gateway", active_gateway)
                    active_dns = v.get("dns", active_dns)
                    break
    
    # 3. Fallback: If still unknown, just grab the first interface that possesses a valid Gateway IP
    if active_gateway == "Unknown" or active_gateway == "-":
        for v in ext_info.values():
            if v.get("gateway") and v.get("gateway") != "-":
                active_gateway = v.get("gateway")
                active_dns = v.get("dns")
                break

    # Grab the current update channel to pass to the frontend
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key='update_channel'").fetchone()
        current_channel = row[0] if row else 'stable'

    # Get the display name (e.g. 'Production (Stable)') for the footer tag
    all_settings = get_all_github_settings()
    channel_display_name = all_settings.get(current_channel, {}).get("display_name", current_channel.capitalize())
    
    # --- NEW: Check for Dedicated Server Mode (Hides OS Reboot/Shutdown controls if absent) ---
    is_standalone = os.path.exists(os.path.join(app.root_path, "standalone"))

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
                           update_channel_name=channel_display_name,
                           is_standalone=is_standalone) # <-- NEW VARIABLE

@app.route('/api/system/cleanup', methods=['POST'])
def cleanup_database():
    """
    Maintenance Tool: Cleans up the database based on the selected interval across ALL tables.
    Crucially, it respects the 'is_protected' (locked) flag for every table, guaranteeing
    that user-favorited devices/logs are never automatically wiped unless a full 'all' wipe is forced.
    """
    days = request.json.get('days')
    
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            cursor = conn.cursor()
            
            if days == 'all':
                # Note: We do NOT respect is_protected on a total factory wipe ("Clear Database Completely")
                for table in ['history', 'dns_logs', 'ping_logs', 'wifi_history', 'networks', 'devices', 'device_scans', 'global_device_names', 'global_device_vendors']:
                    cursor.execute(f"DELETE FROM {table}")
                message = "Database cleared completely."
            else:
                if not str(days).isdigit():
                    return jsonify({"status": "error", "message": "Invalid time interval."}), 400
                
                date_filter = f"datetime('now', '-{int(days)} days')"
                
                # Standard tables using 'timestamp' column
                for table in ['history', 'dns_logs', 'ping_logs', 'wifi_history', 'device_scans']:
                    # device_scans relies entirely on the 'devices' table for its protection status
                    if table == 'device_scans':
                        cursor.execute(f"DELETE FROM {table} WHERE timestamp < {date_filter} AND mac_address NOT IN (SELECT mac_address FROM devices WHERE is_protected=1)")
                    else:
                        cursor.execute(f"DELETE FROM {table} WHERE timestamp < {date_filter} AND is_protected = 0")
                
                # Tables using 'last_seen' or 'last_scan' columns
                cursor.execute(f"DELETE FROM devices WHERE last_seen < {date_filter} AND is_protected = 0")
                cursor.execute(f"DELETE FROM networks WHERE last_scan < {date_filter} AND is_protected = 0")
                
                message = f"All data older than {days} days has been removed. (Protected items were kept)."
            
            conn.commit()
            conn.execute("VACUUM") # Force SQLite to defragment and release the physical hard drive space
            
            return jsonify({"status": "success", "message": message})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/system/db_info', methods=['GET'])
def get_db_info():
    """Returns the current physical size of the main SQLite database converted to MB for the UI."""
    try:
        size_mb = 0
        if os.path.exists(DB_NAME):
            size_mb = os.path.getsize(DB_NAME) / (1024 * 1024)
        return jsonify({"size_mb": f"{size_mb:.2f}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/system/cleanup_orphaned_devices', methods=['POST'])
def cleanup_orphaned_devices():
    """
    Maintenance Tool: Removes specific devices that are ONLY associated with deleted network profiles.
    This safely prunes 'ghost' devices that clutter the historical registry without deleting
    devices that might still be active on a different active network.
    """
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            cursor = conn.cursor()
            
            # Smart isolation query using SET logic (UNION/EXCEPT): 
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
            
            # Completely purge the orphaned ghost devices from all tables
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
    """Simple API wrapper to return the live bandwidth throughput dict."""
    return jsonify(get_bandwidth())

@app.route('/api/adapters')
def get_adapters():
    """
    Fetches the physical and virtual network adapters available on the host machine.
    - Utilizes ThreadPoolExecutor to run heavy OS-level terminal commands concurrently.
    - Accurately identifies Wi-Fi vs Ethernet using strict OS-level framework queries.
    - Evaluates which adapter is actively providing internet ('Active') and which one is 'Pinned'.
    """
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    
    # Execute heavy OS calls concurrently (Cuts delay dramatically)
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_ext = executor.submit(get_extended_iface_info)
        f_wifi = executor.submit(get_wifi_rates)
        f_active = executor.submit(get_active_interface_name)
        f_wifi_ifaces = executor.submit(get_visible_wifi_interfaces)
        
        ext_info = f_ext.result()
        wifi_rates = f_wifi.result()
        active_iface_name = f_active.result()
        known_wifi_ifaces = f_wifi_ifaces.result()
        
    # Extract the precise hardware IDs (like 'en0') that the OS confirmed are Wi-Fi cards
    known_wifi_ids = [i["id"] for i in known_wifi_ifaces]
    
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
        
        # Completely ignore internal software loopbacks and Hyper-V virtual switches to reduce noise
        if "Loopback" in name or "vEthernet" in name or name == "lo": 
            continue
        
        ip4, mac = "-", "-"
        for a in addrs:
            if a.family == socket.AF_INET: 
                ip4 = a.address
            elif a.family == psutil.AF_LINK: 
                mac = a.address

        spec_info = ext_info.get(name, {})
        
        # Windows formatting fuzzy match
        if not spec_info and platform.system() == "Windows":
             for k, v in ext_info.items():
                 if k in name or name in k:
                     spec_info = v
                     break

        if mac == "-" and spec_info.get("mac"):
            mac = spec_info.get("mac")

        gw = spec_info.get("gateway", "-")
        if ":" in gw: gw = "-" # Filter out IPv6 noise
        
        dns = spec_info.get("dns", "-")
        if ":" in dns: dns = "-" # Filter out IPv6 noise

        if platform.system() == "Linux":
            real_dns = get_linux_dns(name)
            if real_dns:
                dns = real_dns

        is_pinned = (mac == pinned_mac) if pinned_mac else False
        is_active_default = False
        if not pinned_mac and active_iface_name:
            is_active_default = (name == active_iface_name or name in active_iface_name)

        # Set the Global Primary Gateway/DNS specifically based on the pinned/active adapter
        if is_pinned or (not pinned_mac and is_active_default):
             if gw != "-": primary_gw = gw
             if dns != "-": primary_dns = dns

        # --- NEW: Identify if it is Wi-Fi or Ethernet (Fixed for macOS) ---
        is_wifi = False
        if name in known_wifi_ids:
            is_wifi = True
        elif any(w in name.lower() for w in ["wi-fi", "wireless", "wlan", "802.11"]):
            is_wifi = True

        raw_speed = st.speed if st else 0
        display_speed = "Not Available"
        
        if raw_speed and raw_speed > 0:
            if raw_speed >= 1000:
                display_speed = f"{raw_speed/1000:g} Gbps"
            else:
                display_speed = f"{raw_speed} Mbps"
        
        # Check against cached rates list to confirm Wi-Fi status
        for wifi_name, rate_str in wifi_rates.items():
            if wifi_name.lower() in name.lower() or name.lower() in wifi_name.lower():
                display_speed = rate_str
                is_wifi = True # Confirmed Wi-Fi via rates list

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
            "is_primary": is_pinned,
            "is_active": is_active_default,
            "type": "Wi-Fi" if is_wifi else "Ethernet"
        })

    # Failsafe: if the active adapter lacks a gateway, pull the first valid one available
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
    """
    Updates the custom name, visibility, and primary (pinned) status of a network adapter.
    Implements a crucial safety net to ensure a user cannot 'hide' the very last active 
    adapter on their system, which would break the dashboard entirely.
    """
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
        
        # 1. Gather all actual usable physical adapters on the system
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
            
        # Update or Insert the new settings (Upsert)
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
    """Simplified endpoint to update custom name and visibility for a specific adapter."""
    d = request.json
    mac = d.get('mac')
    # --- FIXED: Apply string limit ---
    name = str(d.get('name', '')).strip()[:50]
    visible = 1 if d.get('visible') else 0
    
    # If the adapter has no MAC (e.g. a virtual interface), we can't reliably save settings to it
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

# --- Audit Logging Middleware ---
@app.after_request
def audit_logger(response):
    """
    Security Middleware: Automatically intercepts and logs critical configuration changes, 
    deletions, and data exports.
    Provides an internal audit trail within the daily log files for security analysis.
    """
    # Only track successful state-changing or export requests (200/201 HTTP Status)
    if response.status_code in [200, 201] and (request.method in ['POST', 'DELETE'] or 'export' in request.path or 'download' in request.path):
        
        # Ignore background polling and raw tool execution to prevent log spam 
        # (Ping, DNS, Speedtest, etc. are already logged manually)
        ignore_paths = ['/api/speedtest', '/api/scan_network', '/api/dns/lookup', '/api/ping/run', '/api/vendor/lookup', '/api/wifi/save', '/api/live_bandwidth']
        if any(p in request.path for p in ignore_paths) and 'export' not in request.path:
            return response
        
        action = "System Action"
        path = request.path
        
        # Categorize the action
        if 'export' in path or 'download' in path: action = "Data Export"
        elif 'delete' in path or 'clear' in path or 'cleanup' in path: action = "Data Deletion"
        elif 'update' in path or 'rename' in path or 'settings' in path or 'bulk_hide' in path: action = "Configuration Change"
        elif 'toggle_protection' in path: action = "Record Protection Toggled"
        elif 'import' in path: action = "Database Merged"
        
        # Capture the payload context safely if it's a small JSON request
        context = ""
        if request.is_json:
            try:
                data = request.get_json()
                if data:
                    # Mask sensitive payloads or collapse massive arrays
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
    """Fetches all saved network profiles and calculates their total historical device count."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        networks = conn.execute("SELECT * FROM networks ORDER BY last_scan DESC").fetchall()
        result = []
        for net in networks:
            # Dynamically count how many devices belong to this specific network
            count = conn.execute("SELECT COUNT(*) FROM devices WHERE network_id=?", (net['id'],)).fetchone()[0]
            net_dict = dict(net)
            result.append({
                "id": net_dict['id'], "name": net_dict['name'], "gateway_mac": net_dict['gateway_mac'],
                "gateway_ip": net_dict['gateway_ip'], "last_scan": net_dict['last_scan'], "device_count": count,
                "is_protected": net_dict.get('is_protected', 0),
                "allow_matching": net_dict.get('allow_matching', 1),
                "comments": net_dict.get('comments', '') 
            })
        return jsonify(result)

@app.route('/api/system/toggle_matching', methods=['POST'])
def toggle_matching():
    """
    Toggles whether a network profile automatically merges with future scans (Auto-Match).
    Implements conflict resolution: Only ONE network can claim a specific Gateway MAC & IP combo
    at a time to prevent future scans from duplicating or splitting data.
    """
    d = request.json
    item_id = d.get('id')
    state = 1 if d.get('state') else 0
    force = d.get('force', False)
    
    if item_id is not None:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if state == 1:
                # 1. Check if another network with the EXACT same MAC & IP is already set to Auto-Match
                net = cursor.execute("SELECT gateway_mac, gateway_ip FROM networks WHERE id=?", (item_id,)).fetchone()
                if net:
                    mac = net['gateway_mac']
                    ip = net['gateway_ip']
                    conflict = cursor.execute("SELECT id, name FROM networks WHERE gateway_mac=? AND gateway_ip=? AND allow_matching=1 AND id!=?", (mac, ip, item_id)).fetchone()
                    
                    if conflict and not force:
                        # Alert the frontend so it can ask the user if they want to override the conflict
                        return jsonify({
                            "status": "conflict", 
                            "message": f"Another network ('{conflict['name']}') is already set to Auto-Match with this Gateway.\n\nEnabling Auto-Match for this scan will disable it for the older one."
                        })
                    elif conflict and force:
                        # 2. User confirmed the overwrite: Disable matching on all conflicting networks globally
                        cursor.execute("UPDATE networks SET allow_matching=0 WHERE gateway_mac=? AND gateway_ip=? AND id!=?", (mac, ip, item_id))
            
            # Apply the requested state to the target network
            cursor.execute("UPDATE networks SET allow_matching = ? WHERE id = ?", (state, item_id))
            conn.commit()
            
        return jsonify({"status": "success"})
    return jsonify({"error": "Invalid request"}), 400

@app.route('/api/networks/delete', methods=['POST'])
def delete_network():
    """Deletes a network profile. Associated devices are CASCADE deleted automatically by SQLite schema."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM networks WHERE id=?", (request.json.get('id'),))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/merge', methods=['POST'])
def merge_networks():
    """
    Merges multiple fragmented network profiles into a single target network.
    Safely resolves device MAC collisions (if a device existed on both the old and new network)
    by preserving the most recent 'last_seen' timestamp and keeping user-defined metadata.
    """
    d = request.json
    target_id = d.get('target_id')
    source_ids = d.get('source_ids', [])
    
    if not target_id or not source_ids:
        return jsonify({"error": "Invalid parameters provided for merge."}), 400
        
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            target_net = cursor.execute("SELECT name FROM networks WHERE id=?", (target_id,)).fetchone()
            if not target_net: return jsonify({"error": "Target network not found."}), 404
            target_name = target_net['name']
            
            for src_id in source_ids:
                if str(src_id) == str(target_id): continue
                
                # 1. Migrate all historical snapshot logs (device_scans) to the new Target Network
                cursor.execute("UPDATE device_scans SET network_id=?, network_name=? WHERE network_id=?", (target_id, target_name, src_id))
                
                # 2. Safely merge devices, handling duplicates if a device existed on both networks
                src_devices = cursor.execute("SELECT * FROM devices WHERE network_id=?", (src_id,)).fetchall()
                for dev in src_devices:
                    mac = dev['mac_address']
                    target_dev = cursor.execute("SELECT * FROM devices WHERE mac_address=? AND network_id=?", (mac, target_id)).fetchone()
                    
                    if target_dev:
                        # CONFLICT: Device exists in both. Keep Target row, but update metadata if Target is missing it
                        new_last_seen = max(dev['last_seen'] or "", target_dev['last_seen'] or "")
                        c_name = target_dev['custom_name'] or dev['custom_name']
                        c_vend = target_dev['custom_vendor'] or dev['custom_vendor']
                        
                        cursor.execute("""
                            UPDATE devices 
                            SET last_seen=?, custom_name=?, custom_vendor=?, last_network_name=?
                            WHERE mac_address=? AND network_id=?
                        """, (new_last_seen, c_name, c_vend, target_name, mac, target_id))
                        
                        # Delete the duplicate source row to prevent Primary Key constraint errors
                        cursor.execute("DELETE FROM devices WHERE mac_address=? AND network_id=?", (mac, src_id))
                    else:
                        # NO CONFLICT: Safely migrate device to target network directly
                        cursor.execute("UPDATE devices SET network_id=?, last_network_name=? WHERE mac_address=? AND network_id=?", (target_id, target_name, mac, src_id))
                        
                # 3. Delete the ghost source network now that it is empty
                cursor.execute("DELETE FROM networks WHERE id=?", (src_id,))
                
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/networks/update', methods=['POST'])
def update_network():
    """Updates a network name/comment and cascades the new name to all historical device logs."""
    d = request.json
    name = str(d.get('name', '')).strip()[:50] 
    comment = str(d.get('comment', '')).strip()[:200] 
    net_id = d.get('id')
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE networks SET name=?, comments=? WHERE id=?", (name, comment, net_id))
        
        # Propagate the custom name to the history tables so UI tags don't break
        try: 
            conn.execute("UPDATE devices SET last_network_name=? WHERE network_id=?", (name, net_id))
        except sqlite3.OperationalError: pass
        
        try: 
            conn.execute("UPDATE device_scans SET network_name=? WHERE network_id=?", (name, net_id))
        except sqlite3.OperationalError: pass
        
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/rename', methods=['POST'])
def rename_network():
    """Legacy endpoint specifically for quick renames."""
    d = request.json
    name = str(d.get('name', '')).strip()[:50] # Safely sliced to prevent buffer abuse
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

@app.route('/api/networks/<int:net_id>/devices')
def get_network_devices(net_id):
    """
    Fetches all devices mapped to a specific network.
    Dynamically joins 'global_device_names' and 'global_device_vendors' to ensure 
    devices consistently display their user-defined names across completely different Wi-Fi networks.
    """
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT mac_address, hostname, custom_name, ip_address, previous_ip, discovery_status, last_seen, services, is_online, vendor, custom_vendor, is_protected FROM devices WHERE network_id=?"
        devices = conn.execute(query, (net_id,)).fetchall()
        
        dev_list = [dict(d) for d in devices]
        for dev in dev_list:
            # Overlay Global Name / Comments
            g_data = conn.execute("SELECT custom_name, comments FROM global_device_names WHERE mac_address=?", (dev['mac_address'],)).fetchone()
            if g_data:
                if g_data[0]: dev['custom_name'] = g_data[0]
                dev['comments'] = g_data[1] or ""
            else:
                dev['comments'] = ""
                
            # Overlay Global Vendor overrides
            g_vendor = conn.execute("SELECT custom_vendor FROM global_device_vendors WHERE mac_address=?", (dev['mac_address'],)).fetchone()
            if g_vendor and g_vendor[0]: dev['custom_vendor'] = g_vendor[0]

        # Attempt to sort the final list numerically by IP address (e.g. 192.168.1.2 before 192.168.1.10)
        try: dev_list.sort(key=lambda x: ipaddress.IPv4Address(x['ip_address']))
        except: pass
        return jsonify(dev_list)

# ==========================================
# NETWORK SCANNING ENGINES
# ==========================================

@app.route('/api/scan_network')
def scan_network():
    """
    Robust Pinned-First Network Scanner (Standard JSON Response Mode).
    """
    worker_cfg = get_worker_config() if 'get_worker_config' in globals() else {"scan_workers": 20, "ping_workers": 30}
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
                if a.family == psutil.AF_LINK: target_mac_val = a.address

    if not target_ip_val or target_ip_val == "127.0.0.1":
        return jsonify({"error": "Could not determine local IP subnet."})

    target_subnet = f"{target_ip_val.rsplit('.', 1)[0]}.0/24"
    if target_iface: conf.iface = target_iface
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ext_info = get_extended_iface_info()
    gateway_ip = ext_info.get(target_iface, {}).get("gateway", "-")

    try:
        scapy_iface = conf.route.route(target_ip_val)[0]
    except:
        scapy_iface = target_iface

    # --- 1. PERFORM SCAPY ARP SCAN ---
    ans = []
    is_windows = platform.system() == "Windows"
    scan_timeout = 2.5 if is_windows else 1.5
    scan_inter = 0.01
    scan_retry = 2 if is_windows else 1

    try:
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                     timeout=scan_timeout, retry=scan_retry, verbose=0, inter=scan_inter, 
                     iface=scapy_iface, promisc=False)
    except Exception as e:
        print(f"[*] Scapy targeted bind failed: {e}. Retrying globally...")
        try:
            ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                         timeout=scan_timeout, retry=scan_retry, verbose=0, inter=scan_inter, promisc=False)
        except: pass

    raw_candidates = [received for _, received in ans]
    network = ipaddress.IPv4Network(target_subnet, strict=False)

    class MockDev:
        def __init__(self, ip_found, mac_found): 
            self.ip = ip_found
            self.psrc = ip_found
            self.mac = mac_found
            self.hwsrc = mac_found

    def scrape_arp():
        devices = []
        try:
            arp_out = subprocess.check_output(["arp", "-a"], text=True)
            for match in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})', arp_out):
                ip_found, raw_mac = match.groups()
                mac_found = ':'.join([p.zfill(2) for p in raw_mac.replace('-', ':').split(':')]).lower()
                
                # Unconditional grab: We will deduplicate perfectly at the end to prevent logic drops
                if not mac_found.startswith('ff:ff') and not mac_found.startswith('01:00:5e') and not mac_found.startswith('33:33'):
                    try:
                        if ipaddress.IPv4Address(ip_found) in network:
                            devices.append(MockDev(ip_found, mac_found))
                    except: pass
        except: pass
        return devices

    def force_discovery(ip_str):
        """Hybrid Layer-2 / Layer-3 Sweep to bypass strict Windows Firewalls"""
        sys_plat = platform.system().lower()
        if sys_plat == 'windows':
            # 1. Native Windows Kernel ARP (Bypasses ICMP/Ping Firewalls completely)
            try:
                import ctypes, socket
                dest_ip = int.from_bytes(socket.inet_aton(ip_str), 'little')
                mac_addr = (ctypes.c_ubyte * 6)()
                mac_len = ctypes.c_ulong(6)
                ctypes.windll.iphlpapi.SendARP(dest_ip, 0, ctypes.byref(mac_addr), ctypes.byref(mac_len))
            except: pass
            
            # 2. Native Ping (Safety net for routed VLANs where ARP drops)
            cmd = ['ping', '-n', '1', '-w', '250', ip_str]
            try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass
        elif sys_plat == 'darwin':
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(0.1)
                    s.sendto(b'\x00', (ip_str, 5353))
            except: pass
            cmd = ['ping', '-c', '1', '-W', '250', ip_str]
            try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(0.1)
                    s.sendto(b'\x00', (ip_str, 5353))
            except: pass
            cmd = ['ping', '-c', '1', '-W', '1', ip_str]
            try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass

    # --- 2. UNCONDITIONAL OS ARP TABLE MERGE ---
    raw_candidates.extend(scrape_arp())

    # --- 3. ROCK-SOLID OS HYBRID FALLBACK SWEEP ---
    found_ips = set(getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates)
    
    if is_windows or len(found_ips) <= 15:
        print("[*] Scan found few devices. Initiating OS Hybrid Fallback Sweep...")
        max_threads = 60 if is_windows else 255
        
        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            for ip_obj in network.hosts(): 
                ip_str = str(ip_obj)
                if ip_str not in found_ips:
                    executor.submit(force_discovery, ip_str)
                    
        time.sleep(0.5) 
        raw_candidates.extend(scrape_arp())

    # --- 4. EXTRACT GATEWAY MAC FROM RESULTS ---
    gateway_mac = None
    for device in raw_candidates:
        if getattr(device, 'psrc', getattr(device, 'ip', '')) == gateway_ip:
            gateway_mac = getattr(device, 'hwsrc', getattr(device, 'mac', None))
            break

    if not gateway_mac and gateway_ip != "-" and gateway_ip != "Unknown":
        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            existing_net = conn.execute("SELECT gateway_mac FROM networks WHERE gateway_ip=? AND allow_matching=1 ORDER BY last_scan DESC LIMIT 1", (gateway_ip,)).fetchone()
            if existing_net and existing_net[0] and not existing_net[0].startswith("NO_MAC"): gateway_mac = existing_net[0]
                
        if not gateway_mac: gateway_mac = get_gateway_mac(gateway_ip)
        if not gateway_mac: gateway_mac = f"NO_MAC_{int(time.time()*1000)}"

    # --- 5. NETWORK IDENTIFICATION ---
    network_id = None
    allow_match_val = 1
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        existing_net = conn.execute("SELECT id, name FROM networks WHERE gateway_mac=? AND gateway_ip=? AND allow_matching=1 ORDER BY last_scan DESC LIMIT 1", (gateway_mac, gateway_ip)).fetchone()
        if existing_net:
            network_id = existing_net[0]
            final_network_name = existing_net[1]
        else:
            final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"

    # --- 6. TARGETED HISTORICAL POKE ---
    if network_id and allow_match_val == 1:
        historical_targets = []
        found_macs = set(getattr(d, 'hwsrc', getattr(d, 'mac', '')) for d in raw_candidates)
        found_ips = set(getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates)
        
        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            for r in conn.execute("SELECT ip_address, mac_address FROM devices WHERE network_id=?", (network_id,)).fetchall():
                hist_ip, hist_mac = r[0], r[1]
                if hist_mac not in found_macs and hist_ip not in found_ips and hist_ip != "0.0.0.0":
                    try:
                        if ipaddress.IPv4Address(hist_ip) in network:
                            historical_targets.append(hist_ip)
                    except: pass
                    
        if historical_targets:
            print(f"[*] Targeted Poke: Giving {len(historical_targets)} historical devices a second chance at their last known IPs...")
            max_threads = 60 if is_windows else 255
            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                for ip in historical_targets:
                    executor.submit(force_discovery, ip)
            time.sleep(0.5)
            raw_candidates.extend(scrape_arp())

    # --- 7. DEDUPLICATE ALL CANDIDATES ---
    unique_candidates = {}
    for d in raw_candidates:
        ip = getattr(d, 'psrc', getattr(d, 'ip', None))
        if ip: unique_candidates[ip] = d
    raw_candidates = list(unique_candidates.values())

    # --- 8. PROCESS ALL DEVICES CONCURRENTLY ---
    scanned_results = []
    with ThreadPoolExecutor(max_workers=worker_cfg['scan_workers']) as executor:
        futures = [executor.submit(process_device_info, dev, gateway_ip) for dev in raw_candidates]
        for future in futures: scanned_results.append(future.result())

    # --- 9. MANUALLY APPEND GATEWAY & LOCALHOST IF MISSED ---
    found_ips = [d['ip'] for d in scanned_results]
    
    if gateway_ip not in found_ips and gateway_ip != "-" and gateway_ip != "Unknown":
        scanned_results.append({
            "ip": gateway_ip, "mac": gateway_mac,
            "hostname": f"{resolve_hostname(gateway_ip, gateway_mac)} (Router)",
            "vendor": get_mac_vendor(gateway_mac, fetch_online=False),
            "services": check_open_ports(gateway_ip)['services']
        })
        
    if target_ip_val not in found_ips and target_mac_val and target_ip_val != "127.0.0.1":
        scanned_results.append({
            "ip": target_ip_val, "mac": target_mac_val,
            "hostname": f"{socket.gethostname()} (This device)",
            "vendor": get_mac_vendor(target_mac_val, fetch_online=False),
            "services": check_open_ports(target_ip_val)['services']
        })

    # --- 10. DB NETWORK CREATION & DEVICE SAVING ---
    try:
        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            cursor = conn.cursor()
            
            if network_id:
                cursor.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, network_id))
            else:
                cursor.execute("SELECT id FROM networks WHERE gateway_mac=? AND gateway_ip=?", (gateway_mac, gateway_ip))
                allow_match_val = 0 if cursor.fetchone() else 1
                cursor.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip, allow_matching, comments) VALUES (?, ?, ?, ?, ?, '')", 
                               (gateway_mac, final_network_name, current_time, gateway_ip, allow_match_val))
                network_id = cursor.lastrowid

            cursor.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))

            for device in scanned_results:
                cursor.execute("SELECT custom_name, custom_vendor FROM devices WHERE mac_address=? AND network_id=?", (device["mac"], network_id))
                existing = cursor.fetchone()
                final_name = existing[0] if existing and existing[0] else ""
                final_vendor = existing[1] if existing and len(existing) > 1 and existing[1] else ""
                
                if not final_name:
                    glob = cursor.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (device["mac"],)).fetchone()
                    if glob: final_name = glob[0]
                    
                if not final_vendor:
                    glob_vend = cursor.execute("SELECT custom_vendor FROM global_device_vendors WHERE mac_address=?", (device["mac"],)).fetchone()
                    if glob_vend: final_vendor = glob_vend[0]

                cursor.execute("""
                    INSERT INTO devices (mac_address, network_id, hostname, custom_name, custom_vendor, ip_address, previous_ip, discovery_status, last_seen, services, is_online, vendor, last_network_name)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, 'New Device', ?, ?, 1, ?, ?)
                    ON CONFLICT(mac_address, network_id) DO UPDATE SET
                    hostname=excluded.hostname, 
                    custom_name=COALESCE(NULLIF(?, ''), devices.custom_name),
                    custom_vendor=COALESCE(NULLIF(?, ''), devices.custom_vendor),
                    previous_ip = CASE WHEN devices.ip_address != excluded.ip_address AND devices.ip_address != '0.0.0.0' THEN devices.ip_address ELSE devices.previous_ip END,
                    discovery_status = 'Seen Before', ip_address=excluded.ip_address, last_seen=excluded.last_seen,
                    services=excluded.services, is_online=1, vendor=excluded.vendor, last_network_name=excluded.last_network_name
                """, (device["mac"], network_id, device["hostname"], final_name, final_vendor, device["ip"], current_time, device["services"], device["vendor"], final_network_name, final_name, final_vendor))

                cursor.execute("INSERT INTO device_scans (mac_address, network_id, ip_address, hostname, services, timestamp, network_name) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                               (device["mac"], network_id, device["ip"], device["hostname"], device["services"], current_time, final_network_name))

            conn.commit()
            return jsonify({"network_id": network_id, "network_name": final_network_name, "devices": scanned_results})
            
    except Exception as e: 
        return jsonify({"error": f"DB Error: {str(e)}"})
    
@app.route('/api/scan_network_stream')
def scan_network_stream():
    """
    Server-Sent Events (SSE) Streaming Network Scanner.
    Ensures pre-scan gateway MAC resolution is prioritized to prevent breaking 
    auto-matching against historical network profiles with identical IPs.
    """
    is_continue = request.args.get('mode') == 'continue'
    force_merge = request.args.get('force_merge') == 'true'
    expected_net_id = request.args.get('network_id')

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

            ext_info = get_extended_iface_info()
            gateway_ip = ext_info.get(target_iface, {}).get("gateway", "-")

            # --- 0. SMART PRE-CHECK FOR CONTINUE SCAN MISMATCH ---
            if is_continue and expected_net_id and not force_merge:
                with sqlite3.connect(DB_NAME, timeout=10) as conn:
                    db_net = conn.execute("SELECT gateway_mac, gateway_ip FROM networks WHERE id=?", (expected_net_id,)).fetchone()
                    if db_net:
                        db_mac, db_ip = db_net[0], db_net[1]
                        
                        mismatch = False
                        if gateway_ip != "-" and db_ip != "-" and gateway_ip != db_ip:
                            mismatch = True
                        else:
                            gw_mac = get_gateway_mac(gateway_ip) if gateway_ip != "-" else None
                            if gw_mac and db_mac and gw_mac != db_mac and not db_mac.startswith("NO_MAC"):
                                mismatch = True
                                
                        if mismatch:
                            yield f"data: {json.dumps({'type': 'mismatch', 'message': 'Network change detected.'})}\n\n"
                            return

            # --- 1. ROBUST PRE-SCAN GATEWAY MAC RESOLUTION ---
            gateway_mac = None
            if gateway_ip != "-" and gateway_ip != "Unknown":
                with sqlite3.connect(DB_NAME, timeout=10) as conn:
                    existing_net_match = conn.execute("SELECT gateway_mac FROM networks WHERE gateway_ip=? AND allow_matching=1 ORDER BY last_scan DESC LIMIT 1", (gateway_ip,)).fetchone()
                    if existing_net_match and existing_net_match[0] and not existing_net_match[0].startswith("NO_MAC"):
                        gateway_mac = existing_net_match[0]

                if not gateway_mac:
                    gateway_mac = get_gateway_mac(gateway_ip)

            initial_was_placeholder = False
            if not gateway_mac:
                gateway_mac = f"NO_MAC_{int(time.time()*1000)}"
                initial_was_placeholder = True

            is_isolation = request.args.get('mode') == 'isolation'
            is_split = request.args.get('mode') == 'split'
            
            existing_states = {}
            final_network_comment = ""
            
            with sqlite3.connect(DB_NAME, timeout=10) as conn:
                c = conn.cursor()
                
                if is_isolation or is_split:
                    final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip}) - {'Isolated' if is_isolation else 'Split'}"
                    c.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip, allow_matching, comments) VALUES (?, ?, ?, ?, 0, '')", 
                              (gateway_mac, final_network_name, current_time, gateway_ip))
                    network_id = c.lastrowid
                else:
                    if is_continue and expected_net_id:
                        if force_merge:
                            c.execute("SELECT id, name, comments FROM networks WHERE id=?", (expected_net_id,))
                            row = c.fetchone()
                            if row:
                                network_id, final_network_name, final_network_comment = row[0], row[1], row[2] or ""
                                c.execute("UPDATE networks SET last_scan=?, gateway_mac=?, gateway_ip=? WHERE id=?", (current_time, gateway_mac, gateway_ip, network_id))
                            else:
                                network_id = expected_net_id
                                final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                        else:
                            c.execute("SELECT id, name, comments FROM networks WHERE id=?", (expected_net_id,))
                            row = c.fetchone()
                            if row:
                                network_id, final_network_name, final_network_comment = row[0], row[1], row[2] or ""
                                c.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, network_id))
                            else:
                                final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                                c.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip, allow_matching, comments) VALUES (?, ?, ?, ?, 1, '')", 
                                          (gateway_mac, final_network_name, current_time, gateway_ip))
                                network_id = c.lastrowid
                    else:
                        if initial_was_placeholder:
                            c.execute("SELECT id, name, comments FROM networks WHERE gateway_ip=? AND allow_matching=1 ORDER BY last_scan DESC LIMIT 1", (gateway_ip,))
                        else:
                            c.execute("SELECT id, name, comments FROM networks WHERE gateway_mac=? AND gateway_ip=? AND allow_matching=1 ORDER BY last_scan DESC LIMIT 1", (gateway_mac, gateway_ip))
                        
                        row = c.fetchone()
                        if row:
                            network_id, final_network_name, final_network_comment = row[0], row[1], row[2] or ""
                            c.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, network_id))
                        else:
                            c.execute("SELECT id FROM networks WHERE gateway_mac=? AND gateway_ip=?", (gateway_mac, gateway_ip))
                            allow_match_val = 0 if c.fetchone() else 1
                            final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                            c.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip, allow_matching, comments) VALUES (?, ?, ?, ?, ?, '')", 
                                      (gateway_mac, final_network_name, current_time, gateway_ip, allow_match_val))
                            network_id = c.lastrowid
                
                # Fetch IP addresses as well to support the Targeted Poke logic
                c.execute("SELECT mac_address, is_online, services, ip_address FROM devices WHERE network_id=?", (network_id,))
                for r in c.fetchall():
                    existing_states[r[0]] = {'online': r[1], 'services': r[2], 'ip': r[3]}
                    
                c.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))
                conn.commit()

            yield f"data: {json.dumps({'type': 'init', 'network_id': network_id, 'network_name': final_network_name, 'network_comment': final_network_comment})}\n\n"
            # KEEP ALIVE: Prevent browser SSE disconnects during heavy backend blocks
            yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

            try:
                scapy_iface = conf.route.route(target_ip_val)[0]
            except:
                scapy_iface = target_iface

            # --- 2. PERFORM SCAPY SCAN ---
            ans = []
            is_windows = platform.system() == "Windows"
            scan_timeout = 2.5 if is_windows else 1.5
            scan_inter = 0.01
            scan_retry = 2 if is_windows else 1

            try:
                ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                             timeout=scan_timeout, retry=scan_retry, verbose=0, inter=scan_inter, 
                             iface=scapy_iface, promisc=False)
            except Exception as e:
                print(f"[*] Scapy targeted bind failed: {e}. Retrying globally...")
                try:
                    ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                                 timeout=scan_timeout, retry=scan_retry, verbose=0, inter=scan_inter, promisc=False)
                except Exception as e2:
                    print(f"[*] Scapy global bind failed: {e2}")

            raw_candidates = [received for _, received in ans]
            network = ipaddress.IPv4Network(target_subnet, strict=False)

            class MockDev:
                def __init__(self, ip_found, mac_found): 
                    self.ip = ip_found
                    self.psrc = ip_found
                    self.mac = mac_found
                    self.hwsrc = mac_found

            def scrape_arp():
                devices = []
                try:
                    arp_out = subprocess.check_output(["arp", "-a"], text=True)
                    for match in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})', arp_out):
                        ip_found, raw_mac = match.groups()
                        mac_found = ':'.join([p.zfill(2) for p in raw_mac.replace('-', ':').split(':')]).lower()
                        if not mac_found.startswith('ff:ff') and not mac_found.startswith('01:00:5e') and not mac_found.startswith('33:33'):
                            try:
                                if ipaddress.IPv4Address(ip_found) in network:
                                    devices.append(MockDev(ip_found, mac_found))
                            except: pass
                except Exception as e:
                    print(f"[*] OS ARP Table Parsing Failed: {e}")
                return devices

            def force_discovery(ip_str):
                """Hybrid Layer-2 / Layer-3 Sweep to bypass strict Windows Firewalls"""
                sys_plat = platform.system().lower()
                if sys_plat == 'windows':
                    try:
                        import ctypes, socket
                        dest_ip = int.from_bytes(socket.inet_aton(ip_str), 'little')
                        mac_addr = (ctypes.c_ubyte * 6)()
                        mac_len = ctypes.c_ulong(6)
                        ctypes.windll.iphlpapi.SendARP(dest_ip, 0, ctypes.byref(mac_addr), ctypes.byref(mac_len))
                    except: pass
                    cmd = ['ping', '-n', '1', '-w', '250', ip_str]
                    try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except: pass
                elif sys_plat == 'darwin':
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.settimeout(0.1)
                            s.sendto(b'\x00', (ip_str, 5353))
                    except: pass
                    cmd = ['ping', '-c', '1', '-W', '250', ip_str]
                    try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except: pass
                else:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.settimeout(0.1)
                            s.sendto(b'\x00', (ip_str, 5353))
                    except: pass
                    cmd = ['ping', '-c', '1', '-W', '1', ip_str]
                    try: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except: pass

            yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
            raw_candidates.extend(scrape_arp())

            # --- 3. ROCK-SOLID OS HYBRID FALLBACK SWEEP ---
            found_ips = set(getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates)
            
            if is_windows or len(found_ips) <= 15:
                print("[*] Scan found few devices. Initiating OS Hybrid Fallback Sweep...")
                max_threads = 60 if is_windows else 255
                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    for ip_obj in network.hosts(): 
                        ip_str = str(ip_obj)
                        if ip_str not in found_ips:
                            executor.submit(force_discovery, ip_str)
                time.sleep(0.5) 
                raw_candidates.extend(scrape_arp())

            yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

            # --- 4. TARGETED HISTORICAL POKE ---
            if not is_isolation and not is_split:
                historical_targets = []
                found_macs = set(getattr(d, 'hwsrc', getattr(d, 'mac', '')) for d in raw_candidates)
                found_ips = set(getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates)
                
                for mac, state in existing_states.items():
                    if mac not in found_macs:
                        hist_ip = state.get('ip')
                        if hist_ip and hist_ip != "0.0.0.0" and hist_ip not in found_ips:
                            try:
                                if ipaddress.IPv4Address(hist_ip) in network:
                                    historical_targets.append(hist_ip)
                            except: pass

                if historical_targets:
                    print(f"[*] Targeted Poke: Giving {len(historical_targets)} historical devices a second chance at their last known IPs...")
                    max_threads = 60 if is_windows else 255
                    with ThreadPoolExecutor(max_workers=max_threads) as executor:
                        for ip in historical_targets:
                            executor.submit(force_discovery, ip)
                    time.sleep(0.5)
                    raw_candidates.extend(scrape_arp())

            yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

            # --- 5. DEDUPLICATE ALL CANDIDATES ---
            unique_candidates = {}
            for d in raw_candidates:
                ip = getattr(d, 'psrc', getattr(d, 'ip', None))
                if ip: unique_candidates[ip] = d
            raw_candidates = list(unique_candidates.values())

            # --- 6. DEFERRED ROUTER MAC RESOLUTION & POST-SCAN MATCHING ---
            if initial_was_placeholder and gateway_ip != "-" and gateway_ip != "Unknown":
                resolved_mac = None
                for d in raw_candidates:
                    if getattr(d, 'psrc', getattr(d, 'ip', '')) == gateway_ip:
                        resolved_mac = getattr(d, 'hwsrc', getattr(d, 'mac', None))
                        break
                if not resolved_mac:
                    resolved_mac = get_gateway_mac(gateway_ip)

                if resolved_mac and not resolved_mac.startswith("NO_MAC"):
                    gateway_mac = resolved_mac
                    final_network_name = f"Network {gateway_mac[-5:]} ({gateway_ip})"
                    
                    with sqlite3.connect(DB_NAME, timeout=10) as conn:
                        c = conn.cursor()
                        c.execute("SELECT id, name, comments FROM networks WHERE gateway_mac=? AND gateway_ip=? AND id!=?", (gateway_mac, gateway_ip, network_id))
                        existing_match = c.fetchone()
                        
                        if existing_match and not is_isolation and not is_split:
                            matched_net_id, final_network_name, final_network_comment = existing_match[0], existing_match[1], existing_match[2] or ""
                            
                            c.execute("UPDATE device_scans SET network_id=?, network_name=? WHERE network_id=?", (matched_net_id, final_network_name, network_id))
                            c.execute("UPDATE devices SET network_id=?, last_network_name=? WHERE network_id=?", (matched_net_id, final_network_name, network_id))
                            c.execute("UPDATE networks SET last_scan=? WHERE id=?", (current_time, matched_net_id))
                            c.execute("DELETE FROM networks WHERE id=?", (network_id,))
                            network_id = matched_net_id
                            
                            print(f"[*] Post-Scan Match: Merged temporary network into existing historical network ID {network_id}")
                        else:
                            c.execute("UPDATE networks SET gateway_mac=?, name=? WHERE id=?", (gateway_mac, final_network_name, network_id))
                            
                        conn.commit()

                    yield f"data: {json.dumps({'type': 'init', 'network_id': network_id, 'network_name': final_network_name, 'network_comment': final_network_comment})}\n\n"

            found_ips = [getattr(d, 'psrc', getattr(d, 'ip', '')) for d in raw_candidates]
            if gateway_ip not in found_ips and gateway_ip != "-" and gateway_ip != "Unknown":
                raw_candidates.append(MockDev(gateway_ip, gateway_mac))

            if target_ip_val not in found_ips and target_mac_val:
                raw_candidates.append(MockDev(target_ip_val, target_mac_val))

            # --- 7. PROCESS DEVICES STREAM ---
            tasks = []
            for d in raw_candidates:
                mac = getattr(d, 'hwsrc', getattr(d, 'mac', None))
                skip_services = False
                
                if is_continue and mac in existing_states and existing_states[mac]['online'] == 0:
                    skip_services = True
                
                tasks.append((d, skip_services, gateway_ip))

            with ThreadPoolExecutor(max_workers=worker_cfg['scan_workers']) as executor:
                future_to_dev = {executor.submit(process_device_quick, t): t[0] for t in tasks}
                
                for future in as_completed(future_to_dev):
                    try:
                        dev = future.result()
                        
                        if is_continue and dev["mac"] in existing_states and existing_states[dev["mac"]]["online"] == 0:
                            if existing_states[dev["mac"]]["services"]:
                                dev["services"] = existing_states[dev["mac"]]["services"]

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
                                    custom_name=COALESCE(NULLIF(?, ''), devices.custom_name),
                                    custom_vendor=COALESCE(NULLIF(?, ''), devices.custom_vendor),
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

            # --- 8. APPEND OFFLINE DEVICES ---
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

            yield f"data: {json.dumps({'type': 'complete', 'network_id': network_id, 'network_name': final_network_name, 'network_comment': final_network_comment, 'offline_devices': offline_devices})}\n\n"     

        except Exception as critical_err:
            print(f"[!!!] CRITICAL SCAN ERROR: {critical_err}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Server Error: {str(critical_err)}'})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

@app.route('/api/dns/lookup', methods=['POST'])
def dns_lookup():
    """
    Executes a DNS A-Record lookup for a provided domain name.
    Logs the result in the database alongside the active Network Context (Router IP, LAN IP, Network Name)
    so the user knows exactly which environment the resolution occurred in.
    """
    domain = request.json.get('domain', '').strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lan_ip, router_ip, net_name = get_current_network_context()

    # --- STRICT VALIDATION ---
    # Prevents command injection or database pollution from empty/malformed inputs
    if not domain or domain.startswith('-') or not re.match(r'^[\w\.-]+$', domain):
        return jsonify({"timestamp": ts, "domain": domain or "Invalid", "ip": "-", "status": "Failed"})

    try:
        # Uses the host OS's native DNS resolver to find the IP
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
    """Fetches the DNS lookup history. Limits to the 100 most recent records to prevent UI lag."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/dns/clear', methods=['POST'])
def clear_dns_logs():
    """Wipes the entire DNS history log from the database."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM dns_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/tool_logs/update', methods=['POST'])
def update_tool_log():
    """
    Shared endpoint to update user-defined metadata (network name and comments) 
    for a specific DNS or Ping log entry.
    """
    d = request.json or {}
    log_type = d.get('type')
    item_id = d.get('id')
    new_name = str(d.get('name', '')).strip()[:50] 
    new_comment = str(d.get('comment', '')).strip()[:200] 
    
    if log_type not in ['dns', 'ping']:
        return jsonify({"error": "Invalid log type"}), 400
        
    table = 'dns_logs' if log_type == 'dns' else 'ping_logs'
    
    try:
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            conn.execute(f"UPDATE {table} SET network_name = ?, comments = ? WHERE id = ?", (new_name, new_comment, item_id))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ping/run', methods=['POST'])
def run_ping():
    """
    Executes a native OS ICMP Ping (4 packets) against a target IP or domain.
    Parses the terminal output using OS-specific Regex to extract Latency and Packet Loss.
    Logs the result with the active Network Context (Router IP, LAN IP, Network Name).
    """
    target = request.json.get('target', '').strip()
    
    # --- STRICT VALIDATION ---
    # Highly critical to prevent OS command injection since the target is passed to a subprocess shell
    if not target or target.startswith('-') or not re.match(r'^[\w\.-]+$', target):
        return jsonify({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target": target or "Invalid",
            "status": "Failed",
            "latency": "N/A",
            "loss": "100%",
            "error": "Invalid target format"
        })
    
    # Determine OS-specific ping command arguments
    # Windows uses '-n' for packet count, Unix/Mac uses '-c'
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    cmd = ['ping', param, '4', target]
    
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latency = "N/A"
    loss = "100%"
    status = "Failed"
    
    # --- GET CONTEXT (Router, Network Name, LAN IP) ---
    lan_ip, router_ip, net_name = get_current_network_context()

    try:
        # Run the ping command and capture output
        # stderr=subprocess.STDOUT ensures we capture terminal errors like "Host unreachable" in the main output block
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        
        # Parse output for Latency and Packet Loss based on OS formatting
        if platform.system().lower() == 'windows':
            # Windows Output format: "Average = 24ms", "Lost = 0 (0% loss)"
            if "Average =" in output:
                latency = output.split("Average =")[1].strip().replace("ms", "").strip() + " ms"
            if "Lost =" in output:
                loss_part = output.split("Lost =")[1].split("(")[1]
                loss = loss_part.split(")")[0] # Extracts just the percentage, e.g., "0% loss"
        else:
            # Linux / macOS Output format: "min/avg/max = ...", "0% packet loss"
            if "avg" in output: 
                latency = output.split(" = ")[1].split("/")[1] + " ms"
            if "packet loss" in output:
                loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', output)
                if loss_match: 
                    loss = loss_match.group(1) + "%"
        
        # Determine simple Status indicator for the UI
        if "0%" in loss or "0.0%" in loss: 
            status = "Success"
        elif "100%" in loss: 
            status = "Failed"
        else:
            status = "Partial"

    except subprocess.CalledProcessError:
        pass # Ping command returned non-zero exit code (e.g., Host Unreachable / Timeout)
    
    # Log to Database
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
    """Fetches the Ping history. Limits to the 100 most recent records to prevent UI lag."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/ping/clear', methods=['POST'])
def clear_ping_logs():
    """Wipes the entire Ping history log from the database."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM ping_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/export/tool_logs', methods=['POST'])
def export_tool_logs():
    """
    Exports DNS or Ping logs to a downloadable CSV file.
    Uses io.StringIO to generate the CSV entirely in RAM, skipping disk writes.
    """
    log_type = request.json.get('type')
    out = io.StringIO()
    writer = csv.writer(out)
    
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        if log_type == 'dns':
            rows = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Domain', 'Result IP', 'Status', 'Router IP', 'Network', 'LAN IP', 'Comments'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['domain'], r['result_ip'], r['status'], r['router_ip'], r['network_name'], r['lan_ip'], r['comments']])
        else:
            rows = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Target', 'Status', 'Latency', 'Loss', 'Router IP', 'Network', 'LAN IP', 'Comments'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['target'], r['status'], r['latency'], r['packet_loss'], r['router_ip'], r['network_name'], r['lan_ip'], r['comments']])
            
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": f"attachment; filename={log_type}_logs.csv"})

@app.route('/api/system/alerts', methods=['GET'])
def get_system_alerts():
    """
    Retrieves active system alerts (e.g., Disk Space Low, Update Failed) from the JSON file.
    These are rendered as persistent red banner warnings at the top of the UI.
    """
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                return jsonify(json.load(f))
    except: pass
    return jsonify([])

@app.route('/api/system/alerts/dismiss', methods=['POST'])
def dismiss_system_alert():
    """
    Dismisses a specific system alert by removing its exact string from the JSON file array.
    """
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

def get_visible_wifi_interfaces():
    """
    Identifies all physical Wi-Fi adapters available on the host machine.
    - Employs OS-specific commands because standard Python socket libraries cannot easily 
      differentiate Wi-Fi from Ethernet.
    - Caches the raw interface list globally to prevent the UI dropdown from lagging.
    - Cross-references the detected hardware against the SQLite database to explicitly 
      filter out any adapters the user has marked as 'hidden' in the settings.
    """
    global OS_CACHE
    raw_ifaces = []
    sys_plat = platform.system()
    
    if time.time() - OS_CACHE["wifi_ifaces"]["time"] < CACHE_TTL:
        raw_ifaces = OS_CACHE["wifi_ifaces"]["data"]
    else:
        try:
            if sys_plat == "Windows":
                # Windows: 'netsh' is the most reliable native utility for Wi-Fi hardware
                out = subprocess.check_output("netsh wlan show interfaces", shell=True, text=True, encoding='cp437', errors='ignore')
                for line in out.split('\n'):
                    if "Name" in line and ":" in line:
                        raw_ifaces.append(line.split(":", 1)[1].strip())
            elif sys_plat == "Linux":
                # Linux: NetworkManager CLI provides a clean interface listing
                out = subprocess.check_output(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"], text=True)
                for line in out.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2 and parts[1] == "wifi":
                        raw_ifaces.append(parts[0])
            elif sys_plat == "Darwin":
                # macOS: CoreWLAN natively exposes Wi-Fi interfaces directly to Python via pyobjc
                import CoreWLAN
                client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
                interfaces = client.interfaces()
                if interfaces:
                    for i in interfaces:
                        raw_ifaces.append(i.interfaceName())
        except Exception as e:
            pass
            
        OS_CACHE["wifi_ifaces"]["data"] = raw_ifaces
        OS_CACHE["wifi_ifaces"]["time"] = time.time()
        
    final_ifaces = []
    settings_by_mac = {}
    settings_by_name = {}
    
    # Load user visibility preferences from the database
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            for row in conn.execute("SELECT mac_address, custom_name, is_visible FROM adapter_settings"):
                settings_by_mac[row[0]] = {"name": row[1], "visible": row[2]}
                settings_by_name[row[1]] = {"name": row[1], "visible": row[2]} 
    except: pass

    try: net_ifaces = psutil.net_if_addrs()
    except: net_ifaces = {}

    for iface in raw_ifaces:
        mac = "-"
        # Map the OS interface string back to its physical MAC address
        if iface in net_ifaces:
            for a in net_ifaces[iface]:
                if a.family == psutil.AF_LINK:
                    mac = a.address
                    break
                    
        custom_name = iface
        is_visible = 1
        
        # Apply the user's custom name and check if they hid it
        if mac != "-" and mac in settings_by_mac:
            if settings_by_mac[mac]["name"]: custom_name = settings_by_mac[mac]["name"]
            is_visible = settings_by_mac[mac]["visible"]
        elif iface in settings_by_name:
            if settings_by_name[iface]["name"]: custom_name = settings_by_name[iface]["name"]
            is_visible = settings_by_name[iface]["visible"]
            
        # Only append it to the dropdown list if it hasn't been hidden!
        if is_visible:
            final_ifaces.append({"id": iface, "name": custom_name})
            
    return final_ifaces

@app.route('/api/wifi/interfaces')
def api_wifi_interfaces():
    """Returns the visible interfaces to populate the dashboard's Wi-Fi scanner dropdown."""
    return jsonify(get_visible_wifi_interfaces())

def merge_wifi_results(existing_list, new_list):
    """
    Deep merges two Wi-Fi scan results for the 'Continue Scan' feature.
    - Consolidates multiple hardware MACs (BSSIDs) under a single Network Name (SSID) to support Mesh Wi-Fi.
    - If a BSSID appears in both scans, it preserves the strongest signal strength (dBm) found.
    """
    merged = {}
    for net in existing_list + new_list:
        ssid = net.get('ssid', 'Unknown')
        if ssid not in merged:
            merged[ssid] = {"ssid": ssid, "auth": net.get('auth', 'Unknown'), "bssids": {}}
        
        # Update Authentication string if the new scan provides a better/known format
        if net.get('auth') and net.get('auth') != 'Unknown':
            merged[ssid]['auth'] = net.get('auth')
            
        for b in net.get('raw_bssids', []):
            mac = b.get('mac')
            if mac not in merged[ssid]['bssids']:
                merged[ssid]['bssids'][mac] = b
            else:
                existing_b = merged[ssid]['bssids'][mac]
                # Compare and merge DBms (Keep the strongest signal detected across both scans)
                new_dbm = b.get('dbm')
                old_dbm = existing_b.get('dbm')
                if new_dbm is not None:
                    if old_dbm is None or new_dbm > old_dbm:
                        existing_b['dbm'] = new_dbm
                        existing_b['percent'] = b.get('percent')
                        
                # Update channel and band if the new data is more valid
                if b.get('channel') and str(b['channel']) not in ['0', '', '-']:
                    existing_b['channel'] = b['channel']
                if b.get('band') and b.get('band') not in ['Unknown', '', '-']:
                    existing_b['band'] = b['band']
                    
    # Rebuild the final list formatted exactly as the UI expects it
    result = []
    for ssid, data in merged.items():
        raw_b = list(data['bssids'].values())
        best_ch = "0"
        for b in raw_b:
            if b.get('channel') and str(b['channel']) not in ['0', '', '-']:
                best_ch = b['channel']
                break # Just grab the first valid channel for the summary UI tag
        result.append({
            "ssid": ssid,
            "auth": data['auth'],
            "channel": best_ch,
            "raw_bssids": raw_b
        })
    return result

@app.route('/api/wifi')
def get_wifi_networks():
    """
    Cross-Platform Wi-Fi Discovery Engine.
    Returns detailed Wi-Fi data grouped by SSID. 
    Because Wi-Fi APIs are deeply rooted in OS drivers, this requires three completely different 
    strategies depending on the host machine.
    """
    networks_dict = {}
    sys_plat = platform.system()
    req_iface = request.args.get('iface')
    mode = request.args.get('mode', 'new')
    scan_id = request.args.get('scan_id')
    
    target_ifaces = []
    if req_iface:
        target_ifaces = [req_iface]
    else:
        visible_ifaces = get_visible_wifi_interfaces()
        if not visible_ifaces:
            return jsonify({"error": "No Wi-Fi Adapters", "message": "Could not find any visible Wi-Fi adapters to scan with."})
        target_ifaces = [i["id"] for i in visible_ifaces]
    
    try:
        # ==========================================
        # 1. macOS Implementation (CoreWLAN)
        # ==========================================
        if sys_plat == "Darwin":
            try:
                try:
                    # In modern macOS, Location Services must explicitly be requested right before scanning
                    import CoreLocation
                    loc_manager = CoreLocation.CLLocationManager.alloc().init()
                    loc_manager.requestWhenInUseAuthorization()
                except Exception as e:
                    print(f"CoreLocation trigger error: {e}")
                    
                import CoreWLAN
                
                for target_iface in target_ifaces:
                    wifi_interface = CoreWLAN.CWInterface.interfaceWithName_(target_iface)
                    if not wifi_interface: continue
                    
                    # Force an active scan and merge with the OS cache
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
                            
                            # Extract hardware signal strength metrics
                            dbm_val = int(i.rssiValue()) if i.rssiValue() else None
                            pct_val = max(0, min(100, int((dbm_val + 100) * 2))) if dbm_val is not None else None
                            
                            ch_obj = i.wlanChannel()
                            ch = str(ch_obj.channelNumber()) if ch_obj else "0"
                            
                            # Determine Frequency Band (2.4/5/6 GHz) based on Apple's native ENUM or the raw channel ID
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
            # Force the physical adapter to restart its internal cache before scanning
            iface_list_str = ",".join([f"'{i}'" for i in target_ifaces])
            subprocess.run(["powershell", "-Command", f"Get-NetAdapter -Name {iface_list_str} | Restart-NetAdapter"], capture_output=True)
            time.sleep(4) 
            
            for target_iface in target_ifaces:
                # Mode=bssid requests hardware-level details for Mesh Networks rather than a simple summarized list
                cmd = f'netsh wlan show networks interface="{target_iface}" mode=bssid'

                # Retry loop: Netsh sometimes fails to print the 'Signal' percentage on the first pass
                for attempt in range(2):
                    process = subprocess.Popen(
                        cmd, 
                        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    out_bytes, _ = process.communicate(timeout=15)

                    try:
                        stdout = out_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # Windows terminal defaults to CP437 on older systems, which throws decode errors
                        stdout = out_bytes.decode('cp437', errors='ignore')

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
                                raw_mac_line = line.split(":", 1)[1].strip()
                                current_mac = raw_mac_line.split(",")[0].strip() if raw_mac_line else f"Unknown_MAC_{time.time()}"
                                
                                if current_mac not in networks_dict[current_ssid]["bssids"]:
                                    networks_dict[current_ssid]["bssids"][current_mac] = {"dbm": None, "percent": None, "channel": "0", "band": "Unknown"}
                                    
                                if "," in raw_mac_line:
                                    for part in raw_mac_line.split(",")[1:]:
                                        part = part.strip().lower()
                                        if part.startswith("band"):
                                            raw_band = part.split(":", 1)[-1].strip().replace(" ", "")
                                            if "2.4" in raw_band: b = "2.4GHz"
                                            elif "5" in raw_band: b = "5GHz"
                                            elif "6" in raw_band: b = "6GHz"
                                            else: b = raw_band
                                            networks_dict[current_ssid]["bssids"][current_mac]["band"] = b
                                        elif part.startswith("channel"):
                                            ch = part.split(":", 1)[-1].strip()
                                            if re.match(r"^\d{1,3}$", ch):
                                                networks_dict[current_ssid]["bssids"][current_mac]["channel"] = ch
                                        elif part.startswith("signal"):
                                            raw_sig = part.split(":", 1)[-1].strip()
                                            try:
                                                pct_val = int(''.join(filter(str.isdigit, raw_sig)))
                                                networks_dict[current_ssid]["bssids"][current_mac]["percent"] = pct_val
                                                networks_dict[current_ssid]["bssids"][current_mac]["dbm"] = int((pct_val / 2) - 100)
                                            except ValueError: pass
                            
                            # Parse legacy signal percentage outputs
                            elif current_mac and "signal" in line.lower():
                                raw_sig = line.split(":", 1)[1].strip()
                                try:
                                    pct_val = int(''.join(filter(str.isdigit, raw_sig)))
                                    networks_dict[current_ssid]["bssids"][current_mac]["percent"] = pct_val
                                    # Convert Windows Signal % to rough standard dBm approximation
                                    networks_dict[current_ssid]["bssids"][current_mac]["dbm"] = int((pct_val / 2) - 100)
                                except ValueError: pass
                            
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

                    # If Netsh failed to supply signal strengths, we loop and try the terminal command one more time.
                    missing_signals = any(
                        b_data["percent"] is None 
                        for net in networks_dict.values() 
                        for b_data in net["bssids"].values()
                    )
                    if not missing_signals or attempt == 1:
                        break
                    time.sleep(2)

        # ==========================================
        # 3. Linux Implementation (nmcli)
        # ==========================================
        elif sys_plat == "Linux":
            # Force NetworkManager to actively rescan the physical environment
            for target_iface in target_ifaces:
                subprocess.run(["nmcli", "dev", "wifi", "rescan", "ifname", target_iface], capture_output=True)
            time.sleep(2) 
            
            for target_iface in target_ifaces:
                # Provides a highly structured colon-separated output
                cmd = ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,FREQ,SECURITY", "dev", "wifi", "list", "ifname", target_iface]
                try:
                    output = subprocess.check_output(cmd, text=True)
                    for line in output.strip().split('\n'):
                        # Safely split by colons, ignoring any colons that were escaped with backslashes
                        parts = re.split(r'(?<!\\):', line)
                        parts = [p.replace('\\:', ':') for p in parts]
                        
                        if len(parts) >= 6:
                            ssid = parts[0] or "Hidden Network"
                            mac = parts[1].strip() if parts[1] else f"Unknown_MAC_{time.time()}"
                            
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
                            
                            # Calculate the Band based on the exact frequency range (MHz)
                            if 2400 <= freq <= 2500: b = "2.4GHz"
                            elif 5150 <= freq <= 5895: b = "5GHz"
                            elif freq >= 5925: b = "6GHz"
                            else: b = "Unknown"

                            if ssid not in networks_dict:
                                networks_dict[ssid] = {"ssid": ssid, "auth": parts[5], "bssids": {}}
                            
                            if parts[5] != "Unknown" and networks_dict[ssid]["auth"] == "Unknown":
                                networks_dict[ssid]["auth"] = parts[5]
                                
                            networks_dict[ssid]["bssids"][mac] = {"dbm": dbm_val, "percent": pct_val, "channel": ch, "band": b}
                except: pass

        # ==========================================
        # 4. Final Formatting & Merge Logic
        # ==========================================
        # Converts the nested python dictionaries into a flat list structure expected by the frontend
        final_networks = []
        for ssid, net_data in networks_dict.items():
            raw_b = []
            best_ch = "0"
            best_dbm = -1000
            for mac, b_data in net_data["bssids"].items():
                raw_b.append({
                    "mac": mac, "dbm": b_data["dbm"], "percent": b_data["percent"],
                    "channel": b_data["channel"], "band": b_data["band"]
                })
                # Determine which specific access point has the strongest signal to represent the whole Mesh
                curr_dbm = b_data["dbm"] if b_data["dbm"] is not None else -1000
                if curr_dbm > best_dbm:
                    best_dbm = curr_dbm
                    best_ch = b_data["channel"]
            
            final_networks.append({
                "ssid": ssid, "auth": net_data["auth"], "channel": best_ch, "raw_bssids": raw_b
            })

        # Save the finalized scan to Database, providing Support for the 'Continue Scan' UI feature
        try:
            with sqlite3.connect(DB_NAME, timeout=10) as conn:
                c = conn.cursor()
                
                # If continuing an existing scan, pull the old JSON out, merge it, and push it back in
                if mode == 'continue' and scan_id:
                    c.execute("SELECT results_json, scan_name, comments FROM wifi_history WHERE id=?", (scan_id,))
                    row = c.fetchone()
                    if row:
                        existing_nets = json.loads(row[0])
                        final_networks = merge_wifi_results(existing_nets, final_networks)
                        
                        c.execute("UPDATE wifi_history SET results_json=? WHERE id=?", (json.dumps(final_networks), scan_id))
                        conn.commit()
                        
                        # Attach Global Comments to the UI Data dynamically so they persist across sessions
                        for net in final_networks:
                            try:
                                g_com = c.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (net['ssid'],)).fetchone()
                                net['global_comment'] = g_com[0] if g_com else ""
                            except sqlite3.OperationalError:
                                net['global_comment'] = ""
                            
                        return jsonify({"scan_id": scan_id, "scan_name": row[1], "scan_comment": row[2] or "", "networks": final_networks})
                        
                # Fallback or New Scan: Create a completely fresh database entry
                scan_name = f"Scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                scan_comment = ""
                c.execute("INSERT INTO wifi_history (scan_name, comments, results_json) VALUES (?, ?, ?)", 
                          (scan_name, scan_comment, json.dumps(final_networks)))
                conn.commit()
                scan_id_new = c.lastrowid
                
                # Attach Global Comments to the UI Data
                for net in final_networks:
                    try:
                        g_com = c.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (net['ssid'],)).fetchone()
                        net['global_comment'] = g_com[0] if g_com else ""
                    except sqlite3.OperationalError:
                        net['global_comment'] = ""
                    
                return jsonify({"scan_id": scan_id_new, "scan_name": scan_name, "scan_comment": scan_comment, "networks": final_networks})
                
        except Exception as e:
            return jsonify({"error": "Database Save Error", "message": str(e)})

    except Exception as e: 
        return jsonify({"error": "Critical Error", "message": str(e)})

@app.route('/api/wifi/merge', methods=['POST'])
def merge_wifi_scans():
    """
    Management Tool: Merges multiple separate Wi-Fi history scans into a single target scan.
    Soft deletes the old scans by setting is_deleted=1, preserving the database integrity.
    """
    d = request.json
    target_id = d.get('target_id')
    source_ids = d.get('source_ids', [])
    
    if not target_id or not source_ids:
        return jsonify({"error": "Invalid parameters"}), 400
        
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            target_row = c.execute("SELECT results_json FROM wifi_history WHERE id=?", (target_id,)).fetchone()
            if not target_row: return jsonify({"error": "Target scan not found."}), 404
            
            target_nets = json.loads(target_row['results_json'])
            
            for src_id in source_ids:
                if str(src_id) == str(target_id): continue
                src_row = c.execute("SELECT results_json FROM wifi_history WHERE id=?", (src_id,)).fetchone()
                if src_row:
                    src_nets = json.loads(src_row['results_json'])
                    target_nets = merge_wifi_results(target_nets, src_nets) # Re-use the smart helper
                
                # Soft delete the old source scan
                c.execute("UPDATE wifi_history SET is_deleted=1 WHERE id=?", (src_id,))
                
            c.execute("UPDATE wifi_history SET results_json=? WHERE id=?", (json.dumps(target_nets), target_id))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/wifi/history/<int:scan_id>', methods=['GET'])
def load_wifi_scan(scan_id):
    """
    Loads a specific Wi-Fi scan from the database history, parses its JSON structure,
    and dynamically overlays the user's global SSID comments.
    """
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT results_json, scan_name, comments FROM wifi_history WHERE id = ?", (scan_id,))
        row = c.fetchone()
        
        if row:
            results = json.loads(row[0])
            
            # Attach Global Comments to the UI Data dynamically
            for net in results:
                try:
                    g_com = c.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (net['ssid'],)).fetchone()
                    net['global_comment'] = g_com[0] if g_com else ""
                except sqlite3.OperationalError:
                    net['global_comment'] = ""
                
            conn.close()
            return jsonify({"results": results, "name": row[1], "comments": row[2] or ""})
            
        conn.close()
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    """
    Executes an Ookla Speedtest via the command-line interface.
    - Windows: Uses --ip bound to the specific local interface IP.
    - macOS/Linux: Uses --interface bound to the hardware name (e.g., en0).
    - Robustness: If the official C++ CLI binary fails (due to OS driver permission blocks or missing libraries), 
      it automatically falls back to a pure-Python library implementation (limited to ~1Gbps) 
      and notifies the user via the UI Alert banner.
    """
    d = request.json
    target_iface_name = None
    device_ip = "-" 
    pinned_mac = None

    # 1. Identify the Target Adapter and its IP
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
            
            # Fetch hidden interfaces so we don't accidentally test on an adapter the user disabled
            hidden_rows = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_visible = 0").fetchall()
            hidden_macs = [r[0] for r in hidden_rows]
            
            interfaces = psutil.net_if_addrs()
            
            for name, addrs in interfaces.items():
                temp_mac, temp_ip = None, None
                for a in addrs:
                    if a.family == psutil.AF_LINK: temp_mac = a.address
                    if a.family == socket.AF_INET: temp_ip = a.address
                
                # Skip this loop iteration completely if hidden
                if temp_mac in hidden_macs:
                    continue
                
                # If pinned, match by MAC; otherwise, find the active one serving the default route
                if pinned_mac and temp_mac == pinned_mac:
                    target_iface_name = name
                    device_ip = temp_ip
                    break
                elif not pinned_mac and temp_ip == get_local_ip():
                    target_iface_name = name
                    device_ip = temp_ip
    except Exception as e:
        print(f"[*] Speedtest adapter lookup failed: {e}")

    # Block the speedtest if no visible interface is found to prevent it from testing standard loopbacks
    if not target_iface_name and not pinned_mac:
        return jsonify({"error": "No usable or visible network adapter found."})

    # --- ENHANCED HELPER TO PARSE OOKLA OUTPUT ---
    def parse_ookla(raw_text):
        """
        Safely extracts JSON from the CLI output. 
        Occasionally, Ookla's binary prints plain-text connection warnings *before* 
        printing the final JSON response, which crashes standard json.loads().
        """
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as parse_err:
            print(f"\n[!] OOKLA JSON PARSE WARNING: {parse_err}")
            # Scan backwards line-by-line looking for the valid JSON object
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

    # --- PURE PYTHON FALLBACK MECHANISM ---
    def execute_fallback_speedtest():
        """
        Triggered if the official CLI is missing, lacks execution rights, or is blocked by an OS-level firewall.
        Downloads and runs the 'speedtest-cli' python module directly into RAM.
        """
        print("[*] Initiating Python fallback speedtest (Note: Speeds may be limited to ~1Gbps)...")
        try:
            import speedtest
        except ImportError:
            print("[*] Installing Python speedtest-cli module...")
            # Silently install the missing module into the virtual environment on the fly
            subprocess.check_call([sys.executable, "-m", "pip", "install", "speedtest-cli"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import speedtest
            
        try:
            # Bind to specific IP if pinned in the UI
            if device_ip and device_ip != "-" and device_ip != "127.0.0.1":
                st = speedtest.Speedtest(source_address=device_ip)
            else:
                st = speedtest.Speedtest()
                
            st.get_best_server()
            st.download()
            st.upload(pre_allocate=False)
            res = st.results.dict()
            
            down = f"{(res['download']) / 1_000_000:.2f} Mbps"
            up = f"{(res['upload']) / 1_000_000:.2f} Mbps"
            ping = f"{res['ping']:.2f} ms"
            isp = res.get('client', {}).get('isp', 'Unknown')
            wan = res.get('client', {}).get('ip', '-')
            
            return down, up, ping, isp, wan
        except Exception as e:
            print(f"[!] Fallback speedtest failed: {e}")
            raise e

    try:
        # 2. Identify the Path to the officially downloaded CLI Binary
        base_dir = app.root_path 
        st_path = os.path.join(base_dir, "venv", "Scripts", "speedtest.exe") if platform.system() == "Windows" else os.path.join(base_dir, "venv", "bin", "speedtest")
        cmd_path = st_path if os.path.exists(st_path) else "speedtest"
        
        # 3. Build base execution command
        base_cmd = [cmd_path, "--format=json", "--accept-license", "--accept-gdpr"]
        cmd = list(base_cmd)
        
        # 4. Apply OS-Specific Interface Binding
        if platform.system() == "Windows":
            if device_ip and device_ip != "-":
                cmd.extend(["--ip", device_ip])
        else:
            if target_iface_name:
                cmd.extend(["--interface", target_iface_name])
        
        # 5. Execute with Global Routing Fallback Logic
        try:
            raw_out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            res = parse_ookla(raw_out)
        except subprocess.CalledProcessError as e:
            # If the user pinned an adapter, we MUST respect it. Jump straight to the Python fallback.
            if pinned_mac:
                raise e 
                
            # If no pin is set, try removing the strict interface bindings and letting the OS route the traffic naturally
            print(f"[*] Speedtest strict bind failed (Exit {e.returncode}). Retrying globally...")
            raw_out = subprocess.check_output(base_cmd, stderr=subprocess.STDOUT, text=True)
            res = parse_ookla(raw_out)
            device_ip = get_local_ip()
            
        # 6. Check for internal JSON errors generated by the Ookla binary
        if "error" in res:
            raise Exception(f"Ookla Internal Error: {res.get('error')}")
        
        # 7. Format Results (Converting raw bytes to Mbps)
        down = f"{(res['download']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        up = f"{(res['upload']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', '-')

    except Exception as e:
        print(f"\n[!] OFFICIAL OOKLA CLI FAILED: {str(e)}")
        print("[*] Attempting to fall back to alternative speed test...")
        
        # --- DETERMINE EXACT CAUSE FOR USER UI NOTIFICATION ---
        err_str = str(e).lower()
        fallback_reason = "Official Speedtest CLI encountered an unexpected error."
        
        if isinstance(e, FileNotFoundError) or "no such file" in err_str or "not found" in err_str:
            fallback_reason = "Speedtest CLI binary is missing or incompatible."
        elif isinstance(e, PermissionError) or "permission denied" in err_str:
            fallback_reason = "The Operating System blocked the Official Speedtest CLI (Permission Denied)."
        elif "ookla internal error" in err_str:
            fallback_reason = "Ookla servers rejected the connection."
        elif hasattr(e, 'returncode'):
            out_str = str(getattr(e, 'output', '')).lower()
            if "configuration" in out_str or "cannot retrieve" in out_str:
                fallback_reason = "Ookla failed to retrieve server configuration."
            elif "network unreachable" in out_str:
                fallback_reason = "Ookla reported the network is unreachable."
            else:
                fallback_reason = f"Speedtest CLI crashed (Exit Code {e.returncode})."
        
        try:
            # 8. Trigger Alternative Python Speedtest
            down, up, ping, isp, wan = execute_fallback_speedtest()
            
            # Trigger the UI Alert Warning safely using the built-in JSON alert system
            warning_msg = f"Speedtest Fallback Active: {fallback_reason} Using secondary tester. Note: Maximum detected speeds may be limited to ~1Gbps."
            add_system_alert(warning_msg)
            print(f"[*] {warning_msg}")
            
        except Exception as fallback_err:
            print(f"\n[!] FALLBACK SPEEDTEST ALSO FAILED: {fallback_err}\n")
            if pinned_mac:
                return jsonify({"error": "Speed Test not able to complete via pinned adapter. Either unpin adapter or try again later."})
            return jsonify({"error": "Speedtest Failed. Check internet connection or retry later."})
            
    # 9. Save Results to Database
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

@app.route('/api/get_last_name')
def get_last_name():
    """
    Quality-of-Life feature for Speedtests.
    Checks the user's current Public WAN IP against the history database. 
    If they have tested here before, it auto-fills the 'Network Name' input box with their previous label.
    """
    info = get_isp_info()
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        row = conn.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (info['ip'],)).fetchone()
        return jsonify({"last_name": row[0] if row else "", "wan_ip": info['ip'], "isp": info['isp']})

@app.route('/api/history')
def get_history():
    """Fetches the Speedtest history. Uses sqlite3.Row to automatically map columns to JSON dictionaries."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row  # THIS IS KEY: Allows dict(row) conversion
        cursor = conn.execute("SELECT * FROM history ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        return jsonify([dict(ix) for ix in rows])

@app.route('/api/history/update', methods=['POST'])
def update_history():
    """Renames a history entry and updates its connection type (e.g., Wi-Fi vs Ethernet)."""
    d = request.json
    name = str(d.get('name', '')).strip()[:50]
    c_type = str(d.get('type', '')).strip()[:50]
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE history SET network_name = ?, connection_type = ? WHERE id = ?", 
                     (name, c_type, d.get('id'))) 
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/bulk_delete', methods=['POST'])
def bulk_delete():
    """
    Dynamic Bulk Deletion Engine.
    Takes an array of IDs and a target table type from the UI, and deletes them all in a single 
    query using an IN clause. This is significantly faster than looping through individual DELETE queries.
    """
    d = request.json
    table_map = {'networks': 'networks', 'wifi': 'wifi_history', 'history': 'history', 'dns': 'dns_logs', 'ping': 'ping_logs', 'devices': 'devices'}
    table = table_map.get(d.get('type'))
    ids = d.get('ids', [])
    
    if not table or not ids: return jsonify({"error": "Invalid parameters"}), 400
    
    # Generate the appropriate number of '?' placeholders for the SQL statement safely
    placeholders = ','.join(['?'] * len(ids))
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        if table == 'devices':
            # Purge the device entirely from the system, including user-defined global overrides
            conn.execute(f"DELETE FROM devices WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM global_device_names WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM global_device_vendors WHERE mac_address IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM device_scans WHERE mac_address IN ({placeholders})", ids)
        elif table == 'wifi_history':
            # Soft-delete for Wi-Fi scans so the data isn't permanently lost until maintenance is run
            conn.execute(f"UPDATE wifi_history SET is_deleted=1 WHERE id IN ({placeholders})", ids)
        else:
            conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/bulk_export', methods=['POST'])
def bulk_export_networks():
    """
    Generates a ZIP file containing multiple CSVs for selected Network Profiles.
    Uses io.BytesIO to generate the ZIP file entirely in RAM, preventing the need to clutter 
    the local disk with temporary files.
    """
    ids = request.json.get('ids', [])
    if not ids: return jsonify({"error": "No IDs provided"}), 400
    
    PORT_MAP = {"SSH": "22", "HTTP": "80", "HTTPS": "443", "HTTP (8080)": "8080", "HTTPS (8443)": "8443", "Flask/UPnP": "5000", "Portainer/Admin": "9000"}
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            for net_id in ids:
                net = conn.execute("SELECT name FROM networks WHERE id=?", (net_id,)).fetchone()
                if not net: continue
                
                net_name = get_safe_filename(net['name'])
                
                # Fetch devices with the global comments joined dynamically
                devices = conn.execute("""
                    SELECT d.hostname, d.custom_name, d.ip_address, d.previous_ip, d.discovery_status, 
                           d.mac_address, d.is_online, d.services, g.comments
                    FROM devices d
                    LEFT JOIN global_device_names g ON d.mac_address = g.mac_address
                    WHERE d.network_id=?
                """, (net_id,)).fetchall()
                
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                writer.writerow(['Hostname', 'Custom Name', 'IP Address', 'MAC Address', 'Status', 'Services (Ports)', 'History', 'Comments'])
                
                for d in devices:
                    history_text = d['discovery_status']
                    if d['previous_ip']: history_text = f"IP Changed ({d['previous_ip']})"
                    
                    raw_services = d['services'] or "None"
                    port_str = "None" if raw_services == "None" else ", ".join([PORT_MAP.get(s.strip(), s.strip()) for s in raw_services.split(",")])
                    
                    writer.writerow([d['hostname'], d['custom_name'], d['ip_address'], d['mac_address'], 'Online' if d['is_online'] else 'Offline', port_str, history_text, d['comments'] or ""])

                # Write the CSV buffer as a physical file entry into the virtual RAM ZIP file
                zf.writestr(f"network_{net_id}_{net_name}.csv", csv_out.getvalue())
    
    memory_file.seek(0)
    return send_file(memory_file, download_name="networks_bulk_export.zip", as_attachment=True)

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
    Exports Speedtest history to CSV.
    Can selectively export specific rows passed from the UI, or export the entire table
    if no specific rows are provided.
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
    
    # Write Header Row
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
            r.get('device_ip', '-'), 
            r.get('wan_ip', '-'),    
            r.get('isp', '-')
        ])
    
    return Response(
        out.getvalue(), 
        mimetype="text/csv", 
        headers={"Content-disposition": "attachment; filename=history.csv"}
    )

@app.route('/api/devices/export', methods=['POST'])
def export_devices():
    """
    Highly generic CSV exporter. Takes the raw JSON dictionary passed from the frontend UI
    table, extracts the keys to build the header, and maps the values dynamically.
    """
    d = request.json
    rows = d.get('rows', [])
    out = io.StringIO()
    writer = csv.writer(out)
    
    # Check if we have data
    if rows:
        # 1. Write Header Row (using keys from the first item, e.g., "Hostname", "IP Address")
        headers = list(rows[0].keys())
        writer.writerow(headers)
        
        # 2. Write Data Rows (mapping values strictly to the headers)
        for r in rows: 
            writer.writerow([r.get(h) for h in headers])
            
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=devices.csv"})

@app.route('/api/system/export_db')
def export_database():
    """Downloads the entire raw SQLite database file directly."""
    return send_file(DB_NAME, as_attachment=True)

@app.route('/api/system/import_db', methods=['POST'])
def import_database():
    """
    Complex Database Merge Engine.
    Allows users to import a database from another machine and merge it into their active one.
    - Validates file integrity to prevent server crashes from uploaded malware/images.
    - Maps Foreign Network IDs to Local Network IDs so devices don't get mixed up.
    - Updates 'last_seen' dates without overwriting local custom names.
    - Safely appends missing tool logs (Speedtest/Ping/DNS).
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
                
            # B. Check if it's OUR database by looking for a core application table
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

        # --- 3. Merge Networks (ID Mapping) ---
        # We cannot just insert Networks, because their IDs will conflict. 
        # We must map the remote ID to either an existing local ID, or a newly generated local ID.
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

        # --- 4. Merge Devices (Updating metadata) ---
        remote_devices = cursor_r.execute("SELECT * FROM devices").fetchall()
        for dev in remote_devices:
            new_net_id = network_map.get(dev['network_id'])
            if new_net_id:
                # Upsert device: Keep the newest 'last_seen' date, and prioritize local custom names over remote ones.
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

        # --- 5. Merge Logs (History, Ping, DNS, Wi-Fi) ---
        tables_to_append = {
            'history': ['timestamp', 'network_name', 'connection_type', 'download', 'upload', 'ping', 'wan_ip', 'device_ip', 'isp'],
            'dns_logs': ['timestamp', 'domain', 'result_ip', 'record_type', 'status', 'router_ip', 'network_name', 'lan_ip'],
            'ping_logs': ['timestamp', 'target', 'status', 'latency', 'packet_loss', 'network_context', 'router_ip', 'network_name', 'lan_ip'],
            'wifi_history': ['timestamp', 'scan_name', 'comments', 'results_json']
        }

        for table, cols in tables_to_append.items():
            remote_data = cursor_r.execute(f"SELECT * FROM {table}").fetchall()
            for row in remote_data:
                # Ensure we don't insert a log that completely matches an existing local log
                placeholders = " AND ".join([f"{c} IS ?" for c in cols])
                cursor_l.execute(f"SELECT 1 FROM {table} WHERE {placeholders}", [row[c] for c in cols])
                if not cursor_l.fetchone():
                    col_str = ", ".join(cols)
                    val_placeholders = ", ".join(["?" for _ in cols])
                    cursor_l.execute(f"INSERT INTO {table} ({col_str}) VALUES ({val_placeholders})", [row[c] for c in cols])

        # --- 6. Global Settings ---
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

# ==========================================
# UPDATER (GITHUB INTEGRATION)
# ==========================================

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
    """Routes updates to the active channel's specific GitHub parameters (Repo/Branch/Token)."""
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
    """API endpoint to switch the active update channel."""
    channel = (request.json or {}).get('channel', 'stable')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('update_channel', ?)", (channel,))
        conn.commit()
    return jsonify({"status": "success", "channel": channel})

def fetch_github_file(filename):
    """
    Fetches raw file content directly from GitHub.
    Uses native urllib instead of heavy git modules to bypass standard API rate limits.
    """
    gh_set = get_github_settings()
    
    # REQUIRED: 'refs/heads/' format ensures we hit the exact raw file without API wrappers
    url = f"https://raw.githubusercontent.com/{gh_set['owner']}/{gh_set['repo']}/refs/heads/{gh_set['branch']}/{filename}"
    req = urllib.request.Request(url)
    
    req.add_header("User-Agent", "Network-Diagnostics-App")
    
    # If a token is provided (for private forks), add it safely
    if gh_set.get('token'): 
        req.add_header("Authorization", f"token {gh_set['token']}")
        
    try:
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"[!] GitHub Fetch Error ({filename}): {e}")
        return None
    
@app.route('/api/update/check')
def check_update():
    """
    Intelligent Update Checker.
    Instead of downloading the whole repo, it fetches 3 specific files and uses Regex 
    to extract their hardcoded version strings. This is highly secure as it never actually 
    executes the remote python code.
    Returns a list of mismatches so the frontend UI can list exactly what is outdated.
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
            
            # Parse Remote Version using the provided Regex
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

    # Run checks concurrently to keep the dashboard settings page load time extremely fast
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(check_file, name, cfg) for name, cfg in targets.items()]
        for f in futures:
            res = f.result()
            if res: mismatches.append(res)
            
    # Fetch the global tag for the frontend footer (e.g. 1.0.4)
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
        "remote_version": global_remote,      
        "global_local": get_global_version()   
    })

@app.route('/api/update/changelog')
def get_changelog():
    """Fetches the latest Release Notes from the GitHub repository."""
    content = fetch_github_file("Changelog") 
    return jsonify({"status": "success", "changelog": content}) if content else jsonify({"status": "error"})

def backup_for_update():
    """Takes a safe DB snapshot right before updating (Max 5). Forces a backup."""
    execute_backup("update_good", 5, force=True)

@app.route('/api/update/apply', methods=['POST'])
def update_software():
    """
    Cross-Platform GitHub Update Engine.
    - Takes a DB snapshot.
    - Generates a 'rollback.zip' of the CURRENT app state. If the new update crashes 5 times 
      on boot, the supervisor will automatically extract this zip to revert the damage.
    - Downloads the new zipped code from GitHub and extracts it over the active files.
    - Handles Windows Execution Locks dynamically.
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
                    if "backups" in dirs: dirs.remove("backups") 
                    if "__pycache__" in dirs: dirs.remove("__pycache__")
                    if ".git" in dirs: dirs.remove(".git")
                    
                    for file in files:
                        if file.endswith(".db") or file.endswith(".db-wal") or file.endswith(".db-shm"): continue
                        if file.endswith(".bak") or file == "rollback.zip": continue
                        
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, os.path.relpath(file_path, base_dir))
        except Exception as e:
            print(f"[!] Rollback backup warning: {e}")
        
       # --- 3. Download from GitHub ---
       # Using standard web archive zip format to entirely bypass standard GitHub API limits
        zip_url = f"https://github.com/{gh_set['owner']}/{gh_set['repo']}/archive/refs/heads/{gh_set['branch']}.zip"
        req = urllib.request.Request(zip_url)
        
        req.add_header("User-Agent", "Network-Diagnostics-App")
        
        if gh_set.get('token'): 
            req.add_header("Authorization", f"token {gh_set['token']}")
        
        try:
            with urllib.request.urlopen(req) as response: zip_data = io.BytesIO(response.read())
        except Exception as e: return jsonify({"error": f"Download failed: {e}"}), 500

        # --- 4. Extract & Install ---
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(zip_data) as zip_ref:
                root_name = zip_ref.namelist()[0].split('/')[0]
                zip_ref.extractall(temp_dir)
                source_root = os.path.join(temp_dir, root_name)
                
                # Walk through the extracted files and move them into the active directory
                for root, dirs, files in os.walk(source_root):
                    rel_path = os.path.relpath(root, source_root)
                    dest_dir = os.path.join(base_dir, rel_path)
                    
                    if not os.path.exists(dest_dir):
                        os.makedirs(dest_dir)
                    fix_permissions(dest_dir)
                    
                    for file in files:
                        src_file = os.path.join(root, file)
                        dest_file = os.path.join(dest_dir, file)
                        
                        # Never overwrite the database or the python environment during an update!
                        if file == DB_NAME or file.endswith(".db") or "venv" in dest_file: continue

                        try:
                            if os.path.exists(dest_file):
                                try: 
                                    os.replace(src_file, dest_file)
                                except OSError:
                                    # --- CRITICAL FIX: OS File Lock Handling ---
                                    # Windows rigidly locks files (like app.py) while they are running.
                                    # We bypass this by renaming the running file to '.old', which Windows allows.
                                    if platform.system() == "Windows":
                                        backup = dest_file + f".old_{int(time.time())}"
                                        if os.path.exists(backup): os.remove(backup)
                                        os.rename(dest_file, backup)
                                        shutil.move(src_file, dest_file)
                                    else:
                                        # Linux throws cross-device link errors, so we unlink first
                                        os.remove(dest_file)
                                        shutil.move(src_file, dest_file)
                            else: 
                                shutil.move(src_file, dest_file)
                            
                            fix_permissions(dest_file)
                        except Exception as e: 
                            print(f"[!] Update copy failed for {file}: {e}")

        print("[✓] Update applied. Permissions set to Read/Write/Execute for all.")
        # Trigger the supervisor to restart the new application code natively
        threading.Thread(target=restart_server).start()
        return jsonify({"status": "success", "message": "Update successful. All files set to R/W/X."})

    except Exception as e:
        print(f"[X] Update Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wifi/history', methods=['GET'])
def get_wifi_history():
    """Fetches all undeleted Wi-Fi scans to populate the history table."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        c = conn.cursor()
        # Filter out soft-deleted scans (is_deleted=0)
        c.execute("SELECT id, timestamp, scan_name, comments, is_protected FROM wifi_history WHERE is_deleted=0 ORDER BY timestamp DESC")
        rows = c.fetchall()
        history = [{"id": r[0], "timestamp": r[1], "name": r[2], "comments": r[3], "is_protected": r[4] or 0} for r in rows]
        return jsonify(history)

# --- NEW: Endpoint to lock/unlock records ---
@app.route('/api/system/toggle_protection', methods=['POST'])
def toggle_protection():
    """
    Security Feature: Toggles the 'is_protected' flag on a specific record.
    Protected records are immune to the automated Database Cleanup tools.
    """
    d = request.json
    
    # Map frontend types to actual database table names
    table_map = {
        'history': 'history', 
        'wifi': 'wifi_history', 
        'devices': 'devices', 
        'networks': 'networks',
        'dns': 'dns_logs',
        'ping': 'ping_logs',
        'wifi_ssid': 'protected_wifi_ssids' 
    }
    
    table = table_map.get(d.get('type'))
    item_id = d.get('id')
    state = 1 if d.get('state') else 0
    
    if table and item_id is not None:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            if table == 'devices':
                # Devices use MAC addresses as primary identifiers
                conn.execute("UPDATE devices SET is_protected = ? WHERE mac_address = ?", (state, item_id))
            elif table == 'protected_wifi_ssids':
                # Wi-Fi SSIDs use a separate lookup table to ensure the lock persists across multiple scans
                if state:
                    conn.execute("INSERT OR IGNORE INTO protected_wifi_ssids (ssid) VALUES (?)", (item_id,))
                else:
                    conn.execute("DELETE FROM protected_wifi_ssids WHERE ssid = ?", (item_id,))
            else:
                # Standard ID-based tables
                conn.execute(f"UPDATE {table} SET is_protected = ? WHERE id = ?", (state, item_id))
            conn.commit()
        return jsonify({"status": "success"})
    return jsonify({"error": "Invalid request"}), 400

@app.route('/api/wifi/delete', methods=['POST'])
def delete_wifi_scan():
    """Soft deletes a specific Wi-Fi scan by flipping its visibility flag."""
    scan_id = request.json.get('id')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE wifi_history SET is_deleted=1 WHERE id = ?", (scan_id,))
        conn.commit()
    return jsonify({"status": "deleted"})

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
                writer.writerow(["SSID", "MAC", "Signal (dBm)", "Signal (%)", "Channel", "Band", "Authentication", "Comments"])
                
                for net in results:
                    # Dynamically inject global comments for each SSID into the export
                    try:
                        g_com = conn.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (net.get('ssid',''),)).fetchone()
                        comment_str = g_com['comments'] if g_com else ""
                    except sqlite3.OperationalError:
                        comment_str = ""
                        
                    if "raw_bssids" in net and net["raw_bssids"]:
                        for b in net["raw_bssids"]:
                            if "dbm" in b or "percent" in b:
                                writer.writerow([net.get('ssid',''), b.get('mac', '-'), b.get('dbm',''), b.get('percent',''), b.get('channel',''), b.get('band',''), net.get('auth',''), comment_str])
                            else:
                                writer.writerow([net.get('ssid',''), b.get('mac', '-'), b.get('signal',''), "-", b.get('channel',''), b.get('band',''), net.get('auth',''), comment_str])
                    else:
                        writer.writerow([net.get('ssid',''), net.get('mac', '-'), net.get('signal','').replace('<br>', ' | '), "-", net.get('channel',''), net.get('band','').replace('<br>', ' | '), net.get('auth',''), comment_str])

                zf.writestr(f"wifi_scan_{scan_id}_{scan_name}.csv", csv_out.getvalue())
    
    memory_file.seek(0)
    return send_file(memory_file, download_name="wifi_scans_bulk_export.zip", as_attachment=True)

@app.route('/api/wifi/export/<int:scan_id>')
def export_wifi_csv(scan_id):
    """Exports a single Wi-Fi scan to CSV."""
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT scan_name, results_json FROM wifi_history WHERE id = ?", (scan_id,))
        row = c.fetchone()

        if not row:
            conn.close()
            return "Scan not found", 404

        scan_name = get_safe_filename(row['scan_name'])
        results = json.loads(row['results_json'])

        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["SSID", "MAC", "Signal (dBm)", "Signal (%)", "Channel", "Band", "Authentication", "Comments"])
        for net in results:
            try:
                g_com = c.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (net.get('ssid',''),)).fetchone()
                comment_str = g_com['comments'] if g_com else ""
            except sqlite3.OperationalError:
                comment_str = ""
                
            if "raw_bssids" in net and net["raw_bssids"]:
                for b in net["raw_bssids"]:
                    if "dbm" in b or "percent" in b:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('dbm', '-'), b.get('percent', '-'), b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-'), comment_str])
                    else:
                        writer.writerow([net.get('ssid', 'Unknown'), b.get('mac', '-'), b.get('signal', '-'), "-", b.get('channel', '-'), b.get('band', '-'), net.get('auth', '-'), comment_str])
            else:
                writer.writerow([net.get('ssid', 'Unknown'), net.get('mac', '-'), net.get('signal', '-').replace('<br>', ' | '), "-", net.get('channel', '-'), net.get('band', '-').replace('<br>', ' | '), net.get('auth', '-'), comment_str])

        conn.close()
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
    """Soft deletes all Wi-Fi history."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("UPDATE wifi_history SET is_deleted=1")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/wifi/export_active', methods=['POST'])
def export_active_wifi_csv():
    """Exports the current LIVE active scan results directly from the UI payload."""
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
    """Fetches all connection types (Ethernet/Wi-Fi/Mobile) for dropdowns."""
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM connection_types ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/settings/connection_types/add', methods=['POST'])
def add_connection_type():
    """Adds a new custom connection type (e.g. Starlink, VPN)."""
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
    """Removes a custom connection type."""
    type_id = request.json.get('id')
    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("DELETE FROM connection_types WHERE id=?", (type_id,))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/device_history')
def api_device_history():
    """
    Returns the absolute latest state of all unique devices across all networks.
    Crucial Optimization: In SQLite, using MAX(last_seen) in a GROUP BY query 
    automatically guarantees it returns the corresponding row's data. 
    This turns an O(N^2) heavy subquery into a lightning-fast O(N) query, preventing UI lockups.
    """
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            
            query = """
                SELECT d.mac_address, d.hostname, COALESCE(g.custom_name, d.custom_name) as custom_name,
                     g.comments, d.ip_address, MAX(d.last_seen) as last_seen, COALESCE(n.name, d.last_network_name, 'Deleted Network') as network_name, d.vendor, d.is_protected
                FROM devices d
                LEFT JOIN networks n ON d.network_id = n.id
                LEFT JOIN global_device_names g ON d.mac_address = g.mac_address
                GROUP BY d.mac_address
                ORDER BY last_seen DESC
            """
            
            rows = conn.execute(query).fetchall()
            
            result = []
            for r in rows:
                dev = dict(r)
                vendor = dev.get('vendor') or ""
                clean_host = str(dev['hostname']) if dev['hostname'] else "Unknown"
                
                # Clean up legacy hostnames if the vendor is attached in brackets
                if vendor and f"({vendor})" in clean_host:
                    clean_host = clean_host.replace(f"({vendor})", "").strip()
                else:
                    # Failsafe for legacy devices scanned before the vendor column officially existed
                    match = re.search(r'\(([^)]+)\)$', clean_host)
                    if match and match.group(1) not in ["This device", "Router"]:
                        if not vendor: vendor = match.group(1)
                        clean_host = clean_host.replace(f"({match.group(1)})", "").strip()
                        
                dev['vendor'] = vendor
                dev['clean_hostname'] = clean_host
                result.append(dev)
                
            return jsonify(result)
            
    except Exception as e:
        print(f"[!!!] CRITICAL ERROR in /api/device_history: {e}")
        return jsonify({"error": str(e)})
    
@app.route('/api/wifi_networks_history/delete_ssid', methods=['POST'])
def delete_wifi_ssid():
    """
    Targeted JSON Deletion Tool.
    Loops through every single historical Wi-Fi scan and removes a specific SSID from its payload.
    """
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
    """Returns all historical scan records ('device_scans') for a specific MAC address."""
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
    """Background task to fetch vendor info from the web API and save it to the DB permanently."""
    mac = request.json.get('mac')
    if not mac: 
        return jsonify({"vendor": "Unknown"})
        
    # Force the internet lookup bypassing the local cache check
    vendor = get_mac_vendor(mac, fetch_online=True)
    
    # Save the result to the DB so we never need to look it up online again
    if vendor:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.execute("UPDATE devices SET vendor=? WHERE mac_address=?", (vendor, mac))
            conn.commit()
            
    return jsonify({"vendor": vendor or "Unknown"})

@app.route('/api/wifi_networks_history')
def api_wifi_networks_history():
    """
    Heavy Aggregation Endpoint.
    Loops through every stored Wi-Fi scan and aggregates every unique SSID seen, 
    calculating its first/last seen dates and how many unique physical access points (MACs) it has.
    """
    try:
        with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            
            # Fetch protected SSIDs
            try:
                protected_rows = conn.execute("SELECT ssid FROM protected_wifi_ssids").fetchall()
                protected_ssids = {r['ssid'] for r in protected_rows}
            except:
                protected_ssids = set()
            
            # Fetch all scans sorted oldest to newest to properly track first/last seen dates natively
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
                        
                        # Extract unique physical MACs bridging a mesh network
                        if "raw_bssids" in net:
                            for b in net["raw_bssids"]:
                                mac = b.get("mac")
                                if mac and mac != "Unknown MAC":
                                    networks[ssid]["macs"].add(mac)
                except:
                    continue
            
            # Format the output for the UI
            result = []
            for v in networks.values():
                v["mac_count"] = len(v["macs"])
                v.pop("macs") # Remove the Python Set so it can convert to JSON cleanly
                v["is_protected"] = 1 if v["ssid"] in protected_ssids else 0 # Apply protection lock status
                
                # Overlay the global comment for this specific SSID
                try:
                    g_com = conn.execute("SELECT comments FROM global_wifi_comments WHERE ssid=?", (v["ssid"],)).fetchone()
                    v["comments"] = g_com['comments'] if g_com else ""
                except sqlite3.OperationalError:
                    v["comments"] = ""
                    
                result.append(v)
                
            # Sort by most recently seen
            result.sort(key=lambda x: x["last_seen"], reverse=True)
            return jsonify(result)
            
    except Exception as e:
        return jsonify({"error": str(e)})
    
@app.route('/api/wifi_networks_history/details', methods=['POST'])
def api_wifi_network_details():
    """Drill-down API that returns all instances where a specific SSID was seen."""
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
    """
    Maintenance Tool: Physically removes Wi-Fi networks that ONLY exist in soft-deleted scans.
    Crucially respects SSID locks to prevent deleting user-favorited historical items.
    """
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
                
            # NEW: Add all protected SSIDs to the "active" list so they are never permanently deleted
            try:
                prot_rows = c.execute("SELECT ssid FROM protected_wifi_ssids").fetchall()
                for pr in prot_rows: active_ssids.add(pr['ssid'])
            except: pass
            
            # Step 2: Extract JSON from deleted scans and prune them based on the active lists
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
    """Reads the 'autostart' flat file used by the Desktop OS installers to track startup state."""
    if not os.path.exists(AUTOSTART_FILE):
        with open(AUTOSTART_FILE, "w") as f:
            f.write("1")
        return jsonify({"autostart": True})
    try:
        with open(AUTOSTART_FILE, "r") as f:
            return jsonify({"autostart": f.read(10).strip() == "1"}) # Limit read for security
    except:
        return jsonify({"autostart": True})

@app.route('/api/settings/autostart', methods=['POST'])
def set_autostart():
    """Toggles the 'autostart' flat file state."""
    enable = request.json.get('enable', True)
    try:
        with open(AUTOSTART_FILE, "w") as f:
            f.write("1" if enable else "0")
        return jsonify({"status": "success", "autostart": enable})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/settings/workers', methods=['GET'])
def get_workers_endpoint():
    """Returns the active thread configuration alongside the hardware defaults."""
    return jsonify({
        "config": get_worker_config(),
        "hardware": detect_hardware(),
        "defaults": get_default_workers_for_hardware()
    })

@app.route('/api/settings/workers', methods=['POST'])
def save_workers_endpoint():
    """
    Saves a new thread configuration to the 'workers.json' file.
    Creates a `.bak` copy of the previous working configuration so that the crash-loop
    failsafe can automatically restore it if these new settings trigger an Out-Of-Memory error.
    """
    data = request.json or {}
    defaults = get_default_workers_for_hardware()
    new_config = {}
    
    for key in ["server_threads", "scan_workers", "ping_workers"]:
        val = data.get(key)
        # Apply sanity boundaries (1 to 100) to prevent OS thread exhaustions
        if val is not None and str(val).isdigit() and 1 <= int(val) <= 100:
            new_config[key] = int(val)
        else:
            new_config[key] = defaults[key]

    file_path = os.path.join(app.root_path, WORKERS_FILE)
    backup_path = os.path.join(app.root_path, f"{WORKERS_FILE}.bak")
    try:
        # Create a backup of the previously proven working config before saving the new one
        if os.path.exists(file_path):
            shutil.copy2(file_path, backup_path)
            
        with open(file_path, "w") as f:
            json.dump(new_config, f, indent=4)
            
        # Trigger server restart in the background to apply the new ThreadPoolExecutor limits
        threading.Thread(target=restart_server).start()
        
        return jsonify({"status": "success", "config": new_config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/devices/update_metadata', methods=['POST'])
def update_device_metadata():
    """Unified endpoint to save a device's custom name, vendor, and global comments."""
    d = request.json
    mac = d.get('mac')
    name = str(d.get('name', '')).strip()[:50]
    vendor = str(d.get('vendor', '')).strip()[:50]
    comment = str(d.get('comment', '')).strip()[:200]
    network_id = d.get('network_id')

    if not mac: return jsonify({"status": "error", "message": "MAC required"}), 400

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        if network_id:
            conn.execute("UPDATE devices SET custom_name=?, custom_vendor=? WHERE mac_address=? AND network_id=?", (name, vendor, mac, network_id))
        else:
            conn.execute("UPDATE devices SET custom_name=?, custom_vendor=? WHERE mac_address=?", (name, vendor, mac))
        
        # Save globally so the name and comment persist across different network scans
        conn.execute("""
            INSERT INTO global_device_names (mac_address, custom_name, comments) 
            VALUES (?, ?, ?) 
            ON CONFLICT(mac_address) DO UPDATE SET custom_name=excluded.custom_name, comments=excluded.comments
        """, (mac, name, comment))
        
        # Save custom vendor globally
        conn.execute("INSERT OR REPLACE INTO global_device_vendors (mac_address, custom_vendor) VALUES (?, ?)", (mac, vendor))
        conn.commit()

    return jsonify({"status": "success"})

@app.route('/api/wifi/update_ssid_comment', methods=['POST'])
def update_ssid_comment():
    """Saves a global user-defined comment for a specific Wi-Fi SSID."""
    d = request.json
    ssid = d.get('ssid')
    comment = str(d.get('comment', '')).strip()[:200]

    if not ssid: return jsonify({"status": "error", "message": "SSID required"}), 400

    with sqlite3.connect(DB_NAME, timeout=10.0) as conn:
        conn.execute("INSERT INTO global_wifi_comments (ssid, comments) VALUES (?, ?) ON CONFLICT(ssid) DO UPDATE SET comments=excluded.comments", (ssid, comment))
        conn.commit()
    return jsonify({"status": "success"})

def process_device_quick(args):
    """
    Lightning-fast threaded wrapper specifically designed for the SSE 'Continue Scan' stream.
    Re-uses cached port scanning data if a device was already marked online, cutting scan times in half.
    """
    received, skip_services, gateway_ip = args
    ip = getattr(received, 'psrc', getattr(received, 'ip', None))
    mac = getattr(received, 'hwsrc', getattr(received, 'mac', None))
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip, mac, gateway_ip),
        "services": "None" if skip_services else check_open_ports(ip)['services']
    }

@app.route('/api/adapters/bulk_hide', methods=['POST'])
def bulk_hide_adapters():
    """
    Hides multiple adapters at once from the UI tables.
    Implements the same strict safety checks as the individual hide endpoint to guarantee
    the user cannot hide their pinned adapter or their very last visible adapter.
    """
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

        # 2. Prevent hiding the last usable physical adapter
        interfaces = psutil.net_if_addrs()
        valid_keys = []
        for iface_name, addrs in interfaces.items():
            if "Loopback" in iface_name or "vEthernet" in iface_name or iface_name == "lo": 
                continue
            temp_mac = "-"
            for a in addrs:
                if a.family == psutil.AF_LINK: 
                    temp_mac = a.address
            
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
    """Unhides multiple adapters at once, restoring them to the UI tables."""
    macs = request.json.get('macs', [])
    if not macs:
        return jsonify({"status": "error", "message": "No adapters provided."}), 400

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
    """
    Deletes specific devices from a network profile.
    Supports two modes: deleting an explicit list of MACs, or blindly deleting ALL devices marked offline.
    """
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
            # Safely pass net_id as the first parameter in the array, followed by the MACs
            conn.execute(f"DELETE FROM devices WHERE network_id=? AND mac_address IN ({placeholders})", [net_id] + macs)
        conn.commit()
        
    return jsonify({"status": "success"})

@app.route('/api/settings/logging', methods=['GET'])
def get_logging_settings():
    """Fetches the current logging preference constraints and calculates total log folder size."""
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
            
    # Calculate physical log folder size
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
    """
    Saves logging preferences to the database and IMMEDIATELY updates the active os.environ flags.
    Enforces logical exclusivity (you can't enable full logging while simultaneously disabling all logs).
    """
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
    """Generates a compressed ZIP archive containing all .log files and serves it for download."""
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
    """
    Deletes all .log files from the server.
    Implements a critical failsafe for Windows: If the active log file is locked by the OS, 
    it forcefully truncates it to 0 bytes instead of trying to delete it.
    It also resets Python's stdout/stderr internal stream pointers to 0 to prevent 
    allocating a massive blank space blob in the newly truncated file.
    """
    log_dir = os.path.join(app.root_path, 'logs')
    if not os.path.exists(log_dir):
        return jsonify({"status": "success", "message": "No logs to delete."})
        
    try:
        for file_path in Path(log_dir).glob('*.log'):
            try:
                file_path.unlink()
            except PermissionError:
                # Windows Lock Failsafe: Truncate instead of delete
                with open(file_path, 'w') as f:
                    f.truncate(0)
            except Exception as e:
                pass
        
        # IMPORTANT: Reset the active file pointers to zero
        if hasattr(sys.stdout, 'file') and sys.stdout.file:
            try: sys.stdout.file.seek(0)
            except: pass
        if hasattr(sys.stderr, 'file') and sys.stderr.file:
            try: sys.stderr.file.seek(0)
            except: pass
            
        # The Audit Middleware will automatically record this action right after this return statement!
        return jsonify({"status": "success", "message": "System logs deleted successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/system/backups/info', methods=['GET'])
def get_backups_info():
    """Returns the total size (MB) and physical file count of the local SQLite backups folder."""
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
    """Generates a manual snapshot of the database using the engine (Max 5 quota). Skips if identical."""
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
    """Deletes backups based on the selected mode ('all' vs 'keep_latest')."""
    mode = (request.json or {}).get('mode', 'all')
    backup_dir = os.path.join(app.root_path, 'backups')
    
    if not os.path.exists(backup_dir):
        return jsonify({"status": "success", "message": "No backups to delete."})
    
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.back')]
        
        if mode == 'keep_latest':
            safe_files = [f for f in files if "_good_" in f]
            if safe_files:
                # Find the newest valid backup and exclude it from the deletion array
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

@app.route('/api/system/restart_app', methods=['POST'])
def api_restart_app():
    """Signals the server to gracefully restart via the supervisor loop."""
    threading.Thread(target=restart_server).start()
    return jsonify({"status": "success", "message": "Application is restarting. Please wait..."})

@app.route('/api/system/shutdown_app', methods=['POST'])
def api_shutdown_app():
    """Gracefully shuts down the web server and signals the supervisor bash script to exit completely."""
    def trigger_shutdown():
        time.sleep(1) # Give the HTTP response time to reach the browser UI
        try:
            # Create a signal file that the bash supervisor reads to know it shouldn't auto-respawn
            with open(os.path.join(app.root_path, "shutdown_signal"), "w") as f:
                f.write("shutdown")
        except: pass
        
        # Kill the Waitress/Flask process natively
        os._exit(0)
        
    threading.Thread(target=trigger_shutdown).start()
    return jsonify({"status": "success", "message": "Application and supervisor are shutting down..."})

@app.route('/api/system/os_action', methods=['POST'])
def api_os_action():
    """
    Executes a full cross-platform OS reboot or shutdown.
    Security: Strictly protected. Will refuse to execute unless the application was configured
    as a Dedicated Headless Server ('standalone' file exists).
    """
    action = request.json.get('action')
    
    # Hard backend security check to prevent users from accidentally shutting down their personal PCs
    if not os.path.exists(os.path.join(app.root_path, 'standalone')):
        return jsonify({"error": "Dedicated Server Mode is not enabled."}), 403
        
    def execute_os_action():
        time.sleep(2) 
        sys_plat = platform.system().lower()
        try:
            if action == 'reboot':
                if sys_plat == 'windows': subprocess.run(["shutdown", "/r", "/t", "0"])
                else: subprocess.run(["sudo", "reboot"])
            elif action == 'shutdown':
                if sys_plat == 'windows': subprocess.run(["shutdown", "/s", "/t", "0"])
                else: subprocess.run(["sudo", "shutdown", "-h", "now"])
        except Exception as e:
            print(f"[!] OS Action failed: {e}")
            
    threading.Thread(target=execute_os_action).start()
    return jsonify({"status": "success"})

def manage_boot_counter():
    """
    Core Crash Loop Protection Engine.
    Increments a physical counter file on the disk every single time the application boots.
    If the application crashes 5 times in rapid succession, it engages emergency failsafes:
    1. Extracts 'rollback.zip' to revert a corrupted GitHub update.
    2. Restores 'workers.json' to hardware defaults in case bad thread pools caused an OOM crash.
    3. Reverts the web port to the last known successful binding to fix port locking conflicts.
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
        
        # 1. Rollback Failed Update
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
                
        # 2. Restore Worker Settings
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
            
        # 3. Restore Last Known Good Web Port
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

        # Clear the counter so it can attempt a fresh boot
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
    """
    Timer function executed 5 seconds AFTER a successful server boot.
    - If this fires, it means the application is stable and didn't instantly crash.
    - Deletes the crash-loop counter file.
    - Clears the update rollback archive (confirming the new update was completely successful).
    - Saves the current port as the 'last known good' port in the database.
    - Triggers the automated SQLite database backup routine safely in the background.
    """
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
    
    perform_startup_backup()

def create_tray_icon():
    """
    Initializes and runs the cross-platform system tray icon using 'pystray'.
    - Skips execution if running in headless/daemon mode ONLY on Linux or unknown OS.
    - Provides a native context menu allowing the user to seamlessly Restart or Shutdown the Python server.
    - macOS Fix: Integrates with the native NSApplication runloop to prevent locking up the OS dock.
    """
    sys_plat = platform.system()
    
    # On Windows and macOS, the user is always logged in based on the installer configuration.
    # We only check for headless/daemon environments on Linux or unknown OS.
    if sys_plat not in ["Windows", "Darwin"]:
        if is_headless_mode():
            print("[*] Headless/Daemon mode detected. Skipping system tray icon.")
            return

    try:
        import pystray
        from PIL import Image
    except ImportError:
        print("[*] 'pystray' or 'Pillow' missing. Skipping system tray icon.")
        return

    # Select the correct icon format based on OS capabilities
    if sys_plat == "Darwin":
        icon_path = os.path.join(app.root_path, 'static', 'Logo.png')
    else:
        icon_path = os.path.join(app.root_path, 'static', 'favicon.ico')
        
    if not os.path.exists(icon_path):
        icon_path = os.path.join(app.root_path, 'static', 'Logo.png')
        if not os.path.exists(icon_path):
            print("[*] Tray icon image not found in 'static' folder. Skipping.")
            return

    try:
        image = Image.open(icon_path)
        image.thumbnail((32, 32))
    except Exception as e:
        print(f"[!] Failed to load tray icon image: {e}")
        return

    def on_restart(icon, item):
        print("[*] Tray Action: Restart requested.")
        threading.Thread(target=restart_server).start()

    def on_stop(icon, item):
        print("[*] Tray Action: Shutdown requested.")
        try:
            with open(os.path.join(app.root_path, "shutdown_signal"), "w") as f:
                f.write("shutdown")
        except Exception: 
            pass
        icon.stop() 
        os._exit(0)

    try:
        menu = pystray.Menu(
            pystray.MenuItem("Restart Dashboard", on_restart),
            pystray.MenuItem("Stop Dashboard", on_stop)
        )
        icon = pystray.Icon("NetworkDiagnostics", image, "Network Diagnostics", menu)
        
        # CRITICAL MACOS FIX: 
        # On macOS, icon.run() blocks the main thread and requires a setup callback 
        # to properly attach to the native application runloop.
        def setup_action(icon_instance):
            icon_instance.visible = True

        icon.run(setup=setup_action)
        
    except Exception as e:
        # Failsafe: If the UI cannot be drawn for any reason, print the error and let the function finish.
        # This allows the main thread to proceed to `server_thread.join()` so the web server stays active.
        print(f"[!] System tray icon failed to initialize (GUI may be inaccessible): {e}")

def get_available_port(start_port):
    """
    Scans for an available TCP port starting from the user's requested port, scanning upwards (Max +9).
    Explicitly skips known browser-restricted ports (like port 87 or 6000) that would block UI access.
    """
    max_port = max(start_port + 9, 90)
    for port in range(start_port, max_port + 1):
        if port in RESTRICTED_PORTS:
            print(f"[*] Port {port} skipped (browser-restricted unsafe port).")
            continue
            
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Tell the OS we are allowed to test bind to ports that are temporarily stuck in a TIME_WAIT state
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                print(f"[*] Port {port} is in use, checking next...")
                continue
    return None

def check_disk_space():
    """
    Checks if available hard drive disk space is critically low (Below 250MB).
    If true, triggers a persistent UI alert so the user knows why SQLite might fail to write logs.
    """
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

def is_headless_mode():
    """
    Detects if the application is running in a headless (no monitor) or background daemon environment.
    Used primarily to determine if the Tray Icon should be skipped on Linux servers.
    """
    # 1. Explicit standalone configuration file trigger (User created)
    if os.path.exists(os.path.join(app.root_path, "standalone")):
        return True
        
    # 2. Linux-specific: No X11 graphical display server is available in the environment vars
    if platform.system() == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True
            
    return False

# ==========================================
# APPLICATION BOOTSTRAP SEQUENCE
# ==========================================
if __name__ == '__main__':
    # 1. Engage crash-loop protection before anything else
    manage_boot_counter()

    # 2. Disable Wi-Fi Power Management on Linux/Raspberry Pi 
    # (Prevents the Wi-Fi card from going to sleep, which ruins continuous network scanning)
    if platform.system() == "Linux":
        try:
            if shutil.which("iw"):
                out = subprocess.check_output("iw dev | awk '$1==\"Interface\"{print $2}'", shell=True, text=True).strip()
                if out:
                    for iface in out.split('\n'):
                        iface = iface.strip()
                        if iface:
                            subprocess.run(["sudo", "iw", "dev", iface, "set", "power_save", "off"], 
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            print(f"[*] Wi-Fi power management disabled for {iface}.")
        except Exception as e:
            pass

    # 3. Clean up Windows Update artifacts
    cleanup_old_files()
    
    # 4. Core System Initializations
    check_clear_database()
    init_db()
    check_password_reset()
    check_webport_file()
    check_dev_file()
    check_disk_space()
    schedule_routine_backups()
    
    current_port = get_current_port()

    # 5. Port Conflict Fallback
    available_port = get_available_port(current_port)
    
    if not available_port:
        print("\n" + "!"*60)
        print(f"[!] CRITICAL ERROR: Port conflict detected.")
        print(f"[!] Could not find an open port between {current_port} and 90.")
        print(f"[!] Please define a port in the 'webport' file.")
        print("!"*60 + "\n")
        
        port_file = os.path.join(app.root_path, "webport")
        try:
            with open(port_file, "w") as f:
                f.write("8080")
            print(f"[*] Auto-created 'webport' file in {app.root_path} with suggested port 8080.")
        except: pass
        sys.exit(1)
        
    if available_port != current_port:
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
                
    # 6. Start the 5-second stabilization timer to clear the boot crash-loop counter
    threading.Timer(5.0, clear_boot_counter).start()

    def run_web_server():
        """
        Inner wrapper function that spins up the active web server.
        Prioritizes the high-performance 'Waitress' WSGI server for production, 
        and falls back to Flask's basic development server if waitress is missing.
        """
        try:
            from waitress import serve
            lan_ip = get_local_ip()
            v_glob = get_global_version().replace('DEV', '').strip()
            
            print("\n" + "="*60)
            print(f"   DASHBOARD ACTIVE: http://{lan_ip}:{current_port}")
            print(f"   (Local Access: http://127.0.0.1:{current_port})")
            print("   (Production WSGI Server - No Warnings)")
            print("   " + "-"*54)
            print(f"   Versions: Global: {v_glob} | App: {APP_VERSION} | Setup: {get_setup_version()} | HTML: {get_html_version()}")
            print("="*60 + "\n")
            
            worker_cfg = get_worker_config()
            serve(app, host='0.0.0.0', port=current_port, threads=worker_cfg['server_threads'])
            
        except ImportError:
            lan_ip = get_local_ip()
            v_glob = get_global_version().replace('DEV', '').strip()
            
            print("\n" + "="*60)
            print(f"   DASHBOARD ACTIVE: http://{lan_ip}:{current_port}")
            print("   (Development Server)")
            print("   " + "-"*54)
            print(f"   Versions: Global: {v_glob} | App: {APP_VERSION} | Setup: {get_setup_version()} | HTML: {get_html_version()}")
            print("="*60 + "\n")
            
            # Disable reloader because it conflicts with custom threading and the supervisor
            app.run(debug=False, host='0.0.0.0', port=current_port, use_reloader=False)

    # 7. Start the web server in a background daemon thread
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()

    # 8. Start the System Tray icon on the main OS thread
    # If successful, this completely blocks the main thread permanently while the OS icon UI exists.
    create_tray_icon()
    
    # 9. Fallback Failsafe
    # If the tray icon is intentionally skipped (headless Linux server) or crashes internally, 
    # the main thread drops down to here. We use .join() to lock the main thread to the server thread,
    # ensuring the background web application stays alive indefinitely!
    server_thread.join()