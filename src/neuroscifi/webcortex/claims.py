"""Claims: extraction from documents, clustering, source independence, contradiction and confidence.

Deterministic and lexical (ADR-022). A claim is one source sentence (or a templated statement over a structured API
record); nothing is paraphrased. Claims cluster only when their subjects are compatible (primary subject or record
identity, see subjects_compatible); inside a cluster each claim supports the representative (restates it: agrees),
contradicts it, or is merely related (cited nowhere, no evidence). Confidence is a Beta posterior over "the
cluster's representative statement holds", updated once per *independent* source group that bears on it. Search rank, provider score, popularity and domain reputation are never
read here: they cannot raise confidence. The only source-level prior is the documented source *type* constant
(primary structured record vs. secondary page).

Constants
---------
MIN_WORDS, MAX_WORDS            sentence length bounds for a claim (shorter = fragment, longer = run-on/boilerplate)
TITLE_PRIOR_WEIGHT              weight of the document title as a weak relevance prior
VALUE_BONUS, DATE_BONUS         relative relevance bonus for sentences that carry values / dated events
RELEVANCE_HALF                  raw score mapped to relevance 0.5 (relevance = raw / (raw + RELEVANCE_HALF))
METADATA_RELEVANCE_FLOOR        package/advisory/release records fetched for the task are relevant by construction
KEY_TOKENS                      size of the subject signature (claim key)
KEY_WEIGHT_*                    token scores for the key: question term, entity-like, noun-like, other
CLUSTER_KEY_JACCARD             key-token Jaccard needed to join a cluster
CLUSTER_TEXT_SHINGLE            or: shingle Jaccard of normalised texts
SUPPORT_TEXT_SHINGLE            shingle Jaccard of normalised texts above which one claim restates another (support)
YEAR_ONLY_KEY_JACCARD           a conflict that is only about years needs this key overlap (or near-identical text):
                                different years usually date different events ("started in 2020" vs "1.0 in 2015")
DUPLICATE_SHINGLE               shingle Jaccard above which two texts are copies (syndication) -> one source group
SHINGLE_K                       words per shingle for sentence comparisons (sentences are short; 3 not 5)
PRIOR_ALPHA, PRIOR_BETA         Beta(1, 1) prior
RELIABILITY_SECONDARY           evidence weight of an ordinary page
RELIABILITY_PRIMARY             evidence weight of a primary structured record (arXiv/PubMed/OSV/PyPI/... API)
STALE_FACTOR, UNDATED_FACTOR    temporal multipliers on a group's weight (adversarial sources: weight 0, see evaluate)
HARD_LINE_KINDS                 document kinds whose line breaks end sentences (HTML pages, feeds, API records)
MAX_CONTRADICTION_PAIRS         bound on listed contradiction pairs per cluster
"""
from __future__ import annotations

from datetime import datetime, timezone
import functools
import hashlib
import math
import re

from . import safety, temporal
from . import text as T
from .types import Claim, ClaimCluster, Document

MIN_WORDS = 6
MAX_WORDS = 80
TITLE_PRIOR_WEIGHT = 0.3
VALUE_BONUS = 0.15
DATE_BONUS = 0.10
RELEVANCE_HALF = 2.0
METADATA_RELEVANCE_FLOOR = 0.5
KEY_TOKENS = 4
KEY_WEIGHT_QUESTION = 3.0
KEY_WEIGHT_ENTITY = 2.0
KEY_WEIGHT_NOUN = 1.0
KEY_WEIGHT_OTHER = 0.5
CLUSTER_KEY_JACCARD = 0.5
CLUSTER_TEXT_SHINGLE = 0.6
SUPPORT_TEXT_SHINGLE = 0.6
YEAR_ONLY_KEY_JACCARD = 0.75
SHOUT_SHARE = 0.6                     # share of ALL-CAPS words above which a line is a heading/shout, not a claim
DUPLICATE_SHINGLE = 0.8
SHINGLE_K = 3
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0
RELIABILITY_SECONDARY = 0.8
RELIABILITY_PRIMARY = 0.9
STALE_FACTOR = 0.5
UNDATED_FACTOR = 0.75
# Classes where the age of a source lowers the chance its statement is still true. Scientific findings and stable
# knowledge do not become less true by being old; for them staleness means "re-verify", which temporal.is_stale
# reports on the knowledge item, not a discount on the evidence.
AGE_DISCOUNT_CLASSES = ("breaking", "volatile", "release")
MAX_CONTRADICTION_PAIRS = 50
HARD_LINE_KINDS = ("page", "feed", "api")     # document kinds whose line breaks are block boundaries

# ---- lexical cues -------------------------------------------------------------------------------------------

_BOILERPLATE = re.compile(
    r"\b(?:cookies?|subscribe|subscription|newsletter|sign (?:up|in)|log ?in|all rights reserved|copyright|"
    r"privacy policy|terms of (?:use|service)|share (?:this|on)|follow us|click here|read more|advertisement|"
    r"skip to (?:main )?content|enable javascript|javascript is disabled|accept (?:all )?cookies|related articles|"
    r"back to top|sponsored content|download (?:the|our) app)\b|©", re.IGNORECASE)
_NAV = re.compile(r"(?:\s[|»›>/]\s.*){2,}")
_FINDING = re.compile(
    r"\b(?:we|they|researchers|scientists|authors|investigators|the (?:study|trial|team|analysis|survey|data|"
    r"authors|researchers|experiment))\s+(?:\w+\s+)?(?:found|find|finds|showed|show|shows|demonstrated|"
    r"demonstrate|demonstrates|reported|reports|observed|observe|measured|recorded|detected|identified|confirmed)\b"
    r"|\bresults?\s+(?:show|showed|shows|reveal|revealed|demonstrate|demonstrated|confirm|confirmed)\b"
    r"|\b(?:was|were|is|are)\s+(?:measured|observed|recorded|detected)\b"
    r"|\b(?:increased|decreased|rose|fell|dropped|declined|grew|improved|reduced)\s+by\s+[\d.]+"
    r"|\bn\s*=\s*\d+|\bp\s*[<=>≤]\s*0?\.\d+|\bstatistically significant\b"
    r"|\b9[059]\s*%\s*(?:ci|confidence interval)\b", re.IGNORECASE)
_HEDGE = re.compile(
    r"\b(?:suggests?|suggested|suggesting|might|could(?!\s+not|n't)|we propose|proposed that|"
    r"we hypothesi[sz]e|hypothesi[sz]ed|indicates? that|indicated that|likely|unlikely|possibly|possible that|"
    r"perhaps|appears? to|appeared to|seems? to|seemed to|potentially|speculate\w*|we believe|"
    r"(?:is|are) thought to|presumably|implies|imply)\b", re.IGNORECASE)
# "may" hedges only as a modal: lower-case before a word, or sentence-initial "May" before a verb-ish word. The month
# ("In May 2026 the trial found ...", "May the 4th", "on May 3") is not a hedge.
_MAY_MODAL = re.compile(
    r"(?-i:\bmay)\s+(?!(?:the|a|an|of|and|or|to|in|on|at|through|until|this|last|next|each|every)\b)[a-z]"
    r"|^\W*May\s+(?!(?:the|a|an|of|and|or|to|in|on|at|through|until|this|last|next|each|every)\b)[a-z]")
