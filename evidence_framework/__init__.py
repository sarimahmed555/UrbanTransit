"""Machine-readable runtime evidence contracts and threshold checks."""

from .checks import CheckResult, evaluate_thresholds, require_certified_run
from .recorder import EvidenceBundle, EvidenceRecorder
from .schema import (
    EVIDENCE_CATEGORIES,
    NOT_READY,
    NOT_RUN,
    READY_STATUSES,
    schema_for,
)

__all__ = [
    "CheckResult",
    "EVIDENCE_CATEGORIES",
    "EvidenceBundle",
    "EvidenceRecorder",
    "NOT_READY",
    "NOT_RUN",
    "READY_STATUSES",
    "evaluate_thresholds",
    "require_certified_run",
    "schema_for",
]
