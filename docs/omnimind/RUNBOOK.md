# OMNIMIND runbook, for the owner (deliverable 11)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` at `09bfff6`. It applies to the branch `omnimind`
(schema 21), **which is not merged into `main`**: on an installed 0.7.0 none of these commands exists. The general
runbook is `docs/operations/RUNBOOK.md`; upgrades and rollback of the file are `docs/operations/MIGRATIONS.md`
section 2. Places fix lane F4 will change are marked "to be confirmed after fix lane F4".

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
3. Keep your own copy of the individual's file with the services stopped (the file, and its `-wal` and `-shm` files
   if they exist). After the upgrade there is also a sidecar `<name>.artifacts.sqlite` next to it.

**The way back** (MIGRATIONS.md section 2, "Rollback"): stop the services, reinstall the old software, and put back
either your own copy from step 3, or the old software's `nsf archive unpack SOURCE DESTINATION` of the archive from
step 2. Either way brings back whatever you forgot since: repeat those forget requests, and delete the sidecar of the
new-schema file instead of reusing it. Not `nsf checkpoint restore` (the old software refuses the upgraded file; the
new software's restore yields schema 21). `tests/instance/test_migrations.py::test_rollback_facts` pins the refusal
and that a restore yields the new schema; **no test runs the old build**, so the way back itself is unverified.
MIGRATIONS.md's rollback step 3 points to a copy its upgrade steps never take (code review N5); fix lane F4 corrects
that text (**to be confirmed after fix lane F4**).

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
clean child, the two would decode differently (security note N-new-4; **to be confirmed after fix lane F4**, which
refuses that case).

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
| `nsf artifact gaps --report` | what could not be read or made well, and how often |
| `nsf artifact forms`, `nsf artifact adapters`, `nsf form plan`, `nsf form learned` | what can be made; what can be read; what form a request would get; what was learned |

**Exit codes** (one table, `artifacts/outcome.py`): **0** done; **1** could not be done (an id you do not hold, a make
that could not make anything well, a reaction not recorded); **2** refused (a rule or a limit, or only part of a file
was read and you did not pass `--partial`) or a mistake in the command. The code reviewer found `make` exiting 1 for
three refusals the table calls 2 (**to be confirmed after fix lane F4**).

**What a refusal means.** Every refusal is one plain sentence and never a traceback.
- *"... is not read here"*, *"names the format: nothing in it is read"*, *"reads only the header"*: the format was
  identified and no reader for its content exists on this host. Nothing is guessed. `nsf artifact gaps` counts it.
- *a size or time limit* ("would need about 819 MiB", "took longer than the limit"): the file is too large to read
  within the limits. With `add`, a file stopped by a size limit is still kept, unread. With `make`, `--partial` uses a
  file read only in part and says which part.
- *a safety refusal* (a pickle, a file that is two formats at once, an XML document type definition, too deep a
  nesting, an archive with a member name that climbs out or is absolute, overlapping archive members, structure
  hidden past the first 64 KiB): nothing is kept.
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
MiB at the defaults; about 2.8 GiB if you raise the file limit to 1 GiB) and to 25 s; then again in the command. So a
decode costs about twice its time or more: the code reviewer measured adding a 5,000-file zip at 6.0 s (0.78 s
without the guard), a 30-megapixel PNG at 9.4 s (3.3 s), a crafted 135 KiB PNG at 13.4 s (**to be confirmed after fix
lane F4**, which measures and reduces this and states the factor). On macOS and Windows only the time limit applies
(unverified: no such host was available).

## Forget, the hold, and restore

- `nsf artifact forget ID` removes the artifact, everything made from it or contained in it, its bytes (when nothing
  else names them), its learned outcomes, and rewrites later checkpoints without it.
- **Forgetting a partner or a camera** (`nsf forget partner NAME`, `nsf forget camera NAME`, as before) also searches every kept artifact's
  label, read text, specification and bytes for the name, inside compressed and base64 layers. What it cannot find
  is stated in `store.py`: other letter case, look-alike letters, text drawn in a picture, a name split by spaces or
  table cells, encodings it does not undo.
- **A kept file the search cannot finish.** At `09bfff6` such a file counts as naming the forgotten name and is
  **removed**, even when it does not name it; the forget's result gives only a count (security review M-new-1: an
  unrelated forget removed an honest 47 MiB dataset zip and a notebook). Fix lane F4 replaces this: such a file is
  **held** (kept, never read, shown, used or related), listed by id and label in the forget's output and in `nsf
  artifact list` with the reason "could not be checked for what you asked to forget"; you end the hold with `nsf
  artifact forget ID` or `nsf artifact release ID` (your statement that it does not name what was forgotten); a
  restore replays holds; and the search gets a larger budget so that such honest files are checked. **To be confirmed
  after fix lane F4.**
- **A restore** (`nsf checkpoint restore`) gives the restored file its own sidecar, removes every artifact forgotten
  since the checkpoint was taken, and replays `nsf artifact feedback --reset`. Settings are carried. The restored file
  is at schema 21.

## The console view, and upload

The "Made and kept" view lists what is kept (20 a page), shows a made chart, document, table, sound, explorable model
or PNG picture, its "why", its relations and its check, and has Make (one at a time, about 60 s at most), feedback
buttons (recorded only when form learning records) and Forget (with a confirmation). The Presence entity stays on the
view's stage. Other pictures (JPEG, WebP, GIF, TIFF), raw meshes and figures inside DOCX or PDF files are described in
words with the export command. Upload appears only when turned on; files go through the same keeping as `nsf artifact
add`. Pocket shows no artifact. Each opening of a detail decodes the file again (**to be confirmed after fix lane F4**:
a cache per content and a bound on how many decodes run at once).

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
their pins in `docs/quality/evidence/omnimind_driver_pins.json` (null today, so every look is refused). Each look
appends a line to `docs/quality/evidence/omnimind_final_uses.jsonl` before it loads anything, writes summaries only,
and never into the checkout. The rule each look is judged by, written before them:
[../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for the withheld looks".
