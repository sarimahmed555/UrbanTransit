"""Read-only result artifact adapters.

Artifacts are intentionally injected/configured. This layer never reads raw
transport data and never computes or invents analytics values.
"""

import json
from pathlib import Path
from typing import Any, Mapping


class ArtifactRepository:
    def __init__(self, artifact_root: str | Path):
        self.artifact_root = Path(artifact_root)

    def get(self, capability: str, *, filters: Mapping[str, Any] | None = None, body: Mapping[str, Any] | None = None):
        del body
        path = self.artifact_root / f"{capability}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read result artifact '{path}': {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Result artifact '{path}' must contain a JSON object")
        requested_filters = dict(filters or {})
        if requested_filters and value.get("applied_filters") != requested_filters:
            return None
        return value


class InMemoryArtifactRepository:
    """Tiny fixture adapter for tests and host applications."""

    def __init__(self, artifacts: Mapping[str, Mapping[str, Any]] | None = None):
        self.artifacts = dict(artifacts or {})
        self.requests: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def get(self, capability: str, *, filters=None, body=None):
        self.requests.append((capability, dict(filters or {}), dict(body or {})))
        return self.artifacts.get(capability)
