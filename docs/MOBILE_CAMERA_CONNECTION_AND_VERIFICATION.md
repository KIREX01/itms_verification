# Mobile Camera Connection & Ingestion Engine
**UG-ITMS Verification Copilot &bull; Field Operations & Ingestion Documentation**

---

## 1. Executive Summary & Problem Statement

### The Legacy Bottleneck (v1.0.5)
In version 1.0.5, motorcycle verification required technicians to:
1. Walk into the factory or storage yard with standalone digital cameras or personal phones.
2. Manually capture hundreds of front and rear photos of motorcycles.
3. Walk back to the desktop or laptop terminal, connect physical USB data cables or remove SD cards.
4. Manually copy images off the storage media, sort them into separate `front/` and `rear/` directory trees, and manually pair corresponding angles.
5. Ingest the batches into the ITMS Verification Copilot.

This process introduced severe latency, high operator fatigue, and frequent human pairing errors (misplaced photos, skipped bikes, or mismatched front/rear plates).

### The Two Factory Operational Environments

Our solution addresses two distinct operational realities:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OPERATIONAL ENVIRONMENT 1                            │
│                  ON-CONVEYOR (ASSEMBLY LINE INSPECTION)                     │
│                                                                             │
│  [Assembly Line] ──▶ [Number Plate Fitted] ──▶ [Dispatch / Storage]         │
│                                                                             │
│  • Fast, 1-by-1 sequential flow.                                            │
│  • No physical space for parking rows.                                      │
│  • Requirement: 1:1 Guided pairing (Front ➔ Rear ➔ Lock & Advance).         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                       OPERATIONAL ENVIRONMENT 2                            │
│                  OFF-CONVEYOR (STORAGE YARD U-TURN WALK)                    │
│                                                                             │
│    Bike 1     Bike 2     Bike 3     Bike 4     ...     Bike N               │
│   [ REAR ]   [ REAR ]   [ REAR ]   [ REAR ]   ...    [ REAR ]   ◀── Outbound│
│      │          │          │          │                 │          Leg (1..N│
│      ▼          ▼          ▼          ▼                 ▼                   │
│   [ FRONT]   [ FRONT]   [ FRONT]   [ FRONT]   ...    [ FRONT]   ──▶ Inbound │
│                                                          │          Leg (N..1│
│                                                   [TURNAROUND]              │
│                                                                             │
│  • Hundreds of bikes tightly parked in long rows.                           │
│  • Operator walks outbound along the rears: 1, 2, 3 ... N                   │
│  • Operator turns around at row's end and walks inbound: N, N-1 ... 1       │
│  • Trajectory matching: Reverse order auto-pairing [Rear i ⟷ Front N-i+1].  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture & Network Discovery

### 2.1 Zero-Install Mobile PWA
No native app installation (APK/iOS TestFlight) is needed on technician smartphones. The mobile companion is served as a high-performance, responsive Progressive Web Application directly by the Django application:
- **URL**: `http://<laptop-ip>:8000/mobile/`
- **Full Sensor Access**: Leverages HTML5 `<input type="file" accept="image/*" capture="environment">` to invoke the native phone camera hardware, capturing photos at full optical resolution with autofocus, sensor flash, and hardware stabilization.

### 2.2 Dynamic Multi-Network Discovery (`core.services.network_service`)
Factory and yard environments present diverse networking setups. The system dynamically senses, classifies, and updates IP endpoints:

| Connection Mode | Subnet / IP Range | Behavior |
| :--- | :--- | :--- |
| **`LAPTOP_HOTSPOT`** | `192.168.137.1` | Laptop creates a Windows Mobile Hotspot. Phone connects directly without router hardware. |
| **`PHONE_HOTSPOT`** | `192.168.43.x` (Android)<br>`172.20.10.x` (iOS) | Technician enables hotspot on phone. Laptop connects to phone's Wi-Fi. |
| **`WIFI_LAN`** | `192.168.8.x`, `192.168.1.x`, `10.x.x.x` | Both devices share the factory or office Wi-Fi network. |

#### Route-Aware Priority
Virtual adapters (e.g. inactive Microsoft Wi-Fi Direct Virtual Adapter with static IP `192.168.137.1`) are prevented from masquerading as active routes. `network_service.get_local_ip()` probes the active default gateway to select the real reachable LAN interface first.

#### Live Network Transition Polling
- **Terminal UI (TUI)**: Polls network state every 5 seconds. If the laptop switches from Wi-Fi to Hotspot, the TUI dynamically updates the QR code, URL, and connection badge.
- **Web UI Dashboard**: Polls every 10 seconds and provides a manual network interface selector dropdown (`#dash-mobile-ip-select`).

### 2.3 Concurrent Background Web Service (`core.services.background_web_service`)
When field operators launch the Textual Terminal UI via `python manage.py run_tui`, a background daemon thread automatically starts an embedded WSGI HTTP server on `0.0.0.0:8000`.
- **Silent Logging**: Custom `SilentWSGIRequestHandler` suppresses standard HTTP access lines (`GET /api/mobile/ping/ 200`) from writing to `sys.stdout`, preserving the ANSI terminal graphics without visual corruption.
- **Graceful Shutdown**: Shuts down cleanly when the TUI exits.

### 2.4 Django `ALLOWED_HOSTS` Resolution
To prevent `DisallowedHost: Invalid HTTP_HOST header` exceptions when smartphones connect using fluctuating LAN/Hotspot IP addresses, `itms_project/settings.py` enforces wildcard acceptance (`*`) for LAN subnets while preserving security tokens.

### 2.5 Standard-Compliant Compact Unicode QR Codes (`core.services.qr_generator`)
- Generates standard ISO/IEC 18004 QR bit matrices using the `qrcode` library.
- Formats matrices using Unicode half-block characters (`▀`, `▄`, `█`, ` `) with `no_wrap=True`.
- Height is compressed to only 16 rows (77% smaller than conventional full-block ASCII), ensuring the QR code fits neatly inside Textual containers and does not overlap dashboard action sections.

---

## 3. Simplified Mobile UI/UX & Field Ergonomics

The mobile companion interface (`core/templates/core/mobile_companion.html`, `core/static/core/js/mobile_companion.js`, `core/static/core/css/mobile_companion.css`) is designed specifically for **one-handed operation in bright factory yards**:

### 3.1 Mode 1: On-Conveyor Guided Pair Ingestion
- **2-Step Progress Header**: Visual pills indicate Angle 1 (Front) and Angle 2 (Rear).
- **Single Smart Shutter Button**: One large button that dynamically morphs across the inspection lifecycle:
  1. `[ 📸 Take FRONT Photo ]` ➔ Launches native camera targeting headlamp & front plate.
  2. `[ 📸 Take REAR Photo ]` ➔ Automatically morphs to green, prompting for rear plate.
  3. `[ ➡️ Next Motorcycle ]` ➔ Morphs to gold; single tap locks the pair and advances to Bike #N+1.
- **Side-by-Side Mini Thumbnails**: Operators can tap either thumbnail to retake an angle if motion blur is detected.
- **Haptic Confirmations**: Short vibration on capture; double vibration pulse on pair completion.

### 3.2 Mode 2: Off-Conveyor (U-Turn) Row Walk
- **Visual Flow Road**: Top progress indicator clearly shows:
  `[ 1. Going Out: REARS (X) ] ──────▶ [ 2. Walking Back: FRONTS (Y) ]`
- **Giant Shutter Button**: Oversized 170px circular shutter target with high-contrast color scheme (Green for Rears, Blue for Fronts).
- **Turnaround Callout Card**:
  - Automatically pops up as soon as rear photos are captured.
  - Pulses with high-visibility gold border:
    `🔄 REACHED THE END OF THE ROW? TURN AROUND ➔ SWITCH TO FRONTS`
  - Tapping this button instantly switches camera mode, vibrates the phone, and flips guidance into return mode.
- **Return Leg Match Tracker**:
  - Live progress bar: Displays `Matching X of Y bikes (Z%)`.
  - Counter calculates reverse bike index: e.g. `Bike #10 (1 of 10)`.
- **Recent Captures Strip & Retake**:
  - Horizontal preview reel shows captured thumbnails with orientation badges (`R1`, `R2`, `F1`, etc.).
  - **"↩️ Retake Last" Button**: Allows one-tap deletion and retake if the operator sneezes, moves, or captures a blurry shot.
- **Finish Batch Validation**:
  - Checks count symmetry before upload completion.
  - Warns operator if rear count != front count before submitting.

---

## 4. Offline Resilience & Zero Data Loss Guarantee

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  Native Camera  │ ────▶ │   IndexedDB     │ ────▶ │  Background     │
│  Full Megapixel │       │   Outbox Store  │       │  Sync Queue     │
└─────────────────┘       └─────────────────┘       └─────────────────┘
                                   │                         │
                          Safe from page reload     Auto-drains when Wi-Fi
                          or signal disconnect      signal is restored
                                                             │
                                                             ▼
                                                    ┌─────────────────┐
                                                    │ Laptop Backend  │
                                                    │ /api/mobile/... │
                                                    └─────────────────┘
```

1. **Local IndexedDB Outbox**: Every captured image blob is immediately saved to browser IndexedDB storage (`ITMS_Mobile_DB`) before any network transmission is attempted.
2. **Signal Sensing Heartbeat**: The mobile app pings `/api/mobile/ping/` every 3 seconds:
   - Green Pill (`<100ms`): Online, high signal.
   - Yellow Pill (`>100ms`): Weak signal.
   - Red Pill: Offline / Out of range. Red warning banner alerts technician that offline buffering is active.
3. **Auto-Drain Worker**: When the technician walks back within Wi-Fi range of the laptop, the outbox automatically drains in the background, uploading pending photos without operator intervention.
4. **Outbox Tray & Drawer**: Persistent bottom tray displays outbox count (`Outbox: 0 Pending (Synced ✓)`). Technicians can expand the drawer to review pending photos or tap **"Sync Now 🔄"**.

---

## 5. Multi-Phone Concurrency & Laptop Load Protection

To safeguard the operator's laptop from memory saturation, CPU spikes, and GPU contention during deep learning OCR inference, the system implements a centralized session registry (`core.services.device_session_service`):

### 5.1 Connection Capacity Limit
- **Configurable Limit**: `settings.MAX_MOBILE_COMPANION_DEVICES` (default: **2 concurrent phones**).
- **Lease Heartbeat & Pruning**: Every active mobile client reports its unique `device_id`, device friendly name, and mode via `/api/mobile/ping/`. Inactive sessions are automatically pruned after a 20-second TTL (`settings.MOBILE_DEVICE_SESSION_TTL_SECONDS`).
- **HTTP 429 Graceful Rejection**: When a 3rd phone attempts to connect, the server responds with:
  ```json
  {
    "pong": false,
    "allowed": false,
    "error": "DEVICE_LIMIT_EXCEEDED",
    "message": "Server capacity limit reached: 2 of 2 phones are already connected. Please disconnect other devices.",
    "active_count": 2,
    "max_allowed": 2
  }
  ```
- **Operator Blocking Modal**: The mobile phone displays a high-visibility blocking card showing currently connected phone names and an instant **[ 🔄 Try Again ]** button.
- **Graceful Disconnect Button**: Operators can tap **"📱 Disconnect"** in the top navigation bar or trigger `beforeunload` to immediately release their slot for colleagues.
- **Host Dashboard Telemetry**: Both the Web UI Dashboard (`tab_dashboard.js`) and TUI QR pane (`dashboard_pane.py`) display live active phone badges (e.g. `📱 2/2 Phones Connected`).

---

## 6. Photo Quality, Defocus & Blur Detection with Interactive Guidance

Poorly focused, blurry, or overexposed photos taken under harsh sunlight or dark workshop corners break OCR plate recognition. The system introduces a two-tier quality validation pipeline:

### 6.1 Two-Tier Architecture
1. **Client-Side Fast Blur Estimator (<35ms)**:
   - Evaluates a 320px downsampled canvas slice in JavaScript (`mobile_companion.js`).
   - Computes local pixel gradient variance: $\Delta x^2 + \Delta y^2$.
   - Provides immediate pre-upload guidance without network latency.
2. **Server-Side Precision Assessment (`core.services.photo_quality_service`)**:
   - Computes OpenCV Laplacian Variance:
     $$\sigma^2 = \operatorname{Var}\left(\nabla^2 I\right)$$
   - **Critical Blur**: $\sigma^2 < 60$ (severe motion blur, lens smudge, or total defocus).
   - **Soft Focus Warning**: $60 \le \sigma^2 < 100$.
   - **Sharp / Optimal**: $\sigma^2 \ge 100$.
   - **Exposure Analysis**:
     - Underexposed: Mean pixel intensity $\mu < 35$ (too dark).
     - Overexposed: Mean pixel intensity $\mu > 230$ (flash glare / sunlight blowout).

### 6.2 Interactive Operator Correction Card
When a photo fails sharpness or lighting checks:
- The mobile companion activates a high-contrast amber/red banner:
  - **Issue Breakdown**: e.g., `Photo is out of focus / blurry (Sharpness: 34 / 100)`.
  - **Actionable Correction Tips**:
    - *"Hold phone steady with both hands and tap the screen on the plate before taking photo."*
    - *"Check if the phone camera lens has smudges or fingerprints."*
    - *"Adjust angle slightly to avoid direct spotlight reflection on the retro-reflective plate."*
- **Operator Autonomy**:
  - **`[ 📸 Retake Photo Now ]`**: Immediately clears the buffer and reopens the camera.
  - **`[ Keep Photo Anyway ➔ ]`**: Allows bypassing the warning for severely damaged/weathered physical plates, appending a `[Quality Warning]` annotation to the pair operator note for secondary human review.

---

## 7. Responsive Viewport Containment & Touch Lightbox

### 7.1 Intrinsic Dimension Blowout Fix
High-resolution smartphone cameras capture photos at 12MP to 48MP (3000&ndash;4000+ pixels wide). In CSS Grid layouts, column definitions using standard `1fr 1fr` default the minimum column width to `min-width: auto`. This caused raw image elements to expand to their full pixel dimensions, blowing out the viewport and pushing UI controls off-screen.
- **Root-Level Reset**: Added `img { max-width: 100%; height: auto; display: block; }`.
- **Strict Grid Constraints**:
  ```css
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  min-width: 0;
  overflow: hidden;
  ```
- All thumbnail containers are strictly bounded with fixed aspect ratios (`aspect-ratio: 4 / 3; object-fit: cover;`).

### 7.2 Touch Lightbox Modal
Tapping any thumbnail (Conveyor Angle A/B or U-Turn reel) opens `#photo-lightbox-modal`:
- Displays photo centered, constrained to `max-height: 50vh; object-fit: contain;`.
- Displays real-time quality diagnostics: Focus score pill, lighting badge, and captured timestamp.
- Features a 1-tap **"📸 Retake This Angle"** button and quick close.

---

---

## 8. Mobile Batch Management: 200 Photos / 100 Motorcycles Limit

To ensure system stability, prevent DOM bloat in batch accordions, and optimize YOLOv8 / OCR pipeline inference speeds, mobile photo ingestion is capped at **200 photos per batch** (exactly **100 motorcycles**: 100 Front + 100 Rear):

### 8.1 Sequential Batch Rollover
- **Configurable Threshold**: `settings.MAX_MOBILE_BATCH_PHOTOS = 200` (`MAX_MOBILE_BATCH_PAIRS = 100`).
- **Standard Identification**: Batches adhere strictly to the system's canonical naming convention (`BATCH-YYYYMMDD-HHMMSS-xxxxxx`) and source type (`IngestionBatch.SourceType.MOBILE`).
- **Sequential Labelling**:
  - Batch 1: `Mobile Camera Session YYYY-MM-DD #1`
  - Batch 2: `Mobile Camera Session YYYY-MM-DD #2`
  - Batch N: `Mobile Camera Session YYYY-MM-DD #N`
- **Automatic Rollover**: Once an active batch reaches 200 photos, the next photo automatically initiates a new sequential batch without interrupting operator work.
- **Manual Batch Sealing**: Operators can also start a new batch on demand via `POST /api/mobile/new-batch/`.

### 8.2 Pair Affinity Guard
A critical factory edge case occurs when photo #199 is a motorcycle's Front angle, and photo #200 is its Rear angle:
- In `core.services.vault_service.get_or_create_mobile_batch(bike_client_id=...)`, the system checks for any incomplete pair registered under the incoming `bike_client_id`.
- The complementary angle is **guaranteed to bind to the exact same batch** as its partner photo, even across the capacity boundary.
- Pairs are never split across batches, preserving 100% pairing integrity and ground truth.

### 8.3 Mobile Capacity Bar & Yard Row Limits
- **Real-Time Progress Bar**: Mobile companion displays `#batch-capacity-bar` indicating live photo consumption (`42/200 Photos (21/100 Bikes)`).
- **Rollover Notification**: When a batch fills to 200, an alert banner (`#batch-rollover-banner`) informs the operator that the next capture transitions to Batch #2.
- **Off-Conveyor Row Limits**: In U-Turn mode, outbound Rear captures are capped at 100 bikes per row to match the 200-photo batch budget.

---

## 9. Real-Time Connected Phones Telemetry & Synchronization

Previously, connected device counters required manual page reloads or lagged behind. Telemetry synchronization is now instantaneous across all system interfaces:

### 9.1 Terminal UI (TUI) Live Polling
- In `core.tui.dashboard_pane.DashboardPane`, `set_interval(2.0, self.check_network_and_mobile_status)` checks device session changes and batch counts every 2 seconds.
- QR widget and network info panel immediately update with device names:
  `📱 Phones Connected: 1/2 (Samsung A54) (Load Protected)`
  `📦 Active Mobile Batch: Mobile Camera Session 2026-09-28 #1 (42/200 photos • 21 pairs)`

### 9.2 Global Web UI Header & Dashboard Polling
- In `core/static/core/js/utils.js`, `pollMobileCompanionStatus()` queries `/api/mobile/status/` every 2.5 seconds.
- **Top Header Badge (`#header-mobile-badge`)**:
  - 0 Phones: `📱 Mobile (0/2)`
  - 1+ Phones: Pulsing green badge `📱 1/2 Connected (Pixel 7)`
  - 2 Phones: `📱 2/2 Connected (Full)`
- **Dashboard Telemetry Row (`#dash-mobile-connection-status`)**: Shows live device names and real-time batch capacity.
- **Connect Modal (`#modal-mobile-connect`)**: Updates active device IP addresses, online duration, and batch slots in real time.

---

## 10. Verification & Testing

### Test Suite Execution
Automated regression tests are implemented in `core/tests/test_mobile_companion.py`:
- `test_network_service_discovery`: Validates subnet classification (`LAPTOP_HOTSPOT`, `PHONE_HOTSPOT`, `WIFI_LAN`).
- `test_api_mobile_ping`: Confirms low-latency heartbeat response.
- `test_api_network_info`: Tests dynamic QR candidate URLs.
- `test_api_mobile_status`: Tests active session statistics and device counters.
- `test_mobile_companion_view_renders`: Verifies HTML template rendering.
- `test_on_conveyor_guided_pairing_workflow`: Tests 1:1 front & rear sequential pair linking.
- `test_off_conveyor_uturn_upload_workflow`: Verifies trajectory matching for N outbound rears and N inbound fronts.
- `test_offline_retransmission_deduplication`: Confirms SHA-256 deduplication for network reconnect retries.
- `test_device_session_limit_and_disconnect`: Validates 2-device connection limit, HTTP 429 rejection on 3rd device, and slot reuse upon disconnect.
- `test_photo_quality_assessment_service`: Verifies Laplacian blur variance, underexposure, and overexposure scoring.
- `test_mobile_upload_includes_quality_report`: Tests that upload payloads return structured quality feedback.
- `test_batch_capacity_limit_and_sequential_rollover`: Verifies 200-photo limit and `#1` ➔ `#2` sequential batch rollover.
- `test_pair_affinity_preserves_batch_across_capacity_boundary`: Confirms Front + Rear are never separated across batches.
- `test_api_mobile_new_batch`: Tests the explicit new-batch creation REST endpoint.
- `test_api_mobile_status_and_ping_batch_metrics`: Verifies real-time capacity and telemetry payload reporting.

Run the test suite:
```bash
.venv\Scripts\python.exe -m pytest core/tests/test_mobile_companion.py -v
```

---

## 11. Summary of Completed Improvements

| Component | Previous Bottleneck | Solution Implemented |
| :--- | :--- | :--- |
| **Ingestion Latency** | Manual SD card / USB transfer (15-30 min/batch). | Real-time direct wireless ingestion over local network. |
| **Conveyor Workflow** | Disorganized photos requiring manual sorting. | 1-touch Smart Shutter: Front ➔ Rear ➔ Next Bike. |
| **U-Turn Yard Walk** | Confusion at row ends, misplaced trajectory pairs. | Visual flow road + Turnaround callout banner + Return match progress. |
| **Terminal Concurrency** | TUI did not host HTTP, causing phone connection timeouts. | Built-in silent background WSGI server on launch. |
| **Network Switching** | Changing Wi-Fi broke QR code / connection. | 2s active polling & dynamic route-aware IP detection. |
| **Security Exceptions** | Django `DisallowedHost` blocked mobile browser. | Dynamic `ALLOWED_HOSTS` configuration. |
| **QR Code Usability** | QR matrix overflowed TUI cards, payload unreadable. | Standard QR library + compact half-block Unicode matrix. |
| **Viewport CSS Blowout** | Raw 12-48MP photos expanded beyond viewport. | Strict CSS Grid `minmax(0, 1fr)` + touch lightbox preview. |
| **Multi-Phone Load** | Multiple phones could overload laptop CPU/RAM. | Thread-safe session registry, 2-device limit, HTTP 429 backoff. |
| **Blurry/Defocus Photos** | Unfocused shots failed OCR without feedback. | Laplacian variance quality analysis + interactive correction card. |
| **Batch Sizing & Memory** | Unbounded batches overloaded memory and pairing. | 200-photo limit (100 Front + 100 Rear) + Pair Affinity Guard. |
| **Phone Telemetry Lag** | Connected phone count did not update live. | 2–2.5s real-time poller across TUI, Web header, and dashboard. |
