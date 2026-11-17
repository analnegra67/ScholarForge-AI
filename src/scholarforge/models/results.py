"""Schemas of results the host AI submits for each task kind.

These models are published to the host as JSON Schema (``output_schema`` in every
task), so they double as the contract of the host-AI protocol. Validation errors
are returned to the host verbatim so it can correct its submission.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LABEL_RE = re.compile(r"^(sec|fig|tbl|eq|thm|lem|cor|prp|cnj|def|exm|exr|sol|rem|alg)-[A-Za-z0-9][A-Za-z0-9_-]*$")
CITEKEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_:.#$%&+?<>~/-]*$")
LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

ELEMENTS = (
    "definition", "theorem", "lemma", "proposition", "corollary", "proof", "example", "exercise", "solution",
    "figure", "table", "algorithm", "remark", "derivation", "historical-note", "case-study", "summary",
)
CLAIM_KINDS = (
    "theorem", "lemma", "proposition", "corollary", "fact", "empirical", "historical", "hypothesis",
    "conjecture", "interpretation", "definition-consequence", "clinical", "statistical",
)


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- research.plan
class ConceptNode(_Result):
    concept: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    depends_on: list[str] = Field(default_factory=list)


class TheoryStatus(_Result):
    name: str = Field(min_length=1, max_length=300)
    status: Literal["established", "widely_accepted", "contested", "speculative"]
    notes: str = Field("", max_length=3000)


class Convention(_Result):
    topic: str = Field(min_length=1, max_length=200)
    choice: str = Field(min_length=1, max_length=1000)
    rationale: str = Field("", max_length=2000)


class CandidateSource(_Result):
    title: str = Field(min_length=3, max_length=1000)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(None, ge=1400, le=2100)
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    url: str | None = None
    venue: str | None = None
    kind: Literal["article", "book", "chapter", "proceedings", "preprint", "thesis", "report", "web", "dataset",
                  "other"] = "article"
    relevance: str = Field("", max_length=1000)
    citekey: str | None = Field(None, description="optional preferred citation key")

    @field_validator("doi")
    @classmethod
    def _doi(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        v = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", v, flags=re.I)
        if not re.match(r"^10\.\d{4,9}/\S+$", v):
            raise ValueError(f"not a DOI: {v!r}")
        return v

    @field_validator("arxiv_id")
    @classmethod
    def _arxiv(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = re.sub(r"^(arxiv:|https?://arxiv\.org/(abs|pdf)/)", "", v.strip(), flags=re.I).removesuffix(".pdf")
        if not re.match(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", v):
            raise ValueError(f"not an arXiv identifier: {v!r}")
        return v

    @field_validator("isbn")
    @classmethod
    def _isbn(cls, v: str | None) -> str | None:
        if not v:
            return None
        digits = re.sub(r"[^0-9Xx]", "", v)
        if len(digits) not in (10, 13):
            raise ValueError(f"not an ISBN: {v!r}")
        return digits.upper()

    @field_validator("citekey")
    @classmethod
    def _citekey(cls, v: str | None) -> str | None:
        if v and not CITEKEY_RE.match(v):
            raise ValueError(f"invalid citation key {v!r}")
        return v


class ResearchPlanResult(_Result):
    field_name: str = Field(min_length=2, max_length=300, description="the field of knowledge")
    scope: str = Field(min_length=10, max_length=5000)
    subfields: list[str] = Field(default_factory=list)
    concept_map: list[ConceptNode] = Field(min_length=3)
    prerequisites: list[str] = Field(default_factory=list)
    key_theories: list[TheoryStatus] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    conventions: list[Convention] = Field(default_factory=list)
    candidate_sources: list[CandidateSource] = Field(min_length=1)
    profile_id: str | None = Field(None, description="optional: a better-fitting domain profile id")
    notes: str = Field("", max_length=5000)


class SourceFix(_Result):
    source_id: str
    action: Literal["correct", "drop"]
    corrected: CandidateSource | None = None

    @model_validator(mode="after")
    def _needs_correction(self) -> "SourceFix":
        if self.action == "correct" and self.corrected is None:
            raise ValueError("action 'correct' requires 'corrected' metadata")
        return self


class SourcesReviewResult(_Result):
    fixes: list[SourceFix]


# --------------------------------------------------------------------------- architecture
class BlockPlan(_Result):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=10, max_length=3000)
    elements: list[str] = Field(default_factory=list)
    target_words: int = Field(600, ge=80, le=6000)
    key_terms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list, description="citekeys expected to be cited")

    @field_validator("elements")
    @classmethod
    def _elements(cls, v: list[str]) -> list[str]:
        bad = [e for e in v if e not in ELEMENTS]
        if bad:
            raise ValueError(f"unknown elements {bad}; allowed: {list(ELEMENTS)}")
        return v


class SectionPlan(_Result):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field("", max_length=3000)
    blocks: list[BlockPlan] = Field(min_length=1)


class ChapterPlan(_Result):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=10, max_length=5000)
    prerequisites: list[str] = Field(default_factory=list)
    sections: list[SectionPlan] = Field(default_factory=list)
    detail_later: bool = Field(False, description="true: sections will be planned in a later task (large books)")
    target_words: int | None = Field(None, ge=200, le=500000, description="size estimate when detail_later is true")
    appendix: bool = False

    @model_validator(mode="after")
    def _sections(self) -> "ChapterPlan":
        if not self.sections and not self.detail_later:
            raise ValueError(f"chapter '{self.title}' needs sections (or detail_later: true)")
        return self


class PartPlan(_Result):
    title: str = Field(min_length=1, max_length=300)
    chapters: list[ChapterPlan] = Field(min_length=1)


class VolumePlan(_Result):
    title: str = Field(min_length=1, max_length=300)
    parts: list[PartPlan] = Field(default_factory=list)
    chapters: list[ChapterPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _content(self) -> "VolumePlan":
        if bool(self.parts) == bool(self.chapters):
            raise ValueError(f"volume '{self.title}' must have either parts or chapters (not both, not neither)")
        return self


class OutlineResult(_Result):
    title: str = Field(min_length=1, max_length=300)
    subtitle: str | None = Field(None, max_length=300)
    volumes: list[VolumePlan] = Field(default_factory=list)
    parts: list[PartPlan] = Field(default_factory=list)
    chapters: list[ChapterPlan] = Field(default_factory=list)
    appendices: list[ChapterPlan] = Field(default_factory=list)
    notation_conventions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_layout(self) -> "OutlineResult":
        used = sum(bool(x) for x in (self.volumes, self.parts, self.chapters))
        if used != 1:
            raise ValueError("provide exactly one of: volumes, parts, chapters")
        return self


class ChapterDetailResult(_Result):
    sections: list[SectionPlan] = Field(min_length=1)


# --------------------------------------------------------------------------- drafting
class TermDef(_Result):
    term: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=3, max_length=3000)
    aliases: list[str] = Field(default_factory=list)
    redefine: bool = Field(False, description="true only to deliberately change an existing definition")
    justification: str | None = Field(None, max_length=1000)


class NotationDef(_Result):
    symbol: str = Field(min_length=1, max_length=120, description="LaTeX, e.g. \\nabla f")
    meaning: str = Field(min_length=2, max_length=500)
    scope: Literal["global", "chapter"] = "global"


class ClaimSpec(_Result):
    text: str = Field(min_length=5, max_length=3000)
    kind: str
    label: str | None = None
    citations: list[str] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list, description="ids of math_checks supporting this claim")
    speculative: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in CLAIM_KINDS:
            raise ValueError(f"unknown claim kind {v!r}; allowed: {list(CLAIM_KINDS)}")
        return v

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        if v and not LABEL_RE.match(v):
            raise ValueError(f"invalid label {v!r} (expected e.g. thm-chain-rule)")
        return v


MATH_CHECK_KINDS = (
    "identity", "derivative", "gradient", "integral", "limit", "series", "solve", "inequality",
    "matrix_identity", "numeric", "dimension", "lean",
)


class MathCheckSpec(_Result):
    """A machine-checkable assertion. Expressions use SymPy syntax (``**`` for powers, ``exp``, ``log``...)."""

    id: str
    kind: str
    description: str = Field("", max_length=1000)
    scope: Literal["general", "instance"] = Field(
        "general", description="'general': the check proves the claim itself; 'instance': it checks a special "
                               "case (fixed dimensions, sample values) and is evidence, not proof")
    lhs: str | None = None
    rhs: str | None = None
    expr: str | None = None
    expected: str | list[str] | None = None
    var: str | list[str] | None = None
    point: str | None = None
    lower: str | None = None
    upper: str | None = None
    order: int | None = Field(None, ge=1, le=12)
    direction: Literal["+", "-", "+-"] | None = None
    symbols: dict[str, dict[str, bool]] = Field(default_factory=dict,
                                                 description="assumptions, e.g. {'x': {'real': true}}")
    functions: list[str] = Field(default_factory=list, description="undefined function names, e.g. ['f','g']")
    matrices: dict[str, list[int]] = Field(default_factory=dict, description="name -> [rows, cols]")
    dimensions: dict[str, str] = Field(default_factory=dict, description="symbol -> dimension, e.g. 'mass*length/time**2'")
    domain: dict[str, list[float]] = Field(default_factory=dict, description="symbol -> [low, high] for sampling")
    tolerance: float | None = Field(None, gt=0, lt=1)
    code: str | None = Field(None, max_length=20000, description="Lean 4 source for kind='lean'")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not LOCAL_ID_RE.match(v):
            raise ValueError("id must be lowercase letters, digits, '-' or '_' (max 64)")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in MATH_CHECK_KINDS:
            raise ValueError(f"unknown math check kind {v!r}; allowed: {list(MATH_CHECK_KINDS)}")
        return v

    @model_validator(mode="after")
    def _required(self) -> "MathCheckSpec":
        need = {
            "identity": ("lhs", "rhs"), "matrix_identity": ("lhs", "rhs"), "dimension": ("lhs", "rhs"),
            "derivative": ("expr", "var", "expected"), "gradient": ("expr", "var", "expected"),
            "integral": ("expr", "var", "expected"), "limit": ("expr", "var", "point", "expected"),
            "series": ("expr", "var", "point", "order", "expected"), "solve": ("lhs", "var", "expected"),
            "inequality": ("expr",), "numeric": ("expr", "expected"), "lean": ("code",),
        }[self.kind]
        missing = [f for f in need if getattr(self, f) in (None, "", [])]
        if missing:
            raise ValueError(f"math check '{self.id}' of kind {self.kind} requires {missing}")
        if self.kind == "dimension" and not self.dimensions:
            raise ValueError("dimension checks require 'dimensions' for the symbols involved")
        if self.kind == "matrix_identity" and not self.matrices:
            raise ValueError("matrix_identity requires 'matrices' with shapes")
        if self.kind == "matrix_identity":
            self.scope = "instance"  # fixed shapes: never a proof of the general statement
        return self


class FigureRequest(_Result):
    label: str
    renderer: Literal["matplotlib", "graphviz", "tikz", "svg", "mermaid", "external"]
    code: str | None = Field(None, max_length=60000, description="plot code / DOT / TikZ / SVG / Mermaid source")
    prompt: str | None = Field(None, max_length=4000, description="for renderer='external' (paid image API)")
    caption: str = Field(min_length=5, max_length=2000)
    alt_text: str = Field(min_length=10, max_length=2000)
    width: float = Field(0.85, gt=0.1, le=1.0, description="fraction of text width")
    data_provenance: str | None = Field(None, max_length=1000)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        if not LABEL_RE.match(v) or not v.startswith("fig-"):
            raise ValueError(f"figure label must look like fig-name, got {v!r}")
        return v

    @model_validator(mode="after")
    def _payload(self) -> "FigureRequest":
        if self.renderer == "external":
            if not self.prompt:
                raise ValueError(f"{self.label}: renderer 'external' requires 'prompt'")
        elif not self.code:
            raise ValueError(f"{self.label}: renderer '{self.renderer}' requires 'code'")
        return self


class BlockDraftResult(_Result):
    markdown: str = Field(min_length=1)
    summary: str = Field(min_length=10, max_length=2000)
    terms: list[TermDef] = Field(default_factory=list)
    notation: list[NotationDef] = Field(default_factory=list)
    claims: list[ClaimSpec] = Field(default_factory=list)
    math_checks: list[MathCheckSpec] = Field(default_factory=list)
    figures: list[FigureRequest] = Field(default_factory=list)
    new_sources: list[CandidateSource] = Field(default_factory=list)
    changes_summary: str | None = Field(None, max_length=3000, description="for revisions: what changed and why")

    @model_validator(mode="after")
    def _unique_ids(self) -> "BlockDraftResult":
        ids = [c.id for c in self.math_checks]
        if len(ids) != len(set(ids)):
            raise ValueError("math_checks ids must be unique within a block")
        labels = [f.label for f in self.figures]
        if len(labels) != len(set(labels)):
            raise ValueError("figure labels must be unique")
        return self


# --------------------------------------------------------------------------- verification / review
class SupportAssessment(_Result):
    claim_id: str
    citekey: str
    verdict: Literal["supports", "partially_supports", "does_not_support", "contradicts", "cannot_determine"]
    evidence_quote: str | None = Field(None, max_length=1500,
                                       description="verbatim quote from the provided abstract/text, if any")
    notes: str = Field("", max_length=2000)


class ClaimSupportResult(_Result):
    assessments: list[SupportAssessment]


class ReviewFinding(_Result):
    block_id: str
    severity: Literal["minor", "major", "critical"]
    category: Literal["logic", "correctness", "clarity", "consistency", "notation", "terminology", "redundancy",
                      "missing_content", "citation", "level", "structure"]
    description: str = Field(min_length=5, max_length=3000)
    suggestion: str = Field("", max_length=3000)


class ChapterReviewResult(_Result):
    findings: list[ReviewFinding] = Field(default_factory=list)
    overall_assessment: str = Field(min_length=5, max_length=5000)


class RecheckItem(_Result):
    block_id: str
    action: Literal["no_change", "revised"]
    markdown: str | None = None
    note: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _md(self) -> "RecheckItem":
        if self.action == "revised" and not self.markdown:
            raise ValueError("action 'revised' requires the full revised markdown")
        return self


class RecheckResult(_Result):
    items: list[RecheckItem]


class FigureFixResult(_Result):
    label: str
    renderer: Literal["matplotlib", "graphviz", "tikz", "svg", "mermaid"] | None = None
    code: str = Field(min_length=1, max_length=60000)
    caption: str | None = None
    alt_text: str | None = None


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
