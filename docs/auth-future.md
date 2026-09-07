  Evaluating Temporary File Clean-Up
  We have built the foundational tools and infrastructure to connect this verification system to the live ITMS web
  app at https://stock.itms.ug, with dual-token (access token + refresh token) lifecycle management.
  ──────
  ### What Has Been Set Up

  #### 1. Live ITMS API Client & Token Manager (core/services/itms_client.py)
  • Dual-Token Lifecycle:
      • login(): Authenticates against the ITMS web app, extracts access_token and refresh_token, and stores them
      securely.
      • refresh_access_token(): Exchanges the refresh token for a new access token when expired.
      • Auto-Refresh on 401: When calling any API endpoint, if an HTTP 401 Unauthorized response is received, the
      client automatically attempts a token refresh and retries the request without interrupting the user.
  • Token Persistence (TokenStore): Caches tokens in media/vault/.itms_tokens.json so CLI commands and background
  workers don't need to re-login on every invocation.
  • Unified 4-Step Contract: Implements the exact same interface as itms_mock.py (lookup_order, verify_serial,
  upload_front_photo, upload_rear_photo, validate_and_finalize).
  #### 2. Dual-Backend Submission Engine (core/services/submission_worker.py & submit_itms.py)

  • Supports running either in mock mode (sandbox) or live mode (https://stock.itms.ug).
  • Added --backend {mock,live} flag to python manage.py submit_itms.
  • Full audit logging (SubmissionAuditLog) records whether submissions went to mock or live servers.

  #### 3. Dedicated Authentication & Connectivity CLI (manage.py itms_auth)

  A command to inspect and test the ITMS connection:

  • python manage.py itms_auth --ping: Tests network reachability, SSL certificate, and server latency.
  • python manage.py itms_auth --login: Authenticates against the web app and stores tokens.
  • python manage.py itms_auth --status: Displays current token validity and time to expiration.
  • python manage.py itms_auth --refresh: Tests refreshing the access token.
  • python manage.py itms_auth --logout: Clears cached tokens.

  Connectivity check to https://stock.itms.ug:

    Connecting to ITMS server (https://stock.itms.ug)...
    SUCCESS: Connected to https://stock.itms.ug (HTTP 200, 630.88ms) (Server: nginx)

    --- ITMS Authentication Status ---
    Target URL          : https://stock.itms.ug
    User email          : Not set
    Has Access Token    : NO
    Access Token Valid  : NO / Expired
    Has Refresh Token   : NO
    Token Storage File  : media\vault\.itms_tokens.json
  ──────
  ### Information You Need to Provide in the Future

  To connect to https://stock.itms.ug, provide the following details from your ITMS administrator or API
  documentation:

  #### 1. Credentials & Authentication Endpoint

  • API Login URL: The exact endpoint path for authentication (e.g. /api/v1/auth/login, /api/token/, or /site/login).
  • Operator Credentials: The user email/username and password allocated for this automated system.
  • Login Request Format: Whether the endpoint expects:
      • JSON payload (e.g. {"email": "...", "password": "..."}) or Form data.
  • Token Response Schema: The exact JSON keys returned for the tokens (e.g. access_token vs token vs accessToken,
  and refresh_token vs refreshToken).
  • Token Refresh URL: The endpoint used to exchange the refresh token (e.g. /api/auth/refresh or
  /api/token/refresh/).

  #### 2. Order Lookup & Verification Endpoints

  • Order Lookup Route: The endpoint to verify that an installation order exists (e.g. GET /api/orders/{order_number}
  or GET /api/orders?plate={plate}).
  • Serial / Tracker Verification: The endpoint used to confirm the number plate serial number and GPS tracker ID
  match the order.

  #### 3. Photo Upload Endpoints & Multipart Fields

  • Evidence Upload Route: The endpoint path to push images (e.g. POST /api/installations/{order_number}/photos or
  /api/orders/upload).
  • Form Field Names: The multipart form field names expected by the server:
      • e.g. Does it expect front_photo and rear_photo in one request, or separate calls with file and
      orientation="FRONT"?
  • File Constraints: Maximum file size (MB) or allowed MIME types (JPEG, PNG).

  #### 4. Final Submission Confirmation

  • Finalize Route: The endpoint that locks the installation order and triggers submission (e.g. POST
  /api/orders/{order_number}/submit).
  • Confirmation Token / Response: What confirmation code or transaction reference the server returns upon success.