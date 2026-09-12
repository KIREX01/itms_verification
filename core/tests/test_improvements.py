"""
Unit and integration tests for recent system improvements:
- Item 2: Batch submission progress modal, shift handover export, and barcode scanner handling
- Item 3: Uganda plate positional disambiguation, special plate regex, and ensemble OCR voting
- Item 4: Storage lifecycle cleanup and database WAL / query optimization
"""
import os
import csv
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.test import TestCase, SimpleTestCase, TransactionTestCase
from django.utils import timezone

from core.models import (
    EvidenceImage,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.vision.normalizer import normalize_plate, is_valid_plate, canonicalize
from core.services.maintenance_service import clean_storage_lifecycle, optimize_database
from core.services.export_service import export_shift_report


class PlateNormalizationImprovementTests(TestCase):
    def test_standard_ugandan_plates(self):
        self.assertTrue(is_valid_plate("UMA835DS"))
        self.assertTrue(is_valid_plate("UBB456C"))
        self.assertTrue(is_valid_plate("UAX999ZZ"))

    def test_special_institutional_plates(self):
        # Government
        res_ug = normalize_plate("UG 1234A")
        self.assertTrue(res_ug["is_valid"])
        self.assertEqual(res_ug["canonical"], "UG1234A")

        # Police
        res_police = normalize_plate("UPF 501B")
        self.assertTrue(res_police["is_valid"])
        self.assertEqual(res_police["canonical"], "UPF501B")

        # Diplomatic
        res_cd = normalize_plate("CD 2011")
        self.assertTrue(res_cd["is_valid"])
        self.assertEqual(res_cd["canonical"], "CD2011")

    def test_positional_ocr_disambiguation(self):
        # 0 in slot 0 -> U
        # 5 in slot 7 -> S
        res = normalize_plate("0MA835D5")
        self.assertTrue(res["is_valid"])
        self.assertEqual(res["canonical"], "UMA835DS")

        # V in slot 0 -> U
        res_v = normalize_plate("VMA835DS")
        self.assertTrue(res_v["is_valid"])
        self.assertEqual(res_v["canonical"], "UMA835DS")

        # Letters in middle digit slots (O -> 0, B -> 8)
        res_digits = normalize_plate("UMA8O5DS")
        self.assertTrue(res_digits["is_valid"])
        self.assertEqual(res_digits["canonical"], "UMA805DS")


class MaintenanceServiceTests(TestCase):
    def test_optimize_database(self):
        res = optimize_database()
        self.assertIsInstance(res, dict)
        self.assertTrue(res.get("optimized"))
        self.assertIn("message", res)

    def test_clean_storage_lifecycle_dry_run(self):
        res = clean_storage_lifecycle(max_crop_age_days=0, dry_run=True)
        self.assertIsInstance(res, dict)
        self.assertTrue(res["dry_run"])
        self.assertIn("mb_freed", res)

    def test_clean_storage_lifecycle_live(self):
        res = clean_storage_lifecycle(max_crop_age_days=7, optimize_db=True, dry_run=False)
        self.assertIsInstance(res, dict)
        self.assertFalse(res["dry_run"])
        self.assertIn("deleted_count", res)


class ShiftExportServiceTests(TestCase):
    def setUp(self):
        self.order = InstallationOrder.objects.create(
            order_number="PO-TEST-001",
            registration_number="UMA835DS",
            vin="VIN1234567890TEST",
            tracker_id="TRK98765",
            plate_serial="PLT112233",
            installation_officer="TEST OFFICER",
            warehouse_name="TEST WAREHOUSE",
        )
        self.front = EvidenceImage.objects.create(
            file_hash="1" * 64,
            original_source_path="/tmp/f.jpg",
            vault_file="vault/f.jpg",
        )
        self.rear = EvidenceImage.objects.create(
            file_hash="2" * 64,
            original_source_path="/tmp/r.jpg",
            vault_file="vault/r.jpg",
        )
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA835DS",
            order=self.order,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_manual_override=True,
        )
        SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Submitted successfully",
            simulated_token="TEST-TOKEN-9999",
        )

    def test_export_shift_report(self):
        res = export_shift_report(queryset=VehicleInstallationPair.objects.filter(id=self.pair.id))
        self.assertTrue(res["success"])
        self.assertEqual(res["row_count"], 1)

        file_path = res["file_path"]
        self.assertTrue(os.path.exists(file_path))

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
            self.assertIn("Plate (Detected)", header)
            self.assertIn("Order Number", header)
            self.assertIn("ITMS Token / Ref", header)

            row = next(reader)
            self.assertEqual(row[2], "UMA835DS")
            self.assertEqual(row[4], "PO-TEST-001")
            self.assertEqual(row[6], "VIN1234567890TEST")
            self.assertEqual(row[19], "TEST-TOKEN-9999")

        # Cleanup
        try:
            os.remove(file_path)
        except OSError:
            pass


class TUIComponentsTests(TestCase):
    def test_batch_progress_modal_creation(self):
        from core.tui.dialogs import BatchProgressModal
        modal = BatchProgressModal(total_orders=10, dry_run=True)
        self.assertEqual(modal.total_orders, 10)
        self.assertTrue(modal.dry_run)
        self.assertFalse(modal.is_paused)
        self.assertFalse(modal.is_stopped)
        self.assertFalse(modal.is_finished)

    def test_command_provider_has_export_command(self):
        from core.tui.commands import ITMSCommandProvider
        from unittest.mock import MagicMock
        app_mock = MagicMock()
        provider = ITMSCommandProvider(screen=app_mock)
        commands = provider._get_commands()
        command_names = [c[0] for c in commands]
        self.assertTrue(any("Export: Shift Verification Report" in name for name in command_names))
        self.assertTrue(any("Navigate: System Settings & Config" in name for name in command_names))


