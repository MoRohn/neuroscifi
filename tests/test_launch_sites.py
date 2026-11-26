"""Every place product code starts a thread, a timer, a process, a server loop or a waiting loop is classified here
(OMNIMIND lane S; audit-cognition hazard 5, audit-runtime H10).

tests/webcortex/test_background.py finds the launches of the research entry points. This scan is wider: it finds every
call that starts concurrent or repeated work, whatever it runs, and requires each to be in SITES with one of three
classes and a one-line reason:

  registered  runs without being asked, and is a background.py activity (or RUNNER) that the owner's quiet switch
              governs (background.allowed) or that is registered as maintenance or delivery
  request     lives inside one request or call (the owner's, or a registered activity's) and ends with it
  owner       started by an owner's command (nsf start, nsf console, nsf pocket serve, a selftest, a benchmark)

A new site that is not in SITES fails the test, and so does an entry whose site is gone or whose count changed.
"""
import ast
from collections import Counter
from pathlib import Path

from neuroscifi import background

SRC = Path(__file__).resolve().parents[1] / "src" / "neuroscifi"
CLASSES = ("registered", "request", "owner")
# module -> {attribute: kind}
STARTERS = {
    "threading": {"Thread": "thread", "Timer": "timer"},
    "_thread": {"start_new_thread": "thread"},
    "subprocess": {n: "process" for n in ("Popen", "run", "call", "check_call", "check_output", "getoutput",
                                          "getstatusoutput")},
    "os": {n: "process" for n in ("fork", "forkpty", "system", "popen", "posix_spawn", "posix_spawnp", "spawnv",
                                  "spawnve", "spawnvp", "spawnl", "spawnlp", "execv", "execve", "execvp", "execvpe",
                                  "execl", "execlp")},
    "multiprocessing": {"Process": "process", "Pool": "process"},
    "concurrent.futures": {"ThreadPoolExecutor": "executor", "ProcessPoolExecutor": "executor"},
    "asyncio": {"create_subprocess_exec": "process", "create_subprocess_shell": "process", "run": "loop"},
    "signal": {"setitimer": "timer", "alarm": "timer"},
    "sched": {"scheduler": "loop"},
}
WAITS = {"sleep", "wait", "select", "poll", "serve_forever"}


