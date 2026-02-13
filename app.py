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
from datetime import datetime
from flask import Flask, render_template, jsonify, Response, request
from scapy.all import ARP, Ether, srp

# --- Configuration ---
APP_VERSION = "0.1.0"

# GITHUB CONFIGURATION (EDIT THESE)
GITHUB_SETTINGS = {
    "owner": "Pancool",     # e.g. "johndoe"
    "repo": "Network-Testing-Tools",      # e.g. "network-dashboard"
    "token": "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", # Personal Access Token (Keep this secret!)
    "branch": "main"
}

app = Flask(__name__)
DB_NAME = "speedtest.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
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
        c.execute('''CREATE TABLE IF NOT EXISTS adapter_settings (
                        mac_address TEXT PRIMARY KEY,
                        custom_name TEXT,
                        is_visible INTEGER DEFAULT 1
                    )''')
        # Migrations
        try: c.execute("SELECT isp FROM history LIMIT 1")
        except: 
            try: c.execute("ALTER TABLE history ADD COLUMN isp TEXT")
            except: pass
        try: c.execute("SELECT connection_type FROM history LIMIT 1")
        except: 
            try: c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT")
            except: pass
        conn.commit()

init_db()

def get_isp_info():
    try:
        with urllib.request.urlopen('http://ip-api.com/json/?fields=query,isp', timeout=3) as url:
            data = json.loads(url.read().decode())
            return {"ip": data.get("query", "Unknown"), "isp": data.get("isp", "Unknown ISP")}
    except:
        return {"ip": "Unknown", "isp": "Unknown ISP"}

def get_current_network_name():
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True)
            for line in out.split("\n"):
                if "SSID" in line and "BSSID" not in line: return line.split(":")[1].strip()
        elif system == "Darwin":
            cmd = ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"]
            out = subprocess.check_output(cmd, text=True)
            for line in out.split("\n"):
                if " SSID:" in line: return line.split(":")[1].strip()
        elif system == "Linux":
            try:
                out = subprocess.check_output(["iwgetid", "-r"], text=True).strip()
                if out: return out
            except: pass
            out = subprocess.check_output("nmcli -t -f active,ssid dev wifi | grep ^yes", shell=True, text=True)
            return out.split(":")[1].strip()
    except: pass
    return "Ethernet/Unknown"

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except: ip = '127.0.0.1'
    finally: s.close()
    return ip

def get_extended_iface_info():
    info_map = {}
    system = platform.system()
    try:
        if system == "Windows":
            output = subprocess.check_output(["ipconfig", "/all"], text=True)
            current_adapter = None
            for line in output.split('\n'):
                line = line.strip()
                if "adapter" in line and ":" in line:
                    current_adapter = line.split("adapter")[-1].replace(":", "").strip()
                    info_map[current_adapter] = {"gateway": "-", "dns": "-"}
                if current_adapter:
                    if "Default Gateway" in line:
                        parts = line.split(":")
                        if len(parts) > 1 and parts[1].strip(): info_map[current_adapter]["gateway"] = parts[1].strip()
                    elif "DNS Servers" in line:
                        parts = line.split(":")
                        if len(parts) > 1 and parts[1].strip(): info_map[current_adapter]["dns"] = parts[1].strip()
        elif system == "Linux":
            try:
                output = subprocess.check_output(["nmcli", "dev", "show"], text=True)
                current_device = None
                data = {}
                for line in output.split('\n'):
                    if "GENERAL.DEVICE:" in line:
                        if current_device: info_map[current_device] = data
                        current_device = line.split(":")[1].strip()
                        data = {"gateway": "-", "dns": "-"}
                    if "IP4.GATEWAY:" in line: data["gateway"] = line.split(":")[1].strip()
                    elif "IP4.DNS[1]:" in line: data["dns"] = line.split(":")[1].strip()
                if current_device: info_map[current_device] = data
            except:
                dns = "-"
                try:
                    with open("/etc/resolv.conf", "r") as f:
                        for l in f:
                            if l.startswith("nameserver"): dns = l.split()[1]; break
                except: pass
                info_map["global"] = {"gateway": "-", "dns": dns}
        elif system == "Darwin":
            dns = "-"
            try:
                out = subprocess.check_output(["scutil", "--dns"], text=True)
                for line in out.split('\n'):
                    if "nameserver[0]" in line: dns = line.split(":")[1].strip(); break
            except: pass
            gateway = "-"
            try:
                out = subprocess.check_output(["netstat", "-nr"], text=True)
                for line in out.split('\n'):
                    if "default" in line: parts = line.split();  
                    if len(parts) > 1: gateway = parts[1]; break
            except: pass
            info_map["global"] = {"gateway": gateway, "dns": dns}
    except: pass
    return info_map

last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

