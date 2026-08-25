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

logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
conf.verb = 0

# --- Configuration ---
APP_VERSION = "0.8"

# GITHUB CONFIGURATION
# Ensure your Personal Access Token (PAT) has 'repo' scope
GITHUB_SETTINGS = {
    "owner": "Pancool",
    "repo": "Network-Testing-Tools",
    "token": "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",
    "branch": "main"
}

app = Flask(__name__)
DB_NAME = "network_data.db"

# --- Database & Migrations ---
def init_db():
    """Initializes the database with WAL mode for concurrency and creates all tables."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Enable Write-Ahead Logging (Fixes 'database is locked' errors)
        c.execute("PRAGMA journal_mode=WAL;") 
        
        # 1. History Table (Speed Tests)
        # Added device_ip to store the local interface IP used for the test
        c.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT, 
                        network_name TEXT, 
                        connection_type TEXT,
                        download TEXT, 
                        upload TEXT, 
                        ping TEXT, 
                        wan_ip TEXT, 
                        device_ip TEXT,
                        isp TEXT
                    )''')
        
        # 2. Adapter Settings Table
        c.execute('''CREATE TABLE IF NOT EXISTS adapter_settings (
                        mac_address TEXT PRIMARY KEY, 
                        custom_name TEXT, 
                        is_visible INTEGER DEFAULT 1,
                        is_primary INTEGER DEFAULT 0
                    )''')

        # 3. Networks Table 
        c.execute('''CREATE TABLE IF NOT EXISTS networks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        gateway_mac TEXT,
                        name TEXT, 
                        last_scan TEXT, 
                        gateway_ip TEXT
                    )''')

        # 4. Devices Table
        c.execute('''CREATE TABLE IF NOT EXISTS devices (
                        mac_address TEXT, 
                        network_id INTEGER, 
                        hostname TEXT,
                        custom_name TEXT, 
                        ip_address TEXT, 
                        last_seen TEXT,
                        services TEXT, 
                        is_online INTEGER DEFAULT 0,
                        PRIMARY KEY (mac_address, network_id),
                        FOREIGN KEY(network_id) REFERENCES networks(id) ON DELETE CASCADE
                    )''')
        
        # 5. Global Device Names
        c.execute('''CREATE TABLE IF NOT EXISTS global_device_names (
                        mac_address TEXT PRIMARY KEY, 
                        custom_name TEXT
                    )''')
        
        # 6. DNS Logs
        c.execute('''CREATE TABLE IF NOT EXISTS dns_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        domain TEXT,
                        result_ip TEXT, 
                        record_type TEXT, 
                        status TEXT,
                        router_ip TEXT, 
                        network_name TEXT, 
                        lan_ip TEXT
                    )''')
        
        # 7. Ping Logs
        c.execute('''CREATE TABLE IF NOT EXISTS ping_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        target TEXT,
                        status TEXT, 
                        latency TEXT, 
                        packet_loss TEXT, 
                        network_context TEXT,
                        router_ip TEXT, 
                        network_name TEXT, 
                        lan_ip TEXT
                    )''')

        # 8. Wi-Fi Scan History Table
        c.execute('''CREATE TABLE IF NOT EXISTS wifi_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        scan_name TEXT,
                        comments TEXT,
                        results_json TEXT
                    )''')
        
        # 9. Connection Types (For Speed Tests)
        c.execute('''CREATE TABLE IF NOT EXISTS connection_types (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE
                    )''')
        
        # Pre-populate connection types if the table is empty
        c.execute("SELECT COUNT(*) FROM connection_types")
        if c.fetchone()[0] == 0:
            for t in ["Ethernet", "Wi-Fi", "Mobile data"]:
                c.execute("INSERT INTO connection_types (name) VALUES (?)", (t,))
        
        # --- MIGRATIONS (Updates existing databases safely) ---
        
        # VLAN Support Migration (Removes UNIQUE constraint from gateway_mac)
        try:
            c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='networks'")
            row = c.fetchone()
            if row and "gateway_mac TEXT UNIQUE" in row[0]:
                print("[*] Migrating database for VLAN support...")
                c.execute("ALTER TABLE networks RENAME TO networks_old")
                c.execute('''CREATE TABLE networks (
                                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                                gateway_mac TEXT, 
                                name TEXT, 
                                last_scan TEXT, 
                                gateway_ip TEXT
                            )''')
                c.execute("INSERT INTO networks (id, gateway_mac, name, last_scan, gateway_ip) SELECT id, gateway_mac, name, last_scan, gateway_ip FROM networks_old")
                c.execute("DROP TABLE networks_old")
                print("[✓] VLAN migration successful.")
        except Exception as e:
            print(f"[!] Migration check failed: {e}")

        # Column Migrations for History Table
        try: c.execute("ALTER TABLE history ADD COLUMN isp TEXT"); 
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT"); 
        except sqlite3.OperationalError: pass
        # NEW: Migration to add device_ip to existing history tables
        try: c.execute("ALTER TABLE history ADD COLUMN device_ip TEXT"); 
        except sqlite3.OperationalError: pass
        
        # Adapter Settings Migrations
        try: c.execute("ALTER TABLE adapter_settings ADD COLUMN is_visible INTEGER DEFAULT 1"); 
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE adapter_settings ADD COLUMN is_primary INTEGER DEFAULT 0"); 
        except sqlite3.OperationalError: pass

        # Tool Log Context Migrations
        for table in ['dns_logs', 'ping_logs']:
            for col in ['router_ip', 'network_name', 'lan_ip']:
                try: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError: pass 

        conn.commit()
        
# Ensure this runs on startup
init_db()

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

def get_global_version():
    """Reads the current global version from local version.json."""
    try:
        if os.path.exists("version.json"):
            with open("version.json", "r") as f:
                return json.load(f).get("version", "0.0.0")
        return "0.0.0"
    except:
        return "Error"

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
    """Restarts the current Python script robustly on Windows, Linux, and macOS."""
    print("[*] Triggering application restart in 2 seconds...")
    time.sleep(2)  # Allow the HTTP response to finish sending
    
    python = sys.executable
    # os.execl replaces the current process with a new one
    # compatible with Windows (creates new process) and Unix (replaces process)
    os.execl(python, python, *sys.argv)

def get_extended_iface_info():
    """
    Fetches Gateway, DNS, and MAC information.
    - Windows: Parses ipconfig (primary) -> PowerShell (fallback for missing DNS/GW).
    - macOS: Parses networksetup/ipconfig (Fixes missing MACs & secondary Gateways).
    - Linux: Parses ip route/resolv.conf.
    """
    info = {}
    system = platform.system()
    
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
                                info[current_iface]["gateway"] = gw
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
                        info[name]["gateway"] = str(gw_raw)
                    
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
                        default_gw = parts[1]
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
                            
                            # Extract Router (Gateway) - FIX FOR SECONDARY INTERFACES
                            # Looks for: router (ip_mult): {192.168.1.1}
                            match_gw = re.search(r'router\s*\(.*?\)\s*:\s*\{(.*?)\}', ipconfig, re.DOTALL)
                            if match_gw:
                                gw_found = match_gw.group(1).replace('\n', '').strip()
                                if gw_found and gw_found != "0.0.0.0":
                                    gw_val = gw_found

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
                default_gw = gw_out.strip() if ":" not in gw_out else "-"
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
    Prioritizes the Pinned Adapter's traffic if one is set.
    """
    global last_received, last_sent, last_time
    
    target_iface = None
    pinned_mac = None

    # 1. Identify if an adapter is pinned
    try:
        with sqlite3.connect(DB_NAME) as conn:
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
    except: 
        pass

    # 2. Map Pinned MAC to system interface name
    if pinned_mac:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == psutil.AF_LINK and a.address == pinned_mac for a in addrs):
                target_iface = name
                break

    # 3. Get IO Counters
    if target_iface:
        # Get stats ONLY for the pinned adapter
        try:
            io = psutil.net_io_counters(pernic=True)[target_iface]
        except KeyError:
            # Fallback to global if adapter was unplugged
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
    
    # Update global tracking variables for the next poll
    last_received, last_sent, last_time = curr_recv, curr_sent, curr_time
    
    return {
        "download": f"{down / 1024 / 1024:.2f} MB/s", 
        "upload": f"{up / 1024 / 1024:.2f} MB/s"
    }

