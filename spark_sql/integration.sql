-- Reference SQL for review/debugging. The Python entry point applies the same
-- as-of predicates and writes independently named projections.
CREATE OR REPLACE TEMP VIEW trip_context AS
SELECT t.*, r.route_code, r.mode, p.direction_id,
       c.context_event_id, c.event_type, c.value_available_at AS context_available_at
FROM uti_trips t
LEFT JOIN uti_routes r ON t.route_id = r.route_id
LEFT JOIN uti_route_patterns p ON t.pattern_id = p.pattern_id
LEFT JOIN uti_context_events c
  ON t.route_id = c.route_id
 AND t.scheduled_start_utc BETWEEN c.starts_at_utc AND c.ends_at_utc
 AND c.value_available_at <= t.scheduled_start_utc;

CREATE OR REPLACE TEMP VIEW trip_operations AS
SELECT t.operational_departure_id, t.trip_id, t.service_date, t.route_id,
       e.stop_event_id, e.route_stop_id, e.stop_sequence,
       e.actual_arrival_utc, e.actual_departure_utc,
       d.arrival_delay_sec, d.departure_delay_sec,
       pc.boardings, pc.alightings, pc.onboard_departure
FROM uti_trips t
LEFT JOIN uti_trip_stop_events e ON t.trip_id = e.trip_id
LEFT JOIN uti_delays d ON e.stop_event_id = d.stop_event_id
LEFT JOIN uti_passenger_counts pc ON e.stop_event_id = pc.stop_event_id;

CREATE OR REPLACE TEMP VIEW demand_operations AS
SELECT j.journey_id, j.passenger_id, j.trip_id, j.service_date,
       j.origin_route_stop_id, j.destination_route_stop_id,
       q.request_id, q.request_available_at_utc,
       t.operational_departure_id, t.route_id
FROM uti_passenger_journeys j
LEFT JOIN uti_demand_requests q ON j.request_id = q.request_id
LEFT JOIN uti_trips t ON j.trip_id = t.trip_id;
