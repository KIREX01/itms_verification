/**
 * ITMS CLOSING SYSTEM - TAB 1: DASHBOARD & METRICS TELEMETRY
 * Fetches application telemetry, updates KPI counters with dual shift and all-time views,
 * and synchronizes pipeline stage stats.
 */

function setDashboardViewMode(mode) {
    dashboardViewMode = mode;
    const btnShift = document.getElementById("btn-dash-mode-shift");
    const btnAll = document.getElementById("btn-dash-mode-all");
    if (btnShift) btnShift.classList.toggle("active", mode === "SHIFT");
    if (btnAll) btnAll.classList.toggle("active", mode === "ALL_TIME");
    updateDashboardDisplay();
}

function updateDashboardDisplay() {
    if (!telemetryTotalStats) return;
    const total = telemetryTotalStats;
    const shift = telemetryShiftStats || {
        date: new Date().toISOString().slice(0, 10),
        total_pairs: 0,
        pending_review: 0,
        approved: 0,
        submitted: 0,
        issues: 0,
        batches_count: 0,
        total_photos: 0,
    };

    const isShift = (dashboardViewMode === "SHIFT");

    // Header Shift Banner
    const shiftBadge = document.getElementById("dash-shift-badge");
    const shiftDateText = document.getElementById("dash-shift-date-text");
    const shiftSummary = document.getElementById("dash-shift-summary-text");
    if (shiftBadge) {
        shiftBadge.className = isShift ? "badge badge-green" : "badge badge-cyan";
        shiftBadge.innerText = isShift ? "● TODAY'S SHIFT" : "🌐 ALL-TIME TOTALS";
    }
    if (shiftDateText) {
        shiftDateText.innerText = isShift ? `Shift: ${shift.date}` : "Cumulative Total";
    }
    if (shiftSummary) {
        shiftSummary.innerText = isShift
            ? `${shift.total_photos} photos • ${shift.total_pairs} pairs (${shift.approved} approved, ${shift.submitted} submitted)`
            : `${total.total_photos} photos • ${total.total_pairs} pairs (${total.approved} approved, ${total.submitted} submitted)`;
    }

    // KPI Cards Numbers
    const revCount = isShift ? shift.pending_review : total.pending_review;
    const appCount = isShift ? shift.approved : total.approved;
    const subCount = isShift ? shift.submitted : total.submitted;
    const issCount = isShift ? shift.issues : total.issues;

    setText("kpi-review", revCount !== undefined ? revCount : 0);
    setText("kpi-approved", appCount !== undefined ? appCount : 0);
    setText("kpi-submitted", subCount !== undefined ? subCount : 0);
    setText("kpi-issues", issCount !== undefined ? issCount : 0);
    setText("kpi-orders", total.total_orders !== undefined ? total.total_orders : 0);

    // KPI Context Footnotes (Dual Shift vs All-Time Clarity)
    if (isShift) {
        const priorReview = Math.max(0, (total.pending_review || 0) - (shift.pending_review || 0));
        const priorApprv = Math.max(0, (total.approved || 0) - (shift.approved || 0));
        setText("kpi-context-review", `All-time: ${total.pending_review || 0} (${priorReview} prior carryover)`);
        setText("kpi-context-approved", `All-time: ${total.approved || 0} (${priorApprv} prior carryover)`);
        setText("kpi-context-submitted", `All-time: ${total.submitted || 0}`);
        setText("kpi-context-issues", `All-time: ${total.issues || 0}`);
        setText("kpi-context-orders", `Pending sync: ${total.pending_orders || 0}`);
    } else {
        setText("kpi-context-review", `Today's shift: ${shift.pending_review || 0}`);
        setText("kpi-context-approved", `Today's shift: ${shift.approved || 0}`);
        setText("kpi-context-submitted", `Today's shift: ${shift.submitted || 0}`);
        setText("kpi-context-issues", `Today's shift: ${shift.issues || 0}`);
        setText("kpi-context-orders", `Total registry`);
    }

    // 5-Stage Pipeline Cards
    if (isShift) {
        setText("flow-count-ingest", shift.total_photos || 0);
        setText("flow-count-pairs", shift.total_pairs || 0);
        setText("flow-count-vision", shift.total_pairs || 0);
        setText("flow-count-review", shift.pending_review || 0);
        setText("flow-count-submitted", shift.submitted || 0);
    } else {
        setText("flow-count-ingest", total.total_photos || (total.total_pairs ? total.total_pairs * 2 : 0));
        setText("flow-count-pairs", total.total_pairs || 0);
        setText("flow-count-vision", total.total_pairs || 0);
        setText("flow-count-review", total.pending_review || 0);
        setText("flow-count-submitted", total.submitted || 0);
    }

    // Telemetry Panel
    setText("dash-total-orders", total.total_orders || 0);
    setText("dash-shift-date-val", shift.date || "Today");
    setText("dash-shift-batches-val", `${shift.batches_count || 0} today (${total.batches_count || 0} total)`);
    setText("dash-shift-photos-val", `${shift.total_photos || 0} today (${total.total_photos || 0} total)`);
}

async function fetchStats() {
    try {
        const res = await fetch("/api/stats/");
        const data = await res.json();
        if (data.success) {
            const s = data.stats || data;
            telemetryTotalStats = s;
            telemetryShiftStats = s.shift || null;

            // Update Header Nav Queue Badge to show Today's Queue (or total pending review)
            const shiftPending = s.shift ? s.shift.pending_review : s.pending_review;
            setText("badge-nav-queue", shiftPending !== undefined ? shiftPending : (s.pending_review || 0));

            // Queue Tab Approved Counter and Submit Button
            const shiftAppCount = s.shift && s.shift.approved !== undefined ? s.shift.approved : (s.approved || 0);
            const totalAppCount = s.approved !== undefined ? s.approved : 0;
            const activeScope = typeof currentQueueDateScope !== "undefined" ? currentQueueDateScope : "TODAY";
            const effectiveAppCount = (activeScope === "TODAY") ? shiftAppCount : totalAppCount;

            setText("queue-approved-count-text", `${effectiveAppCount} approved pair${effectiveAppCount === 1 ? '' : 's'} ready (${activeScope})`);
            const qAppBtn = document.getElementById("btn-queue-batch-submit");
            if (qAppBtn) {
                qAppBtn.innerHTML = `🚀 Submit Approved (${effectiveAppCount}) <span class="hotkey">B</span>`;
                qAppBtn.disabled = (effectiveAppCount === 0);
                qAppBtn.style.opacity = effectiveAppCount === 0 ? "0.6" : "1";
                qAppBtn.style.cursor = effectiveAppCount === 0 ? "not-allowed" : "pointer";
            }

            updateDashboardDisplay();

            // Real-time synchronization of dry-run mode from config.json
            if (data.dry_run !== undefined && typeof updateDryRunBadges === "function") {
                updateDryRunBadges(Boolean(data.dry_run));
            }
        }
    } catch (err) {
        console.warn("Telemetry fetch error:", err);
    }
}
