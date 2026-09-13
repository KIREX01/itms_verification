"""
Dedicated Dashboard Tab (Tab 1) for the ITMS Closing System.

Provides an executive command center:
1. System Health & Environment Summary (Database, ITMS connection, Operator, Dry-Run status).
2. End-to-End Pipeline Flow Visualizer with real-time stage metrics.
3. Visual Status Distribution Chart (Unicode horizontal bar chart).
4. Storage & Vault Telemetry (Photo count, disk usage, retention status).
5. Offline Outbox Status Card & Auto-Sync Monitor.
6. Quick-Action Workflow Launchpad with keyboard triggers.
"""
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from django.conf import settings
from django.db.models import Q
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static, ProgressBar

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    VehicleInstallationPair,
)
from core.services import config_service
from core.services.itms_web_client import get_current_itms_account, get_web_client


class PipelineStageCard(Static):
    """Interactive visual card representing an end-to-end pipeline stage."""

    can_focus = True

    TOOLTIPS = {
        "ingest": "📥 Photo Ingestion: Click or press [I] to import evidence photos",
        "association": "🔗 Physical Pairing: Click or press [M] to pair via U-Turn walk & filename sequence",
        "vision": "🧠 Joint Vision Engine: Click or press [P] to run dual-stream YOLOv8 plate recognition on pairs",
        "review": "📋 Operator Review: Click or press [4] to open verification queue",
        "finalized": "🚀 ITMS Finalized: Click or press [B] to submit verified batch to ITMS",
    }

    def __init__(self, stage_id: str, **kwargs):
        super().__init__("[dim]Loading stage...[/dim]", **kwargs)
        self.stage_id = stage_id
        if stage_id in self.TOOLTIPS:
            self.tooltip = self.TOOLTIPS[stage_id]

    def _activate_stage(self) -> None:
        try:
            app = self.app
        except Exception:
            app = getattr(self, "_app", None)
        if not app:
            return

        if self.stage_id == "ingest":
            app.action_native_ingest()
        elif self.stage_id == "association":
            app.action_match_pairs()
        elif self.stage_id == "vision":
            app.action_process_vision()
        elif self.stage_id == "review":
            app.action_tab_queue()
        elif self.stage_id == "finalized":
            app.action_batch_submit()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._activate_stage()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self._activate_stage()


