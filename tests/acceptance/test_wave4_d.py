"""Wave 4 lane D: housekeeping left open by Wave 3's reviews (each test fails without its fix)."""
from contextlib import closing
import functools
import importlib.util
import json
from pathlib import Path
import random
import time

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv, scoring
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.webcortex import autonomy, consolidate, orchestrator, scheduler, state

T0, DAY = adv.T0, adv.DAY


# ---- item 1: every saved setting is checked when it is read (security review L8, review3b N8: older ones open) ------

JUNK = [None, "x", "", "true", -1, 0, 1, 1.5, 10 ** 13, float("nan"), 1e308, True, False, [], ["a"], {}, {"x": 1},
        {"tasks": -1}, "http://example.com:3210"]


def _with(db, key, change):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    raw = json.loads(row[0]) if row and not isinstance(change, str) else {}
    with store.write(db):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, change if isinstance(change, str) else json.dumps(raw | change)))


def test_every_saved_web_setting_is_checked_and_a_damaged_one_reads_as_its_cautious_value(tmp_path):
    from dataclasses import fields
    from neuroscifi.webcortex import config as wconfig
    db = store.create(tmp_path / "fuzz-web.sqlite", T0, note="wave 4 lane D fuzz")
    with closing(db):
        state.set_config(db, {"enabled": True, "autonomous": True, "news_rendering": True}, T0)
        sound = db.execute("SELECT value FROM meta WHERE key=?", (wconfig.META_KEY,)).fetchone()[0]
        checked = 0
        for f in fields(wconfig.WebConfig):
            for junk in JUNK:
                _with(db, wconfig.META_KEY, sound)
                _with(db, wconfig.META_KEY, {f.name: junk})
                cfg = state.get_config(db)                     # never raises
                cfg.ceiling(), cfg.budget_for("deep_research")
                listed = [p for p in state.setting_problems(db) if f"saved {f.name} " in p]
                if wconfig.damaged(f.name, junk):
                    cautious = wconfig.CAUTIOUS.get(f.name, getattr(wconfig.WebConfig(), f.name))
                    assert getattr(cfg, f.name) == cautious, (f.name, junk)
                    rule, safe = wconfig.FAILS_CLOSED.get(f.name, (None, None))
                    assert rule is None or getattr(cfg, rule) == safe, (f.name, junk)
                    assert len(listed) == 1, (f.name, junk)
                    checked += 1
                else:
                    assert getattr(cfg, f.name) == junk and not listed, (f.name, junk)
        assert checked > 500
        for row in ("{not json", "[1, 2]", "7"):
            _with(db, wconfig.META_KEY, row)
            cfg = state.get_config(db)
            assert (cfg.enabled, cfg.autonomous, cfg.daily_autonomous["tasks"], cfg.read_ahead) == (False, False, 0, 1)
            assert len(state.setting_problems(db)) == 1


def test_a_damaged_allowance_or_guard_never_reads_looser_than_the_default():
    from neuroscifi.webcortex import config as wconfig
    default = wconfig.WebConfig()
    for name, value in wconfig.CAUTIOUS.items():
        before = getattr(default, name)
        if isinstance(before, dict):
            assert all(value[k] <= before[k] for k in before), name
        elif name.startswith("auto_provisional") or name == "reflection_interval_days":
            assert value >= before, name                # a higher bar, a longer interval
        elif name != "strategy_selection":
            assert value <= before, name
    assert set(wconfig.FAILS_CLOSED) >= {"deny_domains", "private_terms", "budget_ceiling"}


