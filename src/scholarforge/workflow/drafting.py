"""Drafting, verification, review and revision handlers.

Revision loop and its stop criteria:

1. ``draft.block`` (host) -> engine validation (structure, citations, math checks, terminology,
   notation, figures, lint). Errors are returned for correction (max ``max_attempts``).
2. ``verify.block`` (engine) -> verifies cited sources, asks the host to assess claim support,
   recomputes claim statuses.
3. ``review.chapter`` (host, once per chapter) -> referee findings become issues.
4. ``revise.block`` (host) is created only for blocks with *error* issues or *major/critical*
   review findings, at most ``max_revision_rounds`` times per block. When rounds are exhausted
   the block is marked ``needs_review`` and its open issues go to the final report.
"""

from __future__ import annotations

import textwrap
from typing import Any

from scholarforge.core.workflow import EngineOutcome, SubmissionOutcome
from scholarforge.models.entities import Node, Task
from scholarforge.models.enums import (
    CheckOutcome,
    IssueStatus,
    NodeKind,
    NodeStatus,
    Severity,
    SourceStatus,
    TaskActor,
    TaskStatus,
)
from scholarforge.models.results import (
    BlockDraftResult,
    ChapterReviewResult,
    ClaimSupportResult,
    RecheckResult,
)
from scholarforge.science.claims import compute_status, quote_in_text
from scholarforge.science.consistency import ConsistencyEngine
from scholarforge.science.lint import lint_block
from scholarforge.science.markdown import analyze
from scholarforge.science.mathcheck import MathCheckRunner
from scholarforge.science.sources.formats import short_reference
from scholarforge.science.sources.manager import UNUSABLE, SourceManager
from scholarforge.workflow import register
from scholarforge.workflow.base import BaseHandler, fb, has_errors, parse_result, sort_feedback
from scholarforge.workflow.context import QUARTO_RULES, RESPONSE_RULES, ContextBuilder

HARD_CODES = {
    "SCHEMA", "UNBALANCED_CODE_FENCE", "UNBALANCED_DISPLAY_MATH", "UNBALANCED_INLINE_MATH", "UNBALANCED_DIV",
    "RESERVED_HEADING_LEVEL", "DUPLICATE_LABEL", "DUPLICATE_LABEL_GLOBAL", "FIGURE_MARKER_WITHOUT_SPEC",
    "FIGURE_SPEC_WITHOUT_MARKER", "FIGURE_LABEL_TAKEN", "CLAIM_LABEL_MISSING", "UNKNOWN_CHECK_ID", "EMPTY",
}
CATEGORY = {
    "UNBALANCED": "structure", "RESERVED": "structure", "EMPTY_MATH": "structure", "DUPLICATE_LABEL": "structure",
    "TOO_SHORT": "lint", "SHORT": "lint", "LONG": "lint", "MISSING_ELEMENT": "lint", "FILLER": "lint",
    "REPEATED": "lint", "OVERLAPS": "lint", "COPIED": "lint", "LONG_PARAGRAPH": "lint",
    "UNKNOWN_CITATION": "citation", "CITATION": "citation", "NEW_SOURCE": "citation",
    "UNRESOLVED": "xref", "FORWARD": "xref", "MATH": "math", "TERM": "terminology", "REDEFINITION": "terminology",
    "NOTATION": "notation", "FIGURE": "figure", "CLAIM": "claims", "UNKNOWN_CHECK": "claims",
}
ENGINE_CATEGORIES = ("structure", "lint", "citation", "xref", "math", "terminology", "notation", "figure", "claims")


def category_of(code: str) -> str:
    for prefix, cat in CATEGORY.items():
        if code.startswith(prefix):
            return cat
    return "other"


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


