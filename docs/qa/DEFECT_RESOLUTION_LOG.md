# Defect resolution log

Every defect found by user-acceptance testing of the integrated product, with its root cause, correction, regression
test and verification. Discovery ran on real processes (`nsf console`, `nsf pocket serve`, `nsf web work`, `nsf web mcp`,
the CLI) with a real Chromium browser and, for research, the live internet. Raw evidence (scripts, logs, screenshots,
JSON reports) is in the session scratchpad: `uat/` (RC1 discovery), `retest/r1..r4/` (retests), `l2/`, `l5/`, `l6/`,
`l4shots/` (fixer repros). Commits: RC1 `64da95b` → RC2 `cce5134` → RC3 `d7e1bee` → `8054a7f` (0.6.0, final retest) → `4eeaed1` → Final `abcd303`.

**Verification column.** `RT-RC2` = re-executed by an independent retester on RC2 and passed. `RT-final` = re-executed
by the independent final retester (R4) on `8054a7f` and passed. `Fixer live` = verified live by the fixing lane only (not
independent). Every fix also has a regression test that fails on the commit before the fix (checked by running the new
tests against an export of that commit).

Severity: C critical · H high · M medium · L low.

## Pocket (real bridge, Chromium phone/tablet/desktop)

| ID | Sev | Defect (expected → actual) | Root cause | Fix | Regression test | Verification |
|---|---|---|---|---|---|---|
| UAT-P-01 | H | Double-tap "Continued" on a phone kept the episode → **deleted it** while saying "Outcome recorded" | Layout collapsed ~330 px after the first tap; an unconfirmed "Forget here" slid under the finger | Stable layout until the next action; one tap per prediction; Forget ignores taps for 0.7 s and asks for confirmation | `tests/e2e/test_pocket_uat.py::test_double_tap_outcome_keeps_the_episode[*]`, `test_forget_here_needs_confirmation` | RT-RC2 (3 viewports × 3 outcomes) |
| UAT-P-02 | M | "Forget this pairing" → vault restored the revoked credential | Unpair did not remove the vault's `auth` entry; restore did not validate | Vault copy removed; restored pairings checked with the Spark first | `test_forget_pairing_also_removes_the_vault_copy`, `test_restored_credential_is_validated_before_paired` | RT-RC2 |
| UAT-P-03 | M | Pairing erased on the Spark → generic failures, header "Paired" | 403 not distinguished from other failures | "Your Spark no longer accepts this pairing. Pair again." | `test_revoked_pairing_is_explained[*]` | RT-RC2 |
| UAT-P-04 | M | Focus fell to body after keyboard actions (WCAG 2.4.3) | Focused control disabled/hidden | Focus moved to the resulting status/next control | `test_focus_after_keyboard_actions` | RT-RC2 |
| UAT-P-05 | L | Double-click mute sent two requests, wrong count | No in-flight guard | Guard + correct count | `test_mute_double_click` | RT-RC2 |
| UAT-P-06 | L | Goal select showed a refused goal | Change handler did not revert | Revert on refusal | `test_goal_select_reverts_while_prediction_pending` | RT-RC2 |
| Pocket UX 1–12 | L–M | Generic pairing errors, no reachability state, unexplained "insufficient evidence", swallowed image errors, vague passphrase message, wrong control named, double dialogs, small checkboxes, tablet label layout | Copy and guards missing | Distinct messages and guards; 24 px checkboxes | `test_pairing_errors_are_distinct`, `test_reachability_in_the_header`, `test_insufficient_evidence_is_explained`, `test_image_errors_are_specific`, `test_vault_hints`, `test_double_tap_share_episode_and_link`, `test_targets_and_labels` | RT-RC2 |
| UAT-S-01 | M | 32 slow LAN clients locked phones out | Global 32-connection cap; per-read timeout only | 10 s whole-request deadline from accept (incl. TLS); 8 connections per address; eviction of the oldest waiting connection | `tests/test_mobile_http.py::test_slow_clients_do_not_lock_out_phones` + 4 | RT-RC2 (40 IPs, drippers, churn: every legitimate request ≤ 0.51 s) |
| UAT-S-02 | M | A paired device could mint unlimited siblings; rate limit per device in memory; revocation missed siblings | No family cap; in-memory per-device limit | Family cap 6; per-family persisted rate limit; family revoke (device and `nsf pocket erase-pairing --family`); `list-pairings` shows family/sponsor/created | `tests/test_field.py::Family`, `test_rate_limit_per_family_survives_a_restart`, `test_forget_all_linked_devices` | RT-RC2 |
| UAT-S-04 | M | Research submit while writes paused dropped the connection | `InstanceError` not mapped | 507 `writes_paused` + UI message | `test_writes_paused_is_507_not_a_dropped_connection` | RT-RC2 |
| UAT-S-08 | L | 300-character questions in CJK rejected by the 512-byte body cap | Characters vs bytes | Byte counter in UI; `research_submit` body up to 1024 bytes | `test_non_latin_question_fits_the_research_body_cap` | RT-RC2 |
| UAT-S-12 | L | Server version leaked; error pages without security headers | http.server defaults | `Server: NeuroSciFi-Pocket`; headered error pages (console too) | `test_server_header_and_error_pages`, console `test_error_pages_carry_security_headers_and_no_version` | RT-RC2 |
| UAT-S-13 | L | Bridge refused to start after a crash left the invitation file | Never-overwrite rule had no stale-file path | Safe replacement of our own stale 0600 file (no symlinks) | `tests/test_pocket_invitation.py` (5 cases) | RT-RC2 |
| UAT-U-31 | L | Busy port → Python traceback | `OSError` uncaught | One-line error, exit 2 | `test_busy_port_is_one_line` | RT-RC2 |
| RT-B-01 | M | Rapid Share on a second row silently dropped | Global flag released by a 600 ms timer | Per-episode in-flight set | `test_share_episode_on_a_different_row_is_never_dropped` | RT-final |
| RT-B-02 | L | "Automatic sharing stopped" vanished after 8 s | Blanket auto-clear (U-09 fix) | Only transient confirmations auto-clear | `test_pairing_refusal_explanation_stays_until_dismissed` | RT-final |
| RT-B-08 | L | Stale research message after revoke; expired link code shown | Generation check skipped clearing | Cleared on unpair and at code expiry | `test_revoked_pairing_clears_the_research_message`, `test_expired_link_code_is_removed` | RT-final |

