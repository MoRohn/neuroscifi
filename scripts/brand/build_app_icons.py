#!/usr/bin/env python3
"""Pocket's home-screen icons, rendered from the brain logo (docs/assets/brand/neuroscifi-icon.svg), unchanged.

    python scripts/brand/build_app_icons.py [--check]

iPhone and iPad ignore SVG icons and use apple-touch-icon.png (a full square: iOS rounds the corners itself);
Android uses the manifest's PNGs: 192 and 512 pixels as drawn (rounded square, transparent corners) and a 512-pixel
maskable icon (full square). The brain already lies inside the maskable safe zone (a circle of 40 % of the size
around the centre; its furthest stroke is at 36 %, measured on the 180-pixel render), so every launcher shape keeps
all of it without shrinking the logo.
The logo is drawn by Chromium in its designed still pose (prefers-reduced-motion: the logo's own CSS stops its
pulse animation), on the logo's own background gradient where a full square is needed. src/neuroscifi/pocket/web/
icon.svg is a byte-identical copy of the logo (the favicon, notifications and the phone setup page use it).
--check: the copy is identical and every PNG exists at its size (rendering is not byte-stable across Chromium
versions, so pixels are not compared).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "docs" / "assets" / "brand" / "neuroscifi-icon.svg"
WEB = ROOT / "src" / "neuroscifi" / "pocket" / "web"
# (file, size, full square, artwork scale): the logo's own background is #0B1030 -> #1B1150 (top left to bottom right)
ICONS = (("apple-touch-icon.png", 180, True, 1.0), ("icon-192.png", 192, False, 1.0), ("icon-512.png", 512, False, 1.0),
         ("icon-maskable-512.png", 512, True, 1.0))
BACKGROUND = "linear-gradient(135deg, #0B1030 0%, #1B1150 100%)"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def check() -> list[str]:
    problems = []
    if (WEB / "icon.svg").read_bytes() != LOGO.read_bytes():
        problems.append("src/neuroscifi/pocket/web/icon.svg is not the brain logo")
    for name, size, _, _ in ICONS:
        path = WEB / name
        try:
            if png_size(path) != (size, size):
                problems.append(f"{name} is {png_size(path)}, not {size}x{size}")
        except (OSError, ValueError) as error:
            problems.append(str(error))
    return problems


def build():
    from playwright.sync_api import sync_playwright
    import base64
    shutil.copyfile(LOGO, WEB / "icon.svg")
    uri = "data:image/svg+xml;base64," + base64.b64encode(LOGO.read_bytes()).decode()   # the same bytes, inline
    exe = os.environ.get("NSF_CHROMIUM") or ("/usr/bin/chromium" if Path("/usr/bin/chromium").exists() else None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe, channel=None if exe else "chromium")
        try:
            for name, size, square, scale in ICONS:
                page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1,
                                        reduced_motion="reduce")
                art = size * scale
                ground = f"background:{BACKGROUND};" if square else "background:transparent;"
                page.set_content(f'<html><body style="margin:0;width:{size}px;height:{size}px;{ground}'
                                 f'display:grid;place-items:center"><img src="{uri}" width="{art}" height="{art}" '
                                 f'style="display:block"></body></html>')
                page.wait_for_function("() => [...document.images].every(i => i.complete && i.naturalWidth > 0)")
                page.screenshot(path=str(WEB / name), omit_background=not square,
                                clip={"x": 0, "y": 0, "width": size, "height": size})
                page.close()
                print(f"  {name}: {size}x{size}")
        finally:
            browser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="verify the copy and the PNG sizes; write nothing")
    a = ap.parse_args()
    if not a.check:
        build()
    problems = check()
    for p in problems:
        print(f"app icons: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
