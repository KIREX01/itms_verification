"""
Central configuration service for ITMS Verification Copilot.

Manages system-wide settings persisted in `config.json` located at the project root.
Ensures zero-configuration portability on other machines (defaults to SQLite),
enforces operator vs developer command permissions, and provides dynamic configuration
for safety, vision pipeline, network timeouts, and storage lifecycle.
"""
import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "system": {
        "app_title": "ITMS Verification Copilot",
        "developer_mode": False,       # When False, developer tests and tools are hidden
        "theme": "dark",
        "show_safety_guarantee": False, # When True, displays safety guarantee card in ITMS Hub based on loaded config
    },
    "sync": {
        "auto_sync_enabled": True,             # Background active queue & shift synchronization
        "auto_sync_interval_seconds": 60,      # Background sync cycle frequency (seconds)
        "shift_target_date_mode": "today",     # "today", "yesterday", or "manual"
        "manual_target_date": "",              # e.g. "30.09.2026" or "300926"
        "default_export_directory": "exports", # Shift reconciliation spreadsheet export folder
    },
    "crawl": {
        "max_active_pages": 25,                # Max pages to crawl for active orders (20 records/page)
        "max_archive_pages": 50,               # Max pages to crawl for archive orders
        "max_kit_pages": 25,                   # Max pages to crawl for installation kits
    },
    "database": {
        "engine": "sqlite",                    # "sqlite" (default for all machines) or "postgresql"
        "sqlite_file": "db.sqlite3",
        "sqlite_busy_timeout_ms": 30000,       # Milliseconds to wait for SQLite locks before timeout
        "batch_write_size": 200,               # Bulk atomic commit chunk size
        "postgres_host": "localhost",
        "postgres_port": "5432",
        "postgres_db": "itms",
        "postgres_user": "postgres",
        "postgres_password": "",
    },
    "submission": {
        "dry_run_mode": True,                  # Safe simulation mode for ITMS submissions
        "submit_step3": True,                  # Auto-finalize Step 3 remote installation
        "request_timeout_seconds": 15,
        "circuit_breaker_threshold": 3,
        "max_retries": 3,
    },
    "vision": {
        "use_paddleocr": True,
        "adaptive_ensemble_voting": True,
        "positional_disambiguation": True,
        "auto_preprocess_ingest": True,
        "min_ocr_confidence": 0.55,
        "detector_conf_threshold": 0.35,
        "yolo_weights": "models/license-plate-finetune-v1n.pt",  # Path to YOLOv8/YOLOv11 weights (.pt/.onnx)
    },
    "storage": {
        "vault_path": "media/vault",           # Root directory where photographic evidence is stored
        "prompt_vault_on_startup": True,      # Offer user choice of vault location on application launch
        "vault_retention_days": 7,
        "export_retention_days": 30,
        "crops_retention_days": 7,
    },
    "matcher": {
        "uturn_threshold_seconds": 1800,       # Turnaround time delta threshold (seconds) for U-Turn walk
    },
    "network": {
        "itms_base_url": "https://stock.itms.ug",
        "login_endpoint": "/site/login",
        "http_timeout_seconds": 15,            # Socket timeout for ITMS HTTP requests
        "pool_connections": 10,                # Persistent connection pool size
        "pool_maxsize": 20,                    # Maximum active connection pool size
    },
    "compression": {
        "enabled": True,                       # Smart on-the-fly multipart photo compression
        "max_dimension": 1920,                 # Max dimension in px (LANCZOS downsampling)
        "jpeg_quality": 88,                    # JPEG quality for multipart stream
    },
    "outbox": {
        "enabled": True,                       # Auto-transition network failures to OFFLINE_OUTBOX
        "auto_sync_interval_seconds": 15,      # Background heartbeat ping and drain interval
    },
    "bond": {
        "active_bond_code": "AGM",
        "active_bond_name": "AGM Bonded Warehouse",
        "strict_bond_scoping": True,
        "available_bonds": [
            {"code": "AGM", "name": "AGM Bonded Warehouse", "warehouse_id": "agm-bond"},
            {"code": "KLA_CENTRAL", "name": "Kampala Central Bond", "warehouse_id": "kla-central"},
            {"code": "JINJA", "name": "Jinja Regional Bond", "warehouse_id": "jinja-bond"},
            {"code": "MBALE", "name": "Mbale Bond", "warehouse_id": "mbale-bond"},
            {"code": "MBR", "name": "Mbarara Bond", "warehouse_id": "mbarara-bond"},
        ],
    },
}

