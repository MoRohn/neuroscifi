"""Offline Web Cortex benchmark: the real pipeline against a fictional web, scored against gold labels.

What runs for real: intent, the orchestrator (submit/run/cancel/resume), SafeFetcher (address policy, robots,
limits, budgets, cache, ETag revalidation), extraction (scrapling / trafilatura), quarantine, claims, synthesis,
consolidation, knowledge.query and the PyPI / npm providers (pointed at fixture registries). What is substituted:
DNS and sockets (FixtureWeb's resolver/connector serve the corpus from a loopback HTTP server) and the search
engines (providers.corpus.CorpusProvider: BM25 over title and snippet of one result per page; a second one named
"wikipedia" returns snippet-only encyclopedia hits). The task clock is fixed per task (so `budget.max_seconds` is never reached);
latency is wall-clock on this host and is excluded from the determinism comparison.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import resource
import sqlite3
import tempfile
import time
from urllib.parse import urlsplit

from ...instance import store
from .. import claims as claims_mod
from .. import extract, knowledge, orchestrator, safety, state
from .. import services as svc_mod
from ..net import NetPolicy, SafeFetcher
from ..providers.corpus import CorpusProvider
from ..providers.structured import NpmProvider, PyPIProvider
from ..synthesis import LIVE_MODES
from ..types import Budget, FetchError, FetchResult, Query, Usage
from . import corpus as C
from . import metrics as M

LABEL = ("offline benchmark: fictional corpus served by FixtureWeb through the real SafeFetcher; search is "
         "CorpusProvider (BM25 over title and snippet); no network; latency is wall-clock on this host")
SIMULATED_ACCEPTANCE = "benchmark: simulated owner acceptance of every provisional item (not a real owner decision)"
VERDICT_LABELS = ["supported", "disputed", "contested", "insufficient"]
TIGHT_BUDGETS = [("A01", {"max_queries": 1, "max_requests": 3, "max_pages": 1}),
                 ("A08", {"max_bytes": 15_000})]
RESOURCES = ("queries", "requests", "pages", "bytes", "browser_sessions")
MEMORY_CONFIG = {"auto_provisional_min_confidence": 0.0}


class _FixturePyPI(PyPIProvider):
    BASE = f"http://{C.PYPI_HOST}"


class _FixtureNpm(NpmProvider):
    BASE = f"http://{C.NPM_HOST}"


_CODE_LITERAL = re.compile(r"\b\w+\s*=\s*(?:False|None|0)\b")
_NEGATION_WORD = re.compile(r"\b(?:not|no|never|none|nobody|nothing|neither|nor|cannot|without)\b|n't\b", re.I)


def citation_rows(report: dict, text_of) -> list[dict]:
    """One row per (quote answer item, cited source): does THAT source contain the quoted claim (verbatim, or as the
    same normalised sentence)? `text_of(source)` returns the cited source's extracted text (None when unknown)."""
    by_n = {s["n"]: s for s in report.get("sources") or []}
    rows = []
    for i, item in enumerate(report.get("answer") or []):
        if item.get("type") != "quote":
            continue
        quote = item.get("text") or ""
        cites = list(item.get("citations") or [])
        if not cites:
            rows.append({"item": i, "text": quote, "citation": None, "url": None, "resolves": False, "support": None,
                         "supported": False})
        for n in cites:
            source = by_n.get(n)
            how = M.quote_support(quote, text_of(source)) if source is not None else None
            rows.append({"item": i, "text": quote, "citation": n, "url": source["url"] if source else None,
                         "resolves": source is not None, "support": how, "supported": how is not None})
    return rows


def score_citations(rows_by_task: list[tuple[str, list[dict]]]) -> dict:
    """Citation metrics over `citation_rows` of many reports.

    per_cited_source (the headline since the RC1 UAT): precision = cited sources that contain the quoted claim /
    all citations of quote answer items (an uncited quote counts as one unsupported citation); an item is fully
    supported only when EVERY cited source contains it. legacy_any_source (the first baseline's metric, kept for
    comparison): an item is supported when its citations all resolve and at least ONE cited source contains it
    verbatim."""
    items, unsupported = [], []
    for task, rows in rows_by_task:
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(r["item"], []).append(r)
        for i, group in grouped.items():
            cited = [r for r in group if r["citation"] is not None]
            items.append({"task": task, "item": i, "text": group[0]["text"], "citations": len(cited),
                          "supported_citations": sum(r["supported"] for r in cited),
                          "resolves": bool(cited) and all(r["resolves"] for r in cited),
                          "any_verbatim": any(r["support"] == "verbatim" for r in cited),
                          "all_supported": bool(cited) and all(r["supported"] for r in cited)})
            unsupported += [{"task": task, "text": r["text"][:200], "citation": r["citation"], "url": r["url"]}
                            for r in group if not r["supported"]]
    n = len(items)
    total = sum(max(1, i["citations"]) for i in items)
    good = sum(i["supported_citations"] for i in items)
    legacy = sum(1 for i in items if i["resolves"] and i["any_verbatim"])
    return {
        "quote_items": n, "supported": legacy, "rate": M._r(legacy / n if n else None),
        "unresolved_citations": sum(1 for i in items if not i["resolves"]),
        "not_verbatim": [{"task": i["task"], "text": i["text"], "citations": i["citations"]}
                         for i in items if not i["any_verbatim"]][:50],
        "definition": "LEGACY (any source): quote answer items whose text (whitespace-collapsed) is a substring of at "
                      "least one cited source's extracted text and whose citations all resolve to report sources",
        "per_cited_source": {
            "citations": total, "supported_citations": good, "precision": M._r(good / total if total else None),
            "items_every_source_supports": sum(i["all_supported"] for i in items), "items": n,
            "item_rate": M._r(sum(i["all_supported"] for i in items) / n if n else None),
            "unsupported": unsupported[:50],
            "definition": "for every quote answer item, EVERY cited source must contain the quoted claim (verbatim "
                          "or as the same normalised sentence); precision = supported citations / all citations "
                          "(an uncited quote item counts as one unsupported citation)"},
    }


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class _Crash(BaseException):
    """Stands in for the worker process dying mid-task (not an Exception: nothing in the run catches it)."""


