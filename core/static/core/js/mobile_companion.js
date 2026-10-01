/**
 * ITMS Mobile Companion Engine
 * Handles IndexedDB offline caching, camera capture, signal sensing, and background sync.
 */

function getOrCreateDeviceId() {
    let id = localStorage.getItem("itms_mobile_device_id");
    if (!id) {
        id = "DEV-" + Date.now().toString(36).toUpperCase() + "-" + Math.random().toString(36).substring(2, 6).toUpperCase();
        localStorage.setItem("itms_mobile_device_id", id);
    }
    return id;
}

function getDeviceName() {
    const ua = navigator.userAgent;
    let os = "Smartphone";
    if (/Android/i.test(ua)) os = "Android";
    else if (/iPhone|iPad/i.test(ua)) os = "iPhone";
    else if (/Windows/i.test(ua)) os = "Windows Device";

    let browser = "Browser";
    if (/Chrome/i.test(ua) && !/Edg/i.test(ua)) browser = "Chrome";
    else if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) browser = "Safari";
    else if (/Firefox/i.test(ua)) browser = "Firefox";

    return `${os} (${browser})`;
}

// ============================================================================
// State Management
// ============================================================================
const MobileState = {
    mode: "conveyor", // 'conveyor' | 'uturn'
    isOnline: false,
    pingLatency: 0,
    heartbeatTimer: null,
    syncInProgress: false,

    // Device identity & server load protection
    deviceId: getOrCreateDeviceId(),
    deviceName: getDeviceName(),
    isBlockedByLimit: false,
    activeQualityReport: null,
    qualityOrientation: null,
    inspectedOrientation: null,

    // Conveyor State (1-by-1 Bike Inspection)
    conveyor: {
        sequence: 1,
        bikeId: "BIKE-" + Date.now().toString(36).toUpperCase(),
        frontBlob: null,
        frontUrl: null,
        rearBlob: null,
        rearUrl: null,
        isComplete: false,
    },

    // U-Turn Walk State
    uturn: {
        phase: "REAR", // 'REAR' | 'FRONT'
        rearCount: 0,
        frontCount: 0,
        totalPhotos: 0,
        recentTiles: [],
    },

    // Stock & Inventory Scanner State
    stock: {
        subMode: "DISPATCH", // 'DISPATCH' | 'DELIVERY' | 'RETURN'
        category: "PSV",     // 'PSV' | 'PMO'
        queue: [],
        cameraStream: null,
        cameraFacing: "environment",
        isScanning: false,
        lastScannedCode: "",
        lastScannedTime: 0,
        torchActive: false,
        scanLoopId: null,
        barcodeDetector: null,
    }
};

// ============================================================================
// IndexedDB Local Storage (Zero Data Loss Outbox)
// ============================================================================
const DB_NAME = "ITMS_Mobile_DB";
const DB_VERSION = 1;
const STORE_NAME = "outbox_queue";

function openOutboxDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
                store.createIndex("status", "status", { unique: false });
                store.createIndex("captured_at", "captured_at", { unique: false });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

async function savePhotoToOutbox(item) {
    try {
        const db = await openOutboxDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, "readwrite");
            const store = tx.objectStore(STORE_NAME);
            const req = store.put(item);
            req.onsuccess = () => resolve(item);
            req.onerror = () => reject(req.error);
        });
    } catch (err) {
        console.error("IndexedDB write error:", err);
        return null;
    }
}

async function getPendingOutboxItems() {
    try {
        const db = await openOutboxDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, "readonly");
            const store = tx.objectStore(STORE_NAME);
            const req = store.getAll();
            req.onsuccess = () => {
                const items = req.result || [];
                const pending = items.filter(i => i.status === "pending" || i.status === "failed");
                resolve(pending);
            };
            req.onerror = () => reject(req.error);
        });
    } catch (err) {
        return [];
    }
}

async function markOutboxItemSynced(id) {
    try {
        const db = await openOutboxDB();
        return new Promise((resolve) => {
            const tx = db.transaction(STORE_NAME, "readwrite");
            const store = tx.objectStore(STORE_NAME);
            store.delete(id);
            tx.oncomplete = () => resolve(true);
        });
    } catch (err) {
        console.error("IndexedDB delete error:", err);
    }
}

async function refreshOutboxCount() {
    const pending = await getPendingOutboxItems();
    const count = pending.length;
    const countElem = document.getElementById("outbox-count-text");
    const iconElem = document.getElementById("outbox-icon");

    if (countElem) {
        if (count === 0) {
            countElem.textContent = "Outbox: 0 Pending (Synced ✓)";
            if (iconElem) iconElem.textContent = "✓";
        } else {
            countElem.textContent = `Outbox: ${count} Pending (${MobileState.isOnline ? "Syncing..." : "Saved on phone"})`;
            if (iconElem) iconElem.textContent = "📦";
        }
    }

    renderOutboxDrawerItems(pending);
}

