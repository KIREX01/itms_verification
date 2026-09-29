"""
Modal dialog screens for the ITMS Operator TUI.
"""
from datetime import date, datetime
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from django.conf import settings
from django.utils import timezone
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
                            "[bold green]📥 Record Inbound Plate Delivery Manifest[/bold green]  │  "
                            "[dim]Increases warehouse physical stock (+Received) and auto-creates kits[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="Delivery Note # (e.g. DN-20260930-01 or leave AUTO)",
                                id="input-deliv-number",
                                classes="stock-input-field",
                            )
                            yield Input(
                                placeholder="Paper Note Ref # (e.g. DN/FAC/8912)",
                                id="input-deliv-paper-ref",
                                classes="stock-input-field",
                            )
                            yield Input(
                                value="Factory / Central Depot",
                                placeholder="Supplier Name",
                                id="input-deliv-supplier",
                                classes="stock-input-field",
                            )
                            yield Select(
                                [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                value="PSV",
                                id="sel-deliv-category",
                            )
                        with Horizontal(classes="stock-row"):
                            yield Input(
                                placeholder="⚡ Rapid scan delivery plate QR [Enter to add]...",
                                id="input-deliv-single",
                                classes="stock-input-field",
                            )
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-deliv-staged", classes="stock-staged-badge")
                        yield TextArea(
                            id="text-deliv-bulk",
                            classes="stock-textarea",
                        )
                        with Horizontal(classes="stock-row"):
                            yield Checkbox(
                                "Auto-create local Installation Kits (marked 'New') in stock",
                                value=True,
                                id="chk-deliv-kits",
                            )
                            yield Button("📥 Ingest Delivery into Stock", variant="success", id="btn-save-delivery")
                            yield Button("Clear Delivery", variant="default", id="btn-clear-deliv")
                        yield Static("[bold white]Stored Inbound Delivery Notes for Shift:[/bold white]")
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

                # TAB 6: Scheduled Target & Opening Balance Feed
                with TabPane("🎯 Scheduled & Opening Balance", id="tab-scheduled-pane"):
                    with Vertical(classes="stock-tab-pane"):
                        yield Static(
                            "[bold cyan]🎯 Scheduled Installation Target & Opening Balance Entry[/bold cyan]  │  "
                            "[dim]Manually feed in the scheduled target plates to be installed under bond for the shift[/dim]"
                        )
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Scheduled Target PSV (White):[/bold white] ", classes="stock-input-field")
                            yield Input(value="0", placeholder="Target PSV count", id="input-sched-psv", classes="stock-input-field")
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Scheduled Target PMO (Yellow):[/bold white] ", classes="stock-input-field")
                            yield Input(value="0", placeholder="Target PMO count", id="input-sched-pmo", classes="stock-input-field")
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Opening Balance PSV (White):[/bold white] ", classes="stock-input-field")
                            yield Input(value="0", placeholder="Opening PSV count", id="input-open-psv", classes="stock-input-field")
                        with Horizontal(classes="stock-row"):
                            yield Static("[bold white]Opening Balance PMO (Yellow):[/bold white] ", classes="stock-input-field")
                            yield Input(value="0", placeholder="Opening PMO count", id="input-open-pmo", classes="stock-input-field")
                        yield Button("💾 Save Scheduled Targets & Opening Balances", variant="success", id="btn-save-scheduled")

            with Horizontal(id="modal-footer"):
                yield Button("📑 Export CSV [E]", variant="default", id="btn-stock-export")
                yield Button("🔄 Refresh [R]", variant="primary", id="btn-stock-refresh")
                yield Button("Close [Esc]", variant="error", id="btn-stock-close")

    def on_mount(self) -> None:
        table = self.query_one("#table-modal-stock-report", DataTable)
        table.add_columns("Description Metric", "Public White (PSV)", "Private Yellow (PMO)", "Total Combined (Bond)", "Formula / Note")
        table.cursor_type = "row"

        table_deliv = self.query_one("#table-modal-deliv-notes", DataTable)
        table_deliv.add_columns("Delivery Note #", "Paper Ref #", "Category", "Plates Count", "Supplier", "Logged At")
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
                paper_ref = n["paper_note_reference"] or "—"
                table.add_row(
                    f"[bold green]{n['delivery_number']}[/bold green]",
                    f"[bold yellow]{paper_ref}[/bold yellow]",
                    cat_badge,
                    f"[bold cyan]{n['total_plates_count']:,}[/bold cyan]",
                    n["supplier"][:25],
                    f"[dim]{n['created_at']}[/dim]",
                )
        except Exception:
            pass


    def _render_header(self, r: Dict[str, Any]) -> None:
        fmt_date = r.get("formatted_date", "")
        suf = r.get("work_date_suffix", "")
        wh = r.get("warehouse_name", "Bond Warehouse")
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
            psv = row.get("psv", 0)
            pmo = row.get("pmo", 0)
            tot = row.get("total", 0)
            note = row.get("note", "")

            # Highlight specific rows
            if "Closing Balance" in metric:
                m_str = f"[bold green]{metric}[/bold green]"
                p_str = f"[bold green]{psv:,}[/bold green]"
                y_str = f"[bold green]{pmo:,}[/bold green]"
                t_str = f"[bold white on dark_green] {tot:,} [/bold white on dark_green]"
            elif "Scheduled" in metric:
                m_str = f"[bold cyan]{metric}[/bold cyan]"
                p_str = f"[bold cyan]{psv:,}[/bold cyan]"
                y_str = f"[bold cyan]{pmo:,}[/bold cyan]"
                t_str = f"[bold cyan]{tot:,}[/bold cyan]"
            elif "Variance" in metric:
                color = "green" if tot >= 0 else "red"
                m_str = f"[{color}]{metric}[/{color}]"
                p_str = f"[{color}]{psv:+d}[/{color}]"
                y_str = f"[{color}]{pmo:+d}[/{color}]"
                t_str = f"[{color}]{tot:+d}[/{color}]"
            else:
                m_str = f"[bold white]{metric}[/bold white]"
                p_str = f"{psv:,}"
                y_str = f"{pmo:,}"
                t_str = f"[bold white]{tot:,}[/bold white]"

            table.add_row(m_str, p_str, y_str, t_str, f"[dim]{note}[/dim]")

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
                self.query_one("#input-sched-psv", Input).value = str(ledger.scheduled_psv)
                self.query_one("#input-sched-pmo", Input).value = str(ledger.scheduled_pmo)
                self.query_one("#input-open-psv", Input).value = str(ledger.opening_balance_psv)
                self.query_one("#input-open-pmo", Input).value = str(ledger.opening_balance_pmo)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-stock-close":
            self.action_dismiss_modal()
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
            new_cnt = res.get("newly_dispatched", 0)
            dup_cnt = res.get("duplicate_scans_skipped", 0)
            already_cnt = res.get("already_dispatched", 0)
            msg = f"✓ Dispatched {new_cnt} {category} plates."
            if dup_cnt > 0 or already_cnt > 0:
                msg += f" ({dup_cnt} duplicate scans, {already_cnt} already dispatched skipped)"
            self.notify(msg, severity="information")

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
            s_psv = int(self.query_one("#input-sched-psv", Input).value.strip() or 0)
            s_pmo = int(self.query_one("#input-sched-pmo", Input).value.strip() or 0)
            o_psv = int(self.query_one("#input-open-psv", Input).value.strip() or 0)
            o_pmo = int(self.query_one("#input-open-pmo", Input).value.strip() or 0)

            stock_monitoring_service.set_scheduled_target(
                scheduled_psv=s_psv,
                scheduled_pmo=s_pmo,
                target_date_suffix=self.target_date_suffix,
            )
            stock_monitoring_service.set_opening_balances(
                opening_psv=o_psv,
                opening_pmo=o_pmo,
                target_date_suffix=self.target_date_suffix,
            )
            self.notify("Saved scheduled targets and opening balances successfully!", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving scheduled values: {exc}", severity="error")

    def action_export_csv(self) -> None:
        from core.services import stock_monitoring_service
        try:
            content = stock_monitoring_service.export_stock_reconciliation_csv(self.target_date_suffix)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"itms_bond_stock_{self.target_date_suffix}_{date_str}.csv"
            out_path = os.path.join(settings.BASE_DIR, filename)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.notify(f"Exported stock report to {filename}!", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting CSV: {exc}", severity="error")

    def action_dismiss_modal(self) -> None:
        self.dismiss(self._cached_recon)



