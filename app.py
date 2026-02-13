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
from datetime import datetime
from flask import Flask, render_template, jsonify, Response, request
from scapy.all import ARP, Ether, srp

# --- Configuration ---
# Version for this specific script
APP_VERSION = "0.1.0"

# GITHUB CONFIGURATION (Private Repository Access)
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
    """Initializes the database and ensures all required columns exist."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # History Table: Stores speed test results
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
        
        # Adapter Settings Table: Stores persistent custom names and visibility
        c.execute('''CREATE TABLE IF NOT EXISTS adapter_settings (
                        mac_address TEXT PRIMARY KEY, 
                        custom_name TEXT, 
                        is_visible INTEGER DEFAULT 1
                    )''')
        
        # Migrations: Add ISP and Connection Type if they were missing from older DB versions
        try:
            c.execute("SELECT isp FROM history LIMIT 1")
        except sqlite3.OperationalError:
            print("[*] Migrating database: Adding ISP column...")
            c.execute("ALTER TABLE history ADD COLUMN isp TEXT")
            
        try:
            c.execute("SELECT connection_type FROM history LIMIT 1")
        except sqlite3.OperationalError:
            print("[*] Migrating database: Adding connection_type column...")
            c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT")
            
        conn.commit()

init_db()

# --- Versioning Helpers ---
def get_setup_version():
    """Reads SETUP_VERSION from setup_env.py using regex to avoid execution."""
    try:
        if os.path.exists("setup_env.py"):
            with open("setup_env.py", "r") as f:
                content = f.read()
                match = re.search(r'SETUP_VERSION\s*=\s*["\']([^"\']+)["\']', content)
                return match.group(1) if match else "Unknown"
        return "Not Found"
    except:
        return "Error"

def get_global_version():
    """Reads the current global version from the local version.json file."""
    try:
        if os.path.exists("version.json"):
            with open("version.json", "r") as f:
                data = json.load(f)
                return data.get("version", "0.0.0")
        return "0.0.0"
    except:
        return "Error"

# --- System & Network Helpers ---
def get_isp_info():
    """Fetches Public WAN IP and ISP name for the dashboard header."""
    try:
        # Use ip-api for both IP and ISP metadata in one request
        with urllib.request.urlopen('http://ip-api.com/json/?fields=query,isp', timeout=3) as url:
            data = json.loads(url.read().decode())
            return {
                "ip": data.get("query", "Unknown"), 
                "isp": data.get("isp", "Unknown ISP")
            }
    except:
        return {"ip": "Unknown", "isp": "Unknown ISP"}

def get_current_network_name():
    """Detects current Wi-Fi SSID based on the Operating System."""
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True)
            for line in out.split("\n"):
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":")[1].strip()
        elif system == "Darwin": # macOS
            cmd = ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"]
            out = subprocess.check_output(cmd, text=True)
            for line in out.split("\n"):
                if " SSID:" in line:
                    return line.split(":")[1].strip()
        elif system == "Linux":
            out = subprocess.check_output("nmcli -t -f active,ssid dev wifi | grep ^yes", shell=True, text=True)
            return out.split(":")[1].strip()
    except:
        pass
    return "Ethernet/Unknown"

def get_local_ip():
    """Identifies the primary local LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually connect, just identifies interface used to route to 8.8.8.8
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except:
        return '127.0.0.1'
    finally:
        s.close()

def get_extended_iface_info():
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
                        
        elif system == "Darwin":  # macOS Logic
            # 1. Get Default Gateway (Router IP)
            # We look for the 'default' route in the routing table
            gateway = "-"
            try:
                # 'netstat -nr' shows the routing table; we look for the default gateway
                route_output = subprocess.check_output(["netstat", "-nr"], text=True)
                for line in route_output.split('\n'):
                    if "default" in line:
                        parts = line.split()
                        if len(parts) > 1:
                            gateway = parts[1]
                            break
            except: pass

            # 2. Get DNS Servers
            dns_servers = "-"
            try:
                # 'scutil --dns' provides detailed DNS configuration
                dns_output = subprocess.check_output(["scutil", "--dns"], text=True)
                # We typically want the first nameserver listed in Resolver #1
                for line in dns_output.split('\n'):
                    if "nameserver[0]" in line:
                        dns_servers = line.split(":")[1].strip()
                        break
            except: pass

            # On Mac, these are usually global settings for the active interface
            info_map["global"] = {"gateway": gateway, "dns": dns_servers}
            
        else: # Linux Fallback
            info_map["global"] = {"gateway": "-", "dns": "-"}
            
    except Exception as e:
        print(f"Error fetching extended info: {e}")
    return info_map

