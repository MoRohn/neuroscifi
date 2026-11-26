"""Registration of lane B1's adapters (documents, tables, trees, code, notebooks, archives, databases).

`register_all()` registers every B1 adapter once; the main session calls it from the registry's built-in list at
merge. Modules are imported here, not at package import, so nothing runs unless the registry asks for it.
"""
from __future__ import annotations

MODULES = ("tables", "sheets", "documents", "office", "pdf", "trees", "code", "notebooks", "archives", "sqlite_db")


def adapter_classes() -> list[type]:
    import importlib
    classes = []
    for module in MODULES:
        try:
            mod = importlib.import_module(f"{__package__}.{module}")
        except ModuleNotFoundError as error:
            if error.name and error.name.endswith(f".{module}"):
                continue                     # a module of this lane not written yet
            raise
        classes.extend(mod.ADAPTERS)
    return classes


def register_all() -> list[str]:
    """Register every B1 adapter not already registered; returns the names registered now."""
    from . import _ADAPTERS, register
    taken = {a.name for a in _ADAPTERS}
    done = []
    for cls in adapter_classes():
        if cls.name not in taken:
            register(cls())
            done.append(cls.name)
    return done


__all__ = ["MODULES", "adapter_classes", "register_all"]
