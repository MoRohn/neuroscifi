"""Family 3, counterfactual and prospective validity: questions with exact known answers about "what if this had been
different" and "what follows after action A".

Three sources of queries (splits.COUNTERFACTUAL), every answer computed exactly by the bench from the generator's
own truth:

scm     Linear Gaussian structural causal models (chain, fork, collider, mediator) whose equations and observed
        values are given in the query. Rungs: observational E[Y | X = x], interventional E[Y | do(X = x)], and
        counterfactual "Y had X been x'" given every observed value (abduction of the noise, then the intervention).
        A "confounded" model gives the observed graph, the observational means and covariance, and names a hidden
        common cause of X and Y. Back-door adjustment cannot identify the effect of do(X) on Y, but X reaches Y only
        through an observed mediator (X0 -> X1 -> ... -> Y, the hidden cause touching only X0 and Y), so a
        FRONT-DOOR adjustment does (Pearl 1995): these queries carry the exact interventional truth and are marked
        identified_by="front_door". Until the Wave 1 review (B3) they were scored as unanswerable and a decline
        counted as correct; now they are answerable questions: a decline is not within tolerance in the headline
        (since the review's re-review) and is also counted on its own line (scoring.answer_scores front_door_*).
        With only two nodes there is no mediator and the query stays
        unanswerable. Scale for the relative error: the standard deviation of Y.
graph   The individual learns and promotes a graph on a lab.graph_bench world through the product path
        (datasets.simulate -> graphs.learn -> graphs.promote). Queries: population j's next target k steps after a
        held-out state, without an action or after pushing population i by u at the next step. Truth from the
        world's true matrix A (lab/graph_bench.py:21-48): E[y_t | x_t] = A x_t, x_{t+1} = 0.2 x_t + y_t + drive
        (mean-zero drive), so E[y_{t+k} | x_t] = A (0.2 I + A)^(k-1) x_t, and an action adds A (0.2 I + A)^(k-2) u e_i.
        Scale: the standard deviation of that population's held-out targets.
belief  What-if on the belief model: "what would P(partner bit = 1) be had reading #i been the other bit?" The exact
        answer is the posterior of the same individual with that one reading flipped, computed on an in-memory copy
        (sqlite backup) so nothing touches the individual. Scale 1 (it is a probability).

The answerer interface: `answer(query) -> {"value": float | None, "sd": float | None}`; value None = "cannot answer".
A query carries no truth; graph and belief queries carry the individual's open database (read-only use expected).

Scores (scoring.answer_scores; Wave 1 review B3, corrected at its re-review): the headline is
answered_within_tolerance, the share of EVERY answerable question (the front-door ones included; a decline is not
within tolerance) answered within scoring.TOLERANCE x scale of the truth, always beside the same share at
scoring.STRICT_TOLERANCE and the largest relative error, and the interval score of the stated 95 % interval (a
uselessly wide interval pays its width). Every score is reported for three groups that measure different things and
are never pooled in a claim (GROUPS): exact arithmetic on equations handed to the answerer (handed_equations), belief
what-ifs whose truth is the same posterior recomputed on a copy (belief_what_if), and forecasts and actions on a
graph the individual learned (learned_graph, the substantive group); the front-door questions are a fourth line
(front_door). `summary` pools the groups and is marked so in every result (`summary_is`: reported, not a claim). The
old "coverage" (any answer counts) is kept as answered_share; the figure without the front-door questions as
answered_within_tolerance_without_front_door (secondary, never the headline).
The baseline is what the system can answer today: the active graph's one-step prediction (states @ A.T, as
instance/recall.py:42-51 computes it), with sd = sqrt(validation MSE) from the promotion record; nothing else
(no multi-step rollout, no action input in the graph, no SCM engine, no belief sandbox: audit-core Q5). Coverage is
reported as it is. What it cannot show: counterfactual validity on real-world causal questions (these models are
small and fully specified).
"""
from __future__ import annotations

from contextlib import closing
import math
import random
import sqlite3
import tempfile
from pathlib import Path

import numpy as np

from ...instance import beliefs, datasets, evidence, graphs, store
from ...lab import graph_bench
from . import scoring

T0 = 1_800_000_000.0
EPOCH = "bench"
_STRUCTURES = ("chain", "fork", "collider", "mediator", "confounded")


# ---- linear Gaussian SCMs --------------------------------------------------------------------------------------------

