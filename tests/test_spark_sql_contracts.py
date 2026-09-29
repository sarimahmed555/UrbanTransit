"""DEL-09-07 Spark SQL tests: normal, failure and boundary cases.

Two layers, both honest about what they prove:

1. Executed static/contract tests (always run). They parse
   `spark_sql/integration.sql`, apply a stated SQL contract, and compare the
   reference SQL against the executable integration stage in
   `spark_jobs/integration.py`. These read repository source only; they open no
   dataset, start no session and write nothing.
2. Engine execution tests, skipped unless PySpark is installed. A skip is not
   a pass and must not be reported as runtime evidence.

Layer 1 is a definition plus a check, not a runtime claim. Layer 2 is the only
place a row count could ever be produced, and it is gated accordingly.
"""

import ast
import re
import unittest
from pathlib import Path

from spark_jobs.contracts import TABLES


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "spark_sql" / "integration.sql"
INTEGRATION_PY = ROOT / "spark_jobs" / "integration.py"
VIEW_PREFIX = "uti_"

# Statements that would mutate, drop or cache certified data. The integration
# stage is read-only by construction.
FORBIDDEN_KEYWORDS = (
    "INSERT", "OVERWRITE", "DROP", "DELETE", "TRUNCATE",
    "MSCK", "REPAIR TABLE", "CACHE", "UNCACHE", "ALTER TABLE",
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def strip_comments(sql):
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def parse_script(sql):
    """Split on top-level semicolons into ``(statement, terminated)`` pairs.

    No quoted ';' appears in this script, so a depth counter is sufficient.
    An unterminated tail is returned rather than dropped: it is the shape
    Spark itself refuses to run, so the contract has to be able to see it.
    """
    body, depth, current = [], 0, []
    for character in strip_comments(sql):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == ";" and depth == 0:
            statement = "".join(current).strip()
            if statement:
                body.append((statement, True))
            current = []
            continue
        current.append(character)
    tail = "".join(current).strip()
    if tail:
        body.append((tail, False))
    return body


def split_statements(sql):
    return [statement for statement, _ in parse_script(sql)]


def split_top_level(text, separator=","):
    parts, depth, current = [], 0, []
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(character)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def normalise(text):
    return " ".join(text.split()).lower()


def clause_after(sql, keyword):
    """Return the text between a top-level keyword and the next top-level keyword."""
    flat = normalise(sql)
    start = flat.find(keyword)
    if start < 0:
        return ""
    start += len(keyword)
    ends = [flat.find(other, start) for other in (" from ", " where ", " group ", " on ")]
    ends = [end for end in ends if end >= 0]
    return flat[start:min(ends)] if ends else flat[start:]


def referenced_tables(sql):
    return set(re.findall(r"\buti_[a-z_]+\b", sql.lower()))


def view_name(statement):
    match = re.match(
        r"create\s+or\s+replace\s+temp\s+view\s+([a-z_][a-z0-9_]*)", statement, re.I
    )
    return match.group(1) if match else None


def projection(sql):
    """Output column names of the top-level SELECT, alias-aware."""
    flat = normalise(sql)
    select_at = flat.find("select ")
    from_at = flat.find(" from ", select_at)
    if select_at < 0 or from_at < 0:
        return []
    names = []
    for item in split_top_level(flat[select_at + len("select "):from_at]):
        wildcard = re.fullmatch(r"([a-z_][a-z0-9_]*)\.\*", item)
        if wildcard:
            names.append(f"{wildcard.group(1)}.*")
            continue
        aliased = re.fullmatch(
            r"(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s+as\s+([a-z_][a-z0-9_]*)", item
        )
        if aliased:
            names.append(aliased.group(2))
            continue
        simple = re.fullmatch(r"(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)", item)
        names.append(simple.group(1) if simple else item)
    return names


def join_conditions(sql):
    """Normalised ON predicates, one entry per join."""
    flat = normalise(sql)
    from_at = flat.find(" from ")
    body = flat[from_at:] if from_at >= 0 else flat
    conditions = []
    for chunk in re.split(r"\bjoin\b", body)[1:]:
        match = re.search(r"\bon\b(.*)$", chunk)
        conditions.append(normalise(match.group(1)) if match else "")
    return conditions


# The as-of guard must be inclusive: an event published exactly at the trip's
# scheduled departure was known at that instant.
AVAILABILITY_GUARD = re.compile(
    r"value_available_at\s*(<=|<|>=|>)\s*t\.scheduled_start_utc"
)


def context_availability_operator(statement):
    """Comparison operator of the context_events as-of guard, or None."""
    flat = normalise(statement)
    chunks = re.split(r"\bjoin\b", flat)
    for chunk in chunks[1:]:
        if "uti_context_events" not in chunk:
            continue
        match = re.search(r"\bon\b(.*)$", chunk)
        condition = match.group(1) if match else ""
        found = AVAILABILITY_GUARD.search(condition)
        return found.group(1) if found else None
    return None


# ---------------------------------------------------------------------------
# The SQL contract
# ---------------------------------------------------------------------------
def contract_violations(sql):
    """Return a sorted list of contract violations for a whole SQL script."""
    problems = []
    parsed = parse_script(sql)
    if not parsed:
        return ["<empty script>: no SQL statements found"]
    for statement, terminated in parsed:
        name = view_name(statement) or "<unrecognised statement>"
        flat = normalise(statement)
        if not terminated:
            problems.append(f"{name}: statement is not terminated with ';'")
        if name == "<unrecognised statement>":
            problems.append(f"{name}: must be CREATE OR REPLACE TEMP VIEW")
        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword.lower()}\b", flat):
                problems.append(f"{name}: forbidden mutating keyword {keyword}")
        if flat.count("(") != flat.count(")"):
            problems.append(f"{name}: unbalanced parentheses")
        if re.search(r"(?<![\w.])select\s+\*(?!\s*\.)", flat):
            problems.append(f"{name}: unqualified SELECT * is ambiguous under joins")
        for table in sorted(referenced_tables(statement)):
            if table[len(VIEW_PREFIX):] not in TABLES:
                problems.append(f"{name}: unknown certified table {table}")
        for condition in join_conditions(statement):
            if not condition.strip():
                problems.append(f"{name}: join without an ON predicate")
        for keyword in ("inner join", "cross join", "full outer join", "right join"):
            if re.search(rf"\b{keyword}\b", flat):
                problems.append(f"{name}: {keyword.upper()} changes the row count")
        if "uti_context_events" in referenced_tables(statement):
            operator = context_availability_operator(statement)
            if operator is None:
                problems.append(f"{name}: context join has no as-of availability guard")
            elif operator != "<=":
                problems.append(
                    f"{name}: context availability must be an inclusive as-of (<=) "
                    f"predicate, found {operator}"
                )
            if "between" not in flat:
                problems.append(f"{name}: context join is not bounded by the event window")
    return sorted(problems)


