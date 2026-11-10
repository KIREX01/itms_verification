"""
Email Service for ITMS Verification Copilot.

Provides:
- 2FA Email verification via Resend REST API (https://api.resend.com/emails)
- Secure 6-digit OTP code generation using cryptographic randomness
- Offline / Local Development fallback (logs OTP to server console if no key configured)
- Mobile-responsive, executive dark-mode transactional HTML email templates
  (Executive Stock Reconciliation Report, 2FA Verification, Admin Access Alert, Access Activated)
- Uganda national flag branding and bulletproof cross-client email rendering
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Tuple

import requests
from django.conf import settings
from django.utils import timezone

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
    """
    Retrieves sender email address from Django settings or environment.
    Ensures RFC 5322 display name formatting (e.g. 'CLOSE Copilot <notifications@info.kirex.online>')
    so email clients display the professional brand name instead of a raw username.
    """
    raw_from = getattr(settings, "RESEND_FROM_EMAIL", "") or os.environ.get("RESEND_FROM_EMAIL", "")
    raw_from = raw_from.strip()
    if not raw_from:
        return "CLOSE Copilot <notifications@info.kirex.online>"
    # Upgrade legacy 'auth@info.kirex.online' to standard notifications mailbox
    if "auth@info.kirex.online" in raw_from:
        raw_from = raw_from.replace("auth@info.kirex.online", "notifications@info.kirex.online")
    # If already formatted with display name and angle brackets, preserve it
    if "<" in raw_from and ">" in raw_from:
        return raw_from
    # If raw email address provided (e.g. notifications@info.kirex.online), wrap with brand display name
    return f"CLOSE Copilot <{raw_from}>"


# -----------------------------------------------------------------------------
# Modular Corporate Dark-Mode HTML Email Components
# -----------------------------------------------------------------------------

def _render_uganda_ribbon(height: int = 5) -> str:
    """Renders the official Uganda 6-stripe national flag ribbon for email headers."""
    return f"""
    <tr>
      <td style="height:{height}px; line-height:{height}px; font-size:0; padding:0; mso-line-height-rule:exactly;">
        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="height:{height}px; width:100%; border-collapse:collapse;">
          <tr>
            <td width="16.66%" style="background-color:#000000; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
            <td width="16.66%" style="background-color:#ffd100; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
            <td width="16.66%" style="background-color:#d90000; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
            <td width="16.66%" style="background-color:#000000; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
            <td width="16.66%" style="background-color:#ffd100; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
            <td width="16.66%" style="background-color:#d90000; height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _render_email_head(title: str) -> str:
    """Renders bulletproof XHTML transitional head with dark mode tokens and mobile media queries."""
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" style="color-scheme: dark; supported-color-schemes: dark;">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="dark light">
  <meta name="supported-color-schemes" content="dark light">
  <title>{title}</title>
  <style type="text/css">
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
    @media only screen and (max-width: 620px) {{
      .email-wrapper {{ width: 100% !important; padding: 10px 4px !important; }}
      .email-card {{ width: 100% !important; border-radius: 8px !important; }}
      .kpi-col {{ display: block !important; width: 100% !important; max-width: 100% !important; box-sizing: border-box !important; margin-bottom: 8px !important; }}
      .header-stack {{ display: block !important; width: 100% !important; text-align: left !important; margin-top: 10px !important; }}
      .table-responsive {{ overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; width: 100% !important; display: block !important; }}
      .data-table {{ min-width: 440px !important; }}
      .action-btn {{ display: block !important; width: 100% !important; text-align: center !important; box-sizing: border-box !important; padding: 14px 16px !important; }}
      .otp-digit {{ font-size: 28px !important; letter-spacing: 6px !important; }}
      .mobile-hide {{ display: none !important; }}
      .mobile-meta-card {{ display: block !important; width: 100% !important; text-align: left !important; margin-top: 12px !important; }}
    }}
  </style>
</head>
"""


def _render_email_footer(extra_disclaimer: str = "") -> str:
    """Renders the standard enterprise dark mode email footer with security and jurisdiction attribution."""
    disclaimer_html = ""
    if extra_disclaimer:
        disclaimer_html = f'<div style="font-size:10px; color:#475569; margin-top:6px; line-height:1.4;">{extra_disclaimer}</div>'
    return f"""
    <tr>
      <td align="center" style="padding:24px 20px; background-color:#0b0e17; border-top:1px solid #1f293d; font-size:11px; color:#64748b; line-height:1.6;">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
          <tr>
            <td align="center">
              <div style="font-weight:700; color:#94a3b8; letter-spacing:0.4px; margin-bottom:4px; font-size:11px; text-transform:uppercase;">
                CLOSE COPILOT &bull; PLATE AUDIT, INSPECTION &amp; INVENTORY
              </div>
              <div style="color:#64748b; margin-bottom:6px; font-size:11px;">
                Intelligent Transport Monitoring System &bull; Kampala, Uganda
              </div>
              <div style="font-size:10px; color:#475569;">
                Automated operational transmission from verified depot nodes. Do not reply directly to this email.
              </div>
              {disclaimer_html}
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


