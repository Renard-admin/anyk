"""
Evaluation script for АНЫК pipeline.
Runs the full pipeline on the synthetic test dataset and computes metrics.

Usage (from anyk/ directory):
    venv/Scripts/python tests/evaluate.py
    venv/Scripts/python tests/evaluate.py --no-llm
"""

import argparse
import json
import os
import sys
from collections import Counter

# Make sure anyk/ is on sys.path
ANYK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ANYK_DIR not in sys.path:
    sys.path.insert(0, ANYK_DIR)

LABELS_PATH = os.path.join(ANYK_DIR, "data", "test_dataset", "labels.json")
IMAGES_DIR  = os.path.join(ANYK_DIR, "data", "test_dataset", "images")


def run_pipeline(image_path: str, use_llm: bool) -> dict:
    from analyzer.preprocessor     import preprocess
    from analyzer.ocr_engine        import extract_text
    from analyzer.structural_checker import check_structure
    from analyzer.risk_scorer       import calculate_risk as compute_risk
    from analyzer.report_builder    import build_report

    preprocessed = preprocess(image_path)
    ocr_result   = extract_text(preprocessed)

    structural_signals = check_structure(ocr_result)

    llm_signals = []
    if use_llm:
        try:
            from analyzer.llm_analyzer import analyze_consistency
            llm_signals = analyze_consistency(ocr_result, context="")
        except Exception:
            llm_signals = []

    risk   = compute_risk(structural_signals + llm_signals)
    report = build_report(ocr_result, structural_signals, llm_signals, risk,
                          context="", image_filename=os.path.basename(image_path))

    # clean up preprocessed temp file
    try:
        if preprocessed != image_path and os.path.exists(preprocessed):
            os.remove(preprocessed)
    except Exception:
        pass

    return report


def risk_to_predicted_label(risk_level: str) -> str:
    """Map risk_level → predicted label for metrics."""
    if risk_level == "clear":
        return "consistent"
    if risk_level in ("medium", "high"):
        return "inconsistent"
    return "uncertain"


def run_evaluation(use_llm: bool = True) -> dict:
    with open(LABELS_PATH, encoding="utf-8") as f:
        labels = json.load(f)

    TP = FP = TN = FN = 0
    uncertain_skipped = 0
    signal_counts = Counter()
    per_image_results = []

    total = len(labels)
    for i, entry in enumerate(labels):
        fname    = entry["filename"]
        expected = entry["label"]          # consistent / inconsistent / neutral
        img_path = os.path.join(IMAGES_DIR, fname)

        print(f"  [{i+1:02d}/{total}] {fname} (expected={expected})", end=" ", flush=True)

        try:
            report = run_pipeline(img_path, use_llm=use_llm)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        predicted = risk_to_predicted_label(report["risk_level"])
        print(f"→ predicted={predicted} (risk={report['risk_level']})")

        # Count fired signals
        for sig in report.get("signals", []):
            signal_counts[sig["signal_id"]] += 1

        # Skip neutral + uncertain in binary metrics
        if expected == "neutral" or predicted == "uncertain":
            uncertain_skipped += 1
            per_image_results.append({
                "filename": fname, "expected": expected,
                "predicted": predicted, "skipped": True,
            })
            continue

        per_image_results.append({
            "filename": fname, "expected": expected,
            "predicted": predicted, "skipped": False,
        })

        if predicted == "inconsistent" and expected == "inconsistent":
            TP += 1
        elif predicted == "inconsistent" and expected == "consistent":
            FP += 1
        elif predicted == "consistent" and expected == "inconsistent":
            FN += 1
        elif predicted == "consistent" and expected == "consistent":
            TN += 1

    # Compute metrics safely
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    fpr       = FP / (FP + TN) if (FP + TN) > 0 else 0.0
    total_classified = TP + FP + TN + FN
    accuracy  = (TP + TN) / total_classified if total_classified > 0 else 0.0

    return {
        "precision":          round(precision, 4),
        "recall":             round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "accuracy":           round(accuracy, 4),
        "total_samples":      total,
        "uncertain_skipped":  uncertain_skipped,
        "llm_used":           use_llm,
        "confusion_matrix":   {"TP": TP, "FP": FP, "TN": TN, "FN": FN},
        "per_signal_stats":   dict(signal_counts),
        "per_image_results":  per_image_results,
    }


def print_report(m: dict) -> None:
    llm_note = "" if m["llm_used"] else " (без LLM)"
    cm = m["confusion_matrix"]
    total = m["total_samples"]

    consistent_count   = sum(1 for r in m["per_image_results"] if r["expected"] == "consistent")
    inconsistent_count = sum(1 for r in m["per_image_results"] if r["expected"] == "inconsistent")
    neutral_count      = sum(1 for r in m["per_image_results"] if r["expected"] == "neutral")

    print()
    print("=" * 52)
    print(f"  АНЫҚ — Метрики оценки{llm_note}")
    print("=" * 52)
    print(f"Датасет: {total} изображений "
          f"({consistent_count} consistent / {inconsistent_count} inconsistent / {neutral_count} neutral)")
    print(f"Пропущено (uncertain/neutral): {m['uncertain_skipped']}")
    print()
    print(f"Precision:           {m['precision']:.2f}  "
          f"(доля верных среди помеченных)")
    print(f"Recall:              {m['recall']:.2f}  "
          f"(доля найденных из всех мошеннических)")
    print(f"False Positive Rate: {m['false_positive_rate']:.2f}  "
          f"(доля ложных срабатываний)")
    print(f"Accuracy:            {m['accuracy']:.2f}")
    print()
    print("Матрица ошибок:")
    print(f"             {'Предсказано: +':<20} {'Предсказано: -'}")
    print(f"Факт: +      TP={cm['TP']:<16}  FN={cm['FN']}")
    print(f"Факт: -      FP={cm['FP']:<16}  TN={cm['TN']}")
    print()

    if m["per_signal_stats"]:
        print("Топ сигналов:")
        for sig, cnt in sorted(m["per_signal_stats"].items(),
                                key=lambda x: -x[1]):
            print(f"  {sig:<35} {cnt} раз")
    else:
        print("Сигналы: не зафиксировано (structural checker не сработал)")
    print("=" * 52)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="АНЫК evaluation")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM step (structural checks only)")
    args = parser.parse_args()

    use_llm = not args.no_llm

    # Auto-detect: if no API key, disable LLM silently
    if use_llm and not os.environ.get("GEMINI_API_KEY"):
        print("INFO: GEMINI_API_KEY not set — running without LLM.")
        use_llm = False

    print(f"Running evaluation on {IMAGES_DIR}")
    print(f"LLM: {'enabled' if use_llm else 'disabled'}\n")

    metrics = run_evaluation(use_llm=use_llm)
    print_report(metrics)

    # Save results
    results_path = os.path.join(ANYK_DIR, "data", "test_dataset", "eval_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        out = {k: v for k, v in metrics.items() if k != "per_image_results"}
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Results saved → {results_path}")
