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
    """Returns the resolved project root directory."""
    try:
        if settings.configured and getattr(settings, 'BASE_DIR', None):
            return Path(settings.BASE_DIR).resolve()
    except Exception as exc:
        logger.debug('Could not determine BASE_DIR from settings: %s', exc)
    return Path(__file__).resolve().parent.parent.parent


def get_secure_dir(base_dir: Optional[Path] = None) -> Path:
    """Returns and ensures the secure root directory exists with restricted permissions."""
    root = Path(base_dir) if base_dir else get_project_root()
    secure_dir = root / SECURE_DIR_NAME
    secure_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(secure_dir, 0o700)
    except OSError as exc:
        logger.debug('Could not set permissions on %s: %s', secure_dir, exc)
    return secure_dir


def get_secure_auth_dir(base_dir: Optional[Path] = None) -> Path:
    """Returns and ensures the secure/auth directory exists with restricted permissions."""
    auth_dir = get_secure_dir(base_dir) / AUTH_DIR_NAME
    auth_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(auth_dir, 0o700)
    except OSError as exc:
        logger.debug('Could not set permissions on %s: %s', auth_dir, exc)
    return auth_dir


def get_secure_config_path(base_dir: Optional[Path] = None) -> Path:
    """Returns the single source-of-truth configuration path in secure/config.json."""
    root = Path(base_dir) if base_dir else get_project_root()
    secure_path = get_secure_dir(root) / CONFIG_FILE_NAME
    legacy_path = root / CONFIG_FILE_NAME

    if not secure_path.is_file() and legacy_path.is_file():
        try:
            shutil.copy2(legacy_path, secure_path)
            try:
                os.chmod(secure_path, 0o600)
            except OSError:
                pass
            logger.info('Migrated legacy config.json to secure storage: %s', secure_path)
            try:
                legacy_path.unlink()
            except OSError as unl_exc:
                logger.debug('Could not remove legacy config.json: %s', unl_exc)
        except Exception as exc:
            logger.warning('Failed to auto-migrate legacy config.json: %s', exc)
    elif secure_path.is_file() and legacy_path.is_file():
        # Remove legacy duplicate in root to maintain single source of truth in secure folder
        try:
            legacy_path.unlink()
        except OSError as unl_exc:
            logger.debug('Could not remove duplicate legacy config.json: %s', unl_exc)

    return secure_path


def get_secure_auth_path(
    filename: str,
    legacy_vault_file: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Path:
    """
    Safely resolves a file path within secure/auth/ with path traversal validation (CWE-22).
    Migrates legacy vault files if necessary.
    """
    if not filename or "\x00" in filename:
        raise ValueError(f"Invalid secure auth filename: {filename!r}")

    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"Path traversal detected in secure auth filename: {filename!r}")

    clean_filename = Path(filename).name
    if not clean_filename or clean_filename in (".", ".."):
        raise ValueError(f"Path traversal attempt in filename: {filename!r}")

    root = Path(base_dir) if base_dir else get_project_root()
    auth_dir = get_secure_auth_dir(root).resolve()
    target_path = (auth_dir / clean_filename).resolve()

    if not target_path.is_relative_to(auth_dir):
        raise ValueError(f"Path traversal detected in secure auth filename: {filename!r}")

    if not target_path.is_file() and legacy_vault_file:
        if "\x00" not in legacy_vault_file:
            try:
                vault_root = Path(getattr(settings, 'VAULT_ROOT', root / 'media' / 'vault')).resolve()
            except Exception:
                vault_root = (root / 'media' / 'vault').resolve()

            clean_legacy = Path(legacy_vault_file).name
            if clean_legacy and clean_legacy not in (".", ".."):
                legacy_path = (vault_root / clean_legacy).resolve()
                if legacy_path.is_relative_to(vault_root) and legacy_path.is_file():
                    try:
                        shutil.copy2(legacy_path, target_path)
                        try:
                            os.chmod(target_path, 0o600)
                        except OSError:
                            pass
                        legacy_path.unlink()
                        logger.info('Migrated legacy auth file %s -> %s', legacy_path, target_path)
                    except Exception as exc:
                        logger.warning('Failed to migrate legacy auth file %s: %s', legacy_path, exc)

    return target_path


def set_secure_file_permissions(file_path: Path) -> None:
    """Restricts file permissions to owner read/write (0600) when supported by the OS."""
    try:
        p = Path(file_path)
        if p.is_file():
            os.chmod(p, 0o600)
    except OSError as exc:
        logger.debug('Could not set permissions on %s: %s', file_path, exc)

