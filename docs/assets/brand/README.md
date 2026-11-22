# NeuroSciFi brand assets

| File | Use |
|---|---|
| `neuroscifi-banner.svg` | README header (1280 × 400): the brain icon, the tagline, the wordmark (dark variant) and the subtitle |
| `neuroscifi-wordmark-dark.svg` | wordmark alone, for dark backgrounds |
| `neuroscifi-wordmark-light.svg` | wordmark alone, for light backgrounds |
| `neuroscifi-icon.svg` | the brain icon (unchanged by the wordmark work) |

The console and Pocket headers carry the same wordmark as inline SVG (no extra request), coloured by the custom
properties in `src/neuroscifi/presence/web/theme.css`: the light variant by default, the dark variant under
`<html data-theme="dark">`.

## Construction

The wordmark follows the owner's hand sketch: **N E U R O** in capitals on one cap height, then a large **S** that
holds a small **c** in its upper-right bay and a small **i** in its lower-left bay (S-ci), then a tall **F** whose long
top bar shelters a small **i** under its middle bar (F-i). It reads NeuroSciFi.

Every glyph is a monoline centre line with round caps and round joins, defined once in
`scripts/brand/build_wordmark.py` (font units, cap height 100, y grows downwards):

| Element | Metric |
|---|---|
| Capitals | cap tops at 0, baseline at 100; stroke 11 (display cut) or 11.5 (small cut) |
| E | middle arm at 48 (just above the middle), about two thirds of the top arm |
| U, O | round overshoot: U 1.5 and O 2 beyond the cap lines; O is an egg, widest at 55 |
| R | the leg leaves the bowl and sweeps down to 8 units below the baseline, ending in a small curl to the right |
| S | -16 to 116 (above the caps and below the baseline), 78 wide; the lower bowl is the larger |
| F | -24 to 124, the tallest glyph; top bar 83 long (twice the middle bar), middle bar at 48 |
| Nested lowercase | stroke 8.5 (display) or 9 (small); i stems 27 tall, c 28.5 tall with overshoot, i dots 11 wide |
| S-ci | the c sits under the S's top terminal, the i stands on the baseline in the lower bay; each clears the S by at least 5 units |
| F-i | the long i hangs under the end of the middle bar and stands on the F's foot |

Spacing is optical (display cut): straight to straight 19 units, open or round sides closer (E to U 12, O to S 14,
S to F 13, R to O 7 where the leg tucks under the O). `python scripts/brand/build_wordmark.py --report` prints every gap.

The strokes are drawn as a tube in three layers of the same centre line: a rim (the full stroke, the shadow edge), a
body (72 % of the stroke, offset 10 % towards the upper left) and a highlight (20 %, offset 24 %). The rim and the body
carry a two-stop tonal gradient along the strokes, lighter at the top. No filters, fonts, scripts or foreignObject: the
files render the same on GitHub, in browsers and in image viewers.

## Palette: gray matter

Warm greys with a pink, mauve and taupe cast, like cortical gray matter. The nested c and i take a rosier tone of the
same family.

| Role | Dark backgrounds | Light backgrounds |
|---|---|---|
| Rim (gradient) | `#A08C93` → `#927E86` | `#4F3F45` → `#42343A` |
| Body (gradient) | `#DCCBC8` → `#BBA3A6` | `#6F5D63` → `#57464C` |
| Highlight | `#F4EAE7` | `#8D7A80` |
| Nested rim | `#B0808D` → `#A57683` | `#6A3445` → `#5A2B3A` |
| Nested body | `#E8B9C0` → `#D29EA8` | `#9A5A6B` → `#7F4555` |
| Nested highlight | `#FBE7EA` | `#B37C89` |

Contrast (WCAG), the lowest stop of each role against the backgrounds it is used on (the highlight is inside the
stroke and never meets the background):

| Variant, background | Rim | Body | Nested rim | Nested body |
|---|---|---|---|---|
| dark on the banner's `#1A0F3D` (its lightest navy) | 4.71 | 7.55 | 4.68 | 7.80 |
| dark on the banner's `#070A1F` | 5.18 | 8.30 | 5.15 | 8.58 |
| dark on the app's `#070908` / `#101413` | 5.28 / 4.91 | 8.47 / 7.87 | 5.25 / 4.88 | 8.75 / 8.13 |
| light on Pocket `#f4f7f5` | 9.14 | 5.69 | 8.89 | 4.81 |
| light on the console header `#fff` | 9.86 | 6.14 | 9.59 | 5.19 |

`tests/test_brand_wordmark.py` keeps every rim and body stop at 4.5:1 or more.

## Clear space and minimum size

- Clear space: half the cap height on every side of the outline box (the box includes the S and F flourishes and the
  R's tail). In the banner the tagline and subtitle sit about a quarter cap height away by design; nothing else
  should come closer.
- Display cut (banner, standalone files): 64 px tall or more (cap height 43 px).
- Small cut (app headers): 24 px tall or more; the headers use 32 px, and 28 px at 480 px wide and below. Under
  24 px the nested c closes up: use the brain icon, or the name in text.
- Do not set the wordmark in a font, recolour it outside the gray-matter family, move the nested letters out of their
  hosts, or change the brain icon.

## Regenerate

```sh
python scripts/brand/build_wordmark.py           # rewrite every output from the geometry
python scripts/brand/build_wordmark.py --check   # exit 1 when an output is stale
python scripts/brand/build_wordmark.py --report  # glyph gaps and the outline bounds
.venv/bin/python -m pytest -q tests/test_brand_wordmark.py
```

The generator owns the wordmark block between the `nsf-wordmark:begin` and `nsf-wordmark:end` markers in the banner,
in `src/neuroscifi/console/web/index.html` and `src/neuroscifi/pocket/web/index.html`, and in
`src/neuroscifi/presence/web/theme.css`; it also sets the banner's tagline and subtitle offsets. Edit the geometry,
palette or layout constants in the script, never the outputs. A change to Pocket's `index.html` or `theme.css` also
needs a new shell cache name in `src/neuroscifi/pocket/web/sw.js` (and in `tests/js/offline_check.mjs`) so installed
Pockets fetch it.
