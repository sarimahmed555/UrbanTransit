#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 /absolute/certified/package /absolute/certification-marker DATASET_VERSION [--dry-run]" >&2
  exit 2
}

[[ $# -ge 3 && $# -le 4 ]] || usage
CERTIFIED_ROOT="$1"
MARKER="$2"
DATASET_VERSION="$3"
DRY_RUN="${4:-}"
HDFS_ROOT="${HDFS_ROOT:-/urbantransit}"
HDFS_BIN="${HDFS_BIN:-hdfs}"

[[ -z "${DRY_RUN}" || "${DRY_RUN}" == "--dry-run" ]] || usage
[[ "${CERTIFIED_ROOT}" == /* && "${MARKER}" == /* ]] || {
  echo "Certified package and certification marker paths must be absolute" >&2
  exit 2
}
[[ "${HDFS_ROOT}" == /* && ! "${HDFS_ROOT}" =~ [[:space:][:cntrl:]] ]] || {
  echo "HDFS_ROOT must be an absolute HDFS path without whitespace/control characters" >&2
  exit 2
}
[[ "${DATASET_VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "${DATASET_VERSION}" != *..* ]] || {
  echo "Dataset version must be a simple path component" >&2
  exit 2
}
case "${CERTIFIED_ROOT}" in
  */raw_data/*|*/raw_data) echo "Refusing to publish directly from raw_data" >&2; exit 2 ;;
esac
[[ -d "${CERTIFIED_ROOT}/raw" ]] || {
  echo "Certified package raw/ directory is missing: ${CERTIFIED_ROOT}/raw" >&2
  exit 2
}
[[ -f "${MARKER}" ]] || {
  echo "Certification marker is missing: ${MARKER}" >&2
  exit 2
}

HDFS_ROOT="${HDFS_ROOT%/}"
BASE="${HDFS_ROOT}/raw"
DESTINATION="${BASE}/${DATASET_VERSION}"

show_command() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

if [[ "${DRY_RUN}" == "--dry-run" ]]; then
  echo "DRY_RUN: HDFS destination will be ${DESTINATION}; no HDFS command was run."
  show_command "${HDFS_BIN}" dfs -test -d "${BASE}"
  show_command "${HDFS_BIN}" dfs -test -e "${DESTINATION}"
  show_command "${HDFS_BIN}" dfs -mkdir "${DESTINATION}"
  show_command "${HDFS_BIN}" dfs -put "${CERTIFIED_ROOT}/raw" "${DESTINATION}/"
  show_command "${HDFS_BIN}" dfs -put "${MARKER}" "${DESTINATION}/_CERTIFIED"
  exit 0
fi

if ! "${HDFS_BIN}" dfs -test -d "${BASE}"; then
  echo "HDFS preflight failed: base directory is unavailable: ${BASE}" >&2
  exit 2
fi

set +e
"${HDFS_BIN}" dfs -test -e "${DESTINATION}"
test_status=$?
set -e
if [[ ${test_status} -eq 0 ]]; then
  echo "Publication refused: HDFS dataset version already exists: ${DESTINATION}" >&2
  exit 3
elif [[ ${test_status} -ne 1 ]]; then
  echo "HDFS preflight failed while checking destination (exit ${test_status}): ${DESTINATION}" >&2
  exit 2
fi

# mkdir without -p is an exclusive second collision guard after the preflight.
if ! "${HDFS_BIN}" dfs -mkdir "${DESTINATION}"; then
  echo "Publication refused: destination could not be exclusively created: ${DESTINATION}" >&2
  exit 3
fi
if ! "${HDFS_BIN}" dfs -put "${CERTIFIED_ROOT}/raw" "${DESTINATION}/"; then
  echo "Publication failed; partial destination retained for operator review: ${DESTINATION}" >&2
  exit 4
fi
if ! "${HDFS_BIN}" dfs -put "${MARKER}" "${DESTINATION}/_CERTIFIED"; then
  echo "Publication failed while copying the marker; destination retained: ${DESTINATION}" >&2
  exit 4
fi

printf 'Certified package published without overwrite at %s\n' "${DESTINATION}"
