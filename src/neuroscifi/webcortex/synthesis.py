"""Synthesis: the research report (ARCHITECTURE §5) and the bounded working-memory pack.

There is no language model (ADR-022). Every answer sentence is either
  (a) a claim quoted verbatim from a source (type "quote"), with citations to exactly the sources whose own claim
      restates it (claims.agrees; other cluster members are "related", listed as such, never cited), plus
      "copies" (cited documents) and "independent_sources" (independent groups among them), or
  (b) a template over measured quantities (type "template"): counts, statuses, dates, or a structured API record's
      fields ("PyPI reports numpy latest version 2.3.5 ...").
Nothing asserts a fact that is not in a source. Contradictions are shown with both sides, a plain description and
the basis of any assessment; failures, staleness, cache use, undated and injection-flagged sources are always
surfaced. No report field carries text from an adversarial source or text with an injection finding
(report_safe_text). Among release/package records of one package the newest version (semver) leads; older ones
are marked "superseded". Fact verification checks claim slots, not word overlap (see _verdict).

Constants
---------
MAX_ANSWER_ITEMS        quoted statements in the answer (besides the lead template)
MAX_CONTRADICTIONS      contradiction entries listed in the report
RELATION_WINDOW         words between a question relation word and its entity for "states the relation" (ranking)
LIVE_MODES              retrieval modes that count as "retrieved live during this task"
SUPPORTED_MIN_CONFIDENCE, SUPPORTED_MIN_GROUPS   fact-verification thresholds for "supported": the decisive
                        criteria are >= 2 agreeing independent groups and no contradiction; the confidence floor
                        0.72 admits exactly-two dated secondary sources (posterior 2.6/3.6 = 0.722) and rejects one
                        dated + one undated secondary source (2.4/3.4 = 0.706) and two undated ones (0.688)
STALE_VALUE, UNDATED_VALUE                       freshness multipliers in the answer-ranking value
TOKENS_PER_WORD         token estimate for context packs (1 token ~ 0.75 words)
"""
from __future__ import annotations

import functools
import math
import re
from dataclasses import replace
from datetime import datetime, timezone

from . import claims as _claims
from . import safety, sense, temporal
from . import text as T
from .types import Claim, ClaimCluster, Document

MAX_ANSWER_ITEMS = 5
MAX_CONTRADICTIONS = 20
LIVE_MODES = ("live", "api", "feed", "rendered")
SUPPORTED_MIN_CONFIDENCE = 0.72
SUPPORTED_MIN_GROUPS = 2
STALE_VALUE = 0.5
UNDATED_VALUE = 0.75
TOKENS_PER_WORD = 1.33
UNRESOLVED = "Unresolved: the available evidence does not establish which is correct."

ACADEMIC_DOMAINS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ncbi.nlm.nih.gov", "nih.gov", "pubmed.gov",
                    "doi.org", "nature.com", "science.org", "sciencedirect.com", "cell.com", "springer.com",
                    "wiley.com", "plos.org", "frontiersin.org", "elifesciences.org", "jneurosci.org", "pnas.org",
                    "openalex.org", "crossref.org", "semanticscholar.org", "acm.org", "ieee.org", "mdpi.com",
                    "oup.com", "bmj.com", "thelancet.com", "nejm.org")
PREPRINT_DOMAINS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "preprints.org", "osf.io")
DESIGN_CUES = (
    ("meta-analysis", re.compile(r"\bmeta-?analys[ie]s\b", re.I)),
    ("systematic review", re.compile(r"\bsystematic review\b", re.I)),
    ("review", re.compile(r"\b(?:narrative |scoping )?review(?: article)?\b(?! board)", re.I)),
    ("randomized controlled trial", re.compile(r"\brandomi[sz]ed\b", re.I)),
    ("cohort", re.compile(r"\bcohort\b", re.I)),
    ("case study", re.compile(r"\bcase (?:study|report|series)\b", re.I)),
    ("in vivo", re.compile(r"\bin vivo\b", re.I)),
    ("in vitro", re.compile(r"\bin vitro\b", re.I)),
    ("in silico", re.compile(r"\bin silico\b", re.I)),
    ("simulation", re.compile(r"\bsimulat(?:ion|ions|ed)\b", re.I)),
    ("model", re.compile(r"\b(?:computational|mathematical|biophysical|network|statistical|spiking)\s+models?\b",
                         re.I)),
)
_SAMPLE = re.compile(
    r"\bn\s*=\s*(\d[\d,]*)\b|\b(\d[\d,]*)\s+(participants|patients|subjects|volunteers|neurons|mice|rats|cells|"
    r"slices|animals|monkeys|people|individuals|adults|children|recordings|brains)\b", re.I)


# ---- helpers ------------------------------------------------------------------------------------------------

