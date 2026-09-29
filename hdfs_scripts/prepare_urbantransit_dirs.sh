#!/usr/bin/env bash
set -euo pipefail

HDFS_ROOT="${HDFS_ROOT:-/urbantransit}"

hdfs dfs -mkdir -p \
  "${HDFS_ROOT}/raw" \
  "${HDFS_ROOT}/staging" \
  "${HDFS_ROOT}/curated" \
  "${HDFS_ROOT}/features" \
  "${HDFS_ROOT}/analytics" \
  "${HDFS_ROOT}/models" \
  "${HDFS_ROOT}/evidence" \
  "${HDFS_ROOT}/_checkpoints"

printf 'Prepared HDFS directory contract at %s\n' "${HDFS_ROOT}"
