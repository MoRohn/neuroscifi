"""The owner console's "Made and kept" view (OMNIMIND lane U, ADR-079): what the owner kept or NeuroSciFi made, shown
without loosening any security control of the console.

Routes (console/server.py's table; the console's own authentication, owner scope only, every reply with the console's
headers unchanged):
  GET  /api/artifacts?page=N                 a page of the kept artifacts, newest first, each in plain words
  GET  /api/artifacts/<id>                   one artifact: what it is, how to show it, facts, why, relations
  GET  /api/artifacts/<id>/content           its bytes (application/octet-stream, streamed in bounded chunks), for
                                             the page to parse an SVG picture or decode a sound itself
  POST /api/artifacts/<id>/image             a short-lived address for a raster picture (an <img> cannot send the
                                             bearer header): GET /artifact-image/<token>, one artifact, this console
                                             session only, PNG only, IMAGE_TTL seconds
  POST /api/artifacts/<id>/feedback          kept / dismissed / replaced (lane L), only while form_learning records
  POST /api/artifacts/<id>/forget            the artifact and everything made from it (as `nsf artifact forget`)
  POST /api/artifacts/make                   lane P's pipeline as the owner's request, run off the request thread;
  GET  /api/artifacts/jobs/<id>              polled by the page until it ends
  POST /api/artifacts/uploads                upload (off unless console_artifact_upload): start, then
  POST /api/artifacts/uploads/<id>/chunk     raw chunks of at most UPLOAD_CHUNK bytes in order (?offset=N), then
  POST /api/artifacts/uploads/<id>/finish    store.add (it decodes under limits and screens text): kept or why not

No route takes a path or a file name from the request: an id is a lookup key; an uploaded file's name is only its
label. The server sends data (JSON) for tables and documents, never markup; bytes only from /content (opaque to the
browser: octet-stream, nosniff, no-store) and /artifact-image (PNG bytes the store holds, checked to be PNG). Nothing
is written to disk outside the store; upload chunks live in memory, bounded, and expire.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from ..instance.errors import Conflict, NotFound, ValidationError

PAGE = 20                         # artifacts per page of the list
MAX_FROM = 8                      # sources named per item
MAX_FACTS = 40
MAX_TABLE_ROWS = 200              # rows of a table sent to the page (the rest is said)
MAX_TABLE_COLUMNS = 24
MAX_CELL = 200                    # characters of a cell
MAX_BLOCKS = 400                  # blocks of a document sent to the page
MAX_BLOCK_TEXT = 4000
CONTENT_CHUNK = 64 << 10          # bytes per write of /content
IMAGE_TTL = 120.0                 # seconds an image address works
MAX_IMAGES = 32                   # live image addresses per console
MAX_MAKE_SOURCES = 8
MAX_REQUEST = 2000                # characters of what the owner wants to know
MAX_JOBS = 8                      # finished productions kept for the page to read
UPLOAD_CHUNK = 1 << 20            # bytes of one upload chunk (the raw body limit of that route alone)
UPLOAD_TTL = 600.0                # seconds an unfinished upload is kept in memory
MAX_UPLOADS = 2                   # unfinished uploads at once
ID = re.compile(r"^artifact-[0-9a-f]{32}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
JOB = re.compile(r"^[0-9a-f]{32}$")
IMAGE_PATH = re.compile(r"^/artifact-image/([A-Za-z0-9_-]{43})$")
PNG = b"\x89PNG\r\n\x1a\n"
EXPLORABLE = "application/vnd.neuroscifi.explorable+html"
SVG_TYPES = ("image/svg+xml",)
SOUND_TYPES = ("audio/wav", "audio/x-wav", "audio/wave")
OFF = ("The console's view of what is kept and made is off for this individual. Turn it on with "
       "`nsf config --console-artifacts on`.")
UPLOAD_OFF = ("Upload from the console is off: a new way for files to come in is yours to turn on, with "
              "`nsf config --console-artifact-upload on`. Until then, keep files with `nsf artifact add FILE`.")
FORM_WORDS = {"chart.line": "a line chart", "chart.bar": "a bar chart", "chart.scatter": "a scatter chart",
              "chart.histogram": "a histogram", "chart.heatmap": "a heat map", "chart.small_multiples":
              "small multiples (one small chart per series)", "timeline": "a timeline", "graph.nodelink":
              "a node-link diagram", "graph.tree": "a tree diagram", "flow.steps": "a flow of steps",
              "document.html": "a document", "document.md": "a document (Markdown)", "table.html": "a table",
              "data.csv": "a data file (CSV)", "data.json": "a data file (JSON)", "sound.tone": "a tone",
              "sound.sequence": "a sequence of tones", "sound.sonify": "a sonification (values as pitch)",
              "image.raster": "a picture", "image.apng": "an animated picture", "explorable.model":
              "an explorable model with sliders", "explorable.steps": "a step-through", "solid.view": "a 3D viewer",
              "solid.mesh": "a solid (mesh file)", "solid.box": "a solid", "solid.extrude": "a solid",
              "solid.revolve": "a solid", "surface.grid": "a surface", "solid.points": "a set of points in 3D"}
RELATION_WORDS = {"consistent_with": "agrees with", "contradicts": "disagrees with", "describes": "describes",
                  "same_subject": "is about the same subject as"}


@dataclass
class Shared:
    """What the console keeps in memory for this view (one per server; lost on restart, which also issues a new
    session token)."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    images: dict = field(default_factory=dict)      # token -> (artifact id, expires, session digest)
    jobs: dict = field(default_factory=dict)        # job id -> dict
    uploads: dict = field(default_factory=dict)     # upload id -> dict


