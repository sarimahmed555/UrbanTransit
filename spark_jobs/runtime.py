"""Structured Spark runtime evidence without embedding business results."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


class EvidenceLogger:
    def __init__(
        self,
        spark,
        output_path: str,
        *,
        pipeline_id: str,
        run_id: str | None = None,
    ):
        self.spark = spark
        self.output_path = output_path
        self.pipeline_id = pipeline_id
        self.run_id = run_id or str(uuid.uuid4())
        self.records = []

    @contextmanager
    def stage(self, name: str, output: str | None = None):
        started = time.time()
        record = {"run_id": self.run_id, "pipeline_id": self.pipeline_id,
                  "stage": name, "output": output, "status": "RUNNING"}
        try:
            yield record
        except Exception as exc:
            record.update(status="FAILED", error_type=type(exc).__name__,
                          error=str(exc), elapsed_sec=time.time() - started)
            self.records.append(record)
            self.flush()
            raise
        else:
            record.update(status="SUCCEEDED", elapsed_sec=time.time() - started)
            self.records.append(record)
            self.flush()

    def record_count(self, record: dict, frame, *, label: str = "rows") -> None:
        record[label] = frame.count()

    def flush(self) -> None:
        payload = self.spark.sparkContext.parallelize(
            [
                json.dumps(record, sort_keys=True, default=_json_default)
                for record in self.records
            ],
            1,
        )
        payload.saveAsTextFile(
            f"{self.output_path}/run_id={self.run_id}/events-{len(self.records)}"
        )
