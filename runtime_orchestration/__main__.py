"""python -m runtime_orchestration preflight|plan|run"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .certification import load_certification_adapter, require_certified_package
from .plan import Stage, build_plan
from .preflight import Status, run_preflight
from .runner import StageRunner


def _save_new(path, payload):
    destination = Path(path).resolve()
    _assert_safe_local_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _assert_safe_local_path(path):
    resolved = Path(path).resolve()
    if "raw_data" in resolved.parts or "acceptance_harness" in resolved.parts:
        raise ValueError("Orchestration files may not use protected paths")
    if "reports" in resolved.parts and any(
        part.startswith("production") for part in resolved.parts
    ):
        raise ValueError("Production report paths are protected")


def _read_json(path):
    source = Path(path).resolve()
    _assert_safe_local_path(source)
    return json.loads(source.read_text(encoding="utf-8"))


def _preflight_args(parser):
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--dataset-root")
    parser.add_argument("--marker")
    parser.add_argument("--certification-adapter", metavar="MODULE:OBJECT")
    parser.add_argument("--disk-path")
    parser.add_argument("--minimum-free-bytes", type=int)


def _report(args):
    return run_preflight(
        project_root=args.project_root,
        dataset_root=args.dataset_root,
        marker_path=args.marker,
        certification_adapter=args.certification_adapter,
        disk_path=args.disk_path,
        minimum_free_bytes=args.minimum_free_bytes,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    preflight_parser = subparsers.add_parser("preflight")
    _preflight_args(preflight_parser)
    preflight_parser.add_argument("--output")

    plan_parser = subparsers.add_parser("plan")
    _preflight_args(plan_parser)
    plan_parser.add_argument("--hdfs-root")
    plan_parser.add_argument("--dataset-version")
    plan_parser.add_argument("--severity-thresholds-sec", nargs=4, type=float)
    plan_parser.add_argument("--spark-manifest")
    plan_parser.add_argument("--spark-ml-output")
    plan_parser.add_argument("--python-manifest")
    plan_parser.add_argument("--python-ml-output")
    plan_parser.add_argument("--comparison-manifest")
    plan_parser.add_argument("--recommendation-manifest")
    plan_parser.add_argument("--recommendation-request")
    plan_parser.add_argument("--recommendation-output")
    plan_parser.add_argument("--output", required=True)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--python-result", required=True)
    compare_parser.add_argument("--spark-result", required=True)
    compare_parser.add_argument("--dataset-version", required=True)
    compare_parser.add_argument("--output-root", required=True)
    compare_parser.add_argument("--run-id")
    compare_parser.add_argument(
        "--comparison-policy",
        choices=("strict", "production-v1.1-delay-source-event-utc5"),
        default="strict",
    )

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--plan", required=True)
    run_parser.add_argument("--dataset-root", required=True)
    run_parser.add_argument("--marker", required=True)
    run_parser.add_argument("--certification-adapter", metavar="MODULE:OBJECT")
    run_parser.add_argument("--evidence-root", required=True)
    run_parser.add_argument("--project-root", default=str(Path.cwd()))
    run_parser.add_argument("--minimum-free-bytes", type=int)
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly execute READY allowlisted stages; default only dry-runs",
    )

    args = parser.parse_args(argv)
    if args.operation == "preflight":
        report = _report(args)
        output = report.to_dict()
        if args.output:
            _save_new(args.output, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if report.ready else 2
    if args.operation == "plan":
        report = _report(args)
        stages = build_plan(
            project_root=args.project_root,
            preflight=report,
            certified_root=args.dataset_root,
            marker_path=args.marker,
            dataset_version=args.dataset_version,
            hdfs_root=args.hdfs_root,
            spark_severity_thresholds=(
                tuple(args.severity_thresholds_sec)
                if args.severity_thresholds_sec
                else None
            ),
            spark_manifest=args.spark_manifest,
            spark_ml_output=args.spark_ml_output,
            python_manifest=args.python_manifest,
            python_ml_output=args.python_ml_output,
            comparison_manifest=args.comparison_manifest,
            recommendation_manifest=args.recommendation_manifest,
            recommendation_request=args.recommendation_request,
            recommendation_output=args.recommendation_output,
        )
        output = {
            "status": "READY" if report.ready and all(s.status == Status.READY for s in stages) else "BLOCKED",
            "preflight": report.to_dict(),
            "stages": [stage.to_dict() for stage in stages],
        }
        _save_new(args.output, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if output["status"] == "READY" else 2
    if args.operation == "compare":
        from .compare import compare_artifacts

        result = compare_artifacts(
            python_result_path=args.python_result,
            spark_result_path=args.spark_result,
            dataset_version=args.dataset_version,
            output_root=args.output_root,
            run_id=args.run_id,
            comparison_policy=args.comparison_policy,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "SUCCEEDED" else 2

    try:
        plan_document = _read_json(args.plan)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        parser.error("Execution plan must be a readable, safe JSON file")
    try:
        adapter = (
            load_certification_adapter(args.certification_adapter)
            if args.certification_adapter
            else None
        )
        certificate = (
            {
                "status": "CERTIFIED",
                "dataset_version": require_certified_package(
                    args.dataset_root, args.marker, adapter
                )[0].dataset_version,
            }
            if adapter
            else {}
        )
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
        parser.error(f"Certification adapter rejected package: {type(exc).__name__}: {exc}")
    if not isinstance(plan_document, dict) or not isinstance(plan_document.get("stages"), list):
        parser.error("Execution plan must contain an ordered stages array")
    stages = []
    try:
        for value in plan_document["stages"]:
            stages.append(
                Stage(
                    index=value["index"],
                    name=value["name"],
                    status=Status(value["status"]),
                    entrypoint=value.get("entrypoint"),
                    argv=tuple(value["argv"]) if value.get("argv") is not None else None,
                    reason=value["reason"],
                    inputs=tuple(value.get("inputs", ())),
                    outputs=tuple(value.get("outputs", ())),
                )
            )
    except (KeyError, TypeError, ValueError):
        parser.error("Execution plan stage contract is invalid")
    preflight = run_preflight(
        project_root=args.project_root,
        dataset_root=args.dataset_root,
        marker_path=args.marker,
        minimum_free_bytes=args.minimum_free_bytes,
        certification_adapter=adapter,
    )
    result = StageRunner(project_root=args.project_root).run(
        tuple(stages),
        certificate=certificate,
        preflight=preflight,
        dataset_root=args.dataset_root,
        evidence_root=args.evidence_root,
        dry_run=not args.execute,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"DRY_RUN", "SUCCEEDED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
