"""Scenario C (`nsf web deps`): inventory of the versions actually in use, and the advisory/release pass with
fake structured providers. Nothing here reaches the network or installs anything."""
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from neuroscifi.cli import main
from neuroscifi.webcortex import cli as wcli, deps
from neuroscifi.webcortex.search import ProviderError
from neuroscifi.webcortex.types import SearchResult

ROOT = Path(__file__).resolve().parents[2]


def nsf(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


# ---- version helpers -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("current,latest,expected", [
    ("1.2.3", "2.0.0", "major"), ("1.2.3", "1.3.0", "minor"), ("1.2.3", "1.2.4", "patch"),
    ("1.2", "1.2.0", None), ("2.0.0", "1.9.9", "ahead"), ("0.25.0", "0.26.1", "minor"), ("x", "1.0", None),
    ("2026.6", "2026.11.20", "minor"), ("4.0.0", "4.0.0.1", "patch")])
def test_behind_by(current, latest, expected):
    assert deps.behind_by(current, latest) == expected


@pytest.mark.parametrize("version,ecosystem,pre", [
    ("1.0.0", "PyPI", False), ("1.0.0rc1", "PyPI", True), ("2.0b3", "PyPI", True), ("1.0.dev4", "PyPI", True),
    ("1.0.post1", "PyPI", False), ("1.0.0+local", "PyPI", False), ("1.3.0-beta.1", "npm", True),
    ("1.3.0", "npm", False), ("garbage", "npm", True)])
def test_prerelease_detection(version, ecosystem, pre):
    assert deps.is_prerelease(version, ecosystem) is pre


@pytest.mark.parametrize("spec,version,ok", [
    (">=3.10", "3.12.16", True), (">=3.13", "3.12.16", False), (">=3.9,<3.12", "3.12.0", False),
    ("~=3.11", "3.12.1", True), ("~=3.11.2", "3.12.1", False), ("==3.*", "3.12.1", True),
    ("!=3.12.*", "3.12.1", False), ("", "3.12.1", True), (">=3.x", "3.12.1", None)])
def test_requires_python_evaluation(spec, version, ok):
    assert deps.satisfies(spec, version) is ok


@pytest.mark.parametrize("spec,version,ok", [
    ("^3.25.76", "3.26.0", True), ("^3.25.76", "4.6.6", False), ("^0.22.35", "0.22.40", True),
    ("^0.22.35", "0.23.0", False), ("~1.2.3", "1.2.9", True), ("~1.2.3", "1.3.0", False), ("==1.0.0", "1.0.5", False),
    ("2.2.0", "2.2.0", True), (">=1,<2", "1.5", None), (None, "1.0", None)])
def test_declared_range(spec, version, ok):
    assert deps.within_range(spec, version) is ok


# ---- inventory against the real project files ------------------------------------------------------------------

def test_inventory_matches_the_real_project_files():
    inv = deps.inventory(ROOT)
    by = {(p["ecosystem"], deps.normalize(p["name"]) if p["ecosystem"] == "PyPI" else p["name"]): p
          for p in inv["packages"]}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = list(project["dependencies"]) + [r for g in project["optional-dependencies"].values() for r in g]
    for req in pins:
        name, spec, _ = deps.parse_requirement(req)
        item = by[("PyPI", deps.normalize(name))]
        assert item["pinned_spec"] == spec and item["source_file"] == "pyproject.toml"
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        assert item["installed_version"] == installed
        assert item["version"] == (installed or spec.removeprefix("=="))
    assert by[("PyPI", "numpy")]["groups"] == ["dependencies"]
    assert set(by[("PyPI", "playwright")]["groups"]) == {"optional:dev", "optional:web-render"}
    # the installed transitive closure of the web extras, never the optional extras of those packages
    if by[("PyPI", "scrapling")]["installed_version"]:
        assert by[("PyPI", "lxml")]["version"] == metadata.version("lxml")
        assert any(g.startswith("transitive:web") for g in by[("PyPI", "lxml")]["groups"])
    assert ("PyPI", "curl-cffi") not in by and ("PyPI", "patchright") not in by
    lock = json.loads((ROOT / "deploy/open-websearch/package-lock.json").read_text())["packages"]
    assert by[("npm", "open-websearch")]["version"] == lock["node_modules/open-websearch"]["version"]
    for dep, spec in lock["node_modules/open-websearch"]["dependencies"].items():
        item = by[("npm", dep)]
        assert item["pinned_spec"] == spec and item["version"] == lock[f"node_modules/{dep}"]["version"]
    dev = json.loads((ROOT / "package.json").read_text())["devDependencies"]
    for name, spec in dev.items():
        assert by[("npm", name)]["pinned_spec"] == spec and by[("npm", name)]["version"]
    assert inv["count"] == len(inv["packages"]) and not inv["failures"]


