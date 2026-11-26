"""Wave 4 integration: lane S's echo hook (claims.stamp_origins(echo=...), ADR-066) fed by lane E's rule
(webcortex/emitted.py, ADR-067). With source_derivation AND own_text_provenance on, a page that repeats a value the
individual emitted with no source counts for nothing in the derivation grouping; with either alone, or both off, the
grouping is what it was before the two lanes were wired (no echo mark; with own_text_provenance alone no derivation
mark at all)."""
from datetime import datetime, timezone

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.instance import store
from neuroscifi.webcortex import claims, emitted, orchestrator, services as svc_mod, state
from neuroscifi.webcortex.providers.corpus import CorpusProvider

T0, DAY, N = adv.T0, adv.DAY, adv._SN
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."
# an outlet published before the answer (never its echo) and a page published after it repeating it (its echo)
HONEST = ("techledger.example", f"At the moment, {N} 9.4.0 is the latest {N} release.", T0 - 6 * DAY, None)
ECHO = ("askboard.example", f"By now {N} 9.4.0 is the latest {N} release.", T0 - 3600, None)
SETTINGS = {"both": (True, True), "source_derivation": (True, False), "own_text_provenance": (False, True),
            "off": (False, False)}


def _look(derivation, own):
    """(the stored claims stating 9.4.0 by host, their independence groups as host sets) after an unsourced answer
    stating it and one look at HONEST and ECHO, through the real modules (cycles.investigate)."""
    with adv._s_individual(f"wire-{derivation}-{own}", attribution_independence=False, echo_recognition=False,
                           source_derivation=derivation, own_text_provenance=own) as (db, _):
        adv._s_look(db, T0 - 5 * DAY, [], answer=ANSWER)
        adv._s_look(db, T0, [HONEST, ECHO])
        tid = db.execute("SELECT id FROM web_tasks ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        found = [c for cl in state.load_clusters(db, tid) for c in cl.claims if "v9.4.0" in (c.values or [])]
        host = {c.id: c.domain for c in found}
        groups = [{host[i] for i in g} for g in claims.independence_groups(found)]
        return {c.domain: c for c in found}, groups


@pytest.mark.parametrize("name", sorted(SETTINGS))
def test_an_echo_of_an_unsourced_own_answer_counts_for_nothing_only_with_both_rules_on(name):
    derivation, own = SETTINGS[name]
    pages, groups = _look(derivation, own)
    assert set(pages) == {"techledger.example", "askboard.example"}
    marks = {h: (c.flags or {}).get("derivation") for h, c in pages.items()}
    if name == "both":
        assert marks["askboard.example"] == {"cites": [], "echo": True}
        assert claims.derivation_echo(pages["askboard.example"]) and not claims.derivation_echo(
            pages["techledger.example"])
        assert groups == [{"techledger.example"}]              # the echo is in no group
    elif name == "source_derivation":                           # lane S alone: marked as before, no echo
        assert marks == {"techledger.example": {"cites": []}, "askboard.example": {"cites": []}}
        assert sorted(map(sorted, groups)) == [["askboard.example"], ["techledger.example"]]
    else:                                                       # lane E alone, or both off: no derivation mark at all
        assert marks == {"techledger.example": None, "askboard.example": None}
        assert sorted(map(sorted, groups)) == [["askboard.example"], ["techledger.example"]]


def test_the_hook_is_none_unless_both_settings_are_on_and_a_failing_check_counts_as_without_it(monkeypatch, caplog):
    class Cfg:
        def __init__(self, derivation, own):
            self.source_derivation, self.own_text_provenance = derivation, own
    for derivation, own in ((False, False), (True, False), (False, True)):
        assert emitted.derivation_hook(None, [], Cfg(derivation, own)) is None

    def boom(db, found):
        raise OSError("a message that must not be logged")
    monkeypatch.setattr(emitted, "Echoes", boom)
    hook = emitted.derivation_hook(None, [], Cfg(True, True))
    with adv._s_individual("wire-failing", attribution_independence=False, echo_recognition=False,
                           source_derivation=True, own_text_provenance=False) as (db, _):
        adv._s_look(db, T0, [HONEST, ECHO])
        tid = db.execute("SELECT id FROM web_tasks ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        found = [c for cl in state.load_clusters(db, tid) for c in cl.claims]
    with caplog.at_level("WARNING"):
        claims.stamp_origins(found, [], derivation=True, echo=hook)
    assert not any(claims.derivation_echo(c) for c in found)
    assert "the echo check failed" in caplog.text and "must not be logged" not in caplog.text


NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
STATEMENT = "The Halvorsen Bridge opened in 1987 across the Varnel estuary"


@pytest.mark.parametrize("name", sorted(SETTINGS))
def test_the_research_run_passes_the_hook_only_with_both_rules_on(tmp_path, web, monkeypatch, name):
    """orchestrator's evaluate and adaptive-depth calls of stamp_origins receive lane E's hook exactly when both
    settings are on (each alone, or both off: echo=None, the call as before the wiring)."""
    derivation, own = SETTINGS[name]
    seen = []
    real = claims.stamp_origins

    def spy(*args, **kwargs):
        seen.append(kwargs.get("echo"))
        return real(*args, **kwargs)
    monkeypatch.setattr(claims, "stamp_origins", spy)
    for host in ("a-news.example", "estuary-history.example"):
        web.add(host, "/h", f"<html><head><title>Halvorsen Bridge</title><meta property='article:published_time' "
                            f"content='2026-10-21T08:00:00Z'></head><body><article><h1>Halvorsen Bridge</h1>"
                            f"<p>{STATEMENT}.</p></article></body></html>")
    db = store.create(tmp_path / "individual.sqlite", NOW)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "adaptive_depth": True,
                              "source_derivation": derivation, "own_text_provenance": own}, NOW)
        corpus = CorpusProvider([{"url": f"http://{h}/h", "title": "Halvorsen Bridge", "snippet": STATEMENT}
                                 for h in ("a-news.example", "estuary-history.example")])
        services = svc_mod.build(db, tmp_path / "individual.sqlite", fixture=web, providers=[corpus],
                                 clock=lambda: NOW)
        try:
            task = orchestrator.submit(db, "Is it true that the Halvorsen Bridge opened in 1987?", NOW,
                                       mode="fact_verification")
            orchestrator.run(db, task["id"], services)
        finally:
            services.close()
    finally:
        db.close()
    assert len(seen) >= 2                       # adaptive depth (per page read) and evaluate
    if name == "both":
        assert all(callable(e) for e in seen)
    else:
        assert seen == [None] * len(seen)