def make_scm(spec: dict, rng: random.Random) -> dict:
    """Nodes X0..X{n-1} in causal order, edges {child: {parent: coefficient}}, intercepts, noise sd 1. X = X0 is the
    cause asked about and Y = X{n-1} the outcome. chain: X0 -> X1 -> ... ; fork: X1 <- X0 -> X2 ... (Y also
    caused by X0 directly); collider: X0 -> Y <- X1 (n = 3) plus extra parents; mediator: X0 -> M -> Y and X0 -> Y;
    confounded: hidden U -> X0, U -> Y, X0 -> Y (U is node 'U', never observed)."""
    n, kind = spec["nodes"], spec["structure"]
    if kind not in _STRUCTURES:
        raise ValueError(f"unknown structure {kind!r}")
    coef = lambda: rng.choice((-1, 1)) * rng.uniform(0.5, 1.5)  # noqa: E731
    names = [f"X{i}" for i in range(n)]
    edges = {v: {} for v in names}
    if kind == "chain":
        for i in range(1, n):
            edges[names[i]][names[i - 1]] = coef()
    elif kind == "fork":
        for i in range(1, n):
            edges[names[i]][names[0]] = coef()
    elif kind == "collider":
        for i in range(n - 1):
            edges[names[-1]][names[i]] = coef()
    elif kind == "mediator":
        for i in range(1, n):
            edges[names[i]][names[i - 1]] = coef()
        edges[names[-1]][names[0]] = coef()
    else:
        edges = {"U": {}, **edges}
        names = ["U", *names]
        edges["X0"]["U"] = coef()
        for i in range(1, n):
            edges[f"X{i}"][f"X{i - 1}"] = coef()
        edges[f"X{n - 1}"]["U"] = coef()
    intercept = {v: rng.uniform(-1, 1) for v in names}
    return {"order": names, "edges": edges, "intercept": intercept, "noise_sd": 1.0, "cause": "X0",
            "outcome": f"X{n - 1}", "hidden": ["U"] if kind == "confounded" else []}


def _solve(scm, noise, do=None) -> dict:
    """Values of every node given its noise terms, with do = {node: value} overriding equations."""
    do = do or {}
    out = {}
    for v in scm["order"]:
        out[v] = do[v] if v in do else (scm["intercept"][v] + noise[v] +
                                         sum(c * out[p] for p, c in scm["edges"][v].items()))
    return out


def _linear(scm, do=None):
    """Each node as (constant, {noise term: weight}) under do: exact means and covariances follow."""
    do = do or {}
    out = {}
    for v in scm["order"]:
        if v in do:
            out[v] = (do[v], {})
            continue
        const, w = scm["intercept"][v], {v: 1.0}
        for p, c in scm["edges"][v].items():
            pc, pw = out[p]
            const += c * pc
            for k, x in pw.items():
                w[k] = w.get(k, 0.0) + c * x
        out[v] = (const, w)
    return out


def _cov(a, b):
    return sum(x * b[1].get(k, 0.0) for k, x in a[1].items())


def scm_queries(spec: dict, seed: int) -> list[dict]:
    rng = random.Random(f"scm:{spec['structure']}:{spec['nodes']}:{seed}")
    scm = make_scm(spec, rng)
    x, y = scm["cause"], scm["outcome"]
    lin = _linear(scm)
    sd_y = math.sqrt(_cov(lin[y], lin[y]))
    public = {k: scm[k] for k in ("order", "edges", "intercept", "noise_sd", "cause", "outcome")}
    rungs = (("observational", "interventional", "counterfactual") if spec["structure"] != "confounded"
             else ("confounded",))
    front_door = spec["structure"] == "confounded" and spec["nodes"] >= 3     # an observed mediator X1 exists
    out = []
    for i in range(spec["queries"]):
        rung = rungs[i % len(rungs)]
        value = round(rng.uniform(-2, 2), 3)
        if rung == "observational":
            truth = lin[y][0] + _cov(lin[y], lin[x]) / _cov(lin[x], lin[x]) * (value - lin[x][0])
            q = {"rung": rung, "model": public, "given": {x: value}, "ask": y}
        elif rung == "interventional":
            truth = _linear(scm, {x: value})[y][0]
            q = {"rung": rung, "model": public, "do": {x: value}, "ask": y}
        elif rung == "counterfactual":
            noise = {v: rng.gauss(0, 1) for v in scm["order"]}
            factual = _solve(scm, noise)
            q = {"rung": rung, "model": public, "observed": {v: round(factual[v], 6) for v in scm["order"]},
                 "had": {x: value}, "ask": y}
            truth = _solve(scm, {v: q["observed"][v] - scm["intercept"][v] - sum(
                c * q["observed"][p] for p, c in scm["edges"][v].items()) for v in scm["order"]}, {x: value})[y]
        else:
            xs = [v for v in scm["order"] if v not in scm["hidden"]]
            observed_cov = {a: {b: _cov(lin[a], lin[b]) for b in xs} for a in xs}
            q = {"rung": rung, "graph": {v: [p for p in scm["edges"][v] if p not in scm["hidden"]]
                                         for v in xs}, "hidden_common_cause": [x, y],
                 "observational_mean": {v: lin[v][0] for v in xs},
                 "observational_covariance": observed_cov, "do": {x: value}, "ask": y}
            truth = _linear(scm, {x: value})[y][0] if front_door else None
        row = {"kind": "scm", "structure": spec["structure"], "query": q, "truth": truth,
               "answerable": truth is not None, "scale": sd_y}
        if rung == "confounded" and front_door:
            row["identified_by"] = "front_door"
        out.append(row)
    return out


