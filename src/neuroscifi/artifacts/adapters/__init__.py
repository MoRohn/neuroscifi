"""The adapter registry and the whole decode chain (DESIGN §3, ADR-072).

Detect -> Inspect -> Identify -> Resolve -> Decode -> Validate -> Represent:

- `decode(data, name=, declared=, limits=)` detects the format, resolves an available adapter for it, runs it under
  `limits`, checks that it returned a valid `Decoded`, and returns it. When nothing here can decode the bytes it
  returns a `Decoded` whose representation is `Opaque` (made by `inspect_unknown`), never an exception for
  "unknown". Failures of an adapter are named `ArtifactError`s; any other exception an adapter lets out (a bug, or
  hostile bytes it did not foresee) becomes `Malformed`, naming only the exception's type.
- Nesting: an adapter that decodes a container recurses with `decode(inner, limits=nested(limits, used))`;
  `nested` lowers `max_depth` by one and `max_decoded_bytes` by what was already materialised, and `decode` refuses
  when `max_depth` falls below zero.
- `catalog()` is the live capability list and the only thing that may be quoted as "supported".
- External adapters are discovered through the entry-point group `neuroscifi.artifact_adapters` (metadata only:
  nothing is imported) and are loaded only when the owner names them: `load_external(approved_names)`. Discovered
  but unnamed adapters appear in `catalog()` as "found, not approved"; `unload_external(approved_names)` removes a
  loaded one the owner no longer names.
- `on_gap(callback)`: called as `callback(detected, outcome)` with outcome "opaque" or "unsupported" for every
  decode that ends there (lane A counts them by format).
"""
from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable, Iterable
from dataclasses import replace

from ..types import ArtifactError, Decoded, Detected, Limits, Malformed, Opaque, Refused, Unsupported

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "neuroscifi.artifact_adapters"

# Formats identified only so that they can be refused: decoding them would mean executing their content.
REFUSED_FORMATS = {
    "pickle": "Python pickle data can run code when it is loaded, so it is never loaded",
}


class Adapter:
    """Base class for adapters. Subclasses set `name`, `formats`, `families`, `needs` and implement `decode`.

    `needs` lists optional modules the adapter imports lazily; `available()` checks them without importing them.
    `reads` says how far the adapter gets, so that the catalog never calls a header reader a decoder: "content" (the
    format's content is decoded), "content or header" (some variants decoded, the others header only), "header"
    (identified and its header read, nothing else: `HeaderOnly`), "structure" (a container's structure and tracks,
    no frame or sample), "identity" (identified, nothing read)."""

    name: str = ""
    formats: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    reads: str = "content"

    def available(self) -> tuple[bool, str]:
        missing = [m for m in self.needs if importlib.util.find_spec(m) is None]
        if missing:
            return False, "needs " + ", ".join(missing) + " (not installed)"
        return True, "ready"

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        raise NotImplementedError


_ADAPTERS: list[Adapter] = []
_EXTERNAL: dict[str, dict] = {}       # entry-point name -> {"state": ..., "reason": ...}
_GAP_HOOKS: list[Callable[[Detected, str], None]] = []
_DISCOVERED: list | None = None


def register(adapter: Adapter) -> Adapter:
    """Add an adapter. Names are unique; registering a second adapter under a taken name is refused."""
    if not isinstance(getattr(adapter, "name", None), str) or not adapter.name:
        raise ValueError("an adapter needs a name")
    if any(a.name == adapter.name for a in _ADAPTERS):
        raise ValueError(f"an adapter named {adapter.name!r} is already registered")
    for attr in ("formats", "families", "needs"):
        setattr(adapter, attr, tuple(getattr(adapter, attr, ())))
    _ADAPTERS.append(adapter)
    return adapter


def unregister(name: str) -> None:
    _ADAPTERS[:] = [a for a in _ADAPTERS if a.name != name]


def _available(adapter: Adapter) -> tuple[bool, str]:
    try:
        ok, reason = adapter.available()
        return bool(ok), str(reason)
    except Exception as error:   # an adapter's self-check must not break the registry
        return False, f"availability check failed ({type(error).__name__})"


def resolve(detected: Detected) -> Adapter | None:
    """The most recently registered adapter for `detected.format` that is available here (so a lane's specific
    adapter registered after the kernel's built-ins takes precedence); else the latest one that claims the format but
    is unavailable (so the caller can say what it needs); else None."""
    claiming = [a for a in reversed(_ADAPTERS) if detected.format in a.formats]
    for adapter in claiming:
        if _available(adapter)[0]:
            return adapter
    return claiming[0] if claiming else None


def _discover() -> list:
    """Entry points of the adapter group, from installed metadata only (no import)."""
    global _DISCOVERED
    if _DISCOVERED is None:
        try:
            from importlib.metadata import entry_points
            _DISCOVERED = list(entry_points(group=ENTRY_POINT_GROUP))
        except Exception as error:
            log.warning("artifact adapter discovery failed (%s)", type(error).__name__)
            _DISCOVERED = []
    return _DISCOVERED


