/**
 * tab_stock.js
 * Frontend logic for the Stock & Bond Logistics Tab (Tab 8)
 * Mirrors the Textual TUI workflow, ergonomics, and live Kit Inspector.
 */

let currentStockMode = 'dispatch';
let currentStockDateSuffix = '';
let stagedMovements = [];
let lastReconData = null;
let cachedShiftDates = null;
let isSyncingStock = false;

// -----------------------------------------------------------------------------
// DOM Ready & Event Listeners
// -----------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Initial fetch for today
    refreshStockLedger();

    // Universal scan input listener
    const scanInput = document.getElementById('stock-universal-scan');
    if (scanInput) {
        scanInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const val = scanInput.value.trim();
                scanInput.value = '';
                if (val) {
                    handleUniversalScan(val);
                }
            }
        });
    }

    // Document-level keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        const stockPane = document.getElementById('pane-stock');
        if (!stockPane || !stockPane.classList.contains('active')) return;

        const activeTag = document.activeElement ? document.activeElement.tagName : '';
        const isEditing = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT';

        // '/' shortcut always focuses the universal scan input
        if (e.key === '/') {
            if (!isEditing || document.activeElement !== scanInput) {
                e.preventDefault();
                if (scanInput) {
                    scanInput.focus();
                    scanInput.select();
                }
            }
            return;
        }

        // Escape blurs input or closes modals
        if (e.key === 'Escape') {
            if (document.getElementById('stock-paste-modal')?.classList.contains('active')) {
                closeStockPasteModal();
                return;
            }
            if (document.getElementById('stock-target-modal')?.classList.contains('active')) {
                closeLedgerTargetModal();
                return;
            }
            if (isEditing) {
                document.activeElement.blur();
            }
            return;
        }

        // Hotkeys when not inside an input
        if (!isEditing) {
            if (e.key === '1') {
                e.preventDefault();
                setStockMode('dispatch');
            } else if (e.key === '2') {
                e.preventDefault();
                setStockMode('movements');
            } else if (e.key === '3') {
                e.preventDefault();
                setStockMode('audit');
            } else if (e.key === '4') {
                e.preventDefault();
                setStockMode('ledger');
            } else if (e.key === 'f' || e.key === 'F') {
                e.preventDefault();
                cycleStockMode();
            } else if (e.key === 'v' || e.key === 'V') {
                e.preventDefault();
                openStockPasteModal();
            } else if (e.key === 's' || e.key === 'S') {
                e.preventDefault();
                syncStock();
            } else if (e.key === 'e' || e.key === 'E') {
                e.preventDefault();
                exportStockCSV();
            } else if (e.key === 'o' || e.key === 'O') {
                e.preventDefault();
                openLedgerTargetModal();
            } else if (e.key === 'r' || e.key === 'R') {
                e.preventDefault();
                refreshStockLedger();
            }
        }
    });
});

// -----------------------------------------------------------------------------
// Toast / Notification Helper
// -----------------------------------------------------------------------------

function notify(message, type = 'info') {
    if (typeof showToast === 'function') {
        showToast(message, type);
    } else {
        console.log(`[${type.toUpperCase()}] ${message}`);
    }
}

// -----------------------------------------------------------------------------
// Mode Navigation & Switching
// -----------------------------------------------------------------------------

const STOCK_MODES = ['dispatch', 'movements', 'audit', 'ledger'];

function cycleStockMode() {
    const idx = STOCK_MODES.indexOf(currentStockMode);
    const nextIdx = (idx + 1) % STOCK_MODES.length;
    setStockMode(STOCK_MODES[nextIdx]);
}

function setStockMode(mode) {
    if (!STOCK_MODES.includes(mode)) mode = 'dispatch';
    currentStockMode = mode;

    // Update Nav Buttons
    STOCK_MODES.forEach(m => {
        const btn = document.getElementById(`btn-mode-${m}`);
        if (btn) {
            if (m === mode) {
                btn.classList.add('active', 'btn-primary');
                btn.classList.remove('btn-secondary');
            } else {
                btn.classList.remove('active', 'btn-primary');
                btn.classList.add('btn-secondary');
            }
        }
    });

    // Update Mode Containers
    STOCK_MODES.forEach(m => {
        const container = document.getElementById(`mode-${m}`);
        if (container) {
            if (m === mode) {
                container.style.display = 'flex';
                container.classList.add('active');
            } else {
                container.style.display = 'none';
                container.classList.remove('active');
            }
        }
    });

    // Update Feedback Strip
    const feedback = document.getElementById('stock-feedback-alert');
    if (feedback) {
        if (mode === 'dispatch') {
            feedback.innerHTML = '⚡ READY FOR SCANNING: Scan plate or click Paste to load from Excel.';
            feedback.className = 'alert alert-success';
        } else if (mode === 'movements') {
            feedback.innerHTML = '🔄 MOVEMENTS MODE: Scan or paste plates to stage, then select Type & Commit.';
            feedback.className = 'alert alert-info';
        } else if (mode === 'audit') {
            feedback.innerHTML = '🔒 SAFE AUDIT MODE: Scan safe room barcodes to audit against book closing stock.';
            feedback.className = 'alert alert-warning';
        } else if (mode === 'ledger') {
            feedback.innerHTML = '📊 LEDGER MODE: View daily balance sheet reconciliation and targets.';
            feedback.className = 'alert alert-primary';
        }
    }

    const pasteLabel = document.getElementById('paste-mode-label');
    if (pasteLabel) {
        pasteLabel.innerText = mode.toUpperCase();
    }
}

// -----------------------------------------------------------------------------
// Universal Barcode / Plate Scanner
// -----------------------------------------------------------------------------

async function handleUniversalScan(rawVal) {
    const val = rawVal.trim().toUpperCase();
    if (!val) return;

    if (currentStockMode === 'dispatch') {
        await submitDispatch([val]);
    } else if (currentStockMode === 'movements') {
        stageMovementsPlates([val]);
        inspectPlate(val);
    } else if (currentStockMode === 'audit') {
        await submitAudit([val]);
    } else {
        // Ledger mode - inspect plate
        inspectPlate(val);
    }
}

