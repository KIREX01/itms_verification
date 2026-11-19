# Release Notes: CLOSE Copilot v1.2.1

Patch release on top of v1.2.0.

## Fix: installs and updates follow the release branch
- Before this release, the one-line installers (`install.ps1` / `install.sh`), their update path, and the
  in-app updater's zip fallback all used `main`. `main` is the development branch and can contain
  unfinished work.
- They now use `release/windows` / `release/linux`, which only ever carry finished releases.
- An existing install that tracked `main` is moved onto the release branch on its next install or update.
  Operator data (database, `.env`, media, `secure/`) isn't tracked by git, so it is kept.
- New install commands:

  ```powershell
  irm https://raw.githubusercontent.com/KIREX01/itms_verification/release/windows/install.ps1 | iex
  ```
  ```bash
  curl -fsSL https://raw.githubusercontent.com/KIREX01/itms_verification/release/linux/install.sh | bash
  ```

After installing, activate the install from the central backoffice (*Installs* → enrollment code) with
`itms enroll https://close.kirex.online <CODE>`.
