"""Semantic memory retrieval: an optional ranking of knowledge.query by meaning (docs/web-cortex/MEMORY_INTEGRATION.md).

Off until configured. When it is off, not configured or fails for any reason, knowledge.query returns its lexical
ranking exactly as before, and says so in `retrieval`.

Why. The lexical ranking (BM25 over a statement's own words) finds a fact when the question uses its words. Measured
on 118 queries over 48 facts (scripts/bench_memory.py): a paraphrase finds the right fact first 27 % of the time, a
description that does not name the subject finds it 6 times in 12, and a question about something that is not in
memory still returns unrelated facts 10 times in 10.

How. Two small models behind the inference gateway (providers `embed` and `rerank`, inference/config.py), each
optional:

  embed    one vector per live statement and one for the question; the nearest statements are the candidates
           (with the best lexical hits added, so a rare exact word is never lost)
  rerank   a cross-encoder reads the question with each candidate and scores it; the candidates are returned in
           that order, and one scoring under `floor` is not returned at all (nothing in memory is about this)

A failed call falls back at once (no retry) and the models are left alone for RETRY_AFTER seconds, so an endpoint
that is down costs one failed connection per half minute, not a wait on every lookup.

A lookup never waits for a model. When the owner lets the idle policy release the local memory models
(resources/idle.py: `nsf resources idle --set embed=15m --set rerank=15m`, off by default) and a lookup finds one
unloaded, it is ranked by its words at once (`retrieval.waking` names what is starting) and the reload runs in the
background, so a later lookup is ranked by meaning again. Only this host's own services (deploy/memory, by their
loopback ports) are the policy's: an endpoint elsewhere is called as it is.

With only `embed` the candidates are returned by vector similarity and nothing is cut: similarity alone does not
separate "about this" from "about something similar" (measured), so no floor is applied without the reranker.

What it is not. It is not a second memory: there is no vector store. Vectors live in this process only, keyed by
the statement's text, and nothing derived from memory is written to disk. The set is trimmed to the live statements
on every query, and knowledge.forget drops this process's vectors at once. Limit: another long-running process (the
worker, the console) keeps the text and vector of a statement forgotten elsewhere until its own next memory lookup
or its exit. It ranks and filters
what knowledge.query returns; it does not decide whether memory answers a question (intent.assess and the
orchestrator's memory check do), it does not merge or compare knowledge, and it never changes a statement, a
confidence or a verification state.

Privacy. Statements and questions go to the configured endpoints through Gateway.embed / Gateway.rerank, which mark
every text private: an endpoint off this host is refused unless the owner authorised private memory for it.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
import re
import threading
import time

import numpy as np

from ..instance import store
from ..instance.errors import ValidationError

META_KEY = "semantic"
TEMPLATES = ("qwen3", "plain")
# Qwen3-Reranker's own prompt (its model card and chat_template.jinja): without it the model's scores are not
# usable (measured: recall@1 0.79 near, 0.25 unnamed; with it 1.00 and 1.00).
_QWEN3_PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the '
                 'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
INSTRUCTION = "Given a topic or a question, judge whether the stored fact is about that topic or helps to answer that question"
EMBED_BATCH = 64
LEXICAL_KEEP = 5            # best lexical hits always among the candidates
QUERY_CACHE = 256
RESULT_CACHE = 64
RETRY_AFTER = 30.0          # seconds a failed memory model is left alone before the next lookup tries it again


@dataclass
class SemanticConfig:
    enabled: bool = False          # the owner's switch; the endpoints are `nsf web memory config --embed-url/...`
    candidates: int = 10           # nearest statements sent to the reranker (and returned without one)
    floor: float = 0.02            # reranker score under which a statement is not returned (0 = return all)
    timeout: float = 2.0           # seconds for one memory-model call; past it the lexical ranking is used
    rerank_template: str = "qwen3"  # how the question and a statement are written for the reranker (TEMPLATES)
    # Contradictions in other words (pairs(); consolidate.propose): when a new statement shares too few words with
    # anything known, the memory models may propose the stored statement that states the same property of the
    # same subject, and the value comparison of consolidate.py decides what the pair is. It needs the reranker
    # and, with pair_check, the answer model. On whenever semantic memory is on: with pair_check the held-out
    # half of the benchmark has no false pair in 23 (8 of them the hard kind); without it, 3 (MEMORY_INTEGRATION).
    contradictions: bool = True
    pair_similarity: float = 0.6   # embedding similarity under which a stored statement is not a candidate
    pair_floor: float = 0.8        # reranker score (both directions) under which a candidate is not a pair
    pair_check: bool = True        # a pair must also be confirmed by the answer model (consolidate._pair_judge)

    @classmethod
    def from_json(cls, text: str | None) -> "SemanticConfig":
        raw = json.loads(text) if text else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> dict:
        return asdict(self)


def _validate(name, value):
    if name in ("enabled", "contradictions", "pair_check"):
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be true or false")
        return value
    if name in ("pair_similarity", "pair_floor"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not 0.0 <= value <= 1.0:
            raise ValidationError(f"{name} must be a number between 0 and 1")
        return float(value)
    if name == "candidates":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValidationError("candidates must be an integer between 1 and 100")
        return value
    if name in ("floor", "timeout"):
        lo, hi = (0.0, 1.0) if name == "floor" else (0.1, 60.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not lo <= value <= hi:
            raise ValidationError(f"{name} must be a number between {lo:g} and {hi:g}")
        return float(value)
    if name == "rerank_template":
        if value not in TEMPLATES:
            raise ValidationError("rerank_template must be one of " + ", ".join(TEMPLATES))
        return value
    raise ValidationError(f"unknown semantic-retrieval setting: {name}")


def get_config(db) -> SemanticConfig:
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    return SemanticConfig.from_json(row[0] if row else None)


def set_config(db, changes: dict, now=None) -> SemanticConfig:
    """Validate and apply `changes` (field -> value); unknown keys are refused. One state_version bump."""
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("configuration changes must be a non-empty object")
    validated = {name: _validate(name, value) for name, value in changes.items()}
    with store.write(db):
        current = get_config(db).to_json()
        current.update(validated)
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (META_KEY, json.dumps(current, sort_keys=True)))
        store.bump_state_version(db)
    return get_config(db)


# ---- vectors: this process only -----------------------------------------------------------------------------------

class _Vectors:
    """Statement vectors of one individual and one embedding model, in memory. `sync` leaves exactly the live
    statements' vectors: what is no longer live is dropped before anything else happens."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_text: dict[str, np.ndarray] = {}
        self.queries: OrderedDict[str, np.ndarray] = OrderedDict()
        # (topic, limit, settings, the live statements) -> the ranking as row positions and scores. One research
        # task asks memory the same question up to three times; the second and third cost nothing. A changed
        # statement set is another key, so nothing ranked over forgotten text is ever served.
        self.results: OrderedDict[tuple, tuple] = OrderedDict()
        self.down_until, self.down_reason = 0.0, None     # after a failed call: lexical ranking until this moment
        self.version = 0                                  # the newest state version a caller synced with

    def cached(self, key):
        with self.lock:
            if key in self.results:
                self.results.move_to_end(key)
                return self.results[key]
        return None

    def remember(self, key, value):
        with self.lock:
            self.results[key] = value
            while len(self.results) > RESULT_CACHE:
                self.results.popitem(last=False)

    def sync(self, texts, embed, version=0) -> np.ndarray:
        """The vectors of `texts`, embedding what is missing. `version` is the individual's state version when the
        caller read its statements: a caller that read memory before a newer one did (a forget in between) gets its
        vectors but leaves none behind. The lock is not held while the model is called, so one slow embedding does
        not hold every other lookup of the process."""
        with self.lock:
            current = version >= self.version
            if current:
                self.version = version
                wanted = set(texts)
                gone = [t for t in self.by_text if t not in wanted]
                for t in gone:
                    del self.by_text[t]
                if gone:
                    self.results.clear()
            have = {t: self.by_text[t] for t in texts if t in self.by_text}
        missing = [t for t in dict.fromkeys(texts) if t not in have]
        for start in range(0, len(missing), EMBED_BATCH):
            part = missing[start:start + EMBED_BATCH]
            for t, vector in zip(part, embed(part)):
                have[t] = _unit(vector)
        with self.lock:
            if current and version >= self.version:       # nobody read a newer memory while the model worked
                for t in missing:
                    self.by_text[t] = have[t]
        return np.stack([have[t] for t in texts])

    def query(self, text, embed) -> np.ndarray:
        with self.lock:
            if text in self.queries:
                self.queries.move_to_end(text)
                return self.queries[text]
        vector = _unit(embed([text])[0])
        with self.lock:
            self.queries[text] = vector
            while len(self.queries) > QUERY_CACHE:
                self.queries.popitem(last=False)
        return vector