def get_active_interface_name():
    """Finds the interface matching the local IP, returning an empty string if offline."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # This will fail if there is no network routing at all
        s.connect(('8.8.8.8', 80))
        target_ip = s.getsockname()[0]
    except:
        target_ip = '127.0.0.1'
    finally:
        s.close()

    if target_ip == '127.0.0.1':
        return "" # Return empty string instead of None

    interfaces = psutil.net_if_addrs()
    for iface_name, addrs in interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == target_ip:
                return iface_name
    return ""

MAC_VENDOR_CACHE = {}

def get_mac_vendor(mac):
    """Fetches the manufacturer name based on the MAC address."""
    if not mac or mac == "-" or mac.startswith("NO_MAC"): return ""
    
    mac_prefix = mac[:8].upper() 
    if mac_prefix in MAC_VENDOR_CACHE:
        return MAC_VENDOR_CACHE[mac_prefix]

    try:
        # Queries a free MAC lookup API. Added User-Agent to prevent blocking.
        req = urllib.request.Request(
            f"https://api.maclookup.app/v2/macs/{mac_prefix}",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=2) as url:
            data = json.loads(url.read().decode())
            
            # FIXED: The API returns the vendor under 'company', not 'macCompany'
            if data.get('success') and data.get('company'):
                # Clean up long corporate suffixes
                company = data['company'].replace(' Inc.', '').replace(' Ltd.', '').split(',')[0]
                MAC_VENDOR_CACHE[mac_prefix] = company
                return company
    except Exception: 
        pass
    
    MAC_VENDOR_CACHE[mac_prefix] = ""
    return ""

def resolve_hostname(ip, mac=None):
    """Resolves hostname using DNS, ARP cache, NetBIOS, and appends MAC Vendor in brackets."""
    hostname = "Unknown Device"
    
    # 1. Standard DNS (Reverse Lookup)
    try:
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1) 
        name = socket.gethostbyaddr(ip)[0]
        socket.setdefaulttimeout(default_timeout)
        if name and not name.startswith(ip) and name != "?": 
            hostname = name
    except:
        socket.setdefaulttimeout(default_timeout if 'default_timeout' in locals() else None)
    
    # 2. macOS/Linux ARP Cache Fallback
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

    # 3. NetBIOS Lookup (Great for Windows PCs/NAS devices)
    if hostname == "Unknown Device" and platform.system() == "Windows":
        try:
            out = subprocess.check_output(["nbtstat", "-A", ip], text=True, timeout=2)
            for line in out.split('\n'):
                if "<20>" in line and "UNIQUE" in line:
                    hostname = line.split("<20>")[0].strip()
                    break
        except: pass

    # 4. Append MAC Vendor (Always runs if MAC is present)
    if mac:
        vendor = get_mac_vendor(mac)
        if vendor:
            hostname = f"{hostname} ({vendor})"

    return hostname

def process_device_info(received):
    """
    Worker function for threaded scanning. 
    MUST be defined before scan_network calls it.
    """
    # Use fallback mock objects if triggered by ping sweep
    ip = getattr(received, 'psrc', getattr(received, 'ip', None))
    mac = getattr(received, 'hwsrc', getattr(received, 'mac', None))
    
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip, mac),
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
        with sqlite3.connect(DB_NAME) as conn:
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

    try:
        # Use 'iface' to force Scapy to only bind to the chosen adapter
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), 
                     timeout=2, verbose=0, iface=target_iface)
        for _, received in ans: 
            return received.hwsrc
    except Exception as e:
        # Silence the error in logs but print to console for debugging
        print(f"[*] Gateway MAC resolution skipped on {target_iface}: {e}")
    
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
            with sqlite3.connect(DB_NAME) as conn:
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

    return render_template('dashboard.html', 
                           local_ip=get_local_ip(), 
                           wan_ip=info['ip'], 
                           isp_name=info['isp'],
                           router_ip=active_gateway,
                           dns_servers=active_dns,
                           global_version=get_global_version(), 
                           setup_version=get_setup_version(), 
                           app_version=APP_VERSION,
                           html_version=get_html_version())

@app.route('/api/system/cleanup', methods=['POST'])
def cleanup_database():
    """
    Cleans up the database based on the selected interval.
    'days' can be 7, 30, 365, or 'all'.
    """
    days = request.json.get('days')
    tables = ['history', 'dns_logs', 'ping_logs', 'wifi_history']
    
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            
            if days == 'all':
                # Complete wipe of all user data
                for table in tables + ['networks', 'devices', 'global_device_names', 'adapter_settings']:
                    cursor.execute(f"DELETE FROM {table}")
                message = "Database cleared completely."
            else:
                # Selective cleanup of logs based on timestamp
                # SQLite handles '%Y-%m-%d %H:%M:%S' strings naturally with date() functions
                for table in tables:
                    cursor.execute(f"DELETE FROM {table} WHERE timestamp < datetime('now', '-{days} days')")
                message = f"Data older than {days} days has been removed."
            
            conn.commit()
            return jsonify({"status": "success", "message": message})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/adapters')
def get_adapters():
    """
    Fetches all network adapters with:
    1. Stable IPv4 data from ipconfig.
    2. Pinned Adapter logic (Header locks to user choice).
    3. Hardware link speeds with a fallback to 'Not Available' if idle.
    4. Real-time Wi-Fi rates only when active traffic exists.
    5. OS-Specific DNS handling (Fixes Ubuntu 127.0.0.53 issue).
    6. macOS MAC Address Fallback (Fixes missing adapters).
    7. ADDED: Real-time global bandwidth for dashboard metric cards.
    """
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    
    # Fetch stable system info (Gateway/DNS) and Wi-Fi rates
    ext_info = get_extended_iface_info()
    wifi_rates = get_wifi_rates()
    
    # Identify the primary active interface or the user-pinned interface
    active_iface_name = get_active_interface_name()
    primary_gw = "Unknown"
    primary_dns = "Unknown"
    pinned_mac = None

    # Load user settings (Names, Visibility, and Pinned status)
    settings = {}
    try:
        with sqlite3.connect(DB_NAME) as conn:
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
        
        # Filter out virtual/loopback clutter
        if "Loopback" in name or "vEthernet" in name: 
            continue
        
        ip4, mac = "-", "-"
        for a in addrs:
            if a.family == socket.AF_INET: 
                ip4 = a.address
            elif a.family == psutil.AF_LINK: 
                mac = a.address

        # Get stable Gateway and DNS from the default parser
        spec_info = ext_info.get(name, {})
        
        # Windows Cross-reference (Handle alias vs full name)
        if not spec_info and platform.system() == "Windows":
             for k, v in ext_info.items():
                 if k in name or name in k:
                     spec_info = v
                     break

        # macOS MAC Address Fallback
        if mac == "-" and spec_info.get("mac"):
            mac = spec_info.get("mac")

        # Extract values and filter for IPv4
        gw = spec_info.get("gateway", "-")
        if ":" in gw: gw = "-"
        
        dns = spec_info.get("dns", "-")
        if ":" in dns: dns = "-"

        # Linux Specific DNS Fix
        if platform.system() == "Linux":
            real_dns = get_linux_dns(name)
            if real_dns:
                dns = real_dns

        # HEADER PINNING LOGIC
        is_pinned = (mac == pinned_mac) if pinned_mac else False
        is_active_default = False
        if not pinned_mac and active_iface_name:
            is_active_default = (name == active_iface_name or name in active_iface_name)

        if is_pinned or (not pinned_mac and is_active_default):
             if gw != "-": primary_gw = gw
             if dns != "-": primary_dns = dns

        # SPEED LOGIC
        raw_speed = st.speed if st else 0
        display_speed = "Not Available"
        
        if raw_speed > 0:
            if raw_speed >= 1000:
                display_speed = f"{raw_speed/1000:g} Gbps"
            else:
                display_speed = f"{raw_speed} Mbps"
        
        # Real-time Wi-Fi rate override
        for wifi_name, rate_str in wifi_rates.items():
            if wifi_name.lower() in name.lower() or name.lower() in wifi_name.lower():
                display_speed = rate_str

        if "0 Mbps" in display_speed:
            display_speed = "Not Available"

        # Apply custom naming and visibility
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

    # Global Failsafe for the Header
    if primary_gw == "Unknown" or ":" in primary_gw:
        primary_gw = "-"
        for v in ext_info.values():
            curr_gw = v.get("gateway")
            if curr_gw and curr_gw != "-" and ":" not in curr_gw:
                primary_gw = curr_gw
                if primary_dns == "Unknown":
                    primary_dns = v.get("dns", "-")
                break
    
    # --- THE LIVE SPEED FIX ---
    # Call the existing helper to get live throughput
    live_traffic = get_bandwidth()
        
    return jsonify({
        "adapters": adapters_data, 
        "primary_router": primary_gw, 
        "primary_dns": primary_dns,
        "global_speed": live_traffic  # This powers 'live-down' and 'live-up' in the dashboard
    })

@app.route('/api/adapter_settings', methods=['POST'])
def save_adapter_settings():
    """Updates custom name, visibility, and primary (pinned) status."""
    data = request.json
    mac = data.get('mac')
    name = data.get('name', '').strip()
    visible = data.get('visible', 1)
    primary = data.get('is_primary', 0)
    
    if not mac or mac == '-':
        return jsonify({"status": "error", "message": "Cannot configure adapter without MAC address"}), 400

    with sqlite3.connect(DB_NAME) as conn:
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
    name = d.get('name')
    visible = 1 if d.get('visible') else 0
    
    # If the adapter has no MAC (virtual interface), we can't reliably save settings
    if not mac or mac == '-':
        return jsonify({"status": "error", "message": "Cannot configure adapter without MAC address"})

    with sqlite3.connect(DB_NAME) as conn:
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
    macOS ipconfig, and Linux sysfs speed attributes.
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
                        
                        # Only report if there is actual traffic, otherwise skip
                        # so the main loop uses the hardware link speed instead.
                        if tx_val > 0 or rx_val > 0:
                            tx_mbps = round(tx_val / 1_000_000, 1)
                            rx_mbps = round(rx_val / 1_000_000, 1)
                            rates[name] = f"Tx: {tx_mbps} / Rx: {rx_mbps} Mbps"
                    except: continue
                    
        elif system == "Darwin": # macOS
            try:
                # 1. Primary Method: Use the 'airport' utility to get the real-time link rate
                airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
                if os.path.exists(airport_path):
                    # -I provides detailed info including the transmit rate
                    out = subprocess.check_output([airport_path, "-I"], text=True)
                    rate_match = re.search(r'lastTxRate:\s+(\d+)', out)
                    if rate_match:
                        # Map to 'en0' (Standard macOS Wi-Fi interface name)
                        rates["en0"] = f"{rate_match.group(1)} Mbps"
                
                # 2. Fallback: Use ipconfig if airport is restricted
                if "en0" not in rates:
                    out = subprocess.check_output(["ipconfig", "getsummary", "en0"], text=True)
                    tx_match = re.search(r'transmitRate\s+:\s+(\d+)', out)
                    if tx_match: 
                        rates["en0"] = f"{tx_match.group(1)} Mbps"
            except Exception as e:
                print(f"macOS Wi-Fi rate fetch failed: {e}")    

        elif system == "Linux": # Linux
            try:
                # Iterate through interfaces in /sys/class/net
                for iface in os.listdir('/sys/class/net/'):
                    # Check for common Wi-Fi interface prefixes
                    if iface.startswith(('wlan', 'wlp', 'wlo')):
                        try:
                            speed_path = f'/sys/class/net/{iface}/speed'
                            if os.path.exists(speed_path):
                                with open(speed_path, 'r') as f:
                                    speed = f.read().strip()
                                    # -1 often indicates the link is down or speed is unknown
                                    if speed != "-1":
                                        rates[iface] = f"{speed} Mbps"
                        except: continue
            except: pass

    except Exception as e:
        print(f"Error in get_wifi_rates: {e}")
    return rates

# --- Network & Device Management Routes ---

@app.route('/api/networks')
def list_networks():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        networks = conn.execute("SELECT * FROM networks ORDER BY last_scan DESC").fetchall()
        result = []
        for net in networks:
            count = conn.execute("SELECT COUNT(*) FROM devices WHERE network_id=?", (net['id'],)).fetchone()[0]
            result.append({
                "id": net['id'], "name": net['name'], "gateway_mac": net['gateway_mac'],
                "gateway_ip": net['gateway_ip'], "last_scan": net['last_scan'], "device_count": count
            })
        return jsonify(result)

@app.route('/api/networks/delete', methods=['POST'])
def delete_network():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM networks WHERE id=?", (request.json.get('id'),))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/rename', methods=['POST'])
def rename_network():
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE networks SET name=? WHERE id=?", (d.get('name'), d.get('id')))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/devices/update_name', methods=['POST'])
def update_device_name():
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE devices SET custom_name=? WHERE mac_address=? AND network_id=?", 
                     (d.get('name'), d.get('mac'), d.get('network_id')))
        conn.execute("INSERT OR REPLACE INTO global_device_names (mac_address, custom_name) VALUES (?, ?)", 
                     (d.get('mac'), d.get('name')))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/<int:net_id>/devices')
def get_network_devices(net_id):
    """Returns devices for a network, ensuring all fields are present."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row  # This is critical for accessing columns by name
        # Explicitly select 'services' to ensure it's not missed
        query = "SELECT mac_address, hostname, custom_name, ip_address, last_seen, services, is_online FROM devices WHERE network_id=?"
        devices = conn.execute(query, (net_id,)).fetchall()
        
        dev_list = [dict(d) for d in devices]
        
        # Sync with Global Names (Bonus: ensures names follow the device everywhere)
        for dev in dev_list:
            global_name_row = conn.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (dev['mac_address'],)).fetchone()
            if global_name_row and global_name_row[0]:
                dev['custom_name'] = global_name_row[0]

        try:
            dev_list.sort(key=lambda x: ipaddress.IPv4Address(x['ip_address']))
        except: pass
        return jsonify(dev_list)

