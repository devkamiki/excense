# excense

Microsoft 365 → CalDAV/CardDAV. A Graph-backed gateway that exposes your
Exchange Online **calendar**, **contacts** and **tasks** over the open
CalDAV / CardDAV protocols, so any standards-based DAV client can read and
write them — Thunderbird, GNOME, KDE, Apple Calendar & Contacts, Android
via DAVx5, or anything else that speaks CalDAV/CardDAV. No Outlook, no
Gmail, no Exchange ActiveSync client ID.

```
┌──────────────────────┐── CalDAV/CardDAV ──┌────────────────────────┐── Microsoft Graph ──┌──────────────┐
│ any CalDAV/CardDAV   │◄──────────────────►│ excense: Radicale DAV  │◄───────────────────►│ Exchange     │
│ client: Thunderbird, │      (HTTPS)       │ store + Graph bridge   │     (OAuth, your    │ Online       │
│ GNOME, KDE, Apple,   │                    └────────────────────────┘     own client ID)  └──────────────┘
│ Android (DAVx5), …   │
└──────────────────────┘
```

## Why not Exchange ActiveSync?

- Basic auth on Exchange Online is fully retired — EAS/POP/IMAP/EWS are
  OAuth-only now, and EAS OAuth requires an approved client ID per app.
- Reusing another product's client ID (e.g. TB-Sync's) fails Entra's
  `redirect_uri` validation, still needs admin consent in business tenants,
  and breaks their ToS.
- EWS (what most bridge projects used) is being **disabled October 2026 →
  April 2027**. So this project is built on **Microsoft Graph**, the only
  forward-looking API.

## What works (v0.1)

- OAuth sign-in: Entra device-code flow, tokens persisted and refreshed.
- Two-way sync of all calendar events, all contacts, and tasks from all
  your Todo lists into a Radicale store, and back.
- CalDAV/CardDAV server (Radicale) with per-user basic auth, ready for any
  DAV client. Email (IMAP/SMTP gateway) is the next milestone, not
  included yet.

## 1. App registration (Entra) — usually no admin needed

You can normally do this entirely yourself: any user can register an app,
every delegated permission below is user-consentable, and Entra's default
consent policy lets users consent to apps registered in their own tenant.
Admin approval only comes into play if the tenant has disabled user consent
— you'd see a hard "Need admin approval" screen at the first sign-in.

1. Azure portal → **App registrations** → **New registration**:
   - Name: `excense`
   - Supported account types: *Accounts in this organizational directory*
     (or *personal Microsoft accounts* if you're on an MSA — but you need a
     work/school mailbox for Exchange Online).
2. **Authentication** → *Allow public client flows*: **Yes**.
3. **API permissions** → add these **delegated** Graph permissions:
   `Calendars.ReadWrite`, `Contacts.ReadWrite`, `Tasks.ReadWrite`,
   `User.Read`, plus (already present) `offline_access`. None of these
   require admin consent by default.
4. Copy the **Application (client) ID** into `EXCENSE_CLIENT_ID`.

> **Single-tenant gotcha:** if you chose *Accounts in this organizational
> directory*, also set `EXCENSE_TENANT` to your tenant ID or
> `contoso.onmicrosoft.com` — the `common` default fails with "not
> configured as a multi-tenant application".

If the first sign-in does say **Need admin approval**, your tenant blocks
user consent. Two ways out:

- Ask an admin for the one-click *Grant admin consent* button (Entra →
  app registrations → your app → API permissions → *Grant admin consent
  for <tenant>*). After that, tokens are issued without further prompts.
- Or skip registration and borrow a first-party client ID:
  `EXCENSE_CLIENT_ID=14d82eec-204b-4c2f-b7e8-296a70dab67e` (Microsoft Graph
  PowerShell) with `EXCENSE_TENANT=common`. First-party apps are
  pre-authorized for Graph scopes and typically stay consentable even in
  locked-down tenants. Handy for personal use, but it rides on Microsoft's
  app identity rather than your own registration — treat it as a
  workaround, not a setup.

## 2. Local run (any Linux distro)

