
    let updateModal, adapterModal, editSpeedTestModal, allDevices = [], allHistory = [], currentNetworkId = null;
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
    let pauseTimeout = null; // NEW: Tracks the 1-hour countdown

    function escapeHTML(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function escapeJS(str) {
        if (!str) return "";
        return String(str)
            .replace(/\\/g, "\\\\")
            .replace(/'/g, "\\'")
            .replace(/"/g, "&quot;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    const SERVICE_PORT_MAP = {
    "SSH": "22", "HTTP": "80", "HTTPS": "443", "HTTP (8080)": "8080", 
    "HTTPS (8443)": "8443", "Flask/UPnP": "5000", "Portainer/Admin": "9000"
    };

    function convertServicesToPorts(servicesStr) {
        if (!servicesStr || servicesStr === "None" || servicesStr === "NONE") return "None";
        return servicesStr.split(',').map(s => SERVICE_PORT_MAP[s.trim()] || s.trim()).join(', ');
    }

    document.addEventListener("DOMContentLoaded", () => {
        setTheme(localStorage.getItem('theme') || 'dark');
        
        // Initialize Modals safely
        if(document.getElementById('updateModal')) 
            updateModal = new bootstrap.Modal(document.getElementById('updateModal'));
        if(document.getElementById('adapterModal'))
            adapterModal = new bootstrap.Modal(document.getElementById('adapterModal'));
        if(document.getElementById('editSpeedTestModal'))
            editSpeedTestModal = new bootstrap.Modal(document.getElementById('editSpeedTestModal'));
        if(document.getElementById('devHistModal'))
            devHistModal = new bootstrap.Modal(document.getElementById('devHistModal'));
        // Initial Header Data Load
        fetch('/api/get_last_name').then(r=>r.json()).then(d => { 
            if(d.last_name && document.getElementById('st-network-name')) 
                document.getElementById('st-network-name').value = d.last_name;
            if(document.getElementById('header-wan-ip')) 
                document.getElementById('header-wan-ip').innerText = d.wan_ip;
            if(document.getElementById('header-isp')) 
                document.getElementById('header-isp').innerText = d.isp;
        });
        
            // Automatically pause/resume based on tab visibility with a 1-hour delay
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            // Start a 1-hour countdown (60 minutes * 60 seconds * 1000 milliseconds)
            pauseTimeout = setTimeout(() => {
                stopLivePolling();
                console.log("Tab hidden for 1 hour: Live bandwidth polling paused to save resources.");
            }, 60 * 60 * 1000); 
        } else {
            // The user came back! Cancel the shutdown timer if it hasn't finished yet
            if (pauseTimeout) {
                clearTimeout(pauseTimeout);
                pauseTimeout = null;
            }
            // Ensure polling is running
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

        // Start Loops
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

        const savedTouchMode = localStorage.getItem('touchMode') === 'true';
        if (savedTouchMode) document.body.classList.add('touch-mode');
        updateTouchIcon(savedTouchMode);
        // Run the new verification check
        if (typeof verifyUpdateStatus === "function") verifyUpdateStatus();
        const channelSelect = document.getElementById('update-channel-select');
        if (channelSelect && window.APP_CONFIG.updateChannel) {
            channelSelect.value = window.APP_CONFIG.updateChannel;
        }
    });

    // Function to handle database cleanup with confirmation prompts
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

// Removes devices whose associated networks have been completely deleted
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
            loadDatabaseInfo(); // <-- Updates the DB size badge instantly
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

// Removes Wi-Fi networks whose associated scans have been completely deleted
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
            loadDatabaseInfo(); // <-- Updates the DB size badge instantly
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

function loadConnectionTypes() {
    fetch('/api/settings/connection_types').then(r=>r.json()).then(d => {
        const mainSelect = document.getElementById('st-conn-type');
        const modalSelect = document.getElementById('modal-st-type');
        const sysList = document.getElementById('connection-type-list');
        
        let optionsHtml = '';
        let listHtml = '';
        
        d.forEach(t => {
            // --- FIXED: Escape the connection type name to prevent XSS ---
            const safeName = escapeHTML(t.name);
            
            optionsHtml += `<option value="${safeName}">${safeName}</option>`;
            listHtml += `<li class="list-group-item d-flex justify-content-between align-items-center small py-1">
                ${safeName}
                <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteConnectionType(${t.id})"><i class="bi bi-x-lg"></i></button>
            </li>`;
        });
        
        // Preserve selection on main test box if updating
        if (mainSelect) {
            const currentVal = mainSelect.value;
            mainSelect.innerHTML = optionsHtml;
            if (currentVal) mainSelect.value = currentVal;
        }
        if (modalSelect) modalSelect.innerHTML = optionsHtml;
        if (sysList) sysList.innerHTML = listHtml;
    });
}

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

function deleteConnectionType(id) {
    fetch('/api/settings/connection_types/delete', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id})
    }).then(loadConnectionTypes);
}

function showPage(id, link) {
    // Hide all pages
    document.querySelectorAll('.page-section').forEach(p => p.classList.add('hidden'));
    
    // Remove active class from all main nav links AND dropdown items
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.dropdown-item').forEach(l => l.classList.remove('active'));
    
    // Show target page
    document.getElementById(id).classList.remove('hidden'); 
    
    // Highlight the clicked link
    link.classList.add('active');
    
    // If it's a dropdown item, also highlight its parent 'nav-link'
    const parentDropdown = link.closest('.dropdown');
    if (parentDropdown) {
        const toggle = parentDropdown.querySelector('.nav-link.dropdown-toggle');
        if (toggle) toggle.classList.add('active');
    }
    
    // Refresh data based on page
    if(id === 'history') fetchHistory();
    if(id === 'networks') loadNetworks();
    if(id === 'dns-tool') fetchToolLogs('dns');
    if(id === 'ping-tool') fetchToolLogs('ping');
    if(id === 'wifi-history') loadWifiHistory();
    if(id === 'device-history-page') loadDeviceHistory();
    if(id === 'wifi-networks-history') loadWifiNetworksHistory();
}

    // --- Core UI Helpers ---
    function toggleTheme() {
        const next = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
        setTheme(next); localStorage.setItem('theme', next);
    }

    let isManualRefreshing = false;
    let isPolling = false; // NEW: Prevents overlapping background requests

    async function updateLiveRatesOnly() {
        // Skip if a manual refresh is happening or if the last poll hasn't finished
        if (isManualRefreshing || isPolling) return; 
        
        isPolling = true; // Lock

        try {
            const response = await fetch('/api/live_bandwidth');
            const data = await response.json();
            
            // UPDATE THE MAIN METRIC CARDS
            if (data.global_speed) {
                const downEl = document.getElementById('live-down');
                const upEl = document.getElementById('live-up');
                if (downEl) downEl.innerText = data.global_speed.download;
                if (upEl) upEl.innerText = data.global_speed.upload;
            }
            
            // Update the "Speed" column in the table
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
            isPolling = false; // Unlock so the next 3-second tick can run
        }
    }

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
            location.reload(); // Reload to show new data
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

async function refreshNetworkInfo() {
    isManualRefreshing = true;
    const btn = document.getElementById('refresh-btn');
    const originalText = btn.innerHTML;
    
    // UI Feedback
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Updating...';
    
    try {
        await fetchAdapters(); // Re-run the full data fetch
    } finally {
        isManualRefreshing = false;
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

    function setTheme(theme) {
        document.documentElement.setAttribute('data-bs-theme', theme);
        const btn = document.getElementById('theme-toggle');
        if (!btn) return;
        btn.innerHTML = theme === 'dark' ? '<i class="bi bi-moon-fill"></i>' : '<i class="bi bi-sun-fill"></i>';
        btn.className = theme === 'dark' ? 'btn btn-outline-light' : 'btn btn-outline-dark';
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
            
            // Optimistic UI update! 
            if (lastAdaptersData && lastAdaptersData.adapters) {
                if (primary === 1) lastAdaptersData.adapters.forEach(a => a.is_primary = false);
                
                const target = lastAdaptersData.adapters.find(a => a.mac === mac || (a.mac === '-' && a.id === id));
                if (target) {
                    target.name = name; target.visible = visible; target.is_primary = (primary === 1);
                }
            }
            renderAdaptersTable(); // Instant UI refresh
        } else {
            const err = await response.json();
            alert("Failed to save: " + (err.message || "Unknown error"));
        }
    }

    function toggleShowHidden() {
        const isChecked = document.getElementById('showHiddenCheck').checked;
        const unhideBtn = document.getElementById('btn-bulk-unhide');
        
        if (unhideBtn) {
            if (isChecked) unhideBtn.classList.remove('hidden');
            else unhideBtn.classList.add('hidden');
        }
        
        renderAdaptersTable(); // Instant UI update! No backend delay!
    }

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
                renderAdaptersTable(); // Instant table update
                loadWifiAdapters();    // <--- FIX: Instantly sync Wi-Fi dropdown
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
                // Optimistic UI update
                if (lastAdaptersData && lastAdaptersData.adapters) {
                    lastAdaptersData.adapters.forEach(a => {
                        if (selectedMacs.includes(a.mac)) a.visible = 1;
                    });
                }
                tableState.adapters.selected.clear();
                renderAdaptersTable(); // Instant table update
                loadWifiAdapters();    // <--- FIX: Instantly sync Wi-Fi dropdown
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

// --- 1. NETWORKS ---
    function loadNetworks() {
        fetch('/api/networks').then(r=>r.json()).then(d => {
            tableState.networks.data = d;
            tableState.networks.filtered = d;
            tableState.networks.page = 1;
            tableState.networks.selected.clear();
            renderNetworks();
        });
    }

    function filterNetworks() {
        const q = document.getElementById('networks-filter').value.toLowerCase();
        tableState.networks.filtered = tableState.networks.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
        tableState.networks.page = 1;
        renderNetworks();
    }

function renderNetworks() {
        const tb = document.getElementById('network-list');
        if(!tb) return;
        const paginatedData = getPaginatedData('networks');

        tb.innerHTML = paginatedData.length ? paginatedData.map(n => {
            const safeName = escapeJS(n.name);
            const isChecked = tableState.networks.selected.has(String(n.id)) ? 'checked' : '';
            const isProtected = n.is_protected ? 1 : 0; // NEW
            
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="networks-check" value="${n.id}" onchange="toggleSelection('networks', this)" ${isChecked}></td>
                <td><strong>${escapeHTML(n.name)}</strong></td>
                <td class="font-monospace small">${n.gateway_mac}</td>
                <td>${n.gateway_ip}</td>
                <td><span class="badge bg-secondary">${n.device_count}</span></td>
                <td><small>${n.last_scan}</small></td>
                <td class="text-end">
                    <div class="btn-group">
                        <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('networks', ${n.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                            <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-primary" onclick="loadNetworkDevices(${n.id}, '${safeName}')" title="View Devices"><i class="bi bi-eye"></i> Load</button> 
                        <button class="btn btn-sm btn-outline-secondary" onclick="exportSpecificNetwork(${n.id}, '${safeName}')" title="Export Devices CSV"><i class="bi bi-download"></i></button> 
                        <button class="btn btn-sm btn-outline-secondary" onclick="renameNetwork(${n.id}, '${safeName}')" title="Rename"><i class="bi bi-pencil"></i></button> 
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteNetwork(${n.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>`;
        }).join('') : '<tr><td colspan="7" class="text-center text-muted p-4">No saved networks found.</td></tr>';

        updateMasterCheckbox('networks', paginatedData.map(n => n.id));
    }

    // --- 2. SPEED TEST HISTORY ---
    function fetchHistory() { 
        fetch('/api/history').then(r => r.json()).then(d => { 
            tableState.history.data = d; 
            tableState.history.filtered = d;
            tableState.history.page = 1;
            tableState.history.selected.clear();
            renderHistory(); 
        });
    }

    function filterHistory() { 
        const q = document.getElementById('history-filter').value.toLowerCase(); 
        tableState.history.filtered = tableState.history.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q))); 
        tableState.history.page = 1;
        renderHistory();
    }

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
                <td class="text-end">
                    <div class="btn-group">
                        <!-- NEW: Lock Button -->
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

    // --- 3. WI-FI HISTORY ---
    function loadWifiHistory() {
        fetch('/api/wifi/history')
            .then(r => r.json())
            .then(d => {
                tableState.wifi.data = d; 
                tableState.wifi.filtered = d;
                tableState.wifi.page = 1;
                tableState.wifi.selected.clear();
                renderWifiHistoryTable();
            })
            .catch(err => console.error("History Load Error:", err));
    }

    function filterWifiHistory() {
        const q = document.getElementById('wifi-filter').value.toLowerCase();
        tableState.wifi.filtered = tableState.wifi.data.filter(h => 
            h.name.toLowerCase().includes(q) || (h.comments && h.comments.toLowerCase().includes(q))
        );
        tableState.wifi.page = 1;
        renderWifiHistoryTable();
    }

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
                <td class="text-end">
                    <div class="btn-group">
                        <!-- NEW: Lock Button -->
                        <button class="btn btn-sm ${h.is_protected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('wifi', ${h.id}, ${h.is_protected})" title="${h.is_protected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                            <i class="bi ${h.is_protected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-primary" onclick="viewPastWifi(${h.id})" title="View Results"><i class="bi bi-eye"></i></button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="exportWifiCSV(${h.id})" title="Export CSV"><i class="bi bi-download"></i></button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="editWifiHistory(${h.id}, '${safeName}', '${safeComment}')" title="Edit Name/Comment"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteWifiScan(${h.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>`}).join('') : '<tr><td colspan="5" class="text-center p-4 text-muted">No past scans found.</td></tr>';
            
        updateMasterCheckbox('wifi', paginatedData.map(h => h.id));
    }

    // --- 4. DNS & PING TOOLS ---
    function fetchToolLogs(type) {
        fetch(`/api/${type}/logs`).then(r => r.json()).then(d => {
            tableState[type].data = d;
            tableState[type].filtered = d;
            tableState[type].page = 1;
            tableState[type].selected.clear();
            renderToolLogs(type);
        });
    }

    function filterToolLogs(type) {
        const q = document.getElementById(`${type}-filter`).value.toLowerCase();
        tableState[type].filtered = tableState[type].data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
        tableState[type].page = 1;
        renderToolLogs(type);
    }

    function renderToolLogs(type) {
        const tbody = document.getElementById(`${type}-log-list`);
        if (!tbody) return;
        const paginatedData = getPaginatedData(type);
        
        if (type === 'dns') {
            tbody.innerHTML = paginatedData.length ? paginatedData.map(x => {
                const isChecked = tableState.dns.selected.has(String(x.id)) ? 'checked' : '';
                const isProtected = x.is_protected ? 1 : 0; // Grab protection state
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
                    <td class="text-end text-nowrap">
                        <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'} border-0" onclick="toggleProtection('dns', ${x.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                            <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-secondary border-0" onclick="editToolLogNetwork('dns', ${x.id}, '${escapeJS(x.network_name || '')}')" title="Edit Network Name"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('dns', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`}).join('') : '<tr><td colspan="9" class="text-center p-4">No logs found.</td></tr>';
        } else {
            tbody.innerHTML = paginatedData.length ? paginatedData.map(x => {
                const isChecked = tableState.ping.selected.has(String(x.id)) ? 'checked' : '';
                const isProtected = x.is_protected ? 1 : 0; // Grab protection state
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
                    <td class="text-end text-nowrap">
                        <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'} border-0" onclick="toggleProtection('ping', ${x.id}, ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                            <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-secondary border-0" onclick="editToolLogNetwork('ping', ${x.id}, '${escapeJS(x.network_name || '')}')" title="Edit Network Name"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('ping', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`}).join('') : '<tr><td colspan="10" class="text-center p-4">No logs found.</td></tr>';
        }
        updateMasterCheckbox(type, paginatedData.map(x => x.id));
    }

    function editToolLogNetwork(type, id, oldName) {
        const newName = prompt("Edit Network Name:", oldName);
        if (newName === null || newName === oldName) return; // Cancelled or unchanged
        
        fetch('/api/tool_logs/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type, id: id, name: newName })
        })
        .then(r => r.json())
        .then(d => {
            if (d.status === 'success') {
                fetchToolLogs(type); // Refresh the table
            } else {
                alert("Error updating network name: " + d.error);
            }
        })
        .catch(err => alert("Communication error: " + err.message));
    }

    // --- 5. UNIFIED BULK EXPORT & DELETE HELPERS ---
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
            ? dataToExport.map(d => [d.timestamp, d.domain, d.result_ip, d.status, d.router_ip||'-', d.network_name||'-', d.lan_ip||'-'])
            : dataToExport.map(d => [d.timestamp, d.target, d.status, d.latency, d.packet_loss, d.router_ip||'-', d.network_name||'-', d.lan_ip||'-']);
        
        const headers = type === 'dns' 
            ? ['Timestamp', 'Domain', 'Result IP', 'Status', 'Router IP', 'Network', 'LAN IP']
            : ['Timestamp', 'Target', 'Status', 'Latency', 'Loss', 'Router IP', 'Network', 'LAN IP'];

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

// --- UNIFIED STATE MANAGER ---
    const tableState = {
        adapters: { selected: new Set() },
        networks: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() },
        dns: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() },
        ping: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() },
        wifi: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() },
        wifiNetHist: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() }, // <-- ADDED THIS
        history: { data: [], filtered: [], page: 1, perPage: 20, selected: new Set() }
    };
    
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

    function changePage(type, direction) {
        tableState[type].page += direction;
        renderSpecificTable(type);
    }

    function changePerPage(type) {
        const val = document.getElementById(`${type}-per-page`).value;
        tableState[type].perPage = val === 'all' ? 'all' : parseInt(val);
        tableState[type].page = 1;
        renderSpecificTable(type);
    }

    function toggleSelection(type, cb) {
        if (cb.checked) tableState[type].selected.add(cb.value);
        else tableState[type].selected.delete(cb.value);
        updateMasterCheckbox(type);
    }

    function toggleAll(type, masterCb) {
        const checkboxes = document.querySelectorAll(`.${type}-check`);
        checkboxes.forEach(cb => {
            cb.checked = masterCb.checked;
            if (masterCb.checked) tableState[type].selected.add(cb.value);
            else tableState[type].selected.delete(cb.value);
        });
    }

    function updateMasterCheckbox(type, paginatedIds = null) {
        const masterCheck = document.getElementById(`${type}-master-check`);
        if (masterCheck) {
            // If we are dynamically passing IDs during a render, we use them. Otherwise we read the DOM.
            const idsToCheck = paginatedIds || Array.from(document.querySelectorAll(`.${type}-check`)).map(el => el.value);
            masterCheck.checked = idsToCheck.length > 0 && idsToCheck.every(id => tableState[type].selected.has(String(id)));
        }
    }

    function renderSpecificTable(type) {
        if (type === 'networks') renderNetworks();
        else if (type === 'dns') renderToolLogs('dns');
        else if (type === 'ping') renderToolLogs('ping');
        else if (type === 'wifi') renderWifiHistoryTable();
        else if (type === 'wifiNetHist') renderWifiNetworksHistory();
        else if (type === 'history') renderHistory();
    }

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
                selectedDevHistMacs.clear(); // Clear selections when fetching fresh data
                renderDeviceHistory();
            })
            .catch(err => {
                console.error("Device History Error:", err);
                if (tb) tb.innerHTML = `<tr><td colspan="7" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle-fill"></i> Failed to load history: ${err.message}</td></tr>`;
            });
    }

function toggleDevHistSelection(cb) {
        if (cb.checked) {
            selectedDevHistMacs.add(cb.value);
        } else {
            selectedDevHistMacs.delete(cb.value);
        }
        
        // Update the master "Select All" checkbox state
        const masterCheck = document.getElementById('dev-hist-master-check');
        if (masterCheck) {
            const pageMacs = Array.from(document.querySelectorAll('.dev-hist-check')).map(el => el.value);
            masterCheck.checked = pageMacs.length > 0 && pageMacs.every(mac => selectedDevHistMacs.has(mac));
        }
    }

function toggleAllDevHist(masterCb) {
        const checkboxes = document.querySelectorAll('.dev-hist-check');
        checkboxes.forEach(cb => {
            cb.checked = masterCb.checked;
            if (masterCb.checked) {
                selectedDevHistMacs.add(cb.value);
            } else {
                selectedDevHistMacs.delete(cb.value);
            }
        });
    }

function renderDeviceHistory() {
        const tb = document.getElementById('dev-hist-table');
        if (!tb) return;

        if (!Array.isArray(devHistFiltered)) {
            tb.innerHTML = '<tr><td colspan="8" class="text-center p-4 text-danger">Invalid data received from server.</td></tr>';
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
            const isProtected = d.is_protected ? 1 : 0;
            
            let vendorHtml = escapeHTML(d.vendor);
            if (!d.vendor || d.vendor === 'Unknown') {
                const safeMacId = d.mac_address.replace(/:/g, '');
                vendorHtml = `<span id="vendor-${safeMacId}" class="text-muted fst-italic"><span class="spinner-border spinner-border-sm me-1" style="width: 0.8rem; height: 0.8rem;"></span> Fetching...</span>`;
                missingVendors.push(d.mac_address);
            }
            
            const isChecked = selectedDevHistMacs.has(d.mac_address) ? 'checked' : '';
            
            return `
            <tr style="cursor: pointer;" onclick="viewDeviceDetails('${d.mac_address}', '${safeName}', '${safeVendor}')">
                <td onclick="event.stopPropagation()">
                    <input type="checkbox" class="dev-hist-check" value="${d.mac_address}" onchange="toggleDevHistSelection(this)" ${isChecked}>
                </td>
                <td><strong>${escapeHTML(displayName)}</strong></td>
                <td>${vendorHtml}</td>
                <td class="font-monospace text-muted">${d.mac_address}</td>
                <td><span class="badge bg-secondary">${d.network_name}</span></td>
                <td>${d.ip_address}</td>
                <td><small>${d.last_seen}</small></td>
                <td class="text-end text-nowrap" onclick="event.stopPropagation()">
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('devices', '${escapedMac}', ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="updateDeviceName('${escapedMac}', '${safeCustomN}')" title="Edit Name"><i class="bi bi-pencil"></i></button>
                </td>
            </tr>`;
        }).join('') : '<tr><td colspan="8" class="text-center p-4">No device history found.</td></tr>';

        const masterCheck = document.getElementById('dev-hist-master-check');
        if (masterCheck) {
            const pageMacs = paginatedData.map(d => d.mac_address);
            masterCheck.checked = pageMacs.length > 0 && pageMacs.every(mac => selectedDevHistMacs.has(mac));
        }

        if (missingVendors.length > 0) {
            missingVendors.forEach(mac => fetchMissingVendor(mac));
        }
    }

