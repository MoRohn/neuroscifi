import tempfile
import unittest
from pathlib import Path
import numpy as np
from neuroscifi.lab.core import BODY,CAMERA,SPEAKER,balanced_ids,probabilities
from neuroscifi.legacy_live import open_store,add_event,status
from neuroscifi.lab.source_groups import group_log_likelihood
from neuroscifi.lab.human_counts import compute


class ModelChecks(unittest.TestCase):
    def test_symmetry_and_anchor(self):
        for i in range(8):
            self.assertEqual((CAMERA[i],SPEAKER[i]),(CAMERA[7-i],SPEAKER[7-i]))
            self.assertNotEqual(BODY[i],BODY[7-i])
        self.assertEqual(len(set(zip(CAMERA,SPEAKER,BODY))),8)

    def test_group_saturates(self):
        p=probabilities(group_log_likelihood(np.ones((100,1),int),.15,.1))
        self.assertAlmostEqual(float((p @ BODY)[0]),.85,places=10)

    def test_event_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            name=Path(t)/"learner.db"
            db=open_store(name)
            with db:
                db.execute("INSERT INTO epochs VALUES ('desk')")
            args=("desk","e1","camera",1,0,"camera-a")
            add_event(db,*args)
            before=status(db,"desk")
            add_event(db,*args)
            self.assertEqual(before,status(db,"desk"))
            with self.assertRaises(ValueError):
                add_event(db,"desk","e1","camera",1,1,"camera-a")
            db.close()
            db=open_store(name)
            self.assertEqual(before,status(db,"desk"))
            db.close()

    def test_unknown_scope(self):
        db=open_store(":memory:")
        with self.assertRaises(ValueError):
            status(db,"absent")
        db.close()

    def test_published_counts(self):
        self.assertAlmostEqual(compute(9,5,3,11)["fisher_two_sided"],.0542378965)

    def test_balanced(self):
        self.assertEqual(np.bincount(balanced_ids(24,np.random.default_rng(1))).tolist(),[3]*8)
