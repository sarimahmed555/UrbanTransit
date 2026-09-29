"""Artifact-safe evidence recording without running pipelines."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .schema import EVIDENCE_CATEGORIES, NOT_READY, NOT_RUN, empty_bundle, schema_for


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EvidenceBundle:
    def __init__(self, payload: Mapping[str, Any] | None = None):
        self.payload = json.loads(json.dumps(payload or empty_bundle()))
        self.validate()

    def validate(self) -> None:
        if self.payload.get("schema_version") != "1.0":
            raise ValueError("Unsupported evidence schema version")
        evidence = self.payload.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_CATEGORIES):
            raise ValueError("Evidence bundle must contain every required category")
        for category, record in evidence.items():
            if not isinstance(record, dict) or record.get("status") not in {NOT_RUN, NOT_READY, "READY", "SUCCEEDED", "PASSED", "FAILED"}:
                raise ValueError(f"Invalid status for evidence category '{category}'")

    def category(self, name: str) -> dict[str, Any]:
        if name not in EVIDENCE_CATEGORIES:
            raise KeyError(f"Unknown evidence category: {name}")
        return self.payload["evidence"][name]


class EvidenceRecorder:
    """Creates or updates a JSON evidence bundle; never computes results."""

    def __init__(
        self,
        *,
        dataset_version: str | None = None,
        command: str | None = None,
        input_artifact: str | None = None,
        output_artifact: str | None = None,
        clock=_now,
    ):
        self.clock = clock
        self.bundle = EvidenceBundle()
        now = self.clock()
        self.bundle.payload["created_at_utc"] = now
        self.bundle.payload["updated_at_utc"] = now
        self.bundle.payload["provenance"] = {
            "command": command,
            "dataset_version": dataset_version,
            "input_artifact": input_artifact,
            "output_artifact": output_artifact,
        }

    def record(self, category: str, *, status: str = NOT_RUN, **fields: Any) -> None:
        if category not in EVIDENCE_CATEGORIES:
            raise KeyError(f"Unknown evidence category: {category}")
        record = schema_for(category)
        record.update(fields)
        record["status"] = status
        record["recorded_at_utc"] = self.clock()
        provenance = self.bundle.payload["provenance"]
        for key in ("command", "dataset_version", "input_artifact", "output_artifact"):
            if record.get(key) is None:
                record[key] = provenance[key]
        self.bundle.payload["evidence"][category] = record
        self.bundle.payload["updated_at_utc"] = record["recorded_at_utc"]
        self.bundle.payload["bundle_status"] = (
            "READY" if any(item["status"] in {"READY", "SUCCEEDED", "PASSED"} for item in self.bundle.payload["evidence"].values())
            else NOT_READY
        )
        self.bundle.validate()

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.bundle.payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination
