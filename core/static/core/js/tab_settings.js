/**
 * ITMS CLOSING SYSTEM - TAB 6: SETTINGS & SYSTEM CONFIGURATION
 * Manages simulation mode toggling, database telemetry display,
 * and GitHub Releases automated update detection and in-place upgrade.
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
                desc.innerText = `Running v${escapeHtml(data.current_version || '1.0.0')}. (Offline mode: could not connect to GitHub Releases API)`;
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
});
