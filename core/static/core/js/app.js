/**
 * ITMS CLOSING SYSTEM - MASTER APPLICATION ORCHESTRATOR
 * Coordinates all modular subsystems, lifecycle hooks, telemetry intervals,
 * and global keyboard event routing (mirroring Textual TUI keybindings).
 */

document.addEventListener("DOMContentLoaded", () => {
    // 1. Initial data fetching across active modules
    fetchStats();
    fetchPairs();
    fetchItmsStatus();
    initDropzones();
    appendConsoleLog("ITMS Closing System initialized. Operator ready.", "info");

    // 2. Periodic telemetry polling intervals
    setInterval(fetchStats, 8000);
    setInterval(fetchItmsStatus, 25000);

    // 3. Global Keyboard Shortcuts (matching TUI keybindings 1:1)
    document.addEventListener("keydown", (e) => {
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") {
            if (e.key === "Escape") {
                e.target.blur();
            }
            return;
        }

        // Numbers 1 to 6 switch primary navigation tabs (TUI bindings)
        if (e.key >= "1" && e.key <= "6") {
            switchNavTab(parseInt(e.key));
            return;
        }

        // Functional action triggers
        const key = e.key.toLowerCase();
        if (key === "p") {
            triggerPipeline("full_pipeline");
        } else if (key === "m") {
            triggerMatching();
        } else if (key === "y") {
            triggerSyncOrders();
        } else if (key === "b") {
            triggerBatchSubmit(typeof currentQueueDateScope !== "undefined" ? currentQueueDateScope : "TODAY");
        } else if (key === "d") {
            if (activeNavTab === 4 && typeof cycleQueueDateScope === "function") {
                cycleQueueDateScope();
            } else if (activeNavTab === 3 && typeof cycleBatchesDateScope === "function") {
                cycleBatchesDateScope();
            } else if (activeNavTab === 5 && typeof cycleHistoryDateScope === "function") {
                cycleHistoryDateScope();
            } else if (activeNavTab === 1 && typeof setDashboardViewMode === "function") {
                const nextMode = (dashboardViewMode === "SHIFT") ? "ALL_TIME" : "SHIFT";
                setDashboardViewMode(nextMode);
                if (typeof showToast === "function") {
                    showToast(`Dashboard View: ${nextMode === "SHIFT" ? "Today's Shift" : "All-Time"}`, "info");
                }
            }
        } else if (key === "i") {
            openBatchUploadModal();
        } else if (key === "a" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("approve");
        } else if (key === "s" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("swap");
        } else if (key === "e" && activeNavTab === 4) {
            if (selectedPairId) openEditPlateModal();
        } else if (key === "l" && activeNavTab === 4) {
            if (selectedPairId) openLinkOrderModal();
        } else if (e.key === "Enter") {
            const subModal = document.getElementById("modal-submission-progress");
            const subConfirm = document.getElementById("sub-phase-confirm");
            if (subModal && subModal.classList.contains("active") && subConfirm && subConfirm.style.display !== "none") {
                startSubmissionExecution();
                return;
            }
            if (activeNavTab === 4 && selectedPairId) {
                executePairAction("submit");
            }
        } else if (e.key === "ArrowUp" || key === "k") {
            if (activeNavTab === 4) {
                e.preventDefault();
                navigateQueue(-1);
            }
        } else if (e.key === "ArrowDown" || key === "j") {
            if (activeNavTab === 4) {
                e.preventDefault();
                navigateQueue(1);
            }
        } else if (e.key === "Escape") {
            const subModal = document.getElementById("modal-submission-progress");
            if (subModal && subModal.classList.contains("active")) {
                if (typeof isBatchSubmitting !== "undefined" && isBatchSubmitting) {
                    minimizeSubmissionModal();
                } else {
                    closeSubmissionModal();
                }
                return;
            }
            const activeModal = document.querySelector(".modal-overlay.active, .modal-backdrop.active");
            if (activeModal) {
                activeModal.classList.remove("active");
            }
        }
    });
});
