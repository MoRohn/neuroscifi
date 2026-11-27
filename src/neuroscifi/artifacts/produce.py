"""The production pipeline (OMNIMIND Wave O-1, lane P, ADR-076): from files and a request to a made, checked, stored
result, with a visible "why".

Interpret -> Conceptualize -> Explore -> Choose -> Plan -> Generate -> Render -> Evaluate -> Repair -> (Converge) ->
Finalize. Each step is a named function below; what went into it and what came out is recorded, step by step, in the
production record, so that the owner (`nsf artifact show ID --why`) and a test can see why the result has the form it
has.

- interpret: every source is decoded again from its bytes under `Limits` (a flagged artifact, a refusal, an undecoded
  format or a header-only file is never used as content, and the record says why). An archive of Python files is read
  as its package (the combined graph of its modules: the files' relations are its structure, the listing is a
  by-product of packing); an archive or compressed stream holding exactly one decoded file is read as that file;
  several Python files given together are read together as one package.
  A table whose columns the request names is read as those columns (and its column of times): what was asked about
  is the content planned for.
- conceptualize: the frozen form planner decides the form (`forms.plan`); `form=` names one (the planner's explicit
  path: honoured when feasible, else the plan's stated nearest feasible form). A generator form the planner has no
  form for (such as `solid.mesh`, a given mesh as an STL file) is made as named when it is registered here, and the
  planner's own plan is recorded beside it. `forms.foresee` runs before anything is made.
- explore: up to `alternatives` candidates that differ in kind (`forms.alternatives`).
- choose: the order in which they are tried (the plan's choice first).
- plan: a specification builder per candidate (produce_builders.py); "cannot build" is a named result, not an error.
- generate (`make.make`), render (the bytes as a file: media type, size, export extension), evaluate (`check.check`:
  the independent verdict; carried information is the share of the source values the check read back from the
  bytes), repair (at most three rounds of the generators' named repair rules, each round recorded).
- converge: technical verdict first, then carried information, then the planner's total, then simplicity.
- finalize: `store.add_made` with the spec as editable source, `derived_from` links to every source, the check's
  verdict and this record (bounded JSON in the artifact's index). Each foreseen problem and estimate is paired with
  what the check then found, and the pairs are kept as a SIMULATION record in the imagined store (NOT_EVIDENCE) with
  its outcome slot filled by the observation.

Proportion: when the planned form is words, nothing is made and nothing is stored: the measured facts are given as
sentences with the plan's reason. Budgets: the context's seconds and bytes are the planner's budget and are enforced
here (a deadline checked between steps; made bytes over the byte budget are refused). A stop (Ctrl-C, the deadline,
any error) leaves nothing in the store: given files this production stored are forgotten again, and the imagined
record is removed. A result whose technical checks fail is never stored: the production ends "could not make this
well" with the findings in plain words and offers the next candidate.

Nothing made here is evidence (ADR-049, ADR-071): the record and everything in it are labelled as made by the system.
No model is called.
"""
from __future__ import annotations

import functools
import hashlib
import json
import time
from dataclasses import dataclass, field, replace

from . import forms, guarded
from . import produce_builders as B
from .spec import Spec, canonical_json
from .types import ArtifactError, ContentShape, HeaderOnly, Limits, Malformed, Refused

RECORD = "neuroscifi.production/1"
LABEL = "production record: made by the system, not evidence"
MAX_RECORD_BYTES = 14 << 10
MAX_ALTERNATIVES = 4
MAX_SOURCES = 32
MECHANISM, MECHANISM_VERSION = "artifacts.produce", "1"
# Findings whose passing means the source's values were read back from the bytes (carried information).
CARRIED_RULES = ("values_recovered", "nodes_recovered", "edges_recovered", "cells_match", "values_exact",
                 "order_preserved", "printed_values_match", "all_values_binned")
SVG_FORMS = ("chart.line", "chart.bar", "chart.scatter", "chart.histogram", "chart.heatmap", "chart.small_multiples",
             "timeline", "graph.nodelink", "graph.tree", "flow.steps")
# A foreseen problem -> (the check findings, or the generator's refusal rule, that show it on the first made bytes;
# the made forms whose check or generator always looks for it). A finding a check emits only when it fails counts as
# "not found" for those forms. Elsewhere, and for codes with no entry, the problem is "not observable here".
OBSERVABLE = {"label_overlap": (("tick_labels_overlap_x", "tick_labels_overlap_y", "text_overlap"), SVG_FORMS),
              "too_many_categories": (("tick_labels_overlap_x",), ("chart.bar", "chart.heatmap")),
              "too_many_series": (("series_distinguishable",), ("chart.line", "chart.scatter")),
              "points_per_pixel": (("points_exceed_pixels",), ("chart.line", "chart.small_multiples")),
              "too_many_nodes": (("nodes_apart",), ("graph.nodelink", "graph.tree", "flow.steps")),
              "sound_too_long": (("max_duration",), ("sound.sonify",))}
WORDS = ("prose", "spoken")
# Relating the sources of one production (lane R's `relate.compare`, ADR-077): at most this many pairs, and no new
# pair is started after this many seconds (a pair once started runs to its end: `compare` is not interruptible).
MAX_RELATE_PAIRS = 6
MAX_RELATE_SECONDS = 10.0
MAX_RELATE_LINES = 8           # sentences per pair and per verdict shown in the made document


class ProductionStopped(ArtifactError):
    """The production stopped (a deadline, a budget): nothing was stored."""

    reason = "timeout"


@dataclass(frozen=True)
class Given:
    """A file handed over for this production that is not stored yet (the CLI's files): kept only if something is
    made from it."""

    name: str
    data: bytes


@dataclass
class Production:
    status: str                         # "made" | "words" | "failed" | "cannot"
    record: dict
    artifact: str | None = None
    made: object | None = None          # make.Made of the winner (None unless made)
    spec: Spec | None = None
    words: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()          # the losers stored with --keep
    stored_sources: tuple[str, ...] = ()

    def summary(self) -> str:
        return why_text(self.record, short=True)


@dataclass
class _Run:
    """The mutable state of one production (step timings, the record being written)."""

    request: str
    context: forms.Context
    limits: Limits
    deadline: float | None
    steps: list = field(default_factory=list)

    def step(self, name, fn, *args, summary_in=None, summary_out=None, **kwargs):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ProductionStopped(f"the time budget of {self.context.budget_s:g} seconds was used up before "
                                    f"{name}: nothing was stored")
        start = time.monotonic()
        out = fn(*args, **kwargs)
        self.steps.append({"step": name, "seconds": round(time.monotonic() - start, 4),
                           "in": summary_in or {}, "out": summary_out(out) if summary_out else {}})
        return out


# ---------------------------------------------------------------------------------------------------------------
# Interpret


def _decode(data: bytes, name: str, limits: Limits):
    """(representation, detected format, None) or (None, format, why not)."""
    try:
        decoded = guarded.decode(data, name=name, limits=replace(limits, refuse_flagged=True))
    except HeaderOnly as error:
        return None, "unknown", f"only its header is read here ({error})"[:300]
    except ArtifactError as error:
        return None, "unknown", f"{error.reason}: {error}"[:300]
    rep = decoded.representation
    if getattr(rep, "kind", None) == "opaque":
        return None, decoded.detected.format, "nothing here reads its format"
    if decoded.partial:
        return None, decoded.detected.format, "a limit stopped its decoding early: " + "; ".join(decoded.notes)[:200]
    return rep, decoded.detected.format, None


def _unwrap(rep, label):
    """An archive read as its Python package, or as its one decoded file (the module doc's rules)."""
    if getattr(rep, "kind", None) == "tree" and not hasattr(rep, "model"):
        from .adapters.jsontree import as_table
        table = as_table(rep)
        if table is not None:
            return table, "as a table (an array of flat records, one row each)"
    if getattr(rep, "kind", None) != "bundle":
        return rep, "as decoded"
    package = [m for m in rep.members if m.path == "(python package)" and m.inner is not None]
    if package:
        return package[0].inner, "as its Python package (the modules and what they import, define and call)"
    inner = [m for m in rep.members if m.inner is not None]
    if len(rep.members) == 1 and len(inner) == 1:
        return inner[0].inner, f"as the one file it holds ({inner[0].path})"
    return rep, "as decoded"


