"""Versioned analytical artifact boundary; no raw-data or model execution."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

from evidence_framework.schema import NOT_READY
from ml_execution.contracts import finite
from ml_execution.results import read_result


class NotReady(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("generated_at requires an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def not_ready(kind, reason, generated_at=None):
    return {"schema_version": "1.0", "kind": kind, "status": NOT_READY,
            "generated_at": timestamp(generated_at), "reason": str(reason),
            "recommendations": [], "baseline_metrics": None, "estimated_metrics": None,
            "deltas": None, "evidence_status": "PENDING CERTIFIED RUNTIME EVIDENCE"}


def provenance(inputs, policy):
    return {"input_sha256": digest(inputs), "policy_sha256": digest(policy),
            "engine_version": "1.0", "python_version": platform.python_version(),
            "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(Path(__file__).parent.glob("*.py"))},
            "randomness": "none"}


def pointer(document, value):
    if value == "":
        return document
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("Evidence pointer must be an RFC 6901 JSON pointer")
    current = document
    for token in value[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list) and (not key.isdigit() or (len(key) > 1 and key.startswith("0"))):
                raise ValueError("Invalid JSON pointer array index")
            current = current[int(key)] if isinstance(current, list) else current[key]
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise NotReady(f"Analytical evidence pointer absent: {value}") from exc
    return current


class EvidencePackage:
    """Loads only explicitly listed, hash-bound analytical artifacts.

    Generic analytics sources must attest SUCCEEDED/CERTIFIED; ml_execution sources
    reuse that package's existing result validator and artifact readiness checks.
    """
    def __init__(self, manifest_path, *, fixture=False):
        path = Path(manifest_path).resolve()
        if "raw_data" in path.parts:
            raise ValueError("Raw data is not an analytical evidence package")
        if not path.is_file():
            raise NotReady("Analytical evidence manifest absent")
        self.manifest = json.loads(path.read_text())
        m = self.manifest
        if not isinstance(m, dict):
            raise ValueError("Evidence manifest must be a JSON object")
        if m.get("schema_version") != "1.0":
            raise ValueError("Unsupported evidence schema")
        if m.get("status") != ("FIXTURE" if fixture else "CERTIFIED"):
            raise NotReady("Certified analytical evidence is unavailable")
        for key in ("dataset_version", "analytics_version"):
            if not isinstance(m.get(key), str) or not m[key]:
                raise NotReady(f"Missing {key}")
        if not isinstance(m.get("sources"), dict) or not m["sources"]:
            raise NotReady("No source analytical artifacts")
        self.fixture, self.sources, self.references = fixture, {}, {}
        for source_id, spec in m["sources"].items():
            if not isinstance(spec, dict):
                raise ValueError("Source specification must be a JSON object")
            source_path = (path.parent / spec["path"]).resolve()
            if not source_path.is_relative_to(path.parent) or "raw_data" in source_path.parts:
                raise ValueError("Source must remain inside its analytical package")
            if not source_path.is_file():
                raise NotReady(f"Analytical artifact absent: {source_id}")
            raw = source_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec.get("sha256"):
                raise NotReady(f"Analytical artifact hash mismatch: {source_id}")
            if spec.get("kind") == "ml_execution":
                artifact = read_result(source_path, allow_fixture=fixture)
            elif spec.get("kind") == "analytics":
                artifact = json.loads(raw)
                if not isinstance(artifact, dict):
                    raise ValueError("Analytical artifact must be a JSON object")
                if not fixture and artifact.get("certification_status") != "CERTIFIED":
                    raise NotReady(f"Analytical source is uncertified: {source_id}")
            else:
                raise ValueError("Source kind must be analytics or ml_execution")
            if artifact.get("status") != ("FIXTURE_TESTED" if fixture else "SUCCEEDED"):
                raise NotReady(f"Source did not succeed in the required evidence scope: {source_id}")
            if artifact.get("dataset_version") != m["dataset_version"]:
                raise NotReady("Source dataset version mismatch")
            version = artifact.get("analytics_version") if spec["kind"] == "analytics" else artifact.get("feature_version")
            if not version or version != spec.get("version"):
                raise NotReady("Source analytical/feature version mismatch")
            self.sources[source_id] = artifact
            self.references[source_id] = {"source_id": source_id, "artifact": str(source_path),
                                          "sha256": spec["sha256"], "version": version,
                                          "dataset_version": m["dataset_version"]}
        self.manifest_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    def resolve(self, reference):
        if not isinstance(reference, dict):
            raise ValueError("Evidence reference must be a JSON object")
        if reference.get("source_id") not in self.sources:
            raise NotReady("Unknown analytical source")
        source_id = reference["source_id"]
        value = deepcopy(pointer(self.sources[source_id], reference["pointer"]))
        return value, {**self.references[source_id], "pointer": reference["pointer"],
                       "manifest_sha256": self.manifest_sha256}

    def result_base(self, kind, generated_at=None):
        return {"schema_version": "1.0", "kind": kind,
                "status": "FIXTURE_TESTED" if self.fixture else "SUCCEEDED",
                "evidence_status": "FIXTURE-TESTED" if self.fixture else "CERTIFIED_ANALYTICS",
                "dataset_version": self.manifest["dataset_version"],
                "analytics_version": self.manifest["analytics_version"],
                "generated_at": timestamp(generated_at)}


def require_number(mapping, key, *, positive=False):
    if not isinstance(mapping, dict):
        raise NotReady("Required analytical metrics object is missing")
    value = mapping.get(key)
    if not finite(value):
        raise NotReady(f"Required measured value absent/nonfinite: {key}")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid numeric value: {key}")
    return value