def test_every_saved_instance_setting_is_checked_and_a_damaged_one_is_listed(tmp_path):
    from dataclasses import fields
    from neuroscifi.instance import executive
    db = store.create(tmp_path / "fuzz-instance.sqlite", T0, note="wave 4 lane D fuzz")
    with closing(db):
        sound = db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]
        cautious = {"consolidation": False, "graph_rule_migration": False, "quiet": True,
                    "internal_state_history": False, "predictive_regulation": False, "decision_information": False,
                    **{n: False for n in store.IMAGINE_SWITCHES}, "ask_budget_per_epoch": 0}
        checks = store._SETTABLE | store._FIXED
        for f in fields(store.InstanceConfig):
            for junk in JUNK:
                _with(db, "config", sound)
                _with(db, "config", {f.name: junk})
                cfg = store.config(db)                         # never raises
                listed = [p for p in store.setting_problems(db) if f"saved {f.name} " in p]
                if checks[f.name](junk):
                    assert getattr(cfg, f.name) == junk or junk != junk, (f.name, junk)
                    assert not listed
                    continue
                assert len(listed) == 1, (f.name, junk)
                if f.name == "ask_budget_window_seconds":     # read by the executive, which says so with each decision
                    window, warning = executive._ask_window(cfg)
                    assert window == store.InstanceConfig.ask_budget_window_seconds and warning, junk
                elif f.name in store._FIXED:                  # fails closed (Wave 4 fix, review4 M1)
                    allowed, reason = store.write_allowed(db)
                    assert not allowed and f"saved {f.name} is damaged" in reason, (f.name, junk)
                    assert f"nsf config --repair-fixed {f.name}=" in reason and listed[0].endswith(")"), junk
                else:
                    assert getattr(cfg, f.name) == cautious.get(f.name, getattr(store.InstanceConfig, f.name)), \
                        (f.name, junk)


def test_every_saved_voice_setting_is_checked_and_a_damaged_one_reads_as_its_cautious_value(tmp_path):
    from neuroscifi.voice import config as vconfig
    db = store.create(tmp_path / "fuzz-voice.sqlite", T0, note="wave 4 lane D fuzz")
    with closing(db):
        vconfig.update(db, {"retain_audio": True, "audio_ttl_hours": 48})
        sound = db.execute("SELECT value FROM meta WHERE key='voice'").fetchone()[0]
        for name in vconfig.DEFAULTS:
            for junk in JUNK:
                _with(db, "voice", sound)
                _with(db, "voice", {name: junk})
                cfg = vconfig.get(db)                          # never raises
                listed = [p for p in vconfig.problems(db) if f" {name} (" in p]
                if vconfig._sound(name, junk):
                    assert cfg[name] == junk and not listed, (name, junk)
                else:
                    assert cfg[name] == vconfig.CAUTIOUS.get(name, vconfig.DEFAULTS[name]), (name, junk)
                    assert len(listed) == 1, (name, junk)
        _with(db, "voice", "{not json")
        assert vconfig.get(db)["retain_audio"] is False and vconfig.get(db)["consent"] == {}
        assert len(vconfig.problems(db)) == 1
        _with(db, "voice_pref:fam", {"speed": "fast", "mode": "text_only"})
        assert vconfig.effective(db, "fam", owner=False)["speed"] == vconfig.DEFAULTS["speed"]


OWNER_FIXED = {"evidence_ttl_seconds": 86400, "max_evidence_rows": 5000, "min_free_bytes": 1 << 20,
               "max_thermal_celsius": 70}
DAMAGED_FIXED = {"evidence_ttl_seconds": "1d", "max_evidence_rows": 5000.0, "min_free_bytes": "10G",
                 "max_thermal_celsius": "70"}


