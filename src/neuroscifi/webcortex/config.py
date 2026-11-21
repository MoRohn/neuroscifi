"""Per-individual Web Cortex configuration, stored as JSON in the instance `meta` table (key 'webcortex').

Defaults are the reversible, conservative direction: network access OFF, autonomous research OFF, Pocket
research OFF, JavaScript rendering OFF. The owner turns each on explicitly (`nsf web config ...`).
Respecting robots.txt, the private-address block and the read-only default are constants, not settings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json

from .types import Budget, MODES

META_KEY = "webcortex"
USER_AGENT = "NeuroSciFi-WebCortex/0.6 (research agent; honours robots.txt)"
RESPECT_ROBOTS = True            # constant by design (SECURITY_MODEL §5); there is no switch
DEFAULT_ENGINES = ("duckduckgo", "brave", "startpage", "bing")

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
    openwebsearch_url: str = "http://127.0.0.1:3210"   # loopback daemon (trusted local integration)
    engines: list = field(default_factory=lambda: list(DEFAULT_ENGINES))
    providers: list = field(default_factory=lambda: [
        "openwebsearch", "wikipedia", "arxiv", "openalex", "crossref", "pubmed", "pypi", "npm", "osv",
        "github"])
    budget_ceiling: dict = field(default_factory=lambda: asdict(Budget(
        max_queries=16, max_requests=200, max_pages=30, max_bytes=60_000_000, max_seconds=600, max_depth=2,
        max_browser_sessions=2, max_concurrency=6, max_context_tokens=6000)))
    daily_autonomous: dict = field(default_factory=lambda: {"tasks": 12, "requests": 400, "bytes": 80_000_000})
    min_domain_interval: float = 1.0         # seconds between requests to one host
    max_document_bytes: int = 5_000_000
    cache_max_bytes: int = 512 * 1024 * 1024
    cache_max_age_days: float = 30.0
    deny_domains: list = field(default_factory=list)     # never contacted
    private_terms: list = field(default_factory=list)    # extra strings that must never leave in a query
    auto_provisional_min_sources: int = 2    # independent sources before a claim may become provisional
    auto_provisional_min_confidence: float = 0.75
    freshness_ttl: dict = field(default_factory=dict)    # overrides of temporal.FRESHNESS ttl seconds

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
        if not self.browser_rendering:
            budget.max_browser_sessions = 0
        elif not (requested and "max_browser_sessions" in requested):
            budget.max_browser_sessions = max(budget.max_browser_sessions, 1)   # rendering only as an escalation
        return budget.clamp(self.ceiling())