# -----------------------------------------------------------------------------
# 1. 2FA Verification Code Email
# -----------------------------------------------------------------------------

def send_2fa_verification_code(
    to_email: str,
    code: str,
    username: str = "",
    verify_url: str = "",
    logo_url: str = "",
) -> Tuple[bool, str]:
    """
    Sends a 6-digit 2FA verification code to the recipient using the Resend API.
    Features Uganda flag ribbon, high-contrast dark card, one-tap copyable OTP digits,
    optional one-click verification CTA, and clear security ignore disclaimers.
    
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
              <a href="{verify_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#ffd100; color:#000000; font-weight:800; font-size:13px; text-decoration:none; padding:13px 30px; border-radius:6px; letter-spacing:0.5px; text-transform:uppercase; box-shadow:0 3px 12px rgba(255,209,0,0.35);">
                ⚡ Verify &amp; Complete Registration &rarr;
              </a>
            </td>
          </tr>
        </table>
        '''

    head_html = _render_email_head(f"CLOSE Copilot Security Code: {code}")
    ribbon_html = _render_uganda_ribbon(5)
    footer_html = _render_email_footer("If you did not request this verification, please safely disregard this email.")

    html_content = f"""{head_html}
<body style="margin:0; padding:0; background-color:#080b11; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#f1f5f9;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#080b11; width:100%;">
    <tr>
      <td align="center" style="padding:24px 12px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:540px; background-color:#111622; border-radius:12px; border:1px solid #1f293d; overflow:hidden; box-shadow:0 16px 48px rgba(0,0,0,0.6);" class="email-card">
          
          {ribbon_html}

          <!-- Header with Brand Logo -->
          <tr>
            <td align="center" style="padding:28px 24px 20px; border-bottom:1px solid #1f293d; background-color:#151b28;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 12px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="56" height="56" alt="CLOSE Copilot Logo" style="display:block; width:56px; height:56px; border-radius:12px; border:1px solid #28354d; object-fit:contain; background-color:#090d16; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.5px;">
                CLOSE <span style="color:#ffd100;">COPILOT</span>
              </h1>
              <p style="margin:6px 0 0; font-size:11px; font-weight:700; color:#94a3b8; letter-spacing:0.8px; text-transform:uppercase;">
                Plate Audit, Inspection &amp; Inventory &bull; Security Verification
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

              <!-- OTP Code Display Card with High Contrast -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 20px;">
                <tr>
                  <td align="center" style="padding:24px 16px; background-color:#070a10; border:2px solid #ffd100; border-radius:10px; box-shadow:0 0 24px rgba(255,209,0,0.18);">
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:38px; font-weight:900; letter-spacing:12px; color:#ffd100; text-shadow:0 0 12px rgba(255,209,0,0.45); user-select:all; -webkit-user-select:all; cursor:pointer;" class="otp-code-text" title="Tap or click to select and copy code">
                      {code}
                    </div>
                    <div style="margin-top:12px; display:inline-block; padding:4px 12px; background-color:rgba(255,209,0,0.12); border-radius:12px; font-size:11px; color:#ffd100; font-weight:800; text-transform:uppercase; letter-spacing:1px; border:1px solid rgba(255,209,0,0.25);">
                      ⏱️ Valid for 10 minutes
                    </div>
                    <!-- Quick Action Copy Helper -->
                    <div style="margin-top:14px; font-size:12px; color:#94a3b8; line-height:1.4;">
                      📋 <strong>Quick Action:</strong> Tap or double-click the code above to easily copy it to your clipboard.
                    </div>
                  </td>
                </tr>
              </table>

              {quick_verify_html}

              <!-- Security Notice: Ignore If Not Intended User -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:24px; background-color:#1c1215; border:1px solid #7f1d1d; border-radius:8px;">
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

          {footer_html}

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


# -----------------------------------------------------------------------------
# 2. Executive Daily Stock Reconciliation Report Email
# -----------------------------------------------------------------------------

