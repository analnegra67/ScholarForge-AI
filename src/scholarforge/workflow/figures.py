"""Figure handlers: render (engine), fix (host), visual review (host), external generation (user-confirmed)."""

from __future__ import annotations

import textwrap
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from scholarforge.core.workflow import EngineOutcome, SubmissionOutcome
from scholarforge.models.entities import Figure, Node, Task
from scholarforge.models.enums import FigureStatus, Severity, TaskActor, TaskStatus
from scholarforge.models.results import FigureFixResult, FigureRequest
from scholarforge.workflow import register
from scholarforge.workflow.base import BaseHandler, fb, parse_result


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


def request_figure(wf, node: Node, req: FigureRequest) -> Figure:
    mode = "external" if req.renderer == "external" else "native"
    spec = {"renderer": req.renderer, "code": req.code, "prompt": req.prompt, "width": req.width,
            "data_provenance": req.data_provenance}
    existing = wf.pdb.figures.by_label(req.label)
    unchanged = (existing is not None and existing.spec == spec and existing.caption == req.caption
                 and existing.status in (FigureStatus.RENDERED, FigureStatus.APPROVED))
    if unchanged:
        return existing
    fig = wf.pdb.figures.upsert(label=req.label, node_id=node.id, mode=mode, renderer=req.renderer, spec=spec,
                                caption=req.caption, alt_text=req.alt_text,
                                license="original work generated for this project" if mode == "native" else None)
    if mode == "external":
        from scholarforge.illustrations.external.service import plan_external_generation

        plan_external_generation(wf, fig)
    else:
        wf.queue.enqueue("figure.render", actor=TaskActor.ENGINE, title=f"Render {req.label}", target_id=fig.id,
                         priority=40)
    return fig


class FigureRenderHandler(BaseHandler):
    kind = "figure.render"
    actor = TaskActor.ENGINE

    def run(self, wf, task: Task) -> EngineOutcome:
        from scholarforge.illustrations.engine import IllustrationEngine

        fig = wf.pdb.figures.get(task.target_id)
        result = IllustrationEngine(wf.project).render(fig)
        if result.ok:
            for problem in result.qc_problems:
                wf.pdb.issues.add(severity=Severity.WARNING, category="figure", code=problem["code"],
                                  message=f"{fig.label}: {problem['message']}", node_id=fig.node_id)
            if not wf.pdb.tasks.open_for("figure.review", fig.id):
                wf.queue.enqueue("figure.review", title=f"Check rendered figure {fig.label}", target_id=fig.id,
                                 priority=45)
            return EngineOutcome(summary=f"{fig.label} rendered", data={"outputs": result.outputs})
        if not wf.pdb.tasks.open_for("figure.fix", fig.id):
            wf.queue.enqueue("figure.fix", title=f"Fix figure {fig.label}", target_id=fig.id, priority=45,
                             payload={"error": result.error[-4000:]})
        return EngineOutcome(summary=f"{fig.label} failed: {result.error[:200]}", data={"error": result.error[-2000:]})


