import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

SECURE_DIR_NAME = 'secure'
AUTH_DIR_NAME = 'auth'
CONFIG_FILE_NAME = 'config.json'


def get_project_root() -> Path:
    try:
        if settings.configured and getattr(settings, 'BASE_DIR', None):
            return Path(settings.BASE_DIR).resolve()
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent.parent


def get_secure_dir(base_dir: Optional[Path] = None) -> Path:
    root = Path(base_dir) if base_dir else get_project_root()
    secure_dir = root / SECURE_DIR_NAME
    secure_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(secure_dir, 0o700)
    except Exception:
        pass
    return secure_dir


def get_secure_auth_dir(base_dir: Optional[Path] = None) -> Path:
    auth_dir = get_secure_dir(base_dir) / AUTH_DIR_NAME
    auth_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(auth_dir, 0o700)
    except Exception:
        pass
    return auth_dir


def get_secure_config_path(base_dir: Optional[Path] = None) -> Path:
    root = Path(base_dir) if base_dir else get_project_root()
    secure_path = get_secure_dir(root) / CONFIG_FILE_NAME
    legacy_path = root / CONFIG_FILE_NAME

    if not secure_path.is_file() and legacy_path.is_file():
        try:
            shutil.copy2(legacy_path, secure_path)
            try:
                os.chmod(secure_path, 0o600)
            except Exception:
                pass
            logger.info('Migrated legacy config.json to secure storage: %s', secure_path)
        except Exception as exc:
            logger.warning('Failed to auto-migrate legacy config.json: %s', exc)
            return legacy_path
    elif secure_path.is_file() and not legacy_path.is_file():
        try:
            shutil.copy2(secure_path, legacy_path)
        except Exception:
            pass
    elif secure_path.is_file() and legacy_path.is_file():
        try:
            # Sync whichever file was modified more recently
            if legacy_path.stat().st_mtime > secure_path.stat().st_mtime:
                shutil.copy2(legacy_path, secure_path)
                try:
                    os.chmod(secure_path, 0o600)
                except Exception:
                    pass
                logger.info('Synchronized user edits from root config.json to secure storage: %s', secure_path)
            elif secure_path.stat().st_mtime > legacy_path.stat().st_mtime:
                shutil.copy2(secure_path, legacy_path)
        except Exception as exc:
            logger.debug('Config file mtime sync check error: %s', exc)

    return secure_path


def get_secure_auth_path(
    filename: str,
    legacy_vault_file: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Path:
    root = Path(base_dir) if base_dir else get_project_root()
    auth_dir = get_secure_auth_dir(root)
    target_path = auth_dir / filename

    if not target_path.is_file() and legacy_vault_file:
        try:
            vault_root = Path(getattr(settings, 'VAULT_ROOT', root / 'media' / 'vault'))
        except Exception:
            vault_root = root / 'media' / 'vault'

        legacy_path = vault_root / legacy_vault_file
        if legacy_path.is_file():
            try:
                shutil.copy2(legacy_path, target_path)
                try:
                    os.chmod(target_path, 0o600)
                except Exception:
                    pass
                legacy_path.unlink()
                logger.info('Migrated legacy auth file %s -> %s', legacy_path, target_path)
            except Exception as exc:
                logger.warning('Failed to migrate legacy auth file %s: %s', legacy_path, exc)

    return target_path


def set_secure_file_permissions(file_path: Path) -> None:
    try:
        if file_path.is_file():
            os.chmod(file_path, 0o600)
    except Exception:
        pass

