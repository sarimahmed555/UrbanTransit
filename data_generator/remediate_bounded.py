"""Command line entry point for the bounded production remediation.

    python3 -m data_generator.remediate_bounded plan        raw_data/production-v1
    python3 -m data_generator.remediate_bounded materialize raw_data/production-v1 raw_data/production-v1.1
    python3 -m data_generator.remediate_bounded verify      raw_data/production-v1 raw_data/production-v1.1

``materialize`` re-derives the plan from the frozen parent, enforces the storage
inequality, hard-links every unchanged shard, atomically replaces only the
resolved closure, and publishes corrected deterministic metadata.  ``verify``
runs the lightweight streaming checks; neither stage builds a large validation
projection and neither stage ever writes inside ``production-v1``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .bounded_executor import RUNTIME_LOG, materialize
from .bounded_remediation import RESERVE_BYTES, build_plan
from .bounded_verification import verify


def _load_plan(path: Optional[Path], source: Path) -> Dict[str, Any]:
    if path is not None and path.exists():
        return json.loads(path.read_text())
    return build_plan(source, log=lambda message: print(message, flush=True))


def _free(path: Path) -> int:
    import shutil
    return shutil.disk_usage(path).free


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("source", type=Path)
    plan_parser.add_argument("--plan-out", type=Path, required=True)

    run_parser = sub.add_parser("materialize")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("target", type=Path)
    run_parser.add_argument("--plan", type=Path)
    run_parser.add_argument("--journal", type=Path,
                            help="append-only shard journal enabling crash-safe resume")
    run_parser.add_argument("--report", type=Path)

    check_parser = sub.add_parser("verify")
    check_parser.add_argument("source", type=Path)
    check_parser.add_argument("target", type=Path)
    check_parser.add_argument("--report", type=Path)

    args = parser.parse_args(argv)
    log = lambda message: print(message, flush=True)  # noqa: E731

    if args.command == "plan":
        plan = build_plan(args.source, log=log)
        Path(args.plan_out).write_text(json.dumps(plan, indent=2, sort_keys=True, default=str) + "\n")
        print(json.dumps({
            "movement": {k: v for k, v in plan["movement"].items() if k not in ("recipients", "donors")},
            "stops": {k: v for k, v in plan["stops"].items() if k != "mappings"},
            "vehicles": {k: v for k, v in plan["vehicles"].items() if k != "substitutions"},
            "changed_rows": {table: len(rows) for table, rows in plan["updates"].items()},
            "closure_files": len(plan["closure"]),
            "closure_bytes": plan["closure_bytes"],
        }, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "materialize":
        started = time.time()
        plan = _load_plan(args.plan, args.source)
        result = materialize(args.source, args.target, plan, log=log, journal=args.journal)
        result["elapsed_seconds"] = round(time.time() - started, 1)
        result["reserve_bytes"] = RESERVE_BYTES
        runtime = {
            "materialization_started_utc": result["materialization_started_utc"],
            "materialization_finished_utc": result["materialization_finished_utc"],
            "elapsed_seconds": result["elapsed_seconds"],
            "note": "Runtime evidence only. Deliberately excluded from the corrected generation "
                    "manifest so the published metadata stays byte-reproducible for a given "
                    "correction configuration.",
        }
        (args.target / RUNTIME_LOG).write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n")
        if args.report:
            Path(args.report).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "changed_files"},
                         indent=2, sort_keys=True, default=str))
        return 0

    report = verify(args.source, args.target, log=log)
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                      "checks": len(report["checks"])}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
