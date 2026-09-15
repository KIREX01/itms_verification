"""
Modal dialog screens for the ITMS Operator TUI.
"""
import os
from typing import Dict, List, Optional
from django.conf import settings
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, ProgressBar, RichLog, Static

from core.models import EvidenceImage, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.matcher.association import get_closest_candidates
from core.services import viewer
from core.vision import normalizer


class PhotoLinkerModal(ModalScreen[Optional[Dict]]):
    """
    Interactive modal dialog allowing operators to browse, inspect,
    and link candidate evidence photos to a pair.

    Candidates are ranked by:
      - Capture timestamp proximity (closest camera clock time)
      - Plate character similarity
      - Batch affinity
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("v", "preview_candidate", "Preview Photo"),
        Binding("enter", "confirm_selection", "Link Photo"),
        Binding("1", "quick_link(0)", "Pick #1", show=False),
        Binding("2", "quick_link(1)", "Pick #2", show=False),
        Binding("3", "quick_link(2)", "Pick #3", show=False),
        Binding("4", "quick_link(3)", "Pick #4", show=False),
        Binding("5", "quick_link(4)", "Pick #5", show=False),
        Binding("6", "quick_link(5)", "Pick #6", show=False),
        Binding("7", "quick_link(6)", "Pick #7", show=False),
        Binding("8", "quick_link(7)", "Pick #8", show=False),
        Binding("9", "quick_link(8)", "Pick #9", show=False),
    ]

    def __init__(
        self,
        pair: VehicleInstallationPair,
        target_image: EvidenceImage,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.pair = pair
        self.target_image = target_image
        self.candidates: List[Dict] = []

    def compose(self) -> ComposeResult:
        from core.services.camera_naming import parse_camera_filename

        t_img = self.target_image
        t_plate = t_img.detected_plate or "NO_PLATE"
        t_orient = t_img.orientation
        t_sig = parse_camera_filename(t_img.original_source_path or t_img.vault_file)
        t_time = (
            t_img.captured_at.strftime("%Y-%m-%d %H:%M:%S")
            if t_img.captured_at
            else t_img.ingested_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        t_file = os.path.basename(t_img.original_source_path or t_img.vault_file)

        header_lines = [
            f"[bold cyan]═══ Closest Photo Linker ═══[/bold cyan]",
            f"[b]Active Pair:[/b] [bold yellow]{self.pair.registration_number_detected}[/bold yellow]  │  "
            f"[b]Anchor Photo:[/b] [b]{t_file}[/b] ([bold green]{t_orient}[/bold green], Plate: {t_plate}, [cyan]{t_sig.display_tag}[/cyan])  │  "
            f"[b]Captured:[/b] {t_time}",
            "[dim]Select a candidate photo to link with this motorcycle. "
            "Press [b green]Enter[/b green] to link, [b cyan]V[/b cyan] to preview candidate photo, or [1-9] for quick select.[/dim]",
        ]

        with Vertical(id="modal-dialog"):
            yield Static("\n".join(header_lines), id="modal-header")
            yield DataTable(id="modal-table")
            with Horizontal(id="modal-footer"):
                yield Button("Preview Photo [V]", variant="default", id="btn-preview")
                yield Button("Link Candidate [Enter]", variant="primary", id="btn-link")
                yield Button("Cancel [Esc]", variant="error", id="btn-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#modal-table", DataTable)
        table.add_columns("#", "Filename", "Camera / Seq", "Plate", "Orient", "Time Delta", "Similarity", "Match Score")
        table.cursor_type = "row"

        # Load candidates
        self.candidates = get_closest_candidates(self.target_image, top_n=10)
        if not self.candidates:
            self.notify("No candidate photos found to pair with this image.", severity="warning")
            return

        for idx, cand in enumerate(self.candidates):
            table.add_row(
                f"#{idx + 1}",
                cand["filename"],
                cand.get("camera_tag", "—"),
                cand["plate"],
                cand["orientation"],
                cand["time_diff_display"],
                f"{cand['similarity_pct']}%",
                str(cand["score"]),
            )

        table.focus()

    def _get_selected_candidate(self) -> Optional[Dict]:
        table = self.query_one("#modal-table", DataTable)
        row_idx = table.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self.candidates):
            return self.candidates[row_idx]
        return None

    def action_preview_candidate(self) -> None:
        cand = self._get_selected_candidate()
        if not cand:
            self.notify("No candidate selected.", severity="warning")
            return

        img: EvidenceImage = cand["image"]
        abs_path = os.path.join(settings.MEDIA_ROOT, img.vault_file)
        if not os.path.isfile(abs_path):
            abs_path = img.original_source_path or ""

        if not os.path.isfile(abs_path):
            self.notify("Image file not found on disk.", severity="error")
            return

        try:
            viewer.show_single_image(
                abs_path,
                bbox=img.bbox,
                window_title=f"Candidate: {cand['plate']} ({cand['orientation']}) - {cand['filename']}",
                plate_label=cand["plate"],
                orient_label=cand["orientation"],
            )
            self.notify(f"Opened preview: {cand['filename']}")
        except Exception as exc:
            self.notify(f"Could not open image viewer: {exc}", severity="error")

    def action_confirm_selection(self) -> None:
        cand = self._get_selected_candidate()
        if cand:
            self.dismiss(cand)
        else:
            self.notify("No candidate selected to link.", severity="warning")

    def action_quick_link(self, idx: int) -> None:
        idx = int(idx)
        if 0 <= idx < len(self.candidates):
            self.dismiss(self.candidates[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self.candidates):
            self.dismiss(self.candidates[row_idx])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-link":
            self.action_confirm_selection()
        elif btn_id == "btn-preview":
            self.action_preview_candidate()
        elif btn_id == "btn-cancel":
            self.action_cancel()


class PlateQuickEntryModal(ModalScreen[Optional[Dict]]):
    """
    Fast-path interactive modal dialog allowing operators to:
    1. Inspect front and rear photos side-by-side (Press [V]).
    2. Type the plate number directly (e.g. UMA 835DS or UMA946DQ).
    3. Auto-complete plate numbers and order details from active ITMS orders (Press [Tab]).
    4. Automatically fetch hardware inventory (serials, tracker IDs) from ITMS if missing.
    5. Instantly mark the pair as APPROVED and ready for submission without waiting for vision.
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("v", "preview_pair", "Preview Photos"),
        Binding("tab", "autocomplete_plate", "Auto-complete Plate"),
        Binding("enter", "confirm_plate", "Apply Plate"),
    ]

    def __init__(self, pair: VehicleInstallationPair, **kwargs):
        super().__init__(**kwargs)
        self.pair = pair
        self.all_active_orders: List[InstallationOrder] = []
        self._filtered_orders: List[InstallationOrder] = []
        self._selected_order_override: Optional[InstallationOrder] = None
        self._updating_from_table: bool = False

    def compose(self) -> ComposeResult:
        p = self.pair
        f_file = os.path.basename(p.front_image.vault_file) if p.front_image else "None"
        r_file = os.path.basename(p.rear_image.vault_file) if p.rear_image else "None"
        current_tag = p.registration_number_detected or "NO_PLATE"

        header_lines = [
            "[bold cyan]═══ Quick Plate & Order Matcher ═══[/bold cyan]",
            f"[b]Pair ID:[/b] [bold yellow]{str(p.id)[:8]}[/bold yellow]  │  "
            f"[b]Current Tag:[/b] [bold white]{current_tag}[/bold white]  │  "
            f"[b]Front:[/b] [green]{f_file}[/green]  │  [b]Rear:[/b] [green]{r_file}[/green]",
            "[dim]Type the plate number below, or select from active ITMS orders. "
            "Press [bold green]Enter[/bold green] to apply & approve, [bold cyan]Tab[/bold cyan] to auto-complete, [bold cyan]V[/bold cyan] to preview photos side-by-side, or [bold red]Esc[/bold red] to cancel.[/dim]",
        ]

        with Vertical(id="modal-dialog", classes="plate-entry-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            yield Input(
                placeholder="Type plate (e.g. UMA 946DQ) or search Order # / VIN...",
                value="" if current_tag.startswith("PAIR-") or current_tag.startswith("MISSING-") else current_tag,
                id="input-plate",
            )
            yield Static("[bold white]Active ITMS Orders (Live Filtered):[/bold white]", id="active-orders-label")
            yield DataTable(id="table-active-orders")
            with Horizontal(id="modal-footer"):
                yield Button("Preview Photos [V]", variant="default", id="btn-preview-photos")
                yield Button("Auto-complete [Tab]", variant="default", id="btn-autocomplete")
                yield Button("Apply Plate & Approve [Enter]", variant="primary", id="btn-apply-plate")
                yield Button("Cancel [Esc]", variant="error", id="btn-cancel-plate")

    def on_mount(self) -> None:
        table = self.query_one("#table-active-orders", DataTable)
        table.add_columns("Order #", "Registration Plate", "VIN", "Stage / Status", "Warehouse")
        table.cursor_type = "row"

        # Load active / non-archived orders from DB
        self.all_active_orders = list(
            InstallationOrder.objects.filter(
                is_archived=False
            ).order_by("-updated_at")[:500]
        )
        if not self.all_active_orders:
            self.all_active_orders = list(
                InstallationOrder.objects.all().order_by("-updated_at")[:500]
            )

        self._filtered_orders = list(self.all_active_orders)
        self._populate_orders_table()

        # Focus input for immediate typing
        input_widget = self.query_one("#input-plate", Input)
        input_widget.focus()

        # If input has an initial value, filter immediately
        init_val = input_widget.value.strip()
        if init_val:
            self._filter_orders_for_query(init_val)

    def _populate_orders_table(self) -> None:
        try:
            table = self.query_one("#table-active-orders", DataTable)
        except Exception:
            return
        table.clear()
        for idx, o in enumerate(self._filtered_orders):
            stage_str = o.itms_stage or o.order_status or "Pending"
            table.add_row(
                o.order_number,
                f"[bold green]{o.registration_number}[/bold green]",
                o.vin or "—",
                stage_str,
                o.warehouse_name[:25] if o.warehouse_name else "—",
                key=str(idx),
            )
        if self._filtered_orders and len(self._filtered_orders) > 0:
            try:
                table.move_cursor(row=0)
            except Exception:
                pass
        self._update_orders_label()

    def _update_orders_label(self) -> None:
        try:
            label = self.query_one("#active-orders-label", Static)
        except Exception:
            return
        if self._filtered_orders:
            top = self._filtered_orders[0]
            stage_str = top.itms_stage or top.order_status or "Active"
            label.update(
                f"[bold white]Active ITMS Orders:[/bold white] "
                f"[bold green]Top Match → {top.registration_number}[/bold green] "
                f"[cyan]({top.order_number} │ {stage_str})[/cyan] "
                f"[dim]— Press [bold green]Enter[/bold green] or [bold cyan]Tab[/bold cyan] to select[/dim]"
            )
        else:
            label.update(
                "[bold yellow]Active ITMS Orders (0 local matches — will query ITMS live on Enter):[/bold yellow]"
            )

    def _filter_orders_for_query(self, query: str) -> None:
        q = (query or "").strip().lower()
        if not q:
            self._filtered_orders = list(self.all_active_orders)
        else:
            clean_q = normalizer.canonicalize(q).lower() or q
            self._filtered_orders = [
                o for o in self.all_active_orders
                if q in o.registration_number.lower()
                or clean_q in normalizer.canonicalize(o.registration_number).lower()
                or q in o.order_number.lower()
                or q in (o.vin or "").lower()
            ]
            # If not in active memory list, query the local database directly
            if not self._filtered_orders:
                from django.db import models
                db_matches = list(
                    InstallationOrder.objects.filter(
                        models.Q(registration_number__icontains=clean_q) |
                        models.Q(registration_number__icontains=q) |
                        models.Q(order_number__icontains=clean_q) |
                        models.Q(order_number__icontains=q) |
                        models.Q(vin__icontains=q)
                    )[:50]
                )
                if db_matches:
                    self._filtered_orders = db_matches
                    for m in db_matches:
                        if m not in self.all_active_orders:
                            self.all_active_orders.append(m)
        self._populate_orders_table()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._updating_from_table:
            return
        self._selected_order_override = None
        self._filter_orders_for_query(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_confirm_plate()

    def action_autocomplete_plate(self) -> None:
        if self._filtered_orders:
            top = self._filtered_orders[0]
            self._selected_order_override = top
            input_widget = self.query_one("#input-plate", Input)
            self._updating_from_table = True
            input_widget.value = top.registration_number
            self._updating_from_table = False
            self.notify(f"Auto-completed plate: {top.registration_number} (Order #{top.order_number})")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#table-active-orders", DataTable)
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self._filtered_orders):
            selected_order = self._filtered_orders[row_idx]
            self._selected_order_override = selected_order
            if table.has_focus:
                input_widget = self.query_one("#input-plate", Input)
                self._updating_from_table = True
                input_widget.value = selected_order.registration_number
                self._updating_from_table = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self._filtered_orders):
            self._selected_order_override = self._filtered_orders[row_idx]
            input_widget = self.query_one("#input-plate", Input)
            input_widget.value = self._selected_order_override.registration_number
            self.action_confirm_plate()

    def action_preview_pair(self) -> None:
        p = self.pair
        try:
            opened = viewer.show_pair_evidence(p)
            if not opened:
                front_path = os.path.join(settings.MEDIA_ROOT, p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
                rear_path = os.path.join(settings.MEDIA_ROOT, p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None
                opened = viewer.show_evidence_pair(
                    front_path=front_path if front_path and os.path.isfile(front_path) else None,
                    rear_path=rear_path if rear_path and os.path.isfile(rear_path) else None,
                    pair_id=str(p.id)[:8],
                    order_number=p.order.order_number if p.order else "—",
                    plate=p.registration_number_detected,
                    status=p.verification_status,
                    front_bbox=p.front_image.bbox if p.front_image else None,
                    rear_bbox=p.rear_image.bbox if p.rear_image else None,
                )
            if opened:
                self.notify(f"Opened side-by-side preview for {p.registration_number_detected}")
            else:
                self.notify("No local photographic evidence found on disk for this pair.", severity="warning")
        except Exception as exc:
            self.notify(f"Could not open viewer: {exc}", severity="error")

    def action_confirm_plate(self) -> None:
        input_widget = self.query_one("#input-plate", Input)
        table = self.query_one("#table-active-orders", DataTable)
        raw_val = input_widget.value.strip()

        selected_order = self._selected_order_override

        # 1. If table has focus and user has a row highlighted
        if not selected_order and table.has_focus and table.cursor_row is not None and 0 <= table.cursor_row < len(self._filtered_orders):
            selected_order = self._filtered_orders[table.cursor_row]
            raw_val = selected_order.registration_number

        # 2. If user typed a value, check exact match or prefix match against filtered orders
        if not selected_order and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            if self._filtered_orders:
                for o in self._filtered_orders:
                    if normalizer.canonicalize(o.registration_number) == clean_val or o.order_number.upper() == clean_val:
                        selected_order = o
                        raw_val = o.registration_number
                        break
                # Prefix matching: if typed string is a prefix (>= 3 chars) of the top filtered candidate, select it!
                if not selected_order and len(clean_val) >= 3:
                    top_cand = self._filtered_orders[0]
                    top_clean = normalizer.canonicalize(top_cand.registration_number)
                    if top_clean.startswith(clean_val):
                        selected_order = top_cand
                        raw_val = top_cand.registration_number

        # 3. Check all loaded active orders
        if not selected_order and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            for o in self.all_active_orders:
                if normalizer.canonicalize(o.registration_number) == clean_val or o.registration_number.upper() == raw_val.upper():
                    selected_order = o
                    raw_val = o.registration_number
                    break

        # 4. Search local database directly
        if not selected_order and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            from django.db import models
            selected_order = InstallationOrder.objects.filter(
                models.Q(registration_number__iexact=clean_val) |
                models.Q(registration_number__iexact=raw_val) |
                models.Q(order_number__iexact=raw_val) |
                models.Q(order_number__icontains=clean_val)
            ).first()
            if selected_order:
                raw_val = selected_order.registration_number

        # 5. Live lookup against ITMS server if order not in local DB
        if not selected_order and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            try:
                from core.services.itms_web_client import get_web_client
                client = get_web_client()
                if client.session_store.session.is_cookie_valid():
                    fetch_res = client.fetch_order_info(clean_val, download_photos=False)
                    if fetch_res.get("success"):
                        client.sync_order_info_to_local_db(fetch_res, order_uuid=fetch_res.get("order_uuid", ""))
                        selected_order = InstallationOrder.objects.filter(
                            order_number=fetch_res.get("order_number")
                        ).first()
                        if selected_order:
                            raw_val = selected_order.registration_number
            except Exception:
                pass

        if not raw_val:
            self.notify("Please enter a plate number or select an order.", severity="warning")
            return

        canonical = normalizer.canonicalize(raw_val) or raw_val.upper()

        # 6. Auto-fetch hardware details if matched order is missing serials or tracker
        if selected_order and (not selected_order.front_plate_serial or not selected_order.gps_tracker_id):
            try:
                from core.services.itms_web_client import get_web_client
                client = get_web_client()
                if client.session_store.session.is_cookie_valid():
                    info = client.fetch_order_info(selected_order.order_number, download_photos=False)
                    if info.get("success"):
                        client.sync_order_info_to_local_db(info, order_uuid=selected_order.itms_order_uuid)
                        selected_order.refresh_from_db()
            except Exception:
                pass

        p = self.pair
        old_plate = p.registration_number_detected
        p.registration_number_detected = canonical
        if selected_order:
            p.order = selected_order
            p.match_type = VehicleInstallationPair.MatchType.EXACT
            p.match_score = 100.0
        else:
            p.match_type = VehicleInstallationPair.MatchType.NONE
            p.match_score = None

        p.is_manual_override = True
        p.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        p.manual_plate_override = canonical
        p.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        p.refresh_completeness()
        p.save()

        # Update evidence images
        if p.front_image:
            p.front_image.detected_plate = canonical
            p.front_image.save(update_fields=["detected_plate"])
        if p.rear_image:
            p.rear_image.detected_plate = canonical
            p.rear_image.save(update_fields=["detected_plate"])

        # Create audit log
        hw_desc = []
        if selected_order:
            s_val = selected_order.front_plate_serial or selected_order.plate_serial
            t_val = selected_order.gps_tracker_id or selected_order.tracker_id
            if s_val:
                hw_desc.append(f"Plate Serial: {s_val}")
            if t_val:
                hw_desc.append(f"Tracker: {t_val}")
        hw_str = f" [{', '.join(hw_desc)}]" if hw_desc else ""

        order_msg = f"linked to order #{selected_order.order_number}{hw_str}" if selected_order else "no matching order found in local registry"
        SubmissionAuditLog.objects.create(
            pair=p,
            action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator manually confirmed plate '{canonical}' ({order_msg}) [was '{old_plate}'].",
        )

        self.dismiss({
            "pair": p,
            "plate": canonical,
            "order": selected_order,
            "success": True,
        })

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-apply-plate":
            self.action_confirm_plate()
        elif btn_id == "btn-autocomplete":
            self.action_autocomplete_plate()
        elif btn_id == "btn-preview-photos":
            self.action_preview_pair()
        elif btn_id == "btn-cancel-plate":
            self.action_cancel()


class SingleOrderSubmissionModal(ModalScreen[Optional[Dict]]):
    """
    Confirms a single order before submitting to ITMS:
    - Confirms number plate characters and active order association.
    - Displays front and rear photo evidence summary.
    - Supports viewing photos side-by-side ([V]).
    - Supports swapping front and rear assignments ([S]).
    - Supports safe dry-run mode by default.
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("v", "preview_photos", "Preview Photos"),
        Binding("s", "swap_photos", "Swap Photos"),
        Binding("enter", "confirm_submission", "Submit to ITMS"),
    ]

    def __init__(self, pair: VehicleInstallationPair, **kwargs):
        super().__init__(**kwargs)
        self.pair = pair

    def _render_details(self) -> str:
        p = self.pair
        order = p.order
        plate = p.registration_number_detected or "NO_PLATE"
        order_num = order.order_number if order else "—"
        vin = order.vin if order else "—"
        stage = getattr(order, "itms_stage", "") or getattr(order, "order_status", "") or "Stage 1 (Hardware)"
        matched_via = getattr(p, "matched_via", "VISION")

        front = p.front_image
        rear = p.rear_image
        front_file = os.path.basename(front.vault_file) if front else "[red]Missing[/red]"
        rear_file = os.path.basename(rear.vault_file) if rear else "[red]Missing[/red]"

        front_conf = f"{round(front.ocr_confidence, 2)}" if front and front.ocr_confidence else "—"
        rear_conf = f"{round(rear.ocr_confidence, 2)}" if rear and rear.ocr_confidence else "—"

        return (
            f"[bold white]Vehicle & Order Identification:[/bold white]\n"
            f"  • [b]Confirmed Plate:[/b]     [bold yellow]{plate}[/bold yellow] ([cyan]Matched via {matched_via}[/cyan])\n"
            f"  • [b]ITMS Order #:[/b]        [bold cyan]{order_num}[/bold cyan]\n"
            f"  • [b]Chassis / VIN:[/b]       {vin}\n"
            f"  • [b]Current ITMS Stage:[/b]  [green]{stage}[/green]\n\n"
            f"[bold white]Photographic Evidence Confirmation:[/bold white]\n"
            f"  • [b]Front Plate Photo:[/b]   [green]{front_file}[/green] (OCR Conf: {front_conf}, Orient: {front.orientation if front else '—'})\n"
            f"  • [b]Rear Plate Photo:[/b]    [green]{rear_file}[/green] (OCR Conf: {rear_conf}, Orient: {rear.orientation if rear else '—'})\n"
            f"  • [b]Pair Status:[/b]         [bold green]{p.verification_status}[/bold green] (Completeness: {'✓ Complete' if p.is_complete else '✗ Incomplete'})"
        )

    def compose(self) -> ComposeResult:
        from core.services import config_service
        is_dry_default = config_service.get_setting("submission.dry_run_mode", True)

        header_lines = [
            "[bold cyan]═══ Confirm ITMS Order Submission ═══[/bold cyan]",
            "[dim]Review the plate registration number and photographic evidence below. "
            "Press [bold green]Enter[/bold green] to submit, [bold cyan]V[/bold cyan] to preview photos, "
            "[bold yellow]S[/bold yellow] to swap front/rear, or [bold red]Esc[/bold red] to cancel.[/dim]",
        ]

        mode_badge = (
            "[bold yellow]● DRY-RUN SAFETY MODE ACTIVE[/bold yellow] [dim](Simulates submission without live mutation)[/dim]"
            if is_dry_default
            else "[bold red]● LIVE SUBMISSION MODE ACTIVE[/bold red] [dim](Photos and updates will be uploaded to stock.itms.ug)[/dim]"
        )

        with Vertical(id="modal-dialog", classes="plate-entry-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            yield Static(mode_badge, id="modal-submission-mode-indicator")
            yield Static(self._render_details(), id="modal-details-card")
            with Vertical(id="modal-bottom-bar"):
                yield Checkbox(
                    "Dry-Run Safety Mode (Simulate multi-step wizard without mutating live ITMS database)",
                    value=is_dry_default,
                    id="chk-submission-dry-run",
                )
                with Horizontal(id="modal-footer"):
                    yield Button("Preview Photos [V]", variant="default", id="btn-view-evidence")
                    yield Button("Swap Front/Rear [S]", variant="warning", id="btn-swap-evidence")
                    btn_label = "Simulate Submit [Enter]" if is_dry_default else "Confirm & Submit [Enter]"
                    yield Button(btn_label, variant="success", id="btn-submit-order")
                    yield Button("Cancel [Esc]", variant="error", id="btn-cancel-submission")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "chk-submission-dry-run":
            try:
                indicator = self.query_one("#modal-submission-mode-indicator", Static)
                btn = self.query_one("#btn-submit-order", Button)
                if event.value:
                    indicator.update(
                        "[bold yellow]● DRY-RUN SAFETY MODE ACTIVE[/bold yellow] [dim](Simulates submission without live mutation)[/dim]"
                    )
                    btn.label = "Simulate Submit [Enter]"
                else:
                    indicator.update(
                        "[bold red]● LIVE SUBMISSION MODE ACTIVE[/bold red] [dim](Photos and updates will be uploaded to stock.itms.ug)[/dim]"
                    )
                    btn.label = "Confirm & Submit [Enter]"
            except Exception:
                pass

    def action_preview_photos(self) -> None:
        p = self.pair
        try:
            opened = viewer.show_pair_evidence(p)
            if not opened:
                front_path = os.path.join(settings.MEDIA_ROOT, p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
                rear_path = os.path.join(settings.MEDIA_ROOT, p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None
                opened = viewer.show_evidence_pair(
                    front_path=front_path if front_path and os.path.isfile(front_path) else None,
                    rear_path=rear_path if rear_path and os.path.isfile(rear_path) else None,
                    pair_id=str(p.id)[:8],
                    order_number=p.order.order_number if p.order else "—",
                    plate=p.registration_number_detected,
                    status=p.verification_status,
                )
            if opened:
                self.notify(f"Opened preview for {p.registration_number_detected}")
            else:
                self.notify("No local photographic evidence found on disk for this pair.", severity="warning")
        except Exception as exc:
            self.notify(f"Could not open viewer: {exc}", severity="error")

    def action_swap_photos(self) -> None:
        p = self.pair
        if not (p.front_image and p.rear_image):
            self.notify("Need both front and rear photos to swap.", severity="warning")
            return
        p.front_image, p.rear_image = p.rear_image, p.front_image
        p.save(update_fields=["front_image", "rear_image"])
        SubmissionAuditLog.objects.create(
            pair=p,
            action=SubmissionAuditLog.Action.OPERATOR_SWAP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator swapped front and rear image assignments during pre-submission review.",
        )
        self.query_one("#modal-details-card", Static).update(self._render_details())
        self.notify("Swapped front and rear photo assignments.")

    def action_confirm_submission(self) -> None:
        chk = self.query_one("#chk-submission-dry-run", Checkbox)
        dry_run = chk.value
        self.dismiss({
            "pair": self.pair,
            "dry_run": dry_run,
            "confirmed": True,
        })

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-submit-order":
            self.action_confirm_submission()
        elif btn_id == "btn-view-evidence":
            self.action_preview_photos()
        elif btn_id == "btn-swap-evidence":
            self.action_swap_photos()
        elif btn_id == "btn-cancel-submission":
            self.action_cancel()


class BatchSubmissionModal(ModalScreen[Optional[Dict]]):
    """
    Automated batch submission confirmation dialog:
    - Displays all approved orders ready for ITMS submission (e.g. 200 orders!).
    - Checks front & rear photographic completeness for every order.
    - Displays dry-run safety toggle.
    - Confirms and returns execution parameters to the operator.
    """
    DEFAULT_CSS = """
    BatchSubmissionModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    BatchSubmissionModal #modal-dialog {
        width: 92%;
        height: 88%;
        max-height: 94%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    BatchSubmissionModal #modal-header {
        height: auto;
        background: $panel;
        color: $text;
        padding: 0 1;
        margin-bottom: 0;
    }
    BatchSubmissionModal #modal-batch-mode-indicator {
        height: auto;
        margin-top: 1;
        margin-bottom: 0;
        padding: 0 1;
    }
    BatchSubmissionModal #table-batch-review {
        height: 1fr;
        min-height: 6;
        margin-top: 1;
        border: solid $panel;
    }
    BatchSubmissionModal #modal-bottom-bar {
        dock: bottom;
        height: auto;
        background: $surface;
        border-top: solid $panel;
        padding-top: 1;
        margin-top: 1;
    }
    BatchSubmissionModal #chk-batch-dry-run {
        height: auto;
        margin-bottom: 0;
    }
    BatchSubmissionModal #modal-footer {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    BatchSubmissionModal #modal-footer Button {
        margin-left: 2;
        min-width: 22;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "confirm_batch", "Submit Batch", priority=True),
    ]

    def __init__(self, pairs: List[VehicleInstallationPair], **kwargs):
        super().__init__(**kwargs)
        self.pairs = pairs

    def compose(self) -> ComposeResult:
        from core.services import config_service
        is_dry_default = config_service.get_setting("submission.dry_run_mode", True)

        total = len(self.pairs)
        complete = sum(1 for p in self.pairs if p.is_complete)
        header_lines = [
            f"[bold cyan]═══ Batch ITMS Submission Confirmation ═══[/bold cyan]",
            f"[b]Ready to Submit:[/b] [bold green]{total} approved orders[/bold green]  │  "
            f"[b]Complete Pairs (Front + Rear):[/b] [bold yellow]{complete} / {total}[/bold yellow]  │  "
            f"[b]Completeness Rate:[/b] [cyan]{int((complete/total)*100) if total else 0}%[/cyan]",
            "[dim]Review the batch list below. All orders will be submitted sequentially through the automated ITMS wizard. "
            "Press [bold green]Enter[/bold green] to confirm and begin batch submission, or [bold red]Esc[/bold red] to cancel.[/dim]",
        ]

        mode_badge = (
            "[bold yellow]● DRY-RUN BATCH ACTIVE[/bold yellow] [dim](Simulates submission without remote server mutations)[/dim]"
            if is_dry_default
            else "[bold red]● LIVE BATCH SUBMISSION ACTIVE[/bold red] [dim](Direct upload & finalization on stock.itms.ug)[/dim]"
        )

        with Vertical(id="modal-dialog", classes="plate-entry-modal batch-submission-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            yield Static(mode_badge, id="modal-batch-mode-indicator")
            yield DataTable(id="table-batch-review")
            with Vertical(id="modal-bottom-bar", classes="batch-bottom-bar"):
                yield Checkbox(
                    "Dry-Run Safety Mode (Simulate multi-step submission without remote server mutations)",
                    value=is_dry_default,
                    id="chk-batch-dry-run",
                )
                with Horizontal(id="modal-footer"):
                    btn_label = f"🧪 Simulate All {total} Orders [Enter]" if is_dry_default else f"🚀 Submit All {total} Orders [Enter]"
                    yield Button(btn_label, variant="success", id="btn-batch-run")
                    yield Button("Cancel [Esc]", variant="error", id="btn-batch-cancel")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "chk-batch-dry-run":
            try:
                indicator = self.query_one("#modal-batch-mode-indicator", Static)
                btn = self.query_one("#btn-batch-run", Button)
                total = len(self.pairs)
                if event.value:
                    indicator.update(
                        "[bold yellow]● DRY-RUN BATCH ACTIVE[/bold yellow] [dim](Simulates submission without remote server mutations)[/dim]"
                    )
                    btn.label = f"🧪 Simulate All {total} Orders [Enter]"
                else:
                    indicator.update(
                        "[bold red]● LIVE BATCH SUBMISSION ACTIVE[/bold red] [dim](Direct upload & finalization on stock.itms.ug)[/dim]"
                    )
                    btn.label = f"🚀 Submit All {total} Orders [Enter]"
            except Exception:
                pass

    def on_mount(self) -> None:
        table = self.query_one("#table-batch-review", DataTable)
        table.add_columns("#", "Plate", "Order Number", "Front Photo", "Rear Photo", "Completeness", "Method")
        table.cursor_type = "row"

        for idx, p in enumerate(self.pairs, 1):
            f_file = os.path.basename(p.front_image.vault_file) if p.front_image else "Missing"
            r_file = os.path.basename(p.rear_image.vault_file) if p.rear_image else "Missing"
            comp_badge = "[bold green]✓ Ready[/bold green]" if p.is_complete else "[bold red]✗ Incomplete[/bold red]"
            method = getattr(p, "matched_via", "VISION")

            table.add_row(
                f"#{idx}",
                f"[bold yellow]{p.registration_number_detected}[/bold yellow]",
                p.order.order_number if p.order else "—",
                f_file,
                r_file,
                comp_badge,
                method,
                key=str(p.id),
            )

    def action_confirm_batch(self) -> None:
        chk = self.query_one("#chk-batch-dry-run", Checkbox)
        self.dismiss({
            "confirmed": True,
            "dry_run": chk.value,
            "count": len(self.pairs),
            "pairs": self.pairs,
        })

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_confirm_batch()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-batch-run":
            self.action_confirm_batch()
        elif btn_id == "btn-batch-cancel":
            self.action_cancel()


class BatchProgressModal(ModalScreen[None]):
    """Live visual progress modal for batch ITMS submissions with real-time activity stream."""

    DEFAULT_CSS = """
    BatchProgressModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }
    BatchProgressModal #modal-dialog {
        width: 92%;
        height: 90%;
        max-height: 94%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    BatchProgressModal #modal-header {
        height: auto;
        background: $panel;
        color: $text;
        padding: 0 1;
        margin-bottom: 0;
    }
    BatchProgressModal #batch-progress-title {
        height: 1;
        color: $text;
        text-style: bold;
        margin-top: 1;
    }
    BatchProgressModal #batch-progress-bar {
        margin: 0 0;
    }
    BatchProgressModal #batch-progress-step {
        height: 1;
        color: $accent;
    }
    BatchProgressModal #batch-progress-stats {
        height: 1;
        margin-bottom: 0;
    }
    BatchProgressModal #batch-log-container {
        height: 1fr;
        min-height: 8;
        border: solid $panel;
        background: $background;
        padding: 0 1;
        margin-top: 1;
        margin-bottom: 1;
    }
    BatchProgressModal #batch-log-title {
        height: 1;
        color: $text-muted;
    }
    BatchProgressModal #batch-progress-log {
        height: 1fr;
        background: transparent;
    }
    BatchProgressModal #modal-footer {
        height: 3;
        align: right middle;
    }
    BatchProgressModal #modal-footer Button {
        margin-left: 2;
        min-width: 22;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("escape", "request_close", "Close / Stop", priority=True),
        Binding("space", "toggle_pause", "Pause / Resume"),
    ]

    def __init__(self, total_orders: int, dry_run: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.total_orders = total_orders
        self.dry_run = dry_run
        self.is_paused = False
        self.is_stopped = False
        self.is_finished = False

    def compose(self) -> ComposeResult:
        mode_str = "[bold yellow][DRY-RUN][/bold yellow]" if self.dry_run else "[bold green][LIVE][/bold green]"
        with Vertical(id="modal-dialog", classes="batch-progress-modal"):
            yield Static(
                f"[bold cyan]═══ Batch ITMS Submission Progress ═══[/bold cyan]  {mode_str}\n"
                f"[dim]Processing {self.total_orders} orders (~{self.total_orders * 2} photos). Live activity stream & telemetry below.[/dim]",
                id="modal-header",
            )
            yield Static("Initializing submission engine...", id="batch-progress-title")
            yield ProgressBar(id="batch-progress-bar", total=self.total_orders, show_eta=True)
            yield Static("Preparing batch queue...", id="batch-progress-step")
            yield Static(f"Succeeded: 0  │  Failed: 0  │  Remaining: {self.total_orders}", id="batch-progress-stats")
            with Vertical(id="batch-log-container"):
                yield Static("[bold cyan]📜 Live Activity Stream & Compression Telemetry:[/bold cyan]", id="batch-log-title")
                yield RichLog(id="batch-progress-log", highlight=True, markup=True, wrap=True)
            with Horizontal(id="modal-footer"):
                yield Button("Pause [Space]", variant="warning", id="btn-pause-resume")
                yield Button("Stop Batch [Esc]", variant="error", id="btn-stop-batch")

    def log_event(self, text: str) -> None:
        """Appends a timestamped entry to the real-time activity stream."""
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M:%S")
        try:
            rlog = self.query_one("#batch-progress-log", RichLog)
            rlog.write(f"[dim]{now_str}[/dim] {text}")
        except Exception:
            pass

    def update_progress(
        self,
        current_idx: int,
        plate: str,
        order_num: str,
        step_detail: str,
        succeeded: int,
        failed: int,
    ) -> None:
        try:
            pbar = self.query_one("#batch-progress-bar", ProgressBar)
            pbar.progress = current_idx

            title = self.query_one("#batch-progress-title", Static)
            title.update(
                f"[bold cyan][{current_idx}/{self.total_orders}][/bold cyan] "
                f"Plate: [bold yellow]{plate}[/bold yellow]  (Order #{order_num})"
            )

            step = self.query_one("#batch-progress-step", Static)
            step.update(step_detail)

            stats = self.query_one("#batch-progress-stats", Static)
            rem = max(0, self.total_orders - (succeeded + failed))
            stats.update(
                f"[bold green]✓ Succeeded: {succeeded}[/bold green]  │  "
                f"[bold red]✗ Failed: {failed}[/bold red]  │  "
                f"Remaining: {rem}"
            )
        except Exception:
            pass

    def finish_batch(self, succeeded: int, failed: int, message: str = "") -> None:
        self.is_finished = True
        try:
            pbar = self.query_one("#batch-progress-bar", ProgressBar)
            pbar.progress = self.total_orders

            title = self.query_one("#batch-progress-title", Static)
            title.update("[bold green]═══ Batch ITMS Submission Complete ═══[/bold green]")

            step = self.query_one("#batch-progress-step", Static)
            step.update(message or f"Completed {succeeded} of {self.total_orders} orders successfully.")

            stats = self.query_one("#batch-progress-stats", Static)
            stats.update(
                f"[bold green]Total Successful: {succeeded}[/bold green]  │  "
                f"[bold red]Total Failed: {failed}[/bold red]"
            )

            btn_stop = self.query_one("#btn-stop-batch", Button)
            btn_stop.label = "Close [Esc]"
            btn_stop.variant = "primary"

            btn_pause = self.query_one("#btn-pause-resume", Button)
            btn_pause.disabled = True
        except Exception:
            pass

    def action_request_close(self) -> None:
        if self.is_finished:
            self.dismiss(None)
        else:
            self.is_stopped = True
            try:
                step = self.query_one("#batch-progress-step", Static)
                step.update("[bold red]Stopping batch queue safely... waiting for current order to complete.[/bold red]")
            except Exception:
                pass

    def action_toggle_pause(self) -> None:
        if self.is_finished:
            return
        self.is_paused = not self.is_paused
        try:
            btn = self.query_one("#btn-pause-resume", Button)
            btn.label = "Resume [Space]" if self.is_paused else "Pause [Space]"
            btn.variant = "success" if self.is_paused else "warning"
            step = self.query_one("#batch-progress-step", Static)
            if self.is_paused:
                step.update("[bold yellow]Batch queue paused by operator. Press Space to resume.[/bold yellow]")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-stop-batch":
            self.action_request_close()
        elif btn_id == "btn-pause-resume":
            self.action_toggle_pause()


class VaultLocationDialog(ModalScreen[Optional[str]]):
    """
    Interactive modal dialog allowing operators to choose or confirm
    where the Evidence Vault is located on disk.
    """
    BINDINGS = [
        Binding("escape", "dismiss_dialog", "Skip / Keep Current", priority=True),
        Binding("enter", "confirm_selection", "Confirm Vault Path"),
    ]

    def __init__(self, is_startup: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.is_startup = is_startup

    def compose(self) -> ComposeResult:
        from core.services import config_service, vault_service
        current_vault = str(config_service.get_setting("storage.vault_path", "media/vault"))
        prompt_on_startup = config_service.get_setting("storage.prompt_vault_on_startup", True)

        header_lines = [
            "[bold cyan]═══ 📦 Evidence Vault Storage Location ═══[/bold cyan]",
            "The Evidence Vault stores all incoming motorcycle evidence photos, EXIF metadata, timestamps, and hashes.",
            f"Active Vault Root: [bold yellow]{vault_service.get_vault_root()}[/bold yellow]",
            "[dim]Choose your desired vault folder below, or keep the default (media/vault).[/dim]",
        ]

        with Vertical(id="modal-dialog", classes="vault-location-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            with Horizontal(classes="vault-input-row"):
                yield Input(value=current_vault, placeholder="e.g. media/vault or D:/itms_vault", id="input-vault-path")
                yield Button("📂 Browse Folder...", variant="primary", id="btn-browse-vault")
                yield Button("Default (media/vault)", variant="default", id="btn-default-vault")

            with Horizontal(classes="vault-options-row"):
                yield Checkbox(
                    "Prompt for Vault Location on application startup",
                    value=prompt_on_startup,
                    id="chk-prompt-startup",
                )

            yield Static("[dim]Press Enter or click Confirm to save, or Esc to keep current location.[/dim]", id="vault-dialog-hint")

            with Horizontal(id="modal-footer"):
                yield Button("💾 Confirm & Use Vault [Enter]", variant="success", id="btn-confirm-vault")
                yield Button("Skip / Keep Current [Esc]", variant="warning", id="btn-cancel-vault")

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)

    def action_confirm_selection(self) -> None:
        from core.services import config_service, vault_service
        input_w = self.query_one("#input-vault-path", Input)
        chk_w = self.query_one("#chk-prompt-startup", Checkbox)
        new_path = input_w.value.strip() or "media/vault"

        # Update settings in secure/config.json
        vault_service.set_vault_root(new_path)
        config_service.set_setting("storage.prompt_vault_on_startup", bool(chk_w.value))

        self.notify(f"Evidence Vault configured: {new_path}", severity="information")
        self.dismiss(new_path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-confirm-vault":
            self.action_confirm_selection()
        elif btn_id == "btn-cancel-vault":
            self.action_dismiss_dialog()
        elif btn_id == "btn-default-vault":
            input_w = self.query_one("#input-vault-path", Input)
            input_w.value = "media/vault"
        elif btn_id == "btn-browse-vault":
            self._browse_directory()

    def _browse_directory(self) -> None:
        from core.services.file_dialog import prompt_native_directory_selection
        input_w = self.query_one("#input-vault-path", Input)
        chosen = prompt_native_directory_selection(
            initial_dir=input_w.value.strip() or None,
            title="Choose Evidence Vault Storage Directory",
        )
        if chosen:
            input_w.value = chosen
            self.notify(f"Selected: {chosen}")



