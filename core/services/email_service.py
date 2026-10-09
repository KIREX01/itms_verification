"""
Email Service for ITMS Verification Copilot.

Provides:
- 2FA Email verification via Resend REST API (https://api.resend.com/emails)
- Secure 6-digit OTP code generation using cryptographic randomness
- Offline / Local Development fallback (logs OTP to server console if no key configured)
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def generate_2fa_code() -> str:
    """Generates a cryptographically secure 6-digit numeric verification code."""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def get_resend_api_key() -> str:
    """Retrieves Resend API key from Django settings or environment."""
    key = getattr(settings, "RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY", "")
    return key.strip()


def get_resend_from_email() -> str:
    """Retrieves sender email address from Django settings or environment."""
    from_email = getattr(settings, "RESEND_FROM_EMAIL", "") or os.environ.get("RESEND_FROM_EMAIL", "")
    return from_email.strip() or "ITMS Verification <auth@info.kirex.online>"


def send_2fa_verification_code(to_email: str, code: str, username: str = "") -> Tuple[bool, str]:
    """
    Sends a 6-digit 2FA verification code to the recipient using the Resend API.
    
    Returns (success: bool, message: str).
    """
    if not to_email:
        return False, "Email address is required."

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()

    if not api_key:
        logger.warning(
            "[2FA DEV FALLBACK] No RESEND_API_KEY found. Simulated 2FA Code for '%s' (%s): [%s]",
            username or "operator",
            to_email,
            code,
        )
        return True, "Simulated 2FA code generated (Check server console/logs)."

    # HTML Email Template with Uganda Flag Branding & Secure High-Contrast Styling
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ITMS Verification Code</title>
</head>
<body style="margin:0; padding:0; background-color:#0f1117; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#e1e4ea;">
  <div style="max-width:540px; margin:30px auto; background-color:#161922; border-radius:12px; border:1px solid #282d3d; overflow:hidden; box-shadow:0 12px 36px rgba(0,0,0,0.5);">
    
    <!-- Uganda National Flag Ribbon -->
    <div style="height:6px; display:flex; width:100%; font-size:0;">
      <div style="background-color:#000000; width:16.66%; height:6px;"></div>
      <div style="background-color:#ffd100; width:16.66%; height:6px;"></div>
      <div style="background-color:#d90000; width:16.66%; height:6px;"></div>
      <div style="background-color:#000000; width:16.66%; height:6px;"></div>
      <div style="background-color:#ffd100; width:16.66%; height:6px;"></div>
      <div style="background-color:#d90000; width:16.66%; height:6px;"></div>
    </div>

    <!-- Header -->
    <div style="padding:28px 32px 18px; text-align:center; border-bottom:1px solid #232838;">
      <div style="font-size:32px; line-height:1; margin-bottom:10px;">🇺🇬</div>
      <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.5px;">
        ITMS VERIFICATION <span style="color:#ffd100;">COPILOT</span>
      </h1>
      <p style="margin:6px 0 0; font-size:12px; color:#8b949e; letter-spacing:0.3px;">
        OFFICIAL OPERATOR ONBOARDING &bull; 2-FACTOR AUTHENTICATION
      </p>
    </div>

    <!-- Content -->
    <div style="padding:32px 32px 28px;">
      <p style="margin:0 0 16px; font-size:15px; color:#c9d1d9; line-height:1.5;">
        Hello <strong>{username or 'Operator'}</strong>,
      </p>
      <p style="margin:0 0 24px; font-size:14px; color:#8b949e; line-height:1.5;">
        You are completing your registration for the <strong>ITMS Verification Console</strong>. 
        Enter the 6-digit security code below to verify your email and activate your operator account:
      </p>

      <!-- OTP Code Display Card -->
      <div style="text-align:center; margin:28px 0; padding:22px 16px; background-color:#090b10; border:2px solid #ffd100; border-radius:10px;">
        <span style="font-family:'Courier New', Courier, monospace; font-size:36px; font-weight:900; letter-spacing:10px; color:#ffffff; text-shadow:0 0 12px rgba(255,209,0,0.4);">
          {code}
        </span>
        <div style="margin-top:8px; font-size:11px; color:#ffd100; font-weight:700; text-transform:uppercase; letter-spacing:1px;">
          Valid for 10 minutes
        </div>
      </div>

      <p style="margin:24px 0 0; font-size:12px; color:#6e7681; line-height:1.5;">
        ⚠️ If you did not initiate this registration on the ITMS Verification System, please ignore this email. No changes will be made to your account.
      </p>
    </div>

    <!-- Footer -->
    <div style="padding:16px 32px; background-color:#11131a; border-top:1px solid #232838; text-align:center; font-size:11px; color:#484f58;">
      ITMS Verification Copilot &bull; Ministry of Works and Transport / ITMS Operations &bull; Kampala, Uganda
    </div>
  </div>
</body>
</html>"""

    text_content = (
        f"ITMS Verification Copilot - Operator Verification Code\n\n"
        f"Hello {username or 'Operator'},\n\n"
        f"Your 6-digit security verification code is: {code}\n\n"
        f"This code is valid for 10 minutes.\n"
        f"If you did not initiate this request, you can safely ignore this email.\n"
    )

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": f"🔐 ITMS Verification Security Code: {code}",
        "html": html_content,
        "text": text_content,
    }

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if response.status_code in (200, 201):
            res_data = response.json()
            email_id = res_data.get("id", "")
            logger.info("2FA code sent via Resend to %s (id: %s)", to_email, email_id)
            return True, "Verification code sent to your email."
        else:
            err_detail = response.text
            logger.error("Resend API error (%s): %s", response.status_code, err_detail)
            return False, f"Could not send email: {err_detail}"
    except Exception as exc:
        logger.error("Failed to connect to Resend API: %s", exc)
        return False, f"Network error sending verification email: {exc}"
