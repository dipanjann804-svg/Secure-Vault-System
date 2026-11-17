# 🛡️ Security Hardening Guide — Secure Vault System

This document describes how to protect the Secure Vault System (Flask 3, Flask-Login, Flask-SQLAlchemy, Waitress, Fernet) against the common web attack classes: injection, authentication/session attacks, traffic/availability attacks, application-logic flaws, and malware/component exploits.

> **Honest scope note:** no website is ever "100% protected". The goal here is **defense in depth** — several independent layers per attack, so one mistake doesn't become a breach. Treat this as a living checklist: implement it, test it (see [Verification](#10-verification--testing)), and re-run the checks whenever dependencies or routes change.

> The snippets below are drop-in patterns written for the stack in the README. Adapt names (e.g. `Document.stored_filename`) to match your actual `app.py` / `models.py`.

---

## Table of contents

1. [Attack → defense matrix](#1-attack--defense-matrix)
2. [Implementation order (do these first)](#2-implementation-order)
3. [New dependencies & environment variables](#3-new-dependencies--environment-variables)
4. [Foundation: secure config, headers, CSRF, rate limiting](#4-foundation)
5. [Injection attacks](#5-injection-attacks)
6. [Authentication & session attacks](#6-authentication--session-attacks)
7. [Traffic & availability attacks](#7-traffic--availability-attacks)
8. [Application logic & data exposure](#8-application-logic--data-exposure)
9. [Malware & component exploits](#9-malware--component-exploits)
10. [Verification & testing](#10-verification--testing)
11. [Production deployment checklist](#11-production-deployment-checklist)

---

## 1. Attack → defense matrix

| Attack | Primary defenses | Section |
|---|---|---|
| SQL Injection | SQLAlchemy ORM / bound parameters only, never string-built SQL | 5.1 |
| XSS | Jinja autoescape, no `\|safe`, strict CSP with nonces, `HttpOnly` cookies | 5.2 |
| RCE / Command injection | `debug=False`, no `eval/exec/subprocess/pickle`, safe upload handling | 5.3 |
| LFI / RFI | No user-controlled paths/templates, UUID filenames, `send_from_directory` | 5.4 |
| Broken authentication | Strong password policy, lockout, MFA (TOTP), single-use reset tokens | 6.1 |
| Brute force / credential stuffing | Rate limiting + per-account lockout + generic errors | 6.2 |
| Session hijacking | `Secure`/`HttpOnly`/`SameSite` cookies, HTTPS+HSTS, session versioning | 6.3 |
| Clickjacking | `frame-ancestors 'none'` + `X-Frame-Options: DENY` | 6.4 |
| DoS / DDoS | Request size limits, rate limits, Waitress limits, reverse proxy/CDN | 7.1 |
| MitM | HTTPS only, HSTS, verified SMTP TLS | 7.2 |
| DNS / ARP spoofing | HSTS (+preload), TLS cert validation, DNSSEC, don't expose on open Wi-Fi | 7.3 |
| CSRF | Flask-WTF `CSRFProtect`, POST-only state changes, `SameSite` cookies | 8.1 |
| Path traversal | `secure_filename`, `send_from_directory`, owner-scoped lookups | 8.2 |
| SSRF | Don't fetch user-supplied URLs; allowlist if you ever must | 8.3 |
| XXE | Don't parse XML from users; `defusedxml` if you must | 8.4 |
| Malware / ransomware | Extension + magic-byte allowlist, no execution, AV scan, offline backups | 9.1 |
| Vulnerable components | Pinned deps, `pip-audit`, Dependabot, `bandit` | 9.2 |

---

## 2. Implementation order

Highest risk-reduction first:

**Phase 1 — Critical (do before any public deployment)**
- [ ] Set strong, **separate** `SECRET_KEY` and `ENCRYPTION_KEY`; app refuses to start in production without them
- [ ] Remove/replace the generic `/uploads/<filename>` route with an owner-checked download route
- [ ] Never run with `debug=True`; serve only through Waitress behind HTTPS
- [ ] Add CSRF protection to **every** POST (including the JSON batch-delete)
- [ ] Make `/logout` POST-only
- [ ] Secure cookie flags + HTTPS redirect + HSTS
- [ ] Rate limiting + account lockout on `/login`, `/forgot-password`, `/register`
- [ ] Upload allowlist, UUID filenames, `MAX_CONTENT_LENGTH`

**Phase 2 — High**
- [ ] Security headers + CSP (Flask-Talisman)
- [ ] Single-use reset tokens, generic forgot-password response, reset link built from `APP_URL` (not the `Host` header)
- [ ] Session invalidation on password change
- [ ] Open-redirect protection on `next`
- [ ] Strong password policy
- [ ] Verified SMTP TLS

**Phase 3 — Hardening**
- [ ] TOTP-based MFA
- [ ] Encrypt uploaded documents at rest (not only notes)
- [ ] Antivirus scan on upload (ClamAV)
- [ ] Audit log alerts, encrypted off-site backups
- [ ] CI: `pip-audit`, `bandit`, Dependabot

---

## 3. New dependencies & environment variables

Add to `requirements.txt` (pin exact versions with `pip freeze` after installing):

```txt
Flask-WTF          # CSRF protection
Flask-Limiter      # rate limiting
Flask-Talisman     # security headers / CSP / HTTPS redirect
Pillow             # re-encode avatar images
filetype           # magic-byte file type detection
zxcvbn             # password strength estimation
pyotp              # TOTP MFA (Phase 3)
defusedxml         # only if you ever parse XML
```

Dev/CI tools (not deployed): `pip-audit`, `bandit`, `pytest`.

Additions to `.env.example`:

```env
FLASK_ENV=production
SECRET_KEY=<64+ random chars>           # python -c "import secrets; print(secrets.token_urlsafe(64))"
ENCRYPTION_KEY=<fernet key>             # must differ from SECRET_KEY, never derived from it
APP_URL=https://vault.example.com       # used for reset links (prevents Host-header poisoning)
RATELIMIT_STORAGE_URI=redis://localhost:6379/0
FORCE_HTTPS=1
```

Make sure `.gitignore` contains: `.env`, `app.db`, `uploads/`, `venv/`, `*.pem`. If a secret was **ever** committed, rotate it — deleting the commit is not enough.

---

## 4. Foundation

### 4.1 `config.py` — fail fast, secure defaults

```python
import os
from datetime import timedelta

def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} must be set")
    return val

class Config:
    IS_PROD = os.environ.get("FLASK_ENV") == "production"

    SECRET_KEY = _required("SECRET_KEY") if IS_PROD else os.environ.get("SECRET_KEY", "dev-only-change-me")
    ENCRYPTION_KEY = _required("ENCRYPTION_KEY") if IS_PROD else os.environ.get("ENCRYPTION_KEY")

    # --- Sessions / cookies ---
    SESSION_COOKIE_NAME = "__Host-session" if IS_PROD else "session"  # __Host- needs Secure, Path=/, no Domain
    SESSION_COOKIE_SECURE = IS_PROD
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = IS_PROD
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=7)

    # --- Uploads ---
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024      # hard cap per request (matches README)
    UPLOAD_FOLDER = os.path.abspath("uploads") # keep OUTSIDE /static and any web root

    # --- CSRF ---
    WTF_CSRF_TIME_LIMIT = 3600
    WTF_CSRF_SSL_STRICT = IS_PROD

    # --- Misc ---
    APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:5555")
    PREFERRED_URL_SCHEME = "https" if IS_PROD else "http"
```

### 4.2 `security.py` — shared extensions & headers

```python
import os
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

csrf = CSRFProtect()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour", "60 per minute"],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),  # use Redis in prod (multi-worker safe)
)

talisman = Talisman()

CSP = {
    "default-src": "'self'",
    "script-src": "'self'",                       # + nonce, see below
    "style-src": ["'self'", "https://fonts.googleapis.com"],
    "font-src": ["'self'", "https://fonts.gstatic.com"],   # Inter font
    "img-src": ["'self'", "data:"],
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",                  # clickjacking defense
}

def init_security(app):
    csrf.init_app(app)
    limiter.init_app(app)
    talisman.init_app(
        app,
        force_https=app.config["IS_PROD"],
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=True,
        content_security_policy=CSP,
        content_security_policy_nonce_in=["script-src", "style-src"],
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        permissions_policy={"camera": "()", "microphone": "()", "geolocation": "()"},
    )

    @app.after_request
    def extra_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if resp.mimetype in ("text/html", "application/json"):
            resp.headers["Cache-Control"] = "no-store"   # don't cache vault pages in browser/proxies
        return resp
```

In `app.py`, call `init_security(app)` right after the app is created, and put `app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)` **only** if you're behind exactly one trusted reverse proxy (otherwise clients can spoof their IP).

**Templates:** with the nonce setup, inline `<script>` / `<style>` blocks need the nonce — or (better) move them into files under `static/`:

```html
<script nonce="{{ csp_nonce() }}">/* only if you must keep inline JS */</script>
```

**CSRF in every form:**

```html
<form method="post">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  ...
</form>
```

**CSRF for `fetch` / JSON (batch delete):**

```html
<meta name="csrf-token" content="{{ csrf_token() }}">
```
```js
const token = document.querySelector('meta[name="csrf-token"]').content;
fetch("/documents/delete", {
  method: "POST",
  headers: {"Content-Type": "application/json", "X-CSRFToken": token},
  body: JSON.stringify({ids}),
});
```

---

## 5. Injection attacks

### 5.1 SQL Injection

You use SQLAlchemy, so you're safe **as long as** you never build SQL from strings.

```python
# ✅ Safe — ORM / bound parameters
user = User.query.filter((User.email == ident) | (User.username == ident)).first()
docs = Document.query.filter(
    Document.user_id == current_user.id,
    Document.original_filename.ilike(f"%{escaped_q}%", escape="\\"),
).all()

# ❌ NEVER
db.session.execute(f"SELECT * FROM user WHERE email = '{ident}'")

# ✅ If you must use raw SQL, bind parameters
from sqlalchemy import text
db.session.execute(text("SELECT * FROM user WHERE email = :e"), {"e": ident})
```

Search box helper (escape `%` and `_` wildcards so users can't craft expensive patterns):

```python
def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

Extra layers:
- Validate input types/lengths (`len(q) <= 100`, `int` route converters like `<int:doc_id>`).
- In production Postgres, run the app with a **least-privilege DB user** (no `SUPERUSER`, no `CREATE ROLE`, no access to other databases) and `sslmode=require`.
- Grep the codebase: `grep -rnE "execute\(f|execute\(.*%|\.format\(" .` — every hit must be reviewed.

### 5.2 Cross-Site Scripting (XSS)

Notes are decrypted and shown back to the user — that's the highest-risk XSS surface.

- Jinja2 autoescapes `.html` templates. **Never** use `{{ note.content|safe }}` or `Markup(...)` on user data.
- When passing data to JavaScript, use `|tojson`, never string interpolation:
  ```html
  <script nonce="{{ csp_nonce() }}">
    const notes = {{ notes_for_js|tojson }};
  </script>
  ```
- Prefer `textContent` over `innerHTML` in your vanilla JS. If you ever render rich text/markdown, sanitize with `bleach`/`nh3` using a tight allowlist.
- The strict CSP from §4.2 (no `unsafe-inline`, no external script hosts) is the safety net: even if an injection slips through, inline script won't run.
- Cookies are `HttpOnly`, so injected script can't read the session cookie.
- Serve downloads as attachments with `nosniff` (see §8.2) so an uploaded `.html` can never execute in your origin.
- `localStorage` is only used for the theme — never store tokens or secrets there.

### 5.3 Remote Code Execution / Command Injection

- **Never** `app.run(debug=True)` or enable the Werkzeug debugger in any internet-reachable environment — the debugger console is remote code execution. Run only via Waitress:
  ```python
  if __name__ == "__main__":
      from waitress import serve
      serve(app, host="127.0.0.1", port=int(os.environ.get("PORT", 5555)))
  ```
  Binding to `0.0.0.0` (the "Wi-Fi URL" feature) exposes the app to everyone on the network; use it only on a trusted LAN in dev, and bind to `127.0.0.1` behind Nginx in production.
- Ban these on user-influenced data: `eval`, `exec`, `os.system`, `subprocess(..., shell=True)`, `pickle.loads`, `yaml.load` (use `yaml.safe_load`), `__import__`, `render_template_string` with user text (server-side template injection).
- If you add antivirus scanning or image processing that shells out, use list-form `subprocess.run([...], shell=False)` and never put user-supplied filenames in the command; use the UUID path.
- Run the process as a non-root user, ideally in a container with a read-only filesystem except `uploads/` and the DB.
- Run `bandit -r .` in CI — it flags most of the above automatically.

### 5.4 File Inclusion (LFI / RFI)

- Never build a template name, file path, or import from request data:
  ```python
  # ❌ render_template(request.args["page"])  /  open(request.args["file"])
  ```
- Uploaded files are stored with a server-generated name (§8.2) and never `import`ed, `include`d, or rendered as templates.
- Keep `uploads/` out of `static/` and out of the Jinja search path.
- Python has no remote-include feature, but `requests.get(user_url)` piped into `exec`/templates would act as one — see the rules above.

---

## 6. Authentication & session attacks

### 6.1 Broken authentication

**Password policy** (length beats complexity rules):

```python
from zxcvbn import zxcvbn

def validate_password(pw: str, *, username="", email="") -> list[str]:
    errors = []
    if len(pw) < 12:
        errors.append("Use at least 12 characters.")
    if len(pw) > 128:
        errors.append("Password is too long.")        # avoids hashing-DoS
    result = zxcvbn(pw, user_inputs=[username, email.split("@")[0]])
    if result["score"] < 3:
        errors.append("Password is too easy to guess. Try a longer passphrase.")
    return errors
```

Keep Werkzeug's default hashing (scrypt) — don't lower the cost factor. Use `check_password_hash` only (constant-time compare).

**Generic error messages & timing-safe login** (don't reveal whether an account exists):

```python
from werkzeug.security import check_password_hash, generate_password_hash

_DUMMY_HASH = generate_password_hash("not-a-real-password")

def authenticate(identifier: str, password: str):
    user = User.query.filter(
        (User.email == identifier.lower()) | (User.username == identifier)
    ).first()
    # Always do one hash check so "user not found" takes as long as "wrong password"
    ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, password)
    return user if (user and ok) else None

# in the route, on failure:
flash("Invalid credentials.")        # same message for unknown user / wrong password / locked
```

**Password reset hardening** (single-use, short-lived, not poisonable):

```python
import hashlib
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app, url_for

def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="pw-reset-v1")

def make_reset_token(user) -> str:
    # Bind token to the CURRENT password hash → becomes invalid the moment the password changes (single use)
    fingerprint = hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]
    return _serializer().dumps({"uid": user.id, "fp": fingerprint})

def read_reset_token(token: str, max_age=3600):
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data["uid"])
    if not user:
        return None
    fp = hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]
    return user if fp == data["fp"] else None

def reset_link(token: str) -> str:
    # Build from configured APP_URL — NOT request.host_url — to prevent Host-header poisoning
    return current_app.config["APP_URL"].rstrip("/") + url_for("reset_password", token=token)
```

`/forgot-password` must return the **same response** whether or not the email exists ("If that account exists, we've sent a link"), be rate limited, and send the email without making the response slower for existing accounts (queue it / send in a background thread).

**Session invalidation on password change/reset:** add `session_version = db.Column(db.Integer, default=0, nullable=False)` to `User`, bump it on password change, and include it in the Flask-Login id:

```python
class User(UserMixin, db.Model):
    ...
    def get_id(self):
        return f"{self.id}:{self.session_version}"

@login_manager.user_loader
def load_user(session_id):
    try:
        uid, ver = session_id.split(":")
        user = db.session.get(User, int(uid))
    except (ValueError, AttributeError):
        return None
    return user if user and user.session_version == int(ver) else None
```

**MFA (TOTP) — Phase 3:** store a per-user `totp_secret` (Fernet-encrypted with `ENCRYPTION_KEY`), verify with `pyotp.TOTP(secret).verify(code, valid_window=1)` after the password step, and issue 8–10 single-use hashed recovery codes. Rate limit the code endpoint like the password endpoint.

### 6.2 Brute force & credential stuffing

Three layers: per-IP rate limit, per-account lockout, and monitoring.

```python
from datetime import datetime, timedelta
from security import limiter

MAX_FAILS = 5
LOCK_FOR = timedelta(minutes=15)

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per hour")        # per IP
def login():
    if request.method == "POST":
        ident = request.form.get("identifier", "").strip()[:254]
        pw = request.form.get("password", "")[:128]

        user = User.query.filter((User.email == ident.lower()) | (User.username == ident)).first()
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            log_attempt(ident, "FAILED")
            flash("Invalid credentials.")            # don't reveal the lock
            return render_template("login.html"), 429

        if authenticate(ident, pw):
            user.failed_attempts, user.locked_until = 0, None
            db.session.commit()
            log_attempt(ident, "SUCCESS")
            login_user(user)
            session.permanent = True
            return redirect(safe_next(request.args.get("next")))

        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILS:
                user.locked_until = datetime.utcnow() + LOCK_FOR
                user.failed_attempts = 0
            db.session.commit()
        log_attempt(ident, "FAILED")
        flash("Invalid credentials.")
    return render_template("login.html")
```

(This is the logic your `utils/time.py` prototype was meant for — add `failed_attempts` and `locked_until` columns to `User`.)

Also apply limiters to the other abuse-prone endpoints:

```python
@limiter.limit("5 per hour")   # /forgot-password  — also per email address, not only per IP
@limiter.limit("10 per hour")  # /register
@limiter.limit("20 per hour")  # /change-password
```

Credential stuffing specifics: add MFA, optionally reject passwords found in breach corpora (HaveIBeenPwned k-anonymity API — only the first 5 chars of the SHA-1 are sent), and alert users on logins from a new IP/user-agent using your `SecurityLog` table. Add a CAPTCHA (e.g. hCaptcha/Turnstile) after repeated failures from one IP.

### 6.3 Session hijacking & fixation

- Cookie flags are set in `Config` (§4.1): `Secure`, `HttpOnly`, `SameSite=Lax`, `__Host-` prefix, 2-hour lifetime.
- Set `login_manager.session_protection = "strong"` — Flask-Login then invalidates the session if the client's IP/User-Agent fingerprint changes.
- Flask's signed-cookie session is replaced on login, but also call `session.clear()` right before `login_user()` to prevent session fixation.
- Log out fully and **via POST**:
  ```python
  @app.route("/logout", methods=["POST"])
  @login_required
  def logout():
      logout_user()
      session.clear()
      return redirect(url_for("login"))
  ```
  ```html
  <form action="/logout" method="post"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button>Log out</button></form>
  ```
- Never put session IDs/tokens in URLs. Reset tokens in URLs are acceptable only because they're short-lived and single-use — also add `Referrer-Policy: no-referrer` on the reset page, and an `Cache-Control: no-store`.
- Prevent open redirects after login:
  ```python
  from urllib.parse import urlparse, urljoin

  def safe_next(target, default="dashboard"):
      if not target:
          return url_for(default)
      ref, test = urlparse(request.host_url), urlparse(urljoin(request.host_url, target))
      ok = test.scheme in ("http", "https") and ref.netloc == test.netloc
      return target if ok else url_for(default)
  ```

### 6.4 Clickjacking

Already covered by §4.2 (`frame-ancestors 'none'` and `X-Frame-Options: DENY`). Verify with `curl -sI https://your-site | grep -iE "frame|content-security"`. For sensitive actions (delete, change password) the CSRF token also blocks cross-origin forced submissions, and consider re-asking for the password before changing it (already the case if `/change-password` requires the current password — keep that).

---

## 7. Traffic & availability attacks

### 7.1 DoS / DDoS

Application-level DoS is what you can fix in code; volumetric DDoS must be absorbed upstream (CDN/WAF/host).

**In Flask:**
- `MAX_CONTENT_LENGTH` (set in §4.1). The README's 50 MB per request is large — consider 10–25 MB and a per-user storage quota (e.g. 500 MB) checked before saving.
- Rate limits (Flask-Limiter) on every route; stricter on expensive ones (upload, login, search).
- Cap pagination/search sizes (`LIMIT 50`), validate query length.
- Don't hold file bytes in memory: use `file.save(path)` (streams), and for encrypted-at-rest files process in chunks.
- Database: connection pool limits (`SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 10, "max_overflow": 5, "pool_pre_ping": True}`), add indexes on `user_id`, `email`, `username`.

**Waitress limits:**

```python
serve(
    app,
    host="127.0.0.1",
    port=port,
    threads=8,
    connection_limit=200,
    channel_timeout=30,                       # drops slow-loris style connections
    max_request_body_size=50 * 1024 * 1024,
    max_request_header_size=16 * 1024,
)
```

**Nginx in front (recommended):**

```nginx
limit_req_zone $binary_remote_addr zone=login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=addr:10m;

server {
    listen 80;
    server_name vault.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name vault.example.com;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    # ssl_certificate / ssl_certificate_key from Let's Encrypt (certbot)

    client_max_body_size 50m;
    client_body_timeout 15s;
    client_header_timeout 15s;
    keepalive_timeout 15s;
    send_timeout 15s;
    limit_conn addr 20;
    server_tokens off;

    location = /login { limit_req zone=login burst=5 nodelay; proxy_pass http://127.0.0.1:5555; include proxy_params; }
    location /        { limit_req zone=general burst=30 nodelay; proxy_pass http://127.0.0.1:5555; include proxy_params; }
}
```

**Upstream:** put the site behind Cloudflare (or your host's equivalent) for DDoS absorption + WAF managed rules; enable "Under Attack" mode during incidents; keep the origin IP private and firewall it to accept traffic only from the CDN/proxy.

### 7.2 Man-in-the-Middle

- **HTTPS everywhere:** TLS 1.2+ certificate (Let's Encrypt), HTTP → HTTPS redirect, `Secure` cookies, HSTS (`max-age=31536000; includeSubDomains`) — all configured above. Never serve the vault over plain HTTP on a real network, including "Wi-Fi URL" mode.
- **SMTP TLS with certificate verification** (the README mentions a TLS/SSL fallback — make sure it validates certs and never downgrades silently to plaintext):
  ```python
  import smtplib, ssl

  ctx = ssl.create_default_context()          # verifies server certificate + hostname
  try:
      with smtplib.SMTP(server, 587, timeout=15) as s:
          s.starttls(context=ctx)
          s.login(user, password)
          s.send_message(msg)
  except (smtplib.SMTPException, OSError):
      with smtplib.SMTP_SSL(server, 465, context=ctx, timeout=15) as s:
          s.login(user, password)
          s.send_message(msg)
  ```
  Never fall back to unencrypted SMTP, and never set `ctx.check_hostname = False` or `verify_mode = CERT_NONE`.
- Database connection in production: `DATABASE_URL=postgresql://...?sslmode=require` (or `verify-full`).
- Sensitive content should be encrypted at the app level too (notes already are; see §8.5 for files).

### 7.3 DNS / ARP spoofing

These attacks happen on the network, not in your code, so the defense is making a successful spoof useless:

- **HSTS** (+ submit the domain to the HSTS preload list once you're sure HTTPS works for all subdomains) so browsers refuse a downgraded/spoofed HTTP copy.
- **Valid TLS certificate** — a spoofed replica can't present one for your domain; users must never click through certificate warnings.
- **DNSSEC** and a registrar with registry lock + 2FA on the DNS/registrar account; enable **CAA** records so only your CA can issue certificates.
- Don't expose the app on `0.0.0.0` over public/shared Wi-Fi (ARP spoofing is trivial on a shared LAN); use `127.0.0.1` or a VPN.
- Server-side: use `ssl.create_default_context()` for all outbound connections (SMTP, DB, APIs) so a spoofed DNS answer can't impersonate them.

---

## 8. Application logic & data exposure

### 8.1 CSRF

- `CSRFProtect` is enabled globally in §4.2, so every `POST/PUT/PATCH/DELETE` is rejected without a valid token — including upload, note add/delete, profile update, and change-password.
- State-changing actions are **POST only** (never GET): fix `/logout`, and make sure no delete/update route accepts GET.
- `SameSite=Lax` cookies add a second layer.
- Don't exempt routes with `@csrf.exempt` unless they're token-authenticated APIs.

### 8.2 Path traversal & file handling (also fixes the `/uploads/<filename>` risk)

The README lists `GET /uploads/<filename>` — "Serve uploaded file" — which is the most likely place for a traversal or an IDOR (any logged-in user fetching someone else's file by guessing a name). Replace it with owner-scoped routes only.

**Saving files:**

```python
import os, uuid
import filetype
from flask import current_app, abort
from werkzeug.utils import secure_filename

ALLOWED = {
    "pdf":  {"application/pdf"},
    "png":  {"image/png"},
    "jpg":  {"image/jpeg"}, "jpeg": {"image/jpeg"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "txt":  None,                       # no magic bytes for plain text
}

def save_upload(fs, user_id: int):
    original = secure_filename(fs.filename or "")
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED:
        abort(400, "File type not allowed")

    stored = f"{uuid.uuid4().hex}.{ext}"                      # never trust/keep the client's name on disk
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(user_id))
    os.makedirs(folder, mode=0o750, exist_ok=True)
    path = os.path.join(folder, stored)
    fs.save(path)

    expected = ALLOWED[ext]
    if expected is not None:
        kind = filetype.guess(path)
        if not kind or kind.mime not in expected:
            os.remove(path)
            abort(400, "File content does not match its extension")

    os.chmod(path, 0o640)
    return original, stored, os.path.getsize(path)           # store original name only as DB metadata
```

**Downloading files (owner-scoped, always as attachment):**

```python
from flask import send_from_directory

@app.route("/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_user.id))
    resp = send_from_directory(           # internally uses safe_join → blocks ../
        folder, doc.stored_filename,
        as_attachment=True,
        download_name=doc.original_filename,
        mimetype="application/octet-stream",   # never let the browser render user content inline
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp
```

**Avatars:** re-encode with Pillow (strips embedded payloads/EXIF), cap dimensions, and serve through an authenticated route:

```python
from PIL import Image

def save_avatar(fs, user_id):
    img = Image.open(fs.stream)
    img.verify(); fs.stream.seek(0)
    img = Image.open(fs.stream).convert("RGB")
    img.thumbnail((512, 512))
    name = f"{uuid.uuid4().hex}.jpg"
    img.save(os.path.join(current_app.config["UPLOAD_FOLDER"], "avatars", name), "JPEG", quality=85)
    return name
```
Set `Image.MAX_IMAGE_PIXELS = 20_000_000` to prevent decompression bombs.

**Authorization checklist (IDOR):** every query touching `Document`/`Note`/`SecurityLog` filters by `user_id=current_user.id` — including batch delete (verify each id belongs to the user, ignore others) and search.

### 8.3 SSRF

The app currently makes **no** user-directed outbound requests — keep it that way. If you add anything that fetches a URL (avatar-from-URL, link previews, webhooks, HIBP checks):

- Never take a full URL from the user; use fixed hosts (e.g. `https://api.pwnedpasswords.com/range/<prefix>`).
- If a user-supplied URL is unavoidable: allowlist hostnames, resolve DNS yourself and reject private/loopback/link-local ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16` incl. cloud metadata `169.254.169.254`, `::1`, `fc00::/7`), disable redirects (or re-validate each hop), set short timeouts and response-size caps, and pin the resolved IP for the actual request.
- Firewall egress from the app host to only what it needs (SMTP, DB, package mirrors).

### 8.4 XXE

The app doesn't parse XML. Note that `.docx`/`.xlsx`/`.pptx` files are **zipped XML** — storing them is fine, but if you ever add preview/text-extraction/search-indexing:

- Parse with `defusedxml` (not stdlib `xml.etree`/`lxml` defaults), or use libraries that disable external entities.
- Limit decompressed size to defeat zip bombs (check `ZipInfo.file_size` totals and compression ratio before extracting).
- Run extraction in a separate sandboxed process with CPU/memory/time limits.

### 8.5 Data exposure & encryption

- **Separate keys:** `ENCRYPTION_KEY` must be its own Fernet key. The README's fallback — deriving it from `SECRET_KEY` — means leaking the session secret also decrypts every note; in production, refuse to start without a dedicated `ENCRYPTION_KEY`.
- **Key rotation:** use `MultiFernet` so you can rotate without losing old notes:
  ```python
  from cryptography.fernet import Fernet, MultiFernet

  def _fernet():
      keys = [k.strip() for k in os.environ["ENCRYPTION_KEY"].split(",") if k.strip()]  # newest first
      return MultiFernet([Fernet(k) for k in keys])

  def encrypt_note(plain: str) -> str:
      return _fernet().encrypt(plain.encode()).decode()

  def decrypt_note(token: str) -> str:
      return _fernet().decrypt(token.encode()).decode()
  ```
  Rotate by prepending a new key, then re-encrypt rows with `_fernet().rotate(token)`.
- **Encrypt documents at rest too.** Today only notes are encrypted; uploaded files sit as plaintext in `uploads/`. Encrypt after validation and decrypt on download (for big files use chunked encryption such as AES-GCM streaming, as Fernet holds the whole file in memory). The stronger "zero-knowledge" model derives a per-user key from the user's password with scrypt — more private, but a forgotten password then means unrecoverable data.
- Encrypt the database/volume at rest (Postgres on an encrypted disk, LUKS/BitLocker on the host).
- Don't log secrets, note contents, reset tokens, or full passwords; mask emails in logs where possible.
- Error handling: custom 400/403/404/500 pages with no stack traces; `app.config["PROPAGATE_EXCEPTIONS"] = False`; log details server-side only.
- Return **404** (not 403) for other users' resources so ids can't be probed.
- `/health/db` should return only `{"db": "ok"}` (no connection strings/versions) — or restrict to localhost/internal network.
- Security log hygiene: store IP + user-agent but apply retention (e.g. 90 days) and show users their own recent logins.

---

## 9. Malware & component exploits

### 9.1 Malware & ransomware

A vault accepts files from users, so treat every upload as hostile.

- **Allowlist** extensions + magic-byte check (§8.2). Block executables/scripts/archives you don't need: `.exe .dll .bat .cmd .ps1 .sh .js .jar .msi .scr .php .py .html .svg .htm`. (SVG and HTML can carry script — don't accept them unless sanitized.)
- **Never execute or render uploads:** stored outside the web root, random names, downloaded as `application/octet-stream` + `attachment`.
- **Antivirus scan** before accepting (ClamAV daemon):
  ```python
  import clamd   # pip install clamd

  def scan_or_reject(path):
      cd = clamd.ClamdUnixSocket()
      status, sig = cd.scan(path)[path]
      if status == "FOUND":
          os.remove(path)
          abort(400, "Upload rejected by malware scanner")
  ```
- Per-user quota and per-file size limit; reject zero-byte files and double extensions (`invoice.pdf.exe`) — `secure_filename` + the allowlist handles the latter.
- **Ransomware resilience** (assume the server could be compromised):
  - Automated, **encrypted, off-site/immutable backups** of the DB and `uploads/` (3-2-1 rule), and test restores regularly.
  - App runs as a low-privilege user that can't modify the app code, only write to `uploads/` and the DB.
  - Backups use credentials the app server can't delete from (append-only / versioned bucket).
- Keep the host patched (unattended security updates), disable unused services, firewall everything except 80/443 (and SSH from your IP only, key-based auth, no root login).

### 9.2 Components with known vulnerabilities

- **Pin** exact versions in `requirements.txt` (ideally with hashes via `pip-compile --generate-hashes`).
- Scan on every push and weekly:
  ```bash
  pip install pip-audit bandit
  pip-audit -r requirements.txt     # known CVEs in dependencies
  bandit -r . -x ./venv,./tests     # insecure code patterns in your own code
  ```
- Enable **Dependabot** (`.github/dependabot.yml`):
  ```yaml
  version: 2
  updates:
    - package-ecosystem: "pip"
      directory: "/"
      schedule: { interval: "weekly" }
  ```
- Enable GitHub **secret scanning** and push protection.
- Keep Python (3.10+ → prefer the latest supported release), Flask, Werkzeug, Jinja2, `cryptography`, SQLAlchemy, Waitress and Pillow current; Pillow and `cryptography` in particular ship security fixes regularly.
- Load third-party JS/CSS only from your own `/static` (or use Subresource Integrity hashes if you use a CDN) — the CSP in §4.2 enforces this. Self-hosting the Inter font removes the Google Fonts dependency and lets you drop those CSP hosts.
- Remove unused code and files (`templates/base.html` legacy layout, `static/js/alert.js` "demo JS not required", `utils/time.py` once ported) — less code, less attack surface.

---

## 10. Verification & testing

**Automated tests (`tests/test_security.py`):**

```python
def test_cannot_download_other_users_file(client, user_a_login, user_b_document):
    r = client.get(f"/documents/{user_b_document.id}/download")
    assert r.status_code == 404

def test_post_without_csrf_rejected(client_logged_in):
    r = client_logged_in.post("/notes/add", data={"title": "x", "content": "y"})
    assert r.status_code == 400

def test_logout_requires_post(client_logged_in):
    assert client_logged_in.get("/logout").status_code == 405

def test_login_is_generic_and_locks(client, user):
    for _ in range(6):
        r = client.post("/login", data={"identifier": user.email, "password": "wrong"})
    assert b"Invalid credentials" in r.data

def test_security_headers(client):
    r = client.get("/")
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options"):
        assert h in r.headers

def test_traversal_blocked(client_logged_in):
    assert client_logged_in.get("/uploads/../app.py").status_code in (404, 403)

def test_upload_rejects_exe(client_logged_in):
    r = client_logged_in.post("/documents/upload",
        data={"files": (io.BytesIO(b"MZ..."), "evil.exe"), "folder": "Misc"},
        content_type="multipart/form-data")
    assert r.status_code == 400
```

**Manual / external checks:**

| Check | Tool |
|---|---|
| Missing headers, CSP, cookies | `curl -sI https://your-site`, [securityheaders.com](https://securityheaders.com), browser DevTools → Application → Cookies |
| TLS config | [SSL Labs](https://www.ssllabs.com/ssltest/) — aim for A/A+ |
| Dynamic scan (XSS, SQLi, CSRF, traversal) | OWASP ZAP baseline: `docker run -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t https://your-site` — **only against a site you own** |
| SQLi probing of your own search/login | `sqlmap -u "https://your-site/dashboard?q=1" --cookie="..."` — **only against your own staging instance** |
| Brute-force protection | script 20 bad logins → confirm 429 / lockout |
| Dependency CVEs | `pip-audit`, Dependabot alerts |
| Static code scan | `bandit -r .` |
| Secrets in git history | `gitleaks detect` / `trufflehog git file://.` |

---

## 11. Production deployment checklist

**Secrets & config**
- [ ] `FLASK_ENV=production`, `debug` is off everywhere
- [ ] `SECRET_KEY` (64+ random chars) and a separate `ENCRYPTION_KEY` set; stored in the host's secret manager, not in the repo
- [ ] `APP_URL` set to the real https URL
- [ ] `.env`, `app.db`, `uploads/` git-ignored; no secrets in git history

**Network & transport**
- [ ] Valid TLS cert, HTTP→HTTPS redirect, HSTS enabled, TLS 1.2+ only
- [ ] App listens on `127.0.0.1`; Nginx/Cloudflare in front; firewall allows only 80/443 (+ SSH from trusted IPs)
- [ ] Rate limiting active (Redis-backed), Waitress limits configured
- [ ] Origin IP restricted to CDN/proxy

**Application**
- [ ] CSRF on all POSTs, `/logout` is POST
- [ ] Cookies: `Secure`, `HttpOnly`, `SameSite`, short lifetime
- [ ] CSP/security headers verified (securityheaders.com)
- [ ] Generic auth errors, lockout, password policy, single-use reset tokens, MFA available
- [ ] Owner-scoped download/delete; old `/uploads/<filename>` route removed
- [ ] Upload allowlist + magic-byte check + UUID names + size/quota limits (+ ClamAV)
- [ ] Custom error pages, no stack traces, `/health/db` minimal

**Data & operations**
- [ ] Postgres with least-privilege user, `sslmode=require`, encrypted disk
- [ ] Encrypted, tested, off-site backups
- [ ] Logs centralised, alerts on repeated failed logins/lockouts/password resets
- [ ] `pip-audit`, `bandit`, Dependabot, secret scanning running in CI
- [ ] A documented incident plan: how to rotate `SECRET_KEY` / `ENCRYPTION_KEY`, revoke all sessions (bump `session_version`), and restore from backup

---

### Reporting a vulnerability

If you discover a security issue in this project, please **do not** open a public issue. Email the maintainer privately with steps to reproduce, and allow reasonable time for a fix before disclosure.
