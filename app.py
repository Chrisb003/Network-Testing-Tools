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

# --- Configuration ---
APP_VERSION = "0.3.0" # Version bumped for DNS/Ping tools

# GITHUB CONFIGURATION
# Ensure your Personal Access Token (PAT) has 'repo' scope
GITHUB_SETTINGS = {
    "owner": "Pancool",
    "repo": "Network-Testing-Tools",
    "token": "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",
    "branch": "main"
}

app = Flask(__name__)
DB_NAME = "speedtest.db"

# --- Database & Migrations ---
def init_db():
    """Initializes the database with WAL mode for concurrency and creates all tables."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Enable Write-Ahead Logging (Fixes 'database is locked' errors during scans)
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
                        is_visible INTEGER DEFAULT 1
                    )''')

        # 3. Networks Table (Stores known networks by Gateway MAC)
        c.execute('''CREATE TABLE IF NOT EXISTS networks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        gateway_mac TEXT UNIQUE,
                        name TEXT,
                        last_scan TEXT,
                        gateway_ip TEXT
                    )''')

        # 4. Devices Table (Per-Network)
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
        
        # 5. Global Device Names (Remembers names across ALL networks)
        c.execute('''CREATE TABLE IF NOT EXISTS global_device_names (
                        mac_address TEXT PRIMARY KEY,
                        custom_name TEXT
                    )''')
        
        # 6. DNS Logs (New)
        c.execute('''CREATE TABLE IF NOT EXISTS dns_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        domain TEXT,
                        result_ip TEXT,
                        record_type TEXT,
                        status TEXT
                    )''')
        
        # 7. Ping Logs (New)
        c.execute('''CREATE TABLE IF NOT EXISTS ping_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        target TEXT,
                        status TEXT, 
                        latency TEXT,
                        packet_loss TEXT,
                        network_context TEXT
                    )''')
        
        # Migrations for existing users
        try:
            c.execute("SELECT isp FROM history LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE history ADD COLUMN isp TEXT")
        try:
            c.execute("SELECT connection_type FROM history LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT")
            
        conn.commit()

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

def get_extended_iface_info():
    """Gathers Gateway and DNS details for network interfaces."""
    info_map = {}
    system = platform.system()
    try:
        if system == "Windows":
            output = subprocess.check_output(["ipconfig", "/all"], text=True)
            curr = None
            for line in output.split('\n'):
                line = line.strip()
                if "adapter" in line and ":" in line:
                    curr = line.split("adapter")[-1].replace(":", "").strip()
                    info_map[curr] = {"gateway": "-", "dns": "-"}
                if curr:
                    if "Default Gateway" in line and ":" in line: 
                        info_map[curr]["gateway"] = line.split(":")[1].strip()
                    elif "DNS Servers" in line and ":" in line: 
                        info_map[curr]["dns"] = line.split(":")[1].strip()
                        
        elif system == "Darwin": # macOS
            gateway = "-"
            try:
                route_out = subprocess.check_output(["netstat", "-nr"], text=True)
                for line in route_out.split('\n'):
                    if "default" in line:
                        parts = line.split()
                        if len(parts) > 1: gateway = parts[1]; break
            except: pass
            
            dns = "-"
            try:
                dns_out = subprocess.check_output(["scutil", "--dns"], text=True)
                for line in dns_out.split('\n'):
                    if "nameserver[0]" in line: dns = line.split(":")[1].strip(); break
            except: pass
            info_map["global"] = {"gateway": gateway, "dns": dns}
        else:
            info_map["global"] = {"gateway": "-", "dns": "-"}
    except: pass
    return info_map

# --- Bandwidth Tracking ---
last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

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
    """Finds the interface matching the local IP, ignoring loopback."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        target_ip = s.getsockname()[0]
    except:
        target_ip = '127.0.0.1'
    finally:
        s.close()

    interfaces = psutil.net_if_addrs()
    for iface_name, addrs in interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == target_ip and not addr.address.startswith("127."):
                return iface_name
    return None

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

