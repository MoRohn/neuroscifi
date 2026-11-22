# Cognitive inference: the local model and how it may speak

Status: v0.7.0, Lane B. Decision record: ADR-036 (draft in `.omc/autopilot/adr/ADR-036-inference-gateway.md`),
extending ADR-022. Live evidence: `docs/quality/evidence/inference_live.json`.

## 1. What it is

NeuroSciFi now runs a language model on the Spark and lets it write research answers, under three rules:

1. **Local-first.** The model is a local vLLM server on `127.0.0.1:8811`. Any endpoint that is not loopback (LAN,
   tailnet and private ranges included) is refused unless the owner authorises it (`remote_allowed`); then every
   message passes the web privacy screen, private memory needs a second authorisation, a public or CGNAT endpoint
   must be https, and the API key is never sent over plain http off this host.
2. **Evidence-only.** The model writes the answer *after* verification, from the verified evidence bundle only, and
   every sentence is checked by local code before it is shown. It never changes a claim's state, and its text never
   becomes knowledge.
3. **Honest labels.** `generated_by` is `model:<name>` only when a checked model answer is shown; otherwise it is
   `template` and the report says "Model-written summary unavailable — showing the extractive summary quoted from the sources" and why. The extractive
   answer of ADR-022 is always kept, unchanged.

```
intent ─▶ memory ─▶ discovery/reading ─▶ claims + verification ─▶ synthesis (extractive report)
                                                                      │
                                         reasoning.apply ◀────────────┘   (orchestrator._reason)
                                           │ bundle(): verified claims, states, quotes, source ids, dates (untrusted)
                                           │ gateway.structured(): JSON answer, one repair
                                           │ guard(): per-sentence citation / state / number+unit / injection checks
                                           │ self-critique (one call, may only flag; removal needs an objective check)
                                           ▼
                               report.reasoning + report.generated_by ─▶ consolidation (claims only) ─▶ memory
```

## 2. Inference gateway (`src/neuroscifi/inference/`)

| Module | Role |
|---|---|
| `config.py` | Per-individual settings in the instance `meta` table (key `inference`); default not configured. Endpoint locality (loopback / private = RFC 1918, ULA, link-local / remote, CGNAT 100.64.0.0/10 included); only loopback is used without `remote_allowed`. The host is resolved once per gateway and the connection goes to that address (pinned; Host header, SNI and certificate check keep the configured name), so DNS rebinding cannot move it between check and connect. API key read from a file (`key_file`, else `NSF_INFERENCE_KEY_FILE`, else `~/.config/neuroscifi/inference/api_key`); a world-readable key file is refused. |
| `gateway.py` | `Gateway.models()`, `health(probe=)`, `chat(stream=, tools=, response_format=)`, `structured(schema)`. Stdlib `http.client`; no SDK. |
| `jsonschema.py` | The schema subset used for structured output and tool arguments, validated locally. |
| `tools.py` | Allow-listed tools; the model suggests, NeuroSciFi decides and executes. |
| `ledger.py` | One row per call in `<db stem>.inference.sqlite` (0600): purpose, model, locality, status, prompt/completion tokens, latency, first-token latency, attempts, tokens/s. No prompt or answer text. |
| `cli.py` | `nsf llm config|status|models|ask|usage`. |

**Health states** (never guessed; the console and CLI show exactly these):

| State | Meaning |
|---|---|
| `not_configured` | no endpoint; or an endpoint off this host without `remote_allowed`, a public/CGNAT one without https, or an API key over plain http off this host |
| `unauthorised` | the endpoint answered 401/403 (missing or wrong key) |
| `unavailable` | connection refused, timeout, 5xx, unparsable answer |
| `degraded` | it answers, but the configured model is not served, nothing is served, or the `--probe` generation failed |
| `ready` | it lists the model (and with `--probe` generated a reply) |

