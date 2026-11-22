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


class Family(unittest.TestCase):
    """UAT S-02: a pairing family is capped, can be revoked as a whole, and is visible to the owner."""
    def setUp(self):
        root=tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        self.t=1795000000;self.s=bridge.Store(Path(root.name)/"db",lambda:self.t)
        self.a=self.s.pair()

    def link(self,sponsor):
        return self.s.pair(sponsor=bridge.fingerprint(sponsor))

    def test_family_device_cap(self):
        members=[self.a]+[self.link(self.a) for _ in range(bridge.FAMILY_DEVICES-1)]
        self.assertTrue(self.s.family_full(self.a))
        for sponsor in (self.a,members[-1]):
            with self.assertRaises(bridge.FamilyFull):
                self.link(sponsor)
        self.assertFalse(self.s.family_full(self.s.pair()),"another family is unaffected")
        self.s.action(members[2],"unpair",{})
        self.assertFalse(self.s.family_full(self.a))
        self.link(members[-1])

    def test_unpair_whole_family(self):
        b=self.link(self.a);c=self.link(b);other=self.s.pair()
        ep=dict(id=str(uuid4()),features={"goal":["read","owner"]},outcome="paused",learn=True)
        self.s.action(b,"episode",ep)
        for bad in ({"family":False},{"family":1},{"all":True}):
            with self.assertRaises(ValueError):self.s.action(c,"unpair",bad)
        self.assertEqual(self.s.action(c,"unpair",{"family":True}),(200,{}))
        for token in (self.a,b,c):
            with self.assertRaises(PermissionError):self.s.action(token,"state",{})
        self.assertEqual(self.s.action(other,"state",{})[0],200)
        with bridge.closing(bridge.connect(self.s.path)) as db:
            for table in ("episodes","members","family_retired","retired_episodes"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0],1 if table=="members" else 0,table)

    def test_pairings_list_and_family_erase(self):
        b=self.link(self.a);self.s.pair()
        from neuroscifi.instance import store
        instance=store.create(Path(self.s.path).parent/"individual.sqlite",self.t);self.addCleanup(instance.close)
        rows=self.s.pairings(instance)
        self.assertEqual(len(rows),3)
        (la,fa,sa,ca),(lb,fb,sb,cb),(lo,fo,so,co)=rows
        self.assertEqual((sa,sb,so),(None,la,None))
        self.assertEqual(fa,fb);self.assertNotEqual(fa,fo);self.assertEqual(len(fa),8)
        self.assertNotIn(fa,bridge.fingerprint(self.a))
        self.assertEqual({ca,cb,co},{self.t})
        self.assertEqual(self.s.erase(lb,family=True),2)
        self.assertEqual([r[0] for r in self.s.pairings()],[lo])
        with self.assertRaises(PermissionError):self.s.action(b,"state",{})
        self.assertEqual(self.s.erase("nonexistent",family=True),0)