function renderOutboxDrawerItems(items) {
    const listElem = document.getElementById("outbox-items-list");
    if (!listElem) return;

    if (items.length === 0) {
        listElem.innerHTML = '<div class="empty-outbox-message">No pending photos. All evidence synced to laptop!</div>';
        return;
    }

    listElem.innerHTML = items.map(item => `
        <div class="outbox-item-row">
            <div>
                <strong>${item.orientation}</strong> &bull; ${item.mode === "conveyor" ? `Bike #${item.sequence_number}` : `U-Turn #${item.sequence_number}`}
                <span style="font-size:0.68rem; color:var(--ug-text-dim); display:block;">${new Date(item.captured_at).toLocaleTimeString()}</span>
            </div>
            <span class="badge ${item.status === 'uploading' ? 'badge-yellow' : 'badge-secondary'}">
                ${item.status.toUpperCase()}
            </span>
        </div>
    `).join("");
}

// ============================================================================
// Network Heartbeat & Signal Sensing (Device Session Limits Protected)
// ============================================================================
async function pingServer() {
    const start = performance.now();
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2500);

        const url = `/api/mobile/ping/?device_id=${encodeURIComponent(MobileState.deviceId)}&device_name=${encodeURIComponent(MobileState.deviceName)}&mode=${encodeURIComponent(MobileState.mode)}`;
        const resp = await fetch(url, {
            method: "GET",
            cache: "no-store",
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        if (resp.status === 429) {
            // Server capacity limit reached
            const limitData = await resp.json();
            showDeviceLimitModal(limitData);
            setOnlineStatus(false, 0);
            return;
        }

        if (resp.ok) {
            hideDeviceLimitModal();
            const latency = Math.round(performance.now() - start);
            const data = await resp.json();
            setOnlineStatus(true, latency);
            if (data.batch_label || data.batch_photos !== undefined) {
                updateBatchCapacityUI(data);
            }
        } else {
            setOnlineStatus(false, 0);
        }
    } catch (err) {
        setOnlineStatus(false, 0);
    }
}

function updateBatchCapacityUI(batchData) {
    if (!batchData) return;
    const labelElem = document.getElementById("mobile-batch-label");
    const pillElem = document.getElementById("mobile-batch-limit-pill");
    const fillElem = document.getElementById("mobile-batch-progress-fill");
    const bannerElem = document.getElementById("batch-rollover-banner");

    const count = batchData.batch_photos !== undefined ? batchData.batch_photos : (batchData.total_photos || 0);
    const limit = batchData.batch_limit || batchData.batch_max_photos || 200;
    const label = batchData.batch_label || "Active Batch";
    const pairs = Math.floor(count / 2);
    const maxPairs = Math.floor(limit / 2);

    if (labelElem) labelElem.textContent = `📦 ${label}`;
    if (pillElem) pillElem.textContent = `${count}/${limit} Photos (${pairs}/${maxPairs} Bikes)`;

    if (fillElem) {
        const pct = Math.min(100, Math.round((count / limit) * 100));
        fillElem.style.width = `${pct}%`;
        if (pct >= 100) {
            fillElem.classList.add("full");
        } else {
            fillElem.classList.remove("full");
        }
    }

    if (bannerElem) {
        if (batchData.batch_is_full || count >= limit) {
            bannerElem.style.display = "flex";
        } else {
            bannerElem.style.display = "none";
        }
    }
}

function showDeviceLimitModal(data) {
    MobileState.isBlockedByLimit = true;
    const modal = document.getElementById("device-limit-modal");
    if (!modal) return;

    const maxCount = document.getElementById("limit-max-count");
    const infoElem = document.getElementById("active-device-info");

    if (maxCount && data.max_allowed) maxCount.textContent = data.max_allowed;
    if (infoElem && data.active_devices && data.active_devices.length > 0) {
        infoElem.innerHTML = data.active_devices.map(d => 
            `<strong>${d.device_name}</strong> (${d.client_ip || "LAN"}) &bull; Mode: ${d.mode}`
        ).join("<br>");
    } else if (infoElem) {
        infoElem.textContent = "Another technician phone is currently connected.";
    }

    modal.style.display = "flex";
}

function hideDeviceLimitModal() {
    MobileState.isBlockedByLimit = false;
    const modal = document.getElementById("device-limit-modal");
    if (modal) modal.style.display = "none";
}

function retryDeviceConnection() {
    pingServer();
}

async function disconnectDeviceSession() {
    if (!confirm("Disconnect this phone from the ITMS verification station? This releases the connection slot for another operator.")) return;

    try {
        await fetch("/api/mobile/disconnect/", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `device_id=${encodeURIComponent(MobileState.deviceId)}`
        });
    } catch (e) {}

    alert("Device disconnected. You can close this tab or tap 'Check Availability' to reconnect.");
    location.reload();
}

function setOnlineStatus(online, latency) {
    const wasOffline = !MobileState.isOnline && online;
    MobileState.isOnline = online;
    MobileState.pingLatency = latency;

    const pill = document.getElementById("signal-pill");
    const text = document.getElementById("signal-text");
    const banner = document.getElementById("network-alert-banner");

    if (pill && text) {
        pill.classList.remove("online", "weak", "offline");
        if (online) {
            if (latency < 100) {
                pill.classList.add("online");
                text.textContent = `Online (${latency}ms)`;
            } else {
                pill.classList.add("weak");
                text.textContent = `Weak (${latency}ms)`;
            }
            if (banner) banner.style.display = "none";
        } else {
            pill.classList.add("offline");
            text.textContent = MobileState.isBlockedByLimit ? "Station Busy 🔒" : "Offline (Out of Range)";
            if (banner && !MobileState.isBlockedByLimit) banner.style.display = "flex";
        }
    }

    // Trigger auto-drain if transitioning back into Wi-Fi range
    if (wasOffline || (online && !MobileState.syncInProgress)) {
        drainOutboxQueue();
    }
}

function startHeartbeat() {
    pingServer();
    if (MobileState.heartbeatTimer) clearInterval(MobileState.heartbeatTimer);
    MobileState.heartbeatTimer = setInterval(pingServer, 3500);
}

// ============================================================================
// Outbox Synchronization Worker
// ============================================================================
async function drainOutboxQueue() {
    if (MobileState.syncInProgress || !MobileState.isOnline || MobileState.isBlockedByLimit) return;

    const pending = await getPendingOutboxItems();
    if (pending.length === 0) return;

    MobileState.syncInProgress = true;
    const btnSync = document.getElementById("btn-sync-now");
    if (btnSync) btnSync.textContent = "Syncing... ⏳";

    for (const item of pending) {
        if (!MobileState.isOnline || MobileState.isBlockedByLimit) break;

        const formData = new FormData();
        formData.append("photo", item.blob, item.filename);
        formData.append("mode", item.mode);
        formData.append("orientation", item.orientation);
        formData.append("bike_client_id", item.bike_client_id || "");
        formData.append("sequence_number", item.sequence_number || "1");
        formData.append("captured_at", item.captured_at);
        formData.append("device_id", MobileState.deviceId);
        formData.append("device_name", MobileState.deviceName);

        try {
            const resp = await fetch("/api/mobile/upload/", {
                method: "POST",
                body: formData
            });

            if (resp.status === 429) {
                const limitData = await resp.json();
                showDeviceLimitModal(limitData);
                break;
            }

            if (resp.ok) {
                const data = await resp.json();
                if (data.success) {
                    await markOutboxItemSynced(item.id);
                    await refreshOutboxCount();

                    // Update live batch capacity bar
                    if (data.batch_label || data.batch_photos !== undefined) {
                        updateBatchCapacityUI(data);
                    }

                    // If photo quality has blur or exposure defects, show correction advice
                    if (data.quality_report && data.quality_report.has_issues) {
                        showQualityWarning(data.quality_report, item.orientation);
                    }
                }
            } else {
                // Stop drain on server error to avoid rapid retries
                break;
            }
        } catch (uploadErr) {
            console.warn("Upload connection dropped:", uploadErr);
            setOnlineStatus(false, 0);
            break;
        }
    }

    MobileState.syncInProgress = false;
    if (btnSync) btnSync.textContent = "Sync Now 🔄";
    await refreshOutboxCount();
}

function manualSyncOutbox(e) {
    if (e) e.stopPropagation();
    pingServer().then(() => drainOutboxQueue());
}

// ============================================================================
// Mode Switching (Conveyor vs U-Turn vs Stock)
// ============================================================================
function switchMobileMode(mode) {
    if (mode !== "stock" && MobileState.stock && MobileState.stock.isScanning) {
        stopStockCameraScanner();
    }
    MobileState.mode = mode;

    document.querySelectorAll(".mode-btn").forEach(btn => btn.classList.remove("active"));
    document.querySelectorAll(".mode-container").forEach(c => {
        c.classList.remove("active");
        c.style.display = "none";
    });

    if (mode === "conveyor") {
        const btn = document.getElementById("btn-mode-conveyor");
        if (btn) btn.classList.add("active");
        const cont = document.getElementById("container-conveyor");
        if (cont) {
            cont.classList.add("active");
            cont.style.display = "block";
        }
    } else if (mode === "uturn") {
        const btn = document.getElementById("btn-mode-uturn");
        if (btn) btn.classList.add("active");
        const cont = document.getElementById("container-uturn");
        if (cont) {
            cont.classList.add("active");
            cont.style.display = "block";
        }
    } else if (mode === "stock") {
        const btn = document.getElementById("btn-mode-stock");
        if (btn) btn.classList.add("active");
        const cont = document.getElementById("container-stock");
        if (cont) {
            cont.classList.add("active");
            cont.style.display = "block";
        }
        renderStockQueueList();
    }
}

// ============================================================================
// MODE 3: Stock QR & Barcode Scanner Actions
// ============================================================================
function setStockSubMode(subMode) {
    MobileState.stock.subMode = subMode;
    document.getElementById("pill-stock-dispatch").classList.toggle("active", subMode === "DISPATCH");
    document.getElementById("pill-stock-delivery").classList.toggle("active", subMode === "DELIVERY");
    document.getElementById("pill-stock-return").classList.toggle("active", subMode === "RETURN");

    const titleMap = {
        "DISPATCH": "Dispatch to Line (Line Out)",
        "DELIVERY": "Inbound Shipment Delivery",
        "RETURN": "Uninstalled Plates Return",
    };
    document.getElementById("stock-target-title").textContent = titleMap[subMode] || "Plate Intake";
}

function setStockCategory(cat) {
    MobileState.stock.category = cat;
    const btnPsv = document.getElementById("btn-cat-psv");
    const btnPmo = document.getElementById("btn-cat-pmo");
    if (cat === "PSV") {
        btnPsv.style.background = "#0284c7";
        btnPsv.style.color = "#ffffff";
        btnPmo.style.background = "#374151";
        btnPmo.style.color = "#cbd5e1";
    } else {
        btnPmo.style.background = "#eab308";
        btnPmo.style.color = "#000000";
        btnPsv.style.background = "#374151";
        btnPsv.style.color = "#cbd5e1";
    }
}

// ============================================================================
// MODE 3: Stock Live Camera QR & Barcode Scanner Engine
// ============================================================================

function extractPlateFromScannedCode(rawCode) {
    if (!rawCode) return "";
    let str = rawCode.trim();

    // 1. If it's a URL, extract search query params or URL path segment
    if (str.startsWith("http://") || str.startsWith("https://")) {
        try {
            const url = new URL(str);
            const codeParam = url.searchParams.get("code") || url.searchParams.get("kit") || url.searchParams.get("plate");
            if (codeParam) str = codeParam;
            else {
                const parts = url.pathname.split("/").filter(Boolean);
                if (parts.length > 0) str = parts[parts.length - 1];
            }
        } catch (e) {}
    }

    // 2. Remove common system prefixes (e.g. "IK-", "KIT:", "PLATE:", "REG:")
    str = str.replace(/^(KIT:|PLATE:|REG:|IK-)/i, "");

    // 3. Match Ugandan vehicle registration format (e.g. UMA 711PW, UMA711PW, UFX 123A)
    const match = str.match(/([A-Z]{3})\s*([0-9]{3,4})\s*([A-Z]{1,2})/i) || str.match(/([A-Z]{2,3})[0-9]{3,4}[A-Z]{1,2}/i);
    if (match) {
        return match[0].replace(/\s+/g, "").toUpperCase();
    }

    // 4. Fallback: sanitize alphanumeric string
    return str.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

function playScanBeep(success = true) {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);

        if (success) {
            osc.type = "sine";
            osc.frequency.setValueAtTime(880, audioCtx.currentTime);
            osc.frequency.setValueAtTime(1320, audioCtx.currentTime + 0.08);
            gain.gain.setValueAtTime(0.18, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.22);
            osc.start(audioCtx.currentTime);
            osc.stop(audioCtx.currentTime + 0.22);
        } else {
            osc.type = "sawtooth";
            osc.frequency.setValueAtTime(240, audioCtx.currentTime);
            gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.28);
            osc.start(audioCtx.currentTime);
            osc.stop(audioCtx.currentTime + 0.28);
        }
    } catch (e) {
        // AudioContext may be restricted before user gesture
    }
}

async function toggleStockCameraScanner() {
    if (MobileState.stock.isScanning) {
        stopStockCameraScanner();
    } else {
        await startStockCameraScanner();
    }
}

async function startStockCameraScanner() {
    const video = document.getElementById("stock-scanner-video");
    const viewport = document.getElementById("stock-camera-viewport");
    const btnToggle = document.getElementById("btn-toggle-stock-camera");
    const btnTorch = document.getElementById("btn-stock-torch");
    const btnFlip = document.getElementById("btn-stock-flip");

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("Camera access is not supported by your browser or requires a secure (HTTPS) connection.");
        return;
    }

    try {
        if (btnToggle) {
            btnToggle.innerHTML = "<span>⏳ Accessing camera...</span>";
            btnToggle.disabled = true;
        }

        const constraints = {
            video: {
                facingMode: { ideal: MobileState.stock.cameraFacing },
                width: { ideal: 1280 },
                height: { ideal: 720 },
            },
            audio: false,
        };

        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        MobileState.stock.cameraStream = stream;
        MobileState.stock.isScanning = true;

        if (video) {
            video.srcObject = stream;
            await video.play();
        }

        if (viewport) {
            viewport.style.display = "flex";
        }

        // Check torch capability
        const track = stream.getVideoTracks()[0];
        if (track && track.getCapabilities && track.getCapabilities().torch) {
            if (btnTorch) btnTorch.style.display = "inline-block";
        } else {
            if (btnTorch) btnTorch.style.display = "none";
        }
        if (btnFlip) btnFlip.style.display = "inline-block";

        if (btnToggle) {
            btnToggle.disabled = false;
            btnToggle.style.background = "#dc2626";
            btnToggle.innerHTML = "<span>⏹ Stop Camera Scanner</span>";
        }

        // Initialize BarcodeDetector if available
        if ("BarcodeDetector" in window && !MobileState.stock.barcodeDetector) {
            try {
                MobileState.stock.barcodeDetector = new BarcodeDetector({
                    formats: ["qr_code", "code_128", "code_39", "ean_13", "data_matrix", "upc_a"],
                });
            } catch (e) {
                console.warn("BarcodeDetector initialization failed, using canvas fallback:", e);
            }
        }

        startStockScanLoop();
        showMobileToast("📷 Camera scanner active. Point at plate QR or barcode.");
    } catch (err) {
        console.error("Camera access error:", err);
        alert(`Cannot access camera: ${err.message || err.name}. Please ensure camera permission is granted.`);
        if (btnToggle) {
            btnToggle.disabled = false;
            btnToggle.style.background = "";
            btnToggle.innerHTML = "<span>📷 Start Camera QR Scanner</span>";
        }
        MobileState.stock.isScanning = false;
    }
}

function stopStockCameraScanner() {
    if (MobileState.stock.scanLoopId) {
        cancelAnimationFrame(MobileState.stock.scanLoopId);
        MobileState.stock.scanLoopId = null;
    }

    if (MobileState.stock.cameraStream) {
        MobileState.stock.cameraStream.getTracks().forEach(track => track.stop());
        MobileState.stock.cameraStream = null;
    }

    MobileState.stock.isScanning = false;
    MobileState.stock.torchActive = false;

    const viewport = document.getElementById("stock-camera-viewport");
    const video = document.getElementById("stock-scanner-video");
    const btnToggle = document.getElementById("btn-toggle-stock-camera");
    const btnTorch = document.getElementById("btn-stock-torch");
    const btnFlip = document.getElementById("btn-stock-flip");

    if (video) video.srcObject = null;
    if (viewport) {
        viewport.style.display = "none";
        viewport.classList.remove("detected");
    }
    if (btnTorch) {
        btnTorch.style.display = "none";
        btnTorch.style.background = "#30363d";
    }
    if (btnFlip) btnFlip.style.display = "none";

    if (btnToggle) {
        btnToggle.disabled = false;
        btnToggle.style.background = "";
        btnToggle.innerHTML = "<span>📷 Start Camera QR Scanner</span>";
    }
}

function toggleStockTorch() {
    if (!MobileState.stock.cameraStream) return;
    const track = MobileState.stock.cameraStream.getVideoTracks()[0];
    if (!track || !track.applyConstraints) return;

    MobileState.stock.torchActive = !MobileState.stock.torchActive;
    track.applyConstraints({
        advanced: [{ torch: MobileState.stock.torchActive }]
    }).then(() => {
        const btnTorch = document.getElementById("btn-stock-torch");
        if (btnTorch) {
            btnTorch.style.background = MobileState.stock.torchActive ? "#eab308" : "#30363d";
            btnTorch.style.color = MobileState.stock.torchActive ? "#000000" : "#e6edf3";
        }
    }).catch(err => {
        console.warn("Torch error:", err);
    });
}

function flipStockCamera() {
    MobileState.stock.cameraFacing = (MobileState.stock.cameraFacing === "environment") ? "user" : "environment";
    stopStockCameraScanner();
    startStockCameraScanner();
}

function startStockScanLoop() {
    const video = document.getElementById("stock-scanner-video");
    const canvas = document.getElementById("stock-scanner-canvas");
    if (!video) return;

    let isDetecting = false;

    async function tick() {
        if (!MobileState.stock.isScanning) return;

        if (video.readyState === video.HAVE_ENOUGH_DATA && !isDetecting) {
            isDetecting = true;
            try {
                if (MobileState.stock.barcodeDetector) {
                    const barcodes = await MobileState.stock.barcodeDetector.detect(video);
                    if (barcodes && barcodes.length > 0) {
                        const raw = barcodes[0].rawValue;
                        if (raw) {
                            handleStockBarcodeDetected(raw);
                        }
                    }
                } else if (canvas) {
                    canvas.width = Math.min(640, video.videoWidth);
                    canvas.height = Math.min(480, video.videoHeight);
                    const ctx = canvas.getContext("2d");
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    if (window.jsQR) {
                        const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                        const code = window.jsQR(imgData.data, imgData.width, imgData.height);
                        if (code && code.data) {
                            handleStockBarcodeDetected(code.data);
                        }
                    }
                }
            } catch (err) {
                // Frame decode error; continue scanning next frame
            } finally {
                isDetecting = false;
            }
        }

        if (MobileState.stock.isScanning) {
            MobileState.stock.scanLoopId = requestAnimationFrame(tick);
        }
    }

    MobileState.stock.scanLoopId = requestAnimationFrame(tick);
}

function handleStockBarcodeDetected(rawCode) {
    const now = Date.now();
    if (rawCode === MobileState.stock.lastScannedCode && (now - MobileState.stock.lastScannedTime) < 2500) {
        return;
    }

    const cleanPlate = extractPlateFromScannedCode(rawCode);
    if (!cleanPlate || cleanPlate.length < 3) return;

    MobileState.stock.lastScannedCode = rawCode;
    MobileState.stock.lastScannedTime = now;

    // Visual feedback
    const viewport = document.getElementById("stock-camera-viewport");
    const liveBadge = document.getElementById("stock-scan-live-badge");
    const detectedSpan = document.getElementById("stock-last-detected");
    if (viewport) {
        viewport.classList.add("detected");
        setTimeout(() => viewport.classList.remove("detected"), 450);
    }
    if (liveBadge && detectedSpan) {
        detectedSpan.textContent = cleanPlate;
        liveBadge.style.display = "block";
        setTimeout(() => {
            if (liveBadge) liveBadge.style.display = "none";
        }, 2200);
    }

    // Audio & haptic feedback
    playScanBeep(true);
    if (navigator.vibrate) navigator.vibrate(100);

    // Add to staged queue
    addPlateToStockQueue(cleanPlate);

    // Auto-sync if checkbox is checked
    const chkAutoSync = document.getElementById("chk-auto-sync");
    if (chkAutoSync && chkAutoSync.checked) {
        setTimeout(() => {
            syncStockScansToServer();
        }, 250);
    }
}

function submitSingleStockPlate() {
    const input = document.getElementById("input-stock-barcode");
    if (!input) return;
    const raw = input.value.trim().toUpperCase();
    if (!raw) return;

    addPlateToStockQueue(raw);
    input.value = "";
    input.focus();
}

function showMobileToast(message, isWarning = false) {
    let toast = document.getElementById("mobile-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "mobile-toast";
        toast.style.position = "fixed";
        toast.style.bottom = "80px";
        toast.style.left = "50%";
        toast.style.transform = "translateX(-50%)";
        toast.style.padding = "10px 18px";
        toast.style.borderRadius = "20px";
        toast.style.fontSize = "0.85rem";
        toast.style.fontWeight = "bold";
        toast.style.zIndex = "99999";
        toast.style.boxShadow = "0 4px 14px rgba(0,0,0,0.5)";
        toast.style.transition = "opacity 0.3s ease";
        toast.style.pointerEvents = "none";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.background = isWarning ? "#ef4444" : "#10b981";
    toast.style.color = "#ffffff";
    toast.style.opacity = "1";
    if (window._mobileToastTimer) clearTimeout(window._mobileToastTimer);
    window._mobileToastTimer = setTimeout(() => {
        if (toast) toast.style.opacity = "0";
    }, 2800);
}

function addBulkStockPlates() {
    const textarea = document.getElementById("text-stock-bulk");
    if (!textarea) return;
    const lines = textarea.value.split("\n");
    let added = 0;
    let dups = 0;
    lines.forEach(l => {
        const raw = l.trim().toUpperCase();
        const cleaned = raw.replace(/^IK-/, "").replace(/[^A-Z0-9]/g, "");
        if (cleaned) {
            if (!MobileState.stock.queue.includes(cleaned)) {
                MobileState.stock.queue.push(cleaned);
                added++;
            } else {
                dups++;
            }
        }
    });
    textarea.value = "";
    renderStockQueueList();
    if (added > 0 || dups > 0) {
        let msg = `Added ${added} new plates.`;
        if (dups > 0) {
            msg += ` (${dups} duplicates skipped)`;
        }
        showMobileToast(msg, dups > 0 && added === 0);
    }
}

function addPlateToStockQueue(plateStr) {
    let clean = plateStr.replace(/^IK-/, "").replace(/[^A-Z0-9]/g, "");
    if (!clean) return;

    if (!MobileState.stock.queue.includes(clean)) {
        MobileState.stock.queue.push(clean);
        if (navigator.vibrate) {
            navigator.vibrate(50);
        }
        renderStockQueueList();
        showMobileToast(`✓ Added ${clean} (${MobileState.stock.queue.length} staged)`);
    } else {
        if (navigator.vibrate) {
            navigator.vibrate([100, 60, 100]);
        }
        showMobileToast(`⚠️ Duplicate: Plate ${clean} already scanned!`, true);
    }
}

function removeStockPlate(idx) {
    MobileState.stock.queue.splice(idx, 1);
    renderStockQueueList();
}

function renderStockQueueList() {
    const listElem = document.getElementById("stock-scanned-list");
    const countBadge = document.getElementById("stock-scanned-count-badge");
    const queueCount = document.getElementById("stock-queue-count");
    if (!listElem) return;

    const q = MobileState.stock.queue;
    if (countBadge) countBadge.textContent = `${q.length} Scanned`;
    if (queueCount) queueCount.textContent = `${q.length}`;

    if (q.length === 0) {
        listElem.innerHTML = `<div style="color:#64748b; text-align:center; padding:12px;">No plates staged yet. Scan or paste above.</div>`;
        return;
    }

    listElem.innerHTML = q.map((p, idx) => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-bottom:1px solid #1e293b;">
            <span style="font-weight:bold; color:#f8fafc;">${idx + 1}. ${p}</span>
            <span style="color:var(--ug-yellow); font-size:0.75rem;">[${MobileState.stock.category}]</span>
            <button onclick="removeStockPlate(${idx})" style="background:none; border:none; color:#ef4444; font-size:1.1rem; cursor:pointer;">✕</button>
        </div>
    `).join("");
}