// -----------------------------------------------------------------------------
// Persistent Live Kit Inspector (35% Column & Mobile Overlay)
// -----------------------------------------------------------------------------

function openStockMobileInspector() {
    const card = document.getElementById('stock-inspector-card');
    if (card) {
        card.classList.add('mobile-show');
        card.scrollTop = 0;
    }
}

function closeStockMobileInspector() {
    const card = document.getElementById('stock-inspector-card');
    if (card) {
        card.classList.remove('mobile-show');
    }
}

async function inspectPlate(plate, fallbackError = null) {
    if (!plate) return;
    const cleanPlate = plate.trim().toUpperCase();

    // Auto-reveal on mobile devices
    openStockMobileInspector();

    // Show hero title and placeholder loading state
    const heroEl = document.getElementById('ins-plate');
    if (heroEl) heroEl.innerText = cleanPlate;

    const descEl = document.getElementById('ins-desc');
    if (descEl) descEl.innerText = 'Inspecting telematic hardware profile...';

    const statusBadge = document.getElementById('ins-status');
    const catBadge = document.getElementById('ins-cat-badge');
    const errorBox = document.getElementById('ins-error-box');
    const content = document.getElementById('inspector-content');
    const card = document.getElementById('stock-inspector-card');

    try {
        const url = `/api/stock/inspect/?plate=${encodeURIComponent(cleanPlate)}&date_suffix=${currentStockDateSuffix}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            if (heroEl) heroEl.innerText = data.plate || cleanPlate;

            // Category badge
            if (catBadge) {
                catBadge.style.display = 'inline-block';
                catBadge.innerText = data.category || 'PSV';
                catBadge.className = `badge ${data.category === 'PMO' ? 'badge-yellow' : 'badge-muted'}`;
            }

            // Stock Status badge
            if (statusBadge) {
                statusBadge.style.display = 'inline-block';
                statusBadge.innerText = data.stock_status.badge;
                statusBadge.className = `badge ${data.stock_status.badge_class || 'badge-green'}`;
            }

            // Status description
            if (descEl) descEl.innerText = data.stock_status.desc;

            // Blocked alert warning & flash animation
            if (errorBox) {
                if (data.is_blocked) {
                    errorBox.style.display = 'block';
                    errorBox.innerText = `⛔ NOT ON STOCK (BLOCKED): Set physical box aside and swap with kits on stock! (${data.stock_status.desc})`;
                    if (card) {
                        card.classList.remove('inspector-flash-error', 'inspector-flash-success');
                        void card.offsetWidth;
                        card.classList.add('inspector-flash-error');
                    }
                } else {
                    errorBox.style.display = 'none';
                    if (card) {
                        card.classList.remove('inspector-flash-error', 'inspector-flash-success');
                        void card.offsetWidth;
                        card.classList.add('inspector-flash-success');
                    }
                }
            }

            // Hardware Profile
            const kit = data.kit_profile || {};
            setTextSafe('ins-kit-code', kit.kit_code || `IK-${cleanPlate}`);
            setTextSafe('ins-warehouse', kit.warehouse || 'AGM Bonded Warehouse');
            const gpsText = (kit.gps_tracker && kit.gps_tracker !== '—')
                ? kit.gps_tracker
                : (kit.has_telematics ? '—' : '⚡ Syncing from ITMS in background...');
            setTextSafe('ins-gps', gpsText);
            setTextSafe('ins-front', kit.front_ble || '—');
            setTextSafe('ins-rear', kit.rear_ble || '—');
            setTextSafe('ins-fplate', kit.front_plate || '—');
            setTextSafe('ins-rplate', kit.rear_plate || '—');

            // ITMS Order Linkage
            const order = data.order_linkage || {};
            const orderBox = document.getElementById('ins-order-box');
            const unallocBox = document.getElementById('ins-unallocated-box');

            if (order.has_order) {
                if (orderBox) orderBox.style.display = 'flex';
                if (unallocBox) unallocBox.style.display = 'none';

                setTextSafe('ins-order-num', `#${order.order_number}`);
                setTextSafe('ins-order-status', order.order_status || 'Active');
                setTextSafe('ins-order-owner', order.owner_name || '—');
                setTextSafe('ins-order-bike', order.motorcycle_model || '—');
                setTextSafe('ins-order-chassis', order.chassis_number || '—');
            } else {
                if (orderBox) orderBox.style.display = 'none';
                if (unallocBox) unallocBox.style.display = 'block';
            }

            // Line Dispatch Record
            const disp = data.dispatch_record;
            const dispSection = document.getElementById('ins-dispatch-section');
            if (disp && disp.is_dispatched) {
                if (dispSection) dispSection.style.display = 'block';
                setTextSafe('ins-disp-time', disp.dispatched_at || '—');
                setTextSafe('ins-disp-cat', disp.category || '—');
                setTextSafe('ins-disp-suffix', disp.suffix || '—');
                setTextSafe('ins-disp-status', disp.status || '—');
                setTextSafe('ins-disp-operator', disp.operator || '—');
            } else {
                if (dispSection) dispSection.style.display = 'none';
            }

            if (content) content.style.display = 'flex';
            return;
        }
    } catch (e) {
        console.error('inspectPlate error:', e);
    }

    // Fallback if inspection fails
    if (statusBadge) {
        statusBadge.style.display = 'inline-block';
        statusBadge.className = 'badge badge-red';
        statusBadge.innerText = '⛔ REJECTED';
    }
    if (descEl) descEl.innerText = fallbackError || 'Plate not found or verification error.';
    if (errorBox) {
        errorBox.style.display = 'block';
        errorBox.innerText = fallbackError || 'Could not verify kit with ITMS.';
    }
    if (content) content.style.display = 'flex';
}

