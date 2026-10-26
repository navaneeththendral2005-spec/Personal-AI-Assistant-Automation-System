# FRIDAY Email Labeling Guide

Label an email's intent and requested action, not just its subject line. Use the full body when available. Do not treat the received date as an application deadline.

Never infer ground-truth labels from FRIDAY's predictions. Human annotations (`true_*`) are ground truth; `predicted_*`, `rules_*`, and `llm_*` fields are model outputs and must remain separate.

## 1. Labeling Order

Apply the labels in this order:

1. **Spam:** Decide `true_is_spam` first.
2. **Priority:** If `true_is_spam=yes`, leave `true_priority` blank for new-schema annotations. Otherwise, assign a non-spam priority.
3. **Response requirement:** Independently decide whether a response is expected.
4. **Category:** Choose the single best category for the email's main purpose.

Priority and response requirement are related but distinct. An email may be informational while still requiring a response. Conversely, an urgent notification may require immediate action without requiring a reply.

## 2. Spam (`true_is_spam`)

Allowed values: `yes`, `no`.

* **yes:** Unsolicited, deceptive, malicious, or irrelevant bulk content whose main purpose is unwanted promotion, fraud, phishing, malware distribution, or similar activity.
* **no:** Expected, subscribed-to, personally relevant, or legitimate service and career correspondence, including appropriate automated notifications.

Automation alone does not make an email spam. Marketing language alone does not make an email spam.

### Synthetic examples

**yes**

1. "You have won a luxury phone. Pay a processing fee through this unfamiliar link to claim it."
2. "Unrequested bulk message offering guaranteed investment returns and asking for your bank password."

**no**

1. "Your requested password reset is ready. Use the official account page to continue."
2. "You subscribed to the weekly engineering jobs digest. Here are this week's listings."

### Decision rule

Determine whether the email is spam before assigning its topic category.

If `true_is_spam=yes`, leave the new-schema `true_priority` blank. Still label `true_needs_response` and `true_category` according to their definitions.

A legitimate promotion should have `true_is_spam=no` and `true_category=PROMOTIONS`.

## 3. Priority (`true_priority`)

Allowed values for new annotations:

* `URGENT`
* `NEEDS_REPLY`
* `INFORMATIONAL`

Priority describes what to do with a **non-spam email**. Do not use `SPAM` as a priority for new annotations.

### URGENT

Use when a time-sensitive or high-impact action is needed soon. Examples include:

* A clearly stated near-term deadline.
* An imminent interview or assessment.
* An active incident requiring intervention.
* A security risk requiring prompt action.
* A consequential decision that must be made by a specific time.

Do not infer urgency solely from words such as "important" or "urgent." Look for evidence of actual time sensitivity or impact.

### NEEDS_REPLY

Use when a person or organization clearly expects you to reply, confirm, decide, or provide requested information, but there is no evidence that the response needs urgent handling.

### INFORMATIONAL

Use when the email provides useful or relevant information without a clear time-sensitive action or an expected reply.

### Priority decision rule

For non-spam emails, apply this order:

1. If a clear, time-sensitive or high-impact action is required, choose `URGENT`.
2. Otherwise, if a response is expected, choose `NEEDS_REPLY`.
3. Otherwise, choose `INFORMATIONAL`.

A question mark alone does not establish urgency. Likewise, an email should not be marked `NEEDS_REPLY` merely because replying is possible.

### Synthetic examples

**URGENT**

1. "Your technical interview starts in 30 minutes. Join using the meeting details below."
2. "Please approve the production rollback before 2 PM to restore the service."

**NEEDS_REPLY**

1. "Could you confirm whether you can attend Friday's project meeting?"
2. "Please send the missing invoice reference when you have a moment."

**INFORMATIONAL**

1. "Your monthly account statement is now available in the official portal."
2. "The team newsletter summarizes completed work and upcoming events. No action is requested."

## 4. Response Requirement (`true_needs_response`)

Allowed values: `yes`, `no`.

* **yes:** The sender explicitly or clearly expects a reply, confirmation, decision, or requested information from you.
* **no:** No reply is reasonably expected.

Reading a routine notice, opening an account page, or receiving an update does not by itself count as replying.

### Synthetic examples

**yes**

1. "Please confirm your interview availability for Tuesday or Wednesday."
2. "Can you send the missing purchase-order number?"

**no**

1. "Your password was changed successfully."
2. "Your subscribed weekly newsletter is ready to read."

### Decision rule

Evaluate response requirement independently of priority.

For example, an email can be `URGENT` with `true_needs_response=no` if immediate action is required but no reply is expected.

An email can be `INFORMATIONAL` with `true_needs_response=yes` if it asks for a non-urgent confirmation.