function exportDevHistCSV() {
        let dataToExport = [];

        // 1. Check our persistent Set of selected MACs
        if (selectedDevHistMacs.size > 0) {
            const selectedMacs = Array.from(selectedDevHistMacs);
            // Export manually selected items across ALL pages/filters
            dataToExport = allDeviceHistory.filter(d => selectedMacs.includes(d.mac_address));
        } else {
            // 2. If nothing is checked, export everything visible under the current filter
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
            "Last Seen": d.last_seen
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

function viewDeviceDetails(mac, name, vendor) {
    let hasVendor = vendor && vendor.trim() !== "" && vendor !== "undefined" && vendor !== "Unknown";
    
    // Build the HTML for the main title with a placeholder if vendor is missing
    let modalTitleHtml = `<span class="fw-bold">${escapeHTML(name)}</span> <br><span class="text-muted small fs-7 fw-normal">${escapeHTML(mac)}</span>`;
    
    if (hasVendor) {
        modalTitleHtml += `<br><span class="text-muted small fs-7 fw-normal" id="modal-vendor-container">Make: <span id="modal-vendor-val">${escapeHTML(vendor)}</span></span>`;
    } else {
        modalTitleHtml += `<br><span class="text-muted small fs-7 fw-normal" id="modal-vendor-container">Make: <span id="modal-vendor-val" class="fst-italic"><span class="spinner-border spinner-border-sm me-1" style="width: 0.7rem; height: 0.7rem;"></span> Fetching...</span></span>`;
    }
    
    document.getElementById('devHistModalTitle').innerHTML = modalTitleHtml;
    const tbody = document.getElementById('devHistModalBody');
    const exportBtn = document.getElementById('btn-export-dev-modal');
    
    tbody.innerHTML = '<tr><td colspan="5" class="text-center p-3"><div class="spinner-border spinner-border-sm text-primary"></div></td></tr>';
    exportBtn.style.display = 'none'; // Hide export until data is loaded
    
    devHistModal.show();

    // If vendor is missing, trigger an asynchronous background lookup
    if (!hasVendor) {
        fetch('/api/vendor/lookup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mac: mac })
        })
        .then(r => r.json())
        .then(res => {
            const fetchedVendor = (res.vendor && res.vendor !== 'Unknown') ? res.vendor : 'Unknown';
            const valEl = document.getElementById('modal-vendor-val');
            if (valEl) {
                valEl.outerHTML = `<span id="modal-vendor-val">${escapeHTML(fetchedVendor)}</span>`;
            }
            // Sync with global history array so it updates if re-opened
            const globalItem = allDeviceHistory.find(x => x.mac_address === mac);
            if (globalItem) globalItem.vendor = fetchedVendor;
        })
        .catch(() => {
            const valEl = document.getElementById('modal-vendor-val');
            if (valEl) valEl.outerHTML = '<span id="modal-vendor-val">Unknown</span>';
        });
    }

    fetch(`/api/device_history/${mac}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) throw new Error(data.error);

            currentModalDevData = data; // Save data for export feature
            exportBtn.style.display = 'block'; // Reveal export button
            exportBtn.onclick = () => exportSingleDeviceHistory(mac, name);

            tbody.innerHTML = data.length ? data.map(x => {
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
        })
        .catch(err => {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center p-3 text-danger">Failed to load details.</td></tr>`;
        });
}

