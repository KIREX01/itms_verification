/**
 * ITMS CLOSING SYSTEM - MASTER CLIENT APPLICATION JAVASCRIPT
 * Mirrors the Textual TUI Architecture & Controls 1:1 in the Web Browser.
 */

// Global Application State
let activeNavTab = 1; // 1: Dashboard, 2: ITMS, 3: Batches, 4: Queue, 5: History, 6: Settings
let currentQueueFilter = "ALL";
let pairsData = [];
let selectedPairId = null;
let selectedPairDetail = null;

let itmsCurrentSubview = "CONNECT"; // "CONNECT" | "ORDERS" | "ARCHIVE"
let itmsExplorerSource = "live"; // "live" | "local"
let itmsExplorerPage = 1;
let itmsExplorerSearch = "";
let itmsExplorerOrders = [];
let selectedItmsOrder = null;

let tabSelectedFrontFiles = [];
let tabSelectedRearFiles = [];
let currentHistoryFilter = "ALL";
let pipelinePollInterval = null;

// ============================================================================
// Initialization & Global Event Listeners
// ============================================================================
document.addEventListener("DOMContentLoaded", () => {
    fetchStats();
    fetchPairs();
    fetchItmsStatus();
    initDropzones();
    appendConsoleLog("ITMS Closing System initialized. Operator ready.", "info");

    // Periodic telemetry polling
    setInterval(fetchStats, 8000);
    setInterval(fetchItmsStatus, 25000);

    // Global Keyboard Shortcuts matching TUI Keybindings
    document.addEventListener("keydown", (e) => {
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") {
            if (e.key === "Escape") {
                e.target.blur();
            }
            return;
        }

        // Numbers 1 to 6 switch primary navigation tabs (TUI bindings)
        if (e.key >= "1" && e.key <= "6") {
            switchNavTab(parseInt(e.key));
            return;
        }

        // Functional action triggers
        const key = e.key.toLowerCase();
        if (key === "p") {
            triggerPipeline("full_pipeline");
        } else if (key === "m") {
            triggerMatching();
        } else if (key === "y") {
            triggerSyncOrders();
        } else if (key === "b") {
            triggerBatchSubmit();
        } else if (key === "i") {
            openBatchUploadModal();
        } else if (key === "a" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("approve");
        } else if (key === "s" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("swap");
        } else if (key === "e" && activeNavTab === 4) {
            if (selectedPairId) openEditPlateModal();
        } else if (key === "l" && activeNavTab === 4) {
            if (selectedPairId) openLinkOrderModal();
        } else if (e.key === "Enter" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("submit");
        } else if (e.key === "ArrowUp" || key === "k") {
            if (activeNavTab === 4) {
                e.preventDefault();
                navigateQueue(-1);
            }
        } else if (e.key === "ArrowDown" || key === "j") {
            if (activeNavTab === 4) {
                e.preventDefault();
                navigateQueue(1);
            }
        }
    });
});

// ============================================================================
// Primary Navigation Controller (Tabs 1 to 6)
// ============================================================================
function switchNavTab(tabIndex) {
    activeNavTab = tabIndex;
    const tabNames = {
        1: "dashboard",
        2: "itms",
        3: "batches",
        4: "queue",
        5: "history",
        6: "settings",
    };

    for (let i = 1; i <= 6; i++) {
        const btn = document.getElementById(`nav-tab-${i}`);
        const pane = document.getElementById(`pane-${tabNames[i]}`);
        if (btn) btn.classList.toggle("active", i === tabIndex);
        if (pane) pane.classList.toggle("active", i === tabIndex);
    }

    // Lazy load tab data on entry
    if (tabIndex === 2 && itmsCurrentSubview !== "CONNECT" && itmsExplorerOrders.length === 0) {
        fetchItmsExplorerOrders();
    } else if (tabIndex === 3) {
        fetchBatchesList();
    } else if (tabIndex === 5) {
        fetchHistoryEvents();
    }
}

// ============================================================================
// Metrics & Pipeline Telemetry
// ============================================================================
async function fetchStats() {
    try {
        const res = await fetch("/api/stats/");
        const data = await res.json();
        if (data.success && data.stats) {
            const s = data.stats;
            // Header ribbon
            setText("kpi-review", s.pending_review || 0);
            setText("kpi-approved", s.approved || 0);
            setText("kpi-submitted", s.submitted || 0);
            setText("kpi-issues", s.issues || 0);
            setText("kpi-orders", s.total_orders || 0);

            // Tab badge
            setText("badge-nav-queue", s.pending_review || 0);

            // Dashboard pipeline stage cards
            setText("flow-count-ingest", s.total_photos || (s.total_pairs * 2) || 0);
            setText("flow-count-pairs", s.total_pairs || 0);
            setText("flow-count-vision", s.total_pairs || 0);
            setText("flow-count-review", s.pending_review || 0);
            setText("flow-count-submitted", s.submitted || 0);
            setText("dash-total-orders", s.total_orders || 0);
        }
    } catch (err) {
        console.warn("Telemetry fetch error:", err);
    }
}

// ============================================================================
// ITMS Account Status & Authentication
// ============================================================================
async function fetchItmsStatus() {
    try {
        const res = await fetch("/api/itms/status/");
        const data = await res.json();
        if (data.success && data.status) {
            const st = data.status;
            const badge = document.getElementById("header-itms-badge");
            const dashStatus = document.getElementById("dash-itms-status");
            const sessionBadge = document.getElementById("itms-session-badge");
            const accountEmail = document.getElementById("itms-account-email");
            const accountDetails = document.getElementById("itms-account-details");
            const btnDisconnectView = document.getElementById("btn-disconnect-itms-view");
            const settingsEmail = document.getElementById("settings-itms-email");

            if (st.authenticated) {
                if (badge) {
                    badge.className = "badge badge-green";
                    badge.innerText = "● Connected";
                }
                if (dashStatus) dashStatus.innerHTML = `<span style="color:var(--ug-green); font-weight:700;">● Online (${escapeHtml(st.user_email)})</span>`;
                if (sessionBadge) {
                    sessionBadge.className = "badge badge-green";
                    sessionBadge.innerText = "● Authenticated & Active";
                }
                if (accountEmail) accountEmail.innerText = st.user_email || "Connected Operator";
                if (accountDetails) accountDetails.innerText = `Session active on ${st.url}. Session expires in ~${st.expires_in_days} day(s).`;
                if (btnDisconnectView) btnDisconnectView.style.display = "block";
                if (settingsEmail) settingsEmail.innerText = `${st.user_email} (stock.itms.ug)`;
            } else {
                if (badge) {
                    badge.className = "badge badge-yellow";
                    badge.innerText = "● Disconnected";
                }
                if (dashStatus) dashStatus.innerHTML = `<span style="color:var(--ug-yellow);">● Disconnected (Offline)</span>`;
                if (sessionBadge) {
                    sessionBadge.className = "badge badge-yellow";
                    sessionBadge.innerText = "● Not Connected";
                }
                if (accountEmail) accountEmail.innerText = "No Account Connected";
                if (accountDetails) accountDetails.innerText = "Connect your stock.itms.ug credentials to enable live order synchronization and evidence submissions.";
                if (btnDisconnectView) btnDisconnectView.style.display = "none";
                if (settingsEmail) settingsEmail.innerText = "Not connected. Submissions in simulation mode.";
            }
        }
    } catch (err) {
        console.warn("ITMS status fetch error:", err);
    }
}