def _day(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _short(s: str, n: int = 90) -> str:
    s = T.collapse(s)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _span(seconds: float) -> str:
    for size, unit in ((365 * temporal.DAY, "year"), (temporal.DAY, "day"), (temporal.HOUR, "hour"),
                       (60.0, "minute")):
        if seconds >= size:
            n = round(seconds / size, 1)
            n = int(n) if n == int(n) else n
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return f"{int(seconds)} seconds"


def _observed_live(doc: Document) -> bool:
    """A registry-state record (package, advisory, release) fetched live: current as of its retrieval."""
    return _claims.registry_record(doc) and doc.retrieval_mode != "cache"


def _fresh_ts(doc: Document) -> float | None:
    """When the source's content is known to be current: its publication (or last-edited) date; a registry-state
    record (PyPI, npm, OSV, GitHub) fetched live also counts at its retrieval time (registry state as observed).
    Encyclopedia and academic records are dated by their own dates only, however recently they were fetched."""
    ts = doc.published_at
    if _observed_live(doc):
        ts = doc.retrieved_at if ts is None else max(ts, doc.retrieved_at)
    return ts


def _report_flags(flags: dict) -> dict:
    """Source flags as shown in a report: injection findings become {pattern, count} summaries, so no excerpt of
    injected text reaches report consumers (the stored source keeps the full findings for the evidence view)."""
    out = dict(flags)
    if out.get("injection"):
        counts: dict[str, int] = {}
        for f in out["injection"]:
            name = f.get("pattern", "?") if isinstance(f, dict) else "?"
            counts[name] = counts.get(name, 0) + 1
        out["injection"] = [{"pattern": k, "count": n} for k, n in sorted(counts.items())]
    return out


def _source_dates(doc: Document) -> dict:
    """The dates a report source shows besides published_at (T1-05, T1-19): `modified_at` (a revision or
    modification time, labelled as such, never a publication date), a future date set aside
    (`future_published_at`), the `date_note` saying why, and `source_type` "tertiary" for an encyclopedia."""
    md = doc.metadata or {}
    out = {}
    if doc.modified_at is not None:
        out["modified_at"] = doc.modified_at
        if md.get("modified_source"):
            out["modified_source"] = md["modified_source"]
    if md.get("future_published_at") is not None:
        out["future_published_at"] = md["future_published_at"]
        out["date_note"] = md.get("date_note")
    elif doc.published_at is None and doc.modified_at is not None and \
            md.get("modified_source") != "http-last-modified":
        how = {"wikipedia:revision": "the article's latest revision"}.get(md.get("modified_source"),
                                                                           "the page's own modification date")
        out["date_note"] = temporal.MODIFIED_DATE_NOTE.format(day=_day(doc.modified_at), how=how)
    if _claims.encyclopedia(doc):
        out["source_type"] = "tertiary"
    return out


def _claim_fresh_ts(claim: Claim) -> float | None:
    return (claim.flags or {}).get("observed_at") or claim.published_at or claim.event_at


def _representative(c: ClaimCluster) -> Claim | None:
    support = set(c.supporting)
    for x in c.claims:
        if x.text == c.statement and x.id in support:
            return x
    return c.claims[0] if c.claims else None


class _Sources:
    """Numbers sources in order of first citation."""

    def __init__(self, documents: list[Document]):
        self.docs = documents
        self.by_url: dict[str, Document] = {}
        for d in documents:
            for u in (d.canonical_url, d.final_url, d.url):
                if u:
                    self.by_url.setdefault(u, d)
        self.numbers: dict[str, int] = {}
        self.entries: list[dict] = []

    def _key(self, url: str) -> str:
        d = self.by_url.get(url)
        return (d.canonical_url or d.final_url or d.url) if d else url

    def cite(self, url: str, claim: Claim | None = None) -> int:
        n = self.ref(url, claim)
        self.entries[n - 1]["cited"] = True
        return n

    def ref(self, url: str, claim: Claim | None = None) -> int:
        """The source's number without marking it cited (a related source listed next to a finding)."""
        key = self._key(url)
        if key in self.numbers:
            return self.numbers[key]
        n = len(self.entries) + 1
        self.numbers[key] = n
        d = self.by_url.get(url)
        if d is not None:
            entry = {"n": n, "title": d.title, "url": key, "domain": d.domain, "published_at": d.published_at,
                     "retrieved_at": d.retrieved_at, "retrieval_mode": d.retrieval_mode, "kind": d.kind,
                     "flags": _report_flags(d.flags), "cited": False} | _source_dates(d)
        else:
            flags = (claim.flags or {}) if claim else {}
            entry = {"n": n, "title": claim.title if claim else "", "url": key,
                     "domain": claim.domain if claim else "", "published_at": claim.published_at if claim else None,
                     "retrieved_at": claim.retrieved_at if claim else None,
                     "retrieval_mode": flags.get("retrieval_mode", "live"), "kind": flags.get("doc_kind", "page"),
                     "flags": {}, "cited": False}
        self.entries.append(entry)
        return n

    def doc(self, url: str) -> Document | None:
        return self.by_url.get(url)

    def number(self, doc: Document) -> int | None:
        return self.numbers.get(doc.canonical_url or doc.final_url or doc.url)

    def cited_docs(self) -> list[Document]:
        seen, out = set(), []
        for e in self.entries:
            if not e["cited"]:
                continue
            d = self.by_url.get(e["url"])
            if d is not None and id(d) not in seen:
                seen.add(id(d))
                out.append(d)
        return out

    def finish(self) -> list[dict]:
        """Cited sources first (citation order), then sources read but not cited."""
        for d in self.docs:
            key = d.canonical_url or d.final_url or d.url
            if key not in self.numbers:
                self.numbers[key] = len(self.entries) + 1
                self.entries.append({"n": self.numbers[key], "title": d.title, "url": key, "domain": d.domain,
                                     "published_at": d.published_at, "retrieved_at": d.retrieved_at,
                                     "retrieval_mode": d.retrieval_mode, "kind": d.kind,
                                     "flags": _report_flags(d.flags), "cited": False} | _source_dates(d))
        return self.entries


def _supporting(c: ClaimCluster) -> list[Claim]:
    support = set(c.supporting)
    return [x for x in c.claims if x.id in support and _claims.is_reportable(x)]


def _restating(c: ClaimCluster) -> list[Claim]:
    rep = _representative(c)
    return [x for x in _supporting(c) if rep is not None and _claims.restates(rep, x)]


def _cite_cluster(c: ClaimCluster, sources: _Sources) -> list[int]:
    """Only the sources whose own claim restates the representative (claims.restates: the statement itself, a
    near-copy or the same record), so every cited source contains the quoted statement. Sources that support it
    in other words are corroborating (_corroborating), related cluster members are neither."""
    return sorted({sources.cite(x.source_url, x) for x in _restating(c)})


def _corroborating(c: ClaimCluster, sources: _Sources) -> list[int]:
    """Numbers of sources that support the representative without restating it (claims.agrees by an equal value,
    not claims.restates): evidence for its confidence and independent_sources, so the source list marks them
    cited (freshness, liveness and undated-source notes cover all evidence), but never in the item's
    `citations`, which promise the quoted text."""
    restating = _restating(c)
    cited = {sources.ref(x.source_url, x) for x in restating}
    ids = {x.id for x in restating}
    return sorted({sources.cite(x.source_url, x) for x in _supporting(c) if x.id not in ids} - cited)


def _support_counts(c: ClaimCluster) -> tuple[int, int]:
    """(copies, independent_sources): documents that contain the statement (the cited ones), and independent
    groups among all supporting claims, corroborating ones included (near-duplicate copies and one domain count
    once), so a UI can say "3 copies, 1 independent source"."""
    docs = {x.source_url for x in _restating(c)}
    return len(docs), len(_claims.independence_groups(_supporting(c)))


def _related(c: ClaimCluster, sources: _Sources) -> list[int]:
    """Numbers of sources of cluster members that neither support nor contradict the representative."""
    bearing = set(c.supporting) | {i for p in c.contradicting for i in (p.get("a"), p.get("b"))}
    return sorted({sources.ref(x.source_url, x) for x in c.claims
                   if x.id not in bearing and _claims.is_reportable(x)} - set(_cite_numbers(c, sources)))


def _cite_numbers(c: ClaimCluster, sources: _Sources) -> list[int]:
    return [sources.numbers[k] for k in (sources._key(x.source_url) for x in c.claims if x.id in set(c.supporting))
            if k in sources.numbers]


def report_safe_text(claim: Claim | None) -> str | None:
    """The claim's text if it may appear in a report field, else None (adversarial source, injection-like text,
    snippet-only hint). Every producer of report text (answer, findings, contradictions, consolidation.held)
    goes through it."""
    if claim is None or not _claims.is_reportable(claim):
        return None
    return claim.text


def _superseded(ranked: list[tuple[ClaimCluster, float]]) -> set[int]:
    """Indices of release/package record clusters for which a newer version of the same package or repository
    was read (semver order, then date): "latest release" questions answer with the newest one."""
    best: dict[str, tuple] = {}
    keys = {}
    for i, (c, _) in enumerate(ranked):
        rep = _representative(c)
        flags = (rep.flags or {}) if rep else {}
        if rep is None or rep.kind != "metadata" or flags.get("record") not in ("release", "package"):
            continue
        name = str(flags.get("package") or "").lower()
        if not name:
            continue
        rank = (_claims._version_rank(rep), _claims._effective(rep))
        keys[i] = (name, rank)
        if name not in best or rank > best[name]:
            best[name] = rank
    old = {i for i, (name, rank) in keys.items() if rank < best[name]}
    for i, (c, _) in enumerate(ranked):        # a page's statement about an older release of a package read above
        rep = _representative(c)               # ("Corvane 2.5.3 was released on ...") follows the newest one too
        if i in keys or rep is None or rep.kind == "metadata":
            continue
        named = re.search(r"\bv?\d+(?:\.\d+)+", rep.text or "")       # "Corvane 2.5.3 ...": the words before it
        subject = _claims.subject_tokens((rep.flags or {}).get("primary_subject")) or \
            (_claims.subject_tokens(rep.text[:named.start()]) if named else frozenset())
        version = _claims._version_rank(rep)
        newest = [rank for name, rank in best.items() if subject and _claims.subject_tokens(name) <= subject]
        if newest and version and version < newest[0][0]:
            old.add(i)
    return old


def _failure_label(f) -> tuple[str, str]:
    if hasattr(f, "to_json"):
        f = f.to_json()
    if not isinstance(f, dict):
        return str(f)[:80], "failed"
    who = f.get("provider") or ""
    if f.get("engine"):
        who = f"{who}/{f['engine']}" if who else str(f["engine"])
    if not who and f.get("url"):
        m = re.match(r"^[a-z]+://([^/:]+)", str(f["url"]), re.I)
        who = m.group(1) if m else str(f["url"])[:80]
    code = f.get("code") or f.get("error") or "failed"
    return (who or "retrieval"), str(code)[:80]


# ---- mode-specific sections ---------------------------------------------------------------------------------

# ---- fact verification: claim-slot checking -----------------------------------------------------------------
#
# Without a model, a statement is checked slot by slot: it is parsed into subject + relation + object, and each
# extracted claim (one source sentence) is classified against it:
#   SUPPORTS     it names the subject (or starts with a pronoun in a document titled by the subject) and states
#                the same object in the same relation (same place / capital / value within tolerance / date);
#   CONTRADICTS  it names the subject with the same relation and a different object of the same type (another
#                place, value, date or version), or the same relation and object with opposite polarity;
#   IRRELEVANT   anything else. Word overlap alone is never agreement.
# Supported statement shapes (case-insensitive, an optional "is it true that" prefix and "not"/"never" allowed):
#   location   "X is in Y", "X is located/situated/based in Y" (Y capitalised: a place)
#   capital    "X is the capital of Y" (contradicted by "Z is the capital of Y" with Z != X)
#   quantity   "X has/contains/is/measures ... N <unit>" (numbers compare within 1 %, 10 % when either side says
#              about/approximately/around/roughly/nearly)
#   date       "X was released/launched/published/founded/built/completed/born in/on DATE" (year or day)
#   is         "X is Y" (every name in Y and >= 60 % of its other words after X; with numbers or versions, X's
#              whole phrase must appear)
#   event      "X <verb> ... [on DATE / N <unit>]" (same verb stem, Y's words as for "is", equal dates/values)
# When X is a name, the source sentence must be *about* X (its primary subject, claims.primary_subject), not
# mention it in passing ("a steel model of the Corran Spire in Tessaly"); a number in X ("Quillstream 4.2.0")
# must not differ in the sentence (another release is another subject).
# A document titled "X (Qualifier)" whose qualifier names something the statement does not ("Eiffel Tower
# (Paris, Texas)") is about another X and is irrelevant. Statements of any other shape get "insufficient" with
# no sources counted.

_FV_PREFIX = re.compile(
    r"^\s*(?:(?:is|was)\s+it\s+(?:really\s+)?(?:true|correct|accurate|the\s+case|a\s+fact)\s+that|is\s+it\s+true:?|"
    r"(?:fact[- ]?check|verify|check)(?:\s+(?:whether|if|that))?:?|true\s+or\s+false:?|did\s+you\s+know\s+that)\s+",
    re.IGNORECASE)
_NEG = r"(?:(?:not|never|no\s+longer)\s+)?"
_SHAPES = (
    ("capital", re.compile(rf"^(?P<s>.+?)\s+(?:is|was|has\s+been)\s+{_NEG}the\s+capital(?:\s+city)?\s+of\s+(?P<o>.+)$",
                           re.IGNORECASE)),
    ("location", re.compile(rf"^(?P<s>.+?)\s+(?:(?:is|are|was|were|lies|lie|sits|stands|remains)\s+{_NEG}"
                            rf"(?:(?:located|situated|found|based|placed)\s+)?|(?:located|situated|based)\s+)"
                            rf"(?:in|on|at|near)\s+(?P<o>[A-Z].*)$")),
    ("date", re.compile(rf"^(?P<s>.+?)\s+(?:was|were|is|got)\s+{_NEG}(?:first\s+|initially\s+|officially\s+)?"
                        r"(?P<v>released|launched|published|introduced|announced|founded|built|completed|opened|"
                        r"created|established|discovered|invented|born)\s+(?:in|on)\s+(?P<o>.+)$", re.IGNORECASE)),
    ("quantity", re.compile(rf"^(?P<s>.+?)\s+(?:has|have|had|contains?|contained|is|are|was|were|measures?|measured|"
                            rf"weighs?|weighed|spans?|holds?|employs?|counts?|totals?)\s+{_NEG}(?P<o>.*\d.*)$",
                            re.IGNORECASE)),
    ("is", re.compile(rf"^(?P<s>.+?)\s+(?:is|are|was|were)\s+{_NEG}(?P<o>.+)$", re.IGNORECASE)),
)
_RELEASE_CUE = re.compile(r"\b(?:released|release|launched|launch|published|introduced|announced|founded|built|"
                          r"completed|opened|created|established|discovered|invented|born|came\s+out|debuted|"
                          r"first\s+appeared|unveiled|shipped)\b", re.IGNORECASE)
_APPROX = re.compile(r"\b(?:about|approximately|approx\.?|around|roughly|nearly|almost|some|an\s+estimated|"
                     r"estimated|circa|ca\.)\s|~", re.IGNORECASE)
_PLACE = re.compile(
    r"\b(?:in|at|on|near|inside|within)\s+(?:the\s+)?(?:(?:city|town|village|state|country|centre|center|heart|"
    r"outskirts|suburbs?|region|province|county|district|area|commune|municipality)\s+of\s+)?"
    r"(?:(?:central|downtown|northern|southern|eastern|western|north|south|east|west)\s+)?"
    r"(?P<p>[A-Z][\w'’.-]*(?:\s+(?:(?:de|du|des|la|le|of|von|van|der|del|da|di|y|sur|am|upon)\s+)?[A-Z][\w'’.-]*)*"
    r"(?:\s*,\s*[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)*)")
_CAPITAL_OF = re.compile(
    r"(?P<z>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)\s+(?:is|was|has\s+been|became|remains)\s+(?:the\s+)?"
    r"(?:national\s+)?capital(?:\s+city)?\s+of\s+(?:the\s+)?(?P<o>[A-Z][\w'’.-]*(?:\s+(?:of\s+)?[A-Z][\w'’.-]*)*)"
    r"|capital(?:\s+city)?\s+of\s+(?:the\s+)?(?P<o2>[A-Z][\w'’.-]*(?:\s+(?:of\s+)?[A-Z][\w'’.-]*)*)\s*,?\s+"
    r"(?:is|was)\s+(?P<z2>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)")
_DETERMINERS = frozenset("the a an this that these those its their his her".split())
_WORDS = re.compile(r"[^\W_][\w'’.-]*")


def _stems(text: str) -> list[str]:
    return [T.stem(t) for t in T.content_tokens(text)]


def _phrase(text: str) -> frozenset:
    return frozenset(T.stem(t) for t in T.tokens(text) if t not in T.STOPWORDS and t not in _DETERMINERS)


_WH_QUESTION = re.compile(r"^\W*(?:how|what|what's|whats|which|who|whom|whose|when|where|why)\b", re.IGNORECASE)
_YES_NO = re.compile(r"^(?P<aux>is|are|was|were|does|do|did|has|have|had)\s+(?P<rest>\S.*)$", re.IGNORECASE)
_BE_PREDICATE = re.compile(
    r"\s(?=(?:not\s+|never\s+)?(?:in|on|at|near|inside|within|located|situated|based|found|the\s+capital|a|an|"
    r"(?:first\s+|initially\s+|officially\s+)?(?-i:[a-z]+ed|built|born|made|known|held|run|shown|written)|"
    r"about|approximately|around|roughly|nearly|over|under|more\s+than|less\s+than)\b)", re.IGNORECASE)
_NUMBER_PREDICATE = re.compile(r"\s(?=[\d~≈])")
_HAVE_PREDICATE = re.compile(r"\s(?=(?:not\s+|never\s+)?(?:been|(?-i:[a-z]+ed|built|made|known|held|shown|grown|"
                             r"risen|fallen|won|lost))\b)", re.IGNORECASE)
_DO_VERBS = frozenset("""have contain support use include require run reach need weigh measure cost hold employ span
exist work ship list offer provide allow affect cause increase reduce release launch count total mean make take win
lose own sell produce orbit flow drain border host join speak live occur happen change cover cross connect serve
carry follow rank remain stand sit lie""".split())


def _third_person(verb: str) -> str:
    if verb == "have":
        return "has"
    if verb.endswith(("s", "sh", "ch", "x", "z", "o")):
        return verb + "es"
    if verb.endswith("y") and len(verb) > 2 and verb[-2] not in "aeiou":
        return verb[:-1] + "ies"
    return verb + "s"


# Package release statements (RT4-03). A version question is about one package's record, not a sentence shape:
# "Is numpy 2.5.5 the latest (stable) release?" -> "The latest release of numpy is 2.5.5" and "Was numpy 2.5.5
# released?" / "Was numpy==2.5.5 released?" -> "numpy 2.5.5 was released". release_statement() reads either form
# (and the declarative "numpy 2.5.5 is the latest release") back as {name, version, kind, polarity}; the verdict
# checks it against the registry record of that package (_record_check), not only against quoted sentences.
_PKG = r"(?P<x>@?[A-Za-z0-9][\w.-]*?(?:/[\w.-]+)?)"
_VER = r"v?(?P<v>\d+(?:\.\d+)+[\w.+-]*)"
_LATEST_WORDS = r"(?:the\s+)?(?:latest|newest|current|most\s+recent)\s+(?:stable\s+)?(?:release|version)"
_YES_NO_LATEST = re.compile(rf"^(?P<aux>is|was)\s+{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?P<neg>not\s+)?"
                            rf"{_LATEST_WORDS}$", re.IGNORECASE)
_YES_NO_RELEASED = re.compile(rf"^(?P<aux>was|has|is)\s+{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?P<neg>not\s+)?"
                              rf"(?:been\s+)?(?:released|published)$", re.IGNORECASE)
# What a registry record can and cannot establish about one version (ADR-033 as amended, CLAIM_VERIFICATION.md §5):
# "published" and "latest" are settled by the record itself; "stable" only by the record's pre-release marker
# and yank flag; "compatible" can only be refuted by declared metadata (requires_python); "secure" can only be
# refuted, by an OSV advisory (publication never implies it); "recommended" is a judgement: not evaluated.
_ASPECTS = (
    ("stable", r"(?:an?\s+)?(?:stable|final|production[- ]ready)(?:\s+(?:release|version))?"),
    ("compatible", r"compatible\s+with\s+(?P<target>.+)"),
    ("secure", r"(?:secure|safe(?:\s+to\s+use)?|free\s+(?:of|from)\s+(?:known\s+)?vulnerabilities|"
               r"(?P<vuln>vulnerable|insecure)(?:\s+to\s+.+)?)"),
    ("recommended", r"(?:recommended|advisable|a\s+recommended\s+(?:release|version|upgrade))(?:\s+for\s+.+)?"),
)
REGISTRY_ASPECTS = ("published", "latest") + tuple(a for a, _ in _ASPECTS)
_YES_NO_ASPECT = re.compile(rf"^(?P<aux>is|was)\s+{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?P<neg>not\s+)?"
                            rf"(?P<rest>(?:{'|'.join(rx for _, rx in _ASPECTS).replace('(?P<target>', '(?:')
                                             .replace('(?P<vuln>', '(?:')}))$", re.IGNORECASE)
_RELEASE_STATEMENTS = tuple(
    (aspect, re.compile(rf"^{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?:is|was)\s+(?P<neg>not\s+)?(?:{rx})$",
                        re.IGNORECASE)) for aspect, rx in _ASPECTS) + (
    ("latest", re.compile(rf"^{_LATEST_WORDS}\s+of\s+{_PKG}\s+(?:is|was)\s+(?P<neg>not\s+)?(?:version\s+)?{_VER}$",
                          re.IGNORECASE)),
    ("latest", re.compile(rf"^{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?:is|was)\s+(?P<neg>not\s+)?{_LATEST_WORDS}"
                          rf"$", re.IGNORECASE)),
    ("released", re.compile(rf"^{_PKG}(?:\s+|==|\s+version\s+){_VER}\s+(?:was|has\s+been|is)\s+"
                            rf"(?P<neg>not\s+|never\s+)?(?:released|published)$", re.IGNORECASE)),
)


def release_statement(statement: str) -> dict | None:
    """{name, version, kind, polarity[, target]} of a package release statement, or None. kind: "latest",
    "released" (published), "stable", "compatible" (target: what it is compatible with), "secure" ("vulnerable"
    and "insecure" read as "secure" with the opposite polarity) or "recommended"."""
    s = _FV_PREFIX.sub("", T.collapse(statement or "")).strip().rstrip(" ?.!")
    for kind, rx in _RELEASE_STATEMENTS:
        m = rx.match(s)
        if m:
            pol = -1 if m.group("neg") else 1
            groups = m.groupdict()
            if groups.get("vuln"):
                pol = -pol
            out = {"name": m.group("x"), "version": m.group("v").rstrip("."), "kind": kind, "polarity": pol}
            if groups.get("target"):
                out["target"] = groups["target"].strip()
            return out
    return None