def get_bandwidth():
    global last_received, last_sent, last_time
    current_received = psutil.net_io_counters().bytes_recv
    current_sent = psutil.net_io_counters().bytes_sent
    current_time = time.time()
    time_delta = current_time - last_time
    if time_delta == 0: time_delta = 1
    down_speed = (current_received - last_received) / time_delta
    up_speed = (current_sent - last_sent) / time_delta
    last_received = current_received
    last_sent = current_sent
    last_time = current_time
    return {"download": f"{down_speed / 1024 / 1024:.2f} MB/s", "upload": f"{up_speed / 1024 / 1024:.2f} MB/s"}

# --- Device Info Helpers ---
def resolve_hostname(ip):
    try: return socket.gethostbyaddr(ip)[0]
    except: return "Unknown"

def check_open_ports(ip):
    services = []
    has_web = False
    web_port = None
    
    # Extended Port Map
    common_ports = {
        22: "SSH",
        80: "HTTP",
        443: "HTTPS",
        8080: "HTTP-Alt",
        8443: "HTTPS-Alt"
    }
    
    for port, name in common_ports.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15) # Fast timeout
        result = s.connect_ex((ip, port))
        if result == 0:
            services.append(name)
            if port in [80, 443, 8080, 8443]:
                has_web = True
                # Prefer standard ports for the link
                if not web_port: web_port = port 
                elif port == 80: web_port = 80
                elif port == 443 and web_port != 80: web_port = 443
        s.close()
    
    return {"services": ", ".join(services) if services else "None", "has_web": has_web, "web_port": web_port}

# --- UPDATE LOGIC ---
def fetch_github_file(filename):
    """Fetches a file content from private GitHub repo."""
    url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/contents/{filename}?ref={GITHUB_SETTINGS['branch']}"
    req = urllib.request.Request(url)
    if GITHUB_SETTINGS["token"]:
        req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            # GitHub API returns content in base64
            content = base64.b64decode(data['content']).decode('utf-8')
            return content
    except urllib.error.HTTPError as e:
        return None
    except Exception as e:
        return None
    
# --- Routes ---

@app.route('/')
def index():
    info = get_isp_info()
    return render_template('dashboard.html', 
                           local_ip=get_local_ip(), 
                           wan_ip=info['ip'], 
                           isp_name=info['isp'],
                           app_version=APP_VERSION)

@app.route('/api/adapters')
def get_adapters():
    adapters_data = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    extended_info = get_extended_iface_info()
    
    settings_map = {}
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT mac_address, custom_name, is_visible FROM adapter_settings")
        for row in c.fetchall():
            settings_map[row[0]] = {"custom_name": row[1], "is_visible": row[2]}

    for iface_name, addrs in interfaces.items():
        iface_stats = stats.get(iface_name)
        is_up = "Active" if (iface_stats and iface_stats.isup) else "Inactive"
        link_speed = f"{iface_stats.speed} Mbps" if (iface_stats and iface_stats.speed > 0) else "N/A"
        
        ip4, ip6, mac = "-", "-", "-"
        for addr in addrs:
            if addr.family == socket.AF_INET: ip4 = addr.address
            elif addr.family == socket.AF_INET6: ip6 = addr.address.split('%')[0]
            elif addr.family == psutil.AF_LINK: mac = addr.address

        gateway, dns = "-", "-"
        if iface_name in extended_info:
            gateway = extended_info[iface_name].get("gateway", "-")
            dns = extended_info[iface_name].get("dns", "-")
        elif "global" in extended_info and is_up == "Active":
            gateway = extended_info["global"].get("gateway", "-")
            dns = extended_info["global"].get("dns", "-")

        user_name = iface_name
        is_visible = True
        if mac in settings_map:
            if settings_map[mac]["custom_name"]: user_name = settings_map[mac]["custom_name"]
            is_visible = bool(settings_map[mac]["is_visible"])
        elif iface_name in settings_map:
            if settings_map[iface_name]["custom_name"]: user_name = settings_map[iface_name]["custom_name"]
            is_visible = bool(settings_map[iface_name]["is_visible"])
            
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
    return jsonify({"adapters": adapters_data, "global_speed": get_bandwidth()})

@app.route('/api/adapters/update', methods=['POST'])
def update_adapter_settings():
    try:
        data = request.json
        mac = data.get('mac')
        sys_id = data.get('id')
        name = data.get('name')
        visible = 1 if data.get('visible') else 0
        
        key_to_use = mac
        if not key_to_use or key_to_use == "-": key_to_use = sys_id

        if not key_to_use: return jsonify({"error": "Cannot identify adapter"})

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO adapter_settings (mac_address, custom_name, is_visible) VALUES (?, ?, ?)", 
                      (key_to_use, name, visible))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/scan_network')
def scan_network():
    local_ip = get_local_ip()
    target_ip = f"{local_ip.rsplit('.', 1)[0]}.0/24"
    devices = []
    try:
        arp = ARP(pdst=target_ip)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        result = srp(ether/arp, timeout=2, verbose=0)[0]
        for sent, received in result:
            ip = received.psrc
            devices.append({
                'ip': ip, 
                'mac': received.hwsrc,
                'hostname': resolve_hostname(ip),
                'services': check_open_ports(ip)['services'],
                'has_web': check_open_ports(ip)['has_web'],
                'web_port': check_open_ports(ip)['web_port']
            })
    except Exception as e:
        return jsonify({"error": f"Scan failed: {str(e)}"})
    return jsonify(devices)

