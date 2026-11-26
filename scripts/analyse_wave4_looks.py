"""Wave 4's two withheld looks, read exactly as the rule committed in e812a8e says (main session)."""
import json, statistics as st, sys
from neuroscifi.acceptance.cognition import sources
L = sys.argv[1]
out = {}
# ---- look 1 ----
new = json.load(open(L + "/sources.json"))
old = json.load(open("docs/quality/evidence/genesis_sources_withheld_baseline.json"))
diffs = []
if old.get("world_digests") != new.get("world_digests"):
    diffs.append("world_digests")
ow, nw = old["configurations"]["shipped"]["worlds"], new["configurations"]["shipped"]["worlds"]
if set(ow) != set(nw): diffs.append("world names")
for w in ow:
    for k, v in ow[w]["totals_sum"].items():
        if nw[w]["totals_sum"].get(k) != v: diffs.append(f"{w}.totals_sum.{k}: {v} -> {nw[w]['totals_sum'].get(k)}")
    for k, v in ow[w]["mean_correct"].items():
        if nw[w]["mean_correct"].get(k) != v: diffs.append(f"{w}.mean_correct.{k}: {v} -> {nw[w]['mean_correct'].get(k)}")
out["look1"] = {"control_keys_compared": {"worlds": len(ow), "totals_sum_keys": len(next(iter(ow.values()))["totals_sum"]),
                                           "mean_correct_keys": len(next(iter(ow.values()))["mean_correct"])},
                "control_differences": diffs, "void": bool(diffs), "seeds": new["seeds"]}
if not diffs:
    for cfg in ("own_text_provenance", "echo_recognition"):
        v = sources.own_verdict(new, config=cfg)
        tot = {}
        for w, e in v["per_world"].items():
            tot[w] = {k: e[k] for k in ("own_admitted", "wrongly_admitted", "suppressed", "discarded_honest_pages",
                                        "discarded_echo_pages", "mean_correct", "grouping_same")}
        out["look1"][cfg] = {"verdict": v["verdict"], "checks": v["checks"], "per_world": tot}
    t = lambda c, k: sum(e["totals_sum"][k] for e in new["configurations"][c]["worlds"].values())
    keys = ("own_admitted", "wrongly_admitted", "suppressed", "discarded_honest_pages", "discarded_echo_pages")
    out["look1"]["totals"] = {c: {k: t(c, k) for k in keys if k in next(iter(new["configurations"][c]["worlds"].values()))["totals_sum"]}
                              for c in ("shipped", "own_text_provenance", "echo_recognition")}
    # denominators if present
    w0 = next(iter(nw.values()))["totals_sum"]
    out["look1"]["shipped_totals_all_keys"] = {k: t("shipped", k) for k in w0 if isinstance(w0[k], (int, float))}
# ---- look 2 ----
c = json.load(open(L + "/cycles.json"))
look = c["look"]; refs = c.get("references", {}).get("worlds", {})
STEP0_SD = {}
worlds = list(look["baseline"])
for w, sd in zip(worlds, (0.064, 0.039, 0.035)): STEP0_SD[w] = sd
res = {}
for cfg, ref in (("idle-first", "baseline"), ("shipped+idle", "shipped")):
    res[cfg] = {}
    for w in worlds:
        a = {r["seed"]: r["mean.correct"] for r in look[cfg][w]}
        b = {r["seed"]: r["mean.correct"] for r in look[ref][w]}
        d = [a[s] - b[s] for s in sorted(a)]
        sd_ref = st.stdev(b.values())
        rr = refs.get(w, {}).get("round_robin", {}).get("mean_correct", {}).get("mean")
        res[cfg][w] = {"gain": round(st.mean(d), 4), "per_seed": [round(x, 4) for x in d], "ref": ref,
                       "ref_mean": round(st.mean(b.values()), 4), "cfg_mean": round(st.mean(a.values()), 4),
                       "sd_ref_this_look": round(sd_ref, 4), "sd_step0": STEP0_SD[w],
                       "beyond_spread": st.mean(d) > sd_ref, "round_robin": rr}
out["look2"] = {"worlds_in_file_order": worlds, "pairs": res, "look_pairs": c.get("look_pairs")}
json.dump(out, open(L + "/analysis.json", "w"), indent=1)
print(json.dumps(out, indent=1)[:9000])
