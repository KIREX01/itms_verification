/**
 * ITMS CLOSING SYSTEM - PRIMARY NAVIGATION CONTROLLER
 * Controls switching between Tabs 1 to 6 and lazy-loads tab data on entry.
 */

function switchNavTab(tabIndex) {
    activeNavTab = tabIndex;
    const tabNames = {
        1: "dashboard",
        2: "itms",
        3: "batches",
        4: "queue",
        5: "history",
        6: "settings",
    };

    for (let i = 1; i <= 6; i++) {
        const btn = document.getElementById(`nav-tab-${i}`);
        const pane = document.getElementById(`pane-${tabNames[i]}`);
        if (btn) btn.classList.toggle("active", i === tabIndex);
        if (pane) pane.classList.toggle("active", i === tabIndex);
    }

    // Lazy load tab data on entry
    if (tabIndex === 2 && itmsCurrentSubview !== "CONNECT" && itmsExplorerOrders.length === 0) {
        fetchItmsExplorerOrders();
    } else if (tabIndex === 3) {
        fetchBatchesList();
    } else if (tabIndex === 5) {
        fetchHistoryEvents();
    } else if (tabIndex === 6) {
        checkForUpdates(false);
        if (typeof loadVaultSettings === "function") {
            loadVaultSettings();
        }
    }
}
