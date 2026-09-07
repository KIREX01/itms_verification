import hashlib
import shutil
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import EvidenceImage

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
HASH_CHUNK_SIZE = 1024 * 1024


class Command(BaseCommand):
    help = (
        "Evidence Vault ingestion: scans a source directory for photos, computes a "
        "SHA-256 hash of each file, skips exact duplicates already in the vault, and "
        "copies new files into media/vault/YYYY-MM-DD/<uuid>.<ext> for permanent, "
        "forensic-grade storage."
    )

    def add_arguments(self, parser):
        parser.add_argument("source_dir", type=str, help="Directory containing raw photos to ingest.")
        parser.add_argument(
            "--recursive", action="store_true", help="Recurse into subdirectories of source_dir.",
        )

    def handle(self, *args, **options):
        source = Path(options["source_dir"]).expanduser().resolve()
        if not source.is_dir():
            raise CommandError(f"Source directory does not exist: {source}")

        pattern = "**/*" if options["recursive"] else "*"
        candidates = [
            p for p in source.glob(pattern)
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS
        ]

        if not candidates:
            self.stdout.write(self.style.WARNING("No image files found in source directory."))
            return

        vault_root = Path(settings.VAULT_ROOT)
        today_dir = vault_root / date.today().isoformat()
        today_dir.mkdir(parents=True, exist_ok=True)

        ingested, skipped, failed = 0, 0, 0

        for path in candidates:
            try:
                file_hash = self._hash_file(path)
            except OSError as exc:
                self.stderr.write(self.style.ERROR(f"Could not read {path}: {exc}"))
                failed += 1
                continue

            if EvidenceImage.objects.filter(file_hash=file_hash).exists():
                self.stdout.write(f"DUPLICATE_SKIPPED  {path.name}")
                skipped += 1
                continue

            import uuid

            vault_filename = f"{uuid.uuid4()}{path.suffix.lower()}"
            vault_abs_path = today_dir / vault_filename
            shutil.copy2(path, vault_abs_path)

            vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT))

            EvidenceImage.objects.create(
                file_hash=file_hash,
                original_source_path=str(path),
                vault_file=vault_relative,
                file_size_bytes=path.stat().st_size,
                status=EvidenceImage.Status.NEW,
            )
            self.stdout.write(self.style.SUCCESS(f"INGESTED  {path.name} -> {vault_relative}"))
            ingested += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Ingest complete: {ingested} ingested, {skipped} duplicates skipped, {failed} failed."
            )
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()
