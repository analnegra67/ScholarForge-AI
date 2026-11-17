"""Handler base class and feedback helpers."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from scholarforge.core.workflow import EngineOutcome, SubmissionOutcome
from scholarforge.errors import WorkflowError
from scholarforge.models.entities import Task
from scholarforge.models.enums import TaskActor

M = TypeVar("M", bound=BaseModel)


def fb(severity: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    item = {"severity": severity, "code": code, "message": message}
    if details:
        item["details"] = details
    return item


def schema_feedback(exc: ValidationError) -> list[dict[str, Any]]:
    out = []
    for err in exc.errors(include_url=False, include_context=False)[:50]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out.append(fb("error", "SCHEMA", f"{loc or '<root>'}: {err.get('msg')}"))
    return out


def parse_result(model: type[M], result: dict[str, Any]) -> tuple[M | None, list[dict[str, Any]]]:
    if not isinstance(result, dict):
        return None, [fb("error", "SCHEMA", "result must be a JSON object")]
    try:
        return model.model_validate(result), []
    except ValidationError as exc:
        return None, schema_feedback(exc)


def has_errors(feedback: list[dict[str, Any]]) -> bool:
    return any(f["severity"] in ("error", "critical") for f in feedback)


def sort_feedback(feedback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    return sorted(feedback, key=lambda f: rank.get(f["severity"], 4))


class BaseHandler:
    kind: str = ""
    actor: TaskActor = TaskActor.HOST
    result_model: type[BaseModel] | None = None

    def render(self, wf, task: Task) -> dict[str, Any]:
        return {}

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        raise WorkflowError(f"task kind '{self.kind}' does not accept host submissions")

    def run(self, wf, task: Task) -> EngineOutcome:
        raise WorkflowError(f"task kind '{self.kind}' is not an engine task")

    def on_exhausted(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome | None:
        return None

    def schema(self) -> dict[str, Any] | None:
        return self.result_model.model_json_schema() if self.result_model else None
