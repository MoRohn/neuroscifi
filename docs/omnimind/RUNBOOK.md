# OMNIMIND runbook, for the owner (deliverable 11)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` at `09bfff6` with fix lane F4 (`1aa7eb6`) merged. It applies to the branch `omnimind`
(schema 21), **which is not merged into `main`**: on an installed 0.7.0 none of these commands exists. The general
runbook is `docs/operations/RUNBOOK.md`; upgrades and rollback of the file are `docs/operations/MIGRATIONS.md`
section 2. Fix lane F5 (`bf3dac6`) is merged too.

`nsf` below is the command for your individual (`nsfs` on an installed service, as in the general runbook).

## Before anything: the upgrade cannot be undone by a restore

Opening your individual with this software moves its file from schema 19 to 21 (two steps: the artifact index, then
the outcomes of made forms). Each step takes milliseconds and changes no existing row, but **an older build cannot
open the file afterwards, and a checkpoint restore made by this build gives you schema 21 again**.

So, before the first command of this software touches your individual:
1. Stop every service (`sudo systemctl stop neuroscifi-pocket.service neuroscifi-console.service
   neuroscifi-checkpoint.timer`).
2. Take and verify a checkpoint with the **current** software (`nsf checkpoint create --policy cold`, then
   `nsf checkpoint verify ID`), and keep its archive file.
3. Keep your own copy of the individual's file with the services stopped, as MIGRATIONS.md section 2 step 1 now says:
   `install -d -m 700 ~/nsf-before-upgrade && cp -p INDIVIDUAL.sqlite INDIVIDUAL.artifacts.sqlite ~/nsf-before-upgrade/`
   (the sidecar exists only once a file was kept; with the services stopped there is no `-wal` file to copy).

**The way back** (MIGRATIONS.md section 2, "Rollback"): stop the services, reinstall the old software, and put back
either your own copy from step 3, or the old software's `nsf archive unpack SOURCE DESTINATION` of the archive from
step 2. Either way brings back whatever you forgot since: repeat those forget requests, and delete the sidecar of the
new-schema file instead of reusing it. Not `nsf checkpoint restore` (the old software refuses the upgraded file; the
new software's restore yields schema 21). `tests/instance/test_migrations.py::test_rollback_facts` pins the refusal
and that a restore yields the new schema; **no test runs the old build**, so the way back itself is unverified.
Until fix lane F4, MIGRATIONS.md's rollback step 3 pointed to a copy its upgrade steps never took (code review N5);
its upgrade step 1 now takes it.

## Turning things on and off

| what | default | on | off | takes effect |
|---|---|---|---|---|
| the console's "Made and kept" view | **on** | `nsf config --console-artifacts on` | `nsf config --console-artifacts off` (the view's routes answer 409 with this command) | the next call |
| upload from the console | off | `nsf config --console-artifact-upload on` | `... off` | the next call |
| learning which form you keep | off | `nsf config --form-learning record` (keeps reactions and check verdicts) or `apply` (also uses them) | `nsf config --form-learning off`; `nsf artifact feedback --reset` deletes what was learned | the next call |
| an external file reader | none approved | `nsf config --artifact-adapters NAME` (it prints "approved the file reader X from D version V") | `nsf config --artifact-adapters ""` | the next decode |
| the largest file kept or read | 64 MiB | `nsf config --artifact-max-mib N` (1 to 1024) | set it back | the next add |
| the sidecar's total | 2 GiB | `nsf config --artifact-store-max-mib N` | set it back | the next add |
| a production's foresight in the imagined store | off | the existing `imagine_scenarios` switch (with `max_imagined_rows` above 0) | off | the next make |

Nothing else runs on its own: every command below is one you type. The quiet switch has nothing to hold.

**Approving an external reader.** Only a package already installed can be approved, and only by you. The approval is
stored as `NAME=DISTRIBUTION==VERSION`; if the package is upgraded or replaced, `nsf artifact adapters` lists it as
"approve it again" and it is not loaded. An approval stored by name only, before fix lane F3, loads nothing until you
approve it again. An approved reader still runs inside the guard. If it loads in your shell but not in the guard's
clean child, the file is refused ("external_unguarded") and kept unread, never decoded by an unguarded reader
(security note N-new-4, fixed by fix lane F4).

## The commands, and what their answers mean

| you type | you get |
|---|---|
| `nsf inspect FILE` | what the file is and what can be read from it; nothing is kept |
| `nsf artifact add FILE...` | the file kept beside your individual, with what could be read |
| `nsf artifact list`, `show ID [--why]`, `facts ID` | what is kept; one artifact in detail; measurements with how each was computed |
| `nsf artifact make FILE\|ID... -r "what you want to know"` | a made result (or an answer in words when words serve), checked and kept with why |
| `nsf artifact relate A B --question "does the total match?"` | what one measures against what the other states; recorded as the system's claim, not evidence |
| `nsf artifact export ID PATH` | the bytes in a new file only you can read (never overwrites) |
| `nsf artifact forget ID` | gone, with everything made from it |
| `nsf artifact release ID` | a file a forget held is usable again (your statement that it does not name what was forgotten) |
| `nsf artifact gaps --report` | what could not be read or made well, and how often |
| `nsf artifact forms`, `nsf artifact adapters`, `nsf form plan`, `nsf form learned` | what can be made; what can be read; what form a request would get; what was learned |

