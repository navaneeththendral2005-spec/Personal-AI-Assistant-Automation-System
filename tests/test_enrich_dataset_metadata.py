"""Metadata enrichment: read-only DB join into a NEW CSV."""
import csv
import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.services.email_features import extract_features
from scripts.enrich_dataset_metadata import ADDED_COLUMNS, EnrichmentError, enrich_dataset


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            EmailMessage(gmail_message_id="m1", thread_id="t1", sender="a@x.com", subject="s1", recipient="me@x.com",
                         message_date=datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)),
            EmailMessage(gmail_message_id="m2", thread_id="t1", sender="b@x.com", subject="s2", recipient="me@x.com, you@x.com",
                         message_date=datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc)),
            EmailMessage(gmail_message_id="m3", thread_id="t2", sender="c@x.com", subject="s3", recipient=None, message_date=None),
            EmailMessage(gmail_message_id="other", thread_id="t2", sender="d@x.com", subject="s4"),
        ])
        session.commit()
    yield factory
    engine.dispose()


def _write(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


FIELDS = ["email_id", "sender", "subject", "labels", "true_priority"]


def _rows():
    return [
        {"email_id": "m1", "sender": "a@x.com", "subject": "s1", "labels": "INBOX", "true_priority": "URGENT"},
        {"email_id": "m2", "sender": "b@x.com", "subject": "s2", "labels": "INBOX", "true_priority": ""},
        {"email_id": "m3", "sender": "c@x.com", "subject": "s3", "labels": "", "true_priority": "NEEDS_REPLY"},
        {"email_id": "ghost", "sender": "z@x.com", "subject": "gone", "labels": "", "true_priority": "URGENT"},
    ]


def test_enrichment_adds_metadata_without_touching_existing_content(tmp_path, session_factory):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    _write(source, _rows(), FIELDS)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    summary = enrich_dataset(source, target, session_factory)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest  # input untouched
    out = _read(target)
    assert [r["email_id"] for r in out] == ["m1", "m2", "m3", "ghost"]  # order and rows preserved
    for original, enriched in zip(_rows(), out):
        for column in FIELDS:
            assert enriched[column] == original[column]
    by_id = {r["email_id"]: r for r in out}
    assert by_id["m1"]["message_date"] == "2026-10-20T10:00:00+00:00"
    assert by_id["m1"]["thread_id"] == "t1" and by_id["m1"]["thread_message_count"] == "2"
    assert by_id["m2"]["recipient"] == "me@x.com, you@x.com"
    assert by_id["m3"]["message_date"] == "" and by_id["m3"]["recipient"] == ""
    assert by_id["m3"]["thread_message_count"] == "2"  # t2 has two stored messages
    assert all(by_id["ghost"][c] == "" for c in ADDED_COLUMNS)
    assert summary["rows"] == 4 and summary["matched_in_database"] == 3 and summary["unmatched"] == 1
    assert summary["columns_added"] == list(ADDED_COLUMNS)


def test_enriched_columns_activate_the_matching_features(tmp_path, session_factory):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    _write(source, _rows(), FIELDS)
    enrich_dataset(source, target, session_factory)
    row = {r["email_id"]: r for r in _read(target)}["m2"]
    features = extract_features(row)
    assert features["ts_missing"] == 0.0 and features["thread_has_replies"] == 1.0
    assert features["recipient_count"] == 2.0 and features["thread_info_available"] == 1.0


def test_existing_nonblank_values_are_never_overwritten(tmp_path, session_factory):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    fields = FIELDS + ["thread_id", "recipient"]
    rows = [{**r, "thread_id": "keep-me" if r["email_id"] == "m1" else "", "recipient": ""} for r in _rows()]
    _write(source, rows, fields)
    enrich_dataset(source, target, session_factory)
    by_id = {r["email_id"]: r for r in _read(target)}
    assert by_id["m1"]["thread_id"] == "keep-me"
    assert by_id["m2"]["thread_id"] == "t1" and by_id["m2"]["recipient"] == "me@x.com, you@x.com"


def test_output_is_not_overwritten_without_flag_and_never_equals_input(tmp_path, session_factory):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    _write(source, _rows(), FIELDS)
    enrich_dataset(source, target, session_factory)
    first = target.read_bytes()
    with pytest.raises(EnrichmentError):
        enrich_dataset(source, target, session_factory)
    assert target.read_bytes() == first
    enrich_dataset(source, target, session_factory, overwrite=True)
    with pytest.raises(EnrichmentError):
        enrich_dataset(source, source, session_factory, overwrite=True)


def test_database_is_not_modified(tmp_path, session_factory):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    _write(source, _rows(), FIELDS)
    with session_factory() as session:
        before = session.query(EmailMessage).count()
    enrich_dataset(source, target, session_factory)
    with session_factory() as session:
        assert session.query(EmailMessage).count() == before
        assert not session.dirty and not session.new


def test_bad_inputs_raise_clear_errors(tmp_path, session_factory):
    with pytest.raises(EnrichmentError):
        enrich_dataset(tmp_path / "missing.csv", tmp_path / "o.csv", session_factory)
    no_id = tmp_path / "noid.csv"
    _write(no_id, [{"a": "1"}], ["a"])
    with pytest.raises(EnrichmentError):
        enrich_dataset(no_id, tmp_path / "o.csv", session_factory)
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(EnrichmentError):
        enrich_dataset(empty, tmp_path / "o.csv", session_factory)
    dup = tmp_path / "dup.csv"
    dup.write_text("email_id,email_id\nm1,m1\n", encoding="utf-8")
    with pytest.raises(EnrichmentError):
        enrich_dataset(dup, tmp_path / "o.csv", session_factory)
