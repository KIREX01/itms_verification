"""
Bring existing installs under governance without locking anyone out.

* Every user gets an AccountState (ACTIVE) with a 14-day grace period to attach a company email.
* Every active, non-pending account becomes TEAM_LEADER at their current bond
  (OperatorProfile.active_facility → OperatorITMSSession.active_bond_code → "AGM"), which matches
  today's behaviour where any signed-in user can do everything. The administrator re-assigns
  real roles from the backoffice afterwards.
* A company email is pre-attached (verified) from the user's ITMS session email when it is in an
  approved company domain (they already proved ownership by logging into ITMS with it).
"""
from datetime import timedelta

from django.db import migrations
from django.utils import timezone

COMPANY_DOMAINS = ("itms-ug.com",)
GRACE_DAYS = 14


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    AccountState = apps.get_model("governance", "AccountState")
    BondMembership = apps.get_model("governance", "BondMembership")
    CompanyEmailLink = apps.get_model("governance", "CompanyEmailLink")
    OperatorProfile = apps.get_model("core", "OperatorProfile")
    OperatorITMSSession = apps.get_model("core", "OperatorITMSSession")

    now = timezone.now()
    grace_until = now + timedelta(days=GRACE_DAYS)
    used_company_emails = set(CompanyEmailLink.objects.values_list("email", flat=True))

    for user in User.objects.all():
        state, created = AccountState.objects.get_or_create(
            user=user, defaults={"status": "ACTIVE", "company_email_grace_until": grace_until}
        )
        if not created and state.company_email_grace_until is None:
            state.company_email_grace_until = grace_until
            state.save(update_fields=["company_email_grace_until"])

        # Pending signups (deactivated, never signed in) get no role until approved.
        if not user.is_active and user.last_login is None:
            continue

        profile = OperatorProfile.objects.filter(user=user).first()
        itms = OperatorITMSSession.objects.filter(user=user).first()
        bond = ((profile.active_facility if profile else "") or (itms.active_bond_code if itms else "") or "AGM")
        bond = bond.strip().upper() or "AGM"
        BondMembership.objects.get_or_create(
            user=user, bond_code=bond, role="TEAM_LEADER",
            defaults={"status": "ACTIVE", "status_reason": "Migrated from pre-governance access"},
        )

        itms_email = ((itms.itms_email if itms else "") or "").strip().lower()
        if (itms_email and "@" in itms_email and itms_email.rsplit("@", 1)[1] in COMPANY_DOMAINS
                and itms_email not in used_company_emails
                and not CompanyEmailLink.objects.filter(user=user).exists()):
            CompanyEmailLink.objects.create(
                user=user, email=itms_email, status="VERIFIED", verified_via="MIGRATION", verified_at=now,
            )
            used_company_emails.add(itms_email)


def backwards(apps, schema_editor):
    # Governance tables are dropped by reversing 0001; nothing to undo on core data.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("governance", "0001_initial"),
        ("core", "0030_dailystockledger_override_opening_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