def test_cli_no_network_inventory_works_while_web_is_disabled(tmp_path, capsys):
    db = str(tmp_path / "i.sqlite")
    assert nsf(capsys, "init", "--db", db)[0] == 0
    code, out = nsf(capsys, "web", "deps", "--no-network", "--db", db)
    assert code == 0 and out["mode"] == "inventory" and out["task"] is None
    assert out["statement"].startswith(deps.NO_CHANGES) and "No network request" in out["statement"]
    assert out["inventory"]["count"] == len(out["packages"]) >= 20
    code, err = nsf(capsys, "web", "deps", "--db", db)
    assert code == 2 and "disabled" in err


# ---- the network pass with fake providers -----------------------------------------------------------------------

class FakeProvider:
    kinds = frozenset({"technical"})

    def __init__(self, name, answers):
        self.name, self.answers, self.calls = name, answers, []

    def search(self, query, limit, *, budget=None, usage=None):
        self.calls.append(query.text)
        answer = self.answers.get(query.text, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


def package(name, eco, latest, released_at, *, recent=None, requires_python=None, yanked=False):
    url = f"https://pypi.org/project/{name}/" if eco == "PyPI" else f"https://www.npmjs.com/package/{name}"
    return [SearchResult(url=url, title=f"{name} {latest}", snippet="", provider=eco.lower(), kind="package",
                         published_at=released_at,
                         metadata={"package": name, "ecosystem": eco, "latest": latest, "released_at": released_at,
                                   "yanked": yanked, "requires_python": requires_python,
                                   "versions_recent": recent or []})]


def advisory(vid, fixed):
    return [SearchResult(url=f"https://osv.dev/vulnerability/{vid}", title="Heap overflow in parser", snippet="",
                         provider="osv", kind="advisory",
                         metadata={"id": vid, "aliases": ["CVE-2026-0001"], "severity": [],
                                   "severity_label": "HIGH", "fixed_versions": fixed})]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "deploy" / "open-websearch").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1"\n'
        'dependencies = ["oldpkg==1.0.0", "freshpkg==2.0.0", "brokenpkg==0.1.0", "numpy==2.3.5"]\n')
    (root / "deploy" / "open-websearch" / "package-lock.json").write_text(json.dumps({"packages": {
        "": {"dependencies": {"nodepkg": "1.2.3"}}, "node_modules/nodepkg": {"version": "1.2.3"}}}))
    return root


def fake_providers():
    numpy = metadata.version("numpy")
    pypi = FakeProvider("pypi", {
        "pypi:oldpkg": package("oldpkg", "PyPI", "2.1.0", 1.79e9, requires_python=">=3.99"),
        "pypi:freshpkg": package("freshpkg", "PyPI", "2.0.0", 1.78e9),
        "pypi:brokenpkg": ProviderError("pypi", "timed out", code="timeout"),
        "pypi:numpy": package("numpy", "PyPI", numpy, 1.77e9)})
    npm = FakeProvider("npm", {"npm:nodepkg": package(
        "nodepkg", "npm", "1.3.0-beta.1", 1.79e9,
        recent=[{"version": "1.3.0-beta.1", "released_at": 1.79e9}, {"version": "1.2.4", "released_at": 1.785e9},
                {"version": "1.2.3", "released_at": 1.78e9}])})
    osv = FakeProvider("osv", {"osv:PyPI:oldpkg@1.0.0": advisory("GHSA-aaaa-bbbb-cccc", ["1.0.5", "0.9.9"])})
    return [pypi, npm, osv]


def enabled_instance(tmp_path, capsys):
    db = str(tmp_path / "i.sqlite")
    assert nsf(capsys, "init", "--db", db)[0] == 0
    assert nsf(capsys, "web", "config", "--enable", "--set", "min_domain_interval=0", "--db", db)[0] == 0
    return db


