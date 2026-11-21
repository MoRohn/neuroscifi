"""Resource scheduling (RD-46): measurement parsing, the pressure level, audio priority (worker deferral, autonomy
admission, the background model slot cap), critical-pressure refusals (autonomy, the Chromium renderer), the
cross-process audio beacon, Presence resource_constrained, `nsf resources` and GET /api/resources."""
import json
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
                             "utilization_percent": None, "memory_readable": False, "compute_apps": []}


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
