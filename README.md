# 🔒 Secure Vault System

A Flask web app for storing personal documents and notes securely. Files are organized into folders (Identity, Finance, Health, Legal, Property, Misc), notes are AES-encrypted before hitting the database, and the UI supports dark / light mode.

## Features

- **Authentication** — register, log in (email or username), log out, password hashing with Werkzeug
- **Password recovery** — expiring (1 hour) reset links sent via SMTP email
- **Change password** — update credentials while logged in
- **Document vault** — upload multiple files (up to 50 MB per request), organized by folder, with search, download, single + batch delete
- **Encrypted notepad** — notes encrypted with Fernet (AES) before storage, decrypted on read; download as `.txt`, single + batch delete
- **Profile management** — name, phone, date of birth, gender, location, avatar upload
- **Security logging** — login attempts (success/failed) recorded with IP + user agent
- **Dark / light mode** — toggle synced across all pages via `localStorage`

## Tech Stack

| Layer    | Technology |
|----------|------------|
| Backend  | Flask 3, Flask-Login, Flask-SQLAlchemy, Waitress (prod server) |
| Database | SQLite (local) / PostgreSQL (prod via `DATABASE_URL`) |
| Security | Werkzeug password hashing, Fernet symmetric encryption, itsdangerous timed reset tokens |
| Frontend | Jinja2 templates, vanilla CSS/JS, Inter font |
| Email    | SMTP (Gmail-compatible) with TLS/SSL fallback |

## Project Structure

```
Secure-Vault-System/
├── app.py               # Flask app factory, all routes, startup (port 5555)
├── models.py            # User, Document, Note, SecurityLog models
├── encryption.py        # Fernet encrypt_note / decrypt_note helpers
├── email_service.py     # SMTP password-reset email sender
├── config.py            # Config class (SECRET_KEY, DB, mail settings)
├── requirements.txt
├── Procfile             # production process file
├── .env.example         # environment variable template
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

> The SQLite database (`app.db`) and `uploads/` folder are created automatically on first run. No migrations needed.

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
| `APP_URL` / `BASE_URL` | No | Public base URL used to build password-reset links |
| `MAIL_SERVER`    | No | SMTP host (default `smtp.gmail.com`) |
| `MAIL_PORT`      | No | SMTP port (default `587`, falls back to `465`) |
| `MAIL_USERNAME`  | For reset emails | SMTP login (Gmail: use an App Password) |
| `MAIL_PASSWORD`  | For reset emails | SMTP password |
| `MAIL_DEFAULT_SENDER` | No | From-address (defaults to `MAIL_USERNAME`) |

## Routes

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
| `/profile/update` | POST | ✅ | Update profile + avatar |
| `/uploads/<filename>` | GET | ✅ | Serve uploaded file |
| `/health/db` | GET | — | DB health check (`{"db": "ok"}`) |

## Data Model

- **User** — username, email, password hash, profile fields (name, phone, DOB, gender, location, photo)
- **Document** — owner, stored + original filename, path, size, MIME type, folder, timestamps
- **Note** — owner, title, **encrypted** content, timestamps
- **SecurityLog** — user, login ID, IP, timestamp, SUCCESS/FAILED status, user agent

## Security Notes

- Passwords are never stored in plain text (Werkzeug scrypt/PBKDF2 hashing).
- Note contents are Fernet-encrypted; without the `ENCRYPTION_KEY`/`SECRET_KEY` they cannot be decrypted.
- Reset tokens are signed and expire after 1 hour.
- File downloads and deletes are scoped to the logged-in user (`...filter_by(id=..., user_id=current_user.id)`).
- For production, always set a strong `SECRET_KEY` + `ENCRYPTION_KEY` and serve over HTTPS.

## Production

`Procfile` is included for hosts like Render/Heroku. Set `DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, and mail variables in the host's environment settings, then deploy with `pip install -r requirements.txt` as the build command.