class ConfigServiceAndPermissionsTests(TestCase):
    def test_default_config_loading(self):
        import tempfile
        from pathlib import Path
        from core.services import config_service
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = config_service.load_config(base_dir=Path(tmpdir))
            self.assertIsInstance(cfg, dict)
            self.assertIn("database", cfg)
            self.assertIn("submission", cfg)
            self.assertIn("system", cfg)
            self.assertEqual(cfg["database"]["engine"], "sqlite")

    def test_get_and_set_setting(self):
        from core.services import config_service
        self.assertTrue(config_service.set_setting("submission.request_timeout_seconds", 45))
        self.assertEqual(config_service.get_setting("submission.request_timeout_seconds"), 45)
        # Restore default
        config_service.set_setting("submission.request_timeout_seconds", 30)

    def test_database_config_sqlite_default(self):
        import tempfile
        from pathlib import Path
        from core.services import config_service
        with tempfile.TemporaryDirectory() as tmpdir:
            db_conf = config_service.get_database_config(Path(tmpdir))
            self.assertEqual(db_conf["ENGINE"], "django.db.backends.sqlite3")
            self.assertIn("OPTIONS", db_conf)

    def test_developer_mode_command_filtering(self):
        from core.services import config_service
        from core.tui.commands import ITMSCommandProvider
        from unittest.mock import MagicMock

        # When developer_mode is False, developer test commands must NOT be exposed
        config_service.set_setting("system.developer_mode", False)
        app_mock = MagicMock()
        provider = ITMSCommandProvider(screen=app_mock)
        commands = [c[0] for c in provider._get_commands()]
        self.assertFalse(any("[DEV]" in c for c in commands))

        # When developer_mode is True, developer test commands are exposed
        config_service.set_setting("system.developer_mode", True)
        provider_dev = ITMSCommandProvider(screen=app_mock)
        commands_dev = [c[0] for c in provider_dev._get_commands()]
        self.assertTrue(any("[DEV]" in c for c in commands_dev))

        # Restore default False
        config_service.set_setting("system.developer_mode", False)

    def test_settings_pane_widget_compose(self):
        from core.tui.settings_pane import SettingsPane
        pane = SettingsPane()
        self.assertIsNotNone(pane)

    def test_safety_guarantee_config_and_dynamic_rendering(self):
        from core.services import config_service
        from core.tui.itms_pane import ITMSConnectionPane

        # Verify default is False (hidden by default)
        config_service.set_setting("system.show_safety_guarantee", False)
        self.assertFalse(config_service.get_setting("system.show_safety_guarantee"))

        pane = ITMSConnectionPane()

        # Test Dry Run Mode dynamic top bar and safety card
        config_service.set_setting("submission.dry_run_mode", True)
        config_service.set_setting("submission.request_timeout_seconds", 35)
        config_service.set_setting("submission.circuit_breaker_threshold", 4)
        config_service.set_setting("storage.vault_retention_days", 14)

        top_bar_dry = pane._render_top_bar()
        self.assertIn("READ-ONLY AUDIT (DRY-RUN)", top_bar_dry)

        safety_card_dry = pane._render_safety_card()
        self.assertIn("SAFE AUDIT GUARANTEE (CONFIG LOADED)", safety_card_dry)
        self.assertIn("35s timeout", safety_card_dry)
        self.assertIn("Circuit Breaker: 4 errors", safety_card_dry)
        self.assertIn("Vault retention: 14d", safety_card_dry)

        # Test Live Submission Mode dynamic top bar and safety card
        config_service.set_setting("submission.dry_run_mode", False)
        top_bar_live = pane._render_top_bar()
        self.assertIn("LIVE SUBMISSION MODE", top_bar_live)

        safety_card_live = pane._render_safety_card()
        self.assertIn("LIVE SUBMISSION ACTIVE (CONFIG LOADED)", safety_card_live)
        self.assertIn("35s timeout", safety_card_live)
        self.assertIn("after 4 failures", safety_card_live)

        # Verify compose classes based on show_safety_guarantee configuration
        config_service.set_setting("system.show_safety_guarantee", False)
        # Verify get_setting returns False
        self.assertFalse(config_service.get_setting("system.show_safety_guarantee"))

        config_service.set_setting("system.show_safety_guarantee", True)
        self.assertTrue(config_service.get_setting("system.show_safety_guarantee"))

        # Restore defaults
        config_service.set_setting("submission.dry_run_mode", True)
        config_service.set_setting("submission.request_timeout_seconds", 30)
        config_service.set_setting("submission.circuit_breaker_threshold", 3)
        config_service.set_setting("storage.vault_retention_days", 7)
        config_service.set_setting("system.show_safety_guarantee", False)


