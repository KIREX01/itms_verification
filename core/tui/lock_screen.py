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
        with Center():
            with Vertical(id="lock-modal-container"):
                yield Static(
                    "[bold yellow]🔒 ITMS VERIFICATION COPILOT[/bold yellow]\n"
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
                        id="input-master-pin",
                        max_length=12,
                    )
                with Horizontal(id="lock-actions-row"):
                    yield Button("UNLOCK [Enter]", variant="success", id="btn-unlock-pin")
                    yield Button("EXIT [Esc]", variant="error", id="btn-quit-pin")
                yield Static(
                    "[dim]Default Master PIN: 739104 │ Configurable in Settings & TUI_MASTER_PIN[/dim]",
                    id="lock-footer-info",
                )

    def on_mount(self) -> None:
        self.query_one("#input-master-pin", Input).focus()
        self._countdown_timer = self.set_interval(1.0, self._check_lockout_tick)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input-master-pin":
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
        pin_input = self.query_one("#input-master-pin", Input)

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
        pin_input = self.query_one("#input-master-pin", Input)
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
