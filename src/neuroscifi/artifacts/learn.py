"""Learning the form of a result from outcomes (OMNIMIND Wave O-1 lane L, ADR-078; DESIGN §5 "Learning").

What is and is not learned (directive §10.4):
- Stored examples: the made artifacts and their specifications (artifacts/store.py). Nothing here copies them.
- Procedural policy: per key (content kind, purpose, device), a tally of the owner's reactions to the forms the system
  made, turned into preferences handed to the frozen planner (`forms.plan`, through `Context.preferences`). It is
  computed on read from the outcome rows (schema 21, `form_outcomes`); nothing else is stored.
- No model weight is updated anywhere, and no model is called.

Signals come only from outside the system: the owner's reaction to a made artifact (`nsf artifact feedback ID
kept | dismissed | replaced:FORM`; the latest reaction to an artifact is the one that counts) and the independent
check's verdict on its bytes when it was made. The planner's own scores are never a reward.

The rule is a per-key majority (ADR-078 says why it is this, and not a learner of the planner's weights: measured on
the benchmark's SIMULATED people, the weight seam moves almost no choice, and no learner tried beat this rule):
  tally per key and form (the lead form of a combination): +1 kept; -1 dismissed; -1 replaced and +1 the form named;
  -1 a failed check; a passed check counts nothing (that a form could be made says nothing about whether it was
  wanted). Evidence of a key: 1 per reaction, 2 per replacement (it says what was wrong AND what was wanted), 1 per
  failed check. Below MIN_EVIDENCE the key is "not enough yet" and nothing changes. Otherwise each form with a
  positive tally becomes a preference tally / highest tally, in (0, 1].
What it cannot reach, by construction: a learned value is never 0 or below, so nothing learned removes a form (a
preference of -1 is the planner's hard exclusion, and only the owner states one); the planner follows a preference only
among the forms it admitted, after its hard constraints (device, accessibility, feasibility, budget) and after its
proportion rules (a scalar or a sentence's worth stays words; a wish for a short answer is words). The owner's own
stated preferences win: when the context states any positive preference, nothing learned is added, and nothing learned
is added for a form whose name, family or medium the owner named.

Setting `form_learning`: "off" (default) records nothing and applies nothing, so every output is as before;
"record" keeps outcomes and applies nothing; "apply" also hands the learned preferences to the planner.
Reversible: `nsf config --form-learning off` stops it on the next call; `nsf artifact feedback --reset` deletes every
outcome (one deletion_log row, replayed by every restore and rewrite, as Wave 4's own-text forget). Forget: a row goes
with its artifact, whatever forgot it (schema 21's trigger). Scopes: the owner's only. Quiet: nothing here runs unasked.
"""
from __future__ import annotations

import dataclasses
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from . import forms
from .types import SHAPE_KINDS, Malformed

MODES = ("off", "record", "apply")
OWNER_OUTCOMES = ("kept", "dismissed", "replaced")
CHECK_OUTCOMES = ("check_passed", "check_failed")
OUTCOMES = OWNER_OUTCOMES + CHECK_OUTCOMES
MIN_EVIDENCE = 2                 # a key acts from this much evidence (a replacement counts 2): one reaction is not enough
MAX_OUTCOMES = 100_000           # the table is bounded: the oldest rows go first
DELETION_KIND = "form_outcomes"  # deletion_log.target_kind of `nsf artifact feedback --reset`
LABEL = ("learned from your reactions to what was made: a procedural policy (a tally per kind of content, purpose and "
         "device), not evidence; no model weight is changed anywhere")
WHAT_IS_LEARNED = {
    "stored_examples": "the made artifacts and their specifications, kept by the artifact store; not copied here",
    "procedural_policy": "per (content kind, purpose, device): a tally of your reactions, turned into preferences "
                         "the form planner follows among the forms it allows",
    "model_weights": "none: no model weight is updated anywhere, and no model is called",
}


def _key(kind, purpose, device) -> tuple[str, str, str]:
    if kind not in SHAPE_KINDS:
        raise Malformed(f"unknown content kind {kind!r}")
    if purpose not in forms.PURPOSES:
        raise Malformed(f"unknown purpose {purpose!r}")
    if device not in forms.DEVICES:
        raise Malformed(f"unknown device {device!r}")
    return kind, purpose, device