class _Hook:
    """Wraps a SafeFetcher. Around each page fetch: before(ok, url) with the number of successful page fetches so
    far, after(ok, url) once it succeeded. `pages` counts every page fetch attempt."""

    def __init__(self, inner, *, before=None, after=None):
        self.inner, self.before, self.after, self.pages, self.ok = inner, before, after, 0, 0

    def fetch(self, url, **kwargs):
        if kwargs.get("purpose", "page") in ("page", "crawl"):
            if self.before:
                self.before(self.ok, url)
            self.pages += 1
            result = self.inner.fetch(url, **kwargs)
            self.ok += 1
            if self.after:
                self.after(self.ok, url)
            return result
        return self.inner.fetch(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def canon(url: str) -> str:
    try:
        return extract.canonical_url(url)
    except Exception:  # an unparsable URL in a report is compared verbatim
        return url


def _size(path: Path) -> int:
    """Logical size of a SQLite file (page_count x page_size, WAL frames included); 0 when it does not exist yet.
    File sizes are not used: WAL checkpoints make them jump up and down between tasks."""
    if not Path(path).exists():
        return 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(con.execute("PRAGMA page_count").fetchone()[0]) * int(con.execute("PRAGMA page_size").fetchone()[0])
    finally:
        con.close()


def _rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024     # Linux reports KiB


class _Bench:
    def __init__(self, root: Path, web, quick: bool):
        self.root, self.web, self.quick = root, web, quick
        self.provider = CorpusProvider(C.search_results())
        self.wiki = CorpusProvider(C.wiki_snippet_results(), name="wikipedia", kinds=("encyclopedia", "web"))
        self.clock = _Clock(C.T0)
        self.path = root / "bench.sqlite"
        self.db = self._individual(self.path)
        self.services = self._services(self.db, self.path)
        self.records: list[dict] = []
        self._docs: dict[str, object] = {}
        self.minute = 0
        self.check_db = self.memory_db = None
        self.probe_seconds: dict[str, float] = {}

    # ---- plumbing -------------------------------------------------------------------------------------------
    def _individual(self, path):
        db = store.create(path, C.T0, note="nsf web bench (offline, fictional corpus)")
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, C.T0)
        return db

    def _services(self, db, path):
        registries = [_FixturePyPI(None), _FixtureNpm(None)]
        services = svc_mod.build(db, path, fixture=self.web, providers=[self.provider, self.wiki, *registries],
                                 clock=self.clock, sleep=lambda seconds: None)
        for provider in registries:           # the structured providers fetch through this run's SafeFetcher
            provider.fetcher = services.fetcher
        return services

    def tick(self):
        self.minute += 1
        self.clock.t = C.T0 + 60.0 * self.minute
        return self.clock.t

    def run_task(self, key, question, *, db=None, path=None, services=None, mode=None, budget=None):
        db, path, services = db or self.db, path or self.path, services or self.services
        now = self.tick()
        cache = svc_mod.cache_path(path)
        before = (_size(path), _size(cache))
        started = time.perf_counter()
        task = orchestrator.submit(db, question, now, mode=mode, budget=budget, origin="bench")
        done = orchestrator.run(db, task["id"], services)
        wall = time.perf_counter() - started
        record = {"key": key, "question": question, "task_id": done["id"], "mode": done["mode"],
                  "status": done["status"], "error": done["error"], "report": done["result"] or {},
                  "usage": done["usage"], "budget": done["budget"], "wall_seconds": wall,
                  "db_growth": _size(path) - before[0], "cache_growth": _size(cache) - before[1], "db": db}
        self.records.append(record)
        return record

    def doc(self, page_url):
        """The corpus page as the system's own extractor sees it (fetch body from the fixture, extract, quarantine)."""
        key = canon(page_url)
        if key in self._docs:
            return self._docs[key]
        parts = urlsplit(page_url)
        route = self.web.routes.get(((parts.hostname or "").lower(), parts.path or "/"))
        doc = None
        if route is not None and route.status == 200 and route.body:
            ctype = (route.headers.get("Content-Type") or "text/html").split(";")[0].strip().lower()
            result = FetchResult(url=page_url, final_url=page_url, status=200,
                                 headers={k.lower(): v for k, v in route.headers.items()}, content_type=ctype,
                                 body=route.body, retrieved_at=C.T0)
            try:
                doc = safety.quarantine(extract.extract(result))
            except FetchError:
                doc = None
        self._docs[key] = doc
        return doc

    # ---- research tasks -------------------------------------------------------------------------------------
    def questions(self):
        chosen = [q for q in C.QUESTIONS if not self.quick or q["id"] in C.QUICK_QUESTIONS]
        return {q["id"]: self.run_task(q["id"], q["question"]) for q in chosen}

    # ---- scoring --------------------------------------------------------------------------------------------
    @staticmethod
    def sources(report):
        return {canon(s["url"]): s for s in report.get("sources") or []}

    def source_text(self, rec, source):
        """Extracted text of a report source: the API record the run read (from its checkpoint) for api sources,
        else the corpus page as the system's own extractor sees it."""
        if source.get("retrieval_mode") == "api":
            checkpoint = state.task(rec["db"], rec["task_id"])["checkpoint"] or {}
            for entry in checkpoint.get("documents") or []:
                if canon(entry.get("url") or "") == canon(source["url"]) and entry.get("doc"):
                    return entry["doc"].get("text")
        doc = self.doc(source["url"])
        return doc.text if doc is not None else None

    def authoritative(self, recs):
        items = []
        for q in C.QUESTIONS:
            if q["category"] != "authoritative" or q["id"] not in recs:
                continue
            report = recs[q["id"]]["report"]
            acquired = self.sources(report)
            gold = {canon(C.U(p)) for p in q["gold_urls"]}
            found = sorted(gold & set(acquired))
            quotes = [a["text"] for a in report.get("answer") or [] if a.get("type") == "quote"]
            top = quotes[0] if quotes else ""
            gold_answer = [g.lower() for g in q["gold_answer"]]
            item = {"id": q["id"], "status": recs[q["id"]]["status"], "mode": recs[q["id"]]["mode"],
                    "answered_from_memory": bool((report.get("memory") or {}).get("answered_from_memory")),
                    "acquired": len(acquired), "gold_found": len(found), "gold_total": len(gold),
                    "retrieval_success": bool(found), "coverage": round(len(found) / len(gold), 4),
                    "answer_correct_top": any(g in top.lower() for g in gold_answer),
                    "answer_correct_any": any(g in t.lower() for t in quotes for g in gold_answer),
                    "top_quote": top}
            noise = q.get("noise")
            if noise:
                ranked = self.provider.search(Query(text=q["question"]), 10)
                rank = next((r.rank for r in ranked if canon(r.url) == canon(C.U(noise["page"]))), None)
                paras = [M.collapse(p) for p in C.BY_ID[noise["page"]]["paras"]]
                item["noise"] = {"page": C.U(noise["page"]), "provider_rank": rank,
                                 "acquired": canon(C.U(noise["page"])) in acquired,
                                 "top_quote_from_noise_page": bool(top) and M.collapse(top) in paras}
            items.append(item)
        n = len(items)
        noisy = [i for i in items if "noise" in i]
        return {
            "items": items,
            "retrieval_success": M.rate(sum(i["retrieval_success"] for i in items), n),
            "evidence_coverage": {"found": sum(i["gold_found"] for i in items),
                                  "gold": sum(i["gold_total"] for i in items),
                                  "rate": M._r(sum(i["gold_found"] for i in items) /
                                               max(1, sum(i["gold_total"] for i in items)))},
            "answer_correct_top": M.rate(sum(i["answer_correct_top"] for i in items), n),
            "answer_correct_any_quote": M.rate(sum(i["answer_correct_any"] for i in items), n),
            "rank_noise": {"questions": len(noisy),
                           "noise_page_ranked_first": sum(1 for i in noisy if i["noise"]["provider_rank"] == 1),
                           "top_quote_from_noise_page": sum(1 for i in noisy if i["noise"]["top_quote_from_noise_page"]),
                           "top_quote_correct": sum(1 for i in noisy if i["answer_correct_top"])},
        }

    def time_sensitive(self, recs):
        items = []
        for q in C.QUESTIONS:
            if q["category"] != "time" or q["id"] not in recs:
                continue
            report = recs[q["id"]]["report"]
            fresh = report.get("freshness") or {}
            acquired = self.sources(report)
            gold = {canon(C.U(p)) for p in q["gold_in_window"]}
            cited = {k for k, s in acquired.items() if s.get("cited")}
            disclosed = any(str(lim).startswith("Freshness requirement not met") for lim in report.get("limitations") or [])
            items.append({"id": q["id"], "status": recs[q["id"]]["status"], "mode": recs[q["id"]]["mode"],
                          "gold_satisfied": q["gold_satisfied"], "satisfied": fresh.get("satisfied"),
                          "compliant": fresh.get("satisfied") == q["gold_satisfied"],
                          "window_days_expected": q["window_days"],
                          "window_days_reported": (round(fresh["window_seconds"] / C.DAY, 2)
                                                   if fresh.get("window_seconds") else None),
                          "freshness_class": fresh.get("class"),
                          "gold_in_window_acquired": sorted(gold & set(acquired)),
                          "gold_in_window_cited": sorted(gold & cited),
                          "limitation_disclosed": disclosed,
                          "disclosure_required": not q["gold_satisfied"],
                          "disclosure_ok": disclosed if not q["gold_satisfied"] else True,
                          "live": report.get("live")})
        n = len(items)
        return {"items": items, "freshness_compliance": M.rate(sum(i["compliant"] for i in items), n),
                "window_parsed_correctly": M.rate(sum(1 for i in items if i["window_days_reported"] is not None and
                                                      abs(i["window_days_reported"] - i["window_days_expected"]) < 0.01)
                                                  , n),
                "nothing_fresh_disclosed": M.rate(sum(1 for i in items if i["disclosure_required"] and
                                                      i["limitation_disclosed"]),
                                                  sum(1 for i in items if i["disclosure_required"]))}

    def date_accuracy(self):
        gold = {canon(a["url"]): C.epoch(a["date"]) for a in C.ARTICLES}
        seen, rows = set(), []
        for rec in self.records:
            for s in rec["report"].get("sources") or []:
                key = canon(s["url"])
                if key in seen or key not in gold:
                    continue
                seen.add(key)
                g, p = gold[key], s.get("published_at")
                ok = (g is None and p is None) or (g is not None and p is not None and abs(g - p) <= C.DAY)
                rows.append({"url": key, "gold": g, "extracted": p, "correct": ok})
        return {"pages": len(rows), "correct": sum(r["correct"] for r in rows),
                "rate": M._r(sum(r["correct"] for r in rows) / len(rows) if rows else None),
                "errors": [r for r in rows if not r["correct"]]}

    def verification(self, recs):
        items, pairs = [], []
        for q in C.QUESTIONS:
            if q["category"] != "verification" or q["id"] not in recs:
                continue
            verdict = recs[q["id"]]["report"].get("verdict") or {}
            label = verdict.get("label") or "none"
            pairs.append((q["gold_label"], label))
            items.append({"id": q["id"], "status": recs[q["id"]]["status"], "mode": recs[q["id"]]["mode"],
                          "gold": q["gold_label"], "predicted": label, "correct": label == q["gold_label"],
                          "confidence": verdict.get("confidence"), "reasons": (verdict.get("reasons") or [])[:3]})
        return {"items": items, "confusion": M.confusion(pairs, VERDICT_LABELS)}

    def contradictions(self, recs):
        detector, e2e = [], []
        for pair in C.PAIRS:
            if pair["question"] not in recs:
                continue
            question = recs[pair["question"]]["question"]
            ca = self._claim_for(pair["a"], pair["a_text"], question)
            cb = self._claim_for(pair["b"], pair["b_text"], question)
            reason = claims_mod.contradictions(ca, cb) if ca is not None and cb is not None else None
            row = {"id": pair["id"], "type": pair["type"], "gold_conflict": pair["conflict"],
                   "evaluable": ca is not None and cb is not None, "detected": reason is not None, "reason": reason}
            if ca is not None and cb is not None:
                row["independence_groups"] = len(claims_mod.independence_groups([ca, cb]))
            detector.append(row)
            report = recs[pair["question"]]["report"]
            by_n = {s["n"]: canon(s["url"]) for s in report.get("sources") or []}
            want = {canon(C.U(pair["a"])), canon(C.U(pair["b"]))}
            listed = [c for c in report.get("contradictions") or []
                      if {by_n.get(c["a"]["citation"]), by_n.get(c["b"]["citation"])} == want]
            acquired = want <= set(by_n.values())
            e2e.append({"id": pair["id"], "type": pair["type"], "gold_conflict": pair["conflict"],
                        "both_acquired": acquired, "detected": bool(listed),
                        "reason": listed[0]["reason"] if listed else None})

        def score(rows, only_evaluable=False):
            rows = [r for r in rows if r.get("evaluable", True) or not only_evaluable]
            tp = sum(1 for r in rows if r["gold_conflict"] and r["detected"])
            fp = sum(1 for r in rows if not r["gold_conflict"] and r["detected"])
            fn = sum(1 for r in rows if r["gold_conflict"] and not r["detected"])
            return M.prf(tp, fp, fn) | {"pairs": len(rows), "conflicts": sum(r["gold_conflict"] for r in rows),
                                         "controls": sum(not r["gold_conflict"] for r in rows)}
        return {"detector": {"pairs": detector, "all": score(detector), "evaluable_only": score(detector, True)},
                "end_to_end": {"pairs": e2e, "all": score(e2e),
                               "both_sources_acquired": sum(r["both_acquired"] for r in e2e)},
                "syndication": self._syndication(recs)}

    def _claim_for(self, page_id, sentence, question):
        doc = self.doc(C.U(page_id))
        if doc is None:
            return None
        target = M.collapse(sentence)
        for c in claims_mod.extract_claims(doc, question, max_claims=100):
            if M.collapse(c.text) == target:
                return c
        return None

    def _syndication(self, recs):
        """The syndicated story (P29 = P30 on two domains) must count as one independent source."""
        if "A08" not in recs:
            return None
        report = recs["A08"]["report"]
        by_n = {s["n"]: canon(s["url"]) for s in report.get("sources") or []}
        a, b = canon(C.U(C.SYNDICATED[0])), canon(C.U(C.SYNDICATED[1]))
        rows = []
        for f in report.get("findings") or []:
            cited = {by_n.get(n) for n in f.get("citations") or []}
            if a in cited and b in cited:
                domains = {urlsplit(u).hostname for u in cited if u}
                rows.append({"statement": f["statement"], "independent_sources": f["independent_sources"],
                             "cited_domains": len(domains),
                             "counted_once": f["independent_sources"] <= len(domains) - 1})
        return {"findings_citing_both_copies": len(rows), "rows": rows,
                "all_counted_once": bool(rows) and all(r["counted_once"] for r in rows)}

    def injection(self):
        canaries = {s["url"]: [c.lower() for c in s["canaries"]] for s in C.INJECTIONS}
        pages = {}
        for rec in self.records:
            for src in state.sources(rec["db"], rec["task_id"]):
                if src["url"] in canaries:
                    flags = src["flags"] or {}
                    page = pages.setdefault(src["url"], {"url": src["url"], "acquired_by": [], "flagged": False,
                                                         "patterns": set(), "hidden_removed": 0})
                    page["acquired_by"].append(rec["key"])
                    found = flags.get("injection") or []
                    page["patterns"] |= {f.get("pattern") for f in found}
                    page["hidden_removed"] = max(page["hidden_removed"], int(flags.get("hidden_removed") or 0))
                    page["flagged"] = page["flagged"] or bool(found) or bool(flags.get("hidden_removed"))
        leaks, report_mentions = [], []
        all_canaries = sorted({c for cs in canaries.values() for c in cs})
        for rec in self.records:
            report = rec["report"]
            texts = [a.get("text") or "" for a in report.get("answer") or []]
            texts += [f.get("statement") or "" for f in report.get("findings") or []]
            for c in report.get("contradictions") or []:
                texts += [c["a"].get("text") or "", c["b"].get("text") or ""]
            texts += [h.get("statement") or "" for h in (report.get("consolidation") or {}).get("held") or []]
            texts += [c["text"] for c in state.claims(rec["db"], rec["task_id"])]
            blob = "\n".join(texts).lower()
            for canary in all_canaries:
                if canary in blob:
                    leaks.append({"task": rec["key"], "canary": canary})
            for path, value in _strings(report):
                low = value.lower()
                for canary in all_canaries:
                    if canary in low:
                        report_mentions.append(re.sub(r"\[\d+\]", "[]", path))
        statements = "\n".join(r["statement"] for db in (self.db, self.memory_db) if db is not None
                               for r in knowledge.rows(db)).lower()
        leaks += [{"task": "knowledge", "canary": c} for c in all_canaries if c in statements]
        rows = [p | {"patterns": sorted(x for x in p["patterns"] if x)} for p in pages.values()]
        return {"injection_pages": len(canaries), "acquired": len(rows), "flagged": sum(r["flagged"] for r in rows),
                "pages": sorted(rows, key=lambda r: r["url"]), "leaks_into_claims_or_answers": leaks,
                "contained": not leaks,
                "canary_mentions_elsewhere_in_reports": dict(sorted(Counter(report_mentions).items())),
                "note": "leaks are searched in answers, findings, contradictions, consolidation.held statements, "
                        "stored claims and knowledge; mentions elsewhere are where the report quotes the flagged text "
                        "as evidence of the flag (e.g. sources[].flags.injection[].excerpt)"}

    def task_failures(self):
        by_code, by_page = Counter(), {}
        special = {canon(s["url"]): s["id"] for s in C.SPECIAL}
        for rec in self.records:
            for event in state.events(rec["db"], rec["task_id"]):
                if event["kind"] != "error":
                    continue
                detail = event["detail"] or {}
                code = detail.get("code") or detail.get("error")
                by_code[code] += 1
                url = detail.get("url")
                if url and canon(url) in special:
                    by_page.setdefault(special[canon(url)], set()).add(code)
        return {"errors_by_code": dict(sorted(by_code.items())),
                "special_pages_met_in_tasks": {k: sorted(v) for k, v in sorted(by_page.items())}}

    def liveness(self):
        violations, all_cache = [], 0
        for rec in self.records:
            report = rec["report"]
            cited = [s for s in report.get("sources") or [] if s.get("cited")]
            if not cited:
                continue
            modes = {s["retrieval_mode"] for s in cited}
            if modes == {"cache"}:
                all_cache += 1
            expected = bool(modes & set(LIVE_MODES))
            if bool(report.get("live")) != expected:
                violations.append({"task": rec["key"], "live": report.get("live"), "modes": sorted(modes)})
        return {"reports_with_citations": sum(1 for r in self.records if any(
            s.get("cited") for s in r["report"].get("sources") or [])), "all_cited_sources_cache": all_cache,
                "violations": violations, "ok": not violations}

    def budgets(self):
        rows = []
        for rec in self.records:
            over = {k: [rec["usage"].get(k, 0), rec["budget"].get("max_" + k)] for k in RESOURCES
                    if rec["usage"].get(k, 0) > rec["budget"].get("max_" + k, 0)}
            if over:
                rows.append({"task": rec["key"], "over": over})
        return {"tasks_checked": len(self.records), "violations": rows, "ok": not rows}

    # ---- checks in a separate, fresh individual ---------------------------------------------------------------
    def checks(self):
        path = self.root / "checks.sqlite"
        self.check_db = self._individual(path)      # closed in close(): its task records are scored later
        return self._checks(self.check_db, path)

    def _checks(self, db, path):
        out = {"tight_budgets": []}
        services = self._services(db, path)
        for qid, budget in TIGHT_BUDGETS:
            q = next(x for x in C.QUESTIONS if x["id"] == qid)
            rec = self.run_task(f"budget:{qid}", q["question"], db=db, path=path, services=services, budget=budget)
            out["tight_budgets"].append({
                "question": qid, "requested": budget, "status": rec["status"],
                "usage": {k: rec["usage"].get(k) for k in RESOURCES},
                "within": all(rec["usage"].get(k, 0) <= rec["budget"]["max_" + k] for k in RESOURCES),
                "limitations": [x for x in rec["report"].get("limitations") or [] if "budget" in x]})
        services.close()

        # cancel a queued task: immediate, nothing fetched
        hits = len(self.web.log)
        task = orchestrator.submit(db, C.QUESTIONS[2]["question"], self.tick(), origin="bench")
        cancelled = orchestrator.cancel(db, task["id"], self.tick())
        out["cancel_queued"] = {"status": cancelled["status"], "requests_made": len(self.web.log) - hits,
                                "ok": cancelled["status"] == "cancelled" and len(self.web.log) == hits}

        # cancel from another connection while running: the run stops before its next network step
        def cancel_after_first(ok, _url):
            if ok == 1:
                other = store.connect(path)
                try:
                    orchestrator.cancel(other, holder["id"], self.clock.t)
                finally:
                    other.close()
        services = self._services(db, path)
        services.fetcher = _Hook(services.fetcher, after=cancel_after_first)
        q = next(x for x in C.QUESTIONS if x["id"] == "A06")
        holder = orchestrator.submit(db, q["question"], self.tick(), origin="bench")
        done = orchestrator.run(db, holder["id"], services)
        services.close()
        report = done["result"] or {}
        out["cancel_running"] = {"status": done["status"], "page_fetches": services.fetcher.pages,
                                 "partial_report": bool(report),
                                 "limitation": any("cancelled" in x for x in report.get("limitations") or []),
                                 "knowledge_written": db.execute(
                                     "SELECT count(*) FROM web_knowledge WHERE task_id=?", (done["id"],)).fetchone()[0]}
        out["cancel_running"]["ok"] = (done["status"] == "cancelled" and services.fetcher.ok == 1
                                       and services.fetcher.pages == 1
                                       and out["cancel_running"]["knowledge_written"] == 0)

        # crash after the first document, then resume in a fresh process-equivalent (new services)
        def crash_on_second(ok, _url):
            if ok >= 1:          # after the first document was read and checkpointed
                raise _Crash()
        q = next(x for x in C.QUESTIONS if x["id"] == "A03")
        task = orchestrator.submit(db, q["question"], self.tick(), origin="bench")
        services = self._services(db, path)
        services.fetcher = _Hook(services.fetcher, before=crash_on_second)
        crashed = False
        try:
            orchestrator.run(db, task["id"], services)
        except _Crash:
            crashed = True
        services.close()
        mid = state.task(db, task["id"])
        read_before = [d["url"] for d in (mid["checkpoint"] or {}).get("documents", [])]
        hits_before = {u: self._hits(u) for u in read_before}
        services = self._services(db, path)
        self.tick()
        resumed = orchestrator.resume(db, task["id"], services)
        services.close()
        report = resumed["result"] or {}
        modes = {canon(s["url"]): s["retrieval_mode"] for s in report.get("sources") or []}
        out["crash_resume"] = {
            "crashed": crashed, "status_after_crash": mid["status"], "documents_checkpointed": len(read_before),
            "status_after_resume": resumed["status"],
            "refetched_checkpointed_pages": sum(self._hits(u) - hits_before[u] for u in read_before),
            "checkpointed_sources_served_from": sorted({modes.get(canon(u)) for u in read_before}),
            "report_live": report.get("live"),
            "resumed_event": "resumed" in [e["kind"] for e in state.events(db, task["id"])]}
        out["crash_resume"]["ok"] = (crashed and resumed["status"] in ("completed", "partial")
                                     and out["crash_resume"]["refetched_checkpointed_pages"] == 0
                                     and out["crash_resume"]["checkpointed_sources_served_from"] == ["cache"])
        return out

    def _hits(self, page_url):
        parts = urlsplit(page_url)
        return len(self.web.requests_to((parts.hostname or "").lower(), parts.path))

    # ---- memory ---------------------------------------------------------------------------------------------
    def memory(self):
        """Retrieval quality of knowledge.query. Runs the same questions in a separate individual whose owner
        configuration sets auto_provisional_min_confidence = 0 (the >= 2 independent sources and no-contradiction
        bars stay): with the default 0.75, two agreeing secondary pages (posterior 0.72) never qualify, so the
        default run leaves nothing to retrieve (see consolidation). Every provisional item is then accepted
        programmatically (SIMULATED_ACCEPTANCE) and the topic queries are scored against the gold predicates."""
        path = self.root / "memory.sqlite"
        self.memory_db = db = self._individual(path)
        state.set_config(db, MEMORY_CONFIG, C.T0)
        services = self._services(db, path)
        for q in C.QUESTIONS:
            if not self.quick or q["id"] in C.QUICK_QUESTIONS:
                self.run_task(f"memory:{q['id']}", q["question"], db=db, path=path, services=services)
        services.close()
        now = self.tick()
        accepted = []
        for item in knowledge.rows(db, "status='provisional'"):
            knowledge.review(db, item["id"], True, now, note=SIMULATED_ACCEPTANCE)
            accepted.append(item["id"])
        live = knowledge.rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
        rows = []
        for m in C.MEMORY:
            if self.quick and m["id"] not in C.QUICK_MEMORY:
                continue
            gold = {r["id"] for r in live if all(s in r["statement"].lower() for s in m["all"])
                    and not any(s in r["statement"].lower() for s in m.get("none", ()))}
            ranked = [i["id"] for i in knowledge.query(db, m["query"], now)["items"]]
            first = next((k + 1 for k, i in enumerate(ranked) if i in gold), None)
            hits3 = len(gold & set(ranked[:3]))
            rows.append({"id": m["id"], "query": m["query"], "gold_items": len(gold), "returned": len(ranked),
                         "first_relevant_rank": first,
                         "recall_at_3": (round(hits3 / min(3, len(gold)), 4) if gold else None),
                         "reciprocal_rank": (round(1.0 / first, 4) if first else 0.0) if gold else None})
        scored = [r for r in rows if r["gold_items"]]
        return {"label": SIMULATED_ACCEPTANCE, "config": MEMORY_CONFIG, "accepted_items": len(accepted),
                "live_items": len(live), "statements": sorted(r["statement"] for r in live),
                "queries": len(rows), "queries_with_gold_in_memory": len(scored),
                "queries_without_gold_in_memory": [r["id"] for r in rows if not r["gold_items"]],
                "recall_at_3": M._r(sum(r["recall_at_3"] for r in scored) / len(scored) if scored else None),
                "mrr": M._r(sum(r["reciprocal_rank"] for r in scored) / len(scored) if scored else None),
                "recall_definition": "|gold in top 3| / min(3, |gold|), averaged over queries whose gold set is "
                                     "non-empty", "items": rows}

    # ---- probes and extraction --------------------------------------------------------------------------------
    def probes(self):
        fetcher = SafeFetcher(NetPolicy(timeout=0.4, min_domain_interval=0.0), resolver=self.web.resolver,
                              connector=self.web.connector, clock=self.clock, sleep=lambda s: None)
        budget, usage = Budget(max_requests=500, max_bytes=100_000_000), Usage()
        rows = []
        for p in C.PROBES:
            parts = urlsplit(p["url"])
            before = len(self.web.requests_to((parts.hostname or "").lower(), parts.path))
            outcome, detail, extra = "ok", "", {}
            try:
                result = fetcher.fetch(p["url"], purpose=p["purpose"], budget=budget, usage=usage)
                if result.truncated:
                    outcome = "ok_truncated"
                    extra = self._bomb(result)
            except FetchError as exc:
                outcome, detail = exc.code, exc.detail
                if exc.retry_after:
                    extra["retry_after"] = round(exc.retry_after, 1)
            after = len(self.web.requests_to((parts.hostname or "").lower(), parts.path))
            rows.append({"id": p["id"], "group": p["group"], "url": p["url"], "purpose": p["purpose"],
                         "expected": p["expected"], "outcome": outcome, "detail": detail,
                         "contained": outcome in p["expected"], "requests_reaching_server": after - before, **extra})
        groups = {}
        for r in rows:
            g = groups.setdefault(r["group"], {"probes": 0, "contained": 0})
            g["probes"] += 1
            g["contained"] += r["contained"]
        backoff = next(r for r in rows if r["id"] == "rate_limited_backoff")
        content = self._content_probes(fetcher, budget, usage)
        return {"probes": rows, "groups": groups, "all_contained": all(r["contained"] for r in rows),
                "backoff_request_reached_server": backoff["requests_reaching_server"], "content": content}

    def _bomb(self, result):
        started = time.perf_counter()
        try:
            doc = extract.extract(result)
            out = {"extracted": True, "text_chars": len(doc.text), "error": None}
        except Exception as exc:  # measured, not hidden: an extractor crash on hostile markup is a finding
            out = {"extracted": False, "text_chars": 0, "error": type(exc).__name__}
        out["bytes_read"] = len(result.body)
        self.probe_seconds["html_bomb_extract"] = round(time.perf_counter() - started, 3)
        return out

    def _content_probes(self, fetcher, budget, usage):
        out = {}
        pdf = extract.extract(fetcher.fetch(C.U("S15"), budget=budget, usage=usage))
        out["pdf"] = {"kind": pdf.kind, "title": pdf.title, "has_expected_text": "96 cubic metres" in pdf.text,
                      "published_at": pdf.published_at}
        feed = fetcher.fetch(C.U("S16"), purpose="feed", budget=budget, usage=usage)
        entries = extract.parse_feed(feed.body, feed.url)
        out["rss"] = {"entries": len(entries), "dated": sum(1 for e in entries if e.get("published_at")),
                      "urls_match": [e["url"] for e in entries] == [C.U(i) for i in ("P23", "P05", "P03")]}
        broken = extract.extract(fetcher.fetch(C.U("P26"), budget=budget, usage=usage))
        out["broken_html"] = {"extracted": True, "has_expected_text": "designed by Ilse Marten" in broken.text}
        shell = extract.extract(fetcher.fetch(C.U("S14"), budget=budget, usage=usage))
        out["js_shell"] = {"js_suspected": bool(shell.flags.get("js_suspected")), "static_text_chars": len(shell.text),
                           "render": self._render(fetcher)}
        return out

    def _render(self, fetcher):
        if self.quick:
            return {"attempted": False, "reason": "skipped in --quick mode"}
        from ..browser import BrowserRenderer
        renderer = BrowserRenderer(fetcher, timeout=20.0)
        ok, why = renderer.available()
        if not ok:
            return {"attempted": False, "reason": why}
        budget, usage = Budget(max_browser_sessions=1, max_requests=20), Usage()
        started = time.perf_counter()
        try:
            doc = extract.extract(renderer.render(C.U("S14"), budget=budget, usage=usage))
            self.probe_seconds["js_render"] = round(time.perf_counter() - started, 3)
            return {"attempted": True, "ok": True, "has_rendered_text": C.JS_TEXT in M.collapse(doc.text),
                    "retrieval_mode": doc.retrieval_mode, "browser_sessions": usage.browser_sessions,
                    "availability": why}
        except FetchError as exc:
            return {"attempted": True, "ok": False, "error": exc.code, "detail": exc.detail[:200],
                    "availability": why}

    def extraction(self):
        pages = C.ARTICLES if not self.quick else C.ARTICLES[:8]
        engines = ("scrapling", "trafilatura", "auto")
        rows, seconds = [], {e: [] for e in engines}
        for page in pages:
            parts = urlsplit(page["url"])
            body = self.web.routes[(parts.hostname, parts.path)].body
            gold = "\n".join(page["paras"])
            row = {"id": page["id"], "layout": page["layout"]}
            for engine in engines:
                started = time.perf_counter()
                doc = extract.extract_html(body, page["url"], retrieved_at=C.T0, retrieval_mode="live", engine=engine)
                seconds[engine].append(time.perf_counter() - started)
                row[engine] = M.token_f1(doc.text, gold) | {"engine_used": doc.metadata.get("engine")}
            rows.append(row)
        agg = {}
        for engine in engines:
            agg[engine] = {k: round(sum(r[engine][k] for r in rows) / len(rows), 4) for k in ("precision", "recall", "f1")}
            agg[engine]["engine_used"] = dict(Counter(r[engine]["engine_used"] for r in rows))
        by_layout = {}
        for layout in sorted({r["layout"] for r in rows}):
            sub = [r for r in rows if r["layout"] == layout]
            by_layout[layout] = {"pages": len(sub), **{e: round(sum(r[e]["f1"] for r in sub) / len(sub), 4)
                                                       for e in engines}}
        wins = Counter("trafilatura" if r["trafilatura"]["f1"] > r["scrapling"]["f1"] else
                       "scrapling" if r["scrapling"]["f1"] > r["trafilatura"]["f1"] else "tie" for r in rows)
        return ({"pages": len(rows), "mean": agg, "by_layout": by_layout, "per_page_winner": dict(wins),
                 "items": rows, "gold": "paragraph text of the article body (title, chrome and comments excluded)"},
                {e: M.summary([s * 1000 for s in v]) for e, v in seconds.items()})

    # ---- whole run ------------------------------------------------------------------------------------------
    def execute(self) -> dict:
        started = time.perf_counter()
        recs = self.questions()
        repeat_id = "A05" if "A05" in recs else next(iter(recs))
        repeat = self.run_task(f"repeat:{repeat_id}", recs[repeat_id]["question"])
        main = list(recs.values())
        deterministic = {
            "authoritative": self.authoritative(recs),
            "time_sensitive": self.time_sensitive(recs),
            "date_extraction": self.date_accuracy(),
            "verification": self.verification(recs),
            "contradictions": self.contradictions(recs),
            "citations": self._citations(),
            "uat": self.uat(recs),
            "completion": dict(sorted(Counter(r["status"] for r in main).items())),
            "modes": dict(sorted(Counter(r["mode"] for r in main).items())),
            "answered_from_memory": [r["key"] for r in self.records
                                     if (r["report"].get("memory") or {}).get("answered_from_memory")],
            "repeat_check": {"question": repeat_id, "status": repeat["status"], "live": repeat["report"].get("live"),
                             "answered_from_memory": bool((repeat["report"].get("memory") or {})
                                                          .get("answered_from_memory")),
                             "source_modes": dict(Counter(s["retrieval_mode"] for s in
                                                          repeat["report"].get("sources") or []))},
            "consolidation": self.consolidation(main),
            "usage_per_task": {k: M.summary([r["usage"].get(k, 0) for r in main], digits=1)
                               for k in ("queries", "requests", "pages", "bytes")},
        }
        deterministic["checks"] = self.checks()
        deterministic["memory"] = self.memory()
        deterministic["injection"] = self.injection()
        deterministic["task_failures"] = self.task_failures()
        deterministic["liveness"] = self.liveness()
        deterministic["budgets"] = self.budgets()
        deterministic["probes"] = self.probes()
        extraction, extraction_ms = self.extraction()
        deterministic["extraction"] = extraction
        by_mode = {}
        for r in main:
            by_mode.setdefault(r["mode"], []).append(r["wall_seconds"])
        timing = {"label": "wall-clock seconds on this host, offline (loopback FixtureWeb); not comparable to live",
                  "per_mode_seconds": {m: M.summary(v) for m, v in sorted(by_mode.items())},
                  "all_tasks_seconds": M.summary([r["wall_seconds"] for r in main]),
                  "extraction_ms": extraction_ms, "probe_seconds": self.probe_seconds,
                  "run_seconds": round(time.perf_counter() - started, 3)}
        resources = {"per_task_db_growth_bytes": M.summary([r["db_growth"] for r in main], digits=0),
                     "per_task_cache_growth_bytes": M.summary([r["cache_growth"] for r in main], digits=0),
                     "db_bytes_final": _size(self.path), "cache_bytes_final": _size(svc_mod.cache_path(self.path)),
                     "peak_rss_bytes": _rss_bytes(),
                     "peak_rss_note": "process-wide ru_maxrss: includes the in-process fixture server and every "
                                      "earlier run in this process"}
        tasks = [{"key": r["key"], "mode": r["mode"], "status": r["status"], "error": r["error"],
                  "usage": {k: r["usage"].get(k) for k in RESOURCES}, "live": r["report"].get("live"),
                  "sources": len(r["report"].get("sources") or []),
                  "answer": [a["text"] for a in r["report"].get("answer") or []][:3],
                  "limitations": r["report"].get("limitations") or []} for r in self.records]
        deterministic["tasks"] = tasks
        return {"deterministic": deterministic, "timing": timing, "resources": resources}

    # ---- UAT RC1 defect classes ------------------------------------------------------------------------------
    def uat(self, recs):
        """Measurements of the defect classes the RC1 UAT found (fact-check discrimination, number checks, latest
        version, cross-subject merging, code literals, snippet-only records, date provenance, package routing)."""
        main = [r for r in self.records if r["key"] in recs]
        return {"fact_check_discrimination": self._discrimination(recs), "number_checks": self._number_checks(recs),
                "latest_version": self._latest(recs), "cross_subject": self._cross_subject(main),
                "code_literals": self._code_literals(main), "snippet_records": self._snippets(main),
                "dating": self._dating(), "package_routing": self._package_routing(recs)}

    @staticmethod
    def _label(rec):
        return (rec["report"].get("verdict") or {}).get("label") or "none"

    def _discrimination(self, recs):
        rows = []
        for true_id, false_id in C.FACT_PAIRS:
            if true_id not in recs or false_id not in recs:
                continue
            t, f = self._label(recs[true_id]), self._label(recs[false_id])
            rows.append({"true": true_id, "false": false_id, "true_label": t, "false_label": f, "differ": t != f,
                         "discriminated": t == "supported" and f in ("disputed", "contested")})
        return {"pairs": len(rows), "labels_differ": sum(r["differ"] for r in rows),
                "discriminated": sum(r["discriminated"] for r in rows), "rows": rows,
                "definition": "a true/false claim pair is discriminated when the true claim is 'supported' and the "
                              "false one 'disputed' or 'contested'"}

    def _number_checks(self, recs):
        rows = []
        for qid in C.NUMBER_CHECKS:
            if qid not in recs:
                continue
            q = next(x for x in C.QUESTIONS if x["id"] == qid)
            label = self._label(recs[qid])
            rows.append({"id": qid, "predicted": label, "acceptable": q["acceptable"],
                         "ok": label in q["acceptable"], "wrongly_supported": label == "supported"})
        return {"checks": len(rows), "ok": sum(r["ok"] for r in rows),
                "wrongly_supported": sum(r["wrongly_supported"] for r in rows), "rows": rows}

    @staticmethod
    def _top_item(report):
        items = [a for a in report.get("answer") or [] if a.get("status") != "summary"]
        return (items[0].get("text") or "") if items else ""

    def _latest(self, recs):
        rows = []
        for q in C.QUESTIONS:
            if q["category"] != "latest" or q["id"] not in recs:
                continue
            report = recs[q["id"]]["report"]
            top = self._top_item(report)
            texts = [a.get("text") or "" for a in report.get("answer") or []]
            rows.append({"id": q["id"], "status": recs[q["id"]]["status"], "top": top[:300],
                         "correct_top": q["gold_latest"] in top and not any(o in top for o in q["older"]),
                         "older_in_answer": sorted({o for o in q["older"] for t in texts if o in t}),
                         "gold_in_answer": any(q["gold_latest"] in t for t in texts)})
        return {"questions": len(rows), "correct_top": sum(r["correct_top"] for r in rows), "rows": rows,
                "definition": "the first non-summary answer item names the latest version and no older one"}

    def _page_of(self):
        return {canon(a["url"]): a["id"] for a in C.ARTICLES}

    def _cross_subject(self, records):
        page_of, rows = self._page_of(), []
        checked = {"answer": 0, "findings": 0}
        for rec in records:
            report = rec["report"]
            by_n = {s["n"]: canon(s["url"]) for s in report.get("sources") or []}
            for where, items, text_key in (("answer", report.get("answer") or [], "text"),
                                           ("findings", report.get("findings") or [], "statement")):
                for item in items:
                    if item.get("status") == "summary":      # the run summary cites what was read, not a claim
                        continue
                    pages = {page_of.get(by_n.get(n)) for n in item.get("citations") or []} - {None}
                    if len(pages) < 2:
                        continue
                    checked[where] += 1
                    subjects = sorted({C.SUBJECT[p] for p in pages})
                    if len(subjects) > 1:
                        rows.append({"task": rec["key"], "where": where, "text": str(item.get(text_key))[:200],
                                     "subjects": subjects})
        return {"answer_items_checked": checked["answer"],
                "answer_items_merged": sum(1 for r in rows if r["where"] == "answer"),
                "findings_checked": checked["findings"],
                "findings_merged": sum(1 for r in rows if r["where"] == "findings"), "rows": rows[:50],
                "definition": "answer items (the run summary excluded) and findings citing >= 2 corpus pages whose "
                              "gold subjects differ"}

    def _code_literals(self, records):
        contradictions, negated = [], []
        for rec in records:
            for c in rec["report"].get("contradictions") or []:
                a, b = c["a"].get("text") or "", c["b"].get("text") or ""
                if _CODE_LITERAL.search(a) or _CODE_LITERAL.search(b):
                    contradictions.append({"task": rec["key"], "reason": c.get("reason"), "a": a[:160],
                                           "b": b[:160]})
            for claim in state.claims(rec["db"], rec["task_id"]):
                text = claim["text"] or ""
                if (claim.get("polarity") or 0) < 0 and _CODE_LITERAL.search(text) and not _NEGATION_WORD.search(
                        text):
                    negated.append({"task": rec["key"], "text": text[:200]})
        return {"contradictions_on_code_literals": len(contradictions), "negated_code_literal_claims": len(negated),
                "rows": (contradictions + negated)[:30],
                "definition": "contradictions with a code literal (name=False/None/0) on either side, and claims "
                              "marked negative only because of such a literal (no negation word)"}

    def _snippets(self, records):
        pages = {canon(C.U(w["page"])) for w in C.WIKI_SNIPPETS}
        fragments = {M.collapse(w["snippet"]) for w in C.WIKI_SNIPPETS}
        read = cited = quoted = 0
        for rec in records:
            report = rec["report"]
            for src in report.get("sources") or []:
                if canon(src["url"]) in pages and src.get("retrieval_mode") == "api":
                    read += 1
                    cited += bool(src.get("cited"))
            quoted += sum(1 for a in report.get("answer") or [] if M.collapse(a.get("text") or "") in fragments)
        return {"snippet_records_read_as_documents": read, "snippet_records_cited": cited,
                "answer_items_quoting_a_snippet": quoted,
                "definition": "encyclopedia search snippets (metadata snippet_only) read or cited as documents "
                              "instead of the article"}

    def _dating(self):
        rows, seen = [], set()
        for rec in self.records:
            for src in rec["report"].get("sources") or []:
                page = self._page_of().get(canon(src["url"]))
                if page is None or page in seen or not C.BY_ID[page].get("last_modified"):
                    continue
                seen.add(page)
                article = C.BY_ID[page]
                header = C.T0 - article["last_modified"]
                got = src.get("published_at")
                gold = C.epoch(article["date"])
                rows.append({"page": page, "date_via": article["date_via"], "gold": gold, "extracted": got,
                             "correct": (gold is None and got is None) or (gold is not None and got is not None and
                                                                          abs(gold - got) <= C.DAY),
                             "dated_from_last_modified": got is not None and abs(got - header) <= C.DAY})
        return {"pages": len(rows), "correct": sum(r["correct"] for r in rows),
                "dated_from_last_modified": sum(r["dated_from_last_modified"] for r in rows), "rows": rows,
                "definition": "pages whose only date-like signal besides og:updated_time (epoch) is the HTTP "
                              "Last-Modified header: the publication date must come from the page, never the header"}

    def _package_routing(self, recs):
        rows = []
        for q in C.QUESTIONS:
            if not q.get("package") or q["id"] not in recs:
                continue
            rec = recs[q["id"]]
            queries = [e["detail"].get("text") for e in state.events(rec["db"], rec["task_id"])
                       if e["kind"] == "query" and isinstance(e.get("detail"), dict)]
            sources = rec["report"].get("sources") or []
            registry = [s for s in sources if "pypi.org/project/" in s["url"]]
            rows.append({"id": q["id"], "package": q["package"],
                         "pypi_queried": f"pypi:{q['package']}" in queries,
                         "npm_queried": f"npm:{q['package']}" in queries,
                         "registry_source_read": bool(registry),
                         "registry_source_cited": any(s.get("cited") for s in registry)})
        return {"questions": len(rows), "routed": sum(r["pypi_queried"] and r["registry_source_read"] for r in rows),
                "rows": rows,
                "definition": "a plain package name with a release cue is looked up in the (fixture) PyPI registry "
                              "and the registry record is read"}

    def consolidation(self, recs):
        """What the default owner configuration let into memory from the main run (no acceptance applied)."""
        counts, held = Counter(), Counter()
        for rec in recs:
            cons = rec["report"].get("consolidation") or {}
            for key in ("provisional", "strengthened", "superseded", "contested", "pending"):
                counts[key] += len(cons.get(key) or [])
            for h in cons.get("held") or []:
                for reason in h.get("reasons") or []:
                    held[re.sub(r"[\d.]+", "N", reason)] += 1
        return {"config": {"auto_provisional_min_sources": 2, "auto_provisional_min_confidence": 0.75},
                "counts": dict(sorted(counts.items())), "held_reasons": dict(held.most_common()),
                "knowledge_rows": len(knowledge.rows(self.db))}

    def _citations(self):
        return score_citations([(rec["key"], citation_rows(rec["report"], lambda src, rec=rec: self.source_text(
            rec, src))) for rec in self.records])

    def close(self):
        self.services.close()
        self.db.close()
        for db in (self.check_db, self.memory_db):
            if db is not None:
                db.close()


