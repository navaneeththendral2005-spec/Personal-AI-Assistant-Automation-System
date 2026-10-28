import csv
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = PROJECT_ROOT / "data" / "relabel_completed_schema_fixed.csv"

LABEL_COLUMNS = (
    "true_is_spam",
    "true_priority",
    "true_needs_response",
    "true_category",
)

def main() -> None:
    with REVIEW_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    print(f"Review rows: {len(rows)}")

    for column in LABEL_COLUMNS:
        counts = Counter(
            row.get(column, "").strip() or "<BLANK>"
            for row in rows
        )
        print(f"\n{column}:")
        for value, count in sorted(counts.items()):
            print(f"  {value}: {count}")

    print("\nSpam/priority conflicts:")
    print("Row | Spam label | Priority label | Category")

    for row_number, row in enumerate(rows, start=2):
        spam = row.get("true_is_spam", "").strip().lower()
        priority = row.get("true_priority", "").strip().lower()

        if spam == "yes" or priority == "spam":
            print(
                f"{row_number} | "
                f"{row.get('true_is_spam', '').strip()} | "
                f"{row.get('true_priority', '').strip()} | "
                f"{row.get('true_category', '').strip()}"
            )

if __name__ == "__main__":
    main()
