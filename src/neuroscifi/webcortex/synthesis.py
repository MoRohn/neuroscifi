"""Synthesis: the research report (ARCHITECTURE §5) and the bounded working-memory pack.

There is no language model (ADR-022). Every answer sentence is either
  (a) a claim quoted verbatim from a source (type "quote"), with citations to every source that supports it, or
  (b) a template over measured quantities (type "template"): counts, statuses, dates, or a structured API record's
      fields ("PyPI reports numpy latest version 2.3.5 ...").
Nothing asserts a fact that is not in a source. Contradictions are shown with both sides and the basis of any
assessment; failures, staleness, cache use, undated and injection-flagged sources are always surfaced.

Constants
---------
MAX_ANSWER_ITEMS        quoted statements in the answer (besides the lead template)
MAX_CONTRADICTIONS      contradiction entries listed in the report
LIVE_MODES              retrieval modes that count as "retrieved live during this task"
SUPPORTED_MIN_CONFIDENCE, SUPPORTED_MIN_GROUPS   fact-verification thresholds for "supported"
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
SUPPORTED_MIN_CONFIDENCE = 0.75
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


def _fresh_ts(doc: Document) -> float | None:
    """When the source's content is known to be current: its publication date; a structured API record fetched
    live also counts at its retrieval time (registry state as observed). HTTP Last-Modified is not used."""
    ts = doc.published_at
    if doc.kind == "api" and doc.retrieval_mode != "cache":
        ts = doc.retrieved_at if ts is None else max(ts, doc.retrieved_at)
    return ts


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
        key = self._key(url)
        if key in self.numbers:
            return self.numbers[key]
        n = len(self.entries) + 1
        self.numbers[key] = n
        d = self.by_url.get(url)
        if d is not None:
            entry = {"n": n, "title": d.title, "url": key, "domain": d.domain, "published_at": d.published_at,
                     "retrieved_at": d.retrieved_at, "retrieval_mode": d.retrieval_mode, "kind": d.kind,
                     "flags": dict(d.flags), "cited": True}
        else:
            flags = (claim.flags or {}) if claim else {}
            entry = {"n": n, "title": claim.title if claim else "", "url": key,
                     "domain": claim.domain if claim else "", "published_at": claim.published_at if claim else None,
                     "retrieved_at": claim.retrieved_at if claim else None,
                     "retrieval_mode": flags.get("retrieval_mode", "live"), "kind": flags.get("doc_kind", "page"),
                     "flags": {}, "cited": True}
        self.entries.append(entry)
        return n

    def doc(self, url: str) -> Document | None:
        return self.by_url.get(url)

    def number(self, doc: Document) -> int | None:
        return self.numbers.get(doc.canonical_url or doc.final_url or doc.url)

    def cited_docs(self) -> list[Document]:
        seen, out = set(), []
        for e in self.entries:
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
                                     "retrieval_mode": d.retrieval_mode, "kind": d.kind, "flags": dict(d.flags),
                                     "cited": False})
        return self.entries


def _cite_cluster(c: ClaimCluster, sources: _Sources) -> list[int]:
    support = set(c.supporting)
    nums = []
    for x in c.claims:
        if x.id in support:
            nums.append(sources.cite(x.source_url, x))
    return sorted(set(nums))


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

def _target_claim(statement: str, now: float) -> Claim:
    stems = [T.stem(t) for t in T.content_tokens(statement)]
    return Claim(id="target", text=statement, source_url="", domain="", title="", retrieved_at=now,
                 key=_claims.claim_key(statement, stems), polarity=_claims.polarity(statement),
                 values=T.values(statement))


def _match(target: Claim, c: ClaimCluster) -> float:
    best = max((_claims.similarity(target, x) for x in c.claims), default=0.0)
    if best > 0:
        return 1.0 + best
    t = {T.stem(w) for w in T.content_tokens(target.text)}
    lex = max((T.jaccard(t, {T.stem(w) for w in T.content_tokens(x.text)}) for x in c.claims), default=0.0)
    return lex if lex >= 0.25 else 0.0


def _verdict(statement: str, clusters: list[ClaimCluster], sources: _Sources, *, now: float, cls: str) -> dict:
    target = _target_claim(statement, now)
    ranked = sorted(((_match(target, c), i, c) for i, c in enumerate(clusters)), key=lambda t: (-t[0], t[1]))
    if not ranked or ranked[0][0] <= 0:
        return {"label": "insufficient", "confidence": 0.5, "statement": statement, "citations": [],
                "reasons": ["No extracted claim addresses the statement."]}
    c = ranked[0][2]
    ev = _claims.evaluate(c.claims, now=now, freshness_class=cls, target=target)
    a, b = ev["alpha"], ev["beta"]
    mean = a / (a + b)
    sup, con = ev["support_groups"], ev["contra_groups"]
    agree = set(ev["agree"])
    reasons = [f"{sup} independent source group(s) agree with the statement; {con} contradict it.",
               f"Posterior mean {mean:.2f} from a Beta(1,1) prior and weighted independent groups."]
    for x in c.claims:
        if x.id not in agree:
            why = _claims.conflict(target, x)
            if why:
                reasons.append(f"[{sources.cite(x.source_url, x)}] {why}.")
            if len(reasons) >= 5:
                break
    if mean >= SUPPORTED_MIN_CONFIDENCE and sup >= SUPPORTED_MIN_GROUPS and con == 0 and not ev["pairs"]:
        label = "supported"
    elif (b - _claims.PRIOR_BETA) > (a - _claims.PRIOR_ALPHA):
        label = "disputed"
    elif sup >= 1 and con >= 1:
        label = "contested"
    else:
        label = "insufficient"
        if sup < SUPPORTED_MIN_GROUPS:
            reasons.append(f"Fewer than {SUPPORTED_MIN_GROUPS} independent source groups support it.")
    citations = sorted({sources.cite(x.source_url, x) for x in c.claims})
    return {"label": label, "confidence": round(mean, 3), "statement": statement, "citations": citations,
            "reasons": reasons}


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
        ordered = sorted(c.claims, key=lambda x: (x.id not in support, -x.relevance, x.id))
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
               failures=()) -> dict:
    """The research report (ARCHITECTURE §5). Clusters are re-scored with `now` and the question's freshness
    class, ranked by value = confidence x relevance x freshness, and the top MAX_ANSWER_ITEMS are quoted."""
    assessment = dict(assessment or {})
    documents = list(documents or [])
    failures = list(failures or [])
    mode = assessment.get("mode") or "deep_research"
    cls = temporal.classify(question, assessment)
    window = assessment.get("recency_window")
    class_ttl = temporal.ttl(cls)
    required = float(window) if window else class_ttl
    sources = _Sources(documents)

    scored = [_claims.score(c, now=now, freshness_class=cls) for c in clusters if c.claims]
    usable = [c for c in scored if c.status != "insufficient"]

    def value(c: ClaimCluster) -> float:
        rep = _representative(c)
        ok = temporal.window_ok(_claim_fresh_ts(rep), required, now) if rep else None
        factor = 1.0 if ok else (UNDATED_VALUE if ok is None else STALE_VALUE)
        return c.confidence * (rep.relevance if rep else 0.0) * factor

    ranked = sorted(((c, value(c)) for c in usable), key=lambda t: (-t[1], t[0].key, t[0].statement))

    # answer items (quotes) first: they get the lowest source numbers
    items = []
    for c, v in ranked[:MAX_ANSWER_ITEMS]:
        rep = _representative(c)
        items.append({"text": c.statement, "type": "template" if rep and rep.kind == "metadata" else "quote",
                      "citations": _cite_cluster(c, sources), "confidence": c.confidence,
                      "uncertainty": c.uncertainty, "status": c.status,
                      "independent_sources": c.independent_sources, "value": round(v, 6)})

    packages = assessment.get("packages") or []
    findings = []
    for c, v in ranked:
        rep = _representative(c)
        findings.append({"statement": c.statement,
                         "type": "template" if rep and rep.kind == "metadata" else "quote",
                         "kind": rep.kind if rep else "statement", "key": c.key,
                         "citations": _cite_cluster(c, sources), "confidence": c.confidence,
                         "uncertainty": c.uncertainty, "status": c.status,
                         "independent_sources": c.independent_sources, "value": round(v, 6),
                         "published_at": rep.published_at if rep else None, "event_at": rep.event_at if rep else None,
                         "claims": [x.id for x in c.claims], "package": _package_of(c, packages)})
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
            if a is None or b is None:
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
        if d.published_at is None and not (d.kind == "api" and d.retrieval_mode != "cache"):
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
    for d in documents:
        if d.flags.get("truncated"):
            n = sources.number(d)
            ref = f"[{n}] " if n else ""
            uncertainties.append(f"Truncated: {ref}{d.domain} exceeded the size limit; later content was not read.")
    if failures:
        labels = ", ".join(dict.fromkeys(f"{w}: {c}" for w, c in map(_failure_label, failures)))
        uncertainties.append(f"{len(failures)} retrieval failure(s) may have hidden evidence ({labels}).")

    verdict = None
    if mode == "fact_verification":
        verdict = _verdict(assessment.get("claim") or question, usable, sources, now=now, cls=cls)
    comparison = _comparison(assessment.get("comparison"), ranked, sources) if mode == "comparative" else None
    methodology = _methodology(documents, sources) if mode == "scientific" else None

    lead_citations = sorted({n for it in items for n in it["citations"]})
    answer = [{"text": _lead(documents, usable, ranked, satisfied, newest, required, now, live, cited),
               "type": "template", "citations": lead_citations, "confidence": None, "status": "summary"}] + items

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
    groups = _claims.independence_groups(c.claims)
    by_id = {x.id: x for x in c.claims}
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
    return {"a": sa, "b": sb, "reason": reason, "assessment": " ".join(parts)}


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


__all__ = ["LIVE_MODES", "MAX_ANSWER_ITEMS", "UNRESOLVED", "context_pack", "synthesize"]