class FigureFixHandler(BaseHandler):
    kind = "figure.fix"
    result_model = FigureFixResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        from scholarforge.illustrations.engine import RENDERER_GUIDE

        fig = wf.pdb.figures.get(task.target_id)
        return {
            "instructions": _dedent(f"""
                Rendering of figure {fig.label} failed (see context.error). Return corrected source code for the
                same renderer (or switch to another native renderer). The figure must still show what the caption
                describes. Code runs in a sandbox: only the listed libraries, no file or network access; save the
                figure with the provided save function / variable as described in context.renderer_guide.
            """),
            "context": {"label": fig.label, "renderer": fig.renderer, "code": fig.spec.get("code"),
                        "caption": fig.caption, "alt_text": fig.alt_text, "error": task.payload.get("error"),
                        "renderer_guide": RENDERER_GUIDE},
            "output_schema": FigureFixResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        from scholarforge.illustrations.engine import IllustrationEngine

        fix, feedback = parse_result(FigureFixResult, result)
        if fix is None:
            return SubmissionOutcome(False, feedback)
        fig = wf.pdb.figures.get(task.target_id)
        if fix.label != fig.label:
            return SubmissionOutcome(False, [fb("error", "LABEL_MISMATCH", f"expected {fig.label}")])
        spec = {**fig.spec, "code": fix.code, "renderer": fix.renderer or fig.renderer}
        fig = wf.pdb.figures.update(fig.id, spec=spec, renderer=spec["renderer"], mode="native",
                                    caption=fix.caption or fig.caption, alt_text=fix.alt_text or fig.alt_text)
        res = IllustrationEngine(wf.project).render(fig)
        if not res.ok:
            return SubmissionOutcome(False, [fb("error", "RENDER_FAILED", res.error[-3000:])])
        wf.queue.enqueue("figure.review", title=f"Check rendered figure {fig.label}", target_id=fig.id, priority=45)
        return SubmissionOutcome(True, feedback, summary=f"{fig.label} rendered", data={"outputs": res.outputs})

    def on_exhausted(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome | None:
        fig = wf.pdb.figures.get(task.target_id)
        wf.pdb.figures.update(fig.id, status=FigureStatus.FAILED)
        wf.pdb.issues.add(severity=Severity.ERROR, category="figure", code="FIGURE_UNRENDERABLE",
                          message=f"{fig.label} could not be rendered after several attempts; a placeholder is "
                                  "used in builds", node_id=fig.node_id)
        return SubmissionOutcome(True, [], summary="figure marked failed; placeholder will be used")


class FigureReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    verdict: Literal["ok", "problems"]
    problems: list[str] = Field(default_factory=list)
    suggested_caption: str | None = None
    suggested_alt_text: str | None = None


class FigureReviewHandler(BaseHandler):
    kind = "figure.review"
    result_model = FigureReviewResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        fig = wf.pdb.figures.get(task.target_id)
        block_text = wf.project.content.read_block(fig.node_id) if fig.node_id else ""
        png = (fig.outputs or {}).get("png")
        return {
            "instructions": _dedent(f"""
                Look at the rendered figure {fig.label} (PNG path in context.preview_png; the MCP tool
                get_figure_preview returns it as an image). Check that it is scientifically correct and matches the
                caption and the surrounding text: axes, labels, units, legend, plotted functions, readability in
                grayscale print. Report 'ok' or list concrete problems (they trigger a fix of the figure code).
                The alt text must describe the information content for readers who cannot see the image.
            """),
            "context": {"label": fig.label, "caption": fig.caption, "alt_text": fig.alt_text, "code": fig.spec.get("code"),
                        "preview_png": png, "qc": fig.qc, "block_excerpt": (block_text or "")[:6000]},
            "output_schema": FigureReviewResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        rev, feedback = parse_result(FigureReviewResult, result)
        if rev is None:
            return SubmissionOutcome(False, feedback)
        fig = wf.pdb.figures.get(task.target_id)
        if rev.label != fig.label:
            return SubmissionOutcome(False, [fb("error", "LABEL_MISMATCH", f"expected {fig.label}")])
        updates: dict[str, Any] = {}
        if rev.suggested_caption:
            updates["caption"] = rev.suggested_caption
        if rev.suggested_alt_text:
            updates["alt_text"] = rev.suggested_alt_text
        qc = dict(fig.qc or {})
        qc["host_review"] = {"verdict": rev.verdict, "problems": rev.problems, "independent": False}
        updates["qc"] = qc
        if rev.verdict == "ok":
            updates["status"] = FigureStatus.APPROVED
            wf.pdb.figures.update(fig.id, **updates)
            return SubmissionOutcome(True, feedback, summary=f"{fig.label} approved")
        wf.pdb.figures.update(fig.id, status=FigureStatus.NEEDS_REVIEW, **updates)
        fixes = wf.pdb.tasks.list(kind="figure.fix", target_id=fig.id)
        if len(fixes) < 2:
            wf.queue.enqueue("figure.fix", title=f"Fix figure {fig.label} after review", target_id=fig.id, priority=45,
                             payload={"error": "review problems: " + "; ".join(rev.problems)}, unique=False)
        else:
            wf.pdb.issues.add(severity=Severity.WARNING, category="figure", code="FIGURE_REVIEW_PROBLEMS",
                              message=f"{fig.label}: " + "; ".join(rev.problems), node_id=fig.node_id)
        return SubmissionOutcome(True, feedback, summary=f"{fig.label}: problems reported; fix requested")


class ExternalImageHandler(BaseHandler):
    """User-confirmed paid generation. The task stays BLOCKED until confirmed through the confirm tool."""

    kind = "figure.external"
    actor = TaskActor.USER


for _h in (FigureRenderHandler(), FigureFixHandler(), FigureReviewHandler(), ExternalImageHandler()):
    register(_h)
