"""Forgetting a voice session through the hub (console, Pocket, the spoken command) rewrites the checkpoint archives
without it in the background, so the request and the voice turn do not wait (instance/checkpoints.rewrite)."""
import io
import time

from neuroscifi import archive
from neuroscifi.instance import checkpoints
from neuroscifi.voice import transcripts as tx

from .conftest import OWNER, wait_for

SECRET = "the alarm code is 9381-kx"


def test_a_hub_forget_rewrites_the_checkpoints_in_the_background(hub, tmp_path):
    with hub.db() as db:
        sid = tx.start_session(db, "owner", "console:x", "voice_text", time.time())
        tx.upsert(db, sid, "u1", speaker="user", source="asr", status="final", text=SECRET, now=time.time())
        cp = checkpoints.create(db, hub.db_path, tmp_path / "cps", time.time())
    out = hub.forget(OWNER, sid)
    assert out["checkpoints_rewriting"] == [cp["id"]] and "next_steps" not in out

    def rewritten():
        with hub.db() as db:
            return checkpoints._row(db, cp["id"])["status"] == "valid"
    wait_for(rewritten, timeout=30)
    data = io.BytesIO()
    with open(cp["path"], "rb") as source:
        archive.unpack(source, data)
    assert SECRET.encode() not in data.getvalue() and sid.encode() not in data.getvalue()