**Call discipline.** Endpoint guard and off-host privacy screen first; a context budget (prompt estimate at 3.5
characters per token + `max_tokens` ≤ `max_context_tokens`, default 24 000) is checked before anything is sent;
connect timeout 3 s and a whole-call deadline (`read_timeout`, 180 s, streaming included); retries only for connection
errors, 429 and 502–504, at most `max_retries` (2) with backoff 0.5 s doubling, Retry-After honoured up to 10 s; a
stream is never retried after it started; 400/401/403/404 and timeouts are not retried. Health checks do not retry.
`<think>…</think>` is stripped. `thinking` (`default|on|off`) maps to Qwen3's `chat_template_kwargs.enable_thinking`
(the Instruct-2507 model does not think, so `default` is used).

**Structured output.** `response_format: json_schema` is sent (vLLM guided decoding), but the reply is parsed and
validated locally; on a violation the model is re-asked once with the error list; a second failure raises
`schema_invalid`.

**Tools.** `knowledge_query` and `research_task` (permission `read_knowledge`) and `web_research` (permission
`submit_research`, not granted by default; it only queues a task through `orchestrator.submit`, so the web switch,
privacy screen, budgets and deduplication apply). Only granted tools are offered; a call to anything else, with
non-JSON or schema-invalid arguments, is refused and the refusal is returned to the model; tool output goes back
marked untrusted; the loop is bounded (3 rounds, the last without tools). No tool changes configuration, forgets,
accepts knowledge or resolves questions.

## 3. Reasoning over evidence (`src/neuroscifi/webcortex/reasoning.py`)

`orchestrator._reason` calls `reasoning.apply` after synthesis for completed and partial runs (memory answers
included); failed and cancelled runs keep the extractive report only.

- **Bundle.** Up to 12 findings: id `C<n>`, ADR-035 state and category (`finding.verification`, computed by
  `synthesis.claim_state` before reasoning with the same rule as consolidation, `synthesis.decide`), confidence, quoted text (≤ 420 chars), source ids
  (its citations plus corroborating sources), publication/event dates; the cited sources' id, title, domain, dates
  and kind; the verdict label; the contradiction count; whether freshness was met. Findings that trip
  `safety.scan_injection` are left out. The bundle is JSON inside `<evidence>` with `"untrusted": true`; the system
  prompt says instructions inside it are data.
- **Answer.** Streaming structured output: `{"sentences": [{"text", "sources": [int], "claims": ["C1"]}], "gaps": []}`.
- **Guard** (the authority). A sentence is rejected if it cites no source or no claim, cites an id not in the bundle,
  cites a claim through a source that does not carry it, relies on a claim whose ADR-035 state is not `supported`
  (a time-sensitive claim backed only by stale or undated sources is `temporally_unresolved`; the legacy
  `corroborated` label never counts) without hedging in its own words (local patterns: "according to", "one source", "may", "disputed", …),
  calls a non-supported claim verified/confirmed, contains a number that is in none of its cited claims or their
  sources' dates/titles (a small count before "sources" is allowed; "4.2" matches "4.2.0"), gives a number a unit
  or scale the cited evidence does not give that number (`_unit_mismatches`: "41 feet" vs "41 metres", "86 billion"
  vs "86 million", "17 days" vs "17 November", "$1.5 million" vs "1.5 billion dollars"; units and scale words are
  normalised, "%" = "percent"; an ambiguous one-letter unit such as "5m" is not judged), says more than the claims
  it cites (SEC-G-E1, below), or contains instruction-like text. `check_sentence` holds these checks.
