"""Per-individual Web Cortex configuration, stored as JSON in the instance `meta` table (key 'webcortex').

Defaults are the reversible, conservative direction: network access OFF, autonomous research OFF, Pocket
research OFF, general JavaScript rendering OFF, browser User-Agents for the search daemon OFF (honest User-Agent only).
The owner turns each on explicitly (`nsf web config ...`). One bounded exception (ADR-041): `news_rendering` is ON, so
a news / time-sensitive question a person asked may render a scripted news page whose static HTML gave no text or no
date, at most `max_browser_sessions` renders per task (1 by default), each within `max_render_requests` sub-requests
(100) and `daily_renders` per UTC day (40); it does nothing while web access is off, and
`nsf web config --no-news-render` turns it off.
Respecting robots.txt, the private-address block and the read-only default are constants, not settings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json

from .. import __version__
from .types import Budget, MODES

META_KEY = "webcortex"
# The release's major.minor; the product token "NeuroSciFi-WebCortex" is what robots.txt rules name and never changes.
# deploy/open-websearch/apply-honest-ua.mjs carries the same literal (a test ties the two together).
USER_AGENT = f"NeuroSciFi-WebCortex/{'.'.join(__version__.split('.')[:2])} (research agent; honours robots.txt)"
RESPECT_ROBOTS = True            # constant by design (SECURITY_MODEL §5); there is no switch
DEFAULT_ENGINES = ("duckduckgo", "brave", "startpage", "bing")
# Keyed search providers (ADR-034 amendment 1): queries per UTC day per API key, so a paid key is never drained.
# Brave: 30 x 31 days = 930, inside the 1,000 requests the Search plan's $5 monthly credit buys (brave.com/search/api).
DEFAULT_PROVIDER_DAILY_QUERIES = {"brave": 30}

# Per-mode default budgets. A task may ask for less; never more than WebConfig.budget_ceiling.
MODE_BUDGETS = {
    "quick_lookup":      Budget(max_queries=3, max_requests=20, max_pages=3, max_seconds=45, max_depth=0),
    "fact_verification": Budget(max_queries=6, max_requests=40, max_pages=8, max_seconds=120, max_depth=0),
    "deep_research":     Budget(max_queries=10, max_requests=90, max_pages=15, max_seconds=300, max_depth=1),
    "comparative":       Budget(max_queries=10, max_requests=80, max_pages=12, max_seconds=240, max_depth=0),
    "scientific":        Budget(max_queries=8, max_requests=80, max_pages=12, max_seconds=300, max_depth=0),
    "technical":         Budget(max_queries=12, max_requests=120, max_pages=10, max_seconds=240, max_depth=0),
    "change_detection":  Budget(max_queries=2, max_requests=30, max_pages=10, max_seconds=120, max_depth=0),
    "continuous":        Budget(max_queries=4, max_requests=40, max_pages=6, max_seconds=120, max_depth=0),
    "gap_resolution":    Budget(max_queries=4, max_requests=30, max_pages=5, max_seconds=90, max_depth=0),
}
assert set(MODE_BUDGETS) == set(MODES)


@dataclass
class WebConfig:
    enabled: bool = False                    # master switch for every network path
    autonomous: bool = False                 # scheduler may start curiosity/refresh research on its own
    pocket_research: bool = False            # paired Pocket devices may submit and read research tasks
    browser_rendering: bool = False          # permit sandboxed JavaScript rendering (needs Playwright + Chromium)
    # ADR-041: news / time-sensitive questions asked by a person may render a scripted news page when its static
    # HTML gave no text or no date (owner switch; on by default), at most daily_renders renders per UTC day in all.
    news_rendering: bool = True
    # Before memory answers a question without the web, the answer model confirms that the chosen statements state
    # the answer to exactly what is asked (memory_gate.py). It can only send a question to the web. On by default;
    # without a configured model it does nothing.
    memory_check: bool = True
    daily_renders: int = 40
    # Owner opt-in (ADR-032), off by default: let the upstream open-webSearch scrapers send their built-in browser
    # User-Agent strings (a form of impersonation) instead of the honest USER_AGENT patched in by `npm run
    # patch-ua`. With it off, `nsf web daemon start` refuses an unpatched build.
    search_browser_user_agent: bool = False
    openwebsearch_url: str = "http://127.0.0.1:3210"   # loopback daemon (trusted local integration)
    # SearXNG (ADR-034): the primary general discovery provider when set (a loopback http URL, `nsf web searxng
    # start` sets it); empty = not configured, and discovery behaves exactly as before. open-webSearch then runs
    # only as its fallback. searxng_engines optionally pins the engines per category ({"news": ["bing news"]}).
    searxng_url: str = ""
    searxng_engines: dict = field(default_factory=dict)
    # Keyed providers (ADR-034 amendment 1): Brave is the primary general provider while its key file exists
    # (`nsf web provider set-key brave`); this caps the queries sent with each key per UTC day, every origin
    # (autonomy included) counted. 0 switches the provider off without removing the key.
    daily_provider_queries: dict = field(default_factory=lambda: dict(DEFAULT_PROVIDER_DAILY_QUERIES))
    engines: list = field(default_factory=lambda: list(DEFAULT_ENGINES))
    providers: list = field(default_factory=lambda: [
        "openwebsearch", "wikipedia", "arxiv", "openalex", "crossref", "pubmed", "pypi", "npm", "osv",
        "github"])
    budget_ceiling: dict = field(default_factory=lambda: asdict(Budget(
        max_queries=16, max_requests=200, max_pages=30, max_bytes=60_000_000, max_seconds=600, max_depth=2,
        max_browser_sessions=2, max_render_requests=150, max_concurrency=6, max_context_tokens=6000)))
    daily_autonomous: dict = field(default_factory=lambda: {"tasks": 12, "requests": 400, "bytes": 80_000_000})
    min_domain_interval: float = 1.0         # seconds between requests to one host
    # Pages a research task may fetch at once while reading: the next candidates are fetched ahead of their turn, one
    # per host, at most budget.max_concurrency; what the task reads, charges and reports is unchanged. Measured with
    # 300 ms per response: task time 2.10 s at 1, 0.99 s at 4. The default is 1 (one at a time, as 0.7.0), because
    # with a larger window up to window - 1 requests already sent can reach their sites after a cancel, after web
    # access is turned off or after a domain is denied, and those unused requests are not in the daily caps. The
    # owner may accept that for the speed: `nsf web config --set read_ahead=4`.
    read_ahead: int = 1
    max_document_bytes: int = 5_000_000
    cache_max_bytes: int = 512 * 1024 * 1024
    cache_max_age_days: float = 30.0
    deny_domains: list = field(default_factory=list)     # never contacted
    private_terms: list = field(default_factory=list)    # extra strings that must never leave in a query
    auto_provisional_min_sources: int = 2    # independent sources before a claim may become provisional
    # The decisive bar is >= 2 agreeing independent sources and no contradiction; this floor lets exactly two dated
    # secondary sources through (posterior 2.6/3.6 = 0.722) but not one dated + one undated (2.4/3.4 = 0.706).
    auto_provisional_min_confidence: float = 0.72
    freshness_ttl: dict = field(default_factory=dict)    # overrides of temporal.FRESHNESS ttl seconds
    # Interruption budget of web questions (scope web:...), separate from an epoch's ask budget: beyond either cap
    # candidates stay provisional and one aggregate question per task points to the console / `nsf web knowledge`.
    max_open_web_questions: int = 10
    max_web_questions_per_task: int = 3
    # MCP (`nsf web mcp`): web_research calls per hour per server process, and a daily cap for origin 'mcp' tasks
    # (tasks, requests and bytes of the last 24 h; request/byte budgets are clamped to what is left at submit).
    mcp_research_per_hour: int = 6
    daily_mcp: dict = field(default_factory=lambda: {"tasks": 24, "requests": 400, "bytes": 80_000_000})

    @classmethod
    def from_json(cls, text: str | None) -> "WebConfig":
        raw = json.loads(text) if text else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> dict:
        return asdict(self)

    def ceiling(self) -> Budget:
        return Budget.from_json(self.budget_ceiling)

    def budget_for(self, mode: str, requested: dict | None = None) -> Budget:
        base = MODE_BUDGETS.get(mode, MODE_BUDGETS["quick_lookup"])
        budget = Budget.from_json(requested, base=base)
        if not (self.browser_rendering or self.news_rendering):
            budget.max_browser_sessions = 0
        elif not (requested and "max_browser_sessions" in requested):
            budget.max_browser_sessions = max(budget.max_browser_sessions, 1)   # rendering only as an escalation
        return budget.clamp(self.ceiling())