def load_external(approved: Iterable[str]) -> list[str]:
    """Load the discovered external adapters whose entry-point names the owner approved (lane A passes the instance
    setting `artifact_adapters`). Returns the names loaded now. Nothing unnamed is ever imported."""
    approved = {str(n) for n in approved}
    loaded = []
    for ep in _discover():
        if ep.name not in approved:
            _EXTERNAL.setdefault(ep.name, {"state": "found", "reason": "found, not approved"})
            continue
        if _EXTERNAL.get(ep.name, {}).get("state") == "loaded":
            continue
        try:
            obj = ep.load()
            adapter = obj() if isinstance(obj, type) else obj
            if not isinstance(adapter, Adapter):
                raise TypeError("not an Adapter")
            register(adapter)
            _EXTERNAL[ep.name] = {"state": "loaded", "reason": "approved by the owner", "adapter": adapter.name}
            loaded.append(ep.name)
        except Exception as error:
            _EXTERNAL[ep.name] = {"state": "failed", "reason": f"approved, failed to load ({type(error).__name__})"}
    return loaded


def unload_external(approved: Iterable[str]) -> list[str]:
    """Unregister every loaded external adapter whose entry-point name is not in `approved` (the owner withdrew it);
    it is listed again as "found, not approved". Returns the entry-point names unloaded now."""
    approved = {str(n) for n in approved}
    unloaded = []
    for ep_name, state in list(_EXTERNAL.items()):
        if state.get("state") == "loaded" and ep_name not in approved:
            unregister(state.get("adapter", ""))
            _EXTERNAL[ep_name] = {"state": "found", "reason": "found, not approved"}
            unloaded.append(ep_name)
    return unloaded


def loaded_external() -> tuple[str, ...]:
    """The entry-point names of the external adapters loaded now (the guarded child loads the same ones)."""
    return tuple(sorted(n for n, v in _EXTERNAL.items() if v.get("state") == "loaded"))


def catalog() -> list[dict]:
    """The live capability list: one row per adapter (name, formats, families, available, reason, source) and one
    per discovered-but-unloaded external adapter. Nothing else may be quoted as "supported"."""
    external_names = {v.get("adapter") for v in _EXTERNAL.values() if v.get("state") == "loaded"}
    rows = []
    for adapter in _ADAPTERS:
        ok, reason = _available(adapter)
        rows.append({"name": adapter.name, "formats": list(adapter.formats), "families": list(adapter.families),
                     "available": ok, "reason": reason, "reads": str(getattr(adapter, "reads", "content")),
                     "source": "external" if adapter.name in external_names else "builtin"})
    for ep in _discover():
        state = _EXTERNAL.get(ep.name, {"state": "found", "reason": "found, not approved"})
        if state["state"] != "loaded":
            rows.append({"name": ep.name, "formats": [], "families": [], "available": False,
                         "reason": state["reason"], "reads": "nothing", "source": "external"})
    for format_name, why in REFUSED_FORMATS.items():
        rows.append({"name": f"refuse-{format_name}", "formats": [format_name], "families": [], "available": False,
                     "reason": "refused: " + why, "reads": "nothing", "source": "builtin"})
    return rows


def on_gap(callback: Callable[[Detected, str], None]) -> Callable[[], None]:
    """Register `callback(detected, outcome)`; returns a function that removes it. A failing hook is logged by
    type and never breaks a decode."""
    _GAP_HOOKS.append(callback)

    def remove():
        if callback in _GAP_HOOKS:
            _GAP_HOOKS.remove(callback)
    return remove


def _gap(detected: Detected, outcome: str) -> None:
    for hook in list(_GAP_HOOKS):
        try:
            hook(detected, outcome)
        except Exception as error:
            log.warning("artifact gap hook failed (%s)", type(error).__name__)


def nested(limits: Limits, used_bytes: int = 0, elapsed_s: float = 0.0) -> Limits:
    """Limits for a decode nested one container deeper, after `used_bytes` were materialised and `elapsed_s` seconds
    spent at this level: the inner decode gets one level less, and only what is left of the bytes and the time."""
    return replace(limits, max_depth=limits.max_depth - 1,
                   max_decoded_bytes=max(0, limits.max_decoded_bytes - int(used_bytes)),
                   max_seconds=max(0.0, limits.max_seconds - float(elapsed_s)))


def inspect_unknown(data: bytes, detected: Detected | None = None) -> Opaque:
    """What can be established about bytes nothing here decodes (DESIGN §3): findings are measurements, candidates
    are only "consistent with", needs name what would have to exist."""
    from ..unknown import inspect_unknown as _inspect_unknown
    return _inspect_unknown(data, detected)


