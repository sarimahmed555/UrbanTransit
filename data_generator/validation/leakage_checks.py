"""Leakage, revision availability, and chronological split guards."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List

from .common import ValidationReport, as_int, clean_rows, index_rows, load_injections, load_json, load_table, parse_ts


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    cases_path = root / "metadata/prediction_cases.csv"
    cases = []
    if cases_path.exists():
        with cases_path.open("r", encoding="utf-8", newline="") as handle:
            cases = list(csv.DictReader(handle))
    trips = clean_rows(load_table(root, "trips"), "trips", injection_sources)
    groups: Dict[str, List[dict]] = defaultdict(list)
    for trip in trips:
        groups[trip.get("operational_departure_id")].append(trip)
    bad_group_split = []
    for case in cases:
        group = groups.get(case.get("operational_departure_id"), [])
        splits = {row.get("service_date") for row in group}
        if len(splits) > 1 or any(row.get("service_date") and row.get("service_date")[:7] == "2025-12" and case.get("split") != "TRAIN" for row in group):
            bad_group_split.append(case.get("case_id"))
    report.add("leakage_plan_group_membership", not bad_group_split, "all plan versions and dependent representations stay in one split group", bad=bad_group_split[:10])

    bad_cutoffs = []
    for case in cases:
        cutoff = parse_ts(case.get("prediction_cutoff")); label_start = parse_ts(case.get("label_start_utc")); label_end = parse_ts(case.get("label_end_utc"))
        if not cutoff or not label_start or not label_end or label_start < cutoff:
            bad_cutoffs.append(case.get("case_id"))
    report.add("prediction_cutoff_precedes_label", not bad_cutoffs, "prediction cutoff is not after its label start", bad=bad_cutoffs[:10])

    # Known planned information must be published by the issue time.  Actual
    # outcomes are labels and are intentionally not used as pre-trip features.
    late_plans = []
    for case in cases:
        cutoff = parse_ts(case.get("prediction_cutoff"))
        for trip in groups.get(case.get("operational_departure_id"), []):
            published = parse_ts(trip.get("published_at_utc"))
            if cutoff and published and published > cutoff:
                late_plans.append((case.get("case_id"), trip.get("trip_id")))
    report.add("planned_information_available_by_cutoff", not late_plans, "planned schedule information is available at the historical cutoff", bad=late_plans[:10])

    # Explicit revision selection test: the latest value available at 08:20 is
    # the original value; the 08:40 cutoff sees the 08:30 repair.
    revisions_path = root / "metadata/value_revisions.csv"
    revisions = []
    if revisions_path.exists():
        with revisions_path.open("r", encoding="utf-8", newline="") as handle:
            revisions = list(csv.DictReader(handle))
    original = next((row for row in revisions if row.get("revision_id") == "REV-G3-ORIGINAL"), None)
    corrected = next((row for row in revisions if row.get("revision_id") == "REV-G3-CORRECTED"), None)
    revision_ok = False
    if original and corrected:
        early = parse_ts(original.get("prediction_cutoff_early")); late = parse_ts(original.get("prediction_cutoff_late"))
        candidates_early = [row for row in revisions if parse_ts(row.get("value_available_at_utc")) <= early]
        candidates_late = [row for row in revisions if parse_ts(row.get("value_available_at_utc")) <= late]
        early_latest = max(candidates_early, key=lambda row: as_int(row.get("value_revision"), 0) or 0) if candidates_early else None
        late_latest = max(candidates_late, key=lambda row: as_int(row.get("value_revision"), 0) or 0) if candidates_late else None
        revision_ok = bool(early_latest and late_latest and early_latest.get("value_revision") == "1" and late_latest.get("value_revision") == "2" and late_latest.get("corrected_value") == "6" and early_latest.get("corrected_value") in (None, "", "\\N"))
    report.add("value_available_at_cutoff_filter", revision_ok, "historical feature selection never exposes a correction before its supporting evidence")

    # A boundary horizon must not remain in an earlier split.
    bad_purge = []
    for case in cases:
        label_end = parse_ts(case.get("label_end_utc"))
        service_dates = [row.get("service_date") for row in groups.get(case.get("operational_departure_id"), [])]
        service_day = service_dates[0] if service_dates else None
        crosses = False
        if label_end and service_day:
            for boundary in ("2026-01-01", "2026-04-01", "2026-07-01"):
                if service_day < boundary <= label_end.date().isoformat():
                    crosses = True
                    break
        if crosses and str(case.get("purged")).lower() != "true":
            bad_purge.append(case.get("case_id"))
    report.add("boundary_horizon_purge_policy", not bad_purge, "purged cases explicitly carry a reason/flag rather than silently crossing boundaries", bad=bad_purge[:10])
