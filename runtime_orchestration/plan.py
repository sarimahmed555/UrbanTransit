"""SRS-ordered execution plan based only on repository entry points."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys

from .preflight import Status


@dataclass(frozen=True)
class Stage:
    index: int
    name: str
    status: Status
    entrypoint: str | None
    argv: tuple[str, ...] | None
    reason: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    def to_dict(self):
        value = asdict(self)
        value["status"] = self.status.value
        value["argv"] = list(self.argv) if self.argv is not None else None
        value["inputs"] = list(self.inputs)
        value["outputs"] = list(self.outputs)
        return value


STAGE_NAMES = (
    "certified_dataset_gate",
    "runtime_environment_preflight",
    "hdfs_availability",
    "hdfs_directory_readiness",
    "certified_input_publication",
    "spark_schema_ingestion",
    "data_quality_cleaning",
    "spark_sql_integration",
    "partitioned_parquet_outputs",
    "spark_feature_engineering",
    "independent_python_feature_engineering",
    "spark_mllib_execution",
    "independent_python_ml_execution",
    "spark_python_comparison",
    "analytics_recommendation_what_if",
    "postgresql_migration_readiness",
    "backend_api_readiness",
    "evidence_capture",
)


def _check(preflight, name):
    try:
        return preflight.check(name).status
    except (AttributeError, StopIteration):
        return Status.NOT_CONFIGURED


def build_plan(
    *,
    project_root: str | Path,
    preflight,
    certified_root: str | None = None,
    marker_path: str | None = None,
    dataset_version: str | None = None,
    hdfs_root: str | None = None,
    spark_severity_thresholds: tuple[float, float, float, float] | None = None,
    spark_manifest: str | None = None,
    spark_ml_output: str | None = None,
    python_manifest: str | None = None,
    python_ml_output: str | None = None,
    comparison_manifest: str | None = None,
    recommendation_manifest: str | None = None,
    recommendation_request: str | None = None,
    recommendation_output: str | None = None,
):
    root = Path(project_root).resolve()
    cert_status = _check(preflight, "certified_dataset")
    certified_ready = cert_status == Status.READY
    hdfs_root = hdfs_root or "hdfs://localhost:9000/urbantransit"
    from urllib.parse import urlsplit
    parsed_hdfs = urlsplit(hdfs_root)
    spark_hdfs_root = hdfs_root if parsed_hdfs.scheme else f"hdfs://localhost:9000{hdfs_root}"
    hdfs_uri_root = spark_hdfs_root
    if parsed_hdfs.scheme == "hdfs":
        hdfs_root = parsed_hdfs.path

    def command_status(required_checks, *, require_cert=True):
        if require_cert and not certified_ready:
            return Status.BLOCKED
        missing = [name for name in required_checks if _check(preflight, name) != Status.READY]
        if missing:
            return Status.MISSING
        return Status.READY

    def stage(index, name, status, entrypoint, argv, reason, inputs=(), outputs=()):
        return Stage(
            index,
            name,
            status,
            entrypoint,
            tuple(argv) if argv is not None else None,
            reason,
            tuple(map(str, inputs)),
            tuple(map(str, outputs)),
        )

    stages = []
    stages.append(
        stage(
            1,
            STAGE_NAMES[0],
            Status.READY if certified_ready else cert_status,
            None,
            None,
            "Requires an authoritative dataset-owner certification adapter; no marker schema is defined here.",
            (certified_root or "", marker_path or ""),
        )
    )
    stages.append(
        stage(
            2,
            STAGE_NAMES[1],
            Status.READY if preflight.ready else Status.BLOCKED,
            "runtime_orchestration preflight",
            None,
            "Preflight report is supplied to this plan; not rerun as a stage.",
        )
    )
    stages.append(
        stage(
            3,
            STAGE_NAMES[2],
            _check(preflight, "hdfs_availability"),
            None,
            None,
            "No approved read-only HDFS service-availability probe is implemented; configured CLI/XML are not proof of service availability.",
        )
    )
    dirs = root / "hdfs_scripts" / "prepare_urbantransit_dirs.sh"
    safe_hdfs_root = (
        isinstance(hdfs_root, str)
        and hdfs_root.startswith("/")
        and not any(char.isspace() or ord(char) < 32 for char in hdfs_root)
    )
    safe_dataset_version = bool(
        isinstance(dataset_version, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", dataset_version)
        and ".." not in dataset_version
    )
    stages.append(
        stage(
            4,
            STAGE_NAMES[3],
            command_status(("hdfs_cli", "hdfs_configuration"))
            if safe_hdfs_root
            else Status.BLOCKED,
            str(dirs.relative_to(root)) if dirs.is_file() else None,
            (
                "env",
                f"HDFS_ROOT={hdfs_root}",
                "bash",
                str(dirs),
            )
            if dirs.is_file() and safe_hdfs_root
            else None,
            "Existing script runs only hdfs dfs -mkdir -p; execution still awaits an HDFS availability gate.",
            outputs=(f"{hdfs_uri_root}",),
        )
    )
    ingest = root / "hdfs_scripts" / "ingest_certified_dataset.sh"
    publish_argv = None
    if certified_root and marker_path and safe_dataset_version and safe_hdfs_root:
        publish_argv = (
            "env",
            f"HDFS_ROOT={hdfs_root}",
            "bash",
            str(ingest),
            certified_root,
            marker_path,
            dataset_version,
            "--dry-run",
        )
    stages.append(
        stage(
            5,
            STAGE_NAMES[4],
            Status.BLOCKED
            if _check(preflight, "hdfs_availability") != Status.READY or publish_argv is None
            else Status.READY,
            str(ingest.relative_to(root)) if ingest.is_file() else None,
            publish_argv,
            (
                "Non-overwriting versioned publisher is available in dry-run mode; actual publication "
                "remains blocked until an approved HDFS availability gate passes."
                if publish_argv
                else "Publisher requires an explicit certified root, marker, dataset version, and safe HDFS root."
            ),
            (certified_root or "", marker_path or ""),
            (
                f"{hdfs_uri_root.rstrip('/')}/raw/{dataset_version}"
                if dataset_version
                else f"{hdfs_uri_root.rstrip('/')}/raw",
            ),
        )
    )

    spark_pipeline = root / "spark_jobs" / "pipeline.py"
    pipeline_argv = None
    if certified_root and spark_severity_thresholds and safe_dataset_version:
        # Every option below is required by spark_jobs/pipeline.py itself; a
        # generated command must be runnable as printed.
        pipeline_argv = (
            sys.executable,
            "-m",
            "spark_jobs.pipeline",
            "--certified-root",
            certified_root,
            "--hdfs-root",
            spark_hdfs_root,
            "--dataset-version",
            dataset_version,
            "--severity-thresholds-sec",
            *(str(value) for value in spark_severity_thresholds),
        )
    for index, name, entrypoint, argv, reason in (
        (
            6,
            STAGE_NAMES[5],
            spark_pipeline,
            pipeline_argv,
            "Spark pipeline reads typed CSV schemas but there is no separate, confirmed cluster spark-submit resource/command.",
        ),
        (
            7,
            STAGE_NAMES[6],
            spark_pipeline,
            pipeline_argv,
            "DQ hooks are bundled into the Spark pipeline; no standalone cleaning CLI exists.",
        ),
        (
            8,
            STAGE_NAMES[7],
            root / "spark_jobs" / "integration.py",
            pipeline_argv,
            "Integration functions are bundled in the Spark pipeline; standalone SQL script expects registered temporary views and is not an executable input loader.",
        ),
        (
            9,
            STAGE_NAMES[8],
            spark_pipeline,
            pipeline_argv,
            "DQ/features are written under the run-scoped HDFS namespace after every destination is checked for absence and exclusively reserved; the blocker is the unverified cluster submission resource.",
        ),
        (
            10,
            STAGE_NAMES[9],
            spark_pipeline,
            pipeline_argv,
            "Spark feature derivations are executable only inside the combined pipeline; its split destinations are preflighted and reserved before any write, and the blocker is the unverified cluster submission resource.",
        ),
    ):
        stages.append(
            stage(
                index,
                name,
                Status.BLOCKED if pipeline_argv else Status.NOT_CONFIGURED,
                str(entrypoint.relative_to(root)) if entrypoint.is_file() else None,
                argv,
                reason,
                inputs=(certified_root or "",),
                outputs=(
                    f"{hdfs_uri_root}/curated",
                    f"{hdfs_uri_root}/features",
                ),
            )
        )

    stages.append(
        stage(
            11,
            STAGE_NAMES[10],
            Status.NOT_CONFIGURED,
            "python -m python_pipeline",
            None,
            "Python execution CLI is prepared; run only after supplying an authoritative certification adapter and explicit paths. Full-scale bounded materialization remains unverified.",
            (certified_root or "",),
        )
    )

    if spark_manifest and spark_ml_output:
        spark_ml_argv = (
            sys.executable,
            "-m",
            "ml_execution",
            "--engine",
            "spark",
            "--manifest",
            spark_manifest,
            "--output",
            spark_ml_output,
        )
    else:
        spark_ml_argv = None
    spark_ml_status = (
        Status.NOT_CONFIGURED
        if spark_ml_argv is None
        else Status.BLOCKED
        if _check(preflight, "spark_runtime_compatibility") != Status.READY
        else command_status(("pyspark", "java"))
    )
    stages.append(
        stage(
            12,
            STAGE_NAMES[11],
            spark_ml_status,
            "python -m ml_execution --engine spark",
            spark_ml_argv,
            "Existing CLI requires an independently certified Spark feature manifest and a new output directory; runtime compatibility remains untested.",
            (spark_manifest or "",),
            (spark_ml_output or "",),
        )
    )

    if python_manifest and python_ml_output:
        python_ml_argv = (
            sys.executable,
            "-m",
            "ml_execution",
            "--engine",
            "python",
            "--manifest",
            python_manifest,
            "--output",
            python_ml_output,
        )
    else:
        python_ml_argv = None
    stages.append(
        stage(
            13,
            STAGE_NAMES[12],
            command_status(("numpy", "scikit_learn"))
            if python_ml_argv
            else Status.NOT_CONFIGURED,
            "python -m ml_execution --engine python",
            python_ml_argv,
            "Existing CLI requires an independently certified Python feature manifest and a new output directory.",
            (python_manifest or "",),
            (python_ml_output or "",),
        )
    )
    stages.append(
        stage(
            14,
            STAGE_NAMES[13],
            Status.NOT_CONFIGURED,
            "python -m runtime_orchestration.compare",
            None,
            "Comparison CLI is prepared; requires independently certified measured Spark and Python result artifacts with at least 100 shared unseen test cases.",
            (comparison_manifest or "",),
        )
    )

    rec_argv = None
    if recommendation_manifest and recommendation_request and recommendation_output:
        rec_argv = (
            sys.executable,
            "-m",
            "recommendation_engine",
            "recommendations",
            "--manifest",
            recommendation_manifest,
            "--request",
            recommendation_request,
            "--output",
            recommendation_output,
        )
    stages.append(
        stage(
            15,
            STAGE_NAMES[14],
            command_status((), require_cert=True) if rec_argv else Status.NOT_CONFIGURED,
            "python -m recommendation_engine recommendations",
            rec_argv,
            "Recommendation/what-if CLI exists; requires certified analytics manifest, request, and new output path. Analytics result publication is not orchestrated.",
            (recommendation_manifest or "", recommendation_request or ""),
            (recommendation_output or "",),
        )
    )
    stages.append(
        stage(
            16,
            STAGE_NAMES[15],
            Status.NOT_CONFIGURED,
            "python -m backend.serving migrate|readiness",
            None,
            "Migration/readiness CLI exists over the existing apply_migrations and PostgresDatabase implementations; the orchestrator cannot invoke it because driver, environment and connectivity are never probed here.",
        )
    )
    stages.append(
        stage(
            17,
            STAGE_NAMES[16],
            Status.NOT_CONFIGURED,
            "backend.fastapi_app.create_app",
            None,
            "FastAPI factory exists, but no deployable application composition/ASGI launcher or persistent AuthRepository is supplied.",
        )
    )
    stages.append(
        stage(
            18,
            STAGE_NAMES[17],
            Status.READY,
            "runtime_orchestration evidence logger",
            None,
            "Orchestrator evidence schema and non-overwriting capture are prepared; stage evidence is written only when the guarded runner is used.",
        )
    )
    return tuple(stages)