def _within(limits: Limits, deadline: float | None) -> Limits:
    """`limits` with max_seconds no longer than what is left before `deadline` (time.monotonic()): the decode, and
    its guarded child, are given the production's remaining time (code review L1; ruling D of fix lane F1)."""
    if deadline is None:
        return limits
    return replace(limits, max_seconds=max(0.0, min(limits.max_seconds, deadline - time.monotonic())))


def interpret(db, sources, limits: Limits, deadline: float | None = None) -> tuple[list, list]:
    """(pieces, notes per source). `sources`: artifact ids (read from the store) or `Given` files (in memory)."""
    from . import store
    pieces, notes, python = [], [], []
    for n, source in enumerate(sources):
        if isinstance(source, Given):
            label, data, sid, kind = store._label(source.name), bytes(source.data), f"given-{n + 1}", "given"
        else:
            if db is None:
                raise Malformed("an artifact id needs the individual that holds it")
            record = store.get(db, source)
            sid, label, kind = record.id, record.label, record.origin
            if record.flagged:
                notes.append({"id": sid, "label": label, "used": False,
                              "why": "its text looked like instructions to an AI, so it is not used as content"})
                continue
            if record.state != "stored":
                notes.append({"id": sid, "label": label, "used": False, "why": f"its bytes are {record.state}"})
                continue
            data = store.read_bytes(db, sid)
        rep, fmt, why = _decode(data, label, _within(limits, deadline))
        if rep is None:
            notes.append({"id": sid, "label": label, "format": fmt, "used": False, "why": why})
            continue
        if fmt == "python":
            python.append((label, data, sid))
        rep, reading = _unwrap(rep, label)
        if kind == "made":
            reading += "; it was made by the system"
        pieces.append(B.Piece(sid, label, rep, fmt, rep.shape(), reading))
        notes.append({"id": sid, "label": label, "format": fmt, "used": True, "reading": reading,
                      "kind": rep.kind, "shape": _shape(rep.shape())})
    if len(python) >= 2:
        from .adapters.code import python_package_graph
        try:
            graph, _ = python_package_graph({label: data for label, data, _ in python}, limits)
        except ArtifactError:
            graph = None
        if graph is not None:
            ids = {sid for _, _, sid in python}
            pieces = [p for p in pieces if p.source not in ids]
            reading = f"the {len(python)} Python files read together as one package"
            pieces.insert(0, B.Piece(python[0][2], ", ".join(lb for lb, _, _ in python), graph, "python-package",
                                     graph.shape(), reading))
            for note in notes:
                if note.get("id") in ids:
                    note["reading"] = reading
    return pieces, notes


def narrow(pieces, request: str, notes: list) -> list:
    """A table whose columns the request names is read as those columns (with its first column of times, when it has
    one, and in the table's own order): the content planned for is what was asked about, and a column the request
    does not name is not used, not even the rows' names (the words "n for each who" name both; no model reads a
    request more finely here). The rest is noted as not used. Other pieces are unchanged."""
    from .types import Table
    out = []
    for p in pieces:
        rep = p.representation
        if getattr(rep, "kind", None) != "table" or len(rep.columns) < 2:
            out.append(p)
            continue
        named = set(B.named_in(request, [c.name for c in rep.columns]))
        if not named or len(named) == len(rep.columns):
            out.append(p)
            continue
        time = next((c.name for c in rep.columns if c.kind == "time"), None)
        keep = [c for c in rep.columns if c.name in named or c.name == time]
        table = Table(tuple(keep), rep.n_rows, rep.title)
        left = [c.name for c in rep.columns if c not in keep]
        reading = (p.reading if p.reading != "as decoded" else "") + ("; " if p.reading != "as decoded" else "") + \
            f"the columns the request names ({', '.join(c.name for c in keep)}); {len(left)} other column(s) not used"
        out.append(replace(p, representation=table, shape=table.shape(), reading=reading))
        for note in notes:
            if note.get("id") == p.source:
                note["reading"], note["shape"] = reading, _shape(table.shape())
    return out


def _shape(shape: ContentShape) -> dict:
    out = {"kinds": list(shape.kinds), "items": shape.n_items, "series": shape.n_series}
    if shape.has_time:
        out["time"] = True
    if shape.has_space:
        out["space"] = shape.has_space
    if shape.has_parameters:
        out["parameters"] = True
    if shape.duration_s:
        out["duration_s"] = round(shape.duration_s, 3)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Several sources: units a document states for a table's columns, and what one source says about another


def _bytes_of(db, sources, pieces) -> dict:
    """piece source id -> the bytes it was decoded from (the relater reads a chart's marks and a file's comments)."""
    from . import store
    out = {}
    for p in pieces:
        if p.source.startswith("given-"):
            out[p.source] = bytes(sources[int(p.source.split("-", 1)[1]) - 1].data)
        elif db is not None:
            out[p.source] = store.read_bytes(db, p.source)
    return out


def transfer(pieces, data_of: dict, notes: list) -> tuple[list, list]:
    """Units a document among the sources states for a table's columns (lane R's `relate.transfer_units`: one line
    names exactly one column and exactly one unit; a column's own unit is kept on a conflict; nothing is guessed).
    Returns (pieces with the units applied, one entry per applied unit: column, unit, from)."""
    from . import relate as R
    from .types import Column, Table
    documents = [p for p in pieces if getattr(p.representation, "kind", None) == "document"]
    if not documents:
        return pieces, []
    out, applied = [], []
    for p in pieces:
        if getattr(p.representation, "kind", None) != "table":
            out.append(p)
            continue
        table, given, conflicts = p.representation, {}, []
        for d in documents:
            tr = R.transfer_units(table, d.representation, source=d.label, data=data_of.get(d.source))
            table = tr.table
            given.update({col: {"unit": v["unit"], "from": v["from"]} for col, v in tr.applied.items()})
            conflicts += [dict(c, source=d.label) for c in tr.conflicts]
        if not given:
            out.append(p)
            continue
        # the unit as a reader sees it: with where it was read ("degC, from README.md"); the bare unit when that
        # would not fit the forms' 40 characters (the record still names the file)
        cols = tuple(Column(c.name, c.kind, _unit_shown(given[c.name]), c.values, c.n_missing)
                     if c.name in given else c for c in table.columns)
        words = "; ".join(f"{col} in {v['unit']} (from {v['from']})" for col, v in given.items())
        reading = (p.reading if p.reading != "as decoded" else "as decoded") + f"; units stated elsewhere: {words}"
        out.append(replace(p, representation=Table(cols, table.n_rows, table.title), reading=reading))
        applied += [{"table": p.label, "column": col, **v} for col, v in given.items()]
        for note in notes:
            if note.get("id") == p.source:
                note["reading"] = reading
                if conflicts:
                    note["unit_conflicts"] = conflicts[:8]
    return out, applied


def _unit_shown(given: dict) -> str:
    text = f"{given['unit']}, from {given['from']}"
    return text if len(text) <= 40 else given["unit"]


