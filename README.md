# Network Diagnostics Dashboard

A robust, fault-tolerant network diagnostic dashboard designed for troubleshooting network issues across home, church, and managed environments. Built using Python, Flask, SQLite, and Scapy.[cite: 11].
If you prefer to not to use the automated wrapper scripts (`windows launcher.bat`[cite: 13] and `Linux and MacOS Launcher.sh`), you can read the install.md file for instructions on what to do to launch it. Should you want an overview of what the scripts do see bellow.

## Platform & OS Support

* **Windows 11**: Fully supported (requires running as Administrator and Location Services enabled). Performing a Wi-Fi scan temporarily resets the Wi-Fi adapter via PowerShell to retrieve the available network list[cite: 8, 11].
* **macOS**: Fully supported (requires admin permissions and Location permissions). Due to an OS limitation, Wi-Fi scans cannot list the MAC addresses (BSSIDs) of surrounding networks[cite: 11].
* **Linux & Raspberry Pi (ARM/x64)**: Fully supported (tested on Linux Mint and Debian-based distros via `apt-get`)[cite: 11].

## Automated Launcher Scripts Overview
### What the Windows launcher does
`windows launcher.bat`:
1. Requests Administrator privileges.
2. Changes to the folder containing the launcher.
3. Checks for Python. If Python is missing, it opens the Microsoft Store and waits for Python to be installed.
4. Checks internet connectivity.
5. When online, installs `pip-system-certs` to help with certificate errors on some corporate networks.
6. Runs `setup_env.py`.

### What the macOS part of the launcher does
`Linux and MacOS Launcher.sh`:
1. Changes to the folder containing the launcher and detects macOS.
2. Checks for Python 3.
3. If Python is missing, downloads the Python `3.14.7` macOS installer from python.org and installs it with Administrator privileges.
4. Runs `python3 setup_env.py`.

### What the Linux part of the launcher does
`Linux and MacOS Launcher.sh`:
1. Changes to the folder containing the launcher and detects Linux.
2. On Debian- or Ubuntu-based systems, checks for Python 3 and the `venv` module.
3. If required, uses `sudo apt-get` to install Python 3, `python3-venv`, `python3-pip`, and Git.
4. Runs `python3 setup_env.py`.

## Core Features

* **Dashboard Overview**: Tracks local LAN IP, WAN IP, ISP name, Router gateway IP, and DNS servers with primary adapter pinning[cite: 11].
* **Touch Friendly**: Has option to toggle a touch mode which increase size of buttons etc to make it easier to use with a touch screen[cite: 11].
* **Light and Dark Mode**: The UI defaults to dark mode but if you are someone who prefers it there is a light mode to.[cite: 11].
* **Speed Tests**: Integrates Ookla Speedtest CLI for high-bandwidth connections (>1Gbps), custom location tagging, and connection types[cite: 11].
* **Network Adapters**: Manages active interfaces, link speeds, MAC addresses, IPs, gateways, and DNS with visibility toggles[cite: 11].
* **Device Discovery**: Scans subnets via Scapy and ping sweep fallback to list hostnames, vendors, open ports/services, and history tracking[cite: 11].
* **Diagnostic Tools**: Built-in DNS lookup and Ping tools featuring network context logging, CSV exports, and editable network names[cite: 11].
* **Wi-Fi Scanning**: Scans across 2.4GHz, 5GHz, and 6GHz bands with signal strength metrics, channel details, security types, and scan history (The wifi adapter needs to support these bands to see them)[cite: 11].
* **Backup & Migration**: Uses a sqllite database and features, smart change-detection hashing, quota-managed rolling backups, semantic version checks, and auto-corruption recovery[cite: 7].
* **System Management**: Provides log management, update channel switching (stable/dev), worker thread optimization, database maintenance cleanups, and crash-loop auto-rollback via `rollback.zip`[cite: 7, 8, 11].
* **Access Control**: Optional HTTP Basic Authentication with secure password hashing[cite: 8].
* **Logs**: Option to enable detailed log files for troubleshooting issues with the application[cite: 8].

## Accessing the Dashboard

The application runs on port 81 by default[cite: 11]. Open a web browser and navigate to `http://127.0.0.1:81`[cite: 11].

## Startup & Control Files

Create these optional text files in the root directory before startup to trigger automated actions[cite: 11]:

* **`autostart`**: Set to `1` to automatically open the browser on startup, or `0` for headless mode[cite: 11].
* **`dev`**: Forces the setup script to download from the development branch[cite: 11].
* **`passwordreset`**: Resets authentication credentials and disables login requirements on boot[cite: 11].
* **`cleardatabase`**: Completely wipes the database and temporary files on startup[cite: 11].
* **`webport`**: Contains a numeric port value (e.g., `8080`) to override the default web port[cite: 11].
* **`reinstall`**: If there is an issue with the application, this will clear all files and redownload a fresh copy of the app. The database is recreated to[cite: 11].