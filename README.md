# 🔒 Secure Vault System

A Flask web app for storing personal documents and notes securely. Files are organized into folders (Identity, Finance, Health, Legal, Property, Misc), notes are AES-encrypted before hitting the database, and the UI supports dark / light mode.

> **Status legend**
> **Implemented** — in the code today.
> **Planned** — designed and specced below, **not yet in the codebase**. Treat those sections as the build plan, not as a description of current behaviour.

## Features

### Implemented

- **Authentication** — register, log in (email or username), log out, password hashing with Werkzeug
- **Password recovery** — expiring (1 hour) reset links sent via SMTP / HTTP email API
- **Change password** — update credentials while logged in
- **Document vault** — upload multiple files (up to 50 MB per request), organized by folder, with search, download, single + batch delete
- **Encrypted notepad** — notes encrypted with Fernet (AES) before storage, decrypted on read; download as `.txt`, single + batch delete
- **Profile management** — name, phone, date of birth, gender, location, avatar upload
- **Security logging** — login attempts (success/failed) recorded with IP + user agent
- **Dark / light mode** — toggle synced across all pages via `localStorage`

### Planned

- **In-browser document preview** — view PDFs and images without downloading
- **Trash with 30-day retention** — deleted documents are recoverable for 30 days
- **Expiring, protected share links** — send a document to someone via a revocable link instead of emailing the file
- **Account deletion** — permanently erase the account and all of its data
- **Email verification at registration** — confirm the address before the vault unlocks
- **Emergency Lockdown** — one click kills every session, link and download

## Tech Stack

| Layer    | Technology |
|----------|------------|
| Backend  | Flask 3, Flask-Login, Flask-SQLAlchemy, Waitress (prod server) |
| Database | SQLite (local) / PostgreSQL (prod via `DATABASE_URL`) |
| Security | Werkzeug password hashing, Fernet symmetric encryption, itsdangerous timed reset tokens |
| Frontend | Jinja2 templates, vanilla CSS/JS, Inter font |
| Email    | SMTP (Gmail-compatible) with TLS/SSL fallback, or Resend / Brevo HTTP APIs |

## Project Structure

```
Secure-Vault-System/
├── app.py               # Flask app factory, all routes, startup (port 5555)
├── models.py            # User, Document, Note, SecurityLog models
├── encryption.py        # Fernet encrypt_note / decrypt_note helpers
├── email_service.py     # password-reset email sender (Resend / Brevo / SMTP)
├── config.py            # Config class (SECRET_KEY, DB, mail settings)
├── requirements.txt
├── Procfile             # production process file
├── .env.example         # environment variable template
├── security.md          # security hardening guide
├── templates/           # Jinja2 pages
│   ├── index.html       # landing page (/)
│   ├── login.html       # login page (/login)
│   ├── register.html    # registration page (/register)
│   ├── forgot_password.html # reset request (/forgot-password)
│   ├── reset_password.html  # set new password (/reset-password/<token>)
│   ├── change_password.html # change password (/change-password)
│   ├── dashboard.html       # main vault UI (/dashboard)
│   └── base.html            # legacy base layout
├── static/
│   └── js/
│       └── alert.js     # demo JS (not required by app)
├── utils/
│   └── time.py          # lockout prototype (logic ported into app.py)
└── uploads/             # uploaded files + avatars (created at runtime)
```

## Run Locally

Requirements: Python 3.10+.

```powershell
cd "Secure-Vault-System"

# 1. Virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1
# If activation is blocked:
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. Install dependencies
pip install -r requirements.txt

# 3. Environment (optional but recommended)
Copy-Item .env.example .env
# Edit .env and set SECRET_KEY, ENCRYPTION_KEY, mail settings (see below)

# 4. Start the app
python app.py
```

Open **http://127.0.0.1:5555** (a Wi-Fi URL is also printed in the console).

> The SQLite database (`app.db`) and `uploads/` folder are created automatically on first run. No migrations needed — `_sync_schema()` adds any new nullable columns to an existing `app.db`.

Custom port:

