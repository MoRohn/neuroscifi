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
import logging

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

# Settings whose stored value is checked when it is read (WebConfig.from_json): the five Wave 3 ones since the Wave 3
# fix lane (security review L8), and every other one since Wave 4 lane D (CHECKED_ON_LOAD is filled in below the class).
CHECKED_ON_LOAD: tuple = ()


# review3b N8: a damaged value of a setting that tunes an opt-in rule never loosens that rule: the rule itself reads as
# off (its safe value) until the owner sets the setting again (a damaged supersession_min_sources read as 2 while the
# owner had set 5 and supersession stayed "independent"). {damaged setting: (the rule, its safe value)}
# Wave 4 lane D: the same for what bounds or screens what the Web Cortex sends: a damaged deny list, list of private
# terms, per-host interval, budget ceiling or document size turns web access off until it is set again (its default
# could be looser than what the owner had set, and a lost private term could leave in a query).
FAILS_CLOSED = {"supersession_min_sources": ("supersession", "ask"),
                **{name: ("enabled", False) for name in ("deny_domains", "private_terms", "min_domain_interval",
                                                         "budget_ceiling", "max_document_bytes")}}
# Wave 4 lane D: what a damaged value reads as where the default is not the most cautious value (everything else reads
# as its default, which is the cautious direction: off, loopback, not configured). Allowances and caps of work the owner
# did not ask for read as 0 (none), rendering as off, read-ahead as one page at a time, the bars for admitting a fact
# without the owner as their highest (nothing becomes provisional by itself), strategy selection as off, the
# reflection interval as its longest.
CAUTIOUS = {"news_rendering": False, "daily_renders": 0, "daily_provider_queries": {k: 0 for k in
                                                                                    DEFAULT_PROVIDER_DAILY_QUERIES},
            "daily_autonomous": {"tasks": 0, "requests": 0, "bytes": 0},
            "daily_mcp": {"tasks": 0, "requests": 0, "bytes": 0}, "mcp_research_per_hour": 0, "read_ahead": 1,
            "recheck_per_tick": 0, "recheck_daily": 0, "auto_provisional_min_sources": 50,
            "auto_provisional_min_confidence": 1.0, "strategy_selection": "off", "reflection_interval_days": 365.0}
_REPORTED: set = set()


