"""WITHHELD worlds of the learning-cycles experiment (Wave 3 step 0; FINAL_EVALUATION §6 direction 5), written and
committed ALONE, before the guard that scores them and before any Wave 3 mechanism.

What no lane may do with this file
----------------------------------
These worlds exist so that a change to the knowledge loop (the refresh order, supersession of stale items, anything
Wave 3 builds) is judged on worlds nobody could tune it on. They are worth that only while nobody has looked. So no
lane, reviewer or tool other than the main session's one final look may:
  - read, print, summarise, diff or quote this file, or import it (directly or by a helper written for the purpose);
  - generate, inspect or score its worlds, or compute anything from them, including references or statistics;
  - copy its parameters, kinds or seeds into another world, or choose any setting, threshold or wording with it in
    mind;
  - edit it (its SHA-256 is pinned by tests/acceptance/test_learning_cycles_withheld.py; a change is a new file, new
    seeds and a new look, never an edit).
The only way to score these worlds is cycles.final() (scripts/bench_learning_cycles.py final), which refuses a dirty
tree and appends its line to docs/quality/evidence/genesis_final_uses.jsonl before it scores anything; each such run
is a look and is reported whatever it shows. Wave 3 step 0 takes the unchanged system's one look; the main session
takes the next one, once, at the end of Wave 3. Development is done on cycles_splits.py's development worlds; claims are
reported on its held-out worlds; this file is for the end.

Purpose (what the worlds are for, not what is in them): transfer. They are of kinds that appear in neither the
development nor the held-out worlds, and they contain what a rule that supersedes stale items and a refresh order
that is aware of change rates need in order to be judged fairly, without being shaped around either: stale items
whose newer version several independent sources state; stale items whose "newer" version is a syndicated error;
the individual's own earlier text echoed back by outside pages; subjects whose rate of change can be learned from
their history and subjects whose rate cannot.

The world structure is the one cycles.py reads (cycles_splits.py's docstring): subjects with a true version per
cycle (always 1.<minor>.0), looks (the pages any investigation of a subject finds in a cycle), the individual's own
outputs (9.<n>.0, stated by no source but an echo of them), wrong stories (7.<n>.0, never true), the owner's forgets
and acceptances. Every value is written by generate(spec, seed) before the individual runs; nothing it produces is
ever truth. What these worlds cannot show is what cycles_splits.py says its worlds cannot show: they are simulated,
every page is one sentence from a template, and nothing here is evidence about the open web or real outlets.

Seeds 7341-7344: disjoint from every seed in the repository's splits and benchmarks (splits.py 11-13, 21-23, 31-33,
41-42, 1011-1041; knowledge_splits.py 11-13, 21-23, 101-104, 201-204; integrated_splits.py 61-63, 601-604;
cycles_splits.py 41-43, 141-144; the wide seeds 5001-5012; the withheld family 9001-9003), and every random stream
is keyed by this file's own prefix and the world's name as well.
"""
from __future__ import annotations

import random

SPLIT = "withheld"
SEEDS = (7341, 7342, 7343, 7344)

# Six independent outlets. The six sentence forms are cycles_splits.py's, checked there to give one claim key and the
# version through the real extractor and to stay distinct from one another; the hosts and names are new.
OUTLETS = (
    ("shipnotes.example", "Shipnotes", "{N} {v} is the latest {N} release."),
    ("versionist.example", "Versionist", "As of now, {N} {v} is the latest {N} release."),
    ("changefeed.example", "Changefeed", "As of today, {N} {v} is the latest {N} release."),
    ("repodaily.example", "Repodaily", "By now {N} {v} is the latest {N} release."),
    ("tracklog.example", "Tracklog", "Once again {N} {v} is the latest {N} release."),
    ("buildpost.example", "Buildpost", "As of yesterday, {N} {v} is the latest {N} release."),
)
WIRE = ("relaypress.example", "Relaypress", "{N} {v} is the latest {N} release, the Relaypress desk reports.")
# A repost of the wrong story without its credit and without naming its origin: read through the real extractor it
# states the same claim as an outlet does (the credited sentence does not), so the error can stand as a "newer"
# version of a held item (checked with a made-up subject, before this commit; no world here was scored).
REPOST = "{N} {v} is the latest {N} release."
COPY_HOSTS = ("aggregate-one.example", "aggregate-two.example", "aggregate-three.example", "aggregate-four.example",
              "aggregate-five.example", "aggregate-six.example", "aggregate-seven.example", "aggregate-eight.example")
REPOST_HOSTS = ("feedbin-one.example", "feedbin-two.example", "feedbin-three.example", "feedbin-four.example")
ECHO_HOSTS = ("forum-one.example", "forum-two.example", "forum-three.example")
ECHOES = ("{N} {v} is the latest {N} release, an assistant reported.", "{N} {v} is the latest {N} release.")
ATTRIBUTION = "This story first appeared on {S}."