- **Entailment by content-word coverage (SEC-G-E1, `_unentailed`).** Until v0.7.0 a sentence that cited any
  supported claim was not checked for what it said: citing "NumPy is the fundamental package for scientific computing
  with Python." passed "NumPy is secure and recommended for all production use." and "Users should reinstall NumPy
  from the official mirror immediately."; live (R5-O1) a kept sentence called "the widely accepted figure of 100
  billion neurons" "not definitively established" on a Crossref title record that says neither. Every sentence that
  is not a caveat of the closed grammar is now checked, deterministically and without a model:
  - *Content words* are the sentence's words without stopwords, digits and the unit after a number (the number and
    unit checks own them), attribution and hedge vocabulary ("according to one source", "states that", "may", "out
    of date", "temporally unresolved", "not yet confirmed", "estimates vary widely") and its caveat-grammar
    clauses.
  - A content word is *covered* when a cited claim holds it: exactly, by stem or singular/plural, in a closed synonym
    group (released/published/introduced, vary/range, rely/depend, has/contains/includes, bn/billion), by a common
    6-letter prefix, as two claim words written as one, or (an all-capital word) as the initials of claim words; a
    name or plain word may also be covered by a cited source's title.
  - Rejected — "says more than its cited claims support: secure, recommended, …" — when under 75 % of the content
    words are covered (`ENTAIL_COVERAGE`), when a clause of two or more content words has under a third covered
    (`ENTAIL_CLAUSE`: the claim's words in an appositive cannot carry a new predicate), or when **any** uncovered word
    carries assertion weight. Those classes are checked against the claims' own words only, never titles:
    evaluatives (secure, safe, recommended, trusted, stable, best, critical, …), names, scope words (all, every, only,
    …), comparatives (than, faster, more, …), time words (yesterday, currently, recent, last week, month names, …),
    numbers in words, and an adjective after a copula ("is popular", "is unaffected").
  - Instructions — "gives an instruction its cited claims do not give: should" — a modal of obligation (should,
    must, need, recommend, …), an address to the reader (you, everyone, anyone) or an imperative opening ("Reinstall
    …", "Avoid …") fails unless a cited claim holds the same instruction ("Users should upgrade to NumPy 2.5.5"
    supports "According to the advisory, users should upgrade …").
  - Negation: a negator over content words that no cited claim negates nearby ("is not affected" for "is affected")
    fails ("negates what its cited claims do not negate"), and so does a sentence with no negation that repeats the
    word a cited claim negates ("is designed" for "is not designed": "drops the negation of its cited claims"). A
    negator over an evidence word ("not established", "does not indicate", "does not contain information") is a
    hedge.
  The check runs after the number and unit checks, so the attribution repair (R1-04) and the self-critique
  confirmation use it unchanged. Tests: `test_e1_*` (the two E1 examples, R5-O1 and 35 attacks, each of which
  passed before; 18 faithful paraphrases; 5 live sentences that must stay kept).
- **Honest caveats: a closed grammar** (`_caveat`, `insufficiency`, `gap_clause`). A sentence that only says what the
  evidence does not establish asserts no fact: it needs no supported claim, no hedge and (T2-03) no citation, and it
  may repeat the question's own numbers. Two security reviews (SECREVIEW_GUARD F1–F6, SECREVIEW2_GUARD N1–N4) showed
  that recognising caveats by open-ended structure leaks: a statement after a comma or inside "whether ...", the
  question's predicate as an attribute ("the secure numpy 2.5.5 release"), a word sharing a stem with the question
  ("withdrawn" for "with"). **Free-form caveats are therefore rejected on purpose; the deterministic rules note (below)
  carries the message.** A sentence is a caveat only when, after NFKC, unicode punctuation as ASCII and lower case, the
  whole of it is one of these forms (one sentence, a final "." at most):
  - `[LEAD] SUBJECT NEGATED-PREDICATE whether|if Q-CLAUSE [ADVERBIAL]` — "No source states whether numpy 2.5.5 is
    secure."
  - `[LEAD] whether Q-CLAUSE PASSIVE-NEGATION [ADVERBIAL]` — "Whether numpy 2.5.5 is secure cannot be determined
    from the evidence."
  - `[LEAD] it is|remains unclear|unknown|uncertain|not established|not known|not clear whether Q-CLAUSE [ADVERBIAL]`
  - `[LEAD] there is insufficient|no|not enough evidence|information|data to determine|establish|confirm|verify
    whether Q-CLAUSE [ADVERBIAL]`, and `the evidence is insufficient to determine whether Q-CLAUSE`
  - `[LEAD] [the] ATTRIBUTE of ENTITY PASSIVE-NEGATION [ADVERBIAL]` — "The security of numpy 2.5.5 cannot be confirmed
    based on the available evidence."
  - `[LEAD] SUBJECT NEGATED-PREDICATE the ATTRIBUTE of ENTITY [ADVERBIAL]` — "None of the sources addresses the
    security of numpy 2.5.5."
  - `[LEAD] there is insufficient|no|not enough|a lack of evidence|information|data on|about|regarding the ATTRIBUTE of
    ENTITY [ADVERBIAL]`

  SUBJECT: the (available|cited|provided) evidence, the (cited) sources, the provided claims, no (cited) source, none of
  the (cited) sources, no claim. NEGATED-PREDICATE: does not / do not / did not / cannot (and their contractions)
  + state, establish, confirm, show, indicate, address, evaluate, determine, verify, say, specify (the third-person form
  after "no source" / "none of the sources" / "no claim"), optionally with yet / independently / directly.
  PASSIVE-NEGATION: is/are/was not, has/have not been, cannot be + addressed, established, confirmed, evaluated,
  verified, determined, stated, shown, indicated, specified; remains unknown/unclear/unconfirmed/unverified/
  undetermined; lacks (independent) evidence. LEAD: however, but, still, also, currently, overall, unfortunately,
  importantly, notably, that said, in short, in summary, note that (and, in a compound sentence, a joining and / while /
  although / though / yet / as). ADVERBIAL: in the (available|cited|provided) evidence/sources, in the provided claims,
  based on the available evidence, from the (available|cited) evidence/sources, by any of the cited sources, by the
  (cited) sources, independently, directly, yet. **Q-CLAUSE**: exactly the question's own tokens, in the question's
  order, plus at most one "the"/"a" and one copula (is/are/was): no new word of any kind, no stem or prefix matching.
  **ATTRIBUTE of ENTITY**: the question's predicate mapped by a closed table (secure → security / security status,
  safe → safety, recommended → recommendation / recommendation status, compatible → compatibility, stable → stability,
  vulnerable → vulnerability status, supported → support status, maintained → maintenance status) "of" exactly the
  question's words between its leading copula and that predicate ("Is numpy 2.5.5 secure?" → "numpy 2.5.5"); nothing
  but "the" stands before it. Any other token, any other punctuation (arrows, bullets, pipes, brackets, quotes) and any
  second sentence fails the grammar; an item holding more than one sentence is rejected outright.
  A caveat of the grammar is still rejected when the verdict **decided** the question (any state but not_evaluated,
  insufficient, temporally_unresolved or none): it would be false; when a **supported** claim shares any of its topic
  words (words cannot tell whether "NumPy 2.5.5 is affected by a critical RCE vulnerability" answers "is it secure?",
  so the caveat yields to the verdict and the note); and when it says the evidence holds nothing while a claim holds all
  its topic words. Outside the grammar, a clause shaped as an absence claim (a negator scoping over an evidence noun or
  verb: "No source reports any vulnerability in numpy 2.5.5", "no evidence that ...") is rejected when it says the
  evidence holds nothing or has a that- or existential complement (F2); a hedge such as "not yet confirmed by a third
  source" is left to the hedge and state checks. An uncited caveat may hold no non-ASCII letter and no number in words
  (F5). In a compound sentence a clause of the grammar neither hedges the factual clause beside it nor counts as
  calling anything confirmed, and may name the question's numbers; every number, unit, id and injection check still
  applies. "Gaps include ..." is no caveat (F4). Caveats rejected on purpose are listed with their reasons in
  `REJECTED_CAVEATS` (tests/inference/test_inference_reasoning.py).
- **Uncited caveats and the verdict note (D-R5-3).** The question was whether the guard should accept an uncited
  sentence that asserts no fact. It already did (T2-03), and the structural rule widens what is recognised, so an
  uncited caveat may now hold no number and no name the question does not hold. The R5 retest showed the deeper
  problem: for "Is numpy 2.5.5 recommended?" the model's caveat failed in 3 of 3 runs for wording (uncited with an
  extra noun phrase, or compound with a factual first clause), so whether the reader learned that "recommended" was
  never evaluated depended on the model's phrasing. Relaxing the guard further would trade safety for that
  phrasing; instead the answer carries a deterministic system note (`verdict_note`, `reasoning.note` `{text, by:
  "rules"}`) whenever the verdict's ADR-035 state is `not_evaluated` ("NeuroSciFi note: whether numpy 2.5.5 is
  recommended is a judgement the evidence does not establish.") or `insufficient` ("NeuroSciFi note: the evidence
  read does not establish whether numpy 2.5.5 is secure."), on the model path and on every degraded path. It is
  built from the verdict's statement (derived from the question, injection-scanned) and state only, never from
  model text, is never mixed into the model's sentences, and is shown by the console (`#reasoningNote`) and
  said by voice. F6: the statement must be one plain clause (one verb group, no URL or domain, including "dot org"
  and the ideographic and fullwidth full stops, no address to the reader or the model, no instruction), else there is
  no note. `apply(asked=owner|confirmed|autonomous)`: the plain wording only for tasks the owner typed or said in the
  console, by voice or on Pocket (`orchestrator.OWNER_NOTE_ORIGINS`). Every other origin gets the statement quoted as
  the question in guillemets ("NeuroSciFi note: «numpy 2.5.5 is recommended?» is a judgement the evidence does not
  establish."): the CLI (the model's own `web_research` tool in `nsf llm ask` also submits as "cli", so the two
  cannot be told apart), MCP clients, refresh-owner, monitors, the bench, and an autonomous task (curiosity,
  refresh) the owner authorised. An autonomous task without that authorisation gets no note.