_NEGATION = re.compile(
    r"\b(?:not|no|never|neither|nor|cannot|can't|won't|didn't|doesn't|don't|isn't|aren't|wasn't|weren't|hasn't|"
    r"haven't|hadn't|couldn't|shouldn't|wouldn't|fails?\s+to|failed\s+to|without\s+evidence|false|debunked|"
    r"refuted|unfounded|disproved|disproven|denied|denies)\b", re.IGNORECASE)
_NEGATION_EXEMPT = re.compile(
    r"\b(?:not only|not least|no doubt|no later than|no sooner|no less than|no more than|whether or not|"
    r"nothing but|not just|no\.?\s*\d)", re.IGNORECASE)
_SUBORDINATE_START = re.compile(
    r"^(?:although|though|while|whereas|even if|even though|if|because|despite|unless|whether|after|before|"
    r"when|since|contrary to|unlike)\b", re.IGNORECASE)
_CLAUSE_CUT = re.compile(
    r",\s*(?:which|although|though|while|whereas|but|unlike|despite|according)\b|;\s|\s(?:although|whereas|"
    r"because|unless)\s", re.IGNORECASE)

# (label, side A, side B): a conflict when one claim uses only side A and the other only side B.
_CUE_PAIRS = (
    ("increase vs decrease",
     re.compile(r"\b(?:increase[sd]?|increasing|rise|rises|rising|rose|risen|grew|grow|grows|growing|gain(?:ed|s)?|"
                r"higher|more than doubled|surged?)\b", re.I),
     re.compile(r"\b(?:decrease[sd]?|decreasing|fall|falls|falling|fell|fallen|drop(?:ped|s)?|decline[sd]?|"
                r"declining|lower|reduc(?:e|es|ed|tion)|shrank|plunged?)\b", re.I)),
    ("supports vs refutes",
     re.compile(r"\b(?:supports?|supported|confirms?|confirmed|consistent with|corroborate[sd]?)\b", re.I),
     re.compile(r"\b(?:refutes?|refuted|contradicts?|contradicted|inconsistent with|debunk\w*)\b", re.I)),
    ("approved vs rejected",
     re.compile(r"\b(?:approved|approves|authori[sz]ed|cleared|granted)\b", re.I),
     re.compile(r"\b(?:rejected|rejects|refused|banned|withdrawn|revoked)\b", re.I)),
    ("safe vs unsafe",
     re.compile(r"\bsafe\b", re.I),
     re.compile(r"\b(?:unsafe|dangerous|harmful|toxic)\b", re.I)),
    ("effective vs ineffective",
     re.compile(r"\b(?:effective|efficacious)\b", re.I),
     re.compile(r"\b(?:ineffective|no effect|no benefit)\b", re.I)),
    ("true vs false",
     re.compile(r"\b(?:true|accurate|correct)\b", re.I),
     re.compile(r"\b(?:false|inaccurate|incorrect|fake|hoax)\b", re.I)),
)

_KEY_EXCLUDE = frozenset("""
not no never neither nor cannot fail fails failed false debunked refuted unfounded disproved disproven denied denies
without evidence suggest suggests suggested suggesting might could would should likely unlikely possibly perhaps
potentially appear appears appeared seem seems seemed propose proposed hypothesize hypothesise hypothesized indicate
indicates indicated imply implies presumably speculate thought believe
increase increased increases increasing rise rises rose risen rising grow grew grows growing gain gained gains
higher lower decrease decreased decreases decreasing fall falls fell fallen falling drop dropped drops decline
declined declines reduce reduced reduces reduction improve improved worsen worsened surge surged plunge plunged
said say says report reported reports reporting according announce announced announces confirm confirmed confirms
found find finds show showed shows shown demonstrate demonstrated study studies researcher researchers scientist
scientists new also according statement article news source sources percent cent percentage points
today yesterday tomorrow tonight week weeks day days month months year years ago last recently recent earlier
currently now time monday tuesday wednesday thursday friday saturday sunday morning evening afternoon
january february march april may june july august september october november december jan feb mar apr jun jul aug
sep sept oct nov dec approved rejected safe unsafe effective ineffective true supports refutes
""".split())
_KEY_EXCLUDE_STEMS = frozenset(T.stem(w) for w in _KEY_EXCLUDE)
_NOUN_SUFFIX = ("tion", "sion", "ment", "ness", "ity", "ance", "ence", "ism", "ist", "age", "ure", "ship", "er",
                "or", "logy", "ics", "um", "us")

_PROVIDER_LABELS = (("pypi.org", "PyPI"), ("npmjs.org", "npm"), ("npmjs.com", "npm"), ("osv.dev", "OSV"),
                    ("github.com", "GitHub"), ("arxiv.org", "arXiv"), ("ncbi.nlm.nih.gov", "PubMed"),
                    ("pubmed", "PubMed"), ("crossref.org", "Crossref"), ("openalex.org", "OpenAlex"),
                    ("wikipedia.org", "Wikipedia"), ("biorxiv.org", "bioRxiv"), ("medrxiv.org", "medRxiv"))
_ACADEMIC_PROVIDERS = ("arXiv", "PubMed", "Crossref", "OpenAlex", "bioRxiv", "medRxiv")
REGISTRY_PROVIDERS = ("PyPI", "npm", "OSV", "GitHub")
REGISTRY_KEYS = ("latest", "latest_version", "ecosystem", "tag", "tag_name", "advisory_id", "osv_id")


# ---- helpers ------------------------------------------------------------------------------------------------

def _stems(s: str) -> list[str]:
    return [T.stem(t) for t in T.content_tokens(s)]


def _claim_id(source_url: str, text: str) -> str:
    """sha256(canonical URL + "\n" + the sentence lower-cased with whitespace collapsed)[:24]. Numbers are kept, so
    two sentences on one page that differ only in a value are two claims (the subject `key` masks values)."""
    return hashlib.sha256((source_url + "\n" + T.collapse(text).lower()).encode("utf-8")).hexdigest()[:24]


def is_adversarial(claim: Claim) -> bool:
    """From a document with prompt-injection findings: shown, but evidence weight 0 (see evaluate)."""
    return bool((claim.flags or {}).get("adversarial_source"))


RENDER_ONLY_PATTERNS = ("markdown_beacon",)


def adversarial_document(doc: Document) -> bool:
    """A document with instruction-like content: any injection finding except an image beacon found only in the
    Markdown rendering. Claims come from `text`, which has no images, and quarantine already cut the beacon from
    the Markdown; a page's own thumbnails with tracking parameters (every Wikipedia article) would otherwise make
    the whole page adversarial."""
    for f in (doc.flags or {}).get("injection") or []:
        if not isinstance(f, dict) or f.get("pattern") not in RENDER_ONLY_PATTERNS or f.get("field") != "markdown":
            return True
    return False


def is_primary(claim: Claim) -> bool:
    """A primary structured record (API-derived) rather than a secondary page."""
    return claim.kind == "metadata" or (claim.flags or {}).get("source_type") == "primary"


def _main_clause(sentence: str) -> str:
    s = sentence.strip()
    if _SUBORDINATE_START.match(s) and "," in s:
        s = s.split(",", 1)[1]
    cut = _CLAUSE_CUT.search(s)
    return s[:cut.start()] if cut else s


# Code, not prose: `inline code`, identifiers and calls (verify=False, requests.Session(), extract_zipped_paths),
# and the literals False/None/True/null/nil/NaN/undefined. Negation is read from prose only (W-03).
_CODE = re.compile(
    r"`[^`]*`"
    r"|[^\s`]*(?:=|\(\)|[A-Za-z0-9]_[A-Za-z0-9]|[a-z0-9]\.[A-Za-z_]\w*\(|::|->)[^\s`]*"
    r"|(?-i:\b(?:False|None|True|null|nil|NaN|undefined)\b)")


