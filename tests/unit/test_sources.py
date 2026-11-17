"""Source verification against *recorded real API responses* (no network, no invented data)."""

import httpx
import pytest

from scholarforge.e2e.scripted_host import fixture_dir
from scholarforge.models.enums import SourceStatus
from scholarforge.models.results import CandidateSource
from scholarforge.science.sources.formats import to_bibtex, to_csl
from scholarforge.science.sources.http import ApiCache, FixtureTransport, HttpClient
from scholarforge.science.sources.resolver import Claimed, SourceResolver, family_name, make_citekey


@pytest.fixture
def resolver(tmp_path):
    http = HttpClient(cache=ApiCache(tmp_path / "c.sqlite"), transport=FixtureTransport(fixture_dir() / "http"),
                      max_retries=0)
    return SourceResolver(http)


def test_real_doi_verified(resolver):
    v = resolver.resolve(Claimed(title="Learning representations by back-propagating errors",
                                 authors=["David E. Rumelhart"], year=1986, doi="10.1038/323533a0"))
    assert v.status == SourceStatus.VERIFIED
    assert v.record.year == 1986 and "Rumelhart" in v.record.authors[0]


def test_fabricated_doi_not_found(resolver):
    v = resolver.resolve(Claimed(title="Gradient Flows in Infinite-Width Transformers Are Always Convex",
                                 authors=["John Doe"], year=2019, doi="10.9999/sf-fabricated.2019.00123"))
    assert v.status == SourceStatus.NOT_FOUND
    assert "fabricated" in v.reason


def test_real_doi_wrong_paper_is_mismatch(resolver):
    v = resolver.resolve(Claimed(title="Mastering the game of Go with deep neural networks and tree search",
                                 authors=["David Silver"], year=2016, doi="10.1038/nature14236"))
    assert v.status == SourceStatus.MISMATCH
    # the DOI belongs to another paper; the resolver also found the work the host meant and names its DOI
    assert "10.1038/nature16961" in v.reason
    assert any("Human-level control" in (e.get("record_title") or "") for e in v.evidence)


def test_arxiv_and_isbn(resolver):
    v = resolver.resolve(Claimed(title="Adam: A Method for Stochastic Optimization", authors=["Diederik P. Kingma"],
                                 year=2014, arxiv_id="1412.6980", kind="preprint"))
    assert v.status == SourceStatus.VERIFIED and v.abstract
    book = resolver.resolve(Claimed(title="Deep Learning", authors=["Ian Goodfellow"], year=2016,
                                    isbn="9780262035613", kind="book"))
    assert book.status == SourceStatus.VERIFIED


def test_offline_without_cache_is_pending(tmp_path):
    http = HttpClient(enabled=False, cache=ApiCache(tmp_path / "c.sqlite"))
    v = SourceResolver(http).resolve(Claimed(title="Some paper", doi="10.1000/xyz"))
    assert v.status == SourceStatus.PENDING


def test_server_errors_are_pending_not_not_found(tmp_path):
    def handler(request):
        return httpx.Response(503, text="maintenance")

    http = HttpClient(cache=ApiCache(tmp_path / "c.sqlite"), transport=httpx.MockTransport(handler), max_retries=0)
    v = SourceResolver(http).resolve(Claimed(title="Some paper", doi="10.1000/xyz"))
    assert v.status == SourceStatus.PENDING


def test_cache_serves_offline(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"message": {"title": ["A title for caching"], "DOI": "10.1/x"}})

    cache = ApiCache(tmp_path / "c.sqlite")
    live = HttpClient(cache=cache, transport=httpx.MockTransport(handler))
    assert live.get("https://api.crossref.org/works/10.1/x", service="crossref").ok
    offline = HttpClient(enabled=False, cache=cache)
    assert offline.get("https://api.crossref.org/works/10.1/x", service="crossref").from_cache
    assert calls["n"] == 1


def test_names_citekeys_and_bibliography_formats():
    assert family_name("Rumelhart, David E.") == "rumelhart"
    assert family_name("David E. Rumelhart") == "rumelhart"
    assert family_name("Léon Bottou") == "bottou"
    assert make_citekey(["Yann LeCun"], 2015, "Deep learning", set()) == "lecun2015deep"
    assert make_citekey(["Yann LeCun"], 2015, "Deep learning", {"lecun2015deep"}) == "lecun2015deepa"
    cand = CandidateSource(title="Learning & {braces} 100%", authors=["Doe, Jane"], year=2020,
                           doi="https://doi.org/10.1000/ABC", kind="article", venue="J_Test")
    assert cand.doi == "10.1000/ABC"
    from scholarforge.models.entities import Source

    src = Source(id="s", citekey="doe2020", title=cand.title, authors=cand.authors, year=2020, doi=cand.doi,
                 venue=cand.venue, created_at="x", updated_at="x")
    bib = to_bibtex(src)
    assert "\\&" in bib and "\\{braces\\}" in bib and "100\\%" in bib and "J\\_Test" in bib
    csl = to_csl(src)
    assert csl["author"] == [{"family": "Doe", "given": "Jane"}] and csl["issued"] == {"date-parts": [[2020]]}


@pytest.mark.parametrize("bad", ["not-a-doi", "10.1/space here"])
def test_candidate_validation(bad):
    with pytest.raises(ValueError):
        CandidateSource(title="Valid title", doi=bad)
