"""
Subscriber store — SQLite-backed opt-in list for Silex outbound email.

ETHICAL DESIGN NOTES:
  - Signup is OUTBOUND-ONLY. Being a subscriber means Silex MAY write to you
    (after approval); it does NOT add you to the incoming-processing whitelist.
    Subscriber addresses cannot inject into Silex's context.
  - Consent is explicit (recorded with timestamp + source).
  - Unsubscribe is one-click, token-based, immediate, and permanent.
  - This is a consent-managed mailing list; treat the data accordingly.
"""
import os
import sqlite3
import secrets
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SUBSCRIBERS_DB_PATH", "/app/data/subscribers/subscribers.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create the subscribers table if absent. Idempotent."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                email             TEXT UNIQUE NOT NULL,
                name              TEXT,
                status            TEXT NOT NULL DEFAULT 'active',  -- active | unsubscribed
                unsubscribe_token TEXT UNIQUE NOT NULL,
                consent_text      TEXT,
                source            TEXT,
                created_at        TEXT NOT NULL,
                unsubscribed_at   TEXT
            )
        """)
        c.commit()
    logger.info("subscribers DB ready at %s", DB_PATH)


def generate_unsubscribe_token() -> str:
    return secrets.token_urlsafe(24)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def add_subscriber(email: str, name: str = "", consent_text: str = "", source: str = "web") -> dict:
    """Add or reactivate a subscriber. Returns the subscriber row as dict."""
    email = _norm(email)
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid_email"}

    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        existing = c.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
        if existing:
            # reactivate if previously unsubscribed
            c.execute(
                "UPDATE subscribers SET status='active', name=?, consent_text=?, source=?, "
                "unsubscribed_at=NULL WHERE email=?",
                (name or existing["name"], consent_text, source, email),
            )
            c.commit()
            row = c.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
            return {"ok": True, "reactivated": True, **dict(row)}

        token = generate_unsubscribe_token()
        c.execute(
            "INSERT INTO subscribers (email,name,status,unsubscribe_token,consent_text,source,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (email, name, "active", token, consent_text, source, now),
        )
        c.commit()
        row = c.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
        return {"ok": True, "reactivated": False, **dict(row)}


def get_subscriber(email: str) -> dict | None:
    email = _norm(email)
    with _conn() as c:
        row = c.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def is_subscribed(email: str) -> bool:
    """True only if the address is an ACTIVE subscriber."""
    sub = get_subscriber(email)
    return bool(sub and sub.get("status") == "active")


def list_subscribers(active_only: bool = True) -> list:
    with _conn() as c:
        if active_only:
            rows = c.execute("SELECT * FROM subscribers WHERE status='active' ORDER BY created_at DESC").fetchall()
        else:
            rows = c.execute("SELECT * FROM subscribers ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def unsubscribe_by_token(token: str) -> dict:
    """One-click unsubscribe. Immediate and permanent (until they re-opt-in)."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        row = c.execute("SELECT * FROM subscribers WHERE unsubscribe_token=?", (token,)).fetchone()
        if not row:
            return {"ok": False, "error": "invalid_token"}
        c.execute(
            "UPDATE subscribers SET status='unsubscribed', unsubscribed_at=? WHERE unsubscribe_token=?",
            (now, token),
        )
        c.commit()
        return {"ok": True, "email": row["email"]}
