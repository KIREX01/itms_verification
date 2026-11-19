/**
 * Governance UI layer: shows each person only what their role allows.
 *
 *  - Loads /api/me/capabilities/ (roles per bond + capabilities at the active bond).
 *  - Hides nav tabs and any element with data-cap="cap.one cap.two" (shown if ANY matches).
 *  - Quality flags: QI raises (with optional corrected plate), TL applies / rejects / resolves.
 *
 * The server enforces every rule; this file only keeps the screen honest and uncluttered.
 */
"use strict";

window.CLOSE_GOV = {
    loaded: false,
    enforced: true,
    capabilities: new Set(),
    bonds: {},
    activeBond: "",
    isAdmin: false,
};

const NAV_TAB_CAPS = {
    1: ["dashboard.view"],
    2: ["orders.view"],
    3: ["queue.view", "capture.upload"],
    4: ["queue.view"],
    5: ["stock.view", "stock.scan"],
    6: ["reports.view"],
    7: ["settings.operator"],
    8: ["history.view"],
};

const ROLE_LABELS = {
    HELPER: "Helper",
    QUALITY_INSPECTOR: "Quality Inspector",
    TEAM_LEADER: "Team Leader",
    SUPERVISOR: "Supervisor",
};

function hasCap(...caps) {
    const gov = window.CLOSE_GOV;
    if (!gov.loaded || !gov.enforced || gov.isAdmin) return true;
    return caps.some(c => gov.capabilities.has(c));
}

function navTabAllowed(n) {
    const caps = NAV_TAB_CAPS[n];
    return !caps || hasCap(...caps);
}

function applyCapabilityVisibility(root = document) {
    root.querySelectorAll("[data-cap]").forEach(el => {
        const caps = (el.getAttribute("data-cap") || "").split(/\s+/).filter(Boolean);
        el.classList.toggle("gov-hidden", caps.length > 0 && !hasCap(...caps));
    });
    for (const n of Object.keys(NAV_TAB_CAPS)) {
        const btn = document.getElementById(`nav-tab-${n}`);
        if (btn) btn.classList.toggle("gov-hidden", !navTabAllowed(Number(n)));
    }
}

function renderRoleBadge() {
    const gov = window.CLOSE_GOV;
    const host = document.getElementById("gov-role-badge");
    if (!host) return;
    const roles = (gov.bonds[gov.activeBond] || []).map(r => ROLE_LABELS[r] || r);
    if (gov.isAdmin) roles.unshift("Administrator");
    host.textContent = roles.length ? `${roles.join(" · ")}${gov.activeBond ? " @ " + gov.activeBond : ""}` : "";
    host.classList.toggle("gov-hidden", roles.length === 0);
}

async function loadCapabilities() {
    try {
        const res = await fetch("/api/me/capabilities/", { headers: { Accept: "application/json" } });
        if (!res.ok) return;
        const data = await res.json();
        const gov = window.CLOSE_GOV;
        gov.loaded = true;
        gov.enforced = !!data.enforced;
        gov.capabilities = new Set(data.capabilities || []);
        gov.bonds = data.bonds || {};
        gov.activeBond = data.active_bond || "";
        gov.isAdmin = !!data.is_platform_admin;
        applyCapabilityVisibility();
        renderRoleBadge();
        // If the current tab is not allowed, move to the first allowed tab.
        const active = document.querySelector(".nav-tab-item.active");
        const activeNum = active ? Number((active.id || "").replace("nav-tab-", "")) : 1;
        if (!navTabAllowed(activeNum) && typeof switchNavTab === "function") {
            const first = Object.keys(NAV_TAB_CAPS).map(Number).find(navTabAllowed);
            if (first) switchNavTab(first);
        }
    } catch (err) {
        console.warn("Capability load failed", err);
    }
}

