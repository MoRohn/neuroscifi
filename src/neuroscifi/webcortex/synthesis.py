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
LIVE_MODES              retrieval modes that count as "retrieved live during this task"
SUPPORTED_MIN_CONFIDENCE, SUPPORTED_MIN_GROUPS   fact-verification thresholds for "supported": the decisive
                        criteria are >= 2 agreeing independent groups and no contradiction; the confidence floor
                        0.72 admits exactly-two dated secondary sources (posterior 2.6/3.6 = 0.722) and rejects one
                        dated + one undated secondary source (2.4/3.4 = 0.706) and two undated ones (0.688)
STALE_VALUE, UNDATED_VALUE                       freshness multipliers in the answer-ranking value
TOKENS_PER_WORD         token estimate for context packs (1 token ~ 0.75 words)
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from . import claims as _claims
from . import safety, temporal
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
                     "flags": _report_flags(d.flags), "cited": False}
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
                                     "flags": _report_flags(d.flags), "cited": False})
        return self.entries


def _cite_cluster(c: ClaimCluster, sources: _Sources) -> list[int]:
    """Only the sources whose own claim supports the representative (claims.agrees): related cluster members are
    not cited."""
    support = set(c.supporting)
    nums = []
    for x in c.claims:
        if x.id in support and _claims.is_reportable(x):
            nums.append(sources.cite(x.source_url, x))
    return sorted(set(nums))


def _support_counts(c: ClaimCluster) -> tuple[int, int]:
    """(copies, independent_sources) of a cluster's supporting claims: cited documents, and independent groups
    among them (near-duplicate copies and one domain count once), so a UI can say "3 copies, 1 independent
    source"."""
    support = set(c.supporting)
    claims = [x for x in c.claims if x.id in support and _claims.is_reportable(x)]
    docs = {x.source_url for x in claims}
    return len(docs), len(_claims.independence_groups(claims))


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
    return {i for i, (name, rank) in keys.items() if rank < best[name]}


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
    if _claims._PRONOUN_START.match(claim.text or "") and alias and _claims.same_subject(alias, slot["head"]):
        return 0
    return None


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


def _check(slot: dict, claim: Claim) -> tuple[str | None, str]:
    """("support" | "contradict" | None, why) for one claim against the parsed statement."""
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
        places = []
        for m in _PLACE.finditer(after):
            place = m.group("p").rstrip(".,;:")
            toks = _claims.subject_tokens(place)
            if toks and not toks <= slot["head"] and not toks & _claims._CALENDAR:
                places.append((place, toks))
        if not places:
            return None, ""
        match = any(slot["object_tokens"] <= toks for _, toks in places)
        if claim.polarity < 0:
            return ("contradict" if match else None), "denies the place" if match else ""
        rel = "support" if match else "contradict"
        why = "" if match else f"places it in {' and '.join(dict.fromkeys(p for p, _ in places))}, " \
                                 f"not {slot['object']}"
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
            return None, ""                  # a negation elsewhere in the sentence: its scope is unknown
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


