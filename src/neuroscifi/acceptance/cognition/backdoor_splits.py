"""Frozen back-door worlds of Wave 3 lane C's follow-up (ADR-059: what estimated moments cost the causal engine's
EXISTING back-door answers), written and committed alone, before the sampling-uncertainty code and before the harness
reads them.

No setting may be chosen on HELD_OUT. The withheld family is not used, read or imported here; no other worlds file is
changed. Every random stream is keyed by "backdoor", the split, the kind and the seed; the seeds are outside every
other split (splits.py, the wide seeds 5001-5012, knowledge_splits.py, integrated_splits.py, cycles_splits.py,
frontdoor_splits.py 7301-7320 and 7401-7440, the adversarial front-door seeds 7991-7995).

A world is a linear-Gaussian structural causal model (noise sd 1, intercepts uniform in [-1, 1], coefficients a random
sign times a uniform magnitude in the split's range) in which NO hidden cause touches the cause X, so back-door
adjustment for X's observed parents identifies E[Y | do(X = x)]. The question is the engine's graph question with the
key the back-door path reads ("means"): the observed graph, "hidden_common_cause" (observed nodes a hidden U touches;
never X), "means", "observational_covariance", "do" {X: x}, "ask" Y. The truth is computed from the structural
equations alone.

  direct        X -> Y
  confounder    Z -> X, Z -> Y, X -> Y
  two_parents   Z1 -> X, Z2 -> X, Z1 -> Y, Z2 -> Y, X -> Y
  mediated      Z -> X, Z -> Y, X -> M -> Y, hidden U -> M and U -> Y (X untouched)

Scored as frontdoor_splits.py scores answers (TOLERANCE, STRICT_TOLERANCE, EXACT there), with the moments exact and
estimated from 100, 500, 5,000 and 50,000 draws.
"""
from __future__ import annotations

import math
import random

import numpy as np

from .frontdoor_splits import _moments

KINDS = ("direct", "confounder", "two_parents", "mediated")
BACK_DOOR = {
    "development": {"seeds": tuple(range(7501, 7521)), "coefficients": (0.5, 1.5),
                    "samples": (100, 500, 5000, 50000), "kinds": KINDS},
    "held_out": {"seeds": tuple(range(7601, 7641)), "coefficients": (0.3, 2.0),
                 "samples": (100, 500, 5000, 50000), "kinds": KINDS},
}

_STRUCTURES = {
    "direct": (("X", "Y"), {"X": [], "Y": ["X"]}),
    "confounder": (("Z", "X", "Y"), {"Z": [], "X": ["Z"], "Y": ["Z", "X"]}),
    "two_parents": (("Z1", "Z2", "X", "Y"), {"Z1": [], "Z2": [], "X": ["Z1", "Z2"], "Y": ["Z1", "Z2", "X"]}),
    "mediated": (("Z", "X", "M", "Y"), {"Z": [], "X": ["Z"], "M": ["X", "U"], "Y": ["Z", "M", "U"]}),
}


def generate(kind: str, seed: int, split: str = "development") -> dict:
    """{"kind", "seed", "split", "scm" (U included), "query" (exact moments), "truth", "scale" (sd of Y), "x"}."""
    spec = BACK_DOOR[split]
    rng = random.Random(f"backdoor:{split}:{kind}:{seed}")
    observed, parents = _STRUCTURES[kind]
    lo, hi = spec["coefficients"]
    order = ["U", *observed]
    scm = {"order": order,
           "edges": {v: {p: rng.choice((-1, 1)) * rng.uniform(lo, hi) for p in parents.get(v, [])} for v in order},
           "intercept": {v: rng.uniform(-1, 1) for v in order}, "noise_sd": 1.0}
    x = round(rng.uniform(-2, 2), 3)
    mean, load, idx = _moments(scm)
    cov = load @ load.T
    query = {"graph": {v: [p for p in parents.get(v, []) if p != "U"] for v in observed},
             "hidden_common_cause": sorted(v for v in observed if "U" in parents.get(v, [])),
             "means": {v: float(mean[idx[v]]) for v in observed},
             "observational_covariance": {a: {b: float(cov[idx[a], idx[b]]) for b in observed} for a in observed},
             "do": {"X": x}, "ask": "Y"}
    return {"kind": kind, "seed": seed, "split": split, "scm": scm, "query": query,
            "truth": float(_moments(scm, {"X": x})[0][idx["Y"]]), "scale": math.sqrt(cov[idx["Y"], idx["Y"]]),
            "x": x}


def sampled(world: dict, samples: int) -> dict:
    """The world's question with "means" and the covariance estimated from `samples` draws (U drawn and dropped)."""
    key = f"backdoor-sample:{world['split']}:{world['kind']}:{world['seed']}:{samples}"
    rng = np.random.default_rng(int.from_bytes(key.encode(), "big") % (1 << 63))
    mean, load, idx = _moments(world["scm"])
    draws = mean[:, None] + load @ rng.standard_normal((len(idx), samples))
    data = list(world["query"]["means"])
    rows = np.array([draws[idx[v]] for v in data])
    cov = np.cov(rows)
    return world["query"] | {
        "means": {v: float(rows[i].mean()) for i, v in enumerate(data)},
        "observational_covariance": {a: {b: float(cov[i, j]) for j, b in enumerate(data)} for i, a in enumerate(data)}}