USER_SETTINGS_SCHEMA: Dict[str, Dict[str, Any]] = {
    "bond.active_bond_code": {
        "label": "Active Bond Facility",
        "description": "Selected warehouse facility for plate allocation, stock, and fitment operations.",
        "type": "select",
        "default": "AGM",
        "category": "Facility & Operations",
    },
    "bond.strict_bond_scoping": {
        "label": "Strict Bond Scoping",
        "description": "Filter inventory and shift reconciliation strictly to the selected bonded warehouse.",
        "type": "bool",
        "default": True,
        "category": "Facility & Operations",
    },
    "submission.dry_run_mode": {
        "label": "Dry-Run Safety Mode",
        "description": "Simulate ITMS uploads and approvals locally without executing remote mutating writes.",
        "type": "bool",
        "default": True,
        "category": "Safety & Submission",
    },
    "submission.submit_step3": {
        "label": "Auto-Submit Step 3 Confirmation",
        "description": "Automatically finalize remote installation record on ITMS when evidence photos upload.",
        "type": "bool",
        "default": True,
        "category": "Safety & Submission",
    },
    "sync.auto_sync_enabled": {
        "label": "Auto Shift Sync",
        "description": "Periodically poll ITMS active orders, morning installation kits, and shift archive in background.",
        "type": "bool",
        "default": True,
        "category": "Shift & Synchronization",
    },
    "sync.auto_sync_interval_seconds": {
        "label": "Sync Interval (seconds)",
        "description": "Time between automated background sync passes (default: 60s).",
        "type": "int",
        "default": 60,
        "category": "Shift & Synchronization",
    },
    "sync.shift_target_date_mode": {
        "label": "Shift Date Selection Mode",
        "description": "Target date rule for reconciliation ledgers: 'today', 'yesterday', or 'manual'.",
        "type": "select",
        "default": "today",
        "options": ["today", "yesterday", "manual"],
        "category": "Shift & Synchronization",
    },
    "sync.manual_target_date": {
        "label": "Manual Shift Date",
        "description": "Specific calendar date to reconcile when mode is 'manual' (e.g. 30.09.2026 or 300926).",
        "type": "str",
        "default": "",
        "category": "Shift & Synchronization",
    },
    "sync.default_export_directory": {
        "label": "CSV Export Directory",
        "description": "Directory where shift reconciliation CSV reports and ledgers are generated.",
        "type": "str",
        "default": "exports",
        "category": "Shift & Synchronization",
    },
    "storage.vault_path": {
        "label": "Evidence Vault Folder",
        "description": "Local directory where photographic evidence and camera files are stored.",
        "type": "str",
        "default": "media/vault",
        "category": "Storage & Vault",
    },
    "storage.prompt_vault_on_startup": {
        "label": "Prompt for Vault on Startup",
        "description": "Display folder confirmation prompt whenever the application launches.",
        "type": "bool",
        "default": True,
        "category": "Storage & Vault",
    },
    "system.theme": {
        "label": "UI Theme",
        "description": "Terminal and display styling theme.",
        "type": "select",
        "default": "dark",
        "options": ["dark", "light", "high_contrast"],
        "category": "Display & Preferences",
    },
}

