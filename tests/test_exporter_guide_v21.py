from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import load_workbook

from scripts.export_labeling_workbook import CATEGORIES, export_labeling_workbook


def _source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["email_id", "sender", "subject", "snippet", "body_text", "labels",
              "true_is_spam", "true_priority", "true_needs_response", "true_category"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"email_id": "synthetic-1", "sender": "person@example.test",
                         "subject": "Synthetic subject", "snippet": "Synthetic snippet",
                         "body_text": "Synthetic body", "labels": "INBOX",
                         "true_is_spam": "no", "true_priority": "INFORMATIONAL",
                         "true_needs_response": "no", "true_category": "OTHER"})


def test_exporter_uses_frozen_single_source_guide_and_v21_categories(tmp_path: Path) -> None:
    source = tmp_path / "email_labels.csv"
    _source(source)
    guide = tmp_path / "docs" / "EMAIL_LABELING_GUIDE.md"
    guide.parent.mkdir(parents=True)
    canonical = Path("docs/EMAIL_LABELING_GUIDE.md").read_text(encoding="utf-8")
    guide.write_text(canonical, encoding="utf-8")
    output = tmp_path / "labeling"
    export_labeling_workbook(source, mode="relabel", batch_name="guide-check",
                             output_dir=output, count=1, guide_path=guide)
    wb = load_workbook(output / "guide-check.xlsx", data_only=False)
    guide_text = "\n".join(str(c.value) for row in wb["Guide"].iter_rows() for c in row if c.value)
    assert "v2.1" in guide_text
    assert "URGENT" in guide_text
    assert "Legacy category mapping" in guide_text
    assert "PERSONAL" not in CATEGORIES
    assert wb["Progress"]["B6"].value == "v2.1"


def test_exporter_rejects_nonfrozen_or_wrong_version_guide(tmp_path: Path) -> None:
    source = tmp_path / "email_labels.csv"
    _source(source)
    guide = tmp_path / "guide.md"
    guide.write_text("**Status:** Draft\n**Guide version:** `v2.0-draft`\n", encoding="utf-8")
    try:
        export_labeling_workbook(source, mode="relabel", batch_name="bad-guide",
                                 output_dir=tmp_path / "labeling", count=1, guide_path=guide)
    except ValueError as exc:
        assert "frozen guide version v2.1" in str(exc)
    else:
        raise AssertionError("non-frozen guide was accepted")
