/**
 * ITMS CLOSING SYSTEM - GLOBAL APPLICATION STATE
 * Central reactive state store shared across all modules.
 */

// Global Navigation State
var activeNavTab = 1; // 1: Dashboard, 2: ITMS, 3: Batches, 4: Queue, 5: History, 6: Settings

// Review Queue State
var currentQueueFilter = "ALL";
var currentQueueDateScope = "TODAY"; // "TODAY" | "ACTIVE_BATCH" | "CARRYOVER" | "ALL"
var pairsData = [];
var selectedPairId = null;
var selectedPairDetail = null;

// ITMS Hub & Explorer State
var itmsCurrentSubview = "CONNECT"; // "CONNECT" | "ORDERS" | "ARCHIVE"
var itmsExplorerSource = "live"; // "live" | "local"
var itmsExplorerPage = 1;
var itmsExplorerSearch = "";
var itmsExplorerOrders = [];
var selectedItmsOrder = null;

// Batches & Ingestion State
var currentBatchesDateScope = "TODAY"; // "TODAY" | "ACTIVE_BATCH" | "CARRYOVER" | "ALL"
var batchesData = [];
var batchDetailsCache = {};
var expandedBatches = new Set();
var batchSearchQuery = "";
var tabSelectedFrontFiles = [];
var tabSelectedRearFiles = [];

// History & Audit State
var currentHistoryFilter = "ALL";
var currentHistoryDateScope = "TODAY"; // "TODAY" | "ALL"
var historyItemsMap = {};
var activeHistoryLog = null;

// Dashboard & Telemetry State
var dashboardViewMode = "SHIFT"; // "SHIFT" | "ALL_TIME"
var telemetryShiftStats = null;
var telemetryTotalStats = null;

// Background Task State
var pipelinePollInterval = null;
