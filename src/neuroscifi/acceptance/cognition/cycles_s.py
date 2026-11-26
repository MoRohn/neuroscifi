"""Wave 3 lane S (ADR-061): the learning-cycles experiment (cycles.py, played unchanged) with what lane S must count:
what counts as an independent source, what the individual's own text is, and what replaces a held item.

Worlds: lane S's (cycles_splits_s.py, committed alone before any mechanism) and lane F's (cycles_splits.py; its
held-out split was already looked at by Wave 2 and Step 0's checks: disclosed, never a claim alone). The withheld
learning-cycles worlds are never loaded here.

Configurations (each forces only the settings under test over the same base; read back inside every run):
  reference:no-loop     autonomous research off (cycles.NO_LOOP)
  baseline              every Wave 1 setting off (cycles.WAVE1_OFF_WEB), the independence rule before lane S
                        (attribution_independence and echo_recognition off), supersession "ask": Wave 2's baseline
  fix:attribution       baseline + attribution_independence
  fix:echo              baseline + echo_recognition
  fixes                 baseline + both fixes
  shipped               every setting as a new individual has it (nothing forced but enabling the run)
  supersede:K2 / :K3    fixes + supersession "independent", supersession_min_sources 2 / 3
  supersede:K2-no-recency       ... with the rule's recency condition removed (harness ablation, not a setting)
  supersede:K2-no-independence  ... with the rule counting sources (pages), not independent groups (ablation)
  supersede:K2-no-echo          ... with echo_recognition off
Supersession is measured over `fixes` (it must not weaken them): its gain is paired with `fixes`, same world, seed.

Counted per run, beside cycles.Run's own measures (never against anything the individual produced):
  own_admitted          knowledge rows whose supporting pages are all echoes of the individual's own output
  syndicated_admitted   knowledge rows whose supporting pages are all the wire story and its copies
  dependent_admitted    knowledge rows whose supporting pages are all echoes, wire stories or wire copies (any mix)
  wrong_admitted        knowledge rows stating a version not true in the cycle they were written
  honest_merged         per distinct look: honest outlets (honest, lag, rumour pages; copies belong to the outlet
                        they copy) that share an independence group with another true origin: false rejections of
                        independence (claims.independence_groups after the run's stamp_origins)
  copies_missed         per distinct look: extra groups a single true origin spans (the wire and its copies; an
                        outlet and its copies): dependence the rule did not see
  single_origin_admitted  knowledge rows whose supporting pages all come from one true origin (one outlet and its
                        copies, the wire and its copies, the individual's echoes): admitted on one source counted as
                        several (split by whether the value was true then)
  echo_removed / echo_false_honest / echo_false_other   claims consolidation removed as echoes: from echo pages /
                        from honest, lagging or copied pages (false rejections of echo recognition) / from rumour or
                        wire pages that happened to state the individual's own value
  replacements          every supersession (consolidate's summary), classified against the truth of that cycle: true
                        (the new value is true, the old was not), false (the old was true, the new is not), other;
                        by_rule (the supersession rule) or by evidence (the path that existed before lane S);
                        false_persistence: cycles the subject's newest held value stayed wrong after a false one
  missed                contested refreshes of a held item that was not true when the look's honest pages stated the
                        truth (a stale item kept, the owner asked)
  questions             owner questions opened (pending ids in consolidation summaries) and answered by the rule
"""
from __future__ import annotations

from contextlib import ExitStack, closing
import json
import tempfile
import time
from pathlib import Path
from unittest import mock

from ...instance import store
from . import cycles, cycles_splits, cycles_splits_s, scoring

FAMILIES = {"lane-s": cycles_splits_s, "lane-f": cycles_splits}


def _variant_families() -> dict:
    """Wave 3 fix lane (review3 S-2..S-4): each variant of cycles_splits_s_variants.py as a family "variant:<name>"
    (its own splits and seeds; the variant's generator)."""
    from types import SimpleNamespace
    from . import cycles_splits_s_variants as variants
    return {f"variant:{v}": SimpleNamespace(CYCLES=variants.CYCLES, generate=variants.generator(v))
            for v in variants.VARIANTS}


