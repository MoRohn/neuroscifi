"""Wave 3 fix lane: the security review's findings on lane S's opt-in rules (ADR-061), each with a test that fails
without its fix. Both rules ship OFF (decisions D1, D2); these make the settings sound when the owner turns them on."""
from contextlib import closing
import json
import random
import time

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.webcortex import claims, consolidate, orchestrator, text as T
from neuroscifi.webcortex.types import Claim, Document

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


# ---- M5: the model-credit phrase reads a model speaker only ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "According to the AI Act, fines reach 35,000,000 euros.", "According to an AI Index report, 1,812 models shipped.",
    "According to some AI companies, 4,500 GPUs were used.", "The AI said 1,200 is the limit.",
    "According to the AI Safety Institute, 1,234 tests ran.", "Some AI companies said 4,500 GPUs were used."])
def test_ordinary_sentences_about_ai_are_not_model_text(text):
    assert consolidate._MODEL_CREDIT.search(text) is None


@pytest.mark.parametrize("text", [
    f"{N} 9.2.0 is the latest {N} release, an assistant reported.",
    f"\"{N} 9.2.0 is the latest {N} release,\" a chatbot told one reader.",
    f"According to an AI model, {N} 9.2.0 is the latest {N} release.",
    f"An AI assistant says {N} 9.2.0 is the latest {N} release."])
def test_a_sentence_crediting_a_model_speaker_is_still_model_text(text):
    assert consolidate._MODEL_CREDIT.search(text) is not None


def test_honest_pages_crediting_an_ai_institute_are_not_discarded_as_echoes():
    pages = [("devdesk.example", f"According to the AI Safety Institute, {N} 1.5.0 is the latest {N} release.",
              T0 - DAY, None),
             ("factline.example", f"By now {N} 1.5.0 is the newest {N} release, the AI Act registry shows.", T0 - DAY,
              None),
             ("weekly-builds.example", f"Some AI companies said {N} 1.5.0 is now the current {N} release.", T0 - DAY,
              None)]
    with adv._s_individual("m5", attribution_independence=False) as (db, _):
        out = adv._s_look(db, T0, pages)
        assert not out.get("echoes") and [r["status"] for r in adv._s_rows(db, "1.5.0")] == ["provisional"]


# ---- M2: the source check is per subject, not a sample of 200 rows ------------------------------------------------------

def _answered_from_relnotes(db, at):
    adv._s_look(db, at, [("relnotes.example", f"{N} 1.5.0 is the latest {N} release.", at - DAY, None)],
                answer=f"{N} 1.5.0 is the latest {N} release.")


def _two_honest(db, at):
    return adv._s_look(db, at, [(h, t, at - 3600, None) for h, t, _, _ in HONEST[:2]])


def _echo_pages(value):
    return [("askboard.example", f"Someone on the forum says {N} {value} is now the latest {N} release.", T0 - 3600,
             None), ("answer-roundup.example", f"Answers roundup: {N} {value} is the latest {N} release.", T0 - 3600,
                     None)]


def test_a_sourced_answer_stays_sourced_when_many_other_subjects_state_the_value():
    with adv._s_individual("m2") as (db, _):
        adv._s_look(db, T0 - 10 * DAY, [(f"o{i}.example", f"Zorb{i} 1.5.0 is the latest Zorb{i} release.",
                                         T0 - 11 * DAY, None) for i in range(250)])
        _answered_from_relnotes(db, T0 - 5 * DAY)
        out = _two_honest(db, T0)
        assert not out.get("echoes")
        assert [r["status"] for r in adv._s_rows(db, "1.5.0") if N in r["statement"]] == ["provisional"]


def test_an_unsourced_answer_is_still_echoed():
    """The rule still works: a value only the answer stated, then two outside pages: no row."""
    with adv._s_individual("m2-echo") as (db, _):
        adv._s_look(db, T0 - 5 * DAY, [], answer=f"{N} 9.4.0 is the latest {N} release.")
        out = adv._s_look(db, T0, _echo_pages("9.4.0"))
        assert out.get("echoes") and adv._s_rows(db, "9.4.0") == []


# ---- M3: forgetting the source an answer rested on does not make the answer sourceless ----------------------------------

