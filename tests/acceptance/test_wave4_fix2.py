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



# ---- item 2 (security, second pass, 2b(iii)): `--forget-own-text` is recorded and replayed on restore ---------------

def test_forget_own_text_survives_a_restore_of_an_archive_taken_before_it(tmp_path, capsys):
    """review4 s/r2/l3_forget_restore: before the fix the removal left no record, every checkpoint stayed valid, and a
    restore of an older archive brought the fingerprints back. Now it is a deletion_log row (kind own_text) that a
    restore replays, and the archives taken before it are stale (then rewritten without the rows)."""
    from contextlib import closing
    from neuroscifi.cli import main
    from neuroscifi.instance import checkpoints, store
    with _own("forget-restore") as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [], answer=ANSWER)
        older = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 4.5 * DAY)
        db.commit()
        main(["web", "config", "--db", str(path), "--forget-own-text"])
        out = json.loads(capsys.readouterr().out)
        assert out["own_text_removed"] == 2 and emitted.stored(db) == 0
        logged = db.execute("SELECT target_id, effects FROM deletion_log WHERE target_kind=?",
                            (emitted.DELETION_KIND,)).fetchall()
        assert len(logged) == 1 and json.loads(logged[0][1])["own_text_removed"] == 2
        assert older["id"] in json.loads(logged[0][1])["checkpoints_stale"]
        assert emitted.DELETION_KIND in checkpoints.REWRITABLE_KINDS
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, older["id"], copy, T0 - 3 * DAY)
        with closing(store.connect(copy)) as restored:
            assert emitted.stored(restored) == 0
            assert restored.execute("SELECT count(*) FROM web_strategies WHERE name LIKE 'emitted%'").fetchone()[0] == 0
            # the copy remembers the removal: an older archive restored FROM it loses the rows too
            assert restored.execute("SELECT count(*) FROM deletion_log WHERE target_kind=?",
                                    (emitted.DELETION_KIND,)).fetchone()[0] == 1
        # what the individual emitted AFTER the removal is kept by a restore of an archive taken after it
        adv._s_look(db, T0 - 2 * DAY, [], answer=ANSWER)
        newer = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 1.5 * DAY)
        copy2 = tmp_path / "restored2.sqlite"
        checkpoints.restore(db, newer["id"], copy2, T0 - DAY)
        with closing(store.connect(copy2)) as restored:
            assert emitted.stored(restored) == 1


# ---- item 3 (security, second pass, 2a): `nsf config --repair-fixed` ----------------------------------------------

def test_the_repair_says_the_creation_value_is_recorded_nowhere_and_leaves_an_audit_row(tmp_path, capsys):
    """review4 s/r2/m1_pause: the refusal and the help asked for "the value chosen when the individual was created",
    which the file records nowhere. The repair takes only what the setting's own check accepts, says so when it
    refuses, says plainly that the creation value is not recorded, and leaves a deletion_log row."""
    from contextlib import closing
    import pytest
    from neuroscifi.cli import main
    from neuroscifi.instance import store
    from neuroscifi.instance.errors import ValidationError
    path = tmp_path / "repair2.sqlite"
    db = store.create(path, T0, note="wave 4 second fix round", config=store.InstanceConfig(max_evidence_rows=5000))
    with closing(db):
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        with store.write(db):
            db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw | {"max_evidence_rows": 5e3}),))
        reason = store.write_allowed(db, path)[1]
        assert "recorded nowhere" in reason and "a whole number from 0 to" in reason
        for bad in (5000.0, -1, True, "5000", store.MAX_ROW_LIMIT + 1):
            with pytest.raises(ValidationError, match="a whole number from 0 to"):
                store.repair_fixed(db, "max_evidence_rows", bad)
    main(["config", "--db", str(path), "--repair-fixed", "max_evidence_rows=5000"])
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] == ["max_evidence_rows"] and any("recorded nowhere" in n for n in out["notes"])
    db = store.open_existing(path)
    with closing(db):
        rows = [json.loads(e) for (e,) in db.execute(
            "SELECT effects FROM deletion_log WHERE target_kind='setting' AND target_id='max_evidence_rows'")]
        assert len(rows) == 1 and rows[0]["repair"] is True and rows[0]["to"] == 5000 and rows[0]["from"] == 5000.0
        assert rows[0]["creation_value_recorded"] is False