async function syncStockScansToServer() {
    const q = MobileState.stock.queue;
    if (q.length === 0) {
        alert("No plates in queue to sync.");
        return;
    }

    const btn = document.getElementById("btn-sync-stock");
    if (btn) btn.disabled = true;

    const subMode = MobileState.stock.subMode;
    const category = MobileState.stock.category;

    let endpoint = "/api/stock/dispatch/";
    let payload = {
        plates: q,
        plate_category: category,
        operator_name: MobileState.deviceName,
    };

    if (subMode === "DELIVERY") {
        endpoint = "/api/stock/delivery/";
        payload.delivery_number = `DEL-MOB-${Date.now().toString(36).toUpperCase()}`;
        payload.auto_create_kits = true;
    } else if (subMode === "RETURN") {
        endpoint = "/api/stock/return/";
        payload.reason = "LINE_ROLLOVER";
    }

    try {
        const resp = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.success) {
            alert(`✓ Success: Recorded ${q.length} plates for ${subMode} (${category})!`);
            MobileState.stock.queue = [];
            renderStockQueueList();
        } else {
            alert(`Server error: ${data.error || "Unknown"}`);
        }
    } catch (err) {
        alert(`Network connection error: ${err.message}`);
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ============================================================================
// MODE 1: On-Conveyor Actions (1-by-1 Bike Guided Pair Capture)
// ============================================================================
function triggerNativeCapture(orientation) {
    const inputId = orientation === "FRONT" ? "input-native-front" : "input-native-rear";
    const inputElem = document.getElementById(inputId);
    if (inputElem) {
        inputElem.value = "";
        inputElem.click();
    }
}

async function handleNativeFileCaptured(inputElem, orientation) {
    if (!inputElem.files || inputElem.files.length === 0) return;

    const file = inputElem.files[0];
    const previewUrl = URL.createObjectURL(file);
    const nowIso = new Date().toISOString();

    const isFront = (orientation === "FRONT");
    const previewImg = document.getElementById(isFront ? "img-preview-front" : "img-preview-rear");
    const placeholder = document.getElementById(isFront ? "placeholder-front" : "placeholder-rear");
    const statusTag = document.getElementById(isFront ? "status-tag-front" : "status-tag-rear");
    const card = document.getElementById(isFront ? "card-front" : "card-rear");
    const btnView = document.getElementById(isFront ? "btn-view-front" : "btn-view-rear");

    if (previewImg && placeholder) {
        previewImg.src = previewUrl;
        previewImg.style.display = "block";
        placeholder.style.display = "none";
    }

    if (btnView) {
        btnView.style.display = "inline-block";
    }

    if (statusTag) {
        statusTag.textContent = "Captured ✓";
        statusTag.style.color = "var(--ug-green)";
    }

    if (card) {
        card.classList.add("completed");
    }

    if (isFront) {
        MobileState.conveyor.frontBlob = file;
        MobileState.conveyor.frontUrl = previewUrl;
    } else {
        MobileState.conveyor.rearBlob = file;
        MobileState.conveyor.rearUrl = previewUrl;
    }

    // Save item to IndexedDB immediately before upload
    const outboxItem = {
        id: `conveyor_${MobileState.conveyor.bikeId}_${orientation}`,
        bike_client_id: MobileState.conveyor.bikeId,
        mode: "conveyor",
        orientation: orientation,
        sequence_number: MobileState.conveyor.sequence,
        blob: file,
        filename: `${orientation.toLowerCase()}_bike_${MobileState.conveyor.sequence}.jpg`,
        captured_at: nowIso,
        status: "pending"
    };

    await savePhotoToOutbox(outboxItem);
    await refreshOutboxCount();

    // Fast client-side sharpness pre-check (<35ms on mobile)
    estimateImageSharpness(file).then(sharpness => {
        if (sharpness < 35) {
            showQualityWarning({
                is_blurry: true,
                sharpness_score: sharpness,
                has_issues: true,
            }, orientation);
        }
    });

    // Haptic feedback
    if (navigator.vibrate) navigator.vibrate(100);

    // Update Conveyor Stepper Logic
    updateConveyorStepper();

    // Trigger sync
    drainOutboxQueue();
}

// Fast Client-side Laplacian Sharpness Estimator
function estimateImageSharpness(file) {
    return new Promise((resolve) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
            try {
                const canvas = document.createElement("canvas");
                const w = 240;
                const h = Math.max(120, Math.round(img.height * (w / img.width)));
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, w, h);
                const imgData = ctx.getImageData(0, 0, w, h);
                const d = imgData.data;

                // Grayscale conversion
                const gray = new Float32Array(w * h);
                for (let i = 0; i < d.length; i += 4) {
                    gray[i / 4] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
                }

                // 2D discrete Laplacian kernel variance
                let sum = 0, sumSq = 0, count = 0;
                for (let y = 1; y < h - 1; y++) {
                    for (let x = 1; x < w - 1; x++) {
                        const idx = y * w + x;
                        const lap = gray[idx - w] + gray[idx + w] + gray[idx - 1] + gray[idx + 1] - 4 * gray[idx];
                        sum += lap;
                        sumSq += lap * lap;
                        count++;
                    }
                }
                const mean = sum / count;
                const variance = (sumSq / count) - (mean * mean);
                URL.revokeObjectURL(url);
                resolve(Math.round(variance));
            } catch (e) {
                URL.revokeObjectURL(url);
                resolve(100);
            }
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            resolve(100);
        };
        img.src = url;
    });
}

