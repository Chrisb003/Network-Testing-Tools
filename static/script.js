
    let updateModal, adapterModal, editSpeedTestModal, allDevices = [], allHistory = [], currentNetworkId = null;
    let currentScanResults = [];
    let allWifiHistory = [];
    let activeScanComment = "";
    let pendingRemoteVersion = null;
    let allDeviceHistory = [];
    let devHistModal;
    let devHistFiltered = [];
    let devHistCurrentPage = 1;
    let devHistItemsPerPage = 20;
    let selectedDevHistMacs = new Set();
    let currentModalDevData = [];

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

        // Start Loops
        setInterval(updateLiveRatesOnly, 3000); 
        fetchAdapters(); 
        checkUpdates(); 
        loadNetworks();
        loadConnectionTypes();
        const savedTouchMode = localStorage.getItem('touchMode') === 'true';
        if (savedTouchMode) document.body.classList.add('touch-mode');
        updateTouchIcon(savedTouchMode);
        // Run the new verification check
        if (typeof verifyUpdateStatus === "function") verifyUpdateStatus();
    });

    async function unpinAdapter() {
    /**
     * Locates the currently pinned adapter and removes its primary status.
     * Triggers a UI refresh upon success.
     */
    const mac = document.getElementById('modal-mac').value; // Temporary grab or fetch from data
    
    // First, we need to find which adapter is currently pinned in the local data
    const response = await fetch('/api/adapters');
    const data = await response.json();
    const pinned = data.adapters.find(a => a.is_primary === true);

    if (!pinned) return;

    if (!confirm(`Are you sure you want to unpin "${pinned.name}"? The dashboard will return to global monitoring.`)) {
        return;
    }

    try {
        const updateResponse = await fetch('/api/adapter_settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mac: pinned.mac,
                name: pinned.name,
                visible: pinned.visible ? 1 : 0,
                is_primary: 0 // Remove the pin
            })
        });

        if (updateResponse.ok) {
            // Refresh the full UI to update the header and live rates
            refreshNetworkInfo();
        } else {
            alert("Failed to unpin adapter.");
        }
    } catch (err) {
        alert("Error: " + err.message);
    }
}

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

