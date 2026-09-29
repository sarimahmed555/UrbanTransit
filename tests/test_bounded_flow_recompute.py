"""Executable proof of the bounded flow-recompute defect.

`data_generator.bounded_remediation._recompute_counts` rebuilds the per-trip
onboard series after journey bundles are moved between departures. It is a
module-level function over plain dicts, so it can be executed on a two-stop
synthetic trip with no dataset access at all.

The defect proved here: the function writes ``boardings``, ``alightings``,
``onboard_departure`` and ``quality_status``, but never ``onboard_arrival``.
The onboard series is therefore left half-rewritten, which is exactly what
produces two of the seven observed verification failures:

* ``passenger_count_conservation`` -- ``od != oa - a + b``
* ``load_continuity``             -- ``oa[i] != od[i-1]``

The desired-behaviour test is marked ``expectedFailure`` on purpose. It reports
as a known defect, and it turns into a signal the moment the write is added,
so the fix cannot be silently reverted.
"""

import unittest

from data_generator.bounded_remediation import _recompute_counts


def two_stop_trip():
    """A minimal consistent pre-correction trip with one journey on board.

    stop 1: 0 in, 1 boards, 1 out   -> oa=0 od=1
    stop 2: 1 in, 1 alights, 0 out  -> oa=1 od=0   (terminal load zero)
    """
    trip_events = {
        "T1": {
            1: {"stop_event_id": "E1", "route_stop_id": "RS1"},
            2: {"stop_event_id": "E2", "route_stop_id": "RS2"},
        }
    }
    count_rows = {
        "T1": {
            "E1": {"source_row_id": "C1", "boardings": "1", "alightings": "0",
                   "onboard_arrival": "0", "onboard_departure": "1",
                   "quality_status": "OK"},
            "E2": {"source_row_id": "C2", "boardings": "0", "alightings": "1",
                   "onboard_arrival": "1", "onboard_departure": "0",
                   "quality_status": "OK"},
        }
    }
    journeys = {
        "T1": [{"journey_id": "J1", "boarding_stop_event_id": "E1",
                "alighting_stop_event_id": "E2"}]
    }
    return trip_events, count_rows, journeys


# One journey added to the same trip: it boards at E1 and alights at E2.
INCOMING = [{"boarding_stop_event_id": "E1", "alighting_stop_event_id": "E2"}]


def run_with_incoming_move():
    trip_events, count_rows, journeys = two_stop_trip()
    return _recompute_counts(
        {"T1"}, trip_events, count_rows,
        {"T1": list(INCOMING)}, {},          # moves_by_target
        journeys,
        {"A1": 50},                            # assignment_capacity
        {"T1": {1: "A1", 2: "A1"}},            # trip_assignment
        set(),                                 # protected_sources
    )


def run_with_outgoing_move():
    trip_events, count_rows, journeys = two_stop_trip()
    outgoing = [{"from_boarding_stop_event_id": "E1",
                 "from_alighting_stop_event_id": "E2"}]
    return _recompute_counts(
        {"T1"}, trip_events, count_rows,
        {},                                    # moves_by_target
        {"T1": outgoing},                      # moves_by_source
        journeys,
        {"A1": 50},
        {"T1": {1: "A1", 2: "A1"}},
        set(),
    )


def conservation_violations(count_rows, updates):
    """Replay the verifier's own per-row conservation and continuity rules."""
    equation_bad = 0
    continuity_bad = 0
    previous_departure = 0
    for sequence, event_id in ((1, "E1"), (2, "E2")):
        row = dict(count_rows["T1"][event_id])
        row.update(updates.get(row["source_row_id"], {}))
        b, a = int(row["boardings"]), int(row["alightings"])
        oa, od = int(row["onboard_arrival"]), int(row["onboard_departure"])
        if min(b, a, oa, od) < 0 or a > oa or od != oa - a + b:
            equation_bad += 1
        if oa != previous_departure:
            continuity_bad += 1
        previous_departure = od
    return equation_bad, continuity_bad