// Quality & Defocus Correction Alert Handlers
function showQualityWarning(report, orientation) {
    MobileState.activeQualityReport = report;
    MobileState.qualityOrientation = orientation;

    const card = document.getElementById("quality-correction-card");
    if (!card) return;

    const title = document.getElementById("quality-warn-title");
    const score = document.getElementById("quality-warn-score");
    const desc = document.getElementById("quality-warn-desc");

    if (report.is_blurry) {
        if (title) title.textContent = "⚠️ Photo Out of Focus / Blurry";
        if (score) score.textContent = `Sharpness: ${report.sharpness_score} / 100`;
        if (desc) desc.textContent = "The camera was shaking or plate was out of focus. Clear characters are required for ITMS verification.";
    } else if (report.is_underexposed) {
        if (title) title.textContent = "⚠️ Photo Too Dark";
        if (score) score.textContent = "Underexposed";
        if (desc) desc.textContent = "Insufficient light on plate. Turn on flash or use workshop lighting.";
    } else if (report.is_overexposed) {
        if (title) title.textContent = "⚠️ Severe Glare on Plate";
        if (score) score.textContent = "Overexposed";
        if (desc) desc.textContent = "Direct light reflection prevents reading the plate. Adjust camera angle slightly.";
    }

    card.style.display = "flex";

    // Highlight preview card with warning border
    const cardElem = document.getElementById(orientation === "FRONT" ? "card-front" : "card-rear");
    if (cardElem) cardElem.classList.add("has-quality-warning");

    if (navigator.vibrate) navigator.vibrate([150, 80, 150]);
}

