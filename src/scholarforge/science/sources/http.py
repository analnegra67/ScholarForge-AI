"""HTTP access for scholarly APIs: cache, retries, rate limits, offline mode, record/replay.

* Successful responses and definitive ``404``s are cached in ``<cache_dir>/http_cache.sqlite``
  (404s for a shorter time); offline, cached answers are still served.
* ``429``/``5xx``/timeouts are retried with exponential backoff honouring ``Retry-After``.
* Per-service minimum intervals respect published fair-use limits (arXiv: 3 s).
* ``SCHOLARFORGE_HTTP_RECORD=<dir>`` writes every live response to fixture files;
  :class:`FixtureTransport` replays them in tests (real data, no network).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from scholarforge import paths
from scholarforge.errors import NetworkUnavailable
from scholarforge.logging_setup import get_logger
from scholarforge.util.timeutil import iso_in, now_iso

log = get_logger("http")

MIN_INTERVAL_S = {"arxiv": 3.0, "crossref": 0.1, "openalex": 0.12, "semantic_scholar": 1.1, "openlibrary": 0.4}
_last_call: dict[str, float] = {}
_lock = threading.Lock()


@dataclass
class HttpResponse:
    status: int
    text: str
    from_cache: bool = False
    url: str = ""

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def request_key(url: str, params: dict[str, Any] | None) -> tuple[str, str]:
    """Stable (key, display_url); secrets (api_key) are excluded so they never reach the cache or fixtures."""
    base = str(httpx.URL(url).copy_with(query=None))
    clean = {k: str(v) for k, v in sorted((params or {}).items()) if k not in ("api_key", "key", "mailto")}
    full = base + ("?" + urlencode(clean, doseq=True) if clean else "")
    return hashlib.sha256(full.encode("utf-8")).hexdigest(), full


class ApiCache:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.cache_dir() / "http_cache.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, url TEXT, status INTEGER, body TEXT,"
                " fetched_at TEXT, expires_at TEXT)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15)
        return conn

    def get(self, key: str, *, allow_expired: bool = False) -> HttpResponse | None:
        with self._conn() as c:
            row = c.execute("SELECT url, status, body, expires_at FROM http_cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        url, status, body, expires_at = row
        if not allow_expired and expires_at and expires_at < now_iso():
            return None
        return HttpResponse(status=status, text=body, from_cache=True, url=url)

    def put(self, key: str, url: str, status: int, body: str, ttl_s: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO http_cache(key, url, status, body, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET status=excluded.status, body=excluded.body,"
                " fetched_at=excluded.fetched_at, expires_at=excluded.expires_at",
                (key, url, status, body, now_iso(), iso_in(ttl_s)),
            )


class FixtureTransport(httpx.BaseTransport):
    """Replays recorded responses from ``<dir>/<sha256>.json``; unknown requests return 599."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        base = str(request.url.copy_with(query=None))
        key, _ = request_key(base, params)
        f = self.directory / f"{key}.json"
        if not f.exists():
            return httpx.Response(599, text=f"no fixture for {base} {params}")
        data = json.loads(f.read_text(encoding="utf-8"))
        return httpx.Response(data["status"], text=data["body"])


class HttpClient:
    def __init__(self, *, enabled: bool = True, timeout_s: float = 20.0, max_retries: int = 3,
                 user_agent: str = "ScholarForge", cache: ApiCache | None = None,
                 transport: httpx.BaseTransport | None = None, cache_ttl_days: int = 30):
        self.enabled = enabled
        self.max_retries = max_retries
        self.cache = cache if cache is not None else ApiCache()
        self.cache_ttl_s = cache_ttl_days * 86400
        self.record_dir = Path(os.environ["SCHOLARFORGE_HTTP_RECORD"]) if os.environ.get("SCHOLARFORGE_HTTP_RECORD") else None
        self._client = httpx.Client(timeout=timeout_s, headers={"User-Agent": user_agent}, transport=transport,
                                    follow_redirects=True)
        self.calls = 0

    @classmethod
    def from_settings(cls, settings, transport: httpx.BaseTransport | None = None) -> "HttpClient":
        return cls(enabled=settings.network.enabled, timeout_s=settings.network.timeout_s,
                   max_retries=settings.network.max_retries, user_agent=settings.network.user_agent(),
                   transport=transport, cache_ttl_days=settings.sources.cache_ttl_days)

    def close(self) -> None:
        self._client.close()

    def _throttle(self, service: str) -> None:
        interval = MIN_INTERVAL_S.get(service, 0.1)
        with _lock:
            last = _last_call.get(service, 0.0)
            wait = last + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call[service] = time.monotonic()

    def get(self, url: str, params: dict[str, Any] | None = None, *, service: str = "generic",
            cache_ok: bool = True) -> HttpResponse:
        key, full = request_key(url, params)
        if cache_ok:
            hit = self.cache.get(key)
            if hit:
                return hit
        if not self.enabled:
            stale = self.cache.get(key, allow_expired=True)
            if stale:
                return stale
            raise NetworkUnavailable(f"offline and no cached response for {full}",
                                     hint="enable the network (network.enabled) or retry later")
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(service)
            try:
                self.calls += 1
                resp = self._client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                time.sleep(min(30.0, 1.5 * (2**attempt)))
                continue
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * (2**attempt)
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                if attempt < self.max_retries:
                    time.sleep(min(60.0, delay))
                    continue
                break
            text = resp.text
            if resp.status_code == 200:
                self.cache.put(key, full, 200, text, self.cache_ttl_s)
            elif resp.status_code in (404, 410):
                self.cache.put(key, full, resp.status_code, text[:2000], min(self.cache_ttl_s, 7 * 86400))
            self._record(key, full, resp.status_code, text)
            return HttpResponse(status=resp.status_code, text=text, url=full)
        stale = self.cache.get(key, allow_expired=True)
        if stale:
            log.info("serving stale cache for %s after failure", full)
            return stale
        raise NetworkUnavailable(f"request failed for {full}: {last_exc}", hint="check connectivity; will retry later")

    def _record(self, key: str, url: str, status: int, body: str) -> None:
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        (self.record_dir / f"{key}.json").write_text(json.dumps({"url": url, "status": status, "body": body},
                                                                ensure_ascii=False), encoding="utf-8")
