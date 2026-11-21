"""Presence Cortex (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md): a minimal vector entity that shows what the
individual is really doing.

- aggregator.py   Cognitive State Aggregator: derives the multidimensional state ONLY from persisted or in-process
                  state (research task rows, stages, leases and events, the inference ledger, pending questions,
                  worker and provider health, regulation, the optional voice snapshot). Never invents activity.
- activity.py     process-local registry of in-flight work that has no persisted row (presence's own model calls)
- scene_spec.py   Tier C: a bounded, versioned, declarative doodle spec proposed by the local model, validated here
                  (and again in the browser); never markup or code
- routes.py       the console (long-poll GET) and Pocket (family-scoped conditional POST) endpoints
- web/            the shared browser modules served by both the console and Pocket under /presence/
"""
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
MIME = {".mjs": "text/javascript; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8"}
# Served by the console and by Pocket; POCKET is also cached by Pocket's service worker shell (pocket/web/sw.js).
SHARED = ("theme-boot.js", "theme.css", "theme.mjs", "contracts.mjs", "geometry.mjs", "canonical.mjs", "composer.mjs",
          "validator.mjs", "interpreter.mjs", "planner.mjs", "motion.mjs", "renderer.mjs", "a11y.mjs", "channel.mjs",
          "presence.mjs")
POCKET = SHARED + ("pocket-boot.mjs",)
CONSOLE = SHARED + ("console-boot.mjs", "gallery.html", "gallery.mjs", "gallery.css")


def assets(files):
    """{"/presence/<name>": (absolute path, mime)} for a server's static ASSETS table. The absolute path survives
    `WEB / name` joins in either server (joining an absolute path yields it unchanged)."""
    return {f"/presence/{name}": (str(WEB / name), MIME[Path(name).suffix]) for name in files}
