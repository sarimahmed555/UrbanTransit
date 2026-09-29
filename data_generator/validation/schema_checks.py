"""File, header, physical-row, and raw-envelope schema checks."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from ..schemas import TABLE_COLUMNS, all_columns, column_names
from .common import ValidationReport, as_date, as_float, as_int, clean_rows, load_injections, load_table, parse_ts, table_paths


def _type_valid(value: str, type_name: str) -> bool:
    if type_name in {"STR", "ID"}:
        return True
    if type_name == "TS":
        return parse_ts(value) is not None
    if type_name == "DATE":
        return as_date(value) is not None
    if type_name == "INT":
        return as_int(value) is not None
    if type_name.startswith("DEC"):
        return as_float(value) is not None
    if type_name == "BOOL":
        return str(value).lower() in {"true", "false"}
    return True


def _json_type_valid(value, type_name: str) -> bool:
    if value is None:
        return True
    if type_name in {"STR", "ID", "TS", "DATE"}:
        return isinstance(value, str)
    if type_name == "INT":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name.startswith("DEC"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "BOOL":
        return isinstance(value, bool)
    return True


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    expected_tables = list(TABLE_COLUMNS.keys())
    for table in expected_tables:
        paths = table_paths(root, table)
        report.add(f"file_exists:{table}", bool(paths), f"raw/{table}/part-*.csv")
        if not paths:
            continue
        expected = column_names(table, raw=True)
        header_failures = []
        ordinal_failures = []
        metadata_failures = []
        hash_failures = []
        source_ids = []
        for path in paths:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                if header != expected:
                    header_failures.append((path.name, len(header), [item for item in expected if item not in header], [item for item in header if item not in expected]))
                shard_rows = list(reader)
            shard_ids = [row.get("source_row_id") for row in shard_rows]
            source_ids.extend(shard_ids)
            shard_ordinals = [int(row.get("row_ordinal")) for row in shard_rows if row.get("row_ordinal") is not None]
            if shard_ordinals != list(range(1, len(shard_rows) + 1)):
                ordinal_failures.append((path.name, len(shard_rows)))
            required_meta = ("dataset_version", "source_id", "source_row_id", "raw_file", "raw_bytes_sha256", "raw_record_text", "parse_status")
            missing_meta = [row.get("source_row_id") for row in shard_rows if any(row.get(key) in (None, "") for key in required_meta)]
            if missing_meta:
                metadata_failures.append((path.name, missing_meta[:5]))
            bad_hash = [row.get("source_row_id") for row in shard_rows if hashlib.sha256(str(row.get("raw_record_text", "")).encode("utf-8")).hexdigest() != row.get("raw_bytes_sha256")]
            if bad_hash:
                hash_failures.append((path.name, bad_hash[:5]))
        report.add(f"schema_columns:{table}", not header_failures, "all shards have the declared header", bad=header_failures[:5])
        report.add(f"physical_source_row_unique:{table}", len(source_ids) == len(set(source_ids)), "source_row_id is unique per physical row", rows=len(source_ids), unique=len(set(source_ids)))
        report.add(f"raw_ordinals:{table}", not ordinal_failures, "physical ordinals are contiguous within each shard", bad=ordinal_failures)
        report.add(f"raw_envelope:{table}", not metadata_failures, "raw envelope metadata is present", bad=metadata_failures)
        report.add(f"raw_hash_integrity:{table}", not hash_failures, "raw record text hashes match the recorded digest", bad=hash_failures)
        type_failures = []
        declarations = {column.name: column for column in all_columns(table, raw=True)}
        for row in clean_rows(load_table(root, table), table, injection_sources):
            for name, column in declarations.items():
                value = row.get(name)
                if value is None or value == "":
                    if not column.nullable and name not in {"correction_time", "unresolved_reason"}:
                        type_failures.append((row.get("source_row_id"), name, "required_null"))
                    continue
                if not _type_valid(str(value), column.type):
                    type_failures.append((row.get("source_row_id"), name, column.type))
        report.add(f"types_and_nullability:{table}", not type_failures, "valid canonical rows conform to declared types and nullability", bad=type_failures[:10])
    for table in ("gps_events", "context_events"):
        jsonl_paths = sorted((root / "raw" / table).glob("part-*.jsonl"))
        report.add(f"jsonl_exists:{table}", bool(jsonl_paths), str(root / "raw" / table))
        if jsonl_paths:
            jsonl_count = 0
            type_failures = []
            provenance_failures = []
            null_token_failures = []
            declarations = {column.name: column.type for column in all_columns(table, raw=True)}
            csv_paths = table_paths(root, table)
            for csv_path, jsonl_path in zip(csv_paths, jsonl_paths):
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    csv_rows = list(csv.DictReader(handle))
                json_rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                jsonl_count += len(json_rows)
                for index, json_row in enumerate(json_rows):
                    if index >= len(csv_rows):
                        provenance_failures.append((jsonl_path.name, index + 1, "missing_csv_row"))
                        continue
                    csv_row = csv_rows[index]
                    if set(json_row) != set(csv_row):
                        provenance_failures.append((jsonl_path.name, index + 1, "field_set"))
                    for name, type_name in declarations.items():
                        if name in json_row and not _json_type_valid(json_row.get(name), type_name):
                            type_failures.append((jsonl_path.name, index + 1, name, type_name))
                    for name in ("source_row_id", "raw_file", "row_ordinal", "raw_bytes_sha256", "raw_record_text"):
                        if json_row.get(name) != csv_row.get(name):
                            # CSV values are strings; compare the JSON scalar
                            # to its canonical textual representation.
                            expected = csv_row.get(name)
                            if str(json_row.get(name)) != str(expected):
                                provenance_failures.append((jsonl_path.name, index + 1, name))
                    if any(value == r"\N" for value in json_row.values()):
                        null_token_failures.append((jsonl_path.name, index + 1))
            csv_count = len(load_table(root, table))
            report.add(f"jsonl_row_count:{table}", jsonl_count == csv_count, "JSONL mirrors CSV rows", jsonl_rows=jsonl_count, csv_rows=csv_count)
            report.add(f"jsonl_typed_values:{table}", not type_failures, "JSONL mirror values follow the executable schema types", bad=type_failures[:10])
            report.add(f"jsonl_provenance:{table}", not provenance_failures, "JSONL mirror rows retain canonical source-row provenance", bad=provenance_failures[:10])
            report.add(f"jsonl_null_semantics:{table}", not null_token_failures, "JSONL mirrors use JSON null rather than the CSV null token", bad=null_token_failures[:10])
    report.add("generation_manifest_exists", (root / "metadata/generation_manifest.json").exists())
    report.add("schema_snapshot_exists", (root / "metadata/schemas.json").exists())