def test_deps_report_recommendations_failures_and_no_installs(tmp_path, capsys, monkeypatch, project, web):
    providers = fake_providers()
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {"fixture": web, "providers": providers})

    def forbidden(*args, **kwargs):
        raise AssertionError(f"deps must not run a process: {args}")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    db = enabled_instance(tmp_path, capsys)
    code, out = nsf(capsys, "web", "deps", "--root", str(project), "--db", db)
    assert code == 0, out
    by = {p["name"]: p for p in out["packages"]}
    old = by["oldpkg"]
    assert old["recommendation"] == "upgrade (security)" and old["affected"] is True
    assert old["action"] == "upgrade to 1.0.5 or later" and old["latest"] == "2.1.0" and old["behind_by"] == "major"
    assert old["advisories"][0]["id"] == "GHSA-aaaa-bbbb-cccc" and old["advisories"][0]["affected"] is True
    assert old["advisories"][0]["severity"] == "HIGH" and old["advisories"][0]["aliases"] == ["CVE-2026-0001"]
    assert any("breaking changes possible" in n for n in old["compatibility"])
    assert any("requires Python >=3.99" in n for n in old["compatibility"])
    assert any("outside the declared range ==1.0.0" in n and "pin must change" in n for n in old["compatibility"])
    assert by["freshpkg"]["recommendation"] == "up to date" and by["freshpkg"]["advisories"] == []
    assert by["brokenpkg"]["recommendation"] == "unknown (lookup failed: timeout)"
    assert by["numpy"]["recommendation"] == "up to date"
    node = by["nodepkg"]
    assert node["latest"] == "1.2.4" and node["behind_by"] == "patch" and node["recommendation"] == "consider upgrade"
    assert any("pre-release" in n for n in node["compatibility"])
    assert out["packages"][0]["name"] == "oldpkg"   # security first
    assert {"package": "brokenpkg", "lookup": "registry", "code": "timeout"}.items() <= next(
        f for f in out["failures"] if f["package"] == "brokenpkg").items()
    assert any(f["code"] == "missing_file" for f in out["failures"])   # no package.json in this project
    assert out["totals"] == {"packages": 5, "advisories": 1, "affected_packages": 1, "upgrade_security": 1,
                             "unknown": 1, "up_to_date": 2, "consider_upgrade": 1, "behind_major": 1}
    assert out["statement"] == deps.NO_CHANGES and out["mode"] == "full"
    assert out["usage"]["requests"] == 0   # fakes do not fetch; the budget is still sized and clamped
    assert out["budget"]["max_requests"] <= 200
    # the normal research task ran with the packages as structured context
    assert out["task"]["status"] in ("completed", "partial") and "first 4 of 5" in out["task"]["coverage"]
    code, task = nsf(capsys, "web", "show", out["task"]["id"], "--db", db)
    assert code == 0 and task["mode"] == "technical" and task["objective"].endswith("(5 packages)")
    assert "pypi:oldpkg" in providers[0].calls and "osv:PyPI:oldpkg@1.0.0" in providers[2].calls
    # outcomes fed procedural memory
    code, status = nsf(capsys, "web", "status", "--db", db)
    assert status["provider_health"]["pypi"]["failures"] >= 1


def test_deps_audit_checks_advisories_only(tmp_path, capsys, monkeypatch, project, web):
    providers = fake_providers()
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {"fixture": web, "providers": providers})
    db = enabled_instance(tmp_path, capsys)
    code, out = nsf(capsys, "web", "deps", "--audit", "--root", str(project), "--db", db)
    assert code == 0 and out["mode"] == "audit" and out["task"] is None
    assert {p["name"] for p in out["packages"]} == {"numpy", "nodepkg"}   # installed Python + npm lock only
    assert providers[0].calls == [] and providers[1].calls == []          # no registry lookups
    assert all(p["recommendation"] == "no known advisories" for p in out["packages"])
    assert out["totals"]["no_known_advisories"] == 2


def test_deps_queue_leaves_the_task_for_the_worker(tmp_path, capsys, monkeypatch, project, web):
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {"fixture": web, "providers": fake_providers()})
    db = enabled_instance(tmp_path, capsys)
    code, out = nsf(capsys, "web", "deps", "--queue", "--root", str(project), "--db", db)
    assert code == 0 and out["task"]["queued"] is True and out["task"]["status"] == "queued"
    code, worked = nsf(capsys, "web", "work", "--once", "--db", db)
    assert code == 0 and [p["task_id"] for p in worked["processed"]] == [out["task"]["id"]]


def test_budget_is_clamped_by_the_owner_ceiling(tmp_path, capsys, monkeypatch, project, web):
    providers = fake_providers()
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {"fixture": web, "providers": providers})
    db = enabled_instance(tmp_path, capsys)
    assert nsf(capsys, "web", "config", "--set", 'budget_ceiling={"max_requests": 3}', "--db", db)[0] == 0
    code, out = nsf(capsys, "web", "deps", "--audit", "--root", str(project), "--db", db)
    assert code == 0 and out["budget"]["max_requests"] == 3
