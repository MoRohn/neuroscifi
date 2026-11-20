"""Decision D-1 (docs/quality/PREREGISTRATION_D1.md): epoch graph contexts, observation streams and the dynamics term of
beliefs.match_epochs, its empirical prior, governance of streams, and the `nsf epoch context|stream` commands."""
import hashlib
import json
import math
import random

import numpy as np
import pytest

from neuroscifi import cli
from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import beliefs, checkpoints, evidence, executive, governance, store
from neuroscifi.instance.errors import NotFound, ValidationError, WritesPaused
from conftest import T0

LOG_2PI = math.log(2 * math.pi)


def observe(db, epoch, n, channel, root, relation, action=0):
    evidence.observe(db, {"id": f"{epoch}-{n}", "epoch": epoch, "channel": channel, "action": action,
                          "bit": relation ^ action, "root": root}, T0 + n)


def tiny_graph(db, context, matrix, validation_mse, *, status="active"):
    """Insert a revision of `context` whose snapshot is `matrix` (NSG1, 8 bits) and whose validation MSE is given."""
    blob = encode(np.asarray(matrix, dtype=np.float64), len(matrix) - 1, 8)
    metrics = {} if validation_mse is None else {"validation_mse": validation_mse}
    with store.write(db):
        revision = db.execute(
            "INSERT INTO graph_revisions(context,status,bits,degree,rows,cols,byte_size,sha256,blob,rule_version,"
            "origin,metrics,created_at) VALUES(?,?,8,?,?,?,?,?,?,'gate-v1','import',?,?)",
            (context, status, len(matrix) - 1, len(matrix), len(matrix), len(blob), hashlib.sha256(blob).hexdigest(),
             blob, json.dumps(metrics), T0)).lastrowid
    return revision, decode(blob)


def normal_log_pdf(x, variance):
    """log of the normal density, written out (no shared code with beliefs)."""
    return math.log(math.exp(-x * x / (2 * variance)) / math.sqrt(2 * math.pi * variance))


# ---- the dynamics log-likelihood and the combined posterior, closed form -------------------------------------

