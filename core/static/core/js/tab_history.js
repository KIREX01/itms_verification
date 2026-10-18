/**
 * ITMS CLOSING SYSTEM - TAB 5: HISTORY & AUDIT LOGS
 * High-Velocity Verification & Audit Trail Explorer
 * Features: Mobile Touch Cards, In-Memory Fast Filter & Search,
 * Centralized Shift Synchronization, Responsive Pagination.
 */

var currentHistoryFilter = "ALL";
var currentHistoryDateScope = "TODAY";
var historySearchQuery = "";
var historyCurrentPage = 1;
var historyPageSize = 25;
var allHistoryEvents = [];
var filteredHistoryEvents = [];
var historyItemsMap = (typeof historyItemsMap !== "undefined") ? historyItemsMap : {};
var activeHistoryLog = (typeof activeHistoryLog !== "undefined") ? activeHistoryLog : null;

// Pickup any filter or date scope set before this script was loaded
if (typeof window !== "undefined") {
    if (window._pendingHistoryFilter) {
        currentHistoryFilter = window._pendingHistoryFilter;
        delete window._pendingHistoryFilter;
    }
    if (window._pendingHistoryDateScope) {
        currentHistoryDateScope = window._pendingHistoryDateScope;
        delete window._pendingHistoryDateScope;
    }
}

function setHistoryFilter(filter) {
    currentHistoryFilter = filter;
    document.querySelectorAll(".history-chip").forEach(c => {
        c.classList.toggle("active", c.getAttribute("data-hfilter") === filter);
    });
    // In-memory filter or backend fetch if backend filtering is preferred
    historyCurrentPage = 1;
    applyHistoryFiltering();
    renderHistoryData();
}

function setHistoryDateScope(dateScope) {
    currentHistoryDateScope = dateScope;
    const btnToday = document.getElementById("btn-hscope-today");
    const btnAll = document.getElementById("btn-hscope-all");
    if (btnToday) btnToday.classList.toggle("active", dateScope === "TODAY");
    if (btnAll) btnAll.classList.toggle("active", dateScope === "ALL");
    historyCurrentPage = 1;
    void fetchHistoryEvents();
}

function cycleHistoryDateScope() {
    const nextScope = (currentHistoryDateScope === "TODAY") ? "ALL" : "TODAY";
    setHistoryDateScope(nextScope);
    if (typeof showToast === "function") {
        showToast(`Audit Scope: ${nextScope === "TODAY" ? "Today's Shift" : "All Time"}`, "info");
    }
}

function onHistorySearchInput(query) {
    historySearchQuery = (query || "").trim().toLowerCase();
    const clearBtn = document.getElementById("btn-clear-history-search");
    if (clearBtn) {
        clearBtn.style.display = historySearchQuery ? "block" : "none";
    }
    historyCurrentPage = 1;
    applyHistoryFiltering();
    renderHistoryData();
}

function clearHistorySearch() {
    const input = document.getElementById("history-search-input");
    if (input) input.value = "";
    onHistorySearchInput("");
}

function setHistoryPageSize(val) {
    historyPageSize = (val === "ALL") ? 999999 : (parseInt(val, 10) || 25);
    historyCurrentPage = 1;
    renderHistoryData();
}

function changeHistoryPage(delta) {
    const totalPages = Math.ceil(filteredHistoryEvents.length / historyPageSize) || 1;
    const targetPage = historyCurrentPage + delta;
    if (targetPage >= 1 && targetPage <= totalPages) {
        historyCurrentPage = targetPage;
        renderHistoryData();
        // Scroll to top of table or card container
        const tableContainer = document.getElementById("history-table-container");
        if (tableContainer) tableContainer.scrollTop = 0;
        const cardsContainer = document.getElementById("history-mobile-cards-list");
        if (cardsContainer) cardsContainer.scrollTop = 0;
    }
}

