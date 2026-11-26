"""Wave 4 lane E (ADR-067): the individual's own text recognised by what it emitted, not by how a page credits it.

EXPECTATIONS below were written and committed BEFORE the mechanism (webcortex/emitted.py) and before any case that
checks them: each names what must hold with the setting `own_text_provenance` on (and, where said, off). The cases
are added under them afterwards; an expectation is never edited to fit a result (a changed one is a new entry).
"""

EXPECTATIONS = {
    # the rule
    "own_unsourced_value_echoed": "the individual emitted a value for a subject that no source it had read stated; "
                                  "pages read later, published after the emission, stating that value for that "
                                  "subject are not evidence for it: verbatim, paraphrased, with the number written "
                                  "another way (2.4 / v2.4.0), crediting a model or crediting nobody; nothing is "
                                  "admitted on them and each is named as an echo",
    "sourced_emission_is_not_an_echo": "a value the individual emitted while a source it had read stated it is not an "
                                       "echo: later pages stating it count as before",
    "page_published_before_the_emission_counts": "a page whose stated publication precedes the emission is never its "
                                                 "echo, even when it is read after it",
    "other_subject_unaffected": "an emitted value of one subject never discards a page stating the same value of "
                                "another subject",
    "disagreeing_echo_still_counts_against": "an echo that disagrees with what the sources say stays on the "
                                             "contradicting side (counts against a held value only as today)",
    "template_answer_is_not_own_text": "an extractive (template) answer quotes sources: nothing of it is recorded",
    # security
    "hostile_quote_cannot_discard_real_evidence": "a page that quotes the individual's own answer, or any text, cannot "
                                                  "make another page's claim count for less: honest pages stating "
                                                  "another value are admitted as with the setting off",
    "pages_cannot_write_fingerprints": "only the individual's own emissions record fingerprints; reading pages records "
                                       "none",
    "fingerprints_hold_no_text": "what is recorded is salted digests of (subject name, normalised value) with the time "
                                 "and whether a source supported it; never the answer's text, never a source's name",
    "lookup_is_bounded_and_outside_the_write_transaction": "the consolidation's check is point lookups by digest, made "
                                                           "before the write transaction; a claim past the cap is "
                                                           "not an echo and is named in echo_skipped and logged with "
                                                           "ids only",
    "emission_bounded": "at most a fixed number of fingerprints per answer and per subject-value; the store has a cap, "
                        "and a full store records nothing more, visibly",
    "never_in_family_or_agent_scope": "Pocket's family views, the family self-description and the MCP surface never "
                                      "show a fingerprint",
    "failures_keep_the_answer": "a failing record or check never costs the owner the answer or the consolidation: the "
                                "task's error event names the type only",
    # forget and restore
    "forget_source_keeps_the_emission_sourced": "forgetting the source the emitted value rested on keeps the emission "
                                                "sourced (security review M3), and keeps nothing of the source",
    "forget_research_removes_its_fingerprints": "forgetting the task that emitted removes its fingerprints; echo pages "
                                                "then count as with the setting off",
    "forget_knowledge_removes_the_subject_values": "forgetting a knowledge item removes the fingerprints of its subject "
                                                   "and values",
    "forget_voice_session_reaches_them": "forgetting a voice session removes the fingerprints of the research it created",
    "restore_replays_the_forgets": "a checkpoint restored after a forget holds none of the forgotten fingerprints",
    # the default
    "off_changes_nothing": "with the setting off nothing is recorded and consolidation is as before",
    # residual, recorded so it is not mistaken for coverage
    "residual_backdated_page_counts": "a page that states a publication date before the emission counts, whatever it "
                                      "repeats (dates are the page's own claim; not closed)",
}


# ---- the cases (added after the expectations above, each named after the one it checks) --------------------------------
from contextlib import closing, contextmanager  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from neuroscifi.acceptance.cognition import adversarial as adv  # noqa: E402
from neuroscifi.instance import checkpoints, governance, store  # noqa: E402
from neuroscifi.webcortex import consolidate, emitted, orchestrator, state, text as T  # noqa: E402
from neuroscifi.webcortex.types import Claim  # noqa: E402

