/**
 * ITMS CLOSING SYSTEM - PRIMARY NAVIGATION CONTROLLER
 * Controls switching between Tabs 1 to 8 and lazy-loads tab data on entry.
 * Supported Order:
 * 1: Dashboard
 * 2: ITMS
 * 3: Batches
 * 4: Queue
 * 5: Stock
 * 6: Reports
 * 7: Settings
 * 8: History
 */

let activeNavTab = 1;

const NAV_TAB_NAMES = {
    1: "dashboard",
    2: "itms",
    3: "batches",
    4: "queue",
    5: "stock",
    6: "reports",
    7: "settings",
    8: "history",
};

const NAV_NAME_TO_INDEX = {
    "dashboard": 1,
    "itms": 2,
    "batches": 3,
    "batch": 3,
    "queue": 4,
    "stock": 5,
    "reports": 6,
    "report": 6,
    "settings": 7,
    "setting": 7,
    "history": 8,
};

function switchNavTab(target) {
    let tabIndex = 1;
    if (typeof target === "number") {
        tabIndex = target;
    } else if (typeof target === "string") {
        const clean = target.toLowerCase().trim();
        tabIndex = NAV_NAME_TO_INDEX[clean] || parseInt(clean, 10) || 1;
    }

    if (tabIndex < 1 || tabIndex > 8) {
        tabIndex = 1;
    }

    activeNavTab = tabIndex;

    // Toggle active state on tab buttons and workspace panes
    for (let i = 1; i <= 8; i++) {
        const btn = document.getElementById(`nav-tab-${i}`);
        const pane = document.getElementById(`pane-${NAV_TAB_NAMES[i]}`);
        if (btn) btn.classList.toggle("active", i === tabIndex);
        if (pane) pane.classList.toggle("active", i === tabIndex);
    }

    // Auto-close mobile menu on tab selection
    closeMobileMenu();

    // Lazy load tab data on entry
    if (tabIndex === 1) {
        if (typeof fetchStats === "function") {
            void fetchStats();
        }
    } else if (tabIndex === 2) {
        if (typeof itmsCurrentSubview !== "undefined" && itmsCurrentSubview !== "CONNECT" && typeof itmsExplorerOrders !== "undefined" && itmsExplorerOrders.length === 0) {
            if (typeof fetchItmsExplorerOrders === "function") {
                void fetchItmsExplorerOrders();
            }
        }
    } else if (tabIndex === 3) {
        if (typeof fetchBatchesList === "function") {
            void fetchBatchesList();
        }
    } else if (tabIndex === 4) {
        if (typeof fetchPairs === "function") {
            void fetchPairs();
        }
    } else if (tabIndex === 5) {
        if (typeof stockToday === "function") {
            stockToday();
        }
    } else if (tabIndex === 6) {
        if (typeof fetchReportTotals === "function") {
            void fetchReportTotals();
        }
    } else if (tabIndex === 7) {
        if (typeof checkForUpdates === "function") {
            void checkForUpdates(false);
        }
        if (typeof loadVaultSettings === "function") {
            void loadVaultSettings();
        }
    } else if (tabIndex === 8) {
        if (typeof fetchHistoryEvents === "function") {
            void fetchHistoryEvents();
        }
    }
}

/**
 * Mobile Navigation Drawer Controllers
 */
function openMobileMenu() {
    const nav = document.getElementById("mobile-nav-menu");
    const backdrop = document.getElementById("mobile-nav-backdrop");
    if (nav) nav.classList.add("show-mobile");
    if (backdrop) backdrop.classList.add("active");
    document.body.classList.add("mobile-menu-open");
}

function closeMobileMenu() {
    const nav = document.getElementById("mobile-nav-menu");
    const backdrop = document.getElementById("mobile-nav-backdrop");
    if (nav) nav.classList.remove("show-mobile");
    if (backdrop) backdrop.classList.remove("active");
    document.body.classList.remove("mobile-menu-open");
}

function toggleMobileMenu() {
    const nav = document.getElementById("mobile-nav-menu");
    if (nav && nav.classList.contains("show-mobile")) {
        closeMobileMenu();
    } else {
        openMobileMenu();
    }
}

// Global listener to close mobile menu on Escape key
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        closeMobileMenu();
    }
});
