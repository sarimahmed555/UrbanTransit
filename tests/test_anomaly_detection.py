"""DEL-09-15 anomaly tests: normal, failure and boundary cases only.

Covers SRS STEP-39 (unusual transport behaviour), STEP-32 (headway anomalies),
STEP-38 (special-event spikes) and FR-046 (normal behaviour detection).

Scope and honesty rules:

* Every executed case here is a pure, in-memory or static-source test. No Spark
  session, no dataset, no service and no production file is opened.
* Rows are tiny synthetic dicts. They are fixtures, not results, and prove only
  that the shipped classification rules behave as documented.
* A `skipped` case is not a pass. Engine-level (PySpark/Pandas) equivalents are
  marked skipped when the dependency is absent, exactly as elsewhere in `tests/`.
"""

import unittest
from pathlib import Path

from feature_contracts import validate_severity_thresholds
from ml_execution.analytics import service_analytics


ROOT = Path(__file__).resolve().parents[1]

# The four-band occupancy policy from the SRS occupancy categorisation
# (STEP-12): Low / Moderate / High / Overcrowded.
POLICY = {
    "early_tolerance_sec": 60,
    "late_tolerance_sec": 300,
    "recurrence_min_days": 2,
    "occupancy_bands": [0.4, 0.8, 1.0],
}


def case(**overrides):
    """One synthetic stop-event observation with every dimension populated."""
    row = {
        "service_date": "2026-02-02",
        "route_id": "R1",
        "direction_id": 0,
        "trip_id": "T1",
        "stop_id": "S1",
        "distance_band": "short",
        "event_hour": 8,
        "weekday": 0,
        "departure_vehicle_id": "V1",
        "departure_assignment_status": "KNOWN",
        "delay_status": "AVAILABLE",
        "signed_departure_delay_sec": 0.0,
        "occupancy_status": "AVAILABLE",
        "capacity_snapshot": 100,
        "capacity_utilization": 0.5,
        "service_status": "OBSERVED",
    }
    row.update(overrides)
    return row


def band_of(utilization, policy=None):
    """Return the band name the shipped rule assigns to one utilization value."""
    rows = [case(capacity_utilization=utilization, capacity_snapshot=100)]
    result = service_analytics(rows, dict(policy or POLICY))
    counts = result["occupancy_bands"]
    named = [name for name, count in counts.items() if count]
    return named[0] if named else None


# ---------------------------------------------------------------------------
# SRS STEP-39 / STEP-12: occupancy band classification
# ---------------------------------------------------------------------------
class OccupancyBandAnomalyTests(unittest.TestCase):
    def test_normal_utilizations_land_in_their_documented_bands(self):
        self.assertEqual(band_of(0.20), "Low")
        self.assertEqual(band_of(0.50), "Moderate")
        self.assertEqual(band_of(0.90), "High")
        self.assertEqual(band_of(1.40), "Overcrowded")

    def test_band_boundaries_are_half_open_towards_the_lower_band(self):
        # The shipped rule is a strict `>` comparison, so a value sitting
        # exactly on a boundary is NOT yet in the higher band.
        self.assertEqual(band_of(0.4), "Low")
        self.assertEqual(band_of(0.8), "Moderate")
        self.assertEqual(band_of(1.0), "High")

    def test_just_past_each_boundary_enters_the_higher_band(self):
        for boundary, expected in ((0.4, "Moderate"), (0.8, "High"), (1.0, "Overcrowded")):
            with self.subTest(boundary=boundary):
                self.assertEqual(band_of(boundary + 1e-9), expected)

    def test_unmeasurable_occupancy_is_excluded_and_never_read_as_low_demand(self):
        # Missing/unmeasurable capacity is a coverage gap, not a low-demand
        # observation; counting it as Low would fabricate a Step-39 signal.
        rows = [
            case(occupancy_status="UNAVAILABLE", capacity_utilization=0.0),
            case(capacity_snapshot=0, capacity_utilization=0.0),
            case(capacity_snapshot=-40, capacity_utilization=0.0),
            case(capacity_utilization=float("nan")),
            case(capacity_utilization=-0.5),
        ]
        result = service_analytics(rows, dict(POLICY))
        self.assertEqual(result["occupancy_eligible_count"], 0)
        self.assertEqual(result["occupancy_unavailable_count"], 5)
        self.assertIsNone(result["occupancy_bands"])

    def test_zero_utilization_with_valid_capacity_is_a_real_low_signal(self):
        result = service_analytics([case(capacity_utilization=0.0)], dict(POLICY))
        self.assertEqual(result["occupancy_bands"]["Low"], 1)


