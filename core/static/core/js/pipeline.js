
// ============================================================================
// Concurrency Lock & Truthful Batch Submission Controllers (Section 16)
// ============================================================================
window.isGlobalSubmissionLocked = false;
window.globalSubmissionLockInfo = null;
window.lastBatchFailures = [];

window.updateSubmissionLockState = function(isLocked, lockInfo) {
    window.isGlobalSubmissionLocked = Boolean(isLocked);
    window.globalSubmissionLockInfo = lockInfo || null;

    const qAppBtn = document.getElementById("btn-queue-batch-submit");
    const qActionBtn = document.getElementById("btn-submit-action");
    const subConfirmBtn = document.getElementById("btn-sub-start-confirm");
    const headerPill = document.getElementById("header-submission-indicator");
    const headerText = document.getElementById("header-submission-text");

    if (isLocked) {
        const activePlate = lockInfo ? (lockInfo.registration_number || lockInfo.order_number || '') : '';
        const activeOp = lockInfo ? (lockInfo.operator || 'operator') : 'operator';
        const elapsed = lockInfo && lockInfo.elapsed_seconds ? Math.round(lockInfo.elapsed_seconds) : 0;
        const lockMsg = `🔒 Submitting ${activePlate || 'order'} by ${activeOp} (${elapsed}s)...`;

        if (!isBatchSubmitting) {
            if (qAppBtn) {
                qAppBtn.innerHTML = `🔒 Submitting ${activePlate || 'Order'} (${elapsed}s)...`;
                qAppBtn.disabled = true;
                qAppBtn.style.opacity = "0.7";
                qAppBtn.style.cursor = "not-allowed";
                qAppBtn.title = lockMsg;
            }
            if (qActionBtn) {
                qActionBtn.disabled = true;
                qActionBtn.style.opacity = "0.7";
                qActionBtn.title = lockMsg;
            }
            if (subConfirmBtn && !isBatchSubmitting) {
                subConfirmBtn.disabled = true;
                subConfirmBtn.style.opacity = "0.7";
                subConfirmBtn.title = lockMsg;
            }
            if (headerText) headerText.innerText = `🔒 Submitting ${activePlate || 'Order'} (${activeOp}) [${elapsed}s]`;
            if (headerPill) {
                headerPill.className = "badge badge-yellow";
                headerPill.style.display = "inline-flex";
                headerPill.title = `Submission lock active: ${activePlate} is currently being transmitted by ${activeOp}.`;
            }
        }
    } else {
        if (!isBatchSubmitting) {
            if (qActionBtn) {
                qActionBtn.disabled = false;
                qActionBtn.style.opacity = "1";
                qActionBtn.title = "Submit verified motorcycle pair to ITMS (Hotkey: Enter)";
            }
            if (subConfirmBtn) {
                subConfirmBtn.disabled = false;
                subConfirmBtn.style.opacity = "1";
            }
            if (headerPill && headerPill.className.includes("badge-yellow")) {
                headerPill.className = "badge badge-green";
                hideTopSubmissionIndicator();
            }
        }
    }
};

window.reviewConflictsFromSubmitModal = function() {
    closeSubmissionModal();
    if (typeof switchNavTab === "function") switchNavTab(4); // Switch to Queue Tab
    setTimeout(() => {
        if (typeof setWorkQueueFilter === "function") {
            setWorkQueueFilter("ISSUES");
        }
        if (typeof showToast === "function") {
            showToast("Work Queue filtered to unresolved conflicts/issues for operator review.", "warning");
        }
    }, 120);
};

window.jumpToFailedPairsInQueue = function(failedPairIds) {
    closeSubmissionModal();
    const ids = failedPairIds || window.lastBatchFailures.map(f => f.pairId);
    if (typeof switchNavTab === "function") switchNavTab(4); // Switch to Queue Tab
    setTimeout(() => {
        if (typeof setWorkQueueFilter === "function") {
            setWorkQueueFilter("ISSUES");
        }
        if (ids && ids.length > 0 && typeof selectPair === "function") {
            selectPair(ids[0]);
        }
        if (typeof showToast === "function") {
            showToast(`Displaying ${ids ? ids.length : 'failed'} pair(s) with submission exceptions in Review Queue.`, "warning");
        }
    }, 120);
};

/**
 * ITMS CLOSING SYSTEM - BACKGROUND PIPELINE & ACTIVITY CONSOLE
 * Triggers dual-stream joint vision, order sync, batch submissions,
 * progress polling, and terminal stream logging.
 */

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
            startPipelineSSE();
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
            startPipelineSSE();
        }
    } catch (err) {
        showToast("Sync error: " + err, "error");
    }
}

let batchSubmissionAborted = false;
let isBatchSubmitting = false;
let currentSubmissionTask = null; // { type: "single", pair: {...} } or { type: "batch", pairs: [...] }
let currentSubmissionDryRun = true;

/**
 * Fetches the latest system configuration from /api/settings/
 * to ensure submission mode strictly adheres to config.json.
 */
