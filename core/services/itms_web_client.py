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
    try:
        if settings.configured:
            vault_root = getattr(settings, "VAULT_ROOT", "media/vault")
            return Path(vault_root) / ".itms_web_session.json"
    except Exception:
        pass
    return Path("media/vault") / ".itms_web_session.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


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
        })
        # Inject stored cookies if available
        stored_cookies = self.session_store.session.cookies
        for name, val in stored_cookies.items():
            s.cookies.set(name, val, domain=urllib.parse.urlparse(self.base_url).hostname)
        return s

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
            resp = s.get(url, params=query_params, timeout=self.timeout)
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

                    is_row_archived = archive or (len(clean_tds) >= 11)

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
            is_archived = o.get("is_archived", False)
            order_status = o.get("order_status") or o.get("status", "")

            # Determine local system status
            if is_archived and "installed" in order_status.lower():
                local_status = InstallationOrder.Status.INSTALLED
            elif "installed" in order_status.lower() or "approve" in order_status.lower():
                local_status = InstallationOrder.Status.SUBMITTED
            else:
                local_status = InstallationOrder.Status.PENDING

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
                "itms_action_url": o.get("action_url", ""),
                "is_archived": is_archived,
                "status": local_status,
            }

            obj, was_created = InstallationOrder.objects.update_or_create(
                order_number=order_num,
                defaults=defaults,
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

            # Cross-verify and record audit trail for any matching vehicle pairs
            if local_status == InstallationOrder.Status.INSTALLED:
                verified_installed_count += 1
                pairs = VehicleInstallationPair.objects.filter(
                    models.Q(order=obj) | models.Q(registration_number_detected=canonical_reg)
                )
                for pair in pairs:
                    if pair.verification_status != VehicleInstallationPair.VerificationStatus.SUBMITTED:
                        pair.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
                        pair.order = obj
                        pair.save(update_fields=["verification_status", "order"])
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
        s = self._create_requests_session()
        s.headers["Referer"] = f"{self.base_url}/installation-orders/archive"

        try:
            resp = s.get(url, timeout=self.timeout)
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

        s = self._create_requests_session()
        s.headers["Referer"] = f"{self.base_url}/installation-orders/archive"

        try:
            resp = s.get(full_url, timeout=self.timeout, stream=True)
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
