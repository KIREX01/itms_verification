"""
Roles as Django Groups.

  role_capabilities()            -> {role: frozenset(capabilities)} read from the role groups' permissions
  sync_user_groups(user)         -> puts the user in the groups matching their roles / admin / suspended state
  set_role_capabilities(role, caps, actor) -> edit what a role may do (audited, synced to installs)

Groups decide WHAT a role may do; BondMembership decides WHERE (at which bond) a person holds it.
Only capability permissions count: other Django permissions on a role group are ignored.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Iterable

from django.core.cache import cache

from governance.capabilities import (
    ALL_CAPABILITIES, CODENAME_TO_CAPABILITY, PLATFORM_ADMIN_GROUP, ROLE_CAPABILITIES, ROLE_GROUP_NAMES,
    SUSPENDED_GROUP, capability_codename,
)

CACHE_KEY = "close:governance:role_capabilities:v1"
CACHE_SECONDS = 300


def invalidate() -> None:
    cache.delete(CACHE_KEY)


def role_capabilities() -> Dict[str, FrozenSet[str]]:
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    from django.contrib.auth.models import Group

    result: Dict[str, FrozenSet[str]] = {}
    try:
        groups = {g.name: g for g in Group.objects.filter(name__in=ROLE_GROUP_NAMES.values())
                  .prefetch_related("permissions__content_type")}
    except Exception:
        groups = {}
    for role, group_name in ROLE_GROUP_NAMES.items():
        group = groups.get(group_name)
        if group is None:
            result[role] = ROLE_CAPABILITIES.get(role, frozenset())  # group missing: built-in default
            continue
        caps = {CODENAME_TO_CAPABILITY[p.codename] for p in group.permissions.all()
                if p.content_type.app_label == "governance" and p.codename in CODENAME_TO_CAPABILITY}
        result[role] = frozenset(caps)
    cache.set(CACHE_KEY, result, CACHE_SECONDS)
    return result


def _capability_permissions(capabilities: Iterable[str]):
    from django.contrib.auth.models import Permission

    codenames = [capability_codename(c) for c in capabilities]
    return Permission.objects.filter(content_type__app_label="governance", codename__in=codenames)


def ensure_groups() -> None:
    """Creates any missing managed group (seeded with the built-in defaults)."""
    from django.contrib.auth.models import Group

    for role, name in ROLE_GROUP_NAMES.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            group.permissions.add(*_capability_permissions(ROLE_CAPABILITIES[role]))
    admin, created = Group.objects.get_or_create(name=PLATFORM_ADMIN_GROUP)
    if created:
        admin.permissions.add(*_capability_permissions(ALL_CAPABILITIES))
    Group.objects.get_or_create(name=SUSPENDED_GROUP)


def sync_user_groups(user) -> None:
    from django.contrib.auth.models import Group
    from governance.models import AccountState, BondMembership

    if not getattr(user, "pk", None):
        return
    managed = {g.name: g for g in Group.objects.filter(
        name__in=list(ROLE_GROUP_NAMES.values()) + [PLATFORM_ADMIN_GROUP, SUSPENDED_GROUP])}
    if len(managed) < len(ROLE_GROUP_NAMES) + 2:
        ensure_groups()
        managed = {g.name: g for g in Group.objects.filter(
            name__in=list(ROLE_GROUP_NAMES.values()) + [PLATFORM_ADMIN_GROUP, SUSPENDED_GROUP])}

    status = AccountState.objects.filter(user_id=user.pk).values_list("status", flat=True).first() or "ACTIVE"
    wanted = set()
    if status != AccountState.Status.ACTIVE or not user.is_active:
        wanted.add(SUSPENDED_GROUP)
    else:
        roles = BondMembership.objects.filter(user_id=user.pk, status=BondMembership.Status.ACTIVE) \
            .values_list("role", flat=True)
        wanted |= {ROLE_GROUP_NAMES[r] for r in roles if r in ROLE_GROUP_NAMES}
        if user.is_superuser:
            wanted.add(PLATFORM_ADMIN_GROUP)

    current = set(user.groups.filter(name__in=managed.keys()).values_list("name", flat=True))
    to_add = [managed[n] for n in wanted - current if n in managed]
    to_remove = [managed[n] for n in current - wanted]
    if to_add:
        user.groups.add(*to_add)
    if to_remove:
        user.groups.remove(*to_remove)


def set_role_capabilities(role: str, capabilities: Iterable[str], actor, request=None) -> FrozenSet[str]:
    """Changes what a role may do. Only capability permissions on the group are touched."""
    from django.contrib.auth.models import Group
    from governance.services import audit, authz

    if not authz.is_platform_admin(actor):
        raise authz.CapabilityDenied("platform.roles")
    if role not in ROLE_GROUP_NAMES:
        raise ValueError("Unknown role.")
    new_caps = frozenset(c for c in capabilities if c in ALL_CAPABILITIES)
    ensure_groups()
    group = Group.objects.get(name=ROLE_GROUP_NAMES[role])
    before = role_capabilities().get(role, frozenset())
    group.permissions.remove(*_capability_permissions(ALL_CAPABILITIES - new_caps))
    group.permissions.add(*_capability_permissions(new_caps))
    invalidate()
    audit.record(actor, "role.permissions_changed", target_type="group", target_id=group.pk,
                 target_label=group.name, before={"capabilities": sorted(before)},
                 after={"capabilities": sorted(new_caps)}, request=request)
    return new_caps


def apply_role_capabilities(mapping: Dict[str, Iterable[str]]) -> None:
    """Edge: make the local role groups match the central server's (from the policy snapshot)."""
    from django.contrib.auth.models import Group

    ensure_groups()
    for role, caps in (mapping or {}).items():
        if role not in ROLE_GROUP_NAMES:
            continue
        caps = frozenset(c for c in caps if c in ALL_CAPABILITIES)
        group = Group.objects.get(name=ROLE_GROUP_NAMES[role])
        group.permissions.remove(*_capability_permissions(ALL_CAPABILITIES - caps))
        group.permissions.add(*_capability_permissions(caps))
    invalidate()
