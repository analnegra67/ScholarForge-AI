"""Turn a free-text request into :class:`ProjectParams`.

Normally the host AI passes structured parameters. This deterministic parser is
the fallback used by the CLI (``scholarforge new --request``) and by the E2E
scenario, so the minimum required input really is just a topic sentence. It
recognises English, Russian and Ukrainian phrasing for the common options.
"""

from __future__ import annotations

import re

from scholarforge.models.project import ProjectParams
from scholarforge.util.text import detect_script_language

_ELEMENT_WORDS = {
    "proofs": ("proof", "доказ", "доведен"),
    "exercises": ("exercise", "problem set", "упражн", "задач", "вправ"),
    "figures": ("figure", "illustration", "diagram", "иллюстрац", "рисун", "ілюстрац"),
    "references": ("reference", "bibliograph", "citation", "библиограф", "литератур", "бібліограф", "джерел"),
    "examples": ("example", "пример", "приклад"),
    "computations": ("verifiable computation", "computation", "вычислен", "обчислен"),
}

_BOOK_TYPES = (
    ("encyclopedia", ("encyclopedia", "encyclopaedia", "энциклопед", "енциклопед")),
    ("handbook", ("handbook", "справочник", "довідник")),
    ("monograph", ("monograph", "монограф")),
    ("lecture_notes", ("lecture notes", "конспект лекц")),
    ("multivolume", ("multi-volume", "multivolume", "многотом", "багатотом")),
    ("textbook", ("textbook", "учебник", "підручник", "course book")),
)

_DEPTH = (
    ("exhaustive", ("exhaustive", "as deep as possible", "comprehensive", "максимально глубок", "исчерпывающ",
                    "максимально глибок")),
    ("rigorous", ("rigorous", "rigour", "rigor", "строг", "строгий")),
    ("overview", ("overview", "introduction to", "primer", "обзор", "огляд")),
)

_LEVELS = (
    ("research", ("research-level", "research level", "for researchers", "для исследовател")),
    ("graduate", ("graduate", "master", "phd", "магистр", "аспирант")),
    ("undergraduate", ("undergraduate", "bachelor", "студент", "бакалавр")),
    ("school", ("high school", "school", "школ")),
)

_TOPIC_PATTERNS = (
    re.compile(r"\b(?:about|on|regarding|covering)\s+(?:the\s+)?(?P<t>[^.;:\n]+)", re.I),
    re.compile(r"\b(?:по|о|об|про)\s+(?P<t>[^.;:\n]+)", re.I),
)

_INCLUDE = re.compile(r"\b(?:include|including|cover|covering|включ\w*|содерж\w*|включа\w*)\s*:?\s*(?P<l>[^.\n]+)", re.I)

_FORMAT_WORDS = {
    "print": ("print-ready", "print ready", "print edition", "for print", "печат", "друк"),
    "epub": ("epub", "e-book", "ebook"),
    "html": ("html", "website", "web version", "веб"),
    "pdf": ("pdf",),
    "latex": ("latex source",),
}

_LANG_WORDS = {
    "ru": ("in russian", "на русском"),
    "uk": ("in ukrainian", "українською", "на украинском"),
    "en": ("in english", "на английском", "англійською"),
    "de": ("in german", "auf deutsch"),
    "fr": ("in french", "en français"),
    "es": ("in spanish", "en español"),
}


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _clean_topic(raw: str) -> str:
    t = raw.strip().strip("\"'«»“”")
    t = re.split(r"\b(?:include|including|with|for|in (?:english|russian|ukrainian)|включ|с |со )", t, flags=re.I)[0]
    return t.strip(" ,;-").strip()


def _title_case(text: str) -> str:
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "via"}
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() or any(c.isupper() for c in w[1:]):
            out.append(w)
        elif i > 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def params_from_request(request: str, *, language: str | None = None) -> ProjectParams:
    text = request.strip()
    low = text.lower()

    topic = None
    for pat in _TOPIC_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = _clean_topic(m.group("t"))
            if len(candidate) >= 3:
                topic = candidate
                break
    if not topic:
        topic = re.split(r"[.\n]", text)[0][:200].strip() or text[:200]

    lang = language
    if not lang:
        for code, words in _LANG_WORDS.items():
            if _has(low, words):
                lang = code
                break
    lang = lang or detect_script_language(text) or "en"

    book_type = "textbook"
    for name, words in _BOOK_TYPES:
        if _has(low, words):
            book_type = name
            break

    depth = "standard"
    for name, words in _DEPTH:
        if _has(low, words):
            depth = name
            break

    level = "graduate" if depth in ("rigorous", "exhaustive") else "undergraduate"
    for name, words in _LEVELS:
        if _has(low, words):
            level = name
            break

    elements = [name for name, words in _ELEMENT_WORDS.items() if _has(low, words)]
    if not elements:
        elements = ["examples", "references"]

    formats = ["html", "epub", "pdf"]
    for fmt, words in _FORMAT_WORDS.items():
        if _has(low, words) and fmt not in formats:
            formats.append(fmt)

    required: list[str] = []
    m = _INCLUDE.search(text)
    if m:
        element_markers = tuple(w for words in _ELEMENT_WORDS.values() for w in words) + tuple(
            w for words in _FORMAT_WORDS.values() for w in words
        )
        for item in re.split(r",|\band\b|\bи\b|\bта\b|;", m.group("l")):
            item = item.strip().strip(".").strip()
            item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.I)
            if not item or len(item) < 3:
                continue
            if _has(item.lower(), element_markers):
                continue
            required.append(item)

    title = _title_case(topic) if lang == "en" else topic[:1].upper() + topic[1:]
    return ProjectParams(
        topic=topic,
        title=title,
        request=text,
        language=lang,
        level=level,
        depth=depth,
        book_type=book_type,
        formats=formats,
        required_topics=required,
        elements=elements,
    )