def test_dynamics_log_likelihood_and_combined_posterior_on_a_tiny_case(instance):
    db, _ = instance
    for epoch in ("desk", "other"):
        evidence.start_epoch(db, epoch, T0)
    for n in range(10):
        observe(db, "desk", n, "camera", "cam-a", 0)          # desk: cam-a relation 0
        observe(db, "other", 100 + n, "camera", "cam-a", 1)   # other: cam-a relation 1, no context
    assert evidence.set_epoch_context(db, "desk", "panel", T0) == {"epoch": "desk", "context": "panel",
                                                                    "changed": True}
    revision, graph = tiny_graph(db, "panel", [[0, .5], [-.25, 0]], .04)
    evidence.start_epoch(db, "tomorrow", T0 + 500)
    observe(db, "tomorrow", 500, "camera", "cam-a", 0)
    rng = np.random.default_rng(7)
    states = rng.normal(0, .5, size=(8, 2))
    targets = states @ graph.T + rng.normal(0, .2, size=(8, 2))
    evidence.attach_stream(db, "tomorrow", states, targets, T0 + 501, provenance="simulated")
    out = beliefs.match_epochs(db, "tomorrow")

    # Closed form, element by element.
    residuals = targets - states @ graph.T
    desk_dynamics = sum(normal_log_pdf(r, .04) for r in residuals.ravel())
    variance = sum(y * y for y in targets.ravel()) / targets.size
    new_dynamics = sum(normal_log_pdf(y, variance) for y in targets.ravel())
    assert new_dynamics == pytest.approx(-targets.size / 2 * (LOG_2PI + math.log(variance) + 1), abs=1e-9)
    by_epoch = {r["epoch"]: r for r in out["ranking"]}
    desk, other = by_epoch["desk"], by_epoch["other"]
    assert desk["dynamics_log_likelihood"] == pytest.approx(desk_dynamics, abs=1e-9)
    assert desk["dynamics"] == {"source": "graph", "context": "panel", "revision_id": revision, "variance": .04,
                                "why": None}
    # A candidate without a context is scored with the new setup's dynamics term (no free pass either way).
    assert other["dynamics_log_likelihood"] == pytest.approx(new_dynamics, abs=1e-9)
    assert other["dynamics"]["source"] == "new_setup" and other["dynamics"]["context"] is None
    assert out["new_setup_dynamics_log_likelihood"] == pytest.approx(new_dynamics, abs=1e-9)
    # Bits: one camera reading. desk predicts relation 0 (.9), other relation 1 (.1), a new setup a fresh coin (.5).
    assert desk["bits_log_likelihood"] == pytest.approx(math.log(.9), abs=1e-12)
    assert other["bits_log_likelihood"] == pytest.approx(math.log(.1), abs=1e-12)
    assert out["new_setup_bits_log_likelihood"] == pytest.approx(math.log(.5), abs=1e-12)
    for r in (desk, other):
        assert r["log_likelihood"] == r["bits_log_likelihood"] + r["dynamics_log_likelihood"]
    # Combined posterior with prior 1/2 on a new setup and 1/4 on each candidate.
    weights = {"new": .5 * .5 * math.exp(new_dynamics), "desk": .25 * .9 * math.exp(desk_dynamics),
               "other": .25 * .1 * math.exp(new_dynamics)}
    z = sum(weights.values())
    assert out["new_setup_posterior"] == pytest.approx(weights["new"] / z, rel=1e-9)
    assert desk["posterior"] == pytest.approx(weights["desk"] / z, rel=1e-9)
    assert other["posterior"] == pytest.approx(weights["other"] / z, rel=1e-9)
    assert out["best"] == "desk" and [r["epoch"] for r in out["ranking"]] == ["desk", "other"]
    assert out["stream"]["rows"] == 8 and out["stream"]["cols"] == 2 and out["stream"]["provenance"] == "simulated"


def test_wrong_panel_dynamics_rule_a_candidate_out(instance):
    """A graph that predicts the stream badly loses to the zero predictor; a missing validation MSE, an inactive graph
    or another population count gives no dynamics term of its own (the new setup's term is used)."""
    db, _ = instance
    evidence.start_epoch(db, "desk", T0, context="panel")
    for n in range(10):
        observe(db, "desk", n, "camera", "cam-a", 0)
        observe(db, "desk", 50 + n, "word", "ana", 0)
    _, graph = tiny_graph(db, "panel", [[0, .9], [.9, 0]], .01)
    evidence.start_epoch(db, "tomorrow", T0 + 500)
    for n in range(4):
        observe(db, "tomorrow", 500 + 2 * n, "camera", "cam-a", 0)
        observe(db, "tomorrow", 501 + 2 * n, "word", "ana", 0)
    rng = np.random.default_rng(3)
    states = rng.normal(0, 1, size=(12, 2))
    evidence.attach_stream(db, "tomorrow", states, states @ -graph.T, T0 + 520)   # a different panel
    out = beliefs.match_epochs(db, "tomorrow")
    assert out["ranking"][0]["dynamics"]["source"] == "graph" and out["best"] is None
    assert out["new_setup_posterior"] > .999
    for why, fix in (("no validation MSE", lambda: db.execute("UPDATE graph_revisions SET metrics='{}'")),
                     ("no active graph", lambda: db.execute("UPDATE graph_revisions SET status='archived'"))):
        with store.write(db):
            fix()
        entry = beliefs.match_epochs(db, "tomorrow")["ranking"][0]
        assert entry["dynamics"]["source"] == "new_setup" and why in entry["dynamics"]["why"]
        assert entry["dynamics_log_likelihood"] == out["new_setup_dynamics_log_likelihood"]
    tiny_graph(db, "panel", [[0, .5, 0], [0, 0, .5], [.5, 0, 0]], .01)
    entry = beliefs.match_epochs(db, "tomorrow")["ranking"][0]
    assert entry["dynamics"]["source"] == "new_setup" and "3 populations" in entry["dynamics"]["why"]


