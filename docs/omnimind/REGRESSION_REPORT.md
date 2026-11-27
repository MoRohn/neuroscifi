# Preservation of existing behaviour (deliverable 9: "Complete Regression Report")

Status: 2026-11-27, written on `omni-docs2` from `omnimind` at `09bfff6`, with fix lane F4 (`1aa7eb6`) merged. This page collects what each lane and each
reviewer measured about the system that existed before OMNIMIND: the offline research digest, the sentence guard's
differential, the test suites, the acceptance scenarios, the two-account check and the browser suite, each with its
commit and counts. **The final validation at the final commit has not been run yet**: it is the main session's, after
fix lane F4 and these documents are merged (placeholder at the end). Until it is run, "existing functionality is
preserved" is **unverified** at the final commit; the figures below hold at the commits named.

Sources: the lane reports (`lane-*.md`) and review reports in the main session's scratch folder, and the main
session's log (`.omc/handoffs/omnimind.md`); none of these is tracked by git. The committed reference for the digest is
`docs/quality/evidence/offline_deterministic_aarch64.json`.

## The first screen

- **Nothing existing changed at the defaults, by every check run so far**: the offline digest is byte-identical in
  both regimes, the guard differential reports 0 different, `nsf acceptance --all` passes 7 of 7, the two-account
  check passes 56 of 56, and the default import path of the new package needs only numpy. Last checked by the code
  reviewer's second pass at `09bfff6` and by fix lane F4 at `1aa7eb6`.
- **Deliberate changes to existing behaviour** (each a fix, listed below): the audit's defects closed by lane S, the
  console heat map's colours (now gray, the owner's palette rule), the gateway's handling of dict content and tool-call
  arguments, the bound on `nsf archive unpack`, and a new console view that is on by default.
- **Not run anywhere in OMNIMIND**: CI (it needs a push, which is the owner's decision; the code reviewer estimates
  the added test time at about 2.3 minutes locally and says the check job fits its 40-minute cap only if CI is no
  more than about 2.2 times slower than this host); x86_64; macOS and Windows (the decode guard's memory limit is
  Linux-only there).
- **Flaky tests seen under load**: four, each passed on rerun or alone, none in code OMNIMIND touched, and one e2e
  failure in fix lane F4 that did not recur but whose name was not captured (table below).

## What each lane and reviewer measured

