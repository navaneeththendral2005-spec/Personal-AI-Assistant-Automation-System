# FRIDAY Email Labeling Guide (Task 1)

Label the email's intent and requested action, not just its subject line. Use the full body when available. Do not treat the received date as an application deadline. If uncertain, choose the best-supported label; never infer labels from FRIDAY's predictions. `true_needs_response` is `yes` only when a person is expected to reply or take a response-like action.

## Priority (`true_priority`)
- **URGENT** — time-sensitive or high-impact action needed soon. Examples: (1) “Your account will be locked in one hour unless you verify your identity through the official company portal.” (2) “Please approve the production outage rollback before 2 PM today.”
- **NEEDS_REPLY** — a reply, decision, confirmation, or requested action is expected, but it is not clearly urgent. Examples: (1) “Could you confirm whether you can attend Friday's meeting?” (2) “Please review the attached project outline and send your feedback.”
- **INFORMATIONAL** — useful or relevant information with no clear response required. Examples: (1) “Your monthly statement is now available in your account.” (2) “The team newsletter summarizes this week's completed work.”
- **SPAM** — unsolicited, deceptive, malicious, or irrelevant bulk content. Examples: (1) “You won a prize; pay a fee using this unfamiliar link to claim it.” (2) “Repeated unsolicited marketing for a product you never requested.”

## Response required (`true_needs_response`)
- **yes** — a person is expected to reply, confirm, decide, or provide requested information. Examples: (1) “Please confirm your interview availability.” (2) “Can you send the missing invoice number?”
- **no** — no reply is reasonably expected. Examples: (1) “Your password was changed successfully.” (2) “Weekly newsletter: five articles you may enjoy.”

## Topic category (`true_category`)
- **JOB** — jobs, internships, interviews, recruiting, career opportunities. Examples: (1) “Data Analyst Intern applications are open.” (2) “Your interview is scheduled for Tuesday.”
- **FINANCE** — banking, bills, invoices, payments, taxes, financial statements. Examples: (1) “Your electricity bill is ready to pay.” (2) “Your card statement is available.”
- **PERSONAL** — direct personal correspondence or personal-life arrangements. Examples: (1) “Dinner at our place on Saturday?” (2) “Here are the photos from our family trip.”
- **PROMOTIONS** — sales, discounts, advertising, offers, promotional campaigns. Examples: (1) “Get 30% off this weekend.” (2) “Shop the new seasonal collection.”
- **UPDATES** — service notifications, account alerts, newsletters, product or system updates. Examples: (1) “Your delivery status has changed.” (2) “Version 2.1 is now available.”
- **OTHER** — messages that do not fit another category clearly. Examples: (1) “Minutes from the neighborhood association meeting.” (2) “A general inquiry about an unfamiliar topic.”

## Workflow and privacy
1. Run `python -m scripts.export_email_dataset --limit 500 --output data/email_labels.csv`.
2. Run `python -m scripts.label_emails --input data/email_labels.csv` and label each email. Enter a listed label, press Enter to leave a field blank, or enter `q` to save and quit.
3. Re-run the labeler later; already complete rows are skipped. Labels save after each email.
4. Keep `data/email_labels.csv` private. It contains real email text and must never be committed. The examples above are synthetic.
