/**
 * ITMS CLOSING SYSTEM - TAB 7: REPORTS & TOTALS CONTROLLER
 * Fetches real-time system-wide statistics, date-driven order & plate lifecycle breakdowns,
 * warehouse inventory allocations, and shift productivity metrics.
 */

let reportCurrentScope = "ALL";
let reportCurrentDate = "";
let isFetchingReportTotals = false;
let cachedUnallocatedItems = [];

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

async function fetchReportTotals() {
    if (isFetchingReportTotals) return;
    isFetchingReportTotals = true;

    try {
        let url = `/api/reports/totals/?scope=${encodeURIComponent(reportCurrentScope)}`;
        if (reportCurrentDate) {
            url += `&date=${encodeURIComponent(reportCurrentDate)}`;
        }

        const resp = await fetch(url);
        const data = await resp.json();

        if (!data.success) {
            console.error("Failed to fetch report totals:", data.error);
            return;
        }

        renderReportTotals(data.totals);
    } catch (err) {
        console.error("Error fetching report totals:", err);
    } finally {
        isFetchingReportTotals = false;
    }
}

function onReportDateChanged(val) {
    reportCurrentDate = val;
    fetchReportTotals();
}

function toggleReportScope() {
    reportCurrentScope = (reportCurrentScope === "ALL") ? "TODAY" : "ALL";
    const badge = document.getElementById("report-scope-badge");
    const btn = document.getElementById("btn-toggle-report-scope");
    const isToday = (reportCurrentScope === "TODAY");

    if (badge) {
        badge.textContent = isToday ? "Today's Shift" : "All-Time";
        badge.className = `badge ${isToday ? "badge-yellow" : "badge-green"}`;
    }
    if (btn) {
        btn.textContent = isToday ? "📅 Scope: Today [D]" : "📅 Scope: All [D]";
    }

    fetchReportTotals();
}