DEVELOPER_SETTINGS_SCHEMA: Dict[str, Dict[str, Any]] = {
    "system.developer_mode": {
        "label": "Developer Mode Enabled",
        "description": "Enables engineering diagnostic tools, raw telemetry, and advanced parameters.",
        "type": "bool",
        "default": False,
        "category": "Diagnostics",
    },
    "crawl.max_active_pages": {
        "label": "Max Active Orders Pages",
        "description": "Maximum pages crawled when fetching active installation orders (default: 25).",
        "type": "int",
        "default": 25,
        "category": "Crawl & Pagination",
    },
    "crawl.max_archive_pages": {
        "label": "Max Archive Orders Pages",
        "description": "Maximum pages crawled when scanning shift archive orders (default: 50).",
        "type": "int",
        "default": 50,
        "category": "Crawl & Pagination",
    },
    "crawl.max_kit_pages": {
        "label": "Max Kit Catalog Pages",
        "description": "Maximum pages crawled when scanning warehouse stock kits (default: 25).",
        "type": "int",
        "default": 25,
        "category": "Crawl & Pagination",
    },
    "network.http_timeout_seconds": {
        "label": "HTTP Request Timeout (seconds)",
        "description": "Socket timeout before aborting and retrying ITMS requests (default: 15s).",
        "type": "int",
        "default": 15,
        "category": "Network & Sockets",
    },
    "network.pool_connections": {
        "label": "HTTP Pool Connections",
        "description": "Number of persistent connection pools to maintain (default: 10).",
        "type": "int",
        "default": 10,
        "category": "Network & Sockets",
    },
    "network.pool_maxsize": {
        "label": "HTTP Pool Max Sockets",
        "description": "Maximum connection pool socket capacity (default: 20).",
        "type": "int",
        "default": 20,
        "category": "Network & Sockets",
    },
    "submission.circuit_breaker_threshold": {
        "label": "Circuit Breaker Threshold",
        "description": "Consecutive network/server failures before pausing automated requests.",
        "type": "int",
        "default": 3,
        "category": "Network & Sockets",
    },
    "database.sqlite_busy_timeout_ms": {
        "label": "SQLite Busy Timeout (ms)",
        "description": "Lock contention timeout in milliseconds before raising OperationalError (default: 30000ms).",
        "type": "int",
        "default": 30000,
        "category": "Database Performance",
    },
    "database.batch_write_size": {
        "label": "Database Batch Write Chunk",
        "description": "Record batch size for chunked bulk updates and inserts (default: 200).",
        "type": "int",
        "default": 200,
        "category": "Database Performance",
    },
    "vision.min_ocr_confidence": {
        "label": "Minimum OCR Confidence",
        "description": "Confidence threshold below which OCR detections are flagged for operator review (0.0 to 1.0).",
        "type": "float",
        "default": 0.55,
        "category": "Vision Pipeline",
    },
    "vision.detector_conf_threshold": {
        "label": "YOLO Detector Confidence",
        "description": "Bounding box threshold for YOLO vehicle plate detection (0.0 to 1.0).",
        "type": "float",
        "default": 0.35,
        "category": "Vision Pipeline",
    },
    "vision.yolo_weights": {
        "label": "YOLO Neural Weights (.pt / .onnx)",
        "description": "Local path or Hugging Face model ID for YOLOv8/YOLOv11 vehicle license plate detector weights.",
        "type": "string",
        "default": "models/license-plate-finetune-v1n.pt",
        "category": "Vision Pipeline",
    },
    "matcher.uturn_threshold_seconds": {
        "label": "U-Turn Threshold (seconds)",
        "description": "Turnaround time delta for Tier 2 reverse U-turn walk alignment (default: 1800s).",
        "type": "int",
        "default": 1800,
        "category": "Matcher",
    },
    "compression.enabled": {
        "label": "Multipart Compression",
        "description": "Downscale photos in-memory during HTTP upload (master photos remain untouched).",
        "type": "bool",
        "default": True,
        "category": "Compression & Media",
    },
    "compression.max_dimension": {
        "label": "Max Photo Dimension (px)",
        "description": "Maximum width/height in px for streamed photos (LANCZOS downsampling).",
        "type": "int",
        "default": 1920,
        "category": "Compression & Media",
    },
    "compression.jpeg_quality": {
        "label": "JPEG Compression Quality",
        "description": "JPEG encoding quality level (1 to 100).",
        "type": "int",
        "default": 88,
        "category": "Compression & Media",
    },
    "storage.vault_retention_days": {
        "label": "Vault Retention (Days)",
        "description": "Days to preserve evidence photos in local vault before pruning.",
        "type": "int",
        "default": 7,
        "category": "Lifecycle Policies",
    },
    "storage.export_retention_days": {
        "label": "Export Retention (Days)",
        "description": "Days to retain CSV and Excel exports.",
        "type": "int",
        "default": 30,
        "category": "Lifecycle Policies",
    },
    "storage.crops_retention_days": {
        "label": "Crops Retention (Days)",
        "description": "Days to retain cropped plate images.",
        "type": "int",
        "default": 7,
        "category": "Lifecycle Policies",
    },
}


def _get_project_root() -> Path:
    """Resolves project root directory."""
    try:
        from django.conf import settings
        if getattr(settings, "BASE_DIR", None):
            return Path(settings.BASE_DIR)
    except Exception:
        pass
    # Fallback to grandparent directory of this file
    return Path(__file__).resolve().parent.parent.parent


