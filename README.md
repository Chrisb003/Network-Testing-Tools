# <img src="static/Logo.png" width="40" align="top" alt="Logo" /> Network Diagnostics Dashboard

> A robust, fault-tolerant network diagnostic dashboard designed for troubleshooting network issues across home, office, and managed environments. Built using Python, Flask, SQLite, and Scapy[cite: 13].

For manual installation instructions or to bypass the automated launcher scripts, please refer to the [Installation Guide](install.md)[cite: 13].

---

## 💻 Platform & OS Support

* <img src="https://cdn.simpleicons.org/windows11/0078D4" width="18" align="top" /> **Windows 11**: Fully supported (requires running as Administrator and Location Services enabled). Performing a Wi-Fi scan temporarily resets the Wi-Fi adapter via PowerShell to retrieve the available network list[cite: 13].
* <img src="https://cdn.simpleicons.org/apple/999999" width="18" align="top" /> **macOS**: Fully supported (requires admin permissions and Location permissions). *Due to an OS limitation, Wi-Fi scans cannot list the MAC addresses (BSSIDs) of surrounding networks*[cite: 13].
* <img src="https://cdn.simpleicons.org/linux/FCC624" width="18" align="top" /> **Linux & Raspberry Pi**: Fully supported (tested on Linux Mint and Debian-based distros via `apt-get`)[cite: 13].

---

## ✨ Core Features

* **Dashboard Overview**: Tracks local LAN IP, WAN IP, ISP name, Router gateway IP, and DNS servers with primary adapter pinning[cite: 13].
* **Network Adapters**: Manages active interfaces, link speeds, MAC addresses, IPs, gateways, and DNS with visibility toggles and bulk-hiding[cite: 13].
* **Device Discovery & MAC Resolution**: Scans subnets via Scapy (with a ping sweep fallback) to list hostnames, open ports/services, and automatically resolves device manufacturers (Vendors) via API[cite: 13].
* **Global History Tracking**: Remembers devices and Wi-Fi networks globally, tracking first-seen/last-seen dates, IP changes, and connection history across entirely different networks.
* **Wi-Fi Scanning**: Scans across 2.4GHz, 5GHz, and 6GHz bands with signal strength metrics (dBm/%), channel details, security types, and 5dBm clustering for hidden networks[cite: 13].
* **Diagnostic Tools**: Built-in DNS lookup and Ping tools featuring network context logging, editable network names, and bulk CSV exports[cite: 13].
* **Speed Tests**: Integrates the native Ookla Speedtest CLI for accurate high-bandwidth connections (>1Gbps), custom location tagging, and connection types[cite: 13].
* **Database Backup & Merging**: Uses a SQLite database with smart change-detection hashing, quota-managed rolling backups, and the ability to **import and merge** external database files natively through the UI[cite: 13].
* **System Management**: Provides detailed log management, update channel switching (stable/dev), dynamic worker thread optimization, and automated database maintenance cleanups[cite: 13].
* **Data Protection**: Users can "lock" specific speed tests or Wi-Fi scans to protect them from being wiped during automated database cleanups.
* **Access Control**: Optional HTTP Basic Authentication with secure password hashing[cite: 13].
* **Accessible UI**: Includes Light/Dark mode toggles and a dedicated "Touch Mode" to increase button targets for tablets and mobile devices[cite: 13].

---

## 🚀 Automated Launcher Scripts Overview

The repository includes automated wrapper scripts to handle environment setup automatically[cite: 13]:

| OS | Script | Description |
|---|---|---|
| <img src="https://cdn.simpleicons.org/windows11/0078D4" width="16" align="top" /> **Windows** | `Windows Launcher.bat` | Requests Administrator privileges, ensures Python is installed (opening the Microsoft Store if missing), checks internet connectivity, installs `pip-system-certs` to bypass corporate SSL blocks, and launches the setup script[cite: 13]. |
| <img src="https://cdn.simpleicons.org/apple/999999" width="16" align="top" /> **macOS** | `Linux and MacOS Launcher.sh` | Detects macOS, checks for Python 3, automatically downloads and installs the official Python package from python.org if missing, and launches the setup script[cite: 13]. |
| <img src="https://cdn.simpleicons.org/linux/FCC624" width="16" align="top" /> **Linux** | `Linux and MacOS Launcher.sh` | Detects Debian/Ubuntu-based systems, uses `sudo apt-get` to install Python 3, `python3-venv`, `python3-pip`, and `git`, then launches the setup script[cite: 13]. |

---

## 🌍 Accessing the Dashboard

The application runs on port **81** by default[cite: 13]. Open a web browser and navigate to:
```text
[http://127.0.0.1:81](http://127.0.0.1:81)
```

---

## ⚙️ Startup & Control Files

You can create these optional text files in the root directory before startup to trigger automated actions. *Once triggered, the system will automatically delete the file*[cite: 13]:

* `autostart`: Set the contents to `1` to automatically open the browser on startup, or `0` for headless mode[cite: 13].
* `dev`: Forces the database to switch to the development update channel[cite: 13].
* `passwordreset`: Resets authentication credentials and disables login requirements on boot[cite: 13].
* `cleardatabase`: Completely wipes the database and all temporary WAL/SHM files on startup[cite: 13].
* `webport`: Contains a numeric port value (e.g., `8080`) to override the default web server port[cite: 13].
* `reinstall`: Triggers a complete factory reset. Wipes all files, environments, and databases, then re-downloads a fresh copy of the application from GitHub[cite: 13].