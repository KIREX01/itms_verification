"""Roles as Django Groups & Permissions."""
from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from governance import capabilities as C
from governance.capabilities import ROLE_CAPABILITIES, ROLE_GROUP_NAMES, Role
from governance.models import AccountState, BondMembership, CompanyEmailLink
from governance.services import accounts, authz, groups, policy_sync


def _tl(username="tl"):
    user = User.objects.create_user(username, f"{username}@gmail.com", "Str0ng-pass!")
    CompanyEmailLink.objects.create(user=user, email=f"{username}@itms-ug.com", status="VERIFIED")
    BondMembership.objects.create(user=user, bond_code="AGM", role=Role.TEAM_LEADER)
    return user


class GroupSetupTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_groups_exist_with_default_permissions(self):
        for role, name in ROLE_GROUP_NAMES.items():
            group = Group.objects.get(name=name)
            codes = set(group.permissions.values_list("codename", flat=True))
            self.assertEqual(codes, {C.capability_codename(c) for c in ROLE_CAPABILITIES[role]}, name)
        self.assertTrue(Group.objects.filter(name="Platform Administrator").exists())
        self.assertTrue(Group.objects.filter(name="Suspended").exists())

    def test_role_capabilities_read_from_groups(self):
        self.assertEqual(groups.role_capabilities()[Role.HELPER], ROLE_CAPABILITIES[Role.HELPER])


@override_settings(GOVERNANCE_ENFORCE=True)
class GroupMembershipTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_people_join_and_leave_groups_with_roles(self):
        tl = _tl()
        self.assertEqual(set(tl.groups.values_list("name", flat=True)), {"Team Leader"})
        BondMembership.objects.create(user=tl, bond_code="EAGLE", role=Role.SUPERVISOR)
        self.assertEqual(set(tl.groups.values_list("name", flat=True)), {"Team Leader", "Supervisor"})
        BondMembership.objects.filter(user=tl, role=Role.SUPERVISOR).update(status="REMOVED")
        BondMembership.objects.get(user=tl, role=Role.TEAM_LEADER).save()  # any save re-syncs
        groups.sync_user_groups(tl)
        self.assertEqual(set(tl.groups.values_list("name", flat=True)), {"Team Leader"})

    def test_suspend_and_burn_move_people_to_suspended(self):
        admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        self.assertIn("Platform Administrator", admin.groups.values_list("name", flat=True))
        tl = _tl()
        accounts.suspend(tl, "check", actor=admin)
        self.assertEqual(set(tl.groups.values_list("name", flat=True)), {"Suspended"})
        accounts.reinstate(tl, actor=admin)
        self.assertEqual(set(tl.groups.values_list("name", flat=True)), {"Team Leader"})

    def test_editing_a_group_changes_what_the_role_can_do(self):
        admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        tl = _tl()
        self.assertTrue(authz.has_capability(tl, C.ITMS_SUBMIT, "AGM"))
        caps = set(ROLE_CAPABILITIES[Role.TEAM_LEADER]) - {C.ITMS_SUBMIT}
        groups.set_role_capabilities(Role.TEAM_LEADER, caps, admin)
        self.assertFalse(authz.has_capability(tl, C.ITMS_SUBMIT, "AGM"))
        self.assertTrue(authz.has_capability(tl, C.QUEUE_APPROVE, "AGM"))
        groups.set_role_capabilities(Role.TEAM_LEADER, ROLE_CAPABILITIES[Role.TEAM_LEADER], admin)

    def test_group_permission_only_counts_at_the_members_bonds(self):
        tl = _tl()
        self.assertFalse(authz.has_capability(tl, C.ITMS_SUBMIT, "EAGLE"))

    def test_unrelated_django_permissions_are_ignored(self):
        group = Group.objects.get(name="Helper")
        group.permissions.add(Permission.objects.get(codename="delete_user"))
        cache.clear()
        self.assertEqual(groups.role_capabilities()[Role.HELPER], ROLE_CAPABILITIES[Role.HELPER])

    def test_only_admin_can_edit_groups(self):
        tl = _tl()
        with self.assertRaises(authz.CapabilityDenied):
            groups.set_role_capabilities(Role.HELPER, [], tl)


@override_settings(DEPLOYMENT_ROLE="central", GOVERNANCE_ENFORCE=True)
class RolesPageAndSyncTests(TestCase):
    def setUp(self):
        cache.clear()
        from backoffice.tests.test_backoffice import admin_client_with_2fa
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        self.c = admin_client_with_2fa(self.admin)

    def tearDown(self):
        groups.set_role_capabilities(Role.HELPER, ROLE_CAPABILITIES[Role.HELPER], self.admin)

    def test_roles_page_edit_requires_password_and_applies(self):
        self.assertContains(self.c.get("/backoffice/roles/"), "Quality Inspector")
        data = {"role": Role.HELPER, "action": "save", f"cap:{C.CAPTURE_UPLOAD}": "on"}
        self.c.post("/backoffice/roles/", data)  # no password -> refused
        self.assertIn(C.STOCK_SCAN, groups.role_capabilities()[Role.HELPER])
        self.c.post("/backoffice/roles/", {**data, "confirm_password": "Owner-pass-1"})
        self.assertEqual(groups.role_capabilities()[Role.HELPER], frozenset({C.CAPTURE_UPLOAD}))

    def test_snapshot_carries_role_permissions_to_installs(self):
        groups.set_role_capabilities(Role.HELPER, [C.CAPTURE_UPLOAD], self.admin)
        snap = policy_sync.build_snapshot()
        self.assertEqual(snap["role_capabilities"][Role.HELPER], [C.CAPTURE_UPLOAD])
        groups.set_role_capabilities(Role.HELPER, ROLE_CAPABILITIES[Role.HELPER], self.admin)
        policy_sync.apply_snapshot({"users": [], "memberships": [], "company_emails": [], "itms_access": [],
                                    "burned": [], "flags": [], "role_capabilities": snap["role_capabilities"]})
        self.assertEqual(groups.role_capabilities()[Role.HELPER], frozenset({C.CAPTURE_UPLOAD}))
