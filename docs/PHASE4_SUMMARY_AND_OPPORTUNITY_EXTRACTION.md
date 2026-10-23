# FRIDAY Phase 4 — Concise summaries and opportunity extraction

## Implemented
- Replaced the local summary's raw email-context dump with a structured inbox digest.
- Separates job/internship opportunities, explicit deadline evidence, other actionable messages, and other mail.
- Extracts role/subject, sender, approximate company inference from sender domain, eligibility evidence, application URL, and explicit deadline evidence where present.
- Avoids declaring a date a deadline unless deadline/action language and a date are present together.
- Makes `/gmail/intelligence/ask` produce concise evidence-based opportunity findings instead of returning the summary context.
- Keeps source email evidence available in the response.
- Preserves snippet fallback when full body text is unavailable and does not inspect attachments.

## Changed files
- `backend/services/email_intelligence_service.py`
- `tests/test_email_intelligence_bodies.py`
- `README.md`

## Validation
- `python -m pytest -q`: 78 passed.
- Test cases cover concise summaries, snippet fallback, body-only search, no-match behavior, structured opportunity extraction, explicit deadlines, and avoiding false deadline inference.

## Limitations
- This local extractor uses regular expressions and heuristics, not a full language model. Company inference from sender domains is approximate and explicitly labeled. Confirm deadlines, eligibility, roles, and links against the original email before acting.
- If `AI_API_KEY` and `AI_MODEL` are configured, the optional external AI path can be used; bounded email context may be sent to the configured provider. `use_ai=false` stays local.
- Attachments are not analyzed.
