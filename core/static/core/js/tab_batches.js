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
                <h4 style="color:#fff; font-size:1rem; margin-top:8px;">No batches matching '${escapeHtml(batchSearchQuery)}'</h4>
                <p style="font-size:0.75rem; color:var(--ug-text-muted);">Try a different batch ID or shift label.</p>
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
            ? `<span class="badge badge-green" style="font-size:0.65rem;" title="Latest active batch">🔥 TODAY (Active)</span>`
            : (b.is_today
                ? `<span class="badge badge-green" style="font-size:0.65rem;" title="Today's shift batch">● TODAY</span>`
                : `<span class="badge badge-yellow" style="font-size:0.65rem;" title="Prior shift batch">⏳ PRIOR (${escapeHtml(b.created_date || '')})</span>`);

        return `
            <div class="batch-stream-card ${isExp ? 'is-expanded' : ''}" id="batch-stream-${escapeHtml(b.batch_id)}">
                <div class="batch-stream-header" onclick="toggleBatchAccordion('${escapeHtml(b.batch_id)}')">
                    <div class="batch-header-left">
                        <span class="batch-chevron" id="batch-chevron-${escapeHtml(b.batch_id)}">${isExp ? '▼' : '▶'}</span>
                        <span class="batch-id-text">${escapeHtml(b.batch_id)}</span>
                        ${shiftBadge}
                        <span class="badge badge-yellow">${escapeHtml(b.source_type || 'WEB')}</span>
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
                        <div style="padding:20px; text-align:center; color:var(--ug-text-dim);">
                            Loading photos for ${escapeHtml(b.batch_id)}...
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
            container.innerHTML = `<div style="padding:20px; text-align:center; color:var(--ug-text-dim);">No photos found in batch.</div>`;
        }
    } catch (err) {
        console.warn(`Failed loading batch ${batchId}:`, err);
        container.innerHTML = `<div style="padding:20px; text-align:center; color:var(--ug-red);">Failed to load photos.</div>`;
    }
}

function renderFullScaleBatchPhotos(batchId, images) {
    const container = document.getElementById(`batch-photos-${batchId}`);
    if (!container) return;

    if (!images || images.length === 0) {
        container.innerHTML = `<div style="padding:24px; text-align:center; color:var(--ug-text-dim);">No photos recorded in this batch.</div>`;
        return;
    }

    container.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.07);">
            <span style="font-size:0.78rem; font-weight:700; color:var(--ug-text-muted); text-transform:uppercase;">
                📷 Evidence Photos (${images.length} images)
            </span>
            <span style="font-size:0.72rem; color:var(--ug-yellow); display:inline-flex; align-items:center; gap:4px;">
                ↕ Scroll down to inspect all photos
            </span>
        </div>
        <div class="batch-full-photos-grid">
            ${images.map(img => {
                const orientTag = img.orientation === "FRONT" ? "tag-front" : (img.orientation === "REAR" ? "tag-rear" : "tag-unknown");
                const plateHtml = img.detected_plate ? `
                    <div class="batch-photo-plate-overlay">
                        <span>🔍 ${escapeHtml(img.detected_plate)}</span>
                        ${img.ocr_confidence !== null ? `<span style="font-size:0.75rem; color:#fff; font-weight:normal;">(${img.ocr_confidence}%)</span>` : ''}
                    </div>
                ` : `
                    <div class="batch-photo-plate-overlay" style="border-color:rgba(255,255,255,0.2); color:var(--ug-text-dim);">
                        <span>No OCR Plate</span>
                    </div>
                `;

                const pairHtml = img.pair ? `
                    <div class="batch-photo-pair-strip">
                        <div>
                            <div style="color:var(--ug-green); font-weight:700; font-size:0.8rem;">🔗 Pair #${img.pair.pair_id} (${escapeHtml(img.pair.role)})</div>
                            <div style="color:#fff; font-weight:700; font-size:0.78rem;">Detected: ${escapeHtml(img.pair.detected_plate || '—')}</div>
                            <div style="color:var(--ug-text-dim); font-size:0.72rem;">Order: ${img.pair.order_number ? '#' + escapeHtml(img.pair.order_number) : 'Unlinked'}</div>
                        </div>
                        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="jumpToPairInQueue(${img.pair.pair_id})">
                            🔍 Inspect in Queue
                        </button>
                    </div>
                ` : `
                    <div class="batch-photo-pair-strip" style="border-color:rgba(245, 158, 11, 0.3);">
                        <div>
                            <div style="color:var(--ug-yellow); font-weight:700; font-size:0.78rem;">⚠️ Unassigned Evidence Photo</div>
                            <div style="color:var(--ug-text-dim); font-size:0.7rem;">Click 'Match Pairs [M]' on Dashboard to associate.</div>
                        </div>
                    </div>
                `;

                return `
                    <div class="batch-photo-card-large">
                        <div class="batch-photo-full-wrap" onclick="openLightbox('${escapeHtml(img.url)}')">
                            <img class="batch-photo-full-img" src="${escapeHtml(img.url)}" alt="${escapeHtml(img.file_name)}" onerror="this.src='/static/core/placeholder.svg';">
                            <span class="batch-photo-orient-badge ${orientTag}">${img.orientation}</span>
                            ${plateHtml}
                            <span class="batch-photo-zoom-hint">🔍 Click to zoom</span>
                        </div>
                        <div class="batch-photo-details-strip">
                            <div class="batch-photo-meta-row">
                                <span style="font-family:var(--font-mono); font-size:0.75rem; color:#fff; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:260px;" title="${escapeHtml(img.file_name)}">
                                    📁 ${escapeHtml(img.file_name)}
                                </span>
                                <span>${img.file_size_kb} KB • ${escapeHtml(img.captured_at || 'No timestamp')}</span>
                            </div>
                            ${pairHtml}
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
    tabSelectedFrontFiles.forEach(f => formData.append("front_photos", f));
    tabSelectedRearFiles.forEach(f => formData.append("rear_photos", f));

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
        }
    };

    xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 300) {
            try {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    lastIngestedBatchId = data.batch_id;
                    setText("ingest-batch-id", data.batch_id);
                    setText("ingest-total-photos", `${data.ingested_count || total} photos ingested`);

                    // Step 1: Upload Complete
                    setStepStatus(1, "completed", "UPLOADED", "✓");
                    setText("ingest-step-1-desc", `Successfully transferred ${total} photo files.`);

                    // Step 2: Vault Cryptographic Storage & Deduplication
                    setStepStatus(2, "completed", "INGESTED", "✓");
                    setText("ingest-step-2-desc", `SHA-256 hashed: ${data.ingested_count} vaulted, ${data.duplicate_count || 0} duplicates skipped.`);
                    setIngestProgress(40, "Vault ingestion verified. Starting YOLO Vision OCR...");

                    // Clear file selections in memory
                    tabSelectedFrontFiles = [];
                    tabSelectedRearFiles = [];
                    setText("count-tab-front", "0 files selected");
                    setText("count-tab-rear", "0 files selected");
                    batchDetailsCache = {};
                    expandedBatches.add(data.batch_id);

                    // Step 3 & 4: Start background pipeline with batch ID
                    startIngestionPipelineTracking(data.batch_id, data);
                } else {
                    handleIngestError(data.error || "Upload failed from server.");
                }
            } catch (err) {
                handleIngestError("Invalid server response: " + err);
            }
        } else {
            handleIngestError(`Server HTTP error ${xhr.status}: ${xhr.statusText}`);
        }
    };

    xhr.onerror = () => {
        handleIngestError("Network error occurred during photo upload.");
    };

    xhr.open("POST", "/api/upload/");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
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

