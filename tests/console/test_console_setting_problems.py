"""Wave 4 lane D: the console's overview lists damaged saved instance and voice settings, as `nsf config` and
`nsf voice settings` do (the web ones are in the web view since review3b N8)."""
from contextlib import closing
import json

from neuroscifi.instance import store
from neuroscifi.voice import config as vconfig

from test_console_api import console  # noqa: F401 - the fixture


def test_the_overview_lists_damaged_saved_instance_and_voice_settings(console):  # noqa: F811
    c = console
    assert c.ok("GET", "/api/inventory?measure=1")["setting_problems"] == []
    with closing(store.connect(c.path)) as db:
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        with store.write(db):
            db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw | {"min_free_bytes": "x"}),))
            db.execute("INSERT INTO meta(key,value) VALUES('voice',?) ON CONFLICT(key) DO UPDATE SET "
                       "value=excluded.value", (json.dumps({"retain_audio": "yes"}),))
        assert vconfig.get(db)["retain_audio"] is False
    problems = c.ok("GET", "/api/inventory?measure=1")["setting_problems"]
    assert len(problems) == 2 and "min_free_bytes" in problems[0] and "retain_audio" in problems[1]
    assert "setting_problems" not in c.ok("GET", "/api/inventory")        # the header's reload does not ask
