"""
Core Services Layer for ITMS Verification Copilot.

Organized into discrete domain boundaries:

1. Core Domain (Vehicle Verification & Lifecycle):
    - pipeline_runner: Multi-stage AI detection, OCR, and matching pipeline
    - order_sync: InstallationOrder synchronization and cache manager
    - submission_worker: Asynchronous ITMS batch submission queue worker
    - plate_lifecycle_service: State machine for vehicle license plates
    - photo_quality_service: Image quality, blur, and exposure assessment
    - camera_naming: Evidence image file naming and metadata extraction
    - vault_service: Secure filesystem vault storage, hashing, and deduplication
    - viewer: Evidence photo side-by-side inspection

2. ITMS External Integration Domain:
    - itms_web_client: Web portal session scraping and automation
    - itms_client: Direct REST API client
    - itms_mock: Simulated ITMS backend for testing

3. Bond & Inventory Domain:
    - stock_monitoring_service: Real-time bond inventory reconciliation & ledger
    - bond_service: Bond warehouse workflows and transfer tracking
    - kit_provisioning_service: Plate kit allocations and series tracking
    - report_service: Operational reports and daily reconciliation metrics
    - export_service: Shift CSV and structured data exports

4. Mobile Companion Domain:
    - device_session_service: Mobile device pairing and proximity session state
    - network_service: Network interface discovery and LAN IP detection
    - qr_generator: Mobile pairing and inspection QR matrix generation
    - ssl_service: Local HTTPS SSL certificate lifecycle

5. Infrastructure & Security Domain:
    - auth_service: Session management, DPAPI authentication tokens
    - secure_storage: Encrypted credential storage at rest
    - config_service: Central runtime configuration and settings persistence
    - update_service: GitHub releases updater and rollback protection
    - maintenance_service: Database integrity and filesystem housekeeping
    - background_web_service: Background web server process daemon
    - file_dialog: Native OS file picker integration
"""
