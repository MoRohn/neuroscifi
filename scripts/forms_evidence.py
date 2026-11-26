"""Measure the form planner's properties on a wider seeded sample than the unit tests (ADR-073) and print a JSON
summary (the evidence file docs/quality/evidence/omnimind_c.json holds its output).

    PYTHONPATH=src python scripts/forms_evidence.py [--shapes 2000] [--seed 101]

Measured: monotonicity (shapes grown through 8 sizes; a violation is a choice that returns to words after leaving
them), hard-constraint violations over random contexts, determinism, the share of plans per decided_by, and the time
per plan. Every number comes from running `forms.plan`; nothing here judges whether a choice is good.
"""
from __future__ import annotations

import argparse
import json
import random
import time

from neuroscifi.artifacts import forms as F
from neuroscifi.artifacts.types import SHAPE_KINDS, ContentShape

ALL = tuple(sorted({f.generator for f in F.FORMS.values() if f.generator}))
WORDINGS = ("", "How did it change?", "Compare these.", "What is the value?", "How are they related?",
            "How does it work?", "What if I change the rate?", "Where is it?", "What does it sound like?",
            "Explain this.", "What does it look like?", "How are the values spread?", "Tell me about it.")
SIZES = (1, 2, 4, 8, 30, 200, 5000, 2_000_000)
WORDS = {("prose",), ("spoken",)}


def random_shape(rng, n=None):
    kinds = rng.sample(SHAPE_KINDS, rng.choice((1, 1, 1, 2, 2, 3)))
    space = rng.choice((0, 0, 2, 3))
    if any(k in kinds for k in ("image", "map")):
        space = max(space, 2)
    n = n if n is not None else int(10 ** rng.uniform(0, 6.5))
    return ContentShape(tuple(kinds), n, rng.choice((0, 1, 1, 2, 3, 5, 8, 30)), rng.random() < 0.5, space,
                        rng.random() < 0.3, rng.choice((0.0, 0.0, 2.0, 40.0)), rng.choice((0, 0, 100, 2000, 50_000)))


def random_context(rng, generous=False):
    gens = ALL if rng.random() < 0.6 else tuple(rng.sample(ALL, rng.randint(0, len(ALL))))
    if generous:
        return F.Context(device=rng.choice(F.DEVICES), budget_s=1e9, budget_bytes=1 << 60, generators=gens)
    return F.Context(device=rng.choice(F.DEVICES),
                     accessibility=tuple(rng.sample(F.ACCESSIBILITY, rng.choice((0, 0, 1, 2)))),
                     budget_s=rng.choice((0.05, 1.0, 60.0)), budget_bytes=rng.choice((2000, 200_000, 16 << 20)),
                     generators=gens)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--shapes", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=101)
    a = parser.parse_args(argv)
    rng = random.Random(a.seed)
    violations, transitions, examples = 0, 0, []
    for _ in range(a.shapes):
        base = random_shape(rng)
        context = random_context(rng, generous=True)
        request = F.Request(rng.choice(WORDINGS))
        left = None
        for n in SIZES:
            grown = ContentShape(base.kinds, n, base.n_series, base.has_time, base.has_space, base.has_parameters,
                                 base.duration_s, base.text_chars)
            chosen = F.plan(request, (grown,), context).chosen
            if left is not None:
                transitions += 1
                if chosen in WORDS:
                    violations += 1
                    if len(examples) < 5:
                        examples.append({"kinds": list(base.kinds), "n_series": base.n_series, "from_n": left,
                                         "to_n": n, "device": context.device})
                    break
            elif chosen and chosen not in WORDS:
                left = n
    hard, decided, elapsed, plans = 0, {d: 0 for d in F.DECIDED_BY}, 0.0, 0
    for _ in range(a.shapes):
        content, context = (random_shape(rng),), random_context(rng)
        request = F.Request(rng.choice(WORDINGS))
        start = time.perf_counter()
        result = F.plan(request, content, context)
        elapsed += time.perf_counter() - start
        plans += 1
        decided[result.decided_by] += 1
        display = F.resolve_display(context)
        for name in result.chosen:
            form = F.FORMS[name]
            bad = (not set(form.media) <= display.shows or not F.feasible_here(form, frozenset(context.generators))
                   or ("reduced_motion" in context.accessibility and "motion" in form.media))
            hard += bool(bad)
        if json.dumps(result.to_dict(), sort_keys=True) != json.dumps(F.plan(request, content, context).to_dict(),
                                                                      sort_keys=True):
            hard += 1
    print(json.dumps({
        "seed": a.seed, "shapes": a.shapes, "sizes": list(SIZES),
        "monotonicity": {"checked_after_leaving_words": transitions, "returned_to_words": violations,
                         "examples": examples},
        "random_plans": plans, "hard_constraint_or_determinism_violations": hard,
        "decided_by_share": {k: round(v / plans, 4) for k, v in decided.items()},
        "mean_ms_per_plan": round(1000 * elapsed / plans, 2),
    }, indent=1))


if __name__ == "__main__":
    main()
