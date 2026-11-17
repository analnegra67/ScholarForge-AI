"""Illustration engine: renders figures, stores sources/outputs/provenance, runs QC.

Each figure lives in ``<project>/figures/<label>/``::

    source.py | source.dot | source.tex | source.svg | source.mmd   what the host wrote
    figure.pdf  figure.svg  figure.png                               renders
    provenance.json                                                  how it was made
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scholarforge.logging_setup import get_logger
from scholarforge.models.entities import Figure
from scholarforge.models.enums import FigureStatus
from scholarforge.illustrations.qc import check_figure
from scholarforge.illustrations.sandbox import run_python_figure
from scholarforge.util.atomic import atomic_write_bytes, atomic_write_json, atomic_write_text
from scholarforge.util.hashing import sha256_file, sha256_text
from scholarforge.util.timeutil import now_iso
from scholarforge.version import __version__

log = get_logger("illustrations")

RENDERER_GUIDE = {
    "matplotlib": "Python using numpy / matplotlib / scipy only (no sympy). Create the figure with "
                  "`fig, ax = plt.subplots(figsize=(w, h))` (or leave it as the current figure). Do NOT call "
                  "savefig/show; no file, network or OS access. Use line styles and markers, not colour alone.",
    "graphviz": "Graphviz DOT source (digraph/graph). Rendered with the `dot` program if installed.",
    "tikz": "A TikZ picture body: \\begin{tikzpicture} ... \\end{tikzpicture} (pgfplots allowed). Compiled with "
            "LuaLaTeX in a standalone document.",
    "svg": "A self-contained SVG document (no scripts, no external references).",
    "mermaid": "Mermaid diagram source. Rendered with mermaid-cli (mmdc) if installed.",
}
SOURCE_EXT = {"matplotlib": "py", "graphviz": "dot", "tikz": "tex", "svg": "svg", "mermaid": "mmd"}


@dataclass
class RenderResult:
    ok: bool
    outputs: dict[str, str] = field(default_factory=dict)
    error: str = ""
    qc_problems: list[dict[str, str]] = field(default_factory=list)


def text_width_in(project) -> float:
    w_mm, _ = project.config.print.trim_mm()
    m = project.config.print.margins
    return max(2.5, (w_mm - m.inner_mm - m.outer_mm) / 25.4)


class IllustrationEngine:
    def __init__(self, project):
        self.project = project
        self.settings = project.settings

    def figure_dir(self, fig: Figure) -> Path:
        return self.project.figures_dir / fig.label

    def render(self, fig: Figure) -> RenderResult:
        renderer = fig.renderer
        code = (fig.spec or {}).get("code") or ""
        out_dir = self.figure_dir(fig)
        out_dir.mkdir(parents=True, exist_ok=True)
        if renderer in SOURCE_EXT:
            atomic_write_text(out_dir / f"source.{SOURCE_EXT[renderer]}", code)
        try:
            if renderer == "matplotlib":
                files, meta, error = self._matplotlib(fig, code)
            elif renderer == "graphviz":
                files, meta, error = self._graphviz(code)
            elif renderer == "tikz":
                files, meta, error = self._tikz(code)
            elif renderer == "svg":
                files, meta, error = self._svg(code)
            elif renderer == "mermaid":
                files, meta, error = self._mermaid(code)
            else:
                return self._fail(fig, f"renderer '{renderer}' cannot be rendered locally")
        except Exception as exc:  # renderer bugs must not crash the workflow
            log.exception("renderer %s crashed for %s", renderer, fig.label)
            return self._fail(fig, f"{type(exc).__name__}: {exc}")
        if error:
            return self._fail(fig, error)
        outputs: dict[str, str] = {}
        for ext, src in files.items():
            dest = out_dir / f"figure.{ext}"
            atomic_write_bytes(dest, Path(src).read_bytes())
            outputs[ext] = dest.relative_to(self.project.root).as_posix()
        stored = {ext: out_dir / f"figure.{ext}" for ext in files}
        qc, problems = check_figure(stored, caption=fig.caption, alt_text=fig.alt_text,
                                    width_fraction=float(fig.spec.get("width", 0.85)),
                                    text_width_in=text_width_in(self.project),
                                    min_ppi=self.project.config.print.min_image_ppi,
                                    print_color=self.project.config.print.color)
        provenance = {
            "mode": fig.mode, "renderer": renderer, "renderer_meta": meta, "source_sha256": sha256_text(code),
            "outputs_sha256": {ext: sha256_file(p) for ext, p in stored.items()}, "rendered_at": now_iso(),
            "engine_version": __version__, "authored_by": "host AI (code reviewed by ScholarForge policy)",
            "sandbox_policy": self.settings.sandbox.policy, "data_provenance": fig.spec.get("data_provenance"),
            "license": fig.license,
        }
        atomic_write_json(out_dir / "provenance.json", provenance)
        self.project.db.figures.update(fig.id, status=FigureStatus.RENDERED, outputs=outputs, qc=qc,
                                       provenance=provenance)
        self.project.db.events.log("figure.rendered", "figure", fig.id, {"label": fig.label, "renderer": renderer})
        return RenderResult(True, outputs=outputs, qc_problems=problems)

    def _fail(self, fig: Figure, error: str) -> RenderResult:
        self.project.db.figures.update(fig.id, status=FigureStatus.FAILED,
                                       qc={**(fig.qc or {}), "last_error": error[-2000:]})
        return RenderResult(False, error=error)

    # ------------------------------------------------------------------------------ renderers
    def _matplotlib(self, fig: Figure, code: str):
        width = float(fig.spec.get("width", 0.85)) * text_width_in(self.project)
        res = run_python_figure(code, timeout_s=self.settings.sandbox.timeout_s,
                                memory_mb=self.settings.sandbox.memory_mb, width_in=width,
                                policy=self.settings.sandbox.policy)
        if not res.ok:
            return {}, {}, res.error
        return res.files, res.meta, ""

    def _graphviz(self, code: str):
        from scholarforge.tools.detect import find_tool

        dot = find_tool("dot")
        if not dot:
            return {}, {}, ("Graphviz 'dot' is not installed; install Graphviz or redraw this diagram with "
                            "matplotlib/TikZ")
        if re.search(r"\b(image|shapefile|fontpath|imagepath)\s*=", code, re.I):
            return {}, {}, "DOT attributes that read files (image, shapefile, fontpath, imagepath) are not allowed"
        tmp = Path(tempfile.mkdtemp(prefix="sf-dot-"))
        files = {}
        for fmt in ("pdf", "svg", "png"):
            out = tmp / f"figure.{fmt}"
            args = [dot, f"-T{fmt}", "-o", str(out)] + (["-Gdpi=400"] if fmt == "png" else [])
            proc = subprocess.run(args, input=code.encode("utf-8"), capture_output=True, timeout=60)
            if proc.returncode != 0:
                return {}, {}, proc.stderr.decode("utf-8", "replace")[-2000:]
            files[fmt] = out
        return files, {"renderer": "graphviz"}, ""

    def _tikz(self, code: str):
        from scholarforge.tools.detect import find_tool

        lualatex = find_tool("lualatex")
        if not lualatex:
            return {}, {}, "LuaLaTeX is not installed (scholarforge tools install tinytex)"
        if re.search(r"\\(write18|immediate|input|include|openin|openout|read|directlua|luaexec|usepackage\{shellesc)",
                     code):
            return {}, {}, "TikZ code may not use \\write18, \\input, \\include, file I/O or Lua escapes"
        body = code if "\\begin{tikzpicture}" in code else f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"
        doc = ("\\documentclass[tikz,border=4pt]{standalone}\n\\usepackage{amsmath,amssymb}\n"
               "\\usepackage{pgfplots}\n\\pgfplotsset{compat=1.18}\n"
               "\\usetikzlibrary{arrows.meta,positioning,calc,shapes,decorations.pathreplacing}\n"
               f"\\begin{{document}}\n{body}\n\\end{{document}}\n")
        tmp = Path(tempfile.mkdtemp(prefix="sf-tikz-"))
        (tmp / "figure.tex").write_text(doc, encoding="utf-8")
        proc = subprocess.run([lualatex, "--no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                               "figure.tex"], cwd=tmp, capture_output=True, timeout=300)
        pdf = tmp / "figure.pdf"
        if proc.returncode != 0 or not pdf.exists():
            log_text = (tmp / "figure.log").read_text(encoding="utf-8", errors="replace") if (tmp / "figure.log").exists() else ""
            errors = [ln for ln in log_text.splitlines() if ln.startswith("!")][:5]
            return {}, {}, "TikZ compilation failed: " + ("; ".join(errors) or proc.stdout.decode("utf-8", "replace")[-1500:])
        png = tmp / "figure.png"
        _pdf_to_png(pdf, png, dpi=400)
        return {"pdf": pdf, "png": png}, {"renderer": "tikz/lualatex"}, ""

    def _svg(self, code: str):
        clean = sanitize_svg(code)
        tmp = Path(tempfile.mkdtemp(prefix="sf-svg-"))
        svg = tmp / "figure.svg"
        svg.write_text(clean, encoding="utf-8")
        files = {"svg": svg}
        try:
            from svglib.svglib import svg2rlg  # type: ignore[import-not-found]
            from reportlab.graphics import renderPDF  # type: ignore[import-not-found]

            drawing = svg2rlg(str(svg))
            renderPDF.drawToFile(drawing, str(tmp / "figure.pdf"))
            files["pdf"] = tmp / "figure.pdf"
            _pdf_to_png(files["pdf"], tmp / "figure.png", dpi=400)
            files["png"] = tmp / "figure.png"
        except ImportError:
            return {}, {}, ("SVG figures need svglib+reportlab for PDF output (pip install svglib); or use "
                            "matplotlib/TikZ instead")
        return files, {"renderer": "svg"}, ""

    def _mermaid(self, code: str):
        mmdc = shutil.which("mmdc")
        if not mmdc:
            return {}, {}, "mermaid-cli (mmdc) is not installed; redraw with Graphviz, TikZ or matplotlib"
        tmp = Path(tempfile.mkdtemp(prefix="sf-mmd-"))
        (tmp / "figure.mmd").write_text(code, encoding="utf-8")
        files = {}
        for fmt in ("pdf", "svg", "png"):
            out = tmp / f"figure.{fmt}"
            proc = subprocess.run([mmdc, "-i", "figure.mmd", "-o", out.name] + (["-s", "4"] if fmt == "png" else []),
                                  cwd=tmp, capture_output=True, timeout=180)
            if proc.returncode != 0:
                return {}, {}, proc.stderr.decode("utf-8", "replace")[-2000:]
            files[fmt] = out
        return files, {"renderer": "mermaid-cli"}, ""


def _pdf_to_png(pdf: Path, png: Path, dpi: int = 300) -> None:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[0]
        image = page.render(scale=dpi / 72).to_pil()
        image.save(png)
    finally:
        doc.close()


_SVG_FORBIDDEN = re.compile(r"<\s*(script|foreignObject|iframe|object|embed)\b.*?(</\s*\1\s*>|/>)", re.I | re.S)


def sanitize_svg(svg: str) -> str:
    s = _SVG_FORBIDDEN.sub("", svg)
    s = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", s, flags=re.I)
    s = re.sub(r"(xlink:)?href\s*=\s*(\"(?!#)[^\"]*\"|'(?!#)[^']*')", "", s, flags=re.I)
    s = re.sub(r"<!ENTITY[^>]*>", "", s, flags=re.I)
    if "<svg" not in s:
        raise ValueError("not an SVG document")
    return s