# --- UPDATE API ---
@app.route('/api/update/check')
def check_update():
    """Checks version.json on GitHub."""
    content = fetch_github_file("version.json")
    if content:
        try:
            remote_data = json.loads(content)
            # You would implement your own setup_version logic if you could read that file
            # For now we return the remote version
            return jsonify({"status": "success", "remote_version": remote_data.get("version", "0.0.0"), "current_app_version": APP_VERSION})
        except:
            return jsonify({"status": "error", "message": "Failed to parse version.json"})
    return jsonify({"status": "error", "message": "Failed to fetch version.json"})

@app.route('/api/update/changelog')
def get_changelog():
    """Fetches README.md from GitHub."""
    content = fetch_github_file("README.md")
    if content:
        return jsonify({"status": "success", "changelog": content})
    return jsonify({"status": "error", "message": "Failed to fetch Changelog"})

@app.route('/api/update/apply', methods=['POST'])
def apply_update():
    """Simulates a git pull. In production, this would run a subprocess."""
    try:
        # subprocess.run(["git", "pull"], check=True) # Uncomment if using git
        return jsonify({"status": "success", "message": "Update initiated (Simulated). Restart the app to apply changes."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/devices/export', methods=['POST'])
def export_devices():
    try:
        data = request.json
        rows = data.get('rows', [])
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Hostname', 'IP Address', 'MAC Address', 'Services'])
        
        for row in rows:
            writer.writerow([
                row.get('hostname'), 
                row.get('ip'), 
                row.get('mac'), 
                row.get('services')
            ])
            
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=network_devices.csv"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/wifi')
def get_wifi_networks():
    networks = []
    sys_plat = platform.system()
    try:
        if sys_plat == "Windows":
            output = subprocess.check_output(["netsh", "wlan", "show", "network", "mode=bssid"], text=True)
            current_ssid = ""
            for line in output.split('\n'):
                line = line.strip()
                if line.startswith("SSID"): current_ssid = line.split(":")[1].strip()
                elif line.startswith("Signal"): 
                    if current_ssid: 
                        networks.append({"ssid": current_ssid, "signal": line.split(":")[1].strip()})
                        current_ssid = ""
        elif sys_plat == "Linux":
            cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi"]
            output = subprocess.check_output(cmd, text=True)
            for line in output.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2: networks.append({"ssid": parts[0], "signal": parts[1] + "%"})
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify(networks)

@app.route('/api/get_last_name')
def get_last_name():
    info = get_isp_info()
    wan_ip = info['ip']
    last_name = ""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (wan_ip,))
            row = c.fetchone()
            if row: last_name = row[0]
    except: pass
    return jsonify({"wan_ip": wan_ip, "isp": info['isp'], "last_name": last_name})

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    try:
        data = request.json
        manual_name = data.get('network_name', '').strip()
        conn_type = data.get('connection_type', 'Ethernet')
        cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
        output = subprocess.check_output(cmd, text=True)
        result = json.loads(output)
        down_mbps = f"{result['download']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        up_mbps = f"{result['upload']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        ping_ms = f"{result['ping']['latency']:.2f} ms"
        isp_name = result.get('isp', 'Unknown ISP')
        wan_ip = result.get('interface', {}).get('externalIp', 'Unknown')
        network_name = manual_name if manual_name else get_current_network_name()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO history (timestamp, network_name, connection_type, download, upload, ping, wan_ip, isp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (timestamp, network_name, conn_type, down_mbps, up_mbps, ping_ms, wan_ip, isp_name))
            conn.commit()
        return jsonify({"download": down_mbps, "upload": up_mbps, "ping": ping_ms, "network": network_name, "connection_type": conn_type, "wan_ip": wan_ip, "isp": isp_name})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/history/update', methods=['POST'])
def update_history_name():
    try:
        data = request.json
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("UPDATE history SET network_name = ? WHERE id = ?", (data.get('name'), data.get('id')))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/history')
def get_history():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM history ORDER BY id DESC")
            rows = [dict(row) for row in c.fetchall()]
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM history")
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/history/export', methods=['POST'])
def export_history():
    try:
        data = request.json
        rows = data['rows'] if data and 'rows' in data else []
        if not rows:
             with sqlite3.connect(DB_NAME) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("SELECT * FROM history ORDER BY id DESC")
                rows = [dict(row) for row in c.fetchall()]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Timestamp', 'Network Name', 'Type', 'Download', 'Upload', 'Ping', 'WAN IP', 'ISP'])
        for row in rows:
            writer.writerow([row.get('timestamp'), row.get('network_name'), row.get('connection_type', 'N/A'), row.get('download'), row.get('upload'), row.get('ping'), row.get('wan_ip'), row.get('isp', 'N/A')])
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=speedtest_history.csv"})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=81)