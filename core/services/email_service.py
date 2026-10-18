"""
Email Service for ITMS Verification Copilot.

Provides:
- 2FA Email verification via Resend REST API (https://api.resend.com/emails)
- Secure 6-digit OTP code generation using cryptographic randomness
- Offline / Local Development fallback (logs OTP to server console if no key configured)
- Mobile-responsive transactional HTML templates with quick-action copy and security ignore disclaimers
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
    return from_email.strip() or "CLOSE Copilot <auth@info.kirex.online>"


def send_2fa_verification_code(
    to_email: str,
    code: str,
    username: str = "",
    verify_url: str = "",
    logo_url: str = "",
) -> Tuple[bool, str]:
    """
    Sends a 6-digit 2FA verification code to the recipient using the Resend API.
    Supports mobile-responsive layout, quick-copy code, direct one-click verification,
    and a clear security notice to ignore the email if not the intended recipient.
    
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

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or "https://close.kirex.online/static/core/logos/android-chrome-192x192.png"

    # Optional One-Click Verification Button HTML
    quick_verify_html = ""
    if verify_url:
        quick_verify_html = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 20px;">
          <tr>
            <td align="center">
              <a href="{verify_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#ffd100; color:#000000; font-weight:800; font-size:13px; text-decoration:none; padding:12px 28px; border-radius:6px; letter-spacing:0.5px; text-transform:uppercase; box-shadow:0 2px 8px rgba(255,209,0,0.3);">
                ⚡ Verify &amp; Complete Registration &rarr;
              </a>
            </td>
          </tr>
        </table>
        '''

    # High-Contrast Mobile-Responsive HTML Email Template with Uganda Flag Branding
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>CLOSE Copilot Verification Code: {code}</title>
  <style type="text/css">
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
    @media only screen and (max-width: 600px) {{
      .email-wrapper {{ width: 100% !important; padding: 12px 8px !important; }}
      .email-card {{ width: 100% !important; border-radius: 8px !important; }}
      .email-body {{ padding: 22px 16px !important; }}
      .otp-code-text {{ font-size: 30px !important; letter-spacing: 6px !important; }}
      .action-btn {{ display: block !important; width: 100% !important; text-align: center !important; box-sizing: border-box !important; padding: 14px 16px !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:#0b0d13; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#e1e4ea;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#0b0d13; width:100%;">
    <tr>
      <td align="center" style="padding:24px 12px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:540px; background-color:#141722; border-radius:12px; border:1px solid #282d3d; overflow:hidden; box-shadow:0 12px 36px rgba(0,0,0,0.55);" class="email-card">
          
          <!-- Uganda Flag 6-Stripe Ribbon -->
          <tr>
            <td style="height:6px; line-height:6px; font-size:0; padding:0;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="height:6px;">
                <tr>
                  <td width="16.66%" style="background-color:#000000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#ffd100; height:6px;"></td>
                  <td width="16.66%" style="background-color:#d90000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#000000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#ffd100; height:6px;"></td>
                  <td width="16.66%" style="background-color:#d90000; height:6px;"></td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Header with Official Logo -->
          <tr>
            <td align="center" style="padding:28px 24px 18px; border-bottom:1px solid #232838; background-color:#161a25;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 12px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="60" height="60" alt="CLOSE Copilot Logo" style="display:block; width:60px; height:60px; border-radius:12px; border:1px solid #282d3d; object-fit:contain; background-color:#0d0f16; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.5px;">
                CLOSE <span style="color:#ffd100;">COPILOT</span>
              </h1>
              <p style="margin:6px 0 0; font-size:11px; font-weight:700; color:#8b949e; letter-spacing:0.8px; text-transform:uppercase;">
                Focus on plate audit, inspection, and inventory &bull; Security Verification
              </p>
            </td>
          </tr>

          <!-- Body Content -->
          <tr>
            <td style="padding:28px 28px 24px;" class="email-body">
              <p style="margin:0 0 14px; font-size:15px; color:#f1f5f9; line-height:1.5;">
                Hello <strong>{username or 'Operator'}</strong>,
              </p>
              <p style="margin:0 0 22px; font-size:14px; color:#94a3b8; line-height:1.55;">
                You are completing registration or authenticating your access on <strong>CLOSE Copilot</strong>. Use the 6-digit confirmation code below to verify your email address:
              </p>

              <!-- OTP Code Display Card with One-Tap Copy Support -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 20px;">
                <tr>
                  <td align="center" style="padding:22px 16px; background-color:#080a10; border:2px solid #ffd100; border-radius:10px; box-shadow:0 0 20px rgba(255,209,0,0.15);">
                    <div style="font-family:'Courier New', Courier, monospace; font-size:36px; font-weight:900; letter-spacing:10px; color:#ffffff; text-shadow:0 0 10px rgba(255,209,0,0.45); user-select:all; -webkit-user-select:all; -moz-user-select:all; -ms-user-select:all; cursor:pointer;" class="otp-code-text" title="Tap or click to select and copy code">
                      {code}
                    </div>
                    <div style="margin-top:10px; display:inline-block; padding:3px 10px; background-color:rgba(255,209,0,0.12); border-radius:12px; font-size:11px; color:#ffd100; font-weight:800; text-transform:uppercase; letter-spacing:1px;">
                      ⏱️ Valid for 10 minutes
                    </div>
                    <!-- Quick Action Copy Helper -->
                    <div style="margin-top:12px; font-size:12px; color:#94a3b8; line-height:1.4;">
                      📋 <strong>Quick Action:</strong> Tap or double-click the code above to easily copy it to your clipboard.
                    </div>
                  </td>
                </tr>
              </table>

              {quick_verify_html}

              <!-- Security Notice: Ignore If Not Intended User -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:24px; background-color:#1c1316; border:1px solid #7f1d1d; border-radius:8px;">
                <tr>
                  <td style="padding:14px 16px;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td width="28" valign="top" style="font-size:20px; line-height:1; padding-right:10px;">
                          ⚠️
                        </td>
                        <td valign="top" style="font-size:12px; color:#cbd5e1; line-height:1.55;">
                          <strong style="color:#f87171; text-transform:uppercase; letter-spacing:0.5px; display:block; margin-bottom:4px;">
                            Not your request? Please ignore this email
                          </strong>
                          If you did not request this verification code or were not expecting this registration, please safely disregard and <strong>ignore this email</strong>. No account has been activated, no action is required on your part, and your email address remains completely secure.
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding:18px 24px; background-color:#0d0f16; border-top:1px solid #232838; font-size:11px; color:#64748b; line-height:1.5;">
              CLOSE Copilot &bull; Plate Audit, Inspection &amp; Inventory Platform &bull; Kampala, Uganda<br>
              <span style="color:#475569;">This is an automated operational transmission. Please do not reply directly to this email.</span>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_verify_url = f"Direct Verification Link: {verify_url}\n" if verify_url else ""
    text_content = (
        f"CLOSE COPILOT - SECURITY CODE\n\n"
        f"Hello {username or 'Operator'},\n\n"
        f"Your 6-digit operator verification code is: {code}\n\n"
        f"This code is valid for 10 minutes.\n\n"
        f"Quick Actions:\n"
        f"- Copy the 6-digit code above and paste it into the verification prompt.\n"
        f"{text_verify_url}\n"
        f"NOT YOUR REQUEST?\n\n"
        f"If you did not request this verification code or were not expecting this email, please safely disregard and ignore this message. No account has been activated, no changes have been made, and your email remains completely secure.\n\n"
        f"CLOSE Copilot - Plate Audit, Inspection & Inventory - Kampala, Uganda\n"
    )

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": f"🛡️ ITMS Verification Security Code: {code}",
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
    logo_url: str = "",
) -> Tuple[bool, str, list[str]]:
    """
    Sends the official Daily Stock Reconciliation Report email to specified recipients.
    Formats the report table (Opening, Received, Scheduled, Installed, Performance %,
    Transfers, Backlog, Closing Balance) with Uganda-branded responsive HTML styling
    and confidentiality / ignore disclaimers.
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

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or "https://close.kirex.online/static/core/logos/android-chrome-192x192.png"
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
        "CLOSE COPILOT - DAILY STOCK RECONCILIATION REPORT",
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
    text_lines.append("NOT INTENDED RECIPIENT?")
    text_lines.append("This reconciliation report is confidential and intended solely for authorized depot operations.")
    text_lines.append("If received in error, please disregard and notify operations@itms.ug immediately.")
    text_lines.append("CLOSE Copilot - Plate Audit, Inspection & Inventory - Kampala, Uganda")
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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>Stock Reconciliation Report - {bond_name}</title>
  <style type="text/css">
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    @media only screen and (max-width: 600px) {{
      .email-wrapper {{ width: 100% !important; padding: 10px 6px !important; }}
      .email-card {{ width: 100% !important; border-radius: 8px !important; }}
      .report-header-flex {{ flex-direction: column !important; align-items: flex-start !important; }}
      .table-responsive {{ overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:#0b0d13; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#e1e4ea;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#0b0d13; width:100%;">
    <tr>
      <td align="center" style="padding:20px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:680px; background-color:#141720; border-radius:12px; border:1px solid #282d3d; overflow:hidden; box-shadow:0 16px 40px rgba(0,0,0,0.6);" class="email-card">
          
          <!-- Uganda National Flag Ribbon -->
          <tr>
            <td style="height:6px; line-height:6px; font-size:0; padding:0;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="height:6px;">
                <tr>
                  <td width="16.66%" style="background-color:#000000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#ffd100; height:6px;"></td>
                  <td width="16.66%" style="background-color:#d90000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#000000; height:6px;"></td>
                  <td width="16.66%" style="background-color:#ffd100; height:6px;"></td>
                  <td width="16.66%" style="background-color:#d90000; height:6px;"></td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Header with Official Logo -->
          <tr>
            <td style="padding:24px 28px 18px; border-bottom:1px solid #232838; background-color:#161a24;">
              <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;" class="report-header-flex">
                <div style="display:flex; align-items:center; gap:14px;">
                  <img src="{logo_src}" width="48" height="48" alt="CLOSE Copilot Logo" style="display:block; width:48px; height:48px; border-radius:10px; border:1px solid #282d3d; object-fit:contain; background-color:#0d0f16;" />
                  <div>
                    <div style="font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
                      CLOSE Copilot &bull; Daily Shift Ledger
                    </div>
                    <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.3px;">
                      {bond_name}
                    </h1>
                  </div>
                </div>
                  <div style="margin-top:4px; font-size:13px; color:#8b949e;">
                    Official Stock Reconciliation &bull; Shift #{suffix}
                  </div>
                </div>
                <div style="text-align:right; background-color:#0d0f16; border:1px solid #282d3d; border-radius:8px; padding:10px 16px;">
                  <div style="font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px;">Report Date</div>
                  <div style="font-size:15px; font-weight:800; color:#ffd100;">{report_date}</div>
                </div>
              </div>
            </td>
          </tr>

          <!-- Table Section -->
          <tr>
            <td style="padding:24px 24px 20px;">
              <div style="width:100%; overflow-x:auto;" class="table-responsive">
                <table role="presentation" style="width:100%; border-collapse:collapse; margin-bottom:16px; min-width:480px;">
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
              </div>

              {remarks_html}

              <!-- KPI Summary Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:20px; background-color:#0d0f16; border-radius:8px; border:1px solid #232838;">
                <tr>
                  <td style="padding:16px;">
                    <div style="font-size:11px; color:#8b949e; text-transform:uppercase;">Closing Stock Balance</div>
                    <div style="font-size:22px; font-weight:900; color:#ffd100; font-family:monospace; margin-top:2px;">{closing_balance} units</div>
                  </td>
                  <td align="right" style="padding:16px;">
                    <div style="font-size:11px; color:#8b949e; text-transform:uppercase;">Report Dispatcher</div>
                    <div style="font-size:13px; font-weight:700; color:#e1e4ea; margin-top:4px;">👤 {operator_name}</div>
                  </td>
                </tr>
              </table>

              <!-- Confidentiality & Intended Recipient Disclaimer -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:22px; background-color:#1c1316; border:1px solid #7f1d1d; border-radius:8px;">
                <tr>
                  <td style="padding:12px 16px;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td width="26" valign="top" style="font-size:18px; line-height:1; padding-right:10px;">🔒</td>
                        <td valign="top" style="font-size:11px; color:#cbd5e1; line-height:1.5;">
                          <strong style="color:#f87171; text-transform:uppercase; letter-spacing:0.5px;">Confidential Operational Report:</strong>
                          This reconciliation document is strictly intended for designated ITMS depot supervisors and authorized Ministry of Works and Transport personnel. If you received this report in error, please immediately disregard and delete this message.
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding:16px 24px; background-color:#0d0f16; border-top:1px solid #232838; font-size:11px; color:#6e7681;">
              CLOSE Copilot &bull; Plate Audit, Inspection &amp; Inventory Platform &bull; Kampala, Uganda
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
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