def _form(form) -> str:
    parts = str(form).split("+") if isinstance(form, str) else []
    if not 1 <= len(parts) <= 2 or not all(p in forms.FORMS for p in parts):
        raise Malformed(f"unknown form {form!r} (a form planner name, or two joined with '+')")
    return form


# ---------------------------------------------------------------------------------------------------------------
# The rule, over rows held anywhere (the instance file, or a benchmark driver's memory)


@dataclass(frozen=True)
class KeyPolicy:
    """What was observed for one key and what it changes."""

    key: tuple[str, str, str]
    evidence: int
    observed: Mapping[str, int]          # outcome -> count
    tally: Mapping[str, int]             # form -> tally
    preferences: Mapping[str, float]     # form -> value in (0, 1]; empty while not enough or nothing positive

    @property
    def enough(self) -> bool:
        return self.evidence >= MIN_EVIDENCE

    def words(self) -> str:
        kind, purpose, device = self.key
        seen = ", ".join(f"{n} {o.replace('_', ' ')}" for o, n in sorted(self.observed.items())) or "nothing"
        head = f"{kind} for {forms.PURPOSE_PHRASE.get(purpose, purpose)} on the {device}: observed {seen}"
        if not self.enough:
            return f"{head}; not enough yet ({self.evidence} of {MIN_EVIDENCE}), so nothing changes"
        if not self.preferences:
            return f"{head}; no form was kept more than it was turned down, so nothing changes"
        order = sorted(self.preferences.items(), key=lambda kv: (-kv[1], kv[0]))
        named = ", then ".join(forms.FORMS[f].label for f, _ in order)
        return f"{head}; prefers {named}, where the planner allows it"


def _preferences(tally: Mapping[str, int], evidence: int) -> dict[str, float]:
    if evidence < MIN_EVIDENCE:
        return {}
    top = max(tally.values(), default=0)
    if top <= 0:
        return {}
    return {f: round(v / top, 4) for f, v in sorted(tally.items()) if v > 0}


class Tally:
    """The per-key majority over outcome rows. `add` one row (or `count` equal rows); `policy(key)` the result."""

    def __init__(self):
        self._keys: dict[tuple, dict] = {}

    def add(self, kind, purpose, device, form, outcome, replaced_by=None, count=1) -> None:
        key = _key(kind, purpose, device)
        if outcome not in OUTCOMES:
            raise Malformed(f"unknown outcome {outcome!r}")
        lead = _form(form).split("+")[0]
        entry = self._keys.setdefault(key, {"tally": Counter(), "evidence": 0, "observed": Counter()})
        entry["observed"][outcome] += count
        if outcome == "check_passed":
            entry["tally"][lead] += 0
            return
        entry["tally"][lead] += count if outcome == "kept" else -count
        entry["evidence"] += count
        if outcome == "replaced":
            if replaced_by not in forms.FORMS:
                raise Malformed(f"unknown form {replaced_by!r}")
            entry["tally"][replaced_by] += count
            entry["evidence"] += count

    def keys(self) -> list[tuple[str, str, str]]:
        return sorted(self._keys)

    def policy(self, key) -> KeyPolicy:
        entry = self._keys.get(tuple(key))
        if entry is None:
            return KeyPolicy(tuple(key), 0, MappingProxyType({}), MappingProxyType({}), MappingProxyType({}))
        tally = {f: v for f, v in sorted(entry["tally"].items())}
        return KeyPolicy(tuple(key), entry["evidence"], MappingProxyType(dict(sorted(entry["observed"].items()))),
                         MappingProxyType(tally), MappingProxyType(_preferences(tally, entry["evidence"])))


