"""
ITMS Verification Copilot - GitHub Releases Update Engine.
Provides non-destructive update detection and safe distribution management.

Key Guarantees:
1. Compares semantic versions against GitHub Releases API (https://api.github.com/repos/KIREX01/itms_verification/releases/latest).
2. Uses local caching (12 hours) to avoid hitting GitHub rate limits or stalling offline machines.
3. Supports both Git repositories and standalone ZIP release installations.
4. Strictly protects and preserves operator data during updates:
   - SQLite database (`db.sqlite3`)
   - Evidence vault & crops (`media/vault/`, `media/crops/`)
   - Secure credentials and sessions (`secure/`, `.env`, `config.json`)
   - Virtual environments (`.venv/`)
"""
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.version import GITHUB_RELEASES_URL, GITHUB_REPO, __version__, is_newer_version, parse_version

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_FILE = PROJECT_ROOT / "secure" / ".update_cache.json"
CACHE_TTL_HOURS = 12

# Files and directories that MUST NEVER be overwritten during an update
PROTECTED_PATHS = {
    "db.sqlite3",
    ".env",
    "config.json",
    ".venv",
    "venv",
    "media",
    "secure",
    "exports",
    "tools",
}


def _get_cache() -> Optional[Dict[str, Any]]:
    """Loads cached update check if still within TTL."""
    if not CACHE_FILE.is_file():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01T00:00:00"))
        if datetime.now() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return data.get("result")
    except Exception as exc:
        logger.debug("Could not read update cache: %s", exc)
    return None


