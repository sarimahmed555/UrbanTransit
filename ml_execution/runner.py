"""Explicit execution and artifact writing; no production run happens on import."""
from __future__ import annotations
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time

from .analytics import cluster_interpretation, service_analytics, silhouette
from .contracts import TASKS, PERIODS, NotReady, pending, load_features
from .metrics import classification, regression, risk_metrics, compare_baseline, select_model, classification_acceptance
from .models import PythonModel, SparkModel, candidates, SEED


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def metadata():
    versions = {}
    for name in ("numpy", "scikit-learn", "pyspark"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    files = list(Path(__file__).parent.glob("*.py"))
    files.append(Path(__file__).parents[1] / "python_pipeline" / "splits.py")
    return {"python": sys.version, "platform": platform.platform(), "libraries": versions,
            "source_sha256": {str(p.relative_to(Path(__file__).parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            "thread_environment": {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
            "command": sys.argv, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}


def _evaluate(model, x, y, m, kind, seed):
    predictions = model.predict(x)
    probabilities = None
    if m["task_name"] == "crowding_risk":
        probabilities = [p[1] for p in model.probabilities(x)]
        predictions = [int(p >= m["risk_probability_threshold"]) for p in probabilities]
    if kind == "classification":
        metrics = classification(y, predictions, m["class_labels"])
        if probabilities is not None:
            metrics.update(risk_metrics(y, probabilities, m["risk_probability_threshold"]))
    elif kind == "regression":
        metrics = regression(y, predictions)
    else:
        metrics = silhouette(model.scaled(x), predictions, seed=seed)
    return metrics, predictions, probabilities


def execute(manifest_path, output, *, engine="python", fixture=False, max_rows=100000, seed=SEED):
    start = time.perf_counter()
    try:
        m, partitions, hashes = load_features(manifest_path, fixture=fixture, max_rows=max_rows)
    except NotReady as exc:
        return pending(None, exc)
    if engine not in {"python", "spark"} or m["producer"] != engine:
        raise ValueError("Each engine must consume its own independently produced features")
    if engine == "spark" and TASKS[m["task_name"]] == "analytics":
        return pending(m["task_name"], "Use the existing spark_jobs.analytics entry point for independent Spark analytics; this service summary runner is Python")
    output = Path(output).resolve()
    package = Path(manifest_path).resolve().parent
    if output == package or output.is_relative_to(package) or package.is_relative_to(output):
        raise ValueError("Output must be separate from the certified input package")
    if "raw_data" in output.parts:
        raise ValueError("Cannot write ML artifacts into raw data")
    output.mkdir(parents=True, exist_ok=False)
    kind = TASKS[m["task_name"]]
    result = {"schema_version": "1.0", "status": "RUNNING", "task_name": m["task_name"],
              "evidence_status": "FIXTURE-TESTED" if fixture else "PENDING CERTIFIED RUNTIME EVIDENCE",
              "engine": engine, "dataset_version": m["dataset_version"], "feature_version": m["feature_version"],
              "feature_list": m["features"], "random_seed": seed, "periods": PERIODS,
              "sample_counts": {s: len(r) for s, r in partitions.items()}, "input_hashes": hashes,
              "task_contract": {k: v for k, v in m.items() if k != "partitions"},
              "metrics": {}, "baseline_metrics": {}, "model_comparison": [], "selected_model": None,
              "selection_rationale": None, "acceptance": None,
              "artifact_paths": {"result": str(output / "result.json")}, "reproducibility": metadata()}
    spark = None
    try:
        if kind == "analytics":
            result["metrics"] = {s: service_analytics(rows, m.get("analytics_policy", {})) for s, rows in partitions.items()}
            result["model_name"] = "configured_service_aggregations"
            result["model_type"] = "descriptive_analytics"
            result["parameters"] = m["analytics_policy"]
        else:
            if engine == "spark":
                try:
                    from pyspark.sql import SparkSession
                except ImportError as exc:
                    raise NotReady("PySpark is absent; no Spark model was executed") from exc
                spark = SparkSession.builder.appName("urbantransit-certified-ml").getOrCreate()
                result["reproducibility"]["spark_version"] = spark.version
                result["reproducibility"]["spark_conf"] = {
                    k: spark.sparkContext.getConf().get(k, None) for k in
                    ("spark.master", "spark.default.parallelism", "spark.sql.shuffle.partitions")}
            x = {s: [[r[f] for f in m["features"]] for r in rows] for s, rows in partitions.items()}
            y = {s: [r[m["target"]] for r in rows] if kind != "clustering" else None for s, rows in partitions.items()}
            if kind == "regression":
                result["baseline_metrics"] = {s: regression(y[s], [r[m["baseline_column"]] for r in rows]) for s, rows in partitions.items() if s != "train"}
                result["baseline"] = {"name": m["baseline_type"], "feature": m["baseline_column"],
                                      "policy": "Use certified historical value directly; no fallback for missing history"}
            fitted = {}
            for spec in candidates(kind, engine, seed):
                candidate_start = time.perf_counter()
                entry = {**spec, "status": "RUNNING", "validation_metrics": {}}
                try:
                    model = PythonModel(spec, kind) if engine == "python" else SparkModel(spec, kind, spark)
                    model.fit(x["train"], y["train"])
                    entry["effective_parameters"] = model.parameters()
                    entry["validation_metrics"], _, _ = _evaluate(model, x["validation"], y["validation"], m, kind, seed)
                    entry["status"] = "SUCCEEDED"
                    fitted[spec["model_name"]] = model
                except NotReady:
                    raise
                except Exception as exc:
                    entry["status"], entry["error"] = "FAILED", f"{type(exc).__name__}: {exc}"
                entry["runtime_sec"] = time.perf_counter() - candidate_start
                result["model_comparison"].append(entry)
            metric = {"classification": "macro_f1", "regression": "mae", "clustering": "silhouette"}[kind]
            try:
                selected, rationale = select_model(result["model_comparison"], metric, maximize=kind != "regression")
            except ValueError as exc:
                raise NotReady(str(exc)) from exc
            model = fitted[selected]
            result["selected_model"], result["selection_rationale"] = selected, rationale
            result["model_name"], result["model_type"] = selected, kind
            result["parameters"] = model.parameters()
            result["selection_metric"] = metric
            predictions_path = output / "predictions.jsonl"
            with predictions_path.open("x") as stream:
                for split in ("validation", "test"):
                    scores, predictions, probabilities = _evaluate(model, x[split], y[split], m, kind, seed)
                    result["metrics"][split] = scores
                    for i, (row, prediction) in enumerate(zip(partitions[split], predictions)):
                        record = {k: row[k] for k in ("case_id", "service_date", "target_start", "target_end", "feature_cutoff_at")}
                        record.update({"split": split, "task_name": m["task_name"], "engine": engine,
                                       "model_name": selected, "dataset_version": m["dataset_version"],
                                       "feature_version": m["feature_version"], "prediction": prediction,
                                       "actual": y[split][i] if y[split] is not None else None})
                        if probabilities is not None:
                            record["crowding_probability"] = probabilities[i]
                            record["high_risk"] = bool(prediction)
                        stream.write(json.dumps(record, allow_nan=False) + "\n")
            result["artifact_paths"]["predictions"] = str(predictions_path)
            if kind == "classification":
                result["acceptance"] = classification_acceptance(result["metrics"]["test"])
            elif kind == "regression":
                result["baseline_vs_selected"] = {s: compare_baseline(result["baseline_metrics"][s], result["metrics"][s]) for s in ("validation", "test")}
                result["acceptance"] = {"status": "MEASURED", "passed": result["baseline_vs_selected"]["test"]["improved"],
                                        "requirement": "Strict test MAE improvement over documented historical-lag baseline"}
            else:
                interpretation = {s: cluster_interpretation(rows, model.predict(x[s]), m["features"]) for s, rows in partitions.items()}
                interpretation_path = output / "cluster_interpretation.json"
                write_json(interpretation_path, interpretation)
                result["artifact_paths"]["cluster_interpretation"] = str(interpretation_path)
            model_path = output / ("selected_model.pkl" if engine == "python" else "selected_spark_model")
            model.save(model_path)
            result["artifact_paths"]["model"] = str(model_path)
        result["status"] = "FIXTURE_TESTED" if fixture else "SUCCEEDED"
        result["evidence_status"] = "FIXTURE-TESTED" if fixture else "CERTIFIED RUNTIME MEASURED; REVIEW ACCEPTANCE"
        if fixture and result["acceptance"] is not None:
            result["acceptance"]["scope"] = "synthetic fixture only; no production performance claim"
    except NotReady as exc:
        result["status"], result["reason"] = "NOT_READY", str(exc)
        result["acceptance"] = None
    except Exception as exc:
        result["status"], result["reason"] = "FAILED", f"{type(exc).__name__}: {exc}"
        result["acceptance"] = None
    finally:
        if spark is not None:
            spark.stop()
        result["runtime_sec"] = time.perf_counter() - start
        write_json(output / "result.json", result)
    return result
