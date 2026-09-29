"""Fail-closed sequential stage runner with exclusive local evidence capture."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable

from .plan import Stage
from .preflight import PreflightReport, Status


_SAFE_PYTHON_MODULES = {
    ("-m", "ml_execution"),
    ("-m", "recommendation_engine"),
}
_SAFE_SCRIPTS = {
    "hdfs_scripts/prepare_urbantransit_dirs.sh",
    "hdfs_scripts/ingest_certified_dataset.sh",
}
_FORBIDDEN_WORDS = re.compile(
    r"(^|[/\s])(?:rm|format|namenode|truncate)(?:$|[/\s])|--force|--overwrite|\boverwrite\b|(?:^|\s)-f(?:$|\s)",
    re.IGNORECASE,
)
_REDACT = re.compile(
    r"(?i)(password|token|secret|dsn|authorization)(\s*[:=]\s*)(\S+)"
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_log_text(value):
    return _REDACT.sub(r"\1\2[REDACTED]", value)


def validate_command(argv, *, project_root):
    if not isinstance(argv, (tuple, list)) or not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        raise ValueError("Stage command must be a nonempty argv array")
    if any(item in {";", "&&", "||", "|", ">", ">>", "<", "$("} for item in argv):
        raise ValueError("Shell syntax is forbidden; commands are executed without a shell")
    if "--fixture" in argv:
        raise ValueError("Fixture mode is forbidden in the production execution plan")
    command_text = " ".join(argv)
    if _FORBIDDEN_WORDS.search(command_text):
        raise ValueError("Destructive/overwrite command rejected")
    for item in argv:
        parts = Path(item).parts
        if "raw_data" in parts or "acceptance_harness" in parts:
            raise ValueError("Commands may not use protected data/harness paths")
        if "reports" in parts and any(part.startswith("production") for part in parts):
            raise ValueError("Commands may not use production report paths")
    if argv[0] == sys.executable and len(argv) >= 3 and tuple(argv[1:3]) in _SAFE_PYTHON_MODULES:
        return
    if (
        argv[0] == "env"
        and len(argv) >= 4
        and argv[1].startswith("HDFS_ROOT=/")
        and not any(char.isspace() or ord(char) < 32 for char in argv[1].split("=", 1)[1])
        and argv[2] == "bash"
    ):
        return validate_command(argv[2:], project_root=project_root)
    if argv[0] == "bash" and len(argv) == 2:
        script = Path(argv[1]).resolve()
        root = Path(project_root).resolve()
        if script.is_relative_to(root) and script.relative_to(root).as_posix() in _SAFE_SCRIPTS:
            return
    if argv[0] == "bash" and len(argv) == 6:
        script = Path(argv[1]).resolve()
        root = Path(project_root).resolve()
        if (
            script.is_relative_to(root)
            and script.relative_to(root).as_posix() == "hdfs_scripts/ingest_certified_dataset.sh"
            and argv[-1] == "--dry-run"
        ):
            return
    raise ValueError("Command is not an allowlisted repository entry point")


class StageRunner:
    def __init__(
        self,
        *,
        project_root: str | Path,
        executor: Callable | None = None,
        path_exists: Callable[[Path], bool] | None = None,
        remote_path_exists: Callable[[str], bool] | None = None,
        clock: Callable[[], str] = _utc_now,
    ):
        self.project_root = Path(project_root).resolve()
        repository_root = Path(__file__).resolve().parents[1]
        if self.project_root != repository_root:
            raise ValueError("Runner project_root must be this UrbanTransit-IQ repository")
        self.executor = executor or subprocess.run
        self.path_exists = path_exists or (lambda path: path.exists())
        self.remote_path_exists = remote_path_exists or (lambda path: True)
        self.clock = clock

    def run(
        self,
        stages: tuple[Stage, ...],
        *,
        certificate: dict,
        preflight: PreflightReport,
        dataset_root: str | Path,
        evidence_root: str | Path,
        dry_run: bool = False,
    ) -> dict:
        self._validate_stage_order(stages)
        input_root = Path(dataset_root).resolve()
        evidence_dir = Path(evidence_root).resolve()
        if "raw_data" in evidence_dir.parts or "acceptance_harness" in evidence_dir.parts:
            raise ValueError("Evidence output path is protected")
        if "reports" in evidence_dir.parts and any(
            part.startswith("production") for part in evidence_dir.parts
        ):
            raise ValueError("Production report paths are protected")
        if evidence_dir == input_root or evidence_dir.is_relative_to(input_root):
            raise ValueError("Evidence output must not be inside the certified dataset")
        if input_root == evidence_dir or input_root.is_relative_to(evidence_dir):
            raise ValueError("Evidence output must not contain the certified dataset")
        if evidence_dir.exists():
            raise FileExistsError(f"Evidence directory already exists: {evidence_dir}")
        evidence_dir.parent.mkdir(parents=True, exist_ok=True)
        evidence_dir.mkdir(mode=0o700, exist_ok=False)
        records_path = evidence_dir / "stages.jsonl"
        completed, stopped = [], False
        with records_path.open("x", encoding="utf-8") as records:
            if not isinstance(certificate, dict) or certificate.get("status") != "CERTIFIED":
                record = self._gate_record(
                    1,
                    "certified_dataset_gate",
                    None,
                    "Certified dataset gate rejected input",
                    stage=stages[0],
                )
                self._write_record(records, record)
                completed.append(record)
                self._record_remaining_blocked(
                    records,
                    completed,
                    stages[1:],
                    None,
                    "Certified dataset gate rejected input",
                )
                return self._blocked_result(evidence_dir, completed)
            if not isinstance(certificate.get("dataset_version"), str) or not certificate["dataset_version"]:
                record = self._gate_record(
                    1,
                    "certified_dataset_gate",
                    None,
                    "Certification marker has no dataset_version",
                    stage=stages[0],
                )
                self._write_record(records, record)
                completed.append(record)
                self._record_remaining_blocked(
                    records,
                    completed,
                    stages[1:],
                    None,
                    "Certification marker has no dataset_version",
                )
                return self._blocked_result(evidence_dir, completed)
            try:
                certification_check = preflight.check("certified_dataset")
            except StopIteration:
                certification_check = None
            if not certification_check or certification_check.status != Status.READY:
                record = self._gate_record(
                    1,
                    "certified_dataset_gate",
                    certificate["dataset_version"],
                    "Certified package/marker preflight check did not pass",
                    stage=stages[0],
                )
                self._write_record(records, record)
                completed.append(record)
                self._record_remaining_blocked(
                    records,
                    completed,
                    stages[1:],
                    certificate["dataset_version"],
                    "Certified package/marker preflight check did not pass",
                )
                return self._blocked_result(evidence_dir, completed)
            if not preflight.ready:
                record = self._gate_record(
                    1,
                    "certified_dataset_gate",
                    certificate["dataset_version"],
                    None,
                    status="SUCCEEDED",
                    stage=stages[0],
                )
                self._write_record(records, record)
                completed.append(record)
                record = self._gate_record(
                    2,
                    "runtime_environment_preflight",
                    certificate["dataset_version"],
                    "Required runtime preflight checks are not READY",
                    stage=stages[1],
                )
                self._write_record(records, record)
                completed.append(record)
                self._record_remaining_blocked(
                    records,
                    completed,
                    stages[2:],
                    certificate["dataset_version"],
                    "Runtime preflight gate did not pass",
                )
                return self._blocked_result(evidence_dir, completed)
            for stage in stages:
                if stopped:
                    record = self._record(
                        stage, "BLOCKED", certificate, "A prior mandatory stage did not complete"
                    )
                    self._write_record(records, record)
                    completed.append(record)
                    continue
                if stage.status != Status.READY:
                    record = self._record(
                        stage, "BLOCKED", certificate, stage.reason
                    )
                    self._write_record(records, record)
                    completed.append(record)
                    stopped = True
                    continue
                if stage.argv is None:
                    if stage.name in {
                        "certified_dataset_gate",
                        "runtime_environment_preflight",
                        "hdfs_availability",
                        "evidence_capture",
                    }:
                        record = self._record(stage, "SUCCEEDED", certificate)
                        self._write_record(records, record)
                        completed.append(record)
                        continue
                    record = self._record(
                        stage, "BLOCKED", certificate, "No executable command is defined"
                    )
                    self._write_record(records, record)
                    completed.append(record)
                    stopped = True
                    continue
                try:
                    self._validate_stage_command(stage)
                    collision = self._output_collision(stage, input_root)
                    if collision:
                        raise FileExistsError(collision)
                except (ValueError, FileExistsError) as exc:
                    record = self._record(stage, "BLOCKED", certificate, str(exc))
                    self._write_record(records, record)
                    completed.append(record)
                    stopped = True
                    continue
                record = self._record(stage, "DRY_RUN" if dry_run else "RUNNING", certificate)
                record["argv"] = list(stage.argv)
                if dry_run:
                    self._write_record(records, record)
                    completed.append(record)
                    continue
                stdout_path = evidence_dir / f"{stage.index:02d}-{stage.name}.stdout.log"
                stderr_path = evidence_dir / f"{stage.index:02d}-{stage.name}.stderr.log"
                started = time.monotonic()
                record["started_at_utc"] = self.clock()
                try:
                    result = self.executor(
                        list(stage.argv),
                        cwd=self.project_root,
                        shell=False,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    stdout = _safe_log_text(result.stdout or "")
                    stderr = _safe_log_text(result.stderr or "")
                    self._write_exclusive(stdout_path, stdout)
                    self._write_exclusive(stderr_path, stderr)
                    status = "SUCCEEDED" if result.returncode == 0 else "FAILED"
                    record.update(
                        status=status,
                        returncode=result.returncode,
                        elapsed_sec=time.monotonic() - started,
                        ended_at_utc=self.clock(),
                        stdout_file=stdout_path.name,
                        stderr_file=stderr_path.name,
                    )
                    if status == "FAILED":
                        record["reason"] = "Mandatory stage exited nonzero"
                        stopped = True
                except Exception as exc:
                    self._write_exclusive(stdout_path, "")
                    self._write_exclusive(stderr_path, "")
                    record.update(
                        status="FAILED",
                        reason=f"Stage launch failed ({type(exc).__name__})",
                        elapsed_sec=time.monotonic() - started,
                        ended_at_utc=self.clock(),
                        stdout_file=stdout_path.name,
                        stderr_file=stderr_path.name,
                    )
                    stopped = True
                self._write_record(records, record)
                completed.append(record)
        final_status = (
            "FAILED"
            if any(record["status"] == "FAILED" for record in completed)
            else "BLOCKED"
            if any(record["status"] == "BLOCKED" for record in completed)
            else "DRY_RUN"
            if dry_run
            else "SUCCEEDED"
        )
        return {
            "status": final_status,
            "dataset_version": certificate["dataset_version"],
            "evidence_dir": str(evidence_dir),
            "stages": completed,
        }

    def _output_collision(self, stage, input_root):
        if stage.name == "hdfs_directory_readiness":
            return None
        for value in stage.outputs:
            if not value:
                continue
            if value.startswith(("hdfs://", "viewfs://")):
                if self.remote_path_exists(value):
                    return f"HDFS output already exists or could not be verified absent: {value}"
                continue
            output = Path(value).resolve()
            if "raw_data" in output.parts:
                return "Stage output may not target raw_data"
            if output == input_root or output.is_relative_to(input_root):
                return "Stage output overlaps certified input"
            if self.path_exists(output):
                return f"Stage output already exists: {output}"
        return None

    @staticmethod
    def _validate_stage_order(stages):
        indexes = [stage.index for stage in stages]
        if indexes != list(range(1, len(stages) + 1)):
            raise ValueError("Stages must be in unique, contiguous mandatory order")
        from .plan import STAGE_NAMES

        if tuple(stage.name for stage in stages) != STAGE_NAMES:
            raise ValueError("Execution plan must preserve the complete mandatory stage order")

    def _validate_stage_command(self, stage):
        validate_command(stage.argv, project_root=self.project_root)
        argv = stage.argv
        expected = {
            "hdfs_directory_readiness": (
                argv[0] == "env"
                and len(argv) == 4
                and argv[1].startswith("HDFS_ROOT=")
                and argv[2] == "bash"
                and Path(argv[3]).resolve()
                == (self.project_root / "hdfs_scripts/prepare_urbantransit_dirs.sh").resolve()
            ),
            "certified_input_publication": (
                argv[0] == "env"
                and len(argv) == 8
                and argv[1].startswith("HDFS_ROOT=")
                and argv[2] == "bash"
                and Path(argv[3]).resolve()
                == (self.project_root / "hdfs_scripts/ingest_certified_dataset.sh").resolve()
                and argv[-1] == "--dry-run"
            ),
            "spark_mllib_execution": (
                argv[0] == sys.executable
                and tuple(argv[1:3]) == ("-m", "ml_execution")
                and "--engine" in argv
                and argv[argv.index("--engine") + 1 : argv.index("--engine") + 2]
                == ("spark",)
            ),
            "independent_python_ml_execution": (
                argv[0] == sys.executable
                and tuple(argv[1:3]) == ("-m", "ml_execution")
                and "--engine" in argv
                and argv[argv.index("--engine") + 1 : argv.index("--engine") + 2]
                == ("python",)
            ),
            "analytics_recommendation_what_if": (
                argv[0] == sys.executable
                and tuple(argv[1:3]) == ("-m", "recommendation_engine")
                and len(argv) > 3
                and argv[3] in {"recommendations", "what-if"}
            ),
        }
        if not expected.get(stage.name, False):
            raise ValueError("Command does not match its mandatory stage entry point")

    def _record(self, stage, status, certificate, reason=None):
        value = {
            "stage_index": stage.index,
            "stage": stage.name,
            "status": status,
            "dataset_version": certificate.get("dataset_version"),
            "entrypoint": stage.entrypoint,
            "argv": list(stage.argv) if stage.argv else None,
            "inputs": list(stage.inputs),
            "outputs": list(stage.outputs),
            "started_at_utc": self.clock(),
            "ended_at_utc": self.clock(),
            "elapsed_sec": 0.0,
            "reason": reason,
            "returncode": None,
        }
        return value

    @staticmethod
    def _write_exclusive(path, text):
        with path.open("x", encoding="utf-8") as output:
            output.write(text)

    @staticmethod
    def _write_record(stream, record):
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()

    def _gate_record(
        self, index, name, dataset_version, reason, *, status="BLOCKED", stage=None
    ):
        now = self.clock()
        return {
            "stage_index": index,
            "stage": name,
            "status": status,
            "dataset_version": dataset_version,
            "entrypoint": stage.entrypoint if stage else None,
            "argv": list(stage.argv) if stage and stage.argv else None,
            "inputs": list(stage.inputs) if stage else [],
            "outputs": list(stage.outputs) if stage else [],
            "started_at_utc": now,
            "ended_at_utc": now,
            "elapsed_sec": 0.0,
            "reason": reason,
            "returncode": None,
        }

    @staticmethod
    def _blocked_result(evidence_dir, stages):
        return {
            "status": "BLOCKED",
            "evidence_dir": str(evidence_dir),
            "stages": stages,
        }

    def _record_remaining_blocked(
        self, stream, completed, stages, dataset_version, reason
    ):
        for stage in stages:
            record = self._gate_record(
                stage.index,
                stage.name,
                dataset_version,
                reason,
                stage=stage,
            )
            self._write_record(stream, record)
            completed.append(record)
