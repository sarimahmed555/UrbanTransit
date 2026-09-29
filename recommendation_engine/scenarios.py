"""Deterministic fluid-demand queue estimates for one route/direction/service window."""
from copy import deepcopy
import math
from .contracts import NotReady, finite, provenance, require_number

SCENARIOS = {
    "increase_frequency": {"trip_count"}, "decrease_frequency": {"trip_count"},
    "add_vehicle": set(), "change_vehicle_capacity": {"capacity"},
    "shift_trip_start_time": {"shift_minutes"}, "remove_low_demand_trip": set(),
    "add_new_stop": {"added_cycle_minutes", "additional_demand_profile"},
    "increase_predicted_demand": {"factor"},
}
ASSUMPTIONS = [
    "ESTIMATE: fluid expected passenger arrivals are uniform within each certified demand interval.",
    "One route/direction and one common boarding point/critical segment; each passenger uses one departure.",
    "FIFO boarding, hard vehicle capacity, no abandonment, no alighting/turnover within the modeled segment.",
    "The queue starts empty as explicitly required by the baseline contract.",
    "Waiting time covers served passengers within this window; unserved passengers are reported separately.",
    "Demand stays fixed except for explicit demand/new-stop changes; no induced demand or network redistribution.",
]
LIMITATIONS = [
    "All baseline and scenario simulation metrics are ESTIMATES, not observed operational facts.",
    "No fleet duty, road-network, accessibility, labor, budget or dispatch feasibility optimization.",
    "Overcrowding risk is the fraction of departures with queued demand above the configured capacity ratio; not a calibrated probability.",
    "This common-segment model cannot estimate route-wide OD flows, unique network coverage or stop-to-stop occupancy.",
    "Uniform arrivals and empty initial queue can understate actual waits; arrivals after the last departure remain unserved.",
]


def _profile(profile, window):
    if not isinstance(profile, list) or not profile:
        raise NotReady("A complete certified demand profile is required")
    previous = 0.0
    for interval in profile:
        start = require_number(interval, "start_minute")
        end = require_number(interval, "end_minute", positive=True)
        require_number(interval, "passengers")
        if not math.isclose(start, previous, abs_tol=1e-9) or not start < end <= window:
            raise ValueError("Demand intervals must cover the window without gaps/overlaps")
        previous = end
    if not math.isclose(previous, window, abs_tol=1e-9):
        raise ValueError("Demand profile does not cover the whole service window")


def validate_state(state):
    if not isinstance(state, dict):
        raise NotReady("Baseline state must be an analytical object")
    for key in ("entity_ids", "window_start", "trips", "stop_ids", "demand_profile"):
        if key not in state:
            raise NotReady(f"Baseline state missing: {key}")
    if not isinstance(state["entity_ids"], dict) or not state["entity_ids"].get("route_id") or "direction_id" not in state["entity_ids"]:
        raise NotReady("Scenario baseline requires route and direction IDs")
    if state.get("demand_kind") not in {"OBSERVED", "MODEL_FORECAST"}:
        raise NotReady("Demand must identify observed or model-forecast evidence")
    if state["demand_kind"] == "MODEL_FORECAST" and not state.get("demand_model_version"):
        raise NotReady("Forecast demand model version is missing")
    from .contracts import timestamp
    timestamp(state["window_start"])
    window = require_number(state, "window_minutes", positive=True)
    require_number(state, "cycle_minutes", positive=True)
    require_number(state, "crowding_threshold_ratio", positive=True)
    fleet = require_number(state, "vehicle_count", positive=True)
    if int(fleet) != fleet:
        raise ValueError("Vehicle count must be an integer")
    if "initial_queue" not in state or not finite(state["initial_queue"]):
        raise NotReady("Initial queue must be explicitly known")
    if state["initial_queue"] != 0:
        raise NotReady("This scenario model requires a certified empty initial queue")
    if not isinstance(state["stop_ids"], list) or not state["stop_ids"] or len(set(state["stop_ids"])) != len(state["stop_ids"]):
        raise ValueError("Unique baseline stop IDs are required")
    if not isinstance(state["trips"], list) or not state["trips"]:
        raise NotReady("No baseline departures")
    ids, times = set(), set()
    for trip in state["trips"]:
        if not isinstance(trip, dict):
            raise ValueError("Each trip must be a JSON object")
        if not trip.get("trip_id") or trip["trip_id"] in ids:
            raise ValueError("Trips must have unique nonempty IDs")
        time = require_number(trip, "departure_minute")
        require_number(trip, "capacity", positive=True)
        if time > window or time in times:
            raise ValueError("Departure must be unique and within the service window")
        ids.add(trip["trip_id"])
        times.add(time)
    _profile(state["demand_profile"], window)
    return state


