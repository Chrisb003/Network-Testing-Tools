<div align="center">
  <img src="static/Logo.png" alt="Network Diagnostics Logo" width="100" height="100">
  
  # Network Diagnostics Dashboard
  
  **Installation & Documentation Guide**
  
  ![Version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FChrisb003%2FNetwork-Testing-Tools%2Fmain%2Fversion.json&query=%24.version&label=Version&color=2ea44f) <br>
  [**View Repository**](https://github.com/Chrisb003/Network-Testing-Tools) • [**More Info**](https://chris94.uk/test.html) • [**Changelog**](https://chris94.uk/changelog.html)
</div>
<br>
This is a basic network troubleshooting tool I built with AI for my own use, now made public for anyone who might find it helpful.

* **Install:** Download the automated installers from my website (temporary links while the site is under construction), or follow the manual setup instructions below.
* **Support:** Please log bugs or suggestions on GitHub. I can't guarantee ongoing development, but since I use this tool myself, I'll try to push periodic bug fixes. I will be updating the dev branch first then pushing to main branch once tested.
* **Forks:** Feel free to copy the code and create your own versions, but please credit me as the original author.

> [!IMPORTANT]  
> A robust, fault-tolerant network diagnostic dashboard designed for troubleshooting network issues across home, office, and managed environments. Built using Python, Flask, SQLite, and Scapy. Internet access is strictly required for a first-time installation to download dependencies and fetch the latest updates from GitHub.

---

## ✨ Core Features & Support

* **Dedicated Test Device Mode:** Instantly configure the software to run headlessly. Creates persistent configuration triggers and automatically broadcasts a Wi-Fi Mobile Hotspot (Windows/Linux) so you can connect and control the dashboard from your phone.
* **Advanced Device Discovery:** Deep-scans subnets via Scapy (with a multi-threaded OS ping sweep fallback) to list hostnames, open ports/services, and automatically resolves device manufacturers.
* **Smart Network Mapping:** Supports Isolated Scans, Split Scans, and Auto-Matching to intelligently bundle devices under the correct router MAC and IP subnets.
* **Wi-Fi Scanning:** Scans across 2.4GHz, 5GHz, and 6GHz bands with signal strength metrics (dBm/%), channel details, and security types.
* **Speed Tests:** Integrates the native Ookla Speedtest CLI for accurate high-bandwidth connections (>1Gbps), with a seamless pure-Python fallback (limited to ~1Gbps) if the official CLI fails or is blocked by the OS.
* **Database Management:** Export your database natively through the UI, or import and merge external database backups seamlessly.
* **Cross-Platform GUI:** Spawns a background System Tray / Menu bar icon allowing you to natively start, stop, and interact with the background service.

### Platform OS Support

* <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="16" alt="Windows" /> **Windows 10/11:** Fully supported. Performing a Wi-Fi scan temporarily resets the Wi-Fi adapter via PowerShell to retrieve the available network list. Supports native Windows Mobile Hotspot creation.
* <img src="https://cdn.simpleicons.org/apple/999999" width="16" alt="macOS" /> **macOS:** Fully supported. Requires admin and Location permissions for scanning. Automatically compiles into a native `.app` bundle in your Applications folder.
* <img src="https://cdn.simpleicons.org/linux/FCC624" width="16" alt="Linux" /> **Linux & Raspberry Pi:** Fully supported across all major distributions. Automatically detects and utilizes `apt`, `dnf`, `pacman`, or `zypper` package managers. Supports NetworkManager Wi-Fi hotspots and `systemd` background services.

---

## 🚀 Automated Quick Install

The repository includes a suite of powerful, cross-platform deployment scripts. These scripts completely automate environment setup, GitHub syncing, background execution, and uninstallation. *Note: You can run these scripts again at any time to cleanly uninstall the application and remove background services.*

### <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="20" alt="Windows" /> Windows Automated Setup
The `Windows-Installer.ps1` script requests Administrator privileges, downloads and installs Python 3 natively if missing, and syncs project files. It prompts the user to install Npcap, creates Desktop/Start Menu shortcuts with the custom app logo, and automatically unlocks folder permissions.

Open **PowerShell** and paste the following command to begin:
```powershell
& ([scriptblock]::Create((irm "https://chris94.uk/install-scripts/Windows-Installer.ps1")))
```

### <img src="https://cdn.simpleicons.org/apple/999999" width="20" alt="macOS" /> macOS Automated Setup
The `MacOS-Installer.sh` script verifies macOS prerequisites (installing Python 3 if missing), downloads project files natively, and asks whether you prefer a visible terminal or a background `launchd` boot agent. It builds a native macOS `.app` bundle in the Applications folder.

Open the **Terminal** app and paste the following command:
```bash
curl -sSLq https://chris94.uk/install-scripts/MacOS-Installer.sh | sh
```

### <img src="https://cdn.simpleicons.org/linux/FCC624" width="20" alt="Linux" /> Linux Automated Setup
The `Linux-Installer.sh` script dynamically detects your package manager to install Python 3, venv, network-manager, and GUI tray dependencies like GTK3. It supports NetworkManager Wi-Fi Hotspots and allows you to select between a desktop autostart or a `systemd` background boot service.

Open your **Terminal** and paste the following command:
```bash
curl -sSLq https://chris94.uk/install-scripts/Linux-Installer.sh | sh
```

---

## 🌐 Accessing the Dashboard

Once the setup completes, the Waitress WSGI server will spin up. If you ran the automated installer, it will start on the custom port you selected during setup (defaulting to **81**). If you installed manually, it defaults to port **81**.

Open your web browser and navigate to your chosen port (e.g., port 81):
```text
http://127.0.0.1:81
```
If you enabled the Wi-Fi hotspot during installation, connect your phone to the broadcasted SSID and navigate to the Gateway IP with your configured port (usually `http://10.42.0.1:81` on Linux or `http://192.168.137.1:81` on Windows).

---

## ⚙️ Manual Installation Guide

If you prefer to bypass the wrapper scripts, the core `setup_env.py` script can be executed manually to create the local virtual environment and install the required Python dependencies (`flask`, `psutil`, `scapy`, `waitress`, `pystray`, `Pillow`).

### <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="16" alt="Windows" /> Windows Prerequisites
* **Python 3.12+**, with the option to *add Python to PATH* enabled.
* **Npcap**, with *WinPcap API-compatible mode* enabled (required for Scapy packet discovery).

From the project folder, open an **Administrator terminal** and run:
```powershell
python setup_env.py
```

### <img src="https://cdn.simpleicons.org/apple/999999" width="16" alt="macOS" /> macOS Prerequisites
* **Python 3.12+**.
* **Administrator access** via `sudo` to allow Scapy to read ARP tables.
* **Location Services** permissions granted to your Terminal. macOS strictly requires this for Wi-Fi scanning functions to return data.

From the project folder, open your terminal and run:
```bash
sudo python3 setup_env.py
```

### <img src="https://cdn.simpleicons.org/linux/FCC624" width="16" alt="Linux" /> Linux Prerequisites
For Debian, Ubuntu, and Raspberry Pi OS, you must manually install the native system build packages so Python can compile psutil and scapy:
```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential net-tools libpcap-dev
```
From the project folder, open your terminal and run:
```bash
sudo python3 setup_env.py
```

---

## 📖 Application Usage Guide

### Dashboard Basics
* **System Tray Icon:** When launched on a desktop environment, the app adds an icon to your system tray. You can right-click this icon to instantly restart or shut down the dashboard. *Note: If running as a background daemon, without a display, or in Dedicated Server Mode, this icon hides automatically.*
* **Table Sorting:** All data tables across the dashboard are dynamically sortable. Click any column header to toggle ascending/descending order.
* **Touch Mode:** Click the Hand Icon to increase the size of buttons, checkboxes, and table rows for easier tapping on mobile devices.

### Network Adapters
* **Hide / Unhide:** Select adapters to hide them from the main view, useful for removing virtual adapters or VPNs.
* **Pin as Primary:** Locks the dashboard header (Router IP, DNS, and Traffic Speeds) exclusively to this adapter's statistics.
* **Lock Wi-Fi Scanner:** Force the system to exclusively use a specific adapter for airwave scanning.

### Network Scanning
* **Scan Modes:** Use "New Scan" to match against previous networks, "Split Scan" to save as a new independent profile, or "Isolated Scan" to block historical merging entirely. "Continue Scan" resumes without deep-port-checking offline devices to save time.
* **Device Customization:** Click on a device's Name or Comment cell to open the metadata editor. Details follow the device's MAC address across any network it connects to.
* **Unknown Devices:** Devices in deep sleep mode (like phones) or basic IoT gear may not respond with a hostname. You can click "Unknown" to manually assign a custom name to these.
* **Merge Networks:** Check two or more networks in the history table and click "Merge" to bundle duplicate history into a single main network.

### Wi-Fi Scanning
* **Adapter Dropdown:** Choosing "Auto" instructs the system to intelligently scan across ALL un-hidden Wi-Fi adapters simultaneously to merge the most comprehensive signal data.
* **Auto-Logging:** Every time you press Scan, results are automatically saved to the database. You can rename scans or add comments.
* **Global Wi-Fi History:** View an aggregate list of every unique SSID ever seen, including detection counts and last-seen dates.

### Settings & Maintenance
* **Access Control:** Secure your dashboard by enabling "Require Login" in the Settings tab. If you get locked out, create a blank `passwordreset` file in the app folder to clear your credentials.
* **Database Import & Export:** Download a full `.db` backup to your local machine, or upload an existing database to merge its historical networks, devices, and scans into your current setup without deleting your existing data.
* **Database Maintenance:** Use the "Old > X Days" buttons to permanently wipe historical logs and inactive devices. Items you have manually "Locked" (Padlock Icon) will survive this process.
* **Network Connection Types:** Add or remove custom connection names (e.g., '5G Home Internet') that appear in the Speed Test dropdown.
* **System Updates:** Click "Check for Updates" to compare your core application files against the GitHub repository. It will list changes and handle the download, installation, and server restart automatically.
* **Power Controls:** "Restart App" cleanly reboots the Python server in-place without asking for your sudo/admin password again. "Shutdown App" kills the server and background supervisor completely.

---

## 🛠️ Advanced Configuration Files

Change advanced system behavior or trigger emergency actions by creating **blank files** (no file extension) in the same folder as your `app.py` script. The app detects these on boot, executes the action, and deletes the file.

* `standalone`: Enables Dedicated Server Mode. Unlocks "OS Reboot/Shutdown" web controls and forces the app into headless mode (hiding the desktop system tray icon).
* `webport`: Write a number (e.g., `8080`) inside this file to force the web server to run on that port.
* `autostart`: Contains a `1` or `0`. Controls whether the dashboard automatically pops open a web browser when the server starts.
* `workers`: An auto-generated JSON file controlling thread limits. You can safely delete this to reset concurrency to hardware defaults if you get locked out.
* `disablecleanup`: Completely bypasses the automated folder-isolation safety checks during installation.
* `passwordreset`: Automatically disables web authentication and deletes saved credentials.
* `cleardatabase`: **Warning:** Performs an irrecoverable factory wipe of your SQLite database and history logs.
* `reinstall`: **Warning:** Triggers a complete system wipe. Deletes all databases, environments, and backups, then re-downloads a fresh production copy from GitHub.
* `dev`: Forces the updater to switch to the "Development" channel.