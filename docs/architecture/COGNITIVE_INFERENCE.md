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
  normalised, "%" = "percent"; an ambiguous one-letter unit such as "5m" is not judged), or contains
  instruction-like text. `check_sentence` holds these checks.
- **Honest caveats** (`gap_clause`, `insufficiency`, `_parse_gap`). A sentence that only says what the evidence does
  not establish asserts no fact: it needs no supported claim, no hedge and (T2-03) no citation, and it may repeat the
  question's own numbers. The rule fails closed (security review SECREVIEW_GUARD F1–F5): an honest caveat it rejects
  costs little, because the verdict note below states the gap deterministically. A clause is a caveat about the
  question when ALL of these hold:
  1. *Shape.* A negator or absence quantifier (no, not, n't, cannot, never, none, nothing, lack/absence of,
     insufficient, unable/fail to, too little) scopes over an evidence noun or a confirmation verb (evidence,
     information, data, source, indication; confirm, establish, indicate, state, show, determine, verify, address,
     evaluate) with only function words and degree or epistemic modifiers between them, or a knowledge-absence
     adjective (unclear, unknown, unverified) is its predicate; a verbal predicate hangs on an auxiliary and names the
     evidence or says it cannot be done; nothing argues against something (`_COUNTER`: disputes, contradicts,
     otherwise).
  2. *Before the negation* (F3) every word is a question term (the question's own word, or one sharing its stem:
     "security" for "secure"), an evidence noun or a function word (`_PREFIX_WORDS`: determiners, prepositions,
     expletives, aspect nouns such as status/version/release, evidence adjectives such as cited/available/read). An
     attribute, apposition or endorsement ("the fully audited numpy 2.5.5 release") makes it content. An embedded
     question ("Whether numpy 2.5.5 is secure cannot be determined") may hold one verb group; "If ..." is no lead.
  3. *After the evidence predicate* (F2) what is missing is named only as an embedded question (whether/if, one verb
     group; its words are free in a cited caveat because it asserts nothing) or as a topic of question terms and
     function words ("about / on / regarding / of the security of numpy 2.5.5", "addresses its security"). A
     that-complement ("no evidence THAT numpy 2.5.5 is not secure", also "no clear indication that X is secure") and
     an existential complement ("reports ANY / A SINGLE vulnerability", "no sign OF vulnerability") are absence
     claims, not caveats; a second negation or a second finite verb outside the embedded question ends it.
  Clauses (F1) break at every comma, semicolon, colon, dash, bracket, quote, ellipsis and sentence end, and at
  causal and subordinating conjunctions (so, thus, hence, therefore, because, when, which, but, though ...) and at
  "and/or" before a new subject or verb; a leading discourse marker ("However, ...") is stripped first. A sentence is
  a caveat only when every clause is one. The guard reads the sentence NFKC-normalised without format characters
  (zero-width spaces, direction marks), and its words in any script.
  *Absence claims and the bundle* (F2, `_absence_problems`): a clause with the shape of rule 1 that is not a caveat
  and says the evidence holds nothing ("No source reports any vulnerability in numpy 2.5.5"), or has a that- or
  existential complement, is rejected in any sentence, cited or not: the guard cannot check a claim about what the
  evidence lacks. A caveat that passes is compared with every bundle claim: one that says the evidence holds nothing
  ("no source states", "does not address") is rejected when ANY claim contains its topic words (the words that are
  not the question's, else all of them); one that says the evidence is too weak ("cannot be confirmed",
  "insufficient evidence", "unclear") when a SUPPORTED claim does.
  *Uncited caveats* (F5) may hold only the question's terms, stopwords, evidence nouns and verbs, negators and
  function words; no non-ASCII letter (a Cyrillic "О" in "Оpenssl"); no number in words ("twelve"). Digits are left to
  the number check, which allows only the question's own inside a caveat.
  The same negation scope decides the other checks: "cannot be confirmed" calls nothing confirmed, "not reported" or
  "does not indicate" attributes (hedges) nothing, and a caveat clause does not hedge the factual clause beside it,
  so "X fixes a critical flaw, but the evidence does not indicate whether it is recommended" is judged on "X fixes a
  critical flaw". "Gaps include ..." is no caveat (F4): gaps belong in the schema's `gaps` field. Caveats the strict
  rule rejects on purpose are listed with their reasons in `REJECTED_CAVEATS`
  (tests/inference/test_inference_reasoning.py).
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
  said by voice. F6: the statement must be one plain clause (one verb group, no URL or domain, no address to the
  reader or the model, no instruction), else there is no note. An autonomous task's statement (curiosity, refresh)
  may come from web text: without the owner's authorisation of the proposal there is no note; with it the statement
  is quoted as the question in guillemets ("NeuroSciFi note: «numpy 2.5.5 is recommended?» is a judgement the
  evidence does not establish."). `apply(asked=owner|confirmed|autonomous)`; the orchestrator derives it from the
  task's origin and its "proposal authorised by the owner" decision.
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
  The caveat rule reads structure from word classes, not a parse. What bounds it is vocabulary: outside an embedded
  question a caveat may hold only the question's terms, evidence words and function words, so it can say nothing
  the question did not ask; inside a cited caveat's "whether ..." the words are free (an embedded question asserts
  nothing, though it can presuppose: "whether the audited release is secure"). Question terms match by a shared
  5-letter prefix, so a near word of the question ("securities" for "secure") counts as the question's. The bundle
  check compares words, not meaning: "None of the sources addresses the security of numpy 2.5.5" passes beside a
  supported claim of a critical vulnerability (no shared topic word), and an absence claim about a subject the
  claim does not name is not matched; the verdict and the rules note carry that case. Honest caveats are lost by
  design: that-complements, off-question topics, synonyms the question does not use ("suitability" for
  "recommended"), "when" complements. The live R5 paraphrase set keeps 14 of 15. Number
  grounding checks that a number appears in the cited evidence with the same unit or scale word, not that it means
  the same thing (a bare number is matched by value only; "5m" is not judged). Names and dates written in words are
  not checked. The self-critique is a model judging a model; its marks are shown as flags and remove nothing on
  their own, so an overstated sentence that passes every objective check stays visible, flagged.
- Claim states are the ADR-035 states of `synthesis.decide`, written on each finding before reasoning; a stored
  report without them is read as `not_evaluated` (never upgraded from the legacy label). The model
  sees at most 12 findings; it cannot see evidence the extractive pipeline did not extract.
- A model answer costs ≈ 4–5 s and ≈ 2–4 k tokens per research task, inside the task's `max_seconds` budget.
- Token counts in the ledger are the server's; the context budget before sending is an estimate (3.5 characters per
  token).
- `deploy/install.sh --llm` installs the vLLM unit but neither enables it nor provides Docker or the weights (host
  choices, §5).
- Not done in this release: remote providers were tested only with fakes (no remote endpoint is configured); no
  per-sentence semantic entailment check; voice/PersonaPlex integration is area 6.
