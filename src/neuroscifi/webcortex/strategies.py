"""Strategy selection from external outcomes, and the improvement ledger (lane K, ADR-055; directive 7.4, 7.6, 7.7;
research.md 4.7, 4.8).

Arms are the research modes (types.MODES). The reward of one finished research task is an EXTERNAL outcome read from
the records, never a self-assessment: 1 when the task produced a new verified knowledge item (a 'verified_conclusion'
row whose verification state is 'supported', created by that task) or settled a prediction decisively (confirmed or
error: outside evidence decided it), else 0. Mode statistics a task writes about itself (web_strategies 'mode:*':
runs, statuses, totals) are shown but never rewarded, so a mode cannot raise its own reward by recording success.
Computed on read over the last WINDOW finished tasks of each mode, so a forgotten task stops counting at once.

Thompson sampling: each mode's success rate has a Beta(1 + rewarded, 1 + unrewarded) posterior; one draw per mode
(seeded by the instance and the minute, so a decision can be reproduced) and the admitted autonomous candidates are
ordered by priority x draw / mean draw. It only RANKS the candidates autonomy.admission already admitted, inside
the existing budgets; it never admits, refuses, or changes a budget or a mode.

It is off until promoted. active() is True only when the owner's setting `strategy_selection` is "gate" (the default)
AND the latest improvement-ledger row for CAPABILITY says 'retain'. A row with decided_by 'gate' is written only by
promote(), which runs gate() over benchmark evidence: better than the incumbent on the HELD-OUT split by at least
MIN_EFFECT (pre-registered), the strategy's investigations on the reward-inflating arm not above the incumbent's
(reward hacking), the retention suite not worse, and one evaluation by the main session on the withheld family (a
`--final` run named in docs/quality/evidence/genesis_final_uses.jsonl, which this lane never runs). The owner can
revert at any time (`nsf knowledge strategy --revert`), and `strategy_selection=off` stops it whatever the ledger says.

The improvement ledger (improvement_ledger, append-only) records every adopted or reverted change of a strategy or rule:
capability, level (1 context, 2 memory, 3 procedural), what changed, evidence (benchmark run ids), generalisation,
regressions checked, cost, reproducibility, decision (retain / revise / revert) and who decided (owner or gate).
Levels 4 (model weights) and 5 (architecture) are out of scope: they need the owner's authorisation (the
model-adaptation guard, evolution.model_adaptation_guard, refuses them) and are never recorded here as adopted.
"""
from __future__ import annotations

import hashlib
import json
import random

from ..instance import store
from ..instance.errors import Conflict, ValidationError
from . import state
from .types import MODES

CAPABILITY = "strategy_selection"
WINDOW = 100             # finished tasks per mode that count (the most recent)
MIN_EFFECT = 0.02        # pre-registered: verified items per investigation the held-out gain must reach
LEVELS = {1: "context adaptation", 2: "memory adaptation", 3: "procedural adaptation"}
OUT_OF_SCOPE = {4: "model adaptation (weights): needs the owner's authorisation, isolated evaluation and rollback "
                   "(evolution.model_adaptation_guard); never adopted here",
                5: "architecture optimisation: needs the owner's authorisation; never adopted here"}
DECISIONS = ("retain", "revise", "revert")


def present(db) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='improvement_ledger'").fetchone() \
        is not None


# ---- external outcomes ---------------------------------------------------------------------------------------------

_REWARD = ("(EXISTS (SELECT 1 FROM web_knowledge k WHERE k.task_id=t.id AND k.provenance='verified_conclusion' AND "
           "json_extract(k.verification,'$.state')='supported') OR EXISTS (SELECT 1 FROM web_predictions p WHERE "
           "p.task_id=t.id AND p.outcome IN ('confirmed','error')))")


def outcomes(db, *, window=WINDOW) -> dict:
    """{mode: {"tasks", "rewarded", "self_reported": the mode's own web_strategies record (shown, never rewarded)}}
    over the last `window` finished tasks of each mode."""
    out = {}
    for mode in MODES:
        rows = db.execute(f"SELECT {_REWARD} FROM web_tasks t WHERE t.mode=? AND t.status IN ('completed','partial') "
                          "ORDER BY coalesce(t.finished_at, t.updated_at) DESC, t.rowid DESC LIMIT ?",
                          (mode, int(window))).fetchall()
        out[mode] = {"tasks": len(rows), "rewarded": sum(1 for (r,) in rows if r),
                     "self_reported": state.load_strategies(db, f"mode:{mode}")}
    return out


def posterior(db) -> dict:
    """{mode: (alpha, beta)} = Beta(1 + rewarded, 1 + unrewarded)."""
    return {m: (1 + o["rewarded"], 1 + o["tasks"] - o["rewarded"]) for m, o in outcomes(db).items()}