function dismissQualityWarning() {
    const card = document.getElementById("quality-correction-card");
    if (card) card.style.display = "none";
    ["card-front", "card-rear"].forEach(id => {
        const c = document.getElementById(id);
        if (c) c.classList.remove("has-quality-warning");
    });
}

function handleQualityRetake() {
    dismissQualityWarning();
    const orientation = MobileState.qualityOrientation || (MobileState.conveyor.rearBlob ? "REAR" : "FRONT");
    triggerNativeCapture(orientation);
}

// Photo Inspector Lightbox Modal Handlers
function inspectConveyorPhoto(e, orientation) {
    if (e) e.stopPropagation();
    const isFront = (orientation === "FRONT");
    const url = isFront ? MobileState.conveyor.frontUrl : MobileState.conveyor.rearUrl;
    if (!url) return;

    openPhotoInspector(orientation, url, `Motorcycle #${MobileState.conveyor.sequence} &bull; ${orientation}`);
}

function handlePreviewCardClick(orientation) {
    const isFront = (orientation === "FRONT");
    const blob = isFront ? MobileState.conveyor.frontBlob : MobileState.conveyor.rearBlob;
    if (blob) {
        inspectConveyorPhoto(null, orientation);
    } else {
        triggerNativeCapture(orientation);
    }
}

