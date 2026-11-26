"""Wave 3 fix lane: the security review's findings on lane S's opt-in rules (ADR-061), each with a test that fails
without its fix. Both rules ship OFF (decisions D1, D2); these make the settings sound when the owner turns them on."""
import pytest

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.webcortex import claims
from neuroscifi.webcortex.types import Document

T0, DAY, N = adv.T0, adv.DAY, adv._SN
HONEST = [("devdesk.example", f"By now {N} 1.5.0 is the latest {N} release.", T0 - DAY, None),
          ("factline.example", f"Once again {N} 1.5.0 is the latest {N} release.", T0 - DAY, None),
          ("weekly-builds.example", f"As of now, {N} 1.5.0 is the latest {N} release.", T0 - DAY, None)]


def _rows(pages, **settings):
    with adv._s_individual("h1", **settings) as (db, _):
        adv._s_look(db, T0, pages)
        return [r["status"] for r in adv._s_rows(db, "1.5.0")]


def _groups(pages, attribution=True):
    docs = [Document(url=f"http://{h}{p or '/x'}", final_url=f"http://{h}{p or '/x'}",
                     canonical_url=f"http://{h}{p or '/x'}", domain=h, title=N, text=t, retrieved_at=T0,
                     retrieval_mode="live", published_at=at) for h, t, at, p in pages]
    found = [c for d in docs for c in claims.extract_claims(d, adv._SQ) if "v1.5.0" in (c.values or [])]
    claims.stamp_origins(found, docs, attribution=attribution)
    return claims.independence_groups(found)


# ---- H1: a credit never joins two groups -----------------------------------------------------------------------------

BRIDGE = [("hostile.example", f"According to Devdesk, {N} 1.5.0 is the latest {N} release, Factline says.", T0 - DAY,
           "/a")]


def test_one_sentence_crediting_two_honest_outlets_does_not_merge_them():
    assert _rows(HONEST[:2]) == ["provisional"]
    assert _rows(HONEST[:2] + BRIDGE) == ["provisional"]          # before the fix: no row (one source)
    assert len(_groups(HONEST[:2] + BRIDGE)) == 2


def test_a_hostile_host_crediting_each_honest_outlet_merges_nothing():
    bridge = [("hostile.example", f"According to Devdesk, {N} 1.5.0 is the latest {N} release.", T0 - DAY, "/a"),
              ("hostile.example", f"{N} 1.5.0 is the newest {N} release, Factline says.", T0 - DAY, "/b"),
              ("hostile.example", f"Release note: {N} 1.5.0 is the latest {N} release (Weekly-Builds).", T0 - DAY,
               "/c")]
    assert len(_groups(HONEST + bridge)) == len(_groups(HONEST)) == 3
    four = [(f"h{i}.example", f"Line {i}: {N} 1.5.0 is the latest {N} release.", T0 - DAY, None) for i in range(4)]
    roundup = [("hostile.example", f"According to H0, {N} 1.5.0 is the latest {N} release, H1 says, as H2 reports "
                                   f"(H3).", T0 - DAY, "/z")]
    assert len(_groups(four + roundup)) == 4


def test_copies_still_count_with_the_outlet_they_credit():
    story = [("syndiwire.example", f"{N} 1.5.0 is the latest {N} release, the Syndiwire desk reports.", T0 - DAY,
              "/w")]
    copies = [("mirror-one.example", f"According to Syndiwire, {N} 1.5.0 is the latest {N} release.", T0 - DAY, None),
              ("mirror-two.example", f"{N} 1.5.0 is now the latest {N} release, Syndiwire says.", T0 - DAY, None)]
    assert len(_groups(story + copies)) == 1 and len(_groups(story + copies, attribution=False)) == 3
    assert len(_groups(copies)) == 1          # the credited outlet was not read: the copies are still one source


# ---- S-3: the possessive credit (a bug fix only; the off-parser residual is measured in genesis_wave3_fix_s.json)

def test_a_possessive_credit_names_the_outlet():
    assert claims.attribution_keys(f"{N} 7.3.0 is the latest {N} release, Syndiwire's desk says.") == \
        ["name:syndiwire"]
    assert claims.attribution_keys(f"{N} 7.3.0 is the latest {N} release, Syndiwire’s editors said.") == \
        ["name:syndiwire"]
