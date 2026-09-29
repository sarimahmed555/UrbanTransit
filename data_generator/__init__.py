"""UrbanTransit IQ deterministic synthetic dataset generator."""

from .config import GeneratorConfig, default_smoke, production
from .ids import entity_id, operational_departure_id, trip_id

__all__ = ["GeneratorConfig", "default_smoke", "production", "entity_id", "operational_departure_id", "trip_id"]
__version__ = "1.0.0"
