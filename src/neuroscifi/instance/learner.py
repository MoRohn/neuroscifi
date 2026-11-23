"""Windowed sparse graph learner and validation-selected NSG1 packing.

`fit` is the learner loop of `lab.graph_bench.simulate` for one stream, generalised from 24 populations
to N. It trains, in the same pass, a dense matrix and a sparse matrix whose per-receiver support of k
inputs is rewired by a scored swap test. `pack` is the selection rule of
`lab.graph_compression_bench`: the smallest complete NSG1 file whose validation error stays within
the scoring rule's tolerance of the dense FP32 graph. Under the birth rule only the dense matrix is packed
and the sparse one is measured beside it; a rule the owner adds can make the sparse matrix a candidate and
the better of the two the reference (`pack`, rules.py).

The two learners do not have the same range. The dense update takes a step from every input, each
normalised by that input alone, so the steps add up with the population count: on the lab's stream the
dense error is twice the noise floor at 96 populations and above a graph with no edges from 128, and it
grows without bound beyond. The sparse matrix moves on k inputs per receiver and stays at the noise floor
at every size a dataset may have (scripts/bench_graph_learner.py sizes).
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


def _exchange_reference(growing, score, use):
    """One edge-exchange check, receiver by receiver: the loop of `lab.graph_bench.simulate`.

    Per receiver j: the weakest edge of its support (smallest |weight|, lowest index on a tie) is exchanged for the
    non-support, non-self input with the largest |score| (lowest index on a tie) when that score exceeds
    SWAP_RATIO * |weakest weight| + SWAP_MARGIN. Updates `growing` and `use` in place; returns the swaps made.
    `_exchange` is tested against it bit for bit and hands it any support that is not k inputs per receiver.
    """
    n = len(use)
    swaps = 0
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
    return swaps


def _exchange(growing, score, use):
    """`_exchange_reference` for all receivers at once, with the same result bit for bit.

    A receiver reads and writes only its own row of `growing`, `use` and `score`, so the rows are independent.
    With k inputs per receiver (what `_layout` draws and every exchange keeps) the support is an [N, k] index array
    in index order, so argmin over it is the reference's choice, lowest index on a tie. The candidate search is
    argmax over |score| with the support and the diagonal set to -inf: |score| is never below zero, so the first
    maximum of a row is the reference's first maximum over its candidates, for any values (NaN and inf included).
    """
    n = len(use)
    per_row = use.sum(axis=1)
    k = int(per_row[0]) if n else 0
    if not (per_row == k).all() or np.diag(use).any():
        return _exchange_reference(growing, score, use)     # an uneven or self-including support: receiver by receiver
    if k == 0 or k >= n - 1:
        return 0                                            # nothing to drop, or no candidate to add
    rows = np.arange(n)
    inside = np.nonzero(use)[1].reshape(n, k)               # row-major, so each receiver's inputs in index order
    weight = np.abs(growing[rows[:, None], inside])
    at = weight.argmin(axis=1)
    drop, weakest = inside[rows, at], weight[rows, at]
    strength = np.abs(score)
    strength[rows[:, None], inside] = -np.inf
    strength[rows, rows] = -np.inf
    add = strength.argmax(axis=1)
    swap = strength[rows, add] > SWAP_RATIO * weakest + SWAP_MARGIN
    rows, drop, add = rows[swap], drop[swap], add[swap]
    growing[rows, drop] = 0
    use[rows, drop] = False
    use[rows, add] = True
    growing[rows, add] = 0
    return int(len(rows))


def _learn(states, targets, use, *, exchange=_exchange):
    """Run the windowed learner from an initial support mask. `use` is updated in place.

    The window update is `lab.graph_bench.simulate`'s, expression for expression. It is where the time goes: the
    sparse matrix costs a second pass of the same products plus its score, so a fit takes about three times a
    dense-only one whatever the exchange costs (scripts/bench_graph_learner.py time). `exchange`: the edge-exchange
    check (tests pass `_exchange_reference` to compare).
    """
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
            swaps += exchange(growing, score, use)
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


def pack(dense, validation, rule, sparse=None):
    """Pick the smallest NSG1 file within (1 + validation_tolerance) of the reference validation MSE.

    `validation` is (states, targets). Candidates are FP32 plus every (degree, bits) pair from
    rule["pack_candidates"] with degree <= N, cut from `dense`. The reference is the dense FP32 graph, which always
    qualifies, so a choice always exists.

    A rule may ask for more (rules.py; absent in gate-v1, whose choice and result are unchanged):
    - pack_candidates.learned_sparse: `sparse`, the matrix the edge-exchange learner fitted, is a candidate as
      well: as FP32 and at its own degree (the most inputs any receiver has) with each bit width of the rule.
    - pack_reference "best_learner": the reference is the lower validation MSE of the two learners' FP32 graphs.
      The better learner's FP32 file is exactly at the reference, so a choice still always exists.
    With learned_sparse, files of equal size are separated by the lower validation MSE, and the result also says
    which learner the chosen file came from ("learner"), the reference used and each candidate's learner.
    """
    states, targets = validation
    dense = np.asarray(dense).astype("f4")
    baseline = mse(dense, states, targets)
    cols = dense.shape[1]
    tolerance = float(rule["validation_tolerance"])
    spec = rule["pack_candidates"]
    learned = bool(spec.get("learned_sparse", False))
    matrices = {"dense": dense}
    options = [("dense", cols, 32)] + [("dense", degree, bits) for degree in spec["degrees"]
                                       for bits in spec["bits"] if degree <= cols]
    reference = baseline
    if learned:
        if sparse is None:
            raise ValidationError("This scoring rule packs the learned sparse graph, and none was given")
        matrices["sparse"] = np.asarray(sparse).astype("f4")
        if matrices["sparse"].shape != dense.shape:
            raise ValidationError("The dense and the sparse graph have different shapes")
        own_degree = max(1, int((matrices["sparse"] != 0).sum(axis=1).max()))
        options += [("sparse", cols, 32)] + [("sparse", own_degree, bits) for bits in spec["bits"]]
        if rule.get("pack_reference", "dense") == "best_learner":
            reference = min(baseline, mse(matrices["sparse"], states, targets))
    candidates = []
    for source, degree, bits in options:
        blob = encode(matrices[source], degree, bits)
        loss = mse(decode(blob), states, targets)
        candidates.append({"bits": bits, "degree": degree, "byte_size": len(blob), "validation_mse": loss,
                           "eligible": loss <= (1 + tolerance) * reference, "blob": blob, "learner": source})
    eligible = [c for c in candidates if c["eligible"]]
    if learned:
        chosen = min(eligible, key=lambda c: (c["byte_size"], c["validation_mse"]))
    else:
        chosen = min(eligible, key=lambda c: c["byte_size"])
    keys = ("bits", "degree", "byte_size", "validation_mse", "eligible") + (("learner",) if learned else ())
    out = {"blob": chosen["blob"], "bits": chosen["bits"], "degree": chosen["degree"],
           "byte_size": chosen["byte_size"], "validation_mse": chosen["validation_mse"],
           "dense_validation_mse": baseline,
           "candidates": [{key: c[key] for key in keys} for c in candidates]}
    if learned:
        out |= {"learner": chosen["learner"], "reference": rule.get("pack_reference", "dense"),
                "reference_validation_mse": reference}
    return out
