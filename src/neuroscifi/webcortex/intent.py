"""INTENT: decide whether the web is needed, classify the request, and plan the searches (ADR-022: no model).

Everything here is deterministic and rule-based; every decision carries a human-readable reason (`reasons` for the
web decision, `why` for mode / freshness / kinds / window). Cue tables are English: a question in another script is
kept verbatim and tagged with its language — nothing is translated, because there is no model on the host.

    assess(question, now=, known=)  -> assessment dict (ARCHITECTURE §4.2)
    plan(question, assessment, now=) -> QueryPlan   decomposition, expansion, temporal/site/kind routing, ≤ 8 queries
    refine(plan, outcome, assessment, now=) -> QueryPlan | None   one broader round when results are thin
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .config import MODE_BUDGETS
from .types import Query, QueryPlan

DAY = 86400.0
MAX_PLAN_QUERIES = 8
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

# ---- cue tables ------------------------------------------------------------------------------------------------
# (pattern on lower-cased text, window in seconds or None, label override)
_RECENCY = [
    (r"\bbreaking\b", DAY), (r"\b(?:today|tonight|this morning|right now|(?:last|past) 24 hours)\b", DAY),
    (r"\byesterday\b", 2 * DAY),
    (r"\b(?:this|past|last) week\b|\b(?:last|past) (?:7|seven) days\b", 7 * DAY),
    (r"\b(?:this|past|last) month\b|\b(?:last|past) (?:30|thirty) days\b", 31 * DAY),
    (r"\b(?:this|past|last) year\b|\b(?:last|past) 12 months\b", 365 * DAY),
    (r"\brecent(?:ly)?\b", "recent"),
    (r"\b(?:latest|newest|current(?:ly)?|nowadays|now|up[- ]to[- ]date|as of|updates?|updated|still)\b", None),
]
_NEW = re.compile(r"\bnew(?:er|est)?\b")        # case-sensitive: "New York" is not a recency cue
_SCI_STRONG = re.compile(
    r"\b(?:evidence|stud(?:y|ies)|papers?|literature|peer[- ]reviewed|preprints?|arxiv|pubmed|meta[- ]analys[ie]s|"
    r"systematic reviews?|clinical trials?|randomi[sz]ed|cohort|journals?|citations?|experiments?|experimental|"
    r"hypothes[ie]s|empirical|scientific|science)\b")
_SCI_DOMAIN = re.compile(
    r"\b(?:neuroscien\w*|dendrit\w*|neurons?|neuronal|neural circuits?|synap\w*|plasticity|cortex|cortical|"
    r"hippocamp\w*|predictive coding|free[- ]energy principle|genom\w*|proteins?|molecular|quantum|physics|"
    r"biolog\w*|epidemiolog\w*|vaccines?|cognitive science|psycholog\w*|neurobiolog\w*)\b")
_TECH = re.compile(
    r"\b(?:librar(?:y|ies)|packages?|releases?|released|versions?|cve(?:-\d{4}-\d+)?|advisor(?:y|ies)|"
    r"vulnerabilit(?:y|ies)|security patch(?:es)?|dependenc(?:y|ies)|pypi|npm|github|changelogs?|documentation|"
    r"docs|apis?|sdks?|frameworks?|upgrades?|deprecat\w*|semver|pip install|crates?\.io)\b")
_VERIFY = re.compile(
    r"\b(?:verify|verified|verification|is it (?:really )?true|fact[- ]?check\w*|true that|claims?|claimed|"
    r"confirm (?:whether|that|if)|debunk\w*|disagree\w*|contradict\w*|conflicting|"
    r"what can (?:actually )?be established)\b")
_MONITOR = re.compile(
    r"\b(?:monitor\w*|keep (?:track|an eye)|track(?:ing)? (?:changes|updates|developments|releases)|watch(?: for)?|"
    r"notify me|alert me|subscribe|keep me (?:updated|posted|informed))\b")
_CHANGE = re.compile(
    r"\b(?:what(?:'s| has| have)? changed|changes? since|changed since|diff(?:erences?)? since|diff|"
    r"what's new since|since (?:my|our|the) last (?:check|update|run))\b")
_DEEP = re.compile(r"\b(?:investigat\w*|in[- ]depth|deeply|deep[- ]dive|thorough(?:ly)?|comprehensive(?:ly)?|"
                   r"exhaustive(?:ly)?)\b")
_COMPARE_CUE = re.compile(r"\b(?:compar\w*|vs\.?|versus|difference between|better than|which is better)\b")
_ARITH = re.compile(r"^\s*(?:what(?:'s| is)|compute|calculate|evaluate|how much is)?\s*"
                    r"[-+*/×÷^().\d\s=x%]*\d[-+*/×÷^().\d\s=x%]*\s*\??\s*$", re.I)
_DEFINE = re.compile(r"^\s*(?:what is|what's|what are|who is|who was|who were|define|definition of|meaning of|"
                     r"explain)\s+"
                     r"(?:an?\s+|the\s+)?(?P<t>[\w'’ -]{1,60}?)\s*[?.]?\s*$", re.I)
_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)
_DOMAIN = re.compile(r"(?<![@\w.-])(?:site:)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,12})(?![\w-])",
                     re.I)
_FILE_EXT = {"py", "js", "ts", "md", "txt", "json", "yaml", "yml", "toml", "sh", "rs", "csv", "html", "css", "pdf",
             "zip", "gz", "sqlite", "db", "log", "ini", "cfg", "lock", "xml", "png", "jpg"}
SOURCE_SITES = {"arxiv": "arxiv.org", "wikipedia": "wikipedia.org", "github": "github.com",
                "pubmed": "pubmed.ncbi.nlm.nih.gov", "hacker news": "news.ycombinator.com",
                "stack overflow": "stackoverflow.com", "reddit": "reddit.com", "nature": "nature.com",
                "biorxiv": "biorxiv.org", "medrxiv": "medrxiv.org"}
_SOURCE_NAMED = re.compile(r"\b(?:on|from|at|in|according to|via)\s+(" + "|".join(SOURCE_SITES) + r")\b", re.I)

_SCRIPTS = [("zh", r"[一-鿿]"), ("ja", r"[぀-ヿ]"), ("ko", r"[가-힯]"),
            ("ru", r"[Ѐ-ӿ]"), ("ar", r"[؀-ۿ]"), ("he", r"[֐-׿]"),
            ("el", r"[Ͱ-Ͽ]"), ("hi", r"[ऀ-ॿ]"), ("th", r"[฀-๿]")]
_SCRIPT_NAMES = {"zh": "Han (CJK)", "ja": "Japanese kana", "ko": "Hangul", "ru": "Cyrillic", "ar": "Arabic",
                 "he": "Hebrew", "el": "Greek", "hi": "Devanagari", "th": "Thai"}

STOPWORDS = frozenset("""
a an the and or but if then than so of in on at to for from by with without about into onto over under between
among through during before after above below up down out off again further once here there all any both each few
more most other some such no nor not only own same too very can will just should would could may might must shall
do does did doing done be been being is are was were am has have had having i me my mine we us our ours you your
yours he him his she her hers it its they them their theirs what which who whom whose this that these those when
where why how whether also as please tell show give find list explain describe determine check see look get let
make know knows known think want need like s t don't isn't aren't what's it's there's
""".split())
META = frozenset("""
latest newest newer new recent recently current currently now nowadays today tonight yesterday week weeks month
months year years breaking update updates updated developments development news important most meaningful major
significant occur occurs occurring happen happens happened research researching investigate investigation deeply
depth in-depth thorough thoroughly comprehensive monitor monitoring track tracking watch notify alert subscribe keep
verify confirm fact-check fact check claim claims claimed true really actually established establish disagree
disagrees contradict contradicts conflicting sources source two compare comparing comparison versus vs difference
differences better best evidence knowledge related regarding affect affects affecting use uses used using we our
things anything something stuff mine changed changes since diff whats define definition meaning
""".split())
_GENERIC_ACRONYMS = frozenset("I AI API APIS CVE URL HTML JSON HTTP HTTPS PDF SDK CLI GPU CPU OS ML LLM FAQ OK".split())
_HYPHEN_WORDS = frozenset("""open-source peer-reviewed meta-analysis meta-analyses up-to-date state-of-the-art real-time
long-term short-term well-known so-called large-scale small-scale high-level low-level end-to-end built-in third-party
open-ended follow-up cutting-edge in-depth fact-check deep-dive day-to-day one-off two-sided free-energy""".split())
_HYPHEN_PREFIX = ("non-", "self-", "co-", "re-", "pre-", "post-", "multi-", "cross-", "anti-", "semi-", "sub-",
                  "inter-", "over-", "under-", "well-", "high-", "low-", "long-", "short-", "full-", "half-")
_START_WORDS = STOPWORDS | META | frozenset("""research check monitor compare verify define explain find list show
tell give summarize summarise investigate determine please keep track notify watch these those is are does do""".split())
_CLAUSE_VERBS = frozenset("""determine check compare find explain list research investigate monitor update verify
summarize summarise show tell identify evaluate assess analyze analyse see look decide estimate describe confirm
notify track is are does do did has have was were can will should which what how why when who where""".split())


def _cap(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", (text or "").lower())]


def detect_language(question: str) -> tuple[str | None, str | None]:
    """-> (BCP-47-ish tag, note). ASCII-only text is tagged 'en'; other Latin text is left untagged."""
    for tag, pattern in _SCRIPTS:
        if re.search(pattern, question):
            return tag, (f"non-Latin script detected ({_SCRIPT_NAMES[tag]}): the question is searched as written; "
                         f"nothing is translated (no language model on the host) and English cue rules may miss")
    if question.isascii():
        return "en", None
    return None, "non-ASCII Latin text: language not identified; searched as written, not translated"


def extract_entities(question: str) -> list[str]:
    """Deterministic entity candidates in order of appearance: quoted phrases, CamelCase/mixed-case names,
    acronyms, capitalised word runs (sentence-initial function words dropped) and package-like tokens."""
    found: list[tuple[int, str]] = []
    for m in re.finditer(r"[\"“]([^\"”]{2,80})[\"”]", question):
        found.append((m.start(), m.group(1).strip()))
    for m in re.finditer(r"\b[A-Za-z]*[a-z][A-Z][\w]*(?:'s|’s)?|\b[A-Z][A-Z0-9]{1,9}\b", question):
        token = re.sub(r"(?:'s|’s)$", "", m.group(0))
        if token.upper() not in _GENERIC_ACRONYMS:
            found.append((m.start(), token))
    sentence_starts = {0} | {m.end() for m in re.finditer(r"[.?!;:]\s+", question)}
    for m in re.finditer(r"\b[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*", question):
        words = [re.sub(r"(?:'s|’s)$", "", w) for w in m.group(0).split()]
        if m.start() in sentence_starts and words and words[0].lower() in _START_WORDS:
            words = words[1:]
        words = [w for w in words if w not in MONTHS and w.upper() not in _GENERIC_ACRONYMS]
        if words and not (len(words) == 1 and words[0].lower() in _START_WORDS):
            found.append((m.start(), " ".join(words)))
    for m in re.finditer(r"(?<![\w./:-])([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)(?![\w/-])", question):
        token = m.group(1)
        if token not in _HYPHEN_WORDS and not token.startswith(_HYPHEN_PREFIX):
            found.append((m.start(), token))
    for m in re.finditer(r"\b([A-Za-z0-9][\w.-]*)==[0-9][\w.+-]*", question):
        found.append((m.start(), m.group(1)))
    out, seen = [], set()
    for _, entity in sorted(found, key=lambda t: t[0]):
        if entity.lower() not in seen:
            seen.add(entity.lower())
            out.append(entity)
    # a token that is part of a longer entity ("Wall" in "Great Wall") is not listed twice
    return [e for e in out
            if not any(e.lower() != o.lower() and set(e.lower().split()) <= set(o.lower().split()) for o in out)][:12]


def _packages(question: str) -> list[dict]:
    out = []
    for m in re.finditer(r"(?<![\w:])([A-Za-z0-9][\w.-]*)==([0-9][\w.+-]*)", question):
        out.append({"ecosystem": "PyPI", "name": m.group(1), "version": m.group(2).rstrip(".")})
    for m in re.finditer(r"\bpypi:([A-Za-z0-9][\w.-]*)", question, re.I):
        out.append({"ecosystem": "PyPI", "name": m.group(1).rstrip("."), "version": None})
    for m in re.finditer(r"\bnpm:(@?[\w.-]+(?:/[\w.-]+)?)", question, re.I):
        out.append({"ecosystem": "npm", "name": m.group(1).rstrip("."), "version": None})
    for m in re.finditer(r"\bgithub:([A-Za-z0-9-]+/[\w.-]+)", question, re.I):
        out.append({"ecosystem": "github", "name": m.group(1).rstrip("."), "version": None})
    return out


def _month_window(month: int, now: float) -> float:
    today = datetime.fromtimestamp(now, tz=timezone.utc)
    year = today.year if month <= today.month else today.year - 1
    start = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
    return min(366 * DAY, max(31 * DAY, now - start))


def _recency(question: str, low: str, now: float) -> list[tuple[str, float | str | None]]:
    cues = []
    for pattern, window in _RECENCY:
        for m in re.finditer(pattern, low):
            cues.append((m.group(0), window))
    if _NEW.search(question):
        cues.append((_NEW.search(question).group(0), None))
    year_now = datetime.fromtimestamp(now, tz=timezone.utc).year
    for m in re.finditer(r"\b(19|20)\d{2}\b", question):
        year = int(m.group(0))
        if year >= year_now:
            start = datetime(year_now, 1, 1, tzinfo=timezone.utc).timestamp()
            cues.append((m.group(0), max(DAY, now - start)))
    for i, name in enumerate(MONTHS, start=1):
        pattern = r"\b(?:in May|May\s+\d{1,4})\b" if name == "May" else rf"\b{name}\b"
        if re.search(pattern, question):
            cues.append((name, _month_window(i, now)))
    return cues


def _side(text: str, *, left: bool) -> str:
    text = re.sub(r"^\W+|\W+$", "", text.strip())
    lead = (r"^(?:(?:what|which|who|how|is|are|was|were|does|do|should|would|could|can|i|we|you|use|using|the|a|"
            r"an|between|compare|comparing|comparison of|tell me|me|about|it|better|faster|whether|one|"
            r"would you recommend)\s+)+")
    while True:
        new = re.sub(lead, "", text, flags=re.I)
        if new == text:
            break
        text = new
    text = re.split(r"\s+(?:for|in terms of|regarding|when|on|at|with respect to|to do|if)\s+", text, maxsplit=1)[0] \
        if not left else text
    words = text.split()
    words = words[-5:] if left else words[:5]
    return re.sub(r"^\W+|\W+$", "", " ".join(words))


def _comparison(question: str) -> list[str] | None:
    patterns = [
        r"\bdifferences? between (?P<a>.+?) and (?P<b>.+?)(?=[?.!;]|$)",
        r"\bcompar(?:e|ing|ison of) (?P<a>.+?) (?:and|with|to|against|vs\.?|versus) (?P<b>.+?)(?=[?!;]|\.\s|\.$|$)",
        r"\bwhich (?:one )?is (?:better|faster|safer|cheaper|more \w+)[,:]?\s+(?P<a>.+?) or (?P<b>.+?)(?=[?.!;]|$)",
        r"(?P<a>[^,:;?!]+?)\s+(?:vs\.?|versus)\s+(?P<b>[^,:;?!]+)",
        r"(?P<a>[^,:;?!]+?)\s+better than\s+(?P<b>[^,:;?!]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, question, re.I)
        if m:
            a, b = _side(m.group("a"), left=True), _side(m.group("b"), left=False)
            if a and b and a.lower() != b.lower():
                return [a, b]
    return None


def _claim(question: str) -> str | None:
    patterns = [r"\bis it (?:really )?true that (?P<c>.+?)(?=[?!]|$)",
                r"\b(?:verify|confirm|check) (?:that|whether|if) (?P<c>.+?)(?=[?!]|\.\s|\.$|$)",
                r"\bfact[- ]?check:?\s*(?P<c>.+?)(?=[?!]|$)",
                r"\bclaim(?:s|ed)? that (?P<c>.+?)(?=[?!]|\.\s|\.$|$)",
                r"\btrue that (?P<c>.+?)(?=[?!]|$)",
                r"\bverify:?\s+(?P<c>.+?)(?=[?!]|$)"]
    for pattern in patterns:
        m = re.search(pattern, question, re.I)
        if m:
            claim = m.group("c").strip().strip("\"'“”.").strip()
            if len(claim.split()) >= 2:
                return _cap(claim, 300)
    return None


def _sites(question: str, urls: list[str]) -> list[str]:
    sites = []
    stripped = _URL.sub(" ", question)
    for m in _DOMAIN.finditer(stripped):
        domain = m.group(1).lower()
        tld = domain.rsplit(".", 1)[-1]
        if tld in _FILE_EXT or re.fullmatch(r"[\d.]+", domain) or domain.count(".") < 1:
            continue
        sites.append(domain[4:] if domain.startswith("www.") else domain)
    for m in _SOURCE_NAMED.finditer(question):
        sites.append(SOURCE_SITES[m.group(1).lower()])
    out = []
    for s in sites:
        if s not in out:
            out.append(s)
    return out[:3]


def _relevant(item: dict, qwords: set) -> bool:
    words = {w for w in _words(item.get("statement", "")) if w not in STOPWORDS}
    return bool(words & qwords) if qwords else True


# ---- assess ------------------------------------------------------------------------------------------------------

def assess(question: str, *, now: float, known=()) -> dict:
    """Classify a question and decide whether the web is needed. See module docstring and ARCHITECTURE §4.2.

    `known`: accepted knowledge matching the question, each {statement, status, stale: bool, contested: bool}.
    """
    question = " ".join(str(question or "").split())
    low = question.lower()
    language, lang_note = detect_language(question)
    notes = [lang_note] if lang_note else []
    urls = [u.rstrip(".,;:!?") for u in _URL.findall(question)]
    sites = _sites(question, urls)
    entities = extract_entities(_URL.sub(" ", question))
    packages = _packages(question)

    recency = _recency(question, low, now)
    sci_strong = [m.group(0) for m in _SCI_STRONG.finditer(low)]
    sci_domain = [m.group(0) for m in _SCI_DOMAIN.finditer(low)]
    if (sci_strong or sci_domain) and re.search(r"\bresearch\b", low):
        sci_strong.append("research")
    scientific = bool(sci_strong or sci_domain)
    tech = [m.group(0) for m in _TECH.finditer(low)] + (["package reference"] if packages else [])
    technical = bool(tech)
    verify = [m.group(0) for m in _VERIFY.finditer(low)]
    monitor = [m.group(0) for m in _MONITOR.finditer(low)]
    change = [m.group(0) for m in _CHANGE.finditer(low)]
    deep = [m.group(0) for m in _DEEP.finditer(low)]
    if re.search(r"\bresearch\b.*\bdeep(?:ly)?\b|\bdeep(?:ly)?\b.*\bresearch\b", low) and "deeply" not in deep:
        deep.append("research deeply")
    comparison = _comparison(question)
    compare = bool(comparison or _COMPARE_CUE.search(low))
    claim = _claim(question) if verify else None
    arithmetic = bool(_ARITH.match(question)) and bool(re.search(r"\d\s*[-+*/×÷^x%]\s*\d", question))
    definitional = _DEFINE.match(question)
    definitional = bool(definitional) and len(definitional.group("t").split()) <= 6 and not recency

    why: dict[str, str] = {}
    # mode: first match wins
    if monitor:
        mode, why["mode"] = "continuous", f"monitoring requested ({monitor[0]!r})"
    elif change:
        mode, why["mode"] = "change_detection", f"change requested ({change[0]!r})"
    elif verify:
        mode, why["mode"] = "fact_verification", f"verification cue ({verify[0]!r})"
    elif compare:
        mode = "comparative"
        why["mode"] = (f"comparison of {comparison[0]!r} and {comparison[1]!r}" if comparison
                       else "comparison cue without two named sides")
    elif deep:
        mode, why["mode"] = "deep_research", f"depth requested ({deep[0]!r})"
    elif scientific:
        mode, why["mode"] = "scientific", f"scientific cue ({(sci_strong + sci_domain)[0]!r})"
    elif technical:
        mode, why["mode"] = "technical", f"technical cue ({tech[0]!r})"
    else:
        mode, why["mode"] = "quick_lookup", "no mode cue: a quick lookup"

    # recency window: the most restrictive explicit window
    window, window_label = None, None
    for label, value in recency:
        if value == "recent":
            value = 90 * DAY if scientific else 30 * DAY
        if isinstance(value, float) and (window is None or value < window):
            window, window_label = value, label
    if window is not None:
        why["recency_window"] = f"{window_label!r} -> {round(window / DAY, 1)} days"
    elif recency:
        why["recency_window"] = f"{recency[0][0]!r} asks for current information but names no window"
    else:
        why["recency_window"] = "no recency cue"

    if scientific and not (technical and len(tech) > len(sci_strong + sci_domain)):
        freshness, why["freshness_class"] = "scientific", f"scientific cue ({(sci_strong + sci_domain)[0]!r})"
    elif technical:
        freshness, why["freshness_class"] = "release", f"technical cue ({tech[0]!r}): release cadence"
    elif recency:
        if window is not None and window <= 7 * DAY or any(label == "breaking" for label, _ in recency):
            freshness = "breaking"
        else:
            freshness = "volatile"
        why["freshness_class"] = f"recency cue ({(window_label or recency[0][0])!r})"
    elif mode in ("continuous", "change_detection", "fact_verification"):
        freshness, why["freshness_class"] = "volatile", f"{mode} implies the answer may change"
    else:
        freshness, why["freshness_class"] = "stable", "no recency, release or scientific cue"

    kinds = []
    if scientific:
        kinds.append("academic")
    if technical:
        kinds.append("technical")
    if definitional and not (scientific or technical):
        kinds.append("encyclopedia")
    if recency and not (scientific or technical):
        kinds.append("news")
    kinds.append("web")
    why["kinds"] = ", ".join(kinds) + (" (scientific)" if scientific else "") + (" (technical)" if technical else "")

    # ---- web decision ----
    qwords = {w for w in _words(question) if w not in STOPWORDS}
    relevant = [k for k in known or () if isinstance(k, dict) and _relevant(k, qwords)]
    usable = [k for k in relevant if k.get("status", "accepted") in ("accepted", "provisional")]
    fresh = [k for k in usable if not k.get("stale") and not k.get("contested")]
    stale = [k for k in usable if k.get("stale")]
    contested = [k for k in usable if k.get("contested")]
    statements = " ".join(str(k.get("statement", "")).lower() for k in usable)
    unfamiliar = [e for e in entities if e.lower() not in statements]

    hard, soft = [], []
    hard += [f"URL given: {_cap(u, 120)}" for u in urls[:3]]
    if recency:
        hard.append(f"recency requested: {window_label or recency[0][0]}")
    if verify:
        hard.append("claim requires verification" + (f": {_cap(claim, 120)}" if claim else ""))
    if monitor:
        hard.append("continuous monitoring requested")
    if change:
        hard.append("change detection needs a fresh comparison")
    hard += [f"knowledge stale: {_cap(str(k.get('statement', '')), 80)}" for k in stale[:2]]
    hard += [f"knowledge contested: {_cap(str(k.get('statement', '')), 80)}" for k in contested[:2]]
    if scientific:
        soft.append(f"scientific question ({(sci_strong + sci_domain)[0]}): literature must be consulted")
    if technical:
        soft.append(f"technical question ({tech[0]}): registries and advisories change")
    if comparison:
        soft.append(f"comparison of {comparison[0]} and {comparison[1]}")
    soft += [f"unfamiliar entity: {e}" for e in unfamiliar[:5]]

    if arithmetic and not hard:
        needs_web, reasons = False, ["pure arithmetic: computed locally, no web needed"]
    elif definitional and not hard and fresh:
        needs_web, reasons = False, [f"stable definitional question answered by {len(fresh)} fresh, uncontested "
                                     f"knowledge item(s)"]
    elif not hard and not soft and fresh:
        needs_web, reasons = False, [f"answered by {len(fresh)} fresh, uncontested knowledge item(s); "
                                     f"no recency, verification, technical or scientific cue"]
    else:
        needs_web, reasons = True, hard + soft
        if not fresh:
            reasons.append("no fresh, uncontested knowledge answers this")
    if urls:
        notes.append(f"{len(urls)} URL(s) named: fetched directly, not searched")
    if technical and not packages:
        notes.append("no packages listed: set assessment['packages'] = [{ecosystem, name, version}] for registry "
                     "and advisory lookups")
    return {"needs_web": needs_web, "reasons": reasons, "mode": mode, "freshness_class": freshness,
            "recency_window": window, "kinds": kinds, "entities": entities, "urls": urls,
            "comparison": comparison, "claim": claim, "packages": packages, "sites": sites, "language": language,
            "notes": notes, "why": why}


# ---- plan --------------------------------------------------------------------------------------------------------

def keywords(text: str, *, limit: int = 8) -> str:
    """Topical words of `text`, in order, without function words and request/cue vocabulary."""
    out = []
    for token in re.findall(r"[^\W_][\w.+#'’-]*", text or ""):
        token = re.sub(r"(?:'s|’s|[.'’-])+$", "", token)
        low = token.lower()
        if not token or low in STOPWORDS or low in META or re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if low not in (o.lower() for o in out):
            out.append(token)
    return " ".join(out[:limit])


def decompose(question: str) -> list[str]:
    """Split on ';', '?' and sentence ends, then on ' and ' when the right side starts a new clause (verb first)."""
    parts = [p.strip() for p in re.split(r"[;?]\s*|(?<=[.!])\s+", question) if p.strip()]
    clauses = []
    for part in parts:
        pieces = re.split(r"\s+and\s+", part)
        current = pieces[0]
        for piece in pieces[1:]:
            first = (piece.split() or [""])[0].lower()
            if first in _CLAUSE_VERBS:
                clauses.append(current)
                current = piece
            else:
                current = f"{current} and {piece}"
        clauses.append(current)
    return [c.strip(" .!") for c in clauses if c.strip(" .!")]


def plan(question: str, assessment: dict, *, now: float) -> QueryPlan:
    question = " ".join(str(question or "").split())
    mode = assessment.get("mode", "quick_lookup")
    kinds = list(assessment.get("kinds") or ["web"])
    window = assessment.get("recency_window")
    since = now - window if window else None
    lang = assessment.get("language")
    cap = min(MAX_PLAN_QUERIES, MODE_BUDGETS.get(mode, MODE_BUDGETS["quick_lookup"]).max_queries)
    general = "news" if "news" in kinds else "web"
    year = datetime.fromtimestamp(now, tz=timezone.utc).year
    notes = list(assessment.get("notes") or [])
    items: list[tuple[int, Query]] = []

    def add(priority, text, kind, purpose, *, site=None, when=since):
        text = " ".join(str(text).split())[:300]
        if text:
            items.append((priority, Query(text=text, kind=kind, since=when, site=site, language=lang,
                                          purpose=purpose)))

    for pkg in assessment.get("packages") or []:
        eco, name, ver = str(pkg.get("ecosystem") or "PyPI"), str(pkg.get("name") or ""), pkg.get("version")
        if not name:
            continue
        at = f"@{ver}" if ver else ""
        if eco.lower() == "pypi":
            add(0, f"pypi:{name}", "technical", f"registry: latest release of {name}", when=None)
            add(0, f"osv:PyPI:{name}{at}", "technical", f"advisories for {name}{at}", when=None)
        elif eco.lower() == "npm":
            add(0, f"npm:{name}", "technical", f"registry: latest release of {name}", when=None)
            add(0, f"osv:npm:{name}{at}", "technical", f"advisories for {name}{at}", when=None)
        elif eco.lower() == "github":
            add(0, f"github:{name}", "technical", f"releases of {name}", when=None)
        else:
            add(0, f"osv:{eco}:{name}{at}", "technical", f"advisories for {name}{at}", when=None)

    comparison = assessment.get("comparison")
    searchable = _URL.sub(" ", question)
    for site in assessment.get("sites") or []:   # a named domain becomes a site: restriction, not a keyword
        searchable = re.sub(rf"(?i)\b(?:site:)?(?:www\.)?{re.escape(site)}\b", " ", searchable)
    searchable = " ".join(searchable.split())
    clauses = [] if comparison else decompose(searchable)
    topics: list[str] = []
    if comparison:
        a, b = comparison
        context = " ".join(w for w in keywords(searchable).split()
                           if w.lower() not in (a + " " + b).lower().split())[:80]
        add(1, f"{a} vs {b}", general, "comparison: joint")
        add(1, f"{a} {context}", general, f"comparison side A: {a}")
        add(1, f"{b} {context}", general, f"comparison side B: {b}")
        topics.append(f"{a} {b}")
    claim = assessment.get("claim")
    if claim:
        kw = keywords(claim) or claim
        add(1, kw, "web", "claim under verification")
        add(3, f"{kw} fact check", "web", "expansion: verification")
        topics.append(kw)
    subquestions = tuple(clauses) if len(clauses) > 1 else ()
    for i, clause in enumerate(clauses):
        kw = keywords(clause)
        label = f"sub-question {i + 1}" if subquestions else "question"
        if not kw:
            if subquestions:
                notes.append(f"sub-question {i + 1} has no searchable topic (instruction only): {_cap(clause, 80)}")
            continue
        topics.append(kw)
        add(1, kw, general, label)
        if "academic" in kinds:
            add(1, keywords(clause, limit=5), "academic", f"{label}: scholarly")
        if "encyclopedia" in kinds:
            add(1, kw, "encyclopedia", f"{label}: encyclopedia")
    if not topics and not assessment.get("packages"):
        entities = assessment.get("entities") or []
        if entities:
            topics.append(" ".join(entities[:3]))
            add(1, topics[0], general, "entities only")
        else:
            notes.append("no searchable topic in the question: name the claim or supply the sources (URLs)")
    for site in assessment.get("sites") or []:
        for topic in topics[:1]:
            add(2, topic, "web", f"site: user named {site}", site=site)
    for topic in topics[:2]:
        if since is not None and str(year) not in topic:
            add(3, f"{topic} {year}", general, "expansion: year for the recency window")
        if "academic" in kinds:
            add(3, f"{keywords(topic, limit=5)} review", "academic", "expansion: reviews")
            add(4, f"{topic} study", "web", "expansion: studies")
        if "technical" in kinds:
            add(3, f"{topic} release notes", "web", "expansion: release notes")
            add(4, f"{topic} changelog", "web", "expansion: changelog")

    queries, seen = [], set()
    for _, q in sorted(enumerate(items), key=lambda t: (t[1][0], t[0])):
        query = q[1]
        key = (query.text.lower(), query.kind, query.site, query.since)
        if key not in seen:
            seen.add(key)
            queries.append(query)
    if len(queries) > cap:
        notes.append(f"{len(queries) - cap} planned queries dropped by the {cap}-query cap ({mode})")
        queries = queries[:cap]
    return QueryPlan(queries=tuple(queries), subquestions=subquestions,
                     max_sources=MODE_BUDGETS.get(mode, MODE_BUDGETS["quick_lookup"]).max_pages,
                     notes=tuple(notes))


def refine(plan: QueryPlan, outcome, assessment: dict, *, now: float) -> QueryPlan | None:
    """One broader round when discovery was thin (< 3 results) or every provider failed; else None.
    Broader = no `since`, no `site`, text shortened to the key entities (or the first three keywords)."""
    if any(str(n).startswith("refinement") for n in plan.notes):
        return None
    attempts = [a for a in outcome.attempts if a.get("provider")]
    all_failed = bool(attempts) and not any(a.get("ok") and a.get("n") for a in attempts)
    n = len(outcome.results)
    if n >= 3 and not all_failed:
        return None
    reason = "every provider failed" if all_failed else f"only {n} result(s)"
    entities = [e for e in assessment.get("entities") or [] if len(e) > 1][:3]
    kinds = []
    for q in plan.queries:
        if q.kind not in kinds and q.kind != "technical":
            kinds.append(q.kind)
    if "web" not in kinds:
        kinds.append("web")        # broader: general web search in addition to the planned kinds
    texts = []
    if entities:
        texts.append(" ".join(entities))
    for q in plan.queries:
        if q.kind == "technical":
            continue
        short = " ".join(keywords(q.text).split()[:3])
        if short and short.lower() not in (t.lower() for t in texts):
            texts.append(short)
    original = {(q.text.lower(), q.kind) for q in plan.queries if q.since is None and q.site is None}
    queries, seen = [], set()
    for text in texts:
        for kind in kinds:
            key = (text.lower(), kind)
            if key in seen or key in original:
                continue
            seen.add(key)
            queries.append(Query(text=text, kind=kind, language=assessment.get("language"),
                                 purpose=f"refinement: broadened after {reason} (no date/site limit, key terms)"))
    if not queries:
        return None
    return QueryPlan(queries=tuple(queries[:4]), subquestions=plan.subquestions, max_sources=plan.max_sources,
                     notes=(f"refinement: {reason}; dropped date and site limits, shortened to key terms",))


__all__ = ["assess", "plan", "refine", "keywords", "decompose", "extract_entities", "detect_language"]
