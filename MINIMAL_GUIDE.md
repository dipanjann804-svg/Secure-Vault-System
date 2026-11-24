# Secure Vault System — Local Run Guide

## How to run

```powershell
cd "C:\Users\User\Desktop\Secure-Vault-System"
python run_minimal.py
```

Then open **http://127.0.0.1:5555** in your browser.

---

## Why `python app.py` fails on this machine

```
ImportError: DLL load failed while importing _collections_cy:
An Application Control policy has blocked this file.
```

Windows Application Control is blocking SQLAlchemy's native extension
(`venv\Lib\site-packages\sqlalchemy\util\_collections_cy.pyd`). This is an
OS security policy, not a bug in the project. `run_minimal.py` runs the same
app using only Flask + the built-in `sqlite3` module, so no blocked DLL is
loaded.

---

## The `TypeError: Object of type Undefined is not JSON serializable` fix (permanent)

The dashboard template (`templates/dashboard.html`) uses Jinja's `|tojson`
filter on several variables:

```jinja
let BUILTIN_FOLDERS = {{ default_folders|tojson }};
let hiddenFolders  = {{ hidden_folders|tojson }};
let activeFolder   = {{ (active_folder or 'all')|tojson }};
const FOLDER_VIEW  = {{ 'true' if folder_view else 'false' }};
```

If **any** of these variables is not passed to `render_template`, Jinja
substitutes an `Undefined` object, and `|tojson` cannot serialize it — hence
the error.

**Permanent fix:** `run_minimal.py` now builds the *complete* context, exactly
like the original `_dashboard_context()` in `app.py`. Every route that renders
`dashboard.html` passes all nine variables:

| Variable | Type |
|---|---|
| `user` | object (id, username, email, name, phone, DOB, gender, location, photo) |
| `documents` | list of objects with `id`, `original_filename`, `file_size`, `file_type`, `folder`, `created_at` (datetime) |
| `notes` | list of dicts (`id`, `title`, `content`, `created_at` string) |
| `folders` | list |
| `default_folders` | list |
| `hidden_folders` | list |
| `folder_counts` | dict |
| `active_folder` | string or `None` |
| `folder_view` | bool |

Because all values are concrete and JSON-serializable, the error can no longer
occur — no matter which page loads.

---

## Working routes

| Route | Methods | Auth |
|---|---|---|
| `/` | GET | — |
| `/register` | GET, POST | — |
| `/login` | GET, POST | — |
| `/logout` | GET | ✅ |
| `/forgot-password` | GET, POST | — |
| `/reset-password/<token>` | GET, POST | — |
| `/change-password` | GET, POST | ✅ |
| `/dashboard` | GET | ✅ |
| `/folders/<folder_name>` | GET | ✅ |
| `/documents/upload` | POST | ✅ |
| `/documents/<id>/download` | GET | ✅ |
| `/documents/<id>/view` | GET | ✅ (in-browser preview: PDF, PNG, JPG, GIF, WEBP) |
| `/documents/<id>/share` | POST | ✅ (10 min / 1 h / 24 h, one-time or multi-use) |
| `/s/<token>` | GET | — (public share landing page) |
| `/s/<token>/download` | GET | — (public download) |
| `/documents/delete` | POST | ✅ (moves to 30-day Trash) |
| `/trash/list` | GET | ✅ |
| `/trash/restore` | POST | ✅ |
| `/trash/purge` | POST | ✅ |
| `/trash/empty` | POST | ✅ |
| `/notes/add` | POST | ✅ |
| `/notes/delete` | POST | ✅ |
| `/folders/remove` | POST | ✅ (files move to Trash) |
| `/folders/restore` | POST | ✅ |
| `/profile/update` | POST | ✅ |
| `/account/delete` | POST | ✅ (password + `DELETE MY ACCOUNT`) |
| `/uploads/<filename>` | GET | ✅ |
| `/health/db` | GET | — |

All of the above were verified end-to-end (register → login → upload →
download → preview → notes → profile → password change → delete → logout).

---

## Troubleshooting: "preview/download still not working"

Windows allows several processes to bind the **same port** at once. If an old
copy of the server is still running, your browser may be served by the stale
process (old code) even though you just started a new one.

`run_minimal.py` now refuses to start a duplicate on the same port:

```
[!] PORT 5555 IS ALREADY IN USE
```

If you see that, stop the other server first (Ctrl+C in its terminal), or use
another port:

```powershell
$env:PORT=5556; python run_minimal.py
```

To confirm which code your browser is actually talking to, open
**http://127.0.0.1:5555/health/db**. It returns the running build tag:

```json
{"db": "ok", "build": "minimal-4 (share links + 30-day trash + account deletion)"}
```

If the `build` value is missing or older, you are still on a stale server.

To stop every stray instance at once:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*run_minimal*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## Notes / limitations

- `run_minimal.py` stores data in the same `app.db` (SQLite) and `uploads/`
  folder as the original app, so data persists between runs.
- Missing columns in an older `app.db` are added automatically on startup
  (lightweight `ALTER TABLE` migration inside `init_db()`).
- Notes are stored as plain text in minimal mode (the original `app.py` uses
  Fernet encryption; that module is unaffected and can be re-enabled once the
  DLL policy is lifted).
- To run the original `app.py` with full SQLAlchemy features, allow
  `_collections_cy.pyd` in the Windows Application Control policy (or run on a
  machine without that restriction).
