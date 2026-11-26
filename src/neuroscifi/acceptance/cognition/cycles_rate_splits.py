"""Frozen worlds of Wave 3 lane R (ADR-060): learning-cycle worlds whose subjects change at DIFFERENT rates, written and
committed alone, BEFORE the harness that scores them and before any mechanism they measure.

No setting may be chosen on HELD_OUT: anything chosen is chosen on DEVELOPMENT and reported on HELD_OUT once. The
withheld worlds (withheld.py, cycles_withheld.py) are not used here, nothing here was written from them, and nothing
here is tuned on them. cycles_splits.py (lane F's worlds) stays frozen; its held-out split has been looked at by
three lanes and two reviews, so lane R reports on these seeds first and on lane F's held-out second, disclosed.

The worlds are those of cycles_splits.py (fictional software projects whose "latest release" changes; six honest
outlets in their own wording, lagging outlets, a wrong wire story and its syndicated copies, looks that find one
origin only, the individual's own outputs asserting versions no source states and echo pages quoting them, the
owner's forgets, a simulated owner accepting a share of what it researched; the same pages, wordings, version
majors and return shape, so cycles.py scores them unchanged), with three differences:

  rates        each subject belongs to a rate class drawn at its birth from spec["rates"] ((share, change probability
               per cycle), ...): some subjects change often, others rarely, and a subject keeps its class, so its
               history predicts its future ("learnable"). One class only: every subject changes at the same rate and a
               rate-based order has nothing to learn ("uniform"). With spec["shift_cycle"], every subject's class is
               drawn again at that cycle from a separate stream, so the history before it misleads ("shift").
  streams      separate keyed random streams (Wave 3 COMMON): the world's changes (per seed and subject), the pages
               of each look (per seed, subject and cycle), the wire story (per seed and subject), the individual's
               own outputs (per seed and cycle), the owner (per seed). Changing how pages are drawn cannot move a
               truth, and the truth of one subject does not depend on how many others exist.
  allowance    spec["budget_schedule"] (optional): the daily autonomous allowance in each cycle (index 0 unused),
               which cycles.budget_at reads; spec["budget"] is its first value.

Each subject also carries its class ("rate_class") and change probability ("rate") for the references only; the
individual never reads them (it sees pages, as in cycles_splits.py).

Seeds: development 2601-2603, held-out 2701-2704, disjoint from every split and benchmark seed in the tree (splits.py,
knowledge_splits.py, integrated_splits.py, cycles_splits.py, the wide seeds 5001-5012 and the withheld seeds
7341-7344 as stated in COGNITIVE_BENCHMARK.md); every stream is also keyed by "rcycles:" and the world's name.

What these worlds cannot show: that real projects change at such different rates, that a project's rate holds still
(or shifts once, all at the same cycle), or anything about the open web, real search or a real answer model. The
numbers are from a simulated world driven through the real modules.
"""
from __future__ import annotations

import random

from .cycles_splits import ATTRIBUTION, COPY_HOSTS, DAY, ECHO, ECHO_HOST, OUTLETS, WIRE, version   # noqa: F401

_PREFIX = ("Ab", "Brin", "Cor", "Dal", "Eb", "Fal", "Gil", "Hes", "Ist", "Jun", "Kav", "Lor", "Mest", "Nim", "Orv",
           "Pran", "Quor", "Rask", "Sev", "Trel", "Ulm", "Vask", "Wend", "Xan", "Yorv")
_SUFFIX = ("abel", "ondo", "eryx", "imar", "usk", "elle", "onix", "arro", "ith", "ova", "endra", "alos")

_COMMON = {"cycle_hours": 24.0, "outlets_per_look": 3, "copies": 3, "copies_true": 3, "self": 2}