def simulate(state):
    """Integrate FIFO cohorts analytically; no random arrivals or hardcoded outcomes."""
    validate_state(state)
    queue, previous, boarded, wait_sum = [], 0.0, 0.0, 0.0
    events = []
    for trip in sorted(state["trips"], key=lambda t: (t["departure_minute"], t["trip_id"])):
        departure = trip["departure_minute"]
        for interval in state["demand_profile"]:
            left, right = max(previous, interval["start_minute"]), min(departure, interval["end_minute"])
            if right > left and interval["passengers"] > 0:
                rate = interval["passengers"]/(interval["end_minute"]-interval["start_minute"])
                queue.append([left, right, rate])
        waiting = sum((end-start)*rate for start, end, rate in queue)
        room, served = trip["capacity"], 0.0
        while queue and room > 1e-10:
            start, end, rate = queue[0]
            take = min(room, (end-start)*rate)
            consumed_until = start + take/rate
            wait_sum += take * (departure - (start+consumed_until)/2)
            room -= take
            served += take
            if consumed_until >= end-1e-10:
                queue.pop(0)
            else:
                queue[0][0] = consumed_until
        boarded += served
        events.append({"trip_id": trip["trip_id"], "departure_minute": departure,
                       "estimated_passenger_load": served, "estimated_occupancy_ratio": served/trip["capacity"],
                       "queued_demand": waiting, "unconstrained_load_ratio": waiting/trip["capacity"],
                       "overcrowding_pressure": waiting > trip["capacity"]*state["crowding_threshold_ratio"]})
        previous = departure
    demand = sum(i["passengers"] for i in state["demand_profile"])
    capacity = sum(t["capacity"] for t in state["trips"])
    count = len(events)
    metrics = {"occupancy_ratio": boarded/capacity, "passenger_load": boarded/count,
               "waiting_time_minutes": wait_sum/boarded if boarded > 0 else None,
               "route_capacity": capacity, "demand_coverage": boarded/demand if demand > 0 else None,
               "overcrowding_risk": sum(e["overcrowding_pressure"] for e in events)/count,
               "peak_unconstrained_load_ratio": max(e["unconstrained_load_ratio"] for e in events),
               "passengers_served": boarded, "passengers_unserved": max(0.0, demand-boarded),
               "predicted_demand": demand, "trip_count": count,
               "vehicle_count": state["vehicle_count"]}
    return metrics, events


def _count(value):
    if not finite(value) or int(value) != value or value < 1 or value > 10000:
        raise ValueError("Proposed trip count must be an integer between 1 and 10000")
    return int(value)


def _respace(state, count):
    capacities = {t["capacity"] for t in state["trips"]}
    if len(capacities) != 1:
        raise NotReady("Frequency/cycle scenarios require a homogeneous certified vehicle capacity")
    capacity = next(iter(capacities))
    state["trips"] = [{"trip_id": f"ESTIMATE-departure-{i+1}", "departure_minute": (i+1)*state["window_minutes"]/count,
                       "capacity": capacity} for i in range(count)]


