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
