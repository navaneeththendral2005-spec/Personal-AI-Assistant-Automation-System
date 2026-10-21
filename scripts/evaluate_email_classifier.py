"""Evaluate FRIDAY's rule-based email categories against a labeled ML baseline.

Input CSV columns: sender,subject,snippet,labels,true_category.
Labels may be a pipe-separated string. No database or Gmail access is performed.
"""
from __future__ import annotations
import argparse, csv, json, sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

CATEGORIES = ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")


def metrics(y_true: list[str], y_pred: list[str], categories: tuple[str, ...]) -> dict:
    total = len(y_true)
    correct = sum(a == b for a, b in zip(y_true, y_pred))
    per_class = {}
    f1s = []
    for label in categories:
        tp = sum(a == label and b == label for a, b in zip(y_true, y_pred))
        fp = sum(a != label and b == label for a, b in zip(y_true, y_pred))
        fn = sum(a == label and b != label for a, b in zip(y_true, y_pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"support": sum(v == label for v in y_true), "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
        f1s.append(f1)
    matrix = {actual: {predicted: 0 for predicted in categories} for actual in categories}
    for actual, predicted in zip(y_true, y_pred):
        matrix[actual][predicted] += 1
    return {"samples": total, "accuracy": round(correct / total, 4) if total else 0.0,
            "macro_precision": round(sum(v["precision"] for v in per_class.values()) / len(categories), 4),
            "macro_recall": round(sum(v["recall"] for v in per_class.values()) / len(categories), 4),
            "macro_f1": round(sum(f1s) / len(categories), 4), "per_class": per_class,
            "confusion_matrix_rows_actual_columns_predicted": matrix}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Labeled CSV path')
    parser.add_argument('--output', default='reports/classifier_evaluation.json', help='JSON report path')
    parser.add_argument('--test-size', type=float, default=0.25, help='Held-out fraction, 0.1–0.4 (default 0.25)')
    args = parser.parse_args()
    if not 0.1 <= args.test_size <= 0.4:
        parser.error('--test-size must be between 0.1 and 0.4')
    path = Path(args.input)
    if not path.is_file():
        parser.error(f'Input file does not exist: {path}')
    rows = []
    with path.open(newline='', encoding='utf-8-sig') as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            category = (row.get('true_category') or '').strip().upper()
            if category not in CATEGORIES:
                raise SystemExit(f'Line {line_no}: true_category must be one of {", ".join(CATEGORIES)}')
            labels = row.get('labels') or ''
            rows.append({"sender": row.get('sender') or '', "subject": row.get('subject') or '',
                         "snippet": row.get('snippet') or '', "labels": [x.strip() for x in labels.split('|') if x.strip()],
                         "true_category": category})
    if len(rows) < 40:
        raise SystemExit(f'Need at least 40 labeled rows; found {len(rows)}. Use a larger, representative dataset.')
    counts = Counter(r['true_category'] for r in rows)
    if len(counts) < 2 or min(counts.values()) < 2:
        raise SystemExit('Need at least two categories and at least two examples in every represented category.')
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
    except ImportError:
        raise SystemExit('Missing optional ML packages. Run: pip install -r requirements-ml.txt')
    from backend.services.email_classifier import classify_email

    def features(row):
        return ' '.join([row['sender'], row['subject'], row['snippet'], ' '.join(row['labels'])])
    X = [features(r) for r in rows]
    y = [r['true_category'] for r in rows]
    indices = list(range(len(rows)))
    try:
        train_idx, test_idx = train_test_split(indices, test_size=args.test_size, random_state=42, stratify=y)
    except ValueError as exc:
        raise SystemExit(f'Unable to create stratified holdout: {exc}')
    y_test = [y[i] for i in test_idx]
    rules_pred = [classify_email(SimpleNamespace(**rows[i]))['category'] for i in test_idx]
    model = Pipeline([('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000)),
                      ('classifier', LogisticRegression(max_iter=2000, class_weight='balanced'))])
    model.fit([X[i] for i in train_idx], [y[i] for i in train_idx])
    ml_pred = model.predict([X[i] for i in test_idx]).tolist()
    report = {
        'dataset_rows': len(rows), 'class_distribution': dict(counts), 'random_state': 42,
        'test_fraction': args.test_size, 'test_rows': len(test_idx),
        'evaluation_note': 'Held-out results only; small datasets can have high variance. Do not compare on training rows.',
        'rule_based': metrics(y_test, rules_pred, CATEGORIES),
        'tfidf_logistic_regression': metrics(y_test, ml_pred, CATEGORIES),
    }
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('dataset_rows','test_rows','rule_based','tfidf_logistic_regression')}, indent=2))
    print(f'Full report written to {out}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