def test_after_forgetting_the_answers_source_later_honest_pages_still_count():
    with adv._s_individual("m3-source") as (db, _):
        _answered_from_relnotes(db, T0 - 5 * DAY)
        governance.forget_source(db, "relnotes.example", T0 - 4 * DAY)
        assert db.execute("SELECT count(*) FROM web_claims WHERE domain='relnotes.example'").fetchone()[0] == 0
        out = _two_honest(db, T0)
        assert not out.get("echoes") and [r["status"] for r in adv._s_rows(db, "1.5.0")] == ["provisional"]


def test_after_forgetting_the_research_the_answer_rested_on_later_honest_pages_still_count():
    with adv._s_individual("m3-research") as (db, _):
        adv._s_look(db, T0 - 6 * DAY, [("relnotes.example", f"{N} 1.5.0 is the latest {N} release.", T0 - 7 * DAY,
                                        None)])
        first = db.execute("SELECT id FROM web_tasks ORDER BY created_at LIMIT 1").fetchone()[0]
        adv._s_look(db, T0 - 5 * DAY, [], answer=f"{N} 1.5.0 is the latest {N} release.")
        governance.forget_research(db, first, T0 - 4 * DAY)
        out = _two_honest(db, T0)
        assert not out.get("echoes") and [r["status"] for r in adv._s_rows(db, "1.5.0")] == ["provisional"]


def test_a_checkpoint_restored_after_the_forget_replays_the_mark(tmp_path):
    with adv._s_individual("m3-restore") as (db, path):
        _answered_from_relnotes(db, T0 - 5 * DAY)
        made = checkpoints.create(db, path, tmp_path / "checkpoints", T0 - 4.5 * DAY)
        governance.forget_source(db, "relnotes.example", T0 - 4 * DAY)
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, made["id"], copy, T0 - 3 * DAY)
        with closing(store.connect(copy)) as restored:
            assert restored.execute("SELECT count(*) FROM web_claims WHERE domain='relnotes.example'"
                                    ).fetchone()[0] == 0
            answers = [json.loads(r)["reasoning"] for (r,) in restored.execute(
                "SELECT result FROM web_tasks WHERE result LIKE '%model:%'")]
            assert answers and all(consolidate.own_sentences(r) == [] for r in answers)


# ---- M4: the echo checks are bounded, outside the write transaction, and a skip is visible ------------------------------

def test_the_echo_checks_run_before_the_write_transaction(monkeypatch):
    seen = []
    real = consolidate._Echoes.__init__

    def init(self, db, found, **kw):
        seen.append(db.in_transaction)
        real(self, db, found, **kw)
    monkeypatch.setattr(consolidate._Echoes, "__init__", init)
    with adv._s_individual("m4-tx") as (db, _):
        _two_honest(db, T0)
    assert seen == [False]


def test_a_claim_not_checked_in_time_is_not_an_echo_and_is_named(monkeypatch):
    monkeypatch.setattr(consolidate, "ECHO_SECONDS", 0.0)
    with adv._s_individual("m4-skip") as (db, _):
        adv._s_look(db, T0 - 5 * DAY, [], answer=f"{N} 9.4.0 is the latest {N} release.")
        out = adv._s_look(db, T0, _echo_pages("9.4.0"))
        assert len(out.get("echo_skipped") or []) == 2 and not out.get("echoes")


def test_the_echo_checks_take_one_scan_at_ten_thousand_tasks():
    """10,000 stored model-written answers (2 kB each) and eight claims of 78 numbers: the checks finish inside their
    budget, none skipped (before the fix: one full scan per number, inside the write lock; 9.8 s for one such claim at
    10,000 tasks of 20 kB in the security review)."""
    with adv._s_individual("m4-scale") as (db, _):
        task = orchestrator.submit(db, adv._SQ, T0, mode="fact_verification", origin="console")
        cols = [c[1] for c in db.execute("PRAGMA table_info(web_tasks)")]
        row = dict(zip(cols, db.execute("SELECT * FROM web_tasks WHERE id=?", (task["id"],)).fetchone()))
        rnd = random.Random(1)
        filler = " ".join(f"Item{i} {rnd.randint(100, 99999)}.{rnd.randint(0, 9)} was noted." for i in range(200))
        result = json.dumps({"reasoning": {"generated_by": "model:x", "text": filler[:2000]}})
        with store.write(db):
            for i in range(10_000):
                r = dict(row, id=f"bulk{i:07d}", result=result, updated_at=T0 - 1e6 + i, finished_at=T0 - 1e6 + i)
                db.execute(f"INSERT INTO web_tasks({','.join(r)}) VALUES({','.join('?' * len(r))})",
                           tuple(r.values()))
        text = "Velmora " + " ".join(f"{rnd.randint(100, 99999)}.{rnd.randint(1, 9)}" for _ in range(78)) + " ok"
        claim = Claim(id="probe", text=text, source_url="http://x.example/a", domain="x.example", title="x",
                      retrieved_at=T0 + 10, kind="statement", values=T.values(text), polarity=1, published_at=None)
        started = time.perf_counter()
        echoes = consolidate._Echoes(db, [claim] * 8)
        assert time.perf_counter() - started < consolidate.ECHO_SECONDS and echoes.skipped == []


