# Release Notes: CLOSE Copilot v1.2.0: Backoffice & Roles

**Milestone:** v1.2.0
**Baseline:** v1.1.1 (production). Includes the unreleased v1.1.2 security hardening.

---

## Summary

CLOSE Copilot now has a **central backoffice** for the administrator and **per-bond roles** for everyone else.
Access, bans, kill switches and policy are controlled in one place and enforced everywhere: web, mobile,
API and TUI. Enrolled installs receive changes within about a minute.

| Who | Where they work | What they can do |
|---|---|---|
| **Administrator** (owner) | `/backoffice/` only | Grant access, assign roles, suspend / **burn** accounts, kill switches, devices, installs, reports, audit |
| **Team Leader** | Web / TUI | Everything in the Queue (edit, approve, **submit to ITMS**), ITMS connect, stock & ledger, reports, invite Helpers / QIs |
| **Quality Inspector** | Web | Read queue / stock, **flag** a pair, order or kit (with an optional corrected plate). An open flag blocks submission |
| **Helper** | Mobile companion | Photos, U-turn capture, uploads, kit scanning |
| **Supervisor** | Web | Read-only reports, history and stock across their bonds |

Team Leaders, Quality Inspectors and Supervisors need a verified **@itms-ug.com** company email.
Helpers don't. Bond access follows ITMS: a Team Leader can only lead bonds their company email reaches in ITMS.

---

## New

### Backoffice (`/backoffice/`)
- **Overview:** pending requests, open flags, submissions and failures (24h), people missing a company email, switch states.
- **Access requests:** grant (role, bond, company email) / reject / re-send invite.
- **People & roles:** assign and remove roles per bond; attach or revoke the company email; suspend, reinstate,
  sign out everywhere, **burn**.
- **Bonds:** roster per warehouse, open flags, ITMS access.
- **Kill switches & policy:** ITMS master switch, maintenance, dry run, auto-sync, outbox; company domains,
  grace period, invite validity, code attempts, offline grace.
- **Devices** (block/unblock device, MAC, IP, CIDR), **Reports** (cross-bond), **Audit log** (append-only),
  **Installs** (enrolment, health, remote lock, revoke), **Technical**.
- Dangerous actions require re-entering the administrator password. Burn also requires typing the username.

### Accounts
- **Suspend:** immediate, reversible. Every session is killed, and remember-me and QR tokens are invalidated.
- **Burn:** permanent. Everything suspend does, plus every device and ITMS session revoked and all roles removed.
  The login and company emails are banned forever.

### Onboarding: request access → grant → signup
- `/access/request/`: name, any email, role, bond, and the company email for TL/QI/Supervisor. **No account is created.**
- The administrator grants it, and a 6-digit code plus link are emailed (72 h, single use, locked after 5 wrong codes).
- `/signup/?invite=…`: the account is created **active** with its role, bond and company email.
- Team Leaders can invite Helpers and QIs from **Settings → My Team** (the administrator still approves).
- Open self-registration is removed (web and TUI). Only a brand-new empty install can create its first administrator.

### Quality flags
- QI flags a pair, order or kit (reason, note, optional corrected plate). Submission of the pair, or of any pair tied
  to that order or kit, is blocked until the Team Leader applies, rejects or resolves the flag.
- 🚩 badges in the queue, a flag panel in the Action Studio, audit entries.

### Central policy sync
- Installs enroll once: `itms enroll https://close.kirex.online <code>`.
- They check in every 60 s using HMAC-signed, replay-protected messages. Accounts, roles, company emails, burned
  identities and switches are applied locally, and sessions are killed on suspend/burn.
- Remote **Lock now** puts the install into maintenance.
- An install offline for longer than the grace period (default 24 h) keeps working locally but **cannot submit to ITMS**.

### Install control (copies don't run)
- Every laptop / workstation install must be **activated** with the central backoffice before anyone can
  use it. Until then it shows a locked page; only the administrator can activate it, with a one-time code.
  Activation is available from the TUI start screen, `/install/` or `itms enroll`.
- An install is **bound to its computer** (OS machine ID). A working folder copied to another computer
  detects the move, locks itself and erases the copied ITMS portal sessions and sign-ins.