def get_config_path(base_dir: Optional[Path] = None) -> Path:
    """Returns absolute path to config.json in secure storage directory."""
    from core.services.secure_storage import get_secure_config_path
    return get_secure_config_path(base_dir)


def _deep_merge(base: dict, update: dict) -> dict:
    """Recursively merges update into base, preserving defaults for missing keys."""
    res = copy.deepcopy(base)
    for k, v in update.items():
        if k in res and isinstance(res[k], dict) and isinstance(v, dict):
            res[k] = _deep_merge(res[k], v)
        else:
            res[k] = v
    return res


def load_config(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads configuration from config.json.
    Creates default config.json if not present.
    Performs recursive merge to ensure any newly added keys are always populated.
    """
    cfg_path = get_config_path(base_dir)
    if not cfg_path.is_file():
        default_cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(default_cfg, base_dir)
        return default_cfg

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = _deep_merge(DEFAULT_CONFIG, data)
        return merged
    except Exception as exc:
        logger.warning("Error loading %s: %s. Returning defaults.", cfg_path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config_data: Dict[str, Any], base_dir: Optional[Path] = None) -> bool:
    """Saves configuration dictionary solely to secure/config.json."""
    cfg_path = get_config_path(base_dir)
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cfg_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        temp_path.replace(cfg_path)
        logger.info("System configuration saved to %s", cfg_path)
        return True
    except Exception as exc:
        logger.error("Failed to save configuration to %s: %s", cfg_path, exc)
        return False


CONFIG_ALIASES: Dict[str, str] = {
    "shift.target_date_mode": "sync.shift_target_date_mode",
    "shift.manual_target_date": "sync.manual_target_date",
    "export.default_directory": "sync.default_export_directory",
    "sync.export_directory": "sync.default_export_directory",
    "network.http_timeout_seconds": "submission.request_timeout_seconds",
    "submission.smart_compression_enabled": "compression.enabled",
    "compression.smart_compression_enabled": "compression.enabled",
    "network.offline_outbox_enabled": "outbox.enabled",
    "submission.offline_outbox_enabled": "outbox.enabled",
    "network.auto_sync_interval_seconds": "outbox.auto_sync_interval_seconds",
    "yolo_weights": "vision.yolo_weights",
    "plate_yolo_weights": "vision.yolo_weights",
    "vision.weights": "vision.yolo_weights",
}


def get_setting(key_path: str, default: Any = None, base_dir: Optional[Path] = None) -> Any:
    """
    Reads a dotted key path from config (e.g. 'system.developer_mode').
    Supports aliases for cross-version compatibility.
    """
    target_key = CONFIG_ALIASES.get(key_path, key_path)
    config = load_config(base_dir)
    keys = target_key.split(".")
    curr = config
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return default
    return curr


def set_setting(key_path: str, value: Any, base_dir: Optional[Path] = None) -> bool:
    """
    Updates a dotted key path in config.json.
    Supports aliases to maintain backward compatibility.
    """
    target_key = CONFIG_ALIASES.get(key_path, key_path)
    config = load_config(base_dir)
    keys = target_key.split(".")
    curr = config
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = value
    return save_config(config, base_dir)


def get_user_settings(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Returns all operator-facing settings populated with current values and schema metadata."""
    result: Dict[str, Any] = {}
    for key, meta in USER_SETTINGS_SCHEMA.items():
        val = get_setting(key, meta.get("default"), base_dir)
        item = copy.deepcopy(meta)
        item["value"] = val
        result[key] = item
    return result


def get_developer_settings(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Returns all engineering-facing settings populated with current values and schema metadata."""
    result: Dict[str, Any] = {}
    for key, meta in DEVELOPER_SETTINGS_SCHEMA.items():
        val = get_setting(key, meta.get("default"), base_dir)
        item = copy.deepcopy(meta)
        item["value"] = val
        result[key] = item
    return result


def set_user_setting(key_path: str, value: Any, base_dir: Optional[Path] = None) -> bool:
    """Updates an operator-facing setting key."""
    return set_setting(key_path, value, base_dir)


def set_developer_setting(key_path: str, value: Any, base_dir: Optional[Path] = None) -> bool:
    """Updates an engineering-facing setting key."""
    return set_setting(key_path, value, base_dir)


def get_active_bond(base_dir: Optional[Path] = None) -> Dict[str, str]:
    """Returns active bond code and human-readable bond name."""
    raw_code = get_setting("bond.active_bond_code", "AGM", base_dir)
    raw_name = get_setting("bond.active_bond_name", "AGM Bonded Warehouse", base_dir)
    code_str = str(raw_code or "").strip().upper()
    if not code_str or "<" in code_str or "mock" in code_str.lower():
        code_str = "AGM"
    name_str = str(raw_name or "").strip()
    if not name_str or "<" in name_str or "mock" in name_str.lower():
        name_str = "AGM Bonded Warehouse"
    return {"code": code_str, "name": name_str}


def set_active_bond(code: str, name: Optional[str] = None, base_dir: Optional[Path] = None) -> bool:
    """Updates active operating bond warehouse in config.json."""
    if not code or "<" in str(code) or "mock" in str(code).lower():
        return False
    code_clean = str(code).strip().upper()
    name_clean = str(name or "").strip()
    if not name_clean:
        bonds = get_available_bonds(base_dir)
        for b in bonds:
            if b.get("code") == code_clean:
                name_clean = b.get("name", "")
                break
    if not name_clean:
        name_clean = f"{code_clean} Bonded Warehouse"

    set_setting("bond.active_bond_code", code_clean, base_dir)
    return set_setting("bond.active_bond_name", name_clean, base_dir)


def get_available_bonds(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Returns list of configured and available bonded warehouse facilities."""
    bonds = get_setting("bond.available_bonds", None, base_dir)
    if not bonds:
        bonds = copy.deepcopy(DEFAULT_CONFIG["bond"]["available_bonds"])
    return bonds


def is_developer_mode(base_dir: Optional[Path] = None) -> bool:
    """Returns True if developer mode is enabled."""
    return bool(get_setting("system.developer_mode", False, base_dir))


def get_database_config(base_dir: Path) -> Dict[str, Any]:
    """
    Returns Django DATABASES['default'] dictionary.
    Defaults to SQLite (db.sqlite3) for instant portability on other machines.
    Supports PostgreSQL if explicitly configured in config.json or environment variables.
    """
    config = load_config(base_dir)
    db_cfg = config.get("database", {})

    # Check environment variable overrides
    env_engine = os.getenv("DB_ENGINE")
    use_pg_env = os.getenv("USE_POSTGRES", "").lower() in ("1", "true", "yes")

    engine = "sqlite"
    if env_engine:
        engine = env_engine.lower()
    elif use_pg_env:
        engine = "postgresql"
    else:
        engine = db_cfg.get("engine", "sqlite").lower()

    if engine in ("postgres", "postgresql"):
        pg_db = os.getenv("POSTGRES_DB", db_cfg.get("postgres_db", "itms"))
        pg_user = os.getenv("POSTGRES_USER", db_cfg.get("postgres_user", "postgres"))
        pg_pwd = os.getenv("POSTGRES_PASSWORD", db_cfg.get("postgres_password", ""))
        pg_host = os.getenv("POSTGRES_HOST", db_cfg.get("postgres_host", "localhost"))
        pg_port = os.getenv("POSTGRES_PORT", str(db_cfg.get("postgres_port", "5432")))

        # If not running in a test suite, verify reachability so offline boots gracefully fallback to SQLite
        import sys
        is_test_run = any("test" in arg for arg in sys.argv)
        if not is_test_run:
            reach = test_postgres_connection(
                host=pg_host,
                port=pg_port,
                dbname=pg_db,
                user=pg_user,
                password=pg_pwd,
                timeout=2,
            )
            if not reach.get("success"):
                logger.warning(
                    "PostgreSQL configured in last session (%s@%s:%s) is unreachable: %s. Falling back to local SQLite.",
                    pg_db, pg_host, pg_port, reach.get("error")
                )
                os.environ["ITMS_DB_FALLBACK_WARNING"] = (
                    f"PostgreSQL ({pg_db}@{pg_host}:{pg_port}) was unreachable on startup. "
                    f"Falling back to local SQLite offline vault ({reach.get('error')})."
                )
                sqlite_file = db_cfg.get("sqlite_file", "db.sqlite3")
                sqlite_path = Path(sqlite_file) if Path(sqlite_file).is_absolute() else base_dir / sqlite_file
                return {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": sqlite_path,
                    "OPTIONS": {
                        "timeout": 60,
                    },
                }

        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": pg_db,
            "USER": pg_user,
            "PASSWORD": pg_pwd,
            "HOST": pg_host,
            "PORT": str(pg_port),
            "CONN_MAX_AGE": 600,
        }
    else:
        sqlite_file = db_cfg.get("sqlite_file", "db.sqlite3")
        sqlite_path = Path(sqlite_file) if Path(sqlite_file).is_absolute() else base_dir / sqlite_file
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            "OPTIONS": {
                "timeout": 60,
            },
        }