function renderReportTotals(t) {
    if (!t) return;

    // Header timestamp
    const ts = document.getElementById("report-generated-timestamp");
    if (ts) {
        ts.textContent = `Generated: ${t.generated_at || "Now"} • Live database synchronized`;
    }

    // Date-Driven Breakdown & Suffix Analysis
    const dd = t.date_driven || {};
    renderDateDrivenSection(dd);

    // KPI Summary
    const p = t.photos || {};
    const b = t.batches || {};
    const pr = t.pairs || {};
    const o = t.orders || {};
    const k = t.kits || {};

    const elPhotosTotal = document.getElementById("report-val-photos-total");
    if (elPhotosTotal) elPhotosTotal.textContent = p.total ?? 0;

    const elPhotosBd = document.getElementById("report-val-photos-breakdown");
    if (elPhotosBd) elPhotosBd.textContent = `Front: ${p.front ?? 0} │ Rear: ${p.rear ?? 0} │ Today: ${p.today ?? 0}`;

    const elBatchesTotal = document.getElementById("report-val-batches-total");
    if (elBatchesTotal) elBatchesTotal.textContent = b.total ?? 0;

    const elBatchesToday = document.getElementById("report-val-batches-today");
    if (elBatchesToday) elBatchesToday.textContent = `Today: ${b.today ?? 0} batches (${b.avg_photos_per_batch ?? 0} avg photos)`;

    const elPairsTotal = document.getElementById("report-val-pairs-total");
    if (elPairsTotal) elPairsTotal.textContent = pr.total ?? 0;

    const elPairsApp = document.getElementById("report-val-pairs-approved");
    if (elPairsApp) elPairsApp.textContent = `Approved: ${pr.approved ?? 0} │ Submitted: ${pr.submitted ?? 0}`;

    const elOrdersTotal = document.getElementById("report-val-orders-total");
    if (elOrdersTotal) elOrdersTotal.textContent = o.total ?? 0;

    const elOrdersActive = document.getElementById("report-val-orders-active");
    if (elOrdersActive) elOrdersActive.textContent = `Active: ${o.active ?? 0} │ Archive: ${o.archive ?? 0}`;

    const elKitsTotal = document.getElementById("report-val-kits-total");
    if (elKitsTotal) elKitsTotal.textContent = k.total ?? 0;

    const elKitsUnalloc = document.getElementById("report-val-kits-unallocated");
    if (elKitsUnalloc) elKitsUnalloc.textContent = `New: ${k.new_unallocated ?? 0} (${k.unallocated_pct || "0%"})`;

    // Kits Section
    const elKitsNewCount = document.getElementById("report-kits-new-count");
    if (elKitsNewCount) elKitsNewCount.textContent = k.new_unallocated ?? 0;

    const elKitsNewPct = document.getElementById("report-kits-new-pct");
    if (elKitsNewPct) elKitsNewPct.textContent = `(${k.unallocated_pct || "0%"})`;

    const elKitsAlloc = document.getElementById("report-kits-alloc-count");
    if (elKitsAlloc) elKitsAlloc.textContent = k.allocated ?? 0;

    const elKitsInst = document.getElementById("report-kits-inst-count");
    if (elKitsInst) elKitsInst.textContent = k.installed ?? 0;

    // Warehouse Table
    const tbodyWh = document.getElementById("report-warehouses-tbody");
    if (tbodyWh) {
        const warehouses = k.warehouses || [];
        if (warehouses.length === 0) {
            tbodyWh.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:12px; color:#64748b;">No installation kits currently stored in local database.</td></tr>`;
        } else {
            tbodyWh.innerHTML = warehouses.map(w => `
                <tr style="border-bottom:1px solid #1e293b;">
                    <td style="padding:6px 10px; font-weight:600; color:#e2e8f0;">${escapeHtml(w.warehouse || "—")}</td>
                    <td style="padding:6px 10px; text-align:center; font-weight:700;">${w.total || 0}</td>
                    <td style="padding:6px 10px; text-align:center; color:#22c55e; font-weight:700;">${w.new || 0}</td>
                    <td style="padding:6px 10px; text-align:center; color:#eab308; font-weight:700;">${w.allocated || 0}</td>
                    <td style="padding:6px 10px; text-align:center; color:#3b82f6; font-weight:700;">${w.installed || 0}</td>
                </tr>
            `).join("");
        }
    }

    // Queue Section
    const elQueueApp = document.getElementById("report-queue-approved");
    if (elQueueApp) elQueueApp.textContent = pr.approved ?? 0;

    const elQueueSub = document.getElementById("report-queue-submitted");
    if (elQueueSub) elQueueSub.textContent = pr.submitted ?? 0;

    const elQueuePend = document.getElementById("report-queue-pending");
    if (elQueuePend) elQueuePend.textContent = pr.pending_review ?? 0;

    const elQueueIncomp = document.getElementById("report-queue-incomplete");
    if (elQueueIncomp) elQueueIncomp.textContent = pr.incomplete ?? 0;

    const elQueueConf = document.getElementById("report-queue-conflicts");
    if (elQueueConf) elQueueConf.textContent = pr.conflict ?? 0;

    const elQueueFail = document.getElementById("report-queue-failed");
    if (elQueueFail) elQueueFail.textContent = pr.failed ?? 0;

    const elQueueMan = document.getElementById("report-queue-manual");
    if (elQueueMan) elQueueMan.textContent = pr.manual_overrides ?? 0;

    const elQueueRate = document.getElementById("report-queue-success-rate");
    if (elQueueRate) elQueueRate.textContent = `${pr.submission_success_rate || "100%"} Success Rate`;

    // Shift Section
    const elShiftPhotos = document.getElementById("report-shift-photos");
    if (elShiftPhotos) elShiftPhotos.textContent = p.today ?? 0;

    const elShiftPairs = document.getElementById("report-shift-pairs");
    if (elShiftPairs) elShiftPairs.textContent = pr.today ?? 0;

    const elShiftApp = document.getElementById("report-shift-approved");
    if (elShiftApp) elShiftApp.textContent = pr.today_approved ?? 0;

    const elShiftSub = document.getElementById("report-shift-submitted");
    if (elShiftSub) elShiftSub.textContent = pr.today_submitted ?? 0;

    // Hardware Section
    const hw = t.hardware || {};
    const elHwPlates = document.getElementById("report-hw-plates");
    if (elHwPlates) elHwPlates.textContent = `${hw.front_plates ?? 0} Front / ${hw.rear_plates ?? 0} Rear`;

    const elHwTrackers = document.getElementById("report-hw-trackers");
    if (elHwTrackers) elHwTrackers.textContent = `${hw.gps_trackers ?? 0} GPS / ${hw.ble_trackers ?? 0} BLE`;
}

