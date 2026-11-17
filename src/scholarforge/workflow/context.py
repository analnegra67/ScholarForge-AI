"""Context packs: the minimum the host needs to do one task well.

The host model has a finite context window and may be a fresh session, so each
task carries its own compact, budgeted context instead of relying on the
conversation history: book parameters, position in the outline, summaries of
what precedes, the relevant part of the terminology/notation registry, citable
sources and existing cross-reference labels.
"""

from __future__ import annotations

from typing import Any

from scholarforge.models.entities import Node
from scholarforge.models.enums import NodeKind, NodeStatus
from scholarforge.profiles.registry import get_registry
from scholarforge.science.sources.manager import SourceManager
from scholarforge.util.atomic import read_json
from scholarforge.util.text import estimate_tokens, last_paragraphs, normalize_for_match, truncate

QUARTO_RULES = """\
Quarto Markdown conventions (mandatory):
- Do not write the chapter (#) or section (##) heading: they are generated. Use ### and deeper only.
- Theorem-like environments: ::: {#thm-short-name}  ... :::  (prefixes: thm lem prp cor cnj def exm exr sol rem alg).
  A title may be given as ::: {#thm-chain-rule name="Chain rule"}.
- Proofs: ::: {.proof} ... :::   (immediately after the result they prove).
- Display equations that are referenced: $$ ... $$ {#eq-short-name}. Inline math: $...$.
- Cross-references: @thm-..., @eq-..., @fig-..., @sec-... — only labels that already exist (see 'labels')
  or that you define in this block.
- Citations: [@citekey] or [@a; @b, p. 12] using ONLY citekeys listed in 'sources'. Never invent a reference.
  To use a work that is not listed, add it to 'new_sources' (with DOI / arXiv / ISBN if you know them);
  it is verified against Crossref/OpenAlex/arXiv before it may be cited.
- Figures: put a line <!-- sf:figure fig-name --> where the figure belongs and describe it in 'figures'.
- No raw HTML, no images by URL, no tables wider than the text block."""

RESPONSE_RULES = """\
Respond by calling submit_task with a JSON object matching output_schema. Keep math_checks machine-checkable
(SymPy syntax: x**2, exp(x), log(x), sqrt(x), diff(f, x), Matrix([[a, b], [c, d]]), transpose(A)).
Attach a math check to every formula you can (identity, derivative, gradient, integral, limit, series,
matrix_identity, inequality, solve, numeric, dimension) and reference its id in the claim's check_ids."""


