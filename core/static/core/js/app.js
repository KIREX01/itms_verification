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
            triggerBatchSubmit();
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
        } else if (e.key === "Enter" && activeNavTab === 4) {
            if (selectedPairId) executePairAction("submit");
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
            const activeModal = document.querySelector(".modal-backdrop.active");
            if (activeModal) {
                activeModal.classList.remove("active");
            }
        }
    });
});
