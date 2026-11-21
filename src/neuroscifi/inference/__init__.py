"""Inference gateway: a model-independent client for OpenAI-compatible endpoints (docs/architecture/
COGNITIVE_INFERENCE.md, ADR-036 draft).

- config.py   per-individual settings (instance `meta` key 'inference'), endpoint locality, API key file
- gateway.py  discovery, health states, chat (plain and SSE streaming), structured output with local validation
              and one repair retry, timeouts, bounded retries, context budget, remote privacy screen
- jsonschema.py  the JSON Schema subset used to validate structured output and tool arguments locally
- tools.py    allow-listed tools: the model suggests a call, NeuroSciFi validates and executes it through the
              existing authorised functions, or refuses it
- ledger.py   token accounting per call in `<db stem>.inference.sqlite` (no prompt or answer text)
- cli.py      `nsf llm config|status|models|ask|usage`

Nothing here starts a model or downloads weights; the server is deploy/vllm. Without a configured endpoint every
caller gets the explicit state 'not_configured' and the existing extractive path runs unchanged.
"""
