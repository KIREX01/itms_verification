"""v1.3.2: per-person working day, date-first UI, role-trimmed screens, icons, optional device names."""
import json
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from core.services import config_service, date_service

STATIC = Path(settings.BASE_DIR) / "core" / "static" / "core"


def _client(username):
    user = User.objects.create_user(username, f"{username}@example.com", "Str0ng-pass!x")
    c = Client()
    c.force_login(user)
    return c


class WorkingDayTests(TestCase):
    def setUp(self):
        cache.clear()

    def _set(self, c, value):
        return c.post("/api/settings/shift-date/", data=json.dumps({"target_date": value}),
                      content_type="application/json")

    def test_day_is_per_person_and_never_touches_the_server_default(self):
        before = config_service.get_setting("sync.shift_target_date_mode", "today")
        a, b = _client("person_a"), _client("person_b")
        day = (date_service.today_kampala() - timedelta(days=3)).isoformat()
        r = self._set(a, day)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["active_shift"]["date"], day)
        self.assertEqual(a.get("/api/settings/shift-date/").json()["active_shift"]["date"], day)
        self.assertEqual(b.get("/api/settings/shift-date/").json()["active_shift"]["date"],
                         date_service.today_kampala().isoformat())
        self.assertEqual(config_service.get_setting("sync.shift_target_date_mode", "today"), before)

    def test_all_dates_rejected_and_day_clamped(self):
        c = _client("person_c")
        self.assertEqual(self._set(c, "ALL").status_code, 400)
        far = (date_service.today_kampala() + timedelta(days=400)).isoformat()
        got = self._set(c, far).json()["active_shift"]["date"]
        self.assertEqual(got, (date_service.today_kampala() + timedelta(days=date_service.WORKING_DAY_FUTURE_DAYS)).isoformat())
        self.assertEqual(self._set(c, "today").json()["active_shift"]["is_today"], True)

    def test_outside_requests_use_the_configured_default(self):
        with mock.patch.object(config_service, "get_setting", side_effect=lambda k, d=None, *a: "yesterday"
                               if k == "sync.shift_target_date_mode" else d):
            self.assertEqual(date_service.get_active_shift_date(), date_service.yesterday_kampala())
        tokens = date_service.set_request_working_day(None)
        try:
            self.assertEqual(date_service.get_active_shift_date(), date_service.today_kampala())
        finally:
            date_service.reset_request_working_day(tokens)

    def test_history_follows_the_working_day(self):
        c = _client("person_d")
        self._set(c, (date_service.today_kampala() - timedelta(days=1)).isoformat())
        self.assertEqual(c.get("/api/history/?filter=ALL&date_scope=TODAY").status_code, 200)


class DateFirstLayoutTests(TestCase):
    def test_day_bar_first_and_old_date_controls_gone(self):
        c = _client("tl_user")
        html = c.get("/").content.decode()
        self.assertIn('id="day-bar"', html)
        main = html.split('<main class="app-workspace">', 1)[1]
        self.assertLess(main.index('id="day-bar"'), main.index('id="pane-dashboard"'))
        for old in ("header-global-shift-date", "drawer-global-shift-select", "btn-dash-mode-all", "btn-bscope-all",
                    "btn-hscope-all", "btn-toggle-report-scope", "stockPrevDay", "settings-shift-mode-select"):
            self.assertNotIn(f'id="{old}"', html, old)
            self.assertNotIn(f'onclick="{old}', html, old)
        self.assertIn("day_bar.js", html)

    def test_icons_shipped_with_licence(self):
        for name in ("add", "edit", "delete", "view", "yes", "no", "alert", "calendar", "clock", "search"):
            self.assertTrue((STATIC / "icons" / f"{name}.svg").is_file(), name)
        self.assertTrue((STATIC / "icons" / "LICENSE").is_file())
        css = (STATIC / "css" / "app_layout.css").read_text(encoding="utf-8")
        self.assertIn(".ico-edit", css)


class RoleGatingTests(TestCase):
    def test_every_gated_action_exists_and_uses_known_capabilities(self):
        from governance.capabilities import ALL_CAPABILITIES
        js = (STATIC / "js" / "governance.js").read_text(encoding="utf-8")
        block = js.split("const ACTION_CAPS = {", 1)[1].split("};", 1)[0]
        pairs = re.findall(r"(\w+):\s*\[([^\]]*)\]", block)
        self.assertGreater(len(pairs), 50)
        caps = set(ALL_CAPABILITIES) | {"platform.admin"}
        for fn, raw in pairs:
            for cap in re.findall(r'"([a-z_.]+)"', raw):
                self.assertIn(cap, caps, f"{fn} -> {cap}")

    def test_view_only_chips_present(self):
        html = _client("tl_user2").get("/").content.decode()
        self.assertIn('data-cap-missing="queue.edit queue.approve"', html)
        self.assertIn('data-cap-missing="stock.edit stock.scan"', html)


@override_settings(DEPLOYMENT_ROLE="central", CLOSE_DEVICE_ADMISSION=True)
class OptionalDeviceNamesTests(TestCase):
    def test_email_alone_is_enough_and_names_still_checked_when_given(self):
        cache.clear()
        c = Client()
        with mock.patch("core.services.email_service.send_device_code_email", return_value=(True, "")) as send:
            r = c.post("/device/", {"action": "send", "email": "solo@gmail.com"})
        self.assertRedirects(r, "/device/?step=code", fetch_redirect_response=False)
        send.assert_called_once()
        r = Client().post("/device/", {"action": "send", "email": "x@gmail.com", "first_name": "1"})
        self.assertEqual(r.status_code, 403)
        page = Client().get("/").content.decode()
        self.assertIn("(optional)", page)

    def test_no_names_means_signup_asks_for_them(self):
        cache.clear()
        c = Client()
        with mock.patch("core.services.email_service.send_device_code_email", return_value=(True, "")) as send:
            c.post("/device/", {"action": "send", "email": "solo2@gmail.com"})
        c.post("/device/", {"action": "verify", "code": send.call_args.kwargs["code"]})
        page = c.get("/signup/").content.decode()
        self.assertIn('id="first_name" name="first_name" value=""', page)
