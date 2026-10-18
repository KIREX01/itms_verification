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
    void refreshStockLedger();

    // Universal scan input listener
    const scanInput = document.getElementById('stock-universal-scan');
    if (scanInput) {
        scanInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const val = scanInput.value.trim();
                scanInput.value = '';
                if (val) {
                    void handleUniversalScan(val);
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
                void syncStock();
            } else if (e.key === 'e' || e.key === 'E') {
                e.preventDefault();
                if (currentStockMode === 'dispatch') {
                    exportDispatchCSV();
                } else if (currentStockMode === 'movements') {
                    exportMovementsCSV();
                } else if (currentStockMode === 'audit') {
                    exportAuditCSV();
                } else {
                    exportStockCSV();
                }
            } else if (e.key === 'o' || e.key === 'O') {
                e.preventDefault();
                openLedgerTargetModal();
            } else if (e.key === 'r' || e.key === 'R') {
                e.preventDefault();
                void refreshStockLedger();
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
        await inspectPlate(val);
    } else if (currentStockMode === 'audit') {
        await submitAudit([val]);
    } else {
        // Ledger mode - inspect plate
        await inspectPlate(val);
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

            // ITMS Web Portal Quick Link
            const itmsLink = document.getElementById('link-ins-itms-web');
            if (itmsLink) {
                if (order.has_order) {
                    const detailUrl = order.detail_url || (order.kit_uuid ? `https://stock.itms.ug/installation-kit/${order.kit_uuid}/main/information` : (data.plate ? `https://stock.itms.ug/installation-order/index?InstallationOrderSearchFieldSearch%5Bsearch%5D=${encodeURIComponent(data.plate)}` : ''));
                    if (detailUrl) {
                        itmsLink.href = detailUrl;
                        itmsLink.style.display = 'inline-flex';
                    } else {
                        itmsLink.style.display = 'none';
                    }
                } else if (data.plate) {
                    itmsLink.href = `https://stock.itms.ug/installation-kits/index?InstallationKitSearchFieldSearch%5Bsearch%5D=${encodeURIComponent(data.plate)}`;
                    itmsLink.style.display = 'inline-flex';
                } else {
                    itmsLink.style.display = 'none';
                }
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
                await inspectPlate(blocked[0]);
                await openNotOnStockModal(data.docket || data);
            } else {
                const bgSyncNote = data.enriching_in_background ? ' Background ITMS sync initiated ⚡' : '';
                if (feedback) {
                    feedback.className = 'alert alert-success';
                    feedback.innerHTML = `✓ DISPATCH SUCCESS: Recorded ${newCount} plates.${bgSyncNote}`;
                }
                notify(`✓ Dispatched ${newCount} ${category} plates.${bgSyncNote}`, 'success');
                if (platesList.length === 1) {
                    await inspectPlate(platesList[0]);
                } else if (data.verified_plates && data.verified_plates.length > 0) {
                    await inspectPlate(data.verified_plates[0]);
                }
            }

            await refreshStockLedger();
        } else {
            const blocked = (data.rejected_not_on_stock || []).concat(data.already_installed || []);
            if (blocked.length > 0) {
                if (feedback) {
                    feedback.className = 'alert alert-danger';
                    feedback.innerHTML = `⛔ ${blocked.length} KIT(S) BLOCKED: 0 dispatched. Set physical boxes aside!`;
                }
                await inspectPlate(blocked[0]);
                await openNotOnStockModal(data.docket || data);
            } else {
                if (feedback) {
                    feedback.className = 'alert alert-danger';
                    feedback.innerHTML = `⛔ Dispatch failed: ${data.error}`;
                }
                if (platesList.length === 1) {
                    await inspectPlate(platesList[0], data.error);
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
                void refreshStockLedger();
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
            await refreshStockLedger();
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
                await inspectPlate(platesList[0]);
            } else if (data.verified_plates && data.verified_plates.length > 0) {
                await inspectPlate(data.verified_plates[0]);
            }

            await refreshStockLedger();
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
    if (!val || Number.isNaN(Number(val))) {
        notify('Please enter a valid numeric physical count (e.g. 1200).', 'warning');
        return;
    }

    try {
        const res = await fetch('/api/stock/physical_count/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                physical_count: Number.parseInt(val, 10),
                date_suffix: currentStockDateSuffix,
            }),
        });
        const data = await res.json();
        if (data.success) {
            notify(`✓ Physical count updated to ${Number.parseInt(val, 10).toLocaleString()} plates!`, 'success');
            await refreshStockLedger();
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
                await openNotOnStockModal(data.docket || data);
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
        } else if (btn) {
            btn.style.display = 'none';
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

        let count = 0;
    if (data.count !== undefined) {
        count = data.count;
    } else if (data.blocked_plates) {
        count = data.blocked_plates.length;
    }
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
    el.remove();
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

    if (navigator.clipboard?.writeText) {
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

    if (navigator.clipboard?.writeText) {
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
    link.remove();
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
    const psv = Number.parseInt(document.getElementById('ledger-opening-psv').value || '0', 10);
    const pmo = Number.parseInt(document.getElementById('ledger-opening-pmo').value || '0', 10);
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
                    opening_psv: Number.parseInt(psv || '0', 10),
                    opening_pmo: Number.parseInt(pmo || '0', 10),
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
                    scheduled_target: Number.parseInt(target, 10),
                    date_suffix: currentStockDateSuffix,
                }),
            });
        }

        closeLedgerTargetModal();
        notify('✓ Shift opening balances & target saved successfully!', 'success');
        await refreshStockLedger();
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
            await refreshStockLedger();
        } else {
            notify(`Sync warning: ${data.error || 'Server error'}`, 'warning');
        }
    } catch (e) {
        notify(`Sync error: ${e.message}`, 'error');
    } finally {
        isSyncingStock = false;
    }
}