```powershell
$env:PORT=5000; python app.py
```

## Environment Variables

| Variable         | Required | Description |
|------------------|----------|-------------|
| `SECRET_KEY`     | Yes (prod) | Flask session secret + fallback seed for encryption key |
| `ENCRYPTION_KEY` | Recommended | Fernet key for note encryption. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. If unset, one is derived from `SECRET_KEY` |
| `DATABASE_URL`   | No | Postgres URL for production (SQLite used otherwise). `postgres://` is auto-converted to `postgresql://` |
| `PORT`           | No | Server port (default `5555`) |
| `APP_URL` / `BASE_URL` | No | Public base URL used to build password-reset and share links |
| `MAIL_SERVER`    | No | SMTP host (default `smtp.gmail.com`) |
| `MAIL_PORT`      | No | SMTP port (default `587`, falls back to `465`) |
| `MAIL_USERNAME`  | For reset emails | SMTP login (Gmail: use an App Password) |
| `MAIL_PASSWORD`  | For reset emails | SMTP password |
| `MAIL_DEFAULT_SENDER` | No | From-address (defaults to `MAIL_USERNAME`) |
| `RESEND_API_KEY` | No | Preferred on Render — sends mail over HTTPS 443 instead of SMTP |
| `BREVO_API_KEY`  | No | Alternative HTTPS mail provider |

Planned additions: `TRASH_RETENTION_DAYS` (default `30`), `SHARE_BASE_URL` (defaults to `APP_URL`).

## Routes

### Implemented

| Route | Methods | Auth | Description |
|-------|---------|------|-------------|
| `/` | GET | — | Landing page (redirects to dashboard if logged in) |
| `/register` | GET, POST | — | Create account |
| `/login` | GET, POST | — | Log in with email or username |
| `/logout` | GET | ✅ | Log out |
| `/forgot-password` | GET, POST | — | Request reset link |
| `/reset-password/<token>` | GET, POST | — | Set new password (token valid 1 h) |
| `/change-password` | GET, POST | ✅ | Change password |
| `/dashboard` | GET | ✅ | Vault UI (documents + notes + profile) |
| `/documents/upload` | POST | ✅ | Upload files to a folder |
| `/documents/<id>/download` | GET | ✅ | Download own file |
| `/documents/delete` | POST | ✅ | Delete own files (JSON `ids` or form) |
| `/notes/add` | POST | ✅ | Save encrypted note |
| `/notes/delete` | POST | ✅ | Delete own notes |
| `/folders/remove` | POST | ✅ | Remove a folder and its files |
| `/folders/restore` | POST | ✅ | Un-hide a built-in folder |
| `/profile/update` | POST | ✅ | Update profile + avatar |
| `/uploads/<filename>` | GET | ✅ | Serve uploaded file |
| `/health/db` | GET | — | DB health check (`{"db": "ok"}`) |

### Planned

| Route | Methods | Auth | Description |
|-------|---------|------|-------------|
| `/documents/<id>/preview` | GET | ✅ | Inline render of PDF / image (owner only) |
| `/trash` | GET | ✅ | List trashed documents with days remaining |
| `/trash/restore` | POST | ✅ | Restore selected documents |
| `/trash/purge` | POST | ✅ | Permanently delete selected documents now |
| `/documents/<id>/share` | POST | ✅ | Create a protected share link |
| `/shares` | GET | ✅ | List active links + access history |
| `/shares/<id>/revoke` | POST | ✅ | Immediate revocation |
| `/s/<token>` | GET, POST | — | Public share landing page (password / expiry gate) |
| `/s/<token>/download` | POST | — | Consume one download |
| `/account/delete` | POST | ✅ | Permanently delete account and data |
| `/verify-email/<token>` | GET | — | Confirm an address |
| `/verify-email/resend` | POST | ✅ | Resend the verification mail |
| `/emergency-lockdown` | POST | ✅ | Trigger Emergency Lockdown |
| `/emergency-lockdown/unlock` | POST | ✅ | Re-authenticate and restore normal service |

## Data Model

### Implemented

