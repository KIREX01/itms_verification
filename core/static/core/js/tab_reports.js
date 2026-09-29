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
        elCat3Breakdown.textContent = `${unalloc.failed_linking_count || 0} failed pair links │ ${unalloc.stock_kits_count || 0} stock new kits`;
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
        const badgeClass = isPair ? "badge-red" : "badge-green";
        const badgeLabel = isPair ? "PAIR LINK FAILED" : "STOCK NEW KIT";
        const sourceId = isPair ? `Pair #${item.source_id}` : (item.source_id || "Kit");

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
