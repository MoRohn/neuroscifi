"""Frozen property worlds of Wave 3 lane C (front-door adjustment in the linear causal engine; ADR-059), written and
committed BEFORE the mechanism, the adversarial cases and the harness that read them.

No setting of the lane may be chosen on HELD_OUT: anything chosen is chosen on DEVELOPMENT and reported on HELD_OUT.
The withheld family is not used, read or imported here; splits.py and every other worlds file are unchanged. Every
random stream is keyed by the string "frontdoor", the world's name and the seed, so no stream can coincide with
another family's even where a seed number does (the seeds are also outside splits.py's 11-13, 21-23, 31-33, 41-42 and
10xx, the wide seeds 5001-5012, knowledge_splits.py's, integrated_splits.py's 61-63 and 601-604).

A world is a linear-Gaussian structural causal model with a hidden common cause U of the cause X and the outcome Y
(noise sd 1, intercepts uniform in [-1, 1], each coefficient a random sign times a uniform magnitude in the split's
`coefficients` range). The question handed to the engine is what the counterfactual family's confounded questions
hand it: the observed graph (each observed node with its observed parents), "hidden_common_cause" (the observed nodes
a hidden cause touches), the observational means and covariance of the observed nodes (exact, from the equations, or
estimated from `samples` draws), "do" {X: x} and "ask" Y. The truth E[Y | do(X = x)] is computed from the structural
equations alone, U included (generate() below), never from anything the engine says.

Kinds (`expect`: what a sound engine must do; `otherwise`: whether the effect is identifiable by some other formula):

  answer (the plain front-door criterion holds through the observed children of X):
    single        X -> M -> Y
    parallel      X -> M1 -> Y, X -> M2 -> Y
    chain         X -> M1 -> M2 -> Y
    covariate     X -> M -> Y, and an observed W -> X, W -> Y
    extras        X -> M -> Y, an observed V -> M and an observed Z <- Y
  decline (the criterion fails; a front-door formula through M would be a confident wrong answer):
    confounded_mediator  X -> M -> Y, U also -> M                                (not identifiable)
    cause_mediator       X -> M -> Y, U -> X and U -> M only (not Y)             (not identifiable)
    direct_path          X -> M -> Y and X -> Y                                  (not identifiable)
    unobserved_mediator  X -> M -> Y, M in the graph but absent from the data    (not identifiable)
    partial_unobserved   X -> M1 -> Y, X -> M2 -> Y, M2 absent from the data      (not identifiable)
    mediator_outcome     X -> M -> Y, an observed W -> M, W -> Y                 (identifiable by a conditional
                                                                                   front door; out of scope)
    shared_parent        X -> M -> Y, an observed A -> X, A -> M                 (identifiable by a conditional
                                                                                   front door; out of scope)

Pre-registered (this commit, before the mechanism):
  - TOLERANCE 0.1 and STRICT_TOLERANCE 0.05 x the sd of Y, the counterfactual family's (scoring.py); EXACT 1e-9 x the
    sd of Y for answers from exact moments.
  - A FALSE ANSWER is any answer (value not None) to a world whose `expect` is "decline", or an answer to an
    "answer" world farther than TOLERANCE x sd(Y) from the truth. False answers are counted on their own line, apart
    from declines, for exact moments and for every sample size.
  - Verdict rule for shipping the front door on: on the property worlds with exact moments, 0 false answers and every
    "answer" world answered within EXACT on both splits; on the counterfactual family's held-out split, every
    front-door question answered within TOLERANCE and every other answer leaf-identical with the front door on and
    off; the adversarial family not regressing (its new front-door cases declined); retention unchanged. Otherwise it
    ships off. Sampled moments are reported (the error of a point answer from estimated moments, which the engine
    takes as exact) but do not decide the default.
"""
from __future__ import annotations

import math
import random

import numpy as np

TOLERANCE = 0.1
STRICT_TOLERANCE = 0.05
EXACT = 1e-9

ANSWER_KINDS = ("single", "parallel", "chain", "covariate", "extras")
DECLINE_KINDS = ("confounded_mediator", "cause_mediator", "direct_path", "unobserved_mediator", "partial_unobserved",
                 "mediator_outcome", "shared_parent")
OTHERWISE_IDENTIFIABLE = ("mediator_outcome", "shared_parent")

FRONT_DOOR = {
    "development": {"seeds": tuple(range(7301, 7321)), "coefficients": (0.5, 1.5), "samples": (500, 5000, 50000),
                    "kinds": ANSWER_KINDS + DECLINE_KINDS},
    "held_out": {"seeds": tuple(range(7401, 7441)), "coefficients": (0.3, 2.0), "samples": (500, 5000, 50000),
                 "kinds": ANSWER_KINDS + DECLINE_KINDS},
}