@dataclass(frozen=True)
class Learned:
    """What `apply` hands the planner for one key: `preferences` to merge into the context (`context()`) and
    `adjustments` for `forms.plan(..., adjustments=)` (None: the planner's weights are not learned, ADR-078)."""

    key: tuple[str, str, str]
    status: str          # "off" | "recording" | "not_enough" | "nothing_learned" | "applied"
    preferences: Mapping[str, float]
    words: str
    adjustments: Mapping | None = None

    @classmethod
    def of(cls, policy: KeyPolicy, mode: str) -> Learned:
        if mode == "off":
            return cls(policy.key, "off", MappingProxyType({}), "form learning is off: nothing is recorded or used")
        if mode == "record":
            return cls(policy.key, "recording", MappingProxyType({}),
                       "your reactions are recorded; nothing learned is used until form learning is set to apply")
        if not policy.enough:
            return cls(policy.key, "not_enough", MappingProxyType({}), policy.words())
        if not policy.preferences:
            return cls(policy.key, "nothing_learned", MappingProxyType({}), policy.words())
        return cls(policy.key, "applied", policy.preferences, policy.words())

    def context(self, context: forms.Context) -> forms.Context:
        """`context` with the learned preferences added. The owner's stated preferences win: with any positive one
        stated, nothing is added; a form whose name, family or medium the owner named gets nothing learned."""
        stated = dict(context.preferences)
        if not self.preferences or any(v > 0 for v in stated.values()):
            return context
        added = {name: value for name, value in self.preferences.items()
                 if name in forms.FORMS and not any(k in stated for k in (name, forms.FORMS[name].family,
                                                                          *forms.FORMS[name].media))}
        if not added:
            return context
        return dataclasses.replace(context, preferences={**added, **stated})


def key_of(plan: forms.FormPlan, content, context: forms.Context) -> tuple[str, str, str]:
    """The key a plan was made under: the content's leading kind, the plan's main purpose, the device."""
    if not isinstance(content, (tuple, list)):
        content = (content,)
    return forms.primary_kind(forms.merge(tuple(content))), plan.purpose, context.device


def plan_with(lookup: Callable[[tuple], Learned], request: forms.Request, content=(), context=None, *,
              adjustments: Mapping | None = None) -> tuple[forms.FormPlan, Learned]:
    """Plan, and plan again with what was learned for the plan's key when there is something to apply. `lookup(key)`
    returns the Learned for a key (`adjustments_for` over the instance file, or a driver's own Tally)."""
    context = context or forms.Context()
    first = forms.plan(request, content, context, adjustments=adjustments)
    learned = lookup(key_of(first, content, context))
    merged = learned.context(context)
    if merged is context and learned.adjustments is None:
        return first, learned
    final = forms.plan(request, content, merged, adjustments=learned.adjustments or adjustments)
    if final.chosen and final.chosen != first.chosen:
        # The planner's sentence would credit "the preferences you have stated": these were learned, so say that.
        kind, _, device = learned.key
        label = forms.label_of(final.chosen)
        final = dataclasses.replace(final, reason=(
            f"{label[:1].upper()}{label[1:]}, because that is what you kept or asked for before with {kind} on the "
            f"{device} (learned from your reactions; `nsf form learned` shows it)."),
            notes=(*final.notes, f"without what was learned: {forms.label_of(first.chosen)}"))
    return final, learned


# ---------------------------------------------------------------------------------------------------------------
# The instance file


def _viewer(viewer):
    from .store import StoreRefused
    if viewer != "owner":
        raise StoreRefused("what was learned from your reactions is yours: no family or agent scope may read or "
                           "change it", rule="scope")


