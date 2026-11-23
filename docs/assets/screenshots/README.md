# README screenshots

Product screenshots of the owner console and Pocket, for the project README. Each still image exists as WebP (use
this one) and as a 256-colour PNG fallback; the entity clip is an animated WebP. "light" and "dark" follow the system
colour scheme the app was given.

| File (`.webp` / `.png`) | Viewport → image (px) | Caption |
|---|---|---|
| `hero-light`, `hero-dark` | composite → 1600 × 867 | The Presence entity on every device: the console's research view, Pocket on a tablet and Pocket on a phone, all reading sources for a question one of them sent. |
| `console-research-light`, `console-research-dark` | 1440 × 900 → 1600 × 1000 | Console research report: a model-written answer with citations and claim states, the knowledge gaps and the quoted evidence; the entity beside it reads sources for the next question. |
| `pocket-phone-light`, `pocket-phone-dark` | 390 × 844, touch → 780 × 1688 | Pocket on a paired phone: the entity on its stage above the research form while the Spark reads sources for the phone's question. |
| `pocket-tablet-light`, `pocket-tablet-dark` | 820 × 1180, touch → 1100 × 1583 | Pocket on a second device linked with the phone's hand-off code: the phone's question continued here, with its finished, cited report. |
| `voice-light`, `voice-dark` | 1280 × 900 → 1600 × 1125 | Console Voice view: a text-only voice session with an answered research question and a second question in progress, and the Voice check panel open. |
| `setup-console-light`, `setup-console-dark` | 1280 × 860 → 1280 × 860 | The console's Add a phone (ADR-043): code 1 (trust your Spark) and code 2 (pair this phone), waiting for the phone. |
| `setup-phone-light`, `setup-phone-dark` | 390 × 844, touch, iPhone user agent, full page → 640 px wide | The phone setup page that code 1 opens, with the iPhone steps, before the phone trusts the Spark. |
| `entity-alive-light`, `entity-alive-dark` (`.webp`, animated) | 1440 × 900, stage region → 480 px wide, 20 fps, about 14 s | The entity alive on the console's research stage: at rest it follows the pointer round it, answers each typed keystroke, then morphs through the real stages of the question it was asked. |

## How they are made

`scripts/ui/readme_screenshots.py` builds everything in one process, on 127.0.0.1 with free ports and a temporary
directory, from the end-to-end test helpers in `tests/e2e`:

- **Console**: `test_console_research.Research` (console, embedded research worker, offline `FixtureWeb` pages on
  `.example` hosts and a `CorpusProvider`). Its model endpoint is the local stand-in `tests/inference/fake_openai.py`,
  which answers deterministically from the evidence it is sent; its model id is `demo-model`, so the answer says
  "Written by demo-model". The owner accepts the proposed knowledge item in Questions, then asks a second question
  whose page reads are slowed down. That task is still reading during the capture.
- **Pocket**: `test_pocket_research_e2e.Spark` (bridge and instance with Pocket research on) with a running research
  worker and the same model stand-in. The phone pairs, asks a question and links the tablet with its hand-off code.
  The tablet opens that question's report. The phone then asks a second question, which is read slowly.
- **Voice**: the same console setup with `test_voice_e2e._ScriptedEngines` (in-process engines that report ready; no
  speech service is used) and a **text-only** session: the questions are typed, so the transcript is genuine. The
  engines' "ready" health line comes from the scripted engines.
- **Hero**: a small local HTML page lays the three captures out in plain rounded frames on the theme's page colour
  (`#F6F4F3` light, `#121011` dark) and is captured at 2400 × 1300 pixels.
- **Add a phone**: the helper of `tests/e2e/test_phone_setup_e2e.py`: a bridge with a scratch CA, its setup page and
  control socket, and a console pointed at it. The codes encode that scratch instance's addresses and a one-use code
  that expires with it.
- **Entity clip**: the console setup without the model stand-in, motion ON. The real pointer circles the figure for
  four seconds, the question is typed (55 ms per key) and asked, and seven seconds of the run follow. Frames come
  from Chromium's screencast (`Page.startScreencast`, device pixels) as they are painted, each with its real
  timestamp; ffmpeg's concat demuxer keeps those durations, crops the stage region, resamples to 20 fps and writes a
  looping animated WebP (`libwebp_anim`, quality 78, about 1 MB).

The entity's state is never scripted. It shows what the presence endpoint reports for the seeded work. For the still
images the browsers emulate reduced motion, so the entity is drawn in each state's static key pose. Captures are
taken at device scale 2 and downscaled with ImageMagick: WebP at quality 82, PNG8 with 256 colours and no
dithering.

Before each capture, the console's instance id, Pocket's hand-off code and key fingerprints are masked (text only).
A capture fails if its visible text shows a host path, the host name, a loopback address, an e-mail address or a
long hex id.

## Regenerate

```sh
PYTHONPATH=src python scripts/ui/readme_screenshots.py            # writes into docs/assets/screenshots/
PYTHONPATH=src python scripts/ui/readme_screenshots.py --out DIR --only console,voice --keep-raw RAWDIR
```

`--only` takes any of `console`, `pocket`, `hero`, `voice`, `setup` and `entity`. `hero` also captures `console` and
`pocket`. A full run takes about four minutes.

Requirements:

- Playwright for Python (the dev dependencies).
- Chromium: `NSF_CHROMIUM`, else `/usr/bin/chromium`, else Playwright's bundled Chromium.
- ImageMagick 7 (`magick`) with WebP support, for encoding. Without `magick` the script copies the raw device-scale-2
  PNG captures instead, which are much larger.
- ffmpeg with `libwebp_anim`, for the entity clip only.

Nothing outside the process is contacted: no search engine, model server or speech service.