// Guard tab switching (keyboard shortcuts included).
(function wrapTabSwitching() {
    if (typeof window.switchNavTab !== "function") return;
    const original = window.switchNavTab;
    window.switchNavTab = function (target, ...rest) {
        const n = Number(target);
        if (!Number.isNaN(n) && !navTabAllowed(n)) {
            showToast("Your role doesn't include this section.", "warning");
            return;
        }
        return original.call(this, target, ...rest);
    };
})();

/* ------------------------------------------------------------------ */
/* Quality flags                                                        */
/* ------------------------------------------------------------------ */

let flagReasons = [];
let currentFlagTarget = null;   // { type, id, label }

async function fetchFlags(params = {}) {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`/api/flags/?${qs}`, { headers: { Accept: "application/json" } });
    const data = await res.json();
    if (data.reasons) flagReasons = data.reasons;
    return data;
}

function flagRowHtml(f, canResolve) {
    const suggestion = f.suggested_plate
        ? `<div class="gov-flag-suggest">Suggested plate: <strong>${escapeHtml(f.suggested_plate)}</strong></div>` : "";
    const note = f.note ? `<div class="gov-flag-note">${escapeHtml(f.note)}</div>` : "";
    const target = f.target_type === "PAIR" ? "Pair" : f.target_type === "ORDER"
        ? `Order ${escapeHtml(f.order_number || "")}` : `Kit ${escapeHtml(f.kit_code || "")}`;
    let actions = "";
    if (f.status === "OPEN" && canResolve) {
        actions = `<div class="gov-flag-actions">
            ${f.suggested_plate && f.target_type === "PAIR"
                ? `<button class="btn btn-primary btn-sm" onclick="resolveFlag(${f.id}, 'apply')">Apply ${escapeHtml(f.suggested_plate)}</button>` : ""}
            <button class="btn btn-secondary btn-sm" onclick="resolveFlag(${f.id}, 'resolve')">Mark resolved</button>
            <button class="btn btn-secondary btn-sm" onclick="resolveFlag(${f.id}, 'reject')">Reject</button>
        </div>`;
    } else if (f.status !== "OPEN") {
        actions = `<div class="gov-flag-closed">${escapeHtml(f.status.toLowerCase())} by ${escapeHtml(f.resolved_by || "-")}</div>`;
    }
    return `<div class="gov-flag ${f.status === "OPEN" ? "is-open" : ""}">
        <div class="gov-flag-head"><span>🚩 #${f.id} · ${escapeHtml(f.reason)}</span><span>${target}</span></div>
        ${suggestion}${note}
        <div class="gov-flag-meta">Raised by ${escapeHtml(f.raised_by || "-")}</div>
        ${actions}
    </div>`;
}

async function loadPairFlags(pairId) {
    const panel = document.getElementById("queue-flags-panel");
    if (!panel) return;
    if (!pairId || !hasCap("queue.view", "flag.raise")) { panel.innerHTML = ""; return; }
    try {
        const params = { pair_id: pairId, status: "ALL" };
        const detail = (typeof selectedPairDetail !== "undefined") ? selectedPairDetail : null;
        const pairFlags = await fetchFlags(params);
        let flags = pairFlags.flags || [];
        if (detail && detail.order && detail.order.id) {
            const orderFlags = await fetchFlags({ order_id: detail.order.id, status: "ALL" });
            flags = flags.concat(orderFlags.flags || []);
        }
        const open = flags.filter(f => f.status === "OPEN");
        panel.innerHTML = flags.length
            ? `<div class="gov-flags-title">${open.length ? `⛔ ${open.length} open flag(s): submission blocked` : "Flag history"}</div>`
              + flags.map(f => flagRowHtml(f, pairFlags.can_resolve)).join("")
            : "";
    } catch (err) {
        panel.innerHTML = "";
    }
}