Prerequisite: Python 3.11+ with venv/pip. Debian/Ubuntu:
`sudo apt install python3 python3-venv python3-pip`; Fedora, Arch and most
other distros ship venv/pip with the base `python3` package.

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # set EXCENSE_CLIENT_ID (+ EXCENSE_TENANT if single-tenant)
.venv/bin/excense auth          # headless: opens device-code URL
.venv/bin/excense user-add me   # DAV account password
.venv/bin/excense serve         # CalDAV/CardDAV on :5232
.venv/bin/excense sync-loop     # Graph bridge refreshes every 5 min
```

On NixOS, `nix-shell` drops you into a shell with Python and the venv
tooling, then run the same commands unchanged.

## 3. Docker / VPS

```sh
docker compose up -d --build
docker compose exec excense excense auth        # device-code sign-in
docker compose exec excense excense user-add me # or set EXCENSE_PASSWORD on first boot
```

Put it behind TLS (Caddy/Traefik) — DAV clients generally refuse plain
HTTP, DAVx5 included:
```caddy
cal.example.com {
    reverse_proxy excense:5232
}
```

## 4. Connecting clients

Every CalDAV/CardDAV client needs the same three details:

- **Base URL**: `https://cal.example.com/me/` (the per-user folder)
- **Username / password**: the `excense user-add` account
- Discovery then offers a **calendar** collection, a **contacts** address
  book, and one **task list** collection per Microsoft To Do list
  (`todo/<id>`).

Client notes:

- **Thunderbird** (Linux/macOS/Windows): *New Calendar → On the Network →
  CalDAV* with the base URL; the same URL as a *CardDAV* remote address
  book. Task lists appear as VTODO calendars.
- **GNOME / KDE**: add a CalDAV/CardDAV account via GNOME Online Accounts
  or KAccounts — Evolution, GNOME Calendar/Contacts and KOrganizer pick it
  up from there.
- **Apple** (macOS/iOS): Internet Accounts → *Other* → CalDAV / CardDAV.
- **Android**: [DAVx5](https://www.davx5.com/) syncs the account into the
  system; any calendar/contacts/tasks app can then use it (e.g.
  [Tasks.org](https://tasks.org/) for the VTODO lists).

## Sync semantics

Two-way, last-write-wins per item:

- Remote changes are pulled down, unless the local copy has unsynced edits
  (then local wins and is pushed up).
- Local creates/edits/deletes are pushed via Graph (POST/PATCH/DELETE).
- Graph↔local mapping rides in `X-EXCENSE-GRAPHID` + an sqlite state file
  (`data/state/excense.db`).

## Roadmap

- [x] Graph auth + Radicale store + DAV server
- [ ] Graph *delta* queries + webhook (today: full re-list each cycle, with a
      time filter for events)
- [ ] Email: IMAP/SMTP gateway over Graph mail
- [ ] Tasks: creation of new Todo lists from DAV
- [ ] Encrypted token-at-rest, read-only mode, multi-user DAV accounts

## Credits

excense doesn't implement the DAV protocols — it stands on Radicale's
shoulders:

- [Radicale](https://radicale.org) is the CalDAV/CardDAV server doing all
  of the actual protocol work. excense generates its configuration
  (`radicale.conf`: bcrypt htpasswd auth, `owner_only` rights,
  multifilesystem storage), syncs Microsoft Graph data into that store,
  and launches Radicale as `python -m radicale`. Collections on the client
  side are plain Radicale storage. Radicale is GPL-3.0 software and runs
  here as a separate process — keep its license terms in mind if you
  redistribute the combined bundle.

Also used, with thanks:

- [MSAL Python](https://github.com/AzureAD/microsoft-authentication-library-for-python)
  — Entra device-code flow and token caching.
- [vobject](https://github.com/eventable/vobject) — iCalendar / vCard
  conversion between Graph and DAV.
- [DavMail](https://davmail.sourceforge.io/) and the EAS-bridge projects —
  prior art this design learned from.

## Notes / limitations

- EAS certificate-based auth from third-party clients is being retired by
  Microsoft end of 2026 — this project deliberately never used it.
- All-day event filtering on the date window has edge cases; the filter is
  skipped automatically if Graph rejects it.
- Calendar fetch is bounded by `EXCENSE_CALENDAR_PAST_DAYS` /
  `EXCENSE_CALENDAR_FUTURE_DAYS`.