- **Self-critique.** One call (≤ 400 tokens): each accepted sentence is marked `fine`, `unsupported`, `overstated` or
  `missing_hedge`. A mark removes a sentence only when `check_sentence` confirms an objective failure (unknown
  source or claim, a claim state that is not supported stated without a hedge, a number or unit not in the cited
  evidence); otherwise the sentence stays with `flag: {problem, note, confirmed: false}`, shown in the console as
  "self-check flagged". The model's judgement alone never removes a sentence, and it can never add one. `critique`
  reports `removed` and `flagged`. (Before v0.7.0 Lane G any mark removed the sentence; live, that dropped correct
  sentences and once a whole answer.)
- **Time budget.** `orchestrator._reason` passes what is left of the task's `budget.max_seconds`
  (`time_left`); the answer call, its schema repair and the critique share that one deadline (`Gateway.structured`
  gives the repair only the remaining time; `Gateway._call` never exceeds `read_timeout`). With less than
  `MIN_MODEL_SECONDS` (5 s) left no call is made (`status: degraded`, reason `time_budget: …`); a critique without
  time is `skipped`. A timeout degrades the label, never hangs the task, and the model's time is counted in the
  task's `usage.seconds`.
- **Gaps.** Model-identified gaps (injection-scanned, ≤ 300 chars) plus rule gaps from the evidence (single-source
  findings, contested findings, unmet freshness, no claims), each labelled by origin.
