"""ADR-065 (Wave 4 lane D): the console's research and knowledge views carry the caution (display only)."""
import json
import threading

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.console import server
from neuroscifi.instance import store

from test_console_api import TOKEN, Client

T0, DAY, N = adv.T0, adv.DAY, adv._SN


def test_the_console_shows_the_caution_on_answers_knowledge_and_the_question_about_an_item():
    with adv._s_individual("console-caution", **adv.LANE_S_OFF) as (db, path):
        out = adv._s_look(db, T0, [("relnotes.example", f"{N} 1.5.0 is the latest {N} release.", T0 - DAY, None),
                                   ("mirror.example", f"{N} 1.5.0 is the latest {N} release.", T0 - DAY, None),
                                   ("pkgwatch.example", f"As of this week, the newest {N} release is {N} 1.5.0, its "
                                                        "notes say.", T0 - DAY, None)])
        [kid] = out["provisional"]
        rows = db.execute("SELECT id, task_id, source_url, text FROM web_claims WHERE claim_values LIKE '%1.5.0%' "
                          "ORDER BY rowid").fetchall()
        task_id = rows[0][1]
        report = {"answer": [{"type": "quote", "text": rows[0][3], "citations": [1, 2], "corroborating": [3]}],
                  "findings": [{"statement": rows[0][3], "claims": [r[0] for r in rows]}],
                  "sources": [{"n": i + 1, "url": r[2]} for i, r in enumerate(rows)]}
        with store.write(db):
            db.execute("UPDATE web_tasks SET status='completed', result=? WHERE id=?", (json.dumps(report), task_id))
        if not db.execute("SELECT 1 FROM pending WHERE scope=?", (f"web:knowledge:{kid}",)).fetchone():
            with store.write(db):
                db.execute("INSERT INTO pending(epoch, kind, scope, question, created_at) VALUES(NULL, 'ask', ?, ?, ?)",
                           (f"web:knowledge:{kid}", "Accept this knowledge?", T0))
        srv = server.ConsoleServer(path, 0, token=TOKEN, clock=lambda: T0 + 10)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = Client(srv, path)
            task = c.ok("GET", f"/api/web/tasks/{task_id}")
            assert "count as 2 sources" in task["caution"]["answer"][0]["text"]
            items = c.ok("GET", "/api/web/knowledge")["items"]
            assert [i["caution"]["pages"] for i in items if i["id"] == kid] == [3]
            [asked] = [q for q in c.ok("GET", "/api/pending") if q["scope"] == f"web:knowledge:{kid}"]
            assert "count as 2 sources" in asked["caution"]["text"]
        finally:
            srv.shutdown()
            srv.server_close()