function openFlagModal(targetType, targetId, label) {
    if (!targetId) { showToast("Select an item to flag first.", "warning"); return; }
    currentFlagTarget = { type: targetType, id: targetId, label: label || "" };
    const sel = document.getElementById("flag-reason");
    const ensureReasons = flagReasons.length ? Promise.resolve() : fetchFlags({ status: "OPEN" });
    ensureReasons.then(() => {
        sel.innerHTML = flagReasons.map(r => `<option value="${r.code}">${escapeHtml(r.label)}</option>`).join("");
        document.getElementById("flag-target-label").textContent =
            `${targetType === "PAIR" ? "Vehicle pair" : targetType === "ORDER" ? "Order" : "Kit"} ${label || "#" + targetId}`;
        document.getElementById("flag-note").value = "";
        document.getElementById("flag-suggested-plate").value = "";
        document.getElementById("flag-plate-row").style.display = targetType === "PAIR" ? "" : "none";
        openModal("modal-raise-flag");
    });
}

function openFlagForSelectedPair() {
    const detail = (typeof selectedPairDetail !== "undefined") ? selectedPairDetail : null;
    openFlagModal("PAIR", (typeof selectedPairId !== "undefined") ? selectedPairId : null,
        detail ? (detail.registration_number_detected || "") : "");
}

