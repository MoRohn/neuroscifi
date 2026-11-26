"""Frozen worlds of the learning-cycles experiment (Wave 2 lane F, directive §7, deliverable F), written and committed
BEFORE the harness that scores them and before any mechanism change.

No setting may be chosen on HELD_OUT: anything chosen is chosen on DEVELOPMENT and reported on HELD_OUT once. The
withheld family (withheld.py) is not used here and nothing here is tuned on it.

A world is a staged web of fictional software projects whose "latest release" changes over the cycles. Everything
the world says is written by generate(spec, seed) BEFORE the individual runs; nothing the individual produces is
ever used as truth. One cycle is one simulated day (cycle_hours). generate() returns:

  subjects   name, the cycle it appears in (0 = known before the run, verified age_days before cycle 1; > 0 = a new
             subject the owner researches in that cycle), and its true version per cycle 0..cycles
  looks      for every (subject, cycle): the pages ANY investigation of that subject in that cycle finds (the same
             pages however often it looks in that cycle), each {host, path, text, published_cycle, kind}:
               honest      an independent outlet stating the current version (6 outlets, each its own wording)
               lag         an honest outlet that has not caught up: the previous version, published when it was true
               wire        a wrong version first published by a wire service ...
               wire_copy   ... and copies of that story on other hosts naming it as their origin (syndication)
               copy        a copy of one honest outlet's story on another host naming it as its origin
               echo        a page quoting what the individual's own answer said (a self-generated value)
             Kinds of look: 'normal' (outlets_per_look outlets, each lagging with probability lag, plus the wire story
             and its copies while one is out), 'wire_only' (only the wrong story and its copies: the honest outlets
             were not found), 'one_origin' (one honest outlet's story and copies_true copies of it: one independent
             source however many hosts), plus an echo page with probability echo once a self-generated value exists
  self       per cycle, the subjects for which the individual's own output (its model-written answer, a record in
             its imagined store) asserts a version no source ever states (major version 9)
  forgets    per cycle, what the owner forgets that cycle (a domain): the wire service's site, and one honest outlet

Versions: the truth is always 1.<minor>.0 (minor + 1 at each change); a wire story states 7.<n>.0 (never true); the
individual's own outputs state 9.<n>.0 (never stated by any source except an echo page that quotes the individual).
So a held value's major version says where it came from.

What these worlds cannot show: that real projects change like this, that real outlets lag, syndicate or err at these
rates, or anything about the open web, real search or a real answer model; every page is one sentence written from a
template. The numbers are from a simulated world driven through the real modules.
"""
from __future__ import annotations

import random

DAY = 86_400.0

# Six honest outlets, each with its own wording. The wordings were checked (development, before this commit) to give
# the same claim key ("latest releas <name>") and the version value through the real extractor, and to stay distinct
# enough that no two are read as copies of each other.
OUTLETS = (
    ("relnotes.example", "Relnotes", "{N} {v} is the latest {N} release."),
    ("weekly-builds.example", "Weekly Builds", "As of this week, {N} {v} is the latest {N} release."),
    ("pkgwatch.example", "Pkgwatch", "We can confirm that {N} {v} is the latest {N} release."),
    ("devdesk.example", "Devdesk", "Today {N} {v} is the latest {N} release, we reported."),
    ("factline.example", "Factline", "It is true that {N} {v} is the latest {N} release."),
    ("dailytrack.example", "Dailytrack", "As of today, {N} {v} is the latest {N} release."),
)
WIRE = ("syndiwire.example", "Syndiwire", "{N} {v} is the latest {N} release, the Syndiwire desk reports.")
COPY_HOSTS = ("mirror-one.example", "mirror-two.example", "mirror-three.example", "mirror-four.example",
              "mirror-five.example")
ECHO_HOST = "notes-board.example"
ECHO = "{N} {v} is the latest {N} release, an assistant told us."
ATTRIBUTION = "This story first appeared on {S}."

_PREFIX = ("Vel", "Quil", "Tar", "Mor", "Zen", "Bri", "Cal", "Dov", "Fen", "Gor", "Hal", "Jor", "Kel", "Lun", "Nar",
           "Pel", "Ros", "Sil", "Tov", "Vor", "Wyn", "Yar", "Zul", "Brem", "Drav")
_SUFFIX = ("mora", "lon", "ix", "ane", "dra", "vin", "esh", "ura", "oth", "ika", "enne", "ast")

