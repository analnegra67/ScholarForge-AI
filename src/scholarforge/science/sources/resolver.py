"""Decide whether a cited work exists and matches its claimed metadata.

Decision rules (most specific evidence first):

* an identifier (DOI / arXiv / ISBN) that resolves and whose title, first author and
  year agree with the claim -> ``verified``;
* an identifier that resolves to a *different* work -> ``mismatch`` (typical of
  hallucinated references: real DOI, wrong paper);
* identifiers that definitively do not resolve anywhere -> ``not_found``;
* no identifier: bibliographic search; a confident unique match -> ``verified``
  (identifiers are attached), otherwise ``unverifiable``;
* a retraction notice -> ``retracted``; services unreachable -> ``pending``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scholarforge.errors import NetworkUnavailable
from scholarforge.models.enums import SourceStatus
from scholarforge.science.sources.http import HttpClient
from scholarforge.science.sources.services import Arxiv, Crossref, OpenAlex, OpenLibrary, SemanticScholar, WorkRecord
from scholarforge.util.text import normalize_for_match, strip_accents, title_similarity


@dataclass
class Claimed:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    kind: str = "article"


@dataclass
class Verdict:
    status: SourceStatus
    reason: str
    record: WorkRecord | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    abstract: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reason": self.reason,
                "matched": self.record.summary() if self.record else None, "evidence": self.evidence}


def family_name(author: str) -> str:
    a = strip_accents(author or "").strip()
    if not a:
        return ""
    if "," in a:
        fam = a.split(",", 1)[0]
    else:
        parts = [p for p in re.split(r"\s+", a) if p]
        # drop suffixes and initials at the end ("Jr.", "III")
        while len(parts) > 1 and re.fullmatch(r"(jr\.?|sr\.?|ii|iii|iv)", parts[-1].lower()):
            parts.pop()
        fam = parts[-1]
    return normalize_for_match(fam).replace(" ", "")


def compare(claimed: Claimed, rec: WorkRecord, min_title: float) -> dict[str, Any]:
    sim = title_similarity(claimed.title, rec.title or "")
    tol = 3 if (claimed.kind == "book" or rec.kind == "book") else 1
    if claimed.year is None or rec.year is None:
        year_ok = None
    elif rec.service == "arxiv":
        # arXiv dates the first version; the cited (journal) year may be several years later
        year_ok = rec.year - 1 <= claimed.year <= rec.year + 5
    else:
        year_ok = abs(claimed.year - rec.year) <= tol
    author_ok = None
    if claimed.authors and rec.authors:
        first = family_name(claimed.authors[0])
        rec_fams = [family_name(a) for a in rec.authors]
        author_ok = bool(first) and (first in rec_fams[:3] or any(first and first in f for f in rec_fams[:3]))
    title_ok = sim >= min_title
    match = title_ok and author_ok is not False and year_ok is not False
    return {"service": rec.service, "title_similarity": round(sim, 3), "title_ok": title_ok, "year_ok": year_ok,
            "author_ok": author_ok, "match": match, "record_title": rec.title, "record_year": rec.year,
            "record_first_author": rec.authors[0] if rec.authors else None}


class SourceResolver:
    def __init__(self, http: HttpClient, *, min_title_similarity: float = 0.86, openalex_key: str | None = None,
                 use: dict[str, bool] | None = None):
        self.http = http
        self.min_title = min_title_similarity
        self.crossref = Crossref(http)
        self.openalex = OpenAlex(http, openalex_key)
        self.arxiv = Arxiv(http)
        self.s2 = SemanticScholar(http)
        self.openlibrary = OpenLibrary(http)
        self.use = {"crossref": True, "openalex": True, "arxiv": True, "semantic_scholar": True,
                    "openlibrary": True, **(use or {})}

    # ------------------------------------------------------------------------------------------
    def _try(self, fn, *args) -> tuple[WorkRecord | None, str | None]:
        try:
            return fn(*args), None
        except NetworkUnavailable as exc:
            return None, exc.message

    def resolve(self, claimed: Claimed) -> Verdict:
        evidence: list[dict[str, Any]] = []
        network_errors: list[str] = []
        matched: list[WorkRecord] = []
        mismatched: list[tuple[WorkRecord, dict]] = []
        definitive_missing: list[str] = []
        abstract: str | None = None
        retracted = False

        def consider(rec: WorkRecord | None, err: str | None, ident: str) -> None:
            nonlocal abstract, retracted
            if err:
                network_errors.append(err)
                return
            if rec is None:
                return
            if not rec.found:
                definitive_missing.append(f"{rec.service}:{ident}")
                evidence.append({"service": rec.service, "identifier": ident, "found": False})
                return
            cmp = compare(claimed, rec, self.min_title)
            evidence.append({"identifier": ident, "found": True, **cmp})
            retracted = retracted or rec.retracted
            if rec.abstract and not abstract:
                abstract = rec.abstract
            if cmp["match"]:
                matched.append(rec)
            else:
                mismatched.append((rec, cmp))

        doi_found_somewhere = False
        if claimed.doi:
            if self.use["crossref"]:
                rec, err = self._try(self.crossref.lookup_doi, claimed.doi)
                consider(rec, err, claimed.doi)
                doi_found_somewhere |= bool(rec and rec.found)
            if self.use["openalex"] and (not matched or not abstract):
                rec, err = self._try(self.openalex.lookup_doi, claimed.doi)
                consider(rec, err, claimed.doi)
                doi_found_somewhere |= bool(rec and rec.found)
            if self.use["semantic_scholar"] and not doi_found_somewhere and not matched:
                rec, err = self._try(self.s2.lookup, "DOI", claimed.doi)
                consider(rec, err, claimed.doi)
                doi_found_somewhere |= bool(rec and rec.found)
        if claimed.arxiv_id and self.use["arxiv"]:
            rec, err = self._try(self.arxiv.lookup, claimed.arxiv_id)
            consider(rec, err, f"arXiv:{claimed.arxiv_id}")
        if claimed.isbn and self.use["openlibrary"]:
            rec, err = self._try(self.openlibrary.lookup_isbn, claimed.isbn)
            consider(rec, err, f"ISBN:{claimed.isbn}")

        strong_ids = bool(claimed.doi or claimed.arxiv_id)
        matched_by_identifier = bool(matched)
        found_by_search: WorkRecord | None = None
        if not matched and (not strong_ids or definitive_missing or mismatched):
            # no identifier, an unconfirmed ISBN, or a wrong identifier: look the work up by its metadata
            for searcher, label in ((self._search_crossref, "crossref-search"), (self._search_openalex, "openalex-search")):
                if found_by_search:
                    break
                recs, err = searcher(claimed)
                if err:
                    network_errors.append(err)
                    continue
                best = None
                for rec in recs:
                    cmp = compare(claimed, rec, max(self.min_title, 0.9))
                    if cmp["match"] and (best is None or cmp["title_similarity"] > best[1]["title_similarity"]):
                        best = (rec, cmp)
                if best:
                    evidence.append({"identifier": label, "found": True, **best[1]})
                    found_by_search = best[0]
                    retracted = retracted or best[0].retracted
                    abstract = abstract or best[0].abstract
                else:
                    evidence.append({"identifier": label, "found": False, "candidates": len(recs)})

        if retracted:
            rec = matched[0] if matched else found_by_search
            return Verdict(SourceStatus.RETRACTED, "a retraction notice is registered for this work", rec, evidence,
                           abstract)
        if matched_by_identifier:
            best = matched[0]
            via = ", ".join(sorted({e["service"] for e in evidence if e.get("match")}))
            return Verdict(SourceStatus.VERIFIED, f"exists and metadata matches ({via})", best, evidence, abstract)
        if found_by_search is not None:
            if strong_ids and (definitive_missing or mismatched):
                ids = ", ".join(x for x in (found_by_search.doi and f"DOI {found_by_search.doi}",
                                            found_by_search.arxiv_id and f"arXiv {found_by_search.arxiv_id}") if x)
                return Verdict(SourceStatus.MISMATCH,
                               "the given identifier is wrong, but a matching work exists"
                               + (f" ({ids})" if ids else "") + "; correct the identifier",
                               found_by_search, evidence, None)
            return Verdict(SourceStatus.VERIFIED, "exists (unique bibliographic match by title, author and year)",
                           found_by_search, evidence, abstract)
        if mismatched:
            rec, cmp = mismatched[0]
            problems = []
            if not cmp["title_ok"]:
                problems.append(f"title differs (similarity {cmp['title_similarity']}): '{rec.title}'")
            if cmp["author_ok"] is False:
                problems.append(f"first author differs: '{cmp['record_first_author']}'")
            if cmp["year_ok"] is False:
                problems.append(f"year differs: {rec.year}")
            return Verdict(SourceStatus.MISMATCH, "identifier resolves to a work that does not match: "
                           + "; ".join(problems), rec, evidence, None)
        if network_errors and not definitive_missing:
            return Verdict(SourceStatus.PENDING, "bibliographic services unreachable: " + network_errors[0],
                           None, evidence)
        if definitive_missing and (claimed.doi or claimed.arxiv_id):
            return Verdict(SourceStatus.NOT_FOUND, "identifier does not resolve in any service ("
                           + ", ".join(definitive_missing) + "); the reference is probably fabricated or mistyped",
                           None, evidence)
        if network_errors:
            return Verdict(SourceStatus.PENDING, "incomplete check: " + network_errors[0], None, evidence)
        return Verdict(SourceStatus.UNVERIFIABLE, "no identifier and no confident bibliographic match", None, evidence)

    def _search_crossref(self, claimed: Claimed) -> tuple[list[WorkRecord], str | None]:
        if not self.use["crossref"]:
            return [], None
        try:
            return self.crossref.search(claimed.title, claimed.authors, claimed.year), None
        except NetworkUnavailable as exc:
            return [], exc.message

    def _search_openalex(self, claimed: Claimed) -> tuple[list[WorkRecord], str | None]:
        if not self.use["openalex"]:
            return [], None
        try:
            return self.openalex.search(claimed.title), None
        except NetworkUnavailable as exc:
            return [], exc.message


def make_citekey(authors: list[str], year: int | None, title: str, existing: set[str]) -> str:
    fam = family_name(authors[0]) if authors else ""
    fam = re.sub(r"[^a-z0-9]", "", fam)
    stop = {"a", "an", "the", "on", "of", "and", "in", "for", "to", "with", "by", "from", "at", "is"}
    words = [w for w in normalize_for_match(title).split() if w not in stop]
    word = re.sub(r"[^a-z0-9]", "", words[0]) if words else "work"
    base = f"{fam or 'anon'}{year or 'nd'}{word}"[:40]
    key = base
    suffix = ord("a")
    while key in existing:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    return key
