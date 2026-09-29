"""Post-generation production acceptance gates.

The production profile is intentionally not run during Phase 1.  These gates
are nevertheless executable so a later run cannot silently pass merely because
it produced files: the validator must compare measured counts with the
approved target envelope and report missing/incorrect metrics as failures.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict

from .config import PRODUCTION_TARGETS
from .validation.common import ValidationReport, load_json


def compare_production_counts(actual: Mapping[str, Any], targets: Mapping[str, int] | None = None) -> Dict[str, Any]:
    """Compare measured production metrics with the approved target envelope."""

    expected = dict(targets or PRODUCTION_TARGETS)
    comparisons: Dict[str, Dict[str, Any]] = {}
    for key, target in expected.items():
        observed = actual.get(key)
        comparisons[key] = {
            "target": target,
            "actual": observed,
            "passed": observed == target,
        }
    return {
        "passed": all(item["passed"] for item in comparisons.values()),
        "comparisons": comparisons,
    }


def _manifest_metrics(generation: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = dict(generation.get("actual_row_counts", {}))
    observed = generation.get("observed_statistics", {})
    aliases = {
        "service_calendars": "service_calendar",
        "tickets": "tickets",
        "passenger_journeys": "passenger_journeys",
        "demand_requests": "requests",
        "delays": "delays",
        "gps_events": "gps",
        "context_events": "context_events",
        "raw_duplicate_ticket_copies": "raw_duplicate_ticket_copies",
        "operational_departures": "operational_departures",
        "operated_departures": "operated_departures",
        "trip_plan_rows": "trip_plan_rows",
    }
    for target_key, observed_key in aliases.items():
        if target_key not in metrics and observed_key in observed:
            metrics[target_key] = observed[observed_key]
    return metrics


def run(root: Path, report: ValidationReport) -> None:
    """Add production-only checks; smoke validation is unchanged."""

    generation = load_json(root, "metadata/generation_manifest.json")
    configuration = generation.get("configuration_used", {})
    if configuration.get("profile") != "production":
        return
    result = compare_production_counts(_manifest_metrics(generation))
    failed = [key for key, item in result["comparisons"].items() if not item["passed"]]
    report.add(
        "production_target_counts",
        result["passed"],
        "measured production metrics satisfy every approved target",
        failed_targets=failed,
        comparisons=result["comparisons"],
    )
    report.add(
        "production_postprocess_statistics",
        generation.get("observed_statistics", {}).get("canonical_movement_count") is not None,
        "production usable movement statistics are measured after raw generation",
    )
