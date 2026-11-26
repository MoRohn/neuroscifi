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
NOTABLE_PER_PAGE, SALIENT_WORDS, SALIENCE_HALF   salience extraction for requests without a topic (informativeness)
MAX_CONTRADICTION_PAIRS         bound on listed contradiction pairs per cluster
"""
from __future__ import annotations

from datetime import datetime, timezone
import enum
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
NOTABLE_PER_PAGE = 5                          # salience extraction: notable statements kept per page (W-19)
SALIENT_WORDS = (10, 40)                      # sentence length band that informativeness() rates highest
SALIENCE_HALF = 2.0                           # informativeness mapped to relevance 0.5
DOC_DUPLICATE_SHINGLE = 0.6                   # 5-word shingle Jaccard of two documents' texts above which one
DOC_SHINGLE_K = 5                             # is a (lightly edited) copy of the other: one independent source
DOC_DUPLICATE_MIN_WORDS = 40                  # shorter texts are compared sentence by sentence only (two short
                                              # independent reports of one fact are often worded alike)

# ---- claim verification (ADR-035, docs/knowledge/CLAIM_VERIFICATION.md) ------------------------------------
# The fact-verification policy per claim category (temporal.claim_category). Every number is documented in
# CLAIM_VERIFICATION.md §4 with its derivation:
#   undated_factor  multiplier on an undated source's weight; None = an undated source does not count at all
#                   (time-sensitive claims: a source that does not establish its timeframe cannot decide them)
#   min_groups      independent agreeing source groups needed for "supported" by corroboration
#   authority       a direct authoritative record alone may support the statement when nothing contradicts it:
#                   "registry" (the package's own registry record, ADR-033) and/or "official" (the subject's own
#                   official site, official_source)
#   timeframe       only evidence dated inside the question's window (timely) decides; otherwise the statement
#                   is temporally_unresolved
#   evaluated       False: no evidence can verify it lexically (a judgement): not_evaluated
VERIFICATION_MIN_CONFIDENCE = 0.72      # posterior mean floor for "supported" (as ADR-026; see §4)
DISPUTE_WEIGHT_RATIO = 1.5              # contradicting weight must be >= 1.5 x supporting weight (and come from
                                        # more independent groups) for "disputed"; otherwise "conflicting"
CATEGORY_POLICY = {
    "stable":         {"undated_factor": 1.0, "min_groups": 2, "authority": ("official",), "timeframe": False,
                       "evaluated": True},
    "historical":     {"undated_factor": 1.0, "min_groups": 2, "authority": ("official",), "timeframe": False,
                       "evaluated": True},
    "time_sensitive": {"undated_factor": None, "min_groups": 2, "authority": ("registry",), "timeframe": True,
                       "evaluated": True},
    "scientific":     {"undated_factor": UNDATED_FACTOR, "min_groups": 2, "authority": (), "timeframe": False,
                       "evaluated": True},
    "numerical":      {"undated_factor": UNDATED_FACTOR, "min_groups": 2, "authority": (), "timeframe": False,
                       "evaluated": True},
    "comparative":    {"undated_factor": UNDATED_FACTOR, "min_groups": 2, "authority": (), "timeframe": False,
                       "evaluated": True},
    "registry":       {"undated_factor": UNDATED_FACTOR, "min_groups": 2, "authority": ("registry",),
                       "timeframe": False, "evaluated": True},
    "interpretive":   {"undated_factor": UNDATED_FACTOR, "min_groups": 2, "authority": (), "timeframe": False,
                       "evaluated": False},
}
assert set(CATEGORY_POLICY) == set(temporal.CLAIM_CATEGORIES)


class VerificationState(str, enum.Enum):
    """The outcome of checking one statement against the evidence read (serialised as its string value)."""
    SUPPORTED = "supported"                          # enough independent or authoritative evidence agrees
    DISPUTED = "disputed"                            # credible evidence contradicts it and outweighs support
    INSUFFICIENT = "insufficient"                    # evidence was examined; too little bears on it
    CONFLICTING = "conflicting"                      # credible evidence on both sides with comparable support
    TEMPORALLY_UNRESOLVED = "temporally_unresolved"  # about the current state; no evidence establishes "now"
    NOT_EVALUATED = "not_evaluated"                  # no evidence was examined (a judgement, an unsupported
                                                     # statement shape, or no usable source)


VERIFICATION_STATES = tuple(s.value for s in VerificationState)
# The four verdict labels before ADR-035 (still written as verdict["label"] for older readers: Pocket, the
# console, stored reports) and how the states map onto them, and back.
LEGACY_LABELS = ("supported", "disputed", "contested", "insufficient")
STATE_TO_LEGACY = {"supported": "supported", "disputed": "disputed", "conflicting": "contested",
                   "insufficient": "insufficient", "temporally_unresolved": "insufficient",
                   "not_evaluated": "insufficient"}
_LEGACY_TO_STATE = {"supported": "supported", "disputed": "disputed", "contested": "conflicting",
                    "insufficient": "insufficient"}


def normalise_state(value) -> str | None:
    """A verification state string from a current state or a pre-ADR-035 verdict label ("contested" ->
    "conflicting"); None for anything else."""
    if isinstance(value, VerificationState):
        return value.value
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if v in VERIFICATION_STATES else _LEGACY_TO_STATE.get(v)


def verdict_state(verdict: dict | None) -> str | None:
    """The verification state of a stored verdict, normalised on read: its "state" when present and valid, else
    the old "label" mapped (a pre-ADR-035 "insufficient" verdict with no statement shape counted no source, so it
    is "not_evaluated")."""
    if not isinstance(verdict, dict):
        return None
    state = normalise_state(verdict.get("state"))
    if state:
        return state
    state = normalise_state(verdict.get("label"))
    if state == "insufficient" and "shape" in verdict and verdict.get("shape") is None:
        return "not_evaluated"
    return state


def verification_state(*, sup_groups: int, con_groups: int, sup_weight: float, con_weight: float, mean: float,
                       min_groups: int = 2, authoritative: bool = False) -> str:
    """The state from the counted evidence (CLAIM_VERIFICATION.md §3):
    supported      nothing contradicts and either >= min_groups independent groups agree with posterior mean >=
                   VERIFICATION_MIN_CONFIDENCE, or a direct authoritative record supports it (`authoritative`)
    disputed       contradicting groups outnumber supporting ones and their weight is >= DISPUTE_WEIGHT_RATIO x
                   the supporting weight (with no support, any credible contradiction)
    conflicting    credible evidence on both sides, neither outweighing the other that clearly
    insufficient   otherwise (some or no evidence bears on it, not enough to decide)"""
    if con_groups == 0:
        if sup_groups and (authoritative or (sup_groups >= min_groups and mean >= VERIFICATION_MIN_CONFIDENCE)):
            return "supported"
        return "insufficient"
    if sup_groups == 0:
        return "disputed"
    if con_groups > sup_groups and con_weight >= DISPUTE_WEIGHT_RATIO * sup_weight:
        return "disputed"
    return "conflicting"


def timely(claim: Claim, *, now: float, window: float) -> bool | None:
    """Whether the claim's source establishes a timeframe inside `window` seconds before `now`: True (dated inside
    it), False (dated outside it, or more than temporal.FUTURE_SKEW ahead), None (undated). The source's own time
    is what it speaks for: a live registry record's retrieval time, else its publication date, else the event
    date in the sentence."""
    flags = claim.flags or {}
    at = flags.get("observed_at") or claim.published_at or claim.event_at
    return temporal.window_ok(at, window, now)

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

# A sentence may report a figure or belief it does not assert: a myth, an old estimate, a superseded belief or the
# alternative it rejects ("long cited as having 100 billion neurons", "the commonly quoted 100 billion", "rather
# than the often-cited 100 billion", "For half a century, the human brain was believed to contain about 100 billion
# neurons", "purportedly containing 100 billion neurons"). asserted_text drops those parts (per clause: a contrast
# cue drops the rest of its clause, an attribution cue the whole clause) before values and polarity are compared
# (C-09, T1-16): what a source reports as believed is neither support nor a contradiction of the belief.
_CONTRAST_CUE = re.compile(r"\b(?:rather\s+than|instead\s+of|as\s+opposed\s+to|contrary\s+to|in\s+contrast\s+to)\b",
                           re.IGNORECASE)
_ATTRIBUTION_CUE = re.compile(
    r"\b(?:(?:long|often|commonly|widely|frequently|traditionally|popularly|generally|usually|once|previously|"
    r"formerly)[- ](?:been\s+)?(?:cited|quoted|stated|said|claimed|believed|reported|repeated|assumed|held|thought|"
    r"accepted|estimated|given|viewed|regarded|portrayed)|myths?|misconceptions?|"
    r"popular\s+(?:belief|claim|figure|estimate|notion)|"
    r"(?:old|older|earlier|outdated|previous)\s+(?:estimates?|figures?|counts?)|it\s+(?:was|is|has\s+been)\s+"
    r"(?:once\s+|long\s+|often\s+|commonly\s+|widely\s+|previously\s+)?(?:thought|believed|said|claimed|assumed|"
    r"estimated|stated)|(?:was|were|had\s+been|has\s+been|have\s+been)\s+(?:long\s+|once\s+|widely\s+|commonly\s+|"
    r"often\s+|generally\s+|previously\s+)?(?:believed|thought|assumed|presumed|supposed)\s+to|"
    r"purportedly|supposedly|allegedly|reputedly|ostensibly|"
    # T1-16 (RC2 retest): "textbooks say ...", "the commonly cited figure"
    r"textbooks?\s+(?:say|said|state|stated|claim|claimed|teach|taught|give|gave|cite|cited|list|listed)|"
    r"(?:commonly|often|widely|frequently)\s+(?:cited|quoted)\s+(?:figure|number|estimate|value|count))\b",
    re.IGNORECASE)
# A sentence framed in the past ("For decades, scientists said that ... 100 billion") reports what was said then:
# within it, a clause with a reporting verb is attribution, not assertion (T1-16, RC2 retest).
_PAST_FRAME = re.compile(r"^\s*(?:for\s+(?:decades|years|centuries|generations|a\s+(?:long\s+time|century)|half\s+a\s+"
                         r"century|many\s+years)|historically|traditionally|until\s+recently|in\s+the\s+past|"
                         r"long\s+ago)\b", re.IGNORECASE)
_REPORTED_VERB = re.compile(r"\b(?:said|claimed|believed|thought|assumed|taught|estimated|cited|stated|held|"
                            r"reported|quoted)\b", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"([,;:()—]|\s(?:but|whereas|while|although|though)\s)", re.IGNORECASE)
# Scare quotes (R6 D6): an epistemic word in quotation marks, or after "so-called", distances the writer from it
# ('Neuroscientists have become used to a number of "facts" about the human brain: It has 100 billion neurons').
# Its clause is attribution, and when that clause ends in a colon, the list it introduces (the rest of the
# sentence) reports those "facts" rather than asserting them.
_EPISTEMIC = (r"(?:facts?|truths?|knowledge|wisdom|consensus|evidence|science|proof|certaint(?:y|ies)|established|"
              r"settled|known|common\s+knowledge|received\s+wisdom)")
_SCARE_QUOTE = re.compile(r"(?:[\"“”]|(?<![\w])['‘’])\s*" + _EPISTEMIC + r"\s*(?:[\"“”]|['‘’](?![\w]))|"
                          r"\bso[- ]called\s+" + _EPISTEMIC + r"\b", re.IGNORECASE)


@functools.lru_cache(maxsize=65536)
def asserted_text(text: str) -> str:
    """`text` without the parts that report a figure or belief rather than assert it (see the comment above);
    `text` itself when it has no such part."""
    framed = bool(_PAST_FRAME.search(text) and _REPORTED_VERB.search(text))
    if not (framed or _CONTRAST_CUE.search(text) or _ATTRIBUTION_CUE.search(text) or _SCARE_QUOTE.search(text)):
        return text
    parts = _CLAUSE_SPLIT.split(text)
    out = []
    listed = False                  # inside the list a scare-quoted clause introduces with a colon
    for i in range(0, len(parts), 2):
        seg = parts[i]
        m = _CONTRAST_CUE.search(seg)
        if listed:
            seg = ""
        elif m:
            seg = seg[:m.start()]
        elif _SCARE_QUOTE.search(seg):
            listed = i + 1 < len(parts) and parts[i + 1] == ":"
            seg = ""
        elif _ATTRIBUTION_CUE.search(seg) or (framed and _REPORTED_VERB.search(seg)):
            seg = ""
        out.append(seg)
        if i + 1 < len(parts):
            out.append(parts[i + 1])
    return T.collapse("".join(out))


def asserted_values(claim: Claim) -> list[str]:
    """The claim's values that its sentence asserts (asserted_text): a figure it only reports as a myth, an old
    estimate or a former belief is left out. Structured templates assert all their values."""
    values = list(claim.values or [])
    if claim.kind == "metadata" or not values:
        return values
    text = claim.text or ""
    asserted = asserted_text(text)
    if asserted == text:
        return values
    keep = T.values(asserted)
    return [v for v in values if v in keep]

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
REGISTRY_RECORDS = ("package", "advisory", "release")


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


ENCYCLOPEDIA_PROVIDERS = ("Wikipedia",)


def encyclopedia(doc: Document) -> bool:
    """A tertiary reference (Wikipedia, read through its API or as a page): it summarises other sources. Its claims
    are flagged source_type "tertiary", never "primary", however it was read (T1-12/T1-14)."""
    return _provider(doc) in ENCYCLOPEDIA_PROVIDERS


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


_DEFINITE = re.compile(r"^\W*(?:[Tt]he|[Tt]his|[Tt]hat|[Tt]hese|[Tt]hose)\s+(?P<n>(?:[a-z][a-z-]*\s+){0,2}?[a-z][a-z-]*)"
                       r"(?=\s+(?P<v>[A-Za-z']+))")


def title_reference(clause: str) -> int | None:
    """End offset of a subject that only refers back to the document's subject — a pronoun ("It is ...") or a
    definite description of common nouns directly before the verb ("The tower is 330 metres tall", "This river
    flows ...") — or None. Such a sentence is about the subject of its own document's title (OBS-02: "The tower"
    of the Fernsehturm Berlin article is not the Eiffel Tower)."""
    clause = clause or ""
    m = _PRONOUN_START.match(clause)
    if m:
        return m.end()
    m = _DEFINITE.match(clause)
    if m and not any(w in T.STOPWORDS or w in _AUX for w in m.group("n").split()):
        verb = m.group("v")
        if verb.lower() in _AUX or _VERB_CUE.match(verb):
            return m.end("n")
    return None


def primary_subject(sentence: str, title: str = "") -> str | None:
    """The claim's primary subject (see the section comment), or None when the sentence names none before its
    verb ("Most neurons are covered by spines")."""
    clause = _subject_clause(sentence)
    if title_reference(clause) is not None:
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


_PROVIDER_TEXT = re.compile(r"^Latest release \S+ (?:uploaded|published) ")


def record_template(text: str) -> str | None:
    """For a templated statement over a structured record ("OpenAlex record: \"T\", dated ...", "PyPI reports X
    latest version ...", "GitHub lists release v1 of o/r ...", "OSV lists advisory ID ...", the registry clients'
    "Latest release ... uploaded ..."), the record's own subject words (the title, the package, the repository and
    tag, the advisory); None for any other text. Searches and research objectives are built from these, never from
    the template's frame ("OpenAlex record Yes human brain ...", W-20)."""
    text = T.collapse(text or "")
    for rx, kind in _RECORD_TEXT:
        m = rx.search(text)
        if not m:
            continue
        if kind == "advisory":
            target = re.search(r" for (\S+?)(?::|;|\.$)", text)
            return " ".join(x for x in (m.group(1), target.group(1) if target else "") if x)
        if kind == "release":
            return f"{m.group(2)} {m.group(1)}"
        if kind == "package":
            return m.group(2)
        return m.group(1)
    return "" if _PROVIDER_TEXT.match(text) else None


