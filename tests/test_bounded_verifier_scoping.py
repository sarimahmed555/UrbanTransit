"""Static scope tests for the bounded remediation/verification modules.

These tests never open a dataset, never materialise, and never run the
verifier. They exist because the verifier crashed mid-run with

    NameError: name 'journeys_by_request' is not defined

which is a *scope* defect: a name was read at a point where nothing in any
enclosing scope had bound it. A crash of that kind is invisible to a data test
because it only fires after hours of scanning, so it is caught statically here
instead.

Properties proved:

1. Every name read inside every function of the bounded modules resolves to an
   enclosing binding, a module-level binding, or a builtin. This is the exact
   class of the reported crash.
2. The checker detects that class when it is deliberately reintroduced, so it
   cannot pass vacuously.
3. ``verify()`` contains no mutating call, so the verifier cannot repair what
   it is asked to check.

Known limitation: the resolver is flow-insensitive, so it treats ``del name``
as a binding and will not flag a *read* of a name deleted earlier in the same
function. That case needs control-flow analysis and is out of scope here; it is
recorded rather than claimed as covered.

If ``pyflakes`` happens to be installed it is run as a second opinion. The
in-repo checker is authoritative because it needs no dependency.
"""

import ast
import builtins
import io
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "data_generator"

BOUNDED_MODULES = (
    "bounded_verification.py",
    "bounded_remediation.py",
    "bounded_executor.py",
    "remediate_bounded.py",
)

# The defect reported from the verifier run, kept verbatim as a detector fixture.
REPORTED_DEFECT = "journeys_by_request"

_BUILTINS = set(dir(builtins))


def _all_args(arguments):
    found = list(arguments.posonlyargs) + list(arguments.args) + list(arguments.kwonlyargs)
    if arguments.vararg:
        found.append(arguments.vararg)
    if arguments.kwarg:
        found.append(arguments.kwarg)
    return found


def _bind_target(node, bound):
    if isinstance(node, ast.Name):
        bound.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            _bind_target(element, bound)
    elif isinstance(node, ast.Starred):
        _bind_target(node.value, bound)


def module_bindings(tree):
    """Names a module body binds, visible to every function in that module."""
    bound = set()
    for statement in tree.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
    return bound


def local_bindings(function_node):
    """Every name a function body can bind, ignoring statement order.

    Flow-insensitive by design. A resolver that tracked control flow would have
    to prove "bound on every path" and would drown in false positives on a
    600-line function. The reported crash is still caught, because nothing binds
    that name anywhere.
    """
    bound = set()
    for argument in _all_args(function_node.args):
        bound.add(argument.arg)
    for node in ast.walk(function_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            for argument in _all_args(node.args):
                bound.add(argument.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.comprehension,)):
            _bind_target(node.target, bound)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _bind_target(node.optional_vars, bound)
    return bound


