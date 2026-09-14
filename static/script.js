let updateModal, adapterModal, editSpeedTestModal, mergeModal, networkModal, wifiScanModal, toolLogModal, allDevices = [], allHistory = [], currentNetworkId = null;
    let currentScanResults = [];
    let allWifiHistory = [];
    let pendingRemoteVersion = null;
    let allDeviceHistory = [];
    let devHistModal;
    let devHistFiltered = [];
    let devHistCurrentPage = 1;
    let devHistItemsPerPage = 20;
    let selectedDevHistMacs = new Set();
    let currentModalDevData = [];
    let liveBandwidthInterval = null;
    let pauseTimeout = null;
    let deviceConfigModal, wifiSSIDModal;

/**
 * Sanitizes raw strings to prevent Cross-Site Scripting (XSS) attacks in the DOM.
 * Converts dangerous HTML characters into their safe entity equivalents.
 * @param {string} str - The raw string fetched from the database or backend.
 * @returns {string} The sanitized HTML-safe string.
 */
function escapeHTML(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

/**
 * Escapes strings specifically for safe injection into inline JavaScript handlers 
 * (e.g., onclick="myFunction('escaped_string')").
 * @param {string} str - The raw string to be passed into a JS function.
 * @returns {string} The safe, escaped string.
 */
function escapeJS(str) {
    if (!str) return "";
    return String(str)
        .replace(/\\/g, "\\\\")
        .replace(/'/g, "\\'")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "\\n")
        .replace(/\r/g, "\\r");
}

    const SERVICE_PORT_MAP = {
    "SSH": "22", "HTTP": "80", "HTTPS": "443", "HTTP (8080)": "8080", 
    "HTTPS (8443)": "8443", "Flask/UPnP": "5000", "Portainer/Admin": "9000"
    };

/**
 * Maps known service names (like HTTP, SSH) to their standard port numbers for CSV exports.
 * @param {string} servicesStr - Comma-separated list of services (e.g., "HTTP, SSH").
 * @returns {string} Comma-separated list of ports (e.g., "80, 22").
 */
function convertServicesToPorts(servicesStr) {
    if (!servicesStr || servicesStr === "None" || servicesStr === "NONE") return "None";
    return servicesStr.split(',').map(s => SERVICE_PORT_MAP[s.trim()] || s.trim()).join(', ');
}

/**
 * Master Initialization Event.
 * Fires as soon as the DOM is ready. Initializes all Bootstrap modals, hooks up the 
 * sorting listeners to table headers, fetches user preferences, and starts the live polling loops.
 */
document.addEventListener("DOMContentLoaded", () => {
    setTheme(localStorage.getItem('theme') || 'dark');
    
    // Initialize Modals safely
    if(document.getElementById('updateModal')) updateModal = new bootstrap.Modal(document.getElementById('updateModal'));
    if(document.getElementById('adapterModal')) adapterModal = new bootstrap.Modal(document.getElementById('adapterModal'));
    if(document.getElementById('editSpeedTestModal')) editSpeedTestModal = new bootstrap.Modal(document.getElementById('editSpeedTestModal'));
    if(document.getElementById('devHistModal')) devHistModal = new bootstrap.Modal(document.getElementById('devHistModal'));
    if(document.getElementById('mergeNetworkModal')) mergeModal = new bootstrap.Modal(document.getElementById('mergeNetworkModal'));
    if(document.getElementById('mergeWifiModal')) mergeWifiModal = new bootstrap.Modal(document.getElementById('mergeWifiModal'));
    if(document.getElementById('networkModal')) networkModal = new bootstrap.Modal(document.getElementById('networkModal'));
    if(document.getElementById('wifiScanModal')) wifiScanModal = new bootstrap.Modal(document.getElementById('wifiScanModal'));
    if(document.getElementById('toolLogModal')) toolLogModal = new bootstrap.Modal(document.getElementById('toolLogModal'));
    if(document.getElementById('deviceConfigModal')) deviceConfigModal = new bootstrap.Modal(document.getElementById('deviceConfigModal'));
    if(document.getElementById('wifiSSIDModal')) wifiSSIDModal = new bootstrap.Modal(document.getElementById('wifiSSIDModal'));
    
    // AUTO-INITIALIZE TABLE SORT HEADERS
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.classList.add('sortable');
        th.innerHTML += ' <i class="bi bi-arrow-down-up sort-icon text-muted opacity-25 ms-1"></i>';
        
        const tableEl = th.closest('table');
        if (tableEl && tableEl.hasAttribute('data-table-id')) {
            const tableType = tableEl.getAttribute('data-table-id');
            const sortKey = th.getAttribute('data-sort');
            th.addEventListener('click', () => handleSort(tableType, sortKey, th));
        }
    });

    // Initial Header Data Load
    fetch('/api/get_last_name').then(r=>r.json()).then(d => { 
        if(d.last_name && document.getElementById('st-network-name')) document.getElementById('st-network-name').value = d.last_name;
        if(document.getElementById('header-wan-ip')) document.getElementById('header-wan-ip').innerText = d.wan_ip;
        if(document.getElementById('header-isp')) document.getElementById('header-isp').innerText = d.isp;
    });
    
    // Resource Management: Automatically pause live network polling if the tab is hidden for over 1 hour
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            pauseTimeout = setTimeout(() => {
                stopLivePolling();
                console.log("Tab hidden for 1 hour: Live bandwidth polling paused to save resources.");
            }, 60 * 60 * 1000); 
        } else {
            if (pauseTimeout) {
                clearTimeout(pauseTimeout);
                pauseTimeout = null;
            }
            startLivePolling();
        }
    });

    // Fetch Logging Setting
    fetch('/api/settings/logging')
        .then(res => res.json())
        .then(data => {
            const toggleFull = document.getElementById('logging-toggle');
            const toggleDisable = document.getElementById('disable-logging-toggle');
            
            if (toggleFull) toggleFull.checked = data.full_logging;
            if (toggleDisable) toggleDisable.checked = data.disable_all_logs;
            
            const sizeBadge = document.getElementById('log-size-badge');
            if (sizeBadge && data.size_mb) {
                sizeBadge.innerText = data.size_mb + ' MB';
            }
        })
        .catch(err => console.error("Error fetching logging setting:", err));

    // Start Loops & Data Fetches
    loadWorkerSettings();
    fetchAdapters(); 
    checkUpdates(); 
    loadNetworks();
    loadConnectionTypes();
    loadSystemAlerts();
    loadUpdateChannels();
    loadBackupInfo();
    loadDatabaseInfo();
    startLivePolling();
    loadWifiAdapters();

    // Touch Mode Initialization
    const savedTouchMode = localStorage.getItem('touchMode') === 'true';
    if (savedTouchMode) document.body.classList.add('touch-mode');
    updateTouchIcon(savedTouchMode);
    
    if (typeof verifyUpdateStatus === "function") verifyUpdateStatus();
    const channelSelect = document.getElementById('update-channel-select');
    if (channelSelect && window.APP_CONFIG.updateChannel) {
        channelSelect.value = window.APP_CONFIG.updateChannel;
    }

    // Restore Last Active Page on Refresh
    const lastPage = localStorage.getItem('lastActivePage');
    if (lastPage) {
        const targetLink = document.querySelector(`[onclick*="showPage('${lastPage}'"]`);
        if (targetLink) {
            showPage(lastPage, targetLink);
        }
    }
});

/**
 * Loads the user-defined physical network connection types (e.g., Ethernet, Wi-Fi, 5G Home Internet) 
 * to populate the Speed Test selection dropdowns.
 */
function loadConnectionTypes() {
    fetch('/api/settings/connection_types').then(r=>r.json()).then(d => {
        const mainSelect = document.getElementById('st-conn-type');
        const modalSelect = document.getElementById('modal-st-type');
        const sysList = document.getElementById('connection-type-list');
        
        let optionsHtml = '';
        let listHtml = '';
        
        d.forEach(t => {
            const safeName = escapeHTML(t.name);
            optionsHtml += `<option value="${safeName}">${safeName}</option>`;
            listHtml += `<li class="list-group-item d-flex justify-content-between align-items-center small py-1">
                ${safeName}
                <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteConnectionType(${t.id})"><i class="bi bi-x-lg"></i></button>
            </li>`;
        });
        
        if (mainSelect) {
            const currentVal = mainSelect.value;
            mainSelect.innerHTML = optionsHtml;
            if (currentVal) mainSelect.value = currentVal;
        }
        if (modalSelect) modalSelect.innerHTML = optionsHtml;
        if (sysList) sysList.innerHTML = listHtml;
    });
}

/**
 * Submits a new custom connection type to the database.
 */
function addConnectionType() {
    const el = document.getElementById('new-conn-type');
    if(!el.value) return;
    fetch('/api/settings/connection_types/add', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: el.value})
    }).then(r=>r.json()).then(d => {
        if(d.error) alert(d.error);
        else { el.value = ''; loadConnectionTypes(); }
    });
}

/**
 * Deletes a custom connection type from the database.
 */
function deleteConnectionType(id) {
    fetch('/api/settings/connection_types/delete', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id})
    }).then(loadConnectionTypes);
}

    // --- TABLE SORTING ENGINE ---
/**
 * Master handler for clicking column headers to sort tables.
 * Toggles the sort direction, updates the visual arrow icons, and triggers the re-render.
 * @param {string} type - The ID/Category of the table (e.g., 'devices', 'history').
 * @param {string} key - The data key to sort by (e.g., 'ip_address', 'timestamp').
 * @param {HTMLElement} thEl - The table header element that was clicked.
 */
function handleSort(type, key, thEl) {
    const state = tableState[type];
    if (!state) return;

    if (state.sortKey === key) {
        state.sortAsc = !state.sortAsc;
    } else {
        state.sortKey = key;
        state.sortAsc = true;
        // Dates, signal strengths, and counts naturally default to descending so the newest/biggest are at the top
        if (key.includes('time') || key.includes('last') || key.includes('count') || key.includes('dbm')) {
            state.sortAsc = false;
        }
    }

    // Update Icons visually across the table header
    const table = thEl.closest('table');
    table.querySelectorAll('.sort-icon').forEach(icon => {
        icon.className = 'bi bi-arrow-down-up sort-icon text-muted opacity-25 ms-1';
    });
    const activeIcon = thEl.querySelector('.sort-icon');
    if (activeIcon) {
        activeIcon.className = state.sortAsc ? 'bi bi-arrow-up sort-icon text-primary ms-1' : 'bi bi-arrow-down sort-icon text-primary ms-1';
    }

    performSort(type);

    // Route the re-render to the correct specific UI function
    if (type === 'adapters') renderAdaptersTable();
    else if (type === 'devices') filterDevices(); 
    else if (type === 'devHist') {
        devHistCurrentPage = 1; 
        renderDeviceHistory();
    }
    else if (type === 'devHistModalTable') renderDevHistModalTable();
    else if (type === 'wifiNetModalTable') renderWifiNetModalTable();
    else {
        state.page = 1;
        renderSpecificTable(type);
    }
}

/**
 * Extracts pure numbers from messy strings (e.g., "50 Mbps" -> 50) for accurate math sorting.
 * @param {any} val - The raw cell value.
 * @returns {number} The parsed floating point number, or an extremely low baseline if invalid.
 */
function extractNumber(val) {
    if (typeof val === 'number') return val;
    if (!val || val === '-' || String(val).toLowerCase().includes('unknown')) return -999999;
    const match = String(val).match(/-?[\d.]+/);
    return match ? parseFloat(match[0]) : -999999;
}

/**
 * Converts an IPv4 address string into a 32-bit integer for mathematically perfect sorting.
 * @param {string} ipA - The first IP address.
 * @param {string} ipB - The second IP address.
 * @returns {number} The numeric difference for array sorting.
 */
function compareIP(ipA, ipB) {
    const parseIP = ip => {
        if (!ip || ip === '-' || String(ip).toLowerCase().includes('unknown')) return null;
        const parts = String(ip).split('.');
        if(parts.length !== 4) return null;
        const num = parts.reduce((acc, oct) => (acc << 8) + parseInt(oct, 10), 0) >>> 0;
        return isNaN(num) ? null : num;
    };
    const numA = parseIP(ipA);
    const numB = parseIP(ipB);
    if (numA !== null && numB !== null) {
        return numA - numB;
    }
    return String(ipA).localeCompare(String(ipB));
}


/**
 * Master navigation controller for the Single Page Application (SPA).
 * Hides all pages, reveals the target page, and updates the active state in the navbar.
 * @param {string} id - The HTML ID of the page section to display (e.g., 'devices', 'settings').
 * @param {HTMLElement} link - The navigation anchor element that was clicked.
 */
function showPage(id, link) {
    // Save the active page to local storage to persist across server restarts or page refreshes
    localStorage.setItem('lastActivePage', id);
    
    // Hide all pages
    document.querySelectorAll('.page-section').forEach(p => p.classList.add('hidden'));
    
    // Remove 'active' highlight class from all main nav links AND dropdown items
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.dropdown-item').forEach(l => l.classList.remove('active'));
    
    // Show the targeted page
    document.getElementById(id).classList.remove('hidden'); 
    
    // Highlight the clicked link
    link.classList.add('active');
    
    // If a dropdown item was clicked, highlight its parent dropdown toggle as well
    const parentDropdown = link.closest('.dropdown');
    if (parentDropdown) {
        const toggle = parentDropdown.querySelector('.nav-link.dropdown-toggle');
        if (toggle) toggle.classList.add('active');
    }
    
    // Fix for iOS/iPadOS: Drop focus to clear sticky hover states
    if (document.activeElement) {
        document.activeElement.blur();
    }
    // Fix for Bootstrap: Manually close any dropdown menus that get stuck open on touch devices
    document.querySelectorAll('.dropdown-toggle.show').forEach(toggle => {
        const bsDropdown = bootstrap.Dropdown.getInstance(toggle);
        if (bsDropdown) bsDropdown.hide();
    });
    
    // Trigger backend data refreshes depending on which page was opened
    if(id === 'history') fetchHistory();
    if(id === 'networks') loadNetworks();
    if(id === 'dns-tool') fetchToolLogs('dns');
    if(id === 'ping-tool') fetchToolLogs('ping');
    if(id === 'wifi-history') loadWifiHistory();
    if(id === 'device-history-page') loadDeviceHistory();
    if(id === 'wifi-networks-history') loadWifiNetworksHistory();
    
    // Reset the Help page search and tab state when opened
    if(id === 'help') {
        const searchBox = document.getElementById('help-search');
        if (searchBox) searchBox.value = "";
        resetHelpSearch();
        
        const generalTab = document.querySelector('[data-target="help-general"]');
        if (generalTab) switchHelpTab(generalTab);
    }
}

/**
 * Toggles the global UI theme between dark and light modes.
 * Saves the preference to browser LocalStorage.
 */
function toggleTheme() {
    const next = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    setTheme(next); 
    localStorage.setItem('theme', next);
}

/**
 * Applies the selected theme to the DOM and updates the toggle button icon.
 * @param {string} theme - 'dark' or 'light'.
 */
function setTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.innerHTML = theme === 'dark' ? '<i class="bi bi-moon-fill"></i>' : '<i class="bi bi-sun-fill"></i>';
    btn.className = theme === 'dark' ? 'btn btn-outline-light' : 'btn btn-outline-dark';
}

/**
 * Toggles 'Touch Mode' which injects CSS classes to enlarge buttons, table rows, and checkboxes.
 * Essential for usability on iPads, tablets, and phones.
 */
function toggleTouchMode() {
    const isTouch = document.body.classList.toggle('touch-mode');
    localStorage.setItem('touchMode', isTouch ? 'true' : 'false');
    updateTouchIcon(isTouch);
}

/**
 * Updates the visual state of the Touch Mode toggle button.
 * @param {boolean} isTouch - Whether Touch Mode is currently active.
 */
function updateTouchIcon(isTouch) {
    const btn = document.getElementById('touch-toggle');
    if (!btn) return;
    if (isTouch) {
        btn.innerHTML = '<i class="bi bi-hand-index-thumb-fill"></i>';
        btn.classList.replace('btn-outline-secondary', 'btn-secondary');
    } else {
        btn.innerHTML = '<i class="bi bi-hand-index-thumb"></i>';
        btn.classList.replace('btn-secondary', 'btn-outline-secondary');
    }
}

    let isManualRefreshing = false;
    let isPolling = false; // NEW: Prevents overlapping background requests

/**
 * Fast-polls the backend for live network upload/download rates.
 * Mutex locks (isPolling, isManualRefreshing) prevent overlapping HTTP requests if the server hangs.
 */
async function updateLiveRatesOnly() {
    if (isManualRefreshing || isPolling) return; 
    
    isPolling = true; 

    try {
        const response = await fetch('/api/live_bandwidth');
        const data = await response.json();
        
        if (data.global_speed) {
            const downEl = document.getElementById('live-down');
            const upEl = document.getElementById('live-up');
            if (downEl) downEl.innerText = data.global_speed.download;
            if (upEl) upEl.innerText = data.global_speed.upload;
        }
        
        if (data.adapters) {
            data.adapters.forEach(adapter => {
                const speedCell = document.querySelector(`tr[data-id="${adapter.id}"] .speed-cell`);
                if (speedCell) {
                    speedCell.innerText = adapter.speed;
                }
            });
        }
    } catch (err) {
        console.warn("Fast poll failed, skipping update.");
    } finally {
        isPolling = false; 
    }
}

/**
 * Initiates the 3-second background polling loop for live bandwidth data.
 */
function startLivePolling() {
    if (!liveBandwidthInterval) {
        updateLiveRatesOnly(); 
        liveBandwidthInterval = setInterval(updateLiveRatesOnly, 3000);
    }
}

/**
 * Halts the background polling loop. Used to save client/server resources when the tab is hidden.
 */
function stopLivePolling() {
    if (liveBandwidthInterval) {
        clearInterval(liveBandwidthInterval);
        liveBandwidthInterval = null;
    }
}

/**
 * Posts a mult-part file payload to the backend to merge an external SQLite database into the active one.
 * Locks the UI to prevent double-submissions during heavy I/O operations.
 * @param {HTMLInputElement} input - The file input element containing the .db file.
 */
