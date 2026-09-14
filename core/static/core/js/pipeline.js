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

async function triggerBatchSubmit() {
    if (isBatchSubmitting) {
        reopenSubmissionModal();
        return;
    }

    try {
        const res = await fetch("/api/pairs/?status=APPROVED&limit=500");
        const data = await res.json();
        const approvedPairs = (data.pairs || []).filter(p => p.verification_status === "APPROVED" && p.order);

        if (approvedPairs.length === 0) {
            showToast("No approved pairs with matched orders are ready for submission.", "info");
            return;
        }

        const confirmMsg = `Found ${approvedPairs.length} approved vehicle pair(s) ready for submission.\nProceed with batch submission to ITMS?`;
        if (!confirm(confirmMsg)) return;

        // Initialize submission state
        batchSubmissionAborted = false;
        isBatchSubmitting = true;

        openModal("modal-submission-progress");

        // Set mode badge based on simulation mode
        const dryRunBadge = document.getElementById("header-dryrun-badge");
        const isDryRun = dryRunBadge && dryRunBadge.innerText.includes("Dry Run");
        const modeBadge = document.getElementById("sub-modal-mode-badge");
        if (modeBadge) {
            modeBadge.innerText = isDryRun ? "SIMULATED (DRY RUN)" : "LIVE ITMS TRANSMISSION";
            modeBadge.className = isDryRun ? "badge badge-yellow" : "badge badge-red";
        }

        const total = approvedPairs.length;
        setText("sub-kpi-total", total);
        setText("sub-kpi-success", 0);
        setText("sub-kpi-failed", 0);
        setText("sub-kpi-remaining", total);
        setText("sub-progress-percent", "0%");
        setText("sub-item-index-badge", `1 / ${total}`);

        const progressBar = document.getElementById("sub-progress-bar-fill");
        if (progressBar) progressBar.style.width = "0%";

        const btnAbort = document.getElementById("btn-sub-abort");
        if (btnAbort) {
            btnAbort.style.display = "inline-flex";
            btnAbort.disabled = false;
            btnAbort.innerText = "⏹ Stop / Abort";
        }
        const btnDone = document.getElementById("btn-sub-done");
        if (btnDone) btnDone.style.display = "none";
        const btnClose = document.getElementById("btn-close-sub-modal");
        if (btnClose) btnClose.style.display = "none";

        const logStream = document.getElementById("sub-live-log-stream");
        if (logStream) logStream.innerHTML = "";
        appendSubmissionLog(`Initiated batch submission for ${total} verified pairs...`, "info");
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
            const orderNo = p.order ? p.order.order_number : "—";
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
                    body: "action=submit",
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
        if (btnClose) btnClose.style.display = "inline-flex";

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
    } catch (err) {
        isBatchSubmitting = false;
        showToast("Batch submission error: " + err, "error");
        hideTopSubmissionIndicator();
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