FAMILIES |= _variant_families()
BASE = dict(cycles.WAVE1_OFF_WEB) | {"attribution_independence": False, "echo_recognition": False,
                                     "supersession": "ask"}
FIXES = {"attribution_independence": True, "echo_recognition": True}
RULE = FIXES | {"supersession": "independent"}
CONFIGS = {
    "reference:no-loop": {},
    "baseline": {},
    "fix:attribution": {"attribution_independence": True},
    "fix:echo": {"echo_recognition": True},
    "fixes": FIXES,
    "shipped": None,
    "supersede:K2": RULE | {"supersession_min_sources": 2},
    "supersede:K3": RULE | {"supersession_min_sources": 3},
    "supersede:K2-no-recency": RULE | {"supersession_min_sources": 2},
    "supersede:K2-no-independence": RULE | {"supersession_min_sources": 2},
    "supersede:K2-no-echo": RULE | {"supersession_min_sources": 2, "echo_recognition": False},
}
CONFIGS["fix:own"] = {"own_text_provenance": True}          # Wave 4 lane E (ADR-067): own-text provenance alone
ABLATE = {"supersede:K2-no-recency": "recency", "supersede:K2-no-independence": "independence"}
READ_BACK = cycles.READ_BACK + ("attribution_independence", "echo_recognition", "supersession",
                                "supersession_min_sources")
READ_BACK += ("own_text_provenance",)
HONEST = ("honest", "lag", "rumour")
DEPENDENT = ("echo", "wire", "wire_copy")
COUNTS = ("own_admitted", "syndicated_admitted", "dependent_admitted", "wrong_admitted", "rows_written",
          "single_origin_admitted", "single_origin_admitted_true", "single_origin_admitted_wrong",
          "honest_origins", "honest_merged", "copies_missed", "echo_removed", "echo_false_honest",
          "echo_false_other", "replacements_true",
          "replacements_false", "replacements_other", "by_rule_true", "by_rule_false", "by_rule_other", "missed",
          "questions", "answered_by_rule", "contested")


def settings(name, budget) -> dict:
    """The settings a configuration forces (see the module docstring)."""
    forced = CONFIGS[name]
    base = {} if forced is None else dict(BASE) | forced
    return base | {"enabled": True, "autonomous": name != "reference:no-loop",
                   "daily_autonomous": cycles._allowance(budget)}


def _value(statement_values):
    mm = cycles._major_minor(statement_values)
    return None if mm is None else mm