def record_identity(claim: Claim) -> frozenset:
    """Identity of the structured record a claim is about: flags["record_id"] (set on a 'metadata' template and,
    for a registry record — package, advisory, release — on every statement of that record's document: release
    notes of v2.5.4 are about v2.5.4, a package summary about that package), else parsed from a template's text;
    empty for every other claim. Two record claims are about the same record only when their identities
    intersect: two different papers, advisories, packages or releases never corroborate or contradict each
    other, and a statement is cited only to its own record."""
    rid = (claim.flags or {}).get("record_id")
    if rid:
        return frozenset(rid)
    if claim.kind != "metadata":
        return frozenset()
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


_STRONG_IDS = ("doi", "arxiv_id", "pmid", "openalex_id")


def same_identity(ra: frozenset, rb: frozenset) -> bool:
    """Two record identities (record_identity) name the same record: they share an identifier, and no persistent
    identifier scheme both carry (DOI, arXiv id, PMID, OpenAlex id) tells them apart. Two Crossref records with
    one title but different DOIs (two reviews "Review of: ...", a conference paper and its journal version) are
    two records (T1-01)."""
    if not ra & rb:
        return False
    for scheme in _STRONG_IDS:
        xa = {r for r in ra if r.startswith(scheme + ":")}
        xb = {r for r in rb if r.startswith(scheme + ":")}
        if xa and xb and not xa & xb:
            return False
    return True


# A versioned subject is a name followed by its version at the start of the subject ("NumPy 2.5.5 is a patch
# release", "The NumPy 2.5.2 ...", "Python 3.0, released in 2008, ..."). Two versions of one product are two
# subjects: what is true of 2.5.2 (its patch notes, its contributor count) is not a rival answer about 2.5.5. A
# sentence without its own version that names no other subject (or names the product only) is about the version
# its document's title names ("A total of 12 people contributed to this release." in "NumPy 2.5.5 Release Notes").
# Statements of a one-valued slot ("the latest / current / newest / first release") stay comparable across
# versions: "Quillstream 4.2.0 is the latest version" and "Quillstream 3.9 is the latest version" conflict.
_VERSIONED = re.compile(r"^\W*(?:[Tt]he\s+)?(?P<name>[A-Z][\w+#.-]*?(?:\s+[A-Z][\w+#-]*){0,2})\s+v?"
                        r"(?P<ver>\d+(?:\.\d+){0,3})(?![\w.]*\w)")
_ONE_VALUED_SLOT = re.compile(r"\b(?:latest|newest|current|most\s+recent|first|initial)\b", re.IGNORECASE)


def _versioned(text: str) -> tuple[frozenset, tuple] | None:
    m = _VERSIONED.match(text or "")
    if not m or m.group("name").split()[0].lower() in _CALENDAR:
        return None
    name = subject_tokens(m.group("name"))
    return (name, T.version_key(m.group("ver"))) if name else None


@functools.lru_cache(maxsize=65536)
def _versioned_subject_cached(text: str, title: str) -> tuple[frozenset, tuple] | None:
    own = _versioned(_subject_clause(text))
    if own is not None:
        return own
    page = _versioned(title_subject(title) or "")
    if page is None:
        return None
    subject = subject_tokens(primary_subject(text, ""))
    if subject and not same_subject(subject, page[0]):
        return None                                   # the sentence is about another subject than its page
    return page


def versioned_subject(claim: Claim) -> tuple[frozenset, tuple] | None:
    """(name tokens, version key) of the claim's versioned subject (section comment), or None."""
    if claim.kind == "metadata":
        return None
    return _versioned_subject_cached(claim.text or "", claim.title or "")


def different_versions(a: Claim, b: Claim) -> bool:
    """`a` and `b` are about two versions of one named subject (section comment above), not one slot's values."""
    va, vb = versioned_subject(a), versioned_subject(b)
    if va is None or vb is None or not same_subject(va[0], vb[0]) or va[1] == vb[1]:
        return False
    return not (_ONE_VALUED_SLOT.search(a.text or "") or _ONE_VALUED_SLOT.search(b.text or ""))


def subjects_compatible(a: Claim, b: Claim) -> bool:
    """May `a` and `b` be about the same thing? Record claims: only with a record claim of the same identity
    (same_identity). Two versions of one product are different subjects (different_versions). Otherwise: the same
    primary subject (same_subject); when only one claim names a subject, the other must mention it; when neither
    does, the lexical key decides (similarity)."""
    ra, rb = record_identity(a), record_identity(b)
    if ra or rb:
        return same_identity(ra, rb)
    if different_versions(a, b):
        return False
    pa, pb = _subject_of(a), _subject_of(b)
    if pa and pb:
        return same_subject(pa, pb)
    if pa:
        return pa <= _stem_set(b)
    if pb:
        return pb <= _stem_set(a)
    return True


_FIRST_PERSON = re.compile(r"\b(?:I|[Ww]e|[Oo]ur|[Uu]s|[Mm]y)\b")


def first_person(claim: Claim) -> bool:
    """The statement is a personal account ("..., and we paddled every one of them", "the one we recommend",
    "according to the beta images we tested"): the writer reports their own experience, not the fact as a
    reference would. A source-quality signal for choosing a contested cluster's representative (RD-04); never
    evidence weight. Structured records are never first person."""
    return claim.kind != "metadata" and bool(_FIRST_PERSON.search(_prose(claim.text or "")))