- **User** — username, email, password hash, profile fields (name, phone, DOB, gender, location, photo), hidden folders
- **Document** — owner, stored + original filename, path, size, MIME type, folder, timestamps
- **Note** — owner, title, **encrypted** content, timestamps
- **SecurityLog** — user, login ID, IP, timestamp, SUCCESS/FAILED status, user agent

### Planned additions

```
User
  + email_verified          BOOLEAN default false
  + session_version         INT default 0      # bump to kill all sessions
  + downloads_blocked_until DATETIME nullable  # lockdown gate
  + locked_down_at          DATETIME nullable

Document
  + deleted_at              DATETIME nullable  # NULL = live, set = in trash
  + encrypted_dek           TEXT nullable      # wrapped per-document key
  + sha256                  TEXT nullable      # integrity check

ShareLink
  id, document_id, owner_id
  + token_hash              TEXT UNIQUE NOT NULL   # SHA-256 of the raw token
  + created_at, expires_at  DATETIME
  + password_hash           TEXT nullable          # optional gate
  + max_downloads           INT nullable           # NULL = unlimited
  + download_count          INT default 0
  + one_time                BOOLEAN default false  # sugar for max_downloads = 1
  + revoked_at              DATETIME nullable
  + recipient_public_key    TEXT nullable          # advanced key-wrapping mode
  + wrapped_dek             TEXT nullable

ShareAccess
  id, share_id, timestamp, ip_address, user_agent, outcome  # granted / denied / expired / revoked

EmailVerification
  user_id, token_hash, expires_at, used_at

AccountDeletionRequest (optional audit trail)
  user_id, requested_at, completed_at
```

---

## Planned Feature Specs

Everything below is **not implemented yet**. It is written so the work can be picked up in slices.

### 1. In-browser preview for PDFs and images

Today every document must be downloaded to be seen.

- `GET /documents/<id>/preview` renders inline via `send_file(..., as_attachment=False)` with an explicit, **server-decided** MIME type (never the browser-supplied `file.content_type`).
- Only allow `application/pdf` and `image/png|jpeg|gif|webp`. Everything else keeps its attachment download.
- Headers on the preview response: `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox` so a crafted PDF/HTML-in-image cannot execute in the vault origin.
- Owner check is `Document.user_id == current_user.id`, same as download; another user's id must return **404**, not 403.
- Dashboard shows a thumbnail tile for images and an "Open"/"Preview" button that opens a modal `<iframe>` or `<img>` pointing at the preview route.
- PDF viewing uses the browser's native viewer — no third-party PDF.js dependency.
- Optional later: `GET /documents/<id>/thumbnail` generating cached 320 px thumbnails with Pillow.

### 2. Trash with 30-day retention

Deleting currently destroys the row **and** the file immediately, and removing a folder wipes every file in it with one click.

- Deleting sets `Document.deleted_at = now()` instead of deleting the row or the file.
- Live queries add `Document.deleted_at.is_(None)`: dashboard, folder counts, search, download, preview.
- `/trash` lists trashed items with "deleted N days ago / M days left".
- Restore clears `deleted_at`. Permanent delete ("Delete forever") removes the row and unlinks the file.
- Purge job removes documents whose `deleted_at` is older than `TRASH_RETENTION_DAYS` (default 30). Run it:
  - on app start (cheap `DELETE ... WHERE deleted_at < cutoff`), and
  - on a schedule (APScheduler / host cron / `flask purge-trash` CLI command) so long-running servers stay clean.
- `GET /documents/<id>/download` and the share endpoints refuse trashed rows, so a link to a trashed doc dies immediately.
- Emptying the trash is explicit; nothing is destroyed on a timer without the user being able to see it coming.

### 3. Email verification at registration

- `User.email_verified` defaults to `false`; registration issues a hashed, single-use, 24-hour token and mails `/verify-email/<token>`.
- Until verified: the dashboard shows a "Verify your email" banner and upload/share/download are blocked (login still works so the user can resend).
- Verification marks the row, clears the token and logs a `SecurityLog` entry.
- Resend is rate limited (e.g. 3 per hour per account).
- Existing accounts created before this feature get `email_verified = true` in the migration so nobody is locked out.

