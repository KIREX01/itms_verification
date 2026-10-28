/**
 * ITMS CLOSING SYSTEM - TAB 7: ESSENTIAL OPERATOR SETTINGS
 * Keeps only vital operator controls in the Web UI (Active Bond, Shift Date,
 * Simulation Mode, Time-Bound Email Invitation, and Version Updates).
 * Full Device/MAC/IP Firewall & Time-Bound Access Management lives in the TUI.
 */

async function toggleDryRun() {
    try {
        const res = await fetch("/api/settings/toggle-dry-run/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            const isDry = data.dry_run ?? data.dry_run_mode;
            showToast(data.message, "info");
            updateDryRunBadges(isDry);
            fetchStats();
        }
    } catch (err) {
        showToast("Failed to toggle mode: " + err, "error");
    }
}

function updateDryRunBadges(isDry) {
    const chk = document.getElementById("settings-dryrun-toggle");
    if (chk) chk.checked = Boolean(isDry);

    const badge = document.getElementById("settings-dryrun-badge");
    if (badge) {
        badge.className = isDry ? "badge badge-yellow" : "badge badge-red";
        badge.innerText = isDry ? "● Simulation Mode (Safe)" : "● LIVE ITMS SERVER";
    }

    const headerBadge = document.getElementById("header-dryrun-badge");
    if (headerBadge) {
        headerBadge.className = isDry ? "badge badge-yellow" : "badge badge-red";
        headerBadge.innerText = isDry ? "● Dry Run" : "● Live";
    }
}

async function checkForUpdates(force = false) {
    const badge = document.getElementById("settings-update-badge");
    const desc = document.getElementById("settings-update-desc");
    const applyBtn = document.getElementById("btn-apply-update");
    const checkBtn = document.getElementById("btn-check-updates");
    const notesBox = document.getElementById("settings-release-notes-box");
    const notesContent = document.getElementById("release-notes-content");
    const notesTitle = document.getElementById("release-notes-title");
    const notesLink = document.getElementById("release-notes-link");
    const tagSpan = document.getElementById("btn-new-version-tag");

    if (badge) {
        badge.className = "badge badge-muted";
        badge.innerText = "Checking...";
    }
    if (checkBtn) checkBtn.disabled = true;

    try {
        const res = await fetch(`/api/updates/check/?force=${force ? "true" : "false"}`);
        const data = await res.json();

        if (data.success) {
            if (data.update_available) {
                if (badge) {
                    badge.className = "badge badge-yellow";
                    badge.innerText = `● Update v${data.latest_version} Available`;
                }
                if (desc) {
                    desc.innerHTML = `<strong style="color:var(--ug-yellow);">A new version (v${escapeHtml(data.latest_version)}) is available!</strong> Current: v${escapeHtml(data.current_version)}.`;
                }
                if (applyBtn) {
                    applyBtn.style.display = "inline-flex";
                    if (tagSpan) tagSpan.innerText = `v${escapeHtml(data.latest_version)}`;
                }
                if (notesBox) {
                    notesBox.style.display = "block";
                    if (notesTitle) notesTitle.innerText = `Release Notes: ${escapeHtml(data.release_name || "v" + data.latest_version)}`;
                    if (notesLink && data.html_url) notesLink.href = data.html_url;
                    if (notesContent) notesContent.innerText = data.release_notes || "No release notes provided.";
                }
                showToast(`New update v${data.latest_version} is available!`, "info");
            } else {
                if (badge) {
                    badge.className = "badge badge-green";
                    badge.innerText = "✓ Up to date";
                }
                if (desc) {
                    desc.innerText = `Running CLOSE Copilot v${escapeHtml(data.current_version)}. System is on the latest release.`;
                }
                if (applyBtn) applyBtn.style.display = "none";
                if (notesBox) notesBox.style.display = "none";
                if (force) showToast(`System is up to date (v${data.current_version}).`, "success");
            }
        } else {
            if (badge) {
                badge.className = "badge badge-muted";
                badge.innerText = "Offline / Current";
            }
            if (applyBtn) applyBtn.style.display = "none";
            if (notesBox) notesBox.style.display = "none";
            if (force) showToast(data.error || "Could not reach GitHub API", "warning");
        }
    } catch (err) {
        console.warn("Update check error:", err);
        if (badge) {
            badge.className = "badge badge-muted";
            badge.innerText = "Offline";
        }
    } finally {
        if (checkBtn) checkBtn.disabled = false;
    }
}

async function applySystemUpdate() {
    const applyBtn = document.getElementById("btn-apply-update");
    if (!confirm("Download and apply the update now?\n\nYour database and evidence photos are strictly protected.")) {
        return;
    }

    if (applyBtn) {
        applyBtn.disabled = true;
        applyBtn.innerText = "Downloading & Applying Update...";
    }
    showToast("Applying update from GitHub Releases...", "info");

    try {
        const res = await fetch("/api/updates/apply/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Update completed successfully!", "success");
            alert(data.message || "Update applied successfully!");
            checkForUpdates(true);
        } else {
            showToast(data.error || "Update failed", "error");
            alert("Update error: " + (data.error || "Failed applying update"));
        }
    } catch (err) {
        showToast("Update error: " + err, "error");
    } finally {
        if (applyBtn) {
            applyBtn.disabled = false;
            applyBtn.innerText = "⚡ Update System Now";
        }
    }
}

