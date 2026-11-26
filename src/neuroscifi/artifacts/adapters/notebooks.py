"""Jupyter notebooks to `Document` (lane B1). Nothing is executed: the outputs are the ones stored in the file.

Cells in order. Markdown cells go through the Markdown reader of `documents.py`; code cells become `code` blocks
(`ref = {"language", "execution_count", "cell"}`); raw cells become `note` blocks. Stored outputs: stream text and
`text/plain` results become `note` blocks (`ref = {"output": ..., "cell": ...}`); `image/*` outputs become figure
blocks naming the output (`member = "cell N output M (image/png)"`), not decoded here; errors become a `note` with
the error name and value (the traceback is left out; terminal colour codes removed). nbformat 4, and nbformat 3
(`worksheets` with `input`) read the same way.
"""
from __future__ import annotations

import re

from ..safe_b1 import Budget, Deadline, decode_text
from ..types import Block, Malformed
from . import Adapter
from .documents import _figure, _finish, markdown_blocks
from .tables import load_json

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
OUTPUT_TEXT_MAX = 20_000


def _src(value) -> str:
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return str(value or "")


def read_notebook(data, detected, limits):
    deadline, budget = Deadline.of(limits), Budget(limits)
    text, notes = decode_text(data)
    nb = load_json(text)
    if not isinstance(nb, dict):
        raise Malformed("the notebook is not a JSON object")
    if isinstance(nb.get("cells"), list):
        cells = nb["cells"]
    elif isinstance(nb.get("worksheets"), list) and nb["worksheets"] and isinstance(nb["worksheets"][0], dict):
        cells = nb["worksheets"][0].get("cells") or []
    else:
        raise Malformed("the notebook has no cells")
    meta = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
    kernel = meta.get("kernelspec") if isinstance(meta.get("kernelspec"), dict) else {}
    info = meta.get("language_info") if isinstance(meta.get("language_info"), dict) else {}
    language = str(kernel.get("language") or info.get("name") or "") or None
    blocks: list[Block] = []
    notes = list(notes)
    partial = False
    counts = {"code": 0, "markdown": 0, "raw": 0, "outputs": 0}

    def emit(block):
        nonlocal partial
        if not budget.fits_elements(1):
            partial = True
            return
        budget.take_elements(1)
        blocks.append(block)

    for i, cell in enumerate(cells, 1):
        if partial:
            break
        deadline.check()
        if i > limits.max_members:
            partial = True
            notes.append(f"stopped after {limits.max_members} cells")
            break
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type")
        source = _src(cell.get("source", cell.get("input")))
        if kind == "markdown":
            counts["markdown"] += 1
            md_blocks, _, md_notes, md_partial = markdown_blocks(source, budget=budget, deadline=deadline)
            blocks.extend(md_blocks)
            notes.extend(md_notes)
            partial = partial or md_partial
        elif kind == "code":
            counts["code"] += 1
            emit(Block("code", 0, source, {"language": language, "execution_count": cell.get("execution_count",
                                                                                              cell.get("prompt_number")),
                                           "cell": i}))
            for j, output in enumerate(cell.get("outputs") or [], 1):
                if not isinstance(output, dict):
                    continue
                counts["outputs"] += 1
                otype = output.get("output_type")
                ref = {"output": otype, "cell": i}
                if otype == "stream":
                    words = _ANSI.sub("", _src(output.get("text")))[:OUTPUT_TEXT_MAX]
                    if words.strip():
                        emit(Block("note", 0, words.rstrip("\n"), dict(ref, name=output.get("name"))))
                elif otype in ("execute_result", "display_data", "pyout", "display_data"):
                    bundle = output.get("data") if isinstance(output.get("data"), dict) else output
                    plain = _src(bundle.get("text/plain", bundle.get("text")))[:OUTPUT_TEXT_MAX]
                    images = [m for m in bundle if isinstance(m, str) and (m.startswith("image/") or m in ("png", "jpeg"))]
                    for mime in images:
                        emit(Block("figure", 0, plain.strip()[:500],
                                   _figure(alt=plain.strip()[:500] or None, member=f"cell {i} output {j} ({mime})")))
                    if plain.strip() and not images:
                        emit(Block("note", 0, plain.rstrip("\n"), ref))
                elif otype in ("error", "pyerr"):
                    emit(Block("note", 0, f"{output.get('ename', 'error')}: {_ANSI.sub('', str(output.get('evalue', '')))}"[:2000],
                               ref))
        elif kind in ("raw", "heading"):
            counts["raw"] += 1
            if kind == "heading":
                emit(Block("heading", int(cell.get("level") or 1), source.strip()))
            elif source.strip():
                emit(Block("note", 0, source, {"cell": i, "raw": True}))
    notes.append(f"{counts['code']} code cell(s), {counts['markdown']} markdown cell(s), {counts['outputs']} stored "
                 "output(s); nothing was executed: outputs are those saved in the file and may be stale")
    notes.append("tracebacks of stored errors are left out; image outputs are listed, not decoded")
    if partial:
        notes.append(f"stopped after {len(blocks)} blocks: a limit was reached")
    title = None
    if isinstance(meta.get("title"), str):
        title = meta["title"].strip() or None
    return _finish(detected, blocks, title, notes, partial)


class NotebookAdapter(Adapter):
    name = "b1-notebook"
    formats = ("ipynb", "jupyter", "notebook")
    families = ("notebook",)

    def decode(self, data, detected, limits):
        return read_notebook(data, detected, limits)


ADAPTERS = (NotebookAdapter,)

__all__ = ["ADAPTERS", "NotebookAdapter", "read_notebook"]
