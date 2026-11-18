"""ScholarForge MCP server (stdio transport, MCP Python SDK 2.x ``MCPServer``).

Protocol summary for the host AI (the bundled skill explains it in detail):

1. ``create_project`` (or ``list_projects`` + ``resume_project``)
2. loop: ``next_task`` -> do the work with your own reasoning/tools -> ``submit_task``
3. ``build_book`` -> ``verification_report`` / ``preflight_report``

The server never calls a language model. Every tool validates its input, returns
JSON, and reports failures as tool errors carrying ``code``, ``message`` and ``hint``.
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from pydantic import BaseModel, Field

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from scholarforge.errors import ConfirmationRequired, InvalidInput, ScholarForgeError
from scholarforge.logging_setup import configure_logging, get_logger
from scholarforge.version import __version__

log = get_logger("mcp")

INSTRUCTIONS = """\
ScholarForge orchestrates, verifies and typesets scientific books; YOU (the host AI) do the thinking and writing.
Work loop: next_task(project_id) -> perform the task described in 'instructions' using 'context' -> submit_task
with a JSON result matching 'output_schema' and the 'lease_token'. If the result is rejected, fix the reported
errors and resubmit. Call resume_project at the start of a new session. When next_task reports
'ready_to_build', call build_book. Never invent references, data or results; cite only verified sources.
Ask the user before paid actions (confirm_external_image) and before restoring checkpoints."""

_server: MCPServer | None = None


def _service():
    from scholarforge.core.service import ProjectService

    return ProjectService()


def _gate(write: bool) -> None:
    """Subscription gate (only enforced in packaged releases). Reading/exporting existing work is never blocked."""
    if not write:
        return
    from scholarforge.licensing.gate import require_entitlement

    require_entitlement("pro")


def _err(exc: ScholarForgeError) -> ToolError:
    return ToolError(json.dumps(exc.to_dict(), ensure_ascii=False))


def tool_errors(fn):
    """Translate ScholarForge errors into MCP tool errors; never leak tracebacks to the client."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except ScholarForgeError as exc:
            raise _err(exc) from exc
        except Exception as exc:  # pragma: no cover - unexpected bug: keep the server alive
            log.exception("tool %s crashed", fn.__name__)
            raise ToolError(json.dumps({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}",
                                        "hint": "see the ScholarForge log; the project state is unchanged"})) from exc

    return wrapper


async def _run(func, *args, **kwargs):
    """Run blocking work in a worker thread (SQLite connections are per thread)."""
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))


def _with_project(project_id: str, fn):
    project = _service().open(project_id)
    try:
        return fn(project)
    finally:
        project.close()


class ExternalImageApproval(BaseModel):
    approve: bool = Field(description="Generate the image now and accept the cost shown above?")