function openPhotoInspector(orientation, url, customTitle) {
    MobileState.inspectedOrientation = orientation;
    const modal = document.getElementById("photo-lightbox-modal");
    const img = document.getElementById("lightbox-img");
    const title = document.getElementById("lightbox-title");
    const sharpText = document.getElementById("lightbox-sharpness-text");
    const expText = document.getElementById("lightbox-exposure-text");
    const sharpPill = document.getElementById("lightbox-sharpness-pill");
    const expPill = document.getElementById("lightbox-exposure-pill");

    if (img) img.src = url;
    if (title) title.innerHTML = customTitle || `${orientation} Photo`;

    const report = MobileState.activeQualityReport;
    if (report) {
        if (sharpText) sharpText.textContent = `Sharpness: ${report.sharpness_score} (${report.sharpness_label || 'Analyzed'})`;
        if (expText) expText.textContent = `Lighting: ${report.exposure_label || 'Normal'}`;
        if (sharpPill) sharpPill.className = `metric-pill ${report.is_blurry ? 'critical' : 'good'}`;
        if (expPill) expPill.className = `metric-pill ${report.is_underexposed || report.is_overexposed ? 'warning' : 'good'}`;
    } else {
        if (sharpText) sharpText.textContent = "Sharpness: Focused ✓";
        if (expText) expText.textContent = "Lighting: Balanced ✓";
        if (sharpPill) sharpPill.className = "metric-pill good";
        if (expPill) expPill.className = "metric-pill good";
    }

    if (modal) modal.style.display = "flex";
}

function closePhotoInspector() {
    const modal = document.getElementById("photo-lightbox-modal");
    if (modal) modal.style.display = "none";
}

function handleLightboxRetake() {
    closePhotoInspector();
    const orientation = MobileState.inspectedOrientation || "FRONT";
    if (MobileState.mode === "conveyor") {
        triggerNativeCapture(orientation);
    } else {
        triggerUturnCapture();
    }
}

function updateConveyorStepper() {
    const bikeLabel = document.getElementById("conveyor-bike-label");
    if (bikeLabel) {
        bikeLabel.textContent = `Motorcycle #${MobileState.conveyor.sequence}`;
    }

    const hasFront = !!MobileState.conveyor.frontBlob;
    const hasRear = !!MobileState.conveyor.rearBlob;
    const capturedCount = (hasFront ? 1 : 0) + (hasRear ? 1 : 0);

    const progressBadge = document.getElementById("badge-pair-progress");
    if (progressBadge) {
        if (capturedCount === 0) {
            progressBadge.textContent = "0 of 2 Captured";
            progressBadge.className = "badge badge-yellow";
        } else if (capturedCount === 1) {
            progressBadge.textContent = "1 of 2 Captured";
            progressBadge.className = "badge badge-yellow";
        } else {
            progressBadge.textContent = "Pair Complete ✓";
            progressBadge.className = "badge badge-green";
        }
    }

    // Step pills
    const pillFront = document.getElementById("pill-step-front");
    const checkFront = document.getElementById("check-step-front");
    const pillRear = document.getElementById("pill-step-rear");
    const checkRear = document.getElementById("check-step-rear");

    if (pillFront && checkFront) {
        if (hasFront) {
            pillFront.classList.remove("active");
            pillFront.classList.add("completed");
            checkFront.textContent = "✓";
        } else {
            pillFront.classList.add("active");
            pillFront.classList.remove("completed");
            checkFront.textContent = "⭕";
        }
    }

    if (pillRear && checkRear) {
        if (hasRear) {
            pillRear.classList.remove("active");
            pillRear.classList.add("completed");
            checkRear.textContent = "✓";
        } else if (hasFront) {
            pillRear.classList.add("active");
            pillRear.classList.remove("completed");
            checkRear.textContent = "⭕";
        } else {
            pillRear.classList.remove("active", "completed");
            checkRear.textContent = "⭕";
        }
    }

    // Smart Shutter Hero Button
    const btnSmart = document.getElementById("btn-conveyor-smart");
    const smartIcon = document.getElementById("conveyor-smart-icon");
    const smartTitle = document.getElementById("conveyor-smart-title");
    const smartSub = document.getElementById("conveyor-smart-sub");

    if (btnSmart) {
        btnSmart.classList.remove("shutter-step-front", "shutter-step-rear", "shutter-step-complete");
        if (!hasFront) {
            btnSmart.classList.add("shutter-step-front");
            if (smartIcon) smartIcon.textContent = "📸";
            if (smartTitle) smartTitle.textContent = "Take FRONT Photo";
            if (smartSub) smartSub.textContent = "Step 1: Headlamp & Front Plate";
        } else if (!hasRear) {
            btnSmart.classList.add("shutter-step-rear");
            if (smartIcon) smartIcon.textContent = "📸";
            if (smartTitle) smartTitle.textContent = "Take REAR Photo";
            if (smartSub) smartSub.textContent = "Step 2: Tail Lamp & Rear Plate";
        } else {
            btnSmart.classList.add("shutter-step-complete");
            if (smartIcon) smartIcon.textContent = "✓";
            if (smartTitle) smartTitle.textContent = "Pair Captured!";
            if (smartSub) smartSub.textContent = `Tap to advance to Motorcycle #${MobileState.conveyor.sequence + 1}`;
        }
    }

    // Advance to next bike button
    const btnNext = document.getElementById("btn-next-bike");
    if (btnNext) {
        const isPairComplete = (hasFront && hasRear);
        btnNext.disabled = !isPairComplete;
        if (isPairComplete) {
            btnNext.classList.add("ready");
        } else {
            btnNext.classList.remove("ready");
        }
    }
}

function handleConveyorSmartShutter() {
    const hasFront = !!MobileState.conveyor.frontBlob;
    const hasRear = !!MobileState.conveyor.rearBlob;

    if (!hasFront) {
        triggerNativeCapture("FRONT");
    } else if (!hasRear) {
        triggerNativeCapture("REAR");
    } else {
        advanceToNextBike();
    }
}