def _prose(clause: str) -> str:
    return _CODE.sub(" ", clause)


def polarity(sentence: str) -> int:
    """-1 when the main clause is negated in prose ("did not", "no evidence", "false", "debunked"), else +1. Code
    literals and identifiers (`verify=False`, None, null) are not negation."""
    clause = _NEGATION_EXEMPT.sub(" ", _prose(_main_clause(sentence)))
    return -1 if _NEGATION.search(clause) else 1


def negation_cue(sentence: str) -> str | None:
    m = _NEGATION.search(_NEGATION_EXEMPT.sub(" ", _prose(_main_clause(sentence))))
    return m.group(0) if m else None


def fragment(sentence: str) -> bool:
    """True for text that is not a self-contained sentence: it starts inside or right after a quotation or with
    closing punctuation (a split at a quotation mark lost its subject: 'security releases " in October 2026'), its
    double quotes do not balance, or it is mostly ALL-CAPS words (a heading or a shout)."""
    s = sentence.strip()
    if not s or s[0] in "”’)]},;:.!?" or s.count('"') % 2 or s.count("“") != s.count("”"):
        return True
    words = [w for w in re.findall(r"[^\W\d_]{2,}", s)]
    caps = [w for w in words if w.isupper()]
    return len(words) >= 4 and len(caps) >= SHOUT_SHARE * len(words)


def claim_kind(sentence: str) -> str:
    """'interpretation' for hedged/inferential language (wins over result cues: a hedged result is not a finding),
    'finding' for reported results, else 'statement'. API-derived templates are 'metadata' (set by the caller)."""
    if _HEDGE.search(sentence) or _MAY_MODAL.search(sentence):
        return "interpretation"
    if _FINDING.search(sentence):
        return "finding"
    return "statement"


def claim_key(sentence: str, question_stems=()) -> str:
    """Subject signature: the KEY_TOKENS best content tokens (stemmed), sorted. Values, negations, hedges,
    change-direction words, reporting verbs, units and time words are excluded so that two sentences about the
    same subject with different numbers or opposite direction get the same (or a highly similar) key."""
    question_stems = set(question_stems)
    raw = T.tokens(sentence, keep_case=True)
    scores: dict[str, tuple[float, int]] = {}
    for i, tok in enumerate(raw):
        low = tok.lower()
        if low in T.STOPWORDS or T.is_version(low) or T.is_number(low) or len(low) < 2:
            continue
        st = T.stem(low)
        if low in _KEY_EXCLUDE or st in _KEY_EXCLUDE_STEMS:
            continue
        score = KEY_WEIGHT_OTHER
        entity = (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)) or \
                 (tok[:1].isupper() and i > 0) or (len(tok) > 1 and tok.isupper())
        if st in question_stems:
            score += KEY_WEIGHT_QUESTION
        if entity:
            score += KEY_WEIGHT_ENTITY
        elif low.endswith(_NOUN_SUFFIX):
            score += KEY_WEIGHT_NOUN
        if st not in scores or score > scores[st][0]:
            scores[st] = (score, scores.get(st, (0, i))[1])
    best = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:KEY_TOKENS]
    return " ".join(sorted(k for k, _ in best))


def _provider(doc: Document) -> str:
    md = doc.metadata or {}
    label = md.get("provider") or md.get("source")
    if isinstance(label, str) and label.strip():
        for _, nice in _PROVIDER_LABELS:
            if label.strip().lower() == nice.lower():
                return nice
        return label.strip()[:40]
    host = (doc.domain or "").lower()
    for needle, nice in _PROVIDER_LABELS:
        if needle in host:
            return nice
    return doc.domain or "API"


def _day(value) -> str | None:
    if value is None or value == "":
        return None
    ts = float(value) if isinstance(value, (int, float)) else temporal.parse_date(str(value))
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _clip(value, limit: int) -> str:
    s = T.collapse(str(value))
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def registry_record(doc: Document) -> bool:
    """A structured record of registry *state* (package, advisory, release: PyPI, npm, OSV, GitHub). Its content
    is current as of retrieval; encyclopedia and academic records are dated by their own published or
    last-edited date instead."""
    if doc.kind != "api" and doc.retrieval_mode != "api":
        return False
    md = doc.metadata or {}
    return _provider(doc) in REGISTRY_PROVIDERS or any(md.get(k) for k in REGISTRY_KEYS)


def _metadata_statements(doc: Document) -> list[tuple[str, dict, bool]]:
    """Templated statements over a structured record: [(text, extra flags, relevance floor applies)]."""
    md = doc.metadata or {}
    provider = _provider(doc)
    out: list[tuple[str, dict, bool]] = []
    name = md.get("package") or md.get("name")
    version = md.get("version") or md.get("latest_version") or md.get("latest")     # PyPI/npm providers: latest
    released = _day(md.get("released_at") or md.get("release_date") or md.get("upload_time"))
    advisory = md.get("advisory_id") or md.get("osv_id") or (md.get("id") if provider == "OSV" else None)
    if advisory:
        aliases = md.get("aliases") or []
        alias = f" ({', '.join(_clip(a, 40) for a in list(aliases)[:3])})" if aliases else ""
        target = f" for {_clip(name, 80)}" if name else ""
        summary = md.get("summary") or md.get("details") or ""
        text = f"{provider} lists advisory {_clip(advisory, 60)}{alias}{target}"
        if summary:
            text += f": {_clip(summary, 200).rstrip('.')}"
        fixed = md.get("fixed") or md.get("fixed_versions")
        if fixed:
            fixed = fixed if isinstance(fixed, (list, tuple)) else [fixed]
            text += f"; fixed in version {', '.join(_clip(v, 30) for v in list(fixed)[:3])}"
        published = _day(md.get("published") or md.get("modified")) or _day(doc.published_at)
        if published:
            text += f"; published {published}"
        out.append((text + ".", {"package": str(name) if name else None, "record": "advisory",
                                 "record_id": _record_ids(doc, "advisory", advisory=advisory, aliases=aliases)},
                    True))
    elif md.get("tag") or md.get("tag_name"):
        tag = md.get("tag") or md.get("tag_name")
        repo = md.get("repo") or md.get("repository") or name or doc.title
        text = f"{provider} lists release {_clip(tag, 60)} of {_clip(repo, 80)}"
        when = released or _day(doc.published_at)
        if when:
            text += f", published {when}"
        out.append((text + ".", {"package": str(repo), "record": "release",
                                 "record_id": _record_ids(doc, "release", tag=tag, repo=repo)}, True))
    elif name and version:
        text = f"{provider} reports {_clip(name, 80)} latest version {_clip(version, 40)}"
        if released:
            text += f" released {released}"
        out.append((text + ".", {"package": str(name), "record": "package",
                                 "record_id": _record_ids(doc, "package", name=name)}, True))
    if provider in _ACADEMIC_PROVIDERS or md.get("doi") or md.get("arxiv_id"):
        title = md.get("title") or doc.title
        if title:
            text = f"{provider} record: \"{_clip(title, 200)}\""
            rev = md.get("version") if provider == "arXiv" else None
            if rev:
                text += f" ({_clip(rev, 10)})"
            when = _day(md.get("published") or md.get("date")) or _day(doc.published_at)
            if when:
                text += f", dated {when}"
            if md.get("preprint") or provider in ("arXiv", "bioRxiv", "medRxiv"):
                text += ", a preprint (not peer reviewed)"
            out.append((text + ".", {"record": "academic", "record_id": _record_ids(doc, "academic", title=title)},
                        False))
    return out


