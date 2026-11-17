"""Finalise a print PDF: page boxes, output intent, XMP identification (PDF/X-4).

ScholarForge only *labels* a file PDF/X-4 after its own preflight confirms the
requirements it can check (see :mod:`preflight`). That is not a certification:
the report recommends validating with the printer's preflight profile.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf
from pikepdf import Name, Pdf

from scholarforge.publishing.pdfcolor import ColorReport, ColorConverter
from scholarforge.util.timeutil import now_iso
from scholarforge.version import __version__

MM_TO_PT = 72.0 / 25.4


@dataclass
class OutputIntent:
    icc_path: Path
    identifier: str = "FOGRA39"
    info: str = "Coated FOGRA39 (ISO 12647-2:2004)"
    registry: str = "http://www.color.org"


@dataclass
class FinalizeReport:
    pages: int = 0
    boxes_set: bool = False
    output_intent: str | None = None
    pdfx_labelled: bool = False
    color: ColorReport | None = None
    notes: list[str] = field(default_factory=list)


def _xmp_packet(title: str, author: str, pdfx: bool, created: str) -> bytes:
    doc_id = f"uuid:{uuid.uuid4()}"
    inst_id = f"uuid:{uuid.uuid4()}"
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    pdfx_part = ('\n   <pdfxid:GTS_PDFXVersion>PDF/X-4</pdfxid:GTS_PDFXVersion>' if pdfx else "")
    xml = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/">
   <dc:format>application/pdf</dc:format>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{esc(title)}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{esc(author)}</rdf:li></rdf:Seq></dc:creator>
   <xmp:CreateDate>{created}</xmp:CreateDate>
   <xmp:ModifyDate>{created}</xmp:ModifyDate>
   <xmp:MetadataDate>{created}</xmp:MetadataDate>
   <xmp:CreatorTool>ScholarForge {__version__} (Quarto, LuaLaTeX)</xmp:CreatorTool>
   <pdf:Producer>ScholarForge {__version__} / pikepdf</pdf:Producer>
   <pdf:Trapped>False</pdf:Trapped>
   <xmpMM:DocumentID>{doc_id}</xmpMM:DocumentID>
   <xmpMM:InstanceID>{inst_id}</xmpMM:InstanceID>
   <xmpMM:VersionID>1</xmpMM:VersionID>
   <xmpMM:RenditionClass>default</xmpMM:RenditionClass>{pdfx_part}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xml.encode("utf-8")


def icc_info(data: bytes) -> dict[str, Any]:
    """Read the ICC header: device class (bytes 12-16) and data colour space (16-20)."""
    if len(data) < 128 or data[36:40] != b"acsp":
        return {"valid": False}
    device_class = data[12:16].decode("ascii", "replace")
    space = data[16:20].decode("ascii", "replace").strip()
    components = {"CMYK": 4, "RGB": 3, "GRAY": 1}.get(space.upper(), 0)
    return {"valid": True, "device_class": device_class, "colour_space": space, "components": components,
            "version": f"{data[8]}.{data[9] >> 4}"}


def _pdf_date(iso: str) -> str:
    # 2026-11-17T10:00:00.000Z -> D:20261117100000Z
    digits = iso.replace("-", "").replace(":", "").replace("T", "")
    return f"D:{digits[:14]}Z"


def finalize_print_pdf(src: Path, dst: Path, *, trim_mm: tuple[float, float], bleed_mm: float,
                       color_mode: str, title: str, author: str, intent: OutputIntent | None,
                       label_pdfx: bool = True) -> FinalizeReport:
    rep = FinalizeReport()
    created = now_iso().split(".")[0] + "Z"
    with Pdf.open(src) as pdf:
        rep.pages = len(pdf.pages)
        w_pt, h_pt = trim_mm[0] * MM_TO_PT, trim_mm[1] * MM_TO_PT
        b_pt = bleed_mm * MM_TO_PT
        for page in pdf.pages:
            mb = [float(x) for x in page.obj.MediaBox]
            x0, y0 = mb[0], mb[1]
            page.obj.BleedBox = pikepdf.Array([x0, y0, x0 + w_pt + 2 * b_pt, y0 + h_pt + 2 * b_pt])
            page.obj.TrimBox = pikepdf.Array([x0 + b_pt, y0 + b_pt, x0 + b_pt + w_pt, y0 + b_pt + h_pt])
            if "/CropBox" in page.obj:
                del page.obj["/CropBox"]
            if "/ArtBox" in page.obj:
                del page.obj["/ArtBox"]
        rep.boxes_set = True
        rep.color = ColorConverter(pdf, "gray" if color_mode == "bw" else "icc").run(remove_annotations=True)
        root = pdf.Root
        for key in ("/OpenAction", "/AA", "/AcroForm"):
            if key in root:
                del root[key]
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            del names["/JavaScript"]
        profile = intent.icc_path.read_bytes() if intent is not None and intent.icc_path.exists() else b""
        info_icc = icc_info(profile) if profile else {"valid": False}
        if profile and not (info_icc["valid"] and info_icc["device_class"] == "prtr" and info_icc["components"]):
            rep.notes.append(f"{intent.icc_path.name} is not a printer (output) ICC profile "
                             f"({info_icc.get('device_class')}/{info_icc.get('colour_space')}); no PDF/X output intent")
            profile = b""
        if profile:
            icc = pikepdf.Stream(pdf, profile)
            icc["/N"] = info_icc["components"]
            oi = pikepdf.Dictionary({
                "/Type": Name.OutputIntent, "/S": Name("/GTS_PDFX"),
                "/OutputConditionIdentifier": pikepdf.String(intent.identifier),
                "/OutputCondition": pikepdf.String(intent.info),
                "/RegistryName": pikepdf.String(intent.registry),
                "/Info": pikepdf.String(intent.info), "/DestOutputProfile": icc,
            })
            root.OutputIntents = pikepdf.Array([pdf.make_indirect(oi)])
            rep.output_intent = intent.identifier
        else:
            rep.notes.append("no printer output-intent ICC profile available: file cannot be labelled PDF/X-4")
        pdfx = bool(label_pdfx and rep.output_intent and not rep.color.unsupported)
        if label_pdfx and rep.color.unsupported:
            rep.notes.append("colour constructs left unconverted: " + "; ".join(sorted(set(rep.color.unsupported))[:5]))
        info = pdf.docinfo
        info["/Title"] = pikepdf.String(title)
        info["/Author"] = pikepdf.String(author)
        info["/Creator"] = pikepdf.String(f"ScholarForge {__version__} (Quarto, LuaLaTeX)")
        info["/Producer"] = pikepdf.String(f"ScholarForge {__version__} / pikepdf")
        info["/CreationDate"] = pikepdf.String(_pdf_date(created))
        info["/ModDate"] = pikepdf.String(_pdf_date(created))
        info["/Trapped"] = Name("/False")
        if pdfx:
            info["/GTS_PDFXVersion"] = pikepdf.String("PDF/X-4")
        xmp = pikepdf.Stream(pdf, _xmp_packet(title, author, pdfx, created))
        xmp["/Type"] = Name.Metadata
        xmp["/Subtype"] = Name("/XML")
        root.Metadata = pdf.make_indirect(xmp)
        rep.pdfx_labelled = pdfx
        pdf.save(dst, min_version="1.6", fix_metadata_version=False, deterministic_id=False)
    return rep
