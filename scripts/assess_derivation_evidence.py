#!/usr/bin/env python3
"""Wave 4 lane S (ADR-066): which kinds of derivation evidence separate copies from independent reports, measured
on the source-independence family's DEVELOPMENT worlds before any rule is built (held-out only after the verdict rule
is committed; the withheld worlds never). Read-only; no individual is created.

Every detector reads only what the product sees of a page: its text, host, canonical URL, links and stated publication
time (`sources._document`), and the values the real extractor reads (`claims.extract_claims`). The truth is the
derivation graph: two read pages are the same source when their roots intersect. Pairs are pairs of read pages that
yielded a claim and state the same value (the only pairs a grouping can change the support count of), split by the
shape the look staged and by whether today's grouping (`claims.independence_groups` at the defaults) already joins
them. For each detector: of the same-source pairs today does NOT join, the share it flags (what it could add), and of
the independent pairs, the share it flags (what it would merge wrongly), plus a threshold-free separation (AUC) for the
graded ones. No detector uses a list of phrases, hosts or names, or anything specific to the generator.

  time_equal     the two pages state the same publication time
  time_close     their publication times differ by at most one day
  text_overlap   order-preserving overlap of content tokens (longest common subsequence over the shorter page's content
                 tokens, the subject's name and every number left out): graded, AUC; flagged at >= 0.8
  rare_shared    a word 2-gram both pages contain that no other page of the look contains (subject's name and numbers
                 left out): flagged when one exists
  cites_any_case one page names the other page's outlet: some run of 1-4 consecutive words of its text, letters and
                 digits only, lower-cased and joined, equals the other page's site label so written (the product's own
                 identity key, `claims._identity_keys`), at least MIN_KEY characters; or one page links to the other's
                 URL or host
  cites          the same, but the words must be capitalised and not a sentence's first word alone (a page saying
                 "Today, ..." does not name a host labelled "today")
  cites_in_time  `cites`, and the cited page is not stated as published after the citing page
  shared_name    both pages name a capitalised run of words (not a sentence's first word alone, not the subject's name,
                 not either page's own site label) that no read page of the look owns as its site label. The subject's
                 name is taken from the world here (the product would have to find it), so this is an upper bound; and
                 these pages name nothing but their sources and the subject, so a shared name means a shared source
                 here more often than it would on real pages, where honest outlets share the names of the things they
                 write about

Usage: scripts/assess_derivation_evidence.py [--split development] [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MIN_KEY = 4
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _words(text: str) -> list[str]:
    return WORD.findall(text or "")


def _runs(text: str, n: int = 4) -> set:
    w = [_key(x) for x in _words(text)]
    return {"".join(w[i:i + k]) for i in range(len(w)) for k in range(1, n + 1) if i + k <= len(w)}


def _content(text: str, subject: str) -> list[str]:
    from neuroscifi.webcortex import text as T
    skip = {_key(subject)}
    return [t for t in (_key(x) for x in _words(text)) if t and t not in T.STOPWORDS and t not in skip
            and not t.isdigit() and not re.fullmatch(r"v?\d+", t)]


def _lcs(a: list, b: list) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _capital_runs(text: str, n: int = 4) -> set:
    """Keys of 1-4 consecutive capitalised words; a sentence's first word alone is not one."""
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        words = list(re.finditer(r"[^\W_][\w'’&-]*", sentence))
        for i in range(len(words)):
            for k in range(1, n + 1):
                run = words[i:i + k]
                if len(run) < k or not all(w.group(0)[0].isupper() for w in run) or (k == 1 and i == 0):
                    continue
                out.add(_key(" ".join(w.group(0) for w in run)))
    return out


def _names(text: str, subject: str, own: str) -> set:
    """Capitalised runs of words; a sentence's first word alone is not a name."""
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        words = list(re.finditer(r"[^\W_][\w'’&-]*", sentence))
        run = []
        for i, m in enumerate(words + [None]):
            if m is not None and m.group(0)[0].isupper():
                run.append((i, m.group(0)))
                continue
            if run and not (len(run) == 1 and run[0][0] == 0):
                key = _key(" ".join(w for _, w in run))
                if len(key) >= MIN_KEY and key not in (_key(subject), own):
                    out.add(key)
            run = []
    return out


