"""
Unit tests for ITMS Installation Kits fetching, parsing, local caching, and TUI inspector rendering.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.models import InstallationKit
from core.services.itms_web_client import get_web_client
from core.tui.inspectors import InspectorPane


SAMPLE_KITS_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="content-wrapper">
    <div id="w3" class="grid-view table-tr-clicked">
        <table class="table table-bordered table-hover">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Registration number</th>
                    <th>Front Plate</th>
                    <th>Rear Plate</th>
                    <th>Front Tracker</th>
                    <th>Rear Tracker</th>
                    <th>GPS Tracker</th>
                    <th>Warehouse</th>
                    <th>Status</th>
                    <th>Created date</th>
                </tr>
            </thead>
            <tbody>
                <tr data-url="/installation-kit/87a4c9b7-6974-445b-9dcb-4435b510c655/main/information" data-key="87a4c9b7-6974-445b-9dcb-4435b510c655">
                    <td>IK-UMA300PW</td>
                    <td>UMA 300PW</td>
                    <td>001198122</td>
                    <td>001198123</td>
                    <td>8ADC470FE5F7</td>
                    <td>8ADC470FE4BB</td>
                    <td>8BAE4707B340</td>
                    <td>AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</td>
                    <td>New</td>
                    <td class="text-center">24.09.2026</td>
                </tr>
                <tr data-url="/installation-kit/fffcc34b-b700-490f-992c-065821dca0d1/main/information" data-key="fffcc34b-b700-490f-992c-065821dca0d1">
                    <td>IK-UMA295PW</td>
                    <td>UMA 295PW</td>
                    <td>001198112</td>
                    <td>001198113</td>
                    <td>8ADC470FE628</td>
                    <td>8ADC470FE02A</td>
                    <td>8BAE4707C4E3</td>
                    <td>AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</td>
                    <td>New</td>
                    <td class="text-center">24.09.2026</td>
                </tr>
            </tbody>
        </table>
        <div class="summary">Showing 1-20 of 1,200 items.</div>
        <ul class="pagination">
            <li class="page-item active"><a class="page-link" href="/installation-kits/index?page=1">1</a></li>
            <li class="page-item next"><a class="page-link" href="/installation-kits/index?page=2">&raquo;</a></li>
        </ul>
    </div>
</div>
</body>
</html>
"""

SAMPLE_DETAIL_HTML = """
<!DOCTYPE html>
<html>
<body>
<form id="infoForm">
    <div class="card"><div class="card-body">
        <div>
            <p><strong>Registration number</strong>:&ensp;UMA 902PC</p>
            <p><strong>Status</strong>:&ensp;New</p>
            <p><strong>Created by user</strong>:&ensp;<a href="/user/9c4a">JULIET NGUNA </a></p>
        </div>
    </div></div>
    <hr>
    <div class="card">
        <div class="card-header"><h5 class="card-title">Front plate</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;PN-PBL-M-UMA-SQR-BWW</p>
            <p><strong>Serial number</strong>:&ensp;001112104</p>
            <p><strong>Registration number</strong>:&ensp;UMA 902PC</p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
    <div class="card">
        <div class="card-header"><h5 class="card-title">Rear plate</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;PN-PBL-M-UMA-SQR-BWW</p>
            <p><strong>Serial number</strong>:&ensp;001112105</p>
            <p><strong>Registration number</strong>:&ensp;UMA 902PC</p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
    <div class="card">
        <div class="card-header"><h5 class="card-title">GPS tracker</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;GPS</p>
            <p><strong>Serial number</strong>:&ensp;8BAE470761A8</p>
            <p><strong>MAC address</strong>:&ensp;<i class="text-danger fas fa-minus"></i></p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
    <div class="card">
        <div class="card-header"><h5 class="card-title">SIM Card</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;SIM chip</p>
            <p><strong>Serial number</strong>:&ensp;892561000177052285</p>
            <p><strong>MAC address</strong>:&ensp;0391385039</p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
    <div class="card">
        <div class="card-header"><h5 class="card-title">Front tracker</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;BLE</p>
            <p><strong>Serial number</strong>:&ensp;8ADC470F2280</p>
            <p><strong>MAC address</strong>:&ensp;<i class="text-danger fas fa-minus"></i></p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
    <div class="card">
        <div class="card-header"><h5 class="card-title">Rear tracker</h5></div>
        <div class="card-body">
            <p><strong>Article name</strong>:&ensp;BLE</p>
            <p><strong>Serial number</strong>:&ensp;8ADC470F1477</p>
            <p><strong>MAC address</strong>:&ensp;<i class="text-danger fas fa-minus"></i></p>
            <p><strong>Current warehouse</strong>:&ensp;AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)</p>
        </div>
    </div>
</form>
</body>
</html>
"""


