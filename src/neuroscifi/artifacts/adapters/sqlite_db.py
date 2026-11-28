"""SQLite databases to a `Bundle` of `Table`s (lane B1): header facts always; schema, row counts and a bounded
sample per table through a connection that cannot be made to run anything.

The connection (all of it is set up before the first statement touches the file's schema):
- the bytes are handed to `sqlite3.Connection.deserialize` on a fresh `:memory:` connection: nothing is written to
  disk, so there is no temporary file to remove however the work ends (stronger than a private copy on disk), and
  the caller's bytes are never modified;
- `SQLITE_DBCONFIG_DEFENSIVE` on, `SQLITE_DBCONFIG_TRUSTED_SCHEMA` off (also `PRAGMA trusted_schema=OFF`), triggers
  and views disabled (`SQLITE_DBCONFIG_ENABLE_TRIGGER` / `_VIEW` off), the FTS3 tokenizer hook off, extension loading
  never enabled, `PRAGMA query_only=ON`, `cell_size_check=ON`;
- limits through `Connection.setlimit`: one value at most 1 MiB, SQL at most 100 000 characters, expression depth 64,
  no attached databases;
- an authorizer that allows only SELECT, READ, the function `count` and `PRAGMA table_xinfo` (the declared columns),
  then denies everything else;
- a progress handler that interrupts any statement once `max_seconds` have passed.
Views and triggers are listed (name and the table they belong to) and never evaluated; virtual tables are listed
and not read (reading one would run its module). Each ordinary table gives its row count and a sample of at most
SAMPLE_ROWS rows (fewer when `max_elements` or `max_decoded_bytes`, charged per cell in bytes, runs out) as a
`Table`, of its stored columns only (a computed column is listed, never evaluated: fix lane F1); blob cells are not
decoded (missing, counted).
"""
from __future__ import annotations

import sqlite3
import struct
import time

from ..safe import Deadline, injection_notes
from ..safe_b1 import DecodeBudget
from ..types import Bundle, Decoded, Detected, Malformed, Member, Refused
from . import Adapter
from .tables import table_from_rows

HEADER = b"SQLite format 3\x00"
SAMPLE_ROWS = 1000
ENCODINGS = {1: "UTF-8", 2: "UTF-16le", 3: "UTF-16be"}
_ALLOWED_FUNCTIONS = {"count"}


def header_facts(data: bytes) -> dict:
    if len(data) < 100 or not data.startswith(HEADER):
        raise Malformed("the SQLite header is missing or cut short")
    (page_size,) = struct.unpack(">H", data[16:18])
    page_size = 65536 if page_size == 1 else page_size
    write_v, read_v = data[18], data[19]
    pages, freelist, schema_format, encoding, user_version, app_id, version = (
        struct.unpack(">I", data[28:32])[0], struct.unpack(">I", data[36:40])[0], struct.unpack(">I", data[44:48])[0],
        struct.unpack(">I", data[56:60])[0], struct.unpack(">i", data[60:64])[0], struct.unpack(">i", data[68:72])[0],
        struct.unpack(">I", data[96:100])[0])
    facts = {"page_size": page_size, "pages_in_header": pages, "file_pages": len(data) / page_size if page_size else 0,
             "freelist_pages": freelist, "schema_format": schema_format, "text_encoding": ENCODINGS.get(encoding),
             "user_version": user_version, "application_id": app_id, "wal_mode": write_v == 2 or read_v == 2,
             "written_by_sqlite": f"{version // 1000000}.{version // 1000 % 1000}.{version % 1000}" if version else None}
    if page_size < 512 or page_size & (page_size - 1):
        raise Malformed("the SQLite page size is not a power of two between 512 and 65536")
    return facts


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _authorizer(action, arg1, arg2, dbname, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _ALLOWED_FUNCTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() == "table_xinfo" and arg2:
        return sqlite3.SQLITE_OK      # reads the declared columns; evaluates nothing
    return sqlite3.SQLITE_DENY


def open_locked(data: bytes, deadline: Deadline) -> sqlite3.Connection:
    """A connection on a private in-memory copy of `data`, locked down as the module states."""
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=True)
    try:
        for name, value in (("SQLITE_DBCONFIG_DEFENSIVE", True), ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
                            ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False), ("SQLITE_DBCONFIG_ENABLE_VIEW", False),
                            ("SQLITE_DBCONFIG_ENABLE_FTS3_TOKENIZER", False),
                            ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False)):
            if hasattr(sqlite3, name):
                conn.setconfig(getattr(sqlite3, name), value)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1 << 20)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 1)
        conn.deserialize(data)
        for pragma in ("PRAGMA trusted_schema=OFF", "PRAGMA query_only=ON", "PRAGMA cell_size_check=ON",
                       "PRAGMA mmap_size=0"):
            conn.execute(pragma)
        end = time.monotonic() + deadline.remaining()
        conn.set_progress_handler(lambda: 1 if time.monotonic() > end else 0, 1000)
        conn.set_authorizer(_authorizer)
        return conn
    except BaseException:
        conn.close()
        raise