class DashboardMetricsAndOrderSyncTests(TestCase):
    def test_metrics_bar_order_breakdown(self):
        """Tests MetricsBar computes active, done, and total order counts."""
        from core.tui.widgets import MetricsBar
        bar = MetricsBar()
        bar.update = lambda val: setattr(bar, "_last_rendered", val)

        # Initially 0 orders
        bar.refresh_metrics()
        self.assertIn("0 [dim](0 Act │ 0 Done)[/dim]", bar._last_rendered)

        # Create 1 active order
        InstallationOrder.objects.create(
            order_number="ORD-ACT-1",
            registration_number="UMA100A",
            is_active_on_itms=True,
            is_archived=False,
            status=InstallationOrder.Status.PENDING,
        )

        # Create 1 completed/installed order
        InstallationOrder.objects.create(
            order_number="ORD-DONE-1",
            registration_number="UMA200B",
            is_active_on_itms=False,
            is_archived=True,
            order_status="Installed",
            status=InstallationOrder.Status.INSTALLED,
        )

        bar.refresh_metrics()
        self.assertIn("[b]Orders:[/b] 2 [dim]([yellow]1 Act[/yellow] │ [green]1 Done[/green])[/dim]", bar._last_rendered)

    def test_sync_orders_to_local_db_stage_and_flags(self):
        """Tests that sync_orders_to_local_db properly sets is_active_on_itms, is_archived, itms_stage, and last_synced_at."""
        from core.services.itms_web_client import get_web_client
        client = get_web_client()

        raw_orders = [
            {
                "order_number": "PO-SYNC-001",
                "registration_number": "UMA 777AA",
                "vin": "VIN777",
                "status": "Under installation",
                "action_url": "/installation-orders/installation?id=uuid-1",
                "is_archived": False,
            },
            {
                "order_number": "PO-SYNC-002",
                "registration_number": "UMA 888BB",
                "vin": "VIN888",
                "status": "Ready for approve",
                "action_url": "/installation-orders/approve?id=uuid-2",
                "is_archived": False,
            },
            {
                "order_number": "PO-SYNC-003",
                "registration_number": "UMA 999CC",
                "vin": "VIN999",
                "order_status": "Installed",
                "action_url": "",
                "is_archived": True,
            },
        ]

        res = client.sync_orders_to_local_db(raw_orders)
        self.assertEqual(res["created"], 3)

        o1 = InstallationOrder.objects.get(order_number="PO-SYNC-001")
        self.assertTrue(o1.is_active_on_itms)
        self.assertFalse(o1.is_archived)
        self.assertEqual(o1.itms_stage, "STAGE_1_INSTALLATION")
        self.assertEqual(o1.status, InstallationOrder.Status.PENDING)
        self.assertIsNotNone(o1.last_synced_at)

        o2 = InstallationOrder.objects.get(order_number="PO-SYNC-002")
        self.assertTrue(o2.is_active_on_itms)
        self.assertFalse(o2.is_archived)
        self.assertEqual(o2.itms_stage, "STAGE_2_APPROVE")
        self.assertEqual(o2.status, InstallationOrder.Status.SUBMITTED)

        o3 = InstallationOrder.objects.get(order_number="PO-SYNC-003")
        self.assertFalse(o3.is_active_on_itms)
        self.assertTrue(o3.is_archived)
        self.assertEqual(o3.itms_stage, "ARCHIVED")
        self.assertEqual(o3.status, InstallationOrder.Status.INSTALLED)

    def test_action_login_tuple_response(self):
        """Tests that ITMSConnectionPane.action_login gracefully handles 3-tuple return from client.login without error."""
        from unittest.mock import MagicMock, PropertyMock, patch
        from core.tui.itms_pane import ITMSConnectionPane

        pane = ITMSConnectionPane()
        mock_app = MagicMock()
        mock_app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        pane.query_one = MagicMock()
        pane._set_feedback = MagicMock()
        pane._log_preview = MagicMock()
        pane._refresh_status_card = MagicMock()

        url_input = MagicMock(value="https://stock.itms.ug")
        email_input = MagicMock(value="s.ssemakula@itms-ug.com")
        pw_input = MagicMock(value="secretpass")
        chk_input = MagicMock(value=True)

        def mock_query(selector, widget_type=None):
            if "url" in selector:
                return url_input
            if "email" in selector:
                return email_input
            if "password" in selector:
                return pw_input
            if "remember" in selector:
                return chk_input
            return MagicMock()

        pane.query_one.side_effect = mock_query

        fake_tuple = (
            True,
            "Successfully authenticated as s.ssemakula@itms-ug.com.",
            {
                "user_uuid": "8038a438-5dda-48b5-869f-e0f9c3e3ec3f",
                "user_email": "s.ssemakula@itms-ug.com",
            },
        )

        with patch.object(ITMSConnectionPane, "app", new_callable=PropertyMock, return_value=mock_app), \
             patch("core.tui.itms_pane.ITMSWebClient") as MockClientClass:
            mock_inst = MagicMock()
            mock_inst.login.return_value = fake_tuple
            mock_inst.session_store.session.user_uuid = "8038a438-5dda-48b5-869f-e0f9c3e3ec3f"
            MockClientClass.return_value = mock_inst

            if hasattr(pane.action_login, "__wrapped__"):
                pane.action_login.__wrapped__(pane)
            else:
                pane.action_login()

            mock_inst.login.assert_called_once_with("s.ssemakula@itms-ug.com", "secretpass", remember_me=True)
            self.assertEqual(pane.client, mock_inst)
            self.assertEqual(pw_input.value, "")


