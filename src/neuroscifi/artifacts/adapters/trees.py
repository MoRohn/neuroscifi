"""Nested data to `Tree` (lane B1): XML (and RSS/Atom), XML property lists, TOML, INI; YAML only as its JSON subset.

- XML through `safe_b1.parse_xml` (DTDs and entities refused; nodes and depth bounded). Each element is a `Node`:
  `name` is the tag with its namespace kept as `{uri}local`, `attrs` its attributes (names likewise), `value` its
  leading text (stripped; None when blank). Text that follows a child element (mixed content) becomes a `#text`
  child node in place. Comments and processing instructions are dropped (noted).
- XML property lists (`<plist>`): `dict` entries become nodes named by their key; `array` items are named `0`,
  `1`, ...; scalars keep their type (`integer` -> int, `real` -> float, `true`/`false` -> bool, `date` -> the ISO
  text with `attrs["type"] = "date"`); `data` is not decoded (`attrs = {"type": "data", "base64_chars": n}`). The
  usual DOCTYPE naming Apple's DTD is accepted without being loaded (see `parse_xml`). Binary property lists are
  identified and `Unsupported` (the standard library's binary reader has no size or depth bounds).
- TOML through `tomllib`; INI through `configparser` (no interpolation, keys keep their case, `[DEFAULT]` is an
  ordinary section, repeated sections merged); values stay text. Tables and sections are nodes, keys are leaves.
- YAML: no YAML parser is installed. A YAML file that is valid JSON (YAML's JSON subset) is read as such; anything
  else is `Unsupported(needs=("a YAML parser (PyYAML)",))` with the line count and top-level keys found by the
  pattern `^key:` in the message.
"""
from __future__ import annotations

import configparser
import datetime as _dt
import re
import tomllib

from ..safe_b1 import Budget, Deadline, decode_text, injection_notes, local, parse_xml, xml_node_budget, \
    XML_NODE_COST
from ..types import Decoded, Malformed, Node, Refused, Tree, Unsupported
from . import Adapter
from .tables import load_json

MAX_TREE_DEPTH = 256


def value_tree(name: str, value, budget: Budget, depth: int = 0) -> Node:
    """A JSON/TOML-like value (dicts, lists, scalars) as a `Node` tree."""
    if depth > MAX_TREE_DEPTH:
        raise Refused(f"the data nests deeper than {MAX_TREE_DEPTH} levels", rule="max_depth")
    budget.take_elements(1)
    if isinstance(value, dict):
        return Node(str(name), None, {"type": "table"},
                    tuple(value_tree(str(k), v, budget, depth + 1) for k, v in value.items()))
    if isinstance(value, list):
        return Node(str(name), None, {"type": "array"},
                    tuple(value_tree(str(i), v, budget, depth + 1) for i, v in enumerate(value)))
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return Node(str(name), value.isoformat(), {"type": type(value).__name__})
    return Node(str(name), value)


def xml_tree(root, budget: Budget) -> Node:
    """ElementTree element -> Node, iteratively (post-order), mixed-content tails as `#text` children."""
    budget.take_elements(sum(1 for _ in root.iter()))
    built: dict[int, Node] = {}
    stack = [(root, False)]
    while stack:
        el, done = stack.pop()
        if not done:
            stack.append((el, True))
            stack.extend((child, False) for child in reversed(list(el)))
            continue
        kids = []
        for child in el:
            kids.append(built.pop(id(child)))
            if child.tail and child.tail.strip():
                kids.append(Node("#text", child.tail.strip()))
        text = (el.text or "").strip() or None
        built[id(el)] = Node(el.tag, text, dict(el.attrib), tuple(kids))
    return built[id(root)]


def plist_tree(el, name: str, budget: Budget, depth: int = 0) -> Node:
    if depth > MAX_TREE_DEPTH:
        raise Refused(f"the property list nests deeper than {MAX_TREE_DEPTH} levels", rule="max_depth")
    budget.take_elements(1)
    tag = local(el.tag)
    if tag == "dict":
        kids, key = [], None
        for child in el:
            if local(child.tag) == "key":
                key = child.text or ""
            else:
                kids.append(plist_tree(child, key if key is not None else "?", budget, depth + 1))
                key = None
        return Node(name, None, {"type": "dict"}, tuple(kids))
    if tag == "array":
        return Node(name, None, {"type": "array"},
                    tuple(plist_tree(c, str(i), budget, depth + 1) for i, c in enumerate(el)))
    text = el.text or ""
    if tag == "integer":
        try:
            return Node(name, int(text.strip()) if len(text.strip()) < 40 else text.strip(), {"type": "integer"})
        except ValueError:
            raise Malformed("a property-list integer is not a number") from None
    if tag == "real":
        try:
            return Node(name, float(text.strip()), {"type": "real"})
        except ValueError:
            raise Malformed("a property-list real is not a number") from None
    if tag in ("true", "false"):
        return Node(name, tag == "true", {"type": "boolean"})
    if tag == "data":
        return Node(name, None, {"type": "data", "base64_chars": str(len("".join(text.split())))})
    if tag == "date":
        return Node(name, text.strip(), {"type": "date"})
    if tag == "string":
        return Node(name, text, {"type": "string"})
    raise Malformed(f"unknown property-list element <{tag[:40]}>")


