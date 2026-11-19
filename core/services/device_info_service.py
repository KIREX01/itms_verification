"""
Device information: what we know about each browser / phone / install that uses CLOSE Copilot.

Web & phones (stored on core.AuthorizedDevice):
  * server side, every request: user agent parsed into OS / browser / model, plus User-Agent Client
    Hints (exact OS version, phone model, CPU architecture) that Chromium browsers send once asked
    via the Accept-CH response header;
  * client side, once per browser session: screen, pixel ratio, time zone, languages, CPU cores,
    memory, GPU, touch support, network type (core/static/core/js/device_report.js).

Installs (laptops / workstations, reported to the central server on every check-in):
  hostname, OS edition / version / build, architecture, CPU model and cores, RAM, disk, Python,
  app version, time zone, local IPs, MAC address, install path.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import sys
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone

ACCEPT_CH = ("Sec-CH-UA-Platform-Version, Sec-CH-UA-Model, Sec-CH-UA-Arch, Sec-CH-UA-Bitness, "
             "Sec-CH-UA-Full-Version-List, Sec-CH-UA-Mobile, Sec-CH-UA-Platform")
ENRICH_EVERY = timedelta(hours=1)

# ---------------------------------------------------------------------------
# User agent parsing (dependency-free; good enough for the browsers our operators use)
# ---------------------------------------------------------------------------
_WINDOWS_NT = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7"}


def parse_user_agent(ua: str) -> Dict[str, str]:
    ua = ua or ""
    out = {"os_name": "", "os_version": "", "browser": "", "browser_version": "", "device_model": "",
           "is_mobile": "0"}
    m = re.search(r"Windows NT ([\d.]+)", ua)
    if m:
        out["os_name"], out["os_version"] = "Windows", _WINDOWS_NT.get(m.group(1), m.group(1))
    elif "Android" in ua:
        m = re.search(r"Android ([\d.]+)", ua)
        out["os_name"], out["os_version"] = "Android", (m.group(1) if m else "")
        m = re.search(r"Android [\d.]+; (?:[a-z]{2}-[a-z]{2}; )?([^;)]+?)(?: Build/[^;)]*)?\)", ua, re.I)
        if m and m.group(1).strip() not in ("K", "wv"):
            out["device_model"] = m.group(1).strip()
        out["is_mobile"] = "1"
    elif re.search(r"iPhone|iPad|iPod", ua):
        m = re.search(r"OS (\d+[_\d]*) like Mac OS X", ua)
        out["os_name"] = "iPadOS" if "iPad" in ua else "iOS"
        out["os_version"] = m.group(1).replace("_", ".") if m else ""
        out["device_model"] = "iPad" if "iPad" in ua else "iPhone"
        out["is_mobile"] = "1"
    elif "Mac OS X" in ua:
        m = re.search(r"Mac OS X ([\d_]+)", ua)
        out["os_name"], out["os_version"] = "macOS", (m.group(1).replace("_", ".") if m else "")
    elif "CrOS" in ua:
        out["os_name"] = "ChromeOS"
    elif "Linux" in ua:
        out["os_name"] = "Linux"

    for name, pattern in (("Edge", r"Edg(?:A|iOS)?/([\d.]+)"), ("Opera", r"OPR/([\d.]+)"),
                          ("Samsung Internet", r"SamsungBrowser/([\d.]+)"), ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
                          ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"), ("Safari", r"Version/([\d.]+).*Safari")):
        m = re.search(pattern, ua)
        if m:
            out["browser"], out["browser_version"] = name, m.group(1)
            break
    if "Mobile" in ua:
        out["is_mobile"] = "1"
    return out


def _clean_hint(value: str) -> str:
    return (value or "").strip().strip('"')[:120]


def client_hints(request) -> Dict[str, str]:
    h = request.headers
    hints = {
        "platform": _clean_hint(h.get("Sec-CH-UA-Platform", "")),
        "platform_version": _clean_hint(h.get("Sec-CH-UA-Platform-Version", "")),
        "model": _clean_hint(h.get("Sec-CH-UA-Model", "")),
        "arch": _clean_hint(h.get("Sec-CH-UA-Arch", "")),
        "bitness": _clean_hint(h.get("Sec-CH-UA-Bitness", "")),
        "mobile": _clean_hint(h.get("Sec-CH-UA-Mobile", "")),
        "brands": (h.get("Sec-CH-UA-Full-Version-List") or h.get("Sec-CH-UA") or "")[:300],
    }
    return {k: v for k, v in hints.items() if v}


def _windows_from_hints(hints: Dict[str, str]) -> str:
    """Sec-CH-UA-Platform-Version >= 13 means Windows 11 (the user agent says 10 for both)."""
    try:
        major = int((hints.get("platform_version") or "0").split(".")[0])
    except ValueError:
        return ""
    if major >= 13:
        return "11"
    if major > 0:
        return "10"
    return ""


def describe_request(request) -> Dict[str, Any]:
    ua = request.META.get("HTTP_USER_AGENT", "")
    parsed = parse_user_agent(ua)
    hints = client_hints(request)
    if parsed["os_name"] == "Windows":
        parsed["os_version"] = _windows_from_hints(hints) or parsed["os_version"]
    elif hints.get("platform_version") and parsed["os_name"] in ("Android", "macOS"):
        parsed["os_version"] = hints["platform_version"]
    if hints.get("model"):
        parsed["device_model"] = hints["model"]
    return {"parsed": parsed, "hints": hints,
            "accept_language": request.META.get("HTTP_ACCEPT_LANGUAGE", "")[:120]}


def enrich_device(device, request, force: bool = False) -> None:
    """Best-effort: refresh OS / browser / model on an AuthorizedDevice (at most hourly)."""
    if device is None or request is None or not getattr(device, "pk", None):
        return
    info = dict(device.device_info or {})
    last = info.get("server_seen_at")
    ua = request.META.get("HTTP_USER_AGENT", "")
    if not force and last and ua == device.user_agent and device.os_name:
        try:
            from datetime import datetime
            if timezone.now() - datetime.fromisoformat(last) < ENRICH_EVERY:
                return
        except ValueError:
            pass
    desc = describe_request(request)
    p = desc["parsed"]
    info["server"] = {"hints": desc["hints"], "accept_language": desc["accept_language"]}
    info["server_seen_at"] = timezone.now().isoformat()
    device.os_name = p["os_name"][:40]
    device.os_version = p["os_version"][:40]
    device.browser = p["browser"][:40]
    device.browser_version = p["browser_version"][:40]
    if p["device_model"]:
        device.device_model = p["device_model"][:80]
    device.device_info = info
    device.save(update_fields=["os_name", "os_version", "browser", "browser_version", "device_model", "device_info"])


# Keys the browser-side report may set (anything else is ignored), with max lengths.
CLIENT_KEYS = {
    "screen": 40, "viewport": 40, "pixel_ratio": 10, "color_depth": 10, "orientation": 30,
    "timezone": 64, "utc_offset_minutes": 10, "languages": 120, "platform": 60,
    "hardware_concurrency": 10, "device_memory_gb": 10, "max_touch_points": 10, "gpu": 160,
    "connection": 40, "downlink_mbps": 10, "standalone_pwa": 5, "cookies_enabled": 5,
    "ua_platform": 40, "ua_platform_version": 40, "ua_model": 80, "ua_arch": 20, "ua_bitness": 10,
}


def record_client_report(device, payload: Dict[str, Any]) -> Dict[str, str]:
    clean = {}
    for key, max_len in CLIENT_KEYS.items():
        if key in payload and payload[key] not in (None, ""):
            clean[key] = str(payload[key])[:max_len]
    info = dict(device.device_info or {})
    info["client"] = clean
    info["client_reported_at"] = timezone.now().isoformat()
    fields = ["device_info"]
    if clean.get("ua_model") and not device.device_model:
        device.device_model = clean["ua_model"][:80]
        fields.append("device_model")
    if device.os_name == "Windows" and clean.get("ua_platform_version"):
        win = _windows_from_hints({"platform_version": clean["ua_platform_version"]})
        if win:
            device.os_version = win
            fields.append("os_version")
    device.device_info = info
    device.save(update_fields=fields)
    return clean


# ---------------------------------------------------------------------------
# Install (host) information
# ---------------------------------------------------------------------------
def _total_ram_bytes() -> Optional[int]:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys)
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        if sys.platform == "darwin":
            import subprocess
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                      timeout=5).stdout.strip())
    except Exception:
        return None
    return None


def _cpu_model() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine()


def _os_description() -> Dict[str, str]:
    system = platform.system()
    out = {"os_name": system, "os_release": platform.release(), "os_version": platform.version()}
    if system == "Windows":
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            build = 0
        out["os_name"] = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        try:
            out["os_edition"] = platform.win32_edition() or ""
        except Exception:
            pass
    elif system == "Linux" and os.path.exists("/etc/os-release"):
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    out["os_name"] = line.split("=", 1)[1].strip().strip('"')
    elif system == "Darwin":
        out["os_name"] = f"macOS {platform.mac_ver()[0]}"
    return out


def host_info() -> Dict[str, Any]:
    from django.conf import settings
    from core.version import __version__

    base_dir = str(getattr(settings, "BASE_DIR", os.getcwd()))
    try:
        disk = shutil.disk_usage(base_dir)
        disk_info = {"disk_total_gb": round(disk.total / 1e9, 1), "disk_free_gb": round(disk.free / 1e9, 1)}
    except Exception:
        disk_info = {}
    ram = _total_ram_bytes()
    try:
        from core.services.network_service import get_local_ipv4_addresses
        ips = [i.get("ip") for i in get_local_ipv4_addresses() if i.get("ip")][:6]
    except Exception:
        ips = []
    return {
        "hostname": socket.gethostname()[:80],
        **_os_description(),
        "arch": platform.machine(),
        "cpu": _cpu_model()[:120],
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": round(ram / 1e9, 1) if ram else None,
        **disk_info,
        "python": platform.python_version(),
        "app_version": __version__,
        "timezone": time.strftime("%Z"),
        "local_ips": ips,
        "mac": ":".join(f"{(uuid.getnode() >> s) & 0xff:02x}" for s in range(40, -8, -8)),
        "install_path": base_dir[:200],
        "collected_at": timezone.now().isoformat(),
    }