def read_sqlite(data: bytes, detected: Detected, limits) -> Decoded:
    facts = header_facts(data)
    deadline, budget = Deadline(limits.max_seconds), DecodeBudget(limits)
    notes = ["header: " + ", ".join(f"{k}={v}" for k, v in facts.items() if v not in (None, False))]
    if facts["wal_mode"]:
        notes.append("WAL-mode file: changes still in its -wal file (not given) are not here")
    if facts["file_pages"] != int(facts["file_pages"]):
        notes.append("the file is not a whole number of pages: it may be cut short")
    members: list[Member] = []
    partial = False
    try:
        conn = open_locked(data, deadline)
    except (sqlite3.Error, ValueError, OverflowError) as error:
        raise Malformed(f"SQLite could not open the bytes ({type(error).__name__})") from None
    try:
        try:
            schema = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        except sqlite3.OperationalError as error:
            if deadline.remaining() < 0:
                raise Refused("reading the schema took longer than allowed", rule="max_seconds",
                              reason="timeout") from None
            raise Malformed(f"the schema cannot be read ({type(error).__name__})") from None
        except sqlite3.DatabaseError as error:
            raise Malformed(f"the database is damaged ({type(error).__name__})") from None
        for kind, name, table, sql in schema[:limits.max_members]:
            if not isinstance(name, str):
                continue
            det = Detected("database", f"sqlite-{kind}", "application/vnd.sqlite3", "structure", "sqlite",
                           (f"a {kind} in the schema",))
            if kind in ("view", "trigger"):
                members.append(Member(name, 0, det, None, f"{kind} on {table!s}: listed, never evaluated"))
                continue
            if kind != "table" or name.startswith("sqlite_"):
                continue
            if deadline.remaining() < 0:
                partial = True
                notes.append("stopped: the time limit was reached before every table was read")
                break
            if isinstance(sql, str) and sql.lstrip().upper().startswith("CREATE VIRTUAL"):
                members.append(Member(name, 0, det, None, "virtual table: listed, not read (reading would run its "
                                                         "module)"))
                continue
            members.append(_table_member(conn, name, det, budget, deadline))
            if members[-1].note.startswith("stopped"):
                partial = True
        if len(schema) > limits.max_members:
            partial = True
            notes.append(f"the schema lists {len(schema)} objects: the first {limits.max_members} are read")
    finally:
        conn.close()
    notes.append("views and triggers are never evaluated; virtual tables are not read; blob cells are not decoded")
    bundle = Bundle(tuple(members))
    return Decoded(detected, bundle, tuple(notes) + injection_notes(bundle.text()), partial)


