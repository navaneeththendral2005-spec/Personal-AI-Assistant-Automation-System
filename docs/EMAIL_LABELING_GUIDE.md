# FRIDAY Email Labeling Guide — Schema v2

**Status:** Draft for 20-email pilot  
**Schema version:** `v2`  
**Guide version:** `v2.0-draft`  
**Applies to:** Blind human labeling of FRIDAY email records  
**Privacy:** This guide contains synthetic examples only. Real email content and identifiers must remain local.

> **Pilot rule:** Label 20 emails using this draft. Review disagreements and confusing cases once, revise the guide once, then freeze the resulting version. Do not change definitions silently after the freeze.

---

## 1. Purpose and ground-truth principles

Label what the email communicates and what it expects from the recipient—not what a model predicts, what the sender's name suggests, or what the subject line alone implies.

1. Make each decision from the available email context. Use the body when available.
2. Do not use model predictions, rule outputs, previous annotations, or automated suggestions as evidence.
3. Do not infer a deadline from the received date. A deadline must be stated or clearly established by the email.
4. Do not guess. Use `unsure=yes` and explain the ambiguity in `note`.
5. Labels are changed only when the written definitions in this guide justify the change.
6. Treat the four tasks as distinct judgments. One label must not automatically determine another except where an explicit consistency rule below applies.

## 2. Schema v2

Each email receives these fields:

| Field | Allowed values | Meaning |
|---|---|---|
| `is_spam` | `yes`, `no` | Whether the email is unwanted, deceptive, or unsolicited under Section 3. |
| `needs_response` | `yes`, `no` | Whether a real person or organization is genuinely waiting for a reply, confirmation, decision, or action from you. |
| `priority` | `URGENT`, `NEEDS_REPLY`, `INFORMATIONAL` | How soon the email requires attention under Section 4. |
| `category` | `JOB`, `FINANCE`, `PERSONAL`, `PROMOTIONS`, `UPDATES`, `OTHER` | The email's primary topic or purpose. |
| `unsure` | `yes`, `no` | Whether one or more judgments remain genuinely uncertain. |
| `note` | Free text; may be blank | Brief explanation of uncertainty or an unusual boundary case. |

### 2.1 Required-label and uncertainty policy

For a completed row, all four task labels (`is_spam`, `needs_response`, `priority`, and `category`) must be valid values. `unsure` and `note` are also present as workbook fields.

- If you can confidently label all four tasks, set `unsure=no`; `note` may be blank.
- If one or more fields cannot be decided from the available evidence, set `unsure=yes`, explain why in `note`, and leave only the genuinely uncertain task field(s) blank.
- Do not fill an uncertain field merely to make the row importable.
- A row with an uncertain required task field is intentionally incomplete and will not count as a verified complete label.
- If the email is malformed, truncated in a way that prevents judgment, or lacks essential context, mark it unsure rather than inventing context.

## 3. Spam (`is_spam`)

### `yes`

Use `yes` when the email is unwanted, deceptive, or unsolicited. This includes phishing, impersonation, malicious links or attachments, scams, and unsolicited bulk messages whose content is irrelevant or deceptive.

### `no`

Use `no` for legitimate correspondence, expected service notifications, messages from organizations you use, and marketing from a company or mailing list you signed up for. Automation alone does not make an email spam. Marketing language alone does not make an email spam.

### Decision rule

Judge whether the message is unwanted or deceptive independently of its topic. A legitimate subscribed marketing email is `is_spam=no` and normally `category=PROMOTIONS`. A deceptive fake offer is `is_spam=yes`, even if it imitates a promotion or job notice.

Spam does not automatically mean the message is urgent. Do not let threatening or manipulative wording manufacture urgency. Unless there is a separate, genuine time-sensitive action for you, spam should be `needs_response=no` and `priority=INFORMATIONAL`. If the apparent topic is identifiable, still assign its best-fit category; use `OTHER` only when no category fits.

### Synthetic examples

| Invented email | `is_spam` | Reason |
|---|---|---|
| “You are subscribed to our weekly technology deals. This week's offers are inside.” | `no` | The recipient subscribed; this is legitimate marketing. |
| “Your bank account will be closed in 10 minutes. Send your password to this unfamiliar address to stop it.” | `yes` | Deceptive credential request and coercive claim. |

## 4. Response requirement (`needs_response`) and priority

These fields are related but are not interchangeable.

### 4.1 `needs_response`

Use `yes` only when a real person or organization is genuinely waiting for an answer, confirmation, decision, requested information, or an action from you.

Use `no` when no response or action is expected. The fact that you *could* reply, click, read, or optionally follow up does not make a response necessary.

Examples:

- “Could you confirm whether you can attend the interview?” → `yes`.
- “Your account statement is ready to view.” → `no`.
- “Your parcel is out for delivery.” → `no`.
- “Please send the missing invoice number.” → `yes`.

### 4.2 `priority`

#### `URGENT`

Use when the email establishes a genuinely time-sensitive or high-impact matter requiring prompt attention. Evidence may include an imminent appointment, a stated near-term deadline, an active security or service incident, or a consequential decision due soon.

