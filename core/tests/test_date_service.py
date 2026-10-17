from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from core.models import InstallationOrder, StockDispatchScan, StockDelivery
from core.services import config_service, date_service

User = get_user_model()

class DateServiceTestCase(TestCase):
    def setUp(self):
        # Reset config shift date mode
        config_service.set_setting("sync.shift_target_date_mode", "today")
        config_service.set_setting("sync.manual_target_date", "")
        self.user = User.objects.create_user(username="test_op", password="TestPassword123!")

    def test_parse_date_input_various_formats(self):
        # 6-digit suffix
        self.assertEqual(date_service.parse_date_input("171026"), date(2026, 10, 17))
        self.assertEqual(date_service.parse_date_input("300926"), date(2026, 9, 30))
        
        # DD.MM.YYYY, DD/MM/YYYY, DD-MM-YYYY
        self.assertEqual(date_service.parse_date_input("17.10.2026"), date(2026, 10, 17))
        self.assertEqual(date_service.parse_date_input("17/10/2026"), date(2026, 10, 17))
        self.assertEqual(date_service.parse_date_input("17-10-2026"), date(2026, 10, 17))

        # ISO format
        self.assertEqual(date_service.parse_date_input("2026-10-17"), date(2026, 10, 17))

        # ITMS Order format
        self.assertEqual(date_service.parse_date_input("PO-UMA667PU-171026"), date(2026, 10, 17))
        self.assertEqual(date_service.parse_date_input("#PO-UBB123A-260926"), date(2026, 9, 26))

        # ITMS Timestamp format
        self.assertEqual(date_service.parse_date_input("17.10.2026 - 15:45"), date(2026, 10, 17))

        # Relative keywords
        self.assertEqual(date_service.parse_date_input("TODAY"), date_service.today_kampala())
        self.assertEqual(date_service.parse_date_input("YESTERDAY"), date_service.yesterday_kampala())

        # Empty / ALL
        self.assertIsNone(date_service.parse_date_input("ALL"))
        self.assertIsNone(date_service.parse_date_input(""))
        self.assertIsNone(date_service.parse_date_input(None))

    def test_bidirectional_conversions(self):
        d = date(2026, 10, 17)
        suffix = date_service.format_date_suffix(d)
        self.assertEqual(suffix, "171026")
        readable = date_service.format_date_suffix_readable(suffix)
        self.assertEqual(readable, "17.10.2026")
        iso = date_service.format_iso_date(suffix)
        self.assertEqual(iso, "2026-10-17")

    def test_active_shift_modes(self):
        # 1. Mode = today
        config_service.set_setting("sync.shift_target_date_mode", "today")
        today_d = date_service.today_kampala()
        self.assertEqual(date_service.get_active_shift_date(), today_d)
        self.assertEqual(date_service.get_active_shift_suffix(), today_d.strftime("%d%m%y"))

        # 2. Mode = yesterday
        config_service.set_setting("sync.shift_target_date_mode", "yesterday")
        yesterday_d = date_service.yesterday_kampala()
        self.assertEqual(date_service.get_active_shift_date(), yesterday_d)
        self.assertEqual(date_service.get_active_shift_suffix(), yesterday_d.strftime("%d%m%y"))

        # 3. Mode = manual
        config_service.set_setting("sync.shift_target_date_mode", "manual")
        config_service.set_setting("sync.manual_target_date", "30.09.2026")
        self.assertEqual(date_service.get_active_shift_date(), date(2026, 9, 30))
        self.assertEqual(date_service.get_active_shift_suffix(), "300926")

        info = date_service.get_active_shift_info()
        self.assertEqual(info["mode"], "manual")
        self.assertEqual(info["suffix"], "300926")
        self.assertEqual(info["formatted_date"], "30.09.2026")

    def test_api_shift_date_endpoints(self):
        client = Client()
        client.force_login(self.user)

        # GET
        res_get = client.get("/api/settings/shift-date/")
        self.assertEqual(res_get.status_code, 200)
        data_get = res_get.json()
        self.assertTrue(data_get["success"])
        self.assertIn("active_shift", data_get)
        self.assertIn("available_dates", data_get)

        # POST 'yesterday'
        res_post1 = client.post(
            "/api/settings/shift-date/",
            data='{"target_date": "yesterday"}',
            content_type="application/json"
        )
        self.assertEqual(res_post1.status_code, 200)
        data_post1 = res_post1.json()
        self.assertTrue(data_post1["success"])
        self.assertEqual(data_post1["active_shift"]["mode"], "yesterday")

        # POST specific shift date '081026'
        res_post2 = client.post(
            "/api/settings/shift-date/",
            data='{"target_date": "081026"}',
            content_type="application/json"
        )
        self.assertEqual(res_post2.status_code, 200)
        data_post2 = res_post2.json()
        self.assertTrue(data_post2["success"])
        self.assertEqual(data_post2["active_shift"]["suffix"], "081026")
        self.assertEqual(data_post2["active_shift"]["formatted_date"], "08.10.2026")

        # POST reset to 'today'
        res_post3 = client.post(
            "/api/settings/shift-date/",
            data='{"target_date": "today"}',
            content_type="application/json"
        )
        self.assertEqual(res_post3.status_code, 200)
        data_post3 = res_post3.json()
        self.assertTrue(data_post3["success"])
        self.assertEqual(data_post3["active_shift"]["mode"], "today")