class ContextBuilder:
    def __init__(self, wf):
        self.wf = wf
        self.project = wf.project
        self.pdb = wf.pdb

    # --------------------------------------------------------------------------- shared parts
    def profile(self):
        reg = get_registry()
        pid = self.pdb.meta.get("profile_id") or "generic-science"
        overrides = self.pdb.meta.get_json("profile_overrides", None)
        return reg.with_overrides(pid, overrides)

    def book(self) -> dict[str, Any]:
        p = self.project.config.params
        prof = self.profile()
        out = {
            "title": self.pdb.meta.get("book_title") or self.project.config.title,
            "topic": p.topic, "language": p.language, "level": p.level, "audience": p.audience,
            "depth": p.depth, "book_type": p.book_type, "style": p.style, "required_topics": p.required_topics,
            "elements": p.elements, "user_request": truncate(p.request or "", 1500),
            "profile": {"id": prof.id, "name": prof.name, "writing_guidance": prof.writing_guidance.strip(),
                        "conventions": prof.presentation.conventions, "environments": prof.presentation.environments,
                        "evidence_types": prof.evidence_types, "safety_notes": prof.safety_notes},
        }
        conventions = self.research_plan().get("conventions") or []
        if conventions:
            out["conventions"] = conventions
        return out

    def research_plan(self) -> dict[str, Any]:
        path = self.project.research_dir / "plan.json"
        return read_json(path) if path.exists() else {}

    def sources(self, limit: int = 120) -> list[dict[str, Any]]:
        return SourceManager(self.project).for_context(limit)

    def numbering(self, node: Node) -> str:
        """Dotted number such as 3.2 for a section (chapters counted across the book)."""
        chapters = [c for c in self.pdb.nodes.chapters() if not c.plan.get("appendix")]
        if node.kind == NodeKind.CHAPTER:
            return str(chapters.index(node) + 1) if node in chapters else "A"
        if node.kind == NodeKind.SECTION:
            ch = self.pdb.nodes.ancestor_of_kind(node.id, NodeKind.CHAPTER)
            return f"{self.numbering(ch)}.{node.position + 1}" if ch else str(node.position + 1)
        return ""

    # ---------------------------------------------------------------------------- block pack
    def block_pack(self, node: Node, *, budget_tokens: int = 9000) -> dict[str, Any]:
        section = self.pdb.nodes.get(node.parent_id)
        chapter = self.pdb.nodes.ancestor_of_kind(node.id, NodeKind.CHAPTER)
        blocks_in_chapter = [b for b in self.pdb.nodes.descendants(chapter.id) if b.kind == NodeKind.BLOCK]
        idx = next(i for i, b in enumerate(blocks_in_chapter) if b.id == node.id)
        earlier = blocks_in_chapter[:idx]
        prev_written = [b for b in earlier if b.status != NodeStatus.PLANNED]
        tail = ""
        if prev_written:
            text = self.project.content.read_block(prev_written[-1].id) or ""
            tail = last_paragraphs(text, 2, 1800)
        chapters = self.pdb.nodes.chapters()
        ch_idx = next(i for i, c in enumerate(chapters) if c.id == chapter.id)
        prev_chapters = []
        for c in chapters[:ch_idx]:
            done = [b for b in self.pdb.nodes.descendants(c.id) if b.kind == NodeKind.BLOCK and b.written_summary]
            prev_chapters.append({"title": c.title, "summary": truncate(c.summary or "", 400),
                                  "written": truncate(" ".join(b.written_summary for b in done), 600)})
        key_terms = {normalize_for_match(t) for t in node.plan.get("key_terms", [])}
        terms = []
        all_terms = self.pdb.terms.list()
        chapter_block_ids = {b.id for b in blocks_in_chapter}
        for t in all_terms:
            relevant = (t.normalized in key_terms or t.defined_in in chapter_block_ids or len(all_terms) <= 80)
            if relevant:
                terms.append({"term": t.term, "definition": truncate(t.definition, 400), "aliases": t.aliases})
        notation = [{"symbol": n.symbol, "meaning": n.meaning, "scope": n.scope} for n in self.pdb.notation.list()][:150]
        labels = []
        for label, info in self.pdb.labels.all().items():
            owner = info.get("node_id")
            if info["kind"] in ("sec",) or owner in chapter_block_ids or info["kind"] in ("thm", "lem", "def", "prp",
                                                                                            "cor", "eq", "fig"):
                labels.append({"label": label, "title": info.get("title") or ""})
        pack = {
            "book": self.book(),
            "position": {
                "chapter": {"number": self.numbering(chapter), "title": chapter.title,
                            "summary": chapter.summary, "label": f"sec-{chapter.slug}"},
                "section": {"number": self.numbering(section), "title": section.title, "summary": section.summary,
                            "label": f"sec-{chapter.slug}-{section.slug}"},
                "block": {"title": node.title, "summary": node.summary, **node.plan},
                "block_index_in_chapter": idx + 1, "blocks_in_chapter": len(blocks_in_chapter),
                "is_first_block_of_chapter": idx == 0, "is_last_block_of_chapter": idx == len(blocks_in_chapter) - 1,
            },
            "previous": {
                "this_chapter": [{"title": b.title, "summary": b.written_summary or b.summary} for b in earlier],
                "previous_block_ending": tail,
                "previous_chapters": prev_chapters,
                "later_in_chapter": [{"title": b.title, "summary": b.summary} for b in blocks_in_chapter[idx + 1:]],
            },
            "registry": {"terms": terms, "notation": notation},
            "labels": labels[:250],
            "sources": self.sources(),
        }
        # enforce the budget by trimming the least important lists
        while estimate_tokens(str(pack)) > budget_tokens:
            if len(pack["previous"]["previous_chapters"]) > 3:
                pack["previous"]["previous_chapters"] = pack["previous"]["previous_chapters"][-3:]
            elif len(pack["labels"]) > 60:
                pack["labels"] = pack["labels"][-60:]
            elif len(pack["registry"]["terms"]) > 40:
                pack["registry"]["terms"] = pack["registry"]["terms"][:40]
            elif len(pack["sources"]) > 40:
                wanted = set(node.plan.get("sources", []))
                pack["sources"] = [s for s in pack["sources"] if s["citekey"] in wanted] + [
                    s for s in pack["sources"] if s["citekey"] not in wanted][: 40 - len(wanted)]
            elif len(pack["registry"]["notation"]) > 60:
                pack["registry"]["notation"] = pack["registry"]["notation"][:60]
            else:
                break
        return pack

    def chapter_text(self, chapter: Node, max_chars: int = 60000) -> list[dict[str, Any]]:
        out = []
        used = 0
        for b in self.pdb.nodes.descendants(chapter.id):
            if b.kind != NodeKind.BLOCK:
                continue
            text = self.project.content.read_block(b.id) or ""
            if used + len(text) > max_chars:
                out.append({"block_id": b.id, "title": b.title, "summary": b.written_summary,
                            "markdown": "[omitted for length — see summary]"})
                continue
            used += len(text)
            out.append({"block_id": b.id, "title": b.title, "markdown": text})
        return out
