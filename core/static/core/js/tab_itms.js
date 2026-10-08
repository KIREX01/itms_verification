/**
 * ITMS CLOSING SYSTEM - TAB 2: ITMS CONNECT & ORDERS/ARCHIVE EXPLORER
 * Handles stock.itms.ug authentication, order registry browsing, telemetry inspection,
 * and direct web link rendering of installation evidence photos.
 */

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
            const modalBadge = document.getElementById("itms-modal-badge");
            const modalUser = document.getElementById("itms-modal-user");
            const modalDetail = document.getElementById("itms-modal-detail");
            const modalBtnDisconnect = document.getElementById("btn-itms-disconnect");

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
                if (modalBadge) { modalBadge.className = "badge badge-green"; modalBadge.innerText = "● Connected"; }
                if (modalUser) modalUser.innerText = st.user_email || "Connected Operator";
                if (modalDetail) modalDetail.innerText = `Session active on ${st.url || "stock.itms.ug"}.`;
                if (modalBtnDisconnect) modalBtnDisconnect.style.display = "inline-block";
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
                if (modalBadge) { modalBadge.className = "badge badge-yellow"; modalBadge.innerText = "● Disconnected"; }
                if (modalUser) modalUser.innerText = "No Account Connected";
                if (modalDetail) modalDetail.innerText = "Connect your ITMS portal credentials.";
                if (modalBtnDisconnect) modalBtnDisconnect.style.display = "none";
            }

            if (data.active_bond) {
                const bondBadge = document.getElementById("itms-active-bond-badge");
                if (bondBadge) bondBadge.innerText = `${data.active_bond.name} (${data.active_bond.code})`;
                const sel = document.getElementById("itms-select-bond");
                if (sel && sel.value !== data.active_bond.code) {
                    sel.value = data.active_bond.code;
                }
            }
        }
    } catch (err) {
        console.warn("ITMS status fetch error:", err);
    }
}

async function loadOperatingBonds() {
    try {
        const res = await fetch("/api/itms/warehouses/");
        const data = await res.json();
        if (data.success && data.warehouses) {
            const sel = document.getElementById("itms-select-bond");
            if (sel) {
                sel.innerHTML = "";
                data.warehouses.forEach(w => {
                    const opt = document.createElement("option");
                    opt.value = w.code;
                    opt.innerText = `${w.name} (${w.code})`;
                    if (data.active_bond && data.active_bond.code === w.code) {
                        opt.selected = true;
                    }
                    sel.appendChild(opt);
                });
            }
            const bondBadge = document.getElementById("itms-active-bond-badge");
            if (bondBadge && data.active_bond) {
                bondBadge.innerText = `${data.active_bond.name} (${data.active_bond.code})`;
            }
        }
    } catch (err) {
        console.warn("Failed to load operating warehouses:", err);
    }
}

async function onBondSelectionChanged() {
    const sel = document.getElementById("itms-select-bond");
    if (!sel) return;
    const code = sel.value;
    const opt = sel.options[sel.selectedIndex];
    const name = opt ? opt.text.replace(/\s*\([^)]*\)$/, "").trim() : "";

    try {
        const res = await fetch("/api/itms/warehouses/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: code, name: name }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Operating facility set to ${code}.`, "info");
            const bondBadge = document.getElementById("itms-active-bond-badge");
            if (bondBadge && data.active_bond) {
                bondBadge.innerText = `${data.active_bond.name} (${data.active_bond.code})`;
            }
            if (typeof appendConsoleLog === "function") {
                appendConsoleLog(`Operating bond switched to ${data.active_bond.name} (${data.active_bond.code})`, "info");
            }
            if (typeof fetchStockReconciliation === "function") {
                fetchStockReconciliation();
            }
        } else {
            showToast(data.error || "Failed to switch operating bond.", "error");
        }
    } catch (err) {
        showToast("Error switching operating bond: " + err, "error");
    }
}

function openItmsModal() {
    openModal("modal-itms");
}