def relate_sources(pieces, data_of: dict, db=None) -> dict:
    """Each pair of sources compared by measurement (`relate.compare`), bounded (MAX_RELATE_PAIRS pairs, no pair
    started after MAX_RELATE_SECONDS): {"pairs": [...], "skipped": n, "why_skipped": ..., "_relations": [(i, j,
    Relation)]}. Only what `compare` measured is reported; an inconsistency is shown with both values, not
    resolved. No question is passed: the request says what to make, not which of the sources' facts to compare (read
    as a question, "a report of the calls" made the relater count rows as calls), and without one `compare` answers
    from every strong comparison."""
    from itertools import combinations

    from . import relate as R
    from . import relate_cli
    started = time.monotonic()
    sides = [R.side(p.representation, name=None if "made by the system" in p.reading else p.label,
                    data=data_of.get(p.source)) for p in pieces]
    every = list(combinations(range(len(pieces)), 2))
    out, relations, why = [], [], None
    for n, (i, j) in enumerate(every):
        if n >= MAX_RELATE_PAIRS:
            why = f"at most {MAX_RELATE_PAIRS} pairs of sources are compared"
            break
        if time.monotonic() - started > MAX_RELATE_SECONDS:
            why = f"no new pair is started after {MAX_RELATE_SECONDS:g} seconds"
            break
        rel = R.compare(sides[i], sides[j], None)
        if db is not None:
            rel.shared = [x for x in rel.shared if not relate_cli._forgotten(db, x["name"])]
        names = (pieces[i].label, pieces[j].label)
        rel.names = names
        relations.append((i, j, rel))
        decided = [rel.pairs[k] for k in rel.decided_by]
        also = [p for k, p in enumerate(rel.pairs) if k not in rel.decided_by and p.strong
                and p.verdict != "not_comparable"]
        out.append({"first": names[0], "second": names[1], "answer": rel.answer, "reason": rel.reason,
                    "agree": [R.describe_pair(p, names) for p in decided if p.verdict == "consistent"]
                    [:MAX_RELATE_LINES],
                    "disagree": [R.describe_pair(p, names) for p in decided if p.verdict == "inconsistent"]
                    [:MAX_RELATE_LINES],
                    "also": [R.describe_pair(p, names) for p in also][:MAX_RELATE_LINES],
                    "not_compared": [R.describe_pair(p, names) for p in rel.pairs if p.verdict == "not_comparable"]
                    [:MAX_RELATE_LINES],
                    "notes": [str(x)[:300] for x in rel.notes[:4]],
                    "describes": len(rel.describes), "shared_names": len(rel.shared)})
    return {"pairs": out, "compared": len(out), "of": len(every), "skipped": len(every) - len(out),
            "why_skipped": why, "seconds": round(time.monotonic() - started, 4), "_relations": relations}


def relation_blocks(related: dict) -> list:
    """The made document's own section on how its sources relate, in plain words."""
    if not related or not related.get("pairs"):
        return []
    blocks = [{"kind": "heading", "level": 1, "text": "How the sources relate"},
              {"kind": "paragraph", "text": (
                  "Each pair of sources was compared by measurement: a number, size, count or order measured in one "
                  "is compared with what the other states or shows, within the tolerance named. Where they disagree "
                  "both values are given and nothing is corrected. These comparisons were made by the system; they "
                  "are not evidence.")}]
    for pair in related["pairs"]:
        blocks.append({"kind": "heading", "level": 2, "text": B._cut(f"{pair['first']} and {pair['second']}", 4096)})
        blocks.append({"kind": "paragraph", "text": B._cut(_verdict_words(pair) + ".", 4096)})
        for key in ("disagree", "agree", "also", "not_compared"):
            for text in pair.get(key, []):
                blocks.append({"kind": "list_item", "text": B._cut(text[:1].upper() + text[1:], 4096)})
        for note in pair.get("notes", []):
            blocks.append({"kind": "note", "text": B._cut(note, 4096)})
        if not any(pair.get(k) for k in ("disagree", "agree", "also", "not_compared")):
            blocks.append({"kind": "list_item", "text": "Nothing measured in one of them is stated or shown in the "
                                                        "other, so nothing could be compared."})
    if related.get("skipped"):
        blocks.append({"kind": "note", "text": B._cut(
            f"{related['skipped']} of {related['of']} pairs were not compared: {related['why_skipped']}.", 4096)})
    return blocks


def _verdict_words(pair: dict) -> str:
    """`compare`'s reason as a sentence, a disagreement in capitals ("They DISAGREE (1 of 1 comparison(s) ...)")."""
    reason = str(pair.get("reason") or "")
    if reason.startswith("they disagree"):
        return "They DISAGREE" + reason[len("they disagree"):]
    return reason[:1].upper() + reason[1:]


def relation_words(related: dict) -> list[str]:
    """The relations as sentences, for an answer in words (nothing is made, but what was measured is still said)."""
    out = []
    for pair in (related or {}).get("pairs", []):
        out.append(f"{pair['first']} and {pair['second']}: {_verdict_words(pair)}.")
        out += [f"  {t}" for t in pair["disagree"] + pair["agree"]]
    return out


def _relation_record(related: dict, applied: list) -> dict:
    out = {"label": "the system's claims from measuring the sources; not evidence"}
    if related:
        out |= {"compared": related["compared"], "of": related["of"], "skipped": related["skipped"],
                "why_skipped": related["why_skipped"],
                "pairs": [{"first": p["first"], "second": p["second"], "answer": p["answer"],
                           "reason": p["reason"][:300], "agree": len(p["agree"]), "disagree": len(p["disagree"]),
                           "not_compared": len(p["not_compared"])} for p in related["pairs"]]}
    if applied:
        out["units"] = applied[:16]
    return out


def _record_links(db, pieces, related, now) -> list:
    """The relations found, recorded as `nsf artifact relate` records them (relate_cli.record_links)."""
    from . import relate_cli
    out = []
    for i, j, rel in (related or {}).get("_relations", []):
        first, second = pieces[i].source, pieces[j].source
        if first == second or not (first.startswith("artifact-") and second.startswith("artifact-")):
            continue
        out += relate_cli.record_links(db, first, second, rel, now=now)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Conceptualize, explore, choose


def _resolve_form(form: str | None) -> tuple[str | None, str | None]:
    """(planner form or alias, direct generator form) for an explicitly named form."""
    if form is None:
        return None, None
    name = str(form).strip()
    if name in forms.FORMS or name in forms.ALIASES:
        return name, None
    for f in forms.FORMS.values():
        if f.generator == name and f.name != "sound_view":
            return f.name, None
    if name in B.BUILDERS and "." in name:
        return None, name
    raise Malformed(f"unknown form {name!r}: `nsf artifact forms` lists what can be made; `nsf form plan` names "
                    "the planner's forms")


def conceptualize(request: str, pieces, context, form, db=None):
    """(plan, direct generator form, what was learned or None). With an individual whose `form_learning` is "record"
    or "apply", the plan goes through `learn.plan` (lane L, ADR-078): with "apply" a learned habit may decide among
    the forms the planner admits; with "off" (the default) the planner is called exactly as without learning."""
    explicit, direct = _resolve_form(form)
    request_, shapes = forms.Request(request, explicit_form=explicit), tuple(p.shape for p in pieces)
    if db is not None and _learning(db) != "off":
        from . import learn
        plan, learned = learn.plan(db, request_, shapes, context)
        return plan, direct, learned
    return forms.plan(request_, shapes, context), direct, None


def _learning(db) -> str:
    from . import learn
    return learn.mode(db) if learn.present(db) else "off"


def _learning_entry(db, learned) -> dict:
    """What the production record says about form learning (present only when it is not off): the key the plan was
    looked up under (`learn.key_of`: the content's leading kind, the plan's purpose, the device)."""
    entry = {"mode": _learning(db), "key": list(learned.key),
             "status": learned.status, "words": learned.words[:400]}
    if learned.decided:
        entry["decided"] = learned.decided[:600]
    return entry


def _record_check(db, artifact_id, entry: dict, planner_forms, ok: bool, now) -> dict:
    """The independent check's verdict on a stored made artifact, as lane L's outcome row (form learning record or
    apply only). A form the planner has no name for (a generator form named directly) records nothing, with why."""
    from . import learn
    if not all(f in forms.FORMS for f in planner_forms) or not 1 <= len(planner_forms) <= 2:
        return {"recorded": False, "why": "made as a generator form the planner has no form for, so there is no "
                                          "form to learn about"}
    kind, purpose, device = entry["key"]
    try:
        out = learn.record_outcome(db, artifact_id, "check_passed" if ok else "check_failed", kind=kind,
                                   purpose=purpose, device=device, form="+".join(planner_forms), now=now)
    except ArtifactError as error:
        return {"recorded": False, "why": str(error)[:200]}
    return {"recorded": bool(out.get("recorded")), "outcome": out.get("outcome"), "why": out.get("why", "")[:200]}


def _generator_form(planner_form: str) -> str | None:
    f = forms.FORMS.get(planner_form)
    return f.generator if f else planner_form


def explore(plan, n: int) -> list[tuple[str, ...]]:
    out = [c.forms for c in forms.alternatives(plan, max(1, n))]
    if plan.chosen and plan.chosen not in out:
        out.insert(0, plan.chosen)
    return out[:max(1, n)]