def _unit(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


_STORES: dict[str, _Vectors] = {}
_MODEL_NAMES: dict[str, str] = {}      # endpoint -> the model it serves, for an endpoint configured without a name
_STORES_LOCK = threading.Lock()


def _store_key(db, embedder) -> str:
    """One store per individual, endpoint and model: vectors of another endpoint are never compared with these,
    even when both serve a model of the same name."""
    path = store.database_path(db) or f"memory:{id(db)}"
    return hashlib.sha256(f"{path}\0{embedder.cfg.base_url}\0{embedder.cfg.model}".encode()).hexdigest()


def _vectors(db, embedder) -> _Vectors:
    with _STORES_LOCK:
        return _STORES.setdefault(_store_key(db, embedder), _Vectors())


def clear() -> None:
    """Drop every vector this process holds (tests; `nsf web memory clear` after changing the embedding model)."""
    with _STORES_LOCK:
        for held_store in _STORES.values():      # emptied, not only dropped: a lookup in flight may still hold one
            with held_store.lock:
                held_store.by_text.clear()
                held_store.queries.clear()
                held_store.results.clear()
        _STORES.clear()
        _MODEL_NAMES.clear()


def held(db) -> int:
    """Statement vectors this process holds for the individual under its configured embedding model."""
    embedder = _gateways(db)[0]
    if embedder is None:
        return 0
    with _STORES_LOCK:
        found = _STORES.get(_store_key(db, embedder))
    return len(found.by_text) if found else 0


# ---- ranking ------------------------------------------------------------------------------------------------------

def _gateways(db):
    """(embed gateway or None, rerank gateway or None) of the individual; None when the endpoint is not set."""
    from ..inference import config as iconfig
    from ..inference.gateway import Gateway
    path = store.database_path(db)
    out = []
    for provider in ("embed", "rerank"):
        gateway = Gateway.from_db(db, path, provider=provider) \
            if iconfig.get_config(db, provider).configured else None
        if gateway is not None:
            gateway.cfg.max_retries = 0        # a memory lookup never waits out an outage: lexical ranking instead
            if not gateway.cfg.model:          # no model named: ask the endpoint once per process, not per lookup
                name = _MODEL_NAMES.get(gateway.cfg.base_url)
                if name is None:
                    try:
                        name = _MODEL_NAMES[gateway.cfg.base_url] = gateway.model()
                    except Exception:          # rank() reports the failure of the call it then makes
                        name = None
                if name:
                    gateway.cfg.model = gateway._model = name
        out.append(gateway)
    return out


def _pair(cfg: SemanticConfig, question: str, statements: list[str], instruction: str = INSTRUCTION
          ) -> tuple[str, list[str]]:
    if cfg.rerank_template == "qwen3":
        return (f"{_QWEN3_PREFIX}<Instruct>: {instruction}\n<Query>: {question}\n",
                [f"<Document>: {s}{_QWEN3_SUFFIX}" for s in statements])
    return question, statements


def rank(db, topic: str, rows: list[dict], lexical: list[float], *, limit: int, gateways=None):
    """The rows of knowledge.query ranked by meaning -> ([(score, row)], info), or (None, info) when the lexical
    ranking must be used: off, no embedding endpoint, or a failed call (info["reason"] says which).

    `rows` are the live knowledge rows and `lexical` their BM25 scores for the same topic. info: {"mode":
    "semantic", "ranked_by": "rerank" | "embedding", "candidates", "dropped_below_floor", "floor", "models"} or
    {"mode": "lexical", "reason"}. A returned score is the reranker's (0..1) or, without a reranker, the cosine.
    A local memory model the idle policy unloaded is not waited for: {"mode": "lexical", "reason", "waking":
    [service, ...]} now, and its reload runs in the background."""
    cfg = get_config(db)
    if not cfg.enabled:
        return None, {"mode": "lexical"}
    try:
        embedder, reranker = gateways if gateways is not None else _gateways(db)
    except Exception as exc:      # an unreadable key file or similar
        return None, {"mode": "lexical", "reason": f"semantic retrieval unusable: {type(exc).__name__}: {exc}"[:200]}
    if embedder is None:
        return None, {"mode": "lexical", "reason": "semantic retrieval is on but no embedding endpoint is set "
                                                   "(nsf web memory config --embed-url URL)"}
    if not rows:
        return [], {"mode": "semantic", "ranked_by": "embedding", "candidates": 0}
    statements = [r["statement"] for r in rows]
    vectors = _vectors(db, embedder)
    signature = (topic, limit, json.dumps(cfg.to_json(), sort_keys=True), reranker.cfg.model if reranker else None,
                 hashlib.sha256("\0".join(statements).encode()).hexdigest(), tuple(r["id"] for r in rows))
    hit = vectors.cached(signature)
    if hit is not None:
        return [(score, rows[i]) for score, i in hit[0]], dict(hit[1]) | {"cached": True}
    if time.monotonic() < vectors.down_until:      # measured: a refused connection cost each lookup 1.5 s
        return None, {"mode": "lexical", "reason": vectors.down_reason,
                      "retry_in_seconds": round(vectors.down_until - time.monotonic(), 1)}
    # Whose process holds a memory model's port is checked by the gateway before each call (Gateway.stranger): a
    # refusal ends that call "unavailable" with the reason, the lookup is lexical and backs off like any failure.
    with ExitStack() as held_services:
        # The idle policy (resources/idle.py) may have unloaded a memory model. A lookup never waits for one: it is
        # ranked by its words now, and the reload it started makes a later lookup semantic again.
        asleep = _asleep(db, embedder, reranker, held_services)
        if asleep:
            reason = "; ".join(a["why"] for a in asleep)
            if any(a["action"] in ("refused", "failed") for a in asleep):      # not retried on every lookup
                vectors.down_reason, vectors.down_until = reason[:300], time.monotonic() + RETRY_AFTER
            waking = [a["service"] for a in asleep if a["action"] == "loading"]
            return None, {"mode": "lexical", "reason": reason[:300]} | ({"waking": waking} if waking else {})
        return _rank(db, cfg, topic, rows, lexical, limit, embedder, reranker, vectors, signature)


def key_refusal(base_url, key_file) -> str | None:
    """Why the API key must not be sent to this endpoint, or None: resources.idle.listener_refusal, the check the
    inference gateway makes before every call (review L2, S1), asked here before a call is attempted so that
    `nsf web memory` can say "refused" and `nsf setup` contacts nothing. It applies to the memory models' own ports
    on this computer only; the socket that would accept the connection decides, not any socket on that port."""
    from urllib.parse import urlsplit
    from ..inference import config as iconfig
    from ..resources import idle
    import ipaddress
    try:
        url = urlsplit(base_url)
        host = url.hostname or ""
        if host != "localhost":           # a literal address or nothing: no name is looked up for this check
            if not ipaddress.ip_address(host).is_loopback:
                return None
        where, _, addresses = iconfig.resolve(base_url)
        if where != "loopback":
            return None
        return idle.listener_refusal(addresses, url.port or (443 if url.scheme == "https" else 80), key_file or None)
    except Exception:                 # the check must never be the reason a lookup or the setup fails
        return None


def _local_service(gateway, name) -> bool:
    """Whether the gateway's endpoint is this host's own memory service `name` (deploy/memory): only that one is
    the idle policy's to unload and reload. An endpoint elsewhere is never touched."""
    from urllib.parse import urlsplit
    from ..resources import idle
    try:
        url = urlsplit(gateway.cfg.base_url)
        return url.hostname in ("127.0.0.1", "localhost", "::1") and url.port == idle.service_port(name)
    except ValueError:
        return False


def _unloaded_while_idle(db, gateway, name) -> bool:
    path = store.database_path(db)
    if not path or not _local_service(gateway, name):
        return False
    try:
        from ..resources import idle
        return idle.Idle(path).unloaded_by_policy(name)
    except Exception:
        return False


def _asleep(db, embedder, reranker, stack) -> list[dict]:
    """[{"service", "action", "why"}] for each local memory model that cannot be called right now because the idle
    policy unloaded it (its reload was started), is unloading it, or could not reload it. For every other one the
    in-use lock is held on `stack` until the lookup's calls are done. Empty: call them. Never blocks, never raises."""
    path = store.database_path(db)
    if not path:
        return []
    from ..resources import idle
    out = []
    for name, gateway in (("embed", embedder), ("rerank", reranker)):
        if gateway is None or not _local_service(gateway, name):
            continue
        try:
            state = stack.enter_context(idle.using_now(path, name, purpose="memory lookup"))
        except Exception:         # the policy's files are unreadable: the call itself will say what is wrong
            continue
        if state:
            out.append({"service": name, **state})
    return out


def _rank(db, cfg, topic, rows, lexical, limit, embedder, reranker, vectors, signature):
    """rank() once the models may be called: embed, choose candidates, rerank, cut at the floor."""
    from ..inference.gateway import InferenceError
    statements = [r["statement"] for r in rows]
    try:
        matrix = vectors.sync(statements, lambda texts: embedder.embed(texts, purpose="memory_embed",
                                                                       timeout=max(cfg.timeout, 10.0)),
                              store.state_version(db))
        question = vectors.query(topic, lambda texts: embedder.embed(texts, purpose="memory_query",
                                                                     timeout=cfg.timeout))
        if question.shape != matrix.shape[1:]:
            raise InferenceError("bad_response", "the question's vector has another length than the statements'")
        cosine = matrix @ question
        order = sorted(range(len(rows)), key=lambda i: (-float(cosine[i]), -rows[i]["id"]))
        chosen = order[:max(cfg.candidates, limit)]
        best_lexical = sorted((i for i in range(len(rows)) if lexical[i] > 0),
                              key=lambda i: (-lexical[i], -rows[i]["id"]))[:LEXICAL_KEEP]
        chosen += [i for i in best_lexical if i not in chosen]
        models = {"embed": embedder.cfg.model}
        if reranker is None:
            info = {"mode": "semantic", "ranked_by": "embedding", "candidates": len(order[:limit]), "models": models}
            vectors.remember(signature, ([(float(cosine[i]), i) for i in order[:limit]], info))
            return [(float(cosine[i]), rows[i]) for i in order[:limit]], info
        query, documents = _pair(cfg, topic, [statements[i] for i in chosen])
        scores = reranker.rerank(query, documents, purpose="memory_rerank", timeout=cfg.timeout)
    except (InferenceError, OSError, ValueError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        vectors.down_reason = f"semantic retrieval failed ({code}): {exc}"[:300]
        vectors.down_until = time.monotonic() + RETRY_AFTER
        return None, {"mode": "lexical", "reason": vectors.down_reason}
    scored = sorted(zip(scores, chosen), key=lambda pair: (-pair[0], -rows[pair[1]]["id"]))
    kept = [(score, i) for score, i in scored if score >= cfg.floor][:limit]
    info = {"mode": "semantic", "ranked_by": "rerank", "candidates": len(chosen),
            "dropped_below_floor": sum(1 for score, _ in scored if score < cfg.floor), "floor": cfg.floor,
            "models": models | {"rerank": reranker.cfg.model}}
    vectors.remember(signature, (kept, info))
    return [(score, rows[i]) for score, i in kept], info


# ---- the same fact in other words -------------------------------------------------------------------------------------
#
# consolidate.py finds knowledge "about the same subject" by its key words, so a statement of a known fact in other
# words is stored as a second item, and one with another value is not seen as a contradiction (W2). pairs() lets
# the memory models *propose* which stored statement a new one restates. It decides nothing: the caller admits a
# candidate only when both name the same subject and their values can be compared (consolidate._admissible), and
# what the pair is (the same, a contradiction) is the value comparison's verdict (consolidate._compare).
# A pair needs both models: the embedding model chooses the candidates, and the reranker must take the two
# statements to be about the same property of the same subject read in either direction. Measured on
# webcortex/bench/contradiction_paraphrase.json (scripts/bench_knowledge.py contradictions): see
# docs/web-cortex/MEMORY_INTEGRATION.md.

PAIR_INSTRUCTION = ("Given a statement of fact with its numbers written as #, judge whether the stored fact states "
                    "the same property of the same subject")
PAIR_CANDIDATES = 3         # nearest admissible stored statements the reranker reads for one new statement
PAIR_STATEMENTS = 24        # new statements asked about in one consolidation
PAIR_BUDGET = 10.0          # seconds one consolidation may spend asking; what is left is not asked
_NUMBER = re.compile(r"\d[\d.,]*\d|\d")


def _masked(statement: str, like: str | None = None) -> str:
    """The statement as the reranker reads it for a pair: every number replaced by one sign. Whether two
    statements state the same property of the same subject does not depend on their values, and with the values
    in view the reranker scores a contradiction far under a restatement (measured: 0.18 to 0.99 against 0.97 to
    1.00); what the values say is the value comparison's part. Nor does it depend on the unit each chose: given
    `like` (the other statement of the pair), a quantity of a kind `like` also states is written in `like`'s unit
    (text.mask_quantities). Measured: a restatement in miles of a length in kilometres was read under the floor
    one way round (0.07 to 0.66)."""
    from . import text
    return text.mask_quantities(statement, like)


def pairs(db, statements: list[str], rows: list[dict], *, admit=None, gateways=None, sweep: bool = False):
    """For each new statement, the stored statements the memory models take to state the same property of the same
    subject -> (candidates, info). candidates[n] is a list of {"id", "similarity", "score", "score_back"}, best
    first, for statements[n]; empty when nothing qualifies.

    `rows`: the live knowledge rows ({"id", "statement"}), every one of them (their vectors are the ones
    knowledge.query keeps). `admit(n, row) -> bool`: the caller's own test of a candidate, applied before the
    reranker reads it. info: {"mode": "semantic", "asked", "proposed", "models"} or {"mode": "lexical", "reason"}
    when nothing was asked: off, no embedding endpoint or no reranker, a failed call (the models are then left
    alone for RETRY_AFTER seconds, as after a failed lookup), a model the idle policy unloaded (its reload is
    started; nothing waits for it). Never raises. `sweep`: no threshold is applied and every candidate is returned
    with its scores (the benchmark's trace)."""
    from ..inference.gateway import InferenceError
    cfg = get_config(db)
    nothing = [[] for _ in statements]
    if not cfg.enabled or not cfg.contradictions:
        return nothing, {"mode": "lexical"}
    try:
        embedder, reranker = gateways if gateways is not None else _gateways(db)
    except Exception as exc:
        return nothing, {"mode": "lexical", "reason": f"memory models unusable: {type(exc).__name__}: {exc}"[:200]}
    if embedder is None:
        return nothing, {"mode": "lexical", "reason": "no embedding endpoint is set (nsf web memory config "
                                                      "--embed-url URL)"}
    if reranker is None:
        return nothing, {"mode": "lexical", "reason": "pairing statements needs the reranker as well (nsf web "
                                                      "memory config --rerank-url URL)"}
    if not statements or not rows:
        return nothing, {"mode": "semantic", "asked": 0, "proposed": 0,
                         "models": {"embed": embedder.cfg.model, "rerank": reranker.cfg.model}}
    vectors = _vectors(db, embedder)
    if time.monotonic() < vectors.down_until:
        return nothing, {"mode": "lexical", "reason": vectors.down_reason,
                         "retry_in_seconds": round(vectors.down_until - time.monotonic(), 1)}
    stored = [r["statement"] for r in rows]
    out, asked, started = [[] for _ in statements], 0, time.monotonic()
    info = {"mode": "semantic", "models": {"embed": embedder.cfg.model, "rerank": reranker.cfg.model}}
    with ExitStack() as held_services:
        asleep = _asleep(db, embedder, reranker, held_services)
        if asleep:
            reason = "; ".join(a["why"] for a in asleep)
            if any(a["action"] in ("refused", "failed") for a in asleep):
                vectors.down_reason, vectors.down_until = reason[:300], time.monotonic() + RETRY_AFTER
            waking = [a["service"] for a in asleep if a["action"] == "loading"]
            return nothing, {"mode": "lexical", "reason": reason[:300]} | ({"waking": waking} if waking else {})
        try:
            matrix = vectors.sync(stored, lambda texts: embedder.embed(texts, purpose="memory_embed",
                                                                       timeout=max(cfg.timeout, 10.0)),
                                  store.state_version(db))
            new = []
            for start in range(0, len(statements), EMBED_BATCH):
                part = statements[start:start + EMBED_BATCH]
                new += [_unit(v) for v in embedder.embed(part, purpose="memory_pair", timeout=max(cfg.timeout, 10.0))]
            for n, statement in enumerate(statements):
                if n >= PAIR_STATEMENTS or time.monotonic() - started > PAIR_BUDGET:
                    info["not_asked"] = len(statements) - n
                    break
                if new[n].shape != matrix.shape[1:]:
                    raise InferenceError("bad_response", "a statement's vector has another length than the stored")
                cosine = matrix @ new[n]
                order = sorted(range(len(rows)), key=lambda i: (-float(cosine[i]), -rows[i]["id"]))
                chosen = []
                for i in order:
                    if len(chosen) >= (5 if sweep else PAIR_CANDIDATES) or \
                            (not sweep and float(cosine[i]) < cfg.pair_similarity):
                        break
                    if sweep or admit is None or admit(n, rows[i]):
                        chosen.append(i)
                if not chosen:
                    continue
                asked += 1
                query, documents = _pair(cfg, _masked(statement), [_masked(stored[i], like=statement)
                                                                   for i in chosen], PAIR_INSTRUCTION)
                forward = reranker.rerank(query, documents, purpose="memory_pair", timeout=cfg.timeout)
                found = []
                for i, score in zip(chosen, forward):
                    back = None
                    if sweep or score >= cfg.pair_floor:     # read the other way round too: both must agree
                        q, d = _pair(cfg, _masked(stored[i]), [_masked(statement, like=stored[i])],
                                     PAIR_INSTRUCTION)
                        back = reranker.rerank(q, d, purpose="memory_pair", timeout=cfg.timeout)[0]
                    if sweep or (back is not None and back >= cfg.pair_floor):
                        found.append({"id": rows[i]["id"], "similarity": round(float(cosine[i]), 4),
                                      "score": round(float(score), 4),
                                      "score_back": None if back is None else round(float(back), 4)})
                found.sort(key=lambda c: (-min(c["score"], c["score_back"] if c["score_back"] is not None else 0.0),
                                          -c["similarity"], -c["id"]))
                out[n] = found
        except (InferenceError, OSError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            vectors.down_reason = f"semantic retrieval failed ({code}): {exc}"[:300]
            vectors.down_until = time.monotonic() + RETRY_AFTER
            return nothing, {"mode": "lexical", "reason": vectors.down_reason}
    info["asked"], info["proposed"] = asked, sum(1 for found in out if found)
    return out, info


def status(db) -> dict:
    """What the owner sees in `nsf web memory`: the switch, the two endpoints and their health, never a key."""
    from ..inference import config as iconfig
    cfg = get_config(db)
    out = {"enabled": cfg.enabled, "settings": cfg.to_json(), "providers": {}, "vectors_held_by_this_process": 0}
    try:
        embedder, reranker = _gateways(db)
    except Exception as exc:
        return out | {"error": f"{type(exc).__name__}: {exc}"[:200]}
    for name, gateway in (("embed", embedder), ("rerank", reranker)):
        if gateway is None:
            out["providers"][name] = {"configured": False}
            continue
        refused = key_refusal(gateway.cfg.base_url, gateway.cfg.key_file)
        health = {"state": "refused", "reason": refused} if refused else gateway.health()
        out["providers"][name] = {"configured": True, "endpoint": gateway.cfg.base_url, "model": gateway.cfg.model,
                                  "state": health["state"], "reason": health.get("reason"),
                                  "key_present": iconfig.public(gateway.cfg, name)["key_present"]}
        if health["state"] == "unavailable" and _unloaded_while_idle(db, gateway, name):
            # not an outage: say so, and what the next lookup does
            out["providers"][name].update(state="asleep", reason="unloaded while idle; the next memory lookup is "
                                          "ranked by its words and starts it again")
    out["vectors_held_by_this_process"] = held(db)
    out["effect"] = ("off: memory is ranked by its words (lexical)" if not cfg.enabled else
                     "on, but no embedding endpoint: lexical ranking" if embedder is None else
                     "memory is ranked by the reranker; statements under the floor are not returned"
                     if reranker is not None else "memory is ranked by vector similarity; nothing is cut")
    out["contradictions"] = (
        "off: a new statement is compared only with knowledge that shares its key words"
        if not (cfg.enabled and cfg.contradictions) else
        "on, but it needs both memory models: only knowledge that shares its key words is compared"
        if embedder is None or reranker is None else
        "a new statement is also compared with the stored one that states the same property of the same subject "
        "in other words" + (", when the answer model confirms the pair" if cfg.pair_check else
                            " (not confirmed by the answer model: pair_check is off)"))
    return out


__all__ = ["INSTRUCTION", "SemanticConfig", "TEMPLATES", "clear", "get_config", "held", "rank", "set_config",
           "status"]
