"""Project configuration stored in ``<project>/scholarforge.yaml``."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scholarforge.version import PROJECT_FORMAT_VERSION

Format = Literal["html", "epub", "pdf", "print", "latex", "quarto"]
ALL_FORMATS: tuple[str, ...] = ("html", "epub", "pdf", "print", "latex", "quarto")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectParams(_Model):
    """What the user asked for. Only ``topic`` is required; everything else has defaults."""

    topic: str = Field(min_length=3, max_length=500)
    title: str | None = Field(None, max_length=300)
    subtitle: str | None = Field(None, max_length=300)
    request: str | None = Field(None, max_length=20000, description="The user's request, verbatim")
    language: str = Field("en", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
    level: str = Field("graduate", max_length=120)
    audience: str | None = Field(None, max_length=500)
    depth: Literal["overview", "standard", "rigorous", "exhaustive"] = "rigorous"
    target_pages: int | None = Field(None, ge=1, le=1_000_000)
    book_type: Literal[
        "textbook", "monograph", "encyclopedia", "handbook", "reference", "lecture_notes", "multivolume"
    ] = "textbook"
    style: str | None = Field(None, max_length=500)
    formats: list[Format] = Field(default_factory=lambda: ["html", "epub", "pdf"])
    required_topics: list[str] = Field(default_factory=list)
    elements: list[str] = Field(default_factory=lambda: ["proofs", "exercises", "figures", "references"])
    illustrations: Literal["auto", "native_only", "external_allowed", "none"] = "auto"
    profile: str | None = None
    authors: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("formats")
    @classmethod
    def _dedupe_formats(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for f in value:
            if f not in seen:
                seen.append(f)
        return seen

    @field_validator("required_topics", "elements", "authors")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v and v.strip()]


# Trim sizes in millimetres (width, height).
TRIM_PRESETS: dict[str, tuple[float, float]] = {
    "5x8in": (127.0, 203.2),
    "5.5x8.5in": (139.7, 215.9),
    "6x9in": (152.4, 228.6),
    "6.14x9.21in": (155.956, 233.934),
    "7x10in": (177.8, 254.0),
    "7.5x9.25in": (190.5, 234.95),
    "8x10in": (203.2, 254.0),
    "8.5x11in": (215.9, 279.4),
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "b5": (176.0, 250.0),
    "royal": (156.0, 234.0),
    "demy": (138.0, 216.0),
}

# Paper caliper expressed as spine thickness added per *page* (i.e. half a leaf), in mm.
# KDP publishes per-page factors in inches: white 0.002252, cream 0.0025, premium colour 0.002347.
PAPER_PRESETS: dict[str, float] = {
    "kdp-white": 0.002252 * 25.4,
    "kdp-cream": 0.0025 * 25.4,
    "kdp-color-standard": 0.002252 * 25.4,
    "kdp-color-premium": 0.002347 * 25.4,
    "offset-80gsm": 0.050,
    "offset-90gsm": 0.056,
    "coated-115gsm": 0.048,
}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|in)\s*$", re.I)


def parse_trim(value: str) -> tuple[float, float]:
    key = value.strip().lower()
    if key in TRIM_PRESETS:
        return TRIM_PRESETS[key]
    m = _SIZE_RE.match(value)
    if not m:
        raise ValueError(f"unknown trim size {value!r}; use a preset ({', '.join(TRIM_PRESETS)}) or e.g. '170x240mm'")
    w, h, unit = float(m.group(1)), float(m.group(2)), m.group(3).lower()
    factor = {"mm": 1.0, "cm": 10.0, "in": 25.4}[unit]
    return w * factor, h * factor


class Margins(_Model):
    inner_mm: float = Field(19.0, ge=3, le=80)
    outer_mm: float = Field(15.0, ge=3, le=80)
    top_mm: float = Field(18.0, ge=3, le=80)
    bottom_mm: float = Field(20.0, ge=3, le=80)


class CoverSettings(_Model):
    background: str = Field("#1F3A5F", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str = Field("#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field("#E8B04B", pattern=r"^#[0-9A-Fa-f]{6}$")
    back_text: str | None = Field(None, max_length=3000)
    front_image: str | None = None  # path relative to project, or figure label
    spine_text: bool = True
    min_spine_text_mm: float = Field(6.0, ge=0, le=50)
    min_pages_for_spine_text: int = Field(79, ge=0, le=10000)


class PrintSettings(_Model):
    trim: str = "7x10in"
    bleed_mm: float = Field(3.175, ge=0, le=10)
    binding: Literal["perfect", "case", "saddle", "coil"] = "perfect"
    paper: str = "kdp-white"
    page_thickness_mm: float | None = Field(None, gt=0, le=1)
    ppi: float | None = Field(None, gt=50, le=2000, description="Pages per inch as quoted by the printer")
    color: Literal["bw", "color"] = "bw"
    margins: Margins = Field(default_factory=Margins)
    auto_gutter: bool = True
    crop_marks: bool = False
    pdfx: bool = True
    icc_profile: str = "auto"
    min_image_ppi: int = Field(300, ge=72, le=2400)
    font_size_pt: float = Field(10.5, ge=8, le=14)
    cover: CoverSettings = Field(default_factory=CoverSettings)
    printer_notes: str | None = None

    @field_validator("trim")
    @classmethod
    def _trim(cls, value: str) -> str:
        parse_trim(value)
        return value

    @field_validator("paper")
    @classmethod
    def _paper(cls, value: str) -> str:
        if value not in PAPER_PRESETS and value != "custom":
            raise ValueError(f"unknown paper preset {value!r}; known: {', '.join(PAPER_PRESETS)} or 'custom'")
        return value

    @model_validator(mode="after")
    def _custom_paper(self) -> "PrintSettings":
        if self.paper == "custom" and not (self.page_thickness_mm or self.ppi):
            raise ValueError("paper='custom' requires page_thickness_mm or ppi")
        return self

    def trim_mm(self) -> tuple[float, float]:
        return parse_trim(self.trim)

    def per_page_thickness_mm(self) -> float:
        if self.page_thickness_mm:
            return self.page_thickness_mm
        if self.ppi:
            return 25.4 / self.ppi
        return PAPER_PRESETS[self.paper]


class BuildSettings(_Model):
    csl: str | None = None
    block_headings: bool = True
    html_theme: str = "cosmo"
    pdf_documentclass: Literal["scrbook", "book", "scrreprt"] = "scrbook"
    pdf_papersize: Literal["a4", "letter"] = "a4"
    fontsize: Literal["10pt", "11pt", "12pt"] = "11pt"
    mainfont: str | None = None
    sansfont: str | None = None
    monofont: str | None = None
    mathfont: str | None = None
    number_sections: bool = True
    toc_depth: int = Field(2, ge=0, le=5)
    epub_math: Literal["mathml", "webtex", "plain"] = "mathml"
    include_verification_appendix: bool = True
    index: bool = True


class ProjectConfig(_Model):
    format_version: int = PROJECT_FORMAT_VERSION
    id: str
    slug: str
    created_at: str
    engine_version: str
    params: ProjectParams
    print: PrintSettings = Field(default_factory=PrintSettings)
    build: BuildSettings = Field(default_factory=BuildSettings)

    @property
    def title(self) -> str:
        return self.params.title or self.params.topic
