"""Safe, explicit paths and immutable pipeline configuration."""

from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_DATASET_PARTS = frozenset({"production-v1"})


@dataclass(frozen=True)
class PipelinePaths:
    project_root: Path
    dataset_version: str = "production-v1"

    @property
    def raw_root(self) -> Path:
        return self.project_root / "raw_data" / self.dataset_version

    @property
    def processed_root(self) -> Path:
        return self.project_root / "processed_data" / self.dataset_version / "python"

    @property
    def analytical_root(self) -> Path:
        return self.project_root / "parquet_data" / self.dataset_version / "python"

    @property
    def evidence_root(self) -> Path:
        return self.project_root / "reports" / "python" / self.dataset_version

    def validate(self) -> None:
        if not self.project_root.is_absolute():
            raise ValueError("project_root must be absolute")
        if self.dataset_version in FORBIDDEN_DATASET_PARTS:
            # The production version may be represented in contracts, but raw
            # discovery performs the hard no-access check.
            return

    def raw_table_dir(self, table: str) -> Path:
        if not table or Path(table).name != table or table.startswith("."):
            raise ValueError("table must be a simple non-hidden directory name")
        self.validate()
        return self.raw_root / table


def default_paths(project_root: Path | str) -> PipelinePaths:
    paths = PipelinePaths(Path(project_root).resolve())
    paths.validate()
    return paths


@dataclass(frozen=True)
class Split:
    name: str
    start: str
    end: str


SPLIT_OUTPUT_ROOTS = ("processed_data", "parquet_data", "reports")
