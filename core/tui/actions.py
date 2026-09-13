import os
from typing import Any, Dict, List, Optional
from django.conf import settings
from django.core.management import call_command
from textual import work
from textual.widgets import TabbedContent
from core.services import file_dialog, vault_service, viewer
from core.services.submission_worker import submit_pair
from core.models import VehicleInstallationPair, EvidenceImage, IngestionBatch, SubmissionAuditLog
from core.tui.widgets import TextualLogStream
from core.tui.tables import HISTORY_FILTERS

class OperatorActionsMixin:
    def action_tab_dashboard(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-dashboard"
        self._set_activity_visibility(False)
        try:
            from core.tui.dashboard_pane import DashboardPane
            dash = self.query_one("#dashboard-pane", DashboardPane)
            dash.refresh_dashboard()
        except Exception:
            pass

    def action_tab_itms(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-itms"
        self._set_activity_visibility(True)
        try:
            self.query_one("#itms-connection-pane").focus()
        except Exception:
            pass

    def action_tab_batches(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-batches"
        self._set_activity_visibility(True)
        try:
            self.query_one("#table-batches").focus()
        except Exception:
            pass

    def action_tab_queue(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-queue"
        self._set_activity_visibility(True)
        try:
            self.query_one("#table-queue").focus()
        except Exception:
            pass

    def action_tab_history(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-history"
        self._set_activity_visibility(True)
        try:
            self.query_one("#table-history").focus()
        except Exception:
            pass

    def action_tab_settings(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-settings"
        self._set_activity_visibility(False)
        try:
            self.set_focus(None)
        except Exception:
            pass

    @work(thread=True)
    def action_drain_outbox(self) -> None:
        """Drains any orders waiting in OFFLINE_OUTBOX status."""
        from core.models import VehicleInstallationPair
        from core.services.submission_worker import drain_offline_outbox
        from core.services.itms_web_client import get_web_client
        from core.services import config_service

        outbox_count = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX
        ).count()
        if outbox_count == 0:
            self.call_from_thread(self.notify, "Offline Outbox is empty. No pending orders.")
            self.call_from_thread(self.log_message, "Offline Outbox is empty (0 orders).", level="INFO")
            return

        self.call_from_thread(
            self.log_message,
            f"Checking connectivity to drain {outbox_count} order(s) from Offline Outbox...",
            level="ITMS",
        )
        client = get_web_client()
        probe = client.test_connection()
        if not probe.get("success"):
            err = probe.get("error", "Host unreachable")
            self.call_from_thread(
                self.notify,
                f"Cannot drain outbox: ITMS server unreachable ({err})",
                severity="warning",
            )
            self.call_from_thread(
                self.log_message,
                f"[bold red]Cannot drain outbox:[/bold red] ITMS unreachable ({err}). Orders remain safely queued.",
                level="WARNING",
            )
            return

        dry_run = config_service.get_setting("submission.dry_run_mode", True)
        submit_step3 = config_service.get_setting("submission.submit_step3", True)

        def log_cb(msg: str):
            self.call_from_thread(self.log_message, msg, level="ITMS")

        outcomes = drain_offline_outbox(
            backend="web",
            dry_run=dry_run,
            submit_step3=submit_step3,
            log_callback=log_cb,
        )
        succeeded = sum(1 for o in outcomes if o.success)
        failed = len(outcomes) - succeeded
        self.call_from_thread(
            self.notify,
            f"Outbox sync complete: {succeeded} synced, {failed} pending.",
            severity="information" if failed == 0 else "warning",
        )
        self.call_from_thread(
            self.log_message,
            f"Offline Outbox sync finished: [bold green]{succeeded} succeeded[/bold green], [bold red]{failed} failed[/bold red].",
            level="SUCCESS" if failed == 0 else "WARNING",
        )
        self.call_from_thread(self.reload_data)

    def _set_activity_visibility(self, visible: bool) -> None:
        try:
            self.query_one("#activity-container").display = visible
        except Exception:
            pass

    @work(thread=True)
    def action_dev_seed_orders(self) -> None:
        """Seed fake installation orders for developer stress-testing."""
        from core.services.config_service import is_developer_mode
        if not is_developer_mode():
            self.call_from_thread(self.notify, "Developer mode is disabled in Settings.", severity="warning")
            return
        from django.core.management import call_command
        self.call_from_thread(self.log_message, "[DEV] Seeding test orders...", level="INFO")
        try:
            call_command("seed_orders", count=25)
            self.call_from_thread(self.log_message, "[bold green]✓ [DEV] Seeded 25 test orders.[/bold green]", level="SUCCESS")
            self.call_from_thread(self.reload_data)
        except Exception as exc:
            self.call_from_thread(self.log_message, f"[DEV] Seed orders error: {exc}", level="ERROR")

    @work(thread=True)
    def action_dev_benchmark(self) -> None:
        """Runs pipeline detection and OCR benchmarking."""
        from core.services.config_service import is_developer_mode
        if not is_developer_mode():
            self.call_from_thread(self.notify, "Developer mode is disabled in Settings.", severity="warning")
            return
        from django.core.management import call_command
        self.call_from_thread(self.log_message, "[DEV] Starting pipeline benchmark...", level="INFO")
        try:
            call_command("benchmark_pipeline")
            self.call_from_thread(self.log_message, "[bold green]✓ [DEV] Benchmark completed.[/bold green]", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"[DEV] Benchmark error: {exc}", level="ERROR")

    def action_dev_toggle_backend(self) -> None:
        """Toggles between mock and live ITMS submission backend."""
        from core.services.config_service import is_developer_mode
        if not is_developer_mode():
            self.notify("Developer mode is disabled in Settings.", severity="warning")
            return
        current_backend = getattr(settings, "ITMS_SUBMISSION_BACKEND", "mock")
        new_backend = "live" if current_backend == "mock" else "mock"
        setattr(settings, "ITMS_SUBMISSION_BACKEND", new_backend)
        self.notify(f"Switched submission backend to: {new_backend.upper()}", severity="information")
        self.log_message(f"[bold yellow]✓ [DEV] Submission backend switched to: {new_backend.upper()}[/bold yellow]", level="INFO")

    @work(thread=True)
    def action_native_ingest(self) -> None:
        """Opens native desktop dialog to pick files or folders, and ingests them into a new batch."""
        if getattr(self, "_native_ingest_running", False):
            self.call_from_thread(self.notify, "Photo ingestion dialog is already active.", severity="warning")
            return
        self._native_ingest_running = True
        try:
            self.call_from_thread(
                self.log_message,
                "Opening native photo picker dialog (select front/rear files or folder)...",
                level="INFO",
            )
            selected_items = file_dialog.prompt_native_photo_selection()
            if not selected_items:
                self.call_from_thread(self.log_message, "Photo selection cancelled by operator.", level="INFO")
                return

            total = len(selected_items)
            front_sel = sum(1 for it in selected_items if isinstance(it, dict) and it.get("orientation") == "FRONT")
            rear_sel = sum(1 for it in selected_items if isinstance(it, dict) and it.get("orientation") == "REAR")

            self.call_from_thread(
                self.log_message,
                f"Selected {total} photo(s) [{front_sel} Front, {rear_sel} Rear]. Initializing ingestion batch...",
                level="INFO",
            )

            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.CLI,
                source_label=f"Native Dialog ({total} photos: {front_sel}F/{rear_sel}R)",
            )

            ingested = 0
            skipped = 0
            failed = 0

            for it in selected_items:
                if isinstance(it, dict):
                    p = it["path"]
                    orient_override = it.get("orientation") or None
                else:
                    p = it
                    orient_override = None

                img, status = vault_service.ingest_from_disk(p, batch=batch, orientation_override=orient_override)
                base_name = os.path.basename(p)
                orient_tag = f"[{img.orientation}]" if img and img.orientation else (f"[{orient_override}]" if orient_override else "[UNKNOWN]")

                if status == "INGESTED":
                    ingested += 1
                    self.call_from_thread(
                        self.log_message,
                        f"Ingested {orient_tag:8} {base_name} -> {img.vault_file}",
                        level="INFO",
                    )
                elif status == "DUPLICATE_SKIPPED":
                    skipped += 1
                    self.call_from_thread(
                        self.log_message,
                        f"Duplicate skipped {orient_tag:8} {base_name} (already in vault)",
                        level="WARNING",
                    )
                else:
                    failed += 1
                    self.call_from_thread(
                        self.log_message,
                        f"Failed to ingest {orient_tag:8} {base_name} ({status})",
                        level="ERROR",
                    )

            batch.refresh_from_db()
            if ingested == 0 and skipped > 0:
                batch_id_str = batch.batch_id
                batch.delete()
                self.call_from_thread(
                    self.log_message,
                    f"[bold yellow]⚠️ All {skipped} selected photo(s) already exist in the database (SHA-256 duplicates skipped).[/bold yellow] Existing photos are already processed in their original batches.",
                    level="WARNING",
                )
                self.call_from_thread(
                    self.notify,
                    f"All {skipped} photos already exist in database (duplicates skipped).",
                    severity="warning",
                )
            else:
                front_total = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
                rear_total = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()
                unknown_total = batch.images.filter(orientation=EvidenceImage.Orientation.UNKNOWN).count()
                if front_total == 0 and rear_total == 0 and (front_sel > 0 or rear_sel > 0):
                    front_total = front_sel
                    rear_total = rear_sel
                breakdown = f"{front_total} Front, {rear_total} Rear"
                if unknown_total > 0:
                    breakdown += f", {unknown_total} Unknown"
                # Automatically run physical pre-pairing on the newly ingested batch
                try:
                    from core.matcher import association
                    assoc_res = association.run_association(batch_id=batch.batch_id)
                    if assoc_res.complete_pairs > 0:
                        self.call_from_thread(
                            self.log_message,
                            f"Auto-paired [bold green]{assoc_res.complete_pairs} complete pair(s)[/bold green] via physical signals (U-Turn walk & filename sequence).",
                            level="SUCCESS",
                        )
                except Exception as assoc_err:
                    self.call_from_thread(self.log_message, f"Auto-pairing note: {assoc_err}", level="WARNING")

                self.call_from_thread(
                    self.log_message,
                    f"Batch {batch.batch_id} complete: {ingested} ingested ({breakdown}), {skipped} duplicates skipped, {failed} failed.",
                    level="SUCCESS",
                )
                self.call_from_thread(
                    self.log_message,
                    "Tip: Press [b yellow]P[/b yellow] to run Dual-Stream Joint Vision on newly formed pairs.",
                    level="INFO",
                )
                self.call_from_thread(self.notify, f"Batch {batch.batch_id}: {ingested} photos ({front_total}F / {rear_total}R) ingested & paired!")
            self.call_from_thread(self.reload_data)
        finally:
            self._native_ingest_running = False

    def action_open_upload_ui(self):
        import socket
        import webbrowser
        url = "http://127.0.0.1:8000/upload/"
        server_running = False
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.3):
                server_running = True
        except OSError:
            server_running = False

        if server_running:
            try:
                webbrowser.open(url)
                self.notify(f"Opened Web Upload: {url}")
                self.log_message(f"Opened Web Upload in browser: [bold underline cyan]{url}[/bold underline cyan]", level="INFO")
            except Exception as exc:
                self.notify(f"Could not open browser: {exc}", severity="warning")
        else:
            self.notify("Web server not running on port 8000. Launching Native Dialog...", severity="warning")
            self.log_message(
                "Web server is not running on 127.0.0.1:8000. To use browser upload, run in another terminal: [bold cyan]python manage.py runserver[/bold cyan]",
                level="WARNING",
            )
            self.log_message("Opening native desktop file picker dialog instead...", level="INFO")
            self.action_native_ingest()

    def action_cycle_filter(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        if tabs.active == "tab-itms":
            from core.tui.itms_pane import ITMSConnectionPane
            itms_pane = self.query_one("#itms-connection-pane", ITMSConnectionPane)
            itms_pane.action_cycle_filter()
            return
        self.history_filter_index = (self.history_filter_index + 1) % len(HISTORY_FILTERS)
        self.current_history_filter = HISTORY_FILTERS[self.history_filter_index]
        self._update_history_filter_bar()
        self._reload_history_table()
        self.log_message(f"History filter switched to: [b]{self.current_history_filter}[/b]")

    def action_refresh(self):
        self.reload_data()
        self.log_message("Dashboard refreshed from database.", level="INFO")

    def action_approve(self):
        pair = self._get_active_pair()
        if not pair:
            self.notify("No pair selected to approve.", severity="warning")
            return

        # Attempt auto-linking if order is missing
        if pair.is_complete and not pair.order:
            from core.matcher.order_matcher import match_pair_to_order
            match_pair_to_order(pair)
            if not pair.order:
                from core.models import InstallationOrder
                clean_reg = "".join(c for c in pair.registration_number_detected.upper() if c.isalnum())
                found_order = (
                    InstallationOrder.objects.filter(registration_number__iexact=clean_reg)
                    .exclude(status=InstallationOrder.Status.SUBMITTED)
                    .first()
                )
                if found_order:
                    pair.order = found_order
                    pair.save(update_fields=["order"])

        if not pair.is_complete or not pair.order:
            self.notify(f"Cannot approve {pair.registration_number_detected}: Order not found in ITMS.", severity="error")
            self.log_message(
                f"Cannot approve {pair.registration_number_detected}: Order not found in registry (not yet created on ITMS). Sync via [S] or type plate [T].",
                level="WARNING",
            )
            return

        from core.models import InstallationOrder
        op_name = self.current_user.username if getattr(self, "current_user", None) else "Operator"
        was_failed = pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])

        if pair.order and pair.order.status == InstallationOrder.Status.FAILED:
            pair.order.status = InstallationOrder.Status.PENDING
            pair.order.save(update_fields=["status"])

        audit_msg = (
            f"Pair reset from FAILED to APPROVED for retry by operator '{op_name}' in TUI."
            if was_failed
            else f"Pair approved by operator '{op_name}' in TUI."
        )
        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=audit_msg,
        )
        notify_msg = (
            f"Re-approved {pair.registration_number_detected} for retry submission."
            if was_failed
            else f"Approved {pair.registration_number_detected} for submission."
        )
        self.notify(notify_msg)
        self.log_message(f"{notify_msg} by {op_name} (Order: {pair.order.order_number})", level="SUCCESS")
        self.reload_data()

    def action_swap(self):
        pair = self._get_active_pair()
        if not pair or not (pair.front_image and pair.rear_image):
            self.notify("Need both front and rear images to swap assignments.", severity="warning")
            return

        op_name = self.current_user.username if getattr(self, "current_user", None) else "Operator"
        pair.front_image, pair.rear_image = pair.rear_image, pair.front_image
        pair.save(update_fields=["front_image", "rear_image"])
        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_SWAP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator '{op_name}' swapped front and rear image assignments in TUI.",
        )
        self.notify(f"Swapped front/rear for {pair.registration_number_detected}.")
        self.log_message(f"Swapped front/rear assignments for {pair.registration_number_detected} by {op_name}", level="INFO")
        self.reload_data()

    def action_logout(self):
        """Signs out of current operator session and returns to landing portal. Preserves ITMS WebApp session."""
        from core.services import auth_service
        from core.tui.auth_screens import LandingAuthScreen

        auth_service.clear_remembered_session()
        old_user = getattr(self, "current_user", None)
        self.current_user = None
        op_name = old_user.username if old_user else "Guest"
        self.notify(f"System Operator '{op_name}' signed out. ITMS session remains active in vault.", severity="information")
        self.log_message(f"System Operator '{op_name}' signed out. ITMS WebApp session remains preserved in vault.", level="AUTH")
        self.push_screen(LandingAuthScreen(), self._on_auth_completed)

    def action_link_pair(self):
        """Opens interactive Closest Photo Picker modal to link front/rear photos."""
        pair = self._get_active_pair()
        if not pair:
            self.notify("Select a pair to link photos.", severity="warning")
            return

        # Identify existing anchor photo
        target_image = pair.rear_image or pair.front_image
        if not target_image:
            self.notify(f"Pair {pair.registration_number_detected} has no evidence photos attached.", severity="warning")
            return

        from core.tui.dialogs import PhotoLinkerModal
        from core.matcher import association

        def on_link_selected(selected_candidate):
            if not selected_candidate:
                self.log_message("Photo linking cancelled by operator.", level="INFO")
                return

            cand_image = selected_candidate["image"]

            # Determine front vs rear assignment
            if target_image.orientation == EvidenceImage.Orientation.FRONT:
                front = target_image
                rear = cand_image
            elif target_image.orientation == EvidenceImage.Orientation.REAR:
                rear = target_image
                front = cand_image
            else:
                if cand_image.orientation == EvidenceImage.Orientation.REAR:
                    rear = cand_image
                    front = target_image
                else:
                    front = cand_image
                    rear = target_image

            canonical = rear.detected_plate or front.detected_plate or pair.registration_number_detected

            updated_pair = association.link_pair_manually(
                front_image=front,
                rear_image=rear,
                canonical_plate=canonical,
                target_pair=pair,
            )

            self.notify(f"Linked {selected_candidate['filename']} to {updated_pair.registration_number_detected}!")
            self.log_message(
                f"[bold green]Linked pair:[/bold green] {updated_pair.registration_number_detected} "
                f"← Front [{front.id.hex[:6]}] + Rear [{rear.id.hex[:6]}] "
                f"(Score: {selected_candidate['score']}, {selected_candidate['time_diff_display']})",
                level="SUCCESS",
            )
            self.reload_data()

        self.push_screen(PhotoLinkerModal(pair, target_image), on_link_selected)

    def action_view_evidence(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        active_tab = tabs.active

        if active_tab == "tab-itms":
            from core.tui.itms_pane import ITMSConnectionPane
            itms_pane = self.query_one("#itms-connection-pane", ITMSConnectionPane)
            itms_pane.action_view_photos()
            return

        if active_tab == "tab-batches":
            img = self._get_active_batch_image()
            if not img:
                self.notify("No image selected in the batch. Use ↑/↓ to choose an image.", severity="warning")
                return

            abs_path = os.path.join(settings.MEDIA_ROOT, img.vault_file)
            if not os.path.isfile(abs_path):
                self.notify(f"Image not found in vault: {img.vault_file}", severity="error")
                return

            label_plate = img.detected_plate or "NO PLATE"
            label_orient = img.orientation or "UNKNOWN"
            try:
                viewer.show_single_image(
                    abs_path,
                    bbox=img.bbox,
                    window_title=f"ITMS Vision - {label_plate} ({label_orient})",
                    plate_label=img.detected_plate or "",
                    orient_label=img.orientation or "",
                )
                self.notify(f"Opened image: {label_plate} ({label_orient})")
                self.log_message(
                    f"Opened evidence photo for image {str(img.id)[:8]} (Plate: {img.detected_plate or 'None'}, Orient: {img.orientation}, BBox: {img.bbox})",
                    level="INFO",
                )
            except Exception as exc:
                self.notify(f"Error launching viewer: {exc}", severity="error")
                self.log_message(f"Failed to launch viewer: {exc}", level="ERROR")
            return

        pair = self._get_active_pair("table-queue") or self._get_active_pair("table-history")
        if not pair:
            self.notify("Select a pair to view comparison.", severity="warning")
            return

        try:
            opened = viewer.show_pair_evidence(pair)
            if opened:
                has_both = bool(
                    pair.front_image and pair.rear_image and
                    os.path.isfile(os.path.join(settings.MEDIA_ROOT, pair.front_image.vault_file)) and
                    os.path.isfile(os.path.join(settings.MEDIA_ROOT, pair.rear_image.vault_file))
                )
                mode = "Side-by-side comparison" if has_both else "Single evidence photo"
                self.notify(f"{mode} opened for {pair.registration_number_detected}")
                self.log_message(f"{mode} viewer opened for {pair.registration_number_detected}", level="INFO")
            else:
                self.notify("No photographic evidence files found on disk for this pair.", severity="warning")
        except Exception as exc:
            self.notify(f"Error launching viewer: {exc}", severity="error")
            self.log_message(f"Failed to launch viewer: {exc}", level="ERROR")

    @work(thread=True)
    def action_joint_rescan(self) -> None:
        """Runs Dual-Stream Joint Vision on the currently selected pair."""
        pair = self._get_active_pair("table-queue") or self._get_active_pair("table-history")
        if not pair:
            self.call_from_thread(self.notify, "Select a pair in the table to re-scan with Joint Vision.", severity="warning")
            return
        if not (pair.front_image and pair.rear_image):
            self.call_from_thread(self.notify, f"Pair {pair.registration_number_detected} requires both front and rear photos for joint vision.", severity="warning")
            return

        self.call_from_thread(
            self.log_message,
            f"Running Dual-Stream Joint Vision on Pair #{pair.id} ({pair.registration_number_detected})...",
            level="VISION",
        )
        from core.vision.joint_pipeline import DualStreamVisionEngine
        engine = DualStreamVisionEngine()
        res = engine.process_pair(pair)
        lvl = "SUCCESS" if res.success else "WARNING"
        self.call_from_thread(
            self.log_message,
            f"Joint Vision [{res.reconciliation_status}]: Plate={res.plate_number} Cat={res.vehicle_category} (Conf: {res.consensus_conf:.2f})",
            level=lvl,
        )
        for detail in res.details:
            self.call_from_thread(self.log_message, f"  → {detail}", level="INFO")
        self.call_from_thread(self.notify, f"Joint Vision complete for {res.plate_number or pair.registration_number_detected} [{res.reconciliation_status}]")
        self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_process_vision(self) -> None:
        """Runs the vision pipeline in a background thread and streams progress to the bottom log."""
        if getattr(self, "_vision_running", False):
            self.call_from_thread(self.notify, "Vision pipeline is already running in background.", severity="warning")
            return
        self._vision_running = True
        batch_filter = self._selected_batch_id

        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="VISION")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        if batch_filter:
            self.call_from_thread(
                self.log_message,
                f"Starting vision pipeline for batch: {batch_filter}...",
                level="VISION",
            )
            cmd_args = ["process_vision", f"--batch={batch_filter}", "--include-needs-review", "--reprocess-failed"]
        else:
            self.call_from_thread(
                self.log_message,
                "Starting vision pipeline over pending unsubmitted images...",
                level="VISION",
            )
            cmd_args = ["process_vision", "--include-needs-review", "--reprocess-failed"]

        try:
            call_command(*cmd_args, stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(
                self.log_message,
                "Vision pipeline batch execution complete.",
                level="SUCCESS",
            )
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Vision pipeline execution failed: {exc}", level="ERROR")
        finally:
            self._vision_running = False
            self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_match_pairs(self) -> None:
        """Runs associate_pairs in background and logs results."""
        if getattr(self, "_matcher_running", False):
            self.call_from_thread(self.notify, "Pair grouping and matching is already running in background.", severity="warning")
            return
        self._matcher_running = True
        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="MATCHER")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        self.call_from_thread(self.log_message, "Running pair grouping and order matching...", level="MATCHER")
        try:
            call_command("associate_pairs", stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(self.log_message, "Physical pair matching completed. Press [P] to run Joint Dual-Stream Vision on formed pairs.", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Pair matching failed: {exc}", level="ERROR")
        finally:
            self._matcher_running = False
            self.call_from_thread(self.reload_data)

    def action_submit_pair(self) -> None:
        """Opens confirmation modal to review plate and photos, then submits pair to ITMS."""
        pair = self._get_active_pair()
        if not pair:
            self.notify("No pair selected for submission.", severity="warning")
            return

        if not (pair.front_image and pair.rear_image):
            self.notify(f"Pair {pair.registration_number_detected} is missing photos. Need both front and rear.", severity="warning")
            return

        if not pair.order:
            self.notify(f"Pair {pair.registration_number_detected} has no matched order. Press [T] to link.", severity="warning")
            return

        if pair.verification_status not in (
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.FAILED,
        ):
            self.notify(f"Pair {pair.registration_number_detected} is {pair.verification_status}. Press [A] to approve or [T] to type/link.", severity="warning")
            return

        from core.services import config_service
        from core.tui.dialogs import SingleOrderSubmissionModal

        def on_confirmed(res):
            if not res or not res.get("confirmed"):
                self.log_message(f"Submission cancelled for {pair.registration_number_detected}.", level="INFO")
                return

            if pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED:
                from core.models import InstallationOrder
                pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
                pair.save(update_fields=["verification_status"])
                if pair.order and pair.order.status == InstallationOrder.Status.FAILED:
                    pair.order.status = InstallationOrder.Status.PENDING
                    pair.order.save(update_fields=["status"])
                SubmissionAuditLog.objects.create(
                    pair=pair,
                    action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
                    result=SubmissionAuditLog.ResultStatus.INFO,
                    message="Order automatically re-approved for direct submission retry.",
                )

            configured_dry = config_service.get_setting("submission.dry_run_mode", True)
            dry_run = res.get("dry_run", configured_dry)
            self._execute_single_submission(pair, dry_run=dry_run)

        self.push_screen(SingleOrderSubmissionModal(pair), on_confirmed)

    @work(thread=True)
    def _execute_single_submission(self, pair: VehicleInstallationPair, dry_run: Optional[bool] = None) -> None:
        if getattr(self, "_submission_running", False):
            self.call_from_thread(self.notify, "A submission is already active in background.", severity="warning")
            return
        self._submission_running = True
        import time
        from core.services import config_service
        if dry_run is None:
            dry_run = config_service.get_setting("submission.dry_run_mode", True)
        submit_step3 = config_service.get_setting("submission.submit_step3", True)
        mode_str = "[DRY-RUN]" if dry_run else "[LIVE]"
        try:
            # Pre-flight reachability check for live submissions
            if not dry_run:
                from core.services.itms_web_client import get_web_client
                client = get_web_client()
                probe = client.test_connection()
                if not probe.get("success"):
                    err = probe.get("error", "Network unreachable")
                    self.call_from_thread(
                        self.log_message,
                        f"[bold red]✗ Offline / Reachability Error:[/bold red] Cannot reach {client.base_url} ({err}). Submission halted.",
                        level="ERROR",
                    )
                    self.call_from_thread(
                        self.notify,
                        f"No internet / ITMS unreachable: {err}",
                        severity="error",
                    )
                    return

            self.call_from_thread(
                self.log_message,
                f"{mode_str} Submitting {pair.registration_number_detected} (Order #{pair.order.order_number if pair.order else '—'}) to ITMS...",
                level="ITMS",
            )
            outcome = submit_pair(pair, backend="web", dry_run=dry_run, submit_step3=submit_step3)
            if outcome.success:
                self.call_from_thread(
                    self.log_message,
                    f"[bold green]✓ {mode_str} Successfully submitted {pair.registration_number_detected} to ITMS![/bold green] Token: {outcome.token}",
                    level="SUCCESS",
                )
                self.call_from_thread(
                    self.notify,
                    f"Submitted {pair.registration_number_detected} to ITMS! ({mode_str})",
                    severity="information",
                )
            else:
                self.call_from_thread(
                    self.log_message,
                    f"[bold red]✗ Submission failed for {pair.registration_number_detected}:[/bold red] {outcome.error}",
                    level="ERROR",
                )
                self.call_from_thread(
                    self.notify,
                    f"Submission failed: {outcome.error}",
                    severity="error",
                )
        finally:
            self._submission_running = False
            self.call_from_thread(self.reload_data)

    def action_batch_submit(self) -> None:
        """Opens batch submission modal to review all approved orders (e.g. 200 orders) and submit at once."""
        from core.services.itms_web_client import get_current_itms_account
        active_acc = get_current_itms_account()

        qs = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        ).select_related("order", "front_image", "rear_image")

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

        approved_pairs = list(qs)

        if not approved_pairs:
            self.notify("No APPROVED orders ready for submission. Approve orders with [A] or [T] first.", severity="warning")
            return

        from core.services import config_service
        from core.tui.dialogs import BatchSubmissionModal

        def on_batch_confirmed(res):
            if not res or not res.get("confirmed"):
                self.log_message("Batch submission cancelled by operator.", level="INFO")
                return

            configured_dry = config_service.get_setting("submission.dry_run_mode", True)
            dry_run = res.get("dry_run", configured_dry)
            pairs_to_submit = res.get("pairs", approved_pairs)
            self._execute_batch_submission(pairs_to_submit, dry_run=dry_run)

        self.push_screen(BatchSubmissionModal(approved_pairs), on_batch_confirmed)

    @work(thread=True)
    def _execute_batch_submission(self, pairs: List[VehicleInstallationPair], dry_run: Optional[bool] = None) -> None:
        if getattr(self, "_submission_running", False):
            self.call_from_thread(self.notify, "A submission is already active in background.", severity="warning")
            return
        self._submission_running = True
        import time
        from core.services import config_service
        if dry_run is None:
            dry_run = config_service.get_setting("submission.dry_run_mode", True)
        submit_step3 = config_service.get_setting("submission.submit_step3", True)
        from core.services.submission_worker import submit_pair
        from core.tui.dialogs import BatchProgressModal
        mode_str = "[DRY-RUN]" if dry_run else "[LIVE]"
        total = len(pairs)
        total_photos = total * 2

        progress_modal = BatchProgressModal(total_orders=total, dry_run=dry_run)
        self.call_from_thread(self.push_screen, progress_modal)

        self.call_from_thread(
            self.log_message,
            f"[bold cyan]═══ Starting Batch ITMS Submission ({total} orders / ~{total_photos} photos) {mode_str} ═══[/bold cyan]",
            level="ITMS",
        )
        def modal_log(msg: str):
            self.call_from_thread(progress_modal.log_event, msg)

        modal_log(f"[bold cyan]═══ Batch queue initialized with {total} orders ({mode_str}) ═══[/bold cyan]")

        try:
            # 1. Pre-Flight Connectivity Check (Verify Internet & ITMS reachability before touching queue)
            if not dry_run:
                modal_log("🌐 Pre-flight network check: Verifying internet & ITMS reachability...")
                from core.services.itms_web_client import get_web_client
                self.call_from_thread(
                    self.log_message,
                    "Pre-flight network check: Verifying internet connection and ITMS server reachability...",
                    level="INFO",
                )
                client = get_web_client()
                probe = client.test_connection()
                if not probe.get("success"):
                    err = probe.get("error", "Network offline / host unreachable")
                    modal_log(f"[bold red]❌ Pre-flight check failed: {err}[/bold red]")
                    self.call_from_thread(
                        self.log_message,
                        f"[bold red]✗ Pre-Flight Check Failed:[/bold red] Cannot connect to {client.base_url} ({err}). "
                        f"Batch aborted to protect all {total} orders from being wrongly marked failed.",
                        level="ERROR",
                    )
                    self.call_from_thread(
                        self.notify,
                        f"Offline: Cannot reach ITMS ({err}). Connect to internet and retry.",
                        severity="error",
                    )
                    self.call_from_thread(
                        progress_modal.finish_batch,
                        0,
                        total,
                        f"Pre-flight network check failed: {err}",
                    )
                    return

                modal_log(f"[bold green]✓ Network online:[/bold green] Connected to {client.base_url} ({probe.get('latency_ms', 0)}ms)")
                self.call_from_thread(
                    self.log_message,
                    f"[bold green]✓ Network Online:[/bold green] Connected to {client.base_url} ({probe.get('latency_ms', 0)}ms). Starting batch queue...",
                    level="SUCCESS",
                )

            success_count = 0
            fail_count = 0
            consecutive_network_failures = 0
            halted_early = False

            # 2. Sequential Order-by-Order Submission Loop
            for idx, pair in enumerate(pairs, 1):
                # Operator cancellation check
                if getattr(progress_modal, "is_stopped", False):
                    halted_early = True
                    modal_log(f"[bold yellow]⚠️ Batch halted by operator at order [{idx}/{total}]. Remaining orders preserved.[/bold yellow]")
                    self.call_from_thread(
                        self.log_message,
                        f"[bold yellow]Batch halted by operator at order [{idx}/{total}]. Remaining orders preserved.[/bold yellow]",
                        level="WARNING",
                    )
                    break

                # Operator pause loop
                while getattr(progress_modal, "is_paused", False):
                    if getattr(progress_modal, "is_stopped", False):
                        break
                    time.sleep(0.2)

                plate = pair.registration_number_detected
                order_num = pair.order.order_number if pair.order else "NO_ORDER"
                modal_log(f"📦 [bold cyan][{idx}/{total}][/bold cyan] Starting order #{order_num} ({plate}) {mode_str}...")
                self.call_from_thread(
                    progress_modal.update_progress,
                    idx,
                    plate,
                    order_num,
                    f"Submitting 3-step wizard to ITMS ({mode_str})...",
                    success_count,
                    fail_count,
                )
                self.call_from_thread(
                    self.log_message,
                    f"[{idx}/{total}] Processing {plate} (Order #{order_num})...",
                    level="ITMS",
                )

                outcome = submit_pair(
                    pair,
                    backend="web",
                    dry_run=dry_run,
                    submit_step3=submit_step3,
                    log_callback=modal_log,
                )
                if outcome.success:
                    success_count += 1
                    consecutive_network_failures = 0
                    modal_log(f"  ✅ [bold green][SUCCESS][/bold green] {plate} (Order #{order_num}) finalized {mode_str}")
                    self.call_from_thread(
                        progress_modal.update_progress,
                        idx,
                        plate,
                        order_num,
                        f"[bold green]✓ Successfully submitted {plate} {mode_str}[/bold green]",
                        success_count,
                        fail_count,
                    )
                    self.call_from_thread(
                        self.log_message,
                        f"[{idx}/{total}] [bold green]✓ Success:[/bold green] {plate} (Order #{order_num}) submitted {mode_str}.",
                        level="SUCCESS",
                    )
                else:
                    fail_count += 1
                    err_text = str(outcome.error).lower()
                    is_net_err = any(k in err_text for k in ("connection", "timeout", "10054", "offline", "unreachable", "getaddrinfo", "host"))
                    if is_net_err:
                        consecutive_network_failures += 1
                        modal_log(f"  ⚡ [bold magenta][OFFLINE OUTBOX][/bold magenta] {plate}: Network drop -> Queued for auto-sync")
                    else:
                        consecutive_network_failures = 0
                        modal_log(f"  ❌ [bold red][FAILED][/bold red] {plate}: {outcome.error}")

                    self.call_from_thread(
                        progress_modal.update_progress,
                        idx,
                        plate,
                        order_num,
                        f"[bold red]✗ Failed: {outcome.error}[/bold red]",
                        success_count,
                        fail_count,
                    )
                    self.call_from_thread(
                        self.log_message,
                        f"[{idx}/{total}] [bold red]✗ Failed:[/bold red] {plate}: {outcome.error}",
                        level="ERROR",
                    )

                    # 3. Circuit Breaker: Halt if 3 consecutive orders fail from network loss
                    if not dry_run and consecutive_network_failures >= 3:
                        remaining = total - idx
                        halted_early = True
                        self.call_from_thread(
                            self.log_message,
                            f"[bold red]⛔ Circuit Breaker Activated:[/bold red] Detected 3 consecutive network dropouts. "
                            f"Halting batch at order [{idx}/{total}]. The remaining {remaining} orders remain safely in APPROVED status. "
                            f"Check your internet connection and resume when reconnected.",
                            level="ERROR",
                        )
                        self.call_from_thread(
                            self.notify,
                            f"Batch halted at [{idx}/{total}] due to lost internet connection. {remaining} orders preserved.",
                            severity="error",
                        )
                        break

                # Gentle pacing between orders for live uploads (avoids socket congestion & server rate limits)
                if not dry_run and idx < total:
                    time.sleep(0.4)

            status_header = "═══ Batch Submission Suspended ═══" if halted_early else "═══ Batch Submission Complete ═══"
            summary_msg = f"{status_header}: {success_count} succeeded, {fail_count} failed."
            self.call_from_thread(progress_modal.finish_batch, success_count, fail_count, summary_msg)

            self.call_from_thread(
                self.log_message,
                f"[bold green]{status_header}[/bold green]\n"
                f"• Total Queue: {total} orders (~{total_photos} photos)\n"
                f"• Succeeded: [green]{success_count}[/green]\n"
                f"• Failed: [red]{fail_count}[/red]\n"
                f"• Remaining: {total - success_count - fail_count}\n"
                f"• Mode: {mode_str}",
                level="SUCCESS" if fail_count == 0 else "WARNING",
            )
            self.call_from_thread(
                self.notify,
                f"Batch: {success_count}/{total} submitted ({fail_count} failed).",
                severity="information" if fail_count == 0 else "warning",
            )

            # Auto-optimize SQLite WAL database health after large batch write storm
            try:
                from core.services.maintenance_service import optimize_database
                db_opt = optimize_database()
                if db_opt.get("optimized"):
                    self.call_from_thread(
                        self.log_message,
                        f"[dim]DB Maintenance: {db_opt.get('message')}[/dim]",
                        level="INFO",
                    )
            except Exception:
                pass
        finally:
            self._submission_running = False
            self.call_from_thread(self.reload_data)

    def action_export_shift_report(self) -> None:
        """Exports end-of-shift verification and installation handover report to CSV."""
        from core.services.export_service import export_shift_report
        try:
            res = export_shift_report()
            count = res.get("row_count", 0)
            fname = res.get("filename", "shift_report.csv")
            self.log_message(
                f"[bold green]✓ Shift Handover Report Exported:[/bold green] {count} records saved to [cyan]exports/{fname}[/cyan]",
                level="SUCCESS",
            )
            self.notify(f"Shift Report: {count} records exported to exports/{fname}", severity="information")
        except Exception as exc:
            self.log_message(f"Export failed: {exc}", level="ERROR")
            self.notify(f"Export failed: {exc}", severity="error")

    @work(thread=True)
    def action_clean_storage(self) -> None:
        """Runs crop cleanup, vault lifecycle pruning, and database WAL optimization in background."""
        if getattr(self, "_clean_running", False):
            self.call_from_thread(self.notify, "Storage cleanup is already running.", severity="warning")
            return
        self._clean_running = True
        self.call_from_thread(self.log_message, "Executing storage lifecycle cleanup and database optimization...", level="INFO")
        try:
            from core.services.maintenance_service import clean_storage_lifecycle
            res = clean_storage_lifecycle(max_crop_age_days=7, max_export_age_days=30, optimize_db=True)
            self.call_from_thread(
                self.log_message,
                f"[bold green]✓ Storage Cleaned:[/bold green] Removed {res['deleted_count']} stale files ({res['mb_freed']} MB freed).",
                level="SUCCESS",
            )
            db_res = res.get("db_optimization", {})
            if db_res.get("optimized"):
                self.call_from_thread(
                    self.log_message,
                    f"[bold green]✓ Database Optimized:[/bold green] {db_res.get('message')}",
                    level="SUCCESS",
                )
            self.call_from_thread(
                self.notify,
                f"Storage cleaned: {res['mb_freed']} MB freed.",
                severity="information",
            )
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Storage maintenance error: {exc}", level="ERROR")
        finally:
            self._clean_running = False
            self.call_from_thread(self.reload_data)

    def action_quick_type_plate(self) -> None:
        """Opens interactive Quick Plate & Order Matcher modal [T] to type plate or link order."""
        pair = self._get_active_pair()
        if not pair:
            self.notify("Select a pair to enter plate.", severity="warning")
            return

        from core.tui.dialogs import PlateQuickEntryModal

        def on_completed(result):
            if not result or not result.get("success"):
                self.log_message("Manual plate assignment cancelled.", level="INFO")
                return

            plate = result["plate"]
            order = result.get("order")
            order_tag = f" (Order #{order.order_number})" if order else ""
            self.notify(f"Approved {plate}! Ready for submission [U]")
            self.log_message(
                f"[bold green]✓ Fast-Path Plate Assigned:[/bold green] {plate}{order_tag} → Set to APPROVED",
                level="SUCCESS",
            )
            self.reload_data()

        self.push_screen(PlateQuickEntryModal(pair), on_completed)

    def action_retry_failed(self) -> None:
        """Resets all FAILED orders to APPROVED status so they can be resubmitted in batch."""
        from core.services.itms_web_client import get_current_itms_account
        from core.models import InstallationOrder
        active_acc = get_current_itms_account()

        qs = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.FAILED,
            is_complete=True,
            order__isnull=False,
        ).select_related("order", "front_image", "rear_image")

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

        failed_pairs = list(qs)
        if not failed_pairs:
            self.notify("No failed orders found eligible for retry.", severity="information")
            return

        op_name = self.current_user.username if getattr(self, "current_user", None) else "Operator"
        count = len(failed_pairs)

        for p in failed_pairs:
            p.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
            p.save(update_fields=["verification_status"])
            if p.order and p.order.status == InstallationOrder.Status.FAILED:
                p.order.status = InstallationOrder.Status.PENDING
                p.order.save(update_fields=["status"])
            SubmissionAuditLog.objects.create(
                pair=p,
                action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
                result=SubmissionAuditLog.ResultStatus.INFO,
                message=f"Order reset from FAILED to APPROVED for batch retry by {op_name}.",
            )

        self.log_message(
            f"[bold green]✓ Re-approved {count} failed order(s) for submission retry by {op_name}.[/bold green]",
            level="SUCCESS",
        )
        self.notify(f"Reset {count} failed order(s) to APPROVED! Opening batch confirmation...", severity="success")
        self.reload_data()
        self.action_batch_submit()

    @work(thread=True)
    def action_sync_itms_orders(self) -> None:
        """Synchronizes active ITMS installation orders with PostgreSQL / local DB with rate-limit protection."""
        if getattr(self, "_order_sync_running", False):
            self.call_from_thread(self.notify, "Order sync is already in progress.", severity="warning")
            return
        self._order_sync_running = True
        from core.services.order_sync import OrderSyncService
        self.call_from_thread(self.log_message, "Checking active ITMS orders (GET /installation-orders/index)...", level="ITMS")

        try:
            svc = OrderSyncService()
            res = svc.sync_active_orders(force=False)

            if not res.get("success"):
                err = res.get("error", "Sync failed.")
                self.call_from_thread(self.log_message, f"ITMS Order Sync failed: {err}", level="ERROR")
                self.call_from_thread(self.notify, f"Sync Error: {err}", severity="error")
                return

            if res.get("from_cache"):
                self.call_from_thread(
                    self.log_message,
                    f"[yellow]{res['message']}[/yellow]",
                    level="ITMS",
                )
                self.call_from_thread(self.notify, res["message"], severity="information")
            else:
                self.call_from_thread(
                    self.log_message,
                    f"[bold green]✓ ITMS Orders Synced:[/bold green] {res['message']}",
                    level="SUCCESS",
                )
                self.call_from_thread(self.notify, f"Synced {res['total_active_seen']} active orders.", severity="information")
        finally:
            self._order_sync_running = False
            self.call_from_thread(self.reload_data)
