"""Raster measurements (lane B2) on hand-made images whose answers are known by construction."""
import json

import numpy as np

from neuroscifi.artifacts import measure_image as M
from neuroscifi.artifacts import types as T


def _rgb(h, w, colour):
    return np.tile(np.array(colour, np.uint8), (h, w, 1))


def test_stats_histogram_and_dominant_colours():
    img = _rgb(10, 10, (255, 0, 0))
    img[:, 5:] = (0, 0, 255)
    r = T.Raster(img, "rgb", 8)
    stats = {s["channel"]: s for s in M.channel_stats(r)}
    assert stats["red"]["mean"] == 0.5 and stats["red"]["max"] == 1.0 and stats["green"]["max"] == 0.0
    hist = M.luminance_histogram(r)
    assert sum(hist) == 100
    assert hist[int(0.2126 * 16)] == 50 and hist[int(0.0722 * 16)] == 50
    dom = M.dominant_colours(r, k=3)
    assert len(dom) == 2 and {tuple(d["colour"]) for d in dom} == {(223, 32, 32), (32, 32, 223)}
    assert all(d["share"] == 0.5 for d in dom)


def test_transparent_share_and_gray_16bit_normalisation():
    px = np.zeros((2, 2, 4), np.uint8)
    px[0, 0, 3] = 255
    assert M.transparent_share(T.Raster(px, "rgba", 8)) == 0.75
    g = T.Raster(np.array([[0, 65535]], np.uint16), "gray", 16)
    assert M.luminance(g).tolist() == [[0.0, 1.0]]
    assert M.transparent_share(g) == 0.0


def test_edge_density_of_a_step_and_a_flat_image():
    flat = T.Raster(np.full((20, 20), 128, np.uint8), "gray", 8)
    assert M.edge_density(flat) == 0.0
    step = np.zeros((20, 20), np.uint8)
    step[:, 10:] = 255
    d = M.edge_density(T.Raster(step, "gray", 8))
    assert d == 40 / 400              # columns 9 and 10 see the step


def test_regions_count_and_boxes():
    img = np.zeros((12, 12), np.uint8)
    img[1:3, 1:4] = 255                  # 2x3 block
    img[6:11, 6:8] = 255                 # 5x2 block
    img[6, 8:11] = 255                   # attached arm -> same region (4-connected)
    img[0, 11] = 255                     # single pixel
    img[11, 0] = 255
    img[10, 1] = 255                     # diagonal to (11, 0): NOT 4-connected
    out = M.regions(T.Raster(img, "gray", 8))
    assert out["count"] == 5
    first = out["largest"][0]
    assert first["box"] == [6, 6, 10, 10] and first["pixels"] == 13
    assert out["largest"][1] == {"box": [1, 1, 3, 2], "pixels": 6}
    big = T.Raster(np.zeros((2000, 1000), np.uint8), "gray", 8)
    assert "skipped" in M.regions(big)


def test_u_shape_merges_into_one_region():
    img = np.zeros((5, 5), np.uint8)
    img[0:4, 0] = 255
    img[0:4, 4] = 255
    img[4, :] = 255
    assert M.regions(T.Raster(img, "gray", 8))["count"] == 1


def test_analyse_is_json_able():
    r = T.Raster(np.random.default_rng(1).integers(0, 255, (16, 16, 4), dtype=np.uint8), "rgba", 8)
    raw = json.dumps(M.analyse(r), allow_nan=False)
    assert len(raw) < 32 * 1024
