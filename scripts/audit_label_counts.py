import csv
from pathlib import Path
from collections import Counter

files = {
    "email_labels.csv": "true_priority",
    "email_labels_checkpoint_81.csv": "true_priority",
    "email_labels_backup.csv": "true_priority",
    "label_disagreement_adjudication_final.csv": "final_true_priority",
}

for filename, column in files.items():
    path = Path("data") / filename

    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    labeled = [r for r in rows if (r.get(column) or "").strip()]

    print(f"\n{filename}")
    print(f"  Total rows: {len(rows)}")
    print(f"  Non-empty {column}: {len(labeled)}")

    if rows and "label_source" in rows[0]:
        print("  Label sources:", dict(Counter(
            (r.get("label_source") or "<empty>").strip()
            for r in rows
        )))
