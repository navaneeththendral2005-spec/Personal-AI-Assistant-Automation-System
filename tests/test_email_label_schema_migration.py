import csv
from pathlib import Path

import pytest

from scripts.migrate_email_label_schema import migrate_csv


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_migration_derives_spam_without_rewriting_priority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [
            {"email_id": "spam-1", "true_priority": "SPAM"},
            {"email_id": "urgent-1", "true_priority": "URGENT"},
            {"email_id": "reply-1", "true_priority": "NEEDS_REPLY"},
            {"email_id": "info-1", "true_priority": "INFORMATIONAL"},
            {"email_id": "unknown-1", "true_priority": ""},
        ],
    )

    counts = migrate_csv(path, backup=False)
    rows = {row["email_id"]: row for row in read_csv(path)}

    assert rows["spam-1"]["true_is_spam"] == "yes"
    assert rows["urgent-1"]["true_is_spam"] == "no"
    assert rows["reply-1"]["true_is_spam"] == "no"
    assert rows["info-1"]["true_is_spam"] == "no"
    assert rows["unknown-1"]["true_is_spam"] == ""

    assert rows["spam-1"]["true_priority"] == "SPAM"
    assert rows["urgent-1"]["true_priority"] == "URGENT"

    assert counts["derived_yes"] == 1
    assert counts["derived_no"] == 3
    assert counts["left_blank"] == 1


def test_migration_preserves_existing_spam_labels(
    tmp_path: Path,
) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [
            {
                "email_id": "existing-yes",
                "true_priority": "URGENT",
                "true_is_spam": "yes",
            },
            {
                "email_id": "existing-no",
                "true_priority": "SPAM",
                "true_is_spam": "no",
            },
        ],
    )

    counts = migrate_csv(path, backup=False)
    rows = {row["email_id"]: row for row in read_csv(path)}

    assert rows["existing-yes"]["true_is_spam"] == "yes"
    assert rows["existing-no"]["true_is_spam"] == "no"
    assert counts["preserved_existing"] == 2


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [
            {"email_id": "spam-1", "true_priority": "SPAM"},
            {"email_id": "urgent-1", "true_priority": "URGENT"},
        ],
    )

    migrate_csv(path, backup=False)
    first_result = read_csv(path)

    migrate_csv(path, backup=False)
    second_result = read_csv(path)

    assert second_result == first_result


def test_migration_rejects_invalid_existing_spam_label(
    tmp_path: Path,
) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [
            {
                "email_id": "invalid-1",
                "true_priority": "URGENT",
                "true_is_spam": "maybe",
            }
        ],
    )

    original = path.read_bytes()

    with pytest.raises(ValueError, match="Invalid true_is_spam"):
        migrate_csv(path, backup=False)

    assert path.read_bytes() == original


def test_migration_requires_email_id_and_priority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "labels.csv"

    write_csv(path, [{"subject": "Synthetic email"}])

    with pytest.raises(ValueError, match="email_id"):
        migrate_csv(path, backup=False)


def test_migration_creates_backup(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [{"email_id": "info-1", "true_priority": "INFORMATIONAL"}],
    )

    original = path.read_bytes()

    migrate_csv(path)

    backup = path.with_suffix(".csv.bak")

    assert backup.exists()
    assert backup.read_bytes() == original
    assert read_csv(path)[0]["true_is_spam"] == "no"


def test_migration_refuses_to_overwrite_existing_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "labels.csv"

    write_csv(
        path,
        [{"email_id": "info-1", "true_priority": "INFORMATIONAL"}],
    )

    backup = path.with_suffix(".csv.bak")
    backup.write_text("preserve this backup", encoding="utf-8")

    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        migrate_csv(path)

    assert path.read_bytes() == original
    assert backup.read_text(encoding="utf-8") == "preserve this backup"