async function handleImport(input) {
    if (!input.files || !input.files[0]) return;
    
    if (!confirm("This will merge data from the selected file into your current database. Existing data will not be deleted. Continue?")) {
        input.value = "";
        return;
    }

    const formData = new FormData();
    formData.append('file', input.files[0]);

    const btn = input.nextElementSibling;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Merging...';

    try {
        const response = await fetch('/api/system/import_db', {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        
        if (result.status === "success") {
            alert(result.message);
            location.reload(); 
        } else {
            alert("Error: " + result.error);
        }
    } catch (err) {
        alert("Upload failed: " + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
        input.value = "";
    }
}

/**
 * Triggers a backend database purge to remove records older than a specified number of days.
 * Bypasses locked (protected) records unless 'all' is passed for a factory wipe.
 * @param {number|string} interval - Number of days to look back, or 'all' for a total wipe.
 */
async function runCleanup(interval) {
    let warning = `Are you sure you want to delete data older than ${interval} days?`;
    if (interval === 'all') {
        warning = "CRITICAL WARNING: This will delete ALL networks, devices, settings, and logs. This cannot be undone. Proceed?";
    }

    if (!confirm(warning)) return;

    const btn = event.target;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Cleaning...';

    try {
        const response = await fetch('/api/system/cleanup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ days: interval })
        });
        const result = await response.json();
        
        if (result.status === "success") {
            alert(result.message);
            location.reload(); 
        } else {
            alert("Cleanup failed: " + result.message);
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

/**
 * Purges "Ghost" devices from the database.
 * These are devices that belong exclusively to network profiles that have since been deleted.
 */
async function removeOldDevices() {
    if (!confirm("Are you sure you want to completely remove old devices that are no longer associated with any active network scans?")) return;

    const btn = event.currentTarget;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Cleaning...';

    try {
        const response = await fetch('/api/system/cleanup_orphaned_devices', { method: 'POST' });
        const result = await response.json();
        
        if (result.status === "success") {
            alert(result.message);
            if (typeof loadDeviceHistory === "function" && !document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
            loadDatabaseInfo();
        } else {
            alert("Cleanup failed: " + result.message);
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

/**
 * Purges "Ghost" Wi-Fi network records from the database.
 * Deletes global SSIDs whose parent scan histories have been deleted by the user.
 */
async function removeOldWifi() {
    if (!confirm("Are you sure you want to completely remove old Wi-Fi networks that are no longer associated with any active scans?")) return;

    const btn = event.currentTarget;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Cleaning...';

    try {
        const response = await fetch('/api/system/cleanup_orphaned_wifi', { method: 'POST' });
        const result = await response.json();
        
        if (result.status === "success") {
            alert(result.message);
            if (typeof loadWifiNetworksHistory === "function" && !document.getElementById('wifi-networks-history').classList.contains('hidden')) {
                loadWifiNetworksHistory();
            }
            loadDatabaseInfo(); 
        } else {
            alert("Cleanup failed: " + result.message);
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// --- Adapters Logic ---
    let lastAdaptersData = null; // Store data locally to make UI toggles instant

    async function fetchAdapters() {
        /**
         * Performs a full OS data fetch. Now ONLY runs on page load 
         * or when explicitly clicking the "Refresh" button.
         */
        const tbody = document.getElementById('adapter-table');
        if (tbody) tbody.style.opacity = '0.5';

        try {
            const response = await fetch('/api/adapters');
            const data = await response.json();
            
            if (!data || !data.adapters) return;
            
            lastAdaptersData = data; // Cache the data locally
            
            // Respect previous sorting state on auto-refresh
            performSort('adapters');
            
            renderAdaptersTable();   // Pass off to the instant renderer
            
        } catch (err) {
            console.warn("Failed to fetch adapter data: System might be offline.", err);
            const routerEl = document.getElementById('header-router-ip');
            if (routerEl) routerEl.innerText = "Disconnected";
            if (tbody) tbody.style.opacity = '1';
        }
    }

    function renderAdaptersTable() {
        /**
         * Instantly generates the HTML table using the locally cached data.
         * Runs in 0 milliseconds when toggling UI filters.
         */
        if (!lastAdaptersData || !lastAdaptersData.adapters) return;
        
        const data = lastAdaptersData;
        const noMac = document.getElementById('hideNoMacCheck').checked;
        const showHidden = document.getElementById('showHiddenCheck').checked;

        // 1. Update Global Header Stats
        const routerEl = document.getElementById('header-router-ip');
        const dnsEl = document.getElementById('header-dns');
        const downEl = document.getElementById('live-down');
        const upEl = document.getElementById('live-up');

        if (routerEl) routerEl.innerText = data.primary_router && data.primary_router !== "-" ? data.primary_router : "Disconnected";
        if (dnsEl) dnsEl.innerText = data.primary_dns && data.primary_dns !== "-" ? data.primary_dns : "N/A";
        if (data.global_speed) { 
            if (downEl) downEl.innerText = data.global_speed.download; 
            if (upEl) upEl.innerText = data.global_speed.upload; 
        }

        // 2. PINNED ADAPTER ALERT LOGIC
        const alertEl = document.getElementById('pinned-adapter-alert');
        const alertNameEl = document.getElementById('pinned-adapter-name');
        
        const pinned = data.adapters.find(a => a.is_primary === true);
        const active = data.adapters.find(a => a.is_active === true);
        
        if (pinned && alertEl && alertNameEl) {
            alertEl.classList.remove('hidden');
            alertNameEl.innerText = pinned.name;
        } else if (alertEl) {
            alertEl.classList.add('hidden');
        }

        // --- NEW: Auto-Select Speed Test Connection Type ---
        const targetAdapter = pinned || active;
        if (targetAdapter && targetAdapter.mac !== window.lastTargetAdapterMac) {
            window.lastTargetAdapterMac = targetAdapter.mac;
            const typeSelect = document.getElementById('st-conn-type');
            if (typeSelect) {
                // Slight timeout to ensure the DB connection types loaded on initial page boot
                setTimeout(() => {
                    if (Array.from(typeSelect.options).some(opt => opt.value === targetAdapter.type)) {
                        typeSelect.value = targetAdapter.type;
                    }
                }, 100); 
            }
        }

        const tbody = document.getElementById('adapter-table'); 
        if (!tbody) return;

        // 3. Render the Adapter Table
        tbody.innerHTML = data.adapters.map(a => {
            // Filter Logic based on UI toggles
            if (noMac && (!a.mac || a.mac === '-')) return '';
            
            // Backend visible property is numeric or bool
            const isVisible = !!a.visible; 
            if (!isVisible && !showHidden) return '';

            const safeName = escapeJS(a.name);
            const safeId = escapeJS(a.id);
            const originalMac = a.mac || '-';
            const displayMac = originalMac.replace(/-/g, ':');
            const isPrimary = !!a.is_primary;
            
            const isChecked = tableState.adapters.selected.has(originalMac) ? 'checked' : '';
            const checkboxDisabled = originalMac === '-' ? 'disabled' : '';

            return `
                <tr data-id="${a.id}" class="${!isVisible ? 'opacity-50' : ''}">
                    <td onclick="event.stopPropagation()">
                        <input type="checkbox" class="adapters-check" value="${originalMac}" onchange="toggleSelection('adapters', this)" ${isChecked} ${checkboxDisabled}>
                    </td>
                    <td onclick="openAdapterConfig('${originalMac}', '${safeId}', '${safeName}', ${isVisible}, ${isPrimary})" style="cursor: pointer;" title="Edit Adapter">
                        <strong>${escapeHTML(a.name)}</strong> <i class="bi bi-pencil small text-muted ms-1"></i>
                        ${isPrimary ? '<span class="badge bg-primary ms-1" style="font-size: 0.6rem;">PINNED</span>' : ''}
                    </td>
                    <td class="text-muted small">${escapeHTML(a.id)}</td>
                    <td class="${a.status === 'Active' ? 'status-active' : 'status-inactive'}">
                        ${a.status}
                    </td>
                    <td class="font-monospace small text-nowrap">${displayMac}</td>
                    <td>${a.ip4}</td>
                    <td class="text-primary fw-bold">${a.gateway}</td> 
                    <td class="text-info fw-bold">${a.dns}</td>
                    <td class="text-nowrap small fw-bold speed-cell">${a.speed}</td>
                    <td class="text-end">
                        <i class="bi bi-gear" style="cursor: pointer;" 
                           onclick="openAdapterConfig('${originalMac}', '${safeId}', '${safeName}', ${isVisible}, ${isPrimary})">
                        </i>
                    </td>
                </tr>`;
        }).join('');
        
        updateMasterCheckbox('adapters'); 
        
        // Remove the grey-out effect immediately after rendering
        tbody.style.opacity = '1';
    }

/**
 * Removes the 'is_primary' locked flag from the active Pinned Adapter.
 * Reverts the dashboard header metrics back to global OS routing.
 */
async function unpinAdapter() {
    if (!lastAdaptersData) return;
    const pinned = lastAdaptersData.adapters.find(a => a.is_primary === true);
    if (!pinned) return;

    if (!confirm(`Are you sure you want to unpin "${pinned.name}"? The dashboard will return to global monitoring.`)) return;

    try {
        const updateResponse = await fetch('/api/adapter_settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mac: pinned.mac, name: pinned.name,
                visible: pinned.visible ? 1 : 0, is_primary: 0 
            })
        });

        if (updateResponse.ok) {
            pinned.is_primary = false; // Optimistic update
            renderAdaptersTable();     // Instant UI refresh
        } else {
            alert("Failed to unpin adapter.");
        }
    } catch (err) { alert("Error: " + err.message); }
}

/**
 * Pre-populates the Adapter Settings modal with the selected adapter's database info.
 * Dynamically unhides the 'Wi-Fi Lock' checkbox if the adapter is recognized as a wireless interface.
 */
function openAdapterConfig(mac, id, name, vis, primary) {
    document.getElementById('modal-mac').value = mac; 
    document.getElementById('modal-sys-id').value = id;
    document.getElementById('modal-custom-name').value = name; 
    document.getElementById('modal-visible').checked = vis;
    document.getElementById('modal-pin-primary').checked = primary; 
    
    const wifiContainer = document.getElementById('modal-wifi-scanner-container');
    const isWifi = globalWifiAdapters.some(a => a.id === id);
    
    if (isWifi) {
        wifiContainer.classList.remove('hidden');
        document.getElementById('modal-wifi-scanner').checked = (localStorage.getItem('preferredWifiAdapter') === id);
    } else {
        wifiContainer.classList.add('hidden');
    }
    
    adapterModal.show();
}

/**
 * Saves changes to an adapter's custom name, visibility, and pinned status.
 * Updates the global Wi-Fi scanner locks securely via LocalStorage to prevent backend mismatches.
 */
async function saveAdapterSettings() {
    const mac = document.getElementById('modal-mac').value;
    const id = document.getElementById('modal-sys-id').value;
    const name = document.getElementById('modal-custom-name').value;
    const visible = document.getElementById('modal-visible').checked ? 1 : 0;
    const primary = document.getElementById('modal-pin-primary').checked ? 1 : 0;

    if (primary === 1 && visible === 0) {
        alert("You cannot hide a pinned adapter. Please unpin it first or make it visible.");
        return; 
    }
    
    const wifiContainer = document.getElementById('modal-wifi-scanner-container');
    if (!wifiContainer.classList.contains('hidden')) {
        const isScanner = document.getElementById('modal-wifi-scanner').checked;
        if (isScanner) {
            localStorage.setItem('preferredWifiAdapter', id);
        } else if (localStorage.getItem('preferredWifiAdapter') === id) {
            localStorage.removeItem('preferredWifiAdapter');
        }
        
        const select = document.getElementById('wifi-adapter-select');
        if (select) select.value = localStorage.getItem('preferredWifiAdapter') || "";
        if (typeof updateWifiScannerAlert === "function") updateWifiScannerAlert();
    }

    const response = await fetch('/api/adapter_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mac: mac, id: id, name: name,
            visible: visible, is_primary: primary
        })
    });

    if (response.ok) {
        adapterModal.hide();
        loadWifiAdapters();
        
        // Optimistic UI update for instant feedback
        if (lastAdaptersData && lastAdaptersData.adapters) {
            if (primary === 1) lastAdaptersData.adapters.forEach(a => a.is_primary = false);
            
            const target = lastAdaptersData.adapters.find(a => a.mac === mac || (a.mac === '-' && a.id === id));
            if (target) {
                target.name = name; target.visible = visible; target.is_primary = (primary === 1);
            }
        }
        renderAdaptersTable(); 
    } else {
        const err = await response.json();
        alert("Failed to save: " + (err.message || "Unknown error"));
    }
}

/**
 * Toggles the visibility of the "Unhide Selected" bulk action button 
 * and instantly re-renders the adapter table to reveal greyed-out hidden adapters.
 */
function toggleShowHidden() {
    const isChecked = document.getElementById('showHiddenCheck').checked;
    const unhideBtn = document.getElementById('btn-bulk-unhide');
    
    if (unhideBtn) {
        if (isChecked) unhideBtn.classList.remove('hidden');
        else unhideBtn.classList.add('hidden');
    }
    
    renderAdaptersTable(); 
}

/**
 * Posts an array of MAC addresses to the backend to bulk-hide them from the UI.
 * Validates backend conditions (like preventing hiding the last usable adapter) before returning success.
 */
function bulkHideAdapters() {
    const selectedMacs = Array.from(tableState.adapters.selected);
    if (!selectedMacs.length) return alert("Please select at least one adapter to hide.");
    if (!confirm(`Are you sure you want to hide ${selectedMacs.length} selected adapter(s)?`)) return;

    const btn = event.currentTarget;
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Hiding...';
    btn.disabled = true;

    fetch('/api/adapters/bulk_hide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ macs: selectedMacs })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            // Optimistic UI update
            if (lastAdaptersData && lastAdaptersData.adapters) {
                lastAdaptersData.adapters.forEach(a => {
                    if (selectedMacs.includes(a.mac)) a.visible = 0;
                });
            }
            tableState.adapters.selected.clear();
            renderAdaptersTable(); 
            loadWifiAdapters();    
        } else {
            alert("Validation Failed: " + d.message);
        }
    })
    .catch(err => alert("Failed to connect: " + err.message))
    .finally(() => {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    });
}

/**
 * Reverses the bulk-hide operation, restoring visibility flags to the selected adapters in the database.
 */
function bulkUnhideAdapters() {
    const selectedMacs = Array.from(tableState.adapters.selected);
    if (!selectedMacs.length) return alert("Please select at least one adapter to unhide.");
    if (!confirm(`Are you sure you want to unhide ${selectedMacs.length} selected adapter(s)?`)) return;

    const btn = event.currentTarget;
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Unhiding...';
    btn.disabled = true;

    fetch('/api/adapters/bulk_unhide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ macs: selectedMacs })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            if (lastAdaptersData && lastAdaptersData.adapters) {
                lastAdaptersData.adapters.forEach(a => {
                    if (selectedMacs.includes(a.mac)) a.visible = 1;
                });
            }
            tableState.adapters.selected.clear();
            renderAdaptersTable(); 
            loadWifiAdapters();    
        } else {
            alert("Error: " + d.message);
        }
    })
    .catch(err => alert("Failed to connect: " + err.message))
    .finally(() => {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    });
}

let globalWifiAdapters = []; // Tracks which interfaces are actually Wi-Fi

/**
 * Queries the backend for a list of valid, unhidden physical Wi-Fi adapters.
 * Populates the dropdown menu in the Wi-Fi Scanning tab and respects previously saved user preferences.
 */
function loadWifiAdapters() {
    fetch('/api/wifi/interfaces')
        .then(r => r.json())
        .then(data => {
            globalWifiAdapters = data; 
            const select = document.getElementById('wifi-adapter-select');
            if (!select) return;
            
            let html = '<option value="">Auto (All Adapters)</option>';
            data.forEach(iface => {
                html += `<option value="${escapeHTML(iface.id)}">${escapeHTML(iface.name)}</option>`;
            });
            select.innerHTML = html;

            const preferred = localStorage.getItem('preferredWifiAdapter');
            if (preferred && data.some(opt => opt.id === preferred)) {
                select.value = preferred;
            } else if (preferred) {
                // Adapter was unplugged or hidden; fallback to auto
                select.value = "";
                localStorage.removeItem('preferredWifiAdapter');
            }

            select.onchange = function() {
                if (this.value) {
                    localStorage.setItem('preferredWifiAdapter', this.value);
                } else {
                    localStorage.removeItem('preferredWifiAdapter');
                }
                updateWifiScannerAlert();
            };
            
            updateWifiScannerAlert();
        })
        .catch(err => console.error("Error loading Wi-Fi adapters:", err));
}

/**
 * Displays a sticky alert banner on the Wi-Fi scanning page if the user has explicitly locked the scanner to a single hardware interface.
 */
function updateWifiScannerAlert() {
    const preferred = localStorage.getItem('preferredWifiAdapter');
    const alertEl = document.getElementById('wifi-scanner-alert');
    const nameEl = document.getElementById('wifi-scanner-name');
    
    if (preferred && globalWifiAdapters) {
        const adapter = globalWifiAdapters.find(a => a.id === preferred);
        if (adapter) {
            alertEl.classList.remove('hidden');
            nameEl.innerText = adapter.name;
            return;
        }
    }
    alertEl.classList.add('hidden');
}

/**
 * Clears the Wi-Fi scanner lock, reverting the system back to "Auto (All Adapters)".
 */
function resetWifiScanner() {
    localStorage.removeItem('preferredWifiAdapter');
    const select = document.getElementById('wifi-adapter-select');
    if (select) select.value = "";
    updateWifiScannerAlert();
}

/**
 * Triggers a manual refresh of the OS network interfaces.
 * Disables the refresh button visually to indicate a backend query is processing.
 */
async function refreshNetworkInfo() {
    isManualRefreshing = true;
    const btn = document.getElementById('refresh-btn');
    const originalText = btn.innerHTML;
    
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Updating...';
    
    try {
        await fetchAdapters(); 
    } finally {
        isManualRefreshing = false;
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}
// --- 1. NETWORKS ---
/**
 * Loads the core Network Scan History table from the backend.
 */
function loadNetworks() {
    fetch('/api/networks').then(r=>r.json()).then(d => {
        tableState.networks.data = d;
        tableState.networks.filtered = d;
        tableState.networks.page = 1;
        tableState.networks.selected.clear();
        performSort('networks');
        renderNetworks();
    });
}

/**
 * Searches and filters the Network Scan History table based on user input.
 */
function filterNetworks() {
    const q = document.getElementById('networks-filter').value.toLowerCase();
    tableState.networks.filtered = tableState.networks.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
    tableState.networks.page = 1;
    performSort('networks');
    renderNetworks();
}

/**
 * Toggles the Auto-Matching state of a historical Network profile.
 * Prevents multiple networks from fighting over the same MAC/IP subnet automatically.
 * @param {number|string} id - The Network ID.
 * @param {number} currentState - 1 (enabled) or 0 (disabled).
 * @param {boolean} force - Bypasses safety prompts to force an overwrite.
 */
function toggleMatching(id, currentState, force = false) {
    const newState = currentState ? 0 : 1; 
    
    fetch('/api/system/toggle_matching', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id, state: newState, force: force})
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'conflict') {
            if (confirm(d.message + "\n\nDo you want to proceed and make this network the active matching profile?")) {
                toggleMatching(id, currentState, true);
            } else {
                loadNetworks(); 
            }
        } else if (d.status === 'success') {
            loadNetworks(); 
        } else {
            alert("Error: " + (d.error || "Failed to update matching state."));
        }
    })
    .catch(err => alert("Communication error: " + err.message));
}

/**
 * Executes the mathematical sort logic on the target table's underlying data array.
 * Must be called before re-rendering a paginated table.
 * @param {string} type - The table identifier key.
 */
function performSort(type) {
    const state = tableState[type];
    if (!state || !state.sortKey) return;

    let dataArray;
    if (type === 'adapters' && lastAdaptersData) dataArray = lastAdaptersData.adapters;
    else if (type === 'devices') dataArray = allDevices;
    else if (type === 'devHist') dataArray = devHistFiltered;
    else if (type === 'devHistModalTable') dataArray = currentModalDevData;
    else if (type === 'wifiNetModalTable') dataArray = currentModalWifiNetData;
    else dataArray = state.filtered;

    if (!dataArray || dataArray.length === 0) return;

    dataArray.sort((a, b) => {
        let valA = a[state.sortKey] ?? '';
        let valB = b[state.sortKey] ?? '';

        if (state.sortKey.includes('ip') || state.sortKey === 'gateway' || state.sortKey === 'result_ip' || state.sortKey === 'target') {
            return state.sortAsc ? compareIP(valA, valB) : compareIP(valB, valA);
        }

        if (['speed', 'download', 'upload', 'ping', 'latency', 'packet_loss', 'device_count', 'mac_count', 'scan_count', 'dbm'].includes(state.sortKey)) {
            const numA = extractNumber(valA);
            const numB = extractNumber(valB);
            return state.sortAsc ? numA - numB : numB - numA;
        }

        valA = String(valA).toLowerCase();
        valB = String(valB).toLowerCase();
        if (valA < valB) return state.sortAsc ? -1 : 1;
        if (valA > valB) return state.sortAsc ? 1 : -1;
        return 0;
    });
}

/** Advances or rewinds the pagination index for standard tables. */
function changePage(type, direction) {
    tableState[type].page += direction;
    renderSpecificTable(type);
}

/** Updates the items-per-page limit for standard tables and resets the view to Page 1. */
function changePerPage(type) {
    const val = document.getElementById(`${type}-per-page`).value;
    tableState[type].perPage = val === 'all' ? 'all' : parseInt(val);
    tableState[type].page = 1;
    renderSpecificTable(type);
}

/** Master router that triggers the correct HTML render function based on the active table type. */
function renderSpecificTable(type) {
    if (type === 'networks') renderNetworks();
    else if (type === 'dns') renderToolLogs('dns');
    else if (type === 'ping') renderToolLogs('ping');
    else if (type === 'wifi') renderWifiHistoryTable();
    else if (type === 'wifiNetHist') renderWifiNetworksHistory();
    else if (type === 'history') renderHistory();
}

/**
 * Permanently deletes a single DNS or Ping tool log from the database.
 * @param {string} type - 'dns' or 'ping'.
 * @param {string|number} id - Record ID to delete.
 */
function deleteSingleToolLog(type, id) {
    if(confirm(`Delete this ${type.toUpperCase()} log entry?`)) {
        fetch('/api/bulk_delete', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type, ids: [id] })
        }).then(() => fetchToolLogs(type));
    }
}

/** Permanently clears all unprotected DNS or Ping logs from the database. */
function clearToolLogs(type) {
    if(confirm(`Clear all ${type.toUpperCase()} history?`)) fetch(`/api/${type}/clear`, {method:'POST'}).then(() => fetchToolLogs(type));
}

/**
 * Re-names a device custom name globally. Overrides the older prompt logic 
 * to automatically refresh whichever UI page triggered it.
 */
