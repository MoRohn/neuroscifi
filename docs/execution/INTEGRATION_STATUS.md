# Integration status

Updated at every wave boundary. Evidence is the integrator's own re-run, not an agent's report.

| Seam | Producer → consumer | State | Evidence |
|---|---|---|---|
| Package and CLI registration | W1 → all | Integrated | `nsf --help`; check.sh green at `392ea0f` |
| Instance schema and store | architect → W2-A/B/C | Integrated | `nsf init/birth/regulation` smoke; immutability trigger fires (`ef95e48`) |
| `rules.register` at birth → graph gate | architect → W2-B | Integrated | test_graphs gate (d), (e); rule pinned per candidate |
| evidence → beliefs → executive | W2-A internal | Integrated | 66 tests incl. 220-sequence legacy parity at 1e-12 |
| governance → `graphs.invalidate_dependents` | W2-C → W2-B | Integrated | test_governance dataset cascade; test_journeys |
| checkpoints → `archive.convert` | W2-C → prototype primitive | Integrated | test_checkpoints (writer-open backup, corruption, no resurrection) |
| governance → beliefs (post-deletion revision) | W2-C → W2-A | Integrated after fix `a5f4832` | test_journeys::test_forget_records_the_real_post_deletion_belief_state |
| CLI → governance.sweep before each command | architect | Integrated | test_journeys::test_cli_sweeps_expired_evidence_before_reading |
| Pocket web ↔ bridge (mute/unmute) | W2-D | Integrated | test_mobile; 13 Playwright e2e (re-run by integrator) |
| console → instance service | W3-E, I2 | Integrated | 37 console API tests; 12 console e2e |
| acceptance → instance service | W3-F, I1 | Integrated | 7 scenarios; 6 pass, first_physical_test 3/4 (D-1) |
| executive.resolve(confirm) → changepoints → beliefs | R2 | Integrated | test_scope_learning (brute force 1e-12); README CLI run |
| match_epochs → epoch_links → beliefs pooling | R4 | Integrated | link brute-force test; counterfactual return accuracy 0.50 → 0.81 |
| governance → derived memory scrub → restore | R1, I1, R4 | Integrated | test_erasure (all tables + raw DB/WAL bytes); 0 name hits in a dump after forget |
| Pocket TLS server → worker-thread handshake | R3 | Integrated | PocketTLSRobustness |
| webcortex orchestrator ↔ instance (pending questions, resolve) | W1-D → executive | Integrated | tests/webcortex/test_instance_integration.py::test_resolve_confirm_accepts_and_reject_rejects_in_one_transaction, ::test_pocket_monitor_proposal_is_authorised_through_resolve |
| governance web forget ↔ checkpoints restore (migration 7 tombstones) | W4-X V2 → W2-C | Integrated | tests/webcortex/test_instance_integration.py::test_restore_never_resurrects_forgotten_research, ::..._knowledge, ::..._a_forgotten_source_domain; tests/instance/test_migrations.py::test_migration_7_adds_web_tombstones_and_a_claim_time_index |
| console ↔ embedded web worker | W2-E2 → W1-D | Integrated | tests/console/test_console_web.py::test_submit_runs_on_the_embedded_worker_and_reports, ::test_two_workers_never_run_one_task_twice, ::test_web_worker_is_opt_in_and_shutdown_stops_the_worker; tests/e2e/test_console_research.py |
| Pocket ↔ instance research (family-scoped) | W2-E3 → W1-D | Integrated | tests/test_pocket_research.py::test_scenario_f_phone_queues_spark_runs_tablet_reads, ::test_family_scoping_and_owner_view; tests/e2e/test_pocket_research_e2e.py |
| MCP server ↔ orchestrator | W2-E1 → W1-D | Integrated | tests/webcortex/test_mcp.py::test_spawned_server_round_trip, ::test_research_task_evidence_and_knowledge_are_wrapped_as_untrusted, ::test_mcp_origin_tasks_share_a_daily_cap |
| search daemon (open-webSearch) ↔ provider registry | W1-B → W1-D | Integrated (engines return nothing with the honest User-Agent, ADR-032) | tests/webcortex/test_openwebsearch.py::test_registry_sees_engine_attempts_and_status, ::test_engine_breaker_opens_and_is_skipped, ::test_health; live run in docs/quality/evidence/webcortex_live.json |
