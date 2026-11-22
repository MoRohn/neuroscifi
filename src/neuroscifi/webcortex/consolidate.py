"""Controlled consolidation: a task's scored claim clusters -> versioned knowledge candidates (ADR-026, ADR-038).

ADR-038 (docs/knowledge/KNOWLEDGE_EVOLUTION.md §2): eligibility follows the ADR-035 verification state of each
cluster (evolution.cluster_verdict). Only 'supported' clusters (basis corroboration, official_source or
registry_record, recorded on the row) may become knowledge candidates; 'insufficient', 'temporally_unresolved' and
'not_evaluated' clusters are held and kept as research opportunities (web_hypotheses, each with a testable
prediction); 'conflicting' and 'disputed' (contested) clusters record a contradiction (against live knowledge, or
as a hypothesis with 'contradicts' edges). The owner-review path of ADR-026 is unchanged. Every new row records its
provenance kind ('verified_conclusion'), verification record and publication/event/retrieval times, and typed
knowledge-graph edges (claim -> source / knowledge / interval, knowledge -> knowledge, entity -> entity). A
user-asserted row is never superseded by web evidence: a conflicting cluster is recorded as a contradiction. After
the loop the task's verified clusters settle open hypothesis predictions (evolution.observe).

Steps for each scored cluster of the task (state.load_clusters):
1. candidates: clusters ranked by confidence;
2. relevance and evidence quality: claims from the sources the answer set aside (`set_aside`, the report's:
   another entity, another sense of the name, a record that never names the checked statement's subject - read,
   never cited) leave the cluster first and it is re-scored as synthesis re-scores it; a cluster made only of
   them is *held* ("read, not cited"), so they never become knowledge, owner questions, hypotheses or prediction
   observations. Then claims from adversarial sources (claim.flags['adversarial_source'], or a source of this task
   whose quarantine found instruction-like content) are removed from the cluster - they never support, strengthen
   or contest knowledge, and a cluster made only of them is *held*; then
   'corroborated', or 'single_source' from a primary structured source (an API document or a metadata claim),
   with at least one relevant claim; 'contested' clusters become owner questions instead; everything else is
   *held* (stays a claim, listed with reasons);
3. duplication: live knowledge with the same (or a near-identical) key and the same values/polarity is refreshed
   (last_verified_at, supporting claims, sources), never duplicated -> feedback 'strengthened' (or 'unchanged');
4. comparison with memory: same key, conflicting values or polarity -> temporal.supersedes decides (a later
   statement of a series - every value group differs - updates the item when it is newer). A newer cluster
   that meets the provisional bar becomes a new version and the old row becomes 'superseded' (valid_until = the
   new valid_from, superseded_by = new id) -> feedback 'reversed'. Otherwise the contradiction is recorded on the
   knowledge (contradicting list), a 'contradicts' link is stored and the owner is asked -> feedback 'weakened';
5. provenance and time: sources, supporting claim ids, freshness class, validity interval;
6. commit: a new item is written only as 'provisional', and only with >= config.auto_provisional_min_sources
   independent sources, no contradiction and confidence >= config.auto_provisional_min_confidence. It takes the
   key's next version (MAX(version) + 1 over every row of the key, live or archived/rejected/superseded). The
   owner's rejection stands: a statement with the same values and polarity as a rejected row of the same (or a
   near-identical) key is *held* ("owner rejected this before"), never proposed again; a different value may
   become a new version. Each new item
   gets a pending question (scope web:knowledge:<id>) so acceptance stays the owner's decision - within the web
   question budget: at most config.max_web_questions_per_task new questions per task and none while
   config.max_open_web_questions are open; the rest stay provisional (or recorded) without a question of their
   own and ONE aggregate question (scope web:review:<task>) says how many await review. Only a specific,
   well-formed claim on the question's subject is proposed at all (unaskable: R1-02, R2-04; others are held), the
   questions go to the most valuable candidates first, and a statement already asked is not asked again.
A contested cluster about live knowledge compares values first: when its supporting side agrees with the item,
the item is strengthened by it and never lowered; only claims that really conflict with the item are recorded as
contradictions, each citing that claim (see _contested_against).
Nothing is written while instance regulation pauses writes (WritesPaused).
Statements in the summary (held[], contested) are report-safe: text a claim check marks unreportable (instruction-like
content, claims.is_reportable / synthesis.report_safe_text when available, else safety.scan_injection) is withheld.
Entities and mentions are extracted from claim text (capitalised names, CamelCase, package names, acronyms,
identifiers). Feedback rows record before/after confidence and the information gain (entropy reduction, bits).
The binary-belief tables are never touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from ..instance import store
from . import claims as _claims, evolution, knowledge, state
from .curiosity import entropy

MIN_RELEVANCE = 0.0          # a cluster needs at least one claim with relevance above this
MIN_QUESTION_RELEVANCE = 0.3  # ... and one at least this relevant to the task's question to be asked (R2-04)
SIMILAR_KEY = 0.8            # token-set Jaccard above which two keys name the same subject
MAX_ENTITIES_PER_CLAIM = 12
MAX_NEW_HYPOTHESES_PER_TASK = 5   # new research opportunities one task may open (T1-17; refreshes do not count)

_ENTITY_PATTERNS = (
    ("identifier", re.compile(r"\b(?:CVE-\d{4}-\d{4,7}|GHSA(?:-[0-9a-z]{4}){3}|10\.\d{4,9}/[^\s\"'<>]+)",
                              re.IGNORECASE)),
    ("name", re.compile(r"\b[A-Z][a-z0-9]+(?:\s+(?:of\s+|de\s+|von\s+)?[A-Z][a-z0-9]+)+\b")),
    ("name", re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")),                   # CamelCase
    ("package", re.compile(r"(?<![\w.])[a-z][a-z0-9]+(?:[-_][a-z0-9]+)+(?![\w.])")),  # kebab/snake package names
    ("acronym", re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")),
)


def _key_tokens(key):
    return set(re.findall(r"[^\W_]+|<\w+>", key or ""))


def _similar(a, b) -> float:
    ta, tb = _key_tokens(a), _key_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _compare(cluster_values, polarity, item):
    """('same' | 'conflict' | 'series', reason) between a cluster and a knowledge item, by text.values_conflict
    applied separately to temporal values (dates, years) and the other values. Both differing is a series
    (text.values_conflict: another reference period, not a contradiction); one differing is a conflict."""
    from . import text
    if polarity != item["polarity"]:
        return "conflict", "opposite polarity (one source asserts, the other negates)"
    temporal_kinds = ("date", "year")

    def split(values):
        when = [v for v in values if text.value_kind(v)[0] in temporal_kinds]
        return when, [v for v in values if v not in when]

    (new_when, new_other), (old_when, old_other) = split(list(cluster_values)), split(list(item["claim_values"]))
    other = text.values_conflict(new_other, old_other)
    when = text.values_conflict(new_when, old_when)
    if other and when:
        return "series", f"{other}; {when} (another reference period)"
    if other or when:
        return "conflict", other or when
    return "same", None


def _later(new, old) -> bool:
    """Effective time (event date, else publication date) of `new` strictly after `old`'s (retrieval bounds an
    undated old statement)."""
    from . import temporal
    t_new, t_old = temporal.effective_time(new), temporal.effective_time(old)
    t_old = old.get("retrieved_at") if t_old is None else t_old
    return t_new is not None and t_old is not None and t_new > t_old


def _head(cluster):
    support = set(cluster.supporting)
    members = [c for c in cluster.claims if c.id in support] or list(cluster.claims)
    return members[0]


def _values(cluster):
    head = _head(cluster)
    return list(head.values or []), (-1 if head.polarity < 0 else 1)


def _times(cluster):
    support = set(cluster.supporting) or {c.id for c in cluster.claims}
    members = [c for c in cluster.claims if c.id in support]
    published = [c.published_at for c in members if state.finite(c.published_at) is not None]
    events = [c.event_at for c in members if state.finite(c.event_at) is not None]
    retrieved = [c.retrieved_at for c in members if state.finite(c.retrieved_at) is not None]
    return (max(published) if published else None, max(events) if events else None,
            max(retrieved) if retrieved else None)


def _validity(cluster, now):
    """(valid_from, valid_until) by temporal.validity over the newest event/publication date of the supporting
    claims (retrieval time as the upper bound for undated sources); open-ended until superseded. A stated date in
    the future never makes knowledge valid from the future: valid_from is at most `now`."""
    from . import temporal
    published, event, retrieved = _times(cluster)
    out = temporal.validity({"event_at": event, "published_at": published, "retrieved_at": retrieved}, now=now)
    return min(state.finite(out.get("valid_from"), now), now), state.finite(out.get("valid_until"))


def _supersedes(new, old, now):
    """temporal.supersedes, passing `now` where the installed temporal module accepts it (statements dated in the
    future must not win)."""
    import inspect
    from . import temporal
    try:
        takes_now = "now" in inspect.signature(temporal.supersedes).parameters
    except (TypeError, ValueError):
        takes_now = False
    return bool(temporal.supersedes(new, old, now=now) if takes_now else temporal.supersedes(new, old))


def _as_temporal(cluster, now, key):
    published, event, retrieved = _times(cluster)
    values, polarity = _values(cluster)
    return {"key": key, "text": cluster.statement or _head(cluster).text, "values": values, "polarity": polarity,
            "published_at": published, "event_at": event, "retrieved_at": retrieved}


def _item_temporal(item):
    sources = item["sources"]
    published = [s.get("published_at") for s in sources if state.finite(s.get("published_at")) is not None]
    return {"key": item["key"], "text": item["statement"], "values": item["claim_values"], "polarity": item["polarity"],
            "published_at": max(published) if published else None, "event_at": item["valid_from"],
            "retrieved_at": item["last_verified_at"], "valid_from": item["valid_from"]}


def _sources(cluster, extra=()):
    seen, out = set(), []
    for s in list(extra) + [{"url": c.source_url, "title": c.title, "domain": c.domain,
                              "published_at": state.finite(c.published_at), "retrieved_at": c.retrieved_at}
                             for c in cluster.claims if c.id in set(cluster.supporting) or not cluster.supporting]:
        if s.get("url") and s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out


def _adversarial_urls(db, task_id) -> set:
    """Canonical URLs of this task's sources whose quarantine found instruction-like content
    (claims.adversarial_document: an image beacon found only in the Markdown rendering, such as a Wikipedia
    thumbnail with tracking parameters, does not make a page adversarial)."""
    from types import SimpleNamespace
    from .claims import adversarial_document
    out = set()
    for url, flags in db.execute("SELECT canonical_url, flags FROM web_sources WHERE task_id=?", (task_id,)):
        if adversarial_document(SimpleNamespace(flags=state.loads(flags, {}) or {})):
            out.add(url)
    return out


def _is_adversarial(claim, adversarial_urls) -> bool:
    """claims.is_adversarial (flags['adversarial_source'], set at extraction for documents with injection findings),
    or a claim of this task whose source's flags record injection findings (claims stored before that flag)."""
    from .claims import is_adversarial
    return is_adversarial(claim) or claim.source_url in adversarial_urls