function loadConnectionTypes() {
    fetch('/api/settings/connection_types').then(r=>r.json()).then(d => {
        const mainSelect = document.getElementById('st-conn-type');
        const modalSelect = document.getElementById('modal-st-type');
        const sysList = document.getElementById('connection-type-list');
        
        let optionsHtml = '';
        let listHtml = '';
        
        d.forEach(t => {
            optionsHtml += `<option value="${t.name}">${t.name}</option>`;
            listHtml += `<li class="list-group-item d-flex justify-content-between align-items-center small py-1">
                ${t.name}
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

// Ensure the showPage function handles the new 'system' page
function showPage(id, link) {
    document.querySelectorAll('.page-section').forEach(p => p.classList.add('hidden'));
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.getElementById(id).classList.remove('hidden'); 
    link.classList.add('active');
    
    // Refresh data based on page
    if(id === 'history') fetchHistory();
    if(id === 'networks') loadNetworks();
    if(id === 'dns-tool') fetchToolLogs('dns');
    if(id === 'ping-tool') fetchToolLogs('ping');
    if(id === 'wifi-history') loadWifiHistory();
    if(id === 'device-history-page') loadDeviceHistory();
    if(id === 'device-history-page') loadDeviceHistory();
    if(id === 'wifi-networks-history') loadWifiNetworksHistory();
    // System page doesn't need auto-refresh on load
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
            const response = await fetch('/api/adapters');
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
    // --- Consolidated Adapter Logic ---

async function fetchAdapters() {
    /**
     * Performs a full data fetch to update:
     * 1. The Global Header (WAN, Router IP, DNS).
     * 2. The Pinned Adapter Alert message.
     * 3. The Adapter Table (IPs, Status, MACs).
     * 4. Handles "Pinned" status for the primary interface.
     * Includes safety fallbacks for offline/NoneType scenarios.
     */
    const noMac = document.getElementById('hideNoMacCheck').checked;
    const showHidden = document.getElementById('showHiddenCheck').checked;

    try {
        const response = await fetch('/api/adapters');
        const data = await response.json();
        
        if (!data || !data.adapters) return;

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

        // --- 2. PINNED ADAPTER ALERT LOGIC ---
        const alertEl = document.getElementById('pinned-adapter-alert');
        const alertNameEl = document.getElementById('pinned-adapter-name');
        
        // Find if any adapter in the response is marked as primary
        const pinned = data.adapters.find(a => a.is_primary === true);
        
        if (pinned && alertEl && alertNameEl) {
            alertEl.classList.remove('hidden');
            alertNameEl.innerText = pinned.name; // Display custom name
        } else if (alertEl) {
            alertEl.classList.add('hidden');
        }

        const tbody = document.getElementById('adapter-table'); 
        if (!tbody) return;

        // 3. Render the Adapter Table
        tbody.innerHTML = data.adapters.map(a => {
            // Filter Logic based on UI toggles
            if (noMac && (!a.mac || a.mac === '-')) return '';
            if (!a.visible && !showHidden) return '';

            // Sanitize strings for the onclick event
            const safeName = (a.name || '').replace(/'/g, "\\'"); 
            const safeId = (a.id || '').replace(/'/g, "\\'");
            const originalMac = a.mac || '-';
            
            // Format MAC for display (colons instead of dashes)
            const displayMac = originalMac.replace(/-/g, ':');
            
            const isVisible = !!a.visible;
            const isPrimary = !!a.is_primary;

            return `
                <tr data-id="${a.id}" class="${!a.visible ? 'opacity-50' : ''}">
                    <td>
                        <strong>${a.name}</strong> 
                        ${isPrimary ? '<span class="badge bg-primary ms-1" style="font-size: 0.6rem;">PINNED</span>' : ''}
                    </td>
                    <td class="text-muted small">${a.id}</td>
                    <td class="${a.status === 'Active' ? 'status-active' : 'status-inactive'}">
                        ${a.status}
                    </td>
                    <!-- Added text-nowrap to prevent 2 lines, and used displayMac -->
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

    } catch (err) {
        // Fallback for if the API fails entirely due to disconnection
        console.warn("Failed to fetch adapter data: System might be offline.", err);
        const routerEl = document.getElementById('header-router-ip');
        if (routerEl) routerEl.innerText = "Disconnected";
    }
}

function openAdapterConfig(mac, id, name, vis, primary) {
    document.getElementById('modal-mac').value = mac; 
    document.getElementById('modal-sys-id').value = id;
    document.getElementById('modal-custom-name').value = name; 
    document.getElementById('modal-visible').checked = vis;
    document.getElementById('modal-pin-primary').checked = primary; // Set pinning state
    adapterModal.show();
}

async function saveAdapterSettings() {
    const mac = document.getElementById('modal-mac').value;
    const id = document.getElementById('modal-sys-id').value;
    const name = document.getElementById('modal-custom-name').value;
    const visible = document.getElementById('modal-visible').checked ? 1 : 0;
    const primary = document.getElementById('modal-pin-primary').checked ? 1 : 0;

    // Use /api/adapter_settings to match your app.py route exactly
    const response = await fetch('/api/adapter_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mac: mac,
            id: id,
            name: name,
            visible: visible,
            is_primary: primary
        })
    });

    if (response.ok) {
        adapterModal.hide();
        // Trigger a full refresh so the Header (WAN/Router) updates if pinned
        refreshNetworkInfo(); 
    } else {
        const err = await response.json();
        alert("Failed to save: " + (err.message || "Unknown error"));
    }
}

    // --- Saved Networks & Device Logic ---
