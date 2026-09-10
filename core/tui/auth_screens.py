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
        Binding("q", "quit", "Quit", show=False),
        Binding("escape", "quit", "Quit", show=True),
    ]

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
                            yield Static("[dim]Sign in with your operator credentials.[/dim]", classes="auth-hint")
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
                            yield Input(placeholder="Full Name (e.g. Isaac Kirex)", id="input-reg-fullname")
                            yield Input(placeholder="Email Address (optional)", id="input-reg-email")
                            yield Input(placeholder="Password (min 6 chars)", password=True, id="input-reg-password")
                            yield Input(placeholder="Confirm Password", password=True, id="input-reg-confirm")
                            yield Static("", id="reg-error-msg", classes="error-label")
                            yield Button("Create Operator Account & Sign In", variant="success", id="btn-reg-submit")

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

    # ──────────────────────────────────────────────────────────────────────────
    # Event Handlers & Actions
    # ──────────────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        if input_id in ("input-login-username", "input-login-password"):
            self._handle_login()
        elif input_id in ("input-reg-username", "input-reg-password", "input-reg-confirm"):
            self._handle_register()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-login-submit":
            self._handle_login()
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

    def action_quit(self) -> None:
        self.dismiss(None)
