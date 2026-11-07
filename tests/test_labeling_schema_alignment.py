from scripts.label_emails import CATEGORIES, LEGACY_CATEGORIES, _fully_labeled, _legacy_fully_labeled


def test_v21_categories_match_frozen_guide():
    assert CATEGORIES == ("JOB", "FINANCE", "PROMOTIONS", "UPDATES", "OTHER")
    assert "PERSONAL" not in CATEGORIES
    assert "PERSONAL" in LEGACY_CATEGORIES


def test_legacy_personal_is_not_v21_ground_truth_but_remains_legacy_compatible():
    row = {
        "true_is_spam": "no", "true_priority": "INFORMATIONAL",
        "true_needs_response": "no", "true_category": "PERSONAL",
    }
    assert not _fully_labeled(row)
    assert _legacy_fully_labeled({
        "true_priority": "INFORMATIONAL", "true_needs_response": "no",
        "true_category": "PERSONAL",
    })