function exportSingleDeviceHistory(mac, name) {
        if (!currentModalDevData || !currentModalDevData.length) return;
        
        const rows = currentModalDevData.map(x => {
            let historyText = x.discovery_status || "Unknown";
            if (x.previous_ip) historyText = `IP Changed (${x.previous_ip})`;

            return {
            "Network Name": x.network_name,
            "IP Address": x.ip_address,
            "Services (Ports)": convertServicesToPorts(x.services), // <-- Updated
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
            // Sync with global array so it persists during pagination
            const globalItem = allDeviceHistory.find(x => x.mac_address === mac);
            if (globalItem) globalItem.vendor = data.vendor;
        })
        .catch(err => {
            const safeMacId = mac.replace(/:/g, '');
            const el = document.getElementById(`vendor-${safeMacId}`);
            if (el) el.outerHTML = '<span class="text-muted">Failed</span>';
        });
    }

function filterDevHist() {
        const q = document.getElementById('dev-hist-filter').value.toLowerCase();
        devHistFiltered = allDeviceHistory.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
        devHistCurrentPage = 1; 
        renderDeviceHistory();
    }

function changeDevHistPage(direction) {
        devHistCurrentPage += direction;
        renderDeviceHistory();
    }

function changeDevHistPerPage() {
        const val = document.getElementById('dev-hist-per-page').value;
        devHistItemsPerPage = val === 'all' ? 'all' : parseInt(val);
        devHistCurrentPage = 1; 
        renderDeviceHistory();
    }

function exportSpecificNetwork(id, name) {
    // 1. Fetch the devices for this specific network
    fetch(`/api/networks/${id}/devices`)
        .then(r => r.json())
        .then(devices => {
            if (!devices.length) return alert("This network has no devices to export.");

            // 2. Format data for the existing CSV export tool
    const rows = devices.map(d => {
        let historyText = d.discovery_status || "Unknown";
        if (d.previous_ip) historyText = `IP Changed (${d.previous_ip})`;
        
        return {
            "Hostname": d.hostname || "Unknown",
            "Custom Name": d.custom_name || "",
            "IP Address": d.ip_address || "0.0.0.0",
            "MAC Address": d.mac_address || "Unknown",
            "Status": d.is_online ? "Online" : "Offline",
            "Services (Ports)": convertServicesToPorts(d.services), // <-- Updated
            "History": historyText
        };
    });

            // 3. Trigger the CSV download
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

    function toggleTouchMode() {
    const isTouch = document.body.classList.toggle('touch-mode');
    localStorage.setItem('touchMode', isTouch ? 'true' : 'false');
    updateTouchIcon(isTouch);
}

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

function loadNetworkDevices(id, name) {
    currentNetworkId = id; 
    showPage('devices', document.querySelectorAll('.nav-link')[1]);
    
    if(name) {
        document.getElementById('device-tab-title').innerText = `Devices in: ${name}`;
        // Auto-Populate Speed Test Location
        const locationInput = document.getElementById('st-network-name');
        if(locationInput) locationInput.value = name;
        
        // NEW: Reveal the edit pencil
        const editIcon = document.getElementById('btn-edit-active-network');
        if (editIcon) editIcon.classList.remove('hidden');
    }
    
    // Hide the buttons initially while fetching
    const btnSel = document.getElementById('btn-remove-selected');
    const btnOff = document.getElementById('btn-remove-offline');
    if (btnSel) btnSel.classList.add('hidden');
    if (btnOff) btnOff.classList.add('hidden');
    
    fetch(`/api/networks/${id}/devices`).then(r=>r.json()).then(d => {
        allDevices = d; 
        renderDevices(d);
        
        // Trigger background lookup for missing vendors
        resolveMissingVendors(allDevices);
        
        // --- FIXED: Only show 'Remove Selected' for old/saved networks ---
        if (btnSel) btnSel.classList.remove('hidden');
        if (btnOff) btnOff.classList.add('hidden'); // Explicitly keep 'Remove Offline' hidden
    });
}

function scanDevices() {
    const b = document.getElementById('btn-scan-devices'); 
    b.disabled = true; 
    b.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
    
    const btnSel = document.getElementById('btn-remove-selected');
    const btnOff = document.getElementById('btn-remove-offline');
    if (btnSel) btnSel.classList.add('hidden');
    if (btnOff) btnOff.classList.add('hidden');
    const editIcon = document.getElementById('btn-edit-active-network');
    if (editIcon) editIcon.classList.add('hidden');
    
    const tb = document.getElementById('device-list');
    // UNIFIED LOADING STATE: Matches device history & adapter loading layout
    if (tb) tb.innerHTML = `
        <tr>
            <td colspan="10" class="text-center p-5">
                <div class="spinner-border text-primary mb-3"></div>
                <h5 class="text-muted">Scanning Network...</h5>
                <p class="small text-secondary">Discovering active network devices and services.</p>
            </td>
        </tr>`;    
    
    allDevices = [];
    let isFirstDevice = true;

    // Use SSE to stream devices live
    const eventSource = new EventSource('/api/scan_network_stream');

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);

        if (data.type === 'init') {
            currentNetworkId = data.network_id;
            document.getElementById('device-tab-title').innerText = `Devices in: ${data.network_name}`;
            const locationInput = document.getElementById('st-network-name');
            if (locationInput) locationInput.value = data.network_name;
        } 
        else if (data.type === 'device') {
            if (isFirstDevice) {
                tb.innerHTML = '';
                isFirstDevice = false;
            }

            const idx = allDevices.findIndex(d => d.mac_address === data.device.mac_address);
            if (idx >= 0) allDevices[idx] = data.device;
            else allDevices.push(data.device);

            // Keep devices sorted numerically by IPv4 address (with safety fallbacks)
            allDevices.sort((a, b) => {
                const ipA = a.ip_address || "0.0.0.0";
                const ipB = b.ip_address || "0.0.0.0";
                
                const numA = ipA.split('.').reduce((acc, oct) => (acc << 8) + parseInt(oct, 10), 0) >>> 0;
                const numB = ipB.split('.').reduce((acc, oct) => (acc << 8) + parseInt(oct, 10), 0) >>> 0;
                return numA - numB;
            });

            renderDevices(allDevices);
        } 
        else if (data.type === 'complete') {
            eventSource.close();
            b.disabled = false;
            b.innerHTML = '<i class="bi bi-search"></i> Scan';
            
            // --- FIXED: Reveal ALL buttons because a live scan completed ---
            if (btnSel) btnSel.classList.remove('hidden');
            if (btnOff) btnOff.classList.remove('hidden');
            if (editIcon) editIcon.classList.remove('hidden'); // NEW
            
            // Add offline devices to the list
            if (data.offline_devices && data.offline_devices.length > 0) {
                data.offline_devices.forEach(offDev => {
                    // Prevent duplicates just in case
                    if (!allDevices.some(d => d.mac_address === offDev.mac_address)) {
                        allDevices.push(offDev);
                    }
                });

                // Re-sort so offline devices show up natively based on their last IP
                allDevices.sort((a, b) => {
                    const ipA = a.ip_address || "0.0.0.0";
                    const ipB = b.ip_address || "0.0.0.0";
                    const numA = ipA.split('.').reduce((acc, oct) => (acc << 8) + parseInt(oct, 10), 0) >>> 0;
                    const numB = ipB.split('.').reduce((acc, oct) => (acc << 8) + parseInt(oct, 10), 0) >>> 0;
                    return numA - numB;
                });
                renderDevices(allDevices);
            }
            
            // Trigger asynchronous background vendor lookups for unpopulated entries
            resolveMissingVendors(allDevices);
        }
        else if (data.type === 'error') {
            eventSource.close();
            b.disabled = false;
            b.innerHTML = '<i class="bi bi-search"></i> Scan';
            alert(data.message || 'Scan failed.');
            // Add table reset here
            if (allDevices.length === 0) {
                tb.innerHTML = `<tr><td colspan="9" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle"></i> ${data.message}</td></tr>`;
            }
        }
    };

    eventSource.onerror = function() {
        eventSource.close();
        b.disabled = false;
        b.innerHTML = '<i class="bi bi-search"></i> Scan';
        // NEW: Fix the table getting stuck visually if the stream crashes
        if (allDevices.length === 0) {
            tb.innerHTML = '<tr><td colspan="9" class="text-center p-4 text-danger"><i class="bi bi-exclamation-triangle"></i> Connection to scanner lost.</td></tr>';
        } else {
            renderDevices(allDevices); // Render whatever was found before crash
        }
    };
}

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
        
        // Safety fallback: if we don't know the protection state yet, assume 0
        const isProtected = x.is_protected ? 1 : 0;

        return `
            <tr class="${!x.is_online ? 'opacity-75' : ''}">
                <td onclick="event.stopPropagation()">
                    <input type="checkbox" class="device-check" value="${escapeHTML(x.mac_address)}" onchange="updateDeviceMasterCheck()">
                </td>
                <td><strong>${escapeHTML(x.hostname) || 'Unknown'}</strong></td>
                <td onclick="updateDeviceName('${escapedMac}', '${safeCustomN}')" style="cursor:pointer">
                    ${escapeHTML(x.custom_name) || '<i class="text-muted">Set Name</i>'} <i class="bi bi-pencil small"></i>
                </td>
                <td onclick="updateDeviceVendor('${escapedMac}', '${safeCustomV}', '${safeLookupV}')" style="cursor:pointer" title="Click to customize vendor">
                    ${vendorDisplay} <i class="bi bi-pencil small text-muted"></i>
                </td>
                <td>${ipColumnHtml}</td>
                <td class="font-monospace small text-muted">${x.mac_address}</td>
                <td class="${x.is_online ? 'text-success' : 'text-muted'}">${x.is_online ? 'Online' : 'Offline'}</td>
                <td>${serviceBadges}</td>
                <td>${historyHtml}</td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm ${isProtected ? 'btn-warning' : 'btn-outline-secondary'}" onclick="toggleProtection('devices', '${escapedMac}', ${isProtected})" title="${isProtected ? 'Unlock' : 'Lock (Protect from Cleanup)'}">
                        <i class="bi ${isProtected ? 'bi-lock-fill' : 'bi-unlock'}"></i>
                    </button>
                </td>
            </tr>`;
    }).join('') : '<tr><td colspan="10" class="text-center p-4">No devices found.</td></tr>';
}

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
            // Update in-memory state
            const target = allDevices.find(d => d.mac_address === mac);
            if (target) {
                target.custom_vendor = res.vendor;
                renderDevices(allDevices);
            }
        }
    });
}

