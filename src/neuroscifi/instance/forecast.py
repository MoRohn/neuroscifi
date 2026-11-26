"""Forecasts from an active graph (Cognitive Genesis lane M, ADR-052): several steps ahead with propagated
uncertainty, and the consequence of pushing one population, only where the recorded trajectories identify it.

A graph predicts each step's target from that step's state: E[y_t | x_t] = A x_t (datasets.py, learner.py). To go
further than one step a forecast needs how a state carries over to the next one. That is learned from the graph's own
training trajectory (rows are consecutive time steps), per population: x_{t+1,i} = c_i x_{t,i} + g_i y_{t,i} + r_{t,i},
so the next state is M x_t + e_t with M = diag(c) + diag(g) A. Then

    E[y_{t+k-1} | x_t] = A M^(k-1) x_t,   Var = A P_(k-1) A' + R,   P_0 = 0,  P_j = M P_(j-1) M' + Q

with Q the covariance of e_t on the training trajectory and R each population's error of A on the validation
trajectory. Horizon k = 1 is the graph's own one-step prediction (recall.py). The forecast is refused beyond
MAX_HORIZON, and beyond one step when the carry-over grows (spectral radius of M >= 1: the error has no bound).
Parameters are taken as estimated: the interval covers the process noise, not the estimation error of A, c and g.

A push of u on population i at the next step adds A M^(k-2) u e_i at horizon k >= 2. That is the effect of an
intervention only if a push acts like the inputs the recorded trajectory already holds (r: what moved a population
beyond the graph and its own carry-over). action_support() checks what the data can check: such inputs exist
(a share of the state's variance), they do not follow the past state or themselves (no feedback, so a push from
outside is like them), and the carry-over model holds on the validation trajectory. Otherwise the push is declined
with the reason, never guessed. The belief model has no such data (imagine.py): an action there is only the declared
inversion of a reading.

Everything here is read-only computation on stored graphs and datasets; nothing is written. A forecast is imagined,
not evidence (provenance.SIMULATION).
"""
import numpy as np

from .. import provenance
from . import datasets, graphs, store
from .errors import NotFound, ValidationError
from .imagine import Refused

VERSION = "1"
MAX_HORIZON = 12
INPUT_SHARE = 0.05          # the least share of a population's next-state variance the recorded inputs must explain
FEEDBACK_SE = 4.0           # |correlation| above this many standard errors (1/sqrt(T)) = the inputs follow the past
HOLDS_RATIO = 1.5           # validation one-step state error at most this many times the training one
MIN_ROWS_PER_POPULATION = 3  # training rows per population below which no carry-over is learned


def _require(db):
    if not store.config(db).imagine_forecast:
        raise Refused("imagine_forecast is off (`nsf config --imagine-forecast` turns it on)")


def _fit_step(states, targets):
    """Per population, least squares of x_{t+1,i} on (x_{t,i}, y_{t,i}) over consecutive rows: (c, g, residuals)."""
    x0, y0, x1 = states[:-1], targets[:-1], states[1:]
    n = states.shape[1]
    c, g = np.zeros(n), np.zeros(n)
    for i in range(n):
        design = np.column_stack([x0[:, i], y0[:, i]])
        (c[i], g[i]), *_ = np.linalg.lstsq(design, x1[:, i], rcond=None)
    return c, g, x1 - x0 * c - y0 * g


def _corr(a, b):
    """Column-by-column correlations of a[T, n] with b[T, m] (0 where a column does not vary)."""
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    sa, sb = np.sqrt((a * a).sum(axis=0)), np.sqrt((b * b).sum(axis=0))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (a.T @ b) / np.outer(sa, sb)
    return np.nan_to_num(out)


