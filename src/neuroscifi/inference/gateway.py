"""OpenAI-compatible inference gateway (stdlib http.client; no SDK).

Gateway(cfg, key=..., ledger=...):
  models() -> [{"id", "max_model_len", "owned_by"}]           GET {base}/models
  health(probe=False) -> {"state": STATES, "reason", ...}      explicit states, never a guess:
      not_configured  no endpoint, or a remote endpoint without the owner's remote_allowed (or without https)
      unauthorised    the endpoint answered 401/403 (missing or wrong API key)
      unavailable     connection refused, timeout, 5xx, or an unparsable answer
      degraded        it answers, but the configured model is not served, nothing is served, or the probe failed
      ready           it lists the model (and, with probe=True, generated a reply)
  chat(messages, purpose=..., stream=False, tools=None, response_format=None, ...) -> ChatResult
  structured(messages, schema, name=..., purpose=...) -> (value, [ChatResult])   local validation, one repair retry

Every call: the endpoint guard (local-first, see config.py), the remote privacy screen, a context budget check
(estimate of prompt + max_tokens <= cfg.max_context_tokens; else 'context_overflow' before anything is sent),
a connect timeout and a whole-call deadline (cfg.read_timeout, streaming included), bounded retries with
exponential backoff for connection errors, 429 and 502-504 (Retry-After honoured up to 10 s; a stream is never
retried after its first byte), and one ledger row (tokens, latency, first-token latency, attempts; no text).
<think>...</think> blocks (reasoning models) are removed from returned text.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import http.client
import json
import math
import re
import socket
import time
from urllib.parse import urlsplit

from . import config as iconfig
from .jsonschema import check_schema, validate

STATES = ("ready", "degraded", "unavailable", "unauthorised", "not_configured")
ERROR_CODES = ("not_configured", "remote_not_authorised", "private_blocked", "unauthorised", "unavailable",
               "timeout", "http_error", "bad_response", "context_overflow", "schema_invalid")
RETRY_STATUS = (429, 502, 503, 504)
MAX_RESPONSE = 8 * 1024 * 1024
MAX_RETRY_AFTER = 10.0
CHARS_PER_TOKEN = 3.5          # conservative for English and code with BPE tokenizers (Qwen3 ~3.8-4.2)
MESSAGE_OVERHEAD = 6
AUTHORIZED_DISCLOSURE = "owner-approved-disclosure"   # same words as webcortex.orchestrator
USER_AGENT = "NeuroSciFi-Inference/0.7"
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class InferenceError(Exception):
    def __init__(self, code, message, *, status=None, retryable=False, retry_after=None):
        super().__init__(message)
        self.code, self.status, self.retryable, self.retry_after = code, status, retryable, retry_after

    def to_json(self):
        return {"code": self.code, "message": str(self), "status": self.status}


@dataclass
class ChatResult:
    text: str
    model: str
    finish_reason: str | None = None
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    attempts: int = 1
    stream: bool = False

    @property
    def tokens_per_second(self) -> float | None:
        if not self.completion_tokens:
            return None
        start = self.first_token_ms if self.first_token_ms is not None else 0.0
        span = (self.latency_ms - start) / 1000.0
        return round(self.completion_tokens / span, 2) if span > 0 else None

    def to_json(self):
        return asdict(self) | {"tokens_per_second": self.tokens_per_second}


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def estimate_messages(messages) -> int:
    total = 0
    for m in messages:
        total += MESSAGE_OVERHEAD + estimate_tokens(_content_text(m.get("content")))
        for call in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(call))
    return total


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def strip_thinking(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def parse_json(text: str):
    """The JSON value in a model reply: fences and thinking removed; the outermost {...} if text surrounds it."""
    body = strip_thinking(text)
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if 0 <= start < end:
            return json.loads(body[start:end + 1])
        raise


class Gateway:
    def __init__(self, cfg: iconfig.InferenceConfig, *, key=None, ledger=None, clock=time.monotonic,
                 wall=time.time, sleep=time.sleep, resolver=socket.getaddrinfo, task_id=None):
        self.cfg, self.key, self.ledger = cfg, key, ledger
        self.clock, self.wall, self.sleep, self.resolver = clock, wall, sleep, resolver
        self.task_id = task_id
        self._locality = None
        self._model = cfg.model or None

    @classmethod
    def from_db(cls, db, db_path=None, **kwargs) -> "Gateway":
        from .ledger import Ledger, ledger_path
        cfg = iconfig.get_config(db)
        key = iconfig.read_key(cfg) if cfg.configured else None
        ledger = Ledger(ledger_path(db_path)) if db_path is not None and cfg.configured else None
        return cls(cfg, key=key, ledger=ledger, **kwargs)

    # ---- endpoint policy ------------------------------------------------------------------------------------
    def locality(self) -> tuple[str, str]:
        if self._locality is None:
            self._locality = iconfig.locality(self.cfg.base_url, resolver=self.resolver)
        return self._locality

    def guard(self) -> str:
        """Raise unless this endpoint may be used; returns its locality."""
        if not self.cfg.configured:
            raise InferenceError("not_configured", "no model endpoint configured (nsf llm config --url URL)")
        where, detail = self.locality()
        if where == "remote":
            if not self.cfg.remote_allowed:
                raise InferenceError("remote_not_authorised", f"endpoint {detail} is not loopback or private; the "
                                     "owner has not authorised remote inference (nsf llm config --remote-allowed)")
            if urlsplit(self.cfg.base_url).scheme != "https":
                raise InferenceError("remote_not_authorised", "a remote endpoint must use https")
        return where

    def screen(self, messages, authorization=None) -> None:
        """Remote endpoints only: refuse private memory (messages marked private) unless separately authorised,
        and any message the web privacy screen flags unless the owner approved this disclosure."""
        if self.guard() != "remote":
            return
        from ..webcortex import safety
        for m in messages:
            if m.get("private") and not self.cfg.remote_private_memory:
                raise InferenceError("private_blocked", "private memory is not sent to a remote endpoint "
                                     "(nsf llm config --remote-private-memory to authorise)")
            if authorization == AUTHORIZED_DISCLOSURE:
                continue
            found = safety.privacy_screen(_content_text(m.get("content")))
            if found:
                kinds = ", ".join(sorted({f["kind"] for f in found}))
                raise InferenceError("private_blocked", f"message contains private data ({kinds}); not sent to a "
                                     "remote endpoint without owner-approved disclosure")

    # ---- HTTP -------------------------------------------------------------------------------------------------
    def _open(self, method, path, payload, remaining):
        parts = urlsplit(self.cfg.base_url)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                   timeout=min(self.cfg.connect_timeout, max(0.05, remaining)))
        try:
            conn.connect()
        except (socket.timeout, TimeoutError) as exc:
            conn.close()
            raise InferenceError("unavailable", f"connect timeout: {exc}", retryable=True) from None
        except OSError as exc:
            conn.close()
            raise InferenceError("unavailable", f"cannot connect: {exc.strerror or exc}", retryable=True) from None
        conn.sock.settimeout(max(0.05, remaining))
        headers = {"Accept": "application/json, text/event-stream", "User-Agent": USER_AGENT}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, parts.path.rstrip("/") + path, body=body, headers=headers)
            resp = conn.getresponse()
        except (socket.timeout, TimeoutError):
            conn.close()
            raise InferenceError("timeout", "no answer within the deadline") from None
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise InferenceError("unavailable", f"connection failed: {type(exc).__name__}", retryable=True) from None
        return conn, resp

    def _call(self, method, path, payload=None, *, timeout=None, retries=None):
        """One exchange with bounded retries -> (conn, resp, attempts, deadline). Non-2xx raise InferenceError."""
        deadline = self.clock() + (timeout or self.cfg.read_timeout)
        retries = self.cfg.max_retries if retries is None else retries
        attempts = 0
        while True:
            attempts += 1
            try:
                conn, resp = self._open(method, path, payload, deadline - self.clock())
                if 200 <= resp.status < 300:
                    return conn, resp, attempts, deadline
                raw = resp.read(65536)
                conn.close()
                raise self._http_error(resp, raw)
            except InferenceError as err:
                err.attempts = attempts
                if not err.retryable or attempts > retries:
                    raise
                delay = min(MAX_RETRY_AFTER, err.retry_after) if err.retry_after else \
                    self.cfg.backoff * 2 ** (attempts - 1)
                if self.clock() + delay >= deadline:
                    raise
                self.sleep(delay)

    @staticmethod
    def _http_error(resp, raw):
        message = None
        try:
            detail = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            err = detail.get("error")
            message = (err.get("message") if isinstance(err, dict) else err) or detail.get("message")
        message = " ".join(str(message or raw[:300].decode("utf-8", "replace")).split())[:300]
        status = resp.status
        if status in (401, 403):
            return InferenceError("unauthorised", f"HTTP {status}: the endpoint refused the API key", status=status)
        retry_after = None
        try:
            retry_after = float(resp.getheader("Retry-After") or "")
        except ValueError:
            pass
        if status == 400 and re.search(r"context length|maximum context|too many tokens|max_model_len", message,
                                       re.IGNORECASE):
            return InferenceError("context_overflow", f"HTTP 400: {message}", status=status)
        code = "unavailable" if status >= 500 or status == 429 else "http_error"
        return InferenceError(code, f"HTTP {status}: {message}", status=status, retryable=status in RETRY_STATUS,
                              retry_after=retry_after)

    def _json(self, method, path, payload=None, *, timeout=None, retries=None):
        conn, resp, attempts, _ = self._call(method, path, payload, timeout=timeout, retries=retries)
        try:
            raw = resp.read(MAX_RESPONSE + 1)
        except (socket.timeout, TimeoutError):
            raise InferenceError("timeout", "the answer did not arrive within the deadline") from None
        finally:
            conn.close()
        if len(raw) > MAX_RESPONSE:
            raise InferenceError("bad_response", "answer larger than 8 MiB")
        try:
            return json.loads(raw), attempts
        except ValueError:
            raise InferenceError("bad_response", "the endpoint did not answer JSON") from None

    # ---- discovery and health ---------------------------------------------------------------------------------
    def models(self, *, retries=None) -> list[dict]:
        self.guard()
        data, _ = self._json("GET", "/models", timeout=min(self.cfg.read_timeout, 15.0), retries=retries)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise InferenceError("bad_response", "/models did not return a data list")
        return [{"id": str(m.get("id")), "max_model_len": m.get("max_model_len"), "owned_by": m.get("owned_by")}
                for m in items if isinstance(m, dict) and m.get("id")]

    def model(self) -> str:
        if self._model is None:
            served = self.models()
            if not served:
                raise InferenceError("unavailable", "the endpoint serves no model")
            self._model = served[0]["id"]
        return self._model

    def health(self, *, probe=False) -> dict:
        out = {"state": None, "reason": None, "endpoint": self.cfg.base_url or None, "locality": None,
               "model": self.cfg.model or None, "models": [], "key_present": bool(self.key), "latency_ms": None}
        try:
            out["locality"] = self.guard()
        except InferenceError as err:
            return out | {"state": "not_configured", "reason": str(err)}
        start = self.clock()
        try:
            served = self.models(retries=0)    # a health check reports, it does not wait out an outage
        except InferenceError as err:
            state = "unauthorised" if err.code == "unauthorised" else "unavailable"
            return out | {"state": state, "reason": str(err)}
        out["latency_ms"] = round((self.clock() - start) * 1000, 1)
        out["models"] = served
        ids = [m["id"] for m in served]
        if not ids:
            return out | {"state": "degraded", "reason": "the endpoint serves no model"}
        if self.cfg.model and self.cfg.model not in ids:
            return out | {"state": "degraded", "reason": f"configured model {self.cfg.model!r} is not served "
                                                         f"(served: {', '.join(ids)})"}
        out["model"] = self.cfg.model or ids[0]
        if probe:
            try:
                result = self.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                                   purpose="health_probe", max_tokens=8, temperature=0.0)
            except InferenceError as err:
                return out | {"state": "degraded", "reason": f"generation probe failed: {err}"}
            out["probe"] = {"reply": result.text[:40], "latency_ms": result.latency_ms,
                            "completion_tokens": result.completion_tokens}
        return out | {"state": "ready", "reason": None}

    # ---- generation -------------------------------------------------------------------------------------------
    def chat(self, messages, *, purpose, max_tokens=None, temperature=None, tools=None, tool_choice=None,
             response_format=None, stream=False, on_delta=None, authorization=None, timeout=None) -> ChatResult:
        where = self.guard()
        self.screen(messages, authorization)
        max_tokens = min(int(max_tokens or self.cfg.max_output_tokens), self.cfg.max_output_tokens)
        need = estimate_messages(messages) + max_tokens
        if need > self.cfg.max_context_tokens:
            raise InferenceError("context_overflow", f"prompt estimate {need - max_tokens} + output {max_tokens} "
                                 f"tokens exceeds max_context_tokens {self.cfg.max_context_tokens}")
        model = self.model()
        wire = [{k: v for k, v in m.items() if k in ("role", "content", "name", "tool_calls", "tool_call_id")}
                for m in messages]
        payload = {"model": model, "messages": wire, "max_tokens": max_tokens,
                   "temperature": self.cfg.temperature if temperature is None else temperature, "stream": bool(stream)}
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        if self.cfg.thinking != "default":
            payload["chat_template_kwargs"] = {"enable_thinking": self.cfg.thinking == "on"}
        start = self.clock()
        row = {"at": self.wall(), "purpose": purpose, "model": model, "locality": where, "stream": stream,
               "task_id": self.task_id}
        try:
            conn, resp, attempts, deadline = self._call("POST", "/chat/completions", payload, timeout=timeout)
            try:
                if stream:
                    result = self._read_stream(resp, deadline, start, on_delta)
                else:
                    result = self._read_once(resp)
            finally:
                conn.close()
        except InferenceError as err:
            self._record(row | {"status": err.code, "error": str(err)[:300], "attempts": getattr(err, "attempts", 1),
                                "latency_ms": round((self.clock() - start) * 1000, 1)})
            raise
        result.model = result.model or model
        result.attempts, result.stream = attempts, bool(stream)
        result.latency_ms = round((self.clock() - start) * 1000, 1)
        result.text = strip_thinking(result.text)
        self._record(row | {"status": "ok", "prompt_tokens": result.prompt_tokens,
                            "completion_tokens": result.completion_tokens, "latency_ms": result.latency_ms,
                            "first_token_ms": result.first_token_ms, "attempts": attempts,
                            "tokens_per_second": result.tokens_per_second})
        return result

    def _record(self, row):
        if self.ledger is not None:
            try:
                self.ledger.record(row)
            except Exception:   # accounting must never break an answer
                pass

    def _read_once(self, resp) -> ChatResult:
        try:
            raw = resp.read(MAX_RESPONSE + 1)
        except (socket.timeout, TimeoutError):
            raise InferenceError("timeout", "the answer did not arrive within the deadline") from None
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise InferenceError("bad_response", "unexpected chat completion shape") from None
        usage = data.get("usage") or {}
        return ChatResult(text=message.get("content") or "", model=data.get("model") or "",
                          finish_reason=choice.get("finish_reason"), tool_calls=list(message.get("tool_calls") or []),
                          prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))

    def _read_stream(self, resp, deadline, start, on_delta) -> ChatResult:
        parts, calls, first, finish, usage, model = [], {}, None, None, {}, ""
        while True:
            if self.clock() > deadline:
                raise InferenceError("timeout", "the stream did not finish within the deadline")
            try:
                line = resp.fp.readline(MAX_RESPONSE)
            except (socket.timeout, TimeoutError):
                raise InferenceError("timeout", "the stream stalled past the deadline") from None
            except OSError as exc:
                raise InferenceError("unavailable", f"stream broken: {type(exc).__name__}") from None
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                raise InferenceError("bad_response", "unparsable stream chunk") from None
            if chunk.get("error"):
                raise InferenceError("http_error", f"stream error: {str(chunk['error'])[:300]}")
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    if first is None:
                        first = round((self.clock() - start) * 1000, 1)
                    parts.append(text)
                    if on_delta is not None:
                        on_delta(text)
                for tc in delta.get("tool_calls") or []:
                    if first is None:
                        first = round((self.clock() - start) * 1000, 1)
                    slot = calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                                 "function": {"name": "", "arguments": ""}})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        return ChatResult(text="".join(parts), model=model, finish_reason=finish,
                          tool_calls=[calls[i] for i in sorted(calls)], prompt_tokens=usage.get("prompt_tokens"),
                          completion_tokens=usage.get("completion_tokens"), first_token_ms=first)

    def structured(self, messages, schema, *, name, purpose, **kwargs):
        """A JSON value matching `schema`: requested with response_format json_schema, validated HERE, and on a
        violation re-asked once with the errors (bounded repair). Raises InferenceError('schema_invalid')."""
        check_schema(schema)
        fmt = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
        results = [self.chat(messages, purpose=purpose, response_format=fmt, **kwargs)]
        value, errors = _decode(results[0].text, schema)
        if not errors:
            return value, results
        repair = list(messages) + [
            {"role": "assistant", "content": results[0].text[:4000]},
            {"role": "user", "content": "That reply does not match the required JSON schema: " + "; ".join(errors[:8])
             + ". Reply again with only a JSON object that matches the schema."}]
        kwargs.pop("on_delta", None)
        results.append(self.chat(repair, purpose=f"{purpose}:repair", response_format=fmt, **kwargs))
        value, errors = _decode(results[1].text, schema)
        if errors:
            err = InferenceError("schema_invalid", "structured output invalid after one repair: " + "; ".join(errors[:5]))
            err.results = results
            raise err
        return value, results


def _decode(text, schema):
    try:
        value = parse_json(text)
    except (ValueError, TypeError):
        return None, ["not valid JSON"]
    return value, validate(value, schema)


__all__ = ["AUTHORIZED_DISCLOSURE", "ChatResult", "ERROR_CODES", "Gateway", "InferenceError", "STATES",
           "estimate_messages", "estimate_tokens", "parse_json", "strip_thinking"]
