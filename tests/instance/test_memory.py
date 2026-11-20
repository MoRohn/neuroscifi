import json

import pytest

from neuroscifi.instance import memory, store
from neuroscifi.instance.errors import ValidationError
from _seed import T0, add, pending


def seed(db):
    for i in range(5):
        add(db, f"e{i}", recorded_at=T0 + i, expires_at=T0 + 100 + i, root="r1" if i % 2 else "r2",
            channel="camera" if i < 3 else "word")
    pending(db)
    db.execute("INSERT INTO decisions(epoch,state_version,decision,reason,created_at) VALUES('e1',1,'ask','why',?)", (T0,))
    db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES('e1',2,?,?,?)",
               (T0, "evidence:e0", json.dumps({"confidence": 0.8, "map": "x"})))
    db.execute("INSERT INTO datasets(context,split,provenance,rows,cols,sha256,created_at) VALUES('c','train','simulated',8,3,'ab',?)", (T0,))
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES('evidence','e9',?,'{\"deleted\":1}')", (T0,))
    db.execute("INSERT INTO checkpoints(path,policy,sha256,bytes,state_version,max_evidence_seq,created_at,status) VALUES('/p','cold','h',1,1,5,?,'valid')", (T0,))


def test_shapes_for_each_kind(instance):
    db, _ = instance
    seed(db)
    keys = {"kind", "id", "scope", "time", "permissions", "confidence", "outcome_history", "payload"}
    for kind in memory.KINDS:
        out = memory.retrieve(db, kind)
        assert out["state_version"] == store.state_version(db)
        for item in out["items"]:
            assert set(item) == keys and item["kind"] == kind
            assert set(item["permissions"]) == {"consent", "retention", "expires_at"}
            json.dumps(item, allow_nan=False)
    ev = memory.retrieve(db, "evidence", id="e0")["items"][0]
    assert ev["scope"] == {"epoch": "e1", "root": "r2"} and ev["confidence"] is None
    assert ev["permissions"] == {"consent": "learn", "retention": "standard", "expires_at": T0 + 100}
    assert ev["payload"]["channel"] == "camera" and ev["payload"]["seq"] == 1
    assert memory.retrieve(db, "belief")["items"][0]["confidence"] == 0.8
    assert memory.retrieve(db, "belief")["items"][0]["payload"]["summary"]["map"] == "x"
    assert memory.retrieve(db, "dataset")["items"][0]["scope"]["context"] == "c"
    assert memory.retrieve(db, "deletion")["items"][0]["payload"]["effects"] == {"deleted": 1}
    assert memory.retrieve(db, "pending", epoch="e1")["items"][0]["payload"]["question"] == "which way?"
    assert memory.retrieve(db, "decision")["items"][0]["payload"]["decision"] == "ask"
    assert memory.retrieve(db, "checkpoint", id="1")["items"][0]["payload"]["status"] == "valid"


def test_graph_record(instance):
    db, _ = instance
    seed(db)
    db.execute("INSERT INTO graph_revisions(context,status,bits,degree,rows,cols,byte_size,sha256,blob,rule_version,origin,metrics,decision,created_at)"
               " VALUES('c','archived',4,2,3,3,10,'h',x'00','gate-v1','learner',?,?,?)",
               (json.dumps({"heldout_mse": 0.25}), json.dumps({"promoted": False}), T0))
    item = memory.retrieve(db, "graph", context="c")["items"][0]
    assert item["confidence"] == 0.25 and item["outcome_history"] == [{"promoted": False}]
    assert "blob" not in item["payload"]


def test_filters_time_range_and_limit(instance):
    db, _ = instance
    seed(db)
    ids = lambda **kw: [i["id"] for i in memory.retrieve(db, "evidence", **kw)["items"]]  # noqa: E731
    assert ids(since=T0 + 1, until=T0 + 3) == ["e3", "e2", "e1"]
    assert ids(channel="word") == ["e4", "e3"]
    assert ids(root="r1") == ["e3", "e1"]
    assert ids(epoch="none") == []
    assert len(ids(limit=2)) == 2
    assert len(ids(limit=0)) == 1 and len(ids(limit=-5)) == 1
    for i in range(1005 - 5):
        add(db, f"bulk{i}")
    assert len(ids(limit=10**6)) == 1000


def test_validation(instance):
    db, _ = instance
    for kw in ({"kind": "nonsense"}, {"kind": "graph", "channel": "word"}, {"kind": "evidence", "channel": "x"},
               {"kind": "evidence", "since": 5, "until": 1}, {"kind": "evidence", "since": "x"},
               {"kind": "checkpoint", "id": "abc"}, {"kind": "evidence", "limit": "5"}, {"kind": "dataset", "epoch": "e"}):
        kind = kw.pop("kind")
        with pytest.raises(ValidationError):
            memory.retrieve(db, kind, **kw)