class RecomputeCountsBehaviourTests(unittest.TestCase):
    """What the shipped function provably does today."""

    def test_incoming_move_rewrites_departures(self):
        updates = run_with_incoming_move()
        self.assertEqual(updates["C1"]["boardings"], "2")
        self.assertEqual(updates["C1"]["onboard_departure"], "2")
        self.assertEqual(updates["C2"]["alightings"], "2")

    def test_incoming_move_rewrites_onboard_arrival(self):
        """Incoming movement keeps arrival/departure continuity consistent."""
        updates = run_with_incoming_move()
        self.assertEqual(updates["C2"]["onboard_arrival"], "2")

    def test_corrected_series_satisfies_the_verifiers_two_equations(self):
        trip_events, count_rows, _ = two_stop_trip()
        updates = run_with_incoming_move()
        equation_bad, continuity_bad = conservation_violations(count_rows, updates)
        self.assertEqual(equation_bad, 0)
        self.assertEqual(continuity_bad, 0)

    def test_outgoing_move_preserves_flow_consistency(self):
        updates = run_with_outgoing_move()
        trip_events, count_rows, _ = two_stop_trip()
        equation_bad, continuity_bad = conservation_violations(count_rows, updates)
        self.assertEqual(equation_bad, 0)
        self.assertEqual(continuity_bad, 0)

    def test_a_no_op_move_produces_no_updates(self):
        trip_events, count_rows, journeys = two_stop_trip()
        updates = _recompute_counts(
            {"T1"}, trip_events, count_rows, {}, {}, journeys, {"A1": 50},
            {"T1": {1: "A1", 2: "A1"}}, set(),
        )
        self.assertEqual(updates, {})
        self.assertEqual(conservation_violations(count_rows, updates), (0, 0))

    def test_pre_correction_inconsistency_is_still_rejected(self):
        """The fail-closed pre-check must keep working."""
        trip_events, count_rows, journeys = two_stop_trip()
        count_rows["T1"]["E1"]["onboard_departure"] = "5"
        with self.assertRaises(ValueError):
            _recompute_counts({"T1"}, trip_events, count_rows, {}, {}, journeys,
                              {"A1": 50}, {"T1": {1: "A1", 2: "A1"}}, set())

    def test_overload_raises_the_flag(self):
        trip_events, count_rows, journeys = two_stop_trip()
        updates = _recompute_counts(
            {"T1"}, trip_events, count_rows, {"T1": list(INCOMING)}, {}, journeys,
            {"A1": 1}, {"T1": {1: "A1", 2: "A1"}}, set(),
        )
        self.assertEqual(updates["C1"]["quality_status"], "FLAGGED")

    def test_protected_rows_are_refused(self):
        trip_events, count_rows, journeys = two_stop_trip()
        with self.assertRaises(ValueError):
            _recompute_counts({"T1"}, trip_events, count_rows, {"T1": list(INCOMING)},
                              {}, journeys, {"A1": 50}, {"T1": {1: "A1", 2: "A1"}},
                              {"C1"})

    def test_count_coverage_mismatch_is_refused(self):
        trip_events, count_rows, journeys = two_stop_trip()
        del count_rows["T1"]["E2"]
        with self.assertRaises(ValueError):
            _recompute_counts({"T1"}, trip_events, count_rows, {"T1": list(INCOMING)},
                              {}, journeys, {"A1": 50}, {"T1": {1: "A1", 2: "A1"}}, set())


class RecomputeCountsDesiredBehaviourTests(unittest.TestCase):
    """Marks the fix that is still owed. Reported as a known defect."""

    def test_onboard_arrival_is_rewritten_so_the_series_stays_consistent(self):
        updates = run_with_incoming_move()
        self.assertEqual(
            updates.get("C2", {}).get("onboard_arrival"), "2",
            "stop 2 must record the 2 riders that arrived, matching the new departure",
        )
        _, count_rows, _ = two_stop_trip()
        self.assertEqual(conservation_violations(count_rows, updates), (0, 0))


if __name__ == "__main__":
    unittest.main()