# =================================================================================== validation
class BlockValidation:
    """Validate a block submission; on success keep everything needed to apply it."""

    def __init__(self, wf, node: Node, draft: BlockDraftResult):
        self.wf, self.node, self.draft = wf, node, draft
        self.pdb = wf.pdb
        self.feedback: list[dict[str, Any]] = []
        self.analysis = analyze(draft.markdown)
        self.math_results: list = []
        self.term_new: list = []
        self.term_redefs: list = []
        self.consistency = ConsistencyEngine(wf.pdb)

    def run(self) -> list[dict[str, Any]]:
        d, an = self.draft, self.analysis
        if not d.markdown.strip():
            self.feedback.append(fb("error", "EMPTY", "markdown is empty"))
            return self.feedback
        for p in an.problems:
            self.feedback.append(fb(p.severity, p.code, p.message, line=p.line) if p.line else fb(p.severity, p.code, p.message))
        self._figures()
        self._labels_and_claims()
        self._citations()
        self._refs()
        self._terms_and_notation()
        self._math()
        self._lint()
        return sort_feedback(self.feedback)

    def _figures(self) -> None:
        markers = set(self.analysis.figure_markers)
        specs = {f.label for f in self.draft.figures}
        for m in sorted(markers - specs):
            self.feedback.append(fb("error", "FIGURE_MARKER_WITHOUT_SPEC", f"marker for {m} has no entry in 'figures'"))
        for s in sorted(specs - markers):
            self.feedback.append(fb("error", "FIGURE_SPEC_WITHOUT_MARKER",
                                    f"figure {s} is described but has no <!-- sf:figure {s} --> marker in the text"))
        for f in self.draft.figures:
            existing = self.pdb.figures.by_label(f.label)
            if existing and existing.node_id and existing.node_id != self.node.id:
                self.feedback.append(fb("error", "FIGURE_LABEL_TAKEN", f"figure label {f.label} belongs to another block"))
            if f.renderer == "external" and self.wf.project.config.params.illustrations in ("native_only", "none"):
                self.feedback.append(fb("error", "FIGURE_EXTERNAL_NOT_ALLOWED",
                                        f"{f.label}: external image generation is disabled for this project; "
                                        "use a native renderer (matplotlib, graphviz, tikz, svg)"))

    def _labels_and_claims(self) -> None:
        an = self.analysis
        for label in an.labels:
            owner = self.pdb.labels.owner(label)
            if owner is not None and owner != self.node.id:
                self.feedback.append(fb("error", "DUPLICATE_LABEL_GLOBAL", f"label {label} is already used elsewhere"))
        figure_labels = {f.label for f in self.draft.figures}
        check_ids = {c.id for c in self.draft.math_checks}
        for c in self.draft.claims:
            if c.label and c.label not in an.labels and c.label not in figure_labels:
                self.feedback.append(fb("error", "CLAIM_LABEL_MISSING", f"claim label {c.label} is not defined in the "
                                                                        "markdown"))
            for cid in c.check_ids:
                if cid not in check_ids:
                    self.feedback.append(fb("error", "UNKNOWN_CHECK_ID", f"claim refers to unknown math check '{cid}'"))
        prof = ContextBuilder(self.wf).profile()
        for c in self.draft.claims:
            if c.kind in prof.claims.require_citation_kinds and not c.citations:
                self.feedback.append(fb("warning", "CLAIM_NEEDS_CITATION", f"{c.kind} claim without citation: "
                                                                           f"'{c.text[:80]}'"))
            if prof.claims.speculative_policy == "strict" and c.kind in ("hypothesis", "conjecture") and not c.speculative:
                self.feedback.append(fb("error", "CLAIM_SPECULATIVE_UNLABELLED",
                                        f"{c.kind} must be marked speculative: '{c.text[:80]}'"))

    def _citations(self) -> None:
        mgr = SourceManager(self.wf.project)
        online = self.wf.project.settings.network.enabled
        newly = []
        for cand in self.draft.new_sources:
            src, created = mgr.register(cand, proposed_by=f"host:block:{self.node.id}")
            if created:
                newly.append(src)
        if newly and online:
            for src in newly[:8]:
                mgr.verify(src.id)
        keys = set(self.analysis.citations) | {k for c in self.draft.claims for k in c.citations}
        for key in sorted(keys):
            src = self.pdb.sources.by_citekey(key)
            if src is None:
                hint = " (new_sources entries get their own citekey — use the key returned in feedback)" if newly else ""
                self.feedback.append(fb("error", "UNKNOWN_CITATION", f"[@{key}] is not in the source registry{hint}"))
                continue
            if src.status in UNUSABLE:
                reason = (src.verification or {}).get("reason", "")
                self.feedback.append(fb("error", "CITATION_NOT_USABLE",
                                        f"[@{key}] cannot be cited: {src.status.value}. {reason}"))
            elif src.status == SourceStatus.UNVERIFIABLE:
                self.feedback.append(fb("error", "CITATION_UNVERIFIABLE",
                                        f"[@{key}] could not be verified; add a DOI/arXiv/ISBN via new_sources or "
                                        "remove the citation"))
            elif src.status != SourceStatus.VERIFIED:
                self.feedback.append(fb("warning", "CITATION_PENDING", f"[@{key}] is not verified yet "
                                                                       f"({src.status.value})"))
        for src in newly:
            src = self.pdb.sources.get(src.id)
            self.feedback.append(fb("info", "NEW_SOURCE", f"registered new source as [@{src.citekey}] "
                                                          f"({src.status.value})", citekey=src.citekey))

    def _refs(self) -> None:
        defined_here = set(self.analysis.labels) | {f.label for f in self.draft.figures}
        for ref in sorted(self.analysis.refs):
            if ref in defined_here or self.pdb.labels.owner(ref) is not None:
                continue
            if ref.startswith("sec-"):
                self.feedback.append(fb("error", "UNRESOLVED_REFERENCE", f"@{ref} is not a section label "
                                                                         "(see context.labels)"))
            else:
                self.feedback.append(fb("warning", "FORWARD_REFERENCE", f"@{ref} is not defined yet; it must be "
                                                                        "defined later in the book"))

    def _terms_and_notation(self) -> None:
        findings, self.term_new, self.term_redefs = self.consistency.check_terms(self.node.id, self.draft.terms)
        findings += self.consistency.check_notation(self.node.id, self.draft.notation)
        for f in findings:
            self.feedback.append(fb(f.severity, f.code, f.message, **f.details))

    def _math(self) -> None:
        specs = [c.model_dump(exclude_none=True) for c in self.draft.math_checks]
        runner = MathCheckRunner.from_settings(self.wf.project.settings)
        self.math_results = runner.run(specs)
        for r in self.math_results:
            if r.outcome == "fail":
                self.feedback.append(fb("error", "MATH_CHECK_FAILED", f"math check '{r.id}' ({r.kind}) failed: "
                                                                      f"{r.message}", details=r.details))
            elif r.outcome == "error":
                self.feedback.append(fb("error", "MATH_CHECK_INVALID", f"math check '{r.id}' is invalid: {r.message}"))
            elif r.outcome == "inconclusive":
                self.feedback.append(fb("warning", "MATH_CHECK_INCONCLUSIVE", f"math check '{r.id}': {r.message}"))
        prof = ContextBuilder(self.wf).profile()
        has_display = "$$" in self.draft.markdown
        if prof.verification.math_symbolic in ("recommended", "required") and has_display and not specs:
            sev = "error" if prof.verification.math_symbolic == "required" else "warning"
            self.feedback.append(fb(sev, "MATH_CHECKS_MISSING", "the block contains display formulas but no "
                                                                "math_checks; attach checks to computational claims"))

    def _lint(self) -> None:
        others: dict[str, str] = {}
        chapter = self.pdb.nodes.ancestor_of_kind(self.node.id, NodeKind.CHAPTER)
        chapters = self.pdb.nodes.chapters()
        idx = next((i for i, c in enumerate(chapters) if chapter and c.id == chapter.id), 0)
        scope = chapters[max(0, idx - 1): idx + 1]
        for ch in scope:
            for b in self.pdb.nodes.descendants(ch.id):
                if b.kind == NodeKind.BLOCK and b.id != self.node.id and b.status != NodeStatus.PLANNED:
                    others[b.id] = self.wf.project.content.read_block(b.id) or ""
        plan = self.node.plan
        for p in lint_block(self.draft.markdown, self.analysis, target_words=int(plan.get("target_words", 600)),
                            elements=list(plan.get("elements", [])), other_blocks=others):
            self.feedback.append(fb(p.severity, p.code, p.message))