## Console (real console, Chromium)

| ID | Sev | Defect | Root cause | Fix | Regression test | Verification |
|---|---|---|---|---|---|---|
| UAT-C-01 | H | Rejecting/archiving knowledge broke every later consolidation on that subject (`IntegrityError` on `(key, version)`) | Version chosen only from live rows | `next_version` = MAX+1 over all rows; prior rejection stands | `test_consolidate::test_new_knowledge_after_reject_or_archive_takes_the_next_version_and_rejection_stands` | RT-RC2 (reject, archive, reject-again) |
| UAT-U-01 / RT-B-05 | H | Row actions off-screen on phones, then clipped on tablets | Fixed-width tables; 640 px breakpoint | Stacked labelled rows; container query covers tablets | `test_phone_tables_stack_and_keep_actions_on_screen`, `test_row_actions_stay_inside_the_table_box_on_tablets` | RT-RC2 (390); RT-final (768/1024) |
| UAT-U-02 / RT-B-06 / S-09 | H | Verdict contradicted its own report | Verdict counted separately from contradictions; questions checked as if statements | One source of truth; yes/no questions converted to statements; wh-questions get no verdict | `test_a_verdict_only_for_a_checkable_statement_and_never_against_the_answer`, `test_every_contradiction_the_verdict_counts_is_listed_with_a_description`, `test_report_is_plain_and_consistent` | RT-final |
| UAT-U-03 / C-06 | H | Privacy refusal told the owner to set an API parameter | API wording surfaced | Plain refusal naming kinds; explicit consented override | `test_privacy_refusal_is_plain_and_has_an_owner_override` | RT-RC2 |
| RT-B-03 | M | Authorised disclosure silently dropped after the planner reformatted a phone number | Exact-text comparison of findings | Normalised comparison; original authorised text restored; event recorded | `test_an_authorised_value_survives_the_planners_reformatting` | RT-final |
| UAT-C-05 | M | Monitors could be added while web access was off | No enablement check on the routes | 409 + disabled control with reason | `test_monitor_routes_refuse_while_web_access_is_off` | RT-RC2 |
| UAT-U-13 / RT-B-07 | M | Accept path took 4 clicks; contested questions had no value buttons | No shortcuts; parser expected old wording | Report → Knowledge link; Accept/Reject on questions; values parsed from structured pairs/prose | `test_report_links_to_knowledge_and_questions_have_shortcuts`, `test_contested_question_has_one_button_per_value` | RT-RC2 (3 clicks) / RT-final |
| UAT-U-14 / U-28 | M | Jargon (nats, posterior, stemmed keys) and literal backticks | Raw numbers in main text | Plain sentence first; numbers under Technical detail | `test_question_text_is_plain_with_numbers_and_keys_under_technical_detail` | RT-final |
| RT-B-04 | L | Focus lost after Accept/Reject/Forget | Re-render removed focused button | Focus restored to the item or neighbour | `test_knowledge_review_keeps_keyboard_focus` | RT-final |
| UAT-U-04..U-08, U-11, U-12, U-15..U-17, U-21..U-30, C-07, C-10..C-14, C-15 (axe), console U1–U9 | L–M | Worker-less waits, stopped-server states, 409/507 handling, header wrap, small targets, nav height, frozen dates, contradictory badges, raw failures, label inconsistency, heatmap size, spacing, font, stale counts, unexplained disabled buttons, dialog wording, overview inventory, filtered empty state, CJK body size, axe moderate | UI copy/state handling | See UX_VALIDATION | `tests/e2e/test_console_uat.py` (15 tests), `tests/console/test_console_uat.py` | RT-RC2 |

