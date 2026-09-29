"""Measured summaries over certified features; no joins, lags or label creation."""
from collections import defaultdict
from math import sqrt
import random
from .contracts import finite


def silhouette(features, labels, *, seed, sample_limit=500):
    """Euclidean silhouette on a reproducible bounded sample, including singleton=0."""
    if len(features) != len(labels) or not features:
        raise ValueError("Aligned nonempty clustering cases required")
    ids = sorted(random.Random(seed).sample(range(len(labels)), min(len(labels), sample_limit)))
    groups = defaultdict(list)
    for i in ids:
        groups[labels[i]].append(i)
    if not 2 <= len(groups) < len(ids):
        return {"silhouette": None, "reason": "Requires 2..n-1 occupied clusters in sample", "sample_count": len(ids)}
    def distance(i, j):
        return sqrt(sum((a-b)**2 for a, b in zip(features[i], features[j])))
    scores = []
    for i in ids:
        own = [j for j in groups[labels[i]] if j != i]
        if not own:
            scores.append(0.0)
            continue
        a = sum(distance(i, j) for j in own) / len(own)
        b = min(sum(distance(i, j) for j in members) / len(members) for k, members in groups.items() if k != labels[i])
        scores.append((b-a) / max(a, b) if max(a, b) else 0.0)
    return {"silhouette": sum(scores)/len(scores), "sample_count": len(ids),
            "distance": "euclidean on independently train-standardized features", "sample_seed": seed}


def cluster_interpretation(rows, assignments, feature_names):
    groups = defaultdict(list)
    for row, label in zip(rows, assignments):
        groups[int(label)].append(row)
    return [{"cluster_id": label, "sample_count": len(members),
             "feature_means_original_units": {f: sum(r[f] for r in members)/len(members) for f in feature_names},
             "route_ids": sorted({str(r["route_id"]) for r in members if r.get("route_id") is not None}),
             "interpretation": "Descriptive measured feature means; no predefined route archetype"}
            for label, members in sorted(groups.items())]


def service_analytics(rows, config):
    """Full stop/service denominator must come from FE, not the positive-only delays table.

    All limits are supplied by the task contract. Null/unknown attribution is excluded
    only from the affected dimension and is counted explicitly.
    """
    required = {"early_tolerance_sec", "late_tolerance_sec", "recurrence_min_days", "occupancy_bands"}
    if required - config.keys():
        raise ValueError(f"Missing analytics policy: {sorted(required - config.keys())}")
    early, late = config["early_tolerance_sec"], config["late_tolerance_sec"]
    bands = config["occupancy_bands"]
    if not finite(early) or not finite(late) or early < 0 or late < 0 or config["recurrence_min_days"] < 2:
        raise ValueError("Invalid delay/recurrence policy")
    if len(bands) != 3 or not all(finite(x) for x in bands) or not 0 < bands[0] < bands[1] < bands[2]:
        raise ValueError("Supply increasing low/moderate/high occupancy ratio boundaries")
    eligible = [r for r in rows if r.get("delay_status") == "AVAILABLE" and finite(r.get("signed_departure_delay_sec"))]
    occupancy = [r for r in rows if r.get("occupancy_status") == "AVAILABLE" and finite(r.get("capacity_snapshot"))
                 and r["capacity_snapshot"] > 0 and finite(r.get("capacity_utilization")) and r["capacity_utilization"] >= 0]
    def summarize(group):
        delays = [r["signed_departure_delay_sec"] for r in group]
        mean = sum(delays)/len(delays)
        late_rows = [r for r in group if r["signed_departure_delay_sec"] > late]
        days = sorted({r["service_date"] for r in late_rows})
        return {"sample_count": len(group), "mean_signed_delay_sec": mean,
                "delay_stddev_sec": sqrt(sum((d-mean)**2 for d in delays)/len(delays)),
                "max_delay_sec": max(delays), "late_count": len(late_rows), "late_fraction": len(late_rows)/len(group),
                "early_count": sum(d < -early for d in delays),
                "on_time_fraction": sum(-early <= d <= late for d in delays)/len(group),
                "late_service_days": days, "recurring_delay": len(days) >= config["recurrence_min_days"]}
    dimensions = {"route": ["route_id"], "trip": ["trip_id"], "stop": ["stop_id"],
                  "vehicle": ["departure_vehicle_id"], "time": ["event_hour"], "day": ["weekday"],
                  "direction": ["route_id", "direction_id"], "distance": ["distance_band"],
                  "recurring_route_time": ["route_id", "direction_id", "weekday", "event_hour"]}
    grouped = {}
    for name, columns in dimensions.items():
        groups, excluded = defaultdict(list), 0
        for row in eligible:
            if any(row.get(c) is None for c in columns) or (name == "vehicle" and row.get("departure_assignment_status") != "KNOWN"):
                excluded += 1
                continue
            groups[tuple(str(row[c]) for c in columns)].append(row)
        grouped[name] = {"status": "MEASURED" if groups else "NOT_READY", "excluded_count": excluded,
                         "results": [{"segment": dict(zip(columns, key)), **summarize(group)} for key, group in sorted(groups.items())]}
    crowd_groups = defaultdict(list)
    missing_crowd_segment = 0
    for row in occupancy:
        if any(row.get(c) is None for c in ("route_id", "direction_id", "weekday", "event_hour")):
            missing_crowd_segment += 1
            continue
        key = tuple(str(row.get(c)) for c in ("route_id", "direction_id", "weekday", "event_hour"))
        crowd_groups[key].append(row)
    crowd = []
    for key, group in sorted(crowd_groups.items()):
        overloaded = [r for r in group if r["capacity_utilization"] > bands[2]]
        days = sorted({r["service_date"] for r in overloaded})
        crowd.append({"segment": dict(zip(("route_id", "direction_id", "weekday", "event_hour"), key)),
                      "sample_count": len(group), "overloaded_count": len(overloaded), "overloaded_days": days,
                      "persistent": len(days) >= config["recurrence_min_days"],
                      "underutilized_count": sum(r["capacity_utilization"] <= bands[0] for r in group)})
    occupancy_counts = {name: 0 for name in ("Low", "Moderate", "High", "Overcrowded")}
    for row in occupancy:
        index = sum(row["capacity_utilization"] > boundary for boundary in bands)
        occupancy_counts[list(occupancy_counts)[index]] += 1
    return {"policy": config, "sample_count": len(rows), "delay_eligible_count": len(eligible),
            "delay_unavailable_count": len(rows)-len(eligible), "occupancy_eligible_count": len(occupancy),
            "occupancy_unavailable_count": len(rows)-len(occupancy),
            "delay_summary": summarize(eligible) if eligible else None,
            "delay_segments": grouped, "occupancy_bands": occupancy_counts if occupancy else None,
            "persistent_crowding": crowd,
            "crowding_segment_unavailable_count": missing_crowd_segment,
            "service_status_counts": {s: sum((r.get("service_status") or "UNKNOWN") == s for r in rows) for s in sorted({r.get("service_status") or "UNKNOWN" for r in rows})},
            "denominator": "certified unique service/stop cases; cancellations reported separately; punctuality uses available observed delay only"}
