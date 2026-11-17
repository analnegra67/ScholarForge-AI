"""Research, sources and architecture handlers. Importing this module registers all handlers."""

from __future__ import annotations

import textwrap
from typing import Any

from scholarforge.core.workflow import EngineOutcome, SubmissionOutcome
from scholarforge.models.entities import Task
from scholarforge.models.enums import NodeKind, Severity, SourceStatus, TaskActor
from scholarforge.models.results import (
    ChapterDetailResult,
    ChapterPlan,
    OutlineResult,
    ResearchPlanResult,
    SectionPlan,
    SourcesReviewResult,
)
from scholarforge.profiles.registry import get_registry
from scholarforge.science.sources.manager import NEEDS_ATTENTION, SourceManager
from scholarforge.util.atomic import atomic_write_json, atomic_write_text
from scholarforge.util.text import normalize_for_match, slugify, title_similarity
from scholarforge.workflow import register
from scholarforge.workflow.base import BaseHandler, fb, has_errors, parse_result, sort_feedback
from scholarforge.workflow.context import ContextBuilder

MAX_SOURCE_REVIEW_ROUNDS = 2


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


# ======================================================================================= research
class ResearchPlanHandler(BaseHandler):
    kind = "research.plan"
    result_model = ResearchPlanResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        ctx = ContextBuilder(wf)
        reg = get_registry()
        params = wf.project.config.params
        _, ranking = reg.match(" ".join([params.topic, params.request or "", *params.required_topics]))
        prof = ctx.profile()
        min_sources = self.min_sources(wf)
        return {
            "instructions": _dedent(f"""
                You are planning the research for a {params.depth} {params.book_type.replace('_', ' ')} on
                "{params.topic}" (language: {params.language}; level: {params.level}).
                Produce a research plan as JSON matching output_schema:
                - field_name, scope (what is in and out of scope), subfields;
                - concept_map: the key concepts with their prerequisite concepts (depends_on) — this drives the
                  chapter order;
                - prerequisites the reader must already have;
                - key_theories with an honest status: established / widely_accepted / contested / speculative;
                - open_problems: genuinely unresolved questions in the field;
                - conventions: notation, units, sign conventions, terminology choices to keep book-wide;
                - candidate_sources: at least {min_sources} primary or authoritative works (textbooks, seminal
                  papers, reviews). Give DOI / arXiv id / ISBN ONLY when you are certain; otherwise give the exact
                  title, authors and year. ScholarForge checks every entry against Crossref, OpenAlex, arXiv and
                  Open Library: invented or mismatched references are detected and rejected.
                - optionally profile_id if a better domain profile fits (see context.profiles).
                Use your own knowledge and any research tools available to you.
            """),
            "context": {
                "book": ctx.book(),
                "active_profile": prof.summary(),
                "profiles": [{"id": r["id"], "score": r["score"]} for r in ranking] + [{"all": reg.ids()}],
                "min_sources": min_sources,
            },
            "output_schema": ResearchPlanResult.model_json_schema(),
        }

    @staticmethod
    def min_sources(wf) -> int:
        prof = ContextBuilder(wf).profile()
        pages = wf.project.config.params.target_pages
        if pages is not None and pages < 30:
            return max(1, min(prof.sources.min_sources, pages // 5 or 1))
        return prof.sources.min_sources

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        plan, feedback = parse_result(ResearchPlanResult, result)
        if plan is None:
            return SubmissionOutcome(False, feedback)
        need = self.min_sources(wf)
        if len(plan.candidate_sources) < need:
            feedback.append(fb("error", "TOO_FEW_SOURCES", f"{len(plan.candidate_sources)} candidate sources; "
                                                           f"at least {need} required"))
        if plan.profile_id:
            reg = get_registry()
            if plan.profile_id not in reg.ids():
                feedback.append(fb("warning", "UNKNOWN_PROFILE", f"profile '{plan.profile_id}' does not exist; "
                                                                 "keeping the current profile"))
        if has_errors(feedback):
            return SubmissionOutcome(False, sort_feedback(feedback))

        project = wf.project
        data = plan.model_dump(mode="json")
        atomic_write_json(project.research_dir / "plan.json", data)
        atomic_write_text(project.research_dir / "plan.md", _plan_markdown(plan))
        if plan.profile_id and plan.profile_id in get_registry().ids():
            wf.pdb.meta.set("profile_id", plan.profile_id)
        mgr = SourceManager(project)
        added = 0
        for cand in plan.candidate_sources:
            _, created = mgr.register(cand, proposed_by="host:research")
            added += int(created)
        verify = wf.queue.enqueue("sources.verify", actor=TaskActor.ENGINE, title="Verify candidate sources",
                                  priority=5)
        wf.queue.enqueue("architecture.outline", title="Design the book architecture", priority=20,
                         depends_on=[verify.id])
        return SubmissionOutcome(True, feedback, summary=f"research plan stored; {added} sources registered for "
                                                         "verification; next: book architecture",
                                 stored_result={"sources": added})


def _plan_markdown(plan: ResearchPlanResult) -> str:
    lines = [f"# Research plan: {plan.field_name}", "", plan.scope, "", "## Concept map", ""]
    for c in plan.concept_map:
        dep = f" (requires: {', '.join(c.depends_on)})" if c.depends_on else ""
        lines.append(f"- **{c.concept}**{dep}: {c.description}")
    lines += ["", "## Key theories", ""]
    lines += [f"- {t.name} — *{t.status}*. {t.notes}" for t in plan.key_theories]
    lines += ["", "## Open problems", ""] + [f"- {p}" for p in plan.open_problems]
    lines += ["", "## Conventions", ""] + [f"- **{c.topic}**: {c.choice}" for c in plan.conventions]
    return "\n".join(lines) + "\n"


# ======================================================================================== sources
class SourcesVerifyHandler(BaseHandler):
    kind = "sources.verify"
    actor = TaskActor.ENGINE

    def run(self, wf, task: Task) -> EngineOutcome:
        mgr = SourceManager(wf.project)
        budget = max(5.0, wf.project.settings.workflow.engine_time_budget_s * 0.8)
        summary = mgr.verify_pending(budget)
        online = wf.project.settings.network.enabled
        if summary["remaining"]:
            return EngineOutcome(done=False, retry_later=True,
                                 summary=f"verified a batch; {summary['remaining']} sources remaining")
        pending = wf.pdb.sources.list(SourceStatus.PENDING)
        tries = int(task.payload.get("network_retries", 0))
        if pending and online and tries < 3:
            wf.pdb.tasks.update_payload(task.id, {**task.payload, "network_retries": tries + 1})
            return EngineOutcome(done=False, retry_later=True,
                                 summary=f"{len(pending)} sources pending (services unreachable); will retry")
        if pending:
            wf.pdb.issues.add(severity=Severity.WARNING, category="sources", code="SOURCES_PENDING",
                              message=f"{len(pending)} sources could not be verified (offline or services "
                                      "unavailable); they stay uncitable until verified",
                              details={"citekeys": [s.citekey for s in pending]})
        problems = wf.pdb.sources.list(list(NEEDS_ATTENTION))
        rounds = int(wf.pdb.meta.get("sources_review_rounds", "0"))
        if problems and rounds < MAX_SOURCE_REVIEW_ROUNDS:
            wf.queue.enqueue("sources.review", title=f"Fix or drop {len(problems)} unverified sources", priority=15)
        elif problems:
            for src in problems:
                mgr.drop(src.id, f"unresolved after {rounds} review rounds ({src.status.value})")
        return EngineOutcome(summary=f"sources checked: {summary['checked']}", data=summary)


class SourcesReviewHandler(BaseHandler):
    kind = "sources.review"
    result_model = SourcesReviewResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        items = []
        for src in wf.pdb.sources.list(list(NEEDS_ATTENTION)):
            ver = src.verification or {}
            items.append({"source_id": src.id, "citekey": src.citekey, "status": src.status.value,
                          "claimed": {"title": src.title, "authors": src.authors, "year": src.year, "doi": src.doi,
                                      "arxiv_id": src.arxiv_id, "isbn": src.isbn},
                          "finding": ver.get("reason"), "registry_record": ver.get("matched")})
        return {
            "instructions": _dedent("""
                These references could not be verified against the bibliographic registries. For each one choose:
                - action "correct" with corrected metadata, only if you are certain (e.g. a mistyped identifier, or
                  the registry record shown is the work you meant). The corrected entry is verified again.
                - action "drop" if the work may not exist or you are unsure. Dropped works can never be cited.
                Do not guess identifiers. A wrong 'correct' is worse than a 'drop'.
            """),
            "context": {"sources": items},
            "output_schema": SourcesReviewResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        review, feedback = parse_result(SourcesReviewResult, result)
        if review is None:
            return SubmissionOutcome(False, feedback)
        mgr = SourceManager(wf.project)
        open_ids = {s.id for s in wf.pdb.sources.list(list(NEEDS_ATTENTION))}
        unknown = [f.source_id for f in review.fixes if f.source_id not in open_ids]
        if unknown:
            return SubmissionOutcome(False, [fb("error", "UNKNOWN_SOURCE_ID", f"not in this review: {unknown}")])
        handled = set()
        corrected = 0
        for fix in review.fixes:
            handled.add(fix.source_id)
            if fix.action == "drop":
                mgr.drop(fix.source_id, "dropped by host during source review")
            else:
                mgr.correct(fix.source_id, fix.corrected)
                corrected += 1
        for sid in open_ids - handled:
            feedback.append(fb("warning", "NOT_ADDRESSED", f"source {sid} was not addressed; it stays unverified"))
        rounds = int(wf.pdb.meta.get("sources_review_rounds", "0")) + 1
        wf.pdb.meta.set("sources_review_rounds", str(rounds))
        if corrected:
            wf.queue.enqueue("sources.verify", actor=TaskActor.ENGINE, title="Re-verify corrected sources", priority=5)
        return SubmissionOutcome(True, feedback, summary=f"{corrected} corrected (re-verification queued), "
                                                         f"{len(review.fixes) - corrected} dropped")


# =================================================================================== architecture
def _normalized_volumes(outline: OutlineResult) -> list[dict[str, Any]]:
    """Return [{title, parts: [{title|None, chapters: [ChapterPlan]}]}] for any outline layout."""
    if outline.volumes:
        vols = []
        for v in outline.volumes:
            parts = [{"title": p.title, "chapters": p.chapters} for p in v.parts] if v.parts else [
                {"title": None, "chapters": v.chapters}]
            vols.append({"title": v.title, "parts": parts})
        return vols
    if outline.parts:
        return [{"title": None, "parts": [{"title": p.title, "chapters": p.chapters} for p in outline.parts]}]
    return [{"title": None, "parts": [{"title": None, "chapters": outline.chapters}]}]


def _chapter_words(ch: ChapterPlan) -> int:
    if ch.sections:
        return sum(b.target_words for s in ch.sections for b in s.blocks)
    return ch.target_words or 0


class OutlineHandler(BaseHandler):
    kind = "architecture.outline"
    result_model = OutlineResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        ctx = ContextBuilder(wf)
        params = wf.project.config.params
        wpp = wf.project.settings.workflow.words_per_page
        target_words = params.target_pages * wpp if params.target_pages else None
        plan = ctx.research_plan()
        large = bool(target_words and target_words > 60000)
        return {
            "instructions": _dedent(f"""
                Design the complete architecture of the book as JSON matching output_schema.
                - Hierarchy: chapters > sections > blocks (use parts or volumes for large works). A block is one
                  coherent unit of 250-1500 words that will be written in a single task.
                - Order chapters so that every concept comes after its prerequisites (see research.concept_map).
                - Cover every required topic: {params.required_topics or 'as appropriate for the scope'}.
                - Plan concrete elements per block (definition, theorem, proof, example, exercise, figure, ...).
                  Requested book-wide elements: {params.elements}.
                - target_words per block; the whole book should total about
                  {target_words or 'a size appropriate to the depth and scope'} words
                  ({params.target_pages or 'no fixed'} pages at ~{wpp} words/page).
                - key_terms each block introduces, and sources (citekeys from context.sources) it will cite.
                - Put supporting material in appendices when it would break the main line.
                {'- This is a large work: you may set detail_later: true (with target_words) on chapters; their '
                 'sections will be planned in separate tasks.' if large else ''}
            """),
            "context": {
                "book": ctx.book(),
                "research": {k: plan.get(k) for k in ("field_name", "scope", "concept_map", "prerequisites",
                                                       "key_theories", "open_problems")},
                "sources": ctx.sources(),
                "target_words": target_words,
            },
            "output_schema": OutlineResult.model_json_schema(),
        }

    def validate(self, wf, outline: OutlineResult) -> list[dict[str, Any]]:
        feedback: list[dict[str, Any]] = []
        params = wf.project.config.params
        vols = _normalized_volumes(outline)
        chapters = [c for v in vols for p in v["parts"] for c in p["chapters"]] + list(outline.appendices)
        if not chapters:
            feedback.append(fb("error", "EMPTY_OUTLINE", "the outline has no chapters"))
            return feedback
        total = sum(_chapter_words(c) for c in chapters)
        if params.target_pages:
            target = params.target_pages * wf.project.settings.workflow.words_per_page
            ratio = total / target if target else 1
            if ratio < 0.4 or ratio > 2.5:
                feedback.append(fb("error", "SIZE_MISMATCH", f"outline totals {total} words; target is about {target}"))
            elif ratio < 0.7 or ratio > 1.5:
                feedback.append(fb("warning", "SIZE_DEVIATION", f"outline totals {total} words; target about {target}"))
        corpus_parts = []
        for c in chapters:
            corpus_parts += [c.title, c.summary]
            for s in c.sections:
                corpus_parts += [s.title, s.summary]
                for b in s.blocks:
                    corpus_parts += [b.title, b.summary, " ".join(b.key_terms)]
        corpus = " " + normalize_for_match(" ".join(corpus_parts)) + " "
        for topic in params.required_topics:
            nt = normalize_for_match(topic)
            stem_hits = all((" " + w[: max(4, len(w) - 2)]) in corpus for w in nt.split())
            if not stem_hits and not any(title_similarity(topic, c.title) >= 0.6 for c in chapters):
                feedback.append(fb("error", "MISSING_REQUIRED_TOPIC", f"required topic '{topic}' is not covered"))
        planned = [b for c in chapters for s in c.sections for b in s.blocks]
        all_elements = {e for b in planned for e in b.elements}
        wants = {"proofs": "proof", "exercises": "exercise", "figures": "figure"}
        detailed = all(c.sections for c in chapters)
        for want, element in wants.items():
            if want in params.elements and detailed and element not in all_elements:
                feedback.append(fb("error", "MISSING_ELEMENT_TYPE", f"the request asks for {want}, but no block "
                                                                    f"plans a '{element}'"))
        if "exercises" in params.elements:
            for c in chapters:
                if c.sections and not c.appendix and not any("exercise" in b.elements for s in c.sections for b in s.blocks):
                    feedback.append(fb("warning", "CHAPTER_WITHOUT_EXERCISES", f"chapter '{c.title}' has no exercises"))
        titles = [normalize_for_match(c.title) for c in chapters]
        for t in {t for t in titles if titles.count(t) > 1}:
            feedback.append(fb("warning", "DUPLICATE_CHAPTER_TITLE", f"duplicate chapter title '{t}'"))
        known = wf.pdb.sources.citekeys()
        unknown = sorted({k for b in planned for k in b.sources if k not in known})
        if unknown:
            feedback.append(fb("warning", "UNKNOWN_PLANNED_SOURCES", f"planned citekeys not in the registry will be "
                                                                     f"ignored: {unknown[:10]}"))
        return feedback

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        outline, feedback = parse_result(OutlineResult, result)
        if outline is None:
            return SubmissionOutcome(False, feedback)
        feedback += self.validate(wf, outline)
        if has_errors(feedback):
            return SubmissionOutcome(False, sort_feedback(feedback))
        if wf.pdb.nodes.count():
            return SubmissionOutcome(False, [fb("error", "OUTLINE_EXISTS", "the book already has an architecture; "
                                                                          "use update_outline to change it")])
        wf.pdb.meta.set("book_title", outline.title)
        if outline.subtitle:
            wf.pdb.meta.set("book_subtitle", outline.subtitle)
        if outline.notation_conventions:
            wf.pdb.meta.set_json("notation_conventions", outline.notation_conventions)
        builder = OutlineBuilder(wf)
        vols = _normalized_volumes(outline)
        multi_volume = len(vols) > 1
        for vi, vol in enumerate(vols):
            vparent = None
            if multi_volume:
                vparent = builder.node(NodeKind.VOLUME, None, vi, vol["title"] or f"Volume {vi + 1}")
            for pi, part in enumerate(vol["parts"]):
                pparent = vparent
                if part["title"]:
                    pparent = builder.node(NodeKind.PART, vparent, pi, part["title"])
                for ch in part["chapters"]:
                    builder.chapter(pparent, ch)
        for ch in outline.appendices:
            builder.chapter(None, ch.model_copy(update={"appendix": True}))
        atomic_write_json(wf.project.research_dir / "outline.json", outline.model_dump(mode="json"))
        wf.project.checkpoints.create("architecture accepted")
        return SubmissionOutcome(True, feedback, summary=f"architecture stored: {builder.n_chapters} chapters, "
                                                         f"{builder.n_blocks} blocks; drafting starts",
                                 stored_result={"chapters": builder.n_chapters, "blocks": builder.n_blocks})


class OutlineBuilder:
    def __init__(self, wf):
        self.wf = wf
        self.pdb = wf.pdb
        self.n_chapters = 0
        self.n_blocks = 0
        self._counters: dict[str | None, int] = {}

    def _pos(self, parent: str | None) -> int:
        if parent not in self._counters:
            self._counters[parent] = len(self.pdb.nodes.children(parent))
        pos = self._counters[parent]
        self._counters[parent] += 1
        return pos

    def node(self, kind: NodeKind, parent, _pos: int, title: str, summary: str | None = None, plan: dict | None = None):
        parent_id = parent.id if parent is not None else None
        siblings = {n.slug for n in self.pdb.nodes.children(parent_id)}
        slug = slugify(title, 40, kind.value)
        base, n = slug, 2
        while slug in siblings:
            slug = f"{base}-{n}"
            n += 1
        return self.pdb.nodes.create(kind=kind, parent_id=parent_id, position=self._pos(parent_id), title=title,
                                     slug=slug, summary=summary, plan=plan or {})

    def _unique_chapter_slug(self, title: str) -> str:
        existing = {c.slug for c in self.pdb.nodes.chapters()}
        slug = slugify(title, 40, "chapter")
        base, n = slug, 2
        while slug in existing:
            slug = f"{base}-{n}"
            n += 1
        return slug

    def chapter(self, parent, ch: ChapterPlan):
        parent_id = parent.id if parent is not None else None
        slug = self._unique_chapter_slug(ch.title)
        node = self.pdb.nodes.create(
            kind=NodeKind.CHAPTER, parent_id=parent_id, position=self._pos(parent_id), title=ch.title, slug=slug,
            summary=ch.summary,
            plan={"prerequisites": ch.prerequisites, "appendix": ch.appendix, "detail_later": ch.detail_later,
                  "target_words": ch.target_words},
        )
        self.pdb.labels.define(f"sec-{slug}", "sec", node.id, ch.title)
        self.n_chapters += 1
        if ch.sections:
            self.sections(node, ch.sections)
        else:
            self.wf.queue.enqueue("architecture.chapter", title=f"Plan the sections of chapter '{ch.title}'",
                                  target_id=node.id, priority=30)
        return node

    def sections(self, chapter, sections: list[SectionPlan]) -> None:
        for sec in sections:
            snode = self.node(NodeKind.SECTION, chapter, 0, sec.title, sec.summary)
            self.pdb.labels.define(f"sec-{chapter.slug}-{snode.slug}", "sec", snode.id, sec.title)
            for blk in sec.blocks:
                bnode = self.node(NodeKind.BLOCK, snode, 0, blk.title, blk.summary,
                                  {"elements": blk.elements, "target_words": blk.target_words,
                                   "key_terms": blk.key_terms, "sources": blk.sources})
                self.wf.queue.enqueue("draft.block", title=f"Write '{blk.title}' ({chapter.title})",
                                      target_id=bnode.id, priority=100)
                self.n_blocks += 1


class ChapterDetailHandler(BaseHandler):
    kind = "architecture.chapter"
    result_model = ChapterDetailResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        ctx = ContextBuilder(wf)
        chapter = wf.pdb.nodes.get(task.target_id)
        others = [{"title": c.title, "summary": c.summary} for c in wf.pdb.nodes.chapters()]
        return {
            "instructions": _dedent(f"""
                Plan the sections and blocks of chapter "{chapter.title}" (about
                {chapter.plan.get('target_words') or 'an appropriate number of'} words). Blocks are 250-1500 words,
                each with planned elements, key_terms and sources. Keep consistent with the other chapters.
            """),
            "context": {"book": ctx.book(), "chapter": {"title": chapter.title, "summary": chapter.summary,
                                                        "prerequisites": chapter.plan.get("prerequisites")},
                        "all_chapters": others, "sources": ctx.sources()},
            "output_schema": ChapterDetailResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        detail, feedback = parse_result(ChapterDetailResult, result)
        if detail is None:
            return SubmissionOutcome(False, feedback)
        chapter = wf.pdb.nodes.get(task.target_id)
        if wf.pdb.nodes.children(chapter.id):
            return SubmissionOutcome(False, [fb("error", "ALREADY_DETAILED", "this chapter already has sections")])
        builder = OutlineBuilder(wf)
        builder.sections(chapter, detail.sections)
        wf.pdb.nodes.update(chapter.id, plan={**chapter.plan, "detail_later": False})
        return SubmissionOutcome(True, feedback, summary=f"{builder.n_blocks} blocks planned for '{chapter.title}'")


for _h in (ResearchPlanHandler(), SourcesVerifyHandler(), SourcesReviewHandler(), OutlineHandler(),
           ChapterDetailHandler()):
    register(_h)

from scholarforge.workflow import drafting  # noqa: E402,F401  (registers drafting/review handlers)

try:  # figure handlers live with the illustration engine
    from scholarforge.workflow import figures  # noqa: E402,F401
except ModuleNotFoundError as _exc:  # pragma: no cover - during partial installs only
    if _exc.name != "scholarforge.workflow.figures":
        raise