### 4. Expiring, protected document sharing

**The use case.** You need to send a financial document to somebody. Instead of downloading it and attaching it to a chat app, the vault generates a protected access link. The recipient opens the link, passes whatever gate the owner configured, and downloads the file. The owner can watch and revoke.

Owner-configurable per link:

| Setting | Options |
|---|---|
| Expiration | 10 minutes / 1 hour / 24 hours (custom allowed) |
| Password | optional; stored as a password hash, never plaintext |
| One-time download | on/off |
| Maximum downloads | integer, or unlimited |
| Immediate revocation | one click, effective on the next request |
| Access history | every open and download attempt, with outcome |

**Token design.**

- Raw token: `secrets.token_urlsafe(32)` (256 bits of entropy) — generated with the `secrets` module, never `random`.
- Only `sha256(token)` is stored. The raw token is shown **once**, at creation time, and is unrecoverable afterwards; a lost link is re-issued, not re-read.
- Lookup is by hash (indexed, unique), so a database leak does not yield usable links.
- Token comparison is by hash equality on an indexed column; denied responses are uniform (no "token exists but expired" vs "no such token" distinction beyond the expiry page).

**Download flow.**

1. `GET /s/<token>` → hash, look up. Reject if not found, `revoked_at` set, or `expires_at` passed.
2. If the owner is in lockdown or the document is trashed → deny.
3. If a password is set, render a password form; on POST verify with `check_password_hash` and record the outcome in `ShareAccess`.
4. `POST /s/<token>/download` consumes a download. The counter is incremented **atomically** so a one-time link cannot be raced:
   ```sql
   UPDATE share_link
      SET download_count = download_count + 1
    WHERE id = :id
      AND revoked_at IS NULL
      AND (max_downloads IS NULL OR download_count < max_downloads)
   ```
   Zero rows updated means the link is exhausted → deny. This is what makes "one-time download" actually one-time under concurrency.
5. Stream the file as an attachment. Log the access row (IP, user agent, timestamp, outcome).

**Responses** are `Cache-Control: no-store` and `Referrer-Policy: no-referrer`, and the share page never echoes whether the underlying document exists to an unauthenticated visitor.

**Access history** is shown to the owner on `/shares`: created, opened, password-failed, downloaded, expired, revoked — each with timestamp and a masked IP.

**Advanced: wrapped keys for a named recipient.** In the stronger model the file is not encrypted under a vault-wide key:

- Each document gets its own random Data Encryption Key (DEK); bytes are encrypted with AES-GCM under it.
- The DEK is stored wrapped under the owner's key material.
- For a link, the DEK is wrapped **for the intended recipient** (X25519 `SealedBox`, or a key derived from the link password with scrypt/HKDF) and stored in `ShareLink.wrapped_dek`.
- At download time the server unwraps the DEK only for the duration of the request, or hands the recipient a ciphertext they unwrap client-side — so a server compromise alone does not expose the plaintext.
- Trade-off to state plainly: password-derived wrapping means a forgotten link password makes that delivery unrecoverable. That is a feature for a financial document, not a bug.

### 5. Permanent account deletion

- `POST /account/delete` requires the current password **and** a typed confirmation phrase; it is a POST, CSRF-protected, and re-asks for the password even though the user is logged in.
- Order of operations (fail safe, not fail open): revoke all share links → delete documents and unlink their files from `uploads/` → delete notes → delete profile avatar → delete the user row (existing `cascade='all, delete-orphan'` covers notes and documents at the DB level, but files on disk need explicit unlinking).
- `SecurityLog` rows are handled explicitly: either deleted with the account, or retained with `user_id` set to `NULL` and the login id masked, if you want an investigation trail. Decide once and document it — this README assumes deletion removes them with the account unless `SECURITY_LOG_RETENTION` says otherwise.
- The session is cleared and the user is redirected to the landing page with a confirmation.
- Optional graceful path: a 7-day "pending deletion" state with a cancel link, since this is irreversible. If added, the purge job does the final removal.

