"""Run smoke validation and emit a machine-readable report."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from . import production_gate
from .validation.common import ValidationReport
from .validation import leakage_checks, relationship_checks, count_checks, temporal_checks, transport_checks, schema_checks

LOGGER = logging.getLogger("urbantransit.validator")


def validate_dataset(root: Path) -> ValidationReport:
    configuration = json.loads((root / "metadata/generation_manifest.json").read_text()).get("configuration_used", {})
    if configuration.get("profile") == "production":
        raise ValueError("This in-memory validator is smoke-only; production requires bounded sharded validation. "
                         "Do not load production fact tables with this command.")
    report = ValidationReport(str(root))
    schema_checks.run(root, report)
    relationship_checks.run(root, report)
    count_checks.run(root, report)
    temporal_checks.run(root, report)
    transport_checks.run(root, report)
    leakage_checks.run(root, report)
    production_gate.run(root, report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an UrbanTransit IQ generated dataset")
    parser.add_argument("root", help="dataset output directory")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    root = Path(args.root)
    report = validate_dataset(root)
    output = root / "validation" / "validation_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report.failed_count == 0, "passed_count": report.passed_count, "failed_count": report.failed_count, "total_checks": len(report.checks), "report": str(output)}, sort_keys=True))
    if report.failed_count:
        for check in report.checks:
            if not check.passed:
                LOGGER.error("FAIL %s: %s", check.name, check.detail)
    return 0 if report.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
