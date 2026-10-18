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
import re
import secrets
from typing import Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def generate_2fa_code() -> str:
    """Generates a cryptographically secure 6-digit numeric verification code."""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def _sanitize_log_str(val: str, max_len: int = 100) -> str:
    """Sanitizes strings before logging to prevent CRLF log injection (CWE-117)."""
    return re.sub(r"[\x00-\x1f]", "", str(val or "")).strip()[:max_len]


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
        safe_user = _sanitize_log_str(username) or "operator"
        safe_to = _sanitize_log_str(to_email)
        safe_code = _sanitize_log_str(code)
        logger.warning(
            "[2FA DEV FALLBACK] No RESEND_API_KEY found. Simulated 2FA Code for '%s' (%s): [%s]",
            safe_user,
            safe_to,
            safe_code,
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
            safe_to = _sanitize_log_str(to_email)
            safe_id = _sanitize_log_str(email_id)
            logger.info("2FA code sent via Resend to %s (id: %s)", safe_to, safe_id)
            return True, "Verification code sent to your email."
        else:
            err_detail = response.text
            safe_status = _sanitize_log_str(str(response.status_code))
            logger.error("Resend API error (%s): %s", safe_status, err_detail)
            return False, f"Could not send email: {err_detail}"
    except Exception as exc:
        logger.exception("Failed to connect to Resend API")
        return False, f"Network error sending verification email: {exc}"


def send_stock_reconciliation_report_email(
    recon_data: dict,
    recipient_emails: list[str],
    operator_name: str = "Operator",
    notes: str = "",
) -> Tuple[bool, str, list[str]]:
    """
    Sends the official Daily Stock Reconciliation Report email to specified recipients.
    Formats the report table (Opening, Received, Scheduled, Installed, Performance %,
    Transfers, Backlog, Closing Balance) with Uganda-branded responsive HTML styling.
    """
    if not recipient_emails:
        return False, "No recipient emails provided.", []

    # Clean & validate recipient emails
    clean_recipients = []
    for em in recipient_emails:
        c = str(em or "").strip()
        if c and "@" in c and c not in clean_recipients:
            clean_recipients.append(c)

    if not clean_recipients:
        return False, "No valid recipient email addresses found.", []

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()

    bond_name = recon_data.get("storage_bond_name") or recon_data.get("warehouse_name") or "AGM / SPIRO"
    report_date = recon_data.get("formatted_date") or recon_data.get("work_date") or "Current Shift"
    suffix = recon_data.get("work_date_suffix") or ""
    closing_balance = recon_data.get("closing_stock", 0)
    remarks = notes or recon_data.get("remarks") or ""

    rows = recon_data.get("report_table", {}).get("rows", [])

    # Build HTML table rows
    table_rows_html = []
    for idx, r in enumerate(rows):
        metric = r.get("metric", "")
        pmo = r.get("pmo", 0)
        psv = r.get("psv", 0)
        tot = r.get("total", 0)

        is_closing = "closing" in metric.lower()
        is_perf = "performance" in metric.lower() or "%" in metric

        row_bg = "#1f2430" if (idx % 2 == 1) else "#161922"
        border_style = "border-bottom:1px solid #282d3d;"
        text_color = "#e1e4ea"
        font_weight = "normal"

        if is_closing:
            row_bg = "#232a3b"
            border_style = "border-top:2px solid #ffd100; border-bottom:2px solid #ffd100;"
            text_color = "#ffd100"
            font_weight = "bold"

        if is_perf:
            tot_str = str(tot).replace("%", "").strip()
            try:
                tot_val = float(tot_str)
                badge_bg = "#10b981" if tot_val >= 100 else ("#f59e0b" if tot_val >= 80 else "#ef4444")
            except Exception:
                badge_bg = "#ffd100"
            tot_cell = f"<span style='background-color:{badge_bg}; color:#000000; padding:3px 8px; border-radius:4px; font-weight:800; font-size:12px;'>{tot}</span>"
        else:
            tot_cell = f"<span style='font-family:monospace; font-size:14px; font-weight:{font_weight}; color:{text_color};'>{tot}</span>"

        pmo_cell = f"<span style='font-family:monospace; font-size:13px; color:{text_color};'>{pmo}</span>"
        psv_cell = f"<span style='font-family:monospace; font-size:13px; color:{text_color};'>{psv}</span>"

        table_rows_html.append(f'''
        <tr style="background-color:{row_bg}; {border_style}">
          <td style="padding:10px 14px; font-weight:{'800' if is_closing else '600'}; color:{text_color};">{metric}</td>
          <td style="padding:10px 14px; text-align:right;">{pmo_cell}</td>
          <td style="padding:10px 14px; text-align:right;">{psv_cell}</td>
          <td style="padding:10px 14px; text-align:right;">{tot_cell}</td>
        </tr>
        ''')

    table_body = "\n".join(table_rows_html)

    # Build ASCII text representation
    text_lines = [
        "=" * 72,
        "ITMS DAILY STOCK RECONCILIATION REPORT",
        f"Storage Bond Name: {bond_name}",
        f"Report Date:       {report_date} (Shift #{suffix})",
        f"Generated By:      {operator_name}",
        "=" * 72,
        f"{'DESCRIPTION':<24} {'PRIVATE':>12} {'PUBLIC':>12} {'TOTAL':>16}",
        "-" * 72,
    ]
    for r in rows:
        metric = r.get("metric", "")
        pmo = str(r.get("pmo", 0))
        psv = str(r.get("psv", 0))
        tot = str(r.get("total", 0))
        text_lines.append(f"{metric:<24} {pmo:>12} {psv:>12} {tot:>16}")
    text_lines.append("=" * 72)
    if remarks:
        text_lines.append(f"Remarks: {remarks}")
        text_lines.append("-" * 72)
    text_lines.append("Ministry of Works and Transport • ITMS Operations • Kampala, Uganda")
    text_content = "\n".join(text_lines)

    remarks_html = ""
    if remarks:
        remarks_html = f'''
        <div style="margin:20px 0 10px; padding:14px 16px; background-color:#1c212d; border-left:4px solid #ffd100; border-radius:4px;">
          <div style="font-size:11px; text-transform:uppercase; color:#ffd100; font-weight:800; letter-spacing:0.5px; margin-bottom:4px;">Shift Remarks &amp; Notes</div>
          <div style="font-size:13px; color:#c9d1d9; line-height:1.5;">{remarks}</div>
        </div>
        '''

    html_content = f'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Stock Reconciliation Report - {bond_name}</title>
</head>
<body style="margin:0; padding:0; background-color:#0b0d13; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#e1e4ea;">
  <div style="max-width:680px; margin:25px auto; background-color:#141720; border-radius:12px; border:1px solid #282d3d; overflow:hidden; box-shadow:0 16px 40px rgba(0,0,0,0.6);">
    
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
    <div style="padding:24px 28px 18px; border-bottom:1px solid #232838; background-color:#161a24;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
        <div>
          <div style="font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
            ITMS Verification Copilot &bull; Daily Shift Ledger
          </div>
          <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.3px;">
            {bond_name}
          </h1>
          <div style="margin-top:4px; font-size:13px; color:#8b949e;">
            Official Stock Reconciliation &bull; Shift #{suffix}
          </div>
        </div>
        <div style="text-align:right; background-color:#0d0f16; border:1px solid #282d3d; border-radius:8px; padding:10px 16px;">
          <div style="font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px;">Report Date</div>
          <div style="font-size:15px; font-weight:800; color:#ffd100;">{report_date}</div>
        </div>
      </div>
    </div>

    <!-- Table Section -->
    <div style="padding:24px 28px;">
      <table style="width:100%; border-collapse:collapse; margin-bottom:16px;">
        <thead>
          <tr style="background-color:#0d0f16; border-bottom:2px solid #ffd100;">
            <th style="padding:12px 14px; text-align:left; font-size:11px; text-transform:uppercase; color:#ffd100; letter-spacing:0.8px;">Description</th>
            <th style="padding:12px 14px; text-align:right; font-size:11px; text-transform:uppercase; color:#ffd100; letter-spacing:0.8px;">Private</th>
            <th style="padding:12px 14px; text-align:right; font-size:11px; text-transform:uppercase; color:#ffd100; letter-spacing:0.8px;">Public</th>
            <th style="padding:12px 14px; text-align:right; font-size:11px; text-transform:uppercase; color:#ffffff; font-weight:800; letter-spacing:0.8px;">Total</th>
          </tr>
        </thead>
        <tbody>
          {table_body}
        </tbody>
      </table>

      {remarks_html}

      <!-- KPI Summary Card -->
      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px; padding:16px; background-color:#0d0f16; border-radius:8px; border:1px solid #232838; flex-wrap:wrap; gap:12px;">
        <div>
          <div style="font-size:11px; color:#8b949e; text-transform:uppercase;">Closing Stock Balance</div>
          <div style="font-size:22px; font-weight:900; color:#ffd100; font-family:monospace; margin-top:2px;">{closing_balance} units</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:11px; color:#8b949e; text-transform:uppercase;">Report Dispatcher</div>
          <div style="font-size:13px; font-weight:700; color:#e1e4ea; margin-top:4px;">👤 {operator_name}</div>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div style="padding:16px 28px; background-color:#0d0f16; border-top:1px solid #232838; text-align:center; font-size:11px; color:#6e7681;">
      ITMS Verification Copilot &bull; Ministry of Works and Transport / ITMS Operations &bull; Kampala, Uganda
    </div>
  </div>
</body>
</html>'''

    subject = f"📊 Daily Stock Reconciliation Report - {bond_name} - {report_date}"

    # If no RESEND_API_KEY, use development fallback
    if not api_key:
        logger.warning(
            "[EMAIL DEV FALLBACK] No RESEND_API_KEY configured. Simulated stock report email to %s:\n%s",
            clean_recipients,
            text_content,
        )
        return True, "Simulated email sent (Check server console/logs).", clean_recipients

    payload = {
        "from": from_email,
        "to": clean_recipients,
        "subject": subject,
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
            timeout=12,
        )
        if response.status_code in (200, 201):
            logger.info("Stock reconciliation report emailed to %s", clean_recipients)
            return True, f"Report sent to {len(clean_recipients)} recipient(s).", clean_recipients
        else:
            err = response.text
            logger.error("Resend email error (%s): %s", response.status_code, err)
            return False, f"Resend API error: {err}", clean_recipients
    except Exception as exc:
        logger.exception("Failed to dispatch stock report email")
        return False, f"Network error sending report: {exc}", clean_recipients
