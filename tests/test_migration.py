"""Schema/migration check against an *old* database file.

The real ``app.db`` is never used: a throwaway SQLite file is created with a
pre-voice schema, then the app is imported in a subprocess so its
``db.create_all()`` + ``_sync_schema()`` startup path runs against it.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_old_database_is_upgraded_in_place(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            username VARCHAR(150) UNIQUE NOT NULL,
            email VARCHAR(150) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE security_log (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            login_id VARCHAR(150),
            ip_address VARCHAR(45),
            timestamp DATETIME,
            status VARCHAR(20),
            user_agent VARCHAR(255),
            alert_sent BOOLEAN
        );
        INSERT INTO user (username, email, password_hash)
        VALUES ('legacy', 'legacy@example.test', 'stored-hash');
        """
    )
    conn.commit()
    conn.close()

    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
    env["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    env["TRASH_FOLDER"] = str(tmp_path / "trash")
    env["SECRET_KEY"] = "migration-test-secret"

    result = subprocess.run(
        [sys.executable, "-c", "import app; print('imported')"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # New tables from the sharing/voice work exist.
    assert {"voice_context", "share_link", "share_link_item", "share_access", "contact"} <= tables
    # Existing tables gained their new nullable columns.
    assert {"hidden_folders", "name", "profile_photo"} <= _columns(conn, "user")
    assert {"tool", "outcome"} <= _columns(conn, "security_log")
    # Existing rows survive untouched.
    rows = list(conn.execute("SELECT username, password_hash FROM user"))
    assert rows == [("legacy", "stored-hash")]
    # Voice columns the assistant relies on are present.
    assert {
        "pending_action",
        "pending_nonce",
        "pending_summary",
        "pending_expires_at",
        "window_started_at",
        "command_count",
    } <= _columns(conn, "voice_context")
    conn.close()
