"""Lane G2's validators, registered by media type in one place. Importing this module registers them once.
Nothing here imports from `make`: every validator re-reads the bytes with its own reader."""
from __future__ import annotations

from . import register
from .explorable import check_explorable
from .meshes import check_mesh
from .png import check_png
from .wav import check_wav

VALIDATORS = (
    ("audio/wav", check_wav, "g2.wav"),
    ("audio/x-wav", check_wav, "g2.wav"),
    ("model/stl", check_mesh, "g2.mesh"),
    ("model/obj", check_mesh, "g2.mesh"),
    ("image/png", check_png, "g2.png"),
    ("image/apng", check_png, "g2.png"),
    # A vendor type, not text/html: lane G1's documents are text/html and are judged by its own validator; an
    # explorable document carries a script (the fixed runtime) that a plain document must never hold.
    ("application/vnd.neuroscifi.explorable+html", check_explorable, "g2.explorable"),
)

for _media, _validator, _name in VALIDATORS:
    register(_media, _validator, name=_name)

__all__ = ["VALIDATORS"]
