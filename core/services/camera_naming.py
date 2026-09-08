"""
Phone Camera & Digital Photo Naming Conventions Parser.

Analyzes photographic filenames across smartphone vendors and camera systems to extract:
  1. Vendor / Camera Convention (Apple iPhone, Samsung, Google Pixel, Tecno/Infinix, WhatsApp, DCF)
  2. Embedded Capture Timestamps (down to the second or millisecond)
  3. Monotonic Sequential Shutter Counters (e.g. IMG_0041 -> 41, WA0002 -> 2)
  4. Composite Chronological Keys to order photos with precision even if EXIF is stripped.

Common Smartphone Conventions:
  - Apple iPhone (iOS): IMG_0001.JPG, IMG_E0001.JPG (DCF sequential counter 0001-9999)
  - Samsung Galaxy (OneUI): YYYYMMDD_HHMMSS.jpg (e.g. 20260908_132250.jpg)
  - Google Pixel (GCam): PXL_YYYYMMDD_HHMMSSxxx.jpg (millisecond precision)
  - Tecno / Infinix / Xiaomi: IMG_YYYYMMDD_HHMMSS.jpg or YYYYMMDD_HHMMSS.jpg
  - WhatsApp: IMG-YYYYMMDD-WA0001.jpg (preserves date & transmission sequence when EXIF is stripped)
  - DSLR / Generic DCF: DSC_0001.JPG, DSC00001.JPG
  - Numbered Copies / Field Labels: "plate 1 (1).JPG", "bike (2).jpg"
"""
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone as _dt_tz
from typing import Optional, Tuple


def _make_aware_safe(dt: datetime) -> datetime:
    """Safely converts a naive datetime to aware without hard failing if Django settings are not ready."""
    try:
        from django.utils import timezone
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return dt.replace(tzinfo=_dt_tz.utc) if dt.tzinfo is None else dt


@dataclass
class CameraNamingSignal:
    vendor_convention: str
    embedded_datetime: Optional[datetime]
    sequence_number: Optional[int]
    clean_stem: str
    original_filename: str

    @property
    def display_tag(self) -> str:
        """Returns a concise label for TUI and inspectors, e.g. 'iPhone #41', 'Samsung 13:22', 'WA #2'."""
        if self.vendor_convention == "Apple iPhone":
            return f"iPhone #{self.sequence_number}"
        if self.vendor_convention == "WhatsApp":
            return f"WhatsApp WA{self.sequence_number:04d}" if self.sequence_number is not None else "WhatsApp"
        if self.vendor_convention == "Google Pixel":
            t_str = self.embedded_datetime.strftime("%H:%M:%S") if self.embedded_datetime else ""
            return f"Pixel {t_str}".strip()
        if self.vendor_convention in ("Samsung", "Android / Tecno"):
            t_str = self.embedded_datetime.strftime("%H:%M:%S") if self.embedded_datetime else ""
            seq_str = f" #{self.sequence_number}" if self.sequence_number else ""
            return f"{self.vendor_convention} {t_str}{seq_str}".strip()
        if self.vendor_convention == "Digital Camera (DCF)":
            return f"DCF #{self.sequence_number}"
        if self.sequence_number is not None:
            return f"Seq #{self.sequence_number}"
        return self.vendor_convention