# --- Routes ---
@app.route('/')
def index():
    info = get_isp_info()
    ext_info = get_extended_iface_info()
    
    active_ext = ext_info.get("global", {"gateway": "Unknown", "dns": "Unknown"})
    if platform.system() == "Windows":
        for adapter in ext_info.values():
            if adapter.get("gateway") != "-":
                active_ext = adapter
                break

    return render_template('dashboard.html', 
                           local_ip=get_local_ip(), 
                           wan_ip=info['ip'], 
                           isp_name=info['isp'],
                           router_ip=active_ext.get("gateway", "Unknown"),
                           dns_servers=active_ext.get("dns", "Unknown"),
                           global_version=get_global_version(), 
                           setup_version=get_setup_version(), 
                           app_version=APP_VERSION)

@app.route('/api/adapters')
def get_adapters():
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    ext_info = get_extended_iface_info()
    
    primary_gw = "Unknown"
    primary_dns = "Unknown"
    
    settings = {}
    with sqlite3.connect(DB_NAME) as conn:
        for row in conn.execute("SELECT mac_address, custom_name, is_visible FROM adapter_settings"):
            settings[row[0]] = {"name": row[1], "visible": row[2]}

    for name, addrs in interfaces.items():
        st = stats.get(name)
        ip4, ip6, mac = "-", "-", "-"
        for a in addrs:
            if a.family == socket.AF_INET: ip4 = a.address
            elif a.family == socket.AF_INET6: ip6 = a.address.split('%')[0]
            elif a.family == psutil.AF_LINK: mac = a.address

        gw = ext_info.get(name, {}).get("gateway", "-")
        dns = ext_info.get(name, {}).get("dns", "-")
        
        if gw == "-" and st and st.isup:
             gw = ext_info.get("global", {}).get("gateway", "-")
             dns = ext_info.get("global", {}).get("dns", "-")

        if st and st.isup and gw != "-" and primary_gw == "Unknown":
            primary_gw = gw
            primary_dns = dns

        user_name = name
        is_vis = True
        key = mac if (mac and mac != "-") else name
        if key in settings:
            if settings[key]["name"]: user_name = settings[key]["name"]
            is_vis = bool(settings[key]["visible"])
            
        adapters_data.append({
            "id": name, "name": user_name, "mac": mac, 
            "status": "Active" if (st and st.isup) else "Inactive",
            "ip4": ip4, "gateway": gw, "dns": dns,
            "speed": f"{st.speed} Mbps" if (st and st.speed > 0) else "N/A",
            "visible": is_vis
        })
        
    return jsonify({
        "adapters": adapters_data, 
        "global_speed": get_bandwidth(),
        "primary_router": primary_gw, 
        "primary_dns": primary_dns
    })