# ---- no stream: identical to the bits-only matcher -----------------------------------------------------------

def bits_only_match(db, epoch, last=beliefs.MATCH_EVENTS):
    """The matcher before decision D-1 (commit 88f56fd), restated: bits only, prior 1/2 on a new setup."""
    events = beliefs._window(db, epoch, last)
    tallies = beliefs._window_tallies(events)
    new_setup = beliefs.new_setup_log_likelihood(tallies)
    ranking = []
    for (other,) in db.execute("SELECT name FROM epochs WHERE name!=? ORDER BY created_at, name", (epoch,)).fetchall():
        candidate = beliefs._candidate(db, other, tallies)
        if candidate is not None:
            ranking.append(candidate)
    prior = math.log(.5 / len(ranking)) if ranking else None
    total = math.log(.5) + new_setup
    for r in ranking:
        total = beliefs._lse(total, prior + r["log_likelihood"])
    for r in ranking:
        r["posterior"] = math.exp(prior + r["log_likelihood"] - total)
    ranking.sort(key=lambda r: (-r["log_likelihood"], r["epoch"]))
    best = ranking[0]["epoch"] if ranking and ranking[0]["posterior"] >= beliefs.MATCH_POSTERIOR else None
    return {"epoch": epoch, "events": len(events), "ranking": ranking, "best": best,
            "best_posterior": ranking[0]["posterior"] if best is not None else None,
            "new_setup_posterior": math.exp(math.log(.5) + new_setup - total), "new_setup_log_likelihood": new_setup}


def random_epochs(db, rng, count):
    """Epochs with random camera/word/reference readings over a few roots; some get a confirmed change."""
    n = 0
    for e in range(count):
        name = f"e{e}"
        evidence.start_epoch(db, name, T0 + e)
        truth = {"w": rng.randrange(2), "cam-a": rng.randrange(2), "cam-b": rng.randrange(2), "ana": rng.randrange(2),
                 "bo": rng.randrange(2)}
        for _ in range(rng.randrange(0, 30)):
            n += 1
            channel = rng.choice(("camera", "word", "reference"))
            root = rng.choice({"camera": ("cam-a", "cam-b"), "word": ("ana", "bo"),
                               "reference": ("ref-0", "ref-1")}[channel])
            relation = truth["w"] ^ (truth[root] if channel != "reference" else 0) ^ (rng.random() < .1)
            observe(db, name, n, channel, root, int(relation), rng.randrange(2))
        if rng.random() < .3 and n:
            decided = executive.decide(db, name, T0 + 10_000 + n, record=True)
            if decided["decision"] == "ask" and decided["scope"] and decided["scope"].startswith("camera:"):
                executive.resolve(db, decided["pending_id"], "yes", T0 + 10_001 + n, confirm=True)


def test_without_a_stream_the_match_is_the_bits_only_match(tmp_path):
    rng = random.Random(20261120)
    for trial in range(12):
        db = store.create(tmp_path / f"r{trial}.sqlite", T0)
        try:
            random_epochs(db, rng, rng.randrange(2, 6))
            for (epoch,) in db.execute("SELECT name FROM epochs").fetchall():
                last = rng.randrange(1, 20)
                out = beliefs.match_epochs(db, epoch, last=last)
                old = bits_only_match(db, epoch, last)
                assert out["stream"] is None and out["new_setup_dynamics_log_likelihood"] is None
                assert out["prior"] == {"new_setup": .5, "n_new": 0, "n_continue": 0}
                for key in old:
                    if key != "ranking":
                        assert out[key] == old[key], key
                assert len(out["ranking"]) == len(old["ranking"])
                for new, before in zip(out["ranking"], old["ranking"]):
                    assert new["dynamics"] is None and new["dynamics_log_likelihood"] is None
                    assert new["bits_log_likelihood"] == new["log_likelihood"]
                    assert {key: new[key] for key in before} == before
                # A stream with no candidate graph moves nothing: every candidate shares the new setup's term.
                states = np.random.default_rng(trial).normal(size=(9, 3))
                evidence.attach_stream(db, epoch, states, states[:, ::-1], T0 + 50_000)
                streamed = beliefs.match_epochs(db, epoch, last=last)
                assert streamed["best"] == old["best"]
                for new, before in zip(streamed["ranking"], old["ranking"]):
                    assert new["epoch"] == before["epoch"]
                    assert new["posterior"] == pytest.approx(before["posterior"], rel=1e-9, abs=1e-12)
                with store.write(db):
                    db.execute("DELETE FROM epoch_streams")
        finally:
            db.close()