# ---- item 4 (security, second pass, 2a note): an agent is told only that the individual is paused for repair --------

def _damaged(tmp_path, where, name, bad):
    from neuroscifi.instance import store
    from neuroscifi.webcortex import config as wconfig, state
    path = tmp_path / f"agent-{name}.sqlite"
    db = store.create(path, T0, note="wave 4 second fix round")
    state.set_config(db, {"enabled": True}, T0)
    key = "config" if where == "instance" else wconfig.META_KEY
    raw = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
    with store.write(db):
        db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(raw | {name: bad}), key))
    db.close()
    return path


def _agent_text(result):
    assert result["isError"] is True
    return result["content"][0]["text"]


def test_an_agents_refused_call_does_not_name_the_damaged_setting_the_owner_still_sees_it(tmp_path):
    import pytest
    from neuroscifi import self_description
    from neuroscifi.instance import store
    from neuroscifi.instance.errors import Conflict, WritesPaused
    from neuroscifi.webcortex import mcp, orchestrator, state
    paused = f"Error: {store.PAUSED_FOR_REPAIR}"
    path = _damaged(tmp_path, "instance", "max_thermal_celsius", "70")
    server = mcp.Server(path, clock=lambda: T0)
    said = _agent_text(server.call("web_research", {"objective": "What is the latest Velmora release?"}))
    assert said == paused and "thermal" not in said
    record = json.dumps(server.call("self_status", {}))
    assert "max_thermal_celsius" not in record and store.PAUSED_FOR_REPAIR in record
    db = store.open_existing(path)
    try:                                                  # the owner's surfaces keep the full message
        with pytest.raises(WritesPaused, match="saved max_thermal_celsius is damaged"):
            orchestrator.submit(db, "What is the latest Velmora release?", T0, origin="cli")
        owner = json.dumps(self_description.build(db, path, T0, scope="owner"))
        assert "saved max_thermal_celsius is damaged" in owner
    finally:
        db.close()
    path = _damaged(tmp_path, "web", "deny_domains", "evil.example")
    server = mcp.Server(path, clock=lambda: T0)
    for tool, args in (("web_search", {"query": "velmora release"}),
                       ("web_research", {"objective": "What is the latest Velmora release?"})):
        said = _agent_text(server.call(tool, args))
        assert said == paused and "deny_domains" not in said, tool
    db = store.open_existing(path)
    try:
        with pytest.raises(Conflict, match="saved deny_domains is damaged"):
            state.require_enabled(db)
    finally:
        db.close()


# ---- item 5 (security, second pass, 2d): a warning when an unsupported rule is turned on ----------------------------

def test_turning_an_unsupported_rule_on_says_its_recorded_verdict_and_still_obeys(tmp_path, capsys):
    """review4 s 2d: `nsf web config --set source_derivation=true` was accepted silently. It still is accepted (the
    owner decides), and now says the recorded verdict; the same for own_text_provenance with its stated limits.
    Turning either off, or setting anything else, says nothing."""
    from neuroscifi.cli import main
    from neuroscifi.instance import store
    from neuroscifi.webcortex import state
    path = tmp_path / "warn.sqlite"
    store.create(path, T0, note="wave 4 second fix round").close()
    expected = {"source_derivation": ("not supported by the evidence: see ADR-066",
                                      "can keep true facts from being confirmed"),
                "own_text_provenance": ("see ADR-067", "30 days", "can keep a true new value from being confirmed")}
    for name, phrases in expected.items():
        main(["web", "config", "--db", str(path), "--set", f"{name}=true"])
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["config"][name] is True and len(out["warnings"]) == 1
        assert all(p in out["warnings"][0] for p in phrases), out["warnings"]
        assert out["warnings"][0] in captured.err
        main(["web", "config", "--db", str(path), "--set", f"{name}=false"])
        assert "warnings" not in json.loads(capsys.readouterr().out)
    main(["web", "config", "--db", str(path), "--set", "echo_recognition=true"])
    assert "warnings" not in json.loads(capsys.readouterr().out)
    db = store.open_existing(path)
    try:
        assert state.get_config(db).source_derivation is False and state.get_config(db).echo_recognition is True
    finally:
        db.close()
