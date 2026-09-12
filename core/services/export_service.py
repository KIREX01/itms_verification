"""
Export service for ITMS Verification Copilot.

Generates comprehensive, formatted CSV reports for end-of-shift handover,
audit compliance, and supervisor sign-off. Uses UTF-8 with BOM (utf-8-sig)
for 100% native compatibility with Microsoft Excel on Windows.
"""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from django.conf import settings
from django.utils import timezone

from core.models import VehicleInstallationPair, SubmissionAuditLog

logger = logging.getLogger(__name__)


def export_shift_report(
    queryset=None,
    output_dir: Optional[Path] = None,
    filename_prefix: str = "shift_report",
) -> Dict[str, Any]:
    """
    Exports verification and installation records to a timestamped CSV report.

    Args:
        queryset: Optional queryset of VehicleInstallationPair. Defaults to all pairs.
        output_dir: Destination folder. Defaults to settings.BASE_DIR / 'exports'.
        filename_prefix: Filename prefix.

    Returns:
        Dict with success, file_path, row_count, filename.
    """
    if output_dir is None:
        output_dir = Path(settings.BASE_DIR) / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    file_path = output_dir / f"{filename_prefix}_{timestamp_str}.csv"

    if queryset is None:
        queryset = VehicleInstallationPair.objects.all()

    pairs = (
        queryset.select_related("order", "front_image", "rear_image")
        .prefetch_related("audit_logs")
        .order_by("-updated_at")
    )

    headers = [
        "Record ID",
        "Updated At",
        "Plate (Detected)",
        "Manual Override",
        "Order Number",
        "Order Status",
        "Vehicle VIN / Chassis",
        "Tracker IMEI / ID",
        "Plate Serial",
        "Installation Officer",
        "Installation Warehouse",
        "Verification Status",
        "Match Quality",
        "Pairing Method",
        "Front Photo File",
        "Front Photo SHA256",
        "Rear Photo File",
        "Rear Photo SHA256",
        "Submission Status",
        "ITMS Token / Ref",
        "Submitted At",
    ]

    row_count = 0
    with open(file_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)

        for pair in pairs:
            order = pair.order
            front = pair.front_image
            rear = pair.rear_image

            # Extract latest submission token from audit logs if available
            latest_sub_log = (
                pair.audit_logs.filter(action=SubmissionAuditLog.Action.SUBMIT)
                .order_by("-timestamp")
                .first()
            )
            token = (
                latest_sub_log.simulated_token
                or latest_sub_log.message
                if latest_sub_log
                else "—"
            )

            writer.writerow([
                pair.id,
                pair.updated_at.strftime("%Y-%m-%d %H:%M:%S") if pair.updated_at else "—",
                pair.registration_number_detected or "—",
                "YES" if pair.is_manual_override else "NO",
                order.order_number if order else "—",
                order.status if order else "—",
                order.vin if order else "—",
                (order.gps_tracker_id or order.tracker_id) if order else "—",
                (order.front_plate_serial or order.plate_serial) if order else "—",
                order.installation_officer if order else "—",
                order.warehouse_name if order else "—",
                pair.verification_status,
                pair.match_type,
                getattr(pair, "matched_via", "VISION"),
                Path(front.vault_file).name if front and front.vault_file else "—",
                front.file_hash if front else "—",
                Path(rear.vault_file).name if rear and rear.vault_file else "—",
                rear.file_hash if rear else "—",
                pair.verification_status,
                token,
                pair.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if pair.submitted_at else "—",
            ])
            row_count += 1

    logger.info("Exported %d records to %s", row_count, file_path)
    return {
        "success": True,
        "file_path": str(file_path),
        "filename": file_path.name,
        "row_count": row_count,
    }