**Exit codes** (one table, `artifacts/outcome.py`): **0** done; **1** could not be done (an id you do not hold, a make
that could not make anything well, a reaction not recorded); **2** refused (a rule or a limit, or only part of a file
was read and you did not pass `--partial`) or a mistake in the command. `make` and `remake` exit through the product's
outcome table too (fix lane F5): a part used without `--partial`, a limit or a held file gives 2; a format nothing here
reads gives 1. Plain output carries no rule
names or raw timings (time limits print as "1 s", not "0.99998 s"; fix lane F5).

**What a refusal means.** Every refusal is one plain sentence and never a traceback.
- *"... is not read here"*, *"names the format: nothing in it is read"*, *"reads only the header"*: the format was
  identified and no reader for its content exists on this host. Nothing is guessed. `nsf artifact gaps` counts it.
- *a size or time limit* ("would need about 819 MiB", "took longer than the limit"): the file is too large to read
  within the limits. With `add`, a file stopped by a size limit is still kept, unread. With `make`, `--partial` uses a
  file read only in part, and the summary, the "why" and the made thing's description all say which part, in the
  same words ("Only part of weather.csv was used (--partial): ..."); a check line then says the values were read back
  from "the part that was read", never "100% of the source values". A very large archive is drawn as its folders with
  their file counts (`--partial` on a 5,000-file zip makes a 16 KiB tree saying 4,096 of 5,000 members were read).
  A reduced solid says its points are a reduction of its vertices (fix lane F5).
- *a safety refusal* (a pickle, a file that is two formats at once, an XML document type definition or entity,
  XInclude, a bomb, a lying size, too deep a nesting, an archive with a member name that climbs out or is absolute or
  a link, overlapping archive members, damage, structure hidden past the first 64 KiB): nothing is kept. One such part
  refuses the whole file, honest parts beside it included.
- *a note, not a refusal*: a reference to something outside the file (never followed; what needs it is "cannot") or
  embedded active content (a macro project, a PDF script or launch action, an embedded file: never run).

**What refuses a whole file, and what does not** (ADR-080 decision 4; `artifacts/outcome.py`, rules 1 to 3; lane
FX2):
1. **Parser-level attacks refuse the whole file**, even when the attack sits in one part beside honest parts: entities,
   document type definitions, XInclude, decompression bombs, lying sizes, overlapping members, unsafe member names and
   links, damage, nesting past the limit, and text addressed to an AI agent (injection).
2. **A reference to something outside the file is never followed and never refuses by itself.** It is named in a note;
   whatever needs it is "cannot": a glTF whose geometry sits in an outside buffer is read as its header only (counts,
   buffers by index); an SVG linking an outside image is read, with its note. OOXML relationships to outside targets
   are counted by kind, never by address.
3. **Embedded active content is never run and never refuses by itself**: a macro project, a PDF script, auto-run or
   launch action, an embedded file are named in a note.
Stated limits: a spreadsheet cell with no cached value is "cannot" only in the note (in the table it is an empty cell);
a PDF action inside a compressed stream other than the catalog is not found (without `pypdf` a PDF is header facts
only); a text note that quotes the ZIP signature bytes is left unknown; ODF outside links are not counted. Lane FX's own
refusal corpus now reads 165 refused, 3 answered of 168: the 3 are the SVG-with-an-outside-image class, answered with
its note as ruled (its truth file still says "refused").
- *flagged*: the file's own words hold passages addressed to an AI agent. `add` keeps it marked flagged; nothing is
  made from it; its words are never offered to a model.
- *"guard unavailable"*: the limited child process could not start; the file is kept and not read. Check memory and
  try again.

## Limits, and the guard's cost

Per decode: input 64 MiB, 256 MiB decoded, 4,096 members, nesting depth 4, ratio 200 per stream, 5,000,000 elements,
20 s. Per production: 60 s and 16 MiB by default (`--seconds`, `--bytes`), at most 4 alternatives, at most 6 source
pairs related. Large sources are made from a stated reduction (pictures over 4 MP, meshes over 20,000 vertices, lines
over 4,000 points, scatters over 5,000 points, more than 24 children per tree node), and the made thing says so.

Every decode of your file runs first in a child process limited to 256 MiB + twice the file + 512 MiB of memory (896
MiB at the defaults; about 2.8 GiB if you raise the file limit to 1 GiB) and to 25 s. The cost, as fix lane F4
measured it (`docs/quality/evidence/omnimind_f4.json`): the child starts in about 0.1 s; `nsf artifact add` decodes
once, in the child; `make`, `facts`, `relate` and the console's detail view decode twice (in the child, then in the
command) plus about 0.1 s. Adding a 5,000-file zip took 2.97 s (5.81 s before that change; most of it is the screen
for instructions aimed at an AI, run on each member), a crafted 135 KiB PNG 6.86 s (13.19 s), a 30-megapixel PNG
1.02 s. The second, in-command decode of `make`, `facts` and `relate` can use as much memory as the child was allowed;
the help of `--artifact-max-mib` says so: 896 MiB at 64 MiB, about 2.8 GiB at 1024 MiB, and 344 MiB measured to make
a chart from a 60 MiB CSV (fix lane F5). On macOS and Windows
only the time limit applies (unverified: no such host was available).

