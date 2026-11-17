"""Programmatic end-to-end scenario (requirement §14).

Request: "Create a rigorous scientific textbook about the Mathematical Foundations of
Deep Learning. Include calculus, linear algebra, optimization, automatic
differentiation, neural networks, proofs, exercises, figures, references and a
print-ready edition."

The scenario creates the project, drives the host protocol with the scripted host,
verifies sources (recorded real API responses by default, live with network=True),
runs math and consistency checks, renders figures, interrupts and resumes the
session, builds HTML/EPUB/PDF/print/LaTeX/Quarto, preflights the outputs, rebuilds
from sources without any host call and writes a report of what was and was not
verified.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from scholarforge import paths
from scholarforge.e2e.scripted_host import ScriptedHost, fixture_dir
from scholarforge.util.atomic import atomic_write_json, atomic_write_text

PROMPT = ("Create a rigorous scientific textbook about the Mathematical Foundations of Deep Learning. Include "
          "calculus, linear algebra, optimization, automatic differentiation, neural networks, proofs, exercises, "
          "figures, references and a print-ready edition.")

ALL_FORMATS = ["html", "epub", "pdf", "print", "latex", "quarto"]


@contextlib.contextmanager
def isolated_home(home: Path):
    """Run with a private SCHOLARFORGE_HOME (registry, cache, config) but the real tools directory."""
    saved = {k: os.environ.get(k) for k in ("SCHOLARFORGE_HOME", "SCHOLARFORGE_TOOLS_DIR", "SCHOLARFORGE_CONFIG")}
    tools = paths.tools_dir()
    os.environ["SCHOLARFORGE_TOOLS_DIR"] = str(tools)
    os.environ["SCHOLARFORGE_HOME"] = str(home)
    os.environ.pop("SCHOLARFORGE_CONFIG", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Steps:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, **details: Any) -> bool:
        self.items.append({"step": name, "ok": bool(ok), **details})
        return ok

    @property
    def ok(self) -> bool:
        return all(i["ok"] for i in self.items)


def drive(wf, host: ScriptedHost, *, max_iterations: int = 400, stop_after_drafts: int | None = None,
          log: list[dict[str, Any]] | None = None) -> str:
    """Run the host loop until the project is ready to build (or a draft count is reached)."""
    drafts = 0
    for _ in range(max_iterations):
        resp = wf.next_task()
        status = resp["status"]
        if status == "task":
            task = resp["task"]
            if stop_after_drafts is not None and task["kind"] == "draft.block" and drafts >= stop_after_drafts:
                return "interrupted_with_lease"
            result = host.answer(task)
            out = wf.submit_task(task["task_id"], task["lease_token"], result)
            if log is not None:
                log.append({"kind": task["kind"], "title": task["title"], "accepted": out["accepted"],
                            "errors": [f for f in out.get("feedback", []) if f["severity"] == "error"][:5]})
            if not out["accepted"]:
                errors = [f for f in out.get("feedback", []) if f["severity"] == "error"]
                raise RuntimeError(f"scripted host submission rejected for {task['kind']} '{task['title']}': "
                                   f"{errors[:5]}")
            if task["kind"] == "draft.block":
                drafts += 1
            continue
        if status in ("engine_busy",):
            continue
        if status == "waiting_for_user":
            raise RuntimeError(f"unexpected user decision required: {resp.get('blocked')}")
        return status
    raise RuntimeError("scenario did not converge within the iteration limit")


def run_scenario(workdir: Path | None = None, *, render: bool = True, network: bool = False,
                 record: bool = False, formats: list[str] | None = None) -> dict[str, Any]:
    from scholarforge.core.service import ProjectService
    from scholarforge.core.intake import params_from_request
    from scholarforge.core.workflow import Coordinator
    from scholarforge.science.pipeline import VerificationPipeline
    from scholarforge.science.sources import manager as source_manager
    from scholarforge.science.sources.http import FixtureTransport
    from scholarforge.settings import load_settings

    started = time.monotonic()
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="sf-e2e-"))
    base.mkdir(parents=True, exist_ok=True)
    steps = Steps()
    http_dir = fixture_dir() / "http"
    saved_transport = source_manager.DEFAULT_TRANSPORT
    saved_record = os.environ.get("SCHOLARFORGE_HTTP_RECORD")
    with isolated_home(base / "home"):
        if network and record:
            os.environ["SCHOLARFORGE_HTTP_RECORD"] = str(http_dir)
        if not network:
            source_manager.DEFAULT_TRANSPORT = FixtureTransport(http_dir)
        try:
            settings = load_settings()
            settings.projects_root = base / "projects"
            settings.network.enabled = True  # live or replayed, the verification code path is the same
            service = ProjectService(settings)
            host = ScriptedHost()
            log: list[dict[str, Any]] = []

            # 1. project
            params = params_from_request(PROMPT)
            params.target_pages = 5
            params.formats = formats or ALL_FORMATS
            project = service.create(params)
            steps.add("create_project", project.root.exists(), project_id=project.id, path=str(project.root),
                      topic=params.topic, required_topics=params.required_topics, profile=project.db.meta.get("profile_id"))

            # 2-5. research, sources, outline, first drafts — then simulate a crash with a leased task
            wf = Coordinator(project)
            status = drive(wf, host, stop_after_drafts=2, log=log)
            leased = wf.queue.current()
            steps.add("interrupt_mid_session", status == "interrupted_with_lease" and leased is not None,
                      leased_task=leased.kind if leased else None)
            leased_id = leased.id if leased else None
            project_id = project.id
            project.close()
            del wf, project

            # 14. resume in a "new session" (fresh service and database connections)
            service2 = ProjectService(settings)
            project = service2.open(project_id)
            wf = Coordinator(project)
            resume = wf.resume()
            nxt = wf.next_task()
            same = nxt.get("status") == "task" and nxt["task"]["task_id"] == leased_id
            steps.add("resume_after_interruption", resume["released_leases"] >= 1 and same,
                      released=resume["released_leases"], phase=resume["status"]["phase"])
            # hand the re-leased task back to the loop (submit it, then continue)
            if same:
                task = nxt["task"]
                out = wf.submit_task(task["task_id"], task["lease_token"], host.answer(task))
                log.append({"kind": task["kind"], "title": task["title"], "accepted": out["accepted"]})
            final = drive(wf, host, log=log)
            steps.add("authoring_loop", final == "ready_to_build", final_status=final, tasks=len(log),
                      revisions=sum(1 for e in log if e["kind"] == "revise.block"))

            # sources: fabricated reference caught, mismatch corrected
            srcs = {s.citekey: s for s in project.db.sources.list()}
            fake = srcs.get("doe2019")
            silver = srcs.get("silver2016")
            steps.add("fabricated_reference_rejected", fake is not None and fake.status.value in ("not_found", "rejected"),
                      status=fake.status.value if fake else None,
                      reason=(fake.verification or {}).get("reason") if fake else None)
            steps.add("mismatched_reference_detected_and_corrected",
                      silver is not None and silver.status.value == "verified" and silver.doi == "10.1038/nature16961",
                      status=silver.status.value if silver else None, doi=silver.doi if silver else None)
            verified = [s.citekey for s in srcs.values() if s.status.value == "verified"]
            steps.add("sources_verified", len(verified) >= 8, verified=len(verified), total=len(srcs),
                      not_verified={k: s.status.value for k, s in srcs.items() if s.status.value != "verified"})

            # 6-7. verification pass over the whole book
            ver = VerificationPipeline(project).run_all(network=True)
            claims = ver["claims"]
            math_ok = sum(v for k, v in ver["math"].items() if k.startswith("pass"))
            math_fail = sum(v for k, v in ver["math"].items() if k.startswith("fail"))
            steps.add("math_checks", math_ok >= 15 and math_fail == 0, results=ver["math"])
            steps.add("claims_classified", sum(claims.values()) >= 15 and claims.get("verified", 0) >= 5, claims=claims)
            cons = ver["consistency"]
            steps.add("consistency", "UNRESOLVED_REFERENCE" not in cons and "CIRCULAR_DEPENDENCY" not in cons,
                      findings=cons)
            figs = project.db.figures.list()
            steps.add("figures_rendered", len(figs) == 3 and all(f.status.value in ("rendered", "approved") for f in figs),
                      figures={f.label: f.status.value for f in figs})
            blocks = project.db.nodes.blocks()
            steps.add("blocks_written", all(b.status.value != "planned" for b in blocks) and len(blocks) == 6,
                      statuses={b.title: b.status.value for b in blocks}, words=sum(b.word_count for b in blocks))

            # 9-13. build and preflight
            build: dict[str, Any] = {}
            if render:
                from scholarforge.publishing.builder import BookBuilder

                build = BookBuilder(project).build(formats=params.formats, clean=True)
                res = build["results"]
                pre = build["preflight"]
                steps.add("build_html", bool(res.get("html", {}).get("ok")) and pre.get("html", {}).get("ok", False),
                          path=res.get("html", {}).get("path"), preflight=pre.get("html"))
                steps.add("build_epub", bool(res.get("epub", {}).get("ok")) and pre.get("epub", {}).get("ok", False),
                          path=res.get("epub", {}).get("path"), preflight=_short(pre.get("epub")))
                steps.add("build_pdf", bool(res.get("pdf", {}).get("ok")) and pre.get("pdf", {}).get("ok", False),
                          path=res.get("pdf", {}).get("path"), pages=res.get("pdf", {}).get("pages"),
                          preflight=_short(pre.get("pdf")))
                pr = res.get("print", {})
                steps.add("build_print_pdf", bool(pr.get("ok")), path=pr.get("path"), pages=pr.get("pages"),
                          pdfx=pr.get("pdfx"), error=pr.get("error"))
                ppre = pre.get("print", {})
                steps.add("print_preflight", bool(ppre.get("interior", {}).get("ok")) and
                          bool(ppre.get("cover", {}).get("ok")),
                          interior=_short(ppre.get("interior")), cover=_short(ppre.get("cover")))
                steps.add("export_latex", bool(res.get("latex", {}).get("ok")), path=res.get("latex", {}).get("path"))
                steps.add("export_quarto_source", bool(res.get("quarto", {}).get("ok")),
                          path=res.get("quarto", {}).get("path"))
                # rebuild from stored sources only: no host interaction allowed
                before = len(host.answered)
                html_dir = project.exports_dir / "html"
                shutil.rmtree(html_dir, ignore_errors=True)
                again = BookBuilder(project).build(formats=["html"])
                steps.add("rebuild_from_sources_without_model", again["results"]["html"]["ok"]
                          and len(host.answered) == before and (html_dir / "index.html").exists())
            # 15. report
            from scholarforge.science.report import write_verification_report

            rep = write_verification_report(project)
            steps.add("verification_report", Path(rep["markdown"]).exists(), report=rep["markdown"],
                      claims=rep["claims"], open_issues=rep["open_issues"])
            summary = {"ok": steps.ok, "seconds": round(time.monotonic() - started, 1), "project": str(project.root),
                       "workdir": str(base), "network": "live" if network else "recorded API responses",
                       "steps": steps.items, "task_log": log, "build_status": build.get("status")}
            atomic_write_json(project.reports_dir / "e2e-report.json", summary)
            atomic_write_text(project.reports_dir / "e2e-report.md", _markdown(summary))
            project.close()
            return summary
        finally:
            source_manager.DEFAULT_TRANSPORT = saved_transport
            if saved_record is None:
                os.environ.pop("SCHOLARFORGE_HTTP_RECORD", None)
            else:
                os.environ["SCHOLARFORGE_HTTP_RECORD"] = saved_record


def _short(rep: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rep:
        return rep
    return {k: rep.get(k) for k in ("ok", "errors", "warnings", "pdfx") if k in rep} | (
        {"epubcheck": {k: v for k, v in rep["epubcheck"].items() if k != "samples"}} if isinstance(rep.get("epubcheck"), dict)
        else {})


def _markdown(summary: dict[str, Any]) -> str:
    lines = [f"# End-to-end scenario: {'PASS' if summary['ok'] else 'FAIL'}", "",
             f"- Duration: {summary['seconds']} s", f"- Sources: {summary['network']}",
             f"- Project: `{summary['project']}`", "", "| Step | Result |", "|:--|:--|"]
    for s in summary["steps"]:
        lines.append(f"| {s['step']} | {'ok' if s['ok'] else 'FAILED'} |")
    lines += ["", "## Details", ""]
    for s in summary["steps"]:
        details = {k: v for k, v in s.items() if k not in ("step", "ok")}
        lines.append(f"- **{s['step']}**: {details}")
    return "\n".join(lines) + "\n"