- **Output.** `report.reasoning = {generated_by, status, label, answer[{text, citations, claims, states, hedged}],
  rejected[{text, reasons}], gaps[{text, by}], critique, usage, reason, note?}`, `report.generated_by`, a limitation line
  saying how many sentences passed and were rejected, and one `decision` event (when an endpoint is configured).
  `status` is `model`, `not_configured`, `disabled`, `no_evidence`, `unavailable`, `unauthorised`, `degraded`
  (e.g. schema failure, timeout, time budget), `rejected` (nothing passed) or `error`. Undated sources that a search
  engine dated carry `search_engine_reported_date` in the bundle, labelled lower trust.

**Interfaces.** CLI: `nsf web ask` prints the task JSON with `result.generated_by` and `result.reasoning.label`;
`nsf llm ask` replies directly and is labelled "Model output, not checked against evidence". Console: Research view
"Model reasoning" card (`GET /api/llm/status`, same token/Host/Origin checks as every route) and, in a report, the
model answer with its label (`#reasoningLabel`, `data-generated-by` = `model:<name>` or `template`), citations,
claim-state badges and any self-check flag, the knowledge gaps, rejected sentences and token use under Technical
detail, then "Extractive summary (quoted evidence)"; the verdict shows its ADR-035 state, basis and category
(`#verdictBasis`). A template report shows "Extractive summary (template)", the degraded label and the reason.
Browser test: `tests/e2e/test_reasoning_console.py` (fake OpenAI-compatible server).