async function submitFlag() {
    if (!currentFlagTarget) return;
    const payload = {
        target_type: currentFlagTarget.type,
        target_id: currentFlagTarget.id,
        reason_code: document.getElementById("flag-reason").value,
        note: document.getElementById("flag-note").value.trim(),
        suggested_plate: document.getElementById("flag-suggested-plate").value.trim(),
    };
    const res = await fetch("/api/flags/raise/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) { showToast(data.error || "Could not raise flag.", "error"); return; }
    closeModal("modal-raise-flag");
    showToast(data.message || "Flag raised.", "success");
    if (currentFlagTarget.type === "PAIR") loadPairFlags(currentFlagTarget.id);
    if (typeof fetchPairs === "function") fetchPairs();
}

async function resolveFlag(flagId, outcome) {
    let note = "";
    if (outcome === "reject") {
        note = prompt("Why is this flag rejected?") || "";
        if (!note.trim()) return;
    }
    const res = await fetch(`/api/flags/${flagId}/resolve/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outcome, note }),
    });
    const data = await res.json();
    if (!data.success) { showToast(data.error || "Could not update flag.", "error"); return; }
    showToast(`Flag #${flagId} ${data.flag.status.toLowerCase()}.`, "success");
    const pairId = (typeof selectedPairId !== "undefined") ? selectedPairId : null;
    if (pairId) {
        if (typeof selectPair === "function" && outcome === "apply") selectPair(pairId);
        else loadPairFlags(pairId);
    }
    if (typeof fetchPairs === "function") fetchPairs();
}

// Batch / single submit shortcuts (keyboard "B", dashboard cards) respect the role too.
(function guardSubmitTriggers() {
    if (typeof window.triggerBatchSubmit !== "function") return;
    const original = window.triggerBatchSubmit;
    window.triggerBatchSubmit = function (...args) {
        if (!hasCap("itms.submit")) {
            showToast("Only the Team Leader can submit to ITMS.", "warning");
            return;
        }
        return original.apply(this, args);
    };
})();

// Refresh flags whenever a pair is selected in the queue.
(function hookPairSelection() {
    if (typeof window.selectPair !== "function") return;
    const original = window.selectPair;
    window.selectPair = async function (pairId, ...rest) {
        const result = await original.call(this, pairId, ...rest);
        loadPairFlags(pairId);
        return result;
    };
})();

/* ------------------------------------------------------------------ */
/* My account & team (Settings tab)                                      */
/* ------------------------------------------------------------------ */
async function loadAccountCard() {
    const body = document.getElementById("gov-account-body");
    if (!body) return;
    try {
        const res = await fetch("/api/me/profile/", { headers: { Accept: "application/json" } });
        const p = await res.json();
        if (!p.success) { body.textContent = ""; return; }
        const bonds = p.summary.bonds || {};
        const roleRows = Object.keys(bonds).length
            ? Object.entries(bonds).map(([b, roles]) =>
                `<span class="badge badge-muted" style="margin:2px;">${escapeHtml(roles.map(r => ROLE_LABELS[r] || r).join(", "))} @ ${escapeHtml(b)}</span>`).join(" ")
            : `<span style="color:var(--ug-text-dim);">No active role yet.</span>`;
        let company;
        if (p.company_email && p.company_email_status === "VERIFIED") {
            company = `<span style="color:#86efac;">✓ ${escapeHtml(p.company_email)}</span>`;
        } else if (p.company_email) {
            company = `${escapeHtml(p.company_email)} <span style="color:#fcd34d;">(${escapeHtml(p.company_email_status.toLowerCase())})</span>`;
        } else {
            const domains = (p.company_domains || []).map(d => "@" + d).join(", ");
            company = `<span style="color:#fcd34d;">Not attached.</span> Team Leaders: connect ITMS with your ${escapeHtml(domains)} email to attach it. Others: ask the administrator.`;
            if (p.grace_until) company += ` <strong>Deadline: ${escapeHtml(p.grace_until.slice(0, 10))}</strong>`;
        }
        const warnings = (p.warnings || []).map(w => `<div style="color:#fcd34d; margin-top:4px;">⚠️ ${escapeHtml(w)}</div>`).join("");
        body.innerHTML = `
            <div style="display:grid; grid-template-columns:max-content 1fr; gap:6px 14px;">
                <span style="color:var(--ug-text-dim);">Signed in as</span><span>${escapeHtml(p.username)} · ${escapeHtml(p.email || "")}</span>
                <span style="color:var(--ug-text-dim);">Roles</span><span>${roleRows}</span>
                <span style="color:var(--ug-text-dim);">Company email</span><span>${company}</span>
            </div>${warnings}`;
        if (hasCap("team.invite")) loadTeam();
    } catch (err) {
        body.textContent = "";
    }
}

async function loadTeam() {
    const res = await fetch("/api/team/", { headers: { Accept: "application/json" } });
    const t = await res.json();
    if (!t.success) return;
    document.getElementById("gov-team-bond").textContent = t.bond || "—";
    document.getElementById("gov-team-members").innerHTML = (t.members || []).map(m =>
        `<span class="badge badge-muted" style="margin:2px;">${escapeHtml(m.name)} · ${escapeHtml(m.role)}</span>`).join(" ") || "Nobody yet.";
    document.getElementById("gov-team-invites").innerHTML = (t.invites || []).length
        ? "<div style='font-weight:700;'>Your invites</div>" + t.invites.map(i =>
            `<div>${escapeHtml(i.name)} (${escapeHtml(i.email)}) · ${escapeHtml(i.role)} · <em>${escapeHtml(i.status)}</em></div>`).join("")
        : "";
}

function toggleInviteCompanyEmail() {
    const role = document.getElementById("gov-invite-role").value;
    const input = document.getElementById("gov-invite-company");
    input.style.display = role === "QUALITY_INSPECTOR" ? "" : "none";
    input.required = role === "QUALITY_INSPECTOR";
}

async function submitTeamInvite(evt) {
    evt.preventDefault();
    const form = evt.target;
    const payload = Object.fromEntries(new FormData(form).entries());
    const res = await fetch("/api/team/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    showToast(data.success ? data.message : (data.error || "Invite failed."), data.success ? "success" : "error");
    if (data.success) { form.reset(); toggleInviteCompanyEmail(); loadTeam(); }
}

document.addEventListener("DOMContentLoaded", () => {
    loadCapabilities().then(() => {
        loadAccountCard();
        const inv = document.getElementById("gov-invite-role");
        if (inv) toggleInviteCompanyEmail();
    });
});
