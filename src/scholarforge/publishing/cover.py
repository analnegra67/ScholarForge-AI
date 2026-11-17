"""Print cover: back + spine + front with bleed, sized from the printer's paper data.

Spine width = page count x per-page paper thickness (or pages / PPI). The cover is
typeset with LuaLaTeX/TikZ so fonts are embedded, then finalised like the block.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scholarforge.errors import RenderError, ToolMissing
from scholarforge.tools.detect import find_tool


@dataclass
class CoverGeometry:
    trim_w: float
    trim_h: float
    bleed: float
    spine: float
    pages: int

    @property
    def width(self) -> float:
        return 2 * self.trim_w + self.spine + 2 * self.bleed

    @property
    def height(self) -> float:
        return self.trim_h + 2 * self.bleed

    def as_dict(self) -> dict[str, float]:
        return {"trim_width_mm": round(self.trim_w, 3), "trim_height_mm": round(self.trim_h, 3),
                "bleed_mm": self.bleed, "spine_mm": round(self.spine, 3), "pages": self.pages,
                "cover_width_mm": round(self.width, 3), "cover_height_mm": round(self.height, 3)}


def cover_geometry(print_settings, pages: int) -> CoverGeometry:
    w, h = print_settings.trim_mm()
    if pages % 2:
        pages += 1  # printers count both sides of every leaf
    spine = pages * print_settings.per_page_thickness_mm()
    if print_settings.binding in ("saddle", "coil"):
        spine = 0.0
    return CoverGeometry(w, h, print_settings.bleed_mm, spine, pages)


def _tex_escape(s: str) -> str:
    repl = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
            "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(repl.get(c, c) for c in s)


def _hex(c: str) -> str:
    return c.lstrip("#").upper()


def cover_tex(geo: CoverGeometry, *, title: str, subtitle: str | None, author: str, back_text: str | None,
              cover, gray: bool) -> str:
    W, H, b, s, tw = geo.width, geo.height, geo.bleed, geo.spine, geo.trim_w
    spine_text = (cover.spine_text and geo.spine >= cover.min_spine_text_mm
                  and geo.pages >= cover.min_pages_for_spine_text)
    model = r"\selectcolormodel{gray}" if gray else ""
    front_x = b + tw + s  # left edge of the front panel
    lines = [
        r"\documentclass{article}",
        rf"\usepackage[paperwidth={W:.3f}mm,paperheight={H:.3f}mm,margin=0mm]{{geometry}}",
        r"\usepackage{tikz}\usepackage{xcolor}\usepackage{fontspec}",
        r"\setmainfont{Latin Modern Sans}",
        model,
        rf"\definecolor{{sfbg}}{{HTML}}{{{_hex(cover.background)}}}",
        rf"\definecolor{{sffg}}{{HTML}}{{{_hex(cover.text_color)}}}",
        rf"\definecolor{{sfacc}}{{HTML}}{{{_hex(cover.accent)}}}",
        r"\pagestyle{empty}\begin{document}\noindent",
        r"\begin{tikzpicture}[remember picture,overlay,x=1mm,y=1mm]",
        r"\begin{scope}[shift={(current page.south west)}]",
        rf"\fill[sfbg] (0,0) rectangle ({W:.3f},{H:.3f});",
        rf"\fill[sfacc] ({front_x:.3f},{H * 0.62:.3f}) rectangle ({W:.3f},{H * 0.62 + 2.2:.3f});",
        rf"\node[anchor=north west,text width={tw - 30:.1f}mm,align=left,text=sffg,font=\bfseries\fontsize{{26}}{{31}}\selectfont]"
        rf" at ({front_x + 15:.3f},{H * 0.58:.3f}) {{{_tex_escape(title)}}};",
    ]
    if subtitle:
        lines.append(rf"\node[anchor=north west,text width={tw - 30:.1f}mm,align=left,text=sffg,font=\Large]"
                     rf" at ({front_x + 15:.3f},{H * 0.40:.3f}) {{{_tex_escape(subtitle)}}};")
    lines.append(rf"\node[anchor=south west,text=sffg,font=\large] at ({front_x + 15:.3f},{b + 20:.3f})"
                 rf" {{{_tex_escape(author)}}};")
    if back_text:
        lines.append(rf"\node[anchor=north west,text width={tw - 34:.1f}mm,align=justify,text=sffg,font=\normalsize]"
                     rf" at ({b + 17:.3f},{H - b - 25:.3f}) {{{_tex_escape(back_text)}}};")
    # reserved white area for the ISBN barcode (2 x 1.2 in), lower right of the back panel
    lines.append(rf"\fill[white] ({b + tw - 12 - 50.8:.3f},{b + 12:.3f}) rectangle ({b + tw - 12:.3f},{b + 12 + 30.48:.3f});")
    if spine_text:
        size = max(6, min(14, int(s * 2.2)))
        lines.append(rf"\node[rotate=-90,text=sffg,font=\bfseries\fontsize{{{size}}}{{{size + 2}}}\selectfont]"
                     rf" at ({b + tw + s / 2:.3f},{H / 2:.3f}) {{{_tex_escape(title)}}};")
    lines += [r"\end{scope}", r"\end{tikzpicture}", r"\end{document}"]
    return "\n".join(lines) + "\n"


def build_cover(out_pdf: Path, geo: CoverGeometry, *, title: str, subtitle: str | None, author: str,
                back_text: str | None, cover, gray: bool) -> Path:
    lualatex = find_tool("lualatex")
    if not lualatex:
        raise ToolMissing("LuaLaTeX is required for the cover", hint="scholarforge tools install tinytex")
    tmp = Path(tempfile.mkdtemp(prefix="sf-cover-"))
    (tmp / "cover.tex").write_text(cover_tex(geo, title=title, subtitle=subtitle, author=author, back_text=back_text,
                                             cover=cover, gray=gray), encoding="utf-8")
    for _ in range(2):  # remember picture needs two passes
        proc = subprocess.run([lualatex, "--no-shell-escape", "-interaction=nonstopmode", "cover.tex"], cwd=tmp,
                              capture_output=True, timeout=600)
    pdf = tmp / "cover.pdf"
    if proc.returncode != 0 or not pdf.exists():
        log = (tmp / "cover.log").read_text(encoding="utf-8", errors="replace") if (tmp / "cover.log").exists() else ""
        errs = [ln for ln in log.splitlines() if ln.startswith("!")][:5]
        raise RenderError("cover compilation failed: " + ("; ".join(errs) or "see log"),
                          details={"log_tail": log[-3000:]})
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(pdf.read_bytes())
    return out_pdf