class Run(cycles.Run):
    def __init__(self, spec, seed, name, db, generate):
        super().__init__(spec, seed, frozenset({cycles.NO_LOOP}) if name == "reference:no-loop" else frozenset(),
                         db, generate)
        self.name = name
        self.counts = dict.fromkeys(COUNTS, 0)
        self.seen_looks = set()
        self.false_at = []            # (subject, cycle) of each false replacement

    def start(self):
        from ...webcortex import autonomy, state
        state.set_config(self.db, settings(self.name, self.spec["budget"]), cycles.T0 - 30 * cycles.DAY)
        if self.name != "reference:no-loop":
            autonomy.enable(self.db, cycles.T0 - 30 * cycles.DAY)
        cfg = state.get_config(self.db)
        self.config = {k: getattr(cfg, k) for k in READ_BACK}
        for name in sorted(n for n, s in self.names.items() if s["born"] == 0):
            self.owner_research(name, cycles.T0 - self.names[name]["age_days"] * cycles.DAY)
        self.score(None)

    # ---- accounting --------------------------------------------------------------------------------------------------
    def _origin_of(self, look, page):
        if page["kind"] in HONEST:
            return "outlet:" + page["host"]
        if page["kind"] == "copy":
            first = next((p for p in look["pages"] if p["kind"] == "honest" and p.get("variant") == "origin"), None)
            return "outlet:" + (first["host"] if first else page["host"])
        return {"echo": "self", "wire": "wire", "wire_copy": "wire"}.get(page["kind"], "other:" + page["host"])

    def _independence(self, name, look, docs, objective):
        from ...webcortex import claims, state
        if (name, self.cycle) in self.seen_looks or not docs:
            return
        self.seen_looks.add((name, self.cycle))
        by_url = {f"http://{p['host']}{p['path']}": p for p in look["pages"]}
        found = [c for d in docs for c in claims.extract_claims(d, objective)]
        cfg = state.get_config(self.db)
        claims.stamp_origins(found, docs, attribution=cfg.attribution_independence, grouping=cfg.origin_grouping)
        origin = {c.id: self._origin_of(look, by_url[c.source_url]) for c in found if c.source_url in by_url}
        groups = [{origin[i] for i in g if i in origin} for g in claims.independence_groups(found)]
        honest = {o for g in groups for o in g if o.startswith("outlet:")}
        self.counts["honest_origins"] += len(honest)
        self.counts["honest_merged"] += sum(1 for o in honest if any(o in g and len(g) > 1 for g in groups))
        for o in {o for g in groups for o in g}:
            if o != "self":
                self.counts["copies_missed"] += max(0, sum(1 for g in groups if o in g) - 1)

    def execute(self, task):
        from ...webcortex import knowledge
        name = self.subject_in(task["objective"])
        look = (self.world["looks"].get((name, self.cycle)) if name else None) or {"kind": "none", "pages": []}
        by_url = {f"http://{p['host']}{p['path']}": p for p in look["pages"]}
        if name:
            self._independence(name, look, self._documents(name, self.now - 600.0)[1], task["objective"])
        out = super().execute(task)
        truth = self.truth(name) if name else None
        for kid in out["provisional"]:
            row = knowledge.row(self.db, kid)
            pages = [by_url[s["url"]] for s in row["sources"] if s.get("url") in by_url]
            kinds = [p["kind"] for p in pages]
            self.counts["rows_written"] += 1
            value = _value(row["claim_values"])
            if value != (1, truth):
                self.counts["wrong_admitted"] += 1
            if len({self._origin_of(look, p) for p in pages}) == 1:
                self.counts["single_origin_admitted"] += 1
                self.counts["single_origin_admitted_true" if value == (1, truth) else
                            "single_origin_admitted_wrong"] += 1
            if kinds and all(k == "echo" for k in kinds):
                self.counts["own_admitted"] += 1
            if kinds and all(k in ("wire", "wire_copy") for k in kinds):
                self.counts["syndicated_admitted"] += 1
            if kinds and all(k in DEPENDENT for k in kinds):
                self.counts["dependent_admitted"] += 1
        for cid in out.get("echoes") or ():
            found = self.db.execute("SELECT source_url FROM web_claims WHERE id=?", (cid,)).fetchone()
            kind = (by_url.get(found[0]) or {}).get("kind") if found else None
            self.counts["echo_removed" if kind == "echo" else
                        "echo_false_honest" if kind in ("honest", "lag", "copy") else "echo_false_other"] += 1
        for entry in out["superseded"]:
            old, new = (_value(knowledge.row(self.db, entry[k])["claim_values"]) for k in ("old", "new"))
            kind = ("true" if new == (1, truth) and old != (1, truth) else
                    "false" if old == (1, truth) and new != (1, truth) else "other")
            self.counts[f"replacements_{kind}"] += 1
            if entry.get("by") == "rule":
                self.counts[f"by_rule_{kind}"] += 1
            if kind == "false":
                self.false_at.append((name, self.cycle))
        honest_truth = any(p["kind"] == "honest" and f"1.{truth}.0" in p["text"] for p in look["pages"]) \
            if name else False
        for entry in out["contested"]:
            self.counts["contested"] += 1
            if entry.get("knowledge_id") is None:
                continue
            row = knowledge.row(self.db, entry["knowledge_id"])
            if row and row["status"] in ("provisional", "accepted", "stale") and \
                    _value(row["claim_values"]) != (1, truth) and honest_truth:
                self.counts["missed"] += 1
        self.counts["questions"] += len(out.get("pending") or ())
        self.counts["answered_by_rule"] += len(out.get("answered_by_rule") or ())
        return out

    def summary(self) -> dict:
        out = super().summary()
        # review3 S-5: each false replacement's stretch runs until the subject is held right again (a cycle with
        # nothing held no longer ends it; such cycles are counted apart), and a stretch the run ended inside is
        # censored: its length is a lower bound. The per-seed maximum is what is reported, never a mean of maxima.
        stretches = [{"subject": name, "cycle": c} | cycles.false_persistence(self, name, c)
                     for name, c in self.false_at]
        persistence = [s["cycles"] for s in stretches]
        out["s"] = dict(self.counts) | {
            "false_persistence_mean": scoring.spread(persistence)["mean"] if persistence else None,
            "false_persistence_max": max(persistence) if persistence else 0,
            "false_censored": sum(1 for s in stretches if s["censored"]),
            "false_stretches": stretches}
        out["config"] = dict(self.config)
        return out


