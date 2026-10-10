"""
Network discovery and mobile pairing service for ITMS Verification Copilot.

Discovers local network interfaces (Wi-Fi, Ethernet, Windows Mobile Hotspot, Phone Hotspot)
and generates connection endpoints and mobile access URLs with real-time classification,
including Cloud VPS production domain routing.
"""
import logging
import socket
from typing import Any, Dict, List, Optional

from django.http import HttpRequest

logger = logging.getLogger(__name__)

WINDOWS_HOTSPOT_DEFAULT_IP = "192.168.137.1"


def is_private_or_loopback_ip(ip: str) -> bool:
    """Returns True if the IP address is a private LAN, link-local, or loopback address."""
    if not ip:
        return True
    ip = ip.strip()
    if ip.startswith("127.") or ip.startswith("localhost"):
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("169.254."):
        return True
    # 172.16.0.0 – 172.31.255.255
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            second_octet = int(parts[1])
            if 16 <= second_octet <= 31:
                return True
    return False


def is_cloud_or_domain_host(host: str) -> bool:
    """
    Returns True if the host is a public domain name (e.g. close.kirex.online)
    or a non-private public IP address.
    """
    if not host:
        return False
    host_clean = host.split(":")[0].strip().lower()
    if host_clean in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    # If contains non-digit/dot characters, it's a domain name
    if any(c.isalpha() for c in host_clean):
        return True
    # Otherwise check if it's a public IP
    return not is_private_or_loopback_ip(host_clean)


def classify_interface(ip: str) -> Dict[str, Any]:
    """
    Classifies an IP address into connection category:
    - LAPTOP_HOTSPOT: Windows Mobile Hotspot hosted on the laptop (192.168.137.1)
    - PHONE_HOTSPOT: Laptop connected to smartphone hotspot (Android 192.168.43.*, 192.168.42.*, iOS 172.20.10.*)
    - WIFI_LAN: Standard local Wi-Fi or Ethernet router network
    - LOCALHOST: 127.0.0.1 fallback
    """
    if ip == WINDOWS_HOTSPOT_DEFAULT_IP:
        return {
            "mode": "LAPTOP_HOTSPOT",
            "type": "💻 Laptop Hotspot (Hosted by Laptop)",
            "description": "Hosted by laptop; connect phone to laptop's Wi-Fi network",
            "badge": "Laptop Hotspot (192.168.137.1)",
            "is_hotspot": True,
            "is_laptop_hotspot": True,
            "is_phone_hotspot": False,
        }
    elif ip.startswith("192.168.43.") or ip.startswith("192.168.42."):
        return {
            "mode": "PHONE_HOTSPOT",
            "type": "📱 Phone Hotspot (Android AP)",
            "description": "Laptop connected to Android phone hotspot",
            "badge": "Phone Hotspot (Android)",
            "is_hotspot": True,
            "is_laptop_hotspot": False,
            "is_phone_hotspot": True,
        }
    elif ip.startswith("172.20.10."):
        return {
            "mode": "PHONE_HOTSPOT",
            "type": "📱 Phone Hotspot (iPhone AP)",
            "description": "Laptop connected to iPhone Personal Hotspot",
            "badge": "Phone Hotspot (iPhone)",
            "is_hotspot": True,
            "is_laptop_hotspot": False,
            "is_phone_hotspot": True,
        }
    elif ip.startswith("127."):
        return {
            "mode": "LOCALHOST",
            "type": "Localhost Only",
            "description": "Local loopback; not accessible from phone",
            "badge": "Localhost",
            "is_hotspot": False,
            "is_laptop_hotspot": False,
            "is_phone_hotspot": False,
        }
    else:
        return {
            "mode": "WIFI_LAN",
            "type": "🌐 Shared Wi-Fi LAN",
            "description": "Local Wi-Fi router network; phone and laptop must be on same Wi-Fi",
            "badge": "Wi-Fi LAN",
            "is_hotspot": False,
            "is_laptop_hotspot": False,
            "is_phone_hotspot": False,
        }


def get_local_ipv4_addresses() -> List[Dict[str, Any]]:
    """
    Returns all non-loopback IPv4 addresses found on the local host,
    identifying Laptop Hotspot, Phone Hotspot, and Wi-Fi LAN interfaces.
    """
    results: List[Dict[str, Any]] = []
    seen_ips = set()

    # Priority 1: Check actively routed outbound IP (the interface communicating with gateway/internet)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            route_ip = s.getsockname()[0]
            if route_ip and not route_ip.startswith("127."):
                seen_ips.add(route_ip)
                meta = classify_interface(route_ip)
                results.append({
                    "ip": route_ip,
                    "type": meta["type"],
                    "mode": meta["mode"],
                    "description": meta["description"],
                    "badge": meta["badge"],
                    "is_hotspot": meta["is_hotspot"],
                    "is_laptop_hotspot": meta["is_laptop_hotspot"],
                    "is_phone_hotspot": meta["is_phone_hotspot"],
                    "priority": 1,
                })
    except Exception:
        pass

    # Priority 2: Enumerate other local interfaces from getaddrinfo
    try:
        hostname = socket.gethostname()
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for item in addr_info:
            ip = item[4][0]
            if ip and not ip.startswith("127.") and ip not in seen_ips:
                seen_ips.add(ip)
                meta = classify_interface(ip)
                prio = 2 if meta["mode"] in ("LAPTOP_HOTSPOT", "PHONE_HOTSPOT") else (3 if ip.startswith("192.168.") else 4)
                results.append({
                    "ip": ip,
                    "type": meta["type"],
                    "mode": meta["mode"],
                    "description": meta["description"],
                    "badge": meta["badge"],
                    "is_hotspot": meta["is_hotspot"],
                    "is_laptop_hotspot": meta["is_laptop_hotspot"],
                    "is_phone_hotspot": meta["is_phone_hotspot"],
                    "priority": prio,
                })
    except Exception as exc:
        logger.debug("Error querying hostname socket addresses: %s", exc)

    # Sort results so active routed IP is first, then other candidate interfaces
    results.sort(key=lambda x: (x.get("priority", 99), x.get("ip", "")))

    # Fallback to localhost if no network adapter is connected
    if not results:
        meta = classify_interface("127.0.0.1")
        results.append({
            "ip": "127.0.0.1",
            "type": meta["type"],
            "mode": meta["mode"],
            "description": meta["description"],
            "badge": meta["badge"],
            "is_hotspot": False,
            "is_laptop_hotspot": False,
            "is_phone_hotspot": False,
            "priority": 100,
        })

    return results