## Web Cortex research (live internet, CLI/worker/MCP)

| ID | Sev | Defect | Root cause | Fix | Regression test | Verification |
|---|---|---|---|---|---|---|
| UAT-W-01 | H | Answers cited to sources that do not contain them; different subjects merged | 4-stem key clustering; every member cited | Primary-subject and record-identity clustering; citations only from restating sources | `test_statements_about_different_subjects_never_share_a_cluster`, `test_answer_cites_only_sources_that_contain_the_statement` | RT-RC2 partial; RT-final; bench citation precision 1.0 |
| UAT-W-02 | H | Different papers "corroborated" each other into knowledge | Record templates clustered across records | Record identity (DOI/arXiv/advisory/repo+tag/package) | `test_records_of_different_papers_never_corroborate` | RT-RC2 |
| UAT-W-03 | H | `verify=False` read as negation | Negation regex matched code literals | Code literals ignored | `test_code_literals_are_not_negation` | RT-RC2 |
| UAT-W-04 / RT-W-02 | H | Fact checks did not separate true from false; then "Paris" disputed via "France"/"New York" | Lexical agreement; any "in <Place>" counted | Shape-based checks; subject-attached places; containment | `test_fact_check_discriminates_place_claims`, `test_location_checks_attach_places_to_the_subject_and_accept_containment` | RT-final; Fixer live |
| UAT-W-05 | H | Publication date taken from HTTP Last-Modified | Header used as a date source | Header only sets `modified_at`; page modified date is the fallback, labelled | `test_a_publication_date_wins_and_last_modified_is_never_the_fallback` | RT-RC2; bench date extraction 53/53 |
| RT-W-01 | H | (RC2 regression) Wikipedia articles yielded zero claims | Article inherited the search hit's `snippet_only` flag | Flag dropped for articles | `test_a_wikipedia_hit_marked_snippet_only_yields_cited_claims_from_its_article` | RT-final; Fixer live |
| UAT-C-02 / RT-W-03 | H | "Latest release" reported an older version; release notes contested each other | Provider order; no record identity for record statements | Semver-aware latest; record identity for every registry statement; client sentences are templates | `test_different_releases_are_different_records_and_the_highest_version_leads`, `test_statements_of_registry_records_carry_their_records_identity` | RT-final |
| UAT-W-24 | M | Off-topic sentences ranked first ("Capital punishment in Australia") | BM25 overlap ignored the subject | Subject-aware tiers before value | `test_answers_about_the_questions_subject_rank_above_sentences_about_something_else` | RT-final; bench top on-subject 14/14 |
| UAT-W-11 / C-03 / RT-W-04 | M | Plain package names never reached registries; npm namesakes mixed in | Only prefixed forms recognised; no ecosystem choice | Release-cue package detection; ecosystem selection with a note for namesakes | `test_plain_package_name_…`, `test_a_same_named_package_of_another_ecosystem_never_joins_the_answer` | RT-RC2 / RT-final |
| UAT-W-06 | M | Disabling web or denying a domain did not stop a running task | Config read once | Re-check every 2 s; partial with a limitation | `test_turning_web_access_off_mid_run_stops_the_task_partial` | RT-RC2 |
| UAT-W-07 / RT-W-08 | M | Nothing read → "completed" | Status ignored empty acquisition | `failed` with named failures | `test_when_every_provider_fails_and_nothing_is_read_the_task_fails`, `test_nothing_read_because_engines_failed_or_were_empty_is_a_failure` | RT-RC2 / RT-final |
| UAT-W-08 | M | JS render out of budget reported ok | Sub-request refusals unchecked | Limitation + partial | `test_render_budget_shortfall_is_a_limitation_and_rendered_pages_are_cached` | RT-RC2 |
| UAT-W-09 | M | Injection variant reached a report field | Pattern gap; held statements not sanitised | 21 patterns; `report_safe_text` everywhere | `test_injection_variants_never_reach_any_report_field`, `test_held_statements_never_carry_instruction_like_text` | RT-RC2; bench injection 6/6 contained |
| UAT-W-10 | M | Wikipedia snippets used as documents | Snippet stored as text | Article lead fetched | `test_wikipedia_hits_are_snippet_only_and_article_returns_the_lead` | RT-RC2 → RT-W-01 → RT-final |
| UAT-W-12 | M | Autonomy daily cap bypassed by proposals | Proposals skipped the cap | Counted and clamped at propose, confirm and start | `test_curiosity_proposals_count_against_the_daily_autonomous_cap` | RT-RC2 |
| UAT-W-14 / W-13 | M/L | Feeds polluted unrelated research; private feed URL accepted; partial feed failures hidden | One shared word sufficed; no address policy | ≥2 topical terms or all entities + BM25 floor; `check_url`; failures recorded | `test_feed_items_need_two_query_terms_or_every_entity`, `test_feed_urls_must_pass_the_network_address_policy` | regression tests only (NOT TESTED live) |
| UAT-W-17 / S-06 | M | Dead worker's task stuck 10 min | Lease-only liveness | Holder host:pid:start liveness; `worker_alive` | `test_a_dead_workers_task_is_cancelled_resumed_or_requeued_at_once` | RT-RC2 (cancel 0.11 s) |
| UAT-W-18 / W-19 | M | Crawl not checkpointed per page; crawls produced 0 claims | One blocking walk; title-as-topic | Page-by-page with cancel; notable-statement extraction | `test_crawl_records_each_page_as_it_arrives_and_honours_cancel_between_pages`, `test_a_crawl_without_a_question_reports_each_pages_notable_statements` | RT-RC2 / RT-final |
| UAT-W-20 | L | Junk queries; no change baseline; template-based curiosity queries | No hygiene; no snapshot | Hygiene; snapshot comparison; `query_text` | `test_no_query_is_only_stopwords_…`, `test_a_second_change_check_compares_with_the_previous_snapshot` | RT-RC2 partial → regression tests |
| UAT-W-21/22/23, RT-W-06/07, C-09, C-16 | L–M | Wording; accept-then-reject history; rendered pages not cached; "Abstract" glued; mid-run deny reported completed; myth sentences asserted; contested text punctuation/duplication | Various | See commits `cce5134`, `d7e1bee` | `test_review_after_resolve_…`, `test_strip_heading`, `test_a_sentence_reporting_a_myth_does_not_assert_it`, `test_each_disagreement_is_stated_once_…`, `test_a_domain_denied_mid_run_…` | RT-RC2 / regression tests |
| UAT-W-15/16, S-10 | L | Daemon busy-port message, ownership reporting, file modes | — | `port_in_use`; ownership fields; 0700/0600 | `test_port_held_by_a_non_daemon_process_…`, `test_daemon_state_files_are_private` | regression tests only |
| UAT-C-04 | M | Privacy screen flagged 299792458, populations, ISBNs as phones | Digit-run regex | Phone formatting or context required | `test_safety` phone cases | RT-RC2 |
| UAT-C-08 | M | Forgotten knowledge's queued refresh task kept its statement | Refresh not linked to the item | Owner refresh origin; forget cascades to unfinished refreshes | `test_forget_knowledge_forgets_its_unfinished_refresh_tasks` | RT-RC2 |

