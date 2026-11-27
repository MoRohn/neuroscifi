"""Files named on the command line, read as untrusted input.

A path is opened only if it is a plain file (a FIFO, a device or a folder is refused without blocking on it; a link
to a plain file is followed, as before). A whole-file read is bounded by the size the format can have. A NumPy file
is checked before NumPy allocates anything: the `.npy` header (version, length, dtype, shape) and, for an `.npz`,
the zip directory (its size, the number of members, each member's compression method, declared size and compression
ratio) are read from a bounded prefix with the standard library and numpy.lib.format, and the declared sizes are
compared with the bytes really there. Every refusal is InputRefused, a ValueError with a plain reason.
"""
from __future__ import annotations

import errno
import io
import math
import os
import stat
import struct
import zipfile

from numpy.lib import format as npformat


class InputRefused(ValueError):
    """An input file that is not a plain file, is larger than its format allows, or declares more than may be read."""


NPY_MAX_HEADER = 10_000          # numpy's own default limit on the header text (numpy.lib.format)
NPY_KINDS = "biufc"              # booleans, integers, floats, complex: what np.asarray(..., float64) can hold
NPY_MAX_ITEMSIZE = 16            # complex128 / longdouble
ZIP_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)     # what np.savez and np.savez_compressed write
ZIP_MAX_DIRECTORY = 64 << 10     # bytes of central directory: room for far more members than any accepted file has
ZIP_MAX_MEMBERS = 64
_EOCD = struct.Struct("<4s4H2LH")    # zipfile.structEndArchive
_EOCD_SIG, _ZIP64_LOCATOR_SIG = b"PK\x05\x06", b"PK\x06\x07"