def send_stock_reconciliation_report_email(
    recon_data: dict,
    recipient_emails: list[str],
    operator_name: str = "Operator",
    notes: str = "",
    logo_url: str = "",
) -> Tuple[bool, str, list[str]]:
    """
    Sends the official Daily Stock Reconciliation Report email to specified recipients.
    Features:
    - Executive KPI Stat Grid (Opening Stock, Deliveries, Installed, Closing Balance)
    - Standard 9-Row Balance Sheet table with alternating dark rows and high-contrast numbers
    - Floor Movement & Returns verification breakdown (Dispatched, Installed, Returned, Discrepancies)
    - Shift Handover Remarks callout
    - Dispatcher Attribution & Security / Confidentiality notice
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
    closing_psv = recon_data.get("closing_balance_psv", 0)
    closing_pmo = recon_data.get("closing_balance_pmo", 0)
    opening_stock = recon_data.get("opening_stock", 0)
    opening_psv = recon_data.get("opening_balance_psv", 0)
    opening_pmo = recon_data.get("opening_balance_pmo", 0)
    delivered_count = recon_data.get("kits_received_psv", 0) + recon_data.get("kits_received_pmo", 0)
    scheduled_total = recon_data.get("scheduled_total", 0)
    installed_count = recon_data.get("installed_count", 0)
    returned_count = recon_data.get("returned_count", 0)
    dispatched_count = recon_data.get("dispatched", 0) or recon_data.get("dispatched_count", 0)
    unallocated_count = (
        len(recon_data.get("unallocated_plates", []))
        if isinstance(recon_data.get("unallocated_plates"), list)
        else (recon_data.get("unallocated_count", 0) or 0)
    )
    remarks = notes or recon_data.get("remarks") or ""
    perf_pct = round((installed_count / scheduled_total * 100), 1) if scheduled_total > 0 else 0.0

    rows = recon_data.get("report_table", {}).get("rows", [])

    # Build High-Contrast HTML table rows
    table_rows_html = []
    for idx, r in enumerate(rows):
        metric = r.get("metric", "")
        pmo = r.get("pmo", 0)
        psv = r.get("psv", 0)
        tot = r.get("total", 0)

        is_closing = "closing" in metric.lower()
        is_perf = "performance" in metric.lower() or "%" in metric

        row_bg = "#151b27" if (idx % 2 == 1) else "#111621"
        border_style = "border-bottom:1px solid #1f293d;"
        text_color = "#f1f5f9"
        font_weight = "600"

        if is_closing:
            row_bg = "#1a2233"
            border_style = "border-top:2px solid #ffd100; border-bottom:2px solid #ffd100;"
            text_color = "#ffd100"
            font_weight = "800"

        if is_perf:
            tot_str = str(tot).replace("%", "").strip()
            try:
                tot_val = float(tot_str)
                badge_bg = "#10b981" if tot_val >= 100 else ("#f59e0b" if tot_val >= 80 else "#ef4444")
            except Exception:
                badge_bg = "#ffd100"
            tot_cell = f"<span style='background-color:{badge_bg}; color:#000000; padding:3px 10px; border-radius:12px; font-weight:800; font-size:12px; display:inline-block; letter-spacing:0.3px;'>{tot}</span>"
        elif is_closing:
            tot_cell = f"<span style='font-family:monospace; font-size:16px; font-weight:900; color:#ffd100; text-shadow:0 0 10px rgba(255,209,0,0.3);'>{tot}</span>"
        else:
            tot_cell = f"<span style='font-family:monospace; font-size:13px; font-weight:{font_weight}; color:{text_color};'>{tot}</span>"

        pmo_cell = f"<span style='font-family:monospace; font-size:13px; color:{'#ffd100' if is_closing else '#94a3b8'};'>{pmo}</span>"
        psv_cell = f"<span style='font-family:monospace; font-size:13px; color:{'#ffd100' if is_closing else '#cbd5e1'};'>{psv}</span>"

        icon = "🏁 " if is_closing else ("⚡ " if is_perf else ("📋 " if "opening" in metric.lower() else ("📦 " if "received" in metric.lower() else "• ")))

        table_rows_html.append(f'''
        <tr style="background-color:{row_bg}; {border_style}">
          <td style="padding:11px 14px; font-weight:{font_weight}; color:{text_color}; font-size:13px;">{icon}{metric}</td>
          <td style="padding:11px 14px; text-align:right;">{pmo_cell}</td>
          <td style="padding:11px 14px; text-align:right;">{psv_cell}</td>
          <td style="padding:11px 14px; text-align:right;">{tot_cell}</td>
        </tr>
        ''')

    table_body = "\n".join(table_rows_html)

    # Build ASCII text fallback representation
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
    text_lines.append(f"Opening: {opening_stock} (PSV {opening_psv}, PMO {opening_pmo}) | Deliveries: +{delivered_count} | Installed: -{installed_count} | Closing: {closing_balance}")
    text_lines.append(f"Floor Operations: Dispatched {dispatched_count} | Returned {returned_count} | Discrepancy {unallocated_count}")
    if remarks:
        text_lines.append("-" * 72)
        text_lines.append(f"Remarks: {remarks}")
    text_lines.append("-" * 72)
    text_lines.append("CONFIDENTIAL OPERATIONAL REPORT")
    text_lines.append("This document is intended solely for authorized depot operations and the Ministry of Works.")
    text_lines.append("CLOSE Copilot - Plate Audit, Inspection & Inventory - Kampala, Uganda")
    text_content = "\n".join(text_lines)

    remarks_html = ""
    if remarks:
        remarks_html = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:22px 0 10px; background-color:#141a27; border-left:4px solid #ffd100; border-radius:6px; border:1px solid #1f293d;">
          <tr>
            <td style="padding:14px 18px;">
              <div style="font-size:11px; text-transform:uppercase; color:#ffd100; font-weight:800; letter-spacing:0.6px; margin-bottom:4px;">
                📝 Shift Handover Remarks &amp; Operational Notes
              </div>
              <div style="font-size:13px; color:#cbd5e1; line-height:1.55;">
                {remarks}
              </div>
            </td>
          </tr>
        </table>
        '''

    floor_status_badge = (
        '<span style="background-color:rgba(16,185,129,0.15); color:#10b981; font-weight:800; font-size:11px; padding:3px 10px; border-radius:12px; border:1px solid rgba(16,185,129,0.3);">🟢 Balanced Floor Operations</span>'
        if unallocated_count == 0
        else f'<span style="background-color:rgba(239,68,68,0.15); color:#ef4444; font-weight:800; font-size:11px; padding:3px 10px; border-radius:12px; border:1px solid rgba(239,68,68,0.3);">🔴 {unallocated_count} Unallocated Kits</span>'
    )

    head_html = _render_email_head(f"Stock Reconciliation Report - {bond_name} - {report_date}")
    ribbon_html = _render_uganda_ribbon(5)
    footer_html = _render_email_footer("This official report is dispatched directly from the verified depot verification copilot.")

    html_content = f"""{head_html}
