from __future__ import annotations

from pathlib import Path

import pytest

from scripts.analyze_legacy_label_patterns import (
    LegacyPatternError,
    analyze_legacy_patterns,
    write_report,
)


def row(identifier: str, *, spam: str = "no", response: str = "no",
        priority: str = "INFORMATIONAL", category: str = "JOB",
        sender: str = "", body: str = "", subject: str = "",
        thread_count: str = "") -> dict[str, str]:
    return {
        "email_id": identifier,
        "sender": sender,
        "subject": subject,
        "snippet": "",
        "body_text": body,
        "thread_message_count": thread_count,
        "in_reply_to": "",
        "references": "",
        "true_is_spam": spam,
        "true_needs_response": response,
        "true_priority": priority,
        "true_category": category,
    }


def test_aggregate_counts_and_shares_without_exposing_ids_or_content() -> None:
    rows = [
        row("synthetic-1", spam="yes", response="yes", priority="URGENT",
            category="JOB", body="Please reply before the deadline. Unsubscribe?"),
        row("synthetic-2", spam="no", response="no", priority="INFORMATIONAL",
            category="FINANCE", body="Your statement is ready."),
    ]
    report = analyze_legacy_patterns(rows)
    assert report["input_row_count"] == 2
    assert report["tasks"]["is_spam"]["classes"]["yes"]["count"] == 1
    assert report["tasks"]["is_spam"]["classes"]["yes"]["share"] == 0.5
    assert report["tasks"]["needs_response"]["classes"]["yes"]["feature_signals"]["deadline_words"]["count_present"] == 1
    serialized = str(report)
    assert "synthetic-1" not in serialized
    assert "Please reply" not in serialized


def test_legacy_spam_fallback_from_priority_spam() -> None:
    rows = [row("synthetic-1", spam="", priority="SPAM")]
    report = analyze_legacy_patterns(rows)
    assert report["tasks"]["is_spam"]["classes"]["yes"]["count"] == 1
    assert report["tasks"]["priority"]["classes"]["SPAM"]["count"] == 1


def test_domain_inconsistency_and_top_domain_aggregation_are_present_without_console_output() -> None:
    rows = [
        row("synthetic-1", spam="yes", sender=""),
        row("synthetic-2", spam="no", sender=""),
    ]
    report = analyze_legacy_patterns(rows)
    assert report["tasks"]["is_spam"]["domain_inconsistency"]["domains_with_multiple_labels"] == 0
    assert "top_registered_domains_by_class" in report["tasks"]["is_spam"]


def test_duplicate_ids_are_rejected_without_echoing_identifier() -> None:
    rows = [row("secret-synthetic-id"), row("secret-synthetic-id")]
    with pytest.raises(LegacyPatternError, match="identity validation"):
        analyze_legacy_patterns(rows)


def test_report_refuses_overwrite_and_backs_up_when_allowed(tmp_path: Path) -> None:
    output = tmp_path / "reports" / "patterns.json"
    backup_dir = tmp_path / "data" / "backups"
    output.parent.mkdir(parents=True)
    output.write_text("old", encoding="utf-8")
    with pytest.raises(LegacyPatternError, match="already exists"):
        write_report({"safe": True}, output, backup_dir=backup_dir)
    write_report({"safe": True}, output, overwrite=True, backup_dir=backup_dir)
    assert output.read_text(encoding="utf-8").strip() == '{\n  "safe": true\n}'
    assert len(list(backup_dir.glob("patterns.*.json"))) == 1