# ---------------------------------------------------------------------------
# SRS STEP-39: abnormal delay classification and its normal control
# ---------------------------------------------------------------------------
class DelayAnomalyTests(unittest.TestCase):
    def test_normal_service_is_on_time(self):
        result = service_analytics([case(signed_departure_delay_sec=0.0)], dict(POLICY))
        summary = result["delay_summary"]
        self.assertEqual(summary["late_count"], 0)
        self.assertEqual(summary["early_count"], 0)
        self.assertEqual(summary["on_time_fraction"], 1.0)

    def test_tolerance_boundaries_stay_on_time_and_only_exceedance_flags(self):
        exact_late = service_analytics(
            [case(signed_departure_delay_sec=300.0)], dict(POLICY)
        )["delay_summary"]
        self.assertEqual(exact_late["late_count"], 0, "exactly `late` is not late")
        self.assertEqual(exact_late["on_time_fraction"], 1.0)

        over_late = service_analytics(
            [case(signed_departure_delay_sec=300.000001)], dict(POLICY)
        )["delay_summary"]
        self.assertEqual(over_late["late_count"], 1)

        exact_early = service_analytics(
            [case(signed_departure_delay_sec=-60.0)], dict(POLICY)
        )["delay_summary"]
        self.assertEqual(exact_early["early_count"], 0, "exactly -early is not early")

        over_early = service_analytics(
            [case(signed_departure_delay_sec=-60.000001)], dict(POLICY)
        )["delay_summary"]
        self.assertEqual(over_early["early_count"], 1)

    def test_recurring_lateness_needs_the_configured_number_of_days(self):
        one_day = service_analytics(
            [case(service_date="2026-02-02", signed_departure_delay_sec=600.0)],
            dict(POLICY),
        )
        self.assertEqual(one_day["delay_segments"]["day"]["results"][0]["late_count"], 1)
        self.assertFalse(one_day["delay_segments"]["day"]["results"][0]["recurring_delay"])

        two_days = service_analytics(
            [
                case(service_date="2026-02-02", signed_departure_delay_sec=600.0),
                case(service_date="2026-02-03", signed_departure_delay_sec=600.0),
            ],
            dict(POLICY),
        )
        segment = two_days["delay_segments"]["day"]["results"][0]
        self.assertEqual(segment["late_service_days"], ["2026-02-02", "2026-02-03"])
        self.assertTrue(segment["recurring_delay"], "boundary: exactly recurrence_min_days")

    def test_unavailable_delay_is_excluded_from_the_denominator(self):
        rows = [
            case(delay_status="UNAVAILABLE", signed_departure_delay_sec=900.0),
            case(signed_departure_delay_sec=float("nan")),
            case(signed_departure_delay_sec=0.0),
        ]
        result = service_analytics(rows, dict(POLICY))
        self.assertEqual(result["delay_eligible_count"], 1)
        self.assertEqual(result["delay_unavailable_count"], 2)
        self.assertEqual(result["delay_summary"]["sample_count"], 1)

    def test_unknown_vehicle_assignment_is_excluded_from_the_vehicle_dimension(self):
        rows = [
            case(departure_assignment_status="UNKNOWN", departure_vehicle_id=None),
            case(),
        ]
        vehicle = service_analytics(rows, dict(POLICY))["delay_segments"]["vehicle"]
        self.assertEqual(vehicle["excluded_count"], 1)
        self.assertEqual(vehicle["results"][0]["sample_count"], 1)