async function submitItmsConnect() {
    let email = "";
    let password = "";
    const modalEmail = document.getElementById("itms-connect-email");
    const modalPass = document.getElementById("itms-connect-password");
    if (modalEmail && modalPass && (modalEmail.value || modalPass.value)) {
        email = modalEmail.value.trim();
        password = modalPass.value.trim();
    } else {
        const inputEmail = document.getElementById("itms-input-email");
        const inputPass = document.getElementById("itms-input-password");
        if (inputEmail) email = inputEmail.value.trim();
        if (inputPass) password = inputPass.value.trim();
    }

    if (!email || !password) {
        showToast("ITMS Email and Password are required.", "error");
        return;
    }

    const selBond = document.getElementById("itms-select-bond");
    let bondCode = selBond ? selBond.value : "AGM";
    let bondName = selBond && selBond.options[selBond.selectedIndex] ? selBond.options[selBond.selectedIndex].text.replace(/\s*\([^)]*\)$/, "").trim() : "";

    const btnModal = document.getElementById("btn-itms-connect");
    const btnPane = document.getElementById("btn-submit-itms-connect");
    if (btnModal) { btnModal.disabled = true; btnModal.innerText = "Connecting..."; }
    if (btnPane) { btnPane.disabled = true; btnPane.innerText = "Authenticating against ITMS..."; }

    try {
        const res = await fetch("/api/itms/connect/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `email=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}&bond_code=${encodeURIComponent(bondCode)}&bond_name=${encodeURIComponent(bondName)}`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Connected to ITMS WebApp successfully.", "success");
            if (typeof appendConsoleLog === "function") appendConsoleLog(`ITMS session authenticated as '${email}' for ${bondCode}.`, "success");
            if (modalPass) modalPass.value = "";
            const inputPass = document.getElementById("itms-input-password");
            if (inputPass) inputPass.value = "";
            closeModal("modal-itms");
            fetchItmsStatus();
            if (typeof switchItmsSubview === "function") switchItmsSubview("ORDERS");
        } else {
            showToast(data.message || data.error || "ITMS authentication failed.", "error");
            if (typeof appendConsoleLog === "function") appendConsoleLog(`ITMS login failed: ${data.error || data.message}`, "error");
        }
    } catch (err) {
        showToast("Connection error: " + err, "error");
    } finally {
        if (btnModal) { btnModal.disabled = false; btnModal.innerText = "Connect Account"; }
        if (btnPane) { btnPane.disabled = false; btnPane.innerText = "Connect & Sign In"; }
    }
}