# ---- M6: only the owner's research and refreshes let the supersession rule decide -------------------------------------

def _origin_look(db, at, origin, family=None):
    from neuroscifi.acceptance.cognition import cycles
    task = orchestrator.submit(db, adv._SQ, at, mode="fact_verification", origin=origin,
                               **({"family": family} if family else {}))
    pages = [("devdesk.example", f"By now {N} 1.6.0 is the latest {N} release.", at - DAY, "/a"),
             ("factline.example", f"Once again {N} 1.6.0 is the latest {N} release.", at - DAY, "/b"),
             ("relnotes.example", f"{N} 1.4.0 is the latest {N} release.", T0 - DAY, "/old")]
    docs = [Document(url=f"http://{h}{p}", final_url=f"http://{h}{p}", canonical_url=f"http://{h}{p}", domain=h,
                     title=N, text=t, retrieved_at=at, retrieval_mode="live", published_at=pub) for h, t, pub, p in pages]
    return cycles.investigate(db, task, docs, at)


@pytest.mark.parametrize("origin, family, decides", [
    ("pocket", "f" * 64, False), ("mcp", None, False), ("voice", None, False), ("curiosity", None, False),
    ("console", None, True), ("cli", None, True)])
def test_a_family_or_agent_task_never_lets_the_rule_replace_the_owners_item(origin, family, decides):
    with adv._s_individual(f"m6-{origin}", **adv.RULE_ON) as (db, _):
        kid = adv._s_item(db)
        out = _origin_look(db, T0 + 2 * DAY, origin, family)
        by_rule = [s for s in out["superseded"] if s.get("by") == "rule"]
        assert bool(by_rule) is decides
        assert adv._s_live(db, kid)["status"] == ("superseded" if decides else "provisional")


def test_refreshes_let_the_rule_decide():
    assert consolidate.rule_may_decide({"origin": "refresh:12"}) and \
        consolidate.rule_may_decide({"origin": "refresh-owner:12"})
    assert not any(consolidate.rule_may_decide({"origin": o}) for o in ("pocket", "mcp", "voice", "curiosity",
                                                                        "monitor:3", "", None))


# ---- M1 and L1: forget reaches the rule's records; a revert restores nothing forgotten and reopens what it closed -----

def _replaced(db):
    kid = adv._s_item(db)
    out = adv._s_refresh(db, T0 + 3 * DAY)
    new = next(s["new"] for s in out["superseded"] if s.get("by") == "rule")
    return kid, new


def test_a_revert_after_forgetting_a_source_of_the_replaced_item_does_not_write_it_back():
    from neuroscifi.webcortex import knowledge
    with adv._s_individual("m1-source", **adv.RULE_ON) as (db, _):
        kid, new = _replaced(db)
        governance.forget_source(db, "pkgwatch.example", T0 + 4 * DAY)
        assert db.execute("SELECT count(*) FROM web_knowledge WHERE verification LIKE '%pkgwatch%' OR sources LIKE "
                          "'%pkgwatch%'").fetchone()[0] == 0
        live = knowledge.row(db, kid)
        knowledge.revert(db, new, T0 + 5 * DAY)
        old = knowledge.row(db, kid)
        assert old["status"] == "provisional" and old["superseded_by"] is None
        assert not any("pkgwatch" in (s.get("url") or "") for s in old["sources"])
        assert (old["sources"], old["supporting"]) == (live["sources"], live["supporting"])
        gone = set(old["supporting"]) - {r[0] for r in db.execute("SELECT id FROM web_claims")}
        assert gone == set()


