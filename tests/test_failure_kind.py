"""instance.errors.failure_kind: a failure described for a log line without its message (Wave 2, review2b N5, 2c D3)."""
import sqlite3

from neuroscifi.instance.errors import failure_kind
from neuroscifi.voice import hub as vhub


def test_a_database_and_an_os_error_are_named_without_their_message():
    db = sqlite3.connect(":memory:")
    try:
        db.execute("SELECT * FROM no_such_table_SECRET")
    except sqlite3.OperationalError as exc:
        assert failure_kind(exc) == "OperationalError SQLITE_ERROR"
    assert failure_kind(OSError(28, "No space left on device: /home/SECRET")) == "OSError ENOSPC"
    assert failure_kind(ValueError("the SECRET subject")) == "ValueError"


def test_it_cannot_raise_inside_a_handler_that_must_not_fail():
    """review2c D3: an exception whose attribute raises is described by its type alone."""
    class Awkward(OSError):
        @property
        def errno(self):
            raise RuntimeError("SECRET")

        @property
        def sqlite_errorname(self):
            raise RuntimeError("SECRET")
    assert failure_kind(Awkward()) == "Awkward"


def test_the_retry_notice_promises_an_answer_only_where_there_is_one():
    """review2c R1: a cancelled or failed task was announced as "Your research answer is ready"."""
    assert vhub.claim_notice("completed") == vhub.claim_notice("partial") == vhub.CLAIM_NOTICE_ANSWER
    assert vhub.claim_notice("cancelled") == vhub.claim_notice("failed") == vhub.CLAIM_NOTICE_ENDED
    assert "answer" not in vhub.CLAIM_NOTICE_ENDED
