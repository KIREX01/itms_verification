/**
 * ITMS CLOSING SYSTEM - TAB 2: ITMS CONNECT & ORDERS/ARCHIVE EXPLORER
 * Handles stock.itms.ug authentication, order registry browsing, telemetry inspection,
 * and direct web link rendering of installation evidence photos.
 */

async function fetchItmsStatus(force = false) {
    try {
        const res = await fetch(force ? "/api/itms/status/?force=1" : "/api/itms/status/");
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

async function loadOperatingBonds(forceProbe = false) {
    if (forceProbe) {
        void fetchItmsStatus(true);
    }
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
            fetchItmsStatus(true);
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
            fetchItmsStatus(true);
            switchItmsSubview("CONNECT");
        }
    } catch (err) {
        showToast("Error disconnecting: " + err, "error");
    }
}

function switchItmsSubview(subview) {
    itmsCurrentSubview = subview;
    const mobBar = document.getElementById("itms-mobile-selected-bar");
    if (mobBar) mobBar.style.display = "none";

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
                <th style="width:130px;">#</th>
                <th style="width:110px;">Registration number</th>
                <th class="hide-mobile" style="width:110px;">Front Plate</th>
                <th class="hide-mobile" style="width:110px;">Rear Plate</th>
                <th class="hide-mobile" style="width:130px;">Front Tracker</th>
                <th class="hide-mobile" style="width:130px;">Rear Tracker</th>
                <th class="hide-mobile" style="width:130px;">GPS Tracker</th>
                <th class="hide-mobile" style="width:140px;">Warehouse</th>
                <th style="width:90px;">Status</th>
                <th class="hide-mobile" style="width:100px;">Created date</th>
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
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--ug-text-muted);">
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
        const colCount = (itmsCurrentSubview === "KITS") ? 10 : 7;
        tbody.innerHTML = `<tr><td colspan="${colCount}" style="text-align:center; padding:40px; color:var(--ug-text-dim);">
            No records found matching criteria.
        </td></tr>`;
        return;
    }

    if (itmsCurrentSubview === "KITS") {
        tbody.innerHTML = items.map((k, idx) => {
            const kitCode = escapeHtml(k.kit_code || k.number || "---");
            const regPlate = escapeHtml(k.registration_number || "---");
            const frontPlate = escapeHtml(k.front_plate || (k.details_json && k.details_json.front_plate && k.details_json.front_plate.serial_number) || "---");
            const rearPlate = escapeHtml(k.rear_plate || (k.details_json && k.details_json.rear_plate && k.details_json.rear_plate.serial_number) || "---");
            const frontTracker = escapeHtml(k.front_tracker || (k.details_json && k.details_json.front_tracker && k.details_json.front_tracker.serial_number) || "---");
            const rearTracker = escapeHtml(k.rear_tracker || (k.details_json && k.details_json.rear_tracker && k.details_json.rear_tracker.serial_number) || "---");
            const gps = escapeHtml(k.gps_tracker || (k.details_json && k.details_json.gps_tracker && k.details_json.gps_tracker.serial_number) || "---");
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
                <td><strong style="color:#ffffff !important; font-family:var(--font-mono);">${kitCode}</strong></td>
                <td>
                    <span style="color:#ffffff !important; font-family:var(--font-mono); font-weight:800;">${regPlate}</span>
                    <div class="itms-mobile-row-sub">
                        <span>FP: <code>${frontPlate}</code> • RP: <code>${rearPlate}</code></span>
                        <span>GPS: <code style="color:#a78bfa;">${gps}</code></span>
                        <span>Trk: <code style="color:#38bdf8;">${frontTracker || rearTracker}</code></span>
                    </div>
                </td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${frontPlate}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${rearPlate}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:#38bdf8;">${frontTracker}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:#38bdf8;">${rearTracker}</span></td>
                <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:#a78bfa;">${gps}</span></td>
                <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${wh}</span></td>
                <td>
                    <span class="badge ${badgeClass}">${status}</span>
                    <span class="itms-mobile-chevron">›</span>
                </td>
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
        const hasPhoto = Boolean(o.has_photos || (o.photos && o.photos.length > 0));
        const photoBadge = hasPhoto ? '<span title="Evidence photos available" style="margin-left:5px; font-size:0.75rem; vertical-align:middle; cursor:pointer;">📷</span>' : '';

        const hw = o.hardware || {};
        const gpsId = escapeHtml(o.gps_tracker_id || hw.gps_tracker_id || (o.gps_tracker && o.gps_tracker.serial_number) || "");
        const fbId = escapeHtml(o.front_beacon_id || hw.front_beacon_id || (o.front_beacon && o.front_beacon.serial_number) || "");
        const rbId = escapeHtml(o.rear_beacon_id || hw.rear_beacon_id || (o.rear_beacon && o.rear_beacon.serial_number) || "");
        const fpId = escapeHtml(o.front_plate_serial || hw.front_plate_serial || (o.front_plate && o.front_plate.serial_number) || "");
        const rpId = escapeHtml(o.rear_plate_serial || hw.rear_plate_serial || (o.rear_plate && o.rear_plate.serial_number) || "");

        return `<tr id="itms-row-${idx}" onclick="selectItmsOrderAtIndex(${idx})">
            <td><strong style="color:#fff; font-family:var(--font-mono);">${orderNum}</strong></td>
            <td>
                <span style="color:#ffffff !important; font-family:var(--font-mono); font-weight:800;">${regPlate}</span>${photoBadge}
                <div class="itms-mobile-row-sub">
                    ${vin && vin !== "---" ? `<span>VIN: <code>${vin}</code></span>` : ""}
                    ${gpsId ? `<span>GPS: <code style="color:#c084fc;">${gpsId}</code></span>` : ""}
                    ${fbId || rbId ? `<span>BLE: <code style="color:#38bdf8;">${fbId || rbId}</code></span>` : ""}
                    ${fpId || rpId ? `<span>Plates: <code>${fpId}</code> / <code>${rpId}</code></span>` : ""}
                </div>
            </td>
            <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${vin}</span></td>
            <td>
                <span class="badge ${status.toLowerCase().includes('installed') ? 'badge-green' : 'badge-yellow'}">${status}</span>
                <span class="itms-mobile-chevron">›</span>
            </td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${wh}</span></td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-muted);">${officer}</span></td>
            <td class="hide-mobile"><span style="font-size:0.75rem; color:var(--ug-text-dim);">${date}</span></td>
        </tr>`;
    }).join("");
}

