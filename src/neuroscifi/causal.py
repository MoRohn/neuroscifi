"""Linear-Gaussian structural causal models with a deterministic engine (Cognitive Genesis lane M, ADR-052):
Pearl's three rungs computed exactly from a model someone declared, or declined when the question is not identified.

A model is {"order": [node, ...] (causal order), "edges": {child: {parent: coefficient}}, "intercept": {node: value},
"noise_sd": s}: node v = intercept_v + sum(coefficient * parent) + noise_v, the noises independent N(0, s^2). Every
node is then intercept-plus-linear in the noise vector, so every answer is a Gaussian computed in closed form:

  observational   E[Y | given] (rung 1): Gaussian conditioning on the given values.
  interventional  E[Y | do(X = x)] (rung 2): the same model with X's equation replaced by the constant x.
  counterfactual  "Y had X been x'" given the observed values of this unit (rung 3): abduction (the noise posterior
                  given what was observed), action (X's equation replaced), prediction (Y under the posterior noise).
                  With every node observed the noise is known exactly and the answer has no spread.

A question that gives no equations, only a graph, an observational covariance and a hidden common cause, is
answered only by back-door adjustment, and only when no hidden common cause touches the cause; otherwise it is
declined as not identified by this engine (front-door and other identification formulas are not implemented). The
engine computes; it never proposes a model. Answers are true of the declared model only (provenance.SIMULATION).
"""
from __future__ import annotations

import numpy as np

from . import provenance

VERSION = "1"
MAX_NODES = 64
RESOLUTION = 1e-9           # relative floor of a reported standard deviation (floating-point arithmetic)


class NotIdentified(Exception):
    """The question cannot be answered from what was declared."""


def _check(model):
    if not isinstance(model, dict):
        raise ValueError("a model is a JSON object with order, edges, intercept and noise_sd")
    order = model.get("order")
    if not isinstance(order, list) or not order or len(order) > MAX_NODES or len(set(order)) != len(order) \
            or not all(isinstance(v, str) for v in order):
        raise ValueError(f"order must list 1 to {MAX_NODES} distinct node names")
    edges, intercept = model.get("edges") or {}, model.get("intercept") or {}
    seen = set()
    for v in order:
        for p, c in (edges.get(v) or {}).items():
            if p not in seen:
                raise ValueError(f"edge {p} -> {v} does not follow the causal order")
            if isinstance(c, bool) or not isinstance(c, (int, float)) or not np.isfinite(c):
                raise ValueError(f"the coefficient of {p} -> {v} must be a finite number")
        seen.add(v)
    sd = model.get("noise_sd", 1.0)
    if isinstance(sd, bool) or not isinstance(sd, (int, float)) or not np.isfinite(sd) or sd <= 0:
        raise ValueError("noise_sd must be a positive number")
    return order, edges, intercept, float(sd)


def _linear(model, do=None):
    """(mean vector, loading matrix L[node, noise]) of every node under `do`: node = mean + L @ noise."""
    order, edges, intercept, _ = _check(model)
    do = do or {}
    idx = {v: i for i, v in enumerate(order)}
    n = len(order)
    mean, load = np.zeros(n), np.zeros((n, n))
    for v in order:
        i = idx[v]
        if v in do:
            mean[i] = float(do[v])
            continue
        mean[i] = float(intercept.get(v, 0.0))
        load[i, i] = 1.0
        for p, c in (edges.get(v) or {}).items():
            mean[i] += c * mean[idx[p]]
            load[i] += c * load[idx[p]]
    return mean, load, idx


def _names(model, names, what):
    order = _check(model)[0]
    for v in names:
        if v not in order:
            raise ValueError(f"{what} names {v!r}, which is not a node of the model")


def observational(model, given, ask):
    _names(model, [*given, ask], "the question")
    mean, load, idx = _linear(model)
    s2 = _check(model)[3] ** 2
    cov = s2 * load @ load.T
    o = [idx[v] for v in given]
    y = idx[ask]
    if not o:
        return float(mean[y]), float(np.sqrt(cov[y, y]))
    k = cov[np.ix_([y], o)] @ np.linalg.pinv(cov[np.ix_(o, o)])
    value = mean[y] + (k @ (np.array([given[v] for v in given]) - mean[o]))[0]
    var = cov[y, y] - (k @ cov[np.ix_(o, [y])])[0, 0]
    return float(value), _spread(var, value)


def interventional(model, do, ask):
    _names(model, [*do, ask], "the question")
    mean, load, idx = _linear(model, do)
    s2 = _check(model)[3] ** 2
    y = idx[ask]
    return float(mean[y]), _spread(s2 * load[y] @ load[y], mean[y])


