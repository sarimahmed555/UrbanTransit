"""Non-fabricating assertions for certified runtime evidence."""

from dataclasses import dataclass
from typing import Any, Mapping

from .schema import NOT_READY, NOT_RUN, READY_STATUSES


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    passed: bool | None
    detail: str
    evidence: Mapping[str, Any]


def _not_run(name: str, detail: str) -> CheckResult:
    return CheckResult(name, NOT_RUN, None, detail, {})


def evaluate_thresholds(
    record: Mapping[str, Any] | None,
    *,
    minimum_rows: int | None = None,
    minimum_agreement: float | None = None,
    required_metrics: tuple[str, ...] = (),
) -> tuple[CheckResult, ...]:
    """Evaluate thresholds; absent/non-ready evidence is never treated as pass."""
    if not record or record.get("status") not in READY_STATUSES:
        return (_not_run("evidence_ready", "Runtime evidence is not available."),)
    results = []
    if minimum_rows is not None:
        rows = record.get("rows_processed")
        results.append(
            CheckResult(
                "minimum_rows",
                "PASSED" if isinstance(rows, (int, float)) and rows >= minimum_rows else "FAILED",
                bool(isinstance(rows, (int, float)) and rows >= minimum_rows),
                f"Requires at least {minimum_rows} processed rows.",
                {"observed": rows, "required": minimum_rows},
            )
        )
    if minimum_agreement is not None:
        agreement = record.get("agreement", {}).get("rate")
        results.append(
            CheckResult(
                "minimum_agreement",
                "PASSED" if isinstance(agreement, (int, float)) and agreement >= minimum_agreement else "FAILED",
                bool(isinstance(agreement, (int, float)) and agreement >= minimum_agreement),
                f"Requires agreement rate >= {minimum_agreement}.",
                {"observed": agreement, "required": minimum_agreement},
            )
        )
    for metric in required_metrics:
        value = record.get("metrics", {}).get(metric)
        results.append(
            CheckResult(
                f"metric:{metric}",
                "PASSED" if value is not None else "FAILED",
                value is not None,
                f"Requires recorded metric '{metric}'.",
                {"observed": value},
            )
        )
    return tuple(results)


def require_certified_run(record: Mapping[str, Any] | None, *, category: str) -> None:
    if not record or record.get("status") not in READY_STATUSES:
        status = record.get("status") if record else NOT_READY
        raise AssertionError(f"{category} evidence is {status}; a certified runtime is required")
