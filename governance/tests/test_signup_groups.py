"""Sign-up from an invite puts the person in the Django group for their granted role."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from governance.models import AccessRequest
from governance.services import onboarding


@override_settings(DEPLOYMENT_ROLE="central")
class SignupGroupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")

    def _signup(self, role, email, company_email=""):
        req = AccessRequest.objects.create(full_name="New Person", email=email, requested_role=role,
                                           bond_code="AGM", company_email=company_email)
        req, token, code = onboarding.grant(req, self.admin)
        username = "user_" + email.split("@")[0]
        return onboarding.redeem_invite(token, code, username, "Str0ng-pass!x", "Str0ng-pass!x")

    def test_each_role_lands_in_its_group(self):
        cases = [
            ("HELPER", "helper@gmail.com", "", "Helper"),
            ("TEAM_LEADER", "tl@gmail.com", "tl@itms-ug.com", "Team Leader"),
            ("QUALITY_INSPECTOR", "qi@itms-ug.com", "qi@itms-ug.com", "Quality Inspector"),
            ("SUPERVISOR", "sup@itms-ug.com", "sup@itms-ug.com", "Supervisor"),
        ]
        for role, email, company, group in cases:
            user = self._signup(role, email, company)
            self.assertEqual(set(user.groups.values_list("name", flat=True)), {group}, role)

    def test_owner_is_platform_administrator(self):
        self.assertEqual(set(self.admin.groups.values_list("name", flat=True)), {"Platform Administrator"})
