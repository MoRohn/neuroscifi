"""Versioned inference graph snapshot. The mutable training checkpoint is separate."""
import argparse
import hashlib
from pathlib import Path
import struct
import numpy as np

HEADER = struct.Struct(">4sBHHH")
MAGIC = b"NSG1"
MAX_ELEMENTS = 16_777_216


def encode(matrix, degree, bits):
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim != 2 or matrix.size == 0 or matrix.size > MAX_ELEMENTS or not np.isfinite(matrix).all():
        raise ValueError("Expected a bounded, finite, nonempty 2-D graph")
    rows, cols = matrix.shape
    if max(rows, cols) > 65535:
        raise ValueError("Graph block exceeds the index format")
    if bits == 32:
        degree = cols
        payload = matrix.astype("<f4").tobytes()
    else:
        if bits not in (4,8) or not 1 <= degree <= cols:
            raise ValueError("Choose 4 or 8 bits and a valid degree")
        ranked = np.argsort(-np.abs(matrix), axis=1, kind="stable")[:,:degree]
        indices = np.sort(ranked, axis=1)
        retained = np.take_along_axis(matrix, indices, axis=1)
        limit = 2**(bits-1)-1
        # Store max, rather than max/limit, to preserve subnormal representability.
        peaks = np.max(np.abs(retained), axis=1).astype("<f4")
        denom = np.where(peaks > 0, peaks, 1)[:,None]
        q = np.rint(retained/denom*limit).astype(np.int8).ravel()
        if bits == 4:
            nib = q.astype(np.uint8) & 15
            if nib.size % 2:
                nib = np.append(nib, np.uint8(0))
            packed = (nib[0::2] | nib[1::2] << 4).tobytes()
        else:
            packed = q.tobytes()
        index_type = "u1" if cols <= 256 else "<u2"
        payload = peaks.tobytes() + indices.astype(index_type).tobytes() + packed
    body = HEADER.pack(MAGIC, bits, rows, cols, degree) + payload
    return body + hashlib.sha256(body).digest()


def decode(blob):
    if len(blob)<HEADER.size+32 or hashlib.sha256(blob[:-32]).digest()!=blob[-32:]:
        raise ValueError("Graph checksum failed")
    magic, bits, rows, cols, degree = HEADER.unpack(blob[:HEADER.size])
    if magic!=MAGIC or not rows or not cols or rows*cols>MAX_ELEMENTS or not 1<=degree<=cols:
        raise ValueError("Invalid graph header")
    data = memoryview(blob)[HEADER.size:-32]
    if bits==32:
        if degree!=cols or len(data)!=4*rows*cols:
            raise ValueError("Invalid dense block")
        return np.frombuffer(data, dtype="<f4").reshape(rows,cols).copy()
    if bits not in (4,8):
        raise ValueError("Unknown precision")
    dtype = np.dtype("u1" if cols<=256 else "<u2")
    count = rows*degree
    offset = 4*rows+count*dtype.itemsize
    if len(data)!=offset+(count*bits+7)//8:
        raise ValueError("Invalid sparse block length")
    peaks = np.frombuffer(data[:4*rows], dtype="<f4")
    indices = np.frombuffer(data[4*rows:offset], dtype=dtype).reshape(rows,degree)
    if np.any(indices>=cols) or np.any(indices[:,1:]<=indices[:,:-1]) or not np.isfinite(peaks).all() or np.any(peaks<0):
        raise ValueError("Invalid indices or scales")
    if bits==4:
        packed = np.frombuffer(data[offset:],dtype=np.uint8)
        nib = np.column_stack((packed & 15,packed>>4)).ravel()[:count].astype(np.int8)
        q = np.where(nib>=8,nib-16,nib)
    else:
        q = np.frombuffer(data[offset:],dtype=np.int8)
    graph = np.zeros((rows,cols),dtype=np.float32)
    graph[np.arange(rows)[:,None],indices] = q.reshape(rows,degree)/(2**(bits-1)-1)*peaks[:,None]
    return graph


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=["pack","unpack"])
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--bits",type=int,choices=[4,8,32],default=8)
    p.add_argument("--degree",type=int,default=3)
    a=p.parse_args()
    if a.mode=="pack":
        result=encode(np.load(a.input,allow_pickle=False),a.degree,a.bits)
        with a.output.open("xb") as out:
            out.write(result)
    else:
        result=decode(a.input.read_bytes())
        with a.output.open("xb") as out:
            np.save(out,result,allow_pickle=False)