// -----------------------------------------------------------------------------
// Stock Export Handlers (Direct Browser Download & Server Archival)
// -----------------------------------------------------------------------------

async function downloadCsvFile(url, fallbackFilename) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) {
            let errorMsg = `HTTP ${resp.status}`;
            try {
                const json = await resp.json();
                errorMsg = json.error || (json.errors && json.errors[0]?.message) || errorMsg;
            } catch (_) {
                const txt = await resp.text();
                if (txt && txt.length < 120) errorMsg = txt;
            }
            if (typeof notify === 'function') {
                notify(`Export failed: ${errorMsg}`, 'error');
            } else {
                alert(`Export failed: ${errorMsg}`);
            }
            return;
        }

        let filename = fallbackFilename || 'export.csv';
        const disposition = resp.headers.get('Content-Disposition');
        if (disposition && disposition.includes('filename=')) {
            const matches = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disposition);
            if (matches && matches[1]) {
                filename = matches[1].replace(/['"]/g, '').trim();
            }
        }

        const blob = await resp.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(blobUrl);
        document.body.removeChild(a);

        if (typeof notify === 'function') {
            notify(`📥 Downloaded ${filename}`, 'success');
        } else if (typeof showToast === 'function') {
            showToast(`📥 Downloaded ${filename}`, 'success');
        }
    } catch (err) {
        console.warn('Direct fetch download failed, falling back to direct navigation:', err);
        window.location.href = url;
    }
}

function toggleStockExportMenu(event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    const menu = document.getElementById('stock-export-dropdown');
    if (!menu) return;
    const isVisible = menu.style.display === 'block';
    menu.style.display = isVisible ? 'none' : 'block';

    if (!isVisible) {
        const closeMenu = (e) => {
            if (!menu.contains(e.target)) {
                menu.style.display = 'none';
                document.removeEventListener('click', closeMenu);
            }
        };
        setTimeout(() => document.addEventListener('click', closeMenu), 10);
    }
}

