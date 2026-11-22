"""INTENT: decide whether the web is needed, classify the request, and plan the searches (ADR-022: no model).

Everything here is deterministic and rule-based; every decision carries a human-readable reason (`reasons` for the
web decision, `why` for mode / freshness / kinds / window). Cue tables are English: a question in another script is
kept verbatim and tagged with its language — nothing is translated, because there is no model on the host.

    assess(question, now=, known=)  -> assessment dict (ARCHITECTURE §4.2)
    plan(question, assessment, now=, local_terms=) -> QueryPlan   decomposition, expansion, temporal/site/kind
                                                                   routing, ≤ 8 queries
    refine(plan, outcome, assessment, now=, local_terms=, remaining=) -> QueryPlan | None   one broader round when
                                                                   results are thin, within the query budget
    query_text(statement, limit=, local_terms=) -> str   search text for a stored statement (curiosity/refresh)

Local context: references to the individual's own system — its name (SELF_NAMES), "our/my <thing>", "this
individual", "the libraries we use", and the caller's `local_terms` (owner-listed private terms) — are answered
from local documents and state, never sent to a search engine. strip_local removes them (with the noun phrase a
possessive reference owns) before any query is built, and the plan's notes say so.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from . import sense, temporal
from .config import MODE_BUDGETS
from .types import Query, QueryPlan

DAY = 86400.0
MAX_PLAN_QUERIES = 8
MAX_REFINE_QUERIES = 4
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
# "When was Python first released?", "Who founded ...", "How many neurons ...": one fact about a named thing, which an
# encyclopedia states in its lead (T1-07); the encyclopedia is searched as well as the general web.
_SIMPLE_FACT = re.compile(r"^\s*(?:when|who|where|(?:in )?(?:what|which) year|how (?:many|old|long|tall|far|big|large|"
                          r"heavy|deep|high))\b", re.I)
# Broad current-events questions ("What happened in AI this week?") name a field, not a story: SearXNG news is
# searched for the field and its spelled-out name, plus general web headlines in the same window (T1-06).
_FACT_PREDICATE = frozenset("""first last released release founded invented created introduced born died launched discovered
published built made started began begun named called many much old long tall far big large heavy deep high
year""".split())
# A technical one-fact question names software ("When was Rust first released?" is a technical question because of
# "released"); asked bare, the encyclopedia returns the other senses first (Rust: iron oxide, the 2024 film; Python:
# the snake, Monty Python; RC2 retest T1-07). Unless the thing already names its sense, "software" is added.
_SOFTWARE_SENSE = frozenset("""software programming language library framework package app application tool toolkit
database compiler editor browser os kernel engine runtime interpreter sdk api protocol""".split())
BROAD_NEWS_MAX_WINDOW = 8 * DAY
BROAD_NEWS_MAX_WORDS = 2
_SPELLED_OUT = {"ai": "artificial intelligence", "ml": "machine learning", "llm": "large language models",
                "llms": "large language models", "nlp": "natural language processing", "ev": "electric vehicles",
                "evs": "electric vehicles", "vr": "virtual reality", "ar": "augmented reality"}
# A wh-question with a verification cue but no statement to check ("How many neurons ..., and what can be
# established about that number?") is researched, not fact-checked: there is nothing checkable (T1-07).
_WH_QUESTION = re.compile(r"^\s*(?:how|what|when|where|which|who|whom|whose|why)\b", re.I)
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
SELF_NAMES = ("NeuroSciFi", "Neuro SciFi", "Neuro-SciFi")
_SELF_PHRASES = (r"our", r"my", r"(?:this|the) individual", r"(?:this|the) instance", r"this system")
_WE_USE = re.compile(r"(?<!\w)(?:the\s+)?(?:[A-Za-z][\w-]*\s+){1,2}(?:that\s+|which\s+)?(?:we|i)\s+(?:use|run|"
                     r"depend on|rely on|maintain|built|build|ship|deploy)(?!\w)", re.I)
MAX_OWNED_WORDS = 4
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
# Words that only place a statement in time: a query made of them (and stopwords) searches for nothing.
_TEMPORAL = frozenset("""last past ago next previous earlier later since until till day days hour hours minute
minutes tomorrow morning evening weekend weekly daily monthly quarterly""".split())

# ---- plain package names (W-11) --------------------------------------------------------------------------------
# A release cue makes lower-case identifiers in a technical / comparative / quick question registry candidates.
_RELEASE_CUE = re.compile(
    r"\b(?:latest|newest|current|most recent)\s+(?:stable\s+)?(?:release|version)s?\b|\blatest stable\b|"
    r"\brelease (?:dates?|notes|history)\b|\bsecurity advisor(?:y|ies)\b|\badvisor(?:y|ies)\b|"
    r"\bvulnerabilit(?:y|ies)\b|\bnew(?:er|est)? (?:stable )?(?:releases?|versions?)\b")
# A plain release word ("Was numpy 2.5.5 released?", "numpy 2.5.5 version") is a strong cue only next to a name
# carrying a dotted version: "released" alone is too common ("the film was released") to make words registry names.
# The registry aspects of CLAIM_VERIFICATION §5 count too ("Is corvane 2.5.4 stable?", "Is numpy 2.5.5 secure?"):
# only the package registries (and OSV for security) can decide them.
_VERSIONED_RELEASE_CUE = re.compile(r"\b(?:released?|releases|versions?|published|stable|unstable|pre-?releases?|"
                                    r"yanked|compatible|incompatible|secure|insecure|vulnerable|recommended|"
                                    r"latest)\b")
_NAME_WITH_VERSION = re.compile(r"(?<![\w.:/@`'’-])@?[a-z][\w.-]*(?:/[\w.-]+)?(?:\s+(?:version\s+)?v?|==)\d+(?:\.\d+)+")
_REGISTRY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_~!-]{0,63}")   # providers.structured.VERSION
# weak cues admit only names that look like package names (code style, "X library", digits/hyphens/dots)
_WEAK_RELEASE_CUE = re.compile(r"\bupdates?\b|\bupgrades?\b|\bchangelogs?\b")
MAX_PLAIN_QUESTION_WORDS = 14   # plain lower-case words are candidates only in short questions
_ADVISORY_CUE = re.compile(r"\b(?:advisor(?:y|ies)|vulnerabilit(?:y|ies)|vulnerable|cves?|security|secure|"
                           r"insecure)\b", re.I)
_CODE_NAME = re.compile(r"`([A-Za-z0-9@][\w.@/-]{0,99})`")
_NAMED_PACKAGE = re.compile(r"\b([A-Za-z0-9][\w.-]{0,99})\s+(?:(?:python|javascript|js|node(?:\.js)?|npm|pypi|"
                            r"typescript)\s+)?(?:package|library|lib|module)s?\b", re.I)
_LOWER_NAME = re.compile(r"(?<![\w.:/@`'’-])([a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)(?![\w:/@`'’-])")
# Package names are case-insensitive in the registries, but capitalised words are mostly proper nouns ("What is the
# latest version of Quillstream?"). Two capitalised shapes are folded to lower case and treated like a plain word:
# an internal capital (NumPy, PyTorch, SciPy, FastAPI) and a capitalised name carrying a dotted version
# (Django 5.2.3, NumPy 2.5.5).
_CASED_NAME = re.compile(r"(?<![\w.:/@`'’-])([A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]*(?=\s+(?:version\s+)?"
                         r"v?\d+(?:\.\d+)+))(?:[._-][A-Za-z0-9]+)*(?![\w:/@`'’-])")
_PKG_NAME = re.compile(r"@?[a-z0-9](?:[a-z0-9._/-]{0,98}[a-z0-9])?")
_NOT_PACKAGES = frozenset("""python python3 javascript typescript node nodejs java rust golang go ruby php perl kotlin
swift scala linux windows macos android ios chrome firefox safari pypi npm pip github gitlab conda homebrew brew
package packages library libraries lib libs module modules framework frameworks tool tools app apps software
release releases released version versions stable dates date notes advisory advisories security vulnerability
vulnerabilities changelog changelogs patch patches fix fixes bug bugs secure insecure vulnerable recommended
yanked compatible incompatible unstable prerelease pre-release prereleases pre-releases latest""".split())
_COMMON = frozenset("""
able about above across actually after again against ago all almost alone along already also although always
among amount and another answer any anyone anything are area around ask asked available away back bad base based
basic be became because become been before began begin behind being believe below best better between big bit
both bring broken build built but buy by call called came can cannot case cases cause change changed changes
cheap check choose city class clear close code come comes coming common company compare complete consider
could country course current data date day days deal default did different difficult do does doing done down
during each early easy either else end enough even ever every example expected fact fast feature features few
file files final find first fix fixed follow following for found free from full future get gets getting give
given go goes going good got great group had happen happened hard has have help here high history hold home how
however idea if important improve include included includes including info information install installed
instead interest into issue issues it its just keep kind know known large last late later least less let level
life like likely line list little live local long look looking lot low made main major make makes many may mean
means might minor more most move much must my name names near need needed needs never new newer newest news next
no none not note nothing now number of off often old older on once one only open or order other others our out
over own page part people per perhaps place plan point possible post power present pretty previous price problem
problems product program public put question questions quick quite rather read ready real really reason recent
recently related remove report reports required result results return right run running safe said same say see
seem seen send server set setup several should show side similar simple since site small so some someone
something soon source sources start state still stop such support supported sure system take team tell test than
that the their them then there these they thing things think this those though through time times to today
together too top total try trying turn type under until up upgrade upgraded upgrades upgrading upon us use used
user users using usual value very via want was way ways we web well went were what when where whether which while
who whole why will with within without work working works world would write year years yet you your
""".split())
_PYPI_HINT = re.compile(r"\b(?:python3?|pypi|pip|conda|anaconda|jupyter|ipython|dataframes?|data\s+science|"
                        r"machine\s+learning|scientific\s+(?:python|computing)|array\s+computing)\b", re.I)
_NPM_HINT = re.compile(r"\b(?:npm|node(?:\.js)?|nodejs|javascript|typescript|js|ts|yarn|pnpm|deno|react|vue|"
                       r"angular|webpack|frontend|front-end)\b", re.I)
MAX_INFERRED_PACKAGES = 3
# Project names whose PyPI distribution is named differently; the PyPI name is a placeholder that refuses to install.
_PYPI_NAMES = {"pytorch": "torch", "sklearn": "scikit-learn"}
_SIDE_PREDICATE = frozenset("""latest newest newer recent current stable lts release releases released version
versions date dates update updates updated changelog changelogs advisory advisories security vulnerability
vulnerabilities news""".split())


def strip_local(text: str, local_terms=()) -> tuple[str, int]:
    """(text without references to the individual's own system, how many were removed). A possessive reference
    ("NeuroSciFi's learning architecture", "our system's learning rule", "my notes") is removed with the noun
    phrase it owns (up to MAX_OWNED_WORDS non-function words); a bare name is removed alone."""
    text = str(text or "")
    text, count = _WE_USE.subn(" ", text)
    names = sorted({t.strip() for t in (*SELF_NAMES, *(local_terms or ())) if isinstance(t, str) and t.strip()},
                   key=len, reverse=True)
    alternatives = [re.escape(n) for n in names] + list(_SELF_PHRASES)
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(alternatives) + r")(?:'s|’s|s')?(?![\w-])", re.I)
    out, pos = [], 0
    for m in pattern.finditer(text):
        if m.start() < pos:
            continue
        out.append(text[pos:m.start()])
        end, token = m.end(), m.group(0).lower()
        if token in ("our", "my") or token.endswith(("'s", "’s", "s'")):
            for _ in range(MAX_OWNED_WORDS):
                word = re.match(r"\s+(?:own\s+)?([^\W_][\w'’-]*)", text[end:])
                if not word or word.group(1).lower() in STOPWORDS or word.group(1).lower() in META:
                    break
                end += word.end()
        pos = end
        count += 1
    out.append(text[pos:])
    return " ".join("".join(out).split()), count


# "Latest releases affecting NeuroSciFi", "security advisories affecting our dependencies": releases of the
# individual's own dependencies. Once the self-reference is stripped nothing topical is left ("releases"), so a web
# search would look for the bare word; the dependency check (deps.py, `nsf web deps`) answers it instead.
_DEPENDENCY_CUE = re.compile(r"\b(?:releases?|released|updates?|upgrades?|versions?|advisor(?:y|ies)|"
                             r"vulnerabilit(?:y|ies)|cves?|security|dependenc(?:y|ies)|librar(?:y|ies)|packages?)\b",
                             re.I)
_DEPENDENCY_WORDS = frozenset("""releases release released update updates upgrade upgrades version versions advisory
advisories vulnerability vulnerabilities cve cves security dependency dependencies library libraries package packages
affecting affect affects affected impacting impact impacts relevant newer newest new stable important critical known
available pending check whether there have has any""".split())


def dependency_question(question: str, local_terms=()) -> bool:
    """True when the question asks about releases / advisories of the individual's own dependencies: it refers to
    the individual's own system (strip_local), carries a release/advisory/dependency cue, and no topical word is
    left once the self-reference and that vocabulary are removed. "Latest releases affecting NeuroSciFi" -> True;
    "Latest numpy releases" (no self-reference) and "evaluate implications of dendritic computation for NeuroSciFi"
    (a topic remains) -> False."""
    question = " ".join(str(question or "").split())
    stripped, n = strip_local(question, local_terms)
    if not n or not _DEPENDENCY_CUE.search(question):
        return False
    return not [w for w in keywords(stripped).split() if w.lower() not in _DEPENDENCY_WORDS]


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


def _plain_packages(question: str, known=(), *, plain_words: bool = True) -> list[dict]:
    """Registry candidates named plainly ("numpy", "`left-pad`", "the requests library") in a question that carries a
    release cue. Candidates: code-style names, names followed by package/library/module, and lower-case identifiers
    that are not stopwords, cue vocabulary or common English — a plain word ("numpy") only when `plain_words` (a
    strong release cue) and the question is short (MAX_PLAIN_QUESTION_WORDS); otherwise only identifier-shaped ones
    ("left-pad", "py3dns", "zope.interface"). The ecosystem is PyPI and npm (both; the registry that does not know
    the name answers 404, a soft miss) unless the question names one; `inferred` marks them. References to the
    individual's own system are removed first (strip_local)."""
    text = _URL.sub(" ", strip_local(question)[0])
    plain_words = plain_words and len(text.split()) <= MAX_PLAIN_QUESTION_WORDS
    found: list[tuple[int, str]] = []
    for pattern in (_CODE_NAME, _NAMED_PACKAGE):
        for m in pattern.finditer(text):
            found.append((m.start(1), m.group(1).lower()))
    for m in _LOWER_NAME.finditer(text):
        if plain_words or re.search(r"[\d._-]", m.group(1)):
            found.append((m.start(1), m.group(1)))
    if plain_words:
        found += [(m.start(1), m.group(0).lower()) for m in _CASED_NAME.finditer(text)]
    skip = STOPWORDS | META | _TEMPORAL | _START_WORDS | _CLAUSE_VERBS | _COMMON | _NOT_PACKAGES | _HYPHEN_WORDS | {
        m.lower() for m in MONTHS} | {str(k).lower() for k in known}
    names: list[str] = []
    for _, name in sorted(found):
        name = name.rstrip(".-_")
        if (len(name) < 2 or name in skip or name in names or not _PKG_NAME.fullmatch(name)
                or re.fullmatch(r"[\d.]+", name) or name.startswith(_HYPHEN_PREFIX)):
            continue
        names.append(name)
    hint = ecosystem_hint(text)
    ecosystems = (hint,) if hint else ("PyPI", "npm")
    out = []
    for name in names[:MAX_INFERRED_PACKAGES]:
        m = re.search(rf"(?<![\w.-]){re.escape(name)}\s+(?:version\s+)?v?(\d+(?:\.\d+)+[\w.+-]*)", text, re.I)
        version = m.group(1).rstrip(".") if m else None
        for eco in ecosystems:
            if eco == "npm" and name.startswith("@") and "/" not in name:
                continue
            out.append({"ecosystem": eco, "name": _PYPI_NAMES.get(name, name) if eco == "PyPI" else name,
                        "version": version, "inferred": True})
    return out


def ecosystem_hint(text: str) -> str | None:
    """The package ecosystem the wording points to: "PyPI" (python, pip, pypi, conda, jupyter, data science,
    scientific/array computing ...), "npm" (npm, node, javascript, typescript, yarn, react ...), or None when it
    names neither or both (synthesis.package_ecosystems then decides from the records themselves)."""
    py, js = bool(_PYPI_HINT.search(text or "")), bool(_NPM_HINT.search(text or ""))
    return "PyPI" if py and not js else ("npm" if js and not py else None)


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
    # the question's predicate is not part of a side: "numpy vs pandas latest release dates" -> numpy | pandas,
    # "the latest release dates of numpy and pandas" -> numpy | pandas
    if left:
        while len(words) > 1 and (words[0].lower() in _SIDE_PREDICATE or words[0].lower() in STOPWORDS):
            words = words[1:]
    else:
        cut = next((i for i, w in enumerate(words) if i and w.lower().strip(".,;:") in _SIDE_PREDICATE), None)
        words = words[:cut] if cut else words
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


MEMORY_COVERAGE = 0.6   # share of the question's topical terms a knowledge item must state to answer it alone


def _stems(text: str) -> set:
    return {w[:5] for w in _words(text) if w not in STOPWORDS and w not in META}


def covers(item: dict, question: str) -> bool:
    """True when the item's statement states at least MEMORY_COVERAGE of the question's topical terms (5-letter
    stems): sharing one name ("Orbix") is not an answer to another property of it (encryption vs port)."""
    terms = _stems(keywords(question, limit=12))
    if not terms:
        return False
    return len(terms & _stems(str(item.get("statement", "")))) / len(terms) >= MEMORY_COVERAGE


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
    simple_fact = bool(_SIMPLE_FACT.match(question)) and not recency and not packages and not \
        _NAME_WITH_VERSION.search(low)

    why: dict[str, str] = {}
    # mode: first match wins
    if monitor:
        mode, why["mode"] = "continuous", f"monitoring requested ({monitor[0]!r})"
    elif change:
        mode, why["mode"] = "change_detection", f"change requested ({change[0]!r})"
    elif verify and (claim or not _WH_QUESTION.match(question)):
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

    # plain package names with a release cue -> registry lookups (W-11)
    strong_cue = bool(_RELEASE_CUE.search(low) or (_VERSIONED_RELEASE_CUE.search(low) and
                                                   _NAME_WITH_VERSION.search(low)))
    if mode in ("technical", "comparative", "quick_lookup") and (strong_cue or _WEAK_RELEASE_CUE.search(low)):
        inferred = _plain_packages(question, known=[p["name"] for p in packages], plain_words=strong_cue)
        if inferred:
            packages = packages + inferred
            tech.append("package name with a release cue")
            technical = True
            if mode == "quick_lookup":
                mode, why["mode"] = "technical", f"package name(s) with a release cue: {inferred[0]['name']!r}"

    dependencies = mode in ("quick_lookup", "technical") and dependency_question(question)
    if dependencies:
        mode, why["mode"] = "technical", "releases or advisories of the individual's own dependencies: dependency check"
        why["dependency_check"] = ("the question names this system and only release/advisory words remain: the "
                                   "packages in use are checked in their registries (nsf web deps), not searched")
        if "dependency check" not in tech:
            tech.append("dependency check")
        technical = True

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
    elif technical and not recency and temporal.historical_release(question):
        freshness, why["freshness_class"] = "stable", ("a question about a first release is historical: no recency "
                                                       "requirement")
    elif technical:
        freshness, why["freshness_class"] = "release", f"technical cue ({tech[0]!r}): release cadence"
    elif recency:
        if window is not None and window <= 7 * DAY or any(label == "breaking" for label, _ in recency):
            freshness = "breaking"
        else:
            freshness = "volatile"
        why["freshness_class"] = f"recency cue ({(window_label or recency[0][0])!r})"
    elif mode in ("continuous", "change_detection"):
        freshness, why["freshness_class"] = "volatile", f"{mode} implies the answer may change"
    else:
        freshness, why["freshness_class"] = "stable", "no recency, release or scientific cue"

    kinds = []
    if scientific:
        kinds.append("academic")
    if technical:
        kinds.append("technical")
    if definitional and not (scientific or technical) or simple_fact:
        kinds.append("encyclopedia")
    if recency and not (scientific or technical):
        kinds.append("news")
    kinds.append("web")       # a dependency check plans no topic query of any kind (plan)
    why["kinds"] = ", ".join(kinds) + (" (scientific)" if scientific else "") + (" (technical)" if technical else "") \
        + (" (one fact about a named thing: the encyclopedia too)" if simple_fact else "")

    # ---- web decision ----
    qwords = {w for w in _words(question) if w not in STOPWORDS}
    relevant = [k for k in known or () if isinstance(k, dict) and _relevant(k, qwords)]
    usable = [k for k in relevant if k.get("status", "accepted") in ("accepted", "provisional")]
    fresh = [k for k in usable if not k.get("stale") and not k.get("contested") and covers(k, question)]
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
            reasons.append("no fresh, uncontested knowledge answers this" +
                           (f" ({len(usable)} related item(s) do not cover the question's terms)" if usable else ""))
    if urls:
        notes.append(f"{len(urls)} URL(s) named: fetched directly, not searched")
    if dependencies:
        notes.append("dependency check: the packages in use come from the project inventory (nsf web deps)")
    elif technical and not packages:
        notes.append("no packages listed: set assessment['packages'] = [{ecosystem, name, version}] for registry "
                     "and advisory lookups")
    return {"needs_web": needs_web, "reasons": reasons, "mode": mode, "freshness_class": freshness,
            "memory_items": [k["id"] for k in fresh if "id" in k] if not needs_web else [],
            "recency_window": window, "kinds": kinds, "entities": entities, "urls": urls,
            "comparison": comparison, "claim": claim, "packages": packages, "sites": sites, "language": language,
            "notes": notes, "why": why, "dependency_check": dependencies, "simple_fact": simple_fact}


# ---- plan --------------------------------------------------------------------------------------------------------

def keywords(text: str, *, limit: int = 8) -> str:
    """Topical words of `text`, in order, without function words and request/cue vocabulary."""
    out = []
    for token in re.findall(r"[^\W_][\w.+#'’-]*", text or ""):
        token = re.sub(r"(?:'s|’s|[.'’-])+$", "", token)
        low = token.lower()
        if (not token or low in STOPWORDS or low in META or low in _TEMPORAL
                or re.fullmatch(r"(?:19|20)\d{2}", token)):
            continue
        if low not in (o.lower() for o in out):
            out.append(token)
    return " ".join(out[:limit])


_REGISTRY_QUERY = re.compile(r"^(?:pypi|npm|osv|github):\S", re.I)


def searchable_text(text: str) -> bool:
    """A query worth sending: a registry lookup (pypi:/npm:/osv:/github:) or text with at least one topical word.
    Text made only of stopwords, cue words, temporal words ("last", "past week") and years searches for nothing."""
    text = str(text or "").strip()
    return bool(_REGISTRY_QUERY.match(text) or keywords(text))


def query_text(statement: str, *, limit: int = 8, local_terms=()) -> str:
    """Search text for a stored statement (curiosity, refresh, hypotheses): the statement's own topical words in
    their original form and order — never claim-key stems — without URLs, citation markers, function, cue and
    temporal words or references to the individual's own system. '' when nothing searchable remains. A templated
    statement over a structured record ("OpenAlex record: \"T\", dated ...") searches for the record's subject (T),
    never for the template's frame (W-20: "OpenAlex record Yes human brain ...")."""
    from .claims import record_template
    subject = record_template(str(statement or ""))
    if subject is not None:
        statement = subject
    text = re.sub(r"\[\d+(?:[,\s]+\d+)*\]", " ", _URL.sub(" ", str(statement or "")))
    text = text.replace("«", " ").replace("»", " ").replace("“", " ").replace("”", " ").replace('"', " ")
    return keywords(strip_local(text, local_terms)[0], limit=limit)


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


# ---- references to something said earlier (T1-07, T1-10) --------------------------------------------------------
# "that version", "this paper", "the same package": a definite reference to something named before; "it", "its",
# "they", "their": a pronoun. Inside one question they point back to its first clause ("... and evaluate their
# implications"); a whole question made of them points back to the previous question of the conversation.
_ANAPHOR_NOUNS = ("version|release|package|library|module|framework|tool|project|language|model|paper|study|article|"
                  "claim|result|number|figure|value|estimate|topic|one")
_DEFINITE_ANAPHOR = re.compile(rf"\b(?:that|this|the same|those|these)\s+(?P<noun>{_ANAPHOR_NOUNS})s?\b", re.I)
_PRONOUN = re.compile(r"(?<![\w'’-])(?P<p>it|its|they|them|their)(?![\w'’-])", re.I)
# "Is it true that ...", "it is said that ...": an expletive "it" refers to nothing
_EXPLETIVE = re.compile(r"\b(?:is|was) it (?:really |actually |still )?(?:true|false|possible|likely|the case|known)\b|"
                        r"\bit (?:is|was|'s|’s) (?:true|false|said|claimed|reported|known|possible)\b", re.I)
# words that ask about an aspect of a subject without naming one ("Is it maintained?", "What is its license?")
_ASPECT_WORDS = frozenset("""maintained popular deprecated supported fast slow reliable worth license licensed licence
free open-source production ready mature alive abandoned safe official""".split())
_VERSIONED_NAME = re.compile(r"(?<![\w.:/@-])(@?[A-Za-z][\w.-]*(?:/[\w.-]+)?)\s+(?:version\s+)?v?(\d+(?:\.\d+)+[\w.+-]*)")


def _named(text: str) -> bool:
    """Does the text name a subject: a URL, a package reference, or a name that is not just a language / platform
    word ("Python" in "compatible with Python 3.13")?"""
    if _URL.search(text) or _packages(text):
        return True
    if any(m.group(1).lower() not in _NOT_PACKAGES for m in _VERSIONED_NAME.finditer(text)):
        return True
    return any(e.lower() not in _NOT_PACKAGES for e in extract_entities(_URL.sub(" ", text)))


def _topical(text: str) -> list[str]:
    """Topical words that are not aspect, cue, common or number words: what a question is about by itself."""
    skip = _NOT_PACKAGES | _COMMON | _CLAUSE_VERBS | _ASPECT_WORDS
    return [w for w in keywords(text, limit=12).split() if w.lower() not in skip and not re.fullmatch(r"[\d.]+", w)]


def referent_needed(question: str) -> str | None:
    """The phrase of a question that refers to something said before and that the question itself does not name
    ("that version", "it"), else None. It needs one when, without that phrase, the question names no subject and
    no topical word is left ("Is that version secure?", "When was it released?", "What is its license?"); an
    expletive "it" ("Is it true that ...") refers to nothing, and a reference inside a question that names its own
    topic points back within the question ("... the human brain, and what is established about that number?")."""
    question = " ".join(str(question or "").split())
    if not question:
        return None
    m = _DEFINITE_ANAPHOR.search(question) or (None if _EXPLETIVE.search(question) else _PRONOUN.search(question))
    if m is None:
        return None
    rest = _PRONOUN.sub(" ", _DEFINITE_ANAPHOR.sub(" ", question))
    if _named(rest) or _topical(rest):
        return None
    return m.group(0)


_VERSION_NOUNS = frozenset({"version", "release"})


def _subject_of(previous: str, *, versioned: bool = False) -> str | None:
    """What an earlier question was about: its versioned name ("numpy 2.5.5"), else its named entities, else its
    first topical words; None when it names nothing (or itself pointed back without naming). With `versioned`
    ("that version", "this release") only a versioned name will do: "What happened in AI this week?" names no
    version, so "Is that version secure?" after it is not about "AI"."""
    previous = _URL.sub(" ", " ".join(str(previous or "").split()))
    if not previous:
        return None
    m = next((m for m in _VERSIONED_NAME.finditer(previous) if m.group(1).lower() not in
              _COMMON | STOPWORDS | META | {"release", "releases", "version", "versions"}), None)
    if m:
        return f"{m.group(1)} {m.group(2).rstrip('.')}"
    if versioned:
        return None
    entities = [e for e in extract_entities(previous) if e.lower() not in _START_WORDS]
    if entities:
        return " ".join(entities[:2])
    words = keywords(previous, limit=5)
    return words or None


def followup(question: str, previous: str | None = None) -> dict | None:
    """Resolve a question that points back to the previous one of the same conversation (a console or voice
    session; T1-10). None when the question names its own subject. Otherwise either
    {"question": rewritten, "subject", "anaphor", "previous"} — the reference replaced by what `previous` was about
    ("Is that version secure?" after "Is numpy 2.5.5 recommended?" -> "Is numpy 2.5.5 secure?") — or
    {"clarify": text, "anaphor"} when there is no earlier question to take the subject from. Never guesses: a
    subject comes only from `previous`."""
    question = " ".join(str(question or "").split())
    anaphor = referent_needed(question)
    if anaphor is None:
        return None
    noun = _DEFINITE_ANAPHOR.match(anaphor)
    what = noun.group("noun").lower() if noun and noun.group("noun").lower() != "one" else "subject"
    usable = bool(previous) and referent_needed(previous) is None
    subject = _subject_of(previous, versioned=what in _VERSION_NOUNS) if usable else None
    if not subject:
        why = (f"your previous question (“{_cap(' '.join(previous.split()), 80)}”) names no {what}" if usable else
               "there is no earlier question in this conversation to take it from")
        return {"anaphor": anaphor,
                "clarify": f"Which {what} do you mean? “{anaphor}” refers to something said earlier, and {why}. "
                           f"Ask again and name it"
                           + (" (package and version, e.g. “Is numpy 2.5.5 secure?”)." if what in
                              ("version", "release", "package", "library", "module") else ".")}

    def replace(m):
        word = m.group(0).lower()
        return f"{subject}'s" if word in ("its", "their") else subject
    rewritten = _DEFINITE_ANAPHOR.sub(replace, question)
    if rewritten == question:
        rewritten = _PRONOUN.sub(replace, question)
    return {"question": rewritten, "subject": subject, "anaphor": anaphor, "previous": " ".join(previous.split())}


# ---- questions about the individual's own knowledge history (T1-04) ---------------------------------------------
# "What has changed in your understanding of predictive coding?" asks for the stored record (versions,
# supersessions, hypotheses, dates: evolution.changes), not for a new web search.
_HISTORY = re.compile(
    r"^\s*(?:"
    r"what(?:'s| has| have| is)?\s+changed\s+(?:in|about|with)\s+(?:your|its|the individual's|the individual’s)\s+"
    r"(?:understanding|knowledge|view|views|picture|beliefs?|thinking|model)\s+(?:of|about|on|regarding)\s+(?P<a>.+?)"
    r"|how\s+(?:has|have|did)\s+(?:your|its)\s+(?:understanding|knowledge|view|views|picture|beliefs?|thinking)\s+"
    r"(?:of|about|on|regarding)\s+(?P<b>.+?)\s+(?:changed|evolved|developed|shifted)"
    r"|(?:has|have|did)\s+(?:your|its)\s+(?:understanding|knowledge|view|views|beliefs?|thinking)\s+(?:of|about|on|"
    r"regarding)\s+(?P<c>.+?)\s+(?:changed|evolved|shift(?:ed)?|change)"
    r"|what\s+(?:have|did)\s+you\s+(?:learn(?:ed|t)?|revised?|changed?|updated?)\s+(?:about|on|regarding)\s+(?P<d>.+?)"
    r"|(?:show|tell)\s+(?:me\s+)?(?:the\s+)?(?:history|evolution|timeline)\s+of\s+(?:your\s+)?(?:understanding|"
    r"knowledge|beliefs?)\s+(?:of|about|on)\s+(?P<e>.+?)"
    r")(?:\s+(?:since|over|recently|lately)\b.*?)?\s*[?.!]*\s*$", re.I)


def history_topic(question: str) -> str | None:
    """The topic of a question about how the individual's own understanding changed ("What has changed in your
    understanding of predictive coding?" -> "predictive coding"), else None. Such a question is answered from the
    stored history (evolution.changes); a fresh web search is a separate, explicit action."""
    m = _HISTORY.match(" ".join(str(question or "").split()))
    if not m:
        return None
    topic = next(g for g in m.groups() if g)
    topic = re.sub(r"^(?:the|a|an)\s+", "", topic.strip(" ,;:\"'“”"), flags=re.I)
    return topic or None


def plan(question: str, assessment: dict, *, now: float, local_terms=()) -> QueryPlan:
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
        if searchable_text(text):
            items.append((priority, Query(text=text, kind=kind, since=when, site=site, language=lang,
                                          purpose=purpose)))

    advisory_cue = bool(_ADVISORY_CUE.search(question))
    for pkg in assessment.get("packages") or []:
        eco, name, ver = str(pkg.get("ecosystem") or "PyPI"), str(pkg.get("name") or ""), pkg.get("version")
        if not name:
            continue
        inferred = bool(pkg.get("inferred"))
        if inferred and strip_local(name, local_terms)[1]:
            continue          # a plain name that is one of the owner's private terms is local context
        at = f"@{ver}" if ver else ""
        # a plain (inferred) name: both registries are asked; advisories only when a version or an advisory cue
        # makes them answerable
        osv = not inferred or bool(ver) or advisory_cue
        how = " (plain name; a registry that does not know it is a soft miss)" if inferred else ""
        if eco.lower() == "pypi":
            # with the version: the record then says whether that release exists ("Was numpy 2.5.5 released?")
            pinned = f"=={ver}" if ver and _REGISTRY_VERSION.fullmatch(str(ver)) else ""
            add(0, f"pypi:{name}{pinned}", "technical", f"registry: latest release of {name}{how}", when=None)
            if osv:
                add(0, f"osv:PyPI:{name}{at}", "technical", f"advisories for {name}{at}", when=None)
        elif eco.lower() == "npm":
            add(0, f"npm:{name}", "technical", f"registry: latest release of {name}{how}", when=None)
            if osv:
                add(0, f"osv:npm:{name}{at}", "technical", f"advisories for {name}{at}", when=None)
        elif eco.lower() == "github":
            add(0, f"github:{name}", "technical", f"releases of {name}", when=None)
        else:
            add(0, f"osv:{eco}:{name}{at}", "technical", f"advisories for {name}{at}", when=None)

    local = 0

    def strip(text, *, counted=False):
        nonlocal local
        out, n = strip_local(text, local_terms)
        local += n if counted else 0
        return out

    comparison = assessment.get("comparison")
    if comparison:
        sides = [strip(side) for side in comparison]
        comparison = sides if all(keywords(side) for side in sides) else None
        if comparison is None:
            notes.append("comparison with the individual's own system: only the outside side is searched")
    searchable = _URL.sub(" ", question)
    for site in assessment.get("sites") or []:   # a named domain becomes a site: restriction, not a keyword
        searchable = re.sub(rf"(?i)\b(?:site:)?(?:www\.)?{re.escape(site)}\b", " ", searchable)
    searchable = strip(" ".join(searchable.split()), counted=True)   # the claim and sides are parts of it
    dependency_check = bool(assessment.get("dependency_check"))
    clauses = [] if comparison or dependency_check else decompose(searchable)
    topics: list[str] = []
    if comparison:
        a, b = comparison
        context = " ".join(w for w in keywords(searchable).split()
                           if w.lower() not in (a + " " + b).lower().split())[:80]
        add(1, f"{a} vs {b}", general, "comparison: joint")
        add(1, f"{a} {context}", general, f"comparison side A: {a}")
        add(1, f"{b} {context}", general, f"comparison side B: {b}")
        topics.append(f"{a} {b}")
    claim = strip(assessment.get("claim") or "")
    if claim:
        kw = keywords(claim) or claim
        add(1, kw, "web", "claim under verification")
        add(3, f"{kw} fact check", "web", "expansion: verification")
        topics.append(kw)
    subquestions = tuple(clauses) if len(clauses) > 1 else ()
    subject = ""
    for i, clause in enumerate(clauses):
        kw = keywords(clause)
        if subject and (_DEFINITE_ANAPHOR.search(clause) or _PRONOUN.search(clause)):
            # "... and evaluate their implications", "what is established about that number": the clause is about
            # the first clause's subject; searched alone it sends "evaluate implications" or "number" (T1-07)
            own = " ".join(w for w in kw.split() if w.lower() not in _CLAUSE_VERBS)
            kw = keywords(f"{subject} {own}")
            notes.append(f"sub-question {i + 1} refers back to sub-question 1: searched with its subject ({subject})")
        subject = subject or keywords(clause, limit=4)
        label = f"sub-question {i + 1}" if subquestions else "question"
        if not kw:
            if subquestions:
                notes.append(f"sub-question {i + 1} has no searchable topic (instruction only): {_cap(clause, 80)}")
            continue
        topics.append(kw)
        add(1, kw, general, label)
        if "academic" in kinds:
            add(1, keywords(kw, limit=5), "academic", f"{label}: scholarly")
        if "encyclopedia" in kinds:
            # one fact about a named thing: the encyclopedia is asked for the thing itself ("Python", "Rust
            # programming language"), whose lead states such facts; the web query keeps the predicate
            thing = " ".join(w for w in kw.split() if w.lower() not in _FACT_PREDICATE) \
                if assessment.get("simple_fact") else ""
            # nor when it names another sense ("the Rust video game": T1-07, R4)
            if thing and "technical" in kinds and not assessment.get("packages") and \
                    not _SOFTWARE_SENSE & {w.lower() for w in thing.split()} and not sense.question_cue(thing):
                thing = f"{thing} software"
            add(1, thing or kw, "encyclopedia", f"{label}: encyclopedia")
    if dependency_check and not assessment.get("packages"):
        notes.append("dependency check without a package inventory: nothing to look up (the orchestrator supplies "
                     "the packages in use)")
    elif not topics and not assessment.get("packages"):
        entities = _outside(assessment.get("entities") or [], local_terms)
        if entities:
            topics.append(" ".join(entities[:3]))
            add(1, topics[0], general, "entities only")
        else:
            notes.append("no searchable topic in the question: name the claim or supply the sources (URLs)")
    for site in assessment.get("sites") or []:
        for topic in topics[:1]:
            add(2, topic, "web", f"site: user named {site}", site=site)
    broad_news = (general == "news" and since is not None and window <= BROAD_NEWS_MAX_WINDOW and len(topics) == 1
                  and len(topics[0].split()) <= BROAD_NEWS_MAX_WORDS and not comparison and not claim
                  and not assessment.get("packages"))
    if broad_news:
        topic = topics[0]
        spelled = " ".join(_SPELLED_OUT.get(w.lower(), w) for w in topic.split())
        if spelled.lower() != topic.lower():
            add(1, spelled, "news", "broad news: the field spelled out")
        add(2, f"{topic} news", "web", "broad news: general web headlines in the same window")
        notes.append(f"broad current-events question: news for {topic!r} in the last "
                     f"{round(window / DAY)} days; the year expansion is left out (the window already limits it)")
    for topic in topics[:2]:
        if since is not None and str(year) not in topic and not broad_news:
            add(3, f"{topic} {year}", general, "expansion: year for the recency window")
        if "academic" in kinds:
            add(3, f"{keywords(topic, limit=5)} review", "academic", "expansion: reviews")
            add(4, f"{topic} study", "web", "expansion: studies")
        if "technical" in kinds and not (assessment.get("simple_fact") and not assessment.get("packages")):
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
    if local:
        notes.append(f"local context: {local} reference(s) to the individual's own system were not searched; "
                     "they are answered from local documents and state")
    return QueryPlan(queries=tuple(queries), subquestions=subquestions,
                     max_sources=MODE_BUDGETS.get(mode, MODE_BUDGETS["quick_lookup"]).max_pages,
                     notes=tuple(notes))


def _outside(entities, local_terms) -> list[str]:
    """Entities that do not name the individual's own system."""
    return [e for e in entities if strip_local(e, local_terms)[1] == 0]


def refine(plan: QueryPlan, outcome, assessment: dict, *, now: float, local_terms=(),
           remaining: int | None = None) -> QueryPlan | None:
    """One broader round when discovery was thin (< 3 results) or every provider failed; else None.
    Broader = no `since`, no `site`, text shortened to the key entities (or the first three keywords).

    The round never exceeds the query budget: `remaining` is the number of queries the run may still send
    (callers with a live budget pass `budget.max_queries - usage.queries`); by default it is the mode's
    MODE_BUDGETS max_queries minus the queries already planned. None when no query is left."""
    if any(str(n).startswith("refinement") for n in plan.notes):
        return None
    if remaining is None:
        mode = assessment.get("mode") or "quick_lookup"
        remaining = MODE_BUDGETS.get(mode, MODE_BUDGETS["quick_lookup"]).max_queries - len(plan.queries)
    remaining = min(MAX_REFINE_QUERIES, int(remaining))
    if remaining <= 0:
        return None
    attempts = [a for a in outcome.attempts if a.get("provider")]
    all_failed = bool(attempts) and not any(a.get("ok") and a.get("n") for a in attempts)
    n = len(outcome.results)
    if n >= 3 and not all_failed:
        return None
    reason = "every provider failed" if all_failed else f"only {n} result(s)"
    entities = [e for e in _outside(assessment.get("entities") or [], local_terms) if len(e) > 1][:3]
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
        short = " ".join(keywords(strip_local(q.text, local_terms)[0]).split()[:3])
        if short and searchable_text(short) and short.lower() not in (t.lower() for t in texts):
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
    return QueryPlan(queries=tuple(queries[:remaining]), subquestions=plan.subquestions,
                     max_sources=plan.max_sources,
                     notes=(f"refinement: {reason}; dropped date and site limits, shortened to key terms",))


# ---- SearXNG routing (ADR-034) -------------------------------------------------------------------------------------
# One SearXNG category per query kind, never every engine: news/recent -> news, software/package -> it (general web
# as well, because release notes and changelogs live on project sites), scientific -> science (a supplement: the
# structured literature APIs stay direct and primary), everything else -> general. A recency window becomes
# SearXNG's coarse time_range only for general and news: SearXNG silently skips engines without time-range support,
# which would empty the it and science categories.
SEARXNG_CATEGORIES = {"web": "general", "encyclopedia": "general", "news": "news", "technical": "it",
                      "academic": "science"}
_SEARXNG_TIME_RANGES = ((1.5 * DAY, "day"), (8 * DAY, "week"), (32 * DAY, "month"), (366 * DAY, "year"))


def searxng_route(query: Query, *, now: float) -> dict:
    """SearXNG request fields for one planned query: {"categories": str, "time_range": str | None, "why": str}."""
    kind = query.kind if query.kind in SEARXNG_CATEGORIES else "web"
    category = SEARXNG_CATEGORIES[kind]
    why = f"query kind {kind!r} -> {category}"
    if category == "general" and (_TECH.search(str(query.text).lower()) or
                                  re.search(r"release notes|changelog", str(query.purpose).lower())):
        category, why = "general,it", f"{why} + it (software cue)"
    time_range = None
    if query.since is not None and category.split(",")[0] in ("general", "news"):
        window = max(0.0, float(now) - float(query.since))
        time_range = next((label for limit, label in _SEARXNG_TIME_RANGES if window <= limit), None)
        if time_range:
            why += f"; window {round(window / DAY, 1)} days -> time_range={time_range}"
    return {"categories": category, "time_range": time_range, "why": why}


__all__ = ["assess", "plan", "refine", "keywords", "query_text", "searchable_text", "decompose", "extract_entities",
           "detect_language", "strip_local", "dependency_question", "SELF_NAMES",
           "searxng_route", "SEARXNG_CATEGORIES"]
