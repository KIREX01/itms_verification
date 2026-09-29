"""
Background WSGI Web Server for ITMS Verification Copilot.
Enables Textual TUI to concurrently serve the Mobile Companion PWA over LAN / Hotspot
without writing HTTP access logs to stdout (preventing TUI terminal screen corruption).
"""
import logging
import socket
import threading
from typing import Optional
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.wsgi import get_wsgi_application

logger = logging.getLogger(__name__)

_server_instance: Optional[WSGIServer] = None
_server_thread: Optional[threading.Thread] = None


class SilentWSGIRequestHandler(WSGIRequestHandler):
    """Suppresses stdout access logs to prevent corrupting Textual TUI display."""

    def log_message(self, format, *args):
        # Suppress stdout/stderr log spam during TUI operation
        logger.debug(format, *args)


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Checks if a port is already open and accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def start_background_web_server(host: str = "0.0.0.0", port: int = 8000) -> bool:
    """
    Launches Django's WSGI application in a background daemon thread.
    Returns True if server was started, or True if already running.
    """
    global _server_instance, _server_thread

    if _server_instance is not None:
        return True

    if is_port_in_use(port):
        logger.info("Port %d is already in use; assuming existing server is serving mobile companion.", port)
        return True

    try:
        application = StaticFilesHandler(get_wsgi_application())
        httpd = make_server(host, port, application, handler_class=SilentWSGIRequestHandler)
        _server_instance = httpd

        def _serve():
            try:
                httpd.serve_forever()
            except Exception as exc:
                logger.debug("Background web server stopped: %s", exc)

        _server_thread = threading.Thread(target=_serve, daemon=True, name="ITMS-Background-Web")
        _server_thread.start()
        logger.info("Started background web server on %s:%d", host, port)
        return True
    except Exception as exc:
        logger.warning("Could not start background web server on %s:%d: %s", host, port, exc)
        return False


def stop_background_web_server():
    """Stops the background WSGI web server cleanly."""
    global _server_instance, _server_thread
    if _server_instance:
        try:
            _server_instance.shutdown()
        except Exception:
            pass
        _server_instance = None
    _server_thread = None