@pytest.mark.parametrize("name", sorted(OWNER_FIXED))
def test_a_damaged_setting_fixed_at_creation_pauses_writes_until_the_owner_repairs_it(tmp_path, name, caplog):
    """review4 M1: on main a damaged fixed setting made the write check fail; lane D read it as its loosest default
    (no thermal limit, a 7-day lifetime, a million rows, a 64 MiB reserve) and logged that as "cautious"."""
    from neuroscifi.instance import evidence
    from neuroscifi.instance.errors import ValidationError, WritesPaused
    path = tmp_path / f"fixed-{name}.sqlite"
    db = store.create(path, T0, note="wave 4 fix M1", config=store.InstanceConfig(**OWNER_FIXED))
    with closing(db):
        assert store.write_allowed(db, path)[0] in (True, False)          # sound: whatever the host says
        with pytest.raises(ValidationError):
            store.repair_fixed(db, name, OWNER_FIXED[name])                # a sound fixed setting stays fixed
        evidence.start_epoch(db, "desk", T0)
        store._REPORTED.discard(name)
        _with(db, "config", {name: DAMAGED_FIXED[name]})
        with caplog.at_level("WARNING"):
            allowed, reason = store.write_allowed(db, path)
        assert not allowed and reason.startswith(f"the saved {name} is damaged, so writes are paused")
        assert f"`nsf config --repair-fixed {name}=VALUE`" in reason
        assert not any("cautious" in r.getMessage() for r in caplog.records)
        assert any(f"{name} is damaged; writes are paused" in r.getMessage() for r in caplog.records)
        with pytest.raises(WritesPaused):
            evidence.observe(db, {"id": "ev-1", "epoch": "desk", "channel": "camera", "action": 1, "bit": 0,
                                  "root": "cam-a"}, T0, path=path)
        if name == "min_free_bytes":
            with pytest.raises(WritesPaused, match="min_free_bytes is damaged"):
                checkpoints.create(db, path, tmp_path / "ckpt", T0)
        listed = [p for p in store.setting_problems(db) if f"saved {name} " in p]
        assert len(listed) == 1 and "writes are paused" in listed[0]
        with pytest.raises(ValidationError):
            store.repair_fixed(db, name, "still junk")
        out = store.repair_fixed(db, name, OWNER_FIXED[name], T0)
        assert out["changed"] == [name] and getattr(store.config(db), name) == OWNER_FIXED[name]
        assert not store.fixed_damage(db) and not [p for p in store.setting_problems(db) if f"saved {name} " in p]


def test_the_owner_repairs_a_damaged_fixed_setting_with_the_named_command(tmp_path, capsys):
    from neuroscifi.cli import main
    path = tmp_path / "repair.sqlite"
    db = store.create(path, T0, note="wave 4 fix M1 cli")
    with closing(db):
        _with(db, "config", {"max_thermal_celsius": "70"})
        assert not store.write_allowed(db, path)[0]
    main(["config", "--db", str(path), "--repair-fixed", "max_thermal_celsius=70"])
    assert '"max_thermal_celsius"' in capsys.readouterr().out
    db = store.open_existing(path)
    with closing(db):
        assert store.config(db).max_thermal_celsius == 70 and not store.fixed_damage(db)


def test_damaged_saved_settings_are_shown_by_the_cli(tmp_path, capsys):
    from neuroscifi.cli import main
    from neuroscifi.voice import config as vconfig
    path = tmp_path / "cli.sqlite"
    db = store.create(path, T0, note="wave 4 lane D cli")
    with closing(db):
        state.set_config(db, {"enabled": True}, T0)
        _with(db, "webcortex", {"daily_autonomous": "lots"})
        _with(db, "config", {"min_free_bytes": "none"})
        vconfig.update(db, {"retain_audio": False})
        _with(db, "voice", {"audio_ttl_hours": "a week"})
    for argv, needle in ((["web", "config", "--db", str(path)], "daily_autonomous"),
                         (["config", "--db", str(path)], "min_free_bytes"),
                         (["voice", "settings", "--db", str(path)], "audio_ttl_hours")):
        capsys.readouterr()
        main(argv)
        assert needle in capsys.readouterr().out, argv


# ---- item 6: the caution shown to the owner (ADR-065; display only) --------------------------------------------------

def _claims_of(db, *hosts, value=None):
    rows = [dict(zip(state.CLAIM_COLUMNS, r)) for r in db.execute(
        f"SELECT {','.join(state.CLAIM_COLUMNS)} FROM web_claims ORDER BY rowid")]
    return [r for r in rows if r["domain"] in hosts and (value is None or value in (r["claim_values"] or ""))]


