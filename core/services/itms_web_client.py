"""
ITMS (Intelligent Transport Management System) Web Application Client.

Connects to the live ITMS web application (https://stock.itms.ug) using the
reverse-engineered Yii2 session & CSRF cookie authentication flow.

Architecture & Safety Principles:
---------------------------------
1. Yii2 Session Authentication:
   - Initial GET /site/login fetches CSRF cookie & token.
   - Form-encoded POST /site/login transmits credentials & CSRF token.
   - Success (HTTP 302 Found) yields persistent session cookies:
       * `_identity-frontend` (30-day operator identity & UUID)
       * `advanced-frontend` (session state)
       * `_csrf-frontend` (cross-site request forgery token)
   - Cookies are persisted locally in `media/vault/.itms_web_session.json`.

2. Rate-Limiting & API Conservation:
   - Verification requests are cached with an operator cooldown (default 5 minutes).
   - Prohibits automated loops or polling against `stock.itms.ug`.
   - Single, lightweight HEAD/GET reachability checks with strict timeouts.

3. Strict Read-Only Safety:
   - NEVER sends live registration/fitment modification requests until authorized.
   - Read-only inspection methods (dashboard, orders preview) perform pure GET requests.

4. Credential & Privacy Protection:
   - Sensitive credentials are never logged to console or persisted in plaintext.
"""
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from django.conf import settings

logger = logging.getLogger(__name__)

def get_itms_base_url() -> str:
    try:
        if settings.configured:
            return getattr(settings, "ITMS_API_BASE_URL", "https://stock.itms.ug")
    except Exception:
        pass
    return "https://stock.itms.ug"

def get_default_session_file() -> Path:
    from core.services.secure_storage import get_secure_auth_path
    return get_secure_auth_path("itms_web_session.json", legacy_vault_file=".itms_web_session.json")

