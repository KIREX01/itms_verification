import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from core.models import (
    InstallationOrder,
    InstallationKit,
    OperatorITMSSession,
)


class ITMSTabDetailsTestCase(TestCase):
    """
    Unit tests verifying the enriched ITMS order, archive, and installation kit details.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="itms_op",
            password="password123",
            first_name="Emmy",
            last_name="Akena",
        )
        self.session = OperatorITMSSession.objects.create(
            user=self.user,
            itms_email="operator@spiro.ug",
            active_bond_code="AGM",
            is_authenticated=True,
            expires_at=timezone.now() + timedelta(days=1),
            cookies={"_identity-frontend": "mock-token-abc-123"},
        )
        self.client.force_login(self.user)

    def test_active_order_details_payload(self):
        """Active order returns VIN, old plate, front/rear plate, reg num, and tracker details."""
        order = InstallationOrder.objects.create(
            order_number="ORD-UMA509QV",
            registration_number="UMA509QV",
            vin="L6XRZS1A4T0090404",
            old_registration_number="UBA123B",
            front_plate_serial="001264797",
            front_plate_type="PN-PBL-M-UMA-SQR-BWW",
            rear_plate_serial="001264798",
            rear_plate_type="PN-PBL-M-UMA-SQR-BWW",
            gps_tracker_id="8BAE470846FA",
            front_beacon_id="8ADC4710EDDD",
            rear_beacon_id="8ADC4710F4CC",
            warehouse_name="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
            order_status="Under installation",
            is_archived=False,
        )

        res = self.client.get(reverse("core:api_itms_order_detail", args=[order.order_number]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        o = data.get("order", {})

        # Verify Vehicle Details
        self.assertEqual(o.get("vin"), "L6XRZS1A4T0090404")
        self.assertEqual(o.get("old_registration_number"), "UBA123B")
        self.assertEqual(o.get("registration_number"), "UMA509QV")

        # Verify Hardware & Tracker Details
        hw = o.get("hardware", {})
        self.assertEqual(hw.get("gps_tracker_id"), "8BAE470846FA")
        self.assertEqual(hw.get("front_beacon_id"), "8ADC4710EDDD")
        self.assertEqual(hw.get("rear_beacon_id"), "8ADC4710F4CC")
        self.assertEqual(hw.get("front_plate_serial"), "001264797")
        self.assertEqual(hw.get("rear_plate_serial"), "001264798")

    def test_archive_order_details_payload(self):
        """Archive order returns installed by, warehouse, VIN, article types and serials."""
        order = InstallationOrder.objects.create(
            order_number="PO-UMA609QV-061126",
            registration_number="UMA609QV",
            vin="L6XRZS1A5T0090413",
            installation_officer="EMMY AKENA",
            warehouse_name="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
            front_plate_serial="001264797",
            front_plate_type="PN-PBL-M-UMA-SQR-BWW",
            rear_plate_serial="001264798",
            rear_plate_type="PN-PBL-M-UMA-SQR-BWW",
            gps_tracker_id="8BAE470846FA",
            front_beacon_id="8ADC471100E3",
            rear_beacon_id="8ADC47110089",
            order_status="Installed",
            is_archived=True,
        )

        res = self.client.get(reverse("core:api_itms_order_detail", args=[order.order_number]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        o = data.get("order", {})

        self.assertEqual(o.get("installation_officer"), "EMMY AKENA")
        self.assertEqual(o.get("warehouse_name"), "AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION")
        self.assertEqual(o.get("vin"), "L6XRZS1A5T0090413")
        self.assertTrue(o.get("is_archived"))

        # Verify structured component fields
        self.assertEqual(o.get("front_plate", {}).get("article_name"), "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(o.get("front_plate", {}).get("serial_number"), "001264797")
        self.assertEqual(o.get("rear_plate", {}).get("article_name"), "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(o.get("rear_plate", {}).get("serial_number"), "001264798")
        self.assertEqual(o.get("gps_tracker", {}).get("serial_number"), "8BAE470846FA")
        self.assertEqual(o.get("front_beacon", {}).get("serial_number"), "8ADC471100E3")
        self.assertEqual(o.get("rear_beacon", {}).get("serial_number"), "8ADC47110089")

    def test_installation_kit_details_payload(self):
        """Installation kit returns complete breakdown matching stock.itms.ug/installation-kit/<id>/main/information."""
        kit = InstallationKit.objects.create(
            kit_code="IK-UMA697QV",
            registration_number="UMA 697QV",
            status="New",
            created_by_user="Kwoleka Peter",
            created_date="06.11.2026",
            warehouse="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
            front_plate="001266618",
            front_plate_article="PN-PBL-M-UMA-SQR-BWW",
            rear_plate="001266619",
            rear_plate_article="PN-PBL-M-UMA-SQR-BWW",
            gps_tracker="8BAE470846D0",
            gps_article="GPS",
            sim_serial="892561000200984053",
            sim_mac="0391515236",
            sim_article="SIM chip",
            front_tracker="8ADC4711037B",
            front_tracker_article="BLE",
            rear_tracker="8ADC4711010F",
            rear_tracker_article="BLE",
            kit_uuid="63d57860-29ba-47e7-a0ca-969d89bd7640",
            facility_code="AGM",
        )

        res = self.client.get(reverse("core:api_itms_order_detail", args=[kit.kit_code]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("is_kit"))
        o = data.get("order", {})

        # Information card fields
        self.assertEqual(o.get("registration_number"), "UMA 697QV")
        self.assertEqual(o.get("status"), "New")
        self.assertEqual(o.get("created_by_user"), "Kwoleka Peter")
        self.assertEqual(o.get("created_date"), "06.11.2026")
        self.assertEqual(o.get("warehouse_name"), "AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION")

        # Front Plate card fields
        fp = o.get("front_plate", {})
        self.assertEqual(fp.get("article_name"), "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(fp.get("serial_number"), "001266618")
        self.assertEqual(fp.get("registration_number"), "UMA 697QV")

        # Rear Plate card fields
        rp = o.get("rear_plate", {})
        self.assertEqual(rp.get("article_name"), "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(rp.get("serial_number"), "001266619")
        self.assertEqual(rp.get("registration_number"), "UMA 697QV")

        # GPS Tracker card fields
        gps = o.get("gps_tracker", {})
        self.assertEqual(gps.get("article_name"), "GPS")
        self.assertEqual(gps.get("serial_number"), "8BAE470846D0")

        # SIM Card card fields
        sim = o.get("sim_card", {})
        self.assertEqual(sim.get("article_name"), "SIM chip")
        self.assertEqual(sim.get("serial_number"), "892561000200984053")
        self.assertEqual(sim.get("mac_address"), "0391515236")

        # Front Tracker card fields
        ft = o.get("front_tracker", {})
        self.assertEqual(ft.get("article_name"), "BLE")
        self.assertEqual(ft.get("serial_number"), "8ADC4711037B")

        # Rear Tracker card fields
        rt = o.get("rear_tracker", {})
        self.assertEqual(rt.get("article_name"), "BLE")
        self.assertEqual(rt.get("serial_number"), "8ADC4711010F")

    def test_installation_kit_to_dict_includes_all_extended_fields(self):
        """InstallationKit.to_dict() must serialize all extended hardware and article fields."""
        kit = InstallationKit.objects.create(
            kit_code="IK-TEST-01",
            registration_number="UMA 888ZZ",
            front_plate="00111",
            front_plate_article="PN-PBL-M-UMA-SQR-BWW",
            rear_plate="00222",
            rear_plate_article="PN-PBL-M-UMA-SQR-BWW",
            gps_tracker="GPS111",
            gps_article="GPS",
            sim_serial="SIM111",
            sim_mac="039000",
            sim_article="SIM chip",
            front_tracker="BLE111",
            front_tracker_article="BLE",
            rear_tracker="BLE222",
            rear_tracker_article="BLE",
        )
        d = kit.to_dict()
        self.assertEqual(d["front_plate_article"], "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(d["rear_plate_article"], "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(d["gps_article"], "GPS")
        self.assertEqual(d["sim_article"], "SIM chip")
        self.assertEqual(d["sim_serial"], "SIM111")
        self.assertEqual(d["sim_mac"], "039000")
        self.assertEqual(d["front_tracker_article"], "BLE")
        self.assertEqual(d["rear_tracker_article"], "BLE")

    def test_itms_orders_explorer_returns_extended_fields(self):
        """api_itms_orders_explorer returns vin, old plate, and hardware serials for orders and kits."""
        order = InstallationOrder.objects.create(
            order_number="ORD-EXP-001",
            registration_number="UMA999AA",
            vin="L6XRZS1A4T9999999",
            old_registration_number="OLD-111",
            front_plate_serial="001111",
            rear_plate_serial="002222",
            gps_tracker_id="8BAE47000000",
            is_archived=False,
        )

        res = self.client.get(reverse("core:api_itms_orders_explorer"), {"tab": "active", "source": "local"})
        self.assertEqual(res.status_code, 200)
        orders = res.json().get("orders", [])
        matched = next((o for o in orders if o.get("order_number") == order.order_number), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.get("vin"), "L6XRZS1A4T9999999")
        self.assertEqual(matched.get("old_registration_number"), "OLD-111")
        self.assertEqual(matched.get("hardware", {}).get("gps_tracker_id"), "8BAE47000000")

    def test_itms_kits_explorer_returns_all_columns(self):
        """api_itms_orders_explorer with tab=kits returns all 10 columns matching ITMS."""
        kit = InstallationKit.objects.create(
            kit_code="IK-UMA777BB",
            registration_number="UMA 777BB",
            front_plate="007771",
            rear_plate="007772",
            front_tracker="8ADC47111111",
            rear_tracker="8ADC47222222",
            gps_tracker="8BAE47333333",
            warehouse="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
            status="New",
            created_date="08.11.2026",
            facility_code="AGM",
        )

        res = self.client.get(reverse("core:api_itms_orders_explorer"), {"tab": "kits", "source": "local"})
        self.assertEqual(res.status_code, 200)
        kits = res.json().get("kits", [])
        matched = next((k for k in kits if k.get("kit_code") == kit.kit_code), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.get("registration_number"), "UMA 777BB")
        self.assertEqual(matched.get("front_plate"), "007771")
        self.assertEqual(matched.get("rear_plate"), "007772")
        self.assertEqual(matched.get("front_tracker"), "8ADC47111111")
        self.assertEqual(matched.get("rear_tracker"), "8ADC47222222")
        self.assertEqual(matched.get("gps_tracker"), "8BAE47333333")
        self.assertEqual(matched.get("status"), "New")

    @patch("core.views.orders.get_web_client")
    @patch("core.services.order_sync.OrderSyncService")
    def test_api_itms_sync_now_all_tab(self, mock_sync_svc_cls, mock_get_web_client):
        """POST /api/itms/orders/sync/now/ with tab=all executes both shift sync and kits sync."""
        mock_client = MagicMock()
        mock_client.session_store.session.is_cookie_valid.return_value = True
        mock_get_web_client.return_value = mock_client

        mock_svc_inst = MagicMock()
        mock_svc_inst.sync_shift_scoped.return_value = {
            "success": True,
            "target_date": "08.11.2026",
            "active": {"created": 2, "updated": 3, "total_active_seen": 15},
            "archive": {"created": 1, "updated": 4, "installed_verified": 5, "total_fetched": 20},
        }
        mock_svc_inst.sync_installation_kits_scoped.return_value = {
            "success": True,
            "created": 5,
            "updated": 10,
            "total_fetched": 45,
            "pages_fetched": 3,
        }
        mock_sync_svc_cls.return_value = mock_svc_inst

        res = self.client.post(reverse("core:api_itms_sync_now"), {"tab": "all", "pages": "25"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["created"], 8)  # 2 + 1 + 5
        self.assertEqual(data["updated"], 17) # 3 + 4 + 10
        self.assertEqual(data["total_fetched"], 80) # 15 + 20 + 45
        self.assertIn("ITMS Sync complete", data["message"])
        mock_svc_inst.sync_shift_scoped.assert_called_once()
        mock_svc_inst.sync_installation_kits_scoped.assert_called_once()


