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
            // Dashboard KPI Metric Cards
            setText("kpi-review", s.pending_review !== undefined ? s.pending_review : 0);
            setText("kpi-approved", s.approved !== undefined ? s.approved : 0);
            setText("kpi-submitted", s.submitted !== undefined ? s.submitted : 0);
            setText("kpi-issues", s.issues !== undefined ? s.issues : 0);
            setText("kpi-orders", s.total_orders !== undefined ? s.total_orders : 0);

            // Tab badge in header
            setText("badge-nav-queue", s.pending_review !== undefined ? s.pending_review : 0);

            // Dashboard 5-Stage Pipeline Cards
            setText("flow-count-ingest", s.total_photos || (s.total_pairs ? s.total_pairs * 2 : 0));
            setText("flow-count-pairs", s.total_pairs !== undefined ? s.total_pairs : 0);
            setText("flow-count-vision", s.total_pairs !== undefined ? s.total_pairs : 0);
            setText("flow-count-review", s.pending_review !== undefined ? s.pending_review : 0);
            setText("flow-count-submitted", s.submitted !== undefined ? s.submitted : 0);
            setText("dash-total-orders", s.total_orders !== undefined ? s.total_orders : 0);
        }
    } catch (err) {
        console.warn("Telemetry fetch error:", err);
    }
}
