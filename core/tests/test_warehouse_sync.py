import json
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from core.models import (
    Warehouse,
    FacilitySyncMetadata,
    OperatorITMSSession,
    OperatorProfile,
)
from core.services import bond_service
from core.services.itms_web_client import ITMSWebClient, ITMSWebSessionData

User = get_user_model()

SAMPLE_WAREHOUSES_HTML = """
<div id="w4" class="grid-view table-tr-clicked">
<table class="table table-striped table-bordered">
<thead>
<tr>
<th class="text-center">#</th>
<th><a href="/warehouses?sort=name">Name</a></th>
<th><a href="/warehouses?sort=address">Address</a></th>
<th><a href="/warehouses?sort=zip">Zip</a></th>
<th><a href="/warehouses?sort=city">City</a></th>
<th><a href="/warehouses?sort=designation_number">Designation number</a></th>
<th><a href="/warehouses?sort=company_id">Company</a></th>
<th class="text-center"><a href="/warehouses?sort=status">Status</a></th>
<th><a href="/warehouses?sort=group">Group</a></th>
<th><a href="/warehouses?sort=created_date">Created date</a></th>
</tr>
</thead>
<tbody>
<tr data-key="8c59352a-41a6-4548-adb1-e79ca80b6a15" data-url="/warehouse/8c59352a-41a6-4548-adb1-e79ca80b6a15/transactions">
<td class="text-center">1</td>
<td>AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</td>
<td>Spiro Uganda Showroom, 11, Old Portbell Road</td>
<td></td>
<td>Kampala</td>
<td></td>
<td>VGS LTD</td>
<td class="text-center"><span class="badge badge-success">Active</span></td>
<td>MC DEALER</td>
<td>05.11.2024</td>
</tr>
<tr data-key="65d69bcc-6cdd-43ca-84d0-10a84ecb2c88" data-url="/warehouse/65d69bcc-6cdd-43ca-84d0-10a84ecb2c88/transactions">
<td class="text-center">2</td>
<td>AGM (STORAGE) SOLUTIONS UGANDA LIMITED (SPIRO)</td>
<td>Spiro Uganda Showroom, 11, Old Portbell Road</td>
<td></td>
<td>Kampala</td>
<td></td>
<td>VGS LTD</td>
<td class="text-center"><span class="badge badge-success">Active</span></td>
<td>MC DEALER</td>
<td>05.11.2024</td>
</tr>
</tbody>
</table>
</div>
"""


class WarehouseSyncTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_operator",
            email="operator@example.com",
            password="testpassword123",
        )
        self.profile, _ = OperatorProfile.objects.get_or_create(
            user=self.user,
            defaults={"active_facility": "AGM", "allowed_facilities": ["AGM"]},
        )
        self.session_obj = OperatorITMSSession.objects.create(
            user=self.user,
            itms_email="e.akena@itms-ug.com",
            is_authenticated=True,
            cookies={"_identity-frontend": "dummy_cookie_val"},
            active_bond_code="AGM",
            active_bond_name="AGM Bonded Warehouse",
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_parse_warehouses_html(self):
        """Tests parsing ITMS HTML from /warehouses into structured dictionaries."""
        web_client = ITMSWebClient(user=self.user)
        warehouses = web_client.parse_warehouses_html(SAMPLE_WAREHOUSES_HTML)

        self.assertEqual(len(warehouses), 2)
        wh1 = warehouses[0]
        self.assertEqual(wh1["warehouse_uuid"], "8c59352a-41a6-4548-adb1-e79ca80b6a15")
        self.assertEqual(wh1["name"], "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")
        self.assertEqual(wh1["address"], "Spiro Uganda Showroom, 11, Old Portbell Road")
        self.assertEqual(wh1["city"], "Kampala")
        self.assertEqual(wh1["company"], "VGS LTD")
        self.assertEqual(wh1["status"], "Active")
        self.assertEqual(wh1["group_name"], "MC DEALER")
        self.assertEqual(wh1["created_date_itms"], "05.11.2024")
        self.assertEqual(wh1["itms_url"], "/warehouse/8c59352a-41a6-4548-adb1-e79ca80b6a15/transactions")

        wh2 = warehouses[1]
        self.assertEqual(wh2["warehouse_uuid"], "65d69bcc-6cdd-43ca-84d0-10a84ecb2c88")
        self.assertEqual(wh2["name"], "AGM (STORAGE) SOLUTIONS UGANDA LIMITED (SPIRO)")

    @patch.object(ITMSWebClient, "_request_with_retry")
    def test_fetch_warehouses_from_itms_syncs_to_db(self, mock_request):
        """Tests that fetch_warehouses_from_itms persists to Warehouse model & updates FacilitySyncMetadata."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_WAREHOUSES_HTML

        mock_ajax_resp = MagicMock()
        mock_ajax_resp.status_code = 200
        mock_ajax_resp.json.return_value = {
            "results": [
                {"id": "8c59352a-41a6-4548-adb1-e79ca80b6a15", "text": "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)"},
                {"id": "65d69bcc-6cdd-43ca-84d0-10a84ecb2c88", "text": "AGM (STORAGE) SOLUTIONS UGANDA LIMITED (SPIRO)"},
            ]
        }

        def mock_dispatch(method, url, **kwargs):
            if "ajax-search" in url:
                return mock_ajax_resp
            return mock_resp

        mock_request.side_effect = mock_dispatch

        web_client = ITMSWebClient(user=self.user)
        res = web_client.fetch_warehouses_from_itms()

        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["count"], 2)

        # Verify Warehouse objects in DB
        self.assertTrue(Warehouse.objects.filter(code="AGM").exists())
        self.assertTrue(Warehouse.objects.filter(code="AGM_STORAGE").exists())

        agm = Warehouse.objects.get(code="AGM")
        self.assertEqual(agm.warehouse_uuid, "8c59352a-41a6-4548-adb1-e79ca80b6a15")
        self.assertEqual(agm.company, "VGS LTD")
        self.assertEqual(agm.status, "Active")

        # Verify FacilitySyncMetadata was updated with warehouse link
        meta = FacilitySyncMetadata.objects.get(facility_code="AGM")
        self.assertEqual(meta.warehouse_uuid, "8c59352a-41a6-4548-adb1-e79ca80b6a15")
        self.assertEqual(meta.warehouse_name, agm.name)
        self.assertEqual(meta.warehouse, agm)

        # Verify OperatorITMSSession active warehouse was linked
        self.session_obj.refresh_from_db()
        self.assertEqual(self.session_obj.active_warehouse_uuid, "8c59352a-41a6-4548-adb1-e79ca80b6a15")
        self.assertEqual(self.session_obj.active_warehouse, agm)
        self.assertEqual(self.session_obj.active_bond_code, "AGM")

    def test_bond_service_discovers_db_warehouses(self):
        """Tests that bond_service.get_all_discovered_warehouses prioritizes database Warehouse model."""
        Warehouse.objects.create(
            warehouse_uuid="8c59352a-41a6-4548-adb1-e79ca80b6a15",
            code="AGM",
            name="AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)",
            address="Spiro Uganda Showroom, 11, Old Portbell Road",
            city="Kampala",
            company="VGS LTD",
            status="Active",
            group_name="MC DEALER",
            is_active_bond=True,
        )
        Warehouse.objects.create(
            warehouse_uuid="65d69bcc-6cdd-43ca-84d0-10a84ecb2c88",
            code="AGM_STORAGE",
            name="AGM (STORAGE) SOLUTIONS UGANDA LIMITED (SPIRO)",
            address="Spiro Uganda Showroom, 11, Old Portbell Road",
            city="Kampala",
            company="VGS LTD",
            status="Active",
            group_name="MC DEALER",
            is_active_bond=False,
        )

        all_bonds = bond_service.get_all_discovered_warehouses()
        codes = [b["code"] for b in all_bonds]
        self.assertIn("AGM", codes)
        self.assertIn("AGM_STORAGE", codes)

        agm_bond = next(b for b in all_bonds if b["code"] == "AGM")
        self.assertEqual(agm_bond["warehouse_id"], "8c59352a-41a6-4548-adb1-e79ca80b6a15")
        self.assertEqual(agm_bond["source"], "DATABASE_WAREHOUSE")
        self.assertEqual(all_bonds[0]["code"], "AGM")

    def test_api_itms_warehouses_endpoints(self):
        """Tests GET and POST /api/itms/warehouses/ for setting active bond and updating DB state."""
        wh = Warehouse.objects.create(
            warehouse_uuid="8c59352a-41a6-4548-adb1-e79ca80b6a15",
            code="AGM",
            name="AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)",
            city="Kampala",
            status="Active",
            is_active_bond=False,
        )

        # GET request
        resp = self.client.get("/api/itms/warehouses/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(any(w["code"] == "AGM" for w in data["warehouses"]))

        # POST request to switch active bond
        post_resp = self.client.post(
            "/api/itms/warehouses/",
            data=json.dumps({"code": "AGM", "name": wh.name}),
            content_type="application/json",
        )
        self.assertEqual(post_resp.status_code, 200)
        post_data = post_resp.json()
        self.assertTrue(post_data["success"])
        self.assertEqual(post_data["active_bond"]["code"], "AGM")

        # Verify DB model was set to is_active_bond=True
        wh.refresh_from_db()
        self.assertTrue(wh.is_active_bond)

        # Verify FacilitySyncMetadata was updated
        meta = FacilitySyncMetadata.objects.get(facility_code="AGM")
        self.assertEqual(meta.warehouse_uuid, wh.warehouse_uuid)
        self.assertEqual(meta.warehouse, wh)
