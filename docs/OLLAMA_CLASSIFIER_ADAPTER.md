# Local Ollama classifier adapter

## Status

Phase 3 adapter foundation is implemented as an isolated service module:
`backend/services/ollama_classifier.py`. It does not change the existing
rules-only production analysis path. Production routing is intentionally held
for Phase 4 (policy/configuration/benchmark) and Phase 5 (integration).

## Contract

`classify_with_ollama(row, config=None)` accepts a mapping containing optional
`sender`, `subject`, `snippet`, and `body_text` strings. It returns a validated
`Classification` with:

- `is_spam`: `yes` or `no`
- `priority`: `URGENT`, `NEEDS_REPLY`, `INFORMATIONAL`, or `SPAM`
- `needs_response`: `yes` or `no`
- `category`: one of the six project categories
- `confidence`: finite number in `[0, 1]`
- classifier method/version and inference latency

Email fields are treated as untrusted data in the prompt. Body input is bounded.
The adapter calls only the configured local Ollama host and does not persist
email content.

## Failure behavior

The adapter raises typed, controlled errors:
`OllamaTimeoutError`, `OllamaUnavailableError`, or `OllamaOutputError`.
`classify_with_fallback` catches only these adapter errors and returns the
rules result marked as `rules_fallback`, with a non-sensitive failure type.
Unexpected programming errors are not swallowed.

## Configuration

Copy `.env.example` to `.env` and set the optional `OLLAMA_*` values. Ollama
must be running locally and the configured model must already be available.
No model is downloaded automatically.

## Local verification

```powershell
python -m pytest -q tests/test_ollama_classifier.py
python -m pytest -q
```

Tests use synthetic payloads and a stubbed HTTP opener. They do not contact
Ollama, Gmail, or an external API.

## Not yet production-enabled

Do not set the database classifier provenance to `ollama` based solely on
this adapter existing. Phase 4 must define and benchmark explicit routing modes
(rules-only, ML-only, Ollama-only, and hybrid) on the same protected frozen
test IDs. Phase 5 then integrates the selected strategy with persistence,
API schemas, migrations if needed, and rollback to rules-only.