# ---- graph forecasts on a promoted graph -----------------------------------------------------------------------------

def graph_queries(spec: dict, seed: int, db) -> list[dict]:
    rng = random.Random(f"graph:{spec['world_seed']}:{seed}")
    context = f"world-{spec['world_seed']}"
    ids = datasets.simulate(db, context, T0 + 1, seed=spec["world_seed"])
    revision = graphs.learn(db, context, T0 + 2, train=ids["train"], validation=ids["validation"], holder="bench")
    graphs.promote(db, revision, T0 + 3)
    a = graph_bench.world(np.random.default_rng(spec["world_seed"]))[0]
    step = 0.2 * np.eye(a.shape[0]) + a
    states, targets = datasets.load(db, ids["heldout"])
    scale = targets.std(axis=0)
    out = []
    total = spec["queries"] + spec["action_queries"]
    for i in range(total):
        row = rng.randrange(len(states))
        x = states[row]
        j = rng.randrange(a.shape[0])
        k = spec["horizons"][i % len(spec["horizons"])]
        action = None
        if i >= spec["queries"]:
            k = max(2, k)
            action = {"population": rng.randrange(a.shape[0]), "push": round(rng.choice((-1, 1)) * 0.6, 3)}
        mean = a @ np.linalg.matrix_power(step, k - 1) @ x
        if action is not None:
            u = np.zeros(a.shape[0])
            u[action["population"]] = action["push"]
            mean = mean + a @ np.linalg.matrix_power(step, k - 2) @ u
        out.append({"kind": "graph", "query": {"context": context, "state": [float(v) for v in x], "horizon": k,
                                               "action": action, "population": j, "db": db},
                    "truth": float(mean[j]), "answerable": True, "scale": float(scale[j]) or 1.0})
    return out


# ---- belief what-if --------------------------------------------------------------------------------------------------

def _posterior_with_flip(db, evidence_id, root) -> float:
    with closing(sqlite3.connect(":memory:")) as copy:
        db.backup(copy)
        copy.execute("UPDATE evidence SET bit = 1 - bit WHERE id=?", (evidence_id,))
        copy.commit()
        return beliefs.compute(copy, EPOCH)["partners"][root]["p_inverted"]


def belief_queries(spec: dict, seed: int, db) -> list[dict]:
    rng = random.Random(f"belief:{spec['partner_words']}:{seed}")
    w, truth_bit = rng.randint(0, 1), rng.randint(0, 1)
    evidence.start_epoch(db, EPOCH, T0)
    n = 0

    def obs(channel, root, relation):
        nonlocal n
        n += 1
        action = rng.randint(0, 1)
        evidence.observe(db, {"id": f"b-{n}", "epoch": EPOCH, "channel": channel, "root": root, "action": action,
                              "bit": relation ^ action, "provenance": "simulated", "actor": "simulator"}, T0 + n)
        return f"b-{n}"

    for r in range(3):
        for _ in range(4):
            obs("reference", f"ref-{r}", w ^ (rng.random() < beliefs.READING_ERROR))
    words = [obs("word", "p-q", w ^ truth_bit ^ (rng.random() < beliefs.READING_ERROR))
             for _ in range(spec["partner_words"])]
    out = []
    for i in range(spec["queries"]):
        eid = words[rng.randrange(len(words))]
        out.append({"kind": "belief", "query": {"db": db, "epoch": EPOCH, "evidence_id": eid, "factor": "partner:p-q",
                                                "ask": "p_inverted"},
                    "truth": _posterior_with_flip(db, eid, "p-q"), "answerable": True, "scale": 1.0})
    return out


# ---- running a split -------------------------------------------------------------------------------------------------

GROUPS = ("handed_equations", "belief_what_if", "learned_graph", "front_door")
SUMMARY = ("answered_within_tolerance", "answered_within_strict_tolerance", "max_relative_error",
           "answered_within_tolerance_without_front_door", "interval_score", "answered_share", "mean_relative_error",
           "interval_coverage", "declined_confounded_share")
