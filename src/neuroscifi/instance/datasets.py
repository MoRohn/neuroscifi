"""Population trajectories used to learn and score executable graphs.

A dataset is one (states[T,N], targets[T,N]) pair: row t of `states` is the population vector the
graph reads, row t of `targets` the response it must predict (target ~= states @ A.T). Arrays are
stored as `np.savez` bytes, read back with allow_pickle=False, and validated on write and on read.
"""
import hashlib
import io
import zipfile

import numpy as np

from .. import inputfiles
from ..lab import graph_bench
from . import store
from .errors import Conflict, Gone, NotFound, ValidationError

SPLITS = ("train", "validation", "heldout")
PROVENANCES = ("observed", "simulated")
MIN_ROWS, MAX_ROWS = 8, 200_000
MIN_COLS, MAX_COLS = 1, 1024
VALIDATION_STEPS, HELDOUT_STEPS = 256, 512
WORLDS = ("a", "b")
# SQLite refuses a blob longer than SQLITE_MAX_LENGTH (1e9 bytes in a default build) with a raw DataError. The npz
# holds two float64 arrays (16 bytes per cell) plus its zip and npy headers (well under 4 kB).
MAX_BLOB_BYTES, BLOB_FRAMING_BYTES = 1_000_000_000, 4096
# The most elements one array of an accepted pair can have (as float64, both arrays fit in MAX_BLOB_BYTES): an .npz
# whose headers declare more is refused before numpy allocates it (check_npz).
MAX_ELEMENTS = min(MAX_ROWS * MAX_COLS, (MAX_BLOB_BYTES - BLOB_FRAMING_BYTES) // 16)


def _check_context(context):
    if not isinstance(context, str) or not 1 <= len(context) <= 64 or not context.strip():
        raise ValidationError("Context must be a nonempty name of at most 64 characters")
    return context


def _check_arrays(states, targets):
    try:
        states = np.asarray(states, dtype=np.float64)
        targets = np.asarray(targets, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"Dataset arrays must be numeric: {error}") from None
    if states.ndim != 2 or states.shape != targets.shape:
        raise ValidationError("states and targets must be 2-D arrays of the same shape [T, N]")
    rows, cols = states.shape
    if not MIN_ROWS <= rows <= MAX_ROWS:
        raise ValidationError(f"A dataset needs {MIN_ROWS}..{MAX_ROWS} time steps, got {rows}")
    if not MIN_COLS <= cols <= MAX_COLS:
        raise ValidationError(f"A dataset needs {MIN_COLS}..{MAX_COLS} populations, got {cols}")
    if 16 * rows * cols + BLOB_FRAMING_BYTES > MAX_BLOB_BYTES:
        raise ValidationError(f"A dataset of {rows} time steps x {cols} populations is too large to store: at most "
                              f"{(MAX_BLOB_BYTES - BLOB_FRAMING_BYTES) // (16 * cols)} time steps fit with {cols} "
                              "populations")
    if not (np.isfinite(states).all() and np.isfinite(targets).all()):
        raise ValidationError("Dataset arrays must be finite")
    return states, targets


def check_npz(fp, size=None):
    """Refuse (inputfiles.InputRefused, a ValueError) an .npz whose zip directory or array headers declare more than
    a dataset can hold, before np.load allocates or decompresses anything: at most MAX_ELEMENTS elements per array of
    at most 16 bytes each (inputfiles.check_npz). fp is back at its start."""
    return inputfiles.check_npz(fp, inputfiles.size_of(fp) if size is None else size, max_elements=MAX_ELEMENTS,
                                max_member_bytes=16 * MAX_ELEMENTS + BLOB_FRAMING_BYTES)


def _encode(states, targets):
    buffer = io.BytesIO()
    np.savez(buffer, states=states, targets=targets)
    return buffer.getvalue()


def _insert(db, context, split, states, targets, provenance, now, note):
    """Validate and insert one dataset. Caller owns the transaction."""
    _check_context(context)
    if split not in SPLITS:
        raise ValidationError(f"split must be one of {', '.join(SPLITS)}")
    if provenance not in PROVENANCES:
        raise ValidationError(f"provenance must be one of {', '.join(PROVENANCES)}")
    states, targets = _check_arrays(states, targets)
    blob = _encode(states, targets)
    cur = db.execute(
        "INSERT INTO datasets(context,split,provenance,rows,cols,sha256,blob,note,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (context, split, provenance, states.shape[0], states.shape[1], hashlib.sha256(blob).hexdigest(),
         blob, note, now))
    return cur.lastrowid


def add(db, context, split, states, targets, provenance, now, note=None, *, path=None):
    """Store one trajectory pair and return its id."""
    store.require_write_allowed(db, path)
    with store.write(db):
        dataset_id = _insert(db, context, split, states, targets, provenance, now, note)
        store.bump_state_version(db)
    return dataset_id


def load(db, dataset_id):
    """Return (states, targets). NotFound for an unknown id, Gone once deleted."""
    row = db.execute("SELECT rows, cols, sha256, blob, deleted_at FROM datasets WHERE id=?",
                     (dataset_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown dataset {dataset_id}")
    rows, cols, digest, blob, deleted_at = row
    if deleted_at is not None or blob is None:
        raise Gone(f"Dataset {dataset_id} was deleted")
    return decode(blob, digest, rows, cols, f"Dataset {dataset_id}")


def decode(blob, digest, rows, cols, label):
    """(states, targets) of a stored npz blob, checked against its sha256 and recorded shape (allow_pickle=False)."""
    if hashlib.sha256(blob).hexdigest() != digest:
        raise Conflict(f"{label} failed its integrity check")
    try:
        source = io.BytesIO(blob)
        check_npz(source, len(blob))
        with np.load(source, allow_pickle=False) as archive:
            if set(archive.files) != {"states", "targets"}:
                raise ValidationError(f"{label} has unexpected arrays {sorted(archive.files)}")
            states, targets = archive["states"], archive["targets"]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise ValidationError(f"{label} is not a readable npz archive: {error}") from None
    if states.dtype != np.float64 or targets.dtype != np.float64:
        raise ValidationError(f"{label} must hold float64 arrays")
    states, targets = _check_arrays(states, targets)
    if states.shape != (rows, cols):
        raise ValidationError(f"{label} shape {states.shape} differs from its record")
    return states, targets


def simulate(db, context, now, *, seed, world="a", steps=graph_bench.STEPS, path=None):
    """Simulate train/validation/heldout trajectories of one lab.graph_bench world.

    Seed derivation (documented so runs are reproducible):
    - the world pair (a, b) is `graph_bench.world(np.random.default_rng(seed))`, so 'a' and 'b' of the
      same seed are the original and the rewired version of one world (one edge per receiver moved);
    - each split has its own stream `np.random.default_rng([seed, world_index, split_index])` with
      world_index 0 for 'a', 1 for 'b' and split_index 0 train, 1 validation, 2 heldout.
    Train has `steps` rows, validation 256 and heldout 512. All three are written in one transaction.
    """
    if world not in WORLDS:
        raise ValidationError("world must be 'a' or 'b'")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValidationError("seed must be a non-negative integer")
    if isinstance(steps, bool) or not isinstance(steps, int) or not MIN_ROWS <= steps <= MAX_ROWS:
        raise ValidationError(f"steps must be an integer in {MIN_ROWS}..{MAX_ROWS}")
    _check_context(context)
    matrix = graph_bench.world(np.random.default_rng(seed))[WORLDS.index(world)]
    lengths = {"train": steps, "validation": VALIDATION_STEPS, "heldout": HELDOUT_STEPS}
    store.require_write_allowed(db, path)
    out = {}
    with store.write(db):
        for index, split in enumerate(SPLITS):
            rng = np.random.default_rng([seed, WORLDS.index(world), index])
            states, targets = graph_bench.stream(matrix, lengths[split], rng)
            note = f"graph_bench world {world} seed {seed} split {split}"
            out[split] = _insert(db, context, split, states, targets, "simulated", now, note)
        store.bump_state_version(db)
    return out


def listing(db, context=None):
    sql = ("SELECT id, context, split, provenance, rows, cols, sha256, note, created_at, deleted_at, "
           "length(blob) FROM datasets")
    params = ()
    if context is not None:
        sql += " WHERE context=?"
        params = (context,)
    keys = ("id", "context", "split", "provenance", "rows", "cols", "sha256", "note", "created_at",
            "deleted_at", "bytes")
    return [dict(zip(keys, row)) for row in db.execute(sql + " ORDER BY id", params)]