- The install's sync secret is encrypted with a machine-bound key, so a copy can't impersonate it even if
  its files are edited.
- If a copy still reaches the central server, it is reported as a **clone**, the install is revoked
  automatically and the Overview shows an alert.
- **Laptop web access:** only the laptop's own browser and phones paired through its QR code can open its
  pages. Other devices on the Wi-Fi or hotspot are refused before the sign-in page.
  `X-Forwarded-For` spoofing is ignored.
- **No shared default password:** each install generates its own random local administrator password.

### One control centre
- The backoffice exists **only on the central server** (close.kirex.online). Installed apps return 404
  for `/backoffice/`.
- Request access, invite signups and Team Leader invites all happen on the central server.

### Device information
- Browsers and phones record their OS (including Windows 11 vs 10), browser, phone model and Client
  Hints. Once per session they also report screen, time zone, languages, CPU cores, memory, GPU, touch
  and network.
- Installs report hostname, OS edition and build, CPU, RAM, disk, IP addresses, MAC address and install path.
- All of it shows in the backoffice under *Devices* and *Installs*.

### Identity
- ITMS Connect (web and TUI) only accepts the person's own company email. A successful ITMS login attaches and verifies it.
- Each warehouse sync records which bonds the company email can reach. Removed warehouses suspend the matching roles.

### Operator UI
- Tabs, buttons and shortcuts follow the role. Helpers land on the mobile companion.
- Settings → **My Account & Roles** shows roles, company email status and the grace deadline.
- The TUI hides tabs the role can't use, and the ACCESS tab and system/developer cards are administrator-only.

## Fixes
- **Web navigation and the ITMS tab didn't load** (broken in production since 11 Nov). Two scripts
  re-declared shared globals, a browser syntax error. A regression test now guards against it.
- **TLS keys exposed in the now-public repository history** (v1.0.8 `cert.key` and an old embedded key)
  are detected and replaced with freshly generated certificates on start-up.

## Security fixes (from v1.1.2)
Admin-only security, settings and update APIs. The emailed approve link needs an admin login and a POST.
No `?bypass_maintenance=1`. The access guard fails closed. "Extend" never lifts blocks. Approvals never revive
admin-deactivated accounts. Activation codes are throttled. The anonymous ITMS status no longer leaks the operator
identity. Signup no longer stores plaintext passwords or puts codes in URLs. The TUI maintenance save bug is fixed,
as is the TUI renewal crash. Missing `logger` definitions (submission worker, TUI) that turned errors into crashes are fixed.

---

## Upgrade (production server, the central backoffice)

```bash
sudo -u www-data git -C /opt/itms pull origin release/linux
# Mark this server as the central backoffice (systemd unit or /opt/itms/.env):
#   DEPLOYMENT_ROLE=central
sudo -u www-data /opt/itms/.venv/bin/python /opt/itms/manage.py migrate
sudo systemctl restart itms
```

**What happens to existing users:**
- Every active account becomes **Team Leader at its current bond**, so nobody loses access.
- Their company email is attached automatically from their ITMS login when it is `@itms-ug.com`.
- Everyone else gets a **14-day grace period** to attach one (Team Leaders: connect ITMS with the company email).
  The Overview page lists them.
- Then assign the real roles in **Backoffice → People & roles**.

**Laptops / workstations:**
- They lock after the upgrade until activated.
- Before upgrading a laptop, have an enrollment code ready.
- Developers on a source checkout can set `CLOSE_REQUIRE_ENROLLMENT=0` and `CLOSE_LOCAL_ACCESS=open`.
- Pull `release/windows` or `release/linux` and migrate.
- In the backoffice, open **Installs**, create an install, then run `itms enroll <server> <code>` on that machine.

Emergency only: `GOVERNANCE_ENFORCE=0` turns role checks off (legacy behaviour). The Technical page shows when it is off.

## Rolling back
`sudo itms rollback v1.1.1`. Governance tables are additive. Rolling back leaves them unused.

## Known follow-ups
- The TUI and web still hold separate copies of approve / swap / unlink logic. Both are role-gated, and merging
  them into one service is next.
- Six pre-existing test failures (queue ordering, OCR fixture, kit-sync timing) are unchanged from v1.1.1.
- GitHub Actions is blocked by billing, so CI won't run until it is fixed.