## Forget, the hold, and restore

- `nsf artifact forget ID` removes the artifact, everything made from it or contained in it, its bytes (when nothing
  else names them), its learned outcomes, and rewrites later checkpoints without it.
- **Forgetting a partner or a camera** (`nsf forget partner NAME`, `nsf forget camera NAME`, as before) also searches every kept artifact's
  label, read text, specification and bytes for the name, inside compressed and base64 layers. What it cannot find
  is stated in `store.py`: other letter case, look-alike letters, text drawn in a picture, a name split by spaces or
  table cells, encodings it does not undo.
- **The search's own budget** (fix lane F4): 1 GiB decompressed per kept file, 128 MiB per stream, 16 layers deep,
  65,536 streams per layer. An honest 47 MiB dataset zip and a notebook are checked to the end (the security
  reviewer's reproduction: neither held nor removed; the forget took 6.9 s).
- **A kept file the search still cannot finish is held, not deleted** (until fix lane F4 it was removed even when it
  did not name the forgotten name, with only a count: security review M-new-1). A held file keeps its bytes but is
  never read, exported, made from, related or offered to a model. The forget's output lists each held file by id and
  label and says what to do next; `nsf artifact list`, `show` and the console say "held: it could not be checked for
  what you asked to forget". End the hold with `nsf artifact forget ID`, or with `nsf artifact release ID` (your
  statement that it does not name what was forgotten; a log row records it). A hold is a log row, so a restore or a
  checkpoint rewrite replays holds and releases.
- **A restore** (`nsf checkpoint restore`) gives the restored file its own sidecar, removes every artifact forgotten
  since the checkpoint was taken, and replays `nsf artifact feedback --reset`. Settings are carried. The restored file
  is at schema 21.

## The console view, and upload

The "Made and kept" view lists what is kept (20 a page), shows a made chart, document, table, sound, explorable model
or PNG picture, its "why", its relations and its check, and has Make (one at a time, about 60 s at most), feedback
buttons (recorded only when form learning records) and Forget (with a confirmation). The Presence entity stays on the
view's stage. Other pictures (JPEG, WebP, GIF, TIFF), raw meshes and figures inside DOCX or PDF files are described in
words with the export command. Upload appears only when turned on; files go through the same keeping as `nsf artifact
add`. Pocket shows no artifact. Opening a detail reuses the guard's verdict for the same content, one detail decode
runs at a time (a second waits up to 60 s, then is told the view is busy), and a held file is shown without any
decode.

## Models

OMNIMIND calls no model and needs none: nothing to load, update or roll back. No model on this host sees or hears.
Adding one is your decision ([IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md), "The owner's decisions").

## Monitoring

- `nsf artifact gaps --report`: formats nothing read and how often, made things whose check failed, repairs that
  recur, answers in words where another form carried more, artifacts linked as contradicting each other.
- `nsf artifact list`: what is kept, made ones marked "made by the system, not evidence".
- The sidecar's size against `artifact_store_max_bytes`: an add over the cap is refused by name.
- Logs carry ids, sizes, hashes and kinds, never a file's content.

## When something fails

| what you see | what to do |
|---|---|
| a refusal sentence | read it: it names the limit or rule; raise a limit only if you trust the file (memory grows with it) |
| "could not make this well" | nothing was stored; the sentence names what could not be made and why; try `--form` with another form, `--partial`, or a larger `--seconds` |
| a made thing that looks wrong | `nsf artifact show ID --why` (what was read, planned, made, checked, repaired); `nsf artifact remake ID --form F`; `nsf artifact feedback ID dismissed` if learning records |
| the console view answers 409 | it is switched off: `nsf config --console-artifacts on` |
| the sidecar was damaged | it is renamed aside (`<name>.corrupt-<time>`) and an empty one takes its place; `nsf artifact list` shows which bytes are missing |
| anything to undo | settings: switch off; a kept file: `nsf artifact forget ID`; the whole branch: the way back at the top of this page |

## The withheld looks (for the main session, recorded here)

The last measure of OMNIMIND is one look per mechanism at a withheld examination no lane tuned against:
`scripts/bench_omni.py final --family F --subject artifacts --note TEXT --out FILE` (seven families) and
`scripts/bench_omni_forms.py final --family forms|cross|learn --subject ... --note TEXT --out FILE`. A look is refused
unless the tree is clean and committed, the withheld files' SHA-256 equal their pins, and both drivers' SHA-256 equal
their pins in `docs/quality/evidence/omnimind_driver_pins.json` (pinned in `55f89be`; the looks were taken there, once each). Each look
appends a line to `docs/quality/evidence/omnimind_final_uses.jsonl` before it loads anything, writes summaries only,
and never into the checkout. The rule each look is judged by, written before them:
[../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for the withheld looks".
