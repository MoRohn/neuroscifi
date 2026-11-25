"""review1 S3: forgetting a research task takes its question and its id out of every voice session's context (the
last research asked, the one whose answer a session awaits), in the forget's own transaction; a restored checkpoint
replays it. Before, the question text stayed in the context and was carried into the next session."""
from contextlib import closing
import time

from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.voice import transcripts as tx
from neuroscifi.webcortex import orchestrator


def contexts(db):
    return {sid: ctx for sid, ctx in ((s["id"], s["context"]) for s in tx.sessions(db, None, limit=50))}


def test_forgetting_research_scrubs_every_voice_context(instance):
    now = time.time()
    with closing(store.connect(instance)) as db:
        doomed = orchestrator.submit(db, "Owner secret question about a medical test", now, origin="voice")
        kept = orchestrator.submit(db, "Where does the okapi live?", now, origin="voice")
        a = tx.start_session(db, "owner", "console:x1", "voice_text", now)
        tx.update_session(db, a, now, context={"last_task_id": doomed["id"], "last_objective": doomed["objective"],
                                               "follow_task_id": doomed["id"], "follow_by": "1:2:3", "mode": "x"})
        b = tx.start_session(db, "pocket:abcd", "pocket:abcd:x", "voice_text", now)
        tx.update_session(db, b, now, context={"last_task_id": kept["id"], "last_objective": kept["objective"],
                                               "follow_task_id": doomed["id"]})
        c = tx.start_session(db, "owner", "console:x2", "voice_text", now)          # quotes it under another id
        tx.update_session(db, c, now, context={"last_task_id": "other", "last_objective": doomed["objective"]})
        governance.forget_research(db, doomed["id"], now + 1)
        after = contexts(db)
        assert after[a] == {"mode": "x"}
        assert after[b] == {"last_task_id": kept["id"], "last_objective": kept["objective"]}
        assert after[c] == {}
        text = " ".join(map(str, after.values()))
        assert doomed["id"] not in text and "medical test" not in text
        assert "medical test" not in str(tx.last_context(db, "owner"))


def test_a_restored_checkpoint_replays_the_scrub(instance, tmp_path):
    now = time.time()
    with closing(store.connect(instance)) as db:
        doomed = orchestrator.submit(db, "Owner secret question about a medical test", now, origin="voice")
        sid = tx.start_session(db, "owner", "console:x1", "voice_text", now)
        tx.update_session(db, sid, now, context={"last_task_id": doomed["id"], "last_objective": doomed["objective"]})
        archive = checkpoints.create(db, instance, tmp_path / "cp", now + 1, policy="fast")
        governance.forget_research(db, doomed["id"], now + 2)
        checkpoints.restore(db, archive["id"], tmp_path / "restored.sqlite", now + 3)
    with closing(store.connect(tmp_path / "restored.sqlite")) as copy:
        assert copy.execute("SELECT count(*) FROM web_tasks WHERE id=?", (doomed["id"],)).fetchone()[0] == 0
        assert copy.execute("SELECT count(*) FROM voice_sessions WHERE id=?", (sid,)).fetchone()[0] == 1
        assert "medical test" not in str(contexts(copy)) and doomed["id"] not in str(contexts(copy))