# ---- the empirical prior -------------------------------------------------------------------------------------

def epoch_question(db, epoch, about, now):
    with store.write(db):
        return db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(?,'ask',?,?,?)",
                          (epoch, f"epoch:{about}", f"This looks like {about}; continue with that calibration?",
                           now)).lastrowid


def test_prior_counts_the_owners_epoch_answers(instance):
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    for n in range(12):
        observe(db, "desk", n, "camera", "cam-a", 0)
        observe(db, "desk", 100 + n, "word", "ana", 0)
        observe(db, "desk", 200 + n, "reference", f"ref-{n % 3}", 0)
    assert beliefs.answer_prior(db) == {"new_setup": .5, "n_new": 0, "n_continue": 0}
    answers = [("a1", False), ("a2", False), ("a3", None), ("a4", True)]
    for i, (epoch, confirm) in enumerate(answers):
        evidence.start_epoch(db, epoch, T0 + 1000 + i)
        observe(db, epoch, 1000 + 10 * i, "camera", "cam-a", 0)
        observe(db, epoch, 1001 + 10 * i, "word", "ana", 0)
        question = epoch_question(db, epoch, "desk", T0 + 1002 + 10 * i)
        executive.resolve(db, question, "answer", T0 + 1003 + 10 * i, confirm=confirm)
        executive.resolve(db, question, "answer", T0 + 1004 + 10 * i, confirm=confirm)   # idempotent: counted once
    # Two "this is new", one "continue there"; closing a question without an answer (confirm=None) is not counted,
    # nor is an answer to a change question.
    prior = beliefs.answer_prior(db)
    assert prior == {"new_setup": 3 / 5, "n_new": 2, "n_continue": 1}
    evidence.start_epoch(db, "tomorrow", T0 + 2000)
    for n in range(3):
        observe(db, "tomorrow", 2000 + 3 * n, "camera", "cam-a", 0)
        observe(db, "tomorrow", 2001 + 3 * n, "word", "ana", 0)
        observe(db, "tomorrow", 2002 + 3 * n, "reference", f"ref-{n}", 0)
    out = beliefs.match_epochs(db, "tomorrow")
    assert out["prior"] == prior
    old = bits_only_match(db, "tomorrow")   # the same likelihoods under prior 1/2
    k = len(out["ranking"])
    weights = {"new": .6 * math.exp(old["new_setup_log_likelihood"])}
    weights.update({r["epoch"]: .4 / k * math.exp(r["log_likelihood"]) for r in old["ranking"]})
    z = sum(weights.values())
    assert out["new_setup_posterior"] == pytest.approx(weights["new"] / z, rel=1e-12)
    for r in out["ranking"]:
        assert r["posterior"] == pytest.approx(weights[r["epoch"]] / z, rel=1e-12)


# ---- streams: validation, regulation, expiry and forgetting --------------------------------------------------