function applyHistoryFiltering() {
    filteredHistoryEvents = allHistoryEvents.filter(item => {
        // 1. Filter Chip Matching
        if (currentHistoryFilter === "SUBMITTED") {
            if (item.action !== "SUBMIT" && item.action !== "SUBMIT_PAIR") return false;
        } else if (currentHistoryFilter === "FAILED") {
            const res = (item.result || "").toUpperCase();
            if (res !== "FAILURE" && res !== "FAILED") return false;
        } else if (currentHistoryFilter === "ISSUES") {
            const res = (item.result || "").toUpperCase();
            if (res === "SUCCESS" || res === "APPROVED") return false;
        } else if (currentHistoryFilter === "STOCK") {
            const act = (item.action || "").toUpperCase();
            const plt = (item.plate || "").toUpperCase();
            if (!act.includes("STOCK") && !act.includes("DELIVERY") && !act.includes("LEDGER") && !plt.startsWith("SHIFT_")) {
                return false;
            }
        } else if (currentHistoryFilter === "EMAIL") {
            const act = (item.action || "").toUpperCase();
            const ord = (item.order_number || "").toUpperCase();
            if (!act.includes("EMAIL") && ord !== "EMAIL_DISPATCH") {
                return false;
            }
        }

        // 2. Search Query Matching
        if (historySearchQuery) {
            const searchStr = `${item.plate || ""} ${item.order_number || ""} ${item.action || ""} ${item.operator || ""} ${item.message || ""} ${item.timestamp || ""}`.toLowerCase();
            if (!searchStr.includes(historySearchQuery)) return false;
        }

        return true;
    });
}

function renderHistoryData() {
    const totalItems = filteredHistoryEvents.length;
    const totalPages = Math.max(1, Math.ceil(totalItems / historyPageSize));
    if (historyCurrentPage > totalPages) historyCurrentPage = totalPages;

    const startIdx = (historyCurrentPage - 1) * historyPageSize;
    const endIdx = (historyPageSize >= 999999) ? totalItems : Math.min(startIdx + historyPageSize, totalItems);
    const pageItems = filteredHistoryEvents.slice(startIdx, endIdx);

    // Update summary and pagination controls
    const elSummary = document.getElementById("history-pagination-summary");
    if (elSummary) {
        if (totalItems === 0) {
            elSummary.textContent = "Showing 0 events";
        } else {
            elSummary.textContent = `Showing ${startIdx + 1}–${endIdx} of ${totalItems} audit events`;
        }
    }

    const elTotalBadge = document.getElementById("history-total-count-badge");
    if (elTotalBadge) {
        elTotalBadge.textContent = `${totalItems} Events Found`;
    }

    const elPageIndicator = document.getElementById("history-page-indicator");
    if (elPageIndicator) {
        elPageIndicator.textContent = `Page ${historyCurrentPage} of ${totalPages}`;
    }

    const btnPrev = document.getElementById("btn-hist-prev");
    const btnNext = document.getElementById("btn-hist-next");
    if (btnPrev) btnPrev.disabled = (historyCurrentPage <= 1);
    if (btnNext) btnNext.disabled = (historyCurrentPage >= totalPages || totalItems === 0);

    // 1. Render Desktop/Tablet Table
    const tbody = document.getElementById("history-table-tbody");
    if (tbody) {
        if (pageItems.length === 0) {
            const emptyHint = historySearchQuery
                ? `No audit logs matching search '${escapeHtml(historySearchQuery)}'.`
                : `No verification events in ${currentHistoryDateScope === "TODAY" ? "Today's shift" : "All Time"} matching '${escapeHtml(currentHistoryFilter)}'.`;
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:45px 20px; color:var(--ug-text-dim); font-size:0.88rem;">${emptyHint}</td></tr>`;
        } else {
            tbody.innerHTML = pageItems.map(l => {
                const todayBadge = l.is_today 
                    ? `<span class="badge badge-green" style="font-size:0.55rem; padding:1px 5px; margin-left:4px; font-weight:800;">TODAY</span>`
                    : "";
                const resClass = l.result === "SUCCESS" ? "badge-green" : ((l.result === "REVIEW" || l.result === "ISSUES") ? "badge-yellow" : "badge-red");
                return `
                <tr onclick="openHistoryModal('${escapeHtml(String(l.id))}')" style="cursor:pointer;" title="Click to view complete audit trail">
                    <td style="font-size:0.75rem; color:var(--ug-text-dim); font-family:var(--font-mono); white-space:nowrap;">${escapeHtml(l.timestamp)}</td>
                    <td>
                        <strong style="color:var(--ug-yellow); font-family:var(--font-mono); font-size:0.85rem;">${escapeHtml(l.plate)}</strong>
                        ${todayBadge}
                    </td>
                    <td><span style="font-family:var(--font-mono); font-size:0.78rem; color:#94a3b8;">${escapeHtml(l.order_number)}</span></td>
                    <td><span class="badge badge-muted" style="font-size:0.7rem; font-weight:700;">${escapeHtml(l.action)}</span></td>
                    <td><span class="badge ${resClass}" style="font-size:0.7rem; font-weight:800;">${escapeHtml(l.result)}</span></td>
                    <td style="font-size:0.8rem; color:#ffffff; font-weight:600;">${escapeHtml(l.operator_name || l.operator)}</td>
                    <td style="font-size:0.8rem; color:#ffffff; max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(l.message)}</td>
                </tr>`;
            }).join("");
        }
    }

    // 2. Render Mobile Touch Cards
    const cardsContainer = document.getElementById("history-mobile-cards-list");
    if (cardsContainer) {
        if (pageItems.length === 0) {
            cardsContainer.innerHTML = `
                <div style="text-align:center; padding:35px 15px; color:var(--ug-text-dim); background:#141720; border:1px solid var(--ug-border); border-radius:8px;">
                    <i class="fas fa-search" style="font-size:1.8rem; margin-bottom:8px; display:block; opacity:0.5;"></i>
                    <span>No audit events found for selected filters.</span>
                </div>`;
        } else {
            cardsContainer.innerHTML = pageItems.map(l => {
                const todayBadge = l.is_today 
                    ? `<span class="badge badge-green" style="font-size:0.55rem; padding:1px 4px; margin-left:4px;">TODAY</span>`
                    : "";
                const resClass = l.result === "SUCCESS" ? "badge-green" : ((l.result === "REVIEW" || l.result === "ISSUES") ? "badge-yellow" : "badge-red");
                return `
                <div class="history-card-item" onclick="openHistoryModal('${escapeHtml(String(l.id))}')" role="button" tabindex="0">
                    <div class="history-card-header">
                        <div style="display:flex; align-items:center;">
                            <strong style="color:var(--ug-yellow); font-family:var(--font-mono); font-size:0.95rem;">${escapeHtml(l.plate)}</strong>
                            ${todayBadge}
                        </div>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span class="badge ${resClass}" style="font-size:0.7rem; font-weight:800;">${escapeHtml(l.result)}</span>
                        </div>
                    </div>
                    <div class="history-card-meta">
                        <div>
                            <span class="badge badge-muted" style="font-size:0.68rem; font-weight:700;">${escapeHtml(l.action)}</span>
                            <span style="font-family:var(--font-mono); font-size:0.75rem; color:#94a3b8; margin-left:4px;">#${escapeHtml(l.order_number)}</span>
                        </div>
                        <div style="font-size:0.72rem; color:var(--ug-text-dim); font-family:var(--font-mono);">
                            ${escapeHtml(l.timestamp)}
                        </div>
                    </div>
                    <div class="history-card-msg">
                        ${escapeHtml(l.message)}
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.72rem; color:var(--ug-text-muted);">
                        <span><i class="fas fa-user-circle" style="margin-right:4px;"></i>${escapeHtml(l.operator_name || l.operator)}</span>
                        <span style="color:#38bdf8; font-weight:700;">Inspect Detail &rarr;</span>
                    </div>
                </div>`;
            }).join("");
        }
    }
}

