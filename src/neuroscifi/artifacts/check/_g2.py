"""Lane G2's validators, registered by media type in one place. Importing this module registers them once.
Nothing here imports from `make`: every validator re-reads the bytes with its own reader."""
from __future__ import annotations

from . import register
from .wav import check_wav

VALIDATORS = (
    ("audio/wav", check_wav, "g2.wav"),
    ("audio/x-wav", check_wav, "g2.wav"),
)

for _media, _validator, _name in VALIDATORS:
    register(_media, _validator, name=_name)

__all__ = ["VALIDATORS"]