function updateDeviceName(mac, old) { 
    const n = prompt("Set Custom Name:", (old === 'null' || old === 'undefined') ? '' : old); 
    if (n !== null) {
        fetch('/api/devices/update_name', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify({mac: mac, network_id: currentNetworkId || null, name: n})
        }).then(() => {
            if (!document.getElementById('devices').classList.contains('hidden') && currentNetworkId) {
                loadNetworkDevices(currentNetworkId, ""); 
            }
            if (!document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
        }); 
    }
}

/**
 * Renders the table of historical Network Scans (Subnets/VLANs).
 * Applies pagination via the getPaginatedData() helper.
 */
function renderNetworks() {
    const tb = document.getElementById('network-list');
    if(!tb) return;
    const paginatedData = getPaginatedData('networks');

    tb.innerHTML = paginatedData.length ? paginatedData.map(n => {
        const safeName = escapeJS(n.name);
        const safeComment = escapeJS(n.comments || '');
        const isChecked = tableState.networks.selected.has(String(n.id)) ? 'checked' : '';
        const isProtected = n.is_protected ? 1 : 0; 
        const isMatchable = n.allow_matching !== undefined ? (n.allow_matching ? 1 : 0) : 1; 
        
        return `
        <tr>
            <td onclick="event.stopPropagation()"><input type="checkbox" class="networks-check" value="${n.id}" onchange="toggleSelection('networks', this)" ${isChecked}></td>
            <td><strong>${escapeHTML(n.name)}</strong></td>
            <td class="font-monospace small">${n.gateway_mac}</td>
            <td>${n.gateway_ip}</td>
            <td><span class="badge bg-secondary">${n.device_count}</span></td>
            <td><small class="text-muted">${escapeHTML(n.comments || '')}</small></td>
            <td><small>${n.last_scan}</small></td>
            <td class="text-end text-nowrap">
                <div class="btn-group">
                    <button class="btn btn-sm ${isMatchable ? 'btn-outline-success' : 'btn-outline-secondary'}" onclick="toggleMatching(${n.id}, ${isMatchable})" title="${isMatchable ? 'Auto-Matching Enabled (Click to Disable)' : 'Auto-Matching Disabled (Click to Enable)'}">
                        <i class="bi ${isMatchable ? 'bi-diagram-3-fill' : 'bi-diagram-3'}"></i>
                    </button>
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('networks', ${n.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="loadNetworkDevices(${n.id}, '${safeName}', '${safeComment}')" title="View Devices"><i class="bi bi-eye"></i> Load</button> 
                    <button class="btn btn-sm btn-outline-secondary" onclick="exportSpecificNetwork(${n.id}, '${safeName}')" title="Export Devices CSV"><i class="bi bi-download"></i></button> 
                    <button class="btn btn-sm btn-outline-secondary" onclick="openNetworkConfig(${n.id}, '${safeName}', '${safeComment}')" title="Edit Name & Comment"><i class="bi bi-pencil"></i></button> 
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteNetwork(${n.id})" title="Delete"><i class="bi bi-trash"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" class="text-center text-muted p-4">No saved networks found.</td></tr>';

    updateMasterCheckbox('networks', paginatedData.map(n => n.id));
}

/**
 * Commits a Network Merge operation, combining the devices and histories of multiple network scans into a single Target profile.
 * Deletes the source profiles automatically after transferring their records.
 */
function executeMerge() {
    const targetId = document.getElementById('merge-target-select').value;
    const sourceIds = Array.from(tableState.networks.selected).filter(id => id !== targetId);

    if (!targetId || sourceIds.length === 0) return;

    if(!confirm("Are you certain you want to merge these networks? This action cannot be undone.")) return;

    const btn = document.getElementById('btn-execute-merge');
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Merging...';
    btn.disabled = true;

    fetch('/api/networks/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: targetId, source_ids: sourceIds })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            mergeModal.hide();
            tableState.networks.selected.clear();
            loadNetworks(); // Refresh the list automatically
        } else {
            alert("Merge failed: " + d.error);
        }
    })
    .catch(err => alert("Communication error: " + err.message))
    .finally(() => {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    });
}


    // --- 3. WI-FI HISTORY ---
/**
 * Loads the local Wi-Fi scan history (instances of physical scans).
 */
function loadWifiHistory() {
    fetch('/api/wifi/history')
        .then(r => r.json())
        .then(d => {
            tableState.wifi.data = d; 
            tableState.wifi.filtered = d;
            tableState.wifi.page = 1;
            tableState.wifi.selected.clear();
            performSort('wifi');
            renderWifiHistoryTable();
        })
        .catch(err => console.error("History Load Error:", err));
}

/**
 * Renders the table for physical Wi-Fi scans.
 */
function renderWifiHistoryTable() {
    const tb = document.getElementById('wifi-history-table');
    if (!tb) return;
    const paginatedData = getPaginatedData('wifi');

    tb.innerHTML = paginatedData.length ? paginatedData.map(h => {
        const safeName = escapeJS(h.name);
        const safeComment = escapeJS(h.comments);
        const isChecked = tableState.wifi.selected.has(String(h.id)) ? 'checked' : '';
        return `
        <tr>
            <td onclick="event.stopPropagation()"><input type="checkbox" class="wifi-check" value="${h.id}" onchange="toggleSelection('wifi', this)" ${isChecked}></td>
            <td><small class="text-muted">${h.timestamp}</small></td>
            <td><div class="fw-bold text-primary">${escapeHTML(h.name)}</div></td>
            <td><small class="text-muted">${escapeHTML(h.comments) || 'No comments'}</small></td>
            <td class="text-end text-nowrap">
                <div class="btn-group">
                    <button class="btn btn-sm ${h.is_protected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('wifi', ${h.id}, ${h.is_protected})" title="${h.is_protected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${h.is_protected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="viewPastWifi(${h.id})" title="View Results"><i class="bi bi-eye"></i></button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="exportWifiCSV(${h.id})" title="Export CSV"><i class="bi bi-download"></i></button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="openWifiScanConfig(${h.id}, '${safeName}', '${safeComment}')" title="Edit Name/Comment"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteWifiScan(${h.id})" title="Delete"><i class="bi bi-trash"></i></button>
                </div>
            </td>
        </tr>`}).join('') : '<tr><td colspan="5" class="text-center p-4 text-muted">No past scans found.</td></tr>';
        
    updateMasterCheckbox('wifi', paginatedData.map(h => h.id));
}

/**
 * Searches and filters the Wi-Fi scan history.
 */
function filterWifiHistory() {
    const q = document.getElementById('wifi-filter').value.toLowerCase();
    tableState.wifi.filtered = tableState.wifi.data.filter(h => 
        h.name.toLowerCase().includes(q) || (h.comments && h.comments.toLowerCase().includes(q))
    );
    tableState.wifi.page = 1;
    performSort('wifi');
    renderWifiHistoryTable();
}

/**
 * Aggregates all unique SSIDs across all scans and renders the Global Wi-Fi Directory.
 */
function loadWifiNetworksHistory() {
    const tb = document.getElementById('wifi-net-hist-table');
    if (tb) tb.innerHTML = '<tr><td colspan="7" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Loading Wi-Fi History...</h5></td></tr>';

    fetch('/api/wifi_networks_history')
        .then(r => r.json())
        .then(d => {
            if(d.error) throw new Error(d.error);
            tableState.wifiNetHist.data = d;
            tableState.wifiNetHist.filtered = d;
            tableState.wifiNetHist.page = 1;
            tableState.wifiNetHist.selected.clear();
            performSort('wifiNetHist');
            renderWifiNetworksHistory();
        })
        .catch(err => {
            if (tb) tb.innerHTML = `<tr><td colspan="7" class="text-center p-4 text-danger">Error: ${err.message}</td></tr>`;
        });
}

/** Searches and filters the Global Wi-Fi Directory based on user input. */
function filterWifiNetHist() {
    const q = document.getElementById('wifiNetHist-filter').value.toLowerCase();
    tableState.wifiNetHist.filtered = tableState.wifiNetHist.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
    tableState.wifiNetHist.page = 1;
    performSort('wifiNetHist');
    renderWifiNetworksHistory();
}

/** Renders the paginated Global Wi-Fi Directory table. */
function renderWifiNetworksHistory() {
    const tb = document.getElementById('wifi-net-hist-table');
    if (!tb) return;
    const paginatedData = getPaginatedData('wifiNetHist');

    tb.innerHTML = paginatedData.length ? paginatedData.map(n => {
        const isChecked = tableState.wifiNetHist.selected.has(n.ssid) ? 'checked' : '';
        const safeSSID = escapeJS(n.ssid);
        const safeComment = escapeJS(n.comments || '');
        const isProtected = n.is_protected ? 1 : 0; 
        
        return `
        <tr style="cursor: pointer;" onclick="viewWifiNetworkDetails('${safeSSID}')">
            <td onclick="event.stopPropagation()"><input type="checkbox" class="wifiNetHist-check" value="${n.ssid}" onchange="toggleSelection('wifiNetHist', this)" ${isChecked}></td>
            <td onclick="event.stopPropagation(); openWifiSSIDConfig('${safeSSID}', '${safeComment}')" style="cursor:pointer" title="Edit SSID Global Comment">
                <div class="d-flex align-items-center">
                    <strong>${escapeHTML(n.ssid)}</strong>
                    <i class="bi bi-pencil ms-2 small text-muted"></i>
                </div>
                <small class="text-muted" style="font-size: 0.75rem;">${escapeHTML(n.comments || '')}</small>
            </td>
            <td>${n.auth}</td>
            <td><span class="badge bg-info text-dark">${n.mac_count}</span></td>
            <td><span class="badge bg-secondary">${n.scan_count}</span></td>
            <td><small class="text-muted">${n.first_seen}</small></td>
            <td><small>${n.last_seen}</small></td>
            <td class="text-end text-nowrap" onclick="event.stopPropagation()">
                <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('wifi_ssid', '${safeSSID}', ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock SSID (Protect from Cleanup)'}">
                    <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteGlobalSSID('${safeSSID}')" title="Delete SSID"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" class="text-center p-4">No Wi-Fi history found.</td></tr>';

    updateMasterCheckbox('wifiNetHist', paginatedData.map(n => n.ssid));
}

/**
 * Pre-populates the modal configuration window for editing a Wi-Fi Scan Profile (Name and Comments).
 */
function openWifiScanConfig(id, name, comment) {
    if (!id) return;
    document.getElementById('modal-wifi-scan-id').value = id;
    document.getElementById('modal-wifi-scan-name').value = name || "";
    document.getElementById('modal-wifi-scan-comment').value = comment || "";
    wifiScanModal.show();
}

/**
 * Submits the updated name and comment for a specific Wi-Fi scan to the database.
 */
function saveWifiScanSettings() {
    const id = document.getElementById('modal-wifi-scan-id').value;
    const name = document.getElementById('modal-wifi-scan-name').value.trim();
    const comment = document.getElementById('modal-wifi-scan-comment').value.trim();

    if (!id) return;

    const btn = event.currentTarget || document.querySelector('#wifiScanModal .btn-primary');
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    btn.disabled = true;

    fetch('/api/wifi/history/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, name: name, comment: comment })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "success") {
            wifiScanModal.hide();
            if (String(id) === String(window.currentWifiScanId)) {
                const titleEl = document.getElementById('wifi-tab-title');
                if (titleEl) titleEl.innerText = name;
            }
            loadWifiHistory(); 
        } else alert("Error: " + data.error);
    }).finally(() => { btn.innerHTML = origText; btn.disabled = false; });
}

/** Legacy logic handler to trigger renaming from the browser prompt. */
function renameActiveWifiScan() {
    if (!window.currentWifiScanId) return;
    const titleEl = document.getElementById('wifi-tab-title');
    const oldName = titleEl.innerText;
    
    const newName = prompt("Rename Wi-Fi Scan:", oldName); 
    if (newName && newName !== oldName) {
        fetch('/api/wifi/history/update', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify({id: window.currentWifiScanId, name: newName, comment: ""})
        }).then(r => r.json()).then(res => {
            if (res.status === 'success') titleEl.innerText = newName;
            else alert("Failed to rename Wi-Fi scan.");
        }); 
    }
}

/** Initiates global CSV export for selected elements on the Wi-Fi Networks summary page. */
function exportWifiNetHistCSV() {
    let dataToExport = [];
    if (tableState.wifiNetHist.selected.size > 0) {
        const selected = Array.from(tableState.wifiNetHist.selected);
        dataToExport = tableState.wifiNetHist.data.filter(d => selected.includes(d.ssid));
    } else {
        dataToExport = tableState.wifiNetHist.filtered;
        if(!dataToExport.length) return alert("No networks to export.");
        if(!confirm(`No specific items selected. Export all ${dataToExport.length} visible record(s)?`)) return;
    }

    const rows = dataToExport.map(d => ({
        "SSID": d.ssid,
        "Authentication": d.auth,
        "Unique MACs": d.mac_count,
        "Times Scanned": d.scan_count,
        "First Seen": d.first_seen,
        "Last Seen": d.last_seen,
        "Comments": d.comments || ""
    }));

    fetch('/api/devices/export', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rows: rows})
    }).then(r=>r.blob()).then(b=>{
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u;
        a.download = `global_wifi_networks_history_${new Date().getTime()}.csv`;
        a.click();
    });
}


    // --- 4. DNS & PING TOOLS ---
/**
 * Fetches the backend logs for DNS queries or Ping sweeps.
 * @param {string} type - 'dns' or 'ping'.
 */
function fetchToolLogs(type) {
    fetch(`/api/${type}/logs`).then(r => r.json()).then(d => {
        tableState[type].data = d;
        tableState[type].filtered = d;
        tableState[type].page = 1;
        tableState[type].selected.clear();
        performSort(type);
        renderToolLogs(type);
    });
}

/**
 * Implements a case-insensitive fuzzy search across all values in the Tool Logs table.
 * @param {string} type - 'dns' or 'ping'.
 */
function filterToolLogs(type) {
    const q = document.getElementById(`${type}-filter`).value.toLowerCase();
    tableState[type].filtered = tableState[type].data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
    tableState[type].page = 1;
    performSort(type);
    renderToolLogs(type);
}

/**
 * Renders the HTML table for either DNS or Ping logs, formatting the badges appropriately.
 * @param {string} type - 'dns' or 'ping'.
 */
function renderToolLogs(type) {
    const tbody = document.getElementById(`${type}-log-list`);
    if (!tbody) return;
    const paginatedData = getPaginatedData(type);
    
    if (type === 'dns') {
        tbody.innerHTML = paginatedData.length ? paginatedData.map(x => {
            const isChecked = tableState.dns.selected.has(String(x.id)) ? 'checked' : '';
            const isProtected = x.is_protected ? 1 : 0; 
            const safeName = escapeJS(x.network_name || '');
            const safeComment = escapeJS(x.comments || '');
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="dns-check" value="${x.id}" onchange="toggleSelection('dns', this)" ${isChecked}></td>
                <td><small class="text-muted">${x.timestamp}</small></td>
                <td><strong>${escapeHTML(x.domain)}</strong></td>
                <td class="font-monospace">${x.result_ip}</td>
                <td><span class="badge ${x.status==='Resolved'?'bg-success-subtle text-success':'bg-danger-subtle text-danger'}">${x.status}</span></td>
                <td><small>${x.router_ip || '-'}</small></td>
                <td><small>${escapeHTML(x.network_name) || '-'}</small></td>
                <td><small>${x.lan_ip || '-'}</small></td>
                <td><small class="text-muted">${escapeHTML(x.comments || '')}</small></td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'} border-0" onclick="toggleProtection('dns', ${x.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary border-0" onclick="openToolLogConfig('dns', ${x.id}, '${safeName}', '${safeComment}')" title="Edit Network & Comment"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('dns', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`}).join('') : '<tr><td colspan="10" class="text-center p-4">No logs found.</td></tr>';
    } else {
        tbody.innerHTML = paginatedData.length ? paginatedData.map(x => {
            const isChecked = tableState.ping.selected.has(String(x.id)) ? 'checked' : '';
            const isProtected = x.is_protected ? 1 : 0; 
            const safeName = escapeJS(x.network_name || '');
            const safeComment = escapeJS(x.comments || '');
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="ping-check" value="${x.id}" onchange="toggleSelection('ping', this)" ${isChecked}></td>
                <td><small class="text-muted">${x.timestamp}</small></td>
                <td><strong>${escapeHTML(x.target)}</strong></td>
                <td><span class="badge ${x.status==='Success'?'bg-success':'bg-warning'}">${x.status}</span></td>
                <td>${x.latency}</td>
                <td>${x.packet_loss}</td>
                <td><small>${x.router_ip || '-'}</small></td>
                <td><small>${escapeHTML(x.network_name) || '-'}</small></td>
                <td><small>${x.lan_ip || '-'}</small></td>
                <td><small class="text-muted">${escapeHTML(x.comments || '')}</small></td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'} border-0" onclick="toggleProtection('ping', ${x.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary border-0" onclick="openToolLogConfig('ping', ${x.id}, '${safeName}', '${safeComment}')" title="Edit Network & Comment"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('ping', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`}).join('') : '<tr><td colspan="11" class="text-center p-4">No logs found.</td></tr>';
    }
    updateMasterCheckbox(type, paginatedData.map(x => x.id));
}

/**
 * Pre-populates the modal configuration window for editing a DNS or Ping tool log.
 */
function openToolLogConfig(type, id, name, comment) {
    if (!id) return;
    document.getElementById('modal-tool-type').value = type;
    document.getElementById('modal-tool-id').value = id;
    document.getElementById('modal-tool-name').value = name || "";
    document.getElementById('modal-tool-comment').value = comment || "";
    
    document.getElementById('toolModalTitle').innerText = `Edit ${type.toUpperCase()} Log Entry`;
    toolLogModal.show();
}

/**
 * Saves the edited Network Name and Comments for a DNS or Ping tool log.
 */
function saveToolLogSettings() {
    const type = document.getElementById('modal-tool-type').value;
    const id = document.getElementById('modal-tool-id').value;
    const name = document.getElementById('modal-tool-name').value.trim();
    const comment = document.getElementById('modal-tool-comment').value.trim();

    if (!id || !type) return;

    const btn = event.currentTarget || document.querySelector('#toolLogModal .btn-primary');
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    btn.disabled = true;

    fetch('/api/tool_logs/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type, id: id, name: name, comment: comment })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            toolLogModal.hide();
            fetchToolLogs(type); 
        } else {
            alert("Error updating record: " + d.error);
        }
    })
    .catch(err => alert("Communication error: " + err.message))
    .finally(() => {
        btn.innerHTML = origText;
        btn.disabled = false;
    });
}

/**
 * Automatically exports selected Tool Logs (DNS/Ping) or all filtered logs as a CSV spreadsheet.
 * Formats multi-line comments appropriately for safe CSV injection.
 * @param {string} type - 'dns' or 'ping'.
 */
function exportToolLogs(type) {
    let dataToExport = [];
    if (tableState[type].selected.size > 0) {
        const selectedIds = Array.from(tableState[type].selected);
        dataToExport = tableState[type].data.filter(d => selectedIds.includes(String(d.id)));
    } else {
        dataToExport = tableState[type].filtered;
        if (!dataToExport.length) return alert("No logs available to export.");
        if (!confirm(`No specific items selected. Export all ${dataToExport.length} visible record(s)?`)) return;
    }

    const rows = type === 'dns' 
        ? dataToExport.map(d => [d.timestamp, d.domain, d.result_ip, d.status, d.router_ip||'-', d.network_name||'-', d.lan_ip||'-', d.comments ? d.comments.replace(/"/g, '""').replace(/\n/g, ' ') : ''])
        : dataToExport.map(d => [d.timestamp, d.target, d.status, d.latency, d.packet_loss, d.router_ip||'-', d.network_name||'-', d.lan_ip||'-', d.comments ? d.comments.replace(/"/g, '""').replace(/\n/g, ' ') : '']);
    
    const headers = type === 'dns' 
        ? ['Timestamp', 'Domain', 'Result IP', 'Status', 'Router IP', 'Network', 'LAN IP', 'Comments']
        : ['Timestamp', 'Target', 'Status', 'Latency', 'Loss', 'Router IP', 'Network', 'LAN IP', 'Comments'];

    const csvContent = [headers.join(','), ...rows.map(r => `"${r.join('","')}"`)].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${type}_logs_${new Date().getTime()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
}


/** Initiates a ZIP file download containing CSV exports for the selected network or Wi-Fi scans. */
async function bulkExport(type) {
    const config = {
        'networks': { endpoint: '/api/networks/bulk_export', file: 'networks_bulk_export.zip' },
        'wifi': { endpoint: '/api/wifi/bulk_export', file: 'wifi_scans_bulk_export.zip' }
    };
    
    let ids = [];
    if (tableState[type].selected.size > 0) {
        ids = Array.from(tableState[type].selected);
    } else {
        ids = tableState[type].filtered.map(x => String(x.id));
        if (!ids.length) return alert("No items available to export.");
        if (!confirm(`No specific items selected. Export all ${ids.length} visible item(s)?`)) return;
    }

    const btn = event.currentTarget;
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Zipping...';
    btn.disabled = true;

    try {
        const response = await fetch(config[type].endpoint, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: ids })
        });
        
        if (!response.ok) throw new Error("Export failed");
        
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = config[type].file;
        document.body.appendChild(a);
        a.click();
        a.remove();
    } catch(err) {
        alert("Bulk export failed: " + err.message);
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
    }
}

// --- UNIFIED STATE MANAGER ---
    const tableState = {
        adapters: { selected: new Set(), sortKey: '', sortAsc: true },
        networks: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'last_scan', sortAsc: false },
        dns: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'timestamp', sortAsc: false },
        ping: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'timestamp', sortAsc: false },
        wifi: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'timestamp', sortAsc: false },
        wifiNetHist: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'last_seen', sortAsc: false }, 
        history: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set(), sortKey: 'timestamp', sortAsc: false },
        devices: { sortKey: 'ip_address', sortAsc: true },
        devHist: { sortKey: 'last_seen', sortAsc: false },
        devHistModalTable: { sortKey: 'last_seen', sortAsc: false },
        wifiNetModalTable: { sortKey: 'timestamp', sortAsc: false }
    };
    
