"""
Django management command to check for system updates from GitHub Releases.

Usage:
    python manage.py check_updates          # Check if an update is available
    python manage.py check_updates --force  # Force check bypassing 12-hour cache
    python manage.py check_updates --apply  # Safely download, migrate, and apply update
"""
from django.core.management.base import BaseCommand
from core.services.update_service import update_service
from core.version import __version__


class Command(BaseCommand):
    help = "Checks GitHub Releases for application updates and safely applies them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass local 12-hour cache and query GitHub API directly.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Download, apply migrations, and update the application to the latest release.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        apply_update = options["apply"]

        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("  ITMS VERIFICATION COPILOT - SYSTEM UPDATE MANAGER"))
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(f"  Current Version: v{__version__}")

        if not apply_update:
            self.stdout.write("  Checking GitHub Releases API...")
            info = update_service.check_for_updates(force=force)

            if not info.get("success") and info.get("error"):
                self.stdout.write(self.style.WARNING(f"  Check Notice: {info['error']}"))
                self.stdout.write("  (System can continue running offline without updates)")
                return

            if info.get("update_available"):
                self.stdout.write(self.style.NOTICE(f"\n  [!] A NEW UPDATE IS AVAILABLE: v{info['latest_version']}"))
                self.stdout.write(f"  Release Name: {info.get('release_name', '--')}")
                self.stdout.write(f"  Published:    {info.get('published_at', '--')}")
                self.stdout.write(f"  Release URL:  {info.get('html_url', '--')}")

                if info.get("release_notes"):
                    self.stdout.write("\n  --- Release Notes ---")
                    for line in info["release_notes"].splitlines():
                        self.stdout.write(f"    {line}")
                    self.stdout.write("  " + "-" * 40)

                self.stdout.write(self.style.SUCCESS("\n  To apply this update, run:"))
                self.stdout.write(self.style.SUCCESS("    python manage.py check_updates --apply"))
                self.stdout.write("    Or click 'Update System Now' in the Web Console Settings tab.")
            else:
                self.stdout.write(self.style.SUCCESS(f"\n  [+] System is up-to-date (v{__version__})."))
                if info.get("from_cache"):
                    self.stdout.write("  (Result loaded from local cache. Use --force to check live)")
        else:
            self.stdout.write(self.style.WARNING("  Applying update from GitHub..."))
            outcome = update_service.apply_update()
            if outcome.get("success"):
                self.stdout.write(self.style.SUCCESS(f"\n  [+] {outcome.get('message')}"))
            else:
                self.stdout.write(self.style.ERROR(f"\n  [x] Update failed: {outcome.get('error')}"))

        self.stdout.write(self.style.SUCCESS("=" * 65))