async function fetchLatestSubmissionSettings() {
    try {
        const res = await fetch("/api/settings/");
        const data = await res.json();
        if (data && data.dry_run !== undefined) {
            currentSubmissionDryRun = Boolean(data.dry_run);
            if (typeof updateDryRunBadges === "function") {
                updateDryRunBadges(currentSubmissionDryRun);
            }
            return currentSubmissionDryRun;
        }
    } catch (err) {
        console.warn("Could not refresh settings before submission:", err);
    }
    const headerBadge = document.getElementById("header-dryrun-badge");
    if (headerBadge) {
        currentSubmissionDryRun = headerBadge.innerText.includes("Dry Run");
    }
    return currentSubmissionDryRun;
}

/**
 * Updates UI labels and mode badge in the submission confirmation modal.
 */
function updateSubModalModeDisplay(isDry) {
    const modeBadge = document.getElementById("sub-modal-mode-badge");
    if (modeBadge) {
        modeBadge.innerText = isDry ? "SIMULATED (DRY RUN)" : "LIVE ITMS TRANSMISSION";
        modeBadge.className = isDry ? "badge badge-yellow" : "badge badge-red";
    }

    const dryChk = document.getElementById("sub-modal-dryrun-chk");
    if (dryChk) dryChk.checked = isDry;

    const desc = document.getElementById("sub-confirm-dryrun-desc");
    if (desc) {
        desc.innerText = isDry
            ? "Safe Simulation Active: Validates workflow without mutating records on stock.itms.ug."
            : "LIVE Transmission Active: Real photographic evidence and hardware state will be posted to stock.itms.ug.";
    }

    const btnConfirm = document.getElementById("btn-sub-start-confirm");
    if (btnConfirm && currentSubmissionTask) {
        if (currentSubmissionTask.type === "batch") {
            const count = (currentSubmissionTask.pairs || []).length;
            btnConfirm.innerHTML = isDry
                ? `🧪 Simulate All ${count} Orders [Enter]`
                : `🚀 Submit All ${count} Orders (Live) [Enter]`;
            btnConfirm.style.background = isDry ? "var(--ug-yellow)" : "var(--ug-green)";
            btnConfirm.style.borderColor = isDry ? "var(--ug-yellow)" : "var(--ug-green)";
            btnConfirm.style.color = isDry ? "#000" : "#fff";
        } else {
            btnConfirm.innerHTML = isDry
                ? "🧪 Simulate Submit [Enter]"
                : "🚀 Confirm Live Submit [Enter]";
            btnConfirm.style.background = isDry ? "var(--ug-yellow)" : "var(--ug-green)";
            btnConfirm.style.borderColor = isDry ? "var(--ug-yellow)" : "var(--ug-green)";
            btnConfirm.style.color = isDry ? "#000" : "#fff";
        }
    }
}

/**
 * Handles toggling the Dry-Run switch directly inside the submission modal.
 * Persists the preference to config.json via /api/settings/toggle-dry-run/.
 */
async function onSubModalDryRunToggled() {
    const dryChk = document.getElementById("sub-modal-dryrun-chk");
    if (!dryChk) return;
    const isDry = dryChk.checked;
    currentSubmissionDryRun = isDry;
    updateSubModalModeDisplay(isDry);
    if (typeof updateDryRunBadges === "function") {
        updateDryRunBadges(isDry);
    }

    try {
        const res = await fetch("/api/settings/toggle-dry-run/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `dry_run=${isDry}`,
        });
        const data = await res.json();
        if (data && data.success) {
            showToast(`Submission safety mode updated: ${isDry ? 'DRY-RUN SIMULATION' : 'LIVE TRANSMISSION'}`, "info");
        }
    } catch (err) {
        console.warn("Error persisting modal dry-run toggle:", err);
    }
}

/**
 * Opens pre-submission review & confirmation modal for a SINGLE pair.
 */
async function openSingleSubmissionModal(pair) {
    if (!pair) return;
    if (!pair.order && !pair.order_number) {
        showToast("Cannot submit: vehicle pair has no linked ITMS order. Press L to link an order.", "warning");
        return;
    }

    // Refresh live config from config.json
    const isDry = await fetchLatestSubmissionSettings();
    currentSubmissionTask = { type: "single", pair: pair };

    const plate = pair.registration_number_detected || "Pair #" + pair.id;
    const orderNo = pair.order ? pair.order.order_number : (pair.order_number || "—");

    setText("sub-modal-title", "🚀 Single Pair ITMS Submission");
    setText("sub-confirm-scope-title", "SINGLE VEHICLE TARGET");
    setText("sub-confirm-count-badge", "1 Order Ready");
    setText("sub-confirm-target-main", plate);
    setText("sub-confirm-target-sub", `Linked Order #${orderNo} • Evidence: Front & Rear photos verified`);

    const batchListBox = document.getElementById("sub-confirm-batch-list-box");
    if (batchListBox) batchListBox.style.display = "none";

    updateSubModalModeDisplay(isDry);

    // Switch phases: Show confirm phase, hide progress phase
    const pConfirm = document.getElementById("sub-phase-confirm");
    const pProgress = document.getElementById("sub-phase-progress");
    if (pConfirm) pConfirm.style.display = "flex";
    if (pProgress) pProgress.style.display = "none";

    openModal("modal-submission-progress");
}

/**
 * Opens pre-submission review & confirmation modal for ALL approved pairs (Batch).
 */
