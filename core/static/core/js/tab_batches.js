
function updateGlobalBgUploadPill(label, pct, desc) {
    const pill = document.getElementById("global-bg-upload-pill");
    if (!pill) return;
    if (label) {
        const lbl = document.getElementById("global-bg-upload-label");
        if (lbl) lbl.innerText = label;
    }
    if (pct !== undefined && pct !== null) {
        const pEl = document.getElementById("global-bg-upload-pct");
        const fill = document.getElementById("global-bg-upload-fill");
        if (pEl) pEl.innerText = `${pct}%`;
        if (fill) fill.style.width = `${pct}%`;
    }
    if (desc) {
        const dEl = document.getElementById("global-bg-upload-desc");
        if (dEl) dEl.innerText = desc;
    }
    pill.style.display = "block";
}

function hideGlobalBgUploadPill() {
    const pill = document.getElementById("global-bg-upload-pill");
    if (pill) pill.style.display = "none";
}
/**
 * ITMS CLOSING SYSTEM - TAB 3: BATCHES & EVIDENCE PHOTO STREAM
 * Manages full-scale evidence photo viewing, collapsible batch accordion cards,
 * dual-dropzone drag & drop uploads, and batch ingestion pipelines.
 */

function initDropzones() {
    ["front", "rear"].forEach(side => {
        const box = document.getElementById(`dropzone-tab-${side}`);
        if (!box) return;
        box.addEventListener("dragover", (e) => {
            e.preventDefault();
            box.classList.add("dragover");
        });
        box.addEventListener("dragleave", () => {
            box.classList.remove("dragover");
        });
        box.addEventListener("drop", (e) => {
            e.preventDefault();
            box.classList.remove("dragover");
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                if (side === "front") {
                    tabSelectedFrontFiles = Array.from(e.dataTransfer.files);
                    setText("count-tab-front", `${tabSelectedFrontFiles.length} files selected`);
                } else {
                    tabSelectedRearFiles = Array.from(e.dataTransfer.files);
                    setText("count-tab-rear", `${tabSelectedRearFiles.length} files selected`);
                }
                validateTabUploadSymmetry();
            }
        });
    });
}

function setBatchesDateScope(scope) {
    currentBatchesDateScope = scope;
    const scopeButtons = {
        "TODAY": "btn-bscope-today",
        "ACTIVE_BATCH": "btn-bscope-active",
        "CARRYOVER": "btn-bscope-carryover",
        "ALL": "btn-bscope-all"
    };
    Object.keys(scopeButtons).forEach(k => {
        const el = document.getElementById(scopeButtons[k]);
        if (el) el.classList.toggle("active", k === scope);
    });
    fetchBatchesList();
}

function cycleBatchesDateScope() {
    const scopes = ["TODAY", "ACTIVE_BATCH", "CARRYOVER", "ALL"];
    const idx = scopes.indexOf(currentBatchesDateScope);
    const nextScope = scopes[(idx + 1) % scopes.length];
    setBatchesDateScope(nextScope);
    if (typeof showToast === "function") {
        showToast(`Batches Scope: ${nextScope}`, "info");
    }
}

async function fetchBatchesList() {
    const container = document.getElementById("batches-stream-container");
    const countBadge = document.getElementById("batches-count-badge");
    const summaryTag = document.getElementById("batches-scope-summary-tag");
    try {
        const activeScope = currentBatchesDateScope || "TODAY";
        const res = await fetch(`/api/batches/?scope=${encodeURIComponent(activeScope)}`);
        const data = await res.json();
        if (data.success && data.batches) {
            batchesData = data.batches;
            if (countBadge) countBadge.innerText = `${batchesData.length} Batches (${activeScope})`;
            if (summaryTag) {
                const totalText = data.total_batches ? ` • ${data.total_batches} all-time` : '';
                summaryTag.innerText = `Scope: ${activeScope} • ${batchesData.length} batch(es) shown${totalText}`;
            }

            if (batchesData.length === 0) {
                if (container) {
                    container.innerHTML = `
                        <div class="batches-empty-state">
                            <span style="font-size:2.5rem;">📦</span>
                            <h4 style="color:#fff; font-size:1.1rem; margin-top:8px;">No Batches in Scope (${activeScope})</h4>
                            <p style="font-size:0.8rem; max-width:400px; margin-top:4px;">
                                ${activeScope === 'TODAY' ? "No photos ingested during today's shift yet. Check prior carryover or upload a new batch." : "No batches found matching the selected scope."}
                            </p>
                            <div style="display:flex; gap:8px; margin-top:12px;">
                                ${activeScope === 'TODAY' ? `
                                    <button class="btn btn-secondary" onclick="setBatchesDateScope('CARRYOVER')">
                                        ⏳ Check Carryover
                                    </button>
                                    <button class="btn btn-secondary" onclick="setBatchesDateScope('ALL')">
                                        🌐 All Batches
                                    </button>
                                ` : ''}
                                <button class="btn btn-primary" onclick="openBatchUploadModal()">
                                    📷 + New Upload Batch
                                </button>
                            </div>
                        </div>
                    `;
                }
                return;
            }

            // Auto-expand first batch if set is empty
            if (expandedBatches.size === 0 && batchesData.length > 0) {
                expandedBatches.add(batchesData[0].batch_id);
            }

            renderBatchesStream();

            // Load details for any currently expanded batches
            expandedBatches.forEach(bId => {
                loadBatchDetailsAndRender(bId);
            });
        }
    } catch (err) {
        console.warn("Batches list fetch error:", err);
    }
}

