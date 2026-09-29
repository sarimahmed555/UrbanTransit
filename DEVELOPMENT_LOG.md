# Development log

## Foundation entry

- **Date/time:** 2026-09-24T12:37:55+05:00 (Asia/Karachi)
- **Work completed:** Initialized Git; created the requested project folders, directory placeholders, and six root foundation files.
- **Dataset changes:** None; no datasets generated.
- **Data-quality problems:** Not assessed; no datasets available.
- **Spark failures:** Not assessed; no Spark jobs run.
- **Model errors:** Not assessed; no models created or run.
- **Modifications made:** Project foundation only; no system or Hadoop/HDFS, Spark, Java, or SSH configuration changes.
- **Testing completed:** Documentation and structure verification only; no application tests exist yet.
- **Performance improvements:** None; no performance measurements performed.
- **Git commit/reference:** Uncommitted foundation; no commit created.

## Reusable entry template

- **Date/time:** TODO (YYYY-MM-DD HH:MM:SS, timezone)
- **Work completed:** TODO
- **Dataset changes:** TODO
- **Data-quality problems:** TODO
- **Spark failures:** TODO
- **Model errors:** TODO
- **Modifications made:** TODO
- **Testing completed:** TODO
- **Performance improvements:** TODO
- **Git commit/reference:** TODO

## Phase 1 dataset generator entry

- **Date/time:** 2026-09-24T20:13:30+05:00 (Asia/Karachi)
- **Work completed:** Re-ran the modular deterministic generator after independent review and fixed the Phase 1 smoke-path defects: exact production unserved-request budgeting, replacement-flow destination safety, explicit G1 unknown-assignment and G5 unresolved-core fixtures, typed/provenance-checked JSONL mirrors, output-path-invariant logical configuration, deterministic manifest-content hashing, and an executable production target gate. Added regression coverage and updated design/status documentation.
- **Dataset changes:** Generated only `sample_data/smoke/`; no production-scale data and no `raw_data/` output. The package contains 21 transport tables, 3,056 passenger-journey rows (3,055 usable canonical journeys), 3,060 raw ticket rows, 1,618 passenger-count rows, 482 delay rows, 923 GPS rows, and 282 transfer rows. The canonical movement view is `Passenger_Journeys`: 3,055 usable journey movements and 3,054 usable ticket views, counted once with 3,054 overlap. The package is 36,599,644 file bytes (`du -sh`: 36M; 44 files, including 23 raw CSV/JSONL files).
- **Data-quality problems:** Seeded 18 sparse controlled fixtures: all 16 mandatory DQ families, explicit C01 missing-required-value, and one quarantined G5 unresolved-core-trip capability fixture. The expected reconciliation is 15 quarantined rows, 2 duplicate removals, and 1 accepted-flagged journey; these are generator validation/oracle fixtures, not completed independent cleaning-pipeline results.
- **Spark failures:** Not assessed; HDFS and Spark were not run in this phase.
- **Model errors:** Not assessed; no models or predictions were created or run.
- **Modifications made:** Updated `data_generator/`, `tests/test_data_generator.py`, `documentation/DATA_GENERATOR_PHASE1.md`, `documentation/DATASET_ARCHITECTURE.md`, `documentation/DATA_DICTIONARY.md`, `documentation/DATA_GENERATION_PLAN.md`, `MASTER_SRS_CHECKLIST.md`, `README.md`, `AI_USAGE.md`, `requirements.txt`, `.gitignore`, and this log. No Hadoop/HDFS, Spark, Java, SSH, system package, Docker, or service configuration was changed.
- **Testing completed:** `python3 -m compileall -q data_generator tests` passed; `python3 -m unittest discover -s tests -v` passed 8/8 tests; final `python3 -m data_generator.validate sample_data/smoke` passed 270/270 checks with 0 failures. Two independent same-seed smoke generations produced identical SHA-256 digests for all 23 raw CSV/JSONL files and the same deterministic manifest-content hash; the final package matched that reference set.
- **Performance improvements:** Fact rows are emitted incrementally and writers rotate at `chunk_rows`. A measured fresh smoke generation used 7.44 seconds wall time and 122,244 KiB maximum resident memory; validation used 12.37 seconds and 118,520 KiB. The local volume is 98 GiB total with 70 GiB free, below the SRS's 500 GB hardware interface item; production memory/disk usage remains unmeasured and requires a separately approved resource check.
- **Git commit/reference:** The current Phase 1 changes remain uncommitted on top of two pre-existing foundation/design commits (`6a98081`, `58247f7`). No production, HDFS, Spark, model, or commit operation was performed.


## Pre-production recovery review — 2026-09-24

