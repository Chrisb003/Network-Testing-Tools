# <img src="static/Logo.png" width="40" align="top" alt="Logo" /> Network Diagnostics Dashboard

> A robust, fault-tolerant network diagnostic dashboard designed for troubleshooting network issues across home, office, and managed environments. Built using Python, Flask, SQLite, and Scapy.

For manual installation instructions or to bypass the automated launcher scripts, please refer to the [Installation Guide](install.md).

> **⚠️ Important:** Internet access is required for a first-time installation to download dependencies and fetch the latest updates from GitHub.

---

## 💻 Platform & OS Support

* <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="18" align="top" /> **Windows 10/11**: Fully supported. Performing a Wi-Fi scan temporarily resets the Wi-Fi adapter via PowerShell to retrieve the available network list. Supports native Windows Mobile Hotspot creation.
* <img src="https://cdn.simpleicons.org/apple/999999" width="18" align="top" /> **macOS**: Fully supported. Requires admin and Location permissions for scanning. Automatically compiles into a native `.app` bundle in your Applications folder which links to the app. *Due to an OS limitation, Wi-Fi scans cannot list the MAC addresses (BSSIDs) of surrounding networks.*
* <img src="https://cdn.simpleicons.org/linux/FCC624" width="18" align="top" /> **Linux & Raspberry Pi**: Fully supported across all major distributions. Automatically detects and utilizes `apt`, `dnf`, `pacman`, or `zypper` package managers. Supports NetworkManager Wi-Fi hotspots and `systemd` background services.

---

## ✨ Core Features

* **Dedicated Test Device Mode**: Instantly configure the software to run headlessly. Creates persistent configuration triggers and automatically broadcasts a Wi-Fi Mobile Hotspot (Windows/Linux) so you can connect and control the dashboard from your phone.
* **Dashboard Overview**: Tracks local LAN IP, WAN IP, ISP name, Router gateway IP, and DNS servers with primary adapter pinning.
* **Network Adapters**: Manages active interfaces, link speeds, MAC addresses, IPs, gateways, and DNS with visibility toggles, bulk-hiding, and physical/virtual adapter filtering.
* **Advanced Device Discovery**: Deep-scans subnets via Scapy (with a multi-threaded OS ping sweep fallback) to list hostnames, open ports/services (HTTP, SSH, Portainer, etc.), and automatically resolves device manufacturers via an online API.
* **Smart Network Mapping**: Supports Isolated Scans, Split Scans, and Auto-Matching to intelligently bundle devices under the correct router MAC and IP subnets.
* **Global History Tracking**: Remembers devices and Wi-Fi networks globally, tracking first-seen/last-seen dates, IP changes, and connection history across entirely different networks.
* **Wi-Fi Scanning**: Scans across 2.4GHz, 5GHz, and 6GHz bands with signal strength metrics (dBm/%), channel details, security types, and 5dBm clustering for hidden networks. Locks to specific hardware adapters if requested.
* **Diagnostic Tools**: Built-in DNS lookup and Ping tools featuring network context logging, editable network names, and bulk CSV exports.
* **Speed Tests**: Integrates the native Ookla Speedtest CLI for accurate high-bandwidth connections (>1Gbps), custom location tagging, and connection types.
* **Database Backup & Merging**: Uses a SQLite database with smart change-detection hashing, quota-managed rolling backups, and the ability to **import and merge** external database files natively through the UI.
* **System Management**: Provides detailed log management, update channel switching (Stable/Dev), dynamic worker thread optimization, OS Power Controls, and automated database maintenance cleanups.
* **Access Control**: Optional HTTP Basic Authentication with secure password hashing.
* **Accessible UI**: Includes Light/Dark mode toggles and a dedicated "Touch Mode" to increase button targets for tablets and mobile devices.
* **Offline Capable**: Once the app is setup, it can run completely offline (Speed tests and online Vendor API lookups will gracefully skip if no connection is detected).

---

## 🚀 Automated Installation & Management Scripts

The repository includes a suite of powerful, cross-platform deployment scripts. These scripts completely automate environment setup, GitHub syncing, background execution, and uninstallation.

| OS | Script | Description |
|---|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="16" align="top" /> **Windows** | `Windows-Installer.ps1` | Requests Administrator privileges, downloads and installs Python 3.12 directly from python.org if missing, and syncs project files. Prompts for Dedicated Test Device mode (configuring Windows Wi-Fi Hotspots) and creates Desktop/Start Menu shortcuts with the custom app logo. Automatically unlocks folder permissions using `icacls`. |
If you would like to just run the script from powershell you can run "& ([scriptblock]::Create((irm "https://test.chris94.com/install-scripts/Windows-Installer.ps1")))" and it will download and run the setup script.
| <img src="https://cdn.simpleicons.org/linux/FCC624" width="16" align="top" /> **Linux** | `Linux-Installer.sh` | Dynamically detects your Linux distro package manager (`apt`, `dnf`, `pacman`, `zypper`) to install dependencies. Syncs project files, configures NetworkManager Wi-Fi Hotspots, sets up a `systemd` background boot service, builds a desktop shortcut, and enforces `chmod 777` global permissions. |
| <img src="https://cdn.simpleicons.org/apple/999999" width="16" align="top" /> **macOS** | `MacOS-Installer.sh` | Installs official macOS Python packages and Xcode tools if missing. Syncs project files, registers a background `launchd` boot agent, and compiles the application into a native macOS `Network Diagnostics.app` bundle inside your Applications folder. |

*Note: You can run these scripts again at any time to cleanly uninstall the application, wipe the environment, and remove background services.*

---

## 🌍 Accessing the Dashboard

By default, the application binds to port **81**. If you ran an installer script and selected "Dedicated Test Device", it will bind to port **80**.

Open a web browser and navigate to:
```text
[http://127.0.0.1:81](http://127.0.0.1:81)  (or [http://127.0.0.1:80](http://127.0.0.1:80))
```
If you enabled the Wi-Fi hotspot, connect to the broadcasted SSID and navigate to the Gateway IP (usually `http://10.42.0.1:80` on Linux or `http://192.168.137.1:80` on Windows).

---

## ⚙️ Advanced Configuration Triggers

You can change advanced system behavior or trigger emergency recovery actions by creating **blank files** (with no file extension) in the same folder as your `app.py` script. The application will detect these on the next boot, execute the action, and delete the file.

* `standalone`: Enables Dedicated Server Mode. Unlocks the "OS Reboot" and "OS Shutdown" controls in the web interface.
* `disablecleanup`: Completely bypasses the automated folder-isolation safety checks.
* `webport`: Write a number (e.g., `8080`) inside this file to force the web server to run on that port.
* `autostart`: Set the contents to `1` to automatically open the browser on startup, or `0` for headless mode.
* `dev`: Forces the updater to switch to the "Development" channel.
* `passwordreset`: Automatically disables authentication and deletes saved credentials.
* `cleardatabase`: **Warning:** Performs an irrecoverable factory wipe of your SQLite database and history logs.
* `reinstall`: **Warning:** Triggers a complete system wipe. Deletes all databases, environments, and backups, then re-downloads a fresh production copy from GitHub.
* `workers`: Auto-generated file controlling thread limits. You can edit this file manually if UI access is lost due to thread-pool exhaustion on low-end hardware.