function renderDateDrivenSection(dd) {
    if (!dd) return;

    // 1. Populate/Sync Select Dropdown
    const select = document.getElementById("report-date-select");
    if (select && dd.available_dates && dd.available_dates.length > 0) {
        const currVal = dd.selected_date_suffix || "ALL";
        // Only re-populate if count changed or empty
        if (select.options.length <= 1) {
            select.innerHTML = `<option value="ALL">All Work Dates</option>` +
                dd.available_dates.map(d => `
                    <option value="${d.suffix}">${escapeHtml(d.label)} (${d.order_count} orders)</option>
                `).join("");
        }
        select.value = currVal;
    }

    // 2. Selected Date Badge
    const elDateBadge = document.getElementById("report-selected-date-badge");
    if (elDateBadge) {
        const suf = dd.selected_date_suffix || "ALL";
        const fmt = dd.selected_date_formatted || "All Dates";
        elDateBadge.textContent = `Focus: ${fmt} (${suf})`;
    }

    // 3. Category 1: Installed
    const inst = dd.installed_archive || {};
    const elCat1Count = document.getElementById("report-cat1-count");
    if (elCat1Count) elCat1Count.textContent = inst.count ?? 0;

    const elCat1Pct = document.getElementById("report-cat1-pct");
    if (elCat1Pct) elCat1Pct.textContent = inst.percentage || "0%";

    const elCat1Officers = document.getElementById("report-cat1-officers");
    if (elCat1Officers) {
        const officerEntries = Object.entries(inst.officers || {});
        if (officerEntries.length === 0) {
            elCat1Officers.textContent = "Verified Officers: None recorded";
        } else {
            elCat1Officers.textContent = "Officers: " + officerEntries.slice(0, 3).map(([k, v]) => `${k} (${v})`).join(", ");
        }
    }

    // 4. Category 2: Pending Installation
    const pend = dd.pending_orders || {};
    const elCat2Count = document.getElementById("report-cat2-count");
    if (elCat2Count) elCat2Count.textContent = pend.count ?? 0;

    const elCat2Pct = document.getElementById("report-cat2-pct");
    if (elCat2Pct) elCat2Pct.textContent = pend.percentage || "0%";

    const elCat2Stages = document.getElementById("report-cat2-stages");
    if (elCat2Stages) {
        const stageEntries = Object.entries(pend.stages || {});
        if (stageEntries.length === 0) {
            elCat2Stages.textContent = "Stages: None pending";
        } else {
            elCat2Stages.textContent = "Stages: " + stageEntries.map(([k, v]) => `${k} (${v})`).join(", ");
        }
    }

    // 5. Category 3: Unallocated Plates
    const unalloc = dd.unallocated_plates || {};
    const elCat3Count = document.getElementById("report-cat3-count");
    if (elCat3Count) elCat3Count.textContent = unalloc.total_count ?? 0;

    const elCat3Breakdown = document.getElementById("report-cat3-breakdown");
    if (elCat3Breakdown) {
        const recon = dd.stock_reconciliation || {};
        const floor = recon.floor_operations || {};
        const disp = floor.dispatched_count ?? 0;
        if (disp > 0) {
            elCat3Breakdown.textContent = `Elimination: ${disp} Out − ${inst.count ?? 0} Arch − ${pend.count ?? 0} Pend = ${unalloc.total_count ?? 0} Unallocated`;
        } else {
            elCat3Breakdown.textContent = `${unalloc.total_count ?? 0} floor unallocated discrepancy`;
        }
    }

    cachedUnallocatedItems = unalloc.items || [];
    renderUnallocatedTable(cachedUnallocatedItems);
}

function renderUnallocatedTable(items) {
    const tbody = document.getElementById("report-unallocated-tbody");
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:14px; color:#64748b;">No unallocated plates detected. All plates matched to active orders or completed archive.</td></tr>`;
        return;
    }

    tbody.innerHTML = items.slice(0, 100).map(item => {
        const isPair = (item.source === "PAIR_LINK_FAILED");
        const isDispatched = (item.source === "DISPATCH_UNALLOCATED");
        const badgeClass = isPair ? "badge-red" : (isDispatched ? "badge-red" : "badge-yellow");
        const badgeLabel = isPair ? "PAIR LINK FAILED" : (isDispatched ? "UNALLOCATED FLOOR" : "DISCREPANCY");
        const sourceId = isPair ? `Pair #${item.source_id}` : (item.source_id || "Shift Out");

        return `
            <tr style="border-bottom:1px solid #1e293b;">
                <td style="padding:6px 10px; font-weight:700; color:#f8fafc;">
                    <span class="badge ${badgeClass}" style="margin-right:6px; font-size:0.7rem;">${badgeLabel}</span>
                    <span>${escapeHtml(item.display_plate || item.plate)}</span>
                </td>
                <td style="padding:6px 10px; color:#cbd5e1; font-family:monospace; font-size:0.8rem;">
                    ${escapeHtml(sourceId)}
                </td>
                <td style="padding:6px 10px; color:#94a3b8; font-size:0.78rem;">
                    ${escapeHtml(item.reason || "—")}
                </td>
                <td style="padding:6px 10px; color:#e2e8f0; font-size:0.8rem;">
                    ${escapeHtml(item.warehouse || item.status || "—")}
                </td>
                <td style="padding:6px 10px; color:#64748b; font-size:0.78rem;">
                    ${escapeHtml(item.date || "—")}
                </td>
            </tr>
        `;
    }).join("");
}

