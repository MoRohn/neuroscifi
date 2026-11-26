"""What can be established about bytes nothing here decodes (DESIGN §3): `inspect_unknown(data, detected)`.

Findings are measurements; candidates are formats the findings are only consistent with, each with its reason;
needs name what would have to exist for a real decode. Nothing here names a format as a fact."""
from __future__ import annotations

import numpy as np

from .types import Detected, Opaque

SAMPLE_BYTES = 1 << 20     # measurements read at most this much of the input


def _entropy(sample: bytes) -> float:
    if not sample:
        return 0.0
    counts = np.bincount(np.frombuffer(sample, dtype=np.uint8), minlength=256).astype(np.float64)
    p = counts[counts > 0] / len(sample)
    return float(-(p * np.log2(p)).sum())


def _printable_share(sample: bytes) -> float:
    if not sample:
        return 0.0
    arr = np.frombuffer(sample, dtype=np.uint8)
    printable = ((arr >= 0x20) & (arr < 0x7F)) | (arr == 0x09) | (arr == 0x0A) | (arr == 0x0D)
    return float(printable.mean())


def inspect_unknown(data: bytes, detected: Detected | None = None) -> Opaque:
    data = bytes(data)
    sample = data[:SAMPLE_BYTES]
    entropy = _entropy(sample)
    printable = _printable_share(sample)
    findings = [f"size: {len(data)} bytes",
                f"entropy: {entropy:.3f} bits per byte over the first {len(sample)} bytes",
                f"printable ASCII share: {printable:.3f}"]
    candidates, needs = [], []
    if detected is not None and detected.format != "unknown":
        findings.append(f"identified as {detected.format} ({detected.basis}); nothing here decodes it")
        needs.append(f"an adapter for {detected.format}")
    else:
        if len(sample) >= 64 and entropy > 7.5:
            candidates.append("compressed or encrypted data (consistent with: entropy above 7.5 bits per byte)")
        if len(sample) and printable > 0.95:
            candidates.append("text in an unrecognised layout (consistent with: almost all bytes are printable)")
        needs.append("an adapter for a format these bytes match (none identified)")
    return Opaque(len(data), tuple(findings), tuple(candidates), tuple(needs))


__all__ = ["SAMPLE_BYTES", "inspect_unknown"]