## 5. Topic Category (`true_category`)

Choose exactly one category based on the email's main purpose, not incidental words or sender branding.

### JOB

Job openings, internships, applications, recruiting, interviews, assessments, offers, and career advice directly related to a job search.

### FINANCE

Banking, bills, invoices, payments, taxes, card or account statements, and other personal or business financial activity.

### PERSONAL

Direct personal correspondence and personal-life arrangements involving friends, family, or personal contacts.

### PROMOTIONS

Legitimate marketing, sales, discounts, product offers, and advertising that is not spam under the definition above.

### UPDATES

Routine service notifications, account alerts, delivery and status notices, newsletters, and product or system updates that are not primarily about a job or financial transaction.

### OTHER

Relevant messages that do not fit any category above after applying the decision rules.

### Synthetic examples

**JOB**

1. "Applications are open for the Data Analyst internship. Submit your CV by Friday."
2. "Your recruiter scheduled a first-round interview for Tuesday."

**FINANCE**

1. "Your electricity bill is ready, with payment due on the 15th."
2. "Your bank card statement is available in the official account portal."

**PERSONAL**

1. "Would you like to have dinner with us this Saturday?"
2. "Here are the photos from our family trip."

**PROMOTIONS**

1. "Use code SPRING20 for 20% off selected items this weekend."
2. "A store you opted into emails from has announced its seasonal collection."

**UPDATES**

1. "Your parcel status changed to out for delivery."
2. "Version 2.1 is available with fixes to the calendar app."

**OTHER**

1. "The neighborhood association meeting minutes are attached."
2. "A general inquiry asks about a topic unrelated to work, finances, personal arrangements, marketing, or service updates."

## 6. Decision Rules for Confusable Categories

Apply these rules in order when more than one category seems plausible.

### 6.1 JOB vs UPDATES

Choose `JOB` when the main purpose is a specific job, internship, application, recruiting step, interview, assessment, or offer.

Choose `UPDATES` for general platform activity or a broad newsletter that only incidentally mentions hiring.

### 6.2 PROMOTIONS vs SPAM

Determine spam independently of category.

* Unsolicited, deceptive, malicious, or irrelevant bulk content: `true_is_spam=yes`.
* Legitimate marketing and discounts: `true_is_spam=no`, `true_category=PROMOTIONS`.

An automated sender or marketing language alone does not establish spam.

### 6.3 FINANCE vs UPDATES

Choose `FINANCE` when the main purpose is a bill, payment, invoice, bank or card statement, tax, or financial transaction.

Choose `UPDATES` for a generic account or service status alert that is not primarily about money.

### 6.4 PERSONAL vs OTHER

Choose `PERSONAL` for direct correspondence or arrangements involving friends, family, or personal contacts.

Choose `OTHER` for a relevant message with no clear personal relationship and no better-fitting category.

### 6.5 JOB vs OTHER

Choose `JOB` when the message directly concerns a job search or career event, including recruiting-platform notifications about a specific application or hiring step.

Choose `OTHER` when the career connection is incidental or absent.

### 6.6 PROMOTIONS vs UPDATES

Choose `PROMOTIONS` when the main purpose is to persuade you to buy, subscribe, or use an offer.

Choose `UPDATES` when the main purpose is to inform you about a service, account, delivery, or product change without a sales-oriented call to action.

## 7. Legacy Labels and Migration

Older annotations may contain `true_priority=SPAM`.

The schema migration adds `true_is_spam`:

* Legacy `true_priority=SPAM` maps to `true_is_spam=yes`.
* A valid legacy non-spam priority maps to `true_is_spam=no`.
* An empty or invalid legacy priority does not establish a spam label.
* An existing valid human-entered `true_is_spam` value must be preserved.

The migration must not rewrite or delete the original `true_priority` value.

Before using the new priority target for training, review legacy rows where `true_priority=SPAM`. Explicitly correct the priority when appropriate; never silently convert historical spam labels to `INFORMATIONAL`.

Human annotations remain ground truth. Model predictions must never be used to fill missing human labels.

## 8. Workflow and Privacy

1. Export a local dataset:

   `python -m scripts.export_email_dataset --limit 500 --output data/email_labels.csv`

2. Keep `data/email_labels.csv` private. It contains real email text and must never be committed.

3. Migrate an existing CSV explicitly:

   `python -m scripts.migrate_email_label_schema --input data/email_labels.csv`

   The migration creates an adjacent `.bak` file unless `--no-backup` is supplied.

4. Review migrated annotations before training. Migration must not inspect or change model predictions, modify evaluation splits, or fabricate labels.

5. Use synthetic examples for documentation and tests. Do not treat documentation examples as verified training data.
