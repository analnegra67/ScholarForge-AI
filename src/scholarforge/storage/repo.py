"""Repositories over the project database. All SQL lives here."""

from __future__ import annotations

import json
from typing import Any, Iterable

from scholarforge.errors import InvalidInput, NotFound, TaskNotFound
from scholarforge.models.entities import (
    CheckRecord,
    Claim,
    Figure,
    Issue,
    Node,
    Notation,
    Source,
    Task,
    Term,
    row_to_dict,
)
from scholarforge.models.enums import (
    SEVERITY_RANK,
    CheckOutcome,
    ClaimStatus,
    FigureStatus,
    IssueStatus,
    NodeKind,
    NodeStatus,
    Severity,
    SourceStatus,
    TaskActor,
    TaskStatus,
)
from scholarforge.storage.database import Database
from scholarforge.util.ids import new_id, token
from scholarforge.util.text import normalize_for_match
from scholarforge.util.timeutil import iso_in, now_iso
from scholarforge.version import __version__


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class _Repo:
    def __init__(self, db: Database):
        self.db = db


class MetaRepo(_Repo):
    def get(self, key: str, default: str | None = None) -> str | None:
        value = self.db.scalar("SELECT value FROM meta WHERE key = ?", (key,))
        return default if value is None else value

    def set(self, key: str, value: str) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get(key)
        return default if raw is None else json.loads(raw)

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, _j(value))


