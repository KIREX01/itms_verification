"""
Authentication & Operator Session Service.

Handles:
- Django User authentication (PBKDF2 SHA-256)
- Operator account creation / first-time onboarding
- Secure "Remember Me" session persistence via HMAC tokens
- ITMS WebApp credentials, connection testing, and live token verification
- Dynamic generation and loading of the ITMS ANSI Landing Banner
"""
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rich.text import Text

logger = logging.getLogger(__name__)

from core.services.secure_storage import get_secure_auth_path

def get_session_file_path() -> Path:
    return get_secure_auth_path("operator_session.json", legacy_vault_file=".operator_session.json")

def get_prefs_file_path() -> Path:
    return get_secure_auth_path("operator_prefs.json", legacy_vault_file=".operator_prefs.json")

SESSION_FILE = get_session_file_path()
PREFS_FILE = get_prefs_file_path()
DEFAULT_BANNER_ANS = Path("assets/landing_banner.ans")
SOURCE_BANNER_PNG = Path("assets/ascii-magic-1.png")
SHIELD_BANNER_ANS = Path("assets/shield_banner.ans")
SHIELD_EMBLEM_PNG = Path("assets/shield_emblem.png")


def authenticate_operator(username: str, password: str) -> Tuple[Optional[User], str]:
    """Authenticates an operator using Django's standard authentication backend."""
    if not username or not password:
        return None, "Username and password are required."

    user = authenticate(username=username, password=password)
    if user is None:
        # Before failing, if the user is missing in this database, attempt auto-sync from SQLite
        if not User.objects.filter(username=username).exists():
            try:
                from core.services.config_service import ensure_operator_accounts_synced
                if ensure_operator_accounts_synced() > 0:
                    user = authenticate(username=username, password=password)
                    if user is not None and user.is_active:
                        return user, ""
            except Exception:
                pass

        # Check if username even exists to give a helpful message
        if not User.objects.filter(username=username).exists():
            return None, f"User '{username}' does not exist in active database. Did you mean to create an account or switch database?"
        return None, "Invalid password. Please check your credentials."

    if not user.is_active:
        return None, "This operator account is deactivated. Contact an administrator."

    return user, ""


def create_operator_account(
    username: str,
    password: str,
    full_name: str = "",
    email: str = "",
) -> Tuple[Optional[User], str]:
    """Creates a new operator account in the local Django database."""
    username = (username or "").strip()
    password = (password or "").strip()
    full_name = (full_name or "").strip()
    email = (email or "").strip()

    if not username:
        return None, "Username cannot be empty."
    if len(username) < 3:
        return None, "Username must be at least 3 characters long."
    if not password:
        return None, "Password cannot be empty."
    if len(password) < 6:
        return None, "Password must be at least 6 characters long."

    if User.objects.filter(username=username).exists():
        return None, f"Username '{username}' is already taken. Please choose another."

    first_name = full_name
    last_name = ""
    if " " in full_name:
        parts = full_name.split(" ", 1)
        first_name, last_name = parts[0], parts[1]

    # If this is the very first user in the system, grant staff privileges
    is_first_user = User.objects.count() == 0

    try:
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name,
            is_staff=is_first_user,
            is_superuser=is_first_user,
        )
        return user, ""
    except Exception as exc:
        return None, f"Failed to create account: {exc}"


def _compute_session_token(user: User) -> str:
    """Computes a cryptographically-bound HMAC token for the user's active password hash."""
    secret = getattr(settings, "SECRET_KEY", "itms-local-fallback-secret-key").encode()
    return hmac.new(secret, user.password.encode(), hashlib.sha256).hexdigest()


