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
    "database": {
        "engine": "sqlite",            # "sqlite" (default for all machines) or "postgresql"
        "sqlite_file": "db.sqlite3",
        "postgres_host": "localhost",
        "postgres_port": "5432",
        "postgres_db": "itms",
        "postgres_user": "postgres",
        "postgres_password": "",
    },
    "submission": {
        "dry_run_mode": True,          # Safe simulation mode for ITMS submissions
        "submit_step3": True,          # Auto-finalize Step 3 remote installation
        "request_timeout_seconds": 30,
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
    },
    "compression": {
        "enabled": True,               # Smart on-the-fly multipart photo compression
        "max_dimension": 1920,         # Max dimension in px (LANCZOS downsampling)
        "jpeg_quality": 88,            # JPEG quality for multipart stream
    },
    "outbox": {
        "enabled": True,               # Auto-transition network failures to OFFLINE_OUTBOX
        "auto_sync_interval_seconds": 15, # Background heartbeat ping and drain interval
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


def get_setting(key_path: str, default: Any = None, base_dir: Optional[Path] = None) -> Any:
    """
    Reads a dotted key path from config (e.g. 'system.developer_mode').
    Supports aliases for cross-version compatibility.
    """
    aliases = {
        "submission.smart_compression_enabled": "compression.enabled",
        "compression.smart_compression_enabled": "compression.enabled",
        "network.offline_outbox_enabled": "outbox.enabled",
        "submission.offline_outbox_enabled": "outbox.enabled",
        "network.auto_sync_interval_seconds": "outbox.auto_sync_interval_seconds",
    }
    target_key = aliases.get(key_path, key_path)
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
    """
    config = load_config(base_dir)
    keys = key_path.split(".")
    curr = config
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = value
    return save_config(config, base_dir)


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