def counterfactual(model, observed, had, ask):
    _names(model, [*observed, *had, ask], "the question")
    mean, load, idx = _linear(model)
    s2 = _check(model)[3] ** 2
    o = [idx[v] for v in observed]
    n = len(model["order"])
    if o:
        lo = load[o]
        cov_oo = s2 * lo @ lo.T
        gain = s2 * lo.T @ np.linalg.pinv(cov_oo)
        noise_mean = gain @ (np.array([observed[v] for v in observed]) - mean[o])
        noise_cov = s2 * np.eye(n) - gain @ lo * s2
    else:
        noise_mean, noise_cov = np.zeros(n), s2 * np.eye(n)
    cf_mean, cf_load, _ = _linear(model, had)
    y = idx[ask]
    value = cf_mean[y] + cf_load[y] @ noise_mean
    var = cf_load[y] @ noise_cov @ cf_load[y]
    return float(value), _spread(var, value)


def _spread(var, value):
    """The standard deviation, never below the arithmetic's own resolution (a fully known unit has no spread, but its
    answer is computed in floating point)."""
    return max(float(np.sqrt(max(var, 0.0))), RESOLUTION * (1.0 + abs(float(value))))


def _backdoor(query):
    """Back-door adjustment from a graph and an observational covariance: the slope of the outcome on the cause,
    adjusting for the cause's observed parents. Declined when a hidden common cause touches the cause (no observed
    set blocks that back-door path) and when the means needed for a value are not given."""
    graph, cov = query.get("graph") or {}, query.get("observational_covariance") or {}
    (cause, value), = query["do"].items()
    ask = query["ask"]
    hidden = set(query.get("hidden_common_cause") or ())
    if cause in hidden:
        raise NotIdentified(f"a hidden common cause acts on {cause}: no observed set blocks that back-door path, so "
                            f"the effect of do({cause}) on {ask} is not identified by back-door adjustment (front-door "
                            "and other identification formulas are not implemented here)")
    means = query.get("means")
    if not means:
        raise NotIdentified("only a covariance was given: the effect's slope is identified but not the expected "
                            "value without the means")
    adjust = [p for p in graph.get(cause, []) if p != cause]
    names = [cause, *adjust]
    sxx = np.array([[cov[a][b] for b in names] for a in names])
    sxy = np.array([cov[a][ask] for a in names])
    beta = np.linalg.solve(sxx, sxy)
    out = means[ask] + beta[0] * (value - means[cause])
    return float(out), None


def answer(query):
    """Answer one question: {"rung", "value", "sd", "identified", "why", "assumptions", "label", ...}. A query names
    its rung ('observational' with "given", 'interventional' with "do", 'counterfactual' with "observed" and "had";
    or a graph with "observational_covariance" and "hidden_common_cause"), the model or graph, and "ask" (the node).
    ValueError for a malformed question; a question that is not identified is answered with value None and why."""
    if not isinstance(query, dict) or "ask" not in query:
        raise ValueError("a question names the node it asks about ('ask')")
    base = {"label": provenance.NOT_EVIDENCE, "kind": provenance.SIMULATION, "mechanism": "causal.linear_gaussian",
            "mechanism_version": VERSION, "ask": query["ask"]}
    try:
        if "model" in query:
            model = query["model"]
            assumptions = ["the declared structural equations are the true mechanism: linear, with independent "
                           f"Gaussian noise of standard deviation {model.get('noise_sd', 1.0)}, and no hidden cause "
                           "outside the declared nodes"]
            if "had" in query:
                rung = "counterfactual"
                value, sd = counterfactual(model, query.get("observed") or {}, query["had"], query["ask"])
                assumptions.append("the unit's noise is inferred from its observed values (abduction) and kept under "
                                   f"the change {query['had']}")
            elif "do" in query:
                rung = "interventional"
                value, sd = interventional(model, query["do"], query["ask"])
                assumptions.append(f"the intervention {query['do']} replaces those equations and changes nothing "
                                   "else")
            else:
                rung = "observational"
                value, sd = observational(model, query.get("given") or {}, query["ask"])
            return base | {"rung": rung, "value": value, "sd": sd, "identified": True, "why": None,
                           "assumptions": assumptions,
                           "uncertainty": "exact for the declared model: sd is the spread of the answer under its "
                                          "noise (0 when the unit's noise is fully known)"}
        if "graph" in query and "do" in query:
            value, sd = _backdoor(query)
            return base | {"rung": "interventional", "value": value, "sd": sd, "identified": True, "why": None,
                           "assumptions": ["the declared graph is complete apart from the named hidden common causes",
                                           "linear relations; back-door adjustment for the cause's observed parents"],
                           "uncertainty": "a point value: no spread is computed from a covariance alone"}
        raise ValueError("a question gives a model (with given, do, or observed and had) or a graph with do")
    except NotIdentified as why:
        return base | {"rung": query.get("rung") or "interventional", "value": None, "sd": None,
                       "identified": False, "why": str(why), "assumptions": [], "uncertainty": "not answered"}