function advanceToNextBike() {
    const hasFront = !!MobileState.conveyor.frontBlob;
    const hasRear = !!MobileState.conveyor.rearBlob;

    if (!hasFront || !hasRear) {
        alert("Please capture both Front and Rear photos before advancing to the next motorcycle.");
        return;
    }

    // Advance sequence and re-initialize conveyor motorcycle state
    MobileState.conveyor.sequence += 1;
    MobileState.conveyor.bikeId = "BIKE-" + Date.now().toString(36).toUpperCase();
    MobileState.conveyor.frontBlob = null;
    MobileState.conveyor.frontUrl = null;
    MobileState.conveyor.rearBlob = null;
    MobileState.conveyor.rearUrl = null;
    MobileState.conveyor.isComplete = false;

    // Reset preview thumbnail cards
    ["front", "rear"].forEach(side => {
        const previewImg = document.getElementById(`img-preview-${side}`);
        const placeholder = document.getElementById(`placeholder-${side}`);
        const statusTag = document.getElementById(`status-tag-${side}`);
        const card = document.getElementById(`card-${side}`);
        const btnView = document.getElementById(`btn-view-${side}`);

        if (previewImg) { previewImg.src = ""; previewImg.style.display = "none"; }
        if (placeholder) { placeholder.style.display = "block"; }
        if (statusTag) { statusTag.textContent = "Empty"; statusTag.style.color = ""; }
        if (card) { card.classList.remove("completed", "has-quality-warning"); }
        if (btnView) { btnView.style.display = "none"; }
    });

    dismissQualityWarning();
    updateConveyorStepper();

    if (navigator.vibrate) navigator.vibrate([60, 40, 60]);

    // Ensure outbox queue is draining in background
    drainOutboxQueue();
}


// ============================================================================
// MODE 2: Off-Conveyor (U-Turn Yard Walk) Actions
// ============================================================================
function toggleUturnTurnaround() {
    const isRear = (MobileState.uturn.phase === "REAR");
    MobileState.uturn.phase = isRear ? "FRONT" : "REAR";

    const roadRear = document.getElementById("road-step-rear");
    const roadFront = document.getElementById("road-step-front");
    const guidanceBar = document.getElementById("uturn-guidance-bar");
    const guidanceIcon = document.getElementById("uturn-guidance-icon");
    const instruction = document.getElementById("uturn-instruction-text");
    const shutter = document.getElementById("btn-big-shutter");
    const shutterIcon = document.getElementById("shutter-icon");
    const shutterText = document.getElementById("shutter-main-text");
    const turnaroundCallout = document.getElementById("turnaround-callout");
    const returnMatchBanner = document.getElementById("return-match-banner");

    if (MobileState.uturn.phase === "FRONT") {
        if (roadRear) { roadRear.classList.remove("active"); roadRear.classList.add("completed"); }
        if (roadFront) { roadFront.classList.add("active"); roadFront.classList.remove("completed"); }

        if (guidanceBar) guidanceBar.className = "uturn-guidance-bar return-mode";
        if (guidanceIcon) guidanceIcon.textContent = "↩️";
        if (instruction) instruction.innerHTML = "Walking back! Photograph <strong>FRONTS</strong> in reverse order (trajectories auto-pair).";

        if (shutter) {
            shutter.classList.remove("shutter-rear");
            shutter.classList.add("shutter-front");
        }
        if (shutterIcon) shutterIcon.textContent = "📸";
        if (shutterText) shutterText.textContent = "Capture FRONT";

        if (turnaroundCallout) turnaroundCallout.style.display = "none";
        if (returnMatchBanner) returnMatchBanner.style.display = "flex";
    } else {
        if (roadRear) { roadRear.classList.add("active"); roadRear.classList.remove("completed"); }
        if (roadFront) { roadFront.classList.remove("active"); }

        if (guidanceBar) guidanceBar.className = "uturn-guidance-bar";
        if (guidanceIcon) guidanceIcon.textContent = "🚶";
        if (instruction) instruction.innerHTML = "Walk down row snapping <strong>REAR</strong> plates. Tap turnaround when reaching the end.";

        if (shutter) {
            shutter.classList.remove("shutter-front");
            shutter.classList.add("shutter-rear");
        }
        if (shutterIcon) shutterIcon.textContent = "📸";
        if (shutterText) shutterText.textContent = "Capture REAR";

        if (returnMatchBanner) returnMatchBanner.style.display = "none";
        if (turnaroundCallout && MobileState.uturn.rearCount > 0) {
            turnaroundCallout.style.display = "flex";
        } else if (turnaroundCallout) {
            turnaroundCallout.style.display = "none";
        }
    }

    updateUturnShutterCounter();
    if (navigator.vibrate) navigator.vibrate([80, 40, 80]);
}

function updateUturnShutterCounter() {
    const counterElem = document.getElementById("shutter-counter");
    const badgeRear = document.getElementById("road-rear-badge");
    const badgeFront = document.getElementById("road-front-badge");
    const totalElem = document.getElementById("uturn-total-count");
    const matchText = document.getElementById("return-match-text");
    const matchPercent = document.getElementById("return-match-percent");
    const matchFill = document.getElementById("match-progress-fill");
    const btnFinish = document.getElementById("btn-finish-uturn");

    const rears = MobileState.uturn.rearCount;
    const fronts = MobileState.uturn.frontCount;
    const total = MobileState.uturn.totalPhotos;

    if (badgeRear) badgeRear.textContent = `${rears} Rears`;
    if (badgeFront) badgeFront.textContent = `${fronts} Fronts`;
    if (totalElem) totalElem.textContent = total;

    if (counterElem) {
        if (MobileState.uturn.phase === "REAR") {
            if (rears >= 100) {
                counterElem.textContent = "100 Bikes Reached (Row Full)";
            } else {
                counterElem.textContent = `Bike #${rears + 1} (${100 - rears} left in row)`;
            }
        } else {
            const targetBike = rears - fronts;
            counterElem.textContent = targetBike > 0 ? `Bike #${targetBike} (${fronts + 1} of ${rears})` : `Extra Bike #${fronts + 1}`;
        }
    }

    // Match tracking bar calculation
    if (matchText && matchPercent && matchFill) {
        const pct = rears > 0 ? Math.min(100, Math.round((fronts / rears) * 100)) : 0;
        matchText.textContent = `Matching ${fronts} of ${rears} bikes`;
        matchPercent.textContent = `${pct}%`;
        matchFill.style.width = `${pct}%`;
    }

    if (btnFinish) {
        btnFinish.disabled = (total === 0);
        if (rears > 0 && fronts === rears) {
            btnFinish.classList.add("ready");
            btnFinish.innerHTML = `<span>✓ All ${rears} Pairs Matched ➔ Submit Batch</span>`;
        } else {
            btnFinish.classList.remove("ready");
            btnFinish.innerHTML = `<span>🏁 Complete U-Turn Batch (${total} photos)</span>`;
        }
    }
}

function triggerUturnCapture() {
    const input = document.getElementById("input-native-uturn");
    if (input) {
        input.value = "";
        input.click();
    }
}

