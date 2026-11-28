"""OMNIMIND artifacts: detect, decode and represent what is handed over; specify, make and check what is produced
(DESIGN.md of the programme; ADR-072 for this kernel). Nothing here runs unless called: importing it changes no
existing behaviour. The live capability lists are `adapters.catalog()`, `make.catalog()` and `check.catalog()`."""
from .types import (FAMILIES, ArtifactError, ContentShape, Decoded, Detected, Limits, Malformed, Refused,
                    Unsupported)

__all__ = ["ArtifactError", "ContentShape", "Decoded", "Detected", "FAMILIES", "Limits", "Malformed", "Refused",
           "Unsupported"]
