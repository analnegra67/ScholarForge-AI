"""Workflow coordinator: the host-AI protocol.

The protocol (ScholarForge never calls a model itself)::

    host  -> next_task(project)            # engine first runs due local jobs (bounded time)
    engine-> {task: kind, instructions, context, output_schema, lease_token}
    host  -> (reasons / writes / researches with its own capabilities)
    host  -> submit_task(project, task_id, lease_token, result)
    engine-> validates, stores, checks; accepts or returns concrete feedback
    ... repeat until the project is ready to build ...

Task kinds are implemented by handlers registered in :mod:`scholarforge.workflow`.
Each handler renders a task for the host and validates/applies its result (host
tasks), or executes it locally (engine tasks). Everything is persisted, so the
loop can stop at any moment and resume in a new session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from scholarforge.core.project import Project
from scholarforge.core.tasks import TaskQueue
from scholarforge.errors import ScholarForgeError, WorkflowError
from scholarforge.logging_setup import get_logger
from scholarforge.models.entities import Task
from scholarforge.models.enums import NodeKind, NodeStatus, TaskActor, TaskStatus

log = get_logger("workflow")

PHASES = ("research", "architecture", "drafting", "review", "ready_to_build", "built")


@dataclass
class SubmissionOutcome:
    accepted: bool
    feedback: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    stored_result: dict[str, Any] | None = None


@dataclass
class EngineOutcome:
    done: bool = True
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    retry_later: bool = False  # e.g. network unavailable


class TaskHandler(Protocol):
    kind: str
    actor: TaskActor

    def render(self, wf: "Coordinator", task: Task) -> dict[str, Any]: ...

    def submit(self, wf: "Coordinator", task: Task, result: dict[str, Any]) -> SubmissionOutcome: ...

    def run(self, wf: "Coordinator", task: Task) -> EngineOutcome: ...

    def on_exhausted(self, wf: "Coordinator", task: Task, result: dict[str, Any]) -> SubmissionOutcome | None: ...


def _registry() -> dict[str, TaskHandler]:
    from scholarforge.workflow import HANDLERS

    return HANDLERS


class Coordinator:
    def __init__(self, project: Project):
        self.project = project
        self.pdb = project.db
        wf = project.settings.workflow
        self.queue = TaskQueue(project.db, lease_minutes=wf.lease_minutes, default_max_attempts=wf.max_attempts)

    # ------------------------------------------------------------------------------------ setup
    def initialize(self) -> Task:
        from scholarforge.profiles.registry import get_registry

        params = self.project.config.params
        registry = get_registry()
        if params.profile and params.profile in registry.ids():
            profile_id, ranking = params.profile, []
        else:
            profile_id, ranking = registry.match(" ".join([params.topic, params.title or "", params.request or "",
                                                           *params.required_topics]))
        self.pdb.meta.set("profile_id", profile_id)
        self.pdb.meta.set_json("profile_ranking", ranking)
        task = self.queue.enqueue(
            "research.plan", title="Research plan, concept map and candidate sources", priority=10
        )
        self.pdb.meta.set("phase", "research")
        self.project.checkpoints.create("project created")
        return task

    def handler(self, kind: str) -> TaskHandler:
        registry = _registry()
        if kind not in registry:
            raise WorkflowError(f"no handler registered for task kind '{kind}'")
        return registry[kind]

    # ------------------------------------------------------------------------------- engine jobs
    def run_engine_jobs(self, budget_s: float | None = None) -> list[dict[str, Any]]:
        """Run pending local jobs until the time budget is used; return a log of what ran."""
        budget = self.project.settings.workflow.engine_time_budget_s if budget_s is None else budget_s
        deadline = time.monotonic() + budget
        ran: list[dict[str, Any]] = []
        deferred: set[str] = set()
        while time.monotonic() < deadline:
            candidates = [t for t in self.pdb.tasks.list(status=TaskStatus.PENDING, actor=TaskActor.ENGINE)
                          if t.id not in deferred]
            task = None
            for cand in candidates:
                if self.pdb.tasks._deps_done(cand):  # noqa: SLF001 - repository helper
                    task = cand
                    break
            if task is None:
                break
            leased = self.pdb.tasks.lease_next(TaskActor.ENGINE, 600, kinds=[task.kind])
            if leased is None:
                break
            try:
                outcome = self.handler(leased.kind).run(self, leased)
            except ScholarForgeError as exc:
                log.warning("engine task %s (%s) failed: %s", leased.id, leased.kind, exc)
                self.queue.reject(leased.id, [{"code": exc.code, "message": exc.message}])
                ran.append({"task": leased.id, "kind": leased.kind, "ok": False, "error": exc.message})
                continue
            except Exception as exc:  # unexpected bug: keep the project usable, record it
                log.exception("engine task %s (%s) crashed", leased.id, leased.kind)
                self.queue.reject(leased.id, [{"code": "ENGINE_CRASH", "message": f"{type(exc).__name__}: {exc}"}])
                ran.append({"task": leased.id, "kind": leased.kind, "ok": False, "error": type(exc).__name__})
                continue
            if outcome.retry_later:
                self.pdb.tasks.set_status(leased.id, TaskStatus.PENDING)
                deferred.add(leased.id)
                ran.append({"task": leased.id, "kind": leased.kind, "ok": False, "deferred": True,
                            "summary": outcome.summary})
                continue
            self.queue.complete(leased.id, outcome.data)
            ran.append({"task": leased.id, "kind": leased.kind, "ok": True, "summary": outcome.summary})
        return ran

    # ------------------------------------------------------------------------------- host loop
    def next_task(self, *, run_engine: bool = True) -> dict[str, Any]:
        engine_log = self.run_engine_jobs() if run_engine else []
        task = self.queue.current(TaskActor.HOST) or self.queue.lease_next(TaskActor.HOST)
        if task is not None:
            view = self.render(task)
            return {"status": "task", "task": view, "engine_log": engine_log}
        blocked = self.pdb.tasks.list(status=TaskStatus.BLOCKED)
        engine_pending = self.pdb.tasks.list(status=[TaskStatus.PENDING, TaskStatus.LEASED], actor=TaskActor.ENGINE)
        phase = self.phase()
        response: dict[str, Any] = {"status": "idle", "phase": phase, "engine_log": engine_log}
        if blocked:
            response["status"] = "waiting_for_user"
            response["blocked"] = [
                {"task_id": t.id, "kind": t.kind, "title": t.title, "reason": t.payload.get("blocked_reason"),
                 "details": {k: v for k, v in t.payload.items() if k not in ("blocked_reason",)}}
                for t in blocked
            ]
        elif engine_pending:
            response["status"] = "engine_busy"
            response["message"] = (
                f"{len(engine_pending)} local job(s) still pending (e.g. source verification or rendering); "
                "call next_task again to continue them"
            )
        elif phase == "ready_to_build":
            response["status"] = "ready_to_build"
            response["message"] = "All planned content is written and checked. Call build_book to produce outputs."
        elif phase == "built":
            response["status"] = "complete"
            response["message"] = "The book has been built. Rebuild after edits, or review the verification report."
        return response

    def render(self, task: Task) -> dict[str, Any]:
        handler = self.handler(task.kind)
        body = handler.render(self, task)
        view = {
            "task_id": task.id,
            "kind": task.kind,
            "title": task.title,
            "lease_token": task.lease_token,
            "lease_expires_at": task.lease_expires_at,
            "attempt": task.attempts + 1,
            "max_attempts": task.max_attempts,
        }
        if task.feedback:
            view["previous_feedback"] = task.feedback
        view.update(body)
        return view

    def submit_task(self, task_id: str, lease_token: str | None, result: dict[str, Any]) -> dict[str, Any]:
        task = self.queue.verify_lease(task_id, lease_token)
        handler = self.handler(task.kind)
        outcome = handler.submit(self, task, result)
        if outcome.accepted:
            self.queue.complete(task.id, outcome.stored_result if outcome.stored_result is not None else {"accepted": True})
            self._after_progress()
            return {"accepted": True, "summary": outcome.summary, "feedback": outcome.feedback, **outcome.data}
        updated = self.queue.reject(task.id, outcome.feedback)
        if updated.status == TaskStatus.FAILED:
            fallback = handler.on_exhausted(self, updated, result)
            if fallback is not None:
                self.pdb.tasks.set_status(task.id, TaskStatus.DONE, result=fallback.stored_result or {"accepted": True,
                                                                                                      "forced": True})
                self._after_progress()
                return {"accepted": True, "forced": True, "summary": fallback.summary, "feedback": outcome.feedback,
                        **fallback.data}
            return {"accepted": False, "final": True, "feedback": outcome.feedback,
                    "summary": "attempts exhausted; the task is marked failed and listed in the diagnostics report"}
        return {"accepted": False, "final": False, "feedback": outcome.feedback, "attempts_left":
                updated.max_attempts - updated.attempts, "summary": outcome.summary or "please revise and resubmit"}

    def _after_progress(self) -> None:
        new_phase = self.phase()
        old_phase = self.pdb.meta.get("phase")
        if new_phase != old_phase:
            self.pdb.meta.set("phase", new_phase)
            self.pdb.events.log("project.phase", "project", self.project.id, {"from": old_phase, "to": new_phase})
            self.project.checkpoints.create(f"phase {old_phase} -> {new_phase}")

    # ------------------------------------------------------------------------------ state
    def phase(self) -> str:
        """Derive the phase from stored data (robust after crashes or manual edits)."""
        tasks = self.pdb.tasks
        open_statuses = [TaskStatus.PENDING, TaskStatus.LEASED, TaskStatus.BLOCKED]
        if tasks.list(kind="research.plan", status=open_statuses) or not tasks.list(kind="research.plan",
                                                                                     status=TaskStatus.DONE):
            return "research"
        if not self.pdb.nodes.blocks():
            return "architecture"
        blocks = self.pdb.nodes.blocks()
        if any(b.status == NodeStatus.PLANNED for b in blocks):
            return "drafting"
        if self.pdb.tasks.list(status=open_statuses, actor=TaskActor.HOST) or self.pdb.tasks.list(
            status=open_statuses, actor=TaskActor.ENGINE
        ):
            return "review"
        latest = self.pdb.builds.latest()
        if latest and latest.get("status") in ("success", "partial"):
            newest_change = max((b.updated_at for b in blocks), default="")
            if latest.get("finished_at") and latest["finished_at"] >= newest_change:
                return "built"
        return "ready_to_build"

    def status(self) -> dict[str, Any]:
        blocks = self.pdb.nodes.blocks()
        words = sum(b.word_count for b in blocks)
        planned_words = sum(int(b.plan.get("target_words", 0)) for b in blocks)
        return {
            "project_id": self.project.id,
            "title": self.project.config.title,
            "phase": self.phase(),
            "path": str(self.project.root),
            "chapters": len(self.pdb.nodes.chapters()),
            "blocks": {"total": len(blocks), **self.pdb.nodes.status_counts(NodeKind.BLOCK)},
            "words": {"written": words, "planned": planned_words},
            "tasks": self.pdb.tasks.counts(),
            "sources": _count_by(self.pdb.sources.list(), "status"),
            "claims": self.pdb.claims.status_counts(),
            "open_issues": self.pdb.issues.counts(),
            "figures": _count_by(self.pdb.figures.list(), "status"),
            "last_build": self.pdb.builds.latest(),
        }

    def resume(self) -> dict[str, Any]:
        """Session hand-over: release stale leases and summarise where the project stands."""
        released = self.queue.release_host_leases()
        edits = self.project.content.detect_external_edits()
        for edit in edits:
            node = self.pdb.nodes.get(edit.node_id)
            if node.status not in (NodeStatus.PLANNED,):
                self.pdb.nodes.update(node.id, status=NodeStatus.DRAFTED)
                self.queue.enqueue("verify.block", actor=TaskActor.ENGINE, target_id=node.id,
                                   title=f"Re-verify externally edited block '{node.title}'", priority=20)
        status = self.status()
        recent = [e for e in self.pdb.events.list(limit=15)]
        return {
            "status": status,
            "released_leases": released,
            "external_edits": [e.node_id for e in edits],
            "recent_events": [{"ts": e["ts"], "kind": e["kind"], "entity_id": e.get("entity_id")} for e in recent],
            "next_step": "call next_task to continue" if status["phase"] not in ("ready_to_build", "built")
            else "call build_book",
        }


def _count_by(items: list, attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        key = getattr(item, attr)
        key = key.value if hasattr(key, "value") else str(key)
        out[key] = out.get(key, 0) + 1
    return out
