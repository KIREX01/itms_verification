"""
Background services on this machine.

  itms services status            show every service's state
  itms services actions           list the actions you can run
  itms services run <ACTION>      run one now and wait for the result (e.g. order_sync.run_now)
"""
import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Status and actions for this machine's background services (sync, vision, outbox, housekeeping)."

    def add_arguments(self, parser):
        parser.add_argument("op", choices=["status", "actions", "run"])
        parser.add_argument("action", nargs="?", default="")

    def handle(self, *args, **opts):
        import getpass
        import socket

        from governance.services import audit
        from governance.services.service_control import ACTIONS, SERVICE_LABELS, run_action, status_all

        if opts["op"] == "status":
            status = status_all()
            for key, label in SERVICE_LABELS.items():
                self.stdout.write(f"{label:<28} {json.dumps(status.get(key, {}), default=str)}")
            return
        if opts["op"] == "actions":
            for key, spec in ACTIONS.items():
                self.stdout.write(f"{key:<26} {spec['label']}")
            return
        action = opts["action"]
        if action not in ACTIONS:
            raise CommandError(f"Unknown action {action!r}. See: itms services actions")
        actor = f"cli:{getpass.getuser()}@{socket.gethostname()}"
        run = run_action(action, triggered_by=actor, wait=True)
        run.refresh_from_db()
        audit.record("system", "service.action", target_type="service", target_label=action,
                     after={"target": "cli", "ok": run.ok}, actor_label=actor)
        self.stdout.write((self.style.SUCCESS if run.ok else self.style.ERROR)(f"{action}: {run.summary}"))
