import os
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
    def action_tab_queue(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-queue"

    def action_tab_history(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-history"

    def action_tab_batches(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-batches"

    @work(thread=True)
    def action_native_ingest(self) -> None:
        """Opens native desktop dialog to pick files or folders, and ingests them into a new batch."""
        self.call_from_thread(
            self.log_message,
            "Opening native photo picker dialog (select files or folder)...",
            level="INFO",
        )
        selected_paths = file_dialog.prompt_native_photo_selection()
        if not selected_paths:
            self.call_from_thread(self.log_message, "Photo selection cancelled by operator.", level="INFO")
            return

        total = len(selected_paths)
        self.call_from_thread(
            self.log_message,
            f"Selected {total} photo(s). Initializing ingestion batch...",
            level="INFO",
        )

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label=f"Native Dialog ({total} photos)",
        )

        ingested = 0
        skipped = 0
        failed = 0

        for p in selected_paths:
            img, status = vault_service.ingest_from_disk(p, batch=batch)
            base_name = os.path.basename(p)
            if status == "INGESTED":
                ingested += 1
                self.call_from_thread(
                    self.log_message,
                    f"Ingested {base_name} -> {img.vault_file}",
                    level="INFO",
                )
            elif status == "DUPLICATE_SKIPPED":
                skipped += 1
                self.call_from_thread(
                    self.log_message,
                    f"Duplicate skipped: {base_name} (already in vault)",
                    level="WARNING",
                )
            else:
                failed += 1
                self.call_from_thread(
                    self.log_message,
                    f"Failed to ingest {base_name} ({status})",
                    level="ERROR",
                )

        batch.refresh_from_db()
        self.call_from_thread(
            self.log_message,
            f"Batch {batch.batch_id} complete: {ingested} ingested, {skipped} duplicates skipped, {failed} failed.",
            level="SUCCESS",
        )
        self.call_from_thread(
            self.log_message,
            "Tip: Press [b yellow]P[/b yellow] to run vision recognition on newly added photos.",
            level="INFO",
        )
        self.call_from_thread(self.notify, f"Batch {batch.batch_id}: {ingested} photos ingested!")
        self.call_from_thread(self.reload_data)

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
        self.history_filter_index = (self.history_filter_index + 1) % len(HISTORY_FILTERS)
        self.current_history_filter = HISTORY_FILTERS[self.history_filter_index]
        self._update_history_filter_bar()
        self._reload_history_table()
        self.log_message(f"History filter switched to: [b]{self.current_history_filter}[/b]")

    def action_refresh(self):
        self.reload_data()
        self.log_message("Dashboard refreshed from database.", level="INFO")

    def action_approve(self):
        pair = self._get_active_pair("table-queue")
        if not pair:
            self.notify("No pair selected to approve.", severity="warning")
            return
        if not pair.is_complete or not pair.order:
            self.notify("Cannot approve: pair is incomplete or has no matched order.", severity="error")
            self.log_message(f"Cannot approve {pair.registration_number_detected}: missing order or incomplete evidence.", level="WARNING")
            return

        op_name = self.current_user.username if getattr(self, "current_user", None) else "Operator"
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])
        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Pair approved by operator '{op_name}' in TUI.",
        )
        self.notify(f"Approved {pair.registration_number_detected} for submission.")
        self.log_message(f"Approved {pair.registration_number_detected} by {op_name} (Order: {pair.order.order_number})", level="SUCCESS")
        self.reload_data()

    def action_swap(self):
        pair = self._get_active_pair("table-queue")
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
        """Signs out of current operator session and returns to landing portal."""
        from core.services import auth_service
        from core.tui.auth_screens import LandingAuthScreen

        auth_service.clear_remembered_session()
        old_user = getattr(self, "current_user", None)
        self.current_user = None
        self.notify("Signed out. Returning to landing portal...")
        self.log_message(f"Operator '{old_user.username if old_user else 'Guest'}' signed out.", level="INFO")
        self.push_screen(LandingAuthScreen(), self._on_auth_completed)

    def action_link_pair(self):
        """Opens interactive Closest Photo Picker modal to link front/rear photos."""
        pair = self._get_active_pair("table-queue")
        if not pair:
            self.notify("Select a pair in Review Queue to link photos.", severity="warning")
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
    def action_process_vision(self) -> None:
        """Runs the vision pipeline in a background thread and streams progress to the bottom log."""
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
            self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_match_pairs(self) -> None:
        """Runs associate_pairs in background and logs results."""
        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="MATCHER")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        self.call_from_thread(self.log_message, "Running pair grouping and order matching...", level="MATCHER")
        try:
            call_command("associate_pairs", stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(self.log_message, "Pair matching completed.", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Pair matching failed: {exc}", level="ERROR")
        finally:
            self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_submit_pair(self) -> None:
        """Submits the currently selected pair (or all approved) to ITMS with live logging."""
        pair = self._get_active_pair("table-queue")
        if not pair:
            self.call_from_thread(self.notify, "No pair selected for submission.", severity="warning")
            return

        if pair.verification_status != VehicleInstallationPair.VerificationStatus.APPROVED:
            self.call_from_thread(
                self.log_message,
                f"Pair {pair.registration_number_detected} is {pair.verification_status}. Approve it with [A] first.",
                level="WARNING",
            )
            return

        self.call_from_thread(
            self.log_message,
            f"Initiating ITMS submission for {pair.registration_number_detected} (Order: {pair.order.order_number})...",
            level="ITMS",
        )

        outcome = submit_pair(pair)
        if outcome.success:
            self.call_from_thread(
                self.log_message,
                f"Successfully submitted {pair.registration_number_detected} to ITMS! Token: {outcome.token}",
                level="SUCCESS",
            )
        else:
            self.call_from_thread(
                self.log_message,
                f"Submission failed for {pair.registration_number_detected}: {outcome.error}",
                level="ERROR",
            )
        self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_clean_storage(self) -> None:
        """Runs crop cleanup and vault lifecycle pruning in background."""
        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="STORAGE")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        self.call_from_thread(self.log_message, "Executing temporary crop cleanup...", level="INFO")
        try:
            call_command("clean_crops", stdout=out_stream, stderr=err_stream)
            self.call_from_thread(self.log_message, "Enforcing 7-day vault retention lifecycle...", level="INFO")
            call_command("prune_vault", stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(self.log_message, "Storage maintenance completed.", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Storage maintenance error: {exc}", level="ERROR")
        finally:
            self.call_from_thread(self.reload_data)
