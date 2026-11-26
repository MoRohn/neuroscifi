"""The source-independence family (Wave 4 step 0; scripts/bench_sources.py). Worlds are frozen in
sources_splits_dev.py and sources_splits_heldout.py (committed alone, before this harness); the withheld worlds
(sources_withheld.py) are loaded and scored only by final(), one recorded look per run.

One run: a real Web Cortex individual (a temporary file) investigates, cycle by cycle, the looks the world schedules
(the same looks in the same order in every configuration: the timer is not under test here). Each look is one owner
research task read through the real modules, exactly as the learning-cycles harness reads a task
(cycles.investigate): sources saved, claims.extract_claims, claims.stamp_origins (with the individual's own
attribution and grouping settings), claims.cluster / claims.score (whose evidence count is claims.
independence_groups), consolidate.run, the "still holds" predictions, the task completed with its model-written answer
(the individual's own answer where the world plants one that cycle, else the first cluster's statement), the after-run
hooks. The individual's own answers are also recorded in its imagined store, as cycles.Run.plant_self does.
Substituted: search and fetching (the world hands each task its pages as documents, as the cycles family does), the
host's resource gate (held open) and any model (a stub; nothing calls one at these settings). The run itself draws no
random number: the world's keyed streams are its only randomness, so every configuration meets the same pages.

THE METRICS, fixed before any rule, per world, seed and SHAPE (the shape the look staged), against the derivation
graph (never against anything the individual produced; no metric reads a version-number convention: values are
compared as numbers, "2.4" = "v2.4.0" = "2.4.0"):
  support(v)          in one look: the first-hand roots (sources_splits_dev: "<outlet>|<value>") behind the READ pages
                      stating v (a derived page carries its origins' roots, read or not), echoes excluded;
                      meets_bar(v): at least BAR roots, or the project's own release page among them, read or not
                      (the authoritative root: the product lets a direct official record suffice when read)
  wrongly_admitted    knowledge rows written by a look (new rows: provisional, or the new side of a supersession) whose
                      value does not meet the bar in that look; `wrongly_admitted_false` those whose value is also not
                      true now. Beside them: `admitted`, `admitted_false` (not true now, any support)
  own_admitted        rows written whose value no first-hand root states in the look and an echo page states (the
                      individual's own earlier answer became knowledge)
  suppressed          looks in which the true value meets the bar and, after the look, the subject's newest held item
                      does not state it; `suppressed_contested` of them, the look opened a contradiction question (the
                      standing rule: contradictions are asked); `eligible` the looks whose true value meets the bar
  dependent_rises     held items whose confidence rose in a look in which no root stated their value that was not
                      already behind them (their sources before the look): a rise from dependent pages only
  grouping            pairs of distinct read pages that yielded a claim, in one look: the pipeline says "same source"
                      when some claim of each is in one group of claims.independence_groups over a cluster (or the
                      pages share a canonical URL, which the product stores as one source); the truth says "same
                      source" when their roots intersect (echoes share the root "self"). tp / fp / fn / tn; precision
                      = tp / (tp + fp), recall = tp / (tp + fn), pooled over looks
  correct             per cycle, the share of the subjects so far whose newest held item states the value true now;
                      `mean_correct` over cycles 1..N, `final_correct` at cycle N
  cost                wall seconds, seconds inside consolidate.run, investigations, pages read, pages that yielded a
                      claim, knowledge rows and file bytes at the end
REFERENCES (from the derivation graph alone, with the same looks; one reference uses one product function, named):
  oracle        counts the true roots of each value (support) and admits by meets_bar
  every_page    every read page is its own source (echoes included)
  same_value    every page stating the same value is one source
  todays_rule   the product's grouping as shipped (claims.extract_claims + stamp_origins + independence_groups over the
                look's claims at the default settings), and nothing else of the product
  each admits, in a look, the value whose count is at least BAR (or that a read release page states) and strictly
  above every other value's, and holds it (replacing what it held); it is scored by the same metrics. The references
  bound what a source rule can gain in each direction; they do not model the product's other rules (relevance,
  recency, contradiction questions), so `correct` and `suppressed` of a reference are not the product's ceiling.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from ...instance import store
from . import cycles, scoring
from . import sources_splits_dev as dev
from . import sources_splits_heldout as heldout

T0 = cycles.T0
DAY = cycles.DAY
BAR = 2
MECHANISM = "sources-bench"
# Configurations: only the settings under test are forced (on top of every setting as a new individual has it, plus
# `enabled`; the timer is never run, so `autonomous` is left false).
CONFIGS = {"shipped": {},
           "attribution_independence": {"attribution_independence": True},
           "echo_recognition": {"echo_recognition": True},
           "origin_grouping:one_way": {"origin_grouping": "one_way"},
           # Wave 4 lane S (ADR-066): the derivation rule, and its ablations (claims.DERIVATION_ABLATE, set by run_world
           # for the run only; "_ablate" is not a setting)
           "source_derivation": {"source_derivation": True},
           "source_derivation:no-names": {"source_derivation": True, "_ablate": ["names"]},
           "source_derivation:no-links": {"source_derivation": True, "_ablate": ["links"]},
           "source_derivation:no-time": {"source_derivation": True, "_ablate": ["time"]},
           "source_derivation:no-values": {"source_derivation": True, "_ablate": ["values"]},
           "source_derivation:no-whole-group": {"source_derivation": True, "_ablate": ["whole_group"]}}
CONFIGS["own_text_provenance"] = {"own_text_provenance": True}                      # Wave 4 lane E (ADR-067)
FINAL_CONFIGS = ("shipped",)
READ_BACK = ("enabled", "autonomous", "attribution_independence", "echo_recognition", "origin_grouping",
             "supersession", "supersession_min_sources", "auto_provisional_min_sources",
             "auto_provisional_min_confidence", "source_derivation")
READ_BACK += ("own_text_provenance",)
REFERENCES = ("oracle", "every_page", "same_value", "todays_rule")
COUNTS = ("looks", "pages", "pages_with_claims", "admitted", "admitted_false", "wrongly_admitted",
          "wrongly_admitted_false", "own_admitted", "eligible", "suppressed", "suppressed_contested",
          "dependent_rises", "tp", "fp", "fn", "tn")
# Wave 4 lane E (added before its rule was run): read pages a rule set aside as echoes of the individual's own output
# (consolidation's summary["echoes"]), by what the derivation graph says they are: an echo, or an honest page (first-hand
# or derived from an outlet) wrongly discarded
COUNTS += ("discarded_echo_pages", "discarded_honest_pages")
SPLITS = ("development", "held_out")
WITHHELD = "withheld"

WITHHELD_FILE = Path(__file__).resolve().parent / "sources_withheld.py"
DEV_FILE = Path(__file__).resolve().parent / "sources_splits_dev.py"
# Pinned when committed (5ce7868 and cda7271); final() compares the bytes before its ledger line (nothing is loaded).
WITHHELD_SHA256 = "9a4d21094cb30135d33974fb9d8025001c04d8d27b7d01892145da421d9a8aa6"
DEV_SHA256 = "0de0568a68fae30080c9c1588f1696a2265db668b5796530ebc06bc0ef412c15"
HELDOUT_SHA256 = "a74f729e005cdeb2ead2d215721a7a16c0845683d0cc552a152027ea36a3c8d2"


def norm(value) -> str | None:
    """A version as "M.m.p" ("v2.4" -> "2.4.0"); None when it is not a version."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value).strip())
    if not m or m.group(2) is None:
        return None
    return "{}.{}.{}".format(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _key(page) -> str:
    """The URL the product stores a page under (its canonical URL when it names one)."""
    return page.get("canonical") or f"http://{page['host']}{page['path']}"


def _document(page, now, spec, name):
    from ...webcortex.types import Document
    url = f"http://{page['host']}{page['path']}"
    published = min(T0 + page["published_cycle"] * spec["cycle_hours"] * 3600.0 - 3600.0, now - 600.0)
    return Document(url=url, final_url=url, canonical_url=_key(page), domain=page["host"], title=name, text=page["text"], retrieved_at=now, retrieval_mode="live", published_at=published)


def support(look) -> tuple[dict, set]:
    """({value: set of first-hand roots behind the read pages stating it}, the look's authoritative roots: those of
    the project's own release page, read or not)."""
    out = {}
    for p in look["pages"]:
        out.setdefault(p["value"], set()).update(r for r in p["roots"] if r != "self")
    auth = {r for p in look["pages"] + look["unread"] if p.get("authoritative") for r in p["roots"]}
    return out, auth


def meets_bar(value, sup, auth) -> bool:
    roots = sup.get(value, set())
    return len(roots) >= BAR or bool(roots & auth)


def _truth_same(a, b) -> bool:
    return bool(set(a["roots"]) & set(b["roots"]))


def _pairs(pages, same) -> dict:
    """tp / fp / fn / tn over pairs of distinct pages; `same(a, b)` is the grouping's answer."""
    out = dict.fromkeys(("tp", "fp", "fn", "tn"), 0)
    for i, a in enumerate(pages):
        for b in pages[i + 1:]:
            t, s = _truth_same(a, b), same(a, b)
            out["tp" if t and s else "fp" if s else "fn" if t else "tn"] += 1
    return out


# ---- one run -----------------------------------------------------------------------------------------------------------

def investigate(db, task, docs, now, *, answer=None) -> tuple[dict, list, dict]:
    """cycles.investigate, keeping what it reads: (consolidation, scored clusters, origins). Same calls, same order."""
    from ...webcortex import autonomy, claims, consolidate, orchestrator, state
    tid = task["id"]
    freshness = consolidate._freshness(db, state.task(db, tid))
    found, source_ids = [], {}
    with store.write(db):
        for d in docs:
            source_ids[d.canonical_url] = state.save_source(db, tid, d, now)
    for d in docs:
        found += claims.extract_claims(d, task["objective"])
    cfg = state.get_config(db)
    origins = claims.stamp_origins(found, docs, attribution=cfg.attribution_independence, grouping=cfg.origin_grouping,
                                   derivation=cfg.source_derivation)
    clusters = [claims.score(c, now=now, freshness_class=freshness, origins=origins)
                for c in claims.cluster(found)] if found else []
    with store.write(db):
        state.save_clusters(db, tid, clusters, now, source_ids=source_ids)
    started = time.perf_counter()
    out = consolidate.run(db, tid, now)
    out["_seconds"] = time.perf_counter() - started
    orchestrator._predict(db, out["provisional"], now)
    text = answer or (clusters[0].statement if clusters else "No source answered the question.")
    report = {"consolidation": {k: out[k] for k in ("provisional", "strengthened", "unchanged", "superseded",
                                                    "contested", "held", "pending")},
              "reasoning": {"text": text, "generated_by": "model:stub"}, "answer": []}
    usage = {"pages": len(docs), "requests": len(docs), "bytes": sum(len(d.text) for d in docs), "queries": 1}
    checkpoint = state.task(db, tid).get("checkpoint") or {}
    checkpoint["predictions_settled"] = list(out.get("predictions_settled") or [])[:50]
    emission = orchestrator._emission(db, tid, report, "completed", now)     # ADR-067, as the orchestrator stores
    with store.write(db):                                                    # an answer (nothing with it off)
        orchestrator._record_emission(db, tid, emission, now)
        db.execute("UPDATE web_tasks SET status='completed', finished_at=?, updated_at=?, result=?, usage=?, "
                   "checkpoint=? WHERE id=?", (now, now, state.dumps(report), state.dumps(usage),
                                               state.dumps(checkpoint), tid))
        state.record_mode_outcome(db, task["mode"], "completed", usage, now)
        autonomy.settle(db, tid, usage, checkpoint, now)
        store.bump_state_version(db)
    orchestrator._after_run(db, tid, now)
    return out, clusters, origins


class _Quiet:
    """What cycles._environment's stub model asks of a run (nothing is said: no model text is produced here)."""

    def self_subject(self):
        return None


class Run:
    def __init__(self, world, config, db):
        self.world, self.spec, self.config_name, self.db = world, world["spec"], config, db
        self.names = {s["name"]: s for s in world["subjects"]}
        self.pattern = re.compile(r"\b(" + "|".join(sorted(self.names, key=len, reverse=True)) + r")\b")
        self.pages = {_key(p): p for look in world["looks"].values() for p in look["pages"]}
        self.by_shape = {}
        self.series = []
        self.cost = {"investigations": 0, "pages": 0, "pages_with_claims": 0, "consolidate_seconds": 0.0}
        self.config = {}
        self.prints = []                  # per look: what the pipeline grouped and admitted (looks_changed)

    def _count(self, shape, **kw):
        row = self.by_shape.setdefault(shape, dict.fromkeys(COUNTS, 0))
        for k, v in kw.items():
            row[k] += v

    def held(self, name=None) -> dict:
        """{subject: [(id, value, confidence, version, sources)]} of live knowledge rows naming a world subject."""
        from ...webcortex import state
        out = {}
        for kid, statement, values, confidence, version, sources in self.db.execute(
                f"SELECT id, statement, claim_values, confidence, version, sources FROM web_knowledge WHERE status IN "
                f"({','.join('?' * len(state.LIVE_KNOWLEDGE))}) ORDER BY id", state.LIVE_KNOWLEDGE):
            m = self.pattern.search(statement or "")
            if m is None or (name is not None and m.group(1) != name):
                continue
            value = next((v for v in map(norm, json.loads(values or "[]")) if v), None)
            out.setdefault(m.group(1), []).append((kid, value, float(confidence or 0.0), version,
                                                   [s.get("url") for s in json.loads(sources or "[]")
                                                    if isinstance(s, dict)]))
        return out

    @staticmethod
    def newest(rows):
        return max(rows, key=lambda r: (r[3], r[0])) if rows else None

    def _row_value(self, kid):
        row = self.db.execute("SELECT claim_values FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        return next((v for v in map(norm, json.loads(row[0] or "[]")) if v), None) if row else None

    def start(self):
        from ...webcortex import state
        settings = {k: v for k, v in CONFIGS[self.config_name].items() if not k.startswith("_")}
        state.set_config(self.db, {"enabled": True} | settings, T0 - 30 * DAY)
        cfg = state.get_config(self.db)
        self.config = {k: getattr(cfg, k) for k in READ_BACK}
        from ...webcortex import claims
        self.config["_ablate"] = sorted(claims.DERIVATION_ABLATE)      # read back: what run_world set for the run

    def look(self, name, c, now, answer):
        from ...webcortex import claims, orchestrator, state
        look = self.world["looks"][(name, c)]
        shape, pages, truth = look["shape"], look["pages"], self.names[name]["truth"][c]
        task = orchestrator.submit(self.db, f"What is the latest {name} release?", now, mode="fact_verification",
                                   origin="console")
        docs = [_document(p, now, self.spec, name) for p in pages]
        before = {r[0]: r for r in self.held(name).get(name, [])}
        out, clusters, origins = investigate(self.db, state.task(self.db, task["id"]), docs, now, answer=answer)
        after = self.held(name).get(name, [])
        sup, auth = support(look)
        # admitted content
        new_ids = list(out.get("provisional") or [])
        admitted = [self._row_value(k) for k in new_ids]
        echo_values = {p["value"] for p in pages if p["basis"] == "echo"}
        wrong = [v for v in admitted if not meets_bar(v, sup, auth)]
        self._count(shape, looks=1, pages=len(pages), admitted=len(admitted),
                    admitted_false=sum(1 for v in admitted if v != truth), wrongly_admitted=len(wrong),
                    wrongly_admitted_false=sum(1 for v in wrong if v != truth),
                    own_admitted=sum(1 for v in admitted if v in echo_values and not sup.get(v)))
        # suppression
        if meets_bar(truth, sup, auth):
            top = self.newest(after)
            missed = top is None or top[1] != truth
            self._count(shape, eligible=1, suppressed=int(missed),
                        suppressed_contested=int(missed and bool(out.get("contested"))))
        # confidence raised by dependent pages
        for kid, value, conf, _version, _sources in after:
            prev = before.get(kid)
            if prev is None or conf <= prev[2] + 1e-9:
                continue
            behind = {r for url in prev[4] for r in (self.pages.get(url) or {}).get("roots", ()) if r != "self"}
            if not (sup.get(value, set()) - behind):
                self._count(shape, dependent_rises=1)
        # grouping, over pages that yielded a claim
        page_of = {_key(p): p for p in pages}
        with_claims = {c.source_url for cl in clusters for c in cl.claims}
        read = [p for p in pages if _key(p) in with_claims]
        together = set()
        for cl in clusters:
            members = [x for x in cl.claims if not claims.is_adversarial(x)]
            for group in claims.independence_groups(members, origins=origins):
                urls = sorted({x.source_url for x in members if x.id in group})
                together |= {(a, b) for a in urls for b in urls if a != b}
        pairs = _pairs(read, lambda a, b: _key(a) == _key(b) or (_key(a), _key(b)) in together)
        self.prints.append(json.dumps([sorted(together), sorted(map(str, admitted)), sorted(out.get("contested") and
                                       [str(x.get("key")) for x in out["contested"]] or [])]))
        self._count(shape, pages_with_claims=len(read), **pairs)
        gone = set(out.get("echoes") or [])
        discarded = [page_of[u] for u in sorted({c.source_url for cl in clusters for c in cl.claims if c.id in gone})
                     if u in page_of]
        self._count(shape, discarded_echo_pages=sum(1 for p in discarded if p["basis"] == "echo"),
                    discarded_honest_pages=sum(1 for p in discarded if p["basis"] != "echo"))
        self.cost["investigations"] += 1
        self.cost["pages"] += len(pages)
        self.cost["pages_with_claims"] += len(read)
        self.cost["consolidate_seconds"] += out["_seconds"]
        assert set(page_of) >= with_claims - {None}, "a claim from a page the look did not hand over"

    def step(self, c):
        from ...instance import imagined
        now = T0 + c * self.spec["cycle_hours"] * 3600.0
        own = {item["subject"]: item for item in self.world["own"].get(c) or []}
        for item in own.values():
            name = item["subject"]
            imagined.record(self.db, "inference", payload={"text": item["text"]},
                            assumptions=["the individual's own earlier output"],
                            uncertainty="asserted by the individual's own output; no source states it",
                            mechanism=MECHANISM, mechanism_version="1",
                            question=f"What is the latest {name} release?", now=now)
        for name in self.world["schedule"][c]:
            self.look(name, c, now, own[name]["text"] if name in own else None)
        held = self.held()
        born = [n for n, s in self.names.items() if s["born"] <= c]
        right = sum(1 for n in born if (self.newest(held.get(n) or []) or (0, None))[1] == self.names[n]["truth"][c])
        self.series.append(right / len(born) if born else None)

    def summary(self) -> dict:
        tables = {k: self.db.execute(f"SELECT count(*) FROM {k}").fetchone()[0] for k in ("web_knowledge", "web_claims",
                                                                                          "pending", "web_tasks")}
        size = self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute("PRAGMA page_size").fetchone()[0]
        return {"by_shape": self.by_shape, "totals": _total(self.by_shape), "series_correct": self.series,
                "mean_correct": sum(self.series[1:]) / len(self.series[1:]), "final_correct": self.series[-1],
                "cost": self.cost | {"rows_end": tables, "file_bytes_end": size}, "config": dict(self.config),
                "prints": self.prints}


def _total(by_shape) -> dict:
    out = dict.fromkeys(COUNTS, 0)
    for row in by_shape.values():
        for k in COUNTS:
            out[k] += row[k]
    return out


def run_world(world, config) -> dict:
    """One run of one generated world with one configuration (CONFIGS)."""
    from unittest import mock
    from ...webcortex import claims
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-src-") as directory:
        db = store.create(Path(directory) / "s.sqlite", T0 - 31 * DAY, note="wave 4 step 0: source independence")
        with closing(db), cycles._environment([_Quiet()]), mock.patch.object(
                claims, "DERIVATION_ABLATE", frozenset(CONFIGS[config].get("_ablate") or ())):
            run = Run(world, config, db)
            run.start()
            for c in range(world["spec"]["cycles"] + 1):
                run.step(c)
            out = run.summary()
    out["cost"]["wall_seconds"] = time.perf_counter() - started
    return out


# ---- references (the derivation graph; todays_rule adds the product's grouping, nothing else) ---------------------------

def _todays_groups(pages, now, spec, name) -> dict:
    """{page key: group number} under claims.independence_groups at the default settings; a page without a claim is
    its own group."""
    from ...webcortex import claims
    docs = [_document(p, now, spec, name) for p in pages]
    found = []
    for d in docs:
        found += claims.extract_claims(d, f"What is the latest {name} release?")
    origins = claims.stamp_origins(found, docs)
    members = [x for x in found if not claims.is_adversarial(x)]
    group_of = {}
    for n, group in enumerate(claims.independence_groups(members, origins=origins)):
        for x in members:
            if x.id in group:
                group_of.setdefault(x.source_url, n)
    out, extra = {}, 10_000
    for p in pages:
        if _key(p) not in group_of:
            extra += 1
        out[_key(p)] = group_of.get(_key(p), extra)
    return out


def references(world) -> dict:
    """Each reference's metrics on one generated world (see the module docstring)."""
    spec, names = world["spec"], {s["name"]: s for s in world["subjects"]}
    out = {}
    for ref in REFERENCES:
        held, by_shape, series = {}, {}, []
        for c in range(spec["cycles"] + 1):
            now = T0 + c * spec["cycle_hours"] * 3600.0
            for name in world["schedule"][c]:
                look = world["looks"][(name, c)]
                pages, truth = look["pages"], names[name]["truth"][c]
                sup, auth = support(look)
                if ref == "todays_rule":
                    groups = _todays_groups(pages, now, spec, name)
                    same = (lambda gr: lambda a, b: gr[_key(a)] == gr[_key(b)])(groups)
                elif ref == "oracle":
                    same = _truth_same
                elif ref == "every_page":
                    same = (lambda a, b: False)
                else:
                    same = (lambda a, b: a["value"] == b["value"])
                counts = {}
                for v in {p["value"] for p in pages}:
                    stating = [p for p in pages if p["value"] == v]
                    if ref == "oracle":
                        counts[v] = len(sup.get(v, set()))
                    else:            # the number of classes of `same` among the pages stating v (transitive closure)
                        classes = []
                        for p in stating:
                            joined = [k for k in classes if any(same(p, q) for q in k)]
                            merged = [p] + [q for k in joined for q in k]
                            classes = [k for k in classes if k not in joined] + [merged]
                        counts[v] = len(classes)
                ok = {v for v, n in counts.items()        # the oracle admits by the bar on the truth itself
                      if (meets_bar(v, sup, auth) if ref == "oracle" else
                          n >= BAR or any(p.get("authoritative") and p["value"] == v for p in pages))}
                best = max((counts[v] for v in ok), default=None)
                chosen = [v for v in ok if counts[v] == best]
                row = by_shape.setdefault(look["shape"], dict.fromkeys(COUNTS, 0))
                row["looks"] += 1
                row["pages"] += len(pages)
                if len(chosen) == 1:
                    v = chosen[0]
                    row["admitted"] += 1
                    row["admitted_false"] += v != truth
                    row["wrongly_admitted"] += not meets_bar(v, sup, auth)
                    row["wrongly_admitted_false"] += not meets_bar(v, sup, auth) and v != truth
                    row["own_admitted"] += not sup.get(v) and any(p["basis"] == "echo" and p["value"] == v
                                                                  for p in pages)
                    held[name] = v
                if meets_bar(truth, sup, auth):
                    row["eligible"] += 1
                    row["suppressed"] += held.get(name) != truth
                for k, n in _pairs(pages, same).items():
                    row[k] += n
            born = [n for n, s in names.items() if s["born"] <= c]
            series.append(sum(1 for n in born if held.get(n) == names[n]["truth"][c]) / len(born))
        out[ref] = {"by_shape": by_shape, "totals": _total(by_shape), "mean_correct": sum(series[1:]) / len(series[1:]),
                    "final_correct": series[-1]}
    return out


# ---- splits ------------------------------------------------------------------------------------------------------------

def _public(split):
    if split == WITHHELD:
        from . import Refused
        raise Refused("the withheld source-independence worlds are scored only by sources.final() (scripts/"
                      "bench_sources.py final), which records the run in the ledger first. Do not look at them "
                      "while developing.")
    if split == "development":
        return dev.CYCLES["development"], dev.generate
    if split == "held_out":
        return heldout.CYCLES["held_out"], heldout.generate
    raise ValueError(f"unknown split {split!r}; known: {', '.join(SPLITS)}")


def world_digest(world) -> str:
    body = {"subjects": world["subjects"], "schedule": {str(c): v for c, v in world["schedule"].items()},
            "looks": sorted([n, c, look] for (n, c), look in world["looks"].items()),
            "own": {str(c): v for c, v in world["own"].items()}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _rate(row) -> dict:
    """precision and recall of "same source" from tp / fp / fn (None where undefined)."""
    tp, fp, fn = row["tp"], row["fp"], row["fn"]
    return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}


def _summarise(rows, seeds) -> dict:
    """Per world: the per-seed rows (totals, by shape, correctness, cost) and the sums over seeds by shape."""
    shapes = sorted({s for r in rows for s in r["by_shape"]})
    summed = {s: {k: sum(r["by_shape"].get(s, {}).get(k, 0) for r in rows) for k in COUNTS} for s in shapes}
    totals = {k: sum(r["totals"][k] for r in rows) for k in COUNTS}
    out = {"seeds": list(seeds),
           "per_seed": [{"seed": s, "totals": r["totals"] | _rate(r["totals"]), "by_shape": {k: {c: n for c, n in v.items() if n} for k, v in r["by_shape"].items()},
                         "mean_correct": r["mean_correct"], "final_correct": r["final_correct"]}
                        | ({"cost": r["cost"], "config": r["config"]} if "cost" in r else {})
                        for s, r in zip(seeds, rows)],
           "by_shape_sum": {s: summed[s] | _rate(summed[s]) for s in shapes},
           "totals_sum": totals | _rate(totals),
           "mean_correct": scoring.spread([r["mean_correct"] for r in rows]),
           "final_correct": scoring.spread([r["final_correct"] for r in rows])}
    if all("config" in r for r in rows):
        out["config_read_back"] = rows[0]["config"]
        out["config_same_every_seed"] = all(r["config"] == rows[0]["config"] for r in rows)
        out["cost_mean"] = {k: scoring.spread([r["cost"][k] for r in rows])["mean"]
                            for k in ("wall_seconds", "consolidate_seconds", "investigations", "pages",
                                      "pages_with_claims", "file_bytes_end")}
    return out


def run_split(split, *, configs=None, seeds=None) -> dict:
    spec, generate = _public(split)
    return _run_split(split, spec, generate, configs=configs, seeds=seeds)


def _run_split(split, spec, generate, *, configs=None, seeds=None) -> dict:
    configs = list(configs or CONFIGS)
    unknown = sorted(set(configs) - set(CONFIGS))
    if unknown:
        raise ValueError(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(CONFIGS)}")
    seeds = tuple(seeds or spec["seeds"])
    worlds = {w["name"]: [generate(w, s) for s in seeds] for w in spec["worlds"]}
    out = {"split": split, "seeds": list(seeds), "bar": BAR, "configurations": {}, "references": {},
           "world_digests": {n: [world_digest(g) for g in gs] for n, gs in worlds.items()}}
    raw = {(name, n): [run_world(g, name) for g in gs] for name in configs for n, gs in worlds.items()}
    for name in configs:
        out["configurations"][name] = {"settings": CONFIGS[name], "worlds": {}}
        for n in worlds:
            entry = _summarise(raw[(name, n)], seeds)
            base = raw.get(("shipped", n))
            # what the setting DID: looks whose grouping, admitted values or questions differ from `shipped`'s
            entry["looks_changed_vs_shipped"] = [sum(a != b for a, b in zip(r["prints"], s["prints"]))
                                                 for r, s in zip(raw[(name, n)], base)] if base else None
            out["configurations"][name]["worlds"][n] = entry
    for n, gs in worlds.items():
        rows = [references(g) for g in gs]
        out["references"][n] = {ref: _summarise([r[ref] for r in rows], seeds) for ref in REFERENCES}
    return out


# ---- Wave 4 lane E's verdict rule (ADR-067), written and committed BEFORE its first held-out run --------------------------
# For `own_text_provenance` against `shipped` on one split's result (run_split), per world summed over its seeds (the
# fields named are those of totals_sum and mean_correct):
#   A1 own answers: own_admitted summed over the worlds is lower than shipped's, and no world's is higher;
#   A2 nothing else admitted: no world's wrongly_admitted is higher than shipped's;
#   S1 suppression: no world's suppressed exceeds shipped's by more than half its number of seeds (2 on 4 seeds);
#   C1 correctness: no world's mean_correct mean is lower than shipped's by more than shipped's sd in that world;
#   G  grouping untouched: tp, fp and fn equal shipped's in every world (the rule groups nothing).
# "effective" when all hold; "trade" when A1 holds and S1 or C1 fails (reported with its numbers, never as a gain);
# "no effect" when A1 fails and S1, C1 hold; "unsafe" when A2 or G fails, or A1 fails with S1 or C1. Whatever the
# verdict, the setting ships OFF: turning it on is the owner's decision. discarded_honest_pages (the false rejections
# this rule causes) is reported beside it per world.
def own_verdict(result, config="own_text_provenance", base="shipped") -> dict:
    worlds = result["configurations"][config]["worlds"]
    ref = result["configurations"][base]["worlds"]
    seeds = len(result["seeds"])
    per = {}
    for w, e in worlds.items():
        t, b = e["totals_sum"], ref[w]["totals_sum"]
        per[w] = {"own_admitted": (b["own_admitted"], t["own_admitted"]),
                  "wrongly_admitted": (b["wrongly_admitted"], t["wrongly_admitted"]),
                  "suppressed": (b["suppressed"], t["suppressed"]),
                  "discarded_honest_pages": t["discarded_honest_pages"],
                  "discarded_echo_pages": t["discarded_echo_pages"],
                  "mean_correct": (ref[w]["mean_correct"]["mean"], e["mean_correct"]["mean"],
                                   ref[w]["mean_correct"]["sd"]),
                  "grouping_same": all(t[k] == b[k] for k in ("tp", "fp", "fn"))}
    a1 = sum(v["own_admitted"][1] for v in per.values()) < sum(v["own_admitted"][0] for v in per.values()) and \
        all(v["own_admitted"][1] <= v["own_admitted"][0] for v in per.values())
    a2 = all(v["wrongly_admitted"][1] <= v["wrongly_admitted"][0] for v in per.values())
    s1 = all(v["suppressed"][1] - v["suppressed"][0] <= seeds / 2 for v in per.values())
    c1 = all(v["mean_correct"][0] - v["mean_correct"][1] <= v["mean_correct"][2] for v in per.values())
    g = all(v["grouping_same"] for v in per.values())
    if not (a2 and g) or (not a1 and not (s1 and c1)):
        verdict = "unsafe"
    elif a1 and s1 and c1:
        verdict = "effective"
    elif a1:
        verdict = "trade"
    else:
        verdict = "no effect"
    return {"config": config, "reference": base, "verdict": verdict,
            "checks": {"A1": a1, "A2": a2, "S1": s1, "C1": c1, "G": g}, "per_world": per}


# ---- Wave 4 lane S (ADR-066): the verdict rule for source_derivation, written and committed BEFORE any held-out run ----
#
# Input: an output of `scripts/bench_sources.py run --split held_out` holding `shipped` and `source_derivation` (and its
# ablations). All counts are summed over the four seeds of a world (`configurations[c].worlds[w].totals_sum`); `correct`
# is `per_seed[i].mean_correct`, paired with `shipped` on the same world and seed.
#   (A) admissions: wrongly_admitted_false summed over the worlds is LOWER than shipped's and in no world higher, and
#       own_admitted is in no world higher;
#   (B) suppression: suppressed summed over the worlds is NOT higher than shipped's;
#   (C) correctness: in no world is the mean over seeds of the paired difference of mean_correct below minus shipped's
#       sd of mean_correct over the seeds of that world;
#   (D) grouping: pooled precision not below shipped's minus 0.02, and pooled recall above shipped's.
# Verdict: "supported" when A, B, C and D hold; "trade" when A holds and B or C fails (fewer false values admitted,
# paid for with true values not held or a lower `correct`: reported as a trade, never as a gain); "no gain" when A
# fails and B and C hold; "harmful" when A fails and B or C fails. Whatever it says, the setting ships OFF (a change of
# what counts as a source is the owner's to turn on). Reported beside it, per shape: the change of wrongly_admitted,
# wrongly_admitted_false, own_admitted and suppressed, and precision and recall; the same on development, as rates per
# look, for the development -> held-out drop. An ablation (a configuration "source_derivation:no-<x>") is "not
# exercised by these worlds" when every count of its totals_sum equals the full rule's in every world.
DERIVATION_VERDICT_FIELDS = ("wrongly_admitted", "wrongly_admitted_false", "own_admitted", "suppressed", "eligible",
                             "tp", "fp", "fn", "looks")


def verdict_derivation(out, rule="source_derivation", base="shipped") -> dict:
    """The pre-registered verdict for `rule` against `base` on one bench output (see the comment above)."""
    worlds = sorted(out["configurations"][base]["worlds"])
    get = (lambda c, w: out["configurations"][c]["worlds"][w])

    def total(c, k, w=None):
        return sum(get(c, x)["totals_sum"][k] for x in (worlds if w is None else [w]))

    def pooled(c):
        tp, fp, fn = total(c, "tp"), total(c, "fp"), total(c, "fn")
        return (tp / (tp + fp) if tp + fp else None), (tp / (tp + fn) if tp + fn else None)
    paired = {}
    for w in worlds:
        diffs = [a["mean_correct"] - b["mean_correct"]
                 for a, b in zip(get(rule, w)["per_seed"], get(base, w)["per_seed"])]
        paired[w] = {"mean": sum(diffs) / len(diffs), "sd_base": get(base, w)["mean_correct"]["sd"],
                     "better": sum(d > 1e-12 for d in diffs), "worse": sum(d < -1e-12 for d in diffs)}
    a = (total(rule, "wrongly_admitted_false") < total(base, "wrongly_admitted_false")
         and all(total(rule, "wrongly_admitted_false", w) <= total(base, "wrongly_admitted_false", w) for w in worlds)
         and all(total(rule, "own_admitted", w) <= total(base, "own_admitted", w) for w in worlds))
    b = total(rule, "suppressed") <= total(base, "suppressed")
    c = all(p["mean"] >= -p["sd_base"] for p in paired.values())
    (pr, rr), (pb, rb) = pooled(rule), pooled(base)
    d = pr is not None and pb is not None and pr >= pb - 0.02 and rr is not None and rb is not None and rr > rb
    verdict = ("supported" if a and b and c and d else "trade" if a else "no gain" if b and c else "harmful")
    shapes = sorted({s for w in worlds for s in get(base, w)["by_shape_sum"]})
    by_shape = {}
    for s in shapes:
        row = {}
        for k in DERIVATION_VERDICT_FIELDS:
            r = sum(get(rule, w)["by_shape_sum"].get(s, {}).get(k, 0) for w in worlds)
            q = sum(get(base, w)["by_shape_sum"].get(s, {}).get(k, 0) for w in worlds)
            row[k] = {base: q, rule: r}
        by_shape[s] = row
    ablations = {}
    for name in out["configurations"]:
        if name.startswith(rule + ":"):
            same = all(get(name, w)["totals_sum"][k] == get(rule, w)["totals_sum"][k] for w in worlds for k in COUNTS)
            ablations[name] = "not exercised by these worlds" if same else "changes the result"
    return {"verdict": verdict, "A_admissions": a, "B_suppression": b, "C_correct": c, "D_grouping": d,
            "totals": {k: {base: total(base, k), rule: total(rule, k)} for k in DERIVATION_VERDICT_FIELDS},
            "precision_recall": {base: [pb, rb], rule: [pr, rr]}, "paired_correct": paired, "by_shape": by_shape,
            "ablations": ablations}


# ---- the withheld worlds: one look each, recorded before it scores --------------------------------------------------------
FINAL_FAMILY = "sources_withheld"


def digest(path) -> str:
    """sha256 of a file's bytes (nothing is decoded or loaded)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _withheld():
    """(the withheld split, its generator): loaded here only, and only by final()."""
    from . import sources_withheld
    return sources_withheld.CYCLES[sources_withheld.SPLIT], sources_withheld.generate


def final(note, *, configs=None, mechanism="baseline") -> dict:
    """THE look at the withheld worlds for `configs` (names of CONFIGS; default FINAL_CONFIGS) and the references,
    recorded in the ledger as `mechanism` before anything is loaded or scored. Refused before the ledger line: a blank
    note, an unknown configuration, a tree that is dirty or not committed (or whose state git cannot give), a changed
    withheld worlds file or a changed development file (the withheld generator builds on it)."""
    from datetime import datetime, timezone
    from .. import cognition
    if not (note or "").strip():
        raise cognition.Refused("final: a note is required (it is the ledger line's reason)")
    configs = list(configs or FINAL_CONFIGS)
    unknown = sorted(set(configs) - set(CONFIGS))
    if unknown:
        raise cognition.Refused(f"final: unknown configuration(s) {', '.join(unknown)}; known: {', '.join(CONFIGS)}")
    env = cognition.environment()
    if env["git_dirty"] is not False or not env["git_commit"]:
        raise cognition.Refused("final: the tree must be clean and committed (and git must say so), so the ledger "
                                "line names the code it measured")
    for path, pinned in ((WITHHELD_FILE, WITHHELD_SHA256), (DEV_FILE, DEV_SHA256)):
        got = digest(path)
        if got != pinned:
            raise cognition.Refused(f"final: {path.name}'s SHA-256 is {got[:12]}..., not the pinned {pinned[:12]}...: "
                                    "it was changed and is not scored")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"at": at, "commit": env["git_commit"], "dirty": env["git_dirty"], "mechanism": mechanism, "ablate": [],
             "families": [FINAL_FAMILY], "configurations": sorted(configs) + [f"reference:{r}" for r in REFERENCES],
             "note": note, "withheld_sha256": WITHHELD_SHA256}
    lines = cognition._record_final(cognition.LEDGER, entry)
    spec, generate = _withheld()
    out = {"generated_at": at, "environment": env, "ledger_line": len(lines), "ledger_entry": entry}
    out |= _run_split(WITHHELD, spec, generate, configs=configs)
    return out