def _strings(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def run_once(*, quick: bool = False) -> dict:
    web = C.build_web().start()
    # a client that stops reading (truncation, timeout) makes the fixture handler hit a broken pipe; that is the
    # behaviour under test, so the server's traceback printer is silenced for this instance only
    web.server.handle_error = lambda request, client_address: None
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-webbench-") as directory:
            bench = _Bench(Path(directory), web, quick)
            try:
                return bench.execute()
            finally:
                bench.close()
    finally:
        web.stop()


def corpus_summary() -> dict:
    cats = Counter(q["category"] for q in C.QUESTIONS)
    return {"pages": len(C.ARTICLES) + len(C.SPECIAL), "article_pages": len(C.ARTICLES),
            "special_pages": len(C.SPECIAL), "hosts": len(C.hosts()), "questions": dict(sorted(cats.items())),
            "uat_classes": {"fact_pairs": len(C.FACT_PAIRS), "number_checks": len(C.NUMBER_CHECKS),
                            "latest_version_questions": cats.get("latest", 0),
                            "subjects": len(set(C.SUBJECT.values())), "wiki_snippets": len(C.WIKI_SNIPPETS),
                            "fixture_registries": ["pypi", "npm"]},
            "contradiction_pairs": {"conflicts": sum(p["conflict"] for p in C.PAIRS),
                                    "controls": sum(not p["conflict"] for p in C.PAIRS)},
            "injection_pages": len(C.INJECTIONS), "probes": len(C.PROBES), "memory_queries": len(C.MEMORY),
            "search_results": len(C.search_results()), "fictional": True, "language": "en"}


def diff(a, b, prefix="") -> list[str]:
    """Paths at which two JSON-like values differ (bounded)."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k), b.get(k), f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{prefix}[{i}]")
    elif a != b:
        out.append(prefix)
    return out[:50]