def test_the_caution_says_when_pages_look_like_copies_and_when_only_one_source_says_it():
    from neuroscifi.webcortex import caution
    n = adv._SN
    with adv._s_individual("caution-copies", **adv.LANE_S_OFF) as (db, _):
        adv._s_look(db, T0, [("relnotes.example", f"{n} 1.5.0 is the latest {n} release.", T0 - DAY, None),
                             ("mirror.example", f"{n} 1.5.0 is the latest {n} release.", T0 - DAY, None),
                             ("pkgwatch.example", f"As of this week, the newest {n} release is {n} 1.5.0, its notes "
                                                  "say.", T0 - DAY, None)])
        copies = caution.view(db, _claims_of(db, "relnotes.example", "mirror.example", "pkgwatch.example",
                                             value="1.5.0"))
        assert (copies["pages"], copies["groups"], copies["joined"]) == (3, 2, ["near_duplicate"])
        assert "count as 2 sources" in copies["text"] and "nearly the same wording" in copies["text"]
        assert "reworded copy" in copies["text"] and copies["own_answer"] is False
        one = caution.view(db, _claims_of(db, "pkgwatch.example", value="1.5.0"))
        assert one["text"].startswith("Only one source says this.") and one["brief"]
        two = caution.facts(db, _claims_of(db, "relnotes.example", "pkgwatch.example", value="1.5.0"))
        assert (two["pages"], two["groups"]) == (2, 2) and not caution.shown(two)
        assert caution.view(db, _claims_of(db, "relnotes.example", "pkgwatch.example", value="1.5.0")) is None


def test_the_own_answer_flag_is_shown_to_the_owner_only_and_decides_nothing():
    from neuroscifi.webcortex import caution, knowledge
    n = adv._SN
    with adv._s_individual("caution-own", **adv.LANE_S_OFF) as (db, _):
        adv._s_look(db, T0 - 5 * DAY, [], answer=f"{n} 9.4.0 is the latest {n} release.")
        before = [(r["id"], r["status"], r["confidence"]) for r in knowledge.rows(db)]
        out = adv._s_look(db, T0, [
            ("askboard.example", f"Someone on the forum says {n} 9.4.0 is now the latest {n} release.", T0 - 3600,
             None),
            ("answer-roundup.example", f"Answers roundup: {n} 9.4.0 is the latest {n} release.", T0 - 3600, None)])
        rows = _claims_of(db, "askboard.example", "answer-roundup.example", value="9.4.0")
        owner = caution.view(db, rows)
        assert owner["own_answer"] is True and "said earlier" in owner["text"]
        assert owner["brief"] == "Careful: a source may just repeat something I said before."
        family = caution.view(db, rows, own_check=False)
        assert family is None or ("said earlier" not in family["text"] and family["own_answer"] is None)
        # the shipped defaults admitted it (echo_recognition off): the caution changed nothing it could
        assert out["provisional"] and [(r["id"], r["status"], r["confidence"]) for r in knowledge.rows(db)
                                       if r["id"] in {b[0] for b in before}] == before
        [shown] = caution.for_knowledge(db, [knowledge.row(db, out["provisional"][0])])
        assert shown["own_answer"] is True


def test_the_caution_is_display_only_no_other_module_reads_it():
    """Only the views import webcortex/caution.py; nothing that decides (consolidation, claims, autonomy, the
    orchestrator, knowledge, MCP) does."""
    allowed = {"console/server.py", "webcortex/cli.py", "pocket/server.py", "voice/hub.py", "voice/bridge.py"}
    src = ROOT / "src" / "neuroscifi"
    found = set()
    for path in src.rglob("*.py"):
        if "withheld" in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        if "import caution" in text or "caution import" in text or "webcortex.caution" in text:
            found.add(str(path.relative_to(src)))
    assert found == allowed, found


def _staged_report_task(db):
    """A finished task whose report quotes a statement three pages support, two of them copies."""
    n = adv._SN
    adv._s_look(db, T0, [("relnotes.example", f"{n} 1.5.0 is the latest {n} release.", T0 - DAY, None),
                         ("mirror.example", f"{n} 1.5.0 is the latest {n} release.", T0 - DAY, None),
                         ("pkgwatch.example", f"As of this week, the newest {n} release is {n} 1.5.0, its notes say.",
                          T0 - DAY, None)])
    rows = _claims_of(db, "relnotes.example", "mirror.example", "pkgwatch.example", value="1.5.0")
    task_id = rows[0]["task_id"]
    sources = [{"n": i + 1, "url": r["source_url"], "domain": r["domain"], "title": n, "cited": True}
               for i, r in enumerate(rows)]
    report = {"answer": [{"type": "quote", "text": rows[0]["text"], "citations": [1, 2], "corroborating": [3],
                          "status": "corroborated", "confidence": 0.8, "copies": 2, "independent_sources": 2}],
              "findings": [{"statement": rows[0]["text"], "claims": [r["id"] for r in rows]}], "sources": sources}
    with store.write(db):
        db.execute("UPDATE web_tasks SET status='completed', result=? WHERE id=?", (json.dumps(report), task_id))
    return task_id