@dataclass(frozen=True)
class Raw:
    """A reply of bytes (not JSON): the server writes it in CONTENT_CHUNK pieces with an exact Content-Length."""
    body: bytes
    mime: str = "application/octet-stream"


def shared(server) -> Shared:
    with _CREATE:
        state = getattr(server, "artifact_view", None)
        if state is None:
            state = server.artifact_view = Shared()
        return state


_CREATE = threading.Lock()


def _session(server) -> str:
    return hashlib.sha256(server.token.encode()).hexdigest()


def _config(c):
    from ..instance import store as istore
    return istore.config(c.db)


def _on(c):
    if not _config(c).console_artifacts:
        raise Conflict(OFF)


def _id(text):
    if not ID.match(text or ""):
        raise ValidationError("an artifact id is 'artifact-' and 32 hexadecimal digits")
    return text


def _artifact_errors(fn):
    """Lane A's and lane P's named failures (ValueError subclasses) as the console's 400/404, never a 500."""
    def run(c, p, q, b):
        from ..artifacts.types import ArtifactError
        try:
            return fn(c, p, q, b)
        except NotFound:
            raise
        except ArtifactError as error:
            raise ValidationError(str(error)) from None
    run.__name__ = fn.__name__
    return run


# ---- plain words ---------------------------------------------------------------------------------------------

def _n(count, one, many=None) -> str:
    count = int(count or 0)
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _seconds(s) -> str:
    s = float(s or 0)
    if s < 60:
        return f"{s:.1f} seconds" if s != int(s) else _n(int(s), "second")
    return f"{int(s // 60)} min {s % 60:.0f} s"