@app.route('/api/scan_network')
def scan_network():
    """
    Robust Pinned-First Network Scan for Windows/macOS/Linux.
    Improved: Guarantees Router injection even if the router completely blocks ARP.
    Includes an OS-agnostic Ping Sweep fallback for stubborn Wi-Fi adapters.
    """
    pinned_mac = None
    target_iface = None
    target_ip_val = None
    target_mac_val = None

    # 1. Check for Pinned Adapter in Database
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
        if row:
            pinned_mac = row[0]

    # 2. Match Pinned MAC to a System Interface
    if pinned_mac:
        interfaces = psutil.net_if_addrs()
        for name, addrs in interfaces.items():
            current_mac, current_ip = None, None
            for a in addrs:
                if a.family == psutil.AF_LINK: current_mac = a.address
                elif a.family == socket.AF_INET: current_ip = a.address
            
            if current_mac == pinned_mac:
                target_iface, target_ip_val, target_mac_val = name, current_ip, current_mac
                break

    # 3. Fallback to Active Interface
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
    if target_iface:
        conf.iface = target_iface

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scanned_results = []

    try:
        # 4. Physical Scan (ARP) via Scapy
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_subnet), 
                     timeout=3, retry=2, verbose=0, inter=0.02, 
                     iface=target_iface, promisc=False)
        
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(process_device_info, received) for _, received in ans]
            for future in futures: scanned_results.append(future.result())

        # --- OS-AGNOSTIC PING SWEEP FALLBACK ---
        if len(scanned_results) <= 2:
            print("[*] Scapy scan found few devices. Initiating OS Ping Sweep...")
            
            def fast_ping(ip_str):
                """Sends a single fast ping with OS-specific arguments."""
                sys_plat = platform.system().lower()
                if sys_plat == 'windows':
                    cmd = ['ping', '-n', '1', '-w', '500', ip_str]
                elif sys_plat == 'darwin':
                    cmd = ['ping', '-c', '1', '-W', '500', ip_str] # macOS uses ms for -W
                else:
                    cmd = ['ping', '-c', '1', '-W', '1', ip_str]   # Linux uses seconds for -W
                
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except: pass

            # Sweep the subnet concurrently
            network = ipaddress.IPv4Network(target_subnet, strict=False)
            with ThreadPoolExecutor(max_workers=50) as executor:
                for ip in network.hosts():
                    executor.submit(fast_ping, str(ip))
            
            # Read the OS ARP Cache
            arp_out = subprocess.check_output(["arp", "-a"], text=True)
            
            # Parse the ARP cache for MACs and IPs
            found_ips = [d['ip'] for d in scanned_results]
            arp_devices = []
            
            # Regex designed to match Windows, macOS, and Linux ARP table formats
            for match in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})', arp_out):
                ip_found, raw_mac = match.groups()
                
                # Normalize MAC (macOS/Linux sometimes drop leading zeros)
                mac_found = ':'.join([p.zfill(2) for p in raw_mac.replace('-', ':').split(':')]).lower()
                
                # Filter out multicast MACs, broadcast IPs, and devices we already found
                if ip_found not in found_ips and not mac_found.startswith('ff:ff') and not mac_found.startswith('01:00:5e') and ipaddress.IPv4Address(ip_found) in network:
                    # Robust Mock Object passing both IP and MAC variants
                    class MockReceived:
                        ip = ip_found
                        psrc = ip_found
                        hwsrc = mac_found
                        mac = mac_found
                    arp_devices.append(MockReceived())
                    found_ips.append(ip_found)

            # Process newly found devices concurrently
            if arp_devices:
                with ThreadPoolExecutor(max_workers=30) as executor:
                    futures = [executor.submit(process_device_info, dev) for dev in arp_devices]
                    for future in futures: scanned_results.append(future.result())
                    
    except Exception as e: 
        return jsonify({"error": f"Scan failed: {str(e)}"})

    # --- INJECT & LABEL LOCAL HOST ---
    if target_ip_val and target_mac_val and target_ip_val != "127.0.0.1":
        local_device_found = False
        for d in scanned_results:
            if d["ip"] == target_ip_val:
                local_device_found = True
                if "(This device)" not in d["hostname"]:
                    base_name = d["hostname"] if d["hostname"] and d["hostname"] != "Unknown Device" else socket.gethostname()
                    d["hostname"] = f"{base_name} (This device)"
                break
                
        if not local_device_found:
            scanned_results.append({
                "ip": target_ip_val,
                "mac": target_mac_val,
                "hostname": f"{socket.gethostname()} (This device)",
                "services": check_open_ports(target_ip_val)['services']
            })

    try:
        # 5. Database Write (Robust Sync Logic)
        ext_info = get_extended_iface_info()
        spec_info = ext_info.get(target_iface, {})
        gateway_ip = spec_info.get("gateway", "-")
        
        gateway_mac = None
        
        # Check if the router was already found in the broadcast scan
        for device in scanned_results:
            if device["ip"] == gateway_ip:
                gateway_mac = device["mac"]
                # Append a nice label if it was found natively
                if "(Router)" not in device["hostname"]:
                    device["hostname"] = f"{device['hostname']} (Router)"
                break
        
        # --- INJECT & LABEL ROUTER (Guaranteed) ---
        if not gateway_mac and gateway_ip != "-" and gateway_ip != "Unknown":
            # Try targeted ARP as a last resort for the MAC
            gateway_mac = get_gateway_mac(gateway_ip)
            
            # If targeted ARP also failed, assign a placeholder MAC so it still saves
            if not gateway_mac:
                gateway_mac = f"NO_MAC_{int(time.time()*1000)}"
            
            # Unconditionally inject it into the scanned list so it appears in the UI
            scanned_results.append({
                "ip": gateway_ip,
                "mac": gateway_mac,
                "hostname": f"{resolve_hostname(gateway_ip, gateway_mac)} (Router)",
                "services": check_open_ports(gateway_ip)['services']
            })
        
        # Failsafe if there is no gateway IP at all on the system
        if not gateway_mac:
            gateway_mac = f"NO_MAC_{int(time.time()*1000)}"

        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            cursor = conn.cursor()
            # UPDATED: Select both ID and Name to define final_network_name
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
                # Name sync logic (Local -> Global)
                cursor.execute("SELECT custom_name FROM devices WHERE mac_address=? AND network_id=?", (device["mac"], network_id))
                existing = cursor.fetchone()
                final_name = existing[0] if existing and existing[0] else ""
                
                if not final_name:
                    cursor.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (device["mac"],))
                    glob = cursor.fetchone()
                    if glob: final_name = glob[0]

                cursor.execute("""
                    INSERT INTO devices (mac_address, network_id, hostname, custom_name, ip_address, last_seen, services, is_online)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(mac_address, network_id) DO UPDATE SET
                    hostname=excluded.hostname, ip_address=excluded.ip_address, last_seen=excluded.last_seen,
                    services=excluded.services, custom_name=COALESCE(?, devices.custom_name), is_online=1
                """, (device["mac"], network_id, device["hostname"], final_name, device["ip"], current_time, device["services"], final_name))
            
            # FIXED INDENTATION: These run AFTER the loop finishes, not inside it
            conn.commit()
            return jsonify({"network_id": network_id, "network_name": final_network_name, "devices": scanned_results})
            
    except Exception as e: 
        return jsonify({"error": f"DB Error: {str(e)}"})
       