def present(db) -> bool:
    """Whether this file has the outcome table (schema 21)."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='form_outcomes'").fetchone() is not None


def mode(db) -> str:
    from ..instance import store
    return store.config(db).form_learning


def _made_key(db, artifact_id):
    return db.execute("SELECT kind, purpose, device, form FROM form_outcomes WHERE artifact_id=? ORDER BY "
                      "source='check' DESC, id LIMIT 1", (artifact_id,)).fetchone()


def record_outcome(db, artifact_id, outcome, *, by=None, kind=None, purpose=None, device=None, form=None, now=None,
                   viewer="owner") -> dict:
    """Record one outcome of a made artifact (nothing while form_learning is "off").

    A check verdict ("check_passed" / "check_failed", the production pipeline at make time) gives the key and the
    form; an owner reaction ("kept", "dismissed", "replaced" with `by`) takes them from the artifact's earlier row
    when they are not given. Returns {"recorded": bool, "why": plain words, ...}."""
    from ..instance import store
    from .store import NotHeld, _ID
    _viewer(viewer)
    if outcome not in OUTCOMES:
        raise Malformed(f"the outcome is one of {', '.join(OUTCOMES)}")
    if (outcome == "replaced") != (by is not None):
        raise Malformed("a replacement names the form wanted instead (replaced:FORM), and only a replacement does")
    if by is not None and by not in forms.FORMS:
        raise Malformed(f"unknown form {by!r}; the forms are: {', '.join(forms.FORMS)}")
    artifact_id = str(artifact_id)
    if not _ID.fullmatch(artifact_id):
        raise Malformed("an artifact id is 'artifact-' and 32 hexadecimal digits")
    setting = mode(db)
    if setting == "off":
        return {"recorded": False, "mode": setting, "why": "form learning is off, so nothing was recorded "
                "(`nsf config --form-learning record` keeps your reactions)"}
    if not present(db):
        raise Malformed("this individual's file has no outcome table yet (schema 21)")
    now = time.time() if now is None else float(now)
    with store.write(db):
        row = db.execute("SELECT origin FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise NotHeld("no artifact of that id is held")
        if row[0] != "made":
            raise Malformed("only a form the system made can be kept, dismissed or replaced; this one was given")
        if None in (kind, purpose, device, form):
            made = _made_key(db, artifact_id)
            if made is None:
                raise Malformed("nothing records what this artifact was made for (it was made outside a plan, or "
                                "while form learning was off), so a reaction to it teaches nothing")
            kind, purpose, device, form = (given if given is not None else held
                                           for given, held in zip((kind, purpose, device, form), made))
        kind, purpose, device = _key(kind, purpose, device)
        form = _form(form)
        source = "check" if outcome in CHECK_OUTCOMES else "owner"
        db.execute("INSERT INTO form_outcomes(artifact_id,kind,purpose,device,form,outcome,replaced_by,source,at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (artifact_id, kind, purpose, device, form, outcome, by, source, now))
        db.execute("DELETE FROM form_outcomes WHERE id <= (SELECT id FROM form_outcomes ORDER BY id DESC LIMIT 1 "
                   "OFFSET ?)", (MAX_OUTCOMES,))
    return {"recorded": True, "mode": setting, "key": [kind, purpose, device], "form": form, "outcome": outcome,
            "by": by, "why": "recorded" + ("; it is used when a form is chosen" if setting == "apply" else
                                           "; nothing learned is used until form learning is set to apply")}


# The owner's latest reaction to each artifact, and every check verdict, grouped: the tally's input.
_COUNTED = """WITH latest AS (SELECT max(id) AS id FROM form_outcomes WHERE source='owner' {where} GROUP BY artifact_id)
SELECT kind, purpose, device, form, outcome, replaced_by, count(*) FROM form_outcomes
WHERE (source='check' {where}) OR id IN (SELECT id FROM latest)
GROUP BY kind, purpose, device, form, outcome, replaced_by"""


def tally(db, key=None) -> Tally:
    """The Tally of the rows held (of one key with `key`)."""
    out = Tally()
    if not present(db):
        return out
    where, params = "", ()
    if key is not None:
        where, params = "AND kind=? AND purpose=? AND device=?", tuple(_key(*key))
    for kind, purpose, device, form, outcome, by, n in db.execute(_COUNTED.format(where=where), params * 2):
        out.add(kind, purpose, device, form, outcome, by, n)
    return out


def adjustments_for(db, kind, purpose, device, *, viewer="owner") -> Learned:
    """What `apply` hands the planner for this key (nothing unless form_learning is "apply")."""
    _viewer(viewer)
    key = _key(kind, purpose, device)
    setting = mode(db)
    if setting == "off":
        return Learned.of(KeyPolicy(key, 0, {}, {}, {}), "off")
    return Learned.of(tally(db, key).policy(key), setting)


def plan(db, request: forms.Request, content=(), context=None, *, viewer="owner") -> tuple[forms.FormPlan, Learned]:
    """The production pipeline's entry: the planner's plan with what was learned for its key applied."""
    _viewer(viewer)
    return plan_with(lambda key: adjustments_for(db, *key), request, content, context)


def report(db, *, viewer="owner") -> dict:
    """`nsf form learned`: per key what was observed and what it changes, and what is and is not learned."""
    _viewer(viewer)
    setting = mode(db)
    held = tally(db)
    rows = db.execute("SELECT count(*) FROM form_outcomes").fetchone()[0] if present(db) else 0
    keys = []
    for key in held.keys():
        policy = held.policy(key)
        learned = Learned.of(policy, setting)
        keys.append({"key": list(key), "evidence": policy.evidence, "observed": dict(policy.observed),
                     "tally": dict(policy.tally), "preferences": dict(policy.preferences),
                     "status": learned.status, "words": policy.words()})
    return {"label": LABEL, "mode": setting, "min_evidence": MIN_EVIDENCE, "rows": rows, "max_rows": MAX_OUTCOMES,
            "what_is_learned": dict(WHAT_IS_LEARNED), "keys": keys}


def reset(db, now=None, *, viewer="owner") -> dict:
    """`nsf artifact feedback --reset`: delete every outcome, recorded as a forget is (one deletion_log row with the
    state version it committed at, which every restore and rewrite replays); archives that hold rows become stale."""
    from ..instance import store
    _viewer(viewer)
    now = time.time() if now is None else float(now)
    if not present(db):
        return {"outcomes_removed": 0, "checkpoints_stale": []}
    with store.write(db):
        removed = db.execute("DELETE FROM form_outcomes").rowcount
        stale = [i for (i,) in db.execute("SELECT id FROM checkpoints WHERE status='valid' ORDER BY id")] \
            if removed else []
        db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
        effects = {"outcomes_removed": removed, "checkpoints_stale": stale,
                   "state_version": store.bump_state_version(db)}
        db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                   (DELETION_KIND, "all", now, json.dumps(effects, sort_keys=True)))
    return effects


def read_resets(db) -> list:
    """The live file's resets, for checkpoints._read_forgets: (state version, requested_at, effects JSON)."""
    return [(json.loads(effects).get("state_version", float("inf")), requested_at, effects)
            for requested_at, effects in db.execute(
                "SELECT requested_at, effects FROM deletion_log WHERE target_kind=? ORDER BY id", (DELETION_KIND,))]


