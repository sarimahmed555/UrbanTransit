"""Output contracts only; creation is explicit and never writes production data."""

from dataclasses import dataclass
from pathlib import Path

from .config import PipelinePaths


@dataclass(frozen=True)
class OutputContract:
    paths: PipelinePaths

    def staging_table(self, table: str) -> Path:
        return self.paths.processed_root / "staging" / table

    def clean_table(self, table: str) -> Path:
        return self.paths.processed_root / "clean" / table

    def quarantine_table(self, table: str) -> Path:
        return self.paths.processed_root / "quarantine" / table

    def audit(self, name: str) -> Path:
        return self.paths.processed_root / "audit" / name

    def split(self, task: str, split: str) -> Path:
        if split not in {"train", "validation", "test"}:
            raise ValueError(f"Unknown chronological split: {split}")
        return self.paths.processed_root / "ml" / task / split

    def evidence(self, name: str) -> Path:
        return self.paths.evidence_root / name


def output_contract(paths: PipelinePaths) -> OutputContract:
    paths.validate()
    return OutputContract(paths)