class DashboardPane(VerticalScroll):
    """Full-featured Executive Dashboard Pane (Tab 1)."""

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard-container"):
            yield Static(id="dashboard-header-banner")

            # Row 1: Pipeline Flow Visualizer with Modern Interactive Cards
            with Vertical(classes="dashboard-card", id="card-pipeline-flow"):
                yield Static("[bold cyan]🚀 End-to-End Ingestion & Verification Pipeline[/bold cyan] [dim](Interactive Cards - Click any stage or press hotkey)[/dim]", classes="card-title")
                with Horizontal(id="pipeline-cards-container"):
                    yield PipelineStageCard("ingest", id="pipe-card-1", classes="pipeline-stage-card card-ingest")
                    yield Static(" ➜ ", classes="pipeline-connector")
                    yield PipelineStageCard("association", id="pipe-card-2", classes="pipeline-stage-card card-association")
                    yield Static(" ➜ ", classes="pipeline-connector")
                    yield PipelineStageCard("vision", id="pipe-card-3", classes="pipeline-stage-card card-vision")
                    yield Static(" ➜ ", classes="pipeline-connector")
                    yield PipelineStageCard("review", id="pipe-card-4", classes="pipeline-stage-card card-review")
                    yield Static(" ➜ ", classes="pipeline-connector")
                    yield PipelineStageCard("finalized", id="pipe-card-5", classes="pipeline-stage-card card-finalized")

            # Row 2: Verification Status Distribution Chart
            with Vertical(classes="dashboard-card", id="card-status-distribution"):
                yield Static("[bold yellow]📊 Verification Queue Status Distribution[/bold yellow]", classes="card-title")
                yield Static(id="dashboard-distribution-chart")

            # Row 3: Telemetry & System Cards (Horizontal Split)
            with Horizontal(id="dashboard-telemetry-row"):
                # Left: Storage & Vault Telemetry
                with Vertical(classes="dashboard-card half-card", id="card-vault-telemetry"):
                    yield Static("[bold green]📦 Master Vault & Storage Telemetry[/bold green]", classes="card-title")
                    yield Static(id="dashboard-vault-stats")

                # Right: Offline Outbox & Network Health
                with Vertical(classes="dashboard-card half-card", id="card-outbox-status"):
                    yield Static("[bold magenta]⚡ Offline Outbox & Auto-Sync Monitor[/bold magenta]", classes="card-title")
                    yield Static(id="dashboard-outbox-stats")

            # Row 4: Quick Action Workflow Launchpad
            with Vertical(classes="dashboard-card", id="card-quick-actions"):
                yield Static("[bold white]⚡ Quick Action Workflow Launchpad[/bold white] [dim](Click or press keyboard hotkey)[/dim]", classes="card-title")
                with Horizontal(classes="action-button-row"):
                    yield Button("📥 Add Photos [I]", variant="primary", id="btn-dash-ingest")
                    yield Button("🔗 Match Pairs [M]", variant="default", id="btn-dash-match")
                    yield Button("🧠 Run Vision [P]", variant="default", id="btn-dash-vision")
                    yield Button("📋 Review Queue [4]", variant="warning", id="btn-dash-queue")
                with Horizontal(classes="action-button-row"):
                    yield Button("🚀 Batch Submit [B]", variant="success", id="btn-dash-batch")
                    yield Button("⚡ Drain Outbox [O]", variant="default", id="btn-dash-outbox")
                    yield Button("🌐 Sync Orders [Y]", variant="default", id="btn-dash-sync")
                    yield Button("⚙️ Settings [6]", variant="default", id="btn-dash-settings")

    def on_mount(self) -> None:
        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        """Computes and renders real-time dashboard telemetry."""
        active_acc = get_current_itms_account()

        orders_qs = InstallationOrder.objects.all()
        pairs_qs = VehicleInstallationPair.objects.all()
        if active_acc:
            orders_qs = orders_qs.filter(
                Q(account_email__iexact=active_acc) | Q(account_email="") | Q(account_email__isnull=True)
            )
            pairs_qs = pairs_qs.filter(
                Q(order__account_email__iexact=active_acc) |
                Q(account_email__iexact=active_acc) |
                (
                    (Q(order__isnull=True) | Q(order__account_email="") | Q(order__account_email__isnull=True)) &
                    (Q(account_email="") | Q(account_email__isnull=True))
                )
            )

        total_orders = orders_qs.count()
        active_orders = orders_qs.filter(is_active_on_itms=True, is_archived=False).count()
        completed_orders = orders_qs.filter(
            Q(is_archived=True) |
            Q(status=InstallationOrder.Status.INSTALLED) |
            Q(order_status__iexact="installed")
        ).count()

        total_images = EvidenceImage.objects.count()
        unprocessed_images = EvidenceImage.objects.filter(status=EvidenceImage.Status.NEW).count()
        processed_images = EvidenceImage.objects.exclude(status=EvidenceImage.Status.NEW).count()

        total_pairs = pairs_qs.count()
        pending_review = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW).count()
        approved = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED).count()
        submitted = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).count()
        outbox_count = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX).count()
        conflicts = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT).count()
        failed = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.FAILED).count()
        incomplete = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE).count()
        issues_total = conflicts + failed + incomplete

        db_info = config_service.get_active_database_info()
        itms_status = get_web_client().get_status()
        is_itms_online = itms_status.get("authenticated", False)
        itms_user = itms_status.get("user_email") or "Offline"
        itms_badge = f"[bold green]● Online ({itms_user.split('@')[0]})[/bold green]" if is_itms_online else "[dim]○ Disconnected[/dim]"

        dry_run_active = config_service.get_setting("submission.dry_run_mode", True)
        safety_badge = (
            "[bold yellow]● DRY-RUN SIMULATION[/bold yellow]"
            if dry_run_active
            else "[bold red]● LIVE MUTATING MODE[/bold red]"
        )

        compression_active = config_service.get_setting("compression.enabled", True)
        comp_dim = config_service.get_setting("compression.max_dimension", 1920)
        comp_q = config_service.get_setting("compression.jpeg_quality", 88)
        comp_badge = f"[bold green]ON[/bold green] ({comp_dim}px Q{comp_q})" if compression_active else "[dim]OFF[/dim]"

        try:
            op_user = getattr(self.app, "current_user", None)
            op_name = (op_user.get_full_name() or op_user.username) if op_user else "System Operator"
        except Exception:
            op_name = "System Operator"

        header_text = (
            f"[bold cyan]ITMS CLOSING SYSTEM EXECUTIVE DASHBOARD[/bold cyan]  │  "
            f"[b]Operator:[/b] [bold white]{op_name}[/bold white]  │  "
            f"[b]Database:[/b] {db_info['badge']}  │  "
            f"[b]ITMS Link:[/b] {itms_badge}  │  "
            f"[b]Safety:[/b] {safety_badge}  │  "
            f"[b]Compression:[/b] {comp_badge}"
        )
        try:
            self.query_one("#dashboard-header-banner", Static).update(header_text)
        except Exception:
            pass

        # Stage 1: Ingestion
        c1_status = "[bold yellow]● Ingesting...[/bold yellow]" if unprocessed_images > 0 else "[bold green]● All Ingested[/bold green]"
        c1_text = (
            f"[bold cyan]1. Photo Ingestion[/bold cyan]\n"
            f"Total:   [b]{total_images:4d}[/b] photos\n"
            f"Pending: [dim]{unprocessed_images:4d}[/dim]\n"
            f"{c1_status}\n"
            f"[dim]Click or [I][/dim]"
        )

        # Stage 2: Physical Pairing (U-Turn Walk & Filenames)
        c2_status = f"[bold yellow]● {incomplete} Missing Partner[/bold yellow]" if incomplete > 0 else "[bold green]● Pairs Formed[/bold green]"
        c2_text = (
            f"[bold yellow]2. Physical Pairing[/bold yellow]\n"
            f"Pairs:      [b]{total_pairs:4d}[/b] grouped\n"
            f"Incomplete: [dim]{incomplete:4d}[/dim]\n"
            f"{c2_status}\n"
            f"[dim]Click or [M][/dim]"
        )

        # Stage 3: Joint Vision Engine (Dual-Stream YOLOv8 + Consensus)
        c3_status = f"[bold yellow]● {unprocessed_images} Queued[/bold yellow]" if unprocessed_images > 0 else "[bold green]● 100% Verified[/bold green]"
        c3_text = (
            f"[bold magenta]3. Joint Vision Engine[/bold magenta]\n"
            f"Processed:   [b]{processed_images:4d}[/b]\n"
            f"Queued:      [dim]{unprocessed_images:4d}[/dim]\n"
            f"{c3_status}\n"
            f"[dim]Click or [P][/dim]"
        )

        # Stage 4: Operator Review
        c4_status = f"[bold yellow]● {pending_review} Need Review[/bold yellow]" if pending_review > 0 else "[bold green]● Queue Clear[/bold green]"
        c4_text = (
            f"[bold dodgerblue]4. Operator Review[/bold dodgerblue]\n"
            f"Approved:   [b]{approved:4d}[/b]\n"
            f"Pending:    [{'bold yellow' if pending_review else 'dim'}]{pending_review:4d}[/]\n"
            f"{c4_status}\n"
            f"[dim]Click or [4][/dim]"
        )

        # Stage 5: ITMS Finalized
        if outbox_count > 0:
            c5_status = f"[bold magenta]● {outbox_count} in Outbox[/bold magenta]"
        else:
            c5_status = "[bold green]● Synced to ITMS[/bold green]"
        c5_text = (
            f"[bold green]5. ITMS Finalized[/bold green]\n"
            f"Submitted:  [b]{submitted:4d}[/b]\n"
            f"Completed:  [b]{completed_orders:4d}[/b]\n"
            f"{c5_status}\n"
            f"[dim]Click or [B][/dim]"
        )

        try:
            self.query_one("#pipe-card-1", PipelineStageCard).update(c1_text)
            self.query_one("#pipe-card-2", PipelineStageCard).update(c2_text)
            self.query_one("#pipe-card-3", PipelineStageCard).update(c3_text)
            self.query_one("#pipe-card-4", PipelineStageCard).update(c4_text)
            self.query_one("#pipe-card-5", PipelineStageCard).update(c5_text)
        except Exception:
            pass

        def build_ascii_bar(val: int, tot: int, bar_len: int = 35) -> str:
            if tot <= 0 or val <= 0:
                return "░" * bar_len
            filled = int(round((val / tot) * bar_len))
            filled = max(1, min(bar_len, filled))
            return ("█" * filled) + ("░" * (bar_len - filled))

        pct_sub = round((submitted / total_pairs * 100), 1) if total_pairs else 0.0
        pct_app = round((approved / total_pairs * 100), 1) if total_pairs else 0.0
        pct_pen = round((pending_review / total_pairs * 100), 1) if total_pairs else 0.0
        pct_out = round((outbox_count / total_pairs * 100), 1) if total_pairs else 0.0
        pct_iss = round((issues_total / total_pairs * 100), 1) if total_pairs else 0.0

        bar_sub = build_ascii_bar(submitted, total_pairs, 35)
        bar_app = build_ascii_bar(approved, total_pairs, 35)
        bar_pen = build_ascii_bar(pending_review, total_pairs, 35)
        bar_out = build_ascii_bar(outbox_count, total_pairs, 35)
        bar_iss = build_ascii_bar(issues_total, total_pairs, 35)

        dist_text = (
            f"  [cyan]● Submitted to ITMS[/cyan]:   [bold cyan]{bar_sub}[/bold cyan]  {submitted:4d} pairs  ({pct_sub:5.1f}%)\n"
            f"  [green]● Approved (Ready)[/green]:     [bold green]{bar_app}[/bold green]  {approved:4d} pairs  ({pct_app:5.1f}%)\n"
            f"  [yellow]● Pending Review[/yellow]:       [bold yellow]{bar_pen}[/bold yellow]  {pending_review:4d} pairs  ({pct_pen:5.1f}%)\n"
            f"  [magenta]● Offline Outbox[/magenta]:       [bold magenta]{bar_out}[/bold magenta]  {outbox_count:4d} pairs  ({pct_out:5.1f}%)\n"
            f"  [red]● Issues / Conflicts[/red]:   [bold red]{bar_iss}[/bold red]  {issues_total:4d} pairs  ({pct_iss:5.1f}%)"
        )
        try:
            self.query_one("#dashboard-distribution-chart", Static).update(dist_text)
        except Exception:
            pass

        vault_root = Path(getattr(settings, "VAULT_ROOT", "media/vault"))
        vault_bytes = 0
        vault_file_count = 0
        if vault_root.is_dir():
            for root, _, files in os.walk(vault_root):
                for f in files:
                    try:
                        vault_bytes += os.path.getsize(os.path.join(root, f))
                        vault_file_count += 1
                    except OSError:
                        pass
        vault_mb = vault_bytes / (1024 * 1024)
        vault_gb = vault_bytes / (1024 * 1024 * 1024)
        size_str = f"{vault_gb:.2f} GB" if vault_gb >= 1.0 else f"{vault_mb:.1f} MB"

        batches_count = IngestionBatch.objects.count()
        pruned_count = EvidenceImage.objects.filter(is_file_pruned=True).count()
        retention_days = config_service.get_setting("storage.vault_retention_days", 7)

        vault_text = (
            f"  • [b]Vault Master Storage:[/b] [bold green]{size_str}[/bold green] ({vault_file_count} files on disk)\n"
            f"  • [b]Database Evidence Rows:[/b] {total_images} photos registered\n"
            f"  • [b]Ingestion Batches:[/b]     {batches_count} total sessions\n"
            f"  • [b]Retention Policy:[/b]       {retention_days}-day rolling vault retention\n"
            f"  • [b]Pruned Photos:[/b]          {pruned_count} pruned files ([dim]audit intact[/dim])"
        )
        try:
            self.query_one("#dashboard-vault-stats", Static).update(vault_text)
        except Exception:
            pass

        outbox_enabled = config_service.get_setting("outbox.enabled", True)
        sync_interval = config_service.get_setting("outbox.auto_sync_interval_seconds", 15)
        outbox_status_str = "[bold green]Active (Auto-Syncing)[/bold green]" if outbox_enabled else "[dim]Disabled[/dim]"

        outbox_text = (
            f"  • [b]Queued in Outbox:[/b]     [bold {'magenta' if outbox_count else 'green'}]{outbox_count} orders pending auto-sync[/bold {'magenta' if outbox_count else 'green'}]\n"
            f"  • [b]Daemon Heartbeat:[/b]     {outbox_status_str} (Pings every {sync_interval}s)\n"
            f"  • [b]Target Server:[/b]        [cyan]{itms_status.get('base_url', 'https://stock.itms.ug')}[/cyan]\n"
            f"  • [b]Connection Latency:[/b]   {itms_status.get('latency_ms', 0)} ms\n"
            f"  • [b]Session Expiration:[/b]   {itms_status.get('days_left', 30)} days remaining"
        )
        try:
            self.query_one("#dashboard-outbox-stats", Static).update(outbox_text)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-dash-ingest":
            self.app.action_native_ingest()
        elif btn_id == "btn-dash-vision":
            self.app.action_process_vision()
        elif btn_id == "btn-dash-match":
            self.app.action_match_pairs()
        elif btn_id == "btn-dash-queue":
            self.app.action_tab_queue()
        elif btn_id == "btn-dash-batch":
            self.app.action_batch_submit()
        elif btn_id == "btn-dash-outbox":
            self.app.action_drain_outbox()
        elif btn_id == "btn-dash-sync":
            self.app.action_sync_itms_orders()
        elif btn_id == "btn-dash-settings":
            self.app.action_tab_settings()