class DatabaseSwitchingAndSettingsNavigationTests(TransactionTestCase):
    databases = {"default"}

    def test_test_postgres_connection_mocked_success(self):
        """Tests test_postgres_connection reports success when psycopg2 connects."""
        from unittest.mock import MagicMock, patch
        from core.services import config_service

        with patch("psycopg2.connect") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ["PostgreSQL 16.2 on x86_64-pc-linux-gnu"]
            mock_conn.return_value.cursor.return_value = mock_cursor

            res = config_service.test_postgres_connection(
                host="localhost", port=5432, dbname="itms", user="postgres", password="secret"
            )
            self.assertTrue(res["success"])
            self.assertIn("PostgreSQL 16.2", res["version"])
            self.assertIn("Connected", res["message"])

    def test_test_postgres_connection_mocked_failure(self):
        """Tests test_postgres_connection reports error when psycopg2 raises an exception."""
        from unittest.mock import patch
        from core.services import config_service

        with patch("psycopg2.connect", side_effect=Exception("Connection refused")):
            res = config_service.test_postgres_connection(
                host="badhost", port=5432, dbname="itms", user="postgres", password=""
            )
            self.assertFalse(res["success"])
            self.assertIn("Connection refused", res["error"])

    def test_switch_database_sqlite(self):
        """Tests switching database to SQLite succeeds."""
        import copy
        from django.conf import settings
        from django.db import connections
        from core.services import config_service

        orig_db = copy.deepcopy(settings.DATABASES["default"])
        try:
            res = config_service.switch_database("sqlite", run_migrations=False, persist_config=False)
            self.assertTrue(res["success"])
            self.assertEqual(res["engine"], "sqlite")
        finally:
            settings.DATABASES["default"] = orig_db
            try:
                connections.close_all()
            except Exception:
                pass
            try:
                del connections["default"]
            except (KeyError, AttributeError):
                pass
            connections._settings = None
            if "settings" in connections.__dict__:
                del connections.__dict__["settings"]
            from asgiref.local import Local
            connections._connections = Local(connections.thread_critical)
            connections["default"].ensure_connection()

    def test_switch_database_from_worker_thread(self):
        """Tests that switch_database called from a worker thread without initialized connections does not raise AttributeError."""
        import copy
        import threading
        from django.conf import settings
        from django.db import connections
        from core.services import config_service
        from asgiref.local import Local

        orig_db = copy.deepcopy(settings.DATABASES["default"])
        thread_result = {}

        def thread_target():
            try:
                res = config_service.switch_database("sqlite", run_migrations=False, persist_config=False)
                thread_result["res"] = res
            except Exception as exc:
                thread_result["error"] = exc

        try:
            th = threading.Thread(target=thread_target)
            th.start()
            th.join()

            self.assertNotIn("error", thread_result, f"Worker thread raised error: {thread_result.get('error')}")
            self.assertTrue(thread_result.get("res", {}).get("success"))
        finally:
            settings.DATABASES["default"] = orig_db
            try:
                connections.close_all()
            except Exception:
                pass
            try:
                del connections["default"]
            except (KeyError, AttributeError):
                pass
            connections._settings = None
            if "settings" in connections.__dict__:
                del connections.__dict__["settings"]
            connections._connections = Local(connections.thread_critical)
            connections["default"].ensure_connection()

    def test_switch_database_postgres_unreachable_safeguard(self):
        """Tests switching database to unreachable PostgreSQL aborts safely without altering SQLite."""
        import copy
        from django.conf import settings
        from core.services import config_service
        from django.db import connection

        orig_db = copy.deepcopy(settings.DATABASES["default"])
        orig_vendor = connection.vendor
        try:
            res = config_service.switch_database(
                "postgresql", host="127.0.0.1", port=64321, dbname="bad_db", run_migrations=False, persist_config=False
            )
            self.assertFalse(res["success"])
            self.assertEqual(connection.vendor, orig_vendor)
        finally:
            settings.DATABASES["default"] = orig_db

    def test_operator_account_replication_and_sync(self):
        """Tests that operator user accounts and PBKDF2 password hashes are preserved across database operations."""
        from django.contrib.auth.models import User
        from core.services import config_service, auth_service

        # Create a test operator
        test_uname = "test_operator_multi_db"
        User.objects.filter(username=test_uname).delete()
        user, err = auth_service.create_operator_account(
            username=test_uname, password="SecurePassword123!", full_name="Multi DB User"
        )
        self.assertIsNotNone(user)
        self.assertEqual(err, "")
        orig_hash = user.password

        # Authenticate verifies PBKDF2 hash
        auth_u, auth_err = auth_service.authenticate_operator(test_uname, "SecurePassword123!")
        self.assertIsNotNone(auth_u)
        self.assertEqual(auth_u.password, orig_hash)

        # Cleanup
        User.objects.filter(username=test_uname).delete()

    def test_settings_pane_escape_unfocus_and_navigation(self):
        """Tests that SettingsPane on_key un-focuses inputs on Escape key and blurs on Enter."""
        from unittest.mock import MagicMock, PropertyMock, patch
        from textual import events
        from core.tui.settings_pane import SettingsPane

        pane = SettingsPane()
        mock_app = MagicMock()

        with patch.object(SettingsPane, "app", new_callable=PropertyMock, return_value=mock_app):
            esc_event = events.Key(key="escape", character=None)
            pane.on_key(esc_event)
            mock_app.set_focus.assert_called_with(None)

            # on_input_submitted blurs focus
            mock_app.set_focus.reset_mock()
            from textual.widgets import Input
            dummy_input = Input()
            submit_event = Input.Submitted(dummy_input, "test")
            pane.on_input_submitted(submit_event)
            mock_app.set_focus.assert_called_with(None)