T0, DAY, N = adv.T0, adv.DAY, adv._SN
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."
SRC = Path(__file__).resolve().parents[2] / "src" / "neuroscifi"


@contextmanager
def _own(name, on=True, **settings):
    """An individual with this lane's rule on (Wave 3's two source rules off, as shipped)."""
    with adv._s_individual(name, **({"attribution_independence": False, "echo_recognition": False,
                                     "own_text_provenance": on} | settings)) as (db, path):
        yield db, path


def _answer(db, at, text=ANSWER, pages=()):
    """A research task whose stored model-written answer is `text` (the harness's way: cycles.investigate)."""
    adv._s_look(db, at, list(pages), answer=text)
    return db.execute("SELECT id FROM web_tasks ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def _pages(texts, published):
    return [(f"site{i}.example", t, published, None) for i, t in enumerate(texts)]


def _rows(db, value, name=N):
    return [r["status"] for r in adv._s_rows(db, value) if name in r["statement"]]


def _fingerprints(db):
    return {n: json.loads(s) for n, s in db.execute("SELECT name, state FROM web_strategies WHERE name LIKE 'emitted%'")}


COMPANION = f"Once again {N} 9.4.0 is the latest {N} release."     # a second echo, worded as outlets word it
ECHO_FORMS = {      # each form beside the companion (two pages that make a row when they count)
    "verbatim": [ANSWER, COMPANION],
    "paraphrased": [f"Right now {N} 9.4.0 is the latest {N} release, bringing several fixes.", COMPANION],
    "number_two_parts": [f"{N} 9.4 is the latest {N} release, which brings several fixes.",
                         f"Once again {N} 9.4 is the latest {N} release."],
    "number_with_v": [f"{N} v9.4.0 is the latest {N} release, which brings several fixes.", COMPANION],
    "credits_a_model": [f"According to ChatGPT, {N} 9.4.0 is the latest {N} release.",
                        f"{N} 9.4.0 is the latest {N} release (OpenAI, 2023)."],
    "credits_nobody": [f"By now {N} 9.4.0 is the latest {N} release.", COMPANION],
}
TWO = ECHO_FORMS["credits_nobody"]


def _rows_any(db, value="9.4"):
    """Rows naming the subject and stating the value in any form."""
    return [r["status"] for r in adv._s_rows(db) if N in r["statement"] and value in r["statement"]]


@pytest.mark.parametrize("form", sorted(ECHO_FORMS))
def test_own_unsourced_value_echoed(form):
    """EXPECTATIONS['own_unsourced_value_echoed']; with the setting off the same pages make a row (the control)."""
    for on in (True, False):
        with _own(f"e-{form}-{on}", on=on) as (db, _):
            _answer(db, T0 - 5 * DAY)
            out = adv._s_look(db, T0, _pages(ECHO_FORMS[form], T0 - 3600))
            if on:
                assert _rows_any(db) == [] and len(out.get("echoes") or []) == 2, form
            else:
                assert _rows_any(db) == ["provisional"] and not out.get("echoes"), form


def test_sourced_emission_is_not_an_echo():
    with _own("sourced") as (db, _):
        _answer(db, T0 - 5 * DAY, pages=[("relnotes.example", f"{N} 9.4.0 is the latest {N} release.", T0 - 6 * DAY,
                                          None)])
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert not out.get("echoes") and _rows(db, "9.4.0") == ["provisional"]
        assert {e[1] for fp in _fingerprints(db).values() for e in fp.get("e", [])} == {emitted.SOURCED}


def test_page_published_before_the_emission_counts():
    """Also EXPECTATIONS['residual_backdated_page_counts']: the date is the page's own claim, so a backdated echo
    counts the same way (not closed)."""
    with _own("before") as (db, _):
        _answer(db, T0 - 5 * DAY)
        out = adv._s_look(db, T0, _pages(TWO, T0 - 6 * DAY))
        assert not out.get("echoes") and _rows(db, "9.4.0") == ["provisional"]


def test_other_subject_unaffected():
    """The same value of another subject, read after the unsourced answer: not an echo (the check itself; whether such
    pages make a row depends on the question asked, not on this rule)."""
    def claim(cid, text):
        return Claim(id=cid, text=text, source_url=f"http://{cid}.example/a", domain=f"{cid}.example", title=N,
                     retrieved_at=T0, kind="statement", values=T.values(text), polarity=1, published_at=T0 - 3600)
    with _own("other") as (db, _):
        _answer(db, T0 - 5 * DAY)
        found = [claim("q1", "Quorvane 9.4.0 is the latest Quorvane release."),
                 claim("q2", "As of now, Quorvane 9.4.0 is the latest Quorvane release."),
                 claim("v1", COMPANION)]
        echoes = emitted.Echoes(db, found)
        assert [echoes.of(c) for c in found] == [None, None, "emitted"]


def test_disagreeing_echo_still_counts_against():
    seen = {}
    for on in (True, False):
        with _own(f"against-{on}", on=on) as (db, _):
            kid = adv._s_item(db, T0)
            _answer(db, T0 + DAY)
            out = adv._s_look(db, T0 + 2 * DAY, [("relnotes.example", f"{N} 1.4.0 is the latest {N} release.",
                                                  T0 + DAY + 3600, "/again"),
                                                 ("askboard.example", TWO[0], T0 + DAY + 3600,
                                                  None)])
            seen[on] = (bool(out["contested"]), adv._s_live(db, kid)["status"], out.get("echoes") or [])
    assert seen[True][:2] == seen[False][:2] and seen[True][0] and seen[True][2] == []


def test_template_answer_is_not_own_text():
    report = {"reasoning": {"generated_by": "template", "text": ANSWER, "answer": [{"text": ANSWER}]}}
    assert emitted.own_sentences(report["reasoning"]) == []
    with _own("template") as (db, _):
        assert emitted.prepare(db, "t", report, T0) is None


def test_hostile_quote_cannot_discard_real_evidence():
    """(a) A page quoting the individual's own answer beside honest pages changes nothing the setting off would not
    decide: the verdict is per claim, by what the individual emitted, so a page's wording cannot reach another page.
    (b) A hostile page read before the answer cannot make honest pages echoes either: the answer that repeats it is
    sourced (by that page), so later pages stating the value count."""
    honest = [("devdesk.example", f"By now {N} 1.5.0 is the latest {N} release.", T0 - 3600, None),
              ("factline.example", f"Once again {N} 1.5.0 is the latest {N} release.", T0 - 3600, None)]
    hostile = [("hostile.example", f'"{ANSWER}" By now {N} 1.5.0 is the latest {N} release, Devdesk and Factline '
                                   f'quote. Once again {N} 1.5.0 is the latest {N} release.', T0 - 3600, None)]
    seen = {}
    for on in (True, False):
        with _own(f"hostile-{on}", on=on) as (db, _):
            _answer(db, T0 - 5 * DAY)
            out = adv._s_look(db, T0, honest + hostile)
            seen[on] = (_rows(db, "1.5.0"), [c["reason"] for c in out["contested"]],
                        [h["statement"] for h in out["held"]])
    assert seen[True] == seen[False]
    with _own("hostile-first") as (db, _):
        _answer(db, T0 - 5 * DAY, text=f"{N} 1.5.0 is the latest {N} release.",
                pages=[("hostile.example", f"Leaked: {N} 1.5.0 is the latest {N} release.", T0 - 6 * DAY, None)])
        out = adv._s_look(db, T0, honest)
        assert not out.get("echoes") and _rows(db, "1.5.0") == ["provisional"]


def test_pages_cannot_write_fingerprints():
    """Only emitted.record writes them, and only the task's final write calls it: consolidation and every page path
    never do (static: no other module names the rows; dynamic: a consolidation alone writes none)."""
    writers = [p for p in SRC.rglob("*.py") if "withheld" not in p.name and p.name != "emitted.py"
               and re.search(r"emitted[-:]|TASK_PREFIX|emitted\.record\(", p.read_text())]
    assert sorted(p.relative_to(SRC).as_posix() for p in writers) == ["webcortex/orchestrator.py"]
    with _own("pages") as (db, _):
        from neuroscifi.webcortex import claims
        from neuroscifi.webcortex.types import Document
        task = orchestrator.submit(db, adv._SQ, T0, mode="fact_verification", origin="console")
        docs = [Document(url=f"http://{h}/x", final_url=f"http://{h}/x", canonical_url=f"http://{h}/x", domain=h,
                         title=N, text=t, retrieved_at=T0, retrieval_mode="live", published_at=at)
                for h, t, at, _ in _pages([ANSWER, f"Reposted: {ANSWER}"], T0 - 3600)]
        found = [c for d in docs for c in claims.extract_claims(d, adv._SQ)]
        with store.write(db):
            state.save_clusters(db, task["id"], [claims.score(c, now=T0, freshness_class="release")
                                                 for c in claims.cluster(found)], T0)
        consolidate.run(db, task["id"], T0)
        assert emitted.stored(db) == 0 and _fingerprints(db) == {}


def test_fingerprints_hold_no_text():
    with _own("notext") as (db, _):
        _answer(db, T0 - 5 * DAY, pages=[("relnotes.example", f"{N} 9.4.0 is the latest {N} release.", T0 - 6 * DAY,
                                          None)])
        held = _fingerprints(db)
        assert held and all(re.fullmatch(r"emitted(-task)?:[0-9a-f]{32}", n) for n in held)
        blob = json.dumps(held).lower()
        for word in ("velmora", "9.4", "relnotes", "release", "fixes"):
            assert word not in blob


def test_lookup_is_bounded_and_outside_the_write_transaction(monkeypatch, caplog):
    seen = []
    real = emitted.Echoes.__init__

    def init(self, db, found):
        seen.append(db.in_transaction)
        real(self, db, found)
    monkeypatch.setattr(emitted.Echoes, "__init__", init)
    with _own("tx") as (db, _):
        _answer(db, T0 - 5 * DAY)
        adv._s_look(db, T0, _pages(TWO, T0 - 3600))
    assert seen and not any(seen)
    monkeypatch.setattr(emitted, "MAX_LOOKUPS", 1)       # two claims, three distinct (subject, value) pairs
    with caplog.at_level(logging.WARNING), _own("cap") as (db, _):
        _answer(db, T0 - 5 * DAY)
        out = adv._s_look(db, T0, _pages([TWO[0], f"Once again {N} 9.4.0 is the latest {N} release, after {N} "
                                                  f"9.3.0."], T0 - 3600))
    assert len(out.get("echo_skipped") or []) == 1 and len(out.get("echoes") or []) == 1
    logged = " ".join(r.getMessage() for r in caplog.records if r.name == emitted.__name__)
    assert "not checked" in logged and N not in logged and "9.4" not in logged


@pytest.mark.parametrize("bound", ("rows", "time"))
def test_a_source_check_that_cannot_finish_counts_as_sourced(monkeypatch, caplog, bound):
    """lookup_is_bounded...: the emission's source check is bounded (rows per subject name, seconds); a pair it could
    not settle is recorded as not known, which is never an echo, and the log names counts and the task id only."""
    if bound == "rows":
        monkeypatch.setattr(emitted, "SUPPORT_ROWS", 1)
    else:
        monkeypatch.setattr(emitted, "SUPPORT_SECONDS", -1.0)
    with caplog.at_level(logging.WARNING), _own(f"unknown-{bound}") as (db, _):
        adv._s_look(db, T0 - 9 * DAY, [(f"old{i}.example", f"{N} 9.4.{i + 1} was listed in an older {N} release "
                                        f"note.", T0 - 10 * DAY, None) for i in range(3)])
        _answer(db, T0 - 5 * DAY)
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert not out.get("echoes") and _rows(db, "9.4.0") == ["provisional"]
        assert {e[1] for fp in _fingerprints(db).values() for e in fp.get("e", [])} >= {emitted.UNKNOWN}
    logged = " ".join(r.getMessage() for r in caplog.records if r.name == emitted.__name__)
    assert "not checked for a source" in logged and N not in logged and "9.4" not in logged


def test_the_checks_are_point_lookups_at_ten_thousand_answers():
    """10,000 stored answers' fingerprints (16 each) and 2,000 claims about the subject: the consolidation's check of
    eight claims is point lookups; the emission's source check stays inside its budget."""
    with _own("scale") as (db, _):
        salt = emitted._salt(db)
        with store.write(db):
            for i in range(10_000):
                emitted.record(db, {"task": emitted.task_row(salt, f"t{i}"), "task_digest": f"d{i}", "at": T0 - 1e6 + i,
                                    "full": False, "rows": [(emitted.fingerprint(salt, f"s{i}", f"version:{j}.{i}"),
                                                             emitted.UNSOURCED, 0) for j in range(16)]})
        task = orchestrator.submit(db, adv._SQ, T0, mode="fact_verification", origin="console")
        with store.write(db):
            for i in range(2000):
                db.execute("INSERT INTO web_claims(id,task_id,text,key,kind,polarity,claim_values,source_url,domain,"
                           "retrieved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (f"c{i}", task["id"], f"{N} 2.{i}.1 was mentioned.", "k", "statement", 1,
                            json.dumps([f"v2.{i}.1"]), f"http://x{i}.example/", f"x{i}.example", T0 - DAY, T0 - DAY))
        found = [Claim(id=f"p{i}", text=f"{N} 3.{i}.0 is the latest {N} release.", source_url=f"http://p{i}.example/",
                       domain=f"p{i}.example", title=N, retrieved_at=T0, kind="statement",
                       values=T.values(f"{N} 3.{i}.0 is the latest {N} release."), polarity=1, published_at=None)
                 for i in range(8)]
        started = time.perf_counter()
        echoes = emitted.Echoes(db, found)
        lookup = time.perf_counter() - started
        started = time.perf_counter()
        prepared = emitted.prepare(db, task["id"], {"reasoning": {"generated_by": "model:x", "text": ANSWER}}, T0)
        support = time.perf_counter() - started
        assert emitted.stored(db) == 10_000 * 16 and echoes.skipped == [] and lookup < 0.5
        assert support < emitted.SUPPORT_SECONDS + 0.5 and prepared["rows"]


