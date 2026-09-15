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