async function fetchHistoryEvents() {
    const tbody = document.getElementById("history-table-tbody");
    const cardsContainer = document.getElementById("history-mobile-cards-list");
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:35px; color:var(--ug-text-muted);"><i class="fas fa-spinner fa-spin" style="margin-right:8px;"></i>Loading verification audit events...</td></tr>`;
    }
    if (cardsContainer) {
        cardsContainer.innerHTML = `<div style="text-align:center; padding:25px; color:var(--ug-text-muted);"><i class="fas fa-spinner fa-spin" style="margin-right:8px;"></i>Loading events...</div>`;
    }

    try {
        const activeDateScope = currentHistoryDateScope || "TODAY";
        const res = await fetch(`/api/history/?filter=ALL&date_scope=${encodeURIComponent(activeDateScope)}`);
        const data = await res.json();
        if (data.success && data.items) {
            historyItemsMap = {};
            allHistoryEvents = data.items;
            allHistoryEvents.forEach(item => {
                historyItemsMap[item.id] = item;
            });

            applyHistoryFiltering();
            renderHistoryData();
        } else {
            console.warn("Failed to load history items:", data);
        }
    } catch (err) {
        console.warn("History fetch error:", err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:35px; color:#ef4444;"><i class="fas fa-exclamation-triangle" style="margin-right:8px;"></i>Failed to load audit events. Please check connection and click Refresh.</td></tr>`;
        }
    }
}

