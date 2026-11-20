# Integration status

Updated at every wave boundary. Evidence is the integrator's own re-run, not an agent's report.

| Seam | Producer → consumer | State | Evidence |
|---|---|---|---|
| Package and CLI registration | W1 → all | Integrated | `nsf --help`; check.sh green at `392ea0f` |
| Instance schema and store | architect → W2-A/B/C | Integrated | `nsf init/birth/regulation` smoke; immutability trigger fires (`ef95e48`) |
| `rules.register` at birth → graph gate | architect → W2-B | Contracted | — |
| evidence → beliefs → executive | W2-A internal | In progress | — |
| governance → `graphs.invalidate_dependents` | W2-C → W2-B | Contracted | — |
| checkpoints → `archive.convert` | W2-C → prototype primitive | In progress | — |
| Pocket web ↔ bridge (mute/unmute) | W2-D | In progress | — |
| console → instance service | W3 | Not started | — |
| acceptance → instance service | W3 | Not started | — |