def test_postgres_connection(
    host: str = "localhost",
    port: Any = 5432,
    dbname: str = "itms",
    user: str = "postgres",
    password: str = "",
    timeout: int = 5,
) -> Dict[str, Any]:
    """Tests connection to PostgreSQL without modifying Django runtime state."""
    import time
    try:
        import psycopg2
    except ImportError:
        return {
            "success": False,
            "error": "psycopg2 driver not installed",
            "message": "Missing psycopg2 driver in Python environment.",
        }

    start = time.time()
    try:
        conn = psycopg2.connect(
            host=host,
            port=int(port),
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=timeout,
        )
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version_str = cur.fetchone()[0]
        cur.close()
        conn.close()
        latency_ms = int((time.time() - start) * 1000)
        ver_short = version_str.split(",")[0] if version_str else "PostgreSQL"
        return {
            "success": True,
            "latency_ms": latency_ms,
            "version": ver_short,
            "message": f"Connected ({latency_ms}ms) to {dbname}@{host}:{port} ({ver_short})",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "message": f"PostgreSQL connection failed: {exc}",
        }


def ensure_migrations_applied(database: str = "default") -> bool:
    """
    Safely applies any pending migrations to the active database on startup.
    Ensures newly added columns (like account_email) exist in tables before queries run.
    """
    import io
    from django.core.management import call_command
    try:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        call_command(
            "migrate",
            verbosity=0,
            interactive=False,
            database=database,
            stdout=out_buf,
            stderr=err_buf,
        )
        return True
    except Exception as exc:
        logger.warning("Auto-migration check on database '%s': %s", database, exc)
        return False