def build_server() -> MCPServer:
    server = MCPServer(name="scholarforge", title="ScholarForge", version=__version__, instructions=INSTRUCTIONS,
                       description="Local scientific publishing engine: research planning, verified sources, "
                                   "math checks, figures, Quarto/LaTeX typesetting.")

    # ------------------------------------------------------------------------------ environment
    @server.tool(description="Diagnose the local environment: Python packages, Quarto, LuaLaTeX, epubcheck, "
                             "network, credential store, license. Run this first in a new installation.")
    @tool_errors
    async def diagnostics(deep: bool = False) -> dict[str, Any]:
        from scholarforge.diagnostics import run_doctor
        from scholarforge.licensing.gate import license_summary

        report = await _run(run_doctor, deep)
        report["license"] = await _run(license_summary)
        return report

    @server.tool(description="Install external tools into the user profile (no admin rights): 'quarto' (~140 MB), "
                             "'tinytex' (LuaLaTeX, several hundred MB), 'epubcheck' (~5 MB). Downloads come from "
                             "the official releases and are checksum-verified. Requires confirm=true after the "
                             "user agreed.")
    @tool_errors
    async def install_tools(names: list[Literal["quarto", "tinytex", "epubcheck"]],
                            confirm: bool = False) -> dict[str, Any]:
        from scholarforge.tools import installer

        if not confirm:
            return {"confirmation_required": True,
                    "will_install": {n: installer.SIZES[n] for n in names},
                    "next": "ask the user; if they agree call install_tools again with confirm=true"}
        return await _run(installer.install, list(names), assume_yes=True)

    # ---------------------------------------------------------------------------------- projects
    @server.tool(description="Create a book project. Only 'topic' is required; pass the user's request verbatim "
                             "in 'request' and any options they gave. Returns the project id and the first step.")
    @tool_errors
    async def create_project(
        topic: Annotated[str, Field(min_length=3, description="subject of the book")],
        request: str | None = None,
        title: str | None = None,
        subtitle: str | None = None,
        language: str = "en",
        level: str = "graduate",
        audience: str | None = None,
        depth: Literal["overview", "standard", "rigorous", "exhaustive"] = "rigorous",
        target_pages: Annotated[int | None, Field(ge=1)] = None,
        book_type: Literal["textbook", "monograph", "encyclopedia", "handbook", "reference", "lecture_notes",
                           "multivolume"] = "textbook",
        style: str | None = None,
        formats: list[Literal["html", "epub", "pdf", "print", "latex", "quarto"]] | None = None,
        required_topics: list[str] | None = None,
        elements: list[str] | None = None,
        illustrations: Literal["auto", "native_only", "external_allowed", "none"] = "auto",
        profile: str | None = None,
        authors: list[str] | None = None,
        print_trim: str | None = None,
        print_color: Literal["bw", "color"] | None = None,
    ) -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.models.project import PrintSettings, ProjectParams

        data: dict[str, Any] = {"topic": topic, "language": language, "level": level, "depth": depth,
                                "book_type": book_type, "illustrations": illustrations}
        for key, value in (("request", request), ("title", title), ("subtitle", subtitle), ("audience", audience),
                           ("target_pages", target_pages), ("style", style), ("formats", formats),
                           ("required_topics", required_topics), ("elements", elements), ("profile", profile),
                           ("authors", authors)):
            if value is not None:
                data[key] = value
        try:
            params = ProjectParams.model_validate(data)
            ps_data = {}
            if print_trim:
                ps_data["trim"] = print_trim
            if print_color:
                ps_data["color"] = print_color
            print_settings = PrintSettings.model_validate(ps_data) if ps_data else None
        except Exception as exc:
            raise InvalidInput(f"invalid project parameters: {exc}") from exc

        def work():
            project = _service().create(params, print_settings=print_settings)
            try:
                return {"project_id": project.id, "path": str(project.root), "title": project.config.title,
                        "profile": project.db.meta.get("profile_id"),
                        "next": "call next_task with this project_id"}
            finally:
                project.close()

        return await _run(work)

    @server.tool(description="List known book projects (id, title, folder).")
    @tool_errors
    async def list_projects() -> dict[str, Any]:
        return {"projects": await _run(lambda: _service().list())}

    @server.tool(description="Status of a project: phase, progress, open issues, claim and source statistics.")
    @tool_errors
    async def project_status(project_id: str) -> dict[str, Any]:
        from scholarforge.core.workflow import Coordinator

        return await _run(_with_project, project_id, lambda p: Coordinator(p).status())

    @server.tool(description="Start of a new session: releases stale task leases, detects manual edits of the "
                             "sources and summarises where the project stands. Then call next_task.")
    @tool_errors
    async def resume_project(project_id: str) -> dict[str, Any]:
        from scholarforge.core.workflow import Coordinator

        return await _run(_with_project, project_id, lambda p: Coordinator(p).resume())

    # ----------------------------------------------------------------------------- host protocol
    @server.tool(description="Get the next task for you (the host AI): instructions, a compact context pack, the "
                             "JSON schema of the expected result and a lease token. Local jobs (source checks, "
                             "figure rendering) run first within a time budget.")
    @tool_errors
    async def next_task(project_id: str) -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.core.workflow import Coordinator

        return await _run(_with_project, project_id, lambda p: Coordinator(p).next_task())

    @server.tool(description="Submit the JSON result of a task (must match the task's output_schema). Returns "
                             "accepted=true, or concrete feedback to fix and resubmit with the same lease token.")
    @tool_errors
    async def submit_task(project_id: str, task_id: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.core.workflow import Coordinator

        return await _run(_with_project, project_id, lambda p: Coordinator(p).submit_task(task_id, lease_token, result))

    @server.tool(description="The book tree (volumes, parts, chapters, sections, blocks) with statuses and sizes.")
    @tool_errors
    async def get_outline(project_id: str) -> dict[str, Any]:
        def work(p):
            nodes = []
            for n in p.db.nodes.walk():
                nodes.append({"id": n.id, "parent_id": n.parent_id, "kind": n.kind.value, "title": n.title,
                              "status": n.status.value, "words": n.word_count,
                              "target_words": n.plan.get("target_words"), "version": n.version})
            return {"title": p.db.meta.get("book_title") or p.config.title, "nodes": nodes}

        return await _run(_with_project, project_id, work)

    @server.tool(description="Read the current Markdown of a block plus its claims, open issues and versions.")
    @tool_errors
    async def read_block(project_id: str, block_id: str) -> dict[str, Any]:
        def work(p):
            node = p.db.nodes.get(block_id)
            return {"block_id": node.id, "title": node.title, "status": node.status.value, "version": node.version,
                    "markdown": p.content.read_block(node.id),
                    "claims": [{"id": c.id, "kind": c.kind, "status": c.status.value, "reason": c.status_reason,
                                "text": c.text} for c in p.db.claims.for_node(node.id)],
                    "issues": [{"severity": i.severity.value, "code": i.code, "message": i.message}
                               for i in p.db.issues.list(node_id=node.id)],
                    "versions": p.db.nodes.versions(node.id)}

        return await _run(_with_project, project_id, work)

    @server.tool(description="Save an edited block outside the task loop (e.g. a correction the user asked for). "
                             "Structure and citations are validated; the block is re-verified afterwards.")
    @tool_errors
    async def save_block(project_id: str, block_id: str, markdown: str, note: str | None = None) -> dict[str, Any]:
        _gate(write=True)

        def work(p):
            from scholarforge.core.workflow import Coordinator
            from scholarforge.models.enums import NodeKind, NodeStatus, TaskActor
            from scholarforge.science.consistency import ConsistencyEngine
            from scholarforge.science.markdown import analyze

            node = p.db.nodes.get(block_id)
            if node.kind != NodeKind.BLOCK:
                raise InvalidInput(f"{block_id} is not a block")
            an = analyze(markdown)
            errors = [pr.message for pr in an.problems if pr.severity == "error"]
            unknown = sorted(an.citations - p.db.sources.citekeys())
            if unknown:
                errors.append(f"unknown citation keys: {unknown}")
            if errors:
                raise InvalidInput("the block was not saved", details={"errors": errors})
            node = p.content.write_block(node.id, markdown, origin="manual", note=note)
            p.db.labels.set_refs(node.id, an.refs)
            ConsistencyEngine(p.db).update_usage(node.id, markdown)
            if node.status == NodeStatus.PLANNED:
                p.db.nodes.update(node.id, status=NodeStatus.DRAFTED)
                p.db.tasks.cancel_open("draft.block", node.id)
            Coordinator(p).queue.enqueue("verify.block", actor=TaskActor.ENGINE, target_id=node.id,
                                         title=f"Re-verify '{node.title}'", priority=30)
            return {"saved": True, "version": node.version, "words": node.word_count}

        return await _run(_with_project, project_id, work)

    # ---------------------------------------------------------------------------------- sources
    @server.tool(description="Register a scholarly source and verify it immediately against Crossref, OpenAlex, "
                             "arXiv, Semantic Scholar or Open Library. Give identifiers only if you are sure.")
    @tool_errors
    async def add_source(project_id: str, title: str, authors: list[str] | None = None, year: int | None = None,
                         doi: str | None = None, arxiv_id: str | None = None, isbn: str | None = None,
                         url: str | None = None, venue: str | None = None,
                         kind: Literal["article", "book", "chapter", "proceedings", "preprint", "thesis", "report",
                                       "web", "dataset", "other"] = "article",
                         citekey: str | None = None, relevance: str = "") -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.models.results import CandidateSource
        from scholarforge.science.sources.manager import SourceManager

        try:
            cand = CandidateSource(title=title, authors=authors or [], year=year, doi=doi, arxiv_id=arxiv_id,
                                   isbn=isbn, url=url, venue=venue, kind=kind, citekey=citekey, relevance=relevance)
        except Exception as exc:
            raise InvalidInput(f"invalid source: {exc}") from exc

        def work(p):
            mgr = SourceManager(p)
            src, created = mgr.register(cand, proposed_by="host:add_source")
            verdict = mgr.verify(src.id) if p.settings.network.enabled else None
            src = p.db.sources.get(src.id)
            return {"citekey": src.citekey, "created": created, "status": src.status.value,
                    "reason": verdict.reason if verdict else "offline: verification pending",
                    "canonical": {"title": src.title, "authors": src.authors, "year": src.year, "doi": src.doi}}

        return await _run(_with_project, project_id, work)

    @server.tool(description="List the project's sources with their verification status.")
    @tool_errors
    async def list_sources(project_id: str, only_citable: bool = False) -> dict[str, Any]:
        def work(p):
            out = []
            for s in p.db.sources.list():
                if only_citable and s.status.value != "verified":
                    continue
                out.append({"citekey": s.citekey, "status": s.status.value, "title": s.title, "authors": s.authors[:4],
                            "year": s.year, "doi": s.doi, "arxiv_id": s.arxiv_id, "isbn": s.isbn,
                            "reason": (s.verification or {}).get("reason")})
            return {"sources": out}

        return await _run(_with_project, project_id, work)

    # ------------------------------------------------------------------------------ verification
    @server.tool(description="Run the whole-book verification pass: pending sources, all math checks, global "
                             "consistency (cross-references, circular dependencies, notation), claim statuses.")
    @tool_errors
    async def run_checks(project_id: str, offline: bool = False) -> dict[str, Any]:
        from scholarforge.science.pipeline import VerificationPipeline

        return await _run(_with_project, project_id, lambda p: VerificationPipeline(p).run_all(network=not offline))

    @server.tool(description="Check mathematical assertions with SymPy in a sandbox (no project needed). Each "
                             "check: {id, kind, ...} with kind in identity, derivative, gradient, integral, limit, "
                             "series, solve, inequality, matrix_identity, numeric, dimension, lean.")
    @tool_errors
    async def check_math(checks: list[dict[str, Any]]) -> dict[str, Any]:
        from scholarforge.models.results import MathCheckSpec
        from scholarforge.science.mathcheck import MathCheckRunner
        from scholarforge.settings import load_settings

        try:
            specs = [MathCheckSpec.model_validate(c).model_dump(exclude_none=True) for c in checks]
        except Exception as exc:
            raise InvalidInput(f"invalid math check: {exc}") from exc
        runner = MathCheckRunner.from_settings(load_settings())
        results = await _run(runner.run, specs)
        return {"results": [r.to_dict() | {"proof": r.independent_proof} for r in results]}

    @server.tool(description="Write and return the verification report (what was verified, how, what was not).")
    @tool_errors
    async def verification_report(project_id: str) -> dict[str, Any]:
        from scholarforge.science.report import write_verification_report

        return await _run(_with_project, project_id, write_verification_report)

    @server.tool(description="List the terminology registry, or change a term's definition (versioned; blocks "
                             "that use the term are queued for a consistency re-check).")
    @tool_errors
    async def terms(project_id: str, update_term: str | None = None, new_definition: str | None = None,
                    justification: str | None = None) -> dict[str, Any]:
        def work(p):
            from scholarforge.core.workflow import Coordinator
            from scholarforge.science.consistency import ConsistencyEngine

            if update_term:
                _gate(write=True)
                if not new_definition or not justification:
                    raise InvalidInput("new_definition and justification are required to change a term")
                t = p.db.terms.get(update_term)
                if t is None:
                    raise InvalidInput(f"unknown term '{update_term}'")
                p.db.terms.redefine(t.id, new_definition, None)
                deps = ConsistencyEngine(p.db).dependents_of_term(t.id)
                if deps:
                    Coordinator(p).queue.enqueue("consistency.recheck", title=f"Re-check uses of '{t.term}'",
                                                 target_id=t.id, payload={"blocks": deps}, priority=55, unique=False)
                return {"updated": t.term, "blocks_to_recheck": len(deps)}
            return {"terms": [{"term": t.term, "definition": t.definition, "aliases": t.aliases, "version": t.version}
                              for t in p.db.terms.list()],
                    "notation": [{"symbol": n.symbol, "meaning": n.meaning, "scope": n.scope}
                                 for n in p.db.notation.list()]}

        return await _run(_with_project, project_id, work)

    # ----------------------------------------------------------------------------------- figures
    @server.tool(description="Create or replace a computed figure (matplotlib code, Graphviz DOT, TikZ, SVG, "
                             "Mermaid) and render it now in the sandbox. Insert <!-- sf:figure LABEL --> in a block.")
    @tool_errors
    async def render_figure(project_id: str, label: str,
                            renderer: Literal["matplotlib", "graphviz", "tikz", "svg", "mermaid"], code: str,
                            caption: str, alt_text: str, block_id: str | None = None,
                            width: Annotated[float, Field(gt=0.1, le=1.0)] = 0.85) -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.illustrations.engine import IllustrationEngine
        from scholarforge.models.results import FigureRequest

        try:
            req = FigureRequest(label=label, renderer=renderer, code=code, caption=caption, alt_text=alt_text,
                                width=width)
        except Exception as exc:
            raise InvalidInput(f"invalid figure: {exc}") from exc

        def work(p):
            fig = p.db.figures.upsert(label=req.label, node_id=block_id, mode="native", renderer=req.renderer,
                                      spec={"renderer": req.renderer, "code": req.code, "width": req.width},
                                      caption=req.caption, alt_text=req.alt_text,
                                      license="original work generated for this project")
            res = IllustrationEngine(p).render(fig)
            return {"ok": res.ok, "outputs": res.outputs, "error": res.error, "qc_problems": res.qc_problems}

        return await _run(_with_project, project_id, work)

    @server.tool(description="Return the rendered PNG preview of a figure as an image, for visual checking.")
    @tool_errors
    async def get_figure_preview(project_id: str, label: str) -> Image:
        def work(p):
            fig = p.db.figures.by_label(label)
            if fig is None or "png" not in (fig.outputs or {}):
                raise InvalidInput(f"figure {label} has no rendered preview")
            return p.root / fig.outputs["png"]

        path = await _run(_with_project, project_id, work)
        return Image(path=path)

    @server.tool(description="Register an image file supplied by the user (photo, scan, third-party figure). "
                             "State its license/permission; it is copied into the project with provenance.")
    @tool_errors
    async def register_image(project_id: str, label: str, file_path: str, caption: str, alt_text: str,
                             license: str, source: str | None = None, block_id: str | None = None,
                             width: Annotated[float, Field(gt=0.1, le=1.0)] = 0.85) -> dict[str, Any]:
        _gate(write=True)

        def work(p):
            from scholarforge.illustrations.imported import import_image

            return import_image(p, label=label, file_path=Path(file_path), caption=caption, alt_text=alt_text,
                                license=license, source=source, block_id=block_id, width=width)

        return await _run(_with_project, project_id, work)

    @server.tool(description="Approve or decline a pending PAID external image generation (OpenAI Images API, "
                             "billed to the user's own key). Ask the user first; the request shows the estimate.")
    @tool_errors
    async def confirm_external_image(project_id: str, task_id: str, confirmation_token: str, approve: bool,
                                     ctx: Context) -> dict[str, Any]:
        _gate(write=True)
        from scholarforge.illustrations.external.service import confirm_external_generation

        if approve:
            # Prefer asking the human directly through the client (MCP elicitation) when supported.
            caps = ctx.client_capabilities
            if caps is not None and getattr(caps, "elicitation", None) is not None:
                def estimate(p):
                    t = p.db.tasks.get(task_id)
                    return t.payload.get("estimate", {}), t.payload.get("label")

                est, label = await _run(_with_project, project_id, estimate)
                answer = await ctx.elicit(
                    f"ScholarForge wants to generate figure {label} with the OpenAI Images API on your account. "
                    f"Estimated cost: ${est.get('estimated_usd', '?')} (approximate). Proceed?",
                    ExternalImageApproval)
                if answer.action != "accept" or not getattr(answer.data, "approve", False):
                    raise ConfirmationRequired("the user did not approve the paid generation")
        return await _run(_with_project, project_id,
                          lambda p: confirm_external_generation(p, task_id, confirmation_token, approve))

    # ------------------------------------------------------------------------------- publishing
    @server.tool(description="Build the book from stored sources (no regeneration): HTML, EPUB, PDF, print PDF "
                             "with cover, LaTeX and Quarto sources, followed by preflight. Can take minutes.")
    @tool_errors
    async def build_book(project_id: str,
                         formats: list[Literal["html", "epub", "pdf", "print", "latex", "quarto"]] | None = None,
                         clean: bool = False, ctx: Context | None = None) -> dict[str, Any]:
        from scholarforge.publishing.builder import BookBuilder

        if ctx is not None:
            try:
                await ctx.report_progress(0, None, "building")
            except Exception:
                pass
        manifest = await _run(_with_project, project_id, lambda p: BookBuilder(p).build(formats=formats, clean=clean))
        return {"status": manifest["status"], "seconds": manifest["seconds"],
                "outputs": {k: v.get("path") for k, v in manifest["results"].items() if isinstance(v, dict)},
                "errors": {k: v.get("error") for k, v in manifest["results"].items()
                           if isinstance(v, dict) and v.get("error")},
                "preflight_ok": {k: (v.get("ok") if "ok" in v else {s: r.get("ok") for s, r in v.items()})
                                 for k, v in manifest["preflight"].items()},
                "verification_report": manifest.get("verification_report")}

    @server.tool(description="Full preflight results of the latest build (PDF geometry, fonts, colour, images, "
                             "PDF/X-4 checks, EPUB and HTML validation).")
    @tool_errors
    async def preflight_report(project_id: str) -> dict[str, Any]:
        from scholarforge.publishing.preflight import preflight_project

        return await _run(_with_project, project_id, preflight_project)

    @server.tool(description="Change print/build settings of a project: trim size, bleed, paper, colour mode, "
                             "PDF/X, ICC profile, cover colours and text, formats.")
    @tool_errors
    async def configure_project(project_id: str, print_settings: dict[str, Any] | None = None,
                                build_settings: dict[str, Any] | None = None,
                                formats: list[Literal["html", "epub", "pdf", "print", "latex", "quarto"]] | None = None,
                                authors: list[str] | None = None) -> dict[str, Any]:
        _gate(write=True)

        def work(p):
            from scholarforge.models.project import BuildSettings, PrintSettings

            cfg = p.config
            try:
                if print_settings:
                    cfg.print = PrintSettings.model_validate({**cfg.print.model_dump(), **print_settings})
                if build_settings:
                    cfg.build = BuildSettings.model_validate({**cfg.build.model_dump(), **build_settings})
                if formats:
                    cfg.params.formats = list(formats)
                if authors is not None:
                    cfg.params.authors = authors
            except Exception as exc:
                raise InvalidInput(f"invalid settings: {exc}") from exc
            p.save_config()
            return {"saved": True, "print": cfg.print.model_dump(), "build": cfg.build.model_dump(),
                    "formats": cfg.params.formats}

        return await _run(_with_project, project_id, work)

    # ------------------------------------------------------------------------------ checkpoints
    @server.tool(description="Create a checkpoint, list checkpoints, or restore one (restore needs confirm=true "
                             "after the user agreed; a safety checkpoint is taken first).")
    @tool_errors
    async def checkpoints(project_id: str, action: Literal["list", "create", "restore"] = "list",
                          reason: str | None = None, checkpoint_id: str | None = None,
                          confirm: bool = False) -> dict[str, Any]:
        def work(p):
            if action == "create":
                return {"checkpoint": p.checkpoints.create(reason or "manual (host)", auto=False)}
            if action == "restore":
                if not checkpoint_id:
                    raise InvalidInput("checkpoint_id is required")
                if not confirm:
                    return {"confirmation_required": True,
                            "next": "ask the user; restoring replaces the current text with the checkpoint"}
                return {"restored": checkpoint_id, "safety_checkpoint": p.checkpoints.restore(checkpoint_id)}
            return {"checkpoints": p.checkpoints.list()}

        return await _run(_with_project, project_id, work)

    # --------------------------------------------------------------------------------- license
    @server.tool(description="Subscription status (ScholarForge Pro). Book content is never sent to the license "
                             "server.")
    @tool_errors
    async def license_status() -> dict[str, Any]:
        from scholarforge.licensing.gate import license_summary

        return await _run(license_summary)

    @server.tool(description="Activate a ScholarForge Pro license key on this device.")
    @tool_errors
    async def activate_license(license_key: str) -> dict[str, Any]:
        from scholarforge.licensing.client import LicenseClient

        return (await _run(LicenseClient().activate, license_key)).model_dump(mode="json")

    return server


def main() -> None:
    from scholarforge.settings import load_settings
    from scholarforge.util.winenv import complete_windows_env

    added = complete_windows_env()  # MCP clients pass a minimal environment; Quarto needs PATHEXT
    try:
        level = load_settings().log_level
    except ScholarForgeError:
        level = "INFO"
    configure_logging(level, stderr=True)
    log.info("ScholarForge MCP server %s starting (python %s)", __version__, sys.version.split()[0])
    if added:
        log.info("completed the minimal client environment with: %s", ", ".join(added))
    build_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
