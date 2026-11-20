import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
import numpy as np
from neuroscifi import archive, graph_store
from neuroscifi.legacy_live import open_store, add_event, status
from neuroscifi.lab.context_recall import GraphRecall

class CompressionTests(unittest.TestCase):
    def test_every_codec_preserves_odd_bytes(self):
        raw=bytes(range(255))*3
        for method in archive.METHODS:
            with self.subTest(method=method):
                encoded=archive.encode(raw, method)
                self.assertEqual(archive.decode(encoded,method,len(raw)),raw)

    def test_block_boundaries_empty_and_corruption(self):
        for raw in (b"", b"x", b"z"*(archive.BLOCK+1)):
            dest=io.BytesIO()
            archive.pack(io.BytesIO(raw), dest)
            blob=dest.getvalue()
            out=io.BytesIO()
            archive.unpack(io.BytesIO(blob),out)
            self.assertEqual(out.getvalue(),raw)
            for broken in (blob[:-1],blob+b"?",blob[:7]+bytes([blob[7]^32])+blob[8:]):
                with self.assertRaises(ValueError):
                    archive.unpack(io.BytesIO(broken),io.BytesIO())

    def test_live_backup_with_open_writer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            live=open_store(root/"state.db")
            with live: live.execute("INSERT INTO epochs VALUES ('e')")
            add_event(live,"e","event","camera",0,1,"camera1")
            archive.convert("checkpoint",root/"state.db",root/"backup")
            archive.convert("unpack",root/"backup",root/"restored")
            copy=sqlite3.connect(root/"restored")
            self.assertEqual(status(live,"e"),status(copy,"e"))
            copy.close()
            live.close()
            with self.assertRaises(FileExistsError):
                archive.convert("unpack",root/"backup",root/"restored")
            (root/"bad").write_bytes((root/"backup").read_bytes()[:-3])
            with self.assertRaises(ValueError):
                archive.convert("unpack",root/"bad",root/"incomplete")
            self.assertFalse((root/"incomplete").exists())

    def test_graph_formats(self):
        for cols in (3,257):
            a=np.zeros((3,cols),dtype=np.float32)
            a[0,-1]=-.37
            a[2,1]=.2
            for bits in (4,8,32):
                blob=graph_store.encode(a,1,bits)
                recovered=graph_store.decode(blob)
                np.testing.assert_array_equal(a,recovered)
                with self.assertRaises(ValueError): graph_store.decode(blob[:-1])

    def test_graph_selection_uses_past_only(self):
        matrices=[np.array([[v]],dtype="f4") for v in (1.,-1.)]
        s=GraphRecall([graph_store.encode(m,1,4) for m in matrices],history=1)
        first,p=s.predict(np.ones(1))
        self.assertEqual(first,1)
        s.observe(p,np.ones(1))
        self.assertEqual(s.predict(np.ones(1))[0],0)