def checkable_statement(text: str) -> str | None:
    """The statement a fact check can verify, or None (RT-B-06): a declarative statement as given (an optional "is
    it true that" prefix removed), or a yes/no question turned into one ("Was X released?" -> "X was released";
    "Is X in Y?" -> "X is in Y"; "Does X have N ...?" -> "X has N ..."; "Did X reach Y?" -> "X did reach Y").
    A wh-question ("How long is X?") states nothing to check; neither does text whose shape parse_statement does
    not support. Without a checkable statement there is no verdict: the answer and contradictions carry the
    result, and a verdict can never contradict the answer it would summarise."""
    raw = T.collapse(re.sub(r"\bhttps?://\S+", " ", text or ""))
    s = _FV_PREFIX.sub("", raw).strip()
    question = s.endswith("?") and s == raw.strip()     # "Is it true that X is Y?" states X is Y
    s = s.rstrip(" ?.!")
    if not s or _WH_QUESTION.match(s):
        return None
    m = _YES_NO_LATEST.match(s) if question else None
    if m:
        neg = "not " if m.group("neg") else ""
        return f"The latest release of {m.group('x')} {m.group('aux').lower()} {neg}{m.group('v')}"
    m = _YES_NO_RELEASED.match(s) if question else None
    if m:
        verb = "has been" if m.group("aux").lower() == "has" else "was"
        return f"{m.group('x')} {m.group('v')} {verb} {'not ' if m.group('neg') else ''}released"
    m = _YES_NO_ASPECT.match(s) if question else None
    if m:                    # "Is corvane 2.5.5 stable?" -> "corvane 2.5.5 is stable" (a registry aspect)
        return f"{m.group('x')} {m.group('v')} {m.group('aux').lower()} {'not ' if m.group('neg') else ''}" \
               f"{m.group('rest')}"
    m = _YES_NO.match(s)
    if m and (question or not parse_statement(s)):
        aux, rest = m.group("aux").lower(), m.group("rest")
        words = list(re.finditer(r"\S+", rest))
        if len(words) < 2:
            return None
        tail = rest[words[1].start() - 1:]
        if aux in ("is", "are", "was", "were"):
            cut = _BE_PREDICATE.search(tail) or _NUMBER_PREDICATE.search(tail)
            if cut is None:          # "Is Orbix free?": the last word is the predicate
                cut = re.search(r"\s(?=\S+$)", tail)
            at = words[1].start() - 1 + cut.start()
            s = f"{rest[:at].strip()} {aux} {rest[at:].strip()}"
        elif aux in ("has", "have", "had"):
            cut = _HAVE_PREDICATE.search(tail)
            if cut is None:
                return None
            at = words[1].start() - 1 + cut.start()
            s = f"{rest[:at].strip()} {aux} {rest[at:].strip()}"
        else:
            verb = next((w for w in words[1:] if w.group(0).lower() in _DO_VERBS), None)
            if verb is None:
                return None
            base = verb.group(0).lower()
            form = f"did {base}" if aux == "did" else (_third_person(base) if aux == "does" else base)
            s = f"{rest[:verb.start()].strip()} {form} {rest[verb.end():].strip()}".strip()
    elif question:
        return None
    elif _FV_PREFIX.sub("", raw).strip() == raw.strip():
        s = text.strip()                    # already a statement: checked (and shown) as given
    return s if parse_statement(s) is not None else None


_asserted = _claims.asserted_text      # what a sentence asserts, without reported myths or beliefs (C-09)


def parse_statement(statement: str) -> dict | None:
    """{shape, subject, head, phrase, object, object_tokens, values, verb, polarity} of a checkable statement
    (see the section comment), or None for an unsupported shape."""
    s = _FV_PREFIX.sub("", T.collapse(re.sub(r"\bhttps?://\S+", " ", statement or ""))).rstrip(" ?.!")
    if not s:
        return None
    pol = _claims.polarity(s)
    for shape, rx in _SHAPES:
        m = rx.match(s)
        if m:
            subject, obj = m.group("s").strip(" ,"), m.group("o").strip(" ,")
            verb = T.stem(m.group("v").lower()) if "v" in rx.groupindex else None
            return _slot(shape, subject, obj, pol, verb)
    words = [(w.group(0), w.start()) for w in _WORDS.finditer(s)]
    verb_at = next((i for i, (w, _) in enumerate(words) if i > 0 and
                    (w.lower() in _claims._AUX or _claims._VERB_CUE.match(w))), None)
    if verb_at is None:
        verb_at = next((i for i, (w, _) in enumerate(words) if i > 0 and re.fullmatch(r"[a-z]{3,}e?s", w)), None)
    if verb_at is None:
        return None
    i = verb_at
    while i < len(words) and (words[i][0].lower() in _claims._AUX or words[i][0].lower() in ("not", "never")) \
            and i + 1 < len(words):
        i += 1
    verb = words[i][0]
    subject = s[:words[verb_at][1]].strip(" ,")
    obj = s[words[i][1] + len(verb):].strip(" ,")
    if not subject:
        return None
    return _slot("event", subject, obj, pol, T.stem(verb.lower()))


def _slot(shape: str, subject: str, obj: str, polarity: int, verb: str | None) -> dict | None:
    entity = _claims.primary_subject(subject + " is", "") or _claims.primary_subject(subject, "")
    head = _claims.subject_tokens(entity) if entity else _phrase(subject)
    if not head:
        return None
    values = T.values(obj)
    if shape == "location" and (not re.search(r"[A-Z]", obj) or obj.split()[0].lower().rstrip(".,") in
                                _claims._CALENDAR):
        return None
    if shape in ("quantity", "date") and not values:
        return None
    return {"shape": shape, "subject": subject, "head": head, "entity": bool(entity), "phrase": _phrase(subject),
            "subject_values": T.values(subject), "object": obj,
            "object_tokens": _phrase(re.sub(r"[\d.,%]+", " ", obj)),
            "object_names": _claims.subject_tokens(" ".join(re.findall(r"\b[A-Z][\w'’-]*", obj))),
            "values": values, "verb": verb, "polarity": polarity}


def _mention(slot: dict, claim: Claim) -> int | None:
    """Character offset where `claim` names the slot's subject (0 for a pronoun-led sentence in a document
    titled by the subject), or None. A title qualifier the statement does not share makes it another subject."""
    title = _claims.title_subject(claim.title or "") or ""
    m = re.match(r"^(?P<base>.*?)\s*\((?P<qual>[^()]*)\)$", title)
    if m:
        base, qual = _claims.subject_tokens(m.group("base")), _claims.subject_tokens(m.group("qual"))
        if slot["head"] <= base and not qual <= (slot["phrase"] | slot["object_tokens"] | slot["head"]):
            return None
    words = [(T.stem(w.group(0).lower().replace(",", "")), w.start())
             for w in T._TOKEN_RE.finditer(claim.text or "")]
    have = {w for w, _ in words}
    if slot["head"] <= have:
        return next(pos for w, pos in words if w in slot["head"])
    alias = _claims.subject_tokens(title)
    if _claims.title_reference(claim.text or "") is not None and alias and _claims.same_subject(alias, slot["head"]):
        return 0
    return None


# ---- location statements: subject-attached places and containment (RT-W-02) --------------------------------
#
# A place bears on "X is in Y" only when the sentence attaches it to X itself, in the same clause:
#   locative   X [, apposition,] (is|was|stands|sits|lies|remains|located|situated|based|built ...) ... in/on/at P
#              (up to the end of X's clause: a subordinate clause "until the Chrysler Building in New York City was
#              finished" belongs to another subject), "P's X" and "X in P ...";
#   scope      the same slot after a superlative or ranking ("the second tallest structure in France"): it confirms
#              P but never contradicts another place (a ranking's scope is not an address).
# A place after another verb form ("is depicted in Hitchcock's films") is not where X is. Without a gazetteer,
# containment comes from the source document itself (_nests): "Paris, France", "on the Champ de Mars in Paris",
# "Paris is the capital of France", "France's capital Paris". A place the document nests inside Y supports
# "X is in Y"; one that contains Y ("in France" for Paris) is compatible (neither); any other locative place is a
# different place and contradicts.

_PARENS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_LOCATIVE_PARTICIPLES = ("located|situated|found|based|built|erected|constructed|placed|sited|housed|"
                         "headquartered|set|standing|sitting|lying|perched|nestled")
_PREDICATE = re.compile(
    r"^\s*(?:,[^,;]{1,120},\s*)?(?:(?:is|are|was|were|has\s+been|had\s+been|remains|remained|stands|stood|sits|sat|"
    r"lies|lay|rises|rose)\b(?:\s+(?:not|never|no\s+longer))?(?:\s+(?:" + _LOCATIVE_PARTICIPLES + r")\b)?"
    r"|(?:" + _LOCATIVE_PARTICIPLES + r")\b)", re.IGNORECASE)
_CLAUSE_END = re.compile(
    r"[;:!?]|\.(?:\s|$)|\s(?:until|when|whenever|while|after|before|since|because|although|though|whereas|unless|"
    r"where|which|who|whom|whose|that|but|if|than)\b|,\s*(?:and|or)\s", re.IGNORECASE)
_OTHER_VERB = re.compile(
    r"\b(?!(?:" + _LOCATIVE_PARTICIPLES + r")\b)(?:[a-z]{3,}ed|shown|seen|known|made|held|drawn|written|sung|told|"
    r"given|taken|sold|won|lost|named|called|featured|used)\b")
_SUPERLATIVE = re.compile(
    r"\b(?:most|least|first|second|third|only|next|(?!(?:interest|forest|honest|modest|request|guest|harvest|protest|"
    r"contest|manifest|suggest|invest|arrest|digest|conquest|earnest|inquest|behest|everest|budapest|bucharest|"
    r"southwest|northwest|midwest|southeast|northeast)\b)[a-z]{3,}est)\b", re.IGNORECASE)
_PLACE_START = re.compile(r"\s*(?=(?:in|at|on|near)\s+(?:the\s+)?[A-Z])")
_POSSESSOR_PLACE = re.compile(r"(?P<p>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)['’]s?\s+$")
_CAPITAL_POSSESSIVE = re.compile(
    r"(?P<o>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)['’]s\s+capital(?:\s+city)?,?\s+(?:is\s+)?"
    r"(?P<z>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)")


def _place_parts(place: str) -> list[frozenset]:
    return [t for t in (_claims.subject_tokens(p) for p in place.rstrip(".,;:").split(",")) if t]


def _nests(text: str) -> set:
    """(inner, outer) place token sets that `text` nests: "Paris, France" (and longer chains), adjacent place
    phrases ("on the Champ de Mars in Paris"), "Paris is the capital of France", "France's capital Paris"."""
    text = text or ""
    out = set()
    matches = list(_PLACE.finditer(text))
    for m in matches:
        parts = _place_parts(m.group("p"))
        out |= set(zip(parts, parts[1:]))
    for m1, m2 in zip(matches, matches[1:]):
        if m1.end() <= m2.start() and not text[m1.end():m2.start()].strip():
            a, b = _place_parts(m1.group("p")), _place_parts(m2.group("p"))
            if a and b:
                out.add((a[-1], b[0]))
    for m in _CAPITAL_OF.finditer(text):
        z, o = _claims.subject_tokens(m.group("z") or m.group("z2")), _claims.subject_tokens(m.group("o") or m.group("o2"))
        if z and o:
            out.add((z, o))
    for m in _CAPITAL_POSSESSIVE.finditer(text):
        z, o = _claims.subject_tokens(m.group("z")), _claims.subject_tokens(m.group("o"))
        if z and o:
            out.add((z, o))
    return out


def _within(inner: frozenset, outer: frozenset, nests: set) -> bool:
    """`inner` lies inside `outer` by the document's own nestings (transitively)."""
    frontier, seen = [inner], {inner}
    while frontier:
        x = frontier.pop()
        for i, o in nests:
            if _claims.same_subject(i, x) and o not in seen:
                if _claims.same_subject(o, outer):
                    return True
                seen.add(o)
                frontier.append(o)
    return False


def _subject_span(slot: dict, text: str, at: int) -> tuple[int, int]:
    """(start, end) of the subject's mention: the first run of consecutive words covering the subject's head
    tokens; a pronoun-led sentence about the document's subject: the pronoun."""
    end = _claims.title_reference(text) if at == 0 else None
    if end is not None:
        return 0, end
    words = list(T._TOKEN_RE.finditer(text))
    stems = [T.stem(w.group(0).lower().replace(",", "")) for w in words]
    for i in range(len(words)):
        if stems[i] not in slot["head"]:
            continue
        j, have = i, set()
        while j < len(words) and (stems[j] in slot["head"] or words[j].group(0).lower() in _claims._CONNECTORS) \
                and (j == i or not text[words[j - 1].end():words[j].start()].strip()):
            have.add(stems[j])
            j += 1
        if slot["head"] <= have:
            return words[i].start(), words[j - 1].end()
    w = next((w for w in words if w.start() >= at), None)
    return at, w.end() if w else at


def _attached_places(slot: dict, text: str, at: int) -> list[tuple[str, frozenset, frozenset, bool]]:
    """[(place, most specific part's tokens, all tokens, locative)] attached to the subject (section comment)."""
    start, end = _subject_span(slot, text, at)
    if re.search(r"\b(?:of|from|like)\s+(?:the\s+|a\s+|an\s+)?$", text[:start], re.IGNORECASE):
        return []                           # "a replica of the Eiffel Tower ...": the sentence is about the replica
    found = []

    def add(place, locative):
        parts = _place_parts(place)
        toks = frozenset().union(*parts) if parts else frozenset()
        if parts and not toks <= slot["head"] and not toks & _claims._CALENDAR:
            found.append((place.rstrip(".,;:"), parts[0], toks, locative))

    m = _POSSESSOR_PLACE.search(text[:start])
    if m:
        add(m.group("p"), True)
    rest = _PARENS.sub(" ", text[end:])
    if _PLACE_START.match(rest):            # "X in P ..."
        m = _PLACE.match(rest.lstrip())
        if m:
            add(m.group("p"), True)
            rest = rest.lstrip()[m.end():]
    verb = _PREDICATE.match(rest)
    if not verb:
        return found
    cut = _CLAUSE_END.search(rest, verb.end())
    predicate = rest[:cut.start() if cut else len(rest)]
    scope = False
    for m in _PLACE.finditer(predicate, verb.end()):
        gap = predicate[verb.end():m.start()]
        if _OTHER_VERB.search(gap):
            break                           # "is depicted in ...": another predicate's place
        scope = scope or bool(_SUPERLATIVE.search(gap))
        add(m.group("p"), not scope)
    return found


def _location(slot: dict, claim: Claim, nests: set) -> tuple[str | None, str]:
    """("support" | "contradict" | None, why) of one claim for "X is in Y" taken as asserted (the caller flips it
    for a negated statement); see the section comment."""
    text = claim.text or ""
    at = _mention(slot, claim)
    if at is None:
        return None, ""
    places = _attached_places(slot, text, at)
    if not places:
        return None, ""
    parts = _place_parts(slot["object"])
    want = parts[0] if parts else slot["object_tokens"]
    outer = frozenset().union(*parts[1:]) if len(parts) > 1 else frozenset()
    same, other = [], []
    for place, specific, toks, locative in places:
        if want <= toks or _within(specific, want, nests):
            same.append(place)
        elif _within(want, specific, nests) or (outer and specific <= outer):
            continue                        # a broader place that contains Y: compatible, not evidence
        elif locative:
            other.append(place)
    if claim.polarity < 0:
        return ("contradict", "denies the place") if same else (None, "")
    if same:
        return "support", ""
    if other:
        return "contradict", f"places it in {' and '.join(dict.fromkeys(other))}, not {slot['object']}"
    return None, ""



def _compare_values(want: list[str], have: list[str], approx: bool) -> str | None:
    """"same" when a value of `have` equals one of `want`, "different" when values of the same kind exist and none
    is equal, None when nothing is comparable."""
    comparable = False
    for v in want:
        for w in have:
            eq = T.values_equal(v, w, rel_tol=0.1 if approx else None)
            if eq is None:
                continue
            comparable = True
            if eq:
                return "same"
    return "different" if comparable else None