def _pages_as_sources(found, *, origins=None):
    """independence_groups with independence off: every page its own source."""
    groups = {}
    for c in found:
        groups.setdefault(c.source_url, []).append(c.id)
    return list(groups.values())


def _ablated(decide, ablation):
    """The supersession rule's decision (consolidate._rule_replacement) with one condition removed, and only there:
    "recency" (temporal.supersedes always true) or "independence" (pages counted as sources, also by the
    verified-item bar the rule applies). Harness ablations, never settings."""
    def rule(*a, **k):
        from ...webcortex import claims, consolidate
        with ExitStack() as stack:
            if ablation == "recency":
                stack.enter_context(mock.patch.object(consolidate, "_supersedes", lambda *a, **k: True))
            if ablation == "independence":
                stack.enter_context(mock.patch.object(claims, "independence_groups", _pages_as_sources))
            return decide(*a, **k)
    return rule


def run_world(spec, seed, name, *, generate, cycles_=None) -> dict:
    from ...webcortex import consolidate
    started = time.perf_counter()
    spec = dict(spec) | ({"cycles": int(cycles_)} if cycles_ is not None else {})
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-s-") as directory:
        db = store.create(Path(directory) / "s.sqlite", cycles.T0 - 31 * cycles.DAY, note="lane S: supersession")
        with closing(db):
            ref = [None]
            with ExitStack() as stack:
                stack.enter_context(cycles._environment(ref))
                if ABLATE.get(name):
                    stack.enter_context(mock.patch.object(consolidate, "_rule_replacement",
                                                          _ablated(consolidate._rule_replacement, ABLATE[name])))
                run = Run(spec, seed, name, db, generate)
                ref[0] = run
                run.start()
                for _ in range(spec["cycles"]):
                    run.step()
                out = run.summary()
    out["wall_seconds"] = time.perf_counter() - started
    return out


SCALARS = tuple(k for k in cycles.SCALARS if k not in ("changed_launch_cycles", "changed_held_cycles",
                                                       "order_changed_launch_cycles", "order_changed_held_cycles")) + \
    tuple(f"s.{k}" for k in COUNTS) + ("s.false_persistence_mean", "s.false_persistence_max", "s.false_censored")


