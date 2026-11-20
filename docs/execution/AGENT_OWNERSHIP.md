# Agent ownership

Rules: one accountable owner per file. Agents in a shared working tree never commit, stash or reset; the integrator (architect) verifies each workstream independently and commits it. Shared contract files change only through the architect, with an ADR entry.

| Owner | Role / model | Files |
|---|---|---|
| Architect (lead session) | Discovery, contracts, integration, commits, final verification | `docs/**`, `src/neuroscifi/instance/{__init__,schema,store,errors,rules,cli}.py`, `tests/instance/conftest.py`, `scripts/check.sh` (after W1) |
| W1 executor | Foundation (done) | package layout, `pyproject.toml`, `src/neuroscifi/{cli,files_cli}.py`, `lab/cli.py`, `.github/workflows/ci.yml`, `scripts/reproduce.py` |
| W2-A executor (opus) | Evidence, beliefs, executive | `instance/{evidence,beliefs,executive,legacy,cli_evidence}.py`, `tests/instance/test_{evidence,beliefs,executive,legacy}.py` |
| W2-B executor (opus) | Graph lifecycle | `instance/{datasets,learner,graphs,recall,cli_graphs}.py`, `tests/instance/test_{datasets,learner,graphs,recall}.py` |
| W2-C executor (sonnet) | Memory and governance | `instance/{memory,governance,checkpoints,cli_memory}.py`, `tests/instance/test_{memory,governance,checkpoints}.py` |
| W2-D executor (sonnet) | Pocket | `src/neuroscifi/pocket/**`, `tests/test_{mobile,field,mobile_http}.py`, `tests/js/**`, `tests/e2e/**`, `package.json`, `scripts/e2e.sh`, `mobile/README.md` |
| W3 executors | Console, acceptance, bench, doctor | `src/neuroscifi/console/**`, `src/neuroscifi/acceptance/**`, `src/neuroscifi/instance/service.py`, `tests/console/**`, `tests/acceptance/**` |
| Reviewers (read-only) | architect, security-reviewer, code-reviewer, verifier | none; findings only |
| Writer | Operations documents | `docs/operations/**`, `deploy/**` |

Untouchable without an ADR: `src/neuroscifi/vendor_tufazip/**`, `evidence/**`, `research/**`, `figures/**`, `mobile/native/**`, `Architecture.md`, `NeuroSciFi_Architecture.pdf`, `NeuroSciFi_Prototype.zip`, and the numerics in `src/neuroscifi/lab/**`.
