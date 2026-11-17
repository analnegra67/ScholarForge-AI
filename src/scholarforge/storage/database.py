"""SQLite access with explicit, retrying transactions.

* autocommit connection (``isolation_level=None``); writes use ``BEGIN IMMEDIATE`` so
  a writer takes the lock up-front instead of failing half-way;
* WAL journal when the filesystem supports it (falls back to DELETE), ``foreign_keys=ON``;
* ``database is locked`` / ``busy`` errors are retried with backoff — the MCP server and
  the CLI may legitimately touch the same project concurrently;
* one connection per thread (SQLite connections must not be shared across threads).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scholarforge.errors import StorageError
from scholarforge.logging_setup import get_logger

log = get_logger("storage")

_BUSY_RETRIES = 8
_BUSY_BACKOFF_S = 0.1


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


class Database:
    def __init__(self, path: Path, *, wal: bool = True):
        self.path = Path(path)
        self._wal = wal
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- connection management -------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None, check_same_thread=True)
        except sqlite3.Error as exc:
            raise StorageError(f"cannot open database {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        if self._wal:
            try:
                mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    log.warning("WAL journal unavailable for %s (got %s)", self.path.name, mode)
            except sqlite3.OperationalError as exc:  # e.g. network filesystems
                log.warning("cannot enable WAL for %s: %s", self.path.name, exc)
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
            self._local.depth = 0
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # -- transactions -------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Write transaction (re-entrant: nested calls join the outer transaction)."""
        conn = self.conn
        depth = getattr(self._local, "depth", 0)
        if depth > 0:
            self._local.depth = depth + 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        for attempt in range(_BUSY_RETRIES):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if _is_busy(exc) and attempt < _BUSY_RETRIES - 1:
                    time.sleep(_BUSY_BACKOFF_S * (2**attempt))
                    continue
                raise StorageError(f"cannot start transaction on {self.path.name}: {exc}") from exc
        self._local.depth = 1
        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        else:
            try:
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise StorageError(f"commit failed on {self.path.name}: {exc}") from exc
        finally:
            self._local.depth = 0

    # -- helpers ------------------------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        for attempt in range(_BUSY_RETRIES):
            try:
                return self.conn.execute(sql, params)
            except sqlite3.OperationalError as exc:
                if _is_busy(exc) and attempt < _BUSY_RETRIES - 1 and getattr(self._local, "depth", 0) == 0:
                    time.sleep(_BUSY_BACKOFF_S * (2**attempt))
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    def user_version(self) -> int:
        return int(self.scalar("PRAGMA user_version") or 0)

    def backup_to(self, target: Path) -> Path:
        """Consistent online copy using the SQLite backup API."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        if tmp.exists():
            tmp.unlink()
        dest = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
        from scholarforge.util.atomic import replace_with_retry

        replace_with_retry(tmp, target)
        return target

    def integrity_check(self) -> list[str]:
        rows = self.query("PRAGMA integrity_check")
        problems = [r[0] for r in rows if r[0] != "ok"]
        fk = self.query("PRAGMA foreign_key_check")
        problems += [f"foreign key violation: {dict(r)}" for r in fk]
        return problems
