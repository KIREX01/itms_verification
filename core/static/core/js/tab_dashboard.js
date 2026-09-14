/**
 * ITMS CLOSING SYSTEM - TAB 1: DASHBOARD & METRICS TELEMETRY
 * Fetches application telemetry, updates KPI counters, and synchronizes pipeline stage stats.
 */

async function fetchStats() {
    try {
        const res = await fetch("/api/stats/");
        const data = await res.json();
        if (data.success) {
            const s = data.stats || data;
            const appCount = s.approved !== undefined ? s.approved : 0;
            // Dashboard KPI Metric Cards
            setText("kpi-review", s.pending_review !== undefined ? s.pending_review : 0);
            setText("kpi-approved", appCount);
            setText("kpi-submitted", s.submitted !== undefined ? s.submitted : 0);
            setText("kpi-issues", s.issues !== undefined ? s.issues : 0);
            setText("kpi-orders", s.total_orders !== undefined ? s.total_orders : 0);

            // Tab badge in header
            setText("badge-nav-queue", s.pending_review !== undefined ? s.pending_review : 0);

            // Queue tab approved counter and submit button
            setText("queue-approved-count-text", `${appCount} approved pair${appCount === 1 ? '' : 's'} ready`);
            const qAppBtn = document.getElementById("btn-queue-batch-submit");
            if (qAppBtn) {
                qAppBtn.innerHTML = `🚀 Submit Approved (${appCount}) <span class="hotkey">B</span>`;
                qAppBtn.disabled = (appCount === 0);
                qAppBtn.style.opacity = appCount === 0 ? "0.6" : "1";
                qAppBtn.style.cursor = appCount === 0 ? "not-allowed" : "pointer";
            }

            // Dashboard 5-Stage Pipeline Cards
            setText("flow-count-ingest", s.total_photos || (s.total_pairs ? s.total_pairs * 2 : 0));
            setText("flow-count-pairs", s.total_pairs !== undefined ? s.total_pairs : 0);
            setText("flow-count-vision", s.total_pairs !== undefined ? s.total_pairs : 0);
            setText("flow-count-review", s.pending_review !== undefined ? s.pending_review : 0);
            setText("flow-count-submitted", s.submitted !== undefined ? s.submitted : 0);
            setText("dash-total-orders", s.total_orders !== undefined ? s.total_orders : 0);

            // Real-time synchronization of dry-run mode from config.json
            if (data.dry_run !== undefined && typeof updateDryRunBadges === "function") {
                updateDryRunBadges(Boolean(data.dry_run));
            }
        }
    } catch (err) {
        console.warn("Telemetry fetch error:", err);
    }
}