def replay(copy, resets, version) -> int:
    """In a restore's or rewrite's copy (checkpoints._replay): when the live file reset its outcomes after the copy was
    written (a reset newer than the copy's state `version`), delete the copy's outcomes too and record the reset there
    (state version + 1), so an older archive restored from the copy loses them as well. Caller owns the transaction.
    Returns the rows removed."""
    newer = [r for r in resets or () if version < r[0]]
    if not newer or not present(copy):
        return 0
    removed = copy.execute("DELETE FROM form_outcomes").rowcount
    for _, requested_at, effects_json in newer:
        if copy.execute("SELECT 1 FROM deletion_log WHERE target_kind=? AND requested_at=?",
                        (DELETION_KIND, requested_at)).fetchone():
            continue
        carried = {k: v for k, v in json.loads(effects_json).items() if k != "checkpoints_stale"} \
            | {"state_version": version + 1, "replayed": True}
        copy.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                     (DELETION_KIND, "all", requested_at, json.dumps(carried, sort_keys=True)))
        copy.execute("UPDATE checkpoints SET status='stale' WHERE status='valid'")
    return removed


# ---------------------------------------------------------------------------------------------------------------
# Gap detection (directive §10.5): a read-only report from records that already exist; nothing acts on it


GAP_IDS = 10                     # evidence ids listed per line
GAP_SPECS = 2000                 # made artifacts whose specification is read for repair histories (newest first)


WORDS_SUFFICE = ("scalar", "prose", "code")   # kinds whose own form is words: answering them in words is no gap


def _carried_elsewhere() -> frozenset:
    """Content kinds some form whose encoding is not words carries fully (fit 1), less those words suffice for."""
    return frozenset(k for f in forms.FORMS.values() if f.encoding != "words"
                     for k, fit in f.kinds.items() if fit >= 1.0 and k not in WORDS_SUFFICE)