def test_the_console_cli_and_pocket_show_the_caution_and_pocket_never_the_own_answer_flag(tmp_path, capsys):
    from neuroscifi.cli import main
    from neuroscifi.pocket import server as pocket
    with adv._s_individual("caution-views", **adv.LANE_S_OFF) as (db, path):
        task_id = _staged_report_task(db)
        task = orchestrator.get(db, task_id)
        detail = pocket.task_detail(task, db)
        assert "count as 2 sources" in detail["report"]["caution"]["answer"][0]
        assert "own_answer" not in json.dumps(detail) and "said earlier" not in json.dumps(detail)
        assert "caution" not in pocket.task_detail(task)["report"]           # no database: nothing computed
        capsys.readouterr()
        main(["web", "show", task_id, "--db", str(path)])
        shown = json.loads(capsys.readouterr().out)
        assert "count as 2 sources" in shown["caution"]["answer"][0]["text"]


# ---- item 2: requeue honours the day's autonomous task count (review3b L6/N5, (f)) --------------------------------

def _capped(tmp_path, cap):
    db = store.create(tmp_path / "requeue.sqlite", T0, note="wave 4 lane D requeue")
    state.set_config(db, {"enabled": True, "daily_autonomous": {"tasks": cap, "requests": 400,
                                                                "bytes": 80_000_000}}, T0)
    return db


def _ended(db, task_id, status, at):
    with store.write(db):
        db.execute("UPDATE web_tasks SET status=?, created_at=?, updated_at=?, started_at=?, finished_at=? WHERE id=?",
                   (status, at, at, at, at, task_id))


def test_requeue_never_queues_an_autonomous_task_past_the_days_count(tmp_path):
    with closing(_capped(tmp_path, 2)) as db:
        old = orchestrator.submit(db, "What is the current Zorbix version?", T0 - 3 * DAY, mode="fact_verification",
                                  origin="curiosity")["id"]
        part = orchestrator.submit(db, "What is the current Quorvex version?", T0 - 3 * DAY,
                                   mode="fact_verification", origin="curiosity")["id"]
        _ended(db, old, "failed", T0 - 3 * DAY)
        _ended(db, part, "partial", T0 - 3 * DAY)
        for i in range(2):              # today's two places are taken
            orchestrator.submit(db, f"What is the current Racer{i} version?", T0, mode="fact_verification",
                                origin="curiosity")
        assert scheduler.autonomous_usage(db, T0 + 1)["tasks"] == 2
        with pytest.raises(autonomy.BudgetRefused):         # the same task, queued again from three days ago
            orchestrator.requeue(db, old, T0 + 1)
        with pytest.raises(autonomy.BudgetRefused):         # a child that keeps the autonomous origin
            orchestrator.requeue(db, part, T0 + 1)
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 2
        assert state.task(db, old)["status"] == "failed"


def test_requeue_of_the_owners_work_is_never_counted_or_refused(tmp_path):
    with closing(_capped(tmp_path, 1)) as db:
        mine = orchestrator.submit(db, "Owner question: what is the latest Zorbix release?", T0 - 3 * DAY,
                                   mode="fact_verification", origin="console")["id"]
        theirs = orchestrator.submit(db, "What is the current Quorvex version?", T0 - 3 * DAY,
                                     mode="fact_verification", origin="curiosity")["id"]
        _ended(db, mine, "failed", T0 - 3 * DAY)
        _ended(db, theirs, "partial", T0 - 3 * DAY)
        orchestrator.submit(db, "What is the current Racer version?", T0, mode="fact_verification", origin="curiosity")
        assert orchestrator.requeue(db, mine, T0 + 1, origin="console")["requeued"] == "same"
        # the owner continuing a background task from the console queues the owner's own work (origin console)
        assert orchestrator.requeue(db, theirs, T0 + 1, origin="console")["requeued"] == "child"
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 1