def _without_adversarial(cluster, adversarial_urls):
    """(cluster without adversarial claims, number removed). Independent sources are recounted over the remaining
    supporting claims' domains; contradictions involving a removed claim are dropped, and a 'contested' cluster
    left without one is corroborated or single-source again."""
    from dataclasses import replace
    bad = {c.id for c in cluster.claims if _is_adversarial(c, adversarial_urls)}
    if not bad:
        return cluster, 0
    claims = [c for c in cluster.claims if c.id not in bad]
    supporting = [cid for cid in cluster.supporting if cid not in bad]
    contradicting = [p for p in cluster.contradicting if p.get("a") not in bad and p.get("b") not in bad]
    support = set(supporting) or {c.id for c in claims}
    groups = len({(c.domain or c.source_url).lower() for c in claims if c.id in support})
    status = cluster.status
    if status in ("contested", "corroborated"):
        status = "contested" if contradicting else ("corroborated" if groups >= 2 else "single_source")
    statement = cluster.statement
    if claims and statement in {c.text for c in cluster.claims if c.id in bad}:
        statement = claims[0].text
    return replace(cluster, claims=claims, supporting=supporting, contradicting=contradicting,
                   independent_sources=min(cluster.independent_sources, groups), status=status,
                   statement=statement), len(bad)


