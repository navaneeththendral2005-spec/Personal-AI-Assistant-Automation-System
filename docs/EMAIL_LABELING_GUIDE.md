# FRIDAY Email Labeling Guide — v2.1

**Status:** FROZEN — use this version for blind human ground-truth labeling  
**Schema version:** `v2.1`  
**Guide version:** `v2.1`  
**Applies to:** Blind human labeling of FRIDAY email records  
**Privacy:** Examples below are invented. Never copy real email content, sender addresses, subjects, or identifiers into committed documentation or reports.

> **Freeze statement:** The semantic definitions in this guide are frozen as v2.1. Any later semantic change requires a new guide version (v2.2 or later), a changelog entry, and identification and relabeling of every affected row. Do not silently reinterpret existing labels. Only labels personally entered by the human annotator under this guide count as ground truth. AI-generated drafts and legacy labels are not automatically ground truth and are never auto-converted.

## 1. Ground-truth principles and decision order

Label what FRIDAY should do with the message, using the full available context. Do not use model predictions, rule outputs, AI suggestions, old labels, sender reputation alone, or subject lines alone as labeling evidence.

Apply this order to every email:

1. **Spam:** Is this junk FRIDAY should hide from the user's useful inbox?
2. **Needs response/action:** Is a specific request directed at the user, or has the user already started a process that now requires a response, confirmation, decision, verification, or next step?
3. **Priority:** Is there a real deadline or imminent event within approximately 48 hours that the user would actually act on?
4. **Category:** Choose the single best topic category from the message body and purpose.
5. **Unsure:** If evidence is insufficient for any field, mark uncertainty and leave only that field blank.

Treat the four task labels as separate decisions. Apply only the explicit consistency checks in Section 5. Never auto-correct labels.

## 2. Schema and allowed values

| Field | Allowed values | Operational meaning |
|---|---|---|
| `is_spam` | `yes`, `no` | Whether FRIDAY should treat the message as junk and hide it. |
| `needs_response` | `yes`, `no` | Whether a specific request or an already-started process requires a reply, confirmation, decision, verification, or next action from the user. |
| `priority` | `URGENT`, `NEEDS_REPLY`, `INFORMATIONAL` | How soon the user should attend to the message. |
| `category` | `JOB`, `FINANCE`, `PROMOTIONS`, `UPDATES`, `OTHER` | The message's primary purpose. |
| `unsure` | `yes`, `no` | Whether one or more fields cannot be decided confidently. |
| `note` | Free text; may be blank | Short reason for uncertainty or an unusual boundary case. Use synthetic wording in tests and docs only. |

### 2.1 Required labels and uncertainty

- If all four task labels are clear, fill all four and set `unsure=no`; note may be blank.
- If a field genuinely cannot be determined, set `unsure=yes`, explain the uncertainty in `note`, and leave only that field blank. Do not guess to make the row complete.
- An incomplete unsure row is not a verified complete annotation.
- A blank note does not establish who authored a row. Provenance must be established separately; empty-note rows are not automatically human labels.

## 3. Operational definitions

### 3.1 Spam — `is_spam`

**`yes`** only for junk the user would never want: deceptive or irrelevant unsolicited bulk, phishing, scams, impersonation, malicious links or attachments, and fraudulent requests. The cost of hiding a legitimate recruiter or application email is higher than the cost of showing a newsletter.

**`no`** for legitimate correspondence, expected notifications, and legitimate marketing, including newsletters the user finds annoying. A subscribed or otherwise legitimate newsletter is not spam. Automation, bulk delivery, unsubscribe links, or annoyance alone do not make a message spam.

FRIDAY should use a promotion score to rank legitimate promotional mail lower; promotion classification must never itself hide the message.

### 3.2 Needs response/action — `needs_response`

**`yes`** when a specific request is directed at the user, or the user has already started a process and the next step is now expected from them. This includes a requested reply, confirmation, interview slot, assessment, verification step, or application step already in progress.