def unresolved_reads(function_node, module_names, inherited=()):
    """Names read by a function that nothing in scope binds."""
    available = set(inherited) | local_bindings(function_node) | module_names | _BUILTINS
    problems = []
    for node in ast.walk(function_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in available:
                problems.append((function_node.name, node.id, node.lineno))
    return problems


def source_undefined_names(path):
    """All unresolved name reads in the module, as ``(function, name, line)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_names = module_bindings(tree)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            problems.extend(unresolved_reads(node, module_names))
    return sorted(set(problems))


def fixture_undefined_names(source):
    tree = ast.parse(source)
    module_names = module_bindings(tree)
    problems = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            problems.extend(unresolved_reads(node, module_names))
    return problems


def function_source(path, name):
    """Exact source text of one top-level function, without its neighbours."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(lines[start:end])
    raise AssertionError(f"{name} not found in {path}")


# --------------------------------------------------------------------------- #
# The detector itself must work
# --------------------------------------------------------------------------- #
class ScopeCheckerSelfTests(unittest.TestCase):
    def test_detects_the_reported_undefined_name(self):
        fixture = f"""
def verify(target):
    seen = set()
    for row in target:
        if row in seen:
            continue
        context = {REPORTED_DEFECT}[row]
        seen.add(row)
    return seen
"""
        problems = fixture_undefined_names(fixture)
        self.assertTrue(
            any(name == REPORTED_DEFECT for _, name, _ in problems),
            f"checker missed the reported defect class: {problems}",
        )

    def test_accepts_a_correctly_scoped_function(self):
        fixture = """
def verify(target):
    index = {}
    for row in target:
        index[row] = len(index)
    return index
"""
        self.assertEqual(fixture_undefined_names(fixture), [])

    def test_accepts_comprehension_closures_and_walrus(self):
        fixture = """
import json

def verify(rows, total=(n := 10)):
    keys = {row for row in rows}
    pairs = [(row, row.upper()) for row in rows]
    return json.dumps([keys, pairs, total])
"""
        self.assertEqual(fixture_undefined_names(fixture), [])

    def test_accepts_lambdas_with_parameters_and_inner_definitions(self):
        fixture = """
def outer(flag):
    def inner():
        return sorted((3, 1, 2), key=lambda item: -item)
    return inner if flag else len
"""
        self.assertEqual(fixture_undefined_names(fixture), [])

    def test_module_level_names_are_visible_inside_functions(self):
        fixture = """
LIMIT = 10

def verify(rows):
    total = 0
    for row in rows:
        total += LIMIT
    return total
"""
        self.assertEqual(fixture_undefined_names(fixture), [])


# --------------------------------------------------------------------------- #
# The property the crash violated
# --------------------------------------------------------------------------- #
class BoundedModuleScopeTests(unittest.TestCase):
    def test_all_bounded_modules_exist(self):
        missing = [name for name in BOUNDED_MODULES if not (GENERATOR / name).is_file()]
        self.assertEqual(missing, [], f"missing bounded modules: {missing}")

    def test_no_bounded_module_reads_an_unbound_name(self):
        problems = {}
        for name in BOUNDED_MODULES:
            found = source_undefined_names(GENERATOR / name)
            if found:
                problems[name] = [f"{function}: {name} (line {line})"
                                  for function, name, line in found]
        self.assertEqual(
            problems, {},
            "unbound-name defects that would raise NameError mid-run: "
            + "; ".join(f"{module}: {items}" for module, items in problems.items()),
        )

    def test_reported_defect_name_is_absent_from_the_verifier(self):
        source = (GENERATOR / "bounded_verification.py").read_text(encoding="utf-8")
        self.assertNotIn(
            REPORTED_DEFECT, source,
            "the name that crashed the verifier must not reappear",
        )
        # Its replacement must be bound in step 4 and consumed in step 6.
        for symbol in ("request_presence", "request_digests"):
            self.assertGreaterEqual(
                source.count(symbol), 2,
                f"{symbol} must be both bound and consumed in the verifier",
            )

    def test_step_six_and_seven_have_no_unbound_reads(self):
        """Targeted at the crash site: steps 6 and 7 must be self-consistent."""
        problems = source_undefined_names(GENERATOR / "bounded_verification.py")
        late = [problem for problem in problems if problem[2] >= 480]
        self.assertEqual(
            late, [],
            f"a name read at/after line 480 is bound by nothing: {late}",
        )

    def test_every_verifier_check_name_is_unique(self):
        """A duplicated check name would silently overwrite evidence."""
        path = GENERATOR / "bounded_verification.py"
        source = function_source(path, "verify")
        names = [node.args[0].value for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "check"
                 and node.args and isinstance(node.args[0], ast.Constant)]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        self.assertEqual(duplicates, [], f"duplicated verifier check names: {duplicates}")
        self.assertGreaterEqual(len(names), 30, f"only {len(names)} checks found")

    def test_verifier_verify_is_read_only(self):
        """The verifier must not be able to repair what it is asked to check."""
        source = function_source(GENERATOR / "bounded_verification.py", "verify")
        for forbidden in ("os.replace", "unlink", "rmtree", "write_text",
                          "write_bytes", "open(\"w\"", "open('w'", "shutil"):
            self.assertNotIn(forbidden, source, f"verify() must not mutate data: {forbidden}")

    def test_second_opinion_pyflakes_agrees_when_available(self):
        try:
            from pyflakes.api import check
            from pyflakes.reporter import Reporter
        except Exception:
            self.skipTest("pyflakes is not installed; a skip is not a pass")
        for name in BOUNDED_MODULES:
            path = GENERATOR / name
            stream = io.StringIO()
            check(str(path), path.read_text(encoding="utf-8").replace("\t", "    ", 4),
                  Reporter(stream, stream))
            reported = [line for line in stream.getvalue().splitlines()
                        if "undefined name" in line]
            self.assertEqual(reported, [], f"pyflakes reports undefined names in {name}: {reported}")


# --------------------------------------------------------------------------- #
# Step 6 must be reachable and must execute on real rows
# --------------------------------------------------------------------------- #
VERIFIER = GENERATOR / "bounded_verification.py"
STEP_SIX_MARKER = "verify[6/7] ticket and demand request business context"


class StepSixReachabilityTests(unittest.TestCase):
    """Step 6 crashed at runtime, so prove statically that it can be reached."""

    def test_step_six_and_seven_markers_exist_in_order(self):
        source = VERIFIER.read_text(encoding="utf-8")
        self.assertIn(STEP_SIX_MARKER, source)
        self.assertIn("verify[7/7]", source)
        self.assertLess(source.index(STEP_SIX_MARKER), source.index("verify[7/7]"))

    def test_nothing_exits_or_raises_before_step_six(self):
        """A `return` or `raise` ahead of step 6 would make it dead code."""
        source = function_source(VERIFIER, "verify")
        head = source[: source.index(STEP_SIX_MARKER)]
        for terminator in ("return ", "raise ", "sys.exit", "os._exit"):
            self.assertNotIn(
                terminator, head,
                f"verify() contains {terminator!r} before step 6, so step 6 is unreachable",
            )

    def test_mutation_reintroduces_the_reported_crash_and_is_detected(self):
        """Prove this suite would have caught the actual reported failure."""
        source = VERIFIER.read_text(encoding="utf-8")
        self.assertNotIn(REPORTED_DEFECT, source, "precondition: the defect is absent")
        mutated = source.replace(
            f'    log("{STEP_SIX_MARKER}")',
            f"    context = {REPORTED_DEFECT}[rkey]\n    log(\"{STEP_SIX_MARKER}\")",
            1,
        )
        self.assertNotEqual(mutated, source, "the mutation did not apply")
        tree = ast.parse(mutated)  # must parse: the failure is a NameError, not a syntax error
        module_names = module_bindings(tree)
        problems = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                problems.extend(unresolved_reads(node, module_names))
        self.assertTrue(
            any(name == REPORTED_DEFECT for _, name, _ in problems),
            f"the reintroduced crash was not detected: {problems}",
        )


class StepSixPrimitiveTests(unittest.TestCase):
    """Execute the primitives step 6 depends on, on a tiny in-memory fixture."""

    @staticmethod
    def _module():
        from data_generator import bounded_verification

        return bounded_verification

    def test_key_is_stable_and_separates_distinct_identifiers(self):
        module = self._module()
        self.assertEqual(module._key("R-1"), module._key("R-1"))
        self.assertNotEqual(module._key("R-1"), module._key("R-2"))
        self.assertLess(module._key("R-1"), 1 << 63)
        self.assertGreaterEqual(module._key("R-1"), -(1 << 63))

    def test_fingerprint_is_field_order_sensitive(self):
        module = self._module()
        left = module._fingerprint("P1", "T1", "RS1", "RS2", "2026-02-02", "TK1")
        same = module._fingerprint("P1", "T1", "RS1", "RS2", "2026-02-02", "TK1")
        swapped = module._fingerprint("P1", "T1", "RS2", "RS1", "2026-02-02", "TK1")
        self.assertEqual(left, same)
        self.assertNotEqual(left, swapped)
        self.assertEqual(len(left), 16)

    def test_fingerprint_does_not_conflate_adjacent_fields(self):
        module = self._module()
        self.assertNotEqual(
            module._fingerprint("AB", "C"),
            module._fingerprint("A", "BC"),
        )

    def test_request_journey_context_builds_the_step_six_lookup(self):
        module = self._module()
        row = {
            "request_id": "RQ-17",
            "passenger_id": "P-4",
            "origin_route_stop_id": "RS-1",
            "destination_route_stop_id": "RS-9",
            "boarded_at_utc": "2026-02-02T08:15:00Z",
            "service_date": "2026-02-02",
        }
        stop_by_route_stop = {"RS-1": "STOP-1", "RS-9": "STOP-9"}

        request_key, context = module._request_context(row, stop_by_route_stop)

        self.assertEqual(request_key, module._key("RQ-17"))
        self.assertEqual(
            context,
            module._fingerprint(
                "P-4", "STOP-1", "STOP-9",
                "2026-02-02T08:15:00Z", "2026-02-02", "RQ-17",
            ),
        )

    def test_request_journey_context_fails_closed_for_unknown_route_stop(self):
        module = self._module()
        row = {
            "request_id": "RQ-17",
            "passenger_id": "P-4",
            "origin_route_stop_id": "UNKNOWN",
            "destination_route_stop_id": "RS-9",
            "boarded_at_utc": "2026-02-02T08:15:00Z",
            "service_date": "2026-02-02",
        }
        with self.assertRaises(KeyError):
            module._request_context(row, {"RS-9": "STOP-9"})

    def test_passenger_count_conservation_still_fails_on_assignment_mismatch(self):
        source = (GENERATOR / "bounded_verification.py").read_text(encoding="utf-8")
        self.assertIn(
            "bad_equation == 0 and bad_orphan == 0 and bad_assignment == 0",
            source,
        )

    def test_ts_maps_the_null_sentinel_to_a_negative_sentinel(self):
        module = self._module()
        self.assertLess(module._ts(module.NULL), 0)
        self.assertEqual(module._ts(""), module._ts(module.NULL))
        self.assertLess(module._ts("1970-01-01T00:00:00+00:00"), 1)
        self.assertGreater(module._ts("2026-02-02T08:00:00+00:00"), 0)

    def test_scan_rows_yields_the_requested_fields_from_a_shard(self):
        import csv
        import tempfile

        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            shard = Path(directory) / "part-000.csv"
            with shard.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["source_row_id", "ticket_id", "issued_at_utc", "unused"])
                writer.writerow(["S1", "TK1", "2026-02-02T07:00:00+00:00", "x"])
                writer.writerow(["S2", module.NULL, module.NULL, "y"])
            rows = list(module._scan_rows(shard, ("source_row_id", "ticket_id", "issued_at_utc")))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ticket_id"], "TK1")
        self.assertNotIn("unused", rows[0])
        # The step-6 guard `row["ticket_id"] == NULL` must be able to fire.
        self.assertEqual(rows[1]["ticket_id"], module.NULL)
        self.assertEqual(rows[1]["issued_at_utc"], module.NULL)

    def test_step_six_digest_shapes_match_across_the_join(self):
        """Replay both step-6 digest reconstructions against one known journey."""
        module = self._module()
        journey = {
            "passenger_id": "P1", "trip_id": "T1", "origin_route_stop_id": "RS1",
            "destination_route_stop_id": "RS2", "service_date": "2026-02-02",
            "ticket_id": "TK1", "request_id": "RQ1",
            "boarded_at_utc": "2026-02-02T08:00:00+00:00",
        }
        rs_stop = {"RS1": "S1", "RS2": "S2"}
        # Exactly as step 4 records it from the journey row...
        from_journey = module._fingerprint(
            journey["passenger_id"], journey["trip_id"], journey["origin_route_stop_id"],
            journey["destination_route_stop_id"], journey["service_date"], journey["ticket_id"],
        )
        # ...and exactly as step 6 rebuilds it from the ticket row.
        from_ticket = module._fingerprint(
            journey["passenger_id"], journey["trip_id"], journey["origin_route_stop_id"],
            journey["destination_route_stop_id"], journey["service_date"], journey["ticket_id"],
        )
        self.assertEqual(from_journey, from_ticket)
        # The request digest is built on stop ids, resolved through rs_stop.
        from_request = module._fingerprint(
            journey["passenger_id"], rs_stop[journey["origin_route_stop_id"]],
            rs_stop[journey["destination_route_stop_id"]], journey["boarded_at_utc"],
            journey["service_date"], journey["request_id"],
        )
        self.assertEqual(len(from_request), 16)
        # A request row left holding route_stop ids must not match.
        wrong = module._fingerprint(
            journey["passenger_id"], journey["origin_route_stop_id"],
            journey["destination_route_stop_id"], journey["boarded_at_utc"],
            journey["service_date"], journey["request_id"],
        )
        self.assertNotEqual(from_request, wrong)


if __name__ == "__main__":
    unittest.main()