function setTextSafe(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

// -----------------------------------------------------------------------------
// Mode 1: Dispatch Operations
// -----------------------------------------------------------------------------

async function submitDispatch(platesList) {
    const category = document.getElementById('stock-category-select').value;
    const payload = {
        plates: platesList.join('\n'),
        date_suffix: currentStockDateSuffix,
        plate_category: category,
        auto_create_kits: true,
    };

    const feedback = document.getElementById('stock-feedback-alert');

    try {
        const res = await fetch('/api/stock/dispatch/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success) {
            const blocked = (data.rejected_not_on_stock || []).concat(data.already_installed || []);
            const newCount = data.newly_dispatched || 0;

            if (blocked.length > 0) {
                if (feedback) {
                    feedback.className = 'alert alert-danger';
                    feedback.innerHTML = `⛔ ${blocked.length} KIT(S) BLOCKED: Dispatched ${newCount}. Set ${blocked.length} physical boxes aside!`;
                }
                notify(`⚠️ Dispatched ${newCount} kits. ${blocked.length} kit(s) blocked.`, 'warning');
                inspectPlate(blocked[0]);
                openNotOnStockModal(data.docket || data);
            } else {
                const bgSyncNote = data.enriching_in_background ? ' Background ITMS sync initiated ⚡' : '';
                if (feedback) {
                    feedback.className = 'alert alert-success';
                    feedback.innerHTML = `✓ DISPATCH SUCCESS: Recorded ${newCount} plates.${bgSyncNote}`;
                }
                notify(`✓ Dispatched ${newCount} ${category} plates.${bgSyncNote}`, 'success');
                if (platesList.length === 1) {
                    inspectPlate(platesList[0]);
                } else if (data.verified_plates && data.verified_plates.length > 0) {
                    inspectPlate(data.verified_plates[0]);
                }
            }

            refreshStockLedger();
        } else {
            const blocked = (data.rejected_not_on_stock || []).concat(data.already_installed || []);
            if (blocked.length > 0) {
                if (feedback) {
                    feedback.className = 'alert alert-danger';
                    feedback.innerHTML = `⛔ ${blocked.length} KIT(S) BLOCKED: 0 dispatched. Set physical boxes aside!`;
                }
                inspectPlate(blocked[0]);
                openNotOnStockModal(data.docket || data);
            } else {
                if (feedback) {
                    feedback.className = 'alert alert-danger';
                    feedback.innerHTML = `⛔ Dispatch failed: ${data.error}`;
                }
                if (platesList.length === 1) {
                    inspectPlate(platesList[0], data.error);
                }
            }
            notify(`Dispatch failed: ${data.error}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    }
}

function clearDispatchesToday() {
    if (!confirm('Are you sure you want to clear all dispatches for this shift? This will reset dispatched counts.')) {
        return;
    }

    fetch('/api/stock/dispatch/clear/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date_suffix: currentStockDateSuffix }),
    })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                notify(`✓ Cleared ${data.cleared_count || 0} dispatches for shift ${data.date_suffix || currentStockDateSuffix}.`, 'success');
                refreshStockLedger();
            } else {
                notify(`Failed to clear dispatches: ${data.error}`, 'error');
            }
        })
        .catch(e => notify(`Error: ${e.message}`, 'error'));
}

// -----------------------------------------------------------------------------
// Mode 2: Movements & Staging Operations
// -----------------------------------------------------------------------------

function stageMovementsPlates(plates) {
    const valid = plates.map(p => p.trim().toUpperCase()).filter(p => p.length > 3);
    if (valid.length === 0) return;

    // Deduplicate against stagedMovements
    const set = new Set(stagedMovements);
    valid.forEach(p => set.add(p));
    stagedMovements = Array.from(set);

    const lbl = document.getElementById('lbl-movement-staged');
    if (lbl) {
        lbl.innerText = `Staged: ${stagedMovements.length} plates`;
        lbl.className = `badge ${stagedMovements.length > 0 ? 'badge-yellow' : 'badge-muted'}`;
    }

    notify(`✓ Staged ${valid.length} plate(s). Total staged: ${stagedMovements.length}`, 'info');
}

function clearMovementStaged() {
    stagedMovements = [];
    const lbl = document.getElementById('lbl-movement-staged');
    if (lbl) {
        lbl.innerText = 'Staged: 0 plates';
        lbl.className = 'badge badge-muted';
    }
    const pInput = document.getElementById('movement-partner');
    if (pInput) pInput.value = '';
    const rInput = document.getElementById('movement-ref');
    if (rInput) rInput.value = '';

    notify('Cleared staged movement plates.', 'info');
}

async function commitMovement() {
    if (stagedMovements.length === 0) {
        notify('Please scan or paste plates to stage for movement first.', 'warning');
        const scan = document.getElementById('stock-universal-scan');
        if (scan) scan.focus();
        return;
    }

    const moveType = document.getElementById('movement-type').value;
    const category = document.getElementById('stock-category-select').value;
    const partner = (document.getElementById('movement-partner').value || '').trim();
    const ref = (document.getElementById('movement-ref').value || '').trim();

    let endpoint = '';
    let payload = {
        plates: stagedMovements.join('\n'),
        date_suffix: currentStockDateSuffix,
        plate_category: category,
        notes: ref,
    };

    if (moveType === 'DELIVERY') {
        endpoint = '/api/stock/delivery/';
        payload.supplier = partner || 'Factory / Central Depot';
        payload.paper_note_reference = ref;
    } else if (moveType === 'TRANSFER_IN' || moveType === 'TRANSFER_OUT') {
        endpoint = '/api/stock/bond_transfer/';
        payload.transfer_type = moveType;
        payload.other_bond_name = partner || 'Other Bond';
        payload.plates_count = stagedMovements.length;
    } else if (moveType === 'RETURN') {
        endpoint = '/api/stock/return/';
        payload.reason = partner || 'BIKE_NO_SHOW';
    }

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success) {
            const count = stagedMovements.length;
            const feedback = document.getElementById('stock-feedback-alert');
            if (moveType === 'DELIVERY') {
                notify(`✓ Saved Inbound Delivery of ${count} plates! Background ITMS lookup initiated.`, 'success');
                if (feedback) {
                    feedback.className = 'alert alert-success';
                    feedback.innerHTML = `✓ INBOUND DELIVERY SAVED: ${count} plates recorded instantly. Looking up ITMS kit hardware in background...`;
                }
            } else {
                notify(`✓ Recorded ${moveType} for ${count} plates!`, 'success');
            }
            clearMovementStaged();
            refreshStockLedger();
        } else {
            notify(`Movement failed: ${data.error}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    }
}

// -----------------------------------------------------------------------------
// Mode 3: Safe Room Physical Audit
// -----------------------------------------------------------------------------

async function submitAudit(platesList) {
    const feedback = document.getElementById('stock-feedback-alert');
    try {
        notify(`🔒 Auditing ${platesList.length} plates against Safe Room stock...`, 'info');
        const res = await fetch('/api/stock/audit/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                plates: platesList.join('\n'),
                date_suffix: currentStockDateSuffix,
            }),
        });
        const data = await res.json();

        if (data.success) {
            const scanned = data.total_scanned || platesList.length;
            const verified = data.verified_count || 0;
            const unregistered = data.unregistered_count || 0;
            const book = data.book_closing_total || 0;
            const variance = data.variance || 0;
            const varSign = variance >= 0 ? `+${variance}` : `${variance}`;

            const sumEl = document.getElementById('audit-summary');
            if (sumEl) {
                sumEl.innerHTML = `Safe Audit Summary: Physical Audited: <b>${scanned}</b> │ Verified: <span style="color:var(--ug-green);">${verified}</span> │ Unregistered: <span style="color:var(--ug-red);">${unregistered}</span> │ Book Closing: <b>${book}</b> │ Variance: <b>${varSign}</b>`;
            }

            const manualInput = document.getElementById('audit-manual-count');
            if (manualInput) manualInput.value = scanned;

            if (unregistered > 0) {
                if (feedback) {
                    feedback.className = 'alert alert-warning';
                    feedback.innerHTML = `⚠️ SAFE AUDIT SAVED: ${scanned} plates recorded (${unregistered} unregistered). Looking up ITMS kit hardware in background...`;
                }
                notify(`✓ Safe audit saved (${scanned} plates). Background ITMS lookup active for unlinked kits.`, 'warning');
            } else {
                if (feedback) {
                    feedback.className = 'alert alert-success';
                    feedback.innerHTML = `✓ SAFE AUDIT SAVED: All ${scanned} plates recorded instantly! Variance: ${varSign}. Background sync active.`;
                }
                notify(`✓ Safe audit saved: All ${scanned} plates recorded instantly! Variance: ${varSign}.`, 'success');
            }

            if (platesList.length === 1) {
                inspectPlate(platesList[0]);
            } else if (data.verified_plates && data.verified_plates.length > 0) {
                inspectPlate(data.verified_plates[0]);
            }

            refreshStockLedger();
        } else {
            notify(`Audit failed: ${data.error}`, 'error');
            if (feedback) {
                feedback.className = 'alert alert-danger';
                feedback.innerText = `⛔ Audit failed: ${data.error}`;
            }
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    }
}

async function setManualAuditCount() {
    const val = document.getElementById('audit-manual-count').value;
    if (!val || isNaN(val)) {
        notify('Please enter a valid numeric physical count (e.g. 1200).', 'warning');
        return;
    }

    try {
        const res = await fetch('/api/stock/physical_count/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                physical_count: parseInt(val, 10),
                date_suffix: currentStockDateSuffix,
            }),
        });
        const data = await res.json();
        if (data.success) {
            notify(`✓ Physical count updated to ${parseInt(val, 10).toLocaleString()} plates!`, 'success');
            refreshStockLedger();
        } else {
            notify(`Failed to update physical count: ${data.error}`, 'error');
        }
    } catch (e) {
        notify(`Error: ${e.message}`, 'error');
    }
}

