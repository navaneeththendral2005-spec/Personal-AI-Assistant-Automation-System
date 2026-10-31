"""Documentation contract tests for the FRIDAY email labeling guide v2.

These tests use synthetic examples and inspect documentation only. They never
load, print, or depend on real email records.
"""
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "EMAIL_LABELING_GUIDE.md"


def test_guide_declares_v2_draft_and_pilot_freeze():
    text = GUIDE.read_text(encoding="utf-8")
    assert "Schema version:" in text and "`v2`" in text
    assert "20 emails" in text
    assert "FROZEN" in text
    assert "one-time refinement" in text.lower()


def test_guide_defines_all_schema_values():
    text = GUIDE.read_text(encoding="utf-8")
    for value in (
        "`is_spam`", "`needs_response`", "`priority`", "`category`",
        "`unsure`", "`note`", "`URGENT`", "`NEEDS_REPLY`",
        "`INFORMATIONAL`", "`JOB`", "`FINANCE`", "`PERSONAL`",
        "`PROMOTIONS`", "`UPDATES`", "`OTHER`",
    ):
        assert value in text


def test_guide_states_cross_field_consistency_rules():
    text = GUIDE.read_text(encoding="utf-8")
    assert "`yes` | `INFORMATIONAL` | **No**" in text
    assert "`no` | `NEEDS_REPLY` | **No**" in text
    assert "must never silently rewrite" in text


def test_guide_contains_all_required_confusable_pairs_and_synthetic_examples():
    text = GUIDE.read_text(encoding="utf-8")
    for heading in (
        "Spam vs PROMOTIONS",
        "`needs_response` vs `priority`",
        "UPDATES vs PROMOTIONS",
        "PERSONAL vs OTHER",
    ):
        assert heading in text
    assert "Invented email" in text
    assert "synthetic" in text.lower()


def test_guide_forbids_model_assisted_ground_truth_and_protects_privacy():
    text = GUIDE.read_text(encoding="utf-8").lower()
    assert "model predictions" in text
    assert "must not expose" in text
    assert "email identifiers" in text