class EventRepo(_Repo):
    def log(self, kind: str, entity: str | None = None, entity_id: str | None = None, data: Any = None) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO events(ts, kind, entity, entity_id, data) VALUES (?, ?, ?, ?, ?)",
                (now_iso(), kind, entity, entity_id, _j(data) if data is not None else None),
            )

    def list(self, *, entity: str | None = None, entity_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM events"
        params: list[Any] = []
        where = []
        if entity:
            where.append("entity = ?")
            params.append(entity)
        if entity_id:
            where.append("entity_id = ?")
            params.append(entity_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        out = []
        for row in self.db.query(sql, tuple(params)):
            d = row_to_dict(row) or {}
            if d.get("data"):
                try:
                    d["data"] = json.loads(d["data"])
                except (TypeError, json.JSONDecodeError):
                    pass
            out.append(d)
        return out


class NodeRepo(_Repo):
    _UPDATABLE = {
        "title", "slug", "summary", "plan", "status", "content_path", "content_hash", "version",
        "word_count", "revision_rounds", "written_summary", "position", "parent_id",
    }

    def create(
        self, *, kind: NodeKind, parent_id: str | None, position: int, title: str, slug: str,
        summary: str | None = None, plan: dict | None = None, node_id: str | None = None,
    ) -> Node:
        nid = node_id or new_id("nd")
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO nodes(id, parent_id, kind, position, title, slug, summary, plan, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (nid, parent_id, kind.value, position, title, slug, summary, _j(plan or {}), NodeStatus.PLANNED.value, ts, ts),
            )
        return self.get(nid)

    def get(self, node_id: str) -> Node:
        node = Node.from_row(self.db.query_one("SELECT * FROM nodes WHERE id = ?", (node_id,)))
        if node is None:
            raise NotFound(f"node not found: {node_id}")
        return node

    def find(self, node_id: str) -> Node | None:
        return Node.from_row(self.db.query_one("SELECT * FROM nodes WHERE id = ?", (node_id,)))

    def children(self, parent_id: str | None) -> list[Node]:
        if parent_id is None:
            rows = self.db.query("SELECT * FROM nodes WHERE parent_id IS NULL ORDER BY position")
        else:
            rows = self.db.query("SELECT * FROM nodes WHERE parent_id = ? ORDER BY position", (parent_id,))
        return [Node.from_row(r) for r in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM nodes") or 0)

    def walk(self) -> list[Node]:
        """All nodes in reading (pre-) order."""
        rows = self.db.query("SELECT * FROM nodes ORDER BY position")
        by_parent: dict[str | None, list[Node]] = {}
        for r in rows:
            n = Node.from_row(r)
            by_parent.setdefault(n.parent_id, []).append(n)
        out: list[Node] = []

        def visit(parent: str | None) -> None:
            for child in by_parent.get(parent, []):
                out.append(child)
                visit(child.id)

        visit(None)
        return out

    def blocks(self) -> list[Node]:
        return [n for n in self.walk() if n.kind == NodeKind.BLOCK]

    def chapters(self) -> list[Node]:
        return [n for n in self.walk() if n.kind == NodeKind.CHAPTER]

    def ancestors(self, node_id: str) -> list[Node]:
        out: list[Node] = []
        current = self.get(node_id)
        while current.parent_id:
            current = self.get(current.parent_id)
            out.append(current)
        return out

    def ancestor_of_kind(self, node_id: str, kind: NodeKind) -> Node | None:
        for a in self.ancestors(node_id):
            if a.kind == kind:
                return a
        return None

    def descendants(self, node_id: str) -> list[Node]:
        out: list[Node] = []
        for child in self.children(node_id):
            out.append(child)
            out.extend(self.descendants(child.id))
        return out

    def update(self, node_id: str, **fields: Any) -> Node:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise InvalidInput(f"cannot update node fields: {sorted(unknown)}")
        if not fields:
            return self.get(node_id)
        sets, params = [], []
        for key, value in fields.items():
            sets.append(f"{key} = ?")
            if key == "plan":
                value = _j(value)
            elif hasattr(value, "value"):
                value = value.value
            params.append(value)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(node_id)
        with self.db.transaction() as c:
            cur = c.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", tuple(params))
            if cur.rowcount == 0:
                raise NotFound(f"node not found: {node_id}")
        return self.get(node_id)

    def delete(self, node_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM nodes WHERE id = ?", (node_id,))

    def status_counts(self, kind: NodeKind = NodeKind.BLOCK) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM nodes WHERE kind = ? GROUP BY status", (kind.value,))
        return {r["status"]: r["n"] for r in rows}

    def add_version(self, node_id: str, version: int, content_hash: str, path: str, origin: str, note: str | None) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO block_versions(node_id, version, content_hash, path, origin, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (node_id, version, content_hash, path, origin, note, now_iso()),
            )

    def versions(self, node_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM block_versions WHERE node_id = ? ORDER BY version", (node_id,))]


class TaskRepo(_Repo):
    def create(
        self, *, kind: str, actor: TaskActor, title: str = "", target_id: str | None = None,
        payload: dict | None = None, depends_on: Iterable[str] = (), priority: int = 100,
        max_attempts: int = 3, status: TaskStatus = TaskStatus.PENDING,
    ) -> Task:
        tid = new_id("tsk")
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO tasks(id, kind, actor, status, title, priority, target_id, payload, depends_on,"
                " max_attempts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, kind, actor.value, status.value, title, priority, target_id, _j(payload or {}),
                 _j(list(depends_on)), max_attempts, ts, ts),
            )
        return self.get(tid)

    def get(self, task_id: str) -> Task:
        task = Task.from_row(self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)))
        if task is None:
            raise TaskNotFound(f"task not found: {task_id}")
        return task

    def list(
        self, *, status: TaskStatus | Iterable[TaskStatus] | None = None, actor: TaskActor | None = None,
        kind: str | None = None, target_id: str | None = None, limit: int = 1000,
    ) -> list[Task]:
        where, params = [], []
        if status is not None:
            statuses = [status] if isinstance(status, TaskStatus) else list(status)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params += [s.value for s in statuses]
        if actor is not None:
            where.append("actor = ?")
            params.append(actor.value)
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        if target_id is not None:
            where.append("target_id = ?")
            params.append(target_id)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY priority, seq LIMIT ?"
        params.append(limit)
        return [Task.from_row(r) for r in self.db.query(sql, tuple(params))]

    def open_for(self, kind: str, target_id: str | None) -> Task | None:
        row = self.db.query_one(
            "SELECT * FROM tasks WHERE kind = ? AND IFNULL(target_id,'') = IFNULL(?, '')"
            " AND status IN ('pending','leased','blocked') ORDER BY seq LIMIT 1",
            (kind, target_id),
        )
        return Task.from_row(row)

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.db.query("SELECT actor, status, COUNT(*) AS n FROM tasks GROUP BY actor, status"):
            out.setdefault(r["actor"], {})[r["status"]] = r["n"]
        return out

    def _deps_done(self, task: Task) -> bool:
        if not task.depends_on:
            return True
        marks = ",".join("?" * len(task.depends_on))
        n_done = self.db.scalar(
            f"SELECT COUNT(*) FROM tasks WHERE id IN ({marks}) AND status IN ('done','cancelled')",
            tuple(task.depends_on),
        )
        return int(n_done or 0) == len(task.depends_on)

    def expire_leases(self) -> int:
        with self.db.transaction() as c:
            cur = c.execute(
                "UPDATE tasks SET status = 'pending', lease_token = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status = 'leased' AND lease_expires_at < ?",
                (now_iso(), now_iso()),
            )
            return cur.rowcount

    def release_all_leases(self, actor: TaskActor = TaskActor.HOST) -> int:
        with self.db.transaction() as c:
            cur = c.execute(
                "UPDATE tasks SET status = 'pending', lease_token = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status = 'leased' AND actor = ?",
                (now_iso(), actor.value),
            )
            return cur.rowcount

    def lease_next(self, actor: TaskActor, lease_seconds: int, kinds: Iterable[str] | None = None) -> Task | None:
        """Atomically pick the highest-priority runnable task and lease it."""
        kinds = list(kinds) if kinds else None
        with self.db.transaction() as c:
            sql = "SELECT * FROM tasks WHERE status = 'pending' AND actor = ?"
            params: list[Any] = [actor.value]
            if kinds:
                sql += f" AND kind IN ({','.join('?' * len(kinds))})"
                params += kinds
            sql += " ORDER BY priority, seq"
            for row in c.execute(sql, tuple(params)).fetchall():
                task = Task.from_row(row)
                if not self._deps_done(task):
                    continue
                lease = token()
                c.execute(
                    "UPDATE tasks SET status = 'leased', lease_token = ?, lease_expires_at = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (lease, iso_in(lease_seconds), now_iso(), task.id),
                )
                return self.get(task.id)
        return None

    def current_lease(self, actor: TaskActor = TaskActor.HOST) -> Task | None:
        row = self.db.query_one(
            "SELECT * FROM tasks WHERE status = 'leased' AND actor = ? ORDER BY priority, seq LIMIT 1", (actor.value,)
        )
        return Task.from_row(row)

    def set_status(
        self, task_id: str, status: TaskStatus, *, result: dict | None = None, feedback: list | None = None,
        attempts: int | None = None, clear_lease: bool = True,
    ) -> Task:
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, now_iso()]
        if result is not None:
            sets.append("result = ?")
            params.append(_j(result))
        if feedback is not None:
            sets.append("feedback = ?")
            params.append(_j(feedback))
        if attempts is not None:
            sets.append("attempts = ?")
            params.append(attempts)
        if clear_lease:
            sets += ["lease_token = NULL", "lease_expires_at = NULL"]
        if status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            sets.append("completed_at = ?")
            params.append(now_iso())
        params.append(task_id)
        with self.db.transaction() as c:
            cur = c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", tuple(params))
            if cur.rowcount == 0:
                raise TaskNotFound(f"task not found: {task_id}")
        return self.get(task_id)

    def extend_lease(self, task_id: str, lease_seconds: int) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE tasks SET lease_expires_at = ?, updated_at = ? WHERE id = ? AND status = 'leased'",
                      (iso_in(lease_seconds), now_iso(), task_id))

    def update_payload(self, task_id: str, payload: dict) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE tasks SET payload = ?, updated_at = ? WHERE id = ?", (_j(payload), now_iso(), task_id))

    def cancel_open(self, kind: str | None = None, target_id: str | None = None) -> int:
        where = ["status IN ('pending','leased','blocked')"]
        params: list[Any] = [now_iso(), now_iso()]
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if target_id:
            where.append("target_id = ?")
            params.append(target_id)
        with self.db.transaction() as c:
            cur = c.execute(
                "UPDATE tasks SET status = 'cancelled', updated_at = ?, completed_at = ?, lease_token = NULL,"
                f" lease_expires_at = NULL WHERE {' AND '.join(where)}",
                tuple(params),
            )
            return cur.rowcount


