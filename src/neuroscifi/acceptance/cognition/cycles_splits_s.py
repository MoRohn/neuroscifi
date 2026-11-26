"""Frozen worlds of Wave 3 lane S (ADR-061): learning cycles whose pages copy, echo and err in the shapes that can
evade the independence rule. Written and committed ALONE, before the fixes, before supersession and before the
harness changes that score them. Never edit this file: a change of world is a new file.

No setting may be chosen on HELD_OUT: anything chosen (the supersession constant K among them) is chosen on
DEVELOPMENT and reported on HELD_OUT once. Nothing here was written from the withheld learning-cycles worlds
(cycles_withheld.py, never read); what Step 0's one look said of them is category level only (the lane's addendum).
Seeds are disjoint from cycles_splits.py's (41-43, 141-144) and every stream is keyed by this file's world names,
which no other split uses.

Same vocabulary as cycles_splits.py (read it first; the harness, cycles.Run, plays these worlds unchanged): fictional
projects whose "latest release" changes; the truth is 1.<minor>.0; the individual's own self-generated outputs state
9.<n>.0; the implausible wire story 7.<n>.0. What is new here:

  plausible errors   a wrong value that LOOKS newer: 1.<minor+1>.0 before it is true. Stated by the wire story (share
                     `wire_plausible` of its stories) and by `rumour` pages: an honest outlet's own wording, its own
                     host, an independent mistake. No independence rule can tell a rumour from an honest update; a
                     rumour is the honest source of false replacements for a supersession rule.
  syndication shapes a copy of the wire story (kind wire_copy; `variant` says its shape): `line` (the attribution
                     line of cycles_splits), `according` ("According to Syndiwire, ..."), `says` ("..., Syndiwire
                     says."), `paren` (an aggregator's line "... (Syndiwire)."), `via` ("... via Syndiwire."), `desk`
                     (a mirror REPUBLISHED LATER, published in the current cycle, "..., as the Syndiwire desk reports."),
                     `chain` (a copy of a copy: its attribution line names the mirror it took the story from), `bare`
                     (the wire sentence verbatim, no attribution) and `unattributed` (reworded, naming no one: no text
                     rule can see it, so it is the limit of any fix; kept at a small share, not left out)
  echo shapes        pages repeating the individual's own output (kind echo): `assistant` (cycles_splits' wording),
                     `verbatim` (the stored answer's sentence itself), `forum`, `quote`, `roundup`; on up to five
                     hosts, `echo_pages` of them per look; `echo_only` looks find nothing else
  prescient outputs  a share `prescient` of the individual's own outputs state the NEXT true version before any
                     source does (1.<minor+1>.0 of a subject whose truth will change): pages that later state it
                     honestly are honest corroboration; a rule that rejects them is a false rejection, measured here
  team quotes        an honest outlet quoting the project's own announcement by name ("..., the <N> team says."):
                     two such outlets are honest corroboration a strict attribution rule might wrongly merge
  one-origin copies  a one_origin look's copies take the shapes `line`, `according` (naming the outlet) or `bare`
  retractions        the truth goes back one minor version (a release pulled) with probability `retract`: newer pages
                     then state the older number (recency is the stated time, not the number)

Proportions, and why (not chosen to flatter any rule; none was run on these worlds before this commit):
  sd-mixed / s-mixed       every shape at a moderate share; plausible errors a minority of the newer values
  sd-wrong-newer / s-wrong-newer   MOST newer-looking values are wrong: rumours and plausible wire stories outnumber
                           true changes (change 0.05; rumour 0.30 per outlet page, naming a next version other than the one
                           that comes next; wire 0.12 with
                           0.8 of them plausible); a supersession rule is expected to do worst here
  s-echo                   heavy own outputs and echoes (4 per cycle, up to 3 echo pages per look, 0.25 echo-only
                           looks) and a third of the outputs prescient: the false-rejection world for echo recognition
What these worlds cannot show: anything about the open web, real outlets, real syndication or a real answer model;
every page is one sentence from a template, read through the real modules.
"""
from __future__ import annotations

import random

from .cycles_splits import ATTRIBUTION, COPY_HOSTS, ECHO_HOST, OUTLETS, WIRE, version, _names

ECHO_HOSTS = (ECHO_HOST, "askboard.example", "chatlog-digest.example", "reader-forum.example", "answer-roundup.example")
ECHO_SHAPES = {
    "assistant": "{N} {v} is the latest {N} release, an assistant reported.",
    "verbatim": "{N} {v} is the latest {N} release.",
    "forum": "Someone on the forum says {N} {v} is now the latest {N} release.",
    "quote": "\"{N} {v} is the latest {N} release,\" a chatbot told one reader.",
    "roundup": "Answers roundup: {N} {v} is the latest {N} release.",
}
WIRE_SHAPES = {
    "line": None,                                        # ATTRIBUTION line + the wire sentence (cycles_splits)
    "according": "According to Syndiwire, {N} {v} is the latest {N} release.",
    "says": "{N} {v} is now the latest {N} release, Syndiwire says.",
    "paren": "Release roundup: {N} {v} is the latest {N} release (Syndiwire).",
    "via": "{N} {v} is the newest {N} release, via Syndiwire.",
    "desk": "{N} {v} is the latest {N} release, as the Syndiwire desk reports.",
    "chain": None,                                       # attribution line naming another mirror + the wire sentence
    "bare": None,                                        # the wire sentence verbatim
    "unattributed": "Word is that {N} {v} is the latest {N} release.",
}
ORIGIN_SHAPES = ("line", "according", "bare")
TEAM = "{N} {v} is the latest {N} release, the {N} team says."

_W = {"line": 2, "according": 2, "says": 2, "paren": 1, "via": 1, "desk": 2, "chain": 1, "bare": 1, "unattributed": 0.5}

CYCLES = {
    "development": {
        "seeds": (241, 242, 243),
        "worlds": (
            {"name": "sd-mixed", "subjects": 12, "cycles": 12, "cycle_hours": 24.0, "age_days": (1.0, 6.0),
             "budget": 3, "arrivals": 1, "change": 0.10, "retract": 0.02, "outlets_per_look": 3, "lag": 0.2,
             "rumour": 0.06, "team": 0.15, "wire": 0.08, "wire_plausible": 0.5, "wire_only": 0.3, "copies": 3,
             "one_origin": 0.12, "copies_true": 3, "self": 2, "prescient": 0.2, "echo": 0.25, "echo_only": 0.08,
             "echo_pages": 2, "accepted_share": 0.4, "forget_cycle": 6},
            {"name": "sd-wrong-newer", "subjects": 12, "cycles": 12, "cycle_hours": 24.0, "age_days": (1.0, 6.0),
             "budget": 3, "arrivals": 1, "change": 0.05, "retract": 0.03, "outlets_per_look": 3, "lag": 0.25,
             "rumour": 0.30, "team": 0.1, "wire": 0.12, "wire_plausible": 0.8, "wire_only": 0.3, "copies": 3,
             "one_origin": 0.12, "copies_true": 3, "self": 2, "prescient": 0.1, "echo": 0.2, "echo_only": 0.05,
             "echo_pages": 2, "accepted_share": 0.4, "forget_cycle": 7},
        ),
    },
    "held_out": {
        "seeds": (341, 342, 343, 344),
        "worlds": (
            {"name": "s-mixed", "subjects": 14, "cycles": 14, "cycle_hours": 24.0, "age_days": (1.0, 6.5),
             "budget": 3, "arrivals": 1, "change": 0.10, "retract": 0.02, "outlets_per_look": 3, "lag": 0.2,
             "rumour": 0.06, "team": 0.15, "wire": 0.08, "wire_plausible": 0.5, "wire_only": 0.3, "copies": 3,
             "one_origin": 0.12, "copies_true": 3, "self": 2, "prescient": 0.2, "echo": 0.25, "echo_only": 0.08,
             "echo_pages": 2, "accepted_share": 0.4, "forget_cycle": 8},
            {"name": "s-wrong-newer", "subjects": 14, "cycles": 14, "cycle_hours": 24.0, "age_days": (1.0, 6.5),
             "budget": 3, "arrivals": 1, "change": 0.05, "retract": 0.03, "outlets_per_look": 3, "lag": 0.25,
             "rumour": 0.30, "team": 0.1, "wire": 0.12, "wire_plausible": 0.8, "wire_only": 0.3, "copies": 3,
             "one_origin": 0.12, "copies_true": 3, "self": 2, "prescient": 0.1, "echo": 0.2, "echo_only": 0.05,
             "echo_pages": 2, "accepted_share": 0.4, "forget_cycle": 9},
            {"name": "s-echo", "subjects": 12, "cycles": 14, "cycle_hours": 24.0, "age_days": (1.0, 6.5),
             "budget": 3, "arrivals": 1, "change": 0.10, "retract": 0.0, "outlets_per_look": 3, "lag": 0.15,
             "rumour": 0.03, "team": 0.15, "wire": 0.05, "wire_plausible": 0.5, "wire_only": 0.25, "copies": 3,
             "one_origin": 0.1, "copies_true": 3, "self": 4, "prescient": 0.33, "echo": 0.5, "echo_only": 0.25,
             "echo_pages": 3, "accepted_share": 0.4, "forget_cycle": 8},
        ),
    },
}


