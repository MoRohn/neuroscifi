"""Tier C doodles (docs/presence/DOODLE_GRAMMAR.md §Tier C): the local model may PROPOSE a declarative scene spec.

The spec is data in a typed, bounded, versioned JSON schema: an entity body, eyes, one motion kind and at most six
primitive elements placed in the prop area, coloured from the palette by name. It is never markup, code, a path
string or a command. It is validated here with the gateway's JSON-schema check plus geometric rules, and validated
again in the browser (web/validator.mjs) before display. Any failure -> the nearest canonical composition.
Calls go through neuroscifi.inference only (local-first, ledgered, privacy-screened for remote endpoints).
"""
from __future__ import annotations

import re
import threading

from . import activity

SCHEMA_ID = "nsf.presence.scene/1"
PRIMITIVES = ("curve", "line", "arc", "loop", "circle", "dot", "blob", "ribbon", "wave", "spiral", "ring", "rays",
              "orbit", "branch", "node", "polygon", "container", "connector")
TONES = ("ink", "secondary", "purple", "blue", "mint", "yellow", "pink")
ACCENT_TONES = ("purple", "blue", "mint", "yellow", "pink")
BODIES = ("present", "thinking", "working", "responding", "exploring", "resting")
EYES = ("open", "closed", "focused", "none")
MOTIONS = ("still", "breathe", "orbit", "scan", "pulse", "drift", "converge")
CANONICAL = ("present", "thinking", "working", "responding", "exploring", "resting", "reading", "computing",
             "researching", "discovery", "ideation", "experiment", "exploration", "construction", "execution",
             "listening", "growth", "achievement")
CONCEPT = r"^[a-z][a-z ]{0,47}$"
MAX_ELEMENTS = 6
PROP_BOX = (40.0, 8.0, 96.0, 92.0)   # x0, y0, x1, y1 in the 100x100 viewBox (the body occupies the left)