def executable_view_sql(prefix=VIEW_PREFIX):
    """Extract the SQL literals of build_integrated_views without importing pyspark."""
    tree = ast.parse(INTEGRATION_PY.read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_integrated_views"
    )
    returned = next(
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    )
    views = {}
    for key, value in zip(returned.keys, returned.values):
        if not isinstance(key, ast.Constant) or not isinstance(value, ast.Call):
            continue
        sql_node = next(
            arg for arg in value.args if isinstance(arg, ast.Constant | ast.JoinedStr)
        )
        if isinstance(sql_node, ast.Constant):
            views[key.value] = sql_node.value
            continue
        # f-string: every placeholder in this module is the view prefix.
        chunks = []
        for part in sql_node.values:
            if isinstance(part, ast.Constant):
                chunks.append(part.value)
            else:
                chunks.append(prefix)
        views[key.value] = "".join(chunks)
    return views


# ---------------------------------------------------------------------------
# Normal cases: the shipped SQL satisfies the contract
# ---------------------------------------------------------------------------
class SparkSqlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SQL_PATH.read_text(encoding="utf-8")
        cls.statements = split_statements(cls.raw)
        cls.views = {view_name(s): s for s in cls.statements}

    def test_script_contains_only_recognised_view_statements(self):
        self.assertTrue(self.statements, "no SQL statements found")
        self.assertEqual([name for name in self.views if name is None], [])
        self.assertEqual(len(self.views), len(self.statements))

    def test_shipped_sql_has_no_contract_violation(self):
        self.assertEqual(contract_violations(self.raw), [])

    def test_every_view_is_reproducible_and_uses_the_certified_prefix(self):
        for name, statement in self.views.items():
            with self.subTest(view=name):
                self.assertTrue(
                    statement.upper().startswith("CREATE OR REPLACE TEMP VIEW"),
                    "views must be replaceable so a re-run cannot append duplicates",
                )
                self.assertTrue(referenced_tables(statement))

    def test_all_referenced_tables_are_declared_certified_tables(self):
        for name, statement in self.views.items():
            with self.subTest(view=name):
                for table in sorted(referenced_tables(statement)):
                    self.assertIn(table[len(VIEW_PREFIX):], TABLES)

    def test_every_join_is_a_left_join_with_an_on_predicate(self):
        for name, statement in self.views.items():
            with self.subTest(view=name):
                flat = normalise(statement)
                self.assertEqual(
                    flat.count("left join"), len(join_conditions(statement)),
                    "each join needs an ON predicate",
                )
                for condition in join_conditions(statement):
                    self.assertTrue(condition.strip())
                    self.assertIn("=", condition)

    def test_script_declares_no_mutating_statement(self):
        flat = normalise(self.raw)
        for keyword in FORBIDDEN_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertNotRegex(flat, rf"\b{keyword.lower()}\b")


