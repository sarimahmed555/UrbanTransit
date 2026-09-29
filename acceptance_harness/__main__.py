"""Read only explicit evidence; print reports to stdout without writing artifacts."""
import argparse
import json
from pathlib import Path
from .catalog import CATALOG
from .engine import assess
from .report import markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--catalog", action="store_true")
    args = parser.parse_args()
    if args.catalog:
        print(json.dumps([r.public() for r in CATALOG], indent=2))
        return 0
    if not args.manifest:
        parser.error("--manifest is required unless --catalog is selected")
    root = Path(__file__).resolve().parents[1]
    result = assess(args.manifest, project_root=root, fixture=args.fixture)
    print(markdown(result) if args.format == "markdown" else json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["overall_state"] == "PASS" and not args.fixture else 2


if __name__ == "__main__":
    raise SystemExit(main())
