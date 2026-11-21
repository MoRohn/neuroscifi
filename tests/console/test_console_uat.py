"""Owner console server fixes from UAT (lane L3): error pages carry the security headers (S-12), monitor routes
refuse while web access is off (C-05, API_CONTRACTS §8) and a 2000-character CJK question fits (C-13)."""
import json
import socket

from neuroscifi.console import server
from test_console_api import TOKEN, console  # noqa: F401  (fixture)
from test_console_web import enable, idle, instance, web  # noqa: F401  (fixtures)

CJK = ("ヴァルネル川の長さは何キロメートルですか" * 100)[:2000]


def raw(port, data):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(data)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
    head, _, body = out.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    return int(lines[0].split()[1]), headers, body


def test_error_pages_carry_security_headers_and_no_version(console):  # noqa: F811
    for request, code in ((b"OPTIONS / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n", 501),
                          (b"GET / HTTP/1.1\r\nX-Long: " + b"a" * 70000 + b"\r\n\r\n", 431),
                          (b"GET / HTTP/1.1\r\n" + b"".join(b"X-%d: 1\r\n" % k for k in range(120)) + b"\r\n", 431)):
        status, headers, body = raw(console.port, request)
        assert status == code, request[:20]
        assert headers["content-security-policy"] == server.HEADERS["Content-Security-Policy"]
        assert headers["x-content-type-options"] == "nosniff" and headers["cache-control"] == "no-store"
        assert headers["content-type"] == "application/json"
        assert headers["server"] == "nsf-console", "no Python or library version"
        assert set(json.loads(body)) == {"error"} and b"<" not in body


def test_monitor_routes_refuse_while_web_access_is_off(idle):  # noqa: F811
    c = idle
    status, data = c.post("/api/web/monitors", {"topic": "Zephyr compiler releases", "interval_hours": 24})
    assert status == 409 and "disabled" in data["error"]
    enable(c)
    mon = c.ok("POST", "/api/web/monitors", {"topic": "Zephyr compiler releases", "interval_hours": 24})
    assert c.ok("POST", "/api/web/config", {"enabled": False})["config"]["enabled"] is False
    for verb in ("pause", "resume", "stop"):
        status, data = c.post(f"/api/web/monitors/{mon['id']}/{verb}", {})
        assert status == 409 and "disabled" in data["error"], verb
    assert c.ok("GET", "/api/web/monitors")[0]["status"] == "active"   # reads of stored memory still work


def test_a_2000_character_cjk_question_fits(idle):  # noqa: F811
    c = idle
    enable(c)
    assert len(json.dumps({"objective": CJK}, ensure_ascii=False).encode()) > server.MAX_BODY
    status, _, data = c.call("POST", "/api/web/tasks", raw=json.dumps({"objective": CJK}, ensure_ascii=False).encode(),
                             headers={"Content-Type": "application/json"})
    assert status == 200 and data["status"] == "queued", data
    status, _, data = c.call("POST", "/api/web/monitors", raw=json.dumps(
        {"topic": CJK, "interval_hours": 24}, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
    assert status == 200, data
    status, data = c.post("/api/web/tasks", {"objective": "x" * (server.MAX_TEXT_BODY + 1)})
    assert status == 413 and str(server.MAX_TEXT_BODY) in data["error"]
    status, data = c.post("/api/epochs", {"name": "e1", "note": CJK})   # every other route keeps 4 KiB
    assert status == 413 and "4096" in data["error"]