function filterUnallocatedTable(query) {
    const q = (query || "").trim().toLowerCase();
    if (!q) {
        renderUnallocatedTable(cachedUnallocatedItems);
        return;
    }

    const filtered = cachedUnallocatedItems.filter(item => {
        const p = (item.plate || "").toLowerCase();
        const dp = (item.display_plate || "").toLowerCase();
        const src = (item.source_id || "").toLowerCase();
        const wh = (item.warehouse || "").toLowerCase();
        return p.includes(q) || dp.includes(q) || src.includes(q) || wh.includes(q);
    });

    renderUnallocatedTable(filtered);
}

function copyTextToClipboard(text) {
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text)
            .then(() => true)
            .catch(() => fallbackCopyText(text));
    }
    return Promise.resolve(fallbackCopyText(text));
}

function fallbackCopyText(text) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.left = "-999999px";
    textArea.style.top = "-999999px";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    let successful = false;
    try {
        successful = document.execCommand("copy");
    } catch (err) {
        successful = false;
    }
    document.body.removeChild(textArea);
    return successful;
}

async function copyUnallocatedRawPlates() {
    if (!cachedUnallocatedItems || cachedUnallocatedItems.length === 0) {
        if (typeof showToast === "function") {
            showToast("No unallocated plates found to copy.", "warning");
        }
        return;
    }

    const plates = cachedUnallocatedItems.map(item => item.plate || item.display_plate).filter(Boolean);
    if (plates.length === 0) {
        if (typeof showToast === "function") {
            showToast("No valid plate numbers found in unallocated list.", "warning");
        }
        return;
    }

    const text = plates.join("\n");
    const ok = await copyTextToClipboard(text);
    if (ok) {
        if (typeof showToast === "function") {
            showToast(`✓ Copied ${plates.length} raw plate numbers to clipboard for MVR!`, "success");
        }
    } else {
        if (typeof showToast === "function") {
            showToast("Failed to copy plates to clipboard.", "error");
        }
    }
}

async function copyMvrDocket() {
    try {
        let url = "/api/stock/mvr-docket/";
        if (reportCurrentDate) {
            url += `?date=${encodeURIComponent(reportCurrentDate)}`;
        }
        const resp = await fetch(url);
        const data = await resp.json();

        if (!data.success || !data.docket) {
            if (typeof showToast === "function") {
                showToast("Could not generate MVR Docket: " + (data.error || "Unknown error"), "error");
            }
            return;
        }

        const docketText = data.docket.formatted_docket;
        const count = data.docket.count || 0;
        const ok = await copyTextToClipboard(docketText);
        if (ok) {
            if (typeof showToast === "function") {
                showToast(`✓ Copied MVR Allocation Exception Docket (${count} unallocated) to clipboard!`, "success");
            }
        } else {
            if (typeof showToast === "function") {
                showToast("Failed to copy MVR Docket to clipboard.", "error");
            }
        }
    } catch (err) {
        console.error("Error copying MVR docket:", err);
        if (typeof showToast === "function") {
            showToast("Error generating MVR docket: " + err, "error");
        }
    }
}


function toggleShiftCsvMenu(event) {
    if (event) {
        event.stopPropagation();
    }
    const menu = document.getElementById("shift-csv-dropdown");
    if (!menu) return;
    const isShown = menu.style.display === "block";
    menu.style.display = isShown ? "none" : "block";

    if (!isShown) {
        const closeHandler = () => {
            menu.style.display = "none";
            document.removeEventListener("click", closeHandler);
        };
        setTimeout(() => document.addEventListener("click", closeHandler), 10);
    }
}