async function triggerBatchSubmit(requestedScope) {
    if (isBatchSubmitting) {
        reopenSubmissionModal();
        return;
    }

    if (window.isGlobalSubmissionLocked && !isBatchSubmitting) {
        const lockInfo = window.globalSubmissionLockInfo;
        const activePlate = lockInfo ? (lockInfo.registration_number || lockInfo.order_number || '') : '';
        const activeOp = lockInfo ? (lockInfo.operator || 'another operator') : 'another operator';
        showToast(`Another submission is currently in progress (${activePlate} by ${activeOp}). Please wait for it to complete.`, "warning");
        return;
    }

    const scope = requestedScope || (typeof currentQueueDateScope !== "undefined" ? currentQueueDateScope : "TODAY");

    try {
        // Fetch all pairs in the requested scope to detect discrepancies (approved vs unapproved)
        const res = await fetch(`/api/pairs/?scope=${encodeURIComponent(scope)}&limit=500`);
        const data = await res.json();
        const allScopePairs = data.pairs || [];
        const approvedPairs = allScopePairs.filter(p => p.verification_status === "APPROVED");
        const totalInScope = allScopePairs.length;
        const approvedInScope = approvedPairs.length;
        const unapprovedCount = totalInScope - approvedInScope;

        if (approvedPairs.length === 0) {
            if (scope === "TODAY" && data.carryover_approved_count > 0) {
                showToast(`No approved pairs in Today's shift. Found ${data.carryover_approved_count} in Prior Carryover (switch Date Scope to Prior Carryover).`, "warning");
            } else {
                showToast(`No approved pairs ready for submission in ${scope} scope (${unapprovedCount} pair(s) in review/issues). Please approve pairs in Review Queue [A].`, "info");
            }
            return;
        }

        // Refresh live config from config.json
        const isDry = await fetchLatestSubmissionSettings();
        currentSubmissionTask = {
            type: "batch",
            pairs: approvedPairs,
            scope: scope,
            totalInScope: totalInScope,
            approvedInScope: approvedInScope,
            unapprovedCount: unapprovedCount,
        };

        setText("sub-modal-title", `🚀 Batch ITMS Submission (${scope})`);
        setText("sub-confirm-scope-title", `SCOPE: ${scope} TRANSMISSION`);
        setText("sub-confirm-count-badge", `${approvedPairs.length} Orders Ready`);
        setText("sub-confirm-target-main", `${approvedPairs.length} Approved Orders (${scope})`);
        setText("sub-confirm-target-sub", `All verified motorcycle pairs within the '${scope}' date scope will be sequentially submitted to ITMS.`);

        // Pre-Submission Physical Discrepancy Banner (Section 16.3 B)
        const discBanner = document.getElementById("sub-confirm-discrepancy-banner");
        const btnReviewConflicts = document.getElementById("btn-sub-review-conflicts");
        if (discBanner) {
            if (unapprovedCount > 0) {
                setText("sub-discrepancy-approved-count", approvedInScope);
                setText("sub-discrepancy-total-count", totalInScope);
                setText("sub-discrepancy-unapproved-count", unapprovedCount);
                discBanner.style.display = "block";
                if (btnReviewConflicts) btnReviewConflicts.style.display = "inline-flex";
            } else {
                discBanner.style.display = "none";
                if (btnReviewConflicts) btnReviewConflicts.style.display = "none";
            }
        }

        // Reset any leftover exception summary elements
        const exSummary = document.getElementById("sub-exception-summary");
        const btnJumpFailed = document.getElementById("btn-sub-jump-failed");
        if (exSummary) exSummary.style.display = "none";
        if (btnJumpFailed) btnJumpFailed.style.display = "none";

        const batchListBox = document.getElementById("sub-confirm-batch-list-box");
        if (batchListBox) {
            batchListBox.style.display = "flex";
            batchListBox.innerHTML = approvedPairs.slice(0, 50).map((p, idx) => {
                const pPlate = p.registration_number_detected || "Pair #" + p.id;
                const pOrder = p.order ? p.order.order_number : (p.order_number || "—");
                const shiftTag = p.is_latest_batch ? '🔥' : (p.is_today ? '●' : '⏳');
                return `<div style="display:flex; justify-content:space-between; align-items:center; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                    <span style="color:var(--ug-yellow); font-weight:700;">${shiftTag} #${idx+1} ${escapeHtml(pPlate)}</span>
                    <span style="color:#ffffff;">Order #${escapeHtml(pOrder)}</span>
                    <span style="color:var(--ug-green); font-size:0.75rem;">✓ Ready</span>
                </div>`;
            }).join("") + (approvedPairs.length > 50 ? `<div style="color:var(--ug-text-dim); text-align:center; padding-top:4px;">...and ${approvedPairs.length - 50} more pairs</div>` : "");
        }

        updateSubModalModeDisplay(isDry);

        // Switch phases: Show confirm phase, hide progress phase
        const pConfirm = document.getElementById("sub-phase-confirm");
        const pProgress = document.getElementById("sub-phase-progress");
        if (pConfirm) pConfirm.style.display = "flex";
        if (pProgress) pProgress.style.display = "none";

        openModal("modal-submission-progress");
    } catch (err) {
        showToast("Error loading batch items: " + err, "error");
    }
}

/**
 * Executes the active submission task (Single or Batch) with full telemetry.
 */