function openItmsModal() {
    openModal("modal-itms");
}

async function submitItmsCredentials() {
    const email = document.getElementById("itms-input-email").value.trim();
    const password = document.getElementById("itms-input-password").value.trim();
    if (!email || !password) {
        showToast("ITMS Email and Password are required.", "error");
        return;
    }
    const btn = document.getElementById("btn-submit-itms-connect");
    btn.disabled = true;
    btn.innerText = "Authenticating against ITMS...";

    try {
        const res = await fetch("/api/itms/connect/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `email=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Connected to ITMS WebApp successfully.", "success");
            appendConsoleLog(`ITMS session authenticated as '${email}'.`, "success");
            document.getElementById("itms-input-password").value = "";
            fetchItmsStatus();
            switchItmsSubview("ORDERS");
        } else {
            showToast(data.message || data.error || "ITMS authentication failed.", "error");
            appendConsoleLog(`ITMS login failed: ${data.error || data.message}`, "error");
        }
    } catch (err) {
        showToast("Connection error: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "Connect & Sign In";
    }
}

async function disconnectItms() {
    if (!confirm("Are you sure you want to disconnect this ITMS WebApp account?")) return;
    try {
        const res = await fetch("/api/itms/disconnect/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("Disconnected from ITMS WebApp.", "info");
            appendConsoleLog("Disconnected from ITMS WebApp session.", "warning");
            fetchItmsStatus();
            switchItmsSubview("CONNECT");
        }
    } catch (err) {
        showToast("Error disconnecting: " + err, "error");
    }
}

// ============================================================================
// TAB 2: ITMS Hub Subviews (Connect, Active Orders, Archive)
// ============================================================================
function switchItmsSubview(subview) {
    itmsCurrentSubview = subview;
    const btnConnect = document.getElementById("btn-sub-connect");
    const btnOrders = document.getElementById("btn-sub-orders");
    const btnArchive = document.getElementById("btn-sub-archive");
    const viewConnect = document.getElementById("itms-view-connect");
    const viewOrders = document.getElementById("itms-view-orders");
    const subviewControls = document.getElementById("itms-subview-controls");

    btnConnect.classList.toggle("active", subview === "CONNECT");
    btnOrders.classList.toggle("active", subview === "ORDERS");
    btnArchive.classList.toggle("active", subview === "ARCHIVE");

    if (subview === "CONNECT") {
        viewConnect.classList.add("active");
        viewOrders.classList.remove("active");
        subviewControls.style.display = "none";
    } else {
        viewConnect.classList.remove("active");
        viewOrders.classList.add("active");
        subviewControls.style.display = "flex";
        itmsExplorerPage = 1;
        fetchItmsExplorerOrders();
    }
}

function setItmsExplorerSource(src) {
    itmsExplorerSource = src;
    itmsExplorerPage = 1;
    document.getElementById("itms-src-live-btn").classList.toggle("active", src === "live");
    document.getElementById("itms-src-local-btn").classList.toggle("active", src === "local");
    fetchItmsExplorerOrders();
}

function doItmsExplorerSearch() {
    const input = document.getElementById("itms-explorer-search");
    itmsExplorerSearch = input.value.trim();
    itmsExplorerPage = 1;
    fetchItmsExplorerOrders();
}

function clearItmsExplorerSearch() {
    const input = document.getElementById("itms-explorer-search");
    input.value = "";
    itmsExplorerSearch = "";
    itmsExplorerPage = 1;
    fetchItmsExplorerOrders();
}

function changeItmsExplorerPage(delta) {
    itmsExplorerPage = Math.max(1, itmsExplorerPage + delta);
    fetchItmsExplorerOrders();
}

async function fetchItmsExplorerOrders() {
    const tabName = (itmsCurrentSubview === "ARCHIVE") ? "archive" : "active";
    const tbody = document.getElementById("itms-explorer-tbody");
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--ug-text-muted);">
        Fetching ${tabName} orders from ${itmsExplorerSource.toUpperCase()}...
    </td></tr>`;

    const sourceTag = document.getElementById("itms-explorer-source-tag");
    sourceTag.innerText = (itmsExplorerSource === "live") ? "Live ITMS" : "Local DB";
    sourceTag.className = (itmsExplorerSource === "live") ? "badge badge-yellow" : "badge badge-green";

    try {
        const url = `/api/itms/orders/?tab=${encodeURIComponent(tabName)}&source=${encodeURIComponent(itmsExplorerSource)}&page=${itmsExplorerPage}&search=${encodeURIComponent(itmsExplorerSearch)}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            itmsExplorerOrders = data.orders || [];
            renderItmsOrdersTable(itmsExplorerOrders);

            setText("itms-explorer-summary", data.summary || `Showing ${itmsExplorerOrders.length} orders`);
            setText("itms-explorer-page-label", `Page ${itmsExplorerPage}${data.total_pages ? ' of ' + data.total_pages : ''}`);
            document.getElementById("btn-explorer-prev").disabled = (itmsExplorerPage <= 1);
            document.getElementById("btn-explorer-next").disabled = !data.has_next_page;

            // Auto-select first order if available
            if (itmsExplorerOrders.length > 0) {
                selectItmsOrder(itmsExplorerOrders[0]);
            }
        } else {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:#f87171;">
                ${escapeHtml(data.error || "Failed loading orders from ITMS.")}
            </td></tr>`;
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:#f87171;">
            Connection error: ${escapeHtml(String(err))}
        </td></tr>`;
    }
}

