"""Frozen worlds of Wave 4 lane A (ADR-064): learning cycles in which how the day's allowance is divided between
refreshing what is held and reaching subjects that hold nothing matters in some worlds and not in others. Written and
committed ALONE, before the harness that scores them and before the mechanism they measure. Never edit this file: a
change of world is a new file.

No setting may be chosen on HELD_OUT: anything chosen is chosen on DEVELOPMENT and reported on HELD_OUT once. Nothing
here was written from the withheld learning-cycles worlds (cycles_withheld.py, never read); Wave 3's record says only
that, on them, running the idle investigation before the timer's pass helped and that the loop stays below a blind
round-robin. Seeds are disjoint from every split and benchmark seed in the tree (development 6401-6403, held-out
6501-6504; checked against splits.py, knowledge_splits.py, integrated_splits.py, cycles_splits.py,
cycles_rate_splits.py, cycles_splits_s.py, cycles_splits_s_variants.py, the causal splits, the wide seeds 5001-5012
and the withheld seeds 7341-7344 as stated in COGNITIVE_BENCHMARK.md), and every stream is keyed by this file's world
names, which no other split uses.

The worlds are lane S's (cycles_splits_s.generate, played unchanged: fictional projects whose "latest release"
changes; honest outlets in their own wording, lagging outlets, rumours that look newer, the wrong wire story
(implausible 7.x or plausible 1.<minor+1>) and its copies in nine syndication shapes, looks that find one origin only,
the individual's own outputs (9.x, some prescient) and echo pages repeating them in five shapes, retractions, the
owner's forgets and acceptances), so every failure shape Wave 3 staged is present while reaching more subjects meets
more sources. lane S's generator draws everything from streams keyed by the world's name; the worlds here have new
names, so they are new worlds. One thing is added, from this file's own keyed stream ("reach-cycles", world, seed,
"thin", subject):

  thin births   a share `thin_birth` of the subjects (each drawn once, at its birth) are found THIN by the owner's
                own research: the look at its birth cycle, and at the `thin_spell` cycles after it, finds one honest
                outlet and copies of it (one independent origin, kind 'one_origin'), so the owner's research gives
                the individual no knowledge item about it. Later looks are lane S's. With `thin_until`, only subjects
                born before that cycle can be thin (reaching matters early in the run and not later).

Each world states its `kind`, written here before any run of any division rule:
  reach      many subjects hold nothing after the owner's research (a high thin share, two arrivals per cycle):
             reaching them is where the allowance can buy correct subjects
  covered    no thin births and few one-origin looks: nearly every subject is held, subjects change often and the
             allowance is small: refreshing is where it buys correct subjects, and a division that always holds part
             of the allowance for reaching should LOSE here
  mixed      a moderate thin share with heavier wrong stories and echoes: reaching meets more of them
  shift      thin births only in the first half of the run (`thin_until`), covered arrivals after: what reaching can
             buy changes during the run
The `kind` is a label for the report and the verdict rule (which worlds reaching is expected to matter in); the
individual never reads it.

What these worlds cannot show: how many subjects stay without knowledge on the open web, how often real projects
change, or anything about real search or a real answer model; every page is one sentence from a template, read
through the real modules.
"""
from __future__ import annotations

import random

from . import cycles_splits_s as base
from .cycles_splits import ATTRIBUTION, COPY_HOSTS, OUTLETS, version

# lane S's sd-mixed proportions are the common ground; each world states what differs from them.
_S = {"cycle_hours": 24.0, "retract": 0.02, "outlets_per_look": 3, "lag": 0.2, "rumour": 0.06, "team": 0.15,
      "wire": 0.08, "wire_plausible": 0.5, "wire_only": 0.3, "copies": 3, "one_origin": 0.12, "copies_true": 3,
      "self": 2, "prescient": 0.2, "echo": 0.25, "echo_only": 0.08, "echo_pages": 2, "accepted_share": 0.4,
      "thin_spell": 1}

