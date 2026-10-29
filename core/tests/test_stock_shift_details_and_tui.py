import json
from django.test import TransactionTestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from core.models import StockDelivery, StockDeliveryItem, StockDispatchScan, InstallationKit
from core.views.bond import api_stock_shift_details
from core.services.security_envelope import OperatorRequestEnvelope

User = get_user_model()


class StockShiftDetailsAndTUITest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testoperator", password="password123")
        self.factory = RequestFactory()
        self.envelope = OperatorRequestEnvelope(
            user_id=self.user.id,
            username=self.user.username,
            itms_email="operator@test.ug",
            itms_user_uuid="uuid-1234",
            itms_auth_status="ACTIVE",
            facility_code="AGM",
            request_id="req-test",
            trace_id="trace-test",
            client_ip="127.0.0.1",
            timestamp=timezone.now().timestamp(),
            is_valid=True,
        )

    def test_api_stock_shift_details_with_delivery_item(self):
        """Verify that StockDelivery with items does not raise NameError and returns 200 JSON."""
        delivery = StockDelivery.objects.create(
            target_date_suffix="291026",
            delivery_number="DEL-TEST-001",
            total_plates_count=1,
            supplier="Central Depot",
        )
        StockDeliveryItem.objects.create(
            delivery=delivery,
            registration_number="UMA123PX",
            plate_category="PSV",
        )

        request = self.factory.get("/api/stock/shift/details/?date_suffix=291026")
        request.user = self.user
        request.envelope = self.envelope

        response = api_stock_shift_details(request)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("date_suffix"), "291026")
        self.assertEqual(len(data.get("movements", [])), 1)
        movement = data["movements"][0]
        self.assertEqual(movement.get("type"), "DELIVERY")
        self.assertEqual(movement.get("sample_plates"), "UMA123PX")

    def test_dispatch_table_rendering_handles_duplicate_plates(self):
        """Verify that multiple scans with identical plates render without DuplicateKey crash."""
        StockDispatchScan.objects.create(
            work_date_suffix="291026",
            registration_number="UMA999PY",
            plate_category="PSV",
        )
        StockDispatchScan.objects.create(
            work_date_suffix="291026",
            registration_number="UMA999PY",
            plate_category="PSV",
        )

        from textual.app import App
        from core.tui.stock_pane import StockPane

        class DummyApp(App):
            def compose(self):
                yield StockPane(target_date_suffix="291026")

        import asyncio

        async def run_pane():
            app = DummyApp()
            async with app.run_test() as pilot:
                pane = app.query_one(StockPane)
                pane._render_dispatch_table()
                tbl = pane.query_one("#table-stock-dispatch")
                self.assertEqual(tbl.row_count, 2)

        import os
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        asyncio.run(run_pane())
