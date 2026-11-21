"""Presence performance measurements in a real Chromium (docs/presence/PERFORMANCE_RESULTS.md).

Writes measured numbers to a temp file (docs/presence/perf/measurements.json only with NSF_UPDATE_PERF_DOCS=1); asserts only generous budgets so the suite stays
stable on slower machines. Frame pacing comes from requestAnimationFrame timestamps recorded by the renderer;
scripting per frame from the renderer's own performance.now() spans AND from the DevTools ScriptDuration metric;
heap from Performance.getMetrics JSHeapUsedSize after a forced garbage collection.
"""
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import time

import pytest
from playwright.sync_api import expect

from test_presence import TOKEN, Spark, open_console

DOCS_OUT = Path(__file__).resolve().parents[2] / "docs" / "presence" / "perf" / "measurements.json"
# The tracked docs file is rewritten only on request (NSF_UPDATE_PERF_DOCS=1); otherwise a scratch path is used.
OUT = DOCS_OUT if os.environ.get("NSF_UPDATE_PERF_DOCS") == "1" else Path(tempfile.gettempdir()) / "nsf-presence-perf-measurements.json"


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q / 100 * (len(v) - 1))))], 3)


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


def measure(page, cdp, throttle):
    cdp.send("Performance.enable")
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})

    def metrics():
        cdp.send("HeapProfiler.collectGarbage")
        return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    g = "window.__presenceGallery"
    page.locator("#live").scroll_into_view_if_needed()   # off screen the loop is paused (IntersectionObserver)
    # continuous motion: 'reasoning' (orbit swing + accents) for 5 s
    page.evaluate(f"{g}.set('reasoning', {{intensity: 0.6}})")
    page.wait_for_timeout(1200)
    f0 = page.evaluate(f"{g}.stats().frames")
    m0 = metrics()
    page.wait_for_timeout(5000)
    m1 = metrics()
    f1 = page.evaluate(f"{g}.stats().frames")
    samples = page.evaluate("window.__nsfPresence.frames()")
    intervals = samples["intervals"][-(f1 - f0):] if f1 > f0 else []
    script = samples["scriptMs"][-(f1 - f0):] if f1 > f0 else []
    # 200 transitions through every activity, 30 ms apart, then the heap after a forced GC
    page.evaluate(f"{g}.set('idle')")
    page.wait_for_timeout(1500)
    h0 = metrics()
    t0 = time.monotonic()
    after = page.evaluate(f"{g}.run(200, 30)")
    took = time.monotonic() - t0
    page.wait_for_timeout(2500)
    h1 = metrics()
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    frames = max(1, f1 - f0)
    return {
        "cpu_throttle": throttle,
        "continuous_motion_5s": {
            "frames": f1 - f0,
            "raf_interval_ms_p50": pct(intervals, 50), "raf_interval_ms_p95": pct(intervals, 95),
            "renderer_script_ms_per_frame_p50": pct(script, 50), "renderer_script_ms_per_frame_p95": pct(script, 95),
            "devtools_script_ms_per_frame": round((m1["ScriptDuration"] - m0["ScriptDuration"]) * 1000 / frames, 3),
            "devtools_layout_ms_per_frame": round((m1["LayoutDuration"] - m0["LayoutDuration"]) * 1000 / frames, 3),
            "devtools_recalc_style_ms_per_frame": round((m1["RecalcStyleDuration"] - m0["RecalcStyleDuration"]) * 1000 / frames, 3),
        },
        "transitions_200": {
            "wall_s": round(took, 2), "scene_commits": after["transitions"],
            "heap_before_kb": round(h0["JSHeapUsedSize"] / 1024, 1), "heap_after_kb": round(h1["JSHeapUsedSize"] / 1024, 1),
            "heap_delta_kb": round((h1["JSHeapUsedSize"] - h0["JSHeapUsedSize"]) / 1024, 1),
            "dom_nodes_before": h0["Nodes"], "dom_nodes_after": h1["Nodes"],
            "listeners_before": h0["JSEventListeners"], "listeners_after": h1["JSEventListeners"],
            "max_concurrent_loops": after["maxConcurrentLoops"], "svg_paths": after["paths"],
            "max_point_speed": round(after["maxSpeed"], 1),
        },
    }


def test_presence_performance(spark, new_page, browser):
    results = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "chromium": browser.version,
               "machine": f"{platform.machine()} {platform.system()} {platform.release()}", "runs": {}}
    for label, size in (("desktop_1280", (1280, 900)), ("phone_390", (390, 844))):
        page = new_page(*size)
        page.goto(spark.base + "/presence/gallery#token=" + TOKEN)
        expect(page.locator("#gcontent")).to_be_visible()
        cdp = page.context.new_cdp_session(page)
        results["runs"][label] = {"x1": measure(page, cdp, 1), "x4": measure(page, cdp, 4)}
        assert page.problems == []
    # the production console element: idle overhead (no frames: CSS breathing; one open long-poll)
    page = new_page(1280, 900)
    open_console(page, spark)
    t_open = time.monotonic()
    while page.evaluate("window.__nsfPresence.stats().running") and time.monotonic() - t_open < 90:
        page.wait_for_timeout(500)   # resting decays to stillness (wait_for_function needs eval, blocked by the CSP)
    settle_s = round(time.monotonic() - t_open, 1)
    s0 = page.evaluate("window.__nsfPresence.stats()")
    page.wait_for_timeout(10_000)
    s1 = page.evaluate("window.__nsfPresence.stats()")
    results["console_idle_10s"] = {"frames": s1["frames"] - s0["frames"], "requests_completed": s1["requests"] - s0["requests"],
                                   "concept": s1["concept"], "seconds_until_still_after_load": settle_s}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2) + "\n")
    for label in ("desktop_1280", "phone_390"):
        for rate in ("x1", "x4"):
            r = results["runs"][label][rate]
            t = r["transitions_200"]
            assert t["max_concurrent_loops"] <= 1 and t["svg_paths"] == 14
            assert t["listeners_after"] <= t["listeners_before"]
            assert t["heap_delta_kb"] < 2048, t           # no growth beyond noise over 200 transitions
            assert r["continuous_motion_5s"]["frames"] > 100, r
            assert r["continuous_motion_5s"]["renderer_script_ms_per_frame_p95"] < 8, r
    assert results["console_idle_10s"]["frames"] == 0
    assert results["console_idle_10s"]["requests_completed"] <= 2
    assert re.match(r"^\d", results["chromium"])
