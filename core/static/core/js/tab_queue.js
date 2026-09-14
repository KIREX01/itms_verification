/**
 * ITMS CLOSING SYSTEM - TAB 4: REVIEW QUEUE & OPERATOR DECISION STUDIO
 * Controls motorcycle pair verification cards, front & rear comparison viewports,
 * manual plate overrides, and live order linking/unlinking.
 */

let currentQueueBatchFilter = "ALL";

function setQueueBatchFilter(batchVal) {
    currentQueueBatchFilter = batchVal;
    renderQueueCards();
}

async function fetchPairs() {
    try {
        const res = await fetch("/api/pairs/");
        const data = await res.json();
        if (data.success) {
            pairsData = data.pairs || [];
            
            // Populate batch filter options if available
            if (data.batches && data.batches.length > 0) {
                const select = document.getElementById("queue-batch-select");
                if (select) {
                    const currentVal = select.value || currentQueueBatchFilter || "ALL";
                    const latestId = data.latest_batch_id || (data.batches[0] ? data.batches[0].batch_id : "Latest");
                    select.innerHTML = `
                        <option value="ALL">📦 All Batches (Mixed Queue)</option>
                        <option value="LATEST">🔥 Latest Ingested Batch (${escapeHtml(latestId)})</option>
                        <option value="CARRYOVER">⏳ Carryover / Previous Batches</option>
                        ${data.batches.map(b => `<option value="${escapeHtml(b.batch_id)}">📦 ${escapeHtml(b.batch_id)} (${escapeHtml(b.created_at)})</option>`).join("")}
                    `;
                    select.value = currentVal;
                }
            }

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
        // Tab status filter
        if (currentQueueFilter === "PENDING" && p.verification_status !== "PENDING_REVIEW") return false;
        if (currentQueueFilter === "APPROVED" && p.verification_status !== "APPROVED") return false;
        if (currentQueueFilter === "SUBMITTED" && p.verification_status !== "SUBMITTED") return false;
        if (currentQueueFilter === "ISSUES" && !["INCOMPLETE", "CONFLICT", "FAILED", "UNREGISTERED"].includes(p.verification_status)) return false;

        // Batch filter
        if (currentQueueBatchFilter === "LATEST" && !p.is_latest_batch) return false;
        if (currentQueueBatchFilter === "CARRYOVER" && !p.is_carryover) return false;
        if (currentQueueBatchFilter !== "ALL" && currentQueueBatchFilter !== "LATEST" && currentQueueBatchFilter !== "CARRYOVER") {
            if (p.batch_id !== currentQueueBatchFilter) return false;
        }

        // Search text
        if (searchVal) {
            const plate = (p.registration_number_detected || "").toUpperCase();
            const orderNo = (p.order_number || (p.order ? p.order.order_number : "") || "").toUpperCase();
            const batchId = (p.batch_id || "").toUpperCase();
            return plate.includes(searchVal) || orderNo.includes(searchVal) || batchId.includes(searchVal);
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
        const orderNo = p.order ? `#${p.order.order_number}` : (p.order_number ? `#${p.order_number}` : "No order");
        const batchBadge = p.is_latest_batch
            ? `<span class="badge badge-green" style="font-size:0.58rem; padding:1px 5px;" title="Latest Ingested Batch">🔥 NEW</span>`
            : (p.is_carryover 
                ? `<span class="badge badge-yellow" style="font-size:0.58rem; padding:1px 5px;" title="Carryover from Previous Shift / Batch">⏳ PRIOR</span>`
                : `<span class="badge badge-muted" style="font-size:0.58rem; padding:1px 5px;">📦 BATCH</span>`);

        return `<div class="queue-item-card ${isSelected ? 'selected' : ''}" onclick="selectPair(${p.id})">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">
                <div style="display:flex; align-items:center; gap:6px;">
                    <span class="item-plate-text">${plate}</span>
                    ${batchBadge}
                </div>
                <span class="badge badge-yellow" style="font-size:0.65rem;">${status}</span>
            </div>
            <div style="display:flex; align-items:center; justify-content:space-between; font-size:0.75rem; color:var(--ug-text-dim);">
                <span>${escapeHtml(orderNo)}</span>
                <span style="font-size:0.7rem; font-family:var(--font-mono);">${escapeHtml(p.batch_id || '')}</span>
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

            // Update Batch Badge in Header
            const batchBadgeEl = document.getElementById("qdetail-batch-badge");
            if (batchBadgeEl) {
                if (p.is_latest_batch) {
                    batchBadgeEl.className = "badge badge-green";
                    batchBadgeEl.innerHTML = `🔥 Latest Batch: ${escapeHtml(p.batch_id || 'Current')}`;
                } else if (p.is_carryover) {
                    batchBadgeEl.className = "badge badge-yellow";
                    batchBadgeEl.innerHTML = `⏳ Carryover: ${escapeHtml(p.batch_id || 'Prior Shift')}`;
                } else {
                    batchBadgeEl.className = "badge badge-muted";
                    batchBadgeEl.innerHTML = `Batch: ${escapeHtml(p.batch_id || 'Unbatched')}`;
                }
            }

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
                imgFront.style.opacity = "1";
                imgFront.src = front.url;
                imgFront.style.display = "block";
                if (phFront) phFront.style.display = "none";
            } else {
                imgFront.src = "";
                imgFront.style.opacity = "1";
                imgFront.style.display = "none";
                if (phFront) phFront.style.display = "flex";
            }

            if (rear && rear.url) {
                imgRear.style.opacity = "1";
                imgRear.src = rear.url;
                imgRear.style.display = "block";
                if (phRear) phRear.style.display = "none";
            } else {
                imgRear.src = "";
                imgRear.style.opacity = "1";
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

function handlePhotoError(type) {
    const img = document.getElementById(`qimg-${type}`);
    const ph = document.getElementById(`q${type}-placeholder`);
    if (img) {
        img.style.display = "none";
        img.style.opacity = "1";
    }
    if (ph) ph.style.display = "flex";
}

async function executePairAction(action) {
    if (!selectedPairId) return;

    if (action === "submit") {
        if (!selectedPairDetail) return;
        if (!selectedPairDetail.order) {
            showToast("Cannot submit: vehicle pair has no linked ITMS order. Press L to link an order.", "warning");
            return;
        }
        const plate = selectedPairDetail.registration_number_detected || "Pair";
        const orderNo = selectedPairDetail.order.order_number || "—";
        const btnSubmit = document.getElementById("btn-submit-action");
        if (btnSubmit) {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = `🚀 Submitting ${escapeHtml(plate)}...`;
        }
        showTopSubmissionIndicator(`Submitting ${plate} (Order #${orderNo})...`);
        appendConsoleLog(`Submitting ${plate} (Order #${orderNo}) to ITMS...`, "info");

        try {
            const res = await fetch(`/api/pairs/${selectedPairId}/action/`, {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: "action=submit",
            });
            const data = await res.json();
            if (data.success) {
                showToast(`✓ Successfully submitted ${plate} to ITMS! Token: ${data.token || 'OK'}`, "success");
                appendConsoleLog(`✓ Successfully submitted ${plate} to ITMS! Token: ${data.token}`, "success");
                fetchStats();
                fetchPairs();
                selectPair(selectedPairId);
            } else {
                showToast(data.error || data.message || "Submission failed", "error");
                appendConsoleLog(`✕ Failed submitting ${plate}: ${data.error || data.message}`, "error");
            }
        } catch (err) {
            showToast("Submission error: " + err, "error");
        } finally {
            if (btnSubmit) {
                btnSubmit.disabled = false;
                btnSubmit.innerHTML = `🚀 Submit to ITMS <span class="hotkey">Enter</span>`;
            }
            hideTopSubmissionIndicator();
        }
        return;
    }

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

function updateLinkerBanner() {
    const currentText = document.getElementById("linker-current-order-text");
    const unlinkBtn = document.getElementById("btn-modal-unlink");
    if (!currentText) return;
    if (selectedPairDetail && selectedPairDetail.order) {
        currentText.innerHTML = `<strong style="color:var(--ug-yellow);">#${escapeHtml(selectedPairDetail.order.order_number)}</strong> (${escapeHtml(selectedPairDetail.order.registration_number)}) • VIN: ${escapeHtml(selectedPairDetail.order.vin || '—')} • <span class="badge badge-muted">${escapeHtml(selectedPairDetail.order.status || 'Active')}</span>`;
        if (unlinkBtn) unlinkBtn.style.display = "inline-flex";
    } else {
        currentText.innerText = "No order currently linked";
        if (unlinkBtn) unlinkBtn.style.display = "none";
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

    updateLinkerBanner();
    openModal("modal-edit-plate");
    const plateQuery = selectedPairDetail.registration_number_detected || "";
    const searchInput = document.getElementById("input-search-order");
    if (searchInput) searchInput.value = plateQuery;
    searchOrdersForLinking(plateQuery);
    setTimeout(() => document.getElementById("input-edit-plate").focus(), 100);
}

function openLinkOrderModal() {
    if (!selectedPairDetail) {
        showToast("Please select a pair first", "warning");
        return;
    }
    document.getElementById("input-edit-plate").value = selectedPairDetail.registration_number_detected || "";
    const statusSelect = document.getElementById("select-edit-status");
    if (statusSelect) statusSelect.value = selectedPairDetail.verification_status || "";
    const noteInput = document.getElementById("input-edit-note");
    if (noteInput) noteInput.value = selectedPairDetail.operator_note || "";

    updateLinkerBanner();
    openModal("modal-edit-plate");
    const plateQuery = selectedPairDetail.registration_number_detected || "";
    const searchInput = document.getElementById("input-search-order");
    if (searchInput) {
        searchInput.value = plateQuery;
        setTimeout(() => searchInput.focus(), 100);
    }
    searchOrdersForLinking(plateQuery);
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
            await selectPair(selectedPairId);
            updateLinkerBanner();
            fetchStats();
            fetchPairs();
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
                            <th style="width:130px;">Order #</th>
                            <th style="width:120px;">Plate</th>
                            <th style="width:140px;">VIN / Chassis</th>
                            <th style="width:90px;">Status</th>
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
            await selectPair(selectedPairId);
            updateLinkerBanner();
            fetchStats();
            fetchPairs();
        } else {
            showToast(data.error || "Linking failed", "error");
        }
    } catch (err) {
        showToast("Linking error: " + err, "error");
    }
}