def ensure_operator_accounts_synced(base_dir: Optional[Path] = None) -> int:
    """
    Ensures operator user accounts exist in the currently active database.
    If the active database is empty of users (e.g. freshly switched or connected to PostgreSQL),
    this replicates existing operator accounts from the local SQLite database file (db.sqlite3).
    Returns the number of synced accounts.
    """
    from django.contrib.auth.models import User
    try:
        if User.objects.exists():
            return 0
    except Exception:
        return 0

    root_dir = base_dir or _get_project_root()
    sqlite_path = root_dir / "db.sqlite3"
    if not sqlite_path.is_file():
        return 0

    import sqlite3
    synced = 0
    try:
        conn = sqlite3.connect(str(sqlite_path))
        cur = conn.cursor()
        # Verify auth_user table exists
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='auth_user';")
        if cur.fetchone()[0] == 0:
            conn.close()
            return 0

        cur.execute(
            "SELECT username, password, email, first_name, last_name, is_staff, is_superuser, is_active FROM auth_user"
        )
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            uname, pwd, email, fname, lname, is_staff, is_super, is_active = row
            if not User.objects.filter(username=uname).exists():
                User.objects.create(
                    username=uname,
                    password=pwd,  # Preserves identical PBKDF2 hash!
                    email=email or "",
                    first_name=fname or "",
                    last_name=lname or "",
                    is_staff=bool(is_staff),
                    is_superuser=bool(is_super),
                    is_active=bool(is_active),
                )
                synced += 1
        if synced > 0:
            logger.info("Automatically synced %d operator account(s) from SQLite to active database.", synced)
    except Exception as exc:
        logger.warning("Could not auto-sync operator accounts from SQLite: %s", exc)

    return synced