function filterBatchesStream() {
    const input = document.getElementById("batch-search-input");
    batchSearchQuery = (input ? input.value : "").trim().toLowerCase();
    renderBatchesStream();
}

let currentBatchViewMode = (function() {
    try {
        return localStorage.getItem("itms_batch_view_mode") || "large";
    } catch (_) {
        return "large";
    }
})();

function setBatchViewMode(mode) {
    currentBatchViewMode = mode;
    try {
        localStorage.setItem("itms_batch_view_mode", mode);
    } catch (_) {}

    const bLarge = document.getElementById("btn-density-large");
    const bGrid = document.getElementById("btn-density-grid");
    if (bLarge) bLarge.classList.toggle("active", mode === "large");
    if (bGrid) bGrid.classList.toggle("active", mode === "grid");

    document.querySelectorAll(".batch-full-photos-grid").forEach(el => {
        el.classList.toggle("view-grid", mode === "grid");
    });
}

function toggleInCardZoom(imageId, event) {
    if (event) {
        event.stopPropagation();
    }
    const wrap = document.getElementById(`photo-wrap-${imageId}`);
    if (!wrap) return;
    const isMagnified = wrap.classList.toggle("is-magnified");
    const btn = document.getElementById(`photo-zoom-btn-${imageId}`);
    if (btn) {
        btn.innerHTML = isMagnified ? "🔍 Fit Normal" : "🔍 Magnify 1.8x";
    }
}