async function startSubmissionExecution() {
    if (!currentSubmissionTask) return;

    const isDryRun = currentSubmissionDryRun;
    batchSubmissionAborted = false;
    isBatchSubmitting = true;

    // Transition from Phase 1 (Confirm) to Phase 2 (Progress)
    const pConfirm = document.getElementById("sub-phase-confirm");
    const pProgress = document.getElementById("sub-phase-progress");
    if (pConfirm) pConfirm.style.display = "none";
    if (pProgress) pProgress.style.display = "flex";

    setText("sub-modal-title", isDryRun ? "🧪 Simulated ITMS Submission in Progress" : "🚀 Live ITMS Transmission in Progress");

    const modeBadge = document.getElementById("sub-modal-mode-badge");
    if (modeBadge) {
        modeBadge.innerText = isDryRun ? "SIMULATED (DRY RUN)" : "LIVE ITMS TRANSMISSION";
        modeBadge.className = isDryRun ? "badge badge-yellow" : "badge badge-red";
    }

    const btnAbort = document.getElementById("btn-sub-abort");
    if (btnAbort) {
        btnAbort.style.display = "inline-flex";
        btnAbort.disabled = false;
        btnAbort.innerText = "⏹ Stop / Abort";
    }
    const btnDone = document.getElementById("btn-sub-done");
    if (btnDone) btnDone.style.display = "none";

    const logStream = document.getElementById("sub-live-log-stream");
    if (logStream) logStream.innerHTML = "";

    const progressBar = document.getElementById("sub-progress-bar-fill");
    if (progressBar) progressBar.style.width = "0%";

    if (currentSubmissionTask.type === "single") {
        const p = currentSubmissionTask.pair;
        const plate = p.registration_number_detected || "Pair #" + p.id;
        const orderNo = p.order ? p.order.order_number : (p.order_number || "—");

        setText("sub-kpi-total", 1);
        setText("sub-kpi-success", 0);
        setText("sub-kpi-failed", 0);
        setText("sub-kpi-remaining", 1);
        setText("sub-progress-percent", "0%");
        setText("sub-item-index-badge", "1 / 1");

        setText("sub-active-plate", plate);
        setText("sub-active-order", `Order #${orderNo}`);
        setText("sub-status-desc", `Transmitting verification photos to ITMS (${isDryRun ? 'Dry Run' : 'Live'})...`);

        appendSubmissionLog(`[1/1] Submitting ${plate} (Order #${orderNo}) [${isDryRun ? 'DRY-RUN' : 'LIVE'}]...`, "info");
        showTopSubmissionIndicator(`Submitting ${plate}...`);

        let success = false;
        try {
            const subRes = await fetch(`/api/pairs/${p.id}/action/`, {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: `action=submit&dry_run=${isDryRun}`,
            });
            let subData = {};
            try {
                subData = await subRes.json();
            } catch (jsonErr) {
                subData = { success: false, error: "Invalid response from server." };
            }

            if (subRes.status === 409 || subData.code === "SUBMISSION_IN_PROGRESS") {
                const lockMsg = subData.error || subData.message || "Another submission is currently in progress.";
                setText("sub-kpi-failed", 0);
                setText("sub-status-desc", `Submission paused: ${lockMsg}`);
                setText("sub-active-plate", "Transmission Locked");
                appendSubmissionLog(`⚠️ Conflict: ${lockMsg}`, "warning");
                showToast(lockMsg, "warning");
                isBatchSubmitting = false;
                if (btnAbort) btnAbort.style.display = "none";
                if (btnDone) btnDone.style.display = "inline-flex";
                hideTopSubmissionIndicator();
                if (typeof fetchStats === "function") fetchStats();
                return;
            }

            if (subData.success) {
                success = true;
                setText("sub-kpi-success", 1);
                setText("sub-kpi-remaining", 0);
                setText("sub-progress-percent", "100%");
                if (progressBar) progressBar.style.width = "100%";
                setText("sub-status-desc", `Successfully submitted to ITMS! Token: ${subData.token || 'OK'}`);
                appendSubmissionLog(`✓ Success: ${plate} submitted! Receipt: ${subData.token || 'OK'}`, "success");
                showToast(`✓ Successfully submitted ${plate} (${isDryRun ? 'Dry Run' : 'Live'})!`, "success");
            } else {
                setText("sub-kpi-failed", 1);
                setText("sub-kpi-remaining", 0);
                setText("sub-progress-percent", "100%");
                if (progressBar) progressBar.style.width = "100%";
                setText("sub-status-desc", `Submission failed: ${subData.error || subData.message}`);
                appendSubmissionLog(`✕ Failed: ${plate} - ${subData.error || subData.message}`, "error");
                showToast(`Submission failed for ${plate}: ${subData.error || subData.message}`, "error");
            }
        } catch (netErr) {
            setText("sub-kpi-failed", 1);
            setText("sub-kpi-remaining", 0);
            setText("sub-progress-percent", "100%");
            setText("sub-status-desc", `Request error: ${netErr}`);
            appendSubmissionLog(`✕ Request error on ${plate}: ${netErr}`, "error");
            showToast(`Request error: ${netErr}`, "error");
        }

        isBatchSubmitting = false;
        if (btnAbort) btnAbort.style.display = "none";
        if (btnDone) btnDone.style.display = "inline-flex";

        showTopSubmissionIndicator(`✓ Done: ${plate} ${success ? 'submitted' : 'failed'}`);
        setTimeout(() => {
            if (!isBatchSubmitting) hideTopSubmissionIndicator();
        }, 5000);

        fetchStats();
        fetchPairs();
        if (typeof fetchHistoryEvents === "function" && activeNavTab === 5) {
            fetchHistoryEvents();
        }

    } else if (currentSubmissionTask.type === "batch") {
        const approvedPairs = currentSubmissionTask.pairs || [];
        const total = approvedPairs.length;

        setText("sub-kpi-total", total);
        setText("sub-kpi-success", 0);
        setText("sub-kpi-failed", 0);
        setText("sub-kpi-remaining", total);
        setText("sub-progress-percent", "0%");
        setText("sub-item-index-badge", `1 / ${total}`);

        appendSubmissionLog(`Initiated batch submission for ${total} verified pairs (${isDryRun ? 'DRY-RUN' : 'LIVE'})...`, "info");
        showTopSubmissionIndicator(`Submitting 0/${total} (0%)`);

        let succeeded = 0;
        let failed = 0;
        let batchFailures = [];
        window.lastBatchFailures = [];

        for (let i = 0; i < total; i++) {
            if (batchSubmissionAborted) {
                appendSubmissionLog("⏹ Batch submission process aborted by operator.", "warning");
                break;
            }

            const p = approvedPairs[i];
            const plate = p.registration_number_detected || "Pair #" + p.id;
            const orderNo = p.order ? p.order.order_number : (p.order_number || "—");
            const currentIdx = i + 1;

            setText("sub-item-index-badge", `${currentIdx} / ${total}`);
            setText("sub-active-plate", plate);
            setText("sub-active-order", `Order #${orderNo}`);
            setText("sub-status-desc", `Transmitting verification photos to ITMS (${isDryRun ? 'Dry Run' : 'Live'})...`);

            const currentPct = Math.round(((i) / total) * 100);
            setText("sub-progress-percent", `${currentPct}%`);
            if (progressBar) progressBar.style.width = `${currentPct}%`;

            showTopSubmissionIndicator(`Submitting ${currentIdx}/${total}: ${plate} (${currentPct}%)`);
            appendSubmissionLog(`[${currentIdx}/${total}] Submitting ${plate} (Order #${orderNo})...`, "info");

            try {
                const subRes = await fetch(`/api/pairs/${p.id}/action/`, {
                    method: "POST",
                    headers: { "Content-Type": "application/x-www-form-urlencoded" },
                    body: `action=submit&dry_run=${isDryRun}`,
                });
                let subData = {};
                try {
                    subData = await subRes.json();
                } catch (jsonErr) {
                    subData = { success: false, error: "Invalid response from server." };
                }

                // Section 16.3 A: Mutual Exclusion Lock Conflict (HTTP 409)
                if (subRes.status === 409 || subData.code === "SUBMISSION_IN_PROGRESS") {
                    const lockMsg = subData.error || subData.message || "Another submission is currently in progress.";
                    appendSubmissionLog(`⚠️ [${currentIdx}/${total}] Submission Lock Conflict: ${lockMsg}`, "warning");
                    showToast(lockMsg, "warning");

                    // Do NOT count as failed; stop further execution safely so operator can wait
                    isBatchSubmitting = false;
                    if (btnAbort) btnAbort.style.display = "none";
                    if (btnDone) btnDone.style.display = "inline-flex";
                    setText("sub-status-desc", `Batch paused due to active lock: ${lockMsg}`);
                    setText("sub-active-plate", "Transmission Locked");
                    hideTopSubmissionIndicator();
                    if (typeof fetchStats === "function") fetchStats();
                    return;
                }

                if (subData.success) {
                    succeeded++;
                    setText("sub-kpi-success", succeeded);
                    appendSubmissionLog(`✓ [${currentIdx}/${total}] Success: ${plate} submitted & archived! Token: ${subData.token || 'OK'}`, "success");
                } else {
                    failed++;
                    setText("sub-kpi-failed", failed);
                    const errMsg = subData.error || subData.message || 'Error';
                    const isOutbox = Boolean(subData.queued_outbox || errMsg.includes("Offline Outbox"));
                    batchFailures.push({
                        pairId: p.id,
                        plate: plate,
                        orderNumber: orderNo,
                        error: errMsg,
                        isOutbox: isOutbox
                    });
                    appendSubmissionLog(`✕ [${currentIdx}/${total}] Failed: ${plate} - ${errMsg}`, "error");
                }
            } catch (netErr) {
                failed++;
                setText("sub-kpi-failed", failed);
                const errMsg = String(netErr);
                batchFailures.push({
                    pairId: p.id,
                    plate: plate,
                    orderNumber: orderNo,
                    error: errMsg,
                    isOutbox: false
                });
                appendSubmissionLog(`✕ [${currentIdx}/${total}] Request error on ${plate}: ${errMsg}`, "error");
            }

            const remaining = total - (i + 1);
            setText("sub-kpi-remaining", remaining);
            const stepPct = Math.round(((i + 1) / total) * 100);
            setText("sub-progress-percent", `${stepPct}%`);
            if (progressBar) progressBar.style.width = `${stepPct}%`;
        }

        isBatchSubmitting = false;
        window.lastBatchFailures = batchFailures;

        const unapprovedCount = (currentSubmissionTask && currentSubmissionTask.unapprovedCount) || 0;
        const btnJumpFailed = document.getElementById("btn-sub-jump-failed");
        const exSummary = document.getElementById("sub-exception-summary");
        const exList = document.getElementById("sub-exception-list");

        // Section 16.3 C: Truthful Completion State & Exceptions Breakdown
        if (batchSubmissionAborted) {
            setText("sub-status-desc", `Batch aborted by operator. ${succeeded} succeeded, ${failed} failed.`);
            setText("sub-active-plate", "Submission Aborted");
            const modeBadge = document.getElementById("sub-modal-mode-badge");
            if (modeBadge) {
                modeBadge.innerText = "ABORTED BY OPERATOR";
                modeBadge.className = "badge badge-yellow";
            }
        } else if (failed > 0) {
            // Exceptions Encountered (Amber/Red Truthful Header)
            const outboxCount = batchFailures.filter(f => f.isOutbox).length;
            const outboxMsg = outboxCount > 0 ? ` (${outboxCount} in offline outbox)` : '';
            setText("sub-status-desc", `⚠️ Batch Completed with Exceptions: ${succeeded} Succeeded, ${failed} Failed${outboxMsg}.`);
            setText("sub-active-plate", `${succeeded}/${total} Succeeded • ${failed} Failed`);
            setText("sub-item-index-badge", `${total} / ${total}`);

            const modeBadge = document.getElementById("sub-modal-mode-badge");
            if (modeBadge) {
                modeBadge.innerText = "COMPLETED WITH EXCEPTIONS";
                modeBadge.className = "badge badge-red";
            }

            // Populate exception list panel
            if (exSummary && exList) {
                exList.innerHTML = batchFailures.map(f => `
                    <div style="padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.06); display:flex; justify-content:space-between; align-items:center; gap:8px;">
                        <div>
                            <strong style="color:var(--ug-yellow);">${escapeHtml(f.plate)}</strong>
                            <span style="color:rgba(255,255,255,0.6); font-size:0.7rem;"> (Order #${escapeHtml(f.orderNumber || '—')})</span>
                        </div>
                        <span style="color:#fca5a5; text-align:right;">${escapeHtml(f.error)}</span>
                    </div>
                `).join("");
                exSummary.style.display = "block";
            }

            if (btnJumpFailed) {
                btnJumpFailed.style.display = "inline-flex";
                btnJumpFailed.innerHTML = `🔍 Jump to Failed Pairs (${failed})`;
                btnJumpFailed.onclick = () => jumpToFailedPairsInQueue(batchFailures.map(f => f.pairId));
            }
        } else if (unapprovedCount > 0) {
            // Approved subset succeeded, but unapproved pairs remain in queue
            setText("sub-status-desc", `✓ All ${succeeded} approved pairs submitted successfully! Note: ${unapprovedCount} unapproved pair(s) remain in Queue.`);
            setText("sub-active-plate", `Approved Subset Complete (${succeeded})`);
            setText("sub-item-index-badge", `${total} / ${total}`);

            const modeBadge = document.getElementById("sub-modal-mode-badge");
            if (modeBadge) {
                modeBadge.innerText = "PARTIAL BATCH SUBMITTED";
                modeBadge.className = "badge badge-yellow";
            }
            if (exSummary) exSummary.style.display = "none";
            if (btnJumpFailed) {
                btnJumpFailed.style.display = "inline-flex";
                btnJumpFailed.innerHTML = `🔍 Review ${unapprovedCount} Remaining Conflicts`;
                btnJumpFailed.onclick = reviewConflictsFromSubmitModal;
            }
        } else {
            // 100% Truthful Green Completion
            setText("sub-status-desc", `🎉 100% Batch Complete! All ${succeeded} pairs submitted and verified in ITMS.`);
            setText("sub-active-plate", "100% Batch Complete");
            setText("sub-item-index-badge", `${total} / ${total}`);

            const modeBadge = document.getElementById("sub-modal-mode-badge");
            if (modeBadge) {
                modeBadge.innerText = "100% VERIFIED & SUBMITTED";
                modeBadge.className = "badge badge-green";
            }
            if (exSummary) exSummary.style.display = "none";
            if (btnJumpFailed) btnJumpFailed.style.display = "none";
        }

        if (btnAbort) btnAbort.style.display = "none";
        if (btnDone) btnDone.style.display = "inline-flex";

        showTopSubmissionIndicator(`✓ Done: ${succeeded} submitted, ${failed} failed`);
        setTimeout(() => {
            if (!isBatchSubmitting) hideTopSubmissionIndicator();
        }, 5000);

        showToast(`Batch submission finished: ${succeeded} succeeded, ${failed} failed.`, succeeded > 0 ? "success" : "warning");
        fetchStats();
        fetchPairs();
        if (typeof fetchHistoryEvents === "function" && activeNavTab === 5) {
            fetchHistoryEvents();
        }
    }
}