function exportDispatchCSV() {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/category/dispatched/?date=${encodeURIComponent(suf)}`, `shift_${suf}_dispatched.csv`);
}

function exportMovementsCSV() {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/category/movements/?date=${encodeURIComponent(suf)}`, `shift_${suf}_movements.csv`);
}

function exportAuditCSV() {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/category/audit/?date=${encodeURIComponent(suf)}`, `shift_${suf}_audit.csv`);
}

function exportUnregistered() {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/unregistered/?date_suffix=${encodeURIComponent(suf)}`, `unregistered_stocktake_${suf}.csv`);
}

function exportStockCSV() {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/csv/?date_suffix=${encodeURIComponent(suf)}`, `itms_stock_reconciliation_${suf}.csv`);
}

function exportCategoryFromStock(category) {
    const suf = currentStockDateSuffix || 'today';
    const menu = document.getElementById('stock-export-dropdown');
    if (menu) menu.style.display = 'none';
    downloadCsvFile(`/api/stock/export/category/${encodeURIComponent(category)}/?date=${encodeURIComponent(suf)}`, `shift_${suf}_${category}.csv`);
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
    const physCount = tot.physical_count !== undefined ? tot.physical_count : (recon.physical_count || 0);
    const variance = recon.variance !== undefined ? recon.variance : 0;
    const varText = variance > 0 ? `+${variance}` : `${variance}`;

    // 1. Populate Executive Stock KPI Counter Cards
    setTextSafe('kpi-val-opening', (tot.opening || 0).toLocaleString());
    setTextSafe('kpi-val-received', (tot.received || 0).toLocaleString());
    setTextSafe('kpi-val-dispatched', (tot.dispatched || 0).toLocaleString());
    setTextSafe('kpi-val-installed', (tot.installed || 0).toLocaleString());
    setTextSafe('kpi-val-closing', (tot.closing_stock || 0).toLocaleString());
    setTextSafe('kpi-val-physical', physCount.toLocaleString());
    setTextSafe('kpi-sub-physical', `Variance: ${varText}`);
    setTextSafe('kpi-val-unallocated', unallocCount.toLocaleString());

    // Contextual Category Breakdown Subtitles
    const rep = recon.report_table || {};
    const rows = rep.rows || [];
    const openRow = rows.find(r => (r.metric || '').toLowerCase().includes('opening')) || {};
    if (openRow.pmo !== undefined && openRow.psv !== undefined) {
        setTextSafe('kpi-sub-opening', `PMO: ${openRow.pmo} | PSV: ${openRow.psv}`);
    }
    const rcvRow = rows.find(r => (r.metric || '').toLowerCase().includes('received')) || {};
    if (rcvRow.pmo !== undefined && rcvRow.psv !== undefined) {
        setTextSafe('kpi-sub-received', `PMO: ${rcvRow.pmo} | PSV: ${rcvRow.psv}`);
    }
    const instRow = rows.find(r => (r.metric || '').toLowerCase().includes('installed')) || {};
    if (instRow.pmo !== undefined && instRow.psv !== undefined) {
        setTextSafe('kpi-sub-installed', `PMO: ${instRow.pmo} | PSV: ${instRow.psv}`);
    }

    // 2. Mirrored Legacy Strip for backward compatibility
    const stripHtml = `
        <span class="badge badge-muted">Opening: ${tot.opening || 0}</span>
        <span class="badge badge-muted">Received: ${tot.received || 0}</span>
        <span class="badge badge-muted">Dispatched: ${tot.dispatched || 0}</span>
        <span class="badge badge-muted">Installed: ${tot.installed || 0}</span>
        <span class="badge badge-cyan">Closing: ${tot.closing_stock || 0}</span>
        <span class="badge badge-yellow">Physical: ${physCount} (${varText})</span>
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
    const dd = Number.parseInt(suffix.substring(0, 2), 10);
    const mm = Number.parseInt(suffix.substring(2, 4), 10) - 1;
    const yy = Number.parseInt(suffix.substring(4, 6), 10);
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
        await refreshStockLedger();
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
    if (typeof setGlobalShiftDate === 'function' && currentStockDateSuffix) {
        void setGlobalShiftDate(currentStockDateSuffix);
    } else {
        await refreshStockLedger();
    }
}

