"""
ITMS (Intelligent Transport Management System) Web App API Client.

Connects this verification system directly to the live ITMS web application
(e.g. https://stock.itms.ug) to search installation orders, verify serials,
and push verified front and rear photographic evidence.

Authentication & Token Lifecycle:
---------------------------------
1. Login: Authenticates with username/email and password against the ITMS auth endpoint.
   Returns an `access_token` and a `refresh_token`.
2. Token Storage: Tokens are cached in-memory and persisted to a local file
   (`ITMS_TOKEN_STORAGE_FILE`), allowing CLI runs and workers to reuse active sessions.
3. Automatic Refresh: When the access token expires or on HTTP 401 Unauthorized,
   the client automatically calls the refresh endpoint with the refresh token to
   obtain a new access token without interrupting the operator.
4. Workflow contract: Mirrors the 4-step contract from `core/services/itms_mock.py`
   (lookup_order -> verify_serial -> upload_front -> upload_rear -> validate_and_finalize)
   so the submission worker can seamlessly switch between simulated and live backends.
"""
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from django.conf import settings

from core.services.itms_mock import ITMSError, StepResult

logger = logging.getLogger(__name__)


class ITMSAuthError(ITMSError):
    """Raised when authentication or token refresh fails."""


class ITMSConnectionError(ITMSError):
    """Raised when the ITMS server cannot be reached."""


class ITMSAPIError(ITMSError):
    """Raised when the ITMS API returns an unexpected error response."""


@dataclass
class TokenData:
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0  # Unix timestamp
    user_email: str = ""

    def is_access_valid(self, buffer_seconds: int = 60) -> bool:
        """Return True if access token is present and not expired (with buffer)."""
        if not self.access_token:
            return False
        if self.expires_at <= 0:
            # If server didn't specify an expiration, treat non-empty token as valid
            return True
        return time.time() < (self.expires_at - buffer_seconds)

    def has_refresh(self) -> bool:
        """Return True if a refresh token is present."""
        return bool(self.refresh_token)