async function handleNativeUturnFileCaptured(inputElem) {
    if (!inputElem.files || inputElem.files.length === 0) return;

    const file = inputElem.files[0];
    const orientation = MobileState.uturn.phase;
    const nowIso = new Date().toISOString();

    if (orientation === "REAR") {
        MobileState.uturn.rearCount += 1;
    } else {
        MobileState.uturn.frontCount += 1;
    }
    MobileState.uturn.totalPhotos += 1;

    const seqNum = (orientation === "REAR") ? MobileState.uturn.rearCount : MobileState.uturn.frontCount;
    const itemId = `uturn_${Date.now()}_${orientation}_${seqNum}`;

    const outboxItem = {
        id: itemId,
        mode: "uturn",
        orientation: orientation,
        sequence_number: seqNum,
        blob: file,
        filename: `uturn_${orientation.toLowerCase()}_${seqNum}.jpg`,
        captured_at: nowIso,
        status: "pending"
    };

    // Store in recent tiles for undo / retake
    MobileState.uturn.recentTiles.push({
        id: itemId,
        orientation: orientation,
        seqNum: seqNum,
        file: file
    });

    await savePhotoToOutbox(outboxItem);
    await refreshOutboxCount();

    // Show turnaround callout if in REAR phase and at least 1 rear photo snapped
    const turnaroundCallout = document.getElementById("turnaround-callout");
    if (turnaroundCallout && orientation === "REAR") {
        turnaroundCallout.style.display = "flex";
    }

    // Show undo button
    const btnUndo = document.getElementById("btn-undo-snap");
    if (btnUndo) btnUndo.style.display = "inline-flex";

    // Add mini preview tile
    addMiniPreviewTile(file, orientation, seqNum);
    updateUturnShutterCounter();

    if (navigator.vibrate) navigator.vibrate(100);

    drainOutboxQueue();
}

function addMiniPreviewTile(file, orientation, seqNum) {
    const strip = document.getElementById("uturn-recent-strip");
    if (!strip) return;

    // Remove empty placeholder message if present
    const emptyMsg = strip.querySelector(".empty-strip-msg");
    if (emptyMsg) emptyMsg.remove();

    const url = URL.createObjectURL(file);
    const tile = document.createElement("div");
    tile.className = "mini-tile";
    tile.onclick = () => openPhotoInspector(orientation, url, `U-Turn ${orientation} #${seqNum}`);
    tile.innerHTML = `
        <img src="${url}" alt="${orientation}">
        <span class="mini-tile-badge" style="background:${orientation === 'FRONT' ? '#0284c7' : '#10b981'}">${orientation[0]}${seqNum}</span>
    `;

    strip.prepend(tile);
}

async function undoLastUturnSnap() {
    if (!MobileState.uturn.recentTiles || MobileState.uturn.recentTiles.length === 0) return;

    const last = MobileState.uturn.recentTiles.pop();
    if (!last) return;

    if (!confirm(`Retake last photo (${last.orientation} #${last.seqNum})? It will be removed from queue.`)) {
        MobileState.uturn.recentTiles.push(last);
        return;
    }

    // Decrement counters
    if (last.orientation === "REAR") {
        MobileState.uturn.rearCount = Math.max(0, MobileState.uturn.rearCount - 1);
    } else {
        MobileState.uturn.frontCount = Math.max(0, MobileState.uturn.frontCount - 1);
    }
    MobileState.uturn.totalPhotos = Math.max(0, MobileState.uturn.totalPhotos - 1);

    // Delete from IndexedDB
    try {
        const db = await openOutboxDB();
        const tx = db.transaction(STORE_NAME, "readwrite");
        tx.objectStore(STORE_NAME).delete(last.id);
    } catch (err) {
        console.warn("Failed to delete from IndexedDB:", err);
    }

    // Remove first child from preview strip
    const strip = document.getElementById("uturn-recent-strip");
    if (strip && strip.firstElementChild && !strip.firstElementChild.classList.contains("empty-strip-msg")) {
        strip.removeChild(strip.firstElementChild);
        if (strip.children.length === 0) {
            strip.innerHTML = '<div class="empty-strip-msg">Snapped photos will appear here</div>';
        }
    }

    const btnUndo = document.getElementById("btn-undo-snap");
    if (btnUndo && MobileState.uturn.recentTiles.length === 0) {
        btnUndo.style.display = "none";
    }

    // Update Turnaround card visibility if rearCount becomes 0
    const turnaroundCallout = document.getElementById("turnaround-callout");
    if (turnaroundCallout && MobileState.uturn.phase === "REAR" && MobileState.uturn.rearCount === 0) {
        turnaroundCallout.style.display = "none";
    }

    updateUturnShutterCounter();
    await refreshOutboxCount();
    if (navigator.vibrate) navigator.vibrate(50);
}

function finishUturnBatch() {
    const total = MobileState.uturn.totalPhotos;
    const rears = MobileState.uturn.rearCount;
    const fronts = MobileState.uturn.frontCount;

    if (total === 0) {
        alert("No photos captured in this U-Turn walk yet.");
        return;
    }

    if (rears !== fronts) {
        if (!confirm(`Warning: Count asymmetry (${rears} Rears vs ${fronts} Fronts). Reverse trajectory matching will pair photos in reverse order. Proceed to submit batch anyway?`)) {
            return;
        }
    } else {
        if (!confirm(`Submit completed U-Turn batch of ${total} photos (${rears} pairs)?`)) {
            return;
        }
    }

    // Reset U-Turn state for next row
    MobileState.uturn.rearCount = 0;
    MobileState.uturn.frontCount = 0;
    MobileState.uturn.totalPhotos = 0;
    MobileState.uturn.recentTiles = [];
    MobileState.uturn.phase = "REAR";

    const strip = document.getElementById("uturn-recent-strip");
    const turnaroundCallout = document.getElementById("turnaround-callout");
    const returnMatchBanner = document.getElementById("return-match-banner");
    const btnUndo = document.getElementById("btn-undo-snap");

    if (strip) strip.innerHTML = '<div class="empty-strip-msg">Snapped photos will appear here</div>';
    if (turnaroundCallout) turnaroundCallout.style.display = "none";
    if (returnMatchBanner) returnMatchBanner.style.display = "none";
    if (btnUndo) btnUndo.style.display = "none";

    const roadRear = document.getElementById("road-step-rear");
    const roadFront = document.getElementById("road-step-front");
    if (roadRear) { roadRear.classList.add("active"); roadRear.classList.remove("completed"); }
    if (roadFront) { roadFront.classList.remove("active", "completed"); }

    const shutter = document.getElementById("btn-big-shutter");
    const shutterText = document.getElementById("shutter-main-text");
    if (shutter) {
        shutter.classList.remove("shutter-front");
        shutter.classList.add("shutter-rear");
    }
    if (shutterText) shutterText.textContent = "Capture REAR";

    updateUturnShutterCounter();
    drainOutboxQueue();
    alert("✓ U-Turn walk batch queued and uploading to laptop! Reverse trajectory matching will auto-pair photos.");
}

// ============================================================================
// Drawer Toggle
// ============================================================================
function toggleOutboxDrawer() {
    const tray = document.getElementById("outbox-tray");
    const drawer = document.getElementById("outbox-drawer-content");
    if (!tray || !drawer) return;

    const isOpen = (drawer.style.display !== "none");
    if (isOpen) {
        drawer.style.display = "none";
        tray.classList.remove("open");
    } else {
        drawer.style.display = "block";
        tray.classList.add("open");
        refreshOutboxCount();
    }
}

// ============================================================================
// Initialization
// ============================================================================
function initMobileCompanion() {
    try {
        updateConveyorStepper();
    } catch (e) {
        console.error("Error in updateConveyorStepper:", e);
    }
    try {
        updateUturnShutterCounter();
    } catch (e) {
        console.error("Error in updateUturnShutterCounter:", e);
    }
    try {
        refreshOutboxCount();
    } catch (e) {
        console.error("Error in refreshOutboxCount:", e);
    }
    try {
        startHeartbeat();
    } catch (e) {
        console.error("Error in startHeartbeat:", e);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMobileCompanion);
} else {
    initMobileCompanion();
}