**`no`** for generic invitations and optional calls to action such as “Apply now”, “Shop now”, “Register”, “Explore roles”, or “Connect with us” when the user has not started that process and no specific response/action is expected. Generic promotional prompts do not count as a request directed at the user.

Sent mail (the user is the sender) is judged from the user's side. If the user sent the message and is waiting for the other party, `needs_response=no`.

### 3.3 Priority

**`URGENT`** only for a real deadline within approximately 48 hours for something the user would actually do (a relevant application, interview, assessment, security code or account action), or an imminent event the user is part of. A one-time code is urgent only while it remains valid, usually 10–15 minutes. A code that has expired or whose validity window is no longer relevant is `INFORMATIONAL` unless another real urgent action exists.

**`NEEDS_REPLY`** when a reply, confirmation, decision, verification, assessment or already-started application step is expected, but no qualifying urgent deadline or imminent event exists.

**`INFORMATIONAL`** when no specific response/action is expected and no qualifying urgent matter exists.

Contest deadlines, “last chance” language, countdowns, limited-time sales, and “24-hour sale” wording never create `URGENT` by themselves. Priority and needs_response are separate questions: an urgent security action can be `needs_response=no`; a requested interview confirmation can be `needs_response=yes` and `NEEDS_REPLY` if it is not due within about 48 hours.

### 3.4 Category

Choose one category based on the message body and primary purpose, not merely the sender brand or a templated subject.

- **`JOB`:** A concrete job, internship, PPO, application, recruiter communication, interview, assessment, or offer. A receipt for a job application already submitted is also `JOB`.
- **`FINANCE`:** Banking, payments, bills, invoices, taxes, financial transactions, financial accounts, broker market news/digests, and financial-service information.
- **`PROMOTIONS`:** Legitimate marketing, newsletters, sales, discounts, product offers, generic invitations to apply/register/join, contests, courses, career-advice newsletters, and upgrade or feature-use nudges.
- **`UPDATES`:** Passive status or service information about an existing account, application process, delivery, profile view, rewards update, security notice, or one-time/verification code. Use when the message reports a state/change rather than trying to persuade the user to buy, join, upgrade, or use a feature.
- **`OTHER`:** Direct human communication or anything that fits none of JOB, FINANCE, PROMOTIONS, or UPDATES. This replaces the former PERSONAL and OTHER distinction.

#### Finance newsletter boundary

Choose `FINANCE` for broker or financial-provider market news, market digests, portfolio/account information, and analysis whose primary purpose is to inform about financial markets or the user's finances. Choose `PROMOTIONS` when the primary purpose is to sell, advertise, induce an IPO subscription, open an account, buy a product, upgrade a service, or take up a financial offer. If a market digest contains a secondary product advertisement, classify by the dominant body purpose; if the body is principally an offer, use `PROMOTIONS`.

## 4. Seven specific rulings

Each ruling is mandatory. Examples are invented.

### Ruling 1 — JOB versus PROMOTIONS

Use `JOB` only when the message body is about a concrete job/internship/PPO/application or a specific recruitment step. A contest, course, generic “companies are hiring” message, career-advice newsletter, or broad opportunity roundup is `PROMOTIONS` unless it contains a specific role/application relevant to the user.

- Example A: “Data Analyst Intern — submit your application for requisition 482 by Friday.” → `JOB`.
- Example B: “Join our career-growth webinar and discover companies that may hire this year.” → `PROMOTIONS`.

### Ruling 2 — UPDATES versus PROMOTIONS

Passive status information about an existing account, service, or process is `UPDATES`. A nudge to use a feature, join a contest, connect with someone, buy, subscribe, or upgrade is `PROMOTIONS`.

- Example A: “Your profile was viewed by a recruiter; your reward balance has been updated.” → `UPDATES`.
- Example B: “Connect with three professionals and enter our networking challenge.” → `PROMOTIONS`.

### Ruling 3 — Subject/body disagreement

