import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from neuroscifi.pocket import server as bridge


class MobileBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 50000
        self.store = bridge.Store(Path(self.tmp.name)/"test.db", lambda: self.now)
        self.key = self.store.pair()
        self.event = dict(id=str(uuid4()), channel="sound", value="soft", learn=False)

    def call(self, op, data=None):
        return self.store.action(self.key, op, data or {})

    def test_only_enum_receipts(self):
        for patch in [dict(waveform="raw"),dict(transcript="name"),dict(value="Jane"),dict(learn=1),
                      dict(channel="photo"),dict(id="alice"),dict(value=["soft"]),dict(channel=[])]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.call("save",dict(self.event, **patch))
        self.assertEqual(self.call("state")[1]["records"], [])

    def test_permission_and_isolation(self):
        with self.assertRaises(PermissionError):
            self.store.action("unknown","state",{})
        self.call("save",self.event)
        other = self.store.pair()
        self.assertEqual(self.store.action(other,"state",{})[1]["records"],[])
        self.store.action(other,"forget",{"id":self.event["id"]})
        self.assertEqual(len(self.call("state")[1]["records"]),1)

    def test_idempotence_and_learning_scope(self):
        self.assertEqual(self.call("save",self.event)[0],201)
        self.assertEqual(self.call("save",self.event)[0],200)
        self.assertEqual(self.call("save",dict(self.event, learn=True))[0],409)
        self.assertFalse(self.call("state")[1]["prediction"])
        learned=dict(self.event, id=str(uuid4()), learn=True)
        self.call("save",learned)
        self.assertEqual(self.call("state")[1]["prediction"]["sound"]["soft"],.5)
        self.call("forget",{"id":learned["id"]})
        self.assertEqual(self.call("save",learned)[0],410)
        self.assertFalse(self.call("state")[1]["prediction"])

    def test_expiry_and_revoke(self):
        event=dict(self.event, learn=True)
        self.call("save",event)
        self.now += bridge.DAY
        self.store.sweep()
        self.assertEqual(self.call("state")[1], {"records": [],"prediction":{},"muted":[]})
        self.assertEqual(self.call("save",event)[0],410)
        self.call("unpair")
        with self.assertRaises(PermissionError):
            self.call("save",event)

class MuteChannel(unittest.TestCase):
    setUp, call = MobileBridge.setUp, MobileBridge.call

    def learned(self, channel="sound", value="soft", key=None):
        event = dict(id=str(uuid4()), channel=channel, value=value, learn=True)
        self.assertEqual(self.store.action(key or self.key, "save", event)[0], 201)
        return event

    def test_mute_unlearns_and_recomputes(self):
        sound, light = self.learned(), self.learned("light", "dim")
        self.assertEqual(self.call("mute", {"channel": "sound"}), (200, {"unlearned": 1}))
        self.assertEqual(self.call("mute", {"channel": "sound"}), (200, {"unlearned": 0}))
        state = self.call("state")[1]
        self.assertEqual(state["muted"], ["sound"])
        self.assertNotIn("sound", state["prediction"])
        self.assertIn("light", state["prediction"])
        self.assertEqual([r["learn"] for r in state["records"] if r["id"] == sound["id"]], [False])
        self.assertEqual([r["learn"] for r in state["records"] if r["id"] == light["id"]], [True])

    def test_muted_save_is_423_and_identical_retry_is_200(self):
        stored = self.learned()
        self.call("mute", {"channel": "sound"})
        self.assertEqual(self.call("save", stored)[0], 200)
        self.assertEqual(self.call("save", dict(stored, value="strong"))[0], 409)
        fresh = dict(self.event, id=str(uuid4()), learn=True)
        self.assertEqual(self.call("save", fresh)[0], 423)
        self.assertEqual(self.call("save", dict(fresh, learn=False))[0], 201)
        self.assertEqual(self.call("save", dict(fresh, learn=False))[0], 200)
        self.assertEqual(self.call("save", dict(self.event, id=str(uuid4()), channel="light", value="dim", learn=True))[0], 201)
        self.assertFalse(self.call("state")[1]["prediction"].get("sound"))

    def test_unmute_allows_learning_again_without_restoring(self):
        stored = self.learned()
        self.call("mute", {"channel": "sound"})
        self.assertEqual(self.call("unmute", {"channel": "sound"}), (200, {}))
        self.assertEqual(self.call("state")[1]["muted"], [])
        self.assertFalse(self.call("state")[1]["prediction"])
        self.learned()
        self.assertEqual(self.call("state")[1]["prediction"]["sound"]["soft"], .5)
        self.assertEqual(stored["learn"], True)

    def test_mute_validation(self):
        for data in ({}, {"channel": "photo"}, {"channel": ["sound"]}, {"channel": 3}, {"channel": "sound", "x": 1}):
            for op in ("mute", "unmute"):
                with self.subTest(op=op, data=data), self.assertRaises(ValueError):
                    self.call(op, data)

    def test_mute_requires_auth_and_is_isolated(self):
        for op in ("mute", "unmute"):
            with self.assertRaises(PermissionError):
                self.store.action("unknown", op, {"channel": "sound"})
        other = self.store.pair()
        self.learned(key=other)
        self.call("mute", {"channel": "sound"})
        self.assertEqual(self.store.action(other, "state", {})[1]["muted"], [])
        self.assertEqual(self.store.action(other, "state", {})[1]["prediction"]["sound"]["soft"], .5)
        self.assertEqual(self.store.action(other, "save", dict(self.event, id=str(uuid4()), learn=True))[0], 201)

    def test_unpair_cascades_and_new_pairing_starts_unmuted(self):
        self.call("mute", {"channel": "light"})
        self.call("unpair")
        with bridge.closing(bridge.connect(self.store.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM muted").fetchone()[0], 0)
        self.assertEqual(self.store.action(self.store.pair(), "state", {})[1]["muted"], [])

    def test_existing_database_without_table_upgrades(self):
        path = Path(self.tmp.name) / "old.db"
        with bridge.closing(bridge.connect(path)) as db, db:
            db.executescript("CREATE TABLE devices(key TEXT PRIMARY KEY, label TEXT UNIQUE NOT NULL);"
                             "CREATE TABLE events(device TEXT REFERENCES devices(key) ON DELETE CASCADE, id TEXT, ch TEXT, val TEXT,"
                             " learn INTEGER, until INTEGER, PRIMARY KEY(device,id));")
        other = bridge.Store(path, lambda: self.now)
        token = other.pair()
        bridge.Store(path, lambda: self.now)  # second open stays idempotent
        self.assertEqual(other.action(token, "mute", {"channel": "dynamics"})[0], 200)
        self.assertEqual(other.action(token, "state", {})[1]["muted"], ["dynamics"])


if __name__ == "__main__":
    unittest.main()