def what_it_is(record) -> str:
    """What an artifact is, in plain words, from its index (a made one by the form it was made as)."""
    index = record.index or {}
    rep = index.get("representation") or {}
    kind = rep.get("kind")
    form = (index.get("spec") or {}).get("form")
    if record.origin == "made" and form:
        return FORM_WORDS.get(form, f"something made as {form}")
    if kind == "table":
        return f"a table of {_n(rep.get('n_rows'), 'row')} and {_n(rep.get('n_columns'), 'column')}"
    if kind == "signal":
        channels = int(rep.get("channels") or 1)
        return f"a recording of {_seconds(rep.get('duration_s'))}" + (f" ({channels} channels)" if channels > 1 else "")
    if kind == "document":
        heads = rep.get("headings") or []
        return (f"a document of {_n(rep.get('n_blocks'), 'part')}" +
                (f", headed “{str(heads[0].get('text'))[:60]}”" if heads else ""))
    if kind == "raster":
        shape = rep.get("shape") or [rep.get("height"), rep.get("width")]
        if len(shape) >= 2 and shape[0] and shape[1]:
            return f"a picture of {shape[1]} × {shape[0]} pixels"
        return "a picture"
    if kind == "frames":
        return f"an animation of {_n(rep.get('n_frames'), 'frame')}"
    if kind == "drawing":
        return f"a drawing of {_n(rep.get('n_shapes'), 'shape')}"
    if kind == "mesh":
        return f"a solid of {_n(rep.get('n_faces') or rep.get('faces'), 'triangle')}"
    if kind == "tree" and rep.get("model"):
        return f"a model with {_n(len(rep.get('parameters') or []), 'setting')} you can change"
    if kind == "tree":
        return f"nested data of {_n(rep.get('n_nodes'), 'entry', 'entries')}"
    if kind == "graph":
        return f"{_n(rep.get('n_nodes'), 'item')} and {_n(rep.get('n_edges'), 'link')} between them"
    if kind == "features":
        return f"a map of {_n(rep.get('n_features'), 'place')}"
    if kind == "bundle":
        return f"an archive of {_n(rep.get('n_members'), 'file')}"
    if kind == "opaque" or index.get("error"):
        return f"a {record.format} file nothing here can read" if record.format != "unknown" else \
            "a file nothing here can read"
    return f"a {record.format} file"


def _view(record, decoded) -> str:
    """How the page shows it: svg, table, document, sound, explorable, raster, or facts."""
    media = record.media_type
    if media in SVG_TYPES:
        return "svg"
    if media == EXPLORABLE:
        return "explorable"
    kind = getattr(getattr(decoded, "representation", None), "kind", None)
    if media in SOUND_TYPES and kind == "signal":
        return "sound"
    if record.format in ("png", "apng") and kind in ("raster", "frames"):
        return "raster"
    if kind == "table":
        return "table"
    if kind == "document":
        return "document"
    return "facts"


def _cell(value) -> str | float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return None if value != value else value
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and value != value:
            return None
        return value
    return str(value)[:MAX_CELL]


def table_data(table) -> dict:
    """A decoded Table as columns and rows of plain values (bounded; the rest is said)."""
    import math
    columns = list(table.columns)[:MAX_TABLE_COLUMNS]
    n = min(int(table.n_rows), MAX_TABLE_ROWS)
    rows = []
    for i in range(n):
        row = []
        for col in columns:
            v = col.values[i] if i < len(col.values) else None
            if col.kind == "time" and v is not None and not (isinstance(v, float) and math.isnan(v)):
                v = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(v))).removesuffix(" 00:00:00")
            row.append(_cell(v))
        rows.append(row)
    return {"columns": [{"name": str(c.name)[:MAX_CELL], "unit": c.unit, "kind": c.kind} for c in columns],
            "rows": rows, "n_rows": int(table.n_rows), "n_columns": len(table.columns),
            "title": table.title}


def document_data(document) -> dict:
    """A decoded Document as blocks of text (tables as data, figures by their caption and order)."""
    blocks, figure = [], 0
    for block in list(document.blocks)[:MAX_BLOCKS]:
        out = {"kind": block.kind, "level": int(block.level or 0), "text": str(block.text or "")[:MAX_BLOCK_TEXT]}
        if block.kind == "table" and hasattr(block.ref, "columns"):
            out["table"] = table_data(block.ref)
        elif block.kind == "figure":
            out["figure"] = figure
            figure += 1
        blocks.append(out)
    return {"title": document.title, "blocks": blocks, "n_blocks": len(document.blocks)}


