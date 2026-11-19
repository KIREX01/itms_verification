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
from textual import work
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
    Select,
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

                        # Tab 2: First-time setup only. Everyone else requests access (the administrator
                        # grants it in the backoffice and an invite creates their account).
                        first_time = User.objects.count() == 0
                        with TabPane("👤 Create Administrator" if first_time else "🔑 Request Access", id="tab-register"):
                            if not first_time:
                                yield Static(
                                    "[bold]New to CLOSE Copilot?[/bold]\n\n"
                                    "Accounts are no longer created here. Ask for access on the web:\n"
                                    f"  [bold cyan]{self._request_access_url()}[/bold cyan]\n\n"
                                    "[dim]The administrator reviews your request. If approved you'll get an email with a "
                                    "6-digit code and a link to create your account. Team Leaders can also invite "
                                    "Helpers and Quality Inspectors from Settings → My Team.[/dim]",
                                    classes="auth-hint",
                                )
                            else:
                                msg = (
                                    "[bold yellow]★ First-Time Launch: Create Master Operator & Link Warehouse[/bold yellow]"
                                    if first_time
                                    else "[dim]Create an operator account and link your verified ITMS warehouse facility.[/dim]"
                                )
                                yield Static(msg, classes="auth-hint")
                                yield Input(placeholder="Operator Username (min 3 chars)", id="input-reg-username")
                                yield Input(placeholder="Full Name (optional)", id="input-reg-fullname")
                                yield Input(placeholder="Password (min 6 chars)", password=True, id="input-reg-password")
                                yield Input(placeholder="Confirm Password", password=True, id="input-reg-confirm")

                                yield Static(
                                    "[bold cyan]🏢 ITMS Portal Account (Checks & Links Operating Warehouse)[/bold cyan]",
                                    id="reg-itms-title",
                                )
                                yield Static(
                                    "[dim]System queries ITMS to identify your linked bonded warehouse facility before granting access.[/dim]",
                                    id="reg-itms-hint",
                                    classes="auth-hint",
                                )
                                yield Input(placeholder="ITMS Username / Email (e.g. operator@cfao.ug)", id="input-reg-itms-email")
                                yield Input(placeholder="ITMS Password", password=True, id="input-reg-itms-password")
                                yield Select(
                                    self._get_warehouse_options(),
                                    prompt="Select Facility (Auto-Discovered from ITMS)",
                                    id="sel-reg-warehouse",
                                )
                                with Horizontal(id="reg-itms-btn-row"):
                                    yield Button("🔍 Check ITMS & Discover Warehouse", variant="primary", id="btn-reg-check-itms")
                                yield Static("", id="reg-error-msg", classes="error-label")
                                yield Button("Create Operator Account & Proceed", variant="success", id="btn-reg-submit")

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

    def _request_access_url(self) -> str:
        """Access is requested on the central server (the only control centre)."""
        from django.conf import settings
        return getattr(settings, "CLOSE_CENTRAL_URL", "https://close.kirex.online").rstrip("/") + "/access/request/"

    def _get_warehouse_options(self) -> list[tuple[str, str]]:
        from core.services import bond_service
        try:
            warehouses = bond_service.get_all_discovered_warehouses()
            return [(f"{w['name']} ({w['code']})", w["code"]) for w in warehouses]
        except Exception:
            return []

    def _update_reg_error(self, markup: str) -> None:
        try:
            self.query_one("#reg-error-msg", Static).update(markup)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-login-submit":
            self._handle_login()
        elif btn_id == "btn-reg-check-itms":
            self._handle_verify_itms()
        elif btn_id == "btn-reg-submit":
            self._handle_register()

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

    @work(thread=True)
    def _handle_verify_itms(self) -> None:
        itms_email = self.query_one("#input-reg-itms-email", Input).value.strip()
        itms_password = self.query_one("#input-reg-itms-password", Input).value

        if not itms_email or not itms_password:
            self.app.call_from_thread(
                self._update_reg_error,
                "[bold red]✗ Enter ITMS username/email and password to verify warehouse.[/bold red]",
            )
            return

        self.app.call_from_thread(
            self._update_reg_error,
            "[bold yellow]⌛ Authenticating with ITMS & querying linked warehouse...[/bold yellow]",
        )

        from core.services.itms_web_client import ITMSWebClient
        client = ITMSWebClient()
        res = client.login(itms_email, itms_password, remember_me=True)
        success = res[0] if isinstance(res, tuple) else bool(res)
        msg = res[1] if isinstance(res, tuple) and len(res) > 1 else ""

        if not success:
            err = msg or "ITMS Authentication failed. Please check credentials."
            self.app.call_from_thread(
                self._update_reg_error,
                f"[bold red]✗ {err}[/bold red]",
            )
            return

        discovery = client.discover_user_assigned_warehouse()
        discovered_code = discovery.get("code")
        discovered_name = discovery.get("name") or discovered_code
        all_warehouses = discovery.get("available_warehouses", [])

        options = [(f"{w['name']} ({w['code']})", w["code"]) for w in all_warehouses]
        if not options and discovered_code:
            options = [(f"{discovered_name} ({discovered_code})", discovered_code)]

        def update_ui():
            sel = self.query_one("#sel-reg-warehouse", Select)
            sel.set_options(options)
            if discovered_code:
                sel.value = discovered_code
                self._update_reg_error(
                    f"[bold green]✓ ITMS Verified! Linked Warehouse: {discovered_name} ({discovered_code})[/bold green]"
                )
            else:
                self._update_reg_error(
                    "[bold yellow]✓ ITMS Authenticated! Please confirm your operating warehouse from dropdown.[/bold yellow]"
                )

        self.app.call_from_thread(update_ui)

    @work(thread=True)
    def _handle_register(self) -> None:
        if User.objects.exists():
            # Self-registration is closed: accounts come from access requests granted in the backoffice.
            return
        username = self.query_one("#input-reg-username", Input).value.strip()
        full_name = self.query_one("#input-reg-fullname", Input).value.strip() or username
        password = self.query_one("#input-reg-password", Input).value
        confirm = self.query_one("#input-reg-confirm", Input).value
        itms_email = self.query_one("#input-reg-itms-email", Input).value.strip()
        itms_password = self.query_one("#input-reg-itms-password", Input).value
        sel_wh = self.query_one("#sel-reg-warehouse", Select).value

        if not username:
            self.app.call_from_thread(self._update_reg_error, "[bold red]✗ Username is required.[/bold red]")
            return
        if len(password) < 6:
            self.app.call_from_thread(self._update_reg_error, "[bold red]✗ Password must be at least 6 characters.[/bold red]")
            return
        if password != confirm:
            self.app.call_from_thread(self._update_reg_error, "[bold red]✗ Passwords do not match.[/bold red]")
            return

        assigned_code = ""
        assigned_name = ""
        assigned_uuid = ""
        itms_cookies = {}
        itms_csrf = ""

        if itms_email and itms_password:
            self.app.call_from_thread(
                self._update_reg_error,
                "[bold yellow]⌛ Authenticating with ITMS & verifying warehouse...[/bold yellow]",
            )
            from core.services.itms_web_client import ITMSWebClient
            client = ITMSWebClient()
            res = client.login(itms_email, itms_password, remember_me=True)
            success = res[0] if isinstance(res, tuple) else bool(res)
            msg = res[1] if isinstance(res, tuple) and len(res) > 1 else ""
            if not success:
                self.app.call_from_thread(
                    self._update_reg_error,
                    f"[bold red]✗ ITMS Verification Failed: {msg or 'Invalid ITMS credentials'}. Warehouse cannot be linked.[/bold red]",
                )
                return

            discovery = client.discover_user_assigned_warehouse()
            assigned_code = str(sel_wh or discovery.get("code") or "").strip().upper()
            assigned_name = discovery.get("name") or assigned_code
            assigned_uuid = discovery.get("warehouse_uuid", "")
            itms_cookies = client.session_store.session.cookies
            itms_csrf = client.session_store.session.csrf_token

        elif sel_wh:
            assigned_code = str(sel_wh).strip().upper()
            from core.services import bond_service
            all_bonds = bond_service.get_all_discovered_warehouses()
            matched = next((b for b in all_bonds if b.get("code") == assigned_code), None)
            assigned_name = matched.get("name") if matched else f"{assigned_code} Bonded Warehouse"
            assigned_uuid = matched.get("warehouse_uuid", "") if matched else ""
        else:
            self.app.call_from_thread(
                self._update_reg_error,
                "[bold red]✗ ITMS account is required to verify which warehouse you are linked to. Enter ITMS credentials to proceed.[/bold red]",
            )
            return

        if not assigned_code:
            self.app.call_from_thread(
                self._update_reg_error,
                "[bold red]✗ No warehouse could be verified for this account. Please select your operating facility.[/bold red]",
            )
            return

        # Create local operator account
        user, err = auth_service.create_operator_account(
            username=username,
            password=password,
            full_name=full_name,
            email=itms_email or f"{username}@local.terminal",
        )
        if not user:
            self.app.call_from_thread(self._update_reg_error, f"[bold red]✗ {err}[/bold red]")
            return
        # First-time bootstrap: the very first local account is this install's administrator.
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        # Link OperatorProfile & OperatorITMSSession
        from core.models import OperatorProfile, OperatorITMSSession, Warehouse
        wh_instance = Warehouse.objects.filter(code=assigned_code).first()

        OperatorProfile.objects.update_or_create(
            user=user,
            defaults={
                "active_facility": assigned_code,
                "allowed_facilities": [assigned_code],
            },
        )

        if itms_email:
            OperatorITMSSession.objects.update_or_create(
                user=user,
                defaults={
                    "itms_email": itms_email,
                    "cookies": itms_cookies,
                    "csrf_token": itms_csrf,
                    "is_authenticated": True,
                    "active_bond_code": assigned_code,
                    "active_bond_name": assigned_name,
                    "active_warehouse_uuid": assigned_uuid,
                    "active_warehouse": wh_instance,
                    "last_status_message": f"Verified and linked to {assigned_name} ({assigned_code})",
                },
            )

        from core.services import bond_service
        bond_service.set_active_bond(assigned_code, assigned_name)

        auth_service.save_remembered_session(user, remember=True)

        def finish():
            self._update_reg_error(
                f"[bold green]✓ Account created! Linked to {assigned_name} ({assigned_code}). Launching...[/bold green]"
            )
            self.dismiss(user)

        self.app.call_from_thread(finish)

    def action_quit(self) -> None:
        self.dismiss(None)