def test_attach_stream_validates_like_datasets(instance, tmp_path):
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    good = np.zeros((12, 4)), np.ones((12, 4))
    with pytest.raises(NotFound):
        evidence.attach_stream(db, "missing", *good, T0)
    for states, targets in ((np.zeros((12, 4)), np.zeros((12, 3))), (np.zeros(12), np.zeros(12)),
                            (np.zeros((7, 4)), np.zeros((7, 4))), (np.full((12, 4), np.nan), np.zeros((12, 4))),
                            ([["a"] * 4] * 12, np.zeros((12, 4)))):
        with pytest.raises(ValidationError):
            evidence.attach_stream(db, "e", states, targets, T0)
    with pytest.raises(ValidationError):
        evidence.attach_stream(db, "e", *good, T0, provenance="guessed")
    with pytest.raises(ValidationError):
        evidence.set_epoch_context(db, "e", " ", T0)
    with pytest.raises(NotFound):
        evidence.set_epoch_context(db, "missing", "panel", T0)
    out = evidence.attach_stream(db, "e", *good, T0)
    assert out["provenance"] == "observed" and out["expires_at"] == T0 + store.config(db).evidence_ttl_seconds
    assert out["rows"] == 12 and out["cols"] == 4
    stored = beliefs.latest_stream(db, "e")
    assert stored["id"] == out["id"] and np.array_equal(stored["targets"], good[1])
    with store.write(db):   # a tampered blob fails its integrity check
        db.execute("UPDATE epoch_streams SET sha256='0' WHERE id=?", (out["id"],))
    with pytest.raises(Exception, match="integrity"):
        beliefs.latest_stream(db, "e")
    paused = store.create(tmp_path / "paused.sqlite", T0, config=store.InstanceConfig(max_evidence_rows=1))
    try:
        evidence.start_epoch(paused, "e", T0)
        observe(paused, "e", 1, "camera", "cam-a", 0)
        with pytest.raises(WritesPaused):
            evidence.attach_stream(paused, "e", *good, T0)
        assert paused.execute("SELECT count(*) FROM epoch_streams").fetchone()[0] == 0
    finally:
        paused.close()


def test_start_epoch_with_context_is_idempotent_and_replaceable(instance):
    db, _ = instance
    assert evidence.start_epoch(db, "e", T0) == {"epoch": "e", "created": True}
    assert beliefs.epoch_context(db, "e") is None
    version = store.state_version(db)
    assert evidence.start_epoch(db, "e", T0 + 1, context="panel") == {"epoch": "e", "created": False,
                                                                      "context": "panel"}
    assert beliefs.epoch_context(db, "e") == "panel" and store.state_version(db) == version + 1
    assert evidence.start_epoch(db, "e", T0 + 2, context="panel")["created"] is False
    assert store.state_version(db) == version + 1
    assert evidence.set_epoch_context(db, "e", "panel-b", T0 + 3)["changed"] is True
    assert beliefs.epoch_context(db, "e") == "panel-b"