def _verdict(statement: str, clusters: list[ClaimCluster], sources: _Sources, *, now: float, cls: str,
             ttl_overrides: dict | None = None) -> dict:
    """Fact verification by claim-slot checking (section comment above). Every reportable, non-adversarial claim
    is classified against the statement; claims that a listed contradiction pits against a supporting claim count
    as contradicting too, so the verdict never says "0 contradict" next to a contradiction of the checked
    statement. Independent groups weigh in as in claims.evaluate."""
    slot = parse_statement(statement)
    base = {"statement": statement, "shape": slot["shape"] if slot else None}
    if slot is None:
        return dict(base, label="insufficient", confidence=0.5, citations=[], supporting=[], contradicting=[],
                    reasons=["The statement's shape is not one the lexical checker supports (X is in Y; X is the "
                             "capital of Y; X has N <unit>; X was released in DATE; X is Y; X <verb> ... DATE); "
                             "no source was counted."])
    pool = {}
    for c in clusters:
        for x in c.claims:
            if _claims.is_reportable(x):
                pool.setdefault(x.id, x)
    relation, why = {}, {}
    for x in pool.values():
        rel, reason = _check(slot, x)
        if rel:
            relation[x.id], why[x.id] = rel, reason
    for c in clusters:
        for pair in c.contradicting:
            for mine, other in ((pair.get("a"), pair.get("b")), (pair.get("b"), pair.get("a"))):
                if relation.get(mine) == "support" and other in pool and other not in relation:
                    relation[other] = "contradict"
                    why[other] = pair.get("reason", "conflicts with a supporting source")
    order = sorted((pool[i] for i in relation), key=lambda x: (-x.relevance, x.source_url, x.sentence_index, x.id))
    groups = _claims.independence_groups(order)
    by_id = {x.id: x for x in order}
    alpha, beta, sup, con = _claims.PRIOR_ALPHA, _claims.PRIOR_BETA, 0, 0
    outvoted = set()
    for g in groups:
        members = [by_id[i] for i in g]
        a = sum(1 for x in members if relation[x.id] == "support")
        w = max(_claims.evidence_weight(x, now=now, freshness_class=cls, ttl_overrides=ttl_overrides)
                for x in members)
        alpha += w * a / len(members)
        beta += w * (len(members) - a) / len(members)
        if a and a >= len(members) - a:
            sup += 1
            outvoted |= {x.id for x in members if relation[x.id] == "contradict"}
        else:
            con += 1
    mean = alpha / (alpha + beta)
    reasons = [f"{sup} independent source group(s) agree with the statement; {con} contradict it.",
               f"Posterior mean {mean:.2f} from a Beta(1,1) prior and weighted independent groups."]
    for x in order:
        if relation[x.id] == "contradict" and len(reasons) < 6:
            within = " (a sentence in a source group that otherwise supports it)" if x.id in outvoted else ""
            reasons.append(f"[{sources.cite(x.source_url, x)}]{within} {why[x.id] or 'contradicts the statement'}.")
    if mean >= SUPPORTED_MIN_CONFIDENCE and sup >= SUPPORTED_MIN_GROUPS and con == 0:
        label = "supported"
    elif (beta - _claims.PRIOR_BETA) > (alpha - _claims.PRIOR_ALPHA):
        label = "disputed"
    elif sup >= 1 and con >= 1:
        label = "contested"
    else:
        label = "insufficient"
        if not order:
            reasons.append("No source sentence names the subject together with the checked relation.")
        elif sup < SUPPORTED_MIN_GROUPS:
            reasons.append(f"Fewer than {SUPPORTED_MIN_GROUPS} independent source groups support it.")
    citations = sorted({sources.cite(x.source_url, x) for x in order})
    return dict(base, label=label, confidence=round(mean, 3), citations=citations,
                supporting=[x.id for x in order if relation[x.id] == "support"],
                contradicting=[x.id for x in order if relation[x.id] == "contradict"], reasons=reasons)


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
    md = doc.metadata or {}
    if any(md.get(k) for k in ("doi", "arxiv_id", "pmid", "abstract", "venue", "journal")):
        return True
    dom = (doc.domain or "").lower()
    return any(dom == a or dom.endswith("." + a) for a in ACADEMIC_DOMAINS)


