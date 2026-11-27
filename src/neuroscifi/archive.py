"""NeuroSciFi lossless block archives and consistent SQLite checkpoints."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
import hashlib
import io
import lzma
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import zlib

import numpy as np
import zstandard as zstd
from .vendor_tufazip.arithmetic_coder import ArithmeticDecoder, ArithmeticEncoder, BitInputStream, BitOutputStream

MAGIC = b"NSFA\x01"
BLOCK = 65536
FRAME = struct.Struct(">BII32s32s")
END = 255
METHODS = ("raw", "deflate", "zstd", "xz", "delta16-zstd", "adaptive", "delta16-adaptive")
FAST = ("raw", "zstd")
COLD = ("raw", "zstd", "xz", "delta16-zstd")
TRIAL = COLD + ("adaptive", "delta16-adaptive")


class Distribution:
    """Integer cumulative counts with an implicit one-count prior on every byte."""
    def __init__(self):
        self.tree = [0] * 257
        self.total = 256

    def __len__(self):
        return 256

    def __getitem__(self, symbol):
        if symbol == -1:
            return self.total
        index = symbol + 1
        count = index
        while index:
            count += self.tree[index]
            index -= index & -index
        return count

    def learn(self, symbol):
        self.total += 1
        index = symbol + 1
        while index <= 256:
            self.tree[index] += 1
            index += index & -index


def adaptive(source, size=None):
    """Encoder and decoder update identical context counts after every recovered byte."""
    stream = io.BytesIO()
    encoding = size is None
    if encoding:
        bits = BitOutputStream(stream)
        coder = ArithmeticEncoder(32, bits)
    else:
        coder = ArithmeticDecoder(32, BitInputStream(io.BytesIO(source)))
    contexts = [Distribution() for _ in range(17)]
    previous = 16
    output = bytearray()
    for i in range(len(source) if encoding else size):
        model = contexts[previous]
        if encoding:
            symbol = source[i]
            coder.write(model, symbol)
        else:
            symbol = coder.read(model)
            output.append(symbol)
        model.learn(symbol)
        previous = symbol >> 4
    if not encoding:
        return bytes(output)
    coder.finish()
    while bits.numbitsfilled:
        bits.write(0)
    return stream.getvalue()


def delta16(data, reverse=False):
    """Modulo-65536 differences and byte planes. Preserve an odd trailing byte."""
    pairs = len(data) // 2
    if not pairs:
        return data
    tail = data[2*pairs:]
    if reverse:
        lanes = np.frombuffer(data[:2*pairs], dtype=np.uint8)
        diffs = lanes[:pairs].astype(np.uint16) | (lanes[pairs:].astype(np.uint16) << 8)
        return np.cumsum(diffs, dtype=np.uint16).astype("<u2").tobytes() + tail
    values = np.frombuffer(data[:2*pairs], dtype="<u2")
    diffs = np.empty_like(values)
    diffs[0] = values[0]
    diffs[1:] = values[1:] - values[:-1]
    return diffs.astype(np.uint8).tobytes() + (diffs >> 8).astype(np.uint8).tobytes() + tail


def encode(data, method):
    if method == "raw":
        return data
    if method == "deflate":
        return zlib.compress(data, 6)
    if method == "xz":
        return lzma.compress(data, preset=3)
    if method.startswith("delta16-"):
        data = delta16(data)
    if method.endswith("zstd"):
        return zstd.ZstdCompressor(level=3).compress(data)
    if method.endswith("adaptive"):
        return adaptive(data)
    raise ValueError("Unsupported encoding")


def decode(data, method, size):
    if method == "raw":
        raw = data
    elif method == "deflate":
        d = zlib.decompressobj()
        raw = d.decompress(data, size+1)
        if not d.eof or d.unused_data:
            raise ValueError("Invalid deflate frame")
    elif method == "xz":
        d = lzma.LZMADecompressor(memlimit=64<<20)
        raw = d.decompress(data, max_length=size+1)
        if not d.eof or d.unused_data:
            raise ValueError("Invalid XZ frame")
    elif method.endswith("zstd"):
        if zstd.frame_content_size(data) != size:
            raise ValueError("Zstandard frame size mismatch")
        raw = zstd.ZstdDecompressor().decompress(data, max_output_size=size, allow_extra_data=False)
    elif method.endswith("adaptive"):
        raw = adaptive(data, size)
    else:
        raise ValueError("Unsupported encoding")
    if len(raw) != size:
        raise ValueError("Wrong reconstructed length")
    return delta16(raw, reverse=True) if method.startswith("delta16-") else raw


def pack(source, dest, methods=FAST):
    dest.write(MAGIC)
    digest = hashlib.sha256()
    counts = Counter()
    while chunk := source.read(BLOCK):
        digest.update(chunk)
        candidates = ((name, encode(chunk, name)) for name in methods)
        method, encoded = min(candidates, key=lambda candidate:len(candidate[1]))
        counts[method] += 1
        dest.write(FRAME.pack(METHODS.index(method), len(chunk), len(encoded),
                              hashlib.sha256(encoded).digest(), hashlib.sha256(chunk).digest()))
        dest.write(encoded)
    dest.write(bytes([END]) + digest.digest())
    return dict(counts)


def read_exact(source, count):
    data = source.read(count)
    if len(data) != count:
        raise ValueError("Incomplete archive")
    return data


# The most `unpack` writes (fix lane F3, item 5; lane S's open item): NSFA blocks stream, so a small archive of
# highly compressible blocks could otherwise fill the disk. An instance or checkpoint larger than this is not one this
# software makes; `convert` publishes nothing when the cap stops it.
MAX_UNPACK_BYTES = 16 << 30


class UnpackTooLarge(ValueError):
    """The archive would unpack to more than the stated cap; nothing is published."""


def unpack(source, dest, max_bytes=None):
    if read_exact(source, len(MAGIC)) != MAGIC:
        raise ValueError("Unknown archive format")
    digest = hashlib.sha256()
    written, max_bytes = 0, MAX_UNPACK_BYTES if max_bytes is None else max_bytes
    while True:
        tag = read_exact(source, 1)[0]
        if tag == END:
            if read_exact(source, 32) != digest.digest() or source.read(1):
                raise ValueError("Invalid archive footer")
            return
        if tag >= len(METHODS):
            raise ValueError("Unknown codec")
        _, size, length, packed_hash, raw_hash = FRAME.unpack(bytes([tag])+read_exact(source, FRAME.size-1))
        if not 0 < size <= BLOCK or not 0 < length <= 2*BLOCK+1024:
            raise ValueError("Invalid block length")
        if written + size > max_bytes:
            raise UnpackTooLarge(f"the archive would unpack to more than {max_bytes >> 20} MiB, the most this "
                                 "software writes: nothing was written")
        written += size
        encoded = read_exact(source, length)
        if hashlib.sha256(encoded).digest() != packed_hash:
            raise ValueError("Damaged payload")
        raw = decode(encoded, METHODS[tag], size)
        if hashlib.sha256(raw).digest() != raw_hash:
            raise ValueError("Decoded data failed verification")
        digest.update(raw)
        dest.write(raw)


PARTIAL_PREFIX = ".nsf-partial-"
OWNER = "owner"
LEGACY_NAMES = frozenset({"snapshot.sqlite", "snapshot.sqlite-journal", "output", "decoded.sqlite", "restored.sqlite",
                          "restored.sqlite-journal", "restored.sqlite-wal", "restored.sqlite-shm"})
LEGACY_AGE = 3600.0     # an RC1 tmp* working directory older than this is an abandoned one
OWNERLESS_AGE = 60.0    # a partial directory without its owner record (killed while creating it)


def _start_time(pid):
    """The process start time (clock ticks since boot) from /proc, or None where /proc is unavailable."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
        return None