def damaged(name, value) -> bool:
    """Whether a stored value of a CHECKED_ON_LOAD setting is one set_config would refuse."""
    from .state import _validate         # lazy: state imports this module
    from ..instance.errors import ValidationError
    try:
        _validate(name, value)
    except ValidationError:
        return True
    return False


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
    # per host name, at most budget.max_concurrency; what the task reads, charges and reports is unchanged (about
    # half the waiting at 4). Every fetch, ahead of its turn or not, asks before each lookup, each connection and
    # each request whether the task was cancelled, web access turned off or the host denied, so no request is sent
    # after one of these, and requests on the wire are cut. The fetches ahead of a task share its request and byte
    # budget, never open a second connection to a site an exchange is in progress with, and are not made at all
    # for tasks under a daily allowance (curiosity, refreshes, MCP); the allowances count unused read-ahead traffic.
    # What a task's fetches ahead send and the task then does not use has its own allowance (types.Budget.
    # unused_allowance: a tenth of the budget, at least two requests per page of the window): everything a task
    # sends is at most max_requests plus that, and with it spent the task reads one page at a time.
    # The one exception to "unchanged": a 429 answered to a page fetched ahead backs its site off at once, so a
    # source an earlier candidate would have redirected to there is not read (the report says so).
    # 1 reads one page at a time for every task, as 0.7.0 did: `nsf web config --set read_ahead=1`.
    read_ahead: int = 4
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
    # An open web question older than this many days is stale: it stays open and answerable and no longer counts
    # toward max_open_web_questions, so questions the owner never answers do not stop new ones for ever. 0: every
    # open question counts (the rule before this setting). knowledge.web_question_load.
    web_question_stale_days: float = 30.0
    # MCP (`nsf web mcp`): web_research calls per hour per server process, and a daily cap for origin 'mcp' tasks
    # (tasks, requests and bytes of the last 24 h; request/byte budgets are clamped to what is left at submit).
    mcp_research_per_hour: int = 6
    daily_mcp: dict = field(default_factory=lambda: {"tasks": 24, "requests": 400, "bytes": 80_000_000})
    # ADR-046: a forecast (a knowledge item's confidence, a hypothesis' posterior, the owner's estimate) is read
    # through what the settled forecasts of its kind show (evolution.recalibrations) to rank the autonomous
    # investigations its stated value admitted (never to admit or refuse one). The identity until 20 forecasts of
    # the kind have settled; stored forecasts, verification states, verdicts and answers never change.
    # Off: `nsf web config --set calibrated_forecasts=false`.
    calibrated_forecasts: bool = True
    # ADR-050: the timer's curiosity admits investigations by the one rule the idle investigator uses
    # (autonomy.admission: recorded enable, min_gain, lineage, cooldown). "legacy" restores the tick's rule before
    # it for one release: `nsf web config --set tick_admission='"legacy"'`.
    tick_admission: str = "unified"
    # ADR-051 (lane I). curiosity_learning_progress: curiosity.rank (and derived hypotheses in autonomy.candidates)
    # down-weight a subject whose recent looks realised nothing like the gain expected of them (curiosity.progress;
    # the stronger of it and the futility down-weight applies, it fades with a 7-day half-life and has a floor). The
    # reduced gain is what admission compares with its minimum (curiosity.MIN_GAIN on the timer, min_gain on the idle
    # path), so a down-weighted subject can fall below it and not be researched; caps and budgets are unchanged.
    # Off by default (review1 CM1 re-measure): on the held-out worlds' 12 further seeds it lowered the timer path's
    # realised bits per budget (0.139 -> 0.126) while lowering its noise share.
    # On: `nsf web config --set curiosity_learning_progress=true`.
    # salience_record: each autonomy candidate carries one explainable salience score with its signals, logged with
    # the decision (salience.py); display and record only, it never changes what is admitted or its order.
    curiosity_learning_progress: bool = False
    salience_record: bool = True
    # Recursive knowledge evolution (lane K, ADR-055). None of these changes a belief, a verification state or an
    # answer: a knowledge item changes only through the evidence path (consolidate.run).
    # A new hypothesis records its assumptions (template statements from its own records), the claims against it,
    # the expected information gain and cost of one investigation, and every status change with its evidence.
    # Display-only records; off: `nsf web config --set hypothesis_fields=false`.
    hypothesis_fields: bool = True
    # A derived hypothesis whose prediction found no evidence at all by its deadline is marked 'abandoned' (with the
    # evidence: the expired predictions). Off by default: no benchmark measures it.
    hypothesis_abandon: bool = False
    # Record what a new knowledge item or hypothesis rests on (premises.py) and mark its dependents "to re-check"
    # when a premise is superseded, contradicted or forgotten, or a prediction it rested on fails. Display-only
    # records (a mark queues a re-verification, it changes nothing); off: `--set premise_recheck=false`.
    premise_recheck: bool = True
    # The timer's re-check of the knowledge most at risk of being stale (recheck.py; importance x staleness x
    # dependents), before the due-order refreshes, admitted by autonomy.admission, at most recheck_per_tick per pass
    # and recheck_daily per rolling 24 h inside the daily autonomous allowance; held by quiet. Off by default (ADR-055
    # has the measured numbers and why).
    idle_recheck: bool = False
    recheck_per_tick: int = 1
    recheck_daily: int = 4
    # ADR-060 (Wave 3 lane R): the order of that re-check's pool. "staleness" (the default) is the value above;
    # otherwise a "+"-joined set of "rate" (the chance the item changed since it was verified, from its subject's own
    # verified changes, with a prior), "seen" (certain for an item a newer version was seen for, or marked) and
    # "rotation" (an item not looked at for one TTL goes first; alone, a blind rotation). It only orders what the
    # same rule admits, within the same shares and allowance. Default unchanged: no held-out gain (ADR-060).
    recheck_order: str = "staleness"
    # The bounded reflection routine (reflection.py) on the timer, every reflection_interval_days; `nsf knowledge
    # reflect` runs it when the owner asks. Off by default. reflection_model_summary lets a local model phrase the
    # report (display only, stored as an 'inference' in the imagined store); off by default.
    reflection: bool = False
    reflection_interval_days: float = 7.0
    reflection_model_summary: bool = False
    # Strategy selection (strategies.py, Thompson sampling over research modes from external outcomes): "gate"
    # follows the improvement ledger (off until a promotion passed the gate, and the owner can revert it); "off"
    # never ranks by it.
    strategy_selection: str = "gate"
    # ADR-054 (monitoring.py), each its own switch. answer_monitoring: every research result carries
    # report["monitoring"] (the answer confidence, none with "not enough development data" while no fitted map has
    # enough development items; the unfitted claim probability; evidence sufficiency, source dependence, the depth
    # record); a record only, it changes no verification state, answer or knowledge. selective_answering: an answer
    # judged weak (by evidence sufficiency while no fitted map applies) opens with a plain "not enough evidence"
    # line (the quotes stay). verification_escalation: before that, read one more independent source for the
    # weakest statement when the task's own budget still allows it. adaptive_depth: stop reading once the last
    # pages added no statement with two independent sources and no contradiction. Only the record is on by default
    # (display only, nothing else changes); the other three showed no held-out gain (ADR-054).
    # Off: `nsf web config --set answer_monitoring=false`.
    answer_monitoring: bool = True
    selective_answering: bool = False
    verification_escalation: bool = False
    adaptive_depth: bool = False
    # ADR-053: paired Pocket devices also see the owner's own latest research questions by reference ("what we were
    # just doing": the question as asked, its status and when; the report stays family-scoped and cannot be opened
    # from a phone). Off by default: a pairing family may include devices that are not the owner's.
    pocket_recent: bool = False
    # ADR-056 (Wave 2 lane L), the integration loop. salience_attention: among the candidates autonomy.admission
    # already admitted on the idle and timer paths, the one with the higher salience record (salience.py) is taken
    # first (the owner's own records stay first); it never admits or refuses one, and min_gain, lineage, cooldown,
    # budgets and quiet decide exactly as before. Needs salience_record. Off by default (ADR-056 has the numbers).
    # arbitration_record: when two background mechanisms want one subject in one timer pass, the tick's existing
    # order decides (re-check, then due refreshes, then curiosity; one task per subject) and records a decision event
    # on the winner's task naming the mechanism that lost and the rule that decided. A record only.
    salience_attention: bool = False
    arbitration_record: bool = True
    # ADR-061 (Wave 3 lane S), what counts as an independent source, and what may replace an out-of-date item.
    # attribution_independence: a sentence that names the outlet it repeats ("According to X, ...", "..., X says.",
    # "... (X).", "... via X.") counts with that outlet as one source (claims.attribution_keys). echo_recognition: a
    # page repeating a value, or the very wording, that only the individual's own outputs had stated (its model-written
    # answers stored with its tasks; its imagined store is never read here) before any source it read did, or that
    # credits what it states to an assistant, a chatbot or an AI model, is not evidence for that value; a disagreement
    # it raises is still asked (consolidate._without_echoes).
    # Both are OFF by default (ADR-061, Wave 3 fix lane): the lane's own pre-registered rule returned "unsafe" for
    # them, attribution merges honest outlets that credit each other (a correctness cost beyond the spread on staged
    # worlds), and echo recognition rests on a phrase list chosen after held-out runs. The owner may turn either on.
    attribution_independence: bool = False
    echo_recognition: bool = False
    # supersession: "ask" (the standing rule of ADR-026/038: a refresh that contradicts a held item asks the owner and
    # never revises it) or "independent": a newer value (by its stated time) stated by at least
    # supersession_min_sources independent sources, never contradicted by an owner's answer and never the
    # individual's own text, replaces the held item without asking; the replacement is listed for review and one
    # command reverts it (`nsf web replacements`, `nsf web revert ID`). Off ("ask") by default:
    # the owner has not decided to change the standing rule (ADR-061 has the gains and the false replacements).
    supersession: str = "ask"
    supersession_min_sources: int = 2
    # ADR-063 (Wave 3, third fix lane): how a page that NAMES its origin (an attribution line, a wire credit, a
    # canonical URL on another host) counts. "transitive" (the default): it joins every group that origin names or
    # owns, exactly as before ADR-063 (the grouping of 3ce4ae3), so copies naming several origins stay one source, but a
    # page naming two honest outlets as its origins joins them into one source (review3b N1: their fact is then not
    # verified; open at this default). "one_way": each such page attaches to ONE origin (claims._one_way), which closes
    # N1 and keeps copies naming several origins one source on the staged cases (review3c F1), but no benchmark world
    # holds a page naming two origins, so neither direction is measured. Applies to claims read while it is set.
    origin_grouping: str = "transitive"
    # ADR-067 (Wave 4 lane E), the individual's own text by what it emitted. own_text_provenance: when a model-written
    # answer is stored (shown in the console, on Pocket, spoken, returned over MCP), the subject names and normalised
    # values it states are recorded as salted fingerprints with the time and whether a source the individual had read
    # stated that value then (webcortex/emitted.py; never the text); a page read later, published after that answer,
    # stating for that subject a value the individual emitted with no source is not evidence for it (a disagreement it
    # raises is still asked). OFF by default: with it off nothing is recorded and consolidation is as before; turning
    # it on is the owner's decision (ADR-067 has the measurements, both directions).
    own_text_provenance: bool = False
    # ADR-066 (Wave 4 lane S): what counts as one source by evidence of derivation, not by the wording of a credit. On,
    # a page that names another page read in the same task (capitalised words equal to that page's site label,
    # whatever surrounds them) or links to it counts with that page (one edge per group, never between two groups that
    # each hold a page naming no other page, only between groups stating the same values, never to a page published
    # later), on top of today's grouping, which it never splits; a page lane E's echo check marks counts for nothing.
    # OFF by default: an outlet that checked a value and still names another outlet looks exactly like a copy, so the
    # rule trades true facts not verified for copies not counted twice (ADR-066 has both, per shape). Applies to claims
    # read while it is set.
    source_derivation: bool = False

    @classmethod
    def from_json(cls, text: str | None) -> "WebConfig":
        try:
            raw = json.loads(text) if text else {}
        except ValueError:
            raw = None
        if not isinstance(raw, dict):       # Wave 4 lane D: an unreadable row is every setting damaged
            raw = {name: _UNREADABLE for name in CHECKED_ON_LOAD}
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        # Wave 3 fix lane (security review L8), every setting since Wave 4 lane D: a damaged stored value is never used
        # as it stands (a non-string recheck_order made every timer pass raise; a non-int supersession_min_sources made
        # consolidation raise with the rule on): its cautious value applies (CAUTIOUS, else the default; FAILS_CLOSED),
        # state.setting_problems() names it, and a warning is logged once with the setting's name only.
        closed = {}
        for name in sorted(set(values) & set(CHECKED_ON_LOAD)):
            if values[name] is _UNREADABLE or damaged(name, values[name]):
                if name not in _REPORTED:           # review3b N8: once per process and setting, not on every read
                    _REPORTED.add(name)
                    logging.getLogger(__name__).warning("the saved Web Cortex setting %s is not a valid value; "
                                                        "its cautious value is used", name)
                del values[name]
                if name in CAUTIOUS:
                    values[name] = CAUTIOUS[name]
                if name in FAILS_CLOSED:
                    closed[FAILS_CLOSED[name][0]] = FAILS_CLOSED[name][1]
        return cls(**(values | closed))

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


CHECKED_ON_LOAD = tuple(f.name for f in fields(WebConfig))       # every setting (Wave 4 lane D)
_UNREADABLE = object()          # what each setting of a row that is not a JSON object reads as before its check
