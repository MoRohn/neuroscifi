"""`nsf pocket serve --invitation-file`: the one-use code goes to a private file, never to stdout."""
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class InvitationFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "invitation"
        self.port = free_port()

    def start(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "neuroscifi.pocket.server", "--db", str(self.dir / "p.sqlite"),
             "--port", str(self.port), "--invitation-file", str(self.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(), proc.stdout.close(), proc.stderr.close()))
        return proc

    def wait_for_file(self, proc):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.path.exists() or proc.poll() is not None:
                return
            time.sleep(0.05)
        self.fail("server did not write the invitation file")

    def pair(self, code):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps({"code": code})
        conn.request("POST", "/api/pair", body, {"Content-Type": "application/json", "Content-Length": str(len(body))})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_mode_stdout_and_consumption(self):
        proc = self.start()
        line = proc.stdout.readline().strip()
        self.assertEqual(line, f"Invitation written to {self.path}")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        code = self.path.read_text().strip()
        self.assertGreater(len(code), 20)
        self.assertEqual(self.pair("wrong"), 403)
        self.assertTrue(self.path.exists(), "a failed attempt must not consume the invitation")
        self.assertEqual(self.pair(code), 201)
        self.assertFalse(self.path.exists(), "a consumed invitation file must be removed")
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertNotIn(code, line + out + err)

    def test_removed_on_shutdown(self):
        proc = self.start()
        proc.stdout.readline()
        code = self.path.read_text().strip()
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertFalse(self.path.exists())
        self.assertNotIn(code, out + err)

    def test_refuses_to_overwrite(self):
        self.path.write_text("precious")
        os.chmod(self.path, 0o640)
        proc = self.start()
        _, err = proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("never overwritten", err)
        self.assertEqual(self.path.read_text(), "precious")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_refuses_symlink(self):
        target = self.dir / "target"
        self.path.symlink_to(target)
        proc = self.start()
        proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