# --- NEW TOOLS: DNS & Ping ---

@app.route('/api/dns/lookup', methods=['POST'])
def dns_lookup():
    """Performs DNS lookup and logs with network context."""
    domain = request.json.get('domain')
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Get Context
    lan_ip, router_ip, net_name = get_current_network_context()

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
    
    return jsonify({"timestamp": ts, "domain": domain, "ip": ip, "status": status})

@app.route('/api/dns/logs')
def get_dns_logs():
    """Fetches the last 100 DNS lookup records."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/dns/clear', methods=['POST'])
def clear_dns_logs():
    """Clears the DNS history log."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM dns_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/ping/run', methods=['POST'])
def run_ping():
    """
    Executes a system ping (4 packets) and logs latency/loss.
    Now includes Network Context (Router IP, Name, LAN IP).
    """
    target = request.json.get('target')
    
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
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        logs = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(l) for l in logs])

@app.route('/api/ping/clear', methods=['POST'])
def clear_ping_logs():
    """Clears the Ping history log."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM ping_logs")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/export/tool_logs', methods=['POST'])
def export_tool_logs():
    """Exports DNS or Ping logs to CSV including new columns."""
    log_type = request.json.get('type')
    out = io.StringIO()
    writer = csv.writer(out)
    
    with sqlite3.connect(DB_NAME) as conn:
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

# --- Misc (WiFi, Speedtest, History, Update) ---
import re
import platform
import subprocess
import time
from flask import jsonify

@app.route('/api/wifi')
def get_wifi_networks():
    """
    Returns detailed Wi-Fi data grouped by SSID.
    Locks Band and MAC on the same line to fix HTML desync.
    Clusters and averages redundant 'Unknown MAC' signals within a 5dBm variance.
    Enforces strict 2.4GHz -> 5GHz -> 6GHz ordering.
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
                        
                        # Guarantee unique keys for missing MACs to collect all signals
                        mac_val = i.bssid()
                        mac = str(mac_val) if mac_val else f"Unknown_MAC_{id(i)}"
                        
                        sig = f"{i.rssiValue()} dBm" if i.rssiValue() else ""
                        
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
                            networks_dict[ssid]["bssids"][mac] = {"signal": sig, "channel": ch, "band": b}
                        else:
                            if sig: networks_dict[ssid]["bssids"][mac]["signal"] = sig
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
                            networks_dict[current_ssid]["bssids"][current_mac] = {"signal": "", "channel": "0", "band": "Unknown"}
                            
                    elif current_mac and "signal" in line.lower():
                        sig = line.split(":", 1)[1].strip()
                        networks_dict[current_ssid]["bssids"][current_mac]["signal"] = sig
                    
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
                    sig = f"{parts[2]}%" if parts[2] else ""
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
                        
                    networks_dict[ssid]["bssids"][mac] = {"signal": sig, "channel": ch, "band": b}

    except Exception as e: 
        return jsonify({"error": "Critical Error", "message": str(e)})

    # ==========================================
    # 4. Sorting, Merging & HTML Formatting
    # ==========================================
    final_networks = []
    
    def get_band_weight(band_str):
        if "2.4" in band_str: return 1
        if "5" in band_str: return 2
        if "6" in band_str: return 3
        return 4

    import re
    def extract_dbm(s):
        m = re.search(r'(-?\d+)', s)
        return int(m.group(1)) if m else -100

    for net in networks_dict.values():
        bands_dict = {}
        
        # 1. Bucket all MACs and signals by Band
        for mac, data in net["bssids"].items():
            b = data.get("band", "Unknown")
            sig = data.get("signal", "")
            ch = data.get("channel", "0")
            
            if b not in bands_dict:
                bands_dict[b] = {"known_macs": [], "unknown_signals": [], "channels": set()}
            
            if ch and ch != "0":
                bands_dict[b]["channels"].add(ch)
            
            if mac.startswith("Unknown_MAC_"):
                if sig: bands_dict[b]["unknown_signals"].append(sig)
            else:
                bands_dict[b]["known_macs"].append({"mac": mac, "signal": sig})
        
        display_bands = []
        display_signals = []
        display_channels = set()
        
        sorted_bands = sorted(bands_dict.keys(), key=get_band_weight)
        
        # 2. Build the output formatting
        for b in sorted_bands:
            data = bands_dict[b]
            
            # --- Unknown MACs: Apply 5dBm Clustering ---
            if data["unknown_signals"]:
                display_bands.append(f"{b} (Unknown MAC)")
                
                clusters = []
                for sig in data["unknown_signals"]:
                    sig_match = re.search(r'(-?\d+)', sig)
                    if not sig_match:
                        clusters.append({"signals": [], "raw_sig": sig})
                        continue
                        
                    sig_val = int(sig_match.group(1))
                    placed = False
                    
                    for cluster in clusters:
                        if cluster["signals"]:
                            avg_sig = sum(cluster["signals"]) / len(cluster["signals"])
                            if abs(sig_val - avg_sig) <= 5:
                                cluster["signals"].append(sig_val)
                                placed = True
                                break
                    
                    if not placed:
                        clusters.append({"signals": [sig_val], "raw_sig": sig})
                
                # Format averaged signals
                averaged_sigs = []
                for c in clusters:
                    if c["signals"]:
                        avg = int(round(sum(c["signals"]) / len(c["signals"])))
                        averaged_sigs.append(f"{avg} dBm")
                    else:
                        averaged_sigs.append(c.get("raw_sig", ""))
                
                # Sort from strongest to weakest
                sorted_sigs = sorted(averaged_sigs, key=extract_dbm, reverse=True)
                for sig in sorted_sigs:
                    display_signals.append(f"{sig} ({b})" if b != "Unknown" else sig)
                    
            # --- Known MACs: Kept individual ---
            for kmac in data["known_macs"]:
                display_bands.append(f"{b} ({kmac['mac']})")
                sig = kmac["signal"]
                if sig:
                    display_signals.append(f"{sig} ({b})" if b != "Unknown" else sig)
                    
            display_channels.update(data["channels"])
            
        sorted_channels = sorted(list(display_channels), key=lambda x: int(x) if str(x).isdigit() else 0)
        
        final_networks.append({
            "ssid": net["ssid"],
            "mac": "", # Left intentionally empty because it is now cleanly merged into the band column
            "signal": "<br>".join(display_signals),
            "channel": ", ".join(sorted_channels),
            "auth": net["auth"],
            "band": "<br>".join(display_bands)
        })

    # Auto-log scan to database
    if final_networks:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            auto_name = f"Auto-Scan {timestamp}"
            
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                c = conn.cursor()
                c.execute(
                    "INSERT INTO wifi_history (scan_name, comments, results_json) VALUES (?, ?, ?)",
                    (auto_name, "Automatically logged", json.dumps(final_networks))
                )
                conn.commit()
        except Exception:
            pass

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
        with sqlite3.connect(DB_NAME) as conn:
            # Check for Pinned Adapter first
            row = conn.execute("SELECT mac_address FROM adapter_settings WHERE is_primary = 1").fetchone()
            if row:
                pinned_mac = row[0]
            
            interfaces = psutil.net_if_addrs()
            
            # Find the interface name and IP
            for name, addrs in interfaces.items():
                temp_mac, temp_ip = None, None
                for a in addrs:
                    if a.family == psutil.AF_LINK: temp_mac = a.address
                    if a.family == socket.AF_INET: temp_ip = a.address
                
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
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (info['ip'],)).fetchone()
        return jsonify({"last_name": row[0] if row else "", "wan_ip": info['ip'], "isp": info['isp']})