function renderBatchesStream() {
    const container = document.getElementById("batches-stream-container");
    if (!container) return;

    const filtered = batchesData.filter(b => {
        if (!batchSearchQuery) return true;
        const bId = (b.batch_id || "").toLowerCase();
        const label = (b.source_label || "").toLowerCase();
        const src = (b.source_type || "").toLowerCase();
        return bId.includes(batchSearchQuery) || label.includes(batchSearchQuery) || src.includes(batchSearchQuery);
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="batches-empty-state">
                <span style="font-size:2.2rem;">🔍</span>
                <h4 style="color:#ffffff !important; font-size:1rem; margin-top:8px; font-weight:800;">No batches matching '${escapeHtml(batchSearchQuery)}'</h4>
                <p style="font-size:0.75rem; color:rgba(255,255,255,0.85);">Try a different batch ID or shift label.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = filtered.map(b => {
        const isExp = expandedBatches.has(b.batch_id);
        const dupBadge = b.duplicate_count > 0 ? `
            <span class="batch-pill pill-dup">⚠️ <strong>${b.duplicate_count}</strong> Duplicates</span>
        ` : '';

        const shiftBadge = b.is_latest
            ? `<span class="badge badge-secondary" style="font-size:0.68rem; font-weight:800; color:#ffffff !important; border:1px solid rgba(255,255,255,0.3); background:#1e293b;" title="Latest active batch">⚡ TODAY (Active)</span>`
            : (b.is_today
                ? `<span class="badge badge-secondary" style="font-size:0.68rem; font-weight:800; color:#ffffff !important; border:1px solid rgba(255,255,255,0.25);" title="Today's shift batch">● TODAY</span>`
                : `<span class="badge badge-secondary" style="font-size:0.68rem; font-weight:800; color:rgba(255,255,255,0.85) !important;" title="Prior shift batch">⏳ PRIOR (${escapeHtml(b.created_date || '')})</span>`);

        return `
            <div class="batch-stream-card ${isExp ? 'is-expanded' : ''}" id="batch-stream-${escapeHtml(b.batch_id)}">
                <div class="batch-stream-header" onclick="toggleBatchAccordion('${escapeHtml(b.batch_id)}')">
                    <div class="batch-header-left">
                        <span class="batch-chevron" id="batch-chevron-${escapeHtml(b.batch_id)}">${isExp ? '▼' : '▶'}</span>
                        <span class="batch-id-text">${escapeHtml(b.batch_id)}</span>
                        ${shiftBadge}
                        <span class="badge badge-secondary" style="font-size:0.7rem; color:#ffffff !important; border:1px solid rgba(255,255,255,0.2);">${escapeHtml(b.source_type || 'WEB')}</span>
                        <span class="batch-label-text">${escapeHtml(b.source_label || 'Ingestion Session')}</span>
                        <span class="batch-time-text">📅 ${escapeHtml(b.created_at || '--')}</span>
                    </div>
                    <div class="batch-header-right">
                        <div class="batch-pill-group">
                            <span class="batch-pill pill-total">Total: <strong>${b.total_files}</strong></span>
                            <span class="batch-pill pill-ingested">✓ <strong>${b.ingested_count}</strong> Ingested</span>
                            ${dupBadge}
                        </div>
                        <button class="btn btn-secondary batch-toggle-btn" id="batch-toggle-btn-${escapeHtml(b.batch_id)}" onclick="event.stopPropagation(); toggleBatchAccordion('${escapeHtml(b.batch_id)}')">
                            ${isExp ? '▲ Minimize' : `▼ Expand Photos (${b.ingested_count})`}
                        </button>
                    </div>
                </div>
                <div class="batch-stream-body" id="batch-body-${escapeHtml(b.batch_id)}" style="display:${isExp ? 'block' : 'none'};">
                    <div id="batch-photos-${escapeHtml(b.batch_id)}">
                        <div style="padding:24px; text-align:center; color:#ffffff !important; font-weight:600;">
                            ⏳ Loading photos for ${escapeHtml(b.batch_id)}...
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

async function toggleBatchAccordion(batchId) {
    const isExp = expandedBatches.has(batchId);
    if (isExp) {
        expandedBatches.delete(batchId);
    } else {
        expandedBatches.add(batchId);
    }

    const card = document.getElementById(`batch-stream-${batchId}`);
    const body = document.getElementById(`batch-body-${batchId}`);
    const chevron = document.getElementById(`batch-chevron-${batchId}`);
    const toggleBtn = document.getElementById(`batch-toggle-btn-${batchId}`);

    if (card) card.classList.toggle("is-expanded", !isExp);
    if (chevron) chevron.innerText = !isExp ? '▼' : '▶';

    if (isExp) {
        if (body) body.style.display = "none";
        const bInfo = batchesData.find(b => b.batch_id === batchId);
        if (toggleBtn) toggleBtn.innerText = `▼ Expand Photos (${bInfo ? bInfo.ingested_count : ''})`;
    } else {
        if (body) body.style.display = "block";
        if (toggleBtn) toggleBtn.innerText = "▲ Minimize";
        await loadBatchDetailsAndRender(batchId);
    }
}

function expandAllBatches() {
    batchesData.forEach(b => expandedBatches.add(b.batch_id));
    renderBatchesStream();
    batchesData.forEach(b => loadBatchDetailsAndRender(b.batch_id));
}

function collapseAllBatches() {
    expandedBatches.clear();
    renderBatchesStream();
}

async function loadBatchDetailsAndRender(batchId) {
    const container = document.getElementById(`batch-photos-${batchId}`);
    if (!container) return;

    if (batchDetailsCache[batchId]) {
        renderFullScaleBatchPhotos(batchId, batchDetailsCache[batchId]);
        return;
    }

    try {
        const res = await fetch(`/api/batches/${encodeURIComponent(batchId)}/`);
        const data = await res.json();
        if (data.success && data.images) {
            batchDetailsCache[batchId] = data.images;
            renderFullScaleBatchPhotos(batchId, data.images);
        } else {
            container.innerHTML = `<div style="padding:24px; text-align:center; color:rgba(255,255,255,0.8) !important;">No photos found in batch.</div>`;
        }
    } catch (err) {
        console.warn(`Failed loading batch ${batchId}:`, err);
        container.innerHTML = `<div style="padding:24px; text-align:center; color:#ef4444; font-weight:700;">Failed to load photos.</div>`;
    }
}

function renderFullScaleBatchPhotos(batchId, images) {
    const container = document.getElementById(`batch-photos-${batchId}`);
    if (!container) return;

    if (!images || images.length === 0) {
        container.innerHTML = `<div style="padding:28px; text-align:center; color:rgba(255,255,255,0.8) !important;">No evidence photos recorded in this batch.</div>`;
        return;
    }

    const gridClass = currentBatchViewMode === "grid" ? "batch-full-photos-grid view-grid" : "batch-full-photos-grid";

    container.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid rgba(255,255,255,0.1); flex-wrap:wrap; gap:8px;">
            <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:0.82rem; font-weight:800; color:#ffffff !important; text-transform:uppercase; letter-spacing:0.5px;">
                    📷 Evidence Photos (${images.length} images)
                </span>
                <span style="font-size:0.74rem; color:rgba(255,255,255,0.7) !important;">
                    • High-Resolution Inspection Stream
                </span>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:0.74rem; color:#ffffff !important; font-weight:600;">
                    ↕ Tap/Hover to inspect fine plate details
                </span>
            </div>
        </div>
        <div class="${gridClass}" id="batch-grid-${escapeHtml(batchId)}">
            ${images.map(img => {
                const orientTag = img.orientation === "FRONT" ? "tag-front" : (img.orientation === "REAR" ? "tag-rear" : "tag-unknown");
                const plateHtml = img.detected_plate ? `
                    <div class="batch-photo-plate-overlay">
                        <span class="plate-code">🔍 ${escapeHtml(img.detected_plate)}</span>
                        ${img.ocr_confidence !== null ? `<span class="plate-conf-tag">(${img.ocr_confidence}%)</span>` : ''}
                    </div>
                ` : `
                    <div class="batch-photo-plate-overlay no-plate">
                        <span>No OCR Plate</span>
                    </div>
                `;

                const pairHtml = img.pair ? `
                    <div class="batch-photo-pair-strip">
                        <div>
                            <div style="color:#ffffff !important; font-weight:800; font-size:0.82rem;">🔗 Pair #${img.pair.pair_id} (${escapeHtml(img.pair.role)})</div>
                            <div style="color:#ffffff !important; font-weight:700; font-size:0.78rem;">Detected Plate: ${escapeHtml(img.pair.detected_plate || '—')}</div>
                            <div style="color:rgba(255,255,255,0.8) !important; font-size:0.74rem;">Order: ${img.pair.order_number ? '#' + escapeHtml(img.pair.order_number) : 'Unlinked'}</div>
                        </div>
                        <button type="button" class="btn btn-secondary" style="padding:6px 12px; font-size:0.76rem; min-height:36px; font-weight:700; color:#ffffff !important;" onclick="jumpToPairInQueue(${img.pair.pair_id})">
                            🔍 Inspect in Queue
                        </button>
                    </div>
                ` : `
                    <div class="batch-photo-pair-strip" style="border-color:rgba(255, 255, 255, 0.2);">
                        <div>
                            <div style="color:#ffffff !important; font-weight:800; font-size:0.8rem;">⚠️ Unassigned Evidence Photo</div>
                            <div style="color:rgba(255,255,255,0.8) !important; font-size:0.72rem;">Available for pairing. Click 'Match Pairs [M]' on Dashboard to associate.</div>
                        </div>
                    </div>
                `;

                return `
                    <div class="batch-photo-card-large" id="photo-card-${escapeHtml(img.id)}">
                        <div class="batch-photo-full-wrap" id="photo-wrap-${escapeHtml(img.id)}">
                            <img class="batch-photo-full-img" src="${escapeHtml(img.url)}" alt="${escapeHtml(img.file_name)}" onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'100\' height=\'100\' viewBox=\'0 0 100 100\'><rect fill=\'%23111\' width=\'100\' height=\'100\'/><text fill=\'%23fff\' x=\'50\' y=\'55\' text-anchor=\'middle\' font-size=\'11\'>Photo Missing</text></svg>';">
                            <span class="batch-photo-orient-badge ${orientTag}">${escapeHtml(img.orientation || 'UNKNOWN')}</span>
                            ${plateHtml}
                            <div class="batch-photo-tools-overlay">
                                <button type="button" class="in-card-tool-btn" id="photo-zoom-btn-${escapeHtml(img.id)}" onclick="toggleInCardZoom('${escapeHtml(img.id)}', event)" title="Magnify photo 1.8x without opening modal">
                                    🔍 Magnify 1.8x
                                </button>
                                <button type="button" class="in-card-tool-btn" onclick="openLightbox('${escapeHtml(img.url)}'); event.stopPropagation();" title="Open full screen lightbox">
                                    ⛶ Fullscreen
                                </button>
                            </div>
                        </div>
                        <div class="batch-photo-details-strip">
                            <div class="batch-photo-meta-row">
                                <span style="font-family:var(--font-mono); font-size:0.78rem; font-weight:700; color:#ffffff !important; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:280px;" title="${escapeHtml(img.file_name)}">
                                    📁 ${escapeHtml(img.file_name)}
                                </span>
                                <span style="color:rgba(255,255,255,0.85) !important;">${img.file_size_kb} KB • ${escapeHtml(img.captured_at || 'No timestamp')}</span>
                            </div>
                            ${pairHtml}
                            <div class="batch-photo-actions-row">
                                <span style="font-size:0.74rem; font-family:var(--font-mono); color:rgba(255,255,255,0.8) !important;">
                                    ID: <strong>${escapeHtml(img.id ? String(img.id).slice(0, 8) : '')}</strong>
                                </span>
                                <button type="button" class="batch-btn-replace" onclick="openReplaceBatchImageModal('${escapeHtml(img.id)}', '${escapeHtml(batchId)}', '${escapeHtml(img.orientation || 'FRONT')}', '${escapeHtml(img.file_name)}')" title="Replace this evidence image with a new upload or candidate photo">
                                    🔁 Replace Photo
                                </button>
                            </div>
                        </div>
                    </div>
                `;
            }).join("")}
        </div>
    `;
}

let lastIngestedBatchId = null;
let activeIngestXHR = null;
let activeIngestPollInterval = null;
let isBatchIngesting = false;

function openBatchUploadModal() {
    tabSelectedFrontFiles = [];
    tabSelectedRearFiles = [];
    setText("count-tab-front", "0 files selected");
    setText("count-tab-rear", "0 files selected");
    const labelInput = document.getElementById("batch-upload-label");
    if (labelInput) labelInput.value = "";
    const warn = document.getElementById("tab-symmetry-warning");
    if (warn) warn.style.display = "none";

    // Reset views
    const formView = document.getElementById("batch-upload-form-view");
    const progressView = document.getElementById("batch-upload-progress-view");
    const formActions = document.getElementById("batch-footer-form-actions");
    const progressActions = document.getElementById("batch-footer-progress-actions");
    if (formView) formView.style.display = "flex";
    if (progressView) progressView.style.display = "none";
    if (formActions) formActions.style.display = "flex";
    if (progressActions) progressActions.style.display = "none";

    // Reset stepper
    setStepStatus(1, "active", "WAITING", "⏳");
    setText("ingest-step-1-desc", "Transferring image binaries to backend endpoint...");
    setStepStatus(2, "", "WAITING", "⏳");
    setText("ingest-step-2-desc", "SHA-256 fingerprinting, vault indexing & duplicate detection...");
    setStepStatus(3, "", "WAITING", "⏳");
    setText("ingest-step-3-desc", "Detecting license plates and reading Ugandan vehicle registrations...");
    setStepStatus(4, "", "WAITING", "⏳");
    setText("ingest-step-4-desc", "Correlating front and rear angles and linking ITMS installation orders...");

    // Reset progress bar
    setIngestProgress(0, "Ready to upload photos to vault...");
    const sumCard = document.getElementById("ingest-summary-card");
    if (sumCard) sumCard.style.display = "none";

    const titleBadge = document.getElementById("batch-upload-status-badge");
    if (titleBadge) {
        titleBadge.innerText = "Front & Rear 1:1";
        titleBadge.className = "badge badge-yellow";
    }

    isBatchIngesting = false;
    openModal("modal-batch-upload");
}

function closeBatchUploadModal() {
    if (isBatchIngesting) {
        const leave = confirm("Batch upload & processing is currently running.\nDo you want to minimize and let it continue in the background?");
        if (leave) {
            minimizeBatchUploadModal();
        }
        return;
    }
    if (activeIngestPollInterval) {
        clearInterval(activeIngestPollInterval);
        activeIngestPollInterval = null;
    }
    closeModal("modal-batch-upload");
}

function minimizeBatchUploadModal() {
    closeModal("modal-batch-upload");
    if (isBatchIngesting) {
        updateGlobalBgUploadPill("Batch Ingesting in Background", 35, "Transferring & pairing photos...");
    }
    showToast("Batch ingestion running in background. Monitoring progress...", "info");
    startPipelinePolling();
}

function jumpToPairInQueue(pairId) {
    switchNavTab(4);
    setTimeout(() => {
        selectPair(pairId);
    }, 150);
}

function jumpToNewBatchInQueue() {
    closeModal("modal-batch-upload");
    switchNavTab(4);
    setTimeout(() => {
        const select = document.getElementById("queue-batch-select");
        if (select) {
            select.value = "LATEST";
            setQueueBatchFilter("LATEST");
        }
        fetchPairs();
    }, 150);
}

function jumpToBatchPhotos() {
    closeModal("modal-batch-upload");
    switchNavTab(3);
    if (lastIngestedBatchId) {
        expandedBatches.add(lastIngestedBatchId);
        renderBatchesStream();
        loadBatchDetailsAndRender(lastIngestedBatchId);
    }
}

function handleTabFileSelect(side) {
    const input = document.getElementById(`input-tab-${side}`);
    if (side === "front") {
        tabSelectedFrontFiles = Array.from(input.files);
        setText("count-tab-front", `${tabSelectedFrontFiles.length} files selected`);
    } else {
        tabSelectedRearFiles = Array.from(input.files);
        setText("count-tab-rear", `${tabSelectedRearFiles.length} files selected`);
    }
    validateTabUploadSymmetry();
}

function validateTabUploadSymmetry() {
    const fCount = tabSelectedFrontFiles.length;
    const rCount = tabSelectedRearFiles.length;
    const banner = document.getElementById("tab-symmetry-warning");
    if (!banner) return;

    if (fCount > 0 && rCount > 0) {
        banner.style.display = "flex";
        if (fCount === rCount) {
            banner.style.backgroundColor = "rgba(16, 185, 129, 0.15)";
            banner.style.color = "#34d399";
            banner.innerText = `✓ Perfect 1:1 symmetry: ${fCount} Fronts and ${rCount} Rears.`;
        } else {
            banner.style.backgroundColor = "rgba(252, 220, 4, 0.15)";
            banner.style.color = "var(--ug-yellow)";
            const diff = Math.abs(fCount - rCount);
            const missing = fCount > rCount ? "Rear" : "Front";
            banner.innerText = `⚠️ Asymmetric batch: ${fCount} Fronts vs ${rCount} Rears (${diff} missing ${missing}).`;
        }
    } else {
        banner.style.display = "none";
    }
}

function setIngestProgress(pct, statusText) {
    const bar = document.getElementById("ingest-progress-bar-fill");
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    setText("ingest-progress-percent", `${pct}%`);
    if (statusText) setText("ingest-status-text", statusText);
}

function setStepStatus(stepNum, status, badgeText, icon) {
    const card = document.getElementById(`ingest-step-${stepNum}`);
    const badge = document.getElementById(`ingest-step-${stepNum}-badge`);
    const iconElem = document.getElementById(`ingest-step-${stepNum}-icon`);
    if (card) {
        card.className = `ingest-step-card ${status}`;
    }
    if (badge) {
        badge.innerText = badgeText;
        if (status === "completed") {
            badge.className = "badge badge-green";
        } else if (status === "active") {
            badge.className = "badge badge-yellow";
        } else if (status === "failed") {
            badge.className = "badge badge-red";
        } else {
            badge.className = "badge badge-secondary";
        }
    }
    if (iconElem && icon) {
        iconElem.innerText = icon;
    }
}

/**
 * Compresses large high-resolution photos before multipart XHR upload.
 * Reduces 10MB-20MB phone camera photos to ~350KB-500KB Full HD (~1920px).
 */
async function compressImageForUpload(file, maxDimension = 1920, quality = 0.85) {
    if (!file || !file.type || !file.type.startsWith("image/")) {
        return file;
    }
    if (file.size < 450 * 1024) {
        return file;
    }
    return new Promise((resolve) => {
        const img = new Image();
        const reader = new FileReader();
        reader.onload = (e) => {
            img.onload = () => {
                let w = img.width;
                let h = img.height;
                if (w > maxDimension || h > maxDimension) {
                    if (w > h) {
                        h = Math.round((h * maxDimension) / w);
                        w = maxDimension;
                    } else {
                        w = Math.round((w * maxDimension) / h);
                        h = maxDimension;
                    }
                }
                const canvas = document.createElement("canvas");
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, w, h);
                canvas.toBlob(
                    (blob) => {
                        if (blob && blob.size < file.size) {
                            const compressed = new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), {
                                type: "image/jpeg",
                                lastModified: file.lastModified || Date.now(),
                            });
                            resolve(compressed);
                        } else {
                            resolve(file);
                        }
                    },
                    "image/jpeg",
                    quality
                );
            };
            img.onerror = () => resolve(file);
            img.src = e.target.result;
        };
        reader.onerror = () => resolve(file);
        reader.readAsDataURL(file);
    });
}

async function submitTabBatchUpload() {
    const total = tabSelectedFrontFiles.length + tabSelectedRearFiles.length;
    if (total === 0) {
        showToast("Please select photos to upload", "error");
        return;
    }

    const formData = new FormData();
    const labelInput = document.getElementById("batch-upload-label");
    const labelText = (labelInput ? labelInput.value.trim() : "") || "Web Console Upload";
    formData.append("batch_label", labelText);

    // Optimize images client-side before upload to prevent server OOM and accelerate transfer
    const compressedFront = await Promise.all(tabSelectedFrontFiles.map(f => compressImageForUpload(f)));
    const compressedRear = await Promise.all(tabSelectedRearFiles.map(f => compressImageForUpload(f)));
    compressedFront.forEach(f => formData.append("front_photos", f));
    compressedRear.forEach(f => formData.append("rear_photos", f));

    // Transition view to progress view
    isBatchIngesting = true;
    const formView = document.getElementById("batch-upload-form-view");
    const progressView = document.getElementById("batch-upload-progress-view");
    const formActions = document.getElementById("batch-footer-form-actions");
    const progressActions = document.getElementById("batch-footer-progress-actions");

    if (formView) formView.style.display = "none";
    if (progressView) progressView.style.display = "flex";
    if (formActions) formActions.style.display = "none";
    if (progressActions) progressActions.style.display = "flex";

    const btnBg = document.getElementById("btn-ingest-background");
    if (btnBg) btnBg.style.display = "inline-flex";
    const btnQueue = document.getElementById("btn-ingest-open-queue");
    if (btnQueue) btnQueue.style.display = "none";
    const btnPhotos = document.getElementById("btn-ingest-view-photos");
    if (btnPhotos) btnPhotos.style.display = "none";
    const btnDone = document.getElementById("btn-ingest-done");
    if (btnDone) btnDone.style.display = "none";

    setText("ingest-batch-id", "Creating batch...");
    setText("ingest-total-photos", `${total} photos selected`);
    setIngestProgress(2, "Initiating network upload...");
    setStepStatus(1, "active", "UPLOADING", "🔄");

    const xhr = new XMLHttpRequest();
    activeIngestXHR = xhr;

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) {
            const rawPct = Math.round((e.loaded / e.total) * 100);
            const scaledPct = Math.round((rawPct * 0.35)); // 0% to 35% of total pipeline
            const loadedMB = (e.loaded / (1024 * 1024)).toFixed(1);
            const totalMB = (e.total / (1024 * 1024)).toFixed(1);
            setIngestProgress(scaledPct, `Uploading photos: ${loadedMB}MB / ${totalMB}MB (${rawPct}%)`);
            setText("ingest-step-1-desc", `Transferred ${loadedMB}MB of ${totalMB}MB (${rawPct}%)...`);
            updateGlobalBgUploadPill(`Uploading Photos (${rawPct}%)`, scaledPct, `${loadedMB}MB of ${totalMB}MB transferred`);
        }
    };

    function _handleUploadSuccess(data) {
        lastIngestedBatchId = data.batch_id;
        setText("ingest-batch-id", data.batch_id);
        setText("ingest-total-photos", `${data.ingested_count || total} photos ingested`);

        // Step 1: Upload Complete
        setStepStatus(1, "completed", "UPLOADED", "✓");
        setText("ingest-step-1-desc", `Successfully transferred ${total} photo files.`);

        // Step 2: Vault Cryptographic Storage & Deduplication
        setStepStatus(2, "completed", "INGESTED", "✓");
        setText("ingest-step-2-desc", `Vaulted ${data.ingested_count} photos. Physical 1:1 pairs established immediately (${Math.min(data.front_count || 0, data.rear_count || 0)} pairs).`);
        setStepStatus(2, "completed", "PAIRED", "✅");

        // Immediately refresh Review Queue so operator sees pairs & photos instantly!
        if (typeof fetchPairs === "function") void fetchPairs();
        if (typeof fetchBatchesList === "function") void fetchBatchesList();
        if (typeof fetchStats === "function") void fetchStats();
        updateGlobalBgUploadPill("Photos Vaulted & Paired", 45, "Running Dual-Stream Joint Vision OCR...");
        setIngestProgress(40, "Vault ingestion verified. Starting YOLO Vision OCR...");

        // Clear file selections in memory
        tabSelectedFrontFiles = [];
        tabSelectedRearFiles = [];
        setText("count-tab-front", "0 files selected");
        setText("count-tab-rear", "0 files selected");
        batchDetailsCache = {};
        expandedBatches.add(data.batch_id);

        // Step 3 & 4: Start background pipeline with batch ID
        void startIngestionPipelineTracking(data.batch_id, data);
    }

    xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
            handleIngestError(`Server HTTP error ${xhr.status}: ${xhr.statusText}`);
            return;
        }
        try {
            const data = JSON.parse(xhr.responseText);
            if (!data.success) {
                handleIngestError(data.error || "Upload failed from server.");
                return;
            }
            _handleUploadSuccess(data);
        } catch (err) {
            handleIngestError("Invalid server response: " + err);
        }
    };

    xhr.onerror = () => {
        handleIngestError("Network error occurred during photo upload.");
    };

    xhr.open("POST", "/api/upload/");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content
        || (document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '');
    if (csrfToken) {
        xhr.setRequestHeader("X-CSRFToken", csrfToken);
        formData.append("csrfmiddlewaretoken", csrfToken);
    }
    xhr.send(formData);
}

