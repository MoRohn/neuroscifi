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

A question that gives no equations, only a graph, the observational means and covariance and the nodes a hidden
common cause touches, is answered by back-door adjustment when no hidden common cause touches the cause. When one
does, it is answered by front-door adjustment (Pearl 1995; ADR-059, Wave 3 lane C) only when the criterion holds
through the cause's observed children on the way to the outcome, and declined with the reason otherwise; with
front_door=False (`nsf imagine scm --no-front-door`) it is declined as before. No other identification formula (no
general do-calculus, no instrumental variables) is implemented, so a decline means "not identified by this engine".
Every answer says which criterion identified it (identified_by). The engine computes; it never proposes a model.
Answers are true of the declared model only (provenance.SIMULATION).
"""
from __future__ import annotations

import numpy as np

from . import provenance

VERSION = "2"               # 2: front-door adjustment (ADR-059)
MAX_NODES = 64
RESOLUTION = 1e-9           # relative floor of a reported standard deviation (floating-point arithmetic)
FRONT_DOOR = True           # the default of answer(front_door=...) and of `nsf imagine scm` (ADR-059)
_HIDDEN = ("hidden common cause",)    # the hidden node in a graph whose nodes are JSON strings: it cannot collide


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
    if not isinstance(graph, dict) or not all(isinstance(v, list) for v in graph.values()):
        raise ValueError("a graph maps each node to the list of its parents")
    if not isinstance(query["do"], dict) or len(query["do"]) != 1:
        raise ValueError("do names exactly one node and its value")
    (cause, value), = query["do"].items()
    ask = query["ask"]
    # review1 CL10: a string here was read as a set of its characters, a confident wrong answer
    hidden_list = query.get("hidden_common_cause") or []
    if not isinstance(hidden_list, list) or not all(isinstance(n, str) for n in hidden_list):
        raise ValueError("hidden_common_cause is a list of node names")
    nodes = set(graph) | {p for parents in graph.values() for p in parents}
    unknown = sorted({cause, ask, *hidden_list} - nodes)
    if unknown:
        raise ValueError(f"not a node of the graph: {', '.join(map(str, unknown))}")
    hidden = set(hidden_list)
    if cause in hidden:
        raise NotIdentified(f"a hidden common cause acts on {cause}: no observed set blocks that back-door path, so "
                            f"the effect of do({cause}) on {ask} is not identified by back-door adjustment")
    means = query.get("means")
    if not means:
        raise NotIdentified("only a covariance was given: the effect's slope is identified but not the expected "
                            "value without the means")
    adjust = [p for p in graph.get(cause, []) if p != cause]
    names = [cause, *adjust]
    try:
        sxx = np.array([[float(cov[a][b]) for b in names] for a in names])
        sxy = np.array([float(cov[a][ask]) for a in names])
        centre = float(means[ask]), float(means[cause])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"the covariance and the means must give every node the adjustment uses ({error})") from None
    beta = np.linalg.solve(sxx, sxy)
    out = centre[0] + beta[0] * (float(value) - centre[1])
    return float(out), None


def _ancestors(parents, nodes):
    """The nodes and every ancestor of them."""
    out, stack = set(nodes), list(nodes)
    while stack:
        for p in parents.get(stack.pop(), ()):
            if p not in out:
                out.add(p)
                stack.append(p)
    return out


def _d_separated(parents, a, b, given):
    """Whether every path between the sets a and b is blocked by `given` in the DAG {node: parents}: a and b are
    separated in the moral graph of the ancestors of a, b and given once `given` is removed (Lauritzen et al. 1990)."""
    keep = _ancestors(parents, a | b | given)
    near = {v: set() for v in keep}
    for v in keep:
        ps = list(parents.get(v, ()))
        for i, p in enumerate(ps):
            near[v].add(p)
            near[p].add(v)
            for q in ps[i + 1:]:
                near[p].add(q)
                near[q].add(p)
    seen, stack = set(a), list(a)
    while stack:
        for w in near[stack.pop()]:
            if w in given or w in seen:
                continue
            if w in b:
                return False
            seen.add(w)
            stack.append(w)
    return True


def _without_edges_out_of(parents, nodes):
    return {v: [p for p in ps if p not in nodes] for v, ps in parents.items()}


def _frontdoor(query):
    """Front-door adjustment (Pearl 1995) for a graph question whose cause a hidden common cause touches. The mediators
    M are the cause's children that are ancestors of the outcome, so they intercept every directed path from the
    cause X to the outcome Y unless X -> Y is an edge. The criterion's other two conditions are checked by
    d-separation in the graph with one hidden node acting on every node named in hidden_common_cause (several named
    hidden causes read as one shared cause can only add paths, so only add declines): X has no unblocked back-door
    path to M (X and M separated once the edges out of X are removed), and X blocks every back-door path from M to Y
    (M and Y separated given X once the edges out of M are removed). Linear: E[Y | do(X = x)] = E[Y] + b(Y ~ M | X) .
    b(M ~ X) (x - E[X]). Returns (value, None, mediators): a point value, as for back-door adjustment."""
    graph = query["graph"]
    (cause, value), = query["do"].items()
    ask = query["ask"]
    parents = {v: list(ps) for v, ps in graph.items()}
    for v in {p for ps in graph.values() for p in ps}:
        parents.setdefault(v, [])
    if len(parents) > MAX_NODES:
        raise NotIdentified(f"the graph has more than {MAX_NODES} nodes, the most front-door adjustment examines")
    if ask == cause:
        raise NotIdentified("the cause and the outcome are the same node")
    indegree = {v: len(set(ps)) for v, ps in parents.items()}
    ready = [v for v, d in indegree.items() if d == 0]
    done = 0
    children = {v: set() for v in parents}
    for v, ps in parents.items():
        for p in set(ps):
            children[p].add(v)
    while ready:
        v = ready.pop()
        done += 1
        for c in children[v]:
            indegree[c] -= 1
            if not indegree[c]:
                ready.append(c)
    if done != len(parents):
        raise NotIdentified("the declared graph has a cycle")
    if cause in parents[ask]:
        raise NotIdentified(f"the edge {cause} -> {ask} bypasses every mediator")
    towards = _ancestors(parents, {ask})
    mediators = sorted(c for c in children[cause] if c in towards)
    if not mediators:
        raise NotIdentified(f"the declared graph has no directed path from {cause} to {ask} through an observed node")
    cov = query.get("observational_covariance") or {}
    means, alias = query.get("means"), query.get("observational_mean")
    if means is not None and alias is not None and means != alias:
        raise ValueError("means and observational_mean differ: give one")
    means = means if means is not None else alias
    if not isinstance(cov, dict) or (means is not None and not isinstance(means, dict)):
        raise ValueError("the covariance and the means are JSON objects keyed by node")
    unobserved = [m for m in mediators if m not in cov or not means or m not in means]
    if unobserved:
        raise NotIdentified(f"the mediator(s) {', '.join(unobserved)} on the way from {cause} to {ask} are not "
                            "observed (no means and covariance given for them)")
    full = {v: list(ps) for v, ps in parents.items()}
    full[_HIDDEN] = []
    for v in set(query.get("hidden_common_cause") or []):
        full[v].append(_HIDDEN)
    m = set(mediators)
    if not _d_separated(_without_edges_out_of(full, {cause}), {cause}, m, set()):
        raise NotIdentified(f"{cause} has an unblocked back-door path to the mediator(s) {', '.join(mediators)}")
    if not _d_separated(_without_edges_out_of(full, m), m, {ask}, {cause}):
        raise NotIdentified(f"a back-door path from the mediator(s) {', '.join(mediators)} to {ask} is not blocked "
                            f"by {cause}")
    names = [*mediators, cause]
    try:
        sxx = np.array([[float(cov[a][b]) for b in names] for a in names])
        sxy = np.array([float(cov[a][ask]) for a in names])
        centre = float(means[ask]), float(means[cause])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"the covariance and the means must give every node the adjustment uses ({error})") from None
    if not np.all(np.isfinite(sxx)) or not np.all(np.isfinite(sxy)) or not np.all(np.isfinite(centre)) \
            or not np.allclose(sxx, sxx.T, rtol=1e-9, atol=1e-12):
        raise ValueError("the covariance of the mediators and the cause must be finite and symmetric")
    eigen = np.linalg.eigvalsh(sxx)
    if eigen[0] <= 1e-10 * max(eigen[-1], 0.0) or eigen[-1] <= 0:
        raise NotIdentified(f"the covariance of {', '.join(names)} is not positive definite (one is an exact linear "
                            "function of the others), so the adjustment is not defined")
    beta = np.linalg.solve(sxx, sxy)[:len(mediators)]
    slope = sxx[:len(mediators), -1] / sxx[-1, -1]
    return float(centre[0] + beta @ slope * (float(value) - centre[1])), None, mediators


def answer(query, *, front_door: bool = FRONT_DOOR):
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
                           "identified_by": "declared_model", "assumptions": assumptions,
                           "uncertainty": "exact for the declared model: sd is the spread of the answer under its "
                                          "noise (0 when the unit's noise is fully known)"}
        if "graph" in query and "do" in query:
            try:
                value, sd = _backdoor(query)
            except NotIdentified as back:
                cause = next(iter(query["do"]))          # _backdoor checked do, the graph and the hidden list
                if cause not in set(query.get("hidden_common_cause") or []):
                    raise
                if not front_door:
                    raise NotIdentified(f"{back}; front-door adjustment is switched off here (front_door=False, "
                                        "`--no-front-door`), and no other identification formula is "
                                        "implemented") from None
                try:
                    value, sd, mediators = _frontdoor(query)
                except NotIdentified as front:
                    raise NotIdentified(f"{back}; nor by front-door adjustment: {front} (no other identification "
                                        "formula is implemented)") from None
                return base | {
                    "rung": "interventional", "value": value, "sd": sd, "identified": True, "why": None,
                    "identified_by": "front_door", "mediators": mediators,
                    "assumptions": [
                        "the declared graph is complete apart from the named hidden common causes, read as one hidden "
                        "cause acting on every node named",
                        f"linear relations; front-door adjustment through {', '.join(mediators)}: they intercept "
                        f"every directed path from {cause} to {query['ask']}, {cause} has no unblocked back-door "
                        f"path to them, and {cause} blocks every back-door path from them to {query['ask']}",
                        "the given means and covariance are the population's (taken as exact)"],
                    "uncertainty": "a point value: no spread is computed from a covariance alone; means and a "
                                   "covariance estimated from data carry an error this value does not show (ADR-059: "
                                   "from 500 draws, 20 of 200 held-out test answers missed the truth by more than a "
                                   "tenth of the outcome's sd; from 5,000, none)"}
            return base | {"rung": "interventional", "value": value, "sd": sd, "identified": True, "why": None,
                           "identified_by": "back_door",
                           "assumptions": ["the declared graph is complete apart from the named hidden common causes",
                                           "linear relations; back-door adjustment for the cause's observed parents"],
                           "uncertainty": "a point value: no spread is computed from a covariance alone"}
        raise ValueError("a question gives a model (with given, do, or observed and had) or a graph with do")
    except NotIdentified as why:
        return base | {"rung": query.get("rung") or "interventional", "value": None, "sd": None,
                       "identified": False, "identified_by": None, "why": str(why), "assumptions": [],
                       "uncertainty": "not answered"}