| when | commit | non-e2e suite | browser (e2e) | digest, both regimes | guard differential | acceptance | two-account |
|---|---|---|---|---|---|---|---|
| lane B2 | `a48bdf0` | 6,801 passed, 0 failed (chunks) | not run | not stated | not stated | not stated | not run |
| integration lane, items 1-8 | `3af83ef` | 7,638 passed + 1 voice timing failure that passed on rerun | G2 explorable 8 of 8 | same | 0 different | 7 of 7 | not run |
| merge of `main` with Wave 4 (lane main) | `1a00f50` | 7,726 passed, 23 skipped, 0 failed | 22 passed (the rest not run) | same | not stated | not stated | not run |
| integration lane, final | `4604f38` (`c1f5f2f`) | 8,025 passed, 0 failed | G2 explorable 8 of 8 | same | 0 different | 7 of 7 | not run |
| integration lane 2 | `38f80a7` / `505b55b` | 8,205 passed, 23 skipped, +1 setup error that passed on two reruns | G2 explorable 8 of 8 | `68731d72...` both | 1,937 compared, 0 different | 7 of 7 | 55 of 55, then 56 of 56 with the artifacts line |
| lane U (console view) | `841c3a0` | 8,230 passed, 23 skipped, 0 failed | all of `tests/e2e`: 236 run, 235 passed, 1 skipped (+1 flaky failure that passed on rerun) | `68731d72...` both | 1,937 compared, 0 different | not stated | 56 of 56 |
| fix lane F1 | `b4798aa` | 8,243 passed in chunks, +1 failure fixed in the same lane (an adapter UAT reusing a name; then 41 of 41) | G2 explorable 8 of 8 | `68731d72...` both, 0 differing leaves | 0 different | 7 of 7 | 56 of 56 |
| fix lane F1, after merging `omnimind` | `bb113ab` | chunks: 548 + 1,073 + 2,485 + 1,735 + 1,527 + 946 passed; 2 failures: a race in lane U's test (fixed) and `test_follow_restart` (passed alone 2 of 2; `tests/voice` 208 of 208) | console artifacts + G2: 19 passed | `68731d72...` both, 0 leaves | 0 different | 7 of 7 | 56 of 56 |
| fix lane F3 | `a0c7491` | 8,353 passed in chunks, +1 failure (the same console race, present at `29aeee6` too; fixed, then 6 of 6 runs) | all of `tests/e2e` by file group: 42 + 49 + 48 + 62 + 27 passed (1 skipped), `test_ui_matrix.py` 7 of 7 on one run | `68731d72...` both | 1,937 compared, 0 different | 7 of 7 | 56 of 56 |
| main session, merged tree | `09bfff6` | artifacts + console + guards + all acceptance: 2,224 passed, 4 skipped (not the whole suite) | not run | not run | not run | not run | not run |
| code review, second pass | `09bfff6` | `tests/artifacts` 1,526 (80 s); `tests/console` (63 s against 49 s on `main`); `tests/test_no_home.py` 3 passed | not run | 0 differing leaves, both | 1,937 compared, 0 different (232 stricter, 17 loosened, as before) | not run | 56 of 56 |
| measurement review, second pass | `09bfff6` | benchmark tests 160 passed | not run | not run | not run | not run | not run |
| fix lane F4 | `1aa7eb6` | chunks: acceptance 580 (4 skipped; withheld-cases and final run as tests), instance 1,073, artifacts 1,588, console 134 (with the guard tests), presence + inference + voice + data 1,620 (9 skipped), uat 246 + 1 failure (`test_artifact_adapters`, adapted in `1aa7eb6`; `test_uat_rollback.py` then 42 passed; the whole uat chunk not re-run), top-level 740, webcortex 2,485 | all of `tests/e2e` in 4 chunks: 73 + 60 + 49 passed, last chunk 52 passed, 1 skipped, 1 failure that did not recur on a re-run (its name not captured: **unverified as a flake**) | 4,116 leaves, 0 differing, both | 1,937 compared, 0 different | 7 of 7 | 56 of 56 |

`scripts/reproduce.py`: 8 of 8 verified at every lane that ran it (integration lanes, U, F1, F3). `node` on
`tests/js/*.mjs`: 8 of 8 at the integration lanes, U, F1 (merged) and F3.

Also measured by the code reviewer at `09bfff6`: the 91 modules of the artifacts package and `neuroscifi.cli` import
only numpy beyond the standard library; with `console.server`, `console.artifacts` and `presence.aggregator` also
zstandard; both are default dependencies.

## Deliberate changes to existing behaviour