def test_requeue_of_an_autonomous_task_already_counted_today_is_not_refused(tmp_path):
    with closing(_capped(tmp_path, 1)) as db:
        today = orchestrator.submit(db, "What is the current Zorbix version?", T0, mode="fact_verification",
                                    origin="curiosity")["id"]
        _ended(db, today, "failed", T0)
        assert orchestrator.requeue(db, today, T0 + 1)["requeued"] == "same"      # it holds its place already
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 1


# ---- item 3: a checkpoint restore scans the answers before its write transaction (review3c F6) --------------------

def _bulk_answers(db, n, size, rnd):
    """n stored model-written answers of about `size` characters, full of numbers (none about the staged subject)."""
    task = orchestrator.submit(db, adv._SQ, T0 - 2e6, mode="fact_verification", origin="console")
    cols = [c[1] for c in db.execute("PRAGMA table_info(web_tasks)")]
    row = dict(zip(cols, db.execute("SELECT * FROM web_tasks WHERE id=?", (task["id"],)).fetchone()))
    filler = " ".join(f"Item{i} {rnd.randint(100, 99999)}.{rnd.randint(0, 9)} was noted." for i in range(size // 15))
    result = json.dumps({"reasoning": {"generated_by": "model:x", "text": filler[:size]}})
    with store.write(db):
        for i in range(n):
            r = dict(row, id=f"bulk{i:07d}", result=result, updated_at=T0 - 1e6 + i, finished_at=T0 - 1e6 + i)
            db.execute(f"INSERT INTO web_tasks({','.join(r)}) VALUES({','.join('?' * len(r))})", tuple(r.values()))


def test_a_checkpoint_restore_reads_no_task_result_by_scanning_inside_its_write_transaction(tmp_path, monkeypatch):
    """The second fix lane moved the live forget's scan of every answer before its lock (review3b N2); a checkpoint
    replay still ran it inside the restore's transaction (1.5-1.7 s at 10,000 answers of 20 kB; review3c F6). Now it
    runs before it, and the mark inside reads only the candidates by rowid. The mark itself is unchanged (the M3
    replay test in test_wave3_fix.py, and the last lines here)."""
    rnd = random.Random(5)
    name = adv._SN
    with adv._s_individual("restore-scan") as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [("relnotes.example", f"{name} 1.5.0 is the latest {name} release.",
                                        T0 - 6 * DAY, None)], answer=f"{name} 1.5.0 is the latest {name} release.")
        _bulk_answers(db, 10_000, 2_000, rnd)
        made = checkpoints.create(db, path, tmp_path / "checkpoints", T0 - 4.5 * DAY)
        governance.forget_source(db, "relnotes.example", T0 - 4 * DAY)
        full_scans, inside = [], []
        real_rows, real_replay = governance._answer_rows, governance.replay_web_forgets

        def rows(conn, tokens, rowids=None):
            if rowids is None:
                full_scans.append(conn.in_transaction)
            return real_rows(conn, tokens, rowids)

        def replay(conn, *a, **k):
            started = time.perf_counter()
            try:
                return real_replay(conn, *a, **k)
            finally:
                inside.append(time.perf_counter() - started)
        monkeypatch.setattr(governance, "_answer_rows", rows)
        monkeypatch.setattr(governance, "replay_web_forgets", replay)
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, made["id"], copy, T0 - 3 * DAY)
        assert full_scans and True not in full_scans, full_scans
        assert inside and max(inside) < 1.0, inside
        with closing(store.connect(copy)) as restored:
            assert restored.execute("SELECT count(*) FROM web_claims WHERE domain='relnotes.example'"
                                    ).fetchone()[0] == 0
            answers = [json.loads(r)["reasoning"] for (r,) in restored.execute(
                "SELECT result FROM web_tasks WHERE result LIKE ? AND id NOT LIKE 'bulk%'", ("%model:%",))]
            assert answers and all(consolidate.own_sentences(r) == [] for r in answers)


# ---- item 5: the harnesses write summaries by default, the per-cycle histories only on request ---------------------

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "quality" / "evidence"


