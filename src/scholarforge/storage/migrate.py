"""Forward-only schema migrations tracked with ``PRAGMA user_version``.

Each migration runs inside one transaction together with the version bump, so a
failure leaves the database exactly at the previous version. Before migrating an
existing database a backup copy is written next to it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from scholarforge.errors import MigrationError
from scholarforge.logging_setup import get_logger
from scholarforge.storage.database import Database

log = get_logger("migrate")

_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations(package: str = "scholarforge.storage.migrations") -> list[Migration]:
    found: list[Migration] = []
    for entry in resources.files(package).iterdir():
        m = _NAME_RE.match(entry.name)
        if not m:
            continue
        found.append(Migration(int(m.group(1)), m.group(2), entry.read_text(encoding="utf-8")))
    found.sort(key=lambda mg: mg.version)
    for expected, mg in enumerate(found, start=1):
        if mg.version != expected:
            raise MigrationError(f"migration sequence broken at {mg.version:04d}_{mg.name} (expected {expected:04d})")
    return found


def latest_version(migrations: list[Migration] | None = None) -> int:
    migs = migrations if migrations is not None else load_migrations()
    return migs[-1].version if migs else 0


def migrate(db: Database, migrations: list[Migration] | None = None, *, backup: bool = True) -> list[int]:
    """Apply pending migrations; return the list of applied versions."""
    migs = migrations if migrations is not None else load_migrations()
    current = db.user_version()
    newest = migs[-1].version if migs else 0
    if current > newest:
        raise MigrationError(
            f"database {db.path.name} has schema v{current}, newer than this ScholarForge (v{newest})",
            hint="upgrade ScholarForge; never open a newer project with an older engine",
        )
    pending = [m for m in migs if m.version > current]
    if not pending:
        return []
    if backup and current > 0 and db.path.exists():
        bak = db.path.with_name(f"{db.path.name}.bak-v{current}")
        db.backup_to(bak)
        log.info("backed up %s before migrating to v%d", db.path.name, pending[-1].version)
    applied: list[int] = []
    conn = db.conn
    for mg in pending:
        script = f"BEGIN IMMEDIATE;\n{mg.sql}\nPRAGMA user_version = {mg.version};\nCOMMIT;\n"
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise MigrationError(f"migration {mg.version:04d}_{mg.name} failed: {exc}") from exc
        applied.append(mg.version)
        log.info("applied migration %04d_%s to %s", mg.version, mg.name, db.path.name)
    return applied


def open_database(path: Path) -> Database:
    db = Database(path)
    migrate(db)
    return db
