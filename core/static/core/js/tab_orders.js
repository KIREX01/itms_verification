/**
 * core/static/core/js/tab_orders.js
 * ============================================================================
 * Controller for Direct Single-Order Installation & 1-Click Submission UI.
 * Handles:
 *   - Fetching stage & order details: GET /api/itms/orders/<ident>/install/
 *   - Step 1: Hardware fitment save: POST action=save_step1
 *   - Step 2: Dual photo dropzones (Front & Rear) with camera capture & preview
 *   - Step 3: 1-Click final submission: POST action=submit
 *   - Synchronizing status badges and toast notifications
 * ============================================================================
 */

(function () {
    "use strict";

    // Module-scoped state for active order install session
    window.currentOrderInstallSession = {
        orderNumber: null,
        orderData: null,
        stage: "UNKNOWN",
        frontPhotoFile: null,
        rearPhotoFile: null,
        isSubmitting: false,
    };

    /**
     * Retrieve CSRF token from page meta or cookies
     */
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const cookie = document.cookie
            .split("; ")
            .find((row) => row.startsWith("csrftoken="));
        return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
    }

    /**
     * Display a toast notification (falls back to alert if toast system not loaded)
     */
    function notify(message, type = "success") {
        if (typeof window.showToast === "function") {
            window.showToast(message, type);
        } else if (typeof window.showNotification === "function") {
            window.showNotification(message, type);
        } else {
            console.log(`[OrderInstall] [${type.toUpperCase()}] ${message}`);
        }
    }

    /**
     * Update visual stepper pills (Step 1, 2, 3)
     */
    function setInstallStepper(activeStep) {
        for (let i = 1; i <= 3; i++) {
            const pill = document.getElementById(`step-pill-${i}`);
            if (!pill) continue;
            if (i === activeStep) {
                pill.style.background = "rgba(59, 130, 246, 0.25)";
                pill.style.borderColor = "#3b82f6";
                pill.style.color = "#ffffff";
                pill.style.fontWeight = "800";
            } else if (i < activeStep) {
                pill.style.background = "rgba(16, 185, 129, 0.2)";
                pill.style.borderColor = "#10b981";
                pill.style.color = "#4ade80";
                pill.style.fontWeight = "700";
            } else {
                pill.style.background = "transparent";
                pill.style.borderColor = "transparent";
                pill.style.color = "rgba(255, 255, 255, 0.6)";
                pill.style.fontWeight = "600";
            }
        }
    }

    /**
     * Open Single-Order Quick Install Modal for given order identifier
     */
    function populateHardwareDatalist(datalistId, options) {
        const dl = document.getElementById(datalistId);
        if (!dl) return;
        dl.innerHTML = "";
        if (!Array.isArray(options)) return;
        options.forEach((opt) => {
            if (!opt) return;
            const val = opt.serial || opt.value;
            if (!val) return;
            const optionEl = document.createElement("option");
            optionEl.value = val;
            if (opt.text && opt.text !== val) {
                optionEl.label = opt.text;
                optionEl.textContent = opt.text;
            }
            dl.appendChild(optionEl);
        });
    }

    window.openOrderInstallModal = async function (orderIdent) {
        if (!orderIdent) return;

        const modal = document.getElementById("order-install-modal");
        if (!modal) {
            console.error("[OrderInstall] Modal #order-install-modal not found.");
            return;
        }

        // Reset state
        window.currentOrderInstallSession = {
            orderNumber: orderIdent,
            orderData: null,
            stage: "UNKNOWN",
            frontPhotoFile: null,
            rearPhotoFile: null,
            isSubmitting: false,
        };

        // Reset UI previews & fields
        const plateEl = document.getElementById("order-install-plate");
        const orderNumEl = document.getElementById("order-install-number");
        const vinEl = document.getElementById("order-install-vin");
        const stageBadge = document.getElementById("order-install-stage-badge");
        const progBanner = document.getElementById("order-install-progress-banner");

        if (plateEl) plateEl.innerText = "Loading...";
        if (orderNumEl) orderNumEl.innerText = `Order #${orderIdent}`;
        if (vinEl) vinEl.innerText = "---";
        if (stageBadge) {
            stageBadge.className = "badge badge-yellow";
            stageBadge.innerText = "Detecting Stage...";
        }
        if (progBanner) progBanner.style.display = "none";

        // Reset photo boxes
        resetPhotoDropzone("front");
        resetPhotoDropzone("rear");

        // Open modal
        modal.classList.add("active");
        modal.style.display = "flex";
        setInstallStepper(1);

        // Fetch live order stage and hardware serials from backend
        try {
            const url = `/api/itms/orders/${encodeURIComponent(orderIdent)}/install/`;
            const resp = await fetch(url, {
                headers: { Accept: "application/json" },
            });
            const data = await resp.json();

            if (!data.success) {
                notify(data.error || "Failed to load order install details.", "error");
                if (stageBadge) {
                    stageBadge.className = "badge badge-red";
                    stageBadge.innerText = "Error Loading";
                }
                return;
            }

            window.currentOrderInstallSession.orderData = data.order || {};
            window.currentOrderInstallSession.stage = data.stage || "UNKNOWN";

            const o = data.order || {};

            if (plateEl) plateEl.innerText = o.registration_number || "NO PLATE";
            if (orderNumEl) orderNumEl.innerText = `Order #${o.order_number || orderIdent}`;
            if (vinEl) vinEl.innerText = o.vin || "---";

            const whEl = document.getElementById("order-install-warehouse");
            if (whEl) whEl.innerText = o.warehouse_name || "AGM Bonded Warehouse";

            const offDateEl = document.getElementById("order-install-officer-date");
            if (offDateEl) {
                offDateEl.innerText = o.status ? `Status: ${o.status}` : "";
            }

            // Populate Step 1 Hardware inputs
            const frontSerialInput = document.getElementById("order-install-front-serial");
            const rearSerialInput = document.getElementById("order-install-rear-serial");
            const gpsIdInput = document.getElementById("order-install-gps-id");

            const frontVal = o.front_plate_serial || (data.step1_form?.front_plate?.selected_text ? data.step1_form.front_plate.selected_text.split(" ")[0] : "") || data.step1_form?.front_plate?.selected_id || "";
            const rearVal = o.rear_plate_serial || (data.step1_form?.rear_plate?.selected_text ? data.step1_form.rear_plate.selected_text.split(" ")[0] : "") || data.step1_form?.rear_plate?.selected_id || "";
            const gpsVal = o.gps_tracker_id || data.step1_form?.tracker?.selected_text || data.step1_form?.tracker?.selected_id || "";

            if (frontSerialInput) frontSerialInput.value = frontVal;
            if (rearSerialInput) rearSerialInput.value = rearVal;
            if (gpsIdInput) gpsIdInput.value = gpsVal;

            // Populate native datalists with available inventory options (Section 17.3)
            if (data.step1_form) {
                populateHardwareDatalist("order-install-front-options", data.step1_form.front_plate?.options);
                populateHardwareDatalist("order-install-rear-options", data.step1_form.rear_plate?.options);
                populateHardwareDatalist("order-install-gps-options", data.step1_form.tracker?.options);
            }

            // Format stage badge
            updateStageBadge(data.stage);

            // If existing photos / pair exist, populate them
            if (data.existing_pair) {
                const pair = data.existing_pair;
                if (pair.front_image_url) {
                    setPhotoDropzoneImage("front", pair.front_image_url, pair.plate_detected);
                }
                if (pair.rear_image_url) {
                    setPhotoDropzoneImage("rear", pair.rear_image_url, pair.plate_detected);
                }
            }
        } catch (err) {
            console.error("[OrderInstall] Error fetching order install details:", err);
            notify("Network error loading order install status.", "error");
        }
    };

    /**
     * Close Single-Order Quick Install Modal
     */
    window.closeOrderInstallModal = function () {
        const modal = document.getElementById("order-install-modal");
        if (modal) {
            modal.classList.remove("active");
            modal.style.display = "none";
        }
    };

    /**
     * Update the stage badge UI based on detected ITMS stage
     */
    function updateStageBadge(stage) {
        const stageBadge = document.getElementById("order-install-stage-badge");
        if (!stageBadge) return;

        switch (stage) {
            case "STAGE_1_INSTALLATION":
                stageBadge.className = "badge badge-yellow";
                stageBadge.innerText = "Stage 1: Fitment";
                setInstallStepper(1);
                break;
            case "STAGE_2_EVIDENCE":
                stageBadge.className = "badge badge-blue";
                stageBadge.innerText = "Stage 2: Photos";
                setInstallStepper(2);
                break;
            case "STAGE_3_CONFIRMATION":
                stageBadge.className = "badge badge-green";
                stageBadge.innerText = "Stage 3: Finalize";
                setInstallStepper(3);
                break;
            case "STAGE_ARCHIVED":
                stageBadge.className = "badge badge-green";
                stageBadge.innerText = "Installed & Archived";
                setInstallStepper(3);
                break;
            default:
                stageBadge.className = "badge badge-secondary";
                stageBadge.innerText = stage || "Ready";
                setInstallStepper(1);
                break;
        }
    }

    /**
     * Step 1: Save hardware fitment ("Save & Proceed to Photos")
     */
    window.saveOrderStep1 = async function () {
        const session = window.currentOrderInstallSession;
        if (!session.orderNumber) {
            notify("No active order selected.", "error");
            return;
        }

        const frontSerial = (document.getElementById("order-install-front-serial")?.value || "").trim();
        const rearSerial = (document.getElementById("order-install-rear-serial")?.value || "").trim();
        const gpsId = (document.getElementById("order-install-gps-id")?.value || "").trim();
        const isDryRun = document.getElementById("order-install-dry-run-chk")?.checked ?? true;

        const btn = document.getElementById("btn-save-step1");
        const originalText = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Saving...`;
        }

        const fd = new FormData();
        fd.append("action", "save_step1");
        fd.append("front_plate_id", frontSerial);
        fd.append("rear_plate_id", rearSerial);
        fd.append("tracker_id", gpsId);
        fd.append("dry_run", isDryRun ? "true" : "false");

        try {
            const url = `/api/itms/orders/${encodeURIComponent(session.orderNumber)}/install/`;
            const resp = await fetch(url, {
                method: "POST",
                headers: { "X-CSRFToken": getCsrfToken() },
                body: fd,
            });
            const data = await resp.json();

            if (!data.success) {
                notify(data.error || "Failed to save hardware fitment Step 1.", "error");
                return;
            }

            notify("Step 1 hardware fitment saved! Proceed to Step 2 evidence photos.", "success");
            session.stage = data.stage || "STAGE_2_EVIDENCE";
            updateStageBadge(session.stage);
            setInstallStepper(2);

            // Focus front photo upload
            const frontBox = document.getElementById("order-front-preview-box");
            if (frontBox) {
                frontBox.style.outline = "2px solid #3b82f6";
                setTimeout(() => (frontBox.style.outline = "none"), 2000);
            }
        } catch (err) {
            console.error("[OrderInstall] Error saving Step 1:", err);
            notify("Network error saving Step 1.", "error");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    };

    /**
     * Trigger hidden file input for photo upload / camera
     */
    window.triggerOrderPhotoUpload = function (angle) {
        const input = document.getElementById(`order-${angle}-photo-input`);
        if (input) input.click();
    };

    /**
     * Handler when photo file is chosen
     */
    window.onOrderPhotoSelected = function (angle, input) {
        if (!input || !input.files || input.files.length === 0) return;
        const file = input.files[0];

        if (angle === "front") {
            window.currentOrderInstallSession.frontPhotoFile = file;
        } else {
            window.currentOrderInstallSession.rearPhotoFile = file;
        }

        // Preview thumbnail
        const reader = new FileReader();
        reader.onload = function (e) {
            setPhotoDropzoneImage(angle, e.target.result, null);
        };
        reader.readAsDataURL(file);

        // Advance stepper to 3 if both photos ready
        const sess = window.currentOrderInstallSession;
        if (sess.frontPhotoFile && sess.rearPhotoFile) {
            setInstallStepper(3);
        }
    };

    /**
     * Set image thumbnail inside dropzone box
     */
    function setPhotoDropzoneImage(angle, imageUrl, ocrPlate) {
        const placeholder = document.getElementById(`order-${angle}-placeholder`);
        const img = document.getElementById(`order-${angle}-img`);
        const badge = document.getElementById(`order-${angle}-status-badge`);
        const ocrPill = document.getElementById(`order-${angle}-ocr-pill`);

        if (placeholder) placeholder.style.display = "none";
        if (img) {
            img.src = imageUrl;
            img.style.display = "block";
        }
        if (badge) {
            badge.className = "badge badge-green";
            badge.innerText = "✓ Photo Attached";
        }
        if (ocrPill && ocrPlate) {
            ocrPill.className = "badge badge-yellow";
            ocrPill.innerText = `OCR: ${ocrPlate}`;
        }
    }

    /**
     * Reset photo dropzone to initial empty state
     */
    function resetPhotoDropzone(angle) {
        const placeholder = document.getElementById(`order-${angle}-placeholder`);
        const img = document.getElementById(`order-${angle}-img`);
        const badge = document.getElementById(`order-${angle}-status-badge`);
        const ocrPill = document.getElementById(`order-${angle}-ocr-pill`);
        const input = document.getElementById(`order-${angle}-photo-input`);

        if (placeholder) placeholder.style.display = "block";
        if (img) {
            img.src = "";
            img.style.display = "none";
        }
        if (badge) {
            badge.className = "badge badge-dark";
            badge.innerText = "Empty";
        }
        if (ocrPill) {
            ocrPill.className = "badge badge-dark";
            ocrPill.innerText = "OCR: ---";
        }
        if (input) input.value = "";
    }

    /**
     * Step 3: Finalize & Submit to ITMS
     */
    window.submitOrderDirect = async function () {
        const session = window.currentOrderInstallSession;
        if (!session.orderNumber) {
            notify("No active order selected.", "error");
            return;
        }

        if (session.isSubmitting) return;

        const isDryRun = document.getElementById("order-install-dry-run-chk")?.checked ?? true;
        const frontSerial = (document.getElementById("order-install-front-serial")?.value || "").trim();
        const rearSerial = (document.getElementById("order-install-rear-serial")?.value || "").trim();
        const gpsId = (document.getElementById("order-install-gps-id")?.value || "").trim();

        const progBanner = document.getElementById("order-install-progress-banner");
        const progTitle = document.getElementById("order-install-progress-title");
        const progDesc = document.getElementById("order-install-progress-desc");
        const submitBtn = document.getElementById("btn-submit-order-install");

        // Prepare multipart form data
        const fd = new FormData();
        fd.append("action", "submit");
        fd.append("dry_run", isDryRun ? "true" : "false");
        fd.append("front_plate_id", frontSerial);
        fd.append("rear_plate_id", rearSerial);
        fd.append("tracker_id", gpsId);

        if (session.frontPhotoFile) {
            fd.append("front_photo", session.frontPhotoFile);
        }
        if (session.rearPhotoFile) {
            fd.append("rear_photo", session.rearPhotoFile);
        }

        // UI Loading state
        session.isSubmitting = true;
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Submitting...`;
        }
        if (progBanner) {
            progBanner.style.display = "flex";
            if (progTitle) progTitle.innerText = "1/3 Submitting Hardware Fitment...";
            if (progDesc) progDesc.innerText = "Contacting ITMS portal to register serials...";
        }

        setTimeout(() => {
            if (session.isSubmitting && progTitle) {
                progTitle.innerText = "2/3 Uploading Dual Evidence Photos...";
                if (progDesc) progDesc.innerText = "Ingesting front & rear plate photos to vault...";
            }
        }, 1200);

        setTimeout(() => {
            if (session.isSubmitting && progTitle) {
                progTitle.innerText = "3/3 Finalizing Order & Dispatching Telematics...";
                if (progDesc) progDesc.innerText = "Writing audit trail and confirming installation...";
            }
        }, 2500);

        try {
            const url = `/api/itms/orders/${encodeURIComponent(session.orderNumber)}/install/`;
            const resp = await fetch(url, {
                method: "POST",
                headers: { "X-CSRFToken": getCsrfToken() },
                body: fd,
            });
            const data = await resp.json();

            if (!data.success) {
                notify(data.error || "Order submission failed.", "error");
                return;
            }

            // Success feedback
            const simText = data.dry_run ? " [Simulation Mode]" : "";
            notify(`🎉 Order #${data.order_number || session.orderNumber} finalized and submitted successfully!${simText}`, "success");

            // Close modal
            window.closeOrderInstallModal();

            // Refresh table row status to INSTALLED
            updateTableRowToSubmitted(data.order_number || session.orderNumber);

            // Re-fetch ITMS orders if available
            if (typeof window.fetchItmsExplorerOrders === "function") {
                window.fetchItmsExplorerOrders();
            }
        } catch (err) {
            console.error("[OrderInstall] Direct submission error:", err);
            notify("Network error submitting order installation.", "error");
        } finally {
            session.isSubmitting = false;
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = `<span>🚀 Finalize &amp; Submit to ITMS</span>`;
            }
            if (progBanner) {
                progBanner.style.display = "none";
            }
        }
    };

    /**
     * Update table row badge to INSTALLED / SUBMITTED in real-time
     */
    function updateTableRowToSubmitted(orderNumber) {
        const rows = document.querySelectorAll("#itms-explorer-tbody tr");
        rows.forEach((row) => {
            if (row.innerText.includes(orderNumber)) {
                const statusCell = row.querySelector("td:nth-child(4) .badge");
                if (statusCell) {
                    statusCell.className = "badge badge-green";
                    statusCell.innerText = "INSTALLED";
                }
            }
        });
    }

    /**
     * Hook into Escape key to close modal
     */
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            const modal = document.getElementById("order-install-modal");
            if (modal && modal.classList.contains("active")) {
                window.closeOrderInstallModal();
            }
        }
    });

    console.log("[OrderInstall] Controller tab_orders.js initialized.");
})();
