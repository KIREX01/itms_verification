"""
Device Session Manager for Mobile Web Companion.
Protects server and laptop compute resources by enforcing limits
on concurrent active mobile companion connections.
"""
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

# Timeout in seconds after which an inactive mobile client is evicted.
DEVICE_SESSION_TTL_SECONDS = getattr(settings, "MOBILE_DEVICE_SESSION_TTL_SECONDS", 20.0)


class MobileDeviceSessionManager:
    """
    Thread-safe registry for connected smartphone companion devices.
    Limits active connections to prevent server/laptop CPU and memory saturation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Map: { device_id: { "device_name": str, "client_ip": str, "mode": str, "last_seen": float, "connected_at": float } }
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_max_allowed(self) -> int:
        """Returns maximum allowed concurrent mobile companion devices."""
        return getattr(settings, "MAX_MOBILE_COMPANION_DEVICES", 2)

    def _prune_expired_sessions(self, now: float) -> None:
        """Evicts sessions that have stopped heartbeating."""
        expired = [
            dev_id for dev_id, data in self._sessions.items()
            if (now - data.get("last_seen", 0)) > DEVICE_SESSION_TTL_SECONDS
        ]
        for dev_id in expired:
            logger.info("Evicting expired mobile session: %s (%s)", dev_id, self._sessions[dev_id].get("device_name"))
            del self._sessions[dev_id]

    def record_heartbeat(
        self,
        device_id: str,
        device_name: str = "Mobile Client",
        client_ip: str = "127.0.0.1",
        mode: str = "conveyor"
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Records or updates a device heartbeat.
        
        Returns:
            Tuple of (allowed: bool, details: dict)
        """
        if not device_id:
            return False, {"error": "Missing device ID", "allowed": False}

        now = time.time()
        max_allowed = self.get_max_allowed()

        with self._lock:
            self._prune_expired_sessions(now)

            # If device is already registered, refresh its lease
            if device_id in self._sessions:
                self._sessions[device_id].update({
                    "device_name": device_name or self._sessions[device_id].get("device_name"),
                    "client_ip": client_ip or self._sessions[device_id].get("client_ip"),
                    "mode": mode or self._sessions[device_id].get("mode"),
                    "last_seen": now,
                })
                return True, {
                    "allowed": True,
                    "device_id": device_id,
                    "active_count": len(self._sessions),
                    "max_allowed": max_allowed,
                    "active_devices": self._list_active_devices_unlocked(),
                }

            # If device is new, check if capacity permits registration
            if len(self._sessions) >= max_allowed:
                active_list = self._list_active_devices_unlocked()
                logger.warning(
                    "Rejecting mobile connection '%s' (%s): device limit reached (%d/%d)",
                    device_id, device_name, len(self._sessions), max_allowed
                )
                return False, {
                    "allowed": False,
                    "error": "DEVICE_LIMIT_EXCEEDED",
                    "message": (
                        f"Server capacity limit reached: {len(self._sessions)} of {max_allowed} "
                        "phones are already connected. Please disconnect other devices."
                    ),
                    "active_count": len(self._sessions),
                    "max_allowed": max_allowed,
                    "active_devices": active_list,
                }

            # Register new session
            self._sessions[device_id] = {
                "device_id": device_id,
                "device_name": device_name or "Smartphone",
                "client_ip": client_ip,
                "mode": mode,
                "connected_at": now,
                "last_seen": now,
            }
            logger.info("Registered new mobile session: %s (%s) from %s", device_id, device_name, client_ip)

            return True, {
                "allowed": True,
                "device_id": device_id,
                "active_count": len(self._sessions),
                "max_allowed": max_allowed,
                "active_devices": self._list_active_devices_unlocked(),
            }

    def disconnect_device(self, device_id: str) -> bool:
        """Explicitly disconnects and unregisters a device."""
        with self._lock:
            if device_id in self._sessions:
                logger.info("Mobile device explicitly disconnected: %s", device_id)
                del self._sessions[device_id]
                return True
        return False

    def get_active_sessions(self) -> Dict[str, Any]:
        """Returns summary of all currently active sessions."""
        now = time.time()
        with self._lock:
            self._prune_expired_sessions(now)
            return {
                "active_count": len(self._sessions),
                "max_allowed": self.get_max_allowed(),
                "devices": self._list_active_devices_unlocked(),
            }

    def _list_active_devices_unlocked(self) -> List[Dict[str, Any]]:
        """Internal helper to format active device list without locking."""
        devices = []
        for dev_id, data in self._sessions.items():
            devices.append({
                "device_id": dev_id,
                "device_name": data.get("device_name", "Smartphone"),
                "client_ip": data.get("client_ip", ""),
                "mode": data.get("mode", "conveyor"),
                "online_seconds": int(time.time() - data.get("connected_at", time.time())),
            })
        return devices


# Global singleton instance
session_manager = MobileDeviceSessionManager()
