"""Console autonomy controls (D4) over the real HTTP server: status, the recorded owner switch, limits, ranking."""
from contextlib import closing

from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, state
from test_console_web import T0, start_console, stop_console


def test_autonomy_status_switch_and_limits(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True}, T0)
    db.close()
    c = start_console(path, None, worker=False)
    try:
        status = c.ok("GET", "/api/web/autonomy")
        assert status["switch"] is False and status["enabled"] is False
        assert status["autonomous"]["limits"]["searches"] == 20 and status["candidates"]["chosen"] == []
        out = c.ok("POST", "/api/web/autonomy", {"enabled": True, "autonomous_daily": {"searches": 10}})
        assert out["enabled"] is True and out["record"]["enabled_by"] == "owner:console"
        assert out["autonomous"]["limits"]["searches"] == 10 and out["autonomous"]["remaining"]["searches"] == 10
        code, data = c.post("/api/web/autonomy", {"autonomous_daily": {"searches": -1}})
        assert code == 400
        code, data = c.post("/api/web/autonomy", {"enabled": "yes"})
        assert code == 400
        for raw in (b'{"autonomous_daily": {"searches": Infinity}}', b'{"autonomous_daily": {"searches": 1e400}}',
                    b'{"cooldown": Infinity}'):     # T5-04: OverflowError -> 500
            code, _, data = c.call("POST", "/api/web/autonomy", raw=raw, headers={"Content-Type": "application/json"})
            assert code == 400, (raw, code, data)
        assert c.ok("POST", "/api/web/config", {"autonomous": False})["config"]["autonomous"] is False
        assert c.ok("GET", "/api/web/autonomy")["enabled"] is False          # the switch-off is recorded too
        c.ok("POST", "/api/web/config", {"autonomous": True})
        with closing(store.connect(path)) as db:
            assert autonomy.policy(db)["record"]["enabled_by"] == "owner:console"
    finally:
        stop_console(c)