Do not infer urgency from dramatic wording, capital letters, repeated exclamation marks, or the word “urgent” alone. Look for a real deadline, imminent event, or material consequence.

#### `NEEDS_REPLY`

Use when a response, confirmation, decision, or requested information is expected, but the email does not establish that it needs immediate handling.

#### `INFORMATIONAL`

Use when no response or action is expected and there is no separate urgent matter. The email primarily informs, confirms, or provides material for awareness.

### 4.3 Priority and response consistency

| `needs_response` | `priority` | Valid? | Explanation |
|---|---|---|---|
| `yes` | `URGENT` | Yes | A reply/action is expected urgently. |
| `yes` | `NEEDS_REPLY` | Yes | A reply/action is expected, without immediate urgency. |
| `yes` | `INFORMATIONAL` | **No** | A required response cannot be merely informational. |
| `no` | `URGENT` | Yes | Immediate attention/action is needed, but nobody is waiting for a reply. |
| `no` | `NEEDS_REPLY` | **No** | This priority means a response is expected. |
| `no` | `INFORMATIONAL` | Yes | No response is expected and no urgent action is established. |

The importer must flag invalid combinations. It must never silently rewrite one label to make the row consistent.

### Synthetic examples

| Invented email | `needs_response` | `priority` | Reason |
|---|---|---|---|
| “Your technical interview begins in 30 minutes. Please confirm your attendance now.” | `yes` | `URGENT` | A response is expected immediately. |
| “Could you send the purchase-order reference when convenient?” | `yes` | `NEEDS_REPLY` | A response is expected, but no immediate deadline is stated. |
| “A security alert reports an active account compromise. Secure your account immediately; no reply is requested.” | `no` | `URGENT` | Prompt action is needed, but no one is waiting for a reply. |
| “Your monthly statement is available in the account portal.” | `no` | `INFORMATIONAL` | Routine information; no response is expected. |

## 5. Category

Choose exactly one category based on the email's primary purpose. Do not classify solely from sender branding or incidental words.

### `JOB`

Recruitment, job openings, internships, applications, interviews, assessments, offers, recruiter communication, and career opportunities directly related to employment.

### `FINANCE`

Banking, payments, bills, invoices, taxes, card or account statements, financial transactions, and financial-service matters.

### `PERSONAL`

Direct correspondence or arrangements involving friends, family, or personal acquaintances, including personal plans and personal-life updates.

### `PROMOTIONS`

Legitimate marketing, discounts, sales, product offers, advertising, and promotional newsletters. This category is not spam by definition; apply `is_spam` separately.

### `UPDATES`

Service, account, delivery, subscription-status, product/system, and transaction-status notifications whose main purpose is to inform about an existing service or event rather than sell something. Routine receipts and service confirmations generally belong here; financial bills, payments, and statements belong in `FINANCE`.

### `OTHER`

A relevant email that does not fit any of the five categories above after applying the decision rules. Do not use `OTHER` merely because the email is unfamiliar or difficult; use `unsure=yes` when evidence is insufficient.

### Synthetic examples

| Invented email | Category | Reason |
|---|---|---|
| “Applications are open for the summer data analyst internship.” | `JOB` | Specific employment opportunity. |
| “Your electricity bill is due on the 15th.” | `FINANCE` | Bill and payment obligation. |
| “Are we still meeting for dinner this Saturday?” | `PERSONAL` | Direct personal arrangement. |
| “Use your subscriber discount for 20% off this weekend.” | `PROMOTIONS` | Sales-oriented offer. |
| “Your parcel status changed to out for delivery.” | `UPDATES` | Existing delivery status. |
| “The residents' association has published the minutes of its annual meeting.” | `OTHER` | Relevant, but not one of the defined topic classes. |

## 6. Confusable-pair decision rules

Use the following rules when two labels seem plausible. The examples are invented and are not training data.

### 6.1 Spam vs PROMOTIONS

**Rule:** Legitimate marketing from a company or mailing list the recipient signed up for is `is_spam=no`, `category=PROMOTIONS`; deceptive, unwanted, or unsolicited bulk content is `is_spam=yes`.

| Invented email | Correct interpretation |
|---|---|
| “You opted into our bookstore newsletter. Members receive 15% off this week's releases.” | `is_spam=no`, `category=PROMOTIONS` |
| “Claim a guaranteed cash prize by entering your bank password on this unfamiliar page.” | `is_spam=yes`; deceptive content, not a legitimate promotion |

### 6.2 `needs_response` vs `priority`

**Rule:** `needs_response` asks whether someone is waiting for a reply/action; `priority` asks how urgently the matter needs attention. Do not infer one from the other except for the explicit consistency table in Section 4.3.

| Invented email | `needs_response` | `priority` |
|---|---|---|
| “Please confirm your interview slot by 5 PM today.” | `yes` | `URGENT` |
| “A critical security alert requires you to reset your password now; this is an automated notice and no reply is requested.” | `no` | `URGENT` |

### 6.3 UPDATES vs PROMOTIONS

