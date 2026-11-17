"""LaTeX preambles and page geometry for the screen and print editions."""

from __future__ import annotations

from pathlib import Path

from scholarforge.util.atomic import atomic_write_text

KDP_MIN_GUTTER_MM = [(150, 9.6), (300, 12.7), (500, 15.9), (700, 19.1), (828, 22.3), (10**9, 25.4)]


def min_gutter_mm(pages: int) -> float:
    for limit, mm in KDP_MIN_GUTTER_MM:
        if pages <= limit:
            return mm
    return 25.4


def estimate_pages(project) -> int:
    words = sum(b.word_count or int(b.plan.get("target_words", 0)) for b in project.db.nodes.blocks())
    figures = len(project.db.figures.list())
    return max(24, int(words / max(1, project.settings.workflow.words_per_page)) + figures // 2 + 12)


def screen_geometry(cfg) -> list[str]:
    return ["top=25mm", "bottom=28mm", "inner=27mm", "outer=22mm", "headsep=8mm", "footskip=12mm"]


def print_geometry(cfg, pages: int | None = None) -> list[str]:
    ps = cfg.print
    w, h = ps.trim_mm()
    b = ps.bleed_mm
    m = ps.margins
    inner = m.inner_mm
    if ps.auto_gutter and pages:
        inner = max(inner, min_gutter_mm(pages) + 3.0)
    return [
        f"paperwidth={w + 2 * b:.3f}mm", f"paperheight={h + 2 * b:.3f}mm",
        f"inner={inner + b:.3f}mm", f"outer={m.outer_mm + b:.3f}mm",
        f"top={m.top_mm + b:.3f}mm", f"bottom={m.bottom_mm + b:.3f}mm",
        "headsep=6mm", "footskip=10mm", "twoside",
    ]


COMMON_PREAMBLE = r"""
% --- ScholarForge common preamble --------------------------------------------
\usepackage{imakeidx}
\makeindex[intoc,columns=2]
\usepackage{microtype}
\setlength{\emergencystretch}{3em}
\widowpenalty=10000
\clubpenalty=10000
\raggedbottom
\providecommand{\sfbookfont}{}
"""

KOMA_PREAMBLE = r"""
\usepackage[automark]{scrlayer-scrpage}
\clearpairofpagestyles
\ohead{\pagemark}
\ihead{\leftmark}
\setkomafont{pageheadfoot}{\normalfont\small\scshape}
\pagestyle{scrheadings}
"""

PRINT_PREAMBLE = r"""
% --- ScholarForge print edition ------------------------------------------------
% no interactive annotations in print files (PDF/X forbids them inside the page area);
% hyperref is loaded later by the template, so pass the option instead of \hypersetup
\PassOptionsToPackage{draft}{hyperref}
"""

GRAY_PREAMBLE = r"""
% black & white interior: every colour is converted to grey by xcolor
\AtBeginDocument{\ifdefined\selectcolormodel\selectcolormodel{gray}\fi}
"""

HTML_CSS = """
/* ScholarForge screen styles */
.theorem, .lemma, .proposition, .corollary, .definition { border-left: 3px solid #4a6fa5; padding-left: .8em; }
.proof { border-left: 2px dotted #888; padding-left: .8em; }
figure img { max-width: 100%; height: auto; }
"""

AFTER_BODY = r"""
\printindex
"""


def write_preambles(target: Path, project, *, edition: str) -> None:
    cfg = project.config
    text = COMMON_PREAMBLE
    if cfg.build.pdf_documentclass.startswith("scr"):
        text += KOMA_PREAMBLE
    if edition == "print":
        text += PRINT_PREAMBLE
        if cfg.print.color == "bw":
            text += GRAY_PREAMBLE
    atomic_write_text(target / "preamble.tex", text)
    atomic_write_text(target / "after-body.tex", AFTER_BODY if cfg.build.index else "")