async function updateSetting(key, value) {
    try {
        const res = await fetch("/api/settings/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key, value }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Setting updated: ${key}`, "success");
        } else {
            showToast(`Update error: ${data.error || "Unknown error"}`, "error");
        }
    } catch (err) {
        showToast(`Failed to update setting: ${err}`, "error");
    }
}

async function changeActiveBond(selectEl) {
    const code = selectEl.value;
    const name = selectEl.options[selectEl.selectedIndex].text;
    try {
        const res = await fetch("/api/itms/warehouses/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `code=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}`,
        });
        const data = await res.json();
        if (data.success) {
            const badge = document.getElementById("settings-bond-badge");
            if (badge) badge.innerText = `● Active: ${code}`;
            showToast(`Active bond facility set to ${code}`, "success");
        }
    } catch (err) {
        showToast(`Failed to switch bond facility: ${err}`, "error");
    }
}

function _renderWarehouseOptions(targetSel, warehouses, activeBond) {
    if (!targetSel) return;
    const currVal = targetSel.value;
    targetSel.innerHTML = "";
    warehouses.forEach(w => {
        const opt = document.createElement("option");
        opt.value = w.code;
        opt.textContent = `${w.code} - ${w.name}`;
        if (w.code === currVal || (activeBond && w.code === activeBond.code)) {
            opt.selected = true;
        }
        targetSel.appendChild(opt);
    });
}

function _updateBondSyncUI(data) {
    const sel = document.getElementById("settings-bond-select");
    const selItms = document.getElementById("itms-select-bond");
    if (data.warehouses && data.warehouses.length > 0) {
        _renderWarehouseOptions(sel, data.warehouses, data.active_bond);
        _renderWarehouseOptions(selItms, data.warehouses, data.active_bond);
    }
    if (data.active_bond) {
        const badge = document.getElementById("settings-bond-badge");
        if (badge) badge.innerText = `● Active: ${data.active_bond.code}`;
        const itmsBadge = document.getElementById("itms-active-bond-badge");
        if (itmsBadge) itmsBadge.innerText = `${data.active_bond.name} (${data.active_bond.code})`;
    }
}

async function syncBondsFromITMS() {
    const btn = document.getElementById("btn-sync-bonds");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "🔄 Syncing...";
    }
    showToast("Connecting to ITMS to discover bonded warehouses...", "info");
    try {
        const res = await fetch("/api/itms/sync_bonds/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(`Discovered ${data.count || 0} bonded warehouses from ITMS!`, "success");
            _updateBondSyncUI(data);
        } else {
            showToast(data.error || "Could not sync bonds from ITMS.", "warning");
        }
    } catch (err) {
        showToast(`Failed syncing bonds: ${err}`, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "🔄 Sync from ITMS";
        }
    }
}

function onSettingsShiftModeChanged(modeVal) {
    if (typeof setGlobalShiftDate === "function") {
        void setGlobalShiftDate(modeVal);
    } else {
        void updateSetting("sync.shift_target_date_mode", modeVal);
    }
}

function onSettingsManualDateChanged(manualVal) {
    if (!manualVal) return;
    if (typeof setGlobalShiftDate === "function") {
        void setGlobalShiftDate(manualVal);
    } else {
        void updateSetting("sync.manual_target_date", manualVal);
    }
}

function onSettingsDatePickerChanged(isoVal) {
    if (!isoVal) return;
    const manualInput = document.getElementById("settings-manual-date-input");
    if (manualInput) manualInput.value = isoVal;
    const modeSel = document.getElementById("settings-shift-mode-select");
    if (modeSel) modeSel.value = "manual";
    if (typeof setGlobalShiftDate === "function") {
        void setGlobalShiftDate(isoVal);
    }
}

// ============================================================================
// Time-Bound Operator Email Invitation (Web UI)
// ============================================================================

async function sendTimeBoundEmailInvite() {
    const input = document.getElementById("security-preapprove-email");
    const durSel = document.getElementById("security-invite-duration");
    const btn = document.getElementById("btn-send-email-invite");
    const resultEl = document.getElementById("security-preapprove-result");

    if (!input || !input.value.trim()) {
        showToast("Enter an operator email address to invite.", "warning");
        return;
    }

    const email = input.value.trim();
    const durationHours = durSel ? parseInt(durSel.value, 10) || 24 : 24;

    if (btn) {
        btn.disabled = true;
        btn.innerText = "Sending...";
    }

    try {
        const res = await fetch("/api/security/action/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action: "preapprove_email",
                email: email,
                duration_hours: durationHours,
            }),
        });
        const data = await res.json();
        if (data.success) {
            input.value = "";
            showToast(data.message || "Time-bound email invitation sent!", "success");
            if (resultEl) {
                resultEl.innerHTML = `✅ Invited <strong>${escapeHtml(email)}</strong> &bull; Code: <strong style="color:var(--ug-yellow); font-family:var(--font-mono);">${escapeHtml(data.activation_code || "")}</strong> &bull; Valid until: <strong style="color:#6ee7b7;">${escapeHtml(data.expires_at || "")}</strong>`;
            }
        } else {
            showToast(data.error || "Failed to send email invitation.", "error");
        }
    } catch (err) {
        showToast("Invitation error: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "✉️ Send Invite";
        }
    }
}

// Backwards-compatible alias
const preapproveOperatorEmail = sendTimeBoundEmailInvite;
