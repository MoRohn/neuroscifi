import contextlib
import io
import re
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

    def test_forget_before_save_retires_the_id(self):
        self.assertEqual(self.call("forget",{"id":self.event["id"]}),(200,{}))
        self.assertEqual(self.call("save",self.event)[0],410)
        self.assertEqual(self.call("state")[1]["records"],[])

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

    def test_unmute_then_identical_retry_of_event_stored_while_muted_is_200(self):
        stored = self.learned()
        self.call("mute", {"channel": "sound"})
        self.call("unmute", {"channel": "sound"})
        self.assertEqual(self.call("save", stored)[0], 200)
        self.assertEqual(self.call("save", dict(stored, value="strong"))[0], 409)

    def test_old_events_table_gains_asked_column_once(self):
        path = Path(self.tmp.name) / "older.db"
        with bridge.closing(bridge.connect(path)) as db, db:
            db.executescript("CREATE TABLE devices(key TEXT PRIMARY KEY, label TEXT UNIQUE NOT NULL);"
                             "CREATE TABLE events(device TEXT REFERENCES devices(key) ON DELETE CASCADE, id TEXT, ch TEXT, val TEXT,"
                             " learn INTEGER, until INTEGER, PRIMARY KEY(device,id));")
        bridge.Store(path)
        bridge.Store(path)
        with bridge.closing(bridge.connect(path)) as db:
            self.assertEqual([r[1] for r in db.execute("PRAGMA table_info(events)")].count("asked"), 1)

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


class OwnerCommands(unittest.TestCase):
    """UAT S-02: `nsf pocket list-pairings` shows family, sponsor and creation time; `erase-pairing --family`."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "m.sqlite")
        self.store = bridge.Store(self.db)
        self.a = self.store.pair()
        self.b = self.store.pair(sponsor=bridge.fingerprint(self.a))
        self.other = self.store.pair()

    def nsf(self, *argv):
        from neuroscifi import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["pocket", *argv, "--db", self.db])
        return out.getvalue().splitlines()

    def instance(self):
        import time
        from neuroscifi.instance import store
        path = Path(self.tmp.name) / "individual.sqlite"
        if not path.exists():
            store.create(path, time.time()).close()
        return str(path)

    def test_family_tags_are_the_consoles(self):
        """R6 D4: list-pairings showed family=36554896 while the console and `nsf web node list` showed abe0d75f for
        the same family. With the individual's database (--instance-db or NSF_DB) both show the salted digest."""
        from contextlib import closing
        from unittest import mock
        from neuroscifi.instance import store
        from neuroscifi.webcortex import state
        path = self.instance()
        with closing(store.open_existing(path)) as db:
            console = state.family_digest(db, self.store.family(self.a))[:8]   # what nodes.pending/listing show
            other = state.family_digest(db, self.store.family(self.other))[:8]
        shown = [line.split()[1] for line in self.nsf("list-pairings", "--instance-db", path)]
        self.assertEqual(shown, [f"family={console}", f"family={console}", f"family={other}"])
        with mock.patch.dict("os.environ", {"NSF_DB": path}):
            from neuroscifi import cli
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["pocket", "list-pairings", "--db", self.db])
        self.assertEqual([line.split()[1] for line in out.getvalue().splitlines()], shown)
        err = io.StringIO()
        with mock.patch.dict("os.environ", {"NSF_DB": ""}), contextlib.redirect_stderr(err):
            from neuroscifi.pocket import cli as pocket_cli
            import argparse
            parser = argparse.ArgumentParser()
            pocket_cli.register(parser.add_subparsers(dest="cmd"))
            a = parser.parse_args(["pocket", "list-pairings", "--db", self.db])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                a.handler(a)
        self.assertEqual({line.split()[1] for line in out.getvalue().splitlines()}, {"family=-"})
        self.assertIn("--instance-db", err.getvalue())

    def test_list_and_erase_family(self):
        lines = self.nsf("list-pairings", "--instance-db", self.instance())
        self.assertEqual(len(lines), 3)
        pattern = re.compile(r"^([0-9a-f]{12})  family=([0-9a-f]{8})  sponsor=(invitation|[0-9a-f]{12})  "
                             r"created=\d{4}-\d\d-\d\dT\d\d:\d\dZ$")
        rows = [pattern.match(line).groups() for line in lines]
        (la, fa, sa), (lb, fb, sb), (lo, fo, so) = rows
        self.assertEqual((sa, sb, so), ("invitation", la, "invitation"))
        self.assertEqual(fa, fb)
        self.assertNotEqual(fa, fo)
        self.assertEqual(self.nsf("erase-pairing", lb, "--family"), ["Erased pairings: 2"])
        self.assertEqual([line.split()[0] for line in self.nsf("list-pairings", "--instance-db", self.instance())], [lo])
        for token in (self.a, self.b):
            with self.assertRaises(PermissionError):
                self.store.action(token, "state", {})
        self.assertEqual(self.nsf("erase-pairing", lo), ["Erased pairings: 1"])

    def test_old_members_table_gains_sponsor_and_created(self):
        path = Path(self.tmp.name) / "old-members.db"
        with bridge.closing(bridge.connect(path)) as db, db:
            db.executescript("CREATE TABLE devices(key TEXT PRIMARY KEY, label TEXT UNIQUE NOT NULL);"
                             "INSERT INTO devices VALUES('k','aaaaaaaaaaaa');"
                             "CREATE TABLE members(device TEXT PRIMARY KEY REFERENCES devices(key) ON DELETE CASCADE,"
                             " family TEXT NOT NULL);")
        old = bridge.Store(path)
        bridge.Store(path)
        self.assertEqual(old.pairings(), [("aaaaaaaaaaaa", "-", None, None)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.main(["--db", str(path), "--list-pairings"])
        self.assertIn("sponsor=invitation  created=unknown", out.getvalue())


if __name__ == "__main__":
    unittest.main()
