"""Bounded read-only loader and acceptance state machine. Never discovers datasets."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from evidence_framework.schema import READY_STATUSES
from .catalog import CATALOG
from .evaluators import evaluate, MissingEvidence, FailedEvidence

STATES = ("PASS", "FAIL", "PENDING_RUNTIME", "NOT_APPLICABLE", "BLOCKED")
MAX_BYTES = 4 * 1024 * 1024


def _json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Evidence JSON exceeds the bounded 4-MiB contract")
    return (json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number"))),
            hashlib.sha256(raw).hexdigest())


def _pointer(value, pointer):
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Evidence pointer must be a JSON pointer")
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not token.isdigit() or len(token) > 1 and token.startswith("0"):
                raise ValueError("Invalid array pointer")
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _nonempty(obj, keys):
    for key in keys:
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            raise MissingEvidence(f"Missing evidence provenance: {key}")


def _date(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Evidence timestamp requires explicit timezone")


def _gate(record, req, manifest, fixture):
    if not isinstance(record, dict):
        raise ValueError("Evidence record must be an object")
    failed = record.get("status") in {"FAILED", "FAIL"}
    if not failed and (record.get("status") not in READY_STATUSES or record.get("status") == "READY"):
        raise MissingEvidence("No successful executed/reviewed evidence; readiness is not execution")
    expected = "FIXTURE" if fixture else ("CERTIFIED_RUNTIME" if req.evidence_kind == "runtime" else "REVIEWED_ARTIFACT")
    if record.get("evidence_scope") != expected:
        raise MissingEvidence(f"Requires {expected} evidence; code/fixture presence cannot establish compliance")
    provenance = record.get("provenance", {})
    _nonempty(provenance, ("source_revision",))
    if provenance["source_revision"] != manifest.get("source_revision"):
        raise ValueError("Evidence source revision differs from requested acceptance snapshot")
    if req.evidence_kind == "runtime":
        _nonempty(provenance, ("run_id", "command", "recorded_at_utc", "dataset_version"))
        _date(provenance["recorded_at_utc"])
        if provenance["dataset_version"] != manifest.get("dataset_version"):
            raise ValueError("Evidence dataset differs from acceptance dataset")
    else:
        _nonempty(provenance, ("reviewer", "reviewed_at_utc", "review_reference"))
        _date(provenance["reviewed_at_utc"])
    refs = provenance.get("artifact_references")
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x.strip() for x in refs):
        raise MissingEvidence("Traceable runtime/review artifact references are absent")
    if failed:
        raise FailedEvidence("Source reports a failed execution/review")
    facts = record.get("facts")
    if not isinstance(facts, dict):
        raise MissingEvidence("Measured facts/review findings are absent; no values inferred from code")
    return facts


def assess(manifest_path, *, project_root, fixture=False, generated_at=None):
    root = Path(project_root).resolve()
    path = Path(manifest_path).resolve()
    error = None
    manifest = {}
    def permitted(p):
        if not p.is_relative_to(root):
            raise ValueError("Evidence path escapes project root")
        relative = p.relative_to(root)
        if any(part in {"raw_data", "data_generator", ".git"} for part in relative.parts) or any(part.startswith("production") for part in relative.parts):
            raise ValueError("Raw/production-prefixed paths are outside this bounded evidence reader")
        if p.suffix != ".json":
            raise ValueError("Only explicitly selected JSON evidence summaries are read")
    try:
        permitted(path)
        if not path.is_file():
            error = ("PENDING_RUNTIME", "Acceptance evidence manifest absent")
        else:
            manifest, _ = _json(path)
            if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0" or not isinstance(manifest.get("checks"), dict):
                raise ValueError("Invalid acceptance manifest schema")
            _nonempty(manifest, ("dataset_version", "source_revision"))
            unknown = set(manifest["checks"]) - {r.id for r in CATALOG}
            if unknown:
                raise ValueError("Manifest contains unknown requirement IDs; review catalog mapping")
    except (OSError, ValueError, TypeError) as exc:
        error = ("BLOCKED", f"Cannot safely interpret evidence manifest: {exc}")
        if not isinstance(manifest, dict):
            manifest = {}
    rows, cache = [], {}
    for req in CATALOG:
        row = {**req.public(), "state": "PENDING_RUNTIME", "reason": "Evidence not supplied",
               "source": None, "blocked_by": [], "acceptance_eligible": not fixture}
        if error:
            row["state"], row["reason"] = error
        elif req.id in manifest["checks"]:
            try:
                spec = manifest["checks"][req.id]
                source = (path.parent / spec["path"]).resolve()
                permitted(source)
                row["source"] = {"path": str(source.relative_to(root)), "pointer": spec.get("pointer", ""), "sha256": spec["sha256"]}
                if not source.is_file():
                    raise MissingEvidence("Referenced runtime/review evidence artifact absent")
                if source not in cache:
                    cache[source] = _json(source)
                payload, actual_hash = cache[source]
                if actual_hash != spec["sha256"]:
                    raise ValueError("Evidence hash mismatch; source changed or reference is stale")
                record = _pointer(payload, spec.get("pointer", ""))
                facts = _gate(record, req, manifest, fixture)
                row["state"], row["reason"] = evaluate(req, facts)
            except MissingEvidence as exc:
                row["state"], row["reason"] = "PENDING_RUNTIME", str(exc)
            except FailedEvidence as exc:
                row["state"], row["reason"] = "FAIL", str(exc)
            except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                row["state"], row["reason"] = "BLOCKED", f"Malformed/inconsistent evidence: {type(exc).__name__}: {exc}"
        rows.append(row)
    by_id = {r["id"]: r for r in rows}
    # Catalog is ordered so prerequisites are resolved before their dependents.
    for row in rows:
        dependencies = [by_id[k] for k in row["depends_on"] if by_id[k]["state"] not in {"PASS", "NOT_APPLICABLE"}]
        if dependencies and row["state"] != "FAIL":
            row["blocked_by"] = [d["id"] for d in dependencies]
            if any(d["state"] in {"FAIL", "BLOCKED"} for d in dependencies):
                row["state"] = "BLOCKED"
                row["reason"] = "Prerequisite failure/integrity blocker; downstream evidence cannot establish acceptance"
            elif row["state"] == "PASS":
                row["state"] = "PENDING_RUNTIME"
                row["reason"] = "Own criterion met, but required upstream evidence is pending"
    counts = {state: sum(r["state"] == state for r in rows) for state in STATES}
    mandatory = [r for r in rows if r["basis"] == "SRS_MANDATORY"]
    overall = "PASS"
    for state in ("FAIL", "BLOCKED", "PENDING_RUNTIME"):
        if any(r["state"] == state for r in mandatory):
            overall = state
            break
    if fixture:
        overall = "PENDING_RUNTIME"
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    _date(stamp)
    return {"schema_version": "1.0", "report_type": "SRS_ACCEPTANCE", "scope": "FIXTURE_ONLY" if fixture else "REAL_EVIDENCE",
            "catalog_sha256": hashlib.sha256(json.dumps([r.public() for r in CATALOG], sort_keys=True).encode()).hexdigest(),
            "generated_at": stamp, "dataset_version": manifest.get("dataset_version"),
            "source_revision": manifest.get("source_revision"), "overall_state": overall,
            "counts": counts, "checks": rows,
            "blockers": [{"id": r["id"], "reason": r["reason"], "blocked_by": r["blocked_by"]} for r in rows if r["state"] in {"FAIL", "BLOCKED"}],
            "actions": [{"id": r["id"], "state": r["state"], "action": "Resolve failure/integrity issue and supply fresh evidence" if r["state"] in {"FAIL", "BLOCKED"} else "Publish executed/reviewed evidence with provenance and source hash",
                         "missing_or_failed": r["reason"]} for r in rows if r["state"] not in {"PASS", "NOT_APPLICABLE"}],
            "limitations": ["PASS evaluates supplied evidence, not independently replayed workloads or forensic authenticity.",
                            "No source/data execution, database connection, network check, Git command or evidence mutation occurs.",
                            "Grouped checks do not mechanically cover all SRS clauses; full-SRS review remains a separate mandatory gate.",
                            "Fixture PASS rows are not eligible for real acceptance."]}