def _check(slot: dict, claim: Claim, nests: set | None = None) -> tuple[str | None, str]:
    """("support" | "contradict" | None, why) for one claim against the parsed statement. `nests`: the place
    containments its document states (_nests), for location statements."""
    text = claim.text or ""
    shape = slot["shape"]
    same_polarity = claim.polarity == slot["polarity"]
    flip = {"support": "contradict", "contradict": "support"}
    if shape == "capital":                   # functional: another city named capital of Y contradicts
        for m in _CAPITAL_OF.finditer(text):
            z, o = m.group("z") or m.group("z2"), m.group("o") or m.group("o2")
            if not slot["object_tokens"] <= _claims.subject_tokens(o):
                continue
            rel = "support" if _claims.same_subject(_claims.subject_tokens(z), slot["head"]) else "contradict"
            if claim.polarity < 0:
                if rel == "contradict":
                    continue
                rel = "contradict"
            why = "" if rel == "support" else f"names {z} as the capital of {o}"
            return (rel if slot["polarity"] > 0 else flip[rel]), why
        return None, ""
    at = _mention(slot, claim)
    if at is None:
        return None, ""
    if slot["entity"]:                      # the sentence must be about the subject, not mention it in passing
        own = _claims.subject_tokens((claim.flags or {}).get("primary_subject") if "primary_subject" in
                                     (claim.flags or {}) else _claims.primary_subject(text, claim.title or ""))
        if own and not _claims.same_subject(own, slot["head"]):
            return None, ""
    have = list(claim.values or [])
    if slot["subject_values"]:              # "Quillstream 4.2.0": another version of it is another subject
        same = [v for v in have if any(T.values_equal(v, w) for w in slot["subject_values"])]
        other = [v for v in have if v not in same and any(T.values_equal(v, w) is not None and
                                                          T.value_kind(v)[0] == T.value_kind(w)[0]
                                                          for w in slot["subject_values"])]
        if other and not same:
            return None, ""
        have = [v for v in have if v not in same]
    after = text[at:]
    stems = set(_stems(text))
    if shape == "location":
        rel, why = _location(slot, claim, nests or set())
        if rel is None:
            return None, ""
        return (rel if slot["polarity"] > 0 else flip[rel]), why
    text = claim.text or ""
    shape = slot["shape"]
    same_polarity = claim.polarity == slot["polarity"]
    flip = {"support": "contradict", "contradict": "support"}
    if shape == "capital":                   # functional: another city named capital of Y contradicts
        for m in _CAPITAL_OF.finditer(text):
            z, o = m.group("z") or m.group("z2"), m.group("o") or m.group("o2")
            if not slot["object_tokens"] <= _claims.subject_tokens(o):
                continue
            rel = "support" if _claims.same_subject(_claims.subject_tokens(z), slot["head"]) else "contradict"
            if claim.polarity < 0:
                if rel == "contradict":
                    continue
                rel = "contradict"
            why = "" if rel == "support" else f"names {z} as the capital of {o}"
            return (rel if slot["polarity"] > 0 else flip[rel]), why
        return None, ""
    at = _mention(slot, claim)
    if at is None:
        return None, ""
    after = text[at:]
    stems = set(_stems(text))
    asserted = _asserted(text)
    if asserted != text:                    # a myth or old figure the sentence reports is not what it asserts (C-09)
        have = [v for v in T.values(asserted) if not any(T.values_equal(v, w) for w in slot["subject_values"])]
        same_polarity = _claims.polarity(asserted) == slot["polarity"]
    want = slot["values"]
    if shape == "quantity" or (shape in ("is", "event") and want):
        if shape != "event" and not slot["phrase"] <= stems | slot["head"]:
            return None, ""
        if shape == "event" and slot["verb"] not in stems:
            return None, ""
        found = _compare_values([v for v in want if T.value_kind(v)[0] not in ("date", "year")] or want, have,
                                bool(_APPROX.search(text) or _APPROX.search(slot["object"])))
        if found is None:
            return None, ""
        if not same_polarity and shape != "event":
            # the negation denies the statement's own figure only when that figure is in its scope ("does not
            # contain 100 billion neurons"); a negation elsewhere in the sentence has an unknown scope (T1-16)
            cue = _claims._NEGATION.search(asserted)
            denied = T.values(asserted[cue.end():]) if cue else []
            if found == "same" and slot["polarity"] > 0 and _compare_values(want, denied, False) == "same":
                return "contradict", f"denies {', '.join(want)}"
            return None, ""
        if found == "different":
            return ("contradict" if slot["polarity"] > 0 else None), \
                f"reports {', '.join(have)} instead of {', '.join(want)}"
        return ("support" if same_polarity else "contradict"), "" if same_polarity else "opposite polarity"
    if shape == "date":
        if not _RELEASE_CUE.search(text):
            return None, ""
        found = _compare_values(want, [v for v in have if T.value_kind(v)[0] in ("date", "year", "version")], False)
        if found is None or not same_polarity:
            return None, ""
        if found == "different":
            return ("contradict" if slot["polarity"] > 0 else None), \
                f"dates it {', '.join(have)}, not {', '.join(want)}"
        return ("support" if same_polarity else "contradict"), "" if same_polarity else "opposite polarity"
    # "is" without values, or an event without values: the object's words after the subject, same verb
    if shape == "event" and slot["verb"] not in stems:
        return None, ""
    obj = slot["object_tokens"]
    if not obj:
        return None, ""
    after_stems = {T.stem(t) for t in T.tokens(after)}
    if not slot["object_names"] <= after_stems or len(obj & after_stems) < 0.6 * len(obj):
        return None, ""             # every name in the object, and most of its other words
    return ("support" if same_polarity else "contradict"), "" if same_polarity else \
        f"opposite polarity ('{_claims.negation_cue(text) or _claims.negation_cue(slot['subject']) or 'not'}')"


def _past_event(slot: dict, now: float) -> bool:
    """The statement dates a completed event ("X was released on 2 October 2026", "X opened in 1987"): a date
    shape, or an event whose values are all dates or years, every one before `now` (a year: before `now`'s year).
    Whether it happened then does not change as its sources age, so the age discount of the question's freshness
    class (claims.AGE_DISCOUNT_CLASSES: "is it still true?") does not apply; undated sources keep their discount."""
    values = slot["values"]
    if not values or not (slot["shape"] == "date" or slot["shape"] == "event"):
        return False
    year = datetime.fromtimestamp(now, timezone.utc).year
    for v in values:
        kind, _, parsed = T.value_kind(v)
        if kind == "date" and parsed <= now or kind == "year" and parsed < year:
            continue
        return False
    return True


def _record_check(rs: dict, claim: Claim, doc: Document | None) -> tuple[str | None, str]:
    """("support" | "contradict" | None, why) for a package release statement (release_statement) against a
    registry record template: the claim must be a 'metadata' template whose record identity is that package
    (package:<ecosystem>:<name>), and the record's own metadata decides. "latest is V": the registry's latest
    version equals V (else it contradicts, naming the latest). "V was released": the record of the requested
    version (requested_exists, the registry's full release history), else V among the latest / recent
    releases; a version the history does not list contradicts, one the record does not cover is unknown."""
    if claim.kind != "metadata" or doc is None:
        return None, ""
    name = _claims._norm_id(rs["name"])
    if not any(r.startswith("package:") and r.rsplit(":", 1)[-1] == name for r in _claims.record_identity(claim)):
        return None, ""
    md = doc.metadata or {}
    latest = str(md.get("latest") or "")
    if not latest:
        return None, ""
    want = T.version_key(rs["version"])
    registry = _claims._provider(doc)
    if rs["kind"] == "latest":
        rel = "support" if T.version_key(latest) == want else "contradict"
        why = "" if rel == "support" else f"{registry} lists {latest} as the latest version of {rs['name']}"
    elif md.get("requested") is not None and T.version_key(md["requested"]) == want:
        rel = "support" if md.get("requested_exists") else "contradict"
        why = "" if rel == "support" else f"{registry} lists no release {rs['version']} of {rs['name']}"
    elif want in {T.version_key(latest)} | {T.version_key(v.get("version")) for v in md.get("versions_recent") or ()
                                            if isinstance(v, dict)}:
        rel, why = "support", ""
    else:
        return None, ""
    if rs["polarity"] < 0:
        rel = {"support": "contradict", "contradict": "support"}[rel]
        why = why or f"{registry} lists the release"
    return rel, why


_PRERELEASE = re.compile(r"(?i)(?:\d|\.)(?:a|b|c|rc|alpha|beta|pre|preview|dev)\.?\d*(?:\.(?:post|dev)\d*)?$|"
                         r"\.dev\d*|\d-[0-9a-z]")
_SPEC = re.compile(r"^\s*(?P<op>===|==|!=|~=|>=|<=|>|<)\s*(?P<v>[\w.*+!-]+)\s*$")
_PYTHON_TARGET = re.compile(r"\b(?:c?python|py)\s*(?P<v>\d+(?:\.\d+){0,2})\b", re.IGNORECASE)


def prerelease(version: str) -> bool:
    """True when a version string carries a pre-release or development marker (PEP 440 a/b/rc/dev, SemVer
    "-beta.1")."""
    return bool(_PRERELEASE.search(str(version or "").strip()))


def _admits(spec: str, version: str) -> bool | None:
    """Whether a version specifier ("">=3.10,<4"") admits `version` (compared on the release numbers the target
    states); None when a clause cannot be read."""
    want = T.version_key(version)
    if not want:
        return None
    width = len(str(version).split("."))
    for clause in [c for c in str(spec or "").split(",") if c.strip()]:
        m = _SPEC.match(clause)
        if not m:
            return None
        op, v = m.group("op"), m.group("v")
        if v.endswith(".*"):
            prefix = v[:-2].split(".")
            same = str(version).split(".")[:len(prefix)] == prefix
            if (op == "==" and not same) or (op == "!=" and same):
                return False
            continue
        have = T.version_key(".".join(v.split(".")[:max(width, len(v.split(".")))]))
        if not have:
            return None
        ok = {"==": want == have, "===": want == have, "!=": want != have, ">=": want >= have, "<=": want <= have,
              ">": want > have, "<": want < have, "~=": want >= have}[op]
        if not ok:
            return False
    return True


def _affects(advisory: dict, version: str) -> bool | None:
    """Whether an OSV advisory record (its metadata) covers `version`: the query asked for that version, or an
    ECOSYSTEM/SEMVER range introduces it before any fix; None when the record says nothing about it."""
    if advisory.get("queried_version") and T.version_key(advisory["queried_version"]) == T.version_key(version):
        return True
    want = T.version_key(version)
    seen = False
    for rng in advisory.get("affected_ranges") or []:
        if not isinstance(rng, dict) or str(rng.get("type") or "").upper() == "GIT":
            continue
        seen = True
        start = None                        # events in order: introduced .. fixed / last_affected, repeated
        for e in rng.get("events") or []:
            if not isinstance(e, dict):
                continue
            if e.get("introduced") is not None:
                start = () if str(e["introduced"]) == "0" else T.version_key(e["introduced"])
            elif start is not None and (e.get("fixed") is not None or e.get("last_affected") is not None):
                end = T.version_key(e.get("fixed") or e.get("last_affected"))
                if want >= start and (want < end if e.get("fixed") is not None else want <= end):
                    return True
                start = None
        if start is not None and want >= start:
            return True
    return False if seen else None


def _registry_verdict(rs: dict, base: dict, pool: dict, sources: _Sources) -> dict:
    """The verdict for a package-registry statement about stability, compatibility, security or recommendation
    (CLAIM_VERIFICATION.md §5). Publication evidence never yields "supported" for these aspects: "stable" needs
    the record's own classification (no pre-release marker and a yank flag that is explicitly false);
    "compatible" and "secure" can only be refuted by the record's declared metadata or an OSV advisory;
    "recommended" is a judgement and is not evaluated."""
    aspect, name = rs["kind"], _claims._norm_id(rs["name"])
    version, target = rs["version"], rs.get("target") or ""
    records, advisories = [], []
    for x in pool.values():
        if x.kind != "metadata":
            continue
        doc = sources.doc(x.source_url)
        md = (doc.metadata or {}) if doc is not None else {}
        ids = _claims.record_identity(x)
        if any(r.startswith("package:") and r.rsplit(":", 1)[-1] == name for r in ids) and md.get("latest"):
            records.append((x, md))
        elif (x.flags or {}).get("record") == "advisory" and _claims._norm_id(md.get("package") or
                                                                              (x.flags or {}).get("package")) == name:
            advisories.append((x, md))
    reasons, rel, decided_by, why = [], None, [], ""
    registry = _claims._provider(sources.doc(records[0][0].source_url)) if records else "the registry"
    entry = None
    for x, md in records:
        if md.get("requested") is not None and T.version_key(md["requested"]) == T.version_key(version):
            entry = {"exists": bool(md.get("requested_exists")), "yanked": md.get("requested_yanked")}
        else:
            for v in md.get("versions_recent") or ():
                if isinstance(v, dict) and T.version_key(v.get("version")) == T.version_key(version):
                    entry = {"exists": True, "yanked": v.get("yanked")}
        if entry is not None:
            decided_by = [x]
            break
    if aspect == "stable":
        if entry is not None and not entry["exists"]:
            rel, why = "contradict", f"{registry} lists no release {version} of {rs['name']}"
        elif prerelease(version):
            rel, why = "contradict", f"{version} carries a pre-release marker"
        elif entry is not None and entry["yanked"] is True:
            rel, why = "contradict", f"{registry} marks {rs['name']} {version} as yanked"
        elif entry is not None and entry["yanked"] is False:
            rel = "support"
            reasons.append(f"{registry} lists {rs['name']} {version} as a final release (no pre-release marker) that "
                           f"is not yanked. 'Stable' here means exactly that; it does not establish compatibility, "
                           f"security or quality.")
        else:
            reasons.append(f"No registry record read states whether {rs['name']} {version} is yanked; publication "
                           f"alone does not establish that a release is stable.")
    elif aspect == "compatible":
        m = _PYTHON_TARGET.search(target)
        spec = next((md.get("requires_python") for _, md in records if md.get("requires_python")), None)
        admits = _admits(spec, m.group("v")) if (m and spec) else None
        if admits is False:
            rel, why = "contradict", f"{registry} declares requires_python {spec}, which excludes Python {m.group('v')}"
            decided_by = [records[0][0]]
        else:
            reasons.append(("The registry declares requires_python " + spec + ", which admits it; a declaration "
                            "is not a compatibility test." if admits else
                            "No registry metadata read decides compatibility with " + (target or "the target") + ".")
                           + " Publication never establishes compatibility.")
    elif aspect == "secure":
        hits = [(x, md) for x, md in advisories if _affects(md, version)]
        if hits:
            rel = "contradict"
            why = "OSV lists " + ", ".join(sorted({str(md.get("id") or "an advisory") for _, md in hits})[:3]) + \
                  f" affecting {rs['name']} {version}"
            decided_by = [x for x, _ in hits]
        else:
            reasons.append(("No OSV advisory read covers " if advisories else "No OSV record was among the sources "
                            "for ") + f"{rs['name']} {version}; the absence of known advisories does not establish "
                           f"that a version is secure, and publication never does.")
    else:   # recommended
        reasons.append("Whether a version is recommended is a judgement (fit for a purpose, maintenance, risk), not "
                       "a fact a registry record or a quoted sentence can verify. Ask about publication, stability, "
                       "compatibility or advisories instead.")
    if rel and rs["polarity"] < 0:         # "is not stable", "is vulnerable": the same evidence, the other way
        rel = {"support": "contradict", "contradict": "support"}[rel]
    state = "not_evaluated" if aspect == "recommended" else \
        {"support": "supported", "contradict": "disputed"}.get(rel, "insufficient")
    if why:
        reasons.insert(0, why[0].upper() + why[1:] + ".")
    supporting = [x for x in decided_by if rel == "support"]
    contradicting = [x for x in decided_by if rel == "contradict"]
    citations = sorted({sources.cite(x.source_url, x) for x in decided_by})
    return dict(base, label=_claims.STATE_TO_LEGACY[state], state=state, category="registry",
                category_reason="a statement about a package's own registry record",
                aspect=aspect, basis=(("registry_record" if decided_by else "version_marker")
                                      if state in ("supported", "disputed") else None),
                confidence=0.5, citations=citations,
                supporting=[x.id for x in supporting], contradicting=[x.id for x in contradicting],
                reasons=reasons, timeframe=None,
                independent_groups={"supporting": len({x.domain for x in supporting}),
                                    "contradicting": len({x.domain for x in contradicting})},
                _conflicts=[(None, x, why or "contradicts the checked statement") for x in contradicting])