CYCLES = {
    "development": {
        "seeds": (2601, 2602, 2603),
        "worlds": (
            # learnable: a quarter of the subjects change often, the rest rarely, and each keeps its class
            _COMMON | {"name": "dev-rate-mixed", "subjects": 16, "cycles": 14, "age_days": (1.0, 6.0), "budget": 2,
                       "arrivals": 1, "rates": ((0.25, 0.35), (0.75, 0.03)), "lag": 0.15, "wire": 0.05,
                       "wire_only": 0.3, "one_origin": 0.1, "echo": 0.1, "accepted_share": 0.4, "forget_cycle": 7},
            # uniform: one rate for every subject; nothing to learn
            _COMMON | {"name": "dev-rate-uniform", "subjects": 14, "cycles": 14, "age_days": (1.0, 6.0), "budget": 2,
                       "arrivals": 1, "rates": ((1.0, 0.10),), "lag": 0.2, "wire": 0.06, "wire_only": 0.3,
                       "one_origin": 0.1, "echo": 0.1, "accepted_share": 0.45, "forget_cycle": 6},
            # shift: mixed rates, every class drawn again at cycle 7; an allowance that changes from cycle to cycle
            _COMMON | {"name": "dev-rate-shift", "subjects": 14, "cycles": 14, "age_days": (0.5, 5.0), "budget": 2,
                       "budget_schedule": (2, 2, 2, 3, 2, 1, 2, 3, 2, 2, 1, 3, 2, 2, 2), "arrivals": 1,
                       "rates": ((0.3, 0.35), (0.7, 0.03)), "shift_cycle": 7, "outlets_per_look": 2, "lag": 0.15,
                       "wire": 0.05, "wire_only": 0.25, "one_origin": 0.12, "echo": 0.1, "accepted_share": 0.5,
                       "forget_cycle": 8},
        ),
    },
    "held_out": {
        "seeds": (2701, 2702, 2703, 2704),
        "worlds": (
            _COMMON | {"name": "ho-rate-mixed", "subjects": 18, "cycles": 16, "age_days": (1.0, 6.5), "budget": 2,
                       "arrivals": 1, "rates": ((0.2, 0.4), (0.3, 0.1), (0.5, 0.02)), "lag": 0.2, "wire": 0.07,
                       "wire_only": 0.35, "copies": 4, "one_origin": 0.12, "self": 3, "echo": 0.15,
                       "accepted_share": 0.35, "forget_cycle": 8},
            _COMMON | {"name": "ho-rate-uniform", "subjects": 12, "cycles": 14, "age_days": (0.5, 5.0), "budget": 3,
                       "arrivals": 2, "rates": ((1.0, 0.13),), "outlets_per_look": 2, "lag": 0.15, "wire": 0.05,
                       "wire_only": 0.25, "copies": 2, "one_origin": 0.12, "copies_true": 4, "echo": 0.1,
                       "accepted_share": 0.45, "forget_cycle": 9},
            _COMMON | {"name": "ho-rate-shift", "subjects": 16, "cycles": 16, "age_days": (1.0, 6.0), "budget": 2,
                       "budget_schedule": (2, 2, 3, 2, 2, 1, 2, 2, 3, 2, 1, 2, 2, 3, 2, 2, 2), "arrivals": 1,
                       "rates": ((0.25, 0.4), (0.75, 0.03)), "shift_cycle": 8, "lag": 0.2, "wire": 0.06,
                       "wire_only": 0.3, "one_origin": 0.1, "echo": 0.12, "accepted_share": 0.4, "forget_cycle": 10},
        ),
    },
}


def _stream(spec, seed, *parts) -> random.Random:
    return random.Random(":".join(["rcycles", spec["name"], str(seed), *map(str, parts)]))


def _names(spec, seed, n):
    pool = [p + s for p in _PREFIX for s in _SUFFIX]
    _stream(spec, seed, "names").shuffle(pool)
    return pool[:n]


def _draw_class(rng, rates) -> tuple[int, float]:
    roll, acc = rng.random(), 0.0
    for i, (share, rate) in enumerate(rates):
        acc += share
        if roll < acc:
            return i, rate
    return len(rates) - 1, rates[-1][1]