function downloadCategoryCsv(category) {
    const suf = reportCurrentDate || "today";
    const url = `/api/stock/export/category/${encodeURIComponent(category)}/?date=${encodeURIComponent(suf)}`;
    window.location.href = url;
}

function openShiftReconcileModal() {
    const sufInput = document.getElementById("shift-reconcile-suffix");
    if (sufInput) {
        sufInput.value = reportCurrentDate || "";
    }
    if (typeof openModal === "function") {
        openModal("modal-shift-reconcile");
    } else {
        const modal = document.getElementById("modal-shift-reconcile");
        if (modal) modal.classList.add("active");
    }
}

function onShiftInputCountChanged() {
    const input = document.getElementById("shift-reconcile-morning-input");
    const countEl = document.getElementById("shift-reconcile-morning-count");
    if (!input || !countEl) return;
    const raw = input.value || "";
    const lines = raw.split(/[\r\n,;]+/).map(s => s.trim()).filter(s => s.length > 0);
    const unique = new Set(lines.map(s => s.toUpperCase().replace(/\s+/g, "")));
    countEl.textContent = `${unique.size} unique plate(s) entered`;
}

async function submitShiftReconciliation() {
    const morningInput = document.getElementById("shift-reconcile-morning-input");
    const returnsInput = document.getElementById("shift-reconcile-returns-input");
    const suffixInput = document.getElementById("shift-reconcile-suffix");
    const operatorInput = document.getElementById("shift-reconcile-operator");
    const syncChk = document.getElementById("shift-reconcile-sync-chk");
    const runBtn = document.getElementById("btn-run-shift-reconcile");

    const payload = {
        morning_plates: morningInput ? morningInput.value : "",
        return_plates: returnsInput ? returnsInput.value : "",
        date_suffix: suffixInput ? suffixInput.value : "",
        operator_name: operatorInput ? operatorInput.value : "Operator",
        sync_itms: syncChk ? syncChk.checked : true,
    };

    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = "⏳ Reconciling & Updating Inventory...";
    }

    try {
        const resp = await fetch("/api/stock/shift/reconcile/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });

        const data = await resp.json();
        if (!data.success) {
            alert("Shift reconciliation failed: " + (data.error || "Unknown error"));
            return;
        }

        // Render results card
        const card = document.getElementById("shift-reconcile-results-card");
        if (card) {
            card.style.display = "flex";
            const s = data.summary || {};
            const dispEl = document.getElementById("shift-res-dispatched");
            const archEl = document.getElementById("shift-res-archived");
            const pendEl = document.getElementById("shift-res-pending");
            const retEl = document.getElementById("shift-res-returned");
            const unallocEl = document.getElementById("shift-res-unallocated");

            if (dispEl) dispEl.textContent = s.dispatched_count ?? 0;
            if (archEl) archEl.textContent = s.reconciled_installed_count ?? 0;
            if (pendEl) pendEl.textContent = s.on_line_active_count ?? 0;
            if (retEl) retEl.textContent = s.returned_count ?? 0;
            if (unallocEl) unallocEl.textContent = s.unallocated_count ?? 0;

            const cntUnalloc = document.getElementById("btn-cnt-unallocated");
            const cntArch = document.getElementById("btn-cnt-archived");
            const cntPend = document.getElementById("btn-cnt-pending");
            const cntDisp = document.getElementById("btn-cnt-dispatched");

            if (cntUnalloc) cntUnalloc.textContent = s.unallocated_count ?? 0;
            if (cntArch) cntArch.textContent = s.reconciled_installed_count ?? 0;
            if (cntPend) cntPend.textContent = s.on_line_active_count ?? 0;
            if (cntDisp) cntDisp.textContent = s.dispatched_count ?? 0;
        }

        if (typeof showToast === "function") {
            showToast(`✓ Shift reconciled! ${data.summary?.unallocated_count ?? 0} unallocated, ${data.summary?.reconciled_installed_count ?? 0} archived.`, "success");
        }

        // Refresh underlying totals in reports tab
        fetchReportTotals();
    } catch (err) {
        console.error("Error submitting shift reconciliation:", err);
        alert("Network or server error during reconciliation: " + err);
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = "🚀 Run Shift Reconciliation";
        }
    }
}

