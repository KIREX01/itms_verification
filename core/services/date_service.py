"""
Standardized Date, Shift & Time Service for ITMS Verification Copilot.

Enforces unified East Africa Time (Africa/Kampala, UTC+3) across the application,
providing canonical bidirectional conversions between:
1. 6-digit shift suffix: DDMMYY (e.g. '171026') used in ITMS PO numbers ('PO-*-171026') and barcodes.
2. Standard Ugandan human-readable date: DD.MM.YYYY (e.g. '17.10.2026').
3. Standard ISO / DB calendar date: YYYY-MM-DD (e.g. '2026-10-17').
4. Ugandan standard timestamp: DD.MM.YYYY - HH:MM (e.g. '17.10.2026 - 14:30').

Also manages the system-wide Active Operating Shift Date (configured in Settings:
'today', 'yesterday', or 'manual'), mounting the shift date seamlessly across
Settings, Reports Hub, Stock Management, and ITMS Connect.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

KAMPALA_TZ_NAME = "Africa/Kampala"
KAMPALA_TZ = ZoneInfo(KAMPALA_TZ_NAME)


def get_kampala_tz() -> ZoneInfo:
    """Returns the canonical Africa/Kampala ZoneInfo object."""
    return KAMPALA_TZ


def now_kampala() -> datetime:
    """Returns the current timezone-aware datetime in Kampala (EAT, UTC+3)."""
    try:
        return timezone.localtime()
    except Exception:
        return datetime.now(KAMPALA_TZ)


def today_kampala() -> date:
    """Returns today's calendar date in Kampala."""
    try:
        return timezone.localdate()
    except Exception:
        return now_kampala().date()


def yesterday_kampala() -> date:
    """Returns yesterday's calendar date in Kampala."""
    return today_kampala() - timedelta(days=1)


def format_date_suffix(d: Union[date, datetime]) -> str:
    """
    Formats a date or datetime object into a 6-digit DDMMYY string.
    Example: date(2026, 10, 17) -> '171026'
    """
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d%m%y")


def format_date_suffix_readable(date_suffix: Optional[str]) -> str:
    """
    Converts 6-digit DDMMYY string (e.g. '171026') into human-readable '17.10.2026'.
    """
    if not date_suffix or len(date_suffix) != 6 or not date_suffix.isdigit():
        return date_suffix or ""
    dd = date_suffix[:2]
    mm = date_suffix[2:4]
    yy = date_suffix[4:6]
    return f"{dd}.{mm}.20{yy}"


def format_readable_date(val: Any) -> str:
    """
    Converts any date representation (date, datetime, 'YYYY-MM-DD', 'DDMMYY')
    into standardized human-readable 'DD.MM.YYYY'.
    """
    if not val:
        return ""
    d = parse_date_input(val)
    if d:
        return d.strftime("%d.%m.%Y")
    return str(val)


def format_iso_date(val: Any) -> str:
    """
    Converts any date representation into ISO 'YYYY-MM-DD'.
    """
    if not val:
        return ""
    d = parse_date_input(val)
    if d:
        return d.strftime("%Y-%m-%d")
    return str(val)


# Alias for standard date string
format_date_standard = format_iso_date


def format_itms_timestamp(val: Optional[Union[datetime, str]]) -> str:
    """
    Converts datetime (or parseable string) into Ugandan standard 'DD.MM.YYYY - HH:MM' in Kampala time.
    """
    if not val:
        return ""
    if isinstance(val, str):
        val = val.strip()
        if re.match(r"^\d{2}\.\d{2}\.\d{4} - \d{2}:\d{2}$", val):
            return val
        try:
            val = datetime.fromisoformat(val)
        except Exception:
            return val
    if isinstance(val, datetime):
        if timezone.is_naive(val):
            val = timezone.make_aware(val, KAMPALA_TZ)
        else:
            val = val.astimezone(KAMPALA_TZ)
        return val.strftime("%d.%m.%Y - %H:%M")
    return str(val)


def extract_order_date_suffix(order_number: Optional[str]) -> Optional[str]:
    """
    Extracts the 6-digit DDMMYY date suffix from an order number string.
    Example: 'PO-UMA667PU-171026' -> '171026'
             '#PO-UMA667PU-171026' -> '171026'
             'PO-UMA824PS-230926' -> '230926'
    """
    if not order_number:
        return None
    cleaned = str(order_number).strip().lstrip("#")
    m = re.search(r"-(\d{6})$", cleaned)
    return m.group(1) if m else None