async function submitItmsCredentials() {
    return submitItmsConnect();
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

function switchItmsSubview(subview) {
    itmsCurrentSubview = subview;
    const btnConnect = document.getElementById("btn-sub-connect");
    const btnOrders = document.getElementById("btn-sub-orders");
    const btnArchive = document.getElementById("btn-sub-archive");
    const btnKits = document.getElementById("btn-sub-kits");
    const viewConnect = document.getElementById("itms-view-connect");
    const viewOrders = document.getElementById("itms-view-orders");
    const subviewControls = document.getElementById("itms-subview-controls");
    const autoSyncKitsBtn = document.getElementById("btn-itms-auto-sync-kits");
    const thead = document.getElementById("itms-explorer-thead");

    if (btnConnect) btnConnect.classList.toggle("active", subview === "CONNECT");
    if (btnOrders) btnOrders.classList.toggle("active", subview === "ORDERS");
    if (btnArchive) btnArchive.classList.toggle("active", subview === "ARCHIVE");
    if (btnKits) btnKits.classList.toggle("active", subview === "KITS");

    if (autoSyncKitsBtn) {
        autoSyncKitsBtn.style.display = (subview === "KITS") ? "inline-block" : "none";
    }

    if (thead) {
        if (subview === "KITS") {
            thead.innerHTML = `<tr>
                <th style="width:140px;">Kit Code</th>
                <th style="width:110px;">Plate</th>
                <th class="hide-mobile" style="width:130px;">Front Plate</th>
                <th class="hide-mobile" style="width:130px;">Rear Plate</th>
                <th class="hide-mobile" style="width:140px;">GPS Tracker</th>
                <th style="width:100px;">Status</th>
                <th class="hide-mobile" style="width:130px;">Warehouse</th>
                <th class="hide-mobile" style="width:110px;">Created Date</th>
            </tr>`;
        } else {
            thead.innerHTML = `<tr>
                <th style="width:160px;">Order #</th>
                <th style="width:120px;">Plate</th>
                <th class="hide-mobile" style="width:160px;">VIN / Chassis</th>
                <th style="width:110px;">Status</th>
                <th class="hide-mobile" style="width:140px;">Warehouse</th>
                <th class="hide-mobile" style="width:130px;">Officer</th>
                <th class="hide-mobile" style="width:130px;">Date</th>
            </tr>`;
        }
    }

    if (subview === "CONNECT") {
        if (viewConnect) viewConnect.classList.add("active");
        if (viewOrders) viewOrders.classList.remove("active");
        if (subviewControls) subviewControls.style.display = "none";
    } else {
        if (viewConnect) viewConnect.classList.remove("active");
        if (viewOrders) viewOrders.classList.add("active");
        if (subviewControls) subviewControls.style.display = "flex";
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
    let tabName = "active";
    if (itmsCurrentSubview === "KITS") {
        tabName = "kits";
    } else if (itmsCurrentSubview === "ARCHIVE") {
        tabName = "archive";
    }

    const tbody = document.getElementById("itms-explorer-tbody");
    const labelKind = (tabName === "kits") ? "installation kits" : `${tabName} orders`;
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:30px; color:var(--ug-text-muted);">
        Fetching ${labelKind} from ${itmsExplorerSource.toUpperCase()}...
    </td></tr>`;

    const sourceTag = document.getElementById("itms-explorer-source-tag");
    sourceTag.innerText = (itmsExplorerSource === "live") ? "Live ITMS" : "Local DB";
    sourceTag.className = (itmsExplorerSource === "live") ? "badge badge-yellow" : "badge badge-green";

    try {
        const url = `/api/itms/orders/?tab=${encodeURIComponent(tabName)}&source=${encodeURIComponent(itmsExplorerSource)}&page=${itmsExplorerPage}&search=${encodeURIComponent(itmsExplorerSearch)}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            itmsExplorerOrders = data.orders || data.kits || [];
            renderItmsOrdersTable(itmsExplorerOrders);

            setText("itms-explorer-summary", data.summary || `Showing ${itmsExplorerOrders.length} records`);
            setText("itms-explorer-page-label", `Page ${itmsExplorerPage}${data.total_pages ? ' of ' + data.total_pages : ''}`);
            document.getElementById("btn-explorer-prev").disabled = (itmsExplorerPage <= 1);
            document.getElementById("btn-explorer-next").disabled = !data.has_next_page;

            // Auto-select first item if available (desktop only, to keep table visible on mobile)
            if (itmsExplorerOrders.length > 0 && window.innerWidth > 768) {
                selectItmsOrder(itmsExplorerOrders[0]);
            }
        } else {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:30px; color:#f87171;">
                ${escapeHtml(data.error || "Failed loading records from ITMS.")}
            </td></tr>`;
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:30px; color:#f87171;">
            Connection error: ${escapeHtml(String(err))}
        </td></tr>`;
    }
}

function renderItmsOrdersTable(items) {
    const tbody = document.getElementById("itms-explorer-tbody");
    if (!items || items.length === 0) {
        const colCount = (itmsCurrentSubview === "KITS") ? 8 : 7;
        tbody.innerHTML = `<tr><td colspan="${colCount}" style="text-align:center; padding:40px; color:var(--ug-text-dim);">
            No records found matching criteria.
        </td></tr>`;
        return;
    }

    if (itmsCurrentSubview === "KITS") {
        tbody.innerHTML = items.map((k, idx) => {
            const kitCode = escapeHtml(k.kit_code || "---");
            const regPlate = escapeHtml(k.registration_number || "---");
            const frontPlate = escapeHtml(k.front_plate || "---");
            const rearPlate = escapeHtml(k.rear_plate || "---");
            const gps = escapeHtml(k.gps_tracker || "---");
            const status = escapeHtml(k.status || "New");
            const wh = escapeHtml(k.warehouse || "---");
            const created = escapeHtml(k.created_date || "---");

            let badgeClass = "badge-green";
            const stLow = status.toLowerCase();
            if (stLow.includes("installed")) {
                badgeClass = "badge-blue";
            } else if (stLow.includes("allocat")) {
                badgeClass = "badge-yellow";
            }

            return `<tr id="itms-row-${idx}" onclick="selectItmsOrderAtIndex(${idx})">
                <td><strong style="color:#38bdf8; font-family:var(--font-mono);">${kitCode}</strong></td>
                <td><span style="color:var(--ug-yellow); font-family:var(--font-mono); font-weight:800;">${regPlate}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${frontPlate}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${rearPlate}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:#a78bfa;">${gps}</span></td>
                <td><span class="badge ${badgeClass}">${status}</span></td>
                <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${wh}</span></td>
                <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-dim);">${created}</span></td>
            </tr>`;
        }).join("");
        return;
    }

    tbody.innerHTML = items.map((o, idx) => {
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
            <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${vin}</span></td>
            <td><span class="badge ${status.toLowerCase().includes('installed') ? 'badge-green' : 'badge-yellow'}">${status}</span></td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${wh}</span></td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${officer}</span></td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-dim);">${date}</span></td>
        </tr>`;
    }).join("");
}

function openItmsMobileInspector() {
    const panel = document.getElementById("itms-inspector-panel");
    if (panel) {
        panel.classList.add("mobile-show");
        panel.scrollTop = 0;
    }
}

function closeItmsMobileInspector() {
    const panel = document.getElementById("itms-inspector-panel");
    if (panel) {
        panel.classList.remove("mobile-show");
    }
}

function selectItmsOrderAtIndex(idx) {
    if (itmsExplorerOrders[idx]) {
        selectItmsOrder(itmsExplorerOrders[idx]);
        openItmsMobileInspector();
    }
}

async function selectItmsOrder(order) {
    selectedItmsOrder = order;

    // Highlight row
    const rows = document.querySelectorAll("#itms-explorer-tbody tr");
    rows.forEach(r => r.classList.remove("selected"));

    const kitBanner = document.getElementById("itms-insp-kit-banner");
    const linkKitWeb = document.getElementById("link-itms-kit-web");
    const qBanner = document.getElementById("itms-insp-queue-banner");
    const photosGrid = document.getElementById("itms-insp-photos-grid");

    // Case A: Installation Kit Profile
    if (itmsCurrentSubview === "KITS" || (order.kit_code && !order.vin)) {
        setText("itms-insp-plate", order.registration_number || order.kit_code || "---");
        setText("itms-insp-order", `Kit #${order.kit_code || ''} • ${order.warehouse || 'Warehouse Stock'}`);
        setText("itms-insp-gps", order.gps_tracker || "Not Fitted");
        setText("itms-insp-fbeacon", order.front_tracker || "None");
        setText("itms-insp-rbeacon", order.rear_tracker || "None");
        setText("itms-insp-fserial", order.front_plate || "---");
        setText("itms-insp-rserial", order.rear_plate || "---");

        if (photosGrid) {
            photosGrid.innerHTML = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:var(--ug-text-dim); font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md); line-height:1.5;">
                Physical Kit enrolled in warehouse stock with status <strong style="color:var(--ug-green);">${order.status || 'New'}</strong>.<br>
                <span style="color:#94a3b8; font-size:0.72rem;">Evidence photos attach dynamically once fitment is assigned to a vehicle order.</span>
            </div>`;
        }

        if (qBanner) qBanner.style.display = "none";

        if (kitBanner && linkKitWeb) {
            kitBanner.style.display = "flex";
            const detailUrl = order.detail_url || (order.kit_uuid ? `https://stock.itms.ug/installation-kit/${order.kit_uuid}/main/information` : "");
            if (detailUrl) {
                linkKitWeb.href = detailUrl;
                linkKitWeb.style.display = "inline-block";
            } else {
                linkKitWeb.style.display = "none";
            }
        }
        return;
    }

    // Case B: Active or Archive Order Profile
    if (kitBanner) kitBanner.style.display = "none";

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
            const photos = o.photos || [];

            if (photosGrid) {
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
            }

            // Verification Queue correlation
            const qBtn = document.getElementById("btn-itms-jump-queue");
            if (o.matched_pair && qBanner && qBtn) {
                qBanner.style.display = "flex";
                qBtn.onclick = () => {
                    switchNavTab(4);
                    selectPair(o.matched_pair.id);
                };
            } else if (qBanner) {
                qBanner.style.display = "none";
            }
        }
    } catch (err) {
        console.warn("Error fetching order detail:", err);
    }
}