# ---------------------------------------------------------------------------
# SRS STEP-12: persistent crowding needs distinct service days
# ---------------------------------------------------------------------------
class PersistentCrowdingTests(unittest.TestCase):
    def crowd(self, utilizations_by_day):
        rows = [
            case(service_date=day, capacity_utilization=utilization)
            for day, utilization in utilizations_by_day
        ]
        return service_analytics(rows, dict(POLICY))["persistent_crowding"][0]

    def test_normal_service_is_not_persistent_crowding(self):
        result = self.crowd([("2026-02-02", 0.5), ("2026-02-03", 0.6)])
        self.assertEqual(result["overloaded_count"], 0)
        self.assertEqual(result["overloaded_days"], [])
        self.assertFalse(result["persistent"])

    def test_single_overloaded_day_is_not_yet_persistent(self):
        result = self.crowd([("2026-02-02", 1.2), ("2026-02-03", 0.5)])
        self.assertEqual(result["overloaded_count"], 1)
        self.assertEqual(result["overloaded_days"], ["2026-02-02"])
        self.assertFalse(result["persistent"])

    def test_two_overloaded_days_cross_the_persistence_boundary(self):
        result = self.crowd([("2026-02-02", 1.2), ("2026-02-03", 1.05)])
        self.assertTrue(result["persistent"])
        self.assertEqual(result["overloaded_days"], ["2026-02-02", "2026-02-03"])

    def test_capacity_exactly_met_is_not_overloaded(self):
        result = self.crowd([("2026-02-02", 1.0), ("2026-02-03", 1.0)])
        self.assertEqual(result["overloaded_count"], 0, "at capacity is not overload")

    def test_underutilised_days_are_counted_against_the_same_segment(self):
        result = self.crowd([("2026-02-02", 0.1), ("2026-02-03", 0.2)])
        self.assertEqual(result["underutilized_count"], 2)
        self.assertEqual(result["sample_count"], 2)

    def test_crowding_segment_without_full_keys_is_reported_not_dropped(self):
        rows = [case(route_id=None), case()]
        result = service_analytics(rows, dict(POLICY))
        self.assertEqual(result["crowding_segment_unavailable_count"], 1)
        self.assertEqual(len(result["persistent_crowding"]), 1)


# ---------------------------------------------------------------------------
# Failure cases: an unusable policy must raise, never silently mis-band
# ---------------------------------------------------------------------------
class AnomalyPolicyFailureTests(unittest.TestCase):
    def rows(self):
        return [case()]

    def assertRejected(self, policy):
        with self.assertRaises(ValueError):
            service_analytics(self.rows(), policy)

    def test_missing_policy_key_is_rejected(self):
        for key in POLICY:
            policy = dict(POLICY)
            policy.pop(key)
            with self.subTest(missing=key):
                self.assertRejected(policy)

    def test_wrong_number_of_occupancy_bands_is_rejected(self):
        self.assertRejected({**POLICY, "occupancy_bands": [0.4, 0.8]})
        self.assertRejected({**POLICY, "occupancy_bands": [0.4, 0.8, 1.0, 1.2]})

    def test_non_increasing_occupancy_bands_are_rejected(self):
        self.assertRejected({**POLICY, "occupancy_bands": [0.8, 0.4, 1.0]})
        self.assertRejected({**POLICY, "occupancy_bands": [0.4, 0.8, 0.8]})

    def test_out_of_range_occupancy_bands_are_rejected(self):
        self.assertRejected({**POLICY, "occupancy_bands": [0.0, 0.8, 1.0]})
        self.assertRejected({**POLICY, "occupancy_bands": [-0.1, 0.8, 1.0]})

    def test_non_finite_occupancy_band_is_rejected(self):
        self.assertRejected({**POLICY, "occupancy_bands": [0.4, float("nan"), 1.0]})
        self.assertRejected({**POLICY, "occupancy_bands": [0.4, 0.8, float("inf")]})

    def test_invalid_delay_tolerances_are_rejected(self):
        self.assertRejected({**POLICY, "early_tolerance_sec": -1})
        self.assertRejected({**POLICY, "late_tolerance_sec": -1})
        self.assertRejected({**POLICY, "late_tolerance_sec": float("nan")})
        self.assertRejected({**POLICY, "early_tolerance_sec": True})

    def test_recurrence_below_two_days_is_rejected(self):
        self.assertRejected({**POLICY, "recurrence_min_days": 1})
        self.assertRejected({**POLICY, "recurrence_min_days": 0})