GROUP_SUMMARY = ("answered_within_tolerance", "answered_within_strict_tolerance", "max_relative_error",
                 "interval_score", "answered_share", "mean_relative_error", "front_door_declined",
                 "front_door_within_tolerance")
POOLED = ("pooled over every group, the front-door questions included: reported, not a claim (the groups measure "
          "different things and are never pooled in a claim; see summary_by_group)")


def group(row) -> str:
    """Which of GROUPS a query belongs to (see the module doc)."""
    if row.get("identified_by") == "front_door":
        return "front_door"
    return {"scm": "handed_equations", "belief": "belief_what_if", "graph": "learned_graph"}[row["kind"]]


def evaluate(split_spec: dict, make_answerer, *, seeds=None) -> dict:
    seeds = seeds or split_spec["seeds"]
    by_kind: dict = {}
    per_seed, per_seed_group = [], []
    for seed in seeds:
        rows = []
        with tempfile.TemporaryDirectory(prefix="nsf-cognition-") as directory:
            db = store.create(Path(directory) / "cf.sqlite", T0, note="cognitive benchmark: counterfactual")
            with closing(db):
                queries = [q for s in split_spec.get("scm", ()) for q in scm_queries(s, seed)]
                queries += [q for s in split_spec.get("graph", ()) for q in graph_queries(s, seed, db)]
                queries += [q for s in split_spec.get("belief", ()) for q in belief_queries(s, seed, db)]
                answerer = make_answerer()
                for q in queries:
                    got = answerer.answer(q["query"]) or {}
                    value = got.get("value")
                    rows.append({"kind": q["kind"], "rung": q["query"].get("rung") or (
                        "action" if q["query"].get("action") else f"horizon {q['query'].get('horizon')}"
                        if q["kind"] == "graph" else "what-if"), "answerable": q["answerable"], "truth": q["truth"],
                        "value": None if value is None else float(value),
                        "sd": None if got.get("sd") is None else float(got["sd"]), "scale": q["scale"],
                        **({"identified_by": q["identified_by"]} if "identified_by" in q else {})})
        per_seed.append(scoring.answer_scores(rows))
        per_seed_group.append({g: scoring.answer_scores([r for r in rows if group(r) == g]) for g in GROUPS})
        for r in rows:
            by_kind.setdefault(f"{r['kind']}:{r['rung']}", []).append(r)
    summary = {k: scoring.spread([s[k] for s in per_seed]) for k in SUMMARY}
    by_group = {g: {k: scoring.spread([s[g][k] for s in per_seed_group]) for k in GROUP_SUMMARY} for g in GROUPS}
    for g in GROUPS:
        by_group[g]["questions_per_seed"] = per_seed_group[0][g]["queries"] if per_seed_group else 0
    return {"seeds": list(seeds), "tolerance": scoring.TOLERANCE, "strict_tolerance": scoring.STRICT_TOLERANCE,
            "interval": "central 95 % normal interval", "summary": summary, "summary_is": POOLED,
            "summary_by_group": by_group, "per_seed": per_seed,
            "by_query_kind": {k: scoring.answer_scores(v) for k, v in sorted(by_kind.items())}}


class TodayAnswerer:
    """Baseline: the active graph's one-step prediction without an action; everything else 'cannot answer'."""

    def answer(self, query):
        if "context" not in query or query.get("horizon") != 1 or query.get("action") is not None:
            return {"value": None, "sd": None}
        active = [g for g in graphs.listing(query["db"], query["context"]) if g["status"] == "active"]
        if not active:
            return {"value": None, "sd": None}
        shown = graphs.show(query["db"], active[-1]["id"])
        matrix = np.array(shown["matrix"])
        value = float((np.asarray(query["state"]) @ matrix.T)[query["population"]])
        mse = (shown.get("metrics") or {}).get("validation_mse")
        return {"value": value, "sd": math.sqrt(mse) if mse is not None else None}


class ZeroAnswerer:
    """Reference only (the Wave 1 review's trivial mechanism): 0 with sd 1e6 for everything, declining only the
    confounded questions. It scored coverage, interval coverage and abstention 1.0 under the old scores; its
    answered_within_tolerance per group is the floor a real answerer has to clear (a long-horizon forecast decays
    towards 0, so 0 is near the truth there)."""

    def answer(self, query):
        if "hidden_common_cause" in query:
            return {"value": None, "sd": None}
        return {"value": 0.0, "sd": 1e6}


def run(split_spec: dict, make_answerer, *, seeds=None) -> dict:
    return evaluate(split_spec, make_answerer, seeds=seeds)