function resolveMissingVendors(devices) {
    devices.forEach(dev => {
        // Skip lookup if a custom vendor or valid lookup vendor is already present
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


    function filterDevices() { 
        const q = document.getElementById('device-filter').value.toLowerCase(); 
        renderDevices(allDevices.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)))); 
    }
    
    function exportDevicesCSV() { 
        // We define the specific columns and headers we want in the CSV here
        const rows = allDevices.map(d => {
        let historyText = d.discovery_status || "Unknown";
        if (d.previous_ip) historyText = `IP Changed (${d.previous_ip})`;
        
        return {
            "Hostname": d.hostname || "Unknown",
            "Custom Name": d.custom_name || "",
            "IP Address": d.ip_address || "0.0.0.0",
            "MAC Address": d.mac_address || "Unknown",
            "Status": d.is_online ? "Online" : "Offline",
            "Services (Ports)": convertServicesToPorts(d.services), // <-- Updated
            "History": historyText
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
    
    function renameNetwork(id, old) { 
        const n = prompt("Rename Network:", old); 
        if(n && n !== old) {
            fetch('/api/networks/rename', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id, name: n})
            }).then(loadNetworks); 
        }
    }

    function deleteNetwork(id) { 
        if(confirm("Delete network and all devices?")) {
            fetch('/api/networks/delete', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id})
            }).then(loadNetworks); 
        }
    }

    // --- Tools (DNS/Ping/WiFi/History) ---
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
    
    function runPing() {
        const t = document.getElementById('ping-input').value;
        if(!t) return;
        const btn = document.getElementById('btn-ping');
        btn.disabled = true; btn.innerText = "Pinging...";
        document.getElementById('ping-status').innerText = "Sending packets...";
        
        fetch('/api/ping/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({target:t})})
        .then(r=>r.json()).then(d => {
            btn.disabled = false; btn.innerText = "Ping";
            document.getElementById('ping-status').innerText = `Done. Latency: ${d.latency}, Loss: ${d.loss}`;
            fetchToolLogs('ping');
        });
    }

function deleteSingleToolLog(type, id) {
    if(confirm(`Delete this ${type.toUpperCase()} log entry?`)) {
        fetch('/api/bulk_delete', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type, ids: [id] })
        }).then(() => fetchToolLogs(type));
    }
}

    function clearToolLogs(type) {
        if(confirm(`Clear all ${type.toUpperCase()} history?`)) fetch(`/api/${type}/clear`, {method:'POST'}).then(() => fetchToolLogs(type));
    }
    
    