@app.route('/api/adapters/update', methods=['POST'])
def update_adapter_settings():
    d = request.json
    key = d.get('mac') if (d.get('mac') and d.get('mac') != "-") else d.get('id')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("REPLACE INTO adapter_settings (mac_address, custom_name, is_visible) VALUES (?, ?, ?)", 
                     (key, d.get('name'), 1 if d.get('visible') else 0))
        conn.commit()
    return jsonify({"status": "success"})

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
    Parallel Network Scan:
    1. Detects Active Interface (Fixes Wi-Fi).
    2. Sends ARP Packets.
    3. Multithreaded Hostname/Port resolution (Speed fix).
    4. Database Update (Timeout protection).
    """
    # 1. Prepare Interface
    active_iface = get_active_interface_name()
    if not active_iface: return jsonify({"error": "No active interface found."})
    
    # Force Scapy to use the active interface
    conf.iface = active_iface
    
    local_ip = get_local_ip()
    target_ip = f"{local_ip.rsplit('.', 1)[0]}.0/24"
    
    # 2. Identify Gateway
    ext_info = get_extended_iface_info()
    gateway_ip = ext_info.get("global", {}).get("gateway", "-")
    if platform.system() == "Windows" and gateway_ip == "-":
        for adapter in ext_info.values():
            if adapter.get("gateway") != "-": gateway_ip = adapter.get("gateway"); break

    gateway_mac = get_gateway_mac(gateway_ip)
    if not gateway_mac: gateway_mac = "UNKNOWN_GATEWAY"
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scanned_results = []

    try:
        # 3. Physical Scan (ARP)
        # inter=0.02 is optimized for speed without flooding Wi-Fi
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_ip), 
                     iface=active_iface, timeout=2, verbose=0, inter=0.02)
        
        # 4. Parallel Processing (Hostname/Ports)
        # This uses the process_device_info helper defined above
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(process_device_info, received) for _, received in ans]
            for future in futures: scanned_results.append(future.result())
            
    except Exception as e: return jsonify({"error": f"Scan failed: {str(e)}"})

    try:
        # 5. Database Write (With Timeout Protection)
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

            # Mark all offline initially
            cursor.execute("UPDATE devices SET is_online=0 WHERE network_id=?", (network_id,))

            for device in scanned_results:
                # Check Local Name
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
            
            # Fetch & Return Sorted List
            cursor.row_factory = sqlite3.Row
            devices = cursor.execute("SELECT * FROM devices WHERE network_id=?", (network_id,)).fetchall()
            cursor.execute("SELECT name FROM networks WHERE id=?", (network_id,))
            net_name = cursor.fetchone()[0]

            dev_list = [dict(d) for d in devices]
            try: dev_list.sort(key=lambda x: ipaddress.IPv4Address(x['ip_address']))
            except: pass

            return jsonify({"network_id": network_id, "network_name": net_name, "devices": dev_list})
    except Exception as e: return jsonify({"error": f"DB Error: {str(e)}"})

# --- NEW TOOLS: DNS & Ping ---

@app.route('/api/dns/lookup', methods=['POST'])
def dns_lookup():
    """
    Performs a DNS A-record lookup and logs the result.
    """
    domain = request.json.get('domain')
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # Standard socket lookup
        ip = socket.gethostbyname(domain)
        status = "Resolved"
    except:
        ip = "-"
        status = "Failed"
    
    # Log to Database
    with sqlite3.connect(DB_NAME, timeout=5) as conn:
        conn.execute("INSERT INTO dns_logs (timestamp, domain, result_ip, record_type, status) VALUES (?, ?, ?, ?, ?)",
                     (ts, domain, ip, "A", status))
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

    try:
        # Run the ping command and capture output
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        
        # Parse output for Latency and Packet Loss
        if platform.system().lower() == 'windows':
            if "Average =" in output:
                latency = output.split("Average =")[1].strip().replace("ms", "").strip() + " ms"
            if "Lost =" in output:
                loss_part = output.split("Lost =")[1].split("(")[1]
                loss = loss_part.split(")")[0] # e.g., "0% loss"
        else:
            # Linux / macOS parsing
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
    
    # Context: Which network were we on?
    try:
        with sqlite3.connect(DB_NAME) as conn:
            row = conn.execute("SELECT name FROM networks ORDER BY last_scan DESC LIMIT 1").fetchone()
            net_name = row[0] if row else "Unknown"
    except: 
        net_name = "Unknown"

    # Log to Database
    with sqlite3.connect(DB_NAME, timeout=5) as conn:
        conn.execute("INSERT INTO ping_logs (timestamp, target, status, latency, packet_loss, network_context) VALUES (?, ?, ?, ?, ?, ?)",
                     (ts, target, status, latency, loss, net_name))
        conn.commit()

    return jsonify({"timestamp": ts, "target": target, "status": status, "latency": latency, "loss": loss})

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
    """Exports DNS or Ping logs to CSV file."""
    log_type = request.json.get('type') # 'dns' or 'ping'
    out = io.StringIO()
    writer = csv.writer(out)
    
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        if log_type == 'dns':
            rows = conn.execute("SELECT * FROM dns_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Domain', 'Result IP', 'Record Type', 'Status'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['domain'], r['result_ip'], r['record_type'], r['status']])
        else:
            rows = conn.execute("SELECT * FROM ping_logs ORDER BY id DESC").fetchall()
            writer.writerow(['Timestamp', 'Target', 'Status', 'Latency', 'Packet Loss', 'Network Context'])
            for r in rows: 
                writer.writerow([r['timestamp'], r['target'], r['status'], r['latency'], r['packet_loss'], r['network_context']])
            
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": f"attachment; filename={log_type}_logs.csv"})

# --- Misc (WiFi, Speedtest, History, Update) ---

@app.route('/api/wifi')
def get_wifi_networks():
    """Scans for available Wi-Fi networks using OS-specific CLI tools."""
    networks = []
    sys_plat = platform.system()
    try:
        if sys_plat == "Windows":
            output = subprocess.check_output(["netsh", "wlan", "show", "network", "mode=bssid"], text=True)
            ssid = ""
            for line in output.split('\n'):
                line = line.strip()
                if line.startswith("SSID"): 
                    ssid = line.split(":")[1].strip()
                elif line.startswith("Signal") and ssid:
                    networks.append({"ssid": ssid, "signal": line.split(":")[1].strip()})
                    ssid = ""

        elif sys_plat == "Darwin": # macOS
            try:
                # Attempt 1: NetworkSetup (Modern macOS)
                iface_out = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
                wifi_iface = "en0" # Default
                if "Wi-Fi" in iface_out:
                    lines = iface_out.split('\n')
                    for i, line in enumerate(lines):
                        if "Wi-Fi" in line: 
                            # The device identifier is usually on the next line
                            wifi_iface = lines[i+1].split(":")[1].strip()
                            break
                
                output = subprocess.check_output(["networksetup", "-getavailablenetworks", wifi_iface], text=True)
                for line in output.strip().split('\n'):
                    if line.strip() and "Available networks" not in line: 
                        networks.append({"ssid": line.strip(), "signal": "N/A"}) # NetworkSetup doesn't provide signal strength

            except subprocess.CalledProcessError as e:
                # Error 5 means permission denied (Location Services)
                if e.returncode == 5: 
                    return jsonify({"error": "Permission Denied: Please Enable Location Services for Terminal/Python."})
                
                # Attempt 2: Airport Utility (Legacy/Fallback)
                airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
                if os.path.exists(airport_path):
                    output = subprocess.check_output([airport_path, "-s"], text=True)
                    lines = output.strip().split('\n')
                    for line in lines[1:]:
                        parts = line.split()
                        if len(parts) >= 3: 
                            networks.append({"ssid": parts[0], "signal": f"{parts[2]} dBm"})

    except Exception as e: 
        return jsonify({"error": str(e)})
        
    return jsonify(networks)

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    """Runs the Speedtest CLI and logs to history."""
    d = request.json
    try:
        cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
        res = json.loads(subprocess.check_output(cmd, text=True))
        
        down = f"{res['download']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        up = f"{res['upload']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', 'Unknown')
        
        name = d.get('network_name') if d.get('network_name') else "Unknown"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT INTO history (timestamp, network_name, connection_type, download, upload, ping, wan_ip, isp) VALUES (?,?,?,?,?,?,?,?)",
                         (ts, name, d.get('connection_type'), down, up, ping, wan, isp))
            conn.commit()
            
        return jsonify({"download": down, "upload": up, "ping": ping, "network": name, "isp": isp, "wan_ip": wan})
    except Exception as e: 
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
    """Returns speed test history."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return jsonify([dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()])

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
    """Exports current device list to CSV."""
    d = request.json
    rows = d.get('rows', [])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Hostname', 'IP Address', 'MAC Address', 'Services'])
    for r in rows: 
        writer.writerow([r.get('hostname'), r.get('ip'), r.get('mac'), r.get('services')])
    
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
    content = fetch_github_file("README.md")
    return jsonify({"status": "success", "changelog": content}) if content else jsonify({"status": "error"})

@app.route('/api/update/apply', methods=['POST'])
def apply_update():
    """Placeholder for applying updates."""
    return jsonify({"status": "success", "message": "Update initiated. Please restart manually."})

if __name__ == '__main__':
    # Set to port 81 per your configuration
    app.run(debug=True, host='0.0.0.0', port=81)