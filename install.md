# Manual Installation

The launcher scripts are optional convenience wrappers. They locate the project directory, check for Python, install a few platform prerequisites when possible, and then start `setup_env.py`.

`setup_env.py` creates the local `venv` environment and installs the Python dependencies used by the dashboard: `flask`, `psutil`, `scapy`, and `waitress`. On macOS it also installs `pyobjc-framework-CoreWLAN` and `pyobjc-framework-CoreLocation`. It downloads the appropriate Ookla Speedtest CLI during setup. Internet access is therefore required for a first-time installation.

## Windows

### What the Windows launcher does

`windows launcher.bat`:

1. Requests Administrator privileges.
2. Changes to the folder containing the launcher.
3. Checks for Python. If Python is missing, it opens the Microsoft Store and waits for Python to be installed.
4. Checks internet connectivity.
5. When online, installs `pip-system-certs` to help with certificate errors on some corporate networks.
6. Runs `setup_env.py`.

### Install manually

Install or enable the following before running the setup script:

- **Python 3.12 or newer**, with the option to add Python to `PATH` enabled. Verify with `python --version`.
- **Git for Windows**. The setup script can install it through Chocolatey, but install it manually if you do not want automated package installation.
- **Npcap**, with WinPcap API-compatible mode enabled if offered. Scapy uses it for packet-based network discovery.
- An internet connection for the initial dependency and Speedtest CLI downloads.
- Administrator access. Windows setup and some network scanning features require elevation.

The Python packages, virtual environment, and Speedtest CLI are installed by `setup_env.py`; they do not need to be installed globally. If your network uses certificate inspection, you may also install the optional certificate helper:

```powershell
python -m pip install pip-system-certs
```

From the project folder, run:

```powershell
python setup_env.py
```

## macOS

### What the macOS launcher does

`Linux and MacOS Launcher.sh`:

1. Changes to the folder containing the launcher and detects macOS.
2. Checks for Python 3.
3. If Python is missing, downloads the Python `3.14.7` macOS installer from python.org and installs it with Administrator privileges.
4. Runs `python3 setup_env.py`.

### Install manually

Install or enable the following before running the setup script:

- **Python 3**, preferably Python `3.14.7` to match the launcher. Verify with `python3 --version`.
- **Git**. Install Git directly or install Homebrew first and then run `brew install git`. The setup script can install Homebrew and Git when online, but this is optional when preparing the machine manually.
- An internet connection for Python package and Speedtest CLI downloads.
- Administrator access for network scanning.
- **Location Services** for the terminal or application running the dashboard. macOS requires this for Wi-Fi scanning.

The setup script installs the Python packages, including the macOS CoreWLAN and CoreLocation bindings, and installs Speedtest CLI through Homebrew when required.

From the project folder, run:

```bash
python3 setup_env.py
```

## Linux

### What the Linux launcher does

`Linux and MacOS Launcher.sh`:

1. Changes to the folder containing the launcher and detects Linux.
2. On Debian- or Ubuntu-based systems, checks for Python 3 and the `venv` module.
3. If required, uses `sudo apt-get` to install Python 3, `python3-venv`, `python3-pip`, and Git.
4. Runs `python3 setup_env.py`.

The setup script performs additional Debian/Ubuntu setup when online: it installs `python3-venv`, `python3-pip`, `python3-dev`, `build-essential`, `git`, and `net-tools`.

### Install manually

For Debian, Ubuntu, Linux Mint, and Raspberry Pi OS, install the base packages with:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git net-tools
```

Also provide:

- An internet connection for Python package and Speedtest CLI downloads.
- `sudo` access. The application uses elevated privileges for Scapy-based network discovery when it is not already running as root.
- A supported network interface. Wi-Fi scan results depend on the adapter and its supported bands.

The setup script installs the Python packages and selects the Ookla Speedtest CLI for the detected Linux architecture. No global Python package installation is required.

### Raspberry Pi notes

- Use **Raspberry Pi OS 64-bit** where possible and keep the OS fully updated.
- On a 32-bit Raspberry Pi OS installation, the setup script selects the ARMHF Speedtest binary; on a 64-bit installation it selects the AArch64 binary. The binary must match the OS architecture, not just the Pi model.
- For packet capture or low-level network features, install libpcap support if it is not already present:

```bash
sudo apt-get install -y libpcap-dev
```

- If Wi-Fi scanning is unavailable, check that the wireless adapter and driver support monitor/scanning operations. A USB Wi-Fi adapter may be needed for additional bands or capabilities.

From the project folder, run:

```bash
python3 setup_env.py
```

## After Setup

The dashboard normally starts on port `81`. Open `http://127.0.0.1:81` in a browser. The setup process may restart the application automatically after crashes; press `Ctrl+C` in the setup terminal to stop it.
