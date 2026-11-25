"""Forgetting one kind of a name the other channel still holds (ADR-047, schema 15).

Before: a camera forgotten while a partner of the same name was held (or the reverse) left the name in the text of
belief revisions, questions and decisions, because scrubbing worked per root name and the name was still held
(ENGINEERING_PLAN.md §6 item 6). Now the forgotten kind goes: `camera:<root>` (or `partner:<root>` and the
executive's "partner <root>"), its key in the summaries' sensors (or partners) map, and every mention in a question
or decision about that very scope. A bare name anywhere else may be the other one, and stays until the last
evidence of the name goes. A restore does the same in its copy.
"""
from contextlib import closing
import json

from neuroscifi.instance import checkpoints, evidence, executive, governance, store

from conftest import T0

NAME = "zelda"


def obs(db, event_id, channel, root, corrected, now, *, epoch="desk", action=1):
    return evidence.observe(db, {"id": event_id, "epoch": epoch, "channel": channel, "action": action,
                                 "bit": corrected ^ action, "root": root, "consent": "learn"}, now)["seq"]


def text(db):
    """Every scrubbed column of every row, as one string (JSON as stored)."""
    out = []
    for table, columns, _ in governance._SCRUB_TABLES:
        for row in db.execute(f"SELECT {','.join(columns)} FROM {table}"):
            out.extend(str(v) for v in row if v is not None)
    out.extend(scope for (scope,) in db.execute("SELECT scope FROM changepoints"))
    out.extend(segments for (segments,) in db.execute("SELECT segments FROM epoch_links"))
    return "\n".join(out)


def map_keys(db, key):
    keys = set()
    for (summary,) in db.execute("SELECT summary FROM belief_revisions"):
        keys |= set((json.loads(summary).get(key) or {}))
    return keys


def build(db):
    """A camera and a partner both named NAME; a suspected camera change (decisions name camera:NAME); an
    ambiguous partner NAME, so the executive asks "What does NAME mean by their words?"."""
    evidence.start_epoch(db, "desk", T0)
    n = 0
    for k in range(6):
        for channel, root, corrected in (("reference", f"ref-{k % 3}", 0), ("camera", NAME, 1), ("word", "ana", 0)):
            n += 1
            obs(db, f"e{n}", channel, root, corrected, T0 + n)
    for k in range(8):                       # NAME as a partner: half its words one way, half the other
        n += 1
        obs(db, f"e{n}", "word", NAME, k % 2, T0 + n)
    asked = executive.decide(db, "desk", T0 + 100, record=True)
    for k in range(8):                       # then the camera's readings flip: a change is suspected
        n += 1
        obs(db, f"e{n}", "camera", NAME, 0, T0 + 200 + n)
    changed = executive.decide(db, "desk", T0 + 400, record=True)
    return asked, changed


def test_a_camera_forgotten_while_a_partner_of_its_name_is_held_leaves_no_camera_name(instance, tmp_path):
    db, path = instance
    asked, changed = build(db)
    assert asked["scope"] == f"partner:{NAME}" and f"camera:{NAME}" in changed["reason"]
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 450)
    assert f"camera:{NAME}" in text(db) and NAME in map_keys(db, "sensors")
    assert db.execute("SELECT count(*) FROM decisions WHERE reason LIKE ?", (f"%camera:{NAME}%",)).fetchone()[0]
    out = governance.forget_camera(db, NAME, T0 + 500)
    effects = out["effects"]
    assert effects["roots_forgotten"] == 0 and effects["scopes_forgotten"] == 1
    digest = governance.root_digest(store.birth(db)["instance_id"], NAME)
    assert db.execute("SELECT digest, kind FROM forgotten_scopes").fetchall() == [(digest, "camera")]
    assert db.execute("SELECT count(*) FROM forgotten_roots").fetchone()[0] == 0
    after = text(db)
    assert f"camera:{NAME}" not in after and f"camera:#{digest}" in after
    assert NAME not in map_keys(db, "sensors")
    # the partner of that name is untouched: its scope, its map key, its question in its own words
    assert f"partner:{NAME}" in after and NAME in map_keys(db, "partners")
    assert f"What does {NAME} mean by their words?" in after
    # a restore of an archive older than the forget: the same, in the copy
    checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 600)
    with closing(store.open_existing(tmp_path / "r.sqlite")) as copy:
        restored = text(copy)
        assert f"camera:{NAME}" not in restored and NAME not in map_keys(copy, "sensors")
        assert f"partner:{NAME}" in restored and NAME in map_keys(copy, "partners")
        assert copy.execute("SELECT digest, kind FROM forgotten_scopes").fetchall() == [(digest, "camera")]
    # the last evidence of the name goes: now the bare name goes too, everywhere
    gone = governance.forget_partner(db, NAME, T0 + 700)
    assert gone["effects"]["roots_forgotten"] == 1
    assert NAME not in text(db)
    assert NAME.encode() not in path.read_bytes()