def parse_date_input(val: Any) -> Optional[date]:
    """
    Robust parser converting various date formats into a calendar date object:
    - datetime / date objects
    - 'TODAY' -> today_kampala()
    - 'YESTERDAY' -> yesterday_kampala()
    - 6-digit 'DDMMYY' -> date(20YY, MM, DD)
    - 'DD.MM.YYYY', 'DD/MM/YYYY', 'DD-MM-YYYY'
    - 'YYYY-MM-DD', 'YYYY/MM/DD'
    - ITMS order codes e.g. 'PO-*-171026'
    - ITMS timestamp strings '17.10.2026 - 14:30'
    Returns None if empty, 'ALL', or unparseable.
    """
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.astimezone(KAMPALA_TZ).date() if timezone.is_aware(val) else val.date()

    s = str(val).strip()
    if not s or s.upper() in ("ALL", "NONE", "NULL", "DEFAULT"):
        return None

    upper_s = s.upper()
    if upper_s == "TODAY":
        return today_kampala()
    if upper_s == "YESTERDAY":
        return yesterday_kampala()

    # 1. PO Order suffix format (e.g. PO-*-171026)
    po_sfx = extract_order_date_suffix(s)
    if po_sfx:
        s = po_sfx

    # 2. 6-digit DDMMYY format (e.g. 171026)
    if len(s) == 6 and s.isdigit():
        dd, mm, yy = int(s[:2]), int(s[2:4]), int(s[4:6])
        try:
            return date(2000 + yy, mm, dd)
        except ValueError:
            return None

    # 3. DD.MM.YYYY or DD/MM/YYYY or DD-MM-YYYY (with optional time)
    m_dmy = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](20\d{2}|\d{2})(?:\s+.*)?$", s)
    if m_dmy:
        dd, mm = int(m_dmy.group(1)), int(m_dmy.group(2))
        raw_y = int(m_dmy.group(3))
        y = 2000 + raw_y if raw_y < 100 else raw_y
        try:
            return date(y, mm, dd)
        except ValueError:
            return None

    # 4. YYYY-MM-DD or YYYY/MM/DD (with optional time)
    m_ymd = re.match(r"^(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?:[T\s].*)?$", s)
    if m_ymd:
        y, mm, dd = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        try:
            return date(y, mm, dd)
        except ValueError:
            return None

    # 5. Regex search inside complex strings for DD.MM.YYYY
    m_embed = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", s)
    if m_embed:
        try:
            return date(int(m_embed.group(3)), int(m_embed.group(2)), int(m_embed.group(1)))
        except ValueError:
            pass

    return None


def parse_target_date_suffix(date_input: Optional[Any]) -> Optional[str]:
    """
    Normalizes any date representation into a 6-digit DDMMYY suffix format.
    Returns None if input represents 'ALL' or is unparseable.
    """
    if date_input is None:
        return None
    s = str(date_input).strip()
    if s.upper() in ("ALL", "NONE", "", "NULL"):
        return None
    d = parse_date_input(date_input)
    if d:
        return format_date_suffix(d)
    return None


def get_active_shift_date() -> date:
    """
    Returns the system's active operating shift date as configured in Settings.
    - 'today': today in Kampala
    - 'yesterday': yesterday in Kampala
    - 'manual': configured manual_target_date (falls back to today if invalid)
    """
    try:
        from core.services import config_service
        mode = config_service.get_setting("sync.shift_target_date_mode", "today")
        if mode == "yesterday":
            return yesterday_kampala()
        if mode == "manual":
            manual_raw = config_service.get_setting("sync.manual_target_date", "")
            parsed = parse_date_input(manual_raw)
            if parsed:
                return parsed
            logger.warning("Manual shift target date '%s' could not be parsed; defaulting to today", manual_raw)
    except Exception as exc:
        logger.debug("Could not load shift date from config: %s", exc)

    return today_kampala()


def get_active_shift_suffix() -> str:
    """Returns the 6-digit DDMMYY suffix of the active operating shift date."""
    return format_date_suffix(get_active_shift_date())


def get_active_shift_info() -> Dict[str, Any]:
    """
    Returns comprehensive metadata for the active operating shift date,
    suitable for UI banners, badges, and API status responses.
    """
    active_d = get_active_shift_date()
    today_d = today_kampala()
    suffix = format_date_suffix(active_d)
    formatted = format_readable_date(active_d)
    iso_d = format_iso_date(active_d)

    mode = "today"
    manual_raw = ""
    try:
        from core.services import config_service
        mode = str(config_service.get_setting("sync.shift_target_date_mode", "today")).lower()
        manual_raw = str(config_service.get_setting("sync.manual_target_date", ""))
    except Exception:
        pass

    if mode == "all" or manual_raw.strip().upper() == "ALL":
        return {
            "mode": "all",
            "manual_date": "ALL",
            "date": "ALL",
            "suffix": "ALL",
            "formatted_date": "All Dates",
            "display": "All Dates (Aggregate)",
            "is_today": False,
        }

    return {
        "mode": mode,
        "manual_date": manual_raw,
        "date": iso_d,
        "suffix": suffix,
        "formatted_date": formatted,
        "display": f"{formatted} ({suffix})",
        "is_today": (active_d == today_d),
    }