## 4. Deployment on the DGX Spark (measured 2026-11-21)

Server: `deploy/vllm/run.sh` (settings `deploy/vllm/vllm.env`, owner overrides in
`~/.config/neuroscifi/inference/vllm.env`), optional unit `deploy/systemd/neuroscifi-vllm.service`, installed
disabled by `sudo bash deploy/install.sh --llm --llm-user USER` (sets `User=` and `NSF_REPO=`; `--dry-run` prints
the plan; `--searxng` likewise installs `neuroscifi-searxng.service` and its settings template).

```
docker run -d --restart unless-stopped --name neuroscifi-vllm --device nvidia.com/gpu=all \
  -p 127.0.0.1:8811:8000 --user 1000:1000 --read-only --tmpfs /tmp:rw,size=8g,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges --cpus 12 --memory 96g --shm-size 8g --pids-limit 4096 \
  -v ~/.local/share/neuroscifi/models/qwen3-30b-a3b-instruct-2507-fp8:/models/qwen3-30b-a3b-instruct-2507-fp8:ro \
  -v ~/.cache/neuroscifi/vllm:/cache:rw -e HOME=/cache -e HF_HUB_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 ... \
  --env-file ~/.config/neuroscifi/inference/vllm.secret.env  (VLLM_API_KEY, 0600) \
  --health-cmd 'curl -fsS -m 5 http://127.0.0.1:8000/health' --health-start-period 20m \
  vllm/vllm-openai@sha256:48c0a41e8268faf50317ab83dc12c54fe6549b1131b38d06abe5ed15e3009700 \
  /models/qwen3-30b-a3b-instruct-2507-fp8 --served-model-name qwen3-30b-a3b-instruct-2507-fp8 \
  --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.45 --kv-cache-memory-bytes 13958643712 \
  --max-model-len 32768 --max-num-seqs 8 \
  --enable-auto-tool-choice --tool-call-parser hermes --disable-uvicorn-access-log
```

(`deploy/vllm/run.sh print` prints the exact command.) The image is vLLM 0.33.0rc1.dev910+g4fab5a074, CUDA 13.0.2,
arm64. It worked on GB10 (sm_121) with default flags: no `--enforce-eager`, no attention-backend override. The
container runs as the invoking user with a read-only root filesystem and no capabilities; `--host 0.0.0.0` is inside
the container network only, the host publishes on 127.0.0.1. `/v1/models` without the key answers 401.

Measured (vLLM log and host tools; details and raw values in `inference_live.json`):

| Quantity | Value |
|---|---|
| Weights | 31.2 GB on disk (4 safetensors); "Model loading took 29.33 GiB memory and 22.0 s" |
| Cold start (first ever, no compile cache) | 175 s from `run.sh start` to healthy (engine init 126 s incl. torch.compile 17.7 s, CUDA graph capture 34 s) |
| Warm restart (compile cache in `~/.cache/neuroscifi/vllm`) | 52 s to healthy with the fixed KV cache (engine init 5.1 s); 97 s on an earlier restart under memory pressure |
| Memory | Engine process 43 964 MiB (cold start) / 44 028 MiB (fixed KV cache) per `nvidia-smi --query-compute-apps` (device memory is N/A on GB10); KV cache 13 GiB = 141 952 tokens (4.3 × 32 k contexts); host `free -g` used +43 to +56 GiB (other processes included) |
| Decode speed, uncontended | 48.9 tokens/s median, 208.6 ms median first token (5 × ≤300-token streamed replies; one outlier at 33.0 tokens/s); 50.4 tokens/s on a 90-token reply; 40–46 tokens/s in research answers |
| Decode speed, with another lane's PersonaPlex benchmark on the GPU | 21.5 tokens/s median (5 × 300-token replies, range 21.45–22.12) |
| First-token latency | 209–222 ms (short prompts), 309–513 ms (≈1.5–3.5 k-token evidence prompts), ≈1.6 s for the first structured-output request after a start (JSON-schema grammar compilation) |
| A research answer | 2 calls (answer + critique), ≈ 4.3–4.9 s, ≈ 2–3.5 k prompt tokens |