function abortBatchSubmission() {
    if (!isBatchSubmitting) return;
    batchSubmissionAborted = true;
    appendSubmissionLog("Aborting batch transmission after current item completes...", "warning");
    const btnAbort = document.getElementById("btn-sub-abort");
    if (btnAbort) {
        btnAbort.disabled = true;
        btnAbort.innerText = "Aborting...";
    }
}

function appendSubmissionLog(msg, type = "info") {
    const stream = document.getElementById("sub-live-log-stream");
    if (!stream) return;
    const timeStr = new Date().toTimeString().split(" ")[0];
    const div = document.createElement("div");
    let color = "#ffffff";
    if (type === "success") color = "var(--ug-green)";
    else if (type === "error") color = "var(--ug-red)";
    else if (type === "warning") color = "var(--ug-yellow)";
    div.innerHTML = `<span style="color:var(--ug-text-dim);">[${timeStr}]</span> <span style="color:${color}; font-weight:600;">${escapeHtml(msg)}</span>`;
    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
}

let pipelineEventSource = null;

function handlePipelineStatusUpdate(st) {
    const qInd = document.getElementById("queue-processing-indicator");
    const qTxt = document.getElementById("queue-processing-text");
    if (st.running) {
        if (qInd) qInd.style.display = "flex";
        if (qTxt) qTxt.innerText = `Vision: ${st.stage || 'Processing'} (${st.progress || 0}%)`;
        appendConsoleLog(`Stage: ${st.stage || 'Processing'} (${st.progress || 0}%) - ${st.message || ''}`, "info");
    } else {
        if (qInd) qInd.style.display = "none";
        appendConsoleLog(`Pipeline task finished: ${st.message || 'Complete'}`, st.success ? "success" : "error");
        fetchStats();
        fetchPairs();
    }
}

