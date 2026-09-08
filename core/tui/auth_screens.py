"""
Landing & Operator Authentication Screens for ITMS Operator TUI.

Provides:
- High-definition ANSI / ASCII art landing banner (converted from assets/ascii-magic-1.png)
- Operator Sign-In with Django User authentication (PBKDF2 SHA-256)
- First-time setup / Register new operator account
- Persistent "Remember Me" session management
- Connect & Test ITMS WebApp account credentials & tokens
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
        Binding("q", "quit", "Quit", show=False),
        Binding("escape", "quit", "Quit", show=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.banner_text: Text = auth_service.get_banner_rich_text()

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="auth-container"):
                # Top Landing Banner
                yield Static(self.banner_text, id="auth-banner")
                yield Static(
                    "[bold cyan]INTELLIGENT TRANSPORT MONITORING SYSTEM[/bold cyan]  │  "
                    "[bold white]Verification & Evidence Copilot[/bold white]",
                    id="auth-subtitle",
                )

                # Tabbed Authentication & Setup Panel
                with TabbedContent(id="auth-tabs"):
                    # Tab 1: Operator Sign-In
                    with TabPane("🔑 Operator Sign-In", id="tab-login"):
                        yield Static("[dim]Sign in with your local operator credentials.[/dim]", classes="auth-hint")
                        yield Input(placeholder="Operator Username", id="input-login-username")
                        yield Input(placeholder="Password", password=True, id="input-login-password")
                        yield Checkbox("Remember this session on this terminal", value=True, id="chk-login-remember")
                        yield Static("", id="login-error-msg", classes="error-label")
                        yield Button("Sign In to Verification Copilot", variant="primary", id="btn-login-submit")

                    # Tab 2: First-Time Setup / Create Account
                    with TabPane("👤 Create Operator Account", id="tab-register"):
                        first_time = User.objects.count() == 0
                        msg = (
                            "[bold yellow]★ First-Time Launch: Create Master Operator Account[/bold yellow]"
                            if first_time
                            else "[dim]Create a new operator profile for this terminal.[/dim]"
                        )
                        yield Static(msg, classes="auth-hint")
                        yield Input(placeholder="Operator Username (min 3 chars)", id="input-reg-username")
                        yield Input(placeholder="Full Name (e.g. Isaac Kirex)", id="input-reg-fullname")
                        yield Input(placeholder="Email Address (optional)", id="input-reg-email")
                        yield Input(placeholder="Password (min 6 chars)", password=True, id="input-reg-password")
                        yield Input(placeholder="Confirm Password", password=True, id="input-reg-confirm")
                        yield Static("", id="reg-error-msg", classes="error-label")
                        yield Button("Create Operator Account & Sign In", variant="success", id="btn-reg-submit")

                    # Tab 3: Connect ITMS WebApp
                    with TabPane("🌐 Connect ITMS WebApp", id="tab-itms"):
                        yield Static(self._format_itms_status(), id="itms-status-summary")
                        yield Input(placeholder="ITMS WebApp URL", value="https://stock.itms.ug", id="input-itms-url")
                        yield Input(placeholder="ITMS Username / Email", id="input-itms-username")
                        yield Input(placeholder="ITMS Password", password=True, id="input-itms-password")
                        yield Static("", id="itms-error-msg", classes="error-label")
                        with Horizontal(classes="itms-button-row"):
                            yield Button("Test Server Reachability", variant="default", id="btn-itms-ping")
                            yield Button("Authenticate & Save Live Token", variant="primary", id="btn-itms-login")

        yield Footer()

    def on_mount(self) -> None:
        prefs = auth_service.get_operator_preferences()
        last_user = prefs.get("last_username", "")
        if last_user:
            user_input = self.query_one("#input-login-username", Input)
            user_input.value = last_user
            pass_input = self.query_one("#input-login-password", Input)
            pass_input.focus()
        else:
            self.query_one("#input-login-username", Input).focus()

        # If no users exist in database, default to the Register tab
        if User.objects.count() == 0:
            tabs = self.query_one("#auth-tabs", TabbedContent)
            tabs.active = "tab-register"

    def _format_itms_status(self) -> str:
        status = auth_service.get_itms_status()
        auth_color = "green" if status["authenticated"] else "yellow"
        online_color = "green" if status["online"] else "red"
        auth_str = "ACTIVE TOKEN" if status["authenticated"] else "NO TOKEN / SIMULATED"
        online_str = "ONLINE" if status["online"] else "OFFLINE"

        lines = [
            "[bold underline]ITMS WebApp Link Status[/bold underline]:",
            f"• Target Endpoint: [cyan]{status['url']}[/cyan]",
            f"• Server Status:   [{online_color}]{online_str}[/{online_color}]",
            f"• Authentication:  [{auth_color}]{auth_str}[/{auth_color}]" + (f" ({status['user_email']})" if status['user_email'] else ""),
        ]
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────────
    # Event Handlers & Actions
    # ──────────────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        if input_id in ("input-login-username", "input-login-password"):
            self._handle_login()
        elif input_id in ("input-reg-username", "input-reg-password", "input-reg-confirm"):
            self._handle_register()
        elif input_id in ("input-itms-username", "input-itms-password"):
            self._handle_itms_login()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-login-submit":
            self._handle_login()
        elif btn_id == "btn-reg-submit":
            self._handle_register()
        elif btn_id == "btn-itms-ping":
            self._handle_itms_ping()
        elif btn_id == "btn-itms-login":
            self._handle_itms_login()

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
        fullname = self.query_one("#input-reg-fullname", Input).value.strip()
        email = self.query_one("#input-reg-email", Input).value.strip()
        password = self.query_one("#input-reg-password", Input).value
        confirm = self.query_one("#input-reg-confirm", Input).value
        err_label = self.query_one("#reg-error-msg", Static)

        if password != confirm:
            err_label.update("[bold red]✗ Passwords do not match.[/bold red]")
            return

        user, err = auth_service.create_operator_account(
            username=username,
            password=password,
            full_name=fullname,
            email=email,
        )
        if not user:
            err_label.update(f"[bold red]✗ {err}[/bold red]")
            return

        err_label.update("[bold green]✓ Account created successfully! Launching Copilot...[/bold green]")
        auth_service.save_remembered_session(user, remember=True)
        self.dismiss(user)

    @work(thread=True)
    def _handle_itms_ping(self) -> None:
        err_label = self.query_one("#itms-error-msg", Static)
        self.call_from_thread(err_label.update, "[yellow]Pinging ITMS WebApp server...[/yellow]")
        from core.services.itms_client import ITMSClient
        target_url = self.query_one("#input-itms-url", Input).value.strip() or None
        client = ITMSClient(base_url=target_url) if target_url else ITMSClient()
        ping_res = client.test_connection()
        if ping_res.get("success"):
            msg = f"[bold green]✓ {ping_res.get('message', 'Server is online.')}[/bold green]"
        else:
            msg = f"[bold red]✗ {ping_res.get('error', 'Connection failed.')}[/bold red]"
        self.call_from_thread(err_label.update, msg)
        summary = self.query_one("#itms-status-summary", Static)
        self.call_from_thread(summary.update, self._format_itms_status())

    @work(thread=True)
    def _handle_itms_login(self) -> None:
        err_label = self.query_one("#itms-error-msg", Static)
        email = self.query_one("#input-itms-username", Input).value.strip()
        password = self.query_one("#input-itms-password", Input).value
        url = self.query_one("#input-itms-url", Input).value.strip() or None

        if not email or not password:
            self.call_from_thread(err_label.update, "[bold red]✗ Please enter ITMS username/email and password.[/bold red]")
            return

        self.call_from_thread(err_label.update, "[yellow]Authenticating against ITMS WebApp...[/yellow]")
        ok, msg = auth_service.connect_itms_account(email=email, password=password, base_url=url)
        color = "bold green" if ok else "bold red"
        self.call_from_thread(err_label.update, f"[{color}]{msg}[/{color}]")
        summary = self.query_one("#itms-status-summary", Static)
        self.call_from_thread(summary.update, self._format_itms_status())

    def action_quit(self) -> None:
        self.dismiss(None)