Unified-memory findings. (1) `--gpu-memory-utilization` alone does not bound vLLM on GB10: on a warm restart, with the
weights already in the page cache, vLLM counted 8.35 GiB of weights and sized the KV cache at 45.9 GiB, and the engine
held 77 760 MiB instead of ~44 GiB. `vllm.env` therefore fixes the KV cache (`NSF_VLLM_KV_CACHE_BYTES`, 13 GiB), which
brought it back to 44 028 MiB. (2) CUDA allocations count against the container's memory cgroup (`docker stats` showed
44.5 GiB on the cold start), so `--memory` (96g) must stay above the engine's footprint. About 44 GiB leaves room for
the 7B speech model planned in area 6; measure co-residency before raising the KV cache.

## 5. Run, configure, recover

```
deploy/vllm/run.sh start && deploy/vllm/run.sh wait          # start, wait for /health (first start ~3 min)
nsf llm config --url http://127.0.0.1:8811/v1 --model qwen3-30b-a3b-instruct-2507-fp8
nsf llm status --probe                                       # state ready + a one-word generation
nsf web ask "When was Python first released?"                # result.generated_by: model:qwen3-...
nsf llm usage                                                 # token ledger
```

| Symptom | Check | Recovery |
|---|---|---|
| `unavailable: cannot connect` | `deploy/vllm/run.sh status`, `docker logs neuroscifi-vllm` | `run.sh start` (or `systemctl restart neuroscifi-vllm`); research keeps working meanwhile, labelled template |
| `unauthorised` | key file path in `nsf llm config` output (`key_file_effective`, `key_present`) | point `--key-file` at `~/.config/neuroscifi/inference/api_key`; after regenerating the key restart the container |
| `degraded: configured model … not served` | `nsf llm models` | `nsf llm config --model <served name>` |
| Container exits at start | `docker logs` | lower `NSF_VLLM_GPU_UTIL` / `NSF_VLLM_MAX_LEN` in the override file; as a fallback `NSF_VLLM_EXTRA_ARGS=--enforce-eager` (not needed on the reference host) |
| Research worker service cannot read the key | `ProtectHome=yes` in `neuroscifi-webcortex.service` | copy the key to `/etc/neuroscifi/inference.key` (root:neuroscifi 0640) and `nsf llm config --key-file /etc/neuroscifi/inference.key` |
| Stop the model to free memory | | `deploy/vllm/run.sh stop`; answers fall back to the extractive summary |

## 6. Limitations

- The guard is lexical. A hedge word anywhere outside a caveat clause ("could", "reports") satisfies the hedge rule.
  Caveats are a closed grammar: it bounds what a caveat can say to the question's own words and closed lists, at the
  price of honest caveats in any other wording (another subject, "nothing in the sources", a compound caveat, "its
  security", a synonym such as "suitability", a "when" or "that" complement, a question without a mapped predicate for
  the topic form). Those are rejected by design and the rules note carries the message; the live R5 paraphrases keep
  9 of 15 as worded (all 15 in their in-grammar rewrites). The Q-clause may still reorder the question's words ("numpy
  2.5.5 secure is"), and a question that itself carries a claim ("Is numpy 2.5.5 secure despite the backdoor?") lends
  its words to the caveat. The bundle check compares words: a caveat beside a supported claim on its subject is
  rejected even when that claim answers something else, and a claim that answers the question without sharing a word
  with the caveat is not seen; the decided-verdict rule covers the case the verdict decides. Number
  grounding checks that a number appears in the cited evidence with the same unit or scale word, not that it means
  the same thing (a bare number is matched by value only; "5m" is not judged). Names and dates written in words are
  not checked. The self-critique is a model judging a model; its marks are shown as flags and remove nothing on
  their own, so an overstated sentence that passes every objective check stays visible, flagged.
