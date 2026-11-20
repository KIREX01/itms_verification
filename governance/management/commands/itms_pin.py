"""TUI master PIN: itms pin status | set [PIN]  (prompts when PIN is omitted)."""
import getpass

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Show or set the TUI master PIN on this computer."

    def add_arguments(self, parser):
        parser.add_argument("op", choices=["status", "set"])
        parser.add_argument("pin", nargs="?", default="")

    def handle(self, *args, **opts):
        from core.services import config_service

        if opts["op"] == "status":
            self.stdout.write("Master PIN is set." if config_service.is_tui_master_pin_set()
                              else "No master PIN is set. Run: itms pin set")
            return
        pin = opts["pin"] or getpass.getpass("New master PIN (4-12 digits): ")
        if not pin.isdigit() or not 4 <= len(pin) <= 12:
            raise CommandError("The PIN must be 4 to 12 digits.")
        if not opts["pin"] and getpass.getpass("Repeat it: ") != pin:
            raise CommandError("The PINs didn't match.")
        config_service.set_tui_master_pin(pin)
        from governance.services import audit
        audit.record("system", "tui.pin_changed", target_type="system", target_label="tui master pin",
                     actor_label="cli")
        self.stdout.write(self.style.SUCCESS("Master PIN saved."))
