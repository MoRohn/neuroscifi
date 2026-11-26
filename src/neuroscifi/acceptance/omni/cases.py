"""FROZEN case tables of the OMNIMIND benchmark: the development split and the held-out split of every family.

Written and committed before any OMNIMIND mechanism exists (lane Z, from `omnimind` = main 8f68beb). The rules:

- Tune on DEVELOPMENT only. HELD_OUT reports a result once a mechanism is chosen; it is never used to choose.
- Never edit this file to make a result look better. tests/acceptance/test_omni_bench.py pins this file's SHA-256
  and every family's task digest per split; a change shows up there and in review, and every earlier number stops
  being comparable. New tasks go in a new file with a new name.
- The two splits are written separately: other seeds (development 3100-3599, held-out 3600-4099), other file names,
  sizes, parameters and variants, so that something fitted to the development files shows a gap on held-out.
- The withheld split is not here (a later lane writes acceptance/omni/withheld_*.py with seeds 9100-9599).

Each entry is plain data read by the family module that names it; generators are deterministic for an entry.
"""

# ---- 1. intake: one modality at a time ---------------------------------------------------------------------------
INTAKE = {
    "development": (
        # tables
        {"name": "csv-comma-quoted", "fmt": "csv", "seed": 3101, "rows": 12, "missing": 2, "quote_text": True,
         "columns": ("site", "depth_m", "samples", "note")},
        {"name": "csv-semicolon-decimal-comma", "fmt": "csv", "seed": 3102, "rows": 9, "missing": 1,
         "delimiter": ";", "decimal": ",", "columns": ("station", "level", "count", "remark")},
        {"name": "tsv-plain", "fmt": "tsv", "seed": 3103, "rows": 15, "columns": ("probe", "temp_c", "ticks", "tag")},
        {"name": "json-records", "fmt": "json_records", "seed": 3104, "rows": 7, "missing": 1,
         "columns": ("plot", "yield_kg", "rows_sown", "crop")},
        {"name": "ndjson-records", "fmt": "ndjson", "seed": 3105, "rows": 10,
         "columns": ("sensor", "volts", "reads", "zone")},
        {"name": "xlsx-two-sheets", "fmt": "xlsx", "seed": 3106, "sheets": ("Readings", "Sites"), "rows": (6, 4),
         "columns": ("label", "amount", "when")},
        # documents
        {"name": "markdown-report", "fmt": "markdown", "seed": 3107, "sections": 3, "rows": 4},
        {"name": "html-article", "fmt": "html", "seed": 3108, "sections": 4, "rows": 3, "figures": 2},
        {"name": "docx-report", "fmt": "docx", "seed": 3109, "sections": 3, "rows": 3},
        {"name": "odt-note", "fmt": "odt", "seed": 3110, "sections": 2, "rows": 3},
        {"name": "epub-two-chapters", "fmt": "epub", "seed": 3111, "chapters": 2},
        {"name": "pdf-two-pages", "fmt": "pdf", "seed": 3112, "pages": 2},
        # images
        {"name": "png-gray8", "fmt": "png", "seed": 3113, "mode": "gray", "bits": 8, "size": (13, 9)},
        {"name": "png-rgb16-interlaced", "fmt": "png", "seed": 3114, "mode": "rgb", "bits": 16, "size": (11, 10),
         "interlace": True},
        {"name": "png-rgba8", "fmt": "png", "seed": 3115, "mode": "rgba", "bits": 8, "size": (9, 7)},
        {"name": "png-palette-trns", "fmt": "png", "seed": 3116, "mode": "palette", "bits": 8, "size": (10, 6),
         "colors": 5, "trns": True},
        {"name": "png-gray2-interlaced", "fmt": "png", "seed": 3117, "mode": "gray", "bits": 2, "size": (17, 5),
         "interlace": True},
        {"name": "pgm-binary16", "fmt": "pgm", "seed": 3118, "size": (8, 6), "maxval": 65535},
        {"name": "ppm-binary8", "fmt": "ppm", "seed": 3119, "size": (7, 5)},
        {"name": "pgm-ascii", "fmt": "pgm", "seed": 3120, "size": (6, 4), "ascii": True, "comment": "made by hand"},
        {"name": "bmp-24-bottom-up", "fmt": "bmp", "seed": 3121, "size": (7, 5), "bits": 24},
        {"name": "bmp-32-top-down", "fmt": "bmp", "seed": 3122, "size": (6, 4), "bits": 32, "top_down": True},
        {"name": "gif-two-frames", "fmt": "gif", "seed": 3123, "size": (8, 6), "colors": 4, "delays_cs": (20, 35)},
        {"name": "svg-shapes", "fmt": "svg", "seed": 3124, "size": (200, 120), "circles": 3, "rects": 2, "texts": 2},
        # sound
        {"name": "wav16-mono-tone", "fmt": "wav", "seed": 3125, "rate": 22050, "bits": 16, "channels": 1,
         "freq": 440.0, "bursts": 1, "burst_s": 0.8, "gap_s": 0.2},
        {"name": "wav24-stereo-bursts", "fmt": "wav", "seed": 3126, "rate": 44100, "bits": 24, "channels": 2,
         "freq": 660.0, "bursts": 3, "burst_s": 0.25, "gap_s": 0.2},
        {"name": "wav8-mono-bursts", "fmt": "wav", "seed": 3127, "rate": 8000, "bits": 8, "channels": 1,
         "freq": 300.0, "bursts": 4, "burst_s": 0.15, "gap_s": 0.15},
        # meshes
        {"name": "obj-cube-quads", "fmt": "obj", "seed": 3128, "shape": "cube", "side": 2.5,
         "origin": (1.0, -0.5, 0.25)},
        {"name": "stl-ascii-tetrahedron", "fmt": "stl_ascii", "seed": 3129, "shape": "tetra", "legs": (3.0, 2.0, 1.5),
         "origin": (0.0, 0.0, 0.0)},
        {"name": "stl-binary-cube", "fmt": "stl_binary", "seed": 3130, "shape": "cube", "side": 1.75,
         "origin": (-1.0, 2.0, 0.5)},
        {"name": "ply-ascii-open-box", "fmt": "ply", "seed": 3131, "shape": "open_box", "side": 4.0,
         "origin": (0.0, 0.0, 0.0)},
        {"name": "ply-binary-tetrahedron", "fmt": "ply_binary", "seed": 3132, "shape": "tetra",
         "legs": (1.0, 2.5, 2.0), "origin": (0.5, 0.5, 0.5), "quads": False},
        # geography, trees
        {"name": "geojson-mixed", "fmt": "geojson", "seed": 3133, "center": (13.4, 52.5), "spread": 0.2,
         "points": 4, "line": 5},
        {"name": "gpx-two-segments", "fmt": "gpx", "seed": 3134, "center": (-3.2, 55.9), "spread": 0.05,
         "waypoints": 2, "segments": (4, 3)},
        {"name": "json-nested", "fmt": "json_tree", "seed": 3135, "items": 3, "readings": 6},
        {"name": "xml-catalog", "fmt": "xml_tree", "seed": 3136, "items": 4},
        # code, notebooks
        {"name": "python-module", "fmt": "py", "seed": 3137, "modules": (
            {"path": "orbit.py", "doc": "Orbit helpers.", "imports": ("math",),
             "functions": {"square": [], "area": ["square"], "period": ["area", "square"], "report": ["period"]},
             "class": ("Meter", {"read": ["area"], "reset": []})},)},
        {"name": "python-package-zip", "fmt": "py_zip", "seed": 3138, "modules": (
            {"path": "tide/__init__.py", "doc": "Tide package.", "from_imports": {"tide.shore": ["level"]}},
            {"path": "tide/shore.py", "doc": "Shore.", "imports": ("math",),
             "functions": {"level": ["depth"], "depth": []}},
            {"path": "tide/chart.py", "doc": "Chart.", "imports": ("json",), "from_imports": {"tide.shore": ["level"]},
             "functions": {"plot": ["level", "scale"], "scale": []}})},
        {"name": "ipynb-four-cells", "fmt": "ipynb", "seed": 3139, "cells": 4},
        # arrays
        {"name": "npy-float64-2d", "fmt": "npy", "seed": 3140, "dtype": "<f8", "shape": (4, 5)},
        {"name": "npy-int32-1d", "fmt": "npy", "seed": 3141, "dtype": "<i4", "shape": (17,)},
        {"name": "npy-bigendian-fortran", "fmt": "npy", "seed": 3142, "dtype": ">f4", "shape": (3, 4),
         "fortran": True},
        {"name": "npz-two-arrays", "fmt": "npz", "seed": 3143, "arrays": {"pressure": ("<f8", (6,)),
                                                                          "flow": ("<i4", (2, 3))}},
        {"name": "npz-states-targets", "fmt": "npz", "seed": 3144, "arrays": {"states": ("<f8", (5, 3)),
                                                                              "targets": ("<f8", (5, 1))}},
        # archives
        {"name": "zip-mixed", "fmt": "zip", "seed": 3145, "folder": "data", "rows": 5,
         "columns": ("id", "x", "n", "note")},
        {"name": "tar-mixed", "fmt": "tar", "seed": 3146, "folder": "tables", "rows": 4,
         "columns": ("key", "y", "k", "memo")},
        {"name": "tgz-mixed", "fmt": "tgz", "seed": 3147, "folder": "set", "rows": 6,
         "columns": ("ref", "z", "m", "text")},
        {"name": "csv-gzipped", "fmt": "csv_gz", "seed": 3148, "rows": 8, "columns": ("cell", "load", "hits", "tag")},
    ),
    "held_out": (
        # tables
        {"name": "csv-crlf-quoted-missing", "fmt": "csv", "seed": 3601, "rows": 17, "missing": 3, "quote_text": True,
         "crlf": True, "columns": ("well", "flow_lps", "pumps", "comment")},
        {"name": "csv-semicolon-decimal-comma-long", "fmt": "csv", "seed": 3602, "rows": 21, "missing": 2,
         "delimiter": ";", "decimal": ",", "columns": ("parcel", "area_ha", "trees", "owner")},
        {"name": "tsv-missing", "fmt": "tsv", "seed": 3603, "rows": 11, "missing": 2,
         "columns": ("hive", "mass_kg", "frames", "keeper")},
        {"name": "json-records-wide", "fmt": "json_records", "seed": 3604, "rows": 13, "missing": 2,
         "columns": ("lane", "speed_kmh", "axles", "make")},
        {"name": "ndjson-missing", "fmt": "ndjson", "seed": 3605, "rows": 6, "missing": 1,
         "columns": ("valve", "bar", "cycles", "line")},
        {"name": "xlsx-two-sheets-long", "fmt": "xlsx", "seed": 3606, "sheets": ("Ledger", "Depots"),
         "rows": (9, 3), "columns": ("item", "cost", "date")},
        # documents
        {"name": "markdown-long", "fmt": "markdown", "seed": 3607, "sections": 5, "rows": 6, "figures": 2},
        {"name": "html-short", "fmt": "html", "seed": 3608, "sections": 2, "rows": 5, "figures": 1},
        {"name": "docx-long", "fmt": "docx", "seed": 3609, "sections": 5, "rows": 5},
        {"name": "odt-three-sections", "fmt": "odt", "seed": 3610, "sections": 3, "rows": 4},
        {"name": "epub-three-chapters", "fmt": "epub", "seed": 3611, "chapters": 3},
        {"name": "pdf-three-pages", "fmt": "pdf", "seed": 3612, "pages": 3},
        # images
        {"name": "png-gray16-interlaced", "fmt": "png", "seed": 3613, "mode": "gray", "bits": 16, "size": (9, 12),
         "interlace": True},
        {"name": "png-rgb8", "fmt": "png", "seed": 3614, "mode": "rgb", "bits": 8, "size": (15, 8)},
        {"name": "png-rgba16", "fmt": "png", "seed": 3615, "mode": "rgba", "bits": 16, "size": (6, 6)},
        {"name": "png-palette-interlaced", "fmt": "png", "seed": 3616, "mode": "palette", "bits": 8,
         "size": (12, 11), "colors": 7, "interlace": True},
        {"name": "png-gray4", "fmt": "png", "seed": 3617, "mode": "gray", "bits": 4, "size": (19, 3)},
        {"name": "ppm-binary16", "fmt": "ppm", "seed": 3618, "size": (5, 7), "maxval": 65535},
        {"name": "pgm-binary8", "fmt": "pgm", "seed": 3619, "size": (11, 3)},
        {"name": "ppm-ascii", "fmt": "ppm", "seed": 3620, "size": (4, 3), "ascii": True},
        {"name": "bmp-24-odd-width", "fmt": "bmp", "seed": 3621, "size": (5, 6), "bits": 24},
        {"name": "bmp-24-top-down", "fmt": "bmp", "seed": 3622, "size": (9, 2), "bits": 24, "top_down": True},
        {"name": "gif-three-frames", "fmt": "gif", "seed": 3623, "size": (10, 7), "colors": 6, "frames": 3,
         "delays_cs": (10, 50, 25)},
        {"name": "svg-many-shapes", "fmt": "svg", "seed": 3624, "size": (320, 240), "circles": 5, "rects": 4,
         "texts": 3},
        # sound
        {"name": "wav16-stereo-tone", "fmt": "wav", "seed": 3625, "rate": 44100, "bits": 16, "channels": 2,
         "freq": 523.25, "bursts": 1, "burst_s": 0.6, "gap_s": 0.1},
        {"name": "wav24-mono-bursts", "fmt": "wav", "seed": 3626, "rate": 48000, "bits": 24, "channels": 1,
         "freq": 880.0, "bursts": 5, "burst_s": 0.12, "gap_s": 0.18},
        {"name": "wav16-extensible-bursts", "fmt": "wav", "seed": 3627, "rate": 16000, "bits": 16, "channels": 1,
         "freq": 250.0, "bursts": 2, "burst_s": 0.3, "gap_s": 0.25, "extensible": True},
        # meshes
        {"name": "obj-tetrahedron", "fmt": "obj", "seed": 3628, "shape": "tetra", "legs": (2.0, 2.0, 3.5),
         "origin": (-1.0, -1.0, -1.0), "quads": False},
        {"name": "stl-ascii-cube", "fmt": "stl_ascii", "seed": 3629, "shape": "cube", "side": 3.25,
         "origin": (0.0, 0.0, -2.0)},
        {"name": "stl-binary-open-box", "fmt": "stl_binary", "seed": 3630, "shape": "open_box", "side": 2.0,
         "origin": (1.0, 1.0, 1.0)},
        {"name": "ply-ascii-cube", "fmt": "ply", "seed": 3631, "shape": "cube", "side": 0.5,
         "origin": (0.25, 0.25, 0.25)},
        {"name": "obj-open-box-triangles", "fmt": "obj", "seed": 3632, "shape": "open_box", "side": 1.5,
         "origin": (0.0, 0.0, 0.0), "quads": False},
        # geography, trees
        {"name": "geojson-southern", "fmt": "geojson", "seed": 3633, "center": (151.2, -33.9), "spread": 0.4,
         "points": 6, "line": 3},
        {"name": "gpx-three-segments", "fmt": "gpx", "seed": 3634, "center": (-122.4, 37.8), "spread": 0.1,
         "waypoints": 3, "segments": (2, 5, 3)},
        {"name": "json-nested-wide", "fmt": "json_tree", "seed": 3635, "items": 5, "readings": 9},
        {"name": "xml-catalog-long", "fmt": "xml_tree", "seed": 3636, "items": 7},
        # code, notebooks
        {"name": "python-module-deep", "fmt": "py", "seed": 3637, "modules": (
            {"path": "ledger.py", "doc": "Ledger helpers.", "imports": ("json", "math"),
             "functions": {"cents": [], "tax": ["cents"], "net": ["cents", "tax"], "total": ["net", "tax"],
                           "summary": ["total"]},
             "class": ("Book", {"post": ["net"], "close": ["summary"], "open": []})},)},
        {"name": "python-package-zip-three", "fmt": "py_zip", "seed": 3638, "modules": (
            {"path": "mill/__init__.py", "doc": "Mill package.", "from_imports": {"mill.gear": ["turn"]}},
            {"path": "mill/gear.py", "doc": "Gears.", "imports": ("math",),
             "functions": {"turn": ["ratio"], "ratio": [], "teeth": ["ratio"]}},
            {"path": "mill/belt.py", "doc": "Belts.", "imports": ("statistics",),
             "from_imports": {"mill.gear": ["turn", "teeth"]},
             "functions": {"drive": ["turn", "teeth"], "slack": []}},
            {"path": "mill/log.py", "doc": "Logs.", "imports": ("json",), "from_imports": {"mill.belt": ["drive"]},
             "functions": {"record": ["drive"]}})},
        {"name": "ipynb-six-cells", "fmt": "ipynb", "seed": 3639, "cells": 6},
        # arrays
        {"name": "npy-uint8-3d", "fmt": "npy", "seed": 3640, "dtype": "|u1", "shape": (2, 3, 4)},
        {"name": "npy-float32-1d", "fmt": "npy", "seed": 3641, "dtype": "<f4", "shape": (23,)},
        {"name": "npy-bigendian-int-fortran", "fmt": "npy", "seed": 3642, "dtype": ">i4", "shape": (5, 2),
         "fortran": True},
        {"name": "npz-three-arrays", "fmt": "npz", "seed": 3643, "arrays": {"rain": ("<f8", (4, 2)),
                                                                            "days": ("<i4", (9,)),
                                                                            "gauge": ("|u1", (3,))}},
        {"name": "npz-states-targets-long", "fmt": "npz", "seed": 3644, "arrays": {"states": ("<f8", (8, 2)),
                                                                                   "targets": ("<f8", (8, 2))}},
        # archives
        {"name": "zip-nested-folder", "fmt": "zip", "seed": 3645, "folder": "survey/2024", "rows": 7,
         "columns": ("code", "w", "c", "obs")},
        {"name": "tar-flat", "fmt": "tar", "seed": 3646, "folder": "out", "rows": 3,
         "columns": ("name", "v", "q", "info")},
        {"name": "tgz-deep-folder", "fmt": "tgz", "seed": 3647, "folder": "a/b/c", "rows": 5,
         "columns": ("idx", "t", "u", "desc")},
        {"name": "csv-gzipped-long", "fmt": "csv_gz", "seed": 3648, "rows": 19, "missing": 1,
         "columns": ("meter", "kwh", "pulses", "room")},
    ),
}
