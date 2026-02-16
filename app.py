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
from flask import Flask, render_template, jsonify, Response, request
from scapy.all import ARP, Ether, srp, conf
import sys
import zipfile
from pathlib import Path
import shutil
import threading
from flask import jsonify

# --- Configuration ---
APP_VERSION = "0.6.5" # Version bumped for DNS/Ping tools

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
        c.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT, 
                        network_name TEXT, 
                        connection_type TEXT,
                        download TEXT, 
                        upload TEXT, 
                        ping TEXT, 
                        wan_ip TEXT, 
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
                        gateway_mac TEXT UNIQUE,
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

        # --- 8. NEW: Wi-Fi Scan History Table ---
        c.execute('''CREATE TABLE IF NOT EXISTS wifi_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        scan_name TEXT,
                        comments TEXT,
                        results_json TEXT
                    )''')
        
        # --- MIGRATIONS (Updates existing databases safely) ---
        
        # Existing Migrations
        try: c.execute("ALTER TABLE history ADD COLUMN isp TEXT"); 
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT"); 
        except sqlite3.OperationalError: pass
        
        try: c.execute("ALTER TABLE adapter_settings ADD COLUMN is_visible INTEGER DEFAULT 1"); 
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE adapter_settings ADD COLUMN is_primary INTEGER DEFAULT 0"); 
        except sqlite3.OperationalError: pass

        for table in ['dns_logs', 'ping_logs']:
            for col in ['router_ip', 'network_name', 'lan_ip']:
                try: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError: pass 

        # --- Migration for Wi-Fi History (In case table exists but missing columns) ---
        # Note: Since this is a new table, the 'CREATE TABLE IF NOT EXISTS' handles most cases,
        # but if you add columns later, place them here.

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

def restart_server():
    """Restarts the current Python script."""
    print("[*] Triggering application restart...")
    time.sleep(1)  # Give the server a moment to flush the HTTP response
    python = sys.executable
    os.execl(python, python, *sys.argv)

