"""
Email Service for Copilot.

Provides:
- 2FA Email verification via Resend REST API (https://api.resend.com/emails)
- Secure 6-digit OTP code generation using cryptographic randomness
- Offline / Local Development fallback (logs OTP to server console if no key configured)
- Clean, high-contrast white-background email templates optimized for Outlook, Gmail, and Apple Mail
- Minimalist prototype layout: direct, concise, branded as Copilot only (no external agency or ministry claims)
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

DEFAULT_EMAIL_LOGO_URL = "https://close.kirex.online/static/core/logos/android-chrome-192x192.png"


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
    Retrieves sender email address formatted as 'CLOSE Copilot <email>'.
    Ensures clean CLOSE Copilot branding without external agency or government claims.
    """
    raw_from = getattr(settings, "RESEND_FROM_EMAIL", "") or os.environ.get("RESEND_FROM_EMAIL", "")
    raw_from = raw_from.strip()
    if not raw_from:
        return "CLOSE Copilot <notifications@info.kirex.online>"
    # Upgrade legacy addresses if present
    if "auth@info.kirex.online" in raw_from:
        raw_from = raw_from.replace("auth@info.kirex.online", "notifications@info.kirex.online")
    # If already formatted with brackets, extract the email part and enforce "CLOSE Copilot" name
    if "<" in raw_from and ">" in raw_from:
        email_part = raw_from[raw_from.find("<") : raw_from.find(">") + 1]
        return f"CLOSE Copilot {email_part}"
    return f"CLOSE Copilot <{raw_from}>"


# -----------------------------------------------------------------------------
# Modular Clean White Email Components
# -----------------------------------------------------------------------------

def _render_accent_line(height: int = 3, color: str = "#0f172a") -> str:
    """Renders a clean, subtle top accent bar."""
    return f"""
    <tr>
      <td style="height:{height}px; line-height:{height}px; font-size:0; padding:0; background-color:{color};">&nbsp;</td>
    </tr>
    """


