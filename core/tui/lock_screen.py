"""
TUI Master Security Lock Screen.

Provides a dedicated security gate prompting for a 6-digit Master PIN
on startup or upon manual/idle console lock (Ctrl+L).
Enforces:
- Rate limiting (3 failed attempts triggers a 60-second lockout)
- Constant-time PIN verification against PBKDF2 hash or TUI_MASTER_PIN env
- Clean terminal exit on cancellation
"""
import time
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from core.services import config_service


INPUT_MASTER_PIN_ID = "input-master-pin"
INPUT_MASTER_PIN_SELECTOR = "#input-master-pin"


class TUIMasterLockScreen(ModalScreen[bool]):
    """
    Dedicated Master Security Lock modal screen for ITMS Operator TUI.
    Prompts for 6-digit PIN before granting console access.
    """

    BINDINGS = [
        Binding("escape", "quit_app", "Quit Copilot", show=True),
    ]

    CSS = """
    TUIMasterLockScreen {
        align: center middle;
        background: rgba(10, 15, 25, 0.95);
    }

    #lock-modal-container {
        width: 68;
        height: auto;
        border: heavy $warning;
        background: $surface;
        padding: 1 2;
    }

    #lock-banner {
        text-align: center;
        margin-bottom: 1;
    }

    #lock-subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #lock-error-label {
        text-align: center;
        color: $error;
        min-height: 1;
        margin-bottom: 1;
    }

    #input-master-pin {
        width: 32;
        text-align: center;
        border: tall $primary;
    }

    #lock-actions-row {
        align: center middle;
        margin-top: 1;
        height: auto;
    }

    #lock-actions-row Button {
        margin: 0 1;
    }

    #lock-footer-info {
        text-align: center;
        color: $text-disabled;
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failed_attempts = 0
        self.lockout_until = 0.0
        self._countdown_timer = None

    def compose(self) -> ComposeResult:
        with Center(), Vertical(id="lock-modal-container"):
                yield Static(
                    "[bold yellow]🔒 CLOSE COPILOT[/bold yellow]\n"
                    "[bold white]MASTER CONSOLE SECURITY LOCK[/bold white]",
                    id="lock-banner",
                )
                yield Static(
                    "Terminal console access is restricted. Please enter the 6-digit Master PIN.",
                    id="lock-subtitle",
                )
                yield Static("", id="lock-error-label")
                with Center():
                    yield Input(
                        placeholder="● ● ● ● ● ●",
                        password=True,
                        id=INPUT_MASTER_PIN_ID,
                        max_length=12,
                    )
                with Horizontal(id="lock-actions-row"):
                    yield Button("UNLOCK [Enter]", variant="success", id="btn-unlock-pin")
                    yield Button("EXIT [Esc]", variant="error", id="btn-quit-pin")
                yield Static(
                    # Never print the PIN itself: anyone at the terminal could read it.
                    "[dim]Master PIN is set in Settings or via TUI_MASTER_PIN │ Ask your supervisor if unknown[/dim]",
                    id="lock-footer-info",
                )

    def on_mount(self) -> None:
        self.query_one(INPUT_MASTER_PIN_SELECTOR, Input).focus()
        self._countdown_timer = self.set_interval(1.0, self._check_lockout_tick)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == INPUT_MASTER_PIN_ID:
            self._attempt_unlock()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-unlock-pin":
            self._attempt_unlock()
        elif event.button.id == "btn-quit-pin":
            self.action_quit_app()

    def action_quit_app(self) -> None:
        """Exits the application immediately if operator aborts lock screen."""
        self.app.exit()

    def _check_lockout_tick(self) -> None:
        """Updates lockout countdown if terminal is in rate-limit cooldown."""
        err_label = self.query_one("#lock-error-label", Static)
        pin_input = self.query_one(INPUT_MASTER_PIN_SELECTOR, Input)

        now = time.time()
        if now < self.lockout_until:
            remaining = int(self.lockout_until - now) + 1
            err_label.update(f"[bold red]⛔ Console Locked! Cooldown active: {remaining}s remaining...[/bold red]")
            pin_input.disabled = True
        else:
            if pin_input.disabled:
                pin_input.disabled = False
                pin_input.value = ""
                pin_input.focus()
                err_label.update("[dim cyan]Cooldown expired. You may try entering the PIN again.[/dim cyan]")

    def _attempt_unlock(self) -> None:
        pin_input = self.query_one(INPUT_MASTER_PIN_SELECTOR, Input)
        err_label = self.query_one("#lock-error-label", Static)

        now = time.time()
        if now < self.lockout_until:
            remaining = int(self.lockout_until - now) + 1
            err_label.update(f"[bold red]⛔ Terminal locked. Wait {remaining}s.[/bold red]")
            return

        entered_pin = pin_input.value.strip()
        if not entered_pin:
            err_label.update("[bold yellow]Please enter the 6-digit Master PIN.[/bold yellow]")
            return

        if not config_service.is_tui_master_pin_set():
            err_label.update("[bold yellow]No Master PIN is set on this computer. "
                             "Set one with: itms pin set[/bold yellow]")
            return

        if config_service.verify_tui_master_pin(entered_pin):
            self.failed_attempts = 0
            if self._countdown_timer:
                self._countdown_timer.stop()
            self.dismiss(True)
        else:
            self.failed_attempts += 1
            pin_input.value = ""
            pin_input.focus()

            cfg = config_service.load_config()
            max_attempts = int(cfg.get("security", {}).get("tui_max_failed_attempts", 3))
            lockout_sec = int(cfg.get("security", {}).get("tui_lockout_seconds", 60))

            if self.failed_attempts >= max_attempts:
                self.lockout_until = now + lockout_sec
                self.failed_attempts = 0
                pin_input.disabled = True
                err_label.update(
                    f"[bold red]⛔ {max_attempts} Failed Attempts! Terminal locked for {lockout_sec}s.[/bold red]"
                )
            else:
                remaining_tries = max_attempts - self.failed_attempts
                err_label.update(
                    f"[bold red]❌ Invalid Master PIN.[/bold red] [yellow]({remaining_tries} attempt{'s' if remaining_tries > 1 else ''} left)[/yellow]"
                )


class TUIExpiredLockScreen(ModalScreen[bool]):
    """
    Dedicated Security Lock Screen presented when an operator's time-bound grant
    or authorized device access window expires.
    Completely locks console interaction until access is renewed, supervisor unlocks
    with Master PIN, or operator signs out.
    """

    BINDINGS = [
        Binding("escape", "quit_app", "Quit Copilot", show=True),
    ]

    CSS = """
    TUIExpiredLockScreen {
        align: center middle;
        background: rgba(18, 10, 10, 0.96);
    }

    #expired-modal-container {
        width: 78;
        height: auto;
        border: heavy $error;
        background: $surface;
        padding: 1 2;
    }

    #expired-banner {
        text-align: center;
        margin-bottom: 1;
    }

    #expired-subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #expired-reason-box {
        background: #201010;
        border: solid #ef4444;
        color: #fca5a5;
        padding: 1 2;
        text-align: center;
        margin-bottom: 1;
    }

    #expired-code-row {
        align: center middle;
        height: 3;
        margin-top: 1;
        margin-bottom: 1;
    }

    #inp-expired-code {
        width: 30;
        text-align: center;
        margin-right: 1;
        border: tall $success;
    }

    #expired-actions-row {
        align: center middle;
        margin-top: 1;
        height: auto;
    }

    #expired-actions-row Button {
        margin: 0 1;
    }

    #expired-error-label {
        text-align: center;
        min-height: 1;
        margin-top: 0;
        margin-bottom: 1;
    }

    #expired-footer-info {
        text-align: center;
        color: $text-disabled;
        margin-top: 1;
    }
    """

    def __init__(self, reason: str = "", expires_at=None, **kwargs):
        super().__init__(**kwargs)
        self.reason = reason or "Your authorized time-bound window has elapsed."
        self.expires_at = expires_at

    def compose(self) -> ComposeResult:
        with Center(), Vertical(id="expired-modal-container"):
            yield Static(
                "[bold red]⛔ TIME-BOUND ACCESS EXPIRED[/bold red]\n"
                "[bold white]CLOSE COPILOT CONSOLE LOCKED[/bold white]",
                id="expired-banner",
            )
            yield Static(
                "Terminal operations are locked because your authorized session has expired.\n"
                "Enter a 6-digit renewal code, request supervisor Master PIN unlock, or sign out.",
                id="expired-subtitle",
            )
            yield Static(f"[bold yellow]Reason:[/bold yellow] {self.reason}", id="expired-reason-box")
            yield Static("", id="expired-error-label")

            with Horizontal(id="expired-code-row"):
                yield Input(
                    placeholder="Enter 6-Digit Renewal Code",
                    id="inp-expired-code",
                    max_length=12,
                )
                yield Button("Renew Access [Enter]", variant="success", id="btn-expired-renew")

            with Horizontal(id="expired-actions-row"):
                yield Button("🔑 Master PIN Unlock", variant="primary", id="btn-expired-master-pin")
                yield Button("👤 Sign Out / Switch", variant="warning", id="btn-expired-logout")
                yield Button("✕ Exit Copilot [Esc]", variant="error", id="btn-expired-quit")

            yield Static(
                "[dim]Time-bound grants ensure strict zero-trust operational safety across bonded facilities.[/dim]",
                id="expired-footer-info",
            )

    def on_mount(self) -> None:
        self.query_one("#inp-expired-code", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "inp-expired-code":
            self._attempt_renew_code()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-expired-renew":
            self._attempt_renew_code()
        elif bid == "btn-expired-master-pin":
            self._prompt_master_pin()
        elif bid == "btn-expired-logout":
            self._handle_logout()
        elif bid == "btn-expired-quit":
            self.action_quit_app()

    def action_quit_app(self) -> None:
        """Terminates terminal cleanly when operator chooses to quit."""
        self.app.exit()

    def _attempt_renew_code(self) -> None:
        code_input = self.query_one("#inp-expired-code", Input)
        code = code_input.value.strip()
        err_label = self.query_one("#expired-error-label", Static)

        if not code:
            err_label.update("[bold red]Please enter your 6-digit activation / renewal code.[/bold red]")
            return

        from core.services import device_access_service
        user = getattr(self.app, "current_user", None)
        email = (user.email if user else "") or (user.username if user else "")

        ok, msg, dev = device_access_service.activate_with_code(
            request=None,
            email=email,
            activation_code=code,
        )

        if ok:
            err_label.update(f"[bold green]✓ {msg} Access restored![/bold green]")
            self.app.notify(f"Access renewed: {msg}", severity="information")
            self.dismiss(True)
        else:
            err_label.update(f"[bold red]❌ {msg or 'Invalid or expired renewal code.'}[/bold red]")
            code_input.value = ""
            code_input.focus()

    def _prompt_master_pin(self) -> None:
        """Pushes TUIMasterLockScreen to allow supervisor override with Master PIN."""
        self.app.push_screen(TUIMasterLockScreen(), self._on_master_pin_override)

    def _on_master_pin_override(self, unlocked: bool) -> None:
        if unlocked:
            from core.services import device_access_service
            user = getattr(self.app, "current_user", None)
            target = user.email or user.username if user else ""
            if target:
                device_access_service.extend_or_set_expiry(target, duration_hours=4, admin_user=user)
            self.app.notify("Console unlocked with Master PIN (+4h temporary window granted).", severity="success")
            self.dismiss(True)

    def _handle_logout(self) -> None:
        from core.services import auth_service
        auth_service.clear_remembered_session()
        self.app.current_user = None
        self.dismiss(False)



class InstallActivationScreen(ModalScreen[bool]):
    """
    Shown before sign-in when this install is not activated, was copied from another computer,
    or was revoked. Only a one-time enrollment code from the backoffice (Installs page) unlocks it.
    """

    BINDINGS = [Binding("escape", "quit_app", "Quit", show=True)]

    CSS = """
    InstallActivationScreen { align: center middle; background: rgba(10, 12, 18, 0.97); }
    #install-box { width: 84; height: auto; border: heavy $warning; background: $surface; padding: 1 2; }
    #install-title { text-align: center; margin-bottom: 1; }
    #install-msg { margin-bottom: 1; }
    #install-error { color: $error; margin-top: 1; }
    .install-input { margin-bottom: 1; }
    #install-actions { height: auto; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        from django.conf import settings
        from governance.services import install_guard

        state = install_guard.install_state()
        with Center():
            with Vertical(id="install-box"):
                yield Static("[bold yellow]🔒 CLOSE Copilot install is locked[/bold yellow]", id="install-title")
                yield Static(install_guard.lock_message(state), id="install-msg")
                yield Static(f"[dim]This computer: {install_guard.short_fingerprint(install_guard.machine_fingerprint())}[/dim]")
                yield Input(value=getattr(settings, "CLOSE_CENTRAL_URL", "https://close.kirex.online"),
                            placeholder="Central server (https://...)", id="install-central", classes="install-input")
                yield Input(placeholder="Enrollment code from Backoffice -> Installs", id="install-code",
                            classes="install-input")
                yield Input(placeholder="Name for this computer (optional)", id="install-name", classes="install-input")
                with Horizontal(id="install-actions"):
                    yield Button("Activate", variant="primary", id="btn-install-activate")
                    yield Button("Quit", variant="error", id="btn-install-quit")
                yield Static("", id="install-error")

    def on_mount(self) -> None:
        from governance.services import install_guard
        if install_guard.install_state() == install_guard.MOVED:
            install_guard.quarantine_moved_copy()
        self.query_one("#install-code", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-install-quit":
            self.action_quit_app()
        elif event.button.id == "btn-install-activate":
            self._activate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._activate()

    def _activate(self) -> None:
        from governance.services import policy_sync

        central = self.query_one("#install-central", Input).value.strip()
        code = self.query_one("#install-code", Input).value.strip()
        name = self.query_one("#install-name", Input).value.strip()
        err = self.query_one("#install-error", Static)
        if not central or not code:
            err.update("Enter the central server address and the enrollment code.")
            return
        try:
            policy_sync.enroll_with_central(central, code, name=name)
        except Exception as exc:
            err.update(f"Activation failed: {exc}")
            return
        self.app.notify("Install activated. Accounts and roles now come from the central server.")
        self.dismiss(True)

    def action_quit_app(self) -> None:
        self.app.exit()