def made_document(spec) -> dict | None:
    """A document NeuroSciFi made, from its stored specification (data: the blocks it was made from), in the shape of
    document_data: references to figures and tables in words ("Figure 1"), a figure by its caption and its order
    (the page reads the picture itself from the stored file), a table's columns as rows."""
    blocks = ((spec or {}).get("data") or {}).get("blocks")
    if not isinstance(blocks, list):
        return None
    numbers, counts = {}, {"figure": 0, "table": 0}
    for b in blocks:
        if isinstance(b, dict) and b.get("kind") in counts:
            counts[b["kind"]] += 1
            if b.get("id"):
                numbers[b["id"]] = f"{'Figure' if b['kind'] == 'figure' else 'Table'} {counts[b['kind']]}"
    out, figure, table = [], 0, 0
    for b in blocks[:MAX_BLOCKS]:
        if not isinstance(b, dict):
            continue
        kind = str(b.get("kind"))
        text = b.get("text") if "parts" not in b else "".join(
            str(p.get("text")) if "text" in p else numbers.get(p.get("ref"), "") for p in b["parts"] if isinstance(p, dict))
        block = {"kind": kind, "level": int(b.get("level") or 0), "text": str(text or "")[:MAX_BLOCK_TEXT]}
        if kind == "figure":
            inner = b.get("figure") if isinstance(b.get("figure"), dict) else {}
            figure += 1
            block["text"] = f"Figure {figure}. {b.get('caption') or inner.get('title') or ''}".strip()[:MAX_BLOCK_TEXT]
            block["figure"] = figure - 1
        elif kind == "table" and isinstance(b.get("table"), dict):
            table += 1
            columns = [c for c in b["table"].get("columns") or [] if isinstance(c, dict)][:MAX_TABLE_COLUMNS]
            n = max((len(c.get("values") or []) for c in columns), default=0)
            block["text"] = f"Table {table}. {b.get('caption') or ''}".strip()
            block["table"] = {"columns": [{"name": str(c.get("name"))[:MAX_CELL], "unit": c.get("unit"),
                                           "kind": c.get("kind")} for c in columns],
                              "rows": [[_cell((c.get("values") or [None] * n)[i] if i < len(c.get("values") or [])
                                              else None) for c in columns] for i in range(min(n, MAX_TABLE_ROWS))],
                              "n_rows": n, "n_columns": len(b["table"].get("columns") or []), "title": None}
        out.append(block)
    return {"title": spec.get("title"), "blocks": out, "n_blocks": len(blocks)}


