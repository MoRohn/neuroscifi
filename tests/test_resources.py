"""Resource scheduling (RD-46): measurement parsing, the pressure level, audio priority (worker deferral, autonomy
admission, the background model slot cap), critical-pressure refusals (autonomy, the Chromium renderer), the
cross-process audio beacon, Presence resource_constrained, `nsf resources` and GET /api/resources."""
import json
from pathlib import Path
import re
import threading
import time

import pytest

from neuroscifi import resources
from neuroscifi.instance import store
from neuroscifi.presence import aggregator
from neuroscifi.resources import monitor, policy
from neuroscifi.voice import state as vstate
from neuroscifi.webcortex import autonomy, orchestrator, scheduler, state

GIB = 1024 ** 3
T0 = 1_795_200_000.0
READ_MEMORY = monitor.memory      # the real reader (tests/conftest.py replaces monitor.memory for every test)
MEMINFO = """MemTotal:       127848684 kB
MemFree:         5492496 kB
MemAvailable:   44092764 kB
SwapTotal:      127848444 kB
SwapFree:       121934296 kB
"""


def mem(available_gib, total_gib=128):
    return {"total_bytes": int(total_gib * GIB), "available_bytes": int(available_gib * GIB), "swap_total_bytes": 0,
            "swap_free_bytes": 0, "psi": None}


@pytest.fixture
def host(monkeypatch):
    """Set the measured MemAvailable (GiB of 128)."""
    def set_available(gib):
        monkeypatch.setattr(monitor, "memory", lambda *a, **k: mem(gib))
        monitor.clear_cache()
    return set_available


@pytest.fixture
def voice():
    """A voice session in this process: voice(mic_capturing=True) / voice(speaking=True) / voice() idle."""
    def set_state(**flags):
        vstate.reset()
        if flags:
            vstate.update("s1", **{"mode": "voice_text", "session_active": True, **flags})
    yield set_state
    vstate.reset()


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    yield db, path
    db.close()


# ---- measurement ------------------------------------------------------------------------------------------------

def test_meminfo_and_psi_are_parsed_in_bytes(tmp_path):
    (tmp_path / "meminfo").write_text(MEMINFO)
    (tmp_path / "psi").write_text("some avg10=1.50 avg60=0.20 avg300=0.00 total=12\n"
                                  "full avg10=0.25 avg60=0.00 avg300=0.00 total=3\n")
    m = READ_MEMORY(tmp_path / "meminfo", tmp_path / "psi")
    assert m["total_bytes"] == 127848684 * 1024 and m["available_bytes"] == 44092764 * 1024
    assert m["psi"] == {"some_avg10": 1.5, "full_avg10": 0.25}
    assert READ_MEMORY(tmp_path / "absent", tmp_path / "psi") is None
    assert READ_MEMORY(tmp_path / "meminfo", tmp_path / "absent")["psi"] is None


def test_pressure_levels_follow_the_documented_thresholds(monkeypatch):
    limits = monitor.thresholds(128 * GIB)
    assert limits["elevated_below_bytes"] == int(0.15 * 128 * GIB)       # 19.2 GiB > the 16 GiB floor
    assert limits["critical_below_bytes"] == int(0.06 * 128 * GIB)       # 7.68 GiB > the 6 GiB floor
    assert monitor.thresholds(32 * GIB)["critical_below_bytes"] == 6 * GIB   # small host: the absolute floor
    assert monitor.pressure(mem(40))[0] == "ok"
    level, reasons = monitor.pressure(mem(12))
    assert level == "elevated" and "12.0 GiB of 128.0 GiB" in reasons[0]
    assert monitor.pressure(mem(5))[0] == "critical"
    assert monitor.pressure(None) == ("unknown", ["/proc/meminfo is not readable"])
    gpu = {"memory_readable": True, "memory_used_bytes": 98, "memory_total_bytes": 100}
    assert monitor.pressure(mem(40), gpu)[0] == "critical"
    assert monitor.pressure(mem(40), gpu | {"memory_used_bytes": 91})[0] == "elevated"
    assert monitor.pressure(mem(40), {"memory_readable": False})[0] == "ok"     # GB10: N/A is never guessed
    monkeypatch.setenv("NSF_RESOURCE_CRITICAL_GIB", "50")
    assert monitor.pressure(mem(40))[0] == "critical"


