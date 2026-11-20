"""
Fleet management on the central server.

  itms fleet status
  itms fleet token --bond AGM --seats 5 --hours 24 [--label TEXT] [--cidr 41.210.0.0/16]
  itms fleet directive <install name|id> <ACTION | lock | unlock>      (see: itms services actions)
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = "Central fleet: installs, install links (enrollment tokens) and remote directives."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="op", required=True)
        sub.add_parser("status")
        tok = sub.add_parser("token")
        tok.add_argument("--bond", default="")
        tok.add_argument("--seats", type=int, default=5)
        tok.add_argument("--hours", type=int, default=24)
        tok.add_argument("--label", default="")
        tok.add_argument("--cidr", default="")
        d = sub.add_parser("directive")
        d.add_argument("install")
        d.add_argument("action")
        d.add_argument("--reason", default="")

    def handle(self, *args, **opts):
        if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
            raise CommandError("Fleet commands run on the central server (DEPLOYMENT_ROLE=central).")
        getattr(self, f"_{opts['op']}")(opts)

    def _status(self, opts):
        from governance.models import InstallRegistration
        from governance.services import fleet

        policy = fleet.release_policy()
        now = timezone.now()
        self.stdout.write(f"{'Install':<26}{'Bond':<8}{'Version':<10}{'Status':<12}{'Last seen':<14}{'Operator':<16}Host")
        for inst in InstallRegistration.objects.order_by("name"):
            seen = "never"
            if inst.last_seen_at:
                mins = int((now - inst.last_seen_at).total_seconds() // 60)
                seen = "online" if mins < 3 else (f"{mins}m ago" if mins < 120 else f"{mins // 60}h ago")
            status = "LOCKED" if inst.locked else inst.status
            if inst.status == "ACTIVE" and fleet.is_outdated(inst.app_version, policy):
                status = "OUTDATED"
            self.stdout.write(f"{inst.name[:25]:<26}{inst.bond_code or '-':<8}{inst.app_version or '-':<10}"
                              f"{status:<12}{seen:<14}{(inst.health or {}).get('active_operator', '')[:15]:<16}"
                              f"{(inst.device_info or {}).get('hostname', '')}")

    def _token(self, opts):
        from governance.services import fleet

        try:
            token, code = fleet.issue_token("system", bond_code=opts["bond"], label=opts["label"],
                                            max_seats=opts["seats"], hours=opts["hours"],
                                            allowed_ip_cidr=opts["cidr"])
        except fleet.FleetError as exc:
            raise CommandError(str(exc))
        cmds = fleet.install_commands(code, settings.CLOSE_CENTRAL_URL)
        self.stdout.write(self.style.SUCCESS(f"Install link {code}  ({token.max_seats} seats, until "
                                             f"{token.expires_at:%Y-%m-%d %H:%M} UTC). Shown only once."))
        self.stdout.write(f"\nWindows:\n  {cmds['windows']}\n\nLinux / macOS:\n  {cmds['linux']}\n")
        self.stdout.write(f"Already installed:\n  itms enroll {settings.CLOSE_CENTRAL_URL} {code}\n")

    def _directive(self, opts):
        from governance.models import InstallRegistration
        from governance.services import audit, policy_sync

        ref = opts["install"]
        qs = InstallRegistration.objects.filter(Q(name__iexact=ref) | Q(install_id=ref))
        if ref.isdigit():
            qs = qs | InstallRegistration.objects.filter(pk=int(ref))
        insts = list(qs.distinct())
        if len(insts) != 1:
            raise CommandError(f"{len(insts)} installs match {ref!r}; use the install id.")
        inst = insts[0]
        action = opts["action"]
        if action in ("lock", "unlock"):
            inst.locked = action == "lock"
            inst.lock_reason = (opts["reason"] or "Locked by administrator") if inst.locked else ""
            inst.save(update_fields=["locked", "lock_reason"])
            audit.record("system", f"install.{action}", target_type="install", target_id=inst.install_id,
                         target_label=inst.name, reason=opts["reason"], actor_label="cli")
        else:
            try:
                policy_sync.queue_command(inst, action, None)
            except policy_sync.PolicySyncError as exc:
                raise CommandError(f"{exc} See: itms services actions")
        self.stdout.write(self.style.SUCCESS(f"{inst.name}: {action} queued; it applies on the next check-in."))