function exportUnregistered() {
    window.open(`/api/stock/export/unregistered/?date_suffix=${currentStockDateSuffix}`, '_blank');
}

// -----------------------------------------------------------------------------
// Excel Bulk Paste Modal
// -----------------------------------------------------------------------------

function openStockPasteModal() {
    const area = document.getElementById('stock-paste-area');
    if (area) area.value = '';

    const warn = document.getElementById('paste-movement-warning');
    if (warn) {
        warn.style.display = currentStockMode === 'movements' ? 'block' : 'none';
    }

    const modeLbl = document.getElementById('paste-mode-label');
    if (modeLbl) modeLbl.innerText = currentStockMode.toUpperCase();

    if (typeof openModal === 'function') {
        openModal('stock-paste-modal');
    } else {
        const m = document.getElementById('stock-paste-modal');
        if (m) m.classList.add('active');
    }

    if (area) {
        setTimeout(() => area.focus(), 100);
    }
}

function closeStockPasteModal() {
    if (typeof closeModal === 'function') {
        closeModal('stock-paste-modal');
    } else {
        const m = document.getElementById('stock-paste-modal');
        if (m) m.classList.remove('active');
    }
}

async function processStockPaste() {
    const raw = document.getElementById('stock-paste-area').value;
    const plates = raw
        .split(/[\n\r,]+/)
        .map(p => p.trim().toUpperCase())
        .filter(p => p.length > 3);

    if (plates.length === 0) {
        notify('No valid license plates found in pasted text.', 'warning');
        return;
    }

    closeStockPasteModal();

    if (currentStockMode === 'dispatch') {
        await submitDispatch(plates);
    } else if (currentStockMode === 'movements') {
        stageMovementsPlates(plates);
    } else if (currentStockMode === 'audit') {
        await submitAudit(plates);
    } else {
        notify('Bulk paste is not applicable to Ledger mode.', 'warning');
    }
}

