"""SVG -> Drawing and GeoJSON/GPX/KML/KMZ/WKT -> Features (lane B2): geometry recovered through transforms, refused
content recorded and never kept, hostile XML refused; and the geographic measurements on known distances."""
import io
import json
import math
import zipfile

import numpy as np
import pytest

from b2_fixtures import b2_registered  # noqa: F401

from neuroscifi.artifacts import adapters, measure_geo, outcome
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2, svg



def _svg(body, attrs='width="100" height="50"'):
    return f'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>'.encode()


def test_shapes_through_nested_transforms():
    body = ('<g transform="translate(10,20)"><g transform="scale(2)">'
            '<rect id="r" x="1" y="1" width="3" height="4" fill="#000"/></g>'
            '<line x1="0" y1="0" x2="5" y2="0" transform="rotate(90)"/></g>'
            '<polygon points="0,0 4,0 4,3"/><circle cx="5" cy="5" r="2"/>')
    out = adapters.decode(_svg(body))
    assert out.detected.format == "svg"
    d = out.representation
    assert isinstance(d, T.Drawing) and (d.width, d.height) == (100.0, 50.0)
    shapes = {s.kind: s for s in d.shapes}
    assert shapes["rect"].points == ((12.0, 22.0), (18.0, 22.0), (18.0, 30.0), (12.0, 30.0))
    assert shapes["rect"].attrs == {"id": "r", "fill": "#000"}
    line = np.array(shapes["line"].points)
    assert np.allclose(line, [[10, 20], [10, 25]])
    assert shapes["polygon"].points[2] == (4.0, 3.0)
    assert shapes["circle"].points[:2] == ((5.0, 5.0), (7.0, 5.0))


def test_path_commands_and_arc_end_points():
    pts, sub = svg.path_points("M0 0 L10 0 h5 v5 l-5 0 Z m 20 20 C 20 30 30 30 30 20 A 5 5 0 0 1 40 20 Q 45 10 50 20",
                               T.Limits())
    assert sub == 2
    assert pts[:5] == [(0.0, 0.0), (10.0, 0.0), (15.0, 0.0), (15.0, 5.0), (10.0, 5.0)]
    assert pts[5] == (0.0, 0.0)                      # Z returns to the subpath start
    assert pts[6] == (20.0, 20.0)                    # relative m from (0, 0)
    assert pts[14] == (30.0, 20.0)                   # cubic end after 8 flattened points
    assert pts[22] == (40.0, 20.0)                   # arc end exactly
    mid = pts[18]                                    # half-way on a half circle of radius 5 centred (35, 20)
    assert abs(math.hypot(mid[0] - 35, mid[1] - 20) - 5) < 1e-9
    assert pts[-1] == (50.0, 20.0)
    with pytest.raises(T.Malformed):
        svg.path_points("M 1", T.Limits())


def test_viewbox_units_text_and_use():
    body = ('<title>Plan</title><defs><circle id="dot" r="1"/></defs>'
            '<use href="#dot" x="3" y="4"/><text x="1" y="2">Hello <tspan>world</tspan></text>')
    d = adapters.decode(_svg(body, 'width="10cm" viewBox="0 0 200 100"')).representation
    assert (d.width, d.height) == (200.0, 100.0)
    kinds = [s.kind for s in d.shapes]
    assert kinds.count("circle") == 1 and d.shapes[kinds.index("circle")].points[0] == (3.0, 4.0)
    assert "Plan" in d.text() and "Hello" in d.text() and "world" in d.text()
    assert svg.length("1in") == 96.0 and svg.length("10%") is None


def test_active_content_is_refused_and_not_kept_outside_references_are_noted():
    body = ('<script>alert(1)</script><foreignObject><div>x</div></foreignObject>'
            '<rect width="1" height="1" onclick="steal()"/><image href="data:image/png;base64,AAAA" width="2" '
            'height="2"/><a href="https://example.invalid"><circle r="1"/></a>')
    out = adapters.decode(_svg(body, 'width="5" height="5" onload="go()"'))
    note = next(n for n in out.notes if n.startswith("refused"))
    for what in ("script", "foreignObject", "2 x event attribute"):
        assert what in note
    d = out.representation
    assert not any("alert" in (s.text or "") for s in d.shapes)
    assert "example" not in json.dumps([dict(s.attrs) for s in d.shapes])
    # ADR-080 (lane FX2): a reference to an outside file or address is never followed and never refuses the drawing;
    # the drawing is read, a note names what refers (never where), and the outside image's shape keeps no address
    out = adapters.decode(_svg('<rect width="1" height="1"/><image href="https://example.invalid/a.png" width="2" '
                               'height="2"/><use href="other.svg#x"/><a href="https://example.invalid"><circle r="1"/>'
                               '</a>', 'width="5" height="5"'))
    assert [s.kind for s in out.representation.shapes] == ["rect", "image", "circle"]
    assert outcome.outside("1 x image, 1 x link address, 1 x use target") in out.notes
    assert "example" not in json.dumps([dict(s.attrs) for s in out.representation.shapes] + list(out.notes))