# kind -> (observed nodes in causal order, edges {child: [parents]} with U hidden, nodes absent from the data)
_STRUCTURES = {
    "single": (("X", "M", "Y"), {"X": ["U"], "M": ["X"], "Y": ["M", "U"]}, ()),
    "parallel": (("X", "M1", "M2", "Y"), {"X": ["U"], "M1": ["X"], "M2": ["X"], "Y": ["M1", "M2", "U"]}, ()),
    "chain": (("X", "M1", "M2", "Y"), {"X": ["U"], "M1": ["X"], "M2": ["M1"], "Y": ["M2", "U"]}, ()),
    "covariate": (("W", "X", "M", "Y"), {"W": [], "X": ["W", "U"], "M": ["X"], "Y": ["M", "W", "U"]}, ()),
    "extras": (("V", "X", "M", "Y", "Z"), {"V": [], "X": ["U"], "M": ["X", "V"], "Y": ["M", "U"], "Z": ["Y"]}, ()),
    "confounded_mediator": (("X", "M", "Y"), {"X": ["U"], "M": ["X", "U"], "Y": ["M", "U"]}, ()),
    "cause_mediator": (("X", "M", "Y"), {"X": ["U"], "M": ["X", "U"], "Y": ["M"]}, ()),
    "direct_path": (("X", "M", "Y"), {"X": ["U"], "M": ["X"], "Y": ["M", "X", "U"]}, ()),
    "unobserved_mediator": (("X", "M", "Y"), {"X": ["U"], "M": ["X"], "Y": ["M", "U"]}, ("M",)),
    "partial_unobserved": (("X", "M1", "M2", "Y"), {"X": ["U"], "M1": ["X"], "M2": ["X"], "Y": ["M1", "M2", "U"]},
                           ("M2",)),
    "mediator_outcome": (("W", "X", "M", "Y"), {"W": [], "X": ["U"], "M": ["X", "W"], "Y": ["M", "W", "U"]}, ()),
    "shared_parent": (("A", "X", "M", "Y"), {"A": [], "X": ["A", "U"], "M": ["X", "A"], "Y": ["M", "U"]}, ()),
}


def _scm(kind, rng, coefficients):
    observed, parents, _ = _STRUCTURES[kind]
    lo, hi = coefficients
    order = ["U", *observed]
    edges = {v: {p: rng.choice((-1, 1)) * rng.uniform(lo, hi) for p in parents.get(v, [])} for v in order}
    return {"order": order, "edges": edges, "intercept": {v: rng.uniform(-1, 1) for v in order}, "noise_sd": 1.0}


def _moments(scm, do=None):
    """(means, loading matrix L[node, noise]) of every node under do: node = mean + L @ noise, noise iid N(0, 1)."""
    do = do or {}
    idx = {v: i for i, v in enumerate(scm["order"])}
    n = len(idx)
    mean, load = np.zeros(n), np.zeros((n, n))
    for v in scm["order"]:
        i = idx[v]
        if v in do:
            mean[i] = do[v]
            continue
        mean[i], load[i, i] = scm["intercept"][v], 1.0
        for p, c in scm["edges"][v].items():
            mean[i] += c * mean[idx[p]]
            load[i] += c * load[idx[p]]
    return mean, load, idx


def generate(kind: str, seed: int, split: str = "development") -> dict:
    """One world: {"kind", "expect", "otherwise_identifiable", "scm" (U included), "query" (exact moments), "truth",
    "scale" (sd of Y), "x"}. Deterministic per (kind, seed, split)."""
    spec = FRONT_DOOR[split]
    rng = random.Random(f"frontdoor:{split}:{kind}:{seed}")
    scm = _scm(kind, rng, spec["coefficients"])
    x = round(rng.uniform(-2, 2), 3)
    observed, parents, absent = _STRUCTURES[kind]
    mean, load, idx = _moments(scm)
    cov = load @ load.T
    data = [v for v in observed if v not in absent]
    truth = float(_moments(scm, {"X": x})[0][idx["Y"]])
    query = {"graph": {v: [p for p in parents.get(v, []) if p != "U"] for v in observed},
             "hidden_common_cause": sorted(v for v in observed if "U" in parents.get(v, [])),
             "observational_mean": {v: float(mean[idx[v]]) for v in data},
             "observational_covariance": {a: {b: float(cov[idx[a], idx[b]]) for b in data} for a in data},
             "do": {"X": x}, "ask": "Y"}
    return {"kind": kind, "seed": seed, "split": split,
            "expect": "answer" if kind in ANSWER_KINDS else "decline",
            "otherwise_identifiable": kind in ANSWER_KINDS or kind in OTHERWISE_IDENTIFIABLE,
            "scm": scm, "query": query, "truth": truth, "scale": math.sqrt(cov[idx["Y"], idx["Y"]]), "x": x}


def sampled(world: dict, samples: int) -> dict:
    """The world's question with the means and covariance estimated from `samples` draws of the structural equations
    (U drawn and then dropped; numpy's generator keyed by the world and the sample size)."""
    scm = world["scm"]
    key = f"frontdoor-sample:{world['split']}:{world['kind']}:{world['seed']}:{samples}"
    rng = np.random.default_rng(int.from_bytes(key.encode(), "big") % (1 << 63))
    mean, load, idx = _moments(scm)
    draws = mean[:, None] + load @ rng.standard_normal((len(idx), samples))
    data = list(world["query"]["observational_mean"])
    rows = np.array([draws[idx[v]] for v in data])
    cov = np.cov(rows)
    return world["query"] | {
        "observational_mean": {v: float(rows[i].mean()) for i, v in enumerate(data)},
        "observational_covariance": {a: {b: float(cov[i, j]) for j, b in enumerate(data)} for i, a in enumerate(data)}}