def _eligibility(db, task_id, cluster, removed=0):
    """(eligible, reasons): relevance gate before verification and memory comparison (the evidence-quality bar is
    the ADR-035 verification state, _meets_bar)."""
    reasons = []
    if not cluster.claims:
        return False, ["every claim comes from a source with instruction-like content (held, never knowledge)"
                       if removed else "no claims"]
    if max(state.finite(c.relevance, 0.0) for c in cluster.claims) <= MIN_RELEVANCE:
        reasons.append("no claim is relevant to the question")
    if cluster.status == "insufficient":
        reasons.append("insufficient evidence")
    return not reasons, reasons


def _unverified(verdict, cluster=None) -> list[str]:
    """Why a cluster whose ADR-035 state is not 'supported' is not knowledge."""
    out = [f"verification state {verdict['state']} ({verdict.get('reason') or verdict.get('category_reason')}): "
           "kept as a research opportunity, not knowledge"]
    groups = (verdict.get("independent_groups") or {}).get("supporting", 0)
    needed = verdict.get("min_groups")
    if verdict["state"] in ("insufficient", "temporally_unresolved") and needed and groups < needed:
        out.append(f"{groups} independent source(s) < {needed} required"
                   + (" (single source that is not an authoritative record)" if groups == 1 else ""))
    if verdict["state"] == "temporally_unresolved":
        out.append("no source dated inside the time window establishes that it holds now")
    if cluster is not None and cluster.contradicting:
        out.append("the sources contradict each other")
    return out


def _meets_bar(cluster, cfg, verdict):
    """ADR-038: the ADR-035 state must be 'supported' (corroboration, or a direct authoritative record when nothing
    contradicts) and the cluster has no contradiction. For corroboration the owner's floors apply on top
    (auto_provisional_min_sources independent groups, auto_provisional_min_confidence over the category posterior);
    they can only raise the ADR-035 bar, never lower it."""
    reasons = []
    if verdict["state"] != "supported":
        reasons += _unverified(verdict)
    elif verdict.get("basis") == "corroboration":
        groups = (verdict.get("independent_groups") or {}).get("supporting", 0)
        if groups < cfg.auto_provisional_min_sources:
            reasons.append(f"{groups} independent source(s) < {cfg.auto_provisional_min_sources} required")
        if state.finite(verdict.get("confidence"), 0.0) < cfg.auto_provisional_min_confidence:
            reasons.append(f"confidence {state.finite(verdict.get('confidence'), 0.0):.2f} < "
                           f"{cfg.auto_provisional_min_confidence:.2f}")
    if cluster.contradicting:
        reasons.append("the sources contradict each other")
    return not reasons, reasons


_LIVE = f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})"


def _existing(db, cluster):
    """Live knowledge about the same subject: one already supported by one of the cluster's claims (claim ids hash
    the source URL and the sentence, so a re-read finds them whatever the question was), else the latest version
    with the same key, else the most similar live key above SIMILAR_KEY. Only matching rows are decoded: claim
    ids are matched in SQL (json_each), the key by the (status, key) index, similar keys over the key column."""
    ids = sorted({c.id for c in cluster.claims})
    shared = []
    for start in range(0, len(ids), 300):
        part = ids[start:start + 300]
        shared += knowledge.rows(db, f"{_LIVE} AND EXISTS (SELECT 1 FROM json_each(web_knowledge.supporting) j "
                                     f"WHERE j.value IN ({','.join('?' * len(part))}))",
                                 (*state.LIVE_KNOWLEDGE, *part))
    if shared:
        wanted = set(ids)
        return max(shared, key=lambda r: (len(wanted & set(r["supporting"])), r["version"]))
    key = cluster.key
    exact = knowledge.rows(db, f"{_LIVE} AND key=?", (*state.LIVE_KNOWLEDGE, key))
    if exact:
        return max(exact, key=lambda r: r["version"])
    best = None
    for kid, other, version in db.execute(f"SELECT id, key, version FROM web_knowledge WHERE {_LIVE}",
                                          state.LIVE_KNOWLEDGE):
        score = _similar(key, other)
        if score >= SIMILAR_KEY and (best is None or (score, version) > best[:2]):
            best = (score, version, kid)
    return knowledge.row(db, best[2]) if best else None


def next_version(db, key) -> int:
    """MAX(version) + 1 over every row of `key` (live, archived, rejected, superseded): UNIQUE(key, version)."""
    top = db.execute("SELECT max(version) FROM web_knowledge WHERE key=?", (key,)).fetchone()[0]
    return int(top or 0) + 1


def _rejected_before(db, keys, values, polarity):
    """The owner's rejected row stating the same values and polarity under one of `keys` (or a near-identical key),
    else None: a rejected statement is never proposed again."""
    keys = [k for k in dict.fromkeys(keys) if k]
    for item in knowledge.rows(db, "status='rejected'"):
        if item["key"] not in keys and not any(_similar(k, item["key"]) >= SIMILAR_KEY for k in keys):
            continue
        if _compare(values, polarity, item)[0] == "same":
            return item
    return None


WITHHELD = "(withheld: the statement carries instruction-like content)"


def safe_text(claim_or_text) -> str:
    """Report-safe text of a claim (or of a plain statement): synthesis.report_safe_text / claims.is_reportable
    when the installed modules provide them, else the injection scanner. Unreportable text becomes WITHHELD."""
    from . import claims as claims_mod, safety, synthesis
    text = getattr(claim_or_text, "text", claim_or_text)
    text = "" if text is None else str(text)
    if hasattr(claim_or_text, "text"):
        reportable = getattr(claims_mod, "is_reportable", None)
        if reportable is not None and not reportable(claim_or_text):
            return WITHHELD
        report_safe = getattr(synthesis, "report_safe_text", None)
        if report_safe is not None:
            out = report_safe(claim_or_text)
            return WITHHELD if out is None else str(out)
    return WITHHELD if text and safety.scan_injection(text) else text


def _statement(cluster) -> str:
    """The cluster's statement, report-safe (the claim stating it is checked when it is a member)."""
    statement = cluster.statement or (_head(cluster).text if cluster.claims else "")
    source = next((c for c in cluster.claims if c.text == statement), None)
    return safe_text(source if source is not None else statement)


def _held(cluster, reasons, status=None) -> dict:
    return {"key": cluster.key, "statement": _statement(cluster), "status": status or cluster.status,
            "reasons": list(reasons)}


def _bits_gain(before, after):
    """Entropy reduction (bits) of the binary posterior that the statement holds."""
    return round(entropy(0.5 if before is None else before) - entropy(after), 6)