def _stream(spec, seed, *parts):
    return random.Random(":".join(["cycles-s", spec["name"], str(seed), *map(str, parts)]))


def _shape(rng, weights):
    names = list(weights)
    return rng.choices(names, weights=[weights[n] for n in names])[0]


def _wire_pages(rng, spec, name, value, at, c):
    """The wire story and `copies` copies of it, each copy in one syndication shape."""
    text = WIRE[2].format(N=name, v=value)
    pages = [{"host": WIRE[0], "path": f"/{name.lower()}-{at}", "text": text, "published_cycle": at, "kind": "wire",
              "variant": "wire"}]
    hosts = rng.sample(COPY_HOSTS, min(spec["copies"], len(COPY_HOSTS)))
    for n, h in enumerate(hosts):
        shape = _shape(rng, _W)
        published = at
        if shape == "line":
            body = ATTRIBUTION.format(S=WIRE[1]) + "\n" + text
        elif shape == "chain":
            source = hosts[n - 1] if n else COPY_HOSTS[(COPY_HOSTS.index(h) + 1) % len(COPY_HOSTS)]
            label = " ".join(w.capitalize() for w in source.split(".")[0].split("-"))
            body = ATTRIBUTION.format(S=label) + "\n" + text
        elif shape == "bare":
            body = text
        else:
            body = WIRE_SHAPES[shape].format(N=name, v=value)
            if shape == "desk":
                published = c                          # a mirror republished later: a newer date, the same story
        pages.append({"host": h, "path": f"/wire-{name.lower()}-{at}", "text": body, "published_cycle": published,
                      "kind": "wire_copy", "variant": shape})
    return pages