## Backend, security, reliability

| ID | Sev | Defect | Root cause | Fix | Regression test | Verification |
|---|---|---|---|---|---|---|
| UAT-S-03 | M | Console dataset/graph/epoch writes ignored the free-space pause | Free-space check needed an explicit path | Instance path resolved from the connection; checks on every write path | `test_every_write_path_honours_the_free_space_reserve_without_a_path` | RT-RC2 (507 from the UI) |
| UAT-S-05 | M | Corrupt (disposable) public cache disabled all research | Cache opened without handling `DatabaseError` | Quarantine-rename and rebuild; `nsf doctor` checks it | `test_a_corrupt_cache_is_quarantined_and_rebuilt_on_open_and_on_use`, `test_doctor_checks_the_public_cache_file` | RT-RC2 |
| UAT-S-07 / S-14 | L | Requeue not idempotent; duplicate submissions | No existing-child check; no dedupe | Transactional requeue; dedupe of identical open tasks | `test_requeue_is_idempotent_and_identical_submissions_are_deduplicated` | RT-RC2 (5 parallel → 1) |
| REL-01 | M | Built wheel identified itself as 0.5.0 although the release is 0.6 | Version never bumped | 0.6.0 in `pyproject.toml`, `__init__`, README | backend acceptance script (wheel name) | `8054a7f` |


