from pathlib import Path
from typing import List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import IngestionBatch
from core.services import vault_service

VALID_EXTENSIONS = vault_service.VALID_EXTENSIONS


class Command(BaseCommand):
    help = (
        "Evidence Vault ingestion: scans source directory, dedicated --front-dir, or --rear-dir "
        "for photos, auto-detects classified subfolders (front/ and rear/), groups them into an "
        "IngestionBatch, computes SHA-256 hashes, cryptographically skips duplicates, and copies "
        "new files into media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/<uuid>.<ext> for permanent, "
        "forensic-grade storage with orientation tracking."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "source_dir",
            nargs="?",
            type=str,
            default=None,
            help="Directory containing raw photos to ingest (optional if --front-dir or --rear-dir is provided).",
        )
        parser.add_argument(
            "--front-dir", "-f",
            type=str,
            default=None,
            help="Explicit directory containing FRONT-facing motorcycle photos only.",
        )
        parser.add_argument(
            "--rear-dir", "-r",
            type=str,
            default=None,
            help="Explicit directory containing REAR-facing motorcycle photos only.",
        )
        parser.add_argument(
            "--orientation", "-o",
            type=str,
            choices=["FRONT", "REAR", "front", "rear"],
            default=None,
            help="Explicit orientation override for all photos in source_dir (FRONT or REAR).",
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Recurse into subdirectories of source directories.",
        )
        parser.add_argument(
            "--batch-label",
            type=str,
            default="",
            help="Descriptive label or note for this upload batch (e.g. 'Morning Shift Entebbe').",
        )
        parser.add_argument(
            "--allow-asymmetric",
            action="store_true",
            help="Allow ingestion when front and rear photo counts do not match (suppresses strict warnings).",
        )
        parser.add_argument(
            "--strict-counts",
            action="store_true",
            help="Enforce exact 1:1 front and rear photo count matching, aborting if counts differ.",
        )

    def handle(self, *args, **options):
        if not options["source_dir"] and not options["front_dir"] and not options["rear_dir"]:
            raise CommandError(
                "Please specify a source directory, or provide --front-dir and/or --rear-dir."
            )

        candidates: List[Tuple[Path, Optional[str]]] = []
        seen_paths: set[Path] = set()

        def add_file(p: Path, orient: Optional[str]):
            res_p = p.resolve()
            if res_p not in seen_paths and res_p.is_file() and res_p.suffix.lower() in VALID_EXTENSIONS:
                seen_paths.add(res_p)
                candidates.append((res_p, orient))

        # 1. Process explicit FRONT directory
        if options["front_dir"]:
            front_p = Path(options["front_dir"]).expanduser().resolve()
            if not front_p.is_dir():
                raise CommandError(f"Specified front directory does not exist: {front_p}")
            pattern = "**/*" if options["recursive"] else "*"
            for p in front_p.glob(pattern):
                add_file(p, "FRONT")

        # 2. Process explicit REAR directory
        if options["rear_dir"]:
            rear_p = Path(options["rear_dir"]).expanduser().resolve()
            if not rear_p.is_dir():
                raise CommandError(f"Specified rear directory does not exist: {rear_p}")
            pattern = "**/*" if options["recursive"] else "*"
            for p in rear_p.glob(pattern):
                add_file(p, "REAR")

        # 3. Process general source directory
        if options["source_dir"]:
            src_p = Path(options["source_dir"]).expanduser().resolve()
            if not src_p.is_dir():
                raise CommandError(f"Specified source directory does not exist: {src_p}")

            explicit_orient = (options.get("orientation") or "").upper()
            if explicit_orient:
                pattern = "**/*" if options["recursive"] else "*"
                for p in src_p.glob(pattern):
                    add_file(p, explicit_orient)
            else:
                # Inspect for classified subfolders like 'front/', 'rear/', 'fronts/', 'rears/', 'back/'
                subdirs = [d for d in src_p.iterdir() if d.is_dir()]
                front_subdirs = [
                    d for d in subdirs if any(f in d.name.lower() for f in ("front", "forward", "fronts"))
                ]
                rear_subdirs = [
                    d for d in subdirs if any(r in d.name.lower() for r in ("rear", "back", "rears", "backs"))
                ]

                if front_subdirs or rear_subdirs:
                    f_names = [d.name for d in front_subdirs]
                    r_names = [d.name for d in rear_subdirs]
                    self.stdout.write(
                        self.style.NOTICE(
                            f"Auto-detected classified subfolders in {src_p.name}: "
                            f"Front={f_names}, Rear={r_names}"
                        )
                    )
                    for fd in front_subdirs:
                        for p in fd.rglob("*"):
                            add_file(p, "FRONT")
                    for rd in rear_subdirs:
                        for p in rd.rglob("*"):
                            add_file(p, "REAR")

                    # Also collect any remaining files directly in source_dir or other subdirs
                    pattern = "**/*" if options["recursive"] else "*"
                    for p in src_p.glob(pattern):
                        if p.resolve() not in seen_paths:
                            orient = vault_service.detect_folder_orientation(p) or None
                            add_file(p, orient)
                else:
                    pattern = "**/*" if options["recursive"] else "*"
                    for p in src_p.glob(pattern):
                        orient = vault_service.detect_folder_orientation(p) or None
                        add_file(p, orient)

        if not candidates:
            self.stdout.write(self.style.WARNING("No valid image files found in specified directories."))
            return

        # Derive informative batch label
        if options["batch_label"]:
            label = options["batch_label"]
        elif options["front_dir"] and options["rear_dir"]:
            label = f"{Path(options['front_dir']).name}+{Path(options['rear_dir']).name}"
        elif options["source_dir"]:
            label = Path(options["source_dir"]).name
        else:
            label = Path(options["front_dir"] or options["rear_dir"]).name

        front_candidates = sum(1 for _, o in candidates if o == "FRONT")
        rear_candidates = sum(1 for _, o in candidates if o == "REAR")
        general_candidates = len(candidates) - front_candidates - rear_candidates

        # --- Photo Count Symmetry Validation ---
        if front_candidates > 0 and rear_candidates > 0:
            if front_candidates == rear_candidates:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Symmetric Batch Validated: {front_candidates} FRONT and {rear_candidates} REAR photos detected (1:1 ratio)."
                    )
                )
            else:
                diff = abs(front_candidates - rear_candidates)
                missing_side = "REAR" if front_candidates > rear_candidates else "FRONT"
                warn_msg = (
                    f"⚠️  PHOTO COUNT MISMATCH: {front_candidates} FRONT vs {rear_candidates} REAR "
                    f"({diff} photo(s) missing from {missing_side})."
                )
                if options.get("strict_counts"):
                    raise CommandError(
                        f"{warn_msg}\nAborting because --strict-counts is enabled. Use --allow-asymmetric to override."
                    )
                self.stdout.write(self.style.WARNING(warn_msg))
                self.stdout.write(
                    self.style.NOTICE(
                        f"Notice: Vehicle verification requires equal front and rear counts. "
                        f"{diff} vehicle(s) will be registered as INCOMPLETE pairs."
                    )
                )
        elif front_candidates > 0 and rear_candidates == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️  SINGLE-ORIENTATION BATCH WARNING: Ingesting {front_candidates} FRONT photo(s) only without REAR photos!\n"
                    f"Pair matching requires both orientations. Consider using a unified batch folder with front/ and rear/ subfolders."
                )
            )
        elif rear_candidates > 0 and front_candidates == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️  SINGLE-ORIENTATION BATCH WARNING: Ingesting {rear_candidates} REAR photo(s) only without FRONT photos!\n"
                    f"Pair matching requires both orientations. Consider using a unified batch folder with front/ and rear/ subfolders."
                )
            )

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label=label,
        )

        self.stdout.write(f"Initialized upload batch: {batch.batch_id} (label: '{label}')")
        self.stdout.write(
            f"Scanning {len(candidates)} candidate file(s): "
            f"[{front_candidates} Front, {rear_candidates} Rear, {general_candidates} General/Auto]...\n"
        )

        for path, orient in candidates:
            img, status = vault_service.ingest_from_disk(path, batch=batch, orientation_override=orient)
            orient_tag = f"[{img.orientation}]" if img and img.orientation else (f"[{orient}]" if orient else "[UNKNOWN]")
            if status == "INGESTED":
                self.stdout.write(self.style.SUCCESS(f"INGESTED           {orient_tag:9} {path.name} -> {img.vault_file}"))
            elif status == "DUPLICATE_SKIPPED":
                self.stdout.write(f"DUPLICATE_SKIPPED  {orient_tag:9} {path.name} (already in vault as {img.id})")
            elif status == "READ_ERROR":
                self.stderr.write(self.style.ERROR(f"READ_ERROR         {orient_tag:9} {path.name}: file unreadable"))
            else:
                self.stderr.write(self.style.ERROR(f"FAILED             {orient_tag:9} {path.name} ({status})"))

        batch.refresh_from_db()
        front_count = batch.images.filter(orientation="FRONT").count()
        rear_count = batch.images.filter(orientation="REAR").count()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Batch {batch.batch_id} complete: {batch.ingested_count} ingested "
                f"({front_count} Front, {rear_count} Rear), "
                f"{batch.duplicate_count} duplicates skipped, {batch.failed_count} failed."
            )
        )
        self.stdout.write(
            f"To process vision on this batch: python manage.py process_vision --batch {batch.batch_id}"
        )
