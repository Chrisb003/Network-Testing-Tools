# <img src="static/Logo.png" width="40" align="top" alt="Logo" /> Manual Installation Guide

The launcher scripts (`Windows Launcher.bat` and `Linux and MacOS Launcher.sh`) are optional convenience wrappers[cite: 11]. They locate the project directory, check for Python, install platform prerequisites, and then start `setup_env.py`[cite: 11].

The core `setup_env.py` script creates the local `venv` (virtual environment) and installs the required Python dependencies: `flask`, `psutil`, `scapy`, and `waitress`[cite: 11]. It also handles OS-specific requirements, such as macOS CoreWLAN bindings and downloading the correct Ookla Speedtest CLI binary[cite: 11]. 

> **⚠️ Important:** Internet access is strictly required for a first-time installation to download dependencies[cite: 11].

If you prefer to set up the environment manually without the wrapper scripts, follow the platform-specific instructions below[cite: 11].

---
## <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/windows11/windows11-original.svg" width="28" align="top" /> Windows

### Prerequisites
Install or enable the following before running the setup script:

* **Python 3.12 or newer**, with the option to **add Python to `PATH`** enabled. Verify with `python --version`[cite: 11].
* **Git for Windows**. The setup script can install it through Chocolatey, but install it manually if you want to avoid automated package managers[cite: 11].
* **Npcap**, with *WinPcap API-compatible mode* enabled if offered. Scapy requires this for packet-based network discovery[cite: 11].
* **Administrator access**. Windows setup and low-level Scapy network scanning features require elevation[cite: 11].

*Note: The Python packages, virtual environment, and Speedtest CLI are installed by `setup_env.py`; they do not need to be installed globally[cite: 11].*

> **Corporate Networks:** If your network uses deep packet inspection or corporate certificates, you may optionally install the certificate helper inside your environment[cite: 11]:
> ```powershell
> python -m pip install pip-system-certs
> ```

### Launch Command
From the project folder, open an **Administrator terminal** and run[cite: 11]:
```powershell
python setup_env.py
```

---

## <img src="https://cdn.simpleicons.org/apple/999999" width="28" align="top" /> macOS

### Prerequisites
Install or enable the following before running the setup script:

* **Python 3**, ideally the latest stable release (e.g., 3.12+). Verify with `python3 --version`[cite: 11].
* **Git**. Install Git directly or install Homebrew first and then run `brew install git`[cite: 11].
* **Administrator access** via `sudo` to allow Scapy to read ARP tables and network interfaces[cite: 11].
* **Location Services** permissions granted to your Terminal or IDE. *macOS strictly requires Location Services for Wi-Fi scanning functions to return data*[cite: 11].

*Note: The setup script installs the standard Python packages alongside the macOS specific `pyobjc-framework-CoreWLAN` and `pyobjc-framework-CoreLocation` bindings[cite: 11].*

### Launch Command
From the project folder, open your terminal and run[cite: 11]:
```bash
sudo python3 setup_env.py
```

---

## <img src="https://cdn.simpleicons.org/linux/FCC624" width="28" align="top" /> Linux

### Prerequisites
For Debian, Ubuntu, Linux Mint, and Raspberry Pi OS, you must manually install the native system build packages so Python can successfully compile `psutil` and `scapy`[cite: 11].

Install the base packages with[cite: 11]:
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools
```

Also ensure you have:
* **`sudo` access**. The application requires elevated privileges to bind to network interfaces for discovery[cite: 11].
* **A supported network interface**. Wi-Fi scan results heavily depend on the adapter's capabilities and supported bands[cite: 11].

### Raspberry Pi Notes

* Use **Raspberry Pi OS 64-bit** where possible and keep the OS fully updated[cite: 11].
* The setup script will automatically select the ARMHF Speedtest binary for 32-bit Pi OS, and the AArch64 binary for 64-bit Pi OS[cite: 11].
* For packet capture or low-level network features, ensure `libpcap` is installed[cite: 11]:
  ```bash
  sudo apt-get install -y libpcap-dev
  ```
* If Wi-Fi scanning fails, ensure your wireless adapter and driver support monitor/scanning operations. A USB Wi-Fi adapter may be required for 5GHz/6GHz bands on older Pi models[cite: 11].

### Launch Command
From the project folder, open your terminal and run[cite: 11]:
```bash
sudo python3 setup_env.py
```

---

## 🎉 After Setup

Once the setup completes, the Waitress WSGI server will spin up[cite: 11]. 

The dashboard natively starts on port `81`. Open your web browser to[cite: 11]:
```text
[http://127.0.0.1:81](http://127.0.0.1:81)
```

> **💡 Tip:** The setup supervisor is designed to automatically capture crashes and restart the application if it fails. Press `Ctrl+C` in the setup terminal to gracefully stop the dashboard supervisor[cite: 11].