function startPipelineSSE() {
    if (!window.EventSource) {
        startPipelinePolling();
        return;
    }
    if (pipelineEventSource) {
        pipelineEventSource.close();
        pipelineEventSource = null;
    }
    try {
        pipelineEventSource = new EventSource("/api/stream/events/");
        pipelineEventSource.onmessage = function (event) {
            try {
                const data = JSON.parse(event.data);
                if (data && data.pipeline) {
                    handlePipelineStatusUpdate(data.pipeline);
                    if (!data.pipeline.running && pipelineEventSource) {
                        pipelineEventSource.close();
                        pipelineEventSource = null;
                    }
                }
            } catch (e) {
                console.warn("Error parsing SSE data:", e);
            }
        };
        pipelineEventSource.onerror = function () {
            if (pipelineEventSource) {
                pipelineEventSource.close();
                pipelineEventSource = null;
            }
            startPipelinePolling();
        };
    } catch (e) {
        startPipelinePolling();
    }
}

function startPipelinePolling() {
    if (pipelinePollInterval) clearInterval(pipelinePollInterval);
    const qInd = document.getElementById("queue-processing-indicator");
    const qTxt = document.getElementById("queue-processing-text");

    pipelinePollInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/pipeline/status/");
            const st = await res.json();
            handlePipelineStatusUpdate(st);
            if (!st.running) {
                clearInterval(pipelinePollInterval);
                pipelinePollInterval = null;
            }
        } catch (err) {
            console.warn("Polling error:", err);
        }
    }, 1500);
}