def test_svg_hostile_xml():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>')
    with pytest.raises(T.Refused) as err:
        adapters.decode(bomb)
    assert err.value.rule == "xml_dtd"
    with pytest.raises(T.Malformed):
        adapters.decode(b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>', name="x.svg")
    deep = _svg("<g>" * 300 + "</g>" * 300)
    with pytest.raises(T.Refused):
        adapters.decode(deep)
    bomb = ['<defs><g id="l0"><rect width="1" height="1"/></g>']
    bomb += [f'<g id="l{i}">' + f'<use href="#l{i - 1}"/>' * 10 + "</g>" for i in range(1, 9)]
    with pytest.raises(T.Refused) as err:                  # 10^8 copies requested
        adapters.decode(_svg("".join(bomb) + '</defs><use href="#l8"/>'))
    assert err.value.rule == "svg_use"
    loop = _svg('<g id="a"><use href="#a"/><use href="#a"/><rect width="1" height="1"/></g>')
    out = adapters.decode(loop)                   # a self-referencing use is cut at the depth limit
    assert len(out.representation.shapes) < 1000


# ------------------------------------------------------------------------------------------------------------- geo


GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": 7, "properties": {"name": "Oslo"}, "geometry": {"type": "Point", "coordinates": [10.75, 59.91]}},
        {"type": "Feature", "properties": {"name": "equator"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]], [[0.5, 0.5], [1, 0.5], [0.5, 0.5]]]}},
        {"type": "Feature", "properties": {"k": 1},
         "geometry": {"type": "GeometryCollection", "geometries": [
             {"type": "Point", "coordinates": [1, 2, 3]},
             {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [0, 1], [0, 0]]], [[[5, 5], [6, 5], [5, 6], [5, 5]]]]}]}},
    ],
}


def test_geojson_all_shapes_parts_and_measures():
    out = adapters.decode(json.dumps(GEOJSON).encode())
    assert out.detected.format == "geojson"
    f = out.representation
    assert [x.geometry for x in f.features] == ["Point", "LineString", "Polygon", "Point", "MultiPolygon"]
    assert f.crs == "OGC:CRS84" and f.features[0].properties == {"name": "Oslo", "_id": 7}
    assert f.features[2].properties["_parts"] == (4, 3)
    assert f.features[4].properties["_polygons"] == (1, 1) and f.features[3].properties["_collection"] == 0
    assert f.features[3].coordinates.shape == (1, 3)
    lengths = measure_geo.lengths_m(f)
    one_degree = 2 * math.pi * measure_geo.EARTH_RADIUS_M / 360
    assert abs(lengths["per_feature"][0]["length_m"] - one_degree) < 1e-6
    box = measure_geo.bounding_box(f)
    assert (box["min_x"], box["max_x"], box["max_y"]) == (0.0, 10.75, 59.91)
    assert "Oslo" in f.text()


def test_geojson_hostile():
    with pytest.raises(T.Malformed):
        adapters.decode(b'{"type": "Point", "coordinates": [1]}')
    with pytest.raises(T.Malformed):
        adapters.decode(b'{"type": "Polygon", "coordinates": [1, 2]}')
    many = {"type": "MultiPoint", "coordinates": [[0, 0]] * 100}
    with pytest.raises(T.Refused):
        adapters.decode(json.dumps(many).encode(), limits=T.Limits(max_elements=50))
    nested = {"type": "GeometryCollection", "geometries": []}
    for _ in range(12):
        nested = {"type": "GeometryCollection", "geometries": [nested]}
    with pytest.raises(T.Refused):
        adapters.decode(json.dumps(nested).encode(), name="x.geojson")


