"""Explicit permission mapping for the existing API catalog; unknown routes deny."""
from .contracts import Permission, ROLE_PERMISSIONS, Principal, SecurityError, unauthorized

ANALYTICS_CAPABILITIES = frozenset({
    "executiveSummary", "passengerDemand", "routesAndStops", "delayAnalysis", "occupancy",
    "demandForecast", "routeClusters", "passengerFlow", "recommendations",
    "systemHealth", "pipelineStatus", "systemStatus",
})
DIAGNOSTIC_CAPABILITIES = frozenset({"dataQuality", "pipelineComparison"})


def required_permission(endpoint):
    if endpoint.method == "GET" and endpoint.capability in ANALYTICS_CAPABILITIES:
        return Permission.ANALYTICS_READ
    if endpoint.method == "GET" and endpoint.capability in DIAGNOSTIC_CAPABILITIES:
        return Permission.DIAGNOSTICS_READ
    if endpoint.method == "POST" and endpoint.capability == "whatIf":
        return Permission.SCENARIOS_EXECUTE
    raise SecurityError(403, "forbidden", "Access denied")


def require_permission(principal, permission):
    if not isinstance(principal, Principal):
        raise unauthorized()
    if permission not in ROLE_PERMISSIONS.get(principal.role, frozenset()):
        raise SecurityError(403, "forbidden", "Access denied")
    return principal
