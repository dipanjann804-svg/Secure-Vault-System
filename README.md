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
- **Advanced catalogue** — steganographic vault, canary bait files, Shamir key sharding, air-gapped HTML export, time-capsules, forensic watermarking, shoulder-surfer guard, emergency shredder, plus workstation features. See [Advanced Feature Catalogue](#advanced-feature-catalogue-planned--experimental).

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

---

## Advanced Feature Catalogue (Planned / Experimental)

A wider set of defensive and usability features, catalogued so they can be prioritised deliberately rather than accumulated. **None of this is implemented.** Several items are genuinely valuable, several are speculative, and a few are closer to theatre than security — each section says which, because shipping security features that do not work is worse than shipping none. The [matrix in §3](#3-architecture--implementation-matrix) scores every feature, including the ones already specced above.

### 1. Unconventional Defensive & Cryptographic Features

#### 1.1 Steganographic "Cloaked Vault"

Embeds encrypted notes and documents inside ordinary photo or audio files (LSB steganography) so the container looks like an innocent image or song.

- Carrier must be **lossless** — PNG, BMP, or WAV. JPEG/MP3 re-encode destroys LSB payloads; a "save as JPEG" round-trip silently eats the vault.
- Capacity is roughly 1 bit per colour-channel byte: a 1 MB encrypted payload needs a ~8 MB PNG. Document the limit in the UI instead of failing at export time.
- Payload layout: magic header + version + AES-GCM nonce + ciphertext + GCM tag. Extraction verifies the tag before writing anything to disk.
- The vault byte stream must be padded so payload size does not leak through file size, and the container's EXIF must be preserved (a stripped EXIF is itself a tell).

**Honest rating: low.** LSB payloads are detectable by chi-square and RS analysis, and Fernet output is high-entropy — the *presence* of a payload is not hidden from a forensic analyst, only from a casual observer. Plausible deniability is also defeated by the server's own database rows, uploads folder and access logs; a cloaked vault is only coherent if the app keeps **no** metadata linking the container to the user. That is a redesign, not a feature toggle. Treat this as "hides the file in a photo", not "defeats an adversary".

#### 1.2 Canary "Honey-Files"

Realistic decoy documents that look like the real thing; opening one fires a silent webhook with the visitor's IP and device fingerprint.

- Each canary gets a 128-bit random id (non-enumerable). Beacon route: `GET /canary/<canary_id>.gif` — 1×1 transparent GIF, always `200`, `Cache-Control: no-store`.
- Works for **documents served through the vault** (a PDF/HTML whose remote image or link hits the beacon). It cannot work for a file downloaded once and opened offline.
- Alert payload: canary label, timestamp, IP (via `ProxyFix`, only if behind exactly one trusted proxy), user agent, `Accept-Language`, and an optional coarse fingerprint (canvas/WebGL hash, screen size). Fingerprints are fuzzy — record them as a hint, never as identity.
- Bait documents must be marked in the database (`Document.is_canary`) and excluded from trash purge, share links and downloads for the real owner.
- Rate limit the beacon (per IP), never log the beacon URL anywhere public, and never let the beacon response differ between hit and miss.

**Honest rating: medium — detection only.** It tells you someone opened a document you planted; it does not prevent anything, and false positives (a sandboxed mail scanner fetching remote images) are common. Fingerprinting also carries privacy and legal obligations in some jurisdictions — keep it opt-in per user.

#### 1.3 Shamir's Secret Sharing

Fractures the master recovery key into a (k, n) threshold scheme distributed among trusted parties, removing the single point of key failure.

- Split in GF(2⁸) with `secrets` as the randomness source; encode shards as a wordlist (BIP39-style) so they can be printed on paper without transcription errors.
- Each shard carries a **version, index and a hash commitment**; combining shards from different generations must fail loudly rather than silently reconstructing garbage.
- Reconstruction happens client-side or in a one-off CLI — **not** in a long-running web route, because k shards in process memory is exactly the moment the whole scheme is defeated.
- The server must hold **at most one** shard, and preferably zero. If the app stores all n, Shamir adds nothing.

**Honest rating: high, conditional on operational discipline.** The maths is sound; the failure mode is human — shards emailed to the same provider, or three holders in one office. Pair it with a documented recovery rehearsal.

#### 1.4 Air-Gapped Portable HTML Vault

One-click export producing a single `.html` file containing the ciphertext and a Web Crypto AES engine, openable offline from a cold-storage USB stick with zero dependencies.

- Key derivation in-browser: PBKDF2-SHA-256 (≥ 600k iterations) or, better, Argon2id via WASM. The passphrase is typed on the air-gapped machine and never stored.
- `AES-GCM` with a fresh 96-bit nonce per export; the file embeds `{version, kdf params, salt, nonce, ciphertext}` as a JSON blob the inline script parses.
- Constraints: browsers restrict some APIs on `file://`; Web Crypto needs a secure context (`file://` counts in current browsers, but verify on the target machine); base64 inflates payloads ~33 %, so cap the export or offer chunked archives.
- Integrity: the export includes a SHA-256 of its own payload, and the download page shows that hash so it can be compared out-of-band. A tampered export is the real risk — a malicious HTML vault would happily exfiltrate the passphrase.

**Honest rating: high for the offline/cold-storage use case**, with the caveat that security reduces to passphrase strength and to trusting the one HTML file you carried.

#### 1.5 Cryptographic Time-Capsules

Verifiable Delay Functions (VDF) intended to prevent decryption before a future target date, supposedly immune to administrative or physical coercion.

- Construction: repeated squaring in an RSA group of unknown order, with a Wesolowski or Pietrzak proof so the delay is verifiable without redoing the work.
- A real time-lock puzzle (Rivest–Shamir–Wagner) makes the key *the output*, so nobody can decrypt early without the sequential work — but that requires a trusted modulus-generation ceremony where **no one** retains φ(N). If the ceremony is compromised, the capsule opens instantly.

**Honest rating: speculative, and the "immune to coercion" claim is wrong.** A VDF imposes *sequential work*, not a prohibition: an attacker holding the ciphertext and the wrapped key just decrypts, and parallel hardware shortens wall-clock delay. It also does nothing against coercion, because the ciphertext is sitting right there. Market it as **"delayed decryption"**, never as "cannot be decrypted before date X" or "coercion-proof". If you build it, build it as a puzzle whose key is the VDF output, and say plainly that it is a speed bump.

#### 1.6 Dynamic Forensic Watermarking

Translucent diagonal provenance stamps (`CONFIDENTIAL · user@email · IP · Date`) across previews and downloads, so a leaked copy points back to who received it.

- Raster burn-in for images and PDF pages (Pillow / `pypdf` overlay / Ghostscript), generated **per request** so each recipient's copy differs.
- HTML overlay for in-browser previews only — it is cosmetic and disappears from a screenshot-free copy/paste.
- Vary position, angle and opacity per recipient so a crop or patch job does not remove every marker at once; keep the visible stamp plus a per-recipient id for attribution.
- Never let a watermark generation failure silently fall back to an unwatermarked download.

**Honest rating: medium.** It attributes leaks when the attacker is lazy; a determined one removes or blurs it. Value comes from deterrence and from the audit trail, not from robustness.

#### 1.7 Shoulder-Surfer Privacy Guard

Real-time CSS window blur on tab unfocus or inactivity, hover-to-peek redaction, and optional webcam glance detection.

- `blur` on `window` `blur` / `visibilitychange` / a 60 s inactivity timer; reveal requires a deliberate action (click or hover), not an accidental mouse move.
- Hover-to-peek uses `filter: blur(6px)` with `:hover`/`:focus-visible` removal — pure CSS, no JS state to leak.
- Webcam glance detection (face/gaze heuristics) needs camera permission, is unreliable in poor light, and is invasive in an app whose whole purpose is privacy.

**Honest rating: low — and explicitly not security.** CSS blur is a courtesy against a passer-by; it does not stop screenshots, screen recording, or a camera pointed at the screen. Ship the blur and the inactivity lock (cheap, no downside). Ship webcam detection **off by default**, opt-in, with a visible indicator, or omit it.

#### 1.8 Cryptographic "Emergency Shredder"

Multi-pass `/dev/urandom` key-salt overwriting triggered on repeated failed PINs or a duress code, permanently rendering data unrecoverable.

- The reliable mechanism is **destroying the per-document DEK**, not overwriting file bytes. Once the wrapped key is gone, the ciphertext is noise — instant, and it works on every storage medium.
- Overwrite remaining key material (salt, wrapped DEKs, any cached plaintext) with random bytes and `fsync`; delete the rows; then unlink the files.
- Multi-pass overwriting of *user files* is unreliable on SSDs (wear levelling remaps blocks), on copy-on-write filesystems (btrfs/ZFS), under snapshots, and on any cloud/OneDrive-synced path — which is exactly where this project lives (`OneDrive\...\Secure-Vault-System`). Use full-disk encryption *plus* key destruction; do not promise "military-grade overwrite".
- Duress code must open a **decoy vault that looks completely normal**, or the coercer learns they have been fooled. A duress path that visibly shreds is a duress path that gets you hurt.
- Safeguards: typed confirmation, an explicit "no recovery possible" screen, a cool-off window (e.g. 24 h cancel link by email), and never triggering from an unauthenticated/IP-based heuristic.

**Honest rating: medium via key destruction, low via overwrite.** The security property you can actually defend is "the key is gone"; the rest is folklore.

### 2. Workstation & Usability Enhancements

| # | Feature | What it adds | Honest caveat |
|---|---|---|---|
| 2.1 | **In-browser PDF viewer + signature pad** | No unencrypted file lands on disk just to be read or signed; signature captured on `<canvas>` and stored as an encrypted PNG attachment | A drawn signature is not a legally recognised e-signature without identity binding and an audit trail (signer, time, IP, document hash) — say so in the UI |
| 2.2 | **Encrypted voice memo recorder** | `MediaRecorder` → WebM/Opus, **encrypted in the browser** before upload, so the server only ever stores ciphertext | Client-side encryption means the server cannot transcode or index audio; key management moves to the client |
| 2.3 | **Password & secret credentials manager** | Encrypted entries per site, generator, copy-to-clipboard with auto-clear | Breach auditing uses HaveIBeenPwned's k-anonymity range API — only the **first 5 hex chars** of the SHA-1 leave the browser; never send a full hash or a password |
| 2.4 | **Hierarchical custom folders + colour tags** | `parent_id` + materialized path, recursive counts, tags as a separate table so one document can carry several | Replaces today's "folders are inferred from document rows" hack, where an empty folder vanishes; needs its own migration |
| 2.5 | **Multi-device session manager + kill switch** | A `Session` table (ip, user agent, created, last seen, revoked) with per-device revoke | Kill switch = delete the row **and** bump `session_version`; the second half is what actually invalidates a cookie already in the wild |
| 2.6 | **Progressive Web App (PWA)** | Installable offline shell on desktop and mobile, plus a launcher icon | The service worker must cache **static assets only** — never vault pages or decrypted content, or "offline mode" becomes a plaintext cache on disk |

### 3. Architecture & Implementation Matrix

Every feature from this document, in build order. **Security rating** is the honest value of the control, not its ambition.

| Feature | Defense vector | Security rating | Complexity | Phase |
|---|---|---|---|---|
| CSRF protection on all POSTs | Request forgery | **Critical** (prerequisite) | Low | 0 |
| Owner-scoped download/preview (retire `/uploads/<filename>`) | IDOR / horizontal privilege escalation | **Critical** (prerequisite) | Low | 0 |
| Encrypt documents at rest (per-doc DEK, wrapped) | At-rest disclosure, disk seizure | High | Medium | 0 |
| Upload allowlist + magic-byte check | Malware, polyglot files | High | Low | 0 |
| Security headers, CSP, secure cookies | XSS, clickjacking, MitM | High | Low | 0 |
| In-browser PDF/image preview | Download hygiene, malware exposure | Medium | Low | 1 |
| Trash with 30-day retention | Accidental loss, ransomware recovery | Medium | Low | 1 |
| Email verification at registration | Account takeover, spam accounts | Medium | Low | 1 |
| Permanent account deletion | Data minimisation, GDPR erasure | Medium | Low | 1 |
| Expiring / password / one-time share links | Uncontrolled distribution, oversharing | High | Medium | 2 |
| Share access history + revocation | Leak attribution, incident response | Medium | Low | 2 |
| Multi-device session manager + kill switch | Session hijacking, stolen laptop | High | Medium | 2 |
| Emergency Lockdown (6-action) | Active compromise | High | Medium | 2 |
| Credentials manager + HIBP audit | Password reuse, credential stuffing | High | Medium | 2 |
| Forensic watermarking (per-recipient) | Leak attribution, deterrence | Medium | Medium | 2 |
| Hierarchical folders + colour tags | None direct (usability; prevents misfiling) | N/A | Medium | 2 |
| Encrypted voice memos | At-rest audio disclosure | Medium | Medium | 3 |
| Signature pad | Repudiation (weak) | Low | Low | 3 |
| Shamir sharding of recovery key | Single point of key failure | High (conditional) | Medium | 3 |
| Air-gapped portable HTML vault | Cold storage, offline access | High (passphrase-bound) | Medium | 3 |
| PWA offline shell | Availability, UX | Low | Low | 3 |
| Canary honey-files | Intrusion detection | Medium (detection only) | Medium | 4 |
| Steganographic cloaked vault | Plausible deniability | Low (speculative) | High | 4 |
| Cryptographic time-capsules (VDF) | Delayed decryption | Low (speculative) | Very high | 4 |
| Shoulder-surfer blur / inactivity mask | Shoulder surfing | Low | Low | 4 |
| Webcam glance detection | Shoulder surfing | Low, high privacy cost | High | 4 (opt-in) |
| Emergency shredder | Coercion, physical seizure | Medium (key destruction) / Low (overwrite) | Medium | 4 (guarded) |

**Phasing rules.** Phase 0 is a prerequisite for everything — a share link built on top of an unscoped `/uploads/<filename>` route leaks files by design. Phase 2 features depend on Phase 0's at-rest encryption, because "expiring link to an unencrypted file" protects the link, not the data. Phase 4 items are individually gated behind a written threat model and a documented rollback: nothing in Phase 4 may delete data irreversibly without a tested backup path.

### Planned routes (advanced)

| Route | Methods | Auth | Purpose |
|---|---|---|---|
| `/canary/<canary_id>.gif` | GET | — | Tracking beacon (1×1 GIF, always 200, rate limited) |
| `/canary/create` | POST | ✅ | Generate a decoy document |
| `/vault/export.html` | POST | ✅ | Build the air-gapped HTML vault |
| `/vault/recovery/split` | POST | ✅ | Split the recovery key into (k, n) shards |
| `/vault/shred` | POST | ✅ | Emergency shredder (typed confirmation + cool-off) |
| `/sessions` | GET | ✅ | Multi-device session list |
| `/sessions/<id>/revoke` | POST | ✅ | Per-device kill switch |
| `/voice/upload` | POST | ✅ | Store a client-encrypted voice memo |
| `/credentials` | GET | ✅ | Credentials manager |
| `/credentials/<id>/audit` | POST | ✅ | HIBP k-anonymity breach check |
| `/timecapsule/create` | POST | ✅ | Time-locked entry point (Phase 4) |

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
