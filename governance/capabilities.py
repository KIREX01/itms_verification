"""
Capability registry: the single source of truth for "who may do what" in CLOSE Copilot.

Roles are held per bond (warehouse). A person may hold several roles at several bonds;
their capabilities add up. The platform administrator is NOT a bond role: administrators
work in the backoffice (see governance.services.authz.is_platform_admin).

Every enforcement point (API middleware, services, TUI, mobile) asks
`governance.services.authz.has_capability(user, CAP, bond)`; nothing else decides access.
"""
from __future__ import annotations

from typing import Dict, FrozenSet

from django.db import models


class Role(models.TextChoices):
    HELPER = "HELPER", "Helper"
    QUALITY_INSPECTOR = "QUALITY_INSPECTOR", "Quality Inspector"
    TEAM_LEADER = "TEAM_LEADER", "Team Leader"
    SUPERVISOR = "SUPERVISOR", "Supervisor"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
# Capture & scanning (mobile companion, uploads, kit scans)
CAPTURE_UPLOAD = "capture.upload"
STOCK_SCAN = "stock.scan"                  # dispatch/return/audit/verify scans, scan lookups

# Dashboard & read access
DASHBOARD_VIEW = "dashboard.view"
QUEUE_VIEW = "queue.view"                  # review queue, pairs, batches, evidence
ORDERS_VIEW = "orders.view"                # ITMS orders/kits/lifecycle explorer
STOCK_VIEW = "stock.view"
REPORTS_VIEW = "reports.view"
REPORTS_EXPORT = "reports.export"          # CSV exports, MVR docket, report emails
HISTORY_VIEW = "history.view"

# Review queue actions (Team Leader owns all of these)
QUEUE_EDIT = "queue.edit"                  # edit plate, swap, replace photo, link/unlink order, create pair
QUEUE_APPROVE = "queue.approve"
ITMS_SUBMIT = "itms.submit"
PAIR_DELETE = "pair.delete"
PIPELINE_RUN = "pipeline.run"              # run vision pipeline over batches

# Quality flags
FLAG_RAISE = "flag.raise"
FLAG_RESOLVE = "flag.resolve"

# ITMS & stock operations
ITMS_CONNECT = "itms.connect"              # connect/disconnect, sync orders/kits/bonds, install orders
STOCK_EDIT = "stock.edit"                  # deliveries, transfers, opening/physical counts, ledger, clears

# Team & personal settings
TEAM_INVITE = "team.invite"
SETTINGS_OPERATOR = "settings.operator"    # shift date, theme, dry-run, active bond

ALL_CAPABILITIES: FrozenSet[str] = frozenset({
    CAPTURE_UPLOAD, STOCK_SCAN, DASHBOARD_VIEW, QUEUE_VIEW, ORDERS_VIEW, STOCK_VIEW,
    REPORTS_VIEW, REPORTS_EXPORT, HISTORY_VIEW, QUEUE_EDIT, QUEUE_APPROVE, ITMS_SUBMIT,
    PAIR_DELETE, PIPELINE_RUN, FLAG_RAISE, FLAG_RESOLVE, ITMS_CONNECT, STOCK_EDIT,
    TEAM_INVITE, SETTINGS_OPERATOR,
})

ROLE_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    Role.HELPER: frozenset({
        CAPTURE_UPLOAD, STOCK_SCAN,
    }),
    Role.QUALITY_INSPECTOR: frozenset({
        DASHBOARD_VIEW, QUEUE_VIEW, ORDERS_VIEW, STOCK_VIEW, HISTORY_VIEW,
        FLAG_RAISE, SETTINGS_OPERATOR,
    }),
    Role.TEAM_LEADER: frozenset({
        CAPTURE_UPLOAD, STOCK_SCAN, DASHBOARD_VIEW, QUEUE_VIEW, ORDERS_VIEW, STOCK_VIEW,
        REPORTS_VIEW, REPORTS_EXPORT, HISTORY_VIEW, QUEUE_EDIT, QUEUE_APPROVE, ITMS_SUBMIT,
        PAIR_DELETE, PIPELINE_RUN, FLAG_RAISE, FLAG_RESOLVE, ITMS_CONNECT, STOCK_EDIT,
        TEAM_INVITE, SETTINGS_OPERATOR,
    }),
    Role.SUPERVISOR: frozenset({
        DASHBOARD_VIEW, QUEUE_VIEW, STOCK_VIEW, REPORTS_VIEW, REPORTS_EXPORT, HISTORY_VIEW,
        SETTINGS_OPERATOR,
    }),
}

# Roles that require an attached, verified company email (e.g. @itms-ug.com).
COMPANY_EMAIL_ROLES: FrozenSet[str] = frozenset({
    Role.TEAM_LEADER, Role.QUALITY_INSPECTOR, Role.SUPERVISOR,
})

# Roles a Team Leader may invite to their own bond (the administrator approves).
TL_INVITABLE_ROLES: FrozenSet[str] = frozenset({Role.HELPER, Role.QUALITY_INSPECTOR})

DEFAULT_COMPANY_DOMAINS = ("itms-ug.com",)


def capabilities_for_roles(roles) -> FrozenSet[str]:
    caps = set()
    for role in roles:
        caps |= ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(caps)


# ---------------------------------------------------------------------------
# Django Groups & Permissions
# ---------------------------------------------------------------------------
# Each capability is a Django Permission on governance.BondMembership (codename = dots -> underscores),
# each role is a Django Group holding its permissions. The groups' permissions are editable in the
# backoffice; ROLE_CAPABILITIES above is only the starting point (and the fallback).
CAPABILITY_LABELS = {
    CAPTURE_UPLOAD: "Capture photos & upload batches",
    STOCK_SCAN: "Scan kits (dispatch, return, audit)",
    DASHBOARD_VIEW: "See the dashboard",
    QUEUE_VIEW: "See the review queue",
    ORDERS_VIEW: "See ITMS orders & kits",
    STOCK_VIEW: "See stock & ledger",
    REPORTS_VIEW: "See reports",
    REPORTS_EXPORT: "Export reports (CSV, MVR docket, email)",
    HISTORY_VIEW: "See history & audit trail",
    QUEUE_EDIT: "Edit pairs (plate, swap, photos, link orders)",
    QUEUE_APPROVE: "Approve pairs",
    ITMS_SUBMIT: "Submit to ITMS",
    PAIR_DELETE: "Delete pairs",
    PIPELINE_RUN: "Run the vision pipeline",
    FLAG_RAISE: "Raise quality flags",
    FLAG_RESOLVE: "Resolve quality flags",
    ITMS_CONNECT: "Connect / sync the ITMS account",
    STOCK_EDIT: "Edit stock & ledger",
    TEAM_INVITE: "Invite team members",
    SETTINGS_OPERATOR: "Change own operator settings",
}

PLATFORM_ADMIN_GROUP = "Platform Administrator"
SUSPENDED_GROUP = "Suspended"
ROLE_GROUP_NAMES = {
    Role.HELPER: "Helper",
    Role.QUALITY_INSPECTOR: "Quality Inspector",
    Role.TEAM_LEADER: "Team Leader",
    Role.SUPERVISOR: "Supervisor",
}
MANAGED_GROUP_NAMES = frozenset(ROLE_GROUP_NAMES.values()) | {PLATFORM_ADMIN_GROUP, SUSPENDED_GROUP}


def capability_codename(capability: str) -> str:
    return capability.replace(".", "_")


CODENAME_TO_CAPABILITY = {capability_codename(c): c for c in ALL_CAPABILITIES}

