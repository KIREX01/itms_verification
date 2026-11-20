"""
Roles become Django Groups holding Django Permissions.

* Creates the capability permissions (governance.<codename>) on BondMembership.
* Creates the groups Helper / Quality Inspector / Team Leader / Supervisor (seeded with the
  original role matrix), Platform Administrator (every capability) and Suspended (none).
* Puts existing users in the groups that match their active roles; superusers in
  Platform Administrator; suspended / burned accounts in Suspended.
"""
from django.db import migrations

CAPS = {
    "capture_upload": "Capture photos & upload batches",
    "stock_scan": "Scan kits (dispatch, return, audit)",
    "dashboard_view": "See the dashboard",
    "queue_view": "See the review queue",
    "orders_view": "See ITMS orders & kits",
    "stock_view": "See stock & ledger",
    "reports_view": "See reports",
    "reports_export": "Export reports (CSV, MVR docket, email)",
    "history_view": "See history & audit trail",
    "queue_edit": "Edit pairs (plate, swap, photos, link orders)",
    "queue_approve": "Approve pairs",
    "itms_submit": "Submit to ITMS",
    "pair_delete": "Delete pairs",
    "pipeline_run": "Run the vision pipeline",
    "flag_raise": "Raise quality flags",
    "flag_resolve": "Resolve quality flags",
    "itms_connect": "Connect / sync the ITMS account",
    "stock_edit": "Edit stock & ledger",
    "team_invite": "Invite team members",
    "settings_operator": "Change own operator settings",
}
# Frozen copy of the role matrix at the time of this migration.
ROLE_GROUPS = {
    "HELPER": ("Helper", ["capture_upload", "stock_scan"]),
    "QUALITY_INSPECTOR": ("Quality Inspector", ["dashboard_view", "queue_view", "orders_view", "stock_view",
                                                "history_view", "flag_raise", "settings_operator"]),
    "TEAM_LEADER": ("Team Leader", list(CAPS)),
    "SUPERVISOR": ("Supervisor", ["dashboard_view", "queue_view", "stock_view", "reports_view", "reports_export",
                                  "history_view", "settings_operator"]),
}


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    BondMembership = apps.get_model("governance", "BondMembership")
    AccountState = apps.get_model("governance", "AccountState")

    ct, _ = ContentType.objects.get_or_create(app_label="governance", model="bondmembership")
    perms = {}
    for codename, name in CAPS.items():
        perm, _ = Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": name})
        perms[codename] = perm

    role_group = {}
    for role, (group_name, codes) in ROLE_GROUPS.items():
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            group.permissions.set([perms[c] for c in codes])
        role_group[role] = group
    admin_group, created = Group.objects.get_or_create(name="Platform Administrator")
    if created:
        admin_group.permissions.set(list(perms.values()))
    suspended_group, _ = Group.objects.get_or_create(name="Suspended")

    states = dict(AccountState.objects.values_list("user_id", "status"))
    for user in User.objects.all():
        groups = set()
        if states.get(user.pk, "ACTIVE") != "ACTIVE":
            groups.add(suspended_group)
        else:
            for role in BondMembership.objects.filter(user=user, status="ACTIVE").values_list("role", flat=True):
                if role in role_group:
                    groups.add(role_group[role])
            if user.is_superuser:
                groups.add(admin_group)
        if groups:
            user.groups.add(*groups)


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["Helper", "Quality Inspector", "Team Leader", "Supervisor",
                                   "Platform Administrator", "Suspended"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("governance", "0008_capability_permissions"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
