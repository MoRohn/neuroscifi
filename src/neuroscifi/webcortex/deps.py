"""Scenario C: "Check whether the libraries we use have newer stable releases or important security advisories."

`inventory(root)` reads the versions actually in use, never guesses:
- pyproject.toml `[project] dependencies` and every `[project.optional-dependencies]` group (tomllib), with the
  version installed in the running interpreter (importlib.metadata) next to each pin;
- the installed transitive closure of the Web Cortex extras (`web`, `web-extract`, `web-render`), following each
  distribution's `Requires-Dist` (requirements behind an `extra ==` marker are optional and skipped);
- the root package.json devDependencies, resolved through the root package-lock.json;
- deploy/open-websearch/package-lock.json: the top-level open-websearch and its direct dependencies' resolved versions.

`run(...)` then (network only when the individual has web access enabled):
1. submits a normal research task in mode 'technical' whose structured context lists the packages, so the check
   leaves episodes, sources and evidence like any task (`--queue` leaves it for the worker; a task plans at most 8
   queries, so it covers the first packages only);
2. makes one direct, bounded pass through the registry's own structured providers: PyPIProvider / NpmProvider for
   the latest stable release and its date, OSVProvider for advisories of the exact version in use (OSV answers
   version-specifically, so every advisory returned affects that version). The budget is sized to the package
   count and clamped by the owner's ceiling; outcomes feed the provider health record (procedural memory).

The report gives per package: current, latest stable (pre-releases and yanked releases skipped), released_at,
behind_by (major/minor/patch), advisories {id, aliases, summary, severity, fixed_in}, compatibility notes and one
recommendation: "upgrade (security)", "consider upgrade", "up to date" or "unknown (lookup failed: <code>)".
Failures are listed, never hidden. This module never installs, upgrades or removes anything: it runs no pip or npm.

`audit=True` (`nsf web deps --audit`) checks advisories only, for installed Python packages and the npm lock files.
`network=False` (`--no-network`) returns the inventory alone.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import time
import tomllib

WEB_EXTRAS = ("web", "web-extract", "web-render")
NO_CHANGES = "No packages were installed or modified."
TASK_QUERY_CAP = 8
_REQ = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*([^;]*?)\s*(?:;(.*))?$")
_RELEASE = re.compile(r"^v?(\d+(?:\.\d+)*)")
_PY_PRE = re.compile(r"(?i)(a|b|c|rc|alpha|beta|pre|preview|dev)\d*")
_SPEC = re.compile(r"(?i)^\s*(~=|==|!=|<=|>=|<|>|===)\s*(\d+(?:\.\d+)*(?:\.\*)?(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|"
                   r"preview|post|rev|r|dev)[-_.]?\d*)*(?:\+[a-z0-9.]+)?)\s*$")


# ---- versions --------------------------------------------------------------------------------------------------

def normalize(name: str) -> str:
    """PEP 503 normalised project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def release(version) -> tuple | None:
    m = _RELEASE.match(str(version or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_prerelease(version, ecosystem) -> bool:
    text = str(version or "").strip()
    m = _RELEASE.match(text)
    if not m:
        return True
    rest = text[m.end():].split("+", 1)[0]
    if ecosystem == "npm":
        return rest.startswith("-")
    return bool(_PY_PRE.search(rest))


def _pad(t, n=3):
    return tuple(t) + (0,) * max(0, n - len(t))


def behind_by(current, latest) -> str | None:
    """'major' | 'minor' | 'patch' when latest is newer, 'ahead' when current is newer, None when equal/unknown."""
    a, b = release(current), release(latest)
    if a is None or b is None:
        return None
    n = max(len(a), len(b), 3)
    a, b = _pad(a, n), _pad(b, n)
    if a == b:
        return None
    if a > b:
        return "ahead"
    first = next(i for i in range(n) if a[i] != b[i])
    return ("major", "minor")[first] if first < 2 else "patch"


def _cmp_spec(op, want, have) -> bool:
    if want.endswith(".*") and op in ("==", "!="):
        prefix = release(want[:-2]) or ()
        match = tuple(have[:len(prefix)]) == prefix
        return match if op == "==" else not match
    target = release(want)
    if target is None:
        raise ValueError(want)
    n = max(len(target), len(have), 3)
    t, h = _pad(target, n), _pad(have, n)
    if op == "~=":
        prefix = target[:-1] if len(target) > 1 else target
        return h >= t and tuple(have[:len(prefix)]) == tuple(prefix)
    return {"==": h == t, "===": h == t, "!=": h != t, "<=": h <= t, ">=": h >= t, "<": h < t, ">": h > t}[op]


def satisfies(spec, version) -> bool | None:
    """Whether `version` meets a PEP 440 specifier set like '>=3.10,<4'; None when it cannot be evaluated."""
    have = release(version)
    if not spec or not spec.strip():
        return True
    if have is None:
        return None
    try:
        for part in spec.split(","):
            if not part.strip():
                continue
            m = _SPEC.match(part)
            if not m or not _cmp_spec(m.group(1), m.group(2), have):
                return None if not m else False
    except ValueError:
        return None
    return True


# ---- inventory -------------------------------------------------------------------------------------------------

def repo_root(start=None) -> Path:
    """The project folder holding pyproject.toml: from `start`, else this package's checkout, else the cwd."""
    candidates = [Path(start)] if start else [Path(__file__).resolve().parent, Path.cwd()]
    for base in candidates:
        for d in (base, *base.parents):
            if (d / "pyproject.toml").is_file() and (d / "src" / "neuroscifi").is_dir():
                return d
    return Path(start) if start else Path.cwd()


def _installed(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def parse_requirement(text):
    """'name[extra]==1.2; marker' -> (name, spec, marker) or None."""
    m = _REQ.match(text or "")
    if not m:
        return None
    return m.group(1), (m.group(3) or "").strip(), (m.group(4) or "").strip() or None


def _exact(spec):
    spec = (spec or "").strip()
    if spec.startswith("==") and "," not in spec and "*" not in spec:
        return spec[2:].strip()
    return None


def _marker_ok(marker) -> bool | None:
    """False for optional (`extra ==`) requirements; True/False when the marker can be evaluated; else None."""
    if not marker:
        return True
    if re.search(r"\bextra\b", marker):
        return False
    try:
        from packaging.markers import Marker   # present in most environments; optional here
    except ImportError:
        return None
    try:
        return bool(Marker(marker).evaluate())
    except Exception:  # an unparseable marker in third-party metadata: decide by installation instead
        return None


def _transitive(roots, failures) -> dict:
    """Installed distributions reachable from `roots` through non-optional requirements: name -> required by."""
    seen, queue = {}, [(normalize(r), None) for r in roots]
    while queue:
        name, parent = queue.pop(0)
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if parent is not None:
                failures.append({"package": name, "ecosystem": "PyPI", "lookup": "inventory", "code": "not_installed",
                                 "detail": f"required by {parent} but not installed"})
            continue
        seen[name] = parent
        for req in dist.requires or []:
            parsed = parse_requirement(req)
            if parsed is None:
                continue
            dep, _, marker = parsed
            ok = _marker_ok(marker)
            if ok is False or (ok is None and _installed(dep) is None):
                continue
            queue.append((normalize(dep), name))
    return seen


def _read_json(path, failures, label):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        failures.append({"package": None, "ecosystem": "npm", "lookup": "inventory", "code": "missing_file",
                         "detail": f"{label} not found"})
    except (OSError, ValueError) as exc:
        failures.append({"package": None, "ecosystem": "npm", "lookup": "inventory", "code": "unreadable_file",
                         "detail": f"{label}: {type(exc).__name__}"})
    return None


def inventory(root=None) -> dict:
    """Packages in use with their actual versions (see module docstring)."""
    root = repo_root(root)
    failures, notes = [], []
    packages: dict[tuple, dict] = {}

    def add(ecosystem, name, version, source_file, pinned_spec, group, *, installed=None):
        key = (ecosystem, normalize(name) if ecosystem == "PyPI" else name)
        item = packages.get(key)
        if item is None:
            item = packages[key] = {"ecosystem": ecosystem, "name": name, "version": version,
                                    "source_file": source_file, "pinned_spec": pinned_spec,
                                    "installed_version": installed, "groups": []}
        elif item["version"] is None and version is not None:
            item["version"] = version
        if group not in item["groups"]:
            item["groups"].append(group)
        return item

    pyproject = root / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text()).get("project", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        failures.append({"package": None, "ecosystem": "PyPI", "lookup": "inventory", "code": "unreadable_file",
                         "detail": f"pyproject.toml: {type(exc).__name__}"})
        project = {}
    groups = [("dependencies", project.get("dependencies") or [])]
    groups += [(f"optional:{g}", reqs) for g, reqs in (project.get("optional-dependencies") or {}).items()]
    for group, reqs in groups:
        for req in reqs:
            parsed = parse_requirement(req)
            if parsed is None:
                failures.append({"package": req, "ecosystem": "PyPI", "lookup": "inventory", "code": "unparsed",
                                 "detail": "requirement not understood"})
                continue
            name, spec, _ = parsed
            installed = _installed(name)
            item = add("PyPI", name, installed or _exact(spec), "pyproject.toml", spec or None, group,
                       installed=installed)
            pin = _exact(spec)
            if installed and pin and installed != pin:
                note = f"{name}: installed {installed} differs from the pin {pin}"
                item.setdefault("notes", []).append(note)
                notes.append(note)
    web_roots = [parse_requirement(r)[0] for g in WEB_EXTRAS
                 for r in (project.get("optional-dependencies") or {}).get(g, []) if parse_requirement(r)]
    for name, parent in _transitive(web_roots, failures).items():
        if parent is None:
            continue
        add("PyPI", metadata.distribution(name).metadata["Name"] or name, _installed(name), "installed environment",
            None, f"transitive:web (via {parent})", installed=_installed(name))

    manifest = _read_json(root / "package.json", failures, "package.json")
    lock = _read_json(root / "package-lock.json", failures, "package-lock.json") if manifest else None
    lock_packages = (lock or {}).get("packages") or {}
    for name, spec in ((manifest or {}).get("devDependencies") or {}).items():
        resolved = (lock_packages.get(f"node_modules/{name}") or {}).get("version")
        installed = None
        try:
            installed = json.loads((root / "node_modules" / name / "package.json").read_text()).get("version")
        except (OSError, ValueError):
            pass
        add("npm", name, installed or resolved or (spec if release(spec) and spec[:1].isdigit() else None),
            "package-lock.json" if resolved else "package.json", spec, "devDependencies", installed=installed)

    ows = root / "deploy" / "open-websearch"
    ows_lock = _read_json(ows / "package-lock.json", failures, "deploy/open-websearch/package-lock.json")
    entries = (ows_lock or {}).get("packages") or {}
    source = "deploy/open-websearch/package-lock.json"
    for name, spec in ((entries.get("") or {}).get("dependencies") or {}).items():
        top = entries.get(f"node_modules/{name}") or {}
        add("npm", name, top.get("version"), source, spec, "open-websearch")
        for dep, dep_spec in (top.get("dependencies") or {}).items():
            resolved = (entries.get(f"node_modules/{name}/node_modules/{dep}") or
                        entries.get(f"node_modules/{dep}") or {}).get("version")
            if resolved is None:
                failures.append({"package": dep, "ecosystem": "npm", "lookup": "inventory", "code": "unresolved",
                                 "detail": f"{dep} {dep_spec} not in the lock file"})
            add("npm", dep, resolved, source, dep_spec, f"open-websearch dependency (via {name})")

    items = list(packages.values())
    for item in items:
        if item["version"] is None:
            item.setdefault("notes", []).append("no installed or exactly pinned version: advisories not checked")
    files = {"pyproject": str(pyproject), "package_json": str(root / "package.json"),
             "open_websearch_lock": str(ows / "package-lock.json")}
    return {"root": str(root), "python": platform.python_version(), "files": files, "count": len(items),
            "packages": items, "notes": notes, "failures": failures}


# ---- registry and advisory pass ------------------------------------------------------------------------------------

def _latest_stable(meta, ecosystem):
    """(version, released_at, note) of the newest stable, non-yanked release in the registry answer."""
    latest = meta.get("latest")
    yanked = bool(meta.get("yanked"))
    if latest and not yanked and not is_prerelease(latest, ecosystem):
        return latest, meta.get("released_at"), None
    stable = [v for v in meta.get("versions_recent") or [] if isinstance(v, dict) and v.get("version")
              and not v.get("yanked") and not is_prerelease(v["version"], ecosystem) and release(v["version"])]
    if not stable:
        return None, None, f"registry 'latest' {latest} is a pre-release or yanked; no stable release among the " \
                           "most recent five"
    best = max(stable, key=lambda v: _pad(release(v["version"]), 6))
    return best["version"], best.get("released_at"), f"registry 'latest' {latest} skipped (pre-release or yanked)"


def _severity(meta):
    label = meta.get("severity_label")
    vectors = [s.get("score") for s in meta.get("severity") or [] if isinstance(s, dict) and s.get("score")]
    return label or (vectors[0] if vectors else None)


def _advisory(result):
    meta = result.metadata or {}
    return {"id": meta.get("id"), "aliases": meta.get("aliases") or [], "summary": result.title,
            "severity": _severity(meta), "severity_vectors": [s for s in meta.get("severity") or []],
            "fixed_in": meta.get("fixed_versions") or [], "published": meta.get("published"),
            "url": result.url, "affected": True}


def _fix_target(current, advisories):
    have = release(current)
    fixes = sorted({f for a in advisories for f in a["fixed_in"]
                    if release(f) and have and _pad(release(f), 6) > _pad(have, 6)},
                   key=lambda f: _pad(release(f), 6))
    return fixes[-1] if fixes else None


def within_range(spec, version) -> bool | None:
    """Whether `version` is allowed by a declared range: '==X' (PyPI pin), exact 'X', '^X.Y.Z' or '~X.Y.Z' (npm).
    None for forms not evaluated here."""
    spec, target = (spec or "").strip(), release(version)
    if not spec or target is None:
        return None
    if spec.startswith("=="):
        return _exact(spec) == version
    base = release(spec.lstrip("^~"))
    if base is None or not spec.lstrip("^~")[:1].isdigit():
        return None
    base, target = _pad(base), _pad(target)
    if spec.startswith("^"):
        fixed = 1 if base[0] else (2 if base[1] else 3)
        return target >= base and target[:fixed] == base[:fixed]
    if spec.startswith("~"):
        return target >= base and target[:2] == base[:2]
    return spec == version


def _range_note(item, target):
    allowed = within_range(item.get("pinned_spec"), target)
    if allowed is not False:
        return None
    via = next((g.split("(via ", 1)[1].rstrip(")") for g in item.get("groups", []) if "(via " in g), None)
    where = f"{via}'s dependency range" if via else item.get("source_file")
    return f"{target} is outside the declared range {item['pinned_spec']} ({where}): " + \
        (f"needs a release of {via} that allows it" if via else "the pin must change")


def _budget(cfg, calls, concurrency=4):
    from .types import Budget
    calls = max(1, calls)
    return Budget(max_queries=0, max_requests=2 * calls + 2, max_pages=0, max_bytes=3_000_000 * calls,
                  max_seconds=60.0 + 4.0 * calls, max_depth=0, max_browser_sessions=0,
                  max_concurrency=concurrency, max_context_tokens=0).clamp(cfg.ceiling())


class _Pass:
    """One bounded pass over the structured providers. Thread-safe failure log."""

    def __init__(self, db, services, budget, clock):
        import threading
        from .types import Usage
        from . import orchestrator
        self.db, self.services, self.budget, self.clock = db, services, budget, clock
        self.usage, self.lock, self.failures = Usage(), threading.Lock(), []
        self.started = clock()
        cfg = services.config
        self.private = orchestrator.private_terms(db, cfg)
        by_name = {p.name: p for p in getattr(services.registry, "providers", [])}
        self.providers = {k: by_name.get(k) for k in ("pypi", "npm", "osv")}

    def call(self, provider_name, text, limit, item, lookup):
        """(results, error_code) of one provider call; every failure is logged with the package."""
        from .safety import privacy_screen
        from .search import NON_HEALTH_CODES
        from .types import Query
        provider = self.providers.get(provider_name)
        code, detail, results = None, "", None
        if provider is None:
            code, detail = "provider_disabled", f"provider {provider_name} is not enabled in the configuration"
        elif privacy_screen(text, private_terms=self.private):
            code, detail = "privacy_screen", "lookup text matched the privacy screen; not sent"
        elif self.clock() - self.started > self.budget.max_seconds:
            code, detail = "budget_exhausted", "seconds"
        if code is None:
            started = time.perf_counter()
            try:
                results = provider.search(Query(text, "technical"), limit, budget=self.budget, usage=self.usage)
            except Exception as exc:   # recorded per package below, never raised past the pass
                code = getattr(exc, "code", None) or type(exc).__name__
                detail = str(getattr(exc, "detail", "") or exc)[:300]
            latency = round(time.perf_counter() - started, 4)
            if code not in NON_HEALTH_CODES:
                self.services.registry.record(provider_name, code is None, latency, None if code is None else
                                              f"{code}: {detail}"[:300])
        if code is not None:
            with self.lock:
                self.failures.append({"package": item["name"], "ecosystem": item["ecosystem"], "lookup": lookup,
                                      "provider": provider_name, "code": code, "detail": detail})
            return None, code
        return list(results or []), None

    def check(self, item, audit):
        eco, name, current = item["ecosystem"], item["name"], item["version"]
        out = {"ecosystem": eco, "name": name, "current": current, "pinned_spec": item["pinned_spec"],
               "installed_version": item.get("installed_version"), "source_file": item["source_file"],
               "groups": item["groups"], "latest": None, "released_at": None, "behind_by": None,
               "advisories": [], "affected": None, "compatibility": list(item.get("notes") or []),
               "lookups": {}}
        registry_code = advisory_code = None
        if not audit:
            results, registry_code = self.call("pypi" if eco == "PyPI" else "npm",
                                               f"{'pypi' if eco == 'PyPI' else 'npm'}:{name}", 1, item, "registry")
            if results == []:
                registry_code = "not_found"
                with self.lock:
                    self.failures.append({"package": name, "ecosystem": eco, "lookup": "registry",
                                          "provider": "pypi" if eco == "PyPI" else "npm", "code": "not_found",
                                          "detail": "the registry has no such package"})
            elif results:
                meta = results[0].metadata or {}
                latest, released_at, note = _latest_stable(meta, eco)
                out.update(latest=latest, released_at=released_at)
                if note:
                    out["compatibility"].append(note)
                out["behind_by"] = behind_by(current, latest) if current and latest else None
                if out["behind_by"] == "major":
                    out["compatibility"].append("major version bump: breaking changes possible; review the changelog")
                elif out["behind_by"] == "minor" and (release(current) or (1,))[0] == 0:
                    out["compatibility"].append("0.x minor bump: breaking changes possible under semantic versioning")
                note = _range_note(item, latest) if out["behind_by"] in ("major", "minor", "patch") else None
                if note:
                    out["compatibility"].append(note)
                requires = meta.get("requires_python")
                if requires and latest:
                    fits = satisfies(requires, platform.python_version())
                    out["requires_python"] = requires
                    if fits is False:
                        out["compatibility"].append(f"latest {latest} requires Python {requires}; running "
                                                    f"{platform.python_version()} does not satisfy it")
                    elif fits is None:
                        out["compatibility"].append(f"latest {latest} requires Python {requires} (not evaluated)")
                if meta.get("deprecated"):
                    out["compatibility"].append(f"latest release is deprecated: {meta['deprecated']}")
            out["lookups"]["registry"] = registry_code or "ok"
        if current:
            results, advisory_code = self.call("osv", f"osv:{eco}:{name}@{current}", 20, item, "advisories")
            if results is not None:
                out["advisories"] = [_advisory(r) for r in results]
                out["affected"] = bool(out["advisories"])
            out["lookups"]["advisories"] = advisory_code or "ok"
        else:
            out["lookups"]["advisories"] = "skipped: no version in use"
        if out["affected"]:
            target = _fix_target(current, out["advisories"])
            out["recommendation"] = "upgrade (security)"
            out["action"] = f"upgrade to {target} or later" if target else "no fixed version published; " \
                                                                            "review the advisory for mitigations"
            note = _range_note(item, target) if target else None
            if note and not any("outside the declared range" in n for n in out["compatibility"]):
                out["compatibility"].append(note)
            if target and behind_by(current, target) == "major" and out["behind_by"] != "major":
                out["compatibility"].append(f"the fix {target} is a major version bump: breaking changes possible")
        elif registry_code or advisory_code:
            out["recommendation"] = f"unknown (lookup failed: {registry_code or advisory_code})"
        elif not current:
            out["recommendation"] = "unknown (lookup failed: no_version)"
        elif audit:
            out["recommendation"] = "no known advisories"
        elif out["behind_by"] in ("major", "minor", "patch"):
            out["recommendation"] = "consider upgrade"
            out["action"] = f"{current} -> {out['latest']} ({out['behind_by']})"
        else:
            out["recommendation"] = "up to date"
        return out


def check(db, services, packages, *, audit=False, clock=time.time) -> dict:
    """The direct bounded pass (step 2 of the module docstring). Returns {packages, failures, budget, usage}."""
    cfg = services.config
    calls = sum((0 if audit else 1) + (1 if p["version"] else 0) for p in packages)
    budget = _budget(cfg, calls)
    run = _Pass(db, services, budget, clock)
    workers = max(1, min(4, int(budget.max_concurrency)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="webcortex-deps") as pool:
        reports = list(pool.map(lambda p: run.check(p, audit), packages))
    run.usage.seconds = round(clock() - run.started, 3)
    services.persist(db, clock())
    limitations = []
    if any(f["code"] == "budget_exhausted" for f in run.failures):
        limitations.append(f"budget exhausted after {run.usage.requests} requests: raise budget_ceiling to check "
                           "every package")
    if any(f["code"] == "too_large" for f in run.failures):
        limitations.append("some registry documents exceed max_document_bytes; raise it with "
                           "`nsf web config --set max_document_bytes=...` to check them")
    return {"packages": reports, "failures": run.failures, "budget": budget.to_json(), "usage": run.usage.to_json(),
            "limitations": limitations, "calls_planned": calls}


def _totals(reports, audit):
    rec = [r.get("recommendation", "") for r in reports]
    out = {"packages": len(reports), "advisories": sum(len(r["advisories"]) for r in reports),
           "affected_packages": sum(1 for r in reports if r["affected"]),
           "upgrade_security": rec.count("upgrade (security)"),
           "unknown": sum(1 for x in rec if x.startswith("unknown"))}
    if audit:
        out["no_known_advisories"] = rec.count("no known advisories")
    else:
        out.update({"up_to_date": rec.count("up to date"), "consider_upgrade": rec.count("consider upgrade"),
                    "behind_major": sum(1 for r in reports if r["behind_by"] == "major")})
    return out


def run(db, db_path, now, *, services=None, audit=False, network=True, queue=False, root=None,
        clock=time.time) -> dict:
    """Scenario C. `services` (services.Services) is required when network is True."""
    from . import orchestrator, state
    inv = inventory(root)
    head = {"scenario": "C", "question": "Check whether the libraries we use have newer stable releases or important "
                                         "security advisories", "as_of": now, "python": inv["python"],
            "mode": "inventory" if not network else ("audit" if audit else "full"),
            "inventory": {k: inv[k] for k in ("root", "files", "count", "notes")}}
    if not network:
        return head | {"packages": inv["packages"], "failures": inv["failures"], "task": None,
                       "statement": NO_CHANGES + " No network request was made."}
    state.require_enabled(db)
    if services is None:
        raise ValueError("services are required for a network check")
    packages = inv["packages"]
    if audit:
        packages = [p for p in packages if p["ecosystem"] == "npm" or p.get("installed_version")]
    task = None
    if not audit:
        objective = f"Dependency release and advisory check ({len(packages)} packages)"
        context = {"packages": [{"ecosystem": p["ecosystem"], "name": p["name"], "version": p["version"]}
                                for p in packages][:orchestrator.MAX_CONTEXT_PACKAGES]}
        submitted = orchestrator.submit(db, objective, now, mode="technical", origin="cli", context=context)
        covered = min(len(packages), TASK_QUERY_CAP // 2)
        task = {"id": submitted["id"], "status": submitted["status"], "queued": queue,
                "coverage": f"the task plans at most {TASK_QUERY_CAP} queries (registry + advisories per package), "
                            f"so it covers the first {covered} of {len(packages)} packages; the direct pass below "
                            "covers all of them"}
    result = check(db, services, packages, audit=audit, clock=clock)
    if task is not None and not queue:
        done = orchestrator.run(db, task["id"], services)
        task.update(status=done["status"], error=done.get("error"),
                    sources=len((done.get("result") or {}).get("sources") or []))
    reports = result["packages"]
    order = {"upgrade (security)": 0, "consider upgrade": 2, "up to date": 3, "no known advisories": 3}
    reports.sort(key=lambda r: (order.get(r["recommendation"], 1), r["ecosystem"], r["name"].lower()))
    return head | {"packages": reports, "totals": _totals(reports, audit),
                   "failures": inv["failures"] + result["failures"], "limitations": result["limitations"],
                   "budget": result["budget"], "usage": result["usage"], "task": task,
                   "statement": NO_CHANGES}


__all__ = ["inventory", "check", "run", "behind_by", "satisfies", "is_prerelease", "release", "normalize",
           "NO_CHANGES"]