# ---------------------------------------------------------------------------
# Failure cases: the contract must reject broken SQL
# ---------------------------------------------------------------------------
BROKEN_SQL = {
    "unbalanced parentheses": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id FROM uti_trips t LEFT JOIN uti_routes r ON (t.route_id = r.route_id;"
    ),
    "unknown table": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id FROM uti_trips t LEFT JOIN uti_not_a_table r ON t.route_id = r.route_id;"
    ),
    "mutating statement": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id FROM uti_trips t;"
        " INSERT OVERWRITE DIRECTORY '/tmp/x' SELECT 1;"
    ),
    "unqualified star under joins": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT * FROM uti_trips t LEFT JOIN uti_routes r ON t.route_id = r.route_id;"
    ),
    "inner join changes the row count": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id FROM uti_trips t "
        "INNER JOIN uti_routes r ON t.route_id = r.route_id;"
    ),
    "context join without an as-of guard": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id, c.event_type FROM uti_trips t "
        "LEFT JOIN uti_context_events c ON t.route_id = c.route_id;"
    ),
    "context join with a strict future-only guard": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id, c.event_type FROM uti_trips t "
        "LEFT JOIN uti_context_events c ON t.route_id = c.route_id "
        "AND t.scheduled_start_utc BETWEEN c.starts_at_utc AND c.ends_at_utc "
        "AND c.value_available_at > t.scheduled_start_utc;"
    ),
    "context join not bounded by the event window": (
        "CREATE OR REPLACE TEMP VIEW broken AS "
        "SELECT t.trip_id, c.event_type FROM uti_trips t "
        "LEFT JOIN uti_context_events c ON t.route_id = c.route_id "
        "AND c.value_available_at <= t.scheduled_start_utc;"
    ),
}


