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
from datetime import datetime
from flask import Flask, render_template, jsonify, Response, request
from scapy.all import ARP, Ether, srp

# --- Configuration ---
APP_VERSION = "0.1.0"

# GITHUB CONFIGURATION
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
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT, network_name TEXT, connection_type TEXT,
                        download TEXT, upload TEXT, ping TEXT, wan_ip TEXT, isp TEXT
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS adapter_settings (
                        mac_address TEXT PRIMARY KEY, custom_name TEXT, is_visible INTEGER DEFAULT 1
                    )''')
        # Migrations to ensure all columns exist
        try: c.execute("SELECT isp FROM history LIMIT 1")
        except: c.execute("ALTER TABLE history ADD COLUMN isp TEXT")
        try: c.execute("SELECT connection_type FROM history LIMIT 1")
        except: c.execute("ALTER TABLE history ADD COLUMN connection_type TEXT")
        conn.commit()

init_db()

# --- Versioning Helpers ---
def get_setup_version():
    """Extracts version from setup_env.py via Regex."""
    try:
        with open("setup_env.py", "r") as f:
            content = f.read()
            match = re.search(r'SETUP_VERSION\s*=\s*["\']([^"\']+)["\']', content)
            return match.group(1) if match else "Unknown"
    except: return "Unknown"

def get_global_version():
    """Reads local version.json."""
    try:
        with open("version.json", "r") as f:
            return json.load(f).get("version", "0.0.0")
    except: return "0.0.0"

# --- Network & System Helpers ---
def get_isp_info():
    try:
        with urllib.request.urlopen('http://ip-api.com/json/?fields=query,isp', timeout=3) as url:
            data = json.loads(url.read().decode())
            return {"ip": data.get("query", "Unknown"), "isp": data.get("isp", "Unknown ISP")}
    except: return {"ip": "Unknown", "isp": "Unknown ISP"}

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
            out = subprocess.check_output("nmcli -t -f active,ssid dev wifi | grep ^yes", shell=True, text=True)
            return out.split(":")[1].strip()
    except: pass
    return "Ethernet/Unknown"

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except: return '127.0.0.1'
    finally: s.close()

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
                    if "Default Gateway" in line and ":" in line: info_map[curr]["gateway"] = line.split(":")[1].strip()
                    elif "DNS Servers" in line and ":" in line: info_map[curr]["dns"] = line.split(":")[1].strip()
        else: # Basic implementation for Linux/Mac
            info_map["global"] = {"gateway": "-", "dns": "-"}
    except: pass
    return info_map

# --- Bandwidth Usage Monitoring ---
last_received = psutil.net_io_counters().bytes_recv
last_sent = psutil.net_io_counters().bytes_sent
last_time = time.time()

def get_bandwidth():
    global last_received, last_sent, last_time
    curr_received = psutil.net_io_counters().bytes_recv
    curr_sent = psutil.net_io_counters().bytes_sent
    curr_time = time.time()
    delta = curr_time - last_time
    if delta == 0: delta = 1
    down = (curr_received - last_received) / delta
    up = (curr_sent - last_sent) / delta
    last_received, last_sent, last_time = curr_received, curr_sent, curr_time
    return {"download": f"{down / 1024 / 1024:.2f} MB/s", "upload": f"{up / 1024 / 1024:.2f} MB/s"}

# --- Port Scanning for Services ---
def resolve_hostname(ip):
    try: return socket.gethostbyaddr(ip)[0]
    except: return "Unknown"

def check_open_ports(ip):
    services = []; has_web = False; web_port = None
    # Ports to check for web interfaces
    web_ports = {80: "HTTP", 443: "HTTPS", 8080: "HTTP-Alt", 8443: "HTTPS-Alt"}
    common_ports = {22: "SSH"}
    common_ports.update(web_ports)
    
    for port, name in common_ports.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1)
        if s.connect_ex((ip, port)) == 0:
            services.append(name)
            if port in web_ports:
                has_web = True
                if not web_port or port == 80: web_port = port
        s.close()
    return {"services": ", ".join(services) if services else "None", "has_web": has_web, "web_port": web_port}

# --- Update Logic (GitHub) ---
def fetch_github_file(filename):
    url = f"https://api.github.com/repos/{GITHUB_SETTINGS['owner']}/{GITHUB_SETTINGS['repo']}/contents/{filename}?ref={GITHUB_SETTINGS['branch']}"
    req = urllib.request.Request(url)
    if GITHUB_SETTINGS["token"]: req.add_header("Authorization", f"token {GITHUB_SETTINGS['token']}")
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return base64.b64decode(data['content']).decode('utf-8')
    except: return None

# --- Routes ---

@app.route('/')
def index():
    info = get_isp_info()
    return render_template('dashboard.html', 
                           local_ip=get_local_ip(), wan_ip=info['ip'], isp_name=info['isp'],
                           global_version=get_global_version(), setup_version=get_setup_version(), 
                           app_version=APP_VERSION)

@app.route('/api/adapters')
def get_adapters():
    adapters = []
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    ext = get_extended_iface_info()
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
        
        # Check by MAC, then by ID (for No-MAC adapters)
        saved = settings.get(mac) or settings.get(name) or {"name": name, "visible": 1}
        adapters.append({
            "id": name, "name": saved["name"], "mac": mac, "ip4": ip4, "ip6": ip6,
            "status": "Active" if (st and st.isup) else "Inactive",
            "gateway": ext.get(name, {}).get("gateway", "-"),
            "dns": ext.get(name, {}).get("dns", "-"),
            "speed": f"{st.speed} Mbps" if (st and st.speed > 0) else "N/A",
            "visible": bool(saved["visible"])
        })
    return jsonify({"adapters": adapters, "global_speed": get_bandwidth()})

@app.route('/api/adapters/update', methods=['POST'])
def update_adapter():
    d = request.json
    key = d.get('mac') if (d.get('mac') and d.get('mac') != "-") else d.get('id')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("REPLACE INTO adapter_settings (mac_address, custom_name, is_visible) VALUES (?, ?, ?)",
                     (key, d.get('name'), 1 if d.get('visible') else 0))
    return jsonify({"status": "success"})

@app.route('/api/get_last_name')
def get_last_name():
    info = get_isp_info()
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("SELECT network_name FROM history WHERE wan_ip = ? ORDER BY id DESC LIMIT 1", (info['ip'],)).fetchone()
    return jsonify({"last_name": row[0] if row else "", "wan_ip": info['ip'], "isp": info['isp']})

@app.route('/api/speedtest', methods=['POST'])
def run_speedtest():
    d = request.json
    try:
        cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
        res = json.loads(subprocess.check_output(cmd, text=True))
        down = f"{res['download']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        up = f"{res['upload']['bandwidth'] * 8 / 1_000_000:.2f} Mbps"
        ping = f"{res['ping']['latency']:.2f} ms"
        isp = res.get('isp', 'Unknown')
        wan = res.get('interface', {}).get('externalIp', 'Unknown')
        name = d.get('network_name') or get_current_network_name()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT INTO history (timestamp, network_name, connection_type, download, upload, ping, wan_ip, isp) VALUES (?,?,?,?,?,?,?,?)",
                         (ts, name, d.get('connection_type'), down, up, ping, wan, isp))
        return jsonify({"download": down, "upload": up, "ping": ping, "network": name, "isp": isp})
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/api/scan_network')
def scan_network():
    local_ip = get_local_ip()
    target = f"{local_ip.rsplit('.', 1)[0]}.0/24"
    devices = []
    try:
        ans, unans = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=target), timeout=2, verbose=0)
        for s, r in ans:
            ports = check_open_ports(r.psrc)
            devices.append({
                'ip': r.psrc, 'mac': r.hwsrc, 'hostname': resolve_hostname(r.psrc),
                'services': ports['services'], 'has_web': ports['has_web'], 'web_port': ports['web_port']
            })
    except: pass
    return jsonify(devices)

@app.route('/api/history')
def get_history():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return jsonify([dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()])

@app.route('/api/history/update', methods=['POST'])
def update_history():
    d = request.json
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE history SET network_name = ? WHERE id = ?", (d.get('name'), d.get('id')))
    return jsonify({"status": "success"})

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM history")
    return jsonify({"status": "success"})

@app.route('/api/history/export', methods=['POST'])
def export_history():
    d = request.json; rows = d.get('rows', [])
    if not rows:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()]
    out = io.StringIO(); writer = csv.writer(out)
    writer.writerow(['Timestamp', 'Network Name', 'Type', 'Download', 'Upload', 'Ping', 'WAN IP', 'ISP'])
    for r in rows: writer.writerow([r.get('timestamp'), r.get('network_name'), r.get('connection_type'), r.get('download'), r.get('upload'), r.get('ping'), r.get('wan_ip'), r.get('isp')])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=history.csv"})

@app.route('/api/devices/export', methods=['POST'])
def export_devices():
    d = request.json; rows = d.get('rows', [])
    out = io.StringIO(); writer = csv.writer(out)
    writer.writerow(['Hostname', 'IP', 'MAC', 'Services'])
    for r in rows: writer.writerow([r.get('hostname'), r.get('ip'), r.get('mac'), r.get('services')])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=devices.csv"})

@app.route('/api/update/check')
def check_update():
    content = fetch_github_file("version.json")
    if content:
        try:
            remote = json.loads(content)
            return jsonify({
                "status": "success", "remote_version": remote.get("version", "0.0.0"), 
                "app_version": APP_VERSION, "global_version": get_global_version()
            })
        except: pass
    return jsonify({"status": "error"})

@app.route('/api/update/changelog')
def get_changelog():
    content = fetch_github_file("README.md")
    if content: return jsonify({"status": "success", "changelog": content})
    return jsonify({"status": "error"})

@app.route('/api/update/apply', methods=['POST'])
def apply_update():
    return jsonify({"status": "success", "message": "Update simulated. Restart app."})

@app.route('/api/wifi')
def get_wifi_networks():
    networks = []; sys_plat = platform.system()
    try:
        if sys_plat == "Windows":
            output = subprocess.check_output(["netsh", "wlan", "show", "network", "mode=bssid"], text=True)
            ssid = ""
            for line in output.split('\n'):
                if line.strip().startswith("SSID"): ssid = line.split(":")[1].strip()
                elif line.strip().startswith("Signal") and ssid:
                    networks.append({"ssid": ssid, "signal": line.split(":")[1].strip()}); ssid = ""
        elif sys_plat == "Linux":
            cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi"]
            out = subprocess.check_output(cmd, text=True)
            for line in out.strip().split('\n'):
                p = line.split(':'); 
                if len(p) >= 2: networks.append({"ssid": p[0], "signal": p[1] + "%"})
    except: pass
    return jsonify(networks)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=81)