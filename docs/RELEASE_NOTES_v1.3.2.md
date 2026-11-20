# Release Notes: CLOSE Copilot v1.3.2: Date first, per-person working day, screens trimmed per role

**Baseline:** v1.3.1.

## Working day
- **One date control, first on every tab:** `◀  [calendar]  ▶  Today   Thu 20 Nov 2026`, directly under the menu.
  Every tab (Dashboard, ITMS, Batches, Queue, Stock, Reports, History) follows it. Hotkey **D** opens the calendar.
- **Always one day.** These controls are gone:
  - the header "Shift" picker and the drawer copy
  - Batches *All batches*
  - History *All time*
  - Dashboard *All-time*
  - Reports *All dates* / scope toggle
  - Stock ◀ Today ▶
  - the Settings shift-date card
- **Per person.** Each person's day is kept with their sign-in. Changing it no longer changes the day for everyone
  else, or for the background ITMS sync. Before, it was one setting for the whole server.
- **Fixes:**
  - Reports and Stock now follow the selected day reliably (before, they kept their own date after a change).
  - Dashboard, History and report totals count the selected day, not the calendar day.

## Each role sees its own work
| Role | Tabs | Opens on |
|---|---|---|
| Helper | phone capture & dispatch scan | Mobile |
| Quality Inspector | Dashboard, ITMS orders, Queue, Stock (view), History, Settings → My account | Queue |
| Team Leader | everything | Dashboard |
| Supervisor | Dashboard, Queue (view), Stock (view), Reports, History, Settings → My account | Reports |

- **Buttons follow the role:**
  - edit plate / swap / link / replace photo / delete
  - approve, submit to ITMS
  - run vision
  - ITMS connect and sync
  - stock ledger, movements, reconcile, deliveries
  - dispatch scans
  - exports (CSV, MVR docket, email), invites

  Buttons a role can't use are hidden, including ones in lists drawn later. The server still refuses them as before.
- **View only** appears on Queue and Stock for roles that can look but not change.
- **Settings** for non-Team-Leaders shows only *My account*. *System version & updates* and *Dry run* are only for
  the administrator.

## Icons
- Clean SVG icons from Django's admin icon set (Font Awesome shapes, MIT licence, included in
  `core/static/core/icons/`). They replace emoji for: edit, delete, view, approve (yes), reject (no), alert,
  search, calendar, clock and add. They take the text colour.

## Device page
- First and last name are **optional**; only the email is needed for the access code. Sign-up still asks for the
  names and pre-fills them if they were given.