def test_a_partner_forgotten_while_a_camera_of_its_name_is_held_leaves_no_partner_name(instance, tmp_path):
    db, path = instance
    build(db)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 450)
    out = governance.forget_partner(db, NAME, T0 + 500)
    assert out["effects"]["roots_forgotten"] == 0 and out["effects"]["scopes_forgotten"] == 1
    digest = governance.root_digest(store.birth(db)["instance_id"], NAME)
    for db_ in (db,):
        after = text(db_)
        assert f"partner:{NAME}" not in after and f"partner {NAME}" not in after
        assert f"What does {NAME} mean" not in after and f"What does partner:#{digest} mean" in after
        assert NAME not in map_keys(db_, "partners")
        assert f"camera:{NAME}" in after and NAME in map_keys(db_, "sensors")   # the camera of that name stays
    checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 600)
    with closing(store.open_existing(tmp_path / "r.sqlite")) as copy:
        restored = text(copy)
        assert f"partner:{NAME}" not in restored and f"What does {NAME} mean" not in restored
        assert NAME not in map_keys(copy, "partners") and NAME in map_keys(copy, "sensors")


def test_one_kind_forgotten_by_its_last_event_or_by_a_sweep_is_scrubbed_too(instance):
    """Not only forget_camera: the last camera event of the name forgotten by its id leaves the same record."""
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    obs(db, "cam", "camera", NAME, 1, T0 + 1)
    obs(db, "word", "word", NAME, 1, T0 + 2)
    obs(db, "ref", "reference", "ref-0", 0, T0 + 3)
    assert NAME in map_keys(db, "sensors")
    out = governance.forget_evidence(db, "cam", T0 + 10)
    assert out["effects"]["scopes_forgotten"] == 1
    assert NAME not in map_keys(db, "sensors") and NAME in map_keys(db, "partners")


def test_a_later_camera_of_the_same_name_is_not_scrubbed_by_a_restore(instance, tmp_path):
    """The record is of a kind that had no evidence left: a copy that holds a newer camera of the name keeps it."""
    db, path = instance
    build(db)
    governance.forget_camera(db, NAME, T0 + 500)
    obs(db, "new-cam", "camera", NAME, 1, T0 + 600)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 650)
    checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 700)
    with closing(store.open_existing(tmp_path / "r.sqlite")) as copy:
        assert NAME in map_keys(copy, "sensors")       # the summary written after the new camera's reading


def test_the_scoped_scrubber_leaves_the_other_kind_and_bare_names(instance):
    roots = {NAME: ("camera", "0" * 16)}
    sub = governance._scrubber(roots, scoped=True)
    assert sub(f"camera:{NAME} or partner:{NAME}, {NAME}") == f"camera:#{'0' * 16} or partner:{NAME}, {NAME}"
    roots = {NAME: ("partner", "1" * 16)}
    sub = governance._scrubber(roots, scoped=True)
    assert sub(f"partner {NAME} (question #3); camera:{NAME}; partner:{NAME}x") == \
        f"partner partner:#{'1' * 16} (question #3); camera:{NAME}; partner:{NAME}x"