// -----------------------------------------------------------------------------
// Not On Stock (Blocked Plates) & ITMS Transfer Docket
// -----------------------------------------------------------------------------

let lastNotOnStockData = null;

async function checkStockPasteAvailability() {
    const area = document.getElementById('stock-paste-area');
    if (!area) return;
    const raw = area.value;
    const plates = raw
        .split(/[\n\r,]+/)
        .map(p => p.trim().toUpperCase())
        .filter(p => p.length > 3);

    if (plates.length === 0) {
        notify('No valid license plates found in pasted text.', 'warning');
        return;
    }

    notify(`🔍 Verifying ${plates.length} plates against stock...`, 'info');

    try {
        const res = await fetch('/api/stock/verify-batch/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                plates: plates.join('\n'),
                date_suffix: currentStockDateSuffix,
                check_itms_live: true,
            }),
        });
        const data = await res.json();

        if (data.success) {
            const blocked = data.blocked_plates || (data.rejected_not_on_stock || []).concat(data.already_installed || []);
            const validCount = (data.verified_plates || []).length;

            if (blocked.length === 0) {
                notify(`✓ All ${validCount} plate(s) are valid on stock! Clean to dispatch.`, 'success');
                const feedback = document.getElementById('stock-feedback-alert');
                if (feedback) {
                    feedback.className = 'alert alert-success';
                    feedback.innerHTML = `✓ STOCK CHECK PASS: All ${validCount} plate(s) are available in warehouse stock.`;
                }
            } else {
                closeStockPasteModal();
                notify(`⚠️ Stock Check: ${validCount} in stock, ${blocked.length} NOT on stock!`, 'warning');
                openNotOnStockModal(data.docket || data);
            }
        } else {
            notify(`Stock verification failed: ${data.error}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    }
}

async function checkShiftBlockedPlates() {
    try {
        const res = await fetch(`/api/stock/blocked-plates/?date_suffix=${currentStockDateSuffix}`);
        const data = await res.json();
        const btn = document.getElementById('btn-view-blocked-stock');
        const lbl = document.getElementById('lbl-blocked-stock-count');

        if (data.success && data.docket && data.docket.count > 0) {
            lastNotOnStockData = data.docket;
            if (lbl) lbl.innerText = data.docket.count;
            if (btn) btn.style.display = 'inline-flex';
        } else {
            if (btn) btn.style.display = 'none';
        }
    } catch (e) {
        console.debug('checkShiftBlockedPlates notice:', e);
    }
}

async function openNotOnStockModal(data) {
    if (!data) {
        if (lastNotOnStockData) {
            data = lastNotOnStockData;
        } else {
            try {
                const res = await fetch(`/api/stock/blocked-plates/?date_suffix=${currentStockDateSuffix}`);
                const resData = await res.json();
                if (resData.success && resData.docket) {
                    data = resData.docket;
                }
            } catch (e) {
                console.error(e);
            }
        }
    }

    if (!data) {
        notify('No blocked or unlisted plates recorded for this shift.', 'info');
        return;
    }

    lastNotOnStockData = data;

    const count = data.count !== undefined ? data.count : (data.blocked_plates ? data.blocked_plates.length : 0);
    const total = data.total_submitted !== undefined ? data.total_submitted : count;
    const valid = data.verified_plates ? data.verified_plates.length : Math.max(0, total - count);

    // Update Header Badges
    const countBadge = document.getElementById('not-on-stock-count-badge');
    if (countBadge) countBadge.innerText = count;

    const statTotal = document.getElementById('not-on-stock-stat-total');
    if (statTotal) statTotal.innerText = total;

    const statValid = document.getElementById('not-on-stock-stat-valid');
    if (statValid) statValid.innerText = valid;

    const statBlocked = document.getElementById('not-on-stock-stat-blocked');
    if (statBlocked) statBlocked.innerText = count;

    // Update Command Bar Button
    const btn = document.getElementById('btn-view-blocked-stock');
    const lbl = document.getElementById('lbl-blocked-stock-count');
    if (lbl) lbl.innerText = count;
    if (btn && count > 0) btn.style.display = 'inline-flex';

    // Populate Table
    const tbody = document.getElementById('not-on-stock-table-body');
    if (tbody) {
        tbody.innerHTML = '';
        const items = data.items || [];
        if (items.length > 0) {
            items.forEach((item, idx) => {
                const tr = document.createElement('tr');
                const isInstalled = item.status === 'ALREADY_INSTALLED';
                const statusBadge = isInstalled
                    ? '<span class="badge badge-yellow" style="font-size: 0.75rem;">ALREADY INSTALLED</span>'
                    : '<span class="badge badge-danger" style="font-size: 0.75rem;">NOT ON STOCK</span>';

                tr.innerHTML = `
                    <td style="color: var(--ug-text-muted); font-size: 0.85rem;">${item.index || idx + 1}</td>
                    <td style="font-weight: 700; font-family: monospace; font-size: 0.95rem; color: #fff;">${item.plate}</td>
                    <td><span class="badge badge-muted" style="font-size: 0.75rem;">${item.category || 'PSV'}</span></td>
                    <td style="font-size: 0.85rem;">
                        ${statusBadge}
                        <div style="font-size: 0.78rem; color: var(--ug-text-muted); margin-top: 2px;">${item.reason || 'Missing from ITMS Safe Room stock'}</div>
                    </td>
                    <td style="text-align: center;">
                        <span class="badge badge-danger" style="font-size: 0.75rem; background: rgba(220,53,69,0.2); border: 1px solid #dc3545; color: #ff6b6b;">
                            Set Box Aside
                        </span>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            const rawList = data.blocked_plates || data.plates || data.rejected_not_on_stock || [];
            if (rawList.length > 0) {
                rawList.forEach((p, idx) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td style="color: var(--ug-text-muted); font-size: 0.85rem;">${idx + 1}</td>
                        <td style="font-weight: 700; font-family: monospace; font-size: 0.95rem; color: #fff;">${p}</td>
                        <td><span class="badge badge-muted" style="font-size: 0.75rem;">PSV</span></td>
                        <td style="font-size: 0.85rem;">
                            <span class="badge badge-danger" style="font-size: 0.75rem;">NOT ON STOCK</span>
                            <div style="font-size: 0.78rem; color: var(--ug-text-muted); margin-top: 2px;">Not found in local stock or live ITMS /installation-kits</div>
                        </td>
                        <td style="text-align: center;">
                            <span class="badge badge-danger" style="font-size: 0.75rem; background: rgba(220,53,69,0.2); border: 1px solid #dc3545; color: #ff6b6b;">
                                Set Box Aside
                            </span>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } else {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding: 20px; color: var(--ug-text-muted);">No blocked plates recorded for this shift.</td></tr>`;
            }
        }
    }

    // Populate Formatted Message Text
    const txtArea = document.getElementById('not-on-stock-text-area');
    if (txtArea) {
        txtArea.value = data.formatted_message || data.docket?.formatted_message || (data.plates ? data.plates.join('\n') : '');
    }

    switchNotOnStockView('table');

    if (typeof openModal === 'function') {
        openModal('stock-not-on-stock-modal');
    } else {
        const m = document.getElementById('stock-not-on-stock-modal');
        if (m) m.classList.add('active');
    }
}

