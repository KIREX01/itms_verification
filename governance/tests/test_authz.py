"""Role/capability model, identity rules and route enforcement (enforcement ON)."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from governance import capabilities as C
from governance import enforcement
from governance.capabilities import ROLE_CAPABILITIES, Role
from governance.models import AccountState, BondMembership, CompanyEmailLink, ItmsWarehouseAccess
from governance.services import authz, identity


def make_member(username, role, bond="AGM", company_email=None, grace=False):
    user = User.objects.create_user(username, f"{username}@gmail.com", "Str0ng-pass!")
    state = AccountState.objects.get(user=user)
    state.company_email_grace_until = timezone.now() + timedelta(days=3) if grace else None
    state.save()
    BondMembership.objects.create(user=user, bond_code=bond, role=role)
    if company_email:
        CompanyEmailLink.objects.create(user=user, email=company_email, status="VERIFIED",
                                        verified_via="ADMIN_GRANT", verified_at=timezone.now())
    return user


@override_settings(GOVERNANCE_ENFORCE=True)
class CapabilityMatrixTests(TestCase):
    EXPECTED = {
        Role.HELPER: {C.CAPTURE_UPLOAD, C.STOCK_SCAN},
        Role.QUALITY_INSPECTOR: {C.QUEUE_VIEW, C.FLAG_RAISE, C.STOCK_VIEW},
        Role.TEAM_LEADER: {C.QUEUE_APPROVE, C.ITMS_SUBMIT, C.FLAG_RESOLVE, C.STOCK_EDIT, C.TEAM_INVITE},
        Role.SUPERVISOR: {C.REPORTS_VIEW, C.REPORTS_EXPORT, C.STOCK_VIEW},
    }
    FORBIDDEN = {
        Role.HELPER: {C.QUEUE_VIEW, C.REPORTS_VIEW, C.STOCK_EDIT, C.ITMS_SUBMIT},
        Role.QUALITY_INSPECTOR: {C.QUEUE_APPROVE, C.ITMS_SUBMIT, C.QUEUE_EDIT, C.FLAG_RESOLVE, C.STOCK_EDIT},
        Role.SUPERVISOR: {C.QUEUE_EDIT, C.QUEUE_APPROVE, C.ITMS_SUBMIT, C.STOCK_EDIT, C.FLAG_RAISE},
        Role.TEAM_LEADER: set(),
    }

    def test_role_capabilities_match_agreed_matrix(self):
        for role, caps in self.EXPECTED.items():
            self.assertTrue(caps <= ROLE_CAPABILITIES[role], role)
        for role, caps in self.FORBIDDEN.items():
            self.assertFalse(caps & ROLE_CAPABILITIES[role], role)

    def test_capabilities_are_scoped_to_the_bond(self):
        tl = make_member("tl_agm", Role.TEAM_LEADER, "AGM", company_email="tl.agm@itms-ug.com")
        self.assertTrue(authz.has_capability(tl, C.ITMS_SUBMIT, "AGM"))
        self.assertFalse(authz.has_capability(tl, C.ITMS_SUBMIT, "EAGLE"))

    def test_roles_add_up_across_bonds(self):
        user = make_member("multi", Role.HELPER, "AGM")
        CompanyEmailLink.objects.create(user=user, email="multi@itms-ug.com", status="VERIFIED")
        BondMembership.objects.create(user=user, bond_code="EAGLE", role=Role.SUPERVISOR)
        self.assertEqual(authz.bonds_for(user, C.CAPTURE_UPLOAD), {"AGM"})
        self.assertEqual(authz.bonds_for(user, C.REPORTS_VIEW), {"EAGLE"})

    def test_suspended_or_burned_account_has_nothing(self):
        tl = make_member("tl_susp", Role.TEAM_LEADER, company_email="tl.susp@itms-ug.com")
        AccountState.objects.filter(user=tl).update(status=AccountState.Status.SUSPENDED)
        self.assertFalse(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))

    def test_no_membership_means_no_capability(self):
        user = User.objects.create_user("nobody", "n@example.com", "Str0ng-pass!")
        self.assertFalse(authz.has_capability(user, C.QUEUE_VIEW))
        self.assertFalse(authz.has_capability(user, authz.ANY_MEMBER))

    def test_platform_admin_passes(self):
        admin = User.objects.create_superuser("root", "root@example.com", "Str0ng-pass!")
        self.assertTrue(authz.has_capability(admin, C.ITMS_SUBMIT, "ANY"))


@override_settings(GOVERNANCE_ENFORCE=True)
class IdentityRuleTests(TestCase):
    def test_company_role_requires_verified_company_email(self):
        tl = make_member("tl_noemail", Role.TEAM_LEADER)
        self.assertFalse(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))

    def test_grace_period_keeps_access(self):
        tl = make_member("tl_grace", Role.TEAM_LEADER, grace=True)
        self.assertTrue(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))

    def test_non_company_domain_does_not_count(self):
        qi = make_member("qi_gmail", Role.QUALITY_INSPECTOR, company_email="qi@gmail.com")
        self.assertFalse(authz.has_capability(qi, C.QUEUE_VIEW, "AGM"))

    def test_helper_needs_no_company_email(self):
        helper = make_member("helper1", Role.HELPER)
        self.assertTrue(authz.has_capability(helper, C.CAPTURE_UPLOAD, "AGM"))

    def test_team_leader_bond_must_be_in_itms_access(self):
        tl = make_member("tl_itms", Role.TEAM_LEADER, "AGM", company_email="tl.itms@itms-ug.com")
        self.assertTrue(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))  # ITMS access unknown yet
        identity.record_itms_warehouse_access("tl.itms@itms-ug.com", ["EAGLE"])
        self.assertFalse(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))
        identity.record_itms_warehouse_access("tl.itms@itms-ug.com", ["EAGLE", "AGM"])
        self.assertTrue(authz.has_capability(tl, C.QUEUE_VIEW, "AGM"))

    def test_warehouse_dropped_upstream_is_marked_inactive(self):
        identity.record_itms_warehouse_access("x@itms-ug.com", ["AGM", "EAGLE"])
        identity.record_itms_warehouse_access("x@itms-ug.com", ["AGM"])
        self.assertFalse(ItmsWarehouseAccess.objects.get(company_email="x@itms-ug.com", bond_code="EAGLE").active)

    def test_company_email_is_unique_per_person(self):
        a = User.objects.create_user("a_user", "a@example.com", "Str0ng-pass!")
        b = User.objects.create_user("b_user", "b@example.com", "Str0ng-pass!")
        identity.attach_company_email(a, "Shared@ITMS-UG.com", CompanyEmailLink.VerifiedVia.ADMIN_GRANT)
        with self.assertRaises(ValueError):
            identity.attach_company_email(b, "shared@itms-ug.com", CompanyEmailLink.VerifiedVia.ADMIN_GRANT)

    def test_attach_rejects_wrong_domain(self):
        a = User.objects.create_user("c_user", "c@example.com", "Str0ng-pass!")
        with self.assertRaises(ValueError):
            identity.attach_company_email(a, "c@gmail.com", CompanyEmailLink.VerifiedVia.ADMIN_GRANT)


def _iter_patterns(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _iter_patterns(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p.callback


class RouteTableCoverageTests(TestCase):
    """Every operator route must be declared in the capability table (deny-by-default safety net)."""

    NOT_GOVERNED_PREFIXES = ("admin/", "login/", "logout/", "signup/", "access/", "backoffice/",
                             "favicon.ico", "apple-touch-icon.png", "site.webmanifest", "^static")

    def test_all_operator_views_are_declared(self):
        missing = []
        for route, callback in _iter_patterns(get_resolver().url_patterns):
            if route.startswith(self.NOT_GOVERNED_PREFIXES) or route.startswith("^"):
                continue
            name = getattr(callback, "__name__", "")
            if name not in enforcement.VIEW_CAPABILITIES:
                missing.append(f"{route} -> {name}")
        self.assertEqual(missing, [], "Routes missing from governance.enforcement.VIEW_CAPABILITIES")


@override_settings(GOVERNANCE_ENFORCE=True)
class MiddlewareEnforcementTests(TestCase):
    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_helper_cannot_open_queue_or_reports(self):
        helper = make_member("helper_mw", Role.HELPER)
        c = self._client(helper)
        self.assertEqual(c.get("/api/pairs/").status_code, 403)
        self.assertEqual(c.get("/api/reports/totals/").status_code, 403)
        self.assertEqual(c.get("/api/history/").status_code, 403)

    def test_qi_can_read_queue_but_not_approve_or_submit(self):
        qi = make_member("qi_mw", Role.QUALITY_INSPECTOR, company_email="qi.mw@itms-ug.com")
        c = self._client(qi)
        self.assertEqual(c.get("/api/pairs/").status_code, 200)
        r = c.post("/api/pairs/999/action/", data={"action": "approve"},
                   content_type="application/json")
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/pairs/batch-submit/", data={}, content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_supervisor_reads_reports_but_cannot_edit_stock(self):
        sup = make_member("sup_mw", Role.SUPERVISOR, company_email="sup.mw@itms-ug.com")
        c = self._client(sup)
        self.assertEqual(c.get("/api/reports/totals/").status_code, 200)
        self.assertEqual(c.post("/api/stock/ledger/entry/", data={}).status_code, 403)

    def test_team_leader_pair_actions_pass_governance(self):
        tl = make_member("tl_mw", Role.TEAM_LEADER, company_email="tl.mw@itms-ug.com")
        c = self._client(tl)
        r = c.post("/api/pairs/999/action/", data={"action": "approve"}, content_type="application/json")
        self.assertNotEqual(r.status_code, 403)  # 404 for the missing pair, not a governance denial

    def test_user_without_role_sees_no_access_page(self):
        user = User.objects.create_user("norole", "norole@example.com", "Str0ng-pass!")
        r = self._client(user).get("/")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "will assign your role", status_code=403)

    def test_suspended_account_is_signed_out_on_next_request(self):
        tl = make_member("tl_kick", Role.TEAM_LEADER, company_email="tl.kick@itms-ug.com")
        c = self._client(tl)
        self.assertEqual(c.get("/api/pairs/").status_code, 200)
        AccountState.objects.filter(user=tl).update(status=AccountState.Status.SUSPENDED)
        r = c.get("/api/pairs/")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(c.get("/api/pairs/").status_code, 401)  # session is gone

    def test_capabilities_endpoint(self):
        qi = make_member("qi_caps", Role.QUALITY_INSPECTOR, company_email="qi.caps@itms-ug.com")
        data = self._client(qi).get("/api/me/capabilities/").json()
        self.assertTrue(data["success"])
        self.assertIn(C.FLAG_RAISE, data["capabilities"])
        self.assertNotIn(C.ITMS_SUBMIT, data["capabilities"])
        self.assertEqual(data["bonds"], {"AGM": [Role.QUALITY_INSPECTOR]})

    @override_settings(DEPLOYMENT_ROLE="central")
    def test_admin_goes_to_backoffice_on_central(self):
        admin = User.objects.create_superuser("central_admin", "ca@example.com", "Str0ng-pass!")
        r = self._client(admin).get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/backoffice/")


class DataMigrationTests(TestCase):
    def test_existing_accounts_become_team_leaders_with_grace(self):
        from django.apps import apps
        from importlib import import_module

        from core.models import OperatorITMSSession, OperatorProfile

        mig = import_module("governance.migrations.0002_migrate_existing_accounts")
        active = User.objects.create_user("legacy_tl", "legacy@gmail.com", "Str0ng-pass!")
        active.last_login = timezone.now()
        active.save()
        OperatorProfile.objects.create(user=active, active_facility="EAGLE", allowed_facilities=["EAGLE"])
        OperatorITMSSession.objects.create(user=active, itms_email="Legacy.TL@itms-ug.com")
        pending = User.objects.create_user("pending_x", "p@example.com", "Str0ng-pass!", is_active=False)
        BondMembership.objects.all().delete()
        CompanyEmailLink.objects.all().delete()

        mig.forwards(apps, None)

        m = BondMembership.objects.get(user=active)
        self.assertEqual((m.bond_code, m.role), ("EAGLE", Role.TEAM_LEADER))
        self.assertEqual(CompanyEmailLink.objects.get(user=active).email, "legacy.tl@itms-ug.com")
        self.assertFalse(BondMembership.objects.filter(user=pending).exists())
        self.assertIsNotNone(AccountState.objects.get(user=active).company_email_grace_until)


@override_settings(GOVERNANCE_ENFORCE=True)
class TeamAndProfileApiTests(TestCase):
    def test_profile_and_team_invite(self):
        from unittest.mock import patch
        from governance.models import AccessRequest
        tl = make_member("tl_team", Role.TEAM_LEADER, company_email="tl.team@itms-ug.com")
        make_member("helper_team", Role.HELPER)
        c = Client()
        c.force_login(tl)
        prof = c.get("/api/me/profile/").json()
        self.assertEqual(prof["company_email"], "tl.team@itms-ug.com")
        team = c.get("/api/team/").json()
        self.assertEqual(team["bond"], "AGM")
        self.assertEqual(len(team["members"]), 2)
        with patch("core.services.email_service.send_access_request_admin_alert", return_value=(True, "")), \
                override_settings(DEPLOYMENT_ROLE="central"):
            r = c.post("/api/team/", data={"full_name": "New Helper", "email": "nh@gmail.com", "role": "HELPER"},
                       content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(AccessRequest.objects.filter(email="nh@gmail.com", source="TEAM_LEADER").exists())

    def test_qi_cannot_use_team_api(self):
        qi = make_member("qi_team", Role.QUALITY_INSPECTOR, company_email="qi.team@itms-ug.com")
        c = Client()
        c.force_login(qi)
        self.assertEqual(c.get("/api/team/").status_code, 403)

    def test_cannot_switch_to_a_bond_without_a_role(self):
        tl = make_member("tl_switch", Role.TEAM_LEADER, company_email="tl.switch@itms-ug.com")
        c = Client()
        c.force_login(tl)
        r = c.post("/api/itms/warehouses/", data={"code": "EAGLE"}, content_type="application/json")
        self.assertEqual(r.status_code, 403)