def _render_email_head(title: str) -> str:
    """
    Renders bulletproof XHTML transitional head optimized for Outlook/Gmail/Apple Mail
    with clean white background and high-contrast typography.
    """
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{title}</title>
  <style type="text/css">
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
    body {{ margin: 0; padding: 0; background-color: #ffffff; color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
    @media only screen and (max-width: 620px) {{
      .email-wrapper {{ width: 100% !important; padding: 8px 4px !important; }}
      .email-card {{ width: 100% !important; border-radius: 6px !important; border: 1px solid #e2e8f0 !important; }}
      .kpi-col {{ display: block !important; width: 100% !important; max-width: 100% !important; box-sizing: border-box !important; margin-bottom: 8px !important; }}
      .header-stack {{ display: block !important; width: 100% !important; text-align: left !important; margin-top: 8px !important; }}
      .table-responsive {{ overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; width: 100% !important; display: block !important; }}
      .data-table {{ min-width: 380px !important; }}
      .action-btn {{ display: block !important; width: 100% !important; text-align: center !important; box-sizing: border-box !important; padding: 12px 16px !important; }}
      .otp-digit {{ font-size: 28px !important; letter-spacing: 6px !important; }}
      .mobile-hide {{ display: none !important; }}
      .mobile-meta-card {{ display: block !important; width: 100% !important; text-align: left !important; margin-top: 10px !important; }}
    }}
  </style>
</head>
"""


def _render_email_footer(extra_disclaimer: str = "") -> str:
    """Renders clean, minimal prototype footer without government or external agency references."""
    disclaimer_html = ""
    if extra_disclaimer:
        disclaimer_html = f'<div style="font-size:11px; color:#64748b; margin-top:4px; line-height:1.4;">{extra_disclaimer}</div>'
    return f"""
    <tr>
      <td align="center" style="padding:16px 20px; background-color:#f8fafc; border-top:1px solid #e2e8f0; font-size:11px; color:#64748b; line-height:1.5;">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%">
          <tr>
            <td align="center">
              <div style="font-weight:700; color:#334155; font-size:11px; text-transform:uppercase; letter-spacing:0.5px;">
                CLOSE Copilot
              </div>
              <div style="font-size:11px; color:#64748b; margin-top:2px;">
                Operational Shift Summary &bull; Automated Transmission
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
    Straight to the point: clean white card, clear OTP digits, optional 1-click verify.
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

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or DEFAULT_EMAIL_LOGO_URL

    quick_verify_html = ""
    if verify_url:
        quick_verify_html = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 12px;">
          <tr>
            <td align="center">
              <a href="{verify_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#0f172a; color:#ffffff; font-weight:700; font-size:13px; text-decoration:none; padding:12px 28px; border-radius:6px; letter-spacing:0.4px;">
                Verify &amp; Continue &rarr;
              </a>
            </td>
          </tr>
        </table>
        '''

    head_html = _render_email_head(f"Copilot Verification Code: {code}")
    accent_bar = _render_accent_line(3, "#0f172a")
    footer_html = _render_email_footer("If you did not request this verification, you can safely ignore this email.")

    html_content = f"""{head_html}
<body bgcolor="#ffffff" style="margin:0; padding:0; background-color:#ffffff; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#ffffff" style="background-color:#ffffff; width:100%;">
    <tr>
      <td align="center" style="padding:20px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:500px; background-color:#ffffff; border-radius:8px; border:1px solid #e2e8f0; overflow:hidden;" class="email-card">
          
          {accent_bar}

          <!-- Header -->
          <tr>
            <td align="center" style="padding:24px 20px 16px; border-bottom:1px solid #f1f5f9; background-color:#ffffff;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 10px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="48" height="48" alt="Copilot Logo" style="display:block; width:48px; height:48px; border-radius:8px; object-fit:contain; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <h1 style="margin:0; font-size:18px; font-weight:800; color:#0f172a; letter-spacing:0.3px;">
                Copilot Verification Code
              </h1>
            </td>
          </tr>

          <!-- Body Content -->
          <tr>
            <td style="padding:22px 24px 18px;" class="email-body">
              <p style="margin:0 0 14px; font-size:14px; color:#334155; line-height:1.5;">
                Hello <strong>{username or 'Operator'}</strong>,
              </p>
              <p style="margin:0 0 18px; font-size:14px; color:#475569; line-height:1.5;">
                Your 6-digit verification code is:
              </p>

              <!-- OTP Code Display Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:12px 0 16px;">
                <tr>
                  <td align="center" style="padding:20px 14px; background-color:#f8fafc; border:2px solid #0f172a; border-radius:8px;">
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:36px; font-weight:900; letter-spacing:10px; color:#0f172a; user-select:all; -webkit-user-select:all;" class="otp-code-text">
                      {code}
                    </div>
                    <div style="margin-top:10px; font-size:11px; color:#64748b; font-weight:600;">
                      ⏱️ Valid for 10 minutes
                    </div>
                  </td>
                </tr>
              </table>

              {quick_verify_html}

              <p style="margin:16px 0 0; font-size:12px; color:#94a3b8; line-height:1.4;">
                If you did not request this verification code, you can safely ignore this email.
              </p>

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
        f"COPILOT - VERIFICATION CODE\n\n"
        f"Hello {username or 'Operator'},\n\n"
        f"Your verification code is: {code}\n"
        f"This code is valid for 10 minutes.\n\n"
        f"{text_verify_url}"
        f"If you did not request this verification, you can safely ignore this message.\n\n"
        f"Copilot Automated Transmission\n"
    )

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": f"Copilot Verification Code: {code}",
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
    Sends the Daily Stock Reconciliation Report email to specified recipients.
    Features:
    - Pure white background for 100% clarity in Outlook, Gmail, and Apple Mail
    - Direct and concise: starts immediately with the key figures
    - 4 Executive KPI Stat Cards (Opening Stock, Inbound Received, Kits Installed, Closing Balance)
    - Standard 9-Row Balance Sheet table with dark headers, alternating light rows, and high-contrast figures
    - Shift Handover Remarks callout (if notes present)
    - Prototype sign-off: strictly branded as Copilot (no external agency or floor movement clutter)
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

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or DEFAULT_EMAIL_LOGO_URL
    bond_name = recon_data.get("storage_bond_name") or recon_data.get("warehouse_name") or "Depot Bond"
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
    remarks = notes or recon_data.get("remarks") or ""
    perf_pct = round((installed_count / scheduled_total * 100), 1) if scheduled_total > 0 else 0.0

    rows = recon_data.get("report_table", {}).get("rows", [])

    # Build High-Contrast Clean Table Rows
    table_rows_html = []
    for idx, r in enumerate(rows):
        metric = r.get("metric", "")
        pmo = r.get("pmo", 0)
        psv = r.get("psv", 0)
        tot = r.get("total", 0)

        is_closing = "closing" in metric.lower()
        is_perf = "performance" in metric.lower() or "%" in metric

        row_bg = "#f8fafc" if (idx % 2 == 1) else "#ffffff"
        border_style = "border-bottom:1px solid #e2e8f0;"
        text_color = "#0f172a"
        font_weight = "500"

        if is_closing:
            row_bg = "#fef9c3"  # warm light gold highlight
            border_style = "border-top:2px solid #0f172a; border-bottom:2px solid #0f172a;"
            text_color = "#0f172a"
            font_weight = "800"

        if is_perf:
            tot_str = str(tot).replace("%", "").strip()
            badge_bg = "#fee2e2"
            badge_color = "#b91c1c"
            try:
                tot_val = float(tot_str)
                if tot_val >= 100:
                    badge_bg = "#dcfce7"
                    badge_color = "#15803d"
                elif tot_val >= 80:
                    badge_bg = "#fef3c7"
                    badge_color = "#b45309"
            except (ValueError, TypeError):
                badge_bg = "#f1f5f9"
                badge_color = "#0f172a"
            tot_cell = f"<span style='background-color:{badge_bg}; color:{badge_color}; padding:2px 8px; border-radius:10px; font-weight:800; font-size:11px; display:inline-block;'>{tot}</span>"
        elif is_closing:
            tot_cell = f"<span style='font-family:monospace; font-size:15px; font-weight:900; color:#0f172a;'>{tot}</span>"
        else:
            tot_cell = f"<span style='font-family:monospace; font-size:13px; font-weight:{font_weight}; color:{text_color};'>{tot}</span>"

        pmo_cell = f"<span style='font-family:monospace; font-size:13px; color:#475569;'>{pmo}</span>"
        psv_cell = f"<span style='font-family:monospace; font-size:13px; color:#0f172a; font-weight:{font_weight};'>{psv}</span>"

        table_rows_html.append(f'''
        <tr style="background-color:{row_bg}; {border_style}">
          <td style="padding:10px 12px; font-weight:{font_weight}; color:{text_color}; font-size:13px;">{metric}</td>
          <td style="padding:10px 12px; text-align:right;">{pmo_cell}</td>
          <td style="padding:10px 12px; text-align:right;">{psv_cell}</td>
          <td style="padding:10px 12px; text-align:right;">{tot_cell}</td>
        </tr>
        ''')

    table_body = "\n".join(table_rows_html)

    # Build ASCII text fallback representation
    text_lines = [
        "=" * 64,
        "COPILOT - DAILY STOCK REPORT",
        f"Bond:         {bond_name}",
        f"Date:         {report_date} (Shift #{suffix})",
        f"Report By:    {operator_name}",
        "=" * 64,
        f"{'DESCRIPTION':<24} {'PMO':>10} {'PSV':>10} {'TOTAL':>14}",
        "-" * 64,
    ]
    for r in rows:
        metric = r.get("metric", "")
        pmo = str(r.get("pmo", 0))
        psv = str(r.get("psv", 0))
        tot = str(r.get("total", 0))
        text_lines.append(f"{metric:<24} {pmo:>10} {psv:>10} {tot:>14}")
    text_lines.append("=" * 64)
    text_lines.append(f"Opening: {opening_stock} (PSV {opening_psv}, PMO {opening_pmo}) | Inbound: +{delivered_count} | Installed: -{installed_count} | Closing: {closing_balance}")
    if remarks:
        text_lines.append("-" * 64)
        text_lines.append(f"Remarks: {remarks}")
    text_lines.append("-" * 64)
    text_lines.append("Copilot Automated Prototype Dispatch")
    text_content = "\n".join(text_lines)

    remarks_html = ""
    if remarks:
        remarks_html = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:18px 0 10px; background-color:#f0f9ff; border-left:4px solid #0284c7; border-radius:4px; border:1px solid #e0f2fe;">
          <tr>
            <td style="padding:12px 16px;">
              <div style="font-size:11px; text-transform:uppercase; color:#0369a1; font-weight:800; letter-spacing:0.5px; margin-bottom:3px;">
                Shift Handover Remarks:
              </div>
              <div style="font-size:13px; color:#1e293b; line-height:1.5;">
                {remarks}
              </div>
            </td>
          </tr>
        </table>
        '''

    head_html = _render_email_head(f"Copilot Stock Report - {bond_name} - {report_date}")
    accent_bar = _render_accent_line(3, "#0f172a")
    footer_html = _render_email_footer("Internal Prototype Notification")

    html_content = f"""{head_html}
<body bgcolor="#ffffff" style="margin:0; padding:0; background-color:#ffffff; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#ffffff" style="background-color:#ffffff; width:100%;">
    <tr>
      <td align="center" style="padding:18px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:640px; background-color:#ffffff; border-radius:8px; border:1px solid #e2e8f0; overflow:hidden;" class="email-card">
          
          {accent_bar}

          <!-- Header -->
          <tr>
            <td style="padding:20px 24px 16px; border-bottom:1px solid #e2e8f0; background-color:#ffffff;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td valign="middle">
                    <div style="font-size:10px; font-weight:800; color:#64748b; text-transform:uppercase; letter-spacing:0.8px; margin-bottom:2px;">
                      Copilot Daily Stock Summary
                    </div>
                    <h1 style="margin:0; font-size:18px; font-weight:800; color:#0f172a; line-height:1.3;">
                      {bond_name}
                    </h1>
                    <div style="margin-top:2px; font-size:12px; color:#64748b;">
                      Shift #{suffix} &bull; {report_date}
                    </div>
                  </td>
                  <td align="right" valign="middle">
                    <img src="{logo_src}" width="44" height="44" alt="Copilot Logo" style="display:block; width:44px; height:44px; border-radius:8px; object-fit:contain;" />
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Main Content Area -->
          <tr>
            <td style="padding:20px 22px 16px;">
              
              <!-- 4 KPI Summary Cards -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-bottom:18px;">
                <tr>
                  <!-- Card 1: Opening Balance -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-top:3px solid #0284c7; border-radius:6px; padding:12px 8px; text-align:center;">
                    <div style="font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.5px;">Opening Stock</div>
                    <div style="font-size:20px; font-weight:900; color:#0284c7; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{opening_stock:,}</div>
                    <div style="font-size:10px; color:#64748b;">PSV {opening_psv:,} &bull; PMO {opening_pmo:,}</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 2: Inbound Deliveries -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-top:3px solid #16a34a; border-radius:6px; padding:12px 8px; text-align:center;">
                    <div style="font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.5px;">Inbound Received</div>
                    <div style="font-size:20px; font-weight:900; color:#16a34a; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">+{delivered_count:,}</div>
                    <div style="font-size:10px; color:#64748b;">Received Kits</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 3: Kits Installed -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-top:3px solid #d97706; border-radius:6px; padding:12px 8px; text-align:center;">
                    <div style="font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.5px;">Installed</div>
                    <div style="font-size:20px; font-weight:900; color:#d97706; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{installed_count:,}</div>
                    <div style="font-size:10px; color:#64748b;">Target {scheduled_total:,} ({perf_pct:.0f}%)</div>
                  </td>
                  <td width="1%" class="mobile-hide">&nbsp;</td>
                  <!-- Card 4: Closing Balance -->
                  <td width="24%" valign="top" class="kpi-col" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-top:3px solid #0f172a; border-radius:6px; padding:12px 8px; text-align:center;">
                    <div style="font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.5px;">Closing Stock</div>
                    <div style="font-size:20px; font-weight:900; color:#0f172a; font-family:'SFMono-Regular',Consolas,monospace; margin:4px 0 2px;">{closing_balance:,}</div>
                    <div style="font-size:10px; color:#64748b;">PSV {closing_psv:,} &bull; PMO {closing_pmo:,}</div>
                  </td>
                </tr>
              </table>

              <!-- 9-Row Balance Sheet Table -->
              <div style="width:100%; overflow-x:auto;" class="table-responsive">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" class="data-table" style="width:100%; border-collapse:collapse; border:1px solid #e2e8f0; border-radius:6px; overflow:hidden; min-width:380px;">
                  <thead>
                    <tr style="background-color:#0f172a; color:#ffffff;">
                      <th style="padding:10px 12px; text-align:left; font-size:11px; font-weight:700; color:#ffffff; text-transform:uppercase; letter-spacing:0.5px;">Description</th>
                      <th style="padding:10px 12px; text-align:right; font-size:11px; font-weight:700; color:#ffffff; text-transform:uppercase; letter-spacing:0.5px;">PMO (Yellow)</th>
                      <th style="padding:10px 12px; text-align:right; font-size:11px; font-weight:700; color:#ffffff; text-transform:uppercase; letter-spacing:0.5px;">PSV (White)</th>
                      <th style="padding:10px 12px; text-align:right; font-size:11px; font-weight:700; color:#ffffff; text-transform:uppercase; letter-spacing:0.5px;">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table_body}
                  </tbody>
                </table>
              </div>

              {remarks_html}

              <!-- Simple Sign-off -->
              <div style="margin-top:16px; font-size:11px; color:#64748b; text-align:right;">
                Report generated by <strong>{operator_name}</strong> &bull; Shift #{suffix}
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

    if not api_key:
        logger.warning(
            "[STOCK EMAIL DEV FALLBACK] No RESEND_API_KEY found. Simulated stock report dispatch to %s (Date: %s)",
            clean_recipients,
            report_date,
        )
        return True, f"Simulated report generated for {len(clean_recipients)} recipient(s).", clean_recipients

    bond_label = bond_name or "AGM / SPIRO"
    subject = f"Daily Report for {bond_label} - {report_date}"

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
            timeout=15,
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
    Sends an alert email to administrator(s) when an operator requests access activation.
    Features clean white styling, metadata summary, activation code, and approve button.
    """
    clean_admins = [str(e).strip() for e in (admin_emails or []) if e and "@" in str(e)]
    if not clean_admins:
        return False, "No administrator email configured."

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()
    now_str = timezone.now().strftime("%d %b %Y, %H:%M:%S")

    text_content = (
        f"COPILOT - ACCESS REQUEST\n\n"
        f"Requester Email: {requester_email}\n"
        f"Operator Name:   {requester_name or '—'}\n"
        f"Device Name:     {device_name or 'Unknown'}\n"
        f"Client IP:       {client_ip or '—'}\n"
        f"Hardware / MAC:  {mac_address or '—'}\n"
        f"Activation Code: {activation_code}\n"
        f"Approve Link:    {approve_url}\n"
        f"Time:            {now_str}\n"
    )

    if not api_key:
        logger.warning(
            "[ACCESS REQUEST DEV FALLBACK] Simulated admin alert for %s (Code: %s)",
            _sanitize_log_str(requester_email),
            _sanitize_log_str(activation_code),
        )
        return True, "Simulated admin alert logged."

    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or DEFAULT_EMAIL_LOGO_URL

    approve_btn = ""
    if approve_url:
        approve_btn = f'''
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 10px;">
          <tr>
            <td align="center">
              <a href="{approve_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#16a34a; color:#ffffff; font-weight:700; font-size:13px; text-decoration:none; padding:12px 28px; border-radius:6px; letter-spacing:0.3px;">
                Approve Access &rarr;
              </a>
            </td>
          </tr>
        </table>
        '''

    head_html = _render_email_head(f"Copilot: Access Request from {requester_email}")
    accent_bar = _render_accent_line(3, "#dc2626")
    footer_html = _render_email_footer("Internal Administrator Alert")

    html_content = f"""{head_html}
<body bgcolor="#ffffff" style="margin:0; padding:0; background-color:#ffffff; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#ffffff" style="background-color:#ffffff; width:100%;">
    <tr>
      <td align="center" style="padding:20px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:500px; background-color:#ffffff; border-radius:8px; border:1px solid #e2e8f0; overflow:hidden;" class="email-card">
          
          {accent_bar}

          <!-- Header -->
          <tr>
            <td align="center" style="padding:22px 20px 16px; border-bottom:1px solid #f1f5f9; background-color:#ffffff;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 10px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="48" height="48" alt="Copilot Logo" style="display:block; width:48px; height:48px; border-radius:8px; object-fit:contain; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <h1 style="margin:0; font-size:18px; font-weight:800; color:#0f172a;">
                Access Request Alert
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:20px 22px 18px;">
              <p style="margin:0 0 14px; font-size:14px; color:#334155; line-height:1.5;">
                An access activation request was submitted for <strong>Copilot</strong>:
              </p>

              <!-- Metadata Table Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; margin-bottom:16px; overflow:hidden;">
                <tr>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; color:#64748b; width:35%;">Requester:</td>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; font-weight:700; color:#0f172a;">{requester_email}</td>
                </tr>
                <tr>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; color:#64748b;">Name:</td>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; font-weight:600; color:#334155;">{requester_name or '—'}</td>
                </tr>
                <tr>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; color:#64748b;">Device:</td>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; font-family:'SFMono-Regular',Consolas,monospace; color:#0284c7;">{device_name or 'Unknown'}</td>
                </tr>
                <tr>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; color:#64748b;">IP Address:</td>
                  <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; font-size:12px; font-family:'SFMono-Regular',Consolas,monospace; color:#0f172a;">{client_ip or '—'}</td>
                </tr>
                <tr>
                  <td style="padding:8px 12px; font-size:12px; color:#64748b;">Hardware / MAC:</td>
                  <td style="padding:8px 12px; font-size:12px; font-family:'SFMono-Regular',Consolas,monospace; color:#64748b;">{mac_address or '—'}</td>
                </tr>
              </table>

              <!-- Activation Code Box -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin-bottom:16px;">
                <tr>
                  <td align="center" style="padding:16px 14px; background-color:#f8fafc; border:2px dashed #0f172a; border-radius:6px;">
                    <div style="font-size:10px; text-transform:uppercase; color:#64748b; letter-spacing:0.6px; margin-bottom:4px; font-weight:700;">
                      Activation Code
                    </div>
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:30px; font-weight:900; letter-spacing:8px; color:#0f172a;">
                      {activation_code}
                    </div>
                  </td>
                </tr>
              </table>

              {approve_btn}

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
                "subject": f"Copilot: Access Request from {requester_email}",
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
    Notifies an operator that their Copilot access or invitation has been granted.
    Clean white styling, clear 6-digit activation code, and portal entry link.
    """
    if not to_email or "@" not in to_email:
        return False, "Valid recipient email required."

    api_key = get_resend_api_key()
    from_email = get_resend_from_email()
    target_url = login_url or "https://close.kirex.online/signup/"
    heading = "Copilot Access Invitation" if is_invitation else "Copilot Access Granted"
    logo_src = logo_url or getattr(settings, "EMAIL_LOGO_URL", "") or DEFAULT_EMAIL_LOGO_URL

    exp_line_txt = f"Valid Until:     {expires_at_display}\n" if expires_at_display else ""
    text_content = (
        f"COPILOT - ACCESS GRANTED\n\n"
        f"Hello {username},\n\n"
        f"Your email ({to_email}) has been granted access to Copilot.\n"
        f"Activation Code: {activation_code}\n"
        f"{exp_line_txt}"
        f"Portal URL:      {target_url}\n\n"
        f"Copilot Automated Transmission\n"
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
        <div style="display:inline-block; margin-top:8px; font-size:11px; color:#b45309; font-weight:700;">
          ⏳ Valid Until: {expires_at_display}
        </div>
        '''

    head_html = _render_email_head(heading)
    accent_bar = _render_accent_line(3, "#16a34a")
    footer_html = _render_email_footer("Keep your activation credentials secure.")

    html_content = f"""{head_html}
<body bgcolor="#ffffff" style="margin:0; padding:0; background-color:#ffffff; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#ffffff" style="background-color:#ffffff; width:100%;">
    <tr>
      <td align="center" style="padding:20px 10px;" class="email-wrapper">
        <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="max-width:500px; background-color:#ffffff; border-radius:8px; border:1px solid #e2e8f0; overflow:hidden;" class="email-card">
          
          {accent_bar}

          <!-- Header -->
          <tr>
            <td align="center" style="padding:22px 20px 16px; border-bottom:1px solid #f1f5f9; background-color:#ffffff;">
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin:0 auto 10px;">
                <tr>
                  <td align="center">
                    <img src="{logo_src}" width="48" height="48" alt="Copilot Logo" style="display:block; width:48px; height:48px; border-radius:8px; object-fit:contain; margin:0 auto;" />
                  </td>
                </tr>
              </table>
              <h1 style="margin:0; font-size:18px; font-weight:800; color:#0f172a;">
                {heading}
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:20px 22px 18px;">
              <p style="margin:0 0 12px; font-size:14px; color:#334155; line-height:1.5;">
                Hello <strong>{username}</strong>,
              </p>
              <p style="margin:0 0 16px; font-size:14px; color:#475569; line-height:1.5;">
                Your email (<strong>{to_email}</strong>) has been authorized for access to <strong>Copilot</strong>.
              </p>

              <!-- Activation Code Display Card -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:12px 0 16px;">
                <tr>
                  <td align="center" style="padding:18px 14px; background-color:#f8fafc; border:2px solid #16a34a; border-radius:6px;">
                    <div style="font-size:10px; text-transform:uppercase; color:#64748b; letter-spacing:0.6px; margin-bottom:4px; font-weight:700;">
                      6-Digit Activation Code
                    </div>
                    <div style="font-family:'SFMono-Regular', Consolas, 'Courier New', Courier, monospace; font-size:32px; font-weight:900; letter-spacing:8px; color:#0f172a;">
                      {activation_code}
                    </div>
                    {exp_line_html}
                  </td>
                </tr>
              </table>

              <!-- One-Click Portal CTA -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" width="100%" style="margin:16px 0 12px;">
                <tr>
                  <td align="center">
                    <a href="{target_url}" class="action-btn" target="_blank" rel="noopener noreferrer" style="display:inline-block; background-color:#0f172a; color:#ffffff; font-weight:700; font-size:13px; text-decoration:none; padding:12px 28px; border-radius:6px; letter-spacing:0.4px;">
                      Open Copilot &rarr;
                    </a>
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
                "subject": f"Copilot: {heading}",
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