# ======================================================================================= apply
def apply_block(wf, node: Node, val: BlockValidation, *, origin: str) -> dict[str, Any]:
    pdb, d, an = wf.pdb, val.draft, val.analysis
    node = wf.project.content.write_block(node.id, d.markdown, origin=origin, note=d.changes_summary)
    pdb.claims.replace_for_node(node.id, [c.model_dump() for c in d.claims])
    by_id = {r.id: r for r in val.math_results}
    specs = []
    for c in d.math_checks:
        spec = c.model_dump(exclude_none=True)
        spec["_outcome"] = by_id[c.id].outcome if c.id in by_id else None
        specs.append(spec)
    pdb.assertions.replace_for_node(node.id, specs)
    for r in val.math_results:
        outcome = CheckOutcome(r.outcome) if r.outcome in CheckOutcome._value2member_map_ else CheckOutcome.ERROR
        pdb.checks.add(subject_type="assertion", subject_id=f"{node.id}:{r.id}", check_type=f"math.{r.kind}",
                       method=r.method, independent=r.method in ("symbolic", "formal", "numeric", "dimensional"),
                       outcome=outcome, details={"message": r.message, **r.details})
    pdb.labels.remove_for_node(node.id, keep_kinds=())
    claim_titles = {c.label: c.text for c in d.claims if c.label}
    for label, kind in an.labels.items():
        prefix = label.split("-", 1)[0]
        pdb.labels.define(label, prefix, node.id, an.label_titles.get(label) or claim_titles.get(label, "")[:120])
    for f in d.figures:
        pdb.labels.define(f.label, "fig", node.id, f.caption[:120])
    pdb.labels.set_refs(node.id, an.refs)
    cons = val.consistency
    changed_terms = cons.apply_terms(node.id, val.term_new, val.term_redefs)
    cons.apply_notation(node.id, d.notation)
    cons.update_usage(node.id, d.markdown)
    for term_id in changed_terms:
        dependents = cons.dependents_of_term(term_id)
        if dependents:
            wf.queue.enqueue("consistency.recheck", title="Re-check blocks after a definition change",
                             target_id=term_id, payload={"blocks": dependents}, priority=55, unique=False)
    from scholarforge.workflow.figures import request_figure  # local import: optional subsystem

    for f in d.figures:
        request_figure(wf, node, f)
    pdb.issues.resolve_for_node(node.id, categories=ENGINE_CATEGORIES, origin="engine")
    for item in val.feedback:
        if item["severity"] in ("warning", "error") and item["code"] not in ("NEW_SOURCE",):
            sev = Severity.ERROR if item["severity"] == "error" else Severity.WARNING
            pdb.issues.add(severity=sev, category=category_of(item["code"]), code=item["code"],
                           message=item["message"], node_id=node.id, details=item.get("details") or {})
    pdb.nodes.update(node.id, status=NodeStatus.DRAFTED, written_summary=d.summary)
    wf.queue.enqueue("verify.block", actor=TaskActor.ENGINE, title=f"Verify '{node.title}'", target_id=node.id,
                     priority=30)
    math_summary: dict[str, int] = {}
    for r in val.math_results:
        key = f"{r.outcome}:{r.method}"
        math_summary[key] = math_summary.get(key, 0) + 1
    return {"block_id": node.id, "version": node.version, "words": node.word_count, "math_checks": math_summary,
            "warnings": sum(1 for f in val.feedback if f["severity"] == "warning")}