# --- Bandwidth Tracking Logic ---
# Global variables to store last known byte counts
last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

def get_bandwidth():
    """Calculates real-time network throughput in MB/s."""
    global last_received, last_sent, last_time
    curr_received = psutil.net_io_counters().bytes_recv
    curr_sent = psutil.net_io_counters().bytes_sent
    curr_time = time.time()
    
    delta = curr_time - last_time
    if delta <= 0: delta = 1
    
    # Calculate bytes per second and convert to MegaBytes
    down = (curr_received - last_received) / delta
    up = (curr_sent - last_sent) / delta
    
    last_received, last_sent, last_time = curr_received, curr_sent, curr_time
    return {
        "download": f"{down / 1024 / 1024:.2f} MB/s", 
        "upload": f"{up / 1024 / 1024:.2f} MB/s"
    }

# --- Device Discovery & Port Scanning ---
def resolve_hostname(ip):
    """
    Improved hostname resolution for macOS.
    Tries system resolver first, then falls back to the system ARP cache.
    """
    # Method 1: Standard System Resolver
    try:
        name = socket.gethostbyaddr(ip)[0]
        if name and not name.startswith(ip):
            return name
    except:
        pass

    # Method 2: System ARP Cache (Often has names on macOS)
    try:
        # Run 'arp -a' and look for the IP
        arp_output = subprocess.check_output(["arp", "-a"], text=True)
        for line in arp_output.split('\n'):
            if ip in line:
                # Format is usually: hostname (ip) at mac_address ...
                # Example: router (192.168.1.1) at 0:1:2:3:4:5
                match = re.search(r'^(\S+)\s+\(', line)
                if match:
                    name = match.group(1)
                    if name != "?" and name != ip:
                        return name
    except:
        pass

    return "Unknown Device"

def check_open_ports(ip):
    """Scans specific ports to identify web interfaces or SSH services."""
    services = []
    has_web = False
    web_port = None
    
    # Port mapping: 80, 443, 8080, and 8443 allow for clickable web-links
    web_ports = {80: "HTTP", 443: "HTTPS", 8080: "HTTP-Alt", 8443: "HTTPS-Alt"}
    common_ports = {22: "SSH"}
    common_ports.update(web_ports)
    
    for port, name in common_ports.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1) # Aggressive timeout for faster scanning
        if s.connect_ex((ip, port)) == 0:
            services.append(name)
            if port in web_ports:
                has_web = True
                # Set port for the clickable link; prefer standard port 80
                if not web_port or port == 80:
                    web_port = port
        s.close()
    
    return {
        "services": ", ".join(services) if services else "None", 
        "has_web": has_web, 
        "web_port": web_port
    }

# --- Update Logic (Private GitHub Fetching) ---
def fetch_github_file(filename):
    """Fetches raw file content from the private GitHub repository using the PAT."""
    url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/contents/{filename}?ref={GITHUB_SETTINGS['branch']}"
    req = urllib.request.Request(url)
    
    if GITHUB_SETTINGS["token"]:
        req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            # API returns file content as Base64 encoded string
            return base64.b64decode(data['content']).decode('utf-8')
    except urllib.error.HTTPError as e:
        print(f"[!] GitHub API Error: {e.code}")
        return None
    except Exception as e:
        print(f"[!] Update Check Failed: {str(e)}")
        return None