let allNetworks = []; // Global array to hold the network data

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
            const safeName = (n.name || '').replace(/'/g, "\\'");
            const isChecked = tableState.networks.selected.has(String(n.id)) ? 'checked' : '';
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="networks-check" value="${n.id}" onchange="toggleSelection('networks', this)" ${isChecked}></td>
                <td><strong>${n.name}</strong></td>
                <td class="font-monospace small">${n.gateway_mac}</td>
                <td>${n.gateway_ip}</td>
                <td><span class="badge bg-secondary">${n.device_count}</span></td>
                <td><small>${n.last_scan}</small></td>
                <td>
                    <div class="btn-group">
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
            const safeName = (x.network_name || '').replace(/'/g, "\\'");
            const safeType = (x.connection_type || '').replace(/'/g, "\\'");
            const isChecked = tableState.history.selected.has(String(x.id)) ? 'checked' : '';
            
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="history-check" value="${x.id}" onchange="toggleSelection('history', this)" ${isChecked}></td>
                <td><small>${x.timestamp}</small></td>
                <td><strong>${x.network_name}</strong></td>
                <td>${x.isp || '-'}</td>
                <td><span class="badge bg-light text-dark border">${x.connection_type}</span></td>
                <td class="text-primary fw-bold">${x.download}</td>
                <td class="text-success fw-bold">${x.upload}</td>
                <td>${x.ping}</td>
                <td class="text-muted small">${x.device_ip || '-'}</td> 
                <td class="text-muted small">${x.wan_ip || '-'}</td>
                <td class="text-end">
                    <div class="btn-group">
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
            const safeName = h.name.replace(/'/g, "\\'");
            const safeComment = (h.comments || "").replace(/'/g, "\\'");
            const isChecked = tableState.wifi.selected.has(String(h.id)) ? 'checked' : '';
            return `
            <tr>
                <td onclick="event.stopPropagation()"><input type="checkbox" class="wifi-check" value="${h.id}" onchange="toggleSelection('wifi', this)" ${isChecked}></td>
                <td><small class="text-muted">${h.timestamp}</small></td>
                <td><div class="fw-bold text-primary">${h.name}</div></td>
                <td><small class="text-muted">${h.comments || 'No comments'}</small></td>
                <td class="text-end">
                    <div class="btn-group">
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
                return `
                <tr>
                    <td onclick="event.stopPropagation()"><input type="checkbox" class="dns-check" value="${x.id}" onchange="toggleSelection('dns', this)" ${isChecked}></td>
                    <td><small class="text-muted">${x.timestamp}</small></td>
                    <td><strong>${x.domain}</strong></td>
                    <td class="font-monospace">${x.result_ip}</td>
                    <td><span class="badge ${x.status==='Resolved'?'bg-success-subtle text-success':'bg-danger-subtle text-danger'}">${x.status}</span></td>
                    <td><small>${x.router_ip || '-'}</small></td>
                    <td><small>${x.network_name || '-'}</small></td>
                    <td><small>${x.lan_ip || '-'}</small></td>
                    <td class="text-end">
                        <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('dns', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`}).join('') : '<tr><td colspan="9" class="text-center p-4">No logs found.</td></tr>';
        } else {
            tbody.innerHTML = paginatedData.length ? paginatedData.map(x => {
                const isChecked = tableState.ping.selected.has(String(x.id)) ? 'checked' : '';
                return `
                <tr>
                    <td onclick="event.stopPropagation()"><input type="checkbox" class="ping-check" value="${x.id}" onchange="toggleSelection('ping', this)" ${isChecked}></td>
                    <td><small class="text-muted">${x.timestamp}</small></td>
                    <td><strong>${x.target}</strong></td>
                    <td><span class="badge ${x.status==='Success'?'bg-success':'bg-warning'}">${x.status}</span></td>
                    <td>${x.latency}</td>
                    <td>${x.packet_loss}</td>
                    <td><small>${x.router_ip || '-'}</small></td>
                    <td><small>${x.network_name || '-'}</small></td>
                    <td><small>${x.lan_ip || '-'}</small></td>
                    <td class="text-end">
                        <button class="btn btn-sm btn-outline-danger border-0" onclick="deleteSingleToolLog('ping', ${x.id})" title="Delete"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`}).join('') : '<tr><td colspan="10" class="text-center p-4">No logs found.</td></tr>';
        }
        updateMasterCheckbox(type, paginatedData.map(x => x.id));
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
            tb.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Invalid data received from server.</td></tr>';
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
            const safeName = String(displayName).replace(/'/g, "\\'");
            
            let vendorHtml = d.vendor;
            if (!vendorHtml || vendorHtml === 'Unknown') {
                const safeMacId = d.mac_address.replace(/:/g, '');
                vendorHtml = `<span id="vendor-${safeMacId}" class="text-muted fst-italic"><span class="spinner-border spinner-border-sm me-1" style="width: 0.8rem; height: 0.8rem;"></span> Fetching...</span>`;
                missingVendors.push(d.mac_address);
            }
            
            // Check if this MAC is in our persistent memory bank
            const isChecked = selectedDevHistMacs.has(d.mac_address) ? 'checked' : '';
            
            return `
            <tr style="cursor: pointer;" onclick="viewDeviceDetails('${d.mac_address}', '${safeName}')">
                <td onclick="event.stopPropagation()">
                    <input type="checkbox" class="dev-hist-check" value="${d.mac_address}" onchange="toggleDevHistSelection(this)" ${isChecked}>
                </td>
                <td><strong>${displayName}</strong></td>
                <td>${vendorHtml}</td>
                <td class="font-monospace text-muted">${d.mac_address}</td>
                <td><span class="badge bg-secondary">${d.network_name}</span></td>
                <td>${d.ip_address}</td>
                <td><small>${d.last_seen}</small></td>
            </tr>`;
        }).join('') : '<tr><td colspan="7" class="text-center p-4">No device history found.</td></tr>';

        // Auto-update the master checkbox for the newly rendered page
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

function viewDeviceDetails(mac, name) {
        document.getElementById('devHistModalTitle').innerText = `History for: ${name} (${mac})`;
        const tbody = document.getElementById('devHistModalBody');
        const exportBtn = document.getElementById('btn-export-dev-modal');
        
        tbody.innerHTML = '<tr><td colspan="4" class="text-center p-3"><div class="spinner-border spinner-border-sm text-primary"></div></td></tr>';
        exportBtn.style.display = 'none'; // Hide export until data is loaded
        
        devHistModal.show();

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

                    return `
                    <tr>
                        <td><strong>${x.network_name}</strong></td>
                        <td class="font-monospace">${x.ip_address}</td>
                        <td>${historyHtml}</td>
                        <td><small>${x.last_seen}</small></td>
                    </tr>`;
                }).join('') : '<tr><td colspan="4" class="text-center p-3">No data available.</td></tr>';
            })
            .catch(err => {
                tbody.innerHTML = `<tr><td colspan="4" class="text-center p-3 text-danger">Failed to load details.</td></tr>`;
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
            "Services": d.services || "None",
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
        }
        
        fetch(`/api/networks/${id}/devices`).then(r=>r.json()).then(d => {
            allDevices = d; 
            renderDevices(d);
        });
    }

    function scanDevices() {
        const b = document.getElementById('btn-scan-devices'); 
        const originalText = b.innerText; // Save original text to restore later

        // 1. Show Loading State
        b.disabled = true; 
        b.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Scanning...';
        
        // 2. Clear the table temporarily to indicate refresh
        const tb = document.getElementById('device-list');
        // Updated colspan="7" for the new History column
        if (tb) tb.innerHTML = '<tr><td colspan="7" class="text-center p-5"><div class="spinner-border text-primary mb-3"></div><h5 class="text-muted">Scanning Network...</h5><p class="small text-secondary">This may take a few seconds.</p></td></tr>';

        // 3. Perform the Scan
        fetch('/api/scan_network')
            .then(r => r.json())
            .then(d => {
                if(d.error) { 
                    alert(d.error); 
                    // Restore table if error with colspan="7"
                    tb.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Scan Failed. Check console/logs.</td></tr>';
                    return; 
                }
                loadNetworkDevices(d.network_id, d.network_name);
            })
            .catch(err => {
                console.error("Scan Error:", err);
                // Communication error UI with colspan="7"
                tb.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Communication Error.</td></tr>';
            })
            .finally(() => {
                // 4. Restore Button State
                b.disabled = false; 
                b.innerHTML = '<i class="bi bi-search"></i> Scan'; 
            });
    }

    function renderDevices(d) {
        const tb = document.getElementById('device-list');
        if (!tb) return;

        tb.innerHTML = d.length ? d.map(x => {
            // Force Link Logic
            const ip = x.ip_address || "0.0.0.0";
            const services = String(x.services || "").toUpperCase();
            const hasWeb = services.includes("HTTP");
            
            let ipColumnHtml;
            if (hasWeb) {
                const protocol = services.includes("HTTPS") ? "https" : "http";
                const url = `${protocol}://${ip}`;
                ipColumnHtml = `<a href="${url}" target="_blank" style="color: #0d6efd !important; text-decoration: underline !important; font-weight: bold;">${ip}</a>`;
            } else {
                ipColumnHtml = ip;
            }

            // Service Badges
            let serviceBadges = `<span class="badge bg-secondary opacity-50">None</span>`;
            if (services && services !== "NONE") {
                serviceBadges = services.split(',').map(s => {
                    const sTrim = s.trim();
                    const badgeClass = sTrim.includes("HTTP") ? "bg-primary" : "bg-info text-dark";
                    return `<span class="badge ${badgeClass} me-1">${sTrim}</span>`;
                }).join('');
            }

            // History Column Logic
            let historyHtml = `<span class="badge bg-secondary">Unknown</span>`;
            if (x.discovery_status === 'New Device') {
                historyHtml = `<span class="badge bg-success">New Device</span>`;
            } else if (x.previous_ip) {
                historyHtml = `<span class="badge bg-warning text-dark">IP Changed <small>(${x.previous_ip})</small></span>`;
            } else {
                historyHtml = `<span class="badge bg-info text-dark">Seen Before</span>`;
            }

            return `
                <tr>
                    <td><strong>${x.hostname || 'Unknown'}</strong></td>
                    <td onclick="updateDeviceName('${x.mac_address}', '${x.custom_name}')" style="cursor:pointer">
                        ${x.custom_name || '<i class="text-muted">Set Name</i>'} <i class="bi bi-pencil small"></i>
                    </td>
                    <td>${ipColumnHtml}</td>
                    <td class="font-monospace small text-muted">${x.mac_address}</td>
                    <td class="${x.is_online ? 'text-success' : 'text-muted'}">${x.is_online ? 'Online' : 'Offline'}</td>
                    <td>${serviceBadges}</td>
                    <td>${historyHtml}</td>
                </tr>`;
        }).join('') : '<tr><td colspan="7" class="text-center p-4">No devices found.</td></tr>';
    }
    
    function updateDeviceName(mac, old) { 
        const n = prompt("Set Custom Name:", (old === 'null' || old === 'undefined') ? '' : old); 
        if(n !== null) {
            fetch('/api/devices/update_name', {
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify({mac: mac, network_id: currentNetworkId, name: n})
            }).then(() => loadNetworkDevices(currentNetworkId, "")); 
        }
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
            "Services": d.services || "None",
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
    
    
    // --- MISSING WIFI LOGIC ADDED HERE ---
    function scanWifi() { 
    const container = document.getElementById('wifi-list'); 
    const btn = document.getElementById('wifi-scan-btn');
    const exportBtn = document.getElementById('btn-wifi-export');
    const commentBtn = document.getElementById('btn-wifi-comment');
    
    // 1. Reset UI State
    if(btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Scanning...';
    }
    
    if(exportBtn) exportBtn.disabled = true;
    if(commentBtn) commentBtn.disabled = true;
    
    activeScanComment = "";
    const metaDiv = document.getElementById('active-scan-meta');
    if(metaDiv) metaDiv.innerText = "";

    container.innerHTML = `
        <div class="col-12 text-center p-5">
            <div class="spinner-border text-info mb-3"></div>
            <h5 class="text-muted">Deep Scanning Airwaves...</h5>
            <p class="small text-secondary">Hardware reset initiated to detect all bands (2.4/5/6GHz).</p>
        </div>`;

    // 2. Perform Request to Python Backend
    fetch('/api/wifi')
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

            if (!data || data.length === 0) {
                container.innerHTML = '<div class="col-12 text-center p-5 text-muted">No networks detected in range.</div>';
                return;
            }

            if(exportBtn) exportBtn.disabled = false;
            if(commentBtn) commentBtn.disabled = false;

            // 3. Hand the data off to our newly unified rendering function!
            renderWifiResults(data, false);
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
                // Highlight the version tag if a global update is available OR specific files mismatch
                if (d.update_available || isVersionHigher(d.remote_version, window.APP_CONFIG.globalVersion)) {
                    const t = document.getElementById('ver-global');
                    if (t) {
                        t.classList.add('ver-update');
                        t.innerHTML = `Global: ${window.APP_CONFIG.globalVersion} <i class="bi bi-exclamation-circle-fill"></i>`;
                    }
                }
            }
        });
    }

    function isVersionHigher(v1, v2) {
        const p1 = v1.split('.').map(Number), p2 = v2.split('.').map(Number);
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
            const updateFound = d.update_available || isVersionHigher(d.remote_version, window.APP_CONFIG.globalVersion);
            
            if (updateFound) {
                pendingRemoteVersion = d.remote_version; 
                msg.className = "alert alert-warning";
                msg.innerText = d.update_available ? 
                    `Update Required: File mismatch detected (Remote v${d.remote_version})` : 
                    `Update Available: v${d.remote_version} (Current: v${window.APP_CONFIG.globalVersion})`;
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

    html += data.map(n => `
        <div class="col-md-4 mb-3">
            <div class="card shadow-sm h-100 border-0 bg-body-tertiary">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <strong class="text-truncate" title="${n.ssid}" style="max-width: 60%;">${n.ssid}</strong>
                        <span class="badge bg-primary">Ch: ${n.channel}</span>
                    </div>
                    
                    <div class="row g-0 mt-3 border-top pt-2">
                        <div class="col-12 mb-2">
                            <small class="text-muted d-block">Detected Bands & MACs</small>
                            <span class="fw-bold text-info me-2">${n.band}</span>
                            <span class="font-monospace small text-muted" style="font-size: 0.7rem;">${n.mac || ''}</span>
                        </div>
                        <div class="col-6 mb-2">
                            <small class="text-muted d-block">Signal Strength(s)</small>
                            <span class="small">${n.signal}</span>
                        </div>
                        <div class="col-6 mb-2 text-end">
                            <small class="text-muted d-block">Security</small>
                            <span class="small">${n.auth}</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>`).join('');
    container.innerHTML = html;
}

function exportWifiCSV(id) {
    // Points directly to the backend CSV endpoint for the specific scan ID
    window.location.href = `/api/wifi/export/${id}`;
}

async function promptSaveWifi() {
    // 1. Ask for Name - If they click 'Cancel', n will be null.
    let n = prompt("Enter a name for this scan (Leave blank for Date/Time):");
    if (n === null) return; // User clicked Cancel, abort saving.

    // 2. Ask for Comments - Users can hit OK without typing.
    let c = prompt("Add comments (Optional):", "");
    if (c === null) c = ""; // Treat Cancel on comments as empty string.

    try {
        const response = await fetch('/api/wifi/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                name: n, 
                comments: c, 
                results: currentScanResults 
            })
        });
        
        const result = await response.json();
        if (result.status === "success") {
            alert(`Scan saved successfully as: ${result.saved_as}`);
            // If the user is on the history page, refresh the list
            if (!document.getElementById('wifi-history').classList.contains('hidden')) {
                loadWifiHistory();
            }
        }
    } catch (err) {
        alert("Failed to save scan: " + err.message);
    }
}


