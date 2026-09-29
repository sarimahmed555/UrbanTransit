"""Run certified Python ML with python -m, or Spark MLlib through spark-submit."""
import argparse
import json
from ml_execution.contracts import pending
from ml_execution.runner import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True, help="New run directory; overwriting is forbidden")
    parser.add_argument("--engine", choices=("python", "spark"), default="python")
    parser.add_argument("--fixture", action="store_true", help="Synthetic fixture only; cannot emit certified evidence")
    parser.add_argument("--max-rows", type=int, default=100000)
    args = parser.parse_args()
    if args.max_rows < 1:
        parser.error("max-rows must be positive")
    try:
        result = execute(args.manifest, args.output, engine=args.engine, fixture=args.fixture, max_rows=args.max_rows)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        result = pending(None, f"Invalid feature contract: {exc}")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] in {"SUCCEEDED", "FIXTURE_TESTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