def _script(name):
    spec = importlib.util.spec_from_file_location(f"wave4d_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def test_the_learning_cycles_harness_writes_summaries_unless_asked_for_the_history(monkeypatch, tmp_path):
    from neuroscifi.acceptance.cognition import cycles
    bench = _script("bench_learning_cycles")
    monkeypatch.setattr(cycles, "run_split", functools.partial(cycles.run_split, cycles=2, seeds=(41,)))
    monkeypatch.setattr(cycles, "idle_configurations", lambda: {k: v for k, v in cycles.configurations().items()
                                                                if k == "baseline"})
    small, full = tmp_path / "idle.json", tmp_path / "idle_full.json"
    assert bench.main(["idle", "--split", "development", "--out", str(small)]) == 0
    assert bench.main(["idle", "--split", "development", "--out", str(full), "--full-history"]) == 0
    a, b = json.loads(small.read_text()), json.loads(full.read_text())
    assert "series_mean" in _keys(b) and not _keys(a) & set(scoring.HISTORY_KEYS)
    assert a["verdicts"] == b["verdicts"] and a["safety_all_runs"] == b["safety_all_runs"]
    per_seed = [r["configurations"]["baseline"]["worlds"][w]["per_seed"] for r in (a["runs"]["development"],
                                                                                  b["runs"]["development"])
                for w in sorted(r["configurations"]["baseline"]["worlds"])]
    half = len(per_seed) // 2
    assert [[s[cycles.PRIMARY] for s in x] for x in per_seed[:half]] == \
        [[s[cycles.PRIMARY] for s in x] for x in per_seed[half:]]
    assert small.stat().st_size < full.stat().st_size


def test_the_source_rules_harness_writes_summaries_unless_asked_for_the_history(monkeypatch, tmp_path):
    from neuroscifi.acceptance.cognition import cycles_s
    bench = _script("bench_wave3_s")
    monkeypatch.setattr(cycles_s, "run_split", functools.partial(cycles_s.run_split, cycles_=2, seeds=(241,)))
    small, full = tmp_path / "s.json", tmp_path / "s_full.json"
    args = ["run", "--family", "lane-s", "--split", "development", "--config", "baseline", "--config", "fixes"]
    assert bench.main([*args, "--out", str(small)]) == 0
    assert bench.main([*args, "--out", str(full), "--full-history"]) == 0
    a, b = json.loads(small.read_text()), json.loads(full.read_text())
    assert {"history", "items"} <= _keys(b) and not _keys(a) & {"history", "series_mean", "items"}
    assert a["verdicts"] == b["verdicts"] and "classes" in _keys(a)


@pytest.mark.parametrize("name, rule", [
    ("genesis_wave3_fix_orders.json", "order_verdicts"), ("genesis_wave3_fix_idle.json", "idle_verdicts"),
    ("genesis_recheck_order.json", "order_verdicts")])
def test_the_committed_wave3_runs_give_the_same_verdicts_without_their_histories(name, rule):
    """What the pre-registered rules read is in the summaries: dropping the per-cycle histories from the committed
    Wave 3 runs changes no verdict (the files themselves are not rewritten)."""
    from neuroscifi.acceptance.cognition import cycles
    runs = json.loads((EVIDENCE / name).read_text())["runs"]
    for split, run in runs.items():
        assert getattr(cycles, rule)(run) == getattr(cycles, rule)(scoring.summary(run)), split


def test_the_committed_source_rule_runs_give_the_same_verdicts_without_their_histories():
    from neuroscifi.acceptance.cognition import cycles_s
    results = json.loads((EVIDENCE / "genesis_wave3_s.json").read_text())["results"]
    compared = 0
    for name, result in results.items():
        short = scoring.summary(result)
        for config in result["configurations"]:
            if config != "baseline":
                assert cycles_s.verdict(result, config, "baseline") == cycles_s.verdict(short, config, "baseline")
                compared += 1
    assert compared


def test_the_compact_layout_reads_back_unchanged():
    value = {"a": [1, 2.5, {"b": None, "c": "x\n"}], "d": {"e": {"f": {"g": {"h": [1, {"i": []}]}}}}, "j": {}}
    assert json.loads(scoring.dumps(value)) == value and json.loads(scoring.dumps(value, levels=0)) == value