def switch_database(
    engine: str,
    host: str = "localhost",
    port: Any = 5432,
    dbname: str = "itms",
    user: str = "postgres",
    password: str = "",
    sqlite_file: str = "db.sqlite3",
    base_dir: Optional[Path] = None,
    run_migrations: bool = True,
    persist_config: bool = True,
) -> Dict[str, Any]:
    """
    Dynamically switches Django's active default database connection at runtime and persists to config.json.
    Supports seamless switching between SQLite 3 and PostgreSQL while safely migrating operator accounts.
    """
    import io
    from datetime import datetime
    from asgiref.local import Local
    from django.conf import settings
    from django.db import connections
    from django.core.management import call_command

    root_dir = base_dir or _get_project_root()
    clean_engine = engine.lower().strip()

    if clean_engine in ("postgres", "postgresql"):
        # Pre-flight reachability test
        test_res = test_postgres_connection(
            host=host, port=port, dbname=dbname, user=user, password=password, timeout=5
        )
        if not test_res.get("success"):
            return {
                "success": False,
                "error": test_res.get("error", "Database connection test failed"),
                "message": test_res.get("message", "Unable to connect to PostgreSQL"),
            }

        new_dict = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": dbname,
            "USER": user,
            "PASSWORD": password,
            "HOST": host,
            "PORT": str(port),
            "CONN_MAX_AGE": 0,
        }
    else:
        clean_engine = "sqlite"
        sqlite_path = Path(sqlite_file) if Path(sqlite_file).is_absolute() else root_dir / sqlite_file
        new_dict = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            "OPTIONS": {"timeout": 60},
            "CONN_MAX_AGE": 0,
        }

    # Snapshot existing operator accounts from current database before disconnecting
    source_users = []
    try:
        from django.contrib.auth.models import User
        source_users = list(User.objects.all().values(
            "username", "password", "email", "first_name", "last_name",
            "is_staff", "is_superuser", "is_active"
        ))
    except Exception as u_exc:
        logger.debug("Could not snapshot users prior to switch: %s", u_exc)

    try:
        # 1. Close current connection pool
        try:
            connections.close_all()
        except Exception:
            pass

        # 2. Update global settings.DATABASES
        settings.DATABASES["default"] = new_dict

        # 3. Reset ConnectionHandler cached settings and connections safely across threads
        try:
            del connections["default"]
        except (KeyError, AttributeError):
            pass

        connections._settings = None
        if "settings" in connections.__dict__:
            del connections.__dict__["settings"]

        # Reset thread-local container so all worker and UI threads obtain new connection wrappers
        connections._connections = Local(connections.thread_critical)

        # 4. Ensure connection is active
        connections["default"].ensure_connection()

        # 5. Apply migrations if requested
        if run_migrations:
            try:
                out_buf = io.StringIO()
                err_buf = io.StringIO()
                call_command(
                    "migrate",
                    verbosity=0,
                    interactive=False,
                    database="default",
                    stdout=out_buf,
                    stderr=err_buf,
                )
            except Exception as mig_exc:
                logger.warning("Auto-migration note on switch: %s", mig_exc)

        # Replicate operator accounts to the new database to prevent needing new user creation
        synced_accounts = 0
        try:
            from django.contrib.auth.models import User
            for u in source_users:
                if not User.objects.filter(username=u["username"]).exists():
                    User.objects.create(
                        username=u["username"],
                        password=u["password"],  # Preserves identical PBKDF2 hash!
                        email=u.get("email", ""),
                        first_name=u.get("first_name", ""),
                        last_name=u.get("last_name", ""),
                        is_staff=u.get("is_staff", False),
                        is_superuser=u.get("is_superuser", False),
                        is_active=u.get("is_active", True),
                    )
                    synced_accounts += 1

            # In case source was empty but SQLite db.sqlite3 has users, sync them
            if not User.objects.exists():
                synced_accounts += ensure_operator_accounts_synced(root_dir)
        except Exception as sync_exc:
            logger.warning("Operator account replication note on switch: %s", sync_exc)

        # 6. Persist to config.json (unless persist_config=False in testing)
        active_name = dbname if clean_engine in ("postgres", "postgresql") else Path(sqlite_file).name
        if persist_config:
            cfg = load_config(root_dir)
            cfg.setdefault("database", {})
            cfg["database"]["engine"] = "postgresql" if clean_engine in ("postgres", "postgresql") else "sqlite"
            cfg["database"]["last_session_engine"] = clean_engine
            cfg["database"]["last_session_db"] = str(active_name)
            cfg["database"]["last_session_at"] = datetime.now().isoformat()
            if clean_engine in ("postgres", "postgresql"):
                cfg["database"]["postgres_host"] = host
                cfg["database"]["postgres_port"] = str(port)
                cfg["database"]["postgres_db"] = dbname
                cfg["database"]["postgres_user"] = user
                cfg["database"]["postgres_password"] = password
            else:
                cfg["database"]["sqlite_file"] = sqlite_file
            save_config(cfg, root_dir)

            # Persist to operator UI preferences
            try:
                from core.services import auth_service
                auth_service.save_operator_preferences({
                    "last_database_engine": clean_engine,
                    "last_database_name": str(active_name),
                    "last_database_host": host if clean_engine in ("postgres", "postgresql") else "localhost",
                })
            except Exception:
                pass

        # Clear fallback warning if active
        if "ITMS_DB_FALLBACK_WARNING" in os.environ:
            del os.environ["ITMS_DB_FALLBACK_WARNING"]

        return {
            "success": True,
            "engine": clean_engine,
            "database_name": str(active_name),
            "synced_users": synced_accounts,
            "message": f"Successfully switched active database to {clean_engine.upper()} ({active_name}).",
        }
    except Exception as exc:
        logger.error("Failed to switch database: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "message": f"Failed to switch database: {exc}",
        }