class InstallationKitsTestCase(TestCase):
    def setUp(self):
        self.client = get_web_client()

    def test_parse_installation_kits_html(self):
        parsed = self.client.parse_installation_kits_html(SAMPLE_KITS_HTML)
        self.assertEqual(parsed["count"], 2)
        self.assertTrue(parsed["has_next_page"])
        self.assertIn("Showing 1-20 of 1,200 items.", parsed["summary"])

        kit1 = parsed["kits"][0]
        self.assertEqual(kit1["kit_code"], "IK-UMA300PW")
        self.assertEqual(kit1["registration_number"], "UMA 300PW")
        self.assertEqual(kit1["front_plate"], "001198122")
        self.assertEqual(kit1["rear_plate"], "001198123")
        self.assertEqual(kit1["front_tracker"], "8ADC470FE5F7")
        self.assertEqual(kit1["rear_tracker"], "8ADC470FE4BB")
        self.assertEqual(kit1["gps_tracker"], "8BAE4707B340")
        self.assertIn("SPIRO", kit1["warehouse"])
        self.assertEqual(kit1["status"], "New")
        self.assertEqual(kit1["created_date"], "24.09.2026")
        self.assertEqual(kit1["kit_uuid"], "87a4c9b7-6974-445b-9dcb-4435b510c655")

    def test_parse_installation_kit_detail_html(self):
        detail = self.client.parse_installation_kit_detail_html(SAMPLE_DETAIL_HTML)
        self.assertEqual(detail["registration_number"], "UMA 902PC")
        self.assertEqual(detail["status"], "New")
        self.assertEqual(detail["created_by_user"], "JULIET NGUNA")

        # Front plate
        self.assertEqual(detail["front_plate"]["serial_number"], "001112104")
        self.assertEqual(detail["front_plate"]["article_name"], "PN-PBL-M-UMA-SQR-BWW")

        # Rear plate
        self.assertEqual(detail["rear_plate"]["serial_number"], "001112105")

        # GPS Tracker
        self.assertEqual(detail["gps_tracker"]["serial_number"], "8BAE470761A8")
        self.assertEqual(detail["gps_tracker"]["mac_address"], "")

        # SIM Card
        self.assertEqual(detail["sim_card"]["serial_number"], "892561000177052285")
        self.assertEqual(detail["sim_card"]["mac_address"], "0391385039")

        # BLE Beacons
        self.assertEqual(detail["front_tracker"]["serial_number"], "8ADC470F2280")
        self.assertEqual(detail["rear_tracker"]["serial_number"], "8ADC470F1477")

    def test_sync_kits_to_local_db(self):
        parsed = self.client.parse_installation_kits_html(SAMPLE_KITS_HTML)
        res = self.client.sync_kits_to_local_db(parsed["kits"])
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 2)

        # Verify DB records
        kit = InstallationKit.objects.get(kit_code="IK-UMA300PW")
        self.assertEqual(kit.registration_number, "UMA 300PW")
        self.assertEqual(kit.front_plate, "001198122")
        self.assertEqual(kit.rear_plate, "001198123")
        self.assertEqual(kit.gps_tracker, "8BAE4707B340")

        # Syncing again updates rather than duplicates
        res2 = self.client.sync_kits_to_local_db(parsed["kits"])
        self.assertEqual(res2["created"], 0)
        self.assertEqual(res2["updated"], 2)
        self.assertEqual(InstallationKit.objects.count(), 2)

    def test_sync_kit_detail_to_local_db(self):
        # Create base kit record
        InstallationKit.objects.create(
            kit_code="IK-UMA902PC",
            registration_number="UMA 902PC",
            kit_uuid="d9f3a73b-8a2a-4e43-86a4-65d29bdbae5a",
        )

        detail = self.client.parse_installation_kit_detail_html(SAMPLE_DETAIL_HTML)
        self.client.sync_kit_detail_to_local_db("d9f3a73b-8a2a-4e43-86a4-65d29bdbae5a", detail)

        updated_kit = InstallationKit.objects.get(kit_code="IK-UMA902PC")
        self.assertEqual(updated_kit.created_by_user, "JULIET NGUNA")
        self.assertEqual(updated_kit.front_plate, "001112104")
        self.assertEqual(updated_kit.front_plate_article, "PN-PBL-M-UMA-SQR-BWW")
        self.assertEqual(updated_kit.rear_plate, "001112105")
        self.assertEqual(updated_kit.sim_serial, "892561000177052285")
        self.assertEqual(updated_kit.sim_mac, "0391385039")
        self.assertEqual(updated_kit.front_tracker, "8ADC470F2280")
        self.assertEqual(updated_kit.rear_tracker, "8ADC470F1477")

    def test_search_param_parsing(self):
        # Test full ITMS search URL parsing
        search_url = "https://stock.itms.ug/installation-kits/index?page=19&InstallationKitSearchFieldSearch%5Bsearch%5D=235pv"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_KITS_HTML
        mock_resp.url = search_url

        with patch.object(self.client, "_request_with_retry", return_value=mock_resp) as mock_req:
            with patch.object(self.client.session_store.session, "is_cookie_valid", return_value=True):
                res = self.client.fetch_installation_kits(page=1, search_params=search_url)
                self.assertTrue(res["success"])
                # Check that page 19 and search=235pv were passed in GET params
                mock_req.assert_called_once()
                call_args = mock_req.call_args
                params = call_args[1].get("params", {})
                self.assertEqual(params.get("InstallationKitSearchFieldSearch[search]"), "235pv")
                self.assertEqual(params.get("page"), "19")

    def test_inspector_pane_show_itms_kit(self):
        pane = InspectorPane()
        kit_data = {
            "kit_code": "IK-UMA300PW",
            "registration_number": "UMA 300PW",
            "front_plate": "001198122",
            "rear_plate": "001198123",
            "gps_tracker": "8BAE4707B340",
            "status": "New",
            "created_date": "24.09.2026",
            "warehouse": "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED",
        }
        # Calling show_itms_kit must format without throwing an exception
        pane.show_itms_kit(kit_data)
        # Calling with None
        pane.show_itms_kit(None)
