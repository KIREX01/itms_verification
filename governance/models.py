"""
Governance data model: accounts, bond roles, company identity, feature flags and the
administrative audit trail. Installed on every deployment; edited only through
governance services (the backoffice on the central server).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from governance.capabilities import Role


def _new_stamp() -> str:
    return uuid.uuid4().hex


class AccountState(models.Model):
    """Lifecycle of a person's access. BURNED is terminal and irreversible."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        BURNED = "BURNED", "Burned (permanent ban)"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_state")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    # Rotated on suspend/burn/password reset: invalidates remember-me, QR tokens and trust cookies.
    security_stamp = models.CharField(max_length=64, default=_new_stamp)
    reason = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField(default=timezone.now)
    # Migration grace period for existing users who still need to attach a company email.
    company_email_grace_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Account state"

    def __str__(self) -> str:
        return f"{self.user} [{self.status}]"

    def rotate_stamp(self) -> None:
        self.security_stamp = _new_stamp()


class BondMembership(models.Model):
    """A person's role at one bond (warehouse). Capabilities come from ACTIVE memberships."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        REMOVED = "REMOVED", "Removed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bond_memberships")
    bond_code = models.CharField(max_length=32, db_index=True)
    role = models.CharField(max_length=24, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=255, blank=True, default="")
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "bond_code", "role")
        ordering = ["bond_code", "role"]

    def save(self, *args, **kwargs):
        self.bond_code = (self.bond_code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} · {self.get_role_display()} @ {self.bond_code} [{self.status}]"


class CompanyEmailLink(models.Model):
    """The company (e.g. @itms-ug.com) email attached to a person. One email ↔ one person."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        REVOKED = "REVOKED", "Revoked"

    class VerifiedVia(models.TextChoices):
        ADMIN_GRANT = "ADMIN_GRANT", "Administrator grant"
        SIGNUP_CODE = "SIGNUP_CODE", "Signup code"
        ITMS_LOGIN = "ITMS_LOGIN", "ITMS login"
        MIGRATION = "MIGRATION", "Migrated from ITMS session"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="company_email")
    email = models.CharField(max_length=254, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    verified_via = models.CharField(max_length=16, choices=VerifiedVia.choices, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)
    last_itms_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1] if "@" in self.email else ""

    def __str__(self) -> str:
        return f"{self.email} [{self.status}]"


class ItmsWarehouseAccess(models.Model):
    """Which bonds a company email can reach in the ITMS portal (recorded on every warehouse sync)."""

    company_email = models.CharField(max_length=254, db_index=True)
    bond_code = models.CharField(max_length=32, db_index=True)
    warehouse_uuid = models.CharField(max_length=64, blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("company_email", "bond_code")
        verbose_name = "ITMS warehouse access"
        verbose_name_plural = "ITMS warehouse access"

    def save(self, *args, **kwargs):
        self.company_email = (self.company_email or "").strip().lower()
        self.bond_code = (self.bond_code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.company_email} → {self.bond_code} ({'active' if self.active else 'inactive'})"


class FeatureFlag(models.Model):
    """
    Central policy switches (kill switches, safety modes, identity policy). An empty bond_code
    is the global value; a bond_code row overrides it for that bond.
    """

    key = models.CharField(max_length=100, db_index=True)
    bond_code = models.CharField(max_length=32, blank=True, default="", db_index=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("key", "bond_code")
        ordering = ["key", "bond_code"]

    def __str__(self) -> str:
        scope = self.bond_code or "global"
        return f"{self.key} [{scope}] = {self.value!r}"


class AdminAuditLog(models.Model):
    """Append-only record of every administrative / governance change."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=128, blank=True, default="")
    target_label = models.CharField(max_length=255, blank=True, default="")
    bond_code = models.CharField(max_length=32, blank=True, default="", db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default="")
    ip_address = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk is not None and AdminAuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError("AdminAuditLog entries are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AdminAuditLog entries are append-only and cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action} {self.target_label}"


class UserSessionIndex(models.Model):
    """Maps users to their Django session keys so all sessions can be killed on suspend/burn."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    session_key = models.CharField(max_length=64, unique=True)
    ip_address = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)


class BurnedIdentity(models.Model):
    """Emails (login or company) that may never register, be invited or be attached again."""

    email = models.CharField(max_length=254, unique=True)
    burned_user_id = models.IntegerField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    burned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    burned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "Burned identities"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class AccessRequest(models.Model):
    """
    Onboarding: a person asks for access (no account exists yet) -> the administrator grants or
    rejects -> a single-use invite (6-digit code + link) lets them create their account, which is
    born ACTIVE with the granted role, bond and company email.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        GRANTED = "GRANTED", "Granted (invite sent)"
        REJECTED = "REJECTED", "Rejected"
        USED = "USED", "Account created"
        EXPIRED = "EXPIRED", "Expired / locked"

    class Source(models.TextChoices):
        SELF = "SELF", "Requested by the person"
        TEAM_LEADER = "TEAM_LEADER", "Invited by a Team Leader"
        ADMIN = "ADMIN", "Invited by the administrator"

    full_name = models.CharField(max_length=150)
    email = models.CharField(max_length=254, db_index=True)
    phone = models.CharField(max_length=32, blank=True, default="")
    requested_role = models.CharField(max_length=24, choices=Role.choices)
    bond_code = models.CharField(max_length=32, db_index=True)
    company_email = models.CharField(max_length=254, blank=True, default="")
    message = models.TextField(blank=True, default="")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.SELF)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    granted_role = models.CharField(max_length=24, choices=Role.choices, blank=True, default="")
    granted_bond = models.CharField(max_length=32, blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default="")

    invite_token_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    invite_code_hash = models.CharField(max_length=64, blank=True, default="")
    invite_expires_at = models.DateTimeField(null=True, blank=True)
    invite_attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    client_ip = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        self.company_email = (self.company_email or "").strip().lower()
        self.bond_code = (self.bond_code or "").strip().upper()
        self.granted_bond = (self.granted_bond or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.email} → {self.get_requested_role_display()} @ {self.bond_code} [{self.status}]"


class PolicyState(models.Model):
    """Singleton: monotonically increasing policy version, bumped on every governance change.
    Installs compare it on each check-in to know when they need a fresh snapshot."""

    version = models.BigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls) -> "PolicyState":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class InstallRegistration(models.Model):
    """(Central) A laptop / workstation install that pulls policy from this server."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Waiting for enrollment"
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"

    name = models.CharField(max_length=120)
    install_id = models.CharField(max_length=64, unique=True)
    secret = models.CharField(max_length=128, blank=True, default="")
    enrollment_code_hash = models.CharField(max_length=64, blank=True, default="")
    enrollment_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    locked = models.BooleanField(default=False, help_text="Remote lock: install shows maintenance until unlocked.")
    lock_reason = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.CharField(max_length=64, blank=True, default="")
    app_version = models.CharField(max_length=32, blank=True, default="")
    policy_version_acked = models.BigIntegerField(default=0)
    health = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"