def is_reportable(claim: Claim) -> bool:
    """False for text that must never appear in any report field: claims from adversarial sources, text with an
    injection finding (safety.scan_injection) and snippet-only discovery hints."""
    flags = claim.flags or {}
    return not (is_adversarial(claim) or flags.get("snippet_only") or safety.scan_injection(claim.text or ""))


# ---- extraction ---------------------------------------------------------------------------------------------

def informativeness(sentence: str, index: int, count: int) -> float:
    """Salience of a sentence on a page read without a question (W-19): 1 for a sentence of SALIENT_WORDS
    words (0.5 outside that band), + 0.5 per named entity (at most 4: names, acronyms, alphanumeric tokens, not
    the sentence's first word), + 0.5 per value (at most 3), + up to 0.25 for an early position on the page."""
    words = sentence.split()
    band = 1.0 if SALIENT_WORDS[0] <= len(words) <= SALIENT_WORDS[1] else 0.5
    toks = [m.group(0) for m in _WORD.finditer(sentence)]
    names = sum(1 for i, t in enumerate(toks) if _entity_token(t) or (i > 0 and _entity_token(t) is None))
    position = 1.0 - index / max(1, count)
    return band + 0.5 * min(names, 4) + 0.5 * min(len(T.values(sentence)), 3) + 0.25 * position