function renderItmsPhotoCards(photos, isKit = false) {
    if (!photos || photos.length === 0) {
        return `<div style="grid-column:1/-1; padding:22px; text-align:center; color:rgba(255,255,255,0.7); font-size:0.8rem; background:var(--ug-black-surface); border-radius:var(--radius-md); line-height:1.5;">
            ${isKit ? "Physical Kit enrolled in warehouse stock. Evidence photos attach dynamically when fitment is completed." : "No photos uploaded on ITMS for this order."}
        </div>`;
    }

    return photos.map(p => {
        const lbl = escapeHtml(p.label || (p.orientation === "FRONT" ? "Front Plate" : "Rear Plate"));
        const rawUrl = p.url || "";
        const url = escapeHtml(rawUrl);
        const icon = (p.orientation === "FRONT") ? "📷" : "📸";
        return `<div class="photo-link-card">
            <div class="photo-link-label">
                <span style="font-weight:700; color:#ffffff;">${icon} ${lbl}</span>
                <span class="badge badge-secondary" style="font-size:0.68rem; padding:2px 6px; font-weight:700; color:#ffffff;">${escapeHtml(p.orientation || "EVIDENCE")}</span>
            </div>
            <div class="photo-link-thumb" onclick="if(typeof openLightbox==='function'){openLightbox('${url}')}else{window.open('${url}','_blank')}" title="Click for full view">
                <img src="${url}" alt="${lbl}" loading="lazy" onerror="this.onerror=null; this.src=''; this.parentElement.innerHTML='<div style=\'padding:22px 10px; text-align:center; color:#94a3b8; font-size:0.75rem;\'><div style=\'font-size:1.3rem; margin-bottom:4px;\'>⚠️</div>Preview unavailable<br><span style=\'font-size:0.7rem; color:#64748b;\'>Direct link active below</span></div>';">
            </div>
            <div class="photo-link-footer" style="display:flex; justify-content:space-between; align-items:center;">
                <a href="${url}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8; text-decoration:none; display:inline-flex; align-items:center; gap:4px; font-weight:700;">
                    <span>Open Link</span> <span>↗</span>
                </a>
                <button type="button" class="btn btn-secondary btn-sm" onclick="if(typeof openLightbox==='function'){openLightbox('${url}')}else{window.open('${url}','_blank')}; event.stopPropagation();" style="font-size:0.7rem; padding:3px 9px; color:#ffffff; background:#1e293b; border:1px solid rgba(255,255,255,0.25);">
                    🔍 Enlarge
                </button>
            </div>
        </div>`;
    }).join("");
}

