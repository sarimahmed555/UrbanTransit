"""Exact integer apportionment of movements; no passenger/fact rows are built."""
from .common import utc_from_local


def apportion(total, weights, keys):
    """Largest remainders with stable business-key ties, using integer arithmetic."""
    if total < 0 or any(w < 0 for w in weights):
        raise ValueError('negative movement allocation')
    denominator = sum(weights)
    if not denominator:
        if total:
            raise ValueError('positive budget without boarding opportunities')
        return [0] * len(weights)
    result = [total * w // denominator for w in weights]
    remainder_order = sorted(range(len(weights)),
                             key=lambda i: (-(total * weights[i] % denominator), keys[i]))
    for i in remainder_order[:total - sum(result)]:
        result[i] += 1
    return result


def boarding_weights(spec, base, sequences):
    return [base if i == sequences[0] else max(0, int(round(base * (0.18 + ((i + spec.index) % 3) * 0.05))))
            for i in sequences[:-1]]


def boarding_plan(spec, base, sequences):
    weights = boarding_weights(spec, base, sequences)
    if spec.boarding_budget is not None:
        weights = apportion(spec.boarding_budget, weights, sequences[:-1])
    return dict(zip(sequences, weights + [0]))


def metadata_demand(ctx, spec, network, service):
    from .stop_events import demand_base
    n = len(service.stop_times_by_schedule[spec.schedule['schedule_id']])
    sequences = [i for i in range(1, n + 1)
                 if not (spec.incomplete and i == n) and not (spec.skipped_stop and i == 2)]
    row = {'scheduled_start_utc': utc_from_local(spec.service_date, int(spec.schedule['departure_offset_sec'])),
           'route_id': spec.pattern['route_id']}
    base = demand_base(
        ctx, spec, row,
        network.route_stops_by_pattern[spec.pattern['pattern_id']],
        record_scenarios=False,
        event_applies_override=spec.event_id is not None,
    )
    return base, sequences


def allocate_movements(ctx, specs, network, service):
    """Protect rare/scenario extremes; proportionally scale remaining trip weights.

    Every operated departure keeps at least one movement, so intentional missing
    request/ticket fixtures survive. Overcrowd, low-demand and replacement flows
    retain their original budgets. This preserves G1 and capacity extremes.
    """
    target = ctx.config.target_scale['passenger_journeys']
    flexible, weights, fixed = [], [], 0
    for spec in specs:
        if spec.cancelled:
            spec.boarding_budget = 0
            continue
        base, sequences = metadata_demand(ctx, spec, network, service)
        weight = sum(boarding_weights(spec, base, sequences))
        if weight < 1:
            raise ValueError('operated trip has no feasible boarding opportunities')
        if spec.overcrowd or spec.low_demand or spec.replacement:
            spec.boarding_budget = weight
            fixed += weight
        else:
            flexible.append(spec)
            weights.append(weight)
    available = target - fixed - len(flexible)
    if available < 0:
        raise ValueError('movement target cannot preserve protected scenarios and minimum service demand')
    allocations = apportion(available, weights, [spec.operational_id for spec in flexible])
    for spec, amount in zip(flexible, allocations):
        spec.boarding_budget = amount + 1
    if sum(spec.boarding_budget for spec in specs) != target:
        raise ValueError('movement budget does not reconcile')
    return {'canonical_movements': target, 'protected_movements': fixed,
            'flexible_trip_count': len(flexible),
            'rule': 'protected scenario budgets plus largest-remainder weighted trip and stop allocation'}
