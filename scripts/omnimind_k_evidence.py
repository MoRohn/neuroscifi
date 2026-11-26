"""OMNIMIND lane K evidence: detection over the lane's own hand-assembled corpus (tests/artifacts/detect_corpus.py),
its truncated prefixes, conflicting hints, and seeded random bytes; what the decode chain does with each corpus item.

    python scripts/omnimind_k_evidence.py [--output docs/quality/evidence/omnimind_k.json] [--check]

Deterministic (no timings, seeded randomness): --check regenerates and compares with the committed file. A "false
identification" is any answer naming a format that is neither the true one, nor a more general name for it (its
container, or the format it extends), nor "text" for a prefix that really is text, nor "unknown"."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tests" / "artifacts"))
from detect_corpus import conflicts, corpus  # noqa: E402

from neuroscifi.artifacts import adapters  # noqa: E402
from neuroscifi.artifacts.detect import detect  # noqa: E402
from neuroscifi.artifacts.types import ArtifactError  # noqa: E402

GENERAL = {"docx": {"zip"}, "xlsx": {"zip"}, "pptx": {"zip"}, "odt": {"zip"}, "ods": {"zip"}, "odp": {"zip"},
           "epub": {"zip"}, "jar": {"zip"}, "apk": {"zip"}, "npz": {"zip"}, "doc": {"ole"}, "xls": {"ole"},
           "ppt": {"ole"}, "ipynb": {"json"}, "geojson": {"json"}, "ndjson": {"json"}, "atom": {"xml"},
           "rss": {"xml"}, "kml": {"xml"}, "gpx": {"xml"}, "plist": {"xml"}, "svg": {"xml"}}
CUTS = (1, 2, 3, 4, 8, 16, 32, 64, 128, 512)
RANDOM_SEED, RANDOM_N = 20261126, 20000


def build() -> dict:
    items = corpus()
    by_basis, by_family, wrong_full = Counter(), Counter(), []
    for label, data, kw, fmt, basis in items:
        d = detect(data, **{"size": len(data), **kw})
        by_basis[d.basis] += 1
        by_family[d.family] += 1
        if (d.format, d.basis) != (fmt, basis):
            wrong_full.append(label)
    conflict_wrong = [c[0] for c in conflicts()
                      if (lambda d: (d.format, d.basis))(detect(c[1], **{"size": len(c[1]), **c[2]})) != (c[3], c[4])]
    outcomes, false_ids, tried = {"size_given": Counter(), "size_unknown": Counter()}, [], 0
    for label, data, kw, fmt, _ in items:
        for n in CUTS + (max(1, len(data) // 2),):
            for size in (None, len(data)):
                tried += 1
                got = detect(data[:n], name=kw.get("name"), declared=kw.get("declared"), size=size).format
                kind = ("exact" if got == fmt else "general" if got in GENERAL.get(fmt, ()) else got
                        if got in ("text", "unknown") else "false")
                outcomes["size_unknown" if size is None else "size_given"][kind] += 1
                if kind == "false":
                    false_ids.append([label, n, got])
    rng = np.random.default_rng(RANDOM_SEED)
    random_out, text_lengths = Counter(), []
    for _ in range(RANDOM_N):
        n = int(rng.integers(1, 4096))
        got = detect(rng.bytes(n), size=n).format
        random_out["unknown" if got == "unknown" else "text" if got == "text" else "false"] += 1
        if got == "text":
            text_lengths.append(n)
    decoded, opaque, refused = Counter(), Counter(), Counter()
    for label, data, kw, fmt, _ in items:
        try:
            out = adapters.decode(data, name=kw.get("name"), declared=kw.get("declared"))
            (opaque if out.representation.kind == "opaque" else decoded)[fmt] += 1
        except ArtifactError as error:
            refused[f"{fmt}:{error.reason}"] += 1
    return {
        "lane": "K", "what": "artifact detection over lane K's hand-assembled corpus (tests/artifacts/detect_corpus.py)",
        "corpus": {"items": len(items), "distinct_formats": len({c[3] for c in items}),
                   "identified_as_constructed": len(items) - len(wrong_full), "wrong": wrong_full,
                   "by_basis": dict(sorted(by_basis.items())), "by_family": dict(sorted(by_family.items()))},
        "conflicting_hints_and_adversarial": {"items": len(conflicts()), "wrong": conflict_wrong},
        "truncated_prefixes": {"tried": tried, "cuts_bytes": list(CUTS) + ["half"], "outcomes": {k: dict(sorted(v.items())) for k, v in outcomes.items()},
                               "note": ("with the size unknown, a short prefix of a binary format that happens to be "
                                        "printable (b'GIF8', b'PK') is called text, which those bytes are; with the "
                                        "size given, prefixes under 32 bytes of a longer input get no structure "
                                        "verdict"),
                               "false_identifications": false_ids},
        "random_bytes": {"seed": RANDOM_SEED, "tried": RANDOM_N, "lengths": [1, 4095],
                         "outcomes": dict(sorted(random_out.items())),
                         "text_note": (f"inputs called text are {min(text_lengths)}-{max(text_lengths)} bytes of "
                                       "printable ASCII, which are valid text") if text_lengths else "none",
                         "text_lengths": sorted(text_lengths)},
        "decode_chain_on_corpus": {"decoded": dict(sorted(decoded.items())), "opaque": sum(opaque.values()),
                                   "opaque_formats": sorted(opaque), "refused": dict(sorted(refused.items()))},
        "catalog": [{k: r[k] for k in ("name", "formats", "available")} for r in adapters.catalog()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=BASE / "docs/quality/evidence/omnimind_k.json")
    parser.add_argument("--check", action="store_true", help="compare with the committed file instead of writing")
    args = parser.parse_args()
    text = json.dumps(build(), indent=1, sort_keys=True) + "\n"
    if args.check:
        same = args.output.read_text() == text
        print("omnimind_k evidence: " + ("identical" if same else "DIFFERENT"))
        return 0 if same else 1
    args.output.write_text(text)
    print(f"wrote {args.output} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