function renderItmsDetailsHtml(order, isKit, isArchive) {
    if (!order) {
        return `<div style="padding:28px 10px; text-align:center; color:rgba(255,255,255,0.7); font-size:0.82rem;">
            Select a record to view details.
        </div>`;
    }

    // CASE 1: INSTALLATION KITS (matching https://stock.itms.ug/installation-kit/<uuid>/main/information)
    if (isKit) {
        const infoReg = escapeHtml(order.registration_number || order.kit_code || "---");
        const infoStatus = escapeHtml(order.status || order.order_status || "New");
        const infoUser = escapeHtml(order.created_by_user || (order.details_json && order.details_json.created_by_user) || "Kwoleka Peter");
        const infoDate = escapeHtml(order.created_date || order.installation_date || "---");
        const infoWh = escapeHtml(order.warehouse || order.warehouse_name || "AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION");

        const fp = order.front_plate || (order.details_json && order.details_json.front_plate) || {};
        const fpArticle = escapeHtml(order.front_plate_article || (typeof fp === "object" ? fp.article_name : "") || "PN-PBL-M-UMA-SQR-BWW");
        const fpSerial = escapeHtml((typeof fp === "object" ? fp.serial_number : fp) || order.front_plate || "---");
        const fpReg = escapeHtml((typeof fp === "object" ? fp.registration_number : "") || order.registration_number || "---");
        const fpWh = escapeHtml((typeof fp === "object" ? (fp.current_warehouse || fp.warehouse) : "") || infoWh);

        const rp = order.rear_plate || (order.details_json && order.details_json.rear_plate) || {};
        const rpArticle = escapeHtml(order.rear_plate_article || (typeof rp === "object" ? rp.article_name : "") || "PN-PBL-M-UMA-SQR-BWW");
        const rpSerial = escapeHtml((typeof rp === "object" ? rp.serial_number : rp) || order.rear_plate || "---");
        const rpReg = escapeHtml((typeof rp === "object" ? rp.registration_number : "") || order.registration_number || "---");
        const rpWh = escapeHtml((typeof rp === "object" ? (rp.current_warehouse || rp.warehouse) : "") || infoWh);

        const gps = order.gps_tracker || (order.details_json && order.details_json.gps_tracker) || {};
        const gpsArticle = escapeHtml(order.gps_article || (typeof gps === "object" ? gps.article_name : "") || "GPS");
        const gpsSerial = escapeHtml((typeof gps === "object" ? gps.serial_number : gps) || order.gps_tracker || "---");
        const gpsMac = escapeHtml((typeof gps === "object" ? gps.mac_address : "") || "");
        const gpsWh = escapeHtml((typeof gps === "object" ? (gps.current_warehouse || gps.warehouse) : "") || infoWh);

        const sim = order.sim_card || (order.details_json && order.details_json.sim_card) || {};
        const simArticle = escapeHtml(order.sim_article || (typeof sim === "object" ? sim.article_name : "") || "SIM chip");
        const simSerial = escapeHtml((typeof sim === "object" ? sim.serial_number : sim) || order.sim_serial || "---");
        const simMac = escapeHtml(order.sim_mac || (typeof sim === "object" ? sim.mac_address : "") || "");
        const simWh = escapeHtml((typeof sim === "object" ? (sim.current_warehouse || sim.warehouse) : "") || infoWh);

        const ft = order.front_tracker || (order.details_json && order.details_json.front_tracker) || {};
        const ftArticle = escapeHtml(order.front_tracker_article || (typeof ft === "object" ? ft.article_name : "") || "BLE");
        const ftSerial = escapeHtml((typeof ft === "object" ? ft.serial_number : ft) || order.front_tracker || "---");
        const ftMac = escapeHtml(order.front_tracker_mac || (typeof ft === "object" ? ft.mac_address : "") || "");
        const ftWh = escapeHtml((typeof ft === "object" ? (ft.current_warehouse || ft.warehouse) : "") || infoWh);

        const rt = order.rear_tracker || (order.details_json && order.details_json.rear_tracker) || {};
        const rtArticle = escapeHtml(order.rear_tracker_article || (typeof rt === "object" ? rt.article_name : "") || "BLE");
        const rtSerial = escapeHtml((typeof rt === "object" ? rt.serial_number : rt) || order.rear_tracker || "---");
        const rtMac = escapeHtml(order.rear_tracker_mac || (typeof rt === "object" ? rt.mac_address : "") || "");
        const rtWh = escapeHtml((typeof rt === "object" ? (rt.current_warehouse || rt.warehouse) : "") || infoWh);

        return `
        <!-- Information -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-sky">
                <span>ℹ️</span> Information
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Registration number:</span>
                    <span class="hw-val" style="color:#fde047 !important;">${infoReg}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Status:</span>
                    <span class="hw-val"><span class="badge ${infoStatus.toLowerCase().includes('installed') ? 'badge-blue' : 'badge-green'}">${infoStatus}</span></span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Created by user:</span>
                    <span class="hw-val">${infoUser}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Created date:</span>
                    <span class="hw-val">${infoDate}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${infoWh}</span>
                </div>
            </div>
        </div>

        <!-- Front plate -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-sky">
                <span>🚘</span> Front plate
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${fpArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val">${fpSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Registration number:</span>
                    <span class="hw-val" style="color:#fde047 !important;">${fpReg}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${fpWh}</span>
                </div>
            </div>
        </div>

        <!-- Rear plate -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-sky">
                <span>🚘</span> Rear plate
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${rpArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val">${rpSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Registration number:</span>
                    <span class="hw-val" style="color:#fde047 !important;">${rpReg}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${rpWh}</span>
                </div>
            </div>
        </div>

        <!-- GPS tracker -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-purple">
                <span>🛰️</span> GPS tracker
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${gpsArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val" style="color:#c084fc !important;">${gpsSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">MAC address:</span>
                    <span class="hw-val">${gpsMac || "---"}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${gpsWh}</span>
                </div>
            </div>
        </div>

        <!-- SIM Card -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-amber">
                <span>📶</span> SIM Card
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${simArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val" style="color:#fbbf24 !important;">${simSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">MAC address:</span>
                    <span class="hw-val">${simMac || "---"}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${simWh}</span>
                </div>
            </div>
        </div>

        <!-- Front tracker -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-sky">
                <span>📡</span> Front tracker
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${ftArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val" style="color:#38bdf8 !important;">${ftSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">MAC address:</span>
                    <span class="hw-val">${ftMac || "---"}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${ftWh}</span>
                </div>
            </div>
        </div>

        <!-- Rear tracker -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-sky">
                <span>📡</span> Rear tracker
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Article name:</span>
                    <span class="hw-val itms-val-wrap" style="color:#94a3b8 !important;">${rtArticle}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Serial number:</span>
                    <span class="hw-val" style="color:#38bdf8 !important;">${rtSerial}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">MAC address:</span>
                    <span class="hw-val">${rtMac || "---"}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Current warehouse:</span>
                    <span class="hw-val itms-val-wrap">${rtWh}</span>
                </div>
            </div>
        </div>`;
    }

    // CASE 2: COMPLETED ARCHIVE (matching user specification)
    if (isArchive) {
        const installedBy = escapeHtml(order.installation_officer || order.installed_by || order.officer || "EMMY AKENA");
        const warehouse = escapeHtml(order.warehouse_name || order.warehouse || "AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION");
        const vin = escapeHtml(order.vin || "---");

        const hw = order.hardware || (order.order_details && order.order_details.hardware) || {};
        const reg = order.registration_number || "";

        const fpArticle = (order.front_plate && order.front_plate.article_name) || hw.front_plate_type || "PN-PBL-M-UMA-SQR-BWW";
        const fpSerial = (order.front_plate && order.front_plate.serial_number) || hw.front_plate_serial || order.front_plate_serial || order.front_plate || "---";
        const fpStr = `${fpArticle} (${fpSerial}) (${reg})`;

        const rpArticle = (order.rear_plate && order.rear_plate.article_name) || hw.rear_plate_type || "PN-PBL-M-UMA-SQR-BWW";
        const rpSerial = (order.rear_plate && order.rear_plate.serial_number) || hw.rear_plate_serial || order.rear_plate_serial || order.rear_plate || "---";
        const rpStr = `${rpArticle} (${rpSerial}) (${reg})`;

        const gpsSerial = (order.gps_tracker && order.gps_tracker.serial_number) || hw.gps_tracker_id || order.gps_tracker_id || order.gps_tracker || "---";
        const gpsStr = `GPS (${gpsSerial})`;

        const fbSerial = (order.front_beacon && order.front_beacon.serial_number) || hw.front_beacon_id || order.front_beacon_id || order.front_tracker || "---";
        const fbStr = `BLE (${fbSerial})`;

        const rbSerial = (order.rear_beacon && order.rear_beacon.serial_number) || hw.rear_beacon_id || order.rear_beacon_id || order.rear_tracker || "---";
        const rbStr = `BLE (${rbSerial})`;

        return `
        <!-- Archive Header Overview -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-green">
                <span>✅</span> Archive Fitment Overview
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Installed By:</span>
                    <span class="hw-val">${installedBy}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Installation Warehouse:</span>
                    <span class="hw-val itms-val-wrap">${warehouse}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Vehicle VIN/Chassis No.:</span>
                    <span class="hw-val" style="color:#38bdf8 !important;">${vin}</span>
                </div>
            </div>
        </div>

        <!-- Details -->
        <div class="itms-detail-card">
            <div class="itms-detail-card-title accent-yellow">
                <span>📋</span> Details
            </div>
            <div class="hardware-box" style="padding:0; border:none; background:transparent;">
                <div class="hw-row">
                    <span class="hw-label">Front license plate:</span>
                    <span class="hw-val itms-val-wrap">${escapeHtml(fpStr)}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Rear license plate:</span>
                    <span class="hw-val itms-val-wrap">${escapeHtml(rpStr)}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">GPS Tracker:</span>
                    <span class="hw-val" style="color:#c084fc !important;">${escapeHtml(gpsStr)}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Front beacon:</span>
                    <span class="hw-val" style="color:#38bdf8 !important;">${escapeHtml(fbStr)}</span>
                </div>
                <div class="hw-row">
                    <span class="hw-label">Rear beacon:</span>
                    <span class="hw-val" style="color:#38bdf8 !important;">${escapeHtml(rbStr)}</span>
                </div>
            </div>
        </div>`;
    }

    // CASE 3: ACTIVE ORDERS (matching user specification)
    const vin = escapeHtml(order.vin || "---");
    const oldPlate = escapeHtml(order.old_registration_number || (order.details_json && order.details_json["Old plate number"]) || "---");

    const hw = order.hardware || (order.order_details && order.order_details.hardware) || {};
    const frontPlate = escapeHtml((order.front_plate && order.front_plate.serial_number) || hw.front_plate_serial || order.front_plate_serial || "---");
    const rearPlate = escapeHtml((order.rear_plate && order.rear_plate.serial_number) || hw.rear_plate_serial || order.rear_plate_serial || "---");
    const regNum = escapeHtml(order.registration_number || "---");

    const gpsTracker = escapeHtml((order.gps_tracker && order.gps_tracker.serial_number) || hw.gps_tracker_id || order.gps_tracker_id || "---");
    const frontBeacon = escapeHtml((order.front_beacon && order.front_beacon.serial_number) || hw.front_beacon_id || order.front_beacon_id || "---");
    const rearBeacon = escapeHtml((order.rear_beacon && order.rear_beacon.serial_number) || hw.rear_beacon_id || order.rear_beacon_id || "---");

    return `
    <!-- Vehicle Details -->
    <div class="itms-detail-card">
        <div class="itms-detail-card-title accent-sky">
            <span>🚗</span> Vehicle Details
        </div>
        <div class="hardware-box" style="padding:0; border:none; background:transparent;">
            <div class="hw-row">
                <span class="hw-label">Vehicle VIN/Chassis No.</span>
                <span class="hw-val">${vin}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Old plate number</span>
                <span class="hw-val" style="color:var(--ug-text-dim);">${oldPlate}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Front License Plate *</span>
                <span class="hw-val">${frontPlate}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Rear License Plate *</span>
                <span class="hw-val">${rearPlate}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Registration Number</span>
                <span class="hw-val" style="color:#fde047 !important; font-size:0.95rem; font-weight:900;">${regNum}</span>
            </div>
        </div>
    </div>

    <!-- Tracker Details -->
    <div class="itms-detail-card">
        <div class="itms-detail-card-title accent-purple">
            <span>🛰️</span> Tracker Details
        </div>
        <div class="hardware-box" style="padding:0; border:none; background:transparent;">
            <div class="hw-row">
                <span class="hw-label">GPS Tracker *</span>
                <span class="hw-val" style="color:#c084fc !important;">${gpsTracker}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Front Beacon:</span>
                <span class="hw-val" style="color:#38bdf8 !important;">${frontBeacon}</span>
            </div>
            <div class="hw-row">
                <span class="hw-label">Rear Beacon:</span>
                <span class="hw-val" style="color:#38bdf8 !important;">${rearBeacon}</span>
            </div>
        </div>
    </div>`;
}

