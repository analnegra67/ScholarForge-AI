"""License server storage (SQLite). Personal data is minimised: e-mails and keys are stored as hashes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_ref TEXT,
    email_hash TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (provider, provider_ref)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    provider_ref TEXT UNIQUE,
    status TEXT NOT NULL,
    current_period_end INTEGER,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS licenses (
    id TEXT PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    checkout_ref TEXT UNIQUE,
    delivered INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activations (
    license_id TEXT NOT NULL REFERENCES licenses(id),
    device_id TEXT NOT NULL,
    app_version TEXT,
    created_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (license_id, device_id)
);
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    type TEXT NOT NULL,
    received_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_keys (
    checkout_ref TEXT PRIMARY KEY,
    plain_key TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""

KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_license_key() -> str:
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(5)]
    return "SFK-" + "-".join(groups)


class LicenseDB:
    def __init__(self, path: Path, pepper: str):
        self.path = Path(path)
        self.pepper = pepper.encode()
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")  # deleted plain keys are overwritten, not left in free pages
        return conn

    def hash_key(self, key: str) -> str:
        return hmac.new(self.pepper, key.strip().upper().encode(), hashlib.sha256).hexdigest()

    def hash_email(self, email: str | None) -> str | None:
        if not email:
            return None
        return hmac.new(self.pepper, email.strip().lower().encode(), hashlib.sha256).hexdigest()

    def seen_event(self, event_id: str, provider: str, etype: str) -> bool:
        """Record a webhook id; return True if it was already processed (idempotency)."""
        with self._lock, self._conn() as c:
            try:
                c.execute("INSERT INTO webhook_events(event_id, provider, type, received_at) VALUES (?, ?, ?, ?)",
                          (event_id, provider, etype, int(time.time())))
                return False
            except sqlite3.IntegrityError:
                return True

    def upsert_customer(self, provider: str, provider_ref: str | None, email: str | None) -> int:
        with self._lock, self._conn() as c:
            if provider_ref:
                row = c.execute("SELECT id FROM customers WHERE provider = ? AND provider_ref = ?",
                                (provider, provider_ref)).fetchone()
                if row:
                    if email:
                        c.execute("UPDATE customers SET email_hash = ? WHERE id = ?", (self.hash_email(email), row["id"]))
                    return int(row["id"])
            cur = c.execute("INSERT INTO customers(provider, provider_ref, email_hash, created_at) VALUES (?, ?, ?, ?)",
                            (provider, provider_ref, self.hash_email(email), int(time.time())))
            return int(cur.lastrowid)

    def upsert_subscription(self, customer_id: int, provider_ref: str | None, status: str,
                            period_end: int | None, cancel_at_period_end: bool = False) -> None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT id, current_period_end FROM subscriptions WHERE provider_ref = ?",
                            (provider_ref,)).fetchone() if provider_ref else None
            now = int(time.time())
            if row:
                end = period_end or row["current_period_end"]
                c.execute("UPDATE subscriptions SET status = ?, current_period_end = ?, cancel_at_period_end = ?,"
                          " updated_at = ? WHERE id = ?", (status, end, int(cancel_at_period_end), now, row["id"]))
            else:
                c.execute("INSERT INTO subscriptions(customer_id, provider_ref, status, current_period_end,"
                          " cancel_at_period_end, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (customer_id, provider_ref, status, period_end, int(cancel_at_period_end), now))

    def subscription_by_ref(self, provider_ref: str):
        with self._conn() as c:
            return c.execute("SELECT * FROM subscriptions WHERE provider_ref = ?", (provider_ref,)).fetchone()

    def subscription_for_customer(self, customer_id: int):
        with self._conn() as c:
            return c.execute("SELECT * FROM subscriptions WHERE customer_id = ? ORDER BY updated_at DESC LIMIT 1",
                             (customer_id,)).fetchone()

    def issue_license(self, customer_id: int, checkout_ref: str | None) -> tuple[str, str]:
        """Create a license (idempotent per checkout); return (license_id, plain key or '')."""
        with self._lock, self._conn() as c:
            if checkout_ref:
                row = c.execute("SELECT id FROM licenses WHERE checkout_ref = ?", (checkout_ref,)).fetchone()
                if row:
                    return row["id"], ""
            key = new_license_key()
            lid = "lic_" + secrets.token_hex(8)
            c.execute("INSERT INTO licenses(id, customer_id, key_hash, key_prefix, checkout_ref, created_at)"
                      " VALUES (?, ?, ?, ?, ?, ?)", (lid, customer_id, self.hash_key(key), key[:8], checkout_ref,
                                                     int(time.time())))
            if checkout_ref:
                c.execute("INSERT OR REPLACE INTO pending_keys(checkout_ref, plain_key, created_at) VALUES (?, ?, ?)",
                          (checkout_ref, key, int(time.time())))
            return lid, key

    def take_pending_key(self, checkout_ref: str) -> str | None:
        """Hand out the plain key once (the success page shows it), then forget it."""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT plain_key, created_at FROM pending_keys WHERE checkout_ref = ?",
                            (checkout_ref,)).fetchone()
            if not row:
                return None
            c.execute("DELETE FROM pending_keys WHERE checkout_ref = ?", (checkout_ref,))
            c.execute("UPDATE licenses SET delivered = 1 WHERE checkout_ref = ?", (checkout_ref,))
            if time.time() - row["created_at"] > 7 * 86400:
                return None
            return row["plain_key"]

    def license_by_key(self, key: str):
        with self._conn() as c:
            return c.execute("SELECT * FROM licenses WHERE key_hash = ?", (self.hash_key(key),)).fetchone()

    def activations(self, license_id: str) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM activations WHERE license_id = ?", (license_id,)).fetchall()

    def activate(self, license_id: str, device_id: str, app_version: str | None, max_devices: int) -> bool:
        with self._lock, self._conn() as c:
            now = int(time.time())
            row = c.execute("SELECT 1 FROM activations WHERE license_id = ? AND device_id = ?",
                            (license_id, device_id)).fetchone()
            if row:
                c.execute("UPDATE activations SET last_seen = ?, app_version = ? WHERE license_id = ? AND device_id = ?",
                          (now, app_version, license_id, device_id))
                return True
            count = c.execute("SELECT COUNT(*) FROM activations WHERE license_id = ?", (license_id,)).fetchone()[0]
            if count >= max_devices:
                return False
            c.execute("INSERT INTO activations(license_id, device_id, app_version, created_at, last_seen)"
                      " VALUES (?, ?, ?, ?, ?)", (license_id, device_id, app_version, now, now))
            return True

    def deactivate(self, license_id: str, device_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM activations WHERE license_id = ? AND device_id = ?", (license_id, device_id))