class SourceRepo(_Repo):
    _FIELDS = (
        "citekey", "kind", "title", "authors", "year", "venue", "volume", "issue", "pages", "publisher", "doi",
        "arxiv_id", "isbn", "url", "openalex_id", "status", "verification", "abstract", "relevance", "proposed_by",
    )

    def add(self, **fields: Any) -> Source:
        sid = new_id("src")
        ts = now_iso()
        data = {k: fields.get(k) for k in self._FIELDS}
        data["authors"] = _j(data.get("authors") or [])
        data["verification"] = _j(data.get("verification") or {})
        data["status"] = (data.get("status") or SourceStatus.PROPOSED)
        if hasattr(data["status"], "value"):
            data["status"] = data["status"].value
        data["kind"] = data.get("kind") or "article"
        data["proposed_by"] = data.get("proposed_by") or "host"
        cols = ["id", *self._FIELDS, "created_at", "updated_at"]
        values = [sid, *[data[k] for k in self._FIELDS], ts, ts]
        with self.db.transaction() as c:
            c.execute(f"INSERT INTO sources({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(values))
        return self.get(sid)

    def get(self, source_id: str) -> Source:
        src = Source.from_row(self.db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,)))
        if src is None:
            raise NotFound(f"source not found: {source_id}")
        return src

    def by_citekey(self, citekey: str) -> Source | None:
        return Source.from_row(self.db.query_one("SELECT * FROM sources WHERE citekey = ?", (citekey,)))

    def by_identifier(self, *, doi: str | None = None, arxiv_id: str | None = None, isbn: str | None = None) -> Source | None:
        if doi:
            row = self.db.query_one("SELECT * FROM sources WHERE lower(doi) = lower(?)", (doi,))
            if row:
                return Source.from_row(row)
        if arxiv_id:
            row = self.db.query_one("SELECT * FROM sources WHERE arxiv_id = ?", (arxiv_id,))
            if row:
                return Source.from_row(row)
        if isbn:
            row = self.db.query_one("SELECT * FROM sources WHERE isbn = ?", (isbn,))
            if row:
                return Source.from_row(row)
        return None

    def list(self, status: SourceStatus | Iterable[SourceStatus] | None = None) -> list[Source]:
        if status is None:
            rows = self.db.query("SELECT * FROM sources ORDER BY citekey")
        else:
            statuses = [status] if isinstance(status, SourceStatus) else list(status)
            rows = self.db.query(
                f"SELECT * FROM sources WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY citekey",
                tuple(s.value for s in statuses),
            )
        return [Source.from_row(r) for r in rows]

    def update(self, source_id: str, **fields: Any) -> Source:
        unknown = set(fields) - set(self._FIELDS)
        if unknown:
            raise InvalidInput(f"cannot update source fields: {sorted(unknown)}")
        sets, params = [], []
        for key, value in fields.items():
            if key in ("authors", "verification"):
                value = _j(value)
            elif hasattr(value, "value"):
                value = value.value
            sets.append(f"{key} = ?")
            params.append(value)
        sets.append("updated_at = ?")
        params += [now_iso(), source_id]
        with self.db.transaction() as c:
            c.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return self.get(source_id)

    def citekeys(self) -> set[str]:
        return {r[0] for r in self.db.query("SELECT citekey FROM sources")}


