"""Read-only analytical inputs; writes a new requested result JSON, never raw data."""
import argparse
import json
from pathlib import Path
from .contracts import not_ready
from .execution import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("recommendations", "what-if"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request).read_text())
        result = execute(args.mode, args.manifest, request, fixture=args.fixture)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result = {**not_ready(args.mode, exc), "status": "INVALID_REQUEST"}
    output = Path(args.output).resolve()
    if "raw_data" in output.parts:
        parser.error("Cannot write results into raw_data")
    with output.open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+"\n")
    print(json.dumps({"status": result["status"], "output": str(output)}))
    return 0 if result["status"] in {"SUCCEEDED", "FIXTURE_TESTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
