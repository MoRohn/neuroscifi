"""Shared contracts for every Web Cortex stage (binding: see docs/web-cortex/ARCHITECTURE.md §4).

Plain dataclasses so every stage can be tested alone. Times are Unix seconds (float) passed in by the caller;
nothing below the CLI/HTTP layer reads the clock except through an injected `clock`. Everything a stage returns
to the instance layer must be JSON-serialisable through `to_json()`.

Trust rule: every field whose value came from the network (titles, text, snippets, claim text, metadata) is
UNTRUSTED DATA. It may be stored, ranked, quoted and shown, but it is never executed, never used as a tool
argument without validation, and never treated as an instruction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
import math
import threading
from typing import Any

# ---- vocabularies ------------------------------------------------------------------------------------------

MODES = ("quick_lookup", "deep_research", "continuous", "comparative", "scientific", "technical",
         "change_detection", "fact_verification", "gap_resolution")
QUERY_KINDS = ("web", "news", "academic", "technical", "encyclopedia")
RESULT_KINDS = ("web", "academic", "package", "advisory", "release", "feed", "encyclopedia")
RETRIEVAL_MODES = ("live", "cache", "api", "feed", "rendered")   # 'cache' = served from the public cache, not live
DOC_KINDS = ("page", "pdf", "api", "feed", "text", "json")
FRESHNESS_CLASSES = ("breaking", "volatile", "release", "scientific", "stable")
CLAIM_KINDS = ("finding", "interpretation", "statement", "metadata")
TASK_STATES = ("queued", "discovering", "reading", "evaluating", "synthesizing", "completed", "partial",
               "failed", "cancelled")
TERMINAL_STATES = ("completed", "partial", "failed", "cancelled")
# UI stage names (Discovering -> Reading -> Evaluating -> Synthesizing -> Completed)
STAGES = ("discovering", "reading", "evaluating", "synthesizing")
FETCH_ERROR_CODES = ("blocked_policy", "blocked_robots", "dns_failure", "connect_failed", "timeout", "http_error",
                     "too_large", "unsupported_type", "rate_limited", "too_many_redirects", "tls_error",
                     "disabled", "budget_exhausted", "decode_error")


class JsonMixin:
    def to_json(self) -> dict:
        def clean(value):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            if isinstance(value, bytes):
                return None   # bodies never travel as JSON
            if hasattr(value, "to_json"):
                return value.to_json()
            if isinstance(value, dict):
                return {str(k): clean(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            return value
        return {f.name: clean(getattr(self, f.name)) for f in fields(self) if f.name not in getattr(self, "_SKIP", ())}


# ---- discovery ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Query(JsonMixin):
    """One search request sent to a provider. `text` has passed safety.privacy_screen before it leaves."""
    text: str
    kind: str = "web"                 # one of QUERY_KINDS; routes to providers that serve it
    since: float | None = None        # only results published at or after this time (best effort per provider)
    site: str | None = None           # restrict to one registrable domain (best effort)
    language: str | None = None       # BCP-47 tag, e.g. "en"
    purpose: str = ""                 # why this query exists (sub-question, expansion, refinement) for the episode log


@dataclass(frozen=True)
class SearchResult(JsonMixin):
    url: str
    title: str
    snippet: str
    provider: str                     # e.g. "openwebsearch", "arxiv", "osv"
    engine: str | None = None         # e.g. "duckduckgo" inside openwebsearch
    rank: int = 0                     # 1-based rank within the provider response
    kind: str = "web"                 # one of RESULT_KINDS
    published_at: float | None = None
    metadata: dict = field(default_factory=dict)   # structured fields: doi, authors, abstract, version, ...
    score: float = 0.0                # fused score assigned by the registry (higher is better)


@dataclass(frozen=True)
class QueryPlan(JsonMixin):
    queries: tuple[Query, ...]
    subquestions: tuple[str, ...] = ()
    max_sources: int = 6
    notes: tuple[str, ...] = ()


@dataclass
class SearchOutcome(JsonMixin):
    results: list[SearchResult]
    attempts: list[dict]              # [{provider, engine, query, ok, n, error, latency}]
    failures: list[dict]              # attempts that failed or returned nothing usable


# ---- acquisition -------------------------------------------------------------------------------------------

class FetchError(Exception):
    """Acquisition failure with a stable code (FETCH_ERROR_CODES). Never hide one: record it on the task."""

    def __init__(self, code: str, url: str, detail: str = "", *, retry_after: float | None = None,
                 status: int | None = None):
        assert code in FETCH_ERROR_CODES, code
        super().__init__(f"{code}: {url}: {detail}")
        self.code, self.url, self.detail, self.retry_after, self.status = code, url, detail, retry_after, status

    def to_json(self) -> dict:
        return {"code": self.code, "url": self.url, "detail": self.detail, "retry_after": self.retry_after,
                "status": self.status}


@dataclass
class FetchResult(JsonMixin):
    url: str                          # as requested
    final_url: str                    # after validated redirects
    status: int
    headers: dict                     # lower-case names
    content_type: str                 # media type without parameters, e.g. "text/html"
    body: bytes
    retrieved_at: float
    retrieval_mode: str = "live"      # live | cache (304 or fresh cache hit) | rendered
    redirects: list = field(default_factory=list)
    truncated: bool = False
    elapsed: float = 0.0
    remote_ip: str | None = None      # the validated address actually connected to
    _SKIP = ("body",)


@dataclass
class Document(JsonMixin):
    """Extracted, untrusted content of one source."""
    url: str
    final_url: str
    canonical_url: str
    domain: str                       # registrable domain, e.g. "example.co.uk"
    title: str
    text: str                         # main text, hidden/injected content removed (see flags)
    retrieved_at: float
    retrieval_mode: str               # one of RETRIEVAL_MODES
    kind: str = "page"                # one of DOC_KINDS
    content_type: str = "text/html"
    content_sha256: str = ""          # key into the public cache
    markdown: str = ""
    published_at: float | None = None
    modified_at: float | None = None
    authors: list = field(default_factory=list)
    language: str | None = None
    links: list = field(default_factory=list)       # absolute URLs found in the main content (bounded)
    flags: dict = field(default_factory=dict)       # injection: [..], hidden_removed: int, truncated, js_suspected
    metadata: dict = field(default_factory=dict)    # doi, version, venue, abstract, license, ...


# ---- evidence ----------------------------------------------------------------------------------------------

@dataclass
class Claim(JsonMixin):
    id: str                           # sha256(source canonical url + "\n" + normalised text)[:24]
    text: str                         # one sentence (or a structured statement for API sources), untrusted
    source_url: str
    domain: str
    title: str
    retrieved_at: float
    key: str = ""                     # normalised subject signature used for clustering
    kind: str = "statement"           # one of CLAIM_KINDS
    polarity: int = 1                 # +1 asserts, -1 negates
    values: list = field(default_factory=list)      # extracted numbers / versions / dates as strings
    published_at: float | None = None
    event_at: float | None = None
    relevance: float = 0.0
    doc_sha256: str = ""
    sentence_index: int = 0
    flags: dict = field(default_factory=dict)


@dataclass
class ClaimCluster(JsonMixin):
    key: str
    claims: list                      # list[Claim]
    independent_sources: int          # distinct groups after domain and near-duplicate grouping
    supporting: list                  # claim ids
    contradicting: list               # [{"a": id, "b": id, "reason": str}]
    confidence: float                 # posterior mean that the cluster's statement holds
    uncertainty: float                # posterior standard deviation
    status: str = "single_source"     # single_source | corroborated | contested | insufficient
    statement: str = ""               # representative claim text


# ---- budgets -----------------------------------------------------------------------------------------------

_CHARGE_LOCK = threading.Lock()   # one lock for every Usage: fetches and searches charge from many threads


class BudgetExceeded(Exception):
    def __init__(self, resource: str, used, limit):
        super().__init__(f"budget exhausted: {resource} {used} of {limit}")
        self.resource, self.used, self.limit = resource, used, limit


@dataclass
class Budget(JsonMixin):
    max_queries: int = 6
    max_requests: int = 40            # every network request incl. API calls and robots.txt
    max_pages: int = 8                # documents acquired (fetched or rendered)
    max_bytes: int = 20_000_000
    max_seconds: float = 120.0
    max_depth: int = 1                # link-following depth for bounded crawls (0 = no following)
    max_browser_sessions: int = 0     # renders per task; 0 disables JavaScript rendering for the task
    max_render_requests: int = 100    # sub-requests ONE render may make (its own allowance, not max_requests)
    max_concurrency: int = 4
    max_context_tokens: int = 2000    # working-memory pack size (approx. 1 token ~ 0.75 words)

    @classmethod
    def from_json(cls, raw: dict | None, *, base: "Budget | None" = None) -> "Budget":
        base = base or cls()
        if not raw:
            return replace(base)
        known = {f.name for f in fields(cls)}
        return replace(base, **{k: v for k, v in raw.items() if k in known})

    def clamp(self, ceiling: "Budget") -> "Budget":
        """Never exceed the owner's configured ceiling."""
        return Budget(**{f.name: min(getattr(self, f.name), getattr(ceiling, f.name)) for f in fields(self)})


