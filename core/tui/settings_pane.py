"""
Settings and system-wide configuration pane for the ITMS Operator TUI.

Provides an interactive GUI for operators and developers:
- Operator Mode:
  * Submission safety mode (Dry-run toggle, Step 3 auto-confirmation, safety guarantee)
  * Evidence Vault file location chooser & startup prompt toggle
  * Developer Mode switch
- Developer Mode (revealed when Developer Mode is ON):
  * U-Turn Turnaround time threshold adjustment for Tier 2 spatial matcher
  * Submission & Network timeouts and circuit breaker fine-tuning
  * Smart on-the-fly multipart compression parameters
  * Vision pipeline & OCR intelligence controls
  * Storage retention lifecycle policies
  * PostgreSQL connection and database engine switcher
"""
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connection

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static, Switch

from core.services import config_service, vault_service


class SettingsPane(Vertical):
    """Interactive Settings and System Configuration Pane (Tab 6)."""

    BINDINGS = [
        ("down", "scroll_down", "Scroll Down"),
        ("up", "scroll_up", "Scroll Up"),
        ("pagedown", "page_down", "Page Down"),
        ("pageup", "page_up", "Page Up"),
        ("home", "scroll_home", "Top"),
        ("end", "scroll_end", "Bottom"),
    ]

    def action_scroll_down(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_scroll_down()
        except Exception:
            pass

    def action_scroll_up(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_scroll_up()
        except Exception:
            pass

    def action_page_down(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_page_down()
        except Exception:
            pass

    def action_page_up(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_page_up()
        except Exception:
            pass

    def action_scroll_home(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_scroll_home()
        except Exception:
            pass

    def action_scroll_end(self) -> None:
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).action_scroll_end()
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        cfg = config_service.load_config()

        # Operator settings
        dry_run = cfg.get("submission", {}).get("dry_run_mode", True)
        step3 = cfg.get("submission", {}).get("submit_step3", True)
        show_safety = cfg.get("system", {}).get("show_safety_guarantee", False)
        vault_prompt_startup = cfg.get("storage", {}).get("prompt_vault_on_startup", True)
        dev_mode = cfg.get("system", {}).get("developer_mode", False)

        # Developer settings: Trajectory Matcher
        uturn_threshold = str(cfg.get("matcher", {}).get("uturn_threshold_seconds", 1800))

        # Developer settings: Network & Submission
        timeout = str(cfg.get("submission", {}).get("request_timeout_seconds", 30))
        circuit_breaker = str(cfg.get("submission", {}).get("circuit_breaker_threshold", 3))
        compression_enabled = cfg.get("compression", {}).get("enabled", True)
        comp_max_dim = str(cfg.get("compression", {}).get("max_dimension", 1920))
        comp_quality = str(cfg.get("compression", {}).get("jpeg_quality", 88))
        outbox_enabled = cfg.get("outbox", {}).get("enabled", True)
        outbox_interval = str(cfg.get("outbox", {}).get("auto_sync_interval_seconds", 15))

        # Developer settings: Vision & OCR
        ensemble = cfg.get("vision", {}).get("adaptive_ensemble_voting", True)
        syntax_corr = cfg.get("vision", {}).get("positional_disambiguation", True)
        auto_pre = cfg.get("vision", {}).get("auto_preprocess_ingest", True)
        min_ocr_conf = str(cfg.get("vision", {}).get("min_ocr_confidence", 0.55))
        detector_conf = str(cfg.get("vision", {}).get("detector_conf_threshold", 0.35))

        # Developer settings: Storage retention
        crop_days = str(cfg.get("storage", {}).get("crops_retention_days", 7))
        export_days = str(cfg.get("storage", {}).get("export_retention_days", 30))
        vault_days = str(cfg.get("storage", {}).get("vault_retention_days", 7))

        # Developer settings: Database
        db_cfg = cfg.get("database", {})
        active_engine = db_cfg.get("engine", "sqlite").lower()
        use_pg = (connection.vendor != "sqlite" or active_engine in ("postgres", "postgresql"))
        pg_host = str(db_cfg.get("postgres_host", "localhost"))
        pg_port = str(db_cfg.get("postgres_port", "5432"))
        pg_db = str(db_cfg.get("postgres_db", "itms"))
        pg_user = str(db_cfg.get("postgres_user", "postgres"))
        pg_pass = str(db_cfg.get("postgres_password", ""))

        db_info = config_service.get_active_database_info()["display"]
        current_vault_root = str(vault_service.get_vault_root())

        with Vertical(id="settings-container"):
            # Top toolbar
            yield Static(
                f"[bold cyan]⚙️ System-Wide Configuration & Preferences[/bold cyan]  │  "
                f"Active Engine: {db_info}  │  "
                "[dim]Press [Esc] to exit edit mode, [1-6] to navigate, or [F1-F6] anytime[/dim]",
                id="settings-top-bar",
            )

            with VerticalScroll(id="settings-scroll-body"):
                # ==========================================
                # SECTION 1: OPERATOR ESSENTIALS (Visible)
                # ==========================================

                # Card 1A: Submission Safety Controls
                with Vertical(classes="settings-card"):
                    yield Static("[bold yellow]🚀 Submission & Safety Controls[/bold yellow]", classes="settings-card-title")

                    with Horizontal(classes="settings-row"):
                        yield Static("[b]Dry-Run Safety Mode[/b]\n[dim]Simulate ITMS submission without remote mutating actions[/dim]", classes="settings-label")
                        yield Switch(value=dry_run, id="switch-dry-run")

                    with Horizontal(classes="settings-row"):
                        yield Static("[b]Auto-Submit Step 3 Confirmation[/b]\n[dim]Automatically complete final installation step when photos upload[/dim]", classes="settings-label")
                        yield Switch(value=step3, id="switch-submit-step3")

                    with Horizontal(classes="settings-row"):
                        yield Static("[b]Show Safety Guarantee Card[/b]\n[dim]Display audit/live mode guarantee banner in ITMS Hub[/dim]", classes="settings-label")
                        yield Switch(value=show_safety, id="switch-show-safety")

                # Card 1B: Evidence Vault Storage Location
                with Vertical(classes="settings-card"):
                    yield Static("[bold green]📦 Evidence Vault Storage Location[/bold green]", classes="settings-card-title")

                    with Horizontal(classes="settings-row"):
                        yield Static(
                            f"[b]Active Vault Root:[/b] [bold yellow]{current_vault_root}[/bold yellow]\n"
                            "[dim]Directory where incoming evidence photos, EXIF metadata, and hashes are stored[/dim]",
                            id="label-vault-path",
                            classes="settings-label",
                        )
                        yield Button("📂 Choose / Change Vault Directory", variant="primary", id="btn-change-vault")

                    with Horizontal(classes="settings-row"):
                        yield Static("[b]Prompt for Vault Location on Startup[/b]\n[dim]Allow operator to choose or confirm vault directory whenever the app launches[/dim]", classes="settings-label")
                        yield Switch(value=vault_prompt_startup, id="switch-vault-prompt-startup")

                # Card 1C: Permissions & Developer Mode Switch
                with Vertical(classes="settings-card"):
                    yield Static("[bold red]🛡️ Permissions & System Mode[/bold red]", classes="settings-card-title")

                    with Horizontal(classes="settings-row"):
                        yield Static(
                            "[b]Developer & Test Mode[/b]\n"
                            "[dim]Toggle ON to reveal advanced technical variables: U-Turn trajectory adjustment, side-by-side image comparison, vision tuning, multipart compression, and PostgreSQL switcher.[/dim]",
                            classes="settings-label",
                        )
                        yield Switch(value=dev_mode, id="switch-dev-mode")

                # ==========================================
                # SECTION 2: DEVELOPER MODE CONTROLS
                # (Revealed ONLY when Developer Mode is ON)
                # ==========================================
                with Vertical(
                    classes="" if dev_mode else "hidden",
                    id="developer-settings-container",
                ):
                    # Card 2A: Trajectory & Spatial Matcher Calibration
                    with Vertical(classes="settings-card"):
                        yield Static("[bold cyan]🔄 Trajectory & Spatial Matcher Calibration (Developer Mode)[/bold cyan]", classes="settings-card-title")

                        with Horizontal(classes="settings-row"):
                            yield Static(
                                "[b]U-Turn Turnaround Threshold (seconds)[/b]\n"
                                "[dim]Maximum turnaround delta between first & last photos for Tier 2 reverse U-turn walk alignment (default: 1800s)[/dim]",
                                classes="settings-label",
                            )
                            yield Input(value=uturn_threshold, id="input-uturn-threshold", classes="settings-input")

                    # Card 2B: Submission & Network Fine-Tuning
                    with Vertical(classes="settings-card"):
                        yield Static("[bold cyan]⚡ Submission & Network Fine-Tuning (Developer Mode)[/bold cyan]", classes="settings-card-title")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Smart Multipart Photo Compression[/b]\n[dim]Downsample 12-48MP photos for HTTP upload (Master photos in vault remain untouched)[/dim]", classes="settings-label")
                            yield Switch(value=compression_enabled, id="switch-compression-enabled")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Max Photo Dimension (px)[/b]\n[dim]Max width or height in px (LANCZOS downsampling, default: 1920)[/dim]", classes="settings-label")
                            yield Input(value=comp_max_dim, id="input-compression-dim", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]JPEG Compression Quality (1-100)[/b]\n[dim]Quality factor for multipart payload (default: 88)[/dim]", classes="settings-label")
                            yield Input(value=comp_quality, id="input-compression-quality", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Offline Outbox & Auto-Sync[/b]\n[dim]Auto-transition network drops to OFFLINE_OUTBOX and background drain[/dim]", classes="settings-label")
                            yield Switch(value=outbox_enabled, id="switch-outbox-enabled")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Auto-Sync Ping Interval (seconds)[/b]\n[dim]Heartbeat interval to probe stock.itms.ug and drain outbox queue[/dim]", classes="settings-label")
                            yield Input(value=outbox_interval, id="input-outbox-interval", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Circuit Breaker Threshold[/b]\n[dim]Halt batch after N consecutive network disconnects (default: 3)[/dim]", classes="settings-label")
                            yield Input(value=circuit_breaker, id="input-circuit-breaker", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]HTTP Request Timeout (seconds)[/b]\n[dim]Socket timeout per API request before retry (default: 30)[/dim]", classes="settings-label")
                            yield Input(value=timeout, id="input-timeout", classes="settings-input")

                    # Card 2C: Vision Pipeline & OCR Intelligence
                    with Vertical(classes="settings-card"):
                        yield Static("[bold magenta]🧠 Vision Pipeline & OCR Intelligence (Developer Mode)[/bold magenta]", classes="settings-card-title")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Adaptive Contrast Ensemble Voting[/b]\n[dim]Run 5-variant contrast ensemble on challenging or shadowed plates[/dim]", classes="settings-label")
                            yield Switch(value=ensemble, id="switch-ensemble")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Uganda Plate Positional Disambiguation[/b]\n[dim]Enforce Ugandan vehicle registration syntax on OCR characters[/dim]", classes="settings-label")
                            yield Switch(value=syntax_corr, id="switch-syntax-corr")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Auto-Enhance Photos at Ingestion[/b]\n[dim]Dynamic CLAHE contrast normalization and unsharp masking[/dim]", classes="settings-label")
                            yield Switch(value=auto_pre, id="switch-auto-pre")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Minimum OCR Confidence Threshold[/b]\n[dim]Reject plate detections below this confidence (default: 0.55)[/dim]", classes="settings-label")
                            yield Input(value=min_ocr_conf, id="input-min-ocr-conf", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]YOLO Detector Confidence Threshold[/b]\n[dim]Reject bounding boxes below this confidence (default: 0.35)[/dim]", classes="settings-label")
                            yield Input(value=detector_conf, id="input-detector-conf", classes="settings-input")

                    # Card 2D: Storage Retention & Lifecycle Policies
                    with Vertical(classes="settings-card"):
                        yield Static("[bold green]📦 Storage Retention & Lifecycle Policies (Developer Mode)[/bold green]", classes="settings-card-title")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Temporary Crops Retention (Days)[/b]\n[dim]Delete localized plate crops older than N days[/dim]", classes="settings-label")
                            yield Input(value=crop_days, id="input-crop-days", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Shift Export Reports Retention (Days)[/b]\n[dim]Delete timestamped CSV shift reports older than N days[/dim]", classes="settings-label")
                            yield Input(value=export_days, id="input-export-days", classes="settings-input")

                        with Horizontal(classes="settings-row"):
                            yield Static("[b]Master Vault Photos Retention (Days)[/b]\n[dim]Prune submitted photos older than N days from vault storage[/dim]", classes="settings-label")
                            yield Input(value=vault_days, id="input-vault-days", classes="settings-input")

                    # Card 2E: Developer Database Engine & PostgreSQL Connection
                    with Vertical(classes="settings-card", id="card-dev-database"):
                        yield Static("[bold cyan]🗄️ Developer Database Engine & PostgreSQL Connection (Developer Mode)[/bold cyan]", classes="settings-card-title")

                        with Horizontal(classes="settings-row"):
                            yield Static(
                                "[b]Database Backend Engine[/b]\n"
                                "[dim]Toggle ON to use PostgreSQL Server; OFF to use local SQLite (db.sqlite3)[/dim]",
                                classes="settings-label",
                            )
                            yield Switch(value=use_pg, id="switch-use-postgres")

                        with Vertical(id="pg-config-container", classes="" if use_pg else "hidden"):
                            with Horizontal(classes="settings-row"):
                                yield Static("[b]PostgreSQL Host[/b]\n[dim]Server hostname or IP (default: localhost)[/dim]", classes="settings-label")
                                yield Input(value=pg_host, placeholder="localhost", id="input-pg-host", classes="settings-input")

                            with Horizontal(classes="settings-row"):
                                yield Static("[b]PostgreSQL Port[/b]\n[dim]Default port: 5432[/dim]", classes="settings-label")
                                yield Input(value=pg_port, placeholder="5432", id="input-pg-port", classes="settings-input")

                            with Horizontal(classes="settings-row"):
                                yield Static("[b]Database Name[/b]\n[dim]PostgreSQL database name[/dim]", classes="settings-label")
                                yield Input(value=pg_db, placeholder="itms", id="input-pg-db", classes="settings-input")

                            with Horizontal(classes="settings-row"):
                                yield Static("[b]Database User[/b]\n[dim]Database username (e.g. postgres)[/dim]", classes="settings-label")
                                yield Input(value=pg_user, placeholder="postgres", id="input-pg-user", classes="settings-input")

                            with Horizontal(classes="settings-row"):
                                yield Static("[b]Database Password[/b]\n[dim]Database password (masked)[/dim]", classes="settings-label")
                                yield Input(value=pg_pass, password=True, placeholder="Password", id="input-pg-password", classes="settings-input")

                        with Horizontal(classes="settings-db-buttons"):
                            yield Button("🔌 Test PostgreSQL Connection", variant="primary", id="btn-test-pg")
                            yield Button("🔄 Switch Active Database Engine", variant="warning", id="btn-switch-db")

                        yield Static(
                            "[dim]Ready. Test PostgreSQL reachability or switch active database engine.[/dim]",
                            id="settings-db-feedback",
                        )

            # Bottom Action Bar
            with Horizontal(id="settings-actions-bar"):
                yield Button("💾 Save Configuration", variant="success", id="btn-save-settings")
                yield Button("↺ Reset to Defaults", variant="warning", id="btn-reset-settings")
                yield Button("🧹 Clean Storage Now", variant="primary", id="btn-clean-settings")
                yield Button("📦 Backup SQLite DB", variant="default", id="btn-backup-settings")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        sw_id = event.switch.id
        if sw_id == "switch-dev-mode":
            try:
                dev_box = self.query_one("#developer-settings-container")
                scroll_body = self.query_one("#settings-scroll-body", VerticalScroll)
                if event.value:
                    dev_box.remove_class("hidden")
                else:
                    dev_box.add_class("hidden")
                scroll_body.refresh(layout=True)
            except Exception:
                pass
        elif sw_id == "switch-use-postgres":
            try:
                pg_box = self.query_one("#pg-config-container")
                scroll_body = self.query_one("#settings-scroll-body", VerticalScroll)
                if event.value:
                    pg_box.remove_class("hidden")
                else:
                    pg_box.add_class("hidden")
                scroll_body.refresh(layout=True)
            except Exception:
                pass

        # Auto-persist state
        self._save_all_settings(silent=True)

        try:
            app = self.app
        except Exception:
            app = None

        if app:
            if sw_id == "switch-dry-run":
                status_text = "[bold yellow]DRY-RUN SIMULATION[/bold yellow]" if event.value else "[bold red]LIVE SUBMISSION[/bold red]"
                app.notify(
                    f"Dry-Run Mode: {'ACTIVE' if event.value else 'OFF (LIVE MODE)'}",
                    severity="warning" if not event.value else "information",
                )
                if hasattr(app, "log_message"):
                    app.log_message(
                        f"Safety switch toggled: Submission mode is now {status_text}.",
                        level="WARNING" if not event.value else "INFO",
                    )
            elif sw_id == "switch-submit-step3":
                app.notify(f"Auto-Submit Step 3: {'ENABLED' if event.value else 'DISABLED'}")
            elif sw_id == "switch-dev-mode":
                app.notify(f"Developer Mode: {'ENABLED' if event.value else 'DISABLED'}")
                if hasattr(app, "log_message"):
                    app.log_message(
                        f"System Mode: Developer Mode {'ENABLED' if event.value else 'DISABLED'}.",
                        level="INFO",
                    )

    def on_key(self, event: events.Key) -> None:
        """Exit edit mode / blur input on Escape key and refocus scroll body."""
        if event.key == "escape":
            try:
                self.query_one("#settings-scroll-body", VerticalScroll).focus()
            except Exception:
                self.app.set_focus(None)
            event.prevent_default()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Blurs input on Enter key so operator can navigate tabs with digits 1-6 or scroll."""
        try:
            self.query_one("#settings-scroll-body", VerticalScroll).focus()
        except Exception:
            self.app.set_focus(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        app = self.app

        if btn_id == "btn-save-settings":
            self._save_all_settings()
        elif btn_id == "btn-reset-settings":
            self._reset_to_defaults()
        elif btn_id == "btn-clean-settings":
            app.action_clean_storage()
        elif btn_id == "btn-backup-settings":
            self._backup_sqlite_database()
        elif btn_id == "btn-test-pg":
            self.action_test_postgres_connection()
        elif btn_id == "btn-switch-db":
            self.action_switch_active_database()
        elif btn_id == "btn-change-vault":
            self.action_change_vault()

    def action_change_vault(self) -> None:
        """Launches Vault Location dialog and refreshes display."""
        from core.tui.dialogs import VaultLocationDialog

        def _on_vault_selected(new_path):
            self.refresh_vault_display()

        self.app.push_screen(VaultLocationDialog(is_startup=False), _on_vault_selected)

    def refresh_vault_display(self) -> None:
        """Refreshes the displayed Evidence Vault path label."""
        try:
            lbl = self.query_one("#label-vault-path", Static)
            active_root = str(vault_service.get_vault_root())
            lbl.update(
                f"[b]Active Vault Root:[/b] [bold yellow]{active_root}[/bold yellow]\n"
                "[dim]Directory where incoming evidence photos, EXIF metadata, and hashes are stored[/dim]"
            )
        except Exception:
            pass

    def _set_db_feedback(self, text: str, color: str = "white") -> None:
        try:
            fb = self.query_one("#settings-db-feedback", Static)
            fb.update(f"[{color}]{text}[/{color}]")
        except Exception:
            pass

    def _update_top_bar(self) -> None:
        try:
            db_info = config_service.get_active_database_info()["display"]
            top_bar = self.query_one("#settings-top-bar", Static)
            top_bar.update(
                f"[bold cyan]⚙️ System-Wide Configuration & Preferences[/bold cyan]  │  "
                f"Active Engine: {db_info}  │  "
                "[dim]Press [Esc] to exit edit mode, [1-6] to navigate, or [F1-F6] anytime[/dim]"
            )
        except Exception:
            pass

    @work(thread=True)
    def action_test_postgres_connection(self) -> None:
        """Tests reachability of PostgreSQL server using supplied credentials."""
        try:
            host = self.query_one("#input-pg-host", Input).value.strip() or "localhost"
            port = self.query_one("#input-pg-port", Input).value.strip() or "5432"
            dbname = self.query_one("#input-pg-db", Input).value.strip() or "itms"
            user = self.query_one("#input-pg-user", Input).value.strip() or "postgres"
            password = self.query_one("#input-pg-password", Input).value
        except Exception as exc:
            self.app.call_from_thread(self._set_db_feedback, f"Input error: {exc}", "bold red")
            return

        self.app.call_from_thread(
            self._set_db_feedback, f"Testing PostgreSQL reachability to {dbname}@{host}:{port}...", "yellow"
        )
        res = config_service.test_postgres_connection(
            host=host, port=port, dbname=dbname, user=user, password=password, timeout=5
        )

        if res.get("success"):
            self.app.call_from_thread(self._set_db_feedback, f"✓ {res['message']}", "bold green")
            self.app.call_from_thread(
                self.app.notify,
                f"PostgreSQL reachable ({res.get('latency_ms')}ms)!",
                severity="information",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"[bold green]✓ PostgreSQL Test OK:[/bold green] {res['message']}",
                level="SUCCESS",
            )
        else:
            err = res.get("message", "Connection failed.")
            self.app.call_from_thread(self._set_db_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(
                self.app.notify,
                f"PostgreSQL connection failed: {res.get('error')}",
                severity="error",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"[bold red]✗ PostgreSQL Connection Failed:[/bold red] {res.get('error')}",
                level="ERROR",
            )

    @work(thread=True)
    def action_switch_active_database(self) -> None:
        """Switches live database between SQLite and PostgreSQL, running migrations if required."""
        try:
            use_pg = self.query_one("#switch-use-postgres", Switch).value
            host = self.query_one("#input-pg-host", Input).value.strip() or "localhost"
            port = self.query_one("#input-pg-port", Input).value.strip() or "5432"
            dbname = self.query_one("#input-pg-db", Input).value.strip() or "itms"
            user = self.query_one("#input-pg-user", Input).value.strip() or "postgres"
            password = self.query_one("#input-pg-password", Input).value
        except Exception as exc:
            self.app.call_from_thread(self._set_db_feedback, f"Input error: {exc}", "bold red")
            return

        engine = "postgresql" if use_pg else "sqlite"
        target_desc = f"PostgreSQL ({dbname}@{host}:{port})" if use_pg else "SQLite 3 (db.sqlite3)"

        self.app.call_from_thread(
            self._set_db_feedback, f"Connecting and switching to {target_desc}...", "yellow"
        )
        self.app.call_from_thread(
            self.app.log_message, f"Switching active database connection to {target_desc}...", level="INFO"
        )

        res = config_service.switch_database(
            engine=engine,
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            run_migrations=True,
        )

        if res.get("success"):
            synced = res.get("synced_users", 0)
            sync_note = f" ({synced} operator account(s) synced)" if synced > 0 else ""
            if getattr(self.app, "current_user", None):
                try:
                    from django.contrib.auth.models import User
                    from core.services import auth_service
                    active_u = User.objects.filter(username=self.app.current_user.username).first()
                    if active_u:
                        self.app.current_user = active_u
                        auth_service.save_remembered_session(active_u, remember=True)
                except Exception:
                    pass

            self.app.call_from_thread(self._set_db_feedback, f"✓ {res['message']}{sync_note}", "bold green")
            self.app.call_from_thread(self._update_top_bar)
            self.app.call_from_thread(self.app.reload_data)
            try:
                metrics = self.app.query_one("#metrics")
                self.app.call_from_thread(metrics.refresh_metrics)
            except Exception:
                pass
            self.app.call_from_thread(
                self.app.notify,
                f"Active database switched to {target_desc}!{sync_note}",
                severity="information",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"[bold green]✓ Database Engine Switched:[/bold green] {res['message']}{sync_note}",
                level="SUCCESS",
            )
        else:
            err = res.get("message", "Switch failed.")
            self.app.call_from_thread(self._set_db_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(
                self.app.notify,
                f"Database switch failed: {res.get('error')}",
                severity="error",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"[bold red]✗ Database Switch Error:[/bold red] {res.get('error')}",
                level="ERROR",
            )

    def _save_all_settings(self, silent: bool = False) -> None:
        """Collects GUI inputs and persists to secure/config.json."""
        try:
            app = self.app
        except Exception:
            app = None
        try:
            # 1. Operator settings
            dry_run = self.query_one("#switch-dry-run", Switch).value
            step3 = self.query_one("#switch-submit-step3", Switch).value
            show_safety = self.query_one("#switch-show-safety", Switch).value
            vault_prompt = self.query_one("#switch-vault-prompt-startup", Switch).value
            dev_mode = self.query_one("#switch-dev-mode", Switch).value

            cfg = config_service.load_config()

            cfg.setdefault("submission", {})
            cfg["submission"]["dry_run_mode"] = dry_run
            cfg["submission"]["submit_step3"] = step3

            cfg.setdefault("system", {})
            cfg["system"]["developer_mode"] = dev_mode
            cfg["system"]["show_safety_guarantee"] = show_safety

            cfg.setdefault("storage", {})
            cfg["storage"]["prompt_vault_on_startup"] = vault_prompt

            # 2. Developer Mode: Matcher
            try:
                uturn = int(self.query_one("#input-uturn-threshold", Input).value.strip() or 1800)
                cfg.setdefault("matcher", {})
                cfg["matcher"]["uturn_threshold_seconds"] = uturn
            except Exception:
                pass

            # 3. Developer Mode: Network & Compression
            try:
                comp_enabled = self.query_one("#switch-compression-enabled", Switch).value
                comp_dim = int(self.query_one("#input-compression-dim", Input).value.strip() or 1920)
                comp_quality = int(self.query_one("#input-compression-quality", Input).value.strip() or 88)
                cfg.setdefault("compression", {})
                cfg["compression"]["enabled"] = comp_enabled
                cfg["compression"]["max_dimension"] = comp_dim
                cfg["compression"]["jpeg_quality"] = comp_quality
            except Exception:
                pass

            try:
                outbox_enabled = self.query_one("#switch-outbox-enabled", Switch).value
                outbox_interval = int(self.query_one("#input-outbox-interval", Input).value.strip() or 15)
                timeout = int(self.query_one("#input-timeout", Input).value.strip() or 30)
                circuit_breaker = int(self.query_one("#input-circuit-breaker", Input).value.strip() or 3)
                cfg.setdefault("outbox", {})
                cfg["outbox"]["enabled"] = outbox_enabled
                cfg["outbox"]["auto_sync_interval_seconds"] = outbox_interval
                cfg["submission"]["request_timeout_seconds"] = timeout
                cfg["submission"]["circuit_breaker_threshold"] = circuit_breaker
            except Exception:
                pass

            # 4. Developer Mode: Vision & OCR
            try:
                ensemble = self.query_one("#switch-ensemble", Switch).value
                syntax_corr = self.query_one("#switch-syntax-corr", Switch).value
                auto_pre = self.query_one("#switch-auto-pre", Switch).value
                min_ocr = float(self.query_one("#input-min-ocr-conf", Input).value.strip() or 0.55)
                det_conf = float(self.query_one("#input-detector-conf", Input).value.strip() or 0.35)
                cfg.setdefault("vision", {})
                cfg["vision"]["adaptive_ensemble_voting"] = ensemble
                cfg["vision"]["positional_disambiguation"] = syntax_corr
                cfg["vision"]["auto_preprocess_ingest"] = auto_pre
                cfg["vision"]["min_ocr_confidence"] = min_ocr
                cfg["vision"]["detector_conf_threshold"] = det_conf
            except Exception:
                pass

            # 5. Developer Mode: Storage Retention
            try:
                crop_days = int(self.query_one("#input-crop-days", Input).value.strip() or 7)
                export_days = int(self.query_one("#input-export-days", Input).value.strip() or 30)
                vault_days = int(self.query_one("#input-vault-days", Input).value.strip() or 7)
                cfg["storage"]["crops_retention_days"] = crop_days
                cfg["storage"]["export_retention_days"] = export_days
                cfg["storage"]["vault_retention_days"] = vault_days
            except Exception:
                pass

            # 6. Developer Mode: PostgreSQL Database
            try:
                use_pg = self.query_one("#switch-use-postgres", Switch).value
                pg_host = self.query_one("#input-pg-host", Input).value.strip() or "localhost"
                pg_port = self.query_one("#input-pg-port", Input).value.strip() or "5432"
                pg_db = self.query_one("#input-pg-db", Input).value.strip() or "itms"
                pg_user = self.query_one("#input-pg-user", Input).value.strip() or "postgres"
                pg_pass = self.query_one("#input-pg-password", Input).value

                cfg.setdefault("database", {})
                cfg["database"]["engine"] = "postgresql" if use_pg else "sqlite"
                cfg["database"]["postgres_host"] = pg_host
                cfg["database"]["postgres_port"] = pg_port
                cfg["database"]["postgres_db"] = pg_db
                cfg["database"]["postgres_user"] = pg_user
                cfg["database"]["postgres_password"] = pg_pass
            except Exception:
                pass

            config_service.save_config(cfg)

            # Update live django settings
            setattr(settings, "DEVELOPER_MODE", dev_mode)
            setattr(settings, "ITMS_WEB_DRY_RUN", dry_run)
            setattr(settings, "ITMS_SUBMIT_STEP3", step3)
            self.refresh_vault_display()

            # Dynamically refresh ITMS Hub pane if mounted
            if app:
                try:
                    itms_pane = app.query_one("#itms-connection-pane")
                    if hasattr(itms_pane, "_refresh_status_card"):
                        itms_pane._refresh_status_card()
                except Exception:
                    pass

                if not silent:
                    app.notify("Configuration saved successfully to secure/config.json!", severity="information")
                    dev_status = "ENABLED" if dev_mode else "DISABLED"
                    app.log_message(
                        f"[bold green]✓ Settings Persisted:[/bold green] Dry-Run={dry_run}, Auto-Step3={step3}, DevMode={dev_status}, Vault={vault_service.get_vault_root()}.",
                        level="SUCCESS",
                    )
        except Exception as exc:
            if app and not silent:
                app.notify(f"Failed to save settings: {exc}", severity="error")
                app.log_message(f"Settings save error: {exc}", level="ERROR")

    def _reset_to_defaults(self) -> None:
        """Resets inputs to factory defaults in secure/config.json."""
        try:
            app = self.app
        except Exception:
            app = None
        try:
            config_service.save_config(config_service.DEFAULT_CONFIG)

            self.query_one("#switch-dry-run", Switch).value = True
            self.query_one("#switch-submit-step3", Switch).value = True
            self.query_one("#switch-show-safety", Switch).value = False
            self.query_one("#switch-vault-prompt-startup", Switch).value = True
            self.query_one("#switch-dev-mode", Switch).value = False

            try:
                self.query_one("#input-uturn-threshold", Input).value = "1800"
                self.query_one("#switch-compression-enabled", Switch).value = True
                self.query_one("#input-compression-dim", Input).value = "1920"
                self.query_one("#input-compression-quality", Input).value = "88"
                self.query_one("#switch-outbox-enabled", Switch).value = True
                self.query_one("#input-outbox-interval", Input).value = "15"
                self.query_one("#input-timeout", Input).value = "30"
                self.query_one("#input-circuit-breaker", Input).value = "3"

                self.query_one("#switch-ensemble", Switch).value = True
                self.query_one("#switch-syntax-corr", Switch).value = True
                self.query_one("#switch-auto-pre", Switch).value = True
                self.query_one("#input-min-ocr-conf", Input).value = "0.55"
                self.query_one("#input-detector-conf", Input).value = "0.35"

                self.query_one("#input-crop-days", Input).value = "7"
                self.query_one("#input-export-days", Input).value = "30"
                self.query_one("#input-vault-days", Input).value = "7"

                self.query_one("#switch-use-postgres", Switch).value = False
                self.query_one("#input-pg-host", Input).value = "localhost"
                self.query_one("#input-pg-port", Input).value = "5432"
                self.query_one("#input-pg-db", Input).value = "itms"
                self.query_one("#input-pg-user", Input).value = "postgres"
                self.query_one("#input-pg-password", Input).value = ""
            except Exception:
                pass

            self.refresh_vault_display()

            # Dynamically refresh ITMS Hub pane if mounted
            try:
                itms_pane = app.query_one("#itms-connection-pane")
                if hasattr(itms_pane, "_refresh_status_card"):
                    itms_pane._refresh_status_card()
            except Exception:
                pass

            app.notify("Settings reset to factory defaults.", severity="information")
            app.log_message("[yellow]Settings reset to factory defaults (Dry-Run: ON, DevMode: OFF, SafetyCard: OFF).[/yellow]", level="INFO")
        except Exception as exc:
            app.notify(f"Reset failed: {exc}", severity="error")

    def _backup_sqlite_database(self) -> None:
        """Creates a timestamped backup of db.sqlite3 in backups/."""
        app = self.app
        if connection.vendor != "sqlite":
            app.notify(f"Active engine is {connection.vendor}; SQLite backup not applicable.", severity="warning")
            return

        try:
            db_path = Path(connection.settings_dict.get("NAME", "db.sqlite3"))
            if not db_path.exists():
                app.notify("db.sqlite3 not found on disk.", severity="error")
                return

            backup_dir = Path(settings.BASE_DIR) / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"db_backup_{timestamp}.sqlite3"

            # Force checkpoint first so all WAL frames are in main file
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")

            shutil.copy2(db_path, backup_file)
            size_mb = round(backup_file.stat().st_size / (1024 * 1024), 2)

            app.notify(f"Database backed up ({size_mb} MB) to backups/{backup_file.name}", severity="information")
            app.log_message(
                f"[bold green]✓ Database Backup Created:[/bold green] [cyan]backups/{backup_file.name}[/cyan] ({size_mb} MB).",
                level="SUCCESS",
            )
        except Exception as exc:
            app.notify(f"Backup failed: {exc}", severity="error")
            app.log_message(f"Database backup error: {exc}", level="ERROR")
