"""
Management command to audit photographic evidence integrity in the vault.
Re-computes SHA-256 digests for all vaulted photos and verifies them against
their immutable database records to detect tampering, corruption, or missing files.
"""
import sys
from django.core.management.base import BaseCommand
from core.models import EvidenceImage
from core.services import vault_service


class Command(BaseCommand):
    help = "Verifies SHA-256 cryptographic hashes for all evidence photos in the vault."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-id",
            type=str,
            default=None,
            help="Optional batch_id to audit a specific ingestion batch.",
        )
        parser.add_argument(
            "--repair-thumbnails",
            action="store_true",
            help="Generate any missing WebP thumbnails during audit.",
        )
        parser.add_argument(
            "--repair-hashes",
            action="store_true",
            help="Synchronize database file_hash with actual vault files on disk.",
        )

    def handle(self, *args, **options):
        batch_id = options.get("batch_id")
        repair_thumbs = options.get("repair_thumbnails", False)
        repair_hashes = options.get("repair_hashes", False)

        qs = EvidenceImage.objects.all().order_by("ingested_at")
        if batch_id:
            qs = qs.filter(batch__batch_id=batch_id)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No evidence images found to audit."))
            return

        self.stdout.write(f"Auditing SHA-256 integrity for {total} evidence photo(s)...")

        intact_count = 0
        tampered_count = 0
        missing_count = 0
        thumbs_generated = 0
        hashes_repaired = 0

        for img in qs:
            path = vault_service.resolve_vault_path(img.vault_file)
            if not path.is_file():
                missing_count += 1
                self.stdout.write(
                    self.style.ERROR(f"MISSING: Photo [{img.id.hex[:8]}] at {img.vault_file} not found on disk.")
                )
                continue

            try:
                computed_hash = vault_service.hash_file_path(path)
                if computed_hash == img.file_hash:
                    intact_count += 1
                    if repair_thumbs and (not img.thumbnail_file or not img.preview_file):
                        thumb_rel, prev_rel = vault_service.ensure_image_thumbnails(img)
                        if thumb_rel or prev_rel:
                            thumbs_generated += 1
                else:
                    if repair_hashes:
                        img.file_hash = computed_hash
                        img.file_size_bytes = path.stat().st_size
                        img.save(update_fields=["file_hash", "file_size_bytes"])
                        hashes_repaired += 1
                        intact_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"REPAIRED: Photo [{img.id.hex[:8]}] hash updated to match vault file ({computed_hash[:16]}...)"
                            )
                        )
                        if repair_thumbs and (not img.thumbnail_file or not img.preview_file):
                            thumb_rel, prev_rel = vault_service.ensure_image_thumbnails(img)
                            if thumb_rel or prev_rel:
                                thumbs_generated += 1
                    else:
                        tampered_count += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f"TAMPER DETECTED: Photo [{img.id.hex[:8]}] hash mismatch! "
                                f"Expected: {img.file_hash[:16]}... Found: {computed_hash[:16]}..."
                            )
                        )
            except Exception as exc:
                tampered_count += 1
                self.stdout.write(
                    self.style.ERROR(f"ERROR reading [{img.id.hex[:8]}]: {exc}")
                )

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("EVIDENCE VAULT INTEGRITY AUDIT REPORT")
        self.stdout.write("=" * 50)
        self.stdout.write(f"Total photos audited: {total}")
        self.stdout.write(self.style.SUCCESS(f"Intact (Verified):     {intact_count}"))
        if missing_count > 0:
            self.stdout.write(self.style.ERROR(f"Missing from disk:     {missing_count}"))
        if tampered_count > 0:
            self.stdout.write(self.style.ERROR(f"Tampered / Corrupted:  {tampered_count}"))
        if repair_hashes and hashes_repaired > 0:
            self.stdout.write(self.style.SUCCESS(f"Hashes repaired:       {hashes_repaired}"))
        if repair_thumbs and thumbs_generated > 0:
            self.stdout.write(f"Thumbnails generated:  {thumbs_generated}")
        self.stdout.write("=" * 50)

        if missing_count > 0 or tampered_count > 0:
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("All evidence photos passed cryptographic verification."))