def _methodology(documents: list[Document], sources: _Sources) -> list[dict]:
    out = []
    for d in documents:
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
    required = float(window) if window else class_ttl
    sources = _Sources(documents)

    scored = [_claims.score(c, now=now, freshness_class=cls, ttl_overrides=ttl_overrides)
              for c in clusters if c.claims]
    usable = [c for c in scored if c.status != "insufficient"]

    def value(c: ClaimCluster) -> float:
        rep = _representative(c)
        ok = temporal.window_ok(_claim_fresh_ts(rep), required, now) if rep else None
        factor = 1.0 if ok else (UNDATED_VALUE if ok is None else STALE_VALUE)
        return c.confidence * (rep.relevance if rep else 0.0) * factor

    ranked = sorted(((c, value(c)) for c in usable if report_safe_text(_representative(c)) == c.statement),
                    key=lambda t: (-t[1], t[0].key, t[0].statement))
    old = _superseded(ranked)
    if old:      # a newer release of the same package was read: older ones follow it (C-02)
        flagged = [(c, v, i in old) for i, (c, v) in enumerate(ranked)]
        ranked = [(c, v) for c, v, _ in sorted(flagged, key=lambda t: t[2])]
        old_ids = {id(c) for c, v, is_old in flagged if is_old}
    else:
        old_ids = set()

    # answer items (quotes) first: they get the lowest source numbers
    items = []
    for c, v in ranked[:MAX_ANSWER_ITEMS]:
        rep = _representative(c)
        copies, independent = _support_counts(c)
        items.append({"text": c.statement, "type": "template" if rep and rep.kind == "metadata" else "quote",
                      "citations": _cite_cluster(c, sources), "confidence": c.confidence,
                      "uncertainty": c.uncertainty, "status": c.status, "copies": copies,
                      "independent_sources": independent, "value": round(v, 6)})

    packages = assessment.get("packages") or []
    findings = []
    for c, v in ranked:
        rep = _representative(c)
        copies, independent = _support_counts(c)
        finding = {"statement": c.statement,
                   "type": "template" if rep and rep.kind == "metadata" else "quote",
                   "kind": rep.kind if rep else "statement", "key": c.key,
                   "citations": _cite_cluster(c, sources), "related": [], "confidence": c.confidence,
                   "uncertainty": c.uncertainty, "status": c.status, "copies": copies,
                   "independent_sources": independent, "value": round(v, 6),
                   "published_at": rep.published_at if rep else None, "event_at": rep.event_at if rep else None,
                   "claims": [x.id for x in c.claims], "package": _package_of(c, packages)}
        if id(c) in old_ids:
            finding["superseded"] = True
        findings.append(finding)
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
            contradictions.append(_contradiction(a, b, pair["reason"], c, sources))

    # freshness and liveness over cited sources
    cited = sources.cited_docs()
    stamps = [t for t in (_fresh_ts(d) for d in cited) if t is not None]
    newest = max(stamps) if stamps else None
    satisfied = any(temporal.window_ok(_fresh_ts(d), required, now) for d in cited)
    live = any(d.retrieval_mode in LIVE_MODES for d in cited)
    freshness = {"class": cls, "window_seconds": window, "ttl_seconds": class_ttl, "required_seconds": required,
                 "satisfied": bool(satisfied), "newest_source_at": newest}

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
        limitations.append("No claim relevant to the question could be extracted from the sources read")
    limitations.append("Extractive synthesis: statements are quoted from sources, not generated; contradiction "
                       "detection covers numbers, versions, dates, polarity and explicit opposites only (ADR-022)")

    uncertainties: list[str] = []
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
    if mode == "fact_verification" and any(c.claims for c in scored):     # no claims (cancelled): no verdict
        verdict = _verdict(assessment.get("claim") or question, usable, sources, now=now, cls=cls,
                           ttl_overrides=ttl_overrides)
    comparison = _comparison(assessment.get("comparison"), ranked, sources) if mode == "comparative" else None
    methodology = _methodology(documents, sources) if mode == "scientific" else None

    lead_citations = sorted({n for it in items for n in it["citations"]})
    answer = [{"text": _lead(documents, usable, ranked, satisfied, newest, required, now, live, cited),
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
            "comparison": comparison, "methodology": methodology, "usage": usage_json, "budget": None}


def _lead(documents, usable, ranked, satisfied, newest, required, now, live, cited) -> str:
    n_docs = len(documents)
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
    if satisfied:
        text += f" Newest cited source: {temporal.describe_age(newest, now)}."
    else:
        text += f" No cited source is dated within the required {_span(required)}; these statements may be out of date."
    if cited and not live:
        text += " All cited sources were served from cache."
    return text


def _side(x: Claim, sources: _Sources) -> dict:
    return {"id": x.id, "text": x.text, "type": "template" if x.kind == "metadata" else "quote",
            "citation": sources.cite(x.source_url, x), "domain": x.domain, "published_at": x.published_at,
            "event_at": x.event_at, "source_type": "primary" if _claims.is_primary(x) else "secondary",
            "kind": x.kind}


def _contradiction(a: Claim, b: Claim, reason: str, c: ClaimCluster, sources: _Sources) -> dict:
    sa, sb = _side(a, sources), _side(b, sources)
    na, nb = sa["citation"], sb["citation"]
    parts = []

    def dated(n: int, x: Claim) -> str:
        if x.published_at is None and x.event_at is None:
            return f"[{n}] is undated"
        out = f"[{n}] published {_day(x.published_at)}" if x.published_at is not None else f"[{n}] undated"
        return out + (f" (event {_day(x.event_at)})" if x.event_at is not None else "")

    parts.append(f"{dated(na, a)}; {dated(nb, b)}.")
    if sa["source_type"] != sb["source_type"]:
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
    if value_change and (temporal.supersedes(da, db) or temporal.supersedes(db, da)):
        newer_n = na if temporal.supersedes(da, db) else nb
        parts.append(f"Leaning: [{newer_n}] (basis: its effective date is later and it reports a different value "
                     f"for the same subject; a newer value may supersede an older one for time-varying facts). "
                     f"Not independently confirmed.")
    else:
        parts.append(UNRESOLVED)
    return {"a": sa, "b": sb, "reason": reason, "description": _claims.describe_conflict(a, b, reason),
            "assessment": " ".join(parts)}


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


__all__ = ["LIVE_MODES", "MAX_ANSWER_ITEMS", "UNRESOLVED", "context_pack", "parse_statement", "report_safe_text",
           "synthesize"]
