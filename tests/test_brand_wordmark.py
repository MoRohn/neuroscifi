"""The NeuroSciFi wordmark has one source (scripts/brand/build_wordmark.py): every committed copy is what the generator
writes, each copy is valid XML with an accessible name, and the brain icon stays byte-identical everywhere."""
import hashlib
import json
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "assets" / "brand"
SVG = "{http://www.w3.org/2000/svg}"

_spec = importlib.util.spec_from_file_location("build_wordmark", ROOT / "scripts" / "brand" / "build_wordmark.py")
wm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wm)

# The brain icon as it was before the wordmark was redrawn: the banner's icon group and the two icon files.
ICON_GROUP_OPEN = '<g transform="translate(250 200) scale(1.2) translate(-128 -127)">'
ICON_GROUP_SHA256 = "e3a01d3db6176fedd5685a0d2eaee81f2c804482746f1135fe3115e91d112714"
ICON_FILES_SHA256 = {
    "docs/assets/brand/neuroscifi-icon.svg": "9cec14c79eabc1d4a7ff570e827267700f37de23f784183bbd3d3ded9717ca2a",
    # Pocket's app icon is the brain logo itself (owner direction, 2026-11-23): byte-identical
    "src/neuroscifi/pocket/web/icon.svg": "9cec14c79eabc1d4a7ff570e827267700f37de23f784183bbd3d3ded9717ca2a",
}
_icons_spec = importlib.util.spec_from_file_location("build_app_icons", ROOT / "scripts" / "brand" / "build_app_icons.py")
app_icons = importlib.util.module_from_spec(_icons_spec)
_icons_spec.loader.exec_module(app_icons)
MARKED = re.compile(r"<!-- nsf-wordmark:begin.*?-->(.*?)<!-- nsf-wordmark:end -->", re.S)


def icon_group(text):
    """The banner's icon <g> through its matching </g>."""
    start = text.index(ICON_GROUP_OPEN)
    depth = 0
    for m in re.finditer(r"<(/?)g\b[^>]*?(/?)>", text[start:]):
        if m.group(1):
            depth -= 1
        elif not m.group(2):
            depth += 1
        if depth == 0:
            return text[start:start + m.end()]
    raise AssertionError("unclosed icon group")


def test_committed_outputs_are_what_the_generator_writes():
    stale = [str(p.relative_to(ROOT)) for p, text in wm.outputs().items() if p.read_text() != text]
    assert not stale, f"run python scripts/brand/build_wordmark.py; stale: {stale}"
    assert wm.main(["--check"]) == 0


def test_standalone_and_banner_svgs_are_valid_xml_with_an_accessible_name():
    for name in ("neuroscifi-wordmark-dark.svg", "neuroscifi-wordmark-light.svg", "neuroscifi-banner.svg"):
        text = (BRAND / name).read_text()
        root = ET.fromstring(text)
        assert root.tag == SVG + "svg" and root.get("role") == "img", name
        labelled = root.get("aria-labelledby", "").split()
        titles = {t.get("id"): (t.text or "") for t in root.iter(SVG + "title")}
        assert labelled and titles.get(labelled[0]) == "NeuroSciFi", name
        # GitHub renders the files as images: no scripts, no foreign content, no external references.
        for el in root.iter():
            assert el.tag not in (SVG + "script", SVG + "foreignObject"), name
            for attr in ("href", "{http://www.w3.org/1999/xlink}href"):
                assert el.get(attr) is None or el.get(attr).startswith("#"), name
        assert "<text" not in text and "@import" not in text and "@font-face" not in text, name


def test_the_banner_wordmark_uses_no_filter_and_keeps_the_tagline_subtitle_title_and_desc():
    text = (BRAND / "neuroscifi-banner.svg").read_text()
    block = MARKED.search(text).group(1)
    group = ET.fromstring(block.strip())
    assert group.get("id") == "nsf-wordmark"
    assert all(el.get("filter") is None and el.tag != SVG + "filter" for el in group.iter())
    root = ET.fromstring(text)
    ids = {el.get("id") for el in root.iter()}
    assert {"t", "d", "nsf-tagline", "nsf-subtitle", "nsf-wordmark"} <= ids
    assert "A developmental AI built around one artificial connectome" in text
    assert len(text.encode()) < 100_000


