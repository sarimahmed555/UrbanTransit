"""Non-invasive environment and certified-input checks; never starts data jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .certification import (
    CertificationAttestation,
    load_certification_adapter,
    require_certified_package,
)


class Status(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    BLOCKED = "BLOCKED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[Check, ...]

    def __post_init__(self):
        names = [item.name for item in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("Preflight check names must be unique")

    @property
    def ready(self) -> bool:
        return all(
            item.status == Status.READY
            for item in self.checks
            if item.name in REQUIRED_PREFLIGHT
        )

    def to_dict(self) -> dict:
        return {
            "status": "READY" if self.ready else "BLOCKED",
            "checks": [
                {**asdict(item), "status": item.status.value}
                for item in self.checks
            ],
        }

    def check(self, name: str) -> Check:
        return next(item for item in self.checks if item.name == name)


REQUIRED_PREFLIGHT = frozenset(
    {
        "python",
        "java",
        "hadoop_cli",
        "hdfs_cli",
        "hdfs_configuration",
        "hdfs_availability",
        "spark_home",
        "spark_submit",
        "pyspark",
        "spark_runtime_compatibility",
        "pandas",
        "numpy",
        "scikit_learn",
        "pyarrow",
        "fastapi",
        "httpx",
        "postgresql_client",
        "postgresql_driver",
        "postgresql_configuration",
        "postgresql_connectivity",
        "certified_dataset",
        "project_entrypoints",
        "disk_space",
    }
)

PACKAGE_CHECKS = {
    "pyspark": ("pyspark", "pyspark"),
    "pandas": ("pandas", "pandas"),
    "numpy": ("numpy", "numpy"),
    "scikit_learn": ("sklearn", "scikit-learn"),
    "pyarrow": ("pyarrow", "pyarrow"),
    "fastapi": ("fastapi", "fastapi"),
    "httpx": ("httpx", "httpx"),
}

PROJECT_ENTRYPOINTS = {
    "hdfs_directory_setup": "hdfs_scripts/prepare_urbantransit_dirs.sh",
    "hdfs_certified_ingest": "hdfs_scripts/ingest_certified_dataset.sh",
    "spark_pipeline": "spark_jobs/pipeline.py",
    "spark_analytics": "spark_jobs/analytics.py",
    "spark_model": "spark_jobs/model.py",
    "ml_execution_cli": "ml_execution/__main__.py",
    "python_feature_cli": "python_pipeline/__main__.py",
    "comparison_cli": "runtime_orchestration/compare.py",
    "recommendation_cli": "recommendation_engine/__main__.py",
    "serving_migration_callable": "backend/serving/migrate.py",
    "fastapi_factory": "backend/fastapi_app.py",
}


def _which(name: str, path: str | None = None):
    return shutil.which(name, path=path)


def _package_version(module: str, distribution: str):
    if importlib.util.find_spec(module) is None:
        return None
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "installed-version-unknown"


def _java_version(java_path: str | None):
    if not java_path:
        return None
    try:
        process = subprocess.run(
            [java_path, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = process.stderr or process.stdout
    if process.returncode != 0:
        return None
    for line in output.splitlines():
        if "version" in line.lower():
            return line.strip()
    return None


def _hdfs_configuration(env):
    candidates = []
    if env.get("HADOOP_CONF_DIR"):
        candidates.append(Path(env["HADOOP_CONF_DIR"]))
    if env.get("HADOOP_HOME"):
        candidates.append(Path(env["HADOOP_HOME"]) / "etc" / "hadoop")
    for directory in candidates:
        config = directory / "core-site.xml"
        if not config.is_file():
            continue
        try:
            tree = ET.parse(config)
        except (ET.ParseError, OSError):
            return Status.BLOCKED, "core-site.xml is unreadable or invalid"
        for prop in tree.findall(".//property"):
            name = prop.findtext("name")
            value = prop.findtext("value")
            if name == "fs.defaultFS":
                if value and value.startswith(("hdfs://", "viewfs://")):
                    return Status.READY, f"filesystem target configured ({value.split(':', 1)[0]})"
                return Status.BLOCKED, "fs.defaultFS is not an HDFS-compatible URI"
        return Status.NOT_CONFIGURED, "core-site.xml has no fs.defaultFS"
    return Status.NOT_CONFIGURED, "HADOOP_CONF_DIR/HADOOP_HOME Hadoop configuration not found"


def _certified_marker(
    dataset_root: str | None,
    marker_path: str | None,
    certification_adapter=None,
):
    if not dataset_root or not marker_path:
        return Status.NOT_CONFIGURED, "dataset root and explicit certification marker are required"
    if certification_adapter is None:
        return (
            Status.NOT_CONFIGURED,
            "Authoritative certification schema/adapter is pending dataset-owner approval",
        )
    try:
        adapter = (
            load_certification_adapter(certification_adapter)
            if isinstance(certification_adapter, str)
            else certification_adapter
        )
        attestation, _marker_hash = require_certified_package(
            dataset_root, marker_path, adapter
        )
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
        return Status.BLOCKED, f"Certification adapter rejected package: {type(exc).__name__}: {exc}"
    if not isinstance(attestation, CertificationAttestation):
        return Status.BLOCKED, "Certification adapter did not return a valid attestation"
    return Status.READY, (
        f"Certification adapter attested dataset version {attestation.dataset_version}"
    )


def run_preflight(
    *,
    env=None,
    project_root: str | Path | None = None,
    dataset_root: str | None = None,
    marker_path: str | None = None,
    disk_path: str | Path | None = None,
    minimum_free_bytes: int | None = None,
    certification_adapter=None,
    python_version=None,
    which=_which,
    package_version=_package_version,
    java_version=_java_version,
):
    """Probe local configuration only; never invokes Hadoop, HDFS, Spark or DB."""
    env = os.environ if env is None else env
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    checks = []

    python_version = python_version or os.sys.version_info
    checks.append(
        Check(
            "python",
            Status.READY,
            f"{python_version.major}.{python_version.minor}.{python_version.micro}",
        )
        if python_version >= (3, 10)
        else Check("python", Status.MISSING, "Python 3.10 or newer is required"),
    )

    java_home = env.get("JAVA_HOME")
    java_path = which("java", path=env.get("PATH"))
    jversion = java_version(java_path)
    checks.append(
        Check("java", Status.READY, f"{jversion}; JAVA_HOME configured")
        if jversion and java_home and Path(java_home).is_dir()
        else Check(
            "java",
            Status.MISSING if not java_path or not jversion else Status.NOT_CONFIGURED,
            "Java runtime/JAVA_HOME unavailable or not configured",
        )
    )

    hadoop = which("hadoop", path=env.get("PATH"))
    hdfs = which("hdfs", path=env.get("PATH"))
    checks.append(
        Check("hadoop_cli", Status.READY, "hadoop executable found")
        if hadoop
        else Check("hadoop_cli", Status.MISSING, "hadoop executable not found")
    )
    checks.append(
        Check("hdfs_cli", Status.READY, "hdfs executable found")
        if hdfs
        else Check("hdfs_cli", Status.MISSING, "hdfs executable not found")
    )
    status, detail = _hdfs_configuration(env)
    checks.append(Check("hdfs_configuration", status, detail))
    checks.append(
        Check(
            "hdfs_availability",
            Status.NOT_CONFIGURED,
            "HDFS service was not probed; orchestration preflight never runs HDFS commands",
        )
    )

    spark_home = env.get("SPARK_HOME")
    spark_submit = (
        Path(spark_home) / "bin" / "spark-submit"
        if spark_home
        else None
    )
    submit_on_path = which("spark-submit", path=env.get("PATH"))
    checks.append(
        Check("spark_home", Status.READY, "SPARK_HOME configured")
        if spark_home and Path(spark_home).is_dir()
        else Check("spark_home", Status.NOT_CONFIGURED, "SPARK_HOME is not configured")
    )
    checks.append(
        Check("spark_submit", Status.READY, "spark-submit found")
        if (spark_submit and spark_submit.is_file()) or submit_on_path
        else Check("spark_submit", Status.MISSING, "spark-submit executable not found")
    )
    for name, (module, distribution) in PACKAGE_CHECKS.items():
        version = package_version(module, distribution)
        if name == "pyspark" and version:
            detail = f"PySpark {version}; import/runtime compatibility not tested"
            status = Status.READY
        elif version:
            detail, status = f"{distribution} {version}", Status.READY
        else:
            detail, status = f"{distribution} is not installed", Status.MISSING
        checks.append(Check(name, status, detail))
    checks.append(
        Check(
            "spark_runtime_compatibility",
            Status.NOT_CONFIGURED,
            "Spark/JVM compatibility requires an approved runtime smoke check; not run in preflight",
        )
    )

    psql = which("psql", path=env.get("PATH"))
    checks.append(
        Check("postgresql_client", Status.READY, "psql executable found")
        if psql
        else Check("postgresql_client", Status.MISSING, "psql executable not found")
    )
    driver_version = package_version("psycopg2", "psycopg2") or package_version(
        "psycopg", "psycopg"
    )
    checks.append(
        Check("postgresql_driver", Status.READY, "PostgreSQL Python driver available")
        if driver_version
        else Check("postgresql_driver", Status.MISSING, "psycopg/psycopg2 is not installed")
    )
    pg_configured = all(
        env.get(key)
        for key in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    )
    checks.append(
        Check("postgresql_configuration", Status.READY, "PostgreSQL environment configured")
        if pg_configured
        else Check("postgresql_configuration", Status.NOT_CONFIGURED, "PostgreSQL connection environment is incomplete")
    )
    checks.append(
        Check(
            "postgresql_connectivity",
            Status.NOT_CONFIGURED,
            "PostgreSQL connectivity was not tested; orchestration preflight never opens a database connection",
        )
    )

    status, detail = _certified_marker(
        dataset_root, marker_path, certification_adapter
    )
    checks.append(Check("certified_dataset", status, detail))
    missing_entrypoints = [
        name for name, relative in PROJECT_ENTRYPOINTS.items()
        if not (root / relative).is_file()
    ]
    checks.append(
        Check("project_entrypoints", Status.READY, "declared existing entry points found")
        if not missing_entrypoints
        else Check("project_entrypoints", Status.MISSING, ", ".join(missing_entrypoints))
    )

    target = Path(disk_path or root)
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        checks.append(Check("disk_space", Status.BLOCKED, "disk free space could not be read"))
    else:
        if minimum_free_bytes is None:
            checks.append(
                Check(
                    "disk_space",
                    Status.NOT_CONFIGURED,
                    f"{free} bytes available; no approved minimum headroom was supplied",
                )
            )
        elif minimum_free_bytes <= 0:
            checks.append(Check("disk_space", Status.BLOCKED, "approved minimum free bytes must be positive"))
        elif free < minimum_free_bytes:
            checks.append(
                Check("disk_space", Status.BLOCKED, f"{free} bytes available; {minimum_free_bytes} required")
            )
        else:
            checks.append(Check("disk_space", Status.READY, f"{free} bytes available"))

    return PreflightReport(tuple(checks))