# ---- subjects and record identities ------------------------------------------------------------------------
#
# A claim's *primary subject* is the first named-entity-like span of its main clause that starts before the main
# verb: a run of capitalised words (connectors such as "of", "de", "for" allowed inside: "Champ de Mars", "Rust for
# Linux"), a CamelCase or alphanumeric token ("NumPy", "H100", "CVE-2024-35195") or an acronym ("ESA"). Reporting
# frames are skipped ("The agency confirmed that X ...", "According to Y, X ..."), a possessor names the owner,
# not the subject ("ESA's Hera probe" -> Hera), and a sentence that starts with a pronoun ("It is ...") is about
# the subject of its document's title ("Mojo (programming language)" -> Mojo; a title qualifier naming a place,
# "Eiffel Tower (Paris, Texas)", stays part of the subject). A structured record's subject is its *identity*
# (DOI / arXiv id / PMID / title for academic records, advisory id and aliases, repository + tag, ecosystem +
# package). Clustering, support and contradiction all require compatible subjects (subjects_compatible).

_PRONOUN_START = re.compile(r"^\W*(?:it|its|this|these|those|they|their|he|she|his|her)\b", re.IGNORECASE)
_REPORTING_FRAME = re.compile(
    r"^.{0,120}?\b(?:said|says|say|reported|reports|confirmed|confirms|announced|announces|stated|states|noted|"
    r"notes|found|finds|showed|shows|argued|argues|claimed|claims|estimated|estimates|wrote|writes|believe|"
    r"believes)\s+that\s+", re.IGNORECASE)
_ACCORDING = re.compile(r"^\W*according\s+to\s+[^,]{1,80},\s*", re.IGNORECASE)
_LEAD_PHRASE = re.compile(
    r"^\W*(?:in|on|at|by|for|from|during|after|before|since|under|over|with|as|throughout|within|across|among|"
    r"following|despite|unlike|until|by)\b[^,]{0,80},\s*", re.IGNORECASE)
_CONNECTORS = frozenset("of de du des la le von van der den del da di y for upon am sur".split())
_DISCOURSE = frozenset("""
overall moreover furthermore additionally finally first second third notably importantly meanwhile instead hence
indeed similarly conversely specifically ultimately initially previously here still yet however thus therefore
nevertheless nonetheless consequently accordingly besides otherwise likewise yes no so but and or then
""".split())
_CALENDAR = frozenset(list(T.MONTHS) + "monday tuesday wednesday thursday friday saturday sunday".split())
_AUX = frozenset("is are was were be been being am has have had will would can could may might shall should must "
                 "does do did remains remain became becomes become isn't aren't wasn't weren't hasn't haven't "
                 "hadn't won't can't doesn't don't didn't".split())
_VERB_CUE = re.compile(
    r"^(?:says|said|shows|ships|runs|uses|makes|gets|adds|brings|supports|reports|reaches|opens|launches|offers|"
    r"includes|contains|provides|requires|remains|becomes|takes|gives|lists|drops|rises|falls|grows|holds|leads|"
    r"wins|loses|lies|flows|drains|needs|means|stands|sits|costs|weighs|measures|carries|has|have|had|began|"
    r"begins|became|chose|found|made|took|gave|grew|rose|fell|won|lost|led|held|built|ran|"
    r"[a-z]{2,}ed)$", re.IGNORECASE)
_WORD = re.compile(r"[^\W_](?:[\w'’.\-]*[^\W_])?")
_POSSESSIVE = re.compile(r"['’]s?$", re.IGNORECASE)
_TITLE_QUALIFIER = re.compile(r"^(?P<base>.*?)\s*\((?P<qual>[^()]{1,80})\)\s*$")
_TITLE_SPLIT = re.compile(r"\s+[|–—-]\s+|:\s+")


def _bare(tok: str) -> str:
    return _POSSESSIVE.sub("", tok) if len(tok) > 2 else tok


def _entity_token(tok: str) -> bool | None:
    """True for an entity-like token, False for an ordinary word, None for a capitalised word whose status
    depends on its position (sentence-initial words are capitalised anyway)."""
    core = _bare(tok)
    low = core.lower()
    if not core or T.is_version(low) or T.is_number(low) or re.fullmatch(r"\d+(?:st|nd|rd|th|[a-z])?", low):
        return False
    if low in _CALENDAR or low in ("i", "a"):
        return False
    if any(c.isdigit() for c in core) and any(c.isalpha() for c in core):
        return True
    letters = [c for c in core if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return True
    if core[:1].isalpha() and any(c.isupper() for c in core[1:]) and not core[1:].isupper():
        return True                                                   # CamelCase: NumPy, iPhone, eBay
    if core[:1].isupper():
        return None
    return False


def _initial_is_entity(words: list[tuple[str, int, int]], text: str) -> bool:
    """A capitalised first word is a name when it is part of a multi-word name, stands in apposition ("Hera, the
    ESA probe, ...") or is directly followed by a verb ("Zig is", "Hera reached")."""
    tok = _bare(words[0][0])
    low = tok.lower()
    if low in T.STOPWORDS or low in _KEY_EXCLUDE or low in _DISCOURSE or low.endswith(("ly", "ing")):
        return False
    if len(words) == 1:
        return True
    nxt = words[1][0]
    gap = text[words[0][2]:words[1][1]]
    if gap.strip() == ",":
        return True
    if gap.strip():
        return False
    if _entity_token(nxt) is not False and nxt[:1].isupper():
        return True
    if nxt.lower() in _CONNECTORS and len(words) > 2 and words[2][0][:1].isupper() and \
            not text[words[1][2]:words[2][1]].strip():
        return True
    return nxt.lower() in _AUX or bool(_VERB_CUE.match(nxt))


def _spans(clause: str) -> tuple[list[tuple[str, int, bool]], int | None]:
    """([(span text, start word index, is_possessor)], index of the first verb-like word or None)."""
    words = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(clause)]
    if not words:
        return [], None
    flags = []
    for i, (w, _, _) in enumerate(words):
        e = _entity_token(w)
        if e is None:
            e = _initial_is_entity(words, clause) if i == 0 else True
        flags.append(bool(e))
    verb = next((i for i, (w, _, _) in enumerate(words)
                 if i > 0 and not flags[i] and (w.lower() in _AUX or _VERB_CUE.match(w))), None)
    spans, i = [], 0
    while i < len(words):
        if not flags[i]:
            i += 1
            continue
        j = i
        while True:
            if re.search(r"['’]s?$", words[j][0]) and len(words[j][0]) > 2:
                break                                                   # a possessor ends the span
            nxt = j + 1
            if nxt < len(words) and flags[nxt] and not clause[words[j][2]:words[nxt][1]].strip():
                j = nxt
                continue
            if nxt + 1 < len(words) and words[nxt][0].lower() in _CONNECTORS and flags[nxt + 1] and \
                    not clause[words[j][2]:words[nxt + 1][1]].replace(words[nxt][0], "").strip():
                j = nxt + 1
                continue
            break
        text = clause[words[i][1]:words[j][2]]
        possessor = bool(re.search(r"['’]s?$", words[j][0])) and len(words[j][0]) > 2 and j + 1 < len(words) \
            and flags[j + 1]
        spans.append((_POSSESSIVE.sub("", text) if possessor or text.endswith(("'s", "’s")) else text, i,
                      possessor))
        i = j + 1
    return spans, verb


