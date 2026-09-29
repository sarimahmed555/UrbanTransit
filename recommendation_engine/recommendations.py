"""Deterministic rules over source facts. Thresholds are policy, never fake results."""
from .contracts import NotReady, digest, finite, provenance, require_number

RULES = {
    "increase_frequency": ("Increase frequency on the overloaded route", ("mean_occupancy_ratio", "overload_days")),
    "reduce_underutilized_service": ("Review reducing underutilized trips/services while preserving coverage", ("mean_occupancy_ratio", "underutilized_days")),
    "modify_schedule": ("Modify the schedule around recurrent delays", ("late_fraction", "late_days")),
    "higher_capacity_vehicle": ("Allocate a higher-capacity vehicle", ("peak_occupancy_ratio", "overload_days")),
    "investigate_bottleneck": ("Investigate the bottleneck stop", ("mean_delay_sec", "late_days")),
    "adjust_departure_time": ("Adjust departure time using the evaluated alternative", ("current_expected_wait_minutes", "alternative_expected_wait_minutes")),
    "improve_headways": ("Improve spacing between vehicles", ("headway_cv", "irregular_headway_days")),
}


def validate_policy(p):
    for key in ("minimum_samples", "minimum_days", "overload_ratio", "underutilization_ratio",
                "late_fraction", "bottleneck_delay_sec", "headway_cv"):
        require_number(p, key, positive=True)
    if int(p["minimum_samples"]) != p["minimum_samples"] or int(p["minimum_days"]) != p["minimum_days"] or p["minimum_days"] < 2:
        raise ValueError("Sample/day policies must be integers, with recurrence >=2 days")
    if not 0 < p["late_fraction"] <= 1 or not p["underutilization_ratio"] < p["overload_ratio"]:
        raise ValueError("Invalid fraction/occupancy thresholds")
    for key in ("passenger_impact_bands", "severity_bands"):
        bands = p.get(key)
        if not isinstance(bands, list) or len(bands) != 3 or not all(finite(x) and x > 0 for x in bands) or not bands[0] < bands[1] < bands[2]:
            raise ValueError(f"{key} requires three increasing positive thresholds")
    return p


