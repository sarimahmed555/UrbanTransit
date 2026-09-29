"""Adapter boundary for the authoritative dataset certification package.

The repository intentionally does not define or parse a certification-marker
schema here. A dataset owner supplies the adapter after its schema is approved.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from pathlib import Path
import re
from typing import Protocol


@dataclass(frozen=True)
class CertificationAttestation:
    dataset_version: str
    evidence_reference: str


class CertificationAdapter(Protocol):
    def certify(self, dataset_root: Path, marker_path: Path) -> CertificationAttestation:
        """Validate the authoritative certificate/package and return its identity."""


def load_certification_adapter(reference: str) -> CertificationAdapter:
    try:
        module_name, attribute = reference.split(":", 1)
    except ValueError as exc:
        raise ValueError("Adapter must use the form module:object") from exc
    if not module_name or not attribute:
        raise ValueError("Adapter must use the form module:object")
    adapter = getattr(importlib.import_module(module_name), attribute)
    adapter = adapter() if isinstance(adapter, type) else adapter
    if not callable(getattr(adapter, "certify", None)):
        raise TypeError("Certification adapter must provide certify(dataset_root, marker_path)")
    return adapter


def require_certified_package(
    dataset_root: str | Path,
    marker_path: str | Path,
    adapter: CertificationAdapter,
) -> tuple[CertificationAttestation, str]:
    root = Path(dataset_root).resolve()
    marker = Path(marker_path).resolve()
    if "raw_data" in marker.parts:
        raise ValueError("Certification marker must not be under raw_data")
    if not root.is_dir() or not (root / "raw").is_dir():
        raise FileNotFoundError(f"Certified package root/raw directory is missing: {root}")
    if not marker.is_file():
        raise FileNotFoundError(f"Certification marker is missing: {marker}")
    attestation = adapter.certify(root, marker)
    if not isinstance(attestation, CertificationAttestation):
        raise TypeError("Certification adapter returned an invalid attestation")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", attestation.dataset_version):
        raise ValueError("Certification adapter returned an unsafe dataset version")
    if not attestation.evidence_reference.strip():
        raise ValueError("Certification adapter returned no evidence reference")
    digest = hashlib.sha256(marker.read_bytes()).hexdigest()
    return attestation, digest
