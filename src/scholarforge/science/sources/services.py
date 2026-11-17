"""Clients for Crossref, OpenAlex, arXiv, Semantic Scholar and Open Library.

Each client returns normalised :class:`WorkRecord` objects. ``lookup_*`` methods
return ``WorkRecord(found=False)`` for a definitive "does not exist" answer and
raise :class:`NetworkUnavailable` when no answer could be obtained.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from scholarforge.errors import NetworkUnavailable
from scholarforge.science.sources.http import HttpClient


@dataclass
class WorkRecord:
    service: str
    found: bool
    title: str | None = None
    authors: list[str] = field(default_factory=list)  # "Family, Given"
    year: int | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    url: str | None = None
    openalex_id: str | None = None
    kind: str | None = None
    abstract: str | None = None
    retracted: bool = False
    identifier: str | None = None  # what was looked up

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], False) and k != "abstract"}


def _strip_tags(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"<[^>]+>", " ", text)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip() or None


CROSSREF_KIND = {
    "journal-article": "article", "book": "book", "monograph": "book", "edited-book": "book",
    "book-chapter": "chapter", "proceedings-article": "proceedings", "posted-content": "preprint",
    "dissertation": "thesis", "report": "report", "reference-book": "book", "dataset": "dataset",
}


class Crossref:
    BASE = "https://api.crossref.org/works"

    def __init__(self, http: HttpClient):
        self.http = http

    @staticmethod
    def _record(msg: dict[str, Any], identifier: str | None = None) -> WorkRecord:
        title = (msg.get("title") or [None])[0]
        subtitle = (msg.get("subtitle") or [None])[0]
        if title and subtitle and subtitle.lower() not in title.lower():
            title = f"{title}: {subtitle}"
        authors = []
        for a in msg.get("author") or []:
            if a.get("family"):
                authors.append(f"{a['family']}, {a.get('given', '')}".strip().rstrip(","))
            elif a.get("name"):
                authors.append(a["name"])
        year = None
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (msg.get(key) or {}).get("date-parts") or [[None]]
            if parts and parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break
        retracted = any((u.get("type") or "").lower() in ("retraction", "removal")
                        for u in (msg.get("updated-by") or []))
        isbns = msg.get("ISBN") or []
        return WorkRecord(
            service="crossref", found=True, title=_strip_tags(title), authors=authors, year=year,
            venue=(msg.get("container-title") or [None])[0], volume=msg.get("volume"), issue=msg.get("issue"),
            pages=msg.get("page"), publisher=msg.get("publisher"), doi=(msg.get("DOI") or "").lower() or None,
            isbn=re.sub(r"[^0-9X]", "", isbns[0]) if isbns else None, url=msg.get("URL"),
            kind=CROSSREF_KIND.get(msg.get("type", ""), msg.get("type")), abstract=_strip_tags(msg.get("abstract")),
            retracted=retracted, identifier=identifier,
        )

    def lookup_doi(self, doi: str) -> WorkRecord:
        resp = self.http.get(f"{self.BASE}/{quote(doi, safe='/()-._;:<>')}", service="crossref")
        if resp.status in (404, 410):
            return WorkRecord(service="crossref", found=False, identifier=doi)
        if not resp.ok:
            raise NetworkUnavailable(f"Crossref returned HTTP {resp.status} for {doi}")
        return self._record(resp.json().get("message", {}), identifier=doi)

    def search(self, title: str, authors: list[str] | None = None, year: int | None = None, rows: int = 5) -> list[WorkRecord]:
        query = title
        if authors:
            query += " " + " ".join(a.split(",")[0] for a in authors[:2])
        params: dict[str, Any] = {"query.bibliographic": query, "rows": rows,
                                  "select": "DOI,title,subtitle,author,issued,published-print,published-online,"
                                            "container-title,volume,issue,page,publisher,type,ISBN,URL,updated-by"}
        resp = self.http.get(self.BASE, params, service="crossref")
        if not resp.ok:
            raise NetworkUnavailable(f"Crossref search failed with HTTP {resp.status}")
        return [self._record(item) for item in resp.json().get("message", {}).get("items", [])]


OPENALEX_KIND = {"article": "article", "book": "book", "book-chapter": "chapter", "preprint": "preprint",
                 "dissertation": "thesis", "report": "report", "dataset": "dataset", "review": "article"}


def _openalex_abstract(inv: dict[str, list[int]] | None) -> str | None:
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        positions.extend((i, word) for i in idxs)
    return " ".join(w for _, w in sorted(positions)) or None


class OpenAlex:
    BASE = "https://api.openalex.org/works"

    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.http = http
        self.api_key = api_key

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(extra or {})
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    @staticmethod
    def _record(w: dict[str, Any], identifier: str | None = None) -> WorkRecord:
        authors = []
        for a in w.get("authorships") or []:
            name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name")
            if name:
                parts = name.split()
                authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}".strip().rstrip(",") if len(parts) > 1 else name)
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        biblio = w.get("biblio") or {}
        pages = None
        if biblio.get("first_page"):
            pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
        doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
        return WorkRecord(
            service="openalex", found=True, title=_strip_tags(w.get("title") or w.get("display_name")),
            authors=authors, year=w.get("publication_year"), venue=src.get("display_name"),
            volume=biblio.get("volume"), issue=biblio.get("issue"), pages=pages, doi=doi,
            openalex_id=(w.get("id") or "").rsplit("/", 1)[-1] or None, url=loc.get("landing_page_url"),
            kind=OPENALEX_KIND.get(w.get("type", ""), w.get("type")),
            abstract=_openalex_abstract(w.get("abstract_inverted_index")), retracted=bool(w.get("is_retracted")),
            identifier=identifier,
        )

    def lookup_doi(self, doi: str) -> WorkRecord:
        resp = self.http.get(f"{self.BASE}/doi:{quote(doi, safe='/()-._;:<>')}", self._params(), service="openalex")
        if resp.status in (404, 410):
            return WorkRecord(service="openalex", found=False, identifier=doi)
        if not resp.ok:
            raise NetworkUnavailable(f"OpenAlex returned HTTP {resp.status} for {doi}")
        return self._record(resp.json(), identifier=doi)

    def search(self, title: str, rows: int = 5) -> list[WorkRecord]:
        clean = re.sub(r"[,:;|]", " ", title)
        resp = self.http.get(self.BASE, self._params({"search": clean, "per-page": rows}), service="openalex")
        if not resp.ok:
            raise NetworkUnavailable(f"OpenAlex search failed with HTTP {resp.status}")
        return [self._record(w) for w in resp.json().get("results", [])]


_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class Arxiv:
    BASE = "https://export.arxiv.org/api/query"

    def __init__(self, http: HttpClient):
        self.http = http

    def lookup(self, arxiv_id: str) -> WorkRecord:
        resp = self.http.get(self.BASE, {"id_list": arxiv_id, "max_results": 1}, service="arxiv")
        if not resp.ok:
            raise NetworkUnavailable(f"arXiv returned HTTP {resp.status}")
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise NetworkUnavailable(f"arXiv returned malformed XML: {exc}") from exc
        entries = root.findall("a:entry", _ATOM)
        if not entries:
            return WorkRecord(service="arxiv", found=False, identifier=arxiv_id)
        e = entries[0]
        entry_id = (e.findtext("a:id", default="", namespaces=_ATOM) or "")
        title = (e.findtext("a:title", default="", namespaces=_ATOM) or "").strip()
        if "api/errors" in entry_id or title.lower() == "error":
            return WorkRecord(service="arxiv", found=False, identifier=arxiv_id)
        authors = []
        for a in e.findall("a:author", _ATOM):
            name = (a.findtext("a:name", default="", namespaces=_ATOM) or "").strip()
            if name:
                parts = name.split()
                authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}".strip().rstrip(",") if len(parts) > 1 else name)
        published = e.findtext("a:published", default="", namespaces=_ATOM) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        doi = e.findtext("arxiv:doi", default=None, namespaces=_ATOM)
        journal = e.findtext("arxiv:journal_ref", default=None, namespaces=_ATOM)
        return WorkRecord(
            service="arxiv", found=True, title=re.sub(r"\s+", " ", title), authors=authors, year=year,
            venue=journal or "arXiv", doi=doi.lower() if doi else None, arxiv_id=arxiv_id,
            url=f"https://arxiv.org/abs/{arxiv_id}", kind="preprint",
            abstract=re.sub(r"\s+", " ", e.findtext("a:summary", default="", namespaces=_ATOM) or "").strip() or None,
            identifier=arxiv_id,
        )


class SemanticScholar:
    BASE = "https://api.semanticscholar.org/graph/v1/paper"
    FIELDS = "title,authors,year,venue,externalIds,abstract,publicationTypes,journal,isOpenAccess"

    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.http = http
        self.api_key = api_key  # sent as header would be ideal; the public API also works without one

    @staticmethod
    def _record(p: dict[str, Any], identifier: str | None = None) -> WorkRecord:
        authors = []
        for a in p.get("authors") or []:
            name = a.get("name") or ""
            parts = name.split()
            if parts:
                authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}".strip().rstrip(",") if len(parts) > 1 else name)
        ext = p.get("externalIds") or {}
        journal = p.get("journal") or {}
        return WorkRecord(service="semantic_scholar", found=True, title=p.get("title"), authors=authors,
                          year=p.get("year"), venue=p.get("venue") or journal.get("name"),
                          volume=journal.get("volume"), pages=journal.get("pages"),
                          doi=(ext.get("DOI") or "").lower() or None, arxiv_id=ext.get("ArXiv"),
                          abstract=p.get("abstract"), identifier=identifier)

    def lookup(self, kind: str, value: str) -> WorkRecord:
        resp = self.http.get(f"{self.BASE}/{kind}:{quote(value, safe='/()-._;:<>')}", {"fields": self.FIELDS},
                             service="semantic_scholar")
        if resp.status == 404:
            return WorkRecord(service="semantic_scholar", found=False, identifier=value)
        if not resp.ok:
            raise NetworkUnavailable(f"Semantic Scholar returned HTTP {resp.status}")
        return self._record(resp.json(), identifier=value)


class OpenLibrary:
    BASE = "https://openlibrary.org/api/books"

    def __init__(self, http: HttpClient):
        self.http = http

    def lookup_isbn(self, isbn: str) -> WorkRecord:
        key = f"ISBN:{isbn}"
        resp = self.http.get(self.BASE, {"bibkeys": key, "format": "json", "jscmd": "data"}, service="openlibrary")
        if not resp.ok:
            raise NetworkUnavailable(f"Open Library returned HTTP {resp.status}")
        data = resp.json().get(key)
        if not data:
            return WorkRecord(service="openlibrary", found=False, identifier=isbn)
        authors = []
        for a in data.get("authors") or []:
            parts = (a.get("name") or "").split()
            if parts:
                authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}".strip().rstrip(",") if len(parts) > 1 else parts[0])
        m = re.search(r"(1[5-9]\d\d|20\d\d)", data.get("publish_date") or "")
        publishers = data.get("publishers") or []
        title = data.get("title")
        if data.get("subtitle"):
            title = f"{title}: {data['subtitle']}"
        return WorkRecord(service="openlibrary", found=True, title=title, authors=authors,
                          year=int(m.group(1)) if m else None,
                          publisher=publishers[0].get("name") if publishers else None, isbn=isbn,
                          url=data.get("url"), kind="book", identifier=isbn)
