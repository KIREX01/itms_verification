"""
ITMS Verification Copilot Version & Release Metadata.
Tracks semantic versioning and GitHub Release repository information.
"""
from typing import Tuple

__version__ = "1.0.0"
GITHUB_REPO = "KIREX01/itms_verification"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_REPO_HTML_URL = f"https://github.com/{GITHUB_REPO}"


def parse_version(v_str: str) -> Tuple[int, ...]:
    """
    Parses a version string into a tuple of integers for comparison.
    Handles 'v1.2.3', '1.2.3', '1.0.0-rc1', etc.
    """
    cleaned = v_str.strip().lstrip("vV")
    parts = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer_version(remote_v_str: str, local_v_str: str = __version__) -> bool:
    """Returns True if remote version is strictly newer than local version."""
    try:
        remote_tuple = parse_version(remote_v_str)
        local_tuple = parse_version(local_v_str)
        return remote_tuple > local_tuple
    except Exception:
        return False


def get_version_info() -> dict:
    """Returns complete version metadata dictionary."""
    return {
        "version": __version__,
        "version_tag": f"v{__version__}",
        "github_repo": GITHUB_REPO,
        "github_url": GITHUB_REPO_HTML_URL,
        "releases_url": f"{GITHUB_REPO_HTML_URL}/releases",
    }
