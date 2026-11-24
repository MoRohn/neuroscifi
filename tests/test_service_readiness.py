"""Speech synthesis and SearXNG count as loaded only once they answer (open-items round, lane D), like the vLLM
servers since review F4; SearXNG's unit is stopped and started as a unit."""
from pathlib import Path

import pytest

from neuroscifi.resources import idle
from test_resources_idle import MIN, fake, memory_host, spool, _unload_idle      # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]
INSTALL = str(ROOT / "deploy" / "install.sh")


# ---- loaded only once it answers: speech synthesis and SearXNG -----------------------------------------------------

@pytest.mark.parametrize("name,port,path", [("tts", 18432, "/health"), ("searxng", 18888, "/healthz")])
def test_a_running_service_without_metrics_counts_as_loaded_only_once_it_answers(fake, name, port, path):   # noqa: F811
    """Review F4 gave the vLLM servers this rule; these two had kept "running means loaded"."""
    p = _unload_idle(fake, name)
    container = idle.SERVICES[name].container
    fake.running[container] = fake.now            # left running by a reload that was killed
    fake.deaf.add(port)
    if name == "tts":
        fake.fail_wait.add(container)
    out = p.load(name, reason="owner command")
    assert out["action"] == "failed" and out["why"] == "it was running but did not answer"
    assert f"http://127.0.0.1:{port}{path}" in fake.asked          # asked on the port Docker publishes
    assert container not in fake.running                           # stopped: "unloaded" stays true
    assert p.read()["services"][name]["state"] == "unloaded"
    fake.running[container] = fake.now
    fake.deaf.discard(port)
    fake.fail_wait.discard(container)
    assert p.load(name, reason="owner command") == {"action": "none", "why": "already running"}
    assert p.read()["services"][name]["state"] == "loaded"


def test_searxng_is_asked_again_for_as_long_as_its_start_may_take(fake):      # noqa: F811
    p = _unload_idle(fake, "searxng")
    fake.running["neuroscifi-searxng"] = fake.now
    fake.deaf.add(18888)
    started = fake.now
    real = fake.clock

    def slow(url, calls=[]):
        calls.append(url)
        if len(calls) == 4:
            fake.deaf.discard(18888)              # it answers on the fourth look
        return 18888 not in fake.deaf
    p.read_ready = slow
    assert p.load("searxng", reason="owner command") == {"action": "none", "why": "already running"}
    assert 0 < real() - started <= idle.READY_WAIT


def test_a_service_whose_published_port_cannot_be_told_counts_as_before(fake):      # noqa: F811
    p = fake.make()
    fake.PUBLISHED = {}                           # docker port answers nothing
    assert p.answers(idle.SERVICES["tts"]) is None
    assert p.load("tts", reason="owner command") == {"action": "none", "why": "already running"}
    assert p.answers(idle.Service("x", "x", "c", (), ())) is None


def test_an_active_searxng_unit_is_stopped_and_started_as_a_unit(fake):      # noqa: F811
    """Its own commands write the instance database, which the idle account of an installed deployment may only read."""
    fake.units.add("neuroscifi-searxng.service")
    p = _unload_idle(fake, "searxng")
    assert ["systemctl", "--no-ask-password", "stop", "neuroscifi-searxng.service"] in fake.calls
    p.ensure_loaded("searxng", purpose="web search")
    assert ["systemctl", "--no-ask-password", "start", "neuroscifi-searxng.service"] in fake.calls
    assert not [c for c in fake.calls if "searxng" in c and "web" in c]