/**
 * Calculates and returns the correct subset of data required for rendering the current page of a table.
 * Manages the "Page X of Y" UI elements and enables/disables the Next/Prev buttons.
 * @param {string} type - The table identifier key (e.g., 'networks', 'dns').
 * @returns {Array} The subset of data items to map and render.
 */
function getPaginatedData(type) {
    const state = tableState[type];
    const totalItems = state.filtered.length;
    let totalPages = state.perPage === 'all' ? 1 : Math.ceil(totalItems / state.perPage);
    if (totalPages === 0) totalPages = 1;
    if (state.page > totalPages) state.page = totalPages;

    const infoEl = document.getElementById(`${type}-page-info`);
    const prevBtn = document.getElementById(`btn-${type}-prev`);
    const nextBtn = document.getElementById(`btn-${type}-next`);
    
    if (infoEl) infoEl.innerText = `Page ${state.page} of ${totalPages} (${totalItems} items)`;
    if (prevBtn) prevBtn.disabled = state.page <= 1;
    if (nextBtn) nextBtn.disabled = state.page >= totalPages;

    if (state.perPage === 'all') return state.filtered;
    const start = (state.page - 1) * state.perPage;
    return state.filtered.slice(start, start + state.perPage);
}

/**
 * Handles individual checkbox toggles within a data table.
 * Adds or removes the ID from the active tracking set and evaluates the state of the Master Checkbox.
 */
function toggleSelection(type, cb) {
    if (cb.checked) tableState[type].selected.add(cb.value);
    else tableState[type].selected.delete(cb.value);
    updateMasterCheckbox(type);
}

/**
 * Master Checkbox handler. Toggles all visible checkboxes on the current page.
 */
function toggleAll(type, masterCb) {
    const checkboxes = document.querySelectorAll(`.${type}-check`);
    checkboxes.forEach(cb => {
        cb.checked = masterCb.checked;
        if (masterCb.checked) tableState[type].selected.add(cb.value);
        else tableState[type].selected.delete(cb.value);
    });
}

/**
 * Updates the visual state (checked/unchecked) of the Master Checkbox based on the individual rows currently selected.
 */
function updateMasterCheckbox(type, paginatedIds = null) {
    const masterCheck = document.getElementById(`${type}-master-check`);
    if (masterCheck) {
        // Use supplied dynamic IDs during render, otherwise query the physical DOM elements
        const idsToCheck = paginatedIds || Array.from(document.querySelectorAll(`.${type}-check`)).map(el => el.value);
        masterCheck.checked = idsToCheck.length > 0 && idsToCheck.every(id => tableState[type].selected.has(String(id)));
    }
}

/**
 * Reusable function that triggers the mass-deletion of selected records in the backend database.
 * Auto-refreshes the appropriate UI table once completed.
 * @param {string} type - The table identifier key (e.g., 'networks', 'dns').
 */
function bulkDelete(type) {
    const selectedIds = Array.from(tableState[type].selected);
    if (!selectedIds.length) return alert("Please select at least one item.");
    if (!confirm(`Are you sure you want to delete ${selectedIds.length} selected item(s)?`)) return;

    fetch('/api/bulk_delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type, ids: selectedIds })
    }).then(() => {
        if (type === 'networks') loadNetworks();
        else if (type === 'wifi') loadWifiHistory();
        else if (type === 'history') fetchHistory();
        else if (type === 'dns') fetchToolLogs('dns');
        else if (type === 'ping') fetchToolLogs('ping');
    });
}

/**
 * Loads the Global Device Directory, displaying the most recent known state of every device
 * across all recorded network scans.
 */
function loadDeviceHistory() {
    const tb = document.getElementById('dev-hist-table');
    if (tb) tb.innerHTML = '<tr><td colspan="7" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Loading History...</h5><p class="small text-secondary">Gathering global device data.</p></td></tr>';

    fetch('/api/device_history')
        .then(r => r.json())
        .then(data => {
            if (data.error) throw new Error(data.error);

            allDeviceHistory = data;
            devHistFiltered = data; 
            devHistCurrentPage = 1;
            selectedDevHistMacs.clear(); 
            performSort('devHist');
            renderDeviceHistory();
        })
        .catch(err => {
            console.error("Device History Error:", err);
            if (tb) tb.innerHTML = `<tr><td colspan="7" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle-fill"></i> Failed to load history: ${err.message}</td></tr>`;
        });
}

/**
 * Handles individual checkbox toggles for the Device History view.
 */
function toggleDevHistSelection(cb) {
    if (cb.checked) selectedDevHistMacs.add(cb.value);
    else selectedDevHistMacs.delete(cb.value);
    
    const masterCheck = document.getElementById('dev-hist-master-check');
    if (masterCheck) {
        const pageMacs = Array.from(document.querySelectorAll('.dev-hist-check')).map(el => el.value);
        masterCheck.checked = pageMacs.length > 0 && pageMacs.every(mac => selectedDevHistMacs.has(mac));
    }
}

/**
 * Toggles all checkboxes on the current paginated view for Device History.
 */
function toggleAllDevHist(masterCb) {
    const checkboxes = document.querySelectorAll('.dev-hist-check');
    checkboxes.forEach(cb => {
        cb.checked = masterCb.checked;
        if (masterCb.checked) selectedDevHistMacs.add(cb.value);
        else selectedDevHistMacs.delete(cb.value);
    });
}

/**
 * Renders the paginated Global Device History table.
 * Automatically initiates background HTTP queries for devices missing vendor data.
 */
function renderDeviceHistory() {
    const tb = document.getElementById('dev-hist-table');
    if (!tb) return;

    if (!Array.isArray(devHistFiltered)) {
        tb.innerHTML = '<tr><td colspan="9" class="text-center p-4 text-danger">Invalid data received from server.</td></tr>';
        return;
    }

    const totalItems = devHistFiltered.length;
    let totalPages = devHistItemsPerPage === 'all' ? 1 : Math.ceil(totalItems / devHistItemsPerPage);
    if (totalPages === 0) totalPages = 1;
    if (devHistCurrentPage > totalPages) devHistCurrentPage = totalPages;

    const infoEl = document.getElementById('dev-hist-page-info');
    if (infoEl) infoEl.innerText = `Page ${devHistCurrentPage} of ${totalPages} (${totalItems} items)`;
    document.getElementById('btn-dev-hist-prev').disabled = devHistCurrentPage <= 1;
    document.getElementById('btn-dev-hist-next').disabled = devHistCurrentPage >= totalPages;

    let paginatedData = devHistFiltered;
    if (devHistItemsPerPage !== 'all') {
        const start = (devHistCurrentPage - 1) * devHistItemsPerPage;
        const end = start + devHistItemsPerPage;
        paginatedData = devHistFiltered.slice(start, end);
    }

    const missingVendors = [];

    tb.innerHTML = paginatedData.length ? paginatedData.map(d => {
        const displayName = d.custom_name || d.clean_hostname || "Unknown";
        const safeName = escapeJS(displayName);
        const safeVendor = escapeJS(d.vendor);
        const escapedMac = escapeJS(d.mac_address);
        
        const safeCustomN = escapeJS(d.custom_name);
        const safeCustomV = escapeJS(d.custom_vendor || '');
        const safeLookupV = escapeJS(d.vendor || '');
        const safeComment = escapeJS(d.comments || '');
        const isProtected = d.is_protected ? 1 : 0;
        
        let vendorHtml = escapeHTML(d.vendor);
        if (!d.vendor || d.vendor === 'Unknown') {
            const safeMacId = d.mac_address.replace(/:/g, '');
            vendorHtml = `<span id="vendor-${safeMacId}" class="text-muted fst-italic"><span class="spinner-border spinner-border-sm me-1" style="width: 0.8rem; height: 0.8rem;"></span> Fetching...</span>`;
            missingVendors.push(d.mac_address);
        }
        
        const isChecked = selectedDevHistMacs.has(d.mac_address) ? 'checked' : '';
        
        return `
        <tr style="cursor: pointer;" onclick="viewDeviceDetails('${d.mac_address}', '${safeName}', '${safeVendor}', '${safeComment}')">
            <td onclick="event.stopPropagation()">
                <input type="checkbox" class="dev-hist-check" value="${d.mac_address}" onchange="toggleDevHistSelection(this)" ${isChecked}>
            </td>
            <td onclick="event.stopPropagation(); openDeviceConfig('${escapedMac}', '${safeCustomN}', '${safeCustomV || safeLookupV}', '${safeComment}')" style="cursor:pointer" title="Edit Device Metadata">
                <strong>${escapeHTML(displayName)}</strong> <i class="bi bi-pencil ms-2 small text-muted"></i>
            </td>
            <td>${vendorHtml}</td>
            <td class="font-monospace text-muted">${d.mac_address}</td>
            <td><span class="badge bg-secondary">${d.network_name}</span></td>
            <td>${d.ip_address}</td>
            <td><small>${d.last_seen}</small></td>
            <td onclick="event.stopPropagation(); openDeviceConfig('${escapedMac}', '${safeCustomN}', '${safeCustomV || safeLookupV}', '${safeComment}')" style="cursor:pointer" title="Edit Device Metadata">
                <small class="text-muted" style="font-size: 0.8rem;">${escapeHTML(d.comments || '')}</small>
            </td>
            <td class="text-end text-nowrap" onclick="event.stopPropagation()">
                <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('devices', '${escapedMac}', ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                    <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                </button>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="9" class="text-center p-4">No device history found.</td></tr>';

    const masterCheck = document.getElementById('dev-hist-master-check');
    if (masterCheck) {
        const pageMacs = paginatedData.map(d => d.mac_address);
        masterCheck.checked = pageMacs.length > 0 && pageMacs.every(mac => selectedDevHistMacs.has(mac));
    }

    if (missingVendors.length > 0) {
        missingVendors.forEach(mac => fetchMissingVendor(mac));
    }
}

/**
 * Changes the current page index for the Device History view.
 */
function changeDevHistPage(direction) {
    devHistCurrentPage += direction;
    renderDeviceHistory();
}

/**
 * Adjusts the pagination limit for the Device History view.
 */
function changeDevHistPerPage() {
    const val = document.getElementById('dev-hist-per-page').value;
    devHistItemsPerPage = val === 'all' ? 'all' : parseInt(val);
    devHistCurrentPage = 1; 
    renderDeviceHistory();
}

/**
 * Searches the Device History array based on user input.
 */
function filterDevHist() {
    const q = document.getElementById('dev-hist-filter').value.toLowerCase();
    devHistFiltered = allDeviceHistory.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
    devHistCurrentPage = 1; 
    performSort('devHist');
    renderDeviceHistory();
}

/**
 * Executes a background fetch to the backend API to identify the hardware manufacturer of a device using its MAC address.
 * Updates the UI cell dynamically upon completion.
 * @param {string} mac - The physical MAC address to lookup.
 */
function fetchMissingVendor(mac) {
    fetch('/api/vendor/lookup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mac: mac })
    })
    .then(r => r.json())
    .then(data => {
        const safeMacId = mac.replace(/:/g, '');
        const el = document.getElementById(`vendor-${safeMacId}`);
        if (el) {
            el.outerHTML = data.vendor !== 'Unknown' ? data.vendor : '<span class="text-muted">Unknown</span>';
        }
        const globalItem = allDeviceHistory.find(x => x.mac_address === mac);
        if (globalItem) globalItem.vendor = data.vendor;
    })
    .catch(err => {
        const safeMacId = mac.replace(/:/g, '');
        const el = document.getElementById(`vendor-${safeMacId}`);
        if (el) el.outerHTML = '<span class="text-muted">Failed</span>';
    });
}

/**
 * Displays a modal window detailing every single historical network scan a specific device was detected in.
 * @param {string} mac - The device MAC address.
 * @param {string} name - Display name.
 * @param {string} vendor - Manufacturer string.
 * @param {string} comment - Global device comment.
 */
function viewDeviceDetails(mac, name, vendor, comment) {
    let hasVendor = vendor && vendor.trim() !== "" && vendor !== "undefined" && vendor !== "Unknown";
    
    let modalTitleHtml = `
        <div class="d-flex justify-content-between align-items-start">
            <div>
                <span class="fw-bold">${escapeHTML(name)}</span> 
                <i class="bi bi-pencil ms-2 text-muted" style="font-size: 0.9rem; cursor:pointer;" onclick="openDeviceConfig('${escapeJS(mac)}', '${escapeJS(name)}', '${escapeJS(vendor)}', '${escapeJS(comment)}')" title="Edit Device Metadata"></i>
                <br><span class="text-muted small fs-7 fw-normal">${escapeHTML(mac)}</span>`;
    
    if (hasVendor) {
        modalTitleHtml += `<br><span class="text-muted small fs-7 fw-normal" id="modal-vendor-container">Make: <span id="modal-vendor-val">${escapeHTML(vendor)}</span></span>`;
    } else {
        modalTitleHtml += `<br><span class="text-muted small fs-7 fw-normal" id="modal-vendor-container">Make: <span id="modal-vendor-val" class="fst-italic"><span class="spinner-border spinner-border-sm me-1" style="width: 0.7rem; height: 0.7rem;"></span> Fetching...</span></span>`;
    }
    
    modalTitleHtml += `</div></div>`;
        
    if(comment && comment !== 'undefined') {
         modalTitleHtml += `<div class="mt-2 p-2 bg-body-tertiary rounded small border fw-normal">${escapeHTML(comment).replace(/\n/g, '<br>')}</div>`;
    }
    
    document.getElementById('devHistModalTitle').innerHTML = modalTitleHtml;
    const tbody = document.getElementById('devHistModalBody');
    const exportBtn = document.getElementById('btn-export-dev-modal');
    
    tbody.innerHTML = '<tr><td colspan="5" class="text-center p-3"><div class="spinner-border spinner-border-sm text-primary"></div></td></tr>';
    exportBtn.style.display = 'none'; 
    devHistModal.show();

    if (!hasVendor) {
        fetch('/api/vendor/lookup', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mac: mac })
        }).then(r => r.json()).then(res => {
            const fetchedVendor = (res.vendor && res.vendor !== 'Unknown') ? res.vendor : 'Unknown';
            const valEl = document.getElementById('modal-vendor-val');
            if (valEl) valEl.outerHTML = `<span id="modal-vendor-val">${escapeHTML(fetchedVendor)}</span>`;
            const globalItem = allDeviceHistory.find(x => x.mac_address === mac);
            if (globalItem) globalItem.vendor = fetchedVendor;
        }).catch(() => {
            const valEl = document.getElementById('modal-vendor-val');
            if (valEl) valEl.outerHTML = '<span id="modal-vendor-val">Unknown</span>';
        });
    }

    fetch(`/api/device_history/${mac}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) throw new Error(data.error);
            currentModalDevData = data; 
            exportBtn.style.display = 'block';
            exportBtn.onclick = () => exportSingleDeviceHistory(mac, name);
            performSort('devHistModalTable');
            renderDevHistModalTable();
        })
        .catch(err => {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center p-3 text-danger">Failed to load details.</td></tr>`;
        });
}

/**
 * Renders the table inside the 'Device Details' modal.
 */
function renderDevHistModalTable() {
    const tbody = document.getElementById('devHistModalBody');
    if (!currentModalDevData) return;
    
    tbody.innerHTML = currentModalDevData.length ? currentModalDevData.map(x => {
        let historyHtml = `<span class="badge bg-secondary">Unknown</span>`;
        if (x.discovery_status === 'New Device') historyHtml = `<span class="badge bg-success">New Device</span>`;
        else if (x.previous_ip) historyHtml = `<span class="badge bg-warning text-dark">IP Changed <small>(${x.previous_ip})</small></span>`;
        else historyHtml = `<span class="badge bg-info text-dark">Seen Before</span>`;

        let serviceHtml = x.services && x.services !== "None" && x.services !== "NONE" 
            ? `<span class="small">${escapeHTML(x.services)}</span>` 
            : '<span class="text-muted">-</span>';

        return `
        <tr>
            <td><strong>${escapeHTML(x.network_name)}</strong></td>
            <td class="font-monospace">${escapeHTML(x.ip_address)}</td>
            <td>${serviceHtml}</td>
            <td>${historyHtml}</td>
            <td><small>${escapeHTML(x.last_seen)}</small></td>
        </tr>`;
    }).join('') : '<tr><td colspan="5" class="text-center p-3">No data available.</td></tr>';
}

/**
 * Deletes all selected devices permanently from the backend database across all networks.
 */
function deleteSelectedDeviceHistory() {
    if (selectedDevHistMacs.size === 0) return alert("Please select at least one device to delete.");
    
    if (!confirm(`Are you sure you want to completely delete the ${selectedDevHistMacs.size} selected device(s)? This will permanently remove them from all scanned networks.`)) return;

    const selectedMacs = Array.from(selectedDevHistMacs);
    const btn = event.currentTarget;
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Deleting...';
    btn.disabled = true;

    fetch('/api/bulk_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'devices', ids: selectedMacs })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            selectedDevHistMacs.clear();
            loadDeviceHistory(); 
        } else {
            alert("Failed to delete devices: " + (d.error || d.message));
        }
    })
    .catch(err => alert("Error: " + err.message))
    .finally(() => {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    });
}

/**
 * Initiates CSV export for the Global Device History view.
 */
function exportDevHistCSV() {
    let dataToExport = [];

    if (selectedDevHistMacs.size > 0) {
        const selectedMacs = Array.from(selectedDevHistMacs);
        dataToExport = allDeviceHistory.filter(d => selectedMacs.includes(d.mac_address));
    } else {
        const q = document.getElementById('dev-hist-filter').value.toLowerCase();
        dataToExport = allDeviceHistory.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
        
        if (!dataToExport.length) return alert("No devices available to export.");
        if (!confirm(`No specific items selected. Export all ${dataToExport.length} visible record(s)?`)) return;
    }

    const rows = dataToExport.map(d => ({
        "Name": d.custom_name || d.clean_hostname,
        "Make (Vendor)": d.vendor || "Unknown",
        "MAC Address": d.mac_address,
        "Last Network": d.network_name,
        "Last IP": d.ip_address,
        "Last Seen": d.last_seen,
        "Comments": d.comments || ""
    }));

    fetch('/api/devices/export', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({rows: rows})
    })
    .then(r => r.blob())
    .then(b => {
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u; 
        a.download = `global_device_history_${new Date().getTime()}.csv`; 
        document.body.appendChild(a);
        a.click();
        a.remove();
    }); 
}

/**
 * Initiates CSV export for a specific device's history log from the details modal.
 */
function exportSingleDeviceHistory(mac, name) {
    if (!currentModalDevData || !currentModalDevData.length) return;
    
    const rows = currentModalDevData.map(x => {
        let historyText = x.discovery_status || "Unknown";
        if (x.previous_ip) historyText = `IP Changed (${x.previous_ip})`;

        return {
        "Network Name": x.network_name,
        "IP Address": x.ip_address,
        "Services (Ports)": convertServicesToPorts(x.services), 
        "Status": historyText,
        "Last Seen": x.last_seen
    };
    });

    fetch('/api/devices/export', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({rows: rows})
    })
    .then(r => r.blob())
    .then(b => {
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u; 
        const safeFileName = name.replace(/\s+/g, '_').toLowerCase();
        a.download = `device_history_${safeFileName}_${mac.replace(/:/g, '')}.csv`; 
        document.body.appendChild(a);
        a.click();
        a.remove();
    }); 
}

