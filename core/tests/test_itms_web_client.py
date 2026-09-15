"""
Unit tests for ITMSWebClient & Yii2 session management.
"""
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.services.itms_web_client import (
    ITMSWebClient,
    ITMSWebSessionData,
    ITMSWebSessionStore,
)


class ITMSWebSessionStoreTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmp_dir.name) / ".test_itms_session.json"
        self.store = ITMSWebSessionStore(storage_path=self.session_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_load_session(self):
        data = ITMSWebSessionData(
            base_url="https://stock.itms.ug",
            user_email="operator@example.com",
            user_uuid="11111111-2222-3333-4444-555555555555",
            cookies={"_identity-frontend": "dummy-val", "advanced-frontend": "sess-1"},
            csrf_token="csrf-abc",
            is_authenticated=True,
            saved_at=time.time(),
            expires_at=time.time() + 3600,
            last_verified_at=time.time(),
            last_status_message="Active",
        )
        self.store.save(data)
        self.assertTrue(self.session_file.is_file())

        new_store = ITMSWebSessionStore(storage_path=self.session_file)
        loaded = new_store.load()
        self.assertEqual(loaded.user_email, "operator@example.com")
        self.assertEqual(loaded.user_uuid, "11111111-2222-3333-4444-555555555555")
        self.assertTrue(loaded.is_authenticated)
        self.assertTrue(loaded.is_cookie_valid())
        self.assertTrue(loaded.is_recently_verified(max_age_seconds=60))

    def test_clear_session(self):
        data = ITMSWebSessionData(
            user_email="test@example.com",
            cookies={"_identity-frontend": "tok"},
            is_authenticated=True,
        )
        self.store.save(data)
        self.store.clear()
        self.assertFalse(self.session_file.is_file())
        self.assertFalse(self.store.session.is_authenticated)


class ITMSWebClientTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmp_dir.name) / ".test_itms_session.json"
        self.store = ITMSWebSessionStore(storage_path=self.session_file)
        self.client = ITMSWebClient(base_url="https://stock.itms.ug", session_store=self.store)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("requests.Session.get")
    def test_test_connection_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Server": "nginx"}
        mock_get.return_value = mock_resp

        res = self.client.test_connection()
        self.assertTrue(res["success"])
        self.assertEqual(res["status_code"], 200)
        self.assertEqual(res["server"], "nginx")

    @patch("requests.Session")
    def test_login_success_302(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # Mock GET /site/login to return form with CSRF token
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.text = '<input type="hidden" name="_csrf-frontend" value="token123">'
        mock_session.get.return_value = mock_get_resp

        # Mock POST /site/login to return 302 redirect with identity cookie
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 302
        mock_post_resp.headers = {"Location": "https://stock.itms.ug/"}
        mock_session.post.return_value = mock_post_resp

        # Mock session cookies
        mock_cookies = {
            "_csrf-frontend": "csrf_cookie_val",
            "advanced-frontend": "adv_cookie_val",
            "_identity-frontend": 'edcdc7b8cba54e4aa2a716c19de46acbf0bc25ad83bf9e9657d64259ce61d7e9a%3A2%3A%7Bi%3A0%3Bs%3A18%3A%22_identity-frontend%22%3Bi%3A1%3Bs%3A83%3A%22%5B%2211111111-2222-3333-4444-555555555555%22%2C%22A2dljxuDH5Ch-JeKoGKcLUP_PbRMytC1%22%2C2592000%5D%22%3B%7D',
        }
        mock_session.cookies.get_dict.return_value = mock_cookies
        mock_session.cookies.get.side_effect = lambda k, default="": mock_cookies.get(k, default)

        ok, msg, sess = self.client.login("operator@example.com", "secretpass")
        self.assertTrue(ok)
        self.assertIn("Successfully authenticated", msg)
        self.assertEqual(sess["user_uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(sess["user_email"], "operator@example.com")

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_login_failure_invalid_credentials(self, mock_get, mock_post):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.text = '<input type="hidden" name="_csrf-frontend" value="token123">'
        mock_get.return_value = mock_get_resp

        # Mock POST /site/login returning 200 with invalid-feedback
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.text = '<div class="invalid-feedback">Incorrect username or password.</div>'
        mock_post.return_value = mock_post_resp

        ok, msg, sess = self.client.login("user@bad.com", "wrongpass")
        self.assertFalse(ok)
        self.assertEqual(msg, "Incorrect username or password.")
        self.assertEqual(sess, {})

    def test_verify_session_cooldown_rate_limit(self):
        # Save an active session verified 10 seconds ago
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            last_verified_at=time.time() - 10,
            expires_at=time.time() + 86400,
        ))

        # verify_session without force should return cached result without network request
        with patch("requests.Session.get") as mock_get:
            res = self.client.verify_session(force=False, max_age_seconds=300)
            self.assertTrue(res["valid"])
            self.assertTrue(res["cached"])
            mock_get.assert_not_called()

    @patch("requests.Session.get")
    def test_fetch_read_only_dashboard(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            user_uuid="11111111-2222-3333-4444-555555555555",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Date": "Tue, 08 Sep 2026 19:14:16 GMT"}
        mock_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>ITMS - Dashboard</title></head>
        <body>
            <a href="/installation-orders/index">Installation Orders</a>
            <a href="/installation-kits">Installation Kits</a>
            <a href="/user/11111111-2222-3333-4444-555555555555/main/information">Profile</a>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_read_only_dashboard()
        self.assertTrue(res["success"])
        self.assertEqual(res["page_title"], "ITMS - Dashboard")
        self.assertEqual(res["user_uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(len(res["accessible_modules"]), 3)

    @patch("requests.Session.get")
    def test_fetch_installation_orders_and_sync(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>ITMS - Installation orders</title></head>
        <body>
            <table class="table table-bordered table-hover">
            <thead>
                <tr>
                    <th>#</th><th>Sales Order</th><th>Service type</th><th>VIN</th>
                    <th>Registration Number</th><th>Old Reg</th><th>Warehouse</th>
                    <th>Status</th><th>Officer</th><th>Date</th>
                </tr>
            </thead>
            <tbody>
                <tr data-url="/installation-orders/installation?id=a2403a81-e499-4ce8-a125-0df0be644e74" data-key="a2403a81">
                    <td>PO-UMA835DS-030926</td><td>S-029225</td><td>First Time Registration</td>
                    <td>LC6PCJBJ8S0052136</td><td>UMA 835DS</td><td>-</td>
                    <td>EAGLE GENERAL TRADERS</td><td>Under installation</td><td>-</td><td>-</td>
                </tr>
                <tr data-url="/installation-orders/approve?id=296f8002-c1a0-4a12-a8a2-18efc7c8f401" data-key="296f8002">
                    <td>PO-UMA946DQ-030926</td><td>S-029225</td><td>First Time Registration</td>
                    <td>LC6PCJBJ3S0052092</td><td>UMA 946DQ</td><td>-</td>
                    <td>EAGLE GENERAL TRADERS</td><td>Ready for approve</td><td>-</td><td>-</td>
                </tr>
            </tbody>
            </table>
            <ul class="pagination">
                <li class="page-item"><a href="/installation-orders/index?page=2">2</a></li>
            </ul>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_installation_orders(page=1)
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 2)
        self.assertTrue(res["has_next_page"])

        orders = res["orders"]
        self.assertEqual(orders[0]["order_number"], "PO-UMA835DS-030926")
        self.assertEqual(orders[0]["registration_number"], "UMA 835DS")
        self.assertEqual(orders[0]["vin"], "LC6PCJBJ8S0052136")
        self.assertEqual(orders[0]["order_key"], "a2403a81-e499-4ce8-a125-0df0be644e74")

        # Test sync to local database
        from core.models import InstallationOrder
        sync_res = self.client.sync_orders_to_local_db(orders)
        self.assertEqual(sync_res["total"], 2)

        order_in_db = InstallationOrder.objects.get(order_number="PO-UMA835DS-030926")
        self.assertEqual(order_in_db.registration_number, "UMA835DS")
        self.assertEqual(order_in_db.status, InstallationOrder.Status.PENDING)

    @patch("requests.Session.get")
    def test_fetch_installation_orders_with_url_and_unspaced_plate(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><table></table></body></html>"
        mock_get.return_value = mock_resp

        # Test passing full URL
        test_url = "https://stock.itms.ug/installation-orders/index?InstallationOrderSearch%5Bservice_type%5D=&InstallationOrderSearch%5Bregistration_number%5D=UMA946DQ&InstallationOrderSearch%5Bwarehouse_id%5D=&InstallationOrderSearch%5Bvin%5D=&InstallationOrderSearch%5Bold_registration_number%5D=&InstallationOrderSearch%5Bstatus%5D="
        res = self.client.fetch_installation_orders(search_params=test_url)
        self.assertTrue(res["success"])

        # Verify that the outgoing request had formatted plate "UMA 946DQ"
        args, kwargs = mock_get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params.get("InstallationOrderSearch[registration_number]"), "UMA 946DQ")

    @patch("requests.Session.get")
    def test_fetch_archive_orders_and_sync_installed(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>ITMS - Installation orders (Archive)</title></head>
        <body>
            <table class="table table-bordered table-hover">
            <thead>
                <tr>
                    <th>#</th><th>Sales Order</th><th>Service type</th><th>VIN</th>
                    <th>Registration Number</th><th>Old Reg</th><th>Warehouse</th>
                    <th>Order Status</th><th>Registration Status</th><th>Officer</th><th>Date</th><th></th>
                </tr>
            </thead>
            <tbody>
                <tr data-url="/installation-orders/info?id=edb3ecd7-8d46-4424-9ed5-0c6dd5400efa" data-key="edb3ecd7">
                    <td>PO-UMA633PG-080926</td><td>-</td><td>First Time Registration</td>
                    <td>LC6PCJBJ7T0A94706</td><td>UMA 633PG</td><td>-</td>
                    <td>EAGLE GENERAL TRADERS</td><td>Installed</td><td>Active</td>
                    <td>TEST OFFICER</td><td>08.09.2026 - 15:05</td><td></td>
                </tr>
            </tbody>
            </table>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        # Create a pending VehicleInstallationPair in local db to test cross-verification
        from core.models import InstallationOrder, VehicleInstallationPair, SubmissionAuditLog
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA633PG",
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        res = self.client.fetch_archive_orders(page=1)
        self.assertTrue(res["success"])
        self.assertTrue(res["is_archive"])
        self.assertEqual(res["count"], 1)

        orders = res["orders"]
        self.assertEqual(orders[0]["order_number"], "PO-UMA633PG-080926")
        self.assertEqual(orders[0]["order_status"], "Installed")
        self.assertEqual(orders[0]["registration_status"], "Active")
        self.assertEqual(orders[0]["officer"], "TEST OFFICER")

        # Sync to local DB
        sync_res = self.client.sync_orders_to_local_db(orders)
        self.assertEqual(sync_res["total"], 1)
        self.assertEqual(sync_res["installed_verified"], 1)

        db_order = InstallationOrder.objects.get(order_number="PO-UMA633PG-080926")
        self.assertEqual(db_order.status, InstallationOrder.Status.INSTALLED)
        self.assertTrue(db_order.is_archived)
        self.assertEqual(db_order.order_status, "Installed")
        self.assertEqual(db_order.registration_status, "Active")
        self.assertEqual(db_order.installation_officer, "TEST OFFICER")

        # Check that the pair was cross-verified and audited
        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)
        self.assertEqual(pair.order, db_order)
        audit_log = SubmissionAuditLog.objects.filter(pair=pair, action=SubmissionAuditLog.Action.ARCHIVE_VERIFY).first()
        self.assertIsNotNone(audit_log)
        self.assertIn("Confirmed Installed in ITMS Archive", audit_log.message)

    def test_parse_order_info_html(self):
        """Tests parsing order details, serials, and photo links from recorded ITMS response."""
        recorded_path = Path("stock.itms.ug/GET_stock_itms_ug__installation_orders_info_20260910024652.txt")
        if not recorded_path.is_file():
            self.skipTest("Recorded order info file not found")

        with open(recorded_path, "r", encoding="utf-8") as f:
            content = f.read()

        parts = content.split("RESPONSE", 1)
        html = parts[1] if len(parts) > 1 else content

        res = self.client.parse_order_info_html(html)

        self.assertEqual(res["order_number"], "PO-UMA282PG-080926")
        self.assertEqual(res["installed_by"], "TEST OFFICER")
        self.assertIn("EAGLE", res["warehouse"])
        self.assertEqual(res["vin"], "LC6PCJBJ7T0A11226")
        self.assertEqual(res["registration_number"], "UMA 282PG")

        # Hardware Inventory
        self.assertEqual(res["front_plate"]["type"], "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(res["front_plate"]["serial"], "001107363")
        self.assertEqual(res["front_plate"]["plate"], "UMA 282PG")

        self.assertEqual(res["rear_plate"]["type"], "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(res["rear_plate"]["serial"], "001107364")
        self.assertEqual(res["rear_plate"]["plate"], "UMA 282PG")

        self.assertEqual(res["gps_tracker"]["device_id"], "8BAE47076F84")
        self.assertEqual(res["front_beacon"]["device_id"], "8ADC470F6032")
        self.assertEqual(res["rear_beacon"]["device_id"], "8ADC470F1A9E")

        # Photos
        self.assertEqual(len(res["photos"]), 2)
        self.assertIn("TjVwNKfbm3.jpeg", res["front_photo_url"])
        self.assertIn("Rsv79gdc-4.jpeg", res["rear_photo_url"])
        self.assertEqual(res["photos"][0]["orientation"], "FRONT")
        self.assertEqual(res["photos"][1]["orientation"], "REAR")

    @patch("requests.Session.get")
    def test_fetch_order_info_and_sync_db(self, mock_get):
        """Tests fetching order info via UUID and synchronizing full metadata to local DB."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "valid-token"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
        <body>
            <h4 class="heading-title">Installation order: #PO-UMA999ZZ-090926</h4>
            <p class="mb-0"><strong>Installed By</strong>:&ensp;TEST OFFICER</p>
            <p class="mb-0"><strong>Installation Warehouse</strong>:&ensp;KAMPALA MAIN DEPOT</p>
            <p class="mb-0"><strong>Vehicle VIN/Chassis No.</strong>:&ensp;VIN999888777</p>
            <p class="mb-0"><strong>Front license plate</strong>:&ensp;PN-TEST (009990001) (UMA 999ZZ)</p>
            <p class="mb-0"><strong>Rear license plate</strong>:&ensp;PN-TEST (009990002) (UMA 999ZZ)</p>
            <p class="mb-0"><strong>GPS Tracker</strong>:&ensp;GPS (GPS999)</p>
            <p class="mb-0"><strong>Front beacon</strong>:&ensp;BLE (BLE-FRONT-1)</p>
            <p class="mb-0"><strong>Rear beacon</strong>:&ensp;BLE (BLE-REAR-2)</p>
            <div class="col">
                <strong>Front plate number</strong>
                <div class="plate-photo"><img src="/storage/photos/front999.jpeg"></div>
            </div>
            </div>
            <div class="col">
                <strong>Rear plate number</strong>
                <div class="plate-photo"><img src="/storage/photos/rear999.jpeg"></div>
            </div>
            </div>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        order_uuid = "11111111-2222-3333-4444-555555555555"
        res = self.client.fetch_order_info(order_uuid)
        self.assertTrue(res["success"])
        self.assertEqual(res["order_number"], "PO-UMA999ZZ-090926")
        self.assertEqual(res["registration_number"], "UMA 999ZZ")
        self.assertEqual(res["front_plate"]["serial"], "009990001")
        self.assertEqual(res["rear_plate"]["serial"], "009990002")
        self.assertEqual(len(res["photos"]), 2)

        # Sync to DB
        from core.models import InstallationOrder
        sync_res = self.client.sync_order_info_to_local_db(res, order_uuid=order_uuid)
        self.assertTrue(sync_res["success"])
        self.assertTrue(sync_res["created"])

        order = InstallationOrder.objects.get(order_number="PO-UMA999ZZ-090926")
        self.assertEqual(order.registration_number, "UMA999ZZ")
        self.assertEqual(order.front_plate_serial, "009990001")
        self.assertEqual(order.rear_plate_serial, "009990002")
        self.assertEqual(order.gps_tracker_id, "GPS999")
        self.assertEqual(order.front_beacon_id, "BLE-FRONT-1")
        self.assertEqual(order.rear_beacon_id, "BLE-REAR-2")
        self.assertIn("front999.jpeg", order.front_photo_url)
        self.assertIn("rear999.jpeg", order.rear_photo_url)
        self.assertIsNotNone(order.info_fetched_at)
        self.assertEqual(len(order.photos_json), 2)

    def test_parse_installation_page_html(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="csrf-param" content="_csrf-frontend">
            <meta name="csrf-token" content="test-csrf-token-123">
        </head>
        <body>
            <ol class="breadcrumb">
                <li class="breadcrumb-item active">PO-UMA560PJ-100926</li>
            </ol>
            <form id="installationOrderCreateForm" action="/installation-orders/installation?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post">
                <input type="hidden" name="_csrf-frontend" value="test-csrf-token-123">
                <dl><dt>Vehicle VIN/Chassis No.</dt><dd>LC6PCJBJXT0B13846</dd></dl>
                <dl><dt>Old plate number</dt><dd><i class="text-danger fas fa-minus"></i></dd></dl>
                <dl><dt>Registration Number</dt><dd>UMA 560PJ</dd></dl>

                <select id="installationorderform-front_license_plate_id" name="InstallationOrderForm[front_license_plate_id]">
                    <option value="">-- Choose --</option>
                    <option value="front-plate-uuid-1" selected>001102336</option>
                </select>

                <select id="installationorderform-back_license_plate_id" name="InstallationOrderForm[back_license_plate_id]">
                    <option value="">-- Choose --</option>
                    <option value="rear-plate-uuid-2" selected>001102337</option>
                </select>

                <select id="installationorderform-tracker_id" name="InstallationOrderForm[tracker_id]">
                    <option value="">-- Choose --</option>
                    <option value="tracker-uuid-3" selected>8BAE470773A4</option>
                </select>

                <span id="frontBeaconIdStub">8ADC470F69D2</span>
                <span id="backBeaconIdStub">8ADC470F537C</span>
            </form>
            <script>
            jQuery('#installationOrderCreateForm').yiiActiveForm([], {"validationUrl":"\\/installation-orders\\/validate-installation?id=b4579023-fab3-4e19-9990-824fefc1aac0"});
            </script>
        </body>
        </html>
        """
        parsed = self.client.parse_installation_page_html(sample_html)
        self.assertEqual(parsed["order_number"], "PO-UMA560PJ-100926")
        self.assertEqual(parsed["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertEqual(parsed["csrf_token"], "test-csrf-token-123")
        self.assertEqual(parsed["vin"], "LC6PCJBJXT0B13846")
        self.assertEqual(parsed["registration_number"], "UMA 560PJ")
        self.assertEqual(parsed["front_plate"]["selected_id"], "front-plate-uuid-1")
        self.assertEqual(parsed["front_plate"]["selected_text"], "001102336")
        self.assertEqual(parsed["rear_plate"]["selected_id"], "rear-plate-uuid-2")
        self.assertEqual(parsed["rear_plate"]["selected_text"], "001102337")
        self.assertEqual(parsed["tracker"]["selected_id"], "tracker-uuid-3")
        self.assertEqual(parsed["tracker"]["selected_text"], "8BAE470773A4")
        self.assertEqual(parsed["front_beacon"], "8ADC470F69D2")
        self.assertEqual(parsed["rear_beacon"], "8ADC470F537C")
        self.assertEqual(parsed["validation_url"], "/installation-orders/validate-installation?id=b4579023-fab3-4e19-9990-824fefc1aac0")

    @patch("requests.Session.get")
    def test_fetch_installation_step1(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <form id="installationOrderCreateForm" action="/installation-orders/installation?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post">
            <input type="hidden" name="_csrf-frontend" value="csrf-token-abc">
            <dl><dt>Registration Number</dt><dd>UMA 560PJ</dd></dl>
        </form>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_installation_step1("b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertTrue(res["success"])
        self.assertEqual(res["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertEqual(res["registration_number"], "UMA 560PJ")

    @patch("requests.Session.post")
    def test_validate_installation_step1_valid(self, mock_post):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            csrf_token="csrf-test-token",
            expires_at=time.time() + 86400,
        ))

        # Yii2 ActiveForm returns [] when validation succeeds
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_post.return_value = mock_resp

        res = self.client.validate_installation_step1(
            order_uuid="b4579023-fab3-4e19-9990-824fefc1aac0",
            front_plate_id="front-1",
            back_plate_id="rear-2",
            tracker_id="tracker-3",
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["valid"])
        self.assertEqual(res["errors"], {})

        # Verify POST called with AJAX header and payload
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(call_args[1]["data"]["ajax"], "installationOrderCreateForm")
        self.assertEqual(call_args[1]["data"]["InstallationOrderForm[front_license_plate_id]"], "front-1")

    @patch("requests.Session.post")
    def test_validate_installation_step1_invalid(self, mock_post):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "installationorderform-front_license_plate_id": ["Front License Plate cannot be blank."]
        }
        mock_post.return_value = mock_resp

        res = self.client.validate_installation_step1(
            order_uuid="b4579023-fab3-4e19-9990-824fefc1aac0",
            front_plate_id="",
            back_plate_id="rear-2",
            tracker_id="tracker-3",
        )
        self.assertTrue(res["success"])
        self.assertFalse(res["valid"])
        self.assertIn("installationorderform-front_license_plate_id", res["errors"])

    @patch("requests.Session.post")
    def test_submit_installation_step1_success_302(self, mock_post):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            csrf_token="csrf-val-xyz",
            expires_at=time.time() + 86400,
        ))

        # First mock response is for AJAX validate-installation ([]), second is 302 redirect for /installation
        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = []

        mock_sub_resp = MagicMock()
        mock_sub_resp.status_code = 302
        mock_sub_resp.headers = {
            "Location": "https://stock.itms.ug/installation-orders/approve?id=b4579023-fab3-4e19-9990-824fefc1aac0"
        }
        mock_post.side_effect = [mock_val_resp, mock_sub_resp]

        res = self.client.submit_installation_step1(
            order_uuid="b4579023-fab3-4e19-9990-824fefc1aac0",
            front_plate_id="uuid-front",
            back_plate_id="uuid-rear",
            tracker_id="uuid-tracker",
            just_save=False,
            run_validation_first=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["step2_ready"])
        self.assertIn("/installation-orders/approve", res["redirect_url"])

    def test_sync_installation_step1_to_local_db(self):
        from core.models import InstallationOrder, VehicleInstallationPair

        step1_data = {
            "order_number": "PO-UMA560PJ-100926",
            "order_uuid": "b4579023-fab3-4e19-9990-824fefc1aac0",
            "registration_number": "UMA 560PJ",
            "vin": "LC6PCJBJXT0B13846",
            "front_plate": {"selected_id": "fp-1", "selected_text": "001102336"},
            "rear_plate": {"selected_id": "rp-2", "selected_text": "001102337"},
            "tracker": {"selected_id": "tr-3", "selected_text": "8BAE470773A4"},
            "front_beacon": "8ADC470F69D2",
            "rear_beacon": "8ADC470F537C",
        }

        # Create pair to test auto-linking
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA560PJ",
        )

        res = self.client.sync_installation_step1_to_local_db(step1_data)
        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertEqual(res["pairs_updated"], 1)

        order = InstallationOrder.objects.get(order_number="PO-UMA560PJ-100926")
        self.assertEqual(order.registration_number, "UMA560PJ")
        self.assertEqual(order.vin, "LC6PCJBJXT0B13846")
        self.assertEqual(order.front_plate_serial, "001102336")
        self.assertEqual(order.rear_plate_serial, "001102337")
        self.assertEqual(order.gps_tracker_id, "8BAE470773A4")
        self.assertEqual(order.front_beacon_id, "8ADC470F69D2")
        self.assertEqual(order.rear_beacon_id, "8ADC470F537C")

        pair.refresh_from_db()
        self.assertEqual(pair.order, order)

    def test_parse_approve_page_html(self):
        sample_html = """
        <html>
        <head>
            <meta name="csrf-token" content="csrf-token-step2-xyz">
        </head>
        <body>
            <div class="alert-success">Installation order created successfully</div>
            <ol class="breadcrumb">
                <li class="breadcrumb-item active">PO-UMA560PJ-100926</li>
            </ol>
            <form id="w3" action="/installation-orders/approve?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post" enctype="multipart/form-data">
                <input type="hidden" name="_csrf-frontend" value="csrf-token-step2-xyz">
                <input type="hidden" id="vehicleTypeField" name="vehicle_type" value="M">
                <input type="file" name="InstallationOrderApproveForm[front_plate]">
                <input type="file" name="InstallationOrderApproveForm[rear_plate]">
                <input type="file" name="InstallationOrderApproveForm[checklist]">
            </form>
        </body>
        </html>
        """
        parsed = self.client.parse_approve_page_html(sample_html)
        self.assertEqual(parsed["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertEqual(parsed["order_number"], "PO-UMA560PJ-100926")
        self.assertEqual(parsed["csrf_token"], "csrf-token-step2-xyz")
        self.assertEqual(parsed["vehicle_type"], "M")
        self.assertEqual(parsed["alert_message"], "Installation order created successfully")
        self.assertFalse(parsed["requires_checklist"])

    @patch("requests.Session.get")
    def test_fetch_approve_step2(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <form action="/installation-orders/approve?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post">
            <input type="hidden" name="_csrf-frontend" value="token-abc">
            <input type="hidden" name="vehicle_type" value="M">
        </form>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_approve_step2("b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertTrue(res["success"])
        self.assertEqual(res["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")

    @patch("requests.Session.post")
    def test_upload_installation_step2_photos_success_302(self, mock_post):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            csrf_token="csrf-val-xyz",
            expires_at=time.time() + 86400,
        ))

        # Create temporary dummy image files
        front_img = Path(self.tmp_dir.name) / "front_plate.jpg"
        front_img.write_bytes(b"dummy-front-image-bytes")
        rear_img = Path(self.tmp_dir.name) / "rear_plate.jpg"
        rear_img.write_bytes(b"dummy-rear-image-bytes")

        # Mock POST 302 Found redirect to Step 3 confirmation
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {
            "Location": "https://stock.itms.ug/installation-orders/confirmation?id=296f8002-c1a0-4a12-a8a2-18efc7c8f401"
        }
        mock_post.return_value = mock_resp

        res = self.client.upload_installation_step2_photos(
            order_uuid="296f8002-c1a0-4a12-a8a2-18efc7c8f401",
            front_photo_path=front_img,
            rear_photo_path=rear_img,
            vehicle_type="M",
            dry_run=False,
        )

        self.assertTrue(res["success"])
        self.assertFalse(res.get("dry_run", False))
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["step3_ready"])
        self.assertIn("/installation-orders/confirmation", res["redirect_url"])

        # Check call arguments
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertIn("files", call_kwargs)
        file_field_names = [f[0] for f in call_kwargs["files"]]
        self.assertIn("InstallationOrderApproveForm[front_plate]", file_field_names)
        self.assertIn("InstallationOrderApproveForm[rear_plate]", file_field_names)
        self.assertIn("InstallationOrderApproveForm[checklist]", file_field_names)

    @patch("requests.Session.post")
    def test_upload_installation_step2_photos_error_200(self, mock_post):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        front_img = Path(self.tmp_dir.name) / "front.jpg"
        front_img.write_bytes(b"dummy-front")
        rear_img = Path(self.tmp_dir.name) / "rear.jpg"
        rear_img.write_bytes(b"dummy-rear")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <div class="invalid-feedback">Front plate number cannot be blank.</div>
        """
        mock_post.return_value = mock_resp

        res = self.client.upload_installation_step2_photos(
            order_uuid="296f8002-c1a0-4a12-a8a2-18efc7c8f401",
            front_photo_path=front_img,
            rear_photo_path=rear_img,
            dry_run=False,
        )

        self.assertFalse(res["success"])
        self.assertEqual(res["status_code"], 200)
        self.assertIn("Front plate number cannot be blank.", res["form_errors"])

    @patch("requests.Session.post")
    def test_upload_installation_step2_photos_dry_run_default(self, mock_post):
        """Verifies dry_run=True by default prevents any POST network call to ITMS."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        front_img = Path(self.tmp_dir.name) / "front_plate.jpg"
        front_img.write_bytes(b"dummy-front")
        rear_img = Path(self.tmp_dir.name) / "rear_plate.jpg"
        rear_img.write_bytes(b"dummy-rear")

        res = self.client.upload_installation_step2_photos(
            order_uuid="296f8002-c1a0-4a12-a8a2-18efc7c8f401",
            front_photo_path=front_img,
            rear_photo_path=rear_img,
        )

        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["step3_ready"])
        self.assertIn("/installation-orders/confirmation", res["redirect_url"])
        mock_post.assert_not_called()

    @patch("requests.Session.post")
    def test_submit_installation_step1_dry_run_default(self, mock_post):
        """Verifies dry_run=True by default prevents mutating POST network call on Step 1."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        # Mock validate_installation_step1 to succeed
        with patch.object(self.client, "validate_installation_step1", return_value={"success": True, "valid": True}):
            res = self.client.submit_installation_step1(
                order_uuid="81ca8000-192a-4064-8858-79c7a2c30a8a",
                front_plate_id="fp-1",
                back_plate_id="bp-2",
                tracker_id="tr-3",
            )

        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["step2_ready"])
        self.assertIn("/installation-orders/approve", res["redirect_url"])
        mock_post.assert_not_called()

    def test_parse_confirmation_page_html(self):
        sample_html = """
        <html>
        <head>
            <meta name="csrf-token" content="csrf-token-step3-xyz">
        </head>
        <body>
            <ol class="breadcrumb">
                <li class="breadcrumb-item active">PO-UMA560PJ-100926</li>
            </ol>
            <div class="fw-semibold">Service Type:</div>
            <div class="text-muted small">First Time Registration</div>
            <div class="fw-semibold">Vehicle VIN/Chassis No.:</div>
            <div class="text-muted small">LC6PCJBJXT0B13846</div>
            <div class="fw-semibold">Old plate number:</div>
            <div class="text-muted small">—</div>
            <div class="fw-semibold">Registration Number:</div>
            <div class="text-muted small">UMA 560PJ</div>
            <div class="fw-semibold">Front Plate:</div>
            <div class="text-muted small">001102336</div>
            <div class="fw-semibold">Rear Plate:</div>
            <div class="text-muted small">001102337</div>
            <div class="fw-semibold">GPS Tracker:</div>
            <div class="text-muted small">8BAE470773A4</div>
            <div class="fw-semibold">Front Beacon:</div>
            <div class="text-muted small">8ADC470F69D2</div>
            <div class="fw-semibold">Rear Beacon:</div>
            <div class="text-muted small">8ADC470F537C</div>

            <form id="installationOrderEditPhotoForm" action="/installation-orders/confirmation?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post">
                <p class="mb-2 fw-semibold">Front Plate</p>
                <img src="/storage/local/installation-orders/plate-photos/front.jpg">
                <span class="text-dark small">front.jpg</span>

                <p class="mb-2 fw-semibold">Rear Plate</p>
                <img src="/storage/local/installation-orders/plate-photos/rear.jpg">
                <span class="text-dark small">rear.jpg</span>

                <p class="mb-2 fw-semibold">Installation checklist</p>
                <span class="text-dark small">No file</span>
            </form>
        </body>
        </html>
        """
        parsed = self.client.parse_confirmation_page_html(sample_html)
        self.assertEqual(parsed["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertEqual(parsed["order_number"], "PO-UMA560PJ-100926")
        self.assertEqual(parsed["csrf_token"], "csrf-token-step3-xyz")
        self.assertEqual(parsed["service_type"], "First Time Registration")
        self.assertEqual(parsed["vin"], "LC6PCJBJXT0B13846")
        self.assertEqual(parsed["old_plate"], "")
        self.assertEqual(parsed["registration_number"], "UMA 560PJ")
        self.assertEqual(parsed["front_plate"], "001102336")
        self.assertEqual(parsed["rear_plate"], "001102337")
        self.assertEqual(parsed["tracker"], "8BAE470773A4")
        self.assertEqual(parsed["front_beacon"], "8ADC470F69D2")
        self.assertEqual(parsed["rear_beacon"], "8ADC470F537C")
        self.assertEqual(len(parsed["photo_cards"]), 3)
        self.assertEqual(parsed["photo_cards"][0]["label"], "Front Plate")
        self.assertEqual(parsed["photo_cards"][0]["filename"], "front.jpg")
        self.assertEqual(parsed["photo_cards"][1]["label"], "Rear Plate")
        self.assertEqual(parsed["photo_cards"][1]["filename"], "rear.jpg")
        self.assertEqual(parsed["photo_cards"][2]["label"], "Installation checklist")
        self.assertEqual(parsed["photo_cards"][2]["filename"], "")

    @patch("requests.Session.get")
    def test_fetch_confirmation_step3(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <ol class="breadcrumb">
            <li class="breadcrumb-item active">PO-UMA560PJ-100926</li>
        </ol>
        <form action="/installation-orders/confirmation?id=b4579023-fab3-4e19-9990-824fefc1aac0" method="post">
            <input type="hidden" name="_csrf-frontend" value="token-step3-val">
        </form>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_confirmation_step3("b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertTrue(res["success"])
        self.assertEqual(res["order_uuid"], "b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertEqual(res["order_number"], "PO-UMA560PJ-100926")

    @patch("requests.Session.post")
    def test_submit_confirmation_step3_dry_run_default(self, mock_post):
        """Verifies Step 3 confirmation defaults to dry_run=True and does NOT send network POST."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        res = self.client.submit_confirmation_step3("b4579023-fab3-4e19-9990-824fefc1aac0")
        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["is_finalized"])
        self.assertIn("/installation-orders/index", res["redirect_url"])
        mock_post.assert_not_called()

    @patch("requests.Session.post")
    def test_submit_confirmation_step3_live_302(self, mock_post):
        """Verifies Step 3 confirmation sends multipart form with touched identifiers = 0 when dry_run=False."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            csrf_token="csrf-step3-token",
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {
            "Location": "https://stock.itms.ug/installation-orders/index"
        }
        mock_post.return_value = mock_resp

        res = self.client.submit_confirmation_step3(
            order_uuid="b4579023-fab3-4e19-9990-824fefc1aac0",
            dry_run=False,
        )
        self.assertTrue(res["success"])
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["status_code"], 302)
        self.assertTrue(res["is_finalized"])
        self.assertIn("/installation-orders/index", res["redirect_url"])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        data = call_kwargs["data"]
        self.assertEqual(data.get("front_plate_touched"), "0")
        self.assertEqual(data.get("rear_plate_touched"), "0")
        self.assertEqual(data.get("checklist_photo_touched"), "0")

    def test_sync_confirmation_step3_to_local_db(self):
        from core.models import InstallationOrder, VehicleInstallationPair
        step3_data = {
            "order_number": "PO-UMA560PJ-100926",
            "order_uuid": "b4579023-fab3-4e19-9990-824fefc1aac0",
            "registration_number": "UMA 560PJ",
            "vin": "LC6PCJBJXT0B13846",
            "front_plate": "001102336",
            "rear_plate": "001102337",
            "tracker": "8BAE470773A4",
            "front_beacon": "8ADC470F69D2",
            "rear_beacon": "8ADC470F537C",
            "photo_cards": [
                {"label": "Front Plate", "filename": "front.jpg"},
                {"label": "Rear Plate", "filename": "rear.jpg"},
            ],
        }

        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA560PJ",
        )

        res = self.client.sync_confirmation_step3_to_local_db(step3_data)
        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertEqual(res["pairs_updated"], 1)

        order = InstallationOrder.objects.get(order_number="PO-UMA560PJ-100926")
        self.assertEqual(order.registration_number, "UMA560PJ")
        self.assertEqual(order.vin, "LC6PCJBJXT0B13846")
        self.assertEqual(order.front_plate_serial, "001102336")
        self.assertEqual(order.rear_plate_serial, "001102337")
        self.assertEqual(order.gps_tracker_id, "8BAE470773A4")
        self.assertEqual(order.itms_order_uuid, "b4579023-fab3-4e19-9990-824fefc1aac0")

        pair.refresh_from_db()
        self.assertEqual(pair.order, order)

    @patch("requests.Session.post")
    def test_submit_confirmation_step3_with_replacement_photos(self, mock_post):
        """Verifies Step 3 confirmation correctly sets touched='1' and attaches binary for replaced photos."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            csrf_token="csrf-step3-token",
            expires_at=time.time() + 86400,
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"Location": "https://stock.itms.ug/installation-orders/index"}
        mock_post.return_value = mock_resp

        # Create dummy replacement image
        rep_front = Path(self.tmp_dir.name) / "new_front.jpg"
        rep_front.write_bytes(b"new-front-binary-bytes")

        res = self.client.submit_confirmation_step3(
            order_uuid="b4579023-fab3-4e19-9990-824fefc1aac0",
            front_photo_path=rep_front,
            rear_photo_path=None,  # untouched
            dry_run=False,
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["has_replacements"])
        self.assertIn("Front: new_front.jpg", res["replacements"])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        data = call_kwargs["data"]
        files = call_kwargs["files"]

        # Touched flags
        self.assertEqual(data.get("front_plate_touched"), "1")
        self.assertEqual(data.get("rear_plate_touched"), "0")
        self.assertEqual(data.get("checklist_photo_touched"), "0")

        # Files attached
        front_file_tuple = next(f for f in files if f[0] == "front_plate")
        self.assertEqual(front_file_tuple[1][0], "new_front.jpg")
        self.assertEqual(front_file_tuple[1][1], b"new-front-binary-bytes")

        rear_file_tuple = next(f for f in files if f[0] == "rear_plate")
        self.assertEqual(rear_file_tuple[1][0], "")  # empty filename
        self.assertEqual(rear_file_tuple[1][1], b"")  # empty binary

    @patch("requests.Session.get")
    def test_fetch_confirmation_step3_fallback_synthesis(self, mock_get):
        """Verifies fetch_confirmation_step3 synthesizes Step 3 data from order_info when GET /confirmation redirects."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        # Mock GET /confirmation to return 302 redirect (as ITMS does for unsubmitted orders)
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"Location": "https://stock.itms.ug/installation-orders/index"}
        mock_get.return_value = mock_resp

        # Mock fetch_order_info to return existing photos
        mock_info = {
            "success": True,
            "order_number": "PO-UMA835DS-030926",
            "registration_number": "UMA 835DS",
            "vin": "LC6PCJBJ8S0052136",
            "front_plate": {"serial": "000538185"},
            "rear_plate": {"serial": "000538186"},
            "gps_tracker": {"device_id": "8BAE4701EE89"},
            "front_beacon": {"device_id": "8ADC4703ED54"},
            "rear_beacon": {"device_id": "8ADC4703F1EB"},
            "photos": [
                {"label": "Front Plate", "orientation": "FRONT", "filename": "front.jpg", "url": "http://img/front.jpg"},
                {"label": "Rear Plate", "orientation": "REAR", "filename": "rear.jpg", "url": "http://img/rear.jpg"},
            ],
            "raw_details": {"Service Type": "First Time Registration"},
        }
        with patch.object(self.client, "fetch_order_info", return_value=mock_info):
            res = self.client.fetch_confirmation_step3("a2403a81-e499-4ce8-a125-0df0be644e74")

        self.assertTrue(res["success"])
        self.assertTrue(res.get("synthesized_from_info"))
        self.assertEqual(res["order_number"], "PO-UMA835DS-030926")
        self.assertEqual(res["registration_number"], "UMA 835DS")
        self.assertEqual(res["front_plate"], "000538185")
        self.assertEqual(res["rear_plate"], "000538186")
        self.assertEqual(len(res["photo_cards"]), 3)

    def test_detect_order_stage_lifecycle(self):
        """Verifies detect_order_stage accurately classifies STAGE_3_CONFIRMATION, STAGE_2_APPROVE, and STAGE_ARCHIVED."""
        self.store.save(ITMSWebSessionData(
            user_email="operator@example.com",
            cookies={"_identity-frontend": "some-val"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        # Case 1: Archived order
        with patch.object(self.client, "fetch_archive_orders", return_value={
            "orders": [{"order_key": "arch-1", "order_number": "PO-1", "order_status": "Installed"}]
        }):
            s1 = self.client.detect_order_stage("PO-1")
            self.assertEqual(s1["stage"], "STAGE_ARCHIVED")
            self.assertEqual(s1["step"], 4)

        # Case 2: Step 3 Confirmation (photos already uploaded)
        with patch.object(self.client, "fetch_archive_orders", return_value={"orders": []}), \
             patch.object(self.client, "fetch_installation_orders", return_value={
                 "orders": [{"order_key": "active-uuid", "order_number": "PO-UMA835DS-030926", "action_url": "/installation-orders/approve?id=active-uuid"}]
             }), \
             patch.object(self.client, "fetch_order_info", return_value={
                 "success": True,
                 "order_number": "PO-UMA835DS-030926",
                 "photos": [{"filename": "f.jpg"}, {"filename": "r.jpg"}],
                 "front_plate": {"serial": "001"},
             }):
            s2 = self.client.detect_order_stage("UMA 835DS")
            self.assertEqual(s2["stage"], "STAGE_3_CONFIRMATION")
            self.assertEqual(s2["step"], 3)
            self.assertEqual(s2["photos_count"], 2)

        # Case 3: Step 2 Approve (hardware assigned, no photos yet)
        with patch.object(self.client, "fetch_archive_orders", return_value={"orders": []}), \
             patch.object(self.client, "fetch_installation_orders", return_value={
                 "orders": [{"order_key": "active-uuid-2", "order_number": "PO-NEW", "action_url": "/installation-orders/approve?id=active-uuid-2"}]
             }), \
             patch.object(self.client, "fetch_order_info", return_value={
                 "success": True,
                 "order_number": "PO-NEW",
                 "photos": [],
                 "front_plate": {"serial": "001"},
             }):
            s3 = self.client.detect_order_stage("PO-NEW")
            self.assertEqual(s3["stage"], "STAGE_2_APPROVE")
            self.assertEqual(s3["step"], 2)
            self.assertEqual(s3["photos_count"], 0)

    def test_show_order_confirmation_photos_viewer(self):
        """Verifies viewer helper resolves confirmation photo cards and launches comparison."""
        from core.services import viewer

        front_p = Path(self.tmp_dir.name) / "cf_front.jpg"
        front_p.write_bytes(b"dummy")
        rear_p = Path(self.tmp_dir.name) / "cf_rear.jpg"
        rear_p.write_bytes(b"dummy")

        confirmation_data = {
            "order_number": "PO-TEST",
            "registration_number": "UMA 123AB",
            "photo_cards": [
                {"label": "Front Plate", "orientation": "FRONT", "local_path": str(front_p)},
                {"label": "Rear Plate", "orientation": "REAR", "local_path": str(rear_p)},
            ],
        }

        with patch("subprocess.Popen") as mock_popen, \
             patch("cv2.imwrite") as mock_imwrite:
            res_path = viewer.show_order_confirmation_photos(confirmation_data, block=False)
            self.assertIsNotNone(res_path)
            mock_popen.assert_called_once()



