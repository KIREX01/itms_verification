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
            user_email="k.jeremiah@itms-ug.com",
            user_uuid="ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13",
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
        self.assertEqual(loaded.user_email, "k.jeremiah@itms-ug.com")
        self.assertEqual(loaded.user_uuid, "ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13")
        self.assertTrue(loaded.is_authenticated)
        self.assertTrue(loaded.is_cookie_valid())
        self.assertTrue(loaded.is_recently_verified(max_age_seconds=60))

    def test_clear_session(self):
        data = ITMSWebSessionData(
            user_email="test@itms.ug",
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
            "_identity-frontend": 'edcdc7b8cba54e4aa2a716c19de46acbf0bc25ad83bf9e9657d64259ce61d7e9a%3A2%3A%7Bi%3A0%3Bs%3A18%3A%22_identity-frontend%22%3Bi%3A1%3Bs%3A83%3A%22%5B%22ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13%22%2C%22A2dljxuDH5Ch-JeKoGKcLUP_PbRMytC1%22%2C2592000%5D%22%3B%7D',
        }
        mock_session.cookies.get_dict.return_value = mock_cookies
        mock_session.cookies.get.side_effect = lambda k, default="": mock_cookies.get(k, default)

        ok, msg, sess = self.client.login("k.jeremiah@itms-ug.com", "secretpass")
        self.assertTrue(ok)
        self.assertIn("Successfully authenticated", msg)
        self.assertEqual(sess["user_uuid"], "ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13")
        self.assertEqual(sess["user_email"], "k.jeremiah@itms-ug.com")

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
            user_email="k.jeremiah@itms-ug.com",
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
            user_email="k.jeremiah@itms-ug.com",
            user_uuid="ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13",
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
            <a href="/user/ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13/main/information">Profile</a>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        res = self.client.fetch_read_only_dashboard()
        self.assertTrue(res["success"])
        self.assertEqual(res["page_title"], "ITMS - Dashboard")
        self.assertEqual(res["user_uuid"], "ce1e5fe5-02b5-4a3e-8eb9-ebba8ac1ce13")
        self.assertEqual(len(res["accessible_modules"]), 3)

    @patch("requests.Session.get")
    def test_fetch_installation_orders_and_sync(self, mock_get):
        self.store.save(ITMSWebSessionData(
            user_email="k.jeremiah@itms-ug.com",
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
            user_email="k.jeremiah@itms-ug.com",
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
            user_email="k.jeremiah@itms-ug.com",
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
                    <td>JEREMIAH KATO</td><td>08.09.2026 - 15:05</td><td></td>
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
        self.assertEqual(orders[0]["officer"], "JEREMIAH KATO")

        # Sync to local DB
        sync_res = self.client.sync_orders_to_local_db(orders)
        self.assertEqual(sync_res["total"], 1)
        self.assertEqual(sync_res["installed_verified"], 1)

        db_order = InstallationOrder.objects.get(order_number="PO-UMA633PG-080926")
        self.assertEqual(db_order.status, InstallationOrder.Status.INSTALLED)
        self.assertTrue(db_order.is_archived)
        self.assertEqual(db_order.order_status, "Installed")
        self.assertEqual(db_order.registration_status, "Active")
        self.assertEqual(db_order.installation_officer, "JEREMIAH KATO")

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
        self.assertEqual(res["installed_by"], "JEREMIAH KATO")
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
            user_email="k.jeremiah@itms-ug.com",
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

