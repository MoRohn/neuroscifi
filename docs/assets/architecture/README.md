# Architecture diagram

`neuroscifi-architecture-light.svg` and `neuroscifi-architecture-dark.svg` are the hand-authored system diagram for the
top-level README. Both files have the same geometry. Only the `<style>` block differs, and that block holds every colour.

## Embedding

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture/neuroscifi-architecture-dark.svg">
  <img src="docs/assets/architecture/neuroscifi-architecture-light.svg" width="100%"
       alt="NeuroSciFi architecture: clients, the DGX Spark with the orchestrator, Web Cortex, inference gateway, the individual and loopback services, and the external services that receive only screened queries, page fetches and empty pushes">
</picture>
```

## Editing

- **Keep the two files in sync.** Make each geometry or text change in both files. The two files must differ only in the
  `<style>` block, and this check prints nothing when they do:

  ```sh
  cd docs/assets/architecture
  diff <(sed '/<style>/,/<\/style>/d' neuroscifi-architecture-light.svg) \
       <(sed '/<style>/,/<\/style>/d' neuroscifi-architecture-dark.svg)
  ```

- **Structure.** Every component is a `<g>` with an id, such as `web-cortex`, `wc-verify`, `inference-gateway`,
  `individual`, `svc-vllm` or `ext-brave`. A card is one `<rect>` followed by its `<text>` lines.
- **Styling.** Styling uses classes only. Flow lines are `f-q` (question and research), `f-a` (guarded answer),
  `f-o` (owner review), `f-e` (leaves the host), `f-l` (loopback call) and `f-n` (client link). Each flow has a matching
  arrowhead marker (`ah-q`, `ah-a`, and so on).
- **Layout grid.** The viewBox is 1200 × 870. There are four layers: clients at x 24–174, the Spark host at x 190–948,
  the egress trunk at x 960 and External at x 972–1176. The Web Cortex chips sit in columns at x 398, 582 and 765.
- **Text.** Text is at least 12 px and uses the system-ui stack. There are no web fonts, scripts, `foreignObject` or
  external references. Keep about 10 % horizontal slack in each card, because other platforms' system fonts are wider
  than Liberation Sans.
- **Contrast.** Text meets WCAG AA in both palettes. The lowest light-theme ratio is 4.8:1 (green on the Spark tint).
- **Names.** Component names follow the docs: [SYSTEM_ARCHITECTURE](../../architecture/SYSTEM_ARCHITECTURE.md),
  [COGNITIVE_INFERENCE](../../architecture/COGNITIVE_INFERENCE.md), [RESOURCE_SCHEDULING](../../architecture/RESOURCE_SCHEDULING.md),
  [Web Cortex](../../web-cortex/ARCHITECTURE.md), [voice bridge](../../voice/COGNITIVE_VOICE_BRIDGE.md),
  [Presence](../../presence/PRESENCE_CORTEX_ARCHITECTURE.md), [Pocket](../../../mobile/README.md) and ADR-034 to ADR-042.
  When a component is renamed there, rename it here as well.

## Checking a change

```sh
xmllint --noout docs/assets/architecture/*.svg
chromium --headless --hide-scrollbars --window-size=1200,870 \
  --screenshot=/tmp/arch-light.png docs/assets/architecture/neuroscifi-architecture-light.svg
```

Look at the PNG for clipped text, overlaps and misaligned arrows, and check the dark file too. To see it at the
README's column width, place the SVG in an `<img width="880">` on a `#ffffff` or `#0d1117` page.