def test_emission_bounded(caplog, monkeypatch):
    many = " ".join(f"{N} 4.{i}.1 was mentioned." for i in range(40))
    with _own("bounded") as (db, _):
        tid = _answer(db, T0 - 5 * DAY, text=many)
        salt = emitted._salt(db)
        assert len(_fingerprints(db)[emitted.task_row(salt, tid)]["k"]) == emitted.MAX_PER_ANSWER
        for k in range(5):
            _answer(db, T0 - 4 * DAY + k)
        key = emitted.fingerprint(salt, N.lower(), "version:9.4")
        assert len(_fingerprints(db)[key]["e"]) == emitted.MAX_EMISSIONS
    monkeypatch.setattr(emitted, "MAX_FINGERPRINTS", 1)
    with caplog.at_level(logging.WARNING), _own("full") as (db, _):
        _answer(db, T0 - 5 * DAY)
        before = emitted.stored(db)
        _answer(db, T0 - 4 * DAY, text=f"{N} 7.7.0 is the latest {N} release.")
        assert emitted.stored(db) == before
    assert any("maximum" in r.getMessage() for r in caplog.records if r.name == emitted.__name__)


def test_never_in_family_or_agent_scope():
    """No module but emitted.py lists the rows: every read of web_strategies names its row; Pocket, the MCP surface
    and the self-description never read the table."""
    for path in SRC.rglob("*.py"):
        if "withheld" in path.name:
            continue
        text = path.read_text()
        for m in re.finditer(r"FROM web_strategies([^\"']*)", text):
            assert "WHERE name" in m.group(1) or path.name == "emitted.py", (path, m.group(0))
        if path.parent.name == "pocket" or path.name in ("mcp.py", "self_description.py"):
            assert "web_strategies" not in text and "emitted" not in text, path