def _feedback(db, task_id, knowledge_id, key, effect, before, after, detail, now):
    db.execute("INSERT INTO web_feedback(task_id,knowledge_id,key,effect,before_confidence,after_confidence,"
               "info_gain,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (task_id, knowledge_id, key, effect, before, after, _bits_gain(before, after),
                state.dumps(detail or {}), now))


# R1-02: an "Accept as knowledge" question costs the owner's attention, so it is raised only for a specific,
# well-formed claim. Headings ("Advice for using NumPy on untrusted data#"), titles ("How NumPy ... enabled ..."),
# context-bound sentences ("NumPy enables many of these analyses.") and unquantified generalities ("allow for a
# large amount of complexity") were each asked on the RC2 retest and filled the owner's question budget.
_TITLE_START = re.compile(r"^(?:how|why|what|when|where|which|who|whose)\b", re.I)
_SENTENCE_END = re.compile(r"[.!][\"'”’)\]]*$")
_VAGUE = re.compile(r"\b(?:many|much|several|various|numerous|some|a\s+(?:large|small|great|huge|vast)\s+"
                    r"(?:amount|number|deal)\s+of|a\s+lot\s+of|lots\s+of|a\s+number\s+of|a\s+variety\s+of)\b", re.I)
# DD-3: relevance counts the question's words, so a claim about the question's own entity that shares no other word
# with it ("Fennick is an incremental build tool ..." for "Is it true that Fennick was created by Oda Haraldsen?",
# relevance 0.07) read as off-subject. Such a claim is on the subject when it defines the entity ("X is a/an
# <class>", not "X is a crucial part of <something else>") or states a value about it, and carries no judgement or
# promotional word (marketing copy about the entity stays held).
_DEFINITION = re.compile(r"^[^,;:]+?\s(?:is|was)\s+an?\s+(?P<cls>[^,;:]+)", re.I)
_CONSTITUENT = re.compile(r"^(?:[\w-]+\s+){0,3}?(?:part|parts|piece|component|element|member|portion|feature|aspect|"
                          r"ingredient|cornerstone|backbone|pillar)\s+of\b", re.I)
_PROMOTIONAL = re.compile(r"\b(?:crucial|essential|vital|powerful|leading|premier|popular|innovative|"
                          r"cutting[\s-]edge|state[\s-]of[\s-]the[\s-]art|world[\s-]class|robust|seamless(?:ly)?|"
                          r"easy|easier|effortless(?:ly)?|versatile|vibrant|amazing|awesome|revolutionary|trusted|"
                          r"beloved|de[\s-]facto)\b", re.I)


def _about_entity_shape(s: str) -> bool:
    """`s`, a claim about the question's entity, defines it or states a value about it, without judging it."""
    from . import temporal, text as T
    if temporal._INTERPRETIVE.search(s) or _PROMOTIONAL.search(s):
        return False
    definition = _DEFINITION.match(s)
    return bool(definition and not _CONSTITUENT.match(definition.group("cls"))) or bool(T.values(s))


def unaskable(statement, relevance=1.0, *, about=False) -> str | None:
    """None when `statement` deserves an owner question: a structured record's statement, or a self-contained
    (curiosity.claim_shaped), complete sentence that is not a title and states something specific (an
    unquantified "many", "some", "a large amount of" ... without any number is too vague to check), whose best
    claim `relevance` to the task's question is at least MIN_QUESTION_RELEVANCE (R2-04: a python.org marketing
    sentence, relevance 0.23, was proposed for "When was Python first released?"), or that is `about` the question's
    entity (synthesis.subject_tier 0) and defines it or states a value about it (DD-3). Else why not."""
    from .curiosity import claim_shaped
    s = " ".join(str(statement or "").split())
    if state.finite(relevance, 0.0) < MIN_QUESTION_RELEVANCE and not (about and _about_entity_shape(s)):
        return f"off the question's subject (relevance {state.finite(relevance, 0.0):.2f} < {MIN_QUESTION_RELEVANCE})"
    if s and _claims.record_template(s) is not None:
        return None
    why = claim_shaped(s)
    if why is not None:
        return why
    if not _SENTENCE_END.search(s):
        return "not a complete sentence (a heading, a title or cut-off text)"
    if _TITLE_START.match(s):
        return "a title or a question, not a statement"
    if _VAGUE.search(s) and not re.search(r"\d", s):
        return "too vague to check (an unquantified 'many', 'some' or 'a large amount of')"
    return None


def _question_focus(task) -> dict:
    """The task question's entities and relation words (synthesis.question_focus, with the run's assessment)."""
    from . import synthesis
    return synthesis.question_focus(task.get("objective") or "", (task.get("checkpoint") or {}).get("assessment"))


def _unaskable_cluster(cluster, focus) -> str | None:
    """unaskable for a cluster: its best claim relevance, and whether the claim stating it is about the question's
    entity (synthesis.subject_tier 0) and writes its name as the question does: a capitalised name stays
    capitalised ("A python is a large snake" shares only the word with "When was Python first released?")."""
    from . import synthesis
    statement = _statement(cluster)
    claim = next((c for c in cluster.claims if c.text == statement), _head(cluster))
    named = any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", claim.text or "", 0 if n[:1].isupper() else re.I)
                for n in focus.get("names") or ())
    return unaskable(statement, max(state.finite(c.relevance, 0.0) for c in cluster.claims),
                     about=named and synthesis.subject_tier(claim, focus) == 0)


def _question_value(cluster, verdict) -> float:
    """How much an owner question about this candidate is worth (R1-02 ranking): the ADR-035 posterior, plus 0.1
    per independent supporting group (up to 5), plus 0.1 when it states a value (a number, date or version)."""
    groups = (verdict.get("independent_groups") or {}).get("supporting", 0) or 0
    return (state.finite(verdict.get("confidence"), 0.0) + 0.1 * min(int(groups), 5)
            + (0.1 if _values(cluster)[0] else 0.0))


def _question(statement, n_sources, confidence, freshness, basis=None):
    return (f"Accept as knowledge: «{knowledge.quote(statement)}» ({n_sources} independent sources, confidence "
            f"{confidence:.2f}, freshness class {freshness}" + (f", verified by {basis}" if basis else "") + ")?")


def _window(task):
    """The question's recency window (seconds) from the task's assessment, or None."""
    assessment = (task.get("checkpoint") or {}).get("assessment") or {}
    return state.finite(assessment.get("recency_window"))


