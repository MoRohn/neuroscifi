"""The integration lane's additions on top of the merge of `main` (Cognitive Genesis Wave 4): the store's record of
each made artifact's independent check verdict survives a restore that replays Wave 4's own-text forget and
OMNIMIND's root forget together, and what was made from a forgotten file goes with it."""
import json
from contextlib import closing

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.artifacts import make
from neuroscifi.artifacts import store as A
from neuroscifi.instance import checkpoints, evidence, governance, store
from neuroscifi.webcortex import emitted

T0, DAY, N = adv.T0, adv.DAY, adv._SN
PARTNER = "Ottokar-vb8"
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."


def _made(kind, source_id):
    if kind == "csv":
        spec = {"spec": "data.csv", "version": 1, "title": "Ledger", "description": "", "options": {},
                "sources": [source_id], "made_by": {"generator": "", "version": 0},
                "data": {"columns": [{"name": "item", "kind": "text", "values": ["=SUM(A1)", "pear"]},
                                     {"name": "n", "kind": "number", "values": [1, 2]}]}}
    else:
        spec = {"spec": "sound.sonify", "version": 1, "title": "As pitch", "description": "", "options":
                {"reference": "none"}, "sources": [source_id], "made_by": {"generator": "", "version": 0},
                "data": {"values": [1.0, 3.0, 2.0]}}
    made = make.make(spec)
    return made, made.spec


def test_check_verdicts_survive_a_restore_that_replays_own_text_and_a_root_forget(tmp_path):
    with adv._s_individual("int-replay", attribution_independence=False, echo_recognition=False,
                           own_text_provenance=True) as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [("relnotes.example", f"{N} 1.5.0 is the latest {N} release.",
                                        T0 - 6 * DAY, None)], answer=ANSWER)
        assert emitted.stored(db) > 0
        evidence.start_epoch(db, "int", T0 - 5 * DAY)
        evidence.observe(db, {"id": "int-w0", "epoch": "int", "channel": "word", "action": 0, "bit": 1,
                              "root": PARTNER}, T0 - 5 * DAY + 1)
        table = A.add(db, b"item,n\npear,2\n", name="ledger.csv", now=T0 - 5 * DAY)
        ledger_made, ledger_spec = _made("csv", table.id)
        ledger = A.add_made(db, ledger_made, ledger_spec, [table.id], now=T0 - 5 * DAY + 1)
        note = A.add(db, f"a note about {PARTNER}\n".encode(), name="note.txt", now=T0 - 5 * DAY)
        sound_made, sound_spec = _made("sound", note.id)
        sound = A.add_made(db, sound_made, sound_spec, [note.id], now=T0 - 5 * DAY + 2)
        before = A.show(db, ledger.id)["index"]["check"]
        assert before["ok"] and before["warnings"] and A.show(db, sound.id)["index"]["check"]["ok"]
        older = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 4.5 * DAY)

        with store.write(db):
            live_own = emitted.remove_all(db, T0 - 4.4 * DAY)["own_text_removed"]
        removed = governance.forget_partner(db, PARTNER, T0 - 4.1 * DAY)["effects"]["artifacts_removed"]
        assert live_own > 0 and removed == 2                              # the note and the sound made from it

        dest = tmp_path / "restored.sqlite"
        result = checkpoints.restore(db, older["id"], dest, T0 - 4 * DAY)
        assert result["own_text_removed"] == live_own and result["artifacts_removed"] == 2
        with closing(store.connect(dest)) as copy:
            held = {i: json.loads(x) for i, x in copy.execute("SELECT id, index_json FROM artifacts")}
            assert set(held) == {table.id, ledger.id}
            assert held[ledger.id]["check"] == before                     # the verdict travels with the artifact
            assert emitted.stored(copy) == 0
            text = "\n".join(str(v) for (t,) in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")
                             for row in copy.execute(f"SELECT * FROM {t}") for v in row if not isinstance(v, bytes))
            assert PARTNER not in text and note.id not in text and sound.id not in text
