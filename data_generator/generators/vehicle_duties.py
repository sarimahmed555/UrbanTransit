"""Deterministic fleet reservations; no fact tables or external services.

Reserve the full planned/observed envelope for both replacement vehicles.
This conservative policy keeps the planned vehicle free through the original
trip end and positions the replacement before the trip begins. Superseded
plans are alternatives, never additional concurrent duties. The project buffer
is 60 minutes (15 turnaround + 45 synthetic-network deadhead), not an SRS rule.
"""
from datetime import date, timedelta

from .common import parse_utc, utc_from_local

BUFFER_SECONDS = 3600


class VehicleDutyAllocator:
    def __init__(self, vehicles, buffer_seconds=BUFFER_SECONDS):
        self.vehicles = sorted(vehicles, key=lambda row: row['vehicle_id'])
        self.buffer = timedelta(seconds=buffer_seconds)
        self.reservations = {}
        self.day = None
        self.peak_reservations = 0
        self.cursor = 0

    def allocate(self, service_date, start, end, *, exclude=(), different_capacity=None):
        start, end = parse_utc(start), parse_utc(end)
        if end < start:
            raise ValueError('duty ends before it starts')
        if self.day is not None and service_date < self.day:
            raise ValueError('allocate service dates in chronological order')
        if self.day != service_date:
            midnight = parse_utc(utc_from_local(service_date, 0))
            self.reservations = {key: [(a, b) for a, b in intervals if b + self.buffer > midnight]
                                 for key, intervals in self.reservations.items()}
            self.day = service_date
            self.eligible = [vehicle for vehicle in self.vehicles
                if vehicle['operational_status'] == 'AVAILABLE'
                and date.fromisoformat(vehicle['commissioned_on']) <= service_date
                and (not vehicle.get('retired_on') or service_date < date.fromisoformat(vehicle['retired_on']))]
        candidates = self.eligible[self.cursor:] + self.eligible[:self.cursor]
        for vehicle in candidates:
            key = vehicle['vehicle_id']
            if key in exclude or vehicle['nominal_capacity'] == different_capacity:
                continue
            intervals = self.reservations.setdefault(key, [])
            if any(start < b + self.buffer and a < end + self.buffer for a, b in intervals):
                continue
            intervals.append((start, end))
            self.peak_reservations = max(self.peak_reservations, sum(bool(v) for v in self.reservations.values()))
            self.cursor = (self.vehicles.index(vehicle) + 1) % len(self.vehicles)
            return vehicle
        raise RuntimeError(f'fleet infeasible: no eligible vehicle for {service_date} {start}–{end}; '
                           f'fleet={len(self.vehicles)}, buffer={self.buffer.total_seconds()} seconds')


def reserve_trip(ctx, spec, network, service, vehicles):
    """Shared production/preflight path, including actual timing deviations."""
    from .common import add_seconds

    start = utc_from_local(spec.service_date, int(spec.schedule['departure_offset_sec']))
    times = service.stop_times_by_schedule[spec.schedule['schedule_id']]
    end = add_seconds(start, int(times[-1]['departure_offset_sec']))
    if not spec.cancelled:
        # Timing formula bounds: earliest is -75 early -90 bunching seconds;
        # initial delay <=450 +180 context, then <=12 sec/stop, bottleneck
        # dwell <=175 and replacement <=390. Reserve a wider envelope.
        start = add_seconds(start, -300)
        end = add_seconds(end, 1800 + 12 * len(times))
    spec.duty_envelope_start, spec.duty_envelope_end = start, end
    if not hasattr(ctx, 'vehicle_duties'):
        ctx.vehicle_duties = VehicleDutyAllocator(vehicles)
    incoming = ctx.vehicle_duties.allocate(spec.service_date, start, end)
    outgoing = None
    if spec.replacement and not spec.cancelled:
        outgoing = ctx.vehicle_duties.allocate(spec.service_date, start, end,
                                               exclude=(incoming['vehicle_id'],),
                                               different_capacity=incoming['nominal_capacity'])
    spec.allocated_replacement_id = outgoing['vehicle_id'] if outgoing else ''
    return incoming['vehicle_id']