def test_the_brain_icon_is_byte_identical():
    banner = (BRAND / "neuroscifi-banner.svg").read_text()
    assert hashlib.sha256(icon_group(banner).encode()).hexdigest() == ICON_GROUP_SHA256
    for rel, digest in ICON_FILES_SHA256.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def test_the_home_screen_icons_are_the_brain_logo():
    """iPhone and iPad take apple-touch-icon.png (they ignore SVG), Android the manifest's PNGs (maskable included);
    all are rendered from the logo by scripts/brand/build_app_icons.py, and the console's favicon is the logo too."""
    assert app_icons.check() == []
    web = ROOT / "src" / "neuroscifi" / "pocket" / "web"
    manifest = json.loads((web / "manifest.webmanifest").read_text())
    icons = {(i["src"], i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
    assert {("/icon-192.png", "192x192", "any"), ("/icon-512.png", "512x512", "any"),
            ("/icon-maskable-512.png", "512x512", "maskable")} <= icons
    page = (web / "index.html").read_text()
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in page
    from neuroscifi.console import server as console
    from neuroscifi.pocket import server as bridge
    assert console.ICON == (BRAND / "neuroscifi-icon.svg").read_bytes()
    for name in ("apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        assert bridge.ASSETS["/" + name] == (name, "image/png")
    shell = (web / "sw.js").read_text()
    assert all(f'"/{name}"' in shell for name in ("apple-touch-icon.png", "icon-192.png", "icon-512.png",
                                                  "icon-maskable-512.png"))

def test_the_header_wordmarks_are_inline_named_images_that_follow_the_theme():
    css = wm.THEME_CSS.read_text()
    for page in wm.HTML_PAGES:
        blocks = MARKED.findall(page.read_text())
        assert len(blocks) == 1, page
        svg = ET.fromstring(blocks[0])
        assert svg.get("role") == "img" and svg.get("aria-label") == "NeuroSciFi" and svg.get("class") == "nsf-wm"
        # CSP style-src 'self': no inline style; the colours come from theme.css custom properties.
        assert all(el.get("style") is None and el.tag not in (SVG + "style", SVG + "script") for el in svg.iter())
        classes = {c for el in svg.iter() for c in (el.get("class") or "").split()}
        for cls in classes - {"nsf-wm"}:
            assert f".nsf-wm .{cls}{{" in css, cls
    assert '[data-theme="dark"] .nsf-wm{' in css


def test_the_palette_meets_text_contrast_on_its_backgrounds():
    # the README banner's grounds, the earlier app pages, and the shared design system's header and page (ui.css)
    backgrounds = {"dark": ("#070A1F", "#0E0F33", "#1A0F3D", "#070908", "#101413", "#121011", "#1A1719"),
                   "light": ("#F4F7F5", "#FFFFFF", "#F6F4F3")}
    for variant, palette in wm.PALETTES.items():
        for key in ("rim", "body", "nest_rim", "nest_body"):
            for colour in palette[key]:
                for bg in backgrounds[variant]:
                    assert wm.contrast(colour, bg) >= 4.5, (variant, key, colour, bg)


def test_the_nested_letters_sit_clear_of_and_inside_their_hosts():
    for cut in (wm.DISPLAY, wm.SMALL):
        report = wm.gaps(cut)
        assert min(v for k, v in report.items() if " " in k) >= 3.5, (cut.name, report)
        assert min(v for k, v in report.items() if " " not in k) >= 5, (cut.name, report)
    for ch in "SF":   # every nested outline lies within the host letter's outline box
        g, cut = wm.GLYPHS[ch], wm.DISPLAY
        host = wm.sample(wm.parse(g["caps"]))
        x0, x1 = min(p[0] for p in host) - cut.caps / 2, max(p[0] for p in host) + cut.caps / 2
        y0, y1 = min(p[1] for p in host) - cut.caps / 2, max(p[1] for p in host) + cut.caps / 2
        nested = [(p, cut.nest / 2) for p in wm.sample(wm.parse(g["nest"]))] + [(p, cut.dot / 2) for p in g["dots"]]
        assert all(x0 <= x - r and x + r <= x1 and y0 <= y - r and y + r <= y1 for (x, y), r in nested), ch