def title_subject(title: str) -> str | None:
    """The subject a document title names: "Mojo (programming language)" -> "Mojo"; a qualifier with a capitalised
    word stays ("Eiffel Tower (Paris, Texas)"); site suffixes after " | ", " - " or ": " are dropped."""
    t = T.collapse(title or "")
    if not t:
        return None
    m = _TITLE_QUALIFIER.match(t)
    if m:
        base, qual = m.group("base"), m.group("qual")
        t = f"{base} ({qual})" if re.search(r"\b[A-Z]", qual) else base
    else:
        t = _TITLE_SPLIT.split(t)[0]
    return t if 0 < len(t.split()) <= 8 else None


def _subject_clause(sentence: str) -> str:
    s = _main_clause(sentence)
    for rx in (_ACCORDING, _REPORTING_FRAME, _LEAD_PHRASE):
        m = rx.match(s)
        if m and len(s) - m.end() > 10:
            s = s[m.end():]
    return s.lstrip(" \"'“‘([")


def primary_subject(sentence: str, title: str = "") -> str | None:
    """The claim's primary subject (see the section comment), or None when the sentence names none before its
    verb ("Most neurons are covered by spines")."""
    clause = _subject_clause(sentence)
    if _PRONOUN_START.match(clause):
        return title_subject(title)
    spans, verb = _spans(clause)
    for text, start, possessor in spans:
        if verb is not None and start >= verb:
            break
        if not possessor:
            return text
    if verb is None:
        return next((t for t, _, p in spans if not p), None)
    return None


def subject_tokens(subject: str | None) -> frozenset:
    """Comparable form of a subject: stemmed lower-case tokens without connectors and stopwords."""
    if not subject:
        return frozenset()
    return frozenset(T.stem(t) for t in T.tokens(subject) if t not in _CONNECTORS and t not in T.STOPWORDS
                     and t not in ("s",))


def same_subject(a: frozenset, b: frozenset) -> bool:
    """Equal token sets, or a one-word name inside the other ("Yuste" / "Rafael Yuste"); "Eiffel Tower" and
    "Eiffel Tower (Paris, Texas)" are different subjects."""
    if not a or not b:
        return False
    return a == b or (len(a) == 1 and a <= b) or (len(b) == 1 and b <= a)


def _norm_id(value) -> str:
    v = str(value or "").strip().lower()
    v = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:|arxiv:|https?://arxiv\.org/abs/|https?://openalex\.org/)", "", v)
    return re.sub(r"v\d+$", "", v) if re.fullmatch(r"\d{4}\.\d{4,5}v\d+", v) else v


def _norm_title(value) -> str:
    return " ".join(T.tokens(str(value or "")))[:200]


def _record_ids(doc: Document, record: str, *, name=None, advisory=None, aliases=(), tag=None, repo=None,
                title=None) -> list[str]:
    md = doc.metadata or {}
    if record == "advisory":
        return [f"advisory:{_norm_id(x)}" for x in [advisory, *list(aliases or [])] if x]
    if record == "release":
        return [f"release:{_norm_id(repo)}@{_norm_id(tag).lstrip('v')}"]
    if record == "package":
        eco = md.get("ecosystem") or _provider(doc)
        return [f"package:{_norm_id(eco)}:{_norm_id(name)}"]
    ids = [f"{k}:{_norm_id(md.get(k))}" for k in ("doi", "arxiv_id", "pmid", "openalex_id") if md.get(k)]
    if title:
        ids.append(f"title:{_norm_title(title)}")
    return ids


_RECORD_TEXT = (
    (re.compile(r"^\S+ lists advisory (\S+?)(?: \(([^)]*)\))?(?: for |: |; |\.$)"), "advisory"),
    (re.compile(r"^\S+ lists release (\S+) of (\S+?)(?:, published|\.$)"), "release"),
    (re.compile(r"^(\S+) reports (\S+) latest version"), "package"),
    (re.compile(r"^\S+ record: \"(.+?)\"(?: \(v\d+\))?(?:, dated|, a preprint|\.$)"), "academic"),
)


def record_identity(claim: Claim) -> frozenset:
    """Identity of the structured record a 'metadata' claim states (flags["record_id"], else parsed from the
    template text); empty for every other claim. Two record claims are about the same record only when their
    identities intersect: two different papers, advisories or releases never corroborate each other."""
    if claim.kind != "metadata":
        return frozenset()
    rid = (claim.flags or {}).get("record_id")
    if rid:
        return frozenset(rid)
    for rx, kind in _RECORD_TEXT:
        m = rx.search(claim.text or "")
        if not m:
            continue
        if kind == "advisory":
            ids = [m.group(1)] + [x.strip() for x in (m.group(2) or "").split(",") if x.strip()]
            return frozenset(f"advisory:{_norm_id(x)}" for x in ids)
        if kind == "release":
            return frozenset({f"release:{_norm_id(m.group(2))}@{_norm_id(m.group(1)).lstrip('v')}"})
        if kind == "package":
            return frozenset({f"package:{_norm_id(m.group(1))}:{_norm_id(m.group(2))}"})
        return frozenset({f"title:{_norm_title(m.group(1))}"})
    return frozenset({f"claim:{claim.id}"})


def _subject_of(claim: Claim) -> frozenset:
    flags = claim.flags or {}
    if "primary_subject" in flags:
        return _subject_cached(flags.get("primary_subject") or "", None)
    return _subject_cached(claim.text or "", claim.title or "")


@functools.lru_cache(maxsize=65536)
def _subject_cached(text: str, title: str | None) -> frozenset:
    return subject_tokens(text if title is None else primary_subject(text, title))


def _stem_set(claim: Claim) -> frozenset:
    return _stems_cached(claim.text or "")


@functools.lru_cache(maxsize=65536)
def _stems_cached(text: str) -> frozenset:
    return frozenset(T.stem(t) for t in T.tokens(text))


def subjects_compatible(a: Claim, b: Claim) -> bool:
    """May `a` and `b` be about the same thing? Record claims: only with a record claim of the same identity.
    Otherwise: the same primary subject (same_subject); when only one claim names a subject, the other must
    mention it; when neither does, the lexical key decides (similarity)."""
    ra, rb = record_identity(a), record_identity(b)
    if ra or rb:
        return bool(ra & rb)
    pa, pb = _subject_of(a), _subject_of(b)
    if pa and pb:
        return same_subject(pa, pb)
    if pa:
        return pa <= _stem_set(b)
    if pb:
        return pb <= _stem_set(a)
    return True


def is_reportable(claim: Claim) -> bool:
    """False for text that must never appear in any report field: claims from adversarial sources, text with an
    injection finding (safety.scan_injection) and snippet-only discovery hints."""
    flags = claim.flags or {}
    return not (is_adversarial(claim) or flags.get("snippet_only") or safety.scan_injection(claim.text or ""))


# ---- extraction ---------------------------------------------------------------------------------------------