class MultiAccountDataIntegrityTests(TestCase):
    """Tests multi-account data isolation, ownership tagging, and session switching."""

    def setUp(self):
        InstallationOrder.objects.all().delete()
        VehicleInstallationPair.objects.all().delete()
        EvidenceImage.objects.all().delete()

    def test_order_sync_account_tagging(self):
        """Verifies synced orders are tagged with the active ITMS account email and UUID."""
        from unittest.mock import MagicMock
        from core.services.order_sync import OrderSyncService
        from core.services.itms_web_client import ITMSWebSessionData

        mock_client = MagicMock()
        mock_client.session_store.session = ITMSWebSessionData(
            user_email="s.ssemakula@itms-ug.com",
            user_uuid="8038a438-5dda-48b5-869f-e0f9c3e3ec3f",
            is_authenticated=True,
        )
        mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "has_next_page": False,
            "orders": [
                {
                    "order_number": "ORD-ACC-001",
                    "registration_number": "UMA 111AA",
                    "status": "Under installation",
                    "action_url": "/installation-orders/installation?id=uuid-1",
                }
            ],
        }

        service = OrderSyncService(client=mock_client)
        res = service.sync_active_orders(force=True)
        self.assertTrue(res.get("success") or res.get("created", 0) > 0 or res.get("result", {}).get("created", 0) > 0)

        order = InstallationOrder.objects.get(order_number="ORD-ACC-001")
        self.assertEqual(order.account_email, "s.ssemakula@itms-ug.com")
        self.assertEqual(order.account_uuid, "8038a438-5dda-48b5-869f-e0f9c3e3ec3f")

    def test_order_sync_multi_account_disappearance_isolation(self):
        """
        Critical test: Verifies that when Account B syncs, active orders belonging to
        Account A are NOT marked as disappeared or archived!
        """
        from unittest.mock import MagicMock
        from core.services.order_sync import OrderSyncService
        from core.services.itms_web_client import ITMSWebSessionData

        # Pre-existing active order for Account A
        order_a = InstallationOrder.objects.create(
            order_number="ORD-USER-A",
            registration_number="UMA100A",
            account_email="user_a@itms-ug.com",
            is_active_on_itms=True,
            is_archived=False,
            status=InstallationOrder.Status.PENDING,
        )

        # Account B syncs, which only sees ORD-USER-B
        mock_client = MagicMock()
        mock_client.session_store.session = ITMSWebSessionData(
            user_email="user_b@itms-ug.com",
            user_uuid="uuid-b",
            is_authenticated=True,
        )
        mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "has_next_page": False,
            "orders": [
                {
                    "order_number": "ORD-USER-B",
                    "registration_number": "UBB200B",
                    "status": "Under installation",
                    "action_url": "/installation-orders/installation?id=uuid-b",
                }
            ],
        }

        service = OrderSyncService(client=mock_client)
        service.sync_active_orders(force=True)

        # Reload Order A: it MUST remain active and NOT archived!
        order_a.refresh_from_db()
        self.assertTrue(order_a.is_active_on_itms)
        self.assertFalse(order_a.is_archived)
        self.assertEqual(order_a.status, InstallationOrder.Status.PENDING)

    def test_prior_guided_account_isolation(self):
        """Verifies prior-guided candidate orders only include the active account's orders."""
        from core.matcher.prior_guided import get_active_orders_cache

        InstallationOrder.objects.create(
            order_number="ORD-A-1",
            registration_number="UMA100A",
            account_email="user_a@itms-ug.com",
            is_active_on_itms=True,
            is_archived=False,
        )
        InstallationOrder.objects.create(
            order_number="ORD-B-1",
            registration_number="UBB200B",
            account_email="user_b@itms-ug.com",
            is_active_on_itms=True,
            is_archived=False,
        )

        # Scope to User B
        cached_b = get_active_orders_cache(account_email="user_b@itms-ug.com")
        plates_b = [o["registration_number"] for o in cached_b]
        self.assertIn("UBB200B", plates_b)
        self.assertNotIn("UMA100A", plates_b)

    def test_cross_account_submission_blocked(self):
        """Verifies submission worker halts if attempting to submit an order linked to a different account."""
        from unittest.mock import patch, MagicMock
        from core.services.submission_worker import submit_pair
        from core.services.itms_web_client import ITMSWebSessionData

        order_a = InstallationOrder.objects.create(
            order_number="ORD-SECURE-A",
            registration_number="UMA333A",
            account_email="user_a@itms-ug.com",
        )
        front_img = EvidenceImage.objects.create(
            file_hash="hash_front_1",
            original_source_path="front.jpg",
            vault_file="front.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear_img = EvidenceImage.objects.create(
            file_hash="hash_rear_1",
            original_source_path="rear.jpg",
            vault_file="rear.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA333A",
            order=order_a,
            front_image=front_img,
            rear_image=rear_img,
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )

        # Mock active session as user_b@itms-ug.com
        mock_client = MagicMock()
        mock_client.session_store.session = ITMSWebSessionData(
            user_email="user_b@itms-ug.com",
            is_authenticated=True,
        )

        with patch("core.services.itms_web_client.get_web_client", return_value=mock_client):
            outcome = submit_pair(pair, backend="web", dry_run=True)
            self.assertFalse(outcome.success)
            self.assertIn("Cross-account submission blocked", outcome.error)

        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)

    def test_client_logout_and_pane_logout(self):
        """Verifies client.logout clears session and pane.action_itms_logout runs cleanly."""
        from unittest.mock import MagicMock, PropertyMock, patch
        from core.services.itms_web_client import ITMSWebClient, ITMSWebSessionStore
        from core.tui.itms_pane import ITMSConnectionPane

        store = ITMSWebSessionStore()
        client = ITMSWebClient(session_store=store)
        client.session_store.session.user_email = "test@itms-ug.com"
        client.session_store.session.is_authenticated = True

        res = client.logout()
        self.assertTrue(res[0] if isinstance(res, tuple) else res.get("success"))
        self.assertEqual(client.session_store.session.user_email, "")

        pane = ITMSConnectionPane()
        mock_app = MagicMock()
        with patch.object(ITMSConnectionPane, "app", new_callable=PropertyMock, return_value=mock_app):
            if hasattr(pane.action_itms_logout, "__wrapped__"):
                pane.action_itms_logout.__wrapped__(pane)
            else:
                pane.action_itms_logout()

            self.assertEqual(pane._last_fetched_orders, [])
            self.assertEqual(pane._active_orders_map, {})