def _freshness(db, task):
    assessment = (task.get("checkpoint") or {}).get("assessment") or {}
    cls = assessment.get("freshness_class")
    if cls:
        return cls
    from . import temporal
    return temporal.classify(task["objective"], assessment or None)


class _Questions:
    """The web question budget of one consolidation: at most config.max_web_questions_per_task new questions, and
    none once config.max_open_web_questions are open (knowledge.ask); what is not asked is counted and summarised
    in ONE aggregate question for the task (knowledge.review_question). New knowledge candidates are offered and
    asked at flush() in order of _question_value, so the budget goes to the most valuable ones; a statement already
    asked in an open question is not asked again (R1-02)."""

    def __init__(self, db, cfg, task_id):
        self.db, self.cfg, self.task_id = db, cfg, task_id
        self.asked, self.deferred, self.duplicates = 0, 0, 0
        self.offered = []

    def offer(self, knowledge_id, statement, question, value):
        self.offered.append((value, knowledge_id, statement, question))

    def flush(self, now) -> list[int]:
        """Ask the offered candidates' questions, most valuable first; set each asked item's pending_id."""
        asked = []
        for _, kid, statement, question in sorted(self.offered, key=lambda o: (-o[0], o[1])):
            head = f"Accept as knowledge: «{knowledge.quote(statement)}»"
            if self.db.execute("SELECT 1 FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:knowledge:%' "
                               "AND substr(question, 1, ?) = ? LIMIT 1", (len(head), head)).fetchone():
                self.duplicates += 1
                continue
            pid = self.ask(f"web:knowledge:{kid}", question, now)
            if pid is not None:
                self.db.execute("UPDATE web_knowledge SET pending_id=? WHERE id=?", (pid, kid))
                asked.append(pid)
        self.offered = []
        return asked

    def ask(self, scope, question, now):
        if self.db.execute("SELECT 1 FROM pending WHERE scope=? AND resolved_at IS NULL",
                           (scope,)).fetchone() is None and self.asked >= self.cfg.max_web_questions_per_task:
            self.deferred += 1
            return None
        pid = knowledge.ask(self.db, scope, question, now, self.cfg)
        if pid is None:
            self.deferred += 1
        else:
            self.asked += 1
        return pid

    def close(self, now):
        return knowledge.review_question(self.db, self.task_id, self.deferred, now, self.cfg)


def _insert_knowledge(db, task_id, cluster, freshness, now, *, key, version, questions, verdict):
    values, polarity = _values(cluster)
    valid_from, valid_until = _validity(cluster, now)
    published, event, retrieved = _times(cluster)
    confidence = state.finite(cluster.confidence, 0.0)
    supporting = list(cluster.supporting) or [c.id for c in cluster.claims]
    kid = db.execute(
        "INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
        "first_verified_at,last_verified_at,valid_from,valid_until,supporting,contradicting,sources,claim_values,"
        "polarity,task_id,created_at,updated_at,provenance,verification,published_at,event_at,retrieved_at) "
        "VALUES(?,?,?,'provisional',?,?,?,?,?,?,?,?,'[]',?,?,?,?,?,?,'verified_conclusion',?,?,?,?)",
        (key, version, cluster.statement or _head(cluster).text, confidence,
         state.finite(cluster.uncertainty, 0.0), freshness, now, now, valid_from, valid_until,
         state.dumps(supporting), state.dumps(_sources(cluster)), state.dumps(values), polarity, task_id, now, now,
         state.dumps(verdict), published, event, retrieved)).lastrowid
    statement = _statement(cluster)
    questions.offer(kid, statement, _question(statement, cluster.independent_sources, confidence, freshness,
                                              verdict.get("basis")), _question_value(cluster, verdict))
    wanted = set(supporting)
    evolution.record_knowledge_edges(
        db, task_id, [c for c in cluster.claims if c.id in wanted], kid,
        {"published_at": published, "event_at": event, "retrieved_at": retrieved, "verified_at": now,
         "effective_at": valid_from, "valid_from": valid_from, "valid_until": valid_until}, now, head=_head(cluster))
    return kid, valid_from


def _research_opportunity(db, task_id, cluster, verdict, now, summary, *, freshness, cfg, knowledge_id=None,
                          origin=None):
    """A cluster that is not verified knowledge becomes (or refreshes) a hypothesis with a testable prediction
    (evolution.open_hypothesis), at most evolution.MAX_HYPOTHESES_PER_TASK per task. Returns its id or None."""
    from .curiosity import claim_shaped
    if len(summary["hypotheses"]) >= evolution.MAX_HYPOTHESES_PER_TASK:
        return None
    statement = _statement(cluster)
    if claim_shaped(statement) is not None:     # T1-17: record titles and context-bound sentences are no hypotheses
        return None
    values, polarity = _values(cluster)
    same = db.execute("SELECT id FROM web_hypotheses WHERE status='open' AND key!=? AND lower(statement)=?",
                      (cluster.key, " ".join(statement.split()).lower())).fetchone()
    if same is not None:        # the same statement under another key: one hypothesis, not two
        if same[0] not in summary["hypotheses"]:
            summary["hypotheses"].append(same[0])
        return same[0]
    created = db.execute("SELECT count(*) FROM web_hypotheses WHERE task_id=?", (task_id,)).fetchone()[0]
    if task_id is not None and created >= MAX_NEW_HYPOTHESES_PER_TASK and not any(
            evolution._relation(values, polarity, h["claim_values"], h["polarity"]) == "same"
            for h in evolution.hypotheses(db, status="open", key=cluster.key, limit=50)):
        return None             # this task already opened its share; only an existing hypothesis is refreshed
    origin = origin or (verdict["state"] if verdict["state"] in evolution.HYPOTHESIS_ORIGINS else "insufficient")
    hid, created = evolution.open_hypothesis(
        db, key=cluster.key, statement=statement, origin=origin, verification=verdict,
        claims=[c.id for c in cluster.claims], values=values, polarity=polarity, now=now, task_id=task_id,
        knowledge_id=knowledge_id, freshness_class=freshness, ttl_overrides=cfg.freshness_ttl or None)
    if hid not in summary["hypotheses"]:
        summary["hypotheses"].append(hid)
    return hid


