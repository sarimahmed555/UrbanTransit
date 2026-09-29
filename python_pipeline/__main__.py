"""Run the independent Python feature implementation on an adapter-certified package."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

from evidence_framework import EvidenceRecorder
from runtime_orchestration.certification import (
    CertificationAttestation,
    load_certification_adapter,
    require_certified_package,
)
from runtime_orchestration.output_safety import (
    create_output_directory,
    versioned_output_path,
)


PIPELINE_ID = "independent_python_features_v1"
FEATURE_TABLES = (
    "context_events",
    "delays",
    "passenger_counts",
    "passenger_journeys",
    "route_patterns",
    "route_stops",
    "routes",
    "schedule_stop_times",
    "schedules",
    "trip_stop_events",
    "trip_vehicle_assignments",
    "trips",
    "vehicles",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _feature_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    sources = (
        root / "feature_contracts.py",
        root / "python_pipeline" / "features.py",
        root / "python_pipeline" / "splits.py",
    )
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.relative_to(root).as_posix().encode())
        digest.update(bytes.fromhex(_sha256(source)))
    return digest.hexdigest()


def _read_table_files(raw_root: Path, table: str, *, max_rows: int):
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Pandas is required for Python feature engineering") from exc

    table_root = raw_root / table
    if not table_root.is_dir():
        raise FileNotFoundError(f"Required certified raw table directory is missing: {table_root}")
    files = sorted(
        path for path in table_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".jsonl"}
    )
    if not files:
        raise FileNotFoundError(f"Certified raw table has no CSV/JSONL files: {table_root}")

    frames, total_rows = [], 0
    hashes = {}
    for path in files:
        hashes[str(path.relative_to(table_root))] = _sha256(path)
        if path.suffix.lower() == ".csv":
            chunks = pd.read_csv(path, chunksize=max_rows + 1)
        else:
            chunks = pd.read_json(path, lines=True, chunksize=max_rows + 1)
        for chunk in chunks:
            total_rows += len(chunk)
            if total_rows > max_rows:
                raise ValueError(
                    f"Bounded CLI read exceeded --max-rows-per-table={max_rows}: {table}"
                )
            frames.append(chunk)
    return pd.concat(frames, ignore_index=True), total_rows, hashes


def _write_new_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _write_evidence(path: Path, recorder: EvidenceRecorder) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(recorder.bundle.payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def execute(
    *,
    input_root: str | Path,
    marker_path: str | Path,
    output_root: str | Path,
    run_id: str,
    adapter,
    severity_thresholds_sec: tuple[float, float, float, float],
    max_rows_per_table: int = 100_000,
) -> dict:
    if max_rows_per_table <= 0:
        raise ValueError("max_rows_per_table must be positive")
    start_time = _utc_now()
    started = time.perf_counter()
    attestation = None
    marker_hash = None
    status, failure_reason = "NOT_READY", None
    source_hashes, input_counts, output_counts, output_hashes = {}, {}, {}, {}
    certification_passed = False
    destination = versioned_output_path(
        output_root,
        stage="python_features",
        dataset_version="UNATTESTED",
        run_id=run_id,
    )
    destination = create_output_directory(destination, input_root=input_root)
    certificate = None
    try:
        certificate, marker_hash = require_certified_package(input_root, marker_path, adapter)
        attestation = certificate
        certification_passed = True
        destination.rmdir()
        destination = versioned_output_path(
            output_root,
            stage="python_features",
            dataset_version=certificate.dataset_version,
            run_id=run_id,
        )
        destination = create_output_directory(destination, input_root=input_root)

        tables = {}
        raw_root = Path(input_root).resolve() / "raw"
        for table in FEATURE_TABLES:
            frame, count, hashes = _read_table_files(
                raw_root, table, max_rows=max_rows_per_table
            )
            tables[table] = frame
            input_counts[table] = count
            source_hashes.update(
                {f"{table}/{name}": digest for name, digest in hashes.items()}
            )

        from feature_contracts import validate_severity_thresholds
        from python_pipeline.features import (
            build_feature_frames_from_tables,
            write_feature_frames,
        )

        thresholds = validate_severity_thresholds(severity_thresholds_sec)
        frames = build_feature_frames_from_tables(
            tables, severity_thresholds_sec=thresholds
        )
        output_counts = {name: int(len(frame)) for name, frame in frames.items()}
        write_feature_frames(frames, destination / "features")
        for artifact in sorted((destination / "features").rglob("*")):
            if artifact.is_file():
                output_hashes[str(artifact.relative_to(destination))] = _sha256(artifact)
        status = "SUCCEEDED"
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        failure_reason = f"{type(exc).__name__}: {exc}"
        status = "FAILED" if certification_passed else "NOT_READY"
    except Exception as exc:
        failure_reason = f"Unexpected {type(exc).__name__}: {exc}"
        status = "FAILED" if certification_passed else "NOT_READY"
    elapsed = time.perf_counter() - started
    end_time = _utc_now()
    feature_version = _feature_fingerprint()
    manifest = {
        "schema_version": "1.0",
        "status": status,
        "pipeline_id": PIPELINE_ID,
        "producer": "python",
        "dataset_version": attestation.dataset_version if attestation else None,
        "certification_reference": attestation.evidence_reference if attestation else None,
        "certification_marker_sha256": marker_hash,
        "feature_version": feature_version,
        "run_id": run_id,
        "started_at_utc": start_time,
        "ended_at_utc": end_time,
        "runtime_sec": elapsed,
        "input_artifact": str(Path(input_root).resolve()),
        "output_artifact": str(destination),
        "input_counts": input_counts,
        "output_counts": output_counts,
        "input_sha256": source_hashes,
        "output_sha256": output_hashes,
        "failure_reason": failure_reason,
        "chronological_splits": ["train", "validation", "test"],
        "independent_provenance": {
            "source": "python_pipeline.features",
            "spark_inputs_allowed": False,
            "feature_source_sha256": feature_version,
        },
    }
    _write_new_json(destination / "run_manifest.json", manifest)
    recorder = EvidenceRecorder(
        dataset_version=manifest["dataset_version"],
        command="python -m python_pipeline",
        input_artifact=manifest["input_artifact"],
        output_artifact=str(destination),
    )
    recorder.record(
        "python_pipeline",
        status=status,
        input_counts=input_counts,
        output_counts=output_counts,
        feature_outputs={k: f"{destination}/features/task={k}" for k in output_counts},
        runtime_sec=elapsed,
        input_sha256=source_hashes,
        output_sha256=output_hashes,
        started_at_utc=start_time,
        ended_at_utc=end_time,
        run_id=run_id,
        pipeline_id=PIPELINE_ID,
        failure_reason=failure_reason,
        notes=[
            "Certification parsing is delegated to the configured authoritative adapter.",
            "Only certified-package raw/ CSV and JSONL source tables are read.",
        ],
    )
    _write_evidence(destination / "evidence.json", recorder)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--certification-adapter", required=True, metavar="MODULE:OBJECT")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--severity-thresholds-sec", required=True, nargs=4, type=float,
        metavar=("ON_TIME", "MINOR", "MODERATE", "MAJOR"),
    )
    parser.add_argument("--max-rows-per-table", type=int, default=100_000)
    args = parser.parse_args(argv)
    run_id = args.run_id or str(uuid.uuid4())
    try:
        adapter = load_certification_adapter(args.certification_adapter)
        result = execute(
            input_root=args.input_root,
            marker_path=args.marker,
            output_root=args.output_root,
            run_id=run_id,
            adapter=adapter,
            severity_thresholds_sec=tuple(args.severity_thresholds_sec),
            max_rows_per_table=args.max_rows_per_table,
        )
    except Exception as exc:
        print(f"Python feature execution failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