def decide(statement: str, pool, relation: dict, *, category: str, now: float, cls: str,
           ttl_overrides: dict | None = None, window: float | None = None, origins: dict | None = None,
           registry_ids=frozenset()) -> dict:
    """The ADR-035 decision for one checked statement: the ONE rule shared by the report verdict (_verdict), the
    per-claim states the reasoning guard reads (claim_states -> evolution.cluster_verdict) and consolidation
    (evolution.cluster_verdict). Callers differ only in how each claim bears on the statement (`relation`
    {claim id: "support" | "contradict"}: the lexical checker for a question's statement, the cluster's own
    agreement and contradiction pairs for a cluster's representative) and which claims are the package's own
    registry record (`registry_ids`).

    Policy (claims.CATEGORY_POLICY[category]): a time-sensitive statement counts only evidence dated inside
    `window` (else the class TTL); copies and syndicated stories are one independent group (claims.
    independence_groups with `origins` and the claims' own origin flags); each group adds its weight (the category's
    undated factor; no age discount for completed past events or historical statements) to a Beta(1,1) posterior;
    claims.verification_state decides, a direct authoritative record (registry record, or the subject's official
    site for a narrow non-quantity fact) suffices only when nothing contradicts.

    -> {state, basis, confidence (posterior mean, unrounded), supporting_groups, contradicting_groups, min_groups,
        timeframe, order, counted, supporting, contradicting, outvoted (contradicting ids inside supporting groups),
        copies, authority}"""
    policy = _claims.CATEGORY_POLICY[category]
    slot = parse_statement(statement)
    order = sorted((x for x in pool if x.id in relation),
                   key=lambda x: (-x.relevance, x.source_url, x.sentence_index, x.id))
    timeframe = None
    counted = order
    if policy["timeframe"]:
        ts_cls = cls if cls in _claims.AGE_DISCOUNT_CLASSES else temporal.CATEGORY_FRESHNESS[category]
        span = float(window) if window else temporal.ttl(ts_cls, ttl_overrides)
        status = {x.id: _claims.timely(x, now=now, window=span) for x in order}
        counted = [x for x in order if status[x.id]]
        timeframe = {"required": True, "window_seconds": span, "class": ts_cls,
                     "timely": sum(1 for v in status.values() if v), "undated": sum(1 for v in status.values()
                                                                         if v is None),
                     "outside_window": sum(1 for v in status.values() if v is False)}
    groups = _claims.independence_groups(counted, origins=origins)
    by_id = {x.id: x for x in counted}
    alpha, beta, sup, con = _claims.PRIOR_ALPHA, _claims.PRIOR_BETA, 0, 0
    outvoted = set()
    age_cls = None if (slot is not None and _past_event(slot, now)) or category == "historical" else cls
    undated = policy["undated_factor"] if policy["undated_factor"] is not None else _claims.UNDATED_FACTOR
    for g in groups:
        members = [by_id[i] for i in g]
        a = sum(1 for x in members if relation[x.id] == "support")
        w = max(_claims.evidence_weight(x, now=now, freshness_class=age_cls, ttl_overrides=ttl_overrides,
                                        undated_factor=undated) for x in members)
        alpha += w * a / len(members)
        beta += w * (len(members) - a) / len(members)
        if a and a >= len(members) - a:
            sup += 1
            outvoted |= {x.id for x in members if relation[x.id] == "contradict"}
        else:
            con += 1
    mean = alpha / (alpha + beta)
    supporting = [x for x in order if relation[x.id] == "support"]
    contradicting = [x for x in order if relation[x.id] == "contradict"]
    backing = [x for x in supporting if x.id in by_id]
    authority = None
    if con == 0 and "registry" in policy["authority"] and any(x.id in registry_ids for x in backing):
        authority = "registry_record"   # the package registry is the authority on its own releases (ADR-033)
    elif con == 0 and "official" in policy["authority"] and slot and slot.get("entity") and \
            slot.get("shape") != "quantity" and any(_claims.official_source(x, slot["head"]) for x in backing):
        authority = "official_source"
    state = _claims.verification_state(sup_groups=sup, con_groups=con, sup_weight=alpha - _claims.PRIOR_ALPHA,
                                       con_weight=beta - _claims.PRIOR_BETA, mean=mean,
                                       min_groups=policy["min_groups"], authoritative=authority is not None)
    basis = None
    if state == "supported":
        corroborated = sup >= policy["min_groups"] and mean >= _claims.VERIFICATION_MIN_CONFIDENCE
        basis = "corroboration" if corroborated else authority
    elif state in ("disputed", "conflicting"):
        basis = "contradiction"
    if policy["timeframe"] and state == "insufficient" and len(counted) < len(order):
        state = "temporally_unresolved"
    return {"state": state, "basis": basis, "confidence": mean, "supporting_groups": sup, "contradicting_groups": con,
            "min_groups": policy["min_groups"], "timeframe": timeframe, "order": order, "counted": counted,
            "supporting": supporting, "contradicting": contradicting, "outvoted": outvoted,
            "copies": len(counted) - len(groups), "authority": authority}


def _verdict(statement: str, clusters: list[ClaimCluster], sources: _Sources, *, now: float, cls: str,
             ttl_overrides: dict | None = None, window: float | None = None, origins: dict | None = None) -> dict:
    """Fact verification by claim-slot checking (section comment above) under the claim-category policy of
    ADR-035 (claims.CATEGORY_POLICY, docs/knowledge/CLAIM_VERIFICATION.md). Every reportable, non-adversarial
    claim is classified against the statement; claims that a listed contradiction pits against a supporting
    claim count as contradicting too, so the verdict never says "0 contradict" next to a contradiction of the
    checked statement. Independent groups (copies and syndicated stories joined: claims.independence_groups with
    `origins`) weigh in as in claims.evaluate, with the category's undated factor; a time-sensitive statement is
    decided only by evidence dated inside `window` (else the class TTL). The verdict carries `state`
    (claims.VerificationState), `category`, `basis`, `timeframe` and the legacy `label`."""
    slot = parse_statement(statement)
    rs = release_statement(statement)
    base = {"statement": statement, "shape": slot["shape"] if slot else None}
    pool = {}
    for c in clusters:
        for x in c.claims:
            if _claims.is_reportable(x):
                pool.setdefault(x.id, x)
    if rs and rs["kind"] not in ("latest", "released"):
        return _registry_verdict(rs, base, pool, sources)
    kinds = {"academic" for d in sources.docs if _is_academic(d)}
    category, category_reason = temporal.claim_category(statement, now=now, freshness_class=cls,
                                                        registry=rs is not None, source_kinds=kinds)
    policy = _claims.CATEGORY_POLICY[category]
    head = dict(base, category=category, category_reason=category_reason,
                aspect=("published" if rs["kind"] == "released" else rs["kind"]) if rs else None)

    def unevaluated(reason: str) -> dict:
        return dict(head, label="insufficient", state="not_evaluated", basis=None, confidence=0.5, citations=[],
                    supporting=[], contradicting=[], reasons=[reason], timeframe=None,
                    independent_groups={"supporting": 0, "contradicting": 0}, _conflicts=[])

    if slot is None:
        return unevaluated("The statement's shape is not one the lexical checker supports (X is in Y; X is the "
                           "capital of Y; X has N <unit>; X was released in DATE; X is Y; X <verb> ... DATE); "
                           "no source was counted.")
    if not policy["evaluated"]:
        return unevaluated(f"Not evaluated: {category_reason}; no source was counted.")
    if not pool:
        return unevaluated("No usable source sentence was read, so no evidence was examined.")
    relation, why = {}, {}
    nests: dict[str, set] = {}
    if slot["shape"] == "location":         # each document's own place nestings (containment, RT-W-02)
        for x in pool.values():
            if x.source_url not in nests:
                doc = sources.doc(x.source_url)
                nests[x.source_url] = _nests(doc.text if doc is not None else "")
            nests[x.source_url] |= _nests(x.text)
    registry_ids = set()
    for x in pool.values():
        rel, reason = _record_check(rs, x, sources.doc(x.source_url)) if rs else (None, "")
        if rel:                             # the package's own registry record decides a release statement
            registry_ids.add(x.id)
        else:
            rel, reason = _check(slot, x, nests.get(x.source_url))
        if rel:
            relation[x.id], why[x.id] = rel, reason
    for c in clusters:
        for pair in c.contradicting:
            for mine, other in ((pair.get("a"), pair.get("b")), (pair.get("b"), pair.get("a"))):
                if relation.get(mine) == "support" and other in pool and other not in relation:
                    relation[other] = "contradict"
                    why[other] = pair.get("reason", "conflicts with a supporting source")
    d = decide(statement, list(pool.values()), relation, category=category, now=now, cls=cls,
               ttl_overrides=ttl_overrides, window=window, origins=origins, registry_ids=registry_ids)
    state, basis, mean, sup, con = d["state"], d["basis"], d["confidence"], d["supporting_groups"], \
        d["contradicting_groups"]
    order, supporting, contradicting, outvoted, timeframe = d["order"], d["supporting"], d["contradicting"], \
        d["outvoted"], d["timeframe"]
    copies = d["copies"]
    counts = f"{sup} independent source group(s) agree with the statement; {con} contradict it."
    if outvoted:             # the counts are derived from the verdict's own lists, never "0 contradict" beside them
        counts += (f" {len(outvoted)} contradicting sentence(s) come from source groups that otherwise support it "
                   f"({len(supporting)} supporting and {len(contradicting)} contradicting sentence(s) in all).")
    reasons = [counts, f"Posterior mean {mean:.2f} from a Beta(1,1) prior and weighted independent groups."]
    reasons.append(f"Category: {category} ({category_reason}).")
    if copies > 0:
        reasons.append(f"{copies} sentence(s) are copies of another source (same site, syndication or near-identical "
                       f"text) and count once.")
    conflicts = []
    for x in contradicting:
        within = " (a sentence in a source group that otherwise supports it)" if x.id in outvoted else ""
        line = f"[{sources.cite(x.source_url, x)}]{within} {why[x.id] or 'contradicts the statement'}."
        if len(reasons) < 7:
            reasons.append(line)
        conflicts.append((x, why[x.id] or "contradicts the checked statement"))
    if basis == "registry_record":
        reasons.append(f"The {rs['name']} registry record itself confirms it; a package registry is the "
                       f"authoritative source for its own releases.")
    elif basis == "official_source":
        site = next(x.domain for x in supporting if _claims.official_source(x, slot["head"]))
        reasons.append(f"{site} is the subject's own official site, a direct authoritative record for this narrowly "
                       f"scoped fact, and no source contradicts it.")
    elif state == "temporally_unresolved":
        reasons.append(f"The statement concerns the current state; {timeframe['undated']} undated and "
                       f"{timeframe['outside_window']} out-of-window sentence(s) bear on it, but no source dated "
                       f"within {_span(timeframe['window_seconds'])} establishes that it holds now.")
    elif state == "conflicting":
        reasons.append("Credible sources disagree and neither side clearly outweighs the other; no consensus is "
                       "assumed.")
    elif state == "insufficient":
        if not order:
            reasons.append("No source sentence names the subject together with the checked relation.")
        elif sup < policy["min_groups"]:
            reasons.append(f"Fewer than {policy['min_groups']} independent source groups support it.")
    citations = sorted({sources.cite(x.source_url, x) for x in order})
    return dict(head, label=_claims.STATE_TO_LEGACY[state], state=state, basis=basis, confidence=round(mean, 3),
                citations=citations, supporting=[x.id for x in supporting],
                contradicting=[x.id for x in contradicting], reasons=reasons, timeframe=timeframe,
                independent_groups={"supporting": sup, "contradicting": con},
                _conflicts=[(supporting[0] if supporting else None, x, reason) for x, reason in conflicts])


def _mentions(item: str, text: str) -> bool:
    need = T.content_tokens(item) or T.tokens(item)
    have = set(T.tokens(text))
    return bool(need) and all(t in have for t in need)


def _comparison(items, ranked: list[tuple[ClaimCluster, float]], sources: _Sources) -> dict | None:
    """Rows per aspect = cluster key without the compared items' own tokens, so "numpy latest version" and
    "pandas latest version" share the row "latest version". Each side holds the best claim mentioning that item."""
    items = [str(x) for x in (items or []) if str(x).strip()][:2]
    if len(items) < 2:
        return None
    item_stems = {T.stem(t) for item in items for t in T.tokens(item)}
    rows: dict[str, dict] = {}
    for c, _ in ranked:
        aspect = " ".join(t for t in c.key.split() if t not in item_stems) or c.key
        support = set(c.supporting)
        ordered = sorted((x for x in c.claims if not _claims.is_adversarial(x)),
                         key=lambda x: (x.id not in support, -x.relevance, x.id))
        row = rows.setdefault(aspect, {"aspect": aspect, "A": None, "B": None, "status": {}})
        for label, item in (("A", items[0]), ("B", items[1])):
            if row[label] is not None:
                continue
            hit = next((x for x in ordered if _mentions(item, x.text)), None)
            if hit is not None:
                row[label] = {"text": hit.text, "type": "template" if hit.kind == "metadata" else "quote",
                              "citations": [sources.cite(hit.source_url, hit)], "confidence": c.confidence,
                              "status": c.status}
    out = [r for r in rows.values() if r["A"] is not None or r["B"] is not None]
    for r in out:
        r.pop("status")
    return {"items": items, "rows": out}


