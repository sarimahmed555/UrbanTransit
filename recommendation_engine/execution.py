"""Public request/result boundary, including explicit unavailable-input results."""
from .contracts import EvidencePackage, NotReady, not_ready
from .recommendations import generate
from .scenarios import analyze


def execute(mode, manifest, request, *, fixture=False, generated_at=None):
    if not isinstance(request, dict):
        return {**not_ready(mode, "Request must be a JSON object", generated_at), "status": "INVALID_REQUEST"}
    try:
        if request.get("schema_version") != "1.0":
            raise ValueError("Request schema_version must be 1.0")
        package = EvidencePackage(manifest, fixture=fixture)
        if mode == "recommendations":
            return generate(package, request["cases"], request["policy"], generated_at=generated_at)
        if mode == "what-if":
            return analyze(package, request, generated_at=generated_at)
        raise ValueError("Unknown execution mode")
    except NotReady as exc:
        result = not_ready(mode, exc, generated_at)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result = {**not_ready(mode, exc, generated_at), "status": "INVALID_REQUEST"}
    if mode == "what-if":
        result.update(result_type="ESTIMATE", scenario_type=request.get("scenario_type"),
                      entity_ids=request.get("entity_ids"), proposed_changes=request.get("proposed_changes"),
                      original_values=None, assumptions=[], evidence_source=None,
                      limitations=["Required evidence or valid scenario inputs unavailable; no estimates calculated."])
    return result
