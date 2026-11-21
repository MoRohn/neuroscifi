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

    def start(self, port=None):
        proc = subprocess.Popen(
            [sys.executable, "-m", "neuroscifi.pocket.server", "--db", str(self.dir / "p.sqlite"),
             "--port", str(port or self.port), "--invitation-file", str(self.path)],
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

    def test_stale_invitation_after_a_crash_is_replaced(self):
        """UAT S-13: a bridge killed without cleanup leaves its 0600 invitation file; the next start replaces it."""
        proc = self.start()
        self.assertEqual(proc.stdout.readline().strip(), f"Invitation written to {self.path}")
        old = self.path.read_text().strip()
        proc.kill()
        proc.wait()
        self.assertTrue(self.path.exists(), "SIGKILL leaves the file behind")
        again = self.start()
        self.assertEqual(again.stdout.readline().strip(), f"Invitation written to {self.path}")
        new = self.path.read_text().strip()
        self.assertNotEqual(new, old)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.pair(old), 403)
        self.assertEqual(self.pair(new), 201)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")], [])

    def test_expired_invitation_file_is_replaced(self):
        self.path.write_text("A" * 32 + "\n")
        os.chmod(self.path, 0o600)
        os.utime(self.path, (time.time() - 600, time.time() - 600))
        proc = self.start()
        self.assertEqual(proc.stdout.readline().strip(), f"Invitation written to {self.path}")
        self.assertNotEqual(self.path.read_text().strip(), "A" * 32)

    def test_live_bridge_invitation_is_not_replaced(self):
        first = self.start()
        first.stdout.readline()
        code = self.path.read_text()
        second = self.start(port=free_port())
        _, err = second.communicate(timeout=15)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("never overwritten", err)
        self.assertEqual(self.path.read_text(), code)

    def test_unrecognised_0600_file_is_not_replaced(self):
        self.path.write_text("precious\n")
        os.chmod(self.path, 0o600)
        os.utime(self.path, (time.time() - 600, time.time() - 600))
        proc = self.start()
        proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.path.read_text(), "precious\n")

    def test_symlink_to_stale_looking_file_is_refused(self):
        target = self.dir / "target"
        target.write_text("A" * 32 + "\n")
        os.chmod(target, 0o600)
        os.utime(target, (time.time() - 600, time.time() - 600))
        self.path.symlink_to(target)
        proc = self.start()
        proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_text(), "A" * 32 + "\n")

    def test_busy_port_is_one_line(self):
        """UAT U-31: like the console, a busy port is one line on stderr, not a traceback."""
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", self.port))
            busy.listen()
            proc = subprocess.run([sys.executable, "-m", "neuroscifi.pocket.server", "--db", str(self.dir / "p.sqlite"),
                                   "--port", str(self.port)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr.strip().splitlines(), [f"nsf: cannot listen on 127.0.0.1:{self.port}: "
                                                            "Address already in use"])
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
