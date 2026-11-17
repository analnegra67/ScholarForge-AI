"""Independent preflight of produced files (PDF, EPUB, HTML).

The PDF checks re-read the file from scratch (they do not trust the writer):
page geometry and boxes, font embedding, colour spaces actually used in content
streams and images, effective image resolution (CTM tracking through forms),
annotations/JavaScript/encryption, output intent and XMP identification.

What is *not* checked (and is stated in every report): total area coverage (ink
limits), trapping, overprint simulation, ICC profile conformance to the printer's
exact characterisation, certified ISO 15930-7 validation.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import pikepdf
from pikepdf import Name

MM_TO_PT = 72.0 / 25.4
NOT_CHECKED = ["total area coverage (ink limit)", "trapping", "overprint behaviour",
               "conformance of the ICC output intent to the printer's process",
               "certified ISO 15930-7 (PDF/X-4) validation — use the printer's preflight profile"]


def _mul(a: list[float], b: list[float]) -> list[float]:
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3], a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


class _Scanner:
    def __init__(self) -> None:
        self.colorspaces: dict[str, int] = {}
        self.images: list[dict[str, Any]] = []
        self.fonts: dict[str, dict[str, Any]] = {}
        self.transparency = False
        self.seen_forms: set[tuple[int, int]] = set()
        self.errors: list[str] = []

    def cs(self, name: str, page: int) -> None:
        self.colorspaces[name] = self.colorspaces.get(name, 0) + 1

    def font(self, key: str, font, page: int) -> None:
        name = str(font.get("/BaseFont", key))
        if name in self.fonts:
            return
        sub = str(font.get("/Subtype", ""))
        desc = font.get("/FontDescriptor")
        if desc is None and "/DescendantFonts" in font:
            desc = font.DescendantFonts[0].get("/FontDescriptor")
        embedded = sub == "/Type3" or (desc is not None and any(k in desc for k in ("/FontFile", "/FontFile2",
                                                                                    "/FontFile3")))
        self.fonts[name] = {"subtype": sub, "embedded": embedded, "first_page": page}

    def walk(self, owner, resources, ctm: list[float], page: int, depth: int = 0) -> None:
        if depth > 12:
            return
        res = resources if resources is not None else pikepdf.Dictionary()
        fonts = res.get("/Font") or {}
        for k, f in fonts.items():
            self.font(str(k), f, page)
        cs_res = res.get("/ColorSpace") or {}
        try:
            ops = pikepdf.parse_content_stream(owner)
        except Exception as exc:
            self.errors.append(f"page {page}: unparsable content ({exc})")
            return
        stack: list[list[float]] = []
        cur = list(ctm)
        for operands, op in ops:
            name = str(op)
            if name == "q":
                stack.append(list(cur))
            elif name == "Q":
                cur = stack.pop() if stack else cur
            elif name == "cm":
                cur = _mul([float(x) for x in operands], cur)
            elif name in ("rg", "RG"):
                self.cs("DeviceRGB", page)
            elif name in ("k", "K"):
                self.cs("DeviceCMYK", page)
            elif name in ("g", "G"):
                self.cs("DeviceGray", page)
            elif name in ("cs", "CS"):
                target = operands[0]
                if target in (Name.DeviceRGB, Name.DeviceCMYK, Name.DeviceGray, Name.Pattern):
                    self.cs(str(target)[1:], page)
                else:
                    resolved = cs_res.get(target)
                    if isinstance(resolved, pikepdf.Name):
                        self.cs(str(resolved)[1:], page)
                    elif isinstance(resolved, pikepdf.Array) and len(resolved):
                        self.cs(str(resolved[0])[1:], page)
            elif name == "gs":
                gs = (res.get("/ExtGState") or {}).get(operands[0])
                if gs is not None:
                    smask = gs.get("/SMask")
                    if (float(gs.get("/ca", 1)) < 1 or float(gs.get("/CA", 1)) < 1
                            or (smask is not None and smask != Name("/None"))):
                        self.transparency = True
            elif name == "Do":
                xo = (res.get("/XObject") or {}).get(operands[0])
                if xo is None:
                    continue
                sub = xo.get("/Subtype")
                if sub == Name.Image:
                    w, h = int(xo.get("/Width", 0)), int(xo.get("/Height", 0))
                    disp_w = math.hypot(cur[0], cur[1]) / 72.0
                    disp_h = math.hypot(cur[2], cur[3]) / 72.0
                    ppi = min(w / disp_w if disp_w else 1e9, h / disp_h if disp_h else 1e9)
                    cs = xo.get("/ColorSpace")
                    cs_name = str(cs[0] if isinstance(cs, pikepdf.Array) else cs)[1:] if cs is not None else "mask"
                    self.cs(f"image:{cs_name}", page)
                    if xo.get("/SMask") is not None:
                        self.transparency = True
                    self.images.append({"page": page, "px": [w, h], "ppi": round(ppi), "colorspace": cs_name})
                elif sub == Name.Form:
                    matrix = [float(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                    grp = xo.get("/Group")
                    if grp is not None and grp.get("/S") == Name.Transparency:
                        self.transparency = True
                    self.walk(xo, xo.get("/Resources"), _mul(matrix, cur), page, depth + 1)


def preflight_pdf(path: Path, *, trim_mm: tuple[float, float] | None = None, bleed_mm: float = 0.0,
                  expect_print: bool = False, expect_pdfx: bool = False, color_mode: str = "color",
                  min_ppi: int = 300) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {"file": str(path)}
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        return {"ok": False, "errors": ["the PDF is encrypted"], "warnings": [], "info": info}
    except Exception as exc:
        return {"ok": False, "errors": [f"cannot open PDF: {exc}"], "warnings": [], "info": info}
    with pdf:
        info["pages"] = len(pdf.pages)
        info["pdf_version"] = pdf.pdf_version
        if pdf.is_encrypted:
            errors.append("the PDF is encrypted")
        scanner = _Scanner()
        sizes = set()
        annots = 0
        box_problems = 0
        for i, page in enumerate(pdf.pages, start=1):
            mb = [float(x) for x in page.obj.MediaBox]
            sizes.add((round(mb[2] - mb[0], 1), round(mb[3] - mb[1], 1)))
            if "/Annots" in page.obj:
                annots += len(page.obj.Annots)
            if expect_print:
                tb, bb = page.obj.get("/TrimBox"), page.obj.get("/BleedBox")
                if tb is None or bb is None:
                    box_problems += 1
                elif trim_mm:
                    t = [float(x) for x in tb]
                    if abs((t[2] - t[0]) - trim_mm[0] * MM_TO_PT) > 1.0 or abs((t[3] - t[1]) - trim_mm[1] * MM_TO_PT) > 1.0:
                        box_problems += 1
                    b = [float(x) for x in bb]
                    if not (b[0] - 0.01 <= t[0] and t[2] <= b[2] + 0.01 and b[0] >= mb[0] - 0.01 and b[2] <= mb[2] + 0.01):
                        box_problems += 1
            scanner.walk(page.obj, page.obj.get("/Resources"), [1, 0, 0, 1, 0, 0], i)
        info["page_sizes_pt"] = sorted(sizes)
        if len(sizes) > 1:
            warnings.append(f"pages have {len(sizes)} different sizes")
        if expect_print and trim_mm:
            want = (round((trim_mm[0] + 2 * bleed_mm) * MM_TO_PT, 1), round((trim_mm[1] + 2 * bleed_mm) * MM_TO_PT, 1))
            if any(abs(s[0] - want[0]) > 1.0 or abs(s[1] - want[1]) > 1.0 for s in sizes):
                errors.append(f"page size {sorted(sizes)} pt does not equal trim + bleed {want} pt")
        if box_problems:
            errors.append(f"{box_problems} page(s) with missing or inconsistent TrimBox/BleedBox")
        unembedded = [n for n, f in scanner.fonts.items() if not f["embedded"]]
        info["fonts"] = len(scanner.fonts)
        if unembedded:
            errors.append(f"fonts not embedded: {unembedded[:8]}")
        info["colorspaces"] = scanner.colorspaces
        info["transparency"] = scanner.transparency
        info["images"] = len(scanner.images)
        low = [im for im in scanner.images if im["ppi"] < min_ppi]
        if low:
            (errors if expect_print else warnings).append(
                f"{len(low)} image placement(s) below {min_ppi} ppi (lowest {min(im['ppi'] for im in low)} ppi on page "
                f"{low[0]['page']})")
        info["min_image_ppi"] = min((im["ppi"] for im in scanner.images), default=None)
        if scanner.errors:
            warnings += scanner.errors[:5]
        if expect_print:
            if annots:
                errors.append(f"{annots} annotation(s) (links) on printed pages")
            rgb = {k: v for k, v in scanner.colorspaces.items() if "DeviceRGB" in k}
            if rgb:
                errors.append(f"device RGB colour used ({rgb}); print files must use grey, CMYK or ICC-based colour")
            if color_mode == "bw":
                colourful = {k: v for k, v in scanner.colorspaces.items()
                             if any(x in k for x in ("RGB", "CMYK", "ICCBased", "Separation", "DeviceN"))}
                if colourful:
                    warnings.append(f"black & white edition still contains colour spaces {colourful}")
        root = pdf.Root
        if "/OpenAction" in root or "/AA" in root:
            (errors if expect_print else warnings).append("document actions present")
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            errors.append("JavaScript present")
        intents = root.get("/OutputIntents")
        pdfx_intent = None
        if intents is not None:
            for oi in intents:
                if oi.get("/S") == Name("/GTS_PDFX"):
                    prof = oi.get("/DestOutputProfile")
                    pdfx_intent = {"identifier": str(oi.get("/OutputConditionIdentifier", "")),
                                   "components": int(prof.get("/N", 0)) if prof is not None else 0}
        info["output_intent"] = pdfx_intent
        xmp_raw = ""
        if "/Metadata" in root:
            try:
                xmp_raw = root.Metadata.read_bytes().decode("utf-8", "replace")
            except Exception:
                xmp_raw = ""
        claims_pdfx = "GTS_PDFXVersion>PDF/X-4<" in xmp_raw
        info["xmp_pdfx_version"] = "PDF/X-4" if claims_pdfx else None
        if expect_pdfx:
            if pdfx_intent is None:
                errors.append("PDF/X: no GTS_PDFX output intent")
            elif pdfx_intent["components"] not in (1, 3, 4):
                errors.append("PDF/X: output intent profile is not a usable output profile")
            if not claims_pdfx:
                errors.append("PDF/X: XMP lacks pdfxid:GTS_PDFXVersion = PDF/X-4")
            if "<pdf:Trapped>" not in xmp_raw:
                errors.append("PDF/X: XMP lacks pdf:Trapped")
            if "<dc:title>" not in xmp_raw:
                errors.append("PDF/X: XMP lacks dc:title")
            if tuple(int(x) for x in pdf.pdf_version.split(".")) < (1, 6):
                errors.append(f"PDF/X-4 requires PDF 1.6 or later (file is {pdf.pdf_version})")
            if pdf.trailer.get("/ID") is None:
                errors.append("PDF/X: trailer /ID missing")
        elif claims_pdfx and errors:
            errors.append("file claims PDF/X-4 but fails the checks above")
    status = "pass" if not errors else "fail"
    conformance = None
    if expect_pdfx:
        conformance = ("passes ScholarForge's PDF/X-4 checks (not a certified validation)" if not errors
                       else "does NOT conform to PDF/X-4")
    return {"ok": not errors, "status": status, "errors": errors, "warnings": warnings, "info": info,
            "pdfx": conformance, "not_checked": NOT_CHECKED if expect_print else []}


# ----------------------------------------------------------------------------------------- EPUB
def check_epub(path: Path, epubcheck: dict[str, str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {"file": str(path)}
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        return {"ok": False, "errors": [f"not a ZIP container: {exc}"], "warnings": [], "info": info}
    with zf:
        names = zf.namelist()
        first = zf.infolist()[0] if zf.infolist() else None
        if first is None or first.filename != "mimetype":
            errors.append("'mimetype' must be the first entry")
        else:
            if first.compress_type != zipfile.ZIP_STORED:
                errors.append("'mimetype' must be stored uncompressed")
            if zf.read("mimetype").decode("ascii", "replace").strip() != "application/epub+zip":
                errors.append("wrong mimetype content")
        if "META-INF/container.xml" not in names:
            errors.append("META-INF/container.xml missing")
            return {"ok": False, "errors": errors, "warnings": warnings, "info": info}
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path or opf_path not in names:
            errors.append("package document (OPF) not found")
            return {"ok": False, "errors": errors, "warnings": warnings, "info": info}
        opf = ET.fromstring(zf.read(opf_path))
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        info["epub_version"] = opf.get("version")
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        items = opf.findall(".//opf:manifest/opf:item", ns)
        info["manifest_items"] = len(items)
        nav = [i for i in items if "nav" in (i.get("properties") or "").split()]
        if not nav:
            errors.append("no navigation document (EPUB 3 nav)")
        bad_xhtml = 0
        for item in items:
            href = base + unquote(item.get("href", ""))
            if href not in names:
                errors.append(f"manifest item missing from container: {href}")
                continue
            if item.get("media-type") == "application/xhtml+xml":
                try:
                    ET.fromstring(zf.read(href))
                except ET.ParseError:
                    bad_xhtml += 1
        if bad_xhtml:
            errors.append(f"{bad_xhtml} XHTML file(s) are not well-formed XML")
        title = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
        if title is None or not (title.text or "").strip():
            errors.append("dc:title missing")
        lang = opf.find(".//{http://purl.org/dc/elements/1.1/}language")
        if lang is None:
            errors.append("dc:language missing")
        info["has_mathml"] = any(b"<math" in zf.read(base + unquote(i.get("href", ""))) for i in items
                                 if i.get("media-type") == "application/xhtml+xml"
                                 and (base + unquote(i.get("href", ""))) in names)
    result: dict[str, Any] = {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}
    if epubcheck and epubcheck.get("jar") and epubcheck.get("java"):
        result["epubcheck"] = run_epubcheck(path, epubcheck["java"], epubcheck["jar"])
        if result["epubcheck"].get("fatal", 0) or result["epubcheck"].get("errors", 0):
            result["ok"] = False
            result["errors"].append(f"epubcheck: {result['epubcheck']['errors']} error(s), "
                                    f"{result['epubcheck'].get('fatal', 0)} fatal")
    else:
        result["epubcheck"] = {"skipped": "epubcheck not installed (scholarforge tools install epubcheck)"}
    return result


def run_epubcheck(path: Path, java: str, jar: str) -> dict[str, Any]:
    out = path.with_suffix(".epubcheck.json")
    try:
        proc = subprocess.run([java, "-jar", jar, str(path), "--json", str(out)], capture_output=True, text=True,
                              timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "no JSON report", "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-1500:]}
    counts: dict[str, int] = {"fatal": 0, "errors": 0, "warnings": 0, "usage": 0}
    samples = []
    for msg in data.get("messages", []):
        sev = msg.get("severity", "").upper()
        key = {"FATAL": "fatal", "ERROR": "errors", "WARNING": "warnings"}.get(sev, "usage")
        counts[key] += 1
        if sev in ("FATAL", "ERROR") and len(samples) < 10:
            loc = (msg.get("locations") or [{}])[0]
            samples.append(f"{msg.get('ID')}: {msg.get('message')} ({loc.get('path')}:{loc.get('line')})")
    counts["samples"] = samples  # type: ignore[assignment]
    counts["checker"] = data.get("checker", {}).get("checkerVersion")  # type: ignore[assignment]
    return counts


# ----------------------------------------------------------------------------------------- HTML
class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.has_math = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if d.get("id"):
            self.ids.add(d["id"])
        for attr in ("href", "src"):
            if d.get(attr):
                self.links.append(d[attr])
        if tag == "script" and d.get("src") and "mathjax" in (d.get("src") or "").lower():
            self.has_math = True


def check_html_site(site: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pages = sorted(site.rglob("*.html"))
    info: dict[str, Any] = {"pages": len(pages)}
    if not (site / "index.html").exists():
        errors.append("index.html missing")
    broken: list[str] = []
    anchors: dict[Path, set[str]] = {}
    parsed: dict[Path, _LinkParser] = {}
    for page in pages:
        p = _LinkParser()
        p.feed(page.read_text(encoding="utf-8", errors="replace"))
        parsed[page] = p
        anchors[page.resolve()] = p.ids
    for page, p in parsed.items():
        for link in p.links:
            u = urlparse(link)
            if u.scheme in ("http", "https", "mailto", "data", "javascript") or link.startswith("//"):
                continue
            target_path = unquote(u.path)
            if not target_path:
                if u.fragment and u.fragment not in p.ids:
                    broken.append(f"{page.name}#{u.fragment}")
                continue
            target = (page.parent / target_path).resolve()
            if not target.exists():
                broken.append(f"{page.name} -> {target_path}")
            elif u.fragment and target.suffix == ".html" and u.fragment not in anchors.get(target, set()):
                broken.append(f"{page.name} -> {target_path}#{u.fragment}")
    info["broken_links"] = len(broken)
    if broken:
        errors.append(f"{len(broken)} broken internal link(s), e.g. {broken[:5]}")
    info["math"] = any(p.has_math for p in parsed.values())
    unresolved = []
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\?@([\w:-]+)|\?\?", text):
            unresolved.append(f"{page.name}: {m.group(0)}")
    if unresolved:
        errors.append(f"unresolved cross-references or citations: {unresolved[:5]}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}


def preflight_project(project) -> dict[str, Any]:
    from scholarforge.util.atomic import read_json

    manifest = project.exports_dir / "manifest.json"
    if not manifest.exists():
        return {"ok": False, "errors": ["no build found; run build first"]}
    return read_json(manifest).get("preflight", {})
