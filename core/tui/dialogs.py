"""
Modal dialog screens for the ITMS Operator TUI.
"""
from datetime import date, datetime
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from django.conf import settings
from django.utils import timezone
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    ProgressBar,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from core.tui.inspectors import escape_markup, escape
from core.models import EvidenceImage, InstallationKit, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
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
            f"[b]Active Pair:[/b] [bold yellow]{escape(self.pair.registration_number_detected or '—')}[/bold yellow]  │  "
            f"[b]Anchor Photo:[/b] [b]{escape(t_file)}[/b] ([bold green]{t_orient}[/bold green], Plate: {escape(t_plate)}, [cyan]{escape(t_sig.display_tag)}[/cyan])  │  "
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
                escape(cand["filename"]),
                escape(cand.get("camera_tag", "—")),
                escape(cand["plate"]),
                cand["orientation"],
                escape(cand["time_diff_display"]),
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
        Binding("ctrl+u", "unlink_order", "Unlink Order"),
        Binding("enter", "confirm_plate", "Apply Plate"),
    ]

    def __init__(self, pair: VehicleInstallationPair, **kwargs):
        super().__init__(**kwargs)
        self.pair = pair
        self.all_active_orders: List[InstallationOrder] = []
        self.all_kits: List[InstallationKit] = []
        self._filtered_items: List[Dict[str, Any]] = []
        self._selected_item_override: Optional[Dict[str, Any]] = None
        self._updating_from_table: bool = False

    def compose(self) -> ComposeResult:
        p = self.pair
        f_file = os.path.basename(p.front_image.vault_file) if (p.front_image and p.front_image.vault_file) else "None"
        r_file = os.path.basename(p.rear_image.vault_file) if (p.rear_image and p.rear_image.vault_file) else "None"
        current_tag = p.registration_number_detected or "NO_PLATE"

        header_lines = [
            "[bold cyan]═══ Quick Plate & Order Matcher ═══[/bold cyan]",
            f"[b]Pair ID:[/b] [bold yellow]{str(p.id)[:8]}[/bold yellow]  │  "
            f"[b]Current Tag:[/b] [bold white]{escape(current_tag)}[/bold white]  │  "
            f"[b]Front:[/b] [green]{escape(f_file)}[/green]  │  [b]Rear:[/b] [green]{escape(r_file)}[/green]",
            "[dim]Type the plate number below, or select from active ITMS orders or unallocated kits. "
            "Press [bold green]Enter[/bold green] to apply & approve, [bold cyan]Tab[/bold cyan] to auto-complete, [bold cyan]V[/bold cyan] to preview photos side-by-side, or [bold red]Esc[/bold red] to cancel.[/dim]",
        ]

        with Vertical(id="modal-dialog", classes="plate-entry-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            yield Input(
                placeholder="Type plate (e.g. UMA 300PW) or search Order # / Kit Code / VIN...",
                value="" if current_tag.startswith("PAIR-") or current_tag.startswith("MISSING-") else current_tag,
                id="input-plate",
            )
            yield Static("", id="plate-lifecycle-card")
            yield Static("[bold white]Active Orders & Unallocated Kits (Live Filtered):[/bold white]", id="active-orders-label")
            yield DataTable(id="table-active-orders")
            with Horizontal(id="modal-footer"):
                yield Button("Preview Photos [V]", variant="default", id="btn-preview-photos")
                yield Button("Auto-complete [Tab]", variant="default", id="btn-autocomplete")
                if self.pair.order:
                    yield Button("Unlink Order [Ctrl+U]", variant="warning", id="btn-unlink-plate-order")
                yield Button("Apply Plate & Approve [Enter]", variant="primary", id="btn-apply-plate")
                yield Button("Cancel [Esc]", variant="error", id="btn-cancel-plate")

    def on_mount(self) -> None:
        table = self.query_one("#table-active-orders", DataTable)
        table.add_columns("Type / Record #", "Registration Plate", "VIN / Hardware", "Lifecycle Status", "Warehouse")
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

        # Load installation kits from DB
        self.all_kits = list(
            InstallationKit.objects.all().order_by("-created_date", "-id")[:500]
        )

        input_widget = self.query_one("#input-plate", Input)
        input_widget.focus()

        init_val = input_widget.value.strip()
        self._update_lifecycle_card(init_val)
        self._filter_items_for_query(init_val)

    def _update_lifecycle_card(self, text: str) -> None:
        try:
            card = self.query_one("#plate-lifecycle-card", Static)
        except Exception:
            return
        val = (text or "").strip()
        if not val or val.startswith("PAIR-") or val.startswith("MISSING-"):
            card.update(
                "[dim]💡 Type a license plate above (e.g. [bold cyan]UMA 300PW[/bold cyan] or [bold cyan]UMA 696PU[/bold cyan]) "
                "to inspect its ITMS stock, active order, or archive lifecycle.[/dim]"
            )
            return

        from core.services.plate_lifecycle_service import resolve_plate_lifecycle, format_lifecycle_card
        from core.services.report_service import parse_target_date_suffix, format_date_suffix_readable

        date_suf = parse_target_date_suffix(val)
        if date_suf and len(date_suf) == 6 and date_suf.isdigit() and not re.match(r"^[A-Za-z]{3}\s*\d{3}", val):
            formatted = format_date_suffix_readable(date_suf)
            archived_cnt = InstallationOrder.objects.filter(order_number__endswith=date_suf, is_archived=True).count()
            active_cnt = InstallationOrder.objects.filter(order_number__endswith=date_suf, is_archived=False).count()
            total_cnt = archived_cnt + active_cnt
            card.update(
                f"[bold cyan]📅 WORK DATE SEARCH:[/bold cyan] [bold yellow]{formatted} (Suffix: {date_suf})[/bold yellow]\n"
                f" [b]Found {total_cnt} orders for this date:[/b]  "
                f"[bold white on dark_blue] {archived_cnt} Installed (Archive) [/]  │  "
                f"[bold black on gold1] {active_cnt} Pending / Active [/]\n"
                f" [dim]Select an order from the table below or press [bold cyan]Tab[/bold cyan] to auto-complete.[/dim]"
            )
            return

        res = resolve_plate_lifecycle(val, query_live_if_missing=False)
        card.update(format_lifecycle_card(res))

    def _filter_items_for_query(self, query: str) -> None:
        q = (query or "").strip().lower()
        clean_q = normalizer.canonicalize(q).lower() if q else ""
        from core.services.report_service import parse_target_date_suffix
        date_suf = parse_target_date_suffix(q)


        all_candidates: List[Dict[str, Any]] = []

        # 1. Orders
        for o in self.all_active_orders:
            stage_str = o.itms_stage or o.order_status or "Pending"
            if o.is_archived:
                status_styled = "[bold white on dark_blue] INSTALLED (ARCHIVE) [/]"
                status_plain = "Installed (Archive)"
            else:
                status_styled = f"[bold black on gold1] {stage_str} [/]"
                status_plain = stage_str

            all_candidates.append({
                "type": "ORDER",
                "obj": o,
                "code": f"#{o.order_number}",
                "plate": o.registration_number,
                "detail": o.vin or "—",
                "status_styled": status_styled,
                "status_plain": status_plain,
                "warehouse": o.warehouse_name or "—",
            })

        # 2. Kits
        for k in self.all_kits:
            k_stat = (k.status or "").lower()
            if k_stat == "new":
                status_styled = "[bold white on dark_green] NEW (UNALLOCATED) [/]"
                status_plain = "New (Unallocated Kit)"
            elif "allocat" in k_stat:
                status_styled = "[bold black on gold1] ALLOCATED [/]"
                status_plain = "Allocated Kit"
            elif "install" in k_stat:
                status_styled = "[bold white on dark_blue] INSTALLED [/]"
                status_plain = "Installed Kit"
            else:
                status_styled = f"[dim]{k.status}[/dim]"
                status_plain = k.status or "Kit"

            all_candidates.append({
                "type": "KIT",
                "obj": k,
                "code": k.kit_code,
                "plate": k.registration_number,
                "detail": f"F: {k.front_plate or '—'} R: {k.rear_plate or '—'} GPS: {k.gps_tracker or '—'}",
                "status_styled": status_styled,
                "status_plain": status_plain,
                "warehouse": k.warehouse or "—",
            })

        def _sort_key(item: Dict[str, Any]) -> int:
            if item["type"] == "KIT" and "new" in item["status_plain"].lower():
                return 0
            if item["type"] == "ORDER" and not getattr(item["obj"], "is_archived", False):
                return 1
            return 2

        if not q:
            # Default sorting: New Kits first, then active orders
            self._filtered_items = sorted(all_candidates, key=_sort_key)[:100]
        else:
            matches = []
            for item in all_candidates:
                plate_str = (item["plate"] or "").lower()
                clean_plate = normalizer.canonicalize(item["plate"]).lower() if item["plate"] else ""
                code_str = (item["code"] or "").lower()
                detail_str = (item["detail"] or "").lower()

                if (
                    q in plate_str
                    or (clean_q and clean_q in clean_plate)
                    or q in code_str
                    or (clean_q and clean_q in code_str)
                    or (date_suf and date_suf in code_str)
                    or q in detail_str
                ):
                    matches.append(item)

            # If not in active memory list, query the local database directly
            if not matches:
                from django.db import models
                order_filter = (
                    models.Q(registration_number__icontains=clean_q or q) |
                    models.Q(order_number__icontains=clean_q or q) |
                    models.Q(vin__icontains=q)
                )
                if date_suf:
                    order_filter = order_filter | models.Q(order_number__icontains=date_suf)

                db_orders = list(
                    InstallationOrder.objects.filter(order_filter)[:50]
                )
                for o in db_orders:
                    stage_str = o.itms_stage or o.order_status or "Pending"
                    matches.append({
                        "type": "ORDER",
                        "obj": o,
                        "code": f"#{o.order_number}",
                        "plate": o.registration_number,
                        "detail": o.vin or "—",
                        "status_styled": "[bold white on dark_blue] INSTALLED (ARCHIVE) [/]" if o.is_archived else f"[bold black on gold1] {stage_str} [/]",
                        "status_plain": "Installed (Archive)" if o.is_archived else stage_str,
                        "warehouse": o.warehouse_name or "—",
                    })

                db_kits = list(
                    InstallationKit.objects.filter(
                        models.Q(registration_number__icontains=clean_q or q) |
                        models.Q(kit_code__icontains=clean_q or q) |
                        models.Q(front_plate__icontains=q) |
                        models.Q(rear_plate__icontains=q) |
                        models.Q(gps_tracker__icontains=q)
                    )[:25]
                )
                for k in db_kits:
                    k_stat = (k.status or "").lower()
                    status_styled = "[bold white on dark_green] NEW (UNALLOCATED) [/]" if k_stat == "new" else f"[bold black on gold1] {k.status} [/]"
                    matches.append({
                        "type": "KIT",
                        "obj": k,
                        "code": k.kit_code,
                        "plate": k.registration_number,
                        "detail": f"F: {k.front_plate or '—'} R: {k.rear_plate or '—'} GPS: {k.gps_tracker or '—'}",
                        "status_styled": status_styled,
                        "status_plain": f"Kit {k.status}",
                        "warehouse": k.warehouse or "—",
                    })

            self._filtered_items = matches

        self._populate_orders_table()

    def _populate_orders_table(self) -> None:
        try:
            table = self.query_one("#table-active-orders", DataTable)
        except Exception:
            return
        table.clear()
        for idx, item in enumerate(self._filtered_items):
            type_tag = "[bold green]KIT[/bold green]" if item["type"] == "KIT" else "[bold yellow]ORD[/bold yellow]"
            table.add_row(
                f"{type_tag} {escape(item['code'])}",
                f"[bold green]{escape(item['plate'])}[/bold green]",
                escape(item["detail"][:28]),
                item["status_styled"],
                escape(item["warehouse"][:25] if item["warehouse"] else "—"),
                key=str(idx),
            )
        if self._filtered_items and len(self._filtered_items) > 0:
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
        if self._filtered_items:
            top = self._filtered_items[0]
            label.update(
                f"[bold white]ITMS Candidates:[/bold white] "
                f"[bold green]Top Match → {escape(top['plate'])}[/bold green] "
                f"[cyan]({escape(top['code'])} │ {escape(top['status_plain'])})[/cyan] "
                f"[dim]— Press [bold green]Enter[/bold green] or [bold cyan]Tab[/bold cyan] to select[/dim]"
            )
        else:
            label.update(
                "[bold yellow]ITMS Matches (0 local matches — will verify live on Enter):[/bold yellow]"
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._updating_from_table:
            return
        self._selected_item_override = None
        self._update_lifecycle_card(event.value)
        self._filter_items_for_query(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_confirm_plate()

    def action_autocomplete_plate(self) -> None:
        if self._filtered_items:
            top = self._filtered_items[0]
            self._selected_item_override = top
            input_widget = self.query_one("#input-plate", Input)
            self._updating_from_table = True
            input_widget.value = top["plate"]
            self._updating_from_table = False
            self._update_lifecycle_card(top["plate"])
            self.notify(f"Auto-completed plate: {top['plate']} ({top['code']})")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#table-active-orders", DataTable)
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self._filtered_items):
            selected_item = self._filtered_items[row_idx]
            self._selected_item_override = selected_item
            if table.has_focus:
                input_widget = self.query_one("#input-plate", Input)
                self._updating_from_table = True
                input_widget.value = selected_item["plate"]
                self._updating_from_table = False
                self._update_lifecycle_card(selected_item["plate"])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self._filtered_items):
            self._selected_item_override = self._filtered_items[row_idx]
            input_widget = self.query_one("#input-plate", Input)
            input_widget.value = self._selected_item_override["plate"]
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

        selected_item = self._selected_item_override

        # 1. If table has focus and user has a row highlighted
        if not selected_item and table.has_focus and table.cursor_row is not None and 0 <= table.cursor_row < len(self._filtered_items):
            selected_item = self._filtered_items[table.cursor_row]
            raw_val = selected_item["plate"]

        # 2. Check candidate matches from filtered list
        if not selected_item and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            if self._filtered_items:
                for itm in self._filtered_items:
                    cand_plate = normalizer.canonicalize(itm["plate"]) or itm["plate"].replace(" ", "").upper()
                    cand_code = itm["code"].replace("#", "").upper()
                    if cand_plate == clean_val or cand_code == clean_val:
                        selected_item = itm
                        raw_val = itm["plate"]
                        break

        if not raw_val:
            self.notify("Please enter a plate number or select an order/kit.", severity="warning")
            return

        canonical = normalizer.canonicalize(raw_val) or raw_val.upper()

        # 3. Resolve Plate Lifecycle
        from core.services.plate_lifecycle_service import resolve_plate_lifecycle
        lifecycle_res = resolve_plate_lifecycle(canonical, query_live_if_missing=True)

        selected_order = None
        kit_linked = None

        if lifecycle_res.is_unallocated:
            # UNALLOCATED KIT: Physical plate taken from stock or detected, but kit status is 'New'
            # and it has not been allocated to any active order or archive in ITMS.
            kit = lifecycle_res.kit or InstallationKit.objects.filter(
                models.Q(registration_number__iexact=canonical) | models.Q(kit_code__iexact=lifecycle_res.kit_code)
            ).first()
            kit_linked = kit
            kit_code = kit.kit_code if kit else (lifecycle_res.kit_code or f"IK-{canonical}")
            wh_name = (kit.warehouse if kit else lifecycle_res.warehouse) or "Warehouse Stock"

            p = self.pair
            old_plate = p.registration_number_detected
            p.registration_number_detected = canonical
            p.order = None
            p.match_type = VehicleInstallationPair.MatchType.NONE
            p.match_score = None
            p.is_manual_override = True
            p.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
            p.manual_plate_override = canonical
            p.verification_status = VehicleInstallationPair.VerificationStatus.UNREGISTERED
            p.operator_note = (
                f"UNALLOCATED: Plate {canonical} belongs to Kit {kit_code} with status 'New' "
                f"in stock at {wh_name}. No active ITMS installation order exists."
            )
            p.refresh_completeness()
            p.save()

            if p.front_image:
                p.front_image.detected_plate = canonical
                p.front_image.save(update_fields=["detected_plate"])
            if p.rear_image:
                p.rear_image.detected_plate = canonical
                p.rear_image.save(update_fields=["detected_plate"])

            hw_desc = []
            if kit:
                s_val = kit.front_plate or kit.rear_plate
                t_val = kit.gps_tracker
                if s_val:
                    hw_desc.append(f"Plate Serial: {s_val}")
                if t_val:
                    hw_desc.append(f"Tracker: {t_val}")
            hw_str = f" [{', '.join(hw_desc)}]" if hw_desc else ""

            SubmissionAuditLog.objects.create(
                pair=p,
                action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
                result=SubmissionAuditLog.ResultStatus.FAILURE,
                message=(
                    f"Operator checked plate '{canonical}' — Identified as UNALLOCATED stock kit "
                    f"({kit_code}{hw_str}, Status: New at {wh_name}). No active ITMS order exists."
                ),
            )

            self.notify(
                f"⚠️ Plate {canonical} is UNALLOCATED in ITMS! Kit {kit_code} is 'New' in stock (not yet assigned to an order).",
                severity="warning",
                timeout=6,
            )

            self.dismiss({
                "pair": p,
                "plate": canonical,
                "order": None,
                "unallocated": True,
                "kit_code": kit_code,
                "success": True,
            })
            return
        elif lifecycle_res.is_allocated:
            # ACTIVE ORDER
            selected_order = lifecycle_res.order or InstallationOrder.objects.filter(
                order_number=lifecycle_res.order_number
            ).first()
        elif lifecycle_res.is_installed:
            # COMPLETED ARCHIVE
            selected_order = lifecycle_res.order or InstallationOrder.objects.filter(
                order_number=lifecycle_res.order_number
            ).first()
            self.notify(f"Notice: Plate {canonical} is already verified in ITMS Archive (#{lifecycle_res.order_number}).", severity="warning")

        # Fallback to local DB search if lifecycle check didn't bind an order
        if not selected_order:
            selected_order = InstallationOrder.objects.filter(
                models.Q(registration_number__iexact=canonical) | models.Q(order_number__iexact=raw_val)
            ).first()

        # Update Pair
        p = self.pair
        old_plate = p.registration_number_detected
        p.registration_number_detected = canonical
        if selected_order:
            p.order = selected_order
            p.match_type = VehicleInstallationPair.MatchType.EXACT
            p.match_score = 100.0
            p.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
            p.operator_note = f"Linked to ITMS order #{selected_order.order_number}"
        else:
            p.order = None
            p.match_type = VehicleInstallationPair.MatchType.NONE
            p.match_score = None
            p.verification_status = VehicleInstallationPair.VerificationStatus.UNREGISTERED
            p.operator_note = f"Plate {canonical} not found in ITMS orders or kits stock."

        p.is_manual_override = True
        p.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        p.manual_plate_override = canonical
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

        if selected_order:
            order_msg = f"linked to order #{selected_order.order_number}{hw_str}"
        else:
            order_msg = "no matching order found in local registry"

        SubmissionAuditLog.objects.create(
            pair=p,
            action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
            result=SubmissionAuditLog.ResultStatus.SUCCESS if selected_order else SubmissionAuditLog.ResultStatus.FAILURE,
            message=f"Operator manually confirmed plate '{canonical}' ({order_msg}) [was '{old_plate}'].",
        )

        self.dismiss({
            "pair": p,
            "plate": canonical,
            "order": selected_order,
            "unallocated": False,
            "success": True,
        })

    def action_unlink_order(self) -> None:
        p = self.pair
        if not p.order:
            self.notify("No order currently linked to this pair.", severity="information")
            return
        old_order_num = p.order.order_number
        p.order = None
        p.match_type = VehicleInstallationPair.MatchType.NONE
        p.match_score = None
        p.is_manual_override = True
        p.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        if p.verification_status in (
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.FAILED,
        ):
            p.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        p.operator_note = f"Order #{old_order_num} unlinked by operator in Plate Matcher."
        p.save(update_fields=[
            "order",
            "match_type",
            "match_score",
            "is_manual_override",
            "matched_via",
            "verification_status",
            "operator_note",
            "updated_at",
        ])
        SubmissionAuditLog.objects.create(
            pair=p,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.INFO,
            message=f"Order #{old_order_num} unlinked from plate {p.registration_number_detected} by operator in dialog.",
        )
        self.notify(f"Unlinked Order #{old_order_num} from {p.registration_number_detected}.")
        self.dismiss({
            "pair": p,
            "plate": p.registration_number_detected,
            "order": None,
            "unlinked": True,
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
        elif btn_id == "btn-unlink-plate-order":
            self.action_unlink_order()
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
        front_file = escape(os.path.basename(front.vault_file)) if (front and front.vault_file) else "[red]Missing[/red]"
        rear_file = escape(os.path.basename(rear.vault_file)) if (rear and rear.vault_file) else "[red]Missing[/red]"

        front_conf = f"{round(front.ocr_confidence, 2)}" if front and front.ocr_confidence else "—"
        rear_conf = f"{round(rear.ocr_confidence, 2)}" if rear and rear.ocr_confidence else "—"

        return (
            f"[bold white]Vehicle & Order Identification:[/bold white]\n"
            f"  • [b]Confirmed Plate:[/b]     [bold yellow]{escape(plate)}[/bold yellow] ([cyan]Matched via {escape(str(matched_via))}[/cyan])\n"
            f"  • [b]ITMS Order #:[/b]        [bold cyan]{escape(order_num)}[/bold cyan]\n"
            f"  • [b]Chassis / VIN:[/b]       {escape(vin)}\n"
            f"  • [b]Current ITMS Stage:[/b]  [green]{escape(stage)}[/green]\n\n"
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
            f_file = escape(os.path.basename(p.front_image.vault_file)) if (p.front_image and p.front_image.vault_file) else "Missing"
            r_file = escape(os.path.basename(p.rear_image.vault_file)) if (p.rear_image and p.rear_image.vault_file) else "Missing"
            comp_badge = "[bold green]✓ Ready[/bold green]" if p.is_complete else "[bold red]✗ Incomplete[/bold red]"
            method = getattr(p, "matched_via", "VISION")

            table.add_row(
                f"#{idx}",
                f"[bold yellow]{escape(p.registration_number_detected or '—')}[/bold yellow]",
                escape(p.order.order_number) if p.order else "—",
                f_file,
                r_file,
                comp_badge,
                escape(str(method)),
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
                f"Plate: [bold yellow]{escape(plate)}[/bold yellow]  (Order #{escape(order_num)})"
            )

            step = self.query_one("#batch-progress-step", Static)
            step.update(escape(step_detail))

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
            step.update(escape(message) if message else f"Completed {succeeded} of {self.total_orders} orders successfully.")

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
            f"Active Vault Root: [bold yellow]{escape(str(vault_service.get_vault_root()))}[/bold yellow]",
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


class ReportsDirectoryDialog(ModalScreen[Optional[str]]):
    """
    Interactive modal dialog allowing operators to choose where
    CSV shift reports and stock reconciliation ledgers are stored on disk.
    """
    BINDINGS = [
        Binding("escape", "dismiss_dialog", "Keep Current", priority=True),
        Binding("enter", "confirm_selection", "Confirm Reports Path"),
    ]

    def compose(self) -> ComposeResult:
        from core.services import config_service
        current_dir = str(config_service.get_setting("sync.default_export_directory", "exports"))
        abs_path = os.path.abspath(current_dir)

        header_lines = [
            "[bold cyan]═══ 📁 Shift CSV Reports & Export Storage ═══[/bold cyan]",
            "Directory where daily master shift reconciliation ledgers, unallocated kits dockets, and",
            "audit exports are written.",
            f"Active Directory: [bold yellow]{escape(abs_path)}[/bold yellow]",
            "[dim]Choose your desired exports folder below, or keep the default (exports).[/dim]",
        ]

        with Vertical(id="modal-dialog", classes="vault-location-modal"):
            yield Static("\n".join(header_lines), id="modal-header")
            with Horizontal(classes="vault-input-row"):
                yield Input(value=current_dir, placeholder="e.g. exports or D:/ITMS_Exports", id="input-reports-path")
                yield Button("📂 Browse Folder...", variant="primary", id="btn-browse-reports")
                yield Button("Default (exports)", variant="default", id="btn-default-reports")

            yield Static("[dim]Press Enter or click Confirm to save, or Esc to keep current location.[/dim]", id="vault-dialog-hint")

            with Horizontal(id="modal-footer"):
                yield Button("💾 Confirm & Use Directory [Enter]", variant="success", id="btn-confirm-reports")
                yield Button("Cancel [Esc]", variant="warning", id="btn-cancel-reports")

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)

    def action_confirm_selection(self) -> None:
        from core.services import config_service
        input_w = self.query_one("#input-reports-path", Input)
        new_path = input_w.value.strip() or "exports"
        try:
            os.makedirs(new_path, exist_ok=True)
        except Exception:
            pass
        config_service.set_user_setting("sync.default_export_directory", new_path)
        self.notify(f"Reports Directory configured: {new_path}", severity="information")
        self.dismiss(new_path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-confirm-reports":
            self.action_confirm_selection()
        elif btn_id == "btn-cancel-reports":
            self.action_dismiss_dialog()
        elif btn_id == "btn-default-reports":
            input_w = self.query_one("#input-reports-path", Input)
            input_w.value = "exports"
        elif btn_id == "btn-browse-reports":
            self._browse_directory()

    def _browse_directory(self) -> None:
        from core.services.file_dialog import prompt_native_directory_selection
        input_w = self.query_one("#input-reports-path", Input)
        chosen = prompt_native_directory_selection(
            initial_dir=input_w.value.strip() or None,
            title="Choose CSV Reports & Ledger Export Directory",
        )
        if chosen:
            input_w.value = chosen
            self.notify(f"Selected: {chosen}")


class YoloWeightsDialog(ModalScreen[Optional[str]]):
    """
    Developer modal dialog for choosing and testing YOLOv8 / YOLOv11 neural network weights
    for vehicle license plate localization and bounding box detection.
    """
    BINDINGS = [
        Binding("escape", "dismiss_dialog", "Keep Current", priority=True),
        Binding("enter", "confirm_selection", "Confirm Weights"),
    ]

    def compose(self) -> ComposeResult:
        from core.vision import detector
        from pathlib import Path
        from django.conf import settings

        current_weights = detector.get_yolo_weights()
        abs_weights = detector._resolve_weights_path(current_weights)
        file_exists = os.path.isfile(abs_weights)
        size_mb = f"{os.path.getsize(abs_weights) / (1024*1024):.1f} MB" if file_exists else "Not Found"
        status_color = "green" if file_exists else "red"

        # Discover available models in models/ directory
        base_dir = getattr(settings, "BASE_DIR", Path("."))
        models_dir = Path(base_dir) / "models"
        options = []
        if models_dir.exists():
            for p in sorted(models_dir.glob("*.pt")):
                options.append((f"{p.name} ({p.stat().st_size / (1024*1024):.1f} MB)", f"models/{p.name}"))
            for p in sorted(models_dir.glob("*.onnx")):
                options.append((f"{p.name} [ONNX] ({p.stat().st_size / (1024*1024):.1f} MB)", f"models/{p.name}"))

        if not options:
            options = [
                ("license-plate-finetune-v1n.pt (Nano)", "models/license-plate-finetune-v1n.pt"),
                ("license-plate-finetune-v1s.pt (Small)", "models/license-plate-finetune-v1s.pt"),
            ]

        # Add preset for HuggingFace pretrained model
        options.append(("morsetechlab/yolov11-license-plate-detection (HF Hub)", "morsetechlab/yolov11-license-plate-detection"))

        header_lines = [
            "[bold magenta]═══ 🧠 YOLOv8 / YOLOv11 Model Weights Selector ═══[/bold magenta]",
            "Select the neural detector checkpoint used for license plate localization on motorcycles.",
            f"Active Weights: [bold yellow]{escape(current_weights)}[/bold yellow]  │  Status: [bold {status_color}]{size_mb}[/bold {status_color}]",
            "[dim]Choose from verified local presets below, or browse for custom .pt / .onnx weights.[/dim]",
        ]

        with Vertical(id="modal-dialog", classes="vault-location-modal"):
            yield Static("\n".join(header_lines), id="modal-header")

            # Presets Dropdown
            with Horizontal(classes="vault-input-row"):
                yield Static("[b]Model Presets:[/b] ", classes="settings-label")
                yield Select(options=options, value=current_weights if any(opt[1] == current_weights for opt in options) else Select.BLANK, id="select-yolo-preset")

            # Manual / Browsed file path
            with Horizontal(classes="vault-input-row"):
                yield Input(value=current_weights, placeholder="e.g. models/license-plate-finetune-v1n.pt", id="input-yolo-path")
                yield Button("📂 Browse Weights (.pt / .onnx)...", variant="primary", id="btn-browse-weights")
                yield Button("Reset Default (v1n)", variant="default", id="btn-default-weights")

            yield Static("[dim]Selecting a weights file dynamically updates the vision pipeline without restarting.[/dim]", id="vault-dialog-hint")

            with Horizontal(id="modal-footer"):
                yield Button("💾 Confirm & Apply Weights [Enter]", variant="success", id="btn-confirm-weights")
                yield Button("Cancel [Esc]", variant="warning", id="btn-cancel-weights")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "select-yolo-preset" and event.value and event.value != Select.BLANK:
            input_w = self.query_one("#input-yolo-path", Input)
            input_w.value = str(event.value)

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)

    def action_confirm_selection(self) -> None:
        from core.vision import detector
        input_w = self.query_one("#input-yolo-path", Input)
        new_weights = input_w.value.strip() or "models/license-plate-finetune-v1n.pt"

        detector.set_yolo_weights(new_weights)
        self.notify(f"✓ Active YOLO weights updated to: {new_weights}", severity="information")
        self.dismiss(new_weights)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-confirm-weights":
            self.action_confirm_selection()
        elif btn_id == "btn-cancel-weights":
            self.action_dismiss_dialog()
        elif btn_id == "btn-default-weights":
            input_w = self.query_one("#input-yolo-path", Input)
            input_w.value = "models/license-plate-finetune-v1n.pt"
        elif btn_id == "btn-browse-weights":
            self._browse_weights()

    def _browse_weights(self) -> None:
        from core.services.file_dialog import prompt_native_file_selection
        from django.conf import settings
        input_w = self.query_one("#input-yolo-path", Input)
        initial = input_w.value.strip()
        init_dir = os.path.dirname(initial) if initial and os.path.exists(os.path.dirname(initial)) else "models"

        chosen = prompt_native_file_selection(
            initial_dir=init_dir,
            file_types=[
                ("YOLO Weights (*.pt;*.onnx;*.engine)", "*.pt;*.onnx;*.engine"),
                ("PyTorch Weights (*.pt)", "*.pt"),
                ("ONNX Models (*.onnx)", "*.onnx"),
                ("All Files (*.*)", "*.*"),
            ],
            title="Choose YOLOv8 / YOLOv11 Model Weights File",
        )
        if chosen:
            # If inside project dir, make relative for portability
            try:
                base_dir = getattr(settings, "BASE_DIR", ".")
                rel = os.path.relpath(chosen, base_dir)
                if not rel.startswith(".."):
                    chosen = rel.replace("\\", "/")
            except Exception:
                pass
            input_w.value = chosen
            self.notify(f"Selected model weights: {chosen}")


# ============================================================================
# Date Selection & Work Shift Modal
# ============================================================================

class DateSelectModal(ModalScreen[Optional[str]]):
    """
    Direct Date Selection Dialog for Reports & Shift Operations.
    Allows operator to:
    1. Select directly from recent active shift dates with counts and formatting.
    2. Click quick shortcut buttons: 'Today', 'Latest Active Shift', 'All Dates'.
    3. Type any 6-digit suffix (e.g. 260929) or full date (e.g. 29.09.2026 / 2026-09-29) in the input.
    """
    DEFAULT_CSS = """
    DateSelectModal {
        align: center middle;
    }
    DateSelectModal #modal-dialog {
        width: 80%;
        max-width: 90;
        height: 75%;
        max-height: 32;
        background: #0d1117;
        border: thick #0284c7;
        padding: 1 2;
    }
    DateSelectModal #modal-header {
        height: auto;
        margin-bottom: 1;
        background: #161b22;
        padding: 0 1;
        border-bottom: solid #30363d;
    }
    DateSelectModal #date-shortcuts-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }
    DateSelectModal #date-shortcuts-row Button {
        margin-right: 1;
    }
    DateSelectModal #date-input-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }
    DateSelectModal #input-date-manual {
        width: 1fr;
        margin-right: 1;
    }
    DateSelectModal #table-modal-dates {
        height: 1fr;
        min-height: 8;
        border: solid #30363d;
        margin-bottom: 1;
    }
    DateSelectModal #modal-footer {
        height: 3;
        align: right middle;
    }
    DateSelectModal #modal-footer Button {
        margin-left: 1;
        min-width: 16;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("enter", "confirm_selection", "Select Date"),
    ]

    def __init__(
        self,
        current_suffix: Optional[str] = None,
        available_dates: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.current_suffix = current_suffix
        self.available_dates = available_dates or []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                "[bold cyan]🗓️ SELECT OPERATIONAL WORK SHIFT DATE[/bold cyan]\n"
                "[dim]Double-click a date, select from list, or type a 6-digit suffix (DDMMYY) directly.[/dim]",
                id="modal-header",
            )
            with Horizontal(id="date-shortcuts-row"):
                yield Button("📅 Today's Date", variant="primary", id="btn-date-today")
                yield Button("⚡ Latest Shift", variant="warning", id="btn-date-latest")
                yield Button("🌐 All Dates Combined", variant="default", id="btn-date-all")

            with Horizontal(id="date-input-row"):
                yield Input(
                    placeholder="Type 6-digit suffix (e.g. 260929) or date (e.g. 29.09.2026)...",
                    id="input-date-manual",
                )
                yield Button("Apply Date", variant="success", id="btn-apply-manual-date")

            yield DataTable(id="table-modal-dates")

            with Horizontal(id="modal-footer"):
                yield Button("Select Highlighted [Enter]", variant="primary", id="btn-select-highlighted")
                yield Button("Cancel [Esc]", variant="error", id="btn-cancel-date")

    def on_mount(self) -> None:
        table = self.query_one("#table-modal-dates", DataTable)
        table.add_columns("Shift Date", "Suffix (DDMMYY)", "Orders Recorded", "Status / Details")
        table.cursor_type = "row"

        from core.services import report_service
        dates = self.available_dates or report_service.get_available_order_dates()

        today_suf = timezone.localdate().strftime("%d%m%y")
        today_fmt = timezone.localdate().strftime("%d.%m.%Y")

        has_today = any(d.get("suffix") == today_suf for d in dates)
        if not has_today:
            table.add_row(
                f"[bold green]{today_fmt} (Today)[/bold green]",
                f"[cyan]{today_suf}[/cyan]",
                "0 orders",
                "[dim]Current shift (New / In Progress)[/dim]",
                key=today_suf,
            )

        for d in dates:
            suf = d.get("suffix", "")
            fmt = d.get("formatted_date", suf)
            cnt = d.get("order_count", 0)
            is_active = (suf == self.current_suffix)
            is_today = (suf == today_suf)

            date_label = f"[bold green]{fmt} (Today)[/bold green]" if is_today else f"[bold white]{fmt}[/bold white]"
            if is_active:
                status_str = "[bold cyan]★ CURRENTLY SELECTED[/bold cyan]"
            else:
                status_str = "[dim]Completed shift[/dim]" if cnt > 0 else "[dim]No orders[/dim]"

            table.add_row(
                date_label,
                f"[cyan]{suf}[/cyan]",
                f"[bold yellow]{cnt:,}[/bold yellow] orders",
                status_str,
                key=suf,
            )

        self.query_one("#input-date-manual", Input).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key:
            selected_suf = str(event.row_key.value)
            self.dismiss(selected_suf)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        today_suf = timezone.localdate().strftime("%d%m%y")

        if btn_id == "btn-date-today":
            self.dismiss(today_suf)
        elif btn_id == "btn-date-latest":
            if self.available_dates:
                self.dismiss(self.available_dates[0].get("suffix", today_suf))
            else:
                self.dismiss(today_suf)
        elif btn_id == "btn-date-all":
            self.dismiss("ALL")
        elif btn_id == "btn-apply-manual-date":
            self._handle_manual_submit()
        elif btn_id == "btn-select-highlighted":
            table = self.query_one("#table-modal-dates", DataTable)
            if table.cursor_row is not None and table.row_count > 0:
                row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
                if row_key and row_key.value:
                    self.dismiss(str(row_key.value))
                    return
            self._handle_manual_submit()
        elif btn_id == "btn-cancel-date":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input-date-manual":
            self._handle_manual_submit()

    def _handle_manual_submit(self) -> None:
        from core.services import report_service
        raw_val = self.query_one("#input-date-manual", Input).value.strip()
        if not raw_val:
            self.dismiss(None)
            return

        parsed = report_service.parse_target_date_suffix(raw_val)
        if parsed:
            self.dismiss(parsed)
        elif raw_val.upper() in ("ALL", "TOTAL", "*"):
            self.dismiss("ALL")
        elif len(raw_val) == 6 and raw_val.isdigit():
            self.dismiss(raw_val)
        else:
            self.notify(f"⚠️ Unrecognized date format: '{raw_val}'. Please use DDMMYY (e.g. 260929) or DD.MM.YYYY.", severity="warning")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_confirm_selection(self) -> None:
        self._handle_manual_submit()


# ============================================================================
# Opening Balances & Shift Targets Modal Dialog
# ============================================================================

class OpeningTargetModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Focused dialog to adjust Opening Balances, Shift Targets, and Remarks for a given shift date.
    Replaces the legacy full StockManagerModal.
    """
    DEFAULT_CSS = """
    OpeningTargetModal {
        align: center middle;
    }
    OpeningTargetModal #modal-dialog {
        width: 64;
        max-width: 72;
        height: auto;
        max-height: 28;
        background: #0d1117;
        border: thick #0284c7;
        padding: 1 2;
    }
    OpeningTargetModal #modal-header {
        height: auto;
        margin-bottom: 1;
        background: #161b22;
        padding: 0 1;
        border-bottom: solid #30363d;
    }
    OpeningTargetModal .field-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }
    OpeningTargetModal .field-label {
        width: 26;
        align-vertical: middle;
    }
    OpeningTargetModal .field-input {
        width: 1fr;
    }
    OpeningTargetModal #lbl-modal-open-total {
        width: 1fr;
        text-align: right;
        align-vertical: middle;
        padding-right: 1;
    }
    OpeningTargetModal #modal-footer {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    OpeningTargetModal #modal-footer Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel / Esc", priority=True),
        Binding("enter", "save_values", "Save & Apply"),
    ]

    def __init__(self, target_date_suffix: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.target_date_suffix = target_date_suffix or timezone.localdate().strftime("%d%m%y")

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                f"[bold cyan]═══ 🎯 Opening Balances & Shift Target ═══[/bold cyan]\n"
                f"[dim]Shift Date: {self.target_date_suffix} │ Set opening physical counts and shift production target.[/dim]",
                id="modal-header",
            )
            with Horizontal(classes="field-row"):
                yield Button("⚡ Auto-Carry Previous Closing", id="btn-modal-autofill-open", variant="default")
                yield Static("", id="lbl-modal-open-total")

            with Horizontal(classes="field-row"):
                yield Static("[bold white]Opening PMO (Yellow):[/bold white]", classes="field-label")
                yield Input(placeholder="0", id="input-modal-open-pmo", type="integer", classes="field-input")

            with Horizontal(classes="field-row"):
                yield Static("[bold white]Opening PSV (White):[/bold white]", classes="field-label")
                yield Input(placeholder="0", id="input-modal-open-psv", type="integer", classes="field-input")

            with Horizontal(classes="field-row"):
                yield Static("[bold white]Scheduled Target (Bikes):[/bold white]", classes="field-label")
                yield Input(placeholder="0", id="input-modal-sched-target", type="integer", classes="field-input")

            with Horizontal(classes="field-row"):
                yield Static("[bold white]Shift Handover Remarks:[/bold white]", classes="field-label")
                yield Input(placeholder="Optional handover remarks...", id="input-modal-remarks", classes="field-input")

            with Horizontal(id="modal-footer"):
                yield Button("Cancel (Esc)", id="btn-modal-cancel", variant="default")
                yield Button("✓ Save Balances", id="btn-modal-save", variant="primary")

    def on_mount(self) -> None:
        from core.services import stock_monitoring_service
        try:
            summary = stock_monitoring_service.compute_daily_reconciliation(self.target_date_suffix)
            sched = summary.get("scheduled_target", 0)
            pmo_open = summary.get("pmo", {}).get("opening", 0)
            psv_open = summary.get("psv", {}).get("opening", 0)
            remarks = summary.get("shift_remarks", "")

            self.query_one("#input-modal-open-pmo", Input).value = str(pmo_open)
            self.query_one("#input-modal-open-psv", Input).value = str(psv_open)
            self.query_one("#input-modal-sched-target", Input).value = str(sched)
            self.query_one("#input-modal-remarks", Input).value = remarks or ""
            self._update_total_label(pmo_open + psv_open)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-modal-cancel":
            self.action_dismiss_modal()
        elif btn_id == "btn-modal-save":
            self.action_save_values()
        elif btn_id == "btn-modal-autofill-open":
            self._handle_autofill()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("input-modal-open-pmo", "input-modal-open-psv"):
            pmo_val = self.query_one("#input-modal-open-pmo", Input).value.strip()
            psv_val = self.query_one("#input-modal-open-psv", Input).value.strip()
            pmo = int(pmo_val) if pmo_val.isdigit() else 0
            psv = int(psv_val) if psv_val.isdigit() else 0
            self._update_total_label(pmo + psv)

    def _update_total_label(self, total: int) -> None:
        try:
            self.query_one("#lbl-modal-open-total", Static).update(
                f"[bold cyan]Total Opening: {total:,}[/bold cyan]"
            )
        except Exception:
            pass

    def _handle_autofill(self) -> None:
        from core.services import stock_monitoring_service
        try:
            carried = stock_monitoring_service.get_previous_shift_closing_balances(self.target_date_suffix)
            pmo = carried.get("pmo", 0)
            psv = carried.get("psv", 0)
            tot = carried.get("total", 0)
            prev_suf = carried.get("previous_date_suffix") or "previous shift"

            self.query_one("#input-modal-open-pmo", Input).value = str(pmo)
            self.query_one("#input-modal-open-psv", Input).value = str(psv)
            self._update_total_label(tot)

            if carried.get("source_ledger_exists"):
                self.notify(f"✓ Carried previous shift ({prev_suf}) closing: {tot:,} plates (PMO: {pmo}, PSV: {psv})", severity="information")
            else:
                self.notify(f"⚠️ No previous shift closing stock found before {self.target_date_suffix}.", severity="warning")
        except Exception as exc:
            self.notify(f"Error auto-filling: {exc}", severity="error")

    def action_save_values(self) -> None:
        from core.services import stock_monitoring_service
        try:
            target_raw = self.query_one("#input-modal-sched-target", Input).value.strip()
            s_target = int(target_raw) if target_raw.isdigit() else 0
            pmo_raw = self.query_one("#input-modal-open-pmo", Input).value.strip()
            o_pmo = int(pmo_raw) if pmo_raw.isdigit() else 0
            psv_raw = self.query_one("#input-modal-open-psv", Input).value.strip()
            o_psv = int(psv_raw) if psv_raw.isdigit() else 0
            remarks_text = self.query_one("#input-modal-remarks", Input).value.strip()

            stock_monitoring_service.set_scheduled_target(
                scheduled_target=s_target,
                target_date_suffix=self.target_date_suffix,
            )
            stock_monitoring_service.set_opening_balances(
                opening_pmo=o_pmo,
                opening_psv=o_psv,
                target_date_suffix=self.target_date_suffix,
            )
            if remarks_text:
                stock_monitoring_service.set_shift_remarks(
                    remarks=remarks_text,
                    target_date_suffix=self.target_date_suffix,
                )
            res = {
                "scheduled_target": s_target,
                "opening_pmo": o_pmo,
                "opening_psv": o_psv,
                "remarks": remarks_text,
            }
            self.notify(f"✓ Saved opening balances & target for shift {self.target_date_suffix}!", severity="information")
            self.dismiss(res)
        except Exception as exc:
            self.notify(f"Error saving balances: {exc}", severity="error")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# Backwards compatibility alias
StockManagerModal = OpeningTargetModal


# ============================================================================
# Excel Column Plate Paste & File Import Modal Dialog
# ============================================================================

class StockPasteModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Dedicated dialog for pasting multi-line / multi-column plate numbers from Excel
    or importing plate files (.xlsx, .xls, .csv, .txt).
    Bypasses terminal buffer bottlenecks using direct OS-level clipboard ingestion.
    """
    DEFAULT_CSS = """
    StockPasteModal {
        align: center middle;
    }
    StockPasteModal #modal-dialog {
        width: 90%;
        max-width: 108;
        height: 85%;
        max-height: 40;
        background: #0d1117;
        border: thick #0284c7;
        padding: 1 2;
    }
    StockPasteModal #modal-header {
        height: auto;
        margin-bottom: 1;
        background: #161b22;
        padding: 0 1;
        border-bottom: solid #30363d;
    }
    StockPasteModal .paste-actions-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }
    StockPasteModal .paste-actions-row Button {
        margin-right: 1;
    }
    StockPasteModal #lbl-paste-modal-stats {
        width: 1fr;
        text-align: right;
        align-vertical: middle;
        padding-right: 1;
    }
    StockPasteModal #text-paste-modal-content {
        height: 1fr;
        min-height: 12;
        border: solid #30363d;
        margin-bottom: 1;
    }
    StockPasteModal #modal-footer {
        height: 3;
        align: right middle;
    }
    StockPasteModal #modal-footer Button {
        margin-left: 1;
        min-width: 18;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel / Esc", priority=True),
        Binding("v", "paste_clipboard", "Paste Clipboard"),
        Binding("enter", "confirm_action", "Confirm & Process"),
    ]

    def __init__(
        self,
        mode: str = "stocktake",
        target_date_suffix: Optional[str] = None,
        initial_text: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.mode = mode.lower()
        self.target_date_suffix = target_date_suffix or timezone.localdate().strftime("%d%m%y")
        self.initial_text = initial_text

    def compose(self) -> ComposeResult:
        titles = {
            "stocktake": (
                "🔒 Safe Stock Taking & Safe Room Physical Audit",
                "Paste ~1,200 plate numbers from Excel or import file. Audits safe storage & calculates variance against book closing.",
                "🔒 Run Safe Stock Audit",
            ),
            "dispatch": (
                "📤 Batch Dispatch & Line Issue Verification",
                "Paste ~1,200 plate numbers from Excel or import file. Instant verification against warehouse stock blocks non-stock plates.",
                "⚡ Verify & Dispatch",
            ),
            "delivery": (
                "📥 Inbound Delivery Manifest Import",
                "Paste plate numbers from delivery manifest or packing list. Automatically logs stock receipts.",
                "📥 Ingest Inbound Plates",
            ),
            "transfers": (
                "🔄 Bond Transfer Manifest Import",
                "Paste plate numbers transferred between bond locations.",
                "🔄 Record Transfer Plates",
            ),
            "returns": (
                "↩️ Line Returns Manifest Import",
                "Paste plates returned from assembly line uninstalled (bike no-show, defect, cancelled).",
                "↩️ Record Returned Plates",
            ),
            "movements": (
                "🔄 Movements Manifest Import",
                "Paste plate numbers for deliveries, transfers, or returns. Staged for movement recording.",
                "🔄 Ingest Movement Plates",
            ),
        }
        title, desc, confirm_lbl = titles.get(
            self.mode,
            ("📋 Bulk Plate Ingestion", "Paste plate numbers or import from file.", "Process Plates")
        )
        self.confirm_label = confirm_lbl

        with Vertical(id="modal-dialog"):
            yield Static(
                f"[bold cyan]═══ {title} ═══[/bold cyan]\n"
                f"[dim]{desc}[/dim]  │  Shift: [bold yellow]{self.target_date_suffix}[/bold yellow]",
                id="modal-header",
            )

            with Horizontal(classes="paste-actions-row"):
                yield Button("📋 Paste from Clipboard (Excel)", variant="primary", id="btn-paste-clipboard")
                yield Button("📂 Import File (.xlsx/.csv/.txt)", variant="default", id="btn-import-file")
                yield Button("🧹 Clear Input", variant="default", id="btn-clear")
                yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-paste-modal-stats")

            yield TextArea(
                id="text-paste-modal-content",
                classes="stock-textarea",
            )

            with Horizontal(id="modal-footer"):
                yield Button("Cancel / Esc", variant="default", id="btn-cancel")
                yield Button(self.confirm_label, variant="success", id="btn-confirm")

    def on_mount(self) -> None:
        t_area = self.query_one("#text-paste-modal-content", TextArea)
        if self.initial_text:
            t_area.text = self.initial_text
            self._update_stats_display(self.initial_text)
        else:
            # Auto-attempt reading clipboard if it contains valid plates
            from core.services import clipboard_service
            clean_plates, dup_count, _, _ = clipboard_service.get_clipboard_plates()
            if clean_plates:
                t_area.text = "\n".join(clean_plates)
                self._update_stats_from_plates(clean_plates, dup_count)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "text-paste-modal-content":
            self._update_stats_display(event.text_area.text)

    def _update_stats_display(self, text: str) -> None:
        from core.services import stock_monitoring_service
        plates, dups, _ = stock_monitoring_service.parse_plate_input_with_stats(text)
        self._update_stats_from_plates(plates, dups)

    def _update_stats_from_plates(self, plates: List[str], dups: int) -> None:
        cnt = len(plates)
        dup_str = f" [yellow]({dups} dups pruned)[/yellow]" if dups > 0 else ""
        try:
            self.query_one("#lbl-paste-modal-stats", Static).update(
                f"[bold green]✓ Found: {cnt:,} plates[/bold green]{dup_str}" if cnt > 0 else "[dim]Staged: 0 plates[/dim]"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-cancel":
            self.action_dismiss_modal()
        elif bid == "btn-confirm":
            self.action_confirm_action()
        elif bid == "btn-paste-clipboard":
            self.action_paste_clipboard()
        elif bid == "btn-import-file":
            self._handle_import_file()
        elif bid == "btn-clear":
            self.query_one("#text-paste-modal-content", TextArea).text = ""
            self._update_stats_from_plates([], 0)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_paste_clipboard(self) -> None:
        from core.services import clipboard_service, stock_monitoring_service
        clean_plates, dup_count, _, _ = clipboard_service.get_clipboard_plates()
        if not clean_plates:
            self.notify("⚠️ Clipboard is empty or contains no valid license plate numbers.", severity="warning")
            return

        t_area = self.query_one("#text-paste-modal-content", TextArea)
        existing = t_area.text.strip()
        if existing:
            existing_plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(existing)
            merged = list(dict.fromkeys(existing_plates + clean_plates))
            t_area.text = "\n".join(merged)
            self._update_stats_from_plates(merged, dup_count)
        else:
            t_area.text = "\n".join(clean_plates)
            self._update_stats_from_plates(clean_plates, dup_count)

        self.notify(f"📋 Ingested {len(clean_plates):,} plates directly from Excel clipboard!", severity="information")

    def _handle_import_file(self) -> None:
        from core.services import file_dialog, clipboard_service, stock_monitoring_service
        file_path = file_dialog.prompt_plate_file_selection()
        if not file_path:
            return
        try:
            clean_plates, dup_count, _ = clipboard_service.read_plates_from_file(file_path)
            if not clean_plates:
                self.notify(f"No valid license plates found in {os.path.basename(file_path)}.", severity="warning")
                return

            t_area = self.query_one("#text-paste-modal-content", TextArea)
            existing = t_area.text.strip()
            if existing:
                existing_plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(existing)
                merged = list(dict.fromkeys(existing_plates + clean_plates))
                t_area.text = "\n".join(merged)
                self._update_stats_from_plates(merged, dup_count)
            else:
                t_area.text = "\n".join(clean_plates)
                self._update_stats_from_plates(clean_plates, dup_count)

            self.notify(f"📂 Imported {len(clean_plates):,} plates from {os.path.basename(file_path)}!", severity="information")
        except Exception as exc:
            self.notify(f"Error importing file: {exc}", severity="error")

    def action_confirm_action(self) -> None:
        from core.services import stock_monitoring_service
        raw_text = self.query_one("#text-paste-modal-content", TextArea).text.strip()
        plates, dup_count, _ = stock_monitoring_service.parse_plate_input_with_stats(raw_text)
        if not plates:
            self.notify("Please paste or import license plates before confirming.", severity="warning")
            return

        self.dismiss({
            "action": "confirm",
            "mode": self.mode,
            "plates": plates,
            "raw_text": raw_text,
            "count": len(plates),
            "dup_count": dup_count,
        })