function renderItmsOrdersTable(orders) {
    const tbody = document.getElementById("itms-explorer-tbody");
    if (!orders || orders.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:40px; color:var(--ug-text-dim);">
            No orders found matching criteria.
        </td></tr>`;
        return;
    }

    tbody.innerHTML = orders.map((o, idx) => {
        const orderNum = escapeHtml(o.order_number || "---");
        const regPlate = escapeHtml(o.registration_number || "---");
        const vin = escapeHtml(o.vin || "---");
        const status = escapeHtml(o.order_status || o.status || "Pending");
        const wh = escapeHtml(o.warehouse || o.warehouse_name || "---");
        const officer = escapeHtml(o.officer || o.installation_officer || "---");
        const date = escapeHtml(o.installation_date || "---");

        return `<tr id="itms-row-${idx}" onclick="selectItmsOrderAtIndex(${idx})">
            <td><strong style="color:#fff; font-family:var(--font-mono);">${orderNum}</strong></td>
            <td><span style="color:var(--ug-yellow); font-family:var(--font-mono); font-weight:800;">${regPlate}</span></td>
            <td><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${vin}</span></td>
            <td><span class="badge ${status.toLowerCase().includes('installed') ? 'badge-green' : 'badge-yellow'}">${status}</span></td>
            <td><span style="font-size:0.75rem; color:var(--ug-text-muted);">${wh}</span></td>
            <td><span style="font-size:0.75rem; color:var(--ug-text-muted);">${officer}</span></td>
            <td><span style="font-size:0.75rem; color:var(--ug-text-dim);">${date}</span></td>
        </tr>`;
    }).join("");
}

function selectItmsOrderAtIndex(idx) {
    if (itmsExplorerOrders[idx]) {
        selectItmsOrder(itmsExplorerOrders[idx]);
    }
}

async function selectItmsOrder(order) {
    selectedItmsOrder = order;

    // Highlight row
    const rows = document.querySelectorAll("#itms-explorer-tbody tr");
    rows.forEach(r => r.classList.remove("selected"));

    // Populate Inspector panel
    setText("itms-insp-plate", order.registration_number || "---");
    setText("itms-insp-order", `Order #${order.order_number || ''} • ${order.warehouse || order.warehouse_name || 'Uganda Hub'}`);

    // Fetch full order details including hardware & direct photo links
    try {
        const res = await fetch(`/api/itms/orders/${encodeURIComponent(order.order_number)}/?live=1`);
        const data = await res.json();
        if (data.success && data.order) {
            const o = data.order;
            const hw = o.hardware || {};
            setText("itms-insp-gps", hw.gps_tracker_id || "Not Fitted");
            setText("itms-insp-fbeacon", hw.front_beacon_id || "None");
            setText("itms-insp-rbeacon", hw.rear_beacon_id || "None");
            setText("itms-insp-fserial", hw.front_plate_serial || "---");
            setText("itms-insp-rserial", hw.rear_plate_serial || "---");

            // Evidence Photos (Direct Web Links - ZERO download required!)
            const photosGrid = document.getElementById("itms-insp-photos-grid");
            const photos = o.photos || [];

            if (photos.length === 0) {
                photosGrid.innerHTML = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:var(--ug-text-dim); font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                    No photos uploaded on ITMS for this order.
                </div>`;
            } else {
                photosGrid.innerHTML = photos.map(p => {
                    const lbl = escapeHtml(p.label || (p.orientation === 'FRONT' ? 'Front Plate' : 'Rear Plate'));
                    const url = escapeHtml(p.url || '');
                    return `<div class="photo-link-card">
                        <div class="photo-link-label">
                            <span>${p.orientation === 'FRONT' ? '🚘' : '🏍️'} ${lbl}</span>
                        </div>
                        <div class="photo-link-thumb" onclick="openLightbox('${url}')" title="Click for full view">
                            <img src="${url}" alt="${lbl}" onerror="this.onerror=null; this.parentElement.innerHTML='<span style=\\'color:var(--ug-text-dim); font-size:0.7rem;\\'>No Preview</span>';">
                        </div>
                        <div class="photo-link-footer">
                            <a href="${url}" target="_blank" rel="noopener noreferrer">Open Link ↗</a>
                        </div>
                    </div>`;
                }).join("");
            }

            // Verification Queue correlation
            const qBanner = document.getElementById("itms-insp-queue-banner");
            const qBtn = document.getElementById("btn-itms-jump-queue");
            if (o.matched_pair) {
                qBanner.style.display = "flex";
                qBtn.onclick = () => {
                    switchNavTab(4);
                    selectPair(o.matched_pair.id);
                };
            } else {
                qBanner.style.display = "none";
            }
        }
    } catch (err) {
        console.warn("Error fetching order detail:", err);
    }
}

async function syncCurrentItmsPage() {
    const tabName = (itmsCurrentSubview === "ARCHIVE") ? "archive" : "active";
    appendConsoleLog(`Starting live synchronization of ${tabName} orders...`, "info");
    try {
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `tab=${encodeURIComponent(tabName)}&pages=1`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            appendConsoleLog(`Sync complete: ${data.message}`, "success");
            fetchStats();
            fetchItmsExplorerOrders();
        } else {
            showToast(data.error || data.message || "Sync failed", "error");
        }
    } catch (err) {
        showToast("Sync error: " + err, "error");
    }
}

// ============================================================================
// TAB 3: Batches & Evidence Stream Controller (Full-Scale Accordion Stream)
// ============================================================================
let batchesData = [];
let batchDetailsCache = {};
let expandedBatches = new Set();
let batchSearchQuery = "";

function initDropzones() {
    ["front", "rear"].forEach(side => {
        const box = document.getElementById(`dropzone-tab-${side}`);
        if (!box) return;
        box.addEventListener("dragover", (e) => {
            e.preventDefault();
            box.classList.add("dragover");
        });
        box.addEventListener("dragleave", () => {
            box.classList.remove("dragover");
        });
        box.addEventListener("drop", (e) => {
            e.preventDefault();
            box.classList.remove("dragover");
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                if (side === "front") {
                    tabSelectedFrontFiles = Array.from(e.dataTransfer.files);
                    setText("count-tab-front", `${tabSelectedFrontFiles.length} files selected`);
                } else {
                    tabSelectedRearFiles = Array.from(e.dataTransfer.files);
                    setText("count-tab-rear", `${tabSelectedRearFiles.length} files selected`);
                }
                validateTabUploadSymmetry();
            }
        });
    });
}

async function fetchBatchesList() {
    const container = document.getElementById("batches-stream-container");
    const countBadge = document.getElementById("batches-count-badge");
    try {
        const res = await fetch("/api/batches/");
        const data = await res.json();
        if (data.success && data.batches) {
            batchesData = data.batches;
            if (countBadge) countBadge.innerText = `${batchesData.length} Batches`;

            if (batchesData.length === 0) {
                if (container) {
                    container.innerHTML = `
                        <div class="batches-empty-state">
                            <span style="font-size:2.5rem;">📦</span>
                            <h4 style="color:#fff; font-size:1.1rem; margin-top:8px;">No Batches Ingested Yet</h4>
                            <p style="font-size:0.8rem; max-width:400px; margin-top:4px;">Upload photos from the field to start processing motorcycle pairs.</p>
                            <button class="btn btn-primary" style="margin-top:12px;" onclick="openBatchUploadModal()">
                                📷 + New Upload Batch
                            </button>
                        </div>
                    `;
                }
                return;
            }

            // Auto-expand first batch if set is empty
            if (expandedBatches.size === 0 && batchesData.length > 0) {
                expandedBatches.add(batchesData[0].batch_id);
            }

            renderBatchesStream();

            // Load details for any currently expanded batches
            expandedBatches.forEach(bId => {
                loadBatchDetailsAndRender(bId);
            });
        }
    } catch (err) {
        console.warn("Batches list fetch error:", err);
    }
}

function filterBatchesStream() {
    const input = document.getElementById("batch-search-input");
    batchSearchQuery = (input ? input.value : "").trim().toLowerCase();
    renderBatchesStream();
}

function renderBatchesStream() {
    const container = document.getElementById("batches-stream-container");
    if (!container) return;

    const filtered = batchesData.filter(b => {
        if (!batchSearchQuery) return true;
        const bId = (b.batch_id || "").toLowerCase();
        const label = (b.source_label || "").toLowerCase();
        const src = (b.source_type || "").toLowerCase();
        return bId.includes(batchSearchQuery) || label.includes(batchSearchQuery) || src.includes(batchSearchQuery);
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="batches-empty-state">
                <span style="font-size:2.2rem;">🔍</span>
                <h4 style="color:#fff; font-size:1rem; margin-top:8px;">No batches matching '${escapeHtml(batchSearchQuery)}'</h4>
                <p style="font-size:0.75rem; color:var(--ug-text-muted);">Try a different batch ID or shift label.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = filtered.map(b => {
        const isExp = expandedBatches.has(b.batch_id);
        const dupBadge = b.duplicate_count > 0 ? `
            <span class="batch-pill pill-dup">⚠️ <strong>${b.duplicate_count}</strong> Duplicates</span>
        ` : '';

        return `
            <div class="batch-stream-card ${isExp ? 'is-expanded' : ''}" id="batch-stream-${escapeHtml(b.batch_id)}">
                <div class="batch-stream-header" onclick="toggleBatchAccordion('${escapeHtml(b.batch_id)}')">
                    <div class="batch-header-left">
                        <span class="batch-chevron" id="batch-chevron-${escapeHtml(b.batch_id)}">${isExp ? '▼' : '▶'}</span>
                        <span class="batch-id-text">${escapeHtml(b.batch_id)}</span>
                        <span class="badge badge-yellow">${escapeHtml(b.source_type || 'WEB')}</span>
                        <span class="batch-label-text">${escapeHtml(b.source_label || 'Ingestion Session')}</span>
                        <span class="batch-time-text">📅 ${escapeHtml(b.created_at || '--')}</span>
                    </div>
                    <div class="batch-header-right">
                        <div class="batch-pill-group">
                            <span class="batch-pill pill-total">Total: <strong>${b.total_files}</strong></span>
                            <span class="batch-pill pill-ingested">✓ <strong>${b.ingested_count}</strong> Ingested</span>
                            ${dupBadge}
                        </div>
                        <button class="btn btn-secondary batch-toggle-btn" id="batch-toggle-btn-${escapeHtml(b.batch_id)}" onclick="event.stopPropagation(); toggleBatchAccordion('${escapeHtml(b.batch_id)}')">
                            ${isExp ? '▲ Minimize' : `▼ Expand Photos (${b.ingested_count})`}
                        </button>
                    </div>
                </div>
                <div class="batch-stream-body" id="batch-body-${escapeHtml(b.batch_id)}" style="display:${isExp ? 'block' : 'none'};">
                    <div id="batch-photos-${escapeHtml(b.batch_id)}">
                        <div style="padding:20px; text-align:center; color:var(--ug-text-dim);">
                            Loading photos for ${escapeHtml(b.batch_id)}...
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

async function toggleBatchAccordion(batchId) {
    const isExp = expandedBatches.has(batchId);
    if (isExp) {
        expandedBatches.delete(batchId);
    } else {
        expandedBatches.add(batchId);
    }

    const card = document.getElementById(`batch-stream-${batchId}`);
    const body = document.getElementById(`batch-body-${batchId}`);
    const chevron = document.getElementById(`batch-chevron-${batchId}`);
    const toggleBtn = document.getElementById(`batch-toggle-btn-${batchId}`);

    if (card) card.classList.toggle("is-expanded", !isExp);
    if (chevron) chevron.innerText = !isExp ? '▼' : '▶';

    if (isExp) {
        // Minimize
        if (body) body.style.display = "none";
        const bInfo = batchesData.find(b => b.batch_id === batchId);
        if (toggleBtn) toggleBtn.innerText = `▼ Expand Photos (${bInfo ? bInfo.ingested_count : ''})`;
    } else {
        // Expand
        if (body) body.style.display = "block";
        if (toggleBtn) toggleBtn.innerText = "▲ Minimize";
        await loadBatchDetailsAndRender(batchId);
    }
}

function expandAllBatches() {
    batchesData.forEach(b => expandedBatches.add(b.batch_id));
    renderBatchesStream();
    batchesData.forEach(b => loadBatchDetailsAndRender(b.batch_id));
}

function collapseAllBatches() {
    expandedBatches.clear();
    renderBatchesStream();
}

async function loadBatchDetailsAndRender(batchId) {
    const container = document.getElementById(`batch-photos-${batchId}`);
    if (!container) return;

    if (batchDetailsCache[batchId]) {
        renderFullScaleBatchPhotos(batchId, batchDetailsCache[batchId]);
        return;
    }

    try {
        const res = await fetch(`/api/batches/${encodeURIComponent(batchId)}/`);
        const data = await res.json();
        if (data.success && data.images) {
            batchDetailsCache[batchId] = data.images;
            renderFullScaleBatchPhotos(batchId, data.images);
        } else {
            container.innerHTML = `<div style="padding:20px; text-align:center; color:var(--ug-text-dim);">No photos found in batch.</div>`;
        }
    } catch (err) {
        console.warn(`Failed loading batch ${batchId}:`, err);
        container.innerHTML = `<div style="padding:20px; text-align:center; color:var(--ug-red);">Failed to load photos.</div>`;
    }
}

function renderFullScaleBatchPhotos(batchId, images) {
    const container = document.getElementById(`batch-photos-${batchId}`);
    if (!container) return;

    if (!images || images.length === 0) {
        container.innerHTML = `<div style="padding:24px; text-align:center; color:var(--ug-text-dim);">No photos recorded in this batch.</div>`;
        return;
    }

    container.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.07);">
            <span style="font-size:0.78rem; font-weight:700; color:var(--ug-text-muted); text-transform:uppercase;">
                📷 Evidence Photos (${images.length} images)
            </span>
            <span style="font-size:0.72rem; color:var(--ug-yellow); display:inline-flex; align-items:center; gap:4px;">
                ↕ Scroll down to inspect all photos
            </span>
        </div>
        <div class="batch-full-photos-grid">
            ${images.map(img => {
                const orientTag = img.orientation === "FRONT" ? "tag-front" : (img.orientation === "REAR" ? "tag-rear" : "tag-unknown");
                const plateHtml = img.detected_plate ? `
                    <div class="batch-photo-plate-overlay">
                        <span>🔍 ${escapeHtml(img.detected_plate)}</span>
                        ${img.ocr_confidence !== null ? `<span style="font-size:0.75rem; color:#fff; font-weight:normal;">(${img.ocr_confidence}%)</span>` : ''}
                    </div>
                ` : `
                    <div class="batch-photo-plate-overlay" style="border-color:rgba(255,255,255,0.2); color:var(--ug-text-dim);">
                        <span>No OCR Plate</span>
                    </div>
                `;

                const pairHtml = img.pair ? `
                    <div class="batch-photo-pair-strip">
                        <div>
                            <div style="color:var(--ug-green); font-weight:700; font-size:0.8rem;">🔗 Pair #${img.pair.pair_id} (${escapeHtml(img.pair.role)})</div>
                            <div style="color:#fff; font-weight:700; font-size:0.78rem;">Detected: ${escapeHtml(img.pair.detected_plate || '—')}</div>
                            <div style="color:var(--ug-text-dim); font-size:0.72rem;">Order: ${img.pair.order_number ? '#' + escapeHtml(img.pair.order_number) : 'Unlinked'}</div>
                        </div>
                        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="jumpToPairInQueue(${img.pair.pair_id})">
                            🔍 Inspect in Queue
                        </button>
                    </div>
                ` : `
                    <div class="batch-photo-pair-strip" style="border-color:rgba(245, 158, 11, 0.3);">
                        <div>
                            <div style="color:var(--ug-yellow); font-weight:700; font-size:0.78rem;">⚠️ Unassigned Evidence Photo</div>
                            <div style="color:var(--ug-text-dim); font-size:0.7rem;">Click 'Match Pairs [M]' on Dashboard to associate.</div>
                        </div>
                    </div>
                `;

                return `
                    <div class="batch-photo-card-large">
                        <div class="batch-photo-full-wrap" onclick="openLightbox('${escapeHtml(img.url)}')">
                            <img class="batch-photo-full-img" src="${escapeHtml(img.url)}" alt="${escapeHtml(img.file_name)}" onerror="this.src='/static/core/placeholder.svg';">
                            <span class="batch-photo-orient-badge ${orientTag}">${img.orientation}</span>
                            ${plateHtml}
                            <span class="batch-photo-zoom-hint">🔍 Click to zoom</span>
                        </div>
                        <div class="batch-photo-details-strip">
                            <div class="batch-photo-meta-row">
                                <span style="font-family:var(--font-mono); font-size:0.75rem; color:#fff; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:260px;" title="${escapeHtml(img.file_name)}">
                                    📁 ${escapeHtml(img.file_name)}
                                </span>
                                <span>${img.file_size_kb} KB • ${escapeHtml(img.captured_at || 'No timestamp')}</span>
                            </div>
                            ${pairHtml}
                        </div>
                    </div>
                `;
            }).join("")}
        </div>
    `;
}

function openBatchUploadModal() {
    tabSelectedFrontFiles = [];
    tabSelectedRearFiles = [];
    setText("count-tab-front", "0 files selected");
    setText("count-tab-rear", "0 files selected");
    const labelInput = document.getElementById("batch-upload-label");
    if (labelInput) labelInput.value = "";
    const warn = document.getElementById("tab-symmetry-warning");
    if (warn) warn.style.display = "none";
    openModal("modal-batch-upload");
}

function jumpToPairInQueue(pairId) {
    switchNavTab(4);
    setTimeout(() => {
        selectPair(pairId);
    }, 150);
}

function handleTabFileSelect(side) {
    const input = document.getElementById(`input-tab-${side}`);
    if (side === "front") {
        tabSelectedFrontFiles = Array.from(input.files);
        setText("count-tab-front", `${tabSelectedFrontFiles.length} files selected`);
    } else {
        tabSelectedRearFiles = Array.from(input.files);
        setText("count-tab-rear", `${tabSelectedRearFiles.length} files selected`);
    }
    validateTabUploadSymmetry();
}

function validateTabUploadSymmetry() {
    const fCount = tabSelectedFrontFiles.length;
    const rCount = tabSelectedRearFiles.length;
    const banner = document.getElementById("tab-symmetry-warning");
    if (!banner) return;

    if (fCount > 0 && rCount > 0) {
        banner.style.display = "flex";
        if (fCount === rCount) {
            banner.style.backgroundColor = "rgba(16, 185, 129, 0.15)";
            banner.style.color = "#34d399";
            banner.innerText = `✓ Perfect 1:1 symmetry: ${fCount} Fronts and ${rCount} Rears.`;
        } else {
            banner.style.backgroundColor = "rgba(252, 220, 4, 0.15)";
            banner.style.color = "var(--ug-yellow)";
            const diff = Math.abs(fCount - rCount);
            const missing = fCount > rCount ? "Rear" : "Front";
            banner.innerText = `⚠️ Asymmetric batch: ${fCount} Fronts vs ${rCount} Rears (${diff} missing ${missing}).`;
        }
    } else {
        banner.style.display = "none";
    }
}

async function submitTabBatchUpload() {
    const total = tabSelectedFrontFiles.length + tabSelectedRearFiles.length;
    if (total === 0) {
        showToast("Please select photos to upload", "error");
        return;
    }

    const formData = new FormData();
    const labelInput = document.getElementById("batch-upload-label");
    formData.append("batch_label", (labelInput ? labelInput.value.trim() : "") || "Web Console Upload");
    tabSelectedFrontFiles.forEach(f => formData.append("front_photos", f));
    tabSelectedRearFiles.forEach(f => formData.append("rear_photos", f));

    const btn = document.getElementById("btn-submit-tab-upload");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "Ingesting batch into vault...";
    }

    try {
        const res = await fetch("/api/upload/", { method: "POST", body: formData });
        const data = await res.json();
        if (data.success) {
            closeModal("modal-batch-upload");
            showToast(`Batch ${data.batch_id}: ${data.ingested_count} photos ingested into evidence vault.`, "success");
            appendConsoleLog(`Ingestion complete: ${data.ingested_count} photos in batch ${data.batch_id}.`, "success");
            // Clear selections
            tabSelectedFrontFiles = [];
            tabSelectedRearFiles = [];
            setText("count-tab-front", "0 files selected");
            setText("count-tab-rear", "0 files selected");
            const warn = document.getElementById("tab-symmetry-warning");
            if (warn) warn.style.display = "none";
            // Clear cache and auto-expand the new batch
            batchDetailsCache = {};
            expandedBatches.add(data.batch_id);
            fetchStats();
            fetchBatchesList();
            triggerPipeline("full_pipeline");
        } else {
            showToast(data.error || "Upload failed", "error");
        }
    } catch (err) {
        showToast("Upload error: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "Upload & Ingest to Vault";
        }
    }
}

// ============================================================================
// TAB 4: Review Queue & Operator Studio Controller
// ============================================================================
async function fetchPairs() {
    try {
        const res = await fetch("/api/pairs/");
        const data = await res.json();
        if (data.success) {
            pairsData = data.pairs || [];
            renderQueueCards();
            if (pairsData.length > 0 && !selectedPairId) {
                selectPair(pairsData[0].id);
            }
        }
    } catch (err) {
        console.warn("Queue fetch error:", err);
    }
}

function setWorkQueueFilter(filter) {
    currentQueueFilter = filter;
    document.querySelectorAll(".queue-filter-pill").forEach(p => {
        p.classList.toggle("active", p.getAttribute("data-filter") === filter);
    });
    renderQueueCards();
}

function filterWorkQueue() {
    renderQueueCards();
}

function renderQueueCards() {
    const container = document.getElementById("queue-cards-container");
    const searchVal = (document.getElementById("queue-search-input").value || "").trim().toUpperCase();

    const filtered = pairsData.filter(p => {
        // Tab filter
        if (currentQueueFilter === "PENDING" && p.verification_status !== "PENDING_REVIEW") return false;
        if (currentQueueFilter === "APPROVED" && p.verification_status !== "APPROVED") return false;
        if (currentQueueFilter === "SUBMITTED" && p.verification_status !== "SUBMITTED") return false;
        if (currentQueueFilter === "ISSUES" && !["INCOMPLETE", "CONFLICT", "FAILED", "UNREGISTERED"].includes(p.verification_status)) return false;

        // Search text
        if (searchVal) {
            const plate = (p.registration_number_detected || "").toUpperCase();
            const orderNo = (p.order_number || "").toUpperCase();
            return plate.includes(searchVal) || orderNo.includes(searchVal);
        }
        return true;
    });

    if (filtered.length === 0) {
        container.innerHTML = `<div style="text-align:center; padding:40px; color:var(--ug-text-dim);">No pairs match criteria</div>`;
        return;
    }

    container.innerHTML = filtered.map(p => {
        const isSelected = p.id === selectedPairId;
        const plate = escapeHtml(p.registration_number_detected || "NO PLATE");
        const status = p.verification_status || "PENDING";
        const orderNo = p.order_number ? `#${p.order_number}` : "No order";

        return `<div class="queue-item-card ${isSelected ? 'selected' : ''}" onclick="selectPair(${p.id})">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">
                <span class="item-plate-text">${plate}</span>
                <span class="badge badge-yellow" style="font-size:0.65rem;">${status}</span>
            </div>
            <div style="display:flex; align-items:center; justify-content:space-between; font-size:0.75rem; color:var(--ug-text-dim);">
                <span>${escapeHtml(orderNo)}</span>
                <span>${p.has_front && p.has_rear ? '✓ 1:1' : '⚠️ Missing'}</span>
            </div>
        </div>`;
    }).join("");
}

function navigateQueue(delta) {
    if (!pairsData.length) return;
    const currentIndex = pairsData.findIndex(p => p.id === selectedPairId);
    let nextIndex = currentIndex + delta;
    if (nextIndex < 0) nextIndex = 0;
    if (nextIndex >= pairsData.length) nextIndex = pairsData.length - 1;
    selectPair(pairsData[nextIndex].id);
}

async function selectPair(pairId) {
    selectedPairId = pairId;
    renderQueueCards();

    try {
        const res = await fetch(`/api/pairs/${pairId}/`);
        const data = await res.json();
        if (data.success && data.pair) {
            selectedPairDetail = data.pair;
            const p = data.pair;

            document.getElementById("queue-studio-empty").style.display = "none";
            document.getElementById("queue-studio-content").style.display = "flex";

            setText("qdetail-plate", p.registration_number_detected || "---");
            setText("qdetail-status", p.verification_status);

            const deltaSec = p.time_delta_sec ?? p.timestamp_delta_seconds;
            setText("qdetail-delta", `Δt: ${deltaSec !== null && deltaSec !== undefined ? deltaSec.toFixed(1) + 's' : 'N/A'}`);

            // Matched Order in header banner
            const orderEl = document.getElementById("qdetail-order-no");
            const btnQuickUnlink = document.getElementById("btn-quick-unlink-order");
            const btnQuickLink = document.getElementById("btn-quick-link-order");
            if (p.order) {
                if (orderEl) orderEl.innerText = `#${p.order.order_number} (${p.order.registration_number})`;
                if (btnQuickUnlink) btnQuickUnlink.style.display = "inline-flex";
                if (btnQuickLink) btnQuickLink.innerText = "🔗 Change Order";
            } else {
                if (orderEl) orderEl.innerText = "No matched order";
                if (btnQuickUnlink) btnQuickUnlink.style.display = "none";
                if (btnQuickLink) btnQuickLink.innerText = "🔗 Link Order";
            }

            // Evidence photos
            const front = p.front || p.front_image;
            const rear = p.rear || p.rear_image;

            const imgFront = document.getElementById("qimg-front");
            const imgRear = document.getElementById("qimg-rear");
            const phFront = document.getElementById("qfront-placeholder");
            const phRear = document.getElementById("qrear-placeholder");

            if (front && front.url) {
                imgFront.src = front.url;
                imgFront.style.display = "block";
                if (phFront) phFront.style.display = "none";
            } else {
                imgFront.src = "";
                imgFront.style.display = "none";
                if (phFront) phFront.style.display = "flex";
            }

            if (rear && rear.url) {
                imgRear.src = rear.url;
                imgRear.style.display = "block";
                if (phRear) phRear.style.display = "none";
            } else {
                imgRear.src = "";
                imgRear.style.display = "none";
                if (phRear) phRear.style.display = "flex";
            }

            setText("qfront-timestamp", front && front.captured_at ? front.captured_at : "--:--:--");
            setText("qrear-timestamp", rear && rear.captured_at ? rear.captured_at : "--:--:--");

            setText("qfront-ocr-text", front && front.detected_plate ? front.detected_plate : "---");
            const fConf = front ? (typeof front.ocr_confidence === 'number' ? front.ocr_confidence.toFixed(0) + "%" : "--%") : "--%";
            setText("qfront-ocr-conf", fConf);

            setText("qrear-ocr-text", rear && rear.detected_plate ? rear.detected_plate : "---");
            const rConf = rear ? (typeof rear.ocr_confidence === 'number' ? rear.ocr_confidence.toFixed(0) + "%" : "--%") : "--%";
            setText("qrear-ocr-conf", rConf);

            // Comparison table
            setText("qcomp-detected", p.registration_number_detected || "---");
            setText("qcomp-order-plate", p.order ? p.order.registration_number : "---");
            setText("qcomp-score", p.match_score !== null && p.match_score !== undefined ? `${p.match_score.toFixed(0)}% (${p.match_type})` : "N/A");
            setText("qcomp-vin", p.order ? (p.order.vin || "—") : "---");
            setText("qcomp-warehouse", p.order ? (p.order.warehouse_name || "Uganda Hub") : "Uganda Hub");
            setText("qcomp-officer", p.order ? (p.order.installation_officer || "Station Officer") : "Station Officer");
            setText("qcomp-complete", p.is_complete ? "1:1 Symmetric" : "Missing Angle");
            setText("qcomp-strategy", p.matched_via || "Joint Vision OCR");
        }
    } catch (err) {
        console.warn("Error selecting pair:", err);
    }
}

async function executePairAction(action) {
    if (!selectedPairId) return;
    try {
        const res = await fetch(`/api/pairs/${selectedPairId}/action/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `action=${encodeURIComponent(action)}`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            fetchStats();
            fetchPairs();
            selectPair(selectedPairId);
        } else {
            showToast(data.error || "Action failed", "error");
        }
    } catch (err) {
        showToast("Error: " + err, "error");
    }
}

function openEditPlateModal() {
    if (!selectedPairDetail) {
        showToast("Please select a pair first", "warning");
        return;
    }
    document.getElementById("input-edit-plate").value = selectedPairDetail.registration_number_detected || "";
    const statusSelect = document.getElementById("select-edit-status");
    if (statusSelect) statusSelect.value = selectedPairDetail.verification_status || "";
    const noteInput = document.getElementById("input-edit-note");
    if (noteInput) noteInput.value = selectedPairDetail.operator_note || "";

    openModal("modal-edit-plate");
    setTimeout(() => document.getElementById("input-edit-plate").focus(), 100);
}

async function submitEditPlate() {
    if (!selectedPairId) return;
    const plate = document.getElementById("input-edit-plate").value.trim();
    const status = document.getElementById("select-edit-status").value;
    const note = document.getElementById("input-edit-note").value.trim();

    try {
        const bodyParams = new URLSearchParams();
        bodyParams.append("action", "manual_override");
        if (plate) bodyParams.append("plate", plate);
        if (status) bodyParams.append("verification_status", status);
        bodyParams.append("operator_note", note);

        const res = await fetch(`/api/pairs/${selectedPairId}/action/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: bodyParams.toString(),
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            closeModal("modal-edit-plate");
            fetchStats();
            fetchPairs();
            selectPair(selectedPairId);
        } else {
            showToast(data.error || "Failed updating pair override", "error");
        }
    } catch (err) {
        showToast("Error: " + err, "error");
    }
}

function openLinkOrderModal() {
    if (!selectedPairId) {
        showToast("Please select a pair first", "warning");
        return;
    }
    const currentText = document.getElementById("linker-current-order-text");
    const unlinkBtn = document.getElementById("btn-modal-unlink");
    if (selectedPairDetail && selectedPairDetail.order) {
        currentText.innerHTML = `<strong style="color:var(--ug-yellow);">#${escapeHtml(selectedPairDetail.order.order_number)}</strong> (${escapeHtml(selectedPairDetail.order.registration_number)}) • VIN: ${escapeHtml(selectedPairDetail.order.vin || '—')}`;
        if (unlinkBtn) unlinkBtn.style.display = "inline-flex";
    } else {
        currentText.innerText = "No order currently linked";
        if (unlinkBtn) unlinkBtn.style.display = "none";
    }

    openModal("modal-link-order");
    const searchInput = document.getElementById("input-search-order");
    if (searchInput) {
        searchInput.value = "";
        setTimeout(() => searchInput.focus(), 100);
    }
    searchOrdersForLinking("");
}

async function unlinkOrderFromSelectedPair() {
    if (!selectedPairId) return;
    try {
        const res = await fetch(`/api/pairs/${selectedPairId}/action/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "action=unlink_order",
        });
        const data = await res.json();
        if (data.success) {
            showToast("Order unlinked successfully.", "info");
            closeModal("modal-link-order");
            fetchStats();
            fetchPairs();
            selectPair(selectedPairId);
        } else {
            showToast(data.error || "Failed to unlink order", "error");
        }
    } catch (err) {
        showToast("Unlink error: " + err, "error");
    }
}

async function searchOrdersForLinking(q) {
    const container = document.getElementById("linker-orders-list");
    try {
        const res = await fetch(`/api/orders/?search=${encodeURIComponent(q)}&limit=50`);
        const data = await res.json();
        if (data.success && data.orders) {
            if (data.orders.length === 0) {
                container.innerHTML = `<div style="padding:24px; text-align:center; color:var(--ug-text-dim);">No active orders matching '${escapeHtml(q)}'.</div>`;
                return;
            }
            container.innerHTML = `
                <table class="itms-grid-table">
                    <thead>
                        <tr>
                            <th style="width:140px;">Order #</th>
                            <th style="width:120px;">Plate</th>
                            <th style="width:140px;">VIN / Chassis</th>
                            <th style="width:100px;">Status</th>
                            <th>Warehouse</th>
                            <th style="width:85px; text-align:right;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.orders.map(o => `
                            <tr>
                                <td><strong style="color:var(--ug-yellow); font-family:var(--font-mono);">${escapeHtml(o.order_number)}</strong></td>
                                <td><strong style="color:#fff;">${escapeHtml(o.registration_number)}</strong></td>
                                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${escapeHtml(o.vin || '—')}</td>
                                <td><span class="badge badge-muted">${escapeHtml(o.status || 'Active')}</span></td>
                                <td style="font-size:0.78rem; color:var(--ug-text-dim);">${escapeHtml(o.warehouse_name || '—')}</td>
                                <td style="text-align:right;">
                                    <button class="btn btn-primary" style="padding:3px 10px; font-size:0.75rem;" onclick="linkOrderToPair(${o.id})">
                                        Link ➜
                                    </button>
                                </td>
                            </tr>
                        `).join("")}
                    </tbody>
                </table>
            `;
        }
    } catch (err) {
        console.warn("Linker search error:", err);
    }
}

async function linkOrderToPair(orderId) {
    try {
        const res = await fetch(`/api/pairs/${selectedPairId}/action/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `action=link_order&order_id=${orderId}`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            closeModal("modal-link-order");
            fetchStats();
            fetchPairs();
            selectPair(selectedPairId);
        } else {
            showToast(data.error || "Linking failed", "error");
        }
    } catch (err) {
        showToast("Linking error: " + err, "error");
    }
}

// ============================================================================
// TAB 5: History & Audit Events Controller
// ============================================================================
let historyItemsMap = {};
let activeHistoryLog = null;

function setHistoryFilter(filter) {
    currentHistoryFilter = filter;
    document.querySelectorAll(".history-chip").forEach(c => {
        c.classList.toggle("active", c.getAttribute("data-hfilter") === filter);
    });
    fetchHistoryEvents();
}

async function fetchHistoryEvents() {
    const tbody = document.getElementById("history-table-tbody");
    try {
        const res = await fetch(`/api/history/?filter=${encodeURIComponent(currentHistoryFilter)}`);
        const data = await res.json();
        if (data.success && data.items) {
            historyItemsMap = {};
            data.items.forEach(item => {
                historyItemsMap[item.id] = item;
            });

            if (data.items.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:40px; color:var(--ug-text-dim);">No events matching '${currentHistoryFilter}'.</td></tr>`;
                return;
            }
            tbody.innerHTML = data.items.map(l => `
                <tr onclick="openHistoryModal(${l.id})" style="cursor:pointer;" title="Click to view complete audit log">
                    <td style="font-size:0.75rem; color:var(--ug-text-dim); font-family:var(--font-mono);">${escapeHtml(l.timestamp)}</td>
                    <td><strong style="color:var(--ug-yellow); font-family:var(--font-mono);">${escapeHtml(l.plate)}</strong></td>
                    <td><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${escapeHtml(l.order_number)}</span></td>
                    <td><span class="badge badge-muted">${escapeHtml(l.action)}</span></td>
                    <td><span class="badge ${l.result === 'SUCCESS' ? 'badge-green' : 'badge-red'}">${escapeHtml(l.result)}</span></td>
                    <td style="font-size:0.8rem; color:var(--ug-text-muted);">${escapeHtml(l.operator)}</td>
                    <td style="font-size:0.8rem; color:#fff; max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(l.message)}</td>
                </tr>
            `).join("");
        }
    } catch (err) {
        console.warn("History fetch error:", err);
    }
}

function openHistoryModal(logId) {
    const log = historyItemsMap[logId];
    if (!log) return;
    activeHistoryLog = log;

    setText("hist-modal-title", `📜 Verification Activity Log #${log.id}`);
    setText("hist-modal-time", log.timestamp || "--");
    setText("hist-modal-operator", log.operator || "Operator");

    const resultBadge = document.getElementById("hist-modal-result");
    if (resultBadge) {
        resultBadge.className = log.result === "SUCCESS" ? "badge badge-green" : "badge badge-red";
        resultBadge.innerText = log.result;
    }

    setText("hist-modal-action", log.action || "--");
    setText("hist-modal-plate", log.plate || "--");
    setText("hist-modal-order", log.order_number && log.order_number !== "N/A" ? `#${log.order_number} (${log.order_plate || ''})` : "Unlinked");
    setText("hist-modal-pair", log.pair_id ? `Pair #${log.pair_id} (${log.pair_status || ''})` : "None");
    setText("hist-modal-token", log.simulated_token || "None (Standard Verification)");
    setText("hist-modal-message", log.message || "--");

    // Photos preview
    const photosContainer = document.getElementById("hist-modal-photos-container");
    const imgF = document.getElementById("hist-img-front");
    const imgR = document.getElementById("hist-img-rear");
    if (photosContainer && (log.front_url || log.rear_url)) {
        photosContainer.style.display = "flex";
        if (imgF) imgF.src = log.front_url || "";
        if (imgR) imgR.src = log.rear_url || "";
    } else if (photosContainer) {
        photosContainer.style.display = "none";
    }

    // Jump button
    const jumpBtn = document.getElementById("btn-hist-jump-queue");
    if (jumpBtn) {
        jumpBtn.style.display = log.pair_id ? "inline-flex" : "none";
    }

    openModal("modal-history-detail");
}

function jumpFromHistoryToQueue() {
    if (!activeHistoryLog || !activeHistoryLog.pair_id) return;
    const pairId = activeHistoryLog.pair_id;
    closeModal("modal-history-detail");
    jumpToPairInQueue(pairId);
}

// ============================================================================
// TAB 6: Settings Controller
// ============================================================================
async function toggleDryRun() {
    try {
        const res = await fetch("/api/settings/toggle-dry-run/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            const isDry = data.dry_run ?? data.dry_run_mode;
            showToast(data.message, "info");
            updateDryRunBadges(isDry);
            fetchStats();
        }
    } catch (err) {
        showToast("Failed to toggle mode: " + err, "error");
    }
}

function updateDryRunBadges(isDry) {
    const chk = document.getElementById("settings-dryrun-toggle");
    if (chk) chk.checked = Boolean(isDry);

    const badge = document.getElementById("settings-dryrun-badge");
    if (badge) {
        badge.className = isDry ? "badge badge-yellow" : "badge badge-red";
        badge.innerText = isDry ? "● Simulation Mode (Safe)" : "● LIVE ITMS SERVER";
    }

    const headerBadge = document.getElementById("header-dryrun-badge");
    if (headerBadge) {
        headerBadge.className = isDry ? "badge badge-yellow" : "badge badge-red";
        headerBadge.innerText = isDry ? "● Dry Run" : "● Live";
    }
}

// ============================================================================
// Background Pipeline & Activity Console Logger
// ============================================================================
async function triggerPipeline(taskType = "full_pipeline") {
    appendConsoleLog(`Starting background task '${taskType}'...`, "info");
    try {
        const res = await fetch("/api/pipeline/run/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `task_type=${encodeURIComponent(taskType)}`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "info");
            startPipelinePolling();
        } else {
            showToast(data.message || "Failed to start pipeline", "warning");
        }
    } catch (err) {
        showToast("Pipeline error: " + err, "error");
    }
}

async function triggerMatching() {
    triggerPipeline("matcher");
}

async function triggerSyncOrders() {
    appendConsoleLog("Triggered order registry sync from ITMS WebApp...", "info");
    try {
        const res = await fetch("/api/orders/sync/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "info");
            startPipelinePolling();
        }
    } catch (err) {
        showToast("Sync error: " + err, "error");
    }
}

async function triggerBatchSubmit() {
    if (!confirm("Are you sure you want to submit all APPROVED pairs to ITMS?")) return;
    appendConsoleLog("Submitting all approved pairs to ITMS...", "info");
    try {
        const res = await fetch("/api/submissions/batch/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            appendConsoleLog(data.message, "success");
            fetchStats();
            fetchPairs();
        } else {
            showToast(data.message || "Batch submission failed", "warning");
        }
    } catch (err) {
        showToast("Submission error: " + err, "error");
    }
}

function startPipelinePolling() {
    if (pipelinePollInterval) clearInterval(pipelinePollInterval);
    pipelinePollInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/pipeline/status/");
            const st = await res.json();
            if (st.running) {
                appendConsoleLog(`Stage: ${st.stage || 'Processing'} (${st.progress || 0}%) - ${st.message || ''}`, "info");
            } else {
                clearInterval(pipelinePollInterval);
                pipelinePollInterval = null;
                appendConsoleLog(`Pipeline task finished: ${st.message || 'Complete'}`, st.success ? "success" : "error");
                fetchStats();
                fetchPairs();
            }
        } catch (err) {
            console.warn("Polling error:", err);
        }
    }, 1500);
}

function appendConsoleLog(message, type = "info") {
    const stream = document.getElementById("activity-stream");
    if (!stream) return;
    const timeStr = new Date().toTimeString().split(" ")[0];
    const div = document.createElement("div");
    div.className = "stream-line";
    div.innerHTML = `<span class="stream-time">[${timeStr}]</span> <span class="stream-${type}">${escapeHtml(message)}</span>`;
    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
}

function toggleActivityConsole() {
    const drawer = document.getElementById("activity-drawer");
    if (drawer) drawer.classList.toggle("collapsed");
}

// ============================================================================
// Utilities & Modals
// ============================================================================
function openModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.add("active");
}

function closeModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.remove("active");
}

function openPhotoModal(side) {
    if (!selectedPairDetail) return;
    const img = (side === "front") ? (selectedPairDetail.front || selectedPairDetail.front_image) : (selectedPairDetail.rear || selectedPairDetail.rear_image);
    if (img && img.url) {
        openLightbox(img.url);
    }
}

function openLightbox(url) {
    const img = document.getElementById("lightbox-img");
    if (img) img.src = url;
    openModal("modal-photo-lightbox");
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 250);
    }, 3500);
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