class TokenStore:
    """Manages storage and persistence of ITMS access and refresh tokens."""

    def __init__(self, storage_path: Optional[str] = None):
        if storage_path:
            self.storage_path = Path(storage_path)
        else:
            default_path = getattr(settings, "ITMS_TOKEN_STORAGE_FILE", None)
            if default_path:
                self.storage_path = Path(default_path)
            else:
                from core.services.secure_storage import get_secure_auth_path
                self.storage_path = get_secure_auth_path("itms_tokens.json", legacy_vault_file=".itms_tokens.json")

        self._tokens: TokenData = TokenData()
        self.load()

    def load(self) -> TokenData:
        """Load stored tokens from disk if the file exists."""
        if not self.storage_path.is_file():
            self._tokens = TokenData()
            return self._tokens

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._tokens = TokenData(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                token_type=data.get("token_type", "Bearer"),
                expires_at=float(data.get("expires_at", 0.0)),
                user_email=data.get("user_email", ""),
            )
        except Exception as exc:
            logger.warning("Failed to read ITMS token file %s: %s", self.storage_path, exc)
            self._tokens = TokenData()
        return self._tokens

    def save(
        self,
        access_token: str,
        refresh_token: str = "",
        expires_in: Optional[int] = None,
        token_type: str = "Bearer",
        user_email: str = "",
    ) -> TokenData:
        """Persist fresh tokens to memory and disk."""
        expires_at = (time.time() + expires_in) if expires_in else 0.0
        # If new refresh token is omitted, preserve the existing one
        if not refresh_token and self._tokens.refresh_token:
            refresh_token = self._tokens.refresh_token

        self._tokens = TokenData(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type or "Bearer",
            expires_at=expires_at,
            user_email=user_email or self._tokens.user_email,
        )

        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self._tokens), f, indent=2)
            # Restrict file permissions on Unix if applicable
            try:
                os.chmod(self.storage_path, 0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to write ITMS token file %s: %s", self.storage_path, exc)

        return self._tokens

    def clear(self):
        """Remove cached tokens from memory and disk."""
        self._tokens = TokenData()
        if self.storage_path.is_file():
            try:
                self.storage_path.unlink()
            except Exception as exc:
                logger.warning("Could not delete token file %s: %s", self.storage_path, exc)

    @property
    def tokens(self) -> TokenData:
        return self._tokens


class ITMSClient:
    """
    HTTP client for the ITMS Web Application (https://stock.itms.ug).
    Handles dual-token JWT authentication, automatic token refresh,
    order queries, and photo uploads.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token_store: Optional[TokenStore] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (base_url or getattr(settings, "ITMS_API_BASE_URL", "https://stock.itms.ug")).rstrip("/")
        self.timeout = timeout or getattr(settings, "ITMS_REQUEST_TIMEOUT_SECONDS", 30)
        self.token_store = token_store or TokenStore()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
        })

    # ──────────────────────────────────────────────────────────────────────────
    # Diagnostic / Connectivity
    # ──────────────────────────────────────────────────────────────────────────

    def test_connection(self) -> Dict[str, Any]:
        """Test network reachability, SSL certificate, and server response."""
        start = time.time()
        try:
            resp = self.session.get(self.base_url, timeout=self.timeout, headers={"Accept": "*/*"})
            elapsed_ms = round((time.time() - start) * 1000, 2)
            is_ok = resp.status_code in (200, 301, 302, 304)
            return {
                "success": is_ok,
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "server": resp.headers.get("Server", "Unknown"),
                "url": self.base_url,
                "message": f"Connected to {self.base_url} (HTTP {resp.status_code}, {elapsed_ms}ms)",
                "error": "" if is_ok else f"Server returned HTTP {resp.status_code}",
            }
        except requests.exceptions.SSLError as exc:
            return {
                "success": False,
                "error": f"SSL Certificate Error: {exc}",
                "url": self.base_url,
            }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "error": f"Connection Error: {exc}",
                "url": self.base_url,
            }

    def get_auth_status(self) -> Dict[str, Any]:
        """Return human-readable status of the stored tokens."""
        tokens = self.token_store.tokens
        is_valid = tokens.is_access_valid()
        has_refresh = tokens.has_refresh()

        expires_in = int(tokens.expires_at - time.time()) if tokens.expires_at > 0 else None
        return {
            "has_access_token": bool(tokens.access_token),
            "access_token_valid": is_valid,
            "has_refresh_token": has_refresh,
            "user_email": tokens.user_email or "Not set",
            "expires_in_seconds": expires_in,
            "token_file": str(self.token_store.storage_path),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Authentication & Token Refresh
    # ──────────────────────────────────────────────────────────────────────────

    def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        login_endpoint: Optional[str] = None,
    ) -> TokenData:
        """
        Authenticate against ITMS web app and store access + refresh tokens.
        Supports both JSON REST API login and Yii2/PHP login endpoints.
        """
        email = email or getattr(settings, "ITMS_USERNAME", "")
        password = password or getattr(settings, "ITMS_PASSWORD", "")
        endpoint = login_endpoint or getattr(settings, "ITMS_LOGIN_ENDPOINT", "/site/login")

        if not email or not password:
            raise ITMSAuthError(
                "Missing credentials: set ITMS_USERNAME and ITMS_PASSWORD in .env "
                "or pass them directly to login()."
            )

        target_url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info("Attempting ITMS login at %s for %s", target_url, email)

        # Attempt 1: Standard JSON API payload
        json_payloads = [
            {"email": email, "password": password},
            {"username": email, "password": password},
            {"LoginForm": {"email": email, "password": password, "rememberMe": 1}},
        ]

        last_error = ""
        for payload in json_payloads:
            try:
                resp = self.session.post(target_url, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        tokens = self._extract_tokens_from_json(data, email)
                        if tokens:
                            return tokens
                    except ValueError:
                        pass
                elif resp.status_code in (400, 401, 403):
                    last_error = resp.text[:200]
            except requests.exceptions.RequestException as exc:
                raise ITMSConnectionError(f"Network failure connecting to {target_url}: {exc}") from exc

        # Attempt 2: Form-encoded payload with CSRF cookie handling (for Yii2 /site/login)
        try:
            # First fetch the login page to acquire CSRF cookie & token
            get_resp = self.session.get(target_url, timeout=self.timeout)
            csrf_match = re.search(r'name=["\']_csrf-frontend["\']\s+value=["\']([^"\']+)["\']', get_resp.text)
            csrf_token = csrf_match.group(1) if csrf_match else ""

            form_data = {
                "LoginForm[email]": email,
                "LoginForm[password]": password,
                "LoginForm[rememberMe]": "1",
            }
            if csrf_token:
                form_data["_csrf-frontend"] = csrf_token

            resp = self.session.post(target_url, data=form_data, timeout=self.timeout)

            # Check if JSON was returned
            try:
                data = resp.json()
                tokens = self._extract_tokens_from_json(data, email)
                if tokens:
                    return tokens
            except ValueError:
                pass

            # If the response redirected or returned auth cookies, check headers
            auth_header = resp.headers.get("Authorization") or resp.headers.get("token")
            if auth_header:
                token_val = auth_header.replace("Bearer ", "").strip()
                return self.token_store.save(access_token=token_val, user_email=email)

            if resp.status_code in (200, 302):
                # Web session established; if server also passes API tokens in body or cookies
                cookie_token = resp.cookies.get("token") or resp.cookies.get("access_token")
                if cookie_token:
                    return self.token_store.save(access_token=cookie_token, user_email=email)

        except requests.exceptions.RequestException as exc:
            raise ITMSConnectionError(f"Failed to post form login to {target_url}: {exc}") from exc

        raise ITMSAuthError(
            f"Login failed at {target_url} (HTTP {resp.status_code if 'resp' in locals() else 'N/A'}). "
            f"Please verify credentials or supply the exact API login endpoint. Response preview: {last_error or 'No tokens returned'}"
        )

    def _extract_tokens_from_json(self, data: Dict[str, Any], email: str) -> Optional[TokenData]:
        """Extract access and refresh tokens from diverse standard JSON shapes."""
        if isinstance(data, dict):
            for envelope_key in ("data", "result", "payload", "tokens"):
                if envelope_key in data and isinstance(data[envelope_key], dict):
                    data = data[envelope_key]

        access = (
            data.get("access_token")
            or data.get("accessToken")
            or data.get("access")
            or data.get("token")
            or data.get("jwt")
        )
        refresh = (
            data.get("refresh_token")
            or data.get("refreshToken")
            or data.get("refresh")
        )
        expires_in = data.get("expires_in") or data.get("expiresIn") or data.get("ttl")
        token_type = data.get("token_type") or data.get("tokenType") or "Bearer"

        if access:
            expires_int = int(expires_in) if expires_in else 3600
            return self.token_store.save(
                access_token=str(access),
                refresh_token=str(refresh) if refresh else "",
                expires_in=expires_int,
                token_type=str(token_type),
                user_email=email,
            )
        return None

    def refresh_access_token(self, refresh_endpoint: Optional[str] = None) -> TokenData:
        """
        Use the stored refresh token to obtain a new access token.
        Raises ITMSAuthError if no refresh token is stored or refresh fails.
        """
        tokens = self.token_store.tokens
        if not tokens.has_refresh():
            raise ITMSAuthError("Cannot refresh: no refresh token is stored. Please login first.")

        endpoint = refresh_endpoint or getattr(settings, "ITMS_REFRESH_ENDPOINT", "/api/auth/refresh")
        target_url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info("Refreshing ITMS access token at %s", target_url)

        payloads = [
            {"refresh_token": tokens.refresh_token},
            {"refreshToken": tokens.refresh_token},
            {"refresh": tokens.refresh_token},
        ]

        last_status, last_text = 0, ""
        for payload in payloads:
            try:
                resp = self.session.post(
                    target_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {tokens.refresh_token}"},
                    timeout=self.timeout,
                )
                last_status = resp.status_code
                last_text = resp.text[:200]
                if resp.status_code == 200:
                    data = resp.json()
                    new_tokens = self._extract_tokens_from_json(data, tokens.user_email)
                    if new_tokens:
                        logger.info("Successfully refreshed ITMS access token.")
                        return new_tokens
            except requests.exceptions.RequestException as exc:
                raise ITMSConnectionError(f"Connection failure refreshing token: {exc}") from exc

        if last_status in (401, 403):
            self.token_store.clear()
            raise ITMSAuthError("Refresh token expired or revoked. Re-login is required.")

        raise ITMSAuthError(
            f"Token refresh failed at {target_url} (HTTP {last_status}): {last_text}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Authorized HTTP Dispatcher
    # ──────────────────────────────────────────────────────────────────────────

    def request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Execute an authorized HTTP request to the ITMS API.
        Automatically attaches the Bearer token, refreshes if expired,
        and transparently retries once on HTTP 401 Unauthorized.
        """
        tokens = self.token_store.tokens

        # Proactively refresh if token is near expiration
        if not tokens.is_access_valid() and tokens.has_refresh():
            try:
                tokens = self.refresh_access_token()
            except Exception as exc:
                logger.warning("Proactive token refresh failed: %s", exc)

        headers = kwargs.pop("headers", {})
        if tokens.access_token:
            headers["Authorization"] = f"{tokens.token_type} {tokens.access_token}"

        target_url = f"{self.base_url}/{endpoint.lstrip('/')}"
        timeout = kwargs.pop("timeout", self.timeout)

        try:
            resp = self.session.request(method, target_url, headers=headers, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise ITMSConnectionError(f"Network error on {method} {target_url}: {exc}") from exc

        # Handle 401 Unauthorized by attempting a token refresh and retrying once
        if resp.status_code == 401 and tokens.has_refresh():
            logger.info("Received 401 from %s, attempting automatic token refresh...", target_url)
            try:
                fresh_tokens = self.refresh_access_token()
                headers["Authorization"] = f"{fresh_tokens.token_type} {fresh_tokens.access_token}"
                resp = self.session.request(method, target_url, headers=headers, timeout=timeout, **kwargs)
            except ITMSAuthError:
                raise ITMSAuthError("Session expired on ITMS server. Please re-authenticate.")

        return resp

    # ──────────────────────────────────────────────────────────────────────────
    # 4-Step Submission Workflow (Matches itms_mock contract)
    # ──────────────────────────────────────────────────────────────────────────

    def lookup_order(self, order_number: str) -> StepResult:
        """Step 1: Locate the vehicle installation order in the ITMS registry."""
        if not order_number:
            raise ITMSError("ORDER_LOOKUP failed: empty order_number")

        endpoint = getattr(settings, "ITMS_ORDER_LOOKUP_ENDPOINT", "/api/orders/lookup")
        try:
            resp = self.request("GET", endpoint, params={"order_number": order_number})
            if resp.status_code == 200:
                return StepResult(success=True, message=f"Order {order_number} located in ITMS registry.")
            if resp.status_code == 404:
                raise ITMSError(f"ORDER_LOOKUP failed: order {order_number} not found in ITMS.")
            raise ITMSAPIError(f"ORDER_LOOKUP returned HTTP {resp.status_code}: {resp.text[:150]}")
        except (ITMSConnectionError, ITMSAuthError) as exc:
            raise ITMSError(f"ORDER_LOOKUP failed: {exc}") from exc

    def verify_serial(self, order_number: str, plate_serial: str, tracker_id: str) -> StepResult:
        """Step 2: Verify the plate serial and tracker hardware against the order."""
        endpoint = getattr(settings, "ITMS_SERIAL_VERIFY_ENDPOINT", "/api/orders/verify-serial")
        payload = {
            "order_number": order_number,
            "plate_serial": plate_serial or "",
            "tracker_id": tracker_id or "",
        }
        try:
            resp = self.request("POST", endpoint, json=payload)
            if resp.status_code == 200:
                return StepResult(
                    success=True,
                    message=f"Serial/tracker verified for {order_number} (serial={plate_serial or 'N/A'}, tracker={tracker_id or 'N/A'}).",
                )
            raise ITMSAPIError(f"SERIAL_VERIFY returned HTTP {resp.status_code}: {resp.text[:150]}")
        except (ITMSConnectionError, ITMSAuthError) as exc:
            raise ITMSError(f"SERIAL_VERIFY failed: {exc}") from exc

    def upload_front_photo(self, order_number: str, vault_path: str) -> StepResult:
        """Step 3: Upload the verified front photograph evidence."""
        return self._upload_photo(order_number, vault_path, orientation="FRONT")

    def upload_rear_photo(self, order_number: str, vault_path: str) -> StepResult:
        """Step 4: Upload the verified rear photograph evidence."""
        return self._upload_photo(order_number, vault_path, orientation="REAR")

    def _upload_photo(self, order_number: str, vault_path: str, orientation: str) -> StepResult:
        """Internal helper for multipart photo uploads."""
        abs_path = os.path.join(settings.MEDIA_ROOT, vault_path)
        if not os.path.isfile(abs_path):
            raise ITMSError(f"UPLOAD_{orientation} failed: evidence file not found at {abs_path}")

        endpoint_setting = (
            "ITMS_UPLOAD_FRONT_ENDPOINT" if orientation == "FRONT" else "ITMS_UPLOAD_REAR_ENDPOINT"
        )
        default_endpoint = (
            "/api/orders/upload-front" if orientation == "FRONT" else "/api/orders/upload-rear"
        )
        endpoint = getattr(settings, endpoint_setting, default_endpoint)

        filename = os.path.basename(abs_path)
        try:
            with open(abs_path, "rb") as photo_file:
                files = {"file": (filename, photo_file, "image/jpeg")}
                data = {"order_number": order_number, "orientation": orientation}
                resp = self.request("POST", endpoint, data=data, files=files)

            if resp.status_code in (200, 201):
                return StepResult(
                    success=True,
                    message=f"{orientation} evidence uploaded for {order_number}: {vault_path}",
                )
            raise ITMSAPIError(f"UPLOAD_{orientation} returned HTTP {resp.status_code}: {resp.text[:150]}")
        except (ITMSConnectionError, ITMSAuthError) as exc:
            raise ITMSError(f"UPLOAD_{orientation} failed: {exc}") from exc

    def validate_and_finalize(self, order_number: str) -> StepResult:
        """Step 5: Final validation & submission confirmation token issuance."""
        endpoint = getattr(settings, "ITMS_FINALIZE_ENDPOINT", "/api/orders/finalize")
        try:
            resp = self.request("POST", endpoint, json={"order_number": order_number})
            if resp.status_code in (200, 201):
                token = ""
                try:
                    res_json = resp.json()
                    token = (
                        res_json.get("token")
                        or res_json.get("submission_token")
                        or res_json.get("confirmation_number")
                        or f"ITMS-LIVE-{order_number}"
                    )
                except ValueError:
                    token = f"ITMS-LIVE-{order_number}"
                return StepResult(
                    success=True,
                    message=f"Live submission validated for {order_number}.",
                    token=token,
                )
            raise ITMSAPIError(f"VALIDATE returned HTTP {resp.status_code}: {resp.text[:150]}")
        except (ITMSConnectionError, ITMSAuthError) as exc:
            raise ITMSError(f"VALIDATE failed: {exc}") from exc


# Module-level default singleton instance
default_client = ITMSClient()
