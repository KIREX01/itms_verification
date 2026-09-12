import os
from typing import Optional
from textual.widgets import DataTable, Static
from core.models import (
    EvidenceImage,
    IngestionBatch,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.tui.inspectors import STATUS_STYLE

HISTORY_FILTERS = ["ALL", "SUBMITTED", "FAILED", "APPROVED", "AUDIT_LOGS"]

class TableLoaderMixin:
    def _update_history_filter_bar(self):
        filter_str = "  ".join([
            f"[bold green]▶ {f}[/bold green]" if f == self.current_history_filter else f"[dim]{f}[/dim]"
            for f in HISTORY_FILTERS
        ])
        bar = self.query_one("#history-filter-bar", Static)
        bar.update(f"[b]History Label Filter:[/b] {filter_str}   [dim](Press [b]F[/b] to cycle)[/dim]")

    def _reload_queue_table(self):
        from core.services.itms_web_client import get_current_itms_account
        active_acc = get_current_itms_account()

        table = self.query_one("#table-queue", DataTable)
        table.clear()
        qs = VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image").filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
                VehicleInstallationPair.VerificationStatus.APPROVED,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.UNREGISTERED,
                VehicleInstallationPair.VerificationStatus.FAILED,
            ]
        )
        if active_acc:
            from django.db.models import Q
            qs = qs.filter(
                Q(order__account_email__iexact=active_acc) |
                Q(account_email__iexact=active_acc) |
                (
                    (Q(order__isnull=True) | Q(order__account_email="") | Q(order__account_email__isnull=True)) &
                    (Q(account_email="") | Q(account_email__isnull=True))
                )
            )
        qs = qs.order_by("-updated_at")[:250]

        for pair in qs:
            style = STATUS_STYLE.get(pair.verification_status, "white")
            score_str = str(round(pair.match_score, 2)) if pair.match_score is not None else "—"
            front_conf = str(round(pair.front_image.ocr_confidence, 2)) if pair.front_image and pair.front_image.ocr_confidence else "—"
            rear_conf = str(round(pair.rear_image.ocr_confidence, 2)) if pair.rear_image and pair.rear_image.ocr_confidence else "—"

            match_display = pair.match_type
            if getattr(pair, "matched_via", None) == VehicleInstallationPair.MatchedVia.MANUAL:
                match_display = "[cyan]MANUAL[/cyan]"
            elif getattr(pair, "matched_via", None) == VehicleInstallationPair.MatchedVia.ORDER_PRIOR:
                match_display = "[magenta]PRIOR[/magenta]"

            table.add_row(
                str(pair.id)[:8],
                pair.registration_number_detected,
                pair.order.order_number if pair.order else "—",
                match_display,
                score_str,
                f"[{style}]{pair.verification_status}[/{style}]",
                "✓" if pair.is_complete else "✗",
                front_conf,
                rear_conf,
                key=str(pair.id),
            )

        if table.row_count > 0 and table.cursor_row is not None:
            self._update_queue_inspector()

    def _reload_history_table(self):
        from core.services.itms_web_client import get_current_itms_account
        active_acc = get_current_itms_account()

        table = self.query_one("#table-history", DataTable)
        table.clear(columns=True)

        if self.current_history_filter == "AUDIT_LOGS":
            table.add_columns("Time", "Pair / Plate", "Action", "Result", "Message", "Token")
            logs = SubmissionAuditLog.objects.select_related("pair", "pair__order").order_by("-timestamp")
            if active_acc:
                from django.db.models import Q
                logs = logs.filter(
                    Q(pair__order__account_email__iexact=active_acc) |
                    Q(pair__account_email__iexact=active_acc)
                )
            for log in logs[:100]:
                plate = log.pair.registration_number_detected if log.pair else "—"
                res_color = "green" if log.result == "SUCCESS" else "red" if log.result == "FAILURE" else "yellow"
                table.add_row(
                    log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    plate,
                    log.action,
                    f"[{res_color}]{log.result}[/{res_color}]",
                    (log.message[:45] + "...") if len(log.message) > 45 else log.message,
                    (log.simulated_token[:12] + "...") if log.simulated_token else "—",
                    key=str(log.id),
                )
        else:
            table.add_columns("ID", "Plate", "Order", "Status", "Submitted At", "Updated At")
            qs = VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image")
            if active_acc:
                from django.db.models import Q
                qs = qs.filter(
                    Q(order__account_email__iexact=active_acc) |
                    Q(account_email__iexact=active_acc)
                )
            if self.current_history_filter == "SUBMITTED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
            elif self.current_history_filter == "FAILED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.FAILED)
            elif self.current_history_filter == "APPROVED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)

            for pair in qs.order_by("-updated_at")[:100]:
                style = STATUS_STYLE.get(pair.verification_status, "white")
                sub_at = pair.submitted_at.strftime("%Y-%m-%d %H:%M") if pair.submitted_at else "—"
                upd_at = pair.updated_at.strftime("%Y-%m-%d %H:%M")

                table.add_row(
                    str(pair.id)[:8],
                    pair.registration_number_detected,
                    pair.order.order_number if pair.order else "—",
                    f"[{style}]{pair.verification_status}[/{style}]",
                    sub_at,
                    upd_at,
                    key=str(pair.id),
                )

        if table.row_count > 0 and table.cursor_row is not None:
            self._update_history_inspector()

    def _reload_batches_table(self):
        table = self.query_one("#table-batches", DataTable)
        table.clear()
        batches = list(IngestionBatch.objects.all().order_by("-created_at")[:50])
        for b in batches:
            table.add_row(
                b.batch_id,
                b.source_type,
                b.source_label or "—",
                str(b.total_files),
                f"[green]{b.ingested_count}[/green]",
                f"[yellow]{b.duplicate_count}[/yellow]",
                f"[red]{b.failed_count}[/red]" if b.failed_count > 0 else "0",
                b.created_at.strftime("%Y-%m-%d %H:%M"),
                key=str(b.batch_id),
            )

        if batches:
            if not self._selected_batch_id or not any(b.batch_id == self._selected_batch_id for b in batches):
                self._selected_batch_id = batches[0].batch_id
            self._update_batch_inspector()
            self._reload_batch_images_table(self._selected_batch_id)
        else:
            self._selected_batch_id = None
            self._reload_batch_images_table(None)
            self._update_batch_inspector()

    def _reload_batch_images_table(self, batch_id: Optional[str]):
        table = self.query_one("#table-batch-images", DataTable)
        table.clear()
        if not batch_id:
            return

        images = list(EvidenceImage.objects.filter(batch__batch_id=batch_id).order_by("ingested_at"))
        for idx, img in enumerate(images, 1):
            status_style = {
                EvidenceImage.Status.PLATE_DETECTED: "bold green",
                EvidenceImage.Status.NEEDS_REVIEW: "bold yellow",
                EvidenceImage.Status.FAILED: "bold red",
                EvidenceImage.Status.PROCESSING: "cyan",
                EvidenceImage.Status.NEW: "dim white",
                EvidenceImage.Status.MATCHED: "green",
                EvidenceImage.Status.READY: "bold cyan",
                EvidenceImage.Status.SUBMITTED: "blue",
            }.get(img.status, "white")

            filename = os.path.basename(img.original_source_path or img.vault_file)
            plate_disp = f"[bold yellow]{img.detected_plate}[/bold yellow]" if img.detected_plate else "[dim]—[/dim]"
            ocr_disp = f"{round(img.ocr_confidence, 2)}" if img.ocr_confidence is not None else "—"
            det_disp = f"{round(img.detector_confidence, 2)}" if img.detector_confidence is not None else "—"

            orient_style = "green" if img.orientation == "FRONT" else "cyan" if img.orientation == "REAR" else "dim"
            orient_disp = f"[{orient_style}]{img.orientation}[/{orient_style}]"
            orient_conf_disp = f"{round(img.orientation_confidence, 2)}" if img.orientation_confidence is not None else "—"

            if img.bbox and len(img.bbox) == 4:
                bbox_disp = f"[{img.bbox[0]},{img.bbox[1]},{img.bbox[2]},{img.bbox[3]}]"
            else:
                bbox_disp = "—"

            table.add_row(
                str(idx),
                str(img.id)[:8],
                filename[:20],
                f"[{status_style}]{img.status}[/{status_style}]",
                plate_disp,
                ocr_disp,
                det_disp,
                orient_disp,
                orient_conf_disp,
                bbox_disp,
                key=str(img.id),
            )
