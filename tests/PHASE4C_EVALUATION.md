# Phase 4C — Local Hybrid Evaluation

Run from the repository root after confirming `data/priority_engine_split.json` is the existing frozen manifest:

```powershell
python -m pytest -q tests/test_phase4c_evaluate_hybrid.py
python -m pytest -q
python -m scripts.phase4c_evaluate_hybrid --input data/email_labels_evaluation.csv --manifest data/priority_engine_split.json --output reports/phase4c_evaluation.json
```

The evaluator reads only human `true_category` and `true_needs_response` values for which the existing task-specific frozen manifest already has IDs. It refuses to create or repair a manifest. It never writes predictions into the input CSV. Reports contain aggregate metrics only.

The rules classifier's priority taxonomy (`LOW/NORMAL/HIGH/URGENT`) is not compatible with the Ollama adapter taxonomy (`URGENT/NEEDS_REPLY/INFORMATIONAL/SPAM`); priority and spam are therefore deliberately excluded rather than mapped by guesswork. Category taxonomy versioning must also be checked against the dataset's labeling guide before interpreting PERSONAL results.

Hybrid predictions are advisory only: when the router calls Ollama, the model suggestion is scored; otherwise the rules prediction is scored. No suggestion is persisted or treated as human truth. The same row is currently evaluated once per task; Ollama may be called once per task when both tasks are evaluated and the rules confidence is below threshold. This is an evaluation-only workload, not production integration.

Use validation/development data—not this frozen test report—to choose thresholds. A frozen test result is a final estimate and must not be used for repeated tuning. Ollama must be running locally; no remote inference is configured.
