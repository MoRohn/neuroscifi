"""ADR-054: the executive's precision report (executive.precision, `nsf regulation --precision`) is computed from
the records that already exist (pending, changepoints, epoch_links), per rule and per epoch, and is display-only."""
import json

from neuroscifi.instance import executive, store

from conftest import T0


def _ask(db, pid, epoch, kind, scope, question, resolution=None):
    db.execute("INSERT INTO pending(id,epoch,kind,scope,question,created_at,resolved_at,resolution) "
               "VALUES(?,?,?,?,?,?,?,?)", (pid, epoch, kind, scope, question, T0 + pid,
                                           None if resolution is None else T0 + 100 + pid, resolution))


def _stage(db):
    with store.write(db):
        _ask(db, 1, "e1", "calibrate", "camera:c1", "The readings for camera:c1 since evidence #4 ...", "yes")
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e1','camera:c1',4,1,?,1)", (T0,))
        _ask(db, 2, "e1", "calibrate", "camera:c1", "The readings for camera:c1 since evidence #9 ...", "no")
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e1','camera:c1',12,2,?,0)", (T0,))
        _ask(db, 3, "e2", "calibrate", "partner:p", "The readings for partner:p since evidence #2 ...",
             executive.EXPIRED)
        _ask(db, 4, "e2", "calibrate", "mechanism", "The readings for mechanism since evidence #3 ...")
        _ask(db, 5, "e2", "ask", "camera:c2", "Something changed around evidence #5: was it camera:c2 or ...?",
             "resolved by evidence: the executive now recommends act")
        _ask(db, 6, "e3", "ask", "epoch:e1", "This looks like e1; continue with that calibration?", "yes")
        db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) "
                   "VALUES('e3','e1','[]',6,?)", (T0,))
        _ask(db, 7, "e3", "ask", "partner:q", "What does q mean by their words? ...", "they mean the opposite")


def test_precision_counts_every_question_by_rule_and_epoch(instance):
    db, _ = instance
    _stage(db)
    version = store.state_version(db)
    report = executive.precision(db)
    assert store.state_version(db) == version            # computed on read; nothing written
    alarm = report["by_rule"]["change_alarm"]
    assert {k: alarm[k] for k in ("asked", "confirmed", "rejected", "expired", "open")} == \
        {"asked": 4, "confirmed": 1, "rejected": 1, "expired": 1, "open": 1}
    assert alarm["precision"] == 0.5 and alarm["owner_answered_share"] == round(2 / 3, 4)
    assert report["by_rule"]["change_ambiguous"]["withdrawn"] == 1
    assert report["by_rule"]["change_ambiguous"]["precision"] is None
    assert report["by_rule"]["epoch_match"]["confirmed"] == 1 and report["by_rule"]["epoch_match"]["precision"] == 1
    assert report["by_rule"]["partner_meaning"]["answered"] == 1
    assert report["by_epoch"]["e1"]["change_alarm"]["precision"] == 0.5
    assert report["by_epoch"]["e2"]["change_alarm"]["precision"] is None
    assert report["display_only"] is True


def test_precision_matches_the_grounding_scenarios_own_count_of_owner_answers(tmp_path):
    from neuroscifi.acceptance import scenarios
    db = store.create(tmp_path / "g.sqlite", scenarios.T0)
    try:
        confirmed = rejected = 0
        for index in range(12):
            logged = scenarios._run_detecting(db, scenarios._grounding_lifetime(0, index), index, manual=False)
            confirmed += logged["owner"]["confirmed"]
            rejected += logged["owner"]["rejected"]
        by_rule = executive.precision(db)["by_rule"]
    finally:
        db.close()
    change = [by_rule.get(r) or {} for r in ("change_alarm", "change_ambiguous")]
    assert confirmed + rejected > 0
    assert sum(r.get("confirmed", 0) for r in change) == confirmed
    assert sum(r.get("rejected", 0) for r in change) == rejected


def test_nsf_regulation_reports_precision_only_when_asked(tmp_path, capsys):
    from neuroscifi import cli
    path = str(tmp_path / "cli.sqlite")
    assert cli.main(["init", "--db", path]) == 0
    capsys.readouterr()
    db = store.open_existing(path)
    try:
        _stage(db)
    finally:
        db.close()
    assert cli.main(["regulation", "--db", path]) == 0
    assert "precision" not in json.loads(capsys.readouterr().out)
    assert cli.main(["regulation", "--db", path, "--precision"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["precision"]["by_rule"]["change_alarm"]["precision"] == 0.5
