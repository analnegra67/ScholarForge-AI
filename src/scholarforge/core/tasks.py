"""Task queue with leases.

A *lease* gives one host session exclusive ownership of a task for a limited time.
If the session dies (terminal closed, client restarted, model limit hit) the lease
expires and the task returns to ``pending`` — no work is lost because nothing is
written until a result is submitted and validated. A stale submission (lease
re-issued to another session meanwhile) is rejected with ``LeaseConflict`` instead
of silently overwriting newer work.
"""

from __future__ import annotations

from typing import Any, Iterable

from scholarforge.errors import LeaseConflict, WorkflowError
from scholarforge.models.entities import Task
from scholarforge.models.enums import TaskActor, TaskStatus
from scholarforge.storage.repo import ProjectDB


class TaskQueue:
    def __init__(self, pdb: ProjectDB, *, lease_minutes: int = 60, default_max_attempts: int = 3):
        self.pdb = pdb
        self.lease_seconds = lease_minutes * 60
        self.default_max_attempts = default_max_attempts

    def enqueue(
        self, kind: str, *, actor: TaskActor = TaskActor.HOST, title: str = "", target_id: str | None = None,
        payload: dict[str, Any] | None = None, depends_on: Iterable[str] = (), priority: int = 100,
        max_attempts: int | None = None, unique: bool = True, status: TaskStatus = TaskStatus.PENDING,
    ) -> Task:
        if unique:
            existing = self.pdb.tasks.open_for(kind, target_id)
            if existing is not None:
                return existing
        task = self.pdb.tasks.create(
            kind=kind, actor=actor, title=title, target_id=target_id, payload=payload, depends_on=depends_on,
            priority=priority, max_attempts=max_attempts or self.default_max_attempts, status=status,
        )
        self.pdb.events.log("task.created", "task", task.id, {"kind": kind, "actor": actor.value, "target": target_id})
        return task

    def lease_next(self, actor: TaskActor = TaskActor.HOST, kinds: Iterable[str] | None = None) -> Task | None:
        self.pdb.tasks.expire_leases()
        task = self.pdb.tasks.lease_next(actor, self.lease_seconds, kinds)
        if task:
            self.pdb.events.log("task.leased", "task", task.id, {"kind": task.kind})
        return task

    def current(self, actor: TaskActor = TaskActor.HOST) -> Task | None:
        self.pdb.tasks.expire_leases()
        return self.pdb.tasks.current_lease(actor)

    def verify_lease(self, task_id: str, lease_token: str | None) -> Task:
        task = self.pdb.tasks.get(task_id)
        if task.status != TaskStatus.LEASED:
            raise LeaseConflict(
                f"task {task_id} is not leased (status: {task.status.value})",
                hint="call next_task to obtain a fresh lease, then resubmit",
            )
        if task.lease_token != lease_token:
            raise LeaseConflict(
                f"lease token mismatch for task {task_id}; another session owns it now",
                hint="call next_task / resume_project to get the current lease",
            )
        return task

    def complete(self, task_id: str, result: dict[str, Any] | None = None) -> Task:
        task = self.pdb.tasks.set_status(task_id, TaskStatus.DONE, result=result or {})
        self.pdb.events.log("task.done", "task", task_id, {"kind": task.kind})
        return task

    def reject(self, task_id: str, feedback: list[dict[str, Any]]) -> Task:
        """Record a failed attempt. A leased task keeps its lease (the host can fix and resubmit with the same
        token); when attempts are exhausted the task is marked failed."""
        task = self.pdb.tasks.get(task_id)
        attempts = task.attempts + 1
        if attempts >= task.max_attempts:
            task = self.pdb.tasks.set_status(task_id, TaskStatus.FAILED, feedback=feedback, attempts=attempts)
        elif task.status == TaskStatus.LEASED:
            task = self.pdb.tasks.set_status(task_id, TaskStatus.LEASED, feedback=feedback, attempts=attempts,
                                             clear_lease=False)
            self.pdb.tasks.extend_lease(task_id, self.lease_seconds)
        else:
            task = self.pdb.tasks.set_status(task_id, TaskStatus.PENDING, feedback=feedback, attempts=attempts)
        self.pdb.events.log("task.rejected", "task", task_id,
                            {"attempts": attempts, "final": task.status == TaskStatus.FAILED})
        return self.pdb.tasks.get(task_id)

    def record_attempt(self, task_id: str, feedback: list[dict[str, Any]]) -> Task:
        task = self.pdb.tasks.get(task_id)
        return self.pdb.tasks.set_status(task_id, task.status, feedback=feedback, attempts=task.attempts + 1, clear_lease=False)

    def block(self, task_id: str, reason: str) -> Task:
        task = self.pdb.tasks.get(task_id)
        payload = dict(task.payload)
        payload["blocked_reason"] = reason
        self.pdb.tasks.update_payload(task_id, payload)
        return self.pdb.tasks.set_status(task_id, TaskStatus.BLOCKED)

    def unblock(self, task_id: str) -> Task:
        task = self.pdb.tasks.get(task_id)
        if task.status != TaskStatus.BLOCKED:
            raise WorkflowError(f"task {task_id} is not blocked")
        return self.pdb.tasks.set_status(task_id, TaskStatus.PENDING)

    def cancel(self, task_id: str) -> Task:
        return self.pdb.tasks.set_status(task_id, TaskStatus.CANCELLED)

    def release_host_leases(self) -> int:
        """Called on resume: a new session takes over all host work in progress."""
        n = self.pdb.tasks.release_all_leases(TaskActor.HOST)
        if n:
            self.pdb.events.log("task.leases_released", data={"count": n})
        return n
