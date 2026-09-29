"""Versioned output naming shared by the safe execution CLIs."""

from __future__ import annotations

from pathlib import Path
import re


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def versioned_output_path(
    output_root: str | Path,
    *,
    stage: str,
    dataset_version: str,
    run_id: str,
) -> Path:
    for name, value in (
        ("stage", stage),
        ("dataset_version", dataset_version),
        ("run_id", run_id),
    ):
        if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
            raise ValueError(f"{name} must be a simple path component")
    return Path(output_root).resolve() / f"stage={stage}" / f"dataset={dataset_version}" / f"run={run_id}"


def create_output_directory(path: str | Path, *, input_root: str | Path) -> Path:
    destination = Path(path).resolve()
    source = Path(input_root).resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Output must not overlap the certified input package")
    if "raw_data" in destination.parts:
        raise ValueError("Output must not target raw_data")
    destination.mkdir(parents=True, exist_ok=False)
    return destination