def choose(plan, explored, direct) -> list[dict]:
    """The candidates to try, in order: the plan's choice (or the form named by its generator name) first. When a
    form explored has no builder here (the planner reads `make.catalog()`, the builders are produce_builders.BUILDERS:
    code review M4), the next candidates the planner admitted whose first form HAS a builder follow, each marked
    "fallback" so the record says the production fell through (the planner itself is not changed)."""
    if direct is not None:
        return [{"forms": [direct], "total": None, "complexity": 0, "direct": True}]
    out = []
    for fs in explored:
        cand = plan.candidate(fs)
        out.append({"forms": list(fs), "total": round(cand.total, 4) if cand else None,
                    "complexity": sum(forms.FORMS[f].complexity for f in fs if f in forms.FORMS)})
    if any(fs and fs[0] not in B.BUILDERS and fs[0] not in WORDS for fs in explored):
        seen = {tuple(fs) for fs in explored}
        for cand in (c for c in plan.candidates if c.excluded is None):
            if cand.forms in seen or not cand.forms or cand.forms[0] not in B.BUILDERS:
                continue
            seen.add(cand.forms)
            out.append({"forms": list(cand.forms), "total": round(cand.total, 4), "fallback": True,
                        "complexity": sum(forms.FORMS[f].complexity for f in cand.forms if f in forms.FORMS)})
            if sum(1 for c in out if c.get("fallback")) >= FALLBACKS:
                break
    return out


FALLBACKS = 2      # admitted candidates with a builder tried after a planned form that has none


# ---------------------------------------------------------------------------------------------------------------
# Plan (builders), composition for several pieces


def _shift(blocks, prefix: str) -> list:
    """A sub-document's blocks inside a section: headings one level down, ids prefixed."""
    out = []
    for b in blocks:
        b = dict(b)
        if b["kind"] == "heading":
            b["level"] = min(5, b.get("level", 1) + 1)
        if "id" in b:
            b["id"] = f"{prefix}-{b['id']}"[:64]
        out.append(b)
    return out


def _part(form: str, piece, request: str, options: dict, context) -> dict:
    """What one piece contributes to a composed document, in the form planned for it (or its fallback)."""
    tried = []
    for name in (form, getattr(forms.FORMS.get(form), "fallback", None), "table"):
        if not name or name in tried or name in WORDS:
            continue
        tried.append(name)
        try:
            built = B.build(name, [piece], request, options)
        except B.CannotBuild:
            continue
        kind = built.spec["spec"]
        rule = "; ".join(built.rules)
        shown = forms.label_of((name,))
        if name != form:
            rule = (f"{forms.label_of((form,))} cannot sit inside a document, so it is shown as {shown}" +
                    (f"; {rule}" if rule else ""))
        if kind in SVG_FORMS:
            return {"figure": built.spec, "rule": rule, "shown_as": shown}
        if kind in ("table.html", "data.csv"):
            return {"table": dict(built.spec["data"]), "caption": built.spec["title"], "rule": rule, "shown_as": shown}
        if kind == "document.html":
            return {"blocks": _shift(built.spec["data"]["blocks"], f"p{id(piece) % 9973}"), "rule": rule,
                    "shown_as": shown}
    return {"facts": B.facts_sentences(piece), "shown_as": "measured facts in words",
            "rule": f"{forms.label_of((form,))} cannot sit inside a document and nothing else could show it"}


def compose(pieces, request, options, context, explicit, relations=None) -> tuple[B.Built, list]:
    """Several pieces: each planned on its own (with the same words and context) and placed in one document under a
    heading with its source."""
    parts, plans = {}, []
    for i, piece in enumerate(pieces):
        sub = forms.plan(forms.Request(request, explicit_form=explicit if explicit not in (None, "document")
                                       else None), (piece.shape,), context)
        first = sub.chosen[0] if sub.chosen else "prose"
        plans.append({"source": piece.source, "chosen": list(sub.chosen), "reason": sub.reason})
        if first in WORDS:
            parts[i] = {"facts": B.facts_sentences(piece), "shown_as": "measured facts in words"}
        else:
            parts[i] = _part(first, piece, request, options, context)
            for b in parts[i].get("blocks", []):
                if b["kind"] == "heading":
                    b["level"] = max(2, b["level"])
    built = B.build_document(pieces, request, options, parts=parts, relations=relations or None)
    return built, plans


def build_candidate(forms_tuple, pieces, request, options, context, explicit, relations=None) -> B.Built:
    first = forms_tuple[0]
    if len(pieces) > 1 and first != "solid.mesh":
        built, plans = compose(pieces, request, options, context, explicit, relations)
        rule = ("several sources: each was planned on its own and placed in one document under its own heading"
                f" ({'; '.join(forms.label_of(tuple(p['chosen'])) for p in plans)})")
        return replace(built, used={**built.used, "plans": plans}, rules=(rule,) + built.rules)
    if len(forms_tuple) == 2 and first not in WORDS:
        # a combination (a chart with its table, a diagram with sentences): both in one document when both fit
        a = B.build(first, pieces, request, options)
        second = forms_tuple[1]
        if a.spec["spec"] in SVG_FORMS and second in ("table", "prose"):
            blocks = [{"kind": "figure", "id": "f1", "caption": B._cut(a.spec["title"]), "figure": a.spec}]
            if second == "table":
                t = B.build("table", pieces, request, options)
                blocks.append({"kind": "table", "id": "t1", "caption": B._cut(t.spec["title"]),
                               "table": dict(t.spec["data"])})
                rules = a.rules + t.rules
            else:
                blocks += [{"kind": "list_item", "text": B._cut(s, 4096)} for s in B.facts_sentences(pieces[0])]
                rules = a.rules
            spec = B._spec("document.html", a.spec["title"], f"{forms.label_of(forms_tuple)[:1].upper()}"
                           f"{forms.label_of(forms_tuple)[1:]}, as the plan combined them.", {"blocks": blocks},
                           pieces, rules=rules)
            return B.Built(spec, {**a.used, "combined_with": second}, rules)
        return replace(a, rules=a.rules + (f"the plan adds {forms.label_of((second,))}, which is not made here",))
    return B.build(first, pieces, request, options)


def _apply_options(spec: dict, options: dict) -> dict:
    if not options:
        return spec
    spec = json.loads(json.dumps(spec))
    spec["options"] = {**spec.get("options", {}), **{k: v for k, v in options.items() if k not in ("measure",)}}
    return spec


# ---------------------------------------------------------------------------------------------------------------
# Generate, render, evaluate, repair


def _carried(verdict) -> float | None:
    found = [f for f in verdict.technical if f.rule in CARRIED_RULES]
    if not found:
        return None
    shares = []
    for f in found:
        if f.rule == "values_recovered" and f.measure is not None and 0 <= f.measure <= 1:
            shares.append(float(f.measure) if f.passed or f.measure < 1 else 0.0)
        else:
            shares.append(1.0 if f.passed else 0.0)
    return round(min(shares), 4)


def _verdict_summary(verdict) -> dict:
    return {"ok": bool(verdict.ok), "failed": [f.rule for f in verdict.technical if not f.passed][:12],
            "style_failed": [f.rule for f in verdict.style if not f.passed][:12],
            "carried": _carried(verdict),
            "findings": [{"rule": f.rule, "passed": f.passed, "detail": f.detail[:160]}
                         for f in verdict.technical if not f.passed or f.rule in CARRIED_RULES][:10]}


def generate(spec: dict, limits: Limits):
    from . import make
    return make.make(spec, limits)


def render(made) -> dict:
    from .store import extension_for
    return {"media_type": made.media_type, "bytes": len(made.bytes), "extension": extension_for(made.media_type),
            "sha256": hashlib.sha256(made.bytes).hexdigest()}


def evaluate(made):
    from . import check
    return check.check(made.bytes, made.media_type, made.spec)


def _g2_repair(value: dict, problem) -> dict | None:
    from .make import _g2
    return _g2.repair(value, problem)