def get_extended_iface_info():
    """
    Fetches Gateway and DNS information across Windows, macOS, and Linux.
    Prioritizes stable text-parsing (ipconfig/netstat) to prevent data flickering.
    Strictly filters for IPv4 (no colons).
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
                    
                    # Identify the start of an adapter section (e.g., "Ethernet adapter Ethernet:")
                    if "adapter" in line and ":" in line:
                        # Extract name between 'adapter' and the trailing colon
                        parts = line.split("adapter")
                        if len(parts) > 1:
                            current_iface = parts[-1].split(":")[0].strip()
                            if current_iface not in info:
                                info[current_iface] = {"gateway": "-", "dns": "-"}
                    
                    if current_iface:
                        # Extract Default Gateway
                        if "Default Gateway" in line and ":" in line:
                            gw = line.split(":")[-1].strip()
                            # Ensure it's not empty and is IPv4 (no colons)
                            if gw and "." in gw and ":" not in gw:
                                info[current_iface]["gateway"] = gw
                        
                        # Extract DNS Servers
                        if "DNS Servers" in line and ":" in line:
                            dns = line.split(":")[-1].strip()
                            if dns and "." in dns and ":" not in dns:
                                info[current_iface]["dns"] = dns
            except Exception as e:
                print(f"ipconfig failed: {e}")

            # --- FALLBACK: PowerShell (Only if ipconfig failed to find an interface) ---
            if not info:
                try:
                    cmd = "Get-NetIPConfiguration | Select-Object InterfaceAlias, " \
                          "@{Name='G';Expression={$_.IPv4DefaultGateway.NextHop}}, " \
                          "@{Name='D';Expression={$_.DNSServer.ServerAddresses}} | ConvertTo-Json"
                    out = subprocess.check_output(["powershell", "-Command", cmd], text=True, timeout=5)
                    data = json.loads(out)
                    adapters = [data] if isinstance(data, dict) else data
                    for item in adapters:
                        name = item.get('InterfaceAlias')
                        if not name: continue
                        
                        gw_raw = item.get('G', "-")
                        gw = str(gw_raw[0]) if isinstance(gw_raw, list) and len(gw_raw) > 0 else str(gw_raw)
                        
                        dns_raw = item.get('D', [])
                        dns_list = [str(d) for d in (dns_raw if isinstance(dns_raw, list) else [dns_raw]) 
                                    if d and ":" not in str(d) and "MSFT_" not in str(d)]
                        
                        info[name] = {
                            "gateway": gw if (gw != "None" and ":" not in gw) else "-",
                            "dns": ", ".join(dns_list) if dns_list else "-"
                        }
                except:
                    pass

        elif system == "Darwin": # macOS
            try:
                # Gateway via netstat
                gw_out = subprocess.check_output("netstat -rn -f inet | grep 'default'", shell=True, text=True)
                default_gw = "-"
                for line in gw_out.split('\n'):
                    parts = line.split()
                    if len(parts) >= 2 and ":" not in parts[1]:
                        default_gw = parts[1]
                        break
                
                # DNS and Interface Mapping via networksetup
                port_out = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
                lines = port_out.split('\n')
                for i, line in enumerate(lines):
                    if "Hardware Port" in line:
                        port_name = line.split(":")[1].strip()
                        dev_name = lines[i+1].split(":")[1].strip()
                        try:
                            dns_out = subprocess.check_output(["networksetup", "-getdnsservers", port_name], text=True)
                            dns_list = [d.strip() for d in dns_out.split('\n') if d.strip() and ":" not in d]
                            dns_val = ", ".join(dns_list) if dns_list and "Any" not in dns_list[0] else "-"
                        except: dns_val = "-"
                        info[dev_name] = {"gateway": default_gw, "dns": dns_val}
            except: pass

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
    """Calculates real-time network throughput."""
    global last_received, last_sent, last_time
    curr_recv = psutil.net_io_counters().bytes_recv
    curr_sent = psutil.net_io_counters().bytes_sent
    curr_time = time.time()
    
    delta = curr_time - last_time
    if delta <= 0: delta = 1
    
    down = (curr_recv - last_received) / delta
    up = (curr_sent - last_sent) / delta
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

def resolve_hostname(ip):
    """Resolves hostname with a strict timeout to avoid Wi-Fi hangs."""
    try:
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1) # Strict 1s timeout
        name = socket.gethostbyaddr(ip)[0]
        socket.setdefaulttimeout(default_timeout)
        if name and not name.startswith(ip): return name
    except:
        socket.setdefaulttimeout(default_timeout if 'default_timeout' in locals() else None)
    
    # macOS/Linux ARP Cache Fallback
    try:
        arp_out = subprocess.check_output(["arp", "-a"], text=True)
        for line in arp_out.split('\n'):
            if ip in line:
                match = re.search(r'^(\S+)\s+\(', line)
                if match:
                    name = match.group(1)
                    if name != "?" and name != ip: return name
    except: pass
    return "Unknown Device"

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
    """Resolves Gateway MAC to identify unique networks."""
    if not gateway_ip or gateway_ip == "-" or gateway_ip == "Unknown": return None
    try:
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=gateway_ip), timeout=2, verbose=0)
        for _, received in ans: return received.hwsrc
    except: pass
    return None

def process_device_info(received):
    """
    Worker function for threaded scanning. 
    MUST be defined before scan_network calls it.
    """
    ip = received.psrc
    mac = received.hwsrc
    return {
        "ip": ip,
        "mac": mac,
        "hostname": resolve_hostname(ip),
        "services": check_open_ports(ip)['services']
    }

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

@app.route('/api/adapters')
def get_adapters():
    """
    Fetches all network adapters with:
    1. Stable IPv4 data from ipconfig.
    2. Pinned Adapter logic (Header locks to user choice).
    3. Hardware link speeds with a fallback to 'Not Available' if idle.
    4. Real-time Wi-Fi rates only when active traffic exists.
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

        # Get stable Gateway and DNS from the ipconfig parser
        spec_info = ext_info.get(name, {})
        
        # Windows Cross-reference (Handle alias vs full name)
        if not spec_info and platform.system() == "Windows":
             for k, v in ext_info.items():
                 if k in name or name in k:
                     spec_info = v
                     break

        # Extract values and filter for IPv4
        gw = spec_info.get("gateway", "-")
        if ":" in gw: gw = "-"
        
        dns = spec_info.get("dns", "-")
        if ":" in dns: dns = "-"

        # --- HEADER PINNING LOGIC ---
        is_pinned = (mac == pinned_mac) if pinned_mac else False

        # Fix: Check if active_iface_name exists before doing the 'in' comparison
        is_active_default = False
        if not pinned_mac and active_iface_name:
            is_active_default = (name == active_iface_name or name in active_iface_name)

        # --- SPEED LOGIC (Corrected for 'Not Available') ---
        # 1. Start with the hardware negotiated speed
        raw_speed = st.speed if st else 0
        display_speed = "Not Available"
        
        if raw_speed > 0:
            if raw_speed >= 1000:
                display_speed = f"{raw_speed/1000:g} Gbps"
            else:
                display_speed = f"{raw_speed} Mbps"
        
        # 2. Override with Real-time Wi-Fi rates (only if get_wifi_rates detected traffic)
        for wifi_name, rate_str in wifi_rates.items():
            if wifi_name.lower() in name.lower() or name.lower() in wifi_name.lower():
                # If rate_str is valid, it overrides the link speed
                display_speed = rate_str

        # Final sanity check to avoid '0 Mbps' strings
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
                primary_dns = v.get("dns", "-")
                break
        
    return jsonify({
        "adapters": adapters_data, 
        "primary_router": primary_gw, 
        "primary_dns": primary_dns
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
                # Target en0 which is usually the default Wi-Fi interface
                out = subprocess.check_output(["ipconfig", "getsummary", "en0"], text=True)
                tx_match = re.search(r'transmitRate\s+:\s+(\d+)', out)
                if tx_match: rates["en0"] = f"{tx_match.group(1)} Mbps"
            except: pass

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

@app.route('/api/system/update', methods=['POST'])
def update_software():
    """
    Downloads the latest code from GitHub, handles Windows file locking,
    updates the files, and restarts the application.
    """
    try:
        # Ensure GITHUB_SETTINGS is available (defined at top of app.py)
        if 'GITHUB_SETTINGS' not in globals():
            return jsonify({"error": "GitHub settings not configured."}), 500

        print("[*] Starting Update Process...")
        
        # 1. Download the ZIP from Private Repo
        zip_url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/zipball/{GITHUB_SETTINGS['branch']}"
        req = urllib.request.Request(zip_url)
        req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
        req.add_header("Accept", "application/vnd.github.v3+json")

        # Buffer the download in memory
        with urllib.request.urlopen(req) as response:
            zip_data = io.BytesIO(response.read())

        base_dir = app.root_path

        # 2. Extract and Overwrite
        with zipfile.ZipFile(zip_data) as zip_ref:
            # GitHub zips have a dynamic top-level folder (e.g. 'Pancool-Repo-a1b2c')
            # We need to find it and strip it.
            root_folder = zip_ref.namelist()[0]
            
            for member in zip_ref.infolist():
                # Skip the root folder itself
                if member.filename == root_folder:
                    continue
                
                # Strip the root folder from the path
                # Example: 'Pancool-Repo-123/templates/index.html' -> 'templates/index.html'
                rel_path = member.filename[len(root_folder):]
                
                # Security check: Don't allow climbing up directories
                if ".." in rel_path or not rel_path:
                    continue

                target_path = os.path.join(base_dir, rel_path)

                # Handle Directories
                if member.is_dir():
                    os.makedirs(target_path, exist_ok=True)
                    continue

                # Handle Files
                # Ensure the parent directory exists
                os.makedirs(os.path.dirname(target_path), exist_ok=True)

                try:
                    # Attempt standard overwrite
                    with zip_ref.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                        
                except PermissionError:
                    # WINDOWS FIX: File is locked (likely app.py)
                    if platform.system() == "Windows":
                        print(f"[!] File locked: {rel_path}. Attempting rename-and-replace...")
                        try:
                            # Rename the running file to .old (Windows allows this)
                            old_backup = target_path + ".old"
                            if os.path.exists(old_backup):
                                os.remove(old_backup) # Remove previous backup if exists
                            
                            os.replace(target_path, old_backup)
                            
                            # Now write the new file to the original name
                            with zip_ref.open(member) as source, open(target_path, "wb") as target:
                                shutil.copyfileobj(source, target)
                            print(f"[✓] Successfully patched locked file: {rel_path}")
                        except Exception as rename_err:
                            print(f"[X] Critical Update Error for {rel_path}: {rename_err}")
                            return jsonify({"error": f"Failed to update locked file {rel_path}"}), 500
                    else:
                        # Linux/Mac usually allow overwriting running files, so this is a real permission issue
                        print(f"[X] Permission Denied: {rel_path}")
                        return jsonify({"error": f"Permission denied for {rel_path}"}), 500

        print("[✓] Update extracted successfully.")
        
        # 3. Trigger Background Restart
        # We start a thread to restart the server AFTER this function returns 200 OK
        threading.Thread(target=restart_server).start()

        return jsonify({
            "status": "success", 
            "message": "Update installed. Server restarting..."
        })

    except Exception as e:
        print(f"[X] Update Failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/scan_network')
def scan_network():
    """
    Robust Network Scan:
    1. Tries to find the active interface/gateway.
    2. Fallback: If gateway fails, guesses subnet based on Local IP (e.g. 192.168.1.0/24).
    3. Runs ARP scan.
    """
    # 1. Prepare Interface & IP
    active_iface = get_active_interface_name()
    local_ip = get_local_ip()
    
    # Calculate Target Subnet (The "Failsafe" that fixes your issue)
    # If Local IP is 192.168.1.50, this makes the target 192.168.1.0/24
    if local_ip and local_ip != "127.0.0.1":
        target_ip = f"{local_ip.rsplit('.', 1)[0]}.0/24"
    else:
        return jsonify({"error": "Could not determine local IP subnet."})

    # 2. Configure Scapy (Try to use active interface, but don't crash if it fails)
    if active_iface:
        conf.iface = active_iface

    # 3. Identify Gateway (Visual only - doesn't stop the scan anymore)
    ext_info = get_extended_iface_info()
    gateway_ip = "-"
    
    # Try to find gateway from the extended info we built earlier
    for iface_details in ext_info.values():
        if iface_details.get("gateway") and iface_details.get("gateway") != "-":
            gateway_ip = iface_details.get("gateway")
            break
            
    gateway_mac = get_gateway_mac(gateway_ip) if gateway_ip != "-" else None
    if not gateway_mac: gateway_mac = "UNKNOWN_GATEWAY"
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scanned_results = []

    try:
        # 4. Physical Scan (ARP)
        # We scan the calculated subnet (target_ip) regardless of whether we found a gateway
        # inter=0.02 avoids flooding Wi-Fi networks
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_ip), 
                     timeout=2, verbose=0, inter=0.02)
        
        # 5. Parallel Processing
        # Uses the 'process_device_info' helper helper function defined earlier in app.py
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(process_device_info, received) for _, received in ans]
            for future in futures: scanned_results.append(future.result())
            
    except Exception as e: 
        print(f"SCAN ERROR: {e}")
        return jsonify({"error": f"Scan failed: {str(e)}"})

    try:
        # 6. Database Write (With Timeout Protection)
        with sqlite3.connect(DB_NAME, timeout=10) as conn:
            cursor = conn.cursor()
            
            # Upsert Network
            cursor.execute("SELECT id FROM networks WHERE gateway_mac=?", (gateway_mac,))
            row = cursor.fetchone()
            if row:
                network_id = row[0]
                cursor.execute("UPDATE networks SET last_scan=?, gateway_ip=? WHERE id=?", 
                               (current_time, gateway_ip, network_id))
            else:
                default_name = f"Network {gateway_mac[-5:]}" if gateway_mac != "UNKNOWN_GATEWAY" else "Unknown Network"
                cursor.execute("INSERT INTO networks (gateway_mac, name, last_scan, gateway_ip) VALUES (?, ?, ?, ?)", 
                               (gateway_mac, default_name, current_time, gateway_ip))
                network_id = cursor.lastrowid

            # Mark all offline initially (so we can see who is currently online)
            cursor.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))

            for device in scanned_results:
                # Name sync logic (Preserve custom names)
                cursor.execute("SELECT custom_name FROM devices WHERE mac_address=? AND network_id=?", 
                               (device["mac"], network_id))
                existing = cursor.fetchone()
                final_name = existing[0] if existing and existing[0] else ""
                
                # Check Global Name if no local name
                if not final_name:
                    cursor.execute("SELECT custom_name FROM global_device_names WHERE mac_address=?", (device["mac"],))
                    glob = cursor.fetchone()
                    if glob: final_name = glob[0]

                # Upsert Device
                cursor.execute("""
                    INSERT INTO devices (mac_address, network_id, hostname, custom_name, ip_address, last_seen, services, is_online)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(mac_address, network_id) DO UPDATE SET
                    hostname=excluded.hostname,
                    ip_address=excluded.ip_address,
                    last_seen=excluded.last_seen,
                    services=excluded.services,
                    custom_name=COALESCE(?, devices.custom_name),
                    is_online=1
                """, (device["mac"], network_id, device["hostname"], final_name, device["ip"], 
                      current_time, device["services"], final_name))
            
            conn.commit()
            
            # 7. Fetch & Return Sorted List
            cursor.row_factory = sqlite3.Row
            devices = cursor.execute("SELECT * FROM devices WHERE network_id=?", (network_id,)).fetchall()
            cursor.execute("SELECT name FROM networks WHERE id=?", (network_id,))
            net_name_row = cursor.fetchone()
            net_name = net_name_row[0] if net_name_row else "Unknown Network"

            dev_list = [dict(d) for d in devices]
            try: dev_list.sort(key=lambda x: ipaddress.IPv4Address(x['ip_address']))
            except: pass

            return jsonify({"network_id": network_id, "network_name": net_name, "devices": dev_list})
            
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