def _label_of(db, artifact_id) -> str | None:
    row = db.execute("SELECT label FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    return row[0] if row else None


def _summary(db, record) -> dict:
    made_from = [r[0] for r in db.execute(
        "SELECT dst_id FROM artifact_links WHERE src=? AND relation='derived_from' AND dst_kind='artifact' "
        "ORDER BY created_at, dst_id LIMIT ?", (record.id, MAX_FROM + 1))]
    return {"id": record.id, "label": record.label, "origin": record.origin, "made": record.origin == "made",
            "provenance": record.provenance, "what": what_it_is(record), "format": record.format,
            "media_type": record.media_type, "size": record.size, "created_at": record.created_at,
            "state": record.state, "flagged": record.flagged,
            "from": [{"id": s, "label": _label_of(db, s)} for s in made_from[:MAX_FROM]],
            "from_more": len(made_from) > MAX_FROM}


# ---- route handlers --------------------------------------------------------------------------------------------

@_artifact_errors
def listing(c, p, q, b):
    from ..artifacts import store
    from .server import _number, _query
    _on(c)
    q = _query(q, ("page",))
    page = _number(q.get("page", "0"), "page", int)
    if not 0 <= page <= 10_000 // PAGE - 1:
        raise ValidationError(f"page must be between 0 and {10_000 // PAGE - 1}")
    if not store.present(c.db):
        return {"items": [], "page": 0, "more": False, "counts": {"given": 0, "made": 0},
                "made_label": store.MADE_LABEL, "upload": False, "learning": "off"}
    out = store.listing(c.db, limit=(page + 1) * PAGE + 1)
    rows = out["items"][page * PAGE:(page + 1) * PAGE]
    items = [_summary(c.db, store.get(c.db, row["id"])) for row in rows]
    cfg = _config(c)
    return {"items": items, "page": page, "more": len(out["items"]) > (page + 1) * PAGE,
            "counts": {k: out["counts"].get(k, 0) for k in ("given", "made")}, "made_label": out["made_label"],
            "upload": bool(cfg.console_artifact_upload), "max_bytes": cfg.artifact_max_bytes,
            "upload_chunk": UPLOAD_CHUNK, "learning": cfg.form_learning}


@_artifact_errors
def detail(c, p, q, b):
    from ..artifacts import forms, store
    from ..artifacts.relate_cli import CLAIM_LABEL, CLAIM_RELATIONS
    from .server import _query
    _on(c)
    _query(q, ())
    artifact_id = _id(p[0])
    record, _, decoded, error = store.decoded_held(c.db, artifact_id)
    out = _summary(c.db, record)
    view = _view(record, decoded)
    out["view"] = view
    index = record.index or {}
    out["notes"] = [str(n)[:300] for n in (index.get("notes") or [])[:8]]
    out["made_notes"] = [str(n)[:400] for n in (index.get("made_notes") or [])[:8]]
    check = index.get("check")
    out["check"] = None if not check else {"ok": bool(check.get("ok")), "failed": list(check.get("failed") or []),
                                           "warnings": list(check.get("warnings") or [])}
    if decoded is not None:
        out["facts"] = store.facts_of(decoded.representation)[:MAX_FACTS]
        rep = decoded.representation
        if view == "table":
            out["table"] = table_data(rep)
        elif view == "document":
            made = made_document(store.spec_of(c.db, artifact_id)) if record.origin == "made" else None
            out["document"] = made or document_data(rep)
        elif view == "sound":
            out["sound"] = {"seconds": float(rep.samples.shape[0] / rep.rate_hz) if rep.rate_hz else 0.0,
                            "channels": int(rep.samples.shape[1]) if rep.samples.ndim > 1 else 1,
                            "rate_hz": float(rep.rate_hz)}
        elif view == "raster":
            px = getattr(rep, "pixels", None)
            if px is None and getattr(rep, "frames", None):
                px = rep.frames[0].pixels
            out["raster"] = {"width": int(px.shape[1]), "height": int(px.shape[0])} if px is not None else {}
    else:
        out["facts"] = [{"name": "size", "value": record.size, "how": "bytes stored"}]
        out["unread"] = (error or {}).get("message") or "nothing here can read this format"
    if view == "explorable":
        out["spec"] = store.spec_of(c.db, artifact_id)
    production = index.get("production") if record.origin == "made" else None
    if production:
        from ..artifacts.produce import why_text
        out["why"] = why_text(production).splitlines()
    shown = store.show(c.db, artifact_id)
    relations = []
    for link in shown["links"]:
        if link["relation"] not in CLAIM_RELATIONS:
            continue
        other = link["dst_id"] if link["src"] == artifact_id else link["src"]
        relations.append({"relation": link["relation"], "words": RELATION_WORDS[link["relation"]],
                          "other": {"id": other, "label": _label_of(c.db, other)}, "how": link["how"],
                          "confidence": link["confidence"]})
    out["relations"] = relations[:24]
    out["claim_label"] = CLAIM_LABEL
    out["made_into"] = [{"id": r[0], "label": _label_of(c.db, r[0])} for r in c.db.execute(
        "SELECT src FROM artifact_links WHERE dst_kind='artifact' AND dst_id=? AND relation='derived_from' "
        "ORDER BY created_at, src LIMIT ?", (artifact_id, MAX_FROM))]
    learning = _config(c).form_learning
    out["feedback"] = None
    if record.origin == "made" and learning in ("record", "apply"):
        out["feedback"] = {"mode": learning, "forms": [{"form": f, "label": forms.label_of((f,))}
                                                       for f in forms.FORMS]}
    out["export"] = f"nsf artifact export {artifact_id} FILE"
    return out


@_artifact_errors
def content(c, p, q, b):
    from ..artifacts import store
    from .server import _query
    _on(c)
    _query(q, ())
    return Raw(store.read_bytes(c.db, _id(p[0]), now=c.now))


@_artifact_errors
def image(c, p, q, b):
    """Mint the address of one raster picture: unguessable (256 bits), IMAGE_TTL seconds, this console session only.
    Needed because an <img> cannot carry the bearer header, and the policy (img-src 'self') admits no blob: or data:
    picture; the address is never logged (the console logs no request)."""
    from ..artifacts import store
    from .server import _fields
    _on(c)
    _fields(b)
    artifact_id = _id(p[0])
    record = store.get(c.db, artifact_id)
    if record.format not in ("png", "apng") or not store.read_bytes(c.db, artifact_id, now=c.now).startswith(PNG):
        raise ValidationError("only a PNG picture is shown here; export it to look at it elsewhere")
    state = shared(c.server)
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with state.lock:
        for key in [k for k, v in state.images.items() if v[1] <= now]:
            del state.images[key]
        while len(state.images) >= MAX_IMAGES:
            del state.images[min(state.images, key=lambda k: state.images[k][1])]
        state.images[token] = (artifact_id, now + IMAGE_TTL, _session(c.server))
    return {"url": f"/artifact-image/{token}", "expires_in": IMAGE_TTL}


def serve_image(handler, path) -> bool:
    """GET /artifact-image/<token> (the static branch of console/server.py: an <img> sends no bearer header). False
    when the path is not an image address. A token is valid only while it lives, only in the console process that
    minted it and only under the session token it was minted under."""
    m = IMAGE_PATH.match(path)
    if not m:
        return False
    if handler.command not in ("GET", "HEAD"):
        handler.fail(405, "method not allowed")
        return True
    server = handler.server
    state = shared(server)
    with state.lock:
        entry = state.images.get(m.group(1))
    if entry is None or entry[1] <= time.monotonic() or not hmac.compare_digest(entry[2], _session(server)):
        handler.fail(404, "not found")
        return True
    from ..artifacts import store as astore
    from ..instance import store as istore
    db = None
    try:
        db = istore.open_existing(server.db_path)
        data = astore.read_bytes(db, entry[0])
    except Exception:              # noqa: BLE001 - forgotten since, or unreadable: the picture is simply gone
        handler.fail(404, "not found")
        return True
    finally:
        if db is not None:
            db.close()
    if not data.startswith(PNG):
        handler.fail(404, "not found")
        return True
    handler.reply_raw(200, data, "image/png")
    return True


@_artifact_errors
def feedback(c, p, q, b):
    from ..artifacts import learn
    from .server import _fields
    _on(c)
    _fields(b, ("reaction",), ("form",))
    reaction, form = b["reaction"], b.get("form")
    if reaction not in learn.OWNER_OUTCOMES:
        raise ValidationError("reaction must be kept, dismissed or replaced")
    if (reaction == "replaced") != isinstance(form, str):
        raise ValidationError("a replacement names the form you wanted (form), and only a replacement does")
    out = learn.record_outcome(c.db, _id(p[0]), reaction, by=form, now=c.now)
    return {"recorded": bool(out.get("recorded")), "why": out.get("why")}


@_artifact_errors
def forget(c, p, q, b):
    from ..artifacts import store
    from ..instance import checkpoints, governance
    from .server import _fields
    _on(c)
    _fields(b)
    result = store.forget_artifact(c.db, _id(p[0]), c.now)
    if (result.get("effects") or {}).get("checkpoints_stale"):
        result = result | checkpoints.after_forget(c.db, c.now)
        if result.get("checkpoints_stale"):
            result["next_steps"] = [governance.REBUILD_HINT.format(n=len(result["checkpoints_stale"]))]
    effects = result.get("effects") or {}
    return {"status": result.get("status"), "removed": int(effects.get("artifacts_removed", 0)),
            "next_steps": result.get("next_steps") or []}


# ---- make (lane P's pipeline, as the owner's request) ------------------------------------------------------------

@_artifact_errors
def make(c, p, q, b):
    """Start a production from kept artifacts; the page polls /api/artifacts/jobs/<id>. One runs at a time per
    console; it is bounded by the pipeline's own budgets (60 s, 16 MiB) and the owner asked for it (not background
    work, so the quiet switch does not hold it, as it does not hold `nsf artifact make`)."""
    from ..artifacts import store
    from .server import _fields
    _on(c)
    _fields(b, ("sources", "request"), ("form",))
    sources, request, form = b["sources"], b["request"], b.get("form")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_MAKE_SOURCES or \
            not all(isinstance(s, str) for s in sources) or len(set(sources)) != len(sources):
        raise ValidationError(f"choose from 1 to {MAX_MAKE_SOURCES} different kept artifacts")
    for s in sources:
        store.get(c.db, _id(s))
    if not isinstance(request, str) or not request.strip() or len(request) > MAX_REQUEST:
        raise ValidationError(f"say what you want to know or see, in at most {MAX_REQUEST} characters")
    if form is not None and (not isinstance(form, str) or not re.fullmatch(r"[a-z_.]{1,40}", form)):
        raise ValidationError("form must be a form name")
    state = shared(c.server)
    with state.lock:
        if any(j["state"] == "running" for j in state.jobs.values()):
            raise Conflict("something is being made already; wait for it to finish")
        while len(state.jobs) >= MAX_JOBS:
            del state.jobs[min(state.jobs, key=lambda k: state.jobs[k]["started"])]
        job_id = secrets.token_hex(16)
        job = {"id": job_id, "state": "running", "started": time.time(), "result": None, "error": None}
        state.jobs[job_id] = job
    worker = threading.Thread(target=_run, args=(c.server, job, request.strip(), list(sources), form),
                              name="nsf-console-make", daemon=True)
    worker.start()
    return {"job": job_id, "state": "running"}


def _run(server, job, request, sources, form):
    """The production, in its own connection; the Presence entity shows it through the in-flight record (types only:
    the activity, the subsystem and a fixed purpose; never the request's words)."""
    from ..artifacts import forms, produce
    from ..artifacts.types import ArtifactError
    from ..instance import store as istore
    from ..instance.errors import InstanceError
    from ..presence import activity
    outcome = {"state": "failed"}
    db = None
    try:
        db = istore.open_existing(server.db_path)
        db.execute("PRAGMA busy_timeout=5000")
        with activity.track("generating", subsystem="artifacts", purpose="console make", priority="foreground"):
            result = produce.produce(db, request, sources, form=form, context=forms.Context(device="console"),
                                     now=server.clock())
        outcome = {"state": "done", "result": {
            "status": result.status, "artifact": result.artifact, "words": list(result.words)[:40],
            "why": produce.why_text(result.record, short=True).splitlines(), "kept": list(result.kept)}}
    except (ArtifactError, InstanceError) as error:
        outcome = {"state": "failed", "error": str(error)[:600]}
    except Exception as error:           # noqa: BLE001 - the page says it failed; the type only, never content
        outcome = {"state": "failed", "error": f"it could not be made ({type(error).__name__})"}
    finally:
        if db is not None:
            db.close()
        job.update(outcome, ended=time.time())


def job(c, p, q, b):
    from .server import _query
    _on(c)
    _query(q, ())
    if not JOB.match(p[0]):
        raise ValidationError("a job id is 32 hexadecimal digits")
    state = shared(c.server)
    with state.lock:
        found = state.jobs.get(p[0])
        return None if found is None else {k: found.get(k) for k in ("id", "state", "result", "error")}


def job_or_404(c, p, q, b):
    out = job(c, p, q, b)
    if out is None:
        raise NotFound("no such production (the console was restarted, or it is long finished)")
    return out


# ---- upload (off by default) -------------------------------------------------------------------------------------

def _upload_on(c):
    _on(c)
    if not _config(c).console_artifact_upload:
        raise Conflict(UPLOAD_OFF)


def _expire(state, now):
    for key in [k for k, u in state.uploads.items() if u["expires"] <= now]:
        del state.uploads[key]


def upload_start(c, p, q, b):
    from .server import _fields, _int
    _upload_on(c)
    _fields(b, ("name", "size"))
    name, size = b["name"], _int(b["size"], "size")
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise ValidationError("name is the file's name, at most 255 characters (it is only the label)")
    cap = _config(c).artifact_max_bytes
    if not 1 <= size <= cap:
        raise ValidationError(f"a file is kept only up to {cap:,} bytes (`nsf config --artifact-max-mib`)"
                              if size > cap else "the file is empty")
    state = shared(c.server)
    now = time.monotonic()
    with state.lock:
        _expire(state, now)
        if len(state.uploads) >= MAX_UPLOADS:
            raise Conflict("two uploads are unfinished already; wait for them or reload the page")
        upload_id = secrets.token_hex(16)
        state.uploads[upload_id] = {"name": name, "size": size, "parts": [], "received": 0, "expires": now + UPLOAD_TTL}
    return {"upload": upload_id, "chunk_bytes": UPLOAD_CHUNK}


def _upload(state, upload_id):
    if not JOB.match(upload_id):
        raise ValidationError("an upload id is 32 hexadecimal digits")
    found = state.uploads.get(upload_id)
    if found is None:
        raise NotFound("no such upload (it finished, expired or the console was restarted)")
    return found


def upload_chunk(c, p, q, b):
    """`b` is the raw chunk (bytes): console/server.py reads it for this route only, up to UPLOAD_CHUNK bytes."""
    from .server import _number, _query
    _upload_on(c)
    q = _query(q, ("offset",))
    offset = _number(q.get("offset"), "offset", int)
    state = shared(c.server)
    with state.lock:
        _expire(state, time.monotonic())
        up = _upload(state, p[0])
        if offset != up["received"]:
            raise ValidationError(f"chunks come in order: the next starts at {up['received']}")
        if up["received"] + len(b) > up["size"]:
            del state.uploads[p[0]]
            raise ValidationError("more bytes came than the file's announced size; the upload is dropped")
        up["parts"].append(bytes(b))
        up["received"] += len(b)
        up["expires"] = time.monotonic() + UPLOAD_TTL
        return {"received": up["received"], "size": up["size"]}


def upload_finish(c, p, q, b):
    from ..artifacts import store
    from ..artifacts.types import ArtifactError
    from ..presence import activity
    from .server import _fields
    _upload_on(c)
    _fields(b)
    state = shared(c.server)
    with state.lock:
        up = _upload(state, p[0])
        if up["received"] != up["size"]:
            raise ValidationError(f"{up['received']:,} of {up['size']:,} bytes came; send the rest first")
        del state.uploads[p[0]]
    data = b"".join(up["parts"])
    up["parts"].clear()
    try:
        with activity.track("reading", subsystem="artifacts", purpose="console upload", priority="foreground"):
            record = store.add(c.db, data, name=up["name"], now=c.now)
    except ArtifactError as error:
        return {"kept": False, "why": str(error)}
    return {"kept": True, "artifact": _summary(c.db, record)}
