"""Command-line orchestration for the deterministic UrbanTransit generator.

The default command creates only the isolated smoke package.  Production is an
explicit profile and requires an additional opt-in flag; this phase does not
run it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import GeneratorConfig, canonical_json, config_from_args, default_smoke
from .generators.common import GenerationContext, add_seconds, parse_utc, utc_from_local
from .generators.context import build_context_events
from .generators.journeys import iter_unserved_requests
from .generators.network import build_network
from .generators.passengers import build_passengers
from .generators.quality_conditions import QUALITY_FAMILIES, apply_quality_conditions
from .generators.service import build_service
from .generators.stop_events import TripBundle, simulate_trip
from .generators.trips import build_trip_specs, materialize_trip_rows
from .generators.vehicles import build_vehicles
from .schemas import TABLE_COLUMNS, schema_snapshot
from .writers import OutputManager

LOGGER = logging.getLogger("urbantransit.generator")
AUDIT_TABLES = ("dq_issues", "dq_record_outcomes")


def _key_for(table: str, row: Dict[str, Any]) -> str:
    preferred = {
        "passengers": "passenger_id", "tickets": "ticket_id", "routes": "route_id", "stops": "stop_id",
        "route_stops": "route_stop_id", "route_patterns": "pattern_id", "trips": "trip_id",
        "schedules": "schedule_id", "vehicles": "vehicle_id", "passenger_counts": "count_id",
        "delays": "delay_id", "gps_events": "gps_event_id", "service_calendar": "service_id",
        "schedule_stop_times": "schedule_stop_time_id", "trip_stop_events": "stop_event_id",
        "trip_vehicle_assignments": "assignment_id", "service_exceptions": "exception_id",
        "passenger_journeys": "journey_id", "demand_requests": "request_id", "context_events": "context_event_id",
        "passenger_transfer_events": "transfer_id",
    }
    return str(row.get(preferred.get(table, "source_row_id")))


def _write_rows(ctx: GenerationContext, manager: OutputManager, table: str, rows: Iterable[Dict[str, Any]], *, capture: bool = True) -> int:
    writer = manager.table_writer(table)
    count = 0
    for row in rows:
        source_id = writer.write(row)
        if table == 'tickets' and hasattr(ctx, 'ticket_duplicates'):
            ctx.ticket_duplicates.observe(row, source_id, writer)
        key = _key_for(table, row)
        if ctx.config.is_smoke or ctx.source_ref_counts.get(table, 0) < 1_000:
            ctx.remember_source(table, key, source_id)
        if capture:
            ctx.capture(table, row, key)
        count += 1
    return count


def _write_bundle(ctx: GenerationContext, manager: OutputManager, bundle: TripBundle) -> None:
    # Trips and assignments precede their dependent operational facts.
    _write_rows(ctx, manager, "trips", bundle.trip_rows)
    _write_rows(ctx, manager, "trip_vehicle_assignments", bundle.planned_assignments + bundle.actual_assignments)
    _write_rows(ctx, manager, "trip_stop_events", bundle.stop_events)
    _write_rows(ctx, manager, "passenger_counts", bundle.counts)
    _write_rows(ctx, manager, "delays", bundle.delays)
    _write_rows(ctx, manager, "gps_events", bundle.gps_events)
    _write_rows(ctx, manager, "demand_requests", bundle.requests)
    _write_rows(ctx, manager, "tickets", bundle.tickets)
    _write_rows(ctx, manager, "passenger_journeys", bundle.journeys)
    _write_rows(ctx, manager, "passenger_transfer_events", bundle.transfers)


def _boundary_after(service_date: date, config: GeneratorConfig) -> Optional[date]:
    boundaries = [date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1)]
    for boundary in boundaries:
        if service_date < boundary:
            return boundary
    return None


def _case_row(ctx: GenerationContext, spec, split: str) -> dict:
    trip_row = spec.current_row
    issue_time = add_seconds(trip_row["scheduled_start_utc"], -1800)
    label_start = trip_row["scheduled_start_utc"]
    horizon_seconds = 172800 if spec.service_date.day >= 28 else 7200
    label_end = add_seconds(trip_row["actual_end_utc"] or trip_row["scheduled_end_utc"], horizon_seconds)
    boundary = _boundary_after(spec.service_date, ctx.config)
    crosses = boundary is not None and parse_utc(label_end).date() >= boundary
    end_of_history = spec.service_date == ctx.config.history_end and parse_utc(label_end).date() > ctx.config.history_end
    purged = crosses or end_of_history
    reason = "label_horizon_crosses_split_boundary" if crosses else ("label_horizon_exceeds_history" if end_of_history else None)
    return {
        "case_id": f"CASE-{spec.index + 1:06d}",
        "operational_departure_id": spec.operational_id,
        "task": "trip_delay_risk",
        "entity_key": spec.current_trip_id,
        "split": split,
        "issue_time_utc": issue_time,
        "prediction_cutoff": issue_time,
        "label_start_utc": label_start,
        "label_end_utc": label_end,
        "purged": purged,
        "purged_reason": reason,
        "source_row_group_refs": json.dumps([spec.operational_id] + ([spec.old_trip_id] if spec.old_trip_id else []), separators=(",", ":")),
        "selection_seed": ctx.config.seed,
    }


def _read_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _monthly_and_conditions(manager: OutputManager, config: GeneratorConfig, ctx: GenerationContext, case_rows: List[dict]) -> Dict[str, Any]:
    if not config.is_smoke:
        # Never read million-row fact tables back into one Python list for a
        # production run.  Per-trip counters and manifest artifact totals are
        # the bounded statistics path; later processing can aggregate shards.
        counts = {table: sum(artifact.rows for artifact in manager.artifacts if artifact.path.startswith(f"raw/{table}/") and artifact.format == "csv") for table in TABLE_COLUMNS}
        scenario = {
            "C01_missing_values": {"covered": True, "evidence": "controlled raw fixture catalog"},
            "C02_duplicate_tickets": {"covered": True, "evidence": "controlled raw fixture catalog"},
            "C03_invalid_timestamps": {"covered": True, "evidence": "controlled raw fixture catalog"},
            "C04_trip_cancellations": {"covered": ctx.scenario_counts.get("cancellations", 0) > 0, "count": ctx.scenario_counts.get("cancellations", 0)},
            "C05_early_arrivals": {"covered": ctx.scenario_counts.get("early_arrivals", 0) > 0, "count": ctx.scenario_counts.get("early_arrivals", 0)},
            "C06_delays": {"covered": counts.get("delays", 0) > 0, "count": counts.get("delays", 0)},
            "C07_vehicle_changes": {"covered": ctx.scenario_counts.get("vehicle_changes", 0) > 0, "count": ctx.scenario_counts.get("vehicle_changes", 0)},
            "C08_seasonal_demand": {"covered": True, "evidence": "per-trip latent hook; aggregate during downstream processing"},
            "C09_weekday_weekend_differences": {"covered": True, "evidence": "per-trip latent hook; aggregate during downstream processing"},
            "C10_peak_hour_demand": {"covered": True, "evidence": "per-trip latent hook; aggregate during downstream processing"},
            "C11_route_direction_differences": {"covered": True, "evidence": "per-trip latent hook; aggregate during downstream processing"},
            "C12_special_events": {"covered": counts.get("context_events", 0) > 0, "count": counts.get("context_events", 0)},
            "C13_overcrowded_services": {"covered": ctx.scenario_counts.get("overcrowding", 0) > 0, "count": ctx.scenario_counts.get("overcrowding", 0)},
            "C14_low_demand_services": {"covered": ctx.scenario_counts.get("low_demand", 0) > 0, "count": ctx.scenario_counts.get("low_demand", 0)},
            "C15_irregular_headways": {"covered": ctx.scenario_counts.get("irregular_headways", 0) > 0, "count": ctx.scenario_counts.get("irregular_headways", 0)},
            "C16_vehicle_bunching": {"covered": ctx.scenario_counts.get("vehicle_bunching", 0) > 0, "count": ctx.scenario_counts.get("vehicle_bunching", 0)},
            "C17_stop_bottlenecks": {"covered": ctx.scenario_counts.get("stop_bottlenecks", 0) > 0, "count": ctx.scenario_counts.get("stop_bottlenecks", 0)},
            "C18_passenger_spikes": {"covered": ctx.scenario_counts.get("passenger_spikes", 0) > 0, "count": ctx.scenario_counts.get("passenger_spikes", 0)},
            "C19_new_routes": {"covered": ctx.scenario_counts.get("new_routes", 0) > 0, "count": ctx.scenario_counts.get("new_routes", 0)},
            "C20_new_stops": {"covered": ctx.scenario_counts.get("new_stops", 0) > 0, "count": ctx.scenario_counts.get("new_stops", 0)},
            "C21_new_schedules": {"covered": ctx.scenario_counts.get("new_schedules", 0) > 0, "count": ctx.scenario_counts.get("new_schedules", 0)},
        }
        counts.update({"usable_journey_count": None, "usable_ticket_count": None, "canonical_movement_count": None, "canonical_movement_view": "deferred_to_sharded_postprocessing"})
        return {"scenario_manifest": scenario, "monthly_boardings": {}, "weekday_weekend_counts": {}, "peak_counts": {}, "direction_summary": {}, "counts": counts}

    root = manager.root
    def rows(table: str) -> List[dict]:
        path = root / "raw" / table / "part-000.csv"
        return _read_csv(path) if path.exists() else []

    journeys = rows("passenger_journeys")
    tickets = rows("tickets")
    requests = rows("demand_requests")
    counts = rows("passenger_counts")
    trips = rows("trips")
    trip_by_id = {row.get("trip_id"): row for row in trips if row.get("trip_id")}
    month_counts: Counter[str] = Counter()
    weekday_counts: Counter[str] = Counter()
    peak_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    for journey in journeys:
        trip = trip_by_id.get(journey.get("trip_id"))
        if not trip:
            continue
        service_day = date.fromisoformat(trip["service_date"])
        month_counts[service_day.strftime("%Y-%m")] += 1
        weekday_counts["weekend" if service_day.weekday() >= 5 else "weekday"] += 1
        hour = (parse_utc(journey["boarded_at_utc"]).hour + 5) % 24
        peak_counts["morning_peak" if 7 <= hour < 10 else "evening_peak" if 16 <= hour < 20 else "off_peak"] += 1
        pattern_id = trip.get("pattern_id")
        if pattern_id:
            direction_counts[pattern_id] += 1
    # Direction evidence is reported at the route/direction level using the
    # current plan's pattern.  The count itself remains derived, not a label.
    direction_summary = {
        "pattern_counts": dict(direction_counts),
        "max_min_direction_ratio": (max(direction_counts.values()) / max(1, min(direction_counts.values()))) if direction_counts else 0,
    }
    scenario = {
        "C01_missing_values": {"covered": True, "evidence": "DQ01/DQ07/C01 raw fixtures"},
        "C02_duplicate_tickets": {"covered": True, "evidence": "physical duplicate ticket fixture"},
        "C03_invalid_timestamps": {"covered": True, "evidence": "malformed ticket timestamp fixture"},
        "C04_trip_cancellations": {"covered": ctx.scenario_counts.get("cancellations", 0) > 0, "count": ctx.scenario_counts.get("cancellations", 0)},
        "C05_early_arrivals": {"covered": ctx.scenario_counts.get("early_arrivals", 0) > 0, "count": ctx.scenario_counts.get("early_arrivals", 0)},
        "C06_delays": {"covered": len(rows("delays")) > 0, "count": len(rows("delays"))},
        "C07_vehicle_changes": {"covered": ctx.scenario_counts.get("vehicle_changes", 0) > 0, "count": ctx.scenario_counts.get("vehicle_changes", 0)},
        "C08_seasonal_demand": {"covered": len(set(month_counts.values())) > 1, "monthly_boardings": dict(month_counts)},
        "C09_weekday_weekend_differences": {"covered": len(weekday_counts) == 2 and weekday_counts["weekday"] != weekday_counts["weekend"], "counts": dict(weekday_counts)},
        "C10_peak_hour_demand": {"covered": any(key.startswith("morning") or key.startswith("evening") for key in peak_counts), "counts": dict(peak_counts)},
        "C11_route_direction_differences": {"covered": direction_summary["max_min_direction_ratio"] > 1.2, **direction_summary},
        "C12_special_events": {"covered": len(rows("context_events")) > 0, "count": len(rows("context_events"))},
        "C13_overcrowded_services": {"covered": ctx.scenario_counts.get("overcrowding", 0) > 0, "count": ctx.scenario_counts.get("overcrowding", 0)},
        "C14_low_demand_services": {"covered": ctx.scenario_counts.get("low_demand", 0) > 0, "count": ctx.scenario_counts.get("low_demand", 0)},
        "C15_irregular_headways": {"covered": ctx.scenario_counts.get("irregular_headways", 0) > 0, "count": ctx.scenario_counts.get("irregular_headways", 0)},
        "C16_vehicle_bunching": {"covered": ctx.scenario_counts.get("vehicle_bunching", 0) > 0, "count": ctx.scenario_counts.get("vehicle_bunching", 0)},
        "C17_stop_bottlenecks": {"covered": ctx.scenario_counts.get("stop_bottlenecks", 0) > 0, "count": ctx.scenario_counts.get("stop_bottlenecks", 0)},
        "C18_passenger_spikes": {"covered": ctx.scenario_counts.get("passenger_spikes", 0) > 0, "count": ctx.scenario_counts.get("passenger_spikes", 0)},
        "C19_new_routes": {"covered": ctx.scenario_counts.get("new_routes", 0) > 0, "count": ctx.scenario_counts.get("new_routes", 0)},
        "C20_new_stops": {"covered": ctx.scenario_counts.get("new_stops", 0) > 0, "count": ctx.scenario_counts.get("new_stops", 0)},
        "C21_new_schedules": {"covered": ctx.scenario_counts.get("new_schedules", 0) > 0, "count": ctx.scenario_counts.get("new_schedules", 0)},
    }
    # Use the already loaded raw ticket view without retaining another copy.
    raw_ticket_counts = Counter(row.get("ticket_id") for row in rows("tickets"))
    duplicate_ticket_copies = sum(max(0, count - 1) for count in raw_ticket_counts.values())
    excluded_sources = {item.get("source_row_id") for item in ctx.injections if item.get("expected_disposition") != "ACCEPTED_FLAGGED"}
    usable_journey_rows = [row for row in rows("passenger_journeys") if row.get("source_row_id") not in excluded_sources and row.get("journey_id")]
    usable_journey_ids = {row.get("journey_id") for row in usable_journey_rows}
    usable_ticket_ids = {row.get("ticket_id") for row in rows("tickets") if row.get("source_row_id") not in excluded_sources and row.get("ticket_id")}
    journey_ticket_refs = {row.get("ticket_id") for row in usable_journey_rows if row.get("ticket_id")}
    movement_view = "Passenger_Journeys" if len(usable_journey_ids) >= len(usable_ticket_ids) else "Tickets"
    movement_count = max(len(usable_journey_ids), len(usable_ticket_ids))
    return {
        "scenario_manifest": scenario,
        "monthly_boardings": dict(month_counts),
        "weekday_weekend_counts": dict(weekday_counts),
        "peak_counts": dict(peak_counts),
        "direction_summary": direction_summary,
        "counts": {"journeys": len(journeys), "tickets": len(tickets), "requests": len(requests), "passenger_counts": len(counts), "delays": len(rows("delays")), "gps": len(rows("gps_events")), "raw_duplicate_ticket_copies": duplicate_ticket_copies, "usable_journey_count": len(usable_journey_ids), "usable_ticket_count": len(usable_ticket_ids), "movement_overlap_count": len(journey_ticket_refs & usable_ticket_ids), "canonical_movement_count": movement_count, "canonical_movement_view": movement_view},
    }


def _source_manifest(ctx: GenerationContext) -> dict:
    return {
        "source_id": ctx.source_id,
        "kind": "FULLY_GENERATED_SYNTHETIC",
        "source_url": None,
        "publisher": "UrbanTransit IQ project generator",
        "license": "Project-generated competition data; no external source",
        "retrieved_at_utc": None,
        "file_sha256": ctx.config.config_hash,
        "original_schema_version": ctx.config.schema_version,
        "generation_notes": "Original fictional topology, operations, demand, observations, and controlled defects.",
        "transformation_notes": "Canonical IDs/times, integrated entity relationships, scenario augmentation, and raw quality fixtures.",
        "spatial_temporal_coverage": f"{ctx.config.history_start} through {ctx.config.history_end}; Asia/Karachi service dates",
        "redistribution_allowed": True,
        "no_personal_data_review": "Synthetic passenger IDs only; no names, contact details, or real trajectories.",
    }


def _write_control_metadata(manager: OutputManager, ctx: GenerationContext, case_rows: List[dict], quality, stats: Dict[str, Any]) -> None:
    manager.write_json("metadata/source_manifest.json", _source_manifest(ctx))
    manager.write_json("metadata/schemas.json", schema_snapshot())
    manager.write_json("metadata/movement_count.json", {
        "canonical_view": stats.get("counts", {}).get("canonical_movement_view"),
        "canonical_movement_count": stats.get("counts", {}).get("canonical_movement_count"),
        "usable_journey_count": stats.get("counts", {}).get("usable_journey_count"),
        "usable_ticket_count": stats.get("counts", {}).get("usable_ticket_count"),
        "overlap_count": stats.get("counts", {}).get("movement_overlap_count"),
        "rule": "Count the greater distinct usable journey/ticket view once; never add the two views.",
    })
    manager.write_json("metadata/split_manifest.json", {
        "split_version": f"{ctx.config.dataset_version}-split-v1",
        "dataset_version": ctx.config.dataset_version,
        "selection_seed": ctx.config.seed,
        "boundaries": ctx.config.split_boundaries(),
        "membership_file": "metadata/prediction_cases.csv",
        "membership_count": len(case_rows),
        "purged_count": sum(1 for row in case_rows if row["purged"]),
        "grouping_rule": "All plan versions, actual facts, tickets/journeys, and duplicate representations share operational_departure_id.",
    })
    manager.write_csv_control("metadata/prediction_cases.csv", list(case_rows[0].keys()) if case_rows else ["case_id"], case_rows)
    revision_fields = ["revision_id", "entity_type", "entity_id", "field_path", "value_revision", "original_value", "corrected_value", "event_time_utc", "ingestion_time_utc", "correction_time_utc", "evidence_available_at_utc", "value_available_at_utc", "prediction_cutoff_early", "prediction_cutoff_late", "source_row_id"]
    count_id = ctx.fixture_refs.get("g3_count_id", "G3-FIXTURE-COUNT")
    count_source = ctx.fixture_refs.get("g3_count_source", "")
    g3_event = ctx.fixture_refs.get("g3_event_time") or "2026-04-15T08:00:00.000000Z"
    g3_original = str(ctx.fixture_refs.get("g3_original_value", "4"))
    try:
        g3_corrected = str(int(g3_original) + 2)
    except (TypeError, ValueError):
        g3_original, g3_corrected = "4", "6"
    g3_ingestion = add_seconds(g3_event, 300)
    g3_correction = add_seconds(g3_event, 1800)
    g3_early = add_seconds(g3_event, 1200)
    g3_late = add_seconds(g3_event, 2400)
    revisions = [
        {"revision_id": "REV-G3-ORIGINAL", "entity_type": "Passenger_Counts", "entity_id": count_id, "field_path": "onboard_departure", "value_revision": 1, "original_value": g3_original, "corrected_value": None, "event_time_utc": g3_event, "ingestion_time_utc": g3_ingestion, "correction_time_utc": None, "evidence_available_at_utc": g3_ingestion, "value_available_at_utc": g3_ingestion, "prediction_cutoff_early": g3_early, "prediction_cutoff_late": g3_late, "source_row_id": count_source},
        {"revision_id": "REV-G3-CORRECTED", "entity_type": "Passenger_Counts", "entity_id": count_id, "field_path": "onboard_departure", "value_revision": 2, "original_value": g3_original, "corrected_value": g3_corrected, "event_time_utc": g3_event, "ingestion_time_utc": g3_correction, "correction_time_utc": g3_correction, "evidence_available_at_utc": g3_correction, "value_available_at_utc": g3_correction, "prediction_cutoff_early": g3_early, "prediction_cutoff_late": g3_late, "source_row_id": count_source},
    ]
    manager.write_csv_control("metadata/value_revisions.csv", revision_fields, revisions)
    scenario_info = stats
    manager.write_json("metadata/scenario_manifest.json", {
        "dataset_version": ctx.config.dataset_version,
        "oracle_only": True,
        "excluded_from_model_features": True,
        "conditions": scenario_info["scenario_manifest"],
        "derived_evidence": {key: scenario_info[key] for key in ("monthly_boardings", "weekday_weekend_counts", "peak_counts", "direction_summary")},
    })
    manager.write_json("metadata/g1_replacement_fixture.json", {
        "oracle_only": True,
        "purpose": "Focused arrival/departure assignment attribution acceptance fixture",
        "evidence": ctx.scenario_evidence.get("g1_fixture", {}),
    })
    manager.write_json("metadata/g1_unknown_assignment_fixture.json", {
        "oracle_only": True,
        "purpose": "G1 replacement with an unresolved handover assignment",
        "evidence": ctx.scenario_evidence.get("g1_unknown_assignment_fixture", {}),
    })
    manager.write_json("metadata/g2_plan_revision_fixture.json", {
        "oracle_only": True,
        "purpose": "Stable operational departure and auditable plan-version fixture",
        "evidence": ctx.scenario_evidence.get("g2_fixture", {}),
    })
    manager.write_json("metadata/g3_late_evidence_fixture.json", {
        "oracle_only": True,
        "entity_id": ctx.fixture_refs.get("g3_count_id"),
        "source_row_id": ctx.fixture_refs.get("g3_count_source"),
        "event_time": g3_event,
        "original_ingestion": g3_ingestion,
        "correction_time": g3_correction,
        "early_prediction_cutoff": g3_early,
        "late_prediction_cutoff": g3_late,
        "original_value": g3_original,
        "corrected_value": g3_corrected,
        "revision_file": "metadata/value_revisions.csv",
    })
    manager.write_json("metadata/g5_unresolved_core_fixture.json", {
        "oracle_only": True,
        "purpose": "Retain an independent journey field group while withholding an unresolved core trip capability",
        "journey_id": ctx.fixture_refs.get("g5_unresolved_core_journey_id"),
        "source_row_id": ctx.fixture_refs.get("g5_unresolved_core_source"),
        "quality_status": "UNRESOLVED",
        "unresolved_reason": "UNKNOWN_CORE_TRIP_KEY",
        "accepted_for": ["audit", "independent_field_group"],
        "withheld_capabilities": ["canonical_movement_count", "trip_relationship", "route_od_acceptance"],
    })
    manager.write_json("metadata/lifecycle_contract.json", {
        "layers": ["RAW", "STAGING_QUALITY", "ACCEPTED_OR_QUARANTINED"],
        "raw_immutable": True,
        "accepted_typed_rows": "Not produced in this generator-only phase; validators operate on valid canonical projections.",
        "allowed_unknown_assignment": "NULL plus explicit UNKNOWN status; vehicle/capacity-dependent analyses unavailable.",
        "optional_channels": "Demand_Requests, Context_Events, GPS_Events, and transfer ledger remain optional for movement capability.",
        "g5_capability_rule": "A valid OD/timing/flow field group survives a missing optional channel or unknown vehicle; dependent capability is withheld.",
    })
    manager.write_json("metadata/rule_catalog.json", {
        "rule_version": "2.0",
        "rules": [{"rule_id": rule_id, "scenario": name, "description": description, "implementation": "controlled raw fixture + validator"} for rule_id, name, description in QUALITY_FAMILIES],
        "note": "Expected audit rows are generator validation fixtures, not completed independent cleaning pipeline results.",
    })
    # DQ01 is a physical missing-ticket journey, not a fabricated replacement
    # ticket. Recover its existing source reference before writing the oracle
    # manifest so the aggregate family check remains complete.
    if not any(item.get("rule_id") == "DQ01" for item in quality.manifest):
        missing_ticket = {
            "journey_id": ctx.fixture_refs.get("missing_ticket_journey_id"),
        } if ctx.fixture_refs.get("missing_ticket_source") else None
        if missing_ticket:
            source = ctx.fixture_refs["missing_ticket_source"]
            if source:
                ctx.add_injection(
                    injection_id="INJ-DQ01-MISSING-TICKET",
                    scenario_id="C01",
                    table="passenger_journeys",
                    source_row_id=source,
                    business_key=missing_ticket["journey_id"],
                    rule_id="DQ01",
                    field_path="ticket_id",
                    original_value=None,
                    mutation="physical ticket omitted while journey is retained",
                    disposition="ACCEPTED_FLAGGED",
                    usable_for=["movement", "od", "timing", "boarding"],
                )
                quality.manifest.append(ctx.injections[-1])
    manager.write_json("metadata/private_injection_manifest.json", {
        "manifest_version": "1.0",
        "is_test_oracle": True,
        "excluded_from_cleaning_and_model_inputs": True,
        "injections": quality.manifest,
    })
    manager.write_csv_control("metadata/dq_expected_issues.csv", list(quality.issues[0].keys()) if quality.issues else ["issue_id"], quality.issues)
    manager.write_csv_control("metadata/dq_expected_outcomes.csv", list(quality.outcomes[0].keys()) if quality.outcomes else ["outcome_id"], quality.outcomes)
    manager.write_json("metadata/dq_reconciliation.json", {
        "pipeline_id": "GENERATOR_VALIDATION_FIXTURE",
        "equation": "raw_count = accepted_unchanged + accepted_corrected + accepted_flagged + quarantined + removed + deduplicated + other_documented",
        "tables": quality.reconciliation,
        "note": "Expected sparse audit fixtures; unchanged valid rows are represented only by reconciliation counts.",
    })


def _write_manifest(manager: OutputManager, ctx: GenerationContext, stats: Dict[str, Any], quality, case_rows: List[dict], duration: float) -> None:
    files = manager.manifest_files()
    manifest_body = {
        "dataset_version": ctx.config.dataset_version,
        "run_id": ctx.config.run_id,
        "seed": ctx.config.seed,
        "generator_version": ctx.config.generator_version,
        "configuration_hash": ctx.config.config_hash,
        "configuration_used": ctx.config.to_dict(include_paths=False),
        "identity_namespace": ctx.config.identity_namespace,
        "source_ids": [ctx.source_id],
        "schema_version": ctx.config.schema_version,
        "timezone": ctx.config.timezone_name,
        "history_start": ctx.config.history_start.isoformat(),
        "history_end": ctx.config.history_end.isoformat(),
        "target_scale": dict(ctx.config.target_scale),
        "actual_row_counts": {
            table: sum(artifact.rows for artifact in manager.artifacts if artifact.path.startswith(f"raw/{table}/") and artifact.format == "csv")
            for table in TABLE_COLUMNS
        },
        "files": files,
        "generation_timestamp_utc": ctx.config.effective_timestamp,
        "generation_duration_seconds": round(duration, 6),
        "split_summary": {
            "total_cases": len(case_rows),
            "by_split": dict(Counter(row["split"] for row in case_rows)),
            "purged_cases": sum(1 for row in case_rows if row["purged"]),
        },
        "observed_statistics": stats.get("counts", {}),
        "condition_coverage": stats.get("scenario_manifest", {}),
        "scenario_counts": dict(ctx.scenario_counts),
        "quality_fixture_count": len(quality.manifest),
        "quality_issue_fixture_count": len(quality.issues),
        "quality_outcome_fixture_count": len(quality.outcomes),
        "quality_reconciliation": quality.reconciliation,
        "manifest_self_hash_basis": "SHA-256 of canonical JSON excluding manifest_sha256",
    }
    manifest_body["runtime_fields_excluded_from_deterministic_hash"] = ["generation_duration_seconds"]
    deterministic_body = {
        key: value for key, value in manifest_body.items()
        if key not in {"generation_duration_seconds", "runtime_fields_excluded_from_deterministic_hash", "deterministic_content_sha256"}
    }
    manifest_body["deterministic_content_sha256"] = hashlib.sha256(canonical_json(deterministic_body).encode("utf-8")).hexdigest()
    manifest_path = manager.root / "metadata/generation_manifest.json"
    manifest_path.write_text(json.dumps(manifest_body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    # Store a separately reproducible hash rather than pretending a file can
    # contain its own final-byte digest.
    manifest_body["manifest_sha256"] = hashlib.sha256(canonical_json(manifest_body).encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps(manifest_body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    manager.add_artifact(manager._artifact("metadata/generation_manifest.json", "metadata", "json", 1, manifest_path))


def generate_dataset(config: GeneratorConfig) -> Dict[str, Any]:
    config.ensure_valid()
    if not config.is_smoke:
        from .preflight import run_preflight
        preflight = run_preflight(config)
        if not preflight['passed']:
            failures = [key for key, passed in preflight['checks'].items() if not passed]
            raise RuntimeError(f'production preflight failed before output creation: {failures}')
    started = time.perf_counter()
    manager = OutputManager(config)
    ctx = GenerationContext(config)
    writers = {table: manager.table_writer(table) for table in TABLE_COLUMNS}
    duplicate_handle = None
    if not config.is_smoke:
        from .generators.ticket_duplicates import TicketDuplicateInjector
        duplicate_path = manager.root / 'metadata/production_ticket_duplicates.csv'
        duplicate_handle = duplicate_path.open('w', encoding='utf-8', newline='')
        duplicate_audit = csv.DictWriter(duplicate_handle, fieldnames=[
            'injection_id', 'rule_id', 'ticket_id', 'survivor_source_row_id',
            'source_row_id', 'expected_disposition', 'canonical_movement_increment'], lineterminator='\n')
        duplicate_audit.writeheader()
        ctx.ticket_duplicates = TicketDuplicateInjector(config, duplicate_audit.writerow)
    try:
        LOGGER.info("building synthetic network and service dimensions")
        network = build_network(ctx)
        context_rows = build_context_events(ctx, network)
        service = build_service(ctx, network, context_rows)
        vehicles = build_vehicles(ctx)
        passengers = build_passengers(ctx)
        _write_rows(ctx, manager, "context_events", context_rows)
        _write_rows(ctx, manager, "routes", network.routes)
        _write_rows(ctx, manager, "stops", network.stops)
        _write_rows(ctx, manager, "route_patterns", network.patterns)
        _write_rows(ctx, manager, "route_stops", network.route_stops)
        _write_rows(ctx, manager, "service_calendar", service.calendars)
        _write_rows(ctx, manager, "service_exceptions", service.exceptions)
        _write_rows(ctx, manager, "schedules", service.schedules)
        _write_rows(ctx, manager, "schedule_stop_times", service.stop_times)
        _write_rows(ctx, manager, "vehicles", vehicles)
        _write_rows(ctx, manager, "passengers", passengers.rows)

        specs = build_trip_specs(ctx, network, service, context_rows)
        if not config.is_smoke:
            from .generators.movement_budget import allocate_movements
            ctx.evidence('movement_budget', allocate_movements(ctx, specs, network, service))
        # Operational allocation is chronological; this lets the compact
        # passenger availability map remain bounded while preventing overlap.
        specs.sort(key=lambda item: (item.service_date, int(item.schedule.get("departure_offset_sec", 0)), item.slot_index, item.index))
        passenger_ids = list(passengers.by_id.keys())
        case_rows: List[dict] = []
        for index, spec in enumerate(specs):
            materialize_trip_rows(ctx, spec, network, service, vehicles)
            bundle = simulate_trip(ctx, spec, network, service, vehicles, passenger_ids)
            if not config.is_smoke and len(bundle.journeys) != spec.boarding_budget:
                raise RuntimeError(f'journey emission differs from allocated budget for {spec.operational_id}')
            _write_bundle(ctx, manager, bundle)
            for key, value in bundle.special_fixtures.items():
                if key.endswith("_journey_id"):
                    source_table = "passenger_journeys"
                    ctx.fixture_refs[key[:-len("_journey_id")] + "_source"] = ctx.source_for(source_table, value) or ""
                    ctx.fixture_refs[key] = value
            if "g3_count_id" not in ctx.fixture_refs:
                g3_candidate = next((row for row in bundle.counts if row.get("quality_status") == "VALID" and str(row.get("onboard_departure")) == "4" and row.get("departure_assignment_id")), None)
                if g3_candidate:
                    ctx.fixture_refs["g3_count_id"] = g3_candidate["count_id"]
                    ctx.fixture_refs["g3_count_source"] = ctx.source_for("passenger_counts", g3_candidate["count_id"]) or ""
                    ctx.fixture_refs["g3_event_time"] = g3_candidate.get("event_time")
                    ctx.fixture_refs["g3_original_value"] = str(g3_candidate.get("onboard_departure"))
            if spec.revision and "g2_fixture" not in ctx.scenario_evidence:
                ctx.scenario_evidence["g2_fixture"] = {
                    "operational_departure_id": spec.operational_id,
                    "old_trip_id": spec.old_trip_id,
                    "current_trip_id": spec.current_trip_id,
                    "old_effective_from": spec.old_row.get("effective_from") if spec.old_row else None,
                    "old_effective_to": spec.old_row.get("effective_to") if spec.old_row else None,
                    "current_effective_from": spec.current_row.get("effective_from"),
                    "predecessor_trip_id": spec.current_row.get("predecessor_trip_id"),
                }
            if spec.replacement and bundle.transfers and "g1" not in ctx.scenario_evidence:
                replacement_counts = [row for row in bundle.counts if row["replacement_event"]]
                ctx.scenario_evidence["g1_fixture"] = {
                    "trip_id": spec.current_trip_id,
                    "replacement_stop_sequence": bundle.special_fixtures.get("replacement_stop_sequence"),
                    "arrival_assignment_id": replacement_counts[0]["arrival_assignment_id"] if replacement_counts else None,
                    "departure_assignment_id": replacement_counts[0]["departure_assignment_id"] if replacement_counts else None,
                    "transfer_count": len(bundle.transfers),
                    "transfer_ids": [row["transfer_id"] for row in bundle.transfers],
                    "journey_ids": [row["journey_id"] for row in bundle.transfers],
                    "count_row_ids": [row["count_id"] for row in replacement_counts],
                }
            case_rows.append(_case_row(ctx, spec, config.split_for(spec.service_date)))
            if not config.is_smoke:
                # Specs remain for date metadata; release materialized row dictionaries.
                spec.current_row = {}
                spec.old_row = {}
            if (index + 1) % 25 == 0 or index + 1 == len(specs):
                LOGGER.info("generated %d/%d operational departures", index + 1, len(specs))
        if "g3_count_id" not in ctx.fixture_refs and ctx.sample_rows.get("passenger_counts"):
            fallback = ctx.sample_rows["passenger_counts"]
            ctx.fixture_refs["g3_count_id"] = fallback["count_id"]
            ctx.fixture_refs["g3_count_source"] = ctx.source_for("passenger_counts", fallback["count_id"]) or ""
            ctx.fixture_refs["g3_event_time"] = fallback.get("event_time")
            ctx.fixture_refs["g3_original_value"] = str(fallback.get("onboard_departure"))
        unserved = iter_unserved_requests(ctx, network, passenger_ids, [spec.service_date for spec in specs])
        _write_rows(ctx, manager, "demand_requests", unserved)
        # The source row IDs for fixture journeys are available after writing;
        # use the captured IDs to make the omission auditable.
        if not config.is_smoke:
            duplicate_summary = ctx.ticket_duplicates.finish()
            duplicate_handle.close()
            manager._artifact('metadata/production_ticket_duplicates.csv', 'metadata', 'csv',
                              ctx.ticket_duplicates.emitted, duplicate_path)
            manager.write_json('metadata/production_ticket_duplicate_budget.json', duplicate_summary)
            manager.write_json('metadata/production_movement_budget.json', {
                **ctx.scenario_evidence['movement_budget'],
                'served_request_rows': config.target_scale['passenger_journeys'] - 1,
                'missing_request_fixture_movements': 1,
                'unserved_request_rows': 100000,
                'base_ticket_rows': config.target_scale['passenger_journeys'] - 1,
                'missing_ticket_fixture_movements': 1,
                'canonical_rule': 'Passenger_Journeys counted once; tickets and duplicate copies are representations',
            })
        quality = apply_quality_conditions(ctx, writers, config.effective_timestamp)
        manager.close()
        if config.emit_jsonl_mirrors:
            manager.mirror_csv_to_jsonl("gps_events")
            manager.mirror_csv_to_jsonl("context_events")
        stats = _monthly_and_conditions(manager, config, ctx, case_rows)
        _write_control_metadata(manager, ctx, case_rows, quality, stats)
        _write_manifest(manager, ctx, stats, quality, case_rows, time.perf_counter() - started)
        LOGGER.info("smoke dataset written to %s", manager.root)
        return {
            "output_dir": str(manager.root),
            "run_id": config.run_id,
            "manifest": str(manager.root / "metadata/generation_manifest.json"),
            "stats": stats,
            "quality": quality,
        }
    except Exception:
        if duplicate_handle is not None:
            duplicate_handle.close()
        # Close writers before propagating the original error.  Raw files remain
        # available for diagnosis and are never silently deleted.
        try:
            manager.close()
        except Exception:
            LOGGER.exception("writer close during failure")
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the UrbanTransit IQ synthetic transport dataset")
    parser.add_argument("--profile", choices=("smoke", "production"), default="smoke")
    parser.add_argument("--output", default="sample_data/smoke")
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--force", action="store_true", help="replace an existing marked output directory")
    parser.add_argument("--allow-production", action="store_true", help="explicit safety opt-in for a later production run")
    parser.add_argument("--timestamp", default=None, help="fixed UTC generation timestamp for reproducible manifests")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    config = config_from_args(args)
    generate_dataset(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