ELEMENT = {"type": "object", "additionalProperties": False,
           "required": ["primitive", "x", "y", "size", "rotation", "tone"],
           "properties": {"primitive": {"enum": list(PRIMITIVES)},
                          "x": {"type": "number", "minimum": 40, "maximum": 96},
                          "y": {"type": "number", "minimum": 8, "maximum": 92},
                          "size": {"type": "number", "minimum": 4, "maximum": 36},
                          "rotation": {"type": "number", "minimum": -180, "maximum": 180},
                          "tone": {"enum": list(TONES)},
                          "count": {"type": "integer", "minimum": 1, "maximum": 5},
                          "sides": {"type": "integer", "minimum": 3, "maximum": 8}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["schema", "concept", "body", "eyes", "motion", "elements"],
          "properties": {"schema": {"const": SCHEMA_ID},
                         "concept": {"type": "string", "pattern": CONCEPT},
                         "body": {"enum": list(BODIES)}, "eyes": {"enum": list(EYES)},
                         "motion": {"enum": list(MOTIONS)},
                         "elements": {"type": "array", "minItems": 1, "maxItems": MAX_ELEMENTS, "items": ELEMENT}}}

# Nearest canonical composition for a concept (also the client's fallback table, web/planner.mjs NEAREST).
NEAREST = (("read|document|paper|book|article", "reading"), ("comput|code|analy|data|calculat", "computing"),
           ("search|research|investigat|look|scan|find", "researching"), ("launch|start|discover", "discovery"),
           ("idea|predict|insight|hypothes", "ideation"), ("experiment|test|simulat|trial", "experiment"),
           ("map|explor|plan|route|navigat", "exploration"), ("build|construct|assembl|consolidat|organi",
                                                              "construction"),
           ("execut|run|process|tool|gear|operat", "execution"), ("listen|audio|sound|voice|hear", "listening"),
           ("learn|grow|feedback|improv", "growth"), ("finish|done|achiev|complet|goal", "achievement"),
           ("think|reason|consider|evaluat|compar|verif", "thinking"), ("respond|answer|reply|speak", "responding"),
           ("rest|sleep|idle|wait", "resting"))


def nearest(concept: str) -> str:
    text = (concept or "").lower()
    for pattern, name in NEAREST:
        if re.search(pattern, text):
            return name
    return "present"


def _box(el):
    half = el["size"] / 2
    return el["x"] - half, el["y"] - half, el["x"] + half, el["y"] + half


_SMALL = ("dot", "node", "circle", "ring", "blob", "polygon", "container")
_MOUTH = ("arc", "curve", "connector", "wave", "ribbon", "loop", "line", "spiral")
_OUTLINE = ("ring", "circle", "blob", "container", "polygon", "orbit")


def _flat(rotation) -> bool:
    r = abs(rotation) % 180
    return r <= 35 or r >= 145


def _eye_like(el) -> bool:
    if el["primitive"] in ("dot", "node"):
        return el["size"] <= 14
    if el["primitive"] in _SMALL:
        return el["size"] <= 12
    return el["primitive"] == "line" and el["size"] <= 10 and not _flat(el["rotation"])


def face_pattern(els):
    """No cartoon faces (mirrors web/composer.mjs facePattern): two small marks side by side above a curve or line,
    or inside a round outline. Returns (i, j, k) or None."""
    for i in range(len(els)):
        for j in range(i + 1, len(els)):
            a, b = els[i], els[j]
            if not (_eye_like(a) and _eye_like(b)):
                continue
            dx = abs(a["x"] - b["x"])
            if abs(a["y"] - b["y"]) > 5 or dx < 4 or dx > 34:
                continue
            mx, ey = (a["x"] + b["x"]) / 2, max(a["y"], b["y"])
            for k, m in enumerate(els):
                if k in (i, j):
                    continue
                if (m["primitive"] in _MOUTH and m["size"] >= 8 and _flat(m["rotation"]) and 3 <= m["y"] - ey <= dx + 14
                        and abs(m["x"] - mx) <= dx / 2 + 4):
                    return i, j, k
                h = m["size"] / 2
                if (m["primitive"] in _OUTLINE and m["size"] >= 14 and min(a["x"], b["x"]) > m["x"] - h
                        and max(a["x"], b["x"]) < m["x"] + h and min(a["y"], b["y"]) > m["y"] - h and ey <= m["y"] + 2):
                    return i, j, k
    return None


def validate_spec(value) -> list[str]:
    """Schema check plus the geometric rules of the doodle grammar; [] means displayable."""
    from ..inference.jsonschema import validate
    errors = validate(value, SCHEMA)
    if errors:
        return errors
    els = value["elements"]
    x0, y0, x1, y1 = PROP_BOX
    for i, el in enumerate(els):
        bx0, by0, bx1, by1 = _box(el)
        if bx0 < x0 - 4 or by0 < y0 - 4 or bx1 > x1 + 2 or by1 > y1 + 2:
            errors.append(f"$.elements[{i}]: outside the prop area")
    accents = [el["tone"] for el in els if el["tone"] in ACCENT_TONES]
    if len(accents) > 3:
        errors.append("more than three accent-coloured elements (accents are sparse)")
    if len(set(accents)) > 2:
        errors.append("more than two accent colours")
    for i in range(len(els)):
        for j in range(i + 1, len(els)):
            a, b = els[i], els[j]
            ratio = a["size"] / b["size"]
            if abs(a["x"] - b["x"]) < 3 and abs(a["y"] - b["y"]) < 3 and 0.8 <= ratio <= 1.25:
                errors.append(f"$.elements[{i}] and [{j}] are stacked on each other (unreadable)")
    strokes = sum(el.get("count", 1) if el["primitive"] in ("rays", "dot", "node") else 1 for el in els)
    if strokes > 10:
        errors.append("too many strokes for the complexity ceiling")
    face = face_pattern(els)
    if face:
        errors.append(f"$.elements[{face[0]}] and [{face[1]}] form a face with [{face[2]}] (no cartoon faces)")
    return errors


PROMPT = ("You design one minimal, abstract line doodle for a status indicator. Reply with ONLY a JSON object that "
          "matches the given JSON schema. The entity body is drawn on the left; place 1 to 4 simple elements in the "
          "prop area (x 40-96, y 8-92 of a 100x100 canvas) that suggest the concept. Use 'ink' for most strokes and "
          "at most two sparse accent colours. No text, no letters, no faces: never two small marks side by side "
          "above a curve. The entity's eye marks follow its activity energy, so 'eyes' is open, closed, focused "
          "(working chevrons) or none.")

_lock = threading.Lock()
_results: dict[str, dict] = {}
MAX_CACHED = 64


def propose(db, db_path, concept: str, *, gateway=None) -> dict:
    """{"status": "ok", "spec": ...} or {"status": not_configured|busy|unavailable|invalid, "fallback": name}."""
    fallback = nearest(concept)
    if not isinstance(concept, str) or not re.match(CONCEPT, concept):
        return {"status": "invalid", "errors": ["concept must be 1-48 lowercase letters and spaces"],
                "fallback": fallback}
    if concept in _results:
        return _results[concept]
    from ..inference import config as iconfig
    from ..inference.gateway import Gateway, InferenceError
    if gateway is None:
        if not iconfig.get_config(db).configured:
            return {"status": "not_configured", "fallback": fallback,
                    "reason": "no inference endpoint is configured (`nsf llm config`)"}
        gateway = Gateway.from_db(db, db_path)
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "fallback": fallback, "reason": "another scene proposal is running"}
    try:
        messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": f"Concept: {concept}"}]
        with activity.track("generating", subsystem="reasoning", purpose="presence_scene") as tokens:
            try:
                value, results = gateway.structured(messages, SCHEMA, name="nsf_presence_scene",
                                                    purpose="presence_scene", max_tokens=500, temperature=0.4,
                                                    stream=True, on_delta=lambda text: tokens(1))
            except InferenceError as err:
                return {"status": "unavailable", "fallback": fallback, "reason": err.code}
        value = dict(value)
        value["concept"] = concept   # the requester's words, never the model's
        errors = validate_spec(value)
        if errors:
            return {"status": "invalid", "errors": errors[:5], "fallback": fallback}
        out = {"status": "ok", "spec": value, "model": results[-1].model}
        if len(_results) >= MAX_CACHED:
            _results.pop(next(iter(_results)))
        _results[concept] = out
        return out
    finally:
        _lock.release()


__all__ = ["CANONICAL", "SCHEMA", "SCHEMA_ID", "nearest", "propose", "validate_spec"]