def test_a_record_written_before_the_fix_is_cut_down_by_a_forget_and_never_written_back():
    from neuroscifi.webcortex import knowledge
    with adv._s_individual("m1-legacy", **adv.RULE_ON) as (db, _):
        kid, new = _replaced(db)
        legacy = {"sources": json.dumps([{"url": "http://pkgwatch.example/velmora"}]), "supporting": '["gone"]',
                  "note": "old"}
        with store.write(db):
            db.execute("UPDATE web_knowledge SET verification=json_set(verification, '$.supersession.previous', "
                       "json(?)) WHERE id=?", (json.dumps(json.loads(db.execute(
                           "SELECT json_extract(verification, '$.supersession.previous') FROM web_knowledge WHERE "
                           "id=?", (new,)).fetchone()[0]) | legacy), new))
        live = knowledge.row(db, kid)
        knowledge.revert(db, new, T0 + 5 * DAY)
        assert (knowledge.row(db, kid)["sources"], knowledge.row(db, kid)["supporting"]) == \
            (live["sources"], live["supporting"])
    with adv._s_individual("m1-legacy-forget", **adv.RULE_ON) as (db, _):
        kid, new = _replaced(db)
        with store.write(db):
            db.execute("UPDATE web_knowledge SET verification=json_set(verification, '$.supersession.previous.sources',"
                       " ?) WHERE id=?", ('[{"url": "http://pkgwatch.example/velmora"}]', new))
        governance.forget_source(db, "unrelated.example", T0 + 4 * DAY)    # any web forget scrubs the record
        governance.forget_source(db, "pkgwatch.example", T0 + 4 * DAY)
        record = knowledge.row(db, new)["verification"]["supersession"]["previous"]
        assert set(record) <= set(consolidate.RESTORE_COLUMNS)