def gap_report(db, *, viewer="owner") -> dict:
    """What is missing most, each line with its count and evidence ids (artifact ids, or formats)."""
    from . import store as artifact_store
    _viewer(viewer)
    lines = []
    if not artifact_store.present(db):
        return {"label": "read-only: nothing acts on this report", "lines": lines}
    undecoded = {}
    for fmt, outcome, count in db.execute("SELECT format, outcome, count FROM artifact_gaps ORDER BY count DESC, "
                                          "format, outcome"):
        undecoded.setdefault(fmt, {"count": 0, "outcomes": []})
        undecoded[fmt]["count"] += count
        undecoded[fmt]["outcomes"].append(outcome)
    for fmt, item in sorted(undecoded.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        ids = [i for (i,) in db.execute(
            "SELECT id FROM artifacts WHERE origin='given' AND json_extract(index_json,'$.detected.format')=? AND "
            "(json_extract(index_json,'$.representation.kind')='opaque' OR json_extract(index_json,'$.error.reason') "
            "IN ('unsupported','missing_capability')) ORDER BY seq DESC LIMIT ?", (fmt, GAP_IDS))]
        lines.append({"gap": "not_decoded", "subject": fmt, "count": item["count"], "evidence": ids,
                      "words": f"files of format {fmt} that nothing here decodes: {item['count']} "
                               f"({' and '.join(sorted(set(item['outcomes'])))})"})
    failed = {}
    for aid, maker, rules in db.execute(
            "SELECT id, maker, json_extract(index_json,'$.check.failed') FROM artifacts WHERE origin='made' AND "
            "json_extract(index_json,'$.check.ok') = 0 ORDER BY seq DESC"):
        entry = failed.setdefault(maker, {"count": 0, "ids": [], "rules": Counter()})
        entry["count"] += 1
        if len(entry["ids"]) < GAP_IDS:
            entry["ids"].append(aid)
        try:
            entry["rules"].update(str(r)[:64] for r in json.loads(rules or "[]"))
        except ValueError:
            pass
    for maker, entry in sorted(failed.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        rules = ", ".join(f"{r} {n}" for r, n in entry["rules"].most_common(5)) or "no rule named"
        lines.append({"gap": "check_failed", "subject": maker, "count": entry["count"], "evidence": entry["ids"],
                      "words": f"made by {maker}, the independent check failed {entry['count']} time(s) ({rules})"})
    repairs = {}
    made = [i for (i,) in db.execute("SELECT id FROM artifacts WHERE origin='made' ORDER BY seq DESC LIMIT ?",
                                     (GAP_SPECS,))]
    for aid in made:
        try:
            spec = artifact_store.spec_of(db, aid)
        except (artifact_store.NotHeld, ValueError):
            continue
        history = ((spec or {}).get("options") or {}).get("history") if isinstance(spec, dict) else None
        for step in history if isinstance(history, list) else ():
            rule = str(step.get("rule", ""))[:64] if isinstance(step, dict) else ""
            if rule:
                entry = repairs.setdefault(rule, {"count": 0, "ids": []})
                entry["count"] += 1
                if aid not in entry["ids"] and len(entry["ids"]) < GAP_IDS:
                    entry["ids"].append(aid)
    for rule, entry in sorted(repairs.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        if entry["count"] > 1:
            lines.append({"gap": "repair_recurs", "subject": rule, "count": entry["count"], "evidence": entry["ids"],
                          "words": f"the repair for {rule} was needed {entry['count']} times"})
    lines.append({"gap": "could_not_make_well", "subject": None, "count": None, "evidence": [],
                  "words": "productions that ended 'could not make this well': nothing records them on this branch "
                           "(the production pipeline is where they would be recorded)"})
    if present(db):
        kinds = sorted(_carried_elsewhere())
        marks = ",".join("?" * len(kinds))
        for kind, n, ids in db.execute(
                f"SELECT kind, count(DISTINCT artifact_id), group_concat(DISTINCT artifact_id) FROM form_outcomes "
                f"WHERE source='check' AND form IN ('prose','spoken') AND kind IN ({marks}) GROUP BY kind "
                f"ORDER BY 2 DESC, kind", kinds):
            lines.append({"gap": "words_for_shown_content", "subject": kind, "count": n,
                          "evidence": sorted((ids or "").split(","))[:GAP_IDS],
                          "words": f"answered in words although the content was {kind}, which a picture, table or "
                                   f"other form can carry: {n} time(s)"})
    pairs = db.execute("SELECT src, dst_id FROM artifact_links WHERE relation='contradicts' ORDER BY created_at DESC, "
                       "src, dst_id").fetchall()
    if pairs:
        lines.append({"gap": "relations_inconsistent", "subject": "contradicts", "count": len(pairs),
                      "evidence": [f"{s} {d}" for s, d in pairs[:GAP_IDS]],
                      "words": f"pairs of artifacts found inconsistent with each other: {len(pairs)}"})
    return {"label": "read-only: nothing acts on this report", "lines": lines}