class ClaimRepo(_Repo):
    def replace_for_node(self, node_id: str, claims: list[dict]) -> list[Claim]:
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute("DELETE FROM claims WHERE node_id = ?", (node_id,))
            for cl in claims:
                c.execute(
                    "INSERT INTO claims(id, node_id, label, kind, text, citations, check_ids, speculative, status,"
                    " status_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id("clm"), node_id, cl.get("label"), cl["kind"], cl["text"], _j(cl.get("citations", [])),
                     _j(cl.get("check_ids", [])), int(bool(cl.get("speculative"))), ClaimStatus.UNVERIFIED.value,
                     None, ts, ts),
                )
        return self.for_node(node_id)

    def for_node(self, node_id: str) -> list[Claim]:
        return [Claim.from_row(r) for r in self.db.query("SELECT * FROM claims WHERE node_id = ? ORDER BY rowid", (node_id,))]

    def get(self, claim_id: str) -> Claim:
        cl = Claim.from_row(self.db.query_one("SELECT * FROM claims WHERE id = ?", (claim_id,)))
        if cl is None:
            raise NotFound(f"claim not found: {claim_id}")
        return cl

    def all(self) -> list[Claim]:
        return [Claim.from_row(r) for r in self.db.query("SELECT * FROM claims ORDER BY rowid")]

    def set_status(self, claim_id: str, status: ClaimStatus, reason: str | None) -> None:
        with self.db.transaction() as c:
            c.execute(
                "UPDATE claims SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
                (status.value, reason, now_iso(), claim_id),
            )

    def status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.query("SELECT status, COUNT(*) AS n FROM claims GROUP BY status")}


