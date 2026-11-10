# Phase 3 — Intelligent Email Management (Features 1–3)

This increment strengthens the existing FRIDAY email-intelligence implementation without replacing the rules-based production classifier or changing the database schema.

## Feature 1 — Intelligent email summarization

Existing entry point remains `GET /gmail/intelligence/summary`.

Enhancements:
- The response now includes `email_summaries`, a structured per-message collection alongside the existing human-readable inbox digest.
- Each item includes message/thread identifiers, sender, subject, date, concise body-first summary, content source, available stored category/priority/response signals, detected actions/deadline, and conservative opportunity details.
- Existing `summary`, provider/fallback metadata, and attachment-access boundary remain intact.
- Summarization remains read-only. Email bodies are evidence, not instructions; attachments are not inspected.

## Feature 2 — Natural-language email search

Existing entry points remain `POST /gmail/intelligence/ask` and `POST /assistant/chat`.

Enhancements:
- Search relevance ranks subject matches above sender, snippet, and body matches, with phrase bonuses.
- The direct inbox question service returns a `relevance_score` for each evidence item.
- Assistant query matching now includes locally stored full body text in addition to metadata, snippets, and analysis fields.
- Responses clarify that attachments and live Gmail are not searched. The service still does not silently answer from unrelated recent mail when a keyword query has no match.
- Existing intent recognition, evidence IDs, follow-up behavior, and read-only contract are retained.

## Feature 3 — Unanswered email detection

New service: `backend/services/email_response_tracking_service.py`.

New endpoint: `GET /gmail/intelligence/unanswered?limit=50&min_response_confidence=0`.

Assistant query support: ask FRIDAY questions such as “Which conversations are unanswered?” or “Which conversations am I waiting to reply to?” through `POST /assistant/chat`.

Detection contract:
1. Group locally stored messages by Gmail `thread_id`; messages without a thread ID are treated as individual conversations.
2. Sort each group by message date and local row ID.
3. Treat Gmail's `SENT` label as the authoritative outgoing-message signal.
4. Exclude a conversation when its latest stored message is labeled `SENT`.
5. Flag a conversation only when its latest stored message is not labeled `SENT` and its stored analysis (or rules-based fallback) says a response is required.
6. Return confidence, priority, waiting duration, message count, and detection rationale.

Important limitation: this is based on messages synchronized into FRIDAY's database, not a guaranteed complete Gmail thread. If an outgoing reply has not been synchronized, a conversation may be flagged incorrectly. The API explicitly reports `complete_thread_guarantee: false`; users should verify in Gmail. No message is sent and no record is modified.

## Database and migration impact

No schema changes or migrations were introduced. The existing `EmailMessage.thread_id`, Gmail labels, and `EmailAnalysis.requires_response` / `response_confidence` fields are reused.

## Verification

New regression coverage is in `tests/test_phase3_email_enhancements.py`:
- Structured summaries expose per-email analysis without breaking the narrative digest.
- Search ranks relevant subject/body evidence.
- A thread with a latest `SENT` message is excluded from unanswered results.
- Natural-language unanswered queries use the thread service.

The focused Phase 3 and existing related regression set is recorded in the release notes. Full-suite execution should be repeated in the developer's normal project environment; the source snapshot provided for this task had flattened archive paths and did not include a live Gmail/PostgreSQL environment.
