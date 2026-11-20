"""Cross-module journeys with the real modules wired together (no doubles)."""
import json
import sqlite3

from neuroscifi.cli import main
from neuroscifi.instance import beliefs, checkpoints, datasets, evidence, executive, governance, graphs, store

from conftest import T0


def _teach(db, epoch, n=6, *, flip_camera_after=None, refs=3):
    evidence.start_epoch(db, epoch, T0)
    seq = 0
    for i in range(n):
        bit = 1 if flip_camera_after is not None and i >= flip_camera_after else 0
        for channel, root, b in (("camera", "cam-a", bit), ("word", "speaker-a", 0)):
            seq += 1
            evidence.observe(db, {"id": f"{epoch}-{seq}", "epoch": epoch, "channel": channel, "action": i % 2,
                                  "bit": b ^ (i % 2), "root": root}, T0 + seq)
    for r in range(refs):
        seq += 1
        evidence.observe(db, {"id": f"{epoch}-ref{r}", "epoch": epoch, "channel": "reference", "action": 0,
                              "bit": 0, "root": f"standard-{r}"}, T0 + seq)


def test_forget_records_the_real_post_deletion_belief_state(instance):
    db, _ = instance
    _teach(db, "bench")
    assert beliefs.compute(db, "bench")["mechanism"]["identifiable"]
    governance.forget_partner(db, "speaker-a", T0 + 100)  # words gone; references still pin the mechanism
    for r in range(3):
        governance.forget_evidence(db, f"bench-ref{r}", T0 + 101 + r)
    last = json.loads(db.execute(
        "SELECT summary FROM belief_revisions WHERE epoch='bench' ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert last["removed"] == 1
    assert last["mechanism"]["identifiable"] is False  # the revision shows what the deletion changed
    assert last["partners"] == {}
    # the next observation compares against a genuine compute() summary, so no spurious revision appears
    before = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
    evidence.observe(db, {"id": "late", "epoch": "bench", "channel": "camera", "action": 0, "bit": 0,
                          "root": "cam-a"}, T0 + 200)
    assert db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0] == before


def test_cli_sweeps_expired_evidence_before_reading(tmp_path, capsys):
    path = tmp_path / "i.sqlite"
    assert main(["init", "--db", str(path), "--ttl-days", "0.000001"]) == 0
    assert main(["epoch", "start", "e", "--db", str(path)]) == 0
    assert main(["observe", "e", "x1", "camera", "0", "0", "cam", "--db", str(path)]) == 0
    capsys.readouterr()
    import time
    time.sleep(0.2)
    assert main(["status", "e", "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["evidence"]["total"] == 0
    db = sqlite3.connect(path)
    assert db.execute("SELECT reason FROM tombstones WHERE id='x1'").fetchone() == ("expired",)


def test_grounding_then_return_to_earlier_setup_after_restart(instance):
    db, path = instance
    _teach(db, "bench", n=12, flip_camera_after=6)
    change = beliefs.detect_change(db, "bench")
    assert change["suspected"] and change["scope"] == "camera:cam-a" and not change["ambiguous"]
    decision = executive.decide(db, "bench", T0 + 500, record=True)
    assert decision["decision"] == "ask" and decision["scope"] == "camera:cam-a"
    _teach(db, "bench-remounted", n=6)
    db.close()
    db2 = store.open_existing(path)
    try:
        assert executive.pending(db2, epoch="bench")[0]["scope"] == "camera:cam-a"
        assert beliefs.compute(db2, "bench-remounted")["mechanism"]["identifiable"]
    finally:
        db2.close()


def test_forgetting_data_reaches_graphs_and_archives(instance, tmp_path):
    db, path = instance
    first = datasets.simulate(db, "desk", T0, seed=813240, steps=256)
    old = graphs.learn(db, "desk", T0 + 1, train=first["train"], validation=first["validation"], holder="t")
    assert graphs.promote(db, old, T0 + 2)["promoted"]
    second = datasets.simulate(db, "desk", T0 + 3, seed=813240)
    new = graphs.learn(db, "desk", T0 + 4, train=second["train"], validation=second["validation"], holder="t")
    assert graphs.promote(db, new, T0 + 5)["promoted"]
    evidence.start_epoch(db, "bench", T0)
    evidence.observe(db, {"id": "secret", "epoch": "bench", "channel": "camera", "action": 0, "bit": 1,
                          "root": "cam-a"}, T0 + 6)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 7)
    governance.forget_evidence(db, "secret", T0 + 8)
    effects = governance.forget_dataset(db, second["train"], T0 + 9)
    active = {r["context"]: r["id"] for r in graphs.listing(db) if r["status"] == "active"}
    assert active["desk"] == old  # dependent graph invalidated, eligible older skill restored
    assert db.execute("SELECT status FROM graph_revisions WHERE id=?", (new,)).fetchone()[0] == "invalidated"
    assert effects  # cascade reported
    assert {c["id"]: c["status"] for c in checkpoints.listing(db)}[cp["id"]] == "stale"
    rebuilt = checkpoints.rebuild_stale(db, path, tmp_path / "cps", T0 + 10)
    assert rebuilt
    assert not (tmp_path / "cps" / cp["path"].split("/")[-1]).exists()
    fresh = [c for c in checkpoints.listing(db) if c["status"] == "valid"]
    out = checkpoints.restore(db, fresh[0]["id"], tmp_path / "r.sqlite", T0 + 12)
    copy = sqlite3.connect(tmp_path / "r.sqlite")
    assert copy.execute("SELECT count(*) FROM evidence WHERE id='secret'").fetchone()[0] == 0
    assert out