class AssertionRepo(_Repo):
    def replace_for_node(self, node_id: str, specs: list[dict]) -> None:
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute("DELETE FROM assertions WHERE node_id = ?", (node_id,))
            for spec in specs:
                c.execute(
                    "INSERT INTO assertions(id, node_id, local_id, spec, last_outcome, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id("asr"), node_id, spec["id"], _j(spec), spec.get("_outcome"), ts),
                )

    def for_node(self, node_id: str) -> list[dict]:
        out = []
        for r in self.db.query("SELECT * FROM assertions WHERE node_id = ? ORDER BY rowid", (node_id,)):
            d = dict(r)
            d["spec"] = json.loads(d["spec"])
            out.append(d)
        return out

    def all(self) -> list[dict]:
        out = []
        for r in self.db.query("SELECT * FROM assertions ORDER BY rowid"):
            d = dict(r)
            d["spec"] = json.loads(d["spec"])
            out.append(d)
        return out

    def set_outcome(self, node_id: str, local_id: str, outcome: str) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE assertions SET last_outcome = ? WHERE node_id = ? AND local_id = ?", (outcome, node_id, local_id))


class CheckRepo(_Repo):
    def add(
        self, *, subject_type: str, subject_id: str, check_type: str, method: str, independent: bool,
        outcome: CheckOutcome, details: dict | None = None,
    ) -> CheckRecord:
        cid = new_id("chk")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO checks(id, subject_type, subject_id, check_type, method, independent, outcome, details,"
                " engine_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, subject_type, subject_id, check_type, method, int(independent), outcome.value, _j(details or {}),
                 __version__, now_iso()),
            )
        return CheckRecord.from_row(self.db.query_one("SELECT * FROM checks WHERE id = ?", (cid,)))

    def for_subject(self, subject_type: str, subject_id: str) -> list[CheckRecord]:
        rows = self.db.query(
            "SELECT * FROM checks WHERE subject_type = ? AND subject_id = ? ORDER BY created_at", (subject_type, subject_id)
        )
        return [CheckRecord.from_row(r) for r in rows]

    def latest(self, subject_type: str, subject_id: str, check_type: str) -> CheckRecord | None:
        row = self.db.query_one(
            "SELECT * FROM checks WHERE subject_type = ? AND subject_id = ? AND check_type = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (subject_type, subject_id, check_type),
        )
        return CheckRecord.from_row(row)

    def all(self) -> list[CheckRecord]:
        return [CheckRecord.from_row(r) for r in self.db.query("SELECT * FROM checks ORDER BY created_at")]

    def outcome_counts(self) -> dict[str, int]:
        return {r["outcome"]: r["n"] for r in self.db.query("SELECT outcome, COUNT(*) AS n FROM checks GROUP BY outcome")}


