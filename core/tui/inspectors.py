from __future__ import annotations

import os
from typing import Any, Dict, Optional

from django.conf import settings
from textual.widgets import Static
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
            f"[green]{pair.order.order_number}[/green] (expects [b]{pair.order.registration_number}[/b])"
            if pair.order
            else "[yellow]— unmatched —[/yellow]"
        )
        serial_line = (
            pair.order.plate_serial
            or pair.order.front_plate_serial
            or pair.order.rear_plate_serial
            or "—"
        ) if pair.order else "—"
        tracker_line = (
            pair.order.tracker_id
            or pair.order.gps_tracker_id
            or "—"
        ) if pair.order else "—"
        front = pair.front_image
        rear = pair.rear_image
        status_color = STATUS_STYLE.get(pair.verification_status, "white")

        from core.services.camera_naming import parse_camera_filename

        front_file = front.vault_file if front else "[red]MISSING[/red]"
        front_ocr = round(front.ocr_confidence, 3) if front and front.ocr_confidence is not None else "N/A"
        front_orient = front.orientation if front else "—"
        front_conf = f"({round(front.orientation_confidence, 2)})" if front and front.orientation_confidence is not None else ""
        front_cam = f" [cyan]({parse_camera_filename(front.original_source_path or front.vault_file).display_tag})[/cyan]" if front else ""

        rear_file = rear.vault_file if rear else "[red]MISSING[/red]"
        rear_ocr = round(rear.ocr_confidence, 3) if rear and rear.ocr_confidence is not None else "N/A"
        rear_orient = rear.orientation if rear else "—"
        rear_conf = f"({round(rear.orientation_confidence, 2)})" if rear and rear.orientation_confidence is not None else ""
        rear_cam = f" [cyan]({parse_camera_filename(rear.original_source_path or rear.vault_file).display_tag})[/cyan]" if rear else ""

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

        account_str = pair.account_email or (pair.order.account_email if pair.order else "") or "[dim]Unassigned[/dim]"

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
            batch_line = f"{batch.batch_id} ({batch.source_label or 'Batch'}) — {tag}"
        else:
            batch_line = "[dim]No batch associated[/dim]"

        lines = [
            f"[b cyan]═══ Pair Inspector ═══[/b cyan]",
            f"[b]Detected Plate:[/b] [bold yellow]{pair.registration_number_detected}[/bold yellow]{override_tag}",
            f"[b]Batch Origin:[/b]   {batch_line}",
            f"[b]ITMS Account:[/b]   [bold green]{account_str}[/bold green]",
            f"[b]Vehicle Class:[/b]  {category_badge}",
            f"[b]Status:[/b]         [{status_color}]{pair.verification_status}[/{status_color}]",
        ]

        if pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED:
            latest_fail = pair.audit_logs.filter(result=SubmissionAuditLog.ResultStatus.FAILURE).first()
            if latest_fail:
                fail_msg = latest_fail.message[:75] + ("..." if len(latest_fail.message) > 75 else "")
                lines.append(f"[b]Failure Reason:[/b] [bold red]{fail_msg}[/bold red]")

        lines.extend([
            f"[b]Match Quality:[/b]  {pair.match_type} (score: {pair.match_score if pair.match_score is not None else 'N/A'}, via: [cyan]{matched_via_tag}[/cyan])",
            f"[b]Pairing Reason:[/b] [cyan]{pair.operator_note or '—'}[/cyan]",
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
            f"[b cyan]═══ Audit Trail: {pair.registration_number_detected} ═══[/b cyan]",
            f"[b]Status:[/b] {pair.verification_status}  │  [b]Order:[/b] {pair.order.order_number if pair.order else '—'}",
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
                token_str = f" (token: {log.simulated_token[:10]}...)" if log.simulated_token else ""
                lines.append(f"• [{time_str}] [b]{log.action}[/b] -> [{result_color}]{log.result}[/{result_color}]{token_str}")
                if log.message:
                    lines.append(f"  [dim]{log.message}[/dim]")

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
            f"[b]Batch ID:[/b]        [bold yellow]{batch.batch_id}[/bold yellow]",
            f"[b]Source Channel:[/b]   {batch.source_type} ({batch.source_label or 'None'})",
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

        filename = os.path.basename(image.original_source_path or image.vault_file)
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
            x1, y1, x2, y2 = [int(v) for v in image.bbox]
            w_px, h_px = x2 - x1, y2 - y1
            bbox_str = f"[{x1}, {y1}, {x2}, {y2}]  ({w_px}×{h_px} px)"
        else:
            bbox_str = "[dim]None (No plate candidate localized)[/dim]"

        # Confidences
        ocr_str = f"{round(image.ocr_confidence * 100, 1)}% ({round(image.ocr_confidence, 3)})" if image.ocr_confidence is not None else "[dim]N/A[/dim]"
        det_str = f"{round(image.detector_confidence * 100, 1)}% ({round(image.detector_confidence, 3)})" if image.detector_confidence is not None else "[dim]N/A[/dim]"

        lines = [
            f"[b cyan]═══ Vision Analysis: {filename[:28]} ═══[/b cyan]",
            f"[b]Status:[/b]         [{status_style}]{image.status}[/{status_style}]",
            f"[b]Detected Plate:[/b] [bold yellow]{image.detected_plate or '—'}[/bold yellow]{syntax_info}",
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
            lines.append(f" [yellow]{image.error_message}[/yellow]")
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
            f" [b]Vault File:[/b] {image.vault_file}",
            f" [b]Camera Origin:[/b] [cyan]{cam_str}[/cyan]",
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

        order_num = order.get("order_number", "N/A")
        plate = order.get("registration_number", "N/A")
        vin = order.get("vin", "N/A")
        status = order.get("order_status") or order.get("status", "N/A")
        reg_status = order.get("registration_status", "Active" if is_archive else "—")
        officer = order.get("officer") or order.get("installation_officer", "N/A")
        date_str = order.get("installation_date", "N/A")
        warehouse = order.get("warehouse", "N/A")
        sales_order = order.get("sales_order", "N/A")

        status_color = "bold green" if "installed" in status.lower() else "yellow"

        vault_root = getattr(settings, "VAULT_ROOT", "media/vault")
        order_dir = os.path.join(vault_root, "itms_photos", order_num)
        has_local_vault = os.path.isdir(order_dir)
        vault_status = "[bold green]✓ Downloaded in Vault[/bold green]" if has_local_vault else "[dim]Not downloaded[/dim]"

        lines = [
            f"[b cyan]═══ ITMS Order Inspector ═══[/b cyan]",
            f"[b]Order #:[/b]        [bold white]#{order_num}[/bold white]",
            f"[b]Registration:[/b]   [bold green]{plate}[/bold green]",
            f"[b]Chassis / VIN:[/b]  {vin}",
            f"[b]Linked Account:[/b] [bold green]{order.get('account_email') or 'Active Session'}[/bold green]",
            f"[b]Order Status:[/b]   [{status_color}]{status}[/{status_color}]",
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