**Rule:** If the primary purpose is to inform about an existing account, service, delivery, or product change, choose `UPDATES`; if it is to persuade the recipient to buy, subscribe, upgrade, or use an offer, choose `PROMOTIONS`.

| Invented email | Category |
|---|---|
| “Your cloud-storage subscription renewed successfully today.” | `UPDATES` |
| “Upgrade your cloud-storage plan today and save 30%.” | `PROMOTIONS` |

### 6.4 PERSONAL vs OTHER

**Rule:** Direct communication involving a recognizable personal relationship or personal arrangement is `PERSONAL`; a relevant message without that relationship and without a better-fitting category is `OTHER`.

| Invented email | Category |
|---|---|
| “Hey, are you free to visit us this Sunday? The family would love to see you.” | `PERSONAL` |
| “The local residents' association has published the public meeting agenda.” | `OTHER` |

### 6.5 JOB vs UPDATES

**Rule:** Choose `JOB` when the main purpose is a specific opening, application, recruitment step, interview, assessment, or offer; choose `UPDATES` for general platform or service activity that only incidentally mentions employment.

| Invented email | Category |
|---|---|
| “The recruiter has invited you to the second-round interview for the analyst role.” | `JOB` |
| “Our careers platform has updated its privacy policy and account settings.” | `UPDATES` |

### 6.6 FINANCE vs UPDATES

**Rule:** Choose `FINANCE` for a bill, payment, invoice, tax, bank/card statement, or financial transaction; choose `UPDATES` for a generic service/account status message not primarily about money.

| Invented email | Category |
|---|---|
| “Your credit-card payment of ₹2,400 has been received.” | `FINANCE` |
| “Your profile settings were saved successfully.” | `UPDATES` |

## 7. Labeling decision order

Apply this sequence to every email:

1. **Read the available context.** Use sender, subject, snippet, and body preview together. Do not rely on the subject alone.
2. **Decide `is_spam`.** Determine whether the message is unwanted, deceptive, or unsolicited.
3. **Decide `needs_response`.** Ask whether a real person or organization is genuinely waiting for a reply, confirmation, decision, or action.
4. **Decide `priority`.** Determine whether attention is urgent, a non-urgent response is expected, or the email is informational. Apply the consistency table.
5. **Decide `category`.** Choose the single best category for the main purpose.
6. **Decide `unsure` and write `note`.** Mark uncertainty honestly; do not guess to complete a row.

The order is a workflow aid, not permission to derive one task's label from another. Re-check each task against its own definition.

## 8. When unsure

Mark `unsure=yes` when the available content does not support a confident decision—for example, missing context, ambiguous intent, truncated content, unclear sender relationship, or conflicting signals.

- Leave only the uncertain task field(s) blank.
- Complete other fields only when their definitions can be applied confidently.
- Add a short, factual note describing what is missing or ambiguous.
- Do not use `OTHER` as a substitute for uncertainty.
- Do not search external sources or contact senders to resolve a label. Label only from the local email data available in the workbook.
- Do not use model output, rules, or old labels to resolve uncertainty.

## 9. Pilot, one-time refinement, and freeze

### Pilot

1. Create a blind workbook containing 20 emails.
2. Label those emails using this draft without viewing old labels or model outputs.
3. Record uncertainty and concise notes where needed.
4. Review aggregate patterns and the difficult examples after the pilot. Do not change labels merely to improve agreement with old labels or a model.

### One-time refinement

After the 20-email pilot, revise this guide once to resolve demonstrated ambiguities. Document each changed definition or decision rule and why it changed. The pilot labels affected by a changed rule must be reviewed and relabeled under the revised guide.

### Freeze

After that single refinement, mark the guide **FROZEN** and assign a final version (for example, `v2.1`). Use that exact version for all subsequent production labeling and record the guide version with each imported batch.

### Changes after freeze

Any later semantic change requires a new guide version (for example, `v2.2` or `v3.0`, depending on scope), a changelog entry, and an explicit list of affected rows/batches that must be relabeled. Never silently reinterpret existing labels. Cosmetic edits that do not change meaning do not require relabeling but must not alter the frozen decision rules.

## 10. Privacy and handling

- Real email content, sender addresses, subjects, bodies, and email identifiers are private local data. Do not paste them into documentation, issue trackers, public logs, or committed tests.
- Use synthetic emails only in tests and documentation.
- Keep labeling workbooks, exports, imports, reports, and manifests in git-ignored private directories.
- Console output and reports must contain aggregate counts only; never print email content or identifiers.
- The workbook is a blind labeling surface. It must not expose previous labels, final labels, predictions, rule outputs, LLM outputs, review fields, or label provenance.
- Full email bodies remain in the local database. A workbook body preview is only for context and is not a replacement for the stored body.

## 11. Version history

| Version | Status | Summary |
|---|---|---|
| `v2.0-draft` | Pilot draft | Defines the four-task schema, six categories, uncertainty policy, consistency rules, confusable pairs, and pilot/freeze process. |
| `v2.1` (reserved) | Final after pilot | One-time refinement based on the 20-email pilot; freeze before production labeling. |
