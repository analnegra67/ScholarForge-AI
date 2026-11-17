"""Text utilities: slugs, normalisation, word counts, similarity.

Everything here is deterministic and language-agnostic enough for multilingual books:
Cyrillic and Greek are transliterated for file names, and normalisation strips
diacritics for fuzzy comparisons (titles, author names).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_TRANSLIT = {
    # Russian / Ukrainian / Belarusian
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e", "є": "ye",
    "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ў": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
    # Greek (common in scientific titles)
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
    "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
    "ο": "o", "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}


def transliterate(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            out.append(_TRANSLIT[low])
        else:
            out.append(ch)
    return "".join(out)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def slugify(text: str, max_len: int = 60, fallback: str = "item") -> str:
    s = strip_accents(transliterate(text)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or fallback


def normalize_for_match(text: str) -> str:
    """Casefold, strip accents/markup/punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"<[^>]+>", " ", s)  # JATS/HTML tags in Crossref titles
    s = re.sub(r"\$[^$]*\$", " ", s)  # inline TeX
    s = strip_accents(s).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str, b: str) -> float:
    """Similarity in [0, 1] robust to word order, punctuation and subtitles."""
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb)
    # A title that is a prefix of the other ("Deep learning" vs "Deep learning: an introduction")
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    prefix = 1.0 if longer.startswith(shorter) and len(shorter) >= 12 else 0.0
    containment = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(seq, jacc, 0.92 * prefix, 0.9 * containment if min(len(ta), len(tb)) >= 3 else 0.0)


_MATH_BLOCK = re.compile(r"\$\$.*?\$\$", re.S)
_MATH_INLINE = re.compile(r"\$[^$\n]+\$")
_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)


def strip_markup(markdown: str) -> str:
    s = _CODE_BLOCK.sub(" ", markdown)
    s = _MATH_BLOCK.sub(" ", s)
    s = _MATH_INLINE.sub(" MATH ", s)
    s = re.sub(r"^:::.*$", " ", s, flags=re.M)  # fenced div markers
    s = re.sub(r"\{[#.][^}]*\}", " ", s)  # attributes
    s = re.sub(r"\[@[^\]]+\]", " ", s)  # citations
    s = re.sub(r"@(?:sec|fig|tbl|eq|thm|lem|cor|prp|cnj|def|exm|exr|sol|rem|alg)-[\w-]+", " ref ", s)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"[#*_`>|]", " ", s)
    return s


def word_count(markdown: str) -> int:
    return len(_WORD.findall(strip_markup(markdown)))


def sentences(text: str) -> list[str]:
    plain = re.sub(r"\s+", " ", strip_markup(text)).strip()
    if not plain:
        return []
    parts = re.split(r"(?<=[.!?。])\s+(?=[A-ZА-ЯЁІЇЄҐ0-9])", plain)
    return [p.strip() for p in parts if len(p.strip()) > 1]


def shingles(text: str, k: int = 5) -> set[tuple[str, ...]]:
    words = [w.casefold() for w in _WORD.findall(strip_markup(text))]
    if len(words) < k:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈4 characters per token) for context budgeting."""
    return max(1, len(text) // 4)


def truncate(text: str, max_chars: int, marker: str = " […]") -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max(0, max_chars - len(marker))]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    return cut + marker


def last_paragraphs(markdown: str, n: int = 2, max_chars: int = 1600) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]
    tail = "\n\n".join(paras[-n:])
    if len(tail) > max_chars:
        tail = "[…] " + tail[-max_chars:]
    return tail


def detect_script_language(text: str) -> str | None:
    """Very small heuristic used only as an intake fallback (the host AI normally decides)."""
    cyr = len(re.findall(r"[Ѐ-ӿ]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if cyr > lat:
        if re.search(r"[іїєґІЇЄҐ]", text):
            return "uk"
        return "ru"
    if lat:
        return "en"
    return None
