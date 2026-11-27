"""Fix lane F4, item 5 (code review N1; security review N-new-1, N-new-4): keeping a file decodes it ONCE, in the
guarded child, which returns the index and the words in its JSON verdict; the parent validates them and decodes
nothing. The index and text layer are the same as an in-process decode gives. An approved external adapter that
loaded in the parent but not in the child means the file is kept, not read."""
import gzip
import io
import json
import zipfile

import pytest

from neuroscifi.artifacts import adapters, guarded
from neuroscifi.artifacts import store as A
from neuroscifi.instance import store

T0 = 1_800_000_000.0


def _zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/notes.txt", "Note one\nThe quick brown fox.\n")
        z.writestr("b.csv", "x,y\n1,2\n3,4\n")
    return b.getvalue()


def _unsafe_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("../escape.txt", "x")
    return b.getvalue()


FILES = {"rain.csv": b"day,rain\n1,0.5\n2,1.25\n3,0\n", "notes.zip": _zip(), "data.json": b'{"a": [1, 2, {"b": "c"}]}',
         "page.md": b"# Title\n\nSome words here.\n", "blob.bin": bytes(range(256)) * 8,
         "song.mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 400,
         "escape.zip": _unsafe_zip(), "small.gz": gzip.compress(b"hello there\n", mtime=0)}


def _db(tmp_path, name):
    path = tmp_path / f"{name}.sqlite"
    store.create(path, T0)
    return store.connect(path)


def _kept(db, name, data):
    try:
        record = A.add(db, data, name=name, now=T0)
    except A.StoreRefused as error:
        return ("refused", error.rule)
    except Exception as error:
        return ("error", type(error).__name__, getattr(error, "rule", None))
    side = A._sidecar(db, create=False)
    with side:
        text = side.layer(record.id, "text")
    index = dict(record.index)
    return (record.family, record.format, record.flagged, json.dumps(index, sort_keys=True), text,
            [(g["format"], g["outcome"], g["count"]) for g in A.gaps(db)])


def test_keeping_a_file_gives_the_same_index_and_words_with_one_decode(tmp_path, monkeypatch):
    off = {name: _kept(_db(tmp_path, "off" + str(i)), name, data) for i, (name, data) in enumerate(FILES.items())}
    monkeypatch.setattr(guarded, "ENABLED", True)
    real = adapters.decode
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs.get("name"))
        return real(*args, **kwargs)
    monkeypatch.setattr(adapters, "decode", counted)
    on = {name: _kept(_db(tmp_path, "on" + str(i)), name, data) for i, (name, data) in enumerate(FILES.items())}
    assert calls == []                       # nothing was decoded in this process: the child did it, once
    assert on == off


def test_an_external_adapter_loaded_only_here_means_the_file_is_kept_unread(tmp_path, monkeypatch):
    monkeypatch.setattr(guarded, "ENABLED", True)
    monkeypatch.setattr(adapters, "loaded_external", lambda: ("someone=dist==1.0",))
    with pytest.raises(A.Refused) as caught:
        guarded.decode(FILES["rain.csv"], name="rain.csv")
    assert caught.value.rule == "external_unguarded"
    db = _db(tmp_path, "ext")
    record = A.add(db, FILES["rain.csv"], name="rain.csv", now=T0)
    assert record.index["error"]["rule"] == "external_unguarded" and "representation" not in record.index


def test_a_malformed_summary_is_a_refusal_never_trusted():
    with pytest.raises((KeyError, TypeError, ValueError)):
        guarded._summary_of({"status": "decoded", "detected": {"family": "nonsense"}})
    with pytest.raises(ValueError):
        guarded._summary_of({"status": "decoded", "detected": {"family": "text", "format": "txt", "media_type": "x",
                                                               "basis": "structure"}, "description": {},
                             "text": "x" * (guarded.TEXT_MAX + 1)})