def test_failures_keep_the_answer(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError(f"{N} 9.4.0 secret")
    monkeypatch.setattr(emitted, "record", broken)
    monkeypatch.setattr(emitted.Echoes, "__init__", broken)
    with _own("faults") as (db, _):
        tid = _answer(db, T0 - 5 * DAY)
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert state.task(db, tid)["status"] == "completed" and json.loads(
            db.execute("SELECT result FROM web_tasks WHERE id=?", (tid,)).fetchone()[0])["reasoning"]["text"] == ANSWER
        errors = [e["detail"] for e in state.events(db, tid) if e["kind"] == "error"]
        assert {"stage": "recording the individual's own claims", "error": "RuntimeError"} in errors
        assert _rows(db, "9.4.0") == ["provisional"] and not out.get("echoes")     # as with the setting off
        details = [e["detail"] for (t,) in db.execute("SELECT id FROM web_tasks").fetchall() for e in state.events(db, t)]
        assert "secret" not in json.dumps(details)


def test_forget_source_keeps_the_emission_sourced():
    with _own("m3") as (db, _):
        _answer(db, T0 - 5 * DAY, pages=[("relnotes.example", f"{N} 9.4.0 is the latest {N} release.", T0 - 6 * DAY,
                                          None)])
        governance.forget_source(db, "relnotes.example", T0 - 4 * DAY)
        assert "relnotes" not in json.dumps(_fingerprints(db))
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert not out.get("echoes") and _rows(db, "9.4.0") == ["provisional"]


def test_forget_research_removes_its_fingerprints():
    with _own("research") as (db, _):
        tid = _answer(db, T0 - 5 * DAY)
        salt = emitted._salt(db)
        key = emitted.fingerprint(salt, N.lower(), "version:9.4")
        assert key in _fingerprints(db)
        governance.forget_research(db, tid, T0 - 4 * DAY)
        assert key not in _fingerprints(db) and emitted.task_row(salt, tid) not in _fingerprints(db)
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert not out.get("echoes") and _rows(db, "9.4.0") == ["provisional"]


def test_forget_knowledge_removes_the_subject_values():
    with _own("knowledge") as (db, _):
        kid = adv._s_item(db, T0)
        tid = _answer(db, T0 + DAY, text=f"{N} 1.4.0 is the latest {N} release.")
        salt = emitted._salt(db)
        key = emitted.fingerprint(salt, N.lower(), "version:1.4")
        assert key in _fingerprints(db)
        governance.forget_knowledge(db, kid, T0 + 2 * DAY)
        held = _fingerprints(db)
        assert key not in held and key not in held.get(emitted.task_row(salt, tid), {}).get("k", [])


def test_forget_voice_session_reaches_them():
    with _own("voice") as (db, _):
        with store.write(db):
            db.execute("INSERT INTO voice_sessions(id,principal,device,mode,status,started_at,updated_at) "
                       "VALUES('s1','owner','desk','text_only','ended',?,?)", (T0 - 6 * DAY, T0 - 6 * DAY))
        tid = _answer(db, T0 - 5 * DAY)
        with store.write(db):
            db.execute("INSERT INTO voice_segments(session_id,utterance,seq,speaker,source,status,authority,text,detail,"
                       "task_id,started_at,created_at,updated_at) VALUES('s1','u1',1,'user','typed','final',"
                       "'user_typed','x',?,?,?,?,?)", (json.dumps({"intent": "research"}), tid, T0 - 5 * DAY,
                                                       T0 - 5 * DAY, T0 - 5 * DAY))
            governance.forget_voice_session(db, "s1", T0 - 4 * DAY)
        salt = emitted._salt(db)
        assert emitted.fingerprint(salt, N.lower(), "version:9.4") not in _fingerprints(db)


def test_restore_replays_the_forgets(tmp_path):
    with _own("restore") as (db, path):
        tid = _answer(db, T0 - 5 * DAY)
        salt = emitted._salt(db)
        key = emitted.fingerprint(salt, N.lower(), "version:9.4")
        made = checkpoints.create(db, path, tmp_path / "checkpoints", T0 - 4.5 * DAY)
        governance.forget_research(db, tid, T0 - 4 * DAY)
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, made["id"], copy, T0 - 3 * DAY)
        with closing(store.connect(copy)) as restored:
            names = {n for (n,) in restored.execute("SELECT name FROM web_strategies")}
            assert key not in names and emitted.task_row(salt, tid) not in names


def test_off_changes_nothing():
    with _own("off", on=False) as (db, _):
        _answer(db, T0 - 5 * DAY)
        out = adv._s_look(db, T0, _pages(TWO, T0 - 3600))
        assert emitted.stored(db) == 0 and _fingerprints(db) == {} and not out.get("echoes")
        assert "echo_skipped" not in out