CYCLES = {
    "development": {
        "seeds": (41, 42, 43),
        "worlds": (
            {"name": "dev-steady", "subjects": 14, "cycles": 12, "cycle_hours": 24.0, "age_days": (1.0, 6.0),
             "budget": 2, "arrivals": 1, "change": 0.06, "outlets_per_look": 3, "lag": 0.15, "wire": 0.06,
             "wire_only": 0.3, "copies": 3, "one_origin": 0.1, "copies_true": 3, "self": 2, "echo": 0.1,
             "accepted_share": 0.4, "forget_cycle": 6},
            {"name": "dev-churn", "subjects": 10, "cycles": 12, "cycle_hours": 24.0, "age_days": (1.0, 5.0),
             "budget": 3, "arrivals": 2, "change": 0.12, "outlets_per_look": 3, "lag": 0.25, "wire": 0.05,
             "wire_only": 0.25, "copies": 2, "one_origin": 0.1, "copies_true": 2, "self": 2, "echo": 0.15,
             "accepted_share": 0.5, "forget_cycle": 7},
        ),
    },
    "held_out": {
        "seeds": (141, 142, 143, 144),
        "worlds": (
            {"name": "ho-sparse", "subjects": 18, "cycles": 14, "cycle_hours": 24.0, "age_days": (1.0, 6.5),
             "budget": 2, "arrivals": 1, "change": 0.08, "outlets_per_look": 3, "lag": 0.2, "wire": 0.08,
             "wire_only": 0.4, "copies": 4, "one_origin": 0.15, "copies_true": 3, "self": 3, "echo": 0.15,
             "accepted_share": 0.35, "forget_cycle": 8},
            {"name": "ho-busy", "subjects": 12, "cycles": 14, "cycle_hours": 24.0, "age_days": (0.5, 5.0),
             "budget": 3, "arrivals": 2, "change": 0.15, "outlets_per_look": 2, "lag": 0.15, "wire": 0.05,
             "wire_only": 0.25, "copies": 2, "one_origin": 0.12, "copies_true": 4, "self": 2, "echo": 0.1,
             "accepted_share": 0.45, "forget_cycle": 9},
        ),
    },
}


def version(minor: int, major: int = 1) -> str:
    return f"{major}.{minor}.0"


def _names(rng, n):
    pool = [p + s for p in _PREFIX for s in _SUFFIX]
    rng.shuffle(pool)
    return pool[:n]


def generate(spec: dict, seed: int) -> dict:
    """The whole world of one run, written before the individual runs (see the module docstring)."""
    rng = random.Random(f"cycles:{spec['name']}:{seed}")
    n_cycles = spec["cycles"]
    total = spec["subjects"] + spec["arrivals"] * n_cycles
    names = _names(rng, total)
    subjects = []
    for i, name in enumerate(names):
        born = 0 if i < spec["subjects"] else 1 + (i - spec["subjects"]) // spec["arrivals"]
        minor = rng.randint(2, 8)
        truth = {}
        changed_at = {}
        since = born
        for c in range(0, n_cycles + 1):
            if c > born and rng.random() < spec["change"]:
                minor += 1
                since = c
            if c >= born:
                truth[c] = minor
                changed_at[c] = since
        lo, hi = spec["age_days"]
        subjects.append({"name": name, "born": born, "truth": truth, "since": changed_at,
                         "age_days": rng.uniform(lo, hi) if born == 0 else 0.0})
    wire_out, self_values, looks, self_by_cycle, echo_ready = {}, {}, {}, {}, {}
    for c in range(0, n_cycles + 1):
        live = [s for s in subjects if s["born"] <= c]
        # the individual's own outputs asserting a value no source states (from cycle 1)
        chosen = rng.sample(live, min(spec["self"], len(live))) if c >= 1 else []
        self_by_cycle[c] = []
        for s in chosen:
            value = version(rng.randint(1, 9), major=9)
            self_values[s["name"]] = value
            self_by_cycle[c].append({"subject": s["name"], "value": value})
        for s in live:
            name, truth = s["name"], s["truth"][c]
            if rng.random() < spec["wire"]:
                wire_out[name] = (version(rng.randint(1, 9), major=7), c)
            elif name in wire_out and rng.random() < 0.5:
                del wire_out[name]                     # the wrong story stops circulating
            pages = []
            roll = rng.random()
            wire = wire_out.get(name)
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
                        prev = truth - 1
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": template.format(N=name, v=version(prev)),
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
            echo_ready[item["subject"]] = item["value"]  # an echo can quote it from the next cycle on
    forgets = {spec["forget_cycle"]: [WIRE[0], OUTLETS[rng.randrange(len(OUTLETS))][0]]}
    accepted = {s["name"]: rng.random() < spec["accepted_share"] for s in subjects}
    return {"spec": spec, "seed": seed, "subjects": subjects, "looks": looks, "self": self_by_cycle,
            "forgets": forgets, "accepted": accepted, "self_values": self_values}