def census(family, split, *, seeds=None) -> dict:
    """What the independence rule makes of EVERY look of the split's worlds (not only the looks a run happened to
    make), with attribution_independence off and on: the claims of each look extracted (the question "What is the
    latest <name> release?"), origins stamped, grouped (claims.independence_groups), and each group compared with
    the pages' true origins (an outlet; its copies belong to it; the wire and its copies; the individual's echoes).
      honest_origins / honest_merged   independent outlets, and those sharing a group with another true origin
                                       (false rejections of independence)
      wire_looks / wire_split          looks with the wire story or its copies, and those whose wire pages form more
                                       than one group (dependence missed)
      origin_looks / origin_split      one_origin looks, and those whose outlet and copies form more than one group
      echo_split                       looks whose echo pages form more than one group (the independence rule never
                                       joins echoes; echo recognition, in consolidation, is what removes them)
    No individual and no product state: the extraction and grouping functions only."""
    from ...webcortex import claims
    from ...webcortex.types import Document
    module = FAMILIES[family]
    spec = module.CYCLES[split]
    seeds = tuple(seeds or spec["seeds"])
    keys = ("looks", "honest_origins", "honest_merged", "wire_looks", "wire_split", "origin_looks", "origin_split",
            "echo_looks", "echo_split")
    out = {}
    for attribution in (False, True):
        totals = dict.fromkeys(keys, 0)
        for world in spec["worlds"]:
            for seed in seeds:
                generated = module.generate(world, seed)
                for (name, c), look in sorted(generated["looks"].items()):
                    if not look["pages"]:
                        continue
                    docs, origin = [], {}
                    for p in look["pages"]:
                        url = f"http://{p['host']}{p['path']}"
                        docs.append(Document(url=url, final_url=url, canonical_url=url, domain=p["host"], title=name,
                                             text=p["text"], retrieved_at=cycles.T0, retrieval_mode="live",
                                             published_at=cycles.T0 + p["published_cycle"] * DAY_S))
                    found = [x for d in docs for x in claims.extract_claims(d, f"What is the latest {name} release?")]
                    claims.stamp_origins(found, docs, attribution=attribution)
                    by_url = {f"http://{p['host']}{p['path']}": p for p in look["pages"]}
                    for x in found:
                        origin[x.id] = Run._origin_of(None, look, by_url[x.source_url])
                    groups = [{origin[i] for i in g} for g in claims.independence_groups(found)]
                    totals["looks"] += 1
                    honest = {o for g in groups for o in g if o.startswith("outlet:")}
                    totals["honest_origins"] += len(honest)
                    totals["honest_merged"] += sum(1 for o in honest if any(o in g and len(g) > 1 for g in groups))
                    for flag, origin_key in (("wire", "wire"), ("echo", "self")):
                        spans = sum(1 for g in groups if origin_key in g)
                        if spans:
                            totals[f"{flag}_looks"] += 1
                            totals[f"{flag}_split"] += spans > 1
                    if look["kind"] == "one_origin":
                        first = next(p for p in look["pages"] if p.get("variant", "origin") == "origin")
                        spans = sum(1 for g in groups if "outlet:" + first["host"] in g)
                        totals["origin_looks"] += 1
                        totals["origin_split"] += spans > 1
        out["attribution_on" if attribution else "attribution_off"] = totals
    return {"family": family, "split": split, "seeds": list(seeds), "census": out}


DAY_S = 86_400.0


def run_split(family, split, *, configs=None, seeds=None, cycles_=None) -> dict:
    module = FAMILIES[family]
    if split not in ("development", "held_out"):
        raise ValueError("lane S runs the development and held_out splits only")
    spec = module.CYCLES[split]
    configs = list(configs or CONFIGS)
    seeds = tuple(seeds or spec["seeds"])
    raw = {(name, world["name"]): [run_world(world, seed, name, generate=module.generate, cycles_=cycles_)
                                   for seed in seeds] for name in configs for world in spec["worlds"]}
    for (name, world), rows in raw.items():
        for ref in ("baseline", "fixes"):
            base = raw.get((ref, world))
            for i, r in enumerate(rows):
                r[f"changed_vs_{ref}"] = dict(zip(("launch_cycles", "held_cycles"), cycles._changed(r, base[i]))) \
                    if base else None
    out = {}
    for name in configs:
        per_world = {}
        for world in spec["worlds"]:
            rows = raw[(name, world["name"])]
            per_world[world["name"]] = {
                "seeds": list(seeds),
                "per_seed": [{k: cycles._get(r, k) for k in SCALARS} | {
                    "changed_vs_baseline": r["changed_vs_baseline"], "changed_vs_fixes": r["changed_vs_fixes"],
                    "false_stretches": r["s"]["false_stretches"]} for r in rows],
                "spread": {k: scoring.spread([cycles._get(r, k) for r in rows]) for k in SCALARS},
                "config_read_back": rows[0]["config"],
                "config_same_every_seed": all(r["config"] == rows[0]["config"] for r in rows),
                "history": [r["history"] for r in rows]}
        out[name] = {"settings": CONFIGS[name], "ablation": ABLATE.get(name), "worlds": per_world}
    refs = cycles._run_references(split, spec, module.generate, seeds=seeds)
    digests = {w["name"]: {str(s): cycles.world_digest(module.generate(w, s)) for s in seeds} for w in spec["worlds"]}
    changes = {}
    for name in (n for n in ITEM_DIFF if n in configs and "baseline" in configs):
        changes[name] = {w["name"]: [item_changes(module.generate(w, s), b["history"], r["history"])
                                     for s, b, r in zip(seeds, raw[("baseline", w["name"])], raw[(name, w["name"])])]
                         for w in spec["worlds"]}
    return {"family": family, "split": split, "seeds": list(seeds), "configurations": out, "references": refs,
            "world_digests": digests, "item_changes_vs_baseline": changes}