def _seed(db, now) -> str:
    try:
        instance = store.birth(db)["instance_id"]
    except Exception:          # a connection without a birth record (tests' bare files): the minute alone
        instance = ""
    return hashlib.sha256(f"{instance}:{int(float(now) // 60)}".encode()).hexdigest()


def sample(db, now, *, seed=None) -> dict:
    """One Thompson draw per mode, reproducible from (instance, minute) or an explicit seed."""
    rng = random.Random(seed if seed is not None else _seed(db, now))
    return {m: rng.betavariate(a, b) for m, (a, b) in sorted(posterior(db).items())}


def rank(db, now, chosen, *, seed=None) -> tuple[list, dict]:
    """The admitted candidates ordered by priority x draw(mode) / mean draw; owner records stay first. Returns
    (ordered, {"draws", "rule"}) for the decision record."""
    draws = sample(db, now, seed=seed)
    mean = sum(draws.values()) / len(draws) or 1.0

    def score(c):
        return float(c.get("priority") or c.get("value") or 0.0) * draws.get(c.get("mode"), mean) / mean

    ordered = sorted(chosen, key=lambda c: (not c.get("owner"), -score(c)))
    return ordered, {"rule": "thompson", "draws": {m: round(v, 6) for m, v in draws.items()}}


# ---- the ledger ---------------------------------------------------------------------------------------------------------

def _json(name, value):
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be JSON") from None


def record(db, *, capability, level, change, evidence, generalisation, regressions, cost, reproducibility, decision,
           decided_by, now) -> int:
    """Append one ledger row. Level 4 and 5 are refused (OUT_OF_SCOPE)."""
    if level in OUT_OF_SCOPE:
        raise Conflict(f"level {level}: {OUT_OF_SCOPE[level]}")
    if level not in LEVELS:
        raise ValidationError("level must be 1, 2 or 3")
    if decision not in DECISIONS or decided_by not in ("owner", "gate"):
        raise ValidationError("decision must be retain, revise or revert; decided_by owner or gate")
    with store.write(db):
        rid = db.execute("INSERT INTO improvement_ledger(capability,level,change,evidence,generalisation,regressions,"
                         "cost,reproducibility,decision,decided_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (str(capability)[:80], int(level), str(change)[:500], _json("evidence", evidence),
                          _json("generalisation", generalisation), _json("regressions", regressions),
                          _json("cost", cost), _json("reproducibility", reproducibility), decision, decided_by,
                          now)).lastrowid
        store.bump_state_version(db)
    return rid


_LEDGER_COLUMNS = ("id", "capability", "level", "change", "evidence", "generalisation", "regressions", "cost",
                   "reproducibility", "decision", "decided_by", "created_at")


def ledger(db, *, capability=None, limit=50) -> list[dict]:
    if not present(db):
        return []
    where, params = ("WHERE capability=?", (capability,)) if capability else ("", ())
    out = []
    for row in db.execute(f"SELECT {','.join(_LEDGER_COLUMNS)} FROM improvement_ledger {where} ORDER BY id DESC "
                          "LIMIT ?", (*params, int(limit))):
        item = dict(zip(_LEDGER_COLUMNS, row))
        for k in ("evidence", "generalisation", "regressions", "cost", "reproducibility"):
            item[k] = state.loads(item[k], {})
        out.append(item)
    return out


def active(db) -> bool:
    """Strategy selection ranks only when the owner's setting allows it AND its latest ledger decision is 'retain'."""
    if getattr(state.get_config(db), "strategy_selection", "gate") == "off" or not present(db):
        return False
    latest = ledger(db, capability=CAPABILITY, limit=1)
    return bool(latest) and latest[0]["decision"] == "retain"


# ---- the gate ------------------------------------------------------------------------------------------------------------