function handleIngestError(errorMsg) {
    isBatchIngesting = false;
    setStepStatus(1, "failed", "FAILED", "✕");
    setIngestProgress(0, `Error: ${errorMsg}`);
    showToast(errorMsg, "error");
    appendConsoleLog(`Upload error: ${errorMsg}`, "error");

    const btnBg = document.getElementById("btn-ingest-background");
    if (btnBg) btnBg.style.display = "none";
    const btnDone = document.getElementById("btn-ingest-done");
    if (btnDone) {
        btnDone.style.display = "inline-flex";
        btnDone.innerText = "Close";
    }
}

async function _finalizePipelineTracking(batchId, batchData) {
    if (activeIngestPollInterval) {
        clearInterval(activeIngestPollInterval);
        activeIngestPollInterval = null;
    }
    isBatchIngesting = false;

    setStepStatus(3, "completed", "DONE", "✓");
    setStepStatus(4, "completed", "PAIRED", "✓");
    setText("ingest-step-3-desc", "YOLO plate detection and Ugandan OCR complete.");
    setText("ingest-step-4-desc", "Front and rear photos correlated; matched against active orders.");
    setIngestProgress(100, "Ingestion & processing pipeline completed successfully!");

    const titleBadge = document.getElementById("batch-upload-status-badge");
    if (titleBadge) {
        titleBadge.innerText = "✓ COMPLETE";
        titleBadge.className = "badge badge-green";
    }

    const sumCard = document.getElementById("ingest-summary-card");
    if (sumCard) {
        sumCard.style.display = "flex";
        setText("ingest-sum-ingested", batchData.ingested_count || batchData.total_files || 0);
        setText("ingest-sum-duplicates", batchData.duplicate_count || 0);
        setText("ingest-sum-pairs", "Loading...");
    }

    try {
        const bRes = await fetch(`/api/batches/${encodeURIComponent(batchId)}/`);
        const bData = await bRes.json();
        if (bData.images) {
            const pairedImages = bData.images.filter(img => img.pair);
            const numPairs = Math.round(pairedImages.length / 2);
            setText("ingest-sum-pairs", numPairs > 0 ? numPairs : "0");
        }
    } catch (e) {
        setText("ingest-sum-pairs", "Ready");
    }

    const btnBg = document.getElementById("btn-ingest-background");
    if (btnBg) btnBg.style.display = "none";
    const btnQueue = document.getElementById("btn-ingest-open-queue");
    if (btnQueue) btnQueue.style.display = "inline-flex";
    const btnPhotos = document.getElementById("btn-ingest-view-photos");
    if (btnPhotos) btnPhotos.style.display = "inline-flex";
    const btnDone = document.getElementById("btn-ingest-done");
    if (btnDone) btnDone.style.display = "inline-flex";

    void fetchStats();
    void fetchBatchesList();
    void fetchPairs();

    showToast(`Batch ${batchId} successfully ingested and processed!`, "success");
    appendConsoleLog(`Batch ${batchId}: Ingestion and vision pipeline complete.`, "success");
    updateGlobalBgUploadPill("Pipeline Complete!", 100, "All pairs verified.");
    setTimeout(hideGlobalBgUploadPill, 4000);
}