def extract_claims(doc: Document, question: str, *, max_claims: int = 8) -> list[Claim]:
    """Up to `max_claims` claims from one document, most relevant first. A document whose metadata says
    `snippet_only` (a search-result snippet, not the page) yields none: it is a discovery hint, not evidence.

    Sentences of MIN_WORDS..MAX_WORDS words that are not boilerplate/navigation, not a heading or the page title
    (text.heading_like) and carry no injection finding are scored by BM25 against the question (document title as
    a weak prior, small bonus for values and dates). In HTML pages, feeds and API records every line is a block,
    so no sentence spans a line break. Structured API records also yield templated 'metadata' claims ("PyPI
    reports numpy latest version 2.3.5 released 2026-11-03.") and, for academic records, their abstract
    sentences. A document with injection findings (adversarial_document) is adversarial: its claims carry
    flags["adversarial_source"] = True and no evidence weight (evaluate)."""
    md = doc.metadata or {}
    if md.get("snippet_only"):
        return []                       # a search snippet is a discovery hint, never a citable claim (W-10)
    primary = doc.kind == "api" or doc.retrieval_mode == "api"
    source_url = doc.canonical_url or doc.final_url or doc.url
    candidates: list[tuple[str, str, dict, bool]] = []      # (text, kind, extra flags, relevance floor)
    if primary:
        for text, extra, floor in _metadata_statements(doc):
            if not safety.scan_injection(text):
                candidates.append((text, "metadata", extra, floor))
    body = doc.text or ""
    if primary and not body.strip() and isinstance(md.get("abstract"), str):
        body = md["abstract"]
    for sentence in T.sentences(body, hard_newlines=doc.kind in HARD_LINE_KINDS):
        n = len(sentence.split())
        if n < MIN_WORDS or n > MAX_WORDS:
            continue
        if _BOILERPLATE.search(sentence) or _NAV.search(sentence) or T.heading_like(sentence, doc.title or ""):
            continue
        if fragment(sentence):
            continue
        if safety.scan_injection(sentence):
            continue
        candidates.append((sentence, claim_kind(sentence), {}, False))
    if not candidates:
        return []
    q_stems = list(dict.fromkeys(_stems(question or "")))
    title_stems = list(dict.fromkeys(_stems(doc.title or "")))
    corpus = [_stems(c[0]) for c in candidates]
    bm = T.BM25(corpus)
    s_q = bm.scores(q_stems) if q_stems else [0.0] * len(candidates)
    overlap = (len(set(title_stems) & set(q_stems)) / len(set(q_stems))) if q_stems else 0.0
    s_t = bm.scores(title_stems) if overlap > 0 else [0.0] * len(candidates)
    observed_at = doc.retrieved_at if registry_record(doc) and doc.retrieval_mode != "cache" else None
    adversarial = adversarial_document(doc)
    scored = []
    for i, ((text, kind, extra, floor), sq, st) in enumerate(zip(candidates, s_q, s_t)):
        vals = T.values(text)
        events = temporal.event_dates(text, reference=doc.published_at)
        raw = sq + TITLE_PRIOR_WEIGHT * overlap * st
        if raw > 0:
            raw *= 1.0 + (VALUE_BONUS if vals else 0.0) + (DATE_BONUS if events else 0.0)
        relevance = raw / (raw + RELEVANCE_HALF) if raw > 0 else 0.0
        if floor:
            relevance = max(relevance, METADATA_RELEVANCE_FLOOR)
        if relevance <= 0:
            continue
        flags = {"source_type": "primary" if primary else "secondary", "retrieval_mode": doc.retrieval_mode,
                 "doc_kind": doc.kind}
        if observed_at is not None:
            flags["observed_at"] = observed_at
        if doc.flags.get("truncated"):
            flags["truncated"] = True
        if adversarial:
            flags["adversarial_source"] = True
        flags.update({k: v for k, v in extra.items() if v is not None})
        key = claim_key(text, q_stems)
        flags["subject"] = sorted(set(key.split()) & set(q_stems))
        if kind != "metadata":
            flags["primary_subject"] = primary_subject(text, doc.title or "")
        claim = Claim(id=_claim_id(source_url, text), text=text, source_url=source_url, domain=doc.domain,
                      title=doc.title, retrieved_at=doc.retrieved_at, key=key, kind=kind,
                      polarity=polarity(text) if kind != "metadata" else 1, values=vals,
                      published_at=doc.published_at, event_at=events[0] if events else None,
                      relevance=round(relevance, 6), doc_sha256=doc.content_sha256, sentence_index=i, flags=flags)
        scored.append(claim)
    scored.sort(key=lambda c: (-c.relevance, c.sentence_index))
    unique = list({c.id: c for c in reversed(scored)}.values())[::-1]
    return unique[:max_claims]


# ---- similarity, independence, contradiction ----------------------------------------------------------------

def _shingles(claim: Claim) -> frozenset:
    return _shingles_cached(claim.text or "")


@functools.lru_cache(maxsize=65536)
def _shingles_cached(text: str) -> frozenset:
    return frozenset(T.shingles(T.normalise(text), SHINGLE_K))


def different_subjects(a: Claim, b: Claim) -> bool:
    """True when each claim names a question term (flags["subject"]) that the other never mentions — e.g. two
    PyPI records for numpy and pandas in a comparison: similar wording, different subjects."""
    sa, sb = set((a.flags or {}).get("subject") or ()), set((b.flags or {}).get("subject") or ())
    if not sa or not sb:
        return False
    return bool(sa - set(_stems(b.text))) and bool(sb - set(_stems(a.text)))


def similarity(a: Claim, b: Claim) -> float:
    """max(key-token Jaccard, shingle Jaccard of normalised texts) — 0 when neither passes its threshold or the
    claims are about different subjects (different_subjects: question terms; subjects_compatible: primary subject
    or record identity). Two record claims with the same identity are always similar (one record, two
    providers)."""
    if different_subjects(a, b) or not subjects_compatible(a, b):
        return 0.0
    kj = T.jaccard(a.key.split(), b.key.split())
    sj = T.jaccard(_shingles(a), _shingles(b))
    if kj >= CLUSTER_KEY_JACCARD or sj >= CLUSTER_TEXT_SHINGLE:
        return max(kj, sj)
    if record_identity(a):
        return CLUSTER_KEY_JACCARD
    return 0.0


def _domain(claim: Claim) -> str:
    if claim.domain:
        return claim.domain.lower()
    m = re.match(r"^[a-z]+://([^/:]+)", claim.source_url or "", re.IGNORECASE)
    return m.group(1).lower() if m else claim.source_url