CYCLES = {
    "development": {
        "seeds": (6401, 6402, 6403),
        "worlds": (
            _S | {"name": "rd-reach", "kind": "reach", "subjects": 10, "cycles": 12, "age_days": (1.0, 6.0),
                  "budget": 3, "arrivals": 2, "change": 0.08, "thin_birth": 0.6, "forget_cycle": 6},
            _S | {"name": "rd-covered", "kind": "covered", "subjects": 14, "cycles": 12, "age_days": (1.0, 5.0),
                  "budget": 2, "arrivals": 1, "change": 0.16, "thin_birth": 0.0, "one_origin": 0.03,
                  "wire_only": 0.15, "forget_cycle": 7},
            _S | {"name": "rd-mixed", "kind": "mixed", "subjects": 12, "cycles": 12, "age_days": (1.0, 6.0),
                  "budget": 3, "arrivals": 1, "change": 0.10, "thin_birth": 0.3, "wire": 0.10,
                  "wire_plausible": 0.6, "echo": 0.3, "forget_cycle": 6},
            _S | {"name": "rd-shift", "kind": "shift", "subjects": 10, "cycles": 12, "age_days": (1.0, 6.0),
                  "budget": 3, "arrivals": 2, "change": 0.10, "thin_birth": 0.6, "thin_until": 6,
                  "forget_cycle": 8},
        ),
    },
    "held_out": {
        "seeds": (6501, 6502, 6503, 6504),
        "worlds": (
            _S | {"name": "rh-reach", "kind": "reach", "subjects": 12, "cycles": 14, "age_days": (1.0, 6.5),
                  "budget": 3, "arrivals": 2, "change": 0.07, "thin_birth": 0.55, "thin_spell": 2,
                  "forget_cycle": 7},
            _S | {"name": "rh-covered", "kind": "covered", "subjects": 16, "cycles": 14, "age_days": (0.5, 5.0),
                  "budget": 2, "arrivals": 1, "change": 0.14, "thin_birth": 0.0, "one_origin": 0.03,
                  "wire_only": 0.15, "lag": 0.15, "forget_cycle": 8},
            _S | {"name": "rh-mixed", "kind": "mixed", "subjects": 14, "cycles": 14, "age_days": (1.0, 6.5),
                  "budget": 3, "arrivals": 1, "change": 0.10, "thin_birth": 0.35, "wire": 0.12,
                  "wire_plausible": 0.7, "echo": 0.35, "echo_only": 0.12, "forget_cycle": 9},
            _S | {"name": "rh-shift", "kind": "shift", "subjects": 12, "cycles": 14, "age_days": (1.0, 6.0),
                  "budget": 3, "arrivals": 2, "change": 0.09, "thin_birth": 0.65, "thin_until": 7,
                  "forget_cycle": 10},
        ),
    },
}


def _stream(spec, seed, *parts) -> random.Random:
    return random.Random(":".join(["reach-cycles", spec["name"], str(seed), *map(str, parts)]))


def _one_origin(rng, spec, s, c) -> dict:
    """One honest outlet's page and copies of it (cycles_splits_s's one-origin shapes): one independent origin."""
    name, truth = s["name"], s["truth"][c]
    host, label, template = OUTLETS[rng.randrange(len(OUTLETS))]
    text = template.format(N=name, v=version(truth))
    pages = [{"host": host, "path": f"/{name.lower()}", "text": text, "published_cycle": s["since"][c],
              "kind": "honest", "variant": "origin"}]
    for h in rng.sample(COPY_HOSTS, min(spec["copies_true"], len(COPY_HOSTS))):
        shape = base.ORIGIN_SHAPES[rng.randrange(len(base.ORIGIN_SHAPES))]
        body = (ATTRIBUTION.format(S=label) + "\n" + text if shape == "line" else
                f"According to {label}, {name} {version(truth)} is the latest {name} release."
                if shape == "according" else text)
        pages.append({"host": h, "path": f"/{name.lower()}-{c}", "text": body, "published_cycle": s["since"][c],
                      "kind": "copy", "variant": shape})
    return {"kind": "one_origin", "pages": pages}


def generate(spec: dict, seed: int) -> dict:
    """The whole world of one run, written before the individual runs: lane S's world for this spec and seed, then
    the thin births (see the module docstring). The return shape is cycles_splits_s.generate's; each subject also
    carries `thin` (for the report only; the individual never reads it)."""
    world = base.generate(spec, seed)
    until = spec.get("thin_until")
    for s in world["subjects"]:
        rng = _stream(spec, seed, "thin", s["name"])
        s["thin"] = rng.random() < spec["thin_birth"] and (until is None or s["born"] < until)
        if not s["thin"]:
            continue
        for c in range(s["born"], min(spec["cycles"], s["born"] + spec["thin_spell"]) + 1):
            world["looks"][(s["name"], c)] = _one_origin(rng, spec, s, c)
    return world