def priority(passengers, severity, policy):
    """Four levels: floor((impact band + severity band)/2), each band in 0..3."""
    if not finite(passengers) or passengers < 0 or not finite(severity) or severity < 0:
        raise ValueError("Priority requires finite nonnegative measured inputs")
    impact_band = sum(passengers >= x for x in policy["passenger_impact_bands"])
    severity_band = sum(severity >= x for x in policy["severity_bands"])
    return {"priority": ("LOW", "MEDIUM", "HIGH", "CRITICAL")[(impact_band+severity_band)//2],
            "passenger_impact": passengers, "severity_ratio": severity,
            "impact_band": impact_band, "severity_band": severity_band,
            "rule": "floor((passenger impact band + operational severity band)/2)"}


def _rule(rule, record, policy):
    metrics = record["metrics"]
    for key in RULES[rule][1]:
        require_number(metrics, key)
    minimum = policy["minimum_days"]
    if rule == "increase_frequency":
        severity = metrics["mean_occupancy_ratio"] / policy["overload_ratio"]
        return severity > 1 and metrics["overload_days"] >= minimum, severity
    if rule == "reduce_underutilized_service":
        for key in ("social_service_required", "coverage_preserved"):
            if not isinstance(record.get(key), bool):
                raise NotReady(f"Missing service protection evidence: {key}")
        load = metrics["mean_occupancy_ratio"]
        severity = 1 + max(0, policy["underutilization_ratio"]-load)/policy["underutilization_ratio"]
        return (load < policy["underutilization_ratio"] and metrics["underutilized_days"] >= minimum
                and not record["social_service_required"] and record["coverage_preserved"]), severity
    if rule == "modify_schedule":
        severity = metrics["late_fraction"] / policy["late_fraction"]
        return severity >= 1 and metrics["late_days"] >= minimum, severity
    if rule == "higher_capacity_vehicle":
        if not isinstance(record.get("higher_capacity_vehicle_feasible"), bool):
            raise NotReady("Missing higher-capacity vehicle feasibility evidence")
        severity = metrics["peak_occupancy_ratio"] / policy["overload_ratio"]
        return severity > 1 and metrics["overload_days"] >= minimum and record["higher_capacity_vehicle_feasible"], severity
    if rule == "investigate_bottleneck":
        if not record["entity_ids"].get("stop_id"):
            raise NotReady("Bottleneck investigation requires an affected stop")
        severity = metrics["mean_delay_sec"] / policy["bottleneck_delay_sec"]
        return severity >= 1 and metrics["late_days"] >= minimum, severity
    if rule == "adjust_departure_time":
        shift = metrics.get("evaluated_shift_minutes")
        if not finite(shift) or shift == 0 or not record["entity_ids"].get("trip_id"):
            raise NotReady("Departure adjustment needs a trip and evaluated nonzero shift")
        before, after = metrics["current_expected_wait_minutes"], metrics["alternative_expected_wait_minutes"]
        # A zero current wait cannot support a claimed reduction; no division fallback.
        severity = 1 + (before-after)/before if before > 0 else 0
        return before > after, severity
    severity = metrics["headway_cv"] / policy["headway_cv"]
    return severity >= 1 and metrics["irregular_headway_days"] >= minimum, severity


def generate(package, cases, policy, *, generated_at=None):
    validate_policy(policy)
    result = package.result_base("recommendations", generated_at)
    result.update(recommendations=[], rule_checks=[], policy=policy,
                  provenance=provenance({"manifest": package.manifest_sha256, "cases": cases}, policy))
    if not cases:
        raise NotReady("No recommendation evidence cases supplied")
    emitted = set()
    for case in cases:
        record, source = package.resolve(case["evidence"])
        if not isinstance(record, dict):
            raise NotReady("Recommendation evidence must contain a case object")
        if not isinstance(record.get("entity_ids"), dict) or not record["entity_ids"].get("route_id"):
            raise NotReady("Recommendation evidence requires a route and applicable entity IDs")
        if record.get("evidence_kind") not in {"OBSERVED", "MODEL_ESTIMATE"}:
            raise NotReady("Declare observed or model-estimated analytical evidence")
        window = record.get("window", {})
        from .contracts import timestamp
        if not isinstance(window, dict) or not window.get("start") or not window.get("end") or timestamp(window["start"]) >= timestamp(window["end"]):
            raise NotReady("A valid source evidence window is required")
        metrics = record.get("metrics", {})
        samples = require_number(metrics, "sample_count", positive=True)
        if int(samples) != samples:
            raise ValueError("Sample count must be an integer")
        passengers = require_number(metrics, "affected_passengers")
        if not case.get("rules") or any(rule not in RULES for rule in case["rules"]):
            raise ValueError("Declare supported recommendation rules for each case")
        for rule in case["rules"]:
            check = {"rule_id": rule, "entity_ids": record["entity_ids"], "source": source}
            try:
                if samples < policy["minimum_samples"]:
                    raise NotReady("Insufficient evidence sample count")
                for name, value in metrics.items():
                    if name.endswith("_days") and (not finite(value) or int(value) != value or not 0 <= value <= samples):
                        raise ValueError("Recurrence day counts must be integers within sample count")
                if "late_fraction" in metrics and not 0 <= metrics["late_fraction"] <= 1:
                    raise ValueError("late_fraction must be in [0,1]")
                triggered, severity = _rule(rule, record, policy)
                check["status"] = "TRIGGERED" if triggered else "NO_ACTION"
                if triggered:
                    identity = digest({"rule": rule, "source": source, "entity": record["entity_ids"], "policy": policy})
                    if identity not in emitted:
                        emitted.add(identity)
                        relevant = {k: metrics[k] for k in ("sample_count", "affected_passengers", *RULES[rule][1])}
                        if rule == "adjust_departure_time":
                            relevant["evaluated_shift_minutes"] = metrics["evaluated_shift_minutes"]
                        result["recommendations"].append({
                            "recommendation_id": identity, "rule_id": rule, "action": RULES[rule][0],
                            "entity_ids": record["entity_ids"], "reason": f"Rule {rule} met the configured evidence thresholds: {relevant}",
                            "supporting_metrics": relevant, "supporting_evidence": record,
                            "source_analytical_artifact": source, "window": window,
                            **priority(passengers, severity, policy),
                            "confidence": {"status": "EVIDENCE_SUPPORTED", "probability": None,
                                           "note": "Rule support, not a calibrated probability of operational benefit"},
                            "evidence_status": result["evidence_status"], "evidence_kind": record["evidence_kind"],
                            "generated_at": result["generated_at"], "provenance": result["provenance"]})
            except NotReady as exc:
                check.update(status="NOT_READY", reason=str(exc))
                result["status"] = "NOT_READY"
            result["rule_checks"].append(check)
    result["recommendations"].sort(key=lambda r: (("CRITICAL", "HIGH", "MEDIUM", "LOW").index(r["priority"]), r["recommendation_id"]))
    return result