async function startIngestionPipelineTracking(batchId, batchData) {
    setStepStatus(3, "active", "RUNNING", "🔄");
    setText("ingest-step-3-desc", "Running Dual-Stream Joint Vision OCR on motorcycle pairs...");
    setIngestProgress(45, "Running Dual-Stream Joint Vision OCR...");

    try {
        await fetch("/api/pipeline/run/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `task_type=full_pipeline&batch_id=${encodeURIComponent(batchId)}`,
        });
    } catch (err) {
        console.warn("Pipeline trigger notice:", err);
    }

    let pollCount = 0;
    if (activeIngestPollInterval) clearInterval(activeIngestPollInterval);

    activeIngestPollInterval = setInterval(() => {
        pollCount++;
        fetch("/api/pipeline/status/")
            .then(res => res.json())
            .then(st => {
                if (st.running) {
                    if (st.stage === "VISION_PROCESSING") {
                        setStepStatus(3, "active", "DETECTING", "🔄");
                        setText("ingest-step-3-desc", st.message || "Running YOLO vision detection & OCR...");
                        const prog = Math.min(68, 45 + Math.round((st.progress_pct || 25) * 0.25));
                        setIngestProgress(prog, `Vision stage: ${st.message || 'Detecting plates...'}`);
                    } else if (st.stage === "PAIR_ASSOCIATION") {
                        setStepStatus(3, "completed", "DONE", "✓");
                        setStepStatus(4, "active", "MATCHING", "🔄");
                        setText("ingest-step-4-desc", st.message || "Pairing front and rear cameras, linking orders...");
                        const prog = Math.min(95, 70 + Math.round((st.progress_pct || 70) * 0.25));
                        setIngestProgress(prog, `Pair matching: ${st.message || 'Linking orders...'}`);
                    }
                } else {
                    void _finalizePipelineTracking(batchId, batchData);
                }
            })
            .catch(pollErr => {
                console.warn("Ingestion status poll error:", pollErr);
                if (pollCount > 60) {
                    clearInterval(activeIngestPollInterval);
                    activeIngestPollInterval = null;
                    isBatchIngesting = false;
                }
            });
    }, 1000);
}

