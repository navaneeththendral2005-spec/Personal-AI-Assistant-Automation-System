"""Documentation contract tests for the frozen FRIDAY labeling guide v2.1.

These tests inspect documentation only and use no real email records.
"""
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "EMAIL_LABELING_GUIDE.md"


def test_guide_declares_frozen_v21_and_relabel_policy() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "# FRIDAY Email Labeling Guide" in text
    assert "v2.1" in text
    assert "**Schema version:** `v2.1`" in text
    assert "**Guide version:** `v2.1`" in text
    assert "**Status:** FROZEN" in text
    assert "v2.2" in text
    assert "relabeling" in text.lower()


def test_guide_defines_all_v21_schema_values_and_category_set() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for value in (
        "`is_spam`", "`needs_response`", "`priority`", "`category`",
        "`unsure`", "`note`", "`URGENT`", "`NEEDS_REPLY`",
        "`INFORMATIONAL`", "`JOB`", "`FINANCE`", "`PROMOTIONS`",
        "`UPDATES`", "`OTHER`",
    ):
        assert value in text
    assert "| `category` | `JOB`, `FINANCE`, `PROMOTIONS`, `UPDATES`, `OTHER` |" in text
    assert "Legacy category mapping" in text
    assert "`PERSONAL` | `OTHER`" in text


def test_guide_states_cross_field_consistency_rules_without_autocorrection() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "`yes` | `INFORMATIONAL` | **No**" in text
    assert "`no` | `NEEDS_REPLY` | **No**" in text
    assert "Never auto-correct labels" in text


def test_guide_contains_v21_confusable_pairs_and_invented_examples() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for heading in (
        "Spam versus PROMOTIONS",
        "Needs response versus priority",
        "UPDATES versus PROMOTIONS",
        "PERSONAL/OTHER legacy distinction versus v2.1 OTHER",
        "JOB versus UPDATES",
        "FINANCE versus UPDATES",
    ):
        assert heading in text
    for ruling in (
        "Ruling 1", "Ruling 2", "Ruling 3", "Ruling 4",
        "Ruling 5", "Ruling 6", "Ruling 7",
    ):
        assert ruling in text
    assert "Example A" in text and "Example B" in text


def test_guide_forbids_ai_drafts_as_ground_truth_and_protects_privacy() -> None:
    text = GUIDE.read_text(encoding="utf-8").lower()
    assert "ai-generated drafts" in text
    assert "never be promoted automatically" in text
    assert "email content" in text
    assert "identifiers" in text
    assert "sender addresses" in text

