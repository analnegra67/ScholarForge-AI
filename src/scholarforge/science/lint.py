"""Text-quality checks: length, planned elements, padding and repetition.

These checks are deterministic and cheap. They do not judge scientific truth;
they enforce the editorial contract (no padding, no repetition, planned content
present) and produce concrete feedback the host can act on.
"""

from __future__ import annotations

import re
from collections import Counter

from scholarforge.science.markdown import Analysis, Problem, filler_hits
from scholarforge.util.text import jaccard, normalize_for_match, sentences, shingles, word_count

ELEMENT_ALIASES = {
    "theorem": {"theorem", "lemma", "proposition", "corollary"},
    "lemma": {"lemma", "theorem"},
    "proposition": {"proposition", "theorem", "lemma"},
    "corollary": {"corollary", "theorem"},
    "definition": {"definition"},
    "proof": {"proof"},
    "example": {"example"},
    "exercise": {"exercise"},
    "solution": {"solution"},
    "figure": {"figure"},
    "table": {"table"},
    "algorithm": {"algorithm"},
    "remark": {"remark"},
    "derivation": {"derivation", "proof"},
}


def lint_block(markdown: str, analysis: Analysis, *, target_words: int, elements: list[str],
               other_blocks: dict[str, str], strict_length: bool = True) -> list[Problem]:
    problems: list[Problem] = []
    words = word_count(markdown)
    lo_err = max(60, int(target_words * 0.4))
    lo_warn = int(target_words * 0.7)
    hi_warn = int(target_words * 1.6)
    if words < lo_err and strict_length:
        problems.append(Problem("error", "TOO_SHORT", f"{words} words; the plan asks for about {target_words} "
                                                      f"(minimum accepted: {lo_err})"))
    elif words < lo_warn:
        problems.append(Problem("warning", "SHORT", f"{words} words; planned about {target_words}"))
    elif words > hi_warn:
        problems.append(Problem("warning", "LONG", f"{words} words; planned about {target_words}. Split or tighten "
                                                   "rather than pad"))

    found = analysis.elements()
    for el in elements:
        accepted = ELEMENT_ALIASES.get(el, {el})
        if el in ("historical-note", "case-study", "summary"):
            continue
        if not (found & accepted):
            problems.append(Problem("warning", "MISSING_ELEMENT", f"planned element '{el}' not found "
                                                                  f"(expected e.g. ::: {{#{_prefix(el)}-name}})"))

    fillers = filler_hits(markdown)
    if words and len(fillers) * 1000 / max(words, 1) > 3:
        problems.append(Problem("warning", "FILLER", f"{len(fillers)} filler phrases (e.g. '{fillers[0]}'); "
                                                     "remove padding"))

    # repetition inside the block
    sents = [normalize_for_match(s) for s in sentences(markdown)]
    counts = Counter(s for s in sents if len(s.split()) >= 8)
    dups = [s for s, n in counts.items() if n > 1]
    if dups:
        problems.append(Problem("warning", "REPEATED_SENTENCE", f"{len(dups)} sentence(s) repeated within the block: "
                                                                f"'{dups[0][:90]}…'"))

    # repetition against other blocks
    mine = shingles(markdown, 6)
    my_sents = {s for s in sents if len(s.split()) >= 10}
    for node_id, text in other_blocks.items():
        if not text:
            continue
        sim = jaccard(mine, shingles(text, 6))
        if sim >= 0.18:
            problems.append(Problem("warning", "OVERLAPS_OTHER_BLOCK",
                                    f"{sim:.0%} overlap (6-word shingles) with block {node_id}; avoid repeating "
                                    "earlier material — refer to it instead"))
            continue
        other_sents = {normalize_for_match(s) for s in sentences(text)}
        common = my_sents & other_sents
        if len(common) >= 2:
            problems.append(Problem("warning", "COPIED_SENTENCES",
                                    f"{len(common)} sentences copied from block {node_id}: '{next(iter(common))[:80]}…'"))

    long_paras = [p for p in re.split(r"\n\s*\n", markdown) if word_count(p) > 320]
    if long_paras:
        problems.append(Problem("info", "LONG_PARAGRAPH", f"{len(long_paras)} paragraph(s) over 320 words"))
    return problems


def _prefix(element: str) -> str:
    return {"theorem": "thm", "lemma": "lem", "proposition": "prp", "corollary": "cor", "definition": "def",
            "example": "exm", "exercise": "exr", "remark": "rem", "algorithm": "alg", "figure": "fig",
            "table": "tbl", "solution": "sol"}.get(element, element[:3])
