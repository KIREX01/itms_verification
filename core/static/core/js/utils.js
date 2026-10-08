/**
 * ITMS CLOSING SYSTEM - UI UTILITIES & MODAL CONTROLLERS
 * Shared modal, lightbox, toast notification, and DOM text helpers.
 */

function openModal(id) {
    if (id === "modal-link-order") id = "modal-edit-plate";
    const m = document.getElementById(id);
    if (m) m.classList.add("active");
}

function closeModal(id) {
    if (id === "modal-link-order") id = "modal-edit-plate";
    const m = document.getElementById(id);
    if (m) m.classList.remove("active");
}

function showTopSubmissionIndicator(text) {
    const pill = document.getElementById("header-submission-indicator");
    const span = document.getElementById("header-submission-text");
    if (span) span.innerText = text;
    if (pill) pill.style.display = "inline-flex";
}

function hideTopSubmissionIndicator() {
    const pill = document.getElementById("header-submission-indicator");
    if (pill) pill.style.display = "none";
}

function reopenSubmissionModal() {
    openModal("modal-submission-progress");
}

function minimizeSubmissionModal() {
    closeModal("modal-submission-progress");
}

function closeSubmissionModal() {
    closeModal("modal-submission-progress");
    hideTopSubmissionIndicator();
}

function openPhotoModal(side) {
    if (!selectedPairDetail) return;
    const img = (side === "front") 
        ? (selectedPairDetail.front || selectedPairDetail.front_image) 
        : (selectedPairDetail.rear || selectedPairDetail.rear_image);
    if (img && img.url) {
        openLightbox(img.url);
    }
}

function openLightbox(url) {
    const img = document.getElementById("lightbox-img");
    if (img) img.src = url;
    openModal("modal-photo-lightbox");
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 250);
    }, 3500);
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

/* ==========================================================================
   Mobile Camera Connect & QR Code Modal Controllers
   ========================================================================== */

let currentMobileQr = null;

async function openMobileConnectModal() {
    // If user is already on a mobile device, just redirect them to the mobile companion
    if (/Mobi|Android/i.test(navigator.userAgent)) {
        window.location.href = '/mobile/';
        return;
    }
    openModal("modal-mobile-connect");
    await loadMobileNetworkInfo();
}

async function loadMobileNetworkInfo() {
    try {
        const resp = await fetch("/api/network/info/");
        if (!resp.ok) return;
        const data = await resp.json();

        const input = document.getElementById("mobile-url-input");
        const select = document.getElementById("mobile-ip-select");
        const badge = document.getElementById("mobile-modal-hotspot-badge");

        if (input) input.value = data.primary_url;

        if (badge) {
            if (data.hotspot_detected) {
                badge.className = "badge badge-green";
                badge.textContent = "Hotspot Active (192.168.137.1)";
            } else {
                badge.className = "badge badge-yellow";
                badge.textContent = "Wi-Fi / LAN Mode";
            }
        }

        if (select && data.candidate_urls) {
            select.innerHTML = data.candidate_urls.map(item => `
                <option value="${escapeHtml(item.url)}" ${item.is_primary ? "selected" : ""}>
                    ${escapeHtml(item.ip)} &bull; ${escapeHtml(item.type)}
                </option>
            `).join("");
        }

        renderMobileQrCode(data.primary_url);
        fetchMobileStats();
    } catch (err) {
        console.error("Failed to load network info:", err);
    }
}

function onMobileIpChanged(selectedUrl) {
    const input = document.getElementById("mobile-url-input");
    if (input) input.value = selectedUrl;
    renderMobileQrCode(selectedUrl);
}

function renderMobileQrCode(url) {
    const container = document.getElementById("mobile-qr-container");
    if (!container) return;

    container.innerHTML = "";
    if (window.QRCode) {
        currentMobileQr = new QRCode(container, {
            text: url,
            width: 156,
            height: 156,
            colorDark: "#000000",
            colorLight: "#ffffff",
            correctLevel: QRCode.CorrectLevel.M
        });
    } else {
        container.innerHTML = `<a href="${escapeHtml(url)}" target="_blank" style="font-size:0.75rem; color:#0284c7; word-break:break-all;">${escapeHtml(url)}</a>`;
    }
}

function copyMobileUrl() {
    const input = document.getElementById("mobile-url-input");
    const status = document.getElementById("mobile-copy-status");
    if (!input) return;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(() => {
            if (status) {
                status.style.display = "inline";
                setTimeout(() => { status.style.display = "none"; }, 2500);
            }
        }).catch(() => {
            input.select();
            document.execCommand("copy");
        });
    } else {
        input.select();
        document.execCommand("copy");
        if (status) {
            status.style.display = "inline";
            setTimeout(() => { status.style.display = "none"; }, 2500);
        }
    }
}