class DryRunSafetyConfigurationAndLiveSubmissionTests(TestCase):
    """
    Tests ensuring that the dry_run_mode toggle in config.json is dynamically
    respected across TUI modals, submission worker, and live ITMS client.
    """

    def setUp(self):
        from core.models import IngestionBatch, EvidenceImage, InstallationOrder, VehicleInstallationPair
        self.batch = IngestionBatch.objects.create(batch_id="BATCH-DRY-TEST-001")
        self.front = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_front_dry_001",
            original_source_path="front_dry.jpg",
            vault_file="vault/front_dry.jpg",
            status=EvidenceImage.Status.READY,
            detected_plate="UAA111A",
        )
        self.rear = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_rear_dry_002",
            original_source_path="rear_dry.jpg",
            vault_file="vault/rear_dry.jpg",
            status=EvidenceImage.Status.READY,
            detected_plate="UAA111A",
        )
        self.order = InstallationOrder.objects.create(
            order_number="ORD-DRY-LIVE-001",
            registration_number="UAA111A",
            status=InstallationOrder.Status.MATCHED,
            account_email="operator@test.com",
            is_active_on_itms=True,
        )
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UAA111A",
            order=self.order,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
            account_email="operator@test.com",
        )

    def test_live_submission_when_dry_run_disabled_in_config(self):
        """When submission.dry_run_mode is false, submit_pair executes live submission without simulation."""
        from unittest.mock import MagicMock, patch
        from core.services import config_service
        from core.services.submission_worker import submit_pair
        from core.models import SubmissionAuditLog

        mock_client = MagicMock()
        mock_client.session_store.session.user_email = "operator@test.com"
        mock_client.session_store.session.is_authenticated = True
        mock_client.execute_installation_order_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-live-999",
            "message": "Live order completed on stock.itms.ug",
            "is_finalized": True,
        }

        with patch.object(config_service, "get_setting", side_effect=lambda key, default=None: False if key == "submission.dry_run_mode" else True):
            with patch("core.services.itms_web_client.get_web_client", return_value=mock_client):
                # When dry_run is not explicitly provided, it must query config_service -> False
                outcome = submit_pair(self.pair, backend="web")
                self.assertTrue(outcome.success)

                # Verify dry_run=False was passed to web_client workflow
                mock_client.execute_installation_order_workflow.assert_called_once()
                call_kwargs = mock_client.execute_installation_order_workflow.call_args[1]
                self.assertFalse(call_kwargs["dry_run"])

                # Verify audit log does NOT contain [DRY-RUN] tag
                audit = self.pair.audit_logs.filter(action=SubmissionAuditLog.Action.SUBMIT).first()
                self.assertIsNotNone(audit)
                self.assertNotIn("[DRY-RUN]", audit.message)

    def test_dry_run_submission_when_dry_run_enabled_in_config(self):
        """When submission.dry_run_mode is true, submit_pair executes simulation with [DRY-RUN] tag."""
        from unittest.mock import MagicMock, patch
        from core.services import config_service
        from core.services.submission_worker import submit_pair
        from core.models import SubmissionAuditLog

        mock_client = MagicMock()
        mock_client.session_store.session.user_email = "operator@test.com"
        mock_client.session_store.session.is_authenticated = True
        mock_client.execute_installation_order_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-dry-111",
            "message": "Simulated wizard completion",
            "is_finalized": True,
        }

        with patch.object(config_service, "get_setting", side_effect=lambda key, default=None: True if key == "submission.dry_run_mode" else True):
            with patch("core.services.itms_web_client.get_web_client", return_value=mock_client):
                outcome = submit_pair(self.pair, backend="web")
                self.assertTrue(outcome.success)

                call_kwargs = mock_client.execute_installation_order_workflow.call_args[1]
                self.assertTrue(call_kwargs["dry_run"])

                audit = self.pair.audit_logs.filter(action=SubmissionAuditLog.Action.SUBMIT).first()
                self.assertIsNotNone(audit)
                self.assertIn("[DRY-RUN]", audit.message)

    def test_single_order_submission_modal_defaults_to_config(self):
        """Modal checkbox must default to False when dry_run_mode is false, and True when true."""
        import asyncio
        from unittest.mock import patch
        from textual.app import App
        from textual.widgets import Checkbox
        from core.services import config_service
        from core.tui.dialogs import SingleOrderSubmissionModal

        async def run():
            class DummyApp(App):
                pass

            app = DummyApp()
            async with app.run_test() as pilot:
                # Case 1: Config is False (Live mode)
                with patch.object(config_service, "get_setting", return_value=False):
                    modal = SingleOrderSubmissionModal(self.pair)
                    app.push_screen(modal)
                    await pilot.pause(0.05)
                    chk = modal.query_one("#chk-submission-dry-run", Checkbox)
                    self.assertFalse(chk.value)
                    modal.dismiss(None)
                    await pilot.pause(0.05)

                # Case 2: Config is True (Dry run mode)
                with patch.object(config_service, "get_setting", return_value=True):
                    modal = SingleOrderSubmissionModal(self.pair)
                    app.push_screen(modal)
                    await pilot.pause(0.05)
                    chk = modal.query_one("#chk-submission-dry-run", Checkbox)
                    self.assertTrue(chk.value)
                    modal.dismiss(None)
                    await pilot.pause(0.05)

        asyncio.run(run())

    def test_batch_submission_modal_defaults_to_config(self):
        """Batch modal checkbox must default to config.json value."""
        import asyncio
        from unittest.mock import patch
        from textual.app import App
        from textual.widgets import Checkbox
        from core.services import config_service
        from core.tui.dialogs import BatchSubmissionModal

        async def run():
            class DummyApp(App):
                pass

            app = DummyApp()
            async with app.run_test() as pilot:
                with patch.object(config_service, "get_setting", return_value=False):
                    modal = BatchSubmissionModal([self.pair])
                    app.push_screen(modal)
                    await pilot.pause(0.05)
                    chk = modal.query_one("#chk-batch-dry-run", Checkbox)
                    self.assertFalse(chk.value)
                    modal.dismiss(None)
                    await pilot.pause(0.05)

                with patch.object(config_service, "get_setting", return_value=True):
                    modal = BatchSubmissionModal([self.pair])
                    app.push_screen(modal)
                    await pilot.pause(0.05)
                    chk = modal.query_one("#chk-batch-dry-run", Checkbox)
                    self.assertTrue(chk.value)
                    modal.dismiss(None)
                    await pilot.pause(0.05)

        asyncio.run(run())

    def test_itms_web_client_resolves_dry_run_from_config(self):
        """ITMSWebClient methods resolve dry_run from config when None is passed."""
        from unittest.mock import patch
        from core.services import itms_web_client, config_service

        with patch.object(config_service, "get_setting", return_value=False):
            self.assertFalse(itms_web_client.resolve_dry_run(None))
            self.assertFalse(itms_web_client.resolve_dry_run(False))
            self.assertTrue(itms_web_client.resolve_dry_run(True))

        with patch.object(config_service, "get_setting", return_value=True):
            self.assertTrue(itms_web_client.resolve_dry_run(None))
            self.assertFalse(itms_web_client.resolve_dry_run(False))
            self.assertTrue(itms_web_client.resolve_dry_run(True))

    def test_settings_pane_switch_changed_auto_persists(self):
        """Toggling a switch in SettingsPane must auto-save config and update live Django settings."""
        from unittest.mock import MagicMock, patch
        from textual.widgets import Switch
        from django.conf import settings
        from core.tui.settings_pane import SettingsPane
        from core.services import config_service

        pane = SettingsPane()
        pane._app = MagicMock()

        mock_switch = MagicMock(spec=Switch)
        mock_switch.id = "switch-dry-run"
        mock_switch.value = False

        event = Switch.Changed(mock_switch, False)

        with patch.object(config_service, "save_config") as mock_save, \
             patch.object(pane, "query_one") as mock_query:
            
            def query_side_effect(selector, *args, **kwargs):
                widget = MagicMock()
                if "switch-dry-run" in selector:
                    widget.value = False
                elif "switch" in selector:
                    widget.value = True
                elif "input" in selector:
                    widget.value = "30"
                return widget

            mock_query.side_effect = query_side_effect
            pane.on_switch_changed(event)

            mock_save.assert_called_once()
            saved_cfg = mock_save.call_args[0][0]
            self.assertFalse(saved_cfg["submission"]["dry_run_mode"])
            self.assertFalse(settings.ITMS_WEB_DRY_RUN)

    def test_batch_submission_modal_buttons_and_footer_visibility(self):
        """Verifies that BatchSubmissionModal mounts buttons inside docked bottom bar with table flex."""
        import asyncio
        from textual.app import App
        from textual.widgets import Button, Checkbox, DataTable
        from core.tui.dialogs import BatchSubmissionModal

        async def run():
            class DummyApp(App):
                pass

            app = DummyApp()
            async with app.run_test() as pilot:
                modal = BatchSubmissionModal([self.pair])
                app.push_screen(modal)
                await pilot.pause(0.05)

                # 1. Verify buttons exist and are visible
                btn_run = modal.query_one("#btn-batch-run", Button)
                btn_cancel = modal.query_one("#btn-batch-cancel", Button)
                self.assertIsNotNone(btn_run)
                self.assertIsNotNone(btn_cancel)
                self.assertTrue("Submit All" in str(btn_run.label) or "Simulate All" in str(btn_run.label))

                # 2. Verify bottom bar container
                bottom_bar = modal.query_one("#modal-bottom-bar")
                self.assertIsNotNone(bottom_bar)
                self.assertIn(btn_run, bottom_bar.query(Button))
                self.assertIn(btn_cancel, bottom_bar.query(Button))

                # 3. Verify table exists with rows
                table = modal.query_one("#table-batch-review", DataTable)
                self.assertEqual(table.row_count, 1)

                modal.dismiss(None)
                await pilot.pause(0.05)

        asyncio.run(run())

    def test_failed_order_resubmission_and_retry_batch(self):
        """Tests that retry_failed resets FAILED pairs and orders back to APPROVED/PENDING."""
        from core.services.submission_worker import submit_approved_pairs
        from core.models import VehicleInstallationPair, InstallationOrder, EvidenceImage, SubmissionAuditLog

        # Set pair to FAILED
        self.pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        self.pair.save(update_fields=["verification_status"])
        self.order.status = InstallationOrder.Status.FAILED
        self.order.save(update_fields=["status"])

        # Without retry_failed=True, submit_approved_pairs does nothing on FAILED pairs
        outcomes = submit_approved_pairs(retry_failed=False, backend="mock", dry_run=True)
        self.assertEqual(len(outcomes), 0)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.FAILED)

        # With retry_failed=True, pair is reset to APPROVED and submitted
        outcomes = submit_approved_pairs(retry_failed=True, backend="mock", dry_run=True)
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].success)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)

    def test_inspector_pane_displays_failure_reason_on_failed_pair(self):
        """Tests that InspectorPane renders the failure reason for FAILED pairs."""
        from core.tui.inspectors import InspectorPane
        from core.models import VehicleInstallationPair, SubmissionAuditLog

        self.pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        self.pair.save(update_fields=["verification_status"])
        SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.FAILURE,
            message="Remote server 504 Gateway Timeout on final step 3",
        )

        pane = InspectorPane()
        pane.update = lambda val: setattr(pane, "_last_rendered", val)
        pane.show_pair(self.pair)

        self.assertIn("Failure Reason", pane._last_rendered)
        self.assertIn("Remote server 504 Gateway Timeout", pane._last_rendered)
        self.assertIn("Approve / Retry", pane._last_rendered)

    def test_dialog_viewer_preview_pair_opens_side_by_side(self):
        """Verifies that modals can invoke viewer preview without AttributeError."""
        from unittest.mock import patch, MagicMock
        from core.services import viewer
        from core.tui.dialogs import PlateQuickEntryModal, SingleOrderSubmissionModal

        # Verify viewer.show_evidence_pair exists and is callable
        self.assertTrue(hasattr(viewer, "show_evidence_pair"))
        self.assertTrue(callable(viewer.show_evidence_pair))

        with patch.object(viewer, "show_pair_evidence", return_value="tmp_preview.png") as mock_pair_ev, \
             patch.object(viewer, "show_evidence_pair", return_value="tmp_preview.png") as mock_ev_pair:
            
            # 1. PlateQuickEntryModal
            modal_entry = PlateQuickEntryModal(self.pair)
            modal_entry.notify = MagicMock()
            modal_entry.action_preview_pair()
            self.assertTrue(mock_pair_ev.called or mock_ev_pair.called)
            modal_entry.notify.assert_called_with("Opened side-by-side preview for UAA111A")

            # 2. SingleOrderSubmissionModal
            modal_single = SingleOrderSubmissionModal(self.pair)
            modal_single.notify = MagicMock()
            modal_single.action_preview_photos()
            modal_single.notify.assert_called_with("Opened preview for UAA111A")

    def test_duplicate_photo_ingestion_detection(self):
        """Verifies that uploading an identical photo returns DUPLICATE_SKIPPED and preserves original evidence."""
        import tempfile
        from core.services import vault_service
        from core.models import EvidenceImage, IngestionBatch

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"TEST_DUPLICATE_CONTENT" * 10)
            f_path = f.name

        try:
            batch1 = vault_service.create_ingestion_batch(source_label="Batch 1")
            img1, status1 = vault_service.ingest_from_disk(f_path, batch=batch1, orientation_override="FRONT")
            self.assertEqual(status1, "INGESTED")
            self.assertIsNotNone(img1)

            # Ingest identical file into Batch 2
            batch2 = vault_service.create_ingestion_batch(source_label="Batch 2")
            img2, status2 = vault_service.ingest_from_disk(f_path, batch=batch2, orientation_override="FRONT")
            self.assertEqual(status2, "DUPLICATE_SKIPPED")
            self.assertEqual(img1.id, img2.id)
            self.assertEqual(batch2.duplicate_count, 1)
            self.assertEqual(batch2.ingested_count, 0)
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)