function scanWifi() { 
    const container = document.getElementById('wifi-list'); 
    const btn = document.getElementById('wifi-scan-btn');
    const exportBtn = document.getElementById('btn-wifi-export');
    const commentBtn = document.getElementById('btn-wifi-comment');
    
    // Grab the selected adapter
    const adapterSelect = document.getElementById('wifi-adapter-select');
    let ifaceQuery = '';
    if (adapterSelect && adapterSelect.value) {
        ifaceQuery = `?iface=${encodeURIComponent(adapterSelect.value)}`;
    }
    
    // 1. Reset UI State
    if(btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
    }
    
    if(exportBtn) exportBtn.disabled = true;
    if(commentBtn) commentBtn.disabled = true;
    
    // Hide edit pencil and reset title
    const titleEl = document.getElementById('wifi-tab-title');
    const editIcon = document.getElementById('btn-edit-active-wifi');
    if (titleEl) titleEl.innerText = "Scan Wi-Fi";
    if (editIcon) editIcon.classList.add('hidden');
    
    activeScanComment = "";
    const metaDiv = document.getElementById('active-scan-meta');
    if(metaDiv) metaDiv.innerText = "";

    container.innerHTML = `
        <div class="col-12 text-center p-5">
            <div class="spinner-border text-info mb-3"></div>
            <h5 class="text-muted">Deep Scanning Airwaves...</h5>
            <p class="small text-secondary">Hardware reset initiated to detect all bands (2.4/5/6GHz).</p>
        </div>`;

    // 2. Perform Request to Python Backend WITH targeted interface
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

            // Extract the network array and metadata from the new backend response
            const nets = data.networks || data;

            if (!nets || nets.length === 0) {
                container.innerHTML = '<div class="col-12 text-center p-5 text-muted">No networks detected in range.</div>';
                return;
            }

            if(exportBtn) exportBtn.disabled = false;
            if(commentBtn) commentBtn.disabled = false;
            
            // --- NEW: Apply the Scan Name and Reveal Edit Button ---
            if (data.scan_id) window.currentWifiScanId = data.scan_id;
            if (titleEl && data.scan_name) {
                titleEl.innerText = data.scan_name;
                if (editIcon) editIcon.classList.remove('hidden');
            }

            // 3. Hand the data off to our newly unified rendering function!
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
            if(btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-search"></i> Scan Wi-Fi';
            }
        });
}

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
    