GPX = b"""<?xml version="1.0"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="0" lon="0"><name>Start</name><time>2024-01-01T00:00:00Z</time></wpt>
  <trk><name>Run</name>
    <trkseg><trkpt lat="0" lon="0"><ele>10</ele><time>2024-01-01T00:00:00Z</time></trkpt>
            <trkpt lat="0" lon="1"><ele>12</ele><time>2024-01-01T01:00:00Z</time></trkpt></trkseg>
    <trkseg><trkpt lat="1" lon="1"><time>2024-01-01T02:00:00Z</time></trkpt></trkseg>
  </trk>
</gpx>"""


def test_gpx_tracks_times_and_shape():
    out = adapters.decode(GPX)
    assert out.detected.format == "gpx"
    f = out.representation
    wpt, trk = f.features
    assert wpt.geometry == "Point" and wpt.properties["name"] == "Start" and wpt.properties["_kind"] == "waypoint"
    assert trk.geometry == "MultiLineString" and trk.properties["_parts"] == (2, 1)
    assert trk.coordinates[:, :2].tolist() == [[0, 0], [1, 0], [1, 1]] and trk.coordinates[0, 2] == 10
    assert math.isnan(trk.coordinates[2, 2])
    span = measure_geo.time_span(f)
    assert span["duration_s"] == 7200.0
    one_degree = 2 * math.pi * measure_geo.EARTH_RADIUS_M / 360
    assert abs(measure_geo.lengths_m(f)["total_m"] - one_degree) < 1e-6      # the gap between segments not counted
    sh = _b2.content_shape(f)
    assert sh.kinds == ("map", "timeline") and sh.has_time and sh.has_space == 2


KML = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><NetworkLink><Link><href>https://example.invalid/more.kml</href></Link></NetworkLink>
<Folder><Placemark><name>Hut</name><TimeStamp><when>2020-05-01T10:00:00Z</when></TimeStamp>
<Point><coordinates>8.5,47.3,500</coordinates></Point></Placemark>
<Placemark><name>Lake</name><Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 1,0 1,1 0,0</coordinates></LinearRing></outerBoundaryIs>
<innerBoundaryIs><LinearRing><coordinates>0.2,0.2 0.4,0.2 0.2,0.2</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark>
<Placemark><name>Walk</name><gx:Track><when>2020-05-01T10:00:00Z</when><when>2020-05-01T10:10:00Z</when>
<gx:coord>8 47 0</gx:coord><gx:coord>8.01 47 0</gx:coord></gx:Track></Placemark></Folder></Document></kml>"""


def test_kml_and_kmz():
    out = adapters.decode(KML)
    f = out.representation
    assert out.detected.format == "kml" and [x.geometry for x in f.features] == ["Point", "Polygon", "LineString"]
    assert f.features[0].coordinates.tolist() == [[8.5, 47.3, 500.0]]
    assert f.features[1].properties["_parts"] == (4, 3)
    assert f.features[2].properties["times_s"][1] - f.features[2].properties["times_s"][0] == 600
    assert any("never fetched" in n for n in out.notes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", KML)
        z.writestr("files/icon.png", b"\x89PNG")
    out = adapters.decode(buf.getvalue())
    assert out.detected.format == "kmz" and len(out.representation.features) == 3
    assert any("other member" in n for n in out.notes)


def test_wkt_forms():
    text = (b"SRID=4326;MULTIPOLYGON (((0 0, 1 0, 0 1, 0 0)),\n ((5 5, 6 5, 5 6, 5 5)))\n"
            b"POINT ZM (1 2 3 4)\nMULTIPOINT (1 2, 3 4)\nMULTIPOINT ((1 2), (3 4))\nLINESTRING EMPTY\n"
            b"GEOMETRYCOLLECTION (POINT (1 1), LINESTRING (0 0, 1 1))")
    out = adapters.decode(text)
    f = out.representation
    assert out.detected.format == "wkt" and f.crs == "EPSG:4326"
    assert [x.geometry for x in f.features] == ["MultiPolygon", "Point", "MultiPoint", "MultiPoint", "LineString",
                                                "Point", "LineString"]
    assert f.features[1].coordinates.tolist() == [[1, 2, 3]] and any("measure" in n for n in out.notes)
    assert f.features[2].coordinates.tolist() == f.features[3].coordinates.tolist()
    with pytest.raises(T.Malformed):
        adapters.decode(b"POLYGON ((0 0, 1 0)", name="x.wkt")


def test_lengths_refused_for_projected_crs():
    f = T.Features((T.Feature("LineString", np.array([[0.0, 0.0], [3.0, 4.0]])),), "EPSG:3857")
    assert measure_geo.lengths_m(f)["total_m"] is None
