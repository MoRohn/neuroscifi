"""Windowed sparse graph learner and validation-selected NSG1 packing.

`fit` is the learner loop of `lab.graph_bench.simulate` for one stream, generalised from 24 populations
to N. It trains, in the same pass, a dense matrix and a sparse matrix whose per-receiver support of k
inputs is rewired by a scored swap test. `pack` is the selection rule of
`lab.graph_compression_bench`: the smallest complete NSG1 file whose validation error stays within
the scoring rule's tolerance of the dense FP32 graph.
"""
import numpy as np

from ..graph_store import decode, encode
from .errors import ValidationError

WINDOW = 32
LEARNING_RATE = .28
RIDGE = .3
SCORE_KEEP, SCORE_NEW = .85, .15
SWAP_EVERY = 4            # windows between support swap checks
SWAP_RATIO, SWAP_MARGIN = 1.15, .035


def _layout(n, k, rng):
    """Initial support: k distinct non-self inputs per receiver (graph_bench.layout for any N)."""
    mask = np.zeros((n, n), bool)
    if k == 0:
        return mask
    for j in range(n):
        mask[j, rng.choice(np.delete(np.arange(n), j), k, replace=False)] = True
    return mask


def _learn(states, targets, use):
    """Run the windowed learner from an initial support mask. `use` is updated in place."""
    n = states.shape[1]
    dense = np.zeros((n, n))
    growing = dense.copy()
    score = dense.copy()
    swaps = 0
    for left in range(0, len(states), WINDOW):
        xx = states[left:left + WINDOW]
        yy = targets[left:left + WINDOW]
        denom = (xx * xx).sum(axis=0) + RIDGE
        for mat, mask in ((dense, None), (growing, use)):
            residual = yy - xx @ mat.T
            grad = (residual.T @ xx) / denom
            np.fill_diagonal(grad, 0)
            mat += LEARNING_RATE * grad if mask is None else LEARNING_RATE * grad * mask
            if mat is growing:
                score = SCORE_KEEP * score + SCORE_NEW * grad
        if (left // WINDOW + 1) % SWAP_EVERY == 0:
            for j in range(n):
                inside = np.where(use[j])[0]
                outside = np.where((~use[j]) & (np.arange(n) != j))[0]
                if not len(inside) or not len(outside):
                    continue
                drop = inside[np.argmin(abs(growing[j, inside]))]
                add = outside[np.argmax(abs(score[j, outside]))]
                if abs(score[j, add]) > SWAP_RATIO * abs(growing[j, drop]) + SWAP_MARGIN:
                    growing[j, drop] = 0
                    use[j, drop] = False
                    use[j, add] = True
                    growing[j, add] = 0
                    swaps += 1
    return {"dense": dense, "sparse": growing, "support": use, "swaps": swaps}


def fit(states, targets, *, k=3, seed=0):
    """Learn dense and k-sparse graphs from one stream. The initial support is drawn from `seed`."""
    states = np.asarray(states, dtype=np.float64)
    targets = np.asarray(targets, dtype=np.float64)
    if states.ndim != 2 or states.shape != targets.shape or not len(states):
        raise ValidationError("fit needs states and targets of the same shape [T, N]")
    n = states.shape[1]
    if isinstance(k, bool) or not isinstance(k, int) or k < 0:
        raise ValidationError("k must be a non-negative integer")
    use = _layout(n, min(k, n - 1), np.random.default_rng(seed))
    return _learn(states, targets, use)


def mse(matrix, states, targets):
    return float(np.mean((states @ np.asarray(matrix).T - targets) ** 2))


def pack(dense, validation, rule):
    """Pick the smallest NSG1 file within (1 + validation_tolerance) of the dense validation MSE.

    `validation` is (states, targets). Candidates are FP32 plus every (degree, bits) pair from
    rule["pack_candidates"] with degree <= N. FP32 always qualifies, so a choice always exists.
    """
    states, targets = validation
    dense = np.asarray(dense).astype("f4")
    baseline = mse(dense, states, targets)
    cols = dense.shape[1]
    tolerance = float(rule["validation_tolerance"])
    options = [(cols, 32)] + [(degree, bits) for degree in rule["pack_candidates"]["degrees"]
                              for bits in rule["pack_candidates"]["bits"] if degree <= cols]
    candidates = []
    for degree, bits in options:
        blob = encode(dense, degree, bits)
        loss = mse(decode(blob), states, targets)
        candidates.append({"bits": bits, "degree": degree, "byte_size": len(blob), "validation_mse": loss,
                           "eligible": loss <= (1 + tolerance) * baseline, "blob": blob})
    chosen = min((c for c in candidates if c["eligible"]), key=lambda c: c["byte_size"])
    return {"blob": chosen["blob"], "bits": chosen["bits"], "degree": chosen["degree"],
            "byte_size": chosen["byte_size"], "validation_mse": chosen["validation_mse"],
            "dense_validation_mse": baseline,
            "candidates": [{key: c[key] for key in ("bits", "degree", "byte_size", "validation_mse", "eligible")}
                           for c in candidates]}
