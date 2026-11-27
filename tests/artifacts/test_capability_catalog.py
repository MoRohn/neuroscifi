"""The live capability lists, pinned (integration lane, ADR-072): `adapters.catalog()`, `make.catalog()` and
`check.catalog()` are the only statements of what can be read, made and checked here, so a capability may not
vanish (or appear) silently. A change to a registry must change this table in the same commit, on purpose.

Availability is pinned too: every built-in reads with the standard library and numpy alone (the PDF reader degrades
to header facts without pypdf and says so in its reason, but stays available); only the pickle row is unavailable,
by rule."""
import json

from neuroscifi import cli
from neuroscifi.artifacts import adapters, check, make
from neuroscifi.artifacts.cli import _plain

# (name, formats, reads, available), in registration order: the kernel's generic adapters first, B1's and B2's after
ADAPTERS = (
    ('plain-text', ('text',), 'content', True),
    ('json', ('json', 'geojson', 'ipynb'), 'content', True),
    ('compressed-stream', ('gzip', 'zstd', 'bzip2', 'xz'), 'content', True),
    ('b1-delimited', ('csv', 'tsv'), 'content', True),
    ('b1-ndjson', ('ndjson', 'jsonl'), 'content', True),
    ('b1-xlsx', ('xlsx', 'xlsm'), 'content', True),
    ('b1-ods', ('ods',), 'content', True),
    ('b1-markdown', ('markdown', 'md'), 'content', True),
    ('b1-html', ('html', 'xhtml'), 'content', True),
    ('b1-rtf', ('rtf',), 'content', True),
    ('b1-docx', ('docx',), 'content', True),
    ('b1-odt', ('odt',), 'content', True),
    ('b1-pptx', ('pptx',), 'content', True),
    ('b1-odp', ('odp',), 'content', True),
    ('b1-epub', ('epub',), 'content', True),
    ('b1-pdf', ('pdf',), 'content', True),
    ('b1-xml', ('xml', 'rss', 'atom'), 'content', True),
    ('b1-plist', ('plist',), 'content', True),
    ('b1-toml', ('toml',), 'content', True),
    ('b1-ini', ('ini', 'cfg'), 'content', True),
    ('b1-yaml', ('yaml', 'yml'), 'content or header', True),
    ('b1-python', ('python', 'py'), 'content', True),
    ('b1-code-surface', ('javascript', 'js', 'typescript', 'ts', 'c', 'cpp', 'c++', 'h', 'shell', 'sh', 'bash'), 'content', True),
    ('b1-manifests', ('pyproject', 'pyproject-toml', 'requirements', 'requirements-txt', 'package-json'), 'content', True),
    ('b1-notebook', ('ipynb', 'jupyter', 'notebook'), 'content', True),
    ('b1-zip', ('zip', 'jar', 'apk'), 'content', True),
    ('b1-tar', ('tar',), 'content', True),
    ('b1-7z-rar', ('7z', 'rar'), 'identity', True),
    ('b1-sqlite', ('sqlite', 'sqlite3'), 'content', True),
    ('b2-png', ('png',), 'content', True),
    ('b2-netpbm', ('pbm', 'pgm', 'ppm', 'pam'), 'content', True),
    ('b2-farbfeld', ('farbfeld',), 'content', True),
    ('b2-qoi', ('qoi',), 'content', True),
    ('b2-bmp', ('bmp',), 'content', True),
    ('b2-ico', ('ico',), 'content', True),
    ('b2-gif', ('gif',), 'content', True),
    ('b2-jpeg', ('jpeg',), 'header', True),
    ('b2-webp', ('webp',), 'header', True),
    ('b2-heif', ('heic', 'avif'), 'header', True),
    ('b2-psd', ('psd',), 'header', True),
    ('b2-tiff', ('tiff',), 'content or header', True),
    ('b2-dicom', ('dicom',), 'content or header', True),
    ('b2-fits', ('fits',), 'content or header', True),
    ('b2-svg', ('svg',), 'content', True),
    ('b2-wav', ('wav',), 'content', True),
    ('b2-aiff', ('aiff',), 'content', True),
    ('b2-au', ('au',), 'content', True),
    ('b2-mp3', ('mp3',), 'header', True),
    ('b2-aac', ('aac',), 'header', True),
    ('b2-flac', ('flac',), 'header', True),
    ('b2-ogg', ('ogg', 'opus', 'vorbis', 'ogv'), 'header', True),
    ('b2-m4a', ('m4a',), 'header', True),
    ('b2-midi', ('midi',), 'content', True),
    ('b2-isobmff', ('mp4', 'mov', '3gp'), 'structure', True),
    ('b2-matroska', ('mkv', 'webm'), 'structure', True),
    ('b2-avi', ('avi',), 'structure', True),
    ('b2-obj', ('obj',), 'content', True),
    ('b2-stl', ('stl',), 'content', True),
    ('b2-ply', ('ply',), 'content', True),
    ('b2-off', ('off',), 'content', True),
    ('b2-gltf', ('gltf', 'glb'), 'content', True),
    ('b2-geo', ('geojson', 'gpx', 'kml', 'kmz', 'wkt'), 'content', True),
    ('b2-npy', ('npy',), 'content', True),
    ('b2-npz', ('npz',), 'content', True),
    ('model_file', ('nsf_model',), 'content', True),          # lane P (ADR-076): the owner's model file
    ('refuse-pickle', ('pickle',), 'nothing', False),
)
GENERATORS = (
    ('animation.svg', 'g1.animation.svg'),
    ('chart.bar', 'g1.chart.bar'),
    ('chart.heatmap', 'g1.chart.heatmap'),
    ('chart.histogram', 'g1.chart.histogram'),
    ('chart.line', 'g1.chart.line'),
    ('chart.scatter', 'g1.chart.scatter'),
    ('chart.small_multiples', 'g1.chart.small_multiples'),
    ('data.csv', 'g1.data.csv'),
    ('data.json', 'g1.data.json'),
    ('document.html', 'g1.document.html'),
    ('document.md', 'g1.document.md'),
    ('explorable.model', 'g2.explorable'),
    ('explorable.steps', 'g2.explorable'),
    ('flow.steps', 'g1.flow.steps'),
    ('graph.nodelink', 'g1.graph.nodelink'),
    ('graph.tree', 'g1.graph.tree'),
    ('image.apng', 'g2.raster'),
    ('image.raster', 'g2.raster'),
    ('solid.box', 'g2.solids'),
    ('solid.extrude', 'g2.solids'),
    ('solid.mesh', 'g2.solids'),                               # lane P (ADR-076): a given mesh, as it is
    ('solid.points', 'g2.solids'),
    ('solid.revolve', 'g2.solids'),
    ('solid.view', 'g2.explorable'),
    ('sound.sequence', 'g2.sound'),
    ('sound.sonify', 'g2.sound'),
    ('sound.tone', 'g2.sound'),
    ('surface.grid', 'g2.solids'),
    ('table.html', 'g1.table.html'),
    ('timeline', 'g1.timeline'),
)
VALIDATORS = (
    ('application/json', 'g1.json'),
    ('application/vnd.neuroscifi.explorable+html', 'g2.explorable'),
    ('audio/wav', 'g2.wav'),
    ('audio/x-wav', 'g2.wav'),
    ('image/apng', 'g2.png'),
    ('image/png', 'g2.png'),
    ('image/svg+xml', 'g1.svg'),
    ('model/obj', 'g2.mesh'),
    ('model/stl', 'g2.mesh'),
    ('text/csv', 'g1.csv'),
    ('text/html', 'g1.html'),
    ('text/markdown', 'g1.markdown'),
)


