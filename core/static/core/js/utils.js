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