function downloadShiftCsvFromModal(category) {
    const suffixInput = document.getElementById("shift-reconcile-suffix");
    const suf = (suffixInput && suffixInput.value) ? suffixInput.value : (reportCurrentDate || "today");
    const url = `/api/stock/export/category/${encodeURIComponent(category)}/?date=${encodeURIComponent(suf)}`;
    window.location.href = url;
}

function openStockKitSyncModal() {
    refreshStockKitReadinessInModal();
    if (typeof openModal === "function") {
        openModal("modal-sync-stock-kits");
    } else {
        const modal = document.getElementById("modal-sync-stock-kits");
        if (modal) modal.classList.add("active");
    }
}

async function refreshStockKitReadinessInModal() {
    try {
        const resp = await fetch("/api/stock/kits/readiness/");
        const data = await resp.json();
        if (data.success && data.readiness) {
            const r = data.readiness;
            const elTotal = document.getElementById("sync-kits-val-total");
            const elNew = document.getElementById("sync-kits-val-new");
            const elAlloc = document.getElementById("sync-kits-val-alloc");
            const elInst = document.getElementById("sync-kits-val-inst");
            const elWh = document.getElementById("sync-kits-wh-name");
            const elBadge = document.getElementById("sync-kits-header-badge");

            if (elTotal) elTotal.textContent = r.total_kits ?? 0;
            if (elNew) elNew.textContent = r.new_unallocated ?? 0;
            if (elAlloc) elAlloc.textContent = r.allocated_orders ?? 0;
            if (elInst) elInst.textContent = r.installed_archived ?? 0;
            if (elWh && r.facility_name) elWh.textContent = r.facility_name.substring(0, 24);
            if (elBadge) {
                elBadge.textContent = `${r.new_unallocated ?? 0} Ready ('New')`;
            }
        }
    } catch (err) {
        console.warn("Could not load kit readiness:", err);
    }
}

function onSyncKitsInputCountChanged() {
    const input = document.getElementById("sync-kits-plates-input");
    const countEl = document.getElementById("sync-kits-input-count");
    if (!input || !countEl) return;
    const raw = input.value || "";
    const lines = raw.split(/[\r\n,;]+/).map(s => s.trim()).filter(s => s.length > 0);
    const unique = new Set(lines.map(s => s.toUpperCase().replace(/\s+/g, "")));
    countEl.textContent = `${unique.size} plate(s) entered`;
}

async function submitStockKitSync() {
    const input = document.getElementById("sync-kits-plates-input");
    const syncChk = document.getElementById("sync-kits-itms-chk");
    const runBtn = document.getElementById("btn-run-kits-sync");

    const payload = {
        plates: input ? input.value : "",
        sync_itms: syncChk ? syncChk.checked : true,
    };

    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = "⏳ Syncing & Provisioning...";
    }

    try {
        const resp = await fetch("/api/stock/kits/sync/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });

        const data = await resp.json();
        if (!data.success) {
            alert("Stock kit sync failed: " + (data.error || "Unknown error"));
            return;
        }

        const resCard = document.getElementById("sync-kits-results-card");
        if (resCard) {
            resCard.style.display = "flex";
            const resTitle = document.getElementById("sync-kits-res-title");
            const resTime = document.getElementById("sync-kits-res-time");
            const resMsg = document.getElementById("sync-kits-res-msg");
            const resSeries = document.getElementById("sync-kits-res-series");

            if (resTitle) resTitle.textContent = `✓ Provisioned ${data.result?.new_kits_ready_count ?? 0} Kits as 'New'`;
            if (resTime) resTime.textContent = `${data.result?.duration_ms ?? 0}ms`;
            if (resMsg) resMsg.textContent = data.result?.message || "Stock kits synchronized.";

            const seriesObj = data.result?.series_breakdown || {};
            const seriesStr = Object.entries(seriesObj).map(([s, c]) => `${s}: ${c}`).join(" │ ") || "All series ready";
            if (resSeries) resSeries.textContent = `Series Breakdown: ${seriesStr}`;
        }

        if (typeof showToast === "function") {
            showToast(`✓ Kits synced: ${data.result?.new_kits_ready_count ?? 0} ready as 'New'!`, "success");
        }

        // Refresh live stats in modal and reports pane
        refreshStockKitReadinessInModal();
        fetchReportTotals();
    } catch (err) {
        console.error("Error running stock kit sync:", err);
        alert("Network or server error during kit sync: " + err);
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = "⚡ Sync & Provision Kits";
        }
    }
}



