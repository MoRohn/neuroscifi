"""Token accounting: one row per model call in `<db stem>.inference.sqlite` (mode 0600), next to the instance.

A row holds when, what for (purpose), which model and endpoint kind, the outcome, prompt/completion tokens as the
server reported them, latency, time to first token, attempts. It never holds prompt or answer text, so it is
operational telemetry, not memory: it is not in checkpoints and `forget` does not need it. Deleting the file loses
only the statistics.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY, at REAL NOT NULL, purpose TEXT NOT NULL, model TEXT, locality TEXT, stream INTEGER,
  status TEXT NOT NULL, error TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms REAL,
  first_token_ms REAL, attempts INTEGER, tokens_per_second REAL, task_id TEXT)
"""
COLUMNS = ("at", "purpose", "model", "locality", "stream", "status", "error", "prompt_tokens", "completion_tokens",
           "latency_ms", "first_token_ms", "attempts", "tokens_per_second", "task_id",
           "provider", "priority", "cached_tokens")
# Added after 0.7.0 (an older ledger gains them on open): which endpoint served the call (chat, embed, rerank), the
# scheduling priority it carried (NULL: none sent) and the prompt tokens the server took from its prefix cache
# (NULL: the server did not say; vLLM says with --enable-prompt-tokens-details).
ADDED = {"provider": "TEXT", "priority": "INTEGER", "cached_tokens": "INTEGER"}


def ledger_path(db_path) -> Path:
    path = Path(db_path).resolve()
    return path.with_name(f"{path.stem}.inference.sqlite")


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        with self._connect() as db:
            db.execute(SCHEMA)
            have = {row[1] for row in db.execute("PRAGMA table_info(calls)")}
            for name, kind in ADDED.items():
                if name not in have:
                    try:
                        db.execute(f"ALTER TABLE calls ADD COLUMN {name} {kind}")
                    except sqlite3.OperationalError:     # another process added it between the two statements
                        pass
            db.execute("CREATE INDEX IF NOT EXISTS calls_at ON calls(at)")   # Presence reads the newest rows often

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def record(self, row: dict) -> None:
        values = [row.get(c) for c in COLUMNS]
        values[COLUMNS.index("stream")] = int(bool(row.get("stream")))
        with self.lock:
            db = self._connect()
            try:
                with db:
                    db.execute(f"INSERT INTO calls({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})",
                               values)
            finally:
                db.close()

    def recent(self, limit=20) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute(f"SELECT id,{','.join(COLUMNS)} FROM calls ORDER BY id DESC LIMIT ?",
                              (int(limit),)).fetchall()
        finally:
            db.close()
        return [dict(zip(("id", *COLUMNS), r)) for r in rows]

    def summary(self, since: float | None = None) -> dict:
        db = self._connect()
        try:
            row = db.execute("SELECT count(*), coalesce(sum(status='ok'),0), coalesce(sum(prompt_tokens),0), "
                             "coalesce(sum(completion_tokens),0), avg(latency_ms), avg(first_token_ms), "
                             "avg(tokens_per_second), sum(cached_tokens), "
                             "sum(CASE WHEN cached_tokens IS NOT NULL THEN prompt_tokens END) "
                             "FROM calls WHERE at >= ?",
                             (since if since is not None else float("-inf"),)).fetchone()
            by_purpose = db.execute("SELECT purpose, count(*), avg(latency_ms) FROM calls WHERE at >= ? AND "
                                    "status='ok' GROUP BY purpose ORDER BY purpose",
                                    (since if since is not None else float("-inf"),)).fetchall()
        finally:
            db.close()
        keys = ("calls", "ok", "prompt_tokens", "completion_tokens", "avg_latency_ms", "avg_first_token_ms",
                "avg_tokens_per_second")
        out = dict(zip(keys, row[:7]))
        # prefix cache: only over the calls whose server reported it (never a guess for the others)
        out["cached_prompt_tokens"] = row[7]
        out["cached_share"] = round(row[7] / row[8], 3) if row[7] is not None and row[8] else None
        out["by_purpose"] = {p: {"calls": n, "avg_latency_ms": None if ms is None else round(ms, 1)}
                             for p, n, ms in by_purpose}
        for k in ("avg_latency_ms", "avg_first_token_ms", "avg_tokens_per_second"):
            out[k] = None if out[k] is None else round(out[k], 1)
        return out


__all__ = ["Ledger", "ledger_path"]