## Found by the final retest (R4, `8054a7f`) and the final regression run — fixed in `4eeaed1`

| ID | Sev | Defect | Root cause | Fix | Regression test | Verification |
|---|---|---|---|---|---|---|
| RT4-01 | M | Answering a question in the console dropped keyboard focus to the page | Re-render removed the focused control; cards had no heading | Focusable card headings; focus to the same/next/previous card or the view heading | `tests/e2e/test_console_uat.py::test_answering_a_question_keeps_keyboard_focus` | e2e (2 consecutive full runs) |
| RT4-02 | L | Curiosity list titled items with stemmed keys and showed "bits" in the main text | Fallback to the key; raw reason text | Statement text as title; numbers under Technical detail | `test_curiosity_titles_are_plain_sentences_with_numbers_under_technical_detail` | e2e |
| RT4-03 | L–M | "Was numpy 2.5.5 released?" never reached PyPI; "Is numpy 2.5.5 the latest release?" became a garbled statement | "released" not a release cue; first-number split; registry records ignored by the subject check | Release cues with versioned names (`pypi:numpy==2.5.5`); question → statement forms; the package's own registry record settles release statements (ADR-033) | `test_a_versioned_name_with_a_plain_release_word_is_a_registry_lookup`, `test_a_registry_record_decides_a_release_verdict`, `test_plain_release_questions_reach_the_registry_and_its_record_decides` | Fixer live (supported, 4 requests each) |
| RT4-04 | L | GitHub release quotes cut mid-word with markdown | 400-character display snippet used as the document | Whole release body as plain text (≤ 20,000 chars, cut at boundaries); snippet only for lists | `test_github_release_body_is_the_whole_plain_text_document_and_the_snippet_never_cuts_a_word` | Fixer live |
| RT4-05 | L | The same disagreement was re-asked by every task | Only knowledge items suppressed questions | The owner's chosen value is recorded; matching open questions close; later tasks note the decision | `test_the_owners_answer_to_a_disagreement_is_not_asked_again_by_later_tasks` | regression test |
| RT4-06 | L | Console URL file (session token) left on SIGHUP | Only SIGTERM routed to cleanup | SIGHUP and SIGQUIT clean up | `test_url_file_is_removed_on_hangup_and_quit[*]` | regression test |
| OBS-01 | M | One Wikipedia 429 kept its circuit open for minutes | Our own backoff refusals counted as provider failures | Politeness waits (rate_limited with retry_after) never move a breaker | `test_politeness_waits_do_not_move_breaker`, `test_wikipedia_backoff_after_one_429_does_not_open_the_circuit` | Fixer live (circuit closed after a 429) |
| OBS-02 | L | "The tower …" from another article quoted beside Eiffel Tower answers | Definite descriptions had no subject | `title_reference()` gives them their document's subject for clustering, ranking and verdicts | `test_a_definite_description_takes_the_subject_of_its_own_documents_title` | regression test; bench answer items improved |
| FR-01 | M | Pocket: revoking a pairing during the post-submit refresh left "Your Spark no longer accepts this pairing" in the research panel after it had been cleared | `openTask` did not check pairing state; a local "no token" condition was raised as HTTP 403 | Local `not_paired` error is silent; `openTask` requires a pairing; submit stops when the pairing changed | `test_revocation_during_the_post_submit_refresh_leaves_no_stale_refusal` (deterministic interleaving) | e2e (2 consecutive full runs) |
| FR-02 | M | Pocket: after reconnecting, the header kept "Spark unreachable" although the Spark answered | A request failed while offline set reach "down"; reconnecting only cleared the offline flag | Re-check the Spark on the `online` event | `test_back_online_rechecks_the_spark_instead_of_keeping_a_stale_unreachable` | e2e (2 consecutive full runs) |

