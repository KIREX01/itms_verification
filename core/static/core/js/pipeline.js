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
            startPipelinePolling();
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
            startPipelinePolling();
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
async function triggerBatchSubmit() {
    if (isBatchSubmitting) {
        reopenSubmissionModal();
        return;
    }

    try {
        const res = await fetch("/api/pairs/?status=APPROVED&limit=500");
        const data = await res.json();
        const approvedPairs = (data.pairs || []).filter(p => p.verification_status === "APPROVED");

        if (approvedPairs.length === 0) {
            showToast("No approved pairs ready for submission. Please approve pairs in Review Queue [A].", "info");
            return;
        }

        // Refresh live config from config.json
        const isDry = await fetchLatestSubmissionSettings();
        currentSubmissionTask = { type: "batch", pairs: approvedPairs };

        setText("sub-modal-title", "🚀 Batch ITMS Submission Confirmation");
        setText("sub-confirm-scope-title", "BATCH TRANSMISSION SCOPE");
        setText("sub-confirm-count-badge", `${approvedPairs.length} Orders Ready`);
        setText("sub-confirm-target-main", `${approvedPairs.length} Approved Vehicle Orders`);
        setText("sub-confirm-target-sub", "All verified motorcycle pairs will be sequentially submitted through the automated ITMS installation wizard.");

        const batchListBox = document.getElementById("sub-confirm-batch-list-box");
        if (batchListBox) {
            batchListBox.style.display = "flex";
            batchListBox.innerHTML = approvedPairs.slice(0, 50).map((p, idx) => {
                const pPlate = p.registration_number_detected || "Pair #" + p.id;
                const pOrder = p.order ? p.order.order_number : (p.order_number || "—");
                return `<div style="display:flex; justify-content:space-between; align-items:center; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                    <span style="color:var(--ug-yellow); font-weight:700;">#${idx+1} ${escapeHtml(pPlate)}</span>
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
            const subData = await subRes.json();
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
                const subData = await subRes.json();
                if (subData.success) {
                    succeeded++;
                    setText("sub-kpi-success", succeeded);
                    appendSubmissionLog(`✓ [${currentIdx}/${total}] Success: ${plate} submitted! Token: ${subData.token || 'OK'}`, "success");
                } else {
                    failed++;
                    setText("sub-kpi-failed", failed);
                    appendSubmissionLog(`✕ [${currentIdx}/${total}] Failed: ${plate} - ${subData.error || subData.message || 'Error'}`, "error");
                }
            } catch (netErr) {
                failed++;
                setText("sub-kpi-failed", failed);
                appendSubmissionLog(`✕ [${currentIdx}/${total}] Request error on ${plate}: ${netErr}`, "error");
            }

            const remaining = total - (i + 1);
            setText("sub-kpi-remaining", remaining);
            const stepPct = Math.round(((i + 1) / total) * 100);
            setText("sub-progress-percent", `${stepPct}%`);
            if (progressBar) progressBar.style.width = `${stepPct}%`;
        }

        isBatchSubmitting = false;

        if (batchSubmissionAborted) {
            setText("sub-status-desc", `Batch aborted by operator. ${succeeded} succeeded, ${failed} failed.`);
            setText("sub-active-plate", "Submission Aborted");
        } else {
            setText("sub-status-desc", `All pairs processed! ${succeeded} succeeded, ${failed} failed.`);
            setText("sub-active-plate", "Batch Complete");
            setText("sub-item-index-badge", `${total} / ${total}`);
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

function startPipelinePolling() {
    if (pipelinePollInterval) clearInterval(pipelinePollInterval);
    pipelinePollInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/pipeline/status/");
            const st = await res.json();
            if (st.running) {
                appendConsoleLog(`Stage: ${st.stage || 'Processing'} (${st.progress || 0}%) - ${st.message || ''}`, "info");
            } else {
                clearInterval(pipelinePollInterval);
                pipelinePollInterval = null;
                appendConsoleLog(`Pipeline task finished: ${st.message || 'Complete'}`, st.success ? "success" : "error");
                fetchStats();
                fetchPairs();
            }
        } catch (err) {
            console.warn("Polling error:", err);
        }
    }, 1500);
}

function appendConsoleLog(message, type = "info") {
    const stream = document.getElementById("activity-stream");
    if (!stream) return;
    const timeStr = new Date().toTimeString().split(" ")[0];
    const div = document.createElement("div");
    div.className = "stream-line";
    div.innerHTML = `<span class="stream-time">[${timeStr}]</span> <span class="stream-${type}">${escapeHtml(message)}</span>`;
    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
}

function toggleActivityConsole() {
    const drawer = document.getElementById("activity-drawer");
    if (drawer) drawer.classList.toggle("collapsed");
}