class TermRepo(_Repo):
    def get(self, term: str) -> Term | None:
        return Term.from_row(self.db.query_one("SELECT * FROM terms WHERE normalized = ?", (normalize_for_match(term),)))

    def by_id(self, term_id: str) -> Term:
        t = Term.from_row(self.db.query_one("SELECT * FROM terms WHERE id = ?", (term_id,)))
        if t is None:
            raise NotFound(f"term not found: {term_id}")
        return t

    def add(self, term: str, definition: str, defined_in: str | None, aliases: list[str]) -> Term:
        tid = new_id("trm")
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO terms(id, term, normalized, definition, defined_in, aliases, version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (tid, term.strip(), normalize_for_match(term), definition.strip(), defined_in, _j(aliases), ts, ts),
            )
            c.execute(
                "INSERT INTO term_history(term_id, version, definition, changed_in, created_at) VALUES (?, 1, ?, ?, ?)",
                (tid, definition.strip(), defined_in, ts),
            )
        return self.by_id(tid)

    def redefine(self, term_id: str, definition: str, changed_in: str | None) -> Term:
        current = self.by_id(term_id)
        ts = now_iso()
        with self.db.transaction() as c:
            c.execute(
                "UPDATE terms SET definition = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (definition.strip(), ts, term_id),
            )
            c.execute(
                "INSERT INTO term_history(term_id, version, definition, changed_in, created_at) VALUES (?, ?, ?, ?, ?)",
                (term_id, current.version + 1, definition.strip(), changed_in, ts),
            )
        return self.by_id(term_id)

    def history(self, term_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM term_history WHERE term_id = ? ORDER BY version", (term_id,))]

    def list(self) -> list[Term]:
        return [Term.from_row(r) for r in self.db.query("SELECT * FROM terms ORDER BY normalized")]

    def set_usage(self, node_id: str, term_ids: Iterable[str]) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM term_usage WHERE node_id = ?", (node_id,))
            for tid in set(term_ids):
                c.execute("INSERT OR IGNORE INTO term_usage(term_id, node_id) VALUES (?, ?)", (tid, node_id))

    def users_of(self, term_id: str) -> list[str]:
        return [r[0] for r in self.db.query("SELECT node_id FROM term_usage WHERE term_id = ?", (term_id,))]


class NotationRepo(_Repo):
    def add(self, symbol: str, meaning: str, scope: str, defined_in: str | None) -> Notation:
        nid = new_id("ntn")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO notation(id, symbol, normalized, meaning, scope, defined_in, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nid, symbol.strip(), normalize_symbol(symbol), meaning.strip(), scope, defined_in, now_iso()),
            )
        return Notation.from_row(self.db.query_one("SELECT * FROM notation WHERE id = ?", (nid,)))

    def by_symbol(self, symbol: str) -> list[Notation]:
        rows = self.db.query("SELECT * FROM notation WHERE normalized = ? ORDER BY created_at", (normalize_symbol(symbol),))
        return [Notation.from_row(r) for r in rows]

    def list(self) -> list[Notation]:
        return [Notation.from_row(r) for r in self.db.query("SELECT * FROM notation ORDER BY created_at")]

    def delete_for_node(self, node_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM notation WHERE defined_in = ?", (node_id,))


def normalize_symbol(symbol: str) -> str:
    """Canonical form of a LaTeX symbol for notation-conflict detection."""
    s = symbol.strip().strip("$").strip()
    s = s.replace("\\boldsymbol", "\\mathbf").replace("\\bm", "\\mathbf")
    return "".join(s.split())


class LabelRepo(_Repo):
    def define(self, label: str, kind: str, node_id: str | None, title: str | None) -> None:
        with self.db.transaction() as c:
            row = c.execute("SELECT node_id FROM labels WHERE label = ?", (label,)).fetchone()
            if row is not None and row[0] != node_id:
                raise InvalidInput(f"duplicate cross-reference label '{label}' (already defined elsewhere)",
                                   details={"label": label, "defined_in": row[0]})
            c.execute(
                "INSERT INTO labels(label, kind, node_id, title) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(label) DO UPDATE SET kind = excluded.kind, title = excluded.title",
                (label, kind, node_id, title),
            )

    def owner(self, label: str) -> str | None:
        row = self.db.query_one("SELECT node_id FROM labels WHERE label = ?", (label,))
        return None if row is None else (row[0] or "")

    def remove_for_node(self, node_id: str, keep_kinds: Iterable[str] = ("sec",)) -> None:
        keep = list(keep_kinds)
        with self.db.transaction() as c:
            if keep:
                c.execute(
                    f"DELETE FROM labels WHERE node_id = ? AND kind NOT IN ({','.join('?' * len(keep))})",
                    (node_id, *keep),
                )
            else:
                c.execute("DELETE FROM labels WHERE node_id = ?", (node_id,))

    def all(self) -> dict[str, dict]:
        return {r["label"]: dict(r) for r in self.db.query("SELECT * FROM labels")}

    def set_refs(self, node_id: str, labels: Iterable[str]) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM refs WHERE node_id = ?", (node_id,))
            for label in set(labels):
                c.execute("INSERT OR IGNORE INTO refs(node_id, label) VALUES (?, ?)", (node_id, label))

    def refs_of(self, node_id: str) -> list[str]:
        return [r[0] for r in self.db.query("SELECT label FROM refs WHERE node_id = ? ORDER BY label", (node_id,))]

    def all_refs(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.db.query("SELECT node_id, label FROM refs")]

    def unresolved(self) -> list[tuple[str, str]]:
        rows = self.db.query("SELECT r.node_id, r.label FROM refs r LEFT JOIN labels l ON l.label = r.label WHERE l.label IS NULL")
        return [(r[0], r[1]) for r in rows]


class IssueRepo(_Repo):
    def add(
        self, *, severity: Severity, category: str, code: str, message: str, node_id: str | None = None,
        details: dict | None = None, origin: str = "engine",
    ) -> Issue:
        # de-duplicate identical open issues
        existing = self.db.query_one(
            "SELECT * FROM issues WHERE IFNULL(node_id,'') = IFNULL(?,'') AND code = ? AND message = ? AND status = 'open'",
            (node_id, code, message),
        )
        if existing:
            return Issue.from_row(existing)
        iid = new_id("iss")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO issues(id, node_id, severity, category, code, message, details, status, origin, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (iid, node_id, severity.value, category, code, message, _j(details or {}), origin, now_iso()),
            )
        return Issue.from_row(self.db.query_one("SELECT * FROM issues WHERE id = ?", (iid,)))

    def list(
        self, *, node_id: str | None = None, status: IssueStatus | None = IssueStatus.OPEN,
        min_severity: Severity = Severity.INFO, category: str | None = None,
    ) -> list[Issue]:
        where, params = [], []
        if node_id is not None:
            where.append("node_id = ?")
            params.append(node_id)
        if status is not None:
            where.append("status = ?")
            params.append(status.value)
        if category is not None:
            where.append("category = ?")
            params.append(category)
        sql = "SELECT * FROM issues"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at"
        issues = [Issue.from_row(r) for r in self.db.query(sql, tuple(params))]
        return [i for i in issues if SEVERITY_RANK[i.severity] >= SEVERITY_RANK[min_severity]]

    def get(self, issue_id: str) -> Issue:
        iss = Issue.from_row(self.db.query_one("SELECT * FROM issues WHERE id = ?", (issue_id,)))
        if iss is None:
            raise NotFound(f"issue not found: {issue_id}")
        return iss

    def set_status(self, issue_ids: Iterable[str], status: IssueStatus) -> int:
        ids = list(issue_ids)
        if not ids:
            return 0
        with self.db.transaction() as c:
            cur = c.execute(
                f"UPDATE issues SET status = ?, resolved_at = ? WHERE id IN ({','.join('?' * len(ids))})",
                (status.value, now_iso(), *ids),
            )
            return cur.rowcount

    def resolve_for_node(self, node_id: str, categories: Iterable[str] | None = None, origin: str | None = None) -> int:
        where = ["node_id = ?", "status = 'open'"]
        params: list[Any] = [now_iso(), node_id]
        cats = list(categories) if categories else []
        if cats:
            where.append(f"category IN ({','.join('?' * len(cats))})")
            params += cats
        if origin:
            where.append("origin = ?")
            params.append(origin)
        with self.db.transaction() as c:
            cur = c.execute(
                f"UPDATE issues SET status = 'resolved', resolved_at = ? WHERE {' AND '.join(where)}", tuple(params)
            )
            return cur.rowcount

    def counts(self) -> dict[str, int]:
        rows = self.db.query("SELECT severity, COUNT(*) AS n FROM issues WHERE status = 'open' GROUP BY severity")
        return {r["severity"]: r["n"] for r in rows}


class FigureRepo(_Repo):
    def upsert(
        self, *, label: str, node_id: str | None, mode: str, renderer: str, spec: dict, caption: str, alt_text: str,
        license: str | None = None,
    ) -> Figure:
        ts = now_iso()
        existing = self.by_label(label)
        with self.db.transaction() as c:
            if existing is None:
                fid = new_id("fig")
                c.execute(
                    "INSERT INTO figures(id, node_id, label, mode, renderer, spec, caption, alt_text, status, license,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?, ?)",
                    (fid, node_id, label, mode, renderer, _j(spec), caption, alt_text, license, ts, ts),
                )
            else:
                if existing.node_id and node_id and existing.node_id != node_id:
                    raise InvalidInput(f"figure label '{label}' already belongs to another block",
                                       details={"label": label})
                fid = existing.id
                c.execute(
                    "UPDATE figures SET node_id = ?, mode = ?, renderer = ?, spec = ?, caption = ?, alt_text = ?,"
                    " status = 'requested', license = ?, updated_at = ? WHERE id = ?",
                    (node_id, mode, renderer, _j(spec), caption, alt_text, license, ts, fid),
                )
        return self.get(fid)

    def get(self, figure_id: str) -> Figure:
        fig = Figure.from_row(self.db.query_one("SELECT * FROM figures WHERE id = ?", (figure_id,)))
        if fig is None:
            raise NotFound(f"figure not found: {figure_id}")
        return fig

    def by_label(self, label: str) -> Figure | None:
        return Figure.from_row(self.db.query_one("SELECT * FROM figures WHERE label = ?", (label,)))

    def list(self, node_id: str | None = None) -> list[Figure]:
        if node_id:
            rows = self.db.query("SELECT * FROM figures WHERE node_id = ? ORDER BY created_at", (node_id,))
        else:
            rows = self.db.query("SELECT * FROM figures ORDER BY created_at")
        return [Figure.from_row(r) for r in rows]

    def update(self, figure_id: str, **fields: Any) -> Figure:
        allowed = {"status", "outputs", "provenance", "qc", "caption", "alt_text", "spec", "license", "renderer", "mode"}
        unknown = set(fields) - allowed
        if unknown:
            raise InvalidInput(f"cannot update figure fields: {sorted(unknown)}")
        sets, params = [], []
        for key, value in fields.items():
            if key in ("outputs", "provenance", "qc", "spec"):
                value = _j(value)
            elif isinstance(value, FigureStatus):
                value = value.value
            sets.append(f"{key} = ?")
            params.append(value)
        sets.append("updated_at = ?")
        params += [now_iso(), figure_id]
        with self.db.transaction() as c:
            c.execute(f"UPDATE figures SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return self.get(figure_id)


class BuildRepo(_Repo):
    def start(self, formats: list[str]) -> str:
        bid = new_id("bld")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO builds(id, started_at, formats, status) VALUES (?, ?, ?, 'running')",
                (bid, now_iso(), _j(formats)),
            )
        return bid

    def finish(self, build_id: str, status: str, outputs: dict, report: dict) -> None:
        with self.db.transaction() as c:
            c.execute(
                "UPDATE builds SET finished_at = ?, status = ?, outputs = ?, report = ? WHERE id = ?",
                (now_iso(), status, _j(outputs), _j(report), build_id),
            )

    def latest(self) -> dict | None:
        return row_to_dict(self.db.query_one("SELECT * FROM builds ORDER BY started_at DESC LIMIT 1"))

    def list(self, limit: int = 20) -> list[dict]:
        return [row_to_dict(r) for r in self.db.query("SELECT * FROM builds ORDER BY started_at DESC LIMIT ?", (limit,))]


class CheckpointRepo(_Repo):
    def add(self, checkpoint_id: str, reason: str, auto: bool, path: str, manifest: dict) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO checkpoints(id, created_at, reason, auto, path, manifest) VALUES (?, ?, ?, ?, ?, ?)",
                (checkpoint_id, now_iso(), reason, int(auto), path, _j(manifest)),
            )

    def list(self) -> list[dict]:
        return [row_to_dict(r) for r in self.db.query("SELECT * FROM checkpoints ORDER BY created_at DESC, rowid DESC")]

    def get(self, checkpoint_id: str) -> dict:
        row = row_to_dict(self.db.query_one("SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)))
        if row is None:
            raise NotFound(f"checkpoint not found: {checkpoint_id}")
        return row

    def delete(self, checkpoint_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))


class ProjectDB:
    """Facade bundling all repositories of one project database."""

    def __init__(self, db: Database):
        self.db = db
        self.meta = MetaRepo(db)
        self.events = EventRepo(db)
        self.nodes = NodeRepo(db)
        self.tasks = TaskRepo(db)
        self.sources = SourceRepo(db)
        self.claims = ClaimRepo(db)
        self.assertions = AssertionRepo(db)
        self.checks = CheckRepo(db)
        self.terms = TermRepo(db)
        self.notation = NotationRepo(db)
        self.labels = LabelRepo(db)
        self.issues = IssueRepo(db)
        self.figures = FigureRepo(db)
        self.builds = BuildRepo(db)
        self.checkpoints = CheckpointRepo(db)

    def close(self) -> None:
        self.db.close()