# ===================================================================================== drafting
class DraftBlockHandler(BaseHandler):
    kind = "draft.block"
    result_model = BlockDraftResult
    origin = "host"

    def instructions(self, wf, node: Node, pack: dict[str, Any]) -> str:
        pos = pack["position"]
        plan = node.plan
        tw = int(plan.get("target_words", 600))
        return _dedent(f"""
            Write block "{node.title}" — section {pos['section']['number']} "{pos['section']['title']}" of chapter
            {pos['chapter']['number']} "{pos['chapter']['title']}" of the book "{pack['book']['title']}".
            Language: {pack['book']['language']}. Level: {pack['book']['level']}. Depth: {pack['book']['depth']}.
            Planned content: {node.summary}
            Planned elements: {', '.join(plan.get('elements', [])) or 'as appropriate'}.
            Target length: about {tw} words (accepted range {max(60, int(tw * 0.4))}-{int(tw * 1.6)}).
            Continue seamlessly from previous.previous_block_ending; do not repeat what previous.this_chapter
            already covered, and do not anticipate later_in_chapter.
            Declare new terms in 'terms' and new symbols in 'notation'; reuse registry.terms and
            registry.notation exactly. Record the main assertions in 'claims' (kinds: theorem, lemma, proposition,
            corollary, fact, empirical, historical, hypothesis, conjecture, interpretation, statistical, clinical)
            with citations; mark open questions speculative.
            Follow book.profile.writing_guidance.

            {QUARTO_RULES}

            {RESPONSE_RULES}
        """)

    def render(self, wf, task: Task) -> dict[str, Any]:
        node = wf.pdb.nodes.get(task.target_id)
        pack = ContextBuilder(wf).block_pack(node)
        return {"instructions": self.instructions(wf, node, pack), "context": pack,
                "output_schema": BlockDraftResult.model_json_schema()}

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        draft, feedback = parse_result(BlockDraftResult, result)
        if draft is None:
            return SubmissionOutcome(False, feedback)
        node = wf.pdb.nodes.get(task.target_id)
        val = BlockValidation(wf, node, draft)
        feedback = val.run()
        if has_errors(feedback):
            task.payload["_last_validation"] = True
            return SubmissionOutcome(False, feedback, summary="the block has errors; fix them and resubmit")
        info = self.apply(wf, node, val)
        return SubmissionOutcome(True, feedback, summary=f"block stored (v{info['version']}, {info['words']} words)",
                                 data=info, stored_result=info)

    def apply(self, wf, node: Node, val: BlockValidation) -> dict[str, Any]:
        return apply_block(wf, node, val, origin=self.origin)

    def on_exhausted(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome | None:
        draft, schema_fb = parse_result(BlockDraftResult, result)
        if draft is None:
            return None
        node = wf.pdb.nodes.get(task.target_id)
        val = BlockValidation(wf, node, draft)
        feedback = val.run()
        if any(f["severity"] == "error" and f["code"] in HARD_CODES for f in feedback):
            return None  # structurally broken content is never stored
        info = self.apply(wf, node, val)
        wf.pdb.nodes.update(node.id, status=NodeStatus.NEEDS_REVIEW)
        return SubmissionOutcome(True, feedback, summary="attempts exhausted: stored with open errors, block marked "
                                                         "needs_review", data=info, stored_result=info)


class ReviseBlockHandler(DraftBlockHandler):
    kind = "revise.block"
    origin = "revision"

    def render(self, wf, task: Task) -> dict[str, Any]:
        node = wf.pdb.nodes.get(task.target_id)
        pack = ContextBuilder(wf).block_pack(node)
        issues = [{"id": i.id, "severity": i.severity.value, "category": i.category, "message": i.message,
                   "suggestion": (i.details or {}).get("suggestion", "")}
                  for i in wf.pdb.issues.list(node_id=node.id, min_severity=Severity.WARNING)]
        current = wf.project.content.read_block(node.id) or ""
        body = super().instructions(wf, node, pack)
        return {
            "instructions": _dedent(f"""
                REVISION (round {node.revision_rounds + 1} of {wf.project.settings.workflow.max_revision_rounds}).
                Rewrite this block to resolve the issues listed in context.issues while keeping everything that is
                correct. Return the COMPLETE revised block (same schema as a new draft, including terms, claims and
                math_checks) and summarise the changes in changes_summary.
            """) + "\n\n" + body,
            "context": {**pack, "current_markdown": current, "issues": issues},
            "output_schema": BlockDraftResult.model_json_schema(),
        }

    def apply(self, wf, node: Node, val: BlockValidation) -> dict[str, Any]:
        info = apply_block(wf, node, val, origin="revision")
        node = wf.pdb.nodes.get(node.id)
        wf.pdb.nodes.update(node.id, revision_rounds=node.revision_rounds + 1)
        wf.pdb.issues.resolve_for_node(node.id, categories=("review", "claims"))
        return info


# ================================================================================= verification
def claim_assessments(wf, claim_id: str) -> list[dict[str, Any]]:
    out = []
    for chk in wf.pdb.checks.for_subject("claim", claim_id):
        if chk.check_type == "claim.support":
            out.append(chk.details)
    return out


def update_claim_statuses(wf, node: Node) -> dict[str, int]:
    prof = ContextBuilder(wf).profile()
    sources = {s.citekey: s for s in wf.pdb.sources.list()}
    math: dict[str, dict[str, Any]] = {}
    for a in wf.pdb.assertions.for_node(node.id):
        checks = wf.pdb.checks.for_subject("assertion", f"{node.id}:{a['local_id']}")
        if checks:
            last = checks[-1]
            math[a["local_id"]] = {"id": a["local_id"], "outcome": last.outcome.value, "method": last.method,
                                   "message": last.details.get("message", ""),
                                   "scope": a["spec"].get("scope", "general")}
    text = wf.project.content.read_block(node.id) or ""
    has_proof = ".proof" in text
    counts: dict[str, int] = {}
    for claim in wf.pdb.claims.for_node(node.id):
        status, reason = compute_status(claim, math_results=math, sources=sources,
                                        assessments=claim_assessments(wf, claim.id), profile=prof,
                                        has_proof_text=has_proof and bool(claim.label))
        wf.pdb.claims.set_status(claim.id, status, reason)
        counts[status.value] = counts.get(status.value, 0) + 1
    return counts


def post_verification(wf, node: Node) -> str:
    """Decide the block's next step after checks: revise, mark verified, or needs_review."""
    node = wf.pdb.nodes.get(node.id)
    max_rounds = wf.project.settings.workflow.max_revision_rounds
    errors = wf.pdb.issues.list(node_id=node.id, min_severity=Severity.ERROR)
    open_revise = wf.pdb.tasks.open_for("revise.block", node.id)
    open_support = wf.pdb.tasks.open_for("claims.support", node.id)
    if errors:
        if open_revise:
            return "revision pending"
        if node.revision_rounds < max_rounds:
            wf.queue.enqueue("revise.block", title=f"Revise '{node.title}' ({len(errors)} errors)",
                             target_id=node.id, priority=70)
            wf.pdb.nodes.update(node.id, status=NodeStatus.REVISING)
            return "revision requested"
        wf.pdb.nodes.update(node.id, status=NodeStatus.NEEDS_REVIEW)
        for issue in errors:
            wf.pdb.issues.set_status([issue.id], IssueStatus.EXPERT_REVIEW)
        return "needs expert review (revision rounds exhausted)"
    if not open_support and node.status in (NodeStatus.DRAFTED, NodeStatus.REVISING):
        wf.pdb.nodes.update(node.id, status=NodeStatus.VERIFIED)
        return "verified (automatic checks passed)"
    return "awaiting claim assessment" if open_support else node.status.value


def maybe_review_chapter(wf, node: Node) -> None:
    if not wf.project.settings.workflow.chapter_review:
        return
    chapter = wf.pdb.nodes.ancestor_of_kind(node.id, NodeKind.CHAPTER)
    if chapter is None or wf.pdb.meta.get(f"chapter_reviewed:{chapter.id}"):
        return
    blocks = [b for b in wf.pdb.nodes.descendants(chapter.id) if b.kind == NodeKind.BLOCK]
    if any(b.status == NodeStatus.PLANNED for b in blocks):
        return
    ids = {b.id for b in blocks}
    busy = [t for t in wf.pdb.tasks.list(status=[TaskStatus.PENDING, TaskStatus.LEASED])
            if t.target_id in ids and t.kind in ("draft.block", "revise.block", "claims.support", "verify.block")]
    if busy:
        return
    wf.queue.enqueue("review.chapter", title=f"Referee review of chapter '{chapter.title}'", target_id=chapter.id,
                     priority=60)


class VerifyBlockHandler(BaseHandler):
    kind = "verify.block"
    actor = TaskActor.ENGINE

    def run(self, wf, task: Task) -> EngineOutcome:
        node = wf.pdb.nodes.get(task.target_id)
        if node.status == NodeStatus.PLANNED:
            return EngineOutcome(summary="block not written yet")
        mgr = SourceManager(wf.project)
        claims = wf.pdb.claims.for_node(node.id)
        if wf.project.settings.network.enabled:
            for key in {k for c in claims for k in c.citations}:
                src = wf.pdb.sources.by_citekey(key)
                if src and src.status in (SourceStatus.PROPOSED, SourceStatus.PENDING):
                    mgr.verify(src.id)
        prof = ContextBuilder(wf).profile()
        need = []
        if prof.verification.claim_support != "off":
            for c in claims:
                if c.citations and not claim_assessments(wf, c.id):
                    cited = [wf.pdb.sources.by_citekey(k) for k in c.citations]
                    if all(s is not None and s.status == SourceStatus.VERIFIED for s in cited):
                        need.append(c.id)
        if need and not wf.pdb.tasks.open_for("claims.support", node.id):
            wf.queue.enqueue("claims.support", title=f"Assess source support for {len(need)} claims in '{node.title}'",
                             target_id=node.id, payload={"claim_ids": need}, priority=50)
        counts = update_claim_statuses(wf, node)
        step = post_verification(wf, node)
        maybe_review_chapter(wf, node)
        return EngineOutcome(summary=f"claims {counts}; {step}", data={"claims": counts, "next": step})


class ClaimSupportHandler(BaseHandler):
    kind = "claims.support"
    result_model = ClaimSupportResult

    def _items(self, wf, task: Task) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        claims = []
        sources: dict[str, dict[str, Any]] = {}
        for cid in task.payload.get("claim_ids", []):
            try:
                c = wf.pdb.claims.get(cid)
            except Exception:
                continue
            claims.append({"claim_id": c.id, "kind": c.kind, "text": c.text, "citations": c.citations})
            for key in c.citations:
                src = wf.pdb.sources.by_citekey(key)
                if src and key not in sources:
                    sources[key] = {"citekey": key, "reference": short_reference(src),
                                    "abstract": (src.abstract or "")[:3500] or None}
        return claims, sources

    def render(self, wf, task: Task) -> dict[str, Any]:
        claims, sources = self._items(wf, task)
        return {
            "instructions": _dedent("""
                For every (claim, cited source) pair, judge whether the source supports the claim:
                supports / partially_supports / does_not_support / contradicts / cannot_determine.
                Give evidence_quote as a VERBATIM excerpt of the provided abstract whenever one supports your
                verdict; ScholarForge checks the quote against the retrieved text, and only matching quotes count
                as independent evidence. If no abstract is provided, you may rely on your knowledge of the work, but
                your judgement is recorded as a model assessment, not as verification. Be strict: a famous work
                does not support claims it does not make.
            """),
            "context": {"claims": claims, "sources": list(sources.values())},
            "output_schema": ClaimSupportResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        res, feedback = parse_result(ClaimSupportResult, result)
        if res is None:
            return SubmissionOutcome(False, feedback)
        claims, sources = self._items(wf, task)
        valid_pairs = {(c["claim_id"], k) for c in claims for k in c["citations"]}
        seen = set()
        for a in res.assessments:
            if (a.claim_id, a.citekey) not in valid_pairs:
                feedback.append(fb("error", "UNKNOWN_PAIR", f"({a.claim_id}, {a.citekey}) is not part of this task"))
        if has_errors(feedback):
            return SubmissionOutcome(False, feedback)
        for a in res.assessments:
            seen.add((a.claim_id, a.citekey))
            abstract = (sources.get(a.citekey) or {}).get("abstract")
            quote_ok = quote_in_text(a.evidence_quote, abstract) if a.evidence_quote else False
            outcome = {"supports": CheckOutcome.PASS, "partially_supports": CheckOutcome.PASS,
                       "does_not_support": CheckOutcome.FAIL, "contradicts": CheckOutcome.FAIL}.get(
                a.verdict, CheckOutcome.INCONCLUSIVE)
            wf.pdb.checks.add(subject_type="claim", subject_id=a.claim_id, check_type="claim.support",
                              method="host_llm", independent=False, outcome=outcome,
                              details={"citekey": a.citekey, "verdict": a.verdict, "quote_verified": quote_ok,
                                       "evidence_quote": a.evidence_quote, "notes": a.notes,
                                       "abstract_available": bool(abstract)})
            if a.evidence_quote:
                wf.pdb.checks.add(subject_type="claim", subject_id=a.claim_id, check_type="claim.quote",
                                  method="text_match", independent=True,
                                  outcome=CheckOutcome.PASS if quote_ok else (CheckOutcome.FAIL if abstract
                                                                              else CheckOutcome.SKIPPED),
                                  details={"citekey": a.citekey})
                if abstract and not quote_ok:
                    feedback.append(fb("warning", "QUOTE_NOT_FOUND", f"evidence quote for {a.citekey} does not "
                                                                     "occur in the retrieved abstract"))
        for pair in valid_pairs - seen:
            feedback.append(fb("warning", "PAIR_NOT_ASSESSED", f"no assessment for claim {pair[0]} / {pair[1]}"))
        node = wf.pdb.nodes.get(task.target_id)
        counts = update_claim_statuses(wf, node)
        for claim in wf.pdb.claims.for_node(node.id):
            if claim.status.value == "contested" and not claim.speculative and claim.kind not in ("hypothesis",
                                                                                                  "conjecture"):
                wf.pdb.issues.add(severity=Severity.ERROR, category="claims", code="CLAIM_NOT_SUPPORTED",
                                  message=f"claim not supported by its citation: '{claim.text[:120]}' — "
                                          f"{claim.status_reason}", node_id=node.id)
        # the claims.support task is still open while we are here; post_verification must not wait for it
        wf.queue.complete(task.id, {"claims": counts})
        step = post_verification(wf, node)
        maybe_review_chapter(wf, node)
        return SubmissionOutcome(True, feedback, summary=f"claims now {counts}; {step}", data={"claims": counts},
                                 stored_result={"claims": counts})


class ChapterReviewHandler(BaseHandler):
    kind = "review.chapter"
    result_model = ChapterReviewResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        chapter = wf.pdb.nodes.get(task.target_id)
        ctx = ContextBuilder(wf)
        blocks = ctx.chapter_text(chapter)
        ids = {b["block_id"] for b in blocks}
        engine_issues = [{"block_id": i.node_id, "severity": i.severity.value, "message": i.message}
                         for i in wf.pdb.issues.list(min_severity=Severity.WARNING) if i.node_id in ids]
        return {
            "instructions": _dedent(f"""
                Act as a critical expert referee for chapter "{chapter.title}". Read the blocks in order and report
                substantive problems: logical gaps, mathematical or factual errors, unjustified steps, inconsistent
                notation or terminology, redundancy, content missing from the plan, level mismatch, weak or wrong
                citations. Each finding names the block_id, a severity (minor, major, critical), a category and a
                concrete suggestion. Major and critical findings trigger a revision of that block (bounded number
                of rounds). Do not pad the review: an empty findings list is fine for a sound chapter.
                Remember: this review is a quality pass, not independent verification.
            """),
            "context": {"book": ctx.book(), "chapter": {"title": chapter.title, "summary": chapter.summary},
                        "blocks": blocks, "engine_issues": engine_issues},
            "output_schema": ChapterReviewResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        review, feedback = parse_result(ChapterReviewResult, result)
        if review is None:
            return SubmissionOutcome(False, feedback)
        chapter = wf.pdb.nodes.get(task.target_id)
        blocks = {b.id: b for b in wf.pdb.nodes.descendants(chapter.id) if b.kind == NodeKind.BLOCK}
        bad = [f.block_id for f in review.findings if f.block_id not in blocks]
        if bad:
            return SubmissionOutcome(False, [fb("error", "UNKNOWN_BLOCK", f"block ids not in this chapter: {bad}")])
        sev_map = {"minor": Severity.INFO, "major": Severity.WARNING, "critical": Severity.ERROR}
        to_revise: set[str] = set()
        for f in review.findings:
            wf.pdb.issues.add(severity=sev_map[f.severity], category="review", code=f"REVIEW_{f.category.upper()}",
                              message=f.description, node_id=f.block_id, origin="host_review",
                              details={"suggestion": f.suggestion, "review_severity": f.severity})
            if f.severity in ("major", "critical"):
                to_revise.add(f.block_id)
        wf.pdb.meta.set(f"chapter_reviewed:{chapter.id}", "1")
        max_rounds = wf.project.settings.workflow.max_revision_rounds
        revised = 0
        for bid, block in blocks.items():
            if bid in to_revise and block.revision_rounds < max_rounds:
                wf.queue.enqueue("revise.block", title=f"Revise '{block.title}' after review", target_id=bid,
                                 priority=70)
                wf.pdb.nodes.update(bid, status=NodeStatus.REVISING)
                revised += 1
            elif bid in to_revise:
                wf.pdb.nodes.update(bid, status=NodeStatus.NEEDS_REVIEW)
        atomic = wf.project.reports_dir / f"review-{chapter.slug}.md"
        from scholarforge.util.atomic import atomic_write_text

        lines = [f"# Referee review: {chapter.title}", "", review.overall_assessment, ""]
        lines += [f"- **{f.severity}** [{f.category}] {blocks[f.block_id].title}: {f.description}"
                  + (f" — *{f.suggestion}*" if f.suggestion else "") for f in review.findings]
        atomic_write_text(atomic, "\n".join(lines) + "\n")
        return SubmissionOutcome(True, feedback, summary=f"{len(review.findings)} findings; {revised} blocks queued "
                                                         "for revision")


class RecheckHandler(BaseHandler):
    kind = "consistency.recheck"
    result_model = RecheckResult

    def render(self, wf, task: Task) -> dict[str, Any]:
        term = wf.pdb.terms.by_id(task.target_id)
        history = wf.pdb.terms.history(term.id)
        old = history[-2]["definition"] if len(history) >= 2 else ""
        items = []
        for bid in task.payload.get("blocks", []):
            node = wf.pdb.nodes.find(bid)
            if node is None:
                continue
            items.append({"block_id": bid, "title": node.title, "markdown": wf.project.content.read_block(bid) or ""})
        return {
            "instructions": _dedent(f"""
                The definition of "{term.term}" changed. Check each block that uses the term: if the text relies on
                the old definition, return action "revised" with the complete corrected markdown; otherwise
                "no_change". Keep all labels, citations and figure markers intact.
            """),
            "context": {"term": term.term, "old_definition": old, "new_definition": term.definition, "blocks": items},
            "output_schema": RecheckResult.model_json_schema(),
        }

    def submit(self, wf, task: Task, result: dict[str, Any]) -> SubmissionOutcome:
        res, feedback = parse_result(RecheckResult, result)
        if res is None:
            return SubmissionOutcome(False, feedback)
        allowed = set(task.payload.get("blocks", []))
        for item in res.items:
            if item.block_id not in allowed:
                feedback.append(fb("error", "UNKNOWN_BLOCK", f"{item.block_id} is not part of this re-check"))
            elif item.action == "revised":
                an = analyze(item.markdown or "")
                for p in an.problems:
                    if p.severity == "error":
                        feedback.append(fb("error", p.code, f"{item.block_id}: {p.message}"))
                known = wf.pdb.sources.citekeys()
                for key in an.citations - known:
                    feedback.append(fb("error", "UNKNOWN_CITATION", f"{item.block_id}: [@{key}] unknown"))
        if has_errors(feedback):
            return SubmissionOutcome(False, feedback)
        revised = 0
        for item in res.items:
            if item.action == "revised":
                node = wf.project.content.write_block(item.block_id, item.markdown or "", origin="consistency",
                                                      note=item.note)
                an = analyze(item.markdown or "")
                wf.pdb.labels.set_refs(node.id, an.refs)
                ConsistencyEngine(wf.pdb).update_usage(node.id, item.markdown or "")
                wf.queue.enqueue("verify.block", actor=TaskActor.ENGINE, title=f"Re-verify '{node.title}'",
                                 target_id=node.id, priority=30)
                revised += 1
        for missing in allowed - {i.block_id for i in res.items}:
            feedback.append(fb("warning", "NOT_RECHECKED", f"block {missing} was not re-checked"))
        return SubmissionOutcome(True, feedback, summary=f"{revised} blocks revised for consistency")


for _h in (DraftBlockHandler(), ReviseBlockHandler(), VerifyBlockHandler(), ClaimSupportHandler(),
           ChapterReviewHandler(), RecheckHandler()):
    register(_h)
