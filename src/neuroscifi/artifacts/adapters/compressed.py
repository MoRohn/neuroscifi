"""gzip / zstd / bzip2 / xz single streams -> Bundle of one member (lane K's pattern for containers).

The stream is inflated by `safe.decompress` in bounded chunks (max_ratio per stream, with a 1 MiB floor, and
max_decoded_bytes, both enforced before the bytes are held), then the inflated bytes are decoded again through
`adapters.decode` with `nested(limits, used)`: one container level less and only what is left of the byte and
time budgets. A tar inside a gzip is reported as tar; nothing is extracted to disk and no member name is followed.
"""
from __future__ import annotations

from ..safe import Budget, Deadline, decompress
from ..types import Bundle, Decoded, Detected, Limits, Member
from . import Adapter

CODECS = {"gzip": "gzip", "zstd": "zstd", "bzip2": "bzip2", "xz": "xz"}


class CompressedStream(Adapter):
    name = "compressed-stream"
    formats = tuple(CODECS)
    families = ("archive",)

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        from . import decode as decode_any, nested
        deadline = Deadline(limits.max_seconds)
        budget = Budget(limits.max_decoded_bytes, "max_decoded_bytes")
        inner_bytes, notes = decompress(data, CODECS[detected.format], limits, deadline=deadline, budget=budget)
        inner = decode_any(inner_bytes, limits=nested(limits, len(inner_bytes), deadline.elapsed()))
        member = Member(path="", size=len(inner_bytes), detected=inner.detected, inner=inner.representation,
                        note="the decompressed stream" + (": " + "; ".join(inner.notes) if inner.notes else ""))
        notes = [f"{detected.format} stream of {len(data)} bytes decompressed to {len(inner_bytes)} bytes", *notes]
        return Decoded(detected, Bundle((member,)), tuple(notes), inner.partial)


__all__ = ["CODECS", "CompressedStream"]