def _entities(db, task_id, clusters, ids_by_key, now):
    found = 0
    for cluster in clusters:
        kid = ids_by_key.get(cluster.key)
        for c in cluster.claims:
            seen, entity_ids = set(), []
            for kind, pattern in _ENTITY_PATTERNS:
                for match in pattern.finditer(c.text or ""):
                    name = " ".join(match.group(0).split()).strip(".,;:")
                    norm = name.casefold()
                    if len(norm) < 2 or norm in seen or any(norm in s for s in seen):
                        continue
                    seen.add(norm)
                    db.execute("INSERT INTO web_entities(name,normalized,kind,first_seen,last_seen) "
                               "VALUES(?,?,?,?,?) ON CONFLICT(normalized) DO UPDATE SET last_seen=excluded.last_seen",
                               (name, norm, kind, now, now))
                    eid = db.execute("SELECT id FROM web_entities WHERE normalized=?", (norm,)).fetchone()[0]
                    db.execute("INSERT INTO web_mentions(entity_id,claim_id,knowledge_id,task_id,created_at) "
                               "VALUES(?,?,?,?,?)", (eid, c.id, kid, task_id, now))
                    entity_ids.append(eid)
                    found += 1
                    if len(seen) >= MAX_ENTITIES_PER_CLAIM:
                        break
                if len(seen) >= MAX_ENTITIES_PER_CLAIM:
                    break
            evolution.record_entity_edges(db, task_id, c.id, entity_ids, kid, now)
    return found


def _contradict(db, task_id, cluster, item, reason, now, summary, questions, *, head=None, weaken=True):
    """Record a contradiction of live knowledge by `head` (default: the cluster's head claim) and ask the owner.
    weaken=False keeps the item's confidence (a minority of the cluster disagrees). Caller owns the txn."""
    head = head or _head(cluster)
    against = list(item["contradicting"])
    known = {a.get("claim") for a in against if isinstance(a, dict)}
    fresh = head.id not in known
    if fresh:
        against.append({"claim": head.id, "task": task_id, "reason": reason, "text": knowledge.quote(safe_text(head)),
                        "url": head.source_url})
        db.execute("UPDATE web_knowledge SET contradicting=?, updated_at=? WHERE id=?",
                   (state.dumps(against), now, item["id"]))
        item["contradicting"] = against
    if item["supporting"]:
        state.add_link(db, task_id, head.id, item["supporting"][0], "contradicts",
                       {"reason": reason, "knowledge_id": item["id"]}, now)
    evolution.link(db, task_id, "claim", head.id, "contradicts", "knowledge", item["id"], {"reason": reason}, now)
    pid = questions.ask(
        f"web:knowledge:{item['id']}",
        f"Sources now contradict known «{knowledge.quote(item['statement'])}»: «{knowledge.quote(safe_text(head))}» "
        f"({reason}). Keep it as knowledge?", now)
    before = state.finite(item["confidence"], 0.0)
    after = before if not (fresh and weaken) else round(before * (1 - state.finite(cluster.confidence, 0.0) / 2), 6)
    if after != before:
        db.execute("UPDATE web_knowledge SET confidence=? WHERE id=?", (after, item["id"]))
    _feedback(db, task_id, item["id"], item["key"], "weakened" if after != before else "unchanged", before, after,
              {"contested": True, "reason": reason, "claim": head.id}, now)
    summary["contested"].append({"key": cluster.key, "knowledge_id": item["id"], "pending_id": pid,
                                 "reason": reason, "claim": head.id})
    if pid is not None:
        summary["pending"].append(pid)


def _strengthen(db, task_id, cluster, item, now, summary, ids_by_key, *, supporting=None, confidence=None,
                verdict=None):
    """Duplication: the cluster restates live knowledge -> refresh it (last_verified_at, supporting claims,
    sources), never duplicate. Caller owns the txn."""
    supporting = list(cluster.supporting) if supporting is None else supporting
    new_claims = [c for c in supporting if c not in item["supporting"]]
    before = state.finite(item["confidence"], 0.0)
    gain = state.finite(cluster.confidence, 0.0) if confidence is None else confidence
    after = max(before, gain) if new_claims else before
    status = "provisional" if item["status"] == "stale" else item["status"]
    db.execute("UPDATE web_knowledge SET last_verified_at=?, supporting=?, sources=?, confidence=?, "
               "uncertainty=min(uncertainty, ?), status=?, updated_at=? WHERE id=?",
               (now, state.dumps(item["supporting"] + new_claims),
                state.dumps(_sources(cluster, item["sources"])), after,
                state.finite(cluster.uncertainty, 1.0), status, now, item["id"]))
    head = item["supporting"][0] if item["supporting"] else None
    for cid in supporting:
        if head and cid != head:
            state.add_link(db, task_id, cid, head, "duplicates" if cid in item["supporting"] else "supports",
                           {"knowledge_id": item["id"]}, now)
    if new_claims:
        published, event, retrieved = _times(cluster)
        wanted = set(new_claims)
        evolution.record_knowledge_edges(db, task_id, [c for c in cluster.claims if c.id in wanted], item["id"],
                                         {}, now)
        db.execute("UPDATE web_knowledge SET published_at=max(coalesce(published_at, ?), coalesce(?, published_at)), "
                   "retrieved_at=max(coalesce(retrieved_at, ?), coalesce(?, retrieved_at)) WHERE id=?",
                   (published, published, retrieved, retrieved, item["id"]))
        if verdict is not None and verdict.get("state") == "supported" and item.get("provenance") != "user_asserted":
            db.execute("UPDATE web_knowledge SET verification=? WHERE id=?", (state.dumps(verdict), item["id"]))
    effect = "strengthened" if new_claims else "unchanged"
    _feedback(db, task_id, item["id"], item["key"], effect, before, after, {"new_claims": len(new_claims)}, now)
    summary["strengthened" if new_claims else "unchanged"].append(item["id"])
    ids_by_key[cluster.key] = item["id"]
    item["supporting"] = item["supporting"] + new_claims
    item["confidence"] = after


def _sides(cluster) -> list[str]:
    """The values the sources of a contested cluster disagree about ("412 km", "389 km"), in first-seen order."""
    out = []
    for pair in cluster.contradicting:
        for side in pair.get("values") or _claims.conflict_sides(pair.get("reason")) or ():
            if isinstance(side, str) and side.strip() and side not in out:
                out.append(side)
    return out