def _is_academic(doc: Document) -> bool:
    if _claims.encyclopedia(doc):
        return False                        # an article lead in metadata["abstract"] is not a paper (T1-14)
    md = doc.metadata or {}
    if any(md.get(k) for k in ("doi", "arxiv_id", "pmid", "abstract", "venue", "journal")):
        return True
    dom = (doc.domain or "").lower()
    return any(dom == a or dom.endswith("." + a) for a in ACADEMIC_DOMAINS)


def _methodology(documents: list[Document], sources: _Sources) -> list[dict]:
    out = []
    for d in documents:
        if _claims.encyclopedia(d):
            out.append({"source": sources.cite(d.canonical_url or d.final_url or d.url), "design": None,
                        "sample": None, "kind": "encyclopedia", "preprint": False, "year": None})
            continue                        # a tertiary reference: no study design, no publication year (T1-14)
        if not _is_academic(d):
            continue
        md = d.metadata or {}
        body = " ".join(x for x in (d.title, md.get("abstract") if isinstance(md.get("abstract"), str) else "",
                                    d.text) if x)
        designs = [label for label, rx in DESIGN_CUES if rx.search(body)]
        if "systematic review" in designs and "review" in designs:
            designs.remove("review")
        m = _SAMPLE.search(body)
        sample = None
        if m:
            sample = f"n = {m.group(1).replace(',', '')}" if m.group(1) else \
                f"{m.group(2).replace(',', '')} {m.group(3).lower()}"
        dom = (d.domain or "").lower()
        preprint = bool(md.get("preprint")) or any(dom == p or dom.endswith("." + p) for p in PREPRINT_DOMAINS)
        year = md.get("year")
        if year is None and d.published_at is not None:
            year = datetime.fromtimestamp(d.published_at, timezone.utc).year
        kind = "review" if any("review" in x or x == "meta-analysis" for x in designs) else \
            ("preprint" if preprint else "primary")
        out.append({"source": sources.cite(d.canonical_url or d.final_url or d.url),
                    "design": ", ".join(designs) or None, "sample": sample, "kind": kind, "preprint": preprint,
                    "year": int(year) if isinstance(year, (int, float, str)) and str(year).isdigit() else None})
    return out


# ---- subject-aware ranking (UAT-W-24) -----------------------------------------------------------------------
#
# The question's focus is its entities (intent.extract_entities, completed by the lower-case nouns that follow a
# name: "the Ostra marsh newt"; the packages and compared items the assessment names) and its relation words (its
# remaining topical words: "capital", "stable release", "population"; "how long" also matches "length"). A claim
#   tier 0  is about a question entity: its head (the noun phrase before the main verb, up to the first comma or
#           preposition; a pronoun stands for the document's title subject) names the entity, its structured
#           record is the entity's (package, repository, title), or the sentence states a relation word about the
#           entity, within RELATION_WINDOW words of it ("Canberra, the capital city of Australia", "the population
#           of Tessaly is ..."); inside the head only its head noun counts ("Capital punishment" is punishment);
#   tier 1  cannot be placed (the question names no entity, or the claim has no head);
#   tier 2  is about something else: its head names another thing ("Capital punishment in Australia ...",
#           "Python 3.15 the latest stable release ...") and the sentence does not state a relation word about the
#           entity outside that head.
# Answer items and findings are ordered by tier, then by the share of relation words the claim contains, then
# by value (confidence x relevance x freshness). Tier 2 claims follow every tier 0 one; a source ALL of whose claims
# are tier 2 is set aside when another source has a tier 0 claim (off_subject_sources, T1-07).
RELATION_WINDOW = 4        # words between a relation word and the entity it is stated about ("capital city of X")
_HEAD_CUT = re.compile(r"[,;:(\[]|\s(?:in|of|for|on|at|from|with|by|to|about|near|under|over|after|before|during|"
                       r"since|until|into|than)\s", re.IGNORECASE)


# "How <adjective> is X?" asks for an attribute that sources state as the adjective or its noun.
_HOW_ATTRIBUTE = {"long": ("long", "length"), "tall": ("tall", "height", "high"), "high": ("high", "height", "tall"),
                  "old": ("old", "age", "aged"), "far": ("far", "distance"), "big": ("big", "size", "large"),
                  "large": ("large", "size", "big"), "deep": ("deep", "depth"), "wide": ("wide", "width"),
                  "heavy": ("heavy", "weight", "weighs"), "fast": ("fast", "speed")}


def _entity_phrase(question: str, name: str) -> str:
    """A capitalised entity with the lower-case nouns that complete it ("Ostra" -> "Ostra marsh newt"), up to the
    next function word, verb or punctuation. The question's last word is never absorbed: in "When did the
    Halvorsen Bridge open?" it is the verb."""
    m = re.search(rf"(?<![\w-]){re.escape(name)}(?P<tail>(?:\s+[a-z][a-z-]*){{1,3}})(?=\s+\w)", question)
    if not m:
        return name
    words = []
    for w in m.group("tail").split():
        if w in T.STOPWORDS or w in _claims._AUX or _claims._VERB_CUE.match(w) or w in _HOW_ATTRIBUTE:
            break
        words.append(w)
    return " ".join([name, *words])


def question_focus(question: str, assessment: dict | None) -> dict:
    """{"entities": [token sets], "relation": [alternative stem sets]} of the question (section comment above)."""
    from . import intent
    assessment = assessment or {}
    question = question or ""
    names = [_entity_phrase(question, n) for n in intent.extract_entities(question)]
    names += [str(p.get("name")) for p in assessment.get("packages") or [] if isinstance(p, dict) and p.get("name")]
    names += [str(x) for x in assessment.get("comparison") or [] if x]
    entities = list(dict.fromkeys(t for t in (_claims.subject_tokens(n) for n in names) if t))
    named = frozenset().union(*entities) if entities else frozenset()
    bare = [*intent.extract_entities(question), *[str(p.get("name")) for p in assessment.get("packages") or []
                                                   if isinstance(p, dict) and p.get("name")]]
    words = intent.keywords(_FV_PREFIX.sub("", question), limit=12)
    how = {m.group(1).lower() for m in re.finditer(r"\bhow\s+([a-z]+)\b", question, re.IGNORECASE)}
    relation = []
    for t in T.content_tokens(words):
        stem = T.stem(t)
        if stem in named or t.lower() in {n.lower() for n in names}:
            continue
        alternatives = _HOW_ATTRIBUTE.get(t.lower()) if t.lower() in how else None
        slot = frozenset(T.stem(a) for a in alternatives) if alternatives else frozenset({stem})
        if slot not in relation:
            relation.append(slot)
    return {"entities": entities, "relation": relation, "names": list(dict.fromkeys(n for n in bare if n))}


def _head(claim: Claim) -> tuple[frozenset, list[str]]:
    """(head tokens, stemmed tokens of the rest of the sentence, in order) of a quoted claim (section comment)."""
    text = _PARENS.sub(" ", claim.text or "")
    clause = _claims._subject_clause(text)
    if _PLURAL_PRONOUN.match(clause):       # "They were first introduced in 2000": not the title's (one) subject
        return frozenset(), [T.stem(t) for t in T.tokens(clause)]
    if _claims.title_reference(clause) is not None:        # "It ...", "The tower ...": the title's subject
        subject = _claims.title_subject(claim.title or "") or ""
        return _claims.subject_tokens(subject), [T.stem(t) for t in T.tokens(clause)]
    words = list(re.finditer(r"[^\W_][\w'’.-]*", clause))
    verb = next((w for i, w in enumerate(words) if i > 0 and (w.group(0).lower() in _claims._AUX or
                                                             _claims._VERB_CUE.match(w.group(0)))), None)
    before = clause[:verb.start()] if verb else " ".join(w.group(0) for w in words[:6])
    cut = _HEAD_CUT.search(before)
    head = before[:cut.start()] if cut else before
    # the head phrase stands in the rest by its head noun only: "the population of Tessaly" keeps "population"
    # next to "Tessaly", "Capital punishment in Australia" keeps "punishment", not "capital"
    noun = ([w for w in T.tokens(head) if w not in T.STOPWORDS] or [""])[-1]
    rest = text.replace(head, f" {noun} ", 1) if head.strip() else text
    return _claims.subject_tokens(head), [T.stem(t) for t in T.tokens(rest)]


_PLURAL_PRONOUN = re.compile(r"^\s*(?:they|these|those|their)\b", re.IGNORECASE)


def _states_relation(rest: list[str], relation: frozenset, entity: frozenset) -> bool:
    """A relation word within RELATION_WINDOW words of every word of the entity ("the capital city of
    Australia"): the sentence states that relation about the entity, not two unrelated mentions."""
    at = [i for i, t in enumerate(rest) if t in relation]
    spots = [[i for i, t in enumerate(rest) if t == e] for e in entity]
    return bool(at) and all(spots) and any(all(any(abs(i - j) <= RELATION_WINDOW for j in s) for s in spots)
                                           for i in at)


def subject_tier(claim: Claim | None, focus: dict) -> int:
    """0 about a question entity, 1 undecided, 2 about something else (section comment above)."""
    entities = focus.get("entities") or []
    if claim is None or not entities:
        return 1
    if claim.kind == "metadata":            # a structured record: its package, repository or title names it
        flags = claim.flags or {}
        own = _claims.subject_tokens(" ".join(str(x) for x in (flags.get("package") or "",
                                                               _claims.record_template(claim.text or "") or "")))
        return 0 if any(e <= own for e in entities) else 2
    head, rest = _head(claim)
    if not head:
        return 1
    # a head of fact predicates only ("First released in Japan ..., the PlayStation ...") names nothing (T1-07, R4)
    if any((e <= head and not _other_name(claim, head - e)) or _claims.same_subject(e, head - _fact_stems())
           for e in entities):
        return 0
    words = frozenset().union(*focus["relation"]) if focus.get("relation") else frozenset()
    page = _claims.subject_tokens(_claims.title_subject(claim.title or "") or "")
    for e in entities:
        if page and (e <= page or _claims.same_subject(e, page)):
            # "Van Rossum first released it in 1991": "it" is the page's subject (R1-04 live, Python article)
            rest = [t for w in rest for t in (sorted(e) if w in ("it", "its") else [w])]
    if any(_states_relation(rest, words, _without_predicate(e)) for e in entities):
        return 0                            # it states the question's relation about the entity
    return 2


@functools.cache
def _fact_stems() -> frozenset:
    from .intent import _FACT_PREDICATE
    return frozenset(T.stem(w) for w in _FACT_PREDICATE)


def _without_predicate(entity: frozenset) -> frozenset:
    """The entity's name without a fact predicate its phrase absorbed ("Rust first" of "When was Rust first
    released?"): "The first stable release, Rust 1.0, was published in May 2015." states the release about Rust,
    though its "first" stands in the head the relation check replaces (T1-07, R4)."""
    return (entity - _fact_stems()) or entity


def _other_name(claim: Claim, extra: frozenset) -> bool:
    """The head names the entity inside a longer proper name ("Monty Python" for Python, T1-07): one of its other
    tokens is a capitalised word of the sentence's subject clause or of the page title. A possessive names the
    owner, not another entity ("ESA's Hera probe")."""
    if not extra:
        return False
    clause = _claims._subject_clause(_PARENS.sub(" ", claim.text or ""))
    capitals = re.findall(r"(?<![\w'’-])[A-Z][\w-]*(?!['’]s\b)(?![\w'’-])", f"{clause} {claim.title or ''}")
    return any(T.stem(w.lower()) in extra for w in capitals)