# Formats that are ZIP archives by design (their adapters read the ZIP), and containers that may hold a ZIP as a
# member: a ZIP directory in their bytes is expected, not a second reading.
ZIP_BASED = frozenset({"zip", "jar", "apk", "npz", "kmz", "docx", "xlsx", "xlsm", "pptx", "odt", "ods", "odp",
                       "epub"})


def _also_a_zip(data: bytes, detected: Detected) -> bool:
    """Whether bytes identified as something else are ALSO a ZIP archive: an end-of-central-directory record whose
    directory lists at least one member (zipfile reads a ZIP with other data before it). Such a polyglot has two
    readings and nothing here can establish which one is meant, so it is refused instead of read one way."""
    if detected.format in ZIP_BASED or detected.container == "zip" or detected.family in ("archive", "unknown"):
        return False
    if data.rfind(b"PK\x05\x06", max(0, len(data) - (1 << 16) - 22)) < 0:
        return False
    import io
    import zipfile
    from ..safe import zip_declared_entries
    declared = zip_declared_entries(data)
    if not declared:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return len(z.infolist()) > 0
    except Exception:       # not a readable ZIP directory: there is only one reading
        return False


# Made once, at import, for when memory has run out so far that even a new refusal cannot be made.
_RESOURCES = Refused("decoding needed more memory or depth than is allowed", rule="resources")


def _resources() -> Refused:
    try:
        return Refused("decoding needed more memory or depth than is allowed", rule="resources")
    except MemoryError:
        _RESOURCES.__traceback__ = None
        return _RESOURCES


def decode(data: bytes, *, name: str | None = None, declared: str | None = None, limits: Limits = Limits()) -> Decoded:
    """The whole chain. Returns a `Decoded` (with an `Opaque` representation when nothing here can decode the
    bytes); raises an `ArtifactError` when a rule or limit refuses the input or an adapter finds it malformed."""
    from ..detect import detect
    data = bytes(data)
    if limits.max_depth < 0:
        raise Refused("containers are nested deeper than allowed", rule="max_depth")
    if len(data) > limits.max_bytes:
        raise Refused(f"input of {len(data)} bytes is over the limit of {limits.max_bytes}", rule="max_bytes",
                      reason="too_large")
    detected = detect(data[:64 << 10], name=name, declared=declared, size=len(data))
    if detected.format in REFUSED_FORMATS:
        raise Refused(REFUSED_FORMATS[detected.format], rule=detected.format)
    if _also_a_zip(data, detected):
        raise Refused(f"the bytes are a {detected.format} file and also a ZIP archive (a polyglot): which reading is "
                      "meant cannot be established, so neither is read", rule="polyglot")
    adapter = resolve(detected)
    if adapter is None:
        _gap(detected, "opaque")
        return Decoded(detected, inspect_unknown(data, detected), ("no adapter here decodes this format",), False)
    ok, reason = _available(adapter)
    if not ok:
        _gap(detected, "opaque")
        opaque = inspect_unknown(data, detected)
        opaque = replace(opaque, needs=tuple(dict.fromkeys(opaque.needs + tuple(adapter.needs))))
        return Decoded(detected, opaque, (f"adapter {adapter.name} is not available: {reason}",), False)
    try:
        try:
            result = adapter.decode(data, detected, limits)
        except Unsupported:
            _gap(detected, "unsupported")
            raise
        except ArtifactError:
            raise
        except (MemoryError, RecursionError):
            raise _resources() from None
        except Exception as error:
            # plain words for the owner; the exception's type only as a detail for a log (ruling C of fix lane F1)
            failure = Malformed(f"the {adapter.name} adapter could not read these bytes")
            failure.detail = type(error).__name__
            raise failure from None
    except MemoryError:
        # memory ran out while an error was being handled or named: the prepared refusal needs no allocation
        raise _resources() from None
    if not isinstance(result, Decoded):
        raise Malformed(f"the {adapter.name} adapter returned {type(result).__name__}, not Decoded")
    return result


def _register_builtins() -> None:
    """The kernel's generic adapters first, then lane B1's and lane B2's specific ones (`resolve` takes the most
    recently registered, so a specific adapter wins over a generic one: B1's notebook over K's JSON tree, B2's
    GeoJSON over K's JSON tree). B2's registration also installs its detection. Lane P's model file (ADR-076) last:
    its detection names only a JSON object marked "neuroscifi.model/1"."""
    from . import _b1, _b2, model_file
    from .compressed import CompressedStream
    from .jsontree import JsonTree
    from .plain import PlainText
    for adapter in (PlainText(), JsonTree(), CompressedStream()):
        register(adapter)
    _b1.register_all()
    _b2.register_all()
    model_file.register_all()


_register_builtins()

__all__ = ["Adapter", "ENTRY_POINT_GROUP", "REFUSED_FORMATS", "ZIP_BASED", "catalog", "decode", "inspect_unknown",
           "load_external", "nested", "on_gap", "register", "resolve", "unload_external", "unregister"]
