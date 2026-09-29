"""Measured metrics only, with explicit undefined cases; standard library."""
import math
from .contracts import finite


def _pairs(actual, predicted):
    if not actual or len(actual) != len(predicted):
        raise ValueError("Metrics require nonempty aligned observations")
    if not all(finite(x) for x in [*actual, *predicted]):
        raise ValueError("Metrics require finite values")


def classification(actual, predicted, labels):
    _pairs(actual, predicted)
    n = len(labels)
    if any(int(x) != x or not 0 <= x < n for x in [*actual, *predicted]):
        raise ValueError("Unknown class index")
    matrix = [[0] * n for _ in labels]
    for a, p in zip(actual, predicted):
        matrix[int(a)][int(p)] += 1
    scores = []
    for k, name in enumerate(labels):
        tp, support, calls = matrix[k][k], sum(matrix[k]), sum(row[k] for row in matrix)
        precision = tp / calls if calls else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores.append({"label": name, "support": support, "precision": precision, "recall": recall, "f1": f1})
    weighted = lambda key: sum(s[key] * s["support"] for s in scores) / len(actual)
    return {"accuracy": sum(matrix[i][i] for i in range(n)) / len(actual),
            "precision": weighted("precision"), "recall": weighted("recall"), "f1": weighted("f1"),
            "macro_f1": sum(s["f1"] for s in scores) / n, "averaging": "support_weighted; macro_f1 uses all declared classes",
            "confusion_matrix": matrix, "class_labels": labels, "per_class": scores,
            "zero_division_policy": "0 for undefined per-class precision/recall/F1", "sample_count": len(actual)}


def regression(actual, predicted):
    _pairs(actual, predicted)
    n, mean = len(actual), sum(actual) / len(actual)
    squared = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    variance = sum((a - mean) ** 2 for a in actual)
    relative = [abs((a - p) / a) for a, p in zip(actual, predicted) if a != 0]
    return {"mae": sum(abs(a - p) for a, p in zip(actual, predicted)) / n,
            "rmse": math.sqrt(squared / n), "r2": 1 - squared / variance if n > 1 and variance > 0 else None,
            "mape": 100 * sum(relative) / len(relative) if relative else None,
            "mape_nonzero_count": len(relative), "mape_excluded_zero_count": n - len(relative),
            "mape_policy": "percent; exclude zero actuals; null when all actuals are zero",
            "r2_policy": "null for fewer than two cases or constant actuals", "sample_count": n}


def risk_metrics(actual, probabilities, threshold):
    _pairs(actual, probabilities)
    if not finite(threshold) or not 0 < threshold < 1:
        raise ValueError("Probability threshold must be between zero and one")
    if any(y not in (0, 1) for y in actual) or any(not 0 <= p <= 1 for p in probabilities):
        raise ValueError("Invalid binary probability observations")
    return {"brier_score": sum((y-p)**2 for y, p in zip(actual, probabilities)) / len(actual),
            "probability_threshold": threshold,
            "flagged_count": sum(p >= threshold for p in probabilities)}


def compare_baseline(baseline, candidate, metric="mae"):
    b, c = baseline.get(metric), candidate.get(metric)
    if not finite(b) or not finite(c):
        return {"status": "NOT_READY", "metric": metric, "improved": None, "absolute_improvement": None, "relative_improvement": None}
    return {"status": "MEASURED", "metric": metric, "baseline": b, "selected": c,
            "improved": c < b, "absolute_improvement": b-c,
            "relative_improvement": (b-c)/abs(b) if b else None}


def select_model(comparison, metric, *, maximize=False):
    eligible = [c for c in comparison if c.get("status") == "SUCCEEDED" and finite(c.get("validation_metrics", {}).get(metric))]
    if len(eligible) < 3:
        raise ValueError("At least three measured candidates are required; failed metrics are never filled")
    selected = sorted(eligible, key=lambda c: ((-1 if maximize else 1) * c["validation_metrics"][metric], c["model_name"]))[0]
    return selected["model_name"], f"Best measured validation {metric}={selected['validation_metrics'][metric]}; alphabetical tie-break; test excluded from selection"


def classification_acceptance(metrics):
    a, f = metrics.get("accuracy"), metrics.get("macro_f1")
    if not finite(a) or not finite(f):
        return {"status": "NOT_READY", "passed": None}
    return {"status": "MEASURED", "passed": a >= .85 or f >= .80,
            "requirement": "test accuracy >= 0.85 OR macro F1 >= 0.80", "accuracy": a, "macro_f1": f}