def resolve_dry_run(explicit_dry_run: Optional[bool] = None) -> bool:
    """
    Returns explicit dry_run if supplied, else dynamically queries config_service
    (or Django settings fallback).
    """
    if explicit_dry_run is not None:
        return bool(explicit_dry_run)
    try:
        from core.services import config_service
        return config_service.get_setting("submission.dry_run_mode", True)
    except Exception:
        try:
            return getattr(settings, "ITMS_WEB_DRY_RUN", True)
        except Exception:
            return True

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def prepare_multipart_image_bytes(
    file_path: Any,
    max_dimension: Optional[int] = None,
    jpeg_quality: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Downsamples photos on-the-fly ONLY when preparing the HTTP multipart stream.
    
    SAFETY GUARANTEE:
      The master photos stored on disk in the vault (media/vault) are
      IMMUTABLE and NEVER modified or overwritten. Compression is performed
      strictly in-memory (io.BytesIO) and streamed directly to requests.
    """
    import io
    from pathlib import Path
    from core.services import config_service

    if enabled is None:
        enabled = bool(config_service.get_setting("compression.enabled", True))
    if max_dimension is None:
        try:
            max_dimension = int(config_service.get_setting("compression.max_dimension", 1920))
        except (ValueError, TypeError):
            max_dimension = 1920
    if jpeg_quality is None:
        try:
            jpeg_quality = int(config_service.get_setting("compression.jpeg_quality", 88))
        except (ValueError, TypeError):
            jpeg_quality = 88

    p = Path(file_path)
    if not p.is_file():
        return b"", {
            "compressed": False,
            "error": f"File not found: {file_path}",
            "original_size": 0,
            "compressed_size": 0,
            "saved_bytes": 0,
            "ratio_pct": 0.0,
            "display_str": "File not found",
        }

    try:
        orig_bytes = p.read_bytes()
    except Exception as exc:
        return b"", {
            "compressed": False,
            "error": str(exc),
            "original_size": 0,
            "compressed_size": 0,
            "saved_bytes": 0,
            "ratio_pct": 0.0,
            "display_str": f"Read error: {exc}",
        }

    orig_size = len(orig_bytes)
    orig_mb = orig_size / (1024 * 1024)

    # If disabled or non-image file, return raw original bytes
    ext = p.suffix.lower()
    if not enabled or ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
        return orig_bytes, {
            "compressed": False,
            "original_size": orig_size,
            "compressed_size": orig_size,
            "saved_bytes": 0,
            "ratio_pct": 0.0,
            "display_str": f"{orig_mb:.2f}MB (Uncompressed)",
        }

    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(orig_bytes)) as img:
            # Respect EXIF orientation tag from camera
            try:
                img = ImageOps.exif_transpose(img) or img
            except Exception:
                pass

            orig_w, orig_h = img.size
            final_w, final_h = orig_w, orig_h
            needs_resize = (orig_w > max_dimension or orig_h > max_dimension)

            if needs_resize:
                scale = min(max_dimension / float(orig_w), max_dimension / float(orig_h))
                final_w = max(1, int(round(orig_w * scale)))
                final_h = max(1, int(round(orig_h * scale)))
                resample_filter = getattr(Image, "Resampling", Image).LANCZOS
                img = img.resize((final_w, final_h), resample=resample_filter)

            # Convert RGBA/P palette modes to RGB for JPEG multipart stream
            if img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                rgb_img.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                img = rgb_img
            elif img.mode != "RGB":
                img = img.convert("RGB")

            out_buf = io.BytesIO()
            img.save(out_buf, format="JPEG", quality=jpeg_quality, optimize=True)
            comp_bytes = out_buf.getvalue()
            comp_size = len(comp_bytes)

            # Use compressed bytes if space saved or dimensions reduced
            if comp_size < orig_size or needs_resize:
                saved = max(0, orig_size - comp_size)
                ratio = round((saved / orig_size) * 100.0, 1) if orig_size > 0 else 0.0
                comp_kb = comp_size / 1024
                comp_mb = comp_size / (1024 * 1024)
                comp_size_str = f"{comp_kb:.0f}KB" if comp_mb < 1.0 else f"{comp_mb:.2f}MB"
                disp = f"{orig_mb:.1f}MB -> {comp_size_str} (-{ratio}%) [{final_w}x{final_h} Q{jpeg_quality}]"
                return comp_bytes, {
                    "compressed": True,
                    "original_size": orig_size,
                    "compressed_size": comp_size,
                    "saved_bytes": saved,
                    "ratio_pct": ratio,
                    "original_dims": (orig_w, orig_h),
                    "final_dims": (final_w, final_h),
                    "quality": jpeg_quality,
                    "display_str": disp,
                }
            else:
                return orig_bytes, {
                    "compressed": False,
                    "original_size": orig_size,
                    "compressed_size": orig_size,
                    "saved_bytes": 0,
                    "ratio_pct": 0.0,
                    "display_str": f"{orig_mb:.2f}MB (Original retained)",
                }
    except Exception as exc:
        logger.warning("Compression fallback for %s: %s", p.name, exc)
        return orig_bytes, {
            "compressed": False,
            "error": str(exc),
            "original_size": orig_size,
            "compressed_size": orig_size,
            "saved_bytes": 0,
            "ratio_pct": 0.0,
            "display_str": f"{orig_mb:.2f}MB (Fallback)",
        }



@dataclass
class ITMSWebSessionData:
    base_url: str = "https://stock.itms.ug"
    user_email: str = ""
    user_uuid: str = ""
    user_display_name: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    csrf_token: str = ""
    is_authenticated: bool = False
    saved_at: float = 0.0
    expires_at: float = 0.0  # Unix timestamp (default: 30 days)
    last_verified_at: float = 0.0
    last_status_message: str = ""

    def is_cookie_valid(self, buffer_seconds: int = 120) -> bool:
        """Return True if session cookies exist and have not expired."""
        if not self.cookies or not self.cookies.get("_identity-frontend"):
            return False
        if self.expires_at <= 0:
            return True
        return time.time() < (self.expires_at - buffer_seconds)

    def is_recently_verified(self, max_age_seconds: int = 300) -> bool:
        """Rate-limiting check: return True if verified within the cooldown window."""
        if self.last_verified_at <= 0:
            return False
        return (time.time() - self.last_verified_at) < max_age_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ITMSWebSessionData":
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class ITMSWebSessionStore:
    """Manages disk persistence and loading of ITMS WebApp cookies."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else get_default_session_file()
        self._session: ITMSWebSessionData = ITMSWebSessionData()
        self.load()

    def load(self) -> ITMSWebSessionData:
        if not self.storage_path.is_file():
            self._session = ITMSWebSessionData()
            return self._session

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._session = ITMSWebSessionData.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load ITMS web session from %s: %s", self.storage_path, exc)
            self._session = ITMSWebSessionData()
        return self._session

    def save(self, session_data: ITMSWebSessionData) -> ITMSWebSessionData:
        self._session = session_data
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(session_data.to_dict(), f, indent=2)
            try:
                os.chmod(self.storage_path, 0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to write ITMS web session to %s: %s", self.storage_path, exc)
        return self._session

    def clear(self) -> None:
        self._session = ITMSWebSessionData()
        if self.storage_path.is_file():
            try:
                self.storage_path.unlink()
            except OSError:
                pass

    @property
    def session(self) -> ITMSWebSessionData:
        return self._session


class ITMSWebClient:
    """
    Client for interacting with the ITMS Web Application (https://stock.itms.ug).
    Provides safe session management, login/logout, rate-limit safe verification,
    and read-only preview of ITMS modules.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        session_store: Optional[ITMSWebSessionStore] = None,
        timeout: int = 15,
    ):
        self.base_url = (base_url or get_itms_base_url()).rstrip("/")
        self.session_store = session_store or ITMSWebSessionStore()
        self.timeout = timeout

    def _create_requests_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        })

        # Configure robust connection pooling & automatic retry for TCP resets (e.g. WinError 10054)
        retry_strategy = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)

        # Inject stored cookies if available
        stored_cookies = self.session_store.session.cookies
        for name, val in stored_cookies.items():
            s.cookies.set(name, val, domain=urllib.parse.urlparse(self.base_url).hostname)
        return s

    def _request_with_retry(
        self,
        method: str,
        url: str,
        max_attempts: int = 3,
        **kwargs
    ) -> requests.Response:
        """
        Executes an HTTP request with automatic recovery against TCP resets
        (such as ConnectionResetError / WinError 10054) and transient network drops.
        """
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            s = self._create_requests_session()
            try:
                if "timeout" not in kwargs:
                    kwargs["timeout"] = self.timeout
                verb = method.lower()
                caller = getattr(s, verb, None)
                if caller is not None:
                    resp = caller(url, **kwargs)
                else:
                    resp = s.request(method, url, **kwargs)
                return resp
            except (requests.exceptions.ConnectionError, ConnectionResetError, requests.exceptions.ChunkedEncodingError) as exc:
                last_exc = exc
                logger.warning(
                    "ITMS WebApp connection reset (attempt %d/%d) on %s: %s",
                    attempt, max_attempts, url, exc
                )
                if attempt < max_attempts:
                    time.sleep(0.6 * attempt)
                    continue
                raise
            except requests.exceptions.RequestException:
                raise
        if last_exc:
            raise last_exc

    # ──────────────────────────────────────────────────────────────────────────
    # Diagnostic / Connectivity (Rate-Limit Friendly)
    # ──────────────────────────────────────────────────────────────────────────

    def test_connection(self) -> Dict[str, Any]:
        """Performs a single lightweight reachability check against the ITMS base URL."""
        start = time.time()
        try:
            s = self._create_requests_session()
            resp = s.get(self.base_url, timeout=self.timeout, allow_redirects=True)
            elapsed_ms = round((time.time() - start) * 1000, 1)
            is_ok = resp.status_code in (200, 301, 302)
            server = resp.headers.get("Server", "nginx")
            return {
                "success": is_ok,
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "server": server,
                "url": self.base_url,
                "message": f"Server reached in {elapsed_ms}ms (HTTP {resp.status_code}, {server})",
                "error": "" if is_ok else f"HTTP Status {resp.status_code}",
            }
        except requests.exceptions.SSLError as exc:
            return {
                "success": False,
                "status_code": 0,
                "error": f"SSL Certificate Error: {exc}",
                "url": self.base_url,
            }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "status_code": 0,
                "error": f"Connection Error: {exc}",
                "url": self.base_url,
            }

    # ──────────────────────────────────────────────────────────────────────────
    # Session Verification (With Rate-Limit Cooldown)
    # ──────────────────────────────────────────────────────────────────────────

    def verify_session(self, force: bool = False, max_age_seconds: int = 300) -> Dict[str, Any]:
        """
        Validates the active session against ITMS WebApp.
        Uses rate-limiting cooldown to avoid reaching API/request limits:
        if verified within `max_age_seconds` and not `force`, returns cached status.
        """
        session_data = self.session_store.session

        if not session_data.is_cookie_valid():
            return {
                "valid": False,
                "cached": False,
                "error": "No active session cookies found. Please sign in.",
                "session": session_data.to_dict(),
            }

        # Rate-limiting check
        if not force and session_data.is_recently_verified(max_age_seconds=max_age_seconds):
            return {
                "valid": session_data.is_authenticated,
                "cached": True,
                "message": f"Session verified (cached {int(time.time() - session_data.last_verified_at)}s ago).",
                "session": session_data.to_dict(),
            }

        s = self._create_requests_session()
        try:
            resp = s.get(f"{self.base_url}/", timeout=self.timeout, allow_redirects=True)
            # If redirected to /site/login, session is expired
            if "/site/login" in resp.url or ("Login" in resp.text and "login-form" in resp.text):
                session_data.is_authenticated = False
                session_data.last_status_message = "Session has expired on ITMS server."
                self.session_store.save(session_data)
                return {
                    "valid": False,
                    "cached": False,
                    "error": "Session has expired on ITMS WebApp. Please sign in again.",
                    "session": session_data.to_dict(),
                }

            if resp.status_code == 200:
                # Extract fresh CSRF token if present
                csrf_token = self._extract_csrf_from_html(resp.text)
                if csrf_token:
                    session_data.csrf_token = csrf_token

                # Extract user UUID if present in profile link
                user_uuid = self._extract_user_uuid_from_html(resp.text)
                if user_uuid:
                    session_data.user_uuid = user_uuid

                # Update cookies from response
                new_cookies = s.cookies.get_dict()
                if new_cookies:
                    session_data.cookies.update(new_cookies)

                session_data.is_authenticated = True
                session_data.last_verified_at = time.time()
                session_data.last_status_message = "Session is active and valid."
                self.session_store.save(session_data)

                return {
                    "valid": True,
                    "cached": False,
                    "message": "Session successfully verified against ITMS WebApp.",
                    "session": session_data.to_dict(),
                }

            return {
                "valid": False,
                "cached": False,
                "error": f"Verification received unexpected HTTP status {resp.status_code}",
                "session": session_data.to_dict(),
            }

        except requests.exceptions.RequestException as exc:
            return {
                "valid": False,
                "cached": False,
                "error": f"Failed to reach ITMS WebApp for verification: {exc}",
                "session": session_data.to_dict(),
            }

    # ──────────────────────────────────────────────────────────────────────────
    # Authentication (Login & Logout)
    # ──────────────────────────────────────────────────────────────────────────

    def login(
        self,
        email: str,
        password: str,
        remember_me: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes Yii2 form login against /site/login.
        Flow:
          1. GET /site/login to acquire CSRF cookie and form token.
          2. POST /site/login with form payload.
          3. Inspect response: HTTP 302 Found = Success; HTTP 200 = Parse validation error.
        """
        email = (email or "").strip()
        password = password or ""

        if not email or not password:
            return False, "Email and password are required.", {}

        login_url = f"{self.base_url}/site/login"
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        try:
            # Step 1: Initial GET to acquire CSRF token
            get_resp = s.get(login_url, timeout=self.timeout)
            if get_resp.status_code != 200:
                return False, f"Could not load login page (HTTP {get_resp.status_code}).", {}

            csrf_token = self._extract_csrf_from_html(get_resp.text)
            if not csrf_token:
                # Fallback to cookie if present
                csrf_token = s.cookies.get("_csrf-frontend", "")

            # Step 2: POST form login
            post_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_url,
                "Origin": self.base_url,
            }
            form_data = {
                "_csrf-frontend": csrf_token,
                "LoginForm[email]": email,
                "LoginForm[password]": password,
            }
            if remember_me:
                form_data["LoginForm[rememberMe]"] = "on"

            post_resp = s.post(
                login_url,
                data=form_data,
                headers=post_headers,
                timeout=self.timeout,
                allow_redirects=False,  # Don't auto-follow so we can catch 302
            )

            # Step 3: Evaluate response
            if post_resp.status_code == 302:
                # Login succeeded!
                cookies_dict = s.cookies.get_dict()
                identity_cookie = cookies_dict.get("_identity-frontend", "")

                # Extract user UUID from cookie
                user_uuid = self._extract_uuid_from_cookie(identity_cookie)

                # Save session
                expires_at = time.time() + (2592000 if remember_me else 86400)
                session_data = ITMSWebSessionData(
                    base_url=self.base_url,
                    user_email=email,
                    user_uuid=user_uuid,
                    cookies=cookies_dict,
                    csrf_token=csrf_token,
                    is_authenticated=True,
                    saved_at=time.time(),
                    expires_at=expires_at,
                    last_verified_at=time.time(),
                    last_status_message=f"Connected as {email}",
                )
                self.session_store.save(session_data)
                logger.info("Successfully authenticated to ITMS WebApp as %s", email)
                return True, f"Successfully authenticated as {email}.", session_data.to_dict()

            elif post_resp.status_code == 200:
                # Login failed: extract error from response HTML
                err_match = re.search(r'<div class="invalid-feedback">([^<]+)</div>', post_resp.text)
                if err_match and err_match.group(1).strip():
                    err_msg = err_match.group(1).strip()
                elif "Incorrect username or password" in post_resp.text:
                    err_msg = "Incorrect username or password."
                elif "Email cannot be blank" in post_resp.text:
                    err_msg = "Email cannot be blank."
                elif "Password cannot be blank" in post_resp.text:
                    err_msg = "Password cannot be blank."
                else:
                    err_msg = "Authentication failed. Please check your credentials."

                return False, err_msg, {}

            else:
                return False, f"Unexpected response from ITMS server (HTTP {post_resp.status_code}).", {}

        except requests.exceptions.SSLError as exc:
            return False, f"SSL Certificate Error: {exc}", {}
        except requests.exceptions.RequestException as exc:
            return False, f"Network error connecting to ITMS: {exc}", {}

    def logout(self) -> Tuple[bool, str]:
        """Safely terminates the ITMS WebApp session and clears stored cookies."""
        session_data = self.session_store.session
        if not session_data.cookies:
            self.session_store.clear()
            return True, "No active session to terminate."

        s = self._create_requests_session()
        logout_url = f"{self.base_url}/site/logout"
        csrf_token = session_data.csrf_token

        try:
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{self.base_url}/",
                "Origin": self.base_url,
            }
            form_data = {"_csrf-frontend": csrf_token} if csrf_token else {}
            s.post(logout_url, data=form_data, headers=headers, timeout=self.timeout)
        except Exception as exc:
            logger.warning("Logout request to ITMS encountered error (clearing local session anyway): %s", exc)
        finally:
            self.session_store.clear()

        return True, "Successfully logged out of ITMS WebApp."

    # ──────────────────────────────────────────────────────────────────────────
    # Read-Only Inspection & Verification Proof (Safe Mode: ZERO writes)
    # ──────────────────────────────────────────────────────────────────────────

    def fetch_read_only_dashboard(self) -> Dict[str, Any]:
        """
        Safely fetches the ITMS Dashboard (GET /) to verify access and metadata.
        Zero data modification requests are performed.
        """
        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        s = self._create_requests_session()
        try:
            resp = s.get(f"{self.base_url}/", timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Dashboard returned HTTP {resp.status_code}",
                }

            html = resp.text
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "Unknown"

            profile_uuid = self._extract_user_uuid_from_html(html) or session_data.user_uuid

            # Detect accessible modules from navigation
            modules = []
            if "/installation-orders/index" in html:
                modules.append("Installation Orders (/installation-orders/index)")
            if "/installation-kits" in html:
                modules.append("Installation Kits (/installation-kits)")
            if "/user/" in html:
                modules.append(f"User Profile (/user/{profile_uuid}/main/information)")

            return {
                "success": True,
                "url": f"{self.base_url}/",
                "page_title": title,
                "user_uuid": profile_uuid,
                "user_email": session_data.user_email,
                "accessible_modules": modules,
                "server_time": resp.headers.get("Date", "Unknown"),
                "status_message": f"Dashboard verified: {title}",
            }
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": f"Connection error: {exc}"}

    def fetch_read_only_orders_preview(self) -> Dict[str, Any]:
        """
        Safely fetches the Installation Orders page (GET /installation-orders/index)
        to inspect table structure and confirm connectivity. Pure GET request.
        """
        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        s = self._create_requests_session()
        url = f"{self.base_url}/installation-orders/index"
        try:
            resp = s.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Orders page returned HTTP {resp.status_code}",
                }

            html = resp.text
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "Orders"

            # Parse table column headers if a table exists
            headers = re.findall(r"<th[^>]*>(.*?)</th>", html, re.DOTALL | re.IGNORECASE)
            clean_headers = [re.sub(r"<[^>]+>", "", h).strip() for h in headers if h.strip()]

            return {
                "success": True,
                "url": url,
                "page_title": title,
                "table_headers": clean_headers[:10],
                "status_message": f"Fitment Orders module verified reachable ({len(clean_headers)} columns detected)",
            }
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": f"Connection error: {exc}"}

    def fetch_installation_orders(
        self,
        page: int = 1,
        search_params: Optional[Any] = None,
        archive: bool = False,
    ) -> Dict[str, Any]:
        """
        Safely fetches installation orders from https://stock.itms.ug/installation-orders/index
        or from the completed archive https://stock.itms.ug/installation-orders/archive.

        Supports pagination (?page=2, ?page=3...) and search filters:
          - registration_number: 'UMA 835DS' or 'UMA835DS'
          - vin: 'LC6PCJBJ8S0052136'
          - old_registration_number: string
          - status: '1' (Ready), '2' (Under installation), '3' (Ready for approve), '4' (Installed), '5' (Defected), '6' (Confirmation)
          - service_type: '1' (First Time Registration), '2' (Post Registration)
          - warehouse_id: UUID string
          - Can also accept a full ITMS URL or query string directly!
        """
        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
                "orders": [],
                "page": page,
                "is_archive": archive,
            }

        # If search_params is a string (e.g. full URL or query string), parse it automatically
        if isinstance(search_params, str):
            raw_str = search_params.strip()
            if "/archive" in raw_str:
                archive = True
            if raw_str.startswith("http://") or raw_str.startswith("https://") or "?" in raw_str or "=" in raw_str:
                parsed = urllib.parse.urlparse(raw_str)
                qs = urllib.parse.parse_qs(parsed.query if parsed.query else raw_str)
                search_params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in qs.items()}
                if "page" in search_params and page == 1:
                    try:
                        page = int(search_params["page"])
                    except ValueError:
                        pass
            else:
                search_params = {"registration_number": raw_str}

        s = self._create_requests_session()
        endpoint_path = "/installation-orders/archive" if archive else "/installation-orders/index"
        url = f"{self.base_url}{endpoint_path}"

        query_params = {}
        if page > 1:
            query_params["page"] = page

        if search_params:
            for k, v in search_params.items():
                if v is None or v == "":
                    continue
                v_str = str(v).strip()
                if not v_str:
                    continue
                if k in ("registration_number", "InstallationOrderSearch[registration_number]"):
                    # Standard Ugandan vehicle/motorcycle plate: 3 letters + space + 3 digits + 1-2 letters (e.g. UMA 946DQ)
                    # ITMS WebApp database stores plates WITH space ("UMA 946DQ").
                    # If operator types unspaced "UMA946DQ", format it to "UMA 946DQ" so ITMS SQL matches.
                    m = re.match(r"^([A-Za-z]{3})\s*(\d{3}[A-Za-z]{1,2})$", v_str)
                    formatted_plate = f"{m.group(1).upper()} {m.group(2).upper()}" if m else v_str
                    query_params["InstallationOrderSearch[registration_number]"] = formatted_plate
                elif k in ("vin", "old_registration_number", "status", "service_type", "warehouse_id"):
                    query_params[f"InstallationOrderSearch[{k}]"] = v_str
                elif k.startswith("InstallationOrderSearch["):
                    query_params[k] = v_str
                else:
                    query_params[k] = v_str

        try:
            resp = self._request_with_retry("GET", url, params=query_params, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Orders endpoint returned HTTP {resp.status_code}",
                    "orders": [],
                    "page": page,
                    "is_archive": archive,
                }

            html = resp.text

            # Parse headers
            th_matches = re.findall(r"<th[^>]*>(.*?)</th>", html, re.DOTALL | re.IGNORECASE)
            headers = [re.sub(r"<[^>]+>", "", th).strip() for th in th_matches if th.strip()]

            # Parse table rows
            pattern = r'<tr[^>]*data-url=["\']([^"\']+)["\'][^>]*>(.*?)</tr>'
            tr_matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)

            orders = []
            for action_url, row_html in tr_matches:
                tds = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL | re.IGNORECASE)
                clean_tds = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
                if clean_tds:
                    key_match = re.search(r"id=([0-9a-fA-F-]+)", action_url)
                    order_key = key_match.group(1) if key_match else ""

                    wh_match = re.search(r"/warehouse/([0-9a-fA-F-]+)/", row_html)
                    warehouse_id = wh_match.group(1) if wh_match else ""

                    is_row_archived = bool(archive)

                    order_num = clean_tds[0] if len(clean_tds) > 0 else ""
                    sales_order = clean_tds[1] if len(clean_tds) > 1 else ""
                    service_type = clean_tds[2] if len(clean_tds) > 2 else ""
                    vin = clean_tds[3] if len(clean_tds) > 3 else ""
                    reg_number = clean_tds[4] if len(clean_tds) > 4 else ""
                    old_reg = clean_tds[5] if len(clean_tds) > 5 else ""
                    warehouse = clean_tds[6] if len(clean_tds) > 6 else ""

                    if is_row_archived:
                        order_status = clean_tds[7] if len(clean_tds) > 7 else ""
                        registration_status = clean_tds[8] if len(clean_tds) > 8 else ""
                        officer = clean_tds[9] if len(clean_tds) > 9 else ""
                        installation_date = clean_tds[10] if len(clean_tds) > 10 else ""
                    else:
                        order_status = clean_tds[7] if len(clean_tds) > 7 else ""
                        registration_status = ""
                        officer = clean_tds[8] if len(clean_tds) > 8 else ""
                        installation_date = clean_tds[9] if len(clean_tds) > 9 else ""

                    orders.append({
                        "order_number": order_num,
                        "sales_order": sales_order,
                        "service_type": service_type,
                        "vin": vin,
                        "registration_number": reg_number,
                        "old_registration_number": old_reg,
                        "warehouse": warehouse,
                        "warehouse_id": warehouse_id,
                        "status": order_status,
                        "order_status": order_status,
                        "registration_status": registration_status,
                        "officer": officer,
                        "installation_date": installation_date,
                        "order_key": order_key,
                        "action_url": action_url,
                        "is_archived": is_row_archived,
                    })

            # Parse summary & pagination
            summary_match = re.search(r'<div[^>]*class=["\'][^"\']*summary[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
            summary_text = re.sub(r"<[^>]+>", "", summary_match.group(1)).strip() if summary_match else ""

            # Check next page link
            next_page_str = f"page={page + 1}"
            has_next = (next_page_str in html) or (len(orders) == 20)

            archive_label = "Archive" if archive else "Active"
            return {
                "success": True,
                "url": resp.url,
                "page": page,
                "count": len(orders),
                "orders": orders,
                "headers": headers,
                "has_next_page": has_next,
                "summary": summary_text,
                "is_archive": archive,
                "status_message": f"Successfully retrieved {len(orders)} {archive_label} order(s) on Page {page}.",
            }

        except (requests.exceptions.ConnectionError, ConnectionResetError) as exc:
            logger.warning("Connection reset while fetching orders from %s: %s", url, exc)
            return {
                "success": False,
                "error": "Remote host temporarily closed connection (WinError 10054). Please retry.",
                "orders": [],
                "page": page,
                "is_archive": archive,
            }
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": f"Connection error: {exc}", "orders": [], "page": page, "is_archive": archive}

    def fetch_archive_orders(
        self,
        page: int = 1,
        search_params: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Convenience method to query the completed orders archive (/installation-orders/archive)."""
        return self.fetch_installation_orders(page=page, search_params=search_params, archive=True)

    def sync_orders_to_local_db(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Saves or updates fetched ITMS installation orders (active or archive) into the local Django database.
        Allows fuzzy order matcher and verification review queue to correlate evidence photos with live ITMS records.
        """
        from django.utils import timezone
        from django.db import models
        from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
        from core.vision import normalizer

        created_count = 0
        updated_count = 0
        verified_installed_count = 0

        for o in orders:
            order_num = o.get("order_number", "").strip()
            reg_num = o.get("registration_number", "").strip()
            if not order_num or not reg_num:
                continue

            canonical_reg = normalizer.canonicalize(reg_num) or reg_num.replace(" ", "").upper()
            is_archived = bool(o.get("is_archived", False))
            order_status = o.get("order_status") or o.get("status", "")
            action_url = o.get("action_url", "")

            # Determine local system status & active/archive flags
            is_installed = "installed" in order_status.lower()
            if is_archived:
                local_status = InstallationOrder.Status.INSTALLED if is_installed else InstallationOrder.Status.SUBMITTED
                is_active = False
                stage = "ARCHIVED"
            elif is_installed:
                local_status = InstallationOrder.Status.INSTALLED
                is_archived = True
                is_active = False
                stage = "ARCHIVED"
            elif "approve" in order_status.lower():
                local_status = InstallationOrder.Status.SUBMITTED
                is_active = True
                is_archived = False
            else:
                local_status = InstallationOrder.Status.PENDING
                is_active = True
                is_archived = False

            if not is_archived:
                if "/confirmation" in action_url:
                    stage = "STAGE_3_CONFIRMATION"
                elif "/approve" in action_url:
                    stage = "STAGE_2_APPROVE"
                elif "/installation" in action_url:
                    stage = "STAGE_1_INSTALLATION"
                else:
                    stage = "STAGE_UNKNOWN"

            defaults = {
                "registration_number": canonical_reg,
                "sales_order": o.get("sales_order", ""),
                "service_type": o.get("service_type", ""),
                "vin": o.get("vin", ""),
                "old_registration_number": o.get("old_registration_number", ""),
                "warehouse_name": o.get("warehouse", ""),
                "warehouse_id": o.get("warehouse_id", ""),
                "order_status": order_status,
                "registration_status": o.get("registration_status", ""),
                "installation_officer": o.get("officer", ""),
                "installation_date": o.get("installation_date", ""),
                "itms_order_uuid": o.get("order_key", ""),
                "itms_action_url": action_url,
                "is_archived": is_archived,
                "is_active_on_itms": is_active,
                "itms_stage": stage,
                "status": local_status,
                "last_synced_at": timezone.now(),
            }

            session_obj = getattr(self.session_store, "session", None)
            email_val = getattr(session_obj, "user_email", "")
            uuid_val = getattr(session_obj, "user_uuid", "")
            curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
            curr_uuid = str(uuid_val) if isinstance(uuid_val, str) else ""
            if curr_email:
                defaults["account_email"] = curr_email
            if curr_uuid:
                defaults["account_uuid"] = curr_uuid

            obj, was_created = InstallationOrder.objects.update_or_create(
                order_number=order_num,
                defaults=defaults,
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

            # Cross-verify and record audit trail for any matching vehicle pairs
            # ONLY when legitimately fetched from archive and confirmed as Installed!
            if is_archived and is_installed and local_status == InstallationOrder.Status.INSTALLED:
                verified_installed_count += 1
                pairs = VehicleInstallationPair.objects.filter(
                    models.Q(order=obj) | models.Q(registration_number_detected=canonical_reg)
                )
                now = timezone.now()
                for pair in pairs:
                    if pair.verification_status != VehicleInstallationPair.VerificationStatus.SUBMITTED:
                        pair.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
                        pair.submitted_at = pair.submitted_at or now
                        pair.order = obj
                        pair.save(update_fields=["verification_status", "order", "submitted_at"])
                        for img in (pair.front_image, pair.rear_image):
                            if img and img.status != EvidenceImage.Status.SUBMITTED:
                                img.status = EvidenceImage.Status.SUBMITTED
                                img.submitted_at = img.submitted_at or now
                                img.save(update_fields=["status", "submitted_at"])
                        SubmissionAuditLog.objects.create(
                            pair=pair,
                            action=SubmissionAuditLog.Action.ARCHIVE_VERIFY,
                            result=SubmissionAuditLog.ResultStatus.SUCCESS,
                            message=(
                                f"Confirmed Installed in ITMS Archive: {order_num} "
                                f"(Date: {o.get('installation_date', 'N/A')}, Officer: {o.get('officer', 'N/A')})"
                            ),
                        )

        return {
            "created": created_count,
            "updated": updated_count,
            "installed_verified": verified_installed_count,
            "total": len(orders),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Order Info & Plate Photo Extraction (Safe Read-Only)
    # ──────────────────────────────────────────────────────────────────────────

    def parse_order_info_html(self, html: str) -> Dict[str, Any]:
        """
        Parses the HTML of an ITMS order info page (/installation-orders/info?id=...).
        Extracts:
          - Order number (e.g. PO-UMA282PG-080926)
          - Header metadata: Installed By, Warehouse, VIN/Chassis
          - Hardware inventory: Front/Rear plate types, serials/barcodes, plate texts
          - Telematics: GPS tracker ID, Front beacon ID, Rear beacon ID
          - Plate evidence photos: Front & Rear photo URLs, filenames, labels
        """
        title_m = re.search(r"<h4[^>]*class=[\"'][^\"']*heading-title[^\"']*[\"'][^>]*>(.*?)</h4>", html, re.DOTALL | re.IGNORECASE)
        title_text = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
        order_num_m = re.search(r"Installation\s+order:\s*#?([A-Za-z0-9\-]+)", title_text, re.IGNORECASE)
        order_number = order_num_m.group(1) if order_num_m else title_text

        # Extract all <p class="mb-0"><strong>Key</strong>:&ensp;Value</p>
        p_matches = re.findall(r"<p[^>]*class=[\"'][^\"']*mb-0[^\"']*[\"'][^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
        raw_kv = {}
        for p in p_matches:
            m = re.search(r"<strong[^>]*>(.*?)</strong>\s*:\s*(?:&ensp;|\s)*(.*)", p, re.DOTALL | re.IGNORECASE)
            if m:
                k = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                v = re.sub(r"&nbsp;", " ", m.group(2))
                v = re.sub(r"<[^>]+>", "", v).strip()
                raw_kv[k] = v

        installed_by = raw_kv.get("Installed By", "")
        warehouse = raw_kv.get("Installation Warehouse", "")
        vin = raw_kv.get("Vehicle VIN/Chassis No.", "")

        def parse_plate_entry(raw_str: str) -> Dict[str, str]:
            # Format: 'PN-PBL-M-UMA-SQR-BWW (001107363) (UMA 282PG)'
            if not raw_str:
                return {"raw": "", "type": "", "serial": "", "plate": ""}
            m = re.match(r"^(.*?)\s*\((.*?)\)\s*\((.*?)\)$", raw_str)
            if m:
                return {
                    "raw": raw_str,
                    "type": m.group(1).strip(),
                    "serial": m.group(2).strip(),
                    "plate": m.group(3).strip(),
                }
            return {"raw": raw_str, "type": "", "serial": "", "plate": ""}

        def parse_device_entry(raw_str: str) -> Dict[str, str]:
            # Format: 'GPS (8BAE47076F84)' or 'BLE (8ADC470F6032)'
            if not raw_str:
                return {"raw": "", "type": "", "device_id": ""}
            m = re.match(r"^(.*?)\s*\((.*?)\)$", raw_str)
            if m:
                return {
                    "raw": raw_str,
                    "type": m.group(1).strip(),
                    "device_id": m.group(2).strip(),
                }
            return {"raw": raw_str, "type": "", "device_id": ""}

        front_plate = parse_plate_entry(raw_kv.get("Front license plate", ""))
        rear_plate = parse_plate_entry(raw_kv.get("Rear license plate", ""))
        gps_tracker = parse_device_entry(raw_kv.get("GPS Tracker", ""))
        front_beacon = parse_device_entry(raw_kv.get("Front beacon", ""))
        rear_beacon = parse_device_entry(raw_kv.get("Rear beacon", ""))

        registration_number = front_plate.get("plate") or rear_plate.get("plate") or ""

        # Extract photos
        photos = []
        front_photo_url = ""
        rear_photo_url = ""

        # Photo blocks: <div class="col"> ... <strong>...</strong> ... <img src="...">
        for block in re.finditer(r"<div[^>]*class=[\"']col[\"'][^>]*>(.*?)</div>\s*</div>", html, re.DOTALL | re.IGNORECASE):
            b_html = block.group(1)
            lbl_m = re.search(r"<strong[^>]*>(.*?)</strong>", b_html, re.DOTALL | re.IGNORECASE)
            img_m = re.search(r"<img[^>]*src=[\"']([^\"']+)[\"']", b_html, re.DOTALL | re.IGNORECASE)
            if img_m:
                lbl = re.sub(r"<[^>]+>", "", lbl_m.group(1)).strip() if lbl_m else "Plate photo"
                src = img_m.group(1).strip()
                full_url = urllib.parse.urljoin(self.base_url, src)
                filename = os.path.basename(urllib.parse.urlparse(src).path)
                orientation = "FRONT" if "front" in lbl.lower() else ("REAR" if "rear" in lbl.lower() else "UNKNOWN")

                entry = {
                    "label": lbl,
                    "url": full_url,
                    "relative_url": src,
                    "filename": filename,
                    "orientation": orientation,
                }
                photos.append(entry)
                if orientation == "FRONT" and not front_photo_url:
                    front_photo_url = full_url
                elif orientation == "REAR" and not rear_photo_url:
                    rear_photo_url = full_url

        return {
            "order_number": order_number,
            "page_title": title_text,
            "installed_by": installed_by,
            "warehouse": warehouse,
            "vin": vin,
            "registration_number": registration_number,
            "front_plate": front_plate,
            "rear_plate": rear_plate,
            "gps_tracker": gps_tracker,
            "front_beacon": front_beacon,
            "rear_beacon": rear_beacon,
            "photos": photos,
            "front_photo_url": front_photo_url,
            "rear_photo_url": rear_photo_url,
            "raw_details": raw_kv,
        }

    def fetch_order_info(
        self,
        order_identifier: str,
        download_photos: bool = False,
        save_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Safely fetches and parses the full order details page:
          GET https://stock.itms.ug/installation-orders/info?id=<uuid>

        Accepts:
          - An ITMS UUID (e.g. 'ca7845a2-2488-495a-82cf-6a2e1502fa12')
          - A full/relative ITMS info URL (e.g. '/installation-orders/info?id=...')
          - An order number (e.g. 'PO-UMA282PG-080926')
          - A registration plate number (e.g. 'UMA 282PG')

        Returns:
          A structured dictionary with all metadata, hardware serials, beacons, tracker,
          and photo links (with optional local download paths).
        """
        from django.db import models

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        target_uuid = ""
        ident = (order_identifier or "").strip()
        if not ident:
            return {"success": False, "error": "Order identifier cannot be empty."}

        # Case 1: Check if ident contains UUID directly or in a URL
        uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        uuid_match = re.search(uuid_pattern, ident)
        if uuid_match:
            target_uuid = uuid_match.group(1)

        # Case 2: Look up in local database if order_number, vin, or plate
        if not target_uuid:
            try:
                from core.models import InstallationOrder
                from core.vision import normalizer
                canonical = normalizer.canonicalize(ident) or ident.replace(" ", "").upper()
                order_rec = InstallationOrder.objects.filter(
                    models.Q(order_number__iexact=ident) |
                    models.Q(registration_number__iexact=canonical) |
                    models.Q(vin__iexact=ident)
                ).exclude(itms_order_uuid="").first()
                if order_rec and order_rec.itms_order_uuid:
                    target_uuid = order_rec.itms_order_uuid
            except Exception:
                pass

        # Case 3: Search live ITMS archive or active orders to resolve the UUID
        if not target_uuid:
            archive_res = self.fetch_archive_orders(page=1, search_params=ident)
            orders = archive_res.get("orders", [])
            for o in orders:
                if o.get("order_key"):
                    target_uuid = o["order_key"]
                    break
            if not target_uuid:
                active_res = self.fetch_installation_orders(page=1, search_params=ident, archive=False)
                active_orders = active_res.get("orders", [])
                for o in active_orders:
                    if o.get("order_key"):
                        target_uuid = o["order_key"]
                        break

        if not target_uuid:
            return {
                "success": False,
                "error": f"Could not resolve ITMS UUID for '{ident}'. Please ensure order exists on stock.itms.ug.",
            }

        url = f"{self.base_url}/installation-orders/info?id={target_uuid}"
        headers = {"Referer": f"{self.base_url}/installation-orders/archive"}

        try:
            resp = self._request_with_retry("GET", url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Order info returned HTTP {resp.status_code}",
                    "url": url,
                    "order_uuid": target_uuid,
                }

            parsed = self.parse_order_info_html(resp.text)
            parsed["success"] = True
            parsed["order_uuid"] = target_uuid
            parsed["url"] = url

            # Optionally download photos
            if download_photos:
                downloaded_photos = []
                order_num = parsed.get("order_number") or target_uuid
                vault_root = getattr(settings, "VAULT_ROOT", "media/vault") if settings.configured else "media/vault"
                order_dir = save_dir or (Path(vault_root) / "itms_photos" / order_num)
                order_dir = Path(order_dir)
                order_dir.mkdir(parents=True, exist_ok=True)

                for p in parsed.get("photos", []):
                    dl_res = self.download_photo(
                        p["url"],
                        save_dir=order_dir,
                        order_number=order_num,
                    )
                    p_copy = dict(p)
                    if dl_res.get("success"):
                        p_copy["local_path"] = dl_res["path"]
                        p_copy["bytes"] = dl_res["bytes"]
                        p_copy["sha256"] = dl_res.get("sha256", "")
                    else:
                        p_copy["download_error"] = dl_res.get("error")
                    downloaded_photos.append(p_copy)
                parsed["photos"] = downloaded_photos

                # Write order_manifest.json for safe archival & forensics
                try:
                    from django.utils import timezone
                    manifest_data = {
                        "order_number": order_num,
                        "order_uuid": target_uuid,
                        "registration_number": parsed.get("registration_number", ""),
                        "vin": parsed.get("vin", ""),
                        "warehouse": parsed.get("warehouse", ""),
                        "installed_by": parsed.get("installed_by", ""),
                        "downloaded_at": timezone.now().isoformat(),
                        "photos": downloaded_photos,
                        "hardware": {
                            "front_plate": parsed.get("front_plate", {}),
                            "rear_plate": parsed.get("rear_plate", {}),
                            "gps_tracker": parsed.get("gps_tracker", {}),
                            "front_beacon": parsed.get("front_beacon", {}),
                            "rear_beacon": parsed.get("rear_beacon", {}),
                        },
                    }
                    manifest_path = order_dir / "order_manifest.json"
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump(manifest_data, f, indent=2)
                    parsed["manifest_path"] = str(manifest_path)
                except Exception as exc:
                    logger.warning("Failed writing order manifest: %s", exc)

            return parsed
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "error": f"Connection error fetching order info: {exc}",
                "url": url,
                "order_uuid": target_uuid,
            }

    def download_photo(
        self,
        photo_url: str,
        save_dir: Optional[Path] = None,
        save_path: Optional[Path] = None,
        order_number: str = "",
    ) -> Dict[str, Any]:
        """
        Safely downloads an ITMS plate photo using authenticated session cookies.
        Saves locally into media/vault/itms_photos/<order_number>/<filename>.
        Computes SHA-256 checksum for cryptographic verification.
        """
        if not photo_url:
            return {"success": False, "error": "Empty photo URL"}

        full_url = urllib.parse.urljoin(self.base_url, photo_url)
        parsed_path = urllib.parse.urlparse(full_url).path
        filename = os.path.basename(parsed_path) or "photo.jpeg"

        if save_path is None:
            if save_dir is not None:
                save_path = Path(save_dir) / filename
            else:
                vault_root = getattr(settings, "VAULT_ROOT", "media/vault") if settings.configured else "media/vault"
                sub_dir = order_number.strip() if order_number else "downloads"
                save_path = Path(vault_root) / "itms_photos" / sub_dir / filename

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        headers = {"Referer": f"{self.base_url}/installation-orders/archive"}

        try:
            resp = self._request_with_retry("GET", full_url, headers=headers, timeout=self.timeout, stream=True)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code} fetching photo from {full_url}",
                }

            import hashlib
            digest = hashlib.sha256()
            total_bytes = 0
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        digest.update(chunk)
                        total_bytes += len(chunk)

            return {
                "success": True,
                "url": full_url,
                "path": str(save_path),
                "filename": filename,
                "bytes": total_bytes,
                "sha256": digest.hexdigest(),
            }
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": f"Failed downloading photo: {exc}"}

    def sync_order_info_to_local_db(
        self,
        order_info: Dict[str, Any],
        order_uuid: str = "",
    ) -> Dict[str, Any]:
        """
        Synchronizes detailed order info, hardware inventory, and photo URLs into the local InstallationOrder database record.
        """
        from django.db import models
        from django.utils import timezone
        from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
        from core.vision import normalizer

        order_num = order_info.get("order_number", "").strip()
        reg_num = order_info.get("registration_number", "").strip()
        if not order_num:
            return {"success": False, "error": "Order number missing from order info"}

        canonical_reg = normalizer.canonicalize(reg_num) or reg_num.replace(" ", "").upper()

        front_p = order_info.get("front_plate", {})
        rear_p = order_info.get("rear_plate", {})
        gps_t = order_info.get("gps_tracker", {})
        front_b = order_info.get("front_beacon", {})
        rear_b = order_info.get("rear_beacon", {})

        target_uuid = order_uuid or order_info.get("order_uuid", "")

        defaults = {
            "registration_number": canonical_reg,
            "vin": order_info.get("vin", ""),
            "warehouse_name": order_info.get("warehouse", ""),
            "installation_officer": order_info.get("installed_by", ""),
            "front_plate_serial": front_p.get("serial", ""),
            "rear_plate_serial": rear_p.get("serial", ""),
            "front_plate_type": front_p.get("type", ""),
            "rear_plate_type": rear_p.get("type", ""),
            "gps_tracker_id": gps_t.get("device_id", ""),
            "front_beacon_id": front_b.get("device_id", ""),
            "rear_beacon_id": rear_b.get("device_id", ""),
            "front_photo_url": order_info.get("front_photo_url", ""),
            "rear_photo_url": order_info.get("rear_photo_url", ""),
            "details_json": order_info.get("raw_details", {}),
            "photos_json": order_info.get("photos", []),
            "info_fetched_at": timezone.now(),
        }

        if target_uuid:
            defaults["itms_order_uuid"] = target_uuid
            defaults["itms_action_url"] = f"/installation-orders/info?id={target_uuid}"

        # Populate legacy plate_serial and tracker_id if empty
        if front_p.get("serial"):
            defaults["plate_serial"] = front_p["serial"]
        elif rear_p.get("serial"):
            defaults["plate_serial"] = rear_p["serial"]

        if gps_t.get("device_id"):
            defaults["tracker_id"] = gps_t["device_id"]

        session_obj = getattr(self.session_store, "session", None)
        email_val = getattr(session_obj, "user_email", "")
        uuid_val = getattr(session_obj, "user_uuid", "")
        curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
        curr_uuid = str(uuid_val) if isinstance(uuid_val, str) else ""
        if curr_email:
            defaults["account_email"] = curr_email
        if curr_uuid:
            defaults["account_uuid"] = curr_uuid

        order_obj, created = InstallationOrder.objects.update_or_create(
            order_number=order_num,
            defaults=defaults,
        )

        # Cross-reference with VehicleInstallationPair if available
        pairs = VehicleInstallationPair.objects.filter(
            models.Q(order=order_obj) | models.Q(registration_number_detected=canonical_reg)
        )
        for pair in pairs:
            if not pair.order_id:
                pair.order = order_obj
                pair.save(update_fields=["order"])
            SubmissionAuditLog.objects.create(
                pair=pair,
                action=SubmissionAuditLog.Action.ORDER_INFO_FETCH,
                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                message=(
                    f"Fetched order info from ITMS: {order_num} "
                    f"(Front: {front_p.get('serial', 'N/A')}, Rear: {rear_p.get('serial', 'N/A')}, "
                    f"Photos: {len(order_info.get('photos', []))})"
                ),
            )

        return {
            "success": True,
            "created": created,
            "order_number": order_num,
            "order_id": order_obj.id,
            "pairs_updated": pairs.count(),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Step 1: Installation Form, AJAX ActiveForm Validation & Step 2 Transition
    # ──────────────────────────────────────────────────────────────────────────

    def parse_installation_page_html(self, html: str) -> Dict[str, Any]:
        """
        Parses Step 1 installation page HTML:
          GET https://stock.itms.ug/installation-orders/installation?id=<uuid>
        
        Extracts:
          - CSRF token (_csrf-frontend / meta tag)
          - Form action and order UUID
          - Breadcrumb order number (e.g. PO-UMA560PJ-100926)
          - Vehicle details: VIN, Old plate, Registration Number
          - Hardware select dropdown options and selected values:
              * front_license_plate_id (selected_id, selected_text, options)
              * back_license_plate_id (selected_id, selected_text, options)
              * tracker_id (selected_id, selected_text, options)
          - Front & Rear Beacon IDs (stubs)
          - Kit ID
          - AJAX validation URL (e.g. /installation-orders/validate-installation?id=<uuid>)
          - Form action URL
        """
        # 1. CSRF Token
        csrf_token = self._extract_csrf_from_html(html)

        # 2. Form action & Order UUID
        form_m = re.search(r'<form[^>]*id=["\']installationOrderCreateForm["\'][^>]*action=["\']([^"\']+)["\']', html)
        form_action = form_m.group(1) if form_m else ""
        uuid_m = re.search(r'id=([0-9a-fA-F-]+)', form_action)
        order_uuid = uuid_m.group(1) if uuid_m else ""

        # Validation URL from yiiActiveForm config
        val_m = re.search(r'["\']validationUrl["\']\s*:\s*["\']([^"\']+)["\']', html)
        if val_m:
            raw_val_url = val_m.group(1).replace(r"\/", "/")
            validation_url = raw_val_url
        else:
            validation_url = f"/installation-orders/validate-installation?id={order_uuid}" if order_uuid else ""

        # 3. Breadcrumb Order Number
        order_num_m = re.search(r'<li[^>]*class=["\']breadcrumb-item active["\'][^>]*>(PO-[A-Za-z0-9\-]+)</li>', html)
        order_number = order_num_m.group(1) if order_num_m else ""

        # 4. Vehicle Details
        def get_dd_val(dt_label: str) -> str:
            m = re.search(rf'<dt>{re.escape(dt_label)}</dt>\s*<dd>(.*?)</dd>', html, re.DOTALL | re.IGNORECASE)
            if m:
                clean = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                return clean
            return ""

        vin = get_dd_val("Vehicle VIN/Chassis No.")
        old_plate = get_dd_val("Old plate number")
        registration_number = get_dd_val("Registration Number")

        # 5. Dropdown Select Elements
        def parse_select(select_id: str) -> Dict[str, Any]:
            m = re.search(rf'<select[^>]*id=["\']{select_id}["\'][^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
            if not m:
                return {"selected_id": "", "selected_text": "", "options": []}
            inner = m.group(1)
            options = []
            selected_id = ""
            selected_text = ""
            for opt_m in re.finditer(r'<option[^>]*value=["\']([^"\']*)["\']([^>]*)>(.*?)</option>', inner, re.DOTALL | re.IGNORECASE):
                val = opt_m.group(1).strip()
                attrs = opt_m.group(2)
                txt = opt_m.group(3).strip()
                is_sel = "selected" in attrs.lower()
                if val:
                    options.append({"value": val, "text": txt, "selected": is_sel})
                if is_sel and val:
                    selected_id = val
                    selected_text = txt
            # If nothing was explicitly marked selected, default to the first non-empty option
            if not selected_id and options:
                selected_id = options[0]["value"]
                selected_text = options[0]["text"]
            return {
                "selected_id": selected_id,
                "selected_text": selected_text,
                "options": options,
            }

        front_plate = parse_select("installationorderform-front_license_plate_id")
        rear_plate = parse_select("installationorderform-back_license_plate_id")
        tracker = parse_select("installationorderform-tracker_id")

        # 6. Beacon IDs
        front_beacon_m = re.search(r'<span[^>]*id=["\']frontBeaconIdStub["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
        front_beacon = front_beacon_m.group(1).strip() if front_beacon_m else ""

        rear_beacon_m = re.search(r'<span[^>]*id=["\']backBeaconIdStub["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
        rear_beacon = rear_beacon_m.group(1).strip() if rear_beacon_m else ""

        # 7. Kit ID
        kit_m = re.search(r'kit_id=([0-9a-fA-F-]+)', html)
        kit_id = kit_m.group(1) if kit_m else ""

        return {
            "order_uuid": order_uuid,
            "order_number": order_number,
            "csrf_token": csrf_token,
            "vin": vin,
            "old_plate": old_plate,
            "registration_number": registration_number,
            "front_plate": front_plate,
            "rear_plate": rear_plate,
            "tracker": tracker,
            "front_beacon": front_beacon,
            "rear_beacon": rear_beacon,
            "kit_id": kit_id,
            "validation_url": validation_url,
            "form_action": form_action,
        }

    def fetch_installation_step1(self, order_identifier: str) -> Dict[str, Any]:
        """
        Fetches Step 1 installation page for an order or plate number:
          GET https://stock.itms.ug/installation-orders/installation?id=<uuid>
        """
        from django.db import models

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        target_uuid = ""
        ident = (order_identifier or "").strip()
        if not ident:
            return {"success": False, "error": "Order identifier cannot be empty."}

        # Check if ident is or contains UUID
        uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        uuid_match = re.search(uuid_pattern, ident)
        if uuid_match:
            target_uuid = uuid_match.group(1)

        # Check local database
        if not target_uuid:
            try:
                from core.models import InstallationOrder
                from core.vision import normalizer
                canonical = normalizer.canonicalize(ident) or ident.replace(" ", "").upper()
                order_rec = InstallationOrder.objects.filter(
                    models.Q(order_number__iexact=ident) |
                    models.Q(registration_number__iexact=canonical) |
                    models.Q(vin__iexact=ident)
                ).exclude(itms_order_uuid="").first()
                if order_rec and order_rec.itms_order_uuid:
                    target_uuid = order_rec.itms_order_uuid
            except Exception:
                pass

        # Check live ITMS active orders table
        if not target_uuid:
            active_res = self.fetch_installation_orders(page=1, search_params=ident, archive=False)
            active_orders = active_res.get("orders", [])
            for o in active_orders:
                if o.get("order_key"):
                    target_uuid = o["order_key"]
                    break

        if not target_uuid:
            return {
                "success": False,
                "error": f"Could not resolve active ITMS installation UUID for '{ident}'.",
            }

        url = f"{self.base_url}/installation-orders/installation?id={target_uuid}"
        headers = {"Referer": f"{self.base_url}/installation-orders/index"}

        try:
            resp = self._request_with_retry("GET", url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Installation page returned HTTP {resp.status_code}",
                    "url": url,
                    "order_uuid": target_uuid,
                }

            parsed = self.parse_installation_page_html(resp.text)
            parsed["success"] = True
            parsed["order_uuid"] = target_uuid
            parsed["url"] = url
            return parsed
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "error": f"Connection error fetching installation page: {exc}",
                "url": url,
                "order_uuid": target_uuid,
            }

    def validate_installation_step1(
        self,
        order_uuid: str,
        front_plate_id: str,
        back_plate_id: str,
        tracker_id: str,
        csrf_token: Optional[str] = None,
        validation_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes AJAX validation against Yii2 ActiveForm endpoint:
          POST /installation-orders/validate-installation?id=<uuid>
        
        Headers:
          X-Requested-With: XMLHttpRequest
          X-CSRF-Token: <csrf_token>
          Referer: .../installation-orders/installation?id=<uuid>
        
        Body:
          _csrf-frontend: <csrf_token>
          ajax: installationOrderCreateForm
          InstallationOrderForm[front_license_plate_id]: <uuid>
          InstallationOrderForm[back_license_plate_id]: <uuid>
          InstallationOrderForm[tracker_id]: <uuid>

        Returns:
          {"success": True, "valid": True, "errors": {}} if response is empty [] or {}
          {"success": True, "valid": False, "errors": {...}, "error": "..."} if field validation errors
        """
        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "valid": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        target_csrf = csrf_token or session_data.csrf_token
        val_url = validation_url or f"{self.base_url}/installation-orders/validate-installation?id={order_uuid}"
        if val_url.startswith("/"):
            val_url = f"{self.base_url}{val_url}"

        s = self._create_requests_session()
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/installation-orders/installation?id={order_uuid}",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if target_csrf:
            headers["X-CSRF-Token"] = target_csrf

        payload = {
            "ajax": "installationOrderCreateForm",
            "InstallationOrderForm[front_license_plate_id]": front_plate_id,
            "InstallationOrderForm[back_license_plate_id]": back_plate_id,
            "InstallationOrderForm[tracker_id]": tracker_id,
        }
        if target_csrf:
            payload["_csrf-frontend"] = target_csrf

        try:
            resp = s.post(val_url, data=payload, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "valid": False,
                    "status_code": resp.status_code,
                    "error": f"Validation endpoint returned HTTP {resp.status_code}: {resp.text[:200]}",
                    "order_uuid": order_uuid,
                    "validation_url": val_url,
                }

            try:
                val_data = resp.json()
            except Exception:
                val_data = resp.text

            # In Yii2 ActiveForm, an empty array [] or empty dict {} means form passed validation!
            if isinstance(val_data, (list, dict)) and len(val_data) == 0:
                return {
                    "success": True,
                    "valid": True,
                    "errors": {},
                    "order_uuid": order_uuid,
                    "validation_url": val_url,
                }
            
            # If there are validation errors, Yii returns a dict of field_id -> [error messages]
            return {
                "success": True,
                "valid": False,
                "errors": val_data if isinstance(val_data, dict) else {"general": [str(val_data)]},
                "error": f"Form validation rejected by ITMS: {val_data}",
                "order_uuid": order_uuid,
                "validation_url": val_url,
            }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "valid": False,
                "error": f"Connection error during ActiveForm validation: {exc}",
                "order_uuid": order_uuid,
                "validation_url": val_url,
            }

    def submit_installation_step1(
        self,
        order_uuid: str,
        front_plate_id: str,
        back_plate_id: str,
        tracker_id: str,
        csrf_token: Optional[str] = None,
        just_save: bool = False,
        run_validation_first: bool = True,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Submits Step 1 installation form:
          POST /installation-orders/installation?id=<uuid>
        
        CRITICAL SAFETY RULE:
          dry_run defaults to None (resolving to config setting submission.dry_run_mode).
          In dry_run mode, no mutating POST request is transmitted to the live ITMS server.
          Live submission occurs ONLY when dry_run=False.

        If just_save is False (default: "Save and continue"), the ITMS server
        processes hardware fitment and redirects with HTTP 302 Found to:
          https://stock.itms.ug/installation-orders/approve?id=<uuid> (Step 2: Photos)
        """
        dry_run = resolve_dry_run(dry_run)
        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        # Step 1a: Pre-validate form inputs using AJAX ActiveForm if requested
        if run_validation_first:
            val_res = self.validate_installation_step1(
                order_uuid=order_uuid,
                front_plate_id=front_plate_id,
                back_plate_id=back_plate_id,
                tracker_id=tracker_id,
                csrf_token=csrf_token,
            )
            if not val_res.get("success") or not val_res.get("valid"):
                return {
                    "success": False,
                    "step": 1,
                    "error": val_res.get("error", "Validation failed"),
                    "validation_errors": val_res.get("errors", {}),
                    "order_uuid": order_uuid,
                }

        # Dry Run Protection Guard
        if dry_run:
            simulated_redirect = f"{self.base_url}/installation-orders/approve?id={order_uuid}"
            try:
                from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                if order_obj:
                    pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                    for p in pairs:
                        SubmissionAuditLog.objects.create(
                            pair=p,
                            action=SubmissionAuditLog.Action.SERIAL_VERIFY,
                            result=SubmissionAuditLog.ResultStatus.SUCCESS,
                            message=f"[DRY RUN] Step 1 simulated: Front={front_plate_id}, Back={back_plate_id}, Tracker={tracker_id}. Live POST skipped.",
                        )
            except Exception:
                pass

            return {
                "success": True,
                "dry_run": True,
                "step": 1,
                "status_code": 302,
                "redirect_url": simulated_redirect,
                "relative_redirect": f"/installation-orders/approve?id={order_uuid}",
                "step2_ready": True,
                "order_uuid": order_uuid,
                "message": f"[DRY RUN] Step 1 hardware fitment verified. Simulated advance to Step 2: {simulated_redirect}",
            }

        target_csrf = csrf_token or session_data.csrf_token
        url = f"{self.base_url}/installation-orders/installation?id={order_uuid}"

        s = self._create_requests_session()
        headers = {
            "Origin": self.base_url,
            "Referer": url,
            "Cache-Control": "max-age=0",
        }

        payload = {
            "InstallationOrderForm[front_license_plate_id]": front_plate_id,
            "InstallationOrderForm[back_license_plate_id]": back_plate_id,
            "InstallationOrderForm[tracker_id]": tracker_id,
        }
        if target_csrf:
            payload["_csrf-frontend"] = target_csrf
        if just_save:
            payload["justSave"] = ""

        try:
            # We must set allow_redirects=False to intercept the 302 redirect location
            resp = self._request_with_retry("POST", url, data=payload, headers=headers, timeout=self.timeout, allow_redirects=False)

            if resp.status_code == 302:
                redirect_url = resp.headers.get("Location", "")
                full_redirect_url = urllib.parse.urljoin(self.base_url, redirect_url)
                is_step2 = "/installation-orders/approve" in redirect_url

                # Audit log if pair exists
                try:
                    from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                    order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                    if order_obj:
                        pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                        for p in pairs:
                            SubmissionAuditLog.objects.create(
                                pair=p,
                                action=SubmissionAuditLog.Action.SERIAL_VERIFY,
                                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                message=f"Step 1 submitted to ITMS: Redirected to {redirect_url}",
                            )
                except Exception:
                    pass

                return {
                    "success": True,
                    "step": 1,
                    "status_code": 302,
                    "redirect_url": full_redirect_url,
                    "relative_redirect": redirect_url,
                    "step2_ready": is_step2,
                    "order_uuid": order_uuid,
                    "message": f"Step 1 submitted successfully. Advanced to Step 2: {full_redirect_url}",
                }
            elif resp.status_code == 200:
                # Returned 200 OK means form re-rendered, likely because of a server validation error
                err_m = re.findall(r'<div[^>]*class=["\'][^"\']*invalid-feedback[^"\']*["\'][^>]*>(.*?)</div>', resp.text, re.DOTALL)
                clean_errs = [re.sub(r'<[^>]+>', '', e).strip() for e in err_m if re.sub(r'<[^>]+>', '', e).strip()]
                return {
                    "success": False,
                    "step": 1,
                    "status_code": 200,
                    "error": "Form submission re-rendered page without redirecting. Validation errors encountered.",
                    "form_errors": clean_errs,
                    "order_uuid": order_uuid,
                }
            else:
                return {
                    "success": False,
                    "step": 1,
                    "status_code": resp.status_code,
                    "error": f"Submission returned unexpected HTTP {resp.status_code}",
                    "order_uuid": order_uuid,
                }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "step": 1,
                "error": f"Connection error submitting Step 1: {exc}",
                "order_uuid": order_uuid,
            }

    def sync_installation_step1_to_local_db(
        self,
        step1_data: Dict[str, Any],
        order_uuid: str = "",
    ) -> Dict[str, Any]:
        """
        Synchronizes Step 1 hardware inventory into local InstallationOrder record.
        """
        from django.db import models
        from django.utils import timezone
        from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
        from core.vision import normalizer

        order_num = step1_data.get("order_number", "").strip()
        reg_num = step1_data.get("registration_number", "").strip()
        target_uuid = order_uuid or step1_data.get("order_uuid", "")

        if not order_num and not target_uuid:
            return {"success": False, "error": "Order identifier missing"}

        canonical_reg = normalizer.canonicalize(reg_num) or reg_num.replace(" ", "").upper()
        front_p = step1_data.get("front_plate", {})
        rear_p = step1_data.get("rear_plate", {})
        tracker = step1_data.get("tracker", {})

        defaults = {
            "registration_number": canonical_reg,
            "vin": step1_data.get("vin", ""),
            "front_plate_serial": front_p.get("selected_text", ""),
            "rear_plate_serial": rear_p.get("selected_text", ""),
            "gps_tracker_id": tracker.get("selected_text", ""),
            "front_beacon_id": step1_data.get("front_beacon", ""),
            "rear_beacon_id": step1_data.get("rear_beacon", ""),
            "plate_serial": front_p.get("selected_text", "") or rear_p.get("selected_text", ""),
            "tracker_id": tracker.get("selected_text", ""),
            "itms_order_uuid": target_uuid,
            "itms_action_url": f"/installation-orders/installation?id={target_uuid}",
            "order_status": "Under installation",
            "details_json": step1_data,
            "info_fetched_at": timezone.now(),
        }

        # Look up by order_number or itms_order_uuid
        order_obj = None
        if order_num:
            order_obj = InstallationOrder.objects.filter(order_number=order_num).first()
        if not order_obj and target_uuid:
            order_obj = InstallationOrder.objects.filter(itms_order_uuid=target_uuid).first()

        created = False
        if order_obj:
            for k, v in defaults.items():
                setattr(order_obj, k, v)
            order_obj.save()
        else:
            if not order_num:
                order_num = f"ORD-{target_uuid[:8]}"
            order_obj = InstallationOrder.objects.create(order_number=order_num, **defaults)
            created = True

        # Associate with pairs if applicable
        pairs = VehicleInstallationPair.objects.filter(
            models.Q(order=order_obj) | models.Q(registration_number_detected=canonical_reg)
        )
        for pair in pairs:
            if not pair.order_id:
                pair.order = order_obj
                pair.save(update_fields=["order"])
            SubmissionAuditLog.objects.create(
                pair=pair,
                action=SubmissionAuditLog.Action.VALIDATE,
                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                message=(
                    f"ITMS Step 1 verified: {order_num} "
                    f"(Front Plate: {front_p.get('selected_text')}, Rear Plate: {rear_p.get('selected_text')}, "
                    f"Tracker: {tracker.get('selected_text')})"
                ),
            )

        return {
            "success": True,
            "created": created,
            "order_number": order_num,
            "order_id": order_obj.id,
            "pairs_updated": pairs.count(),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Step 2: Photo Upload (/installation-orders/approve?id=<uuid>) & Step 3 Redirection
    # ──────────────────────────────────────────────────────────────────────────

    def parse_approve_page_html(self, html: str) -> Dict[str, Any]:
        """
        Parses Step 2 photo approval / evidence upload page:
          GET https://stock.itms.ug/installation-orders/approve?id=<uuid>
        
        Extracts:
          - CSRF token (_csrf-frontend / meta tag)
          - Form action and order UUID
          - Breadcrumb order number (e.g. PO-UMA560PJ-100926)
          - Vehicle type (e.g. 'M' for Motorcycle)
          - Alert message from Step 1 (e.g. 'Installation order created successfully')
          - Checklist requirement status (False for 'M' / first installation)
        """
        csrf_token = self._extract_csrf_from_html(html)

        form_m = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html)
        form_action = form_m.group(1) if form_m else ""
        uuid_m = re.search(r'id=([0-9a-fA-F-]+)', form_action)
        order_uuid = uuid_m.group(1) if uuid_m else ""

        order_num_m = re.search(r'<li[^>]*class=["\']breadcrumb-item active["\'][^>]*>(PO-[A-Za-z0-9\-]+)</li>', html)
        order_number = order_num_m.group(1) if order_num_m else ""

        vtype_m = re.search(r'name=["\']vehicle_type["\']\s+value=["\']([^"\']+)["\']', html)
        vehicle_type = vtype_m.group(1) if vtype_m else "M"

        alert_m = re.search(r'class=["\'][^"\']*alert-success[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL)
        alert_msg = re.sub(r'<[^>]+>', '', alert_m.group(1)).replace("&ensp;", " ").strip() if alert_m else ""

        # In Yii2 activeForm for this view, checklist is optional when vehicle_type == 'M'
        requires_checklist = (vehicle_type != "M")

        return {
            "order_uuid": order_uuid,
            "order_number": order_number,
            "csrf_token": csrf_token,
            "vehicle_type": vehicle_type,
            "form_action": form_action,
            "alert_message": alert_msg,
            "requires_checklist": requires_checklist,
        }

    def fetch_approve_step2(self, order_identifier: str) -> Dict[str, Any]:
        """
        Fetches Step 2 photo approval page:
          GET https://stock.itms.ug/installation-orders/approve?id=<uuid>
        """
        from django.db import models

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        target_uuid = ""
        ident = (order_identifier or "").strip()
        if not ident:
            return {"success": False, "error": "Order identifier cannot be empty."}

        # Check UUID
        uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        uuid_match = re.search(uuid_pattern, ident)
        if uuid_match:
            target_uuid = uuid_match.group(1)

        # Check local DB
        if not target_uuid:
            try:
                from core.models import InstallationOrder
                from core.vision import normalizer
                canonical = normalizer.canonicalize(ident) or ident.replace(" ", "").upper()
                order_rec = InstallationOrder.objects.filter(
                    models.Q(order_number__iexact=ident) |
                    models.Q(registration_number__iexact=canonical) |
                    models.Q(vin__iexact=ident)
                ).exclude(itms_order_uuid="").first()
                if order_rec and order_rec.itms_order_uuid:
                    target_uuid = order_rec.itms_order_uuid
            except Exception:
                pass

        # Check live ITMS active orders table
        if not target_uuid:
            active_res = self.fetch_installation_orders(page=1, search_params=ident, archive=False)
            active_orders = active_res.get("orders", [])
            for o in active_orders:
                if o.get("order_key"):
                    target_uuid = o["order_key"]
                    break

        if not target_uuid:
            return {
                "success": False,
                "error": f"Could not resolve ITMS UUID for '{ident}'.",
            }

        url = f"{self.base_url}/installation-orders/approve?id={target_uuid}"
        headers = {"Referer": f"{self.base_url}/installation-orders/installation?id={target_uuid}"}

        try:
            resp = self._request_with_retry("GET", url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Approve page returned HTTP {resp.status_code}",
                    "url": url,
                    "order_uuid": target_uuid,
                }

            parsed = self.parse_approve_page_html(resp.text)
            parsed["success"] = True
            parsed["order_uuid"] = target_uuid
            parsed["url"] = url
            return parsed
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "error": f"Connection error fetching approve page: {exc}",
                "url": url,
                "order_uuid": target_uuid,
            }

    @staticmethod
    def prepare_multipart_image_bytes(
        file_path: Any,
        max_dimension: Optional[int] = None,
        jpeg_quality: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        return prepare_multipart_image_bytes(file_path, max_dimension, jpeg_quality, enabled)

    def upload_installation_step2_photos(
        self,
        order_uuid: str,
        front_photo_path: Any,
        rear_photo_path: Any,
        checklist_path: Optional[Any] = None,
        csrf_token: Optional[str] = None,
        vehicle_type: str = "M",
        dry_run: Optional[bool] = None,
        log_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Uploads front and rear motorcycle installation photos (and optional checklist)
        to ITMS Step 2:
          POST /installation-orders/approve?id=<uuid> (multipart/form-data)
        
        SMART ON-THE-FLY COMPRESSION:
          Downsamples photos on-the-fly ONLY when preparing the HTTP multipart stream
          (max 1920px, JPEG quality 88%). Master photos in vault are 100% untouched.
        
        CRITICAL SAFETY RULE:
          dry_run defaults to None (resolving to config setting submission.dry_run_mode).
          In dry_run mode, no mutating POST request is transmitted to the live ITMS server.
          Live submission occurs ONLY when dry_run=False.

        Upon success, the server responds with HTTP 302 Found and Location redirecting to:
          https://stock.itms.ug/installation-orders/confirmation?id=<uuid> (Step 3: Confirmation)
        """
        dry_run = resolve_dry_run(dry_run)
        import mimetypes
        from django.conf import settings

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        # Resolve photo paths on disk
        def resolve_file_path(p: Any) -> Optional[Path]:
            if not p:
                return None
            path_obj = Path(p)
            if not path_obj.is_absolute() and settings.configured:
                cand = Path(settings.MEDIA_ROOT) / path_obj
                if cand.is_file():
                    return cand
            if path_obj.is_file():
                return path_obj
            return None

        front_file = resolve_file_path(front_photo_path)
        rear_file = resolve_file_path(rear_photo_path)
        checklist_file = resolve_file_path(checklist_path) if checklist_path else None

        if not front_file:
            return {
                "success": False,
                "error": f"Front photo file not found on disk: {front_photo_path}",
                "order_uuid": order_uuid,
            }
        if not rear_file:
            return {
                "success": False,
                "error": f"Rear photo file not found on disk: {rear_photo_path}",
                "order_uuid": order_uuid,
            }

        # Prepare Smart On-The-Fly Multipart Compression (Vault master images untouched!)
        front_bytes, front_stats = prepare_multipart_image_bytes(front_file)
        rear_bytes, rear_stats = prepare_multipart_image_bytes(rear_file)

        if log_callback:
            try:
                log_callback(f"📸 [bold cyan][COMPRESS][/bold cyan] Front: {front_file.name} ({front_stats.get('display_str')})")
                log_callback(f"📸 [bold cyan][COMPRESS][/bold cyan] Rear:  {rear_file.name} ({rear_stats.get('display_str')})")
            except Exception:
                pass

        # Dry Run Protection Guard
        if dry_run:
            simulated_redirect = f"{self.base_url}/installation-orders/confirmation?id={order_uuid}"
            try:
                from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                if order_obj:
                    pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                    for p in pairs:
                        SubmissionAuditLog.objects.create(
                            pair=p,
                            action=SubmissionAuditLog.Action.VALIDATE,
                            result=SubmissionAuditLog.ResultStatus.SUCCESS,
                            message=f"[DRY RUN] Step 2 simulated photo upload: Front={front_file.name} ({front_stats.get('display_str')}), Rear={rear_file.name} ({rear_stats.get('display_str')}). Live multipart upload skipped.",
                        )
            except Exception as audit_exc:
                logger.warning("Audit log error on Step 2 dry run: %s", audit_exc)

            return {
                "success": True,
                "dry_run": True,
                "step": 2,
                "status_code": 302,
                "redirect_url": simulated_redirect,
                "relative_redirect": f"/installation-orders/confirmation?id={order_uuid}",
                "step3_ready": True,
                "order_uuid": order_uuid,
                "front_photo": str(front_file),
                "rear_photo": str(rear_file),
                "checklist": str(checklist_file) if checklist_file else None,
                "front_stats": front_stats,
                "rear_stats": rear_stats,
                "message": f"[DRY RUN] Step 2 photos verified with on-the-fly compression (Front: {front_stats.get('display_str')}, Rear: {rear_stats.get('display_str')}). Simulated advance to Step 3: {simulated_redirect}",
            }

        front_mime = mimetypes.guess_type(front_file.name)[0] or "image/jpeg"
        rear_mime = mimetypes.guess_type(rear_file.name)[0] or "image/jpeg"

        target_csrf = csrf_token or session_data.csrf_token
        url = f"{self.base_url}/installation-orders/approve?id={order_uuid}"

        headers = {
            "Origin": self.base_url,
            "Referer": url,
            "Cache-Control": "max-age=0",
        }

        try:
            files = [
                ("InstallationOrderApproveForm[front_plate]", (front_file.name, front_bytes, front_mime)),
                ("InstallationOrderApproveForm[rear_plate]", (rear_file.name, rear_bytes, rear_mime)),
            ]

            if checklist_file and checklist_file.is_file():
                check_mime = mimetypes.guess_type(checklist_file.name)[0] or "application/pdf"
                with open(checklist_file, "rb") as cf:
                    checklist_bytes = cf.read()
                files.append(("InstallationOrderApproveForm[checklist]", (checklist_file.name, checklist_bytes, check_mime)))
            else:
                # Send empty file field matching browser multipart format
                files.append(("InstallationOrderApproveForm[checklist]", ("", b"", "application/octet-stream")))

            data = {
                "vehicle_type": vehicle_type,
            }
            if target_csrf:
                data["_csrf-frontend"] = target_csrf

            resp = self._request_with_retry("POST", url, data=data, files=files, headers=headers, timeout=self.timeout, allow_redirects=False)

            if resp.status_code == 302:
                redirect_url = resp.headers.get("Location", "")
                full_redirect_url = urllib.parse.urljoin(self.base_url, redirect_url)
                is_step3 = "/installation-orders/confirmation" in redirect_url

                # Audit log in database
                try:
                    from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                    order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                    if order_obj:
                        order_obj.status = InstallationOrder.Status.SUBMITTED
                        order_obj.save(update_fields=["status"])
                        pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                        for p in pairs:
                            SubmissionAuditLog.objects.create(
                                pair=p,
                                action=SubmissionAuditLog.Action.UPLOAD_FRONT,
                                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                message=f"Uploaded front photo: {front_file.name} ({front_stats.get('display_str')})",
                            )
                            SubmissionAuditLog.objects.create(
                                pair=p,
                                action=SubmissionAuditLog.Action.UPLOAD_REAR,
                                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                message=f"Uploaded rear photo: {rear_file.name} ({rear_stats.get('display_str')})",
                            )
                            SubmissionAuditLog.objects.create(
                                pair=p,
                                action=SubmissionAuditLog.Action.VALIDATE,
                                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                message=f"Step 2 photos uploaded successfully: Redirected to {redirect_url}",
                            )
                except Exception as audit_exc:
                    logger.warning("Audit log error on Step 2 upload: %s", audit_exc)

                return {
                    "success": True,
                    "step": 2,
                    "status_code": 302,
                    "redirect_url": full_redirect_url,
                    "relative_redirect": redirect_url,
                    "step3_ready": is_step3,
                    "order_uuid": order_uuid,
                    "front_photo": str(front_file),
                    "rear_photo": str(rear_file),
                    "front_stats": front_stats,
                    "rear_stats": rear_stats,
                    "message": f"Step 2 photos uploaded successfully ({front_stats.get('display_str')}, {rear_stats.get('display_str')}). Advanced to Step 3: {full_redirect_url}",
                }
            elif resp.status_code == 200:
                err_m = re.findall(r'<div[^>]*class=["\'][^"\']*invalid-feedback[^"\']*["\'][^>]*>(.*?)</div>', resp.text, re.DOTALL)
                clean_errs = [re.sub(r'<[^>]+>', '', e).strip() for e in err_m if re.sub(r'<[^>]+>', '', e).strip()]
                return {
                    "success": False,
                    "step": 2,
                    "status_code": 200,
                    "error": "Step 2 photo upload re-rendered page without redirecting. Validation errors encountered.",
                    "form_errors": clean_errs,
                    "order_uuid": order_uuid,
                }
            else:
                return {
                    "success": False,
                    "step": 2,
                    "status_code": resp.status_code,
                    "error": f"Photo upload returned unexpected HTTP {resp.status_code}",
                    "order_uuid": order_uuid,
                }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "step": 2,
                "error": f"Connection error uploading Step 2 photos: {exc}",
                "order_uuid": order_uuid,
            }

    # ──────────────────────────────────────────────────────────────────────────
    # Step 3: Confirmation / Summary (/installation-orders/confirmation?id=<uuid>) & Completion
    # ──────────────────────────────────────────────────────────────────────────

    def parse_confirmation_page_html(self, html: str) -> Dict[str, Any]:
        """
        Parses Step 3 order confirmation / final summary page:
          GET https://stock.itms.ug/installation-orders/confirmation?id=<uuid>
        
        Extracts:
          - CSRF token (_csrf-frontend / meta tag)
          - Form action and order UUID
          - Order number from breadcrumb
          - Service type, VIN, Old plate, Registration number
          - Front plate serial, Rear plate serial, GPS Tracker, Front Beacon, Rear Beacon
          - Evidence photo cards (Front Plate, Rear Plate, Checklist)
        """
        csrf_token = self._extract_csrf_from_html(html)

        form_m = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html)
        form_action = form_m.group(1) if form_m else ""
        uuid_m = re.search(r'id=([0-9a-fA-F-]+)', form_action)
        order_uuid = uuid_m.group(1) if uuid_m else ""

        order_num_m = re.search(r'<li[^>]*class=["\']breadcrumb-item active["\'][^>]*>(PO-[A-Za-z0-9\-]+)</li>', html)
        order_number = order_num_m.group(1) if order_num_m else ""

        def get_field_val(label: str) -> str:
            m = re.search(
                rf'<div[^>]*class=["\']fw-semibold["\'][^>]*>{re.escape(label)}:?</div>\s*<div[^>]*class=["\']text-muted small["\'][^>]*>(.*?)</div>',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if m:
                val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                return "" if val in ("—", "-", "") else val
            return ""

        service_type = get_field_val("Service Type")
        vin = get_field_val("Vehicle VIN/Chassis No.")
        old_plate = get_field_val("Old plate number")
        registration_number = get_field_val("Registration Number")
        front_plate = get_field_val("Front Plate")
        rear_plate = get_field_val("Rear Plate")
        tracker = get_field_val("GPS Tracker")
        front_beacon = get_field_val("Front Beacon")
        rear_beacon = get_field_val("Rear Beacon")

        # Photo cards extraction
        photo_cards = []
        for p_block in re.finditer(
            r'<p[^>]*class=["\'][^"\']*fw-semibold[^"\']*["\'][^>]*>\s*(Front Plate|Rear Plate|Installation checklist)(.*?)(?=<p[^>]*class=["\'][^"\']*fw-semibold[^"\']*["\']|<div class="d-flex justify-content-between align-items-center mt-4"|</form>)',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            label = p_block.group(1).strip()
            content = p_block.group(2)
            img_m = re.search(r'<img[^>]*src=["\']([^"\']+)["\']', content)
            file_m = re.search(r'<span[^>]*class=["\']text-dark small["\'][^>]*>(.*?)</span>', content)
            filename = file_m.group(1).strip() if file_m else ""
            img_src = img_m.group(1).strip() if img_m else ""
            if filename == "No file":
                filename = ""
            photo_cards.append({
                "label": label,
                "filename": filename,
                "img_src": img_src,
            })

        return {
            "order_uuid": order_uuid,
            "order_number": order_number,
            "csrf_token": csrf_token,
            "form_action": form_action,
            "service_type": service_type,
            "vin": vin,
            "old_plate": old_plate,
            "registration_number": registration_number,
            "front_plate": front_plate,
            "rear_plate": rear_plate,
            "tracker": tracker,
            "front_beacon": front_beacon,
            "rear_beacon": rear_beacon,
            "photo_cards": photo_cards,
        }

    def fetch_confirmation_step3(
        self,
        order_identifier: str,
        download_photos: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetches Step 3 order confirmation / final summary page (read-only GET):
          GET https://stock.itms.ug/installation-orders/confirmation?id=<uuid>
        
        Robust Fallback Principle:
          If GET /confirmation returns HTTP 302 redirect (as Yii2 does when accessed directly
          outside an active wizard session or while status is 'Under installation'), or if the page
          re-renders without confirmation fields, this method gracefully falls back to `fetch_order_info`
          to synthesize the complete Step 3 summary, hardware inventory, and uploaded photo cards.
        """
        from django.db import models
        from django.conf import settings

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        target_uuid = ""
        ident = (order_identifier or "").strip()
        if not ident:
            return {"success": False, "error": "Order identifier cannot be empty."}

        # Check UUID
        uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        uuid_match = re.search(uuid_pattern, ident)
        if uuid_match:
            target_uuid = uuid_match.group(1)

        # Check local DB
        if not target_uuid:
            try:
                from core.models import InstallationOrder
                from core.vision import normalizer
                canonical = normalizer.canonicalize(ident) or ident.replace(" ", "").upper()
                order_rec = InstallationOrder.objects.filter(
                    models.Q(order_number__iexact=ident) |
                    models.Q(registration_number__iexact=canonical) |
                    models.Q(vin__iexact=ident)
                ).exclude(itms_order_uuid="").first()
                if order_rec and order_rec.itms_order_uuid:
                    target_uuid = order_rec.itms_order_uuid
            except Exception:
                pass

        # Check live ITMS active orders table
        if not target_uuid:
            active_res = self.fetch_installation_orders(page=1, search_params=ident, archive=False)
            active_orders = active_res.get("orders", [])
            for o in active_orders:
                if o.get("order_key"):
                    target_uuid = o["order_key"]
                    break

        if not target_uuid:
            return {
                "success": False,
                "error": f"Could not resolve ITMS UUID for '{ident}'.",
            }

        url = f"{self.base_url}/installation-orders/confirmation?id={target_uuid}"
        headers = {"Referer": f"{self.base_url}/installation-orders/approve?id={target_uuid}"}

        needs_fallback = False
        parsed = {}

        try:
            resp = self._request_with_retry("GET", url, headers=headers, timeout=self.timeout, allow_redirects=False)
            if resp.status_code == 302:
                needs_fallback = True
            elif resp.status_code == 200:
                if "/installation-orders/index" in resp.url or "data-url=" in resp.text:
                    needs_fallback = True
                else:
                    parsed = self.parse_confirmation_page_html(resp.text)
                    if not parsed.get("order_number") and not parsed.get("photo_cards"):
                        needs_fallback = True
            else:
                needs_fallback = True
        except requests.exceptions.RequestException:
            needs_fallback = True

        if needs_fallback:
            # Synthesize Step 3 confirmation from existing uploaded photos via fetch_order_info
            order_info = self.fetch_order_info(target_uuid, download_photos=download_photos)
            if not order_info.get("success"):
                return {
                    "success": False,
                    "error": f"Could not fetch confirmation page or order info for '{ident}': {order_info.get('error')}",
                    "url": url,
                    "order_uuid": target_uuid,
                }

            photo_cards = []
            for p in order_info.get("photos", []):
                orient = p.get("orientation", "")
                lbl = "Front Plate" if orient == "FRONT" else ("Rear Plate" if orient == "REAR" else p.get("label", "Plate photo"))
                photo_cards.append({
                    "label": lbl,
                    "filename": p.get("filename", ""),
                    "img_src": p.get("relative_url") or p.get("url", ""),
                    "local_path": p.get("local_path", ""),
                    "orientation": orient,
                })

            if not any("checklist" in str(pc.get("label", "")).lower() for pc in photo_cards):
                photo_cards.append({
                    "label": "Installation checklist",
                    "filename": "",
                    "img_src": "",
                    "local_path": "",
                    "orientation": "CHECKLIST",
                })

            parsed = {
                "order_uuid": target_uuid,
                "order_number": order_info.get("order_number", ""),
                "csrf_token": session_data.csrf_token,
                "form_action": f"/installation-orders/confirmation?id={target_uuid}",
                "service_type": order_info.get("raw_details", {}).get("Service Type") or "First Time Registration",
                "vin": order_info.get("vin", ""),
                "old_plate": order_info.get("raw_details", {}).get("Old plate number", ""),
                "registration_number": order_info.get("registration_number", ""),
                "front_plate": order_info.get("front_plate", {}).get("serial", ""),
                "rear_plate": order_info.get("rear_plate", {}).get("serial", ""),
                "tracker": order_info.get("gps_tracker", {}).get("device_id", ""),
                "front_beacon": order_info.get("front_beacon", {}).get("device_id", ""),
                "rear_beacon": order_info.get("rear_beacon", {}).get("device_id", ""),
                "photo_cards": photo_cards,
                "synthesized_from_info": True,
            }

        # If download_photos requested and not yet resolved:
        if download_photos and not parsed.get("synthesized_from_info"):
            vault_root = getattr(settings, "VAULT_ROOT", "media/vault") if settings.configured else "media/vault"
            order_num = parsed.get("order_number") or target_uuid
            order_dir = Path(vault_root) / "itms_photos" / order_num
            for pc in parsed.get("photo_cards", []):
                src = pc.get("img_src", "")
                if src and not pc.get("local_path"):
                    dl = self.download_photo(src, save_dir=order_dir, order_number=order_num)
                    if dl.get("success"):
                        pc["local_path"] = dl["path"]

        parsed["success"] = True
        parsed["order_uuid"] = target_uuid or parsed.get("order_uuid", "")
        parsed["url"] = url
        return parsed

    def submit_confirmation_step3(
        self,
        order_uuid: str,
        front_photo_path: Optional[Any] = None,
        rear_photo_path: Optional[Any] = None,
        checklist_path: Optional[Any] = None,
        csrf_token: Optional[str] = None,
        dry_run: Optional[bool] = None,
        log_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Submits Step 3 confirmation to finalize and complete the installation order:
          POST /installation-orders/confirmation?id=<uuid> (multipart/form-data)
        
        Photo Replacement Architecture:
          Operators may optionally attach replacement photos if existing uploaded photos
          were blurred, mistaken, or misaligned:
            - If front_photo_path provided: attaches file & sets front_plate_touched="1"
            - If front_photo_path omitted: sets front_plate_touched="0" & empty file attachment
            - Same behavior for rear_photo_path and checklist_path
        
        CRITICAL SAFETY RULE:
          dry_run defaults to None (resolving to config setting submission.dry_run_mode).
          In dry_run mode, no POST network request is transmitted to the live ITMS server.
          Live submission occurs ONLY when dry_run=False.
        
        Upon live success, the server responds with HTTP 302 Found redirecting to:
          https://stock.itms.ug/installation-orders/index
        """
        dry_run = resolve_dry_run(dry_run)
        import mimetypes
        from django.conf import settings

        session_data = self.session_store.session
        if not session_data.is_cookie_valid():
            return {
                "success": False,
                "error": "Session cookies invalid or missing. Please sign in first.",
            }

        # Helper to resolve replacement photo paths on disk
        def resolve_file_path(p: Any) -> Optional[Path]:
            if not p:
                return None
            path_obj = Path(p)
            if not path_obj.is_absolute() and settings.configured:
                cand = Path(settings.MEDIA_ROOT) / path_obj
                if cand.is_file():
                    return cand
            if path_obj.is_file():
                return path_obj
            return None

        front_file = resolve_file_path(front_photo_path)
        rear_file = resolve_file_path(rear_photo_path)
        checklist_file = resolve_file_path(checklist_path)

        has_replacements = bool(front_file or rear_file or checklist_file)
        replaced_info = []
        if front_file:
            replaced_info.append(f"Front: {front_file.name}")
        if rear_file:
            replaced_info.append(f"Rear: {rear_file.name}")
        if checklist_file:
            replaced_info.append(f"Checklist: {checklist_file.name}")

        target_csrf = csrf_token or session_data.csrf_token
        url = f"{self.base_url}/installation-orders/confirmation?id={order_uuid}"
        redirect_target = f"{self.base_url}/installation-orders/index"

        if dry_run:
            replace_desc = f" with replacement photos ({', '.join(replaced_info)})" if replaced_info else ""
            try:
                from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                if order_obj:
                    pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                    for p in pairs:
                        SubmissionAuditLog.objects.create(
                            pair=p,
                            action=SubmissionAuditLog.Action.SUBMIT,
                            result=SubmissionAuditLog.ResultStatus.SUCCESS,
                            message=f"[DRY RUN] Step 3 confirmation simulated: Order #{order_uuid} ready for completion{replace_desc}. Live POST skipped.",
                        )
            except Exception as exc:
                logger.warning("Audit log error on Step 3 dry run: %s", exc)

            return {
                "success": True,
                "dry_run": True,
                "step": 3,
                "status_code": 302,
                "order_uuid": order_uuid,
                "redirect_url": redirect_target,
                "relative_redirect": "/installation-orders/index",
                "is_finalized": True,
                "has_replacements": has_replacements,
                "replacements": replaced_info,
                "message": f"[DRY RUN] Step 3 confirmation simulated for #{order_uuid}{replace_desc}. Order marked ready for completion. (No changes written to stock.itms.ug).",
            }

        s = self._create_requests_session()
        headers = {
            "Origin": self.base_url,
            "Referer": url,
            "Cache-Control": "max-age=0",
        }

        front_bytes, front_stats = (b"", {})
        rear_bytes, rear_stats = (b"", {})
        check_bytes, check_stats = (b"", {})
        data: Dict[str, str] = {}
        files = []

        if front_file:
            front_bytes, front_stats = prepare_multipart_image_bytes(front_file)
            front_mime = mimetypes.guess_type(front_file.name)[0] or "image/jpeg"
            files.append(("front_plate", (front_file.name, front_bytes, front_mime)))
            data["front_plate_touched"] = "1"
            if log_callback:
                try:
                    log_callback(f"📸 [bold cyan][COMPRESS][/bold cyan] Step 3 Front: {front_file.name} ({front_stats.get('display_str')})")
                except Exception:
                    pass
        else:
            files.append(("front_plate", ("", b"", "application/octet-stream")))
            data["front_plate_touched"] = "0"

        if rear_file:
            rear_bytes, rear_stats = prepare_multipart_image_bytes(rear_file)
            rear_mime = mimetypes.guess_type(rear_file.name)[0] or "image/jpeg"
            files.append(("rear_plate", (rear_file.name, rear_bytes, rear_mime)))
            data["rear_plate_touched"] = "1"
            if log_callback:
                try:
                    log_callback(f"📸 [bold cyan][COMPRESS][/bold cyan] Step 3 Rear: {rear_file.name} ({rear_stats.get('display_str')})")
                except Exception:
                    pass
        else:
            files.append(("rear_plate", ("", b"", "application/octet-stream")))
            data["rear_plate_touched"] = "0"

        if checklist_file:
            check_bytes, check_stats = prepare_multipart_image_bytes(checklist_file)
            check_mime = mimetypes.guess_type(checklist_file.name)[0] or "image/jpeg"
            files.append(("checklist_photo", (checklist_file.name, check_bytes, check_mime)))
            data["checklist_photo_touched"] = "1"
        else:
            files.append(("checklist_photo", ("", b"", "application/octet-stream")))
            data["checklist_photo_touched"] = "0"

        if target_csrf:
            data["_csrf-frontend"] = target_csrf

        try:
            resp = self._request_with_retry("POST", url, data=data, files=files, headers=headers, timeout=self.timeout, allow_redirects=False)

            if resp.status_code == 302:
                redirect_url = resp.headers.get("Location", "")
                full_redirect_url = urllib.parse.urljoin(self.base_url, redirect_url)
                is_index = "/installation-orders/index" in redirect_url

                try:
                    from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
                    from django.utils import timezone
                    order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                    if order_obj:
                        order_obj.status = InstallationOrder.Status.SUBMITTED
                        order_obj.order_status = "Installed"
                        order_obj.save(update_fields=["status", "order_status"])
                        pairs = VehicleInstallationPair.objects.filter(order=order_obj)
                        now = timezone.now()
                        for p in pairs:
                            p.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
                            p.submitted_at = now
                            p.save(update_fields=["verification_status", "submitted_at"])
                            for img in (p.front_image, p.rear_image):
                                if img and img.status != EvidenceImage.Status.SUBMITTED:
                                    img.status = EvidenceImage.Status.SUBMITTED
                                    img.submitted_at = now
                                    img.save(update_fields=["status", "submitted_at"])
                            SubmissionAuditLog.objects.create(
                                pair=p,
                                action=SubmissionAuditLog.Action.SUBMIT,
                                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                message=f"Step 3 confirmed and finalized: Redirected to {redirect_url}",
                            )
                except Exception as audit_exc:
                    logger.warning("Audit log error on Step 3 submit: %s", audit_exc)

                return {
                    "success": True,
                    "dry_run": False,
                    "step": 3,
                    "status_code": 302,
                    "redirect_url": full_redirect_url,
                    "relative_redirect": redirect_url,
                    "is_finalized": is_index,
                    "order_uuid": order_uuid,
                    "has_replacements": has_replacements,
                    "replacements": replaced_info,
                    "message": f"Step 3 confirmed successfully. Order finalized: {full_redirect_url}",
                }
            elif resp.status_code == 200:
                err_m = re.findall(r'<div[^>]*class=["\'][^"\']*invalid-feedback[^"\']*["\'][^>]*>(.*?)</div>', resp.text, re.DOTALL)
                clean_errs = [re.sub(r'<[^>]+>', '', e).strip() for e in err_m if re.sub(r'<[^>]+>', '', e).strip()]
                return {
                    "success": False,
                    "dry_run": False,
                    "step": 3,
                    "status_code": 200,
                    "error": "Step 3 confirmation re-rendered page without redirecting.",
                    "form_errors": clean_errs,
                    "order_uuid": order_uuid,
                }
            else:
                return {
                    "success": False,
                    "dry_run": False,
                    "step": 3,
                    "status_code": resp.status_code,
                    "error": f"Step 3 returned unexpected HTTP {resp.status_code}",
                    "order_uuid": order_uuid,
                }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "dry_run": False,
                "step": 3,
                "error": f"Connection error submitting Step 3: {exc}",
                "order_uuid": order_uuid,
            }

    def sync_confirmation_step3_to_local_db(
        self,
        step3_data: Dict[str, Any],
        order_uuid: str = "",
    ) -> Dict[str, Any]:
        """
        Synchronizes Step 3 confirmation data into local InstallationOrder record.
        """
        from django.db import models
        from django.utils import timezone
        from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
        from core.vision import normalizer

        order_num = step3_data.get("order_number", "").strip()
        reg_num = step3_data.get("registration_number", "").strip()
        target_uuid = order_uuid or step3_data.get("order_uuid", "")

        if not order_num and not target_uuid:
            return {"success": False, "error": "Order identifier missing"}

        canonical_reg = normalizer.canonicalize(reg_num) or reg_num.replace(" ", "").upper()

        defaults = {
            "registration_number": canonical_reg,
            "vin": step3_data.get("vin", ""),
            "front_plate_serial": step3_data.get("front_plate", ""),
            "rear_plate_serial": step3_data.get("rear_plate", ""),
            "gps_tracker_id": step3_data.get("tracker", ""),
            "front_beacon_id": step3_data.get("front_beacon", ""),
            "rear_beacon_id": step3_data.get("rear_beacon", ""),
            "plate_serial": step3_data.get("front_plate", "") or step3_data.get("rear_plate", ""),
            "tracker_id": step3_data.get("tracker", ""),
            "itms_order_uuid": target_uuid,
            "itms_action_url": f"/installation-orders/confirmation?id={target_uuid}",
            "order_status": "Ready for approve",
            "details_json": step3_data,
            "info_fetched_at": timezone.now(),
        }

        order_obj = None
        if order_num:
            order_obj = InstallationOrder.objects.filter(order_number=order_num).first()
        if not order_obj and target_uuid:
            order_obj = InstallationOrder.objects.filter(itms_order_uuid=target_uuid).first()

        created = False
        if order_obj:
            for k, v in defaults.items():
                setattr(order_obj, k, v)
            order_obj.save()
        else:
            if not order_num:
                order_num = f"ORD-{target_uuid[:8]}"
            order_obj = InstallationOrder.objects.create(order_number=order_num, **defaults)
            created = True

        # Link pairs
        pairs = VehicleInstallationPair.objects.filter(
            models.Q(order=order_obj) | models.Q(registration_number_detected=canonical_reg)
        )
        for pair in pairs:
            if not pair.order_id:
                pair.order = order_obj
                pair.save(update_fields=["order"])
            SubmissionAuditLog.objects.create(
                pair=pair,
                action=SubmissionAuditLog.Action.VALIDATE,
                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                message=(
                    f"ITMS Step 3 confirmed: {order_num} "
                    f"(Front Plate: {step3_data.get('front_plate')}, Rear Plate: {step3_data.get('rear_plate')}, "
                    f"Tracker: {step3_data.get('tracker')}, Photos: {len(step3_data.get('photo_cards', []))})"
                ),
            )

        return {
            "success": True,
            "created": created,
            "order_number": order_num,
            "order_id": order_obj.id,
            "pairs_updated": pairs.count(),
        }

    def detect_order_stage(self, order_identifier: str) -> Dict[str, Any]:
        """
        Intelligently classifies the current workflow lifecycle stage of an ITMS order:
          - STAGE_ARCHIVED (Step 4 / Completed / Installed): present in archive
          - STAGE_3_CONFIRMATION (Step 3 / Confirmation): photos already uploaded in Step 2, pending final submit
          - STAGE_2_APPROVE (Step 2 / Photos): hardware fitment assigned, pending photo upload
          - STAGE_1_INSTALLATION (Step 1 / Installation): initial hardware selection & activeForm validation
        """
        from django.db import models

        ident = (order_identifier or "").strip()
        if not ident:
            return {"success": False, "error": "Order identifier cannot be empty."}

        # 1. First check if archived (Installed)
        arch_res = self.fetch_archive_orders(page=1, search_params=ident)
        for o in arch_res.get("orders", []):
            order_uuid = o.get("order_key", "")
            return {
                "success": True,
                "stage": "STAGE_ARCHIVED",
                "step": 4,
                "stage_name": "Completed / Archived (Installed)",
                "order_uuid": order_uuid,
                "order_number": o.get("order_number", ""),
                "registration_number": o.get("registration_number", ""),
                "vin": o.get("vin", ""),
                "order_status": o.get("order_status", ""),
                "is_archived": True,
                "photos_count": 0,
                "action_url": o.get("action_url", ""),
                "message": f"Order #{o.get('order_number')} is fully completed and archived (Status: {o.get('order_status')}).",
            }

        # 2. Check active orders table
        active_res = self.fetch_installation_orders(page=1, search_params=ident, archive=False)
        active_order = None
        for o in active_res.get("orders", []):
            active_order = o
            break

        target_uuid = active_order.get("order_key", "") if active_order else ""
        if not target_uuid:
            # Check UUID directly in ident or local DB
            uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
            m = re.search(uuid_pattern, ident)
            if m:
                target_uuid = m.group(1)
            else:
                try:
                    from core.models import InstallationOrder
                    from core.vision import normalizer
                    canonical = normalizer.canonicalize(ident) or ident.replace(" ", "").upper()
                    rec = InstallationOrder.objects.filter(
                        models.Q(order_number__iexact=ident) |
                        models.Q(registration_number__iexact=canonical) |
                        models.Q(vin__iexact=ident)
                    ).exclude(itms_order_uuid="").first()
                    if rec and rec.itms_order_uuid:
                        target_uuid = rec.itms_order_uuid
                except Exception:
                    pass

        if not target_uuid:
            return {
                "success": False,
                "error": f"Could not find or resolve order '{ident}' on ITMS.",
            }

        # 3. Query detailed order info to check existing photos and hardware
        info = self.fetch_order_info(target_uuid, download_photos=False)
        photos = info.get("photos", [])
        order_num = info.get("order_number") or (active_order.get("order_number") if active_order else "")
        reg_num = info.get("registration_number") or (active_order.get("registration_number") if active_order else "")
        vin_str = info.get("vin") or (active_order.get("vin") if active_order else "")
        action_url = active_order.get("action_url") if active_order else f"/installation-orders/info?id={target_uuid}"

        # If photos already exist on ITMS (e.g. UMA 835DS has 2 photos), order is in Step 3!
        if len(photos) >= 2:
            return {
                "success": True,
                "stage": "STAGE_3_CONFIRMATION",
                "step": 3,
                "stage_name": "Step 3: Confirmation / Summary (Photos Uploaded, Pending Final Submission)",
                "order_uuid": target_uuid,
                "order_number": order_num,
                "registration_number": reg_num,
                "vin": vin_str,
                "photos_count": len(photos),
                "photos": photos,
                "action_url": f"/installation-orders/confirmation?id={target_uuid}",
                "order_status": active_order.get("order_status") if active_order else "Under installation",
                "is_archived": False,
                "message": f"Order #{order_num} has {len(photos)} evidence photos uploaded. It is ready for Step 3 confirmation/summary review.",
            }

        # If action_url points to /approve or front_plate serial is assigned, order is in Step 2
        front_p = info.get("front_plate", {})
        has_hw = bool(front_p.get("serial") or front_p.get("plate"))
        if "/approve" in action_url or has_hw:
            return {
                "success": True,
                "stage": "STAGE_2_APPROVE",
                "step": 2,
                "stage_name": "Step 2: Photo Upload (Hardware Assigned, Pending Evidence Photos)",
                "order_uuid": target_uuid,
                "order_number": order_num,
                "registration_number": reg_num,
                "vin": vin_str,
                "photos_count": len(photos),
                "photos": photos,
                "action_url": f"/installation-orders/approve?id={target_uuid}",
                "order_status": active_order.get("order_status") if active_order else "Under installation",
                "is_archived": False,
                "message": f"Order #{order_num} has hardware assigned. Waiting for Step 2 evidence photo upload.",
            }

        # Otherwise Step 1
        return {
            "success": True,
            "stage": "STAGE_1_INSTALLATION",
            "step": 1,
            "stage_name": "Step 1: Installation (Hardware Selection & Serial Verification)",
            "order_uuid": target_uuid,
            "order_number": order_num,
            "registration_number": reg_num,
            "vin": vin_str,
            "photos_count": 0,
            "photos": [],
            "action_url": f"/installation-orders/installation?id={target_uuid}",
            "order_status": active_order.get("order_status") if active_order else "Ready",
            "is_archived": False,
            "message": f"Order #{order_num} requires Step 1 hardware selection.",
        }

    def execute_installation_order_workflow(
        self,
        order_identifier: str,
        front_photo_path: Optional[Any] = None,
        rear_photo_path: Optional[Any] = None,
        checklist_path: Optional[Any] = None,
        pair_id: Optional[int] = None,
        dry_run: Optional[bool] = None,
        submit_step3: Optional[bool] = None,
        log_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        End-to-End Orchestrator:
        Intelligently inspects the order's active stage, then drives it through Step 1 (Hardware fitment),
        Step 2 (Evidence photo upload), and optionally Step 3 (Confirmation & Finalization).
        Resumes seamlessly at Step 3 if photos were already uploaded!

        CRITICAL SAFETY RULE:
          dry_run defaults to None (resolving to config setting submission.dry_run_mode).
          In dry_run mode, no mutating POST request is transmitted to the live ITMS server.
        """
        dry_run = resolve_dry_run(dry_run)
        if submit_step3 is None:
            try:
                from core.services import config_service
                submit_step3 = config_service.get_setting("submission.submit_step3", True)
            except Exception:
                submit_step3 = True
        from core.models import VehicleInstallationPair, InstallationOrder

        pair = None
        if pair_id:
            pair = VehicleInstallationPair.objects.filter(id=pair_id).first()

        # Step 0: Intelligent Stage Detection
        stage_info = self.detect_order_stage(order_identifier)
        if not stage_info.get("success"):
            return {
                "success": False,
                "error": stage_info.get("error", "Could not locate order on ITMS."),
            }

        order_uuid = stage_info["order_uuid"]
        current_stage = stage_info.get("stage")

        if current_stage == "STAGE_ARCHIVED":
            return {
                "success": True,
                "already_archived": True,
                "is_finalized": True,
                "order_uuid": order_uuid,
                "stage": current_stage,
                "message": stage_info.get("message", "Order is already completed and archived."),
            }

        if current_stage == "STAGE_3_CONFIRMATION":
            step3_info = self.fetch_confirmation_step3(order_uuid, download_photos=True)
            # If the confirmation form is genuinely open (not synthesized due to a 302 redirect), submit/view Step 3 directly:
            if not step3_info.get("synthesized_from_info"):
                if submit_step3:
                    step3_res = self.submit_confirmation_step3(
                        order_uuid=order_uuid,
                        front_photo_path=front_photo_path,
                        rear_photo_path=rear_photo_path,
                        checklist_path=checklist_path,
                        csrf_token=step3_info.get("csrf_token"),
                        dry_run=dry_run,
                        log_callback=log_callback,
                    )
                    return {
                        "success": step3_res.get("success", False),
                        "already_in_step3": True,
                        "order_uuid": order_uuid,
                        "step3": step3_res,
                        "is_finalized": step3_res.get("is_finalized", False),
                        "redirect_url": step3_res.get("redirect_url", ""),
                        "message": step3_res.get("message", ""),
                    }
                return {
                    "success": True,
                    "already_in_step3": True,
                    "step3_ready": True,
                    "order_uuid": order_uuid,
                    "confirmation": step3_info,
                    "message": f"Order is already in Step 3 (Confirmation/Summary). Evidence photos are uploaded ({stage_info.get('photos_count')} photos). (Pass --submit-step3 to finalize).",
                }
            # If synthesized_from_info is True, GET /confirmation 302-redirected because Step 2 was not submitted;
            # fall through to Step 2 photo upload to advance and finalize the order.

        step1_res = {}
        step2_res = {}
        step3_res = {}

        if current_stage == "STAGE_1_INSTALLATION":
            step1_info = self.fetch_installation_step1(order_uuid)
            if not step1_info.get("success"):
                return {
                    "success": False,
                    "step": 1,
                    "error": f"Failed fetching Step 1 installation form: {step1_info.get('error')}",
                }
            front_p = step1_info.get("front_plate", {})
            rear_p = step1_info.get("rear_plate", {})
            tracker = step1_info.get("tracker", {})

            # Sync Step 1 to local DB
            self.sync_installation_step1_to_local_db(step1_info, order_uuid=order_uuid)

            # Submit Step 1 ("Save and continue") with ActiveForm validation
            step1_res = self.submit_installation_step1(
                order_uuid=order_uuid,
                front_plate_id=front_p.get("selected_id", ""),
                back_plate_id=rear_p.get("selected_id", ""),
                tracker_id=tracker.get("selected_id", ""),
                csrf_token=step1_info.get("csrf_token"),
                just_save=False,
                run_validation_first=True,
                dry_run=dry_run,
            )
            if not step1_res.get("success"):
                return {
                    "success": False,
                    "step": 1,
                    "error": f"Step 1 failed: {step1_res.get('error')}",
                    "details": step1_res,
                }
        else:
            step1_res = {
                "success": True,
                "already_advanced": True,
                "order_uuid": order_uuid,
                "message": "Order already completed Step 1.",
            }

        # Step 2: Resolve photo files
        actual_front = front_photo_path
        actual_rear = rear_photo_path

        if not actual_front or not actual_rear:
            if not pair and order_uuid:
                order_obj = InstallationOrder.objects.filter(itms_order_uuid=order_uuid).first()
                if order_obj:
                    pair = VehicleInstallationPair.objects.filter(order=order_obj).first()
            if pair:
                if not actual_front and pair.front_image:
                    actual_front = pair.front_image.vault_file
                if not actual_rear and pair.rear_image:
                    actual_rear = pair.rear_image.vault_file

        if not actual_front or not actual_rear:
            return {
                "success": True,
                "step1_complete": True,
                "step2_ready": True,
                "order_uuid": order_uuid,
                "message": "Step 1 completed successfully. Order is in Step 2, but evidence photos were not provided.",
            }

        # Fetch fresh CSRF token from Step 2 page
        step2_info = self.fetch_approve_step2(order_uuid)
        csrf_step2 = step2_info.get("csrf_token") if step2_info.get("success") else None

        # Execute Step 2 photo upload
        step2_res = self.upload_installation_step2_photos(
            order_uuid=order_uuid,
            front_photo_path=actual_front,
            rear_photo_path=actual_rear,
            checklist_path=checklist_path,
            csrf_token=csrf_step2,
            vehicle_type="M",
            dry_run=dry_run,
            log_callback=log_callback,
        )

        if not step2_res.get("success"):
            return {
                "success": False,
                "step1": step1_res,
                "step2": step2_res,
                "order_uuid": order_uuid,
                "error": f"Step 2 failed: {step2_res.get('error')}",
            }

        # Optional Step 3 Finalization
        if submit_step3:
            step3_info = self.fetch_confirmation_step3(order_uuid)
            csrf_step3 = step3_info.get("csrf_token") if step3_info.get("success") else None
            step3_res = self.submit_confirmation_step3(
                order_uuid=order_uuid,
                front_photo_path=front_photo_path,
                rear_photo_path=rear_photo_path,
                checklist_path=checklist_path,
                csrf_token=csrf_step3,
                dry_run=dry_run,
                log_callback=log_callback,
            )
            return {
                "success": step3_res.get("success", False),
                "step1": step1_res,
                "step2": step2_res,
                "step3": step3_res,
                "order_uuid": order_uuid,
                "is_finalized": step3_res.get("is_finalized", False),
                "redirect_url": step3_res.get("redirect_url", ""),
                "message": step3_res.get("message", ""),
            }

        return {
            "success": step2_res.get("success", False),
            "step1": step1_res,
            "step2": step2_res,
            "order_uuid": order_uuid,
            "step3_ready": step2_res.get("step3_ready", False),
            "redirect_url": step2_res.get("redirect_url", ""),
            "message": step2_res.get("message", ""),
        }

    def get_status(self) -> Dict[str, Any]:
        """Returns the current session and reachability status summary."""
        session_data = self.session_store.session
        is_valid = session_data.is_cookie_valid()
        expires_in = int(session_data.expires_at - time.time()) if session_data.expires_at > 0 else 0
        days_left = max(0, round(expires_in / 86400, 1))

        return {
            "url": self.base_url,
            "authenticated": is_valid and session_data.is_authenticated,
            "user_email": session_data.user_email,
            "user_uuid": session_data.user_uuid,
            "expires_in_days": days_left,
            "last_verified_at": session_data.last_verified_at,
            "session_file": str(self.session_store.storage_path),
            "status_message": session_data.last_status_message,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Regex / Extraction Helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_csrf_from_html(html: str) -> str:
        # Match hidden input
        m = re.search(r'name=["\']_csrf-frontend["\']\s+value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        # Match meta tag
        m2 = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
        if m2:
            return m2.group(1)
        return ""

    @staticmethod
    def _extract_user_uuid_from_html(html: str) -> str:
        m = re.search(r'/user/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', html)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_uuid_from_cookie(cookie_val: str) -> str:
        if not cookie_val:
            return ""
        decoded = urllib.parse.unquote(cookie_val)
        m = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', decoded)
        return m.group(1) if m else ""


# Default singleton instance
_default_web_client: Optional[ITMSWebClient] = None


def get_web_client() -> ITMSWebClient:
    global _default_web_client
    if _default_web_client is None:
        _default_web_client = ITMSWebClient()
    return _default_web_client


def get_current_itms_account() -> str:
    """Returns the normalized email of the currently authenticated ITMS account, or empty string if offline."""
    try:
        client = get_web_client()
        session = getattr(getattr(client, "session_store", None), "session", None)
        if getattr(session, "is_authenticated", False) or (hasattr(session, "is_cookie_valid") and session.is_cookie_valid()):
            email = getattr(session, "user_email", "")
            if isinstance(email, str):
                return email.strip().lower()
    except Exception:
        pass
    return ""
