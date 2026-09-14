/**
 * ITMS CLOSING SYSTEM - TAB 5: HISTORY & AUDIT LOGS
 * Displays chronological verification audit events, filter chips,
 * and the interactive activity log inspection modal.
 */

function setHistoryFilter(filter) {
    currentHistoryFilter = filter;
    document.querySelectorAll(".history-chip").forEach(c => {
        c.classList.toggle("active", c.getAttribute("data-hfilter") === filter);
    });
    fetchHistoryEvents();
}

async function fetchHistoryEvents() {
    const tbody = document.getElementById("history-table-tbody");
    if (!tbody) return;
    try {
        const res = await fetch(`/api/history/?filter=${encodeURIComponent(currentHistoryFilter)}`);
        const data = await res.json();
        if (data.success && data.items) {
            historyItemsMap = {};
            data.items.forEach(item => {
                historyItemsMap[item.id] = item;
            });

            if (data.items.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:40px; color:var(--ug-text-dim);">No historical verification events matching '${escapeHtml(currentHistoryFilter)}'.</td></tr>`;
                return;
            }
            tbody.innerHTML = data.items.map(l => `
                <tr onclick="openHistoryModal('${escapeHtml(String(l.id))}')" style="cursor:pointer;" title="Click to view complete audit log">
                    <td style="font-size:0.75rem; color:var(--ug-text-dim); font-family:var(--font-mono);">${escapeHtml(l.timestamp)}</td>
                    <td><strong style="color:var(--ug-yellow); font-family:var(--font-mono);">${escapeHtml(l.plate)}</strong></td>
                    <td><span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ug-text-muted);">${escapeHtml(l.order_number)}</span></td>
                    <td><span class="badge badge-muted">${escapeHtml(l.action)}</span></td>
                    <td><span class="badge ${l.result === 'SUCCESS' ? 'badge-green' : (l.result === 'REVIEW' ? 'badge-yellow' : 'badge-red')}">${escapeHtml(l.result)}</span></td>
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

    // Evidence photos preview
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