- Preserved the uncommitted OpenCode Phase 1 work and original ignored smoke evidence above `6a980815021f0fdcd2d917c14f30af8f52101466`.
- Added production vehicle-duty reservations and metadata-only preflight, source-reference retention per table, streamed unserved requests, release of materialized trip rows, a 55 GiB raw-generation disk floor, and rejection of production by the in-memory smoke validator.
- Tests: inherited 8/8; extended 16/16; final focused allocator/guard tests 8/8. Existing and fresh smoke validation each 270/270. All 23 raw files match inherited hashes; matching-options deterministic manifest hash is `4cf6553b86aeec1d9c9cb589f34a03325a7741146dd135b736fefe2ab7ead96a`. Compilation and whitespace checks passed.
- Preflight: 7 passed, 3 failed (10 total), exit 1. Fleet peak 229 reservations within 320 vehicles; stable allocation digest across runs. 70.78 seconds and 199,372 KiB max RSS. No production facts emitted.
- Remaining failures: 27,720 inactive-calendar departures, 2,865,522 projected movements versus 2,400,000, and 1 projected duplicate ticket copy versus 12,000. Approximate count differences, including delay lower bound 599,853 and 56,136 transfers, are documented for budget reconciliation.
- Resources: estimated approved-target raw 27.30 GiB; raw upper/temporary/margin about 53.68 GiB; all-at-once raw plus two intermediate copies and working allocations about 115.10 GiB. Actual free space about 69.37 GiB. No disk/system configuration changed.
- SRS §1.9.1 lists “500 GB Hard Disk space” without defining mandatory VM runtime/free-space scope; no automatic non-compliance finding. Cleaning, full ML leakage checks, HDFS/Spark and Parquet remain subsequent processing work.
- Evidence and file inventory: `documentation/PREPRODUCTION_REVIEW.md`, `reports/production_preflight.json`. `raw_data/` remains only `.gitkeep`; official PDF hash unchanged during review. No commit/push.
- Result: NOT READY FOR FULL PRODUCTION GENERATION.


## Final cross-check / interrupted production run — 2026-09-25

- **Starting state:** Clean Git status at `69d4847f780fae781173d34bb5421994e0a3e7d5`; `raw_data/` held only `.gitkeep`. Official ignored PDF hash remained `841d9c1969e9849d63d3f2fb52292f2a817d4c8111605ab8781aea68a88fb999`.
- **Cross-check:** Confirmed interconnected trip simulation, deterministic budgets and streamed 25,000-row shards. Initial preflight 11/11; existing suite 27/27. Fixed production passenger-cursor reset before generation and verified deterministic pool coverage/non-overlap.
- **Command:** `/usr/bin/time -v -o reports/production_generation_time.txt python3 -m data_generator.generate --profile production --output raw_data/production-v1 --allow-production --seed 20260924 --timestamp 2026-09-25T00:00:00Z > reports/production_generation.log 2>&1`.
- **Run:** `RUNf8a9da32ff898cda7dc30dbe3d1a2ccc7df94a5d1246dce2e9629382c6c28035`, profile `production`, seed `20260924`. Immediate pre-run free disk 73,881,026,560 bytes; unmodified 55 GiB safety gate passed.
- **Result:** Deliberately interrupted with exit 130 after 2,975 logged departures. The bounded checker uncovered GPS pre-handover observations assigned to the outgoing vehicle. Partial physical output confirms 120 assignment-time violations. This was an integrity stop, not a disk failure or a time-based cancellation.
- **Partial data:** 858,068,095 bytes across 38 files (36 raw CSV shards); 51,392 raw journeys, 63,389 raw tickets, 11,999 measured duplicate ticket copies, 2,979 physical operational identities and 2,921 current non-cancelled trip rows. Service dates span 2025-01-01 through 2025-01-14. Buffered dimension/fact tails were not fully flushed. All 21 actual table counts and per-CSV SHA-256 digests are in `reports/production_interruption.json`. No final manifest or DQ reconciliation was produced.
- **Resource measurement:** Approximately 260.44 seconds through the final progress line; complete duration and maximum RSS unavailable because the interrupted process group left `/usr/bin/time` output empty. Inventory observed 72,729,677,824 bytes free.
- **Correction:** GPS now obtains vehicle attribution from the stop event's arrival/departure phase. Regression first failed for both smoke and production paths, then passed. Source fixes do not modify the preserved partial files.
- **Final tests:** 29 passed, 0 failed in 125.689 seconds. Post-correction production preflight 11 passed, 0 failed. Compilation and whitespace checks passed. Targeted physical partial inspection: 1 passed, 1 failed. Full physical production acceptance validation remains unperformed because generation is incomplete.
- **Status:** No automatic restart, no manual raw repair, no successful full-production claim, and no checklist completion updates. Generator has no resume support; a later run needs a fresh directory or explicit generator overwrite of its marked directory. See `documentation/PRODUCTION_GENERATION_RESULT.md`.
- **Scope:** No HDFS/Spark, Parquet, models, system configuration changes, installations, Docker, sudo, commit or push. Source fixes, tests, bounded validation scripts and reports remain uncommitted; raw output remains ignored.

NOT READY FOR HDFS/SPARK INGESTION