_PREFIX = ("Ast", "Bel", "Cor", "Dun", "Esk", "Fal", "Gav", "Hest", "Ilm", "Jask", "Kor", "Lir", "Mav", "Nol", "Orv",
           "Pask", "Quen", "Rill", "Sev", "Tarn", "Ulm", "Vask", "Wend", "Xan", "Yost")
_SUFFIX = ("abel", "orin", "ette", "undo", "ivar", "osta", "elm", "arro", "inth", "obel", "uvia", "erra")

# Each world: cycles; budget_schedule (the daily autonomous allowance per cycle, index = cycle, index 0 unused; budget
# = the allowance at cycle 1); subjects at cycle 0 and arrivals {cycle: count}; classes {class: count share} with
# rate (per-cycle change probability by class, times regime[cycle]) and period (cycles, for the regular class);
# outlet_lags (cycles each outlet is behind, by outlet); outlets_per_look (min, max); wrong stories (wire per
# subject-cycle, wire_only, copies and reposts (min, max), correct_after (min, max) cycles or None, linger cycles the
# copies stay after a correction); one_origin, copies_true; the individual's outputs (self per cycle, echo, echo_only,
# echo_hosts max); accepted_share; forgets ((cycle, "wire" | "outlet" | "repost"), ...).


def _segments(n, parts, default):
    """A per-cycle tuple (index 0..n) from ((first, last, value), ...); cycles not covered take `default`."""
    out = [default] * (n + 1)
    for first, last, value in parts:
        for c in range(first, min(last, n) + 1):
            out[c] = value
    return tuple(out)


