"""Small, dependency-free metric helpers for the Web Cortex benchmark."""
from __future__ import annotations

from collections import Counter
import math
import re

_WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def token_f1(predicted: str, gold: str) -> dict:
    """Bag-of-words precision/recall/F1 of `predicted` against `gold` (lower-cased \\w+ tokens)."""
    p, g = Counter(words(predicted)), Counter(words(gold))
    overlap = sum((p & g).values())
    precision = overlap / sum(p.values()) if p else 0.0
    recall = overlap / sum(g.values()) if g else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if tp == 0 and
                                                                                         (fp or fn) else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _r(precision), "recall": _r(recall), "f1": _r(f1)}


def confusion(pairs: list[tuple[str, str]], labels: list[str]) -> dict:
    """pairs = [(gold, predicted)]; returns matrix[gold][predicted], per-label precision/recall and accuracy."""
    predicted_labels = sorted({p for _, p in pairs} | set(labels))
    matrix = {g: {p: 0 for p in predicted_labels} for g in labels}
    for g, p in pairs:
        matrix.setdefault(g, {q: 0 for q in predicted_labels})[p] += 1
    per = {}
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[g][label] for g in matrix if g != label)
        fn = sum(v for p, v in matrix[label].items() if p != label)
        per[label] = {"support": tp + fn, "predicted": tp + fp, "precision": _r(tp / (tp + fp) if tp + fp else None),
                      "recall": _r(tp / (tp + fn) if tp + fn else None)}
    correct = sum(1 for g, p in pairs if g == p)
    return {"labels": labels, "matrix": matrix, "per_label": per, "n": len(pairs), "correct": correct,
            "accuracy": _r(correct / len(pairs) if pairs else None)}


def percentile(values, q: float):
    """Linear-interpolated percentile (numpy's default method); None for no values."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    k = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summary(values, *, digits=4) -> dict:
    xs = [float(v) for v in values]
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "max": None}
    return {"n": len(xs), "p50": round(percentile(xs, 50), digits), "p95": round(percentile(xs, 95), digits),
            "mean": round(sum(xs) / len(xs), digits), "max": round(max(xs), digits)}


def rate(numerator: int, denominator: int) -> dict:
    return {"n": numerator, "of": denominator, "rate": _r(numerator / denominator if denominator else None)}


def _r(x, digits=4):
    return None if x is None else round(float(x), digits)


def collapse(text: str) -> str:
    return " ".join((text or "").split())