/**
 * Loads and displays the specific devices associated with a previously scanned network profile.
 * Prepares the UI buttons (Continue/Split/Isolation Scans) based on the network context.
 * @param {string|number} id - The database ID of the network profile.
 * @param {string} name - The network name for display.
 * @param {string} comment - The network comment for display.
 */
function loadNetworkDevices(id, name, comment = "") {
    currentNetworkId = id; 
    showPage('devices', document.querySelectorAll('.nav-link')[1]);
    
    const b = document.getElementById('btn-scan-devices');
    const cBtn = document.getElementById('btn-continue-scan');
    const splitBtn = document.getElementById('btn-split-scan');
    const isoBtn = document.getElementById('btn-isolation-scan');
    
    if (b) b.innerHTML = '<i class="bi bi-search"></i> New Scan';
    if (cBtn) cBtn.classList.remove('hidden');
    if (splitBtn) splitBtn.classList.remove('hidden'); 
    if (isoBtn) isoBtn.classList.add('hidden'); 
    
    if(name) {
        document.getElementById('device-tab-title').innerText = `Devices in: ${name}`;
        document.getElementById('device-tab-comment').innerText = comment || "";
        const locationInput = document.getElementById('st-network-name');
        if(locationInput) locationInput.value = name;
        
        const editIcon = document.getElementById('btn-edit-active-network');
        if (editIcon) editIcon.classList.remove('hidden');
    }
    
    const btnSel = document.getElementById('btn-remove-selected');
    const btnOff = document.getElementById('btn-remove-offline');
    if (btnSel) btnSel.classList.add('hidden');
    if (btnOff) btnOff.classList.add('hidden');
    
    fetch(`/api/networks/${id}/devices`).then(r=>r.json()).then(d => {
        allDevices = d; 
        performSort('devices');
        filterDevices(); 
        resolveMissingVendors(allDevices);
        if (btnSel) btnSel.classList.remove('hidden');
        if (btnOff) btnOff.classList.add('hidden'); 
    });
}

/**
 * Pre-populates the Network Configuration modal to edit a network profile's Name and Comment.
 */
function openNetworkConfig(id, name, comment) {
    if (!id) return;
    document.getElementById('modal-net-id').value = id;
    document.getElementById('modal-net-name').value = name || "";
    document.getElementById('modal-net-comment').value = comment || "";
    networkModal.show();
}

/**
 * Saves changes to a Network Profile's Name and Comment.
 * Instantly updates the visible UI if the user is currently viewing that network.
 */
function saveNetworkSettings() {
    const id = document.getElementById('modal-net-id').value;
    const name = document.getElementById('modal-net-name').value.trim();
    const comment = document.getElementById('modal-net-comment').value.trim();

    if (!id) return;

    const btn = event.currentTarget || document.querySelector('#networkModal .btn-primary');
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    btn.disabled = true;

    fetch('/api/networks/update', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({id: id, name: name, comment: comment})
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'success') {
            networkModal.hide();
            
            // Instantly update Active Scan UI if we are currently looking at it
            if (String(id) === String(currentNetworkId)) {
                const titleEl = document.getElementById('device-tab-title');
                const commentEl = document.getElementById('device-tab-comment');
                const locationInput = document.getElementById('st-network-name');
                
                if (titleEl) titleEl.innerText = `Devices in: ${name}`;
                if (commentEl) commentEl.innerText = comment;
                if (locationInput) locationInput.value = name;
            }

            loadNetworks();
        } else {
            alert("Failed to update network.");
        }
    })
    .catch(err => alert("Communication error: " + err.message))
    .finally(() => {
        btn.innerHTML = origText;
        btn.disabled = false;
    }); 
}

/**
 * Deletes specifically selected devices from an active network scan profile.
 */
function deleteSelectedNetworkDevices() {
    if (!currentNetworkId) return alert("No network selected. Please scan or load a network first.");
    
    const macs = Array.from(document.querySelectorAll('.device-check:checked')).map(cb => cb.value);
    if (!macs.length) return alert("Please select at least one device.");
    if (!confirm(`Delete ${macs.length} selected device(s) from this network?`)) return;
    
    fetch('/api/networks/devices/delete', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ network_id: currentNetworkId, macs: macs, mode: 'selected' })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            document.getElementById('device-master-check').checked = false; 
            loadNetworkDevices(currentNetworkId, ""); 
        } else {
            alert(d.error);
        }
    });
}

/**
 * Automatically removes all devices marked as "Offline" from the current network profile view.
 */
function deleteOfflineNetworkDevices() {
    if (!currentNetworkId) return alert("No network selected. Please scan or load a network first.");
    
    const offlineCount = allDevices.filter(d => !d.is_online).length;
    if (offlineCount === 0) return alert("No offline devices found to delete.");
    
    if (!confirm(`Are you sure you want to delete all ${offlineCount} offline device(s) from this network view?`)) return;
    
    fetch('/api/networks/devices/delete', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ network_id: currentNetworkId, mode: 'offline' })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            document.getElementById('device-master-check').checked = false;
            loadNetworkDevices(currentNetworkId, ""); 
        } else {
            alert(d.error);
        }
    });
}

/**
 * Updates the visual state of the Master Checkbox on the Active Network Scan page.
 */
function updateDeviceMasterCheck() {
    const checks = document.querySelectorAll('.device-check');
    const checked = document.querySelectorAll('.device-check:checked');
    const master = document.getElementById('device-master-check');
    if (master) master.checked = (checks.length > 0 && checks.length === checked.length);
}

/**
 * Initiates the backend process to zip and download all devices within a specific network as a CSV.
 * @param {string|number} id - Target network ID.
 * @param {string} name - Used for the CSV filename generation.
 */
function exportSpecificNetwork(id, name) {
    fetch(`/api/networks/${id}/devices`)
        .then(r => r.json())
        .then(devices => {
            if (!devices.length) return alert("This network has no devices to export.");

            const rows = devices.map(d => {
                let historyText = d.discovery_status || "Unknown";
                if (d.previous_ip) historyText = `IP Changed (${d.previous_ip})`;
                
                return {
                    "Hostname": d.hostname || "Unknown",
                    "Custom Name": d.custom_name || "",
                    "IP Address": d.ip_address || "0.0.0.0",
                    "MAC Address": d.mac_address || "Unknown",
                    "Status": d.is_online ? "Online" : "Offline",
                    "Services (Ports)": convertServicesToPorts(d.services), 
                    "History": historyText,
                    "Comments": d.comments || ""
                };
            });

            fetch('/api/devices/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rows: rows })
            })
            .then(r => r.blob())
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `devices_${name.replace(/\s+/g, '_')}.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
            });
        })
        .catch(err => alert("Export failed: " + err.message));
}

/** Legacy prompt wrapper for quick-renaming the active network scan. */
function renameActiveNetwork() {
    if (!currentNetworkId) return;
    const titleText = document.getElementById('device-tab-title').innerText;
    const oldName = titleText.replace('Devices in: ', '');
    const newName = prompt("Rename Network:", oldName); 
    if (newName && newName !== oldName) {
        fetch('/api/networks/rename', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify({id: currentNetworkId, name: newName})
        }).then(r => r.json()).then(res => {
            if (res.status === 'success') {
                document.getElementById('device-tab-title').innerText = `Devices in: ${newName}`;
                const locationInput = document.getElementById('st-network-name');
                if (locationInput) locationInput.value = newName;
            } else alert("Failed to rename network.");
        }); 
    }
}

/**
 * Initiates an ARP/ICMP network scan via Server-Sent Events (SSE).
 * Streams device discovery data back to the UI in real-time as the Python backend finds them, 
 * bypassing standard HTTP timeouts and preventing the browser from freezing.
 * 
 * @param {string} mode - 'new', 'continue', 'split', or 'isolation'. Controls database merging behavior.
 * @param {boolean} forceMerge - Bypasses safety warnings if the router MAC/IP mismatches.
 */
function scanDevices(mode = 'new', forceMerge = false) {
    const b = document.getElementById('btn-scan-devices'); 
    const cBtn = document.getElementById('btn-continue-scan');
    const splitBtn = document.getElementById('btn-split-scan');
    const isoBtn = document.getElementById('btn-isolation-scan');
    const tb = document.getElementById('device-list');
    
    // Set UI to loading state to prevent multi-clicking
    b.disabled = true; 
    if (cBtn) cBtn.disabled = true;
    if (splitBtn) splitBtn.disabled = true;
    if (isoBtn) isoBtn.disabled = true;
    
    if (mode === 'continue') {
        if (cBtn) cBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
    } else if (mode === 'split') {
        if (splitBtn) splitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
        allDevices = []; // Split creates a blank new table visually
        if (tb) tb.innerHTML = `<tr><td colspan="10" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Performing Split Scan...</h5></td></tr>`;
    } else if (mode === 'isolation') {
        if (isoBtn) isoBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
        allDevices = []; 
        if (tb) tb.innerHTML = `<tr><td colspan="10" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Performing Isolation Scan...</h5></td></tr>`;
    } else {
        b.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
        allDevices = []; 
        if (tb) tb.innerHTML = `<tr><td colspan="10" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Scanning Network...</h5></td></tr>`;
    }
    
    const btnSel = document.getElementById('btn-remove-selected');
    const btnOff = document.getElementById('btn-remove-offline');
    if (btnSel) btnSel.classList.add('hidden');
    if (btnOff) btnOff.classList.add('hidden');
    
    const editIcon = document.getElementById('btn-edit-active-network');
    if (mode === 'new' && editIcon) editIcon.classList.add('hidden');
    
    let isFirstDevice = (mode !== 'continue');

    // Attach mode flags for the backend URL configuration
    let queryParam = `?mode=${mode}`;
    if ((mode === 'continue' || mode === 'split') && currentNetworkId) {
        queryParam += `&network_id=${currentNetworkId}`;
    }
    if (forceMerge) queryParam += '&force_merge=true';
    
    // Open the SSE Stream
    const eventSource = new EventSource('/api/scan_network_stream' + queryParam);

    // Triggers every time Python yields a new piece of data
    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);

        // Network Mismatch Guardian: Prevents accidental pollution of historical VLAN/Subnet data
        if (data.type === 'mismatch') {
            eventSource.close();
            
            b.disabled = false;
            b.innerHTML = '<i class="bi bi-search"></i> New Scan';
            if (cBtn && mode === 'continue') {
                cBtn.disabled = false;
                cBtn.innerHTML = '<i class="bi bi-play-fill"></i> Continue Scan';
            }
            if (splitBtn && mode === 'split') {
                splitBtn.disabled = false;
                splitBtn.innerHTML = '<i class="bi bi-diagram-2"></i> Split Scan';
            }
            
            if (confirm("We detected that the IP Subnet or Router MAC address is different from this loaded network.\n\nIs this the same network?\n- Click OK to force a merge into this network.\n- Click Cancel to safely start a New Scan instead.")) {
                scanDevices('continue', true); 
            } else {
                scanDevices('new', false); 
            }
            return;
        }

        // Setup headers before devices arrive
        if (data.type === 'init') {
            currentNetworkId = data.network_id;
            document.getElementById('device-tab-title').innerText = `Devices in: ${data.network_name}`;
            document.getElementById('device-tab-comment').innerText = data.network_comment || "";
            const locationInput = document.getElementById('st-network-name');
            if (locationInput) locationInput.value = data.network_name;
        } 
        // Individual device loaded
        else if (data.type === 'device') {
            if (isFirstDevice) {
                tb.innerHTML = '';
                isFirstDevice = false;
            }

            const idx = allDevices.findIndex(d => d.mac_address === data.device.mac_address);
            if (idx >= 0) allDevices[idx] = data.device;
            else allDevices.push(data.device);

            performSort('devices');
            filterDevices(); 
        }
        // Stream completed successfully
        else if (data.type === 'complete') {
            eventSource.close();
            
            document.getElementById('device-tab-title').innerText = `Devices in: ${data.network_name}`;
            document.getElementById('device-tab-comment').innerText = data.network_comment || "";

            b.disabled = false;
            b.innerHTML = '<i class="bi bi-search"></i> New Scan';
            
            if (cBtn) {
                cBtn.disabled = false;
                cBtn.classList.remove('hidden');
                cBtn.innerHTML = '<i class="bi bi-play-fill"></i> Continue Scan';
            }
            if (splitBtn) {
                splitBtn.disabled = false;
                splitBtn.classList.remove('hidden');
                splitBtn.innerHTML = '<i class="bi bi-diagram-2"></i> Split Scan';
            }
            if (isoBtn) {
                isoBtn.disabled = false;
                isoBtn.innerHTML = '<i class="bi bi-shield-lock"></i> Isolation Scan';
                isoBtn.classList.add('hidden'); 
            }
            
            if (btnSel) btnSel.classList.remove('hidden');
            if (btnOff) btnOff.classList.remove('hidden');
            if (editIcon) editIcon.classList.remove('hidden'); 
            
            // Inject offline devices at the bottom of the active list
            if (data.offline_devices && data.offline_devices.length > 0) {
                data.offline_devices.forEach(offDev => {
                    const idx = allDevices.findIndex(d => d.mac_address === offDev.mac_address);
                    if (idx >= 0) allDevices[idx] = offDev; 
                    else allDevices.push(offDev);
                });

                performSort('devices');
                filterDevices(); 
            }
            
            resolveMissingVendors(allDevices);
        }
        // Internal Python error
        else if (data.type === 'error') {
            eventSource.close();
            
            b.disabled = false;
            b.innerHTML = '<i class="bi bi-search"></i> New Scan';
            if (cBtn) { cBtn.disabled = false; cBtn.innerHTML = '<i class="bi bi-play-fill"></i> Continue Scan'; }
            if (splitBtn) { splitBtn.disabled = false; splitBtn.innerHTML = '<i class="bi bi-diagram-2"></i> Split Scan'; }
            if (isoBtn) { isoBtn.disabled = false; isoBtn.innerHTML = '<i class="bi bi-shield-lock"></i> Isolation Scan'; }
            
            alert(data.message || 'Scan failed.');
            if (allDevices.length === 0) {
                tb.innerHTML = `<tr><td colspan="10" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle"></i> ${data.message}</td></tr>`;
            }
        }
    };

    // Broken connection or timeout handler
    eventSource.onerror = function() {
        eventSource.close();
        
        b.disabled = false;
        b.innerHTML = '<i class="bi bi-search"></i> New Scan';
        if (cBtn) { cBtn.disabled = false; cBtn.innerHTML = '<i class="bi bi-play-fill"></i> Continue Scan'; }
        if (splitBtn) { splitBtn.disabled = false; splitBtn.innerHTML = '<i class="bi bi-diagram-2"></i> Split Scan'; }
        if (isoBtn) { isoBtn.disabled = false; isoBtn.innerHTML = '<i class="bi bi-shield-lock"></i> Isolation Scan'; }
        
        if (allDevices.length === 0) {
            tb.innerHTML = '<tr><td colspan="10" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle"></i> Connection to scanner lost.</td></tr>';
        } else {
            renderDevices(allDevices); 
        }
    };
}

/**
 * Renders the active network devices into the 'Network Scan' table with color-coded status badges.
 * @param {Array} d - Array of device objects to render.
 */
function renderDevices(d) {
    const tb = document.getElementById('device-list');
    if (!tb) return;

    tb.innerHTML = d.length ? d.map(x => {
        const ip = x.ip_address || "0.0.0.0";
        const services = String(x.services || "").toUpperCase();
        const hasWeb = services.includes("HTTP");
        
        let ipColumnHtml;
        if (!x.is_online) {
            ipColumnHtml = `<span class="text-muted">-</span>`;
        } else if (hasWeb) {
            ipColumnHtml = `<a href="${services.includes('HTTPS') ? 'https' : 'http'}://${ip}" target="_blank" class="fw-bold text-primary text-decoration-underline">${ip}</a>`;
        } else {
            ipColumnHtml = ip;
        }

        let serviceBadges = `<span class="badge bg-secondary opacity-50">None</span>`;
        if (x.is_online && services && services !== "NONE") {
            serviceBadges = services.split(',').map(s => {
                const trim = s.trim();
                return `<span class="badge ${trim.includes('HTTP') ? 'bg-primary' : 'bg-info text-dark'} me-1">${trim}</span>`;
            }).join('');
        }

        let historyHtml = `<span class="badge bg-secondary">Unknown</span>`;
        if (!x.is_online) {
            const lastTime = x.last_seen ? ` on ${x.last_seen}` : '';
            historyHtml = `<span class="badge bg-secondary">Last seen ${ip}${lastTime}</span>`;
        } else if (x.discovery_status === 'New Device') {
            historyHtml = `<span class="badge bg-success">New Device</span>`;
        } else if (x.previous_ip) {
            historyHtml = `<span class="badge bg-warning text-dark">IP changed previous ${x.previous_ip}</span>`;
        } else {
            historyHtml = `<span class="badge bg-info text-dark">Seen Before</span>`;
        }

        const idMac = x.mac_address.replace(/[:-]/g, '');
        const escapedMac = escapeJS(x.mac_address);
        
        let vendorDisplay = `<span id="vend-${idMac}" class="text-muted small fst-italic">Pending...</span>`;
        if (x.custom_vendor) {
            vendorDisplay = `<span class="badge bg-primary-subtle text-primary border border-primary-subtle">${escapeHTML(x.custom_vendor)}</span>`;
        } else if (x.vendor && x.vendor !== 'Unknown') {
            vendorDisplay = `<span id="vend-${idMac}">${escapeHTML(x.vendor)}</span>`;
        }

        const safeCustomV = escapeJS(x.custom_vendor);
        const safeLookupV = escapeJS(x.vendor); 
        const safeCustomN = escapeJS(x.custom_name);
        const safeComment = escapeJS(x.comments || '');
        const isProtected = x.is_protected ? 1 : 0;

        return `
            <tr class="${!x.is_online ? 'opacity-75' : ''}">
                <td onclick="event.stopPropagation()">
                    <input type="checkbox" class="device-check" value="${escapeHTML(x.mac_address)}" onchange="updateDeviceMasterCheck()">
                </td>
                <td><strong>${escapeHTML(x.hostname) || 'Unknown'}</strong></td>
                <td onclick="openDeviceConfig('${escapedMac}', '${safeCustomN}', '${safeCustomV || safeLookupV}', '${safeComment}')" style="cursor:pointer" title="Edit Device Details">
                    <strong>${escapeHTML(x.custom_name) || escapeHTML(x.hostname) || '<i class="text-muted">Set Name</i>'}</strong> <i class="bi bi-pencil ms-2 small text-muted"></i>
                </td>
                <td onclick="updateDeviceVendor('${escapedMac}', '${safeCustomV}', '${safeLookupV}')" style="cursor:pointer" title="Click to customize vendor">
                    ${vendorDisplay} <i class="bi bi-pencil small text-muted"></i>
                </td>
                <td>${ipColumnHtml}</td>
                <td class="font-monospace small text-muted">${x.mac_address}</td>
                <td class="${x.is_online ? 'text-success' : 'text-muted'}">${x.is_online ? 'Online' : 'Offline'}</td>
                <td>${serviceBadges}</td>
                <td>${historyHtml}</td>
                <td onclick="openDeviceConfig('${escapedMac}', '${safeCustomN}', '${safeCustomV || safeLookupV}', '${safeComment}')" style="cursor:pointer">
                    <small class="text-muted" style="font-size: 0.8rem;">${escapeHTML(x.comments || '')}</small>
                </td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('devices', '${escapedMac}', ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                </td>
            </tr>`;
    }).join('') : '<tr><td colspan="11" class="text-center p-4">No devices found.</td></tr>';
}

/**
 * Opens a browser prompt to manually assign a custom Vendor/Manufacturer to a device.
 * @param {string} mac - Device MAC address.
 * @param {string} currentCustom - Existing custom vendor data.
 * @param {string} currentLookup - Automatically resolved vendor data.
 */
function updateDeviceVendor(mac, currentCustom, currentLookup) {
    const promptMsg = currentLookup 
        ? `Enter Custom Vendor name (leave blank to revert to '${currentLookup}'):`
        : "Enter Custom Vendor name:";
    
    const nextVendor = prompt(promptMsg, currentCustom);
    if (nextVendor === null) return; // User cancelled

    fetch('/api/devices/update_vendor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mac: mac,
            vendor: nextVendor.trim(),
            network_id: currentNetworkId
        })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'success') {
            const target = allDevices.find(d => d.mac_address === mac);
            if (target) {
                target.custom_vendor = res.vendor;
                renderDevices(allDevices);
            }
        }
    });
}