@app.route('/api/history')
def get_history():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row  # THIS IS KEY
        cursor = conn.execute("SELECT * FROM history ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        # Convert sqlite objects to a list of dictionaries for JSON
        return jsonify([dict(ix) for ix in rows])

@app.route('/api/history/update', methods=['POST'])
def update_history():
    """Renames a history entry and updates its connection type."""
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE history SET network_name = ?, connection_type = ? WHERE id = ?", 
                     (d.get('name'), d.get('type'), d.get('id')))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/bulk_delete', methods=['POST'])
def bulk_delete():
    """Generic endpoint to bulk delete records across multiple tables."""
    d = request.json
    table_map = {
        'networks': 'networks',
        'wifi': 'wifi_history',
        'history': 'history',
        'dns': 'dns_logs',
        'ping': 'ping_logs'
    }
    table = table_map.get(d.get('type'))
    ids = d.get('ids', [])
    
    if not table or not ids:
        return jsonify({"error": "Invalid request parameters"}), 400
        
    placeholders = ','.join(['?'] * len(ids))
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        # Cascade delete devices if we are deleting networks
        if table == 'networks':
            conn.execute(f"DELETE FROM devices WHERE network_id IN ({placeholders})", ids)
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/networks/bulk_export', methods=['POST'])
def bulk_export_networks():
    """Generates a ZIP file containing multiple CSVs for selected Networks."""
    ids = request.json.get('ids', [])
    if not ids: return jsonify({"error": "No IDs provided"}), 400
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            for net_id in ids:
                net = conn.execute("SELECT name FROM networks WHERE id=?", (net_id,)).fetchone()
                if not net: continue
                
                net_name = net['name'].replace(" ", "_")
                devices = conn.execute("SELECT hostname, custom_name, ip_address, mac_address, is_online, services FROM devices WHERE network_id=?", (net_id,)).fetchall()
                
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                writer.writerow(['Hostname', 'Custom Name', 'IP Address', 'MAC Address', 'Status', 'Services'])
                for d in devices:
                    writer.writerow([d['hostname'], d['custom_name'], d['ip_address'], d['mac_address'], 'Online' if d['is_online'] else 'Offline', d['services']])
                
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
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            for scan_id in ids:
                scan = conn.execute("SELECT scan_name, results_json FROM wifi_history WHERE id=?", (scan_id,)).fetchone()
                if not scan: continue
                
                scan_name = scan['scan_name'].replace(" ", "_")
                results = json.loads(scan['results_json'])
                
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                writer.writerow(["SSID", "MAC(s)", "Signal", "Channel(s)", "Band(s)", "Authentication"])
                for net in results:
                    writer.writerow([net.get('ssid',''), net.get('mac', ''), net.get('signal',''), net.get('channel',''), net.get('band',''), net.get('auth','')])

                zf.writestr(f"wifi_scan_{scan_id}_{scan_name}.csv", csv_out.getvalue())
    
    memory_file.seek(0)
    return send_file(memory_file, download_name="wifi_scans_bulk_export.zip", as_attachment=True)

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clears all speed test history."""
    with sqlite3.connect(DB_NAME) as conn:
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
        with sqlite3.connect(DB_NAME) as conn:
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
    Uses 'IS' for NULL-safe comparisons and updates device metadata.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    uploaded_file = request.files['file']
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        uploaded_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        conn_local = sqlite3.connect(DB_NAME)
        conn_remote = sqlite3.connect(tmp_path)
        conn_remote.row_factory = sqlite3.Row
        
        cursor_l = conn_local.cursor()
        cursor_r = conn_remote.cursor()

        # 1. Merge Networks (ID Mapping)
        network_map = {} 
        remote_networks = cursor_r.execute("SELECT * FROM networks").fetchall()
        for net in remote_networks:
            # Use 'IS' to handle cases where Gateway MAC/IP might be NULL
            cursor_l.execute("SELECT id FROM networks WHERE gateway_mac IS ? AND gateway_ip IS ?", 
                             (net['gateway_mac'], net['gateway_ip']))
            exists = cursor_l.fetchone()
            if exists:
                network_map[net['id']] = exists[0]
            else:
                cursor_l.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip) VALUES (?, ?, ?, ?)",
                                 (net['gateway_mac'], net['name'], net['last_scan'], net['gateway_ip']))
                network_map[net['id']] = cursor_l.lastrowid

        # 2. Merge Devices (Updating metadata)
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

        # 3. Merge Logs (History, Ping, DNS)
        tables_to_append = {
            'history': ['timestamp', 'network_name', 'connection_type', 'download', 'upload', 'ping', 'wan_ip', 'device_ip', 'isp'],
            'dns_logs': ['timestamp', 'domain', 'result_ip', 'record_type', 'status', 'router_ip', 'network_name', 'lan_ip'],
            'ping_logs': ['timestamp', 'target', 'status', 'latency', 'packet_loss', 'network_context', 'router_ip', 'network_name', 'lan_ip'],
            'wifi_history': ['timestamp', 'scan_name', 'comments', 'results_json']
        }

        for table, cols in tables_to_append.items():
            remote_data = cursor_r.execute(f"SELECT * FROM {table}").fetchall()
            for row in remote_data:
                # NULL-safe duplicate check using 'IS'
                placeholders = " AND ".join([f"{c} IS ?" for c in cols])
                cursor_l.execute(f"SELECT 1 FROM {table} WHERE {placeholders}", [row[c] for c in cols])
                if not cursor_l.fetchone():
                    col_str = ", ".join(cols)
                    val_placeholders = ", ".join(["?" for _ in cols])
                    cursor_l.execute(f"INSERT INTO {table} ({col_str}) VALUES ({val_placeholders})", [row[c] for c in cols])

        # 4. Global Settings
        remote_global = cursor_r.execute("SELECT * FROM global_device_names").fetchall()
        for g in remote_global:
            cursor_l.execute("INSERT OR REPLACE INTO global_device_names (mac_address, custom_name) VALUES (?, ?)", 
                             (g['mac_address'], g['custom_name']))

        conn_local.commit()
        conn_local.close()
        conn_remote.close()
        os.remove(tmp_path)
        return jsonify({"status": "success", "message": "Database merged successfully!"})

    except Exception as e:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        return jsonify({"error": str(e)}), 500

# --- Updater (GitHub Integration) ---

def fetch_github_file(filename):
    """Fetches raw file content from private GitHub repo."""
    url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/contents/{filename}?ref={GITHUB_SETTINGS['branch']}"
    req = urllib.request.Request(url)
    if GITHUB_SETTINGS["token"]: 
        req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
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

@app.route('/api/update/apply', methods=['POST'])
def update_software():
    """
    Cross-Platform Update Mechanism:
    Sets full Read/Write/Execute (777) permissions for all users.
    """
    try:
        if 'GITHUB_SETTINGS' not in globals(): return jsonify({"error": "No GitHub settings."}), 500
        print(f"[*] Starting Update on {platform.system()}...")
        
        # 1. Download
        req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/zipball/{GITHUB_SETTINGS['branch']}")
        if GITHUB_SETTINGS.get('token'): req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
        
        try:
            with urllib.request.urlopen(req) as response: zip_data = io.BytesIO(response.read())
        except Exception as e: return jsonify({"error": f"Download failed: {e}"}), 500

        # 2. Extract & Install
        import tempfile
        base_dir = app.root_path
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(zip_data) as zip_ref:
                root_name = zip_ref.namelist()[0].split('/')[0]
                zip_ref.extractall(temp_dir)
                source_root = os.path.join(temp_dir, root_name)
                
                for root, dirs, files in os.walk(source_root):
                    rel_path = os.path.relpath(root, source_root)
                    dest_dir = os.path.join(base_dir, rel_path)
                    
                    # Create directory and set 777
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
                            
                            # Apply Full R/W/X Permissions
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
        conn = sqlite3.connect('network_data.db')
        c = conn.cursor()
        
        # Determine the name: Use provided name, or fallback to current timestamp
        raw_name = data.get('name', '').strip()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scan_name = raw_name if raw_name else f"Scan {timestamp}"
        
        # Comments can be empty (None or empty string)
        comments = data.get('comments', '').strip()

        c.execute("INSERT INTO wifi_history (scan_name, comments, results_json) VALUES (?, ?, ?)",
                  (scan_name, comments, json.dumps(data.get('results', []))))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "saved_as": scan_name})
    except Exception as e:
        print(f"[X] Database Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wifi/history', methods=['GET'])
def get_wifi_history():
    conn = sqlite3.connect('network_data.db')
    c = conn.cursor()
    c.execute("SELECT id, timestamp, scan_name, comments FROM wifi_history ORDER BY timestamp DESC")
    rows = c.fetchall()
    history = [{"id": r[0], "timestamp": r[1], "name": r[2], "comments": r[3]} for r in rows]
    conn.close()
    return jsonify(history)

@app.route('/api/wifi/history/<int:scan_id>', methods=['GET'])
def load_wifi_scan(scan_id):
    conn = sqlite3.connect('network_data.db')
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
    conn = sqlite3.connect('network_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM wifi_history WHERE id = ?", (scan_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

@app.route('/api/wifi/export/<int:scan_id>')
def export_wifi_csv(scan_id):
    try:
        conn = sqlite3.connect('network_data.db')
        c = conn.cursor()
        c.execute("SELECT scan_name, results_json FROM wifi_history WHERE id = ?", (scan_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return "Scan not found", 404

        scan_name = row[0].replace(" ", "_")
        results = json.loads(row[1])

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header Row

        writer.writerow(["SSID", "MAC(s)", "Signal", "Channel(s)", "Band(s)", "Authentication"])
        for net in results:
            writer.writerow([
                net.get('ssid', 'Unknown'),
                net.get('mac', '-'),
                net.get('signal', '-'),
                net.get('channel', '-'),
                net.get('band', '-'),
                net.get('auth', '-')
            ])

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
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM wifi_history")
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
        writer.writerow(["SSID", "MAC(s)", "Signal", "Channel(s)", "Band(s)", "Authentication"])
        for net in results:
            writer.writerow([
                net.get('ssid', 'Unknown'),
                net.get('mac', '-'),
                net.get('signal', '-'),
                net.get('channel', '-'),
                net.get('band', '-'),
                net.get('auth', '-')
            ])

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
    new_name = data.get('name', '').strip()
    new_comment = data.get('comment', '').strip()
    
    # Fallback for name if left empty
    if not new_name:
        new_name = f"Scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    try:
        # Use 'network_data.db' to match your other wifi_history functions
        with sqlite3.connect('network_data.db') as conn:
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
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM connection_types ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/settings/connection_types/add', methods=['POST'])
def add_connection_type():
    """Adds a new custom connection type."""
    name = request.json.get('name', '').strip()
    if not name: return jsonify({"error": "Name required"}), 400
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT INTO connection_types (name) VALUES (?)", (name,))
            conn.commit()
        return jsonify({"status": "success"})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Type already exists"}), 400

@app.route('/api/settings/connection_types/delete', methods=['POST'])
def delete_connection_type():
    """Removes a connection type from the list."""
    type_id = request.json.get('id')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM connection_types WHERE id=?", (type_id,))
        conn.commit()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    cleanup_old_files()
    
    # Try to use the production-ready Waitress server
    try:
            from waitress import serve
            print("\n" + "="*60)
            print(f"   DASHBOARD ACTIVE: http://0.0.0.0:81")
            print("   (Production WSGI Server - No Warnings)")
            print("="*60 + "\n")
            serve(app, host='0.0.0.0', port=81, threads=24)
    except ImportError:
       # Fallback to dev server if waitress isn't installed yet
        app.run(debug=True, host='0.0.0.0', port=81)