def model(db, context):
    """The forecast model of `context`'s active graph: {"revision_id", "A", "M", "Q", "R", "c", "g", "rho",
    "target_var", "support"}. NotFound without an active graph; Refused when its trajectories are not recorded."""
    revision = graphs._active(db, context)
    if revision is None:
        raise NotFound(f"No active graph for context {context!r}")
    rev = graphs._row(db, revision)
    a = graphs._matrix(db, revision).astype(np.float64)
    train, validation = rev["metrics"].get("train_dataset"), rev["metrics"].get("validation_dataset")
    if train is None or validation is None:
        raise Refused(f"graph revision {revision} has no recorded training and validation trajectories (a founding or "
                      "imported graph): how a state carries over to the next step cannot be learned for it")
    states, targets = datasets.load(db, train)
    vstates, vtargets = datasets.load(db, validation)
    n = states.shape[1]
    if len(states) < MIN_ROWS_PER_POPULATION * n or len(vstates) < 3:
        # review1 CL11: the carry-over has 2 parameters per population and Q is n x n, both from rows - 1 steps
        raise Refused(f"graph revision {revision}'s trajectories are too short to learn how a state carries over "
                      f"(training {len(states)} rows, validation {len(vstates)}; {n} populations need at least "
                      f"{MIN_ROWS_PER_POPULATION * n} training rows and 3 validation rows)")
    c, g, r = _fit_step(states, targets)
    m = np.diag(c) + g[:, None] * a
    e = states[1:] - states[:-1] @ m.T
    q = e.T @ e / len(e)
    resid = vtargets - vstates @ a.T
    rho = float(np.max(np.abs(np.linalg.eigvals(m))))
    return {"revision_id": revision, "context": context, "A": a, "M": m, "Q": q, "R": np.mean(resid ** 2, axis=0),
            "c": c, "g": g, "rho": rho, "target_var": targets.var(axis=0),
            "support": _support(states, targets, vstates, vtargets, c, g, r)}


def _support(states, targets, vstates, vtargets, c, g, r):
    """What the recorded trajectories say about pushes, per population (see the module docstring)."""
    t = len(r)
    nxt = states[1:]
    share = r.var(axis=0) / np.maximum(nxt.var(axis=0), 1e-300)
    follows_state = np.max(np.abs(_corr(r, states[:-1])), axis=1)   # the input of step t against the state at t
    follows_self = np.array([abs(_corr(r[1:, [i]], r[:-1, [i]])[0, 0]) for i in range(r.shape[1])]) \
        if t > 2 else np.zeros(r.shape[1])
    limit = FEEDBACK_SE / np.sqrt(max(t, 1))
    held =(np.mean((vstates[1:] - vstates[:-1] * c - vtargets[:-1] * g) ** 2, axis=0)
            / np.maximum(np.mean(r ** 2, axis=0), 1e-300))
    return {"input_share": share, "follows_state": follows_state, "follows_self": follows_self,
            "feedback_limit": limit, "validation_ratio": held}


def action_support(mdl, population):
    """(supported, reasons) for a push on `population` in this model."""
    s = mdl["support"]
    reasons = []
    if s["input_share"][population] < INPUT_SHARE:
        reasons.append(f"the recorded trajectory has no input that moves population {population} by itself (inputs "
                       f"explain {s['input_share'][population]:.1%} of its next-state variance, below "
                       f"{INPUT_SHARE:.0%}): a push cannot be told apart from the graph's own dynamics")
    if s["follows_state"][population] > s["feedback_limit"] or s["follows_self"][population] > s["feedback_limit"]:
        reasons.append(f"the inputs to population {population} follow the past (correlation "
                       f"{max(s['follows_state'][population], s['follows_self'][population]):.3f} with the previous "
                       f"state or input, above {s['feedback_limit']:.3f}): they respond to the system, so a push "
                       "from outside would not act like them")
    if s["validation_ratio"][population] > HOLDS_RATIO:
        reasons.append(f"the carry-over model does not hold on the validation trajectory for population {population} "
                       f"(error {s['validation_ratio'][population]:.2f} times the training error)")
    return not reasons, reasons


def _assumptions(mdl, horizon, action):
    out = [f"the active graph of {mdl['context']!r} (revision {mdl['revision_id']}) gives each step's expected "
           "target from that step's state, as it was learned",
           "a state carries over to the next step as on the graph's training trajectory (rows are consecutive time "
           f"steps): next state = c x + g y + an input with mean zero; spectral radius of the carry-over "
           f"{mdl['rho']:.3f}",
           "the trajectories' rows are consecutive time steps of one run: assumed, not checked (a dataset records no "
           "time index)",
           "nothing else acts on the populations over the horizon beyond inputs like the recorded ones (mean zero)",
           "the parameters are taken as estimated: the interval covers the process noise, not estimation error"]
    if action is not None:
        out.append(f"a push of {action['push']} on population {action['population']} at the next step acts like the "
                   "recorded inputs (checked on the trajectory: present, not following the past, model holds out of "
                   "sample)")
    return out


