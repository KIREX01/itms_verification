# Release Notes: CLOSE Copilot v1.3.0: Device Admission & Sign-in Security

**Baseline:** v1.2.1 (production).

## Summary
Only devices **you approve** can open CLOSE Copilot. An unknown phone or computer sees only
"This device is not authorized", with no sign-in form, so passwords can't even be tried from it.
Sign-ins are recorded and password guessing is locked out, and the backoffice requires a 2-step sign-in.

## New

### Device admission
- **Every device needs approval.** Unknown devices get a blocked page with a short **device code** and a
  *Request device access* form (name, email, reason). The request carries the device's OS, model,
  browser, screen, time zone, CPU/GPU and IP.
- **Approving.** Requests appear in **Backoffice → Devices → Requests**, and you get an email alert.
  Approve (optionally linking the device to a person) or block. Blocking ends that device's sessions.
- **Device identity.** A device is identified only by its server-signed device key. IP addresses, headers
  and query parameters can't make a device count as approved.
- **Phones paired by QR.** Scanning a laptop's QR files a request on behalf of the person who showed it,
  and the phone waits for your approval. Requests made through a laptop install reach the central queue
  on its next check-in, and your decision is sent back to the laptop.
- **The laptop itself** (an enrolled install) is always admitted.
- **Command line** (server bootstrap / break-glass): `itms device list`, `itms device approve <CODE> [--user NAME]`,
  `itms device block <CODE>`.

### Sign-in security
- **Lockout:** 5 wrong passwords for an account (or 20 from one IP) lock it for 15 minutes. That applies to
  the web, the TUI and the Django admin. Unlock from the person's page in the backoffice.
- **No username hints:** error messages no longer reveal whether a username exists.
- **Sign-in history:** every attempt (who, result, IP, device, channel, browser) appears in **Backoffice → Sign-ins**.
- **Sessions:** each person's page lists their active sessions and lets you end one.

### Roles are Django Groups & Permissions
- Each capability is a Django **permission** (`governance.queue_approve`, `governance.itms_submit`, ...)
  and each role is a Django **group** holding them: *Helper, Quality Inspector, Team Leader, Supervisor*.
  There are also *Platform Administrator* and *Suspended* groups.
- **Backoffice → Roles & permissions** shows the permissions-by-role grid. Tick what a role may do, with
  password confirmation and an audit entry. *Reset to default* restores the original matrix. Changes reach
  laptop installs on their next check-in.
- **Automatic membership:** people join and leave groups automatically when roles are granted or removed,
  or when they are suspended, reinstated or burned. Superusers are in *Platform Administrator*.
- **Bond scoping is unchanged:** the group says *what* a role may do; the person's role membership says
  *where* (which bond). Unrelated Django permissions on these groups are ignored.

### Services: sync, vision, outbox & housekeeping from the backoffice
- **Backoffice → Services** shows the background services on the central server **and every laptop**: ITMS order
  sync, ITMS kit sync, the vision pipeline and models, the offline outbox and the central check-in. For each it shows
  running / paused, last run, last result and errors. Laptops report this on every check-in.
- **Actions:** sync orders now, sync kits now, pause / resume automatic sync, run the vision pipeline, reload vision
  models, drain the outbox, clean old crops and exports, optimise the database, and update to the latest release.
  - **On the server** they run immediately.
  - **On a laptop, or all laptops**, they are sent as **signed commands**. The laptop runs them on its next check-in
    (about a minute) and reports the result back.
  - Only these named actions exist; anything else is refused on both sides. Each needs your password and is audited.
- **Central settings:** set these once and they apply on the server and every laptop:
  - sync on/off and intervals
  - outbox retry interval
  - character reader on/off
  - confidence thresholds
  - PaddleOCR and ensemble toggles
  - which shipped detector and character model to use

  Vision models reload automatically after a change.
- **Alerts:** the Overview warns when a service is failing on any machine.
- **Command line:** `itms services status`, `itms services actions`, `itms services run <ACTION>`.

### Administrator 2-step sign-in
- **Setup.** The first backoffice visit asks you to scan a QR code with an authenticator app. You confirm
  with a code and get 8 single-use recovery codes.
- **Each session.** The 6-digit code is required once per session. Codes can't be replayed.
- **Django admin.** `/admin/` on the central server needs the same 2-step sign-in. It is **switched off on
  laptop installs**.
- **Lost phone:** `itms admin-2fa reset <username>` on the server.

### Fixes
- **Client IP behind nginx.** `X-Forwarded-For` is now trusted only from the server's own proxy
  (`TRUSTED_PROXY_IPS`), so visitors can no longer spoof their IP.
- **Backoffice link on laptops.** `/backoffice/` on a laptop now explains that management happens on the
  central server and links to it, instead of a bare "not found".

## Deploying
1. **Approve your own browser first.** Devices that already signed in before the upgrade keep working.
   Any other device of yours will show a code; approve it on the server with
   `sudo -u www-data /opt/itms/.venv/bin/python /opt/itms/manage.py itms_device approve <CODE> --user close_admin`.
2. **2-step sign-in.** On the first backoffice visit after the upgrade, set it up.
3. **Staff devices.** Ask staff to open the site on each device they use and send a request, then approve
   them under *Devices → Requests*.

Emergency only: `CLOSE_DEVICE_ADMISSION=0` in `/opt/itms/.env` turns device admission off.
