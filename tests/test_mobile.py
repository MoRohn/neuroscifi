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
        self.assertEqual(self.call("state")[1], {"records": [],"prediction":{}})
        self.assertEqual(self.call("save",event)[0],410)
        self.call("unpair")
        with self.assertRaises(PermissionError):
            self.call("save",event)

if __name__ == "__main__":
    unittest.main()