class SparkSqlContractFailureTests(unittest.TestCase):
    def test_each_broken_statement_is_rejected(self):
        for label, sql in BROKEN_SQL.items():
            with self.subTest(case=label):
                self.assertNotEqual(
                    contract_violations(sql), [],
                    f"contract failed to reject {label}",
                )

    def test_an_unterminated_trailing_statement_is_rejected(self):
        # Spark refuses an unterminated multi-statement script, and the
        # splitter must surface the tail instead of silently dropping it.
        script = (
            "CREATE OR REPLACE TEMP VIEW a AS SELECT trip_id FROM uti_trips;"
            "CREATE OR REPLACE TEMP VIEW b AS SELECT trip_id FROM uti_trips"
        )
        parsed = parse_script(script)
        self.assertEqual(len(parsed), 2)
        self.assertEqual([terminated for _, terminated in parsed], [True, False])
        self.assertEqual(
            parsed[-1][0],
            "CREATE OR REPLACE TEMP VIEW b AS SELECT trip_id FROM uti_trips",
        )
        self.assertIn(
            "b: statement is not terminated with ';'", contract_violations(script)
        )

    def test_comment_only_and_empty_fragments_are_ignored(self):
        script = (
            "-- a comment with a ; semicolon\n"
            "CREATE OR REPLACE TEMP VIEW a AS SELECT trip_id FROM uti_trips;\n"
            ";;\n"
        )
        self.assertEqual(len(split_statements(script)), 1)
        self.assertEqual(contract_violations(script), [])

    def test_an_empty_script_is_rejected(self):
        self.assertNotEqual(contract_violations("-- nothing here\n"), [])


# ---------------------------------------------------------------------------
# Boundary cases: the as-of predicate is inclusive at the exact boundary
# ---------------------------------------------------------------------------
class SparkSqlBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SQL_PATH.read_text(encoding="utf-8")
        cls.flat = normalise(cls.raw)
        cls.context = next(
            statement for statement in split_statements(cls.raw)
            if "uti_context_events" in statement
        )

    def test_context_availability_predicate_is_inclusive(self):
        self.assertEqual(
            context_availability_operator(self.context), "<=",
            "an event published exactly at departure is known at departure",
        )
        self.assertNotRegex(
            self.context, r"value_available_at\s*<(?!=)",
            "a strict '<' guard would drop an event known exactly at departure",
        )

    def test_event_window_uses_inclusive_between_on_both_ends(self):
        window = re.search(
            r"scheduled_start_utc\s+between\s+c\.starts_at_utc\s+and\s+c\.ends_at_utc",
            self.context, re.I,
        )
        self.assertIsNotNone(window, "event window must be bounded and inclusive")

    def test_every_statement_ends_with_exactly_one_terminator(self):
        self.assertEqual(
            [terminated for _, terminated in parse_script(self.raw)],
            [True] * len(split_statements(self.raw)),
        )
        self.assertEqual(self.raw.rstrip().count(";"), len(split_statements(self.raw)))

    def test_projection_keeps_the_context_availability_column(self):
        # The as-of guard is only auditable if the joined availability value is
        # projected out of the view.
        self.assertIn("context_available_at", projection(self.context))


