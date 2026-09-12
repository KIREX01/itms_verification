"""
Landing & Operator Authentication Screens for ITMS Operator TUI.

Provides:
- High-definition ANSI / ASCII art landing banner (converted from assets/ascii-magic-1.png)
- Operator Sign-In with Django User authentication (PBKDF2 SHA-256)
- First-time setup / Register new operator account
- Persistent "Remember Me" session management
"""
import os
from typing import Optional
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)
from django.contrib.auth.models import User
from core.services import auth_service


class LandingAuthScreen(Screen[Optional[User]]):
    """
    Landing portal and authentication screen displayed on startup or logout.
    Features the ITMS graphic banner and multi-tabbed security panel.
    """
    BINDINGS = [
        Binding("escape", "quit", "Exit Copilot", show=True),
    ]

    @property
    def _binding_chain(self):
        """
        Exclude self.app bindings to isolate authentication screens from
        operator shortcuts (1, 2, 3, p, m, u, ^p, etc.).
        """
        try:
            current_app = self.app
            return [
                (node, b_map)
                for node, b_map in super()._binding_chain
                if node != current_app
            ]
        except Exception:
            return super()._binding_chain

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shield_text: Text = auth_service.get_shield_rich_text(width=22, height=14)

    def compose(self) -> ComposeResult:
        with Center():
            with Horizontal(id="auth-container"):
                # Left Column: Crisp ITMS Shield Emblem & System Info
                with Vertical(id="auth-hero-panel"):
                    yield Static(self.shield_text, id="auth-banner")
                    yield Static(
                        "[bold cyan]INTELLIGENT TRANSPORT[/bold cyan]\n"
                        "[bold white]MONITORING SYSTEM[/bold white]\n"
                        "[dim]Verification Copilot[/dim]\n\n"
                        "[bold green]● Secure Terminal[/bold green]",
                        id="auth-hero-title",
                    )
                    yield Static(self._get_db_badge_markup(), id="auth-hero-db")
                    yield Static(
                        "[dim]Uganda MoWT / Police[/dim]\n"
                        "[dim]Local Vault & Live Sync[/dim]",
                        id="auth-hero-footer",
                    )

                # Right Column: Multi-Tabbed Authentication & WebApp Sync
                with Vertical(id="auth-form-panel"):
                    with TabbedContent(id="auth-tabs"):
                        # Tab 1: Operator Sign-In
                        with TabPane("🔑 Operator Sign-In", id="tab-login"):
                            yield Static(self._get_login_db_hint(), id="login-db-hint", classes="auth-hint")
                            yield Input(placeholder="Operator Username", id="input-login-username")
                            yield Input(placeholder="Password", password=True, id="input-login-password")
                            yield Checkbox("Remember this session on this terminal", value=True, id="chk-login-remember")
                            yield Static("", id="login-error-msg", classes="error-label")
                            yield Button("Sign In to Verification Copilot", variant="primary", id="btn-login-submit")

                        # Tab 2: First-Time Setup / Create Account
                        with TabPane("👤 Create Account", id="tab-register"):
                            first_time = User.objects.count() == 0
                            msg = (
                                "[bold yellow]★ First-Time Launch: Create Master Operator[/bold yellow]"
                                if first_time
                                else "[dim]Create a new operator profile for this terminal.[/dim]"
                            )
                            yield Static(msg, classes="auth-hint")
                            yield Input(placeholder="Operator Username (min 3 chars)", id="input-reg-username")
                            yield Input(placeholder="Password (min 6 chars)", password=True, id="input-reg-password")
                            yield Input(placeholder="Confirm Password", password=True, id="input-reg-confirm")
                            yield Static("", id="reg-error-msg", classes="error-label")
                            yield Button("Create Operator Account & Sign In", variant="success", id="btn-reg-submit")

                        # Tab 3: Database & Engine Switcher
                        with TabPane("💾 Database & Engine", id="tab-database"):
                            yield Static("[dim]Active Database Connection & Seamless Engine Switcher[/dim]", classes="auth-hint")
                            yield Static(self._get_db_details_markup(), id="auth-db-details")
                            yield Static("", id="auth-db-feedback", classes="error-label")
                            with Horizontal(classes="auth-db-btn-row"):
                                yield Button("⚡ Switch to PostgreSQL", variant="primary", id="btn-auth-switch-pg")
                                yield Button("⚡ Use Local SQLite", variant="default", id="btn-auth-switch-sqlite")
                            with Vertical(id="auth-pg-inputs-container"):
                                yield Static("[dim]PostgreSQL Parameters (Saved from Last Session):[/dim]")
                                with Horizontal():
                                    yield Input(placeholder="Host (localhost)", id="input-auth-pg-host")
                                    yield Input(placeholder="Port (5432)", id="input-auth-pg-port")
                                with Horizontal():
                                    yield Input(placeholder="Database (itms)", id="input-auth-pg-db")
                                    yield Input(placeholder="User (postgres)", id="input-auth-pg-user")
                                yield Input(placeholder="Password", password=True, id="input-auth-pg-password")
                                with Horizontal():
                                    yield Button("Test Connection", variant="default", id="btn-auth-test-pg")
                                    yield Button("Apply & Connect PostgreSQL", variant="success", id="btn-auth-apply-pg")

        yield Footer()

    def _get_db_badge_markup(self) -> str:
        from core.services import config_service
        info = config_service.get_active_database_info()
        return f"[b]Database Engine:[/b]\n{info.get('badge', '')}\n[dim]{info.get('name', '')}[/dim]"

    def _get_login_db_hint(self) -> str:
        from core.services import config_service
        info = config_service.get_active_database_info()
        return f"[dim]Connected Database:[/dim] {info.get('display', '')}"

    def _get_db_details_markup(self) -> str:
        from core.services import config_service
        info = config_service.get_active_database_info()
        cfg = config_service.load_config()
        db_cfg = cfg.get("database", {})
        last_engine = db_cfg.get("last_session_engine", info.get("vendor", ""))
        last_db = db_cfg.get("last_session_db", info.get("name", ""))
        last_time = db_cfg.get("last_session_at", "")
        time_str = f" ({last_time[:16].replace('T', ' ')})" if last_time else ""
        return (
            f"[b]Currently Active:[/b] {info.get('display', '')}\n"
            f"[b]Last Session Target:[/b] [yellow]{last_engine.upper()}[/yellow] ({last_db}){time_str}\n"
            f"[dim]Operator user accounts are auto-synchronized across both database engines.[/dim]"
        )

    def _refresh_db_views(self) -> None:
        """Refreshes database status badges and labels after a connection change."""
        try:
            hero_badge = self.query_one("#auth-hero-db", Static)
            hero_badge.update(self._get_db_badge_markup())
            hint_label = self.query_one("#login-db-hint", Static)
            hint_label.update(self._get_login_db_hint())
            details_label = self.query_one("#auth-db-details", Static)
            details_label.update(self._get_db_details_markup())
        except Exception:
            pass

    def on_mount(self) -> None:
        from core.services import config_service
        config_service.ensure_operator_accounts_synced()

        prefs = auth_service.get_operator_preferences()
        last_user = prefs.get("last_username", "")
        if last_user:
            user_input = self.query_one("#input-login-username", Input)
            user_input.value = last_user
            pass_input = self.query_one("#input-login-password", Input)
            pass_input.focus()
        else:
            self.query_one("#input-login-username", Input).focus()

        # Prefill PostgreSQL inputs from config.json
        cfg = config_service.load_config()
        db_cfg = cfg.get("database", {})
        try:
            self.query_one("#input-auth-pg-host", Input).value = str(db_cfg.get("postgres_host", "localhost"))
            self.query_one("#input-auth-pg-port", Input).value = str(db_cfg.get("postgres_port", "5432"))
            self.query_one("#input-auth-pg-db", Input).value = str(db_cfg.get("postgres_db", "num"))
            self.query_one("#input-auth-pg-user", Input).value = str(db_cfg.get("postgres_user", "postgres"))
            self.query_one("#input-auth-pg-password", Input).value = str(db_cfg.get("postgres_password", ""))
        except Exception:
            pass

        # Check for fallback warning
        fb_warn = os.environ.get("ITMS_DB_FALLBACK_WARNING")
        if fb_warn:
            try:
                err_label = self.query_one("#login-error-msg", Static)
                err_label.update(f"[bold yellow]⚠️ {fb_warn}[/bold yellow]")
                fb_label = self.query_one("#auth-db-feedback", Static)
                fb_label.update(f"[bold yellow]⚠️ {fb_warn}[/bold yellow]")
            except Exception:
                pass

        # If no users exist in active database, route to Register
        if User.objects.count() == 0:
            tabs = self.query_one("#auth-tabs", TabbedContent)
            tabs.active = "tab-register"

    # ──────────────────────────────────────────────────────────────────────────
    # Event Handlers & Actions
    # ──────────────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        if input_id in ("input-login-username", "input-login-password"):
            self._handle_login()
        elif input_id in ("input-reg-username", "input-reg-password", "input-reg-confirm"):
            self._handle_register()
        elif input_id in ("input-auth-pg-host", "input-auth-pg-port", "input-auth-pg-db", "input-auth-pg-user", "input-auth-pg-password"):
            self._handle_apply_pg()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-login-submit":
            self._handle_login()
        elif btn_id == "btn-reg-submit":
            self._handle_register()
        elif btn_id == "btn-auth-switch-sqlite":
            self._handle_switch_sqlite()
        elif btn_id in ("btn-auth-switch-pg", "btn-auth-apply-pg"):
            self._handle_apply_pg()
        elif btn_id == "btn-auth-test-pg":
            self._handle_test_pg()

    def _handle_login(self) -> None:
        username = self.query_one("#input-login-username", Input).value.strip()
        password = self.query_one("#input-login-password", Input).value
        remember = self.query_one("#chk-login-remember", Checkbox).value
        err_label = self.query_one("#login-error-msg", Static)

        user, err = auth_service.authenticate_operator(username, password)
        if not user:
            err_label.update(f"[bold red]✗ {err}[/bold red]")
            return

        err_label.update("[bold green]✓ Credentials accepted. Loading Copilot...[/bold green]")
        auth_service.save_remembered_session(user, remember=remember)
        self.dismiss(user)

    def _handle_register(self) -> None:
        username = self.query_one("#input-reg-username", Input).value.strip()
        password = self.query_one("#input-reg-password", Input).value
        confirm = self.query_one("#input-reg-confirm", Input).value
        err_label = self.query_one("#reg-error-msg", Static)

        if not username:
            err_label.update("[bold red]✗ Username is required.[/bold red]")
            return

        if len(password) < 6:
            err_label.update("[bold red]✗ Password must be at least 6 characters.[/bold red]")
            return

        if password != confirm:
            err_label.update("[bold red]✗ Passwords do not match.[/bold red]")
            return

        user, err = auth_service.create_operator_account(
            username=username,
            password=password,
            full_name=username,
            email="",
        )
        if not user:
            err_label.update(f"[bold red]✗ {err}[/bold red]")
            return

        err_label.update("[bold green]✓ Account created successfully! Launching Copilot...[/bold green]")
        auth_service.save_remembered_session(user, remember=True)
        self.dismiss(user)

    def _handle_switch_sqlite(self) -> None:
        from core.services import config_service
        fb_label = self.query_one("#auth-db-feedback", Static)
        fb_label.update("[yellow]Switching to local SQLite 3...[/yellow]")
        res = config_service.switch_database("sqlite", persist_config=True)
        if res.get("success"):
            fb_label.update(f"[bold green]✓ {res['message']}[/bold green]")
            self._refresh_db_views()
            # If user accounts exist in SQLite, switch directly to login tab
            if User.objects.count() > 0:
                tabs = self.query_one("#auth-tabs", TabbedContent)
                tabs.active = "tab-login"
                self.query_one("#input-login-username", Input).focus()
            self.notify("Switched to SQLite database successfully!", severity="information")
        else:
            fb_label.update(f"[bold red]✗ {res.get('error', 'Switch failed')}[/bold red]")

    def _handle_test_pg(self) -> None:
        from core.services import config_service
        host = self.query_one("#input-auth-pg-host", Input).value.strip() or "localhost"
        port = self.query_one("#input-auth-pg-port", Input).value.strip() or "5432"
        db = self.query_one("#input-auth-pg-db", Input).value.strip() or "num"
        user = self.query_one("#input-auth-pg-user", Input).value.strip() or "postgres"
        pwd = self.query_one("#input-auth-pg-password", Input).value
        fb_label = self.query_one("#auth-db-feedback", Static)

        fb_label.update(f"[yellow]Testing connection to {user}@{host}:{port}/{db}...[/yellow]")
        res = config_service.test_postgres_connection(
            host=host, port=port, dbname=db, user=user, password=pwd, timeout=5
        )
        if res.get("success"):
            fb_label.update(f"[bold green]✓ {res['message']}[/bold green]")
        else:
            fb_label.update(f"[bold red]✗ {res.get('error', 'Connection failed')}[/bold red]")

    def _handle_apply_pg(self) -> None:
        from core.services import config_service
        host = self.query_one("#input-auth-pg-host", Input).value.strip() or "localhost"
        port = self.query_one("#input-auth-pg-port", Input).value.strip() or "5432"
        db = self.query_one("#input-auth-pg-db", Input).value.strip() or "num"
        user = self.query_one("#input-auth-pg-user", Input).value.strip() or "postgres"
        pwd = self.query_one("#input-auth-pg-password", Input).value
        fb_label = self.query_one("#auth-db-feedback", Static)

        fb_label.update(f"[yellow]Connecting and migrating PostgreSQL ({db}@{host}:{port})...[/yellow]")
        res = config_service.switch_database(
            engine="postgresql",
            host=host,
            port=port,
            dbname=db,
            user=user,
            password=pwd,
            run_migrations=True,
            persist_config=True,
        )
        if res.get("success"):
            synced = res.get("synced_users", 0)
            sync_note = f" ({synced} operator account(s) synced)" if synced > 0 else ""
            fb_label.update(f"[bold green]✓ {res['message']}{sync_note}[/bold green]")
            self._refresh_db_views()
            if User.objects.count() > 0:
                tabs = self.query_one("#auth-tabs", TabbedContent)
                tabs.active = "tab-login"
                self.query_one("#input-login-username", Input).focus()
            self.notify(f"Connected to PostgreSQL ({db})!", severity="information")
        else:
            fb_label.update(f"[bold red]✗ {res.get('error', 'Switch failed')}[/bold red]")

    def action_quit(self) -> None:
        self.dismiss(None)
