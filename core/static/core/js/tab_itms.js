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
        const colCount = 7;
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
                <td><strong style="color:#ffffff !important; font-family:var(--font-mono);">${kitCode}</strong></td>
                <td><span style="color:#ffffff !important; font-family:var(--font-mono); font-weight:800;">${regPlate}</span></td>
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
        const hasPhoto = Boolean(o.has_photos || (o.photos && o.photos.length > 0));
        const photoBadge = hasPhoto ? '<span title="Evidence photos available" style="margin-left:5px; font-size:0.75rem; vertical-align:middle; cursor:pointer;">📷</span>' : '';

        return `<tr id="itms-row-${idx}" onclick="selectItmsOrderAtIndex(${idx})">
            <td><strong style="color:#fff; font-family:var(--font-mono);">${orderNum}</strong></td>
            <td><span style="color:#ffffff !important; font-family:var(--font-mono); font-weight:800;">${regPlate}</span>${photoBadge}</td>
            <td class="hide-mobile"><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${vin}</span></td>
            <td><span class="badge ${status.toLowerCase().includes('installed') ? 'badge-green' : 'badge-yellow'}">${status}</span></td>
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

function openItmsOrderDetailsModal(order) {
    if (!order) return;
    const isKit = (itmsCurrentSubview === "KITS" || (order.kit_code && !order.vin));

    setText("itms-modal-insp-title", isKit ? "📦 ITMS Kit Profile" : "📋 ITMS Order Details");
    const stBadge = document.getElementById("itms-modal-insp-status-badge");
    const status = order.order_status || order.status || "Pending";
    if (stBadge) {
        stBadge.innerText = status;
        stBadge.className = "badge " + (status.toLowerCase().includes("installed") ? "badge-green" : "badge-yellow");
    }

    setText("itms-modal-insp-plate", order.registration_number || order.kit_code || "---");
    setText("itms-modal-insp-order", isKit ? ("Kit #" + (order.kit_code || "")) : ("Order #" + (order.order_number || "")));
    setText("itms-modal-insp-wh", order.warehouse || order.warehouse_name || "AGM Bonded Warehouse");
    setText("itms-modal-insp-date", order.installation_date || order.created_date || "---");

    // Telematics
    const hw = order.hardware || (order.order_details && order.order_details.hardware) || {};
    setText("itms-modal-insp-gps", hw.gps_tracker_id || order.gps_tracker || "Not Fitted");
    setText("itms-modal-insp-fbeacon", hw.front_beacon_id || order.front_tracker || order.front_beacon || "None");
    setText("itms-modal-insp-rbeacon", hw.rear_beacon_id || order.rear_tracker || order.rear_beacon || "None");
    setText("itms-modal-insp-fserial", hw.front_plate_serial || order.front_plate || "---");
    setText("itms-modal-insp-rserial", hw.rear_plate_serial || order.rear_plate || "---");

    // Evidence photos
    const modalPhotosGrid = document.getElementById("itms-modal-insp-photos-grid");
    if (modalPhotosGrid) {
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
        if (!isKit && order && order.order_number) {
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
        selectItmsOrder(order);
        if (window.innerWidth <= 992) {
            openItmsOrderDetailsModal(order);
        }
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

async function selectItmsOrder(order) {
    if (!order) return;
    selectedItmsOrder = order;

    // Highlight row
    const rows = document.querySelectorAll("#itms-explorer-tbody tr");
    rows.forEach(r => r.classList.remove("selected"));

    const kitBanner = document.getElementById("itms-insp-kit-banner");
    const linkKitWeb = document.getElementById("link-itms-kit-web");
    const qBanner = document.getElementById("itms-insp-queue-banner");
    const photosGrid = document.getElementById("itms-insp-photos-grid");
    const modalPhotosGrid = document.getElementById("itms-modal-insp-photos-grid");

    const isKit = (itmsCurrentSubview === "KITS" || (order.kit_code && !order.vin));

    // Case A: Installation Kit Profile
    if (isKit) {
        setText("itms-insp-plate", order.registration_number || order.kit_code || "---");
        setText("itms-insp-order", `Kit #${order.kit_code || ''}   ${order.warehouse || 'Warehouse Stock'}`);
        setText("itms-insp-gps", order.gps_tracker || "Not Fitted");
        setText("itms-insp-fbeacon", order.front_tracker || "None");
        setText("itms-insp-rbeacon", order.rear_tracker || "None");
        setText("itms-insp-fserial", order.front_plate || "---");
        setText("itms-insp-rserial", order.rear_plate || "---");

        if (photosGrid) photosGrid.innerHTML = renderItmsPhotoCards([], true);
        if (modalPhotosGrid) modalPhotosGrid.innerHTML = renderItmsPhotoCards([], true);

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

    const quickInstallBtn = document.getElementById("btn-itms-insp-quick-install");
    if (quickInstallBtn) {
        quickInstallBtn.style.display = order.order_number ? "inline-flex" : "none";
    }

    // Populate Inspector panel
    setText("itms-insp-plate", order.registration_number || "---");
    setText("itms-insp-order", `Order #${order.order_number || ''}   ${order.warehouse || order.warehouse_name || 'Uganda Hub'}`);

    // If photos or hardware are already cached on order, render immediately!
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

    const existingHw = order.hardware || (order.order_details && order.order_details.hardware);
    if (existingHw) {
        setText("itms-insp-gps", existingHw.gps_tracker_id || "Not Fitted");
        setText("itms-insp-fbeacon", existingHw.front_beacon_id || "None");
        setText("itms-insp-rbeacon", existingHw.rear_beacon_id || "None");
        setText("itms-insp-fserial", existingHw.front_plate_serial || "---");
        setText("itms-insp-rserial", existingHw.rear_plate_serial || "---");
    }

    order.is_loading_details = true;

    // Fetch full order details including hardware & direct photo links
    try {
        const ident = order.order_number || order.order_key || order.registration_number || "";
        const res = await fetch(`/api/itms/orders/${encodeURIComponent(ident)}/?live=1`);
        const data = await res.json();
        order.is_loading_details = false;
        if (data.success && data.order) {
            const o = data.order;
            order.order_details = o;
            order.photos = o.photos || [];
            order.hardware = o.hardware || {};
            order.matched_pair = o.matched_pair || null;
            const hw = o.hardware || {};

            setText("itms-insp-gps", hw.gps_tracker_id || "Not Fitted");
            setText("itms-insp-fbeacon", hw.front_beacon_id || "None");
            setText("itms-insp-rbeacon", hw.rear_beacon_id || "None");
            setText("itms-insp-fserial", hw.front_plate_serial || "---");
            setText("itms-insp-rserial", hw.rear_plate_serial || "---");

            setText("itms-modal-insp-gps", hw.gps_tracker_id || "Not Fitted");
            setText("itms-modal-insp-fbeacon", hw.front_beacon_id || "None");
            setText("itms-modal-insp-rbeacon", hw.rear_beacon_id || "None");
            setText("itms-modal-insp-fserial", hw.front_plate_serial || "---");
            setText("itms-modal-insp-rserial", hw.rear_plate_serial || "---");

            // Evidence Photos (Direct Web Links - ZERO download required!)
            const photos = o.photos || [];
            const photoCardsHtml = renderItmsPhotoCards(photos, false);

            if (photosGrid) photosGrid.innerHTML = photoCardsHtml;
            if (modalPhotosGrid) modalPhotosGrid.innerHTML = photoCardsHtml;

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
            const errHtml = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:rgba(255,255,255,0.7); font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
                ${escapeHtml(data.error || "No photos or live details returned from ITMS.")}
            </div>`;
            if (photosGrid && (!order.photos || order.photos.length === 0)) photosGrid.innerHTML = errHtml;
            if (modalPhotosGrid && (!order.photos || order.photos.length === 0)) modalPhotosGrid.innerHTML = errHtml;
        }
    } catch (err) {
        order.is_loading_details = false;
        console.warn("Error fetching order detail:", err);
        const errHtml = `<div style="grid-column:1/-1; padding:18px; text-align:center; color:#f87171; font-size:0.78rem; background:var(--ug-black-surface); border-radius:var(--radius-md);">
            Failed fetching details: ${escapeHtml(String(err))}
        </div>`;
        if (photosGrid && (!order.photos || order.photos.length === 0)) photosGrid.innerHTML = errHtml;
        if (modalPhotosGrid && (!order.photos || order.photos.length === 0)) modalPhotosGrid.innerHTML = errHtml;
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