def test_forget_epoch_removes_its_streams_and_context_and_sweep_expires_streams(instance, tmp_path):
    db, path = instance
    evidence.start_epoch(db, "gone", T0, context="panel")
    evidence.start_epoch(db, "kept", T0, context="panel")
    observe(db, "gone", 1, "camera", "cam-secret", 0)
    rng = np.random.default_rng(1)
    for epoch in ("gone", "kept"):
        evidence.attach_stream(db, epoch, rng.normal(size=(8, 2)), rng.normal(size=(8, 2)), T0 + 2)
    checkpoint = checkpoints.create(db, path, tmp_path / "cps", T0 + 3)
    out = governance.forget_epoch(db, "gone", T0 + 4)
    assert out["effects"]["derived_deleted"]["epoch_streams"] == 1
    assert out["effects"]["derived_deleted"]["epoch_contexts"] == 1
    assert checkpoint["id"] in out["effects"]["checkpoints_stale"]
    assert db.execute("SELECT count(*) FROM epoch_streams WHERE epoch='gone'").fetchone()[0] == 0
    assert beliefs.epoch_context(db, "gone") is None and beliefs.latest_stream(db, "gone") is None
    assert beliefs.epoch_context(db, "kept") == "panel" and beliefs.latest_stream(db, "kept") is not None
    # A restore never brings the forgotten stream back.
    restored = checkpoints.restore(db, checkpoint["id"], tmp_path / "restored.sqlite", T0 + 5)
    assert restored["streams_removed"] == 1
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert [e for (e,) in copy.execute("SELECT epoch FROM epoch_streams")] == ["kept"]
    finally:
        copy.close()
    # Forgetting an epoch with only a stream (no evidence) still removes it.
    evidence.start_epoch(db, "bare", T0)
    evidence.attach_stream(db, "bare", rng.normal(size=(8, 2)), rng.normal(size=(8, 2)), T0 + 6)
    assert governance.forget_epoch(db, "bare", T0 + 7)["status"] == "forgotten"
    assert beliefs.latest_stream(db, "bare") is None
    ttl = store.config(db).evidence_ttl_seconds
    assert governance.sweep(db, T0 + 2 + ttl - 1)["streams_expired"] == 0
    swept = governance.sweep(db, T0 + 2 + ttl)
    assert swept["streams_expired"] == 1 and swept["wal_checkpoint"] == "ok"
    assert db.execute("SELECT count(*) FROM epoch_streams").fetchone()[0] == 0
    # Streams hold arrays only: no text column of any table names the forgotten camera root.
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in db.execute(f"SELECT * FROM {table}"):
            assert not any(isinstance(v, str) and "cam-secret" in v for v in row), table


# ---- CLI round trip ------------------------------------------------------------------------------------------

def nsf(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def test_cli_epoch_context_and_stream_round_trip(tmp_path, capsys):
    path = tmp_path / "cli.sqlite"
    store.create(path, T0).close()
    db = ["--db", str(path)]
    code, out = nsf(capsys, "epoch", "start", "desk", "--context", "panel", *db)
    assert code == 0 and out == {"epoch": "desk", "created": True, "context": "panel"}
    code, out = nsf(capsys, "epoch", "start", "tomorrow", *db)
    assert code == 0 and out == {"epoch": "tomorrow", "created": True}
    code, out = nsf(capsys, "epoch", "context", "tomorrow", "panel-b", *db)
    assert code == 0 and out == {"epoch": "tomorrow", "context": "panel-b", "changed": True}
    code, err = nsf(capsys, "epoch", "context", "nobody", "panel", *db)
    assert code == 2 and "Unknown epoch" in err
    rng = np.random.default_rng(5)
    states, targets = rng.normal(size=(12, 3)), rng.normal(size=(12, 3))
    np.savez(tmp_path / "stream.npz", states=states, targets=targets)
    code, out = nsf(capsys, "epoch", "stream", "tomorrow", str(tmp_path / "stream.npz"), "--simulated", *db)
    assert code == 0 and out["provenance"] == "simulated" and out["rows"] == 12 and out["cols"] == 3
    code, match = nsf(capsys, "epoch", "match", "tomorrow", *db)
    assert code == 0 and match["stream"]["id"] == out["id"] and match["stream"]["provenance"] == "simulated"
    assert match["new_setup_dynamics_log_likelihood"] == pytest.approx(
        -targets.size / 2 * (LOG_2PI + math.log(np.mean(targets ** 2)) + 1), abs=1e-9)
    np.savez(tmp_path / "wrong.npz", states=states)
    np.save(tmp_path / "plain.npy", states)
    (tmp_path / "junk.npz").write_bytes(b"not an archive")
    np.savez(tmp_path / "objects.npz", states=np.array([object()] * 8), targets=np.zeros(8))
    for name in ("wrong.npz", "plain.npy", "junk.npz", "objects.npz", "absent.npz"):
        code, err = nsf(capsys, "epoch", "stream", "tomorrow", str(tmp_path / name), *db)
        assert code == 2 and err.startswith("nsf: "), name
    check = store.open_existing(path)
    try:
        assert check.execute("SELECT count(*) FROM epoch_streams").fetchone()[0] == 1
        assert beliefs.epoch_context(check, "desk") == "panel"
    finally:
        check.close()