def open_regular(path):
    """The file at `path` opened for binary reading, if it is a plain file; else InputRefused (a folder: the
    IsADirectoryError reading one always gave). The open itself never blocks (O_NONBLOCK, cleared once the file is
    known to be plain), so a FIFO is refused instead of waited on."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):           # as reading a folder always failed: callers report it as before
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(path))
        if not stat.S_ISREG(mode):
            what = "a FIFO" if stat.S_ISFIFO(mode) else "a device" if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) \
                else "a socket" if stat.S_ISSOCK(mode) else "not a plain file"
            raise InputRefused(f"{path} is {what}, not a plain file")
        os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def size_of(fp) -> int:
    return os.fstat(fp.fileno()).st_size


def read_bounded(path, limit: int, what: str = "file") -> bytes:
    """The bytes of a plain file of at most `limit` bytes (InputRefused for a larger one, before it is read)."""
    with open_regular(path) as fp:
        size = size_of(fp)
        if size > limit:
            raise InputRefused(f"{path} holds {size} bytes, more than a {what} can ({limit})")
        data = fp.read(limit + 1)
    if len(data) > limit:                      # grew while it was read
        raise InputRefused(f"{path} holds more than a {what} can ({limit} bytes)")
    return data


def _exact(fp, count, what):
    data = fp.read(count)
    if len(data) != count:
        raise InputRefused(f"truncated .npy {what}")
    return data


def npy_header(fp):
    """(shape, dtype, bytes of header) of the .npy stream at fp's position, read from at most NPY_MAX_HEADER + 12
    bytes; InputRefused for anything numpy.lib.format would not accept, an object dtype, or a header too long."""
    magic = _exact(fp, 8, "magic")
    if magic[:6] != npformat.MAGIC_PREFIX:
        raise InputRefused("not a .npy array (no NumPy magic)")
    version = (magic[6], magic[7])
    if version not in ((1, 0), (2, 0)):
        raise InputRefused(f"unsupported .npy version {version[0]}.{version[1]}")
    width = 2 if version == (1, 0) else 4
    raw = _exact(fp, width, "header length")
    length = int.from_bytes(raw, "little")
    if length > NPY_MAX_HEADER:
        raise InputRefused(f".npy header of {length} bytes is longer than {NPY_MAX_HEADER}")
    text = _exact(fp, length, "header")
    reader = npformat.read_array_header_1_0 if version == (1, 0) else npformat.read_array_header_2_0
    try:
        shape, _, dtype = reader(io.BytesIO(raw + text), max_header_size=NPY_MAX_HEADER)
    except (ValueError, TypeError, SyntaxError) as error:
        raise InputRefused(f"unreadable .npy header: {str(error)[:200]}") from None
    if dtype.hasobject or dtype.kind not in NPY_KINDS or dtype.itemsize > NPY_MAX_ITEMSIZE or dtype.subdtype:
        raise InputRefused(f".npy array of dtype {str(dtype)[:60]} is not numeric")
    return shape, dtype, 8 + width + length


def check_npy(fp, size, *, max_elements):
    """Check the .npy file `fp` (its `size` bytes) before np.load: a numeric dtype, at most `max_elements` elements,
    and the data its header declares really there. Returns (shape, dtype); fp is back at its start."""
    shape, dtype, header = npy_header(fp)
    elements = math.prod(shape)
    if elements > max_elements:
        raise InputRefused(f".npy array of shape {tuple(shape)} has more than {max_elements} elements")
    if header + elements * dtype.itemsize > size:
        raise InputRefused(f".npy header declares {elements * dtype.itemsize} bytes of data; the file holds "
                           f"{max(0, size - header)}")
    fp.seek(0)
    return shape, dtype


def check_npz(fp, size, *, max_elements, max_member_bytes):
    """Check the .npz archive `fp` (its `size` bytes) before np.load reads it: a central directory of at most
    ZIP_MAX_DIRECTORY bytes and ZIP_MAX_MEMBERS members and no ZIP64 end record (np.savez writes none below 4 GiB);
    every member stored or deflated, its stored size within the file, its declared size at most `max_member_bytes`
    (no ratio rule: zipfile never inflates past the declared size, which this bound already caps, so a ratio refused
    only honest arrays of constant data, as `np.savez_compressed` of zeros: code review L11, fix lane F1); every
    .npy member's header as check_npy
    with `max_elements`, its declared data within the member's declared size. zipfile stops a member's
    decompression at the declared size, so nothing larger is ever decompressed. Returns {name: (shape, dtype)};
    fp is back at its start."""
    tail_size = min(size, _EOCD.size + 0xFFFF)
    fp.seek(size - tail_size)
    tail = fp.read(tail_size)
    at = tail.rfind(_EOCD_SIG)
    if at < 0 or at + _EOCD.size > len(tail):
        raise InputRefused("not a zip archive (no end of central directory)")
    _, _, _, _, members, directory, _, _ = _EOCD.unpack(tail[at:at + _EOCD.size])
    if at >= 20 and tail[at - 20:at - 16] == _ZIP64_LOCATOR_SIG or members == 0xFFFF or directory == 0xFFFFFFFF:
        raise InputRefused("zip archive with a ZIP64 directory (larger than any accepted .npz)")
    if members > ZIP_MAX_MEMBERS or directory > ZIP_MAX_DIRECTORY:
        raise InputRefused(f"zip archive declares {members} members in a {directory}-byte directory; at most "
                           f"{ZIP_MAX_MEMBERS} members in {ZIP_MAX_DIRECTORY} bytes are read")
    fp.seek(0)
    try:
        with zipfile.ZipFile(fp) as archive:
            infos = archive.infolist()
            if len(infos) > ZIP_MAX_MEMBERS:
                raise InputRefused(f"zip archive holds more than {ZIP_MAX_MEMBERS} members")
            found = {}
            for info in infos:
                name = info.filename[:80]
                if info.compress_type not in ZIP_METHODS:
                    raise InputRefused(f"member {name!r} uses compression method {info.compress_type}; only stored "
                                       "and deflated members are read")
                if info.compress_size > size:
                    raise InputRefused(f"member {name!r} declares more stored bytes than the archive holds")
                if info.file_size > max_member_bytes:
                    raise InputRefused(f"member {name!r} declares {info.file_size} bytes; at most "
                                       f"{max_member_bytes} are read")
                if not info.filename.endswith(".npy"):
                    continue
                with archive.open(info) as member:
                    shape, dtype, header = npy_header(member)
                elements = math.prod(shape)
                if elements > max_elements:
                    raise InputRefused(f"array {name!r} of shape {tuple(shape)} has more than {max_elements} "
                                       "elements")
                if header + elements * dtype.itemsize > info.file_size:
                    raise InputRefused(f"array {name!r} declares more data than its member holds")
                found[info.filename] = (shape, dtype)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, NotImplementedError,
            RuntimeError) as error:
        raise InputRefused(f"unreadable zip archive: {str(error)[:200]}") from None
    fp.seek(0)
    return found


__all__ = ["InputRefused", "check_npy", "check_npz", "npy_header", "open_regular", "read_bounded", "size_of"]
