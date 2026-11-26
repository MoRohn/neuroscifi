"""Wave 3 lane P: why the offline research benchmark's digest differs between hosts.

The development host's deterministic metrics (docs/quality/evidence/offline_deterministic_aarch64.json, written by
`scripts/bench_wave1.py offline --deterministic-out`) hash to the recorded 68731d72…; with the one leaf that names
the browser's file path set to where CI's Playwright Chromium lives, they hash to CI's 426abca5…, so nothing else
differed on CI's x86_64 runner. The portable digest drops that path and is the same for both."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "quality" / "evidence" / "offline_deterministic_aarch64.json"
RECORDED = "68731d7235cbae9fca289124ea59bed99c1aa5b578ecaf8784d2e3df54cdf9d5"   # development host (aarch64)
# CI's x86_64 runner: the whole digest, read (read-only `gh run view --log-failed`) from the Wave 2 pull request's
# run 45759723830 in the fix lane; the 12 characters quoted before (426abca5d803) are its start
CI_PREFIX = "426abca5d803349df0953045bd233495d2c23363a3107aea16de66151de46f6c"
CI_BROWSER = "/home/runner/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"   # ci.yml's NSF_CHROMIUM


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_metrics_are_the_recorded_digest_and_ci_differs_only_by_the_browser_path():
    bench = _script("bench_wave1")
    raw = EVIDENCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RECORDED
    det = json.loads(raw)
    assert bench.canonical(det) == raw
    render = det["probes"]["content"]["js_shell"]["render"]
    assert render["availability"] == "chromium at /usr/bin/chromium"
    ci = json.loads(raw)
    ci["probes"]["content"]["js_shell"]["render"]["availability"] = f"chromium at {CI_BROWSER}"
    assert hashlib.sha256(bench.canonical(ci)).hexdigest().startswith(CI_PREFIX)
    portable = hashlib.sha256(bench.canonical(bench.portable(det))).hexdigest()
    assert portable == hashlib.sha256(bench.canonical(bench.portable(ci))).hexdigest()
    assert portable != RECORDED
    assert bench.portable(det)["probes"]["content"]["js_shell"]["render"]["availability"] == "chromium"
    assert det["probes"]["content"]["js_shell"]["render"]["availability"] == "chromium at /usr/bin/chromium"


def test_the_diff_lists_each_differing_leaf_with_both_values(tmp_path, capsys):
    diff = _script("diff_deterministic")
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"x": {"y": 1.5, "z": ["p", "q"]}, "n": 3}))
    b.write_text(json.dumps({"sha256": "-", "deterministic": {"x": {"y": 1.25, "z": ["p"]}, "n": 3, "m": True}}))
    assert diff.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "x.y\n  A: 1.5\n  B: 1.25" in out and 'x.z[1]\n  A: "q"\n  B: "<absent>"' in out
    assert 'm\n  A: "<absent>"\n  B: true' in out and "# 3 differing leaves" in out and "\nn\n" not in out
    assert diff.main([str(a), str(a)]) == 0
