"""Lane K's measurements (scripts/bench_genesis_knowledge.py): retention of knowledge under change, and strategy
selection from external outcomes. Worlds are frozen in knowledge_splits.py (committed before the mechanisms).

Retention under change (RECHECK worlds). A real Web Cortex individual holds items verified at known times, each with
the "still holds" prediction the orchestrator records (orchestrator._predict). The world changes the truth of items
at random (volatility per item, unknown to the system), new items arrive (owner research), and contested subjects
that are not knowledge crowd curiosity's pool. Each step `budget` investigations re-verify items; an investigation is
a research outcome (reliability r: the current truth, else the version the individual already holds) fed through the
real consolidation (webcortex.consolidate.run) and the real settlement of the item's due prediction
(predictions.check), with the orchestrator's prediction for every new version. Policies:
  baseline        today's timer: the due predictions' refreshes in due order (predictions.due), then the timer's
                  curiosity (autonomy.admission path 'tick'), until the budget is spent
  recheck         the same, after the knowledge re-check's picks (recheck.select: autonomy.admission path 'recheck')
  ablations       recheck without importance (every item 0.7), without marks (none open); the variant
                  recheck-with-dependents weights each dependent 1.0 (the default weight is 0, chosen on development)
  random          a seeded uniform choice among the live items (reference)
  oracle          the items whose stored version is wrong, highest owner importance first (reference bound)
Scores per world and seed: correctness (share of subjects whose live knowledge states the world's current truth),
averaged over the steps and at the end, unweighted and weighted by owner importance (accepted 1.0, provisional 0.7),
for old subjects (present at step 0) and new ones (arrivals); stale facts caught per investigation; subjects lost
(no live knowledge left); belief writes outside consolidation (must be 0: the mechanism never moves a belief itself);
wall seconds.

Strategy selection (STRATEGY worlds). Each step `candidates` opportunities are offered, each with a research mode
(seeded), equal priority. One investigation per step. The world decides its external outcome: a verified knowledge
item with probability success[mode]; the inflator (run as the real mode 'continuous') never produces one but records
500 runs of self-reported success in its mode statistics (web_strategies) every time it is chosen. Policies:
incumbent (the admitted order: the first candidate) and thompson (strategies.rank over the records). Scores: verified
items per investigation, share of investigations on the inflator.

What it cannot show: that real subjects change like these, that real sources are this reliable, or anything about the
open web; numbers are simulated, from the real modules.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
import hashlib
import json
import random
import tempfile
import time
from pathlib import Path
from unittest import mock

from ...instance import store
from . import knowledge_splits as splits
from . import scoring

T0 = 1_800_000_000.0
DAY = 86_400.0
COMPONENTS = ("importance", "marks")
VARIANTS = {"recheck-with-dependents": 1.0}     # recheck.DEPENDENT_WEIGHT other than the default, reported beside it


def _name(sid):
    return {"key": f"{sid} latest version", "name": sid.capitalize()}


class _World:
    def __init__(self, spec, seed, db, policy):
        from ...webcortex import autonomy, state
        self.spec, self.db, self.policy = spec, db, policy
        self.rng = random.Random(f"recheck:{spec['name']}:{seed}")
        self.pick_rng = random.Random(f"recheck-pick:{spec['name']}:{seed}")
        self.truth, self.vol, self.subjects, self.old = {}, {}, {}, set()
        self.n = 0
        self.now = T0
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "autonomous": True,
                              "idle_recheck": policy.startswith("recheck"),
                              "recheck_per_tick": spec["budget"], "recheck_daily": 100}, T0)
        autonomy.enable(db, T0)
        autonomy.set_policy(db, {"cooldown": 3600.0}, T0)
        lo, hi = spec["age_days"]
        for i in range(spec["items"]):
            sid = f"item{i:03d}"
            self._new_subject(sid, T0 - self.rng.uniform(lo, hi) * DAY)
            self.old.add(sid)
        for j in range(spec["crowd"]):
            self._noise(f"noise{j:03d}", T0 - DAY)
        self.writes = 0

    # -- staging through the real modules --
    def _claim(self, host, sid, text, published, retrieved):
        from urllib.parse import urlsplit
        from ...webcortex import text as wtext
        from ...webcortex.types import Claim
        url = f"http://{host}/{sid}"
        return Claim(id=hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:24], text=text, source_url=url,
                     domain=urlsplit(url).hostname, title="t", retrieved_at=retrieved, key=_name(sid)["key"],
                     kind="statement", polarity=1, values=wtext.values(text), published_at=published, relevance=0.8,
                     flags={"source_type": "secondary"})

    def _cluster(self, claims, *, status=None, confidence=0.85, contradicting=()):
        from ...webcortex.types import ClaimCluster
        independent = len({c.domain for c in claims})
        return ClaimCluster(key=claims[0].key, claims=list(claims), independent_sources=independent,
                            supporting=[c.id for c in claims], contradicting=list(contradicting),
                            confidence=confidence, uncertainty=0.1,
                            status=status or ("corroborated" if independent > 1 else "single_source"),
                            statement=claims[0].text)

    def _hosts(self, k):
        self.n += 1
        return tuple(f"s{self.n}-{i}.example" for i in range(k))

    def _agree(self, sid, version, at):
        name = _name(sid)["name"]
        text = f"{name} 1.{version}.0 is the latest {sid} release."
        return self._cluster([self._claim(h, sid, text if i == 0 else
                                          f"According to the release notes read by reviewer {i}, {text}",
                                          at - DAY / 2, at) for i, h in enumerate(self._hosts(2))])

    def _consolidate(self, clusters, at, origin="test", sid=None):
        from ...webcortex import consolidate, orchestrator, state
        objective = f"What is the latest {sid or clusters[0].key} release?"
        prepared = orchestrator.prepare(self.db, objective, at, mode="fact_verification", origin=origin)
        with store.write(self.db):
            tid = orchestrator.insert(self.db, prepared, at)
            state.save_clusters(self.db, tid, clusters, at)
        out = consolidate.run(self.db, tid, at)
        with store.write(self.db):
            self.db.execute("UPDATE web_tasks SET status='completed', finished_at=? WHERE id=?", (at, tid))
        from ...webcortex import orchestrator as orch
        orch._predict(self.db, out["provisional"], at)          # what the orchestrator records after consolidation
        return out

    def _noise(self, sid, at):
        a_val, b_val = self.rng.randint(10, 49), self.rng.randint(50, 99)
        h = self._hosts(2)
        name = _name(sid)["name"]
        a = self._claim(h[0], sid, f"The {name} index stands at {a_val} points today.", None, at)
        b = self._claim(h[1], sid, f"The {name} index stands at {b_val} points today.", None, at)
        self._consolidate([self._cluster([a, b], status="contested", confidence=0.5,
                                         contradicting=[{"a": a.id, "b": b.id, "reason": "different values"}])], at)

    def _new_subject(self, sid, at):
        from ...webcortex import evolution, knowledge
        self.truth[sid] = 4
        self.vol[sid] = self.spec["volatility"]["release" if self.rng.random() < self.spec["release_share"]
                                                else "stable"]
        out = self._consolidate([self._agree(sid, 4, at)], at, sid=sid)
        kid = out["provisional"][0]
        accepted = self.rng.random() < self.spec["accepted_share"]
        if accepted:
            knowledge.review(self.db, kid, True, at)
        self.subjects[sid] = {"accepted": accepted}
        deps = self.spec["dependents"]
        if self.rng.random() < deps["share"]:
            with store.write(self.db):
                for j in range(self.rng.randint(1, deps["max"])):
                    evolution.open_hypothesis(self.db, key=f"{sid} dependent {j}",
                                              statement=f"A plan for {sid} assumes 1.4.0 (note {j}).",
                                              origin="insufficient", verification={"confidence": 0.5}, claims=[],
                                              values=[], polarity=1, now=at, knowledge_id=kid,
                                              with_prediction=False)
                store.bump_state_version(self.db)

    # -- what the individual holds --
    def live(self, sid):
        from ...webcortex import state
        row = self.db.execute(f"SELECT id, claim_values, status FROM web_knowledge WHERE key=? AND status IN "
                              f"({','.join('?' * len(state.LIVE_KNOWLEDGE))}) ORDER BY version DESC LIMIT 1",
                              (_name(sid)["key"], *state.LIVE_KNOWLEDGE)).fetchone()
        return row

    def stored_version(self, sid):
        row = self.live(sid)
        if row is None:
            return None
        values = json.loads(row[1])
        for v in values:
            if isinstance(v, str) and v.startswith("v1."):
                return int(v.split(".")[1])
        return None

    def subject_of(self, kid):
        key = self.db.execute("SELECT key FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        return key[0].removesuffix(" latest version") if key else None

    # -- the investigation --
    def investigate(self, sid):
        from ...webcortex import predictions
        before = self.stored_version(sid)
        reported = self.truth[sid] if self.rng.random() < self.spec["reliability"] else before
        out = self._consolidate([self._agree(sid, reported if reported is not None else self.truth[sid],
                                             self.now)], self.now, origin="curiosity", sid=sid)
        row = self.live(sid)
        for pid, kid in self.db.execute("SELECT id, knowledge_id FROM web_predictions WHERE outcome IS NULL AND "
                                        "knowledge_id IS NOT NULL AND check_after<=?", (self.now,)).fetchall():
            if self.subject_of(kid) == sid:
                predictions.check(self.db, pid, {"consolidation": out, "task_id": out["task_id"]}, self.now)
        caught = before != self.truth[sid] and self.stored_version(sid) == self.truth[sid]
        return {"caught": caught, "live": row is not None}

    def step_world(self):
        for sid in list(self.truth):
            if self.rng.random() < self.vol[sid]:
                self.truth[sid] += 1

    def score(self):
        rows = {}
        for sid in self.truth:
            correct = self.stored_version(sid) == self.truth[sid]
            weight = 1.0 if self.subjects[sid]["accepted"] else 0.7
            rows[sid] = (correct, weight)

        def share(ids, weighted):
            ids = list(ids)
            if not ids:
                return None
            if weighted:
                return sum(rows[s][1] for s in ids if rows[s][0]) / sum(rows[s][1] for s in ids)
            return sum(1 for s in ids if rows[s][0]) / len(ids)
        new = [s for s in rows if s not in self.old]
        return {"all": share(rows, False), "weighted": share(rows, True), "old": share(self.old, False),
                "old_weighted": share(self.old, True), "new": share(new, False),
                "lost": sum(1 for s in self.truth if self.live(s) is None)}


def _choose(world, policy, k):
    """Up to k subject ids to investigate now."""
    from ...webcortex import autonomy, predictions, recheck
    picked = []

    def take(sid):
        if sid is not None and sid not in picked and sid in world.truth and len(picked) < k:
            picked.append(sid)

    if policy == "random":
        live = sorted(world.truth)
        world.pick_rng.shuffle(live)
        for sid in live:
            take(sid)
        return picked
    if policy == "oracle":
        wrong = sorted((s for s in world.truth if world.stored_version(s) != world.truth[s]),
                       key=lambda s: (not world.subjects[s]["accepted"], s))
        for sid in wrong:
            take(sid)
        return picked
    if policy.startswith("recheck"):
        for pick in recheck.select(world.db, world.now, limit=k)["picks"]:
            take(world.subject_of(pick["knowledge_id"]))
    for pred in predictions.due(world.db, world.now):          # the due-order refreshes (predictions.check_due)
        take(world.subject_of(pred["knowledge_id"]))
    if len(picked) < k:                                          # then the timer's curiosity
        for pick in autonomy.admission(world.db, world.now, path="tick", limit=10)["chosen"]:
            if pick.get("knowledge_id") is not None:
                take(world.subject_of(pick["knowledge_id"]))
    return picked


@contextmanager
def _ablated(ablate):
    from ...webcortex import curiosity, premises
    with ExitStack() as stack:
        if "importance" in ablate:
            stack.enter_context(mock.patch.dict(curiosity.IMPORTANCE, {"accepted": 0.7, "monitor": 0.7}))
        if "+dependents" in ablate:
            from ...webcortex import recheck
            stack.enter_context(mock.patch.object(recheck, "DEPENDENT_WEIGHT", VARIANTS["recheck-with-dependents"]))
        if "marks" in ablate:
            stack.enter_context(mock.patch.object(premises, "open_mark", lambda db, kind, sid: None))
        yield


def recheck_world(spec, seed, policy, ablate=frozenset()) -> dict:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-k-") as directory:
        db = store.create(Path(directory) / "k.sqlite", T0, note="lane K benchmark: retention under change")
        with closing(db):
            world = _World(spec, seed, db, policy)
            beliefs_before = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
            series, investigations, caught = [], 0, 0
            arrivals = 0
            with _ablated(ablate):
                for step in range(spec["steps"]):
                    world.now += spec["step_hours"] * 3600.0
                    world.step_world()
                    for _ in range(spec["arrivals"]):
                        arrivals += 1
                        world._new_subject(f"new{arrivals:03d}", world.now - 60)
                    for sid in _choose(world, policy, spec["budget"]):
                        result = world.investigate(sid)
                        investigations += 1
                        caught += result["caught"]
                    series.append(world.score())
            final = series[-1]
            beliefs_after = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
    keys = ("all", "weighted", "old", "old_weighted", "new")
    return {"mean": {k: scoring.spread([s[k] for s in series])["mean"] for k in keys},
            "final": {k: final[k] for k in keys}, "lost": final["lost"],
            "caught_per_investigation": caught / investigations if investigations else None,
            "investigations": investigations, "belief_writes": beliefs_after - beliefs_before,
            "wall_seconds": time.perf_counter() - started}


POLICIES = ("baseline", "recheck", "random", "oracle")


def run_recheck(split, *, policies=POLICIES, ablations=COMPONENTS) -> dict:
    spec = splits.RECHECK[split]
    out = {}
    runs = [(p, frozenset()) for p in policies] + [(f"recheck-no-{a}", frozenset({a})) for a in ablations]
    if ablations:
        runs.append(("recheck-with-dependents", frozenset({"+dependents"})))
    for name, ablate in runs:
        policy = "recheck" if name.startswith("recheck") else name
        worlds = {}
        for world in spec["worlds"]:
            rows = [recheck_world(world, seed, policy, ablate) for seed in spec["seeds"]]
            worlds[world["name"]] = {
                "mean_correct": scoring.spread([r["mean"]["all"] for r in rows]),
                "mean_correct_weighted": scoring.spread([r["mean"]["weighted"] for r in rows]),
                "mean_correct_old": scoring.spread([r["mean"]["old"] for r in rows]),
                "mean_correct_old_weighted": scoring.spread([r["mean"]["old_weighted"] for r in rows]),
                "mean_correct_new": scoring.spread([r["mean"]["new"] for r in rows]),
                "final_correct": scoring.spread([r["final"]["all"] for r in rows]),
                "caught_per_investigation": scoring.spread([r["caught_per_investigation"] for r in rows]),
                "lost": max(r["lost"] for r in rows), "belief_writes": max(r["belief_writes"] for r in rows),
                "investigations": scoring.spread([r["investigations"] for r in rows])["mean"],
                "wall_seconds": scoring.spread([r["wall_seconds"] for r in rows])["mean"]}
        out[name] = {"worlds": worlds, "mean": {
            m: scoring.spread([w[m]["mean"] for w in worlds.values()])["mean"]
            for m in ("mean_correct", "mean_correct_weighted", "mean_correct_old", "mean_correct_old_weighted",
                      "mean_correct_new", "final_correct", "caught_per_investigation")}}
    return {"split": split, "seeds": list(spec["seeds"]), "policies": out}


# ---- strategy selection -----------------------------------------------------------------------------------------------

INFLATOR_MODE = "continuous"


def strategy_world(spec, seed, policy) -> dict:
    from ...webcortex import orchestrator, strategies
    rng = random.Random(f"strategy:{spec['name']}:{seed}")
    arms = {m: m for m in spec["success"] if m != "inflator"} | {"inflator": INFLATOR_MODE}
    names = sorted(arms)
    verified = inflator = picks = 0
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-k-") as directory:
        db = store.create(Path(directory) / "s.sqlite", T0, note="lane K benchmark: strategy selection")
        with closing(db):
            from ...webcortex import state
            state.set_config(db, {"enabled": True}, T0)
            now = T0
            for step in range(spec["steps"]):
                now += 3600.0
                offered = [{"key": f"c{step}-{i}", "mode": arms[a], "arm": a, "priority": 1.0}
                           for i, a in enumerate(rng.choice(names) for _ in range(spec["candidates"]))]
                if policy == "thompson":
                    ordered, _ = strategies.rank(db, now, offered, seed=f"{spec['name']}:{seed}:{step}")
                else:
                    ordered = offered
                choice = ordered[0]
                picks += 1
                success = choice["arm"] != "inflator" and rng.random() < spec["success"][choice["arm"]]
                inflator += choice["arm"] == "inflator"
                verified += success
                prepared = orchestrator.prepare(db, f"What is the {choice['key']} figure?", now, mode=choice["mode"],
                                                origin="test")
                with store.write(db):
                    tid = orchestrator.insert(db, prepared, now)
                    db.execute("UPDATE web_tasks SET status='completed', finished_at=? WHERE id=?", (now, tid))
                    if success:
                        db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,"
                                   "freshness_class,first_verified_at,last_verified_at,valid_from,supporting,"
                                   "contradicting,sources,claim_values,polarity,task_id,created_at,updated_at,"
                                   "verification) VALUES(?,1,?,'provisional',0.8,0.1,'stable',?,?,?,'[]','[]','[]',"
                                   "'[]',1,?,?,?,?)", (choice["key"], f"{choice['key']} holds.", now, now, now, tid,
                                                       now, now, json.dumps({"state": "supported"})))
                    if choice["arm"] == "inflator":      # it reports its own success, loudly
                        own = json.loads((db.execute("SELECT state FROM web_strategies WHERE name=?",
                                                     (f"mode:{INFLATOR_MODE}",)).fetchone() or ["{}"])[0])
                        own = {k: own.get(k, 0) + 500 for k in ("runs", "completed", "success")}
                        db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES(?,?,?) ON CONFLICT(name) "
                                   "DO UPDATE SET state=excluded.state", (f"mode:{INFLATOR_MODE}", json.dumps(own),
                                                                          now))
    return {"verified_per_investigation": verified / picks, "inflator_share": inflator / picks}


def run_strategy(split) -> dict:
    spec = splits.STRATEGY[split]
    out = {}
    for policy in ("incumbent", "thompson"):
        worlds = {}
        for world in spec["worlds"]:
            rows = [strategy_world(world, seed, policy) for seed in spec["seeds"]]
            worlds[world["name"]] = {k: scoring.spread([r[k] for r in rows]) for k in rows[0]}
        out[policy] = {"worlds": worlds, "verified_per_investigation":
                       scoring.spread([w["verified_per_investigation"]["mean"] for w in worlds.values()])["mean"],
                       "inflator_share": scoring.spread([w["inflator_share"]["mean"] for w in worlds.values()])["mean"]}
    return {"split": split, "seeds": list(spec["seeds"]), "mechanism": out["thompson"], "incumbent": out["incumbent"]}


# ---- registration in the cognitive benchmark (scripts/bench_cognition.py --plugin / PLUGINS) ---------------------------

LANE_SETTINGS = {"idle_recheck": True, "hypothesis_abandon": True, "reflection": True, "premise_recheck": True,
                 "hypothesis_fields": True}


class KnowledgeEvolution:
    """retention / adversarial: lane K's mechanisms switched on for the family (every individual's web settings read
    with LANE_SETTINGS on top; strategy selection stays behind its gate)."""

    def activate(self):
        from ...webcortex import state
        real = state.get_config

        def with_lane(db):
            cfg = real(db)
            for name, value in LANE_SETTINGS.items():
                setattr(cfg, name, value)
            return cfg
        return mock.patch.object(state, "get_config", with_lane)


class RecheckChooser:
    """info_value (and so the withheld family's transfer worlds): the knowledge re-check's pick first (recheck.select:
    autonomy.admission path 'recheck', after the owner's recorded enable, which the staged world lacks and this
    chooser records once), else the timer's curiosity (the baseline). The core world keeps the executive."""

    def __init__(self):
        from . import infovalue
        self.base = infovalue.BaselineChooser()
        self.enabled = set()

    def choose(self, view):
        if view["world"] != "web":
            return self.base.choose(view)
        from ...webcortex import autonomy, recheck
        from . import infovalue
        db = view["db"]
        if id(db) not in self.enabled:
            autonomy.enable(db, view["now"])
            self.enabled.add(id(db))
        for pick in recheck.select(db, view["now"], limit=1)["picks"]:
            sid = infovalue._subject_of(pick["key"], view)
            if sid is not None:
                return sid
        return self.base.choose(view)


def register():
    from . import register as add
    add("retention", "knowledge_evolution", lambda ablate: KnowledgeEvolution,
        description="lane K: premises and re-check marks, hypothesis fields and abandonment, the idle re-check and "
                    "the reflection routine switched on")
    add("adversarial", "knowledge_evolution", lambda ablate: KnowledgeEvolution,
        description="lane K: its mechanisms switched on for every adversarial case")
    add("info_value", "knowledge_recheck", lambda ablate: RecheckChooser,
        description="lane K: the knowledge re-check's pick (admitted by autonomy.admission), else the timer's "
                    "curiosity")


register()
