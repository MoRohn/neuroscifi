"""What one racing process does in test_autonomy's cross-process test. A module of its own, with no test imports:
the processes are started fresh (`spawn`), not forked, and each imports only this.

Why not fork: the test process holds threads of earlier tests, and a forked child inherits every lock one of them
held at that instant with nobody left to release it. Such a child never reached the barrier, the others waited on
it for ever, and on one CI run the six of them held the job's log until its time limit."""
from types import SimpleNamespace

from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, scheduler


BARRIER_TIMEOUT = 120.0     # every process has started and opened the file well within this, even on a slow runner


def race(path, n, now, barrier, out):
    """Wait for all six, then claim. Whatever happens is reported on `out` as (n, task id | None, error | None):
    a process that fails says why instead of leaving the test to wait for it."""
    try:
        db = store.open_existing(path)
        try:
            barrier.wait(BARRIER_TIMEOUT)
            holder = f"proc-{n}"
            claimed = scheduler.claim_next(db, holder, now)
            if claimed is None:
                out.put((n, None, None))
                return
            task = orchestrator._claim(db, claimed[0], SimpleNamespace(holder=holder, clock=lambda: now),
                                       resume=False)
            out.put((n, task["id"] if task else None, None))
        finally:
            db.close()
    except BaseException as error:          # reported, not swallowed: the test fails with it
        import traceback
        out.put((n, None, "".join(traceback.format_exception(error))[-2000:]))
