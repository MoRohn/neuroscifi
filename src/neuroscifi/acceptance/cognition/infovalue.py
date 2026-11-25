"""Family 2, information value: does the system spend its investigations where they teach the most, and not on noise?

Two kinds of staged world (splits.INFO_VALUE), each with subjects whose expected information gain is known because
the world's generator knows how every report is produced:

core  A real individual (instance.store) whose mechanism is pinned by healthy reference readings. Cameras and
      partners have true bits; a "noise" partner's words are coin flips, so reading it teaches nothing about anything
      (the noisy TV), although its belief stays maximally uncertain and keeps looking worth asking about. One
      investigation = `per_step` readings of the subject the policy names, written with instance.evidence.observe.
      Known gain: scoring.readings_gain over the bench's own posterior of that subject (counts of readings with the
      reading error 0.10 of beliefs.py:57), zero for a noise subject. Realised information: the change in the log
      score of the true bits of every honest factor, read from beliefs.compute (scoring.log_score_gain): it measures
      movement towards what is true, so it cannot be gamed by a confident wrong belief.
web   A real Web Cortex individual whose knowledge is staged through webcortex.consolidate.run. Subjects: stale (stale
      but important: the world changed with probability `prior`), noise (sources disagree at random for ever), fresh
      (verified a day ago), gap (one source so far, true with probability `prior`), contested (two sides, A holds with
      probability `prior`). One investigation = one research outcome (a report of the given reliability about the
      truth; noise reports are coin flips) fed back through consolidate.run as a task with origin 'curiosity'. Known
      gain: scoring.report_gain over the bench's posterior of that subject. Cost: curiosity.cost of the mode the
      system would use (webcortex/config.py MODE_BUDGETS); `budget` is in those units.

The chooser interface: `choose(view) -> subject id | None` (None = investigate nothing this step). view: {"world":
"core" | "web", "seed", "db", "epoch" (core), "now", "subjects": [ids], "budget_left", "history": [earlier steps],
"per_step" (core)}. It never
contains the truth. Baselines run the real code: core -> instance.executive.decide(record=False) (the 7-rule order,
instance/executive.py:108-185), its scope mapped to the subject it names (partner:X and camera:X -> that subject;
test_reference -> a reading of a new reference root; act -> the first camera, "test a new action and check its
observed consequence"; defer/observe -> nothing); web -> webcortex.curiosity.rank (the timer's path,
scheduler.tick) and webcortex.autonomy.candidates (the idle investigator's path), top candidate mapped to its subject
by key. Reference policies (bounds, not the system): "oracle" (greatest known gain per cost, knows which subjects are
noise) and "random" (seeded).

Scores per world and seed: realised bits, realised bits per unit of budget, the sum of known gains of the chosen
investigations, the share of budget spent on noise subjects, and on stale-but-important ones. What it cannot show:
that real subjects behave like these channels, or the value of a question answered by the owner (an owner's answer
to a partner question is not evidence in the belief model today: executive.resolve records it, beliefs ignore it).
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import random
import tempfile
from pathlib import Path

from ...instance import beliefs, evidence, executive, store
from . import scoring

T0 = 1_800_000_000.0
DAY = 86_400.0
READING_ERROR = beliefs.READING_ERROR
EPOCH = "bench"


# ---- core world ------------------------------------------------------------------------------------------------------

class _Core:
    def __init__(self, spec, seed, db):
        self.spec, self.db, self.n = spec, db, 0
        self.rng = random.Random(f"info-core:{spec['name']}:{seed}")
        self.w = self.rng.randint(0, 1)
        self.truth = {"mechanism": self.w}
        self.subjects, self.noise, self.counts, self.extra_refs = [], set(), {"mechanism": [0, 0]}, 0
        for kind, items in (("camera", spec["cameras"]), ("partner", spec["partners"])):
            for item in items:
                sid = f"{kind}:{item['root']}"
                self.subjects.append(sid)
                self.truth[sid] = int(item["truth"])
                self.counts[sid] = [0, 0]
                if item.get("noise"):
                    self.noise.add(sid)
        self.subjects.append("reference")

    def now(self):
        return T0 + 10 + self.n

    def _obs(self, channel, root, relation):
        self.n += 1
        action = self.rng.randint(0, 1)
        evidence.observe(self.db, {"id": f"ev-{self.n}", "epoch": EPOCH, "channel": channel, "root": root,
                                   "action": action, "bit": relation ^ action, "provenance": "simulated",
                                   "actor": "simulator"}, self.now())

    def reference(self, root):
        """One reading of a healthy reference fixture (it reads the mechanism itself, with the reading error)."""
        bit = self.w ^ (self.rng.random() < READING_ERROR)
        self.counts["mechanism"][bit] += 1
        self._obs("reference", root, bit)

    def read(self, sid, k):
        """k readings of a subject from the world (and the bench's own tally of them)."""
        if sid == "reference":
            self.extra_refs += 1
            for _ in range(k):
                self.reference(f"ref-extra-{self.extra_refs}")
            return
        kind, root = sid.split(":", 1)
        for _ in range(k):
            if sid in self.noise:
                bit = self.rng.randint(0, 1)
            else:
                bit = self.truth[sid] ^ (self.rng.random() < READING_ERROR)
            self.counts[sid][bit] += 1
            self._obs("camera" if kind == "camera" else "word", root, self.w ^ bit)

    def known_gain(self, sid, k):
        if sid in self.noise:
            return 0.0
        n0, n1 = self.counts["mechanism" if sid == "reference" else sid]
        p = _count_posterior(n0, n1)
        return scoring.readings_gain(p, READING_ERROR, k)

    def beliefs(self):
        c = beliefs.compute(self.db, EPOCH)
        out = {"mechanism": c["mechanism"]["p_inverted"]}
        for sid in self.subjects:
            if sid == "reference":
                continue
            kind, root = sid.split(":", 1)
            table = c["sensors"] if kind == "camera" else c["partners"]
            out[sid] = table.get(root, {}).get("p_inverted", 0.5)
        return out


def _count_posterior(n0, n1) -> float:
    """P(bit = 1) after n1 readings saying 1 and n0 saying 0, uniform prior, reading error READING_ERROR."""
    import math
    llr = (n1 - n0) * math.log((1 - READING_ERROR) / READING_ERROR)
    return 1.0 / (1.0 + math.exp(-llr)) if llr > -700 else 0.0


def core_world(spec: dict, seed: int, chooser) -> dict:
    with tempfile.TemporaryDirectory(prefix="nsf-cognition-") as directory:
        db = store.create(Path(directory) / "core.sqlite", T0, note="cognitive benchmark: information value (core)")
        with closing(db):
            evidence.start_epoch(db, EPOCH, T0)
            world = _Core(spec, seed, db)
            for i in range(spec["reference_roots"]):
                for _ in range(spec["reference_readings"]):
                    world.reference(f"ref-{i}")
            for kind, items in (("camera", spec["cameras"]), ("partner", spec["partners"])):
                for item in items:
                    world.read(f"{kind}:{item['root']}", item["initial"])
            start = world.beliefs()
            history, spent = [], 0
            for step in range(spec["budget"]):
                view = {"world": "core", "seed": seed, "db": db, "epoch": EPOCH, "now": world.now(),
                        "subjects": list(world.subjects),
                        "budget_left": spec["budget"] - spent, "history": list(history), "per_step": spec["per_step"]}
                sid = (max(world.subjects, key=lambda s: (world.known_gain(s, spec["per_step"]), s))
                       if chooser == "oracle" else chooser.choose(view))
                spent += 1
                if sid not in world.subjects:
                    history.append({"step": step, "subject": None, "known_gain": 0.0})
                    continue
                gain = world.known_gain(sid, spec["per_step"])
                world.read(sid, spec["per_step"])
                history.append({"step": step, "subject": sid, "known_gain": gain, "noise": sid in world.noise})
            end = world.beliefs()
        honest = [s for s in start if s not in world.noise]
        realised = sum(scoring.log_score_gain(start[s], end[s], world.truth[s]) for s in honest)
        spurious = sum(abs(end[s] - 0.5) - abs(start[s] - 0.5) for s in world.noise)
    steps = len(history)
    return {"realised_bits": realised, "realised_bits_per_budget": realised / spec["budget"],
            "known_gain_bits": sum(h["known_gain"] for h in history),
            "noise_share": sum(1 for h in history if h.get("noise")) / steps,
            "idle_share": sum(1 for h in history if h["subject"] is None) / steps,
            "noise_confidence_change": spurious, "choices": [h["subject"] for h in history]}


class ExecutiveChooser:
    """Baseline (core): the executive's rule order through instance.executive.decide(record=False)."""

    def choose(self, view):
        if view["world"] != "core":
            return None
        d = executive.decide(view["db"], view["epoch"], view["now"], record=False)
        scope = d.get("scope") or ""
        if d["decision"] == "test_reference":
            return "reference" if scope == "mechanism" else _scope_subject(scope, view)
        if d["decision"] in ("ask", "calibrate"):
            return _scope_subject(scope, view)
        if d["decision"] == "act":
            return next((s for s in view["subjects"] if s.startswith("camera:")), None)
        return None


def _scope_subject(scope, view):
    if scope.startswith("partner:"):
        sid = scope
    elif scope.startswith("camera:"):
        sid = scope
    else:
        return None
    return sid if sid in view["subjects"] else None


# ---- web world -------------------------------------------------------------------------------------------------------

_MODE = {"stale": "fact_verification", "fresh": "fact_verification", "contested": "fact_verification",
         "gap": "gap_resolution", "noise": "gap_resolution"}
_RELIABILITY = {"fresh": 0.95, "noise": 0.5}
_PRIOR = {"fresh": 0.02, "noise": 0.5}


def _cost(kind):
    from ...webcortex import curiosity
    return curiosity.cost(_MODE[kind])


def _words(sid):
    return {"key": f"{sid} latest version", "name": sid.capitalize()}


class _Web:
    """Staging and outcomes through the real consolidation (webcortex.consolidate.run)."""

    def __init__(self, spec, seed, db):
        from ...webcortex import state
        self.spec, self.db = spec, db
        self.rng = random.Random(f"info-web:{spec['name']}:{seed}")
        self.subjects = {s["id"]: dict(s) for s in spec["subjects"]}
        self.truth, self.p, self.version = {}, {}, {}
        self.now = T0 + max([s.get("age_days", 0.0) for s in spec["subjects"]] + [2.0]) * DAY
        self.n = 0
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
        for sid, s in self.subjects.items():
            kind = s["kind"]
            prior = s.get("prior", _PRIOR.get(kind, 0.5))
            self.p[sid] = prior
            self.truth[sid] = int(self.rng.random() < prior) if kind != "noise" else None
            self.version[sid] = 4
            self._stage(sid, kind, s)

    # -- the evaluating stage's output contract (types.Claim / ClaimCluster), as in tests/webcortex/test_consolidate
    def _claim(self, host, sid, text, published, retrieved):
        from urllib.parse import urlsplit
        from ...webcortex import text as wtext
        from ...webcortex.types import Claim
        url = f"http://{host}/{sid}"
        return Claim(id=hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:24], text=text, source_url=url,
                     domain=urlsplit(url).hostname, title="t", retrieved_at=retrieved, key=_words(sid)["key"],
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

    def _sentence(self, sid, version, n):
        text = f"{_words(sid)['name']} 1.{version}.0 is the latest {sid} release."
        return text if n == 0 else f"According to the release notes read by reviewer {n}, {text}"

    def _agree(self, sid, version, hosts, published, retrieved):
        return self._cluster([self._claim(h, sid, self._sentence(sid, version, i), published, retrieved)
                              for i, h in enumerate(hosts)])

    def _consolidate(self, clusters, at, origin="test", objective=None):
        from ...webcortex import consolidate, orchestrator, state
        objective = objective or f"What is the latest {clusters[0].key} release?"
        prepared = orchestrator.prepare(self.db, objective, at, mode="fact_verification", origin=origin)
        with store.write(self.db):
            tid = orchestrator.insert(self.db, prepared, at)
            state.save_clusters(self.db, tid, clusters, at)
        out = consolidate.run(self.db, tid, at)
        with store.write(self.db):
            self.db.execute("UPDATE web_tasks SET status='completed' WHERE id=?", (tid,))
        return out

    def _hosts(self, k):
        self.n += 1
        return tuple(f"s{self.n}-{i}.example" for i in range(k))

    def _noise_cluster(self, sid, at):
        a_val, b_val = self.rng.randint(10, 99), self.rng.randint(10, 99)
        while b_val == a_val:
            b_val = self.rng.randint(10, 99)
        h = self._hosts(2)
        name = _words(sid)["name"]
        a = self._claim(h[0], sid, f"The {name} index stands at {a_val} points today.", None, at)
        b = self._claim(h[1], sid, f"The {name} index stands at {b_val} points today.", None, at)
        return self._cluster([a, b], status="contested", confidence=0.5,
                             contradicting=[{"a": a.id, "b": b.id, "reason": f"different values: {a_val} vs {b_val}"}])

    def _stage(self, sid, kind, s):
        from ...webcortex import knowledge
        if kind in ("stale", "fresh", "contested"):
            at = T0 if kind != "fresh" else self.now - DAY
            out = self._consolidate([self._agree(sid, 4, self._hosts(2), at - DAY, at)], at)
            kid = out["provisional"][0]
            knowledge.review(self.db, kid, True, at)
            if kind == "contested":
                self._consolidate([self._agree(sid, 3, self._hosts(2), at - 3 * DAY, at + 1)], at + 1)
        elif kind == "gap":
            at = self.now - DAY
            self._consolidate([self._cluster([self._claim(self._hosts(1)[0], sid, self._sentence(sid, 4, 0),
                                                          at - DAY, at)])], at)
        elif kind == "noise":
            self._consolidate([self._noise_cluster(sid, T0 + DAY)], T0 + DAY)

    def known_gain(self, sid):
        kind = self.subjects[sid]["kind"]
        r = self.subjects[sid].get("reliability", _RELIABILITY.get(kind, 0.9))
        return scoring.report_gain(self.p[sid], r)

    def investigate(self, sid):
        """One research outcome about `sid`, fed back through consolidate.run (origin 'curiosity'); returns the
        realised bits about the subject's truth (0 for noise)."""
        kind = self.subjects[sid]["kind"]
        r = self.subjects[sid].get("reliability", _RELIABILITY.get(kind, 0.9))
        at = self.now
        if kind == "noise":
            self._consolidate([self._noise_cluster(sid, at)], at, origin="curiosity")
            return 0.0
        x = self.truth[sid]
        y = x if self.rng.random() < r else 1 - x
        before = self.p[sid]
        self.p[sid] = scoring.report_posterior(before, r, y)
        if kind in ("stale", "fresh"):
            if y == 1:                                    # the report says a newer version is out
                self.version[sid] += 1
            cluster = self._agree(sid, self.version[sid], self._hosts(2), at - DAY, at)
        elif kind == "gap":
            cluster = (self._agree(sid, 4, self._hosts(2), at - DAY, at) if y == 1 else
                       self._agree(sid, 5, self._hosts(2), at - DAY, at))
        else:                                             # contested: y=1 -> side A (version 4) holds
            cluster = self._agree(sid, 4 if y == 1 else 3, self._hosts(3), at - DAY, at)
        self._consolidate([cluster], at, origin="curiosity")
        return scoring.log_score_gain(before, self.p[sid], x)


def web_world(spec: dict, seed: int, chooser) -> dict:
    with tempfile.TemporaryDirectory(prefix="nsf-cognition-") as directory:
        db = store.create(Path(directory) / "web.sqlite", T0, note="cognitive benchmark: information value (web)")
        with closing(db):
            world = _Web(spec, seed, db)
            history, spent, realised = [], 0.0, 0.0
            budget = float(spec["budget"])
            for step in range(int(3 * spec["budget"])):
                if spent >= budget - 1e-9:
                    break
                world.now += spec["step_hours"] * 3600.0
                view = {"world": "web", "seed": seed, "db": db, "now": world.now, "subjects": list(world.subjects),
                        "budget_left": budget - spent, "history": list(history)}
                if chooser == "oracle":
                    best = max(world.subjects,
                               key=lambda s: (world.known_gain(s) / _cost(world.subjects[s]["kind"]), s))
                    sid = best if world.known_gain(best) > 1e-9 else None
                else:
                    sid = chooser.choose(view)
                if sid not in world.subjects:
                    history.append({"step": step, "subject": None, "known_gain": 0.0, "cost": 0.0})
                    continue
                kind = world.subjects[sid]["kind"]
                cost = _cost(kind)
                if spent + cost > budget + 1e-9:
                    break
                gain = world.known_gain(sid)
                bits = world.investigate(sid)
                spent += cost
                realised += bits
                history.append({"step": step, "subject": sid, "kind": kind, "known_gain": gain, "cost": cost,
                                "realised": bits})
    chosen = [h for h in history if h["subject"] is not None]
    return {"realised_bits": realised, "realised_bits_per_budget": realised / budget, "budget_spent": spent,
            "known_gain_bits": sum(h["known_gain"] for h in chosen),
            "noise_share": sum(h["cost"] for h in chosen if h["kind"] == "noise") / budget,
            "stale_share": sum(h["cost"] for h in chosen if h["kind"] == "stale") / budget,
            "idle_steps": sum(1 for h in history if h["subject"] is None),
            "choices": [h["subject"] for h in history]}


def _subject_of(key, view):
    for sid in view["subjects"]:
        if key == _words(sid)["key"]:
            return sid
    return None


class CuriosityChooser:
    """Baseline (web, the timer's path): the top candidate of webcortex.curiosity.rank (scheduler.tick)."""

    def choose(self, view):
        if view["world"] != "web":
            return None
        from ...webcortex import curiosity
        for c in curiosity.rank(view["db"], view["now"]):
            if not c["excluded"]:
                return _subject_of(c["target"].get("key"), view)
        return None


class AutonomyChooser:
    """Baseline (web, the idle investigator's path): the top chosen candidate of webcortex.autonomy.candidates."""

    def choose(self, view):
        if view["world"] != "web":
            return None
        from ...webcortex import autonomy
        chosen = autonomy.candidates(view["db"], view["now"])["chosen"]
        return _subject_of(chosen[0]["key"], view) if chosen else None


class BaselineChooser:
    """The system's baseline in both worlds: the executive (core) and the curiosity ranking (web)."""

    def __init__(self, web="curiosity"):
        self.core = ExecutiveChooser()
        self.web = CuriosityChooser() if web == "curiosity" else AutonomyChooser()

    def choose(self, view):
        return (self.core if view["world"] == "core" else self.web).choose(view)


class RandomChooser:
    """Reference: a uniform choice among the subjects, seeded by the world's seed and the step."""

    def choose(self, view):
        rng = random.Random(f"random-chooser:{view['world']}:{view['seed']}:{len(view['history'])}")
        return rng.choice(view["subjects"])


def run(split_spec: dict, make_chooser, *, seeds=None) -> dict:
    """Every world of the split under every seed; per world the scores averaged over seeds and their spread.
    make_chooser() builds a fresh chooser per world and seed; make_chooser = "oracle" runs the reference oracle."""
    seeds = seeds or split_spec["seeds"]
    out = {"seeds": list(seeds), "core": {}, "web": {}}
    keys = {"core": ("realised_bits", "realised_bits_per_budget", "known_gain_bits", "noise_share", "idle_share"),
            "web": ("realised_bits", "realised_bits_per_budget", "known_gain_bits", "noise_share", "stale_share",
                    "idle_steps")}
    for kind, fn in (("core", core_world), ("web", web_world)):
        for spec in split_spec.get(kind, ()):
            rows = [fn(spec, seed, "oracle" if make_chooser == "oracle" else make_chooser()) for seed in seeds]
            out[kind][spec["name"]] = {k: scoring.spread([r[k] for r in rows]) for k in keys[kind]}
            out[kind][spec["name"]]["choices"] = {str(seed): r["choices"] for seed, r in zip(seeds, rows)}
    for kind in ("core", "web"):
        worlds = list(out[kind].values())
        if worlds:
            out[f"{kind}_mean"] = {k: scoring.spread([w[k]["mean"] for w in worlds])["mean"] for k in keys[kind]}
    return out