function appendConsoleLog(message, type = "info") {
    const timeStr = new Date().toTimeString().split(" ")[0];
    const stream = document.getElementById("activity-stream");
    if (stream) {
        const div = document.createElement("div");
        div.className = "stream-line";
        div.innerHTML = `<span class="stream-time">[${timeStr}]</span> <span class="stream-${type}">${escapeHtml(message)}</span>`;
        stream.appendChild(div);
        stream.scrollTop = stream.scrollHeight;
    } else {
        console.log(`[${timeStr}] [${type.toUpperCase()}] ${message}`);
    }
}

function toggleActivityConsole() {
    const drawer = document.getElementById("activity-drawer");
    if (drawer) drawer.classList.toggle("collapsed");
}


// -----------------------------------------------------------------------------
// Universal CSRF & Application-Wide Shift Date Controller
// -----------------------------------------------------------------------------

if (typeof window.getCsrfToken !== "function") {
    window.getCsrfToken = function() {
        const input = document.querySelector('[name=csrfmiddlewaretoken]');
        if (input && input.value) return input.value;
        const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : "";
    };
}

window.activeShiftDateInfo = {
    suffix: "",
    formatted_date: "",
    mode: "today",
    is_today: true,
    display: ""
};

window.cachedAvailableShiftDates = [];

window.initGlobalShiftDate = async function() {
    try {
        const res = await fetch("/api/settings/shift-date/");
        const data = await res.json();
        if (data.success) {
            if (data.active_shift) {
                window.activeShiftDateInfo = data.active_shift;
            }
            if (data.available_dates) {
                window.cachedAvailableShiftDates = data.available_dates;
            }
            populateAllShiftDropdowns(window.cachedAvailableShiftDates, window.activeShiftDateInfo);
            syncAllDateSelectorsUI(window.activeShiftDateInfo);
        }
    } catch (err) {
        console.warn("initGlobalShiftDate error:", err);
    }
};

