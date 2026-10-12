"""
Unit tests for Production API Response Protocol (OHAS Protocol) and
Security Request Envelope Zero-Trust Gating.
"""
from datetime import timedelta
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import OperatorITMSSession
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
        # Create active ITMS session for active_user
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
        """Authenticated caller with no ITMS session receives 412 ITMS_CONNECTION_REQUIRED."""
        self.client.force_login(self.unlinked_user)
        res = self.client.get(reverse("core:api_orders_list"))
        self.assertEqual(res.status_code, 412)
        data = res.json()
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("code"), "ITMS_CONNECTION_REQUIRED")
        self.assertIn("Connect your ITMS account in Tab 2", data.get("error"))

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
        self.assertEqual(err["code"], "ITMS_CONNECTION_REQUIRED")
        self.assertEqual(err["field"], "itms_session")
        self.assertEqual(err["details"]["status"], "UNLINKED")

    def test_unlinked_user_blocked_from_stock_endpoints(self):
        """Unlinked user is blocked from stock monitoring endpoints."""
        self.client.force_login(self.unlinked_user)
        res = self.client.get(reverse("core:api_stock_shift_dates"))
        self.assertEqual(res.status_code, 412)
        data = res.json()
        self.assertEqual(data.get("code"), "ITMS_CONNECTION_REQUIRED")

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