function openHistoryModal(logId) {
    const log = historyItemsMap[logId];
    if (!log) return;
    activeHistoryLog = log;

    setText("hist-modal-title", `Verification Activity Log #${log.id}`);
    setText("hist-modal-time", log.timestamp || "--");
    setText("hist-modal-operator", log.operator_name || (log.operator ? log.operator.split(" (")[0] : "Operator"));
    setText("hist-modal-email", log.operator_email || "");

    const resultBadge = document.getElementById("hist-modal-result");
    if (resultBadge) {
        resultBadge.className = log.result === "SUCCESS" ? "badge badge-green" : ((log.result === "REVIEW" || log.result === "ISSUES") ? "badge badge-yellow" : "badge badge-red");
        resultBadge.innerText = log.result;
    }

    setText("hist-modal-action", log.action || "--");
    setText("hist-modal-plate", log.plate || "--");
    setText("hist-modal-order", log.order_number && log.order_number !== "N/A" ? `#${log.order_number} (${log.order_plate || ""})` : "Unlinked");
    setText("hist-modal-pair", log.pair_id ? `Pair #${log.pair_id} (${log.pair_status || ""})` : "None");
    setText("hist-modal-token", log.simulated_token || "None (Standard Verification)");
    setText("hist-modal-message", log.message || "--");

    // Evidence photos preview
    const photosContainer = document.getElementById("hist-modal-photos-container");
    const imgF = document.getElementById("hist-img-front");
    const imgR = document.getElementById("hist-img-rear");
    if (photosContainer && (log.front_url || log.rear_url || log.front_thumb_url || log.rear_thumb_url)) {
        photosContainer.style.display = "flex";
        if (imgF) {
            imgF.src = log.front_thumb_url || log.front_url || "";
            imgF.dataset.fullUrl = log.front_url || "";
        }
        if (imgR) {
            imgR.src = log.rear_thumb_url || log.rear_url || "";
            imgR.dataset.fullUrl = log.rear_url || "";
        }
    } else if (photosContainer) {
        photosContainer.style.display = "none";
    }

    // Jump to review queue button
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
    if (typeof jumpToPairInQueue === "function") {
        jumpToPairInQueue(pairId);
    }
}

function updateHistoryActiveShiftDisplay(activeShift) {
    if (!activeShift) return;
    const badge = document.getElementById("history-active-shift-badge");
    if (badge) {
        badge.textContent = activeShift.formatted_date ? `${activeShift.formatted_date} (${activeShift.suffix})` : (activeShift.display || activeShift.suffix || "Live Shift");
    }
    const csvBtn = document.getElementById("btn-hist-csv");
    if (csvBtn) {
        const sfx = activeShift.suffix || "";
        csvBtn.href = sfx ? `/api/export/report/?date=${encodeURIComponent(sfx)}` : "/api/export/report/";
    }
}

// React to global shift date synchronization events
window.addEventListener("shiftDateChanged", (e) => {
    const detail = e.detail;
    if (detail) {
        updateHistoryActiveShiftDisplay(detail);
        if (currentHistoryDateScope === "TODAY") {
            void fetchHistoryEvents();
        }
    }
});

// Auto-initialize when document loads or when pane is revealed
document.addEventListener("DOMContentLoaded", () => {
    if (window.activeShiftDateInfo) {
        updateHistoryActiveShiftDisplay(window.activeShiftDateInfo);
    }
    // Attach reliable delegated event listeners to history filter chips
    const chipContainer = document.querySelector(".history-filter-chips");
    if (chipContainer) {
        chipContainer.addEventListener("click", (e) => {
            const btn = e.target.closest(".history-chip");
            if (btn) {
                const targetFilter = btn.getAttribute("data-hfilter");
                if (targetFilter) {
                    setHistoryFilter(targetFilter);
                }
            }
        });
    }
    void fetchHistoryEvents();
});

// Explicit global window bindings for inline HTML event handlers
window.setHistoryFilter = setHistoryFilter;
window.setHistoryDateScope = setHistoryDateScope;
window.cycleHistoryDateScope = cycleHistoryDateScope;
window.onHistorySearchInput = onHistorySearchInput;
window.clearHistorySearch = clearHistorySearch;
window.setHistoryPageSize = setHistoryPageSize;
window.changeHistoryPage = changeHistoryPage;
window.applyHistoryFiltering = applyHistoryFiltering;
window.renderHistoryData = renderHistoryData;
window.fetchHistoryEvents = fetchHistoryEvents;
window.openHistoryModal = openHistoryModal;
window.jumpFromHistoryToQueue = jumpFromHistoryToQueue;
window.updateHistoryActiveShiftDisplay = updateHistoryActiveShiftDisplay;