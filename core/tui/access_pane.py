"""
TUI Device, MAC/IP Firewall & Time-Bound Email Access Management Console (`Tab 9: ACCESS`).

Centralized terminal management hub for:
1. Auto-discovering & backfilling MAC addresses and IPs for all already-active accounts & sessions.
2. Enforcing time-bound access (`12h`, `24h`, `7d`, `30d`, etc.) on all granted emails, with
   one-click approval, window extension, and immediate expiration.
3. Blocking / unblocking unauthorized IP addresses, CIDR subnets, Layer-2 MAC addresses, and Device IDs.
4. Controlling Strict Email Pre-Approval mode and multi-device account sharing limits
   (Team Leader Desktop + Field Photo Fitter Phone + QR Pairing).
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Static, Switch

from core.models import AuthorizedDevice, EmailAccessRequest
from core.services import device_access_service


class AccessControlPane(Vertical):
    """Full interactive TUI Access, MAC/IP Firewall & Time-Bound Email Manager."""

    DEFAULT_CSS = """
    AccessControlPane {
        height: 1fr;
        padding: 0 1;
    }
    #access-top-strip {
        height: auto;
        min-height: 1;
        background: #0f172a;
        border-bottom: solid #1e293b;
        color: #e2e8f0;
        padding: 0 1;
        margin-bottom: 0;
    }
    #access-feedback-bar {
        height: auto;
        min-height: 1;
        background: #111827;
        color: #93c5fd;
        padding: 0 1;
        margin-bottom: 1;
    }
    #access-scroll {
        height: 1fr;
    }
    .access-section-box {
        height: auto;
        background: #0b0f19;
        border: solid #1e293b;
        padding: 0 1;
        margin-bottom: 1;
    }
    .access-section-title {
        height: 1;
        color: #facc15;
        text-style: bold;
        margin-top: 0;
        margin-bottom: 0;
    }
    .access-controls-row {
        height: 3;
        align-vertical: middle;
        margin-top: 0;
        margin-bottom: 0;
    }
    .access-controls-row Input {
        margin-right: 1;
    }
    .access-controls-row Button {
        margin-right: 1;
    }
    .access-inline-lbl {
        width: auto;
        padding: 1 1 0 0;
        color: #cbd5e1;
    }
    #inp-access-max-devices, #inp-access-default-hours, #inp-access-duration {
        width: 14;
    }
    #inp-access-email, #inp-access-firewall-target {
        width: 1fr;
        min-width: 24;
    }
    #table-tui-access-requests {
        height: 10;
        min-height: 7;
        border: solid #1e293b;
        margin-bottom: 0;
    }
    #table-tui-access-devices {
        height: 13;
        min-height: 9;
        border: solid #1e293b;
        margin-bottom: 0;
    }
    """

    BINDINGS = [
        ("r", "refresh_access", "Refresh & Sync MACs"),
    ]

    def compose(self) -> ComposeResult:
        sec_cfg = device_access_service.get_security_access_config()
        strict_on = bool(sec_cfg.get("require_email_approval", False))
        max_devs = str(sec_cfg.get("max_devices_per_account", 4))
        def_hours = f"{sec_cfg.get('default_duration_hours', 24)}h"

        yield Static(
            "[bold yellow]🛡️ TUI Access, MAC/IP Firewall & Time-Bound Email Console[/bold yellow]",
            id="access-top-strip",
        )
        yield Static(
            "[dim]Active accounts & MAC addresses are auto-synced. Press [Esc] to exit inputs, [R] to refresh.[/dim]",
            id="access-feedback-bar",
        )

        with VerticalScroll(id="access-scroll"):
            # 1. Policy & Quick Grant / Firewall Controls
            with Vertical(classes="access-section-box"):
                yield Static("⚙️ Global Access Policy, Time-Bound Invites & Network Firewall", classes="access-section-title")

                with Horizontal(classes="access-controls-row"):
                    yield Static("[b]Strict Pre-Approval:[/b]", classes="access-inline-lbl")
                    yield Switch(value=strict_on, id="sw-access-strict")
                    yield Static(" [b]Shared Devices/Acct:[/b]", classes="access-inline-lbl")
                    yield Input(value=max_devs, placeholder="4", id="inp-access-max-devices")
                    yield Static("[b]Default Window:[/b]", classes="access-inline-lbl")
                    yield Input(value=def_hours, placeholder="24h / 7d", id="inp-access-default-hours")
                    yield Button("💾 Save Policy", id="btn-access-save-policy", variant="primary")
                    yield Button("🔄 Sync Active & MACs", id="btn-dev-sync", variant="default")

                with Horizontal(classes="access-controls-row"):
                    yield Static("[b]Time-Bound Email:[/b]", classes="access-inline-lbl")
                    yield Input(placeholder="operator@info.kirex.online", id="inp-access-email")
                    yield Static("[b]Duration:[/b]", classes="access-inline-lbl")
                    yield Input(value=def_hours, placeholder="12h / 24h / 7d", id="inp-access-duration")
                    yield Button("✉️ Invite / Grant", id="btn-access-invite", variant="warning")

                with Horizontal(classes="access-controls-row"):
                    yield Static("[b]Firewall Target:[/b] ", classes="access-inline-lbl")
                    yield Input(placeholder="IP (192.168.1.50), Subnet (10.0.0.0/24), MAC (AA:BB:..), or Device ID", id="inp-access-firewall-target")
                    yield Button("🚫 Block Target", id="btn-access-block-target", variant="error")
                    yield Button("🔓 Unblock Target", id="btn-access-unblock-target", variant="success")

            # 2. Time-Bound Email Grants & Pending Requests Table
            with Vertical(classes="access-section-box"):
                yield Static("📩 Time-Bound Email Grants & Activation Requests (Uses Duration Above)", classes="access-section-title")
                yield DataTable(id="table-tui-access-requests")
                with Horizontal(classes="access-controls-row"):
                    yield Button("✅ Approve Selected", id="btn-req-approve", variant="success")
                    yield Button("⏳ Extend Window", id="btn-req-extend", variant="primary")
                    yield Button("⛔ Expire Now", id="btn-req-expire", variant="warning")
                    yield Button("✕ Reject", id="btn-req-reject", variant="error")

            # 3. Registered & Monitored Devices Table (Auto-populated Active Accounts + MAC/IP)
            with Vertical(classes="access-section-box"):
                yield Static("📱 Active Accounts, Shared Companion Phones & Firewall Rules", classes="access-section-title")
                yield DataTable(id="table-tui-access-devices")
                with Horizontal(classes="access-controls-row"):
                    yield Button("🚫 Block Device", id="btn-dev-block", variant="error")
                    yield Button("🚫 Block MAC", id="btn-dev-block-mac", variant="error")
                    yield Button("🚫 Block IP", id="btn-dev-block-ip", variant="warning")
                    yield Button("🔓 Unblock / Approve", id="btn-dev-unblock", variant="success")
                    yield Button("⏳ Extend Window", id="btn-dev-extend", variant="primary")
                    yield Button("⛔ Expire Device", id="btn-dev-expire", variant="default")

    def on_mount(self) -> None:
        req_tbl = self.query_one("#table-tui-access-requests", DataTable)
        req_tbl.cursor_type = "row"
        req_tbl.add_columns("ID", "Email", "Name", "Status", "Code", "Window", "Time Left", "IP", "MAC")

        dev_tbl = self.query_one("#table-tui-access-devices", DataTable)
        dev_tbl.cursor_type = "row"
        dev_tbl.add_columns("Status", "Account / Email", "Device", "IP Address", "MAC Address", "Linked Via", "Time Left", "Device ID")

        self.refresh_access_data()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and isinstance(self.app.focused, Input):
            self.app.set_focus(None)
            event.prevent_default()
            event.stop()

    def action_refresh_access(self) -> None:
        self.refresh_access_data(notify_user=True)

    def _set_feedback(self, markup_msg: str) -> None:
        try:
            self.query_one("#access-feedback-bar", Static).update(markup_msg)
        except Exception:
            pass

    def _get_admin_user(self):
        return getattr(self.app, "current_user", None)

    def _get_duration_input(self) -> str:
        try:
            val = self.query_one("#inp-access-duration", Input).value.strip()
            return val or "24h"
        except Exception:
            return "24h"

    def refresh_access_data(self, notify_user: bool = False) -> None:
        """Auto-onboards active accounts, backfills MAC/IPs, and reloads both TUI tables."""
        touched = device_access_service.sync_existing_active_accounts_and_devices()
        sec_cfg = device_access_service.get_security_access_config()
        host_ip = device_access_service.resolve_local_host_ip()
        host_mac = device_access_service.resolve_local_host_mac_address() or "Auto"

        try:
            self.query_one("#sw-access-strict", Switch).value = bool(sec_cfg.get("require_email_approval", False))
        except Exception:
            pass

        # Reload Email Requests / Time-Bound Grants
        req_tbl = self.query_one("#table-tui-access-requests", DataTable)
        req_tbl.clear()
        requests_qs = list(EmailAccessRequest.objects.order_by("-updated_at")[:150])
        for r in requests_qs:
            status_disp = r.status
            if r.status in ("APPROVED", "PRE_APPROVED"):
                status_disp = f"[green]{r.status}[/green]"
            elif r.status == "PENDING":
                status_disp = f"[yellow]{r.status}[/yellow]"
            else:
                status_disp = f"[red]{r.status}[/red]"

            countdown = device_access_service.format_expiry_countdown(r.expires_at)
            cd_disp = f"[red]{countdown}[/red]" if countdown == "EXPIRED" else f"[cyan]{countdown}[/cyan]"
            req_tbl.add_row(
                str(r.id),
                r.email,
                (r.full_name or r.username or "-")[:18],
                status_disp,
                r.activation_code or "-",
                f"{r.duration_hours}h",
                cd_disp,
                r.client_ip or "-",
                r.mac_address or "Auto",
                key=str(r.id),
            )

        # Reload Authorized & Blocked Devices
        dev_tbl = self.query_one("#table-tui-access-devices", DataTable)
        dev_tbl.clear()
        devices_qs = list(AuthorizedDevice.objects.select_related("user").order_by("-last_seen_at")[:250])
        for d in devices_qs:
            if d.status == "APPROVED":
                st = "[green]APPROVED[/green]"
            elif d.status == "PENDING":
                st = "[yellow]PENDING[/yellow]"
            elif d.status == "EXPIRED":
                st = "[magenta]EXPIRED[/magenta]"
            else:
                st = f"[bold red]{d.status}[/bold red]"

            acct = d.user.username if d.user else "-"
            email_str = d.authorized_email or (d.user.email if d.user else "")
            acct_col = f"{acct} ({email_str})" if email_str and acct != "-" else (email_str or acct)
            countdown = device_access_service.format_expiry_countdown(d.expires_at)
            cd_disp = f"[red]{countdown}[/red]" if countdown == "EXPIRED" else countdown

            dev_tbl.add_row(
                st,
                acct_col[:30],
                (d.device_name or d.device_type)[:24],
                d.ip_address or d.pinned_ip_cidr or "-",
                d.mac_address or "Auto/LAN",
                d.linked_via,
                cd_disp,
                d.device_id[:22],
                key=d.device_id,
            )

        approved_cnt = sum(1 for d in devices_qs if d.status == "APPROVED")
        blocked_cnt = sum(1 for d in devices_qs if d.status in ("BLOCKED", "REVOKED"))
        pending_cnt = sum(1 for r in requests_qs if r.status == "PENDING")
        mode_lbl = "[bold yellow]STRICT[/bold yellow]" if sec_cfg.get("require_email_approval") else "[bold green]STANDARD[/bold green]"

        try:
            self.query_one("#access-top-strip", Static).update(
                f"[bold yellow]🛡️ ACCESS & FIREWALL[/bold yellow] │ Mode: {mode_lbl} │ "
                f"Host: [cyan]{host_ip}[/cyan] ([yellow]{host_mac}[/yellow]) │ "
                f"Approved: [green]{approved_cnt}[/green] │ Pending: [yellow]{pending_cnt}[/yellow] │ Blocked: [red]{blocked_cnt}[/red]"
            )
        except Exception:
            pass

        if notify_user:
            msg = f"Refreshed access registry ({touched} active account/MAC record(s) auto-synced)."
            self._set_feedback(f"[green]✓ {msg}[/green]")
            self.app.notify(msg, severity="information")

    def _get_selected_request_id(self) -> Optional[str]:
        tbl = self.query_one("#table-tui-access-requests", DataTable)
        if tbl.cursor_row is None or tbl.row_count == 0:
            return None
        try:
            rk = tbl.coordinate_to_cell_key(tbl.cursor_coordinate).row_key
            return str(rk.value) if rk and rk.value else None
        except Exception:
            return None

    def _get_selected_device(self) -> Optional[AuthorizedDevice]:
        tbl = self.query_one("#table-tui-access-devices", DataTable)
        if tbl.cursor_row is None or tbl.row_count == 0:
            return None
        try:
            rk = tbl.coordinate_to_cell_key(tbl.cursor_coordinate).row_key
            dev_id = str(rk.value) if rk and rk.value else ""
            if dev_id:
                return AuthorizedDevice.objects.filter(device_id=dev_id).first()
        except Exception:
            pass
        return None

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "sw-access-strict":
            cfg = device_access_service.update_security_access_config(
                mode="STRICT" if event.value else "STANDARD",
                require_email_approval=bool(event.value),
            )
            self._set_feedback(
                f"[bold green]✓ Email gate set to {cfg['mode']}[/bold green] "
                "(existing active accounts & QR-paired phones remain trusted)."
            )
            self.refresh_access_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        admin = self._get_admin_user()
        dur_raw = self._get_duration_input()

        if bid == "btn-dev-sync":
            self.refresh_access_data(notify_user=True)
            return

        if bid == "btn-access-save-policy":
            strict_val = self.query_one("#sw-access-strict", Switch).value
            max_dev_raw = self.query_one("#inp-access-max-devices", Input).value.strip() or "4"
            def_hr_raw = self.query_one("#inp-access-default-hours", Input).value.strip() or "24h"
            try:
                max_devs = int(max_dev_raw)
            except ValueError:
                max_devs = 4
            cfg = device_access_service.update_security_access_config(
                mode="STRICT" if strict_val else "STANDARD",
                require_email_approval=strict_val,
                max_devices_per_account=max_devs,
                default_duration_hours=def_hr_raw,
            )
            msg = f"Saved policy: Mode={cfg['mode']}, MaxShared={cfg['max_devices_per_account']}, DefaultWindow={cfg['default_duration_hours']}h"
            self._set_feedback(f"[bold green]✓ {msg}[/bold green]")
            self.app.notify(msg, severity="information")
            self.refresh_access_data()
            return

        if bid == "btn-access-invite":
            email_inp = self.query_one("#inp-access-email", Input)
            email = email_inp.value.strip().lower()
            if not email or "@" not in email:
                self.app.notify("Enter a valid operator email address.", severity="warning")
                return
            req = device_access_service.preapprove_operator_email(
                email=email,
                duration_hours=dur_raw,
                admin_user=admin,
                send_invite_email=True,
            )
            email_inp.value = ""
            msg = f"Invited {req.email} for {req.duration_hours}h (Code: {req.activation_code})"
            self._set_feedback(f"[bold green]✓ {msg}[/bold green]")
            self.app.notify(msg, severity="information")
            self.refresh_access_data()
            return

        if bid == "btn-access-block-target":
            t_inp = self.query_one("#inp-access-firewall-target", Input)
            target = t_inp.value.strip()
            ok, msg, _ = device_access_service.block_device_or_network_target(
                target, reason="Blocked via TUI Firewall", admin_user=admin
            )
            if ok:
                t_inp.value = ""
            self._set_feedback(f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]")
            self.app.notify(msg, severity="information" if ok else "error")
            self.refresh_access_data()
            return

        if bid == "btn-access-unblock-target":
            t_inp = self.query_one("#inp-access-firewall-target", Input)
            target = t_inp.value.strip()
            ok, msg = device_access_service.unblock_device_or_target(target, admin_user=admin)
            if ok:
                t_inp.value = ""
            self._set_feedback(f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]")
            self.app.notify(msg, severity="information" if ok else "error")
            self.refresh_access_data()
            return

        # Request Table Actions
        if bid in ("btn-req-approve", "btn-req-extend", "btn-req-expire", "btn-req-reject"):
            req_id = self._get_selected_request_id()
            if not req_id:
                self.app.notify("Select an email row in the requests table first.", severity="warning")
                return
            if bid == "btn-req-approve":
                ok, msg, _ = device_access_service.approve_access_request(
                    req_id, admin_user=admin, duration_hours=dur_raw
                )
            elif bid == "btn-req-extend":
                ok, msg = device_access_service.extend_or_set_expiry(
                    req_id, duration_hours=dur_raw, admin_user=admin
                )
            elif bid == "btn-req-expire":
                ok, msg = device_access_service.expire_immediately(req_id, admin_user=admin)
            else:
                req_obj = EmailAccessRequest.objects.filter(pk=int(req_id)).first()
                if req_obj:
                    req_obj.status = EmailAccessRequest.Status.REJECTED
                    req_obj.save(update_fields=["status", "updated_at"])
                    ok, msg = True, f"Rejected request for {req_obj.email}."
                else:
                    ok, msg = False, "Request not found."
            self._set_feedback(f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]")
            self.app.notify(msg, severity="information" if ok else "error")
            self.refresh_access_data()
            return

        # Device Table Actions
        if bid in ("btn-dev-block", "btn-dev-block-mac", "btn-dev-block-ip", "btn-dev-unblock", "btn-dev-extend", "btn-dev-expire"):
            dev = self._get_selected_device()
            if not dev:
                self.app.notify("Select a device row in the devices table first.", severity="warning")
                return
            if bid == "btn-dev-block":
                ok, msg, _ = device_access_service.block_device_or_network_target(
                    dev.device_id, reason="Device blocked in TUI", admin_user=admin
                )
            elif bid == "btn-dev-block-mac":
                if not dev.mac_address:
                    self.app.notify("Selected device does not have a resolved MAC address.", severity="warning")
                    return
                ok, msg, _ = device_access_service.block_device_or_network_target(
                    dev.mac_address, reason="MAC blocked in TUI", admin_user=admin
                )
            elif bid == "btn-dev-block-ip":
                target_ip = dev.ip_address or dev.pinned_ip_cidr
                if not target_ip:
                    self.app.notify("Selected device does not have an IP address.", severity="warning")
                    return
                ok, msg, _ = device_access_service.block_device_or_network_target(
                    target_ip, reason="IP blocked in TUI", admin_user=admin
                )
            elif bid == "btn-dev-unblock":
                ok, msg = device_access_service.unblock_device_or_target(dev.device_id, admin_user=admin)
            elif bid == "btn-dev-extend":
                target_key = dev.authorized_email or dev.device_id
                ok, msg = device_access_service.extend_or_set_expiry(
                    target_key, duration_hours=dur_raw, admin_user=admin
                )
            else:
                target_key = dev.authorized_email or dev.device_id
                ok, msg = device_access_service.expire_immediately(target_key, admin_user=admin)
            self._set_feedback(f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]")
            self.app.notify(msg, severity="information" if ok else "error")
            self.refresh_access_data()