function openItmsOrderDetailsModal(order) {
    if (!order) return;
    const isKit = (itmsCurrentSubview === "KITS" || (order.kit_code && !order.vin));
    const isArchive = (itmsCurrentSubview === "ARCHIVE" || Boolean(order.is_archived));

    setText("itms-modal-insp-title", isKit ? "📦 ITMS Kit Profile" : (isArchive ? "🏛️ ITMS Completed Archive" : "📋 ITMS Order Details"));
    const stBadge = document.getElementById("itms-modal-insp-status-badge");
    const status = order.order_status || order.status || "Pending";
    if (stBadge) {
        stBadge.innerText = status;
        let badgeClass = "badge-yellow";
        const stLow = status.toLowerCase();
        if (stLow.includes("installed") || stLow.includes("archive")) {
            badgeClass = "badge-green";
        } else if (stLow.includes("new")) {
            badgeClass = "badge-blue";
        }
        stBadge.className = "badge " + badgeClass;
    }

    setText("itms-modal-insp-plate", order.registration_number || order.kit_code || "---");
    setText("itms-modal-insp-order", isKit ? ("Kit #" + (order.kit_code || "")) : ("Order #" + (order.order_number || "")));
    setText("itms-modal-insp-wh", order.warehouse || order.warehouse_name || "AGM Solutions Uganda Limited");
    setText("itms-modal-insp-date", order.installation_date || order.created_date || "---");

    // Dynamic record details
    const modalDetailsCont = document.getElementById("itms-modal-insp-details-container");
    if (modalDetailsCont) {
        modalDetailsCont.innerHTML = renderItmsDetailsHtml(order, isKit, isArchive);
    }

    // Evidence photos
    const modalPhotosSec = document.getElementById("itms-modal-insp-photos-section");
    const modalPhotosGrid = document.getElementById("itms-modal-insp-photos-grid");
    if (modalPhotosSec) {
        modalPhotosSec.style.display = isKit ? "none" : "block";
    }
    if (modalPhotosGrid && !isKit) {
        const photos = (order.order_details && order.order_details.photos) || order.photos || [];
        if (photos.length > 0) {
            modalPhotosGrid.innerHTML = renderItmsPhotoCards(photos, isKit);
        } else if (order.is_loading_details) {
            modalPhotosGrid.innerHTML = `<div style="grid-column:1/-1; padding:24px; text-align:center; color:#93c5fd; font-size:0.82rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                <span class="spinner" style="display:inline-block; width:14px; height:14px; border:2px solid #3b82f6; border-top-color:transparent; border-radius:50%; animation:spin 0.8s linear infinite; vertical-align:middle; margin-right:8px;"></span> Loading ITMS evidence photos...
            </div>`;
        } else {
            modalPhotosGrid.innerHTML = renderItmsPhotoCards([], isKit);
        }
    }

    // Jump to Queue button
    const qBtn = document.getElementById("btn-modal-itms-jump-queue");
    if (qBtn) {
        const matched = order.matched_pair || (order.order_details && order.order_details.matched_pair);
        if (matched) {
            qBtn.style.display = "inline-flex";
            qBtn.onclick = () => {
                closeModal("modal-itms-order-details");
                switchNavTab(4);
                if (typeof selectPair === "function") selectPair(matched.id);
            };
        } else {
            qBtn.style.display = "none";
        }
    }

    // Direct kit web link
    const kitLink = document.getElementById("link-modal-itms-kit-web");
    if (kitLink) {
        const detailUrl = order.detail_url || (order.kit_uuid ? `https://stock.itms.ug/installation-kit/${order.kit_uuid}/main/information` : "");
        if (isKit && detailUrl) {
            kitLink.style.display = "inline-flex";
            kitLink.href = detailUrl;
        } else {
            kitLink.style.display = "none";
        }
    }

    // Quick Install button at bottom of details modal
    const modalQuickInstallBtn = document.getElementById("btn-modal-itms-quick-install");
    if (modalQuickInstallBtn) {
        if (!isKit && !isArchive && order && order.order_number) {
            modalQuickInstallBtn.style.display = "inline-flex";
            modalQuickInstallBtn.onclick = () => {
                closeModal("modal-itms-order-details");
                if (typeof window.openOrderInstallModal === "function") {
                    window.openOrderInstallModal(order.order_number);
                }
            };
        } else {
            modalQuickInstallBtn.style.display = "none";
        }
    }

    openModal("modal-itms-order-details");
}

