"""Asks Chromium browsers for detailed User-Agent Client Hints (exact OS version, phone model, CPU
architecture) so device records are accurate. Browsers that don't support hints ignore the header."""
from typing import Callable

from django.http import HttpRequest, HttpResponse

from core.services.device_info_service import ACCEPT_CH


class ClientHintsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not request.path.startswith("/static/"):
            response["Accept-CH"] = ACCEPT_CH
            vary = response.get("Vary", "")
            if "Sec-CH-UA" not in vary:
                response["Vary"] = f"{vary}, Sec-CH-UA" if vary else "Sec-CH-UA"
        return response