def test_forgetting_the_replaced_items_research_leaves_no_trace_in_the_rules_records():
    from neuroscifi.webcortex import knowledge
    with adv._s_individual("m1-research", **adv.RULE_ON) as (db, _):
        kid, new = _replaced(db)
        task = knowledge.row(db, kid)["task_id"]
        governance.forget_research(db, task, T0 + 4 * DAY)
        assert db.execute("SELECT count(*) FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0] == 0
        assert "pkgwatch" not in json.dumps(knowledge.row(db, new)["verification"])
        assert db.execute("SELECT count(*) FROM pending WHERE question LIKE ?", (f"%{N} 1.4.0%",)).fetchone()[0] == 0


def test_a_revert_reopens_the_questions_the_rule_closed_and_marks_its_links():
    from neuroscifi.webcortex import knowledge
    with adv._s_individual("l1", **adv.RULE_ON) as (db, _):
        kid = adv._s_item(db)
        scope = f"web:knowledge:{kid}"
        before = sorted(r[0] for r in db.execute("SELECT id FROM pending WHERE scope=? AND resolved_at IS NULL",
                                                  (scope,)))
        assert before, "the staging must leave the held item an open question"
        out = adv._s_refresh(db, T0 + 3 * DAY)
        new = next(s["new"] for s in out["superseded"] if s.get("by") == "rule")
        assert db.execute("SELECT count(*) FROM pending WHERE scope=? AND resolved_at IS NULL", (scope,)
                          ).fetchone()[0] == 0
        result = knowledge.revert(db, new, T0 + 4 * DAY)
        after = sorted(r[0] for r in db.execute("SELECT id FROM pending WHERE scope=? AND resolved_at IS NULL",
                                                 (scope,)))
        assert after == before and result["pending_reopened"] == before
        links = [json.loads(d) for (d,) in db.execute("SELECT detail FROM web_claim_links WHERE kind='supersedes'")]
        assert links and all(d.get("reverted") is True for d in links if d.get("by") == "rule")


# ---- L6: the day's autonomous task count is enforced where a task is created ------------------------------------------

def _autonomous_individual(tmp_path, cap):
    from neuroscifi.webcortex import state
    path = tmp_path / "l6.sqlite"
    db = store.create(path, T0, note="fix lane L6")
    state.set_config(db, {"enabled": True, "daily_autonomous": {"tasks": cap, "requests": 400,
                                                                "bytes": 80_000_000}}, T0)
    return db, path


def test_two_deciders_that_both_saw_room_cannot_both_take_the_last_place(tmp_path):
    from neuroscifi.webcortex import autonomy, scheduler
    db, _ = _autonomous_individual(tmp_path, 2)
    with closing(db):
        for i in range(3):          # the owner's own research is neither counted nor blocked
            orchestrator.submit(db, f"Owner question {i}: what is the latest {N} release?", T0, mode="fact_verification",
                                origin="console")
        orchestrator.submit(db, "What is the current Zorbix version?", T0, mode="fact_verification", origin="curiosity")
        assert autonomy._tasks_spent(db, T0 + 1) is None and autonomy._tasks_spent(db, T0 + 1) is None
        orchestrator.submit(db, "What is the current QuorvexA version?", T0 + 2, mode="fact_verification",
                            origin="curiosity")
        with pytest.raises(autonomy.BudgetRefused):
            orchestrator.submit(db, "What is the current QuorvexB version?", T0 + 2, mode="fact_verification",
                                origin="curiosity")
        assert scheduler.autonomous_usage(db, T0 + 3)["tasks"] == 2


def test_concurrent_autonomous_submissions_never_pass_the_cap(tmp_path):
    import threading
    from neuroscifi.webcortex import scheduler
    db, path = _autonomous_individual(tmp_path, 3)
    db.close()
    errors, barrier = [], threading.Barrier(8)

    def decider(k):
        with closing(store.connect(path)) as mine:
            barrier.wait()
            try:
                orchestrator.submit(mine, f"What is the current Racer{k} version?", T0 + 5, mode="fact_verification",
                                    origin="curiosity")
            except Exception as exc:        # noqa: BLE001 - the refusal is the point
                errors.append(type(exc).__name__)
    threads = [threading.Thread(target=decider, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with closing(store.connect(path)) as db:
        assert scheduler.autonomous_usage(db, T0 + 6)["tasks"] == 3
    assert errors.count("BudgetRefused") == 5, errors


# ---- L7: `nsf imagine scm` input robustness ---------------------------------------------------------------------------

def _front_door(**extra):
    return {"graph": {"X": [], "M": ["X"], "Y": ["M"]}, "hidden_common_cause": ["X", "Y"], "do": {"X": 1.0},
            "ask": "Y", "means": {"X": 0, "M": 0, "Y": 0}, "moments": "population",
            "observational_covariance": {"X": {"X": 1, "M": 0.5, "Y": 0.6}, "M": {"X": 0.5, "M": 1, "Y": 0.7},
                                         "Y": {"X": 0.6, "M": 0.7, "Y": 1}}} | extra


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_a_do_value_that_is_not_finite_is_a_one_line_error(value):
    from neuroscifi import causal
    with pytest.raises(ValueError, match="finite"):
        causal.answer(_front_door(do={"X": value}))


def test_duplicate_parent_entries_do_not_make_the_front_door_quadratic():
    from neuroscifi import causal
    plain = causal.answer(_front_door())
    started = time.perf_counter()
    padded = causal.answer(_front_door(graph={"X": [], "M": ["X"] * 30_000, "Y": ["M"] * 30_000}))
    assert time.perf_counter() - started < 2.0                  # 13.6 s before the fix
    assert padded["value"] == pytest.approx(plain["value"]) and padded["identified_by"] == plain["identified_by"]


def test_the_owners_command_prints_one_line_for_a_value_that_is_not_finite(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    import neuroscifi
    path = tmp_path / "q.json"
    path.write_text(json.dumps(_front_door(do={"X": "nan"})))
    src = Path(neuroscifi.__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "imagine", "scm", str(path)], capture_output=True,
                         text=True, timeout=60, env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin",
                                                     "HOME": str(tmp_path)})
    assert out.returncode != 0 and "Traceback" not in out.stderr and "finite" in out.stderr + out.stdout


# ---- L8: a damaged stored Wave 3 web setting falls back to its default and is reported --------------------------------

@pytest.mark.parametrize("name, bad", [("recheck_order", 7), ("supersession_min_sources", "two"),
                                       ("supersession", "auto"), ("attribution_independence", "yes"),
                                       ("echo_recognition", 1)])
def test_a_damaged_saved_wave3_setting_reads_as_its_default(tmp_path, name, bad):
    from neuroscifi.webcortex import scheduler, state
    from neuroscifi.webcortex.config import META_KEY, WebConfig
    db = store.create(tmp_path / "l8.sqlite", T0, note="fix lane L8")
    with closing(db):
        state.set_config(db, {"enabled": True, "autonomous": True, "supersession": "independent"}, T0)
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()[0])
        with store.write(db):
            db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(raw | {name: bad}), META_KEY))
        assert getattr(state.get_config(db), name) == getattr(WebConfig(), name)
        assert [p for p in state.setting_problems(db) if name in p]
        scheduler.tick(db, T0 + 10, None, queue=True)           # the timer's pass does not raise