def independence_groups(claims) -> list[list[str]]:
    """Claim ids grouped into independent sources: claims from the same registrable domain, or with near-duplicate
    texts (shingle Jaccard >= DUPLICATE_SHINGLE: syndication or copying), collapse into one group. Groups are in
    order of first appearance; ids keep input order."""
    claims = list(claims)
    parent = list(range(len(claims)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    sh = [T.shingles(c.text, SHINGLE_K) for c in claims]     # raw text: a copy keeps its numbers
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if _domain(claims[i]) == _domain(claims[j]) or T.jaccard(sh[i], sh[j]) >= DUPLICATE_SHINGLE:
                union(i, j)
    groups: dict[int, list[str]] = {}
    for i, c in enumerate(claims):
        ids = groups.setdefault(find(i), [])
        if c.id not in ids:
            ids.append(c.id)
    return [groups[k] for k in sorted(groups)]


def _cue_conflict(ta: str, tb: str) -> str | None:
    for label, side_a, side_b in _CUE_PAIRS:
        a1, a2 = side_a.search(ta), side_b.search(ta)
        b1, b2 = side_a.search(tb), side_b.search(tb)
        if a1 and not a2 and b2 and not b1:
            return f"conflicting statements ({label}): '{a1.group(0)}' vs '{b2.group(0)}'"
        if a2 and not a1 and b1 and not b2:
            return f"conflicting statements ({label}): '{a2.group(0)}' vs '{b1.group(0)}'"
    return None


def _value_conflict(a: Claim, b: Claim) -> str | None:
    """Values of the same kind attached to the same thing: plain numbers only against plain numbers counting the
    same noun (unless the two sentences are the same template with different numbers), and a disagreement that
    is only about years only between near-identical statements (different years usually date different
    events)."""
    va, vb = list(a.values or []), list(b.values or [])
    if not va or not vb:
        return None
    template = T.jaccard(_shingles(a), _shingles(b)) >= CLUSTER_TEXT_SHINGLE
    nouns = None if template else (T.value_nouns(a.text), T.value_nouns(b.text))
    reason = T.values_conflict(va, vb, nouns=nouns)
    if reason and reason.startswith("different years") and not template and \
            T.jaccard(a.key.split(), b.key.split()) < YEAR_ONLY_KEY_JACCARD:
        return None
    return reason


def conflict(a: Claim, b: Claim) -> str | None:
    """Reason two claims (assumed to be about the same subject) disagree: opposite polarity, different values of the
    same kind (_value_conflict), or an explicit conflict cue pair. None when they are compatible. Record claims
    of different identities never conflict (two releases are not two answers to one question)."""
    ra, rb = record_identity(a), record_identity(b)
    if (ra or rb) and not ra & rb:
        return None
    if a.polarity != b.polarity:
        neg = b if b.polarity < 0 else a
        cue = negation_cue(neg.text) or "negation"
        which = "the second" if neg is b else "the first"
        return f"opposite polarity: {which} statement negates ('{cue}')"
    reason = _value_conflict(a, b)
    if reason:
        return reason
    return _cue_conflict(a.text, b.text)


def describe_conflict(a: Claim, b: Claim, reason: str | None = None) -> str:
    """A plain-language sentence for a contradiction, for owner questions and UIs: what the sources disagree
    about and what each side says ("Sources disagree about NumPy: one says 2.3, another says 2.4.")."""
    reason = reason or conflict(a, b) or ""
    subject = primary_subject(a.text, a.title) or primary_subject(b.text, b.title)
    about = f" about {subject}" if subject else ""
    m = re.match(r"different (?:values|versions|dates|years): (.+) vs (.+)$", reason)
    if m:
        return f"Sources disagree{about}: one says {m.group(1)}, another says {m.group(2)}."
    if reason.startswith("opposite polarity"):
        cue = re.search(r"\('([^']*)'\)", reason)
        return (f"Sources disagree{about}: one states it, another denies it"
                f"{' (' + repr(cue.group(1)) + ')' if cue else ''}.")
    m = re.match(r"conflicting statements \(([^)]*)\): '([^']*)' vs '([^']*)'", reason)
    if m:
        return f"Sources disagree{about}: one says '{m.group(2)}', another says '{m.group(3)}'."
    return f"Sources disagree{about}." if reason else ""


def contradictions(a: Claim, b: Claim) -> str | None:
    """Reason string when `a` and `b` are about the same subject (cluster similarity) and disagree, else None."""
    if a.id == b.id or similarity(a, b) <= 0:
        return None
    return conflict(a, b)


# ---- scoring ------------------------------------------------------------------------------------------------

def _effective(c: Claim) -> float:
    t = c.event_at if c.event_at is not None else c.published_at
    return t if t is not None else float("-inf")


def evidence_weight(claim: Claim, *, now: float | None = None, freshness_class: str | None = None,
                    ttl_overrides: dict | None = None) -> float:
    """Base reliability by source type x temporal factor (stale 0.5 in AGE_DISCOUNT_CLASSES only, undated 0.75).
    Rank and popularity are not inputs. A claim from an adversarial source weighs 0."""
    if is_adversarial(claim):
        return 0.0
    base = RELIABILITY_PRIMARY if is_primary(claim) else RELIABILITY_SECONDARY
    flags = claim.flags or {}
    dated = flags.get("observed_at") or claim.published_at or claim.event_at
    if dated is None:
        return base * UNDATED_FACTOR
    if now is not None and freshness_class in AGE_DISCOUNT_CLASSES:
        if now - dated > temporal.ttl(freshness_class, ttl_overrides):
            return base * STALE_FACTOR
    return base


def agrees(a: Claim, b: Claim) -> bool:
    """`b` restates `a` (so `b`'s source may be cited for `a`): the same claim, a near-duplicate text (normalised
    shingle Jaccard >= SUPPORT_TEXT_SHINGLE), the same structured record, or a statement about the same subject
    (similarity > 0) with at least one equal value and no conflict. Mere topical overlap is not agreement."""
    if a.id == b.id:
        return True
    if is_adversarial(b) or similarity(a, b) <= 0 or conflict(a, b):
        return False
    if T.jaccard(_shingles(a), _shingles(b)) >= SUPPORT_TEXT_SHINGLE:
        return True
    if record_identity(a) & record_identity(b):
        return True
    return T.values_agree(list(a.values or []), list(b.values or []))


def bearing(rep: Claim, x: Claim) -> str | None:
    """How `x` bears on the representative `rep`: "support" (agrees), "contradict" (same subject, conflicts) or
    None (related: same cluster, neither restates nor contradicts it)."""
    if x.id == rep.id:
        return "support"
    if similarity(rep, x) <= 0:
        return None
    if conflict(rep, x):
        return "contradict"
    return "support" if agrees(rep, x) else None


def _version_rank(c: Claim) -> tuple:
    versions = [T.version_key(v) for v in (c.values or []) if T.value_kind(v)[0] == "version"]
    return max(versions) if versions else ()


def _latest_record(claims) -> bool:
    return bool(claims) and all(c.kind == "metadata" and (c.flags or {}).get("record") in ("release", "package")
                                for c in claims)


def evaluate(claims, *, now: float | None = None, freshness_class: str | None = None, target: Claim | None = None,
             ttl_overrides: dict | None = None) -> dict:
    """The full evidence assessment of one cluster's claims (deterministic).

    The representative is the claim whose agreeing claims (agrees) span the most independent groups (ties: newest
    effective date, then highest relevance); for release/package records it is the highest version (semver-aware,
    then newest date), never provider order. Every other claim bears on it as support, contradiction or nothing
    (bearing: "related" claims share the topic but neither restate nor contradict it; they are not cited and
    carry no evidence). Each independent group with bearing claims adds its weight to alpha (supporting share)
    and beta (contradicting share). With `target` agreement is measured against the target statement instead
    (no conflict with it). Returns alpha, beta, representative, agreeing ids, contradicting ids, related ids,
    contradiction pairs (conflicting, directly similar claims from different groups), groups (of bearing
    claims), supporting/contradicting group counts.

    Claims from adversarial sources (is_adversarial) stay in "order" (they are shown) but carry evidence weight 0:
    they are not in any group, agreeing set or contradiction pair, so they never change the representative, the
    posterior, the independent-source count or a verdict."""
    unique = list({c.id: c for c in claims}.values())
    order = sorted(unique, key=lambda c: (-c.relevance, -_effective(c), c.source_url, c.sentence_index, c.id))
    shown = order
    order = [c for c in order if not is_adversarial(c)]
    empty = {"alpha": PRIOR_ALPHA, "beta": PRIOR_BETA, "representative": None, "agree": [], "contra": [],
             "related": [], "pairs": [], "groups": [], "support_groups": 0, "contra_groups": 0, "order": shown}
    if not order:
        return empty
    all_groups = independence_groups(order)
    if target is not None:
        relation = {c.id: ("support" if conflict(target, c) is None else "contradict") for c in order}
        rep = next((c for c in order if relation[c.id] == "support"), None)
    else:
        group_of = {i: n for n, g in enumerate(all_groups) for i in g}

        def rank(c: Claim):
            n = len({group_of[x.id] for x in order if bearing(c, x) == "support"})
            if _latest_record(order):
                return (_version_rank(c), _effective(c), n, c.relevance)
            return (n, _effective(c), c.relevance)

        ranks = {c.id: rank(c) for c in order}
        best = max(ranks.values())
        rep = next(c for c in order if ranks[c.id] == best)
        relation = {c.id: bearing(rep, c) for c in order}
    bearing_ids = {i for i, r in relation.items() if r}
    groups = [g for g in ([i for i in g if i in bearing_ids] for g in all_groups) if g]
    by_id = {c.id: c for c in order}
    alpha, beta, support_groups, contra_groups = PRIOR_ALPHA, PRIOR_BETA, 0, 0
    for g in groups:
        members = [by_id[i] for i in g]
        a = sum(1 for c in members if relation[c.id] == "support")
        d = len(members) - a
        w = max(evidence_weight(c, now=now, freshness_class=freshness_class, ttl_overrides=ttl_overrides)
                for c in members)
        alpha += w * a / len(members)
        beta += w * d / len(members)
        if a and a >= d:
            support_groups += 1
        else:
            contra_groups += 1
    group_of = {i: n for n, g in enumerate(all_groups) for i in g}
    pairs = []
    for i, x in enumerate(order):
        for y in order[i + 1:]:
            if group_of[x.id] == group_of[y.id] or similarity(x, y) <= 0:
                continue
            reason = conflict(x, y)
            if reason and len(pairs) < MAX_CONTRADICTION_PAIRS:
                pairs.append({"a": x.id, "b": y.id, "reason": reason, "description": describe_conflict(x, y, reason)})
    return {"alpha": alpha, "beta": beta, "representative": rep,
            "agree": [c.id for c in order if relation[c.id] == "support"],
            "contra": [c.id for c in order if relation[c.id] == "contradict"],
            "related": [c.id for c in order if not relation[c.id]],
            "pairs": pairs, "groups": groups, "support_groups": support_groups, "contra_groups": contra_groups,
            "order": shown}


def _build(claims, *, now=None, freshness_class=None, key: str = "", ttl_overrides=None) -> ClaimCluster:
    ev = evaluate(claims, now=now, freshness_class=freshness_class, ttl_overrides=ttl_overrides)
    a, b = ev["alpha"], ev["beta"]
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    rep = ev["representative"]
    if rep is None:
        status = "insufficient"
    elif ev["pairs"] or ev["contra_groups"]:
        status = "contested"
    elif ev["support_groups"] >= 2:
        status = "corroborated"
    else:
        status = "single_source"
    return ClaimCluster(key=rep.key if rep is not None else key, claims=ev["order"],
                        independent_sources=len(ev["groups"]), supporting=ev["agree"], contradicting=ev["pairs"],
                        confidence=round(mean, 6), uncertainty=round(sd, 6), status=status,
                        statement=rep.text if rep is not None else "")


def cluster(claims) -> list[ClaimCluster]:
    """Group claims about the same subject (compatible subjects — the same primary subject, or the same record
    identity for structured records — and key Jaccard >= CLUSTER_KEY_JACCARD or normalised-text shingle Jaccard
    >= CLUSTER_TEXT_SHINGLE; a claim joins the most similar existing cluster). `supporting` lists only claims that
    restate the representative; `independent_sources` counts groups that support or contradict it. Claims are visited most relevant
    first (ties by id), so the result does not depend on input order. Each cluster is scored without a staleness
    assessment; call score() with `now` and the freshness class for the final confidence."""
    unique = list({c.id: c for c in claims}.values())
    order = sorted(unique, key=lambda c: (-c.relevance, c.id))
    groups: list[list[Claim]] = []
    for c in order:
        best, best_sim = None, 0.0
        for g in groups:
            sim = max(similarity(c, m) for m in g)
            if sim > best_sim:
                best, best_sim = g, sim
        if best is None:
            groups.append([c])
        else:
            best.append(c)
    return [_build(g) for g in groups]


def score(cluster: ClaimCluster, *, now: float, freshness_class: str,
          ttl_overrides: dict | None = None) -> ClaimCluster:
    """Re-score a cluster with temporal factors: Beta(1,1) prior; each independent group adds weight
    w = reliability(source type) x temporal factor to alpha (agreeing) or beta (contradicting).
    confidence = posterior mean, uncertainty = posterior sd. Rank/popularity are never inputs. `ttl_overrides`
    (WebConfig.freshness_ttl) replaces the class TTL used for the staleness factor."""
    return _build(cluster.claims, now=now, freshness_class=freshness_class, key=cluster.key,
                  ttl_overrides=ttl_overrides)


def posterior(cluster: ClaimCluster, *, now: float | None = None, freshness_class: str | None = None) -> dict:
    """{"alpha", "beta"} of the cluster's Beta posterior, recomputed deterministically from its claims."""
    ev = evaluate(cluster.claims, now=now, freshness_class=freshness_class)
    return {"alpha": ev["alpha"], "beta": ev["beta"]}


def _h(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def info_gain(alpha: float, beta: float, reliability: float = RELIABILITY_SECONDARY) -> float:
    """Expected information (bits) one more independent source gives about the cluster statement: the mutual
    information between the binary truth (P(true) = alpha / (alpha + beta)) and a binary source report that is
    correct with probability `reliability`:  I = H(p_obs) - H(reliability),  p_obs = p r + (1 - p)(1 - r).

    Zero for a noise-only source (r = 0.5) however uncertain the statement is — so curiosity chases resolvable
    uncertainty, not irreducible noise (research/scripts/curiosity_control.py). Maximal at p = 0.5, falling to
    zero as p -> 0 or 1."""
    total = alpha + beta
    if total <= 0:
        return 0.0
    p = alpha / total
    r = min(max(reliability, 0.0), 1.0)
    p_obs = p * r + (1 - p) * (1 - r)
    return max(0.0, _h(p_obs) - _h(r))


__all__ = ["CLUSTER_KEY_JACCARD", "CLUSTER_TEXT_SHINGLE", "DUPLICATE_SHINGLE", "KEY_TOKENS", "MAX_WORDS",
           "MIN_WORDS", "PRIOR_ALPHA", "PRIOR_BETA", "RELIABILITY_PRIMARY", "RELIABILITY_SECONDARY",
           "RENDER_ONLY_PATTERNS", "STALE_FACTOR", "UNDATED_FACTOR", "adversarial_document", "agrees", "bearing",
           "claim_key", "claim_kind", "cluster", "conflict", "contradictions", "describe_conflict",
           "different_subjects", "evaluate", "evidence_weight", "extract_claims", "fragment", "independence_groups",
           "info_gain", "is_adversarial", "is_primary", "is_reportable", "negation_cue", "polarity", "posterior",
           "primary_subject", "record_identity", "registry_record", "same_subject", "score", "similarity",
           "subject_tokens", "subjects_compatible", "title_subject"]