let mobileStatusPollInterval = null;

async function pollMobileCompanionStatus() {
    try {
        const resp = await fetch("/api/mobile/status/", { cache: "no-store" });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success) return;

        const devCount = data.active_devices_count || 0;
        const maxDev = data.max_devices_allowed || 2;
        const devices = data.active_devices || [];
        const devNames = devices.map(d => d.device_name || "Phone").join(", ");

        const batchLimit = data.batch_max_photos || 200;
        const batchPhotos = data.total_photos || 0;
        const batchLabel = data.batch_label || "Active Batch";

        // 1. Live Header Mobile Badge Update
        const headerBadge = document.getElementById("header-mobile-badge");
        if (headerBadge) {
            if (devCount > 0) {
                headerBadge.className = "badge badge-green";
                headerBadge.style.background = "";
                headerBadge.style.color = "";
                headerBadge.style.border = "";
                headerBadge.innerHTML = `📱 ${devCount}/${maxDev} Connected <span style="font-size:0.7rem; font-weight:400; opacity:0.9;">(${escapeHtml(devNames)})</span>`;
                headerBadge.title = `Active phone(s): ${devNames}. Click to view connection guide.`;
            } else {
                headerBadge.className = "badge";
                headerBadge.style.background = "rgba(2, 132, 199, 0.2)";
                headerBadge.style.color = "#7dd3fc";
                headerBadge.style.border = "1px solid rgba(2, 132, 199, 0.4)";
                headerBadge.textContent = `📱 Mobile (0/${maxDev})`;
                headerBadge.title = "No phone connected. Click to pair via QR code or Hotspot.";
            }
        }

        // 2. Live Dashboard Panel Elements (Tab 1)
        const dashCounter = document.getElementById("dash-mobile-photos-counter");
        const dashDevPill = document.getElementById("dash-mobile-devices-pill");
        const dashDevNames = document.getElementById("dash-mobile-device-names");

        if (dashDevPill) {
            if (devCount > 0) {
                dashDevPill.className = "badge badge-green";
                dashDevPill.textContent = `📱 ${devCount}/${maxDev} Phones Connected`;
            } else {
                dashDevPill.className = "badge badge-yellow";
                dashDevPill.textContent = `📱 0/${maxDev} Phones Connected`;
            }
        }
        if (dashDevNames) {
            dashDevNames.textContent = devCount > 0 ? `(${devNames})` : `(Ready to pair)`;
        }
        if (dashCounter) {
            const pairsCount = Math.floor(batchPhotos / 2);
            dashCounter.textContent = `📦 ${batchLabel}: ${batchPhotos}/${batchLimit} photos (${pairsCount} pairs)`;
        }

        // 3. Live Connect Modal Elements (if open)
        const modalDevBadge = document.getElementById("modal-connected-devices-badge");
        const modalCapBadge = document.getElementById("modal-batch-capacity-badge");
        const modalDevDetail = document.getElementById("modal-connected-devices-detail");

        if (modalDevBadge) {
            if (devCount > 0) {
                modalDevBadge.style.color = "var(--ug-green)";
                modalDevBadge.textContent = `● 📱 ${devCount}/${maxDev} Phones Connected`;
            } else {
                modalDevBadge.style.color = "var(--ug-yellow)";
                modalDevBadge.textContent = `📱 0/${maxDev} Phones Connected (Offline)`;
            }
        }
        if (modalCapBadge) {
            modalCapBadge.textContent = `${batchLabel}: ${batchPhotos}/${batchLimit} photos`;
        }
        if (modalDevDetail) {
            if (devCount > 0) {
                modalDevDetail.innerHTML = devices.map(d =>
                    `🟢 <strong>${escapeHtml(d.device_name)}</strong> (${escapeHtml(d.client_ip || "Local")}) &bull; Mode: <em>${escapeHtml(d.mode)}</em> &bull; Online ${d.online_seconds || 0}s`
                ).join("<br>");
            } else {
                modalDevDetail.textContent = "No phones currently connected. Point phone camera at QR code to pair.";
            }
        }
    } catch (err) {
        // silent
    }
}

async function fetchMobileStats() {
    await pollMobileCompanionStatus();
}

// Start real-time mobile status polling on DOMContentLoaded
document.addEventListener("DOMContentLoaded", () => {
    pollMobileCompanionStatus();
    if (!mobileStatusPollInterval) {
        mobileStatusPollInterval = setInterval(pollMobileCompanionStatus, 2500);
    }
});