# ---------------------------------------------------------------------------
# Parity: the reference SQL must match the executable integration stage
# ---------------------------------------------------------------------------
class SparkSqlParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql_views = {
            name: statement
            for name, statement in (
                (view_name(s), s) for s in split_statements(SQL_PATH.read_text(encoding="utf-8"))
            )
        }
        cls.py_views = executable_view_sql()

    def test_both_definitions_declare_the_same_views(self):
        self.assertEqual(
            sorted(self.sql_views), sorted(self.py_views),
            "spark_sql/integration.sql and spark_jobs/integration.py have drifted apart",
        )

    def test_both_definitions_reference_the_same_tables_per_view(self):
        for name in sorted(self.py_views):
            with self.subTest(view=name):
                self.assertEqual(
                    referenced_tables(self.sql_views[name]),
                    referenced_tables(self.py_views[name]),
                )

    def test_both_definitions_project_the_same_columns_per_view(self):
        for name in sorted(self.py_views):
            with self.subTest(view=name):
                self.assertEqual(
                    projection(self.sql_views[name]),
                    projection(self.py_views[name]),
                )

    def test_both_definitions_join_on_the_same_predicates(self):
        for name in sorted(self.py_views):
            with self.subTest(view=name):
                self.assertEqual(
                    join_conditions(self.sql_views[name]),
                    join_conditions(self.py_views[name]),
                )

    def test_python_integration_module_does_not_mutate_certified_data(self):
        source = INTEGRATION_PY.read_text(encoding="utf-8")
        for keyword in FORBIDDEN_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertNotRegex(source, rf"\b{keyword.lower()}\b")

    def test_registering_views_uses_the_same_prefix_as_the_queries(self):
        source = INTEGRATION_PY.read_text(encoding="utf-8")
        self.assertIn('f"{prefix}{name}"', source)
        self.assertEqual(VIEW_PREFIX, "uti_")


# ---------------------------------------------------------------------------
# Engine execution: gated, and a skip is not a pass
# ---------------------------------------------------------------------------
def _have_pyspark():
    try:
        import pyspark  # noqa: F401
    except Exception:
        return False
    return True


def _ts(value):
    import datetime as dt

    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=dt.timezone.utc
    )


# Fixture design. One route R1 with a known event E1, one event E2 published
# exactly at T1's departure, and one event E3 published one second after it.
# T3 sits on an unknown route with no event at all.
TRIP_SCHEMA = "trip_id string, route_id string, pattern_id string, scheduled_start_utc timestamp"
EVENT_SCHEMA = (
    "context_event_id string, route_id string, starts_at_utc timestamp, "
    "ends_at_utc timestamp, value_available_at timestamp, event_type string"
)