async function startIngestionPipelineTracking(batchId, batchData) {
    // Step 3: YOLO Vision Active
    setStepStatus(3, "active", "RUNNING", "🔄");
    setText("ingest-step-3-desc", "Executing YOLO plate detector and Ugandan syntax OCR...");
    setIngestProgress(45, "Running YOLO Vision detection & OCR...");

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

    activeIngestPollInterval = setInterval(async () => {
        pollCount++;
        try {
            const res = await fetch("/api/pipeline/status/");
            const st = await res.json();

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
                // Pipeline complete
                clearInterval(activeIngestPollInterval);
                activeIngestPollInterval = null;
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

                // Show summary card
                const sumCard = document.getElementById("ingest-summary-card");
                if (sumCard) {
                    sumCard.style.display = "flex";
                    setText("ingest-sum-ingested", batchData.ingested_count || batchData.total_files || 0);
                    setText("ingest-sum-duplicates", batchData.duplicate_count || 0);
                    setText("ingest-sum-pairs", "Loading...");
                }

                // Query batch details to show pairs formed count
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

                // Update footer buttons
                const btnBg = document.getElementById("btn-ingest-background");
                if (btnBg) btnBg.style.display = "none";
                const btnQueue = document.getElementById("btn-ingest-open-queue");
                if (btnQueue) btnQueue.style.display = "inline-flex";
                const btnPhotos = document.getElementById("btn-ingest-view-photos");
                if (btnPhotos) btnPhotos.style.display = "inline-flex";
                const btnDone = document.getElementById("btn-ingest-done");
                if (btnDone) btnDone.style.display = "inline-flex";

                fetchStats();
                fetchBatchesList();
                fetchPairs();

                showToast(`Batch ${batchId} successfully ingested and processed!`, "success");
                appendConsoleLog(`Batch ${batchId}: Ingestion and vision pipeline complete.`, "success");
            }
        } catch (pollErr) {
            console.warn("Ingestion status poll error:", pollErr);
            if (pollCount > 60) {
                // Timeout safety after 60s
                clearInterval(activeIngestPollInterval);
                activeIngestPollInterval = null;
                isBatchIngesting = false;
            }
        }
    }, 1000);
}