# ---- Q7: the looks' machinery (built and tested on public splits only; nothing here loads the withheld worlds) -------

@pytest.fixture
def sealed(monkeypatch, tmp_path):
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import cycles

    def loader():
        raise AssertionError("the withheld worlds were loaded")
    monkeypatch.setattr(cycles, "_withheld", loader)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)
    return ledger


@pytest.mark.parametrize("failure", ["timeout", "exit 128", "oserror"])
def test_a_dirty_check_that_fails_is_not_clean_and_the_look_is_refused(sealed, monkeypatch, failure):
    import subprocess
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import cycles

    real = subprocess.run

    def run(argv, **kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0" * 40 + "\n", stderr="")
        if argv[:2] != ["git", "status"]:
            return real(argv, **kwargs)                # platform's own calls (uname) are not the check
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 5)
        if failure == "oserror":
            raise OSError("git is gone")
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: index file corrupt")
    monkeypatch.setattr(cognition.subprocess, "run", run)
    assert cognition.environment()["git_dirty"] is True
    with pytest.raises(cognition.Refused, match="clean"):
        cycles.final("a test")
    with pytest.raises(cognition.Refused, match="clean"):
        cognition.run(["withheld"], final=True, note="a test")
    assert not sealed.exists()


def test_a_changed_worlds_file_is_refused_before_the_ledger_and_before_loading(sealed, monkeypatch):
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import cycles
    assert cycles.withheld_digest() == cycles.WITHHELD_SHA256       # the file as committed (hashed, never read)
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": False})
    monkeypatch.setattr(cycles, "WITHHELD_SHA256", "f" * 64)
    with pytest.raises(cognition.Refused, match="SHA-256"):
        cycles.final("a test")
    assert not sealed.exists()