def read_xml(data, detected, limits):
    deadline, budget = Deadline.of(limits), Budget(limits)
    root = parse_xml(data, max_nodes=xml_node_budget(budget), deadline=deadline)
    budget.take_bytes(min(budget.bytes_left, XML_NODE_COST * sum(1 for _ in root.iter())))
    tree = Tree(xml_tree(root, budget))
    notes = ("namespaces kept as {uri}local; comments and processing instructions dropped",)
    return Decoded(detected, tree, notes + injection_notes(tree.text()), False)


def read_plist(data, detected, limits):
    if data.startswith(b"bplist"):
        raise Unsupported("binary property list identified; it is not decoded here (no bounded binary-plist "
                          "reader)", needs=("a bounded binary property-list reader",))
    deadline, budget = Deadline.of(limits), Budget(limits)
    root = parse_xml(data, max_nodes=xml_node_budget(budget), deadline=deadline, allow_doctype_reference=True)
    if local(root.tag) != "plist":
        raise Malformed("the root element is not <plist>")
    kids = [plist_tree(child, "root", budget) for child in root]
    tree = Tree(Node("plist", None, dict(root.attrib), tuple(kids)))
    notes = ("a DOCTYPE naming an external DTD is accepted and never loaded", "data values are not decoded")
    return Decoded(detected, tree, notes + injection_notes(tree.text()), False)


def read_toml(data, detected, limits):
    budget = Budget(limits)
    text, notes = decode_text(data)
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise Malformed(f"not valid TOML ({str(error).split('(')[0].strip()[:80]})") from None
    except RecursionError:
        raise Refused("the TOML nests too deeply to read", rule="max_depth") from None
    tree = Tree(value_tree("toml", doc, budget))
    return Decoded(detected, tree, tuple(notes) + injection_notes(tree.text()), False)


def read_ini(data, detected, limits):
    budget = Budget(limits)
    text, notes = decode_text(data)
    parser = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True,
                                       default_section="\x00no default section\x00")
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise Malformed(f"not valid INI ({type(error).__name__})") from None
    sections = []
    for section in parser.sections():
        budget.take_elements(1 + len(parser[section]))
        sections.append(Node(section, None, {"type": "section"},
                             tuple(Node(k, v) for k, v in parser.items(section, raw=True))))
    tree = Tree(Node("ini", None, {}, tuple(sections)))
    notes = tuple(notes) + ("values kept as text; no interpolation; repeated sections merged",)
    return Decoded(detected, tree, notes + injection_notes(tree.text()), False)


def read_yaml(data, detected, limits):
    text, notes = decode_text(data)
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            value = load_json(stripped)
        except Malformed:
            value = None
        if value is not None:
            tree = Tree(value_tree("yaml", value, Budget(limits)))
            return Decoded(detected, tree, tuple(notes) + ("read as YAML's JSON subset",) +
                           injection_notes(tree.text()), False)
    keys = re.findall(r"(?m)^([A-Za-z_][\w.-]*)\s*:", text)
    raise Unsupported(f"YAML identified ({text.count(chr(10)) + 1} lines, {len(keys)} top-level key(s)); no YAML "
                      "parser is installed and it is not the JSON subset", needs=("a YAML parser (PyYAML)",))


def _adapter(adapter_name, fmts, reader):
    class _A(Adapter):
        name = adapter_name
        formats = fmts
        families = ("tree",)

        def decode(self, data, detected, limits):
            return reader(data, detected, limits)
    _A.__name__ = adapter_name.replace("-", "_")
    return _A


XmlAdapter = _adapter("b1-xml", ("xml", "rss", "atom"), read_xml)
PlistAdapter = _adapter("b1-plist", ("plist",), read_plist)
TomlAdapter = _adapter("b1-toml", ("toml",), read_toml)
IniAdapter = _adapter("b1-ini", ("ini", "cfg"), read_ini)
YamlAdapter = _adapter("b1-yaml", ("yaml", "yml"), read_yaml)

ADAPTERS = (XmlAdapter, PlistAdapter, TomlAdapter, IniAdapter, YamlAdapter)

__all__ = ["ADAPTERS", "IniAdapter", "PlistAdapter", "TomlAdapter", "XmlAdapter", "YamlAdapter", "plist_tree",
           "read_ini", "read_plist", "read_toml", "read_xml", "read_yaml", "value_tree", "xml_tree"]