function openCurrentOrderInModal() {
    if (selectedItmsOrder) {
        openItmsOrderDetailsModal(selectedItmsOrder);
    } else if (itmsExplorerOrders && itmsExplorerOrders.length > 0) {
        openItmsOrderDetailsModal(itmsExplorerOrders[0]);
    } else {
        if (typeof showToast === "function") showToast("Please select an order from the list first.", "info");
    }
}

function selectItmsOrderAtIndex(idx) {
    if (itmsExplorerOrders && itmsExplorerOrders[idx]) {
        const order = itmsExplorerOrders[idx];
        selectItmsOrder(order, idx);
        openItmsOrderDetailsModal(order);
    }
}

function openItmsMobileInspector() {
    if (selectedItmsOrder) {
        openItmsOrderDetailsModal(selectedItmsOrder);
    }
}

function closeItmsMobileInspector() {
    closeModal("modal-itms-order-details");
}

async function selectItmsOrder(order, idx) {
    if (!order) return;
    selectedItmsOrder = order;

    // Highlight row
    const rows = document.querySelectorAll("#itms-explorer-tbody tr");
    rows.forEach(r => r.classList.remove("selected"));
    if (typeof idx === "number") {
        const selRow = document.getElementById(`itms-row-${idx}`);
        if (selRow) selRow.classList.add("selected");
    }

    const kitBanner = document.getElementById("itms-insp-kit-banner");
    const linkKitWeb = document.getElementById("link-itms-kit-web");
    const qBanner = document.getElementById("itms-insp-queue-banner");
    const photosSec = document.getElementById("itms-insp-photos-section");
    const photosGrid = document.getElementById("itms-insp-photos-grid");
    const modalPhotosGrid = document.getElementById("itms-modal-insp-photos-grid");

    const isKit = (itmsCurrentSubview === "KITS" || (order.kit_code && !order.vin));
    const isArchive = (itmsCurrentSubview === "ARCHIVE" || Boolean(order.is_archived));

    // Update inspector header
    setText("itms-insp-plate", order.registration_number || order.kit_code || "---");
    setText("itms-insp-order", isKit ? (`Kit #${order.kit_code || ''} • ${order.warehouse || 'Warehouse Stock'}`) : (`Order #${order.order_number || ''} • ${order.warehouse || order.warehouse_name || 'Uganda Hub'}`));

    // Update mobile selected record quick bar
    const mobBar = document.getElementById("itms-mobile-selected-bar");
    const mobPlate = document.getElementById("itms-mob-plate");
    const mobOrder = document.getElementById("itms-mob-order");
    if (mobBar && mobPlate && mobOrder) {
        mobBar.style.display = "flex";
        mobPlate.innerText = order.registration_number || order.kit_code || "---";
        mobOrder.innerText = isKit
            ? (`Kit #${order.kit_code || ''} • ${order.status || 'New'}`)
            : (`Order #${order.order_number || ''} • ${order.vin || order.order_status || ''}`);
    }

    // Render inspector details container
    const inspDetailsCont = document.getElementById("itms-insp-details-container");
    if (inspDetailsCont) {
        inspDetailsCont.innerHTML = renderItmsDetailsHtml(order, isKit, isArchive);
    }

    // Case A: Installation Kit Profile
    if (isKit) {
        if (photosSec) photosSec.style.display = "none";
        if (photosGrid) photosGrid.innerHTML = renderItmsPhotoCards([], true);
        if (modalPhotosGrid) modalPhotosGrid.innerHTML = renderItmsPhotoCards([], true);
        if (qBanner) qBanner.style.display = "none";

        const quickInstallBtn = document.getElementById("btn-itms-insp-quick-install");
        if (quickInstallBtn) quickInstallBtn.style.display = "none";

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
    } else {
        // Case B: Active or Archive Order Profile
        if (kitBanner) kitBanner.style.display = "none";
        if (photosSec) photosSec.style.display = "block";

        const quickInstallBtn = document.getElementById("btn-itms-insp-quick-install");
        if (quickInstallBtn) {
            quickInstallBtn.style.display = (!isArchive && order.order_number) ? "inline-flex" : "none";
        }

        const existingPhotos = (order.order_details && order.order_details.photos) || order.photos || [];
        if (existingPhotos.length > 0) {
            if (photosGrid) photosGrid.innerHTML = renderItmsPhotoCards(existingPhotos, false);
            if (modalPhotosGrid) modalPhotosGrid.innerHTML = renderItmsPhotoCards(existingPhotos, false);
        } else {
            const loadingHtml = `<div style="grid-column:1/-1; padding:22px; text-align:center; color:#93c5fd; font-size:0.8rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                <span class="spinner" style="display:inline-block; width:14px; height:14px; border:2px solid #3b82f6; border-top-color:transparent; border-radius:50%; animation:spin 0.8s linear infinite; vertical-align:middle; margin-right:8px;"></span> Loading ITMS evidence photos & telematics...
            </div>`;
            if (photosGrid) photosGrid.innerHTML = loadingHtml;
            if (modalPhotosGrid) modalPhotosGrid.innerHTML = loadingHtml;
        }
    }

    order.is_loading_details = true;

    // Fetch full order or kit details from server
    try {
        const ident = order.order_number || order.kit_code || order.kit_uuid || order.registration_number || "";
        const res = await fetch(`/api/itms/orders/${encodeURIComponent(ident)}/?live=1`);
        const data = await res.json();
        order.is_loading_details = false;
        if (data.success && data.order) {
            const o = data.order;
            order.order_details = o;
            order.photos = o.photos || [];
            order.hardware = o.hardware || {};
            order.matched_pair = o.matched_pair || null;
            if (o.front_plate) order.front_plate = o.front_plate;
            if (o.rear_plate) order.rear_plate = o.rear_plate;
            if (o.gps_tracker) order.gps_tracker = o.gps_tracker;
            if (o.sim_card) order.sim_card = o.sim_card;
            if (o.front_tracker) order.front_tracker = o.front_tracker;
            if (o.rear_tracker) order.rear_tracker = o.rear_tracker;
            if (o.created_by_user) order.created_by_user = o.created_by_user;
            if (o.old_registration_number) order.old_registration_number = o.old_registration_number;

            // Re-render dynamic details containers
            const inspCont = document.getElementById("itms-insp-details-container");
            if (inspCont) inspCont.innerHTML = renderItmsDetailsHtml(order, isKit, isArchive);

            const modalCont = document.getElementById("itms-modal-insp-details-container");
            if (modalCont) modalCont.innerHTML = renderItmsDetailsHtml(order, isKit, isArchive);

            // Evidence Photos
            if (!isKit) {
                const photos = o.photos || [];
                const photoCardsHtml = renderItmsPhotoCards(photos, false);
                if (photosGrid) photosGrid.innerHTML = photoCardsHtml;
                if (modalPhotosGrid) modalPhotosGrid.innerHTML = photoCardsHtml;
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

            const modalQBtn = document.getElementById("btn-modal-itms-jump-queue");
            if (modalQBtn) {
                if (o.matched_pair) {
                    modalQBtn.style.display = "inline-flex";
                    modalQBtn.onclick = () => {
                        closeModal("modal-itms-order-details");
                        switchNavTab(4);
                        if (typeof selectPair === "function") selectPair(o.matched_pair.id);
                    };
                } else {
                    modalQBtn.style.display = "none";
                }
            }
        } else {
            if (!isKit) {
                const errHtml = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:rgba(255,255,255,0.7); font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                    ${escapeHtml(data.error || "No photos or live details returned from ITMS.")}
                </div>`;
                if (photosGrid && (!order.photos || order.photos.length === 0)) photosGrid.innerHTML = errHtml;
                if (modalPhotosGrid && (!order.photos || order.photos.length === 0)) modalPhotosGrid.innerHTML = errHtml;
            }
        }
    } catch (err) {
        order.is_loading_details = false;
        console.warn("Error fetching order detail:", err);
        if (!isKit) {
            const errHtml = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:#f87171; font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                Failed fetching details: ${escapeHtml(String(err))}
            </div>`;
            if (photosGrid && (!order.photos || order.photos.length === 0)) photosGrid.innerHTML = errHtml;
            if (modalPhotosGrid && (!order.photos || order.photos.length === 0)) modalPhotosGrid.innerHTML = errHtml;
        }
    }
}

function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content
        || (document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '')
        || (document.querySelector('[name=csrfmiddlewaretoken]')?.value || '');
}

async function syncItmsShiftOrders() {
    const btn = document.getElementById("btn-itms-sync-shift");
    const origText = btn ? btn.innerHTML : "⚡ Sync Shift Orders";
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = "⏳ Syncing Shift...";
    }

    if (typeof appendConsoleLog === "function") {
        appendConsoleLog("Starting comprehensive shift synchronization (active queue + completed archive)...", "info");
    }
    if (typeof showToast === "function") {
        showToast("⏳ Synchronizing active orders and shift archive from ITMS...", "info");
    }

    try {
        const csrf = getCsrfToken();
        const targetDate = (window.activeShiftDateInfo && window.activeShiftDateInfo.suffix) ? window.activeShiftDateInfo.suffix : "";
        const bodyPayload = `tab=shift&pages=25${targetDate ? '&target_date=' + encodeURIComponent(targetDate) : ''}`;
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf,
            },
            body: bodyPayload,
        });
        const data = await res.json();
        if (data.success) {
            const msg = `✓ ${data.message || "Shift sync completed successfully."}`;
            if (typeof showToast === "function") showToast(msg, "success");
            if (typeof appendConsoleLog === "function") appendConsoleLog(msg, "success");
            fetchStats();
            fetchItmsExplorerOrders();
            if (typeof fetchReportTotals === "function") fetchReportTotals();
        } else {
            const err = data.error || data.message || "Shift sync failed";
            if (typeof showToast === "function") showToast(err, "error");
            if (typeof appendConsoleLog === "function") appendConsoleLog(`Shift sync error: ${err}`, "error");
        }
    } catch (err) {
        if (typeof showToast === "function") showToast("Error during shift sync: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
    }
}