@dataclass
class Usage(JsonMixin):
    queries: int = 0
    requests: int = 0
    pages: int = 0
    bytes: int = 0
    seconds: float = 0.0
    browser_sessions: int = 0
    tokens: int = 0
    render_requests: int = 0          # sub-requests of all renders (each within budget.max_render_requests)
    # Requests sent and bytes received for pages fetched ahead of their turn (config.read_ahead) that the task then
    # did not use: real traffic to the sites, counted apart from requests/bytes (what the task used and was charged).
    prefetch_unused_requests: int = 0
    prefetch_unused_bytes: int = 0

    def charge_unused(self, *, requests=0, bytes=0):
        """Count what an unused read-ahead fetch sent and received (net.SafeFetcher.speculate). Never checked
        against the budget: the traffic already happened, and the task never used it."""
        with _CHARGE_LOCK:
            self.prefetch_unused_requests += requests
            self.prefetch_unused_bytes += bytes

    def to_json(self) -> dict:
        """As JsonMixin, without the unused-prefetch counters while they are 0, so a task that fetched nothing ahead
        (read_ahead 1) records exactly the usage it always did."""
        out = super().to_json()
        for name in ("prefetch_unused_requests", "prefetch_unused_bytes"):
            if not out.get(name):
                out.pop(name, None)
        return out

    def charge(self, budget: Budget, *, queries=0, requests=0, pages=0, bytes=0, browser_sessions=0):
        """Check then add, atomically. Raises BudgetExceeded before the limit would be passed (pre-flight)."""
        with _CHARGE_LOCK:
            self._charge(budget, queries, requests, pages, bytes, browser_sessions)

    def _charge(self, budget, queries, requests, pages, bytes, browser_sessions):
        deltas = {"queries": queries, "requests": requests, "pages": pages, "bytes": bytes,
                  "browser_sessions": browser_sessions}
        for name, delta in deltas.items():
            limit = getattr(budget, "max_" + name)
            if delta > 0 and getattr(self, name) + delta > limit:
                raise BudgetExceeded(name, getattr(self, name) + delta, limit)
        self.queries += queries
        self.requests += requests
        self.pages += pages
        self.bytes += bytes
        self.browser_sessions += browser_sessions

    @classmethod
    def from_json(cls, raw: dict | None) -> "Usage":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (raw or {}).items() if k in known})


def as_dict(value: Any):
    """JSON form of a contract object, list of them, or a plain value."""
    if hasattr(value, "to_json"):
        return value.to_json()
    if isinstance(value, (list, tuple)):
        return [as_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: as_dict(v) for k, v in value.items()}
    return value


__all__ = [n for n in dir() if not n.startswith("_")] + ["asdict"]
