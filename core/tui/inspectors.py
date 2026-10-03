from __future__ import annotations

import os
from typing import Any, Dict, Optional

from django.conf import settings
from rich.markup import escape as _rich_escape
from textual.widgets import Static

def escape_markup(text: Any) -> str:
    """
    Safely escapes text for Textual/Rich markup rendering.
    Textual's markup parser treats bracketed expressions with colons, signs,
    or uppercase letters (e.g. [Clock Skew Calibrated: ...]) as tags.
    Escaping '[' to r'\\[' guarantees literal rendering with zero MarkupError crashes.
    """
    if text is None:
        return ""
    return str(text).replace(r"\[", "[").replace("[", r"\[")

escape = escape_markup

from core.models import (
    EvidenceImage,
    IngestionBatch,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.vision import normalizer

STATUS_STYLE = {
    VehicleInstallationPair.VerificationStatus.PENDING_REVIEW: "yellow",
    VehicleInstallationPair.VerificationStatus.APPROVED: "bold green",
    VehicleInstallationPair.VerificationStatus.INCOMPLETE: "red",
    VehicleInstallationPair.VerificationStatus.CONFLICT: "bold red",
    VehicleInstallationPair.VerificationStatus.UNREGISTERED: "magenta",
    VehicleInstallationPair.VerificationStatus.SUBMITTED: "bold cyan",
    VehicleInstallationPair.VerificationStatus.FAILED: "bold red",
}

class InspectorPane(Static):
    """Displays rich detail for the currently selected pair or batch."""

    def show_pair(self, pair: VehicleInstallationPair):
        if pair is None:
            self.update("[dim]No pair selected. Use ↑/↓ to browse.[/dim]")
            return

        order_line = (
            f"[green]{escape(pair.order.order_number)}[/green] (expects [b]{escape(pair.order.registration_number)}[/b])"
            if pair.order
            else "[yellow]— unmatched —[/yellow]"
        )
        serial_line = escape(
            (
                pair.order.plate_serial
                or pair.order.front_plate_serial
                or pair.order.rear_plate_serial
                or "—"
            ) if pair.order else "—"
        )
        tracker_line = escape(
            (
                pair.order.tracker_id
                or pair.order.gps_tracker_id
                or "—"
            ) if pair.order else "—"
        )
        front = pair.front_image
        rear = pair.rear_image
        status_color = STATUS_STYLE.get(pair.verification_status, "white")

        from core.services.camera_naming import parse_camera_filename

        front_file = escape(front.vault_file) if front else "[red]MISSING[/red]"
        front_ocr = round(front.ocr_confidence, 3) if front and front.ocr_confidence is not None else "N/A"
        front_orient = front.orientation if front else "—"
        front_conf = f"({round(front.orientation_confidence, 2)})" if front and front.orientation_confidence is not None else ""
        front_cam = f" [cyan]({escape(parse_camera_filename(front.original_source_path or front.vault_file).display_tag)})[/cyan]" if front else ""

        rear_file = escape(rear.vault_file) if rear else "[red]MISSING[/red]"
        rear_ocr = round(rear.ocr_confidence, 3) if rear and rear.ocr_confidence is not None else "N/A"
        rear_orient = rear.orientation if rear else "—"
        rear_conf = f"({round(rear.orientation_confidence, 2)})" if rear and rear.orientation_confidence is not None else ""
        rear_cam = f" [cyan]({escape(parse_camera_filename(rear.original_source_path or rear.vault_file).display_tag)})[/cyan]" if rear else ""

        category_badge = "—"
        latest_audit = pair.audit_logs.filter(message__icontains="Category:").first()
        if latest_audit:
            if "Category: PSV" in latest_audit.message:
                category_badge = "[bold white on dark_blue] PSV [/] [bold cyan]Commercial Boda (White Plates)[/]"
            elif "Category: PMO" in latest_audit.message:
                category_badge = "[bold black on gold1] PMO [/] [bold yellow]Private Motorcycle (Yellow Plates)[/]"
            elif "COLOR_CONFLICT" in latest_audit.message:
                category_badge = "[bold white on red] COLOR CONFLICT [/] [red]Mismatched White/Yellow Plates[/]"

        matched_via_tag = getattr(pair, "matched_via", "VISION")
        override_tag = " [bold cyan](Manual Operator Override)[/bold cyan]" if getattr(pair, "is_manual_override", False) else ""

        account_str = escape(pair.account_email or (pair.order.account_email if pair.order else "") or "Unassigned")

        batch = (
            getattr(front, "batch", None)
            or getattr(rear, "batch", None)
        )
        from django.utils import timezone
        today = timezone.localdate()
        latest_batch = IngestionBatch.objects.order_by("-created_at").first()
        if batch:
            is_latest = (latest_batch and batch.batch_id == latest_batch.batch_id)
            is_today = (batch.created_at.date() == today)
            if is_latest:
                tag = "[bold green]🔥 TODAY (Active Batch)[/bold green]"
            elif is_today:
                tag = "[green]● TODAY (Shift)[/green]"
            else:
                tag = f"[yellow]⏳ PRIOR DAY ({batch.created_at.strftime('%Y-%m-%d')})[/yellow]"
            batch_line = f"{escape(batch.batch_id)} ({escape(batch.source_label or 'Batch')}) — {tag}"
        else:
            batch_line = "[dim]No batch associated[/dim]"

        lines = [
            f"[b cyan]═══ Pair Inspector ═══[/b cyan]",
            f"[b]Detected Plate:[/b] [bold yellow]{escape(pair.registration_number_detected or '—')}[/bold yellow]{override_tag}",
            f"[b]Batch Origin:[/b]   {batch_line}",
            f"[b]ITMS Account:[/b]   [bold green]{account_str}[/bold green]",
            f"[b]Vehicle Class:[/b]  {category_badge}",
            f"[b]Status:[/b]         [{status_color}]{pair.verification_status}[/{status_color}]",
        ]

        if pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED:
            latest_fail = pair.audit_logs.filter(result=SubmissionAuditLog.ResultStatus.FAILURE).first()
            if latest_fail:
                fail_msg = escape(latest_fail.message[:75] + ("..." if len(latest_fail.message) > 75 else ""))
                lines.append(f"[b]Failure Reason:[/b] [bold red]{fail_msg}[/bold red]")

        # Resolve ITMS plate lifecycle state
        lifecycle_lines = []
        det_plate = pair.registration_number_detected
        if det_plate and not det_plate.startswith("PAIR-") and not det_plate.startswith("MISSING-"):
            try:
                from core.services.plate_lifecycle_service import resolve_plate_lifecycle
                res = resolve_plate_lifecycle(det_plate, query_live_if_missing=False)
                badge = f"[{res.badge_style}]  {res.badge_label}  [/{res.badge_style}]"
                lifecycle_lines.append(f"[b]ITMS Lifecycle:[/b] {badge}")
                if res.is_unallocated:
                    lifecycle_lines.append(
                        f"[b]Kit Stock:[/b]     [bold green]{escape(res.kit_code)}[/bold green] [dim]({escape(res.warehouse[:28])})[/dim]  │  "
                        f"Serials: F: [cyan]{escape(res.front_plate_serial or '—')}[/cyan] R: [cyan]{escape(res.rear_plate_serial or '—')}[/cyan] GPS: [cyan]{escape(res.gps_tracker or '—')}[/cyan]"
                    )
                elif res.is_installed:
                    lifecycle_lines.append(
                        f"[b]Archive Order:[/b] [bold cyan]#{escape(res.order_number)}[/bold cyan]  │  "
                        f"Officer: {escape(res.officer or 'Officer')}  │  Date: {escape(res.install_date or 'Recorded')}"
                    )
                elif res.is_allocated:
                    lifecycle_lines.append(
                        f"[b]Active Order:[/b]  [bold yellow]#{escape(res.order_number)}[/bold yellow]  │  "
                        f"Stage: [bold yellow]{escape(res.order_status or 'Active')}[/bold yellow]  │  Wh: {escape(res.warehouse[:22])}"
                    )
            except Exception:
                pass

        lines.extend(lifecycle_lines)

        lines.extend([
            f"[b]Match Quality:[/b]  {escape(str(pair.match_type))} (score: {pair.match_score if pair.match_score is not None else 'N/A'}, via: [cyan]{escape(str(matched_via_tag))}[/cyan])",
            f"[b]Pairing Reason:[/b] [cyan]{escape(pair.operator_note or '—')}[/cyan]",
            f"[b]Matched Order:[/b]  {order_line}",
            f"[b]Plate Serial:[/b]   {serial_line}  │  [b]Tracker ID:[/b] {tracker_line}",
            "",
            "[b underline]Photographic Evidence[/b underline]:",
            f" [b]Front:[/b] {front_file}{front_cam}",
            f"        ocr_conf={front_ocr} orient={front_orient} {front_conf}".rstrip(),
            f" [b]Rear:[/b]  {rear_file}{rear_cam}",
            f"        ocr_conf={rear_ocr} orient={rear_orient} {rear_conf}".rstrip(),
            "",
        ])

        from core.services import config_service
        dev_mode = config_service.is_developer_mode()
        v_action = "[b cyan]V[/b cyan]: Compare Images (Dev)" if dev_mode else "[dim]V: Compare (Dev Mode)[/dim]"

        lines.extend([
            "[b]Quick Actions:[/b]",
            " [b green]A[/b green]: Approve / Retry   [b green]T[/b green]: Type Plate / Match",
            " [b yellow]S[/b yellow]: Swap Front/Rear       [b magenta]L[/b magenta]: Link / Pick Photo",
            f" {v_action}     [b blue]U[/b blue]: Submit to ITMS",
            " [b green]J[/b green]: Joint Re-Scan       [b green]P[/b green]: Batch Vision",
        ])
        self.update("\n".join(lines))

    def show_audit_history(self, pair: VehicleInstallationPair):
        if pair is None:
            self.update("[dim]No historical pair selected.[/dim]")
            return

        logs = list(pair.audit_logs.all().order_by("-timestamp")[:10])
        lines = [
            f"[b cyan]═══ Audit Trail: {escape(pair.registration_number_detected or '—')} ═══[/b cyan]",
            f"[b]Status:[/b] {pair.verification_status}  │  [b]Order:[/b] {escape(pair.order.order_number) if pair.order else '—'}",
            f"[b]Submitted At:[/b] {pair.submitted_at.strftime('%Y-%m-%d %H:%M:%S') if pair.submitted_at else 'Not Submitted'}",
            "",
            "[b underline]Recent Audit Log Entries:[/b underline]",
        ]

        if not logs:
            lines.append("[dim]No audit log entries recorded for this pair.[/dim]")
        else:
            for log in logs:
                result_color = "green" if log.result == "SUCCESS" else "red" if log.result == "FAILURE" else "cyan"
                time_str = log.timestamp.strftime("%H:%M:%S")
                token_str = f" (token: {escape(log.simulated_token[:10])}...)" if log.simulated_token else ""
                lines.append(f"• \\[{time_str}\\] [b]{escape(log.action)}[/b] -> [{result_color}]{escape(log.result)}[/{result_color}]{token_str}")
                if log.message:
                    lines.append(f"  [dim]{escape(log.message)}[/dim]")

        self.update("\n".join(lines))

    def show_batch_info(self, batch: IngestionBatch):
        if batch is None:
            self.update("[dim]No batch selected. Use ↑/↓ to browse batches.[/dim]")
            return

        total_imgs = batch.images.count()
        detected_imgs = batch.images.filter(status=EvidenceImage.Status.PLATE_DETECTED).count()
        review_imgs = batch.images.filter(status=EvidenceImage.Status.NEEDS_REVIEW).count()
        failed_imgs = batch.images.filter(status=EvidenceImage.Status.FAILED).count()
        new_imgs = batch.images.filter(status=EvidenceImage.Status.NEW).count()
        front_imgs = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
        rear_imgs = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()

        lines = [
            f"[b cyan]═══ Batch Overview ═══[/b cyan]",
            f"[b]Batch ID:[/b]        [bold yellow]{escape(batch.batch_id)}[/bold yellow]",
            f"[b]Source Channel:[/b]   {escape(batch.source_type)} ({escape(batch.source_label or 'None')})",
            f"[b]Created At:[/b]       {batch.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"[b]Files Ingested:[/b]   {batch.ingested_count} / {batch.total_files} ({batch.duplicate_count} skipped, {batch.failed_count} failed)",
            "",
            "[b underline]Vision Pipeline Summary[/b underline]:",
            f" • Plates Detected:   [bold green]{detected_imgs}[/bold green] / {total_imgs}",
            f" • Needs Review:      [bold yellow]{review_imgs}[/bold yellow]",
            f" • Processing Failed: [bold red]{failed_imgs}[/bold red]",
            f" • Pending (New):     [dim]{new_imgs}[/dim]",
            "",
            "[b underline]Vehicle Orientation[/b underline]:",
            f" • Front Photos:      [green]{front_imgs}[/green]",
            f" • Rear Photos:       [cyan]{rear_imgs}[/cyan]",
            "",
            "[b underline]Operator Navigation[/b underline]:",
            " [dim]• Press [b]Tab[/b] to focus the Photos table below[/dim]",
            " [dim]• Use [b]↑/↓[/b] to inspect each photo's vision analysis[/dim]",
            " [dim]• Press [b]V[/b] or [b]Enter[/b] to view photo with BBox[/dim]",
            " [dim]• Press [b]P[/b] to run vision pipeline on this batch[/dim]",
        ]
        self.update("\n".join(lines))

    def show_image_vision_analysis(self, image: EvidenceImage):
        if image is None:
            self.update("[dim]No photo selected. Use ↑/↓ in the photos table to browse.[/dim]")
            return

        status_style = {
            EvidenceImage.Status.PLATE_DETECTED: "bold green",
            EvidenceImage.Status.NEEDS_REVIEW: "bold yellow",
            EvidenceImage.Status.FAILED: "bold red",
            EvidenceImage.Status.PROCESSING: "cyan",
            EvidenceImage.Status.NEW: "dim white",
            EvidenceImage.Status.MATCHED: "green",
            EvidenceImage.Status.READY: "bold cyan",
            EvidenceImage.Status.SUBMITTED: "blue",
        }.get(image.status, "white")

        raw_path = image.original_source_path or image.vault_file or "unknown_photo"
        filename = escape(os.path.basename(raw_path) if raw_path else "unknown_photo")
        size_kb = round(image.file_size_bytes / 1024.0, 1) if image.file_size_bytes else 0

        # Ugandan syntax check
        syntax_info = ""
        is_syntax_valid = False
        if image.detected_plate:
            norm = normalizer.normalize_plate(image.detected_plate)
            is_syntax_valid = norm.get("is_valid", False)
            syntax_info = " [green](✓ Valid Uganda Syntax)[/green]" if is_syntax_valid else " [yellow](⚠ Non-standard Syntax)[/yellow]"

        # Orientation badge
        orient_color = "green" if image.orientation == "FRONT" else "cyan" if image.orientation == "REAR" else "yellow"
        orient_conf_str = f"({round(image.orientation_confidence * 100, 1)}%)" if image.orientation_confidence is not None else ""

        # Bounding box details
        if image.bbox and len(image.bbox) == 4:
            try:
                x1, y1, x2, y2 = [int(float(v)) for v in image.bbox]
                w_px, h_px = x2 - x1, y2 - y1
                bbox_str = f"\\[{x1}, {y1}, {x2}, {y2}\\]  ({w_px}×{h_px} px)"
            except Exception:
                bbox_str = escape(str(image.bbox))
        else:
            bbox_str = "[dim]None (No plate candidate localized)[/dim]"

        # Confidences
        ocr_str = f"{round(image.ocr_confidence * 100, 1)}% ({round(image.ocr_confidence, 3)})" if image.ocr_confidence is not None else "[dim]N/A[/dim]"
        det_str = f"{round(image.detector_confidence * 100, 1)}% ({round(image.detector_confidence, 3)})" if image.detector_confidence is not None else "[dim]N/A[/dim]"

        lines = [
            f"[b cyan]═══ Vision Analysis: {filename[:28]} ═══[/b cyan]",
            f"[b]Status:[/b]         [{status_style}]{image.status}[/{status_style}]",
            f"[b]Detected Plate:[/b] [bold yellow]{escape(image.detected_plate or '—')}[/bold yellow]{syntax_info}",
            "",
            "[b underline]Vision Pipeline Breakdown[/b underline]:",
            f" [b]OCR Confidence:[/b]     {ocr_str}",
            f" [b]Detector Conf (YOLO):[/b] {det_str}",
            f" [b]Plate Bounding Box:[/b]  {bbox_str}",
            f" [b]Orientation:[/b]         [{orient_color}]{image.orientation}[/{orient_color}] {orient_conf_str}",
            f" [b]Attempts / Retries:[/b]  {image.retry_count} / {getattr(settings, 'VISION_MAX_RETRIES', 3)}",
            f" [b]Processed At:[/b]        {image.processed_at.strftime('%Y-%m-%d %H:%M:%S') if image.processed_at else 'Not processed yet'}",
            "",
            "[b underline]Diagnostic / Error Notes[/b underline]:",
        ]

        if image.error_message:
            lines.append(f" [yellow]{escape(image.error_message)}[/yellow]")
        elif image.status == EvidenceImage.Status.PLATE_DETECTED:
            if is_syntax_valid:
                lines.append(" [green]Plate recognized and validated against Uganda plate grammar.[/green]")
            else:
                lines.append(" [yellow]Plate detected but syntax does not conform to standard Uganda plate grammar.[/yellow]")
        elif image.status == EvidenceImage.Status.NEW:
            lines.append(" [dim]Image ingested; pending vision processing (press P).[/dim]")
        else:
            lines.append(" [dim]No diagnostic notes recorded.[/dim]")

        from core.services.camera_naming import parse_camera_filename
        c_sig = parse_camera_filename(image.original_source_path or image.vault_file)
        cam_str = f"{c_sig.vendor_convention} [{c_sig.display_tag}]" if c_sig.display_tag != "Standard" else "Standard File"

        lines.extend([
            "",
            "[b underline]File Information[/b underline]:",
            f" [b]Image ID:[/b]   {str(image.id)[:16]}...",
            f" [b]Vault File:[/b] {escape(image.vault_file or '—')}",
            f" [b]Camera Origin:[/b] [cyan]{escape(cam_str)}[/cyan]",
            f" [b]File Size:[/b]  {size_kb} KB" + (" [red](PRUNED)[/red]" if image.is_file_pruned else ""),
            "",
            "[b]Quick Actions:[/b]",
            " Press [b cyan]V[/b cyan] or [b cyan]Enter[/b cyan] to view photo with plate BBox in viewer",
            " Press [b yellow]P[/b yellow] to reprocess this batch",
        ])
        self.update("\n".join(lines))

    def show_itms_order(self, order: Optional[Dict[str, Any]], is_archive: bool = False):
        """Displays rich metadata and photo vault status for a selected ITMS order."""
        if not order:
            self.update(
                "[dim]No ITMS order selected.\n"
                "Use ↑/↓ arrow keys to browse table.\n"
                "Press [b yellow]V[/b yellow] to view side-by-side photos.\n"
                "Press [b yellow]F[/b yellow] to cycle sub-views.[/dim]"
            )
            return

        order_num = escape(str(order.get("order_number", "N/A")))
        plate = escape(str(order.get("registration_number", "N/A")))
        vin = escape(str(order.get("vin", "N/A")))
        status = order.get("order_status") or order.get("status", "N/A")
        reg_status = escape(str(order.get("registration_status", "Active" if is_archive else "—")))
        officer = escape(str(order.get("officer") or order.get("installation_officer", "N/A")))
        date_str = escape(str(order.get("installation_date", "N/A")))
        warehouse = escape(str(order.get("warehouse", "N/A")))
        sales_order = escape(str(order.get("sales_order", "N/A")))
        account_email = escape(str(order.get('account_email') or 'Active Session'))

        status_str = escape(str(status or "N/A"))
        status_color = "bold green" if "installed" in status_str.lower() else "yellow"

        vault_root = getattr(settings, "VAULT_ROOT", "media/vault")
        order_dir = os.path.join(vault_root, "itms_photos", str(order.get("order_number", "")))
        has_local_vault = os.path.isdir(order_dir)
        vault_status = "[bold green]✓ Downloaded in Vault[/bold green]" if has_local_vault else "[dim]Not downloaded[/dim]"

        lines = [
            f"[b cyan]═══ ITMS Order Inspector ═══[/b cyan]",
            f"[b]Order #:[/b]        [bold white]#{order_num}[/bold white]",
            f"[b]Registration:[/b]   [bold green]{plate}[/bold green]",
            f"[b]Chassis / VIN:[/b]  {vin}",
            f"[b]Linked Account:[/b] [bold green]{account_email}[/bold green]",
            f"[b]Order Status:[/b]   [{status_color}]{status_str}[/{status_color}]",
            f"[b]Reg Status:[/b]     {reg_status}",
            f"[b]Officer:[/b]        {officer}",
            f"[b]Install Date:[/b]   {date_str}",
            f"[b]Warehouse:[/b]      {warehouse[:32]}",
            f"[b]Sales Order:[/b]    {sales_order}",
            "",
            "[b underline]Photographic Evidence & Vault[/b underline]:",
            f" [b]Safe Storage:[/b]  {vault_status}",
            f" [b]Vault Folder:[/b]  media/vault/itms_photos/{order_num}/",
            "",
            "[b]Quick Actions:[/b]",
            " [b yellow]V[/b yellow]: View Side-by-Side Photos (GUI)",
            " [b cyan]Enter[/b cyan]: Inspect Full Hardware & Serials",
            " [b green]F[/b green]: Cycle Navigation Sub-View",
        ]
        self.update("\n".join(lines))

    def show_itms_kit(self, kit: Optional[Dict[str, Any]]):
        """Displays rich hardware component breakdown and metadata for a selected ITMS installation kit."""
        if not kit:
            self.update(
                "[dim]No Installation Kit selected.\n"
                "Use ↑/↓ arrow keys to browse table.\n"
                "Press [b cyan]Enter[/b cyan] or click [b]Kit Details[/b] to fetch full breakdown.\n"
                "Press [b yellow]F[/b yellow] to cycle sub-views.[/dim]"
            )
            return

        kit_code = escape(str(kit.get("kit_code") or kit.get("number") or "N/A"))
        plate = escape(str(kit.get("registration_number", "N/A")))
        status = str(kit.get("status", "New"))
        created_date = escape(str(kit.get("created_date", "—")))
        warehouse = escape(str(kit.get("warehouse", "—")))
        created_by = escape(str(kit.get("created_by_user", "—")))

        status_color = "bold green" if status.lower() == "new" else "bold cyan" if "installed" in status.lower() else "yellow"

        # Hardware breakdown (from detail dictionary or list row fallback)
        front_plate = kit.get("front_plate") or {}
        rear_plate = kit.get("rear_plate") or {}
        gps_tracker = kit.get("gps_tracker") or {}
        sim_card = kit.get("sim_card") or {}
        front_tracker = kit.get("front_tracker") or {}
        rear_tracker = kit.get("rear_tracker") or {}

        # Handle either dicts (from detail inspection) or string serials (from list row)
        fp_serial = escape(front_plate.get("serial_number", "") if isinstance(front_plate, dict) else str(front_plate or ""))
        if not fp_serial or fp_serial == "{}":
            fp_serial = escape(str(kit.get("front_plate") or "—")) if not isinstance(kit.get("front_plate"), dict) else "—"
        fp_article = escape(front_plate.get("article_name", "PN-PBL-M-UMA-SQR-BWW") if isinstance(front_plate, dict) else (kit.get("front_plate_article") or "PN-PBL-M-UMA-SQR-BWW"))

        rp_serial = escape(rear_plate.get("serial_number", "") if isinstance(rear_plate, dict) else str(rear_plate or ""))
        if not rp_serial or rp_serial == "{}":
            rp_serial = escape(str(kit.get("rear_plate") or "—")) if not isinstance(kit.get("rear_plate"), dict) else "—"
        rp_article = escape(rear_plate.get("article_name", "PN-PBL-M-UMA-SQR-BWW") if isinstance(rear_plate, dict) else (kit.get("rear_plate_article") or "PN-PBL-M-UMA-SQR-BWW"))

        gps_serial = escape(gps_tracker.get("serial_number", "") if isinstance(gps_tracker, dict) else str(gps_tracker or ""))
        if not gps_serial or gps_serial == "{}":
            gps_serial = escape(str(kit.get("gps_tracker") or "—")) if not isinstance(kit.get("gps_tracker"), dict) else "—"
        gps_article = escape(gps_tracker.get("article_name", "GPS") if isinstance(gps_tracker, dict) else (kit.get("gps_article") or "GPS"))
        gps_mac = escape(gps_tracker.get("mac_address", "—") if isinstance(gps_tracker, dict) else "—") or "—"

        sim_serial = escape(sim_card.get("serial_number", "") if isinstance(sim_card, dict) else str(kit.get("sim_serial") or "—")) or "—"
        sim_article = escape(sim_card.get("article_name", "SIM chip") if isinstance(sim_card, dict) else (kit.get("sim_article") or "SIM chip"))
        sim_mac = escape(sim_card.get("mac_address", "") if isinstance(sim_card, dict) else str(kit.get("sim_mac") or "—")) or "—"

        ft_serial = escape(front_tracker.get("serial_number", "") if isinstance(front_tracker, dict) else str(front_tracker or ""))
        if not ft_serial or ft_serial == "{}":
            ft_serial = escape(str(kit.get("front_tracker") or "—")) if not isinstance(kit.get("front_tracker"), dict) else "—"
        ft_article = escape(front_tracker.get("article_name", "BLE") if isinstance(front_tracker, dict) else (kit.get("front_tracker_article") or "BLE"))

        rt_serial = escape(rear_tracker.get("serial_number", "") if isinstance(rear_tracker, dict) else str(rear_tracker or ""))
        if not rt_serial or rt_serial == "{}":
            rt_serial = escape(str(kit.get("rear_tracker") or "—")) if not isinstance(kit.get("rear_tracker"), dict) else "—"
        rt_article = escape(rear_tracker.get("article_name", "BLE") if isinstance(rear_tracker, dict) else (kit.get("rear_tracker_article") or "BLE"))

        lines = [
            f"[b cyan]═══ Installation Kit Inspector ═══[/b cyan]",
            f"[b]Kit Code:[/b]       [bold white]{kit_code}[/bold white]",
            f"[b]Registration:[/b]   [bold green]{plate}[/bold green]",
            f"[b]Status:[/b]         [{status_color}]{escape(status)}[/{status_color}]",
            f"[b]Created By:[/b]     {created_by}",
            f"[b]Created Date:[/b]   {created_date}",
            f"[b]Warehouse:[/b]      {warehouse[:30]}",
            "",
            "[b underline]Component Inventory & Hardware[/b underline]:",
            f" [bold white]Front Plate:[/bold white]    {fp_serial} [dim]({fp_article})[/dim]",
            f" [bold white]Rear Plate:[/bold white]     {rp_serial} [dim]({rp_article})[/dim]",
            f" [bold white]GPS Tracker:[/bold white]    {gps_serial} [dim]({gps_article})[/dim]",
            f" [bold white]SIM Card:[/bold white]       {sim_serial} [cyan]MAC:[/cyan] {sim_mac}",
            f" [bold white]Front BLE:[/bold white]      {ft_serial} [dim]({ft_article})[/dim]",
            f" [bold white]Rear BLE:[/bold white]       {rt_serial} [dim]({rt_article})[/dim]",
            "",
            "[b]Quick Actions:[/b]",
            " [b cyan]Enter[/b cyan]: Load Full Hardware Info from ITMS",
            " [b green]F[/b green]: Cycle Navigation Sub-View",
        ]
        self.update("\n".join(lines))

    def show_stock_kit_details(
        self,
        plate: Optional[str] = None,
        dispatch_scan: Optional[Any] = None,
        kit: Optional[Any] = None,
        verification_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Displays comprehensive hardware profile, stock verification status,
        and ITMS order linkage for a plate scanned or selected in the Stock tab.
        """
        if not plate and not dispatch_scan and not kit:
            self.update(
                "[dim]No Plate or Kit selected.\n\n"
                "⚡ Rapid scan a plate QR/barcode or select a row in the table.\n"
                "Full hardware serials (GPS tracker, BLE beacons), warehouse location,\n"
                "and live stock verification will appear here automatically.[/dim]"
            )
            return

        from core.vision import normalizer
        from core.models import InstallationKit, StockDispatchScan, InstallationOrder

        norm_plate = normalizer.normalize_plate(plate) if plate else None
        if not norm_plate and dispatch_scan:
            norm_plate = normalizer.normalize_plate(getattr(dispatch_scan, "registration_number", ""))
        if not norm_plate and kit:
            norm_plate = normalizer.normalize_plate(getattr(kit, "registration_number", ""))

        # If kit not passed directly, look up locally
        if not kit and norm_plate:
            from django.db.models import Q
            kit = InstallationKit.objects.filter(
                Q(registration_number=norm_plate) | Q(kit_code__icontains=norm_plate)
            ).first()

        # If dispatch_scan not passed, find latest
        if not dispatch_scan and norm_plate:
            dispatch_scan = StockDispatchScan.objects.filter(registration_number=norm_plate).order_by("-dispatched_at").first()

        # Linked order lookup
        order = None
        if norm_plate:
            order = InstallationOrder.objects.filter(registration_number=norm_plate).first()

        # Category determination
        cat_badge = "[bold white on dark_blue] PSV White [/]"
        if dispatch_scan and getattr(dispatch_scan, "plate_category", "") == "PMO":
            cat_badge = "[bold black on gold1] PMO Yellow [/]"
        elif kit and "PMO" in getattr(kit, "kit_code", "").upper():
            cat_badge = "[bold black on gold1] PMO Yellow [/]"

        # Verification Status Badge
        is_blocked = bool(error_message)
        if not is_blocked and verification_result is not None:
            if isinstance(verification_result, tuple):
                is_blocked = not verification_result[0]
                if is_blocked and len(verification_result) > 2 and verification_result[2]:
                    error_message = verification_result[2]
            elif isinstance(verification_result, dict):
                is_blocked = not (verification_result.get("is_valid") or verification_result.get("valid"))
                if is_blocked:
                    error_message = verification_result.get("error") or verification_result.get("reason")
            elif hasattr(verification_result, "is_valid"):
                is_blocked = not verification_result.is_valid
                if is_blocked:
                    error_message = getattr(verification_result, "reason", "")

        if is_blocked:
            err_lower = (error_message or "").lower()
            if "awaiting" in err_lower or "transfer" in err_lower:
                status_badge = "[bold white on dark_red] ⏳ AWAITING ITMS TRANSFER [/]"
            else:
                status_badge = "[bold white on dark_red] ⛔ NOT ON STOCK (BLOCKED) [/]"
            status_desc = f"[red]{escape(error_message or 'Not found on stock in Safe Room or ITMS')}[/red]"


        elif dispatch_scan:
            status_badge = "[bold black on gold1] 📤 DISPATCHED TO LINE [/]"
            t_dt = getattr(dispatch_scan, "dispatched_at", None) or getattr(dispatch_scan, "created_at", None)
            t_str = t_dt.strftime("%H:%M:%S") if t_dt else "Today"
            op = getattr(dispatch_scan, "operator_name", "") or getattr(dispatch_scan, "operator_username", "Operator")
            status_desc = f"[yellow]Dispatched at {t_str} by {escape(op)}[/yellow]"
        elif kit:
            k_stat = getattr(kit, "status", "New")
            if k_stat.lower() == "new":
                status_badge = "[bold white on dark_green] ✓ ON STOCK (Safe Room) [/]"
                status_desc = "[green]Ready in warehouse safe stock. Clean to dispatch.[/green]"
            elif "installed" in k_stat.lower():
                status_badge = "[bold white on dark_blue] 🏆 INSTALLED / ARCHIVED [/]"
                status_desc = f"[cyan]Order completed on {escape(str(getattr(kit, 'created_date', '')))}[/cyan]"
            else:
                status_badge = f"[bold yellow] ⏳ {escape(k_stat.upper())} [/]"
                status_desc = f"[yellow]Status in ITMS: {escape(k_stat)}[/yellow]"
        else:
            status_badge = "[bold white on dark_blue] ℹ️ UNVERIFIED [/]"
            status_desc = "[dim]Scan barcode or query ITMS to verify stock status.[/dim]"

        lines = [
            "[b cyan]═══ 📦 Stock & Kit Inspector ═══[/b cyan]",
            f"[b]Plate:[/b]          [bold yellow]{escape(norm_plate or 'UNKNOWN')}[/bold yellow]  {cat_badge}",
            f"[b]Stock Status:[/b]   {status_badge}",
            f"               {status_desc}",
            "",
            "[b underline]ITMS Installation Kit Profile[/b underline]:",
        ]

        if kit:
            wh = (
                getattr(kit, "warehouse", "")
                or getattr(kit, "warehouse_facility", "")
                or "Warehouse Stock"
            )
            gps = (
                getattr(kit, "gps_tracker", "")
                or getattr(kit, "gps_tracker_id", "")
                or getattr(kit, "imei", "")
                or "—"
            )
            f_ble = (
                getattr(kit, "front_tracker", "")
                or getattr(kit, "ble_beacon_front", "")
                or "—"
            )
            r_ble = (
                getattr(kit, "rear_tracker", "")
                or getattr(kit, "ble_beacon_rear", "")
                or "—"
            )
            f_plate = (
                getattr(kit, "front_plate", "")
                or getattr(kit, "front_plate_serial", "")
                or "—"
            )
            r_plate = (
                getattr(kit, "rear_plate", "")
                or getattr(kit, "rear_plate_serial", "")
                or "—"
            )

            lines.extend([
                f" [b]Kit Code:[/b]      [bold white]{escape(getattr(kit, 'kit_code', '—'))}[/bold white]",
                f" [b]Warehouse:[/b]     {escape(wh[:32])}",
                f" [b]GPS Tracker:[/b]   [bold cyan]{escape(gps)}[/bold cyan]",
                f" [b]Front BLE:[/b]     {escape(f_ble)}",
                f" [b]Rear BLE:[/b]      {escape(r_ble)}",
                f" [b]Front Plate:[/b]   {escape(f_plate)}",
                f" [b]Rear Plate:[/b]    {escape(r_plate)}",
            ])
        else:
            lines.append(" [dim]No linked kit record found in local inventory.[/dim]")

        # Order Linkage section
        lines.append("")
        lines.append("[b underline]ITMS Order Linkage[/b underline]:")
        if order:
            o_num = escape(getattr(order, "order_number", "—"))
            o_stat = escape(getattr(order, "status", "Active"))
            lines.extend([
                f" [b]Order Number:[/b]  [bold green]#{o_num}[/bold green] ([yellow]{o_stat}[/yellow])",
                f" [b]Owner / Fleet:[/b] {escape(getattr(order, 'owner_name', '—')[:30])}",
                f" [b]Motorcycle:[/b]    {escape(getattr(order, 'motorcycle_model', '—'))} [dim]({escape(getattr(order, 'chassis_number', '—'))})[/dim]",
            ])
        else:
            lines.append(" [bold green]● Unallocated Kit[/bold green] [dim](Available for line allocation)[/dim]")

        # Line Dispatch details
        if dispatch_scan:
            lines.append("")
            lines.append("[b underline]Line Dispatch Record[/b underline]:")
            t_dt = getattr(dispatch_scan, "dispatched_at", None) or getattr(dispatch_scan, "created_at", None)
            lines.extend([
                f" [b]Dispatched At:[/b] {t_dt.strftime('%Y-%m-%d %H:%M:%S') if t_dt else '—'}",
                f" [b]Category:[/b]      {escape(getattr(dispatch_scan, 'plate_category', 'PSV'))}",
                f" [b]Shift Suffix:[/b]  {escape(getattr(dispatch_scan, 'work_date_suffix', '—'))}",
                f" [b]Status:[/b]        {escape(getattr(dispatch_scan, 'status', 'ON_LINE_ACTIVE'))}",
            ])

        lines.extend([
            "",
            "[b]Quick Actions:[/b]",
            " [b green]Enter[/b green]: Scan next plate barcode",
            " [b cyan]Tab[/b cyan]: Switch between inputs and tables",
        ])

        self.update("\n".join(lines))


