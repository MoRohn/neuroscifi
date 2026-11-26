"""Wave 4, second fix round: what the reviews' second passes found (review4 security and measurement, second pass).
Each case fails on the tree before its fix (71e5672) and passes with it."""
from contextlib import contextmanager
import json

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.webcortex import emitted

T0, DAY, N = adv.T0, adv.DAY, adv._SN
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."
ECHOES = [f"By now {N} 9.4.0 is the latest {N} release.", f"Once again {N} 9.4.0 is the latest {N} release."]


@contextmanager
def _own(name, **settings):
    with adv._s_individual(name, **({"attribution_independence": False, "echo_recognition": False,
                                     "own_text_provenance": True} | settings)) as (db, path):
        yield db, path


def _pages(texts, published):
    return [(f"site{i}.example", t, published, None) for i, t in enumerate(texts)]


def _rows(db, value="9.4.0"):
    return [r["status"] for r in adv._s_rows(db, value) if N in r["statement"]]


def _emissions(db):
    return [json.loads(s)["e"] for (s,) in db.execute("SELECT state FROM web_strategies WHERE name LIKE 'emitted:%'")]


# ---- item 1 (security, second pass, 2b(ii)): an expired emission never masks a fresh one ----------------------------

def test_a_fresh_emission_counts_though_the_pairs_first_emission_has_expired():
    """review4 s/r2/h3_expiry: emitted on day 0 and again on day 40, echoed on day 42: an echo."""
    with _own("expiry-fresh") as (db, _):
        adv._s_look(db, T0, [], answer=ANSWER)
        adv._s_look(db, T0 + 40 * DAY, [], answer=ANSWER)
        out = adv._s_look(db, T0 + 42 * DAY, _pages(ECHOES, T0 + 42 * DAY - 3600))
        assert len(out.get("echoes") or []) == 2 and _rows(db) == []


def test_expired_emissions_are_dropped_before_the_list_is_cut_to_its_maximum():
    """Three emissions (the most kept) on days 0 to 2, a fourth on day 40: the fourth is kept and counts on day 42;
    the expired ones are gone, and so are their tasks' lists."""
    with _own("expiry-full") as (db, _):
        for day in range(emitted.MAX_EMISSIONS):
            adv._s_look(db, T0 + day * DAY, [], answer=ANSWER)
        adv._s_look(db, T0 + 40 * DAY, [], answer=ANSWER)
        assert [[e[0] for e in row] for row in _emissions(db)] == [[T0 + 40 * DAY]]
        tasks = db.execute("SELECT count(*) FROM web_strategies WHERE name LIKE 'emitted-task:%'").fetchone()[0]
        assert tasks == 1
        out = adv._s_look(db, T0 + 42 * DAY, _pages(ECHOES, T0 + 42 * DAY - 3600))
        assert len(out.get("echoes") or []) == 2 and _rows(db) == []

