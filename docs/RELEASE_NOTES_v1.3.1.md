# Release Notes: CLOSE Copilot v1.3.1: Device access code, sign-up, roles from the backoffice, bonds from ITMS

**Baseline:** v1.3.0.

## Summary
A new phone or computer gets in with a code sent to the person's email. New people create their own account; you
assign their role in the backoffice; their bonds come from the warehouses their ITMS account can see. Every device,
sign-in and sign-up page shows the logo and only a few words.

## The flow
1. **New device:** *"This device isn't authorised to use CLOSE Copilot."* The person enters **first name**,
   **last name** and **email** (checked as they type and on the server), then taps **Send access code**.
2. **6-digit access code** by email (Resend). It lasts 15 minutes and allows 5 tries. Sending is limited per device
   and per network. Burned or suspended people get no code.
3. **Code entered:**
   - **Has an account** → the device is unlocked and linked to them → **Sign in** (username *or* email).
   - **New person** → **Create account**. First name, last name and email are carried over (the email is locked),
     a username is suggested (e.g. `akello.grace`), and they set a password. The password must be 8+ characters,
     not common, not only numbers, and not like their name or email; it is typed twice.
4. **Account created with no role.** You get an email *"New sign-up: name: Assign role"*, and the Overview shows
   **New sign-ups waiting for a role**. Until then the person sees *"Your account is ready. Your administrator will
   assign your role."*

**Laptops** that can't send email fall back to administrator approval (*Devices → Requests*). An approved
access request for the same email is applied automatically at sign-up.

## Roles and bonds
- **Backoffice → People → person → Roles:** switch on Helper / Quality Inspector / Team Leader / Supervisor and save.
  Django groups follow automatically. It needs your password and is audited.
- **Bonds come from ITMS:** when the person connects their ITMS account (company email), the warehouses ITMS
  returns become their bonds for those roles.
  - Updated on every ITMS sync: a warehouse removed in ITMS is removed here.
  - Signing up with an `@itms-ug.com` email attaches it as their company email automatically.
- A Team Leader, QI or Supervisor with a role but **no bonds yet** sees a simple **Connect ITMS** form instead of
  being blocked.
- **Add a bond by hand** for people without ITMS (e.g. Helpers).
- Existing people keep their current roles and bonds.

## Data consistency
- Usernames are lowercase, 3-30 letters or numbers (dot, dash, underscore), and unique regardless of case.
  Sign-in ignores case.
- One account per email; names are tidied ("okello  samuel" → "Okello Samuel").
- Devices that requested access or were unlocked with an email are linked to the account created for it, under the
  person's name.
- The central server no longer pre-fills the previous person's username on the sign-in page.
