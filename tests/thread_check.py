"""The session-end check of tests/conftest.py: no thread of NeuroSciFi may be alive when the test session ends.

A server, hub or bridge that a test starts and does not close leaves its threads running until the test process
ends. Thirty-five of them had gathered by the time one test forked six children, and a forked child inherits every
lock such a thread holds (that test hung once on CI). The check fails the session when a thread of NeuroSciFi is
still alive at its end, naming the test file that started it, so a new leak is found the day it is written. A thread
is "of NeuroSciFi" when its name starts with nsf- or its target is defined in the package. Its own module, so that a
test can exercise it (every tests/**/conftest.py is a module named `conftest`)."""
import threading
import time

# Names that may stay alive, each with its reason. Empty: every thread the product starts ends with what started it.
EXPECTED_THREADS: tuple = ()
LEAK_GRACE = 10.0              # seconds a thread that is just finishing (a reply, a write) may take

BORN: dict = {}                # thread -> the test file during which it appeared


def ours(thread) -> bool:
    target = getattr(thread, "_target", None)
    module = getattr(target, "__module__", None) or type(thread).__module__ or ""
    return thread.name.startswith("nsf-") or module.startswith("neuroscifi")


def describe(thread) -> str:
    target = getattr(thread, "_target", None)
    return f"{thread.name} ({getattr(target, '__qualname__', type(thread).__name__)})"


def note_births(before, nodeid) -> None:
    """Record, for every thread of ours that appeared since `before`, the test file that started it."""
    for thread in threading.enumerate():
        if thread not in before and ours(thread):
            BORN.setdefault(thread, nodeid.split("::")[0])


def left_behind(grace=None) -> list[str]:
    """The threads of ours still alive after `grace` seconds (default LEAK_GRACE), described with their origin."""
    def left():
        return [t for t in threading.enumerate() if t.is_alive() and ours(t) and t is not threading.current_thread()
                and not (EXPECTED_THREADS and t.name.startswith(EXPECTED_THREADS))]
    deadline = time.monotonic() + (LEAK_GRACE if grace is None else grace)
    while left() and time.monotonic() < deadline:
        time.sleep(0.1)
    return sorted(f"  {describe(t)}   started in {BORN.get(t, 'a fixture or an earlier session-scoped setup')}"
                  for t in left())


MESSAGE = ("threads of NeuroSciFi are still alive at the end of the test session: something a test started was not "
           "closed (a server: server_close(); a voice hub: close(); a bridge run in a thread: main(started=...) and "
           "server.shutdown()):\n")