def test_the_counterfactual_look_refuses_a_dirty_tree_and_an_output_inside_the_checkout(sealed, monkeypatch,
                                                                                       tmp_path):
    import importlib.util
    from pathlib import Path
    from neuroscifi.acceptance import cognition
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("bench_cognition_fix", root / "scripts" / "bench_cognition.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    inside = root / "docs" / "quality" / "evidence" / "a-test-output.json"
    look = ["run", "--family", "withheld", "--mechanism", "imagination", "--final", "--note", "a test"]
    assert bench.main(look + ["--out", str(inside)]) == 2
    assert bench.main(look) == 2                                   # no --out: refused as well
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": True})
    assert bench.main(look + ["--out", str(tmp_path / "out.json")]) == 2
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": None, "git_dirty": None})
    assert bench.main(look + ["--ablate", "front_door", "--out", str(tmp_path / "out.json")]) == 2
    assert not sealed.exists() and not inside.exists() and not (tmp_path / "out.json").exists()


def test_the_front_door_comparison_holds_on_a_held_out_pair_and_catches_a_change(tmp_path):
    """The pair the main session runs before the look, on held-out (no --final): 0 differing leaves outside the
    exemptions; a changed leaf outside them, a wrong ablation or a fallback is caught."""
    import importlib.util
    from pathlib import Path
    from neuroscifi.acceptance import cognition
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("compare_fd", root / "scripts" / "compare_front_door_look.py")
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    on = cognition.run(["counterfactual"], split="held_out", mechanism="imagination")
    off = cognition.run(["counterfactual"], split="held_out", mechanism="imagination", ablate=["front_door"])
    (tmp_path / "on.json").write_text(json.dumps(on))
    (tmp_path / "off.json").write_text(json.dumps(off))
    out = compare.compare(on, off)
    assert out["void"] == [] and out["a_holds"] and out["b_holds"] and out["differing_leaves"] > 0
    assert compare.main([str(tmp_path / "on.json"), str(tmp_path / "off.json")]) == 0
    changed = json.loads(json.dumps(off))
    changed["families"]["counterfactual"]["result"]["summary"]["mean_relative_error"]["mean"] = 0.5
    assert not compare.compare(on, changed)["b_holds"]
    assert compare.compare(on, on)["void"]                                      # OFF not ablated: void
    fell = json.loads(json.dumps(off))
    fell["families"]["counterfactual"]["mechanism"]["fell_back_to_baseline"] = True
    assert compare.compare(on, fell)["void"]


def test_the_look_configurations_read_back_as_named():
    from neuroscifi.acceptance.cognition import cycles, cycles_splits
    known = cycles.configurations() | cycles.EXTRA
    assert set(cycles.LOOKS["wave3"]) <= set(known) and set(cycles.LOOK_PAIRS) <= set(cycles.LOOKS["wave3"])
    spec = cycles_splits.CYCLES["development"]["worlds"][0]
    keys = ("attribution_independence", "echo_recognition", "supersession", "recheck_order", "idle_investigation")
    seen = {name: {k: cycles.run_world(spec, 41, known[name], cycles=1)["config"][k] for k in keys}
            for name in ("baseline", "shipped", "lane-s:fixes-off", "lane-s:echo-only", "lane-s:attribution-only",
                         "lane-s:both", "lane-s:supersede-K2", "shipped+idle", "idle-first")}
    step0 = (False, False, "ask", "staleness", "not run")
    assert tuple(seen["baseline"].values()) == step0 == tuple(seen["shipped"].values()) == \
        tuple(seen["lane-s:fixes-off"].values())
    assert tuple(seen["lane-s:echo-only"].values())[:2] == (False, True)
    assert tuple(seen["lane-s:attribution-only"].values())[:2] == (True, False)
    assert tuple(seen["lane-s:both"].values())[:2] == (True, True)
    assert seen["lane-s:supersede-K2"]["supersession"] == "independent"
    assert seen["shipped+idle"]["idle_investigation"] == "after the pass"
    assert seen["idle-first"]["idle_investigation"] == "before the pass"


def test_the_new_counters_count_on_development_worlds():
    """On lane S's development world with both fixes off (the holes open), admitted wrong content is counted without a
    version convention; with the rule on, replacements are classified and a false one carries its persistence."""
    from neuroscifi.acceptance.cognition import cycles, cycles_splits_s
    spec = cycles_splits_s.CYCLES["development"]["worlds"][1]           # sd-wrong-newer
    known = cycles.configurations() | cycles.EXTRA
    seen = {}
    for seed in (241, 242, 243):
        out = cycles.run_world(spec, seed, known["lane-s:supersede-K2"], generate=cycles_splits_s.generate)
        for k, v in out["admitted"].items():
            seen[k] = seen.get(k, 0) + v
        for k in ("true", "false", "by_rule_true", "by_rule_false"):
            seen["rep_" + k] = seen.get("rep_" + k, 0) + out["replacements"][k]
        for f in out["replacements"]["false_list"]:
            assert set(f) == {"subject", "cycle", "cycles", "wrong", "none", "censored"}
            assert f["cycles"] == f["wrong"] + f["none"]
        assert out["replacements"]["false_persistence_max"] == max(
            (f["cycles"] for f in out["replacements"]["false_list"]), default=0)
    assert seen["rows"] > 0 and seen["wrong"] > 0 and seen["wire_only"] + seen["own_only"] > 0
    assert seen["rep_true"] + seen["rep_false"] > 0 and seen["rep_by_rule_true"] + seen["rep_by_rule_false"] > 0


def test_false_persistence_counts_nothing_held_apart_and_censors_a_run_that_ends_wrong():
    from neuroscifi.acceptance.cognition import cycles

    class Fake:
        cycle = 6
        history = {"A": [(2, 3), (3, 3), (4, None), (5, 4), (6, 4)], "B": [(2, 3), (3, None), (4, 3), (5, 3)]}

        def truth(self, name, c):
            return 4 if name == "A" else 9
    assert cycles.false_persistence(Fake(), "A", 2) == {"cycles": 3, "wrong": 2, "none": 1, "censored": False}
    assert cycles.false_persistence(Fake(), "B", 2) == {"cycles": 5, "wrong": 3, "none": 2, "censored": True}