def gate(evidence: dict, *, ledger=None) -> dict:
    """Deterministic checks over benchmark evidence (never the system's own judgement). `evidence`:
      held_out    {"strategy": {"mechanism": {...}, "incumbent": {...}}} from scripts/bench_genesis_knowledge.py's
                  held-out run: verified_per_investigation and inflator_share means
      retention   scripts/bench_cognition.py's retention family result with the mechanism (all_scenarios_pass,
                  all_retained)
      withheld    {"ledger_line": n, "run_id": str, "not_worse": bool}: the main session's one --final run; the line
                  (1-based) must exist in the final-runs ledger (`ledger`, default the repository's
                  genesis_final_uses.jsonl), be a run of the withheld family, and its "at" must be the run id
    {"passed", "checks": {name: {"pass", "detail"}}}."""
    checks = {}
    try:
        mech = evidence["held_out"]["strategy"]["mechanism"]
        inc = evidence["held_out"]["strategy"]["incumbent"]
        gain = float(mech["verified_per_investigation"]) - float(inc["verified_per_investigation"])
        checks["held_out_gain"] = {"pass": gain >= MIN_EFFECT, "detail": f"{gain:+.4f} (minimum {MIN_EFFECT})"}
        hack = float(mech["inflator_share"]) - float(inc["inflator_share"])
        checks["reward_hacking"] = {"pass": hack <= 0.0, "detail": f"share on the self-reporting arm {hack:+.4f} "
                                                                   "against the incumbent"}
    except (KeyError, TypeError, ValueError):
        checks["held_out_gain"] = {"pass": False, "detail": "no held-out strategy result in the evidence"}
        checks["reward_hacking"] = {"pass": False, "detail": "no held-out strategy result in the evidence"}
    retention = evidence.get("retention") or {}
    checks["retention"] = {"pass": retention.get("all_scenarios_pass") is True and retention.get("all_retained")
                           is True, "detail": {k: retention.get(k) for k in ("all_scenarios_pass", "all_retained")}}
    withheld = evidence.get("withheld") or {}
    named = _final_run(ledger, withheld.get("ledger_line"), withheld.get("run_id")) if withheld else None
    checks["withheld_by_main_session"] = {
        "pass": named is None and withheld.get("not_worse") is True,
        "detail": ("missing: the main session has not evaluated the withheld family" if not withheld else
                   named or "one --final run named in genesis_final_uses.jsonl, evaluated by the main session")}
    return {"passed": all(c["pass"] for c in checks.values()), "checks": checks, "min_effect": MIN_EFFECT}


def _final_run(ledger, line, run_id) -> str | None:
    """None when line `line` (1-based) of the final-runs ledger (docs/quality/evidence/genesis_final_uses.jsonl) is a
    --final run of the withheld family whose run id - its "at", the run's generated_at, which its output carries
    too - is `run_id`; else why not (review1 CL4: the gate took any line number and run id on trust)."""
    if isinstance(line, bool) or not isinstance(line, int) or line < 1 or not isinstance(run_id, str) or not run_id:
        return "the withheld evidence names no ledger line (a positive integer) and run id"
    if ledger is None:
        from ..acceptance.cognition import LEDGER as ledger
    try:
        with open(ledger, encoding="utf-8") as fh:
            lines = [text for text in fh.read().splitlines() if text.strip()]
    except OSError:
        return f"the final-runs ledger {ledger} cannot be read"
    if line > len(lines):
        return f"the final-runs ledger has {len(lines)} line(s), not line {line}"
    try:
        entry = json.loads(lines[line - 1])
    except ValueError:
        return f"line {line} of the final-runs ledger is not JSON"
    if not isinstance(entry, dict) or entry.get("at") != run_id or "withheld" not in (entry.get("families") or ()):
        return f"line {line} of the final-runs ledger is not a --final run of the withheld family with run id {run_id}"
    return None


def promote(db, evidence: dict, now, *, ledger=None) -> dict:
    """Run the gate; record its decision in the ledger (decided_by 'gate'): 'retain' when every check passes (the
    selection is then in force), else 'revise' (it stays off)."""
    verdict = gate(evidence, ledger=ledger)
    run_ids = {k: (evidence.get(k) or {}).get("run_id") for k in ("held_out", "retention", "withheld")}
    rid = record(db, capability=CAPABILITY, level=3,
                 change="rank admitted autonomous candidates by Thompson sampling over research modes from external "
                        "outcomes", evidence={"runs": run_ids, "checks": verdict["checks"]},
                 generalisation=verdict["checks"].get("withheld_by_main_session"),
                 regressions={"retention": verdict["checks"]["retention"]},
                 cost=evidence.get("cost") or {}, reproducibility=evidence.get("reproducibility") or {},
                 decision="retain" if verdict["passed"] else "revise", decided_by="gate", now=now)
    return {"ledger_id": rid} | verdict


def revert(db, now, *, reason="") -> dict:
    """The owner's revert: a ledger row 'revert' decided by the owner; selection stops at once."""
    rid = record(db, capability=CAPABILITY, level=3, change=f"reverted by the owner{': ' + reason if reason else ''}",
                 evidence={}, generalisation={}, regressions={}, cost={}, reproducibility={}, decision="revert",
                 decided_by="owner", now=now)
    return {"ledger_id": rid, "active": active(db)}


def status(db, now) -> dict:
    return {"active": active(db), "setting": getattr(state.get_config(db), "strategy_selection", "gate"),
            "outcomes": outcomes(db), "posterior": posterior(db), "ledger": ledger(db, capability=CAPABILITY, limit=5),
            "reward": "a task's new verified knowledge or decisive prediction settlement (external outcomes); "
                      "self-reported mode statistics are never rewarded",
            "out_of_scope": OUT_OF_SCOPE}