- **Entailment coverage (SEC-G-E1), measured.** Replay of every kept model sentence of the v0.7 live runs (retest
  r1, r2, r4, r5, fix r1/r4/r5/r6 live, UAT, rc2: 112 task files, 75 distinct model answers, 130 distinct kept
  sentences) against its own bundle; 107 of them pass the guard without the check. Read by hand: 71 faithful (every
  assertion in the cited claims or titles, paraphrase allowed), 19 grey (a faithful core plus a hedged inference the
  claims do not hold: "…, which may affect compatibility", "which suggests caution in deployment") and 17 unfaithful
  (R5-O1; "Rao and Ballard proposed …" on a title record that names neither; "numpy 2.5.5 is reported as stable"
  on a PyPI release record; "has not been recently updated" for "last updated 6 days ago"). With the check: faithful
  kept 63 of 71 (**89 %**; 8 false rejections, 11 %), grey 2 of 19, unfaithful 1 of 17. The 8 false rejections are
  paraphrases in other words: "warned that superintelligence could manipulate human emotions" for "will hack your
  limbic system", "a range between 62 and 94 billion neurons based on a sample of nine brains" for "variations
  between 62 and 94 bn neurons (n = 9)", "the security status … may be out of date" (the claim says "vulnerabilities",
  not "security"), "AI-related stocks experienced a significant sell-off" for a Nasdaq figure under the title
  "Stocks Plunge as AI Trade Unwinds", "final release details were pending" for "bugfixes before the final
  release". Of the 68 answers that kept at least one sentence, **14 (21 %) keep none** and fall back to the
  labelled extractive template with the rules note; 11 of those 14 had kept only grey or unfaithful sentences (all
  five of UAT "Is numpy 2.5.5 stable?"). The thresholds and word lists were tuned on this same corpus: there is no
  held-out set, so retention on new model output may be lower. Live (2026-11-21, Qwen3-30B-A3B, 10 runs of four
  questions): two fact-verification runs degraded to the template because of this check alone, one sentence saying
  "according to more recent studies" (study, paper, article, review are now evidence nouns) and one adding "though
  estimates vary widely" (now a hedge phrase); every other rejected sentence also failed an earlier rule.
- **Entailment coverage, limits.** It compares words, not meaning. Word order and roles are not checked ("Guido van
  Rossum was first released in February 1991 by Python" passes on "Python was first released in February 1991 by
  Guido van Rossum"); a pronoun's antecedent is not resolved (the one unfaithful sentence kept: "Python was first
  introduced in 2000" on "They were firstly introduced in 2000", about PEPs). Up to a quarter of a sentence's
  content words may be uncovered when none is in a weighted class and its clause is otherwise covered, so a short
  new predicate joined by "and" without an auxiliary passes ("NumPy is the fundamental package for scientific
  computing with Python and powers spreadsheets"). Attribution and hedge words are never content, so "may",
  "estimated" or "estimates vary" add nothing and are not checked. The weighted classes and synonym groups are
  closed lists: an evaluative outside them ("popular" is caught only after a copula) counts like any other word. A
  paraphrase in other words is rejected; the extractive answer and the rules note remain.
- Claim states are the ADR-035 states of `synthesis.decide`, written on each finding before reasoning; a stored
  report without them is read as `not_evaluated` (never upgraded from the legacy label). The model
  sees at most 12 findings; it cannot see evidence the extractive pipeline did not extract.
- A model answer costs ≈ 4–5 s and ≈ 2–4 k tokens per research task, inside the task's `max_seconds` budget.
- Token counts in the ledger are the server's; the context budget before sending is an estimate (3.5 characters per
  token).
- `deploy/install.sh --llm` installs the vLLM unit but neither enables it nor provides Docker or the weights (host
  choices, §5).
- Not done in this release: remote providers were tested only with fakes (no remote endpoint is configured); no
  per-sentence semantic entailment check (the SEC-G-E1 check is lexical coverage, above); voice/PersonaPlex
  integration is area 6.