def extract_claims(doc: Document, question: str, *, max_claims: int = 8, salience: bool = False) -> list[Claim]:
    """Up to `max_claims` claims from one document, most relevant first. A document whose metadata says
    `snippet_only` (a search-result snippet, not the page) yields none: it is a discovery hint, not evidence.

    Sentences of MIN_WORDS..MAX_WORDS words that are not boilerplate/navigation, not a heading or the page title
    (text.heading_like) and carry no injection finding are scored by BM25 against the question (document title as
    a weak prior, small bonus for values and dates). In HTML pages, feeds and API records every line is a block,
    so no sentence spans a line break. Structured API records also yield templated 'metadata' claims ("PyPI
    reports numpy latest version 2.3.5 released 2026-11-03.") and, for academic records, their abstract
    sentences. A document with injection findings (adversarial_document) is adversarial: its claims carry
    flags["adversarial_source"] = True and no evidence weight (evaluate).

    `salience` (the request names no topic: "investigate these sources: URL", W-19): there is no question to
    score against, so sentences are ranked by informativeness() instead and the top NOTABLE_PER_PAGE become
    'statement' claims flagged "notable" (notable statements of the page, not answers to a question); relevance
    is informativeness / (informativeness + SALIENCE_HALF)."""
    md = doc.metadata or {}
    if md.get("snippet_only"):
        return []                       # a search snippet is a discovery hint, never a citable claim (W-10)
    primary = doc.kind == "api" or doc.retrieval_mode == "api"
    source_url = doc.canonical_url or doc.final_url or doc.url
    candidates: list[tuple[str, str, dict, bool]] = []      # (text, kind, extra flags, relevance floor)
    record_ids: list[str] = []          # the registry record this document is (package, advisory, release)
    if primary:
        for text, extra, floor in _metadata_statements(doc):
            if extra.get("record") in REGISTRY_RECORDS:
                record_ids += [r for r in extra.get("record_id") or () if r not in record_ids]
            if not safety.scan_injection(text):
                candidates.append((text, "metadata", extra, floor))
    body = doc.text or ""
    if primary and not body.strip() and isinstance(md.get("abstract"), str):
        body = md["abstract"]
    provider_text = md.get("provider_text") if primary else None
    if isinstance(provider_text, str) and provider_text.strip() and provider_text in body:
        # a sentence the registry client wrote ("Latest release 2.5.5 uploaded ...; requires Python >=3.12."): a
        # template over the record, never a quote from it (RT-W-03)
        body = body.replace(provider_text, "\n")
        if not safety.scan_injection(provider_text):
            candidates.append((T.collapse(provider_text), "metadata",
                               {"record_id": list(record_ids) or None, "provider_text": True}, True))
    for sentence in T.sentences(body, hard_newlines=doc.kind in HARD_LINE_KINDS):
        sentence = T.strip_heading(sentence)            # "Abstract A key challenge ..." (RT-W-06)
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
    if salience:
        max_claims = min(max_claims, NOTABLE_PER_PAGE)
        sentences = [i for i, c in enumerate(candidates) if c[1] != "metadata"]
        rank = {i: informativeness(candidates[i][0], n, len(sentences)) for n, i in enumerate(sentences)}
        candidates = [(t, "metadata" if k == "metadata" else "statement",
                       extra if k == "metadata" else dict(extra, notable=True), floor)
                      for t, k, extra, floor in candidates]
    q_stems = list(dict.fromkeys(_stems(question or "")))
    title_stems = list(dict.fromkeys(_stems(doc.title or "")))
    corpus = [_stems(c[0]) for c in candidates]
    bm = T.BM25(corpus)
    s_q = bm.scores(q_stems) if q_stems else [0.0] * len(candidates)
    overlap = (len(set(title_stems) & set(q_stems)) / len(set(q_stems))) if q_stems else 0.0
    s_t = bm.scores(title_stems) if overlap > 0 else [0.0] * len(candidates)
    observed_at = doc.retrieved_at if registry_record(doc) and doc.retrieval_mode != "cache" else None
    tertiary = encyclopedia(doc)
    adversarial = adversarial_document(doc)
    origin = origin_keys(doc)
    scored = []
    for i, ((text, kind, extra, floor), sq, st) in enumerate(zip(candidates, s_q, s_t)):
        vals = T.values(text)
        events = temporal.event_dates(text, reference=doc.published_at)
        raw = sq + TITLE_PRIOR_WEIGHT * overlap * st
        if raw > 0:
            raw *= 1.0 + (VALUE_BONUS if vals else 0.0) + (DATE_BONUS if events else 0.0)
        relevance = raw / (raw + RELEVANCE_HALF) if raw > 0 else 0.0
        if salience and i in rank:
            relevance = rank[i] / (rank[i] + SALIENCE_HALF)
        if floor:
            relevance = max(relevance, METADATA_RELEVANCE_FLOOR)
        if relevance <= 0:
            continue
        flags = {"source_type": "tertiary" if tertiary else ("primary" if primary else "secondary"),
                 "retrieval_mode": doc.retrieval_mode, "doc_kind": doc.kind}
        if observed_at is not None:
            flags["observed_at"] = observed_at
        if doc.flags.get("truncated"):
            flags["truncated"] = True
        if adversarial:
            flags["adversarial_source"] = True
        if origin:
            flags["origin"] = origin
        flags.update({k: v for k, v in extra.items() if v is not None})
        if record_ids and "record_id" not in flags:
            flags["record_id"] = list(record_ids)   # a statement of a registry record is about that record
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
    or record identity). Two record templates with the same identity are always similar (one record, two
    providers)."""
    if different_subjects(a, b) or not subjects_compatible(a, b):
        return 0.0
    kj = T.jaccard(a.key.split(), b.key.split())
    sj = T.jaccard(_shingles(a), _shingles(b))
    if kj >= CLUSTER_KEY_JACCARD or sj >= CLUSTER_TEXT_SHINGLE:
        return max(kj, sj)
    if _same_record(a, b):
        return CLUSTER_KEY_JACCARD
    return 0.0


def _same_record(a: Claim, b: Claim) -> bool:
    """Two templates over the same structured record (one record, two providers or two fields)."""
    return a.kind == "metadata" and b.kind == "metadata" and same_identity(record_identity(a), record_identity(b))


def _domain(claim: Claim) -> str:
    if claim.domain:
        return claim.domain.lower()
    m = re.match(r"^[a-z]+://([^/:]+)", claim.source_url or "", re.IGNORECASE)
    return m.group(1).lower() if m else claim.source_url


# ---- syndication: one origin report, many copies --------------------------------------------------------------
#
# A copy is not an independent source. Besides the same host and near-identical sentences (independence_groups),
# a document names its origin when it was republished: a canonical URL on another host, an attribution line
# ("This story first appeared on Syndicate Wire", "originally published by", "republished from") or a news-wire
# credit ("(Reuters)", "Associated Press"). origin_keys() turns those into keys; two claims whose keys meet (or
# whose origin is the other's own site) are one group. Documents whose whole texts are near-identical are joined
# by document_origins() (DOC_DUPLICATE_SHINGLE).
_ATTRIBUTION = re.compile(
    r"\b(?:first\s+(?:appeared|published|ran)|originally\s+(?:appeared|published|ran)|(?:republished|reprinted|"
    r"syndicated|adapted)(?:\s+with\s+permission)?|this\s+(?:story|article|report)\s+(?:is|was)\s+(?:provided|"
    r"supplied|distributed)|distributed|provided)[ \t]+(?:on|in|at|by|from)[ \t]+(?:the[ \t]+)?"
    r"(?P<name>[A-Z][\w&'’-]*(?:[ \t]+(?:[A-Z][\w&'’-]*|of|and|&)){0,4})")
_WIRES = {"reuters": "reuters", "associated press": "ap", "ap": "ap", "afp": "afp", "agence france-presse": "afp",
          "dpa": "dpa", "press association": "pa", "pa media": "pa", "upi": "upi", "united press international": "upi",
          "ansa": "ansa", "efe": "efe", "kyodo": "kyodo", "xinhua": "xinhua", "bloomberg": "bloomberg"}
_WIRE_CREDIT = re.compile(r"\((?P<w>Reuters|AP|AFP|dpa|DPA|PA Media|UPI|ANSA|EFE|Kyodo|Xinhua|Bloomberg)\)|"
                          r"\b(?:[Bb]y|[Ss]ource:|[Cc]redit:)\s+(?:the\s+)?(?P<w2>Reuters|Associated Press|AFP|"
                          r"Agence France-Presse|Press Association|PA Media|United Press International)\b")
_SECOND_LEVEL = frozenset("co com net org gov ac edu ne or go mil nic".split())


def _name_key(name: str) -> str:
    return "name:" + re.sub(r"[^a-z0-9]", "", (name or "").lower())


def site_label(host: str) -> str:
    """The registrable label of a host ("docs.kestrel-lang.org" -> "kestrel-lang", "news.bbc.co.uk" -> "bbc")."""
    labels = [x for x in (host or "").lower().split(":")[0].strip(".").split(".") if x]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        labels = labels[:-1]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def _host(url: str) -> str:
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/:?#]+)", url or "", re.IGNORECASE)
    return m.group(1).lower() if m else ""


def origin_keys(doc: Document) -> list[str]:
    """The origin a republished document names (see the section comment): "site:<host>" for a canonical URL on
    another host, "name:<normalised name>" for an attribution or wire credit. Empty for an original."""
    keys = []
    canonical = _host(doc.canonical_url or "")
    own = (doc.domain or _host(doc.final_url or doc.url or "")).lower()
    if canonical and own and canonical != own:
        keys.append("site:" + canonical)
    text = doc.text or ""
    for m in _ATTRIBUTION.finditer(text):
        name = re.sub(r"\s+(?:and|of|&)$", "", m.group("name").strip(" .,'’"))
        wire = _WIRES.get(name.lower())
        keys.append("name:" + wire if wire else _name_key(name))
    for m in _WIRE_CREDIT.finditer(text):
        keys.append("name:" + _WIRES[(m.group("w") or m.group("w2")).lower()])
    return list(dict.fromkeys(k for k in keys if k not in ("name:", "site:")))


def document_origins(documents) -> dict[str, list[str]]:
    """{source url: ["doc:<url of the first copy>"]} for documents of at least DOC_DUPLICATE_MIN_WORDS words whose
    whole texts are near-identical (5-word shingle Jaccard >= DOC_DUPLICATE_SHINGLE): a lightly edited copy of a
    story is the same source."""
    docs = [d for d in documents if len((d.text or "").split()) >= DOC_DUPLICATE_MIN_WORDS]
    shingles = [T.shingles(T.normalise(d.text), DOC_SHINGLE_K) for d in docs]
    root = list(range(len(docs)))
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            if root[j] == j and shingles[i] and T.jaccard(shingles[i], shingles[j]) >= DOC_DUPLICATE_SHINGLE:
                root[j] = root[i]
    out: dict[str, list[str]] = {}
    for i, d in enumerate(docs):
        if root[i] != i or any(root[j] == i for j in range(i + 1, len(docs))):
            first = docs[root[i]]
            key = "doc:" + (first.canonical_url or first.final_url or first.url)
            for u in {d.canonical_url, d.final_url, d.url} - {None, ""}:
                out.setdefault(u, []).append(key)
    return out


def stamp_origins(claims, documents, *, attribution: bool = False, grouping: str = "transitive",
                  derivation: bool = False, echo=None) -> dict[str, list[str]]:
    """Add each claim's document_origins keys to its flags["origin"] (in place) and return the origins: copies
    of one story then form one independence group wherever the claims go next (scoring, the report verdict,
    per-claim states, consolidation from stored clusters), not only where the documents are at hand. With
    `attribution` (WebConfig.attribution_independence, ADR-061) the outlets a sentence credits (attribution_keys)
    go to flags["credited"], kept apart from the origins: a reworded copy that names the story's origin inside the
    sentence counts with that origin, and a credit never joins two other groups (independence_groups).
    `grouping` (WebConfig.origin_grouping, ADR-063): "one_way" marks each claim flags["origin_grouping"] = "one_way"
    (independence_groups uses the one-way rule for claims that all carry it); any other value removes the mark, and a
    claim never marked is left exactly as it was.
    `derivation` (WebConfig.source_derivation, ADR-066): each claim gets flags["derivation"] = {"cites": the other
    pages of these documents its page names or links to (derivation_cites)}, and {"echo": true} besides when `echo`
    (a callable claim -> bool: lane E's check whether the page repeats the individual's own answer; None: no check)
    says so; independence_groups then uses the derivation rule for claims that all carry it. Off: the mark is removed
    where present, and a claim never marked is left exactly as it was."""
    origins = document_origins(documents)
    cites = derivation_cites(documents, claims) if derivation else {}
    echo_failed = None
    for c in claims:
        if derivation:
            mark = {"cites": list(cites.get(c.source_url) or ())}
            if echo is not None:
                try:            # a failing check is logged (its type only); the page then counts as without the hook
                    if echo(c) is True:
                        mark["echo"] = True
                except Exception as exc:        # noqa: BLE001 - logged below (its type only), never silent
                    from ..instance.errors import failure_kind
                    echo_failed = echo_failed or failure_kind(exc)
            c.flags = dict(c.flags or {}, derivation=mark)
        elif "derivation" in (c.flags or {}):
            c.flags = {k: v for k, v in c.flags.items() if k != "derivation"}
    if echo_failed is not None:
        import logging
        logging.getLogger(__name__).warning("the echo check failed (%s); those pages count as without it",
                                            echo_failed)
    failed = None
    for c in claims:
        extra = list(origins.get(c.source_url) or ())
        if extra:
            c.flags = dict(c.flags or {}, origin=list(dict.fromkeys([*((c.flags or {}).get("origin") or ()), *extra])))
        if grouping == "one_way":
            c.flags = dict(c.flags or {}, origin_grouping="one_way")
        elif "origin_grouping" in (c.flags or {}):
            c.flags = {k: v for k, v in c.flags.items() if k != "origin_grouping"}
        credited = []
        if attribution:
            try:        # Wave 3 reconciliation (lane U's pattern): a failing read is logged, never the task's end
                credited = attribution_keys(c.text)
                if not (isinstance(credited, list) and all(isinstance(k, str) for k in credited)):
                    raise TypeError(f"the credited outlets are {type(credited).__name__}, not a list of keys")
            except Exception as exc:        # noqa: BLE001 - logged below (its type only), never silent
                from ..instance.errors import failure_kind
                failed = failed or failure_kind(exc)
                credited = []
        if credited:
            c.flags = dict(c.flags or {}, credited=list(dict.fromkeys([*((c.flags or {}).get("credited") or ()),
                                                                       *credited])))
    if failed is not None:
        import logging
        logging.getLogger(__name__).warning("the outlets sentences credit could not be read (%s); those claims count "
                                            "as with attribution_independence off", failed)
    return origins


# ---- ADR-061 (Wave 3 lane S): the outlet a sentence credits -----------------------------------------------------------
#
# A copy of a story need not carry an attribution line: it may reword the story and credit its origin inside the
# sentence ("According to Syndiwire, ...", "..., Syndiwire says.", "... (Syndiwire).", "... via Syndiwire.", "..., as
# the Syndiwire desk reports."). attribution_keys() reads that credit as the origin key "name:<name>" (the key an
# attribution line gives, and the one a claim on the credited outlet's own site carries), so such copies are one
# independent source with the outlet they credit. A credit to the sentence's own subject ("..., the Velmora team says"
# in a sentence about Velmora) is the subject speaking, not an outlet repeated: it gives no key, so two outlets quoting
# a project's announcement stay two sources, as before. Indefinite or plural speakers ("Someone says", "Experts said")
# name no outlet.
# Limits (security review L2, L3; review3 S-3): the protection is wording-deep. Credits in other phrasings ("Syndiwire
# notes", "as reported in Syndiwire", "reports Syndiwire", "(h/t Syndiwire)", a link-only or lowercase credit, another
# language: 19 of 27 phrasings tried) give no key, and a made-up outlet per copy gives a key no other page shares, so
# such copies look independent again exactly as before this rule. Cost: quadratic in the credits of one text (each
# credited name is searched in the rest of the sentence); a claim is at most MAX_WORDS words, so one claim takes
# well under a millisecond (a 100 kB text with 6,666 credits took 3.1 s in the review: never a claim).
_CREDITED = r"(?P<name>[A-Z][\w&'’-]*(?:[ \t]+[A-Z][\w&'’-]*){0,3})"
_CREDIT_VERB = r"(?:reports?|reported|says|said|states|stated|writes|wrote|announced|announces|confirms|confirmed)"
_CREDITS = (
    re.compile(r"\b[Aa]ccording[ \t]+to[ \t]+(?:the[ \t]+)?" + _CREDITED),
    re.compile(r"\b(?:via|per|[Ss]ource:|[Cc]redit:|(?:first[ \t]+|as[ \t]+)?reported[ \t]+by)[ \t]+(?:the[ \t]+)?"
               + _CREDITED),
    re.compile(r"(?:^|[,;:][\"”’']?[ \t]*|\b(?:as|that)[ \t]+)(?:[Tt]he[ \t]+)?" + _CREDITED
               + r"(?:[ \t]+[a-z]+)?[ \t]+" + _CREDIT_VERB + r"\b"),
    re.compile(r"\((?:the[ \t]+)?" + _CREDITED + r"\)[ \t]*[.!]?[ \t]*$"),
)
_NO_OUTLET = frozenset("someone somebody everyone everybody nobody people officials experts analysts researchers "
                       "scientists critics sources reports many some it they he she we i you this that these those "
                       "there here one others users readers fans".split())
_OUTLET_SUFFIX = frozenset("desk team newsroom news staff editors editorial wire service agency reporters reporter "
                           "blog magazine site website".split())


def attribution_keys(text: str) -> list[str]:
    """["name:<normalised name>"] for each outlet the sentence credits (see the section comment); [] for none."""
    text = text or ""
    keys = []
    for pattern in _CREDITS:
        for m in pattern.finditer(text):
            words = m.group("name").split()
            if words:           # a possessive credit ("Syndiwire's desk says") names Syndiwire, not "Syndiwires"
                words[-1] = re.sub(r"['’]s$", "", words[-1]) or words[-1]
            if words and words[0].lower() == "the":
                words = words[1:]
            while words and words[-1].lower() in _OUTLET_SUFFIX:
                words = words[:-1]
            if not words or words[0].lower() in _NO_OUTLET or words[0].lower() in T.MONTHS or \
                    words[0].lower() in T.STOPWORDS:
                continue
            rest = (text[:m.start("name")] + " " + text[m.end("name"):]).lower()
            if all(re.search(r"\b" + re.escape(w.lower()) + r"\b", rest) for w in words):
                continue        # the sentence's own subject speaking, not an outlet repeated
            keys.append(_name_key(" ".join(words)))
    return list(dict.fromkeys(k for k in keys if k != "name:"))


def _identity_keys(claim: Claim) -> set:
    host = _domain(claim)
    return {"site:" + host, _name_key(site_label(host))}


def _origin_of(claim: Claim, origins: dict | None) -> set:
    keys = set((claim.flags or {}).get("origin") or ())
    if origins:
        keys |= set(origins.get(claim.source_url) or ())
    return keys


def official_source(claim: Claim, subject: frozenset) -> bool:
    """True when the claim comes from the subject's own site: the registrable label of its host, split at
    hyphens and without generic words (lang, project, official, docs, ...), names exactly the subject's stemmed
    name tokens ("kestrel-lang.example" for Kestrel, "nimbus-os.example" for Nimbus-OS). A structured record
    (API) is not a site; an adversarial source is never official."""
    if not subject or is_primary(claim) or is_adversarial(claim):
        return False
    parts = [p for p in re.split(r"[-_]", site_label(_domain(claim))) if p and p not in _GENERIC_SITE_WORDS]
    if not parts:
        return False
    names = {T.stem(t) for t in subject if not t.isdigit()}
    return bool(names) and {T.stem(p) for p in parts} == names


_GENERIC_SITE_WORDS = frozenset("lang language project official org hq dev docs doc app io js py get use try www "
                                "home site web the team foundation".split())


def independence_groups(claims, *, origins: dict | None = None) -> list[list[str]]:
    """Claim ids grouped into independent sources: claims from the same host, with near-duplicate texts (shingle
    Jaccard >= DUPLICATE_SHINGLE: syndication or copying), or sharing an origin (origin_keys in
    flags["origin"], or `origins` {source url: keys} such as document_origins) collapse into one group. Groups
    are in order of first appearance; ids keep input order.

    A credit (flags["credited"], attribution_independence; ADR-061) never joins two groups (security review H1: one
    sentence crediting two honest outlets merged them, so a true fact was never verified). The groups are formed as
    without credits; then a group made only of crediting claims counts with the first group (one with a claim that
    credits no one) of an outlet it credits, and the remaining groups made only of crediting claims are one group per
    outlet they credit (copies crediting an outlet that was not read are one source). A group that holds a claim
    crediting no one is never moved, so a credit cannot carry a union between two such groups.

    That is the rule of WebConfig.origin_grouping "transitive", the default (ADR-063): the grouping below is the code
    of 3ce4ae3, unchanged, so a named origin joins transitively and a page naming two honest outlets as its origins
    makes them one source (review3b N1, open at the shipped defaults). When every claim was read with origin_grouping "one_way"
    (stamp_origins marks it in flags["origin_grouping"]), _one_way_groups decides instead; one claim read without it
    and this rule decides. A one-way grouping that fails is logged (its type only) and this rule decides.

    When every claim was read with WebConfig.source_derivation (ADR-066; stamp_origins marks flags["derivation"]),
    _derivation_groups decides: this grouping, coarsened by one-way citation edges (see there). A derivation grouping
    that fails is logged (its type only) and the grouping without it decides."""
    claims = list(claims)
    if claims and all(isinstance((c.flags or {}).get("derivation"), dict) for c in claims):
        try:
            return _derivation_groups(claims, origins)
        except Exception as exc:        # noqa: BLE001 - logged below (its type only); the grouping without it decides
            from dataclasses import replace
            from ..instance.errors import failure_kind
            import logging
            logging.getLogger(__name__).warning("the derivation grouping failed (%s); the grouping without it is used",
                                                failure_kind(exc))
            claims = [replace(c, flags={k: v for k, v in c.flags.items() if k != "derivation"}) for c in claims]
    if claims and all((c.flags or {}).get("origin_grouping") == "one_way" for c in claims):
        try:
            return _one_way_groups(claims, origins)
        except Exception as exc:        # noqa: BLE001 - logged below (its type only); the default rule decides
            from dataclasses import replace
            from ..instance.errors import failure_kind
            import logging
            logging.getLogger(__name__).warning("the one-way origin grouping failed (%s); the transitive grouping "
                                                "(the default) is used", failure_kind(exc))
            claims = [replace(c, flags={k: v for k, v in c.flags.items() if k != "origin_grouping"}) for c in claims]
    if any((c.flags or {}).get("credited") for c in claims):
        return _groups_with_credits(claims, origins)
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
    ident = [_identity_keys(c) for c in claims]
    orig = [_origin_of(c, origins) for c in claims]
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if _domain(claims[i]) == _domain(claims[j]) or T.jaccard(sh[i], sh[j]) >= DUPLICATE_SHINGLE or \
                    orig[i] & (orig[j] | ident[j]) or orig[j] & ident[i]:
                union(i, j)
    groups: dict[int, list[str]] = {}
    for i, c in enumerate(claims):
        ids = groups.setdefault(find(i), [])
        if c.id not in ids:
            ids.append(c.id)
    return [groups[k] for k in sorted(groups)]


def _groups_with_credits(claims, origins) -> list[list[str]]:
    """independence_groups when some claim credits an outlet (see there)."""
    from dataclasses import replace
    plain = [dict.fromkeys(ids) for ids in independence_groups(
        [replace(c, flags={k: v for k, v in (c.flags or {}).items() if k != "credited"}) for c in claims],
        origins=origins)]
    by_id = {c.id: c for c in claims}
    keys = [set().union(*(_identity_keys(by_id[i]) | _origin_of(by_id[i], origins) for i in g)) for g in plain]
    credits = [set().union(*(set((by_id[i].flags or {}).get("credited") or ()) for i in g)) for g in plain]
    movable = [all((by_id[i].flags or {}).get("credited") for i in g) for g in plain]
    groups = [list(g) for g in plain]
    alive = [True] * len(groups)
    # a group of crediting claims only counts with the first other group an outlet it credits belongs to
    for n, g in enumerate(plain):
        if not movable[n]:
            continue
        target = next((m for m in range(len(plain)) if m != n and alive[m] and not movable[m]
                       and credits[n] & keys[m]), None)
        if target is not None:
            groups[target] += groups[n]
            alive[n] = False
    # what is left of them: one group per outlet credited (crediting groups that share a credit are one source)
    loose = [n for n in range(len(plain)) if alive[n] and movable[n]]
    for a_i, a in enumerate(loose):
        for b in loose[a_i + 1:]:
            if alive[a] and alive[b] and credits[a] & credits[b]:
                groups[a] += groups[b]
                credits[a] |= credits[b]
                alive[b] = False
    order = {c.id: n for n, c in enumerate(claims)}
    out = [sorted(dict.fromkeys(groups[n]), key=order.get) for n in range(len(groups)) if alive[n]]
    return sorted(out, key=lambda ids: order[ids[0]])



def _one_way_groups(claims, origins) -> list[list[str]]:
    """independence_groups with WebConfig.origin_grouping "one_way" (ADR-063; off by default).

    Claims from the same host, with near-duplicate texts, or that are copies of one document (a shared "doc:<url>"
    key, document_origins) are one group: that join is transitive, as in the default rule.

    A page that NAMES an origin (origin_keys in flags["origin"] or `origins`: an attribution line, a wire credit, a
    canonical URL on another host) and, with attribution_independence (ADR-061), a group made only of claims that
    credit an outlet (flags["credited"]) count with ONE of the origins they name (_one_way): each such group takes one
    edge, so a component holds at most one group that names nothing, and a page naming two honest outlets ("first
    published by Devdesk", "originally appeared in Factline"; "(Reuters) (Bloomberg)") counts with one of them and
    never joins the two (review3b N1, N3). A group holding a claim that credits no one uses only its pages' named
    origins, never a credit. A wire's name (the names of _WIRES) is owned only by that wire's own site (_WIRE_SITES),
    never by another host whose site label is that name (review3c F2: "reuters.<tld>" naming Bloomberg)."""
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
    orig = [_origin_of(c, origins) for c in claims]
    copies = [{k for k in o if k.startswith("doc:")} for o in orig]
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if _domain(claims[i]) == _domain(claims[j]) or T.jaccard(sh[i], sh[j]) >= DUPLICATE_SHINGLE or \
                    copies[i] & copies[j]:
                union(i, j)
    base: dict[int, list[int]] = {}
    for i in range(len(claims)):
        base.setdefault(find(i), []).append(i)
    members = [base[k] for k in sorted(base)]
    ident = [set().union(*(_owned_keys(claims[i]) for i in g)) for g in members]
    pointers = []
    for g in members:
        named = set().union(*(orig[i] - copies[i] for i in g))
        credited = [set((claims[i].flags or {}).get("credited") or ()) for i in g]
        if all(credited):           # a group made only of crediting claims also counts with an outlet it credits
            named |= set().union(*credited)
        pointers.append(named)
    label = [tuple(sorted({_domain(claims[i]) or "" for i in g})) for g in members]
    comp = _one_way(ident, pointers, label)
    if not (isinstance(comp, list) and len(comp) == len(members)):
        raise TypeError(f"the one-way components are {type(comp).__name__}, not one per group")
    groups: dict = {}
    for g, idx in zip(members, comp):
        groups.setdefault(idx, []).extend(claims[i].id for i in g)
    first = {}
    for n, c in enumerate(claims):
        first.setdefault(c.id, n)
    out = [sorted(dict.fromkeys(ids), key=first.get) for ids in groups.values()]
    return sorted(out, key=lambda ids: first[ids[0]])


# The wires' own sites: the only hosts that own a wire's name under the one-way rule (review3c F2). A host is a wire's
# site when it is one of these or a subdomain of one.
_WIRE_SITES = {"reuters": ("reuters.com",), "ap": ("apnews.com", "ap.org"), "afp": ("afp.com",), "dpa": ("dpa.com",),
               "pa": ("pa.media",), "upi": ("upi.com",), "ansa": ("ansa.it",), "efe": ("efe.com",),
               "kyodo": ("kyodonews.net",), "xinhua": ("xinhuanet.com", "news.cn"), "bloomberg": ("bloomberg.com",)}
assert set(_WIRE_SITES) == set(_WIRES.values())


def _owned_keys(claim: Claim) -> set:
    """_identity_keys for the one-way rule: the host and its site label, except a wire's name, which only that wire's
    own site owns (_WIRE_SITES)."""
    host = _domain(claim)
    keys = {"site:" + host}
    label = _name_key(site_label(host))
    if label[len("name:"):] not in _WIRE_SITES:
        keys.add(label)
    keys |= {"name:" + wire for wire, sites in _WIRE_SITES.items()
             if any(host == s or host.endswith("." + s) for s in sites)}
    return keys


def _one_way(ident, pointers, label) -> list:
    """The component of each group under _one_way_groups' one-way origin edges: ident[g] the keys group g owns,
    pointers[g] the keys it names, label[g] its sort label. Each group naming a key it does not own takes ONE edge,
    chosen from content only (so every order of the claims gives the same groups; review3c F1): to a group that owns a
    named key and names nothing itself (an original); else to a group that owns a named key; else to the named key no
    group owns that the most groups name (copies of one unread origin meet there, whatever else each names); ties by
    key, then by the owner's sorted hosts."""
    owners: dict = {}
    for g, keys in enumerate(ident):
        for k in keys:
            owners.setdefault(k, []).append(g)
    out_keys = [sorted(set(named) - ident[g]) for g, named in enumerate(pointers)]
    root = [not keys for keys in out_keys]
    popularity: dict = {}
    for keys in out_keys:
        for k in keys:
            popularity[k] = popularity.get(k, 0) + 1
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g, keys in enumerate(out_keys):
        if not keys:
            continue
        candidates = []
        for k in keys:
            others = [o for o in owners.get(k, ()) if o != g]
            if others:
                o = min(others, key=lambda o: (not root[o], label[o], o))
                candidates.append(((0 if root[o] else 1, 0, k), ("g", o)))
            else:
                candidates.append(((2, -popularity[k], k), ("k", k)))
        a, b = find(("g", g)), find(min(candidates)[1])
        if a != b:
            parent[a] = b
    return [find(("g", g)) for g in range(len(ident))]


# ---- ADR-066 (Wave 4 lane S): one source by evidence of derivation, not by the wording of a credit --------------------
#
# WebConfig.source_derivation, OFF by default. Evidence a page carries about the OTHER pages of its task: it names one
# of them (capitalised words of its text that, joined, equal that page's site label: "Acme Wire" for
# acmewire.example, whatever verb or line surrounds them) or links to it (its host or URL). Such a page may derive
# from the page it names (a copy crediting its origin, a round-up, a mirror) or may have checked the value itself and
# still name it (a credit, a contributor line): the text cannot tell the two apart, so the join is an uncertain
# estimate of "same source". The rule counts what the evidence cannot
# separate as ONE source: the consolidation bar counts only groups whose independence is supported; it never lowers it.
# What the rule may and may not do (the Wave 3 reviews' lessons, review3 H1, review3b N1/N3, review3c F1/F2):
#   - it starts from today's grouping and only joins: copies today's grouping joins are never separated;
#   - a group moves only when every page in it names another page; it takes ONE edge (_derivation_edges, chosen from
#     content only, so every order of the claims gives the same groups), to a group that names nothing or to a name
#     where groups naming it meet, never to a group that itself names another; so two groups that each hold a page
#     naming no other page are never joined, however many pages name them (a round-up is not a bridge), and no page
#     can carry an honest copy into a third group;
#   - the edge goes only to a group stating exactly the same values (a copy never joins the other side of a
#     disagreement), and never to a page stated as published after the naming page (a derivation cannot precede its
#     origin);
#   - no list of phrases, hosts or names: the only text it reads is capitalised words matched against the site labels
#     of the pages read in the same task (the identity key independence_groups already uses);
#   - lane E's hook: a page its echo check marks (flags["derivation"]["echo"]) counts for nothing: it is in no group;
#   - (Wave 4 fix, review4 H1) a page the injection screen flagged, or one that yields no claim, is never named or
#     linked (so it can never be the point where others meet), and a name both pages' claims carry (the subject they
#     are about, "Velmora" for velmora.org) or a calendar word (a host labelled like a month) is no site label.
# VERDICT (ADR-066, after review4 H1, M2, M3): not supported; do not turn on. Its only signal, a capitalised name equal
# to a host's site label, is a convention of the benchmark worlds' generator, and one page linked by every honest
# outlet still merges them.
# Bounds: DERIVATION_SCAN_WORDS words of a page are read; DERIVATION_MAX_CITES pages kept per page; a site label
# shorter than DERIVATION_MIN_KEY characters is never matched in text (links still count).
DERIVATION_MIN_KEY = 4
DERIVATION_SCAN_WORDS = 5000
DERIVATION_MAX_CITES = 16
# The benchmark's ablations only (empty in the product): "names", "links", "time" (the publication-order filter),
# "values" (the same-values condition), "whole_group" (a group moves only when every page in it cites).
DERIVATION_ABLATE: frozenset = frozenset()
_CALENDAR = frozenset("january february march april may june july august september october november december monday "
                      "tuesday wednesday thursday friday saturday sunday".split())
_CAPITALISED = re.compile(r"[^\W_][\w'’&-]*")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def _capitalised_keys(text: str) -> set:
    """Letters and digits of 1-4 consecutive capitalised words, lower-cased and joined ("Acme Wire" -> "acmewire");
    a sentence's first word alone is not one ("Today, ..." names nothing). The first DERIVATION_SCAN_WORDS words."""
    out, seen = set(), 0
    for sentence in _SENTENCE_BREAK.split(text or ""):
        words = [m.group(0) for m in _CAPITALISED.finditer(sentence)]
        words = words[:max(0, DERIVATION_SCAN_WORDS - seen)]
        seen += len(words)
        for i in range(len(words)):
            for k in range(1, 5):
                run = words[i:i + k]
                if len(run) < k or not all(w[0].isupper() for w in run) or (k == 1 and i == 0):
                    continue
                out.add(re.sub(r"[^a-z0-9]", "", " ".join(run).lower()))
        if seen >= DERIVATION_SCAN_WORDS:
            break
    return out


def derivation_cites(documents, claims=None) -> dict[str, list[str]]:
    """{source url: identity keys "name:<site label>" of the OTHER documents (another host and label) that this
    document names (_capitalised_keys meets their site label) or links to}, sorted, at most DERIVATION_MAX_CITES; a
    document stated as published after this one is never cited (a derivation cannot precede its origin). Only the
    normalised name is kept, never a host or URL: the key is what the citing page's own words already say, and
    forgetting the cited page leaves nothing of it here. Never cited (Wave 4 fix, review4 H1): a document the injection
    screen flagged (adversarial_document) and, given the task's `claims`, one that yields none; never a name match on a
    calendar word or, given `claims`, on a name the claims of both pages carry (the subject they are both about)."""
    docs = [d for d in documents or () if d is not None]
    own = [(d.domain or _host(d.final_url or d.url or "")).lower() for d in docs]
    labels = [re.sub(r"[^a-z0-9]", "", site_label(h)) for h in own]
    said: dict = {}
    if claims is not None:
        from .consolidate import _name_set
        for c in claims:
            said.setdefault(c.source_url, set()).update(_name_set(c.text))
    urls = [{d.canonical_url, d.final_url, d.url} - {None, ""} for d in docs]
    named_in_claims = [set().union(*(said.get(u, set()) for u in us)) if us else set() for us in urls]
    citable = [not adversarial_document(d) and (claims is None or any(u in said for u in urls[j]))
               for j, d in enumerate(docs)]
    out: dict[str, list[str]] = {}
    for i, d in enumerate(docs):
        named = _capitalised_keys(d.text)
        linked = set()
        for link in (d.links or ())[:DERIVATION_MAX_CITES * 8]:
            if isinstance(link, str):
                linked.add(link)
                linked.add(_host(link))
        keys = set()
        for j, e in enumerate(docs):
            if not own[j] or own[j] == own[i] or not citable[j]:
                continue
            if d.published_at is not None and e.published_at is not None and e.published_at > d.published_at and \
                    "time" not in DERIVATION_ABLATE:
                continue
            if len(labels[j]) >= DERIVATION_MIN_KEY and labels[j] != labels[i] and labels[j] in named and \
                    labels[j] not in _CALENDAR and labels[j] not in (named_in_claims[i] & named_in_claims[j]) and \
                    "names" not in DERIVATION_ABLATE:
                keys.add("name:" + labels[j])
            if labels[j] and labels[j] != labels[i] and "links" not in DERIVATION_ABLATE and \
                    (own[j] in linked or bool({e.url, e.final_url, e.canonical_url} & linked)):
                keys.add("name:" + labels[j])          # the same key as the words: no host or URL is kept
        if keys:
            for u in {d.canonical_url, d.final_url, d.url} - {None, ""}:
                out[u] = sorted(keys)[:DERIVATION_MAX_CITES]
    return out


def _derivation_mark(claim: Claim) -> dict:
    mark = (claim.flags or {}).get("derivation")
    return mark if isinstance(mark, dict) else {}


def derivation_echo(claim: Claim) -> bool:
    """Lane E's hook, as the derivation rule reads it: the page was marked as repeating the individual's own answer."""
    return _derivation_mark(claim).get("echo") is True


def _derivation_groups(claims, origins) -> list[list[str]]:
    """independence_groups with WebConfig.source_derivation (ADR-066; see the section comment). Today's grouping of the
    claims (every other setting as it is), then: each group all of whose pages cite another page of the task takes
    ONE edge (_derivation_edges) to a group that owns a cited key, cites nothing and states exactly the same values (or
    to the key, where the groups citing it meet); pages marked as echoes are in no group."""
    from dataclasses import replace
    counted = [c for c in claims if not derivation_echo(c)]
    if not counted:
        return []
    base = independence_groups([replace(c, flags={k: v for k, v in (c.flags or {}).items() if k != "derivation"})
                                for c in counted], origins=origins)
    by_id = {c.id: c for c in counted}
    ident, pointers, label = [], [], []
    for ids in base:
        members = [by_id[i] for i in ids]
        sig = "" if "values" in DERIVATION_ABLATE else \
            "|" + ",".join(sorted({str(v) for c in members for v in (c.values or ())}))
        ident.append({k + sig for c in members for k in _identity_keys(c)})
        cited = [set(_derivation_mark(c).get("cites") or ()) for c in members]
        moves = any(cited) if "whole_group" in DERIVATION_ABLATE else all(cited)
        pointers.append({k + sig for k in set().union(*cited)} - ident[-1] if moves else set())
        label.append(tuple(sorted({_domain(c) or "" for c in members})))
    comp = _derivation_edges(ident, pointers, label)
    if not (isinstance(comp, list) and len(comp) == len(base)):
        raise TypeError(f"the derivation components are {type(comp).__name__}, not one per group")
    groups: dict = {}
    for ids, idx in zip(base, comp):
        groups.setdefault(idx, []).extend(ids)
    first = {}
    for n, c in enumerate(counted):
        first.setdefault(c.id, n)
    out = [sorted(dict.fromkeys(ids), key=first.get) for ids in groups.values()]
    return sorted(out, key=lambda ids: first[ids[0]])


def _derivation_edges(ident, pointers, label) -> list:
    """The component of each group under the derivation rule: ident[g] the keys group g owns, pointers[g] the keys it
    cites (empty: it does not move), label[g] its sort label. A group that cites takes ONE edge, chosen from content
    only: to a group that owns a cited key and cites nothing itself (ties by key, then label); else to the cited key
    no such group owns that the most citing groups cite (ties by key), where citing groups meet. An edge never ends at
    a group that cites, so no page can carry another into a third group (review3c F2: a page on a host labelled like
    the outlet an honest copy names, itself naming an honest outlet)."""
    roots: dict = {}
    for g, keys in enumerate(ident):
        if not pointers[g]:
            for k in keys:
                roots.setdefault(k, []).append(g)
    popularity: dict = {}
    for keys in pointers:
        for k in keys:
            popularity[k] = popularity.get(k, 0) + 1
    out = []
    for g, keys in enumerate(pointers):
        if not keys:
            out.append(("g", g))
            continue
        owned = [(k, label[o], o) for k in keys for o in roots.get(k, ()) if o != g]
        out.append(("g", min(owned)[2]) if owned else ("k", min(keys, key=lambda k: (-popularity[k], k))))
    return out



def dependence(claims, documents=(), *, origins: dict | None = None) -> dict:
    """How independent the groups of independence_groups really are (ADR-054, reporting only: it never changes a
    posterior, a verification state or what consolidation keeps). Two groups are joined here, not there, when
    they share a registrable site label (one family of sites: "docs.x.example" and "blog.x.example") or when a
    document of one links to a page or the site of the other (one cites the other, so its statement may rest on
    it). `documents` give the links; without them only families are seen.

    {"groups": n (independence_groups), "effective_groups": m <= n, "families": [[site label, [hosts]]] of the
     groups that share a label, "citations": [[citing host, cited host]], "one_family": the n >= 2 groups are all
     one family, "circular": a citation joins two groups}."""
    claims = [c for c in claims if c is not None]
    groups = independence_groups(claims, origins=origins)
    by_id = {c.id: c for c in claims}
    from .extract import canonical_url
    docs = {}
    for d in documents or ():
        for u in {d.canonical_url, d.final_url, d.url} - {None, ""}:
            docs.setdefault(u, d)
    hosts, labels, pages, links = [], [], [], []
    for ids in groups:
        members = [by_id[i] for i in ids if i in by_id]
        hosts.append({_domain(c) for c in members})
        labels.append({site_label(h) for h in hosts[-1]} - {""})
        urls, out = set(), set()
        for c in members:
            urls.add(canonical_url(c.source_url))
            doc = docs.get(c.source_url)
            for link in (doc.links if doc is not None else ()) or ():
                try:
                    out.add(canonical_url(link))
                except Exception:   # untrusted link text that is not a URL
                    continue
        pages.append(urls)
        links.append(out)
    parent = list(range(len(groups)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    citations = []
    for i in range(len(groups)):
        for j in range(len(groups)):
            if i == j:
                continue
            if i < j and labels[i] & labels[j]:
                parent[find(j)] = find(i)
            cited = {_host(u) for u in links[i]} & hosts[j] or (links[i] & pages[j])
            if cited:
                parent[find(j)] = find(i)
                citations.append([sorted(hosts[i])[0], sorted(hosts[j])[0]])
    shared = {}
    for i, ls in enumerate(labels):
        for label in ls:
            shared.setdefault(label, set()).update(hosts[i])
    families = [[label, sorted(h)] for label, h in sorted(shared.items())
                if sum(1 for ls in labels if label in ls) > 1]
    effective = len({find(i) for i in range(len(groups))})
    return {"groups": len(groups), "effective_groups": effective, "families": families, "citations": citations,
            "one_family": len(groups) >= 2 and all(labels) and bool(set.intersection(*labels)),
            "circular": bool(citations)}


def _cue_conflict(ta: str, tb: str) -> str | None:
    for label, side_a, side_b in _CUE_PAIRS:
        a1, a2 = side_a.search(ta), side_b.search(ta)
        b1, b2 = side_a.search(tb), side_b.search(tb)
        if a1 and not a2 and b2 and not b1:
            return f"conflicting statements ({label}): '{a1.group(0)}' vs '{b2.group(0)}'"
        if a2 and not a1 and b1 and not b2:
            return f"conflicting statements ({label}): '{a2.group(0)}' vs '{b1.group(0)}'"
    return None


# The subject phrase of a sentence without a named subject: its content words before the main verb ("the cerebral
# cortex", "the human brain"). Two counts of the same noun about disjoint subject phrases are figures for two
# different things (the cortex's 16 billion neurons are not a rival answer to the brain's 86 billion, T1-01).
_PHRASE_SKIP = frozenset("approximately about around roughly nearly almost some total number".split())


@functools.lru_cache(maxsize=65536)
def _subject_phrase(text: str) -> tuple[str, ...]:
    clause = _subject_clause(text)
    words = [m.group(0) for m in _WORD.finditer(clause)]
    _, verb = _spans(clause)
    if not verb:
        return ()
    return tuple(T.stem(w.lower()) for w in words[:verb]
                 if w.lower() not in T.STOPWORDS and w.lower() not in _PHRASE_SKIP and not T.is_number(w.lower())
                 and not T.is_version(w.lower()) and not w.lower().endswith("ly"))


def _different_things(a: Claim, b: Claim) -> bool:
    """Neither sentence names a primary subject, their subject phrases share no word, and one's head noun (the last
    word of its phrase) is absent from the other sentence (see the comment above)."""
    if _subject_of(a) or _subject_of(b):
        return False
    pa, pb = _subject_phrase(a.text or ""), _subject_phrase(b.text or "")
    if not pa or not pb or set(pa) & set(pb):
        return False
    return pa[-1] not in _stem_set(b) or pb[-1] not in _stem_set(a)


# T1-01 (RC2 retest): a figure for a subgroup ("the healthy female human brain") is not a figure for the whole
# population ("the human brain"), nor for another subgroup: the scopes differ, so the values do not conflict.
_SUBGROUP = re.compile(r"\b(?:male|female|males|females|men|women|man|woman|boys?|girls?|child|children|infants?|"
                       r"newborns?|neonates?|adolescents?|teenagers?|elderly|aged\s+\d+)\b", re.IGNORECASE)
_SUBGROUP_CANON = {"males": "male", "man": "men", "woman": "women", "females": "female", "boy": "boys",
                   "girl": "girls", "child": "children", "infant": "infants", "newborn": "newborns",
                   "neonate": "neonates", "adolescent": "adolescents", "teenager": "teenagers"}


def _subgroups(text: str) -> frozenset:
    return frozenset(_SUBGROUP_CANON.get(w.lower(), w.lower()) for w in _SUBGROUP.findall(asserted_text(text or "")))


def different_scope(a: Claim, b: Claim) -> bool:
    """The two statements are about different subgroups (or one about a subgroup, the other about the whole)."""
    return _subgroups(a.text) != _subgroups(b.text)


def _within_range(a: Claim, b: Claim) -> bool:
    """`b`'s comparable values all lie inside a range `a` states ("between 73 and 99 bn", "14–16 billion"), and at
    least one does (T1-01: 86 billion is not a contradiction of 73–99 billion)."""
    spans = T.ranges(asserted_text(a.text or ""))
    if not spans:
        return False
    hit = False
    for v in asserted_values(b):
        inside = [x for x in (T.within(v, lo, hi) for lo, hi in spans) if x is not None]
        if not inside:
            continue
        if not any(inside):
            return False
        hit = True
    return hit


def _value_conflict(a: Claim, b: Claim) -> str | None:
    """Values of the same kind attached to the same thing: plain numbers only against plain numbers counting the
    same noun (unless the two sentences are the same template with different numbers), and a disagreement that
    is only about years only between near-identical statements (different years usually date different
    events). Only asserted values count (asserted_values: a figure a sentence reports as a myth or a former belief
    is not its own)."""
    va, vb = asserted_values(a), asserted_values(b)
    if not va or not vb:
        return None
    template = T.jaccard(_shingles(a), _shingles(b)) >= CLUSTER_TEXT_SHINGLE
    nouns = None if template else (T.value_nouns(a.text), T.value_nouns(b.text))
    reason = T.values_conflict(va, vb, nouns=nouns)
    if reason and reason.startswith("different years") and not template and \
            T.jaccard(a.key.split(), b.key.split()) < YEAR_ONLY_KEY_JACCARD:
        return None
    if reason and (_within_range(a, b) or _within_range(b, a)):
        return None
    return reason


# A denial contradicts an assertion only of the same predicate: the first content words after a pre-positive
# negation cue ("does not support encryption at rest", "is not open to visitors", "no evidence that X causes Y"; at
# least DENIAL_MATCH of DENIAL_TERMS) must occur in what the other sentence asserts, and the other sentence must
# not deny the same word by a negative prefix ("not completely backward-compatible" and "backwards-incompatible"
# agree, T1-01). A post-positive cue ("the claim was debunked", "X is false") has no scope to read, so the polarity
# difference is the conflict, as before.
_POSTPOSITIVE_NEGATION = frozenset("false debunked refuted unfounded disproved disproven denied denies".split())
_DENIAL_SKIP = frozenset(_AUX | set("be been being yet ever even always really quite entirely fully completely "
                                    "directly necessarily just only much very so too any longer more".split()))
_NEGATIVE_PREFIXES = ("non", "un", "in", "im", "ir", "il", "dis")
DENIAL_TERMS = 3          # content words read after the negation cue
DENIAL_MATCH = 2          # of which the other sentence must assert at least this many (all, when fewer were read)


@functools.lru_cache(maxsize=65536)
def _denied_terms(text: str) -> tuple[str, ...]:
    clause = _NEGATION_EXEMPT.sub(" ", _prose(_main_clause(text)))
    m = _NEGATION.search(clause)
    if not m or m.group(0).lower() in _POSTPOSITIVE_NEGATION:
        return ()
    out = []
    for tok in T.tokens(clause[m.end():]):
        if tok in T.STOPWORDS or tok in _DENIAL_SKIP or tok.endswith("ly") or T.is_number(tok) or T.is_version(tok):
            continue
        out.append(T.stem(tok))
        if len(out) == DENIAL_TERMS:
            break
    return tuple(out)


def _denies_same(neg: Claim, pos: Claim) -> bool:
    """The negated claim denies what the other asserts (see the comment above)."""
    terms = set(_denied_terms(neg.text or ""))
    if not terms:
        return True
    asserted = asserted_text(pos.text or "")
    if len(terms & _stems_cached(asserted)) < min(DENIAL_MATCH, len(terms)):
        return False                  # the other sentence does not assert the denied predicate (or only reports it)
    for tok in T.tokens(asserted):
        for prefix in _NEGATIVE_PREFIXES:
            if tok.startswith(prefix) and len(tok) > len(prefix) + 3 and \
                    T.stem(tok[len(prefix):].lstrip("-")) in terms:
                return False
    return True


def conflict(a: Claim, b: Claim) -> str | None:
    """Reason two claims (assumed to be about the same subject) disagree: opposite polarity about the same predicate
    (_denies_same), different values of the same kind (_value_conflict), or an explicit conflict cue pair. None when
    they are compatible. Record claims of different identities never conflict (two releases are not two answers
    to one question), nor do statements about two versions of one product (different_versions) or about two
    different things (_different_things: "the cerebral cortex" vs "the human brain"), or about different subgroups
    (different_scope: "the female human brain" vs "the human brain")."""
    ra, rb = record_identity(a), record_identity(b)
    if (ra or rb) and not same_identity(ra, rb):
        return None
    if different_versions(a, b) or (_different_things(a, b) and
                                    T.jaccard(_shingles(a), _shingles(b)) < CLUSTER_TEXT_SHINGLE):
        return None
    if different_scope(a, b):
        return None
    if a.polarity != b.polarity:
        neg, pos = (b, a) if b.polarity < 0 else (a, b)
        if _denies_same(neg, pos):
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


def conflict_sides(reason: str | None) -> list[str] | None:
    """What each side of a conflict says, machine-readable ([A, B]) for UIs and owner questions: the two values
    ("412 km", "389 km"), the two cue words, or ["states it", "denies it"]; None when the reason names neither."""
    reason = reason or ""
    m = re.match(r"different (?:values|versions|dates|years): (.+) vs (.+)$", reason)
    if m:
        return [m.group(1), m.group(2)]
    m = re.match(r"conflicting statements \([^)]*\): '([^']*)' vs '([^']*)'", reason)
    if m:
        return [m.group(1), m.group(2)]
    if reason.startswith("opposite polarity"):
        return ["denies it", "states it"] if "the first" in reason else ["states it", "denies it"]
    return None


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
                    ttl_overrides: dict | None = None, undated_factor: float = UNDATED_FACTOR) -> float:
    """Base reliability by source type x temporal factor (stale 0.5 in AGE_DISCOUNT_CLASSES only, undated
    `undated_factor`: 0.75 by default; fact verification passes the claim category's factor, CATEGORY_POLICY).
    Rank and popularity are not inputs. A claim from an adversarial source weighs 0."""
    if is_adversarial(claim):
        return 0.0
    base = RELIABILITY_PRIMARY if is_primary(claim) else RELIABILITY_SECONDARY
    flags = claim.flags or {}
    dated = flags.get("observed_at") or claim.published_at or claim.event_at
    if dated is None:
        return base * undated_factor
    if now is not None and freshness_class in AGE_DISCOUNT_CLASSES:
        if now - dated > temporal.ttl(freshness_class, ttl_overrides):
            return base * STALE_FACTOR
    return base


def _same_statement(a: Claim, b: Claim) -> bool:
    return T.jaccard(_shingles(a), _shingles(b)) >= SUPPORT_TEXT_SHINGLE or _same_record(a, b)


def restates(a: Claim, b: Claim) -> bool:
    """`b` states `a` itself, so `b`'s source may be cited for `a`'s text: the same claim, a near-duplicate text
    (normalised shingle Jaccard >= SUPPORT_TEXT_SHINGLE) or the same structured record, about a compatible subject
    and without conflict. A sentence that only shares a value with `a` ("... opened in 1987" for "... opened in
    1987 and now carries 42,000 vehicles a day") corroborates it (agrees) but does not contain it."""
    if a.id == b.id:
        return True
    if is_adversarial(b) or similarity(a, b) <= 0 or conflict(a, b):
        return False
    return _same_statement(a, b)


def _reports_only(c: Claim) -> bool:
    """A sentence that reports some of its own values as a belief rather than asserting them (asserted_values)."""
    return c.kind != "metadata" and bool(c.values) and len(asserted_values(c)) < len(c.values)


def agrees(a: Claim, b: Claim) -> bool:
    """`b` supports `a` (evidence for it): `b` restates it (restates), or `b` is a statement about the same subject
    (similarity > 0) with at least one equal value and no conflict (corroboration in other words: it counts as
    evidence, but its source is not cited for `a`'s text). Mere topical overlap is not agreement."""
    if a.id == b.id:
        return True
    if is_adversarial(b) or similarity(a, b) <= 0 or conflict(a, b):
        return False
    if _reports_only(b) and not _reports_only(a):
        return False        # "It was long thought that X": the same words, but reported, not stated (T1-16)
    if _same_statement(a, b):
        return True
    return T.values_agree(asserted_values(a), asserted_values(b))


def bearing(rep: Claim, x: Claim) -> str | None:
    """How `x` bears on the representative `rep`: "support" (agrees), "contradict" (same subject, conflicts) or
    None (related: same cluster, neither restates nor contradicts it)."""
    if x.id == rep.id:
        return "support"
    if similarity(rep, x) <= 0:
        return None
    if conflict(rep, x):
        # one document is not pitted against itself: two of its sentences that disagree (a superseded belief and
        # the finding, two label definitions) are one source, related but not a contradiction (T1-01)
        return None if x.source_url == rep.source_url else "contradict"
    return "support" if agrees(rep, x) else None


def _version_rank(c: Claim) -> tuple:
    versions = [T.version_key(v) for v in (c.values or []) if T.value_kind(v)[0] == "version"]
    return max(versions) if versions else ()


def _latest_record(claims) -> bool:
    return bool(claims) and all(c.kind == "metadata" and (c.flags or {}).get("record") in ("release", "package")
                                for c in claims)


def evaluate(claims, *, now: float | None = None, freshness_class: str | None = None, target: Claim | None = None,
             ttl_overrides: dict | None = None, origins: dict | None = None) -> dict:
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
    # a page lane E's echo check marked under source_derivation (ADR-066) counts for nothing, as an adversarial one,
    # where the derivation rule groups (every claim read with it, as independence_groups decides; review4 L3)
    marked = bool(order) and all(isinstance((c.flags or {}).get("derivation"), dict) for c in order)
    order = [c for c in order if not is_adversarial(c) and not (marked and derivation_echo(c))]
    empty = {"alpha": PRIOR_ALPHA, "beta": PRIOR_BETA, "representative": None, "agree": [], "contra": [],
             "related": [], "pairs": [], "groups": [], "support_groups": 0, "contra_groups": 0, "order": shown}
    if not order:
        return empty
    all_groups = independence_groups(order, origins=origins)
    if target is not None:
        relation = {c.id: ("support" if conflict(target, c) is None else "contradict") for c in order}
        rep = next((c for c in order if relation[c.id] == "support"), None)
    else:
        group_of = {i: n for n, g in enumerate(all_groups) for i in g}

        def rank(c: Claim):
            n = len({group_of[x.id] for x in order if bearing(c, x) == "support"})
            if _latest_record(order):
                return (_version_rank(c), _effective(c), n, c.relevance)
            # equal independent support: an impersonal statement leads a personal account (RD-04: a travel blog's
            # "we paddled all 520 km" must not lead a reference page's 412 km because the blog is newer)
            return (n, not first_person(c), _effective(c), c.relevance)

        ranks = {c.id: rank(c) for c in order}
        best = max(ranks.values())
        rep = next(c for c in order if ranks[c.id] == best)
        top = order[0]       # the most relevant claim
        if rep is not top and not _latest_record(order) and ranks[top.id][:2] == best[:2] and \
                bearing(rep, top) is None:
            # the newer date decides only between statements of one slot (that agree or conflict); a merely
            # related newer sentence ("In December 2023, the first drivers written in Rust were accepted") never
            # leads the most relevant one ("The first stable release, Rust 1.0, was published in May 2015", R1-04)
            rep = top
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
    pairs, paired = [], set()

    def add_pair(x: Claim, y: Claim, reason: str) -> None:
        # one orientation per pair: the side that agrees with the representative first (C-16/RT-B-07: the owner's
        # question never states one disagreement twice, once in each order)
        if relation.get(y.id) == "support" and relation.get(x.id) != "support":
            x, y, reason = y, x, conflict(y, x) or reason
        if len(pairs) < MAX_CONTRADICTION_PAIRS:
            pairs.append({"a": x.id, "b": y.id, "reason": reason, "description": describe_conflict(x, y, reason),
                          "values": conflict_sides(reason)})
            paired.update((x.id, y.id))

    for i, x in enumerate(order):
        for y in order[i + 1:]:
            if group_of[x.id] == group_of[y.id] or similarity(x, y) <= 0:
                continue
            reason = conflict(x, y)
            if reason:
                add_pair(x, y, reason)
    if rep is not None:      # a contradiction counted against the representative is always listed (C-16)
        for c in order:
            if relation[c.id] == "contradict" and c.id not in paired:
                add_pair(rep, c, conflict(rep, c) or "conflicts with the representative statement")
    return {"alpha": alpha, "beta": beta, "representative": rep,
            "agree": [c.id for c in order if relation[c.id] == "support"],
            "contra": [c.id for c in order if relation[c.id] == "contradict"],
            "related": [c.id for c in order if not relation[c.id]],
            "pairs": pairs, "groups": groups, "support_groups": support_groups, "contra_groups": contra_groups,
            "order": shown}


def _build(claims, *, now=None, freshness_class=None, key: str = "", ttl_overrides=None, origins=None) -> ClaimCluster:
    ev = evaluate(claims, now=now, freshness_class=freshness_class, ttl_overrides=ttl_overrides, origins=origins)
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
          ttl_overrides: dict | None = None, origins: dict | None = None) -> ClaimCluster:
    """Re-score a cluster with temporal factors: Beta(1,1) prior; each independent group adds weight
    w = reliability(source type) x temporal factor to alpha (agreeing) or beta (contradicting).
    confidence = posterior mean, uncertainty = posterior sd. Rank/popularity are never inputs. `ttl_overrides`
    (WebConfig.freshness_ttl) replaces the class TTL used for the staleness factor. `origins` (document_origins)
    joins copies of one document into one independent group."""
    return _build(cluster.claims, now=now, freshness_class=freshness_class, key=cluster.key,
                  ttl_overrides=ttl_overrides, origins=origins)


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


__all__ = ["CATEGORY_POLICY", "DISPUTE_WEIGHT_RATIO", "LEGACY_LABELS", "STATE_TO_LEGACY", "VERIFICATION_MIN_CONFIDENCE",
           "VERIFICATION_STATES", "VerificationState", "document_origins", "normalise_state", "official_source",
           "first_person", "origin_keys", "site_label", "stamp_origins", "timely", "verdict_state", "verification_state",
           "CLUSTER_KEY_JACCARD", "CLUSTER_TEXT_SHINGLE", "DUPLICATE_SHINGLE", "KEY_TOKENS", "MAX_WORDS",
           "MIN_WORDS", "PRIOR_ALPHA", "PRIOR_BETA", "RELIABILITY_PRIMARY", "RELIABILITY_SECONDARY",
           "RENDER_ONLY_PATTERNS", "STALE_FACTOR", "UNDATED_FACTOR", "adversarial_document", "agrees", "bearing",
           "claim_key", "claim_kind", "cluster", "conflict", "conflict_sides", "contradictions", "describe_conflict",
           "different_subjects", "encyclopedia", "evaluate", "evidence_weight", "extract_claims", "fragment", "independence_groups",
           "info_gain", "is_adversarial", "is_primary", "is_reportable", "negation_cue", "polarity", "posterior",
           "primary_subject", "record_identity", "record_template", "registry_record", "restates", "same_subject", "score", "similarity",
           "subject_tokens", "subjects_compatible", "title_subject"]
