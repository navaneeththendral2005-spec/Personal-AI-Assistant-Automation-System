"""Feature extraction, normalisation, and missing/inconsistent-metadata edge cases."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.email_features import (
    FEATURE_NAMES,
    FeatureContext,
    attachment_metadata_from_payload,
    clean_text,
    context_for_rows,
    extract_features,
    feature_matrix,
    feature_schema,
    feature_vector,
    legacy_model_text,
    normalize_gmail_labels,
    normalized_model_text,
    parse_sender,
    parse_timestamp,
    schema_fingerprint,
)
from scripts.evaluate_priority_engine import _features as existing_text_features


# --------------------------------------------------------------------------- #
# Text normalisation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value", [None, "", "   ", "\n\t ", float("nan")])
def test_clean_text_treats_missing_and_blank_as_empty(value):
    assert clean_text(value) == ""


def test_clean_text_handles_non_strings_and_bytes():
    assert clean_text(12345) == "12345"
    assert clean_text(b"hello \xff world") == "hello \ufffd world"
    assert clean_text(["a", "b"]) == "['a', 'b']"


def test_clean_text_unescapes_html_and_double_escaping():
    assert clean_text("Here&#39;s what") == "Here's what"
    assert clean_text("Tom &amp;amp; Jerry") == "Tom & Jerry"


def test_clean_text_removes_invisible_characters_and_collapses_whitespace():
    assert clean_text("a\u200bb\u00adc\ufeff  d\n\n e") == "abc d e"
    assert clean_text("\x00\x07bell") == "bell"


def test_clean_text_applies_nfkc_and_truncates():
    assert clean_text("ＦＵＬＬ　ｗｉｄｔｈ") == "FULL width"
    assert clean_text("abcdef", max_chars=3) == "abc"


# --------------------------------------------------------------------------- #
# Sender parsing
# --------------------------------------------------------------------------- #

def test_parse_sender_standard_header():
    sender = parse_sender('Acme Billing <Billing@Mail.Acme.co.in>')
    assert sender.is_valid
    assert sender.display_name == "Acme Billing"
    assert sender.address == "billing@mail.acme.co.in"
    assert sender.local_part == "billing"
    assert sender.domain == "mail.acme.co.in"
    assert sender.registered_domain == "acme.co.in"


def test_parse_sender_bare_address_quoted_name_and_multiple_addresses():
    assert parse_sender("a@b.com").address == "a@b.com"
    quoted = parse_sender('"Doe, Jane" <jane@x.org>')
    assert quoted.display_name == "Doe, Jane" and quoted.address == "jane@x.org"
    multi = parse_sender("A <a@x.com>, B <b@y.com>")
    assert multi.address == "a@x.com" and multi.address_count == 2


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "<>", "undisclosed-recipients:;", "no at sign here", "a@b", "@nodomain.com", "user@"],
)
def test_parse_sender_malformed_values_never_raise_and_are_invalid(value):
    assert parse_sender(value).is_valid is False


def test_parse_sender_recovers_address_embedded_in_text():
    sender = parse_sender("Support Team support@vendor.io")
    assert sender.is_valid and sender.address == "support@vendor.io"


def test_parse_sender_handles_unicode_display_name():
    sender = parse_sender("Zoë Ñandú <zoe@例え.com>")
    # IDN domain is not a valid ASCII address; must be flagged invalid, not crash.
    assert sender.is_valid is False


# --------------------------------------------------------------------------- #
# Gmail label normalisation
# --------------------------------------------------------------------------- #

def test_normalize_labels_handles_every_encoding():
    expected = frozenset({"INBOX", "CATEGORY_PROMOTIONS"})
    assert normalize_gmail_labels("INBOX|CATEGORY_PROMOTIONS") == expected
    assert normalize_gmail_labels("inbox, category promotions") == expected
    assert normalize_gmail_labels(["Inbox", "Category-Promotions"]) == expected
    assert normalize_gmail_labels('["INBOX", "CATEGORY_PROMOTIONS"]') == expected
    assert normalize_gmail_labels("INBOX;CATEGORY_PROMOTIONS;INBOX") == expected


@pytest.mark.parametrize("value", [None, "", "   ", "[]", " ,|; ", "none", "NaN", [], ["", None]])
def test_normalize_labels_missing_values_give_empty_set(value):
    assert normalize_gmail_labels(value) == frozenset()


def test_normalize_labels_survives_broken_json():
    assert normalize_gmail_labels('["INBOX", "SPAM"') == frozenset({"INBOX", "SPAM"})


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #

def test_parse_timestamp_accepts_common_formats():
    expected = datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc)
    assert parse_timestamp("2026-10-20T04:30:00Z") == expected
    assert parse_timestamp("2026-10-20T10:00:00+05:30") == expected
    assert parse_timestamp("Tue, 20 Oct 2026 10:00:00 +0530") == expected
    assert parse_timestamp("2026-10-20 04:30:00") == expected  # naive assumed UTC
    assert parse_timestamp(expected) == expected
    assert parse_timestamp(expected.timestamp()) == expected
    assert parse_timestamp(int(expected.timestamp() * 1000)) == expected
    assert parse_timestamp(str(int(expected.timestamp()))) == expected


@pytest.mark.parametrize(
    "value", [None, "", "   ", "garbage", "2026-13-45", True, float("nan"), float("inf"), "1900-01-01", "3000-01-01", {}]
)
def test_parse_timestamp_rejects_blank_malformed_or_implausible(value):
    assert parse_timestamp(value) is None


# --------------------------------------------------------------------------- #
# Extraction: robustness
# --------------------------------------------------------------------------- #

HOSTILE_ROWS = [
    {},
    {"sender": None, "subject": None, "snippet": None, "body_text": None, "labels": None},
    {"sender": "", "subject": "   ", "snippet": "\n", "body_text": "\t"},
    {"sender": 123, "subject": 4.5, "snippet": ["x"], "body_text": {"a": 1}, "labels": 7},
    {"sender": "<>", "message_date": "not a date", "thread_message_count": "many", "attachment_count": "??"},
    {"body_text": "x" * 250_000, "subject": "Re: Re: RE: FWD: " + "!" * 500},
    {"recipient": ", , ,,", "references": [None, "", 5], "attachment_names": [None, 3]},
    {"labels": "[[[", "message_date": "9" * 40, "has_attachment": "maybe"},
]


@pytest.mark.parametrize("row", HOSTILE_ROWS)
def test_extract_features_never_raises_and_is_finite(row):
    features = extract_features(row)
    assert tuple(features) == FEATURE_NAMES
    assert all(isinstance(v, float) and math.isfinite(v) for v in features.values())


def test_feature_names_are_unique_and_match_vector_length():
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)
    assert len(feature_vector({})) == len(FEATURE_NAMES)
    assert feature_matrix([{}, {}]) == [feature_vector({}), feature_vector({})]


def test_missing_flags_distinguish_unknown_from_empty():
    empty = extract_features({})
    assert empty["sender_missing"] == 1.0 and empty["subject_missing"] == 1.0
    assert empty["body_missing"] == 1.0 and empty["snippet_missing"] == 1.0
    assert empty["ts_missing"] == 1.0 and empty["recipient_missing"] == 1.0
    assert empty["attachment_info_available"] == 0.0 and empty["thread_info_available"] == 0.0

    present = extract_features({"sender": "a@b.com", "subject": "Hi", "snippet": "x", "body_text": "y"})
    assert present["sender_missing"] == 0.0 and present["body_missing"] == 0.0


def test_malformed_sender_and_timestamp_are_flagged_not_crashing():
    features = extract_features({"sender": "no at sign", "message_date": "garbage"})
    assert features["sender_invalid"] == 1.0 and features["sender_missing"] == 0.0
    assert features["ts_invalid"] == 1.0 and features["ts_missing"] == 0.0


def test_unusually_long_body_is_flagged_and_bounded():
    long_row = {"body_text": "word " * 50_000}
    features = extract_features(long_row)
    assert features["body_truncated"] == 1.0
    assert len(normalized_model_text(long_row)) < 21_000
    assert extract_features({"body_text": "short"})["body_truncated"] == 0.0


def test_extraction_is_deterministic():
    row = {"sender": "A <a@b.com>", "subject": "Re: hi?", "body_text": "please confirm", "message_date": "2026-10-20T10:00:00Z"}
    assert extract_features(row) == extract_features(dict(row))


# --------------------------------------------------------------------------- #
# Extraction: meaning of individual features
# --------------------------------------------------------------------------- #

def test_sender_features():
    bulk = extract_features({"sender": "Shop <no-reply@mail.shop.com>"})
    assert bulk["sender_is_noreply"] == 1.0 and bulk["sender_bulk_domain"] == 1.0
    human = extract_features({"sender": "Ravi Kumar <ravi.kumar@gmail.com>"})
    assert human["sender_is_freemail"] == 1.0 and human["sender_is_noreply"] == 0.0
    newsletter = extract_features({"sender": "news@brand.com"})
    assert newsletter["sender_bulk_local"] == 1.0


def test_subject_and_body_features():
    features = extract_features({
        "subject": "Re: Re: Invoice due tomorrow?",
        "snippet": "Hi Navaneeth, please confirm the payment",
        "body_text": "Hi Navaneeth, can you confirm? Pay by EOD. Details at https://x.io/pay. Unsubscribe here.",
    })
    assert features["subject_reply_depth"] == 2.0 and features["is_reply"] == 1.0
    assert features["subject_has_question"] == 1.0
    assert features["hits_deadline"] >= 1.0 and features["hits_finance"] >= 1.0
    assert features["hits_request"] >= 1.0 and features["body_url_count"] == 1.0
    assert features["body_has_unsubscribe"] == 1.0 and features["body_personal_greeting"] == 1.0


def test_quoted_reply_detection():
    body = "Thanks!\n\nOn Mon, 5 Oct 2026 at 10:00, Ravi <ravi@x.com> wrote:\n> earlier text"
    assert extract_features({"body_text": body})["body_has_quoted_reply"] == 1.0


def test_recipient_features():
    many = ", ".join(f"u{i}@x.com" for i in range(6))
    assert extract_features({"recipient": many})["recipient_is_bulk"] == 1.0
    assert extract_features({"recipient": "me@x.com"})["recipient_count"] == 1.0


def test_timestamp_and_recency_use_reference_time_not_the_clock():
    stamp = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    reference = stamp + timedelta(days=10)
    row = {"message_date": stamp.isoformat()}
    with_ref = extract_features(row, FeatureContext(reference_time=reference))
    assert with_ref["ts_age_days_log"] == pytest.approx(math.log1p(10.0))
    assert with_ref["ts_is_weekend"] == 0.0  # Tuesday
    no_ref = extract_features(row)
    assert no_ref["ts_age_days_log"] == 0.0 and no_ref["ts_missing"] == 0.0
    assert extract_features(row, FeatureContext(reference_time=reference)) == with_ref


def test_context_for_rows_uses_latest_timestamp_only():
    rows = [
        {"message_date": "2026-10-01T00:00:00Z"},
        {"message_date": "2026-10-09T00:00:00Z"},
        {"message_date": "garbage"},
        {"true_priority": "URGENT"},
    ]
    assert context_for_rows(rows).reference_time == datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert context_for_rows([{}]).reference_time is None


def test_thread_and_reply_features():
    features = extract_features({
        "thread_id": "t1", "thread_message_count": "4", "in_reply_to": "<abc@x>",
        "references": "<a@x> <b@x> <c@x>",
    })
    assert features["thread_info_available"] == 1.0 and features["thread_has_replies"] == 1.0
    assert features["thread_message_count_log"] == pytest.approx(math.log1p(4.0))
    assert features["in_reply_to_present"] == 1.0 and features["references_count"] == 3.0
    assert features["is_reply"] == 1.0


def test_attachment_features_from_columns():
    features = extract_features({"attachment_names": "cv.pdf|invite.ics|photo.png", "has_attachment": "true"})
    assert features["attachment_info_available"] == 1.0 and features["has_attachment"] == 1.0
    assert features["attachment_count_log"] == pytest.approx(math.log1p(3.0))
    assert features["attachment_has_pdf"] == features["attachment_has_calendar"] == features["attachment_has_image"] == 1.0
    assert extract_features({"has_attachment": "no"})["has_attachment"] == 0.0
    assert extract_features({"attachment_count": "oops"})["attachment_info_available"] == 0.0


def test_attachment_metadata_from_gmail_payload():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "filename": ""},
            {"mimeType": "application/pdf", "filename": "offer.pdf"},
            {"mimeType": "multipart/related", "parts": [{"mimeType": "image/png", "filename": "logo.png"}]},
        ],
    }
    meta = attachment_metadata_from_payload(payload)
    assert meta == {
        "has_attachment": True,
        "attachment_count": 2,
        "attachment_names": "offer.pdf|logo.png",
        "attachment_mime_types": "application/pdf|image/png",
    }
    assert extract_features(meta)["attachment_has_pdf"] == 1.0
    for bad in (None, {}, [], "text", {"parts": "oops"}, {"parts": [None, 5]}):
        assert attachment_metadata_from_payload(bad)["attachment_count"] == 0


# --------------------------------------------------------------------------- #
# Model text and schema
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "row",
    [
        {"sender": "A <a@b.com>", "subject": "S", "snippet": "N", "body_text": "B"},
        {"sender": None, "subject": "", "snippet": None, "body_text": "only body"},
        {},
    ],
)
def test_legacy_model_text_matches_existing_baseline_exactly(row):
    assert legacy_model_text(row) == existing_text_features(row)


def test_normalized_text_adds_domain_token_and_normalises():
    text = normalized_model_text({
        "sender": "Shop <deals@mail.shop.co.uk>", "subject": "Big&nbsp;Sale", "snippet": "it&#39;s on", "body_text": "x\u200by",
    })
    assert "senderdomain_shop_co_uk" in text and "it's on" in text and "xy" in text


def test_schema_fingerprint_is_stable_and_describes_features():
    assert schema_fingerprint() == schema_fingerprint()
    schema = feature_schema()
    assert schema["names"] == list(FEATURE_NAMES) and schema["version"]