def generate(spec: dict, seed: int) -> dict:
    """The whole world of one run, written before the individual runs (see the module docstring); the return shape
    is cycles_splits.generate's, each subject with its rate class added."""
    n_cycles = spec["cycles"]
    total = spec["subjects"] + spec["arrivals"] * n_cycles
    names = _names(spec, seed, total)
    rates, shift = spec["rates"], spec.get("shift_cycle")
    subjects = []
    for i, name in enumerate(names):
        rng = _stream(spec, seed, "truth", i)            # the world's changes: per seed and subject
        born = 0 if i < spec["subjects"] else 1 + (i - spec["subjects"]) // spec["arrivals"]
        klass, rate = _draw_class(rng, rates)
        classes = {}
        minor = rng.randint(2, 8)
        truth, changed_at, since = {}, {}, born
        for c in range(0, n_cycles + 1):
            if shift is not None and c == shift and born < shift:
                klass, rate = _draw_class(_stream(spec, seed, "shift", i), rates)
            if c > born and rng.random() < rate:
                minor += 1
                since = c
            if c >= born:
                truth[c] = minor
                changed_at[c] = since
                classes[c] = klass
        lo, hi = spec["age_days"]
        subjects.append({"name": name, "born": born, "truth": truth, "since": changed_at,
                         "age_days": rng.uniform(lo, hi) if born == 0 else 0.0,
                         "rate_class": classes, "rate": {c: rates[k][1] for c, k in classes.items()}})
    self_values, looks, self_by_cycle, echo_ready = {}, {}, {}, {}
    wire_out = {}
    for c in range(0, n_cycles + 1):
        live = [s for s in subjects if s["born"] <= c]
        own = _stream(spec, seed, "self", c)              # the individual's own outputs: per seed and cycle
        chosen = own.sample(live, min(spec["self"], len(live))) if c >= 1 else []
        self_by_cycle[c] = []
        for s in chosen:
            value = version(own.randint(1, 9), major=9)
            self_values[s["name"]] = value
            self_by_cycle[c].append({"subject": s["name"], "value": value})
        for i, s in enumerate(subjects):
            if s["born"] > c:
                continue
            name, truth = s["name"], s["truth"][c]
            wr = _stream(spec, seed, "wire", i, c)        # the wrong story: per seed, subject and cycle
            if wr.random() < spec["wire"]:
                wire_out[name] = (version(wr.randint(1, 9), major=7), c)
            elif name in wire_out and wr.random() < 0.5:
                del wire_out[name]                        # the wrong story stops circulating
            rng = _stream(spec, seed, "look", i, c)       # the pages of this look: per seed, subject and cycle
            pages = []
            wire = wire_out.get(name)
            roll = rng.random()
            if wire is not None and rng.random() < spec["wire_only"]:
                kind = "wire_only"
            elif roll < spec["one_origin"]:
                kind = "one_origin"
            else:
                kind = "normal"
            if kind == "one_origin":
                host, label, template = OUTLETS[rng.randrange(len(OUTLETS))]
                text = template.format(N=name, v=version(truth))
                pages.append({"host": host, "path": f"/{name.lower()}", "text": text,
                              "published_cycle": s["since"][c], "kind": "honest"})
                for h in rng.sample(COPY_HOSTS, min(spec["copies_true"], len(COPY_HOSTS))):
                    pages.append({"host": h, "path": f"/{name.lower()}-{c}",
                                  "text": ATTRIBUTION.format(S=label) + "\n" + text,
                                  "published_cycle": s["since"][c], "kind": "copy"})
            if kind == "normal":
                for host, _label, template in rng.sample(OUTLETS, spec["outlets_per_look"]):
                    lagging = s["since"][c] > s["born"] and rng.random() < spec["lag"]
                    if lagging:
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": template.format(N=name, v=version(truth - 1)),
                                      "published_cycle": max(s["born"], s["since"].get(s["since"][c] - 1, s["born"])),
                                      "kind": "lag"})
                    else:
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": template.format(N=name, v=version(truth)),
                                      "published_cycle": s["since"][c], "kind": "honest"})
            if wire is not None and kind in ("normal", "wire_only"):
                value, at = wire
                text = WIRE[2].format(N=name, v=value)
                pages.append({"host": WIRE[0], "path": f"/{name.lower()}-{at}", "text": text, "published_cycle": at,
                              "kind": "wire"})
                for h in rng.sample(COPY_HOSTS, min(spec["copies"], len(COPY_HOSTS))):
                    pages.append({"host": h, "path": f"/wire-{name.lower()}-{at}",
                                  "text": ATTRIBUTION.format(S=WIRE[1]) + "\n" + text, "published_cycle": at,
                                  "kind": "wire_copy"})
            if echo_ready.get(name) and rng.random() < spec["echo"]:
                pages.append({"host": ECHO_HOST, "path": f"/{name.lower()}-{c}",
                              "text": ECHO.format(N=name, v=echo_ready[name]), "published_cycle": c, "kind": "echo"})
            looks[(name, c)] = {"kind": kind, "pages": pages}
        for item in self_by_cycle[c]:
            echo_ready[item["subject"]] = item["value"]   # an echo can quote it from the next cycle on
    owner = _stream(spec, seed, "owner")                  # the owner: per seed
    forgets = {spec["forget_cycle"]: [WIRE[0], OUTLETS[owner.randrange(len(OUTLETS))][0]]}
    accepted = {s["name"]: owner.random() < spec["accepted_share"] for s in subjects}
    return {"spec": spec, "seed": seed, "subjects": subjects, "looks": looks, "self": self_by_cycle,
            "forgets": forgets, "accepted": accepted, "self_values": self_values}