def make_checked(spec: dict, limits: Limits, run: _Run) -> dict:
    """generate -> render -> evaluate -> repair, at most three repair rounds. Returns {"made", "verdict", "spec",
    "rounds", "first_verdict", "refusal", "seconds"}; "made" is None when nothing could be made."""
    from .make.repair import MAX_ROUNDS
    rounds, current, best, first, refusal = [], spec, None, None, None
    started = time.monotonic()
    for round_no in range(MAX_ROUNDS + 1):
        try:
            made = run.step("generate", generate, current, limits,
                            summary_in={"form": current["spec"], "round": round_no},
                            summary_out=lambda m: {"media_type": m.media_type, "bytes": len(m.bytes)})
        except Refused as error:
            refusal = refusal or {"rule": error.rule, "reason": error.reason, "message": str(error)[:300]}
            fixed = _g2_repair(current, error) if round_no < MAX_ROUNDS else None
            rounds.append({"round": round_no, "made": False, "refused": error.rule, "why": str(error)[:200],
                           "repair": (fixed or {}).get("options", {}).get("history", [None])[-1] if fixed else None})
            if fixed is None:
                break
            current = fixed
            continue
        except ArtifactError as error:
            rounds.append({"round": round_no, "made": False, "refused": error.reason, "why": str(error)[:200]})
            refusal = refusal or {"rule": error.reason, "reason": error.reason, "message": str(error)[:300]}
            break
        shown = run.step("render", render, made, summary_in={"round": round_no}, summary_out=lambda r: r)
        verdict = run.step("evaluate", evaluate, made, summary_in={"round": round_no},
                           summary_out=lambda v: {"ok": v.ok, "failed": [f.rule for f in v.technical
                                                                         if not f.passed][:8],
                                                  "carried": _carried(v)})
        first = first or verdict
        summary = _verdict_summary(verdict)
        rounds.append({"round": round_no, "made": True, "bytes": shown["bytes"], **summary})
        key = (verdict.ok, -len(summary["failed"]), -len(summary["style_failed"]))
        if best is None or key > best[0]:
            best = (key, made, verdict, current)
        if verdict.ok and not summary["style_failed"]:
            break
        if round_no >= MAX_ROUNDS:
            break
        nxt = run.step("repair", _repair, current, verdict, summary_in={"round": round_no},
                       summary_out=lambda s: {"repaired": s is not None,
                                              "change": _last_change(s) if s is not None else None})
        if nxt is None:
            break
        rounds[-1]["repair"] = _last_change(nxt)
        current = nxt
    out = {"rounds": rounds, "first_verdict": first, "refusal": refusal,
           "seconds": round(time.monotonic() - started, 4)}
    if best is None:
        return out | {"made": None, "verdict": None, "spec": current}
    return out | {"made": best[1], "verdict": best[2], "spec": best[3]}


def _repair(spec: dict, verdict):
    from . import make
    nxt = make.repair(spec, verdict)
    if nxt is None:
        nxt = _g2_repair(spec, verdict)
    return nxt


def _last_change(spec: dict):
    history = spec.get("options", {}).get("history") or []
    if not history:
        return None
    last = history[-1]
    return last.get("change") if isinstance(last, dict) else str(last)


# ---------------------------------------------------------------------------------------------------------------
# Foresight against outcome


def _factor(predicted: float, observed: float) -> str:
    if predicted <= 0 or observed <= 0:
        return "right" if predicted == observed else ("under-predicted" if observed > predicted else "over-predicted")
    ratio = observed / predicted
    return "right" if 0.5 <= ratio <= 2.0 else ("under-predicted" if ratio > 2.0 else "over-predicted")


def foresight_pairs(foreseen: dict, outcome: dict, context, seconds: float, made_form: str | None = None) -> list[dict]:
    """Each prediction of `forms.foresee` beside what the made bytes and the independent check showed.
    Numbers: right within a factor of two. Problems: right, false alarm (predicted, not found) or missed (found,
    not predicted); only problems some check here can observe are paired."""
    pairs = []
    est = foreseen.get("estimates") or {}
    if outcome.get("bytes") is not None and "bytes" in est:
        pairs.append({"what": "bytes", "predicted": est["bytes"], "observed": outcome["bytes"],
                      "outcome": _factor(float(est["bytes"]), float(outcome["bytes"]))})
    if "seconds" in est:
        pairs.append({"what": "seconds", "predicted": est["seconds"], "observed": round(seconds, 4),
                      "outcome": _factor(float(est["seconds"]), float(seconds))})
    if outcome.get("bytes") is not None and "bytes" in est:
        p_over = est["bytes"] > context.budget_bytes or est.get("seconds", 0) > context.budget_s
        o_over = outcome["bytes"] > context.budget_bytes or seconds > context.budget_s
        pairs.append({"what": "over_budget", "predicted": bool(p_over), "observed": bool(o_over),
                      "outcome": _problem_outcome(p_over, o_over)})
    predicted = set(foreseen.get("codes") or [])
    failed = set(outcome.get("failed_first") or ())
    for code, (rules, made_forms) in OBSERVABLE.items():
        observable = made_form in made_forms
        if code in predicted and not observable:
            pairs.append({"what": code, "predicted": True, "observed": None, "outcome": "not observable here"})
            continue
        if not observable:
            continue
        found = any(r in failed for r in rules)
        if code in predicted or found:
            pairs.append({"what": code, "predicted": code in predicted, "observed": found,
                          "outcome": _problem_outcome(code in predicted, found)})
    for code in sorted(predicted - set(OBSERVABLE)):
        pairs.append({"what": code, "predicted": True, "observed": None, "outcome": "not observable here"})
    return pairs


def _problem_outcome(predicted: bool, found: bool) -> str:
    if predicted and found:
        return "right"
    if not predicted and not found:
        return "right"
    return "false alarm" if predicted else "missed"


def foresee(planner_form, pieces, context) -> dict:
    if planner_form not in forms.FORMS or not pieces:
        return {"form": planner_form, "problems": [], "codes": [], "estimates": {}, "label": "estimate"}
    shape = forms.merge(tuple(p.shape for p in pieces))
    seen = forms.foresee(planner_form, shape, context)
    return {"form": planner_form, "problems": [p.text for p in seen.problems][:8],
            "codes": [p.code for p in seen.problems], "fixes": [p.fix for p in seen.problems if p.fix][:8],
            "estimates": {k: float(v) for k, v in seen.estimates.items()}, "label": seen.label}


# ---------------------------------------------------------------------------------------------------------------
# The record


def _plan_summary(plan) -> dict:
    admitted = [c for c in plan.candidates if c.excluded is None]
    excluded = [c for c in plan.candidates if c.excluded is not None and len(c.forms) == 1]
    return {"chosen": list(plan.chosen), "decided_by": plan.decided_by, "reason": plan.reason,
            "purpose": plan.purpose, "notes": list(plan.notes)[:6], "explicit": list(plan.explicit or []) or None,
            "explicit_honoured": plan.explicit_honoured,
            "considered": [{"forms": list(c.forms), "total": round(c.total, 3),
                            "scores": {k: round(v, 2) for k, v in c.scores.items()}} for c in admitted[:8]],
            "admitted": len(admitted),
            "excluded": [{"forms": list(c.forms), "why": c.excluded[:140]} for c in excluded[:8]],
            "n_excluded": len(excluded), "label": plan.label}


