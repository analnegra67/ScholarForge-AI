"""Static analysis of Quarto Markdown blocks.

Extracts labels, cross-references, citations, figure markers and theorem-like
environments, and detects structural errors (unbalanced math, fences, divs,
reserved heading levels). Code blocks are masked first so that their content is
never mistaken for markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

XREF_PREFIXES = ("sec", "fig", "tbl", "eq", "thm", "lem", "cor", "prp", "cnj", "def", "exm", "exr", "sol", "rem",
                 "alg", "lst")
_PFX = "|".join(XREF_PREFIXES)
LABEL_DEF_RE = re.compile(r"\{#((?:" + _PFX + r")-[A-Za-z0-9][A-Za-z0-9_-]*)([^}]*)\}")
REF_RE = re.compile(r"(?<![\w@/])-?@((?:" + _PFX + r")-[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?)")
BRACKET_CITE_RE = re.compile(r"\[([^\[\]]*?@[^\[\]]*?)\]")
CITEKEY_IN_BRACKET_RE = re.compile(r"(?<![\w.])-?@([A-Za-z0-9_][A-Za-z0-9_:.#$%&+?<>~/-]*[A-Za-z0-9_])")
NARRATIVE_CITE_RE = re.compile(r"(?<![\w@/\[])@([A-Za-z0-9_][A-Za-z0-9_:.#$%&+?<>~/-]*[A-Za-z0-9_])")
FIG_MARKER_RE = re.compile(r"^[ \t]*<!--\s*sf:figure\s+(fig-[A-Za-z0-9][A-Za-z0-9_-]*)\s*-->[ \t]*$", re.M)
DIV_OPEN_RE = re.compile(r"^[ \t]*(:{3,})[ \t]*(\{[^}]*\}|[A-Za-z][\w-]*)[ \t]*$")
DIV_CLOSE_RE = re.compile(r"^[ \t]*(:{3,})[ \t]*$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*(\{[^}]*\})?[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(```+|~~~+)")

ENV_FROM_PREFIX = {
    "thm": "theorem", "lem": "lemma", "cor": "corollary", "prp": "proposition", "cnj": "conjecture",
    "def": "definition", "exm": "example", "exr": "exercise", "sol": "solution", "rem": "remark", "alg": "algorithm",
    "tbl": "table", "fig": "figure", "eq": "equation",
}
CLASS_ENVS = {"proof": "proof", "remark": "remark", "solution": "solution", "callout-note": "remark"}


@dataclass
class Problem:
    severity: str  # error | warning | info
    code: str
    message: str
    line: int | None = None


@dataclass
class Analysis:
    labels: dict[str, str] = field(default_factory=dict)  # label -> kind
    label_titles: dict[str, str] = field(default_factory=dict)
    refs: set[str] = field(default_factory=set)
    citations: set[str] = field(default_factory=set)
    figure_markers: list[str] = field(default_factory=list)
    environments: dict[str, int] = field(default_factory=dict)
    headings: list[tuple[int, str]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    has_pipe_table: bool = False
    has_code: bool = False
    label_blocks: dict[str, str] = field(default_factory=dict)  # label -> text of its div (for dependency analysis)

    def elements(self) -> set[str]:
        found = set(self.environments)
        if self.figure_markers or any(k == "figure" for k in self.labels.values()):
            found.add("figure")
        if self.has_pipe_table:
            found.add("table")
        if self.has_code:
            found.add("algorithm")
        if any(k in ("equation",) for k in self.labels.values()):
            found.add("derivation")
        return found


def _mask_code(text: str) -> tuple[str, bool]:
    """Blank out fenced code blocks and inline code, preserving line numbers."""
    lines = text.split("\n")
    out: list[str] = []
    in_fence: str | None = None
    had_code = False
    for line in lines:
        m = FENCE_RE.match(line)
        if in_fence is None and m:
            in_fence = m.group(1)[0] * 3
            had_code = True
            out.append("")
            continue
        if in_fence is not None:
            if line.strip().startswith(in_fence):
                in_fence = None
            out.append("")
            continue
        out.append(re.sub(r"`[^`\n]*`", lambda mm: " " * len(mm.group(0)), line))
    masked = "\n".join(out)
    return masked, had_code if in_fence is None else had_code


def _fences_balanced(text: str) -> bool:
    count = 0
    open_char = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if not m:
            continue
        ch = m.group(1)[0]
        if open_char is None:
            open_char = ch
            count += 1
        elif ch == open_char:
            open_char = None
            count += 1
    return count % 2 == 0


def _strip_math_for_dollar_check(text: str) -> tuple[str, int]:
    """Return text with display math removed and the number of $$ delimiters."""
    n_display = len(re.findall(r"(?<!\\)\$\$", text))
    stripped = re.sub(r"(?<!\\)\$\$.*?(?<!\\)\$\$", " ", text, flags=re.S)
    return stripped, n_display


def analyze(markdown: str) -> Analysis:
    res = Analysis()
    if not _fences_balanced(markdown):
        res.problems.append(Problem("error", "UNBALANCED_CODE_FENCE", "a fenced code block (```) is not closed"))
    text, had_code = _mask_code(markdown)
    res.has_code = had_code

    # display / inline math balance
    stripped, n_display = _strip_math_for_dollar_check(text)
    if n_display % 2:
        res.problems.append(Problem("error", "UNBALANCED_DISPLAY_MATH", "odd number of $$ delimiters"))
    for i, para in enumerate(re.split(r"\n\s*\n", stripped)):
        singles = len(re.findall(r"(?<![\\$])\$(?!\$)", para))
        if singles % 2:
            res.problems.append(Problem("error", "UNBALANCED_INLINE_MATH",
                                        f"paragraph {i + 1} has an unmatched $ (inline math)"))
    # math with nothing but whitespace
    if re.search(r"\$\$\s*\$\$", text):
        res.problems.append(Problem("warning", "EMPTY_MATH", "empty display math block"))

    # headings
    for ln, line in enumerate(text.split("\n"), start=1):
        h = HEADING_RE.match(line)
        if h:
            level = len(h.group(1))
            res.headings.append((level, h.group(2)))
            if level <= 2:
                res.problems.append(Problem("error", "RESERVED_HEADING_LEVEL",
                                            f"heading level {level} is reserved for chapters/sections; use ### or deeper",
                                            ln))
        if re.match(r"^\s*\|.*\|\s*$", line) and re.search(r"\|\s*:?-{3,}", line):
            res.has_pipe_table = True

    # divs: balance and environments
    stack: list[tuple[str, str | None, int, int]] = []  # (colons, label, start_line, start_offset)
    lines = text.split("\n")
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    for ln, line in enumerate(lines, start=1):
        mo = DIV_OPEN_RE.match(line)
        if mo:
            attrs = mo.group(2)
            label = None
            m_id = re.search(r"#((?:" + _PFX + r")-[A-Za-z0-9][A-Za-z0-9_-]*)", attrs)
            if m_id:
                label = m_id.group(1)
                env = ENV_FROM_PREFIX.get(label.split("-", 1)[0])
                if env and env not in ("figure", "equation", "table"):
                    res.environments[env] = res.environments.get(env, 0) + 1
            for cls in re.findall(r"\.([\w-]+)", attrs) if attrs.startswith("{") else [attrs]:
                env = CLASS_ENVS.get(cls)
                if env:
                    res.environments[env] = res.environments.get(env, 0) + 1
            stack.append((mo.group(1), label, ln, offsets[ln - 1]))
            continue
        mc = DIV_CLOSE_RE.match(line)
        if mc:
            if not stack:
                res.problems.append(Problem("error", "UNBALANCED_DIV", "closing ::: without an opening div", ln))
            else:
                _, label, _, start = stack.pop()
                if label:
                    res.label_blocks[label] = text[start: offsets[ln - 1]]
    for colons, label, ln, _ in stack:
        res.problems.append(Problem("error", "UNBALANCED_DIV", f"div opened at line {ln} is never closed", ln))

    # labels
    for m in LABEL_DEF_RE.finditer(text):
        label = m.group(1)
        kind = label.split("-", 1)[0]
        if label in res.labels:
            res.problems.append(Problem("error", "DUPLICATE_LABEL", f"label {label} is defined twice in this block"))
        res.labels[label] = ENV_FROM_PREFIX.get(kind, kind)
        title = re.search(r'(?:name|title)="([^"]+)"', m.group(2) or "")
        if title:
            res.label_titles[label] = title.group(1)

    # references & citations
    res.refs = {m.group(1) for m in REF_RE.finditer(text)}
    cites: set[str] = set()
    for m in BRACKET_CITE_RE.finditer(text):
        for k in CITEKEY_IN_BRACKET_RE.finditer(m.group(1)):
            key = k.group(1)
            if not key.split("-", 1)[0] in XREF_PREFIXES or "-" not in key:
                cites.add(key)
    no_brackets = BRACKET_CITE_RE.sub(" ", text)
    no_brackets = re.sub(r"\S+@\S+\.\w+", " ", no_brackets)  # e-mail addresses
    for m in NARRATIVE_CITE_RE.finditer(no_brackets):
        key = m.group(1)
        prefix = key.split("-", 1)[0]
        if prefix in XREF_PREFIXES and "-" in key:
            continue
        cites.add(key)
    res.citations = cites

    res.figure_markers = FIG_MARKER_RE.findall(markdown)
    return res


FILLER_PATTERNS = [
    r"\bit is (?:important|worth|interesting) to (?:note|mention|emphasi[sz]e) that\b",
    r"\bit goes without saying\b", r"\bneedless to say\b", r"\bin today'?s (?:world|fast-paced)\b",
    r"\bas we all know\b", r"\bplays? a (?:crucial|vital|pivotal|key) role\b", r"\bdelve(?:s|d)? into\b",
    r"\bin the realm of\b", r"\ba testament to\b", r"\bat the end of the day\b", r"\bfirst and foremost\b",
    r"\bin a nutshell\b", r"\bunlock(?:s|ing)? the (?:power|potential)\b", r"\bgame[- ]changer\b",
    r"\bважно отметить\b", r"\bстоит отметить\b", r"\bследует отметить\b", r"\bкак известно\b",
    r"\bв современном мире\b", r"\bварто зазначити\b", r"\bвідомо, що\b",
]
_FILLER_RE = [re.compile(p, re.I) for p in FILLER_PATTERNS]


def filler_hits(text: str) -> list[str]:
    hits = []
    for rx in _FILLER_RE:
        hits.extend(m.group(0) for m in rx.finditer(text))
    return hits


def insert_index_entry(markdown: str, term: str) -> str:
    """Insert a LaTeX index entry right after the first occurrence of ``term`` (outside math/code)."""
    entry = term.replace("{", "").replace("}", "").replace("!", "").replace("@", "").replace("|", "")
    pattern = re.compile(r"(?<![\w$])(" + re.escape(term) + r")(?![\w$])", re.I)
    masked, _ = _mask_code(markdown)
    masked_math = re.sub(r"\$\$.*?\$\$", lambda m: " " * len(m.group(0)), masked, flags=re.S)
    masked_math = re.sub(r"\$[^$\n]+\$", lambda m: " " * len(m.group(0)), masked_math)
    masked_math = re.sub(r"\{[^}\n]*\}", lambda m: " " * len(m.group(0)), masked_math)
    masked_math = re.sub(r"^[ \t]*#{1,6} .*$", lambda m: " " * len(m.group(0)), masked_math, flags=re.M)
    masked_math = re.sub(r"^[ \t]*:{3,}.*$", lambda m: " " * len(m.group(0)), masked_math, flags=re.M)
    m = pattern.search(masked_math)
    if not m:
        return markdown
    end = m.end(1)
    return markdown[:end] + "`\\index{" + entry + "}`{=latex}" + markdown[end:]
