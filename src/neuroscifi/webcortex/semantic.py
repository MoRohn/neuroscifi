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

With only `embed` the candidates are returned by vector similarity and nothing is cut: similarity alone does not
separate "about this" from "about something similar" (measured), so no floor is applied without the reranker.

What it is not. It is not a second memory: there is no vector store. Vectors live in this process only, keyed by
the statement's text, and the set is trimmed to the live statements on every query, so a forgotten, superseded or
archived statement leaves no vector behind and nothing derived from memory is written to disk. It ranks and filters
what knowledge.query returns; it does not decide whether memory answers a question (intent.assess and the
orchestrator's memory check do), it does not merge or compare knowledge, and it never changes a statement, a
confidence or a verification state.

Privacy. Statements and questions go to the configured endpoints through Gateway.embed / Gateway.rerank, which mark
every text private: an endpoint off this host is refused unless the owner authorised private memory for it.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
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

    @classmethod
    def from_json(cls, text: str | None) -> "SemanticConfig":
        raw = json.loads(text) if text else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> dict:
        return asdict(self)


def _validate(name, value):
    if name == "enabled":
        if not isinstance(value, bool):
            raise ValidationError("enabled must be true or false")
        return value
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

    def sync(self, texts, embed) -> np.ndarray:
        with self.lock:
            wanted = set(texts)
            gone = [t for t in self.by_text if t not in wanted]
            for t in gone:
                del self.by_text[t]
            if gone:
                self.results.clear()
            missing = [t for t in dict.fromkeys(texts) if t not in self.by_text]
            for start in range(0, len(missing), EMBED_BATCH):
                part = missing[start:start + EMBED_BATCH]
                for t, vector in zip(part, embed(part)):
                    self.by_text[t] = _unit(vector)
            return np.stack([self.by_text[t] for t in texts])

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
_STORES_LOCK = threading.Lock()


def _vectors(db, model) -> _Vectors:
    path = store.database_path(db) or f"memory:{id(db)}"
    key = hashlib.sha256(f"{path}\0{model}".encode()).hexdigest()
    with _STORES_LOCK:
        return _STORES.setdefault(key, _Vectors())


def clear() -> None:
    """Drop every vector this process holds (tests; `nsf web memory clear` after changing the embedding model)."""
    with _STORES_LOCK:
        _STORES.clear()


def held(db) -> int:
    """Statement vectors this process holds for the individual under its configured embedding model."""
    embedder = _gateways(db)[0]
    if embedder is None:
        return 0
    path = store.database_path(db) or f"memory:{id(db)}"
    key = hashlib.sha256(f"{path}\0{embedder.cfg.model}".encode()).hexdigest()
    with _STORES_LOCK:
        found = _STORES.get(key)
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
        out.append(gateway)
    return out


def _pair(cfg: SemanticConfig, question: str, statements: list[str]) -> tuple[str, list[str]]:
    if cfg.rerank_template == "qwen3":
        return (f"{_QWEN3_PREFIX}<Instruct>: {INSTRUCTION}\n<Query>: {question}\n",
                [f"<Document>: {s}{_QWEN3_SUFFIX}" for s in statements])
    return question, statements


def rank(db, topic: str, rows: list[dict], lexical: list[float], *, limit: int, gateways=None):
    """The rows of knowledge.query ranked by meaning -> ([(score, row)], info), or (None, info) when the lexical
    ranking must be used: off, no embedding endpoint, or a failed call (info["reason"] says which).

    `rows` are the live knowledge rows and `lexical` their BM25 scores for the same topic. info: {"mode":
    "semantic", "ranked_by": "rerank" | "embedding", "candidates", "dropped_below_floor", "floor", "models"} or
    {"mode": "lexical", "reason"}. A returned score is the reranker's (0..1) or, without a reranker, the cosine."""
    from ..inference.gateway import InferenceError
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
    vectors = _vectors(db, embedder.cfg.model)
    if time.monotonic() < vectors.down_until:      # measured: a refused connection cost each lookup 1.5 s
        return None, {"mode": "lexical", "reason": vectors.down_reason,
                      "retry_in_seconds": round(vectors.down_until - time.monotonic(), 1)}
    signature = (topic, limit, json.dumps(cfg.to_json(), sort_keys=True), reranker.cfg.model if reranker else None,
                 hashlib.sha256("\0".join(statements).encode()).hexdigest(), tuple(r["id"] for r in rows))
    hit = vectors.cached(signature)
    if hit is not None:
        return [(score, rows[i]) for score, i in hit[0]], dict(hit[1]) | {"cached": True}
    try:
        matrix = vectors.sync(statements, lambda texts: embedder.embed(texts, purpose="memory_embed",
                                                                       timeout=max(cfg.timeout, 10.0)))
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
        vectors.down_reason = f"semantic retrieval failed ({code}): {exc}"[:200]
        vectors.down_until = time.monotonic() + RETRY_AFTER
        return None, {"mode": "lexical", "reason": vectors.down_reason}
    scored = sorted(zip(scores, chosen), key=lambda pair: (-pair[0], -rows[pair[1]]["id"]))
    kept = [(score, i) for score, i in scored if score >= cfg.floor][:limit]
    info = {"mode": "semantic", "ranked_by": "rerank", "candidates": len(chosen),
            "dropped_below_floor": sum(1 for score, _ in scored if score < cfg.floor), "floor": cfg.floor,
            "models": models | {"rerank": reranker.cfg.model}}
    vectors.remember(signature, (kept, info))
    return [(score, rows[i]) for score, i in kept], info


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
        health = gateway.health()
        out["providers"][name] = {"configured": True, "endpoint": gateway.cfg.base_url, "model": gateway.cfg.model,
                                  "state": health["state"], "reason": health.get("reason"),
                                  "key_present": iconfig.public(gateway.cfg)["key_present"]}
    out["vectors_held_by_this_process"] = held(db)
    out["effect"] = ("off: memory is ranked by its words (lexical)" if not cfg.enabled else
                     "on, but no embedding endpoint: lexical ranking" if embedder is None else
                     "memory is ranked by the reranker; statements under the floor are not returned"
                     if reranker is not None else "memory is ranked by vector similarity; nothing is cut")
    return out


__all__ = ["INSTRUCTION", "SemanticConfig", "TEMPLATES", "clear", "get_config", "held", "rank", "set_config",
           "status"]