def apply_scenario(state, request):
    kind = request.get("scenario_type")
    if kind not in SCENARIOS:
        raise ValueError("Unknown scenario type")
    change = request.get("proposed_changes")
    if not isinstance(change, dict) or set(change) != SCENARIOS[kind]:
        raise ValueError(f"{kind} requires exactly these proposed_changes: {sorted(SCENARIOS[kind])}")
    ids = request.get("entity_ids", {})
    if not isinstance(ids, dict):
        raise ValueError("Scenario entity_ids must be a JSON object")
    if ids.get("route_id") != state["entity_ids"]["route_id"] or ids.get("direction_id") != state["entity_ids"]["direction_id"]:
        raise ValueError("Request route/direction does not match baseline")
    allowed_ids = {"route_id", "direction_id"} | ({"trip_id"} if kind in {"shift_trip_start_time", "remove_low_demand_trip"} else set()) | ({"stop_id"} if kind == "add_new_stop" else set())
    if set(ids) != allowed_ids:
        raise ValueError(f"Scenario entity IDs must be exactly {sorted(allowed_ids)}")
    proposed = deepcopy(state)
    assumptions = []
    count = len(state["trips"])
    if kind in {"increase_frequency", "decrease_frequency"}:
        target = _count(change["trip_count"])
        if (kind == "increase_frequency" and target <= count) or (kind == "decrease_frequency" and target >= count):
            raise ValueError("Trip count must change in the requested direction")
        _respace(proposed, target)
        assumptions.append("Departures are evenly re-spaced over the same window; extra fleet feasibility is not assumed.")
    elif kind == "add_vehicle":
        extra = math.floor(state["window_minutes"]/state["cycle_minutes"])
        if extra < 1:
            raise NotReady("No full additional cycle fits the window; a detailed vehicle duty model is required")
        proposed["vehicle_count"] += 1
        _respace(proposed, _count(count+extra))
        assumptions.append("One added vehicle supplies floor(window_minutes/cycle_minutes) extra departures; full-cycle approximation, evenly spaced.")
    elif kind == "change_vehicle_capacity":
        capacity = require_number(change, "capacity", positive=True)
        for trip in proposed["trips"]:
            trip["capacity"] = capacity
        assumptions.append("The proposed capacity applies to every departure; accessibility and vehicle feasibility require separate review.")
    elif kind in {"shift_trip_start_time", "remove_low_demand_trip"}:
        trip = next((t for t in proposed["trips"] if t["trip_id"] == ids["trip_id"]), None)
        if trip is None:
            raise ValueError("Unknown scenario trip")
        if kind == "shift_trip_start_time":
            shift = change["shift_minutes"]
            if not finite(shift) or shift == 0:
                raise ValueError("Shift must be finite and nonzero")
            trip["departure_minute"] += shift
            assumptions.append("Only the selected departure shifts; observed demand arrival timing remains fixed.")
        else:
            if not isinstance(state.get("social_service_required"), bool) or not isinstance(state.get("removal_eligible_trip_ids"), list):
                raise NotReady("Certified low-demand and service-coverage removal eligibility is missing")
            if state["social_service_required"] or ids["trip_id"] not in state["removal_eligible_trip_ids"]:
                raise ValueError("Trip removal is not supported by the certified coverage/low-demand assessment")
            if count <= 1:
                raise ValueError("Cannot remove the only departure")
            proposed["trips"].remove(trip)
            assumptions.append("Demand from the removed trip remains in the same FIFO queue; no demand is silently discarded.")
    elif kind == "add_new_stop":
        stop = ids["stop_id"]
        if not isinstance(stop, str) or not stop or stop in state["stop_ids"]:
            raise ValueError("New stop must have a distinct nonempty ID")
        added = require_number(change, "added_cycle_minutes", positive=True)
        profile = change["additional_demand_profile"]
        _profile(profile, state["window_minutes"])
        if [(i["start_minute"], i["end_minute"]) for i in profile] != [(i["start_minute"], i["end_minute"]) for i in state["demand_profile"]]:
            raise ValueError("Additional stop demand must use the same explicit time grid")
        proposed["cycle_minutes"] += added
        target = math.floor(count * state["cycle_minutes"]/proposed["cycle_minutes"])
        if target < 1:
            raise NotReady("Added stop leaves no full-cycle departure under this approximation")
        _respace(proposed, target)
        for existing, extra in zip(proposed["demand_profile"], profile):
            existing["passengers"] += extra["passengers"]
        proposed["stop_ids"].append(stop)
        assumptions.append("Added stop demand and cycle minutes are explicit user scenario assumptions, not observed facts.")
        assumptions.append("Fixed service resources: departures=floor(original departures * original cycle / proposed cycle); new demand enters the common-segment queue.")
    else:
        if state["demand_kind"] != "MODEL_FORECAST":
            raise NotReady("Increasing predicted demand requires a source model forecast")
        factor = require_number(change, "factor", positive=True)
        if factor <= 1:
            raise ValueError("Predicted-demand multiplier must exceed 1")
        for interval in proposed["demand_profile"]:
            interval["passengers"] *= factor
        assumptions.append("Demand increases proportionally in every interval; the multiplier is a user scenario assumption.")
    validate_state(proposed)
    return proposed, assumptions


def analyze(package, request, *, generated_at=None):
    state, source = package.resolve(request["baseline_evidence"])
    validate_state(state)
    proposed, extra_assumptions = apply_scenario(state, request)
    baseline, baseline_events = simulate(state)
    estimated, scenario_events = simulate(proposed)
    result = package.result_base("what_if", generated_at)
    result.update({"result_type": "ESTIMATE", "scenario_type": request["scenario_type"],
                   "entity_ids": request["entity_ids"], "original_values": state,
                   "proposed_changes": request["proposed_changes"], "proposed_values": proposed,
                   "assumptions": ASSUMPTIONS+extra_assumptions,
                   "baseline_metrics": baseline, "estimated_metrics": estimated,
                   "baseline_metrics_type": "ESTIMATE", "estimated_metrics_type": "ESTIMATE",
                   "deltas": {k: estimated[k]-v if v is not None and estimated[k] is not None else None for k, v in baseline.items()},
                   "baseline_departure_estimates": baseline_events, "scenario_departure_estimates": scenario_events,
                   "evidence_source": source, "limitations": LIMITATIONS,
                   "provenance": provenance({"source": source, "request": request}, {"model": "FIFO-fluid-common-segment-v1"})})
    return result
