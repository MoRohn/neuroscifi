import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from test_mobile import bridge

class Field(unittest.TestCase):
    def setUp(self):
        root=tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        self.t=100;self.s=bridge.Store(Path(root.name)/"db",lambda:self.t)
        self.a=self.s.pair()
        self.b=self.s.pair(sponsor=bridge.fingerprint(self.a))
        self.other=self.s.pair()
        self.event=dict(id=str(uuid4()),features={"goal":["read","owner"],"power":["standard","ios"]},outcome="paused",learn=False)
    def test_link_and_forget(self):
        self.assertEqual(self.s.action(self.a,"episode",self.event)[0],201)
        self.assertEqual(self.s.action(self.b,"episode",self.event)[0],200)
        self.assertEqual(self.s.action(self.b,"episode",dict(self.event,learn=True))[0],409)
        self.assertEqual(self.s.action(self.other,"episodes",{})[1]["episodes"],[])
        self.assertEqual(self.s.action(self.b,"episodes",{})[1]["episodes"],[self.event])
        self.s.action(self.b,"forget_episode",{"id":self.event["id"]})
        self.assertEqual(self.s.action(self.a,"episode",self.event)[0],410)
    def test_schema_and_expiry(self):
        for f in ({"url":"example"},{"power":["name","ios"]},{"sound":["soft","ios"]},{}):
            with self.assertRaises(ValueError): self.s.action(self.a,"episode",dict(self.event,features=f))
        self.s.action(self.b,"episode",self.event);self.t+=86400
        self.assertEqual(self.s.action(self.a,"episodes",{})[1]["episodes"],[])
        self.assertEqual(self.s.action(self.b,"episode",self.event)[0],410)
    def test_removed_origin_cannot_be_replayed_by_linked_device(self):
        self.s.action(self.a,"episode",self.event)
        self.s.action(self.a,"unpair",{})
        self.assertEqual(self.s.action(self.b,"episodes",{})[1]["episodes"],[])
        self.assertEqual(self.s.action(self.b,"episode",self.event)[0],410)

    def test_revoked_sponsor(self):
        self.s.action(self.a,"unpair",{})
        with self.assertRaises(PermissionError):self.s.pair(sponsor=bridge.fingerprint(self.a))