def generate(spec: dict, seed: int) -> dict:
    """The whole world of one run, written before the individual runs (see the module docstring). Truth, the wire,
    own outputs and each look draw from their own keyed streams."""
    n_cycles = spec["cycles"]
    total = spec["subjects"] + spec["arrivals"] * n_cycles
    names = _names(_stream(spec, seed, "names"), total)
    subjects = []
    for i, name in enumerate(names):
        rng = _stream(spec, seed, "truth", name)
        born = 0 if i < spec["subjects"] else 1 + (i - spec["subjects"]) // spec["arrivals"]
        minor = rng.randint(2, 8)
        truth, changed_at, since = {}, {}, born
        for c in range(0, n_cycles + 1):
            if c > born:
                roll = rng.random()
                if roll < spec["change"]:
                    minor += 1
                    since = c
                elif roll < spec["change"] + spec["retract"] and minor > 1:
                    minor -= 1                         # a release pulled: the truth goes back one version
                    since = c
            if c >= born:
                truth[c] = minor
                changed_at[c] = since
        lo, hi = spec["age_days"]
        subjects.append({"name": name, "born": born, "truth": truth, "since": changed_at,
                         "age_days": rng.uniform(lo, hi) if born == 0 else 0.0})
    by_name = {s["name"]: s for s in subjects}

    def next_change(s, c):
        """The first version after cycle c that differs from the truth at c (None when none comes)."""
        return next((s["truth"][d] for d in range(c + 1, n_cycles + 1) if s["truth"][d] != s["truth"][c]), None)

    wire_out, self_values, looks, self_by_cycle, echo_ready = {}, {}, {}, {}, {}
    wire_rng = {s["name"]: _stream(spec, seed, "wire", s["name"]) for s in subjects}
    for c in range(0, n_cycles + 1):
        live = [s for s in subjects if s["born"] <= c]
        srng = _stream(spec, seed, "self", c)
        chosen = srng.sample(live, min(spec["self"], len(live))) if c >= 1 else []
        self_by_cycle[c] = []
        for s in chosen:
            ahead = next_change(s, c)
            if ahead is not None and ahead > s["truth"][c] and srng.random() < spec["prescient"]:
                value, kind = version(ahead), "prescient"
            else:
                value, kind = version(srng.randint(1, 9), major=9), "self"
            self_values[s["name"]] = value
            self_by_cycle[c].append({"subject": s["name"], "value": value, "kind": kind})
        for s in live:
            name, truth = s["name"], s["truth"][c]
            wr = wire_rng[name]
            if wr.random() < spec["wire"]:
                plausible = wr.random() < spec["wire_plausible"]
                wire_out[name] = (version(truth + 1) if plausible else version(wr.randint(1, 9), major=7), c,
                                  plausible)
            elif name in wire_out and wr.random() < 0.5:
                del wire_out[name]
            wire = wire_out.get(name)
            if wire is not None and wire[2] and version(truth) == wire[0]:
                del wire_out[name]                     # a plausible story that came true is no longer an error
                wire = None
            rng = _stream(spec, seed, "look", name, c)
            pages = []
            roll = rng.random()
            echo = echo_ready.get(name)
            if wire is not None and rng.random() < spec["wire_only"]:
                kind = "wire_only"
            elif echo is not None and rng.random() < spec["echo_only"]:
                kind = "echo_only"
            elif roll < spec["one_origin"]:
                kind = "one_origin"
            else:
                kind = "normal"
            if kind == "one_origin":
                host, label, template = OUTLETS[rng.randrange(len(OUTLETS))]
                text = template.format(N=name, v=version(truth))
                pages.append({"host": host, "path": f"/{name.lower()}", "text": text,
                              "published_cycle": s["since"][c], "kind": "honest", "variant": "origin"})
                for h in rng.sample(COPY_HOSTS, min(spec["copies_true"], len(COPY_HOSTS))):
                    shape = ORIGIN_SHAPES[rng.randrange(len(ORIGIN_SHAPES))]
                    body = (ATTRIBUTION.format(S=label) + "\n" + text if shape == "line" else
                            f"According to {label}, {name} {version(truth)} is the latest {name} release."
                            if shape == "according" else text)
                    pages.append({"host": h, "path": f"/{name.lower()}-{c}", "text": body,
                                  "published_cycle": s["since"][c], "kind": "copy", "variant": shape})
            if kind == "normal":
                ahead = next_change(s, c)
                for host, _label, template in rng.sample(OUTLETS, spec["outlets_per_look"]):
                    lagging = s["since"][c] > s["born"] and rng.random() < spec["lag"]
                    rumour = not lagging and rng.random() < spec["rumour"]
                    team = rng.random() < spec["team"]
                    if lagging:
                        prev = s["truth"][s["since"][c] - 1]
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": (TEAM if team else template).format(N=name, v=version(prev)),
                                      "published_cycle": max(s["born"], s["since"].get(s["since"][c] - 1, s["born"])),
                                      "kind": "lag", "variant": "team" if team else "outlet"})
                    elif rumour and ahead != truth + 1:
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": template.format(N=name, v=version(truth + 1)), "published_cycle": c,
                                      "kind": "rumour", "variant": "outlet"})
                    else:
                        pages.append({"host": host, "path": f"/{name.lower()}",
                                      "text": (TEAM if team else template).format(N=name, v=version(truth)),
                                      "published_cycle": s["since"][c], "kind": "honest",
                                      "variant": "team" if team else "outlet"})
            if wire is not None and kind in ("normal", "wire_only"):
                pages += _wire_pages(rng, spec, name, wire[0], wire[1], c)
            if echo is not None and (kind == "echo_only" or (kind == "normal" and rng.random() < spec["echo"])):
                for n, h in enumerate(rng.sample(ECHO_HOSTS, rng.randint(1, spec["echo_pages"]))):
                    shape = list(ECHO_SHAPES)[rng.randrange(len(ECHO_SHAPES))]
                    pages.append({"host": h, "path": f"/{name.lower()}-{c}-{n}",
                                  "text": ECHO_SHAPES[shape].format(N=name, v=echo), "published_cycle": c,
                                  "kind": "echo", "variant": shape})
            looks[(name, c)] = {"kind": kind, "pages": pages}
        for item in self_by_cycle[c]:
            echo_ready[item["subject"]] = item["value"]    # an echo can quote it from the next cycle on
    frng = _stream(spec, seed, "forgets")
    forgets = {spec["forget_cycle"]: [WIRE[0], OUTLETS[frng.randrange(len(OUTLETS))][0]]}
    arng = _stream(spec, seed, "accepted")
    accepted = {s["name"]: arng.random() < spec["accepted_share"] for s in subjects}
    assert set(by_name) == set(accepted)
    return {"spec": spec, "seed": seed, "subjects": subjects, "looks": looks, "self": self_by_cycle,
            "forgets": forgets, "accepted": accepted, "self_values": self_values}