CELL_MAX = 1 << 20       # one value at most (SQLITE_LIMIT_LENGTH), lowered per table so a row fits what is left


def _cell_bytes(v) -> int:
    """What a fetched cell holds in memory, as charged: text by its characters at the width Python stores them."""
    if isinstance(v, str):
        return len(v) if v.isascii() else 4 * len(v)
    if isinstance(v, bytes):
        return len(v)
    return 8


def _table_member(conn, name, det, budget, deadline) -> Member:
    """One table's count and sample. Only STORED columns are selected (`PRAGMA table_xinfo`: hidden 0, or 3 for a
    stored generated column, whose value is on disk): a virtual generated column would be computed by the SELECT
    itself, and an innocuous built-in (printf, hex, zeroblob) can make each of its cells a megabyte (security review
    H1), so it is listed, not evaluated. Cells are charged in bytes, and the per-value length limit is lowered so one
    row cannot exceed what is left of the budget."""
    q = _quote(name)
    try:
        (count,) = conn.execute(f"SELECT count(*) FROM {q}").fetchone()
        info = conn.execute(f"PRAGMA table_xinfo({q})").fetchall()
        stored = [str(r[1]) for r in info if r[6] in (0, 3)]
        computed = sum(1 for r in info if r[6] not in (0, 3))
        if not stored:
            return Member(name, 0, det, None, f"{count} row(s); no stored column (computed columns are not "
                                              "evaluated)")
        room = max(0, min(SAMPLE_ROWS, budget.max_elements - budget.elements))
        left = max(0, budget.max_bytes - budget.bytes)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(1024, min(CELL_MAX, left // len(stored))))
        try:
            cur = conn.execute(f"SELECT {', '.join(_quote(c) for c in stored)} FROM {q} LIMIT {int(room)}")
            columns = [d[0] for d in cur.description or ()]
            rows, blobs, cut = [], 0, ""
            for row in cur:
                if not budget.fits_elements(len(row)):
                    cut = "element"
                    break
                size = sum(_cell_bytes(v) for v in row)
                if not budget.fits_bytes(size):
                    cut = "byte"
                    break
                budget.take_elements(len(row))
                budget.take_bytes(size)
                cells = []
                for v in row:
                    if isinstance(v, bytes):
                        blobs += 1
                        v = None
                    cells.append(v)
                rows.append(cells)
        finally:
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, CELL_MAX)
    except sqlite3.OperationalError as error:
        if deadline.remaining() < 0:
            return Member(name, 0, det, None, "stopped: the time limit was reached while reading this table")
        return Member(name, 0, det, None, f"not read ({type(error).__name__}: denied or damaged)")
    except sqlite3.DataError:            # SQLITE_TOOBIG: one value over the lowered length limit
        return Member(name, 0, det, None, "not read: a value is longer than the decode budget leaves room for")
    except sqlite3.DatabaseError as error:
        return Member(name, 0, det, None, f"not read: the table is damaged ({type(error).__name__})")
    table, tnotes = table_from_rows([columns] + rows, header=True, title=name, deadline=deadline)
    note = f"{count} row(s); {len(rows)} in the sample" + (f"; {blobs} blob cell(s) not decoded" if blobs else "")
    if computed:
        note += f"; {computed} computed column(s) listed, not evaluated"
    if tnotes:
        note += "; " + "; ".join(tnotes)
    if len(rows) < min(count, SAMPLE_ROWS):
        note = f"stopped early ({cut or 'element'} limit): " + note
    return Member(name, 0, det, table, note[:600])


class SqliteAdapter(Adapter):
    name = "b1-sqlite"
    formats = ("sqlite", "sqlite3")
    families = ("database",)

    def decode(self, data, detected, limits):
        return read_sqlite(data, detected, limits)


ADAPTERS = (SqliteAdapter,)

__all__ = ["ADAPTERS", "SqliteAdapter", "header_facts", "open_locked", "read_sqlite"]