# review3 S-6: every (subject, cycle) whose newest held version differs from the baseline's run of the same world and
# seed, with the truth, both values and the class (true / wrong / none on each side), per configuration
ITEM_DIFF = ("fix:attribution", "fix:echo", "fixes")


def _state(value, truth):
    return "none" if value is None else "true" if value == truth else "wrong"


def item_changes(world, base_history, history) -> dict:
    """{"items": [{subject, cycle, truth, baseline, value, class}], "classes": {class: count}} (see ITEM_DIFF)."""
    truth = {s["name"]: s["truth"] for s in world["subjects"]}
    base = {(n, c): v for n, h in base_history.items() for c, v in h}
    mine = {(n, c): v for n, h in history.items() for c, v in h}
    items = []
    for key in sorted(set(base) | set(mine)):
        if base.get(key) != mine.get(key):
            name, c = key
            t = truth[name].get(c)
            items.append({"subject": name, "cycle": c, "truth": t, "baseline": base.get(key), "value": mine.get(key),
                          "class": f"{_state(base.get(key), t)}->{_state(mine.get(key), t)}"})
    classes = {}
    for it in items:
        classes[it["class"]] = classes.get(it["class"], 0) + 1
    return {"items": items, "classes": classes}


# ---- the verdicts, written before the held-out run --------------------------------------------------------------------
#
# PRIMARY = mean over cycles 1..N of `correct` (cycles.PRIMARY). Per world the gain of a configuration is the mean over
# seeds of its paired difference with its reference configuration (same world, same seed); the smallest gain the rule
# can see is the reference's sd of PRIMARY over seeds.
# The fixes (reference: baseline), shipped ON only when, on every split and family run:
#   (s1) safety: own_admitted, syndicated_admitted and one_origin_rises are 0 in every world and seed, and
#        dependent_admitted is not above the baseline's in any world and seed;
#   (s2) retention: retention.lost + retention.regressed not above the baseline's, every world and seed;
#   (s3) correctness: no world loses more than the baseline's spread (a loss beyond it is a cost to report and blocks
#        "on"), and every paired difference that is not 0 is explained run by run (changed_held cycles listed);
#   (s4) false rejections: honest_merged and echo_false are reported beside, with their denominators.
# Supersession (reference: fixes; it ships OFF whatever the result):
#   gain      as cycles' rule (a): the gain > the fixes' spread in every held-out world of lane S's family
#   ablation  removing recency or independence makes it worse in at least one world, or the verdict says so
#   safety    (s1) and (s2) hold; false replacements reported with their spread, never netted against true ones
# The verdict word: "gain" (gain everywhere, safety held), "no gain" (else, safety held), "unsafe" (safety broken).
# K, chosen on DEVELOPMENT before any held-out run (lane S development, seeds 241-243): K = 2. K = 3 made 0.33
# replacements per run in each development world (not exercised); K = 2 made 3.3 and 3.7 rule replacements per run,
# of which 0 and 1.0 false (sd-mixed, sd-wrong-newer). Both are run on held-out; the verdict is K = 2's.
PRIMARY = cycles.PRIMARY
K = 2
SUPERSEDE = f"supersede:K{K}"