def save_remembered_session(user: User, remember: bool = True) -> None:
    """Saves or clears the remembered operator session on this terminal machine."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not remember:
        clear_remembered_session()
        # Still remember the username for convenience
        save_operator_preferences({"last_username": user.username})
        return

    token = _compute_session_token(user)
    from core.services.config_service import get_active_database_info
    db_info = get_active_database_info()
    data = {
        "username": user.username,
        "token": token,
        "saved_at": time.time(),
        "database_vendor": db_info.get("vendor", ""),
        "database_name": db_info.get("name", ""),
    }
    session_file = get_session_file_path()
    try:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(session_file, 0o600)
        except Exception:
            pass
        save_operator_preferences({
            "last_username": user.username,
            "last_database_vendor": db_info.get("vendor", ""),
            "last_database_name": db_info.get("name", ""),
        })
    except Exception as exc:
        logger.warning("Could not save operator session: %s", exc)


def get_remembered_session() -> Optional[User]:
    """Validates and retrieves the currently remembered operator user, if valid."""
    session_file = get_session_file_path()
    if not session_file.is_file():
        return None

    try:
        from core.services.config_service import ensure_operator_accounts_synced
        ensure_operator_accounts_synced()
    except Exception:
        pass

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        username = data.get("username", "")
        token = data.get("token", "")
        if not username or not token:
            return None

        user = User.objects.filter(username=username).first()
        if not user or not user.is_active:
            return None

        expected_token = _compute_session_token(user)
        if hmac.compare_digest(token, expected_token):
            return user
    except Exception as exc:
        logger.warning("Failed to validate remembered session: %s", exc)
    return None


def clear_remembered_session() -> None:
    """Deletes the stored session file."""
    session_file = get_session_file_path()
    if session_file.is_file():
        try:
            session_file.unlink()
        except OSError:
            pass


def get_operator_preferences() -> Dict[str, Any]:
    """Retrieves UI/UX preferences (e.g. last used username, theme, ITMS mode)."""
    prefs_file = get_prefs_file_path()
    if not prefs_file.is_file():
        return {}
    try:
        with open(prefs_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_operator_preferences(updates: Dict[str, Any]) -> None:
    """Updates and saves UI/UX preferences to disk."""
    prefs = get_operator_preferences()
    prefs.update(updates)
    prefs_file = get_prefs_file_path()
    prefs_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(prefs_file, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        try:
            os.chmod(prefs_file, 0o600)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Could not save operator preferences: %s", exc)


def get_itms_status() -> Dict[str, Any]:
    """Queries current ITMS WebApp connection status."""
    from core.services.itms_web_client import get_web_client
    client = get_web_client()
    status = client.get_status()
    ping_status = client.test_connection()
    return {
        "url": client.base_url,
        "online": ping_status.get("success", False),
        "online_error": ping_status.get("error", ""),
        "authenticated": status.get("authenticated", False),
        "user_email": status.get("user_email", ""),
        "user_uuid": status.get("user_uuid", ""),
        "expires_in_days": status.get("expires_in_days", 0),
        "last_status_message": status.get("status_message", ""),
    }


def connect_itms_account(
    email: str,
    password: str,
    base_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """Authenticates against the live ITMS WebApp and stores session cookies."""
    from core.services.itms_web_client import ITMSWebClient, get_web_client
    client = ITMSWebClient(base_url=base_url) if base_url else get_web_client()
    try:
        ok, msg, sess = client.login(email=email, password=password)
        if ok:
            save_operator_preferences({
                "itms_email": email,
                "itms_url": client.base_url,
                "itms_mode": "live",
            })
            return True, msg
        return False, msg
    except Exception as exc:
        return False, f"Unexpected error connecting to ITMS: {exc}"


def render_banner_ansi(
    png_path: Path,
    out_ans_path: Path,
    width: int = 150,
    height: Optional[int] = None,
) -> str:
    """
    Converts a PNG image into high-resolution ANSI TrueColor half-blocks (`▀`).
    Each character cell renders 2 vertical pixels (foreground = top, background = bottom),
    providing double vertical resolution in standard terminals.
    """
    from PIL import Image

    if not png_path.is_file():
        return ""

    img = Image.open(png_path).convert("RGB")
    if height is None:
        height = max(1, round(width * img.height / img.width / 2))
    img_resized = img.resize((width, height * 2), Image.Resampling.LANCZOS)
    pixels = img_resized.load()

    lines = []
    for y in range(0, height * 2, 2):
        parts = []
        for x in range(width):
            r1, g1, b1 = pixels[x, y]
            r2, g2, b2 = pixels[x, y + 1]
            parts.append(f"\033[38;2;{r1};{g1};{b1}m\033[48;2;{r2};{g2};{b2}m▀\033[0m")
        lines.append("".join(parts))

    ansi_art = "\n".join(lines)
    out_ans_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_ans_path, "w", encoding="utf-8") as f:
        f.write(ansi_art)
    return ansi_art


def get_shield_rich_text(width: int = 22, height: int = 14) -> Text:
    """Returns a Rich Text renderable of the focused, high-contrast ITMS Shield emblem."""
    needs_render = False
    if not SHIELD_BANNER_ANS.is_file():
        needs_render = True
    elif SHIELD_EMBLEM_PNG.is_file() and SHIELD_EMBLEM_PNG.stat().st_mtime > SHIELD_BANNER_ANS.stat().st_mtime:
        needs_render = True

    if needs_render:
        if not SHIELD_EMBLEM_PNG.is_file() and SOURCE_BANNER_PNG.is_file():
            try:
                from PIL import Image, ImageEnhance
                src_img = Image.open(SOURCE_BANNER_PNG)
                shield = src_img.crop((115, 45, 785, 1055))
                enhancer = ImageEnhance.Sharpness(shield.convert("RGB"))
                shield_sharp = enhancer.enhance(1.4)
                contrast = ImageEnhance.Contrast(shield_sharp)
                shield_crisp = contrast.enhance(1.15)
                shield_crisp.save(SHIELD_EMBLEM_PNG)
            except Exception as exc:
                logger.warning("Could not crop shield emblem from source: %s", exc)

        if SHIELD_EMBLEM_PNG.is_file():
            try:
                render_banner_ansi(SHIELD_EMBLEM_PNG, SHIELD_BANNER_ANS, width=width, height=height)
            except Exception as exc:
                logger.warning("Could not render shield banner ANSI: %s", exc)

    if SHIELD_BANNER_ANS.is_file():
        try:
            with open(SHIELD_BANNER_ANS, "r", encoding="utf-8") as f:
                return Text.from_ansi(f.read())
        except Exception as exc:
            logger.warning("Could not load shield banner ANSI: %s", exc)

    return get_banner_rich_text(width=width, height=height)


def get_banner_rich_text(width: int = 70, height: Optional[int] = None) -> Text:
    """Returns a Rich Text renderable of the ITMS Landing Banner."""
    # If .ans file doesn't exist or PNG is newer, re-render
    needs_render = False
    if not DEFAULT_BANNER_ANS.is_file():
        needs_render = True
    elif SOURCE_BANNER_PNG.is_file() and SOURCE_BANNER_PNG.stat().st_mtime > DEFAULT_BANNER_ANS.stat().st_mtime:
        needs_render = True

    if needs_render and SOURCE_BANNER_PNG.is_file():
        try:
            render_banner_ansi(SOURCE_BANNER_PNG, DEFAULT_BANNER_ANS, width, height)
        except Exception as exc:
            logger.warning("Could not auto-generate landing banner: %s", exc)

    if DEFAULT_BANNER_ANS.is_file():
        try:
            with open(DEFAULT_BANNER_ANS, "r", encoding="utf-8") as f:
                return Text.from_ansi(f.read())
        except Exception as exc:
            logger.warning("Could not load banner .ans: %s", exc)

    # Fallback stylized ASCII text banner if image/ANSI is not available
    fallback = Text()
    fallback.append("╔══════════════════════════════════════════════════════════════════════════╗\n", style="bold cyan")
    fallback.append("║                  INTELLIGENT TRANSPORT MONITORING SYSTEM                 ║\n", style="bold yellow")
    fallback.append("║                Uganda Vehicle Installation Verification Copilot          ║\n", style="bold white")
    fallback.append("╚══════════════════════════════════════════════════════════════════════════╝", style="bold cyan")
    return fallback