def _owner_alive(text):
    try:
        pid, started = text.split()
        pid = int(pid)
    except ValueError:
        return False
    current = _start_time(pid)
    if current is not None:
        return current == started      # same pid, same start: the same process (a reused pid is not)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return started == "-"


def sweep_partials(parent, now=None) -> int:
    """Remove abandoned working directories in `parent`: ours (PARTIAL_PREFIX) whose owner process is gone, and
    RC1-style tmp* directories that hold nothing but a snapshot/output and are over LEGACY_AGE old. They hold a
    full plaintext copy of the instance (T5-03: a kill during a checkpoint left one behind for good, so forgotten
    data survived). Returns how many were removed."""
    import time
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(Path(parent).iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            age = now - entry.lstat().st_mtime
            if entry.name.startswith(PARTIAL_PREFIX):
                try:
                    owner = (entry / OWNER).read_text()
                except OSError:
                    owner = None
                if (owner is None and age < OWNERLESS_AGE) or (owner is not None and _owner_alive(owner)):
                    continue
            elif entry.name.startswith("tmp"):
                names = {child.name for child in entry.iterdir()}
                if not names or not names <= LEGACY_NAMES or age < LEGACY_AGE:
                    continue
            else:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += not entry.exists()
    return removed


@contextmanager
def partial_dir(parent):
    """A private working directory in `parent` for one conversion, removed however the conversion ends; if the
    process is killed, the next sweep_partials of `parent` (every conversion, prune, console start) removes it."""
    sweep_partials(parent)
    directory = Path(tempfile.mkdtemp(prefix=PARTIAL_PREFIX, dir=parent))
    try:
        (directory / OWNER).write_text(f"{os.getpid()} {_start_time(os.getpid()) or '-'}")
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def convert(command, source, destination, methods=FAST, max_unpack_bytes=None):
    """Never replace an existing file. Publish only complete outputs."""
    if destination.exists():
        raise FileExistsError(destination)
    with partial_dir(destination.parent) as temp:
        if command == "checkpoint":
            snapshot = temp / "snapshot.sqlite"
            uri = source.resolve().as_uri()+"?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as live, closing(sqlite3.connect(snapshot)) as copy:
                live.backup(copy)
            source = snapshot
        fd = os.open(temp/"output", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)  # private whatever the umask
        with source.open("rb") as inp, os.fdopen(fd, "wb") as out:
            if command == "unpack":
                unpack(inp, out, max_unpack_bytes)
            else:
                pack(inp, out, methods)
            out.flush()
            os.fsync(out.fileno())
        os.link(temp/"output", destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["pack","unpack","checkpoint"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--policy", choices=["fast","cold","experimental"], default="fast")
    args = parser.parse_args()
    convert(args.command, args.source, args.destination, {"fast":FAST,"cold":COLD,"experimental":TRIAL}[args.policy])
