"""Device admission from the command line (server bootstrap / break-glass).

    python manage.py itms_device list                      # devices waiting for approval
    python manage.py itms_device approve 7KQ2-M9XD [--user close_admin]
    python manage.py itms_device block 7KQ2-M9XD --reason "unknown phone"
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from governance.services import device_admission


class Command(BaseCommand):
    help = "List, approve or block devices by the code shown on their 'not authorized' page."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "approve", "block"])
        parser.add_argument("code", nargs="?")
        parser.add_argument("--user", default="", help="Link the approved device to this username")
        parser.add_argument("--reason", default="", help="Reason for blocking")

    def handle(self, *args, **opts):
        from core.models import AuthorizedDevice

        if opts["action"] == "list":
            pending = AuthorizedDevice.objects.filter(status=AuthorizedDevice.Status.PENDING).order_by("-requested_at")
            if not pending:
                self.stdout.write("No devices waiting for approval.")
            for d in pending:
                self.stdout.write(f"{device_admission.device_code(d.device_id)}  {d.request_name or '-':<20} "
                                  f"{d.request_email or '-':<28} {d.os_name} {d.os_version} {d.device_model} "
                                  f"{d.browser}  IP {d.ip_address}  {d.requested_at:%Y-%m-%d %H:%M}"
                                  if d.requested_at else
                                  f"{device_admission.device_code(d.device_id)}  {d.device_name}  IP {d.ip_address}")
            return
        if not opts["code"]:
            raise CommandError("Give the device code shown on the device's screen.")
        dev = device_admission.find_by_code(opts["code"])
        if dev is None:
            raise CommandError(f"No device with code {opts['code']}.")
        if opts["action"] == "approve":
            user = None
            if opts["user"]:
                user = User.objects.filter(username=opts["user"]).first()
                if user is None:
                    raise CommandError(f"No user {opts['user']}.")
            device_admission.approve(dev, "cli", user=user)
            self.stdout.write(self.style.SUCCESS(f"Approved {dev.device_name} ({opts['code']})."))
        else:
            device_admission.block(dev, "cli", reason=opts["reason"] or "Blocked from the command line")
            self.stdout.write(self.style.SUCCESS(f"Blocked {dev.device_name} ({opts['code']})."))