/**
 * Asynchronously fetches manufacturer names for devices missing vendor data via an external MAC API.
 * @param {Array} devices - Array of active devices.
 */
function resolveMissingVendors(devices) {
    devices.forEach(dev => {
        if (dev.custom_vendor || (dev.vendor && dev.vendor !== 'Unknown')) return;

        const safeMac = dev.mac_address.replace(/[:-]/g, '');
        const el = document.getElementById(`vend-${safeMac}`);
        if (el) el.innerHTML = '<span class="spinner-border spinner-border-sm" style="width:0.6rem;height:0.6rem;"></span>';

        fetch('/api/vendor/lookup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mac: dev.mac_address })
        })
        .then(r => r.json())
        .then(res => {
                dev.vendor = res.vendor || 'Unknown';
                const targetEl = document.getElementById(`vend-${safeMac}`);
                if (targetEl && !dev.custom_vendor) {
                    targetEl.outerHTML = `<span id="vend-${safeMac}">${escapeHTML(dev.vendor)}</span>`;
                }
            })
        .catch(() => {
            const targetEl = document.getElementById(`vend-${safeMac}`);
            if (targetEl && !dev.custom_vendor) targetEl.innerText = 'Unknown';
        });
    });
}

/**
 * Filters the active Network Scan device list based on user search input.
 */
function filterDevices() { 
    const q = document.getElementById('device-filter').value.toLowerCase(); 
    renderDevices(allDevices.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)))); 
}

/**
 * Exports the currently active Network Scan device list as a CSV spreadsheet.
 */
function exportDevicesCSV() { 
    const rows = allDevices.map(d => {
        let historyText = d.discovery_status || "Unknown";
        if (d.previous_ip) historyText = `IP Changed (${d.previous_ip})`;
        
        return {
            "Hostname": d.hostname || "Unknown",
            "Custom Name": d.custom_name || "",
            "IP Address": d.ip_address || "0.0.0.0",
            "MAC Address": d.mac_address || "Unknown",
            "Status": d.is_online ? "Online" : "Offline",
            "Services (Ports)": convertServicesToPorts(d.services),
            "History": historyText,
            "Comments": d.comments || ""
        };
    });

    fetch('/api/devices/export', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({rows: rows})
    })
    .then(r => r.blob())
    .then(b => {
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u; 
        a.download = 'devices_list.csv'; 
        document.body.appendChild(a);
        a.click();
        a.remove();
    }); 
}
 
/**
 * Legacy prompt wrapper for quick-renaming a historical network profile.
 * @param {number|string} id - The Network ID.
 * @param {string} old - The previous network name.
 */
function renameNetwork(id, old) { 
    const n = prompt("Rename Network:", old); 
    if(n && n !== old) {
        fetch('/api/networks/rename', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id, name: n})
        }).then(loadNetworks); 
    }
}

/**
 * Permanently deletes a Network Scan profile and all its associated devices.
 * @param {number|string} id - The Network ID.
 */
function deleteNetwork(id) { 
    if(confirm("Delete network and all devices?")) {
        fetch('/api/networks/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id})
        }).then(loadNetworks); 
    }
}

/**
 * Pre-populates the modal for merging multiple network scans into a single profile.
 * Highlights Subnet and MAC address mismatches dynamically.
 */
function openMergeModal() {
    const selectedIds = Array.from(tableState.networks.selected);
    if (selectedIds.length < 2) return alert("Please select at least TWO networks to merge.");

    const selectedNets = tableState.networks.data.filter(n => selectedIds.includes(String(n.id)));
    
    let mismatchHtml = "";
    let subnets = new Set();
    let macs = new Set();
    
    selectedNets.forEach(n => {
        macs.add(n.gateway_mac);
        if (n.gateway_ip && n.gateway_ip !== '-' && n.gateway_ip !== 'Unknown') {
            let parts = n.gateway_ip.split('.');
            if (parts.length === 4) {
                subnets.add(`${parts[0]}.${parts[1]}.${parts[2]}.0/24`);
            }
        }
    });

    if (subnets.size > 1) {
        mismatchHtml += `<div class="alert alert-warning py-2 small shadow-sm"><i class="bi bi-exclamation-triangle-fill"></i> <strong>Subnet Mismatch!</strong> You are merging networks with different IP Subnets: <strong>${Array.from(subnets).join(', ')}</strong>.</div>`;
    }
    if (macs.size > 1) {
        mismatchHtml += `<div class="alert alert-warning py-2 small shadow-sm"><i class="bi bi-exclamation-triangle-fill"></i> <strong>Hardware Mismatch!</strong> The Gateway MAC addresses differ. Only the MAC of your target network will be preserved.</div>`;
    }

    document.getElementById('merge-warnings').innerHTML = mismatchHtml;

    const select = document.getElementById('merge-target-select');
    select.innerHTML = selectedNets.map(n => 
        `<option value="${n.id}">${escapeHTML(n.name)} (IP: ${n.gateway_ip} | MAC: ${n.gateway_mac})</option>`
    ).join('');

    mergeModal.show();
}

    // --- Tools (DNS/Ping/WiFi/History) ---
/**
 * Executes a DNS resolution request against the Python backend.
 * Disables the UI button to prevent spamming the lookup queue.
 */
function runDNS() {
    const d = document.getElementById('dns-input').value;
    if(!d) return;
    
    // 1. Grab the button and set it to a loading state
    const btn = document.getElementById('btn-dns');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Looking up...';

    // 2. Perform the fetch
    fetch('/api/dns/lookup', {
        method:'POST', 
        headers:{'Content-Type':'application/json'}, 
        body:JSON.stringify({domain:d})
    })
    .then(() => fetchToolLogs('dns'))
    .finally(() => {
        // 3. Always restore the button, even if the request fails
        btn.disabled = false; 
        btn.innerHTML = originalText;
    });
}

/**
 * Triggers a backend OS ICMP Ping sweep against a target IP or domain.
 * Updates the UI status label with real-time feedback.
 */
function runPing() {
    const t = document.getElementById('ping-input').value;
    if(!t) return;
    
    const btn = document.getElementById('btn-ping');
    btn.disabled = true; 
    btn.innerText = "Pinging...";
    document.getElementById('ping-status').innerText = "Sending packets...";
    
    fetch('/api/ping/run', {
        method:'POST', 
        headers:{'Content-Type':'application/json'}, 
        body:JSON.stringify({target:t})
    })
    .then(r=>r.json())
    .then(d => {
        btn.disabled = false; 
        btn.innerText = "Ping";
        document.getElementById('ping-status').innerText = `Done. Latency: ${d.latency}, Loss: ${d.loss}`;
        fetchToolLogs('ping');
    });
}

/**
 * Initiates a hardware-level Wi-Fi scan using the backend OS tools (netsh, nmcli, or CoreWLAN).
 * Handles UI loading states and dynamically appends the 'iface' and 'mode' query parameters.
 * @param {string} mode - 'new' for a fresh scan, 'continue' to append new data to the active list.
 */
function scanWifi(mode = 'new') { 
    const container = document.getElementById('wifi-list'); 
    const btn = document.getElementById('wifi-scan-btn');
    const contBtn = document.getElementById('wifi-continue-btn');
    const exportBtn = document.getElementById('btn-wifi-export');
    const commentBtn = document.getElementById('btn-wifi-comment');
    
    // Grab the selected adapter from the dropdown
    const adapterSelect = document.getElementById('wifi-adapter-select');
    let ifaceQuery = `?mode=${mode}`;
    if (adapterSelect && adapterSelect.value) {
        ifaceQuery += `&iface=${encodeURIComponent(adapterSelect.value)}`;
    }
    
    // Pass the active Scan ID if we are continuing a scan
    if (mode === 'continue' && window.currentWifiScanId) {
        ifaceQuery += `&scan_id=${window.currentWifiScanId}`;
    } else {
        window.currentWifiScanId = null; // Clear it on a fresh scan
    }
    
    // 1. Reset UI State
    if (mode === 'continue') {
        if(contBtn) { contBtn.disabled = true; contBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...'; }
        if(btn) btn.disabled = true;
    } else {
        if(btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...'; }
        if(contBtn) contBtn.disabled = true;
    }
    
    if(exportBtn) exportBtn.disabled = true;
    if(commentBtn) commentBtn.disabled = true;
    
    // Hide edit pencil and reset title/comment
    const titleEl = document.getElementById('wifi-tab-title');
    const commentEl = document.getElementById('wifi-tab-comment');
    const editIcon = document.getElementById('btn-edit-active-wifi');
    
    if (mode === 'new') {
        if (titleEl) titleEl.innerText = "Scan Wi-Fi";
        if (commentEl) commentEl.innerText = "";
        if (editIcon) editIcon.classList.add('hidden');
        if (contBtn) contBtn.classList.add('hidden');
        
        container.innerHTML = `
            <div class="col-12 text-center p-5">
                <div class="spinner-border text-info mb-3"></div>
                <h5 class="text-muted">Deep Scanning Airwaves...</h5>
                <p class="small text-secondary">Hardware reset initiated to detect all bands (2.4/5/6GHz).</p>
            </div>`;
    }

    // 2. Perform Request to Python Backend WITH targeted interface and mode
    fetch(`/api/wifi${ifaceQuery}`)
        .then(r => {
            if (!r.ok) throw new Error(`HTTP Error! Status: ${r.status}`);
            return r.json();
        })
        .then(data => {
            if (data.error) {
                container.innerHTML = `
                    <div class="col-12">
                        <div class="alert alert-warning shadow-sm p-4">
                            <h5 class="alert-heading"><i class="bi bi-exclamation-triangle"></i> ${data.error}</h5>
                            <p class="mb-0">${data.message}</p>
                        </div>
                    </div>`;
                return;
            }

            // Extract the network array and metadata from the backend response
            const nets = data.networks || data;

            if (!nets || nets.length === 0) {
                container.innerHTML = '<div class="col-12 text-center p-5 text-muted">No networks detected in range.</div>';
                return;
            }

            if(exportBtn) exportBtn.disabled = false;
            if(commentBtn) commentBtn.disabled = false;
            
            // Apply the Scan Name, Comment, and Reveal Edit/Continue Buttons
            if (data.scan_id) window.currentWifiScanId = data.scan_id;
            if (titleEl && data.scan_name) {
                titleEl.innerText = data.scan_name;
                if (commentEl) commentEl.innerText = data.scan_comment || "";
                if (editIcon) editIcon.classList.remove('hidden');
            }
            if (contBtn) contBtn.classList.remove('hidden');

            // 3. Hand the data off to the rendering function
            renderWifiResults(nets, false);
        })
        .catch(err => {
            console.error("Scan Failed:", err);
            container.innerHTML = `
                <div class="col-12">
                    <div class="alert alert-danger shadow-sm p-4">
                        <h5 class="alert-heading"><i class="bi bi-bug"></i> Critical Dashboard Error</h5>
                        <p class="mb-0">The application failed to communicate with the scanner.</p>
                        <hr>
                        <small class="d-block">Error Details: ${err.message}</small>
                        <button class="btn btn-sm btn-outline-danger mt-3" onclick="location.reload()">Reload Dashboard</button>
                    </div>
                </div>`;
        })
        .finally(() => {
            if(btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-search"></i> Scan Wi-Fi'; }
            if(contBtn) { contBtn.disabled = false; contBtn.innerHTML = '<i class="bi bi-play-fill"></i> Continue Scan'; }
        });
}

/**
 * Loads a specific historical Wi-Fi scan from the database into the Active Scan view for inspection.
 * @param {number|string} id - The ID of the Wi-Fi scan to load.
 */
function viewPastWifi(id) {
    fetch(`/api/wifi/history/${id}`).then(r => r.json()).then(d => {
        showPage('wifi', document.querySelector('[onclick*="showPage(\'wifi\'"]'));
        
        window.currentWifiScanId = id;
        const titleEl = document.getElementById('wifi-tab-title');
        const commentEl = document.getElementById('wifi-tab-comment');
        const editIcon = document.getElementById('btn-edit-active-wifi');
        const contBtn = document.getElementById('wifi-continue-btn');
        
        if (titleEl) {
            titleEl.innerText = d.name;
            if (commentEl) commentEl.innerText = d.comments || "";
            if (editIcon) editIcon.classList.remove('hidden');
        }
        
        if (contBtn) contBtn.classList.remove('hidden');
        
        renderWifiResults(d.results, true);
    });
}

/**
 * Transforms the highly-nested dynamic Wi-Fi scan JSON array into a flat CSV format and triggers a download.
 */
function exportActiveWifiCSV() {
    if (!currentScanResults.length) return;
    
    const rows = [];
    currentScanResults.forEach(net => {
        const comment = net.global_comment || "";
        if (net.raw_bssids && net.raw_bssids.length) {
            net.raw_bssids.forEach(b => {
                rows.push({
                    "SSID": net.ssid || "Unknown",
                    "MAC": b.mac || "-",
                    "Signal (dBm)": b.dbm || "-",
                    "Signal (%)": b.percent || "-",
                    "Channel": b.channel || "-",
                    "Band": b.band || "-",
                    "Authentication": net.auth || "-",
                    "Comments": comment
                });
            });
        } else {
            rows.push({
                "SSID": net.ssid || "Unknown",
                "MAC": net.mac || "-",
                "Signal (dBm)": net.signal || "-",
                "Signal (%)": "-",
                "Channel": net.channel || "-",
                "Band": net.band || "-",
                "Authentication": net.auth || "-",
                "Comments": comment
            });
        }
    });

    fetch('/api/devices/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: rows })
    })
    .then(r => r.blob())
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `wifi_scan_${new Date().getTime()}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    });
}

/**
 * Opens the configuration modal to edit a specific global Wi-Fi SSID's comment.
 * @param {string} ssid - The Wi-Fi Network Name.
 * @param {string} comment - Existing comment data.
 */
function openWifiSSIDConfig(ssid, comment) {
    document.getElementById('modal-ssid-id').value = ssid;
    document.getElementById('modal-ssid-display').value = ssid;
    document.getElementById('modal-ssid-comment').value = comment || "";
    wifiSSIDModal.show();
}

/**
 * Submits the edited Wi-Fi SSID comment to the backend database.
 * Instantly updates the local UI memory array so the comment displays without reloading.
 */
function saveWifiSSIDConfig() {
    const ssid = document.getElementById('modal-ssid-id').value;
    const comment = document.getElementById('modal-ssid-comment').value.trim();

    const btn = event.currentTarget || document.querySelector('#wifiSSIDModal .btn-primary');
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    btn.disabled = true;

    fetch('/api/wifi/update_ssid_comment', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: ssid, comment: comment })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'success') {
            wifiSSIDModal.hide();
            
            // Instantly update the local memory array with the new comment
            if (currentScanResults && currentScanResults.length > 0) {
                const targetNet = currentScanResults.find(n => n.ssid === ssid);
                if (targetNet) {
                    targetNet.global_comment = comment;
                }
            }
            
            // Refresh whichever page is visible
            if (!document.getElementById('wifi-networks-history').classList.contains('hidden')) {
                loadWifiNetworksHistory();
            }
            if (!document.getElementById('wifi').classList.contains('hidden') && currentScanResults.length) {
                renderWifiResults(currentScanResults, false);
            }
        } else {
            alert("Error: " + res.error);
        }
    }).finally(() => { 
        btn.innerHTML = origText; 
        btn.disabled = false; 
    });
}

/**
 * Permanently deletes an individual Wi-Fi scan profile from the database.
 * @param {string|number} id - Target scan ID.
 */
function deleteWifiScan(id) {
    if(confirm("Delete this scan record?")) {
        fetch('/api/wifi/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id })
        }).then(loadWifiHistory);
    }
}

/**
 * Wipes out all Wi-Fi scan history permanently (unless locked/protected).
 */
function clearAllWifiHistory() {
    if(confirm("Are you sure you want to permanently delete ALL Wi-Fi scan history?")) {
        fetch('/api/wifi/history/clear_all', { method: 'POST' })
            .then(() => loadWifiHistory());
    }
}

/**
 * Triggers the backend Speed Test execution.
 * Handles UI loading states and formats the returned Download, Upload, and Ping values.
 */
function runSpeedTest() { 
    const btn = document.getElementById('btn-speedtest');
    const resultDiv = document.getElementById('speedtest-result'); 
    btn.disabled = true; 
    resultDiv.innerHTML = `<div class="spinner-border spinner-border-sm text-primary"></div> <span class="ms-2">Testing...</span>`;
    
    fetch('/api/speedtest', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({
            network_name: document.getElementById('st-network-name').value,
            connection_type: document.getElementById('st-conn-type').value
        })
    }).then(r => r.json()).then(d => {
        btn.disabled = false;
        if (d.error) { resultDiv.innerHTML = `<span class="text-danger">${d.error}</span>`; } 
        else {
            resultDiv.innerHTML = `<span class="text-primary fw-bold">↓ ${d.download}</span> | <span class="text-success fw-bold">↑ ${d.upload}</span> <span class="ms-2 text-muted small">(${d.ping})</span>`;
            if (typeof fetchHistory === "function") fetchHistory();
        }
    }).catch(err => { btn.disabled = false; resultDiv.innerHTML = "Error."; }); 
}

/**
 * Loads the Speed Test History table from the backend SQLite database.
 */
function fetchHistory() { 
    fetch('/api/history').then(r => r.json()).then(d => { 
        tableState.history.data = d; 
        tableState.history.filtered = d;
        tableState.history.page = 1;
        tableState.history.selected.clear();
        performSort('history');
        renderHistory(); 
    });
}

/**
 * Filters the Speed Test History table based on search input.
 */
function filterHistory() { 
    const q = document.getElementById('history-filter').value.toLowerCase(); 
    tableState.history.filtered = tableState.history.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q))); 
    tableState.history.page = 1;
    performSort('history');
    renderHistory();
}

/**
 * Renders the HTML table for the Speed Test History page, handling pagination and protection locks.
 */
function renderHistory() { 
    const tb = document.getElementById('history-table');
    if (!tb) return;
    const paginatedData = getPaginatedData('history');

    tb.innerHTML = paginatedData.length ? paginatedData.map(x => {
        const safeName = escapeJS(x.network_name);
        const safeType = escapeJS(x.connection_type);
        const isChecked = tableState.history.selected.has(String(x.id)) ? 'checked' : '';
        
        return `
        <tr>
            <td onclick="event.stopPropagation()"><input type="checkbox" class="history-check" value="${x.id}" onchange="toggleSelection('history', this)" ${isChecked}></td>
            <td><small>${x.timestamp}</small></td>
            <td><strong>${escapeHTML(x.network_name)}</strong></td>
            <td>${x.isp || '-'}</td>
            <td><span class="badge bg-light text-dark border">${x.connection_type}</span></td>
            <td class="text-primary fw-bold">${x.download}</td>
            <td class="text-success fw-bold">${x.upload}</td>
            <td>${x.ping}</td>
            <td class="text-muted small">${x.device_ip || '-'}</td> 
            <td class="text-muted small">${x.wan_ip || '-'}</td>
            <td class="text-end text-nowrap">
                <div class="btn-group">
                    <button class="btn btn-sm ${x.is_protected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('history', ${x.id}, ${x.is_protected})" title="${x.is_protected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${x.is_protected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="openEditSpeedTestModal(${x.id}, '${safeName}', '${safeType}')" title="Edit"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteSingleSpeedTest(${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="11" class="text-center p-4">No history.</td></tr>';

    updateMasterCheckbox('history', paginatedData.map(x => x.id));
}

/**
 * Initiates the CSV export for the Speed Test History.
 * Automatically exports selected rows, or all visible filtered rows if none are explicitly selected.
 */
function exportHistoryCSV() { 
    let dataToExport = [];
    if (tableState.history.selected.size > 0) {
        const selectedIds = Array.from(tableState.history.selected);
        dataToExport = tableState.history.data.filter(d => selectedIds.includes(String(d.id)));
    } else {
        dataToExport = tableState.history.filtered;
        if (!dataToExport.length) return alert("No history records available to export.");
        if (!confirm(`No specific items selected. Export all ${dataToExport.length} visible record(s)?`)) return;
    }

    fetch('/api/history/export', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({rows: dataToExport})
    })
    .then(r => r.blob())
    .then(b => { 
        const u = URL.createObjectURL(b);
        const a = document.createElement('a'); 
        a.href = u; 
        a.download = `speedtest_history_${new Date().getTime()}.csv`; 
        document.body.appendChild(a);
        a.click(); 
        a.remove();
    }); 
}

/**
 * Opens the configuration modal to edit a specific Speed Test record.
 */
function openEditSpeedTestModal(id, name, type) {
    document.getElementById('modal-st-id').value = id;
    document.getElementById('modal-st-name').value = name;
    
    const typeSelect = document.getElementById('modal-st-type');
    typeSelect.value = type;
    
    if(typeSelect.selectedIndex === -1) {
        const opt = document.createElement('option');
        opt.value = type; opt.text = type; opt.selected = true;
        typeSelect.add(opt);
    }
    
    editSpeedTestModal.show();
}

/**
 * Saves changes to a Speed Test record (Network Name or Connection Type).
 */
function saveSpeedTestEdit() {
    const id = document.getElementById('modal-st-id').value;
    const n = document.getElementById('modal-st-name').value;
    const t = document.getElementById('modal-st-type').value;

    fetch('/api/history/update', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({id: id, name: n, type: t})
    }).then(() => {
        editSpeedTestModal.hide();
        fetchHistory(); 
    }); 
}