| FR-03 | M | Monitor reported "meaningful change" and opened an owner question for search churn (live Scenario D: two ticks minutes apart) | A different paper's date appeared under a shared stemmed record-template key whose previous value list was empty, and counted as a value change | Record claims keyed by record id; a value change needs stated values before and after; new values follow the new-claim rule | `tests/webcortex/test_scheduler.py::test_a_value_appearing_under_a_shared_record_template_key_is_not_a_change` | fixed in `abcd303`; live Scenario D re-run at `abcd303` (REGRESSION_REPORT §4) |

FR-01 and FR-02 first appeared as order-dependent failures of existing tests in full-suite runs; they were traced to
real races in the product (not test flakiness) and fixed at the root.

## Earlier review findings (pre-UAT)

The independent architecture, security and code reviews (P0×2, P1×14) and the documentation/benchmark audits
(R1–R16) are recorded with their fixes in commits `df00137` and `64da95b`; see `docs/web-cortex/TEST_RESULTS.md` and the
reviewers' probe before/after logs. Highlights: forgotten research no longer resurrects from checkpoints (migration 7
tombstones); slow-drip fetches time out; HTML attribute bombs refused; robots.txt RFC 9309; honest search User-Agent.

## Open (not fixed) — see PRODUCTION_READINESS

| ID | Sev | Description | Why open |
|---|---|---|---|
| OPEN-01 | M | True claims backed by two independent sources where one is undated stay "insufficient" (bench F08/F12/F14) | Documented rule (ADR-026); policy decision pending (decisions-pending item 5) |
| OPEN-02 | L–M | A contested cluster's representative can be the minority (noise) page's value (bench A02); the contradiction is listed | Representative selection by group size; needs a source-quality signal that is not popularity |
| OPEN-03 | M | Key-free search engines return nothing with an honest User-Agent; general web discovery relies on structured APIs and feeds | Owner decision (ADR-032, decisions-pending item 4) |
| OPEN-04 | — | Unknown backend defects in scope never independently executed: see NOT TESTED rows in E2E_TEST_RESULTS | Two backend testers stalled; core backend checks were run by the integrator instead |
