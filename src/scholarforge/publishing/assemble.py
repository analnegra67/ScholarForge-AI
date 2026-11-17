"""Assemble a Quarto book project from the stored sources (no model calls).

The generated project is fully self-contained (Markdown chapters, figures,
CSL-JSON bibliography, LaTeX preambles, ``_quarto.yml``) and is exported as the
"Quarto source" deliverable, so a book can always be rebuilt or edited by hand.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw

from scholarforge.models.entities import Node
from scholarforge.models.enums import ClaimStatus, FigureStatus, NodeKind, Severity, SourceStatus
from scholarforge.publishing import latex
from scholarforge.science.markdown import FIG_MARKER_RE, insert_index_entry
from scholarforge.science.sources.manager import SourceManager
from scholarforge.util.atomic import atomic_write_text
from scholarforge.version import __version__

LANG_NAMES = {"en": "english", "ru": "russian", "uk": "ukrainian", "de": "ngerman", "fr": "french", "es": "spanish",
              "it": "italian", "pt": "portuguese", "pl": "polish"}


@dataclass
class AssembledBook:
    root: Path
    chapters: list[str] = field(default_factory=list)
    appendices: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    missing_figures: list[str] = field(default_factory=list)
    cited: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def _yaml_str(text: str) -> str:
    return text.replace('"', "'")


def placeholder_figure(directory: Path, label: str, caption: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1800, 700), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 1789, 689], outline="black", width=6)
    draw.text((60, 300), f"Figure {label} is not available yet", fill="black")
    draw.text((60, 360), caption[:120], fill="black")
    img.save(directory / f"{label}.png", dpi=(300, 300))
    img.save(directory / f"{label}.pdf", "PDF", resolution=300.0)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="230" viewBox="0 0 600 230">'
           f'<rect x="2" y="2" width="596" height="226" fill="white" stroke="black" stroke-width="3"/>'
           f'<text x="20" y="110" font-family="sans-serif" font-size="18">Figure {label} is not available yet'
           f'</text></svg>')
    (directory / f"{label}.svg").write_text(svg, encoding="utf-8")


def write_svg_wrapper(png: Path, target: Path) -> None:
    import base64

    with Image.open(png) as im:
        w, h = im.size
    data = base64.b64encode(png.read_bytes()).decode("ascii")
    target.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                      f'viewBox="0 0 {w} {h}" width="{w // 4}" height="{h // 4}"><image width="{w}" height="{h}" '
                      f'xlink:href="data:image/png;base64,{data}"/></svg>', encoding="utf-8")


class BookAssembler:
    def __init__(self, project, *, edition: str = "screen"):
        self.project = project
        self.pdb = project.db
        self.cfg = project.config
        self.edition = edition  # "screen" (HTML/EPUB/PDF) or "print"

    # ---------------------------------------------------------------------------------- api
    def assemble(self, target: Path) -> AssembledBook:
        if target.exists():
            for child in target.iterdir():
                if child.name in ("_output", ".quarto"):
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        target.mkdir(parents=True, exist_ok=True)
        book = AssembledBook(root=target)
        figdir = target / "figures"
        figdir.mkdir(exist_ok=True)
        self._copy_figures(figdir, book)
        index_terms = self._index_terms()
        chapters = self.pdb.nodes.chapters()
        layout: list[Any] = []
        current_part: dict[str, Any] | None = None
        for n, ch in enumerate(chapters, start=1):
            fname = f"chapters/{n:02d}-{ch.slug}.qmd"
            atomic_write_text(target / fname, self._chapter_markdown(ch, index_terms, book))
            if ch.plan.get("appendix"):
                book.appendices.append(fname)
                continue
            part = self._part_of(ch)
            if part is not None:
                if current_part is None or current_part["id"] != part.id:
                    current_part = {"id": part.id, "part": part.title, "chapters": []}
                    layout.append(current_part)
                current_part["chapters"].append(fname)
            else:
                current_part = None
                layout.append(fname)
            book.chapters.append(fname)
        extra = self._back_matter(target, book)
        mgr = SourceManager(self.project)
        bib = mgr.write_bibliography(target, cited_only=book.cited or None)
        atomic_write_text(target / "index.qmd", self._front_matter())
        latex.write_preambles(target, self.project, edition=self.edition)
        quarto_cfg = self._quarto_yml(layout, book.appendices + extra, bib_count=int(bib["count"]))
        atomic_write_text(target / "_quarto.yml", yaml.safe_dump(quarto_cfg, sort_keys=False, allow_unicode=True,
                                                                 width=100))
        atomic_write_text(target / "styles.css", latex.HTML_CSS)
        return book

    # ------------------------------------------------------------------------------ helpers
    def _part_of(self, chapter: Node) -> Node | None:
        if chapter.parent_id is None:
            return None
        parent = self.pdb.nodes.get(chapter.parent_id)
        return parent if parent.kind == NodeKind.PART else None

    def _copy_figures(self, figdir: Path, book: AssembledBook) -> None:
        for fig in self.pdb.figures.list():
            outputs = fig.outputs or {}
            ok = fig.status in (FigureStatus.RENDERED, FigureStatus.APPROVED, FigureStatus.NEEDS_REVIEW) and outputs
            if ok:
                for ext, rel in outputs.items():
                    src = self.project.root / rel
                    if src.exists():
                        shutil.copy2(src, figdir / f"{fig.label}.{ext}")
                if "svg" not in outputs and "png" in outputs:
                    # HTML uses .svg by default: wrap the raster in a self-contained SVG (images referenced from an
                    # SVG loaded through <img> must be inline data URIs)
                    write_svg_wrapper(self.project.root / outputs["png"], figdir / f"{fig.label}.svg")
                book.figures.append(fig.label)
            else:
                placeholder_figure(figdir, fig.label, fig.caption)
                book.missing_figures.append(fig.label)

    def _index_terms(self) -> dict[str, list[str]]:
        by_node: dict[str, list[str]] = {}
        if not self.cfg.build.index:
            return by_node
        for term in self.pdb.terms.list():
            if term.defined_in:
                by_node.setdefault(term.defined_in, []).append(term.term)
        return by_node

    def _figure_markdown(self, label: str) -> str:
        fig = self.pdb.figures.by_label(label)
        if fig is None:
            return f"\n::: {{.callout-warning}}\nFigure {label} is missing.\n:::\n"
        width = int(round(float(fig.spec.get("width", 0.85)) * 100))
        caption = fig.caption.replace("\n", " ").strip()
        alt = _yaml_str(fig.alt_text.replace("\n", " ").strip())
        # chapter files live in chapters/, so figures are referenced relative to them
        return f'![{caption}](../figures/{label}){{#{label} fig-alt="{alt}" width="{width}%"}}'

    def _chapter_markdown(self, ch: Node, index_terms: dict[str, list[str]], book: AssembledBook) -> str:
        out = [f"# {ch.title} {{#sec-{ch.slug}}}", ""]
        for sec in self.pdb.nodes.children(ch.id):
            if sec.kind != NodeKind.SECTION:
                continue
            out += [f"## {sec.title} {{#sec-{ch.slug}-{sec.slug}}}", ""]
            for blk in self.pdb.nodes.children(sec.id):
                text = self.project.content.read_block(blk.id)
                if text is None:
                    out += [f"::: {{.callout-note}}\n*This part ({blk.title}) has not been written yet.*\n:::", ""]
                    book.warnings.append(f"unwritten block: {blk.title}")
                    continue
                if self.cfg.build.block_headings and len(self.pdb.nodes.children(sec.id)) > 1:
                    out += [f"### {blk.title}", ""]
                for term in index_terms.get(blk.id, []):
                    text = insert_index_entry(text, term)
                text = FIG_MARKER_RE.sub(lambda m: self._figure_markdown(m.group(1)), text)
                from scholarforge.science.markdown import analyze

                book.cited |= analyze(text).citations
                out += [text.strip(), ""]
        return "\n".join(out).rstrip() + "\n"

    def _front_matter(self) -> str:
        p = self.cfg.params
        plan_path = self.project.research_dir / "plan.json"
        scope = ""
        if plan_path.exists():
            from scholarforge.util.atomic import read_json

            scope = read_json(plan_path).get("scope", "")
        lines = ["# Preface {.unnumbered}", ""]
        if scope:
            lines += [scope.strip(), ""]
        lines += [
            f"This {p.book_type.replace('_', ' ')} is written for readers at the {p.level} level"
            + (f" ({p.audience})" if p.audience else "") + ".", "",
            "::: {.callout-note title=\"How this book was produced\"}",
            "The text was drafted with the assistance of an AI model and assembled, checked and typeset with "
            f"ScholarForge {__version__}. Cited works were verified against bibliographic registries, and formulas "
            "with attached checks were verified by computer algebra; the appendix *Verification report* lists what "
            "was checked, what was not, and what needs expert review. Machine checks reduce but do not eliminate "
            "the possibility of errors.",
            ":::", "",
        ]
        conventions = self.pdb.meta.get_json("notation_conventions", []) or []
        if conventions:
            lines += ["## Conventions {.unnumbered}", ""] + [f"- {c}" for c in conventions] + [""]
        return "\n".join(lines)

    def _back_matter(self, target: Path, book: AssembledBook) -> list[str]:
        files: list[str] = []
        terms = self.pdb.terms.list()
        if terms:
            lines = ["# Glossary {#sec-glossary}", ""]
            for t in terms:
                where = ""
                if t.defined_in:
                    sec = self._section_label(t.defined_in)
                    where = f" (see @{sec})" if sec else ""
                lines += [f"**{t.term}**", f": {t.definition.strip()}{where}", ""]
            atomic_write_text(target / "glossary.qmd", "\n".join(lines))
            files.append("glossary.qmd")
        notation = self.pdb.notation.list()
        if notation:
            lines = ["# Notation {#sec-notation}", "", "| Symbol | Meaning |", "|:--|:--|"]
            seen = set()
            for n in notation:
                key = (n.normalized, n.meaning)
                if key in seen:
                    continue
                seen.add(key)
                sym = n.symbol.strip().strip("$")
                meaning = n.meaning.replace("|", "\\|")
                lines.append(f"| ${sym}$ | {meaning} |")
            atomic_write_text(target / "notation.qmd", "\n".join(lines) + "\n")
            files.append("notation.qmd")
        if self.cfg.build.include_verification_appendix:
            atomic_write_text(target / "verification.qmd", self._verification_appendix(book))
            files.append("verification.qmd")
        atomic_write_text(target / "references.qmd", "# References {.unnumbered}\n\n::: {#refs}\n:::\n")
        files.append("references.qmd")
        return files

    def _section_label(self, block_id: str) -> str | None:
        node = self.pdb.nodes.find(block_id)
        if node is None or node.parent_id is None:
            return None
        sec = self.pdb.nodes.get(node.parent_id)
        ch = self.pdb.nodes.ancestor_of_kind(block_id, NodeKind.CHAPTER)
        if sec.kind != NodeKind.SECTION or ch is None:
            return None
        return f"sec-{ch.slug}-{sec.slug}"

    def _verification_appendix(self, book: AssembledBook) -> str:
        claims = self.pdb.claims.all()
        counts: dict[str, int] = {}
        for c in claims:
            counts[c.status.value] = counts.get(c.status.value, 0) + 1
        sources = self.pdb.sources.list()
        src_counts: dict[str, int] = {}
        for s in sources:
            src_counts[s.status.value] = src_counts.get(s.status.value, 0) + 1
        latest: dict[str, Any] = {}
        for c in self.pdb.checks.all():  # chronological: keep the latest result of every assertion
            if c.check_type.startswith("math."):
                latest[c.subject_id] = c
        math_ok = sum(1 for c in latest.values() if c.outcome.value == "pass" and c.method in ("symbolic", "formal"))
        math_num = sum(1 for c in latest.values() if c.outcome.value == "pass" and c.method in ("numeric", "dimensional"))
        open_issues = self.pdb.issues.list(min_severity=Severity.WARNING)
        lines = ["# Verification report {#sec-verification-report}", "",
                 "This appendix is generated automatically. It states what was verified and how.", "",
                 "| Claim status | Count | Meaning |", "|:--|--:|:--|"]
        meaning = {
            "verified": "proved by exact computer algebra or a formal proof",
            "supported": "independent evidence agrees (numerical checks, or quoted evidence found in a verified source)",
            "contested": "speculative/open, or a source does not support it",
            "unverified": "no independent evidence yet",
            "requires_expert_review": "a check failed or the claim is high-stakes",
        }
        for status in ClaimStatus:
            lines.append(f"| {status.value.replace('_', ' ')} | {counts.get(status.value, 0)} | {meaning[status.value]} |")
        lines += ["", f"Machine-checked formulas: {math_ok} proved symbolically or formally, {math_num} supported "
                      "numerically or dimensionally.", "",
                  "| Source status | Count |", "|:--|--:|"]
        for status in SourceStatus:
            if src_counts.get(status.value):
                lines.append(f"| {status.value.replace('_', ' ')} | {src_counts[status.value]} |")
        lines += ["", "Only *verified* sources (existence and metadata confirmed in Crossref, OpenAlex, arXiv or "
                      "Open Library) can be cited in this book.", ""]
        if book.missing_figures:
            lines += [f"Figures not yet available: {', '.join(book.missing_figures)}.", ""]
        if open_issues:
            lines += [f"Open editorial or scientific issues at build time: {len(open_issues)} "
                      "(see the project report for details).", ""]
        lines += ["A language model's own review of its text is never counted as independent verification."]
        return "\n".join(lines) + "\n"

    def _quarto_yml(self, layout: list[Any], appendices: list[str], bib_count: int) -> dict[str, Any]:
        p, b = self.cfg.params, self.cfg.build
        title = self.pdb.meta.get("book_title") or self.cfg.title
        subtitle = self.pdb.meta.get("book_subtitle") or p.subtitle
        chapters: list[Any] = ["index.qmd"]
        for item in layout:
            if isinstance(item, dict):
                chapters.append({"part": item["part"], "chapters": item["chapters"]})
            else:
                chapters.append(item)
        book_cfg: dict[str, Any] = {"title": title, "author": p.authors or ["ScholarForge project"],
                                    "date": "today", "chapters": chapters}
        if subtitle:
            book_cfg["subtitle"] = subtitle
        if appendices:
            book_cfg["appendices"] = appendices
        pdf: dict[str, Any] = {
            "documentclass": b.pdf_documentclass,
            "classoption": (["twoside", "open=any"] if self.edition == "screen" else ["twoside", "open=right"])
            + (["numbers=noenddot"] if b.pdf_documentclass.startswith("scr") else []),
            "pdf-engine": "lualatex",
            "default-image-extension": "pdf",
            "papersize": b.pdf_papersize,
            "fontsize": b.fontsize,
            "toc": True, "toc-depth": b.toc_depth, "number-sections": b.number_sections,
            "include-in-header": ["preamble.tex"],
            "include-after-body": ["after-body.tex"],
            "keep-tex": True,
            "colorlinks": self.edition == "screen",
            "latex-auto-install": True,
            "geometry": latex.screen_geometry(self.cfg) if self.edition == "screen" else latex.print_geometry(self.cfg),
        }
        for key in ("mainfont", "sansfont", "monofont", "mathfont"):
            value = getattr(b, key)
            if value:
                pdf[key] = value
        cfg: dict[str, Any] = {
            "project": {"type": "book", "output-dir": "_output"},
            "book": book_cfg,
            "lang": p.language,
            "number-sections": b.number_sections,
            "crossref": {"chapters": True},
            "format": {
                "html": {"theme": b.html_theme, "css": ["styles.css"], "html-math-method": "mathjax",
                         "default-image-extension": "svg", "toc-depth": b.toc_depth,
                         "number-sections": b.number_sections},
                "epub": {"html-math-method": b.epub_math, "default-image-extension": "png", "toc": True,
                         "toc-depth": b.toc_depth, "number-sections": b.number_sections},
                "pdf": pdf,
            },
        }
        if bib_count:
            cfg["bibliography"] = ["references.json"]
            cfg["link-citations"] = True
            if b.csl:
                cfg["csl"] = b.csl
        return cfg