def _contested_against(db, task_id, cluster, item, reason, now, summary, questions, ids_by_key):
    """A contested cluster about live knowledge: compare values first. When the cluster's supporting side states
    the same values and polarity as the item, the item is strengthened by them (confidence never lowered) and
    only claims that really conflict with it are recorded as contradictions, each citing that claim. When the
    supporting side conflicts with the item, the item is contradicted by it (citing the supporting head)."""
    values, polarity = _values(cluster)
    relation, differ = _compare(values, polarity, item)
    if relation != "same":
        _contradict(db, task_id, cluster, item, differ or reason, now, summary, questions)
        return
    support = set(cluster.supporting) or {c.id for c in cluster.claims}
    _strengthen(db, task_id, cluster, item, now, summary, ids_by_key,
                supporting=[c for c in cluster.supporting if c in support] or [c.id for c in cluster.claims],
                confidence=state.finite(item["confidence"], 0.0))
    for claim in cluster.claims:
        if claim.id in support:
            continue
        other, why = _compare(list(claim.values or []), -1 if claim.polarity < 0 else 1, item)
        if other != "same":
            _contradict(db, task_id, cluster, item, why or reason, now, summary, questions, head=claim,
                        weaken=False)


def _without_set_aside(cluster, set_aside, *, now, freshness, ttl_overrides):
    """The cluster without claims from the `set_aside` sources, re-scored as synthesis re-scores its clusters
    (claims.score: supporting side, contradictions, statement and confidence over the remaining claims); with no
    claims left when every claim came from one of them. Unchanged when none did."""
    from dataclasses import replace
    kept = [c for c in cluster.claims if c.source_url not in set_aside]
    if len(kept) == len(cluster.claims):
        return cluster
    if not kept:
        return replace(cluster, claims=[])
    return _claims.score(replace(cluster, claims=kept), now=now, freshness_class=freshness,
                         ttl_overrides=ttl_overrides)


