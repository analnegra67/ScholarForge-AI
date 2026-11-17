"""Bibliography serialisation: CSL-JSON (for Pandoc citeproc) and BibTeX (LaTeX export)."""

from __future__ import annotations

import re
from typing import Any

from scholarforge.models.entities import Source

CSL_TYPE = {"article": "article-journal", "book": "book", "chapter": "chapter", "proceedings": "paper-conference",
            "preprint": "article", "thesis": "thesis", "report": "report", "web": "webpage", "dataset": "dataset",
            "other": "document"}
BIB_TYPE = {"article": "article", "book": "book", "chapter": "incollection", "proceedings": "inproceedings",
            "preprint": "misc", "thesis": "phdthesis", "report": "techreport", "web": "misc", "dataset": "misc",
            "other": "misc"}


def split_name(name: str) -> dict[str, str]:
    name = name.strip()
    if "," in name:
        fam, given = name.split(",", 1)
        return {"family": fam.strip(), "given": given.strip()} if given.strip() else {"family": fam.strip()}
    parts = name.split()
    if len(parts) == 1:
        return {"family": parts[0]}
    return {"family": parts[-1], "given": " ".join(parts[:-1])}


def to_csl(src: Source) -> dict[str, Any]:
    item: dict[str, Any] = {"id": src.citekey, "type": CSL_TYPE.get(src.kind, "document"), "title": src.title}
    if src.authors:
        item["author"] = [split_name(a) for a in src.authors]
    if src.year:
        item["issued"] = {"date-parts": [[src.year]]}
    if src.venue and src.kind not in ("book", "preprint"):
        item["container-title"] = src.venue
    for key, value in (("volume", src.volume), ("issue", src.issue), ("page", src.pages),
                       ("publisher", src.publisher), ("DOI", src.doi), ("ISBN", src.isbn)):
        if value:
            item[key] = value
    if src.arxiv_id:
        item.setdefault("publisher", "arXiv")
        item["number"] = f"arXiv:{src.arxiv_id}"
        item["URL"] = f"https://arxiv.org/abs/{src.arxiv_id}"
    elif src.url and not src.doi:
        item["URL"] = src.url
    return item


_BIB_ESCAPE = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
               "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def bib_escape(text: str) -> str:
    return "".join(_BIB_ESCAPE.get(c, c) for c in text)


def to_bibtex(src: Source) -> str:
    fields: list[tuple[str, str]] = [("title", "{" + bib_escape(src.title) + "}")]
    if src.authors:
        fields.append(("author", " and ".join(bib_escape(a) for a in src.authors)))
    if src.year:
        fields.append(("year", str(src.year)))
    if src.venue and src.kind == "article":
        fields.append(("journal", bib_escape(src.venue)))
    elif src.venue and src.kind in ("chapter", "proceedings"):
        fields.append(("booktitle", bib_escape(src.venue)))
    for key, value in (("volume", src.volume), ("number", src.issue), ("pages", src.pages),
                       ("publisher", src.publisher), ("isbn", src.isbn)):
        if value:
            fields.append((key, bib_escape(str(value).replace("-", "--") if key == "pages" else str(value))))
    if src.doi:
        fields.append(("doi", src.doi))
    if src.arxiv_id:
        fields += [("eprint", src.arxiv_id), ("archivePrefix", "arXiv")]
    if src.url and not src.doi and not src.arxiv_id:
        fields.append(("url", src.url))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@{BIB_TYPE.get(src.kind, 'misc')}{{{src.citekey},\n{body}\n}}\n"


def short_reference(src: Source) -> str:
    first = src.authors[0].split(",")[0] if src.authors else "Anon."
    etal = " et al." if len(src.authors) > 1 else ""
    year = src.year or "n.d."
    title = re.sub(r"\s+", " ", src.title)
    return f"{first}{etal} ({year}). {title}"
