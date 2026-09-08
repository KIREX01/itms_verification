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
from textual.widgets import Button, DataTable, Static

from core.models import EvidenceImage, VehicleInstallationPair
from core.matcher.association import get_closest_candidates
from core.services import viewer


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