def test_the_adapter_catalog_is_pinned():
    rows = [r for r in adapters.catalog() if r["source"] == "builtin"]
    assert [(r["name"], tuple(r["formats"]), r["reads"], r["available"]) for r in rows] == list(ADAPTERS)


def test_every_header_or_structure_reader_says_so_in_its_row():
    # the formats DESIGN §8 names as not decoded here are never catalogued as "content"
    by_format = {f: r for r in adapters.catalog() for f in r["formats"]}
    for fmt in ("jpeg", "webp", "heic", "avif", "psd", "mp3", "aac", "flac", "opus", "vorbis", "m4a"):
        assert by_format[fmt]["reads"] == "header", fmt
    for fmt in ("mp4", "mov", "mkv", "webm", "avi"):
        assert by_format[fmt]["reads"] == "structure", fmt
    assert by_format["7z"]["reads"] == by_format["rar"]["reads"] == "identity"


def test_the_generator_and_validator_catalogs_are_pinned():
    assert [(r["form"], r["generator"]) for r in make.catalog()] == list(GENERATORS)
    assert all(r["available"] for r in make.catalog())
    assert [(r["media_type"], r["validator"]) for r in check.catalog()] == list(VALIDATORS)


def test_every_made_media_type_has_a_validator():
    # a generator whose output no validator reads could never pass a check: none may be registered
    checked = {r["media_type"] for r in check.catalog()}
    from neuroscifi.artifacts.make import _g1, _g2
    made = {"audio/wav", "model/stl", "model/obj", "image/png", "image/apng", _g2.explorable.MEDIA_TYPE}
    assert made | set(_g1.FORMS.values()) <= checked


def test_the_commands_print_the_catalogs_in_plain_words(capsys):
    assert cli.main(["artifact", "forms"]) == 0
    out = capsys.readouterr().out
    assert "sound.sonify: made by g2.sound version 1: ready" in out and "audio/wav: g2.wav" in out
    assert "chart.line: made by g1.chart.line version 1: ready" in out and "image/svg+xml: g1.svg" in out
    assert cli.main(["artifact", "forms", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert [(r["form"], r["generator"]) for r in value["generators"]] == list(GENERATORS)
    text = _plain("adapters", {"approved": [], "adapters": adapters.catalog()})
    assert "jpeg: reads only the header (sizes, layout, metadata): the content is not decoded here [b2-jpeg" in text
    assert "mp4, mov, 3gp: reads the container's structure and tracks: no frame or sample is decoded" in text
    assert "csv, tsv: reads the content [b1-delimited, builtin]" in text
    assert "pickle: not available: refused" in text