When the subject is templated or misleading and the subject and body disagree, categorize by the body’s actual dominant purpose. Do not let a subject prefix such as “Update” override promotional body content.

- Example A: Subject “Update: application accepted”; body confirms the user's application status and next process state. → `JOB`.
- Example B: Subject “Account update”; body is primarily an offer to upgrade to a paid plan. → `PROMOTIONS`.

### Ruling 4 — Finance newsletters and broker market digests

Market news, market digests, and financial analysis are `FINANCE`. Sales/IPO nudges and product-acquisition campaigns are `PROMOTIONS`. Classify by the dominant body purpose, not merely by the broker's identity.

- Example A: “Daily market close: index movements, sector performance, and bond yields.” → `FINANCE`.
- Example B: “Subscribe to this new IPO today; open a premium trading account to access the offer.” → `PROMOTIONS`.

### Ruling 5 — Sent mail

Judge from the user's perspective. If the user sent the message and is waiting for a response from someone else, `needs_response=no`; the other party's expected reply is not an action FRIDAY should ask the user to perform now.

- Example A: The user sent “Could you share the interview feedback?” and is waiting. → `needs_response=no`; normally `INFORMATIONAL` while waiting.
- Example B: The user sent “I confirm that I will attend the interview.” → `needs_response=no`; the user has already completed their response.

### Ruling 6 — Receipt for an application already submitted

A receipt or confirmation that the user has applied for a job is `JOB`, `needs_response=no`, and `INFORMATIONAL`, unless the body explicitly requests a new step or states a genuine imminent deadline.

- Example A: “You applied for the Software Intern role. We have received your application.” → `JOB`, `no`, `INFORMATIONAL`.
- Example B: “Your application was submitted successfully; we will contact you if shortlisted.” → `JOB`, `no`, `INFORMATIONAL`.

### Ruling 7 — One-time and verification codes

A self-requested one-time code is `UPDATES`. It is `URGENT` only while the code is valid and the user is actively completing the related sign-in/verification action (typically a 10–15 minute validity window). Otherwise it is `INFORMATIONAL`. Do not infer urgency after expiry.

- Example A: “Your sign-in code is valid for 10 minutes” arrives while the user is signing in. → `UPDATES`, `needs_response=no`, `URGENT`.
- Example B: A sign-in code notification is reviewed after its validity window has expired and no further action is needed. → `UPDATES`, `needs_response=no`, `INFORMATIONAL`.

## 5. Confusable-pair rules and consistency checks

### 5.1 Spam versus PROMOTIONS

Legitimate newsletters and subscribed marketing are `is_spam=no`, `category=PROMOTIONS`; deceptive, irrelevant unsolicited bulk or scams are `is_spam=yes`. Never mark a legitimate recruiter or job notice as spam merely because it is automated or promotional in tone.

- Example A: A subscribed retailer's weekly discount email → `no`, `PROMOTIONS`.
- Example B: A fake recruiter message asking for an advance fee → `yes`; categorize by its apparent job-related body only if that remains the best topic, otherwise `OTHER`.

### 5.2 Needs response versus priority

Decide whether a specific user action is expected first, then urgency separately. `URGENT` does not require a reply; `NEEDS_REPLY` does imply a response/action expectation.

- Example A: A security notice says “disable the compromised session now; do not reply.” → `needs_response=no`, `URGENT`.
- Example B: A recruiter asks the user to select one of three interview slots next week. → `needs_response=yes`, `NEEDS_REPLY`.

### 5.3 UPDATES versus PROMOTIONS

Existing state/status or security information is `UPDATES`; persuasion to buy, join, connect, use a feature, subscribe, or upgrade is `PROMOTIONS`.

- Example A: “Your account recovery email was changed.” → `UPDATES`.
- Example B: “Try our new account dashboard and unlock premium features.” → `PROMOTIONS`.

### 5.4 PERSONAL/OTHER legacy distinction versus v2.1 OTHER

In v2.1, direct human communication and anything outside the four named topical categories are `OTHER`. Do not create a PERSONAL label. A concrete job-related message remains `JOB` even if it comes from a person.

