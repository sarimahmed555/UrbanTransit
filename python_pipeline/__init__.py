"""Independent Pandas pipeline foundations for UrbanTransit IQ.

This package reads immutable raw source files directly. It never imports or
consumes Spark outputs, fitted models, predictions, or derived Spark data.
"""

from .config import PipelinePaths, Split, default_paths
from .splits import SPLITS, assign_split, assert_no_future_leakage

__all__ = [
    "PipelinePaths",
    "Split",
    "SPLITS",
    "assign_split",
    "assert_no_future_leakage",
    "default_paths",
]