/**
 * Deletes a single Speed Test record from the database.
 */
function deleteSingleSpeedTest(id) {
    if(confirm("Delete this speed test record?")) {
        fetch('/api/bulk_delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'history', ids: [id] })
        }).then(fetchHistory);
    }
}

/** Legacy helper: Allows direct renaming via browser prompt */
function editHistory(id, oldName, oldType) { 
    const n = prompt("Rename Network:", oldName); 
    if (n === null) return; 
    
    const t = prompt("Connection Type (e.g., Wi-Fi or Ethernet):", oldType);
    if (t === null) return;

    fetch('/api/history/update', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({id: id, name: n, type: t})
    }).then(fetchHistory); 
}


    // --- Updater Logic (Global Version) ---
/**
 * Contacts the GitHub repository to compare the local application version against the remote version.
 * Displays an alert badge on the footer if an update or channel mismatch is detected.
 */
function checkUpdates() {   
    fetch('/api/update/check').then(r => r.json()).then(d => {
        if (d.status === 'success') {
            pendingRemoteVersion = d.remote_version;
            
            const local = window.APP_CONFIG.globalVersion || "0.0.0";
            const remote = d.remote_version || "0.0.0";
            const channel = window.APP_CONFIG.updateChannel || "stable";
            
            const isLocalDev = local.includes('DEV');
            
            // Determine if we crossed channels (e.g. from Stable to Dev)
            let crossChannelUpdate = false;
            if (channel === 'dev' && !isLocalDev) crossChannelUpdate = true;
            if (channel === 'stable' && isLocalDev) crossChannelUpdate = true;
            
            const isHigher = isVersionHigher(remote, local);
            
            if (d.update_available || isHigher || crossChannelUpdate) {
                const t = document.getElementById('ver-global');
                if (t) {
                    t.classList.add('ver-update');
                    t.innerHTML = `Running V${local.replace('DEV', '')} <i class="bi bi-exclamation-circle-fill"></i>`;
                }
            }
        }
    });
}

/**
 * SemVer (Semantic Versioning) comparison helper.
 * @param {string} v1 - Remote version.
 * @param {string} v2 - Local version.
 * @returns {boolean} True if remote is strictly greater than local.
 */
function isVersionHigher(v1, v2) {
    const cleanV1 = String(v1).replace('DEV', '');
    const cleanV2 = String(v2).replace('DEV', '');
    const p1 = cleanV1.split('.').map(Number), p2 = cleanV2.split('.').map(Number);
    for (let i = 0; i < Math.max(p1.length, p2.length); i++) {
        if ((p1[i]||0) > (p2[i]||0)) return true;
        if ((p1[i]||0) < (p2[i]||0)) return false;
    }
    return false;
}

/**
 * Opens the System Update modal, fetches dynamic release notes from the GitHub Changelog, 
 * and configures the UI based on whether a channel switch or standard update is required.
 * Includes a safeguard to prevent updating if the target branch does not exist.
 */
function openUpdateModal() {
    if (!updateModal) {
        const modalEl = document.getElementById('updateModal');
        if (modalEl) updateModal = new bootstrap.Modal(modalEl);
        else return console.error("Update modal HTML is missing.");
    }

    updateModal.show();
    
    const msg = document.getElementById('update-msg');
    const btn = document.getElementById('btn-apply');
    const cl = document.getElementById('changelog-text');
    
    msg.innerText = "Checking for updates...";
    msg.className = "alert alert-info";
    cl.innerText = "Fetching release notes...";
    btn.disabled = true;

    fetch('/api/update/check')
        .then(r => r.json())
        .then(d => {
            const local = window.APP_CONFIG.globalVersion || "0.0.0";
            const remote = d.remote_version || "0.0.0";
            const channel = window.APP_CONFIG.updateChannel || "stable";
            
            const isLocalDev = local.includes('DEV');
            
            let crossChannelUpdate = false;
            if (channel === 'dev' && !isLocalDev) crossChannelUpdate = true;
            if (channel === 'stable' && isLocalDev) crossChannelUpdate = true;
            
            const isHigher = isVersionHigher(remote, local);
            const updateFound = d.update_available || isHigher || crossChannelUpdate;
            
            // BUGFIX: If remote is 0.0.0, the branch does not exist on GitHub (404 Error).
            if (remote === "0.0.0") {
                msg.className = "alert alert-danger"; 
                msg.innerText = `Update Error: The '${channel}' branch could not be found on your GitHub repository.`; 
                btn.disabled = true;
            } 
            else if (updateFound) {
                pendingRemoteVersion = remote; 
                msg.className = "alert alert-warning";
                
                let statusText = "Update Available";
                let reasonText = `v${remote.replace('DEV', '')} (Current: v${local.replace('DEV', '')})`;
                
                if (crossChannelUpdate) {
                    statusText = "Channel Switch Required";
                    reasonText = channel === 'dev' ? "Installing Development Build..." : "Restoring Production Build...";
                } else if (d.update_available && !isHigher) {
                    statusText = "Update Required";
                    reasonText = `File mismatch detected (Remote v${remote.replace('DEV', '')})`;
                }
                
                msg.innerText = `${statusText}: ${reasonText}`;
                btn.disabled = false;
            } else {
                msg.className = "alert alert-success"; 
                msg.innerText = "Your system and all core files are up to date."; 
                btn.disabled = true;
            }
            return fetch('/api/update/changelog');
        })
        .then(r => r.json())
        .then(d => { 
            cl.innerText = d.changelog || "No release notes available for this version."; 
        })
        .catch(err => {
            msg.className = "alert alert-danger";
            msg.innerText = "Error communicating with update server.";
            cl.innerText = "Check your internet connection.";
        });
}

/**
 * Triggers the backend Python engine to download the GitHub zipball, extract it, and restart the server.
 * Displays backend errors safely if the download or extraction fails.
 */
function applyUpdate() { 
    if(!confirm("Update system? The server will restart.")) return;
    
    localStorage.setItem('update_pending', 'true');

    const btn = document.getElementById('btn-apply');
    btn.disabled = true; 
    btn.innerText = "Installing...";

    fetch('/api/update/apply', {method: 'POST'})
        .then(r => r.json())
        .then(d => { 
            // BUGFIX: Check for the 'error' key specifically to prevent "undefined" alerts
            if (d.error) {
                alert("Update Failed: " + d.error);
                localStorage.removeItem('update_pending');
                btn.disabled = false;
                btn.innerText = "Install Update";
            } else {
                alert(d.message || "Update applied successfully."); 
                setTimeout(() => location.reload(), 5000); 
            }
        })
        .catch(err => {
            alert("Connection lost. Server is likely restarting. Page will reload.");
            setTimeout(() => location.reload(), 5000); 
        }); 
}

/**
 * Runs automatically on page load if an update was just performed.
 * Validates that all files were successfully overwritten by the OS and triggers a warning modal if files were locked.
 */
function verifyUpdateStatus() {
    if (localStorage.getItem('update_pending')) {
        localStorage.removeItem('update_pending'); 
        
        console.log("Verifying update integrity...");
        
        fetch('/api/update/check')
            .then(r => r.json())
            .then(d => {
                if (d.mismatches && d.mismatches.length > 0) {
                    const listEl = document.getElementById('failed-list');
                    if(listEl) {
                        listEl.innerHTML = d.mismatches.map(m => 
                            `<li><strong>${m.file}</strong>: Local v${m.local} <span class="text-danger">(Remote v${m.remote})</span></li>`
                        ).join('');
                    }
                    new bootstrap.Modal(document.getElementById('updateFailedModal')).show();
                } else {
                    alert("Update verified! All core files are successfully updated.");
                }
            });
    }
}

/**
 * Renders the results of an active Wi-Fi scan into grouped, interactive UI cards.
 * Splits networks by SSID, categorizes their BSSIDs by frequency band (2.4/5/6GHz), 
 * and handles expanding global comment blocks.
 * @param {Array} data - The array of Wi-Fi network objects.
 * @param {boolean} isHistory - Whether this data is being loaded from the database or an active scan.
 */
function renderWifiResults(data, isHistory = false) {
    const container = document.getElementById('wifi-list');
    currentScanResults = data;
    
    let html = '';

    html += data.map(n => {
        let detailsHtml = '';
        
        if (n.raw_bssids && n.raw_bssids.length > 0) {
            const bands = {};
            n.raw_bssids.forEach(b => {
                const band = b.band || 'Unknown';
                if (!bands[band]) bands[band] = [];
                bands[band].push(b);
            });

            // Sort bands visually: 2.4GHz -> 5GHz -> 6GHz
            const sortedBands = Object.keys(bands).sort((a, b) => {
                const wA = a.includes('2.4') ? 1 : a.includes('5') ? 2 : a.includes('6') ? 3 : 4;
                const wB = b.includes('2.4') ? 1 : b.includes('5') ? 2 : b.includes('6') ? 3 : 4;
                return wA - wB;
            });

            sortedBands.forEach((band, idx) => {
                const bandClean = escapeHTML(band.replace('GHz', 'Ghz'));
                const mtClass = idx === 0 ? '' : 'mt-3 ';
                
                detailsHtml += `<div class="${mtClass}fw-bold text-info mb-1" style="font-size: 1.15rem;">${bandClean}</div>`;
                detailsHtml += `
                <div class="table-responsive overflow-hidden">
                    <table class="table table-sm table-borderless align-middle w-100 mb-1" style="table-layout: fixed; font-size: 0.95rem;">
                        <thead>
                            <tr class="text-muted border-bottom border-secondary-subtle" style="font-size: 0.8rem;">
                                <th class="p-1 px-0" style="width: 38%;">BSSID (MAC)</th>
                                <th class="p-1 text-center" style="width: 15%;">Channel</th>
                                <th class="p-1 text-center text-truncate" style="width: 25%;">Security</th>
                                <th class="p-1 text-end px-0" style="width: 22%;">Signal Strength</th>
                            </tr>
                        </thead>
                        <tbody class="font-monospace">
                `;

                bands[band].sort((a, b) => {
                    const valA = (a.percent !== '' && a.percent !== null) ? a.percent : -100;
                    const valB = (b.percent !== '' && b.percent !== null) ? b.percent : -100;
                    return valB - valA;
                });

                bands[band].forEach(b => {
                    const mac = b.mac || 'Unknown';
                    const ch = (b.channel && b.channel !== '0') ? b.channel : '-';
                    const auth = n.auth || 'Unknown';
                    
                    let pctHtml = '<span class="fw-bold text-muted">-%</span>';
                    if (b.percent !== '' && b.percent !== null) {
                        let color = 'text-danger';
                        if (b.percent >= 75) color = 'text-success';
                        else if (b.percent >= 40) color = 'text-warning';
                        pctHtml = `<span class="fw-bold ${color}">${escapeHTML(String(b.percent))}%</span>`;
                    }

                    detailsHtml += `
                        <tr>
                            <td class="p-1 px-0">${escapeHTML(mac)}</td>
                            <td class="p-1 text-center">${escapeHTML(ch)}</td>
                            <td class="p-1 text-center text-truncate" title="${escapeHTML(auth)}">${escapeHTML(auth)}</td>
                            <td class="p-1 text-end px-0">${pctHtml}</td>
                        </tr>
                    `;
                });
                
                detailsHtml += `</tbody></table></div>`;
            });
        } else if (n.details) {
            detailsHtml = n.details;
        } else {
            detailsHtml = `<span class="text-muted">${escapeHTML(n.band || '')}</span>`;
        }

        const chDisplay = n.channel ? (n.channel.startsWith('Ch:') ? n.channel : 'Ch: ' + n.channel) : 'Ch: -';
        
        // Expanding Comment Support
        const globalComment = n.global_comment || '';
        const safeComment = escapeJS(globalComment);
        const isMultiLine = globalComment.includes('\n') || globalComment.length > 60;
        
        let commentHtml = '';
        if (globalComment) {
            const uid = Math.random().toString(36).substr(2, 9);
            commentHtml = `
            <div class="mt-2 d-flex align-items-start border-top pt-2 border-secondary-subtle">
                <div id="comment-${uid}" class="text-muted small text-break mb-0" style="max-height: 1.5em; overflow: hidden; transition: max-height 0.3s; flex-grow: 1;">
                    ${escapeHTML(globalComment).replace(/\n/g, '<br>')}
                </div>
                ${isMultiLine ? `<i class="bi bi-chevron-down ms-2 text-secondary p-1" style="cursor: pointer; font-size: 0.9rem;" onclick="const b = document.getElementById('comment-${uid}'); if(b.style.maxHeight === '1.5em' || b.style.maxHeight === ''){b.style.maxHeight='none'; this.classList.replace('bi-chevron-down', 'bi-chevron-up');}else{b.style.maxHeight='1.5em'; this.classList.replace('bi-chevron-up', 'bi-chevron-down');}"></i>` : ''}
            </div>`;
        }

        return `
        <div class="col-lg-6 mb-3">
            <div class="card shadow-sm h-100 border-0 bg-body-tertiary">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-1 gap-2">
                        <strong class="text-truncate fs-5" title="${escapeHTML(n.ssid)}" style="max-width: 65%;">
                            ${escapeHTML(n.ssid)}
                            <i class="bi bi-pencil ms-2 text-muted" style="font-size: 0.9rem; cursor:pointer;" onclick="openWifiSSIDConfig('${escapeJS(n.ssid)}', '${safeComment}')" title="Edit SSID Comment"></i>
                        </strong>
                        <span class="badge bg-primary text-nowrap text-truncate text-end" title="${escapeHTML(chDisplay)}" style="max-width: 35%; font-size: 0.85rem;">${escapeHTML(chDisplay)}</span>
                    </div>
                    ${commentHtml}
                    <div class="border-top pt-2 mt-2 border-secondary-subtle w-100">
                        ${detailsHtml}
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
    
    container.innerHTML = html;
}

/**
 * Fetches the historical scan records specifically for a chosen SSID.
 * Populates and displays the details inside the 'Wi-Fi Net Modal'.
 */
function viewWifiNetworkDetails(ssid, comment) {
    let modalTitleHtml = `
        <div class="d-flex justify-content-between align-items-start">
            <div>
                <span class="fw-bold">History for: ${escapeHTML(ssid)}</span>
                <i class="bi bi-pencil ms-2 text-muted" style="font-size: 0.9rem; cursor:pointer;" onclick="openWifiSSIDConfig('${escapeJS(ssid)}', '${escapeJS(comment)}')" title="Edit SSID Comment"></i>
            </div>
        </div>`;
        
    if(comment && comment !== 'undefined') {
         modalTitleHtml += `<div class="mt-2 p-2 bg-body-tertiary rounded small border fw-normal text-muted">${escapeHTML(comment).replace(/\n/g, '<br>')}</div>`;
    }

    document.getElementById('wifiNetModalTitle').innerHTML = modalTitleHtml;
    const tbody = document.getElementById('wifiNetModalBody');
    const exportBtn = document.getElementById('btn-export-wifiNet-modal');
    
    tbody.innerHTML = '<tr><td colspan="6" class="text-center p-3"><div class="spinner-border spinner-border-sm text-primary"></div></td></tr>';
    exportBtn.style.display = 'none';
    
    let modal = bootstrap.Modal.getInstance(document.getElementById('wifiNetModal'));
    if (!modal) modal = new bootstrap.Modal(document.getElementById('wifiNetModal'));
    modal.show();

    fetch('/api/wifi_networks_history/details', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ssid: ssid})
    })
    .then(r => r.json())
    .then(data => {
        if(data.error) throw new Error(data.error);
        currentModalWifiNetData = data;
        exportBtn.style.display = 'block';
        exportBtn.onclick = () => exportSingleWifiNetworkHistory(ssid);

        performSort('wifiNetModalTable');
        renderWifiNetModalTable();
    })
    .catch(err => {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center p-3 text-danger">Failed to load details.</td></tr>`;
    });
}

/** Points directly to the backend CSV endpoint for a specific scan ID. */
function exportWifiCSV(id) {
    window.location.href = `/api/wifi/export/${id}`;
}

// --- BULK ACTION HELPERS ---
/**
 * Toggles all checkboxes of a specific class based on a master checkbox state.
 * @param {HTMLInputElement} source - The master checkbox element.
 * @param {string} className - The CSS class of the target checkboxes.
 */
function toggleSelectAll(source, className) {
    document.querySelectorAll(`.${className}`).forEach(cb => cb.checked = source.checked);
}

/**
 * Retrieves an array of values from all currently checked checkboxes of a specific class.
 * @param {string} className - The CSS class of the target checkboxes.
 * @returns {Array<string>} An array of selected values.
 */
function getSelectedIds(className) {
    return Array.from(document.querySelectorAll(`.${className}:checked`)).map(cb => cb.value);
}

let currentModalWifiNetData = [];