function closeNotOnStockModal() {
    if (typeof closeModal === 'function') {
        closeModal('stock-not-on-stock-modal');
    } else {
        const m = document.getElementById('stock-not-on-stock-modal');
        if (m) m.classList.remove('active');
    }
}

function switchNotOnStockView(viewMode) {
    const tblView = document.getElementById('not-on-stock-view-table');
    const txtView = document.getElementById('not-on-stock-view-text');
    const btnTbl = document.getElementById('btn-tab-not-on-stock-table');
    const btnTxt = document.getElementById('btn-tab-not-on-stock-text');

    if (viewMode === 'text') {
        if (tblView) tblView.style.display = 'none';
        if (txtView) txtView.style.display = 'flex';
        if (btnTbl) { btnTbl.className = 'btn btn-secondary'; }
        if (btnTxt) { btnTxt.className = 'btn btn-primary'; }
    } else {
        if (tblView) tblView.style.display = 'block';
        if (txtView) txtView.style.display = 'none';
        if (btnTbl) { btnTbl.className = 'btn btn-primary'; }
        if (btnTxt) { btnTxt.className = 'btn btn-secondary'; }
    }
}

function _fallbackCopyText(text, successMsg) {
    const el = document.createElement('textarea');
    el.value = text;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
    notify(successMsg, 'success');
}

function copyNotOnStockRawPlates() {
    if (!lastNotOnStockData) return;
    const raw = lastNotOnStockData.raw_plates
        || (lastNotOnStockData.plates ? lastNotOnStockData.plates.join('\n') : '')
        || (lastNotOnStockData.blocked_plates ? lastNotOnStockData.blocked_plates.join('\n') : '');

    if (!raw) {
        notify('No plate numbers to copy.', 'warning');
        return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(raw).then(() => {
            notify('📋 Copied raw plates to clipboard! (Ready to paste into ITMS)', 'success');
        }).catch(() => {
            _fallbackCopyText(raw, '📋 Copied raw plates to clipboard!');
        });
    } else {
        _fallbackCopyText(raw, '📋 Copied raw plates to clipboard!');
    }
}

function copyNotOnStockMessage() {
    if (!lastNotOnStockData) return;
    const msg = lastNotOnStockData.formatted_message
        || (document.getElementById('not-on-stock-text-area') ? document.getElementById('not-on-stock-text-area').value : '');

    if (!msg) {
        notify('No message text to copy.', 'warning');
        return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(msg).then(() => {
            notify('💬 Copied Transfer Manager message to clipboard! (Ready for WhatsApp/Email)', 'success');
        }).catch(() => {
            _fallbackCopyText(msg, '💬 Copied Transfer Manager message to clipboard!');
        });
    } else {
        _fallbackCopyText(msg, '💬 Copied Transfer Manager message to clipboard!');
    }
}

