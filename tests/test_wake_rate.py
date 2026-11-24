"""The wake unit's starts are bounded by the clock (open-items round, lane D): the path unit starts the service once
per request that finds it stopped, so the number of starts was the asker's to choose, and thirty in ten minutes
stopped the unit until `systemctl reset-failed`."""
import json
from pathlib import Path
import re


from neuroscifi.resources import idle
from test_resources_idle import MIN, fake, memory_host, spool, _unload_idle      # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]
INSTALL = str(ROOT / "deploy" / "install.sh")


# ---- the wake unit's starts are bounded by the clock ---------------------------------------------------------------

def test_the_wake_command_lingers_and_serves_what_arrives_meanwhile(spool):      # noqa: F811
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed", "rerank")
    (fake.spool / "embed.wake").write_text("")
    naps = []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 3:
            (fake.spool / "rerank.wake").write_text("")           # a second request, while the command is still there
    served = idle.serve_wake_requests(fake.path, fake.spool, idle=fake.make(), linger=10.0, sleep=nap)
    assert [(s["service"], s["action"]) for s in served] == [("embed", "load"), ("rerank", "load")]
    assert sum(naps) == 10.0 and not list(fake.spool.iterdir())
    assert idle.serve_wake_requests(fake.path, fake.spool, idle=fake.make(), sleep=naps.append) == []
    assert sum(naps) == 10.0                                       # by hand (no --linger) it does not stay


def test_the_wake_unit_cannot_reach_its_start_limit_whatever_is_written_into_the_folder():
    unit = (ROOT / "deploy" / "systemd" / "neuroscifi-wake.service").read_text()
    linger = float(re.search(r"resources wake .*--linger (\d+)'", unit).group(1))
    burst = int(re.search(r"^StartLimitBurst=(\d+)$", unit, re.M).group(1))
    interval = re.search(r"^StartLimitIntervalSec=(\d+)min$", unit, re.M)
    assert linger == idle.WAKE_LINGER
    assert int(interval.group(1)) * 60 / linger < burst            # one start per linger at most: under the limit
    assert int(re.search(r"^TimeoutStartSec=(\d+)min$", unit, re.M).group(1)) * 60 > linger + idle.STUCK_HOLD


def test_nsf_resources_wake_takes_linger(spool, capsys, monkeypatch):      # noqa: F811
    from neuroscifi import cli
    seen = {}
    monkeypatch.setattr(idle, "serve_wake_requests",
                        lambda path, folder, *, idle=None, linger=0.0: seen.update(linger=linger) or [])
    assert cli.main(["resources", "wake", "--db", str(spool.path), "--linger", "25"]) == 0
    assert seen == {"linger": 25.0} and json.loads(capsys.readouterr().out) == {"served": []}
