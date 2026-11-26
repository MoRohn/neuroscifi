"""What can be established about bytes nothing here decodes (DESIGN §3): `inspect_unknown(data, detected)`.

Findings are measurements of the bytes (size, entropy, printable share, embedded signatures at offsets, record-length
regularity by autocorrelation, runs of printable text: counts, lengths and offsets only, never the text itself).
Candidates are formats or layouts the findings are only CONSISTENT WITH, each with its reason; none is a fact.
Needs name what would have to exist for a real decode. Every measurement reads a bounded sample."""
from __future__ import annotations

import numpy as np

from .types import Detected, Opaque

SAMPLE_BYTES = 1 << 20      # entropy, printable share and text runs read at most this much
SCAN_BYTES = 4 << 20        # embedded signatures are looked for in at most this much
AUTOCORR_BYTES = 1 << 16    # record regularity is measured on at most this much
MAX_LAG = 4096
MAX_HITS = 16
TEXT_RUN = 8

# Embedded signatures worth reporting at any offset: at least 4 bytes, so chance hits are rare.
EMBEDDED = (
    (b"\x89PNG\r\n\x1a\n", "PNG image"), (b"\xff\xd8\xff\xe0", "JPEG (JFIF) image"), (b"\xff\xd8\xff\xe1", "JPEG (Exif) image"),
    (b"GIF89a", "GIF image"), (b"GIF87a", "GIF image"), (b"%PDF-", "PDF document"), (b"PK\x03\x04", "ZIP member"),
    (b"\x1f\x8b\x08", "gzip stream"), (b"\x28\xb5\x2f\xfd", "Zstandard frame"), (b"\xfd7zXZ\x00", "xz stream"),
    (b"BZh91AY&SY", "bzip2 stream"), (b"SQLite format 3\x00", "SQLite database"), (b"\x7fELF", "ELF executable"),
    (b"OggS\x00", "Ogg page"), (b"RIFF", "RIFF chunk"), (b"fLaC", "FLAC stream"), (b"\x1a\x45\xdf\xa3", "EBML header"),
    (b"\x89HDF\r\n\x1a\n", "HDF5 superblock"), (b"\x93NUMPY", "NumPy array"), (b"<?xml", "XML declaration"),
    (b"ID3\x03", "ID3 tag"), (b"ID3\x04", "ID3 tag"), (b"ftyp", "ISO BMFF 'ftyp' box"),
)


def _entropy(sample: bytes) -> float:
    if not sample:
        return 0.0
    counts = np.bincount(np.frombuffer(sample, dtype=np.uint8), minlength=256).astype(np.float64)
    p = counts[counts > 0] / len(sample)
    return float(-(p * np.log2(p)).sum())


def _printable_mask(sample: bytes) -> np.ndarray:
    arr = np.frombuffer(sample, dtype=np.uint8)
    return ((arr >= 0x20) & (arr < 0x7F)) | (arr == 0x09) | (arr == 0x0A) | (arr == 0x0D)


def _text_runs(mask: np.ndarray) -> tuple[int, int, int, list[int]]:
    """(number of runs of >= TEXT_RUN printable bytes, longest, total bytes in them, first offsets)."""
    if not mask.size:
        return 0, 0, 0, []
    padded = np.concatenate(([False], mask, [False])).astype(np.int8)
    edges = np.diff(padded)
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    lengths = ends - starts
    keep = lengths >= TEXT_RUN
    return int(keep.sum()), int(lengths[keep].max()) if keep.any() else 0, int(lengths[keep].sum()), \
        [int(s) for s in starts[keep][:5]]


def _embedded(data: bytes) -> list[tuple[int, str]]:
    hits = []
    view = data[:SCAN_BYTES]
    for magic, what in EMBEDDED:
        at = view.find(magic)
        found = 0
        while at >= 0 and found < 4:
            hits.append((at, what))
            found += 1
            at = view.find(magic, at + 1)
    hits.sort()
    return hits[:MAX_HITS]