def parse_camera_filename(filename_or_path: str) -> CameraNamingSignal:
    """Extracts vendor convention, embedded timestamp, and sequential counter from a filename."""
    if not filename_or_path:
        return CameraNamingSignal(
            vendor_convention="Unknown",
            embedded_datetime=None,
            sequence_number=None,
            clean_stem="",
            original_filename="",
        )

    base = os.path.basename(str(filename_or_path))
    stem, _ext = os.path.splitext(base)

    # 1. Google Pixel / GCam: PXL_YYYYMMDD_HHMMSSxxx(.PORTRAIT/.MP)?
    m_pixel = re.search(r"PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(\d{3})?", stem)
    if m_pixel:
        try:
            year, month, day, hour, minute, second = map(int, m_pixel.groups()[:6])
            ms = int(m_pixel.group(7) or 0) * 1000
            dt = _make_aware_safe(datetime(year, month, day, hour, minute, second, ms))
            seq = (hour * 3600 + minute * 60 + second) * 1000 + int(m_pixel.group(7) or 0)
            return CameraNamingSignal(
                vendor_convention="Google Pixel",
                embedded_datetime=dt,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 2. WhatsApp: IMG-YYYYMMDD-WAXXXX
    m_wa = re.search(r"IMG-(\d{4})(\d{2})(\d{2})-WA(\d+)", stem, re.IGNORECASE)
    if m_wa:
        try:
            year, month, day = map(int, m_wa.groups()[:3])
            dt = _make_aware_safe(datetime(year, month, day, 0, 0, 0))
            seq = int(m_wa.group(4))
            return CameraNamingSignal(
                vendor_convention="WhatsApp",
                embedded_datetime=dt,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 3. Samsung Galaxy / Tecno / Infinix / Xiaomi: YYYYMMDD_HHMMSS(_XX)?
    # Patterns: 20260908_132250, IMG_20260908_132250, IMAGE_20260908_132250
    m_android = re.search(r"(?:IMG_|IMAGE_|SAM_|VID_)?(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?:_(\d+))?", stem, re.IGNORECASE)
    if m_android:
        try:
            year, month, day, hour, minute, second = map(int, m_android.groups()[:6])
            sub_seq = int(m_android.group(7) or 0)
            dt = _make_aware_safe(datetime(year, month, day, hour, minute, second))
            seq = (hour * 3600 + minute * 60 + second) * 100 + sub_seq
            vendor = "Samsung" if not stem.upper().startswith("IMG_") else "Android / Tecno"
            return CameraNamingSignal(
                vendor_convention=vendor,
                embedded_datetime=dt,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 4. Apple iPhone / iOS: IMG_XXXX or IMG_EXXXX (where XXXX is 4-5 sequential digits)
    m_iphone = re.search(r"(?:^|_)IMG_E?(\d{4,5})$", stem, re.IGNORECASE)
    if m_iphone:
        try:
            seq = int(m_iphone.group(1))
            return CameraNamingSignal(
                vendor_convention="Apple iPhone",
                embedded_datetime=None,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 5. Digital Camera / DCF standard: DSC_XXXX, DSC0XXXX, SAM_XXXX
    m_dcf = re.search(r"^(?:DSC_?|_DSC|PIC_|SAM_)(\d{4,6})$", stem, re.IGNORECASE)
    if m_dcf:
        try:
            seq = int(m_dcf.group(1))
            return CameraNamingSignal(
                vendor_convention="Digital Camera (DCF)",
                embedded_datetime=None,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 6. Windows numbered copies or parenthesized numbers: e.g. "plate 1 (2)", "bike (1)"
    m_copy = re.search(r"\((\d+)\)$", stem)
    if m_copy:
        try:
            seq = int(m_copy.group(1))
            return CameraNamingSignal(
                vendor_convention="Numbered Copy",
                embedded_datetime=None,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    # 7. Fallback: Any trailing sequence of digits
    m_digits = re.search(r"(\d+)$", stem)
    if m_digits:
        try:
            seq = int(m_digits.group(1))
            return CameraNamingSignal(
                vendor_convention="Numbered Sequence",
                embedded_datetime=None,
                sequence_number=seq,
                clean_stem=stem,
                original_filename=base,
            )
        except Exception:
            pass

    return CameraNamingSignal(
        vendor_convention="Standard",
        embedded_datetime=None,
        sequence_number=None,
        clean_stem=stem,
        original_filename=base,
    )


def extract_chronological_sort_key(
    filename: str,
    captured_at: Optional[datetime] = None,
    fallback_dt: Optional[datetime] = None,
) -> Tuple[float, int, str]:
    """Generates a composite sort key (timestamp_epoch, sequence_num, filename)

    Ensures robust, deterministic chronological sorting even when:
      - EXIF is missing but filename encodes a timestamp (Samsung/Pixel/Xiaomi).
      - EXIF is missing and filename is sequential (iPhone IMG_0041, WhatsApp WA0001).
      - Timestamps share the exact same second (sequential counter breaks the tie).
    """
    sig = parse_camera_filename(filename)

    # 1. Best datetime
    dt = captured_at or sig.embedded_datetime or fallback_dt
    epoch = dt.timestamp() if dt else 0.0

    # 2. Sequence number
    seq = sig.sequence_number if sig.sequence_number is not None else 0

    return (epoch, seq, filename)