# ---------------------------------------------------------------------------
# SRS STEP-39 abnormal-delay severity ladder (five labels, four boundaries)
# ---------------------------------------------------------------------------
class DelaySeverityThresholdTests(unittest.TestCase):
    def test_zero_is_a_valid_lower_boundary(self):
        self.assertEqual(validate_severity_thresholds([0, 60, 300, 900]), (0, 60, 300, 900))

    def test_wrong_length_is_rejected(self):
        for values in ([0, 60, 300], [0, 60, 300, 900, 1800], []):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_severity_thresholds(values)

    def test_non_increasing_boundaries_are_rejected(self):
        for values in ([0, 60, 60, 900], [0, 300, 60, 900], [900, 300, 60, 0]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_severity_thresholds(values)

    def test_negative_non_finite_and_non_numeric_values_are_rejected(self):
        for values in (
            [-1, 60, 300, 900],
            [0, 60, 300, float("nan")],
            [0, 60, 300, float("inf")],
            [0, 60, 300, "900"],
            [0, 60, 300, None],
            [True, 60, 300, 900],
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_severity_thresholds(values)

    def test_both_engines_expose_the_same_five_label_ladder(self):
        spark = (ROOT / "spark_jobs" / "features.py").read_text(encoding="utf-8")
        python = (ROOT / "python_pipeline" / "features.py").read_text(encoding="utf-8")
        for source in (spark, python):
            with self.subTest(source=source[:40]):
                self.assertIn("delay_severity", source)
        self.assertIn("validate_severity_thresholds", spark)


# ---------------------------------------------------------------------------
# Static traceability: every STEP-39 "such as" case has a concrete rule
# ---------------------------------------------------------------------------
# SRS STEP-39 lists the cases as examples ("such as"), so the project selects
# the applicable ones. Each entry names a file and a token that must exist, so
# the mapping cannot silently rot when the code moves.
STEP_39_TRACEABILITY = {
    "sudden passenger spikes": (
        "data_generator/generators/stop_events.py", 'scenario("passenger_spikes")'),
    "sudden demand drops": (
        "data_generator/generators/stop_events.py", 'scenario("low_demand")'),
    "abnormal delays": (
        "data_generator/generators/stop_events.py", 'scenario("delays")'),
    "unexpected route usage": (
        "data_generator/generators/network.py", 'scenario("new_routes"'),
    "excessively low passenger counts": (
        "data_generator/generators/stop_events.py", 'scenario("low_demand")'),
    "duplicate ticketing": (
        "data_generator/generators/quality_conditions.py", "INJ-DQ04-DUP-TICKET"),
    "impossible occupancy": (
        "data_generator/generators/quality_conditions.py", "INJ-DQ10-CAPACITY-VIOLATION"),
    "abnormal travel time": (
        "data_generator/generators/quality_conditions.py", "INJ-DQ08-IMPOSSIBLE-ARRIVAL"),
    "irregular stop activity": (
        "data_generator/generators/quality_conditions.py", "INJ-DQ13-BROKEN-SEQUENCE"),
}

# FR-046 additionally requires *normal* behaviour to be detected, and
# STEP-32/STEP-38 require headway irregularity and event spikes to stay
# separate from the normal baseline.
NORMAL_BEHAVIOUR_TRACEABILITY = {
    "normal demand baseline": (
        "python_pipeline/features.py", "def build_demand_forecast_features"),
    "headway irregularity signal": (
        "spark_jobs/features.py", '"headway_deviation_sec"'),
    "special event not resetting the baseline": (
        "data_generator/generators/stop_events.py", 'scenario("event_demand_spike")'),
    "early/normal arrival evidence": (
        "data_generator/validation/transport_checks.py", "early_arrival_evidence"),
    "bunching and irregular headway evidence": (
        "data_generator/validation/transport_checks.py", "bunching_irregular_evidence"),
    "real overload retained and flagged": (
        "data_generator/validation/transport_checks.py", "overcrowded_service_evidence"),
    "low demand service observable": (
        "data_generator/validation/transport_checks.py", "low_demand_service_evidence"),
}


class AnomalyTraceabilityTests(unittest.TestCase):
    def assertTraceable(self, mapping, label):
        missing = []
        for case_name, (relative, token) in mapping.items():
            path = ROOT / relative
            if not path.is_file() or token not in path.read_text(encoding="utf-8"):
                missing.append(f"{case_name} -> {relative}:{token}")
        self.assertEqual(missing, [], f"{label} without an implementation anchor: {missing}")

    def test_every_step_39_example_case_maps_to_an_implemented_rule(self):
        self.assertTraceable(STEP_39_TRACEABILITY, "STEP-39 cases")

    def test_normal_behaviour_detection_is_implemented(self):
        self.assertTraceable(NORMAL_BEHAVIOUR_TRACEABILITY, "FR-046 / STEP-32 / STEP-38")

    def test_anomaly_capability_is_declared_in_the_submission_documents(self):
        for name in ("ANALYTICS_CAPABILITY_MAP.md", "PROJECT_REPORT_TEMPLATE.md"):
            text = (ROOT / "documentation" / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                self.assertRegex(text.lower(), r"anomal")

    def test_readme_still_reports_the_execution_status_of_anomaly_tests(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # README must not claim anomaly tests are executed evidence; the
        # suite is static/unit only until a certified run records results.
        self.assertIn("PENDING CERTIFIED RUNTIME EVIDENCE", readme)
        self.assertIn("A skip is not a pass", readme)


# ---------------------------------------------------------------------------
# Engine parity: both pipelines must classify anomalies identically
# ---------------------------------------------------------------------------
class AnomalyEngineParityTests(unittest.TestCase):
    def sources(self):
        return {
            "spark": (ROOT / "spark_jobs" / "features.py").read_text(encoding="utf-8"),
            "python": (ROOT / "python_pipeline" / "features.py").read_text(encoding="utf-8"),
        }

    def test_both_engines_emit_the_same_over_capacity_and_headway_signals(self):
        for engine, source in self.sources().items():
            with self.subTest(engine=engine):
                self.assertIn("over_capacity", source)
                self.assertIn("capacity_utilization", source)
                self.assertIn("actual_headway_sec", source)
                self.assertIn("headway_deviation_sec", source)

    def test_both_engines_keep_unmeasurable_capacity_unavailable(self):
        for engine, source in self.sources().items():
            with self.subTest(engine=engine):
                self.assertIn("UNAVAILABLE", source)

    def test_occupancy_and_headway_contracts_are_shared_not_redefined(self):
        contracts = (ROOT / "feature_contracts.py").read_text(encoding="utf-8")
        self.assertIn('"occupancy": FeatureContract(', contracts)
        self.assertIn('"headway": FeatureContract(', contracts)
        self.assertIn("ratios above 1 and flag over-capacity without clipping", contracts)


# ---------------------------------------------------------------------------
# Engine-level equivalents: honest skips when the dependency is absent
# ---------------------------------------------------------------------------
def _dependency(name):
    try:
        __import__(name)
    except Exception:  # pragma: no cover - depends on the environment
        return False
    return True


HAVE_PYSPARK = _dependency("pyspark")
HAVE_PANDAS = _dependency("pandas")


@unittest.skipUnless(HAVE_PYSPARK, "PySpark is not installed; a skip is not a pass")
class SparkEngineAnomalyTests(unittest.TestCase):
    """Tiny in-memory frames only. Never reads a dataset or writes a path."""

    def test_over_capacity_is_flagged_without_clipping(self):
        from spark_jobs.features import build_occupancy_features

        spark = __import__("pyspark.sql", fromlist=["SparkSession"]).SparkSession.builder
        session = spark.master("local[1]").appName("anomaly-test").getOrCreate()
        try:
            frame = session.createDataFrame([
                ("2026-02-02", "E1", 120, 100),
                ("2026-02-02", "E2", 100, 100),
                ("2026-02-02", "E3", 50, 0),
                ("2026-02-02", "E4", 50, None),
            ], "service_date string, stop_event_id string, "
               "onboard_departure long, capacity_snapshot long")
            result = build_occupancy_features(frame)
            rows = {r["stop_event_id"]: r for r in result.collect()}
            self.assertTrue(rows["E1"]["over_capacity"])
            self.assertGreater(rows["E1"]["capacity_utilization"], 1.0,
                               "over-capacity ratio must not be clipped")
            self.assertFalse(rows["E2"]["over_capacity"], "exactly at capacity")
            self.assertIsNone(rows["E3"]["capacity_utilization"])
            self.assertEqual(rows["E3"]["occupancy_status"], "UNAVAILABLE")
            self.assertIsNone(rows["E4"]["capacity_utilization"])
        finally:
            session.stop()

    def test_headway_deviation_is_null_for_the_first_departure_only(self):
        from spark_jobs.features import build_headway_features

        spark = __import__("pyspark.sql", fromlist=["SparkSession"]).SparkSession.builder
        session = spark.master("local[1]").appName("anomaly-test").getOrCreate()
        try:
            frame = session.createDataFrame([
                ("2026-02-02", "R1", 0, "ST1", "T1", "E1", 100, 100),
                ("2026-02-02", "R1", 0, "ST1", "T2", "E2", 130, 100),
                ("2026-02-02", "R1", 0, "ST1", "T3", "E3", 160, 100),
            ], "service_date string, route_id string, direction_id int, stop_id string, "
               "trip_id string, stop_event_id string, actual_departure_utc long, "
               "scheduled_departure_utc long")
            rows = {r["stop_event_id"]: r for r in build_headway_features(frame).collect()}
            self.assertIsNone(rows["E1"]["actual_headway_sec"], "no prior departure")
            self.assertEqual(rows["E2"]["actual_headway_sec"], 30)
            self.assertEqual(rows["E2"]["headway_deviation_sec"], 0)
            self.assertEqual(rows["E3"]["headway_deviation_sec"], 0)
        finally:
            session.stop()


@unittest.skipUnless(HAVE_PANDAS, "Pandas is not installed; a skip is not a pass")
class PythonEngineAnomalyTests(unittest.TestCase):
    """Tiny in-memory frames only, mirroring the Spark parity cases."""

    def frames(self):
        import pandas

        operations = pandas.DataFrame([
            {"service_date": "2026-02-02", "stop_event_id": "E1",
             "onboard_departure": 120, "capacity_snapshot": 100},
            {"service_date": "2026-02-02", "stop_event_id": "E2",
             "onboard_departure": 100, "capacity_snapshot": 100},
            {"service_date": "2026-02-02", "stop_event_id": "E3",
             "onboard_departure": 50, "capacity_snapshot": 0},
        ])
        headway = pandas.DataFrame([
            {"service_date": "2026-02-02", "route_id": "R1", "direction_id": 0,
             "stop_id": "ST1", "trip_id": "T1", "stop_event_id": "E1",
             "actual_departure_utc": "2026-02-02T00:01:40+00:00",
             "scheduled_departure_utc": "2026-02-02T00:01:40+00:00"},
            {"service_date": "2026-02-02", "route_id": "R1", "direction_id": 0,
             "stop_id": "ST1", "trip_id": "T2", "stop_event_id": "E2",
             "actual_departure_utc": "2026-02-02T00:03:10+00:00",
             "scheduled_departure_utc": "2026-02-02T00:03:10+00:00"},
        ])
        return operations, headway

    def test_over_capacity_is_flagged_without_clipping(self):
        from python_pipeline.features import build_occupancy_features

        operations, _ = self.frames()
        rows = build_occupancy_features(operations).set_index("stop_event_id")
        self.assertTrue(bool(rows.loc["E1", "over_capacity"]))
        self.assertGreater(rows.loc["E1", "capacity_utilization"], 1.0)
        self.assertFalse(bool(rows.loc["E2", "over_capacity"]))
        self.assertTrue(pandas_isna(rows.loc["E3", "capacity_utilization"]))
        self.assertEqual(rows.loc["E3", "occupancy_status"], "UNAVAILABLE")

    def test_headway_deviation_is_null_only_for_the_first_departure(self):
        from python_pipeline.features import build_headway_features

        _, headway = self.frames()
        rows = build_headway_features(headway).set_index("stop_event_id")
        self.assertTrue(pandas_isna(rows.loc["E1", "actual_headway_sec"]))
        self.assertAlmostEqual(rows.loc["E2", "actual_headway_sec"], 90.0)
        self.assertAlmostEqual(rows.loc["E2", "headway_deviation_sec"], 0.0)


def pandas_isna(value):
    """Null check that does not require importing pandas in this module."""
    return value is None or (isinstance(value, float) and value != value)


if __name__ == "__main__":
    unittest.main()