def mentions_name(text: str, name: str) -> bool:
    """`text` names `name` on its own: not inside a longer proper name ("Monty Python", "Python Software
    Foundation", "Reticulated python": a capitalised word other than a function word right before or after it)."""
    def proper(w):
        return bool(w) and w.group(1)[:1].isupper() and w.group(1).lower() not in T.STOPWORDS

    for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?:['’]s)?(?![\w-])", text or "", re.IGNORECASE):
        before = re.search(r"([\w'’-]+) $", text[:m.start()])
        after = re.match(r" ([\w'’-]+)", text[m.end():])
        if not (proper(before) or proper(after)):
            return True
    return False


# D-R5-4 / T1-07 (R4): a sentence that states a first release with its date ("The first stable release, Rust 1.0,
# was published in May 2015.", "Van Rossum first released it in 1991") answers a first-release question; among
# statements of equal subject tier, sense and relation coverage it leads (it ranked 14th-21st behind "Since the full
# release, Rust has followed a simple rhythm", so the model never saw it).
_FIRST_RELEASE = re.compile(
    r"\b(?:first|initial|original|earliest|debut)\s+(?:[\w.-]+\s+){0,2}?(?:release|version|launch)\b|"
    r"\b(?:first|initially|originally)\s+(?:[a-z]+ly\s+)?(?:released|launched|published|introduced|shipped|"
    r"appeared)\b|\b(?:version\s+)?v?1\.0\b(?=[^.]*\b(?:released?|published|launched|stable)\b)", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:1[6-9]|20)\d\d\b")


def first_release(text: str | None) -> bool:
    """`text` states a first release (or version 1.0) together with a year (see the comment above)."""
    return bool(text and _FIRST_RELEASE.search(text) and _YEAR.search(text))


def off_subject_sources(clusters, focus: dict) -> set:
    """Source URLs that are about another entity (T1-07: Wikipedia "Monty Python" for "When was Python first
    released?", a stable-diffusion image page for "Is numpy 2.5.5 stable?"): no claim of theirs is about the
    question's entity (subject_tier 0) and neither their title nor any of their claims names it on its own
    (mentions_name). Only when another source has a claim that is about it or names it, so an answer is never
    emptied by a misread entity."""
    names = focus.get("names") or []
    if not names or not focus.get("entities"):
        return set()
    on: dict[str, bool] = {}
    for c in clusters:
        for x in c.claims:
            hit = subject_tier(x, focus) == 0 or any(mentions_name(t, n) for n in names
                                                     for t in (x.text or "", x.title or ""))
            on[x.source_url] = on.get(x.source_url, False) or hit
    if not any(on.values()):
        return set()
    return {url for url, hit in on.items() if not hit}


def statement_off_subject(clusters, statement: str | None) -> set:
    """Source URLs that never name the checked statement's subject (N2, R4 retest: "The human brain contains 100
    billion neurons." cited "Milky Way contains 100 billion planets" and a "USD 100 Billion" climate-finance record,
    which share only the number). Named means: every capitalised word of a proper-name subject ("Eiffel Tower"), or the head
    noun of a common-noun subject ("brain" of "The human brain"), appears in one of the source's claims or its
    title ("Hera" of "The Hera probe"). Only when another source names it, so a fact check is never emptied by a misparsed subject."""
    slot = parse_statement(statement) if statement else None
    if not slot or not slot.get("subject"):
        return set()
    words = [w for w in T.tokens(slot["subject"]) if w.isalpha() and w not in T.STOPWORDS]
    names = [w for w in re.findall(r"[\w'’-]+", slot["subject"]) if w[:1].isupper() and w.lower() not in T.STOPWORDS]
    if slot.get("entity") and names:
        needed = _claims.subject_tokens(" ".join(names))
    else:
        needed = frozenset({T.stem(words[-1])}) if words else frozenset()
    if not needed:
        return set()
    on: dict[str, bool] = {}
    for c in clusters:
        for x in c.claims:
            hit = needed <= _claims.subject_tokens(f"{x.text or ''} {x.title or ''}")
            on[x.source_url] = on.get(x.source_url, False) or hit
    if not any(on.values()):
        return set()
    return {url for url, hit in on.items() if not hit}


def relation_coverage(claim: Claim | None, focus: dict) -> float:
    """Share of the question's relation words (each with its alternatives: "how long" -> long/length) the claim
    contains; 0 when the question has none."""
    relation = focus.get("relation") or []
    if claim is None or not relation:
        return 0.0
    have = _claims._stem_set(claim)
    return round(sum(1 for slot in relation if slot & have) / len(relation), 6)


# ---- ecosystem relevance (RT-W-04) ---------------------------------------------------------------------------
#
# A plain package name ("numpy") is looked up on PyPI and npm when the question names neither ecosystem; the same
# name on the other registry is usually another, unrelated package (npm "numpy" 0.0.1, 2017). When both answer:
#   1. the question's own wording decides (intent.ecosystem_hint: python/pip/pypi/conda/jupyter/data science vs
#      npm/node/javascript/typescript/react ...);
#   2. otherwise the records do: a *placeholder* record (deprecated, latest version below 0.1, or a single release
#      whose description is empty or only repeats the name) gives way to a substantive one, and so does a record
#      whose latest release is more than PACKAGE_STALE_SECONDS older than the other's;
#   3. otherwise (two substantive, active packages) both are kept: they are different records and never cluster.
# A record set aside is still a source read, but no claim is taken from it (orchestrator.evaluate) and it never
# reaches the answer, findings or comparison; one uncertainty line names it ("numpy also exists on npm (0.0.1,
# 2017): a different package, set aside").
PACKAGE_STALE_SECONDS = 2 * 365 * temporal.DAY


def _package_record(d: Document) -> tuple[str, str] | None:
    md = d.metadata or {}
    eco, name = md.get("ecosystem"), md.get("package")
    if (d.kind != "api" and d.retrieval_mode != "api") or eco not in ("PyPI", "npm") or not name:
        return None
    return str(eco), str(name).lower()


def _placeholder(d: Document) -> bool:
    md = d.metadata or {}
    versions = md.get("versions_recent") or []
    summary = T.collapse(str(md.get("summary") or "")).lower().strip(" .")
    bare = not summary or summary == str(md.get("package") or "").lower()
    latest = T.version_key(md.get("latest") or "")
    return bool(md.get("deprecated")) or (bool(latest) and latest < T.version_key("0.1")) or \
        (len(versions) <= 1 and bare)


def package_ecosystems(question: str, assessment: dict | None, documents) -> dict:
    """{"aside": {canonical url: note}} for package records of another ecosystem that the same plain name matched
    (see the section comment above). Deterministic; reads only the question and the records' own metadata."""
    from . import intent
    named = {(str(p.get("ecosystem")), str(p.get("name")).lower()) for p in (assessment or {}).get("packages") or []
             if isinstance(p, dict) and not p.get("inferred")}
    by_name: dict[str, dict[str, Document]] = {}
    for d in documents or []:
        rec = _package_record(d)
        if rec:
            by_name.setdefault(rec[1], {}).setdefault(rec[0], d)
    hint = intent.ecosystem_hint(question)
    aside = {}
    for name, recs in sorted(by_name.items()):
        if len(recs) < 2:
            continue
        keep = None
        explicit = [eco for eco in recs if (eco, name) in named]
        if len(explicit) == 1:
            keep = explicit[0]
        elif hint in recs:
            keep = hint
        else:
            when = {eco: float((d.metadata or {}).get("released_at") or d.published_at or 0.0) for eco, d in recs.items()}
            for a, b in (("PyPI", "npm"), ("npm", "PyPI")):
                if not _placeholder(recs[a]) and (_placeholder(recs[b]) or
                                                  when[a] - when[b] > PACKAGE_STALE_SECONDS):
                    keep = a
                    break
        if keep is None:
            continue
        for eco, d in recs.items():
            if eco == keep:
                continue
            md = d.metadata or {}
            year = _day(md.get("released_at") or d.published_at)
            detail = ", ".join(x for x in (str(md.get("latest") or ""), year[:4] if year else "") if x)
            aside[d.canonical_url or d.final_url or d.url] = (
                f"{md.get('package') or name} also exists on {eco}{f' ({detail})' if detail else ''}: a different "
                f"package from the {keep} one this answer is about; set aside, not mixed into the answer.")
    return {"aside": aside}


def _package_of(c: ClaimCluster, packages) -> str | None:
    for x in c.claims:
        p = (x.flags or {}).get("package")
        if p:
            return str(p)
    for p in packages or ():
        if _mentions(str(p), c.statement):
            return str(p)
    return None


# ---- the report ---------------------------------------------------------------------------------------------

def claim_state(cluster: ClaimCluster, *, now: float, cls: str, ttl_overrides: dict | None = None,
                window: float | None = None, origins: dict | None = None) -> dict:
    """{state, category, basis, reason}: the ADR-035 verification state of one reported statement (a cluster's
    representative), exactly as consolidation will decide it (evolution.cluster_verdict, i.e. decide). Written
    on every finding and answer item before reasoning, so the reasoning guard reads this state and never the
    legacy cluster label ("corroborated" counts sources, not timeliness or category)."""
    from . import evolution
    v = evolution.cluster_verdict(cluster, now=now, freshness_class=cls, ttl_overrides=ttl_overrides, window=window,
                                  origins=origins)
    return {"state": v["state"], "category": v.get("category"), "basis": v.get("basis"), "reason": v.get("reason")}


def synthesize(question: str, assessment: dict | None, documents, clusters, *, now: float, usage=None,
               failures=(), ttl_overrides: dict | None = None) -> dict:
    """The research report (ARCHITECTURE §5). Clusters are re-scored with `now` and the question's freshness
    class, ranked by value = confidence x relevance x freshness, and the top MAX_ANSWER_ITEMS are quoted.
    `ttl_overrides` (WebConfig.freshness_ttl) replaces class TTLs in scoring, the freshness requirement and the
    verdict."""
    assessment = dict(assessment or {})
    documents = list(documents or [])
    failures = list(failures or [])
    mode = assessment.get("mode") or "deep_research"
    cls = temporal.classify(question, assessment)
    window = assessment.get("recency_window")
    class_ttl = temporal.ttl(cls, ttl_overrides)
    # D-R5-4: a question about a first release asks for history: no recency requirement at all
    historical = not window and temporal.historical_release(question)
    required = None if historical else float(window) if window else class_ttl
    sources = _Sources(documents)

    aside = package_ecosystems(question, assessment, documents)["aside"]
    if aside:                # RT-W-04: another ecosystem's package of the same name is never part of the answer
        clusters = [replace(c, claims=kept) for c in clusters
                    for kept in [[x for x in c.claims if x.source_url not in aside]] if kept]
    focus = question_focus(question, assessment)
    # {url: why} of the sources read but never cited; report.set_aside hands this one decision to consolidation,
    # which removes the same claims, so a set-aside source never feeds knowledge, owner questions or hypotheses
    unrelated = dict.fromkeys(off_subject_sources(clusters, focus), "about another entity than the question's")
    if mode == "fact_verification":      # N2: a record that shares only the number is not about the statement
        for url in statement_off_subject(clusters, checkable_statement(assessment.get("claim") or question)):
            unrelated.setdefault(url, "never names the checked statement's subject")
    # T1-07 (R4): "Rust (video game)" is read for "When was Rust first released?" but is another sense of the name
    senses = sense.separate(assessment.get("claim") or question, assessment, documents, focus.get("names"),
                            mentions=mentions_name)
    if senses:
        for url in senses["set_aside"]:
            unrelated.setdefault(url, f"another sense of {senses['name']} ({senses['sources'][url]})")
    own_sense = {u for u, v in (senses or {}).get("sources", {}).items() if v == (senses or {}).get("sense")}
    if unrelated:            # T1-07: a page about another entity is a source read, never cited
        clusters = [replace(c, claims=kept) for c in clusters
                    for kept in [[x for x in c.claims if x.source_url not in unrelated]] if kept]
    origins = _claims.document_origins(documents)      # copies of one story are one source (ADR-035)
    scored = [_claims.score(c, now=now, freshness_class=cls, ttl_overrides=ttl_overrides, origins=origins)
              for c in clusters if c.claims]
    usable = [c for c in scored if c.status != "insufficient"]

    def value(c: ClaimCluster) -> float:
        rep = _representative(c)
        ok = temporal.window_ok(_claim_fresh_ts(rep), required, now) if rep else None
        factor = 1.0 if ok else (UNDATED_VALUE if ok is None else STALE_VALUE)
        return c.confidence * (rep.relevance if rep else 0.0) * factor

    def order(t):            # UAT-W-24: on-subject first, then the question's sense, relation coverage, value
        c = t[0]
        rep = _representative(c)
        # a contested cluster is placed by the best of its two sides: "Van Rossum first released it in 1991 as
        # Python 0.9.0" answers "When was Python first released?" though the side that leads is about "the code
        # (for Python version 0.9.1)" (R1-04 live)
        sides = {x["a"] for x in c.contradicting} | {x["b"] for x in c.contradicting}
        claims = [rep] + [x for x in c.claims if x.id in sides and x is not rep]
        return (min(subject_tier(x, focus) for x in claims), 0 if any(x.source_url in own_sense for x in claims)
                else 1, -max(relation_coverage(x, focus) for x in claims),
                -max(historical and first_release(x.text) for x in claims), -t[1], c.key, c.statement)

    ranked = sorted(((c, value(c)) for c in usable if report_safe_text(_representative(c)) == c.statement),
                    key=order)
    old = _superseded(ranked)
    if old:      # a newer release of the same package was read: older ones follow it (C-02)
        flagged = [(c, v, i in old) for i, (c, v) in enumerate(ranked)]
        ranked = [(c, v) for c, v, _ in sorted(flagged, key=lambda t: t[2])]
        old_ids = {id(c) for c, v, is_old in flagged if is_old}
    else:
        old_ids = set()

    # the ADR-035 state of each reported statement, decided now (before reasoning reads it) by the same rule as
    # consolidation (evolution.cluster_verdict -> decide); `status` keeps the legacy cluster label
    verified = {id(c): claim_state(c, now=now, cls=cls, ttl_overrides=ttl_overrides, window=window, origins=origins)
                for c, _ in ranked}

    # answer items (quotes) first: they get the lowest source numbers
    items = []
    for c, v in ranked[:MAX_ANSWER_ITEMS]:
        rep = _representative(c)
        copies, independent = _support_counts(c)
        items.append({"text": c.statement, "type": "template" if rep and rep.kind == "metadata" else "quote",
                      "citations": _cite_cluster(c, sources), "corroborating": [], "confidence": c.confidence,
                      "uncertainty": c.uncertainty, "status": c.status, "verification": verified[id(c)],
                      "copies": copies, "independent_sources": independent, "value": round(v, 6)})

    packages = assessment.get("packages") or []
    findings = []
    for c, v in ranked:
        rep = _representative(c)
        copies, independent = _support_counts(c)
        finding = {"statement": c.statement,
                   "type": "template" if rep and rep.kind == "metadata" else "quote",
                   "kind": rep.kind if rep else "statement", "key": c.key,
                   "citations": _cite_cluster(c, sources), "corroborating": [], "related": [],
                   "confidence": c.confidence,
                   "uncertainty": c.uncertainty, "status": c.status, "verification": verified[id(c)],
                   "copies": copies, "independent_sources": independent, "value": round(v, 6),
                   "published_at": rep.published_at if rep else None, "event_at": rep.event_at if rep else None,
                   "claims": [x.id for x in c.claims], "package": _package_of(c, packages)}
        if id(c) in old_ids:
            finding["superseded"] = True
        findings.append(finding)
    for entry, (c, _) in [*zip(items, ranked), *zip(findings, ranked)]:     # numbered after the restating ones
        entry["corroborating"] = _corroborating(c, sources)
    related = list(zip(findings, [c for c, _ in ranked]))     # numbered last, after every cited source
    if mode == "scientific":
        for f in findings:
            f["section"] = "interpretations" if f["kind"] == "interpretation" else "findings"
        findings.sort(key=lambda f: f["section"] == "interpretations")
    elif mode == "technical":
        order = list(dict.fromkeys(f["package"] for f in findings if f["package"]))
        findings.sort(key=lambda f: order.index(f["package"]) if f["package"] in order else len(order))

    # contradictions: both sides, dates, source types, and the basis of any assessment
    contradictions = []
    for c, _ in ranked:
        by_id = {x.id: x for x in c.claims}
        for pair in c.contradicting:
            if len(contradictions) >= MAX_CONTRADICTIONS:
                break
            a, b = by_id.get(pair["a"]), by_id.get(pair["b"])
            if report_safe_text(a) is None or report_safe_text(b) is None:
                continue
            contradictions.append(_contradiction(a, b, pair["reason"], c, sources, now=now, cls=cls))

    # freshness and liveness over cited sources
    cited = sources.cited_docs()
    # N3 (R4): freshness is decided by the sources the answer's quoted statements cite; a source cited only by a
    # low-ranked finding (an off-topic record, a job advert) neither dates the answer nor makes it fresh
    answered = {n for item in items for n in item["citations"]}
    answer_docs = [d for d in cited if sources.number(d) in answered]
    # newest_source_at is a publication date (a registry record's release upload time), never a fetch time (R1-03);
    # a registry record read live still makes the registry state fresh (observed_at)
    stamps = [d.published_at for d in answer_docs if d.published_at is not None]
    newest = max(stamps) if stamps else None
    observed = [d.retrieved_at for d in answer_docs if _observed_live(d) and d.retrieved_at is not None]
    satisfied = historical or any(temporal.window_ok(_fresh_ts(d), required, now) for d in answer_docs)
    live = any(d.retrieval_mode in LIVE_MODES for d in cited)
    freshness = {"class": cls, "window_seconds": window, "ttl_seconds": class_ttl, "required_seconds": required,
                 "satisfied": bool(satisfied), "newest_source_at": newest,
                 "observed_at": max(observed) if observed else None}

    limitations: list[str] = []
    for f in failures:
        who, code = _failure_label(f)
        label = f"{who}: {code}"
        if label not in limitations:
            limitations.append(label)
    if not satisfied:
        if newest is None:
            limitations.append(f"Freshness requirement not met: no cited source is dated (required: within "
                               f"{_span(required)}, {cls} class)")
        else:
            limitations.append(f"Freshness requirement not met: newest source is from {_day(newest)} "
                               f"(required: within {_span(required)}, {cls} class)")
    if cited and not live:
        oldest = min(d.retrieved_at for d in cited)
        limitations.append(f"Served from cache retrieved {temporal.describe_age(oldest, now)}; not re-verified live")
    if not usable:
        limitations.append("No claim relevant to the question could be extracted from the sources read" if documents
                           else "No source was read, so no claim could be extracted")
    limitations.append("Extractive synthesis: statements are quoted from sources, not generated; contradiction "
                       "detection covers numbers, versions, dates, polarity and explicit opposites only (ADR-022)")

    uncertainties: list[str] = list(dict.fromkeys(aside.values()))
    if senses:
        uncertainties.append(f"{senses['name']} also names {sense._join(senses['others'])}: "
                             f"{len(senses['set_aside'])} source(s) about {'it' if len(senses['others']) == 1 else 'them'}"
                             f" were read, not cited" + (f"; this answer is about {senses['label']}" if senses['label']
                                                         else "") + f" ({senses['by']}).")
    for c, _ in ranked:
        rep = _representative(c)
        if c.status == "single_source" and rep is not None:
            uncertainties.append(f"Single source: \"{_short(c.statement)}\" is reported only by {rep.domain} "
                                 f"[{sources.cite(rep.source_url, rep)}].")
        elif c.status == "contested":
            uncertainties.append(f"Contested: \"{_short(c.statement)}\" has {len(c.contradicting)} conflicting "
                                 f"report(s); see contradictions.")
    for d in cited:
        if d.published_at is None and not _observed_live(d):
            uncertainties.append(f"Undated source: [{sources.number(d)}] {d.domain} gives no publication date; "
                                 f"its age is unknown.")
    for d in documents:
        found = d.flags.get("injection") or []
        if found:
            n = sources.number(d)
            ref = f"[{n}] " if n else ""
            patterns = ", ".join(sorted({f.get("pattern", "?") for f in found}))
            q = d.flags.get("quarantined_sentences", 0)
            uncertainties.append(f"Instruction-like content in {ref}{d.domain} ({d.canonical_url or d.url}): "
                                 f"{patterns}; {q} sentence(s) quarantined and not used as evidence.")
    adversarial = sum(1 for d in documents if _claims.adversarial_document(d))
    if adversarial:
        uncertainties.append(f"{adversarial} source(s) excluded as adversarial: they carry instruction-like content, "
                             f"so their remaining statements are shown but carry no evidence weight.")
    for d in documents:
        if d.flags.get("truncated"):
            n = sources.number(d)
            ref = f"[{n}] " if n else ""
            uncertainties.append(f"Truncated: {ref}{d.domain} exceeded the size limit; later content was not read.")
    if failures:
        labels = ", ".join(dict.fromkeys(f"{w}: {c}" for w, c in map(_failure_label, failures)))
        uncertainties.append(f"{len(failures)} retrieval failure(s) may have hidden evidence ({labels}).")

    verdict = None
    # a package-registry statement gets a verdict in any mode ("Is corvane 2.5.4 stable?" may be a quick lookup)
    release_question = release_statement(checkable_statement(question) or "") is not None
    if (mode == "fact_verification" or release_question) and any(c.claims for c in scored):   # cancelled: none
        statement = checkable_statement(assessment.get("claim") or question)
        if statement is None:        # RT-B-06: nothing checkable (a wh-question): the answer carries the result
            limitations.append("No verdict: the question is not a checkable statement (X is in Y; X is the capital "
                               "of Y; X has N <unit>; X was released in DATE; X is Y; X <verb> ...); the answer and "
                               "contradictions carry the result")
        else:
            verdict = _verdict(statement, usable, sources, now=now, cls=cls, ttl_overrides=ttl_overrides,
                               window=window, origins=origins)
            listed = {side["id"] for c in contradictions for side in (c["a"], c["b"])}
            for sup, x, reason in verdict.pop("_conflicts"):     # S-09: every counted contradiction is listed
                if x.id not in listed and len(contradictions) < MAX_CONTRADICTIONS:
                    contradictions.append(_verdict_contradiction(statement, sup, x, reason, sources))
                    listed.add(x.id)
    comparison = _comparison(assessment.get("comparison"), ranked, sources) if mode == "comparative" else None
    methodology = _methodology(documents, sources) if mode == "scientific" else None

    lead_citations = sorted({n for it in items for n in it["citations"]})
    quoted = [r for r in (_representative(c) for c, _ in ranked) if r is not None and r.kind != "metadata"]
    notable = bool(quoted) and all((r.flags or {}).get("notable") for r in quoted)
    lead = _lead(documents, usable, ranked, satisfied, newest, required, now, live, cited)
    if notable:            # W-19: a request without a question gets the pages' notable statements, not answers
        for entry in items + findings:
            entry["notable"] = True
        lead = ("Notable statements: the request asks no question, so the most informative sentences of the pages "
                "read are listed; they are not answers. " + lead)
    answer = [{"text": lead,
               "type": "template", "citations": lead_citations, "confidence": None, "status": "summary"}] + items

    for f, c in related:
        f["related"] = _related(c, sources)
    if usage is None:
        usage_json = {}
    elif hasattr(usage, "to_json"):
        usage_json = usage.to_json()
    else:
        usage_json = dict(usage)
    return {"question": question, "mode": mode, "as_of": now, "live": bool(live), "answer": answer,
            "findings": findings, "contradictions": contradictions, "uncertainties": uncertainties,
            "limitations": limitations, "freshness": freshness, "sources": sources.finish(), "verdict": verdict,
            "comparison": comparison, "methodology": methodology, "usage": usage_json, "budget": None,
            "sense": senses, "set_aside": dict(sorted((dict.fromkeys(aside, "another ecosystem's package of the same "
                                                                     "name") | unrelated).items()))}


def _lead(documents, usable, ranked, satisfied, newest, required, now, live, cited) -> str:
    n_docs = len(documents)
    if not n_docs:
        return "No source was read: the searches returned nothing that could be read."
    if not ranked:
        return f"No usable claims were extracted from {n_docs} source{'s' if n_docs != 1 else ''} read."
    top = ranked[0][0]
    agreeing = _claims.evaluate(top.claims)["support_groups"]
    status = {"corroborated": "corroborated", "single_source": "reported by a single source",
              "contested": "contested"}.get(top.status, top.status)
    text = (f"{n_docs} source{'s' if n_docs != 1 else ''} read; {len(usable)} statement cluster"
            f"{'s' if len(usable) != 1 else ''} had usable evidence; the top statement is {status} "
            f"({agreeing} agreeing independent source group{'s' if agreeing != 1 else ''} of "
            f"{top.independent_sources}).")
    if satisfied and newest is not None:
        text += f" Newest cited source: {temporal.describe_age(newest, now)}."
    elif satisfied:
        text += " The cited registry records were read live."
    else:
        text += f" No cited source is dated within the required {_span(required)}; these statements may be out of date."
    if cited and not live:
        text += " All cited sources were served from cache."
    return text


def _verdict_contradiction(statement: str, sup: Claim | None, x: Claim, reason: str, sources: _Sources) -> dict:
    """A sentence the verdict counts against the checked statement, listed as a contradiction: the supporting
    sentence it conflicts with (or, when no source supports the statement, the checked statement itself) vs it."""
    b = _side(x, sources)
    if sup is not None:
        a = _side(sup, sources)
        description = (f"Sources disagree about the checked statement: [{a['citation']}] supports it, "
                       f"[{b['citation']}] {reason}.")
    else:
        a = {"id": None, "text": statement, "type": "statement", "citation": None, "domain": "", "published_at": None,
             "event_at": None, "source_type": "checked statement", "kind": "statement"}
        description = f"A source contradicts the checked statement: [{b['citation']}] {reason}."
    return {"a": a, "b": b, "reason": f"fact check: {reason}", "description": description, "verdict": True,
            "values": None,
            "assessment": f"Counted against «{_short(statement)}» in the verdict. " + UNRESOLVED}


def _side(x: Claim, sources: _Sources) -> dict:
    return {"id": x.id, "text": x.text, "type": "template" if x.kind == "metadata" else "quote",
            "citation": sources.cite(x.source_url, x), "domain": x.domain, "published_at": x.published_at,
            "event_at": x.event_at, "source_type": "primary" if _claims.is_primary(x) else
            ((x.flags or {}).get("source_type") if (x.flags or {}).get("source_type") == "tertiary" else "secondary"),
            "kind": x.kind}


def _contradiction(a: Claim, b: Claim, reason: str, c: ClaimCluster, sources: _Sources, *, now: float | None = None,
                   cls: str | None = None) -> dict:
    sa, sb = _side(a, sources), _side(b, sources)
    na, nb = sa["citation"], sb["citation"]
    parts = []

    def dated(n: int, x: Claim) -> str:
        if x.published_at is None and x.event_at is None:
            return f"[{n}] is undated"
        out = f"[{n}] published {_day(x.published_at)}" if x.published_at is not None else f"[{n}] undated"
        return out + (f" (event {_day(x.event_at)})" if x.event_at is not None else "")

    parts.append(f"{dated(na, a)}; {dated(nb, b)}.")
    if sa["source_type"] != sb["source_type"] and "primary" in (sa["source_type"], sb["source_type"]):
        prim = na if sa["source_type"] == "primary" else nb
        sec = nb if prim == na else na
        parts.append(f"[{prim}] is a primary structured record; [{sec}] is a secondary page.")
    counted = [x for x in c.claims if not _claims.is_adversarial(x)]
    groups = _claims.independence_groups(counted)
    by_id = {x.id: x for x in counted}
    side_a = sum(1 for g in groups if any(_claims.conflict(by_id[i], b) and not _claims.conflict(by_id[i], a)
                                          for i in g))
    side_b = sum(1 for g in groups if any(_claims.conflict(by_id[i], a) and not _claims.conflict(by_id[i], b)
                                          for i in g))
    parts.append(f"Independent source groups: {side_a} side with [{na}], {side_b} with [{nb}].")
    da = {"key": c.key, "values": a.values, "published_at": a.published_at, "event_at": a.event_at,
          "retrieved_at": a.retrieved_at, "polarity": a.polarity}
    db = {"key": c.key, "values": b.values, "published_at": b.published_at, "event_at": b.event_at,
          "retrieved_at": b.retrieved_at, "polarity": b.polarity}
    value_change = not reason.startswith("opposite polarity") and not reason.startswith("conflicting statements")
    # a newer value supersedes an older one only for a time-varying fact: the question's class ages evidence, or
    # the statement is about the current state; never for an anatomical count or a historical date (T1-16)
    time_varying = cls in _claims.AGE_DISCOUNT_CLASSES or \
        temporal.claim_category(a.text or "", now=now, freshness_class=cls)[0] == "time_sensitive"
    if value_change and time_varying and (temporal.supersedes(da, db) or temporal.supersedes(db, da)):
        newer_n = na if temporal.supersedes(da, db) else nb
        parts.append(f"Leaning: [{newer_n}] (basis: its effective date is later and it reports a different value "
                     f"for the same subject; a newer value may supersede an older one for time-varying facts). "
                     f"Not independently confirmed.")
    else:
        parts.append(UNRESOLVED)
    return {"a": sa, "b": sb, "reason": reason, "description": _claims.describe_conflict(a, b, reason),
            "values": _claims.conflict_sides(reason), "assessment": " ".join(parts)}


# ---- working memory -----------------------------------------------------------------------------------------

def _estimate(s: str) -> int:
    return math.ceil(len((s or "").split()) * TOKENS_PER_WORD)


def context_pack(report: dict, *, max_tokens: int) -> dict:
    """A bounded working-memory bundle: the highest-value answer items and findings (greedy by value) whose token
    estimate (words x 1.33) fits `max_tokens` together with an uncertainty line and the cited sources. Quoted
    texts travel inside safety.untrusted envelopes. Returns {"tokens", "items", "uncertainty", "sources"}."""
    max_tokens = max(0, int(max_tokens))
    by_n = {s["n"]: s for s in report.get("sources", [])}
    findings = report.get("findings", [])
    single = sum(1 for f in findings if f.get("status") == "single_source")
    contested = sum(1 for f in findings if f.get("status") == "contested")
    fresh = (report.get("freshness") or {}).get("satisfied")
    uncertainty = (f"Uncertainty: {single} single-source and {contested} contested finding(s); freshness "
                   f"{'met' if fresh else 'NOT met'}; live={'yes' if report.get('live') else 'no'}; "
                   f"{len(report.get('limitations', []))} limitation(s).")
    used = _estimate(uncertainty)
    if used > max_tokens:
        words = uncertainty.split()[:int(max_tokens / TOKENS_PER_WORD)]
        uncertainty = " ".join(words)
        used = _estimate(uncertainty)
    candidates, seen = [], set()
    for pos, it in enumerate(list(report.get("answer", [])) + list(findings)):
        textv = it.get("text") or it.get("statement")
        if it.get("status") == "summary" or not textv or textv in seen:
            continue
        seen.add(textv)
        val = it.get("value", it.get("confidence") or 0.0)
        candidates.append((-(val or 0.0), pos, textv, it))
    candidates.sort(key=lambda t: (t[0], t[1]))
    items, sources, have = [], [], set()
    for _, _, textv, it in candidates:
        cites = [n for n in it.get("citations", []) if n in by_n]
        new = [n for n in cites if n not in have]
        cost = _estimate(textv) + 2 + sum(_estimate(f"[{n}] {by_n[n]['title']} {by_n[n]['url']}") for n in new)
        if used + cost > max_tokens:
            continue
        used += cost
        for n in new:
            have.add(n)
            s = by_n[n]
            sources.append({"n": n, "title": s["title"], "url": s["url"], "domain": s["domain"],
                            "published_at": s["published_at"]})
        src = by_n[cites[0]]["url"] if cites else ""
        items.append({"content": safety.untrusted(textv, src), "type": it.get("type", "quote"), "citations": cites,
                      "confidence": it.get("confidence"), "status": it.get("status")})
    sources.sort(key=lambda s: s["n"])
    return {"tokens": used, "items": items, "uncertainty": uncertainty, "sources": sources}


__all__ = ["LIVE_MODES", "MAX_ANSWER_ITEMS", "UNRESOLVED", "claim_state", "context_pack", "decide", "parse_statement",
           "report_safe_text", "synthesize"]