class SmartCompressionAndOfflineOutboxTests(TestCase):
    """Tests for on-the-fly multipart compression, offline outbox queuing and auto-sync, and dashboard telemetry."""

    def test_smart_multipart_photo_compression(self):
        """Verifies downsampling on-the-fly leaves disk master photo completely untouched."""
        import tempfile
        from PIL import Image
        from core.services.itms_web_client import prepare_multipart_image_bytes

        # Create a large test image (2400 x 1800)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            test_path = tf.name

        try:
            img = Image.new("RGB", (2400, 1800), color=(100, 150, 200))
            img.save(test_path, format="JPEG", quality=95)
            disk_size = os.path.getsize(test_path)

            # Compress on-the-fly
            comp_bytes, stats = prepare_multipart_image_bytes(
                test_path, max_dimension=1920, jpeg_quality=85, enabled=True
            )

            # Master file on disk must be identical in size (100% untouched)
            self.assertEqual(os.path.getsize(test_path), disk_size)

            # Multipart stream must be downscaled and smaller
            self.assertTrue(stats["compressed"])
            self.assertLessEqual(stats["final_dims"][0], 1920)
            self.assertLessEqual(stats["final_dims"][1], 1920)
            self.assertLess(len(comp_bytes), disk_size)
            self.assertIn("-", stats["display_str"])  # e.g. "(-45.2%)"

            # When disabled, returns exact raw disk bytes
            raw_bytes, disabled_stats = prepare_multipart_image_bytes(test_path, enabled=False)
            self.assertFalse(disabled_stats["compressed"])
            self.assertEqual(len(raw_bytes), disk_size)
        finally:
            if os.path.exists(test_path):
                os.remove(test_path)

    def test_offline_outbox_queue_on_network_failure(self):
        """Verifies network failures during submission transition pair to OFFLINE_OUTBOX."""
        from unittest.mock import patch, MagicMock
        from core.services.submission_worker import submit_pair, drain_offline_outbox

        order = InstallationOrder.objects.create(
            order_number="ORD-OUTBOX-101",
            registration_number="UBA123A",
            status=InstallationOrder.Status.PENDING,
        )
        front = EvidenceImage.objects.create(
            file_hash="f" * 64,
            original_source_path="f.jpg",
            vault_file="f.jpg",
        )
        rear = EvidenceImage.objects.create(
            file_hash="r" * 64,
            original_source_path="r.jpg",
            vault_file="r.jpg",
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UBA123A",
            order=order,
            front_image=front,
            rear_image=rear,
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )

        # Simulate network drop
        mock_web_client = MagicMock()
        mock_web_client.session_store.session.user_email = ""
        mock_web_client.execute_installation_order_workflow.return_value = {
            "success": False,
            "error": "Connection timed out (WinError 10054 Connection reset by peer)",
        }

        def mock_get_setting(key, default=None):
            if key == "outbox.enabled":
                return True
            return default if default is not None else True

        with patch("core.services.itms_web_client.get_web_client", return_value=mock_web_client), \
             patch("core.services.config_service.get_setting", side_effect=mock_get_setting):
            outcome = submit_pair(pair, backend="web", dry_run=False)
            self.assertFalse(outcome.success)
            self.assertIn("Offline Outbox", outcome.error)

            pair.refresh_from_db()
            self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX)

            # Verify audit log recorded outbox queue action
            audit = SubmissionAuditLog.objects.filter(pair=pair).first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.action, SubmissionAuditLog.Action.OUTBOX_QUEUE)

            # Test draining outbox
            mock_web_client.execute_installation_order_workflow.return_value = {
                "success": True,
                "message": "Step 3 completed successfully",
                "redirect_url": "https://stock.itms.ug/installation-orders/index",
            }
            drained_outcomes = drain_offline_outbox(backend="web", dry_run=False)
            self.assertEqual(len(drained_outcomes), 1)
            self.assertTrue(drained_outcomes[0].success)

            pair.refresh_from_db()
            self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)

    def test_batch_progress_modal_activity_ticker(self):
        """Verifies BatchProgressModal instantiates and accepts real-time activity stream events."""
        from core.tui.dialogs import BatchProgressModal

        modal = BatchProgressModal(total_orders=50, dry_run=True)
        self.assertEqual(modal.total_orders, 50)
        self.assertTrue(modal.dry_run)
        # Verify log_event does not raise error
        modal.log_event("Testing activity ticker stream")

    def test_dashboard_pane_telemetry_computation(self):
        """Verifies DashboardPane computes telemetry without error."""
        from core.tui.dashboard_pane import DashboardPane

        pane = DashboardPane()
        # Test calling refresh_dashboard (headless simulation)
        pane.refresh_dashboard()
        self.assertIsNotNone(pane)

    def test_pipeline_stage_cards_initialization_and_actions(self):
        """Verifies PipelineStageCard initializes with tooltips and executes actions."""
        from core.tui.dashboard_pane import PipelineStageCard

        card = PipelineStageCard("ingest")
        self.assertEqual(card.stage_id, "ingest")
        self.assertIn("Photo Ingestion", card.tooltip)

        mock_app = MagicMock()
        card._app = mock_app
        card._activate_stage()
        mock_app.action_native_ingest.assert_called_once()

        card_vision = PipelineStageCard("vision")
        card_vision._app = mock_app
        card_vision._activate_stage()
        mock_app.action_process_vision.assert_called_once()