window.populateAllShiftDropdowns = function(dates, activeShift) {
    if (activeShift) {
        window.activeShiftDateInfo = activeShift;
    }
    const activeSfx = activeShift ? activeShift.suffix : "";

    // Top Centralized Header, Dashboard & Mobile Drawer Selectors
    const shiftSelects = document.querySelectorAll("select.header-shift-select, #global-shift-select, #dash-global-shift-select, #drawer-global-shift-select");
    if (!shiftSelects || shiftSelects.length === 0) return;

    shiftSelects.forEach(sel => {
        const currentSelected = sel.value || activeSfx;
        sel.innerHTML = "";

        // Standard All Dates (Aggregate) option
        const optAll = document.createElement("option");
        optAll.value = "ALL";
        optAll.textContent = "All Dates (Aggregate)";
        if (currentSelected === "ALL" || (!currentSelected && activeSfx === "ALL")) {
            optAll.selected = true;
        }
        sel.appendChild(optAll);

        if (Array.isArray(dates) && dates.length > 0) {
            dates.forEach(d => {
                const opt = document.createElement("option");
                opt.value = d.suffix;
                const star = d.is_today ? " (Live Shift)" : "";
                opt.textContent = `${d.formatted_date} [${d.suffix}]${star}`;
                if (d.suffix === currentSelected || (!currentSelected && d.suffix === activeSfx)) {
                    opt.selected = true;
                }
                sel.appendChild(opt);
            });
        } else if (activeShift && activeShift.suffix) {
            const opt = document.createElement("option");
            opt.value = activeShift.suffix;
            opt.textContent = `${activeShift.formatted_date || activeShift.suffix} [${activeShift.suffix}] (Live Shift)`;
            opt.selected = true;
            sel.appendChild(opt);
        }
    });
};

window.setGlobalShiftDate = async function(targetVal) {
    if (!targetVal) return;
    try {
        if (typeof showToast === "function") {
            showToast(`📅 Synchronizing application to shift ${targetVal}...`, "info");
        }
        const csrf = typeof getCsrfToken === "function" ? getCsrfToken() : "";
        const res = await fetch("/api/settings/shift-date/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrf,
            },
            body: JSON.stringify({ target_date: targetVal })
        });
        const data = await res.json();
        if (data.success && data.active_shift) {
            window.activeShiftDateInfo = data.active_shift;
            syncAllDateSelectorsUI(data.active_shift);
            if (typeof showToast === "function") {
                showToast(`✓ All features mounted to shift: ${data.active_shift.display}`, "success");
            }

            // Dispatch global event
            window.dispatchEvent(new CustomEvent("shiftDateChanged", { detail: data.active_shift }));

            // Update Reports Hub
            if (typeof reportCurrentDate !== "undefined") {
                reportCurrentDate = data.active_shift.suffix;
            }
            if (typeof fetchReportTotals === "function") {
                void fetchReportTotals();
            }

            // Update Stock Hub
            if (typeof currentStockDateSuffix !== "undefined") {
                currentStockDateSuffix = data.active_shift.suffix;
            }
            if (typeof refreshStockLedger === "function") {
                void refreshStockLedger();
            }

            // Update ITMS Orders Explorer
            if (typeof loadItmsOrders === "function") {
                void loadItmsOrders();
            }
        } else {
            if (typeof showToast === "function") {
                showToast(`Shift date update failed: ${data.error || "Unknown error"}`, "error");
            }
        }
    } catch (err) {
        console.error("Error setting global shift date:", err);
    }
};

window.syncAllDateSelectorsUI = function(shiftInfo) {
    if (!shiftInfo) return;
    window.activeShiftDateInfo = shiftInfo;
    const sfx = shiftInfo.suffix;
    const shiftLabel = shiftInfo.formatted_date ? `${shiftInfo.formatted_date} (${sfx})` : (shiftInfo.display || sfx || "Live Shift");

    // Synchronize all shift selectors across desktop and mobile header/drawer
    const shiftSelects = document.querySelectorAll("select.header-shift-select, #global-shift-select, #dash-global-shift-select, #drawer-global-shift-select");
    shiftSelects.forEach(sel => {
        if (sfx) {
            sel.value = sfx;
        }
    });

    // Reports Hub - Read-only active shift badge & focus indicator
    const repBadge = document.getElementById("report-active-shift-badge");
    if (repBadge) {
        repBadge.textContent = shiftLabel;
    }
    const repFocusBadge = document.getElementById("report-selected-date-badge");
    if (repFocusBadge) {
        repFocusBadge.textContent = `Focus: ${shiftLabel}`;
    }

    // Stock Management - Read-only active shift badge
    const stockBadge = document.getElementById("stock-active-shift-badge");
    if (stockBadge) {
        stockBadge.textContent = shiftLabel;
    }
    const stockDisplay = document.getElementById("stock-date-display");
    if (stockDisplay) {
        stockDisplay.innerText = `Shift: ${sfx}`;
    }

    // Settings Tab
    const setMode = document.getElementById("settings-shift-mode-select");
    if (setMode) {
        setMode.value = shiftInfo.mode || "today";
    }
    const setManual = document.getElementById("settings-manual-date-input");
    if (setManual && shiftInfo.manual_date) {
        setManual.value = shiftInfo.manual_date;
    }
    const setBadge = document.getElementById("settings-active-shift-badge");
    if (setBadge) {
        setBadge.textContent = `Active: ${shiftInfo.display}`;
        setBadge.className = shiftInfo.is_today ? "badge badge-green" : "badge badge-yellow";
    }
};

document.addEventListener("DOMContentLoaded", () => {
    void initGlobalShiftDate();
});