| change | lane | at the defaults | evidence |
|---|---|---|---|
| The gateway refuses message parts it cannot count and screen (image, audio, malformed parts) | S (`feaed2a`) | honest text unchanged | `tests/inference/test_inference_gateway.py`; ADR-070 |
| `.npy` / `.npz` headers and zip directories checked before numpy allocates; plain files only, bounded reads, named errors instead of tracebacks | S (`9129457`) | honest files give byte-identical outputs | `tests/test_input_files.py` |
| Forget reaches imagined records that name a forgotten root, encoded values included | S (`38079c4`) | nothing removed when nothing names the root | `tests/instance/test_erasure.py` |
| Voice engines: a port held by another account's process is not used; reply sizes capped | S (`6d6c10c`) | unchanged for this deployment's own services | `tests/voice/test_http_engines.py` |
| Web setting changes leave one audit row each | S (`950beaf`) | new `deletion_log` rows of kind `web_setting` | ADR-070 |
| An honest `savez_compressed` of constant data is accepted (lane S's ratio rule removed) | F1 (`aac5bca`) | honest files no longer refused | `test_input_files.py::test_an_honest_npz_of_constant_data_is_not_refused` |
| The gateway refuses dict content and privacy-screens tool-call arguments | F1 (`aac5bca`) | text content unchanged | `test_inference_gateway.py::test_dict_content_is_refused_and_tool_call_arguments_are_screened` |
| `nsf archive unpack` stops at 16 GiB of output, checked before each block is decoded; nothing is written | F3 (`30fe2f9`) | honest archives unchanged | `tests/test_files_cli.py` |
| The console's heat map is a gray ramp with a slash on negative cells (was white, blue and rust) | U (`51c5a5d`) | an existing view looks different | `tests/e2e/test_console.py` (assertion deliberately updated), `tests/console/test_palette_rule.py` |
| The console shows a new view, "Made and kept" (`console_artifacts`, default **on**); existing routes and the security headers unchanged to the character | U | a new view at the defaults | `test_console_artifacts.py::test_every_route_needs_the_session_token_and_sends_the_unchanged_headers`; ADR-079 |
| Schema 19 to 21 (artifact tables; `form_outcomes`): forward-only; an upgraded file cannot be opened by an older build | A, L | the individual's file is upgraded when opened | `docs/operations/MIGRATIONS.md` §2; `test_migrations.py::test_rollback_facts` |

Everything else OMNIMIND added is reached only through new owner commands (`nsf inspect`, `nsf artifact ...`, `nsf
form ...`) or settings that are off by default.

## Flaky tests seen under load

| test | seen | rerun | touched by OMNIMIND |
|---|---|---|---|
| `tests/voice/...test_follow_restart` (voice timing) | integration lane (`3af83ef`), F1 merged (`bb113ab`) | passed alone (2 of 2); `tests/voice` 208 of 208 | no |
| `tests/test_phone_setup.py::test_the_setup_page_serves_its_files_and_the_certificate_only` (setup error) | integration lane 2 | the file alone 16 passed; again on `505b55b` | no |
| `tests/e2e/test_console_autonomy_e2e.py::test_a_notification_is_claimed_only_with_a_delivered_question` | lane U | passed alone; the file 8 of 8 twice | no |
| `tests/e2e/test_ui_matrix.py::test_pocket_matrix[dark]` (geometry), once `console overview 320 light` | F3 | fails 1 of 3 at the base `29aeee6` too, 2 of 4 at F3 | no |
| `tests/console/test_console_artifacts.py::test_make_is_bounded_and_one_at_a_time` | F1, F3 | a race in lane U's own test: fixed (`bb113ab`, `a0c7491`) | yes (fixed) |
| one test of the e2e chunk with pocket UAT, phone setup and voice (name not captured) | F4 | the same chunk re-run: no failure | unknown (**unverified as a flake**) |

## Final validation at the final commit (placeholder)

To be filled by the main session after fix lane F4 and this documentation are merged and the drivers are pinned.
**Not yet run.**

| check | command | result |
|---|---|---|
| lint | `ruff check .` | not yet run |
| the whole non-e2e suite | `python -m pytest -q -p no:cacheprovider --ignore=tests/e2e` (or chunks) | not yet run |
| JavaScript | `node tests/js/*.mjs` | not yet run |
| browser suite | `bash scripts/e2e.sh` | not yet run |
| reproduction | `python scripts/reproduce.py` | not yet run |
| sentence guard | `python scripts/guard_differential.py compare` (must be 0 different) | not yet run |
| offline digest | `python scripts/bench_wave1.py offline --regime defaults` and `--regime all_off` (must equal `offline_deterministic_aarch64.json`) | not yet run |
| acceptance | `nsf acceptance --all` | not yet run |
| two accounts | `python scripts/two_account_check.py` | not yet run |
| OMNIMIND development, every family and subject | `scripts/bench_omni.py run --split development --subject artifacts`; `scripts/bench_omni_forms.py run --split development` for forms, cross, learn | not yet run (must reproduce 512 / 12 / 0 / 0; 107 good / 0 bad / 0 violations; 72 of 72; 0.8158 / 0.7617 against 0.5871 / 0.5842) |
| CI check job | needs a push (the owner's decision) | not run |

Fix lane F4 ran this list on `1aa7eb6` (its row in the table above): reproduce 8 of 8, node 8 of 8, and development
unchanged in every family and subject (512 / 12 / 0 / 0; forms 107 good, 0 bad, 0 violations, `FORMS_UNMAPPED` empty;
cross 72 of 72; learn 0.8158 / 0.7617 against 0.5871 / 0.5842). The final-commit run above is still the main
session's.