function downloadNotOnStockDocket() {
    if (!lastNotOnStockData) return;
    const text = lastNotOnStockData.formatted_message
        || (document.getElementById('not-on-stock-text-area') ? document.getElementById('not-on-stock-text-area').value : '');

    if (!text) {
        notify('No docket content to download.', 'warning');
        return;
    }

    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `ITMS_Stock_Transfer_Request_${currentStockDateSuffix || 'shift'}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    notify('📥 Downloaded ITMS Stock Transfer Request docket (.txt)', 'success');
}

// -----------------------------------------------------------------------------
// Opening & Target Balance Sheet Modal
// -----------------------------------------------------------------------------

function openLedgerTargetModal() {
    if (lastReconData) {
        const rep = lastReconData.report_table || {};
        const rows = rep.rows || [];
        const openRow = rows.find(r => r.metric === 'Opening Balance') || {};

        const psvInput = document.getElementById('ledger-opening-psv');
        const pmoInput = document.getElementById('ledger-opening-pmo');
        const tgtInput = document.getElementById('ledger-target-total');

        if (psvInput && openRow.psv !== undefined) psvInput.value = openRow.psv;
        if (pmoInput && openRow.pmo !== undefined) pmoInput.value = openRow.pmo;

        const schedRow = rows.find(r => r.metric === 'Target (Scheduled)') || {};
        if (tgtInput && schedRow.total !== undefined) tgtInput.value = schedRow.total;

        updateTotalOpening();
    }

    if (typeof openModal === 'function') {
        openModal('stock-target-modal');
    } else {
        const m = document.getElementById('stock-target-modal');
        if (m) m.classList.add('active');
    }
}

function closeLedgerTargetModal() {
    if (typeof closeModal === 'function') {
        closeModal('stock-target-modal');
    } else {
        const m = document.getElementById('stock-target-modal');
        if (m) m.classList.remove('active');
    }
}

function updateTotalOpening() {
    const psv = parseInt(document.getElementById('ledger-opening-psv').value || '0', 10);
    const pmo = parseInt(document.getElementById('ledger-opening-pmo').value || '0', 10);
    const totEl = document.getElementById('ledger-opening-total');
    if (totEl) {
        totEl.innerText = (psv + pmo).toLocaleString();
    }
}

async function autoCarryPreviousClosing() {
    try {
        notify('⚡ Fetching previous shift closing balance...', 'info');
        const res = await fetch(`/api/stock/previous-closing/?date_suffix=${currentStockDateSuffix}`);
        const data = await res.json();

        if (data.success && data.data) {
            const d = data.data;
            document.getElementById('ledger-opening-psv').value = d.opening_psv || 0;
            document.getElementById('ledger-opening-pmo').value = d.opening_pmo || 0;
            updateTotalOpening();

            notify(`⚡ Auto-carried closing balances from shift ${d.from_shift_suffix || 'previous'}: PSV ${d.opening_psv}, PMO ${d.opening_pmo}.`, 'success');
        } else {
            notify('No previous shift closing balance available.', 'warning');
        }
    } catch (e) {
        notify(`Error: ${e.message}`, 'error');
    }
}

async function saveLedgerTargets() {
    const psv = document.getElementById('ledger-opening-psv').value;
    const pmo = document.getElementById('ledger-opening-pmo').value;
    const target = document.getElementById('ledger-target-total').value;
    const remarks = document.getElementById('ledger-remarks').value;

    try {
        if (psv !== '') {
            await fetch('/api/stock/opening/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    opening_psv: parseInt(psv || '0', 10),
                    opening_pmo: parseInt(pmo || '0', 10),
                    notes: remarks,
                    date_suffix: currentStockDateSuffix,
                }),
            });
        }

        if (target !== '') {
            await fetch('/api/stock/scheduled/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scheduled_target: parseInt(target, 10),
                    date_suffix: currentStockDateSuffix,
                }),
            });
        }

        closeLedgerTargetModal();
        notify('✓ Shift opening balances & target saved successfully!', 'success');
        refreshStockLedger();
    } catch (e) {
        notify(`Failed to save targets: ${e.message}`, 'error');
    }
}

// -----------------------------------------------------------------------------
// Unified ITMS Sync & CSV Export
// -----------------------------------------------------------------------------

async function syncStock() {
    if (isSyncingStock) {
        notify('Sync is already running...', 'warning');
        return;
    }

    isSyncingStock = true;
    notify('⟳ Starting unified sync (ITMS Installation Kits & Shift Orders)...', 'info');

    try {
        const res = await fetch('/api/stock/kits/sync/', { method: 'POST' });
        const data = await res.json();

        if (data.success) {
            notify('✓ Unified ITMS sync complete!', 'success');
            refreshStockLedger();
        } else {
            notify(`Sync warning: ${data.error || 'Server error'}`, 'warning');
        }
    } catch (e) {
        notify(`Sync error: ${e.message}`, 'error');
    } finally {
        isSyncingStock = false;
    }
}

function exportStockCSV() {
    window.location.href = `/api/stock/export/csv/?date_suffix=${currentStockDateSuffix}`;
}

// -----------------------------------------------------------------------------
// Data Fetching & Rendering
// -----------------------------------------------------------------------------

async function refreshStockLedger() {
    try {
        const url = `/api/stock/reconciliation/?date_suffix=${currentStockDateSuffix}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            lastReconData = data.reconciliation;
            updateDashboard(data.reconciliation);
        }

        // Fetch shift details (dispatches, movements, audits)
        await fetchStockDetails();
        await checkShiftBlockedPlates();
    } catch (e) {
        console.error('refreshStockLedger error:', e);
    }
}

async function fetchStockDetails() {
    try {
        const url = `/api/stock/shift/details/?date_suffix=${currentStockDateSuffix}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            renderDispatchesTable(data.dispatches);
            renderMovementsTable(data.movements);
            renderAuditsTable(data.audits);
        }
    } catch (e) {
        console.error('fetchStockDetails error:', e);
    }
}

function updateDashboard(recon) {
    if (!recon) return;

    currentStockDateSuffix = recon.work_date_suffix || currentStockDateSuffix;
    const dateDisplay = document.getElementById('stock-date-display');
    if (dateDisplay) {
        dateDisplay.innerText = `Shift: ${currentStockDateSuffix}`;
    }

    const tot = recon.total || {};
    const unallocCount = (recon.unallocated_plates || []).length;

    const stripHtml = `
        <span class="badge badge-muted">Opening: ${tot.opening || 0}</span>
        <span class="badge badge-muted">Received: ${tot.received || 0}</span>
        <span class="badge badge-muted">Dispatched: ${tot.dispatched || 0}</span>
        <span class="badge badge-muted">Installed: ${tot.installed || 0}</span>
        <span class="badge badge-cyan">Closing: ${tot.closing_stock || 0}</span>
        <span class="badge badge-yellow">Physical: ${tot.physical_count || 0}</span>
        <span class="badge ${unallocCount > 0 ? 'badge-red' : 'badge-green'}">Unallocated: ${unallocCount}</span>
    `;

    const stripEl = document.getElementById('stock-status-strip');
    if (stripEl) {
        stripEl.innerHTML = stripHtml;
    }

    // Ledger Table rows
    const tbody = document.querySelector('#table-stock-ledger tbody');
    if (tbody && recon.report_table && recon.report_table.rows) {
        tbody.innerHTML = '';
        recon.report_table.rows.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-weight: 600;">${row.metric}</td>
                <td style="font-family: var(--font-mono);">${row.pmo}</td>
                <td style="font-family: var(--font-mono);">${row.psv}</td>
                <td style="font-weight: 700; font-family: var(--font-mono);">${row.total}</td>
                <td style="color: var(--ug-text-muted); font-size: 0.85em;">${row.note || '—'}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Dispatch summary counter
    const dispSummary = document.getElementById('dispatch-summary');
    if (dispSummary) {
        dispSummary.innerText = `Dispatched Today: ${tot.dispatched || 0} kits`;
    }
}

function renderDispatchesTable(dispatches) {
    const tbody = document.querySelector('#table-stock-dispatch tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!dispatches || dispatches.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center" style="padding: 30px; color: var(--ug-text-muted);">No plates dispatched today yet.</td></tr>';
        return;
    }

    dispatches.forEach((d, idx) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${idx + 1}</td>
            <td class="text-muted" style="font-family: var(--font-mono);">${(d.dispatched_at || '').substring(11, 19)}</td>
            <td style="font-weight: 800; font-family: var(--font-mono); color: var(--ug-yellow);">${d.registration_number}</td>
            <td><span class="badge ${d.plate_category === 'PMO' ? 'badge-yellow' : 'badge-muted'}">${d.plate_category}</span></td>
            <td style="font-family: var(--font-mono); color: #fff;">IK-${d.registration_number}</td>
            <td style="font-family: var(--font-mono); color: var(--ug-cyan);">${d.gps_tracker || '—'}</td>
            <td><span class="badge badge-green">${d.status || 'ON LINE'}</span></td>
            <td class="text-muted">${d.operator_name || d.operator_username || 'Operator'}</td>
        `;
        tr.style.cursor = 'pointer';
        tr.onclick = () => inspectPlate(d.registration_number);
        tbody.appendChild(tr);
    });
}