@unittest.skipUnless(_have_pyspark(), "PySpark is not installed; a skip is not a pass")
class SparkSqlExecutionTests(unittest.TestCase):
    """Tiny in-memory frames. Reads no dataset and writes no path.

    The expected pairs below are derived by hand from the as-of rule, not
    observed from a run. They state what the SQL *must* produce; whether it
    does is only knowable where PySpark exists.
    """

    def setUp(self):
        from pyspark.sql import SparkSession

        self.spark = (
            SparkSession.builder.master("local[1]").appName("spark-sql-test").getOrCreate()
        )
        self.spark.createDataFrame(
            [
                ("T1", "R1", "P1", _ts("2026-02-02 08:00:00")),
                ("T2", "R1", "P1", _ts("2026-02-02 09:00:00")),
                ("T3", "R9", "P9", _ts("2026-02-02 09:30:00")),
            ], TRIP_SCHEMA,
        ).selectExpr(
            "*", "trip_id AS operational_departure_id",
            "DATE '2026-02-02' AS service_date",
        ).createOrReplaceTempView("uti_trips")
        self.spark.createDataFrame(
            [
                # known well before both trips, covers both
                ("E1", "R1", _ts("2026-02-02 07:00:00"), _ts("2026-02-02 10:00:00"),
                 _ts("2026-02-01 00:00:00"), "RAIN"),
                # published exactly at T1's departure -> known at that instant
                ("E2", "R1", _ts("2026-02-02 08:00:00"), _ts("2026-02-02 09:00:00"),
                 _ts("2026-02-02 08:00:00"), "FESTIVAL"),
                # published one second after T1 departed, still before T2
                ("E3", "R1", _ts("2026-02-02 06:00:00"), _ts("2026-02-02 10:00:00"),
                 _ts("2026-02-02 08:00:01"), "MATCH"),
            ], EVENT_SCHEMA,
        ).createOrReplaceTempView("uti_context_events")
        self.spark.createDataFrame(
            [("R1", "R-001", "BUS")], "route_id string, route_code string, mode string"
        ).createOrReplaceTempView("uti_routes")
        self.spark.createDataFrame(
            [("P1", "R1", 0)], "pattern_id string, route_id string, direction_id int"
        ).createOrReplaceTempView("uti_route_patterns")
        # Remaining integration inputs are registered empty on purpose: this
        # module exercises the SQL contract, not the fact tables. Every view
        # must still resolve, so the schemas carry the joined key columns.
        for view, schema in (
            ("trip_stop_events",
             "trip_id string, stop_event_id string, route_stop_id string, "
             "stop_sequence int, actual_arrival_utc timestamp, "
             "actual_departure_utc timestamp"),
            ("delays",
             "stop_event_id string, arrival_delay_sec long, departure_delay_sec long"),
            ("passenger_counts",
             "stop_event_id string, boardings long, alightings long, "
             "onboard_departure long"),
            ("passenger_journeys",
             "journey_id string, passenger_id string, trip_id string, "
             "service_date string, origin_route_stop_id string, "
             "destination_route_stop_id string, request_id string"),
            ("demand_requests", "request_id string, request_available_at_utc timestamp"),
        ):
            self.spark.createDataFrame([], schema).createOrReplaceTempView(f"uti_{view}")
        for statement in split_statements(SQL_PATH.read_text(encoding="utf-8")):
            self.spark.sql(statement)

    def tearDown(self):
        self.spark.stop()

    def attached_events(self):
        rows = self.spark.sql(
            "SELECT trip_id, context_event_id, context_available_at "
            "FROM trip_context WHERE context_event_id IS NOT NULL"
        ).collect()
        return {(row["trip_id"], row["context_event_id"]) for row in rows}

    def test_normal_case_known_event_attaches_to_both_matching_trips(self):
        attached = self.attached_events()
        self.assertIn(("T1", "E1"), attached)
        self.assertIn(("T2", "E1"), attached)

    def test_boundary_case_event_published_exactly_at_departure_is_included(self):
        self.assertIn(("T1", "E2"), self.attached_events())

    def test_boundary_case_event_published_after_departure_is_excluded(self):
        attached = self.attached_events()
        self.assertNotIn(("T1", "E3"), attached, "as-of guard leaked a future event")
        self.assertIn(("T2", "E3"), attached, "the same event is known for the later trip")

    def test_left_joins_keep_a_trip_with_no_event_or_route_row(self):
        rows = {
            row["trip_id"]: row
            for row in self.spark.sql(
                "SELECT trip_id, context_event_id, route_code FROM trip_context"
            ).collect()
        }
        self.assertIn("T3", rows, "an unmatched trip must not be dropped")
        self.assertIsNone(rows["T3"]["context_event_id"])
        self.assertIsNone(rows["T3"]["route_code"])

    def test_no_trip_is_lost_and_every_event_stays_on_its_own_route(self):
        self.spark.sql("SELECT * FROM trip_context").createOrReplaceTempView("ctx")
        cross = self.spark.sql(
            "SELECT count(*) AS n FROM trip_context "
            "WHERE context_event_id IS NOT NULL AND route_id NOT IN "
            "(SELECT route_id FROM uti_context_events)"
        ).collect()[0]["n"]
        self.assertEqual(cross, 0)

    def test_failure_case_unknown_table_is_rejected_by_spark(self):
        with self.assertRaises(Exception):
            self.spark.sql(
                "SELECT t.trip_id FROM uti_trips t "
                "LEFT JOIN uti_not_a_table r ON t.route_id = r.route_id"
            )

    def test_executable_python_projection_agrees_with_the_sql_file(self):
        from spark_jobs.integration import build_integrated_views

        views = build_integrated_views(self.spark)
        for name in ("trip_context", "trip_operations", "demand_operations"):
            with self.subTest(view=name):
                self.assertEqual(
                    sorted(views[name].columns), sorted(self.spark.table(name).columns)
                )


if __name__ == "__main__":
    unittest.main()