function openEditSpeedTestModal(id, name, type) {
    document.getElementById('modal-st-id').value = id;
    document.getElementById('modal-st-name').value = name;
    
    const typeSelect = document.getElementById('modal-st-type');
    typeSelect.value = type;
    
    // If a type was previously deleted but exists on an old record, temporarily add it to the dropdown so it displays correctly
    if(typeSelect.selectedIndex === -1) {
        const opt = document.createElement('option');
        opt.value = type; opt.text = type; opt.selected = true;
        typeSelect.add(opt);
    }
    
    editSpeedTestModal.show();
}

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

function deleteSingleSpeedTest(id) {
    if(confirm("Delete this speed test record?")) {
        fetch('/api/bulk_delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'history', ids: [id] })
        }).then(fetchHistory);
    }
}

    // --- Updater Logic (Global Version) ---
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
                
                // Trigger the alert badge if there is a mismatch OR standard update
                if (d.update_available || isHigher || crossChannelUpdate) {
                    const t = document.getElementById('ver-global');
                    if (t) {
                        t.classList.add('ver-update');
                        // --- FIXED: Match the new "Running V" format ---
                        t.innerHTML = `Running V${local.replace('DEV', '')} <i class="bi bi-exclamation-circle-fill"></i>`;
                    }
                }
            }
        });
    }

function isVersionHigher(v1, v2) {
        // Automatically strip 'DEV' so it strictly compares the numeric values
        const cleanV1 = String(v1).replace('DEV', '');
        const cleanV2 = String(v2).replace('DEV', '');
        const p1 = cleanV1.split('.').map(Number), p2 = cleanV2.split('.').map(Number);
        for (let i = 0; i < Math.max(p1.length, p2.length); i++) {
            if ((p1[i]||0) > (p2[i]||0)) return true;
            if ((p1[i]||0) < (p2[i]||0)) return false;
        }
        return false;
    }

