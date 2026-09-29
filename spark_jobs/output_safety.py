"""Fail-closed Spark output checks and versioned destination construction."""

from __future__ import annotations

import re


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def versioned_spark_path(output_root: str, *, stage: str, dataset_version: str, run_id: str) -> str:
    for name, value in (
        ("stage", stage),
        ("dataset_version", dataset_version),
        ("run_id", run_id),
    ):
        if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
            raise ValueError(f"{name} must be a simple path component")
    return (
        f"{output_root.rstrip('/')}/runs/stage={stage}"
        f"/dataset={dataset_version}/run={run_id}"
    )


def assert_spark_path_absent(
    spark, output_path: str, *, stage: str, run_id: str, dataset_version: str,
    resume_reserved=False,
) -> None:
    if not output_path:
        raise ValueError("Spark output path must be explicit")
    jpath = spark._jvm.org.apache.hadoop.fs.Path(output_path)
    filesystem = jpath.getFileSystem(spark._jsc.hadoopConfiguration())
    lock_path = spark._jvm.org.apache.hadoop.fs.Path(f"{output_path}._execution.lock")
    try:
        output_exists = filesystem.exists(jpath)
        lock_exists = filesystem.exists(lock_path)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot verify output absence for stage={stage}, run={run_id}, "
            f"dataset={dataset_version}: {output_path}"
        ) from exc
    if output_exists or (lock_exists and not resume_reserved):
        raise FileExistsError(
            f"Refusing Spark output collision for stage={stage}, run={run_id}, "
            f"dataset={dataset_version}: {output_path}"
        )


def reserve_spark_output(
    spark, output_path: str, *, stage: str, run_id: str, dataset_version: str,
    resume_reserved=False,
) -> str:
    """Atomically reserve a run destination; persistent locks prevent unsafe retries."""
    jpath = spark._jvm.org.apache.hadoop.fs.Path(output_path)
    filesystem = jpath.getFileSystem(spark._jsc.hadoopConfiguration())
    lock_path = spark._jvm.org.apache.hadoop.fs.Path(f"{output_path}._execution.lock")
    try:
        output_exists = filesystem.exists(jpath)
        lock_exists = filesystem.exists(lock_path)
        if output_exists or (lock_exists and not resume_reserved):
            raise FileExistsError(
                f"Refusing Spark output collision for stage={stage}, run={run_id}, "
                f"dataset={dataset_version}: {output_path}"
            )
        if lock_exists and resume_reserved:
            return str(lock_path)
        if output_exists or lock_exists:
            raise FileExistsError(
                f"Refusing Spark output collision for stage={stage}, run={run_id}, "
                f"dataset={dataset_version}: {output_path}"
            )
        filesystem.mkdirs(jpath.getParent())
        lock_stream = filesystem.create(lock_path, False)
        lock_stream.close()
        if filesystem.exists(jpath):
            raise FileExistsError(
                f"Spark output appeared after reservation for stage={stage}, "
                f"run={run_id}, dataset={dataset_version}: {output_path}"
            )
    except FileExistsError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Cannot atomically reserve output for stage={stage}, run={run_id}, "
            f"dataset={dataset_version}: {output_path}"
        ) from exc
    return str(lock_path)
