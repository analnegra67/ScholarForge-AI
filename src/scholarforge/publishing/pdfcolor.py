"""Colour handling for print PDFs (content-stream level, with pikepdf).

Two strategies:

``icc``  (colour print, PDF/X-4 late binding) – every DeviceRGB use is re-tagged as
         ICC-based sRGB. Pixel and colour values are unchanged; the printer's RIP converts
         to its CMYK space using the output intent. DeviceGray and DeviceCMYK are left alone.
``gray`` (black & white interior) – DeviceRGB/DeviceCMYK colours are converted to
         DeviceGray (luminance), and RGB images are re-encoded as greyscale.

Constructs the converter cannot transform safely (RGB shadings in gray mode,
pattern colour spaces, inline RGB images) are reported, never silently ignored.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass, field
from typing import Any

import pikepdf
from PIL import Image, ImageCms
from pikepdf import Name, Operator, Pdf

SRGB_NAME = Name("/SFsRGB")


@dataclass
class ColorReport:
    mode: str
    streams: int = 0
    rgb_ops_converted: int = 0
    cmyk_ops_converted: int = 0
    images_converted: int = 0
    colorspaces_converted: int = 0
    annotations_removed: int = 0
    unsupported: list[str] = field(default_factory=list)


def srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _lum(r: float, g: float, b: float) -> float:
    return max(0.0, min(1.0, 0.2126 * r + 0.7152 * g + 0.0722 * b))


def _cmyk_to_gray(c: float, m: float, y: float, k: float) -> float:
    r, g, b = (1 - min(1.0, c + k)), (1 - min(1.0, m + k)), (1 - min(1.0, y + k))
    return _lum(r, g, b)


class ColorConverter:
    def __init__(self, pdf: Pdf, mode: str):
        if mode not in ("icc", "gray"):
            raise ValueError("mode must be 'icc' or 'gray'")
        self.pdf = pdf
        self.mode = mode
        self.report = ColorReport(mode=mode)
        self._done: set[tuple[int, int]] = set()
        self._srgb_cs = None
        if mode == "icc":
            stream = pikepdf.Stream(pdf, srgb_icc_bytes())
            stream["/N"] = 3
            stream["/Alternate"] = Name.DeviceRGB
            self._srgb_cs = pdf.make_indirect(pikepdf.Array([Name.ICCBased, stream]))

    # ------------------------------------------------------------------------------------ api
    def _collect_used_colorspaces(self) -> set[str]:
        """Names selected with cs/CS anywhere in the document (pages, forms, Type 3 glyphs)."""
        used: set[str] = set()
        seen: set[tuple[int, int]] = set()

        def scan(owner, res, depth=0):
            if depth > 12:
                return
            try:
                for operands, op in pikepdf.parse_content_stream(owner):
                    if str(op) in ("cs", "CS") and operands:
                        used.add(str(operands[0]))
            except Exception:
                return
            xobjs = (res or {}).get("/XObject") or {}
            for _, x in xobjs.items():
                if x.get("/Subtype") == Name.Form:
                    key = x.objgen
                    if key != (0, 0) and key in seen:
                        continue
                    seen.add(key)
                    scan(x, x.get("/Resources"), depth + 1)

        for page in self.pdf.pages:
            scan(page.obj, page.obj.get("/Resources"))
        return used

    def run(self, remove_annotations: bool = True) -> ColorReport:
        self._used_cs = self._collect_used_colorspaces()
        for page in self.pdf.pages:
            if remove_annotations and "/Annots" in page.obj:
                self.report.annotations_removed += len(page.obj.Annots)
                del page.obj["/Annots"]
            self._group(page.obj)
            res = page.obj.get("/Resources")
            if res is None:
                res = pikepdf.Dictionary()
                page.obj["/Resources"] = res
            self._resources(res)
            self._content(page.obj, res, is_page=True)
        return self.report

    # ------------------------------------------------------------------------------- helpers
    def _seen(self, obj) -> bool:
        try:
            key = obj.objgen
        except Exception:
            return False
        if key == (0, 0):
            return False
        if key in self._done:
            return True
        self._done.add(key)
        return False

    def _group(self, obj) -> None:
        grp = obj.get("/Group")
        if grp is not None and grp.get("/CS") == Name.DeviceRGB:
            grp["/CS"] = self._srgb_cs if self.mode == "icc" else Name.DeviceGray
            self.report.colorspaces_converted += 1

    def _convert_cs_value(self, value):
        """Return a converted colour space object, or None if unchanged."""
        if value == Name.DeviceRGB:
            return self._srgb_cs if self.mode == "icc" else Name.DeviceGray
        if self.mode == "gray" and value == Name.DeviceCMYK:
            return Name.DeviceGray
        return None

    def _resources(self, res) -> None:
        if self._seen(res):
            return
        cs_dict = res.get("/ColorSpace")
        if cs_dict is not None:
            for key in list(cs_dict.keys()):
                val = cs_dict[key]
                new = self._convert_cs_value(val)
                if new is not None:
                    cs_dict[key] = new
                    self.report.colorspaces_converted += 1
                elif isinstance(val, pikepdf.Array) and len(val) == 2 and val[0] == Name.Pattern:
                    base = val[1]
                    if base == Name.DeviceRGB or (self.mode == "gray" and base == Name.DeviceCMYK):
                        if self.mode == "icc" and base == Name.DeviceRGB:
                            cs_dict[key] = pikepdf.Array([Name.Pattern, self._srgb_cs])
                            self.report.colorspaces_converted += 1
                        elif str(key) not in getattr(self, "_used_cs", set()):
                            # declared but never selected (pgf declares these on every page): safe to neutralise
                            cs_dict[key] = pikepdf.Array([Name.Pattern, Name.DeviceGray])
                            self.report.colorspaces_converted += 1
                        else:
                            self.report.unsupported.append(f"uncoloured pattern colour space {key} in use")
                elif isinstance(val, pikepdf.Array) and len(val) and val[0] == Name.Pattern:
                    pass  # coloured patterns: their own content streams are converted below
        shadings = res.get("/Shading")
        if shadings is not None:
            for key in list(shadings.keys()):
                sh = shadings[key]
                cs = sh.get("/ColorSpace")
                if cs == Name.DeviceRGB:
                    if self.mode == "icc":
                        sh["/ColorSpace"] = self._srgb_cs
                        self.report.colorspaces_converted += 1
                    else:
                        self.report.unsupported.append(f"RGB shading {key} (cannot convert to grey safely)")
        patterns = res.get("/Pattern")
        if patterns is not None:
            for key in list(patterns.keys()):
                pat = patterns[key]
                if self._seen(pat):
                    continue
                ptype = int(pat.get("/PatternType", 0))
                if ptype == 1:  # tiling pattern: has its own content stream and resources
                    pres = pat.get("/Resources")
                    if pres is None:
                        pres = pikepdf.Dictionary()
                        pat["/Resources"] = pres
                    self._resources(pres)
                    self._content(pat, pres, is_page=False)
                elif ptype == 2:  # shading pattern
                    sh = pat.get("/Shading")
                    if sh is not None and sh.get("/ColorSpace") == Name.DeviceRGB:
                        if self.mode == "icc":
                            sh["/ColorSpace"] = self._srgb_cs
                            self.report.colorspaces_converted += 1
                        else:
                            self.report.unsupported.append(f"RGB shading pattern {key} (cannot convert to grey safely)")
        xobjs = res.get("/XObject")
        if xobjs is not None:
            for key in list(xobjs.keys()):
                x = xobjs[key]
                sub = x.get("/Subtype")
                if sub == Name.Image:
                    self._image(x)
                elif sub == Name.Form:
                    if self._seen(x):
                        continue
                    self._group(x)
                    fres = x.get("/Resources")
                    if fres is None:
                        fres = pikepdf.Dictionary()
                        x["/Resources"] = fres
                    self._resources(fres)
                    self._content(x, fres, is_page=False)
        fonts = res.get("/Font")
        if fonts is not None:  # Type 3 fonts carry their own resources and glyph procedures
            for key in list(fonts.keys()):
                f = fonts[key]
                if f.get("/Subtype") == Name.Type3 and f.get("/Resources") is not None:
                    self._resources(f["/Resources"])

    def _image(self, img) -> None:
        if self._seen(img):
            return
        cs = img.get("/ColorSpace")
        if img.get("/ImageMask") is True:
            return
        if self.mode == "icc":
            if cs == Name.DeviceRGB:
                img["/ColorSpace"] = self._srgb_cs
                self.report.images_converted += 1
            elif isinstance(cs, pikepdf.Array) and len(cs) == 4 and cs[0] == Name.Indexed and cs[1] == Name.DeviceRGB:
                cs[1] = self._srgb_cs
                self.report.images_converted += 1
            return
        if cs in (Name.DeviceRGB, Name.DeviceCMYK) or (isinstance(cs, pikepdf.Array) and len(cs) and cs[0] in
                                                        (Name.Indexed, Name.ICCBased)):
            try:
                pil = pikepdf.PdfImage(img).as_pil_image()
            except Exception as exc:
                self.report.unsupported.append(f"image could not be decoded for grey conversion: {exc}")
                return
            gray = pil.convert("L")
            data = zlib.compress(gray.tobytes(), 9)
            img.write(data, filter=Name.FlateDecode)
            img["/ColorSpace"] = Name.DeviceGray
            img["/BitsPerComponent"] = 8
            for k in ("/DecodeParms", "/Decode"):
                if k in img:
                    del img[k]
            self.report.images_converted += 1

    def _content(self, owner, res, *, is_page: bool) -> None:
        try:
            ops = pikepdf.parse_content_stream(owner)
        except Exception as exc:
            self.report.unsupported.append(f"unparsable content stream: {exc}")
            return
        new_ops = []
        changed = False
        fill_cs = stroke_cs = "gray"
        added_srgb = False
        for operands, op in ops:
            name = str(op)
            if name in ("rg", "RG"):
                vals = [float(v) for v in operands]
                if self.mode == "gray":
                    new_ops.append(([_lum(*vals)], Operator("g" if name == "rg" else "G")))
                else:
                    new_ops.append(([SRGB_NAME], Operator("cs" if name == "rg" else "CS")))
                    new_ops.append((operands, Operator("sc" if name == "rg" else "SC")))
                    added_srgb = True
                self.report.rgb_ops_converted += 1
                changed = True
                continue
            if name in ("k", "K") and self.mode == "gray":
                vals = [float(v) for v in operands]
                new_ops.append(([_cmyk_to_gray(*vals)], Operator("g" if name == "k" else "G")))
                self.report.cmyk_ops_converted += 1
                changed = True
                continue
            if name in ("cs", "CS"):
                target = operands[0]
                kind = "other"
                if target == Name.DeviceRGB:
                    kind = "rgb"
                    if self.mode == "icc":
                        operands = [SRGB_NAME]
                        added_srgb = True
                    else:
                        operands = [Name.DeviceGray]
                    changed = True
                elif target == Name.DeviceCMYK and self.mode == "gray":
                    kind = "cmyk"
                    operands = [Name.DeviceGray]
                    changed = True
                elif target == Name.DeviceGray:
                    kind = "gray"
                if name == "cs":
                    fill_cs = kind
                else:
                    stroke_cs = kind
                new_ops.append((operands, op))
                continue
            if name in ("sc", "scn", "SC", "SCN") and self.mode == "gray":
                current = fill_cs if name in ("sc", "scn") else stroke_cs
                nums = [] if any(isinstance(v, pikepdf.Name) for v in operands) else [float(v) for v in operands]
                if current == "rgb" and len(nums) == 3:
                    new_ops.append(([_lum(*nums)], op))
                    changed = True
                    continue
                if current == "cmyk" and len(nums) == 4:
                    new_ops.append(([_cmyk_to_gray(*nums)], op))
                    changed = True
                    continue
            if name == "BI" or name == "INLINE IMAGE":
                self.report.unsupported.append("inline image (left unchanged)")
            new_ops.append((operands, op))
        if added_srgb:
            cs_dict = res.get("/ColorSpace")
            if cs_dict is None:
                cs_dict = pikepdf.Dictionary()
                res["/ColorSpace"] = cs_dict
            cs_dict[SRGB_NAME] = self._srgb_cs
        if changed:
            data = pikepdf.unparse_content_stream(new_ops)
            if is_page:
                owner.Contents = self.pdf.make_stream(data)
            else:
                owner.write(data)
            self.report.streams += 1


def convert_pdf_colors(src, dst, mode: str, *, remove_annotations: bool = True) -> ColorReport:
    with Pdf.open(src) as pdf:
        report = ColorConverter(pdf, mode).run(remove_annotations=remove_annotations)
        pdf.save(dst)
    return report
