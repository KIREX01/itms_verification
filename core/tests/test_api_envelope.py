"""
Unit tests for Production API Response Protocol (OHAS Protocol),
Security Request Envelope Zero-Trust Gating, and Facility-Aware Multi-Tenant Scoping.
"""
from datetime import timedelta
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    FacilitySyncMetadata,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    OperatorITMSSession,
    OperatorProfile,
)
from core.services import scoped_order_service
from core.services.api_envelope import (
    HEADER_BARE,
    HEADER_ENVELOPE,
    format_api_response,
    is_envelope_requested,
)
from core.services.security_envelope import (
    build_operator_envelope,
)


class APIResponseEnvelopeTestCase(TestCase):
    """Tests the OHAS standardized envelope protocol and content negotiation."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testoperator",
            password="Password123!",
            email="op@example.com",
        )
        self.client.force_login(self.user)

    def test_is_envelope_requested_helper(self):
        class DummyRequest:
            def __init__(self, accept_header=""):
                self.headers = {"Accept": accept_header}

        self.assertFalse(is_envelope_requested(None))
        self.assertFalse(is_envelope_requested(DummyRequest("")))
        self.assertFalse(is_envelope_requested(DummyRequest("application/json")))
        self.assertFalse(is_envelope_requested(DummyRequest(HEADER_BARE)))
        self.assertTrue(is_envelope_requested(DummyRequest(HEADER_ENVELOPE)))

    def test_default_bare_mode_backward_compatibility(self):
        """Unenveloped bare response is preserved when no Accept header is supplied."""
        res = self.client.get(reverse("core:api_itms_status"))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # In bare mode, payload is returned directly
        self.assertIn("success", data)
        self.assertIn("status", data)
        self.assertNotIn("meta", data)
        self.assertNotIn("errors", data)
        # Trace ID must still be attached as response header
        self.assertTrue(res.has_header("X-Trace-ID"))
        self.assertTrue(res["X-Trace-ID"].startswith("req_"))

    def test_explicit_bare_mode(self):
        """Requesting bare mode via Accept header returns bare response."""
        res = self.client.get(
            reverse("core:api_itms_status"),
            HTTP_ACCEPT=HEADER_BARE,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("success", data)
        self.assertNotIn("meta", data)

    def test_envelope_mode_success(self):
        """Requesting envelope mode wraps success payload into { data, meta, errors }."""
        res = self.client.get(
            reverse("core:api_itms_status"),
            HTTP_ACCEPT=HEADER_ENVELOPE,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/vnd.ohas.envelope+json", res.get("Content-Type", ""))

        payload = res.json()
        self.assertIn("data", payload)
        self.assertIn("meta", payload)
        self.assertIn("errors", payload)
        self.assertEqual(payload["errors"], [])

        # Check meta structure
        meta = payload["meta"]
        self.assertIn("trace_id", meta)
        self.assertIn("timestamp", meta)
        self.assertEqual(meta["system_tier"], "TIER0_HEALTHY")
        self.assertEqual(meta["api_version"], "v1")

        # Check data contains actual endpoint result
        self.assertTrue(payload["data"].get("success"))
        self.assertIn("status", payload["data"])

        # Check X-Trace-ID header matches meta.trace_id
        self.assertEqual(res["X-Trace-ID"], meta["trace_id"])

    def test_custom_trace_id_propagation(self):
        """Provided X-Trace-ID is carried through into response header and meta."""
        custom_id = "req_custom999888777"
        res = self.client.get(
            reverse("core:api_itms_status"),
            HTTP_ACCEPT=HEADER_ENVELOPE,
            HTTP_X_TRACE_ID=custom_id,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["X-Trace-ID"], custom_id)
        payload = res.json()
        self.assertEqual(payload["meta"]["trace_id"], custom_id)


class SecurityEnvelopeGateTestCase(TestCase):
    """Tests zero-trust request gating with OperatorRequestEnvelope."""

    def setUp(self):
        self.client = Client()
        self.unlinked_user = User.objects.create_user(
            username="unlinked_op",
            password="Password123!",
            email="unlinked@example.com",
        )
        self.active_user = User.objects.create_user(
            username="active_op",
            password="Password123!",
            email="active@example.com",
        )
        # Create active ITMS session for active_user at AGM
        OperatorITMSSession.objects.create(
            user=self.active_user,
            itms_email="active.operator@itms-ug.com",
            itms_user_uuid="usr-uuid-active-123",
            cookies={"_identity-frontend": "valid-cookie-token"},
            csrf_token="csrf-token-abc",
            is_authenticated=True,
            expires_at=timezone.now() + timedelta(days=7),
            active_bond_code="AGM",
            active_bond_name="AGM Bonded Warehouse",
        )

        self.expired_user = User.objects.create_user(
            username="expired_op",
            password="Password123!",
            email="expired@example.com",
        )
        # Create expired ITMS session for expired_user
        OperatorITMSSession.objects.create(
            user=self.expired_user,
            itms_email="expired.operator@itms-ug.com",
            itms_user_uuid="usr-uuid-expired-456",
            cookies={"_identity-frontend": "expired-cookie-token"},
            is_authenticated=True,
            expires_at=timezone.now() - timedelta(days=1),
            active_bond_code="AGM",
        )

    def test_anonymous_request_blocked(self):
        """Unauthenticated caller receives 401 with AUTHENTICATION_REQUIRED."""
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("code"), "AUTHENTICATION_REQUIRED")

    def test_unlinked_user_blocked_from_orders_bare_mode(self):
        """Authenticated caller with no ITMS session receives 412 ITMS_ACCOUNT_NOT_LINKED."""
        self.client.force_login(self.unlinked_user)
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 412)
        data = res.json()
        self.assertFalse(data.get("success"))
        self.assertIn(data.get("code"), ("ITMS_ACCOUNT_NOT_LINKED", "ITMS_CONNECTION_REQUIRED"))
        self.assertIn("official ITMS account in Tab 2", data.get("error"))

    def test_unlinked_user_blocked_from_orders_envelope_mode(self):
        """Envelope mode returns structured error with code and details."""
        self.client.force_login(self.unlinked_user)
        res = self.client.get(
            reverse("core:api_orders_list"),
            HTTP_ACCEPT=HEADER_ENVELOPE,
        )
        self.assertEqual(res.status_code, 412)
        payload = res.json()
        self.assertIsNone(payload["data"])
        self.assertIn("meta", payload)
        self.assertEqual(len(payload["errors"]), 1)

        err = payload["errors"][0]
        self.assertIn(err["code"], ("ITMS_ACCOUNT_NOT_LINKED", "ITMS_CONNECTION_REQUIRED"))
        self.assertEqual(err["field"], "itms_session")
        self.assertEqual(err["details"]["status"], "UNLINKED")

    def test_unlinked_user_blocked_from_stock_endpoints(self):
        """Unlinked user is blocked from stock monitoring endpoints."""
        self.client.force_login(self.unlinked_user)
        res = self.client.get(reverse("core:api_stock_shift_dates"))
        self.assertEqual(res.status_code, 412)
        data = res.json()
        self.assertIn(data.get("code"), ("ITMS_ACCOUNT_NOT_LINKED", "ITMS_CONNECTION_REQUIRED"))

    def test_unlinked_user_permitted_on_itms_account_management(self):
        """Unlinked user can query itms status and warehouses to connect account."""
        self.client.force_login(self.unlinked_user)
        res_status = self.client.get(reverse("core:api_itms_status"))
        self.assertEqual(res_status.status_code, 200)

        res_warehouses = self.client.get(reverse("core:api_itms_warehouses"))
        self.assertEqual(res_warehouses.status_code, 200)

    def test_expired_user_blocked_with_session_expired(self):
        """Expired session returns 412 with ITMS_SESSION_EXPIRED."""
        self.client.force_login(self.expired_user)
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 412)
        data = res.json()
        self.assertEqual(data.get("code"), "ITMS_SESSION_EXPIRED")

    def test_active_user_permitted_on_orders_and_stock(self):
        """Active ITMS session unlocks orders and stock endpoints."""
        self.client.force_login(self.active_user)
        res_orders = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res_orders.status_code, 200)
        data_orders = res_orders.json()
        self.assertTrue(data_orders.get("success"))

        res_stock = self.client.get(reverse("core:api_stock_shift_dates"))
        self.assertEqual(res_stock.status_code, 200)
        data_stock = res_stock.json()
        self.assertTrue(data_stock.get("success"))

    def test_active_user_envelope_mode_returns_data(self):
        """Active ITMS session in envelope mode wraps orders list into { data, meta, errors }."""
        self.client.force_login(self.active_user)
        res = self.client.get(
            reverse("core:api_orders_list"),
            HTTP_ACCEPT=HEADER_ENVELOPE,
        )
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertIn("data", payload)
        self.assertIn("meta", payload)
        self.assertEqual(payload["errors"], [])
        self.assertTrue(payload["data"].get("success"))
        self.assertIn("orders", payload["data"])


class FacilityMultiTenantScopingTestCase(TestCase):
    """Tests Facility-Aware Multi-Tenant Scoping, Deduplication, and Transfer Workflows."""

    def setUp(self):
        self.client = Client()

        # Operator 1 at AGM
        self.op1 = User.objects.create_user(
            username="op_agm1",
            password="Password123!",
            email="op1@example.com",
        )
        OperatorITMSSession.objects.create(
            user=self.op1,
            itms_email="op1@itms-ug.com",
            itms_user_uuid="uuid-op1",
            cookies={"_identity-frontend": "cookie-op1"},
            is_authenticated=True,
            expires_at=timezone.now() + timedelta(days=7),
            active_bond_code="AGM",
            active_bond_name="AGM Bond",
        )

        # Operator 2 at AGM
        self.op2 = User.objects.create_user(
            username="op_agm2",
            password="Password123!",
            email="op2@example.com",
        )
        OperatorITMSSession.objects.create(
            user=self.op2,
            itms_email="op2@itms-ug.com",
            itms_user_uuid="uuid-op2",
            cookies={"_identity-frontend": "cookie-op2"},
            is_authenticated=True,
            expires_at=timezone.now() + timedelta(days=7),
            active_bond_code="AGM",
            active_bond_name="AGM Bond",
        )

        # Seed AGM and SPIRO orders
        InstallationOrder.objects.create(
            order_number="ORD-AGM-001",
            registration_number="UMA100AA",
            facility_code="AGM",
        )
        InstallationOrder.objects.create(
            order_number="ORD-SPIRO-001",
            registration_number="UMA200BB",
            facility_code="SPIRO",
        )

        # Seed AGM and SPIRO kits
        InstallationKit.objects.create(
            kit_code="IK-AGM-001",
            registration_number="UMA100AA",
            facility_code="AGM",
        )
        InstallationKit.objects.create(
            kit_code="IK-SPIRO-001",
            registration_number="UMA200BB",
            facility_code="SPIRO",
        )

    def test_orders_scoped_to_operator_facility(self):
        """Operator at AGM only sees orders for facility AGM."""
        self.client.force_login(self.op1)
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        orders = data.get("orders", [])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["order_number"], "ORD-AGM-001")

    def test_kits_scoped_to_operator_facility(self):
        """Operator at AGM only sees kits for facility AGM."""
        self.client.force_login(self.op1)
        res = self.client.get(reverse("core:api_itms_kits"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        kits = data.get("kits", [])
        self.assertEqual(len(kits), 1)
        self.assertEqual(kits[0]["kit_code"], "IK-AGM-001")

    def test_facility_deduplication_reuses_fresh_cache(self):
        """If AGM kits were synced recently by Operator 1, Operator 2 reuses cache without re-scraping."""
        # Operator 1 records sync 10 minutes ago
        scoped_order_service.record_facility_kits_synced(
            facility_code="AGM",
            total_kits=1,
            operator=self.op1,
        )

        # Operator 2 requests sync
        self.client.force_login(self.op2)
        res = self.client.get(reverse("core:api_itms_kits"), {"sync": "true"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("deduplicated"))
        self.assertIn("Facility AGM kits are fresh", data.get("message"))

    def test_transfer_workflow_switches_facility_context(self):
        """Transferring operator to SPIRO immediately switches data scope to SPIRO."""
        self.client.force_login(self.op1)

        # Transfer operator 1 to SPIRO
        scoped_order_service.switch_operator_facility(self.op1, "SPIRO")

        # Now query orders
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        orders = data.get("orders", [])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["order_number"], "ORD-SPIRO-001")

        # Query kits
        res_kits = self.client.get(reverse("core:api_itms_kits"))
        self.assertEqual(res_kits.status_code, 200)
        data_kits = res_kits.json()
        self.assertEqual(data_kits.get("facility_code"), "SPIRO")
        kits = data_kits.get("kits", [])
        self.assertEqual(len(kits), 1)
        self.assertEqual(kits[0]["kit_code"], "IK-SPIRO-001")

    def test_operator_batches_private_isolation(self):
        """Batches created by Operator 1 are not visible in Operator 2's list."""
        batch1 = IngestionBatch.objects.create(
            batch_id="BATCH-OP1-001",
            operator=self.op1,
            facility_code="AGM",
        )
        batch2 = IngestionBatch.objects.create(
            batch_id="BATCH-OP2-001",
            operator=self.op2,
            facility_code="AGM",
        )

        # Op1 sees only batch 1
        self.client.force_login(self.op1)
        res1 = self.client.get(reverse("core:api_batches_list"), {"scope": "ALL"})
        batches1 = res1.json().get("batches", [])
        batch_ids1 = [b["batch_id"] for b in batches1]
        self.assertIn("BATCH-OP1-001", batch_ids1)
        self.assertNotIn("BATCH-OP2-001", batch_ids1)

        # Op2 sees only batch 2
        self.client.force_login(self.op2)
        res2 = self.client.get(reverse("core:api_batches_list"), {"scope": "ALL"})
        batches2 = res2.json().get("batches", [])
        batch_ids2 = [b["batch_id"] for b in batches2]
        self.assertIn("BATCH-OP2-001", batch_ids2)
        self.assertNotIn("BATCH-OP1-001", batch_ids2)
