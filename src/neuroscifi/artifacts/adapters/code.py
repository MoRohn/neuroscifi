"""Source code and dependency manifests to `Graph` (lane B1). Nothing is imported, executed or evaluated.

Python (through `ast.parse` only): nodes `module:<m>`, `class:<m>.<Qual>`, `function:<m>.<qual>` (methods too),
`import:<dotted>` for modules imported from outside the file or package, and `unresolved:<name>` for call or base
targets defined nowhere in the file or package (builtins included, marked `builtin`). Edges: `defines` (module ->
class/function, class -> method, function -> nested function), `imports`, `calls`, `inherits`. A call is resolved
by name, in this order: `self.x`/`cls.x` to a method of the enclosing class; a name defined at the top of the same
module; a name bound by an import (`from m import f`, `import m` then `m.f`) to a definition in the package when
there is one; otherwise it is kept unresolved with its written name. No type inference: `obj.method()` on any other
receiver stays unresolved. Node attributes: line numbers, the first line of the docstring, decorators, base names.
`outline(graph)` turns a Python graph into a `Document` outline (modules, classes, functions as nested headings).
Bounds: each file at most PY_FILE_MAX bytes; all files together at most max_decoded_bytes / 256 (the syntax tree
and graph cost about 250 times the source, measured); nodes and edges count against max_elements.

JavaScript/TypeScript, C/C++ and shell: an honest minimum, said in the notes as a surface reading: line counts,
comment lines and their share, and top-level definitions and imports found by the line patterns below (lines over
500 characters, such as minified code, are counted but not matched). Nothing is parsed and calls are not traced.

Manifests: `pyproject.toml` ([project] dependencies and optional-dependencies, [build-system] requires, Poetry
tables), `requirements.txt` (requirement lines; `-r`/`-c` includes are named, never followed; options ignored),
`package.json` (dependencies, devDependencies, peerDependencies, optionalDependencies) -> a `Graph` with one
project node and one `package:<normalised name>` node per dependency, edges `depends`, `optional`, `build`, `dev`,
`peer`; each package node keeps the written version specification as text.
"""
from __future__ import annotations

import ast
import builtins
import json
import re
import tomllib
from collections.abc import Mapping

from ..safe_b1 import Budget, Deadline, decode_text, injection_notes
from ..types import Block, Decoded, Document, Edge, GNode, Graph, Malformed, Refused
from . import Adapter

PY_FILE_MAX = 4 << 20
AST_COST = 256                 # bytes of memory per byte of source while parsing (measured about 250)
_BUILTINS = frozenset(dir(builtins))


# ---------------------------------------------------------------------------------------------------------------
# Python