function viewPastWifi(id) {
    fetch(`/api/wifi/history/${id}`).then(r => r.json()).then(d => {
        showPage('wifi', document.querySelector('[onclick*="showPage(\'wifi\'"]'));
        renderWifiResults(d.results, true);
        document.querySelector('#wifi h5').innerText = `Viewing: ${d.name}`;
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

function addCommentToActiveScan() {
    const comment = prompt("Add a comment to this current scan:", activeScanComment);
    if (comment !== null) {
        activeScanComment = comment;
        document.getElementById('active-scan-meta').innerText = comment ? `Comment: ${comment}` : "";
        
        // Optional: You can update the backend log entry if you want to 
        // associate this comment with the last auto-logged scan.
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
            const safeSSID = n.ssid.replace(/'/g, "\\'");
            return `
            <tr style="cursor: pointer;" onclick="viewWifiNetworkDetails('${safeSSID}')">
                <td onclick="event.stopPropagation()"><input type="checkbox" class="wifiNetHist-check" value="${n.ssid}" onchange="toggleSelection('wifiNetHist', this)" ${isChecked}></td>
                <td><strong>${n.ssid}</strong></td>
                <td>${n.auth}</td>
                <td><span class="badge bg-info text-dark">${n.mac_count}</span></td>
                <td><span class="badge bg-secondary">${n.scan_count}</span></td>
                <td><small class="text-muted">${n.first_seen}</small></td>
                <td><small>${n.last_seen}</small></td>
            </tr>`;
        }).join('') : '<tr><td colspan="7" class="text-center p-4">No Wi-Fi history found.</td></tr>';

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
                    <td>${x.scan_name}</td>
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