def forecast(db, context, state, horizon, *, population=None, action=None):
    """Expected targets `horizon` steps ahead (horizon 1 = the graph's one-step prediction) from `state`, with their
    standard deviations, under the active graph of `context`; optionally after pushing one population at the next
    step (action = {"population": i, "push": u}), only where action_support() holds. Refused beyond MAX_HORIZON and,
    beyond one step, when the carry-over grows without bound. Returns {"mean", "sd", "skill", ...}; "skill" is
    1 - forecast variance / the target's variance on the training trajectory (0 = no better than its long-run
    spread). Read-only."""
    _require(db)
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= MAX_HORIZON:
        raise Refused(f"the horizon must be a whole number of steps from 1 to {MAX_HORIZON} (the stated limit of "
                      "these forecasts)")
    mdl = model(db, context)
    n = mdl["A"].shape[0]
    try:
        x = np.asarray(state, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValidationError("state must be a list of numbers") from None
    if x.shape != (n,) or not np.isfinite(x).all():
        raise ValidationError(f"state must hold {n} finite numbers (one per population)")
    if population is not None and (isinstance(population, bool) or not isinstance(population, int)
                                   or not 0 <= population < n):
        raise ValidationError(f"population must be an index from 0 to {n - 1}")
    if horizon > 1 and mdl["rho"] >= 1.0:
        raise Refused(f"the carry-over of {context!r} grows (spectral radius {mdl['rho']:.3f} >= 1): forecast errors "
                      "have no bound beyond one step, so only horizon 1 is answered")
    a, m, q = mdl["A"], mdl["M"], mdl["Q"]
    reach = np.linalg.matrix_power(m, horizon - 1)
    mean = a @ reach @ x
    cov_x = np.zeros((n, n))
    for _ in range(horizon - 1):
        cov_x = m @ cov_x @ m.T + q
    var = np.einsum("ij,jk,ik->i", a, cov_x, a) + mdl["R"]
    support = None
    if action is not None:
        if not isinstance(action, dict) or set(action) != {"population", "push"}:
            raise ValidationError('an action is {"population": i, "push": u}')
        i, u = action["population"], action["push"]
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < n:
            raise ValidationError(f"the pushed population must be an index from 0 to {n - 1}")
        if isinstance(u, bool) or not isinstance(u, (int, float)) or not np.isfinite(u):
            raise ValidationError("push must be a finite number")
        ok, reasons = action_support(mdl, i)
        support = {"supported": ok, "reasons": reasons}
        if not ok:
            raise Refused("the consequence of this push is not identified by the recorded data: " + "; ".join(reasons))
        if horizon >= 2:
            push = np.zeros(n)
            push[i] = u
            mean = mean + a @ np.linalg.matrix_power(m, horizon - 2) @ push
    sd = np.sqrt(np.maximum(var, 0.0))
    skill = 1.0 - var / np.maximum(mdl["target_var"], 1e-300)
    pick = (lambda v: float(v[population])) if population is not None else (lambda v: [float(z) for z in v])
    return {"label": provenance.NOT_EVIDENCE, "kind": provenance.SIMULATION, "mechanism": "forecast.rollout",
            "mechanism_version": VERSION, "context": context, "revision_id": mdl["revision_id"], "horizon": horizon,
            "population": population, "action": action, "mean": pick(mean), "sd": pick(sd), "skill": pick(skill),
            "limits": {"max_horizon": MAX_HORIZON, "spectral_radius": mdl["rho"]}, "action_support": support,
            "assumptions": _assumptions(mdl, horizon, action),
            "uncertainty": "a standard deviation per population from the propagated process noise of the carry-over "
                           "and the graph's validation error; estimation error of the parameters is not included"}
