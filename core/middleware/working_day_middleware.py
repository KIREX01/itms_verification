"""Gives every web request the person's own working day (from their session)."""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse

from core.services import date_service


class WorkingDayMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        day = None
        session = getattr(request, "session", None)
        if session is not None:
            raw = session.get(date_service.WORKING_DAY_SESSION_KEY)
            parsed = date_service.parse_date_input(raw) if raw else None
            if parsed:
                day = date_service.clamp_working_day(parsed)
        tokens = date_service.set_request_working_day(day)
        try:
            return self.get_response(request)
        finally:
            date_service.reset_request_working_day(tokens)