# --- Routes ---
@app.route('/')
def index():
    info = get_isp_info()
    ext_info = get_extended_iface_info()
    
    # Extract Gateway and DNS from the 'global' or active interface
    # This logic assumes the helper returns 'global' for Mac/Linux or specific names for Windows
    active_ext = ext_info.get("global", {"gateway": "Unknown", "dns": "Unknown"})
    if platform.system() == "Windows":
        # Find the first adapter with a gateway if 'global' isn't used
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
    """
    Fetches all network interfaces, merges them with persistent DB settings,
    calculates live global bandwidth, and identifies primary Router/DNS info.
    """
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    extended_info = get_extended_iface_info()
    
    # Initialize variables for the dynamic Header update
    primary_gateway = "Unknown"
    primary_dns = "Unknown"
    
    # Load user-defined settings (Custom Names & Visibility) from the database
    settings_map = {}
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT mac_address, custom_name, is_visible FROM adapter_settings")
            for row in c.fetchall():
                # Keyed by MAC address (or System ID for Loopback)
                settings_map[row[0]] = {"custom_name": row[1], "is_visible": row[2]}
    except Exception as e:
        print(f"Database error in get_adapters: {e}")

    # Iterate through all hardware and virtual interfaces detected by the OS
    for iface_name, addrs in interfaces.items():
        iface_stats = stats.get(iface_name)
        
        # Basic status and speed
        is_up = "Active" if (iface_stats and iface_stats.isup) else "Inactive"
        link_speed = f"{iface_stats.speed} Mbps" if (iface_stats and iface_stats.speed > 0) else "N/A"
        
        # Extract IP and MAC addresses
        ip4, ip6, mac = "-", "-", "-"
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip4 = addr.address
            elif addr.family == socket.AF_INET6:
                ip6 = addr.address.split('%')[0] # Clean IPv6 scope ID
            elif addr.family == psutil.AF_LINK:
                mac = addr.address

        # Retrieve Gateway and DNS from extended system info
        # Windows uses specific names; Mac/Linux typically fall back to 'global'
        gateway = extended_info.get(iface_name, {}).get("gateway", "-")
        dns = extended_info.get(iface_name, {}).get("dns", "-")
        
        if gateway == "-" and is_up == "Active":
            gateway = extended_info.get("global", {}).get("gateway", "-")
            dns = extended_info.get("global", {}).get("dns", "-")

        # If this interface is active and has a gateway, nominate it for the Header display
        if is_up == "Active" and gateway != "-" and primary_gateway == "Unknown":
            primary_gateway = gateway
            primary_dns = dns

        # Apply User Customization (DB Lookup)
        # Use MAC as primary key; fallback to Interface Name for No-MAC adapters (Loopback)
        user_name = iface_name
        is_visible = True
        db_key = mac if (mac and mac != "-") else iface_name
        
        if db_key in settings_map:
            if settings_map[db_key]["custom_name"]: 
                user_name = settings_map[db_key]["custom_name"]
            is_visible = bool(settings_map[db_key]["is_visible"])
            
        # Append the processed adapter data
        adapters_data.append({
            "id": iface_name,
            "name": user_name,
            "mac": mac,
            "status": is_up, 
            "ip4": ip4, 
            "ip6": ip6, 
            "gateway": gateway,
            "dns": dns,
            "speed": link_speed,
            "visible": is_visible
        })
        
    # Final combined response: Table Data + Live Usage + Header Info
    return jsonify({
        "adapters": adapters_data, # The list of interfaces
        "global_speed": get_bandwidth(),
        "primary_router": primary_gateway,
        "primary_dns": primary_dns
    })

@app.route('/api/adapters/update', methods=['POST'])
def update_adapter_settings():
    """Saves custom adapter name and visibility to the database."""
    try:
        data = request.json
        mac = data.get('mac')
        sys_id = data.get('id')
        name = data.get('name')
        visible = 1 if data.get('visible') else 0
        
        # Use MAC as primary key, System ID as fallback for No-MAC adapters
        key = mac if (mac and mac != "-") else sys_id

        if not key:
            return jsonify({"error": "Cannot identify adapter"})

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO adapter_settings (mac_address, custom_name, is_visible) VALUES (?, ?, ?)", 
                      (key, name, visible))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/scan_network')
def scan_network():
    """Performs LAN scan with Reverse DNS and Port Discovery."""
    local_ip = get_local_ip()
    target_ip = f"{local_ip.rsplit('.', 1)[0]}.0/24"
    devices = []
    try:
        ans, unans = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target_ip), timeout=2, verbose=0)
        for sent, received in ans:
            ip = received.psrc
            scan_info = check_open_ports(ip)
            devices.append({
                'ip': ip, 
                'mac': received.hwsrc,
                'hostname': resolve_hostname(ip),
                'services': scan_info['services'],
                'has_web': scan_info['has_web'],
                'web_port': scan_info['web_port']
            })
    except Exception as e:
        return jsonify({"error": f"Scan failed: {str(e)}"})
    return jsonify(devices)

