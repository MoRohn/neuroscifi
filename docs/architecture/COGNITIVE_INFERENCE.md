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
- **Request: what the model is asked to write (v0.7.0 prompt lane).** The request is aligned with the guard below,
  whose rules are unchanged. The system prompt (`SYSTEM`) asks for at most 5 one-sentence items, each a close
  restatement of the claim(s) it cites: the claims' own words, names, numbers, units, dates and negations; nothing
  added (no cause, consequence, comparison, prediction, judgement, scope word, time word or instruction); no hedge, no
  attribution, no statement about agreement or verification, no caveat and no source id. Its worked example and the
  attribution it shows are generated from `PROMPT_EXAMPLE` and `HEDGES`, which a test checks against the guard: the
  good sentence passes, every bad one is rejected. The model sees a compact view of the bundle (claims: id, state,
  text, source ids, dates; sources: id, title, dates; the verdict's state and statement), not confidence, category,
  domain, retrieval date, contradiction count or freshness.
- **Answer.** Streaming structured output (`answer_schema(b)`): `{"sentences": [{"claims": ["C1"], "text"}], "gaps":
  []}`. The claim ids come first (guided decoding generates them before the text), 1 to 3 per sentence, limited to the
  bundle's ids by an `enum`; the model writes no source ids.
- **Composition** (`compose`, before the guard). Each sentence's sources are the cited claims' own sources, so a claim
  is never cited through a source that does not carry it. A sentence citing a claim that is not `supported` gets the
  attribution of its weakest cited state appended by code, so the hedge cannot be forgotten or misworded: "…, according
  to one source." (`insufficient`, `not_evaluated`; "the cited sources" for two or more), "…, according to one source,
  possibly out of date." (`temporally_unresolved`), "…, though the sources disagree." (`conflicting`), "…, though the
  claim is disputed." (`disputed`). The model's own leading or trailing "according to" a source word ("one source",
  "the cited sources", "a report", "the release notes", "the benchmark") is replaced; any other "according to" ("handles
  strings according to the Unicode standard") is content and stays. A leading "Yes,"/"No," (an answer no claim gives;
  never the "No" of "No-one") and a repeat of the same text citing the same claims are removed. When a sentence
  cites several claims, a claim that supports none of it (none of its content words other than names: "NumPy 2.5.5
  was released on 17 November" beside "NumPy 2.5.5 ships a backdoor") is dropped from its claims and sources, and the
  attribution counts only the claims that support it ("according to one source", not "the cited sources";
  SECREVIEW5 C3; since SECREVIEW6 a claim must cover a word of the sentence's predicate, from its first finite verb
  on, not only its subject or setting). A caveat of the closed grammar, a question and a sentence the guard will reject
  as relayed instruction content (`_relays_instruction`: instruction words, or, beside a weak claim, a sentence that
  is not declarative) get no attribution. **What is guaranteed (SECREVIEW6):** evidence that is not `supported` never
  yields an instruction or any non-declarative sentence, hedged or not — the guard rejects the sentence when it, or
  the weak claim's own text, fails a fail-closed declarative test (below), and never repairs it (R1-04); beside
  `supported` claims an instruction passes only when a cited claim instructs the same verb with the same object.
  Tested end to end through `compose` and through `apply` with a model that obeys. What the evidence does not
  establish is left to the rules note (`verdict_note`). The guard judges every composed sentence as it judged the
  model's. The voice "simpler" answer (`voice/bridge.py`) uses the same schema and composition.
- **Guard** (the authority). A sentence is rejected if it cites no source or no claim, cites an id not in the bundle,
  cites a claim through a source that does not carry it, relies on a claim whose ADR-035 state is not `supported`
  (a time-sensitive claim backed only by stale or undated sources is `temporally_unresolved`; the legacy
  `corroborated` label never counts) without hedging in its own words (local patterns: "according to", "one source", "may", "disputed", …),
  calls a non-supported claim verified/confirmed, contains a number that is in none of its cited claims or their
  sources' dates/titles (a small count before "sources" is allowed; "4.2" matches "4.2.0"), gives a number a unit
  or scale the cited evidence does not give that number (`_unit_mismatches`: "41 feet" vs "41 metres", "86 billion"
  vs "86 million", "17 days" vs "17 November", "$1.5 million" vs "1.5 billion dollars"; units and scale words are
  normalised, "%" = "percent"; an ambiguous one-letter unit such as "5m" is not judged), says more than the claims
  it cites (SEC-G-E1, below), or contains instruction-like text. `check_sentence` holds these checks, and (SECREVIEW5)
  three more:
  - **Weak evidence carries only declarative sentences** (SECREVIEW6 rule 1, `_non_declarative`, fail closed) —
    "relays an instruction or a non-declarative sentence from weak evidence" — when any cited claim is not
    `supported`, the sentence and each such claim's own text must be declarative. After a closed list of discourse
    and attribution leads ("however", "according to one source", "possibly out of date"), every clause — split at
    punctuation, at but / so / then / while / whereas / because / although / though / unless, and at and / or before a
    verb — must begin with a subject token: a determiner, quantifier or pronoun; a number or version; a name (a
    capitalised word the cited claims or the question write capitalised away from a sentence start, a mixed-case,
    all-capital or dotted word they hold, a capitalised word of a short or sentence-case source title, a word right
    before a version as in "numpy 2.5.5", or the first word of a name: "Van Rossum"); a word in -s that is no
    stopword; or a gerund that a finite verb follows. A clause headed by a preposition, "To", a participle or a gerund
    is a lead only when a comma and a clause with a subject follow ("In 2026, …", "Released in May, …"); a fragment
    after a comma ("…, with breaking changes", "…, released on 15 November") may hold no verb before its object
    ("…, uninstall it"); "and is …" continues the predicate; a verb its auxiliary began before an adverb ("It does,
    however, use …") continues it. Anything else at a clause head is non-declarative: a bare verb ("Skip …", "Hold off
    …", "Roll back …"), a lowercase or sentence-initial-only capitalised word, "Let's", a preposition without a comma
    ("For safety uninstall …"). Non-declarative anywhere: obligation and recommendation words (should, must, ought,
    recommended, urged, need to, essential / required … to), "you/your" (not a generic one: "depending on what you
    mean", "if you count"), "is to (be)", "the fix / remedy / solution is", recommendation phrases. The claims are a
    part-of-speech oracle: a head word they hold only inflected as a verb ("skips" for "Skip") is a bare verb.
    NeuroSciFi's own record claims ("Crossref record: \"T\"", "OSV lists advisory X: …") are checked from their
    data on. A false positive drops only a sentence that cites weak evidence; the extractive evidence stays.
  - **Supported instructions are exactly backed** (rule 2, `_instructed`) — "gives an instruction its cited claims do
    not give" — each instructed (verb, object head) of the sentence (the verb after an obligation word, the object of
    "recommend", a clause head that is no subject and looks verbal) must be an instructed (verb, object head) of a
    cited claim: "Users should downgrade NumPy 2.5.5" is not backed by "Users should upgrade to NumPy 2.5.5", nor
    "… and remove plugins" by an instruction to upgrade beside the fact that 2.5.5 removes plugins.
  - **Versions and names keep their predicate and polarity** (rule 3, `_anchor_problems`) — "ties a version or name
    to what its cited claims do not say of it" — "v2.5.5" is read as 2.5.5; for a version, number or name in both the
    sentence and a cited claim, the content words that the sentence's clause holding it shares with a claim clause
    holding it must have the same polarity there ("NumPy 2.5.5 is affected …" for "… NumPy 2.5.5 is not affected"),
    a clause that shares none may not take the words a claim says of another version or name ("2.5.5 has the parser
    flaw" for "2.5.4 has the parser flaw and 2.5.5 has the fix"), and the content word right before a version names
    what it is ("the fix in NumPy 2.5.4" for "affected NumPy 2.5.4 before the fix in NumPy 2.5.5"). Clauses here end
    only where a new subject or a contrast starts, not at every comma, and two versions of one such clause keep their
    claim's order. A claim clause that names nothing else ("… and it is not cryptographically secure") may support a
    name's polarity.
  - **A cited claim that supports none of the sentence's predicate** — "cites C1, which support(s) none of what it
    says" — when a sentence cites several claims (C3, `_claim_supports`; rule 4).
  - **Meaning changes** (R6 retest D5, `_meaning_changes`): words of at most four that a cited claim puts in (scare)
    quotes, repeated without them ("a number of 'facts' about the human brain"); a "not merely / not only / not just
    X" without its complement.
  A code name the cited claim holds ("numpy.dtypes.StringDType") ends no sentence; "several / other / different
  sources" in the cited claim's own words after a preposition ("a comparison of different studies") attributes
  nothing (R2-03).
- **Entailment by content-word coverage (SEC-G-E1, `_unentailed`).** Until v0.7.0 a sentence that cited any
  supported claim was not checked for what it said: citing "NumPy is the fundamental package for scientific computing
  with Python." passed "NumPy is secure and recommended for all production use." and "Users should reinstall NumPy
  from the official mirror immediately."; live (R5-O1) a kept sentence called "the widely accepted figure of 100
  billion neurons" "not definitively established" on a Crossref title record that says neither. Every sentence that
  is not a caveat of the closed grammar is now checked, deterministically and without a model:
  - *Content words* are the sentence's words without stopwords, digits and the unit after a number (the number and
    unit checks own them), attribution and hedge vocabulary ("according to one source", "states that", "may", "may
    be out of date", "temporally unresolved", "not yet confirmed", "estimates vary widely") and its caveat-grammar
    clauses. Words that assert something (supported, unsupported, outdated, known, believed, considered, one, two,
    several) are content, not attribution (round 4, SECREVIEW4_ENTAIL P1/P2).
  - A content word is *covered* when a cited claim holds it: exactly, by stem (singular/plural, "-able":
    "exploitable" meets "exploited"), in a closed synonym group (released/published/launched, vary/range,
    rely/depend, bn/billion), or by a common 6-letter prefix when neither word is a verb form (-ed, -ing:
    "exploitation" does not cover "exploited", "maintainers" not "maintained"). An acronym is covered only literally
    ("RCE" is not "reported crash errors"); a cited source's title covers names only (a word with a capital letter in
    the sentence), never a predicate.
  - Rejected — "says more than its cited claims support: secure, recommended, …" — when under 75 % of the content
    words are covered (`ENTAIL_COVERAGE`), when a clause of two or more content words has under a third covered
    (`ENTAIL_CLAUSE`), when a clause holds more than one uncovered content word (`ENTAIL_MAX_NEW_CLAUSE`) or the
    sentence more than two (`ENTAIL_MAX_NEW`), or when **any** uncovered word carries assertion weight. Those
    classes are checked against the claims' own words only: evaluatives (secure, safe, recommended, advisable,
    outdated, stable, best, critical, …), names, scope words (all, every, only, universally, …), comparatives (than,
    faster, more, outperforms, surpasses, …), time words (yesterday, currently, newly, recent, month names, …),
    quantities in words (one, two, several, multiple, many, …; "one source" is attribution), an adjective after a
    copula ("is popular", "is unaffected"), a new verb after "and" ("… and leaks memory") and a new verb right after
    a name or version ("NumPy 2.5.5 contains …"). An embedded whether/if question is exempt from the clause rules.
    Since SECREVIEW5: any new word right after a copula is a predicate, not only adjective-shaped ones ("is faulty",
    "is exposed", "is a priority", "is behind", "was created by"); an uncovered "-ly" adverb is weighted ("handles
    strings insecurely", "badly", "poorly"); "handful", "couple", "dozen", "lot" are quantities.
  - Instructions — "gives an instruction its cited claims do not give: should" — a modal of obligation (should,
    must, need, recommend, advisable, …), an address to the reader (you, everyone, anyone) or a recommendation phrase
    ("makes sense", "the way to go", "would do well", "the recommended path") fails unless a cited claim holds
    instruction words too, and an instructed verb unless it is exactly backed (rule 2 above).
  - Polarity (`_polarity`). Negators (not, no, never, n't, without, …) scope forward over the next content words
    (in the sentence over the rest of the clause); privative words (lack, fail, missing, absent, drop, remove, stop,
    abandon, strip, revert, spare, immune, "free of", …) do too, and after a copula also back over the subject ("the
    fix is missing", "support was dropped", "the fix was reverted", "is free of the flaw"); "dropped by 30 percent" is
    an amount, not a negation. "No-one" and "no one" negate like "nobody", "none", "never" and "zero" (SECREVIEW5 C2:
    "No-one has exploited …" read as one word lost its negation in round 4). In the sentence a negator's scope ends
    at the next negator with a content word before it and at "and"/"or" before a new subject ("… and NumPy 2.5.4 is
    patched"); two negators with no content word between them ("not without", "does not lack") are rejected unless a
    cited claim holds the same pair ("states a double negation its cited claims do not state"); an "un-" noun the
    sentence derives from the claim's "un-" word ("uncertainty" for "uncertain") negates nothing new. "It is not
    true that …" negates the rest, and
    an even number of negators over a word is no negation. Hedge heads ("not believed to be", "not known to be") are
    skipped, so they never cancel a claim's negation. "unX" is X negated. A negator over an evidence word ("not
    established", "does not indicate", "lack recent or verified evidence") is a hedge. Rejected: the first word a
    sentence negator negates when no cited claim negates it or stands next to a negation ("negates what its cited
    claims do not negate": "is not affected", "lacks the fix", "is unsupported"); and the first word a claim negator
    negates when the sentence states it unnegated and no claim states it so ("drops the negation of its cited
    claims": "is affected" for "is not believed to be affected"), whatever other negator the sentence holds.
  - Role swaps: a past-tense verb shared with a claim whose subject in the sentence is not its subject in the claim
    but stands after the verb there ("The legacy branch abandoned the maintainers") — "changes who does what in its
    cited claims"; in two passives, an agent after "by" that is not the claim's agent of that verb but stands
    elsewhere in the claim ("fixed by Google and reported by the maintainers" for "reported by Google and fixed by
    the maintainers").
  The check runs after the number and unit checks, so the attribution repair (R1-04) and the self-critique
  confirmation use it unchanged. Tests: `test_e1_*` (the two E1 examples, R5-O1, 36 attacks, all 47 cases of the
  round-4 review and "the way to go", each rejected; 17 faithful paraphrases and 4 live sentences that must stay
  kept; the measured costs, asserted as rejections) and `test_r5_*` (SECREVIEW5: the relayed instructions end to end
  through `compose` and `apply`, in four claim states; "No-one", "no one", "nobody", "zero", "never"; the citation of
  supporting claims only; 27 entailment attacks of its section 3; the prompt lane's proposals A–E, attacks and
  faithful cases).
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
  on a PyPI release record; "has not been recently updated" for "last updated 6 days ago").

  | | first version (round 3) | after the round-4 review | after the round-5 review |
  |---|---|---|---|
  | faithful kept | 63 of 71 (89 %) | 51 of 71 (72 %) | **50 of 71 (70 %)** |
  | grey kept | 2 of 19 | 1 of 19 | 1 of 19 |
  | unfaithful kept | 1 of 17 | 1 of 17 | 1 of 17 |
  | answers (of 68 with a kept sentence) falling back to the template | 14 (21 %) | 19 (28 %) | **20 (29 %)** |

  (These sentences were written for the earlier answer request, which asked for hedges and attribution in the
  model's own words; the aligned request is measured below. Round 5 cost one more: "…, and recommends manual
  verification" restated from an `insufficient` claim, a relayed recommendation.)

  Retention is now below the 85 % aimed at. The 20 false rejections are paraphrases in other words:
  "warned that superintelligence could manipulate human emotions" for "will hack your limbic system"; "a major math
  problem" for "a Holy Grail of math problems"; "AI-affiliated individual of solving" for "someone linked to one
  claims to have cracked"; "a range … based on a sample of nine brains" for "variations … (n = 9)"; "open-source AI
  software" where the claim says "artificial intelligence" (acronyms only literally); "the security status … may be
  out of date" (the claim says "vulnerabilities"). Ten of them are lost to the per-clause and per-sentence caps
  alone: without the caps 61 of 71 (86 %) and 13 fallbacks; with two per clause and three per sentence 59 of 71
  (83 %) and 14 — but "… fixes bugs discovered after the 2.5.4 release including backdoors" then passes. The caps
  stay; aligning the model's prompt to the guard (a separate lane) is the way to raise acceptance. The thresholds and
  word lists were tuned on this same corpus: there is no held-out set. Live (2026-11-21, Qwen3-30B-A3B, 10 asks of
  the four questions, first version of the check): two fact-verification runs degraded to the template because of
  this check alone ("according to more recent studies", now an evidence noun; "though estimates vary widely", now a
  hedge phrase); every other rejected sentence also failed an earlier rule.
- **Round-5 guard with the aligned request, measured (2026-11-22).** The 44 answers of the paired replay below
  (the aligned request), composed and guarded again: with the c7eede8 guard template fallback 8 of 44 (all empty
  answers), 3.07 kept per answer, 7 rejected, survival 95 %; with this guard 8 of 44 (the same empty answers), 3.18
  kept per answer, 4 rejected, survival 97 %. Code names no longer split a sentence (+6); the 4 rejections are the
  same zod sentence relayed from an `insufficient` claim, which says "from your own dependencies" (reader address,
  C1). A fresh live run of the same 44 bundles (new model answers, this guard): template 8 of 44 (all empty), 3.11
  kept per answer, survival 96 %; its 5 rejections are all C1 (the zod "your" four times, and "A user who can freely
  execute NumPy functions must be considered to have the same privileges …", where "must" is epistemic). The same
  fresh answers under the c7eede8 guard: 3.05 kept, survival 94 % (8 code names read as sentence ends).
- **Round-6 guard (structural rules), measured (2026-11-22).** Same inputs as above. The 44 answers of the paired
  replay: template 8 of 44 (the empty answers), 3.07 kept per answer (3.18 with the round-5 guard), 9 rejected,
  survival 94 % (97 %); the new rejections are a mailing-list "Hi All, …", a "Prediction: …" headline, a commit title
  "#32221: BUG: avoid segfaults …", "Here, we provide …" (non-declarative, as intended) and the zod "your" (C1). The
  fresh live answers of round 5: 8 of 44, 2.91 kept, survival 90 % (3.11, 96 %), adding the two copies of "a number
  of facts about the human brain" without the claim's scare quotes (D5). A fresh live run with this guard (44 new
  answers): template 8 of 44 (all empty), 2.84 kept per answer, survival 89 %; its 15 rejections are all of the
  classes above (scare quotes 3, the zod "your" 4, headlines and commit titles 4, an epistemic "must", a list item
  "translation models such as …", a claim's "release notes and the changelog are available …", "Here, we …").
  The 107 kept live sentences of the earlier
  request: faithful kept 45 of 71 (63 %; 50 with the round-5 guard), grey 1 of 19, unfaithful 1 of 17; template
  fallback 24 of 68 answers (20). The structural test costs sentences that open with a generic noun or adjective
  ("Predictive coding offers …", "Biased or inaccurate training data …", "Exploitation requires …"), list items after
  a comma or "and" that look like a verb and its object ("…, infrastructure updates, and compromise fixes"), a
  modal in its epistemic sense ("should, with few exceptions, continue to compile"), "is to minimize", and a
  capitalised name the claims write only at a sentence start when no title names it — each only beside weak evidence.
- **Declarative test, limits.** It is lexical and fails closed: what it cannot read as a subject is rejected beside
  weak evidence (above). What it accepts as a subject can still head an instruction in a few shapes: a word in -s
  ("Focus on numpy-secure"), a gerund with a finite verb later in the clause, a name the claims or a short title
  hold. Beside supported claims an imperative is recognised only when it looks verbal (a determiner, pronoun,
  particle or code word follows, the claims hold it inflected, or it is a common action verb); an unknown verb before
  a plural-noun object is then judged only by coverage (one new word per clause, a new verb after a name). Rule 3
  compares words: a paraphrase with no shared word, a pronoun's antecedent, and a version in a clause with no other
  content word are not judged.
- **Entailment coverage, limits.** It compares words, not meaning, and one new content word per clause is allowed:
  a noun or verb swapped for one the claim does not hold passes when it is the only new word in a clause of the
  claim's words and is not right after a name, a version or "and" ("NumPy is the fundamental package for scientific
  espionage with Python"; "The new parser raises memory use in NumPy 2.5.5 by 30 percent" for "reduces"). Role swaps
  are caught only for a past-tense verb the sentence shares with the claim and for swapped "by" agents of two
  passives: a present-tense swap ("NumPy 2.5.5 users target attackers" for "Attackers target NumPy 2.5.5 users") and
  an object swap with the same verb ("replaces the new loader with the legacy parser") pass. Two versions in one
  clause keep their claim's order (a passive that reorders them is rejected, a cost); across clauses rule 3 ties each
  to its own predicate. An antonym outside the closed privative list counts only as an uncovered word:
  "NumPy 2.5.5 is clear of the parser flaw" passes on "NumPy 2.5.5 is affected by the parser flaw". A pronoun's
  antecedent is not resolved (the one unfaithful sentence kept: "Python was first introduced in 2000" on "They were
  firstly introduced in 2000", about PEPs). Polarity is lexical: negators, privatives and "unX" are closed lists
  ("hardly", "barely" are caught only as uncovered words), and a sentence negator passes when its first
  negated word stands within a few words of a claim's negation. Attribution and hedge words are never content, so
  "may", "estimated" or "estimates vary" add nothing and are not checked. The weighted classes, recommendation
  phrases and synonym groups are closed lists. A paraphrase in other words is rejected; the extractive answer and
  the rules note remain.
- **Prompt alignment, measured (2026-11-21, Qwen3-30B-A3B, live).** A fixed set of 11 questions (numpy 2.5.5
  secure / recommended / stable, Python's first release, the 100 billion neurons statement, AI this week, predictive
  coding, Makemake's and Jupiter's moons, zod 4 stable, the boiling point of ethanol), asked twice each on a fresh
  instance with the d9cbe22 request and with this one; the guard is the same in both (the round-3 entailment check; the combined measurement with the round-4 check follows in this section). Template fallback (no model
  sentence kept): **8 of 22 → 3 of 22**; the 3 are answers in which the model wrote no sentence at all (ethanol twice:
  no claim gives the boiling point; predictive coding once), none is a guard rejection. Kept sentences per answer
  0.73 → 3.09 (distinct), sentences rejected 46 → 3 (survival 26 % → 96 %). Rejections before: says more than its
  claims 36, unhedged state 15, negation 6, caveat outside the grammar 6, citation 4, caveat beside a supported claim 2,
  number 2, disagreement 1, two sentences 1; after: "holds more than one sentence" 3 (a dotted name,
  "numpy.dtypes.StringDType", read as a sentence end). Model time per answer (answer and critique) median 6.9 s →
  7.8 s, mean 10.0 s → 6.4 s; whole ask median 17.1 s → 15.4 s; prompt and completion tokens (both calls) median
  2 574 → 3 030 and 266 → 338, because more sentences reach the critique. Hand check of every kept sentence: before,
  12 of 16 faithful, 3 grey, 1 unfaithful ("predictive coding is related to neural coding" on a claim about neural
  coding only); after, 62 of 68 faithful, 2 grey (a "facts" in scare quotes restated without them; a numpy 2.5.2 bug
  fix restated without its version under "Is numpy 2.5.5 secure?"), 0 unfaithful, and 4 that assert nothing useful:
  three paper or article titles phrased as questions, attributed by the guard's own repair (R1-04), and a
  "Prediction: …" headline. The price of faithfulness is closeness: the model now mostly copies claims, so 13 of the
  68 open with a word that points back ("It", "This release", "Here, we", "The company") and depend on their
  neighbours, some restate bibliographic records, and an answer can include an off-topic claim; it repeated a
  sentence in two answers (4 repeats, now removed by `compose`, not counted above). A paired replay removes the
  search variance: the answer call alone, once with each request, on the same 44 bundles of those runs, the two run
  side by side. Template fallback 14 of 44 → 8 of 44, all 8 empty answers (ethanol 4, predictive coding 3, AI this
  week 1; before, every fallback was a guard rejection); kept per answer 0.91 → 3.09; survival 32 % → 96 %; answer
  call median 6.0 s → 6.5 s, mean 6.1 s → 5.3 s; prompt tokens median 1 686 → 1 544 (the compact evidence view
  outweighs the longer prompt), completion tokens median 190 → 192. The model now writes nothing more often when
  the claims are weak; for ethanol that is right (no claim gives the boiling point), for predictive coding it is not
  (a claim defines it). The guard remains the only authority: composition adds only attribution words it already
  accepts.
- Claim states are the ADR-035 states of `synthesis.decide`, written on each finding before reasoning; a stored
  report without them is read as `not_evaluated` (never upgraded from the legacy label). The model
  sees at most 12 findings; it cannot see evidence the extractive pipeline did not extract.
- A model answer costs ≈ 4–5 s and ≈ 2–4 k tokens per research task, inside the task's `max_seconds` budget.
- Token counts in the ledger are the server's; the context budget before sending is an estimate (3.5 characters per
  token).
- `deploy/install.sh --llm` installs the vLLM unit but neither enables it nor provides Docker or the weights (host
  choices, §5).
- Remote providers: the remote code path is verified live against the local vLLM rehearsed as a remote provider
  (§7). A real external provider is owner-side: `tests/inference/test_inference_remote_live.py` runs against one.
- Not done in this release: no per-sentence semantic entailment check (the SEC-G-E1 check is lexical coverage, above);
  voice/PersonaPlex integration is area 6.

## 7. Remote providers: live contract (2026-11-22)

`tests/inference/test_inference_remote_live.py` exercises the gateway's remote path against a real OpenAI-compatible endpoint. It is skipped unless `NSF_LIVE_REMOTE=1`.

```sh
# a real external provider (owner-side)
NSF_LIVE_REMOTE=1 NSF_REMOTE_URL=https://api.example.com/v1 NSF_REMOTE_KEY_FILE=/abs/path/key \
  [NSF_REMOTE_MODEL=name] [NSF_REMOTE_CA=/abs/ca.pem] pytest -q tests/inference/test_inference_remote_live.py
# the local vLLM rehearsed as a remote provider (what ran here)
NSF_LIVE_REMOTE=1 NSF_REMOTE_UPSTREAM=127.0.0.1:8811 NSF_REMOTE_KEY_FILE=~/.config/neuroscifi/inference/api_key \
  pytest -q tests/inference/test_inference_remote_live.py
```

**The rehearsal setup:**

- A TLS proxy fronts vLLM, with a certificate for `inference.remote.test` from a throwaway `nsf pocket tls` CA. The test process trusts only that CA (`SSL_CERT_FILE`).
- The locality decision is fixed to `remote`, and the connection is pinned to 127.0.0.1.
- Everything after the locality decision is production code: the owner's `remote_allowed`, https only, the privacy screen and private-memory consent, SNI and certificate verification against the configured name, the whole-call deadline, retries and the ledger.
- The key is read from its 0600 file and never printed.

**Result: 8 passed, 1 skipped**, against Qwen3-30B-A3B-Instruct-2507-FP8 in vLLM. The skipped test is the CLI test, which needs a really remote address.

| Contract | Observed |
|---|---|
| Owner authorisation | without `remote_allowed`: `remote_not_authorised`, health `not_configured`, no connection attempted; with it: health `ready` with a probe |
| https only | the same endpoint over `http://`: refused before connecting |
| Chat and ledger | reply, server token counts; ledger row `locality=remote`, tokens, latency; a marker string from the prompt absent from the ledger file |
| Streaming | ≥ 3 deltas, first-token time recorded (ledger `stream=1`) |
| Structured output | a JSON Schema answer validated locally (Paris, France, a population number) |
| Tool allow-list | the model called the allow-listed `release_lookup`, which ran and returned its value into the answer; a forced call to an unlisted `forget_memory` was refused ("not on the allow-list") and never executed; an ungranted permission offers no tools |
| Timeouts | `read_timeout` 1.0 s on a 2000-token request: `timeout` raised at the deadline and recorded in the ledger; the same for a stream |
| Consent and privacy gate | a message marked `private` without `remote_private_memory`: `private_blocked`. E-mail, phone and API-key text: `private_blocked`. In every blocked case no connection was opened (gateway spy and proxy connection count unchanged) and no ledger row written. Owner-approved disclosure sends the screened text; `remote_private_memory` sends private memory but does not switch off the identifier screen |

Only the gateway marks and gates private messages: no current caller sends memory marked `private`. Research synthesis sends the question and public web evidence, both of which pass the screen.
