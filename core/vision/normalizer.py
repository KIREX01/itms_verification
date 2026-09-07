"""
Plate text canonicalization and syntax validation.

Ugandan standard plates follow: 3 letters, space, 3 digits, 1-2 letters
  e.g. UMA 123AA, UBB 456C
Canonical form strips the space/hyphen and uppercases: UMA123AA, UBB456C.

Positional correction fixes common OCR confusions *based on which slot
the character falls in* (a `0` in a letter slot is almost always an `O`;
an `O` in a digit slot is almost always a `0`).
"""
import re

PLATE_REGEX = re.compile(r"^([A-Z]{3})\s*(\d{3})([A-Z]{1,2})$")

# letter-slot confusions: OCR digit -> intended letter
_DIGIT_TO_LETTER = {"0": "O", "1": "I", "8": "B", "5": "S", "2": "Z", "4": "A"}
# digit-slot confusions: OCR letter -> intended digit
_LETTER_TO_DIGIT = {"O": "0", "I": "1", "B": "8", "S": "5", "Z": "2", "A": "4"}

# In Uganda, all standard registrations begin with 'U'. Common OCR confusions for leading 'U':
_LEADING_U_CONFUSIONS = {"V": "U", "W": "U", "Y": "U", "0": "U", "O": "U", "J": "U"}
# Motorcycle prefix confusions for 'UM':
_MOTORCYCLE_PREFIX_CONFUSIONS = {"WH": "UM", "VW": "UM", "UW": "UM", "WM": "UM", "VM": "UM"}


def canonicalize(raw_text: str) -> str:
    """Strip whitespace/hyphens/underscores and uppercase. Does NOT validate syntax."""
    if not raw_text:
        return ""
    cleaned = re.sub(r"[\s\-_.]", "", raw_text.upper())
    # Drop any character that isn't alphanumeric (OCR noise, punctuation)
    cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)
    return cleaned


def _correct_positional(cleaned: str) -> str:
    """
    Attempt to coerce a cleaned string into the LLL DDD LL(1-2) pattern by
    correcting characters that are in the wrong "alphabet" for their slot.
    Handles Ugandan standard vehicle and motorcycle registration patterns.
    All Ugandan plates start with 'U'. Ugandan motorcycles start with 'UM'
    (currently 'UMA', with only the 3rd letter changing in the future).
    """
    if not cleaned:
        return cleaned

    # Handle motorcycle plates where 2-line OCR read prefix as 2 letters (e.g. UM145PD or WH145PD)
    # Pattern: 2 letters + 3 digits + 1-2 letters (length 6 or 7)
    m_2letter = re.match(r"^([A-Z]{2})(\d{3})([A-Z]{1,2})$", cleaned)
    if m_2letter:
        pfx, digits, sfx = m_2letter.groups()
        # If prefix is UM or an OCR confusion of UM (like WH, VW, UW)
        resolved_pfx = _MOTORCYCLE_PREFIX_CONFUSIONS.get(pfx, pfx)
        if resolved_pfx == "UM":
            # Current Ugandan motorcycle series is UMA; 3rd letter is 'A'
            return f"UMA{digits}{sfx}"

    if len(cleaned) not in (7, 8):
        return cleaned

    chars = list(cleaned)

    # All standard Ugandan plates begin with 'U'
    if chars[0] in _LEADING_U_CONFUSIONS:
        chars[0] = _LEADING_U_CONFUSIONS[chars[0]]

    # For motorcycles (starts with U and 2nd char is M or M confusion like W, H, N)
    if chars[0] == "U" and chars[1] in ("W", "H", "N"):
        chars[1] = "M"

    # Slots: 0-2 letters, 3-5 digits, 6..end letters
    for i in range(0, 3):
        if chars[i] in _DIGIT_TO_LETTER:
            chars[i] = _DIGIT_TO_LETTER[chars[i]]
    for i in range(3, 6):
        if chars[i] in _LETTER_TO_DIGIT:
            chars[i] = _LETTER_TO_DIGIT[chars[i]]
    for i in range(6, len(chars)):
        if chars[i] in _DIGIT_TO_LETTER:
            chars[i] = _DIGIT_TO_LETTER[chars[i]]

    return "".join(chars)


def normalize_plate(raw_text: str) -> dict:
    """
    Full normalization pipeline.

    Returns:
        {
            "canonical": str,     # best-effort canonical string
            "is_valid": bool,     # matches PLATE_REGEX after correction
            "raw": str,           # original input, unmodified
        }
    """
    cleaned = canonicalize(raw_text)
    corrected = _correct_positional(cleaned)

    is_valid = bool(PLATE_REGEX.match(corrected))
    canonical = corrected if is_valid else cleaned

    return {
        "canonical": canonical,
        "is_valid": is_valid,
        "raw": raw_text,
    }


def is_valid_plate(canonical_text: str) -> bool:
    return bool(PLATE_REGEX.match(canonical_text))