<body style="margin:0; padding:0; background-color:#080b11; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#f1f5f9;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#080b11; width:100%;">
    <tr>
      <td align="center" style="padding:22px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:680px; background-color:#111622; border-radius:12px; border:1px solid #1f293d; overflow:hidden; box-shadow:0 18px 48px rgba(0,0,0,0.65);" class="email-card">
          
          {ribbon_html}

          <!-- Header with Company Logo & Facility Context -->
          <tr>
            <td style="padding:24px 28px 20px; border-bottom:1px solid #1f293d; background-color:#151b28;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td valign="top">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0">
                      <tr>
                        <td valign="middle" style="padding-right:14px;">
                          <img src="{logo_src}" width="52" height="52" alt="CLOSE Copilot Logo" style="display:block; width:52px; height:52px; border-radius:10px; border:1px solid #28354d; object-fit:contain; background-color:#090d16;" />
                        </td>
                        <td valign="middle">
                          <div style="font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:1px; margin-bottom:3px;">
                            CLOSE COPILOT &bull; DAILY SHIFT LEDGER
                          </div>
                          <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.3px; line-height:1.2;">
                            {bond_name}
                          </h1>
                          <div style="margin-top:4px; font-size:12px; color:#94a3b8;">
                            Official Stock Reconciliation &bull; Shift #{suffix}
                          </div>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td align="right" valign="top" class="mobile-meta-card">
                    <div style="display:inline-block; background-color:#090d16; border:1px solid #28354d; border-radius:8px; padding:10px 16px; text-align:right;">
                      <div style="font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px; font-weight:700;">Report Date</div>
                      <div style="font-size:15px; font-weight:800; color:#ffd100; margin-top:2px;">{report_date}</div>
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Main Content Area -->
          <tr>
            <td style="padding:24px 24px 20px;">
              
              <!-- Executive Summary Stat Grid (4 KPI Cards) -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-bottom:22px;">
                <tr>
                  <!-- Card 1: Opening Balance -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#141b29; border:1px solid #232f45; border-top:3px solid #38bdf8; border-radius:8px; padding:14px 10px; text-align:center;">
                    <div style="font-size:10px; font-weight:800; color:#8b949e; text-transform:uppercase; letter-spacing:0.6px;">Opening Balance</div>
                    <div style="font-size:22px; font-weight:900; color:#38bdf8; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{opening_stock:,}</div>
                    <div style="font-size:10px; color:#64748b;">PSV {opening_psv:,} &bull; PMO {opening_pmo:,}</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 2: Inbound Deliveries -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#141b29; border:1px solid #232f45; border-top:3px solid #10b981; border-radius:8px; padding:14px 10px; text-align:center;">
                    <div style="font-size:10px; font-weight:800; color:#8b949e; text-transform:uppercase; letter-spacing:0.6px;">Inbound Received</div>
                    <div style="font-size:22px; font-weight:900; color:#10b981; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">+{delivered_count:,}</div>
                    <div style="font-size:10px; color:#64748b;">Verified Deliveries</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 3: Kits Installed -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#141b29; border:1px solid #232f45; border-top:3px solid #ffd100; border-radius:8px; padding:14px 10px; text-align:center;">
                    <div style="font-size:10px; font-weight:800; color:#8b949e; text-transform:uppercase; letter-spacing:0.6px;">Kits Installed</div>
                    <div style="font-size:22px; font-weight:900; color:#ffd100; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{installed_count:,}</div>
                    <div style="font-size:10px; color:#64748b;">Target {scheduled_total:,} ({perf_pct:.0f}%)</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 4: Closing Balance -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#141b29; border:1px solid #232f45; border-top:3px solid #f59e0b; border-radius:8px; padding:14px 10px; text-align:center;">
                    <div style="font-size:10px; font-weight:800; color:#8b949e; text-transform:uppercase; letter-spacing:0.6px;">Closing Balance</div>
                    <div style="font-size:22px; font-weight:900; color:#f59e0b; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{closing_balance:,}</div>
                    <div style="font-size:10px; color:#64748b;">Active Safe Stock</div>
                  </td>
                </tr>
              </table>

              <!-- Official 9-Row Balance Sheet Table -->
              <div style="width:100%; overflow-x:auto;" class="table-responsive">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" class="data-table" style="width:100%; border-collapse:collapse; border:1px solid #1f293d; border-radius:8px; overflow:hidden; min-width:440px;">
                  <thead>
                    <tr style="background-color:#0b0f19; border-bottom:2px solid #ffd100;">
                      <th style="padding:12px 14px; text-align:left; font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:0.8px;">Description</th>
                      <th style="padding:12px 14px; text-align:right; font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:0.8px;">Private (PMO)</th>
                      <th style="padding:12px 14px; text-align:right; font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:0.8px;">Public (PSV)</th>
                      <th style="padding:12px 14px; text-align:right; font-size:11px; font-weight:900; color:#ffffff; text-transform:uppercase; letter-spacing:0.8px;">Total Combined</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table_body}
                  </tbody>
                </table>
              </div>

              <!-- Floor Movements & Returns Breakdown Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="background-color:#131926; border:1px solid #1f293d; border-radius:8px; margin-top:22px; overflow:hidden;">
                <tr>
                  <td style="padding:12px 16px; border-bottom:1px solid #1b2333; background-color:#161d2c;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td style="font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:0.8px;">
                          Floor Movement &amp; Return Verification
                        </td>
                        <td align="right">
                          {floor_status_badge}
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:16px 16px;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td width="25%" align="center" style="padding:6px;">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase;">Dispatched</div>
                          <div style="font-size:17px; font-weight:800; color:#f1f5f9; font-family:'SFMono-Regular',Consolas,monospace; margin-top:3px;">{dispatched_count:,}</div>
                        </td>
                        <td width="25%" align="center" style="padding:6px; border-left:1px solid #1f293d;">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase;">Installed</div>
                          <div style="font-size:17px; font-weight:800; color:#10b981; font-family:'SFMono-Regular',Consolas,monospace; margin-top:3px;">{installed_count:,}</div>
                        </td>
                        <td width="25%" align="center" style="padding:6px; border-left:1px solid #1f293d;">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase;">Returned to Safe</div>
                          <div style="font-size:17px; font-weight:800; color:#38bdf8; font-family:'SFMono-Regular',Consolas,monospace; margin-top:3px;">{returned_count:,}</div>
                        </td>
                        <td width="25%" align="center" style="padding:6px; border-left:1px solid #1f293d;">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase;">Discrepancy</div>
                          <div style="font-size:17px; font-weight:800; color:{'#10b981' if unallocated_count == 0 else '#ef4444'}; font-family:'SFMono-Regular',Consolas,monospace; margin-top:3px;">{unallocated_count:,}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              {remarks_html}

              <!-- Dispatcher Attribution & System Node Info -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:20px; background-color:#121723; border-radius:8px; border:1px solid #1f293d;">
                <tr>
                  <td style="padding:14px 18px;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td valign="middle">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px;">Shift Dispatch Sign-off</div>
                          <div style="font-size:13px; font-weight:700; color:#f1f5f9; margin-top:3px;">
                            👤 {operator_name} &bull; <span style="color:#ffd100; font-weight:600;">Authorized Depot Officer</span>
                          </div>
                        </td>
                        <td align="right" valign="middle" class="header-stack">
                          <div style="font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px;">System &bull; Shift Node</div>
                          <div style="font-size:11px; font-family:'SFMono-Regular',Consolas,monospace; color:#64748b; margin-top:3px;">
                            ITMS-COPILOT v2.4 &bull; #{suffix}
                          </div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- Confidentiality & Intended Recipient Disclaimer -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-top:22px; background-color:#1a1114; border:1px solid #7f1d1d; border-radius:8px;">
                <tr>
                  <td style="padding:14px 18px;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
                      <tr>
                        <td width="28" valign="top" style="font-size:20px; line-height:1; padding-right:12px;">🔒</td>
                        <td valign="top" style="font-size:11px; color:#cbd5e1; line-height:1.55;">
                          <strong style="color:#f87171; text-transform:uppercase; letter-spacing:0.5px; display:block; margin-bottom:2px;">
                            Confidential Operational Report:
                          </strong>
                          This reconciliation document is strictly intended for designated ITMS depot supervisors and authorized Ministry of Works and Transport personnel. If you received this report in error, please immediately disregard and delete this message.
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          {footer_html}

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

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


