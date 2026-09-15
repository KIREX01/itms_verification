"""
ITMS Command Palette & Command Providers.

Provides an organized, categorized command palette accessible via Ctrl+P (^P)
with instant fuzzy search, keyboard navigation, clear closing mechanism,
and categorized execution across the entire ITMS verification workflow.
"""
from typing import List, Tuple, Callable
from textual.command import CommandPalette, DiscoveryHit, Hit, Hits, Provider, CommandInput, CommandList, SearchIcon
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, LoadingIndicator
from textual.binding import Binding
from textual.app import ComposeResult
from textual import events, on


class ITMSCommandPalette(CommandPalette):
    """
    Enhanced Command Palette with clear close button, escape priority,
    and background-click dismissal.
    """
    BINDINGS = [
        Binding("escape", "escape", "Close Palette", priority=True),
        Binding("ctrl+p", "escape", "Close Palette", priority=True, show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="--container"):
            with Horizontal(id="--input"):
                yield SearchIcon()
                yield CommandInput(placeholder=self._placeholder, select_on_focus=False)
                yield Button("✕ Close [Esc]", variant="error", id="btn-close-palette")
            with Vertical(id="--results"):
                yield CommandList()
                yield LoadingIndicator()

    def action_escape(self) -> None:
        """Close and dismiss the command palette cleanly."""
        self._action_escape()

    def action_close(self) -> None:
        """Alternative close action alias."""
        self._action_escape()

    @on(Button.Pressed, "#btn-close-palette")
    def on_close_palette_button(self, event: Button.Pressed) -> None:
        event.stop()
        self._action_escape()

    def on_click(self, event: events.Click) -> None:
        if event.widget == self:
            event.stop()
            self._action_escape()


class ITMSCommandProvider(Provider):
    """Provides organized ITMS verification commands for the Textual Command Palette (Ctrl+P / ^P)."""

    def _get_commands(self) -> List[Tuple[str, Callable, str]]:
        app = self.app
        cmds = [
            # 1. Pipeline & Vision
            ("Pipeline: Run Vision Pipeline (Detect & OCR) [P]", app.action_process_vision, "Run YOLO plate detector and PaddleOCR across vault"),
            ("Pipeline: Match Pairs & Spatial Association [M]", app.action_match_pairs, "Pair front and rear motorcycle evidence photos"),
            
            # 2. Operator Review & Pairing
            ("Review: Quick Type Plate / Match [T]", app.action_quick_type_plate, "Fast-path plate typing and order linking dialog"),
            ("Review: Approve Selected Pair [A]", app.action_approve, "Mark active pair as approved for submission"),
            ("Review: Swap Front/Rear Photos [S]", app.action_swap, "Invert front and rear image assignments on active pair"),
            ("Review: Link / Pick Photo [L]", app.action_link_pair, "Manually pick and associate photos for selected pair"),
            ("Review: View Side-by-Side Comparison [V] (Dev Mode)", app.action_view_evidence, "Launch high-res image viewer with plate crops & bounding boxes (Requires Developer Mode)"),
            
            # 3. ITMS Submission & Sync
            ("ITMS: Submit Single Pair [U]", app.action_submit_pair, "Drive active pair through ITMS installation wizard"),
            ("ITMS: Batch Submit All Approved [B]", app.action_batch_submit, "Submit all approved pairs in unattended queue"),
            ("ITMS: Retry All Failed Orders [^R]", app.action_retry_failed, "Reset all failed orders to APPROVED and open batch submission modal"),
            ("ITMS: Sync Active Orders [Y]", app.action_sync_itms_orders, "Synchronize active installation orders from stock.itms.ug"),
            
            # 4. Ingestion & Upload
            ("Upload: Native Add Photos Dialog [I]", app.action_native_ingest, "Open OS directory picker for photo ingestion"),
            ("Upload: Open Web Upload UI [W]", app.action_open_upload_ui, "Launch browser-based photo uploader"),
            
            # 5. Tabs & Navigation
            ("Navigate: Review Queue (Tab 1)", app.action_tab_queue, "Switch to Review Queue tab"),
            ("Navigate: History & Audit (Tab 2)", app.action_tab_history, "Switch to History & Audit tab"),
            ("Navigate: Ingestion Batches (Tab 3)", app.action_tab_batches, "Switch to Ingestion Batches tab"),
            ("Navigate: ITMS WebApp Connection (Tab 4)", app.action_tab_itms, "Switch to ITMS WebApp live connection tab"),
            ("Navigate: System Settings & Config (Tab 5)", app.action_tab_settings, "Open configuration pane for safety, OCR, and storage settings"),
            
            # 6. Filters & Storage
            ("Filter: Cycle History Status Filter [F]", app.action_cycle_filter, "Toggle between All, Submitted, Failed, and Audit Logs"),
            ("Storage: Clean Storage & Optimize DB [C]", app.action_clean_storage, "Prune cropped scratch files, truncate SQLite WAL, and optimize storage"),
            ("Export: Shift Verification Report [E]", app.action_export_shift_report, "Export timestamped CSV shift handover & audit compliance report"),
            
            # 7. System & Session
            ("System: Refresh Data [R]", app.action_refresh, "Reload tables, metrics, and inspection panes"),
            ("System: Sign Out Operator [X]", app.action_logout, "Sign out current operator and return to login portal"),
            ("System: Close Command Palette [Esc]", lambda: None, "Close the command palette and return to app"),
            ("System: Quit Copilot [Q]", app.action_quit, "Exit the verification copilot application"),
        ]

        # 8. Developer & Testing Tools (ONLY shown when developer_mode is enabled)
        from core.services.config_service import is_developer_mode
        if is_developer_mode():
            cmds.extend([
                ("Developer: Seed Synthetic Orders [DEV]", app.action_dev_seed_orders, "Seed 25 test installation orders for pipeline testing"),
                ("Developer: Benchmark Pipeline [DEV]", app.action_dev_benchmark, "Benchmark YOLO detector and OCR engines across vault"),
                ("Developer: Toggle ITMS Mock/Live [DEV]", app.action_dev_toggle_backend, "Switch between mock simulation and live ITMS submissions"),
            ])

        return cmds

    async def discover(self) -> Hits:
        for name, callback, help_text in self._get_commands():
            yield DiscoveryHit(
                name,
                callback,
                help=help_text,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, callback, help_text in self._get_commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    callback,
                    help=help_text,
                )