function stockPrevDay() {
    void navigateStockDay('prev');
}

function stockNextDay() {
    void navigateStockDay('next');
}

function stockToday() {
    if (typeof setGlobalShiftDate === 'function') {
        void setGlobalShiftDate('today');
    } else {
        currentStockDateSuffix = '';
        void refreshStockLedger();
    }
}

// React immediately to global shift date changes
window.addEventListener('shiftDateChanged', (e) => {
    const detail = e.detail;
    if (detail && detail.suffix && detail.suffix !== currentStockDateSuffix) {
        currentStockDateSuffix = detail.suffix;
        void refreshStockLedger();
    }
});


// -----------------------------------------------------------------------------
// Quick Count Inbound Delivery (Bulk intake by quantity)
// -----------------------------------------------------------------------------

function openQuickDeliveryModal() {
    const qtyInput = document.getElementById('quick-delivery-quantity');
    if (qtyInput) qtyInput.value = '';

    const catSelect = document.getElementById('quick-delivery-category');
    if (catSelect) {
        catSelect.value = (typeof currentStockCategory !== 'undefined' && currentStockCategory) ? currentStockCategory : 'PSV';
    }

    const dateInput = document.getElementById('quick-delivery-date');
    if (dateInput) {
        dateInput.value = currentStockDateSuffix || '';
    }

    const refInput = document.getElementById('quick-delivery-ref');
    if (refInput) refInput.value = '';

    const notesInput = document.getElementById('quick-delivery-notes');
    if (notesInput) notesInput.value = '';

    const autoKitsChk = document.getElementById('quick-delivery-autokits');
    if (autoKitsChk) autoKitsChk.checked = true;

    if (typeof openModal === 'function') {
        openModal('modal-quick-delivery');
    } else {
        const m = document.getElementById('modal-quick-delivery');
        if (m) m.classList.add('active');
    }

    if (qtyInput) {
        setTimeout(() => qtyInput.focus(), 120);
    }
}

function closeQuickDeliveryModal() {
    if (typeof closeModal === 'function') {
        closeModal('modal-quick-delivery');
    } else {
        const m = document.getElementById('modal-quick-delivery');
        if (m) m.classList.remove('active');
    }
}