def resolve_date_and_suffix(target: Optional[Any] = None) -> Tuple[date, str]:
    """
    Normalizes a date object, string, or 6-digit suffix into a (date, suffix) tuple.
    CRITICAL: If target is None, empty, or 'DEFAULT', this automatically resolves
    to the system's active operating shift date configured in Settings!
    """
    if target is None:
        d = get_active_shift_date()
        return d, format_date_suffix(d)

    s = str(target).strip()
    if not s or s.upper() in ("DEFAULT", "NONE"):
        d = get_active_shift_date()
        return d, format_date_suffix(d)

    if s.upper() == "ALL":
        return get_active_shift_date(), "ALL"

    parsed_d = parse_date_input(target)
    if parsed_d:
        return parsed_d, format_date_suffix(parsed_d)

    # Fallback to active shift date
    active_d = get_active_shift_date()
    return active_d, format_date_suffix(active_d)


def get_available_shift_dates() -> List[Dict[str, Any]]:
    """
    Scans distinct date suffixes across InstallationOrders, Dispatches, Deliveries,
    and Safe Audits. Returns ordered list (chronologically newest first).
    """
    from core.models import InstallationOrder, SafeAuditScan, StockDelivery, StockDispatchScan

    suffix_counts: Dict[str, Dict[str, int]] = {}

    def _add_suffix(sfx: Optional[str], cat: str):
        if not sfx or len(sfx) != 6 or not sfx.isdigit():
            return
        if sfx not in suffix_counts:
            suffix_counts[sfx] = {"orders": 0, "dispatches": 0, "audits": 0}
        suffix_counts[sfx][cat] = suffix_counts[sfx].get(cat, 0) + 1

    # 1. Orders
    for num in InstallationOrder.objects.values_list("order_number", flat=True):
        s = extract_order_date_suffix(num)
        if s:
            _add_suffix(s, "orders")

    # 2. Dispatches
    for s in StockDispatchScan.objects.exclude(work_date_suffix__isnull=True).exclude(work_date_suffix="").values_list("work_date_suffix", flat=True).distinct():
        _add_suffix(s, "dispatches")

    # 3. Deliveries
    for s in StockDelivery.objects.exclude(target_date_suffix__isnull=True).exclude(target_date_suffix="").values_list("target_date_suffix", flat=True).distinct():
        _add_suffix(s, "orders")

    # 4. Safe Audits
    for s in SafeAuditScan.objects.exclude(work_date_suffix__isnull=True).exclude(work_date_suffix="").values_list("work_date_suffix", flat=True).distinct():
        _add_suffix(s, "audits")

    # Ensure today and active shift suffix are represented
    today_sfx = format_date_suffix(today_kampala())
    if today_sfx not in suffix_counts:
        suffix_counts[today_sfx] = {"orders": 0, "dispatches": 0, "audits": 0}

    active_info = get_active_shift_info()
    active_sfx = active_info["suffix"]
    if active_sfx not in suffix_counts:
        suffix_counts[active_sfx] = {"orders": 0, "dispatches": 0, "audits": 0}

    def _sort_key(s: str):
        if len(s) == 6 and s.isdigit():
            dd, mm, yy = int(s[:2]), int(s[2:4]), int(s[4:6])
            return (yy, mm, dd)
        return (0, 0, 0)

    results = []
    for s in sorted(suffix_counts.keys(), key=_sort_key, reverse=True):
        formatted = format_date_suffix_readable(s)
        d_parsed = parse_date_input(s)
        iso_d = d_parsed.strftime("%Y-%m-%d") if d_parsed else ""
        cnt_info = suffix_counts[s]
        total_activity = cnt_info["orders"] + cnt_info["dispatches"] + cnt_info["audits"]
        results.append({
            "suffix": s,
            "formatted_date": formatted,
            "iso_date": iso_d,
            "label": f"{formatted} ({s})",
            "order_count": cnt_info["orders"],
            "dispatch_count": cnt_info["dispatches"],
            "audit_count": cnt_info["audits"],
            "total_activity": total_activity,
            "is_today": (s == today_sfx),
            "is_active_shift": (s == active_sfx),
        })

    return results