def launch_sites(tree, module):
    """Counter of (module:qualname, kind) for every launch in `tree`: a thread, timer, process, executor or
    serve_forever call, a class derived from Thread or Process, and a while loop that waits (sleep, wait, select,
    poll) in its test or body."""
    aliases = {}           # local name -> module for `import x as y`
    names = {}             # local name -> (module, attribute) for `from x import y as z`
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in STARTERS:
                    aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                if a.name == "concurrent.futures" and a.asname:
                    aliases[a.asname] = "concurrent.futures"
        elif isinstance(node, ast.ImportFrom) and node.module in STARTERS:
            for a in node.names:
                if a.name in STARTERS[node.module]:
                    names[a.asname or a.name] = (node.module, a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "concurrent":
            for a in node.names:
                if a.name == "futures":
                    aliases[a.asname or "futures"] = "concurrent.futures"

    def kind_of(func):
        if isinstance(func, ast.Name) and func.id in names:
            mod, attr = names[func.id]
            return STARTERS[mod][attr]
        if isinstance(func, ast.Attribute):
            if func.attr == "serve_forever":
                return "server"
            base = func.value
            if isinstance(base, ast.Name):
                mod = aliases.get(base.id, base.id if base.id in STARTERS else None)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                mod = f"{base.value.id}.{base.attr}"
                mod = mod if mod in STARTERS else None
            else:
                mod = None
            if mod in STARTERS and func.attr in STARTERS[mod]:
                return STARTERS[mod][func.attr]
        return None

    def waits(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) in WAITS:
                    return True
        return False

    found = Counter()

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
                if isinstance(child, ast.ClassDef) and any(
                        kind_of(ast.Attribute(value=b.value, attr=b.attr) if isinstance(b, ast.Attribute) else b)
                        in ("thread", "process") for b in child.bases):
                    found[(f"{module}:{inner}", "thread subclass")] += 1
            if isinstance(child, ast.Call):
                kind = kind_of(child.func)
                if kind:
                    found[(f"{module}:{scope}", kind)] += 1
            if isinstance(child, (ast.While, ast.AsyncFor)) and waits(child):
                found[(f"{module}:{scope}", "loop")] += 1
            visit(child, inner)
    visit(tree, "")
    return found


# (site, kind) -> (count, class, reason)
SITES = {
    # ---- registered: runs without being asked; background.py names it ---------------------------------------------
    ("neuroscifi.console.server:ConsoleServer.__init__", "thread"):
        (1, "registered", "the embedded research worker of `nsf console --web-worker` (RUNNER ConsoleServer._work)"),
    ("neuroscifi.console.server:ConsoleServer._work", "loop"):
        (1, "registered", "that worker's restart loop around scheduler.work (RUNNER; each claim asks allowed())"),
    ("neuroscifi.webcortex.scheduler:work", "thread"):
        (1, "registered", "the worker's heartbeat thread (activity worker.heartbeat)"),
    ("neuroscifi.webcortex.scheduler:work.beat", "loop"):
        (1, "registered", "the heartbeat's 10 s loop (activity worker.heartbeat)"),
    ("neuroscifi.webcortex.scheduler:work", "loop"):
        (1, "registered", "the worker's claim loop (activities worker.claim_background, worker.idle_investigation)"),
    ("neuroscifi.pocket.server:main", "thread"):
        (2, "registered", "the bridge's upkeep and push threads (activities pocket.upkeep, pocket.push)"),
    ("neuroscifi.pocket.server:main.upkeep", "loop"):
        (1, "registered", "expiring Pocket pairing records every 60 s (activity pocket.upkeep)"),
    ("neuroscifi.pocket.push:Push.run", "loop"):
        (1, "registered", "delivering queued Web Push notices (activity pocket.push)"),
    ("neuroscifi.resources.idle:serve_wake_requests", "loop"):
        (1, "registered", "the wake path unit's service loop (activity resources.wake)"),
    ("neuroscifi.resources.idle:_Load", "thread subclass"):
        (1, "request", "one on-demand reload a waiting call asked for (Idle.load); ends when the service is up"),
    # ---- request: lives inside one request or call ----------------------------------------------------------------
    ("neuroscifi.presence.activity:_publish", "timer"):
        (1, "request", "re-publishes a running call's activity row for Presence while that call runs; numbers only"),
    ("neuroscifi.inference.gateway:Gateway._open", "timer"):
        (1, "request", "the whole-call deadline watchdog of one model request"),
    ("neuroscifi.inference.gateway:Gateway._call", "loop"):
        (1, "request", "bounded retries with back-off of one model request"),
    ("neuroscifi.pocket.push:_resolve", "thread"):
        (1, "request", "one DNS lookup with a deadline for one push delivery"),
    ("neuroscifi.pocket.server:PocketServer.process_request", "thread"):
        (1, "request", "one thread per Pocket connection, ends with it"),
    ("neuroscifi.pocket.server:open_control", "thread"):
        (1, "request", "the bridge's local control socket, answering the owner's `nsf pocket` commands"),
    ("neuroscifi.presence.routes:console_state", "loop"):
        (1, "request", "one long poll of the Presence state, bounded by its wait"),
    ("neuroscifi.resources.idle:Idle.load", "loop"):
        (1, "request", "waiting, bounded, for a reload one call needs"),
    ("neuroscifi.resources.idle:_flock_until", "loop"):
        (1, "request", "waiting, bounded, for a lock one call needs"),
    ("neuroscifi.resources.idle:_requested_load", "loop"):
        (1, "request", "waiting, bounded, for the wake unit to load a service one call asked for"),
    ("neuroscifi.resources.idle:_run", "process"):
        (1, "request", "one docker/systemctl/run.sh command of a reload or unload, with a timeout"),
    ("neuroscifi.resources.idle:_spawn_load", "process"):
        (1, "request", "the reload a waiting call asked for (start command of a model service)"),
    ("neuroscifi.resources.idle:wake", "thread"):
        (1, "request", "reloads a service a voice session or call is about to use"),
    ("neuroscifi.resources.monitor:_run", "process"):
        (1, "request", "one read-only system query (nvidia-smi, docker ps) for `nsf resources`, with a timeout"),
    ("neuroscifi.resources.policy:background_model_slot", "loop"):
        (1, "request", "waiting, bounded by a deadline, for a model slot one background call needs"),
    ("neuroscifi.voice.hub:VoiceHub._write_later", "thread"):
        (1, "request", "the voice session's single writer thread; ends when the session closes"),
    ("neuroscifi.voice.hub:VoiceHub._resume_follow", "thread"):
        (1, "request", "following research the owner asked about by voice, resumed with the owner's session"),
    ("neuroscifi.voice.hub:VoiceHub._final.persist", "thread"):
        (1, "request", "answering one final utterance of the owner's session"),
    ("neuroscifi.voice.hub:VoiceHub.text", "thread"):
        (1, "request", "answering one typed message of the owner's session"),
    ("neuroscifi.voice.hub:VoiceHub._deliver", "thread"):
        (2, "request", "speaking one reply, or following the research it promised, in the owner's session"),
    ("neuroscifi.voice.hub:VoiceHub._follow_task", "loop"):
        (1, "request", "following one task the owner asked about, bounded by FOLLOW_MAX"),
    ("neuroscifi.voice.hub:VoiceHub._rewrite_checkpoints", "thread"):
        (1, "request", "rewriting the archives the owner's forget made stale (activity instance.checkpoint)"),
    ("neuroscifi.voice.hub:VoiceHub.events", "loop"):
        (1, "request", "one long poll of a session's events, bounded by its wait"),
    ("neuroscifi.voice.hub:VoiceHub.speech", "loop"):
        (1, "request", "one long poll for synthesized audio, bounded by its wait"),
    ("neuroscifi.webcortex.browser:BrowserRenderer.render", "thread"):
        (1, "request", "one page render of a research task, with a deadline"),
    ("neuroscifi.webcortex.net:SafeFetcher._await", "loop"):
        (1, "request", "waiting for one read-ahead fetch, bounded"),
    ("neuroscifi.webcortex.net:SafeFetcher.speculate", "thread"):
        (1, "request", "one read-ahead fetch inside a running research task (its own budget and gate)"),
    ("neuroscifi.webcortex.net:_Watchdog._watch", "loop"):
        (1, "request", "the deadline watchdog of one fetch"),
    ("neuroscifi.webcortex.net:_Watchdog.arm", "thread"):
        (1, "request", "the deadline watchdog of one fetch (polling form)"),
    ("neuroscifi.webcortex.net:_Watchdog.arm", "timer"):
        (1, "request", "the deadline watchdog of one fetch (timer form)"),
    ("neuroscifi.webcortex.providers.openwebsearch:bounded_request", "timer"):
        (1, "request", "the deadline of one search request"),
    ("neuroscifi.webcortex.scheduler:wait_for_queue", "loop"):
        (1, "request", "waiting, bounded, for a task the caller queued"),
    ("neuroscifi.webcortex.search:ProviderRegistry.search", "executor"):
        (1, "request", "querying the search providers of one query in parallel"),
    ("neuroscifi.webcortex.deps:check", "executor"):
        (1, "request", "one research task's dependency check (or `nsf web deps`), its packages in parallel"),
    # ---- owner: started by an owner's command ---------------------------------------------------------------------
    ("neuroscifi.resources.policy:AudioBeacon.__init__", "thread"):
        (1, "owner", "publishes the audio state of the owner's console or bridge (RD-46); starts no work"),
    ("neuroscifi.resources.policy:AudioBeacon._loop", "loop"):
        (1, "owner", "that beacon's poll loop, while the console or bridge runs; writes one small state file"),
    ("neuroscifi.console.cli:_serve", "server"): (1, "owner", "`nsf console`: the console's HTTP server"),
    ("neuroscifi.pocket.server:main", "server"): (1, "owner", "`nsf pocket serve`: the bridge's HTTPS server"),
    ("neuroscifi.pocket.server:main", "timer"):
        (1, "owner", "removes the invitation file `nsf pocket serve --invitation-file` wrote, when it expires"),
    ("neuroscifi.pocket.setup_page:start", "thread"): (1, "owner", "`nsf setup`'s phone setup page, while it runs"),
    ("neuroscifi.pocket.tls:lan_addresses", "process"):
        (1, "owner", "`ip -o addr show` once, for the certificate of `nsf pocket serve`"),
    ("neuroscifi.start_cli:_start", "process"): (1, "owner", "`nsf start`: the services the owner starts"),
    ("neuroscifi.start_cli:_start", "thread"): (1, "owner", "`nsf start`: one reader per service's output"),
    ("neuroscifi.start_cli:_start", "loop"): (1, "owner", "`nsf start`: waits until the owner stops it"),
    ("neuroscifi.start_cli:_add_phone", "loop"): (1, "owner", "`nsf start`: waits for the phone the owner adds"),
    ("neuroscifi.start_cli:_wait_for_url", "loop"): (1, "owner", "`nsf start`: waits, bounded, for the console"),
    ("neuroscifi.lab.cli:_reproduce", "process"): (1, "owner", "`nsf reproduce`: runs scripts/reproduce.py"),
    ("neuroscifi.webcortex.cli:node_info", "process"): (1, "owner", "`nsf web status`: `node --version` once"),
    ("neuroscifi.webcortex.fixtureweb:FixtureWeb.start", "thread"):
        (1, "owner", "the offline benchmark's local fixture web server (127.0.0.1, measurement only)"),
    ("neuroscifi.webcortex.bench:environment", "process"):
        (2, "owner", "the benchmark's environment record (git rev-parse and status, once)"),
    ("neuroscifi.webcortex.providers.openwebsearch:Daemon.start", "process"):
        (1, "owner", "`nsf web openwebsearch start`: the search daemon the owner starts"),
    ("neuroscifi.webcortex.providers.openwebsearch:Daemon.start", "loop"):
        (1, "owner", "waits, bounded, for that daemon to answer"),
    ("neuroscifi.webcortex.providers.openwebsearch:Daemon.stop", "loop"):
        (1, "owner", "waits, bounded, for that daemon to exit"),
    ("neuroscifi.webcortex.providers.searxng:Container.start", "loop"):
        (1, "owner", "`nsf web searxng start` (by the owner, or the reload a call asked for): waits, bounded"),
    ("neuroscifi.voice.selftest:HostAudio.open", "process"): (1, "owner", "`nsf voice selftest`: records the mic"),
    ("neuroscifi.voice.selftest:HostAudio.play", "process"): (1, "owner", "`nsf voice selftest`: plays a sample"),
    ("neuroscifi.voice.selftest:HostAudio.play", "thread"): (1, "owner", "`nsf voice selftest`: feeds the player"),
    ("neuroscifi.acceptance.doctor:_lsblk_chain", "process"): (1, "owner", "`nsf doctor`: lsblk once, read only"),
    ("neuroscifi.acceptance.scenarios:_host", "process"): (1, "owner", "`nsf acceptance`: host facts, once"),
    ("neuroscifi.acceptance.scenarios:_subprocess_json", "process"):
        (1, "owner", "`nsf acceptance`: one scenario in a child process"),
    ("neuroscifi.acceptance.scenarios:_loaded_loop", "process"):
        (1, "owner", "`nsf acceptance`: the load generator of the loaded scenario"),
    ("neuroscifi.acceptance.scenarios:_loaded_loop", "loop"):
        (1, "owner", "`nsf acceptance`: waits, bounded, for that load generator"),
    ("neuroscifi.acceptance.cognition:environment", "process"):
        (2, "owner", "the cognitive benchmark's environment record (measurement only; MEASUREMENT_ONLY)"),
}


def _scan_source():
    found = Counter()
    for path in sorted(SRC.rglob("*.py")):
        if "withheld" in path.name:
            continue
        module = ("neuroscifi." + ".".join(path.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
        found.update(launch_sites(ast.parse(path.read_text()), module))
    return found


def test_the_scanner_finds_threads_timers_processes_servers_and_waiting_loops():
    sample = ("import threading, subprocess as sp, os\nfrom threading import Thread as T\n"
              "from concurrent.futures import ThreadPoolExecutor\nimport multiprocessing\nimport time\n"
              "def a():\n    threading.Thread(target=f).start()\n"
              "def b():\n    T(target=f).start()\n"
              "def c():\n    threading.Timer(5, f).start()\n"
              "def d():\n    sp.run(['x'])\n    os.system('x')\n"
              "def e():\n    with ThreadPoolExecutor() as pool: pass\n"
              "def g():\n    multiprocessing.Process(target=f).start()\n"
              "def h(server):\n    server.serve_forever()\n"
              "def i(stop):\n    while not stop.wait(60):\n        pass\n"
              "def j():\n    while True:\n        time.sleep(1)\n"
              "def k(n):\n    while n:\n        n -= 1\n"
              "class W(threading.Thread):\n    pass\n"
              "class Q:\n    def m(self):\n        def inner():\n            os.fork()\n")
    assert launch_sites(ast.parse(sample), "neuroscifi.new") == Counter({
        ("neuroscifi.new:a", "thread"): 1, ("neuroscifi.new:b", "thread"): 1, ("neuroscifi.new:c", "timer"): 1,
        ("neuroscifi.new:d", "process"): 2, ("neuroscifi.new:e", "executor"): 1, ("neuroscifi.new:g", "process"): 1,
        ("neuroscifi.new:h", "server"): 1, ("neuroscifi.new:i", "loop"): 1, ("neuroscifi.new:j", "loop"): 1,
        ("neuroscifi.new:W", "thread subclass"): 1, ("neuroscifi.new:Q.m.inner", "process"): 1})


def test_every_launch_site_in_the_product_is_classified_and_every_classified_site_exists():
    found = _scan_source()
    declared = Counter({site: count for site, (count, _, _) in SITES.items()})
    unclassified = {site: n for site, n in found.items() if site not in SITES}
    assert unclassified == {}, ("a new thread, timer, process or waiting loop: classify it in SITES (registered, "
                                "request or owner) with a one-line reason")
    assert {s: n for s, n in found.items() if declared[s] != n} == {}, "a classified site's count changed"
    assert set(SITES) - set(found) == set(), "SITES names a launch that is no longer in the source"


def test_each_classification_is_well_formed_and_a_registered_one_is_in_background_py():
    activities = set(background.ACTIVITIES)
    for (site, kind), (count, klass, reason) in SITES.items():
        assert klass in CLASSES and isinstance(count, int) and count >= 1, site
        assert isinstance(reason, str) and len(reason.split()) >= 4, f"{site}: give a one-line reason"
        if klass == "registered":
            names = {word.strip("(),;") for word in reason.split()}
            runner = "RUNNER" in reason and any(site.split(":")[0] in r for r in background.RUNNERS)
            assert names & activities or runner, f"{site}: a registered launch names its background.py activity"