@app.route('/api/wifi')
def get_wifi_networks():
    """
    Scans for available Wi-Fi networks.
    Handles macOS 'Status 5' (Access Denied) by suggesting permission fixes.
    """
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

        elif sys_plat == "Darwin":  # macOS Logic
            # Check for the airport utility first
            airport_paths = [
                "/usr/sbin/airport",
                "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
            ]
            
            for path in airport_paths:
                if os.path.exists(path):
                    try:
                        output = subprocess.check_output([path, "-s"], text=True)
                        lines = output.strip().split('\n')
                        for line in lines[1:]:
                            parts = line.split()
                            if len(parts) >= 3:
                                networks.append({"ssid": parts[0], "signal": f"{parts[2]} dBm"})
                        return jsonify(networks)
                    except subprocess.CalledProcessError:
                        continue

            # Fallback to networksetup
            try:
                # Detect active Wi-Fi interface
                iface_out = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
                wifi_iface = "en0"
                if "Wi-Fi" in iface_out:
                    lines = iface_out.split('\n')
                    for i, line in enumerate(lines):
                        if "Wi-Fi" in line:
                            wifi_iface = lines[i+1].split(":")[1].strip()
                            break
                
                output = subprocess.check_output(["networksetup", "-getavailablenetworks", wifi_iface], text=True)
                for line in output.strip().split('\n'):
                    ssid = line.strip()
                    if ssid and "Available networks" not in ssid:
                        networks.append({"ssid": ssid, "signal": "N/A"})
            except subprocess.CalledProcessError as e:
                if e.returncode == 5:
                    return jsonify({"error": "Permission Denied: Enable Location Services for Terminal/Python."})
                return jsonify({"error": f"macOS Error: {str(e)}"})

        elif sys_plat == "Linux":
            cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi"]
            out = subprocess.check_output(cmd, text=True)
            for line in out.strip().split('\n'):
                p = line.split(':')
                if len(p) >= 2: 
                    networks.append({"ssid": p[0], "signal": p[1] + "%", "security": p[2] if len(p) > 2 else "N/A"})
                    
    except Exception as e:
        return jsonify({"error": str(e)})
        
    return jsonify(networks)

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    """Runs the official CLI and saves detailed results to history."""
    d = request.json
    try:
        cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
        res = json.loads(subprocess.check_output(cmd, text=True))
        
        down = f"{res['download']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        up = f"{res['upload']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', 'Unknown')
        
        # Priority: Manual Name > Auto-Detected Name
        name = d.get('network_name') if d.get('network_name') else get_current_network_name()
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
    """Finds the most recent network label used for the current WAN IP."""
    info = get_isp_info()
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (info['ip'],)).fetchone()
        return jsonify({"last_name": row[0] if row else "", "wan_ip": info['ip'], "isp": info['isp']})

@app.route('/api/history')
def get_history():
    """Returns the speed test history."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return jsonify([dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()])

@app.route('/api/history/update', methods=['POST'])
def update_history():
    """Allows manual editing of network labels in past test results."""
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE history SET network_name = ? WHERE id = ?", (d.get('name'), d.get('id')))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Wipes all speed test results."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM history")
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/history/export', methods=['POST'])
def export_history():
    """Generates a CSV of speed test results."""
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
    """Generates a CSV of discovered LAN devices."""
    d = request.json
    rows = d.get('rows', [])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Hostname', 'IP Address', 'MAC Address', 'Services'])
    for r in rows:
        writer.writerow([r.get('hostname'), r.get('ip'), r.get('mac'), r.get('services')])
    
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=devices.csv"})

@app.route('/api/update/check')
def check_update():
    """Checks the private GitHub repo for version differences."""
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
    return jsonify({"status": "error", "message": "Could not connect to GitHub"})

@app.route('/api/update/changelog')
def get_changelog():
    """Fetches README.md content to display as the changelog."""
    content = fetch_github_file("README.md")
    if content:
        return jsonify({"status": "success", "changelog": content})
    return jsonify({"status": "error", "message": "Failed to fetch README"})

@app.route('/api/update/apply', methods=['POST'])
def apply_update():
    """Placeholder for update automation."""
    return jsonify({"status": "success", "message": "Update initiated. Please restart the app manually."})

if __name__ == '__main__':
    # Set to port 81 per your current configuration
    app.run(debug=True, host='0.0.0.0', port=81)