def run(db, task_id, now, *, holder=None, set_aside=None) -> dict:
    """Consolidate one task's scored clusters into knowledge candidates. One transaction, one state bump.

    `set_aside` ({source url: why}, the task report's set_aside): the sources the answer read but never cited.
    Their claims are removed before anything is decided (step 2), by the answer's own decision, never re-derived.
    WritesPaused while instance regulation pauses writes. With `holder` (the running worker) the transaction is
    fenced: state.LostLease when another worker took the task over meanwhile, and nothing is written."""
    cfg = state.get_config(db)
    state.require_writable(db)
    set_aside = dict(set_aside or {})
    summary = {"task_id": task_id, "candidates": 0, "provisional": [], "strengthened": [], "unchanged": [],
               "superseded": [], "contested": [], "held": [], "pending": [], "feedback": 0, "entities": 0,
               "questions_deferred": 0, "review_question": None, "hypotheses": [], "verification": [],
               "predictions_settled": []}
    with store.write(db):
        if holder is not None:
            state.require_lease(db, task_id, holder)
        questions = _Questions(db, cfg, task_id)
        task = state.task(db, task_id)
        clusters = sorted(state.load_clusters(db, task_id), key=lambda c: (-state.finite(c.confidence, 0.0), c.key))
        summary["candidates"] = len(clusters)
        freshness = _freshness(db, task)
        window = _window(task)
        focus = _question_focus(task)
        feedback_before = db.execute("SELECT count(*) FROM web_feedback").fetchone()[0]
        adversarial = _adversarial_urls(db, task_id)
        ids_by_key, cleaned, observed = {}, [], []
        for n, cluster in enumerate(clusters):
            whole, cluster = cluster, _without_set_aside(cluster, set_aside, now=now, freshness=freshness,
                                                         ttl_overrides=cfg.freshness_ttl or None)
            if whole.claims and not cluster.claims:
                summary["held"].append(_held(whole, [f"read, not cited: {why}" for why in dict.fromkeys(
                    set_aside[c.source_url] for c in whole.claims)]))
                continue
            cluster, removed = _without_adversarial(cluster, adversarial)
            cleaned.append(cluster)
            if not cluster.claims:
                summary["held"].append({"key": cluster.key, "statement": WITHHELD, "status": cluster.status,
                                        "reasons": ["every claim comes from a source with instruction-like content "
                                                    "(held, never knowledge)"]})
                continue
            verdict = evolution.cluster_verdict(cluster, now=now, freshness_class=freshness,
                                                ttl_overrides=cfg.freshness_ttl or None, window=window)
            observed.append((cluster, verdict, _statement(cluster)))
            summary["verification"].append({"key": cluster.key, "state": verdict["state"],
                                            "category": verdict.get("category"), "basis": verdict.get("basis")})
            item = _existing(db, cluster)
            if cluster.status == "contested":
                reason = "; ".join(sorted({c.get("reason", "") for c in cluster.contradicting} - {""})) or \
                    "the sources disagree"
                if item is not None:
                    _contested_against(db, task_id, cluster, item, reason, now, summary, questions, ids_by_key)
                    continue
                described = "; ".join(sorted({c.get("description") or c.get("reason") or ""
                                              for c in cluster.contradicting} - {""})) or "the sources disagree"
                sides = _sides(cluster)
                decided = knowledge.contested_decision(db, cluster.key, sides)
                if decided is None:
                    pid, note = questions.ask(
                        f"web:contested:{task_id}:{n}",
                        f"«{knowledge.quote(_statement(cluster))}»: {described} Nothing was stored as knowledge. "
                        "Which holds?", now), None
                else:           # the owner already decided this disagreement: noted, never asked again (RT4-05)
                    day = datetime.fromtimestamp(decided["decided_at"], timezone.utc).strftime("%Y-%m-%d")
                    pid, note = None, f"owner chose {decided['chosen']} on {day}"
                prior = db.execute("SELECT count(*) FROM web_feedback WHERE key=?", (cluster.key,)).fetchone()[0]
                conf = state.finite(cluster.confidence, 0.5)
                _feedback(db, task_id, None, cluster.key, "unchanged" if prior else "new", conf, conf,
                          {"contested": True, "reason": reason, "pending_id": pid, "values": sides} |
                          ({"owner_decision": note} if note else {}), now)
                hid = None
                if decided is None:   # the disagreement itself is the research opportunity (ADR-035: no consensus)
                    hid = _research_opportunity(db, task_id, cluster, verdict, now, summary, freshness=freshness,
                                                cfg=cfg, origin="disputed" if verdict["state"] == "disputed"
                                                else "conflicting")
                    for pair in cluster.contradicting:
                        if hid is not None and pair.get("b"):
                            evolution.link(db, task_id, "claim", pair["b"], "contradicts", "hypothesis", hid,
                                           {"reason": pair.get("reason", "")}, now)
                summary["contested"].append({"key": cluster.key, "knowledge_id": None, "pending_id": pid,
                                             "reason": reason, "state": verdict["state"], "hypothesis_id": hid} |
                                            ({"decided": note} if note else {}))
                if pid is not None:
                    summary["pending"].append(pid)
                continue
            eligible, reasons = _eligibility(db, task_id, cluster, removed)
            if not eligible:
                summary["held"].append(_held(cluster, reasons))
                continue
            values, polarity = _values(cluster)
            if verdict["state"] != "supported":
                rejected = _rejected_before(db, [cluster.key] + ([item["key"]] if item else []), values, polarity)
                held = _held(cluster, _unverified(verdict, cluster))
                if rejected is None:
                    held["hypothesis_id"] = _research_opportunity(
                        db, task_id, cluster, verdict, now, summary, freshness=freshness, cfg=cfg,
                        knowledge_id=item["id"] if item is not None else None)
                summary["held"].append(held | {"verification": verdict["state"]})
                continue
            if item is not None:
                relation, differ = _compare(values, polarity, item)
                if relation == "same":   # duplication: strengthen, never duplicate
                    _strengthen(db, task_id, cluster, item, now, summary, ids_by_key, verdict=verdict)
                    continue
                if item.get("provenance") == "user_asserted":   # the owner's assertion is never overwritten
                    _contradict(db, task_id, cluster, item, f"{differ}; the owner asserted the stored statement",
                                now, summary, questions, weaken=False)
                    continue
                new_t, old_t = _as_temporal(cluster, now, item["key"]), _item_temporal(item)
                if relation == "conflict":
                    newer = _supersedes(new_t, old_t, now)
                else:   # a later statement of a series updates the item; the old version keeps its period
                    newer = _later(new_t, old_t)
                    if not newer:
                        summary["held"].append(_held(cluster, [f"older statement of a series than knowledge "
                                                               f"#{item['id']} ({differ})"]))
                        continue
                rejected = _rejected_before(db, [cluster.key, item["key"]], values, polarity)
                if rejected is not None:
                    summary["held"].append(_held(cluster, [f"owner rejected this before (knowledge "
                                                           f"#{rejected['id']})"]))
                    continue
                ok, bar = _meets_bar(cluster, cfg, verdict)
                shape = _unaskable_cluster(cluster, focus)
                if newer and ok and shape is not None:
                    summary["held"].append(_held(cluster, [f"not a specific, well-formed claim ({shape}): kept as a "
                                                           "claim, no owner question"]))
                    continue
                if newer and ok:
                    kid, valid_from = _insert_knowledge(db, task_id, cluster, freshness, now,
                                                             key=item["key"], version=next_version(db, item["key"]),
                                                             questions=questions, verdict=verdict)
                    db.execute("UPDATE web_knowledge SET status='superseded', valid_until=?, superseded_by=?, "
                               "updated_at=? WHERE id=?", (valid_from, kid, now, item["id"]))
                    knowledge.close_questions(db, f"web:knowledge:{item['id']}",
                                              f"superseded by knowledge #{kid}", now)
                    if item["supporting"]:
                        state.add_link(db, task_id, _head(cluster).id, item["supporting"][0], "supersedes",
                                       {"old": item["id"], "new": kid, "reason": differ}, now)
                    evolution.link(db, task_id, "knowledge", kid, "supersedes", "knowledge", item["id"],
                                   {"reason": differ, "valid_until": valid_from}, now)
                    _feedback(db, task_id, kid, item["key"], "reversed", state.finite(item["confidence"], 0.0),
                              state.finite(cluster.confidence, 0.0), {"superseded": item["id"], "reason": differ},
                              now)
                    summary["superseded"].append({"old": item["id"], "new": kid, "reason": differ})
                    summary["provisional"].append(kid)
                    ids_by_key[cluster.key] = kid
                    continue
                why = differ if newer is False else f"{differ}; newer but below the provisional bar ({'; '.join(bar)})"
                _contradict(db, task_id, cluster, item, why, now, summary, questions)
                continue
            rejected = _rejected_before(db, [cluster.key], values, polarity)
            if rejected is not None:
                summary["held"].append(_held(cluster, [f"owner rejected this before (knowledge #{rejected['id']})"]))
                continue
            ok, bar = _meets_bar(cluster, cfg, verdict)
            if not ok:
                summary["held"].append(_held(cluster, bar))
                continue
            shape = _unaskable_cluster(cluster, focus)
            if shape is not None:
                summary["held"].append(_held(cluster, [f"not a specific, well-formed claim ({shape}): kept as a "
                                                       "claim, no owner question"]))
                continue
            kid, _ = _insert_knowledge(db, task_id, cluster, freshness, now, key=cluster.key,
                                            version=next_version(db, cluster.key), questions=questions,
                                            verdict=verdict)
            _feedback(db, task_id, kid, cluster.key, "new", None, state.finite(cluster.confidence, 0.0),
                      {"independent_sources": cluster.independent_sources, "basis": verdict.get("basis")}, now)
            summary["provisional"].append(kid)
            ids_by_key[cluster.key] = kid
        summary["pending"] += questions.flush(now)
        summary["questions_deferred"] = questions.deferred
        summary["review_question"] = questions.close(now)
        if summary["review_question"] is not None:
            summary["pending"].append(summary["review_question"])
        summary["entities"] = _entities(db, task_id, cleaned, ids_by_key, now)
        summary["predictions_settled"] = evolution.observe(db, task_id, observed, now, freshness_class=freshness,
                                                           ttl_overrides=cfg.freshness_ttl or None,
                                                           knowledge_by_key=ids_by_key)
        summary["feedback"] = db.execute("SELECT count(*) FROM web_feedback").fetchone()[0] - feedback_before
        summary["pending"] = sorted(set(summary["pending"]))
        state.log_event(db, task_id, "consolidated", {k: (len(v) if isinstance(v, list) else v)
                                                       for k, v in summary.items() if k != "task_id"}, now)
        summary["state_version"] = store.bump_state_version(db)
    return summary