def _save_cache(result: Dict[str, Any]) -> None:
    """Saves update check result to cache file."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cached_at": datetime.now().isoformat(),
            "result": result,
        }
        CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write update cache: %s", exc)


class UpdateService:
    """Manages update checks and safe system upgrading via GitHub Releases."""

    @staticmethod
    def check_for_updates(force: bool = False) -> Dict[str, Any]:
        """
        Checks GitHub Releases API for newer version tags.
        Returns detailed update metadata and changelog.
        """
        if not force:
            cached = _get_cache()
            if cached:
                cached["from_cache"] = True
                return cached

        result: Dict[str, Any] = {
            "success": False,
            "update_available": False,
            "current_version": __version__,
            "latest_version": __version__,
            "release_name": f"v{__version__}",
            "release_notes": "",
            "html_url": f"https://github.com/{GITHUB_REPO}/releases",
            "published_at": "",
            "download_url": "",
            "is_git": (PROJECT_ROOT / ".git").is_dir(),
            "error": None,
        }

        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_URL,
                headers={
                    "User-Agent": f"ITMS-Verification-Copilot/{__version__}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    tag_name = payload.get("tag_name", "").strip().lstrip("vV")
                    remote_name = payload.get("name") or f"Release v{tag_name}"
                    body = payload.get("body", "").strip()
                    html_url = payload.get("html_url", "")
                    published = payload.get("published_at", "")
                    zip_url = payload.get("zipball_url", "")

                    # Check for attached release zip asset
                    assets = payload.get("assets", [])
                    for asset in assets:
                        if asset.get("name", "").endswith(".zip"):
                            zip_url = asset.get("browser_download_url", zip_url)
                            break

                    newer = is_newer_version(tag_name, __version__)

                    result.update({
                        "success": True,
                        "update_available": newer,
                        "current_version": __version__,
                        "latest_version": tag_name,
                        "release_name": remote_name,
                        "release_notes": body,
                        "html_url": html_url,
                        "published_at": published,
                        "download_url": zip_url,
                    })
                    _save_cache(result)
                    return result
        except urllib.error.HTTPError as err:
            # 404 means no releases published yet
            if err.code == 404:
                result.update({
                    "success": True,
                    "update_available": False,
                    "release_notes": "No remote releases found on GitHub. System is on latest development branch.",
                })
                _save_cache(result)
                return result
            result["error"] = f"GitHub API error: {err.code} {err.reason}"
        except urllib.error.URLError as err:
            result["error"] = f"Network offline or connection timed out ({err.reason})"
        except Exception as exc:
            result["error"] = f"Unexpected error checking updates: {exc}"

        return result

    @staticmethod
    def validate_download_url(url: Optional[str]) -> bool:
        """
        Validates that a download URL points to an authentic GitHub release or repository archive (CWE-918).
        Only https:// URLs from github.com and official GitHub CDN hosts are permitted.
        """
        if not url or not isinstance(url, str):
            return False
        import urllib.parse
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower()
        trusted_hosts = {
            "github.com",
            "api.github.com",
            "codeload.github.com",
            "objects.githubusercontent.com",
            "raw.githubusercontent.com",
        }
        if host in trusted_hosts or host.endswith(".github.com") or host.endswith(".githubusercontent.com"):
            return True
        return False

    @staticmethod
    def apply_update(download_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes a safe in-place upgrade.
        1. If explicit download_url is provided, downloads and applies release archive.
        2. If .git is present, pulls changes via git.
        3. If standalone/zip, downloads release asset or main branch archive.
        4. Runs pip install and Django migrations.
        5. Verifies system integrity.
        """
        if download_url:
            if not UpdateService.validate_download_url(download_url):
                return {"success": False, "error": f"Untrusted download URL: {download_url}"}
            return UpdateService._apply_zip_update(download_url)

        is_git = (PROJECT_ROOT / ".git").is_dir()

        if is_git:
            return UpdateService._apply_git_update()
        else:
            return UpdateService._apply_zip_update(download_url)

    @staticmethod
    def _apply_git_update() -> Dict[str, Any]:
        """Performs non-destructive git pull rebase."""
        if not shutil.which("git"):
            return {
                "success": False,
                "error": "Git executable is not installed or not found in system PATH.",
            }
        try:
            # Check for uncommitted changes in tracked files
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            dirty_files = [line.strip() for line in status_proc.stdout.splitlines() if line.startswith((" M", "M "))]
            if dirty_files:
                logger.warning("Local code modifications detected before update: %s", dirty_files)

            # If untracked media/itms_web.log or other transient log files exist, temporarily move them
            # to avoid merge conflicts with older tracked versions
            log_candidates = [
                PROJECT_ROOT / "media" / "itms_web.log",
            ]
            for log_f in log_candidates:
                if log_f.is_file():
                    try:
                        bak = log_f.with_suffix(".log.bak")
                        if bak.is_file():
                            bak.unlink()
                        shutil.move(str(log_f), str(bak))
                    except Exception as log_exc:
                        logger.debug("Could not move %s before git update: %s", log_f, log_exc)

            # Fetch and rebase
            fetch_proc = subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if fetch_proc.returncode != 0:
                # Fallback to current branch
                subprocess.run(["git", "fetch"], cwd=PROJECT_ROOT, timeout=30)

            pull_proc = subprocess.run(
                ["git", "pull", "--rebase"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if pull_proc.returncode != 0:
                return {
                    "success": False,
                    "error": f"Git update conflict: {pull_proc.stderr.strip() or pull_proc.stdout.strip()}",
                }

            # Locate active python executable
            local_venv = PROJECT_ROOT / ".venv"
            if sys.platform == "win32":
                venv_py = local_venv / "Scripts" / "python.exe"
            else:
                venv_py = local_venv / "bin" / "python"
            py_bin = str(venv_py) if venv_py.is_file() else sys.executable

            # 1. Run pip install directly in fresh subprocess from newly pulled requirements.txt
            req_file = PROJECT_ROOT / "requirements.txt"
            if req_file.is_file():
                print("  [>] Installing / updating Python dependencies (requirements.txt)...")
                creationflags = 0x08000000 if platform.system().lower() == "windows" else 0
                try:
                    subprocess.run(
                        [py_bin, "-m", "pip", "install", "-r", str(req_file)],
                        cwd=str(PROJECT_ROOT),
                        timeout=180,
                        creationflags=creationflags,
                    )
                except Exception as pip_exc:
                    logger.warning("pip invocation notice: %s", pip_exc)

            # 2. Run post-update migrations & dependency checks
            post_ok, post_msg = UpdateService._run_post_update_tasks()
            if not post_ok:
                return {"success": False, "error": post_msg}

            # Clear update cache
            if CACHE_FILE.is_file():
                CACHE_FILE.unlink()

            return {
                "success": True,
                "message": "System successfully updated from GitHub. Please restart the application.",
                "restart_required": True,
            }
        except Exception as exc:
            return {"success": False, "error": f"Git update error: {exc}"}

    @staticmethod
    def _apply_zip_update(download_url: Optional[str] = None) -> Dict[str, Any]:
        """Downloads release zip from GitHub and unpacks preserving operator data."""
        if not download_url:
            check = UpdateService.check_for_updates(force=True)
            download_url = check.get("download_url")

        if not download_url:
            download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"

        if not UpdateService.validate_download_url(download_url):
            return {"success": False, "error": f"Untrusted or insecure download URL: {download_url}"}

        temp_dir = Path(tempfile.mkdtemp(prefix="itms_update_"))
        zip_path = temp_dir / "release.zip"

        try:
            # 1. Download zip archive
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": f"ITMS-Verification-Copilot/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=60) as response, open(zip_path, "wb") as f:
                shutil.copyfileobj(response, f)

            # 2. Extract to temp directory with strict ZipSlip validation
            extract_dir = (temp_dir / "extracted").resolve()
            extract_dir.mkdir()
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.infolist():
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"Malicious zip entry detected: {member.filename}")
                    target_dest = (extract_dir / member_path).resolve()
                    if not target_dest.is_relative_to(extract_dir):
                        raise ValueError(f"ZipSlip path traversal detected: {member.filename}")
                    # Skip symlinks (external_attr check)
                    if (member.external_attr >> 16) & 0o120000 == 0o120000:
                        logger.warning("Skipping symlink in zip: %s", member.filename)
                        continue
                    if member.is_dir():
                        target_dest.mkdir(parents=True, exist_ok=True)
                    else:
                        target_dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src_file, open(target_dest, "wb") as dst_file:
                            shutil.copyfileobj(src_file, dst_file)

            # Locate root folder inside extracted zip (GitHub zips usually have a single root folder)
            extracted_items = list(extract_dir.iterdir())
            source_root = extracted_items[0] if len(extracted_items) == 1 and extracted_items[0].is_dir() else extract_dir

            # 3. Copy files into PROJECT_ROOT, strictly skipping protected paths
            project_root_resolved = PROJECT_ROOT.resolve()
            for item in source_root.rglob("*"):
                if item.is_dir() or item.is_symlink():
                    continue
                rel_path = item.relative_to(source_root)
                first_segment = rel_path.parts[0] if rel_path.parts else ""

                # Never overwrite protected directories or files
                if first_segment in PROTECTED_PATHS or rel_path.name in PROTECTED_PATHS:
                    continue

                dest_file = (PROJECT_ROOT / rel_path).resolve()
                if not dest_file.is_relative_to(project_root_resolved):
                    logger.warning("Skipping file attempting to escape project root: %s", rel_path)
                    continue

                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest_file)

            # Locate active python executable
            local_venv = PROJECT_ROOT / ".venv"
            if sys.platform == "win32":
                venv_py = local_venv / "Scripts" / "python.exe"
            else:
                venv_py = local_venv / "bin" / "python"
            py_bin = str(venv_py) if venv_py.is_file() else sys.executable

            # 4. Run pip install directly in fresh subprocess from newly extracted requirements.txt
            req_file = PROJECT_ROOT / "requirements.txt"
            if req_file.is_file():
                print("  [>] Installing / updating Python dependencies (requirements.txt)...")
                creationflags = 0x08000000 if platform.system().lower() == "windows" else 0
                try:
                    subprocess.run(
                        [py_bin, "-m", "pip", "install", "-r", str(req_file)],
                        cwd=str(PROJECT_ROOT),
                        timeout=180,
                        creationflags=creationflags,
                    )
                except Exception as pip_exc:
                    logger.warning("pip invocation notice: %s", pip_exc)

            # 5. Run migrations & dependencies
            post_ok, post_msg = UpdateService._run_post_update_tasks()
            if not post_ok:
                return {"success": False, "error": post_msg}

            if CACHE_FILE.is_file():
                CACHE_FILE.unlink()

            return {
                "success": True,
                "message": "Release archive installed successfully. Please restart the application.",
                "restart_required": True,
            }
        except Exception as exc:
            return {"success": False, "error": f"Failed applying release archive: {exc}"}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def ensure_dependencies(install_missing: bool = True, upgrade: bool = False) -> Tuple[bool, str]:
        """
        Verifies and installs required Python packages from requirements.txt.
        Guarantees that new and missing dependencies added in system updates
        are automatically installed into the active Python environment.
        """
        try:
            from scripts import bootstrap
            ok = bootstrap.ensure_dependencies(install_missing=install_missing, upgrade=upgrade)
            if ok:
                return True, "Python dependencies verified and up to date."
            return False, "Some Python dependencies could not be installed."
        except Exception as exc:
            logger.error("Error verifying dependencies: %s", exc)
            return False, f"Dependency verification error: {exc}"

    @staticmethod
    def _run_post_update_tasks() -> Tuple[bool, str]:
        """Runs pip install, database migrations, and resource verification after code update."""
        try:
            # 1. Install or update Python dependencies from requirements.txt
            from scripts import bootstrap
            bootstrap.ensure_directories()
            bootstrap.ensure_env_file()
            bootstrap.ensure_dependencies(install_missing=True)

            # 2. Apply database migrations
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itms_project.settings")
            import django
            django.setup()
            from django.core.management import call_command
            call_command("migrate", interactive=False)

            # 3. Verify AI model weights and OCR engine
            bootstrap.ensure_model_weights(download_missing=True)
            bootstrap.detect_and_configure_ocr(download_missing=True)

            return True, "Dependencies, migrations, and model weights verified."
        except Exception as exc:
            logger.error("Post-update task error: %s", exc)
            return False, f"Post-update configuration error: {exc}"


update_service = UpdateService()