async function submitQuickDelivery() {
    const qtyInput = document.getElementById('quick-delivery-quantity');
    const catSelect = document.getElementById('quick-delivery-category');
    const supInput = document.getElementById('quick-delivery-supplier');
    const refInput = document.getElementById('quick-delivery-ref');
    const dateInput = document.getElementById('quick-delivery-date');
    const notesInput = document.getElementById('quick-delivery-notes');
    const autoKitsChk = document.getElementById('quick-delivery-autokits');
    const submitBtn = document.getElementById('btn-submit-quick-delivery');

    const quantity = parseInt(qtyInput ? qtyInput.value : '0', 10);
    if (!quantity || quantity <= 0) {
        notify('Please enter a valid quantity of plates to deliver.', 'warning');
        if (qtyInput) qtyInput.focus();
        return;
    }

    const payload = {
        quantity: quantity,
        plate_category: catSelect ? catSelect.value : 'PSV',
        supplier: (supInput && supInput.value.trim()) ? supInput.value.trim() : 'Factory / Central Depot',
        paper_note_reference: refInput ? refInput.value.trim() : '',
        target_date_suffix: (dateInput && dateInput.value.trim()) ? dateInput.value.trim() : (currentStockDateSuffix || ''),
        notes: notesInput ? notesInput.value.trim() : '',
        auto_create_kits: autoKitsChk ? autoKitsChk.checked : true,
    };

    try {
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerText = 'Ingesting Delivery...';
        }

        const res = await fetch('/api/stock/delivery/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success) {
            notify(`📦 Inbound delivery of ${data.plates_count || quantity} ${payload.plate_category} plates recorded!`, 'success');
            const feedback = document.getElementById('stock-feedback-alert');
            if (feedback) {
                feedback.className = 'alert alert-success';
                feedback.innerHTML = `📦 INBOUND DELIVERY SAVED: ${data.plates_count || quantity} ${payload.plate_category} plates recorded under ${data.delivery_number || 'DN'}.`;
            }
            closeQuickDeliveryModal();
            await refreshStockLedger();
        } else {
            notify(`Delivery recording failed: ${data.error || 'Server error'}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerText = '📦 Ingest Inbound Delivery';
        }
    }
}

// -----------------------------------------------------------------------------
// Daily Stock Ledger Edit & Overrides (9-Row Full Metric Editor)
// -----------------------------------------------------------------------------

function openEditLedgerModal() {
    const dateInput = document.getElementById('edit-ledger-date');
    if (dateInput) {
        dateInput.value = currentStockDateSuffix || '';
    }

    prefillEditLedgerFromCurrent();

    if (typeof openModal === 'function') {
        openModal('modal-edit-ledger');
    } else {
        const m = document.getElementById('modal-edit-ledger');
        if (m) m.classList.add('active');
    }
}

function closeEditLedgerModal() {
    if (typeof closeModal === 'function') {
        closeModal('modal-edit-ledger');
    } else {
        const m = document.getElementById('modal-edit-ledger');
        if (m) m.classList.remove('active');
    }
}

function prefillEditLedgerFromCurrent() {
    if (!lastReconData) {
        calcEditLedgerTotals();
        return;
    }

    const rep = lastReconData.report_table || {};
    const rows = rep.rows || [];

    function findRow(name) {
        return rows.find(r => (r.metric || '').toLowerCase().includes(name.toLowerCase())) || {};
    }

    const openRow = findRow('Opening Balance');
    const rcvRow = findRow('Kits Received') || findRow('Received');
    const schedRow = findRow('Target') || findRow('Scheduled');
    const instRow = findRow('Installed');
    const trinRow = findRow('Transfer In');
    const troutRow = findRow('Transfer Out');
    const physRow = findRow('Physical');

    function setField(id, val) {
        const el = document.getElementById(id);
        if (el) el.value = (val !== undefined && val !== null) ? val : '';
    }

    setField('edit-ledger-open-pmo', openRow.pmo);
    setField('edit-ledger-open-psv', openRow.psv);

    setField('edit-ledger-recv-pmo', rcvRow.pmo);
    setField('edit-ledger-recv-psv', rcvRow.psv);

    setField('edit-ledger-sched-pmo', schedRow.pmo);
    setField('edit-ledger-sched-psv', schedRow.psv);
    setField('edit-ledger-sched-total', schedRow.total);

    setField('edit-ledger-inst-pmo', instRow.pmo);
    setField('edit-ledger-inst-psv', instRow.psv);

    setField('edit-ledger-trin-pmo', trinRow.pmo);
    setField('edit-ledger-trin-psv', trinRow.psv);

    setField('edit-ledger-trout-pmo', troutRow.pmo);
    setField('edit-ledger-trout-psv', troutRow.psv);

    setField('edit-ledger-physical', physRow.total !== undefined ? physRow.total : (lastReconData.total ? lastReconData.total.physical_count : ''));

    const notesEl = document.getElementById('edit-ledger-notes');
    if (notesEl && lastReconData.notes) {
        notesEl.value = lastReconData.notes;
    }

    calcEditLedgerTotals();
}

function calcEditLedgerTotals() {
    function getVal(id) {
        const el = document.getElementById(id);
        if (!el || el.value === '') return 0;
        const n = parseInt(el.value, 10);
        return isNaN(n) ? 0 : n;
    }

    function setTotal(id, val) {
        const el = document.getElementById(id);
        if (el) el.innerText = (val || 0).toLocaleString();
    }

    const openPmo = getVal('edit-ledger-open-pmo');
    const openPsv = getVal('edit-ledger-open-psv');
    setTotal('edit-ledger-open-total', openPmo + openPsv);

    const rcvPmo = getVal('edit-ledger-recv-pmo');
    const rcvPsv = getVal('edit-ledger-recv-psv');
    setTotal('edit-ledger-recv-total', rcvPmo + rcvPsv);

    const schedPmo = getVal('edit-ledger-sched-pmo');
    const schedPsv = getVal('edit-ledger-sched-psv');
    const schedTotEl = document.getElementById('edit-ledger-sched-total');
    if (schedTotEl && !schedTotEl.value && (schedPmo > 0 || schedPsv > 0)) {
        schedTotEl.value = schedPmo + schedPsv;
    }

    const instPmo = getVal('edit-ledger-inst-pmo');
    const instPsv = getVal('edit-ledger-inst-psv');
    setTotal('edit-ledger-inst-total', instPmo + instPsv);

    const trinPmo = getVal('edit-ledger-trin-pmo');
    const trinPsv = getVal('edit-ledger-trin-psv');
    setTotal('edit-ledger-trin-total', trinPmo + trinPsv);

    const troutPmo = getVal('edit-ledger-trout-pmo');
    const troutPsv = getVal('edit-ledger-trout-psv');
    setTotal('edit-ledger-trout-total', troutPmo + troutPsv);
}

async function submitEditLedger() {
    const submitBtn = document.getElementById('btn-submit-edit-ledger');
    const dateInput = document.getElementById('edit-ledger-date');
    const bondSelect = document.getElementById('edit-ledger-bond');

    function optInt(id) {
        const el = document.getElementById(id);
        if (!el || el.value.trim() === '') return null;
        const n = parseInt(el.value, 10);
        return isNaN(n) ? null : n;
    }

    const payload = {
        target_date_suffix: (dateInput && dateInput.value.trim()) ? dateInput.value.trim() : (currentStockDateSuffix || ''),
        storage_bond_name: (bondSelect && bondSelect.value) ? bondSelect.value : 'AGM / SPIRO',
        opening_balance_pmo: optInt('edit-ledger-open-pmo'),
        opening_balance_psv: optInt('edit-ledger-open-psv'),
        kits_received_pmo: optInt('edit-ledger-recv-pmo'),
        kits_received_psv: optInt('edit-ledger-recv-psv'),
        scheduled_pmo: optInt('edit-ledger-sched-pmo'),
        scheduled_psv: optInt('edit-ledger-sched-psv'),
        scheduled_total: optInt('edit-ledger-sched-total'),
        kits_installed_pmo: optInt('edit-ledger-inst-pmo'),
        kits_installed_psv: optInt('edit-ledger-inst-psv'),
        bond_transfer_in_pmo: optInt('edit-ledger-trin-pmo'),
        bond_transfer_in_psv: optInt('edit-ledger-trin-psv'),
        bond_transfer_out_pmo: optInt('edit-ledger-trout-pmo'),
        bond_transfer_out_psv: optInt('edit-ledger-trout-psv'),
        physical_count: optInt('edit-ledger-physical'),
        notes: (document.getElementById('edit-ledger-notes') || {}).value || '',
    };

    try {
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerText = 'Saving Overrides...';
        }

        const res = await fetch('/api/stock/ledger/entry/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success) {
            notify('✏️ Daily stock ledger overrides saved successfully!', 'success');
            closeEditLedgerModal();
            await refreshStockLedger();
        } else {
            notify(`Failed to save ledger overrides: ${data.error || 'Server error'}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerText = '💾 Save 9-Row Ledger Overrides';
        }
    }
}

