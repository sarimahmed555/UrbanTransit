"""Adapter-gated HDFS publication with exclusive local evidence capture."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from evidence_framework import EvidenceRecorder
from .certification import (
    CertificationAdapter,
    require_certified_package,
)
from .output_safety import create_output_directory, versioned_output_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_hashes(raw_root: Path):
    hashes, total_bytes = {}, 0
    for source in sorted(path for path in raw_root.rglob("*") if path.is_file()):
        relative = source.relative_to(raw_root).as_posix()
        hashes[relative] = _sha256(source)
        total_bytes += source.stat().st_size
    return hashes, total_bytes


def _write_new(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _write_new_text(path: Path, value: str):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value)


def execute_publication(
    *,
    certified_root: str | Path,
    marker_path: str | Path,
    adapter: CertificationAdapter,
    hdfs_root: str,
    output_root: str | Path,
    run_id: str | None = None,
    execute: bool = False,
    confirm_hdfs_available: bool = False,
    executor=subprocess.run,
) -> dict:
    run_id = run_id or str(uuid.uuid4())
    started_at, started = _now(), time.perf_counter()
    if execute and not confirm_hdfs_available:
        raise ValueError(
            "Actual publication requires --confirm-hdfs-available after an approved service check"
        )
    if (
        not isinstance(hdfs_root, str)
        or not hdfs_root.startswith("/")
        or any(char.isspace() or ord(char) < 32 for char in hdfs_root)
    ):
        raise ValueError("HDFS root must be an absolute path without whitespace/control characters")

    certificate = None
    marker_hash = None
    package_hashes, package_bytes = {}, 0
    destination = None
    status, failure_reason = "NOT_READY", None
    stdout, stderr, returncode = "", "", None
    dataset_version = "UNATTESTED"
    try:
        certificate, marker_hash = require_certified_package(
            certified_root, marker_path, adapter
        )
        dataset_version = certificate.dataset_version
        destination = versioned_output_path(
            output_root,
            stage="hdfs_publication",
            dataset_version=dataset_version,
            run_id=run_id,
        )
        destination = create_output_directory(destination, input_root=certified_root)
        package_hashes, package_bytes = _package_hashes(
            Path(certified_root).resolve() / "raw"
        )
        if not package_hashes:
            raise ValueError("Certified package raw/ contains no files to publish")
        root = Path(__file__).resolve().parents[1]
        script = root / "hdfs_scripts" / "ingest_certified_dataset.sh"
        command = [
            "bash",
            str(script),
            str(Path(certified_root).resolve()),
            str(Path(marker_path).resolve()),
            certificate.dataset_version,
        ]
        if not execute:
            command.append("--dry-run")
        env = None
        import os

        env = os.environ.copy()
        env["HDFS_ROOT"] = hdfs_root
        started_process = time.perf_counter()
        process = executor(
            command,
            cwd=root,
            env=env,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
        returncode = process.returncode
        stdout, stderr = process.stdout or "", process.stderr or ""
        status = (
            ("SUCCEEDED" if execute else "DRY_RUN")
            if returncode == 0
            else "FAILED"
        )
        if status == "FAILED":
            failure_reason = f"Publisher exited with status {returncode}"
    except FileExistsError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        failure_reason = f"{type(exc).__name__}: {exc}"
        status = "NOT_READY" if certificate is None else "FAILED"
    except Exception as exc:
        failure_reason = f"Unexpected {type(exc).__name__}: {exc}"
        status = "NOT_READY" if certificate is None else "FAILED"

    ended_at, elapsed = _now(), time.perf_counter() - started
    if destination is None:
        destination = versioned_output_path(
            output_root,
            stage="hdfs_publication",
            dataset_version=dataset_version,
            run_id=run_id,
        )
        destination = create_output_directory(destination, input_root=certified_root)
    stdout_path, stderr_path = destination / "stdout.log", destination / "stderr.log"
    _write_new_text(stdout_path, stdout)
    _write_new_text(stderr_path, stderr)
    root = Path(__file__).resolve().parents[1]
    command = [
        "bash",
        str(root / "hdfs_scripts" / "ingest_certified_dataset.sh"),
        str(Path(certified_root).resolve()),
        str(Path(marker_path).resolve()),
        certificate.dataset_version if certificate else "<unattested>",
    ]
    if not execute:
        command.append("--dry-run")
    result = {
        "schema_version": "1.0",
        "stage": "hdfs_publication",
        "status": status,
        "command": command,
        "run_id": run_id,
        "dataset_version": certificate.dataset_version if certificate else None,
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "runtime_sec": elapsed,
        "input_artifact": str(Path(certified_root).resolve()),
        "output_artifact": (
            f"hdfs://{hdfs_root.rstrip('/')}/raw/{certificate.dataset_version}"
            if certificate
            else None
        ),
        "marker_sha256": marker_hash,
        "input_sha256": package_hashes,
        "input_file_count": len(package_hashes),
        "input_bytes": package_bytes,
        "returncode": returncode,
        "stdout_file": stdout_path.name,
        "stderr_file": stderr_path.name,
        "failure_reason": failure_reason,
        "dry_run": not execute,
    }
    _write_new(destination / "run.json", result)
    recorder = EvidenceRecorder(
        dataset_version=result["dataset_version"],
        command=" ".join(command),
        input_artifact=result["input_artifact"],
        output_artifact=result["output_artifact"],
    )
    recorder.record(
        "hdfs_ingestion",
        status="NOT_READY" if status == "DRY_RUN" else status,
        source=result["input_artifact"],
        hdfs_paths=[result["output_artifact"]] if result["output_artifact"] else [],
        row_counts={},
        timings_sec={"runtime": elapsed},
        input_sha256=package_hashes,
        marker_sha256=marker_hash,
        started_at_utc=started_at,
        ended_at_utc=ended_at,
        returncode=returncode,
        failure_reason=failure_reason,
        stdout_file=stdout_path.name,
        stderr_file=stderr_path.name,
        notes=[
            f"source_files={len(package_hashes)}",
            f"source_bytes={package_bytes}",
            "Dry run does not publish data." if not execute else "HDFS publication command completed.",
        ],
    )
    _write_new(destination / "evidence.json", recorder.bundle.payload)
    result["evidence_artifact"] = str(destination / "evidence.json")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certified-root", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--certification-adapter", required=True, metavar="MODULE:OBJECT")
    parser.add_argument("--hdfs-root", default="/urbantransit")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-hdfs-available", action="store_true")
    args = parser.parse_args(argv)
    try:
        from .certification import load_certification_adapter

        adapter = load_certification_adapter(args.certification_adapter)
        result = execute_publication(
            certified_root=args.certified_root,
            marker_path=args.marker,
            adapter=adapter,
            hdfs_root=args.hdfs_root,
            output_root=args.output_root,
            run_id=args.run_id,
            execute=args.execute,
            confirm_hdfs_available=args.confirm_hdfs_available,
        )
    except Exception as exc:
        print(f"HDFS publication setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"SUCCEEDED", "DRY_RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
