"""Project-level source registry: registration, verification, bibliography export."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from scholarforge import secrets
from scholarforge.errors import NotFound
from scholarforge.logging_setup import get_logger
from scholarforge.models.entities import Source
from scholarforge.models.enums import CheckOutcome, SourceStatus
from scholarforge.models.results import CandidateSource
from scholarforge.science.sources.formats import short_reference, to_bibtex, to_csl
from scholarforge.science.sources.http import HttpClient
from scholarforge.science.sources.resolver import Claimed, SourceResolver, Verdict, make_citekey
from scholarforge.util.atomic import atomic_write_json, atomic_write_text
from scholarforge.util.text import title_similarity
from scholarforge.util.timeutil import now_iso

log = get_logger("sources")

CITABLE = (SourceStatus.VERIFIED,)
UNUSABLE = (SourceStatus.NOT_FOUND, SourceStatus.MISMATCH, SourceStatus.RETRACTED, SourceStatus.REJECTED)
NEEDS_ATTENTION = (SourceStatus.NOT_FOUND, SourceStatus.MISMATCH, SourceStatus.UNVERIFIABLE)

# Tests can inject a transport (fixture replay) for every SourceManager created afterwards.
DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


class SourceManager:
    def __init__(self, project, http: HttpClient | None = None):
        self.project = project
        self.pdb = project.db
        settings = project.settings
        self.http = http or HttpClient.from_settings(settings, transport=DEFAULT_TRANSPORT)
        self.resolver = SourceResolver(
            self.http, min_title_similarity=settings.sources.min_title_similarity,
            openalex_key=secrets.get_secret("openalex_api_key"),
            use={"crossref": settings.sources.crossref, "openalex": settings.sources.openalex,
                 "arxiv": settings.sources.arxiv, "semantic_scholar": settings.sources.semantic_scholar,
                 "openlibrary": settings.sources.openlibrary},
        )

    # ------------------------------------------------------------------------------ register
    def find_duplicate(self, cand: CandidateSource) -> Source | None:
        dup = self.pdb.sources.by_identifier(doi=cand.doi, arxiv_id=cand.arxiv_id, isbn=cand.isbn)
        if dup:
            return dup
        if cand.citekey:
            dup = self.pdb.sources.by_citekey(cand.citekey)
            if dup and title_similarity(dup.title, cand.title) >= 0.9:
                return dup
        from scholarforge.science.sources.resolver import family_name

        for src in self.pdb.sources.list():
            if title_similarity(src.title, cand.title) < 0.96:
                continue
            if cand.year and src.year and abs(cand.year - src.year) > 1:
                continue
            if cand.authors and src.authors and family_name(cand.authors[0]) != family_name(src.authors[0]):
                continue  # same title, different first author: a different work ("Deep learning" 2015 vs 2016)
            return src
        return None

    def register(self, cand: CandidateSource, proposed_by: str = "host") -> tuple[Source, bool]:
        dup = self.find_duplicate(cand)
        if dup:
            return dup, False
        existing = self.pdb.sources.citekeys()
        key = cand.citekey if cand.citekey and cand.citekey not in existing else make_citekey(
            cand.authors, cand.year, cand.title, existing)
        src = self.pdb.sources.add(
            citekey=key, kind=cand.kind, title=cand.title, authors=cand.authors, year=cand.year, venue=cand.venue,
            doi=cand.doi, arxiv_id=cand.arxiv_id, isbn=cand.isbn, url=cand.url, relevance=cand.relevance,
            status=SourceStatus.PROPOSED, proposed_by=proposed_by,
        )
        self.pdb.events.log("source.registered", "source", src.id, {"citekey": key, "by": proposed_by})
        return src, True

    # -------------------------------------------------------------------------------- verify
    def verify(self, source_id: str) -> Verdict:
        src = self.pdb.sources.get(source_id)
        verdict = self.resolver.resolve(Claimed(title=src.title, authors=src.authors, year=src.year, doi=src.doi,
                                                arxiv_id=src.arxiv_id, isbn=src.isbn, kind=src.kind))
        update: dict[str, Any] = {"status": verdict.status,
                                  "verification": {**verdict.to_dict(), "checked_at": now_iso()}}
        rec = verdict.record
        if verdict.status == SourceStatus.VERIFIED and rec is not None:
            # canonical metadata from the registry replaces what the model remembered
            for attr in ("title", "year", "venue", "volume", "issue", "pages", "publisher", "openalex_id"):
                value = getattr(rec, attr)
                if value:
                    update[attr] = value
            if rec.authors:
                update["authors"] = rec.authors
            for attr in ("doi", "arxiv_id", "isbn", "url"):
                value = getattr(rec, attr)
                if value and not getattr(src, attr):
                    update[attr] = value
            if rec.kind in ("article", "book", "chapter", "proceedings", "preprint", "thesis", "report", "dataset"):
                update["kind"] = rec.kind if not (src.arxiv_id and rec.service == "arxiv") else "preprint"
        if verdict.abstract:
            update["abstract"] = verdict.abstract
        elif verdict.status == SourceStatus.VERIFIED and self.resolver.use.get("semantic_scholar"):
            abstract = self._extra_abstract(update.get("doi") or src.doi, update.get("arxiv_id") or src.arxiv_id)
            if abstract:
                update["abstract"] = abstract
        src = self.pdb.sources.update(source_id, **update)
        outcome = {SourceStatus.VERIFIED: CheckOutcome.PASS, SourceStatus.PENDING: CheckOutcome.INCONCLUSIVE,
                   SourceStatus.UNVERIFIABLE: CheckOutcome.INCONCLUSIVE}.get(verdict.status, CheckOutcome.FAIL)
        services = sorted({e.get("service", e.get("identifier", "?")) for e in verdict.evidence}) or ["none"]
        self.pdb.checks.add(subject_type="source", subject_id=source_id, check_type="source.exists",
                            method="+".join(str(s) for s in services), independent=True, outcome=outcome,
                            details=verdict.to_dict())
        self.pdb.events.log("source.verified", "source", source_id, {"status": verdict.status.value})
        log.info("source %s -> %s", src.citekey, verdict.status.value)
        return verdict

    def _extra_abstract(self, doi: str | None, arxiv_id: str | None) -> str | None:
        """Abstracts are evidence for claim support; some registries withhold them, so try Semantic Scholar."""
        from scholarforge.errors import NetworkUnavailable

        for kind, value in (("DOI", doi), ("ARXIV", arxiv_id)):
            if not value:
                continue
            try:
                rec = self.resolver.s2.lookup(kind, value)
            except NetworkUnavailable:
                return None
            if rec.found and rec.abstract:
                return rec.abstract
        return None

    def verify_pending(self, budget_s: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + budget_s
        todo = self.pdb.sources.list([SourceStatus.PROPOSED, SourceStatus.PENDING])
        done: dict[str, int] = {}
        remaining = 0
        for i, src in enumerate(todo):
            if time.monotonic() > deadline:
                remaining = len(todo) - i
                break
            verdict = self.verify(src.id)
            done[verdict.status.value] = done.get(verdict.status.value, 0) + 1
        return {"checked": done, "remaining": remaining,
                "pending_network": done.get(SourceStatus.PENDING.value, 0)}

    def drop(self, source_id: str, reason: str) -> Source:
        src = self.pdb.sources.update(source_id, status=SourceStatus.REJECTED,
                                      verification={**self.pdb.sources.get(source_id).verification,
                                                    "rejected_reason": reason, "rejected_at": now_iso()})
        self.pdb.events.log("source.rejected", "source", source_id, {"reason": reason})
        return src

    def correct(self, source_id: str, cand: CandidateSource) -> Source:
        self.pdb.sources.get(source_id)
        return self.pdb.sources.update(source_id, title=cand.title, authors=cand.authors, year=cand.year,
                                       venue=cand.venue, doi=cand.doi, arxiv_id=cand.arxiv_id, isbn=cand.isbn,
                                       url=cand.url, kind=cand.kind, status=SourceStatus.PROPOSED)

    # ------------------------------------------------------------------------------- queries
    def status_of(self, citekey: str) -> SourceStatus | None:
        src = self.pdb.sources.by_citekey(citekey)
        return None if src is None else src.status

    def citable(self) -> list[Source]:
        return self.pdb.sources.list(list(CITABLE))

    def for_context(self, limit: int = 120) -> list[dict[str, Any]]:
        out = []
        for src in self.citable()[:limit]:
            out.append({"citekey": src.citekey, "reference": short_reference(src), "kind": src.kind,
                        "relevance": (src.relevance or "")[:200]})
        return out

    def require(self, citekey: str) -> Source:
        src = self.pdb.sources.by_citekey(citekey)
        if src is None:
            raise NotFound(f"unknown citation key {citekey}")
        return src

    # ------------------------------------------------------------------------------- export
    def write_bibliography(self, directory: Path, cited_only: set[str] | None = None) -> dict[str, str]:
        directory.mkdir(parents=True, exist_ok=True)
        sources = [s for s in self.citable() if cited_only is None or s.citekey in cited_only]
        csl = [to_csl(s) for s in sources]
        json_path = atomic_write_json(directory / "references.json", csl)
        bib_path = atomic_write_text(directory / "references.bib", "\n".join(to_bibtex(s) for s in sources))
        return {"csl_json": str(json_path), "bibtex": str(bib_path), "count": str(len(sources))}