def get_active_database_info() -> Dict[str, Any]:
    """Returns vendor and connection metadata for the currently active default database."""
    from django.db import connection
    vendor = connection.vendor
    if vendor == "sqlite":
        name = Path(connection.settings_dict.get("NAME", "db.sqlite3")).name
        display = f"[bold green]SQLite 3[/bold green] (File: [cyan]{name}[/cyan])"
        badge = "[bold green]● SQLite[/bold green]"
        host = "localhost"
        port = ""
    else:
        name = connection.settings_dict.get("NAME", "itms")
        host = connection.settings_dict.get("HOST", "localhost")
        port = connection.settings_dict.get("PORT", "5432")
        display = f"[bold cyan]PostgreSQL[/bold cyan] ([cyan]{name}[/cyan] @ {host}:{port})"
        badge = f"[bold cyan]● PG:{name}[/bold cyan]"

    return {
        "vendor": vendor,
        "name": name,
        "host": host,
        "port": port,
        "display": display,
        "badge": badge,
    }


class ConfigService:
    """Convenience wrapper for OOP access to central configuration."""

    @staticmethod
    def get_setting(key_path: str, default: Any = None, base_dir: Optional[Path] = None) -> Any:
        return get_setting(key_path, default, base_dir)

    @staticmethod
    def set_setting(key_path: str, value: Any, base_dir: Optional[Path] = None) -> bool:
        return set_setting(key_path, value, base_dir)

    @staticmethod
    def is_developer_mode(base_dir: Optional[Path] = None) -> bool:
        return is_developer_mode(base_dir)

    @staticmethod
    def get_database_config(base_dir: Optional[Path] = None) -> Dict[str, Any]:
        return get_database_config(base_dir or Path("."))

    @staticmethod
    def load_config(base_dir: Optional[Path] = None) -> Dict[str, Any]:
        return load_config(base_dir)

    @staticmethod
    def save_config(config: Dict[str, Any], base_dir: Optional[Path] = None) -> bool:
        return save_config(config, base_dir)

    @staticmethod
    def test_postgres_connection(
        host: str = "localhost",
        port: Any = 5432,
        dbname: str = "itms",
        user: str = "postgres",
        password: str = "",
        timeout: int = 5,
    ) -> Dict[str, Any]:
        return test_postgres_connection(host, port, dbname, user, password, timeout)

    @staticmethod
    def switch_database(
        engine: str,
        host: str = "localhost",
        port: Any = 5432,
        dbname: str = "itms",
        user: str = "postgres",
        password: str = "",
        sqlite_file: str = "db.sqlite3",
        base_dir: Optional[Path] = None,
        run_migrations: bool = True,
        persist_config: bool = True,
    ) -> Dict[str, Any]:
        return switch_database(
            engine, host, port, dbname, user, password, sqlite_file, base_dir, run_migrations, persist_config
        )

    @staticmethod
    def ensure_migrations_applied(database: str = "default") -> bool:
        return ensure_migrations_applied(database)

    @staticmethod
    def ensure_operator_accounts_synced(base_dir: Optional[Path] = None) -> int:
        return ensure_operator_accounts_synced(base_dir)

    @staticmethod
    def get_active_database_info() -> Dict[str, Any]:
        return get_active_database_info()


_service_instance = ConfigService()


def get_config_service() -> ConfigService:
    """Returns singleton ConfigService instance."""
    return _service_instance