def _regularity(sample: bytes) -> tuple[int, float] | None:
    """(lag, r): the lag in [2, MAX_LAG] with the highest normalised autocorrelation of the byte values, when the
    signal has any variance and the best r is at least 0.5; else None."""
    x = np.frombuffer(sample[:AUTOCORR_BYTES], dtype=np.uint8).astype(np.float64)
    if len(x) < 64:
        return None
    x = x - x.mean()
    var = float((x * x).sum())
    if var <= 0:
        return None
    n = 1 << int(np.ceil(np.log2(2 * len(x))))
    f = np.fft.rfft(x, n)
    ac = np.fft.irfft(f * np.conj(f), n)[:min(MAX_LAG, len(x) // 4) + 1] / var
    if len(ac) <= 2:
        return None
    lag = int(np.argmax(ac[2:]) + 2)
    r = float(ac[lag])
    # report the shortest lag whose correlation is within 0.05 of the best (a period's multiples score as well)
    for shorter in range(2, lag):
        if ac[shorter] >= r - 0.05 and lag % shorter == 0:
            lag, r = shorter, float(ac[shorter])
            break
    return (lag, r) if r >= 0.5 else None


def _float_layout(sample: bytes) -> str | None:
    for dtype, label in (("<f4", "little-endian float32"), ("<f8", "little-endian float64"),
                         (">f4", "big-endian float32"), (">f8", "big-endian float64")):
        size = np.dtype(dtype).itemsize
        n = (min(len(sample), 1 << 16) // size) * size
        if n < 64 * size:
            continue
        values = np.frombuffer(sample[:n], dtype=dtype)
        with np.errstate(all="ignore"):
            mag = np.abs(values)
            plausible = np.isfinite(values) & ((mag == 0) | ((mag > 1e-12) & (mag < 1e12)))
        if plausible.mean() >= 0.99 and np.count_nonzero(values) >= len(values) // 4:
            return label
    return None


def inspect_unknown(data: bytes, detected: Detected | None = None) -> Opaque:
    data = bytes(data)
    sample = data[:SAMPLE_BYTES]
    entropy = _entropy(sample)
    mask = _printable_mask(sample)
    printable = float(mask.mean()) if mask.size else 0.0
    runs, longest, run_bytes, run_offsets = _text_runs(mask)
    zero_share = float(np.count_nonzero(np.frombuffer(sample, dtype=np.uint8) == 0) / len(sample)) if sample else 0.0
    findings = [f"size: {len(data)} bytes",
                f"entropy: {entropy:.3f} bits per byte over the first {len(sample)} bytes",
                f"printable ASCII share: {printable:.3f}; zero-byte share: {zero_share:.3f}",
                f"{runs} run(s) of {TEXT_RUN} or more printable bytes (longest {longest}, {run_bytes} bytes in all)"
                + (f", first at offset(s) {', '.join(map(str, run_offsets))}" if run_offsets else "")]
    candidates: list[str] = []
    needs: list[str] = []
    hits = _embedded(data)
    for at, what in hits:
        findings.append(f"the byte pattern of a {what} at offset {at}")
    regular = _regularity(sample)
    if regular is not None:
        findings.append(f"byte values repeat with a period of {regular[0]} bytes (autocorrelation {regular[1]:.2f})")

    if detected is not None and detected.format != "unknown":
        findings.append(f"identified as {detected.format} ({detected.basis}); nothing here decodes it")
        needs.append(f"an adapter for {detected.format}")
        return Opaque(len(data), tuple(findings), tuple(candidates), tuple(needs))

    if len(sample) >= 256 and entropy > 7.5:
        candidates.append("compressed or encrypted data (consistent with: entropy above 7.5 bits per byte and no "
                          "recognised header)")
    if sample and printable > 0.95:
        candidates.append("text in a layout not recognised here (consistent with: almost every byte is printable)")
    elif sample and printable > 0.7 and any(b >= 0x80 for b in sample[:4096]):
        candidates.append("text in a single-byte legacy encoding such as Latin-1 or Windows-1252 (consistent with: "
                          "mostly printable bytes, high bytes that are not valid UTF-8)")
    if regular is not None and entropy < 7.5:
        candidates.append(f"a sequence of fixed-length binary records of {regular[0]} bytes (consistent with: the "
                          "periodic autocorrelation)")
    layout = _float_layout(sample) if entropy < 7.9 else None
    if layout:
        candidates.append(f"a raw array of {layout} values (consistent with: at least 99% of the sample reads as "
                          "finite numbers of moderate size)")
    for at, what in hits:
        if at > 0:
            candidates.append(f"a container or concatenation that holds a {what} at offset {at} (consistent with: "
                              "its byte pattern there)")
            break
    if zero_share > 0.5 and entropy < 3:
        candidates.append("sparse binary data or a preallocated file (consistent with: more than half the bytes "
                          "are zero)")
    needs.append("an adapter for a format these bytes match (none was identified)")
    if candidates:
        needs.append("for a raw layout: the record or array description (field types, byte order, shape), which "
                     "the bytes do not state")
    return Opaque(len(data), tuple(findings), tuple(candidates), tuple(needs))


__all__ = ["SAMPLE_BYTES", "inspect_unknown"]
