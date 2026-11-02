import json
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from core.models import VehicleInstallationPair, InstallationOrder, InstallationKit
from core.views.pairs import api_pair_detail, api_pairs_list

User = get_user_model()


class PairDetailHardwareEnrichmentTest(TestCase):
    def setUp(self):
        self.user, _ = User.objects.get_or_create(username="testinspector")
        self.factory = RequestFactory()

    def test_pair_detail_includes_order_hardware_and_kit(self):
        order = InstallationOrder.objects.create(
            order_number="PO-TEST-ORDER-1",
            registration_number="UMA959GH",
            service_type="First Time Registration",
            vin="LB5DJ3R23SZ835915",
            old_registration_number="—",
            front_plate_serial="000389975",
            rear_plate_serial="000389976",
            gps_tracker_id="8BAE470304CC",
            front_beacon_id="8ADC470655DF",
            rear_beacon_id="8ADC4706645F",
        )
        pair = VehicleInstallationPair.objects.create(
            order=order,
            registration_number_detected="UMA 959GH",
            verification_status="PENDING",
        )

        request = self.factory.get(f"/api/pairs/{pair.id}/")
        request.user = self.user

        response = api_pair_detail(request, pair.id)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue(data.get("success"))
        pair_data = data.get("pair", {})

        # Verify order details
        ord_data = pair_data.get("order")
        self.assertIsNotNone(ord_data)
        self.assertEqual(ord_data.get("service_type"), "First Time Registration")
        self.assertEqual(ord_data.get("vin"), "LB5DJ3R23SZ835915")
        self.assertEqual(ord_data.get("old_registration_number"), "—")
        self.assertEqual(ord_data.get("registration_number"), "UMA959GH")
        self.assertEqual(ord_data.get("front_plate_serial"), "000389975")
        self.assertEqual(ord_data.get("rear_plate_serial"), "000389976")
        self.assertEqual(ord_data.get("gps_tracker_id"), "8BAE470304CC")
        self.assertEqual(ord_data.get("front_beacon_id"), "8ADC470655DF")
        self.assertEqual(ord_data.get("rear_beacon_id"), "8ADC4706645F")

        # Verify hardware convenience block
        hw = pair_data.get("hardware")
        self.assertIsNotNone(hw)
        self.assertEqual(hw.get("service_type"), "First Time Registration")
        self.assertEqual(hw.get("vin"), "LB5DJ3R23SZ835915")
        self.assertEqual(hw.get("registration_number"), "UMA959GH")
        self.assertEqual(hw.get("front_plate"), "000389975")
        self.assertEqual(hw.get("rear_plate"), "000389976")
        self.assertEqual(hw.get("gps_tracker"), "8BAE470304CC")
        self.assertEqual(hw.get("front_beacon"), "8ADC470655DF")
        self.assertEqual(hw.get("rear_beacon"), "8ADC4706645F")

    def test_pair_detail_hardware_fallback_to_installation_kit(self):
        """When an order has blank serials or is unlinked, hardware pre-fills from InstallationKit."""
        kit = InstallationKit.objects.create(
            kit_code="IK-UMA959GH",
            registration_number="UMA 959GH",
            front_plate="000389975",
            rear_plate="000389976",
            gps_tracker="8BAE470304CC",
            front_tracker="8ADC470655DF",
            rear_tracker="8ADC4706645F",
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA 959GH",
            verification_status="PENDING",
        )

        request = self.factory.get(f"/api/pairs/{pair.id}/")
        request.user = self.user

        response = api_pair_detail(request, pair.id)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode("utf-8"))
        hw = data.get("pair", {}).get("hardware", {})
        self.assertEqual(hw.get("front_plate"), "000389975")
        self.assertEqual(hw.get("rear_plate"), "000389976")
        self.assertEqual(hw.get("gps_tracker"), "8BAE470304CC")
        self.assertEqual(hw.get("front_beacon"), "8ADC470655DF")
        self.assertEqual(hw.get("rear_beacon"), "8ADC4706645F")

    def test_pairs_list_includes_hardware_and_kit(self):
        order = InstallationOrder.objects.create(
            order_number="PO-TEST-ORDER-2",
            registration_number="UMA959GH",
            service_type="First Time Registration",
            vin="LB5DJ3R23SZ835915",
            front_plate_serial="000389975",
            rear_plate_serial="000389976",
            gps_tracker_id="8BAE470304CC",
            front_beacon_id="8ADC470655DF",
            rear_beacon_id="8ADC4706645F",
        )
        pair = VehicleInstallationPair.objects.create(
            order=order,
            operator=self.user,
            registration_number_detected="UMA 959GH",
            verification_status="PENDING",
        )
        request = self.factory.get("/api/pairs/")
        request.user = self.user
        response = api_pairs_list(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue(data.get("success"))
        pairs = data.get("pairs", [])
        self.assertGreaterEqual(len(pairs), 1)
        target = [p for p in pairs if p["id"] == pair.id][0]
        self.assertIn("hardware", target)
        self.assertEqual(target["hardware"]["gps_tracker"], "8BAE470304CC")
        self.assertEqual(target["hardware"]["vin"], "LB5DJ3R23SZ835915")

