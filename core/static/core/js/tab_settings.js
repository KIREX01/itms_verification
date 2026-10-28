/**
 * ITMS CLOSING SYSTEM - TAB 6: SETTINGS & SYSTEM CONFIGURATION
 * Manages simulation mode toggling, database telemetry display,
 * device/IP/MAC firewall & email access control, and GitHub Releases updates.
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
        const res = await fetch(`/api/updates/check/?force=${force ? 'true' : 'false'}`);
        const data = await res.json();

        if (data.success) {
            if (data.update_available) {
                if (badge) {
                    badge.className = "badge badge-yellow";
                    badge.innerText = `● Update v${data.latest_version} Available`;
                }
                if (desc) {
                    desc.innerHTML = `<strong style="color:var(--ug-yellow);">A new version (v${escapeHtml(data.latest_version)}) is available on GitHub!</strong> Current version: v${escapeHtml(data.current_version)}.`;
                }
                if (applyBtn) {
                    applyBtn.style.display = "inline-flex";
                    if (tagSpan) tagSpan.innerText = `v${escapeHtml(data.latest_version)}`;
                }
                if (notesBox) {
                    notesBox.style.display = "block";
                    if (notesTitle) notesTitle.innerText = `Release Notes: ${escapeHtml(data.release_name || 'v' + data.latest_version)}`;
                    if (notesLink && data.html_url) notesLink.href = data.html_url;
                    if (notesContent) notesContent.innerText = data.release_notes || "No release notes provided for this version.";
                }
                showToast(`New update v${data.latest_version} is available!`, "info");
            } else {
                if (badge) {
                    badge.className = "badge badge-green";
                    badge.innerText = "✓ Up to date";
                }
                if (desc) {
                    desc.innerText = `Running ITMS Verification Copilot v${escapeHtml(data.current_version)}. System is on the latest release.`;
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
            if (desc) {
                desc.innerText = `Running v${escapeHtml(data.current_version || '1.0.2')}. (Offline mode: could not connect to GitHub Releases API)`;
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
    if (!confirm("Are you sure you want to download and apply the update now?\n\nYour local database and evidence photos are strictly protected and will not be touched.")) {
        return;
    }

    if (applyBtn) {
        applyBtn.disabled = true;
        applyBtn.innerText = "Downloading & Applying Update...";
    }
    showToast("Applying update from GitHub Releases...", "info");
    appendConsoleLog("Initiating safe update from GitHub Releases...", "info");

    try {
        const res = await fetch("/api/updates/apply/", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Update completed successfully! Please restart the application.", "success");
            appendConsoleLog(data.message, "success");
            alert(data.message || "Update applied successfully! Please restart the Web Console ('itms') to start using the new version.");
            checkForUpdates(true);
        } else {
            showToast(data.error || "Update failed", "error");
            appendConsoleLog(`Update failed: ${data.error}`, "error");
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

// ============================================================================
// Evidence Vault Storage Folder Management
// ============================================================================

let vaultPresets = {};

async function loadVaultSettings() {
    const input = document.getElementById("settings-vault-path-input");
    const statusBadge = document.getElementById("vault-status-badge");
    const diskBadge = document.getElementById("vault-disk-badge");

    if (!input) return;

    try {
        const res = await fetch("/api/settings/vault-folder/");
        const data = await res.json();
        if (data.success) {
            vaultPresets = data.presets || {};
            input.value = data.vault_path || "";

            if (statusBadge) {
                if (data.is_default) {
                    statusBadge.className = "badge badge-cyan";
                    statusBadge.innerText = `● Default (${data.photo_count || 0} photos)`;
                } else {
                    statusBadge.className = "badge badge-green";
                    statusBadge.innerText = `● Custom Vault (${data.photo_count || 0} photos)`;
                }
            }
            if (diskBadge && data.stats) {
                diskBadge.innerText = `${data.stats.free_gb ?? '--'} GB Free`;
                diskBadge.className = "badge badge-green";
            }
        }
    } catch (err) {
        console.warn("Failed to load vault settings:", err);
    }
}

function setVaultPreset(key) {
    const input = document.getElementById("settings-vault-path-input");
    const msgBox = document.getElementById("vault-message-box");
    if (!input) return;

    if (vaultPresets && vaultPresets[key]) {
        input.value = vaultPresets[key];
    } else if (key === "default") {
        input.value = "media/vault";
    }

    if (msgBox) {
        msgBox.style.display = "block";
        msgBox.style.backgroundColor = "rgba(59, 130, 246, 0.12)";
        msgBox.style.border = "1px solid var(--ug-blue)";
        msgBox.style.color = "var(--ug-blue)";
        msgBox.innerHTML = `Preset selected: <strong>${escapeHtml(input.value)}</strong>. Click <strong>💾 Save Location</strong> to apply.`;
    }
}

async function browseVaultFolder() {
    const browseBtn = document.getElementById("btn-browse-vault");
    const input = document.getElementById("settings-vault-path-input");
    const msgBox = document.getElementById("vault-message-box");

    if (browseBtn) {
        browseBtn.disabled = true;
        browseBtn.innerText = "📁 Opening...";
    }
    showToast("Opening folder browser window...", "info");

    try {
        const res = await fetch("/api/settings/browse-vault-folder/", { method: "POST" });
        const data = await res.json();

        if (data.success && data.selected_path) {
            if (input) input.value = data.selected_path;
            if (msgBox) {
                msgBox.style.display = "block";
                msgBox.style.backgroundColor = "rgba(59, 130, 246, 0.12)";
                msgBox.style.border = "1px solid var(--ug-blue)";
                msgBox.style.color = "var(--ug-blue)";
                msgBox.innerHTML = `Selected folder: <strong>${escapeHtml(data.selected_path)}</strong>. Click <strong>💾 Save Location</strong> to apply.`;
            }
            showToast("Folder selected! Click Save to apply.", "success");
        } else if (data.canceled) {
            // User closed dialog without selecting
        } else if (data.error) {
            showToast("Browse error: " + data.error, "error");
        }
    } catch (err) {
        showToast("Failed to launch folder picker: " + err, "error");
    } finally {
        if (browseBtn) {
            browseBtn.disabled = false;
            browseBtn.innerText = "📁 Browse...";
        }
    }
}

async function saveVaultFolder() {
    const input = document.getElementById("settings-vault-path-input");
    const saveBtn = document.getElementById("btn-save-vault");
    const migrateChk = document.getElementById("settings-vault-migrate-chk");
    const msgBox = document.getElementById("vault-message-box");
    const statusBadge = document.getElementById("vault-status-badge");
    const diskBadge = document.getElementById("vault-disk-badge");

    if (!input) return;
    const targetPath = input.value.trim();
    if (!targetPath) {
        showToast("Please enter or select a folder path.", "warning");
        return;
    }

    const migrate = migrateChk ? migrateChk.checked : false;

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = "💾 Saving...";
    }
    showToast("Updating evidence vault storage location...", "info");

    try {
        const res = await fetch("/api/settings/vault-folder/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                path: targetPath,
                migrate: migrate,
            }),
        });
        const data = await res.json();

        if (data.success) {
            input.value = data.vault_path || targetPath;
            if (statusBadge) {
                if (data.is_default) {
                    statusBadge.className = "badge badge-cyan";
                    statusBadge.innerText = `● Default (${data.photo_count || 0} photos)`;
                } else {
                    statusBadge.className = "badge badge-green";
                    statusBadge.innerText = `● Custom Vault (${data.photo_count || 0} photos)`;
                }
            }
            if (diskBadge && data.stats) {
                diskBadge.innerText = `${data.stats.free_gb ?? '--'} GB Free`;
                diskBadge.className = "badge badge-green";
            }

            let msg = data.message || "Evidence vault location updated successfully.";
            if (data.migrated_count > 0) {
                msg += ` (${data.migrated_count} photos copied to new location).`;
            }

            if (msgBox) {
                msgBox.style.display = "block";
                msgBox.style.backgroundColor = "rgba(34, 197, 94, 0.12)";
                msgBox.style.border = "1px solid var(--ug-green)";
                msgBox.style.color = "var(--ug-green)";
                msgBox.innerText = msg;
            }
            showToast(msg, "success");
            if (typeof appendConsoleLog === "function") {
                appendConsoleLog(msg, "success");
            }
        } else {
            const err = data.error || "Failed to update vault folder.";
            if (msgBox) {
                msgBox.style.display = "block";
                msgBox.style.backgroundColor = "rgba(239, 68, 68, 0.12)";
                msgBox.style.border = "1px solid var(--ug-red)";
                msgBox.style.color = "var(--ug-red)";
                msgBox.innerText = err;
            }
            showToast(err, "error");
        }
    } catch (err) {
        showToast("Error updating vault location: " + err, "error");
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerText = "💾 Save Location";
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    loadVaultSettings();
    loadSecurityOverview(false);
});

async function updateSetting(key, value) {
    try {
        const res = await fetch("/api/settings/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key, value })
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
            body: `code=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}`
        });
        const data = await res.json();
        if (data.success) {
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

function toggleDeveloperSettingsMode(chk) {
    const devContainer = document.getElementById("developer-settings-section");
    if (devContainer) {
        devContainer.style.display = chk.checked ? "block" : "none";
    }
    void updateSetting("system.developer_mode", chk.checked);
}

async function browseGenericPath(inputId, type, title, settingKey) {
    const input = document.getElementById(inputId);
    showToast(`Opening ${type} browser window...`, "info");
    
    try {
        const payload = {
            type: type,
            title: title,
            initial_dir: input ? input.value : ""
        };
        const res = await fetch("/api/settings/browse-path/", { 
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success && data.selected_path) {
            if (input) {
                input.value = data.selected_path;
            }
            if (settingKey) {
                await updateSetting(settingKey, data.selected_path);
            }
        } else if (data.canceled) {
            // canceled
        } else if (data.error) {
            showToast("Browse error: " + data.error, "error");
        }
    } catch (err) {
        showToast("Failed to launch browser: " + err, "error");
    }
}

// -----------------------------------------------------------------------------
// Operating Shift Target Date Handlers
// -----------------------------------------------------------------------------

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
    if (manualInput) {
        manualInput.value = isoVal;
    }
    const modeSel = document.getElementById("settings-shift-mode-select");
    if (modeSel) {
        modeSel.value = "manual";
    }
    if (typeof setGlobalShiftDate === "function") {
        void setGlobalShiftDate(isoVal);
    }
}

// ============================================================================
// Device & Email Access Control (IP / MAC Firewall & Shared Devices)
// ============================================================================

async function loadSecurityOverview(notify = false) {
    const reqTbody = document.getElementById("security-requests-tbody");
    const devTbody = document.getElementById("security-devices-tbody");
    if (!reqTbody && !devTbody) return;

    try {
        const res = await fetch("/api/security/overview/");
        const data = await res.json();
        if (!data.success) return;

        const cfg = data.config || {};
        const strictChk = document.getElementById("security-strict-email-toggle");
        if (strictChk) strictChk.checked = Boolean(cfg.require_email_approval);

        const maxDevInput = document.getElementById("security-max-devices-input");
        if (maxDevInput && cfg.max_devices_per_account) {
            maxDevInput.value = cfg.max_devices_per_account;
        }

        const modeBadge = document.getElementById("security-mode-badge");
        if (modeBadge) {
            modeBadge.className = cfg.require_email_approval ? "badge badge-yellow" : "badge badge-green";
            modeBadge.innerText = cfg.require_email_approval ? "● Strict Pre-Approval" : "● Active Firewall";
        }

        const blockedSummary = document.getElementById("security-blocked-summary");
        if (blockedSummary) {
            const ips = (cfg.blocked_ips || []).length;
            const macs = (cfg.blocked_macs || []).length;
            const cidrs = (cfg.blocked_cidrs || []).length;
            blockedSummary.innerText = `Blocked IPs: ${ips} | Blocked Subnets: ${cidrs} | Blocked MACs: ${macs}`;
        }

        _renderSecurityRequestsTable(reqTbody, data.access_requests || []);
        _renderSecurityDevicesTable(devTbody, data.devices || []);

        if (notify) {
            showToast("Security & device access registry refreshed.", "info");
        }
    } catch (err) {
        console.warn("Failed to load security overview:", err);
    }
}

function _renderSecurityRequestsTable(tbody, requests) {
    if (!tbody) return;
    if (!requests.length) {
        tbody.innerHTML = `<tr><td colspan="6" style="padding:10px; text-align:center; color:var(--ug-text-dim);">No pending or recent email activation requests.</td></tr>`;
        return;
    }

    tbody.innerHTML = requests.map(r => {
        const badgeCls = (r.status === "APPROVED" || r.status === "PRE_APPROVED")
            ? "badge-green"
            : (r.status === "PENDING" ? "badge-yellow" : "badge-red");
        const ipMac = `${escapeHtml(r.ip_address || "-")} / ${escapeHtml(r.mac_address || "-")}`;
        return `<tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 8px;">
                <div style="font-weight:700; color:#fff;">${escapeHtml(r.email)}</div>
                <div style="font-size:0.68rem; color:var(--ug-text-muted);">${escapeHtml(r.full_name || r.username || "")}</div>
            </td>
            <td style="padding:6px 8px;">${escapeHtml(r.requested_role || "SHARED_TEAM")} (${escapeHtml(r.facility_code || "DEFAULT")})</td>
            <td style="padding:6px 8px; font-family:var(--font-mono); font-size:0.7rem;">${ipMac}</td>
            <td style="padding:6px 8px; font-family:var(--font-mono); font-weight:800; color:var(--ug-yellow);">${escapeHtml(r.activation_code || "-")}</td>
            <td style="padding:6px 8px;"><span class="badge ${badgeCls}" style="font-size:0.65rem;">${escapeHtml(r.status)}</span></td>
            <td style="padding:6px 8px; text-align:right; white-space:nowrap;">
                ${r.status !== "APPROVED" ? `<button class="btn btn-primary" style="padding:3px 8px; font-size:0.68rem;" onclick="securityDeviceAction('approve_request', {request_id: ${r.id}})">Approve</button>` : ""}
                ${r.status !== "REJECTED" ? `<button class="btn btn-secondary" style="padding:3px 8px; font-size:0.68rem; border-color:#ef4444; color:#fca5a5;" onclick="securityDeviceAction('reject_request', {request_id: ${r.id}})">Reject</button>` : ""}
            </td>
        </tr>`;
    }).join("");
}

function _renderSecurityDevicesTable(tbody, devices) {
    if (!tbody) return;
    if (!devices.length) {
        tbody.innerHTML = `<tr><td colspan="7" style="padding:10px; text-align:center; color:var(--ug-text-dim);">No devices recorded yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = devices.map(d => {
        const badgeCls = d.status === "APPROVED"
            ? "badge-green"
            : (d.status === "PENDING" ? "badge-yellow" : "badge-red");
        const isBlocked = d.status === "BLOCKED";
        return `<tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 8px;">
                <div style="font-weight:700; color:#fff;">${escapeHtml(d.device_name || d.device_type)}</div>
                <div style="font-size:0.66rem; color:var(--ug-text-dim); font-family:var(--font-mono);">${escapeHtml((d.device_id || "").slice(0, 14))}</div>
            </td>
            <td style="padding:6px 8px;">
                <div style="color:#e2e8f0;">${escapeHtml(d.username || "-")}</div>
                <div style="font-size:0.68rem; color:var(--ug-text-muted);">${escapeHtml(d.authorized_email || "")}</div>
            </td>
            <td style="padding:6px 8px; font-family:var(--font-mono); color:#93c5fd;">${escapeHtml(d.ip_address || "-")}</td>
            <td style="padding:6px 8px; font-family:var(--font-mono); color:#fcd34d;">${escapeHtml(d.mac_address || "LAN/Hidden")}</td>
            <td style="padding:6px 8px;"><span class="badge badge-cyan" style="font-size:0.65rem;">${escapeHtml(d.linked_via || "LOGIN")}</span></td>
            <td style="padding:6px 8px;"><span class="badge ${badgeCls}" style="font-size:0.65rem;">${escapeHtml(d.status)}</span></td>
            <td style="padding:6px 8px; text-align:right; white-space:nowrap;">
                ${isBlocked
                    ? `<button class="btn btn-primary" style="padding:3px 8px; font-size:0.68rem;" onclick="securityDeviceAction('unblock_target', {target: '${escapeHtml(d.device_id)}'})">Unblock</button>`
                    : `<button class="btn btn-secondary" style="padding:3px 8px; font-size:0.68rem; border-color:#ef4444; color:#fca5a5;" onclick="securityDeviceAction('block_target', {target: '${escapeHtml(d.device_id)}'})">Block Device</button>`
                }
                ${d.ip_address && !isBlocked
                    ? `<button class="btn btn-secondary" style="padding:3px 8px; font-size:0.68rem;" title="Block IP ${escapeHtml(d.ip_address)}" onclick="securityDeviceAction('block_target', {target: '${escapeHtml(d.ip_address)}'})">Block IP</button>`
                    : ""
                }
            </td>
        </tr>`;
    }).join("");
}

async function securityDeviceAction(action, payload = {}) {
    try {
        const res = await fetch("/api/security/action/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, ...payload }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Security policy updated.", "success");
            await loadSecurityOverview(false);
        } else {
            showToast(data.error || "Action failed.", "error");
        }
        return data;
    } catch (err) {
        showToast("Security action error: " + err, "error");
        return { success: false };
    }
}

async function toggleStrictEmailApproval(enabled) {
    await securityDeviceAction("update_config", {
        require_email_approval: Boolean(enabled),
        mode: enabled ? "STRICT" : "STANDARD",
    });
}

async function updateMaxSharedDevices(maxCount) {
    if (!maxCount || maxCount < 1) return;
    await securityDeviceAction("update_config", {
        max_devices_per_account: maxCount,
    });
}

async function preapproveOperatorEmail() {
    const input = document.getElementById("security-preapprove-email");
    const resultEl = document.getElementById("security-preapprove-result");
    if (!input || !input.value.trim()) {
        showToast("Enter an email address to pre-approve.", "warning");
        return;
    }
    const email = input.value.trim();
    const data = await securityDeviceAction("preapprove_email", { email });
    if (data && data.success) {
        input.value = "";
        if (resultEl) {
            resultEl.innerHTML = `✅ Pre-approved <strong>${escapeHtml(email)}</strong> &bull; Activation Code: <strong style="color:var(--ug-yellow); font-family:var(--font-mono);">${escapeHtml(data.activation_code || "")}</strong>`;
        }
    }
}

async function blockNetworkTarget() {
    const input = document.getElementById("security-block-target");
    if (!input || !input.value.trim()) {
        showToast("Enter an IP, CIDR subnet, MAC address, or Device ID to block.", "warning");
        return;
    }
    const target = input.value.trim();
    const data = await securityDeviceAction("block_target", { target });
    if (data && data.success) {
        input.value = "";
    }
}