def module_name(path: str) -> str:
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; 'pkg/__init__.py' -> 'pkg'."""
    path = path.replace("\\", "/").lstrip("./")
    if path.endswith(".py"):
        path = path[:-3]
    parts = [p for p in path.split("/") if p and p != "."]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__main__"


def _doc(node) -> str | None:
    try:
        doc = ast.get_docstring(node, clean=True)
    except Exception:
        return None
    return doc.strip().splitlines()[0][:200] if doc and doc.strip() else None


def _dotted(expr) -> str | None:
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    return None


class _Builder:
    """Accumulates nodes and edges of one graph within the element budget."""

    def __init__(self, budget: Budget):
        self.budget = budget
        self.nodes: dict[str, GNode] = {}
        self.edges: dict[tuple, Edge] = {}

    def node(self, nid: str, kind: str, label: str, attrs: dict | None = None) -> str:
        if nid not in self.nodes:
            self.budget.take_elements(1)
            self.nodes[nid] = GNode(nid, kind, label, attrs or {})
        return nid

    def edge(self, src: str, dst: str, kind: str) -> None:
        key = (src, dst, kind)
        if key not in self.edges:
            self.budget.take_elements(1)
            self.edges[key] = Edge(src, dst, kind)

    def graph(self) -> Graph:
        return Graph(tuple(self.nodes.values()), tuple(self.edges.values()))


class _Module:
    """Definitions and bindings of one parsed module (first pass)."""

    def __init__(self, name: str, tree: ast.Module, lines: int, is_package: bool = False):
        self.name, self.tree, self.lines, self.is_package = name, tree, lines, is_package
        self.top: dict[str, str] = {}             # top-level name -> node id
        self.methods: dict[str, dict[str, str]] = {}   # class node id -> method name -> node id
        self.aliases: dict[str, str] = {}         # local name -> dotted target ("pkg.mod" or "pkg.mod.f")


def _first_pass(mod: _Module, b: _Builder) -> list[tuple[str, ast.AST, str | None]]:
    """Create definition nodes; returns (scope node id, body node, enclosing class id) for the call pass."""
    mid = b.node(f"module:{mod.name}", "module", mod.name, {"lines": mod.lines, "doc": _doc(mod.tree)})
    scopes = [(mid, mod.tree, None)]
    stack = [(mod.tree, mid, mod.name, None, 0)]
    while stack:
        node, parent, qual, cls, depth = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(child, ast.ClassDef)
                q = f"{qual}.{child.name}"
                nid = f"{'class' if is_class else 'function'}:{q}"
                attrs = {"line": child.lineno, "end_line": getattr(child, "end_lineno", None), "doc": _doc(child),
                         "decorators": [d for d in (_dotted(x if not isinstance(x, ast.Call) else x.func)
                                                    for x in child.decorator_list) if d][:16]}
                if is_class:
                    attrs["bases"] = [d for d in (_dotted(x) for x in child.bases) if d][:16]
                else:
                    attrs["async"] = isinstance(child, ast.AsyncFunctionDef)
                    attrs["method"] = cls is not None and parent == cls
                b.node(nid, "class" if is_class else "function", child.name, attrs)
                b.edge(parent, nid, "defines")
                if parent == mid:
                    mod.top[child.name] = nid
                if cls is not None and parent == cls and not is_class:
                    mod.methods.setdefault(cls, {})[child.name] = nid
                if is_class:
                    mod.methods.setdefault(nid, {})
                    stack.append((child, nid, q, nid, depth + 1))
                else:
                    scopes.append((nid, child, cls if parent == cls else None))
                    stack.append((child, nid, q, None, depth + 1))
            elif isinstance(child, (ast.Import, ast.ImportFrom)) and parent == mid:
                _bind_import(mod, child)
            elif parent == mid and isinstance(child, (ast.If, ast.Try)):
                stack.append((child, parent, qual, cls, depth))     # definitions under a module-level if/try
    return scopes


def _bind_import(mod: _Module, node) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            mod.aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else \
                alias.name.split(".")[0]
    else:
        base = node.module or ""
        if node.level:
            parts = mod.name.split(".")
            if not mod.is_package:
                parts = parts[:-1]                    # a module's package is its parent
            parts = parts[:max(0, len(parts) - (node.level - 1))]
            base = ".".join([p for p in parts if p] + ([base] if base else []))
        for alias in node.names:
            if alias.name == "*":
                continue
            mod.aliases[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name


def _calls_in(scope: ast.AST):
    """Calls written directly in this scope (not inside nested function or class definitions)."""
    stack = list(ast.iter_child_nodes(scope)) if not isinstance(scope, ast.Module) else list(scope.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack.extend(node.decorator_list)
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _resolve(name: str, mod: _Module, cls: str | None, modules: dict[str, _Module]) -> str | None:
    head, _, rest = name.partition(".")
    if head in ("self", "cls") and cls and rest and "." not in rest:
        return modules[mod.name].methods.get(cls, {}).get(rest)
    if not rest and head in mod.top:
        return mod.top[head]
    target = None
    if head in mod.aliases:
        target = mod.aliases[head] + (("." + rest) if rest else "")
    if target is None:
        return None
    return _lookup(target, modules)


def _lookup(dotted: str, modules: dict[str, _Module]) -> str | None:
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        mname, remainder = ".".join(parts[:cut]), parts[cut:]
        if mname in modules:
            m = modules[mname]
            if not remainder:
                return f"module:{mname}"
            nid = m.top.get(remainder[0])
            if nid and len(remainder) == 2 and nid.startswith("class:"):
                return m.methods.get(nid, {}).get(remainder[1])
            return nid if len(remainder) == 1 else None
    return None


def python_package_graph(files: Mapping[str, bytes], limits) -> tuple[Graph, list[str]]:
    """One `Graph` for Python files given as {path: bytes}; calls resolved across them. Files that do not parse are
    named in the notes and left out."""
    deadline, budget = Deadline.of(limits), Budget(limits)
    total = sum(len(v) for v in files.values())
    if total * AST_COST > limits.max_decoded_bytes:
        raise Refused(f"{total} bytes of Python source would need more memory than allowed to parse",
                      rule="max_decoded_bytes", reason="too_large")
    notes: list[str] = []
    modules: dict[str, _Module] = {}
    for path in sorted(files):
        deadline.check()
        raw = files[path]
        if len(raw) > PY_FILE_MAX:
            notes.append(f"{path}: larger than {PY_FILE_MAX} bytes, not parsed")
            continue
        text, _ = decode_text(raw)
        try:
            tree = ast.parse(text, filename="<artifact>", type_comments=False)
        except SyntaxError as error:
            notes.append(f"{path}: not valid Python (line {error.lineno}), left out")
            continue
        except (RecursionError, MemoryError, ValueError):
            notes.append(f"{path}: nests too deeply to parse, left out")
            continue
        name = module_name(path)
        if name in modules:
            notes.append(f"{path}: a second file for module {name}, left out")
            continue
        modules[name] = _Module(name, tree, text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                                path.replace("\\", "/").endswith("__init__.py"))
    if not modules:
        raise Malformed("no file holds valid Python")
    b = _Builder(budget)
    scopes = {}
    for name, mod in modules.items():
        scopes[name] = _first_pass(mod, b)
    unresolved = 0
    for name, mod in modules.items():
        deadline.check()
        mid = f"module:{name}"
        for alias, target in mod.aliases.items():
            found = _lookup(target, modules)
            if found:
                b.edge(mid, found, "imports")
            else:
                b.edge(mid, b.node(f"import:{target}", "import", target), "imports")
        for sid, node, cls in scopes[name]:
            if isinstance(node, ast.ClassDef):
                continue
            for call in _calls_in(node):
                written = _dotted(call.func)
                if not written:
                    continue
                target = _resolve(written, mod, cls, modules)
                if target is None:
                    unresolved += 1
                    builtin = written in _BUILTINS
                    target = b.node(f"unresolved:{written}", "unresolved", written,
                                    {"builtin": builtin} if builtin else {})
                b.edge(sid, target, "calls")
        for cid in list(mod.methods):
            cnode = b.nodes[cid]
            for base in cnode.attrs.get("bases", ()):
                target = _resolve(base, mod, None, modules) or b.node(f"unresolved:{base}", "unresolved", base,
                                                                      {"builtin": base in _BUILTINS})
                b.edge(cid, target, "inherits")
    notes.append("Python read with ast only (never imported or run); calls resolved by name, no type inference")
    if unresolved:
        notes.append(f"{unresolved} call(s) name something defined outside the file or package: kept unresolved")
    return b.graph(), notes


def outline(graph: Graph) -> Document:
    """A Document outline of a Python graph: modules, classes and functions as nested headings, in line order."""
    children: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "defines":
            children.setdefault(e.src, []).append(e.dst)
    nodes = {n.id: n for n in graph.nodes}
    blocks = []
    for root in sorted(n.id for n in graph.nodes if n.kind == "module"):
        stack = [(root, 1)]
        while stack:
            nid, level = stack.pop()
            n = nodes[nid]
            blocks.append(Block("heading", min(level, 9), f"{n.kind} {n.label}", {"node": nid}))
            if n.attrs.get("doc"):
                blocks.append(Block("paragraph", 0, n.attrs["doc"]))
            kids = sorted(children.get(nid, ()), key=lambda k: nodes[k].attrs.get("line") or 0, reverse=True)
            stack.extend((k, level + 1) for k in kids)
    return Document(tuple(blocks), None)


def read_python(data, detected, limits):
    graph, notes = python_package_graph({"module.py": data}, limits)
    if not graph.nodes:
        raise Malformed("not valid Python")
    if any("not valid Python" in n for n in notes):
        raise Malformed(next(n for n in notes if "not valid Python" in n).replace("module.py: ", "the source is "))
    return Decoded(detected, graph, tuple(notes + list(injection_notes(graph.text()))), False)


# ---------------------------------------------------------------------------------------------------------------
# Other languages: a surface reading

SURFACE = {
    "javascript": {
        "line": "//", "block": ("/*", "*/"),
        "defs": [(re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
                  "function"),
                 (re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
                 (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
                             r"(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"), "function")],
        "imports": [re.compile(r"^import\s.*?from\s+['\"]([^'\"]+)['\"]"), re.compile(r"^import\s+['\"]([^'\"]+)['\"]"),
                    re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")],
    },
    "c": {
        "line": "//", "block": ("/*", "*/"),
        "defs": [(re.compile(r"^(?!(?:if|for|while|switch|return|else|do)\b)[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*"
                             r"\([^;{}]*\)\s*\{?\s*$"), "function"),
                 (re.compile(r"^(?:typedef\s+)?(?:struct|union|enum)\s+([A-Za-z_]\w*)\s*\{"), "type")],
        "imports": [re.compile(r"^#\s*include\s*[<\"]([^>\"]+)[>\"]")],
    },
    "shell": {
        "line": "#", "block": None,
        "defs": [(re.compile(r"^function\s+([A-Za-z_][\w-]*)"), "function"),
                 (re.compile(r"^([A-Za-z_][\w-]*)\s*\(\)\s*\{?"), "function")],
        "imports": [re.compile(r"^(?:source|\.)\s+(\S+)")],
    },
}
LANGUAGE_OF = {"javascript": "javascript", "js": "javascript", "typescript": "javascript", "ts": "javascript",
               "c": "c", "cpp": "c", "c++": "c", "h": "c", "shell": "shell", "sh": "shell", "bash": "shell"}


def read_surface(data, detected, limits):
    lang = LANGUAGE_OF.get(detected.format, "c")
    rules = SURFACE[lang]
    deadline, budget = Deadline.of(limits), Budget(limits)
    text, tnotes = decode_text(data)
    lines = text.splitlines()
    b = _Builder(budget)
    mid = b.node("module:source", "module", "source", {"language": lang})
    blank = comment = 0
    in_block = False
    for i, line in enumerate(lines, 1):
        if i % 4096 == 0:
            deadline.check()
        s = line.strip()
        if not s:
            blank += 1
            continue
        if in_block:
            comment += 1
            if rules["block"][1] in s:
                in_block = False
            continue
        if rules["block"] and s.startswith(rules["block"][0]):
            comment += 1
            in_block = rules["block"][1] not in s[len(rules["block"][0]):]
            continue
        if s.startswith(rules["line"]) and not (lang == "shell" and i == 1 and s.startswith("#!")):
            if not (lang == "c" and s.startswith("#")):
                comment += 1
                continue
        if line[:1].isspace() or len(line) > 500:
            continue                                   # top-level definitions only; long lines are not matched
        for rx, kind in rules["defs"]:
            m = rx.match(line)
            if m:
                nid = b.node(f"{kind}:{m.group(1)}:{i}", kind, m.group(1), {"line": i})
                b.edge(mid, nid, "defines")
                break
        for rx in rules["imports"]:
            for m in rx.finditer(line):
                iid = b.node(f"import:{m.group(1)}", "import", m.group(1))
                b.edge(mid, iid, "imports")
    code_lines = len(lines) - blank
    share = comment / code_lines if code_lines else 0.0
    attrs = dict(b.nodes[mid].attrs, lines=len(lines), blank_lines=blank, comment_lines=comment,
                 comment_share=round(share, 4))
    b.nodes[mid] = GNode(mid, "module", "source", attrs)
    notes = list(tnotes) + [f"surface reading of {lang}: line counts and top-level definitions found by line "
                            "patterns, not parsed; calls are not traced"]
    graph = b.graph()
    return Decoded(detected, graph, tuple(notes + list(injection_notes(graph.text()))), False)


# ---------------------------------------------------------------------------------------------------------------
# Dependency manifests

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dep(b: _Builder, root: str, spec: str, kind: str, group: str | None = None) -> None:
    spec = spec.strip()
    m = _REQ_NAME.match(spec)
    if not m:
        return
    name = m.group(1)
    rest, _, marker = m.group(3).partition(";")
    attrs = {"spec": rest.strip()[:200] or None, "marker": marker.strip()[:200] or None}
    if m.group(2):
        attrs["extras"] = m.group(2)[1:-1][:200]
    if group:
        attrs["group"] = group[:100]
    nid = b.node(f"package:{_normalise(name)}", "package", name, attrs)
    b.edge(root, nid, kind)


def read_manifest(data, detected, limits):
    budget = Budget(limits)
    b = _Builder(budget)
    text, tnotes = decode_text(data)
    notes = list(tnotes)
    fmt = detected.format
    if fmt in ("pyproject", "pyproject-toml"):
        try:
            doc = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            raise Malformed("pyproject.toml is not valid TOML") from None
        project = doc.get("project") if isinstance(doc.get("project"), dict) else {}
        poetry = doc.get("tool", {}).get("poetry", {}) if isinstance(doc.get("tool"), dict) else {}
        name = str(project.get("name") or poetry.get("name") or "project")
        root = b.node(f"project:{_normalise(name)}", "project", name, {"version": str(project.get("version") or
                                                                                    poetry.get("version") or "")})
        for spec in project.get("dependencies", []) or []:
            _dep(b, root, str(spec), "depends")
        for extra, specs in (project.get("optional-dependencies") or {}).items():
            for spec in specs or []:
                _dep(b, root, str(spec), "optional", extra)
        build = doc.get("build-system", {}) if isinstance(doc.get("build-system"), dict) else {}
        for spec in build.get("requires", []) or []:
            _dep(b, root, str(spec), "build")
        for key, kind in (("dependencies", "depends"), ("dev-dependencies", "dev")):
            for dep, spec in (poetry.get(key) or {}).items():
                if dep.lower() != "python":
                    _dep(b, root, f"{dep} {spec if isinstance(spec, str) else ''}", kind)
        for group, body in (poetry.get("group") or {}).items():
            for dep, spec in ((body or {}).get("dependencies") or {}).items():
                _dep(b, root, f"{dep} {spec if isinstance(spec, str) else ''}", "dev", group)
    elif fmt in ("requirements", "requirements-txt"):
        root = b.node("project:requirements", "project", "requirements.txt")
        for line in text.splitlines():
            line = line.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r", "-c", "--requirement", "--constraint")):
                notes.append(f"includes another file ({line.split(None, 1)[-1][:100]}): named, not followed")
            elif line.startswith("-"):
                notes.append("an option line is ignored")
            elif "://" in line or line.startswith((".", "/")):
                nid = b.node(f"package:{line[:200]}", "package", line[:200], {"spec": None, "reference": True})
                b.edge(root, nid, "depends")
            else:
                _dep(b, root, line, "depends")
    else:
        try:
            doc = json.loads(text)
        except ValueError:
            raise Malformed("package.json is not valid JSON") from None
        if not isinstance(doc, dict):
            raise Malformed("package.json does not hold an object")
        name = str(doc.get("name") or "package")
        root = b.node(f"project:{name}", "project", name, {"version": str(doc.get("version") or "")})
        for key, kind in (("dependencies", "depends"), ("devDependencies", "dev"), ("peerDependencies", "peer"),
                          ("optionalDependencies", "optional")):
            deps = doc.get(key) or {}
            if isinstance(deps, dict):
                for dep, spec in deps.items():
                    nid = b.node(f"package:{dep}", "package", str(dep), {"spec": str(spec)[:200]})
                    b.edge(root, nid, kind)
        if isinstance(doc.get("scripts"), dict) and doc["scripts"]:
            notes.append(f"{len(doc['scripts'])} script(s) are declared: never run")
    notes.append("dependencies as declared; nothing was resolved, downloaded or installed")
    return Decoded(detected, b.graph(), tuple(notes), False)


# ---------------------------------------------------------------------------------------------------------------
# Adapters


class PythonAdapter(Adapter):
    name = "b1-python"
    formats = ("python", "py")
    families = ("code",)

    def decode(self, data, detected, limits):
        return read_python(data, detected, limits)


class SurfaceCodeAdapter(Adapter):
    name = "b1-code-surface"
    formats = tuple(LANGUAGE_OF)
    families = ("code",)

    def decode(self, data, detected, limits):
        return read_surface(data, detected, limits)


class ManifestAdapter(Adapter):
    name = "b1-manifests"
    formats = ("pyproject", "pyproject-toml", "requirements", "requirements-txt", "package-json")
    families = ("code",)

    def decode(self, data, detected, limits):
        return read_manifest(data, detected, limits)


ADAPTERS = (PythonAdapter, SurfaceCodeAdapter, ManifestAdapter)

__all__ = ["ADAPTERS", "ManifestAdapter", "PythonAdapter", "SURFACE", "SurfaceCodeAdapter", "module_name", "outline",
           "python_package_graph", "read_manifest", "read_python", "read_surface"]