function openUpdateModal() {
    // 1. Safety Check: Ensure the modal object exists before calling it
    if (!updateModal) {
        const modalEl = document.getElementById('updateModal');
        if (modalEl) updateModal = new bootstrap.Modal(modalEl);
        else return console.error("Update modal HTML is missing.");
    }

    // 2. Show the modal immediately
    updateModal.show();
    
    const msg = document.getElementById('update-msg');
    const btn = document.getElementById('btn-apply');
    const cl = document.getElementById('changelog-text');
    
    msg.innerText = "Checking for updates...";
    msg.className = "alert alert-info";
    cl.innerText = "Fetching release notes...";
    btn.disabled = true;

    // 3. Fetch status and changelog in parallel
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
            
            if (updateFound) {
                pendingRemoteVersion = remote; 
                msg.className = "alert alert-warning";
                
                let statusText = "Update Available";
                let reasonText = `v${remote.replace('DEV', '')} (Current: v${local.replace('DEV', '')})`;
                
                // Customize modal text if crossing channels
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

// 1. Trigger the update and set the verification flag
    function applyUpdate() { 
        if(!confirm("Update system? The server will restart.")) return;
        
        // Save flag to trigger verification after the page reloads
        localStorage.setItem('update_pending', 'true');

        const btn = document.getElementById('btn-apply');
        btn.disabled = true; 
        btn.innerText = "Installing...";

        fetch('/api/update/apply', {method: 'POST'})
            .then(r => r.json())
            .then(d => { 
                alert(d.message); 
                // Wait 5 seconds for the server to cycle before reloading
                setTimeout(() => location.reload(), 5000); 
            })
            .catch(err => {
                alert("Connection lost. Server is likely restarting. Page will reload.");
                setTimeout(() => location.reload(), 5000); 
            }); 
    }

    // 2. Perform file-by-file verification after the reload
    function verifyUpdateStatus() {
        if (localStorage.getItem('update_pending')) {
            localStorage.removeItem('update_pending'); // Clear flag immediately
            
            console.log("Verifying update integrity...");
            
            fetch('/api/update/check')
                .then(r => r.json())
                .then(d => {
                    if (d.mismatches && d.mismatches.length > 0) {
                        // Display the specific files that failed to update
                        const listEl = document.getElementById('failed-list');
                        if(listEl) {
                            listEl.innerHTML = d.mismatches.map(m => 
                                `<li><strong>${m.file}</strong>: Local v${m.local} <span class="text-danger">(Remote v${m.remote})</span></li>`
                            ).join('');
                        }
                        new bootstrap.Modal(document.getElementById('updateFailedModal')).show();
                    } else {
                        // All versions match the remote repository
                        alert("Update verified! All core files are successfully updated.");
                    }
                });
        }
    }

    function renderWifiResults(data, isHistory = false) {
    const container = document.getElementById('wifi-list');
    currentScanResults = data;
    
    let html = '';

    html += data.map(n => {
        let detailsHtml = '';
        
        // Use raw_bssids to dynamically build our tables
        if (n.raw_bssids && n.raw_bssids.length > 0) {
            const bands = {};
            n.raw_bssids.forEach(b => {
                const band = b.band || 'Unknown';
                if (!bands[band]) bands[band] = [];
                bands[band].push(b);
            });

            // Sort bands (2.4, 5, 6)
            const sortedBands = Object.keys(bands).sort((a, b) => {
                const wA = a.includes('2.4') ? 1 : a.includes('5') ? 2 : a.includes('6') ? 3 : 4;
                const wB = b.includes('2.4') ? 1 : b.includes('5') ? 2 : b.includes('6') ? 3 : 4;
                return wA - wB;
            });

            sortedBands.forEach((band, idx) => {
                const bandClean = escapeHTML(band.replace('GHz', 'Ghz'));
                const mtClass = idx === 0 ? '' : 'mt-4 ';
                
                // Frequency Header (Significantly larger)
                detailsHtml += `<div class="${mtClass}fw-bold text-info mb-1" style="font-size: 1.15rem;">${bandClean}</div>`;
                
                // Table Header Row for the data
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

                // Sort MACs by percentage descending
                bands[band].sort((a, b) => {
                    const valA = (a.percent !== '' && a.percent !== null) ? a.percent : -100;
                    const valB = (b.percent !== '' && b.percent !== null) ? b.percent : -100;
                    return valB - valA;
                });

                bands[band].forEach(b => {
                    const mac = b.mac || 'Unknown';
                    const ch = (b.channel && b.channel !== '0') ? b.channel : '-';
                    const auth = n.auth || 'Unknown';
                    
                    // Format percentage colors natively in the column
                    let pctHtml = '<span class="fw-bold text-muted">-%</span>';
                    if (b.percent !== '' && b.percent !== null) {
                        let color = 'text-danger';
                        if (b.percent >= 75) color = 'text-success';
                        else if (b.percent >= 40) color = 'text-warning';
                        pctHtml = `<span class="fw-bold ${color}">${escapeHTML(String(b.percent))}%</span>`;
                    }

                    // Data Row
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
            // Safe fallback if raw_bssids is missing
            detailsHtml = n.details;
        } else {
            // Extreme fallback for super old scans
            detailsHtml = `<span class="text-muted">${escapeHTML(n.band || '')}</span>`;
        }

        const chDisplay = n.channel ? (n.channel.startsWith('Ch:') ? n.channel : 'Ch: ' + n.channel) : 'Ch: -';

        return `
        <div class="col-lg-6 mb-3"> <!-- Widened to col-lg-6 so the larger table text fits comfortably -->
            <div class="card shadow-sm h-100 border-0 bg-body-tertiary">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-2 gap-2">
                        <!-- Made the network SSID name larger -->
                        <strong class="text-truncate fs-5" title="${escapeHTML(n.ssid)}" style="max-width: 55%;">${escapeHTML(n.ssid)}</strong>
                        <span class="badge bg-primary text-nowrap text-truncate text-end" title="${escapeHTML(chDisplay)}" style="max-width: 45%; font-size: 0.85rem;">${escapeHTML(chDisplay)}</span>
                    </div>
                    
                    <div class="border-top pt-2 mt-2 w-100">
                        ${detailsHtml}
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
    
    container.innerHTML = html;
}

function exportWifiCSV(id) {
    // Points directly to the backend CSV endpoint for the specific scan ID
    window.location.href = `/api/wifi/export/${id}`;
}


function viewPastWifi(id) {
    fetch(`/api/wifi/history/${id}`).then(r => r.json()).then(d => {
        showPage('wifi', document.querySelector('[onclick*="showPage(\'wifi\'"]'));
        
        // Store ID globally and reveal the edit tools
        window.currentWifiScanId = id;
        const titleEl = document.getElementById('wifi-tab-title');
        const editIcon = document.getElementById('btn-edit-active-wifi');
        
        if (titleEl) {
            titleEl.innerText = d.name;
            if (editIcon) editIcon.classList.remove('hidden');
        }
        
        renderWifiResults(d.results, true);
    });
}

function deleteWifiScan(id) {
    if(confirm("Delete this scan record?")) {
        fetch('/api/wifi/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id })
        }).then(loadWifiHistory);
    }
}


function clearAllWifiHistory() {
    if(confirm("Are you sure you want to permanently delete ALL Wi-Fi scan history?")) {
        fetch('/api/wifi/history/clear_all', { method: 'POST' })
            .then(() => loadWifiHistory());
    }
}

// Export Active Scan Function
function exportActiveWifiCSV() {
    if (!currentScanResults.length) return;

    fetch('/api/wifi/export_active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: currentScanResults })
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

function editWifiHistory(id, oldName, oldComment) {
    // 1. Prompt for new Name
    const newName = prompt("Edit Scan Name:", oldName);
    if (newName === null) return; // Cancelled
    
    // 2. Prompt for new Comment
    const newComment = prompt("Edit Comment:", oldComment);
    if (newComment === null) return; // Cancelled

    // 3. Send update to server
    fetch('/api/wifi/history/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            id: id, 
            name: newName, 
            comment: newComment 
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "success") {
            loadWifiHistory(); // Refresh the list to show changes
        } else {
            alert("Error: " + data.error);
        }
    })
    .catch(err => alert("Communication error: " + err.message));
} // <--- THIS BRACE WAS MISSING. IT CLOSES THE FUNCTION PROPERLY.

// --- BULK ACTION HELPERS ---
function toggleSelectAll(source, className) {
    document.querySelectorAll(`.${className}`).forEach(cb => cb.checked = source.checked);
}

function getSelectedIds(className) {
    return Array.from(document.querySelectorAll(`.${className}:checked`)).map(cb => cb.value);
}

// --- UPDATED SPEED TEST EDIT LOGIC ---
function editHistory(id, oldName, oldType) { 
    const n = prompt("Rename Network:", oldName); 
    if (n === null) return; // Cancelled
    
    const t = prompt("Connection Type (e.g., Wi-Fi or Ethernet):", oldType);
    if (t === null) return; // Cancelled

    fetch('/api/history/update', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({id: id, name: n, type: t})
    }).then(fetchHistory); 
}

let currentModalWifiNetData = [];

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
                renderWifiNetworksHistory();
            })
            .catch(err => {
                if (tb) tb.innerHTML = `<tr><td colspan="7" class="text-center p-4 text-danger">Error: ${err.message}</td></tr>`;
            });
    }

    function filterWifiNetHist() {
        const q = document.getElementById('wifiNetHist-filter').value.toLowerCase();
        tableState.wifiNetHist.filtered = tableState.wifiNetHist.data.filter(x => Object.values(x).some(v => String(v).toLowerCase().includes(q)));
        tableState.wifiNetHist.page = 1;
        renderWifiNetworksHistory();
    }

function renderWifiNetworksHistory() {
        const tb = document.getElementById('wifi-net-hist-table');
        if (!tb) return;
        const paginatedData = getPaginatedData('wifiNetHist');

        tb.innerHTML = paginatedData.length ? paginatedData.map(n => {
            const isChecked = tableState.wifiNetHist.selected.has(n.ssid) ? 'checked' : '';
            const safeSSID = escapeJS(n.ssid);
            const isProtected = n.is_protected ? 1 : 0; // NEW
            
            return `
            <tr style="cursor: pointer;" onclick="viewWifiNetworkDetails('${safeSSID}')">
                <td onclick="event.stopPropagation()"><input type="checkbox" class="wifiNetHist-check" value="${n.ssid}" onchange="toggleSelection('wifiNetHist', this)" ${isChecked}></td>
                <td><strong>${escapeHTML(n.ssid)}</strong></td>
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

    function viewWifiNetworkDetails(ssid) {
        document.getElementById('wifiNetModalTitle').innerText = `History for: ${ssid}`;
        const tbody = document.getElementById('wifiNetModalBody');
        const exportBtn = document.getElementById('btn-export-wifiNet-modal');
        
        tbody.innerHTML = '<tr><td colspan="6" class="text-center p-3"><div class="spinner-border spinner-border-sm text-primary"></div></td></tr>';
        exportBtn.style.display = 'none';
        
        // Setup Modal (Use singleton to prevent memory leaks if clicked multiple times)
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

            tbody.innerHTML = data.length ? data.map(x => {
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
        })
        .catch(err => {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center p-3 text-danger">Failed to load details.</td></tr>`;
        });
    }

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
            "Last Seen": d.last_seen
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

function changeUpdateChannel() {
    const select = document.getElementById('update-channel-select');
    const newChannel = select.value;
    const newChannelName = select.options[select.selectedIndex].text;
    const oldChannel = window.APP_CONFIG.updateChannel || 'stable';

    // 1. Display Dynamic Warnings
    if (newChannel !== oldChannel) {
        const warning = `Are you sure you want to switch the update channel to ${newChannelName}?\n\nThe database or configurations may be different and not backwards compatible.`;
        if (!confirm(warning)) {
            select.value = oldChannel; // Revert the dropdown
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

function saveWorkerSettings() {
    const payload = {
        server_threads: parseInt(document.getElementById('workers-server').value),
        scan_workers: parseInt(document.getElementById('workers-scan').value),
        ping_workers: parseInt(document.getElementById('workers-ping').value)
    };
    
    // --- NEW: Add confirmation prompt ---
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
            // --- NEW: Alert and reload the page ---
            alert("Worker settings saved. The system is restarting and will reload in 3 seconds.");
            setTimeout(() => {
                window.location.reload();
            }, 3000);
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

function resetWorkerSettings() {
    if (!hardwareWorkerDefaults) return;
    document.getElementById('workers-server').value = hardwareWorkerDefaults.server_threads;
    document.getElementById('workers-scan').value = hardwareWorkerDefaults.scan_workers;
    document.getElementById('workers-ping').value = hardwareWorkerDefaults.ping_workers;
    saveWorkerSettings();
}

function deleteSelectedDeviceHistory() {
    if (selectedDevHistMacs.size === 0) {
        return alert("Please select at least one device to delete.");
    }
    
    if (!confirm(`Are you sure you want to completely delete the ${selectedDevHistMacs.size} selected device(s)? This will permanently remove them from all scanned networks.`)) {
        return;
    }

    const selectedMacs = Array.from(selectedDevHistMacs);
    
    // Disable the button to prevent spam clicking
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
            loadDeviceHistory(); // Refresh the list
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

function updateDeviceMasterCheck() {
    const checks = document.querySelectorAll('.device-check');
    const checked = document.querySelectorAll('.device-check:checked');
    const master = document.getElementById('device-master-check');
    if (master) master.checked = (checks.length > 0 && checks.length === checked.length);
}

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
            document.getElementById('device-master-check').checked = false; // Reset master check
            loadNetworkDevices(currentNetworkId, ""); // Refresh the list
        } else {
            alert(d.error);
        }
    });
}

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
            document.getElementById('device-master-check').checked = false; // Reset master check
            loadNetworkDevices(currentNetworkId, ""); // Refresh the list
        } else {
            alert(d.error);
        }
    });
}

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

function dismissSystemAlert(msg, btnElement) {
    // Optimistically remove from UI instantly
    const alertBox = btnElement.closest('.alert');
    if(alertBox) alertBox.remove();
    
    // Tell the backend to delete it from the JSON file
    fetch('/api/system/alerts/dismiss', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg})
    });
}

function loadBackupInfo() {
    fetch('/api/system/backups/info')
        .then(r => r.json())
        .then(d => {
            const badge = document.getElementById('backup-size-badge');
            if (badge) badge.innerText = `${d.size_mb} MB (${d.count} files)`;
        }).catch(e => console.error(e));
}

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

// Handle Logging toggles
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

    // Handle clearing the system diagnostic logs
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
                    // NEW: Instantly reset the size badge on the UI
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

// Function to delete an individual SSID from the global history
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
            loadWifiNetworksHistory(); // Refresh the list
        } else {
            alert("Error: " + d.error);
        }
    });
}

