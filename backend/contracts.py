"""Public route and response contracts for the analytics service."""

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class EndpointContract:
    name: str
    method: str
    path: str
    capability: str
    description: str
    filters: FrozenSet[str] = frozenset()
    body: bool = False


ANALYTICS_FIELDS = {
    "executiveSummary": ("kpis", "trends"),
    "passengerDemand": ("kpis", "trends", "routeDemand", "stopDemand"),
    "routesAndStops": ("kpis", "routePerformance", "stopRanking"),
    "delayAnalysis": ("kpis", "delayDistribution", "routeReliability"),
    "occupancy": ("kpis", "bands", "persistentSignals"),
    "demandForecast": ("forecastSeries", "evaluationReadiness"),
    "routeClusters": ("clusters", "assignments"),
    "passengerFlow": ("odMatrix", "topPairs", "directionalSummary"),
    "whatIf": ("baseline", "scenario", "assumptions"),
    "recommendations": ("insights", "actionQueue"),
    "dataQuality": ("kpis", "issueFamilies", "reconciliation"),
    "systemStatus": ("pipelineStages", "sources", "events"),
    "systemHealth": ("health",),
    "pipelineStatus": ("pipelineStages",),
    "pipelineComparison": ("comparison",),
}

COMMON_FILTERS = frozenset(
    {"dateRange", "startDate", "endDate", "routeId", "stopId", "direction", "vehicleId", "period"}
)


def _analytics(name: str, path: str, description: str, filters=COMMON_FILTERS) -> EndpointContract:
    return EndpointContract(name, "GET", path, name, description, frozenset(filters))


ENDPOINTS = (
    EndpointContract(
        "health", "GET", "/api/v1/system/health", "systemHealth",
        "Service and artifact adapter health.",
    ),
    EndpointContract(
        "pipelineStatus", "GET", "/api/v1/system/pipeline-status", "pipelineStatus",
        "Certified pipeline stage status.",
    ),
    _analytics("systemStatus", "/api/v1/analytics/system-status", "Pipeline, source, and event status."),
    _analytics("executiveSummary", "/api/v1/analytics/executive-summary", "Executive KPI summary."),
    _analytics("passengerDemand", "/api/v1/analytics/passenger-demand", "Passenger demand analytics."),
    _analytics("routesAndStops", "/api/v1/analytics/routes-stops", "Routes and stops analytics."),
    _analytics("delayAnalysis", "/api/v1/analytics/delay-analysis", "Delay and reliability analytics."),
    _analytics("occupancy", "/api/v1/analytics/occupancy-crowding", "Occupancy and crowding analytics."),
    _analytics("demandForecast", "/api/v1/analytics/demand-forecast", "Forecasting results."),
    _analytics("routeClusters", "/api/v1/analytics/route-clusters", "Route clustering results."),
    _analytics("passengerFlow", "/api/v1/analytics/passenger-flow", "Origin-destination flow analytics."),
    EndpointContract(
        "whatIf", "POST", "/api/v1/analytics/what-if", "whatIf",
        "What-if analysis request and result.", COMMON_FILTERS, True,
    ),
    _analytics("recommendations", "/api/v1/analytics/recommendations", "Evidence-backed recommendations."),
    _analytics("dataQuality", "/api/v1/analytics/data-quality", "Data-quality summary."),
    _analytics(
        "pipelineComparison",
        "/api/v1/analytics/pipeline-comparison",
        "Python-versus-Spark pipeline comparison.",
    ),
)

ROUTES = {(endpoint.method, endpoint.path): endpoint for endpoint in ENDPOINTS}

COMPOSITION_PATHS = (
    ("GET", "/api/v1/system/readiness"),
    ("GET", "/api/v1/system/dataset-status"),
    ("GET", "/api/v1/catalog/{kind}"),
    ("GET", "/api/v1/models/{capability}/summary"),
    ("GET", "/api/v1/dashboard"),
    ("GET", "/api/v1/reports/{report_type}"),
    ("GET", "/api/v1/reports/{report_type}/download"),
)

if len(ROUTES) != len(ENDPOINTS):
    raise ValueError("Duplicate API method/path in endpoint contracts")
if set(ROUTES) & set(COMPOSITION_PATHS):
    raise ValueError("Composition API paths duplicate an endpoint contract")
if len(set(COMPOSITION_PATHS)) != len(COMPOSITION_PATHS):
    raise ValueError("Duplicate API method/path in composition contracts")
