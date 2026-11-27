"""Run one of lane FX's corpora through the benchmark's own driver (ArtifactsSubject) in ONE child process with a
3 GiB address-space cap and a wall-clock kill; the items run one at a time inside it, each under a 30 s alarm.

The corpora (ADR-080; the figures of `docs/quality/evidence/omnimind_fx.json`) are generated, never stored: every
file is built by the generators beside this one from fixed seeds (40000-43999) with the benchmark's own encoders.
They are lane FX's own development evidence, written by the lane that wrote the fixes, not an examination.
  corpus_unknown   identification disguises and near-misses (3,669 items)
  corpus_hostile   a hostile part inside an honest file, the honest part asked (168)
  corpus_honest    honest edges: what each rule turns away (19)
  corpus_cross     relater twin pairs in 12 kinds (144): run `corpus_cross.py OUT.json` itself
usage, from the repository root:
  PYTHONPATH=src python scripts/omnimind_fx/run_corpus.py corpus_unknown OUT.json
Truths are lane FX's: at lane FX2's commit the hostile class H6_svg_external_image (3 items) is answered with its
note by ADR-080 rule 2, as ruled, where lane FX's truth says refused (the honest edge `svg_external_image` agrees
with its truth again)."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def child(module, out, hostile):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
    sys.path.insert(0, HERE)
    from neuroscifi.acceptance.omni import run_in_process, score_task
    from neuroscifi.acceptance.omni.subject import ArtifactsSubject
    mod = __import__(module)
    subj = ArtifactsSubject()
    per, wrong = {}, []
    for cls, task in mod.ITEMS:
        subj._cache.clear()
        answer, info = run_in_process(task, subj, seconds=30.0)
        res = "failed" if answer is None else score_task(task, answer).outcome
        c = per.setdefault(cls, {"right": 0, "abstained": 0, "wrong": 0, "failed": 0})
        c[res] += 1
        if res in ("wrong", "failed"):
            wrong.append({"id": task.id, "q": task.question, "expect": task.expect, "outcome": res,
                          "got": None if answer is None else repr(answer.value)[:80],
                          "status": None if answer is None else answer.status, "info": info})
    tot = {k: sum(c[k] for c in per.values()) for k in ("right", "abstained", "wrong", "failed")}
    json.dump({"n": len(mod.ITEMS), "total": tot, "per_class": per, "wrong": wrong}, open(out, "w"), indent=1)
    print(json.dumps(tot), len(mod.ITEMS))


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[3] == "--child":
        child(sys.argv[1], sys.argv[2], False)
    else:
        r = subprocess.run([sys.executable, __file__, sys.argv[1], sys.argv[2], "--child"], timeout=1500)
        sys.exit(r.returncode)