def _envelope(spec: dict) -> dict:
    data = canonical_json(spec.get("data", {}))
    return {"form": spec["spec"], "title": spec.get("title", "")[:200], "description": spec.get("description",
                                                                                                "")[:400],
            "options": {k: v for k, v in spec.get("options", {}).items() if k != "history"},
            "data_sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(), "data_bytes": len(data)}


def bound_record(record: dict, limit: int = MAX_RECORD_BYTES) -> dict:
    """The record within `limit` bytes of JSON: step summaries go first, then candidates' rounds, then the score
    sheet is cut; what was cut is said."""
    def size(r):
        return len(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    r = json.loads(json.dumps(record, allow_nan=False))
    cuts = []
    if size(r) > limit:
        for s in r.get("steps", []):
            s.pop("in", None)
            s.pop("out", None)
        cuts.append("step inputs and outputs")
    if size(r) > limit:
        for c in r.get("candidates", []):
            for rd in c.get("rounds", []):
                rd.pop("findings", None)
        cuts.append("findings of each round")
    if size(r) > limit:
        plan = r.get("plan", {})
        for c in plan.get("considered", []):
            c.pop("scores", None)
        plan["excluded"] = plan.get("excluded", [])[:3]
        cuts.append("the criterion scores")
    if size(r) > limit:
        for c in r.get("candidates", []):
            c.pop("rules", None)
            c.pop("used", None)
        cuts.append("builders' rules")
    while size(r) > limit and r.get("steps"):
        r["steps"] = r["steps"][: len(r["steps"]) // 2]
        cuts.append("later steps")
    if size(r) > limit:
        r = {k: r[k] for k in ("record", "label", "asked", "outcome", "made", "check", "learning") if k in r}
        cuts.append("everything but the outcome")
    if cuts:
        r["cut"] = list(dict.fromkeys(cuts))
    return r


# ---------------------------------------------------------------------------------------------------------------
# Finalize


def _simulate(db, foreseen: dict, pairs: list, made_form: str, now: float):
    """The foresight and its outcome as one SIMULATION record in the imagined store (NOT_EVIDENCE): the payload holds
    the predictions (numbers and booleans, a form name), the outcome slot what was observed. No content is in it.
    Returns (record id or None, why not)."""
    from .. import provenance
    from ..instance import imagined
    if not imagined.present(db):
        return None, "this individual has no imagined store"
    payload, observed = {"form": made_form, "planner_form": foreseen.get("form")}, {}
    for pair in pairs:
        key = pair["what"]
        if isinstance(pair["predicted"], (int, float)) and not isinstance(pair["predicted"], bool):
            payload[key] = float(pair["predicted"])
        else:
            payload[key] = bool(pair["predicted"])
        if pair["observed"] is not None:
            observed[key] = pair["observed"] if isinstance(pair["observed"], bool) else float(pair["observed"])
    try:
        rid = imagined.record(db, provenance.SIMULATION, payload=payload,
                              assumptions=["the predictions are arithmetic from counts (forms.foresee), made before "
                                           "anything was made",
                                           "the observation is what the made bytes and the independent check showed"],
                              uncertainty="estimates from counts, not measurements; compared with one observation",
                              mechanism=MECHANISM, mechanism_version=MECHANISM_VERSION,
                              question="Did the pre-production check foresee what making this form showed?", now=now)
        imagined.observe(db, rid, observation=observed, now=now)
    except Exception as error:         # the imagined store refusing (a limit of 0, paused writes) is not fatal
        return None, f"not kept: {type(error).__name__}: {str(error)[:120]}"
    return rid, None


def _purge_simulation(db, rid, now):
    if db is None or rid is None:
        return
    from . import store
    try:
        store.discard_simulation(db, rid, now)
    except Exception:
        pass


def _forget_stored(db, ids, now):
    from . import store
    for sid in ids:
        try:
            store.forget_artifact(db, sid, now)
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------------------------
# The pipeline


def produce(db, request: str, sources, *, form: str | None = None, context: forms.Context | None = None,
            limits: Limits | None = None, options: dict | None = None, alternatives: int = 1, keep: bool = False,
            now: float | None = None) -> Production:
    """Make, check and keep the result the request and the content call for (the module doc). `sources`: stored
    artifact ids, or `Given` files (stored only when something is made). `db=None` runs every step but Finalize
    (nothing is stored; the record says so): the benchmark's driver uses it."""
    from . import store
    if not isinstance(request, str):
        raise Malformed("the request is text")
    request = request[:2000]
    sources = list(sources)
    if not sources:
        raise Malformed("give at least one file or artifact id to make something from")
    if len(sources) > MAX_SOURCES:
        raise Malformed(f"at most {MAX_SOURCES} sources")
    alternatives = max(1, min(MAX_ALTERNATIVES, int(alternatives)))
    context = context or forms.Context()
    limits = limits or (store.limits(_config(db)) if db is not None else Limits())
    options = dict(options or {})
    now = time.time() if now is None else now
    run = _Run(request, context, limits, time.monotonic() + context.budget_s)
    record = {"record": RECORD, "label": LABEL,
              "asked": {"request": request, "form": form, "options": options, "device": context.device,
                        "budget": {"seconds": context.budget_s, "bytes": context.budget_bytes},
                        "alternatives": alternatives}}
    pieces, notes = run.step("interpret", functools.partial(interpret, deadline=run.deadline), db, sources, limits,
                             summary_in={"sources": len(sources)},
                             summary_out=lambda r: {"pieces": len(r[0]), "unused": sum(1 for n in r[1]
                                                                                        if not n["used"])})
    related, applied = None, []
    if len(pieces) >= 2:
        data_of = _bytes_of(db, sources, pieces)
        pieces, applied = transfer(pieces, data_of, notes)
        related = run.step("relate", relate_sources, pieces, data_of, db,
                           summary_in={"pieces": len(pieces)},
                           summary_out=lambda r: {"compared": r["compared"], "of": r["of"],
                                                  "answers": [p["answer"] for p in r["pairs"]]})
    pieces = narrow(pieces, request, notes)
    record["sources"] = notes
    if related is not None:
        record["relations"] = _relation_record(related, applied)
    if not pieces:
        record["outcome"] = "nothing to make"
        record["why"] = "none of the sources could be read as content: " + "; ".join(
            f"{n['label']}: {n['why']}" for n in notes if not n["used"])[:600]
        record["steps"] = run.steps
        return Production("cannot", bound_record(record))
    plan, direct, learned = run.step("conceptualize", conceptualize, request, pieces, context, form, db,
                                     summary_in={"shapes": [_shape(p.shape) for p in pieces][:4], "form": form},
                                     summary_out=lambda r: {"chosen": list(r[0].chosen),
                                                            "decided_by": r[0].decided_by, "direct": r[1]})
    record["plan"] = _plan_summary(plan)
    learning = _learning_entry(db, learned) if learned is not None else None
    if learning is not None:
        record["learning"] = learning
        if learning.get("decided"):
            record["plan"]["learned"] = learning["decided"]
    explicit, _ = _resolve_form(form)
    if direct is None and (not plan.chosen or plan.chosen[0] in WORDS):
        # proportion: words, nothing made, nothing stored
        words = []
        for piece in pieces:
            words += [f"{piece.label}: {s}" for s in B.facts_sentences(piece)]
        words += relation_words(related)
        record["outcome"] = "words"
        record["why"] = (plan.reason + " No picture or file was made, and nothing was stored.")
        record["steps"] = run.steps
        return Production("words", bound_record(record), words=tuple(words))
    explored = run.step("explore", explore, plan, alternatives if direct is None else 1,
                        summary_in={"alternatives": alternatives}, summary_out=lambda r: {"candidates": r})
    tried = run.step("choose", choose, plan, explored, direct, summary_out=lambda r: {"order": [c["forms"] for c in r]})
    make_limits = replace(_within(limits, run.deadline), max_bytes=min(limits.max_bytes, context.budget_bytes))
    stored_sources: list[str] = []
    sim_id = None
    try:
        if db is not None and any(isinstance(s, Given) for s in sources):
            pieces, stored_sources = _store_given(db, sources, pieces, record, now)
        candidates = []
        for cand in tried:
            fs = tuple(cand["forms"])
            entry = {"forms": list(fs), "form": _generator_form(fs[0]), "total": cand["total"]}
            if cand.get("fallback"):
                entry["fallback"] = ("the planned form has no builder here: this is the next form the planner "
                                     "admitted that has one")
            seen = foresee(fs[0], pieces, context) if direct is None else {"form": fs[0], "codes": [],
                                                                           "problems": [], "estimates": {}}
            entry["foreseen"] = seen
            try:
                built = run.step("plan", build_candidate, fs, pieces, request, options, context, explicit,
                                 relation_blocks(related),
                                 summary_in={"forms": list(fs)},
                                 summary_out=lambda b: {"spec": b.spec["spec"], "used": b.used})
            except B.CannotBuild as why:
                entry["cannot"] = str(why)[:300]
                candidates.append(entry)
                continue
            spec = _apply_options(built.spec, options)
            used = dict(built.used)
            if "plans" in used:
                entry["piece_plans"] = [{"source": p["source"], "chosen": p["chosen"], "reason": p["reason"][:240]}
                                        for p in used.pop("plans")][:8]
            entry.update({"spec_form": spec["spec"], "used": _small(used), "rules": list(built.rules)[:8]})
            result = make_checked(spec, make_limits, run)
            entry["rounds"] = result["rounds"]
            entry["_result"] = result
            if result["made"] is not None:
                v = result["verdict"]
                entry["verdict"] = _verdict_summary(v)
                first = result["first_verdict"]
                observed = {"bytes": len(result["made"].bytes),
                            "failed_first": [f.rule for f in list(first.technical) + list(first.style)
                                             if not f.passed] + ([result["refusal"]["rule"]]
                                                                 if result["refusal"] else [])}
                entry["foresight"] = foresight_pairs(seen, observed, context, result["seconds"], spec["spec"])
                entry["final_spec"] = _envelope(result["spec"])
            else:
                entry["verdict"] = None
                entry["cannot"] = (result["refusal"] or {}).get("message", "nothing could be made")
                entry["foresight"] = foresight_pairs(seen, {"failed_first": [(result["refusal"] or {}).get("rule")]},
                                                     context, result["seconds"], spec["spec"])
            candidates.append(entry)
        winner, rule_words = converge(candidates)
        record["candidates"] = [{k: v for k, v in c.items() if k != "_result"} for c in candidates]
        record["converge"] = {"rule": rule_words, "winner": winner}
        if winner is None:
            record["outcome"] = "could not make this well"
            record["why"] = _failure_words(candidates)
            nxt = [c.forms for c in forms.alternatives(plan, 4) if list(c.forms) not in [x["forms"] for x in tried]]
            record["next"] = {"forms": list(nxt[0]), "label": forms.label_of(nxt[0])} if nxt else None
            record["steps"] = run.steps
            if stored_sources:
                _forget_stored(db, stored_sources, now)
                record["sources_not_kept"] = len(stored_sources)
            return Production("failed", bound_record(record))
        best = candidates[winner]
        result = best["_result"]
        made, spec = result["made"], Spec.from_dict(result["made"].spec.to_dict())
        record["made"] = {"form": spec.spec, "planner_form": best["forms"][0], "media_type": made.media_type,
                          "bytes": len(made.bytes), "sha256": hashlib.sha256(made.bytes).hexdigest(),
                          "title": spec.title, "notes": [str(n)[:200] for n in made.notes[:6]]}
        record["check"] = best["verdict"]
        record["foresight"] = best.get("foresight", [])
        record["outcome"] = "made"
        if db is None:
            record["finalize"] = {"stored": False, "why": "no individual was given: nothing is stored"}
            record["steps"] = run.steps
            return Production("made", bound_record(record), made=made, spec=spec)
        sim_id, sim_why = _simulate(db, best["foreseen"], record["foresight"], spec.spec, now)
        record["simulation"] = sim_id or sim_why
        if learning is not None:
            # said in the record before it is stored; the row itself is written once the artifact exists
            learning["form"] = "+".join(best["forms"])
        source_ids = [p.source for p in pieces]
        record["steps"] = run.steps + [{"step": "finalize", "seconds": 0.0,
                                        "in": {"sources": len(source_ids)}, "out": {"kept": True}}]
        bounded = bound_record(record)
        out = run.step("finalize", store.add_made, db, made, spec, _source_ids(spec, source_ids), now=now,
                       production=bounded)
        if learning is not None:
            _record_check(db, out.id, learning, best["forms"], bool(best["verdict"]["ok"]), now)
        if related is not None:
            _record_links(db, pieces, related, now)
        kept = []
        if keep:
            for i, c in enumerate(candidates):
                r = c.get("_result")
                if i == winner or not r or r["made"] is None or not r["verdict"].ok:
                    continue
                s = Spec.from_dict(r["made"].spec.to_dict())
                lose = {"record": RECORD, "label": LABEL, "outcome": "kept alternative", "winner": out.id,
                        "asked": record["asked"], "made": {"form": s.spec, "planner_form": c["forms"][0]},
                        "check": c["verdict"]}
                if learning is not None:
                    lose["learning"] = {**learning, "form": "+".join(c["forms"])}
                    lose["learning"].pop("decided", None)
                kept.append(store.add_made(db, r["made"], s, _source_ids(s, source_ids), now=now,
                                           production=bound_record(lose)).id)
                if learning is not None:
                    _record_check(db, kept[-1], learning, c["forms"], True, now)
        return Production("made", bounded, artifact=out.id, made=made, spec=spec, kept=tuple(kept),
                          stored_sources=tuple(stored_sources))
    except BaseException:
        _purge_simulation(db, sim_id, now)
        if stored_sources:
            _forget_stored(db, stored_sources, now)
        raise


def _config(db):
    from ..instance import store as instance_store
    return instance_store.config(db)


def _small(value, limit=1200):
    text = json.dumps(value, sort_keys=True, default=str)
    return value if len(text) <= limit else {"cut": True, "keys": sorted(value)[:12] if isinstance(value, dict) else []}


def _source_ids(spec: Spec, ids) -> list:
    named = [s for s in spec.sources if s != "request"]
    return list(dict.fromkeys(named + [i for i in ids if i.startswith("artifact-")]))


def _store_given(db, sources, pieces, record, now):
    """Store the given files now that something will be made; the pieces take their artifact ids."""
    from . import store
    ids, mapping = [], {}
    for n, source in enumerate(sources):
        if isinstance(source, Given):
            rec = store.add(db, source.data, name=source.name, now=now)
            ids.append(rec.id)
            mapping[f"given-{n + 1}"] = rec.id
    for note in record.get("sources", []):
        if note.get("id") in mapping:
            note["id"] = mapping[note["id"]]
    return [replace(p, source=mapping.get(p.source, p.source)) for p in pieces], ids


def converge(candidates) -> tuple[int | None, str]:
    rule = ("the technical verdict first (a result whose technical checks failed is never kept), then the share of "
            "the source values read back from the bytes, then the planner's total, then the simpler form")
    ok = [i for i, c in enumerate(candidates) if c.get("verdict") and c["verdict"]["ok"]]
    if not ok:
        return None, rule

    def key(i):
        c = candidates[i]
        carried = c["verdict"].get("carried")
        total = c.get("total")
        complexity = sum(forms.FORMS[f].complexity for f in c["forms"] if f in forms.FORMS)
        return (-(carried if carried is not None else 1.0), -(total if total is not None else 0.0), complexity, i)
    return min(ok, key=key), rule


def _failure_words(candidates) -> str:
    parts = []
    for c in candidates:
        label = forms.label_of(tuple(c["forms"])) if c["forms"][0] in forms.FORMS else c["forms"][0]
        if c.get("cannot") and not c.get("verdict"):
            parts.append(f"{label}: {c['cannot']}")
        elif c.get("verdict"):
            failed = [f for f in c["verdict"]["findings"] if not f["passed"]]
            parts.append(f"{label}: the independent check found " + "; ".join(
                f["detail"] for f in failed[:3]) if failed else f"{label}: the independent check failed")
    return ("Could not make this well. " + " ".join(p.rstrip(".") + "." for p in parts))[:1200]


# ---------------------------------------------------------------------------------------------------------------
# Remake: the stored spec is the artifact's editable source


def parse_options(pairs) -> dict:
    """`k=v` pairs: v read as JSON when it parses (numbers, true/false, lists), else as text."""
    out = {}
    for pair in pairs or ():
        key, sep, value = str(pair).partition("=")
        if not sep or not key.strip():
            raise Malformed(f"an option is key=value, not {pair!r}")
        try:
            out[key.strip()] = json.loads(value)
        except ValueError:
            out[key.strip()] = value
    return out


def remake(db, artifact_id: str, *, form: str | None = None, options: dict | None = None,
           context: forms.Context | None = None, now: float | None = None) -> Production:
    """Make an artifact again from its stored source: with `form`, the whole production runs again on the same
    sources and request with that form; with `options` only, the stored spec's options are edited and the spec is made,
    checked and repaired again. The new artifact is a new made artifact (the old one stays)."""
    from . import store
    record = store.get(db, artifact_id)
    if record.origin != "made":
        raise Malformed("only an artifact the system made has a specification to remake")
    old = store.spec_of(db, artifact_id) or {}
    production = record.index.get("production") or {}
    sources = [s for s in old.get("sources", []) if s != "request"]
    request = (production.get("asked") or {}).get("request", "")
    now = time.time() if now is None else now
    if form is not None:
        result = produce(db, request, sources, form=form, context=context, options=options, now=now)
        result.record["remade_from"] = artifact_id
        return result
    if not options:
        raise Malformed("say what to change: --form F or --option key=value")
    context = context or forms.Context()
    run = _Run(request, context, store.limits(_config(db)), time.monotonic() + context.budget_s)
    spec = _apply_options(old, options)
    spec.setdefault("options", {})
    result = make_checked(spec, replace(run.limits, max_bytes=min(run.limits.max_bytes, context.budget_bytes)), run)
    rec = {"record": RECORD, "label": LABEL, "outcome": "made", "remade_from": artifact_id,
           "asked": {**(production.get("asked") or {}), "options": options},
           "sources": production.get("sources", []), "plan": production.get("plan"),
           "candidates": [{"forms": [old.get("spec")], "form": old.get("spec"), "rounds": result["rounds"]}]}
    if result["made"] is None or not result["verdict"].ok:
        rec["outcome"] = "could not make this well"
        rec["why"] = ("Could not make this well: " + "; ".join(
            f["detail"] for f in (_verdict_summary(result["verdict"])["findings"] if result["verdict"] else [])
            if not f["passed"])[:800] or (result["refusal"] or {}).get("message", "nothing could be made"))
        rec["steps"] = run.steps
        return Production("failed", bound_record(rec))
    made, new_spec = result["made"], Spec.from_dict(result["made"].spec.to_dict())
    rec["made"] = {"form": new_spec.spec, "media_type": made.media_type, "bytes": len(made.bytes),
                   "sha256": hashlib.sha256(made.bytes).hexdigest(), "title": new_spec.title}
    rec["check"] = _verdict_summary(result["verdict"])
    rec["steps"] = run.steps
    old_learning = production.get("learning") if _learning(db) != "off" else None
    if old_learning and old_learning.get("key") and old_learning.get("form"):
        rec["learning"] = {k: old_learning[k] for k in ("mode", "key", "form") if k in old_learning} | \
            {"mode": _learning(db), "status": "remade"}
    bounded = bound_record(rec)
    out = store.add_made(db, made, new_spec, _source_ids(new_spec, sources), now=now, production=bounded)
    if rec.get("learning"):
        _record_check(db, out.id, rec["learning"], rec["learning"]["form"].split("+"), True, now)
    return Production("made", bounded, artifact=out.id, made=made, spec=new_spec)


# ---------------------------------------------------------------------------------------------------------------
# The "why", in plain words


def why_text(record: dict, *, short: bool = False) -> str:
    """The production record in plain words: what was asked, what the sources were measured to be, which forms were
    considered with their scores, what was foreseen, what was made, what the independent check found, what was
    repaired."""
    if not record:
        return "No production record is kept for this artifact (it was not made by `nsf artifact make`)."
    lines = []
    asked = record.get("asked") or {}
    lines.append(f"Asked: {asked.get('request') or '(no words)'}" +
                 (f" (form named: {asked['form']})" if asked.get("form") else ""))
    if record.get("remade_from"):
        lines.append(f"Remade from {record['remade_from']}.")
    for s in record.get("sources") or []:
        if s.get("used"):
            shape = s.get("shape") or {}
            lines.append(f"Source {s.get('label')}: {s.get('format')}, read {s.get('reading')}; measured as "
                         f"{', '.join(shape.get('kinds', []))} with {shape.get('items')} items"
                         + (f" in {shape.get('series')} series" if shape.get("series") else "") + ".")
        else:
            lines.append(f"Source {s.get('label')} was not used: {s.get('why')}.")
    plan = record.get("plan") or {}
    if plan:
        lines.append(f"Planned: {plan.get('reason')}")
        if plan.get("learned"):
            lines.append(f"Learned: {plan['learned']}")
        if not short:
            for c in plan.get("considered", [])[:6]:
                scores = c.get("scores") or {}
                detail = ", ".join(f"{k} {scores[k]:.2f}" for k in forms.CRITERIA if k in scores)
                lines.append(f"  considered {forms.label_of(tuple(c['forms'])) if all(f in forms.FORMS for f in c['forms']) else c['forms']}"
                             f": {c['total']:.2f}" + (f" ({detail})" if detail else ""))
            for c in plan.get("excluded", [])[:4]:
                lines.append(f"  not possible: {', '.join(c['forms'])}: {c['why']}")
    for pair in (record.get("relations") or {}).get("pairs", []):
        lines.append(f"Related {pair.get('first')} and {pair.get('second')}: {_verdict_words(pair)}.")
    for unit in (record.get("relations") or {}).get("units", [])[: (2 if short else 16)]:
        lines.append(f"  unit of {unit['column']} in {unit['table']}: {unit['unit']}, as {unit['from']} states it")
    learning = record.get("learning")
    if learning and not short:
        lines.append(f"Form learning ({learning.get('mode')}): {learning.get('words') or learning.get('status')}")
    for c in record.get("candidates") or []:
        seen = c.get("foreseen") or {}
        label = forms.label_of(tuple(c["forms"])) if all(f in forms.FORMS for f in c["forms"]) else c["forms"][0]
        if seen.get("problems") or seen.get("estimates"):
            est = seen.get("estimates") or {}
            lines.append(f"Foreseen for {label}: " + ("; ".join(seen.get("problems") or []) or "no problem") +
                         (f" (estimate: {est.get('bytes', 0):.0f} bytes, {est.get('seconds', 0):.2f} s)"
                          if est else ""))
        for sub in c.get("piece_plans") or []:
            name = next((s.get("label") for s in record.get("sources") or [] if s.get("id") == sub["source"]),
                        sub["source"])
            lines.append(f"  planned for {name} on its own: {sub['reason']}")
        if c.get("cannot") and not c.get("verdict"):
            lines.append(f"Could not make {label}: {c['cannot']}")
        for rule in (c.get("rules") or [])[: (2 if short else 8)]:
            lines.append(f"  left out or chosen by rule: {rule}")
        for rd in c.get("rounds") or []:
            if rd.get("repair"):
                lines.append(f"  repaired after round {rd['round']}: {rd['repair']}")
            elif rd.get("refused"):
                lines.append(f"  round {rd['round']} refused: {rd.get('why')}")
    made = record.get("made")
    if made:
        lines.append(f"Made: {made.get('title')} ({made.get('form')}, {made.get('media_type')}, "
                     f"{made.get('bytes')} bytes); made by the system, not evidence.")
    check = record.get("check")
    if check:
        carried = check.get("carried")
        lines.append("Independent check: " + ("passed" if check.get("ok") else "FAILED") +
                     (f"; {carried:.0%} of the source values were read back from the bytes" if carried is not None
                      else "") + (f"; failed: {', '.join(check['failed'])}" if check.get("failed") else "") +
                     (f"; style notes: {', '.join(check['style_failed'])}" if check.get("style_failed") else ""))
    if record.get("foresight") and not short:
        lines.append("Foreseen against found: " + "; ".join(
            f"{p['what']} {p['outcome']}" for p in record["foresight"]))
    if record.get("outcome") in ("words", "could not make this well", "nothing to make"):
        lines.append(record.get("why", ""))
        if record.get("next"):
            lines.append(f"The next form to try: {record['next']['label']} (`--form {record['next']['forms'][0]}`).")
    if record.get("cut"):
        lines.append(f"(The record was shortened to fit: {', '.join(record['cut'])} left out.)")
    lines.append("This record was made by the system; it is not evidence.")
    return "\n".join(line for line in lines if line)


__all__ = ["Given", "LABEL", "Production", "ProductionStopped", "RECORD", "bound_record", "converge", "foresight_pairs",
           "interpret", "parse_options", "produce", "remake", "why_text"]