def get_mobile_connection_info(
    port: int = 8000,
    ssl_port: int = 443,
    use_https: bool = True,
    request: Optional[HttpRequest] = None,
) -> Dict[str, Any]:
    """
    Returns structured connection metadata for mobile pairing:
    - primary_url: Best URL to open on the mobile browser (HTTPS default for camera & QR)
    - https_primary_url: Direct HTTPS URL (enables mobile camera & QR scanner)
    - http_primary_url: Standard HTTP URL
    - connection_mode: CLOUD_VPS, LAPTOP_HOTSPOT, PHONE_HOTSPOT, or WIFI_LAN
    - connection_badge: Human-readable badge text
    - candidate_urls: List of all valid URLs (for QR display & manual switching)
    - hotspot_detected: True if Windows Mobile Hotspot IP (192.168.137.1) is present
    """
    interfaces = get_local_ipv4_addresses()
    primary = interfaces[0]
    primary_ip = primary["ip"]
    hotspot_detected = any(i.get("is_hotspot") for i in interfaces)
    primary_mode = primary.get("mode", "WIFI_LAN")

    active_scheme = "https" if use_https else "http"
    active_port = ssl_port if use_https else port

    primary_url = f"{active_scheme}://{primary_ip}:{active_port}/mobile/"
    https_primary_url = f"https://{primary_ip}:{ssl_port}/mobile/"
    http_primary_url = f"http://{primary_ip}:{port}/mobile/"

    candidate_urls = [
        {
            "ip": iface["ip"],
            "type": iface["type"],
            "mode": iface.get("mode", "WIFI_LAN"),
            "badge": iface.get("badge", ""),
            "description": iface.get("description", ""),
            "url": f"{active_scheme}://{iface['ip']}:{active_port}/mobile/",
            "https_url": f"https://{iface['ip']}:{ssl_port}/mobile/",
            "http_url": f"http://{iface['ip']}:{port}/mobile/",
            "is_primary": (iface["ip"] == primary_ip),
            "is_hotspot": iface.get("is_hotspot", False),
            "is_laptop_hotspot": iface.get("is_laptop_hotspot", False),
            "is_phone_hotspot": iface.get("is_phone_hotspot", False),
        }
        for iface in interfaces
    ]

    is_cloud = False
    connection_badge = primary.get("badge", "")
    connection_desc = primary.get("description", "")

    # Check incoming request host for Cloud VPS production deployment (e.g. close.kirex.online)
    if request is not None:
        try:
            req_host = request.get_host().strip()
            is_secure_conn = request.is_secure() or (request.META.get("HTTP_X_FORWARDED_PROTO") == "https")
            cloud_scheme = "https" if (is_secure_conn or use_https) else "http"

            if is_cloud_or_domain_host(req_host):
                is_cloud = True
                primary_mode = "CLOUD_VPS"
                primary_ip = req_host.split(":")[0]
                primary_url = f"{cloud_scheme}://{req_host}/mobile/"
                https_primary_url = f"https://{req_host}/mobile/"
                http_primary_url = f"http://{req_host}/mobile/"
                active_scheme = cloud_scheme
                connection_badge = f"Cloud Server ({req_host})"
                connection_desc = "Connected via Cloud VPS. Accessible anywhere over cellular (4G/5G) or Wi-Fi — no shared local network needed!"

                # Unmark previous primary candidates
                for c in candidate_urls:
                    c["is_primary"] = False

                # Insert cloud URL as candidate #1
                candidate_urls.insert(0, {
                    "ip": req_host,
                    "type": "☁️ Cloud Server (Accessible Anywhere)",
                    "mode": "CLOUD_VPS",
                    "badge": connection_badge,
                    "description": connection_desc,
                    "url": primary_url,
                    "https_url": https_primary_url,
                    "http_url": http_primary_url,
                    "is_primary": True,
                    "is_hotspot": False,
                    "is_laptop_hotspot": False,
                    "is_phone_hotspot": False,
                    "is_cloud": True,
                })
        except Exception as exc:
            logger.debug("Could not inspect request host for cloud pairing: %s", exc)

    return {
        "success": True,
        "primary_ip": primary_ip,
        "primary_url": primary_url,
        "https_primary_url": https_primary_url,
        "http_primary_url": http_primary_url,
        "use_https": use_https,
        "scheme": active_scheme,
        "connection_mode": primary_mode,
        "connection_badge": connection_badge,
        "connection_desc": connection_desc,
        "is_cloud": is_cloud,
        "is_laptop_hotspot": primary.get("is_laptop_hotspot", False) if not is_cloud else False,
        "is_phone_hotspot": primary.get("is_phone_hotspot", False) if not is_cloud else False,
        "port": port,
        "ssl_port": ssl_port,
        "active_port": active_port,
        "hotspot_detected": hotspot_detected,
        "hotspot_default_ip": WINDOWS_HOTSPOT_DEFAULT_IP,
        "candidate_urls": candidate_urls,
    }