# -----------------------------------------------------------------------------
# 3. Security Access Request Admin Alert Email
# -----------------------------------------------------------------------------

def send_access_request_admin_alert(
    admin_emails: list[str],
    requester_email: str,
    requester_name: str = "",
    client_ip: str = "",
    mac_address: str = "",
    device_name: str = "",
    activation_code: str = "",
    approve_url: str = "",
    logo_url: str = "",
) -> Tuple[bool, str]:
    """
    Sends an alert email to administrator(s) when a new operator email or unrecognized device
    requests access activation. Features dark-mode layout, Uganda flag ribbon, structured
    metadata card, prominent 6-digit activation code, and 1-click approval CTA.
    """
    clean_admins = [str(e).strip() for e in (admin_emails or []) if e and "@" in str(e)]
    if not clean_admins:
        return False, "No administrator email configured."

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()
    now_str = timezone.now().strftime("%d %b %Y, %H:%M:%S")

    text_content = (
        f"CLOSE COPILOT - DEVICE & EMAIL ACCESS REQUEST\n\n"
        f"Requester Email: {requester_email}\n"
        f"Name / Username: {requester_name or 'Operator'}\n"
        f"Device Name:     {device_name or 'Unknown'}\n"
        f"Client IP:       {client_ip or 'N/A'}\n"
        f"MAC Address:     {mac_address or 'N/A (Cloud/Remote)'}\n"
        f"Activation Code: {activation_code}\n"
        f"Approve Link:    {approve_url}\n"
        f"Time:            {now_str} EAT\n"
    )

    if not api_key:
        logger.warning(
            "[ACCESS REQUEST DEV FALLBACK] Simulated admin alert for %s (Code: %s)",
            _sanitize_log_str(requester_email),
            _sanitize_log_str(activation_code),
        )
        return True, "Simulated admin alert logged."

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or "https://close.kirex.online/static/core/logos/android-chrome-192x192.png"

    approve_btn = ""
    if approve_url:
        approve_btn = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:20px 0 10px;">
          <tr>
            <td align="center">
              <a href="{approve_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#ffd100; color:#000000; font-weight:800; font-size:13px; text-decoration:none; padding:13px 28px; border-radius:6px; letter-spacing:0.5px; text-transform:uppercase; box-shadow:0 3px 12px rgba(255,209,0,0.35);">
                ✅ Approve Email &amp; Authorize Device &rarr;
              </a>
            </td>
          </tr>
        </table>
        '''

    head_html = _render_email_head(f"Security Alert: Access Request from {requester_email}")
    ribbon_html = _render_uganda_ribbon(5)
    footer_html = _render_email_footer("Security notification intended strictly for authorized system administrators.")

    html_content = f"""{head_html}
<body style="margin:0; padding:0; background-color:#080b11; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#f1f5f9;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#080b11; width:100%;">
    <tr>
      <td align="center" style="padding:24px 12px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:540px; background-color:#111622; border-radius:12px; border:1px solid #1f293d; overflow:hidden; box-shadow:0 16px 48px rgba(0,0,0,0.6);" class="email-card">
          
          {ribbon_html}

          <!-- Header -->
          <tr>
            <td align="center" style="padding:26px 24px 18px; border-bottom:1px solid #1f293d; background-color:#151b28;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 10px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="52" height="52" alt="CLOSE Copilot Logo" style="display:block; width:52px; height:52px; border-radius:10px; border:1px solid #28354d; object-fit:contain; background-color:#090d16; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <div style="font-size:11px; font-weight:800; color:#ef4444; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
                🚨 High-Priority Security Alert
              </div>
              <h1 style="margin:0; font-size:19px; font-weight:800; color:#ffffff; letter-spacing:0.3px;">
                Access Activation Request
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:26px 24px 22px;">
              <p style="margin:0 0 16px; font-size:14px; color:#cbd5e1; line-height:1.5;">
                An operator or unrecognized hardware device has requested access activation on <strong>CLOSE Copilot</strong>:
              </p>

              <!-- Metadata Table Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="background-color:#0c1018; border:1px solid #1f293d; border-radius:8px; margin-bottom:20px; overflow:hidden;">
                <tr>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:12px; color:#8b949e; width:35%;">Requester Email:</td>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:13px; font-weight:700; color:#ffffff;">{requester_email}</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:12px; color:#8b949e;">Operator Name:</td>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:13px; font-weight:700; color:#cbd5e1;">{requester_name or '—'}</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:12px; color:#8b949e;">Device Name:</td>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:13px; font-family:'SFMono-Regular',Consolas,monospace; color:#38bdf8;">{device_name or 'Unregistered Device'}</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:12px; color:#8b949e;">Client IP Address:</td>
                  <td style="padding:10px 14px; border-bottom:1px solid #1b2333; font-size:13px; font-family:'SFMono-Regular',Consolas,monospace; color:#ffd100;">{client_ip or '—'}</td>
                </tr>
                <tr>
                  <td style="padding:10px 14px; font-size:12px; color:#8b949e;">Hardware / MAC:</td>
                  <td style="padding:10px 14px; font-size:12px; font-family:'SFMono-Regular',Consolas,monospace; color:#94a3b8;">{mac_address or 'Cloud / Remote'}</td>
                </tr>
              </table>

              <!-- Activation Code Box -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-bottom:20px;">
                <tr>
                  <td align="center" style="padding:18px 16px; background-color:#080b12; border:2px dashed #ffd100; border-radius:10px;">
                    <div style="font-size:11px; text-transform:uppercase; color:#8b949e; letter-spacing:0.8px; margin-bottom:6px; font-weight:700;">
                      6-Digit Activation Code
                    </div>
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:32px; font-weight:900; letter-spacing:10px; color:#ffd100; text-shadow:0 0 10px rgba(255,209,0,0.35);">
                      {activation_code}
                    </div>
                    <div style="font-size:11px; color:#64748b; margin-top:6px;">
                      Share this code with the operator or click below to approve instantly.
                    </div>
                  </td>
                </tr>
              </table>

              {approve_btn}

              <div style="margin-top:16px; font-size:11px; color:#64748b; line-height:1.5; text-align:center;">
                ⚠️ Only approve requests from verified warehouse personnel operating under an authorized depot.
              </div>

            </td>
          </tr>

          {footer_html}

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json={
                "from": from_email,
                "to": clean_admins,
                "subject": f"🛡️ Access Request: {requester_email} ({device_name or client_ip})",
                "html": html_content,
                "text": text_content,
            },
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10,
        )
        return resp.status_code in (200, 201), "Admin alert sent."
    except Exception as exc:
        logger.warning("Failed sending admin access alert: %s", exc)
        return False, str(exc)


# -----------------------------------------------------------------------------
# 4. Operator Access Activated / Invitation Email
# -----------------------------------------------------------------------------

def send_access_activated_email(
    to_email: str,
    username: str = "Operator",
    activation_code: str = "",
    login_url: str = "",
    expires_at_display: str = "",
    is_invitation: bool = False,
    logo_url: str = "",
) -> Tuple[bool, str]:
    """
    Notifies an operator that their time-bound email access or invitation has been granted.
    Features Uganda flag branding, dark mode styling, activation code container,
    validity expiration badge, and 1-click portal sign-in button.
    """
    if not to_email or "@" not in to_email:
        return False, "Valid recipient email required."

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()
    target_url = login_url or "https://close.kirex.online/signup/"
    heading = "✉️ Operator Invitation — CLOSE Copilot" if is_invitation else "✅ Access Approved — CLOSE Copilot"
    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or "https://close.kirex.online/static/core/logos/android-chrome-192x192.png"

    exp_line_txt = f"Valid Until:     {expires_at_display}\n" if expires_at_display else ""
    text_content = (
        f"CLOSE COPILOT - TIME-BOUND ACCESS GRANTED\n\n"
        f"Hello {username},\n\n"
        f"Your email ({to_email}) has been granted time-bound access to CLOSE Copilot.\n"
        f"Activation Code: {activation_code}\n"
        f"{exp_line_txt}"
        f"Portal URL:      {target_url}\n"
    )

    if not api_key:
        logger.info(
            "[ACCESS ACTIVATED DEV FALLBACK] Granted %s (Code: %s, Expires: %s)",
            _sanitize_log_str(to_email),
            _sanitize_log_str(activation_code),
            _sanitize_log_str(expires_at_display or "N/A"),
        )
        return True, "Simulated invitation/activation email logged."

    exp_line_html = ""
    if expires_at_display:
        exp_line_html = f'''
        <div style="display:inline-block; margin-top:10px; padding:4px 12px; background-color:rgba(245,158,11,0.12); border-radius:12px; font-size:11px; color:#f59e0b; font-weight:800; border:1px solid rgba(245,158,11,0.25);">
          ⏳ Time-Bound Access Valid Until: {expires_at_display}
        </div>
        '''

    head_html = _render_email_head(heading)
    ribbon_html = _render_uganda_ribbon(5)
    footer_html = _render_email_footer("Please keep your activation credentials secure and do not share them.")

    html_content = f"""{head_html}
<body style="margin:0; padding:0; background-color:#080b11; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#f1f5f9;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#080b11; width:100%;">
    <tr>
      <td align="center" style="padding:24px 12px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:540px; background-color:#111622; border-radius:12px; border:1px solid #1f293d; overflow:hidden; box-shadow:0 16px 48px rgba(0,0,0,0.6);" class="email-card">
          
          {ribbon_html}

          <!-- Header -->
          <tr>
            <td align="center" style="padding:26px 24px 18px; border-bottom:1px solid #1f293d; background-color:#151b28;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 10px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="52" height="52" alt="CLOSE Copilot Logo" style="display:block; width:52px; height:52px; border-radius:10px; border:1px solid #28354d; object-fit:contain; background-color:#090d16; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <div style="font-size:11px; font-weight:800; color:#10b981; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
                Authorization Complete
              </div>
              <h1 style="margin:0; font-size:20px; font-weight:800; color:#ffffff; letter-spacing:0.3px;">
                {heading}
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:28px 26px 24px;">
              <p style="margin:0 0 14px; font-size:15px; color:#f1f5f9; line-height:1.5;">
                Hello <strong>{username}</strong>,
              </p>
              <p style="margin:0 0 20px; font-size:14px; color:#cbd5e1; line-height:1.55;">
                Your email address (<strong>{to_email}</strong>) has been authorized for operator access on <strong>CLOSE Copilot</strong>.
              </p>

              <!-- Activation Code Display Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 20px;">
                <tr>
                  <td align="center" style="padding:22px 16px; background-color:#080b12; border:2px solid #ffd100; border-radius:10px; box-shadow:0 0 20px rgba(255,209,0,0.15);">
                    <div style="font-size:11px; text-transform:uppercase; color:#8b949e; letter-spacing:0.8px; margin-bottom:6px; font-weight:700;">
                      Your 6-Digit Activation Code
                    </div>
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:36px; font-weight:900; letter-spacing:10px; color:#ffd100; text-shadow:0 0 10px rgba(255,209,0,0.35);">
                      {activation_code}
                    </div>
                    {exp_line_html}
                  </td>
                </tr>
              </table>

              <!-- One-Click Portal Sign-In CTA -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:20px 0 22px;">
                <tr>
                  <td align="center">
                    <a href="{target_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#ffd100; color:#000000; font-weight:800; font-size:13px; text-decoration:none; padding:13px 30px; border-radius:6px; letter-spacing:0.5px; text-transform:uppercase; box-shadow:0 3px 12px rgba(255,209,0,0.35);">
                      🚀 Open CLOSE Copilot &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <!-- 3-Step Quick Start Guide -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="background-color:#0c1018; border:1px solid #1f293d; border-radius:8px; padding:14px; margin-top:16px;">
                <tr>
                  <td>
                    <div style="font-size:11px; font-weight:800; color:#ffd100; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">
                      Quick Getting Started Steps:
                    </div>
                    <ol style="margin:0; padding-left:18px; font-size:12px; color:#94a3b8; line-height:1.7;">
                      <li>Click the button above to access the verification portal.</li>
                      <li>Enter your 6-digit activation code to bind your session.</li>
                      <li>In Tab 2, sign in to your official ITMS account to begin barcode and pair verification.</li>
                    </ol>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          {footer_html}

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json={
                "from": from_email,
                "to": [to_email],
                "subject": "✉️ CLOSE Copilot Access Invitation" if is_invitation else "✅ CLOSE Copilot Access Approved",
                "html": html_content,
                "text": text_content,
            },
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10,
        )
        return resp.status_code in (200, 201), "Email sent."
    except Exception as exc:
        logger.warning("Failed sending activation/invitation email: %s", exc)
        return False, str(exc)
