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

            # Row 4: Wireless Mobile Camera QR Section
            with Vertical(classes="dashboard-card", id="card-mobile-qr"):
                yield Static("[bold cyan]📱 Wireless Mobile Camera Connect & QR Code[/bold cyan] [dim](Point phone camera at screen to pair)[/dim]", classes="card-title")
                with Horizontal(id="dashboard-mobile-qr-row"):
                    with Vertical(id="dashboard-qr-box"):
                        yield Static(id="dashboard-qr-code")
                    with Vertical(id="dashboard-qr-details"):
                        yield Static(id="dashboard-mobile-info")

            # Row 5: Quick Action Workflow Launchpad
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
        self.set_interval(2.0, self.check_network_and_mobile_status)

    def check_network_and_mobile_status(self) -> None:
        """Polls network interface changes and updates QR code & connected phones live in real time."""
        try:
            from core.services import network_service
            from core.services.device_session_service import session_manager
            from core.services import vault_service

            net_info = network_service.get_mobile_connection_info(port=8000)
            primary_url = net_info.get("primary_url")
            sess_info = session_manager.get_active_sessions()
            dev_count = sess_info.get("active_count", 0)
            batch = vault_service.get_or_create_mobile_batch()
            batch_count = batch.ingested_count

            last_url = getattr(self, "_last_primary_url", None)
            last_devs = getattr(self, "_last_dev_count", None)
            last_batch_count = getattr(self, "_last_batch_count", None)

            if primary_url != last_url or dev_count != last_devs or batch_count != last_batch_count:
                self._last_primary_url = primary_url
                self._last_dev_count = dev_count
                self._last_batch_count = batch_count
                self._update_mobile_qr_widgets(net_info)
        except Exception:
            pass

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

        from django.utils import timezone
        today = timezone.localdate()

        today_images_qs = EvidenceImage.objects.filter(
            Q(ingested_at__date=today) | Q(batch__created_at__date=today)
        )
        today_total_images = today_images_qs.count()
        today_unprocessed = today_images_qs.filter(status=EvidenceImage.Status.NEW).count()
        today_processed = today_images_qs.exclude(status=EvidenceImage.Status.NEW).count()

        today_pairs_qs = pairs_qs.filter(
            Q(created_at__date=today) |
            Q(front_image__batch__created_at__date=today) |
            Q(rear_image__batch__created_at__date=today) |
            Q(front_image__ingested_at__date=today) |
            Q(rear_image__ingested_at__date=today)
        )
        today_total_pairs = today_pairs_qs.count()
        today_pending_review = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW).count()
        today_approved = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED).count()
        today_submitted = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).count()
        today_incomplete = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE).count()
        today_failed = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.FAILED).count()
        today_conflicts = today_pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT).count()
        today_issues = today_incomplete + today_failed + today_conflicts
        today_batches_count = IngestionBatch.objects.filter(created_at__date=today).count()

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
            f"[b]Shift (Today {today.strftime('%Y-%m-%d')}):[/b] "
            f"[bold yellow]{today_total_pairs} pairs[/bold yellow] "
            f"([green]{today_approved} Apprv[/green] │ [cyan]{today_submitted} Sub[/cyan] │ [red]{today_issues} Issues[/red])  │  "
            f"[b]DB:[/b] {db_info['badge']}  │  "
            f"[b]ITMS:[/b] {itms_badge}  │  "
            f"{safety_badge}"
        )
        try:
            self.query_one("#dashboard-header-banner", Static).update(header_text)
        except Exception:
            pass

        # Stage 1: Ingestion
        c1_status = "[bold yellow]● Ingesting...[/bold yellow]" if today_unprocessed > 0 else "[bold green]● All Ingested[/bold green]"
        c1_text = (
            f"[bold cyan]1. Photo Ingestion[/bold cyan]\n"
            f"Shift:    [bold yellow]{today_total_images:3d}[/bold yellow] photos ({today_batches_count} batches)\n"
            f"All-Time: [dim]{total_images:3d} photos[/dim]\n"
            f"{c1_status}\n"
            f"[dim]Click or [I][/dim]"
        )

        # Stage 2: Physical Pairing (U-Turn Walk & Filenames)
        c2_status = f"[bold yellow]● {today_incomplete} Missing Partner[/bold yellow]" if today_incomplete > 0 else "[bold green]● Pairs Formed[/bold green]"
        c2_text = (
            f"[bold yellow]2. Physical Pairing[/bold yellow]\n"
            f"Shift:    [bold yellow]{today_total_pairs:3d}[/bold yellow] pairs formed\n"
            f"All-Time: [dim]{total_pairs:3d} pairs[/dim]\n"
            f"{c2_status}\n"
            f"[dim]Click or [M][/dim]"
        )

        # Stage 3: Joint Vision Engine (Dual-Stream YOLOv8 + Consensus)
        c3_status = f"[bold yellow]● {today_unprocessed} Queued[/bold yellow]" if today_unprocessed > 0 else "[bold green]● 100% Verified[/bold green]"
        c3_text = (
            f"[bold magenta]3. Joint Vision Engine[/bold magenta]\n"
            f"Shift:    [bold yellow]{today_processed:3d}[/bold yellow] verified\n"
            f"All-Time: [dim]{processed_images:3d} processed[/dim]\n"
            f"{c3_status}\n"
            f"[dim]Click or [P][/dim]"
        )

        # Stage 4: Operator Review
        c4_status = f"[bold yellow]● {today_pending_review} Need Review[/bold yellow]" if today_pending_review > 0 else "[bold green]● Shift Clear[/bold green]"
        c4_text = (
            f"[bold dodgerblue]4. Operator Review[/bold dodgerblue]\n"
            f"Shift:    [green]{today_approved:3d} Apprv[/green] │ [yellow]{today_pending_review:2d} Pend[/yellow]\n"
            f"All-Time: [dim]{approved:3d} Apprv │ {pending_review:2d} Pend[/dim]\n"
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
            f"Shift:    [cyan]{today_submitted:3d}[/cyan] submitted\n"
            f"All-Time: [dim]{submitted:3d} submitted ({completed_orders} done)[/dim]\n"
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

        def build_ascii_bar(val: int, tot: int, bar_len: int = 30) -> str:
            if tot <= 0 or val <= 0:
                return "░" * bar_len
            filled = int(round((val / tot) * bar_len))
            filled = max(1, min(bar_len, filled))
            return ("█" * filled) + ("░" * (bar_len - filled))

        pct_sub = round((submitted / total_pairs * 100), 1) if total_pairs else 0.0

        bar_sub = build_ascii_bar(today_submitted, today_total_pairs, 30)
        bar_app = build_ascii_bar(today_approved, today_total_pairs, 30)
        bar_pen = build_ascii_bar(today_pending_review, today_total_pairs, 30)
        bar_iss = build_ascii_bar(today_issues, today_total_pairs, 30)

        dist_text = (
            f"  [bold yellow]Shift Work Distribution (Today {today.strftime('%Y-%m-%d')} - {today_total_pairs} pairs):[/bold yellow]\n"
            f"  [cyan]● Submitted to ITMS[/cyan]:   [bold cyan]{bar_sub}[/bold cyan]  {today_submitted:3d} pairs\n"
            f"  [green]● Approved (Ready)[/green]:     [bold green]{bar_app}[/bold green]  {today_approved:3d} pairs\n"
            f"  [yellow]● Pending Review[/yellow]:       [bold yellow]{bar_pen}[/bold yellow]  {today_pending_review:3d} pairs\n"
            f"  [red]● Issues / Conflicts[/red]:   [bold red]{bar_iss}[/bold red]  {today_issues:3d} pairs\n"
            f"  [dim]───────────────────────────────────────────────────────────────────────[/dim]\n"
            f"  [dim]All-Time Metrics: {submitted} submitted ({pct_sub:.1f}%) │ {approved} approved │ {pending_review} pending │ {issues_total} issues │ {total_pairs} total pairs[/dim]"
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

        # Refresh Wireless Mobile Companion QR Code & URL
        try:
            from core.services import network_service
            net_info = network_service.get_mobile_connection_info(port=8000)
            self._last_primary_url = net_info.get("primary_url")
            self._update_mobile_qr_widgets(net_info)
        except Exception:
            pass

    def _update_mobile_qr_widgets(self, net_info: Dict[str, Any]) -> None:
        try:
            from core.services.qr_generator import generate_rich_qr
            primary_url = net_info.get("primary_url", "http://127.0.0.1:8000/mobile/")
            mode = net_info.get("connection_mode", "WIFI_LAN")
            badge = net_info.get("connection_badge", "Wi-Fi LAN")
            desc = net_info.get("connection_desc", "")

            if mode == "LAPTOP_HOTSPOT":
                status_tag = f"[bold green]🟢 {badge}[/bold green]"
                tip = "[bold cyan]Laptop is broadcasting Hotspot. Connect phone to laptop's Wi-Fi network.[/bold cyan]"
            elif mode == "PHONE_HOTSPOT":
                status_tag = f"[bold green]🟢 {badge}[/bold green]"
                tip = "[bold green]Laptop is connected to Phone Hotspot. Direct mobile pairing active.[/bold green]"
            else:
                status_tag = f"[bold green]🟢 {badge} ({net_info.get('primary_ip')})[/bold green]"
                tip = "[dim]Phone and laptop must be connected to the same Wi-Fi network.[/dim]"

            qr_widget = self.query_one("#dashboard-qr-code", Static)
            if qr_widget:
                qr_widget.update(generate_rich_qr(primary_url, quiet_zone=2))

            info_widget = self.query_one("#dashboard-mobile-info", Static)
            if info_widget:
                from core.services.device_session_service import session_manager
                from core.services import vault_service
                from django.conf import settings

                sess_info = session_manager.get_active_sessions()
                dev_count = sess_info.get("active_count", 0)
                max_dev = sess_info.get("max_allowed", 2)
                devices = sess_info.get("devices", [])
                dev_names = ", ".join(d.get("device_name", "Phone") for d in devices)
                dev_names_str = f" ({dev_names})" if dev_names else ""
                dev_color = "green" if dev_count > 0 else "yellow"

                batch = vault_service.get_or_create_mobile_batch()
                max_batch_photos = getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)
                pairs_count = batch.ingested_count // 2

                cand_list = "\n".join(f"  • [cyan]{c['ip']}[/cyan] ({c['type']})" for c in net_info.get("candidate_urls", [])[:3])
                https_url = net_info.get("https_primary_url")
                https_line = f"🔒 [bold green]Secure Camera (HTTPS):[/bold green] [bold underline green]{https_url}[/bold underline green]\n" if https_url else ""
                info_text = (
                    f"[bold yellow]Direct Mobile Companion URL:[/bold yellow]\n[bold underline cyan]{primary_url}[/bold underline cyan]\n"
                    f"{https_line}\n"
                    f"[bold]Active Network Mode:[/bold] {status_tag}  •  📱 [bold cyan]Phones Connected:[/bold cyan] [bold {dev_color}]{dev_count}/{max_dev}{dev_names_str}[/bold {dev_color}] (Load Protected)\n"
                    f"📦 [bold white]Active Mobile Batch:[/bold white] [bold yellow]{batch.source_label}[/bold yellow] ([cyan]{batch.ingested_count}/{max_batch_photos} photos[/cyan] • {pairs_count}/{max_batch_photos//2} pairs)\n"
                    f"[dim]{desc}[/dim]\n\n"
                    f"[dim]Available Interfaces:[/dim]\n{cand_list}\n\n"
                    f"[bold white]Supported Phone Modes:[/bold white]\n"
                    f"  🏭 [bold green]On-Conveyor[/bold green]: 1-by-1 Front ➔ Rear guided capture (Instant pair)\n"
                    f"  🚶 [bold cyan]Off-Conveyor[/bold cyan]: U-Turn yard walk (Rears 1..N ➔ Fronts N..1)\n\n"
                    f"💡 {tip}\n"
                    f"[dim](Real-time poll: Auto-detects phone connect/disconnect & IP switches every 2s)[/dim]"
                )
                info_widget.update(info_text)
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
