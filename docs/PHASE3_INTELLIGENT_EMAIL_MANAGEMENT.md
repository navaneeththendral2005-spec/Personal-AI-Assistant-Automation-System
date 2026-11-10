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


## Real-inbox refinement pass — 2026-11-10

A manual validation against the developer's synchronized Gmail data returned HTTP 200 for sync/analyze, inbox summary, inbox question, unanswered threads, assistant chat, and daily briefing. The observed output exposed three quality issues: automated security notices could be mistaken for reply requests, marketing copy could be ranked as important, and broad career searches could match interview-course promotions. The following refinement pass addresses those cases:

- Automated/no-reply senders, routine system notices, spam/trash, and promotional/update messages are excluded from unanswered-response tracking.
- Response detection no longer treats the generic phrase `please review` as sufficient evidence of a reply request.
- Promotional messages default to LOW priority unless a trusted account/security-risk signal is present; promotional urgency language does not override this guard.
- Career search uses source email text rather than classifier explanations, supports combined interview/assessment/job queries, and rejects generic interview-course promotions without a concrete role/event signal.
- The local inbox digest is shorter, caps the number of items per section, preserves explicit eligibility/deadline evidence, and removes tracking query strings from displayed application links.
- Daily briefing filters routine promotions and updates out of high-priority and response-needed sections.
- Production classifier provenance was advanced to `friday-rules-v2`. Existing stored analyses are refreshed on the next `/gmail/intelligence/analyze` or `/gmail/intelligence/sync-and-analyze` run; no migration is required.

Focused verification after this refinement: 69 related tests passed, including classifier, summary, search, unanswered-thread, assistant, and Gmail sync/idempotency coverage. A complete local suite run in this packaging environment reached the final progress indicator but did not exit before the execution timeout; therefore no full-suite pass is claimed here. The developer should run the full suite once in their normal Windows environment after integrating the updated files.


## Final relevance and coverage refinement — 2026-11-10

The follow-up real-inbox review showed that a compound query asking for interview invitations, assessment deadlines, and recruiter requests could be resolved as a generic deadline query. It also showed that interview-preparation copy could receive inflated priority because it mentioned interviews, and that local thread coverage needed to be made more visible. This refinement addresses those findings:

- Compound career-action queries are resolved before generic deadline intent and each requested facet is gated on concrete evidence. Interview-preparation content and generic job digests do not satisfy an invitation/deadline/recruiter-request facet by keyword overlap alone.
- Assessment matches require assessment/test wording plus a detected deadline or explicit assessment completion-by language. Recruiter-request matches require a recruiter/talent/HR sender signal plus a direct reply/confirmation request.
- Automatic HIGH priority from interview wording now requires a concrete hiring event (invitation, scheduled interview, interview slot/details, or assessment scheduled). Generic interview readiness/practice content does not qualify by itself. Explicit shortlist-for and offer signals remain supported.
- Daily briefing's HIGH queue excludes generic opportunity/application-stage notices when they have neither a response requirement nor a detected deadline. Concrete interview/outcome/follow-up signals remain eligible.
- Unanswered detection now reports `coverage_status: PARTIAL_LOCAL_HISTORY`, total stored threads, and the number of single-message threads. This is transparency, not a claim that FRIDAY can fetch or prove complete Gmail thread history. Users must sync relevant conversation history and verify candidates in Gmail.

No new services, tables, columns, or migrations were added.

### Focused regression coverage

The Phase 3 tests now cover facet-specific career search, exclusion of interview-preparation content from HIGH priority, and explicit partial-thread-coverage metadata.

## Reliability refinement — 2026-11-10

### Evidence-gated career-action search

The `/gmail/intelligence/ask` endpoint now applies stricter evidence gates when a query explicitly asks for interview invitations, assessment deadlines, or recruiter response requests. For assessment deadlines, the assessment phrase and deadline cue must occur in the same sentence. Generic job alerts, automated digest posts, LinkedIn notification digests, and interview-preparation promotions are excluded from these specific action searches. These targeted searches use deterministic local evidence rather than asking a generative model to broaden the selected results.

### Checkpointed historical thread synchronization

`POST /gmail/intelligence/sync-and-analyze` now accepts `history_max_threads` (default 50, maximum 500). In addition to syncing recent messages, it verifies a bounded number of thread IDs already present in FRIDAY's database using read-only Gmail `threads.get` calls. Successful verifications are recorded in `gmail_thread_sync_states`; later requests continue with unverified thread IDs. Failed attempts retain only the exception class and can be retried on a later run.

Apply the additive migration before using this feature:

```powershell
alembic upgrade head
```

The `/gmail/intelligence/unanswered` response reports `verified_stored_thread_count`, `unverified_stored_thread_count`, and `coverage_status`. `STORED_THREADS_VERIFIED` means every thread ID currently known to FRIDAY was fetched successfully. It does **not** mean FRIDAY has discovered every thread in the Gmail mailbox; `mailbox_wide_guarantee` remains false.

### Competition and skills-challenge classification

Hackathons, HackQuest, coding competitions, and similar student challenges are not treated as employment opportunities unless concrete hiring-stage evidence is present. Explicit closing dates remain eligible for deadline extraction and prioritization.