def paired(result, config, reference, world, metric=PRIMARY):
    mine = result["configurations"][config]["worlds"][world]["per_seed"]
    ref = result["configurations"][reference]["worlds"][world]["per_seed"]
    return [m[metric] - r[metric] for m, r in zip(mine, ref)]


def verdict(result, config, reference) -> dict:
    worlds = list(result["configurations"][reference]["worlds"])
    per_world = {}
    safe = retention = True
    for w in worlds:
        diffs = paired(result, config, reference, w)
        sd = result["configurations"][reference]["worlds"][w]["spread"][PRIMARY]["sd"] or 0.0
        gain = sum(diffs) / len(diffs)
        mine = result["configurations"][config]["worlds"][w]["per_seed"]
        ref = result["configurations"][reference]["worlds"][w]["per_seed"]
        s1 = all(r["s.own_admitted"] == 0 and r["s.syndicated_admitted"] == 0 and r["one_origin_rises"] == 0 and
                 r["s.dependent_admitted"] <= b["s.dependent_admitted"] for r, b in zip(mine, ref))
        s2 = all((r["retention.lost"] or 0) + (r["retention.regressed"] or 0) <=
                 (b["retention.lost"] or 0) + (b["retention.regressed"] or 0) for r, b in zip(mine, ref))
        safe &= s1
        retention &= s2
        per_world[w] = {"gain": gain, "reference_sd": sd, "beats": gain > sd, "loses": gain < -sd, "paired": diffs,
                        "safety": s1, "retention": s2,
                        "false_replacements": scoring.spread([r["s.replacements_false"] for r in mine]),
                        "true_replacements": scoring.spread([r["s.replacements_true"] for r in mine])}
    gain = all(per_world[w]["beats"] for w in worlds)
    word = "unsafe" if not (safe and retention) else "gain" if gain else "no gain"
    return {"config": config, "reference": reference, "verdict": word, "safety": safe, "retention": retention,
            "any_world_loses": any(per_world[w]["loses"] for w in worlds), "per_world": per_world}


def roadmap_verdict(result, config, reference) -> dict:
    """The roadmap's rule for a default (IMPLEMENTATION_ROADMAP §2, as Wave 2's COMMON states it), applied to one
    configuration against its reference on one split: ON only with a gain beyond the reference's spread in every
    world, retention (lost + regressed) not above the reference's in any world and seed, and no adversarial count
    (own, syndicated, dependent admitted, one-origin rises) above it in any world and seed. The ablation is the
    reference itself (the fix off). "on" or "off"."""
    worlds = list(result["configurations"][reference]["worlds"])
    per_world, gain_all, retention, safety = {}, True, True, True
    keys = ("s.own_admitted", "s.syndicated_admitted", "s.dependent_admitted", "one_origin_rises")
    for w in worlds:
        diffs = paired(result, config, reference, w)
        sd = result["configurations"][reference]["worlds"][w]["spread"][PRIMARY]["sd"] or 0.0
        gain = sum(diffs) / len(diffs)
        mine = result["configurations"][config]["worlds"][w]["per_seed"]
        ref = result["configurations"][reference]["worlds"][w]["per_seed"]
        gain_all &= gain > sd
        retention &= all((r["retention.lost"] or 0) + (r["retention.regressed"] or 0) <=
                         (b["retention.lost"] or 0) + (b["retention.regressed"] or 0) for r, b in zip(mine, ref))
        safety &= all((r[k] or 0) <= (b[k] or 0) for r, b in zip(mine, ref) for k in keys)
        per_world[w] = {"gain": gain, "reference_sd": sd, "beats": gain > sd, "loses": gain < -sd}
    return {"config": config, "reference": reference,
            "verdict": "on" if gain_all and retention and safety else "off",
            "checks": {"gain_beyond_spread_every_world": gain_all, "retention": retention, "no_safety_regression":
                       safety}, "per_world": per_world}


def dumps(value) -> str:
    return json.dumps(scoring.rounded(value), indent=1)
