"""Figure quality control: resolution, fonts, colour use, accessibility metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def png_info(path: Path) -> dict[str, Any]:
    with Image.open(path) as im:
        info = {"width_px": im.width, "height_px": im.height, "mode": im.mode}
        small = im.convert("RGB").resize((min(256, im.width), min(256, max(1, im.height * 256 // max(1, im.width)))))
        pixels = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    chroma = [max(p) - min(p) for p in pixels]
    colourful = sum(1 for c in chroma if c > 40) / max(1, len(chroma))
    info["colour_fraction"] = round(colourful, 4)
    return info


def pdf_info(path: Path) -> dict[str, Any]:
    import pikepdf

    out: dict[str, Any] = {"fonts": [], "unembedded_fonts": [], "images": 0, "type3_fonts": 0}
    with pikepdf.open(path) as pdf:
        out["pages"] = len(pdf.pages)
        for page in pdf.pages:
            res = page.obj.get("/Resources") or {}
            fonts = res.get("/Font") or {}
            for _, font in fonts.items():
                sub = font.get("/Subtype")
                name = str(font.get("/BaseFont", "?"))
                out["fonts"].append(name)
                if sub == "/Type3":
                    out["type3_fonts"] += 1
                    continue
                desc = font.get("/FontDescriptor")
                if desc is None and "/DescendantFonts" in font:
                    desc = font["/DescendantFonts"][0].get("/FontDescriptor")
                if desc is None or not any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    out["unembedded_fonts"].append(name)
            xobjs = res.get("/XObject") or {}
            out["images"] += sum(1 for _, x in xobjs.items() if x.get("/Subtype") == "/Image")
    return out


def check_figure(files: dict[str, Path], *, caption: str, alt_text: str, width_fraction: float,
                 text_width_in: float, min_ppi: int, print_color: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    qc: dict[str, Any] = {}
    problems: list[dict[str, str]] = []
    vector_print = False
    if "pdf" in files:
        try:
            vector_print = pdf_info(files["pdf"])["images"] == 0
        except Exception:
            vector_print = False
    qc["print_master"] = "vector PDF" if vector_print else "raster"
    if "png" in files:
        info = png_info(files["png"])
        qc["png"] = info
        print_width_in = max(0.5, width_fraction * text_width_in)
        ppi = info["width_px"] / print_width_in
        qc["effective_ppi_at_print_width"] = round(ppi)
        # print uses the vector PDF when there is one; the PNG then only serves screens (150 ppi is ample)
        needed = 150 if vector_print else min_ppi
        if ppi < needed:
            problems.append({"code": "FIGURE_LOW_RESOLUTION",
                             "message": f"raster image gives {ppi:.0f} ppi at its printed width (< {needed})"})
        if print_color == "bw" and info["colour_fraction"] > 0.02:
            problems.append({"code": "FIGURE_RELIES_ON_COLOUR",
                             "message": "figure uses colour but the print edition is black & white: make series "
                                        "distinguishable by line style / markers / labels"})
    if "pdf" in files:
        info = pdf_info(files["pdf"])
        qc["pdf"] = info
        if info["unembedded_fonts"]:
            problems.append({"code": "FIGURE_FONT_NOT_EMBEDDED",
                             "message": f"fonts not embedded: {info['unembedded_fonts'][:3]}"})
        if info["type3_fonts"]:
            problems.append({"code": "FIGURE_TYPE3_FONT", "message": "Type 3 fonts in vector figure (prefer Type 42)"})
    if alt_text.strip().lower() == caption.strip().lower():
        problems.append({"code": "FIGURE_ALT_EQUALS_CAPTION", "message": "alt text should describe the content, "
                                                                        "not repeat the caption"})
    if len(alt_text.split()) < 8:
        problems.append({"code": "FIGURE_ALT_TOO_SHORT", "message": "alt text is too short to be useful"})
    return qc, problems