async function syncCurrentItmsPage() {
    const btn = document.getElementById("btn-itms-sync-page");
    const origText = btn ? btn.innerHTML : "📥 Sync Page to DB";
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = "⏳ Syncing...";
    }

    let tabName = "active";
    if (itmsCurrentSubview === "KITS") {
        tabName = "kits";
    } else if (itmsCurrentSubview === "ARCHIVE") {
        tabName = "archive";
    }

    const labelKind = (tabName === "kits") ? "installation kits" : `${tabName} orders`;
    if (typeof appendConsoleLog === "function") appendConsoleLog(`Starting live synchronization of ${labelKind} (page ${itmsExplorerPage})...`, "info");

    try {
        const csrf = getCsrfToken();
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf,
            },
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
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
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
        const csrf = getCsrfToken();
        const res = await fetch("/api/itms/orders/sync/now/", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf,
            },
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


// Explicit window attachment to guarantee availability across subviews
window.syncItmsShiftOrders = syncItmsShiftOrders;
window.syncCurrentItmsPage = syncCurrentItmsPage;
window.autoSyncAllItmsKits = autoSyncAllItmsKits;

window.openItmsOrderDetailsModal = openItmsOrderDetailsModal;
window.openCurrentOrderInModal = openCurrentOrderInModal;
window.openCurrentOrderInInstallModal = function () {
    if (selectedItmsOrder && selectedItmsOrder.order_number) {
        if (typeof window.openOrderInstallModal === "function") {
            window.openOrderInstallModal(selectedItmsOrder.order_number);
        }
    }
};
