"""Lane FX3's own relater corpus (ADR-081): generated twins for the classes the spent cross cases found, with traps
whose right answer is "cannot", so that a rule that brought guessing back would show as wrong answers.

    PYTHONPATH=src python scripts/omnimind_fx3/corpus_relate.py OUT.json

Every file is generated here from seeds 31000-31999 (outside every reserved range of acceptance/omni) by the builders
of tests/artifacts/test_fx3_relate.py and the cross family's public generators; the truth is known by construction
(True, False, or None where the relation cannot be measured). This is development evidence written by the lane that
wrote the rules, not an examination. Prints and writes counts only: right, cannot, wrong per class.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "artifacts"))

import test_fx3_relate as T  # noqa: E402
from neuroscifi.acceptance.omni import cross  # noqa: E402
from neuroscifi.acceptance.omni.subject import relate  # noqa: E402

SEEDS = range(31000, 32000)
N = 12          # twins per class


def items():
    out = []                                  # (class, files, question, expected)
    rng = random.Random(SEEDS[0])
    q_members = ["Do the archive's files match this list?", "Is the listing correct for the package?",
                 "Does each file agree with the manifest?", "Check the list against the archive."]
    for i in range(N):
        files = {f"d{k}/f{i}_{k}.bin": bytes(rng.randrange(256) for _ in range(rng.randrange(20, 400)))
                 for k in range(rng.randrange(2, 6))}
        algo = ("sha256", "sha1", "md5")[i % 3]
        z = T._zip(files)
        q = q_members[i % 4]
        target = sorted(files)[i % len(files)]
        out.append(("members_digest", {"a.zip": z, "SUMS.txt": T._sums(files, algo)}, q, True))
        out.append(("members_digest", {"a.zip": z, "SUMS.txt": T._sums(files, algo, spoil=target)}, q, False))
        sizes = "".join(f"{len(b)}  {n}\n" for n, b in files.items()).encode()
        bigger = sizes.replace(f"{len(files[target])}  {target}".encode(), f"{len(files[target]) + 3}  {target}".encode())
        out.append(("members_size", {"a.zip": z, "list.txt": sizes}, q, True))
        out.append(("members_size", {"a.zip": z, "list.txt": bigger}, q, False))
        other = {f"z/{k}.dat": bytes([k]) * 9 for k in range(3)}
        out.append(("trap_listing_of_other_files", {"a.zip": z, "SUMS.txt": T._sums(other)}, q, None))
        out.append(("trap_digest_of_tar", {"a.tar": T._tar(files), "SUMS.txt": T._sums(files)}, q, None))
    verbs = ["leads to", "feeds", "reports to", "calls", "links to", "precedes"]
    q_links = ["Do the diagram and the text agree?", "Is the drawing the structure the notes describe?",
               "Are the joins in the figure the ones listed?"]
    for i in range(N):
        labels = list(T.LABELS)
        edges = [(labels[k], labels[k + 1]) for k in range(4)]
        rng.shuffle(edges)
        moved = list(edges)
        a, _ = moved[0]
        moved[0] = (a, next(x for x in labels if x != a and {a, x} not in [set(e) for e in edges]))
        svg = T._graph_svg(edges)
        q = q_links[i % 3]
        verb = verbs[i % len(verbs)]
        out.append(("links", {"g.svg": svg, "t.txt": T._edge_text(edges, verb)}, q, True))
        out.append(("links", {"g.svg": svg, "t.txt": T._edge_text(moved, verb)}, q, False))
        out.append(("trap_links_three_names", {"g.svg": svg, "t.txt": b"* Ash, Birch and Elm share a desk\n"
                                               + T._edge_text(edges, verb)}, q, None))
        out.append(("trap_links_line_off_nodes", {"g.svg": svg.replace(b"</svg>", b'<line x1="1" y1="210" x2="490" '
                                                                     b'y2="210"/></svg>'),
                                                  "t.txt": T._edge_text(edges, verb)}, q, None))
    q_times = ["Do the sounds start when the list says?", "Is the cue sheet right for this file?",
               "Are the onsets the listed times?"]
    for i in range(N):
        onsets, t = [], 0.6
        for _ in range(rng.randrange(3, 7)):
            t += rng.uniform(1.0, 2.2)
            onsets.append(round(t, 3))
        late = list(onsets)
        late[i % len(late)] = round(late[i % len(late)] + rng.uniform(0.3, 0.8), 3)
        wav = T._beeps(onsets, freq=rng.choice([440.0, 700.0, 990.0]))
        q = q_times[i % 3]
        out.append(("times", {"s.wav": wav, "c.txt": T._cues(onsets)}, q, True))
        out.append(("times", {"s.wav": wav, "c.txt": T._cues(late)}, q, False))
        clock = "".join(f"09:{10 + k:02d}:00 sound\n" for k in range(len(onsets))).encode()
        out.append(("trap_times_clock_of_day", {"s.wav": wav, "c.txt": clock}, q, None))
    q_size = ["Is the image the size the caption gives?", "Does the note state the picture's height correctly?",
              "Are the width and height in the text right?"]
    for i in range(N):
        w, h = rng.randrange(20, 160), rng.randrange(20, 160)
        img = T._png(w, h)
        q = q_size[i % 3]
        if i % 3 == 1:
            right, wrong = f"Tall label, {h} px from top to bottom.\n", f"Tall label, {h + 5} px from top to bottom.\n"
        else:
            right = f"It is {w} pixels wide and {h} pixels high.\n"
            wrong = f"It is {w} pixels wide and {h + 7} pixels high.\n"
        out.append(("image_axis", {"i.png": img, "n.txt": right.encode()}, q, True))
        out.append(("image_axis", {"i.png": img, "n.txt": wrong.encode()}, q, False))
        out.append(("trap_image_depiction", {"i.png": img, "n.txt": f"A grey whale, {w} pixels wide and {h} pixels "
                                             "high.\n".encode()}, "Does the text say what the image shows?", None))
        out.append(("trap_count_of_drawn_things", {"i.png": img, "n.txt": f"The card shows {3 + i % 4} squares, {w} "
                                                   f"by {h} pixels.\n".encode()},
                    "Is the number of squares in the note the number in the picture?", None))
    for i in range(N):
        n = rng.randrange(2, 9)
        note = f"On the way we made {n} stops.\n".encode()
        q = "Does the route file have as many stops as the note says?" if i % 2 else \
            "Is the number of stops in the note the number in the route?"
        out.append(("waypoints", {"r.gpx": T._gpx(n), "n.txt": note}, q, True))
        out.append(("waypoints", {"r.gpx": T._gpx(n + 1), "n.txt": note}, q, False))
    for i in range(N):
        vals = [rng.randrange(10, 90) for _ in range(rng.randrange(3, 8))]
        report = f"# Season\n\nThe crew planted {sum(vals)} trees in all.\n".encode()
        q = "Is the season total in the report what the log adds up to?"
        out.append(("count_as_total", {"r.md": report, "l.csv": T._plant_csv(vals)}, q, True))
        out.append(("count_as_total", {"r.md": report, "l.csv": T._plant_csv(vals[:-1] + [vals[-1] + 1])}, q, False))
    for i in range(N):
        mark = ("points", "line", "bars")[i % 3]
        knobs = {"svg": "c.svg", "csv": "c.csv", "series": "Arrivals", "x": "month", "n": rng.randrange(5, 12),
                 "mark": mark, "scale": "linear"}
        q = ["Do the marks show the values in the sheet?", "Was this line traced from the arrival counts?",
             "Do the dots match the readings?"][i % 3]
        for matching in (True, False):
            files = cross._BUILD["chart_series"](knobs, random.Random(SEEDS[0] + 500 + i), matching, "csv")
            out.append(("chart_words", files, q, matching))
    return out


def main(argv=None) -> int:
    out_path = (argv or sys.argv[1:] or [None])[0]
    per: dict[str, dict] = {}
    for cls, files, question, expected in items():
        answer = relate(dict(files), question)["answer"]
        answer = answer if isinstance(answer, bool) else None
        res = "right" if answer is expected else ("cannot" if answer is None else "wrong")
        c = per.setdefault(cls, {"right": 0, "cannot": 0, "wrong": 0})
        c[res] += 1
    total = {k: sum(c[k] for c in per.values()) for k in ("right", "cannot", "wrong")}
    doc = {"total": total, "per_class": per, "n": sum(total.values()), "seeds": [SEEDS.start, SEEDS.stop - 1]}
    print(json.dumps(doc["total"]), doc["n"])
    if out_path:
        Path(out_path).write_text(json.dumps(doc, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