- Example A: A friend asks whether dinner is still on Saturday. → `OTHER`.
- Example B: A recruiter sends a specific interview invitation. → `JOB`.

### 5.5 JOB versus UPDATES

A concrete employment opportunity or active recruitment step is `JOB`; passive status about an existing application is still `JOB` under the application-receipt ruling. Generic career content is `PROMOTIONS`.

- Example A: “Your application for the Data Engineer role has moved to interview stage.” → `JOB`.
- Example B: “Five career tips to improve your profile” newsletter → `PROMOTIONS`.

### 5.6 FINANCE versus UPDATES

Financial information about balances, transactions, bills, statements, market movements, and financial accounts is `FINANCE`. General account/security status unrelated to a financial matter is `UPDATES`.

- Example A: “Your monthly brokerage statement is ready.” → `FINANCE`.
- Example B: “Your learning-platform password was changed.” → `UPDATES`.

### 5.7 Priority consistency

The following combinations are valid/invalid. The importer may flag invalid combinations, but must never auto-correct them.

| `needs_response` | `priority` | Valid? | Why |
|---|---|---|---|
| `yes` | `URGENT` | Yes | Specific action is expected urgently. |
| `yes` | `NEEDS_REPLY` | Yes | Specific action is expected, without qualifying urgency. |
| `yes` | `INFORMATIONAL` | **No** | A specific required action cannot be merely informational. |
| `no` | `URGENT` | Yes | Prompt action/attention is needed, but no reply is expected. |
| `no` | `NEEDS_REPLY` | **No** | This priority implies an expected response/action. |
| `no` | `INFORMATIONAL` | Yes | No specific action or urgent matter exists. |

- Example A: A requested interview confirmation due in 12 hours → `yes`, `URGENT`.
- Example B: A passive parcel status notification → `no`, `INFORMATIONAL`.

## 6. Legacy category mapping (reference only)

| Legacy category | v2.1 category mapping |
|---|---|
| `JOB` | `JOB` |
| `FINANCE` | `FINANCE` |
| `PROMOTIONS` | `PROMOTIONS` |
| `UPDATES` | `UPDATES` |
| `PERSONAL` | `OTHER` |
| `OTHER` | `OTHER` |

This table documents the conceptual mapping only. **Legacy labels are not auto-converted or overwritten.** Old labels remain historical data for comparison. Only labels personally entered blind under v2.1 count as new ground truth. AI drafts, including rows marked with `claude_draft:` and rows with unknown provenance, must never be promoted automatically.

## 7. When unsure

- Set `unsure=yes` if the available message context does not support a confident decision.
- Leave only the genuinely uncertain field(s) blank; fill other fields only when supported.
- Explain the uncertainty briefly in `note` without copying unnecessary personal content.
- Do not guess from sender domain, brand, subject prefix, model output, or historical labels.
- Do not mark unsure merely because a message is automated, unfamiliar, or a newsletter.

## 8. Changelog

- **v2.1:** Replaced broad legacy interpretations with operational FRIDAY actions; narrowed spam to deceptive/irrelevant junk only; broadened needs_response to specific requests and already-started job-hunt steps; restricted URGENT to real approximately-48-hour deadlines or imminent events; merged PERSONAL into OTHER; clarified JOB/PROMOTIONS, UPDATES/PROMOTIONS, subject/body, finance digest, sent-mail, application receipt, and OTP rules; added explicit legacy mapping and freeze policy.
- **v2.0-draft:** Earlier pilot draft. Superseded; not frozen ground truth guidance.

## 9. Freeze and versioning

**v2.1 is frozen.** Do not change these semantics during the v2.1 labeling round. If a genuine semantic correction becomes necessary, create v2.2 (or later), add a dated changelog entry, describe the exact changed rule, identify affected rows/batches, and relabel those affected rows blind. Never retroactively rewrite labels merely because a model prediction disagrees.