// -----------------------------------------------------------------------------
// Email Stock Reconciliation Report Modal
// -----------------------------------------------------------------------------

function openEmailStockReportModal() {
    const dateInput = document.getElementById('email-stock-date');
    if (dateInput) {
        dateInput.value = currentStockDateSuffix || '';
    }

    const recipInput = document.getElementById('email-stock-recipients');
    if (recipInput) recipInput.value = '';

    const notesInput = document.getElementById('email-stock-notes');
    if (notesInput) notesInput.value = '';

    const chkOp = document.getElementById('email-stock-chk-operator');
    if (chkOp) chkOp.checked = true;

    const chkItms = document.getElementById('email-stock-chk-itms');
    if (chkItms) chkItms.checked = true;

    if (typeof openModal === 'function') {
        openModal('modal-email-stock-report');
    } else {
        const m = document.getElementById('modal-email-stock-report');
        if (m) m.classList.add('active');
    }
}

function closeEmailStockReportModal() {
    if (typeof closeModal === 'function') {
        closeModal('modal-email-stock-report');
    } else {
        const m = document.getElementById('modal-email-stock-report');
        if (m) m.classList.remove('active');
    }
}

async function submitEmailStockReport() {
    const submitBtn = document.getElementById('btn-submit-email-stock');
    const dateInput = document.getElementById('email-stock-date');
    const recipInput = document.getElementById('email-stock-recipients');
    const notesInput = document.getElementById('email-stock-notes');
    const chkOp = document.getElementById('email-stock-chk-operator');
    const chkItms = document.getElementById('email-stock-chk-itms');

    const rawRecipients = recipInput ? recipInput.value.trim() : '';
    const recipientsList = rawRecipients
        ? rawRecipients.split(/[,;]+/).map(r => r.trim()).filter(r => r.includes('@'))
        : [];

    const payload = {
        target_date_suffix: (dateInput && dateInput.value.trim()) ? dateInput.value.trim() : (currentStockDateSuffix || ''),
        recipients: recipientsList,
        include_operator_email: chkOp ? chkOp.checked : true,
        include_itms_email: chkItms ? chkItms.checked : true,
        notes: notesInput ? notesInput.value.trim() : '',
    };

    try {
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerText = 'Dispatching Email...';
        }

        const res = await fetch('/api/stock/report/email/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success) {
            const count = (data.recipients && data.recipients.length) ? data.recipients.length : 1;
            notify(`📧 Stock reconciliation report emailed to ${count} recipient(s)!`, 'success');
            closeEmailStockReportModal();
        } else {
            notify(`Failed to send report email: ${data.error || 'Server error'}`, 'error');
        }
    } catch (e) {
        notify(`Network error: ${e.message}`, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerText = '📤 Send Report Email';
        }
    }
}


// -----------------------------------------------------------------------------
// Clipboard & Inspection Helpers
// -----------------------------------------------------------------------------

function copyCurrentInspectorPlate() {
    const el = document.getElementById('ins-plate');
    if (el && el.innerText && el.innerText !== 'SCAN PLATE') {
        copyTextToClipboard(el.innerText.trim(), 'Plate Number');
    }
}

function copyTextToClipboard(text, label = 'Text') {
    if (!text || text === '-' || text === '---') return;
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            if (typeof notify === 'function') notify(`📋 ${label} copied to clipboard!`, 'info');
        }).catch(() => {
            fallbackCopy(text, label);
        });
    } else {
        fallbackCopy(text, label);
    }
}

function fallbackCopy(text, label) {
    try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        if (typeof notify === 'function') notify(`📋 ${label} copied to clipboard!`, 'info');
    } catch (e) {
        console.error('Copy fallback failed:', e);
    }
}
