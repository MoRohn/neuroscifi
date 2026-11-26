"""Frozen HELD-OUT worlds of the source-independence family (Wave 4 step 0), written and committed ALONE, after the
development worlds (sources_splits_dev.py, whose generator, cast, forms and documented wording sources they use
unchanged) and before the withheld worlds, the harness and any source rule. Never edit this file.

Nothing may be chosen on these worlds: a rule, a constant or a threshold is chosen on development and reported here
once. The shapes and their forms are development's; the worlds differ in their seeds (8401-8404, disjoint from every
seed in the repository outside the withheld files and from development's 8301-8303), their names (which key every
stream), their size (12 subjects, 12 cycles) and their proportions, stated below with the reason for each.

  src-ho-mixed    development's stated weights (honest corroboration the largest share) on new seeds
  src-ho-copies   copies and echoes heavier, honest corroboration lighter, as on development's second world, with
                  more wrong stories (story_true 0.4) and more own answers that are wrong
  src-ho-origins  multi-origin lines, round-ups, wire-like hosts, honest credits and primary-source citation heavier:
                  the shapes where a rule that reads credits can merge honest outlets, so a rule's suppression is
                  visible on held-out and not only its admissions
"""
from __future__ import annotations

from .sources_splits_dev import WEIGHTS, generate  # noqa: F401  (generate: the generator of these worlds)

_BASE = {"cycles": 12, "cycle_hours": 24.0, "arrivals": 1, "revisits": 4, "retract": 0.02, "primary_read": 0.5,
         "origin_read": 0.5, "echo_with_honest": 0.5}

CYCLES = {
    "held_out": {
        "seeds": (8401, 8402, 8403, 8404),
        "worlds": (
            _BASE | {"name": "src-ho-mixed", "subjects": 12, "change": 0.12, "weights": WEIGHTS, "story_true": 0.55,
                     "story_neighbour": 0.30, "convergent": 0.25, "own_per_cycle": 2, "own_right": 0.4,
                     "own_neighbour": 0.4},
            _BASE | {"name": "src-ho-copies", "subjects": 12, "change": 0.10,
                     "weights": WEIGHTS | {"independent": 14, "syndicated_credit": 18, "syndicated_nocredit": 12,
                                           "echo": 16, "extra_origin": 7, "mirror_dates": 7},
                     "story_true": 0.40, "story_neighbour": 0.35, "convergent": 0.3, "origin_read": 0.4,
                     "own_per_cycle": 3, "own_right": 0.3, "own_neighbour": 0.45, "echo_with_honest": 0.4},
            _BASE | {"name": "src-ho-origins", "subjects": 12, "change": 0.12,
                     "weights": WEIGHTS | {"independent": 16, "two_origins": 12, "roundup": 10, "wire_like_host": 9,
                                           "verified_credit": 12, "primary_cited": 14, "extra_origin": 8},
                     "story_true": 0.55, "story_neighbour": 0.30, "convergent": 0.25, "own_per_cycle": 2,
                     "own_right": 0.4, "own_neighbour": 0.4},
        ),
    },
}