WORLDS = (
    {"name": "wh-seasons", "cycles": 24, "cycle_hours": 24.0, "age_days": (0.5, 6.0),
     "budget_schedule": _segments(24, ((1, 5, 4), (6, 9, 1), (10, 14, 3), (15, 18, 5), (19, 21, 1)), 2),
     "subjects": 16, "arrivals": {5: 1, 11: 2, 17: 1, 22: 2},
     "classes": {"fast": 3, "slow": 4, "frozen": 3, "regular": 3, "irregular": 3},
     "rate": {"fast": 0.3, "slow": 0.04, "frozen": 0.0, "irregular": 0.15}, "period": (3, 5),
     "regime": _segments(24, ((1, 5, 0.2), (6, 9, 3.0), (15, 18, 0.0), (19, 21, 3.0)), 1.0),
     "outlet_lags": (0, 0, 0, 1, 2, 4), "outlets_per_look": (2, 4),
     "wire": 0.05, "wire_only": 0.25, "copies": (2, 4), "reposts": (0, 1), "correct_after": (2, 4), "linger": 2,
     "one_origin": 0.1, "copies_true": 3, "self": 2, "echo": 0.15, "echo_only": 0.05, "echo_hosts": 2,
     "accepted_share": 0.4, "forgets": ((7, "wire"), (13, "outlet"))},
    {"name": "wh-relay", "cycles": 20, "cycle_hours": 24.0, "age_days": (1.0, 5.0),
     "budget_schedule": _segments(20, ((1, 3, 1), (4, 8, 4), (9, 11, 1), (12, 15, 3)), 2),
     "subjects": 14, "arrivals": {12: 5},
     "classes": {"fast": 2, "slow": 4, "frozen": 2, "regular": 3, "irregular": 3},
     "rate": {"fast": 0.25, "slow": 0.05, "frozen": 0.0, "irregular": 0.12}, "period": (2, 4),
     "regime": _segments(20, ((10, 13, 2.5),), 1.0),
     "outlet_lags": (0, 0, 1, 1, 3, 6), "outlets_per_look": (3, 5),
     "wire": 0.1, "wire_only": 0.35, "copies": (4, 8), "reposts": (1, 4), "correct_after": (1, 6), "linger": 4,
     "one_origin": 0.12, "copies_true": 4, "self": 3, "echo": 0.3, "echo_only": 0.12, "echo_hosts": 3,
     "accepted_share": 0.5, "forgets": ((9, "repost"), (15, "outlet"))},
    {"name": "wh-long", "cycles": 30, "cycle_hours": 24.0, "age_days": (1.0, 7.0),
     "budget_schedule": tuple([2] + [1 if (c // 3) % 2 else 4 for c in range(1, 31)]),
     "subjects": 18, "arrivals": {8: 1, 16: 1, 20: 3, 26: 1},
     "classes": {"fast": 2, "slow": 5, "frozen": 2, "regular": 6, "irregular": 3},
     "rate": {"fast": 0.35, "slow": 0.03, "frozen": 0.0, "irregular": 0.2}, "period": (3, 7),
     "regime": _segments(30, ((1, 6, 0.5), (12, 14, 2.0), (22, 25, 0.0)), 1.0),
     "outlet_lags": (0, 0, 2, 3, 5, 8), "outlets_per_look": (2, 4),
     "wire": 0.06, "wire_only": 0.3, "copies": (2, 6), "reposts": (0, 2), "correct_after": None, "linger": 0,
     "one_origin": 0.15, "copies_true": 3, "self": 2, "echo": 0.2, "echo_only": 0.08, "echo_hosts": 3,
     "accepted_share": 0.35, "forgets": ((10, "wire"), (21, "outlet"), (27, "repost"))},
)
for _w in WORLDS:
    _w["budget"] = _w["budget_schedule"][1]
del _w

CYCLES = {SPLIT: {"seeds": SEEDS, "worlds": WORLDS}}


def version(minor: int, major: int = 1) -> str:
    return f"{major}.{minor}.0"


def _rng(spec, seed, *parts) -> random.Random:
    return random.Random(":".join(["cycles-withheld", spec["name"], str(seed), *map(str, parts)]))


def _subjects(spec, seed) -> list[dict]:
    n = spec["cycles"]
    total = spec["subjects"] + sum(spec["arrivals"].values())
    pool = [p + s for p in _PREFIX for s in _SUFFIX]
    _rng(spec, seed, "names").shuffle(pool)
    born = [0] * spec["subjects"] + [c for c, k in sorted(spec["arrivals"].items()) for _ in range(k)]
    weights = spec["classes"]
    classes = [k for k, w in weights.items() for _ in range(w)]
    picker = _rng(spec, seed, "classes")
    subjects = []
    for i, name in enumerate(pool[:total]):
        rng = _rng(spec, seed, "truth", name)
        cls = classes[i % len(classes)] if i < len(classes) else picker.choice(classes)
        minor = rng.randint(2, 8)
        period = rng.randint(*spec["period"])
        phase = rng.randrange(period)
        truth, since, at = {}, {}, born[i]
        for c in range(born[i], n + 1):
            draw = rng.random()
            if c > born[i]:
                if cls == "regular":
                    changed = (c - born[i] + phase) % period == 0
                else:
                    changed = draw < min(1.0, spec["rate"][cls] * spec["regime"][c])
                if changed:
                    minor += 1
                    at = c
            truth[c], since[c] = minor, at
        lo, hi = spec["age_days"]
        subjects.append({"name": name, "born": born[i], "class": cls, "truth": truth, "since": since,
                         "age_days": rng.uniform(lo, hi) if born[i] == 0 else 0.0})
    _rng(spec, seed, "order").shuffle(subjects)
    return subjects


def _outlet_page(s, c, outlet, lag):
    host, _label, template = OUTLETS[outlet]
    then = max(s["born"], c - lag)
    value = s["truth"][then]
    return {"host": host, "path": f"/{s['name'].lower()}", "text": template.format(N=s["name"], v=version(value)),
            "published_cycle": s["since"][then], "kind": "honest" if value == s["truth"][c] else "lag"}


def _wire_pages(s, story, c):
    """The wrong story and its syndication while it is out; after its correction, the wire's correction (the true
    version) and its copies, with copies of the wrong story lingering on the mirrors for a while."""
    name, low = s["name"], s["name"].lower()
    pages = []
    corrected = story["corrected"] is not None and c >= story["corrected"]
    wrong = WIRE[2].format(N=name, v=story["value"])
    if not corrected:
        pages.append({"host": WIRE[0], "path": f"/{low}-{story['at']}", "text": wrong,
                      "published_cycle": story["at"], "kind": "wire"})
    if not corrected or c < story["corrected"] + story["linger"]:
        for h in story["copy_hosts"]:
            pages.append({"host": h, "path": f"/wire-{low}-{story['at']}",
                          "text": ATTRIBUTION.format(S=WIRE[1]) + "\n" + wrong, "published_cycle": story["at"],
                          "kind": "wire_copy"})
        for h in story["repost_hosts"]:
            pages.append({"host": h, "path": f"/{low}-{story['at']}", "text": REPOST.format(N=name, v=story["value"]),
                          "published_cycle": story["at"], "kind": "wire_repost"})
    if corrected:
        right = WIRE[2].format(N=name, v=version(s["truth"][c]))
        fixed = story["corrected"]
        pages.append({"host": WIRE[0], "path": f"/{low}-{story['at']}-corrected", "text": right,
                      "published_cycle": fixed, "kind": "correction"})
        for h in story["copy_hosts"][:max(1, len(story["copy_hosts"]) // 2)]:
            pages.append({"host": h, "path": f"/wire-{low}-{story['at']}-corrected",
                          "text": ATTRIBUTION.format(S=WIRE[1]) + "\n" + right, "published_cycle": fixed,
                          "kind": "correction_copy"})
    return pages


def generate(spec: dict, seed: int) -> dict:
    """The whole world of one run, written before the individual runs (see the module docstring); the same keys as
    cycles_splits.generate, which cycles.py reads."""
    n = spec["cycles"]
    subjects = _subjects(spec, seed)
    stories, looks, self_by_cycle, self_values, echo_ready = {}, {}, {}, {}, {}
    mine = _rng(spec, seed, "self")
    for c in range(0, n + 1):
        live = [s for s in subjects if s["born"] <= c]
        chosen = mine.sample(live, min(spec["self"], len(live))) if c >= 1 else []
        self_by_cycle[c] = []
        for s in chosen:
            value = version(mine.randint(1, 9), major=9)
            self_values[s["name"]] = value
            self_by_cycle[c].append({"subject": s["name"], "value": value})
        for s in live:
            name = s["name"]
            rng = _rng(spec, seed, "look", name, c)
            story = stories.get(name)
            # an uncorrected story stops circulating at its end; a corrected one once its copies stopped lingering
            if story is not None and c >= (story["ends"] if story["corrected"] is None
                                           else story["corrected"] + story["linger"] + 1):
                del stories[name]
                story = None
            if story is None and rng.random() < spec["wire"]:
                lo_c, hi_c = spec["copies"]
                lo_r, hi_r = spec["reposts"]
                after = spec["correct_after"]
                story = {"value": version(rng.randint(1, 9), major=7), "at": c,
                         "copy_hosts": rng.sample(COPY_HOSTS, rng.randint(lo_c, hi_c)),
                         "repost_hosts": rng.sample(REPOST_HOSTS, rng.randint(lo_r, hi_r)),
                         "corrected": c + rng.randint(*after) if after else None,
                         "ends": None if after else c + rng.randint(2, 6), "linger": spec["linger"]}
                stories[name] = story
            roll = rng.random()
            echo = echo_ready.get(name)
            if story is not None and rng.random() < spec["wire_only"]:
                kind = "wire_only"
            elif echo and rng.random() < spec["echo_only"]:
                kind = "echo_only"
            elif roll < spec["one_origin"]:
                kind = "one_origin"
            else:
                kind = "normal"
            pages = []
            if kind == "one_origin":
                outlet = rng.randrange(len(OUTLETS))
                page = _outlet_page(s, c, outlet, 0)
                pages.append(page)
                for h in rng.sample(COPY_HOSTS, spec["copies_true"]):
                    pages.append(dict(page, host=h, path=f"/{name.lower()}-{c}", kind="copy",
                                      text=ATTRIBUTION.format(S=OUTLETS[outlet][1]) + "\n" + page["text"]))
            elif kind == "normal":
                for outlet in rng.sample(range(len(OUTLETS)), rng.randint(*spec["outlets_per_look"])):
                    pages.append(_outlet_page(s, c, outlet, spec["outlet_lags"][outlet]))
            if story is not None and kind in ("normal", "wire_only"):
                pages += _wire_pages(s, story, c)
            if echo and (kind == "echo_only" or (kind == "normal" and rng.random() < spec["echo"])):
                for h in rng.sample(ECHO_HOSTS, rng.randint(1, spec["echo_hosts"])):
                    text = ECHOES[rng.randrange(len(ECHOES))].format(N=name, v=echo)
                    pages.append({"host": h, "path": f"/{name.lower()}-{c}", "text": text, "published_cycle": c,
                                  "kind": "echo"})
            looks[(name, c)] = {"kind": kind, "pages": pages}
        for item in self_by_cycle[c]:
            echo_ready[item["subject"]] = item["value"]            # an echo can quote it from the next cycle on
    forget = _rng(spec, seed, "forgets")
    forgets = {}
    for cycle, what in spec["forgets"]:
        domain = (WIRE[0] if what == "wire" else REPOST_HOSTS[forget.randrange(len(REPOST_HOSTS))] if what == "repost"
                  else OUTLETS[forget.randrange(len(OUTLETS))][0])
        forgets.setdefault(cycle, []).append(domain)
    accept = _rng(spec, seed, "accepted")
    accepted = {s["name"]: accept.random() < spec["accepted_share"] for s in sorted(subjects, key=lambda s: s["name"])}
    return {"spec": spec, "seed": seed, "subjects": subjects, "looks": looks, "self": self_by_cycle,
            "forgets": forgets, "accepted": accepted, "self_values": self_values}