/** Renders the sub-table inside the 'Wi-Fi Network Details' modal showing specific historical scans. */
function renderWifiNetModalTable() {
    const tbody = document.getElementById('wifiNetModalBody');
    if (!currentModalWifiNetData) return;
    
    tbody.innerHTML = currentModalWifiNetData.length ? currentModalWifiNetData.map(x => {
        let sig = "";
        if (x.dbm && x.percent) sig = `${x.dbm} dBm (${x.percent}%)`;
        else if (x.dbm) sig = `${x.dbm} dBm`;
        else if (x.percent) sig = `${x.percent}%`;
        else sig = "-";
        
        return `
        <tr>
            <td><small class="text-muted">${x.timestamp}</small></td>
            <td>${escapeHTML(x.scan_name)}</td>
            <td class="font-monospace">${x.mac}</td>
            <td>${x.band} (Ch ${x.channel})</td>
            <td>${sig}</td>
            <td>${x.auth}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" class="text-center p-3">No data available.</td></tr>';
}

/** Triggers a CSV export containing the historical scan logs for a specific Wi-Fi SSID. */
function exportSingleWifiNetworkHistory(ssid) {
    if (!currentModalWifiNetData.length) return;
    const rows = currentModalWifiNetData.map(x => ({
        "SSID": ssid,
        "Scan Date": x.timestamp,
        "Scan Name": x.scan_name,
        "MAC Address": x.mac,
        "Band": x.band,
        "Channel": x.channel,
        "Signal (dBm)": x.dbm,
        "Signal (%)": x.percent,
        "Authentication": x.auth
    }));
    
    fetch('/api/devices/export', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rows: rows})
    }).then(r=>r.blob()).then(b=>{
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u;
        a.download = `wifi_history_${ssid.replace(/\s+/g, '_')}.csv`;
        a.click();
    });
}

/**
 * Fetches the available update channels (e.g., Stable, Dev) from the backend 
 * and populates the Settings dropdown menu.
 */
function loadUpdateChannels() {
    fetch('/api/settings/channels')
        .then(r => r.json())
        .then(data => {
            const select = document.getElementById('update-channel-select');
            if (!select) return;
            
            select.innerHTML = '';
            data.channels.forEach(ch => {
                const opt = document.createElement('option');
                opt.value = ch.id;
                opt.textContent = ch.name;
                select.appendChild(opt);
            });
            
            // Set the active channel once populated
            if (window.APP_CONFIG.updateChannel) {
                select.value = window.APP_CONFIG.updateChannel;
            }
        })
        .catch(err => console.error("Error loading update channels:", err));
}

/**
 * Posts a request to change the application's update repository.
 * Prompts the user with a warning about backward compatibility before executing.
 */
function changeUpdateChannel() {
    const select = document.getElementById('update-channel-select');
    const newChannel = select.value;
    const newChannelName = select.options[select.selectedIndex].text;
    const oldChannel = window.APP_CONFIG.updateChannel || 'stable';

    // 1. Display Dynamic Warnings
    if (newChannel !== oldChannel) {
        const warning = `Are you sure you want to switch the update channel to ${newChannelName}?\n\nThe database or configurations may be different and not backwards compatible.`;
        if (!confirm(warning)) {
            select.value = oldChannel; // Revert the dropdown if cancelled
            return;
        }
    } else {
        return; // No change made
    }

    // 2. Proceed with the API call if confirmed
    select.disabled = true;
    
    fetch('/api/settings/channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel: newChannel })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === "success") {
            window.APP_CONFIG.updateChannel = newChannel;
            
            // Instantly update the Footer Badge
            const badge = document.getElementById('footer-channel-badge');
            if (badge) {
                badge.innerText = newChannelName;
                if (newChannel.toLowerCase().includes('dev')) {
                    badge.className = 'badge bg-warning text-dark me-2';
                } else {
                    badge.className = 'badge bg-success me-2';
                }
            }
            
            // Automatically re-check for updates against the new repository
            checkUpdates();
            alert(`Update channel switched to: ${newChannelName}.`);
        }
    })
    .catch(err => {
        alert("Error changing channel: " + err.message);
        select.value = oldChannel; // Revert the dropdown on error
    })
    .finally(() => {
        select.disabled = false;
    });
}

let hardwareWorkerDefaults = null;

/**
 * Loads the current ThreadPool concurrency settings and the host hardware identity.
 * Populates the 'Workers' settings card.
 */
function loadWorkerSettings() {
    fetch('/api/settings/workers')
        .then(r => r.json())
        .then(data => {
            const badge = document.getElementById('detected-hw-badge');
            if (badge) badge.innerText = data.hardware;
            
            hardwareWorkerDefaults = data.defaults;
            
            const srv = document.getElementById('workers-server');
            const scn = document.getElementById('workers-scan');
            const png = document.getElementById('workers-ping');
            
            if (srv) srv.value = data.config.server_threads;
            if (scn) scn.value = data.config.scan_workers;
            if (png) png.value = data.config.ping_workers;
        })
        .catch(err => console.error("Failed to load worker settings:", err));
}

/**
 * Submits new thread concurrency settings to the backend.
 * Warns the user that the server must restart immediately to apply changes.
 */
function saveWorkerSettings() {
    const payload = {
        server_threads: parseInt(document.getElementById('workers-server').value),
        scan_workers: parseInt(document.getElementById('workers-scan').value),
        ping_workers: parseInt(document.getElementById('workers-ping').value)
    };
    
    if (!confirm("Save worker settings? The server will restart instantly to apply changes.")) return;

    // Change button text to show loading
    const btn = event.currentTarget || document.querySelector('button[onclick="saveWorkerSettings()"]');
    const originalText = btn ? btn.innerHTML : "Save Worker Settings";
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Restarting...';
    }

    fetch('/api/settings/workers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === "success") {
            alert("Worker settings saved. The system is restarting and will reload in 3 seconds.");
            setTimeout(() => window.location.reload(), 3000);
        } else {
            alert("Error saving workers: " + res.message);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    })
    .catch(err => {
        // If the server goes down faster than the fetch finishes, catch the network error and reload anyway
        setTimeout(() => window.location.reload(), 3000);
    });
}

/** Resets the thread concurrency pools back to their hardware defaults. */
function resetWorkerSettings() {
    if (!hardwareWorkerDefaults) return;
    document.getElementById('workers-server').value = hardwareWorkerDefaults.server_threads;
    document.getElementById('workers-scan').value = hardwareWorkerDefaults.scan_workers;
    document.getElementById('workers-ping').value = hardwareWorkerDefaults.ping_workers;
    saveWorkerSettings();
}

/**
 * Loads and displays unread emergency system alerts (e.g., disk space, crash loops) as banners at the top of the UI.
 */
function loadSystemAlerts() {
    fetch('/api/system/alerts').then(r=>r.json()).then(alerts => {
        const container = document.getElementById('system-alerts-container');
        if(!container) return;
        
        if(alerts.length > 0) {
            container.innerHTML = alerts.map(msg => `
                <div class="alert alert-danger alert-dismissible fade show shadow-sm" role="alert">
                    <i class="bi bi-exclamation-octagon-fill me-2"></i>
                    <strong>System Notice:</strong> ${escapeHTML(msg)}
                    <button type="button" class="btn-close" onclick="dismissSystemAlert('${escapeJS(msg)}', this)"></button>
                </div>
            `).join('');
        }
    });
}

/**
 * Toggles the "Padlock" protection flag on a specific database record, preventing it from being accidentally deleted during automated cleanup sweeps.
 * @param {string} type - Table category (e.g., 'history', 'devices').
 * @param {string|number} id - Record identifier.
 * @param {number} currentState - 1 (locked) or 0 (unlocked).
 */
function toggleProtection(type, id, currentState) {
    const newState = currentState ? 0 : 1; 
    fetch('/api/system/toggle_protection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({type: type, id: id, state: newState})
    }).then(() => {
        if(type === 'history') fetchHistory();
        if(type === 'wifi') loadWifiHistory();
        if(type === 'dns') fetchToolLogs('dns');
        if(type === 'ping') fetchToolLogs('ping');
        if(type === 'networks') loadNetworks();
        if(type === 'wifi_ssid') loadWifiNetworksHistory(); 
        if(type === 'devices') {
            if (!document.getElementById('devices').classList.contains('hidden') && currentNetworkId) {
                loadNetworkDevices(currentNetworkId, "");
            }
            if (!document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
        }
    });
}

/**
 * Triggers the backend Python engine to safely clone the active SQLite database into a timestamped `.back` file.
 */
function createLocalBackup() {
    const btn = event.currentTarget;
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Creating...';
    btn.disabled = true;
    
    fetch('/api/system/backups/create', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.status === 'success') {
                alert(d.message);
                loadBackupInfo(); 
            } else {
                alert("Error: " + d.error);
            }
        })
        .finally(() => { 
            btn.innerHTML = origHtml; 
            btn.disabled = false; 
        });
}

/**
 * Clears the physical `.back` backup files generated on the server storage.
 * @param {string} mode - 'all' or 'keep_latest'.
 */
function deleteLocalBackups(mode) {
    let msg = mode === 'all' 
        ? "Are you sure you want to delete ALL local backups?" 
        : "Are you sure you want to delete all local backups EXCEPT the latest good one?";
        
    if(!confirm(msg)) return;
    
    fetch('/api/system/backups/delete', {
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            alert(d.message);
            loadBackupInfo(); 
        } else {
            alert("Error: " + (d.error || d.message));
        }
    });
}

/**
 * Handles toggling the internal Python Waitress logging mechanism.
 * Maintains mutual exclusivity between "Full Logging" and "Disable All Logs".
 */
function toggleLogging(triggeredBy) {
    const toggleFull = document.getElementById('logging-toggle');
    const toggleDisable = document.getElementById('disable-logging-toggle');
    
    let isFullEnabled = toggleFull.checked;
    let isDisableEnabled = toggleDisable.checked;
    
    // Mutual Exclusivity Logic
    if (triggeredBy === 'full' && isFullEnabled && isDisableEnabled) {
        if (confirm("Enabling Full Diagnostic Logging will turn off 'Disable All Logs'. Do you want to continue?")) {
            isDisableEnabled = false;
            toggleDisable.checked = false;
        } else {
            toggleFull.checked = false; // Revert the switch
            return;
        }
    } else if (triggeredBy === 'disable' && isDisableEnabled && isFullEnabled) {
        if (confirm("Enabling 'Disable All Logs' will completely turn off Full Diagnostic Logging. Do you want to continue?")) {
            isFullEnabled = false;
            toggleFull.checked = false;
        } else {
            toggleDisable.checked = false; // Revert the switch
            return;
        }
    }
    
    fetch('/api/settings/logging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            full_logging: isFullEnabled, 
            disable_all_logs: isDisableEnabled 
        })
    })
    .then(res => res.json())
    .then(data => {
        if(data.status !== "success") {
            alert("Failed to update logging settings: " + (data.message || "Unknown error"));
            toggleFull.checked = !isFullEnabled;
            toggleDisable.checked = !isDisableEnabled;
        }
    })
    .catch(err => { 
        alert("Communication error saving log settings.");
        toggleFull.checked = !isFullEnabled;
        toggleDisable.checked = !isDisableEnabled;
    });
}

/**
 * Safely forces the Python backend to wipe out all `.log` files from the disk, 
 * bypassing OS lock constraints.
 */
function deleteSystemLogs() {
    if (!confirm("Are you sure you want to delete all system diagnostic logs?")) return;
    
    const btn = event.currentTarget;
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Clearing...';
    btn.disabled = true;

    fetch('/api/system/logs/delete', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.status === "success") {
                alert("System logs have been successfully cleared.");
                const sizeBadge = document.getElementById('log-size-badge');
                if (sizeBadge) sizeBadge.innerText = '0.00 MB';
            } else {
                alert("Error: " + d.message);
            }
        })
        .catch(err => alert("Communication error: " + err.message))
        .finally(() => {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        });
}

/** Optimistically removes a system alert from the UI and triggers a backend deletion. */
function dismissSystemAlert(msg, btnElement) {
    const alertBox = btnElement.closest('.alert');
    if(alertBox) alertBox.remove();
    
    fetch('/api/system/alerts/dismiss', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg})
    });
}

/** Fetches and displays the total disk space consumed by SQLite backups. */
function loadBackupInfo() {
    fetch('/api/system/backups/info')
        .then(r => r.json())
        .then(d => {
            const badge = document.getElementById('backup-size-badge');
            if (badge) badge.innerText = `${d.size_mb} MB (${d.count} files)`;
        }).catch(e => console.error(e));
}

/** Permanently deletes an individual Wi-Fi SSID and its entire history from the database. */
function deleteGlobalSSID(ssid) {
    if (!confirm(`Are you sure you want to completely delete "${ssid}" from all past Wi-Fi scans?`)) return;

    fetch('/api/wifi_networks_history/delete_ssid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: ssid })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            loadWifiNetworksHistory(); 
        } else {
            alert("Error: " + d.error);
        }
    });
}

/** Fetches and displays the size of the active SQLite database in MB. */
function loadDatabaseInfo() {
    fetch('/api/system/db_info')
        .then(r => r.json())
        .then(d => {
            const badge = document.getElementById('db-size-badge');
            if (badge && d.size_mb) badge.innerText = `${d.size_mb} MB`;
        }).catch(e => console.error(e));
}

// ==============================================
// HELP SECTION LOGIC
// ==============================================

/**
 * Switches between the sidebar tabs within the Help documentation page.
 * Hides all other sections and resets the search bar.
 * @param {HTMLElement} btn - The sidebar button that was clicked.
 */
function switchHelpTab(btn) {
    // 1. Reset nav pills
    document.querySelectorAll('#help-tabs .nav-link').forEach(l => l.classList.remove('active'));
    btn.classList.add('active');
    
    // 2. Hide all help sections
    document.querySelectorAll('.help-section').forEach(s => s.classList.add('hidden'));
    
    // 3. Clear search if switching via sidebar
    document.getElementById('help-search').value = "";
    resetHelpSearch();
    
    // 4. Show the target content
    const targetId = btn.getAttribute('data-target');
    const targetSection = document.getElementById(targetId);
    if (targetSection) targetSection.classList.remove('hidden');
}

/**
 * Filters the Help documentation blocks in real-time.
 * Un-hides all sections globally to allow the user to search across all tabs simultaneously.
 */
function filterHelp() {
    const query = document.getElementById('help-search').value.toLowerCase();
    
    if (!query) {
        // If the search bar is cleared, revert to the currently active tab
        resetHelpSearch();
        const activeBtn = document.querySelector('#help-tabs .nav-link.active');
        if (activeBtn) switchHelpTab(activeBtn);
        return;
    }
    
    // If searching, unhide ALL sections so the search looks globally across all pages
    document.querySelectorAll('.help-section').forEach(section => {
        section.classList.remove('hidden'); 
        let hasVisibleMatch = false;
        
        // Loop through the individual help items inside the section
        section.querySelectorAll('.help-item').forEach(item => {
            if (item.innerText.toLowerCase().includes(query)) {
                item.classList.remove('hidden');
                hasVisibleMatch = true;
            } else {
                item.classList.add('hidden');
            }
        });
        
        // Hide the whole section header if no items inside it match the search
        if (hasVisibleMatch) {
            section.style.display = 'block';
        } else {
            section.style.display = 'none';
        }
    });
}

/**
 * Resets the Help documentation back to its default visibility states.
 */
function resetHelpSearch() {
    document.querySelectorAll('.help-section').forEach(section => {
        section.style.display = '';
        section.querySelectorAll('.help-item').forEach(item => {
            item.classList.remove('hidden');
        });
    });
}

// ==============================================
// TOUCH-FRIENDLY TOOLTIP ENGINE
// ==============================================

/**
 * Touch-Friendly Tooltip Engine.
 * Replaces standard HTML `title` attributes with Bootstrap JS tooltips.
 * Configured with `trigger: 'hover focus'` to allow long-press activations on tablets.
 */
function upgradeTooltips() {
    document.querySelectorAll('[title]').forEach(el => {
        // Move the text to Bootstrap's data attribute
        el.setAttribute('data-bs-title', el.getAttribute('title'));
        el.setAttribute('data-bs-toggle', 'tooltip');
        
        // Remove the native title so we don't get ugly double-tooltips on desktop
        el.removeAttribute('title'); 
        
        new bootstrap.Tooltip(el, { 
            trigger: 'hover focus' 
        });

        // Force tooltip to hide on click (fixes iPad stuck tooltips bug)
        el.addEventListener('click', function() {
            const instance = bootstrap.Tooltip.getInstance(this);
            if (instance) instance.hide();
        });
    });
}

// Run once immediately to catch all static buttons on the page
upgradeTooltips();

// Create an automated observer that watches the page in the background.
// Whenever a live scan finishes and generates new DOM rows, this instantly upgrades their tooltips!
const tooltipObserver = new MutationObserver(() => {
    upgradeTooltips();
    
    // BUGFIX: Clean up "stuck" orphaned tooltips when their parent elements are destroyed by live data streams.
    // Bootstrap adds 'aria-describedby' to the hovered element. If that element no longer exists in the DOM, 
    // the tooltip floating in the body is an orphan and must be deleted to prevent ghost tooltips.
    document.querySelectorAll('.tooltip').forEach(tooltipNode => {
        const tooltipId = tooltipNode.getAttribute('id');
        if (tooltipId) {
            const triggerEl = document.querySelector(`[aria-describedby="${tooltipId}"]`);
            if (!triggerEl) {
                tooltipNode.remove();
            }
        }
    });
});

tooltipObserver.observe(document.body, { childList: true, subtree: true });

// ==============================================
// POWER & EXECUTION CONTROLS
// ==============================================

/**
 * Signals the backend Waitress supervisor loop to completely restart the Flask web application.
 * Reconnects the frontend automatically after 4 seconds.
 */
function restartApp() {
    if (!confirm("Are you sure you want to restart the dashboard application?")) return;
    
    fetch('/api/system/restart_app', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            alert(d.message || "Restarting...");
            document.body.innerHTML = "<h2 style='color:white; text-align:center; margin-top:20%;'>Restarting...<br><div class='spinner-border mt-3'></div></h2>";
            setTimeout(() => location.reload(), 4000);
        }).catch(e => alert("Reconnecting..."));
}

/**
 * Signals the backend to gracefully kill the Python process and exit the supervisor loop completely.
 * Warns the user that terminal/physical access is required to bring the system back online.
 */
function shutdownApp() {
    if (!confirm("Are you sure you want to completely shut down the application and supervisor?\n\nYou will need to manually start the server again from the terminal.")) return;
    
    fetch('/api/system/shutdown_app', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            alert(d.message || "Shutting down...");
            document.body.innerHTML = "<h2 style='color:white; text-align:center; margin-top:20%;'><i class='bi bi-power text-danger mb-3' style='font-size: 3rem;'></i><br>Application Offline.</h2><p style='color:gray; text-align:center;'>You may close this tab.</p>";
        }).catch(e => {
            document.body.innerHTML = "<h2 style='color:white; text-align:center; margin-top:20%;'><i class='bi bi-power text-danger mb-3' style='font-size: 3rem;'></i><br>Application Offline.</h2><p style='color:gray; text-align:center;'>You may close this tab.</p>";
        });
}

/**
 * High-privilege OS execution handler. ONLY visible if 'Dedicated Server Mode' (standalone config) is active.
 * Sends hardware-level Reboot or Shutdown signals directly to Windows or Linux.
 * @param {string} action - 'reboot' or 'shutdown'.
 */
function osAction(action) {
    let msg = action === 'reboot' 
        ? "WARNING: You are about to completely REBOOT the host Operating System. This will disconnect all users and halt all background processes.\n\nProceed?" 
        : "CRITICAL WARNING: You are about to SHUT DOWN the host Operating System. The server will physically power off and require a manual hardware boot to come back online.\n\nProceed?";
        
    if (!confirm(msg)) return;
    
    fetch('/api/system/os_action', { 
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: action})
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            alert("Command accepted. The Operating System will now " + action + ".");
            document.body.innerHTML = `<h2 style='color:white; text-align:center; margin-top:20%;'>Operating System is performing a ${action}...</h2>`;
        } else {
            alert("Error: " + d.error);
        }
    });
}

/**
 * Pre-populates the modal for merging multiple Wi-Fi scans.
 * Dynamically builds a dropdown list of the chosen scans so the user can select a "Target" to merge everything into.
 */
function openWifiMergeModal() {
    const selectedIds = Array.from(tableState.wifi.selected);
    if (selectedIds.length < 2) return alert("Please select at least TWO Wi-Fi scans to merge.");

    const selectedScans = tableState.wifi.data.filter(s => selectedIds.includes(String(s.id)));
    
    const select = document.getElementById('merge-wifi-target-select');
    select.innerHTML = selectedScans.map(s => 
        `<option value="${s.id}">${escapeHTML(s.name)} (${s.timestamp})</option>`
    ).join('');

    mergeWifiModal.show();
}

/**
 * Triggers the backend process to merge the selected Wi-Fi scans into the chosen Target profile.
 * Deletes the duplicate source scans from the database upon completion.
 */
function executeWifiMerge() {
    const targetId = document.getElementById('merge-wifi-target-select').value;
    const sourceIds = Array.from(tableState.wifi.selected).filter(id => id !== targetId);

    if (!targetId || sourceIds.length === 0) return;

    if(!confirm("Are you certain you want to merge these Wi-Fi scans? This action cannot be undone.")) return;

    const btn = document.getElementById('btn-execute-wifi-merge');
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Merging...';
    btn.disabled = true;

    fetch('/api/wifi/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: targetId, source_ids: sourceIds })
    })
    .then(r => r.json())
    .then(d => {
        if (d.status === 'success') {
            mergeWifiModal.hide();
            tableState.wifi.selected.clear();
            loadWifiHistory(); // Refresh the historical list
        } else {
            alert("Merge failed: " + d.error);
        }
    })
    .catch(err => alert("Communication error: " + err.message))
    .finally(() => {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    });
}

/**
 * Opens the Device Configuration modal to edit a device's custom metadata.
 * Pre-fills the input fields with the existing data for that specific MAC address.
 * 
 * @param {string} mac - The device MAC address.
 * @param {string} name - The existing custom name (if any).
 * @param {string} vendor - The existing vendor name (if any).
 * @param {string} comment - Existing multi-line comments.
 */
function openDeviceConfig(mac, name, vendor, comment) {
    document.getElementById('modal-dev-mac').value = mac;
    document.getElementById('modal-dev-name').value = name || "";
    document.getElementById('modal-dev-vendor').value = vendor || "";
    document.getElementById('modal-dev-comment').value = comment || "";
    deviceConfigModal.show();
}

/**
 * Submits the edited device metadata to the backend database.
 * Updates the global device registry so the changes persist across different network scans.
 * Automatically refreshes the active UI table (Network Scan or Device History) to reflect changes.
 */
function saveDeviceConfig() {
    const mac = document.getElementById('modal-dev-mac').value;
    const name = document.getElementById('modal-dev-name').value.trim();
    const vendor = document.getElementById('modal-dev-vendor').value.trim();
    const comment = document.getElementById('modal-dev-comment').value.trim();

    if (!mac) return;
    const btn = event.currentTarget || document.querySelector('#deviceConfigModal .btn-primary');
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    btn.disabled = true;

    fetch('/api/devices/update_metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mac: mac, name: name, vendor: vendor, comment: comment, network_id: currentNetworkId || null })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'success') {
            deviceConfigModal.hide();
            // Refresh whichever page is currently visible
            if (!document.getElementById('devices').classList.contains('hidden') && currentNetworkId) {
                loadNetworkDevices(currentNetworkId, ""); 
            }
            if (!document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
        } else alert("Error: " + res.error);
    }).finally(() => { btn.innerHTML = origText; btn.disabled = false; });
}