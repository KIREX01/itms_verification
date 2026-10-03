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
# Stock Monitoring & Daily Plate Reconciliation Manager Modal
# ============================================================================

class StockManagerModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Interactive Stock Monitoring & Bond Reconciliation Modal Dialog.
    Allows operators to:
    1. Scan / paste plates dispatched to the installation line (PSV White & PMO Yellow) with instant duplicate prevention.
    2. Record inbound deliveries received from supplier/factory with rapid scan and auto-kit creation.
    3. Record Bond Transfers In and Bond Transfers Out.
    4. Record uninstalled returned plates (bike no-show, defective plate, cancelled).
    5. Feed in manual Scheduled targets and Opening Stock balances.
    6. View the real-time PSV vs PMO Bond Reconciliation Table and export to CSV.
    """
    DEFAULT_CSS = """
    StockManagerModal {
        align: center middle;
    }
    StockManagerModal #modal-dialog {
        width: 95%;
        max-width: 122;
        height: 92%;
        max-height: 48;
        background: #0d1117;
        border: thick #0284c7;
        padding: 1 2;
    }
    StockManagerModal #modal-header {
        height: auto;
        margin-bottom: 1;
        background: #161b22;
        padding: 0 1;
        border-bottom: solid #30363d;
    }
    StockManagerModal #stock-tabbed-content {
        height: 1fr;
    }
    StockManagerModal .stock-tab-pane {
        height: 1fr;
        padding: 1 0;
    }
    StockManagerModal .stock-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }
    StockManagerModal .stock-input-field {
        width: 1fr;
        margin-right: 1;
    }
    StockManagerModal .stock-staged-badge {
        width: auto;
        min-width: 18;
        padding: 0 1;
        align-vertical: middle;
        text-align: right;
    }
    StockManagerModal .stock-textarea {
        height: 7;
        border: solid #30363d;
        margin-bottom: 1;
    }
    StockManagerModal #table-modal-stock-report {
        height: 1fr;
        min-height: 10;
        border: solid #30363d;
        margin-bottom: 1;
    }
    StockManagerModal #table-modal-deliv-notes {
        height: 6;
        min-height: 4;
        border: solid #30363d;
        margin-bottom: 1;
    }
    StockManagerModal #lbl-stocktake-summary {
        height: auto;
        margin-top: 1;
        padding: 0 1;
    }
    StockManagerModal .stock-label-fixed {
        width: 25;
        min-width: 25;
        align-vertical: middle;
        padding-right: 1;
    }
    StockManagerModal .stock-section-title {
        height: auto;
        margin-top: 1;
        margin-bottom: 0;
        padding: 0 1;
    }
    StockManagerModal #lbl-sched-status-card {
        height: 3;
        background: #161b22;
        border: solid #0284c7;
        padding: 0 1;
        margin-bottom: 1;
        align-vertical: middle;
    }
    StockManagerModal .stock-textarea-remarks {
        height: 4;
        border: solid #30363d;
        margin-bottom: 1;
    }
    StockManagerModal #modal-footer {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    StockManagerModal #modal-footer Button {
        margin-left: 1;
        min-width: 16;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close / Cancel", priority=True),
        Binding("p", "show_phone_scanner", "Phone Scanner"),
        Binding("e", "export_csv", "Export CSV"),
        Binding("r", "refresh_stock", "Refresh"),
    ]

    def __init__(self, target_date_suffix: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.target_date_suffix = target_date_suffix or timezone.localdate().strftime("%d%m%y")
        self._cached_recon: Optional[Dict[str, Any]] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(id="modal-header")

            with TabbedContent(id="stock-tabbed-content"):
                # TAB 1: Live Stock Report Table (PSV vs PMO)
                with TabPane("📊 Stock Report (PSV / PMO)", id="tab-report-view"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold cyan]📋 Daily Bond Physical Stock & Reconciliation Balance[/bold cyan]  │  "
                            "[dim]Opening + Received + Transfer In - Transfer Out - Installed = Closing Balance[/dim]",
                            id="stock-report-intro",
                        )
                        with Horizontal(classes="stock-row", id="row-stock-kits-prep"):
                            yield Button("📦 Sync & Prep Morning Stock Kits", variant="success", id="btn-sync-stock-kits")
                            yield Static("[dim]Safe Room Ready: [bold green]Checking...[/bold green][/dim]", id="lbl-stock-ready-badge", classes="stock-staged-badge")
                        yield DataTable(id="table-modal-stock-report")
                        yield Static(id="stock-floor-summary")

                # TAB 2: Dispatched (Taking Out to Line)
                with TabPane("📤 Dispatch (Line Out)", id="tab-dispatch-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold yellow]📤 Record Plates Dispatched to Assembly Line[/bold yellow]  │  "
                            "[dim]Scan plate QR or paste multi-line list (e.g. UMA711PW, UMA993PW...)[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("⚡ Prep & Provision Stock Kits", variant="success", id="btn-sync-stock-kits-tab2")
                            yield Static("[dim]Ready in Stock: [bold green]Checking...[/bold green][/dim]", id="lbl-dispatch-stock-status", classes="stock-staged-badge")
                        with Horizontal(classes="stock-row"):
                            yield Select(
                                [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                value="PSV",
                                id="sel-dispatch-category",
                                prompt="Select Plate Category",
                            )
                            yield Input(
                                placeholder="⚡ Rapid scan plate QR code [Enter to add]...",
                                id="input-dispatch-single",
                                classes="stock-input-field",
                            )
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-dispatch-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-dispatch-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("💾 Record Dispatched Plates [Enter]", variant="primary", id="btn-save-dispatch")
                            yield Button("Clear Batch", variant="default", id="btn-clear-dispatch")

                # TAB 3: Inbound Deliveries
                with TabPane("📥 Inbound Delivery", id="tab-delivery-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold green]📥 Record Inbound Delivery Note Manifest[/bold green]  │  "
                            "[dim]Date-anchored deliveries for shift  │  Increases warehouse stock (+Received)[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Delivery Identifier:[/bold white] ", classes="stock-label-fixed")
                            yield Input(
                                placeholder="Auto: DN-YYYYMMDD-01",
                                id="input-deliv-number",
                                classes="stock-input-field",
                            )
                            yield Static("[bold white]Category:[/bold white] ", classes="stock-label-fixed")
                            yield Select(
                                [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                value="PSV",
                                id="sel-deliv-category",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Note Photo / File:[/bold white] ", classes="stock-label-fixed")
                            yield Input(
                                placeholder="Optional image file path (or uploaded from phone companion)",
                                id="input-deliv-photo-path",
                                classes="stock-input-field",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="⚡ Rapid scan plate QR / barcode with USB scanner [Enter to add]...",
                                id="input-deliv-single",
                                classes="stock-input-field",
                            )
                            yield Static("[bold green]📦 Scanned: 0 plates[/bold green]", id="lbl-deliv-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-deliv-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("📥 Ingest Delivery into Stock", variant="success", id="btn-save-delivery")
                            yield Button("Clear Scans", variant="default", id="btn-clear-deliv")
                            yield Button("👁️ View Selected Delivery Note & Plates", variant="primary", id="btn-view-deliv-note")
                        yield Static("[bold white]📋 Stored Delivery Notes for Shift Work Date (Select row to view details & plates):[/bold white]")
                        yield DataTable(id="table-modal-deliv-notes")

                # TAB 4: Bond Transfers (In / Out)
                with TabPane("🔄 Bond Transfers", id="tab-transfers-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold magenta]🔄 Record Inter-Bond Transfers (Transfer In / Transfer Out)[/bold magenta]  │  "
                            "[dim]Transfer In (+Stock) from another bond  │  Transfer Out (-Stock) to another bond[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Select(
                                [
                                    ("Bond Transfer In (Received from another bond)", "TRANSFER_IN"),
                                    ("Bond Transfer Out (Sent to another bond)", "TRANSFER_OUT"),
                                ],
                                value="TRANSFER_IN",
                                id="sel-transfer-type",
                            )
                            yield Select(
                                [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                value="PSV",
                                id="sel-transfer-category",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="Other Bond Location / Name (e.g. Kampala Central Bond)",
                                id="input-transfer-bond",
                                classes="stock-input-field",
                            )
                            yield Input(
                                placeholder="Plates Count (e.g. 50)",
                                id="input-transfer-count",
                                classes="stock-input-field",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="⚡ Rapid scan transfer plate QR [Enter to add]...",
                                id="input-transfer-single",
                                classes="stock-input-field",
                            )
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-transfer-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-transfer-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("🔄 Record Bond Transfer", variant="primary", id="btn-save-transfer")
                            yield Button("Clear Transfer", variant="default", id="btn-clear-transfer")

                # TAB 5: Returns (Line In)
                with TabPane("↩️ Returns (Line In)", id="tab-returns-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold red]↩️ Record Uninstalled Plates Returned to Stock[/bold red]  │  "
                            "[dim]Scan plate QR or paste returned plates (Bike No-Show, Defective, Cancelled)[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Select(
                                [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                value="PSV",
                                id="sel-return-category",
                            )
                            yield Select(
                                [
                                    ("Bike No-Show (Owner did not arrive)", "BIKE_NO_SHOW"),
                                    ("Defective Plate (Damaged / Bad Print)", "DEFECTIVE_PLATE"),
                                    ("Cancelled Order", "CANCELLED_ORDER"),
                                    ("Line Rollover (Shift End)", "LINE_ROLLOVER"),
                                ],
                                value="BIKE_NO_SHOW",
                                id="sel-return-reason",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="⚡ Rapid scan returned plate QR code [Enter to add]...",
                                id="input-return-single",
                                classes="stock-input-field",
                            )
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-return-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-return-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("↩️ Record Returned Plates", variant="warning", id="btn-save-return")
                            yield Button("Clear Returns", variant="default", id="btn-clear-return")

                # TAB 6: Scheduled Installation Target & Opening Balance Entry
                with TabPane("🎯 Scheduled & Opening Balance", id="tab-scheduled-pane"):
                    with Vertical(classes="stock-tab-pane", id="pane-scheduled-container"):
                        yield Static(
                            "[bold cyan]🎯 Scheduled Installation Target & Opening Balance Entry[/bold cyan]  │  "
                            "[dim]Operational Target for shift (does not alter physical stock)  │  Opening is physical safe inventory[/dim]",
                            id="header-scheduled-pane"
                        )

                        # SECTION A: Scheduled Installation Target (Single Input)
                        yield Static(
                            "[bold yellow]1. 🎯 SCHEDULED INSTALLATION TARGET (Single Combined Target)[/bold yellow]\n"
                            "[dim]Enter the total planned target plates to be installed under bond today (covers both Private Yellow & Public White). Operational goal only.[/dim]",
                            classes="stock-section-title"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Scheduled Target (Total):[/bold white] ", classes="stock-label-fixed")
                            yield Input(value="0", placeholder="e.g. 500 (Single target for shift)", id="input-sched-target", classes="stock-input-field")

                        yield Static(
                            "[bold green]📊 Live Status:[/bold green] Target: 0  │  Installed: 0  │  Daily Performance: 0%  │  Backlog: 0",
                            id="lbl-sched-status-card"
                        )

                        # SECTION B: Physical Opening Stock Balances
                        yield Static(
                            "\n[bold green]2. 📦 PHYSICAL OPENING STOCK BALANCES (Safe Room / Storage Box at 06:00)[/bold green]\n"
                            "[dim]Physical number plates in safe room at start of shift. Formula: Closing = Opening + Received + Transfer In - Transfer Out - Installed.[/dim]",
                            classes="stock-section-title"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]PRIVATE (Yellow):[/bold white] ", classes="stock-label-fixed")
                            yield Input(value="0", placeholder="Opening PMO count", id="input-open-pmo", classes="stock-input-field")
                            yield Static("[bold white]PUBLIC (White):[/bold white] ", classes="stock-label-fixed")
                            yield Input(value="0", placeholder="Opening PSV count", id="input-open-psv", classes="stock-input-field")
                            yield Static("[bold cyan]Total Opening: 0[/bold cyan]", id="lbl-open-total", classes="stock-staged-badge")

                        # SECTION C: Shift Remarks
                        yield Static(
                            "\n[bold white]3. 📝 SHIFT REMARKS (Handover Notes / Audit Remarks from Official Report):[/bold white]",
                            classes="stock-section-title"
                        )
                        yield TextArea(
                            id="text-stock-remarks",
                            classes="stock-textarea-remarks",
                        )

                        with Horizontal(classes="stock-row"):
                            yield Button("💾 Save Scheduled Target, Balances & Remarks", variant="success", id="btn-save-scheduled")
                            yield Button("🔄 Auto-Carry Previous Day Closing Stock", variant="primary", id="btn-autofill-opening")

                # TAB 7: Safe Room Stock Taking & Physical Audit
                with TabPane("🔒 Safe Stock Taking", id="tab-stocktake-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold cyan]🔒 Physical Stock Taking & Safe Room Audit[/bold cyan]  │  "
                            "[dim]Physically count/scan all number plates currently inside the safe room or storage box to reconcile with book stock[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="⚡ Rapid scan safe room plate QR [Enter to add]...",
                                id="input-stocktake-single",
                                classes="stock-input-field",
                            )
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-stocktake-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-stocktake-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Button("🔒 Perform Safe Stock Taking Audit", variant="primary", id="btn-save-stocktake")
                            yield Button("Clear Scans", variant="default", id="btn-clear-stocktake")
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Or enter manual physical count:[/bold white] ", classes="stock-input-field")
                            yield Input(value="0", placeholder="e.g. 850", id="input-stocktake-manual-count", classes="stock-input-field")
                            yield Button("💾 Set Physical Count", variant="success", id="btn-stocktake-set-manual")
                        yield Static(id="lbl-stocktake-summary")

            with Horizontal(id="modal-footer"):
                yield Button("📱 Phone Scanner [P]", variant="warning", id="btn-stock-phone-scanner")
                yield Button("📑 Export CSV [E]", variant="default", id="btn-stock-export")
                yield Button("🔄 Refresh [R]", variant="primary", id="btn-stock-refresh")
                yield Button("Close [Esc]", variant="error", id="btn-stock-close")

    def on_mount(self) -> None:
        table = self.query_one("#table-modal-stock-report", DataTable)
        table.add_columns("DESCRIPTION", "PRIVATE (PMO)", "PUBLIC (PSV)", "Total Combined", "REMARKS / Formula Note")
        table.cursor_type = "row"

        table_deliv = self.query_one("#table-modal-deliv-notes", DataTable)
        table_deliv.add_columns("Delivery Identifier", "Category", "Plates Count", "Note Photo", "Delivered Plates Sample", "Logged At")
        table_deliv.cursor_type = "row"

        self.action_refresh_stock()

    def action_refresh_stock(self) -> None:
        """Fetches fresh reconciliation from stock service and updates all widgets."""
        from core.services import stock_monitoring_service
        try:
            recon = stock_monitoring_service.compute_daily_reconciliation(self.target_date_suffix)
            self._cached_recon = recon
            self._render_header(recon)
            self._render_report_table(recon)
            self._render_delivery_notes_table()
            self._load_inputs(recon)

            from core.models import InstallationKit
            new_cnt = InstallationKit.objects.filter(status__iexact="New").count()
            try:
                self.query_one("#lbl-stock-ready-badge", Static).update(
                    f"[bold green]📦 Safe Room Ready: {new_cnt:,} kits ('New' in warehouse stock)[/bold green]"
                )
            except Exception:
                pass
            try:
                self.query_one("#lbl-dispatch-stock-status", Static).update(
                    f"[bold green]📦 Ready in Stock: {new_cnt:,} 'New' kits[/bold green]"
                )
            except Exception:
                pass
        except Exception as exc:
            self.notify(f"Stock reconciliation error: {exc}", severity="error")

    def _render_delivery_notes_table(self) -> None:
        from core.services import stock_monitoring_service
        try:
            table = self.query_one("#table-modal-deliv-notes", DataTable)
            table.clear()
            notes = stock_monitoring_service.get_delivery_notes_for_date(self.target_date_suffix)
            if not notes:
                table.add_row("No delivery notes logged for this shift", "—", "—", "—", "—", "—")
                return
            for n in notes:
                cat_badge = "[bold white on dark_blue] PSV [/]" if n["plate_category"] == "PSV" else "[bold black on gold1] PMO [/]"
                has_photo = "[bold green]✓ Attached[/bold green]" if n.get("has_image") else "[dim]No photo[/dim]"
                plates = n.get("plates", [])
                sample = ", ".join(plates[:4]) + (f" (+{len(plates)-4} more)" if len(plates) > 4 else "")
                table.add_row(
                    f"[bold green]{n['delivery_number']}[/bold green]",
                    cat_badge,
                    f"[bold cyan]{n['total_plates_count']:,}[/bold cyan]",
                    has_photo,
                    f"[dim]{sample}[/dim]",
                    f"[dim]{n['created_at']}[/dim]",
                    key=str(n["id"]),
                )
        except Exception:
            pass


    def _render_header(self, r: Dict[str, Any]) -> None:
        fmt_date = r.get("formatted_date", "")
        suf = r.get("work_date_suffix", "")
        wh = r.get("storage_bond_name") or r.get("warehouse_name") or "AGM SPIRO/8/2"
        self.query_one("#modal-header", Static).update(
            f"[bold cyan]═══ 📦 ITMS BOND PHYSICAL STOCK & RECONCILIATION MANAGER ═══[/bold cyan]\n"
            f"[bold white]Shift Work Date:[/bold white] [bold yellow]{fmt_date}[/bold yellow] ([cyan]{suf}[/cyan])  │  "
            f"[bold white]Warehouse / Bond:[/bold white] [bold white]{wh}[/bold white]  │  "
            f"[dim]Synced: {r.get('last_reconciled_at', '')}[/dim]"
        )

    def _render_report_table(self, r: Dict[str, Any]) -> None:
        table = self.query_one("#table-modal-stock-report", DataTable)
        table.clear()

        rows = r.get("report_table", {}).get("rows", [])
        for row in rows:
            metric = row.get("metric", "")
            pmo = row.get("pmo", 0)
            psv = row.get("psv", 0)
            tot = row.get("total", 0)
            note = row.get("note", "")

            # Highlight specific rows
            if "Closing Balance" in metric:
                m_str = f"[bold green]{metric}[/bold green]"
                y_str = f"[bold green]{pmo:,}[/bold green]" if isinstance(pmo, int) else f"[bold green]{pmo}[/bold green]"
                p_str = f"[bold green]{psv:,}[/bold green]" if isinstance(psv, int) else f"[bold green]{psv}[/bold green]"
                t_str = f"[bold white on dark_green] {tot:,} [/bold white on dark_green]" if isinstance(tot, int) else f"[bold white on dark_green] {tot} [/bold white on dark_green]"
            elif "SCHEDULED" in metric or "Scheduled" in metric:
                m_str = f"[bold cyan]{metric}[/bold cyan]"
                y_str = f"[bold cyan]{pmo:,}[/bold cyan]" if isinstance(pmo, int) else f"[bold cyan]{pmo}[/bold cyan]"
                p_str = f"[bold cyan]{psv:,}[/bold cyan]" if isinstance(psv, int) else f"[bold cyan]{psv}[/bold cyan]"
                t_str = f"[bold cyan]{tot:,}[/bold cyan]" if isinstance(tot, int) else f"[bold cyan]{tot}[/bold cyan]"
            elif "Installed" in metric:
                m_str = f"[bold yellow]{metric}[/bold yellow]"
                y_str = f"[bold yellow]{pmo:,}[/bold yellow]" if isinstance(pmo, int) else f"[bold yellow]{pmo}[/bold yellow]"
                p_str = f"[bold yellow]{psv:,}[/bold yellow]" if isinstance(psv, int) else f"[bold yellow]{psv}[/bold yellow]"
                t_str = f"[bold yellow]{tot:,}[/bold yellow]" if isinstance(tot, int) else f"[bold yellow]{tot}[/bold yellow]"
            elif "perfomance" in metric.lower() or "performance" in metric.lower():
                m_str = f"[bold magenta]{metric}[/bold magenta]"
                y_str = f"{pmo}"
                p_str = f"{psv}"
                t_str = f"[bold magenta]{tot}[/bold magenta]"
            elif "Backlog" in metric:
                color = "red" if (isinstance(tot, int) and tot > 0) else "green"
                m_str = f"[{color}]{metric}[/{color}]"
                y_str = f"[{color}]{pmo:,}[/{color}]" if isinstance(pmo, int) else f"[{color}]{pmo}[/{color}]"
                p_str = f"[{color}]{psv:,}[/{color}]" if isinstance(psv, int) else f"[{color}]{psv}[/{color}]"
                t_str = f"[{color}]{tot:,}[/{color}]" if isinstance(tot, int) else f"[{color}]{tot}[/{color}]"
            elif "Variance" in metric:
                color = "green" if (isinstance(tot, int) and tot >= 0) else "red"
                m_str = f"[{color}]{metric}[/{color}]"
                y_str = f"[{color}]{pmo:+d}[/{color}]" if isinstance(pmo, int) else f"[dim]{pmo}[/dim]"
                p_str = f"[{color}]{psv:+d}[/{color}]" if isinstance(psv, int) else f"[dim]{psv}[/dim]"
                t_str = f"[{color}]{tot:+d}[/{color}]" if isinstance(tot, int) else f"[{color}]{tot}[/{color}]"
            else:
                m_str = f"[bold white]{metric}[/bold white]"
                y_str = f"{pmo:,}" if isinstance(pmo, int) else str(pmo)
                p_str = f"{psv:,}" if isinstance(psv, int) else str(psv)
                t_str = f"[bold white]{tot:,}[/bold white]" if isinstance(tot, int) else str(tot)

            table.add_row(m_str, y_str, p_str, t_str, f"[dim]{note}[/dim]")

        floor = r.get("floor_operations", {})
        unalloc = floor.get("unallocated_discrepancy", 0)
        unalloc_style = "[bold red]" if unalloc > 0 else "[bold green]"
        self.query_one("#stock-floor-summary", Static).update(
            f" [b]Floor Operations:[/b] Dispatched: [cyan]{floor.get('dispatched_count', 0)}[/cyan]  │  "
            f"Returned: [yellow]{floor.get('returned_count', 0)}[/yellow]  │  "
            f"Net on Line: [white]{floor.get('net_dispatched', 0)}[/white]  │  "
            f"Pending Orders: [gold1]{floor.get('itms_pending_count', 0)}[/gold1]  │  "
            f"{unalloc_style}⚠️ Unallocated Discrepancy: {unalloc} plates{unalloc_style}"
        )

    def _load_inputs(self, r: Dict[str, Any]) -> None:
        try:
            from core.models import DailyStockLedger
            work_d = r.get("work_date")
            ledger = DailyStockLedger.objects.filter(work_date=work_d).first()
            if ledger:
                target_val = ledger.scheduled_total if ledger.scheduled_total > 0 else (ledger.scheduled_psv + ledger.scheduled_pmo)
                try:
                    self.query_one("#input-sched-target", Input).value = str(target_val)
                except Exception:
                    pass
                try:
                    self.query_one("#input-open-pmo", Input).value = str(ledger.opening_balance_pmo)
                    self.query_one("#input-open-psv", Input).value = str(ledger.opening_balance_psv)
                    tot_open = ledger.opening_balance_pmo + ledger.opening_balance_psv
                    self.query_one("#lbl-open-total", Static).update(f"[bold cyan]Total Opening: {tot_open:,}[/bold cyan]")
                except Exception:
                    pass
                try:
                    self.query_one("#text-stock-remarks", TextArea).text = ledger.notes or ""
                except Exception:
                    pass

                # Live status banner
                sched_sum = r.get("scheduled_summary", {})
                inst_tot = sched_sum.get("installed_total", 0)
                inst_pmo = sched_sum.get("installed_pmo", 0)
                inst_psv = sched_sum.get("installed_psv", 0)
                perf = sched_sum.get("daily_performance_pct", 0.0)
                backlog = sched_sum.get("backlog_level", 0)
                try:
                    self.query_one("#lbl-sched-status-card", Static).update(
                        f"[bold green]📊 Live Status:[/bold green] Target: [bold cyan]{target_val:,}[/bold cyan]  │  "
                        f"Installed: [bold yellow]{inst_tot:,}[/bold yellow] (PRIVATE: {inst_pmo}, PUBLIC: {inst_psv})  │  "
                        f"Performance: [bold magenta]{perf}%[/bold magenta]  │  "
                        f"Backlog: [bold red]{backlog:,} remaining[/bold red]"
                    )
                except Exception:
                    pass

                try:
                    self.query_one("#input-stocktake-manual-count", Input).value = str(ledger.physical_count) if ledger.physical_count is not None else ""
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from core.services import stock_monitoring_service
            auto_ref = stock_monitoring_service.generate_delivery_note_reference(self.target_date_suffix)
            deliv_inp = self.query_one("#input-deliv-number", Input)
            deliv_inp.placeholder = f"Auto: {auto_ref}"
            if not deliv_inp.value.strip():
                deliv_inp.value = auto_ref
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-stock-close":
            self.action_dismiss_modal()
        elif btn_id == "btn-stock-phone-scanner":
            self.action_show_phone_scanner()
        elif btn_id == "btn-stock-refresh":
            self.action_refresh_stock()
        elif btn_id == "btn-stock-export":
            self.action_export_csv()
        elif btn_id == "btn-save-dispatch":
            self._handle_save_dispatch()
        elif btn_id == "btn-clear-dispatch":
            self.query_one("#text-dispatch-bulk", TextArea).text = ""
            self.query_one("#input-dispatch-single", Input).value = ""
            try:
                self.query_one("#lbl-dispatch-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
        elif btn_id == "btn-save-delivery":
            self._handle_save_delivery()
        elif btn_id == "btn-clear-deliv":
            self.query_one("#text-deliv-bulk", TextArea).text = ""
            try:
                self.query_one("#input-deliv-single", Input).value = ""
                self.query_one("#input-deliv-number", Input).value = ""
                self.query_one("#input-deliv-paper-ref", Input).value = ""
                self.query_one("#lbl-deliv-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
        elif btn_id == "btn-save-transfer":
            self._handle_save_transfer()
        elif btn_id == "btn-clear-transfer":
            self.query_one("#text-transfer-bulk", TextArea).text = ""
            try:
                self.query_one("#input-transfer-single", Input).value = ""
                self.query_one("#lbl-transfer-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
        elif btn_id == "btn-save-return":
            self._handle_save_return()
        elif btn_id == "btn-clear-return":
            self.query_one("#text-return-bulk", TextArea).text = ""
            try:
                self.query_one("#input-return-single", Input).value = ""
                self.query_one("#lbl-return-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
        elif btn_id == "btn-save-scheduled":
            self._handle_save_scheduled()
        elif btn_id == "btn-autofill-opening":
            self._handle_autofill_opening()
        elif btn_id == "btn-save-stocktake":
            self._handle_save_stocktake()
        elif btn_id == "btn-clear-stocktake":
            self.query_one("#text-stocktake-bulk", TextArea).text = ""
            try:
                self.query_one("#input-stocktake-single", Input).value = ""
                self.query_one("#lbl-stocktake-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
        elif btn_id == "btn-stocktake-set-manual":
            self._handle_set_manual_physical_count()
        elif btn_id in ("btn-sync-stock-kits", "btn-sync-stock-kits-tab2"):
            self._handle_sync_stock_kits()

    @work(thread=True)
    def _handle_sync_stock_kits(self) -> None:
        if getattr(self, "_is_syncing_kits", False):
            self.app.call_from_thread(self.notify, "Kit sync is already in progress...", severity="warning")
            return
        self._is_syncing_kits = True

        def _update_btn_state(text: str, disabled: bool):
            for bid in ("#btn-sync-stock-kits", "#btn-sync-stock-kits-tab2"):
                try:
                    btn = self.query_one(bid, Button)
                    btn.label = text
                    btn.disabled = disabled
                except Exception:
                    pass

        self.app.call_from_thread(_update_btn_state, "⏳ Syncing Kits...", True)
        self.app.call_from_thread(self.notify, "🔄 Synchronizing installation kits from ITMS, deliveries, and safe audits...")

        from core.services import kit_provisioning_service

        def _on_progress(msg: str):
            if hasattr(self.app, "log_message"):
                self.app.call_from_thread(self.app.log_message, msg, level="ITMS")

        try:
            res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=None,
                sync_itms=True,
                max_pages=35,
                log_callback=_on_progress,
            )
            count = res.get("new_kits_ready_count", 0)
            wh = res.get("warehouse_facility", "Warehouse Stock")
            itms_cnt = res.get("itms_kits_synced", 0)
            pages = res.get("itms_pages_crawled", 0)
            created = res.get("kits_created", 0)
            updated = res.get("kits_updated", 0)
            self.app.call_from_thread(
                self.notify,
                f"✓ Synced kits: {itms_cnt} from ITMS ({pages} pgs), {created} created, {updated} updated ({count} ready as 'New' in {wh})",
                severity="information",
                timeout=8,
            )
            self.app.call_from_thread(self.action_refresh_stock)
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Error syncing kits: {exc}", severity="error")
        finally:
            self._is_syncing_kits = False
            self.app.call_from_thread(_update_btn_state, "📦 Sync & Prep Stock Kits", False)

    def on_input_changed(self, event: Input.Changed) -> None:
        inp_id = event.input.id
        if inp_id in ("input-open-pmo", "input-open-psv"):
            try:
                pmo_val = self.query_one("#input-open-pmo", Input).value.strip()
                psv_val = self.query_one("#input-open-psv", Input).value.strip()
                pmo = int(pmo_val) if pmo_val.isdigit() else 0
                psv = int(psv_val) if psv_val.isdigit() else 0
                self.query_one("#lbl-open-total", Static).update(f"[bold cyan]Total Opening: {pmo + psv:,}[/bold cyan]")
            except Exception:
                pass
        elif inp_id == "input-sched-target":
            try:
                t_val = event.value.strip()
                target_cnt = int(t_val) if t_val.isdigit() else 0
                sched_sum = (self._cached_recon or {}).get("scheduled_summary", {})
                inst_tot = sched_sum.get("installed_total", 0)
                inst_pmo = sched_sum.get("installed_pmo", 0)
                inst_psv = sched_sum.get("installed_psv", 0)
                perf = round((inst_tot / target_cnt) * 100.0, 1) if target_cnt > 0 else 0.0
                backlog = max(0, target_cnt - inst_tot)
                self.query_one("#lbl-sched-status-card", Static).update(
                    f"[bold green]📊 Live Status:[/bold green] Target: [bold cyan]{target_cnt:,}[/bold cyan]  │  "
                    f"Installed: [bold yellow]{inst_tot:,}[/bold yellow] (PRIVATE: {inst_pmo}, PUBLIC: {inst_psv})  │  "
                    f"Performance: [bold magenta]{perf}%[/bold magenta]  │  "
                    f"Backlog: [bold red]{backlog:,} remaining[/bold red]"
                )
            except Exception:
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Rapid USB barcode scanner handler with automatic deduplication."""
        from core.services import stock_monitoring_service
        from core.models import StockDispatchScan, StockDeliveryItem, StockReturnScan
        inp_id = event.input.id
        raw_val = event.value.strip()

        if not raw_val:
            return

        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.notify(f"⚠️ Invalid plate QR/barcode format: '{raw_val}'", severity="warning")
            event.input.value = ""
            event.input.focus()
            return

        target_area_id = None
        target_badge_id = None
        db_check = None

        if inp_id == "input-dispatch-single":
            target_area_id = "#text-dispatch-bulk"
            target_badge_id = "#lbl-dispatch-staged"
            db_check = ("dispatch", StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix, registration_number=plate).exists())
        elif inp_id == "input-deliv-single":
            target_area_id = "#text-deliv-bulk"
            target_badge_id = "#lbl-deliv-staged"
            db_check = ("delivery", StockDeliveryItem.objects.filter(registration_number=plate).exists())
        elif inp_id == "input-transfer-single":
            target_area_id = "#text-transfer-bulk"
            target_badge_id = "#lbl-transfer-staged"
        elif inp_id == "input-return-single":
            target_area_id = "#text-return-bulk"
            target_badge_id = "#lbl-return-staged"
            db_check = ("return", StockReturnScan.objects.filter(work_date_suffix=self.target_date_suffix, registration_number=plate).exists())
        elif inp_id == "input-stocktake-single":
            target_area_id = "#text-stocktake-bulk"
            target_badge_id = "#lbl-stocktake-staged"

        if not target_area_id:
            return

        text_area = self.query_one(target_area_id, TextArea)
        curr_text = text_area.text
        existing_plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(curr_text)

        # Check 1: In-staging duplicate prevention
        if plate in existing_plates:
            self.notify(f"⚠️ Duplicate ignored: Plate {plate} is ALREADY in staging batch!", severity="warning")
            event.input.value = ""
            event.input.focus()
            return

        # Check 2: Database existing check advisory
        if db_check:
            kind, exists = db_check
            if exists:
                if kind == "dispatch":
                    self.notify(f"⚠️ Advisory: Plate {plate} was already dispatched today!", severity="warning")
                elif kind == "delivery":
                    self.notify(f"⚠️ Advisory: Plate {plate} already exists in warehouse stock!", severity="warning")
                elif kind == "return":
                    self.notify(f"⚠️ Advisory: Plate {plate} was already returned today!", severity="warning")

        # Append plate cleanly
        text_area.text = f"{curr_text}\n{plate}".strip()
        event.input.value = ""
        event.input.focus()

        staged_count = len(existing_plates) + 1
        if target_badge_id:
            try:
                self.query_one(target_badge_id, Static).update(f"[bold green]Staged: {staged_count} plates[/bold green]")
            except Exception:
                pass
        self.notify(f"✓ Scanned {plate} (Total Staged: {staged_count})", severity="information")

    def _handle_save_dispatch(self) -> None:
        from core.services import stock_monitoring_service
        bulk_text = self.query_one("#text-dispatch-bulk", TextArea).text
        single_text = self.query_one("#input-dispatch-single", Input).value
        combined = f"{bulk_text}\n{single_text}".strip()
        cat_select = self.query_one("#sel-dispatch-category", Select)
        category = str(cat_select.value or "PSV")

        if not combined:
            self.notify("Please scan or paste plate numbers first.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_dispatch_scans(
                plates=combined,
                plate_category=category,
                target_date_suffix=self.target_date_suffix,
            )
            if not res.get("success"):
                err = res.get("error", "Failed to dispatch plates.")
                self.notify(f"⛔ {err}", severity="error")
                return

            new_cnt = res.get("newly_dispatched", 0)
            dup_cnt = res.get("duplicate_scans_skipped", 0)
            already_cnt = res.get("already_dispatched", 0)
            rejected = res.get("rejected_not_on_stock", [])
            synced = res.get("synced_from_itms", [])

            msg = f"✓ Dispatched {new_cnt} {category} plates."
            if synced:
                msg += f" ({len(synced)} synced live from ITMS)"
            if dup_cnt > 0 or already_cnt > 0:
                msg += f" ({dup_cnt} duplicate scans, {already_cnt} already dispatched skipped)"
            self.notify(msg, severity="information")

            if rejected:
                self.notify(
                    f"⛔ BLOCKED: {len(rejected)} plate(s) not on ITMS stock ({', '.join(rejected)})",
                    severity="error",
                )
                self.query_one("#text-dispatch-bulk", TextArea).text = "\n".join(rejected)
            else:
                self.query_one("#text-dispatch-bulk", TextArea).text = ""

            self.query_one("#input-dispatch-single", Input).value = ""
            try:
                self.query_one("#lbl-dispatch-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving dispatch scans: {exc}", severity="error")

    def _handle_save_delivery(self) -> None:
        from core.services import stock_monitoring_service
        deliv_no = self.query_one("#input-deliv-number", Input).value.strip()
        paper_ref = ""
        try:
            paper_ref = self.query_one("#input-deliv-paper-ref", Input).value.strip()
        except Exception:
            pass
        supplier = self.query_one("#input-deliv-supplier", Input).value.strip()
        cat_select = self.query_one("#sel-deliv-category", Select)
        category = str(cat_select.value or "PSV")
        bulk_text = self.query_one("#text-deliv-bulk", TextArea).text.strip()
        single_text = ""
        try:
            single_text = self.query_one("#input-deliv-single", Input).value.strip()
        except Exception:
            pass
        combined = f"{bulk_text}\n{single_text}".strip()
        auto_kits = self.query_one("#chk-deliv-kits", Checkbox).value

        if not combined:
            self.notify("Please scan or paste incoming plates for this delivery.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_delivery(
                delivery_number=deliv_no,
                paper_note_reference=paper_ref,
                supplier=supplier,
                plates=combined,
                plate_category=category,
                target_date_suffix=self.target_date_suffix,
                auto_create_kits=auto_kits,
            )
            p_cnt = res.get("plates_count", 0)
            k_cnt = res.get("created_kits_count", 0)
            dup_cnt = res.get("duplicate_scans_skipped", 0)
            msg = f"✓ Ingested delivery {res.get('delivery_number')} with {p_cnt} unique {category} plates ({k_cnt} new kits created)!"
            if dup_cnt > 0:
                msg += f" ({dup_cnt} duplicate scans skipped)"
            self.notify(msg, severity="information")

            self.query_one("#text-deliv-bulk", TextArea).text = ""
            try:
                self.query_one("#input-deliv-single", Input).value = ""
                self.query_one("#input-deliv-number", Input).value = ""
                self.query_one("#input-deliv-paper-ref", Input).value = ""
                self.query_one("#lbl-deliv-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving delivery: {exc}", severity="error")

    def _handle_save_transfer(self) -> None:
        from core.services import stock_monitoring_service
        t_type = str(self.query_one("#sel-transfer-type", Select).value or "TRANSFER_IN")
        cat = str(self.query_one("#sel-transfer-category", Select).value or "PSV")
        bond_name = self.query_one("#input-transfer-bond", Input).value.strip() or "Other Bond"
        cnt_val = self.query_one("#input-transfer-count", Input).value.strip()
        bulk_text = self.query_one("#text-transfer-bulk", TextArea).text.strip()
        single_text = ""
        try:
            single_text = self.query_one("#input-transfer-single", Input).value.strip()
        except Exception:
            pass
        combined = f"{bulk_text}\n{single_text}".strip()

        count = int(cnt_val) if cnt_val.isdigit() else 0
        if not count and not combined:
            self.notify("Please enter plates count or scan transfer plates.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_bond_transfer(
                transfer_type=t_type,
                plate_category=cat,
                plates_count=count,
                other_bond_name=bond_name,
                plates=combined if combined else None,
                target_date_suffix=self.target_date_suffix,
            )
            lbl = "Transfer In" if t_type == "TRANSFER_IN" else "Transfer Out"
            self.notify(f"Recorded {lbl} of {res.get('plates_count')} {cat} plates ({bond_name})!")
            self.query_one("#text-transfer-bulk", TextArea).text = ""
            try:
                self.query_one("#input-transfer-single", Input).value = ""
                self.query_one("#lbl-transfer-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
            self.query_one("#input-transfer-count", Input).value = ""
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving bond transfer: {exc}", severity="error")

    def _handle_save_return(self) -> None:
        from core.services import stock_monitoring_service
        cat_select = self.query_one("#sel-return-category", Select)
        category = str(cat_select.value or "PSV")
        reason_select = self.query_one("#sel-return-reason", Select)
        reason = str(reason_select.value or "BIKE_NO_SHOW")
        bulk_text = self.query_one("#text-return-bulk", TextArea).text.strip()
        single_text = ""
        try:
            single_text = self.query_one("#input-return-single", Input).value.strip()
        except Exception:
            pass
        combined = f"{bulk_text}\n{single_text}".strip()

        if not combined:
            self.notify("Please scan or paste returned plates first.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_return_scans(
                plates=combined,
                plate_category=category,
                reason=reason,
                target_date_suffix=self.target_date_suffix,
            )
            new_cnt = res.get("newly_returned", 0)
            dup_cnt = res.get("duplicate_scans_skipped", 0)
            already_cnt = res.get("already_returned", 0)
            msg = f"✓ Recorded {new_cnt} returned {category} plates."
            if dup_cnt > 0 or already_cnt > 0:
                msg += f" ({dup_cnt} duplicates, {already_cnt} already returned skipped)"
            self.notify(msg, severity="information")

            self.query_one("#text-return-bulk", TextArea).text = ""
            try:
                self.query_one("#input-return-single", Input).value = ""
                self.query_one("#lbl-return-staged", Static).update("[dim]Staged: 0 plates[/dim]")
            except Exception:
                pass
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving return scans: {exc}", severity="error")

    def _handle_save_scheduled(self) -> None:
        from core.services import stock_monitoring_service
        try:
            target_raw = self.query_one("#input-sched-target", Input).value.strip()
            s_target = int(target_raw) if target_raw.isdigit() else 0
            o_pmo = int(self.query_one("#input-open-pmo", Input).value.strip() or 0)
            o_psv = int(self.query_one("#input-open-psv", Input).value.strip() or 0)
            remarks_text = self.query_one("#text-stock-remarks", TextArea).text.strip()

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
            self.notify(f"✓ Saved scheduled target ({s_target:,}), opening balances (Total: {o_pmo + o_psv:,}) & remarks!", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving scheduled values: {exc}", severity="error")

    def _handle_autofill_opening(self) -> None:
        from core.services import stock_monitoring_service
        try:
            carried = stock_monitoring_service.get_previous_shift_closing_balances(self.target_date_suffix)
            pmo = carried.get("pmo", 0)
            psv = carried.get("psv", 0)
            tot = carried.get("total", 0)
            prev_suf = carried.get("previous_date_suffix") or "previous shift"

            self.query_one("#input-open-pmo", Input).value = str(pmo)
            self.query_one("#input-open-psv", Input).value = str(psv)
            self.query_one("#lbl-open-total", Static).update(f"[bold cyan]Total Opening: {tot:,}[/bold cyan]")

            if carried.get("source_ledger_exists"):
                self.notify(f"✓ Auto-carried previous shift ({prev_suf}) closing stock: {tot:,} plates (PMO: {pmo:,}, PSV: {psv:,})", severity="information")
            else:
                self.notify(f"⚠️ No previous shift closing stock found before {self.target_date_suffix}. Opening set to 0.", severity="warning")
        except Exception as exc:
            self.notify(f"Error auto-carrying previous closing stock: {exc}", severity="error")

    def _handle_save_stocktake(self) -> None:
        from core.services import stock_monitoring_service
        bulk_text = self.query_one("#text-stocktake-bulk", TextArea).text.strip()
        single_text = ""
        try:
            single_text = self.query_one("#input-stocktake-single", Input).value.strip()
        except Exception:
            pass
        combined = f"{bulk_text}\n{single_text}".strip()

        if not combined:
            self.notify("Please scan or paste safe room physical plates first.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_stock_taking_audit(
                scanned_plates=combined,
                target_date_suffix=self.target_date_suffix,
            )
            scanned = res.get("total_scanned", 0)
            book = res.get("book_closing_total", 0)
            variance = res.get("variance", 0)
            var_color = "green" if variance == 0 else ("yellow" if variance > 0 else "red")
            summary_text = (
                f"[bold cyan]Safe Audit Summary:[/bold cyan]  Physical Scanned: [bold white]{scanned:,}[/bold white]  │  "
                f"Book Closing: [bold white]{book:,}[/bold white]  │  "
                f"Variance: [bold {var_color}]{variance:+d}[/bold {var_color}]"
            )
            try:
                self.query_one("#lbl-stocktake-summary", Static).update(summary_text)
            except Exception:
                pass

            msg = f"✓ Safe stock taking complete: Scanned {scanned} plates. Variance: {variance:+d}."
            self.notify(msg, severity="information" if variance >= 0 else "warning")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error performing stock taking audit: {exc}", severity="error")

    def _handle_set_manual_physical_count(self) -> None:
        from core.services import stock_monitoring_service
        cnt_val = self.query_one("#input-stocktake-manual-count", Input).value.strip()
        if not cnt_val.isdigit():
            self.notify("Please enter a valid numeric physical count.", severity="warning")
            return

        try:
            res = stock_monitoring_service.set_physical_count(
                physical_count=int(cnt_val),
                target_date_suffix=self.target_date_suffix,
            )
            cnt = res.get("physical_count", 0)
            variance = res.get("variance", 0)
            self.notify(f"✓ Physical count set to {cnt}. Audit variance: {variance:+d}.", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error setting physical count: {exc}", severity="error")

    def action_export_csv(self) -> None:
        from core.services import stock_monitoring_service
        try:
            content = stock_monitoring_service.export_stock_reconciliation_csv(self.target_date_suffix)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"itms_bond_stock_{self.target_date_suffix}_{date_str}.csv"
            out_dir = os.path.join(settings.BASE_DIR, "exports")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, filename)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.notify(f"Exported stock report to exports/{filename}!", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting CSV: {exc}", severity="error")

    def action_dismiss_modal(self) -> None:
        self.dismiss(self._cached_recon)

    def action_show_phone_scanner(self) -> None:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        url = f"http://{local_ip}:8000/mobile/"
        self.notify(f"📱 Phone Scanner URL: {url}\nSelect Mode 3 (WAREHOUSE & BOND STOCK SCANNER) for live camera QR scanning.", severity="information", timeout=8)
        if hasattr(self.app, "log_message"):
            self.app.log_message(
                f"[bold cyan]📱 Mobile Phone Stock Scanner:[/bold cyan] Open [bold yellow]{url}[/bold yellow] on your smartphone camera (Select Mode 3: WAREHOUSE & BOND STOCK SCANNER for auto-scan intake).",
                level="INFO",
            )