import re
import platform
import subprocess
import time
from flask import jsonify

@app.route('/api/wifi')
def get_wifi_networks():
    """
    Returns detailed Wi-Fi data grouped by SSID and automatically
    logs every successful scan to the database history.
    """
    networks_dict = {}
    sys_plat = platform.system()
    
    try:
        if sys_plat == "Darwin":
            return jsonify({"error": "Incompatible", "message": "macOS location privacy locks Wi-Fi scanning."})

        elif sys_plat == "Windows":
            # Force hardware refresh (Requires Admin)
            subprocess.run(["powershell", "-Command", "Get-NetAdapter | Where-Object {$_.MediaType -eq 'Native 802.11'} | Restart-NetAdapter"], capture_output=True)
            time.sleep(4) 

            process = subprocess.Popen(
                "netsh wlan show networks mode=bssid", 
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                text=True, encoding='cp437', errors='ignore'
            )
            stdout, _ = process.communicate(timeout=15)

            current_ssid = None
            current_ch = None 
            
            for line in stdout.split('\n'):
                line = line.strip()
                if not line: continue

                if line.lower().startswith("ssid"):
                    parts = line.split(":", 1)
                    current_ssid = parts[1].strip() if len(parts) > 1 else "Hidden Network"
                    if current_ssid not in networks_dict:
                        networks_dict[current_ssid] = {"ssid": current_ssid, "signal": [], "channel": [], "auth": "Unknown", "band": []}

                elif current_ssid:
                    if "authentication" in line.lower():
                        networks_dict[current_ssid]["auth"] = line.split(":", 1)[1].strip()
                    
                    elif "signal" in line.lower():
                        sig = line.split(":", 1)[1].strip()
                        if sig not in networks_dict[current_ssid]["signal"]:
                            networks_dict[current_ssid]["signal"].append(sig)
                    
                    elif "channel" in line.lower():
                        ch = line.split(":", 1)[1].strip()
                        if re.match(r"^\d{1,3}$", ch):
                            current_ch = int(ch)
                            if ch not in networks_dict[current_ssid]["channel"]:
                                networks_dict[current_ssid]["channel"].append(ch)
                            
                            band_label = ""
                            if 1 <= current_ch <= 14: band_label = "2.4GHz"
                            elif 32 <= current_ch <= 177: band_label = "5GHz"
                            elif current_ch >= 190: band_label = "6GHz"
                            
                            if band_label and band_label not in networks_dict[current_ssid]["band"]:
                                networks_dict[current_ssid]["band"].append(band_label)

        elif sys_plat == "Linux":
            output = subprocess.check_output(["nmcli", "-t", "-f", "SSID,SIGNAL,CHAN,SECURITY", "dev", "wifi"], text=True)
            for line in output.strip().split('\n'):
                parts = line.split(":")
                if len(parts) >= 4:
                    ssid = parts[0] or "Hidden Network"
                    ch = int(parts[2]) if parts[2].isdigit() else 0
                    if ssid not in networks_dict:
                        networks_dict[ssid] = {"ssid": ssid, "signal": [], "channel": [], "auth": parts[3], "band": []}
                    
                    if f"{parts[1]}%" not in networks_dict[ssid]["signal"]: networks_dict[ssid]["signal"].append(f"{parts[1]}%")
                    if str(ch) not in networks_dict[ssid]["channel"]: networks_dict[ssid]["channel"].append(str(ch))
                    
                    b = "2.4GHz" if ch <= 14 else "5GHz" if ch <= 177 else "6GHz"
                    if b not in networks_dict[ssid]["band"]: networks_dict[ssid]["band"].append(b)

    except Exception as e: 
        return jsonify({"error": "Critical Error", "message": str(e)})

    # Final result processing
    final_networks = []
    for net in networks_dict.values():
        net["channel"].sort(key=int)
        net["band"].sort()
        
        final_networks.append({
            "ssid": net["ssid"],
            "signal": ", ".join(net["signal"]),
            "channel": ", ".join(net["channel"]),
            "auth": net["auth"],
            "band": ", ".join(net["band"])
        })

    # --- AUTOMATIC HISTORY LOGGING ---
    if final_networks:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            auto_name = f"Auto-Scan {timestamp}"
            
            # Inside @app.route('/api/wifi') ...
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                c = conn.cursor()
                # Ensure the column names match your init_db (scan_name, comments, results_json)
                c.execute(
                    "INSERT INTO wifi_history (scan_name, comments, results_json) VALUES (?, ?, ?)",
                    (auto_name, "Automatically logged", json.dumps(final_networks))
                )
                conn.commit()
            print(f"[✓] Wi-Fi scan auto-logged: {auto_name}")
        except Exception as db_err:
            print(f"[!] Database Auto-log Error: {db_err}")

    return jsonify(final_networks)

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    """
    Executes an Ookla Speedtest using the CLI binary located in the virtual environment.
    Automatically handles path resolution for Windows, macOS, and Linux.
    """
    d = request.json
    try:
        # 1. Determine Absolute Path to the CLI Binary
        # app.root_path provides the directory where app.py is located
        base_dir = app.root_path 
        
        if platform.system() == "Windows":
            # Windows venv structure uses 'Scripts'
            st_path = os.path.join(base_dir, "venv", "Scripts", "speedtest.exe")
        else:
            # macOS and Linux venv structure uses 'bin'
            st_path = os.path.join(base_dir, "venv", "bin", "speedtest")

        # 2. Check if the binary exists; if not, fall back to global 'speedtest' command
        # This is the CRITICAL "Fallback" line I missed in the shorter version
        cmd_path = st_path if os.path.exists(st_path) else "speedtest"
        
        # 3. Execute the Speedtest
        # --accept-license and --accept-gdpr are required for non-interactive execution
        cmd = [cmd_path, "--format=json", "--accept-license", "--accept-gdpr"]
        res = json.loads(subprocess.check_output(cmd, text=True))
        
        # 4. Format the Resulting Data
        down = f"{(res['download']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        up = f"{(res['upload']['bandwidth'] * 8) / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', '-')
        
        name = d.get('network_name') or "Unnamed Network"
        conn_type = d.get('connection_type') or "Ethernet"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 5. Log to the Database
        try:
            with sqlite3.connect(DB_NAME, timeout=10) as conn:
                # Ensure PRAGMA journal_mode=WAL is active for concurrency
                conn.execute("PRAGMA journal_mode=WAL;") 
                conn.execute("""
                    INSERT INTO history (timestamp, network_name, connection_type, download, upload, ping, wan_ip, isp) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (ts, name, conn_type, down, up, ping, wan, isp))
                conn.commit()
        except sqlite3.Error as db_err:
            print(f"[X] Database Logging Error: {db_err}")
            # We still return the results even if the database logging fails
            
        return jsonify({"download": down, "upload": up, "ping": ping})

    except subprocess.CalledProcessError as e:
        print(f"[X] Speedtest Execution Error: {e}")
        return jsonify({"error": "Speedtest CLI failed to execute. Ensure it is installed correctly."})
    except FileNotFoundError:
        return jsonify({"error": "Speedtest binary not found. Please run setup.py again."})
    except Exception as e:
        print(f"[X] Unexpected Speedtest Error: {e}")
        return jsonify({"error": str(e)})

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
    """Renames a history entry."""
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE history SET network_name = ? WHERE id = ?", (d.get('name'), d.get('id')))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clears all speed test history."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM history")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/history/export', methods=['POST'])
def export_history():
    """Exports speed test history to CSV."""
    d = request.json
    rows = d.get('rows', [])
    if not rows:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()]
    
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Timestamp', 'Network Name', 'Type', 'Download', 'Upload', 'Ping', 'WAN IP', 'ISP'])
    for r in rows: 
        writer.writerow([r.get('timestamp'), r.get('network_name'), r.get('connection_type'), r.get('download'), r.get('upload'), r.get('ping'), r.get('wan_ip'), r.get('isp')])
    
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=history.csv"})

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
    """Checks for version mismatch."""
    content = fetch_github_file("version.json")
    if content:
        try: 
            remote = json.loads(content)
            return jsonify({
                "status": "success", 
                "remote_version": remote.get("version", "0.0.0"), 
                "app_version": APP_VERSION, 
                "global_version": get_global_version()
            })
        except: pass
    return jsonify({"status": "error"})

@app.route('/api/update/changelog')
def get_changelog():
    """Fetches Release Notes."""
    content = fetch_github_file("Changelog")
    return jsonify({"status": "success", "changelog": content}) if content else jsonify({"status": "error"})

@app.route('/api/update/apply', methods=['POST'])
def apply_update():
    """Placeholder for applying updates."""
    return jsonify({"status": "success", "message": "Update initiated. Please restart manually."})

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
        writer.writerow(["SSID", "Signal", "Channel(s)", "Band(s)", "Authentication"])
        
        # Data Rows
        for net in results:
            writer.writerow([
                net.get('ssid', 'Unknown'),
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
        writer.writerow(["SSID", "Signal", "Channel(s)", "Band(s)", "Authentication"])
        
        for net in results:
            writer.writerow([
                net.get('ssid', 'Unknown'),
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

if __name__ == '__main__':
    cleanup_old_files()
    # Set to port 81 per your configuration
    app.run(debug=True, host='0.0.0.0', port=81)