function renderMovementsTable(movements) {
    const tbody = document.querySelector('#table-stock-movements tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!movements || movements.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center" style="padding: 30px; color: var(--ug-text-muted);">No stock movements recorded for this shift yet.</td></tr>';
        return;
    }

    movements.forEach((m, idx) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${idx + 1}</td>
            <td class="text-muted" style="font-family: var(--font-mono);">${m.time}</td>
            <td style="font-weight: 700; color: #fff;">${m.type}</td>
            <td><span class="badge ${m.category === 'PMO' ? 'badge-yellow' : 'badge-muted'}">${m.category}</span></td>
            <td>${m.partner}</td>
            <td style="font-family: var(--font-mono);">${m.ref || '—'}</td>
            <td style="font-weight: 800; font-family: var(--font-mono);">${m.count}</td>
            <td class="text-muted" style="font-size: 0.85em; font-family: var(--font-mono);">${m.sample_plates || '—'}</td>
        `;
        tr.style.cursor = 'pointer';
        if (m.sample_plates) {
            const firstPlate = m.sample_plates.split(',')[0].trim();
            if (firstPlate) tr.onclick = () => inspectPlate(firstPlate);
        }
        tbody.appendChild(tr);
    });
}

function renderAuditsTable(audits) {
    const tbody = document.querySelector('#table-stock-audit tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!audits || audits.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center" style="padding: 30px; color: var(--ug-text-muted);">No physical stock audited for this shift yet.</td></tr>';
        return;
    }

    audits.forEach((a, idx) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${idx + 1}</td>
            <td style="font-weight: 800; font-family: var(--font-mono); color: var(--ug-yellow);">${a.plate}</td>
            <td><span class="badge badge-green">✓ AUDITED</span></td>
            <td style="font-family: var(--font-mono); color: #fff;">${a.kit_code || `IK-${a.plate}`}</td>
            <td style="font-family: var(--font-mono); color: var(--ug-cyan);">${a.gps_tracker || '—'}</td>
            <td class="text-muted" style="font-family: var(--font-mono);">${a.front_ble || '—'}</td>
            <td class="text-muted" style="font-family: var(--font-mono);">${a.rear_ble || '—'}</td>
        `;
        tr.style.cursor = 'pointer';
        tr.onclick = () => inspectPlate(a.plate);
        tbody.appendChild(tr);
    });
}

// -----------------------------------------------------------------------------
// Shift Date Navigation
// -----------------------------------------------------------------------------

function parseDateSuffix(suffix) {
    if (!suffix || suffix.length !== 6) return new Date();
    const dd = parseInt(suffix.substring(0, 2), 10);
    const mm = parseInt(suffix.substring(2, 4), 10) - 1;
    const yy = parseInt(suffix.substring(4, 6), 10);
    return new Date(2000 + yy, mm, dd);
}

function formatDateSuffix(date) {
    const dd = String(date.getDate()).padStart(2, '0');
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const yy = String(date.getFullYear()).substring(2);
    return `${dd}${mm}${yy}`;
}

async function getShiftDates() {
    if (cachedShiftDates) return cachedShiftDates;
    try {
        const res = await fetch('/api/stock/shift/dates/');
        const data = await res.json();
        if (data.success && data.dates) {
            cachedShiftDates = data.dates;
            return cachedShiftDates;
        }
    } catch (e) {
        console.error('getShiftDates error:', e);
    }
    return [];
}

async function navigateStockDay(direction) {
    const dates = await getShiftDates();
    if (!dates || dates.length === 0) {
        const d = parseDateSuffix(currentStockDateSuffix);
        d.setDate(d.getDate() + (direction === 'prev' ? -1 : 1));
        currentStockDateSuffix = formatDateSuffix(d);
        refreshStockLedger();
        return;
    }

    const current = currentStockDateSuffix || formatDateSuffix(new Date());
    const idx = dates.indexOf(current);

    if (idx === -1) {
        if (direction === 'prev') {
            currentStockDateSuffix = dates[0] || current;
        }
    } else {
        if (direction === 'prev') {
            if (idx + 1 < dates.length) currentStockDateSuffix = dates[idx + 1];
        } else {
            if (idx - 1 >= 0) currentStockDateSuffix = dates[idx - 1];
            else currentStockDateSuffix = '';
        }
    }
    refreshStockLedger();
}

function stockPrevDay() {
    navigateStockDay('prev');
}

function stockNextDay() {
    navigateStockDay('next');
}

function stockToday() {
    currentStockDateSuffix = '';
    refreshStockLedger();
}