def test_nvidia_smi_na_and_compute_apps_with_commas(monkeypatch):
    outputs = {"--query-gpu=memory.used,memory.total,utilization.gpu": "[N/A], [N/A], 0\n",
               "--query-compute-apps=pid,process_name,used_memory":
                   "4290, /usr/lib/chromium/chromium --type=gpu-process --x=a,b, 257\n"
                   "2301868, VLLM::EngineCore, 44134\n"}
    monkeypatch.setattr(monitor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(monitor, "_run", lambda args, *a, **k: outputs.get(args[1]))
    g = monitor.gpu()
    assert g["memory_readable"] is False and g["memory_used_bytes"] is None and g["utilization_percent"] == 0.0
    assert g["compute_apps"] == [
        {"pid": 4290, "name": "/usr/lib/chromium/chromium", "memory_bytes": 257 * 1024 * 1024},
        {"pid": 2301868, "name": "VLLM::EngineCore", "memory_bytes": 44134 * 1024 * 1024}]


def test_docker_stats_and_cold_start_from_logs(monkeypatch):
    stats = ('{"Name":"neuroscifi-voice-tts","MemUsage":"2.401GiB / 8GiB","CPUPerc":"0.19%"}\n'
             '{"Name":"neuroscifi-vllm","MemUsage":"9.967GiB / 96GiB","CPUPerc":"2.34%"}\n')
    logs = ("2026-11-21T12:27:38.053717221Z warming up\n"
            "2026-11-21T12:27:39.832695071Z tts: ready on cuda after 4.0s\n")

    def run(args, *a, **k):
        if args[:2] == ["docker", "ps"]:
            return "neuroscifi-voice-tts\nneuroscifi-vllm\nother-app\n"
        if args[:2] == ["docker", "stats"]:
            assert "other-app" not in args
            return stats
        if args[:2] == ["docker", "inspect"]:
            return "2026-11-21T12:27:34.896017588Z\n"
        if args[:2] == ["docker", "logs"]:
            assert k.get("merge") is True          # docker replays the container's stderr on stderr
            return logs if args[-1] == "neuroscifi-voice-tts" else "no readiness line\n"
    monkeypatch.setattr(monitor, "_run", run)
    rows = monitor.containers()
    assert rows[0] == {"name": "neuroscifi-voice-tts", "memory_bytes": int(2.401 * GIB), "memory_limit_bytes": 8 * GIB,
                       "cpu_percent": 0.19}
    starts = {r["name"]: r["seconds"] for r in monitor.cold_starts()}
    assert starts == {"neuroscifi-voice-tts": 4.9, "neuroscifi-vllm": None}


def test_without_docker_or_nvidia_smi_nothing_is_invented(monkeypatch):
    monkeypatch.setattr(monitor.shutil, "which", lambda name: None)
    assert monitor.containers() is None and monitor.cold_starts() is None
    assert monitor.gpu() == {"available": False, "memory_used_bytes": None, "memory_total_bytes": None,
                             "utilization_percent": None, "memory_readable": False, "temperature_c": None,
                             "power_draw_w": None, "throttle": None, "compute_apps": []}


def test_gpu_temperature_power_and_throttle_reasons_come_from_the_same_nvidia_smi_call(monkeypatch):
    calls = []
    health = "--query-gpu=" + monitor.GPU_HEALTH_QUERY
    outputs = {health: "[N/A], [N/A], 96, 57, 59.13, 0x0000000000000400, Not Active, Not Active, Not Active, "
                       "Not Active\n",
               "--query-compute-apps=pid,process_name,used_memory": "61259, VLLM::EngineCore, 30499\n"}

    def run(args, *a, **k):
        calls.append(args[1])
        return outputs.get(args[1])
    monkeypatch.setattr(monitor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(monitor, "_run", run)
    g = monitor.gpu()            # the real GB10 answer, captured 2026-11-23
    assert g["memory_readable"] is False and g["utilization_percent"] == 96.0
    assert g["temperature_c"] == 57.0 and g["power_draw_w"] == 59.13
    assert g["throttle"] == {"thermal": False, "sw_thermal_slowdown": False, "hw_thermal_slowdown": False,
                             "sw_power_cap": False, "hw_slowdown": False, "active_mask": "0x0000000000000400"}
    assert calls == [health, "--query-compute-apps=pid,process_name,used_memory"]     # one query, no fallback
    outputs[health] = "[N/A], [N/A], 100, 91, 120.5, 0x0000000000000060, Active, [N/A], Not Active, Not Active\n"
    g = monitor.gpu()
    assert g["throttle"]["thermal"] is True and g["throttle"]["hw_thermal_slowdown"] is None


def test_a_driver_without_the_health_fields_still_reports_utilisation_and_leaves_the_rest_unread(monkeypatch):
    outputs = {"--query-gpu=" + monitor.GPU_QUERY: "1024, 81920, 12\n"}       # the health query fails: None
    monkeypatch.setattr(monitor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(monitor, "_run", lambda args, *a, **k: outputs.get(args[1]))
    g = monitor.gpu()
    assert g["memory_readable"] is True and g["utilization_percent"] == 12.0
    assert g["temperature_c"] is None and g["power_draw_w"] is None and g["throttle"] is None


def test_host_load_average_comes_from_proc_loadavg_and_an_unreadable_file_says_so(tmp_path):
    (tmp_path / "loadavg").write_text("1.80 1.27 1.49 4/2270 62478\n")
    out = monitor.load(tmp_path / "loadavg")
    assert out["available"] is True and (out["avg1"], out["avg5"], out["avg15"]) == (1.8, 1.27, 1.49)
    assert out["cpus"] >= 1
    assert monitor.load(tmp_path / "absent") == {"available": False, "reason": f"{tmp_path / 'absent'} is not readable"}


# ---- the inference server's state (vLLM /metrics) ---------------------------------------------------------------

LIVE_METRICS = (Path(__file__).parent / "data" / "vllm_metrics.prom").read_text()
MODEL = "qwen3-30b-a3b-instruct-2507-fp8"


def _set(text, metric, value):
    """The exposition with one sample's value replaced (`metric` names the series up to its labels)."""
    return re.sub(rf"^({re.escape(metric)}\{{[^\n]*\}}) \S+$", rf"\g<1> {value}", text, count=1, flags=re.M)


@pytest.fixture
def metrics_server():
    """A loopback HTTP server that answers /metrics with `server.text` (status `server.status`) and records the
    Authorization headers it received."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            box.seen.append(self.headers.get("Authorization"))
            body = box.text.encode()
            self.send_response(box.status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    box = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    box.text, box.status, box.seen = LIVE_METRICS, 200, []
    box.url = f"http://127.0.0.1:{box.server_port}/metrics"
    thread = threading.Thread(target=box.serve_forever, daemon=True)
    thread.start()
    yield box
    box.shutdown()
    box.server_close()


def test_live_vllm_metrics_give_queue_depth_kv_cache_and_lifetime_means(metrics_server):
    out = monitor.inference(metrics_server.url, "k-secret", clock=lambda: T0)
    assert out["available"] is True and out["models"] == [MODEL]
    assert (out["requests_running"], out["requests_waiting"]) == (1.0, 0.0)     # waiting_by_reason is not added
    assert out["kv_cache_usage"] == pytest.approx(0.0275, abs=1e-4)
    assert out["server_started_at"] == 1.79544966713e+09
    life = out["since_start"]
    assert life["mean_seconds"] == {"queue": round(0.001113671954954043 / 17, 4),
                                    "prefill": round(5.329227845999412 / 17, 4),
                                    "decode": round(32.43045773898484 / 17, 4),
                                    "first_token": round(6.956857442855835 / 18, 4),
                                    "request": round(38.26640057563782 / 17, 4)}
    assert life["requests_finished"] == 17.0 and life["prompt_tokens"] == 34681.0
    assert life["prefix_cache_hit_share"] == round(512 / 34681, 4) and life["generation_tokens"] == 1363.0
    assert life["preemptions"] == 0.0
    assert life["finished_by_reason"] == {"stop": 17.0, "length": 0.0, "abort": 0.0, "error": 0.0, "repetition": 0.0}
    assert out["interval"] == {"available": False, "reason": "no earlier reading in this process (an interval "
                                                             "appears from the second reading, at least 15 s later)"}
    assert metrics_server.seen == ["Bearer k-secret"] and "k-secret" not in json.dumps(out)


def test_the_interval_since_the_previous_reading_shows_what_lifetime_means_hide(metrics_server):
    monitor.inference(metrics_server.url, clock=lambda: T0)
    text = LIVE_METRICS
    for metric, value in (("vllm:e2e_request_latency_seconds_count", 19.0),
                          ("vllm:e2e_request_latency_seconds_sum", 38.26640057563782 + 20.0),
                          ("vllm:request_queue_time_seconds_count", 19.0),
                          ("vllm:request_queue_time_seconds_sum", 0.001113671954954043 + 12.0),
                          ("vllm:prompt_tokens_total", 35681.0), ("vllm:prefix_cache_queries_total", 35681.0),
                          ("vllm:prefix_cache_hits_total", 1412.0), ("vllm:num_preemptions_total", 3.0)):
        text = _set(text, metric, value)
    metrics_server.text = text
    out = monitor.inference(metrics_server.url, clock=lambda: T0 + 60)
    now = out["interval"]
    assert now["available"] is True and now["seconds"] == 60.0 and now["requests_finished"] == 2.0
    assert now["mean_seconds"]["queue"] == 6.0                 # lifetime says 0.4 s: a queue built up just now
    assert now["mean_seconds"]["request"] == 10.0 and now["mean_seconds"]["decode"] is None   # no decode finished
    assert now["prompt_tokens"] == 1000.0 and now["prefix_cache_hit_share"] == 0.9 and now["preemptions"] == 3.0
    assert out["since_start"]["mean_seconds"]["queue"] == round((0.001113671954954043 + 12.0) / 19, 4)
    metrics_server.text = _set(LIVE_METRICS, "vllm:e2e_request_latency_seconds_count", 1.0).replace(
        "process_start_time_seconds 1.79544966713e+09", "process_start_time_seconds 1.79545e+09")
    assert monitor.inference(metrics_server.url, clock=lambda: T0 + 120)["interval"] == {
        "available": False, "reason": "the server restarted since the previous reading"}


def test_an_unreachable_refusing_or_non_vllm_endpoint_is_reported_absent_with_the_reason(metrics_server):
    metrics_server.text = "# HELP python_info x\npython_info{version=\"3.12\"} 1.0\n"
    out = monitor.inference(metrics_server.url)
    assert out == {"available": False, "url": metrics_server.url,
                   "reason": f"{metrics_server.url} has no vllm: metrics (not a vLLM server)"}
    metrics_server.status = 401
    assert monitor.inference(metrics_server.url)["reason"] == f"{metrics_server.url} answered HTTP 401"
    closed = "http://127.0.0.1:9/metrics"
    out = monitor.inference(closed, timeout=1.0)
    assert out["available"] is False and out["reason"].startswith(f"{closed} is unreachable (")


def test_the_scrape_is_cached_and_the_hot_path_sample_never_makes_it(metrics_server, monkeypatch):
    first = monitor.inference_cached(metrics_server.url)
    assert monitor.inference_cached(metrics_server.url) is first and len(metrics_server.seen) == 1
    monkeypatch.setattr(monitor, "inference", lambda *a, **k: pytest.fail("the hot path scraped /metrics"))
    monkeypatch.setattr(monitor, "gpu", lambda: pytest.fail("the hot path ran nvidia-smi"))
    assert monitor.sample()["load"] is None and resources.status(None, full=False)["inference"] is None


def test_the_idle_policy_reads_in_flight_and_served_requests_with_the_shared_parser(metrics_server):
    from neuroscifi.resources import idle
    assert idle._metrics(metrics_server.url) == {"in_flight": 1.0, "served": 17.0}
    assert metrics_server.seen == [None]                       # as before: the idle tick sends no key
    metrics_server.text = "python_info 1.0\n"
    assert idle._metrics(metrics_server.url) is None


def test_status_reads_the_configured_loopback_endpoint_with_the_gateway_key_and_says_why_when_it_cannot(
        individual, metrics_server, tmp_path):
    from neuroscifi.inference import config as iconfig
    db, path = individual
    assert resources.status(path)["inference"] == {
        "available": False, "reason": "the inference endpoint is not configured (nsf llm config --url ...)"}
    assert resources.status(None)["inference"]["reason"] == "no instance database, so the inference endpoint is " \
                                                            "not known"
    key = tmp_path / "api_key"
    key.write_text("k-from-file\n")
    key.chmod(0o600)
    iconfig.set_config(db, {"base_url": metrics_server.url.replace("/metrics", "/v1"), "key_file": str(key)})
    out = resources.status(path)
    assert out["inference"]["available"] is True and out["inference"]["requests_running"] == 1.0
    assert metrics_server.seen == ["Bearer k-from-file"] and "k-from-file" not in json.dumps(out)
    assert out["load"]["available"] in (True, False) and "temperature_c" in (out["gpu"] or {"temperature_c": 0})
    iconfig.set_config(db, {"base_url": "https://models.example.org/v1", "remote_allowed": True})
    assert resources.status(path)["inference"]["reason"] == (
        "the inference endpoint https://models.example.org is not on this host; its server state is not measured "
        "here")


# ---- policy ---------------------------------------------------------------------------------------------------

def test_background_gate_audio_priority_and_critical_pressure(host, voice):
    host(40)
    assert policy.background_gate()["allowed"] is True
    voice(mic_capturing=True)
    gate = policy.background_gate()
    assert gate["allowed"] is False and gate["audio"] is True and gate["reason"].startswith(policy.AUDIO_REASON)
    voice(speaking=True)
    assert policy.background_gate()["allowed"] is False
    voice(session_active=True)            # a session that neither captures nor speaks does not hold work back
    assert policy.background_gate()["allowed"] is True
    voice()
    host(5)
    gate = policy.background_gate()
    assert gate["allowed"] is False and "memory pressure critical" in gate["reason"] and "5.0 GiB" in gate["reason"]
    assert gate["why"] == "memory"
    assert policy.engine_gate("browser rendering")[0] is False
    host(12)                              # elevated: reported, nothing refused
    assert policy.background_gate()["allowed"] is True and policy.engine_gate("x") == (True, None)


def test_the_audio_beacon_carries_audio_state_to_another_process(tmp_path, voice):
    db_path = tmp_path / "i.sqlite"
    beacon = policy.AudioBeacon(db_path)
    beacon.tick()
    assert not (tmp_path / "i.sqlite.resources").exists()        # idle: nothing written
    voice(mic_capturing=True)
    beacon.tick()
    written = json.loads((tmp_path / "i.sqlite.resources" / "audio.json").read_text())
    assert written["active"] is True
    voice()                                # this process is idle now; another process reads the beacon
    assert policy.audio_active(db_path) is True
    assert policy.audio_active(db_path, now=written["at"] + policy.BEACON_STALE + 1) is False   # stale: ignored
    beacon.tick()
    assert policy.audio_active(db_path) is False


def test_background_model_slots_are_capped_while_audio_runs(tmp_path, voice):
    db_path = tmp_path / "i.sqlite"
    assert policy.model_slots(db_path) == policy.BACKGROUND_MODEL_SLOTS == 2
    with policy.background_model_slot(db_path) as a, policy.background_model_slot(db_path) as b:
        assert (a, b) == (0, 1)
    voice(speaking=True)
    assert policy.model_slots(db_path) == 1
    with policy.background_model_slot(db_path) as a:
        t = time.monotonic()
        with policy.background_model_slot(db_path, deadline=time.monotonic() + 0.3) as b:
            assert a == 0 and b is None and time.monotonic() - t >= 0.3
    held = threading.Event()
    release = threading.Event()

    def other():                          # a slot held by another thread (flock is per open file: as a process)
        with policy.background_model_slot(db_path):
            held.set()
            release.wait(5)
    worker = threading.Thread(target=other)
    worker.start()
    held.wait(5)
    try:
        with policy.background_model_slot(db_path, deadline=time.monotonic() + 0.3) as b:
            assert b is None
    finally:
        release.set()
        worker.join()


# ---- enforcement in the research worker ---------------------------------------------------------------------------

def test_worker_defers_new_autonomous_research_while_voice_is_active_but_runs_user_tasks(individual, voice):
    db, path = individual
    auto = orchestrator.submit(db, "Zephyr compiler boards", T0, mode="quick_lookup", origin="curiosity")
    voice(mic_capturing=True)
    assert scheduler.claim_next(db, "w", T0) is None                     # the autonomous task is not claimed
    verdict = autonomy.admit(db, state.task(db, auto["id"]), T0)         # nor admitted by another path
    assert verdict["wait"].startswith(policy.AUDIO_REASON)
    assert scheduler._over_cap(db, T0, state.get_config(db)).startswith(policy.AUDIO_REASON)   # the timer skips it
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup", origin="console")
    assert scheduler.claim_next(db, "w", T0)[0] == user["id"]           # the owner's own research goes ahead
    voice()
    db.execute("UPDATE web_tasks SET status='cancelled' WHERE id=?", (user["id"],))
    assert scheduler.claim_next(db, "w2", T0 + 1)[0] == auto["id"]      # audio over: it may start


def test_the_worker_logs_when_deferral_starts_and_ends(individual, voice, monkeypatch):
    db, path = individual
    events = []
    audio = {"allowed": False, "reason": policy.AUDIO_REASON + "; x", "why": "audio"}
    gates = iter([audio, audio | {"reason": policy.AUDIO_REASON + "; changed text"},
                  {"allowed": True, "reason": None, "why": None}])
    monkeypatch.setattr(scheduler, "background", lambda db: next(gates, {"allowed": True, "reason": None,
                                                                          "why": None}))
    monkeypatch.setattr(scheduler, "claim_next", lambda *a, **k: None)
    stop = threading.Event()
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
    scheduler.work(path, stop=stop, sleep=sleep, idle=True)
    for (detail,) in db.execute("SELECT detail FROM web_events WHERE task_id IS NULL AND kind='decision' ORDER BY id"):
        events.append(json.loads(detail))
    assert [e["worker"] for e in events] == ["deferring new autonomous research",
                                             "autonomous research may start again"]
    assert events[0]["reason"].startswith(policy.AUDIO_REASON)


def test_critical_memory_refuses_background_autonomy_and_rendering(individual, host, monkeypatch):
    db, path = individual
    auto = orchestrator.submit(db, "Zephyr compiler boards", T0, mode="quick_lookup", origin="curiosity")
    host(4)
    assert "memory pressure critical" in autonomy.admit(db, state.task(db, auto["id"]), T0)["wait"]
    assert scheduler.claim_next(db, "w", T0) is None

    class Renderer:
        calls = 0

        def available(self):
            return True, "fake"

        def render(self, *a, **k):
            Renderer.calls += 1
            raise AssertionError("must not render under critical memory pressure")
    from neuroscifi.webcortex.types import FetchResult
    run = orchestrator._Run.__new__(orchestrator._Run)
    events = []
    run.services = type("S", (), {"renderer": Renderer()})()
    run.usage = type("U", (), {"browser_sessions": 0})()
    run.budget = type("B", (), {"max_browser_sessions": 1})()
    run.event = lambda kind, detail, stage=None: events.append((kind, detail))
    shell = (b"<html><head><title>x</title></head><body><div id='app'></div><script src='/app.js'></script>"
             b"</body></html>")
    result = FetchResult(url="http://spa.example/", final_url="http://spa.example/", status=200, headers={},
                         content_type="text/html", body=shell, retrieved_at=T0)
    run.finish("http://spa.example/", result)
    assert Renderer.calls == 0
    assert events and events[0][0] == "decision" and events[0][1]["render"] is False
    assert "memory pressure critical" in events[0][1]["reason"]


# ---- surfaces -------------------------------------------------------------------------------------------------------

def test_presence_reports_measured_pressure_as_resource_constrained(individual, host):
    db, path = individual
    s = aggregator.derive(db, path, time.time())
    assert s["sources"]["resources"] == "ok" and "resource_constrained" not in s["condition"]
    host(12)
    s = aggregator.derive(db, path, time.time() + 10)
    assert s["sources"]["resources"] == "elevated" and "resource_constrained" in s["condition"]


def test_status_cli_and_console_route(individual, host, voice, tmp_path, capsys):
    from neuroscifi.cli import main
    db, path = individual
    host(5)
    voice(user_speaking=True)
    out = resources.status(path, full=False)
    assert out["level"] == "critical" and out["audio_active"] is True
    assert out["policy"]["background_research"]["allowed"] is False
    assert out["policy"]["browser_rendering"]["allowed"] is False
    assert out["policy"]["background_model_slots"] == 1 and out["policy"]["idle_unload"]["automatic"] is False
    assert main(["resources", "--db", str(path), "--quick"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["level"] == "critical" and printed["gpu"] is None
    from neuroscifi.console import server
    srv = server.ConsoleServer(path, 0, token="t" * 40)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
        conn.request("GET", "/api/resources", headers={"Authorization": "Bearer " + "t" * 40})
        body = json.loads(conn.getresponse().read())
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert body["level"] == "critical" and set(body["policy"]) >= {"background_research", "browser_rendering"}


def test_the_metrics_scrape_never_forwards_the_key_through_a_redirect():
    """Found in review: urllib follows a 3xx and resends the Authorization header. A process answering on the
    endpoint's port with 302 to another address received the model server's API key there. A redirect is now an
    error ("answered HTTP 302") and nothing is sent to its target."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"vllm:num_requests_running 0\n")

        def log_message(self, *args):
            pass

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    sink, redirector = HTTPServer(("127.0.0.1", 0), Sink), HTTPServer(("127.0.0.1", 0), Redirector)
    for server in (sink, redirector):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = monitor.inference(f"http://127.0.0.1:{redirector.server_port}/metrics", "SECRET-KEY-1")
    finally:
        for server in (sink, redirector):
            server.shutdown()
            server.server_close()
    assert seen == [] and out["available"] is False and "HTTP 302" in out["reason"]
    assert "SECRET-KEY-1" not in str(out)