async function syncCurrentItmsPage() {
    let tabName = "active";
    if (itmsCurrentSubview === "KITS") {
        tabName = "kits";
    } else if (itmsCurrentSubview === "ARCHIVE") {
        tabName = "archive";
    }

    const labelKind = (tabName === "kits") ? "installation kits" : `${tabName} orders`;
    if (typeof appendConsoleLog === "function") appendConsoleLog(`Starting live synchronization of ${labelKind}...`, "info");

    try {
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `tab=${encodeURIComponent(tabName)}&pages=1`,
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, "success");
            if (typeof appendConsoleLog === "function") appendConsoleLog(`Sync complete: ${data.message}`, "success");
            fetchStats();
            fetchItmsExplorerOrders();
            if (typeof fetchReportTotals === "function") fetchReportTotals();
        } else {
            showToast(data.error || data.message || "Sync failed", "error");
        }
    } catch (err) {
        showToast("Sync error: " + err, "error");
    }
}

async function autoSyncAllItmsKits() {
    const btn = document.getElementById("btn-itms-auto-sync-kits");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Crawling All 20+ Pages...";
    }
    if (typeof appendConsoleLog === "function") {
        appendConsoleLog("Starting multi-page auto-sync of all installation kits from stock.itms.ug...", "info");
    }
    if (typeof showToast === "function") {
        showToast("⏳ Crawling and synchronizing all installation kits pages from ITMS...", "info");
    }

    try {
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "tab=kits&pages=30",
        });
        const data = await res.json();
        if (data.success) {
            const msg = `✓ Synced ${data.total_fetched || 0} kits (${data.created || 0} new, ${data.updated || 0} updated) across ${data.pages_fetched || 0} pages!`;
            if (typeof showToast === "function") showToast(msg, "success");
            if (typeof appendConsoleLog === "function") appendConsoleLog(msg, "success");
            fetchItmsExplorerOrders();
            if (typeof fetchReportTotals === "function") fetchReportTotals();
        } else {
            if (typeof showToast === "function") showToast(data.error || "Auto-sync kits failed.", "error");
        }
    } catch (err) {
        if (typeof showToast === "function") showToast("Error during auto-sync kits: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "⚡ Auto-Sync Kits (All Pages)";
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    loadOperatingBonds();
});

