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
    let normalized = (filter || "ALL").toUpperCase();
    if (normalized === "SUBMITTED") normalized = "SUBMISSIONS";
    if (normalized === "APPROVED") normalized = "APPROVALS";
    if (normalized === "OVERRIDE") normalized = "OVERRIDES";
    if (normalized === "FAILED") normalized = "ISSUES";
    currentHistoryFilter = normalized;

    document.querySelectorAll(".history-chip").forEach(c => {
        const chipFilter = (c.getAttribute("data-hfilter") || "").toUpperCase();
        const matches = (chipFilter === normalized) ||
            (normalized === "SUBMISSIONS" && chipFilter === "SUBMITTED") ||
            (normalized === "APPROVALS" && chipFilter === "APPROVED") ||
            (normalized === "OVERRIDES" && chipFilter === "OVERRIDE") ||
            (normalized === "ISSUES" && chipFilter === "FAILED");
        c.classList.toggle("active", matches);
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
    const btnTodayM = document.getElementById("btn-hscope-today-m");
    const btnAllM = document.getElementById("btn-hscope-all-m");

    if (btnToday) btnToday.classList.toggle("active", dateScope === "TODAY");
    if (btnAll) btnAll.classList.toggle("active", dateScope === "ALL");
    if (btnTodayM) btnTodayM.classList.toggle("active", dateScope === "TODAY");
    if (btnAllM) btnAllM.classList.toggle("active", dateScope === "ALL");

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
        const act = (item.action || "").toUpperCase();
        const res = (item.result || "").toUpperCase();
        const stat = (item.pair_status || "").toUpperCase();
        const plt = (item.plate || "").toUpperCase();
        const ord = (item.order_number || "").toUpperCase();

        // 1. Filter Chip Matching
        if (currentHistoryFilter === "SUBMISSIONS" || currentHistoryFilter === "SUBMITTED") {
            const isSub = act === "SUBMIT" || act === "SUBMIT_PAIR" || act === "ITMS_SUBMISSION" || stat === "SUBMITTED";
            if (!isSub) return false;
        } else if (currentHistoryFilter === "APPROVALS" || currentHistoryFilter === "APPROVED") {
            const isApp = act === "PAIR_APPROVAL" || act === "OPERATOR_APPROVE" || act === "APPROVE" || stat === "APPROVED";
            if (!isApp) return false;
        } else if (currentHistoryFilter === "OVERRIDES" || currentHistoryFilter === "OVERRIDE") {
            const isOvr = act === "MANUAL_OVERRIDE" || act === "OPERATOR_OVERRIDE" || act === "OPERATOR_SWAP" || 
                          act === "MANUAL_PLATE_ASSIGN" || act === "ORDER_PRIOR_CORRECT" || act === "IMAGE_REPLACED";
            if (!isOvr) return false;
        } else if (currentHistoryFilter === "ISSUES" || currentHistoryFilter === "FAILED") {
            const isIssue = res === "FAILURE" || res === "FAILED" || res === "ISSUES" ||
                            stat === "CONFLICT" || stat === "FAILED" || stat === "INCOMPLETE" || stat === "UNREGISTERED";
            if (!isIssue) return false;
        } else if (currentHistoryFilter === "STOCK" || currentHistoryFilter === "EMAIL") {
            const isStock = act.includes("STOCK") || act.includes("DELIVERY") || act.includes("LEDGER") || 
                            act.includes("EMAIL") || ord === "EMAIL_DISPATCH" || ord === "LEDGER_OVERRIDE" || plt.startsWith("SHIFT_");
            if (!isStock) return false;
        }

        // 2. Search Query Matching
        if (historySearchQuery) {
            const searchStr = `${item.plate || ""} ${item.order_number || ""} ${item.order_plate || ""} ${item.action || ""} ${item.operator || ""} ${item.operator_name || ""} ${item.message || ""} ${item.timestamp || ""}`.toLowerCase();
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
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:45px 20px; color:var(--ug-text-dim); font-size:0.88rem;">${emptyHint}</td></tr>`;
        } else {
            tbody.innerHTML = pageItems.map(l => {
                const todayBadge = l.is_today 
                    ? `<span class="badge badge-green" style="font-size:0.55rem; padding:1px 5px; margin-left:4px; font-weight:800;">TODAY</span>`
                    : "";
                const resClass = l.result === "SUCCESS" ? "badge-green" : ((l.result === "REVIEW" || l.result === "ISSUES") ? "badge-yellow" : "badge-red");

                // Dual evidence photos thumbnail cell
                const fUrl = l.front_url || "";
                const fThumb = l.front_thumb_url || fUrl;
                const rUrl = l.rear_url || "";
                const rThumb = l.rear_thumb_url || rUrl;
                let photosCell = `<span style="color:var(--ug-text-dim); font-size:0.75rem;">--</span>`;

                if (fThumb || rThumb) {
                    const fImgHtml = fThumb 
                        ? `<img src="${escapeHtml(fThumb)}" alt="Front" class="history-grid-thumb" title="Front Angle (Click to zoom)" onclick="if(typeof openLightbox==='function'){openLightbox('${escapeHtml(fUrl || fThumb)}');}else{window.open('${escapeHtml(fUrl || fThumb)}','_blank');} event.stopPropagation();" onerror="this.style.display='none';">`
                        : "";
                    const rImgHtml = rThumb 
                        ? `<img src="${escapeHtml(rThumb)}" alt="Rear" class="history-grid-thumb" title="Rear Angle (Click to zoom)" onclick="if(typeof openLightbox==='function'){openLightbox('${escapeHtml(rUrl || rThumb)}');}else{window.open('${escapeHtml(rUrl || rThumb)}','_blank');} event.stopPropagation();" onerror="this.style.display='none';">`
                        : "";
                    photosCell = `<div class="history-grid-thumbs-wrap">${fImgHtml}${rImgHtml}</div>`;
                }

                return `
                <tr onclick="openHistoryModal('${escapeHtml(String(l.id))}')" style="cursor:pointer;" title="Click to view complete audit trail">
                    <td style="font-size:0.75rem; color:var(--ug-text-dim); font-family:var(--font-mono); white-space:nowrap;">${escapeHtml(l.timestamp)}</td>
                    <td>
                        <strong style="color:var(--ug-yellow); font-family:var(--font-mono); font-size:0.85rem;">${escapeHtml(l.plate)}</strong>
                        ${todayBadge}
                    </td>
                    <td style="text-align:center;">${photosCell}</td>
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

                // Mobile evidence photos strip
                const fUrl = l.front_url || "";
                const fThumb = l.front_thumb_url || fUrl;
                const rUrl = l.rear_url || "";
                const rThumb = l.rear_thumb_url || rUrl;
                let mobilePhotosHtml = "";

                if (fThumb || rThumb) {
                    const fCard = fThumb
                        ? `<div class="history-card-photo-box" onclick="if(typeof openLightbox==='function'){openLightbox('${escapeHtml(fUrl || fThumb)}');}else{window.open('${escapeHtml(fUrl || fThumb)}','_blank');} event.stopPropagation();" title="Front Angle (Tap to zoom)">
                               <span class="history-photo-tag">FRONT</span>
                               <img src="${escapeHtml(fThumb)}" alt="Front" class="history-card-photo-img" onerror="this.parentElement.style.display='none';">
                           </div>`
                        : "";
                    const rCard = rThumb
                        ? `<div class="history-card-photo-box" onclick="if(typeof openLightbox==='function'){openLightbox('${escapeHtml(rUrl || rThumb)}');}else{window.open('${escapeHtml(rUrl || rThumb)}','_blank');} event.stopPropagation();" title="Rear Angle (Tap to zoom)">
                               <span class="history-photo-tag">REAR</span>
                               <img src="${escapeHtml(rThumb)}" alt="Rear" class="history-card-photo-img" onerror="this.parentElement.style.display='none';">
                           </div>`
                        : "";
                    mobilePhotosHtml = `<div class="history-card-photos-strip">${fCard}${rCard}</div>`;
                }

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
                    ${mobilePhotosHtml}
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
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:35px; color:var(--ug-text-muted);"><i class="fas fa-spinner fa-spin" style="margin-right:8px;"></i>Loading verification audit events...</td></tr>`;
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
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:35px; color:#ef4444;"><i class="fas fa-exclamation-triangle" style="margin-right:8px;"></i>Failed to load audit events. Please check connection and click Refresh.</td></tr>`;
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
    const boxF = document.getElementById("hist-box-front") || (imgF ? imgF.parentElement : null);
    const boxR = document.getElementById("hist-box-rear") || (imgR ? imgR.parentElement : null);

    const fUrl = log.front_url || "";
    const fThumb = log.front_thumb_url || fUrl;
    const rUrl = log.rear_url || "";
    const rThumb = log.rear_thumb_url || rUrl;

    const hasFront = Boolean(fThumb || fUrl);
    const hasRear = Boolean(rThumb || rUrl);

    if (photosContainer && (hasFront || hasRear)) {
        photosContainer.style.display = "flex";
        if (imgF && boxF) {
            if (hasFront) {
                boxF.style.display = "block";
                imgF.src = fThumb;
                imgF.dataset.fullUrl = fUrl || fThumb;
            } else {
                boxF.style.display = "none";
            }
        }
        if (imgR && boxR) {
            if (hasRear) {
                boxR.style.display = "block";
                imgR.src = rThumb;
                imgR.dataset.fullUrl = rUrl || rThumb;
            } else {
                boxR.style.display = "none";
            }
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