def _auc(pos: list, neg: list) -> float | None:
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def assess(split: str) -> dict:
    from neuroscifi.acceptance.cognition import sources
    from neuroscifi.webcortex import claims
    spec, generate = sources._public(split)
    flags = ("today", "time_equal", "time_close", "text_overlap", "rare_shared", "cites_any_case", "cites",
             "cites_in_time", "shared_name")
    # per shape: counts of same-source pairs (all, not joined today, flagged among those) and independent pairs
    table = collections.defaultdict(lambda: collections.Counter())
    graded = collections.defaultdict(lambda: {"same": [], "independent": []})
    for world in spec["worlds"]:
        for seed in spec["seeds"]:
            g = generate(world, seed)
            for c in range(world["cycles"] + 1):
                now = sources.T0 + c * world["cycle_hours"] * 3600.0
                for name in g["schedule"][c]:
                    look = g["looks"][(name, c)]
                    docs = [sources._document(p, now, world, name) for p in look["pages"]]
                    found, value_of = [], {}
                    for p, d in zip(look["pages"], docs):
                        got = claims.extract_claims(d, f"What is the latest {name} release?")
                        vals = {sources.norm(v) for x in got for v in (x.values or ())} - {None}
                        if got:
                            found += got
                            value_of[sources._key(p)] = vals
                    if not found:
                        continue
                    claims.stamp_origins(found, docs)
                    group_of = {}
                    for n, grp in enumerate(claims.independence_groups(
                            [x for x in found if not claims.is_adversarial(x)])):
                        for x in found:
                            if x.id in grp:
                                group_of.setdefault(x.source_url, n)
                    read = [(p, d) for p, d in zip(look["pages"], docs) if sources._key(p) in value_of]
                    labels = {sources._key(p): _key(claims.site_label(p["host"])) for p, _ in read}
                    runs = {sources._key(p): _runs(p["text"]) for p, _ in read}
                    caps = {sources._key(p): _capital_runs(p["text"]) for p, _ in read}
                    names = {sources._key(p): _names(p["text"], name, labels[sources._key(p)]) for p, _ in read}
                    owned = set(labels.values())
                    bigrams = {}
                    for p, _ in read:
                        w = _content(p["text"], name)
                        bigrams[sources._key(p)] = {(a, b) for a, b in zip(w, w[1:])}
                    df = collections.Counter(b for s in bigrams.values() for b in s)
                    for i, (pa, da) in enumerate(read):
                        for pb, db in read[i + 1:]:
                            ka, kb = sources._key(pa), sources._key(pb)
                            if not (value_of[ka] & value_of[kb]):
                                continue
                            same = bool(set(pa["roots"]) & set(pb["roots"]))
                            today = da.url in group_of and group_of.get(da.url) == group_of.get(db.url)
                            ta, tb = da.published_at, db.published_at

                            def cites(x, y, dx, dy, kx, ky, table=caps):
                                lab = labels[ky]
                                by_name = len(lab) >= MIN_KEY and lab in table[kx]
                                by_link = any(claims._host(u) == dy.domain or u == dy.url for u in dx.links or ())
                                return by_name or by_link
                            c_ab, c_ba = cites(pa, pb, da, db, ka, kb), cites(pb, pa, db, da, kb, ka)
                            any_case = cites(pa, pb, da, db, ka, kb, runs) or cites(pb, pa, db, da, kb, ka, runs)
                            in_time = (c_ab and (tb is None or ta is None or tb <= ta)) or \
                                      (c_ba and (ta is None or tb is None or ta <= tb))
                            shared = (names[ka] & names[kb]) - owned
                            ca, cb = _content(pa["text"], name), _content(pb["text"], name)
                            overlap = _lcs(ca, cb) / min(len(ca), len(cb)) if ca and cb else 0.0
                            rare = any(df[b] == 2 for b in bigrams[ka] & bigrams[kb])
                            got = {"today": today, "time_equal": ta == tb,
                                   "time_close": ta is not None and tb is not None and abs(ta - tb) <= 86400.0,
                                   "text_overlap": overlap >= 0.8, "rare_shared": rare, "cites_any_case": any_case, "cites": c_ab or c_ba,
                                   "cites_in_time": in_time, "shared_name": bool(shared)}
                            for shape in (look["shape"], "all"):
                                row = table[shape]
                                kind = "same" if same else "independent"
                                row[f"{kind}_pairs"] += 1
                                if not today:
                                    row[f"{kind}_not_today"] += 1
                                for f in flags:
                                    if got[f]:
                                        row[f"{kind}:{f}"] += 1
                                        if not today:
                                            row[f"{kind}_not_today:{f}"] += 1
                                if not today:
                                    graded[shape][kind].append(overlap)
    out = {}
    for shape, row in sorted(table.items()):
        entry = {"same_source_pairs": row["same_pairs"], "independent_pairs": row["independent_pairs"],
                 "same_source_not_joined_today": row["same_not_today"],
                 "independent_joined_today": row["independent_pairs"] - row["independent_not_today"]}
        for f in flags[1:]:
            entry[f] = {"adds_of_same_not_joined_today": row[f"same_not_today:{f}"],
                        "flags_independent": row[f"independent:{f}"],
                        "flags_independent_not_joined_today": row[f"independent_not_today:{f}"]}
        entry["text_overlap"]["auc_not_joined_today"] = _auc(graded[shape]["same"], graded[shape]["independent"])
        out[shape] = entry
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="development", choices=("development", "held_out"))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    result = {"split": a.split, "min_key": MIN_KEY, "by_shape": assess(a.split)}
    text = json.dumps(result, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