### 6. Emergency Lockdown

One click on the dashboard's **Emergency Lockdown** button must do all six things, atomically enough that a partial failure is visible rather than silent:

1. **Revoke all active sessions** — bump `User.session_version` so every existing cookie and `get_id()` value stops authenticating, and `session.clear()` on the clicking device.
2. **Disable existing sharing links** — set `revoked_at = now()` on every non-revoked `ShareLink` owned by the user, so no link survives.
3. **Temporarily block downloads** — set `User.downloads_blocked_until`; while set, download, preview and share routes return `423 Locked` (and still write a `SecurityLog` entry so attempts are visible).
4. **Require fresh authentication** — the session carries a `reauth_at` marker; sensitive routes (download, share, profile, delete) demand a password re-entry that is at most a few minutes old. Unlock clears the marker.
5. **Notify the account owner** — email via the existing `email_service` path: what was locked, when, from which IP/user agent, and how to unlock. Reuse `send_password_reset_email`'s provider chain with a new template.
6. **Preserve the security logs** — lockdown must never delete `SecurityLog` rows. It appends a `LOCKDOWN` event and leaves the rest of the trail intact for investigation.

Unlock (`POST /emergency-lockdown/unlock`) requires the account password, clears `downloads_blocked_until` and `locked_down_at`, and re-issues share links rather than reviving revoked ones. Lockdown is never reversible by a single GET, never by a token in a URL, and never by the same session that triggered it without a fresh password.

**Implementation note.** Do the database writes in one transaction, then send the email after commit — an email failure must not roll back the lock. Log and surface any error instead of swallowing it.

---

## Security Notes

Implemented today:

- Passwords are never stored in plain text (Werkzeug scrypt/PBKDF2 hashing).
- Note contents are Fernet-encrypted; without the `ENCRYPTION_KEY`/`SECRET_KEY` they cannot be decrypted.
- Reset tokens are signed and expire after 1 hour.
- Document downloads and deletes are owner-scoped (`...filter_by(id=..., user_id=current_user.id)`).
- Login attempts are rate limited per IP + identifier with exponential lockout.
- For production, always set a strong `SECRET_KEY` + `ENCRYPTION_KEY` and serve over HTTPS.

Invariants the planned features must preserve:

- Share tokens are 256-bit `secrets` values; only their SHA-256 hash is persisted.
- Share passwords are hashed, never stored or logged in plaintext.
- Download counting is atomic, so one-time and max-count links hold under concurrent requests.
- Every share/preview/trash route keeps the `user_id == current_user.id` ownership filter and returns **404** for other users' resources.
- Trashed documents are excluded from download, preview and sharing.
- Emergency Lockdown never deletes `SecurityLog` rows.
- Account deletion unlinks files on disk as well as rows in the database.

See [`security.md`](security.md) for the full hardening guide (CSRF, rate limiting, headers, CSP, upload validation, key rotation).

## Known Gaps

Ordered by risk, so the next slice is obvious:

1. No CSRF protection on POST routes (`Flask-WTF` is not installed).
2. `GET /uploads/<filename>` serves any file to any logged-in user — it is not scoped to the owner.
3. Uploaded documents are stored unencrypted; only notes use Fernet.
4. Upload MIME type comes from the browser (`file.content_type`) with no extension allowlist or magic-byte check.
5. The login lockout tracker is in-memory, so it resets across workers and restarts (`gunicorn --workers 2`).
6. Reset tokens are reusable for their full hour and sessions are not invalidated on password change.
7. `config.py` is dead code — the app configures itself inline in `create_app()`.

## Production

`Procfile` is included for hosts like Render/Heroku. Set `DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, and mail variables in the host's environment settings, then deploy with `pip install -r requirements.txt` as the build command.

Before the planned features ship, add a degrade path for each: a `TRASH_RETENTION_DAYS` default, a scheduled purge command in the host's cron/scheduler, and a `SECRET_KEY` that is stable across deploys (rotating it invalidates every signed share and reset token).