// Updated toggleProtection to refresh the correct table based on type
// Updated toggleProtection to refresh the correct table based on type
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
            // Refresh the specific table we are looking at
            if (!document.getElementById('devices').classList.contains('hidden') && currentNetworkId) {
                loadNetworkDevices(currentNetworkId, "");
            }
            if (!document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
        }
    });
}

function loadDatabaseInfo() {
    fetch('/api/system/db_info')
        .then(r => r.json())
        .then(d => {
            const badge = document.getElementById('db-size-badge');
            if (badge && d.size_mb) badge.innerText = `${d.size_mb} MB`;
        }).catch(e => console.error(e));
}
    // Start the loop and run it immediately
    function startLivePolling() {
        if (!liveBandwidthInterval) {
            updateLiveRatesOnly(); // Run once instantly
            liveBandwidthInterval = setInterval(updateLiveRatesOnly, 3000);
        }
    }

    // Stop the loop to save resources
    function stopLivePolling() {
        if (liveBandwidthInterval) {
            clearInterval(liveBandwidthInterval);
            liveBandwidthInterval = null;
        }
    }

// Overrides the existing updateDeviceName so it refreshes BOTH tables if needed
function updateDeviceName(mac, old) { 
    const n = prompt("Set Custom Name:", (old === 'null' || old === 'undefined') ? '' : old); 
    if (n !== null) {
        fetch('/api/devices/update_name', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify({mac: mac, network_id: currentNetworkId || null, name: n})
        }).then(() => {
            // Refresh whichever page we are currently looking at
            if (!document.getElementById('devices').classList.contains('hidden') && currentNetworkId) {
                loadNetworkDevices(currentNetworkId, ""); 
            }
            if (!document.getElementById('device-history-page').classList.contains('hidden')) {
                loadDeviceHistory();
            }
        }); 
    }
}

let globalWifiAdapters = []; // Tracks which interfaces are actually Wi-Fi

function loadWifiAdapters() {
    fetch('/api/wifi/interfaces')
        .then(r => r.json())
        .then(data => {
            globalWifiAdapters = data; // Store full objects for modal verification
            const select = document.getElementById('wifi-adapter-select');
            if (!select) return;
            
            // Retain the Auto option
            let html = '<option value="">Auto (All Adapters)</option>';
            data.forEach(iface => {
                // Uses iface.id for backend commands, but iface.name for the UI display
                html += `<option value="${escapeHTML(iface.id)}">${escapeHTML(iface.name)}</option>`;
            });
            select.innerHTML = html;

            // Retrieve previously saved selection
            const preferred = localStorage.getItem('preferredWifiAdapter');
            if (preferred && data.some(opt => opt.id === preferred)) {
                // If it exists in the current valid dropdown list, select it
                select.value = preferred;
            } else if (preferred) {
                // If the adapter is unplugged or hidden, fallback to auto and clear memory
                select.value = "";
                localStorage.removeItem('preferredWifiAdapter');
            }

            // Bind an event to save changes automatically and update the banner
            select.onchange = function() {
                if (this.value) {
                    localStorage.setItem('preferredWifiAdapter', this.value);
                } else {
                    localStorage.removeItem('preferredWifiAdapter');
                }
                updateWifiScannerAlert();
            };
            
            // Set initial banner state
            updateWifiScannerAlert();
        })
        .catch(err => console.error("Error loading Wi-Fi adapters:", err));
}

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

function resetWifiScanner() {
    localStorage.removeItem('preferredWifiAdapter');
    const select = document.getElementById('wifi-adapter-select');
    if (select) select.value = "";
    updateWifiScannerAlert();
}

function renameActiveNetwork() {
    if (!currentNetworkId) return;
    
    // Extract the raw network name from the header text (removing "Devices in: ")
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
                // Instantly update the UI without reloading
                document.getElementById('device-tab-title').innerText = `Devices in: ${newName}`;
                const locationInput = document.getElementById('st-network-name');
                if (locationInput) locationInput.value = newName;
            } else {
                alert("Failed to rename network.");
            }
        }); 
    }
}

function renameActiveWifiScan() {
    if (!window.currentWifiScanId) return;
    
    const titleEl = document.getElementById('wifi-tab-title');
    const oldName = titleEl.innerText;
    
    const newName = prompt("Rename Wi-Fi Scan:", oldName); 
    if (newName && newName !== oldName) {
        fetch('/api/wifi/history/update', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify({
                id: window.currentWifiScanId, 
                name: newName, 
                comment: "" // Keeps the existing logic happy
            })
        }).then(r => r.json()).then(res => {
            if (res.status === 'success') {
                titleEl.innerText = newName;
            } else {
                alert("Failed to rename Wi-Fi scan.");
            }
        }); 
    }
}